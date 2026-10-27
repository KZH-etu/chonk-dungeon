"""Étape 15 : l'écran pour la vidéo.

Une fenêtre à côté du jeu qui montre ce que fait le modèle, pour des spectateurs qui ne connaissent
ni le jeu ni le harness : ce qui vient de se passer, ce que pense Mistral, son objectif, son plan
(numéroté, et dessiné sur la capture), ce qu'il a appris (le carnet), la vie, l'étage, le coût.

Elle ne fait que lire les logs du harness (turns.jsonl et les captures) : aucun lien avec
la boucle, aucun appel API. Elle suit une partie en cours ou rejoue une partie finie.

    python -m chonk_dungeon view                                  # suit la partie en cours (dossier le plus récent)
    python -m chonk_dungeon view logs/runs/<date-heure> --replay  # rejoue une partie (2 s par tour)
        Espace : pause / reprise ; flèches gauche et droite : tour précédent / suivant
        F11 : plein écran (Échap pour en sortir) ; Ctrl + / Ctrl - : texte plus grand / plus petit

La fenêtre s'agrandit librement : étroite, la capture est au-dessus du texte ; large (plein écran),
la capture est à gauche et le raisonnement à droite. Le texte affiché est en anglais (public de la vidéo).
En direct, elle se place à côté du jeu s'il y a la place : elle ne doit pas le recouvrir (le harness se
met en pause sinon). Écran trop petit pour les deux côte à côte : --move-game colle le jeu en haut à gauche.
"""

import argparse
import ctypes
import json
import re
import tkinter as tk
import tkinter.font as tkfont
from ctypes import wintypes
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageTk

from .capture import find_window

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)  # pixels physiques, comme capture.py
except (AttributeError, OSError):
    ctypes.windll.user32.SetProcessDPIAware()

RUNS = Path(__file__).resolve().parent.parent / "logs" / "runs"
GAME_TITLE = "Shattered Pixel Dungeon"
WIDTH, HEIGHT = 720, 1120     # taille de départ (fenêtre étroite, à côté du jeu)
PAD = 18
POLL_MS = 300
WIDE_RATIO = 1.15             # largeur / hauteur au-delà de laquelle la capture passe à gauche
NOTE_HEIGHT = 90             # pixels de fenêtre par note du carnet affichée (les dernières)

BG, CARD, TEXT, DIM = "#121218", "#1f1f29", "#f2f2f2", "#9a9aab"
GOLD, GREEN, ORANGE, RED, BLUE = "#ffd36b", "#5cc96b", "#e8913a", "#e5484d", "#5aa9ff"
MODE_COLORS = {"Exploration": "#3f9a4a", "Alert": "#c99a1e", "Combat": "#d06a1c",
               "Danger": "#d33a3a", "Dead": "#6a6a6a", "Menu": "#3d7fc4"}
MODE_TEXT = {"Exploration": "exploring, no enemy around", "Alert": "an enemy is in sight",
             "Combat": "fighting", "Danger": "low health!", "Dead": "the hero died",
             "Menu": "a game window is open"}
MARK_COLORS = {"goto": (90, 220, 120), "examine": (90, 170, 255)}
MARK_DEFAULT = (255, 80, 80)
FONT = "Segoe UI"
FONT_FILE = "C:/Windows/Fonts/segoeuib.ttf"

# Ce que le harness dit au modèle et qui n'intéresse pas les spectateurs (règles des plans, etc.)
HIDDEN = ("next turn", "your move is over", "nothing happened", "only the first", "the actions after",
          "plan:", "attack button disappeared", "pickup button disappeared", "the window closed")


# --------------------------------------------------------------------------- #
# Lecture des logs
# --------------------------------------------------------------------------- #

def latest_run():
    runs = sorted(path.parent for path in RUNS.glob("*/turns.jsonl"))
    return runs[-1] if runs else None


class TurnLog:
    """Lit turns.jsonl au fur et à mesure qu'il grandit (seulement les lignes complètes)."""

    def __init__(self, run_dir):
        self.run_dir = run_dir
        self.offset = 0
        self.turns = []

    def poll(self):
        """Renvoie les nouveaux tours depuis le dernier appel."""
        try:
            with (self.run_dir / "turns.jsonl").open("rb") as log:
                log.seek(self.offset)
                data = log.read()
        except OSError:
            return []
        end = data.rfind(b"\n") + 1  # une ligne en cours d'écriture attend le prochain passage
        self.offset += end
        new = []
        for line in data[:end].splitlines():
            try:
                new.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        self.turns += new
        return new


def run_cost(turns):
    """(appels, coût) cumulés jusqu'à ces tours compris."""
    calls = sum(len(t["call"]["responses"]) for t in turns if t.get("call"))
    cost = sum(t["call"]["cost_usd"] for t in turns if t.get("call"))
    return calls, cost


def hero_stats(turns):
    """(hp, ht, niveau) exacts du dernier tour dont le prompt a l'état du jeu (Chonk build), sinon None."""
    for turn in reversed(turns):
        found = re.search(r"HP (\d+)/(\d+), hero level (\d+)", (turn.get("call") or {}).get("prompt", ""))
        if found:
            return tuple(int(value) for value in found.groups())
    return None


def kills(turns):
    """Ennemis tués jusqu'à ces tours (« You defeated » dans ce qui a changé, une fois par tour)."""
    total = 0
    for turn in turns:
        messages = set()
        for reason in turn.get("reasons", []):
            messages.update(m.strip() for m in re.split(r"\||;|new log messages:", reason) if "defeated" in m)
        total += len(messages)
    return total


# --------------------------------------------------------------------------- #
# Textes pour les spectateurs
# --------------------------------------------------------------------------- #

def target_name(label):
    """(genre, nom) d'une cible du harness : « enemy: marsupial rat (hunting...) » -> ("enemy", "marsupial rat")."""
    label = label or ""
    if label.startswith(("enemy:", "item:")):
        kind, name = label.split(":", 1)
        return kind, name.split(" (")[0].strip()
    if label.startswith("DOWN stairs"):
        return "stairs", "the stairs down"
    if label.startswith("door"):
        return "door", "a door"
    if label.startswith("edge"):
        return "edge", "the unexplored area"
    return "", label.split(" (")[0].strip()


def step_text(action):
    """Une action du plan, en mots simples."""
    if "key" in action and action.get("key"):
        return {"close": "Close the window", "inventory": "Open the bag", "journal": "Open the journal",
                "hero_info": "Look at the hero sheet"}.get(action["key"], "Press " + action["key"].replace("_", " "))
    kind, name = target_name(action.get("label"))
    verb = action.get("action")
    if verb == "goto":
        return {"enemy": f"Go and fight the {name}", "item": f"Go and pick up the {name}",
                "stairs": "Take the stairs down", "door": "Walk to a door",
                "edge": "Explore the unknown area"}.get(kind, f"Go to {name or action.get('target')}")
    if verb == "examine":
        return f"Examine the {name}" if kind in ("enemy", "item") else "Examine the marked tile"
    if verb == "use":
        item, how = action.get("item", ""), str(action.get("verb", "")).lower()
        text = {"drink": f"Drink the {item}", "eat": f"Eat the {item}", "read": f"Read the {item}",
                "throw": f"Throw a {item}", "equip": f"Equip the {item}"}.get(how, f"{how.capitalize()} the {item}")
        return text + (f" at the {name}" if how == "throw" and name else "")
    if verb == "move":
        return "Step " + ", ".join(step.replace("_", "-") for step in action.get("steps") or [])
    if verb:
        return {"loot": "Pick up the item", "attack": "Attack the enemy next to the hero", "map": "Look at the map",
                "rest": "Rest to recover health", "wait": "Wait one turn", "resume": "Resume walking"}.get(verb, verb)
    label = action.get("label")
    return "Click: " + label if label and label != "unknown" else "Click the marked spot"


def plan_of(decision):
    if not decision:
        return []
    return decision["plan"] if decision.get("plan") else [decision]


def events(reasons):
    """Ce qui vient de se passer, pour les spectateurs : [(symbole, texte, couleur)]."""
    found = []
    for reason in reasons:
        low = reason.lower()
        goto = re.search(r"goto T\d+ \((.*?)\): (.*)", reason)
        if low.startswith("start of the session"):
            found.append(("▶", "Mistral takes control of the hero", TEXT))
        elif low.startswith("danger"):
            found.append(("⚠", "Health is low", RED))
        elif re.match(r"hp \d+% -> \d+%", low):
            before, after = (int(v) for v in re.findall(r"\d+", reason)[:2])
            found.append(("♥", f"{'Lost' if after < before else 'Recovered'} health: {before}% → {after}%",
                          RED if after < before else GREEN))
        elif low.startswith("you are now on floor"):
            found.append(("▼", "Arrived on floor " + reason.split()[-1], GREEN))
        elif low.startswith("you died"):
            found.append(("☠", "The hero died", RED))
        elif low.startswith("new log messages:"):
            for message in reason.split(":", 1)[1].split("|"):
                message = message.strip()
                if re.search(r"\?\w|\w\?\w|descend to floor", message):
                    continue  # lettre mal lue par l'OCR (« ?austic oo?e ») ; l'étage est déjà annoncé
                color = RED if "killed you" in message.lower() else GREEN if (
                    "defeated" in message or "Level up" in message) else GOLD
                found.append(("»", f"Game: {message}", color))
        elif low.startswith("attack button appeared"):
            found.append(("⚔", "An enemy is right next to the hero", ORANGE))
        elif low == "enemy in view":
            found.append(("!", "An enemy comes into sight", ORANGE))
        elif low.startswith("no enemy in view"):
            found.append(("✓", "No enemy in sight any more", GREEN))
        elif low.startswith("pickup button appeared"):
            found.append(("✋", "There is an item under the hero", TEXT))
        elif low.startswith("a window opened"):
            found.append(("▢", "A game window opened", DIM))
        elif goto:
            name = target_name(goto.group(1))[1]
            result = goto.group(2)
            if result.startswith("done"):
                found.append(("✓", f"Reached {name}", GREEN))
            elif result.startswith("stopped"):
                found.append(("…", f"Stopped on the way to {name}", ORANGE))
            else:
                found.append(("✗", f"Could not reach {name}", ORANGE))
        elif low.startswith("use:"):
            found.append(("✓", reason[4:].split(".")[0].strip().capitalize(), GREEN))
        elif low.startswith("move:") and "blocked" in low:
            found.append(("✗", "A wall blocked the way", ORANGE))
        elif not low.startswith(HIDDEN) and not low.startswith(("move:", "stuck")):
            found.append(("•", reason if len(reason) < 110 else reason[:107] + "...", DIM))
    unique = []
    for event in found:  # le journal du jeu apparaît parfois deux fois (compte rendu + nouveaux messages)
        if event not in unique:
            unique.append(event)
    return unique


def banner_of(turn):
    """Gros titre sur la capture pour les moments forts : nouvel étage, montée de niveau, mort."""
    reasons = " ".join(turn.get("reasons", []))
    if turn.get("mode") == "Dead":
        killer = re.search(r"([A-Z][\w ]+?) killed you", reasons)
        return ("GAME OVER" + (f" - killed by a {killer.group(1).lower()}" if killer else ""), RED)
    floor = re.search(r"you are now on floor (\d+)", reasons)
    if floor:
        return (f"FLOOR {floor.group(1)}", GREEN)
    if "Level up" in reasons or "evel up" in reasons:
        return ("LEVEL UP!", GOLD)
    return None


# --------------------------------------------------------------------------- #
# Capture annotée
# --------------------------------------------------------------------------- #

def font_of(size):
    try:
        return ImageFont.truetype(FONT_FILE, size)
    except OSError:
        return ImageFont.load_default()


def rgb(color):
    return tuple(int(color[i:i + 2], 16) for i in (1, 3, 5))


def marked_shot(image, decision, size, banner=None):
    """La capture du tour à la taille `size`, avec les points visés par le plan, numérotés comme le plan
    (vert : aller, bleu : examiner, rouge : clic ou lancer), et un bandeau pour les moments forts."""
    shot = image.resize(size, Image.LANCZOS)
    draw = ImageDraw.Draw(shot, "RGBA")
    unit = max(shot.width / 1280, 0.4)
    steps = plan_of(decision)
    for number, action in enumerate(steps, 1):
        point = action.get("point_2d") or action.get("screen_2d")
        if not point:
            continue
        x, y = point[0] / 1000 * (shot.width - 1), point[1] / 1000 * (shot.height - 1)
        color = MARK_COLORS.get(action.get("action"), MARK_DEFAULT)
        radius = 30 * unit
        draw.ellipse([x - radius, y - radius, x + radius, y + radius], outline=color, width=max(2, round(5 * unit)))
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            draw.line([x + 36 * unit * dx, y + 36 * unit * dy, x + 52 * unit * dx, y + 52 * unit * dy],
                      fill=color, width=max(2, round(5 * unit)))
        if len(steps) > 1:  # le numéro de l'étape du plan, dans une pastille
            bx, by, r = x + radius * 0.8, y - radius * 0.8, 17 * unit
            draw.ellipse([bx - r, by - r, bx + r, by + r], fill=color)
            draw.text((bx, by), str(number), fill=(0, 0, 0), font=font_of(round(24 * unit)), anchor="mm")
    if banner:
        text, color = banner
        font = font_of(round(64 * unit))
        top = shot.height * 0.14
        draw.rectangle([0, top, shot.width, top + 110 * unit], fill=(0, 0, 0, 170))
        draw.text((shot.width / 2, top + 55 * unit), text, fill=rgb(color), font=font, anchor="mm")
    return shot


# --------------------------------------------------------------------------- #
# Fenêtre
# --------------------------------------------------------------------------- #

def placement(width, move_game=False):
    """(x, y, avertissement) : à côté du jeu, sans le recouvrir (sinon le harness se met en pause).

    `move_game` : s'il n'y a pas la place, colle d'abord le jeu en haut à gauche de l'écran.
    """
    user32 = ctypes.windll.user32
    try:
        hwnd = find_window(GAME_TITLE)  # titre qui contient GAME_TITLE : le jeu ou son Chonk build
    except SystemExit:
        hwnd = None
    screen_w = user32.GetSystemMetrics(0)
    if not hwnd:
        return screen_w - width, 0, None
    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    if move_game and screen_w - rect.right < width and rect.left < width:
        user32.SetWindowPos(hwnd, None, 0, 0, 0, 0, 0x0001 | 0x0004)  # SWP_NOSIZE | SWP_NOZORDER
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
    if screen_w - rect.right >= width:
        return rect.right, 0, None
    if rect.left >= width:
        return rect.left - width, 0, None
    return screen_w - width, 0, ("Pas assez de place à côté du jeu : déplace le jeu contre le bord gauche de"
                                 " l'écran, ou relance avec --move-game (le harness se met en pause si une"
                                 " fenêtre recouvre le jeu).")


# Tailles de texte (pixels) pour une colonne de texte de 680 px ; elles suivent la taille de la fenêtre
FONT_SIZES = {"title": 17, "mode": 22, "status": 22, "card": 14, "event": 17, "quote": 22, "goal": 18,
              "step": 19, "note": 15, "footer": 15}


class Viewer:
    def __init__(self, root, run_dir=None, replay=False, delay=2.0, start=1, zoom=1.0):
        self.root = root
        self.run_dir = run_dir
        self.replay = replay
        self.delay_ms = round(delay * 1000)
        self.paused = False
        self.index = 0
        self.zoom = zoom
        self.follow_latest = run_dir is None  # direct sans dossier donné : suit la partie la plus récente
        self.log = TurnLog(run_dir) if run_dir else None
        self.photo = None   # garde l'image en vie (sinon tkinter l'efface)
        self.current = None  # (tour, tours jusqu'à lui) affiché, redessiné quand la fenêtre change de taille
        self.image = None    # capture du tour, en pleine taille
        self.layout_job = None
        self.wide = None
        self.fonts = {name: tkfont.Font(family=FONT, size=-size, weight="bold" if name in (
            "title", "mode", "status", "card", "goal", "step") else "normal") for name, size in FONT_SIZES.items()}
        self.fonts["quote"].configure(slant="italic")
        self.build()
        root.bind("<Configure>", self.on_resize)
        root.bind("<F11>", lambda _: root.attributes("-fullscreen", not root.attributes("-fullscreen")))
        root.bind("<Escape>", lambda _: root.attributes("-fullscreen", False))
        for key, step in (("<Control-plus>", 1.1), ("<Control-equal>", 1.1), ("<Control-minus>", 1 / 1.1),
                          ("<Control-KP_Add>", 1.1), ("<Control-KP_Subtract>", 1 / 1.1)):
            root.bind(key, lambda _, step=step: self.set_zoom(step))
        if replay:
            self.log.poll()
            if not self.log.turns:
                raise SystemExit(f"Aucun tour dans {run_dir / 'turns.jsonl'}")
            self.index = next((i for i, t in enumerate(self.log.turns) if t["turn"] >= start), 0)
            for key, step in (("<space>", None), ("<Right>", 1), ("<Left>", -1)):
                root.bind(key, lambda _, step=step: self.on_key(step))
            self.root.after(50, self.show_index)
            self.root.after(self.delay_ms, self.tick)
        else:
            self.wait_text("Waiting for a game to start...")
            self.poll()

    # --- construction -------------------------------------------------------------

    def label(self, parent, font, color=TEXT, wrap=True, **options):
        widget = tk.Label(parent, bg=parent["bg"], fg=color, anchor="w", justify="left", font=self.fonts[font],
                          **options)
        widget.wrap = wrap
        return widget

    def card(self, title, color=DIM):
        frame = tk.Frame(self.panel, bg=CARD, padx=16, pady=12)
        frame.pack(fill="x", pady=(0, 12))
        self.label(frame, "card", color, text=title.upper()).pack(fill="x")
        return frame

    def build(self):
        self.root.configure(bg=BG)
        self.header = tk.Frame(self.root, bg=BG)
        self.title = self.label(self.header, "title", DIM, text="Mistral Large 4 plays Shattered Pixel Dungeon")
        self.title.pack(fill="x")
        row = tk.Frame(self.header, bg=BG)
        row.pack(fill="x", pady=(8, 0))
        self.mode = tk.Label(row, font=self.fonts["mode"], fg="white", bg=BG, padx=12, pady=2)
        self.mode.pack(side="left")
        self.mode_text = tk.Label(row, font=self.fonts["footer"], fg=DIM, bg=BG, padx=10)
        self.mode_text.pack(side="left")
        self.status = tk.Label(row, font=self.fonts["status"], fg=TEXT, bg=BG)
        self.status.pack(side="right")
        self.hp = tk.Canvas(self.header, bg=BG, height=30, highlightthickness=0)
        self.hp.pack(fill="x", pady=(10, 0))
        self.hp_info = None
        self.hp.bind("<Configure>", lambda _: self.paint_hp())

        self.shot = tk.Label(self.root, bg=BG)

        self.panel = tk.Frame(self.root, bg=BG)
        happened = self.card("What just happened")
        self.events = tk.Frame(happened, bg=CARD)
        self.events.pack(fill="x", pady=(4, 0))
        thinking = self.card("Mistral thinks", GOLD)
        self.reasoning = self.label(thinking, "quote", TEXT, pady=4)
        self.reasoning.pack(fill="x")
        self.goal = self.label(thinking, "goal", GOLD)
        self.goal.pack(fill="x", pady=(4, 0))
        plan = self.card("Its plan (mouse and keyboard)")
        self.steps = tk.Frame(plan, bg=CARD)
        self.steps.pack(fill="x", pady=(4, 0))
        notebook = self.card("What it has learned (its notebook)")
        self.notes = self.label(notebook, "note", TEXT)
        self.notes.pack(fill="x", pady=(4, 0))

        self.footer = self.label(self.root, "footer", DIM)
        self.explain = self.label(self.root, "footer", DIM, text=(
            "Each turn, a harness sends Mistral a screenshot of the game and what the player knows (map, bag). "
            "Mistral answers with its thoughts and a plan, played with the mouse and keyboard."))

    def wraps(self, widget):
        """Tous les textes à retour à la ligne sous `widget`."""
        for child in widget.winfo_children():
            if isinstance(child, tk.Label) and getattr(child, "wrap", False):
                yield child
            yield from self.wraps(child)

    # --- mise en page ----------------------------------------------------------------

    def on_resize(self, event):
        if event.widget is self.root:
            if self.layout_job:
                self.root.after_cancel(self.layout_job)
            self.layout_job = self.root.after(80, self.layout)

    def set_zoom(self, step):
        self.zoom = min(max(self.zoom * step, 0.5), 3.0)
        self.layout()

    def layout(self):
        """Place la capture et le texte selon la taille de la fenêtre, et règle les tailles de texte."""
        self.layout_job = None
        width, height = max(self.root.winfo_width(), 200), max(self.root.winfo_height(), 200)
        wide = width > height * WIDE_RATIO
        text_width = (width * 0.36 if wide else width) - 2 * PAD
        scale = min(max(text_width / 680, 0.7), 2.4) * self.zoom
        for name, size in FONT_SIZES.items():
            self.fonts[name].configure(size=-round(size * scale))
        if wide != self.wide:
            self.wide = wide
            for widget in (self.header, self.shot, self.panel, self.footer, self.explain):
                widget.grid_forget()
            self.root.grid_columnconfigure(0, weight=1)
            self.root.grid_columnconfigure(1, weight=0)
            self.root.grid_rowconfigure(4, weight=1 if wide else 0)  # la place en trop va sous la capture
            if wide:
                self.header.grid(row=0, column=0, columnspan=2, sticky="ew", padx=PAD, pady=(PAD, 10))
                self.shot.grid(row=1, column=0, sticky="n", padx=(PAD, 10))
                self.panel.grid(row=1, column=1, rowspan=4, sticky="new", padx=(10, PAD))
                self.footer.grid(row=2, column=0, sticky="ew", padx=PAD, pady=(10, 0))
                self.explain.grid(row=3, column=0, sticky="ew", padx=PAD, pady=(4, PAD))
            else:
                self.header.grid(row=0, column=0, sticky="ew", padx=PAD, pady=(PAD, 10))
                self.shot.grid(row=1, column=0, padx=PAD, pady=(0, 4))
                self.footer.grid(row=2, column=0, sticky="ew", padx=PAD, pady=(0, 10))  # le carnet peut être coupé
                self.panel.grid(row=3, column=0, sticky="ew", padx=PAD)
                self.explain.grid(row=4, column=0, sticky="ew", padx=PAD, pady=(4, PAD))
        self.panel.configure(width=round(text_width))
        self.text_width = text_width
        for widget in self.wraps(self.panel):
            widget.configure(wraplength=round(text_width - 40 * scale))
        shot_width = width * 0.64 - 2 * PAD if wide else width - 2 * PAD
        for widget in (self.footer, self.explain):
            widget.configure(wraplength=round(shot_width if wide else text_width))
        shot_height = height * 0.72 if wide else height * 0.3
        self.shot_size = (max(round(min(shot_width, shot_height * 16 / 9)), 100),
                          max(round(min(shot_width, shot_height * 16 / 9) * 9 / 16), 56))
        if self.current:
            self.show(*self.current)

    # --- affichage ------------------------------------------------------------------

    def wait_text(self, text):
        self.status.config(text=text)
        self.mode.config(text="", bg=BG)

    def draw_hp(self, turn, done):
        """La barre de vie : exacte (prompt du Chonk build) ou lue à l'écran."""
        self.hp_info = (turn, done)
        self.paint_hp()

    def paint_hp(self):
        if not self.hp_info:
            return
        turn, done = self.hp_info
        self.hp.delete("all")
        width = self.hp.winfo_width()
        height = round(30 * self.fonts["status"].cget("size") / -FONT_SIZES["status"])
        if int(self.hp.cget("height")) != height:
            self.hp.configure(height=height)
        stats = hero_stats(done)
        ratio = stats[0] / stats[1] if stats else turn.get("state", {}).get("hp_ratio", 0)
        if turn.get("mode") == "Dead":
            ratio = 0
        color = GREEN if ratio > 0.5 else ORANGE if ratio > 0.25 else RED
        self.hp.create_rectangle(0, 0, width, height, fill="#2b2b36", width=0)
        self.hp.create_rectangle(0, 0, width * ratio, height, fill=color, width=0)
        text = f"HP {stats[0]}/{stats[1]}" if stats and turn.get("mode") != "Dead" else f"HP {ratio:.0%}"
        if stats:
            text += f"   ·   hero level {stats[2]}"
        self.hp.create_text(10, height / 2, anchor="w", text=text, fill="white", font=self.fonts["footer"])

    def rows(self, frame, items, font):
        """Remplace le contenu de `frame` par des lignes (symbole, texte, couleur)."""
        for child in frame.winfo_children():
            child.destroy()
        for symbol, text, color in items:
            row = tk.Frame(frame, bg=CARD)
            row.pack(fill="x", pady=1)
            tk.Label(row, text=symbol, bg=CARD, fg=color, font=self.fonts[font], width=2, anchor="n").pack(
                side="left", anchor="n")
            label = self.label(row, font, color if color != DIM else TEXT, text=text)
            label.configure(wraplength=round(self.text_width - 90 * -self.fonts[font].cget("size") / 20))
            label.pack(side="left", fill="x", anchor="n")

    def show(self, turn, done):
        """Affiche un tour ; `done` = tous les tours jusqu'à lui (coût, vie et tués cumulés)."""
        if not self.current or self.current[0] is not turn:
            try:
                self.image = Image.open(self.run_dir / turn["screenshot"]).convert("RGB")
            except OSError:
                self.image = None
        self.current = (turn, done)
        if not hasattr(self, "shot_size"):
            self.layout()  # première fois : la mise en page appelle show
            return
        mode = turn["mode"]
        self.mode.config(text=mode, bg=MODE_COLORS.get(mode, "#555"))
        self.mode_text.config(text=MODE_TEXT.get(mode, ""))
        deepest = max((t.get("deepest") or t.get("floor") or 0) for t in done)
        self.status.config(text=f"Floor {turn.get('floor', '?')}  ·  Turn {turn['turn']}  ·  {kills(done)} kills")
        self.draw_hp(turn, done)

        decision = turn.get("decision")
        if self.image:
            self.photo = ImageTk.PhotoImage(marked_shot(self.image, decision, self.shot_size, banner_of(turn)))
            self.shot.config(image=self.photo)

        self.rows(self.events, events(turn.get("reasons", [])) or [("•", "Its previous plan is over", DIM)],
                  "event")
        reasoning = decision.get("reasoning") if decision else None
        self.reasoning.config(text=f"“{reasoning}”" if reasoning else "(no answer)")
        self.goal.config(text=f"Goal: {decision['goal']}" if decision and decision.get("goal") else "")
        steps = [(f"{number}.", step_text(action), TEXT) for number, action in enumerate(plan_of(decision), 1)]
        self.rows(self.steps, steps or [("", "(nothing)", DIM)], "step")

        notes = turn.get("notes")
        if notes is None:
            self.notes.config(text="(not logged for this game)", fg=DIM)
        elif notes:
            room = (self.root.winfo_height() - (540 if self.wide else 980)) / NOTE_HEIGHT / self.zoom
            shown = list(enumerate(notes, 1))[-min(max(round(room), 2), 8):]
            self.notes.config(text="\n".join(f"{i}. {note}" for i, note in shown), fg=TEXT)
        else:
            self.notes.config(text="(empty for now)", fg=DIM)

        calls, cost = run_cost(done)
        model = next((t["call"].get("model") for t in reversed(done) if t.get("call") and t["call"].get("model")),
                     None)
        footer = f"Deepest floor {deepest}  ·  {calls} calls to {model or 'the model'}  ·  {cost:.2f} $ so far"
        if self.replay:
            footer += f"  ·  replay {turn['turn']}/{self.log.turns[-1]['turn']}" + ("  (paused)" if self.paused else "")
        self.footer.config(text=footer)

    # --- direct ---------------------------------------------------------------------

    def poll(self):
        """Suit la partie (la plus récente, sauf dossier donné) ; affiche chaque nouveau tour."""
        newest = latest_run() if self.follow_latest else self.run_dir
        if newest and (self.log is None or newest != self.log.run_dir):
            self.run_dir, self.log = newest, TurnLog(newest)
            self.root.title(f"Chonk Dungeon - {newest.name}")
        if self.log and self.log.poll():
            self.show(self.log.turns[-1], self.log.turns)
        self.root.after(POLL_MS, self.poll)

    # --- rejeu ----------------------------------------------------------------------

    def show_index(self):
        self.show(self.log.turns[self.index], self.log.turns[:self.index + 1])

    def tick(self):
        if not self.paused and self.index < len(self.log.turns) - 1:
            self.index += 1
            self.show_index()
        self.root.after(self.delay_ms, self.tick)

    def on_key(self, step):
        if step is None:
            self.paused = not self.paused
        else:
            self.paused = True
            self.index = min(max(self.index + step, 0), len(self.log.turns) - 1)
        self.show_index()


def main():
    parser = argparse.ArgumentParser(description="Écran pour la vidéo (étape 15)")
    parser.add_argument("run", nargs="?", type=Path, help="dossier de partie (par défaut : la plus récente)")
    parser.add_argument("--replay", action="store_true", help="rejoue la partie au lieu de la suivre en direct")
    parser.add_argument("--delay", type=float, default=2.0, help="rejeu : secondes par tour")
    parser.add_argument("--start", type=int, default=1, help="rejeu : premier tour affiché")
    parser.add_argument("--title", help="titre en haut de la fenêtre")
    parser.add_argument("--fullscreen", action="store_true", help="plein écran (F11 pour basculer)")
    parser.add_argument("--zoom", type=float, default=1.0, help="taille du texte (1 = automatique)")
    parser.add_argument("--move-game", action="store_true",
                        help="colle le jeu en haut à gauche de l'écran s'il n'y a pas la place à côté")
    args = parser.parse_args()
    if args.replay and args.run is None:
        args.run = latest_run()
        if args.run is None:
            raise SystemExit(f"Aucune partie avec turns.jsonl dans {RUNS}")

    root = tk.Tk()
    root.title(f"Chonk Dungeon - {args.run.name}" if args.run else "Chonk Dungeon")
    if args.replay:  # rien à ne pas recouvrir : au milieu de l'écran
        x, y, warning = max((root.winfo_screenwidth() - WIDTH) // 2, 0), 0, None
    else:
        x, y, warning = placement(WIDTH, args.move_game)
    height = min(HEIGHT, root.winfo_screenheight() - 80)  # au-dessus de la barre des tâches
    root.geometry(f"{WIDTH}x{height}+{x}+{y}")
    root.minsize(420, 400)
    if warning:
        print(warning, flush=True)
    viewer = Viewer(root, args.run, args.replay, args.delay, args.start, args.zoom)
    if args.title:
        viewer.title.config(text=args.title)
    if args.fullscreen:
        root.attributes("-fullscreen", True)
    root.mainloop()


if __name__ == "__main__":
    main()

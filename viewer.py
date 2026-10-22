"""Étape 15 : l'écran pour la vidéo.

Une fenêtre à côté du jeu qui montre ce que fait le modèle : le mode, le raisonnement,
le carnet de notes et la dernière capture avec le point visé (point_2d) dessiné dessus.

Elle ne fait que lire les logs du harness (turns.jsonl et les captures) : aucun lien avec
la boucle, aucun appel API. Elle suit une partie en cours ou rejoue une partie finie.

    python viewer.py                                  # suit la partie en cours (dossier le plus récent)
    python viewer.py logs/runs/<date-heure> --replay  # rejoue une partie (2 s par tour)
        Espace : pause / reprise ; flèches gauche et droite : tour précédent / suivant

Le texte affiché est en anglais (public de la vidéo). La fenêtre se place à côté du jeu
s'il y a la place : elle ne doit pas le recouvrir (le harness se met en pause sinon).
Écran trop petit pour les deux côte à côte : --move-game colle le jeu en haut à gauche.
"""

import argparse
import ctypes
import json
import tkinter as tk
from ctypes import wintypes
from pathlib import Path

from PIL import Image, ImageDraw, ImageTk

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)  # pixels physiques, comme capture.py
except (AttributeError, OSError):
    ctypes.windll.user32.SetProcessDPIAware()

RUNS = Path(__file__).parent / "logs" / "runs"
GAME_TITLE = "Shattered Pixel Dungeon"
WIDTH, HEIGHT = 720, 1120
PAD = 20
SHOT_WIDTH = WIDTH - 2 * PAD
POLL_MS = 300

BG, CARD, TEXT, DIM = "#16161c", "#23232d", "#f2f2f2", "#9a9aab"
MODE_COLORS = {"Exploration": "#4caf50", "Alert": "#d4a72c", "Combat": "#e07b2a",
               "Danger": "#d93b3b", "Dead": "#7a7a7a", "Menu": "#4a8fd4"}
CLICK_COLOR, EXAMINE_COLOR = (255, 70, 70), (80, 200, 255)
FONT = "Segoe UI"


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


def describe(decision):
    """L'action du tour, en quelques mots pour le public."""
    if not decision:
        return "no action (the brain stopped)"
    if "key" in decision:
        return "key: " + decision["key"].replace("_", " ")
    action = decision.get("action")
    if action == "examine":
        return "examine the marked tile"
    if action:
        return {"loot": "pick up the item", "attack": "attack the nearest enemy", "map": "look at the floor map",
                "rest": "rest", "wait": "wait one turn", "resume": "resume the walk"}.get(action, action)
    label = decision.get("label")
    return "click" + (f": {label}" if label and label != "unknown" else " on the marked tile")


def marked_shot(path, decision):
    """La capture du tour, réduite, avec le point visé (rouge : clic, bleu : examiner)."""
    try:
        image = Image.open(path).convert("RGB")
    except OSError:
        return None
    shot = image.resize((SHOT_WIDTH, round(image.height * SHOT_WIDTH / image.width)), Image.LANCZOS)
    point = (decision or {}).get("point_2d")
    if point:
        x = point[0] / 1000 * (shot.width - 1)
        y = point[1] / 1000 * (shot.height - 1)
        color = EXAMINE_COLOR if decision.get("action") == "examine" else CLICK_COLOR
        draw = ImageDraw.Draw(shot)
        for radius, width in ((17, 4), (5, 0)):
            box = [x - radius, y - radius, x + radius, y + radius]
            draw.ellipse(box, outline=color, width=width) if width else draw.ellipse(box, fill=color)
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            draw.line([x + 22 * dx, y + 22 * dy, x + 32 * dx, y + 32 * dy], fill=color, width=4)
    return shot


# --------------------------------------------------------------------------- #
# Fenêtre
# --------------------------------------------------------------------------- #

def placement(width, move_game=False):
    """(x, y, avertissement) : à côté du jeu, sans le recouvrir (sinon le harness se met en pause).

    `move_game` : s'il n'y a pas la place, colle d'abord le jeu en haut à gauche de l'écran.
    """
    user32 = ctypes.windll.user32
    hwnd = user32.FindWindowW(None, GAME_TITLE)
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


class Viewer:
    def __init__(self, root, run_dir=None, replay=False, delay=2.0, start=1):
        self.root = root
        self.run_dir = run_dir
        self.replay = replay
        self.delay_ms = round(delay * 1000)
        self.paused = False
        self.index = 0
        self.follow_latest = run_dir is None  # direct sans dossier donné : suit la partie la plus récente
        self.log = TurnLog(run_dir) if run_dir else None
        self.photo = None  # garde l'image en vie (sinon tkinter l'efface)
        self.build()
        if replay:
            self.log.poll()
            if not self.log.turns:
                raise SystemExit(f"Aucun tour dans {run_dir / 'turns.jsonl'}")
            self.index = next((i for i, t in enumerate(self.log.turns) if t["turn"] >= start), 0)
            for key, step in (("<space>", None), ("<Right>", 1), ("<Left>", -1)):
                root.bind(key, lambda _, step=step: self.on_key(step))
            self.show_index()
            self.root.after(self.delay_ms, self.tick)
        else:
            self.wait_text("Waiting for a game to start...")
            self.poll()

    # --- construction -------------------------------------------------------------

    def label(self, parent, size, color=TEXT, bold=False, **options):
        widget = tk.Label(parent, bg=parent["bg"], fg=color, anchor="w", justify="left",
                          font=(FONT, -size, "bold" if bold else "normal"), **options)
        widget.pack(fill="x", anchor="w")
        return widget

    def card(self, title):
        frame = tk.Frame(self.root, bg=CARD, padx=14, pady=10)
        frame.pack(fill="x", padx=PAD, pady=(0, 12))
        if title:
            self.label(frame, 14, DIM, bold=True, text=title.upper())
        return frame

    def build(self):
        self.root.configure(bg=BG)
        top = tk.Frame(self.root, bg=BG)
        top.pack(fill="x", padx=PAD, pady=(PAD, 10))
        self.title = self.label(top, 16, DIM, text="Mistral Large 4 plays Shattered Pixel Dungeon")
        row = tk.Frame(top, bg=BG)
        row.pack(fill="x", pady=(6, 0))
        self.mode = tk.Label(row, font=(FONT, -22, "bold"), fg="white", bg=BG, padx=12, pady=2)
        self.mode.pack(side="left")
        self.status = tk.Label(row, font=(FONT, -24, "bold"), fg=TEXT, bg=BG, padx=14)
        self.status.pack(side="left")

        self.shot = tk.Label(self.root, bg=BG)
        self.shot.pack(padx=PAD, pady=(0, 12))

        thinking = self.card("Model")
        self.action = self.label(thinking, 20, "#ffd36b", bold=True, wraplength=SHOT_WIDTH - 28)
        self.reasoning = self.label(thinking, 24, TEXT, wraplength=SHOT_WIDTH - 28, pady=4)
        self.reasons = self.label(thinking, 15, DIM, wraplength=SHOT_WIDTH - 28)

        notebook = self.card("Notebook")
        self.notes = self.label(notebook, 17, TEXT, wraplength=SHOT_WIDTH - 28)

        self.footer = self.label(self.root, 15, DIM, padx=PAD)

    # --- affichage ------------------------------------------------------------------

    def wait_text(self, text):
        self.status.config(text=text)
        self.mode.config(text="", bg=BG)

    def show(self, turn, done):
        """Affiche un tour ; `done` = tous les tours jusqu'à lui (pour le coût cumulé)."""
        state = turn.get("state", {})
        floor = f"Floor {turn.get('floor', '?')}"
        if turn.get("goal_floor"):
            floor += f" / goal {turn['goal_floor']}"
        self.mode.config(text=turn["mode"], bg=MODE_COLORS.get(turn["mode"], "#555"))
        self.status.config(text=f"Turn {turn['turn']}  ·  {floor}  ·  HP {state.get('hp_ratio', 0):.0%}")

        shot = marked_shot(self.run_dir / turn["screenshot"], turn.get("decision"))
        if shot:
            self.photo = ImageTk.PhotoImage(shot)
            self.shot.config(image=self.photo)

        decision = turn.get("decision")
        self.action.config(text="▶ " + describe(decision))
        self.reasoning.config(text=f"“{decision['reasoning']}”" if decision and decision.get("reasoning") else "")
        self.reasons.config(text="Called because: " + "; ".join(turn.get("reasons", [])))

        notes = turn.get("notes")
        if notes is None:
            self.notes.config(text="(not logged for this game)", fg=DIM)
        elif notes:
            self.notes.config(text="\n".join(f"{i}. {note}" for i, note in enumerate(notes, 1)), fg=TEXT)
        else:
            self.notes.config(text="(empty)", fg=DIM)

        calls, cost = run_cost(done)
        model = next((t["call"].get("model") for t in reversed(done) if t.get("call") and t["call"].get("model")),
                     None)
        footer = f"{calls} model calls  ·  {cost:.3f} $ so far"
        if model:
            footer += f"  ·  {model}"
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
    parser.add_argument("--move-game", action="store_true",
                        help="colle le jeu en haut à gauche de l'écran s'il n'y a pas la place à côté")
    args = parser.parse_args()
    if args.replay and args.run is None:
        args.run = latest_run()
        if args.run is None:
            raise SystemExit(f"Aucune partie avec turns.jsonl dans {RUNS}")

    root = tk.Tk()
    root.title(f"Chonk Dungeon - {args.run.name}" if args.run else "Chonk Dungeon")
    x, y, warning = placement(WIDTH, args.move_game)
    height = min(HEIGHT, root.winfo_screenheight() - 80)  # au-dessus de la barre des tâches
    root.geometry(f"{WIDTH}x{height}+{x}+{y}")
    if warning:
        print(warning, flush=True)
    viewer = Viewer(root, args.run, args.replay, args.delay, args.start)
    if args.title:
        viewer.title.config(text=args.title)
    root.mainloop()


if __name__ == "__main__":
    main()

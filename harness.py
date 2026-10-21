"""Étape 10 : la boucle du harness et ses modes.

À chaque tour : lire l'écran (détecteurs), en déduire le mode, appeler le
« cerveau » seulement quand c'est utile, exécuter sa décision, recommencer.

Le cerveau est interchangeable :
- scripted : une liste d'actions fixée à l'avance (tests sans API)
- manual   : on tape l'action dans la console (tests sans API)
- mistral  : le modèle (étape 11)

Format d'une décision (celui de l'étape 11) :
    {"key": "up"}                                   touche (nom de actions.KEYS)
    {"point_2d": [x, y], "label": "..."}            clic (0-1000) : se déplacer, interagir
    {"action": "attack"} / {"action": "loot"}       boutons rouge / bleu
    {"action": "examine", "point_2d": [x, y]}       examiner une case
    {"action": "resume"} / {"action": "wait"} / {"action": "rest"}
avec un champ "reasoning" facultatif.

Exemples (sans API) :
    python harness.py --brain scripted --script "right; left; examine 537 500; wait"
    python harness.py --brain manual
"""

import argparse
import shlex
from collections import deque
from datetime import datetime

from actions import Actions
from capture import LOGS_DIR, GameWindow, changed_fraction, resize
from detectors import changes, read_screen, window_rect
from ocr import Reader

# Modes, du plus prioritaire au moins prioritaire
DEAD, MENU, DANGER, COMBAT, ALERT, EXPLORE = "Mort", "Menu", "Danger", "Combat", "Alerte", "Exploration"

DANGER_HP = 0.35          # vie à 35 % ou moins
DANGER_DROP = 0.30        # ou 30 % de vie perdus depuis le dernier tour
AUTO_PICKUP = True        # bouton bleu sans ennemi en vue : on ramasse sans appeler le modèle
WALK_TIMEOUT = 20.0       # un long déplacement peut durer plusieurs secondes


# --------------------------------------------------------------------------- #
# Modes
# --------------------------------------------------------------------------- #

def mode_for(state, previous):
    """Mode du tour, d'après l'état lu à l'écran et celui du tour précédent."""
    if state["dead"]:
        return DEAD
    if state["window_open"]:
        return MENU
    drop = previous["hp_ratio"] - state["hp_ratio"] if previous else 0
    if state["hp_ratio"] <= DANGER_HP or drop >= DANGER_DROP:
        return DANGER
    if state["attack_button"] or state["enemies_visible"]:
        return COMBAT
    if previous and changes(previous, state):
        return ALERT
    return EXPLORE


MOVE_KEYS = {"up", "down", "left", "right", "up_left", "up_right", "down_left", "down_right"}
MOVED = 0.01  # au-delà de 1 % de pixels changés, l'écran a bougé (la caméra suit le héros)


def describe_reasons(mode, state, previous, last_action, screen_changed=True):
    """Pourquoi on appelle le cerveau, en phrases courtes en anglais (le modèle doit savoir ce qui a changé)."""
    reasons = changes(previous, state) if previous else ["start of the session"]
    if mode == DANGER:
        reasons.insert(0, f"DANGER: HP at {state['hp_ratio']:.0%}")
    is_move = last_action and (("point_2d" in last_action and "action" not in last_action)
                               or last_action.get("key") in MOVE_KEYS)
    if is_move and not screen_changed:
        reasons.append("nothing happened: the hero did not move (that tile may be his own tile, a wall,"
                       " or out of reach)")
    elif is_move and mode == EXPLORE:
        reasons.append("your move is over")
    return reasons or ["next turn"]


# --------------------------------------------------------------------------- #
# Cerveaux de test (sans API)
# --------------------------------------------------------------------------- #

def parse_command(text):
    """« right », « click 520 480 », « examine 537 500 », « attack »... -> décision."""
    words = shlex.split(text)
    if not words:
        return {"action": "wait"}
    verb, numbers = words[0].lower(), [int(w) for w in words[1:] if w.lstrip("-").isdigit()]
    if verb in ("click", "go") and len(numbers) == 2:
        return {"point_2d": numbers, "label": " ".join(words[3:]) or "clic"}
    if verb == "examine" and len(numbers) == 2:
        return {"action": "examine", "point_2d": numbers}
    if verb in ("attack", "loot", "resume", "wait", "rest"):
        return {"action": verb}
    return {"key": verb}


class ScriptedBrain:
    """Joue une liste d'actions fixée à l'avance, puis s'arrête."""

    def __init__(self, script):
        self.queue = deque(c.strip() for c in script.split(";") if c.strip())

    def decide(self, situation):
        if situation.get("examined"):
            examined = situation["examined"]
            print(f"   examiné : {examined if isinstance(examined, str) else 'image de la fenêtre (texte illisible)'}")
        if not self.queue:
            return None
        return {**parse_command(self.queue.popleft()), "reasoning": "script de test"}


class ManualBrain:
    """Affiche la situation et demande l'action au clavier (q pour quitter)."""

    def decide(self, situation):
        print(format_situation(situation))
        text = input("   action (right, click x y, examine x y, attack, loot, wait, q) > ").strip()
        if text.lower() in ("q", "quit"):
            return None
        return {**parse_command(text), "reasoning": "manuel"}


def format_situation(situation):
    lines = [f"   [{situation['mode']}] " + " ; ".join(situation["reasons"])]
    state = situation["state"]
    lines.append(f"   vie {state['hp_ratio']:.0%} | attaque {'oui' if state['attack_button'] else 'non'}"
                 f" | ramassage {'oui' if state['pickup_button'] else 'non'}"
                 f" | ennemis en vue {'oui' if state['enemies_visible'] else 'non'}"
                 f" | image {'oui' if situation['image'] is not None else 'non'}")
    for line in state["messages"][-3:]:
        lines.append(f"   journal [{line['color']}] {line['text']}")
    if situation.get("examined"):
        lines.append(f"   examiné : {situation['examined']}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# La boucle
# --------------------------------------------------------------------------- #

class Harness:
    def __init__(self, brain, max_turns=200, run_dir=None):
        self.window = GameWindow()
        self.actions = Actions(self.window)
        self.reader = Reader()
        self.brain = brain
        self.max_turns = max_turns
        self.run_dir = run_dir or LOGS_DIR.parent / "runs" / datetime.now().strftime("%Y%m%d-%H%M%S")
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.examined = None  # résultat du dernier « examiner », joint à l'appel suivant
        self.state = None     # dernier état lu à l'écran

    def look(self):
        self.window.focus()
        image, _, _ = self.window.wait_stable()
        self.state = read_screen(image, self.reader)
        return image, self.state

    def run(self):
        image, state = self.look()
        previous, last_action, previous_image = None, None, None
        for turn in range(1, self.max_turns + 1):
            mode = mode_for(state, previous)
            screen_changed = previous_image is None or changed_fraction(previous_image, image) > MOVED
            reasons = describe_reasons(mode, state, previous, last_action, screen_changed)
            print(f"\nTour {turn} [{mode}] " + " ; ".join(reasons))
            image.save(self.run_dir / f"{turn:04}.png")

            if mode == DEAD:
                print("Mort : fin de la partie.")
                return
            if AUTO_PICKUP and state["pickup_button"] and not state["enemies_visible"] and mode != MENU:
                decision = {"action": "loot", "reasoning": "ramassage automatique (aucun ennemi en vue)"}
            else:
                situation = {
                    "turn": turn, "mode": mode, "reasons": reasons, "state": state,
                    # Image seulement si l'écran n'est pas que du texte déjà lu
                    "image": None if self.examined and isinstance(self.examined, str) else resize(image),
                    "examined": self.examined,
                    "last_action": last_action,
                }
                self.examined = None
                decision = self.brain.decide(situation)
                if decision is None:
                    print("Le cerveau s'arrête.")
                    return
            print(f"   -> {decision}")
            if decision.get("reasoning"):
                with (self.run_dir / "reasoning.txt").open("a", encoding="utf-8") as notes:
                    notes.write(f"{turn}\t{mode}\t{decision['reasoning']}\n")
            self.execute(decision)
            last_action = decision
            previous, previous_image = state, image
            image, state = self.look()
        print(f"Limite de {self.max_turns} tours atteinte.")

    def execute(self, decision):
        action = decision.get("action")
        if action == "examine":
            self.examine(decision["point_2d"])
        elif action in ("attack", "loot", "resume", "wait", "rest"):
            names = {"loot": "pickup"}
            self.actions.press(names.get(action, action), wait=False)
            self.window.wait_stable(timeout=WALK_TIMEOUT if action in ("resume", "rest") else 3.0)
        elif "point_2d" in decision:
            self.actions.click(decision["point_2d"], wait=False)
            self.window.wait_stable(timeout=WALK_TIMEOUT)  # le héros marche seul jusqu'à la case
        elif "key" in decision:
            self.actions.press(decision["key"])
        else:
            print(f"   décision inconnue, on attend : {decision}")
            self.actions.press("wait")

    def examine(self, point_2d):
        """E (comme la loupe), clic sur la case, lecture de la fenêtre de description, puis fermeture."""
        self.actions.press("examine")
        image, _, _ = self.actions.click(point_2d)
        rect = window_rect(image)
        if rect is None:
            self.examined = "nothing to examine on that tile (no description window opened)"
            self.actions.press("close")  # sort du mode « examiner » s'il est resté actif
            return
        crop = image.crop(rect)
        crop.save(self.run_dir / f"examine-{datetime.now():%H%M%S}.png")
        text = self.reader.read_window(crop)
        # Texte lisible -> appel sans image ; sinon on garde l'image de la fenêtre
        self.examined = text if text and "?" not in text else crop
        self.actions.press("close")


def main():
    parser = argparse.ArgumentParser(description="Boucle du harness (étapes 10 et 11)")
    parser.add_argument("--brain", choices=["scripted", "manual", "mistral"], default="manual")
    parser.add_argument("--script", default="", help="actions séparées par « ; » (cerveau scripted)")
    parser.add_argument("--max-turns", type=int, default=200)
    parser.add_argument("--model", help="cerveau mistral (par défaut : CHONK_MODEL)")
    parser.add_argument("--reasoning", default="none", help="reasoning_effort du modèle (none = pas de réflexion)")
    parser.add_argument("--budget", type=float, default=0.5, help="coût maximal de la partie, en $")
    parser.add_argument("--dry-run", action="store_true", help="cerveau mistral : affiche le prompt sans appeler l'API")
    args = parser.parse_args()
    if args.brain == "mistral":
        from brain import MistralBrain
        brain = MistralBrain(args.model, args.reasoning, args.budget, args.dry_run)
    elif args.brain == "scripted":
        brain = ScriptedBrain(args.script)
    else:
        brain = ManualBrain()
    harness = Harness(brain, max_turns=args.max_turns)
    print(f"Captures de chaque tour : {harness.run_dir}")
    try:
        harness.run()
    finally:
        if hasattr(brain, "summary"):
            print(f"\nBilan : {brain.summary()}")


if __name__ == "__main__":
    main()

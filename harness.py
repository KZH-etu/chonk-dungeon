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
import json
import shlex
import time
import traceback
from collections import deque
from datetime import datetime

from actions import Actions
from budget import spent
from capture import LOGS_DIR, GameWindow, changed_fraction, resize
from detectors import changes, new_messages, read_screen, window_rect
from llm import BUDGET_USD
from memory import Memory, describe_action
from ocr import Reader

# Modes, du plus prioritaire au moins prioritaire
DEAD, MENU, DANGER, COMBAT, ALERT, EXPLORE = "Dead", "Menu", "Danger", "Combat", "Alert", "Exploration"

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
STUCK_REPEATS = 3
NOT_STUCK_ACTIONS = {"wait", "rest", "examine"}  # ne changent pas l'écran, et c'est voulu
NEW_GAME_BUTTON = [500, 532]  # bouton « New Game » de l'écran de mort (640, 383 sur 1280x720)
COVERED_CHECKS, COVERED_PAUSE = 20, 3.0  # jeu couvert par une autre fenêtre : on attend jusqu'à 1 min


class WindowCovered(RuntimeError):
    """Une autre fenêtre reste devant le jeu : on ne peut ni voir ni jouer."""


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
    def __init__(self, brain, max_turns=200, goal_floor=None, run_dir=None):
        self.window = GameWindow()
        self.actions = Actions(self.window)
        self.reader = Reader()
        self.brain = brain
        self.max_turns = max_turns
        self.goal_floor = goal_floor
        self.memory = Memory()
        self.run_dir = run_dir or LOGS_DIR.parent / "runs" / datetime.now().strftime("%Y%m%d-%H%M%S")
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.examined = None  # résultat du dernier « examiner », joint à l'appel suivant
        self.state = None     # dernier état lu à l'écran
        self.turns = 0
        self.death_cause = None
        self.kills = self.items = 0
        self.started = datetime.now()
        self.recent = deque(maxlen=STUCK_REPEATS)  # (action, l'écran a-t-il changé ?)

    def wait_uncovered(self):
        """Met le jeu devant ; si une autre fenêtre le couvre (alerte Windows...), attend qu'elle parte."""
        for attempt in range(COVERED_CHECKS):
            self.window.focus()
            if not self.window.covered():
                return
            if attempt == 0:
                print("   Une autre fenêtre couvre le jeu : pause en attendant qu'elle se ferme.")
            time.sleep(COVERED_PAUSE)
        raise WindowCovered(f"le jeu est resté couvert par une autre fenêtre pendant"
                            f" {COVERED_CHECKS * COVERED_PAUSE:.0f} s")

    def check_stuck(self, last_action, screen_changed):
        """Même action STUCK_REPEATS fois de suite sans rien changer à l'écran -> message pour le modèle."""
        if not last_action or last_action.get("action") in NOT_STUCK_ACTIONS:
            self.recent.clear()
            return None
        self.recent.append((describe_action(last_action), screen_changed))
        if (len(self.recent) == STUCK_REPEATS and len({a for a, _ in self.recent}) == 1
                and not any(changed for _, changed in self.recent)):
            self.recent.clear()
            print("   Bloqué : même action sans effet, on prévient le modèle.")
            return (f"STUCK: you did '{describe_action(last_action)}' {STUCK_REPEATS} times in a row and"
                    " nothing changed. Do something different.")
        return None

    def log_turn(self, turn, mode, reasons, state, decision, call):
        """Une ligne par tour dans turns.jsonl : ce qui a été vu, envoyé, répondu et joué."""
        seen = {key: state[key] for key in ("hp_ratio", "attack_button", "pickup_button", "enemies_visible",
                                            "window_open", "inventory_open", "dead")}
        seen["messages"] = [f"[{m['color']}] {m['text']}" for m in state["messages"]]
        record = {"turn": turn, "time": datetime.now().isoformat(timespec="seconds"), "mode": mode,
                  "floor": self.memory.floor, "deepest": self.memory.deepest, "goal_floor": self.goal_floor,
                  "reasons": reasons, "state": seen, "screenshot": f"{turn:04}.png",
                  "decision": decision, "call": call, "notes": list(self.memory.notes)}
        with (self.run_dir / "turns.jsonl").open("a", encoding="utf-8") as log:
            log.write(json.dumps(record, ensure_ascii=False) + "\n")

    def summary(self, end):
        """Bilan de la partie (complété par main avec les appels et le coût)."""
        return {"end": end, "floor": self.memory.floor, "deepest_floor": self.memory.deepest,
                "goal_floor": self.goal_floor, "turns": self.turns, "death_cause": self.death_cause,
                "enemies_killed": self.kills, "items_picked": self.items,
                "started": self.started.isoformat(timespec="seconds"),
                "ended": datetime.now().isoformat(timespec="seconds"),
                "minutes": round((datetime.now() - self.started).total_seconds() / 60, 1)}

    def start_new_game(self):
        """Écran de mort : bouton « New Game ». Renvoie True si une nouvelle partie a bien commencé."""
        self.window.focus()
        self.actions.click(NEW_GAME_BUTTON, wait=False)
        self.window.wait_stable(timeout=10.0)
        _, state = self.look()
        started = not state["dead"] and state["hp_ratio"] > 0 and not state["window_open"]
        print("Nouvelle partie lancée." if started else
              "Après « New Game », l'écran n'est pas une partie en cours : arrêt (à vérifier à la main).")
        return started

    def look(self):
        self.wait_uncovered()
        image, _, _ = self.window.wait_stable()
        self.state = read_screen(image, self.reader)
        return image, self.state

    def run(self):
        """Joue jusqu'à la mort, l'étage visé, l'arrêt du cerveau ou la limite de tours. Renvoie la raison de l'arrêt."""
        image, state = self.look()
        self.memory.observe_messages([line["text"] for line in state["messages"]])
        previous, last_action, previous_image = None, None, None
        for turn in range(1, self.max_turns + 1):
            self.turns = turn
            screen_changed = previous_image is None or changed_fraction(previous_image, image) > MOVED
            stuck = self.check_stuck(last_action, screen_changed)
            if stuck and state["window_open"]:
                self.actions.press("close")  # sans fenêtre, Échap ouvrirait le menu du jeu
                image, state = self.look()
            mode = mode_for(state, previous)
            reasons = describe_reasons(mode, state, previous, last_action, screen_changed)
            if stuck:
                reasons.insert(0, stuck)
            added = new_messages([m["text"] for m in previous["messages"]],
                                 [m["text"] for m in state["messages"]]) if previous else []
            if self.memory.observe_messages(added):
                reasons.insert(0, f"you are now on floor {self.memory.floor}")
            self.kills += sum(1 for text in added if text.startswith("You defeated"))
            self.items += sum(1 for text in added if text.startswith("You picked up"))
            self.memory.record_outcome(reasons)
            self.memory.save(self.run_dir / "memory.json")
            print(f"\nTour {turn} [{mode}] étage {self.memory.floor} : " + " ; ".join(reasons))
            image.save(self.run_dir / f"{turn:04}.png")

            if mode == DEAD:
                red = [line["text"] for line in state["messages"] if line["color"] == "red"]
                self.death_cause = red[-1] if red else None
                print(f"Mort : fin de la partie ({self.death_cause or 'cause non lue'}).")
                return "dead"
            if self.goal_floor and self.memory.deepest >= self.goal_floor:
                print(f"Objectif atteint : étage {self.memory.deepest}.")
                return "goal"
            call = None  # appel au modèle de ce tour (aucun pour le ramassage automatique)
            if AUTO_PICKUP and state["pickup_button"] and not state["enemies_visible"] and mode != MENU:
                decision = {"action": "loot", "reasoning": "automatic pickup (no enemy in view)"}
            else:
                situation = {
                    "turn": turn, "mode": mode, "reasons": reasons, "state": state,
                    # Image seulement si l'écran n'est pas que du texte déjà lu
                    "image": None if self.examined and isinstance(self.examined, str) else resize(image),
                    "examined": self.examined,
                    "memory": self.memory.to_text(),
                }
                self.examined = None
                self.brain.last_call = None
                decision = self.brain.decide(situation)
                call = self.brain.last_call
                if decision is None:
                    self.log_turn(turn, mode, reasons, state, None, call)
                    print("Le cerveau s'arrête.")
                    return "stopped"
            print(f"   -> {decision}")
            self.memory.apply(decision)  # avant le log : le carnet du tour inclut la note qui vient d'être écrite
            self.log_turn(turn, mode, reasons, state, decision, call)
            if decision.get("reasoning"):
                with (self.run_dir / "reasoning.txt").open("a", encoding="utf-8") as notes:
                    notes.write(f"{turn}\t{mode}\t{decision['reasoning']}\n")
            self.memory.record_action(turn, mode, decision)
            self.execute(decision)
            last_action = decision
            previous, previous_image = state, image
            image, state = self.look()
        print(f"Limite de {self.max_turns} tours atteinte.")
        return "max_turns"

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
        if isinstance(self.examined, str):
            self.memory.note_examined(text)
        self.actions.press("close")


def main():
    parser = argparse.ArgumentParser(description="Boucle du harness (étapes 10 à 13)")
    parser.add_argument("--brain", choices=["scripted", "manual", "mistral"], default="manual")
    parser.add_argument("--script", default="", help="actions séparées par « ; » (cerveau scripted)")
    parser.add_argument("--max-turns", type=int, default=200)
    parser.add_argument("--goal-floor", type=int, default=3, help="arrête la partie en succès à cet étage (0 = jamais)")
    parser.add_argument("--model", help="cerveau mistral (par défaut : CHONK_MODEL)")
    parser.add_argument("--reasoning", default="none", help="reasoning_effort du modèle (none = pas de réflexion)")
    parser.add_argument("--budget", type=float, default=0.5, help="coût maximal de la session (toutes les parties), en $")
    parser.add_argument("--games", type=int, default=1, help="nombre de parties : après une mort, « New Game » et on continue")
    parser.add_argument("--dry-run", action="store_true", help="cerveau mistral : affiche le prompt sans appeler l'API")
    args = parser.parse_args()
    if args.brain == "mistral":
        from brain import MistralBrain
        brain = MistralBrain(args.model, args.reasoning, args.budget, args.dry_run)
    elif args.brain == "scripted":
        brain = ScriptedBrain(args.script)
    else:
        brain = ManualBrain()
    for game in range(1, args.games + 1):
        harness = Harness(brain, max_turns=args.max_turns, goal_floor=args.goal_floor or None)
        print(f"\n=== Partie {game}/{args.games} : captures dans {harness.run_dir}")
        calls_before, cost_before = getattr(brain, "calls", 0), getattr(brain, "cost", 0.0)
        tokens_before = getattr(brain, "tokens_in", 0), getattr(brain, "tokens_out", 0)
        end = "error"
        try:
            end = harness.run()
        except KeyboardInterrupt:
            end = "interrupted"
            print("\nArrêt demandé (Ctrl+C).")
        except WindowCovered as error:
            end = "window_covered"
            print(f"\nArrêt : {error}.")
        except Exception:  # une erreur imprévue ne doit pas faire perdre le bilan
            traceback.print_exc()
        finally:
            summary = harness.summary(end)
            summary["calls"] = getattr(brain, "calls", 0) - calls_before
            summary["tokens_in"] = getattr(brain, "tokens_in", 0) - tokens_before[0]
            summary["tokens_out"] = getattr(brain, "tokens_out", 0) - tokens_before[1]
            summary["cost_usd"] = round(getattr(brain, "cost", 0.0) - cost_before, 4)
            summary["model"] = getattr(brain, "model", args.brain)
            harness.memory.save(harness.run_dir / "memory.json")
            (harness.run_dir / "run_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False),
                                                              encoding="utf-8")
            print(f"Bilan de la partie : {summary}")
        if end != "dead" or game == args.games or not harness.start_new_game():
            break
    if hasattr(brain, "summary"):
        print(f"\nBilan de la session : {brain.summary()}")
        print(f"Budget : {spent():.3f} $ dépensés en tout (selon les logs), reste {BUDGET_USD - spent():.2f} $"
              " (détail : python budget.py)")


if __name__ == "__main__":
    main()

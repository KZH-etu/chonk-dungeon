"""Étape 10 : la boucle du harness et ses modes.

À chaque tour : lire l'écran (détecteurs), en déduire le mode, appeler le
« cerveau » seulement quand c'est utile, exécuter sa décision, recommencer.

Le cerveau est interchangeable :
- scripted : une liste d'actions fixée à l'avance (tests sans API)
- manual   : on tape l'action dans la console (tests sans API)
- mistral  : le modèle (étape 11)

Format d'une décision (celui de l'étape 11) :
    {"key": "up"}                                   touche (nom de actions.KEYS)
    {"action": "move", "steps": ["right", "down"]}  pas au clavier, un par un (s'arrête si bloqué)
    {"point_2d": [x, y], "label": "..."}            clic (0-1000) : se déplacer, interagir
    {"action": "attack"} / {"action": "loot"}       boutons rouge / bleu
    {"action": "examine", "point_2d": [x, y]}       examiner une case
    {"action": "resume"} / {"action": "wait"} / {"action": "rest"}
    {"action": "map"}                               carte de l'étage (vue dézoomée, overview.py)
    {"action": "goto", "target": "T1"}              clic sur une cible : le jeu fait marcher le héros (Chonk build)
    {"action": "examine", "target": "T2"}           examiner une cible (Chonk build)
    {"action": "use", "item": "...", "verb": "DRINK"}  utiliser un objet du sac (Chonk build)
    {"plan": [décision, décision, ...]}             plusieurs actions jouées dans l'ordre (au plus MAX_PLAN) ;
                                                    le plan s'arrête dès que la situation change
avec des champs "reasoning", "goal", "note_add", "note_remove" facultatifs.

Exemples (sans API ; « ; » sépare les tours, « + » les actions d'un même plan) :
    python harness.py --brain scripted --script "right; left; examine 537 500; wait"
    python harness.py --brain scripted --script "move right right + wait + move left left"
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
from brain import MAX_PLAN
from budget import spent
from capture import LOGS_DIR, GameWindow, changed_fraction, resize
from detectors import changes, new_messages, read_screen, window_rect
import game_state
from llm import BUDGET_USD
from memory import Memory, describe_action
from ocr import Reader
from overview import ZOOM_IN_KEY, ZOOM_OUT_KEY, ZOOM_STEPS, build_map, map_to_screen

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
NO_MOVE_LIMIT = 3  # déplacements sans effet de suite (même si les cibles diffèrent) -> message STUCK
MAX_STEPS = 10     # pas au plus par action « move »
MAX_GOTO = 40      # pas au plus par action « goto » au clavier (si aucun point du chemin n'est cliquable)
GOTO_CLICKS = 4    # clics au plus par « goto » (une cible hors de l'écran demande plusieurs clics)
WALK_ACTIONS = {"move", "goto"}
NOT_STUCK_ACTIONS = {"wait", "rest", "examine", "map", "attack"}  # « attack » : en combat l'écran bouge à peine  # ne changent pas l'écran, et c'est voulu
NEW_GAME_BUTTON = [500, 532]  # bouton « New Game » de l'écran de mort (640, 383 sur 1280x720)
COVERED_CHECKS, COVERED_PAUSE = 20, 3.0  # jeu couvert par une autre fenêtre : on attend jusqu'à 1 min


class WindowCovered(RuntimeError):
    """Une autre fenêtre reste devant le jeu : on ne peut ni voir ni jouer."""


def is_move(action):
    """Clic de déplacement, pas d'une case ou suite de pas."""
    return bool(action) and (("point_2d" in action and "action" not in action) or action.get("key") in MOVE_KEYS
                             or action.get("action") in WALK_ACTIONS)


def describe_reasons(mode, state, previous, last_action, screen_changed=True):
    """Pourquoi on appelle le cerveau, en phrases courtes en anglais (le modèle doit savoir ce qui a changé)."""
    reasons = changes(previous, state) if previous else ["start of the session"]
    if mode == DANGER:
        reasons.insert(0, f"DANGER: HP at {state['hp_ratio']:.0%}")
    if is_move(last_action) and not screen_changed:
        reasons.append("nothing happened: the hero did not move (that tile may be his own tile, a wall,"
                       " or out of reach)")
    elif is_move(last_action) and mode == EXPLORE:
        reasons.append("your move is over")
    return reasons or ["next turn"]


CHATTY_ACTIONS = {"attack", "wait", "loot"}  # leurs messages blancs (« You hit the rat ») sont attendus


def plan_interrupted(action, start, before, after):
    """Pourquoi arrêter un plan après `action` : ce qui a changé et que le plan ne pouvait pas prévoir
    (`start` : état au début du plan ; `before`, `after` : avant et après l'action). Liste vide : on continue."""
    why = []
    if after["dead"]:
        why.append("you died")
    if before["window_open"] != after["window_open"]:
        why.append("a window opened" if after["window_open"] else "the window closed")
    fighting = action.get("action") in ("attack", "wait")
    if before["hp_ratio"] - after["hp_ratio"] >= 0.01 and (
            not fighting or after["hp_ratio"] <= DANGER_HP or start["hp_ratio"] - after["hp_ratio"] >= DANGER_DROP):
        why.append(f"HP {before['hp_ratio']:.0%} -> {after['hp_ratio']:.0%}")
    if bool(before["attack_button"]) != bool(after["attack_button"]):
        why.append("attack button " + ("appeared: an enemy is next to you" if after["attack_button"] else
                                       "disappeared: no enemy next to you any more"))
    if after["pickup_button"] and not before["pickup_button"]:
        why.append("pickup button appeared: an item is under you")
    if before["enemies_visible"] != after["enemies_visible"]:
        why.append("enemy in view" if after["enemies_visible"] else "no enemy in view any more")
    added = new_messages([m["text"] for m in before["messages"]], [m["text"] for m in after["messages"]])
    lines = after["messages"][len(after["messages"]) - len(added):] if added else []
    if action.get("action") in CHATTY_ACTIONS:
        lines = [line for line in lines if line["color"] != "white"]
    if lines:
        why.append("new log messages: " + " | ".join(line["text"] for line in lines))
    return why


# --------------------------------------------------------------------------- #
# Cerveaux de test (sans API)
# --------------------------------------------------------------------------- #

def parse_command(text):
    """« right », « click 520 480 », « examine 537 500 », « attack »... -> décision ; « a + b » -> plan."""
    if "+" in text:
        return {"plan": [parse_command(part) for part in text.split("+") if part.strip()]}
    words = shlex.split(text)
    if not words:
        return {"action": "wait"}
    verb, numbers = words[0].lower(), [int(w) for w in words[1:] if w.lstrip("-").isdigit()]
    if verb in ("click", "go") and len(numbers) == 2:
        return {"point_2d": numbers, "label": " ".join(words[3:]) or "clic"}
    if verb == "examine" and len(numbers) == 2:
        return {"action": "examine", "point_2d": numbers}
    if verb == "goto" and len(words) > 1:
        return {"action": "goto", "target": words[1].upper()}
    if verb == "examine" and len(words) == 2:
        return {"action": "examine", "target": words[1].upper()}
    if verb == "use" and len(words) >= 3:  # use "turquoise potion" drink
        return {"action": "use", "item": words[1], "verb": words[2].upper()}
    if verb == "move":
        return {"action": "move", "steps": words[1:]}
    if verb in ("attack", "loot", "resume", "wait", "rest", "map"):
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
        self.no_moves = 0     # déplacements sans effet de suite
        self.feedback = None  # action refusée par le harness : expliquée au modèle au tour suivant
        self.map_view = None  # carte affichée (vue dézoomée) : {"picture", "box", "scale"}
        self.walked = 0       # pas réellement faits par le dernier « move »
        self.walk_complete = True  # le dernier « move » a-t-il fait tous ses pas ?
        self.targets = {}     # cibles proposées au modèle ce tour (Chonk build) : {"T1": {"cell", "label", "steps"}}

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
        """Message STUCK pour le modèle : même action STUCK_REPEATS fois de suite sans rien changer à l'écran,
        ou NO_MOVE_LIMIT déplacements de suite sans effet (le modèle varie un peu ses clics sans s'en sortir)."""
        self.no_moves = self.no_moves + 1 if is_move(last_action) and not screen_changed else 0
        if self.no_moves >= NO_MOVE_LIMIT:
            moves, self.no_moves = self.no_moves, 0
            self.recent.clear()
            print("   Bloqué : déplacements sans effet, on prévient le modèle.")
            return (f"STUCK: your last {moves} moves did nothing. You can only walk to floor tiles you can see:"
                    " black areas are unexplored, walls cannot be walked on. Move toward a lit floor tile, an open"
                    " door or the far end of a visible corridor (or click one on the map), or go back through the"
                    " door you came in and explore another part of the floor.")
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

    def log_turn(self, turn, mode, reasons, state, decision, call, screenshot=None):
        """Une ligne par tour dans turns.jsonl : ce qui a été vu, envoyé, répondu et joué."""
        seen = {key: state[key] for key in ("hp_ratio", "attack_button", "pickup_button", "enemies_visible",
                                            "window_open", "inventory_open", "dead")}
        seen["messages"] = [f"[{m['color']}] {m['text']}" for m in state["messages"]]
        record = {"turn": turn, "time": datetime.now().isoformat(timespec="seconds"), "mode": mode,
                  "floor": self.memory.floor, "deepest": self.memory.deepest, "goal_floor": self.goal_floor,
                  "reasons": reasons, "state": seen, "screenshot": screenshot or f"{turn:04}.png",
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
            if last_action and last_action.get("action") in WALK_ACTIONS:
                screen_changed = self.walked > 0  # un aller-retour ramène le même écran, mais le héros a bougé
            stuck = self.check_stuck(last_action, screen_changed)
            if stuck and state["window_open"]:
                self.actions.press("close")  # sans fenêtre, Échap ouvrirait le menu du jeu
                image, state = self.look()
            mode = mode_for(state, previous)
            reasons = describe_reasons(mode, state, previous, last_action, screen_changed)
            if stuck:
                reasons.insert(0, stuck)
                lost = mode in (EXPLORE, ALERT) or (mode == COMBAT and not state["attack_button"])
                if lost and not self.map_view:
                    self.open_map()  # perdu (aucun ennemi au contact) : la carte, sans attendre qu'il la demande
                    image, state = self.look()
            if self.feedback:
                reasons.insert(0, self.feedback)
                self.feedback = None
            added = new_messages([m["text"] for m in previous["messages"]],
                                 [m["text"] for m in state["messages"]]) if previous else []
            game = game_state.load()  # état exact exporté par le Chonk build (None avec le jeu officiel)
            floor_changed = self.memory.observe_messages(added)
            if game and self.memory.observe_depth(game.get("depth")):
                floor_changed = True
            if floor_changed:
                reasons.insert(0, f"you are now on floor {self.memory.floor}")
            self.kills += sum(1 for text in added if text.startswith("You defeated"))
            self.items += sum(1 for text in added if text.startswith("You picked up"))
            self.memory.record_outcome(reasons)
            self.memory.save(self.run_dir / "memory.json")
            print(f"\nTour {turn} [{mode}] étage {self.memory.floor} : " + " ; ".join(reasons)
                  + (" [carte]" if self.map_view else ""))
            image.save(self.run_dir / f"{turn:04}.png")
            screenshot = None
            if self.map_view:
                screenshot = f"{turn:04}-map.png"
                self.map_view["picture"].save(self.run_dir / screenshot)

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
            elif mode == MENU and self.chasm_window(image):
                decision = {"key": "close", "reasoning": "chasm: never jump (it does not count), going around"}
                self.feedback = ("You stepped next to a chasm and the game asked to jump in: the harness said no."
                                 " Jumping never counts for the goal. Chasm tiles cannot be crossed: walk around"
                                 " them or explore another part of the floor.")
            else:
                self.targets = game_state.targets(game_state.Floor(game), self.memory.examined) if game else {}
                situation = {
                    "turn": turn, "mode": mode, "reasons": reasons, "state": state,
                    # Image seulement si l'écran n'est pas que du texte déjà lu ; la carte à la place de la capture
                    "image": (self.map_view["picture"] if self.map_view else
                              None if self.examined and isinstance(self.examined, str) else resize(image)),
                    "map": self.map_view is not None,
                    "examined": self.examined,
                    "memory": self.memory.to_text(),
                    "game": game_state.describe(game, self.targets) if game else None,
                }
                self.examined = None
                self.brain.last_call = None
                decision = self.brain.decide(situation)
                call = self.brain.last_call
                if decision is None:
                    self.log_turn(turn, mode, reasons, state, None, call, screenshot)
                    print("Le cerveau s'arrête.")
                    return "stopped"
            for action in decision.get("plan") or [decision]:
                if action.get("target") in self.targets:  # goto, examine : pour l'historique et le viewer
                    action["label"] = self.targets[action["target"]]["label"]
                    point = self.target_point(action["target"])
                    if point:
                        action["screen_2d"] = point  # où la cible est à l'écran (dessinée par le viewer)
            plan, dropped = self.plan_of(decision)
            print(f"   -> {decision}")
            self.memory.apply(decision)  # avant le log : le carnet du tour inclut la note qui vient d'être écrite
            self.log_turn(turn, mode, reasons, state, decision, call, screenshot)
            if decision.get("reasoning"):
                with (self.run_dir / "reasoning.txt").open("a", encoding="utf-8") as notes:
                    notes.write(f"{turn}\t{mode}\t{decision['reasoning']}\n")
            self.memory.record_action(turn, mode, decision)
            last_action = self.play(plan, dropped)
            previous, previous_image = state, image
            image, state = self.look()
        print(f"Limite de {self.max_turns} tours atteinte.")
        return "max_turns"

    def plan_of(self, decision):
        """Les actions à jouer : celles du plan (au plus MAX_PLAN) ou la décision seule. click, examine et map
        se jouent seuls (le modèle doit voir leur résultat avant la suite), comme toute action sur la carte.
        Renvoie (actions, explication pour le modèle si une partie du plan est écartée)."""
        actions = (decision.get("plan") or [decision])[:MAX_PLAN]
        for index, action in enumerate(actions):
            if action.get("action") == "goto" and index + 1 < len(actions):
                return actions[:index + 1], ("the actions after goto were not played: once you have moved, the"
                                             " targets change. Put goto last in a plan.")
            if "point_2d" in action or action.get("action") in ("map", "examine") or self.map_view:
                keep = max(index, 1)
                if keep < len(actions):
                    return actions[:keep], (f"only the first {keep} action(s) of your plan were played: click,"
                                            " examine and map must come alone (you need to see their result"
                                            " first), and on the map only one action is played.")
                break
        return actions, None

    def play(self, actions, dropped=None):
        """Joue les actions d'un plan dans l'ordre. S'arrête à une action refusée, à un « move » qui n'a pas fait
        tous ses pas, ou dès que la situation change (plan_interrupted) : la suite du plan ne vaut plus.
        Le compte rendu va au modèle au tour suivant. Renvoie la dernière action jouée (pour STUCK)."""
        report = [dropped] if dropped else []
        played = None
        start = before = self.state
        for index, action in enumerate(actions):
            refused = self.refuse(action, before)
            if refused:
                print(f"   action {index + 1}/{len(actions)} refusée : {refused}")
                report.append(refused)  # pas jouée : expliqué au modèle au tour suivant
                break
            if len(actions) > 1:
                print(f"   action {index + 1}/{len(actions)} : {describe_action(action)}")
            self.walk_complete = True
            if self.map_view and "point_2d" in action:
                # Point sur la carte -> point sur l'écran dézoomé : on agit avant de rezoomer
                view = self.map_view
                point = map_to_screen(action["point_2d"], view["box"], view["scale"], view["picture"].size)
                self.execute(dict(action, point_2d=point))
                self.close_map()
            else:
                self.close_map()
                self.execute(action)
            played = action
            if self.feedback:
                report.append(self.feedback)
                self.feedback = None
            if index + 1 == len(actions):
                if len(actions) > 1:
                    report.append(f"plan: all {len(actions)} actions done.")
                break
            if not self.walk_complete:
                report.append(f"plan stopped after {index + 1} of {len(actions)} actions: the move did not finish.")
                break
            _, after = self.look()
            why = plan_interrupted(action, start, before, after)
            if why:
                print("   plan interrompu : " + " ; ".join(why))
                report.append(f"plan stopped after {index + 1} of {len(actions)} actions because: " + "; ".join(why))
                break
            before = after
        self.feedback = " ".join(report) or None
        return played or actions[0]

    def open_map(self):
        """Dézoome la vue du jeu au maximum et prépare la carte de la partie explorée de l'étage."""
        if self.map_view:
            return
        for _ in range(ZOOM_STEPS):
            self.actions.press(ZOOM_OUT_KEY, wait=False)
            time.sleep(0.3)
        image, _, _ = self.window.wait_stable()
        picture, box, scale = build_map(image)
        self.map_view = {"picture": picture, "box": box, "scale": scale}

    def close_map(self):
        """Remet le zoom normal (sans effet si la carte n'est pas affichée)."""
        if not self.map_view:
            return
        self.map_view = None
        for _ in range(ZOOM_STEPS):
            self.actions.press(ZOOM_IN_KEY, wait=False)
            time.sleep(0.3)
        self.window.wait_stable()

    def chasm_window(self, image):
        """La fenêtre ouverte est-elle « voulez-vous sauter dans le gouffre ? » (le harness répond toujours non)."""
        rect = window_rect(image)
        text = self.reader.read_window(image.crop(rect)) if rect else ""
        return "jump into the chasm" in text

    @staticmethod
    def refuse(decision, state):
        """Raison de ne pas jouer une décision qui ne peut rien faire, ou None."""
        if decision.get("action") == "attack" and not state["attack_button"]:
            return ("attack did nothing: no enemy is next to the hero (there is no red attack button). Move next"
                    " to the enemy first, or wait for it to come to you.")
        return None

    def execute(self, decision):
        action = decision.get("action")
        if action == "examine" and decision.get("target"):
            point = self.target_point(decision["target"])
            if point is None:
                self.examined = f"examine: unknown or off-screen target '{decision['target']}'"
            else:
                chosen = self.targets[decision["target"]]
                if chosen["kind"] == "enemy":  # même si la fenêtre n'est pas lisible par l'OCR
                    self.memory.examined.add(chosen["label"].split(": ", 1)[1].split(" (")[0].lower())
                self.examine(point)
                game = game_state.load()
                mob = next((m for m in (game or {}).get("mobs", []) if (m["x"], m["y"]) == chosen["cell"]), None)
                if mob and mob.get("info"):
                    # Le texte exact de la fenêtre qui vient de s'ouvrir, exporté par le jeu (l'OCR ne lit pas
                    # la mise en page des fenêtres de monstres)
                    self.examined = f"{mob['name'].capitalize()}\n{mob['info']}"
                    self.memory.note_examined(self.examined)
        elif action == "examine":
            self.examine(decision["point_2d"])
        elif action == "use":
            self.feedback = self.use(decision.get("item") or "", decision.get("verb") or "",
                                     decision.get("target"))
        elif action == "map":
            self.open_map()
        elif action == "move":
            self.feedback = self.walk(decision.get("steps") or [])
        elif action == "goto":
            self.feedback = self.goto(decision.get("target"))
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

    def to_2d(self, pixel):
        """Pixels de la fenêtre du jeu -> point_2d (0-1000), le format des clics."""
        _, _, width, height = self.window.client_rect()
        return [round(pixel[0] / width * 1000), round(pixel[1] / height * 1000)]

    def target_point(self, target):
        """point_2d de la case d'une cible, si elle est à l'écran et cliquable (hors des boutons), sinon None."""
        chosen = self.targets.get(target)
        game, ui = game_state.load(), game_state.load_ui()
        if not (chosen and game and ui):
            return None
        hero = (game["hero"]["x"], game["hero"]["y"])
        pixel = game_state.screen_point(ui, hero, chosen["cell"])
        return self.to_2d(pixel) if game_state.clickable(ui, pixel) else None

    def goto(self, target):
        """Clique sur la cible comme un joueur : le jeu trace le chemin et fait marcher le héros, et
        s'arrête de lui-même si un ennemi apparaît. Cible hors de l'écran : clic sur le point le plus lointain
        du chemin encore visible, puis on recommence. Renvoie le compte rendu pour le modèle."""
        self.walked = 0
        self.walk_complete = False
        chosen = self.targets.get(target)
        if not chosen:
            names = ", ".join(self.targets) or "none (no Game state this turn)"
            return f"goto: unknown target '{target}'. Targets this turn: {names}."
        label, goal = chosen["label"], chosen["cell"]
        before = self.state
        for click in range(GOTO_CLICKS):
            game, ui = game_state.load(), game_state.load_ui()
            if not (game and ui):
                return f"goto {target}: the game state is not available."
            floor = game_state.Floor(game)
            if game["depth"] != self.memory.floor:
                break  # l'escalier a été pris
            steps = floor.path(goal)
            if not steps:
                return f"goto {target} ({label}): no known path from here."
            # La case la plus lointaine du chemin qui est à l'écran, hors des boutons
            cell, pick = floor.hero, None
            for step in steps:
                dx, dy = game_state.DIRECTIONS[step]
                cell = (cell[0] + dx, cell[1] + dy)
                pixel = game_state.screen_point(ui, floor.hero, cell)
                if game_state.clickable(ui, pixel):
                    pick = (cell, pixel)
            if pick is None:  # rien de cliquable (interface devant) : au clavier
                return f"goto {target} ({label}): " + self.walk(steps, limit=MAX_GOTO)
            start = floor.hero
            self.actions.click(self.to_2d(pick[1]), wait=False)
            image, _, _ = self.window.wait_stable(timeout=WALK_TIMEOUT)
            after = read_screen(image, self.reader)
            self.state = after
            game = game_state.load()
            moved = game and (game["hero"]["x"], game["hero"]["y"]) != start
            if moved:
                self.walked += 1
            happened = changes(before, after)
            arrived = game and (game["depth"] != self.memory.floor or pick[0] == goal)
            if arrived:
                break
            if not moved:
                return (f"goto {target} ({label}): the hero did not move (the way may be blocked)"
                        + (": " + "; ".join(happened) if happened else "."))
            if happened:
                return f"goto {target} ({label}): stopped on the way because: " + "; ".join(happened)
            before = after
        self.walk_complete = True
        return f"goto {target} ({label}): done."

    def use(self, item, verb, target=None):
        """Utilise un objet du sac comme un joueur : ouvre l'inventaire, clique sur l'objet puis sur le bouton
        de l'action (DRINK, EAT...), et sur une cible si l'action en demande une (THROW). Referme l'inventaire
        s'il était fermé. Renvoie le compte rendu pour le modèle."""
        opened = not self.state["inventory_open"]
        if opened:
            self.actions.press("inventory")
        started = time.time()
        ui = game_state.load_ui(newer_than=started)
        slot = game_state.find_slot(ui, item) if ui else None
        if not slot:
            if opened:
                self.actions.press("inventory")
            return f"use: no '{item}' in your bag (use the name shown in 'Your bag')."
        self.actions.click(self.to_2d(game_state.center(slot["rect"])))
        ui = game_state.load_ui(newer_than=time.time())
        button = game_state.find_button(ui, verb) if ui and verb else None
        if not button:
            choices = [b["text"] for b in (ui or {}).get("buttons", []) if b["window"] and b["text"]]
            self.actions.press("close")
            if opened:
                self.actions.press("inventory")
            return f"use: '{verb}' is not an action of {slot['item']} (its actions: {', '.join(choices)})."
        self.actions.click(self.to_2d(game_state.center(button["rect"])))
        report = f"use: {button['text'].lower()} {slot['item']}"
        if target:
            point = self.target_point(target)
            if point:
                self.actions.click(point)
                report += f" at {target}"
            else:
                report += f" (target {target} is not on screen: the game may be waiting for a target)"
        image, _, _ = self.window.wait_stable()
        if opened and read_screen(image, self.reader)["inventory_open"]:
            self.actions.press("inventory")
        return report + "."

    def walk(self, steps, limit=MAX_STEPS):
        """Joue les pas un par un. S'arrête si un pas ne fait pas bouger le héros (mur, obstacle) ou si
        quelque chose change (ennemi, objet, vie, fenêtre, message). Renvoie le compte rendu pour le modèle."""
        steps = [step for step in steps if step in MOVE_KEYS][:limit]
        self.walked = 0
        self.walk_complete = False
        if not steps:
            return "move: no valid step given (use up, down, left, right, up_left, up_right, down_left, down_right)."
        before_image, before_state = self.window.grab(), self.state
        for done, step in enumerate(steps):
            self.actions.press(step, wait=False)
            image, _, _ = self.window.wait_stable(timeout=3.0)
            state = read_screen(image, self.reader)
            if changed_fraction(before_image, image) <= MOVED:
                return (f"move: {done} of {len(steps)} steps done, then '{step}' was blocked (a wall or an"
                        " obstacle is there): the hero did not move.")
            self.walked = done + 1
            happened = changes(before_state, state) if before_state else []
            if happened and done + 1 < len(steps):
                return f"move: stopped after {done + 1} of {len(steps)} steps because: " + "; ".join(happened)
            before_image, before_state = image, state
        self.walk_complete = True
        return f"move: all {len(steps)} step{'s' if len(steps) > 1 else ''} done."

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
            try:
                harness.close_map()  # ne pas laisser le jeu dézoomé
            except Exception:
                traceback.print_exc()
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

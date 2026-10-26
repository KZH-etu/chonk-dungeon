"""Étape 12 : la mémoire du harness.

- Historique court : les dernières actions, chacune avec ce qui s'est passé ensuite
- Carnet de notes : le modèle ajoute ou efface des notes ; ce qu'il examine y va tout seul
- Étage courant : suivi grâce au journal (« You descend to floor 2 of the dungeon. »)

Pas d'historique d'images : une seule capture par appel.
"""

import json
import re
from collections import deque

HISTORY_SIZE = 8
MAX_NOTES = 15
NOTE_MAX_CHARS = 160
SIMILAR_NOTE = 0.4    # part de mots en commun au-delà de laquelle une note remplace l'ancienne
FLOOR_MESSAGE = re.compile(r"You (descend|return) to floor (\d+)")
FLOOR_PREFIX = re.compile(r"^(\(?floor \d+\)?\s*[:,-]?\s*)+", re.IGNORECASE)  # le harness ajoute le sien
STOP_WORDS = {"the", "a", "an", "of", "to", "is", "in", "on", "at", "for", "and", "or", "it", "i", "be"}


def _words(text):
    return set(re.findall(r"[a-z]+", text.lower())) - STOP_WORDS


def similarity(a, b):
    """Part de mots (hors mots vides) communs à deux notes, de 0 à 1."""
    a, b = _words(a), _words(b)
    return len(a & b) / len(a | b) if a | b else 1.0


def describe_action(decision):
    """Une action (ou un plan d'actions) en quelques mots, pour l'historique."""
    if "plan" in decision:
        return "; ".join(describe_action(action) for action in decision["plan"])
    if "key" in decision:
        return f"key {decision['key']}"
    action = decision.get("action")
    point = decision.get("point_2d")
    if action == "examine" and not decision.get("target"):
        return f"examine {point}"
    if action == "move":
        return "move " + ",".join(decision.get("steps") or [])
    if action == "goto":
        return f"goto {decision.get('target')}" + (f" ({decision['label']})" if decision.get("label") else "")
    if action == "use":
        return f"use {decision.get('item')} {decision.get('verb')}" + (f" at {decision['target']}" if decision.get("target") else "")
    if action == "examine" and decision.get("target"):
        return f"examine {decision['target']}" + (f" ({decision['label']})" if decision.get("label") else "")
    if action:
        return action
    label = decision.get("label")
    return f"click {point}" + (f" ({label})" if label else "")


class Memory:
    def __init__(self):
        self.history = deque(maxlen=HISTORY_SIZE)  # {"turn", "mode", "action", "outcome"}
        self.notes = []
        self.floor = 1
        self.deepest = 1
        self.goal = None  # objectif du moment, donné par le modèle et rappelé à l'appel suivant
        self.examined = set()  # noms (en minuscules) de ce qui a été examiné : monstres « NOT EXAMINED YET »

    # --- historique ----------------------------------------------------------

    def record_action(self, turn, mode, decision):
        self.history.append({"turn": turn, "mode": mode, "action": describe_action(decision), "outcome": None})

    def record_outcome(self, reasons):
        """Ce qui a suivi la dernière action (les raisons du tour suivant)."""
        if self.history and self.history[-1]["outcome"] is None:
            self.history[-1]["outcome"] = "; ".join(reasons)

    # --- carnet ----------------------------------------------------------------

    def add_note(self, text, prefix=""):
        """Ajoute une note ; une note presque identique à une note existante la remplace (le modèle a
        tendance à réécrire la même idée à chaque tour, ce qui remplit le carnet et coûte des tokens)."""
        text = prefix + " ".join(FLOOR_PREFIX.sub("", str(text)).split())[:NOTE_MAX_CHARS]
        if not text.strip() or text in self.notes:
            return
        scores = [similarity(text, note) for note in self.notes]
        if scores and max(scores) >= SIMILAR_NOTE:
            self.notes.pop(scores.index(max(scores)))
        self.notes.append(text)
        if len(self.notes) > MAX_NOTES:
            self.notes.pop(0)  # la plus ancienne

    def remove_note(self, number):
        """Efface la note n° `number` (numérotée à partir de 1, comme dans le prompt)."""
        if isinstance(number, int) and 1 <= number <= len(self.notes):
            self.notes.pop(number - 1)

    def apply(self, decision):
        """Applique les changements de carnet demandés par le modèle."""
        if decision.get("goal"):
            self.goal = " ".join(str(decision["goal"]).split())[:NOTE_MAX_CHARS]
        self.remove_note(decision.get("note_remove"))
        if decision.get("note_add"):
            # L'étage où la note est écrite : une note de l'étage 1 ne vaut plus à l'étage 2
            self.add_note(decision["note_add"], prefix=f"(floor {self.floor}) ")

    def note_examined(self, text):
        """Ce qui a été examiné : titre et première phrase de la description."""
        lines = [line for line in text.splitlines() if line.strip()]
        if not lines:
            return
        self.examined.add(lines[0].strip().lower())
        summary = lines[0] + (f": {lines[1]}" if len(lines) > 1 else "")
        first_sentence = summary.split(". ")[0].rstrip(".") + "."
        if not any(note.startswith(f"Examined {lines[0]}:") for note in self.notes):
            self.add_note(f"Examined {first_sentence}")

    # --- étage -----------------------------------------------------------------

    def observe_messages(self, messages):
        """Met à jour l'étage d'après les nouveaux messages du journal. Renvoie True si l'étage a changé."""
        changed = False
        for text in messages:
            match = FLOOR_MESSAGE.search(text)
            if match and int(match.group(2)) != self.floor:
                self.floor = int(match.group(2))
                self.deepest = max(self.deepest, self.floor)
                self.goal = None  # le but de l'étage précédent ne vaut plus
                changed = True
        return changed

    def observe_depth(self, depth):
        """Étage lu directement dans le jeu (Chonk build). Renvoie True si l'étage a changé."""
        if not isinstance(depth, int) or depth == self.floor:
            return False
        self.floor = depth
        self.deepest = max(self.deepest, depth)
        self.goal = None
        return True

    # --- pour le prompt et les logs ----------------------------------------------

    def to_text(self):
        lines = [f"Current floor: {self.floor} (deepest reached: {self.deepest})."]
        if self.goal:
            lines.append(f"Your current goal (from your last answer): {self.goal}")
        if self.history:
            lines.append("Your recent actions (oldest first) and what followed:")
            for item in self.history:
                outcome = item["outcome"] or "(just played)"
                lines.append(f"- T{item['turn']} [{item['mode']}] {item['action']} -> {outcome}")
        if self.notes:
            lines.append("Your notebook (note_remove takes the number):")
            lines += [f"{i}. {note}" for i, note in enumerate(self.notes, 1)]
        else:
            lines.append("Your notebook is empty.")
        return "\n".join(lines)

    def save(self, path):
        path.write_text(json.dumps({"floor": self.floor, "deepest": self.deepest, "goal": self.goal, "notes": self.notes,
                                    "history": list(self.history)}, indent=2, ensure_ascii=False),
                        encoding="utf-8")

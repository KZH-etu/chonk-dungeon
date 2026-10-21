"""Étape 11 : le cerveau Mistral du harness (prompt, format de réponse, appel).

Le prompt est en anglais, comme le texte du jeu. La réponse est un JSON imposé par
l'API (json_schema strict) : le modèle ne peut ni oublier un champ ni inventer une action.
"""

import json

from actions import KEYS
from capture import to_data_url
from llm import RateLimiter, complete, cost_usd, default_model, make_client

MAX_TOKENS = 300
EXPECTED_TOKENS = 2_600  # estimation d'un appel avec image, avant d'avoir le vrai chiffre

SYSTEM_PROMPT = """You are playing Shattered Pixel Dungeon, a turn-based roguelike, through a harness.
Goal: reach floor 3. You start on floor 1, so you must take a DOWN staircase twice.
Only DOWN staircases count: never take the UP staircase (the one you arrive by on each floor; examine
calls it "Depth entrance" and says the stairs lead up).

How it works
- Each call you get: why you are called, the screen state read by the harness (HP, buttons, enemies
  in view), the game log as text, often a screenshot, and sometimes the text of an examined tile.
- You answer with ONE action. The harness plays it, waits until the screen is still, and calls you
  again when something matters (a long walk is a single call).
- Positions: "point_2d": [x, y], normalized 0-1000 on the screenshot ([0, 0] top-left,
  [1000, 1000] bottom-right). Point at the CENTER of the tile.
- Memory: you only see one screenshot per call, so the harness keeps for you the current floor, your
  recent actions with what followed (learn from them: do not repeat what did not work), and a notebook.
  Write a note when you learn something worth keeping (where the down staircase is, a dangerous enemy,
  what an item does, your plan for this floor); remove notes that are no longer true. Screen positions
  change when the camera moves, so describe places in words ("down stairs in the room north of the
  start"), not with coordinates. What you examine is noted for you automatically.

Actions
- click (point_2d): walk to a tile (the hero walks the whole path on his own and stops if something
  happens), open a door by walking into it, attack an adjacent enemy by clicking it, take the down
  staircase by clicking it, or press a button inside an open window.
- examine (point_2d): the game describes what is on that tile (monster, item, trap, terrain).
  It takes no game turn. When you are not sure what something is, examine it instead of guessing.
- attack: attack the enemy shown on the red button (nearest enemy).
- loot: pick up the item under the hero (blue button).
- key (key): press a game key, e.g. a one-tile step (up, down, left, right, up_left, up_right,
  down_left, down_right), inventory, journal, hero_info, close (closes a window), quickslot_1..6.
- wait: skip one turn. rest: rest until healed or disturbed. resume: continue an interrupted walk.

What the screen shows
- The camera follows the hero: he is at the center of the screen, around [500, 500] (higher, around
  [500, 450], when the inventory panel is open). Never click his own tile: it does nothing useful.
- The log is at the bottom left, the HP bar under it.
- Red button on the right edge: an enemy can be attacked. Blue button: an item is under the hero.
- Red counter with a skull under the top-right menu: enemies are in view.
- Staircases: a dark pit with grey steps. The one near where you arrived goes UP: ignore it. When in
  doubt, examine a staircase before taking it. Doors are brown wooden rectangles in walls.
- Anything else you do not recognize: use examine.

Play well
- Explore with FAR targets: one click walks the whole path. Aim for doors, corridor openings and the
  edges of explored areas (unexplored parts are black). Do not click tiles next to the hero to explore.
- Explore each floor to find items and the down staircase, then go down.
- Fight one enemy at a time, ideally in a corridor or a doorway. Do not walk into several enemies.
- Sewer snakes dodge a lot. To hit them reliably, attack a snake when it stands on a DOOR tile right
  next to you: wait near the doorway for it to step onto the door, then attack.
- Rest when HP is low and no enemy is in view. Eat when the log says you are hungry.
- Orange log lines are warnings, red ones are dangerous, green ones are good news.

Answer
- "reasoning": one short sentence (max 20 words), shown to viewers.
- "action" and its fields; set unused fields to null. "label": what is at point_2d ("unknown" if unsure).
- "note_add": a short note to add to your notebook, or null. "note_remove": number of a note to delete,
  or null. Most turns need no note."""

_POINT = {"type": "array", "items": {"type": "integer"}}
_SCHEMA = {
    "type": "object",
    "properties": {
        "reasoning": {"type": "string"},
        "action": {"type": "string",
                   "enum": ["click", "examine", "attack", "loot", "key", "wait", "rest", "resume"]},
        "point_2d": {"anyOf": [_POINT, {"type": "null"}]},
        "key": {"anyOf": [{"type": "string", "enum": sorted(KEYS)}, {"type": "null"}]},
        "label": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "note_add": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "note_remove": {"anyOf": [{"type": "integer"}, {"type": "null"}]},
    },
    "required": ["reasoning", "action", "point_2d", "key", "label", "note_add", "note_remove"],
    "additionalProperties": False,
}
RESPONSE_FORMAT = {"type": "json_schema",
                   "json_schema": {"name": "game_action", "schema_definition": _SCHEMA, "strict": True}}


def situation_text(situation):
    """Le message de chaque tour, en texte (l'image éventuelle est jointe à part)."""
    state = situation["state"]
    yes_no = lambda value: "yes" if value else "no"  # noqa: E731
    lines = [
        f"Turn {situation['turn']}. Mode: {situation['mode']}.",
        "Why you are called: " + "; ".join(situation["reasons"]),
        f"Screen state: HP {state['hp_ratio']:.0%}; attack button: {yes_no(state['attack_button'])};"
        f" pickup button: {yes_no(state['pickup_button'])}; enemies in view: {yes_no(state['enemies_visible'])};"
        f" inventory panel open: {yes_no(state['inventory_open'])}; window open: {yes_no(state['window_open'])}.",
    ]
    if state["messages"]:
        lines.append("Game log (oldest first):")
        lines += [f"- {line['text']}" + (f" [{line['color']}]" if line["color"] != "white" else "")
                  for line in state["messages"]]
    if situation.get("memory"):
        lines.append(situation["memory"])
    examined = situation.get("examined")
    if isinstance(examined, str):
        lines.append(f"Examine result: {examined}")
    elif examined is not None:
        lines.append("Examine result: the description window is attached as an image.")
    if situation["image"] is None and not (examined is not None and not isinstance(examined, str)):
        lines.append("No screenshot this time: the examine result above is all that changed.")
    return "\n".join(lines)


def to_decision(answer):
    """Réponse du modèle -> décision du harness (None si incomplète), avec les changements de carnet."""
    decision = _action(answer)
    if decision:
        for field in ("note_add", "note_remove"):
            if answer.get(field) is not None:
                decision[field] = answer[field]
    return decision


def _action(answer):
    action = answer.get("action")
    point = answer.get("point_2d")
    has_point = isinstance(point, list) and len(point) == 2
    reasoning = answer.get("reasoning", "")
    if action == "click" and has_point:
        return {"point_2d": point, "label": answer.get("label") or "", "reasoning": reasoning}
    if action == "examine" and has_point:
        return {"action": "examine", "point_2d": point, "reasoning": reasoning}
    if action == "key" and answer.get("key") in KEYS:
        return {"key": answer["key"], "reasoning": reasoning}
    if action in ("attack", "loot", "wait", "rest", "resume"):
        return {"action": action, "reasoning": reasoning}
    return None


class MistralBrain:
    def __init__(self, model=None, reasoning="none", budget_usd=0.5, dry_run=False):
        self.model = model or default_model()
        self.reasoning = reasoning
        self.budget_usd = budget_usd
        self.dry_run = dry_run
        self.client = None if dry_run else make_client()
        self.limiter = RateLimiter()
        self.calls = 0
        self.tokens_in = self.tokens_out = 0
        self.cost = 0.0
        self.last_prompt_tokens = EXPECTED_TOKENS

    def messages(self, situation):
        content = [{"type": "text", "text": situation_text(situation)}]
        image = situation["image"]
        examined = situation.get("examined")
        if examined is not None and not isinstance(examined, str):
            image = examined  # fenêtre « examiner » illisible : on envoie son image
        if image is not None:
            content.append({"type": "image_url", "image_url": to_data_url(image)})
        return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": content}]

    def decide(self, situation):
        messages = self.messages(situation)
        if self.dry_run:
            print("----- prompt (sans appel API) -----")
            print(messages[1]["content"][0]["text"])
            print(f"[image jointe : {'oui' if len(messages[1]['content']) > 1 else 'non'}]")
            return None
        if self.cost >= self.budget_usd:
            print(f"   Budget de la partie atteint ({self.cost:.3f} $ sur {self.budget_usd} $) : arrêt.")
            return None
        for attempt in range(2):  # réponse illisible : un nouvel essai, puis on attend
            self.limiter.wait(self.last_prompt_tokens + MAX_TOKENS)
            try:
                response, seconds = complete(self.client, self.model, messages, MAX_TOKENS,
                                             response_format=RESPONSE_FORMAT, reasoning=self.reasoning)
            except RuntimeError as error:  # 429 qui persiste : on ne fait pas planter la partie
                print(f"   {error} On attend un tour.")
                return {"action": "wait", "reasoning": "rate limited"}
            usage = response.usage
            self.limiter.record(usage.prompt_tokens + usage.completion_tokens)
            self.last_prompt_tokens = usage.prompt_tokens
            self.calls += 1
            self.tokens_in += usage.prompt_tokens
            self.tokens_out += usage.completion_tokens
            call_cost = cost_usd(self.model, usage)
            self.cost += call_cost
            text = response.choices[0].message.content or ""
            print(f"   [{self.model}] {usage.prompt_tokens} + {usage.completion_tokens} tokens,"
                  f" {seconds:.1f} s, {call_cost:.4f} $ (partie : {self.cost:.3f} $)")
            try:
                decision = to_decision(json.loads(text))
            except (json.JSONDecodeError, TypeError, AttributeError):
                decision = None
            if decision:
                return decision
            print(f"   Réponse inutilisable ({text[:120]!r}), essai {attempt + 1}/2")
        return {"action": "wait", "reasoning": "unreadable answer"}

    def summary(self):
        return (f"{self.calls} appels, {self.tokens_in} + {self.tokens_out} tokens, {self.cost:.4f} $"
                f" ({self.model})")

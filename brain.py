"""Étape 11 : le cerveau Mistral du harness (prompt, format de réponse, appel).

Le prompt est en anglais, comme le texte du jeu. La réponse est un JSON imposé par
l'API (json_schema strict) : le modèle ne peut ni oublier un champ ni inventer une action.
"""

import json

from actions import KEYS
from capture import to_data_url
from llm import RateLimiter, complete, cost_usd, default_model, make_client

MAX_TOKENS = 500  # un plan de plusieurs actions est plus long qu'une action seule
MAX_PLAN = 5      # actions au plus par réponse
EXPECTED_TOKENS = 2_600  # estimation d'un appel avec image, avant d'avoir le vrai chiffre

SYSTEM_PROMPT = """You are playing Shattered Pixel Dungeon, a turn-based roguelike, through a harness.
Goal: reach floor 3. You start on floor 1, so you must take a DOWN staircase twice.
Only DOWN staircases count: never take the UP staircase (the one you arrive by on each floor; examine
calls it "Depth entrance" and says the stairs lead up). Jumping into a chasm never counts: the harness
always refuses the jump.

How it works
- Each call you get: why you are called, the screen state read by the harness (HP, buttons, enemies
  in view), the game log as text, often a screenshot, and sometimes the text of an examined tile.
- You answer with a PLAN: a list of 1 to 5 actions, played in order. The harness plays them, waits
  until the screen is still, and calls you again when the plan is over. If something changes on the way
  (an enemy appears, HP drops, a window opens, an item, a log message...), the rest of the plan is dropped
  and you are told why: plan only what is safe from what you see now. One or two actions is often enough.
  Good plans: ["attack", "attack", "attack"] against one weak enemy next to you; ["wait", "wait", "wait"]
  next to a doorway until an enemy steps in (the plan stops as soon as it is next to you); ["loot",
  "move ..."]; ["rest", "move ..."].
- click, examine and map always come alone (a plan of one action): you need to see their result first.
- Positions: "point_2d": [x, y], normalized 0-1000 on the screenshot ([0, 0] top-left,
  [1000, 1000] bottom-right). Point at the CENTER of the tile.
- Memory: you only see one screenshot per call, so the harness keeps for you the current floor, your
  recent actions with what followed (learn from them: do not repeat what did not work), and a notebook.
  Write a note when you learn a lasting fact (where the down staircase is, a dead end, a dangerous
  enemy, what an item does); remove notes that are no longer true. Do not note what you are doing right
  now: your recent actions are already listed. A note close to an existing one replaces it. Screen positions
  change when the camera moves, so describe places in words ("down stairs in the room north of the
  start"), not with coordinates. What you examine is noted for you automatically.

Actions
- goto (target): when your message has a "Game state" block (read from the game itself), walk the shortest
  known path to one of its Targets, by id ("T1", "T2"...): the DOWN stairs, an edge of the explored area,
  an item, an enemy. THE way to travel: the harness plays the steps and stops early if something happens
  (enemy, item, message), then tells you. Put goto last in a plan: the targets change once you have moved.
- move (steps): a few precise steps. A list of one-tile steps played with the keyboard,
  e.g. ["right", "right", "right", "down"] (up, down, left, right, up_left, up_right, down_left,
  down_right; at most 10). Count the tiles on the screenshot: the hero's tile is in the center, one tile
  is about 37 wide and 67 tall in point_2d units. Walking into a door opens it; walking into an adjacent
  enemy attacks it; stepping onto the down staircase takes it. The harness plays the steps one by one
  and stops early if a step is blocked (wall, obstacle) or if something happens (enemy, item, message),
  then tells you how many steps were done.
- click (point_2d): press a button inside an open window, or, on the MAP view only, walk far to an
  explored tile in one go. Do not click to walk on the normal screen: use move.
- examine (point_2d): the game describes what is on that tile (monster, item, trap, terrain).
  It takes no game turn. When you are not sure what something is, examine it instead of guessing.
- attack: attack the enemy shown on the red button (nearest enemy). Only when the red button is shown:
  otherwise no enemy is next to the hero and the harness refuses it.
- loot: pick up the item under the hero (blue button).
- key (key): press a game key: inventory, journal, hero_info, close (closes a window), quickslot_1..6.
- wait: skip one turn. rest: rest until healed or disturbed. resume: continue an interrupted walk.
- map: see the MAP of everything explored on this floor (takes no game turn). Use it when you do not
  know where to go, before going back, or to find unexplored doors and corridors. On the map, the hero
  is in the yellow circle, and you can click any explored tile to walk there in one go.

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
- When there is a "Game state" block, trust it over your reading of the screenshot: its map is exact.
  Go down as soon as the DOWN stairs are a target and you are not in danger; otherwise goto the nearest
  edge of the explored area, an item on the way, or fight the enemy that comes to you.
- Explore with long moves (up to 10 steps) toward doors, corridor openings and the edges of the explored
  area. For a place far away, open the map and click it there.
- Black areas are unexplored: walk to the edge of the lit area, the hero sees further from there.
  Walls cannot be walked through: if a step is blocked, look again and go around.
- When a way is blocked, do not insist: go back and take another door or corridor. Floors have many.
- Items lying on the floor: walk onto them, the harness picks them up.
- Explore each floor to find items and the down staircase, then go down.
- Fight one enemy at a time, ideally in a corridor or a doorway. Do not walk into several enemies.
- Sewer snakes dodge a lot. To hit them reliably, attack a snake when it stands on a DOOR tile right
  next to you: wait near the doorway for it to step onto the door, then attack.
- Rest when HP is low and no enemy is in view. Eat when the log says you are hungry.
- Orange log lines are warnings, red ones are dangerous, green ones are good news.

Answer
- "reasoning": one short sentence (max 20 words), shown to viewers.
- "goal": your current objective in a few words ("explore the corridor east of the start room", "kill the
  rat", "find the down stairs"). It is shown to you next call: keep it until it is done, then set a new one.
- "plan": 1 to 5 actions; in each, set the fields the action does not use to null. "label": what is at
  point_2d ("unknown" if unsure).
- "note_add": a short note to add to your notebook, or null. "note_remove": number of a note to delete,
  or null. Most turns need no note."""

_POINT = {"type": "array", "items": {"type": "integer"}}
_DIRECTIONS = ["up", "down", "left", "right", "up_left", "up_right", "down_left", "down_right"]
_ACTION = {
    "type": "object",
    "properties": {
        "action": {"type": "string",
                   "enum": ["goto", "move", "click", "examine", "attack", "loot", "key", "wait", "rest", "resume",
                            "map"]},
        "target": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "steps": {"anyOf": [{"type": "array", "items": {"type": "string", "enum": _DIRECTIONS}}, {"type": "null"}]},
        "point_2d": {"anyOf": [_POINT, {"type": "null"}]},
        "key": {"anyOf": [{"type": "string", "enum": sorted(KEYS)}, {"type": "null"}]},
        "label": {"anyOf": [{"type": "string"}, {"type": "null"}]},
    },
    "required": ["action", "target", "steps", "point_2d", "key", "label"],
    "additionalProperties": False,
}
_SCHEMA = {
    "type": "object",
    "properties": {
        "reasoning": {"type": "string"},
        "goal": {"type": "string"},
        "plan": {"type": "array", "items": _ACTION},
        "note_add": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "note_remove": {"anyOf": [{"type": "integer"}, {"type": "null"}]},
    },
    "required": ["reasoning", "goal", "plan", "note_add", "note_remove"],
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
        "Usable now: " + ", ".join(usable_actions(situation)) + ".",
    ]
    if state["messages"]:
        lines.append("Game log (oldest first):")
        lines += [f"- {line['text']}" + (f" [{line['color']}]" if line["color"] != "white" else "")
                  for line in state["messages"]]
    if situation.get("game"):
        lines.append(situation["game"])
    if situation.get("memory"):
        lines.append(situation["memory"])
    if situation.get("map"):
        lines.append("The image is the MAP of the explored part of this floor (zoomed-out view), not the usual"
                     " screenshot. The hero is in the yellow circle. Black = unexplored. Any point_2d you give now"
                     " is on this map: click an explored tile to walk there in one go (doors, corridor ends, the"
                     " down staircase). The harness goes back to the normal view after your action.")
    examined = situation.get("examined")
    if isinstance(examined, str):
        lines.append(f"Examine result: {examined}")
    elif examined is not None:
        lines.append("Examine result: the description window is attached as an image.")
    if situation["image"] is None and not (examined is not None and not isinstance(examined, str)):
        lines.append("No screenshot this time: the examine result above is all that changed.")
    return "\n".join(lines)


def usable_actions(situation):
    """Les actions qui peuvent servir maintenant, d'après l'écran (comme une liste de coups légaux)."""
    state = situation["state"]
    if situation.get("map"):
        return ["click (an explored tile of the map, to walk there)"]
    if state["window_open"]:
        return ["click (a button of the window)", "key close"]
    usable = ["goto (a target id)"] if situation.get("game") else []
    usable += ["move", "examine", "map", "wait", "rest", "key"]
    usable.append("attack (red button shown)" if state["attack_button"] else
                  "NOT attack (no red button: no enemy next to you)")
    if state["pickup_button"]:
        usable.append("loot (blue button shown)")
    return usable


def to_decision(answer):
    """Réponse du modèle -> décision du harness (None si aucune action utilisable), avec le but et les
    changements de carnet. Une action mal remplie coupe le plan à cet endroit."""
    plan = []
    for item in (answer.get("plan") or [])[:MAX_PLAN]:
        action = _action(item) if isinstance(item, dict) else None
        if action is None:
            break
        plan.append(action)
    if not plan:
        return None
    decision = {"plan": plan, "reasoning": answer.get("reasoning", "")}
    for field in ("goal", "note_add", "note_remove"):
        if answer.get(field) is not None:
            decision[field] = answer[field]
    return decision


def _action(item):
    action = item.get("action")
    point = item.get("point_2d")
    steps = item.get("steps")
    if action == "move" and isinstance(steps, list) and steps:
        return {"action": "move", "steps": steps}
    if action == "goto" and isinstance(item.get("target"), str) and item["target"].strip():
        return {"action": "goto", "target": item["target"].strip().upper()}
    has_point = isinstance(point, list) and len(point) == 2
    if action == "click" and has_point:
        return {"point_2d": point, "label": item.get("label") or ""}
    if action == "examine" and has_point:
        return {"action": "examine", "point_2d": point}
    if action == "key" and item.get("key") in KEYS:
        return {"key": item["key"]}
    if action in ("attack", "loot", "wait", "rest", "resume", "map"):
        return {"action": action}
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
        # Ce que le harness enregistre pour ce tour (turns.jsonl) : prompt, réponses brutes, tokens, temps, coût
        self.last_call = {"model": self.model, "prompt": messages[1]["content"][0]["text"],
                          "image": len(messages[1]["content"]) > 1,
                          "responses": [], "tokens_in": 0, "tokens_out": 0, "seconds": 0.0, "cost_usd": 0.0}
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
            self.last_call["responses"].append(text)
            self.last_call["tokens_in"] += usage.prompt_tokens
            self.last_call["tokens_out"] += usage.completion_tokens
            self.last_call["seconds"] = round(self.last_call["seconds"] + seconds, 2)
            self.last_call["cost_usd"] = round(self.last_call["cost_usd"] + call_cost, 5)
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

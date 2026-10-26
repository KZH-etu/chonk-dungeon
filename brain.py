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

SYSTEM_PROMPT = """You are playing Shattered Pixel Dungeon, a turn-based roguelike, like a human player: you look at
the screen, think, and act with the mouse and keyboard through a harness. Viewers watch your reasoning.
Goal: go as deep as you can, floor by floor, by taking the DOWN stairs. Never take the UP stairs you arrive
by (they lead back up). Jumping into a chasm is always refused.

Each call you get
- Why you are called (what changed), the screen state, the game log, a screenshot, and usually a "Game
  state" block read from the game itself: an exact map around you, Targets (T1, T2...) with their distance,
  and the content of your bag. Trust it over your reading of the screenshot.
- Your memory: current floor, your goal, your recent actions with what followed, your notebook.

You answer with a plan: 1 to 5 actions played in order. If something changes on the way (an enemy, damage,
an item, a window, a message), the rest is dropped and you are told why: plan only what is safe now. One or
two actions is often enough. goto, examine, click and map must be the LAST action of a plan: you need to see
where you end up, or what you learn, before doing more.

Actions
- goto (target id): click a target like a player: the hero walks there by himself and stops if an enemy
  shows up. Clicking an enemy walks to it and attacks it; clicking the DOWN stairs takes them.
- examine (target id, or point_2d): look at a monster, item or tile with the magnifier; the game gives its
  name and description. Takes no game turn. Examine every new kind of monster (marked NOT EXAMINED YET)
  before fighting it: the description tells how it fights. What you learn goes to your notebook.
- attack: hit the enemy next to you (only when the red attack button is shown).
- use (item, verb, optional target): use an item of your bag. item = its name as listed in "Your bag",
  verb = one of its actions (DRINK, EAT, READ, EQUIP, THROW...). THROW needs a target id.
- move (steps): a few precise one-tile steps with the keyboard (up, down, left, right, up_left, up_right,
  down_left, down_right; at most 10), e.g. to step back into a doorway.
- wait: skip one turn. rest: let many turns pass, until healed or disturbed. loot: pick up the item under you.
- click (point_2d): press a button in an open window; [x, y] from 0 to 1000 on the screenshot.
- key (key): inventory, journal, hero_info, close (closes a window), quickslot_1..6.
- map: a zoomed-out view of the explored floor (useful only without a Game state block).

How the game works
- Health: the hero heals by himself, about 1 HP every 10 turns, unless he is starving. "rest" lets those
  turns pass: it is the safe way to heal, with no enemy in view. Dewdrops heal when picked up. One kind of
  potion heals at once, but potions start unidentified (just a color): drinking an unknown potion is a
  gamble, worth it when you are about to die.
- Food does not heal: it stops hunger. Eat when the log says you are hungry: a starving hero stops healing
  and loses HP.
- Fight one enemy at a time, in a corridor or a doorway, with enough HP. When HP is low, back off and rest.
  Throwing weapons (darts, shurikens, stones) hit from a distance.
- Pick up the items you see (goto them). Explore until you find the DOWN stairs: doors and edges of the
  explored area lead to new rooms.
- Log colors: orange = warning, red = danger, green = good news.

Answer
- "reasoning": one or two short sentences in the first person, like a player thinking aloud ("That rat is
  asleep: I grab the potion first, then I deal with it."). Shown to viewers.
- "goal": your current objective in a few words. Shown to you next call: keep it until done, then change it.
- "plan": 1 to 5 actions; in each, set the fields the action does not use to null. "label": what you click
  or examine with point_2d, else null.
- "note_add": a lasting fact worth remembering (where the stairs are, what an item does, a dangerous enemy),
  or null. "note_remove": the number of a note that is no longer true, or null."""

_POINT = {"type": "array", "items": {"type": "integer"}}
_DIRECTIONS = ["up", "down", "left", "right", "up_left", "up_right", "down_left", "down_right"]
_ACTION = {
    "type": "object",
    "properties": {
        "action": {"type": "string",
                   "enum": ["goto", "move", "click", "examine", "attack", "use", "loot", "key", "wait", "rest",
                            "resume", "map"]},
        "target": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "item": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "verb": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "steps": {"anyOf": [{"type": "array", "items": {"type": "string", "enum": _DIRECTIONS}}, {"type": "null"}]},
        "point_2d": {"anyOf": [_POINT, {"type": "null"}]},
        "key": {"anyOf": [{"type": "string", "enum": sorted(KEYS)}, {"type": "null"}]},
        "label": {"anyOf": [{"type": "string"}, {"type": "null"}]},
    },
    "required": ["action", "target", "item", "verb", "steps", "point_2d", "key", "label"],
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
    usable = ["goto (a target id)", "use (an item of your bag)"] if situation.get("game") else []
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
    target = item.get("target").strip().upper() if isinstance(item.get("target"), str) and item["target"].strip() else None
    if action == "goto" and target:
        return {"action": "goto", "target": target}
    if action == "examine" and target:
        return {"action": "examine", "target": target}
    if action == "use" and isinstance(item.get("item"), str) and isinstance(item.get("verb"), str):
        return {"action": "use", "item": item["item"], "verb": item["verb"].upper(), "target": target}
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

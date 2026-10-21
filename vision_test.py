"""Étape 7 : test de vision.

Envoie des captures de référence au modèle, lui demande de pointer (point_2d)
et de lire l'interface, dessine les points sur l'image, mesure l'erreur par
rapport à ground_truth.json, et note les tokens, le temps et le coût.

Exemples :
    python vision_test.py                                   # toutes les captures, 1024 et 768 px
    python vision_test.py images/screen_shot_game/SPD_in_fight.png --widths 768
    python vision_test.py --model mistral-medium-latest --widths 1024 768
    python vision_test.py --dry-run                         # prépare les images sans appeler l'API
"""

import argparse
import base64
import io
import json
import re
import time
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from llm import BUDGET_USD, PRICES, TOKENS_PER_MINUTE, complete, env_value, load_api_key, prices_for

ROOT = Path(__file__).parent
IMAGES_DIR = ROOT / "images" / "screen_shot_game"
GROUND_TRUTH = IMAGES_DIR / "ground_truth.json"
LOGS_DIR = ROOT / "logs" / "vision_test"


# Une case du jeu fait ~48 px dans une fenêtre de 1280 px de large
TILE_PX_AT_1280 = 48

PROMPT = """You are looking at a screenshot of the game Shattered Pixel Dungeon.

Coordinates: give every position as "point_2d": [x, y], normalized from 0 to 1000,
where [0, 0] is the top-left corner of the image and [1000, 1000] the bottom-right.
Point at the CENTER of the object.

What things look like:
- Staircase: a dark, roughly round stone pit in the floor with light grey steps
  going down diagonally inside it. It can be in a darker area (already explored,
  not currently in view): still report it. A floor has at most 2 staircases.
- Doors (brown wooden rectangle in an opening of a room wall) are NOT stairs.
- Items lying on the floor (potions, keys, scrolls, weapons...) are NOT enemies and
  NOT stairs: ignore them. Enemies are creatures (rats, crabs, snakes...).
- Attack button: red button at the bottom right showing the enemy portrait. It is
  only visible during combat.
- Pickup button: blue button on the right edge of the screen showing the icon of
  the item under the hero. It is only visible when the hero stands on an item.

Tasks:
1. POINT at: the hero, each visible enemy, each staircase, the attack button and
   the pickup button.
   For each candidate enemy or staircase, FIRST write in "seen" what is actually
   drawn at that spot, in a few words (e.g. "round dark pit with grey steps",
   "brown wooden door in a wall", "white key on the floor", "small brown rat").
   THEN set "is_creature" / "is_stairs" from that description. Candidates set to
   false are ignored, so it is fine to list doubtful things.
2. READ: the hero's health (HP bar at the bottom left, "current/max"), the last 3
   lines of the message log (yellow/white text above the HP bar, oldest first),
   the status icons (if any), and whether the inventory window is open.

Answer with ONE JSON object in this exact shape:
```json
{
  "hero": {"point_2d": [x, y]},
  "enemies": [{"seen": "...", "is_creature": true, "label": "rat", "point_2d": [x, y]}],
  "stairs": [{"seen": "...", "is_stairs": true, "point_2d": [x, y]}],
  "attack_button": {"point_2d": [x, y]},
  "pickup_button": {"point_2d": [x, y]},
  "hp": {"current": 0, "max": 0},
  "last_messages": ["...", "...", "..."],
  "status_icons": [],
  "inventory_open": false
}
```
Use null for attack_button / pickup_button when they are not visible and [] for empty lists."""

# Schéma strict : l'API impose la structure (pas de crochet oublié, pas de boucle)
_POINT = {"type": "array", "items": {"type": "integer"}}
_PLACE = {"type": "object", "properties": {"point_2d": _POINT},
          "required": ["point_2d"], "additionalProperties": False}
# "seen" vient avant le verdict : le modèle décrit ce qu'il voit, puis décide (vérification sans mode réflexion)
_ENEMY = {"type": "object",
          "properties": {"seen": {"type": "string"}, "is_creature": {"type": "boolean"},
                         "label": {"type": "string"}, "point_2d": _POINT},
          "required": ["seen", "is_creature", "label", "point_2d"], "additionalProperties": False}
_STAIRS = {"type": "object",
           "properties": {"seen": {"type": "string"}, "is_stairs": {"type": "boolean"}, "point_2d": _POINT},
           "required": ["seen", "is_stairs", "point_2d"], "additionalProperties": False}
_ANSWER_SCHEMA = {
    "type": "object",
    "properties": {
        "hero": _PLACE,
        "enemies": {"type": "array", "items": _ENEMY},
        "stairs": {"type": "array", "items": _STAIRS},
        "attack_button": {"anyOf": [_PLACE, {"type": "null"}]},
        "pickup_button": {"anyOf": [_PLACE, {"type": "null"}]},
        "hp": {"type": "object", "properties": {"current": {"type": "integer"}, "max": {"type": "integer"}},
               "required": ["current", "max"], "additionalProperties": False},
        "last_messages": {"type": "array", "items": {"type": "string"}},
        "status_icons": {"type": "array", "items": {"type": "string"}},
        "inventory_open": {"type": "boolean"},
    },
    "required": ["hero", "enemies", "stairs", "attack_button", "pickup_button", "hp",
                 "last_messages", "status_icons", "inventory_open"],
    "additionalProperties": False,
}
RESPONSE_FORMAT = {"type": "json_schema",
                   "json_schema": {"name": "screen_reading", "schema_definition": _ANSWER_SCHEMA, "strict": True}}


# --------------------------------------------------------------------------- #
# Utilitaires
# --------------------------------------------------------------------------- #

def resize(image, width):
    """Réduit l'image à `width` px de large en gardant les proportions."""
    if image.width <= width:
        return image.copy()
    height = round(image.height * width / image.width)
    return image.resize((width, height), Image.LANCZOS)


def to_data_url(image):
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()


def extract_json(text):
    """Extrait le bloc JSON de la réponse en texte libre."""
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidate = match.group(1) if match else None
    if candidate is None:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end == -1:
            return None
        candidate = text[start:end + 1]
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        return None


def call_model(client, model, content, max_tokens, reasoning=None):
    """Appel avec attente et nouvel essai en cas de 429 (limite de tokens/min)."""
    return complete(client, model, [{"role": "user", "content": content}], max_tokens,
                    response_format=RESPONSE_FORMAT, reasoning=reasoning)


# --------------------------------------------------------------------------- #
# Points : conversion, dessin et erreur
# --------------------------------------------------------------------------- #

def collect_points(answer):
    """Renvoie ([(nom, [x, y] normalisé)], [candidats écartés par le modèle]) à partir de sa réponse."""
    points, rejected = [], []

    def add(name, obj):
        if isinstance(obj, dict) and isinstance(obj.get("point_2d"), list) and len(obj["point_2d"]) == 2:
            points.append((name, obj["point_2d"]))

    def add_candidate(name, obj, flag):
        if isinstance(obj, dict) and obj.get(flag) is False:
            rejected.append(f"{name} ({obj.get('seen', '?')})")
        else:
            add(name, obj)

    add("hero", answer.get("hero"))
    for i, enemy in enumerate(answer.get("enemies") or []):
        add_candidate(f"enemy:{enemy.get('label', i)}", enemy, "is_creature")
    for stairs in answer.get("stairs") or []:
        add_candidate("stairs", stairs, "is_stairs")
    add("attack_button", answer.get("attack_button"))
    add("pickup_button", answer.get("pickup_button"))
    return points, rejected


def to_pixels(point, size, coords):
    x, y = point
    if coords == "norm":
        return x * size[0] / 1000, y * size[1] / 1000
    return x, y  # coordonnées déjà en pixels de l'image envoyée


def match_points(pixels, targets):
    """Associe chaque référence à un seul point (le plus proche d'abord) ; renvoie l'erreur par point, None si en trop."""
    pairs = sorted(
        (((px - tx) ** 2 + (py - ty) ** 2) ** 0.5, i, j)
        for i, (px, py) in enumerate(pixels) for j, (tx, ty) in enumerate(targets))
    errors, used = [None] * len(pixels), set()
    for error, i, j in pairs:
        if errors[i] is None and j not in used:
            errors[i] = error
            used.add(j)
    return errors


def score_points(points, truth, original_size, sent_size, coords):
    """Erreur de chaque point, en pixels de l'original et en cases (un point en trop = fausse alerte)."""
    tile = TILE_PX_AT_1280 * original_size[0] / 1280
    scale = original_size[0] / sent_size[0]
    targets = {
        "hero": [truth["hero"]] if truth.get("hero") else [],
        "enemy": truth.get("enemies", []),
        "stairs": truth.get("stairs", []),
        "attack_button": [truth["attack_button"]] if truth.get("attack_button") else [],
        "pickup_button": [truth["pickup_button"]] if truth.get("pickup_button") else [],
    }
    pixels = []
    for name, point in points:
        px, py = to_pixels(point, sent_size, coords)
        pixels.append((px * scale, py * scale))
    errors = [None] * len(points)
    for kind, kind_targets in targets.items():
        indexes = [i for i, (name, _) in enumerate(points) if name.split(":")[0] == kind]
        for i, error in zip(indexes, match_points([pixels[i] for i in indexes], kind_targets)):
            errors[i] = error
    results = []
    for (name, _), (px, py), error in zip(points, pixels, errors):
        results.append({
            "name": name,
            "pixel": [round(px), round(py)],
            "error_px": None if error is None else round(error, 1),
            "error_tiles": None if error is None else round(error / tile, 2),
            "hit": None if error is None else error <= tile / 2,
        })
    # Objets attendus mais non pointés (une entrée par référence sans point associé)
    matched = [r["name"].split(":")[0] for r in results if r["error_px"] is not None]
    missed = [k for k, v in targets.items() for _ in range(len(v) - matched.count(k))]
    false_alarms = [r["name"] for r in results if r["error_px"] is None]
    return results, missed, false_alarms


def draw(original, scored, truth, path):
    image = original.convert("RGB")
    canvas = ImageDraw.Draw(image)
    try:
        font = ImageFont.truetype("arial.ttf", 14)
    except OSError:
        font = ImageFont.load_default()

    # Références en vert (croix)
    if truth:
        refs = [truth.get("hero")] + truth.get("enemies", []) + truth.get("stairs", []) + [
            truth.get("attack_button"), truth.get("pickup_button")]
        for ref in filter(None, refs):
            x, y = ref
            canvas.line([x - 10, y, x + 10, y], fill="lime", width=2)
            canvas.line([x, y - 10, x, y + 10], fill="lime", width=2)

    # Réponses du modèle (cercle) : bleu = dans la case, rouge = raté, jaune = pas de référence
    for result in scored:
        x, y = result["pixel"]
        color = "yellow" if result["hit"] is None else ("cyan" if result["hit"] else "red")
        canvas.ellipse([x - 8, y - 8, x + 8, y + 8], outline=color, width=3)
        canvas.text((x + 11, y - 8), result["name"], fill=color, font=font, stroke_width=2, stroke_fill="black")
    image.save(path)


def compare_reading(answer, truth):
    if not truth:
        return {}
    hp = answer.get("hp") or {}
    expected = truth.get("last_messages", [])
    got = [m.strip() for m in (answer.get("last_messages") or []) if isinstance(m, str)]
    reading = {
        "hp_ok": [hp.get("current"), hp.get("max")] == truth.get("hp"),
        "messages_ok": sum(1 for m in expected if m in got),
        "messages_expected": len(expected),
    }
    if "inventory_open" in truth:
        reading["inventory_ok"] = answer.get("inventory_open") == truth["inventory_open"]
    return reading


# --------------------------------------------------------------------------- #
# Programme principal
# --------------------------------------------------------------------------- #

def main():
    parser = argparse.ArgumentParser(description="Test de vision (étape 7)")
    parser.add_argument("images", nargs="*", type=Path, help="captures (par défaut : toutes celles de ground_truth.json)")
    parser.add_argument("--model", default=env_value("CHONK_MODEL") or "mistral-medium-latest",
                        help="par défaut : CHONK_MODEL (environnement ou .env), sinon mistral-medium-latest")
    parser.add_argument("--widths", nargs="+", type=int, default=[1024, 768])
    parser.add_argument("--coords", choices=["norm", "pixels"], default="norm",
                        help="norm = 0-1000 (demandé dans le prompt), pixels = pixels de l'image envoyée")
    parser.add_argument("--max-tokens", type=int, default=800)
    parser.add_argument("--reasoning", choices=["none", "minimal", "low", "medium", "high", "xhigh"],
                        help="reasoning_effort des modèles qui raisonnent (par défaut : non envoyé)")
    parser.add_argument("--pause", type=float, default=15, help="pause entre deux appels (s), pour la limite de tokens/min")
    parser.add_argument("--dry-run", action="store_true", help="prépare et enregistre les images sans appeler l'API")
    args = parser.parse_args()

    truths = json.loads(GROUND_TRUTH.read_text(encoding="utf-8")) if GROUND_TRUTH.exists() else {}
    images = args.images or [IMAGES_DIR / name for name in truths if not name.startswith("_")]

    run_dir = LOGS_DIR / datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)
    print(f"Modèle : {args.model}  |  largeurs : {args.widths}  |  réflexion : {args.reasoning or 'défaut'}  |  dossier : {run_dir}\n")

    client = None
    if not args.dry_run:
        from mistralai.client import Mistral
        client = Mistral(api_key=load_api_key())

    price_in, price_out = prices_for(args.model)
    rows = []
    first_call = True

    for image_path in images:
        original = Image.open(image_path)
        truth = truths.get(image_path.name)
        for width in args.widths:
            sent = resize(original.convert("RGB"), width)
            tag = f"{image_path.stem}_{width}"
            sent.save(run_dir / f"{tag}_sent.png")
            print(f"-> {image_path.name} à {sent.width}x{sent.height}")
            if args.dry_run:
                continue

            if not first_call:
                time.sleep(args.pause)
            first_call = False

            content = [
                {"type": "text", "text": PROMPT},
                {"type": "image_url", "image_url": to_data_url(sent)},
            ]
            response, seconds = call_model(client, args.model, content, args.max_tokens, args.reasoning)
            text = response.choices[0].message.content
            thinking = ""
            if isinstance(text, list):  # certains modèles renvoient des morceaux (texte + réflexion)
                thinking = "".join(getattr(part, "text", "") or ""
                                   for chunk in text for part in (getattr(chunk, "thinking", None) or []))
                text = "".join(getattr(chunk, "text", "") or "" for chunk in text)
            text = text or ""
            usage = response.usage
            if response.choices[0].finish_reason == "length":
                print(f"   ATTENTION : réponse coupée à {args.max_tokens} tokens"
                      f" (réflexion : {len(thinking)} caractères) -> augmenter --max-tokens ou baisser --reasoning")
            cost = (usage.prompt_tokens * price_in + usage.completion_tokens * price_out) / 1e6

            answer = extract_json(text) or {}
            points, rejected = collect_points(answer)
            scored, missed, false_alarms = score_points(
                points, truth or {}, original.size, sent.size, args.coords)
            reading = compare_reading(answer, truth)
            draw(original, scored, truth, run_dir / f"{tag}_points.png")
            (run_dir / f"{tag}_response.txt").write_text(text, encoding="utf-8")
            if thinking:
                (run_dir / f"{tag}_thinking.txt").write_text(thinking, encoding="utf-8")

            hits = [r for r in scored if r["hit"] is not None]
            row = {
                "image": image_path.name,
                "width": sent.width,
                "height": sent.height,
                "prompt_tokens": usage.prompt_tokens,
                "completion_tokens": usage.completion_tokens,
                "seconds": round(seconds, 1),
                "cost_usd": round(cost, 5),
                "json_ok": bool(answer),
                "points": scored,
                "hits": sum(r["hit"] for r in hits),
                "scored_points": len(hits),
                "mean_error_tiles": round(sum(r["error_tiles"] for r in hits) / len(hits), 2) if hits else None,
                "missed": missed,
                "false_alarms": false_alarms,
                "rejected_by_model": rejected,
                "reading": reading,
                "answer": answer,
            }
            rows.append(row)
            print(f"   {usage.prompt_tokens} + {usage.completion_tokens} tokens, {seconds:.1f} s, {cost:.4f} $"
                  f" | points dans la bonne case : {row['hits']}/{row['scored_points']}"
                  f" (erreur moy. {row['mean_error_tiles']} case)"
                  f" | ratés : {missed or '-'} | fausses alertes : {false_alarms or '-'}"
                  f" | écartés par le modèle : {rejected or '-'}"
                  f" | lecture : {reading or '-'}")

    if not rows:
        print("\nAucun appel effectué.")
        return

    (run_dir / "results.json").write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
    print_summary(rows, args.model, run_dir)


def print_summary(rows, model, run_dir):
    lines = [f"# Test de vision : {model}", "",
             "| Largeur | Tokens entrée (moy.) | Tokens sortie (moy.) | Temps (moy.) | Coût/appel | Pointage OK | Erreur moy. (cases) | PV lus | Messages lus |",
             "|---|---|---|---|---|---|---|---|---|"]
    for width in sorted({r["width"] for r in rows}, reverse=True):
        group = [r for r in rows if r["width"] == width]
        n = len(group)
        tin = sum(r["prompt_tokens"] for r in group) / n
        tout = sum(r["completion_tokens"] for r in group) / n
        sec = sum(r["seconds"] for r in group) / n
        cost = sum(r["cost_usd"] for r in group) / n
        hits = sum(r["hits"] for r in group)
        scored = sum(r["scored_points"] for r in group)
        errors = [r["mean_error_tiles"] for r in group if r["mean_error_tiles"] is not None]
        hp = sum(1 for r in group if r["reading"].get("hp_ok"))
        msg_ok = sum(r["reading"].get("messages_ok", 0) for r in group)
        msg_exp = sum(r["reading"].get("messages_expected", 0) for r in group)
        per_minute = TOKENS_PER_MINUTE / (tin + tout)
        large_in, large_out = PRICES["mistral-large"]
        cost_large = (tin * large_in + tout * large_out) / 1e6
        lines.append(f"| {width} px | {tin:.0f} | {tout:.0f} | {sec:.1f} s | {cost:.4f} $ | {hits}/{scored} | "
                     f"{(sum(errors) / len(errors)) if errors else float('nan'):.2f} | {hp}/{n} | {msg_ok}/{msg_exp} |")
        lines.append(f"|  | → **{BUDGET_USD / cost:.0f} appels** pour {BUDGET_USD:.0f} $ | "
                     f"→ {per_minute:.1f} appels/min max | | | | | | |")
        lines.append(f"|  | Aux prix de Large 4 : {cost_large:.4f} $/appel | "
                     f"→ **{BUDGET_USD / cost_large:.0f} appels** pour {BUDGET_USD:.0f} $ | | | | | | |")
    total = sum(r["cost_usd"] for r in rows)
    lines += ["", f"Coût total de ce test : {total:.4f} $ ({len(rows)} appels)"]
    summary = "\n".join(lines)
    (run_dir / "summary.md").write_text(summary + "\n", encoding="utf-8")
    print("\n" + summary)
    print(f"\nImages annotées : {run_dir}  (croix verte = référence, cercle cyan = bonne case, rouge = raté, jaune = non attendu)")


if __name__ == "__main__":
    main()

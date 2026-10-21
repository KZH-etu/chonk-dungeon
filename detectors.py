"""Étape 9 : détecteurs Python (sans LLM).

Lisent une capture 1280x720 et en tirent l'état de l'interface : vie, boutons
rouge et bleu, ennemis en vue, fenêtre ouverte, inventaire, messages. La boucle
du harness s'en sert pour n'appeler le modèle que quand quelque chose change.

Positions et couleurs mesurées sur de vraies captures (étape 8). Les captures de
référence, recadrées à la main, sont recalées sur la barre de vie (hud_offset).

Vérification sur les captures de référence (sans appel API) :
    python detectors.py --check
Lecture d'une capture :
    python detectors.py chemin/capture.png
"""

import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image

from ocr import HP_BAR_LEFT, HP_BAR_TOP, Reader, hud_offset

ROOT = Path(__file__).parent
GROUND_TRUTH = ROOT / "images" / "screen_shot_game" / "ground_truth.json"

HP_BAR_RIGHT = 470            # bord droit de la barre pleine
HP_ROW = HP_BAR_TOP + 25      # ligne sous le texte « 20/20 »
RIGHT_COLUMN = 12             # colonne lue à 12 px du bord droit (boutons rouge et bleu)
BUTTON_MIN_HEIGHT = 30
DANGER_ZONE = (60, 120)       # compteur d'ennemis (crâne) sous le bouton du menu
WINDOW_COLOR = (160, 166, 149)  # bord clair des fenêtres du jeu
WINDOW_ZONE = (60, 390, 120, 1160)  # zone (y0, y1, x0, x1) où ne passe aucun élément fixe de l'interface
WINDOW_MIN_PIXELS = 600       # fenêtre ouverte : 2 000 à 4 000 px de bord ; sans fenêtre : 0 à 210
CHROME_COLORS = [(123, 128, 115), (160, 166, 149)]
INVENTORY_BORDER_Y = 475      # bord haut du panneau d'inventaire ouvert (fermé : y = 649)
GAME_OVER_COLOR = (131, 141, 110)   # pierre du bandeau « Game Over » (absente de toutes les autres captures)
GAME_OVER_ZONE = (220, 310, 440, 840)
GAME_OVER_MIN_PIXELS = 500          # écran de mort : ~1 600 px


def _near(rgb, color, tolerance):
    return np.abs(rgb.astype(int) - color).sum(axis=-1) <= tolerance


def _red(rgb):
    rgb = rgb.astype(int)
    return (rgb[..., 0] > 120) & (rgb[..., 1] < 70) & (rgb[..., 2] < 70)


def _blue(rgb):
    rgb = rgb.astype(int)
    return (rgb[..., 2] > 60) & (rgb[..., 2] > rgb[..., 0] + 40)


def _runs(flags, max_gap=15):
    """Plages (début, fin) de True, en fusionnant les trous de moins de `max_gap` (icône dans le bouton)."""
    runs = []
    for y in np.where(flags)[0]:
        if runs and y - runs[-1][1] <= max_gap:
            runs[-1][1] = y
        else:
            runs.append([y, y])
    return [(a, b) for a, b in runs]


# --------------------------------------------------------------------------- #
# Détecteurs
# --------------------------------------------------------------------------- #

def hp_ratio(rgb, offset):
    """Part de la barre de vie encore rouge (0 à 1)."""
    dx, dy = offset
    row = rgb[HP_ROW + dy, HP_BAR_LEFT + dx:HP_BAR_RIGHT + dx + 1]
    red = np.where((row[:, 0].astype(int) > 80) & (row[:, 0].astype(int) > 2 * row[:, 1].astype(int)))[0]
    if len(red) == 0:
        return 0.0
    return round((red.max() + 1) / (HP_BAR_RIGHT - HP_BAR_LEFT + 1), 3)


def right_buttons(rgb):
    """Boutons du bord droit : attaque (rouge), ramassage (bleu), compteur d'ennemis (crâne rouge)."""
    height, width = rgb.shape[:2]
    column = rgb[:, width - RIGHT_COLUMN]
    found = {"attack_button": None, "pickup_button": None, "enemies_visible": False}

    def center(run):
        return [round((width - 35) * 1000 / width), round((run[0] + run[1]) / 2 * 1000 / height)]

    for run in _runs(_red(column)):
        if run[0] < DANGER_ZONE[1] and run[1] > DANGER_ZONE[0]:
            found["enemies_visible"] = True
        elif run[0] >= DANGER_ZONE[1] and run[1] - run[0] >= BUTTON_MIN_HEIGHT:
            found["attack_button"] = center(run)
    for run in _runs(_blue(column)):
        if run[1] - run[0] >= BUTTON_MIN_HEIGHT:
            found["pickup_button"] = center(run)
    return found


def window_open(rgb, offset):
    dx, dy = offset
    y0, y1, x0, x1 = WINDOW_ZONE
    zone = rgb[y0 + dy:y1 + dy, x0 + dx:x1 + dx]
    return int(_near(zone, WINDOW_COLOR, 6).sum()) >= WINDOW_MIN_PIXELS


def inventory_open(rgb, offset):
    dx, dy = offset
    band = rgb[INVENTORY_BORDER_Y + dy - 3:INVENTORY_BORDER_Y + dy + 4, 730 + dx:1270 + dx]
    chrome = np.zeros(band.shape[:2], bool)
    for color in CHROME_COLORS:
        chrome |= _near(band, color, 6)
    return bool((chrome.sum(axis=1) > 400).any())


def dead(rgb, offset, hp):
    """Mort : vie à 0, ou bandeau « Game Over » (au cas où la barre serait mal lue)."""
    dx, dy = offset
    y0, y1, x0, x1 = GAME_OVER_ZONE
    banner = int(_near(rgb[y0 + dy:y1 + dy, x0 + dx:x1 + dx], GAME_OVER_COLOR, 6).sum()) >= GAME_OVER_MIN_PIXELS
    return hp == 0 or banner


def read_screen(image, reader=None):
    """État de l'interface lu sur une capture (image PIL ou tableau RGB)."""
    rgb = np.asarray(image.convert("RGB")) if isinstance(image, Image.Image) else image
    reader = reader or Reader()
    offset = hud_offset(rgb)
    state = {"hp_ratio": hp_ratio(rgb, offset)}
    state.update(right_buttons(rgb))
    state["window_open"] = window_open(rgb, offset)
    state["inventory_open"] = inventory_open(rgb, offset)
    state["dead"] = dead(rgb, offset, state["hp_ratio"])
    state["messages"] = reader.read_log(rgb)
    state["messages_unreadable"] = any("?" in line["text"] for line in state["messages"])
    return state


def changes(before, after):
    """Ce qui a changé entre deux états : liste de raisons lisibles (pour dire au modèle pourquoi on l'appelle)."""
    reasons = []
    if before["hp_ratio"] - after["hp_ratio"] >= 0.01:
        reasons.append(f"vie : {before['hp_ratio']:.0%} -> {after['hp_ratio']:.0%}")
    for key, label in [("attack_button", "bouton d'attaque"), ("pickup_button", "bouton de ramassage")]:
        if bool(before[key]) != bool(after[key]):
            reasons.append(f"{label} {'apparu' if after[key] else 'disparu'}")
    if before["enemies_visible"] != after["enemies_visible"]:
        reasons.append("ennemi en vue" if after["enemies_visible"] else "plus d'ennemi en vue")
    if after["dead"] and not before["dead"]:
        reasons.append("mort")
    if before["window_open"] != after["window_open"]:
        reasons.append("fenêtre ouverte" if after["window_open"] else "fenêtre fermée")
    old = [line["text"] for line in before["messages"]]
    new = [line["text"] for line in after["messages"]]
    added = new_messages(old, new)
    if added:
        reasons.append("nouveaux messages : " + " | ".join(added))
    return reasons


def new_messages(old, new):
    """Lignes apparues en bas du journal (le journal défile vers le haut)."""
    for keep in range(min(len(old), len(new)), 0, -1):
        if old[len(old) - keep:] == new[:keep]:
            return new[keep:]
    return new if new != old else []


# --------------------------------------------------------------------------- #
# Vérification
# --------------------------------------------------------------------------- #

def check():
    truths = json.loads(GROUND_TRUTH.read_text(encoding="utf-8"))
    reader = Reader()
    totals = {}

    def score(name, ok):
        good, count = totals.get(name, (0, 0))
        totals[name] = (good + bool(ok), count + 1)
        return "ok" if ok else "FAUX"

    for name, truth in truths.items():
        if name.startswith("_"):
            continue
        state = read_screen(Image.open(GROUND_TRUTH.parent / name), reader)
        hp = truth["hp"][0] / truth["hp"][1]
        texts = [line["text"] for line in state["messages"]]
        print(name)
        print(f"   vie {state['hp_ratio']:.2f} (attendu {hp:.2f}) {score('vie', abs(state['hp_ratio'] - hp) < 0.03)}"
              f" | attaque {bool(state['attack_button'])} {score('attaque', bool(state['attack_button']) == bool(truth['attack_button']))}"
              f" | ramassage {bool(state['pickup_button'])} {score('ramassage', bool(state['pickup_button']) == bool(truth['pickup_button']))}"
              f" | inventaire {state['inventory_open']} {score('inventaire', state['inventory_open'] == truth['inventory_open'])}"
              f" | fenêtre {state['window_open']} {score('fenêtre', not state['window_open'])}"
              f" | mort {state['dead']} {score('mort', not state['dead'])}"
              f" | ennemis en vue {state['enemies_visible']}")
        expected = truth["last_messages"]
        got = texts[-len(expected):]
        print(f"   messages {score('messages', got == expected)} : {got}")
    print("\nTotal : " + " | ".join(f"{name} {good}/{count}" for name, (good, count) in totals.items()))


def main():
    parser = argparse.ArgumentParser(description="Détecteurs (étape 9)")
    parser.add_argument("images", nargs="*", type=Path)
    parser.add_argument("--check", action="store_true", help="compare aux captures de référence (ground_truth.json)")
    args = parser.parse_args()
    if args.check:
        check()
    reader = Reader()
    for path in args.images:
        print(path)
        for key, value in read_screen(Image.open(path), reader).items():
            print(f"   {key}: {value}")


if __name__ == "__main__":
    main()

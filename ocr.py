"""Étape 9 : lecture du journal des messages, sans LLM.

Le jeu dessine ses messages avec une police pixel, toujours identique : on
découpe chaque ligne en lettres et on compare chaque lettre à une bibliothèque
de lettres apprises sur de vraies captures (ocr_glyphs.json).

La police installée avec le jeu (pixel_font.ttf) ne suffit pas : le moteur du
jeu la dessine avec son propre lissage, différent de celui de Python.

Apprentissage (à refaire quand on ajoute des captures dans OCR_SAMPLES) :
    python ocr.py --learn
Lecture d'une capture :
    python ocr.py chemin/capture.png
"""

import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).parent
GLYPHS_FILE = ROOT / "ocr_glyphs.json"
UNKNOWN = "?"

# Repère : coin haut gauche de la barre de vie sur une capture 1280x720
HP_BAR_LEFT, HP_BAR_TOP = 90, 660
# Journal : 6 lignes, haut des majuscules à y = 484, 504, ..., 584 (la plus ancienne en haut)
LOG_LINE_TOPS = [484 + 20 * i for i in range(6)]
LOG_WIDTH = 720
LINE_ABOVE, LINE_BELOW = 1, 18  # bande d'une ligne : majuscules + jambages (p, g, y)
CORE_RATIO = 0.88                # cœur des lettres : >= 88 % du pixel le plus lumineux de la ligne
MIN_TEXT_LEVEL = 235             # ligne sans pixel aussi lumineux : vide (ou message presque effacé)
MAP_LEVEL = 225                  # le décor monte jusqu'à 221 (haut des murs) : jamais pris pour du texte
TINY_INK = 8                     # signes minuscules (. : ' ,) : reconnus à leur position plutôt qu'à leur forme
SPACE_GAP = 6                    # écart (px) à partir duquel deux lettres sont séparées par une espace
MAX_DISTANCE = 0.25              # écart toléré avec une lettre connue (part des pixels)

# Couleurs du texte (canal dominant à 255) -> sens dans le jeu
COLORS = {"white": (255, 255, 255), "yellow": (255, 255, 0), "orange": (255, 136, 0), "red": (255, 0, 0),
          "green": (0, 255, 0)}


# --------------------------------------------------------------------------- #
# Repérage et découpage
# --------------------------------------------------------------------------- #

def hud_offset(rgb):
    """Décalage (dx, dy) de l'interface par rapport à une capture 1280x720 (0, 0 pour le harness).

    Utile pour les captures de référence recadrées à la main : on se cale sur la barre de vie.
    """
    height = rgb.shape[0]
    zone = rgb[height - 80:height, 0:200].astype(int)
    red = (zone[..., 0] > 80) & (zone[..., 0] > 2 * zone[..., 1]) & (zone[..., 0] > 1.5 * zone[..., 2])
    rows = np.where(red.sum(1) > 50)[0]
    if len(rows) == 0:
        return 0, 0
    top = rows.min()
    left = np.where(red[top + 8])[0].min()
    return int(left - HP_BAR_LEFT), int(top + height - 80 - HP_BAR_TOP)


def core_mask(rgb):
    """Pixels du cœur des lettres. Seuil relatif : les vieux messages s'estompent peu à peu."""
    level = rgb.max(axis=2)
    top = level.max() if level.size else 0
    if top < MIN_TEXT_LEVEL:
        return np.zeros(level.shape, bool)
    return level >= max(CORE_RATIO * top, MAP_LEVEL)


def segment(mask):
    """Découpe une bande de texte en lettres : liste de (x_début, x_fin), séparées par des colonnes vides."""
    columns = mask.any(axis=0)
    runs, x = [], 0
    while x < len(columns):
        if columns[x]:
            start = x
            while x < len(columns) and columns[x]:
                x += 1
            runs.append((start, x))
        else:
            x += 1
    return runs


def log_bands(rgb):
    """Les 6 bandes du journal (image RGB de chaque ligne), de la plus ancienne à la plus récente."""
    dx, dy = hud_offset(rgb)
    bands = []
    for top in LOG_LINE_TOPS:
        y0, y1 = top + dy - LINE_ABOVE, top + dy + LINE_BELOW
        x0 = max(dx, 0)
        bands.append(rgb[y0:y1, x0:x0 + LOG_WIDTH])
    return bands


def line_color(band, mask):
    pixels = band[mask].astype(int)
    if len(pixels) == 0:
        return None
    mean = pixels.mean(axis=0)
    return min(COLORS, key=lambda name: np.abs(mean - COLORS[name]).sum())


# --------------------------------------------------------------------------- #
# Reconnaissance
# --------------------------------------------------------------------------- #

def distance(a, b):
    """Part des pixels différents entre deux lettres (même hauteur), meilleur décalage de -1 à +1 px."""
    if abs(a.shape[1] - b.shape[1]) > 1:
        return 1.0
    width = max(a.shape[1], b.shape[1]) + 2
    best = 1.0
    pa = np.zeros((a.shape[0], width), bool)
    pa[:, 1:1 + a.shape[1]] = a
    for shift in (0, 1, 2):
        pb = np.zeros((b.shape[0], width), bool)
        if shift + b.shape[1] > width:
            continue
        pb[:, shift:shift + b.shape[1]] = b
        ink = max(pa.sum(), pb.sum(), 1)
        best = min(best, (pa ^ pb).sum() / ink)
    return best


class Reader:
    def __init__(self, glyphs_file=GLYPHS_FILE):
        self.glyphs = []  # (lettre, masque)
        self._cache = {}
        if glyphs_file.exists():
            for char, rows in json.loads(glyphs_file.read_text(encoding="utf-8")):
                self.glyphs.append((char, np.array([[c == "#" for c in row] for row in rows])))

    def read_glyph(self, glyph):
        # Les mêmes lettres reviennent d'une capture à l'autre : on garde le résultat
        key = (glyph.shape, glyph.tobytes())
        if key not in self._cache:
            self._cache[key] = self._match(glyph)
        return self._cache[key]

    def _match(self, glyph):
        if glyph.sum() <= TINY_INK:
            return self._read_tiny(glyph)
        best_char, best = UNKNOWN, MAX_DISTANCE
        for char, known in self.glyphs:
            d = distance(glyph, known)
            if d < best:
                best_char, best = char, d
        return best_char

    def _read_tiny(self, glyph):
        """Un point ou une apostrophe : le signe connu dont la hauteur (haut, bas) est la plus proche."""
        def rows(mask):
            ys = np.where(mask.any(axis=1))[0]
            return ys.min(), ys.max()
        top, bottom = rows(glyph)
        tiny = [(char, rows(known)) for char, known in self.glyphs if known.sum() <= TINY_INK * 2]
        if not tiny:
            return UNKNOWN
        char, (t, b) = min(tiny, key=lambda item: abs(item[1][0] - top) + abs(item[1][1] - bottom))
        return char if abs(t - top) + abs(b - bottom) <= 4 else UNKNOWN

    def read_band(self, band):
        """Texte d'une bande (chaîne vide si la ligne est vide)."""
        mask = core_mask(band)
        text, previous_end = "", None
        for start, end in segment(mask):
            if previous_end is not None and start - previous_end >= SPACE_GAP:
                text += " "
            text += self.read_glyph(mask[:, start:end])
            previous_end = end
        return text

    def read_log(self, rgb):
        """Lignes visibles du journal : [{"text", "color"}], la plus ancienne en premier.

        Une ligne qui ne finit pas par . ! ? est la suite coupée d'un long message : on la recolle.
        """
        lines = []
        for band in log_bands(rgb):
            mask = core_mask(band)
            text = self.read_band(band)
            if not text:
                continue
            if lines and lines[-1]["text"][-1:] not in ".!?" and UNKNOWN not in lines[-1]["text"][-1:]:
                lines[-1]["text"] += " " + text
            else:
                lines.append({"text": text, "color": line_color(band, mask)})
        return lines


# --------------------------------------------------------------------------- #
# Apprentissage à partir de captures dont on connaît le texte
# --------------------------------------------------------------------------- #

# Captures et texte exact de leurs lignes de journal, de la plus ancienne à la plus récente
# (seulement les lignes visibles ; None = ligne à ignorer)
OCR_SAMPLES = {
    "images/ocr/status_healing.png": [
        "You defeated marsupial rat.", "You defeated sewer snake.", "You picked up: gold.",
        "You picked up: silver potion.", "Your wounds begin to close.", "You picked up: indigo potion."],
    "images/ocr/death_game_over.png": [
        "You defeated marsupial rat.", "You picked up: scroll of KAUNAN.", "You are hungry.",
        "That food tasted delicious!", "You defeated marsupial rat.", "Marsupial rat killed you..."],
    "images/ocr/log_hungry_scroll.png": [
        "You return to floor 1 of the dungeon.", "You defeated sewer snake.", "You defeated marsupial rat.",
        "You picked up: scroll of KAUNAN.", "You are hungry.", "That food tasted delicious!"],
    "images/screen_shot_game/SPD_scene_with_trap_treasure.png": [
        "You collect the dewdrop into your waterskin.", "You don't have a matching key.",
        "You don't have a matching key.", "You defeated marsupial rat.",
        "You picked up: potion of healing.", "You defeated sewer snake."],
    "images/screen_shot_game/SPD_scene_with_two_enemies_object_trap_and_staircase_descending.png": [
        "You don't have a matching key.", "You don't have a matching key.", "You defeated marsupial rat.",
        "You picked up: potion of healing.", "You defeated sewer snake.", "You picked up: gold."],
    "images/screen_shot_game/SPD_in_fight.png": ["You descend to floor 1 of the dungeon."],
}


def learn(samples=OCR_SAMPLES):
    """Associe chaque lettre découpée à son caractère, quand le nombre de lettres correspond."""
    glyphs, skipped = {}, []
    for path, texts in samples.items():
        rgb = np.asarray(Image.open(ROOT / path).convert("RGB"))
        bands = [b for b in log_bands(rgb) if core_mask(b).any()]
        if len(bands) != len(texts):
            skipped.append(f"{path} : {len(bands)} lignes vues, {len(texts)} attendues")
            continue
        for band, text in zip(bands, texts):
            if text is None:
                continue
            mask = core_mask(band)
            runs = segment(mask)
            chars = text.replace(" ", "")
            if len(runs) != len(chars):
                skipped.append(f"{path} « {text} » : {len(runs)} lettres découpées pour {len(chars)}")
                continue
            for (start, end), char in zip(runs, chars):
                rows = ["".join("#" if v else "." for v in row) for row in mask[:, start:end]]
                glyphs.setdefault((char, tuple(rows)), 0)
                glyphs[(char, tuple(rows))] += 1
    data = [[char, list(rows)] for (char, rows) in sorted(glyphs)]
    GLYPHS_FILE.write_text(json.dumps(data, indent=0, ensure_ascii=False), encoding="utf-8")
    chars = sorted({c for c, _ in glyphs})
    print(f"{len(data)} formes de lettres pour {len(chars)} caractères : {''.join(chars)}")
    for line in skipped:
        print("   ignoré :", line)


def main():
    parser = argparse.ArgumentParser(description="Lecture du journal (étape 9)")
    parser.add_argument("images", nargs="*", type=Path)
    parser.add_argument("--learn", action="store_true", help="reconstruit ocr_glyphs.json depuis OCR_SAMPLES")
    args = parser.parse_args()
    if args.learn:
        learn()
    reader = Reader()
    for path in args.images:
        rgb = np.asarray(Image.open(path).convert("RGB"))
        print(path)
        for line in reader.read_log(rgb):
            print(f"   [{line['color']}] {line['text']}")


if __name__ == "__main__":
    main()

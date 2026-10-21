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
LOG_END_GAP = 16                 # un blanc plus large termine une ligne du journal (ce qui suit est une fenêtre)
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


def cut_at_gap(mask, gap=LOG_END_GAP):
    """Efface tout ce qui suit le premier blanc de `gap` px : la ligne s'arrête là."""
    runs = segment(mask)
    for (_, end), (start, _) in zip(runs, runs[1:]):
        if start - end >= gap:
            mask = mask.copy()
            mask[:, end:] = False
            break
    return mask


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


def letters(mask):
    """Lettres d'une ligne : [(x_début, x_fin, forme, décalage)], la forme recadrée au plus près.

    Le décalage est la position du bas de la lettre par rapport à la ligne de base
    (0 pour « a », positif pour les jambages de « p », négatif pour l'apostrophe) :
    il distingue les lettres de même forme mais pas à la même hauteur (« , » et « ' »).
    """
    boxes = []
    for start, end in segment(mask):
        rows = np.where(mask[:, start:end].any(axis=1))[0]
        boxes.append((start, end, mask[rows.min():rows.max() + 1, start:end], int(rows.max())))
    tall = [bottom for _, _, shape, bottom in boxes if shape.shape[0] >= 4]
    baseline = max(set(tall), key=tall.count) if tall else 0
    return [(start, end, shape, bottom - baseline) for start, end, shape, bottom in boxes]


# --------------------------------------------------------------------------- #
# Reconnaissance
# --------------------------------------------------------------------------- #

def distance(a, b):
    """Part des pixels différents entre deux lettres (forme, décalage), meilleur recalage de ±1 px."""
    (sa, oa), (sb, ob) = a, b
    if abs(oa - ob) > 1 or abs(sa.shape[0] - sb.shape[0]) > 1 or abs(sa.shape[1] - sb.shape[1]) > 1:
        return 1.0
    height, width = max(sa.shape[0], sb.shape[0]) + 2, max(sa.shape[1], sb.shape[1]) + 2

    def place(shape, dy, dx):  # aligné sur le bas, comme sur la ligne de base
        canvas = np.zeros((height, width), bool)
        top = height - 1 - shape.shape[0] + dy
        if top < 0 or top + shape.shape[0] > height or dx + shape.shape[1] > width:
            return None
        canvas[top:top + shape.shape[0], dx:dx + shape.shape[1]] = shape
        return canvas

    pa = place(sa, oa - ob, 1) if oa >= ob else place(sa, 0, 1)
    ink = max(sa.sum(), sb.sum(), 1)
    best = 1.0
    for dy in (-1, 0, 1):
        for dx in (0, 1, 2):
            pb = place(sb, (ob - oa if ob > oa else 0) + dy, dx)
            if pa is not None and pb is not None:
                best = min(best, (pa ^ pb).sum() / ink)
    return best


def text_rows(mask):
    """Lignes d'un bloc de texte (fenêtre) : plages de rangées contenant de l'encre.

    Deux plages à 2 px ou moins l'une de l'autre forment une seule ligne (le point du « i »).
    """
    rows = mask.any(axis=1)
    groups, y = [], 0
    while y < len(rows):
        if rows[y]:
            start = y
            while y < len(rows) and rows[y]:
                y += 1
            if groups and start - groups[-1][1] <= 2:
                groups[-1] = (groups[-1][0], y)
            else:
                groups.append((start, y))
        else:
            y += 1
    return groups


def strip_icons(line):
    """Retire les « mots » faits seulement de ? en début et fin de ligne : ce sont des icônes
    (portrait, bouton, objet). Un ? au milieu du texte reste : c'est une lettre inconnue."""
    words = line.split(" ")
    while words and set(words[0]) <= {UNKNOWN}:
        words.pop(0)
    while words and set(words[-1]) <= {UNKNOWN}:
        words.pop()
    return " ".join(words)


def window_text_mask(rgb):
    """Texte d'une fenêtre : fond sombre uni, lettres blanches ou colorées à pleine intensité."""
    return rgb.max(axis=2) >= MAP_LEVEL


class Reader:
    def __init__(self, glyphs_file=GLYPHS_FILE):
        self.glyphs = []  # (lettre, (forme, décalage))
        self._cache = {}
        if glyphs_file.exists():
            for char, offset, rows in json.loads(glyphs_file.read_text(encoding="utf-8")):
                shape = np.array([[c == "#" for c in row] for row in rows])
                self.glyphs.append((char, (shape, offset)))

    def read_glyph(self, shape, offset):
        # Les mêmes lettres reviennent d'une capture à l'autre : on garde le résultat
        key = (shape.shape, shape.tobytes(), offset)
        if key not in self._cache:
            self._cache[key] = self._match(shape, offset)
        return self._cache[key]

    def _match(self, shape, offset):
        if shape.sum() <= TINY_INK:
            return self._read_tiny(shape, offset)
        best_char, best = UNKNOWN, MAX_DISTANCE
        for char, known in self.glyphs:
            d = distance((shape, offset), known)
            if d < best:
                best_char, best = char, d
        return best_char

    def _read_tiny(self, shape, offset):
        """Un point ou une apostrophe : le signe connu dont la hauteur (haut, bas) est la plus proche."""
        top, bottom = offset - shape.shape[0] + 1, offset
        tiny = [(char, (o - s.shape[0] + 1, o)) for char, (s, o) in self.glyphs if s.sum() <= TINY_INK * 2]
        if not tiny:
            return UNKNOWN
        char, (t, b) = min(tiny, key=lambda item: abs(item[1][0] - top) + abs(item[1][1] - bottom))
        return char if abs(t - top) + abs(b - bottom) <= 3 else UNKNOWN

    def read_mask(self, mask):
        """Texte d'une ligne déjà binarisée (chaîne vide si la ligne est vide)."""
        text, previous_end = "", None
        for start, end, shape, offset in letters(mask):
            if previous_end is not None and start - previous_end >= SPACE_GAP:
                text += " "
            text += self.read_glyph(shape, offset)
            previous_end = end
        return text

    def read_band(self, band):
        return self.read_mask(core_mask(band))

    def read_log(self, rgb):
        """Lignes visibles du journal : [{"text", "color"}], la plus ancienne en premier.

        Une ligne qui ne finit pas par . ! ? est la suite coupée d'un long message : on la recolle.
        """
        lines = []
        for band in log_bands(rgb):
            mask = cut_at_gap(core_mask(band))
            text = self.read_mask(mask)
            if not text:
                continue
            if lines and lines[-1]["text"][-1:] not in ".!?" and UNKNOWN not in lines[-1]["text"][-1:]:
                lines[-1]["text"] += " " + text
            else:
                lines.append({"text": text, "color": line_color(band, mask)})
        return lines

    def read_window(self, image):
        """Texte d'une fenêtre (image recadrée sur la fenêtre), une ligne de texte par ligne."""
        rgb = np.asarray(image.convert("RGB")) if isinstance(image, Image.Image) else image
        mask = window_text_mask(rgb)
        lines = [strip_icons(self.read_mask(mask[y0:y1])) for y0, y1 in text_rows(mask)]
        return "\n".join(line for line in lines if line)


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


# Fenêtres (images recadrées sur la fenêtre, ex. résultats d'« examiner ») et texte de chaque ligne
WINDOW_SAMPLES = {
    "images/ocr/windows/journal_notes.png": [
        "Adventuring Notes +",
        "As you journey through the dungeon, you will automatically record noteworthy",
        "things here.",
        "Floor 1"],
    # Zones de texte découpées dans les captures images/screen_shot_game/SPD_hero_info_* et SPD_journal_*
    "images/ocr/windows/hero_title.png": ["LEVEL 1 DUELIST"],
    "images/ocr/windows/hero_stats.png": [
        "Strength 10", "Health 20/20", "Experience 8/10", "Gold Collected 126", "Maximum Depth 1",
        "Dungeon Seed RYB-YCP-RKQ"],
    "images/ocr/windows/talents_tier.png": ["Tier 1"],
    "images/ocr/windows/talents_reach.png": ["Reach level 6 to unlock more talents."],
    "images/ocr/windows/catalog_equipment.png": ["Equipment (11/171)"],
    "images/ocr/windows/catalog_melee.png": ["Melee Weapons (3/33):"],
    "images/ocr/windows/catalog_armor.png": ["Armor (2/11):"],
    "images/ocr/windows/catalog_enchant.png": ["Enchantments and Curses (0/27):"],
    "images/ocr/windows/item_title.png": ["Quarterstaff"],
    "images/ocr/windows/item_body.png": [
        "A staff of hardwood, its ends are shod with", "iron.",
        "Typically this tier-2 melee weapon deals 2-12", "damage and requires 12 strength to use",
        "properly. Probably this weapon is too heavy", "for you.", "This weapon blocks 0-2 damage.",
        "The Duelist can assume a defensive stance", "with a quarterstaff. This stance takes no",
        "time to activate and triples her evasion", "typically for 4 turns."],
    "images/ocr/windows/guide_title.png": ["Tome of Dungeon Mastery"],
    "images/ocr/windows/examine_stairs_up.png": ["Depth entrance", "Stairs lead up to the upper depth."],
}


def _learn_line(mask, text, glyphs, skipped, label):
    found = letters(mask)
    chars = text.replace(" ", "")
    if len(found) != len(chars):
        skipped.append(f"{label} « {text} » : {len(found)} lettres découpées pour {len(chars)}")
        return
    for (_, _, shape, offset), char in zip(found, chars):
        rows = tuple("".join("#" if v else "." for v in row) for row in shape)
        glyphs.add((char, offset, rows))


def learn():
    """Associe chaque lettre découpée à son caractère, quand le nombre de lettres correspond."""
    glyphs, skipped = set(), []
    for path, texts in OCR_SAMPLES.items():
        rgb = np.asarray(Image.open(ROOT / path).convert("RGB"))
        bands = [b for b in log_bands(rgb) if core_mask(b).any()]
        if len(bands) != len(texts):
            skipped.append(f"{path} : {len(bands)} lignes vues, {len(texts)} attendues")
            continue
        for band, text in zip(bands, texts):
            if text is not None:
                _learn_line(cut_at_gap(core_mask(band)), text, glyphs, skipped, path)
    for path, texts in WINDOW_SAMPLES.items():
        mask = window_text_mask(np.asarray(Image.open(ROOT / path).convert("RGB")))
        rows = text_rows(mask)
        if len(rows) != len(texts):
            skipped.append(f"{path} : {len(rows)} lignes vues, {len(texts)} attendues")
            continue
        for (y0, y1), text in zip(rows, texts):
            if text is not None:
                _learn_line(mask[y0:y1], text, glyphs, skipped, path)
    data = [[char, offset, list(rows)] for char, offset, rows in sorted(glyphs)]
    GLYPHS_FILE.write_text(json.dumps(data, indent=0, ensure_ascii=False), encoding="utf-8")
    chars = sorted({c for c, _, _ in glyphs})
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

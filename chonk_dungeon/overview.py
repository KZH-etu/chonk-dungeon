"""Vue d'ensemble de l'étage : la carte de ce qui a été exploré.

Le modèle ne voit qu'un écran à la fois et oublie ce qu'il a déjà exploré (partie 1 :
25 tours coincé dans un cul-de-sac). Le jeu n'a pas de minicarte, mais sa vue se
dézoome (touche « - ») : au zoom minimal, tout l'étage exploré tient à l'écran.

On recadre cette capture sur la partie explorée (le reste est noir), on l'agrandit à la
taille d'une capture normale et on entoure le héros (toujours au centre de l'écran).
Le modèle peut cliquer sur cette carte pour traverser l'étage en un clic : le harness
convertit le point en position écran, clique tant que la vue est dézoomée, puis rezoome.

Coût : une image de la taille d'une capture normale, seulement quand la carte sert.

Test sur une capture dézoomée (sans jeu ni API) :
    python -m chonk_dungeon.overview capture.png      # écrit capture-map.png à côté
"""

import argparse
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from .ocr import LOG_LINE_TOPS, LOG_WIDTH, core_mask, cut_at_gap

ZOOM_OUT_KEY, ZOOM_IN_KEY = "minus", "equals"
ZOOM_STEPS = 2            # du zoom normal au zoom minimal (vérifié en jeu : 5 cycles sur 5)
LIT_LEVEL = 25            # au-dessus : case explorée ; les zones inexplorées sont noires (0)
MAP_PAD = 24              # marge autour de la partie explorée (px de la capture)
MAX_AREA = 768 * 432      # pas plus de pixels qu'une capture normale envoyée au modèle
MAX_SIDE = 768
HERO = (640, 360)         # la caméra suit le héros : centre de la zone de jeu
HERO_COLOR = (255, 230, 0)
# Interface (fixe, ne se dézoome pas) : barre de vie, barre d'outils, boutons du haut et du bord droit
UI_ZONES = [(0, 600, 480, 720), (750, 640, 1280, 720), (1160, 0, 1280, 70), (1200, 70, 1280, 640)]


def explored_mask(rgb):
    """Pixels de carte explorée : tout ce qui n'est pas noir, moins l'interface et le texte du journal."""
    mask = rgb.max(axis=2) > LIT_LEVEL
    for x0, y0, x1, y1 in UI_ZONES:
        mask[y0:y1, x0:x1] = False
    for top in LOG_LINE_TOPS:  # texte du journal : seulement la longueur de chaque ligne (la carte continue derrière)
        y0, y1 = top - 4, top + 18
        text = cut_at_gap(core_mask(rgb[y0:y1, :LOG_WIDTH]))
        columns = np.where(text.any(axis=0))[0]
        if len(columns):
            mask[y0:y1, :columns.max() + 6] = False
    return mask


def build_map(image):
    """Capture dézoomée -> (carte pour le modèle, boîte (x0, y0, x1, y1) dans la capture, échelle)."""
    rgb = np.asarray(image.convert("RGB"))
    ys, xs = np.where(explored_mask(rgb))
    if len(xs) == 0:
        box = (0, 0, image.width, image.height)
    else:
        box = (max(int(xs.min()) - MAP_PAD, 0), max(int(ys.min()) - MAP_PAD, 0),
               min(int(xs.max()) + MAP_PAD + 1, image.width), min(int(ys.max()) + MAP_PAD + 1, image.height))
    clean = image.convert("RGB")
    hidden = np.asarray(clean).copy()
    hidden[~explored_mask(hidden) & (hidden.max(axis=2) > LIT_LEVEL)] = 0  # interface et journal effacés
    crop = Image.fromarray(hidden).crop(box)
    scale = min(MAX_SIDE / crop.width, MAX_SIDE / crop.height, (MAX_AREA / (crop.width * crop.height)) ** 0.5)
    size = (max(round(crop.width * scale), 1), max(round(crop.height * scale), 1))
    scale = size[0] / crop.width
    picture = crop.resize(size, Image.NEAREST)  # pixel art : pas de flou
    hx, hy = (HERO[0] - box[0]) * scale, (HERO[1] - box[1]) * scale
    radius = max(14, round(8 * scale))
    ImageDraw.Draw(picture).ellipse([hx - radius, hy - radius, hx + radius, hy + radius],
                                    outline=HERO_COLOR, width=3)
    return picture, box, scale


def map_to_screen(point_2d, box, scale, picture_size, screen_size=(1280, 720)):
    """Point (0-1000) sur la carte -> point (0-1000) sur l'écran du jeu dézoomé."""
    x = box[0] + point_2d[0] / 1000 * (picture_size[0] - 1) / scale
    y = box[1] + point_2d[1] / 1000 * (picture_size[1] - 1) / scale
    return [round(x * 1000 / (screen_size[0] - 1)), round(y * 1000 / (screen_size[1] - 1))]


def main():
    parser = argparse.ArgumentParser(description="Carte de l'étage à partir d'une capture dézoomée")
    parser.add_argument("images", nargs="+", type=Path)
    args = parser.parse_args()
    for path in args.images:
        picture, box, scale = build_map(Image.open(path))
        target = path.with_name(path.stem + "-map.png")
        picture.save(target)
        print(f"{path.name}: zone {box}, échelle {scale:.2f}, carte {picture.size} -> {target.name}")


if __name__ == "__main__":
    main()

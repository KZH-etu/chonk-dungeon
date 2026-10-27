"""Étape 8 : actions dans le jeu (touches et clics).

Chaque action met le jeu au premier plan, agit, puis attend que l'écran se
stabilise et renvoie la nouvelle capture.

Test (le héros doit être au milieu d'une salle dégagée, sans ennemi) :
    python -m chonk_dungeon.actions --test
Pendant le test, ne pas toucher au clavier ni à la souris. Arrêt d'urgence :
envoyer la souris dans un coin de l'écran (sécurité de pyautogui).
"""

import argparse
import ctypes
import time
from ctypes import wintypes
from datetime import datetime

import pyautogui

from .capture import LOGS_DIR, GameWindow, changed_fraction

pyautogui.FAILSAFE = True  # souris dans un coin = arrêt immédiat
pyautogui.PAUSE = 0.05

# Noms des actions du harness -> touches du jeu (table de l'étape 2, noms QWERTY)
KEYS = {
    "up": "w", "down": "s", "left": "a", "right": "d",
    "up_left": "num7", "up_right": "num9", "down_left": "num1", "down_right": "num3",
    "wait": "space", "rest": "z", "examine": "e", "attack": "q", "pickup": "c",
    "special": "x", "resume": "r", "next_target": "tab", "inventory": "i",
    "hero_info": "h", "journal": "j", "close": "esc",
    **{f"quickslot_{n}": str(n) for n in range(1, 7)},
}

# Le jeu lit la POSITION physique des touches (nommée d'après un clavier QWERTY).
# pyautogui envoie des lettres, que Windows place selon la disposition active :
# sur un clavier AZERTY, « w » devient la touche Z du jeu (se reposer). On envoie
# donc directement les codes de position (scancodes), valables quelle que soit la disposition.
SCANCODES = {
    "esc": 0x01, **{str(n): 0x01 + n for n in range(1, 10)}, "0": 0x0B, "minus": 0x0C, "equals": 0x0D,
    "tab": 0x0F, "q": 0x10, "w": 0x11, "e": 0x12, "r": 0x13, "t": 0x14, "y": 0x15,
    "u": 0x16, "i": 0x17, "o": 0x18, "p": 0x19, "enter": 0x1C,
    "a": 0x1E, "s": 0x1F, "d": 0x20, "f": 0x21, "g": 0x22, "h": 0x23, "j": 0x24,
    "k": 0x25, "l": 0x26, "z": 0x2C, "x": 0x2D, "c": 0x2E, "v": 0x2F, "b": 0x30,
    "n": 0x31, "m": 0x32, "space": 0x39, "backspace": 0x0E,
    **{f"f{n}": 0x3A + n for n in range(1, 11)},
    "num7": 0x47, "num8": 0x48, "num9": 0x49, "num4": 0x4B, "num5": 0x4C,
    "num6": 0x4D, "num1": 0x4F, "num2": 0x50, "num3": 0x51,
}

# Durée d'appui : un appui instantané peut passer inaperçu pour certains jeux
HOLD_SECONDS = 0.05
MOVE_SETTLE_SECONDS = 0.1


# --------------------------------------------------------------------------- #
# Envoi des touches par scancode (SendInput)
# --------------------------------------------------------------------------- #

KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class _MOUSEINPUT(ctypes.Structure):  # seulement pour donner sa taille à l'union
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class _INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("ki", _KEYBDINPUT), ("mi", _MOUSEINPUT)]
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _U)]


def _send_scancode(scancode, up=False):
    flags = KEYEVENTF_SCANCODE | (KEYEVENTF_KEYUP if up else 0)
    event = _INPUT(type=1, ki=_KEYBDINPUT(wVk=0, wScan=scancode, dwFlags=flags, time=0, dwExtraInfo=0))
    if ctypes.windll.user32.SendInput(1, ctypes.byref(event), ctypes.sizeof(event)) != 1:
        raise OSError(f"SendInput a échoué pour le scancode {scancode:#x}")


class Actions:
    def __init__(self, window=None):
        self.window = window or GameWindow()

    def _ready(self):
        if not self.window.focus():
            print("   Attention : le jeu n'a pas pu passer au premier plan")

    def press(self, action, wait=True):
        """Appuie sur la touche d'une action (nom de KEYS, ou nom de touche de SCANCODES)."""
        key = KEYS.get(action, action)
        scancode = SCANCODES[key]
        self._ready()
        _send_scancode(scancode)
        time.sleep(HOLD_SECONDS)
        _send_scancode(scancode, up=True)
        return self.window.wait_stable() if wait else None

    def click(self, point_2d, wait=True):
        """Clique sur un point_2d (0-1000, sur l'image de la zone client)."""
        x, y = self.window.to_screen(point_2d)
        self._ready()
        # Le jeu prend le clic à la dernière position de souris qu'il a vue : on déplace,
        # on lui laisse le temps de voir le déplacement, puis on clique
        pyautogui.moveTo(x, y)
        time.sleep(MOVE_SETTLE_SECONDS)
        pyautogui.click()
        return self.window.wait_stable() if wait else None


# --------------------------------------------------------------------------- #
# Test
# --------------------------------------------------------------------------- #

# Chaque paire aller-retour ramène le héros (si aucun mur ne bloque)
TEST_SEQUENCE = [
    ("right", "touche D"), ("left", "touche A"),
    ("up", "touche W"), ("down", "touche S"),
    ("up_right", "diagonale pavé num. 9"), ("down_left", "diagonale pavé num. 1"),
    ("up_left", "diagonale pavé num. 7"), ("down_right", "diagonale pavé num. 3"),
    # L'inventaire est un panneau, pas une fenêtre : Échap ouvrirait le menu du jeu
    ("inventory", "basculer l'inventaire (I)"), ("inventory", "le rebasculer (I)"),
    ("journal", "ouvrir le journal"), ("close", "fermer (Échap)"),
    ("rest", "se reposer (Z)"),
]
# Le héros est au centre de l'écran ; une case fait ~48 px sur 1280, soit ~37 en 0-1000
TEST_CLICKS = [([537, 500], "clic une case à droite du héros"),
               ([463, 500], "clic une case à gauche du héros")]
REACTED = 0.01  # au-delà de 1 % de pixels changés, le jeu a réagi


def run_test():
    actions = Actions()
    run_dir = LOGS_DIR / ("actions-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
    run_dir.mkdir(parents=True, exist_ok=True)
    print("Test des actions dans 3 s : ne touchez plus au clavier ni à la souris.")
    time.sleep(3)

    actions.window.focus()
    before, _, _ = actions.window.wait_stable()
    steps = [(lambda a=a: actions.press(a), f"{a} ({label})") for a, label in TEST_SEQUENCE]
    steps += [(lambda p=p: actions.click(p), label) for p, label in TEST_CLICKS]

    for i, (do, label) in enumerate(steps, 1):
        start = time.perf_counter()
        after, stable, seconds = do()
        change = changed_fraction(before, after)
        verdict = "réagit" if change > REACTED else "AUCUN EFFET"
        print(f"{i:2}. {label:<45} {verdict:<12} ({change:.1%} des pixels,"
              f" {'stable' if stable else 'PAS stable'} en {seconds:.2f} s, total {time.perf_counter() - start:.2f} s)")
        after.save(run_dir / f"{i:02}_{label.split(' ')[0]}.png")
        before = after
    print(f"\nCaptures après chaque action : {run_dir}")


def main():
    parser = argparse.ArgumentParser(description="Actions dans le jeu (étape 8)")
    parser.add_argument("--test", action="store_true", help="teste touches, diagonales et clics")
    args = parser.parse_args()
    if args.test:
        run_test()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()

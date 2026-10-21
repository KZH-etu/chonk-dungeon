"""Étape 8 : capture de la fenêtre du jeu.

Trouve la fenêtre de Shattered Pixel Dungeon, capture sa zone client (sans
barre de titre ni bordures), la réduit pour le modèle et attend que l'écran
se stabilise après une action.

Toutes les coordonnées sont en pixels physiques de l'écran : le processus se
déclare « DPI aware » pour que mss (capture) et pyautogui (clics) parlent la
même langue, même avec une mise à l'échelle Windows à 125 %.

Test :
    python capture.py              # trouve la fenêtre, enregistre une capture
    python capture.py --stable     # mesure le temps de stabilisation de l'écran
"""

import argparse
import base64
import ctypes
import io
import time
from ctypes import wintypes
from datetime import datetime
from pathlib import Path

import mss
import numpy as np
from PIL import Image

# Avant tout appel à l'API Windows : coordonnées en pixels physiques
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)  # par écran
except (AttributeError, OSError):
    ctypes.windll.user32.SetProcessDPIAware()

ROOT = Path(__file__).parent
LOGS_DIR = ROOT / "logs" / "capture"
WINDOW_TITLE = "Shattered Pixel Dungeon"
MODEL_WIDTH = 768

user32 = ctypes.windll.user32


# --------------------------------------------------------------------------- #
# Fenêtre du jeu
# --------------------------------------------------------------------------- #

def find_window(title=WINDOW_TITLE):
    """Renvoie le handle de la première fenêtre visible dont le titre contient `title`."""
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def callback(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            length = user32.GetWindowTextLengthW(hwnd)
            buffer = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buffer, length + 1)
            if title.lower() in buffer.value.lower():
                found.append(hwnd)
        return True

    user32.EnumWindows(callback, 0)
    if not found:
        raise SystemExit(f"Fenêtre « {title} » introuvable : le jeu est-il lancé (et pas en plein écran) ?")
    return found[0]


class GameWindow:
    def __init__(self, title=WINDOW_TITLE):
        self.hwnd = find_window(title)
        self._mss = mss.MSS()

    def client_rect(self):
        """(left, top, width, height) de la zone client, en pixels écran. Relu à chaque appel (la fenêtre peut bouger)."""
        rect = wintypes.RECT()
        user32.GetClientRect(self.hwnd, ctypes.byref(rect))
        origin = wintypes.POINT(0, 0)
        user32.ClientToScreen(self.hwnd, ctypes.byref(origin))
        return origin.x, origin.y, rect.right, rect.bottom

    def focus(self):
        """Met le jeu au premier plan (les touches vont à la fenêtre active)."""
        if user32.GetForegroundWindow() == self.hwnd:
            return True
        if user32.IsIconic(self.hwnd):
            user32.ShowWindow(self.hwnd, 9)  # SW_RESTORE
        # Windows refuse le premier plan à un processus en arrière-plan, sauf juste
        # après une entrée clavier : un appui sur Alt (sans effet dans le jeu) le débloque
        for attempt in range(2):
            if attempt:
                user32.keybd_event(0x12, 0, 0, 0)  # VK_MENU (Alt) enfoncé
                user32.keybd_event(0x12, 0, 2, 0)  # relâché
            user32.SetForegroundWindow(self.hwnd)
            time.sleep(0.15)
            if user32.GetForegroundWindow() == self.hwnd:
                return True
        return False

    def grab(self):
        """Capture la zone client en pleine résolution (image PIL RGB)."""
        left, top, width, height = self.client_rect()
        shot = self._mss.grab({"left": left, "top": top, "width": width, "height": height})
        return Image.frombytes("RGB", shot.size, shot.rgb)

    def to_screen(self, point_2d):
        """Convertit un point_2d (0-1000 sur l'image envoyée = toute la zone client) en pixels écran."""
        left, top, width, height = self.client_rect()
        x, y = point_2d
        x = min(max(x, 0), 1000)
        y = min(max(y, 0), 1000)
        return round(left + x * (width - 1) / 1000), round(top + y * (height - 1) / 1000)

    def wait_stable(self, timeout=3.0, interval=0.12, changed_max=0.003, settle=2):
        """Attend que l'écran ne bouge plus et renvoie (image, stable, secondes).

        Stable = moins de `changed_max` des pixels changent nettement entre deux
        captures, `settle` fois de suite. Un seuil plutôt que l'égalité stricte :
        l'eau et les torches du jeu sont animées en permanence.
        """
        start = time.perf_counter()
        previous = self.grab()
        calm = 0
        while time.perf_counter() - start < timeout:
            time.sleep(interval)
            current = self.grab()
            if changed_fraction(previous, current) <= changed_max:
                calm += 1
                if calm >= settle:
                    return current, True, time.perf_counter() - start
            else:
                calm = 0
            previous = current
        return previous, False, time.perf_counter() - start


# --------------------------------------------------------------------------- #
# Images
# --------------------------------------------------------------------------- #

def changed_fraction(a, b, level=30):
    """Part des pixels dont au moins une couleur change de plus de `level` (0-255)."""
    if a.size != b.size:
        return 1.0
    diff = np.abs(np.asarray(a, dtype=np.int16) - np.asarray(b, dtype=np.int16)).max(axis=2)
    return float((diff > level).mean())


def resize(image, width=MODEL_WIDTH):
    """Réduit l'image à `width` px de large en gardant les proportions."""
    if image.width <= width:
        return image.copy()
    height = round(image.height * width / image.width)
    return image.resize((width, height), Image.LANCZOS)


def to_data_url(image):
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()


# --------------------------------------------------------------------------- #
# Test
# --------------------------------------------------------------------------- #

def main():
    parser = argparse.ArgumentParser(description="Test de capture (étape 8)")
    parser.add_argument("--stable", action="store_true", help="mesure aussi la stabilisation de l'écran")
    parser.add_argument("--width", type=int, default=MODEL_WIDTH)
    args = parser.parse_args()

    window = GameWindow()
    if not window.focus():
        print("   Attention : le jeu n'a pas pu passer au premier plan, la capture montrera ce qui le recouvre")
    left, top, width, height = window.client_rect()
    scale = ctypes.windll.shcore.GetScaleFactorForDevice(0)
    print(f"Fenêtre trouvée : zone client {width}x{height} en ({left}, {top}), mise à l'échelle Windows {scale} %")
    if (width, height) != (1280, 720):
        print(f"   Attention : zone client différente de 1280x720 (x{width / 1280:.2f}).")

    run_dir = LOGS_DIR / datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)
    start = time.perf_counter()
    image = window.grab()
    print(f"Capture : {image.width}x{image.height} en {(time.perf_counter() - start) * 1000:.0f} ms")
    image.save(run_dir / "full.png")
    sent = resize(image, args.width)
    sent.save(run_dir / f"sent_{sent.width}.png")
    print(f"Image pour le modèle : {sent.width}x{sent.height}")

    if args.stable:
        frames = [window.grab()]
        for _ in range(10):
            time.sleep(0.12)
            frames.append(window.grab())
        changes = [changed_fraction(a, b) for a, b in zip(frames, frames[1:])]
        print("Bruit de fond (jeu immobile), part des pixels qui changent : "
              + ", ".join(f"{c:.4f}" for c in changes))
        _, stable, seconds = window.wait_stable()
        print(f"wait_stable : {'stable' if stable else 'PAS stable'} après {seconds:.2f} s")

    print(f"Images : {run_dir}")


if __name__ == "__main__":
    main()

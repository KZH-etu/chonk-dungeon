"""Point d'entrée : python -m chonk_dungeon <commande> [options]

    play     jouer une partie (le harness)       python -m chonk_dungeon play --brain mistral
    view     l'écran pour la vidéo               python -m chonk_dungeon view --replay
    budget   le coût dépensé, d'après les logs   python -m chonk_dungeon budget

Les options de chaque commande : python -m chonk_dungeon <commande> --help
"""

import importlib
import sys

COMMANDS = {"play": "harness", "view": "viewer", "budget": "budget"}


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(__doc__.strip())
        sys.exit(0 if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help") else 2)
    command = sys.argv.pop(1)
    sys.argv[0] = f"python -m chonk_dungeon {command}"
    importlib.import_module(f".{COMMANDS[command]}", __package__).main()


if __name__ == "__main__":
    main()

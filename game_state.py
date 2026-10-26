"""État exact du jeu, exporté par le build modifié de SPD (« Chonk build », ChonkState.java).

À chaque fois que le héros attend un ordre, le jeu écrit chonk_state.json : la grille des cases que le
héros a vues (et seulement celles-là), les monstres en vue, les objets et pièges déjà vus, sa vie.
Le harness en tire, sans appel au modèle :
- un extrait de la carte autour du héros, en texte
- les directions libres
- des cibles (T1, T2...) : escalier qui descend, bords inexplorés, objets, ennemis, avec le plus court
  chemin connu, que l'action « goto » suit toute seule

Avec le jeu officiel, le fichier n'existe pas : tout cela est simplement absent (vision seule).

Test (le build doit tourner) :
    python game_state.py
"""

import json
import os
import time
from collections import deque
from pathlib import Path

STATE_FILE = Path(os.environ.get("CHONK_STATE") or Path(os.environ.get("APPDATA", "")) / ".chonkdungeon"
                  / "Shattered Pixel Dungeon (Chonk build)" / "chonk_state.json")

DIRECTIONS = {"up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0),
              "up_left": (-1, -1), "up_right": (1, -1), "down_left": (-1, 1), "down_right": (1, 1)}
WALKABLE = set(".+'>~")       # sol, portes, escalier qui descend, eau
AVOID = set("<^:")            # escalier qui monte (on remonterait), piège, gouffre : seulement comme cible
VIEW_X, VIEW_Y = 12, 7        # demi-largeur et demi-hauteur de l'extrait (l'écran montre ~26 x 15 cases)
FRONTIERS = 3                 # bords inexplorés proposés au modèle (plus les portes)
FRONTIER_GAP = 5              # écart minimal entre deux bords proposés
LEGEND = ("@ you, # wall, . floor, + door, ' open door, L locked door, > DOWN stairs, < up stairs, ~ water,"
          " : chasm, ^ trap, ? unexplored, e enemy, a ally, i item")


def load(path=STATE_FILE):
    """L'état exporté par le jeu, ou None (jeu officiel, ou fichier pas encore écrit)."""
    for _ in range(3):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, ValueError):  # remplacé pendant la lecture : on relit
            time.sleep(0.05)
    return None


class Floor:
    """La grille connue d'un étage, avec les chemins depuis le héros."""

    def __init__(self, state):
        self.state = state
        self.rows = state["map"]
        self.width, self.height = state["width"], state["height"]
        self.hero = (state["hero"]["x"], state["hero"]["y"])
        self.mobs = {(m["x"], m["y"]): m for m in state["mobs"]}
        self.items = {(i["x"], i["y"]): i for i in state["items"]}
        self.distance, self.previous = self._search()

    def tile(self, x, y):
        if 0 <= x < self.width and 0 <= y < self.height:
            return self.rows[y][x]
        return "?"

    def walkable(self, x, y):
        return self.tile(x, y) in WALKABLE and (x, y) not in self.mobs

    def _search(self):
        """Plus courts chemins (8 directions) depuis le héros sur les cases connues et sûres. Une case à
        éviter (escalier qui monte, piège, gouffre) ou occupée (monstre) peut être atteinte, pas traversée."""
        distance, previous = {self.hero: 0}, {}
        queue = deque([self.hero])
        while queue:
            x, y = queue.popleft()
            if (x, y) != self.hero and not self.walkable(x, y):
                continue  # une cible atteignable, mais on ne passe pas au travers
            for dx, dy in DIRECTIONS.values():
                cell = (x + dx, y + dy)
                if cell in distance:
                    continue
                if self.walkable(*cell) or self.tile(*cell) in AVOID or cell in self.mobs:
                    distance[cell] = distance[(x, y)] + 1
                    previous[cell] = (x, y)
                    queue.append(cell)
        return distance, previous

    def path(self, cell):
        """Les pas (noms de direction) jusqu'à `cell`, ou None si on ne connaît pas de chemin."""
        if cell not in self.distance:
            return None
        steps = []
        while cell != self.hero:
            before = self.previous[cell]
            delta = (cell[0] - before[0], cell[1] - before[1])
            steps.append(next(name for name, d in DIRECTIONS.items() if d == delta))
            cell = before
        return steps[::-1]

    def free_directions(self):
        x, y = self.hero
        return [name for name, (dx, dy) in DIRECTIONS.items() if self.walkable(x + dx, y + dy)]

    def frontiers(self):
        """Bords de la zone explorée : cases sûres atteignables qui touchent une case jamais vue, les plus
        proches d'abord, espacées d'au moins FRONTIER_GAP cases (sinon les trois plus proches seraient le même
        bout de couloir). Une porte qui donne sur l'inconnu est toujours gardée : c'est la meilleure piste."""
        edges = sorted((cell for cell in self.distance if self.walkable(*cell) and cell != self.hero
                        and any(self.tile(cell[0] + dx, cell[1] + dy) == "?" for dx, dy in DIRECTIONS.values())),
                       key=lambda cell: self.distance[cell])
        doors = [cell for cell in edges if self.tile(*cell) == "+"]
        chosen = list(doors)
        for cell in edges:
            if all(max(abs(cell[0] - c[0]), abs(cell[1] - c[1])) >= FRONTIER_GAP for c in chosen):
                chosen.append(cell)
        chosen.sort(key=lambda cell: self.distance[cell])
        return chosen[:FRONTIERS + len(doors)]

    def view(self):
        """L'extrait de la carte centré sur le héros, une ligne de texte par rangée de cases."""
        hx, hy = self.hero
        lines = []
        for y in range(hy - VIEW_Y, hy + VIEW_Y + 1):
            row = []
            for x in range(hx - VIEW_X, hx + VIEW_X + 1):
                cell = (x, y)
                if cell == self.hero:
                    row.append("@")
                elif cell in self.mobs:
                    row.append("a" if self.mobs[cell]["ally"] else "e")
                elif cell in self.items:
                    row.append("i")
                else:
                    row.append(self.tile(x, y))
            lines.append("".join(row))
        # Rangées et colonnes du bord entièrement inexplorées : des tokens pour rien (le @ garde le repère)
        while lines and set(lines[0]) == {"?"}:
            lines.pop(0)
        while lines and set(lines[-1]) == {"?"}:
            lines.pop()
        left = min(len(line) - len(line.lstrip("?")) for line in lines)
        right = min(len(line) - len(line.rstrip("?")) for line in lines)
        return [line[left:len(line) - right] for line in lines]


def where(hero, cell):
    """« 3 right, 2 up » : position d'une case par rapport au héros."""
    dx, dy = cell[0] - hero[0], cell[1] - hero[1]
    parts = []
    if dx:
        parts.append(f"{abs(dx)} {'right' if dx > 0 else 'left'}")
    if dy:
        parts.append(f"{abs(dy)} {'down' if dy > 0 else 'up'}")
    return ", ".join(parts) or "here"


def compress(steps):
    """['right', 'right', 'down'] -> « right x2, down »."""
    out = []
    for step in steps:
        if out and out[-1][0] == step:
            out[-1][1] += 1
        else:
            out.append([step, 1])
    return ", ".join(f"{name} x{count}" if count > 1 else name for name, count in out)


def targets(floor):
    """Les cibles proposées au modèle : {"T1": {"cell", "label", "steps"}}, dans l'ordre d'intérêt."""
    found = []
    stairs = [(x, y) for y, row in enumerate(floor.rows) for x, tile in enumerate(row) if tile == ">"]
    for cell in stairs:
        found.append((cell, "DOWN stairs (take them to go deeper)"))
    for cell, mob in sorted(floor.mobs.items(), key=lambda kv: floor.distance.get(kv[0], 999)):
        if not mob["ally"]:
            found.append((cell, f"enemy: {mob['name']} ({mob['state']}, HP {mob['hp']}/{mob['ht']})"))
    for cell, item in sorted(floor.items.items(), key=lambda kv: floor.distance.get(kv[0], 999)):
        if cell != floor.hero:
            found.append((cell, f"item: {item['name']}"))
    for cell in floor.frontiers():
        kind = "door" if floor.tile(*cell) == "+" else "edge of the explored area"
        found.append((cell, f"{kind} (unexplored beyond)"))
    result = {}
    for cell, label in found:
        steps = floor.path(cell)
        result[f"T{len(result) + 1}"] = {"cell": cell, "label": label, "steps": steps}
    return result


def describe(state, found):
    """Le bloc de texte ajouté au message du modèle."""
    floor = Floor(state)
    hero = state["hero"]
    lines = [f"Game state (exact, read from the game): floor {state['depth']}, HP {hero['hp']}/{hero['ht']},"
             f" hero level {hero['level']}, hunger {hero['hunger']}.",
             f"Map around you (1 character = 1 tile, rows from top to bottom; {LEGEND}):"]
    lines += floor.view()
    free = floor.free_directions()
    lines.append("Free steps from your tile: " + (", ".join(free) if free else "none") + ".")
    if found:
        lines.append("Targets (use goto with the id; it follows the path itself):")
        for name, target in found.items():
            steps = target["steps"]
            route = (f"{len(steps)} step{'s' if len(steps) > 1 else ''}: {compress(steps)}" if steps else
                     "no known path")
            lines.append(f"- {name}: {target['label']}, {where(floor.hero, target['cell'])} ({route})")
    if not any(tile == ">" for row in floor.rows for tile in row):
        lines.append("The down stairs are not found yet on this floor: explore (goto an edge of the explored area).")
    return "\n".join(lines)


def main():
    state = load()
    if state is None:
        print(f"Pas d'état : {STATE_FILE} introuvable (le Chonk build est-il lancé ?)")
        return
    print(describe(state, targets(Floor(state))))


if __name__ == "__main__":
    main()

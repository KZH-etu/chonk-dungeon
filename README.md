# Chonk Dungeon

**Mistral Large plays Shattered Pixel Dungeon.** An entry for Mistral's *Le Chonk wants to play* challenge,
vibe coded with Claude Code.

Shattered Pixel Dungeon is a turn-based roguelike: one hero, a dungeon of random floors, monsters, unidentified
potions and scrolls, and permanent death. Chonk Dungeon is a harness that lets a Mistral model play it the
way a person does. The model looks at the screen, reads the game log and thinks aloud. Then it acts with the
mouse and keyboard: it clicks where it wants to go, examines unknown monsters and drinks potions from its bag.

The harness does not hard-code any strategy. Every decision (where to go, when to fight, when to rest or flee,
what to use) comes from the model.

## How it works

Each turn the harness:

1. **Captures the game window** and reads it with small detectors and a pixel-font OCR: HP bar, game log
   (with its colors), open windows, the attack and pick-up buttons, and whether the hero is busy.
2. **Reads the game state** that the modified game writes after each hero turn (`chonk_state.json`). This is
   what the player knows: the explored map, visible monsters with their description, seen items, the
   bag and its actions. From it, the harness builds a compact map and a list of **targets** (`T1`, `T2`...):
   down stairs, enemies, items, doors and unexplored edges, each with its walking distance.
3. **Calls the model only when it matters**, when something changed or the previous plan ended. The call
   carries the screenshot, the situation, the game state, a short memory (goal, recent actions and their
   results, a notebook of lasting facts) and the list of actions usable right now.
4. **Plays the model's plan**: 1 to 5 actions, as strict JSON. The plan stops as soon as something changes
   (an enemy shows up, the hero takes damage, a window opens, an item lands under the hero). The model is
   then told why it stopped.

Actions are those of a player:

| Action | What the harness does |
|---|---|
| `goto T3` | Clicks the target on screen. The game walks the hero there, attacks an enemy or takes the stairs |
| `examine T1` | Uses the magnifier on a monster or tile and saves its description to the notebook |
| `use "potion" DRINK` | Opens the inventory, clicks the item, then the action button (`EAT`, `READ`, `THROW`...) |
| `attack`, `loot` | Presses the attack or pick-up button |
| `move`, `wait`, `rest` | Keyboard steps, skip a turn, rest until healed or disturbed |
| `click`, `key`, `map` | Clicks a point (0–1000) in a window, presses a game key, shows the zoomed-out map |

The model's reasoning, goal and plan are logged each turn (`logs/runs/<date>/turns.jsonl`, with screenshots).
The viewer shows them next to the game, live or as a replay for the video: what just happened, Mistral's
thoughts and goal, its numbered plan (also drawn on the screenshot), its notebook, HP, floor and cost. The window
can be resized; wide or full screen (F11), the screenshot goes left and the reasoning right.

## The game: a fork of Shattered Pixel Dungeon

The harness plays the **Chonk build**, a lightly modified Shattered Pixel Dungeon 4.0.2. Its source is
published separately under GPL-3.0: https://github.com/KZH-etu/shattered-pixel-dungeon (branch `chonk`).
The changes are kept small:

- a separate app name, package and save folder (`%APPDATA%\.chonkdungeon\`), with the update checker
  disabled, as the original project asks of forks;
- `ChonkState.java`, which writes `chonk_state.json` (what the player knows, nothing hidden) and
  `chonk_ui.json` (where the hero, the inventory slots and the buttons are on screen, so clicks land exactly).

Gameplay is unchanged. The model sees nothing a player could not see.

## Running it

Windows, Python 3.12, a 1280×720 game window. The keyboard mapping assumes an AZERTY layout (see `chonk_dungeon/actions.py`).

```powershell
python -m venv .venv; .venv\Scripts\pip install -r requirements.txt
# .env: MISTRAL_API_KEY=...  and  CHONK_MODEL=mistral-large-4 (or another Mistral vision model)

# Build the fork once (JDK 21): in the fork folder, gradlew desktop:release
.\scripts\launch_game.ps1         # starts the Chonk build (CHONK_JAVA / CHONK_JAR to change the paths)

python -m chonk_dungeon play --brain mistral --max-turns 60 --budget 0.30 --goal-floor 0
python -m chonk_dungeon view      # live view of the model's reasoning, next to the game
python -m chonk_dungeon view logs/runs/<date> --replay --fullscreen   # replay a past game
python -m chonk_dungeon budget    # money spent so far, from the logs
```

Each command lists its options with `--help`. In the viewer, Space pauses a replay, ← → step through turns,
F11 toggles full screen and Ctrl + / Ctrl − change the text size. A replay shows each turn for `--delay`
seconds, or with `--speed 10` follows the real timing of the game, 10 times faster, so that it lines up with a
recording of the game sped up as much.

Tests without the API: `--dry-run` prints the prompt, `--brain manual` takes actions typed in the console,
`--brain scripted --script "goto T1; examine T2"` plays a fixed list. The harness pauses when another window
covers the game, and stops at the `--budget` limit (in $, counted from the API's token usage).

## Project layout

```
chonk_dungeon/          the harness (python -m chonk_dungeon play | view | budget)
  harness.py            main loop: read the screen, call the brain, play its plan
  brain.py              prompt, JSON schema of the answer, Mistral call
  llm.py                API client: rate limit, retries, cost
  memory.py             goal, recent actions and their results, notebook
  game_state.py         exact state from the Chonk build: map, targets, bag
  capture.py            capture of the game window
  detectors.py, ocr.py  reading the screen: HP, buttons, windows, death, game log (pixel font)
  overview.py           zoomed-out floor map
  actions.py            keyboard and mouse
  budget.py             cost of the games, from the logs
  viewer.py             the view for the video, live or replay
scripts/launch_game.ps1 starts the Chonk build
tools/vision_test.py    benchmark of the model's vision on reference screenshots
samples/                reference screenshots (detector and OCR checks, vision benchmark)
docs/development-plan.md  development notes (in French)
logs/                   games, screenshots and costs (not in git)
```

Self-checks without the API: `python -m chonk_dungeon.detectors --check` (screen reading on the reference
screenshots), `python -m chonk_dungeon.game_state` (state exported by the running game).

## Credits

- **Shattered Pixel Dungeon** by Evan Debenham (Shattered Pixel), GPL-3.0:
  https://github.com/00-Evan/shattered-pixel-dungeon. You can support its development at
  https://www.patreon.com/ShatteredPixel. Based on **Pixel Dungeon** by Oleg Dolya (Watabou).
- The Chonk build is an unofficial fork: please do not report its issues to the original developers.
- Mistral AI models through the Mistral API.
- The project (harness, viewer and the changes to the game) was vibe coded with Claude Code (Anthropic).
  Every in-game decision is made by Mistral.
- Idea of action plans, a stated goal and a "usable actions" line inspired by
  [heroes-3-mcp](https://github.com/andreinwald/heroes-3-mcp), another entry of the challenge.

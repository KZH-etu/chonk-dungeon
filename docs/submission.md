Hey all! I had Le Chonk play **Shattered Pixel Dungeon**, a turn-based roguelike with permadeath 🐱🗡️

Every turn it gets a screenshot, what the player knows (the explored map, the monsters in sight, its bag), its
goal and the notes it wrote to itself. It thinks aloud, then plays a plan of a few actions like a person
would: clicking where to go, examining monsters, eating, drinking, fighting. There is no scripted strategy and
no other model: every decision is Mistral Large 4, at about $0.005 per decision.

In its best run it reached **floor 3**. Along the way it killed 13 enemies, finished the Sad Ghost's quest
rat and picked the *Hearty Meal* talent right after nearly starving. Then it examined a sewer crab and wrote
"extremely fast, fight at range or use doorways"... and tried to outrun it 🫠

🎬 Video: VIDEO_LINK
💻 Code: https://github.com/KZH-etu/chonk-dungeon
🎮 Modified game (GPL-3.0, state export only): https://github.com/KZH-etu/shattered-pixel-dungeon

Vibe coded by 1 person with Claude Code, on top of the open-source Shattered Pixel Dungeon (Evan Debenham).
Every in-game decision is Mistral Large 4 :lechonk:

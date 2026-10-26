# Chonk Dungeon : plan de route

Harness permettant à **Mistral Large 4** de jouer à **Shattered Pixel Dungeon** par la vision (captures d'écran, puis clavier et souris).

- **Concours** : « Le Chonk wants to play » (Mistral AI), du 16 au 29 octobre 2026
- **Soumission visée** : 28 octobre, pour garder une journée de marge
- **Objectif** : minimum étage 3, cible étage 5, bonus battre Goo (premier boss)

## Budget et limites (contrainte n° 1)

- **Limites ML4 (offre gratuite)** : 20 000 tokens/min, 0,83 req/s. Le goulot, ce sont les **tokens par minute** : environ 4 appels/min, soit 1 appel toutes les 15 s
- **Budget** : forfait inclus de **10 $/mois**, renouvelé le 1er novembre, donc **après** le concours. C'est tout le budget du concours
- **Prix ML4** (sources tierces, à confirmer sur mistral.ai/pricing) : 1,36 $/M en entrée et 4,18 $/M en sortie (actuellement −50 %)
- **Estimation** : ~4 800 tokens par appel, soit ~0,004 à 0,007 $ par appel, donc **1 350 à 2 700 appels** au total (à recalculer à l'étape 7)

| Poste | Appels |
|---|---|
| Tests et mise au point | ~300 |
| Parties d'évaluation et vidéo | ~1 000 à 1 500 |
| Réserve | le reste |

**Leviers prioritaires pour réduire le nombre d'appels** (ils peuvent le diviser par 2 ou 3) :
1. Détecteurs Python : n'appeler le modèle que quand c'est utile
2. Déplacement lointain : une décision = plusieurs pas
3. Image plus petite (768 px), recadrée sur la zone utile

**On accepte la lenteur** : une partie de 300 appels dure environ 75 minutes. On la lance et on fait autre chose pendant ce temps. Dans la vidéo, tout est accéléré.

**Dernier recours** : quelques euros de crédits en paiement à l'usage, **avec un plafond de dépenses**.

---

## Phase 0 : préparation (19-20 oct.)

### Étape 1 : installer le jeu
- [x] Télécharger la version Windows « Latest » sur https://github.com/00-Evan/shattered-pixel-dungeon/releases
- [x] Désactiver le plein écran (`fullscreen` à `false` dans `%APPDATA%\.shatteredpixel\Shattered Pixel Dungeon\settings.xml`)
- [x] Noter la **version exacte** installée : 4.02 (code de version `922` dans `settings.xml`, nom à lire dans le menu du jeu)
- [x] Mettre le jeu **en anglais** (`language` = `en`)
- [x] Fixer une taille de fenêtre (1280×720) et ne plus y toucher : les coordonnées du harness en dépendent

### Étape 2 : maîtriser le jeu soi-même
- [x] Jouer 2 ou 3 parties jusqu'à l'étage 3 au moins
- [x] Ouvrir l'écran **Key bindings** et faire une capture (`images/key_bindings/`)
- [x] Remplir la table des touches ci-dessous (touches par défaut du jeu, identiques aux captures) :

| Action | Touche réelle |
|---|---|
| Haut / Gauche / Bas / Droite | `W` `A` `S` `D` (ou flèches, ou pavé num. `8` `4` `2` `6`) |
| Diagonales NO / NE / SO / SE | Pavé num. `7` `9` `1` `3` **uniquement** |
| Attendre / Ramasser | `Espace` (ou pavé num. `5`) |
| Se reposer | `Z` |
| Fouiller | Pas d'action dédiée dans la liste des touches |
| Examiner | `E` |
| Attaquer (case rouge) | `Q` |
| Ramasser (bouton bleu) | `C` (ou `Entrée`) |
| Action spéciale | `X` |
| Reprendre le déplacement | `R` |
| Cible suivante / onglet suivant | `Tab` |
| Inventaire | `F` (ou `I`) |
| Emplacements rapides 1 à 6 | `1` à `6` |
| Sacs 1 à 5 | `F1` à `F5` |
| Infos du héros | `H` |
| Journal | `J` |
| Zoom + / − | `+` (ou `=`) / `-` |
| Fermer une fenêtre | `Échap` (ou `Retour arrière`) |

> **Ramassage** : marcher sur la case d'un objet ramasse **certains** objets automatiquement, mais pas tous. Pour les autres, il faut le faire **volontairement**. Dès que le héros est sur un objet pas encore ramassé, le **bouton bleu** apparaît sur le bord droit de l'écran avec l'icône de l'objet : on ramasse avec `C` (ou un clic sur le bouton). Règle du harness : après chaque déplacement, si le bouton bleu est visible, proposer l'action « ramasser » au modèle.

> **Testé à l'étape 8** : les diagonales du pavé numérique (`7` `9` `1` `3`) fonctionnent quand on les envoie au jeu, pas besoin de les réassigner.

- [x] Repérer à l'écran : la zone des messages, la barre de vie, les icônes de statut, la case rouge, la case bleue, l'inventaire rapide
- [x] Faire **3 captures de référence** : exploration calme, combat (case rouge visible), inventaire ouvert

### Étape 3 : environnement Python (sous Windows, pas WSL)
```powershell
mkdir C:\dev\chonk-dungeon
cd C:\dev\chonk-dungeon
mise use python@3.12
python -m venv .venv
.venv\Scripts\activate
pip install mistralai mss pyautogui pillow pygetwindow numpy
```
- [x] Vérifier que `python -c "import mss, pyautogui"` ne renvoie pas d'erreur

### Étape 4 : clé API Mistral
- [x] Créer une clé sur console.mistral.ai
- [x] `setx MISTRAL_API_KEY "ta_clé"` puis rouvrir PowerShell
- [x] Vérifier les **limites de débit** et le budget de ton compte : 20 000 tokens/min, 0,83 req/s, 10 $/mois (voir « Budget et limites »)
- [ ] Fixer un **plafond de dépenses** (Admin Panel > Subscriptions > Billing) avant d'ajouter des crédits

### Étape 5 : dépôt GitHub
- [x] `git init` puis `gh repo create chonk-dungeon --public --source . --remote origin`
- [x] Ajouter un `.gitignore` (`.venv/`, `logs/`, `*.env`)
- [x] **Ne jamais committer la clé API**

### Étape 6 : enregistrement
- [x] Installer **OBS Studio** et configurer une scène « fenêtre du jeu »
- [x] Enregistrer **toutes** les sessions de test à partir de maintenant

---

## Phase 1 : valider la vision (20-21 oct.)

### Étape 7 : script de test de vision
- [ ] Envoyer à ML4 une des captures de référence
- [ ] Lui demander de **pointer** (`point_2d`) : le héros, chaque ennemi, l'escalier, la case rouge
- [ ] Dessiner les points sur l'image et vérifier visuellement
- [ ] Lui demander de **lire** : la vie, les 3 derniers messages, les statuts
- [ ] Noter le **nombre de tokens et le temps** par appel
- [ ] Comparer les tokens d'une capture à 1024 px et à 768 px, en vérifiant que le pointage reste précis
- [ ] **Recalculer le budget** (coût par appel, nombre d'appels possibles) avec les vrais chiffres

> **Point de décision** : si le pointage est imprécis sur la carte, on ajoute une **grille numérotée** sur la capture, et le modèle choisit une case au lieu de coordonnées libres.

> **Point de décision (plan B)** : si une capture coûte beaucoup plus que prévu, on passe en **mode hybride**. Le Chonk ne reçoit l'image que lorsqu'il en a besoin (menus, alertes, nouvelle salle). Le reste du temps, il reçoit un court résumé texte produit par les détecteurs Python. On divise ainsi les tokens par 2 ou 3 sans perdre l'aspect vision.

---

## Phase 2 : le cœur du harness (21-24 oct.)

### Étape 8 : capture et actions
- [x] `capture.py` : trouver la fenêtre du jeu, la mettre au premier plan, capturer sa zone client (1280×720, sans barre de titre) et réduire (768 px)
- [x] `actions.py` : appuyer sur une touche, cliquer sur un `point_2d` (conversion de 0-1000 en pixels de la fenêtre)
- [x] Attendre que l'écran se **stabilise** après chaque action (comparaison de deux captures) : ~0,5 s sans mouvement, ~1 s après un pas

> **Clavier AZERTY** : le jeu lit la **position** des touches (noms QWERTY). Envoyées comme lettres, `W` et `A` deviennent `Z` (se reposer) et `Q` (attaquer). `actions.py` envoie donc des **scancodes** (position physique), indépendants de la disposition du clavier.

> **Inventaire** : c'est un panneau, pas une fenêtre. `Échap` ne le ferme pas (il ouvre le menu du jeu) ; on le bascule avec `I`.

> **Écran** : mise à l'échelle Windows 125 %, mais la zone client du jeu fait bien 1280×720 pixels physiques. Le harness se déclare « DPI aware » pour que capture et clics utilisent les mêmes pixels.

### Étape 9 : détecteurs Python (sans LLM)
> **Prioritaire** : chaque appel évité compte, en tokens par minute comme en dollars.

- [x] Case rouge visible ou non : bouton d'attaque, cherché sur la colonne à 12 px du bord droit (sa hauteur varie avec l'inventaire)
- [x] Case bleue visible ou non : bouton de ramassage, même colonne
- [x] **Ennemis en vue** : compteur rouge à crâne sous le bouton du menu (bonus, gratuit)
- [x] Niveau de vie (longueur de la barre rouge, x = 90 à 470)
- [x] Changement dans la zone des messages : comparaison des lignes lues (`new_messages`)
- [x] Statuts : pas de détecteur d'icônes (il y en a beaucoup, et le décor passe derrière). Un statut se signale par une **perte de vie** et un **message** coloré (ex. vert « Your wounds begin to close. »), déjà détectés ; le modèle voit les icônes sur l'image quand on l'appelle
- [x] Fenêtre ouverte : couleur du bord des fenêtres (160,166,149) dans la zone centrale (2 000 à 4 000 px avec fenêtre, 210 au plus sans)
- [x] Inventaire ouvert : bord du panneau à y = 475 (fermé : y = 649)
- [x] **Lecture du journal par OCR** (`ocr.py`) : bibliothèque de lettres apprises sur de vraies captures (`ocr_glyphs.json`), avec la couleur et l'ordre des lignes, et recollage des longs messages coupés
  - La police du jeu (`pixel_font.ttf`, dans `desktop-4.0.2.jar`) ne suffit pas : le moteur la lisse à sa façon
  - Validé sans appel API : 6/7 captures de référence (la 7e a la 1re lettre coupée au recadrage), 30/30 vraies captures
  - Couleurs des lignes : blanc, jaune, orange (avertissement), rouge (mort), vert (soin, effet positif)
  - Lettres connues : `!'.1:AKMNTUYabcdefghiklmnoprstuvwy`. Une lettre inconnue donne `?` : la ligne est alors marquée illisible et on envoie l'image au modèle. **Enrichir** en ajoutant des captures et leur texte dans `OCR_SAMPLES` puis `python ocr.py --learn`
  - Reste à faire : le texte des **fenêtres** (examiner, descriptions), écrit dans une autre taille de police
- [x] Écran de mort : vie à 0, ou bandeau « Game Over » (couleur (131,141,110), absente de toutes les autres captures). Le message de mort est en **rouge** (« Marsupial rat killed you... »)
- `python detectors.py --check` : compare tous les détecteurs à `ground_truth.json` (7/7 partout, dont la mort, sauf les messages, 6/7). Environ 45 ms par capture

### Étape 10 : la boucle et les modes
`harness.py` : la boucle, avec un « cerveau » interchangeable. `scripted` (liste d'actions) et `manual` (console) servent aux tests **sans API** ; le cerveau Mistral arrive à l'étape 11.

- [x] Machine à états, du plus prioritaire au moins prioritaire : **Mort** (fin de partie), **Menu** (fenêtre ouverte), **Danger** (vie ≤ 35 % ou 30 % perdus d'un coup), **Combat** (ennemi en vue ou bouton d'attaque), **Alerte** (un changement : message, vie, bouton), **Exploration**
- [x] Exploration : décisions longues (clic sur une case éloignée = le héros marche seul, la boucle attend qu'il s'arrête, jusqu'à 20 s ; action `resume` = `R`)
- [x] Combat et Danger : un appel par tour
- [x] Le message envoyé au modèle explique **pourquoi** on l'appelle (`changes()` : « vie : 100 % -> 70 % », « bouton d'attaque apparu », « nouveaux messages : ... », « déplacement terminé »)
- [x] **Appel sans image** après « examiner » quand la fenêtre est lisible ; l'image reste envoyée pour la carte et les menus
- [x] Les derniers messages du journal sont joints **en texte** à chaque appel (mise en forme à l'étape 11)
- [x] Action **examiner** : `E` (ou la loupe), clic sur la case, lecture OCR de la fenêtre (`window_rect` + `read_window`), fermeture. Testé dans le jeu : escalier → « Depth entrance / Stairs lead up to the upper depth. »
- [x] **Ramassage automatique** : bouton bleu sans ennemi en vue → `C` sans appeler le modèle (un appel économisé à chaque objet)
- [ ] Limite de tokens par minute (`RateLimiter`) : écrite, à brancher sur le cerveau Mistral (étape 11)

> **Clics** : le jeu prend un clic à la **dernière position de souris qu'il a vue**. `actions.py` déplace la souris, attend 0,1 s, puis clique ; sans cette pause, le clic tombait à l'ancienne position (la loupe ou le héros).

> **Fenêtres** : l'OCR lit aussi le texte des fenêtres (même police que le journal, plus une police plus grande pour les titres), appris sur les captures `SPD_hero_info_*` et `SPD_journal_*`. Les icônes donnent des `?` en bout de ligne, retirés. Pas encore lu : le texte **sombre sur parchemin** des pages du guide (on enverra l'image).

> **Principe : examiner plutôt que tout décrire.** Le jeu contient des éléments qu'on ne peut pas tous décrire à l'avance (on ne les a pas tous vus). Le prompt ne décrit que la **liste fermée** dont le harness dépend : héros, escalier, boutons d'attaque et de ramassage, barre de vie, journal. Pour tout le reste (monstres, objets, pièges, décor), en cas de doute, le modèle **examine** la case : le jeu lui donne le nom et la description. Les erreurs restantes se corrigent en jouant (un faux escalier : `>` ne fait rien, le journal le dit).

### Étape 11 : le prompt et le format de réponse
`brain.py` (cerveau Mistral) et `llm.py` (clé, prix, 429, limite de tokens/min, partagé avec `vision_test.py`). Lancement : `python harness.py --brain mistral` (`--dry-run` affiche le prompt sans appel API, `--budget` plafonne le coût de la partie).

- [x] Prompt système **en anglais** (comme le texte du jeu, le README et la vidéo) : objectif, fonctionnement du harness, actions, ce que montre l'écran (liste fermée), conseils de jeu, format de réponse (~530 mots)
- [x] Réponse en **JSON imposé par l'API** (`json_schema` strict) plutôt qu'en texte libre + regex : au test de vision, 100 % de JSON valides. Le modèle ne peut ni oublier un champ ni inventer une action
- [x] Format : `{"reasoning", "action": click | examine | attack | loot | key | wait | rest | resume, "point_2d", "key", "label"}`, champs inutiles à `null`, converti en décision du harness
- [x] `label` peut valoir « unknown » : en cas de doute, le prompt demande d'**examiner** plutôt que de deviner
- [x] `reasoning` : une phrase courte, enregistrée dans `reasoning.txt` du dossier de la partie (pour la vidéo)
- [x] Message de chaque tour : mode, raisons de l'appel, état lu par les détecteurs, journal en texte, dernière action, résultat d'« examiner », image (sauf quand le texte suffit)
- [x] Limite de tokens/min branchée ; réponse inutilisable : un nouvel essai puis « attendre » ; 429 persistant : « attendre » sans planter
- [x] **Premier test (5 appels, 0,011 $)** : JSON valide à chaque appel, ~1 500 tokens et **0,0023 $ par appel**. Mais le modèle cliquait sur sa propre case et restait dans la salle. Corrigé :
  - le harness signale un clic sans effet (« nothing happened: the hero did not move ... »)
  - le prompt dit où est le héros (~[500, 500]) et demande des cibles **lointaines** (portes, couloirs, bords de la zone explorée)
- [x] **Second test (10 appels, 0,024 $)** : le modèle vise les portes, sort de la salle, trouve un serpent d'égout, l'attaque et le tue (7 tours, 50 % de vie perdus : le serpent esquive beaucoup)
  - Corrigé au passage : des pixels de mur clairs étaient lus comme des lignes du journal (une ligne doit commencer au bord gauche), et « RAIDO » était mal lu (I et O appris)
- [x] Objectif donné au modèle : **atteindre l'étage 3**, donc prendre **deux fois un escalier qui descend** (jamais celui qui monte, par lequel on arrive : « Depth entrance » à l'examen). L'étage 5 et Goo viendront ensuite
- [x] Conseil de jeu : les serpents d'égout esquivent beaucoup ; on les touche plus sûrement quand ils sont **sur une case de porte** juste à côté du héros (attendre près de la porte, puis attaquer)

### Étape 12 : la mémoire
`memory.py`, branché dans `harness.py` et `brain.py`. Enregistré à chaque tour dans `memory.json` du dossier de la partie (pour la vidéo).

- [x] **Carnet de notes** : la réponse a deux champs `note_add` (une note courte) et `note_remove` (numéro d'une note). 15 notes au plus (la plus ancienne part). Les notes du modèle sont préfixées par l'étage où il les écrit (« (floor 1) ... »)
- [x] Ce qu'il apprend en examinant va **tout seul** dans le carnet (« Examined Depth entrance: Stairs lead up to the upper depth. »), une seule fois par chose
- [x] **Historique court** : les 8 dernières actions, chacune avec **ce qui a suivi** (« T2 click [500, 500] -> nothing happened: the hero did not move »)
- [x] Pas d'historique d'images : **une seule capture par appel**
- [x] **Étage courant** suivi grâce au journal (« You descend to floor 2 ») et donné au modèle à chaque tour ; `--goal-floor 3` (par défaut) arrête la partie en succès à l'étage 3
- Le prompt demande de décrire les lieux en mots, pas en coordonnées (elles changent quand la caméra bouge). Coût : ~+500 tokens par appel (~0,003 $ au lieu de 0,0024 $)
- Testé dans le jeu avec le cerveau scripté (sans API) : l'examen de l'escalier est noté dans le carnet, l'historique garde chaque action et sa suite, `memory.json` est écrit à chaque tour

### Étape 13 : filets de sécurité
- [x] Même action 3 fois sans changement à l'écran : message « STUCK: ... Do something different. » au modèle, et Échap **seulement si une fenêtre est ouverte** (sans fenêtre, Échap ouvre le menu du jeu). `wait`, `rest` et `examine` ne comptent pas (ils ne changent pas l'écran, et c'est voulu). Testé dans le jeu
- [x] Réponse illisible : on réessaie une fois, puis action par défaut (attendre)
- [x] Mort : bilan de la partie dans `run_summary.json` (fin, étage atteint, tours, cause de la mort lue en rouge dans le journal, appels, coût, durée) ; `--games N` relance avec « New Game » et vérifie qu'une partie a bien commencé, sinon arrêt propre. **Relance pas encore testée** (il faut une mort)
- [x] Erreur **429** : attente puis nouvel essai ; si elle persiste, « attendre » sans faire planter la partie
- [x] **Compteur de coût** : `--budget` arrête proprement la session quand il est dépassé
- [x] **Fenêtre devant le jeu** (alerte Windows, notification) : vérifiée avant chaque capture (`WindowFromPoint` sur 9 points) ; pause jusqu'à 1 min, puis arrêt propre. Découvert pendant un test : une alerte « batterie faible » assombrissait le jeu, et la barre de vie assombrie se lisait comme vide
- [x] **Mort = bandeau « Game Over »** uniquement (une vie à 0 ne suffit plus, pour la même raison)
- [x] Erreur imprévue ou Ctrl+C : le bilan est quand même enregistré

### Étape 14 : logs
Un dossier par partie dans `logs/runs/<date-heure>/` :

- [x] `0001.png`, `0002.png`... : la capture de chaque tour (pleine résolution)
- [x] `turns.jsonl` : une ligne par tour avec le mode, l'étage, les raisons de l'appel, l'état lu à l'écran (vie, boutons, ennemis, fenêtre, journal), la décision, et l'appel au modèle (prompt envoyé, image jointe ou non, réponses brutes, tokens, temps, coût). `null` quand aucun appel (ramassage automatique)
- [x] `reasoning.txt` (raisonnement de chaque tour) et `memory.json` (carnet, historique, étage), pour la vidéo
- [x] `run_summary.json` : fin de partie, étage atteint, tours, **ennemis tués**, **objets ramassés**, cause de la mort, **appels, tokens et coût**, durée
- [x] **Total cumulé** : `python budget.py` additionne les parties (`run_summary.json`) et les tests de vision (`results.json`), plus `logs/budget_extra.json` pour les dépenses absentes des logs ; affiche le reste sur 10 $. Le harness l'affiche en fin de session. Estimation : la console Mistral fait foi
  - Au 22/10 : **0,161 $ sur 10 $** selon les logs (43 appels), un peu sous-estimé car les tout premiers tests de vision (20/10) ne sont plus dans `logs/`

---

## Phase 3 : affichage et évaluation (25-26 oct.)

### Étape 15 : l'écran pour la vidéo
- [x] `viewer.py` : une fenêtre (tkinter, 720 px de large) à côté du jeu qui affiche le **mode** (badge de couleur), le tour, l'étage et l'objectif, la vie, la **capture du tour avec le `point_2d` dessiné** (cible rouge : clic ; bleue : examiner), l'**action**, le **raisonnement**, pourquoi le modèle est appelé, le **carnet de notes**, et les appels et le coût cumulés. Texte en anglais (public de la vidéo)
- [x] Elle ne fait que **lire les logs** (`turns.jsonl` et les captures) : aucun lien avec la boucle, aucun appel API
  - **Direct** : `python viewer.py` suit la partie la plus récente (relit `turns.jsonl` toutes les 0,3 s)
  - **Rejeu** : `python viewer.py logs/runs/<date-heure> --replay --delay 2` (Espace : pause ; flèches : tour précédent / suivant). Pratique pour la vidéo : on rejoue la meilleure partie et on l'enregistre avec OBS
- [x] `turns.jsonl` enregistre aussi le carnet de chaque tour, l'étage le plus profond, l'objectif et le modèle
- [x] Placement : à côté du jeu, sans le recouvrir (sinon le harness se met en pause). Sur cet écran (2160 px de large), le jeu centré ne laisse pas la place : `--move-game` le colle en haut à gauche, la fenêtre se met à droite
- [x] Testé sans API : rejeu d'une partie de démonstration, et direct pendant une partie scriptée de 3 tours (le jeu n'est pas recouvert)
- [ ] OBS : ajouter une 2e source « capture de fenêtre » pour `Chonk Dungeon`, à côté du jeu

### Étape 16 : les parties d'évaluation
- [ ] Lancer **3 à 5 parties** avec le même réglage (environ 75 min chacune : on les lance et on fait autre chose)
- [ ] **Seulement s'il reste du budget** : quelques parties **sans carnet de notes** pour comparer
- [ ] Pour la vidéo, **une seule bonne partie suffit** : le jury regarde 2 minutes, pas des statistiques
- [ ] Calculer : étage moyen et maximum, causes de mort, coût moyen par partie

**Partie 1 (22/10, `logs/runs/20261022-140841`)** : arrêtée à la main au tour 40, étage 1, 2 ennemis tués, 39 appels, 0,146 $ (~0,0037 $ par appel : le carnet plein grossit le prompt). Ce qui n'allait pas :
- « attack » choisi 3 fois sans bouton rouge (rat endormi à 3 cases) → **le harness refuse « attack » sans bouton rouge** et l'explique au modèle
- Tours 14 à 40 : coincé à l'entrée d'une petite salle dont le sol est un gouffre. Clics dans le noir (zone inexplorée : rien ne se passe) puis ~15 clics sur le mur au-dessus du héros, avec des cibles toujours un peu différentes, donc la détection « même action 3 fois » ne se déclenchait presque pas → **3 déplacements sans effet de suite, quelles que soient les cibles, déclenchent un message STUCK** qui explique quoi faire
- La fenêtre « sauter dans le gouffre ? » est apparue 5 fois (le modèle a bien répondu non) → **le harness la lit et répond non tout seul**, sans appel : sauter ne compte pas pour l'objectif
- Une note presque à chaque tour, toujours la même idée (« chasm blocks east… »), avec le préfixe d'étage en double → **une note proche d'une note existante la remplace** (40 % de mots communs ; 15 notes → 9 sur cette partie) ; le prompt demande des faits durables, pas ce qu'il fait en ce moment
- Objets ignorés (objet laissé par le serpent, tas d'or) et retour dans la salle de départ → prompt : marcher sur les objets, les cases noires ne se cliquent pas, changer de chemin quand un passage est bloqué
- Ce qui a bien marché : clics précis sur les ennemis, sur la porte du bas et sur le bouton « No » de la fenêtre

**Carte de l'étage (`overview.py`, action « map »)** : le modèle ne voit qu'un écran et oublie ce qu'il a exploré. Le jeu n'a pas de minicarte, mais sa vue se dézoome (touche « - », 2 crans jusqu'au zoom minimal, « = » pour revenir ; la molette est moins fiable). Au zoom minimal, tout l'étage exploré tient à l'écran.
- [x] Le harness dézoome, recadre la capture sur la partie explorée (le noir, l'interface et le texte du journal sont ignorés), l'agrandit à la taille d'une capture normale et entoure le héros en jaune. La carte remplace la capture de ce tour : **même coût**, seulement quand elle sert
- [x] Le modèle peut **cliquer sur la carte** pour traverser l'étage en un clic : le harness convertit le point, clique tant que la vue est dézoomée, puis rezoome
- [x] Ouverte par l'action « map » (le modèle la demande quand il est perdu) et **automatiquement avec le message STUCK** (sans ennemi au contact)
- [x] Testé sans API : clic sur le tas d'or de la carte → le héros traverse l'étage et ramasse l'or, zoom normal retrouvé ; 3 clics inutiles → STUCK + carte automatique, puis zoom normal. Le zoom est aussi remis à la fin de la partie, même après une erreur
- [x] Validé en partie 2 : le modèle l'a demandée 10 fois et ses clics sur la carte font bien bouger le héros

**Partie 2 (22/10, `logs/runs/20261022-151106`)** : arrêtée à la main au tour 74, étage 1, 2 rats tués, 74 appels, 0,272 $. La carte sert et le carnet est propre (3 notes), mais l'exploration reste lente : 17 clics sans effet sur 74 tours (petits pas, clics dans le noir, y compris sur la carte), objets ignorés (clé, coffre, potion), faux STUCK en combat (« attack » 3 fois alors que la vie baisse).

**Déplacements au clavier (action « move »)** :
- [x] Grille mesurée : au zoom normal, une case fait **48 px** (26 × 15 cases à l'écran) et **le héros est toujours sur la case centrale** (à 5 px près sur toutes les captures des parties 1 et 2)
- [x] Le modèle donne une liste de pas (8 directions, 10 au plus) ; le harness les joue un par un et **s'arrête au premier pas bloqué** (l'écran ne bouge pas : mur, obstacle) ou dès que quelque chose change (ennemi, objet, vie, fenêtre, message), puis le dit au modèle. Le clic ne sert plus qu'aux fenêtres et à la carte
- [x] Testé sans API : les 4 directions droites et les 4 diagonales marchent ; « right » ×10 s'arrête au 3e pas sur un tonneau, « down_left » ×4 au 2e pas sur le mur
- [ ] Reconnaître les cases (sol, mur, porte, gouffre…) pour dire au modèle les directions libres et repérer les cibles : étiquetage automatique avec la loupe du jeu, puis classifieur Python (sans coût API)

**Plans d'actions (26/10)**, idées reprises de [heroes-3-mcp](https://github.com/andreinwald/heroes-3-mcp) (autre participant : Heroes 3 via un serveur MCP dans le moteur VCMI, agent Mistral en TypeScript). Lui lit l'état du jeu dans le moteur (cases accessibles, coups légaux) ; nous, seulement l'écran. Ce qui se transpose :
- [x] **Le modèle répond par un plan de 1 à 5 actions** (chez lui, plusieurs appels d'outils par réponse). Le harness les joue dans l'ordre et **arrête le plan dès que la situation change** (`plan_interrupted`) : fenêtre, mort, vie perdue (en combat : seulement sous 35 % ou 30 % perdus depuis le début du plan), bouton d'attaque apparu ou disparu, objet sous le héros, ennemi en vue ou plus en vue, nouveau message (en combat, `wait` et `loot` : seulement les messages colorés). Puis il dit au modèle ce qui a été joué et pourquoi le plan s'est arrêté. Exemples : `attack` ×3 contre un rat, `wait` ×3 près d'une porte en attendant un serpent, `loot` puis `move`
  - `click`, `examine` et `map` se jouent **seuls** : le modèle doit voir leur résultat avant la suite (le reste du plan est écarté, et on le lui dit)
  - Gain attendu : moins d'appels en combat (7 appels pour un serpent dans la partie 1) et pour les petites suites évidentes
- [x] **But courant** (champ `goal`) : l'objectif du moment, rappelé au modèle à l'appel suivant, effacé au changement d'étage. Chez lui, c'est l'historique de conversation (100 messages) qui garde l'intention ; trop cher pour nous avec les images, ce champ en garde l'essentiel pour quelques tokens
- [x] **Actions utilisables maintenant** dans chaque message (« Usable now: ... attack (red button shown) »), comme ses lignes `attack=ids|none wait=yes` : le modèle sait d'avance ce qui sera refusé
- [x] **Nouvel essai sur les erreurs 5xx et les coupures réseau** (pas seulement 429) : avant, une erreur 503 arrêtait la partie
- Testé sans jeu ni API (décodage du plan, interruptions, découpage, refus, carnet, viewer). **Reste à tester dans le jeu** : `python harness.py --brain scripted --script "move right right + wait + move left left"`
- Pas repris : ses outils d'observation séparés (`get_map`, `get_battle`...), inutiles quand chaque appel reçoit déjà la capture ; son historique de conversation complet (trop de tokens avec des images)

---

## Phase 4 : livraison (27-28 oct.)

### Étape 17 : la vidéo (2 minutes maximum)
| Temps | Contenu |
|---|---|
| 0:00-0:10 | Accroche : un combat avec le raisonnement affiché |
| 0:10-0:30 | Le principe : capture, ML4, action, avec les points dessinés |
| 0:30-1:30 | Moments forts en accéléré |
| 1:30-1:50 | Goo, ou le plus loin atteint |
| 1:50-2:00 | Résultats des parties d'évaluation et lien du dépôt |

### Étape 18 : le README (en anglais)
- [ ] Ce que fait le projet, en 3 lignes, avec un GIF
- [ ] Installation et lancement
- [ ] Architecture : un schéma de la boucle et des modes
- [ ] Résultats et statistiques
- [ ] **Crédits** : Shattered Pixel Dungeon par Evan Debenham (GPL-3.0), basé sur Pixel Dungeon de Watabou. Préciser que le jeu n'est pas inclus dans le dépôt et doit être téléchargé séparément.

### Étape 19 : soumission sur Discord (#challenges-submissions)
- [ ] La vidéo (2 min max) ou un GIF
- [ ] Le lien du dépôt GitHub
- [ ] Une courte description **en anglais**
- [ ] **Une seule soumission** : on poste une fois, quand tout est prêt

---

## Conseils

1. **Fais marcher une version moche de bout en bout le plus tôt possible** (capture, ML4, une touche), avant de soigner quoi que ce soit. Un harness simple qui joue vaut mieux qu'un harness parfait qui ne joue pas le 28.
2. **Ne touche plus à la taille de la fenêtre ni au zoom** une fois les détecteurs calés dessus.
3. **Teste les détecteurs sur tes captures de référence** avant de les brancher sur la vraie boucle.
4. **Garde un œil sur le coût dès le premier jour**, avec les logs de tokens. Avec 10 $ pour tout le concours, une partie trop chère, c'est moins de parties d'évaluation. Respecte la répartition du budget.
5. **Enregistre tout avec OBS** : les meilleurs moments de la vidéo arriveront par surprise.
6. **Écris le README au fil de l'eau**, pas le dernier soir.
7. **Committe chaque soir**, même si ça ne marche pas encore.
8. **Si tu prends du retard**, coupe dans cet ordre : la comparaison sans carnet, puis l'écran pour la vidéo (le terminal suffit), puis l'objectif Goo. Ne coupe jamais les logs ni la vidéo.

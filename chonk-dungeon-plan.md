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
- [ ] Prompt système : objectif, règles du jeu de base, table des touches, format de réponse
- [ ] Réponse en **texte libre**, actions extraites par regex (recommandation du cookbook Mistral)
- [ ] Format des actions : `{"key": "..."}`, `{"point_2d": [x, y], "label": "..."}`, `{"action": "attack"}`, `{"action": "loot"}`, `{"action": "examine", "point_2d": [x, y]}`
- [ ] Le modèle a le droit de répondre **« inconnu »** pour un élément au lieu d'être forcé de trancher (créature ou non, escalier ou non) : un élément inconnu proche ou sur le chemin → examiner
- [ ] Un champ `reasoning` court, affiché dans la vidéo

### Étape 12 : la mémoire
- [ ] **Carnet de notes** : le modèle peut ajouter ou modifier des notes (objets identifiés, dangers, plan)
- [ ] Ce qu'il apprend en examinant va dans le carnet (ex. « planches marron sur l'eau = pont »), pour ne pas réexaminer la même chose à chaque étage
- [ ] **Historique court** : les 5 à 10 dernières actions en texte
- [ ] Pas d'historique d'images : **une seule capture par appel**

### Étape 13 : filets de sécurité
- [ ] Même action 3 fois sans changement à l'écran : Échap automatique et message « tu es bloqué »
- [ ] Réponse illisible : on réessaie une fois, puis action par défaut (attendre)
- [ ] Mort : on enregistre les statistiques et on relance une partie
- [ ] Erreur **429** (limite de tokens par minute) : attendre quelques secondes puis réessayer, sans faire planter la partie
- [ ] **Compteur de coût** : arrêter proprement la partie si le budget fixé pour elle est dépassé

### Étape 14 : logs
- [ ] Un dossier par partie : capture, prompt, réponse, action, tokens et temps, pour chaque tour
- [ ] Un `run_summary.json` : étage atteint, tours, ennemis tués, cause de la mort, **nombre d'appels et coût total**
- [ ] Un total cumulé de toutes les parties, pour suivre le budget restant

---

## Phase 3 : affichage et évaluation (25-26 oct.)

### Étape 15 : l'écran pour la vidéo
- [ ] Une fenêtre ou une page à côté du jeu qui affiche : le mode actuel, le raisonnement, le carnet de notes et le dernier `point_2d` dessiné sur la capture

### Étape 16 : les parties d'évaluation
- [ ] Lancer **3 à 5 parties** avec le même réglage (environ 75 min chacune : on les lance et on fait autre chose)
- [ ] **Seulement s'il reste du budget** : quelques parties **sans carnet de notes** pour comparer
- [ ] Pour la vidéo, **une seule bonne partie suffit** : le jury regarde 2 minutes, pas des statistiques
- [ ] Calculer : étage moyen et maximum, causes de mort, coût moyen par partie

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

"""Chonk Dungeon : un harness qui fait jouer un modèle Mistral à Shattered Pixel Dungeon.

Modules :
    harness     la boucle : lire l'écran, appeler le cerveau, jouer son plan
    brain       le prompt, le schéma JSON de la réponse, l'appel au modèle
    llm         le client Mistral (limite de débit, nouvelles tentatives, coût)
    memory      l'objectif, l'historique des actions, le carnet de notes
    game_state  l'état exact exporté par le Chonk build : carte, cibles, sac
    capture     la capture de la fenêtre du jeu
    detectors   ce qu'on lit à l'écran (vie, boutons, fenêtres, mort)
    ocr         la lecture du journal du jeu (police pixel)
    overview    la carte dézoomée de l'étage
    actions     le clavier et la souris
    budget      le coût des parties, d'après les logs
    viewer      l'écran pour la vidéo (direct ou rejeu)
"""

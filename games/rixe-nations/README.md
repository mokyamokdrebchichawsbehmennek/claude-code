# Rixe Nations

Jeu de stratégie en temps réel jouable dans le navigateur (téléphone et ordinateur).

- `index.html` : tout le code du jeu.
- `unites.json` : les données (unités, bâtiments, économie, IA, cartes).
- Les images (`img/…`) ne sont pas dans ce dépôt : elles sont publiées avec l'artefact
  « Escarmouche », qui est la version jouable.

## Derniers changements

- **Vétérans** : une unité qui abat 2, 5 puis 10 ennemis monte en grade (chevrons dorés),
  avec plus de dégâts et de PV. Le grade survit à une évacuation par l'escouade de secours
  et aux sauvegardes.
- **Production en boucle** : le bouton « ↻ Boucle » d'un bâtiment remet en file chaque unité
  sortie, dès que les ressources et le logement le permettent.
- **Alerte de vague** : le bandeau d'objectif signale la vague en préparation et l'assaut en cours.
- **Nom de l'adversaire** : les messages citent la nation réellement affrontée (ils disaient
  toujours « Reptiliens », même en jouant les Reptiliens).
- **Population** : les policiers et miliciens des refuges ne bloquent plus la sortie des unités.

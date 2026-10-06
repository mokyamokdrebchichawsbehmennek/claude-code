# Luciole

Petit jeu d'arcade pour navigateur, en un seul fichier HTML (canvas + JavaScript, sans dépendance).

Tu es une luciole perdue dans une forêt de nuit. Ta lumière faiblit à chaque seconde :
bois les gouttes de nectar pour la raviver et évite les araignées qui descendent des branches.
Dans le noir, seuls leurs yeux rouges les trahissent. Toutes les 25 secondes, une nouvelle
nuit commence et les araignées deviennent plus nombreuses et plus rapides.

## Jouer

Ouvre `index.html` dans un navigateur.

| Action      | Commande                                  |
| ----------- | ----------------------------------------- |
| Se déplacer | souris, doigt, flèches, ZQSD ou WASD       |
| Pause       | `P` ou `Échap`                            |
| Son         | `M`                                       |
| Jouer       | `Entrée` ou `Espace`                      |

## Règles

- 3 vies ; une araignée touchée coûte une vie et un peu de lumière.
- La partie s'arrête quand tu n'as plus de vies ou plus de lumière.
- Score : 1 point par seconde de survie, 10 points par goutte de nectar.
- Le record est gardé dans le navigateur (`localStorage`).

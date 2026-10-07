# Pool agentique : comité de direction jeu vidéo

Un comité de direction simulé par des agents Claude Code, pour éclairer les décisions d'un studio de jeu vidéo mobile et PC.

## Contenu

| Fichier | Rôle |
|---|---|
| `pool-agentique/MEMOIRE.md` | Mémoire partagée : contexte, règles, fiches de rôle, gouvernance, journal des décisions. Chaque agent la lit avant de répondre. |
| `.claude/agents/codir-*.md` | Un sous-agent par rôle (CEO, COO, VPM, DMD, DAF, DCO, DSI, DJL, DPR, DST-C, DST-M) et par zone de distribution (`codir-ddp-eu`, `-na`, `-jp`, `-kr`, `-cn`, `-sea`, `-in`, `-latam`, `-tr`, `-mena`). |
| `.claude/commands/codir.md` | Commande `/codir` : l'orchestrateur qui choisit les agents, collecte les avis, fait synthétiser par le COO, arbitrer par le CEO, puis consigne la décision. |

## Utilisation

1. Remplissez la section 1 de `MEMOIRE.md` (ou laissez `/codir` vous poser les questions).
2. Lancez une séance dans Claude Code, à la racine du dépôt :

```
/codir revue Faut-il un battle pass dès le soft launch ?
/codir codir Go/no-go du soft launch aux Philippines et au Canada
/codir premortem Lancement global prévu en mars
/codir avocat On met 80 % du budget UA sur TikTok
```

Sans mode explicite, `/codir` fait une revue ciblée.

3. Validez ou modifiez la décision proposée. L'orchestrateur l'inscrit alors au journal (section 10) et met à jour les hypothèses et les sources.

## Limites

- Les agents ne remplacent ni un avocat, ni un expert-comptable, ni des données réelles. Les points marqués `[À VÉRIFIER]` doivent être confirmés sur une source officielle.
- Un CODIR complet mobilise jusqu'à une vingtaine d'agents : préférez la revue ciblée pour les questions courantes.

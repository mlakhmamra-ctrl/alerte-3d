# ALERTE 3D — décision collective pendant une crue

Projet « Mondes Vivants » (5A IABD, thématique ESSAIM). Les habitants d'un quartier fictif, Rive-Claire, reçoivent
pendant une crue des alertes de la mairie, des rumeurs et des messages de leurs voisins. Sans chef, chacun doit décider
**quand partir, par où, et vers quel refuge**. Une partie d'entre eux raisonne avec un **LLM**, les autres suivent des
**règles explicites**. Chaque décision suit un graphe **LangGraph**, la mémoire et le RAG des habitants sont dans
**PostgreSQL**, tout passe par **Kafka**, et la scène 3D est dans **Godot**.

## État du projet

| | |
|---|---|
| **Population** | 100 habitants par défaut : les 12 de la fiche + des voisins générés, en foyers de 2 à 3 personnes (jusqu'à 400) |
| **Qui pense avec un LLM** | 20 habitants par défaut (4 de la fiche + 16 générés), les 80 autres suivent des règles |
| **LLM utilisés** | Ollama en local (`qwen2.5` de 0.5b à 7b) ou API compatibles OpenAI (Groq) ; embeddings `nomic-embed-text` |
| **Décision des agents** | un graphe LangGraph : observer → se souvenir (RAG) → décider (LLM) → valider → agir, avec secours par les règles |
| **Mémoire et RAG** | dans PostgreSQL (pgvector) pendant le run : souvenirs, messages, tableau commun du quartier, documents |
| **Quartier** | plan de la fiche au centre + quartiers Ouest et Est, 5 refuges (99 places) ; crue lente, rapide ou éclair |
| **Runs enregistrés** | 64 runs complets, dont **51 avec de vrais appels LLM** (Ollama et Groq) |
| **Étude des paramètres** | 105 runs à règles (21 configurations × 5 graines) : [docs/analyse_parametres.md](docs/analyse_parametres.md) |
| **Démos PostgreSQL** | mémoire individuelle, partagée et collective, optimisation, RAG et graphe : [docs/memoire_postgresql.md](docs/memoire_postgresql.md) |

## Architecture

```
 ┌──────────────── Docker ───────────────────────────────────────────────────────┐
 │  Ollama / API  ◄──  sim (Python)  ──événements──►  Kafka  ──►  gateway  ──────┼──WebSocket──►  Godot 4
 │  LLM, embeddings    habitants (LangGraph),         alerte.events    (indicateurs) │   ws://127.0.0.1:8765
 │                     crue, rues                     alerte.commands ◄──────────────┼── commandes (alerte, rumeur…)
 │                      │        ▲                                                   │
 │        mémoire, RAG  ▼        │                                                   │
 │                     PostgreSQL + pgvector (port 5440)       data/runs/<run_id>.jsonl │
 └───────────────────────────────────────────────────────────────────────────────┘
          admin/ (gestion des villageois)  ·  analyse/ (analyse des runs, dans le navigateur)
```

| dossier | rôle |
|---|---|
| `sim/` | la simulation : habitants (règles ou LLM), graphe de décision LangGraph, crue, rues, messages, mémoire et RAG (PostgreSQL), indicateurs |
| `gateway/` | relaie Kafka vers Godot en WebSocket et calcule les indicateurs en flux |
| `godot/` | la scène 3D (maquette du quartier, eau, habitants, panneaux d'information) |
| `admin/` | page web de gestion des villageois : modifier les habitants, en ajouter en lot, relancer |
| `analyse/` | page web d'analyse des runs : bilan, rejeu, suivi de chaque habitant, comparaison de paramètres |
| `postgres/` | la mémoire des habitants dans PostgreSQL : schéma et fonctions du RAG, chargement des runs, démos de requêtes |
| `tools/` | étude des paramètres, analyse de lots de runs, faux LLM de test |
| `docs/` | architecture, contrat d'événements, résultats |

## Lancer

Il faut **Docker Desktop**, et **Godot 4.3 ou plus récent** (version standard) pour la scène 3D.

| commande (Windows) | ce qu'elle fait |
|---|---|
| `lancer.bat` | Kafka, Ollama, PostgreSQL, simulation, passerelle. Premier lancement : téléchargement des modèles (environ 6 Go) |
| `lancer_gpu.bat` | la même chose, avec Ollama sur la carte NVIDIA (à utiliser aussi pour chaque relance) |
| `lancer_api.bat` | sans Ollama : les agents LLM utilisent les API listées dans `LLM_BACKENDS` (clés dans `.env`) |
| `lancer_demo.bat` | démo en direct, accélérée : 09:00 → 10:00 en 12 minutes (carte NVIDIA) |
| `lancer.bat faux` | sans aucun modèle : un faux LLM à heuristiques, pour tester la chaîne (ne pas présenter comme des résultats LLM) |
| `lancer_analyse.bat` | ouvre l'application d'analyse des runs |
| `lancer_postgres.bat` | la base PostgreSQL de la simulation, avec les runs d'exemple chargés, puis psql (`reset` pour repartir de zéro) |

Sous Mac/Linux : `./lancer.sh` (ou `./lancer.sh gpu`), ou directement `docker compose up --build`.

Puis dans Godot : **Importer** → `godot/project.godot` → **F5**. La scène se connecte seule à la simulation.
Arrêt : Ctrl+C, puis `docker compose down`.

## Réglages

Les valeurs par défaut sont dans `.env.exemple`. Pour en changer, créez un fichier `.env` avec seulement les lignes
modifiées : il est ignoré par git, c'est là que vont les clés d'API (`LLM_API_KEY`, `LLM_GROQ_KEY`…).

| variable | rôle | défaut |
|---|---|---|
| `N_AGENTS` | nombre d'habitants | `100` |
| `LLM_AGENTS`, `LLM_EXTRA` | habitants de la fiche qui pensent avec un LLM, et nombre d'habitants générés en plus (`none` = 100 % règles) | `A04,A05,A06,A11`, `16` |
| `LLM_BIG`, `LLM_SMALL` | les deux tailles de modèle | `qwen2.5:7b`, `qwen2.5:0.5b` |
| `LLM_BACKENDS` | où envoyer les appels LLM (`local`, `groq`…, plusieurs possibles) | `local` |
| `SCENARIO` | `pont_ferme` (pont fermé à 09:20) ou `pont_ouvert` (la rumeur est fausse) | `pont_ferme` |
| `CRUE` | vitesse de montée de l'eau : `lente`, `rapide`, `eclair` | `rapide` |
| `UPDATE_AT`, `RUMOR_AT` | heure de la mise à jour de la mairie et de la rumeur | `09:30`, `09:20` |
| `PANNE_RESEAU` | heure de coupure du réseau mobile, ou `none` | `none` |
| `MODE` | `async` (chacun à son rythme) ou `sync` (pas de 30 s) | `async` |
| `TIME_SCALE` | vitesse du temps (1 = temps réel, un run dure 1 h) | `1` |
| `MONDE` | `etendu` (3 quartiers, 5 refuges) ou `fiche` (plan exact de la fiche, 2 refuges) | `etendu` |
| `MEMOIRE` | `postgres` (mémoire et RAG dans PostgreSQL, repli automatique en RAM) ou `ram` | `postgres` |

Les habitants peuvent aussi être modifiés pendant la simulation avec `admin/index.html` (touche **H** dans Godot).

## Dans Godot

Le quartier est une maquette suspendue ; les textes sont dans les panneaux autour de la scène (touche **I** pour les masquer).
Gilet = état de l'habitant (gris chez soi, orange prépare son départ, blanc en route, rouge bloqué, vert à l'abri),
orbe au-dessus de la tête = agent LLM, impulsions entre habitants = messages. Clic sur un habitant : profil, croyances,
dernière décision et sa justification, documents retrouvés, journal de mémoire.

Touches principales : **A** alerte · **U** rumeur · **B** fermer le pont · **K** couper le réseau · **Espace** pause ·
**R** relancer · **+ / −** vitesse du temps · **V** vue à la première personne · **H** gestion des villageois.

## Décision et mémoire des habitants

Chaque réflexion d'un habitant est un passage dans un **graphe LangGraph** (`sim/graphe.py`) :

```
observer ──┬── agent à règles ───────────────────────────────────────► regles ──► agir
           └── agent LLM ──► se_souvenir ──► decider ──┬── réponse invalide ──┘   ▲
                             (RAG PostgreSQL)           └── réponse valide ──► croire
```

Chaque habitant garde un **journal** de ce qu'il a vu, fait et entendu, copié en direct dans PostgreSQL. Avant de décider,
un agent LLM y retrouve (nœud `se_souvenir`) :
- **mémoire individuelle** : ses 3 souvenirs les plus utiles, jamais un souvenir postérieur à l'heure de sa réflexion ;
- **mémoire partagée** : les messages reçus, entrés dans son journal à l'heure où il les a lus ;
- **mémoire collective** : s'il est inscrit au groupe du quartier et que le réseau passe, ce que dit le quartier
  (« le pont est fermé : 12 messages, 7 sources ») ;
- **connaissances** : les 2 documents du quartier les plus pertinents (plein texte et pgvector).

Le rappel des souvenirs mêle les **mots** et le **sens** : chaque souvenir a son vecteur (`nomic-embed-text`, 768 nombres),
calculé en arrière-plan pendant le run, une fois par contenu. Sur un banc de questions reformulées, un souvenir pertinent
arrive dans les 3 premiers dans **90 %** des cas, contre **17 %** avec les mots seuls (`python eval_rag.py souvenirs`).

Si PostgreSQL ne répond pas, la simulation continue avec la mémoire en RAM. Chaque décision (`agent.think`) garde ce que
le RAG a retrouvé et le chemin suivi dans le graphe. Démos pour psql et pgAdmin (trois mémoires, optimisation, RAG et
graphe) : [docs/memoire_postgresql.md](docs/memoire_postgresql.md).

## Analyser

Chaque run est enregistré en entier dans `data/runs/<run_id>.jsonl` (seul le run d'exemple est versionné).

- **Application d'analyse** (`lancer_analyse.bat`) : bilan d'un run, rejeu sur la carte, suivi complet de chaque habitant,
  comparaison de plusieurs runs et effet des paramètres.
- **Étude des paramètres** : `python tools/etude_parametres.py run`, puis `rapport` (les runs vont dans `data/etude/`, non versionné).
- **Spark** (`lancer_spark.bat batch`) : analyse distribuée de tous les runs (`data/runs`, `data/etude`), avec des tables
  Parquet et un rapport `data/spark/rapport_spark.md` (indicateurs par configuration, comportements par profil, diffusion
  des messages). `lancer_spark.bat flux` branche Spark Structured Streaming sur Kafka pendant un run : agrégats par
  fenêtre de 10 s dans le topic `alerte.spark`. Voir [docs/spark.md](docs/spark.md).
- **Rejouer un run sans LLM** :
  `docker compose --profile replay run --rm -e REPLAY_FILE=/data/runs/exemple_regles_pont_ferme.jsonl replay`

Principaux résultats de l'étude (agents à règles) :
- **l'heure de l'alerte décide de tout** : en crue lente, 98 % atteignent un refuge avec une alerte à 09:01, 25 % avec une alerte à 09:20 ;
- **les victimes sont toujours des personnes qui ne peuvent pas monter à l'étage** ;
- **le temps de préparation et la taille du foyer ralentissent l'évacuation** (on part ensemble, au rythme du plus lent) ;
- la rumeur n'a pas d'effet sur les agents à règles : son effet est à chercher du côté des agents LLM.

## Limites

- L'étude des paramètres ne porte que sur des agents à règles ; la comparaison systématique règles / LLM reste à faire
  (un run réel avec Ollama dure environ 1 h, et les API gratuites ont des quotas).
- Plusieurs écarts à la fiche sont assumés et réglables : quartier étendu, population de 100, vitesse de crue.
- Les seuils de danger et de dégâts sont des paramètres de maquette, pas des données réelles.
- Le RAG et le graphe ont été testés avec le faux LLM (run complet de 100 habitants, dans Docker) ; un run avec les vrais
  modèles reste à faire avant la soutenance.
- Le banc des embeddings est petit (8 questions, 69 cas, un run) : le poids du sens (0,5) est un premier réglage, pas une optimisation.

## Documentation

- [docs/architecture.md](docs/architecture.md) : lecture ESSAIM, choix des agents et des LLM, modèle de temps, exigences couvertes
- [docs/evenements.md](docs/evenements.md) : contrat des événements Kafka
- [docs/analyse_parametres.md](docs/analyse_parametres.md) : étude des paramètres et des comportements
- [docs/resultats_baseline_regles.md](docs/resultats_baseline_regles.md) : premiers résultats 100 % règles
- [docs/memoire_postgresql.md](docs/memoire_postgresql.md) : la mémoire des habitants dans PostgreSQL, et la démo
- [docs/spark.md](docs/spark.md) : Spark en batch (analyse des runs) et en streaming (flux Kafka)

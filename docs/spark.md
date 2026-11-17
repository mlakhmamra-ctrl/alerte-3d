# Spark dans ALERTE 3D

Spark (3.5.3, image officielle `apache/spark`) est utilisé de deux façons :

| job | entrée | sortie | rôle |
|---|---|---|---|
| **batch** [spark/analyse_spark.py](../spark/analyse_spark.py) | tous les journaux `data/runs/*.jsonl` et `data/etude/<config>/*.jsonl` | tables Parquet et `rapport_spark.md` dans `data/spark/` | analyse de tous les runs enregistrés : indicateurs par configuration, comportements par profil, diffusion des messages |
| **streaming** [spark/flux_spark.py](../spark/flux_spark.py) | topic Kafka `alerte.events`, en direct | topic Kafka `alerte.spark`, et la console du conteneur | agrégats par fenêtre de 10 s pendant que la simulation tourne |

```
                         ┌──────────── pendant le run ─────────────┐
 sim ──► Kafka alerte.events ──► gateway (indicateurs métier, événement par événement) ──► Godot
                │
                └──► Spark Structured Streaming (fenêtres de 10 s, filigrane 30 s) ──► Kafka alerte.spark
 sim ──► data/runs/*.jsonl ─┐
 étude ─► data/etude/**.jsonl ┴──► Spark batch ──► data/spark/ : Parquet (argent, or) + rapport_spark.md
                         └──────────── après les runs ─────────────┘
```

## Lancer

```
lancer_spark.bat batch      # = docker compose --profile spark-batch run --rm spark-batch
lancer_spark.bat flux       # = docker compose --profile spark up spark-flux   (lancer la simulation à côté)
docker compose logs -f spark-flux                                             # fenêtres en direct
```

Les deux services ont un *profil* compose : ils ne démarrent pas avec `lancer.bat`. Au premier lancement de `flux`, Spark
télécharge le connecteur Kafka (`spark-sql-kafka-0-10`), qu'il garde ensuite dans le volume `ivy`.

## Le job batch : bronze, argent, or

Les données sont organisées en trois niveaux :

1. **bronze** : les fichiers JSONL tels que la simulation les écrit, avec un événement par ligne et l'enveloppe `alerte.event/1`.
2. **argent** : `data/spark/evenements/`, les événements lus, typés et **partitionnés par type** en Parquet. Une requête
   sur `type=agent.say` ne lit que ce dossier. Les positions (`world.tick`) et les grilles d'eau (`flood.update`)
   représentent l'essentiel du volume mais ne servent à aucun bilan : elles sont comptées puis écartées.
3. **or** : les tables d'analyse.

| table | une ligne par… | construite à partir de |
|---|---|---|
| `runs` | run terminé | `run.end` (paramètres, indicateurs, appels LLM) |
| `par_config` | configuration : moyenne et écart-type sur les graines | `runs` |
| `habitants` | habitant et par run | `run.start` (profil), joint à `run.end` (état final) et `agent.victim` |
| `par_profil` | trait de profil et valeur | `habitants` |
| `diffusion_minute` | configuration, minute simulée et sorte de message | `agent.say` |
| `volume` | type d'événement | tous les événements |

Points techniques :
- le champ `data` change selon le type d'événement. Il est lu en **texte JSON brut**, et chaque table n'en extrait que ce qu'il
  lui faut (`from_json` avec un petit schéma, ou `get_json_object`) ;
- **aucune UDF Python** : tous les calculs restent dans le moteur de Spark (JVM), sans aller-retour avec Python ;
- les événements utiles sont **mis en cache** une seule fois, puis réutilisés par toutes les tables ;
- avant l'écriture partitionnée, un `repartition("type")` fait que chaque tâche n'ouvre qu'un seul écrivain Parquet. Sans lui, le
  premier essai saturait les 3 Go de mémoire : chacune des 24 tâches ouvrait un écrivain par type d'événement, soit plusieurs centaines d'écrivains en même temps.

**Mesure** (PC de développement, 24 cœurs, Docker Desktop) : 249 fichiers, 797 Mo, 1 074 348 événements, 220 runs terminés,
traités en **95 s**, écriture des tables comprise.

Lire les tables ailleurs (pandas, DuckDB, Spark) : `pd.read_parquet("data/spark/par_config")`.

## Le job streaming

Spark lit `alerte.events` au fil de l'eau, groupe les événements par **fenêtre de 10 s** (temps réel d'émission `t_proc`)
et par run, puis publie chaque fenêtre en JSON dans `alerte.spark` :

```json
{"debut":"…T20:48:40Z","fin":"…T20:48:50Z","run_id":"3cef02f5","evenements":186,"messages":15,"departs":0,"arrivees":0,
 "victimes":0,"decisions_llm":8,"incidents_llm":2,"niveau_eau_max":-0.258,"heure_simulee":"09:02"}
```

- **filigrane (watermark) de 30 s** : Spark attend les événements en retard pendant 30 s au plus, puis fige la fenêtre et
  libère sa mémoire ;
- **mode `update`** : une fenêtre est republiée chaque fois qu'elle change. Le consommateur garde la dernière version
  (clé Kafka = `run_id`, et la fenêtre `debut`) ;
- **checkpoint** (`data/spark/checkpoints/`) : après un redémarrage, Spark reprend aux offsets Kafka où il s'était arrêté.

Ce job complète la passerelle, il ne la remplace pas. La passerelle calcule les indicateurs **métier** événement par
événement (contexte commun, convergence…) et les envoie à Godot. Spark calcule des agrégats **par fenêtre de temps**,
de la façon dont on traiterait un flux beaucoup plus gros réparti sur plusieurs machines.

Réglages (variables d'environnement du service) : `FENETRE` (10 seconds), `RETARD` (30 seconds), `TOPIC_ENTREE`, `TOPIC_SORTIE`.

## Tests

[tests/test_spark.py](../tests/test_spark.py) lance les deux jobs sur le run d'exemple versionné. Les tables doivent
retrouver le bilan écrit par la simulation (12 habitants, 11 arrivés, départ moyen 21,3 min), et l'agrégation du flux doit
compter chaque départ et chaque arrivée une seule fois. Ces tests sont sautés sans PySpark ou sans Java. La CI installe Java 17.

Sous Windows, PySpark seul ne peut pas lire de fichiers sans les binaires Hadoop `winutils.exe` et `hadoop.dll`. Pour
lancer les tests en local, passez par le conteneur :

```
docker run --rm -u root -v "%cd%:/repo" -w /repo apache/spark:3.5.3-scala2.12-java17-python3-ubuntu bash -c ^
  "export PYTHONPATH=/opt/spark/python:$(ls /opt/spark/python/lib/py4j-*.zip); pip install -q pytest==8.3.4; python3 -m pytest tests/test_spark.py"
```

## Limites

- Spark tourne en mode **local** (`local[*]`), c'est-à-dire un seul conteneur qui utilise tous les cœurs. Le même code
  tournerait sur un cluster (`--master spark://…` ou Kubernetes) sans modification, mais ce n'est pas démontré ici.
- Le volume (0,8 Go) reste modeste : Spark est surtout utile ici pour le parallélisme, le format Parquet et le streaming
  fenêtré. Un script Python suffit encore pour quelques runs (`tools/analyse_runs.py`).
- Les vieux runs de `data/runs/` n'enregistraient pas tous les paramètres (crue, personnes à l'étage) : ils apparaissent en
  « crue ? » ou « — » dans le rapport.
- Le tableau « comportements par profil » mélange toutes les configurations. Pour isoler un effet, filtrez la table
  `habitants` sur une configuration.
- La passerelle ne relaie pas encore `alerte.spark` vers Godot.

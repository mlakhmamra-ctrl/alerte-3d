# ALERTE 3D : démarche de génie logiciel

Ce document présente les choix de génie logiciel du projet : comment le système est découpé, comment ses parties
communiquent, comment on vérifie qu'il fonctionne et qu'il continue de fonctionner après une modification.
Les détails techniques de chaque brique sont dans [architecture.md](architecture.md) et [evenements.md](evenements.md).

## 1. Architecture

### 1.1 Des services indépendants reliés par des événements

Le système est découpé en services qui tournent chacun dans leur conteneur :

| service | rôle |
|---|---|
| `sim` (Python) | simule la crue, les rues et les habitants ; publie tout ce qui se passe sous forme d'événements |
| `gateway` (Python) | valide les événements, calcule les indicateurs en flux, relaie vers Godot par WebSocket |
| `kafka` | bus de messages entre les services |
| `ollama` | modèles de langage (décisions des habitants LLM, embeddings) |
| `postgres` + pgvector | mémoire des habitants et recherche documentaire (RAG) |
| Godot 4 (sur le PC) | vue 3D, inspecteur d'agent, envoi de commandes (alerte, rumeur, pause) |

Les services ne s'appellent jamais directement. Ils publient et consomment des messages sur des topics Kafka
(`alerte.events`, `alerte.commands`, `alerte.indicators`, `alerte.dlq`). On obtient ainsi une **architecture orientée
événements** (patron *publish / subscribe*) où les services sont **faiblement couplés**. Par exemple, on peut remplacer la
vue Godot ou ajouter un nouveau consommateur d'indicateurs sans toucher à la simulation.

### 1.2 Patrons de conception

- **Passerelle (Gateway / Adapter)** : [gateway/main.py](../gateway/main.py) traduit Kafka en WebSocket. Le client 3D n'a
  pas besoin de connaître Kafka.
- **Graphe d'états** : le cycle de décision d'un habitant est un graphe LangGraph ([sim/graphe.py](../sim/graphe.py)),
  `observer → se_souvenir → decider → croire / regles → agir`. Chaque étape est un nœud et chaque choix est une arête
  conditionnelle. La logique est donc explicite, et l'événement `agent.think` indique le chemin suivi dans le graphe.
- **Stratégie interchangeable avec repli** : un habitant décide soit par des règles explicites, soit par un LLM. Si le
  LLM ne répond pas ou répond mal (JSON invalide, sortie de rôle), les règles prennent le relais. L'incident est tracé
  (`llm.incident`) et aucune exception ne remonte. La mémoire suit le même principe : PostgreSQL par défaut, mémoire en
  RAM ([sim/rag.py](../sim/rag.py)) si la base est indisponible.
- **File de lettres mortes (Dead Letter Queue)** : un événement non conforme est déposé dans `alerte.dlq` au lieu
  d'arrêter la chaîne.
- **Écritures asynchrones par file d'attente** : la mémoire PostgreSQL ([sim/memoire_pg.py](../sim/memoire_pg.py))
  écrit dans l'ordre, sur une seule connexion, sans jamais faire attendre la simulation.

### 1.3 Séparation des responsabilités et réutilisation

Chaque module Python a une seule responsabilité : le monde et la crue (`world.py`), les habitants (`agents.py`), les
profils et la génération de population (`profiles.py`), l'accès aux modèles (`llm.py`), la recherche documentaire
(`rag.py`), les indicateurs (`indicators.py`). Le module des indicateurs est **le même code** dans la simulation et dans
la passerelle. Les deux calculs ne peuvent donc pas diverger.

## 2. Contrat d'interface versionné

Tous les événements respectent le contrat **`alerte.event/1`**, documenté dans [evenements.md](evenements.md) :

- une enveloppe commune (`schema`, `run_id`, `seq`, `type`, `t_sim`, `t_proc`, `data`) ;
- pour chaque type d'événement, la liste de ses champs obligatoires ;
- un numéro `seq` sans trou dans un run, ce qui permet de détecter une perte de message.

La passerelle **valide chaque message à l'exécution** (fonction `valid`). Le contrat est **versionné** : si un champ
obligatoire change, on passe à `alerte.event/2`, et la passerelle accepte les deux versions pendant la transition. Un
test unitaire vérifie que la validation accepte un événement correct et rejette un type inconnu, une version inconnue ou
des données incomplètes.

## 3. Vérification et validation

### 3.1 Trois niveaux de tests (pytest)

| niveau | fichier | ce qui est vérifié |
|---|---|---|
| **Unitaire** (23 tests) | [tests/test_unitaires.py](../tests/test_unitaires.py) | montée de la crue, ralentissement dans l'eau, calcul des chemins et détours, génération de population, réglages bornés, indicateurs, RAG lexical, détection des sorties de rôle du LLM, contrat d'événements |
| **Non-régression** | [tests/test_reproductibilite.py](../tests/test_reproductibilite.py) | quatre scénarios de référence rejoués avec une graine fixe ; l'empreinte SHA-256 de tous leurs événements doit être identique à celle enregistrée dans [tests/empreintes.json](../tests/empreintes.json) |
| **Intégration (test de fumée)** | [tests/test_chaine_llm.py](../tests/test_chaine_llm.py) | la chaîne LLM complète tourne jusqu'au bout, en passant par les bons nœuds du graphe, et les réponses invalides sont rattrapées |
| **Jobs Spark** | [tests/test_spark.py](../tests/test_spark.py) | les tables du job batch retrouvent le bilan écrit par la simulation, et l'agrégation du streaming compte chaque départ une seule fois |

Le test de non-régression suit le principe du *golden master* : un **refactoring ne doit changer aucune empreinte**.
Quand un changement de comportement est voulu, on régénère explicitement les empreintes (`MAJ_EMPREINTES=1`). Le test fixe
aussi `PYTHONHASHSEED` à une valeur différente à chaque fois, pour s'assurer que l'ordre interne des ensembles Python
n'influence pas le résultat.

### 3.2 Doublure de test et injection de fautes

[tools/fake_ollama.py](../tools/fake_ollama.py) est un **faux Ollama** : il imite l'API du serveur de modèles sans GPU et
sans modèle. Il produit **volontairement** des pannes (JSON cassé, sortie de rôle, latence) à un taux réglable. Cela
permet de tester en conditions réelles la robustesse du mécanisme de repli. Ses réponses ne sont jamais présentées comme
celles d'un vrai LLM.

### 3.3 Reproductibilité

Toute simulation est déterminée par sa configuration et sa graine aléatoire. Chaque run est journalisé événement par
événement (`data/runs/<run_id>.jsonl`) et peut être **rejoué à l'identique** sans aucun appel au LLM (mode `replay`).
Le journal sert donc à la fois d'historique, d'outil de débogage et de base pour l'analyse.

## 4. Qualité du code et intégration continue

**Analyse statique** (configuration dans [pyproject.toml](../pyproject.toml)) :
- **Ruff** détecte les erreurs (imports inutiles, noms non définis), les pièges courants et les erreurs propres à
  asyncio, comme une tâche lancée sans référence et qui peut être détruite en cours de route ;
- **complexité cyclomatique** (McCabe), plafonnée à 20 par fonction ;
- **Radon** mesure la complexité et l'indice de maintenabilité.

**Intégration continue** : le workflow GitHub Actions
[.github/workflows/qualite.yml](../.github/workflows/qualite.yml) installe les dépendances, lance l'analyse statique puis
toute la suite de tests à chaque *push* et chaque *pull request*. C'est un garde-fou contre la régression et la dette
technique.

## 5. Déploiement et configuration

- **Conteneurisation** : un Dockerfile par service, et l'ensemble est orchestré par **Docker Compose**. Les
  dépendances de démarrage sont contrôlées par des *healthchecks* : Kafka doit être prêt avant que ses topics soient
  créés, et les topics doivent exister avant que la passerelle démarre.
- **Variantes par composition** : des fichiers compose superposables (`gpu`, `demo`, `fake`, `api`) permettent de
  lancer la même application avec un GPU, en démonstration ou avec le faux Ollama, sans dupliquer la configuration.
- **Configuration externalisée** par variables d'environnement (scénario, graine, vitesse de crue, nombre et type
  d'agents, mode mémoire…), selon le principe *12-factor*.
- **Secrets hors du dépôt** : `.env` n'est pas versionné, et `.env.exemple` sert de modèle.
- **Dépendances figées** (versions exactes) pour des installations reproductibles.

## 6. Données

- **PostgreSQL** avec un schéma décrit en SQL et des scripts numérotés, exécutés dans l'ordre (`postgres/01_schema.sql`,
  `02_charger.sql`…).
- **pgvector** pour la recherche par similarité de sens (RAG hybride, lexical et vectoriel).
- Les événements très volumineux et répétitifs (`world.tick`, `flood.update`) restent dans le journal JSONL et ne sont
  pas écrits en base.
- **Apache Spark** ([spark.md](spark.md)) :
  - en batch, il analyse tous les journaux (0,8 Go, environ un million d'événements, en 95 s) et les range en trois
    niveaux, bronze, argent et or : JSONL brut, Parquet partitionné par type d'événement, puis tables d'analyse ;
  - en streaming (Structured Streaming), il lit le topic Kafka en direct et publie des agrégats par fenêtre de 10 s, avec
    filigrane et checkpoint.

## 7. Traçabilité et documentation

- Le code et la documentation citent les **identifiants des exigences** du cahier des charges (B1 contexte commun,
  B2 indicateurs, F4 contrat d'événements, F6 lien indicateur → événement brut, C7 décalage, E3 rejeu, A4 robustesse
  LLM). On peut ainsi relier chaque exigence à sa mise en œuvre.
- Chaque indicateur calculé garde l'événement qui l'a produit et le délai entre l'événement et l'indicateur.
- Documentation technique dans [docs/](.) : architecture, contrat d'événements, mémoire PostgreSQL, étude des
  paramètres et résultats.
- **Démarche expérimentale outillée** : [tools/etude_parametres.py](../tools/etude_parametres.py) lance des plans
  d'expériences sur une grille de paramètres, avec plusieurs graines par configuration, puis produit des tableaux de
  comparaison (règles contre LLM, effet de chaque paramètre).

## 8. Limites et pistes d'amélioration

- **Historique de versions** : l'historique git est court et peu découpé. Un travail par branches et *pull requests*,
  avec des commits plus petits, rendrait l'évolution plus lisible et ferait tourner la CI à chaque étape.
- **Couverture de tests** : elle n'est pas mesurée. Ajouter `pytest-cov` permettrait de la suivre dans la CI.
- **Modélisation** : les schémas sont en texte (ASCII). Des diagrammes UML (composants, séquence d'une décision)
  compléteraient la documentation.
- **Tests de la vue 3D** : la partie Godot n'est pas couverte par des tests automatiques.

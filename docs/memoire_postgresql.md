# Mémoire individuelle, partagée et collective dans PostgreSQL

Dans la simulation, chaque habitant garde sa mémoire en RAM (`Agent.mem`, 60 souvenirs au plus) et tout ce qui se passe
part sur Kafka puis dans `data/runs/<run_id>.jsonl`. Ce dossier montre comment **PostgreSQL** peut porter cette mémoire :
la stocker, la partager entre habitants, la protéger, et l'interroger avec des requêtes.

```
lancer_postgres.bat          crée la base « alerte », charge deux runs, ouvre psql
postgres/01_schema.sql       tables, déclencheurs, fonctions, droits
postgres/02_charger.sql      charge un run JSONL (100 % SQL)
postgres/03_demo.sql         la démo : 11 étapes de requêtes commentées
postgres/03_demo_pgadmin.sql la même, pour pgAdmin (une requête à la fois, F5)
postgres/04_demo_relation.sql le lien entre les 3 mémoires, en suivant une information
postgres/05_demo_optimisation.sql optimiser la mémoire : fusion, oubli par importance, résumé, index
```

## Le modèle : trois niveaux de mémoire

| niveau | table | qui la lit | comment elle se remplit |
|---|---|---|---|
| **individuelle** (privée) | `souvenir` | l'habitant seul (Row Level Security) | ses observations et ses actes ; **et chaque message reçu** (déclencheur `message_recu`) |
| **partagée** | `message` + `reception` | l'auteur et les destinataires | `partager()` : un message et ses réceptions, dans une seule transaction |
| **collective** | `fait_collectif` (vue `tableau_commun`) | tout le quartier | déclencheur `message_publie` : chaque affirmation (`pont = ferme`) est comptée, avec ses **sources indépendantes** |

À côté : `croyance` (ce que chacun croit, dans le temps) et `verite` (l'état réel du monde) pour mesurer le **contexte commun**
(part des habitants qui ont la bonne lecture du pont, du refuge et du danger). Le calcul SQL (`etat_a`) redonne exactement
les chiffres de la simulation (`sim/indicators.py`) : 50 % en fin de run d'exemple, 40 % sur le run à 100 habitants.

Idées clés à expliquer :
- **Partager = écrire dans la mémoire des autres.** Un `INSERT` dans `reception` crée le souvenir chez le destinataire.
- **Écho ≠ confirmation.** 3 relais d'un même message comptent pour 1 source : `sources` garde les origines distinctes.
- **Une transaction, tout ou rien.** Message, réceptions, souvenirs et tableau commun sont mis à jour ensemble, ou pas du tout.
- **Concurrence.** Deux habitants qui partagent en même temps la même info : `INSERT ... ON CONFLICT DO UPDATE` verrouille la
  ligne du tableau commun, le second attend puis compte +1. Les numéros de message viennent d'une séquence (jamais de doublon).
- **Vie privée.** Le rôle `agent_sim` + `SET memoire.agent = 'A05'` : Salma ne voit que ses souvenirs et ses messages ;
  le tableau commun reste visible par tous. La même fonction `rappel()` ne fouille alors que sa propre mémoire.
- **Se souvenir.** `rappel()` = recherche plein texte en français (`tsvector`, index GIN) + sujets en commun + fraîcheur :
  la même logique que `RAG.recall` dans `sim/rag.py`.

## Lancer

Double-cliquer `lancer_postgres.bat` : il démarre PostgreSQL dans Docker (conteneur `alerte-postgres`, port 5440,
mot de passe `alerte`), charge les runs au premier lancement (`lancer_postgres.bat reset` pour repartir de zéro), puis ouvre psql :

```
\i postgres/03_demo.sql
```

Run à 100 habitants (agents LLM qui se contredisent sur le pont) :

```
\set run c7862089 \set agent A16 \set msg m16
\i postgres/03_demo.sql
```

À la main : `psql -d alerte -f postgres/01_schema.sql`, puis
`psql -d alerte -v fichier=data/runs/<run>.jsonl -f postgres/02_charger.sql` pour chaque run.

## Déroulé de la démo (≈ 10 min)

| étape | ce qu'on montre | technique SQL |
|---|---|---|
| 0 | contenu de la base | sous-requêtes de comptage |
| 1 | les souvenirs de Salma avant 09:26 : vus, faits, entendus | jointure, filtre temporel |
| 2 | « le pont est-il fermé ? » : 3 souvenirs utiles ; à 09:15, rien | `tsvector`, `ts_rank`, tableaux |
| 3 | qui a dit quoi à qui ; volume par genre et par canal | vue, `string_agg`, `FILTER` |
| 4 | arbre de relais d'une observation ; portée de chaque info | `WITH RECURSIVE` |
| 5 | le tableau commun ; son état à 09:15, 09:21, 09:31 | vue + fenêtre `OVER`, voyage dans le temps |
| 6 | qui a juste à 09:25 ; courbe du contexte commun | `LATERAL`, `generate_series` |
| 7 | qui a appris la fermeture, quand, de qui ; qui ne l'a jamais su ; grands relais | `jsonb @>`, anti-jointure `NOT EXISTS` |
| 8 | Omar partage en direct → souvenirs + tableau à jour → `ROLLBACK` | fonction PL/pgSQL, transaction |
| 9 | Salma ne voit que sa mémoire | Row Level Security |
| 10 | requête sur le journal brut, plan d'exécution | JSONB + index GIN, `EXPLAIN` |
| 11 | temps réel et concurrence avec deux terminaux | `LISTEN/NOTIFY`, verrou de ligne |

Pour l'étape 11, ouvrir deux psql sur `alerte` :

```sql
-- terminal 1
LISTEN memoire_partagee;
-- terminal 2
SELECT memoire.partager('7086a147', 'A10', 'Le pont est fermé', '{"bridge":"ferme"}', ARRAY['A07']);
-- terminal 1 : n'importe quelle requête (SELECT 1;) → « Notification asynchrone "memoire_partagee" ... »

-- concurrence : terminal 1  BEGIN; SELECT memoire.partager(... 'A10' ...);   (sans COMMIT)
--               terminal 2  SELECT memoire.partager(... 'A11' ...);          → attend
--               terminal 1  COMMIT;                                          → le terminal 2 repart, +2 messages
```

## Optimiser la mémoire (`05_demo_optimisation.sql`)

- **Moins stocker** : la vue `souvenir_compact` fusionne les souvenirs répétés (Salma : 48 → 28 ; 11 à 14 % de place gagnée sur un run).
- **Mieux oublier** : `importance()` note chaque souvenir de 1 (répétition) à 5 (vu ou vérifié soi-même) ; `memoire_de_travail()`
  garde les k plus importants. Avec 12 places, Salma garde 11 souvenirs sur le pont, contre 0 avec « les 12 plus récents »
  (ce que fait la simulation : `del s.mem[:-60]`).
- **Résumer** : `consolider()` remplace tous les souvenirs jusqu'à une heure par un seul résumé de ce qui comptait (48 → 35).
- **Retrouver vite** : l'index GIN `souvenir_tsv` sert la recherche plein texte (visible avec `EXPLAIN`).

Ces mécanismes sont dans la base seulement ; la simulation Python garde son oubli « premier entré, premier sorti ».

## Limites et suite possible

- Les données viennent des runs enregistrés : la simulation garde sa mémoire en RAM pendant le run. Brancher un consommateur
  Kafka (`alerte.events`) qui insère dans ces tables donnerait la même base **en direct**, sans toucher aux agents.
- Le rappel est lexical. Les embeddings du RAG (Ollama) pourraient aller dans une colonne `vector` avec l'extension `pgvector`.
- Les souvenirs initiaux des profils (ex. Karim et la crue de 2019) ne sont pas dans les événements, donc pas chargés.

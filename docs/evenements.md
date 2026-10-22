# Contrat d'événements `alerte.event/1` (exigence F4)

Tous les événements passent par le topic Kafka `alerte.events`, un message JSON par événement, et sont aussi écrits
ligne par ligne dans `data/runs/<run_id>.jsonl`. La passerelle (`gateway/main.py`) vérifie chaque événement :
s'il ne respecte pas ce contrat, il part dans le topic `alerte.dlq` et n'arrête rien.

**Version** : le champ `schema` vaut `alerte.event/1`. Si on change un champ obligatoire, on passe à `alerte.event/2`
et la passerelle accepte les deux pendant la transition (liste `SCHEMAS`).

## Enveloppe (commune à tous les événements)

| champ | type | sens |
|---|---|---|
| `schema` | texte | toujours `alerte.event/1` |
| `run_id` | texte | identifiant du run (8 caractères), ou `control` pour les accusés de commande |
| `seq` | entier | numéro d'ordre dans le run, sans trou : permet de détecter une perte |
| `type` | texte | un des types ci-dessous |
| `t_sim` | entier ou décimal | heure **simulée**, en minutes depuis minuit (540 = 09:00, 600 = 10:00) |
| `t_proc` | entier | heure **réelle** d'émission, en millisecondes (horloge du PC) |
| `agent` | texte ou `null` | l'habitant concerné (`A01`…`A12`, puis `N13`… pour les voisins générés), `null` pour le monde |
| `data` | objet | contenu propre au type |
| `replay` | booléen, optionnel | `true` quand l'événement est rejoué depuis un fichier (E3) |

Garder `t_sim` **et** `t_proc` permet de mesurer le décalage entre le monde simulé et le calcul réel (utile pour C5 et E4).

## Types d'événements

Les champs en **gras** sont obligatoires (vérifiés par la passerelle). Les autres sont présents mais facultatifs.

### Monde

| type | quand | data |
|---|---|---|
| `run.start` | début du run | **n_agents**, **agents** (profils, positions, contacts, moteur), **refuges**, **streets** (nœuds et arêtes), **heightmap**, **map** ; mode, scenario, seed, time_scale, t_start, t_end, update_at, rumor_at, bridge_close_at, models, llm_agents |
| `world.truth` | au début et à chaque changement | **bridge** (`ouvert`/`ferme`), **danger** (vrai à partir de 09:10), **refuges** (encore accessibles et non pleins). C'est la vérité qui sert à calculer les indicateurs, jamais montrée aux agents |
| `world.tick` | toutes les 5 s simulées | **level** (niveau d'eau), **agents** (position, état, refuge de chacun) ; bridge |
| `flood.update` | quand l'eau change | **level**, **cells** (profondeur par cellule de 5 m) |
| `official.bulletin` | 09:00 (vigilance) et `UPDATE_AT` (évacuation), ou commande | **level**, **text** ; claims, msg_id, to (qui l'a reçu) |
| `rumor` | `RUMOR_AT`, ou commande | **text**, **claim** (`ferme`/`ouvert`), **targets** ; msg_ids, true (la rumeur est-elle vraie à ce moment ?) |
| `bridge.closed` | 09:20 en `pont_ferme`, ou commande | at |
| `refuge.full` | un refuge atteint sa capacité | **refuge** |
| `tick` | à chaque pas (mode sync) | **step** ; thinkers, wall_ms (temps réel du pas) |
| `metrics` | régulièrement | statistiques LLM et RAG du moment (appels, tokens, latence) |
| `run.paused`, `run.resumed` | commande pause / reprise | — |
| `run.end` | fin du run | **indicators** (valeurs finales) ; calls, calls_total, tokens_in, tokens_out, incidents, incidents_by_kind, temporal_violations, avg_latency_ms, p95_latency_ms, rag_*, scenario, mode, seed, n_agents, llm_agents, update_at, rumor_at, agents (résumé par habitant) |

### Habitants

| type | quand | data |
|---|---|---|
| `agent.think` | chaque décision | **action**, **why** (justification), **decider** (`rule`, `big`, `small`, ou `rule` après un échec LLM) ; model, t_info (heure de la dernière information utilisée), risk, latency_ms, retrieved (textes retrouvés), retrieved_meta (id, fiabilité), query |
| `agent.belief` | la croyance change | **bridge**, **refuge**, **danger**, **risk** ; certainty |
| `agent.say` | un message part vers des contacts | **to**, **text**, **kind** (`relais`, `observation`, `correction`…), **origin** (source d'origine), **msg_id** ; hops, parent (message relayé), claims, decider, model |
| `agent.observe` | l'habitant voit l'eau | **what** ; depth, where |
| `agent.prepare` | il commence à préparer son départ | **ready_at** ; refuge |
| `agent.depart` | il part (irréversible) | **refuge**, **via** (`pont_central`, `passage_hauteurs` ou `sans_franchissement`), **path** ; nodes, length, why |
| `agent.reroute` | il change de chemin en route | **reason**, **path** ; refuge, old_refuge, via, nodes |
| `agent.blocked` | plus de chemin praticable, même pour rentrer chez soi | **reason** ; at |
| `agent.shelter` | aucun chemin sûr vers un refuge : l'habitant reste (ou rentre) chez lui, à l'étage | **reason** |
| `agent.verify` | il appelle le poste municipal | **until** (fin de l'appel, 3 min) ; topic |
| `agent.verify_result` | réponse du poste | **result** ; bridge |
| `agent.arrive` | arrivée au refuge | **refuge** ; t_depart, load, cap |
| `llm.incident` | réponse LLM inutilisable | **kind** (`timeout`, `unparsable`, `llm_error`, `role_exit`, `assistant_posture`, `agent_exception`) ; détail. L'habitant repasse alors sur ses règles (A4) |

### Commandes (Godot → simulation)

Godot envoie du JSON sur le WebSocket ; la passerelle le pose dans `alerte.commands`. La simulation répond par un
`command.ack` (**cmd** ; mode, paused) dans `alerte.events`.

| commande | effet |
|---|---|
| `{"type": "inject_alert"}` | bulletin d'évacuation de la mairie, maintenant |
| `{"type": "inject_rumor", "targets": ["A06"]}` | rumeur sur le pont (targets facultatif) |
| `{"type": "close_bridge"}` | ferme le Pont Central |
| `{"type": "pause"}` / `{"type": "resume"}` | met l'horloge simulée en pause / la relance |
| `{"type": "set_mode", "mode": "sync"}` | relance un run dans l'autre mode |
| `{"type": "set_scenario", "scenario": "pont_ouvert"}` | relance un run avec l'autre scénario |
| `{"type": "restart"}` | relance un run avec les mêmes réglages |

### Indicateurs (passerelle → Godot et `alerte.indicators`)

La passerelle recalcule les indicateurs à chaque événement avec `sim/indicators.py` (le même code que la simulation)
et publie un événement de type `indicator` (même enveloppe, `agent` = `null`). Son `data` contient les valeurs, plus
`source_seq` et `source_type` (l'événement brut qui a fait bouger l'indicateur, à retrouver par son `seq` dans le JSONL : F6)
et `lag_ms` (délai entre l'émission de cet événement et le calcul).

Valeurs : `safe` et `safe_pct` (à l'abri), `departed`, `depart_delay_avg` (minutes après le premier bulletin de 09:00),
`convergence_refuge` et `refuge_mode` (part des habitants qui visent le refuge le plus choisi), `convergence_bridge`,
`exact_bridge`, `exact_refuge`, `exact_danger` (part qui a juste sur chaque fait), `common_context` et
`common_context_avg` (voir architecture, section 1), `messages` (par type), `verified`, `rumor_relays_before_correction`,
`rumor_corrected`, `refuge_load`, `blocked`, `wet_avg_min` (minutes passées dans l'eau, en moyenne), `truth`.

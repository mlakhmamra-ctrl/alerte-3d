# Baseline 100 % règles (exigences E1, E2)

Runs sans aucun LLM (`LLM_AGENTS=none`), mode sync, 5 graines par configuration, rumeur « Pont Central fermé » à 09:20.
Tableau produit par `python tools/analyse_runs.py data/runs` (moyenne ± écart-type). `depart_delay_avg` est en minutes après le bulletin de 09:00 ; `wet_avg_min` en minutes passées dans l'eau.

| configuration | n runs | safe | depart_delay_avg | common_context_avg | convergence_refuge | exact_bridge | rumor_relays_before_correction | wet_avg_min | calls | tokens | incidents |
|---|---|---|---|---|---|---|---|---|---|---|---|
| pont_ferme · sync · maj 09:10 · règles seules | 5 | 12.00 ± 0.00 | 15.32 ± 0.33 | 0.19 ± 0.01 | 0.93 ± 0.04 | 0.00 ± 0.00 | 0.00 ± 0.00 | 0.99 ± 0.07 | 0.00 ± 0.00 | 0.00 ± 0.00 | 0.00 ± 0.00 |
| pont_ferme · sync · maj 09:30 · règles seules | 5 | 11.60 ± 0.55 | 21.54 ± 0.23 | 0.23 ± 0.06 | 0.68 ± 0.07 | 0.53 ± 0.05 | 0.00 ± 0.00 | 3.68 ± 1.34 | 0.00 ± 0.00 | 0.00 ± 0.00 | 0.00 ± 0.00 |
| pont_ouvert · sync · maj 09:10 · règles seules | 5 | 12.00 ± 0.00 | 15.96 ± 0.44 | 0.56 ± 0.02 | 1.00 ± 0.00 | 1.00 ± 0.00 | 0.00 ± 0.00 | 0.51 ± 0.05 | 0.00 ± 0.00 | 0.00 ± 0.00 | 0.00 ± 0.00 |
| pont_ouvert · sync · maj 09:30 · règles seules | 5 | 12.00 ± 0.00 | 25.76 ± 0.11 | 0.09 ± 0.01 | 1.00 ± 0.00 | 0.40 ± 0.04 | 0.00 ± 0.00 | 1.30 ± 0.07 | 0.00 ± 0.00 | 0.00 ± 0.00 | 0.00 ± 0.00 |

## Ce qu'on lit

- Avec le pont fermé et une mairie qui tarde (09:30), un habitant n'est pas à l'abri à 10:00 dans 2 runs sur 5, et les gens passent plus de temps dans l'eau.
- Une mise à jour à 09:10 fait partir tout le monde environ 6 à 10 minutes plus tôt.
- Piège intéressant : pont fermé + mairie à 09:10 donne `exact_bridge` = 0. La mairie a dit « ouvert » à 09:10, ce qui était vrai à ce moment, puis le pont a fermé à 09:20 sans nouveau bulletin. Une information juste mais périmée fait autant de dégâts qu'une rumeur.
- `common_context_avg` distingue bien les situations (0,56 quand la mairie confirme tôt un pont ouvert, contre 0,09 à 0,23 dans les autres cas) : c'est l'argument pour B3.
- Ces chiffres sont ceux des règles seules. Les comparaisons avec les agents LLM restent à faire avec Ollama.

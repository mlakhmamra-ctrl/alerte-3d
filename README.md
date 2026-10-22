# ALERTE 3D — décision collective pendant une crue (thématique ESSAIM)

MVP du projet « Mondes Vivants » (5A IABD). Douze habitants du quartier fictif de Rive-Claire reçoivent des alertes,
des rumeurs et des messages de voisins pendant une crue. Sans chef, ils doivent choisir **quand partir, par où, et vers
quel refuge**. Quatre d'entre eux raisonnent avec un LLM local (Ollama), les huit autres suivent des règles explicites.
Tout passe par Kafka, et la scène 3D est dans Godot.

L'explication complète (lecture ESSAIM ↔ ALERTE, choix des agents, du LLM, API ou agent, architecture, exigences
couvertes) est dans [docs/architecture.md](docs/architecture.md). Le contrat d'événements est dans
[docs/evenements.md](docs/evenements.md).

```
 ┌──────────── Docker (docker compose up) ────────────────────────────────────┐
 │  ollama  ◄──HTTP──  sim (Python)  ──événements──►  Kafka  ──►  gateway  ───┼──WebSocket──►  Godot 4 (sur votre PC)
 │  qwen2.5:7b / 0.5b   12 habitants                 alerte.events   (valide,  │   ws://127.0.0.1:8765
 │  nomic-embed-text    crue, rues, RAG              alerte.commands  indicateurs)◄─ commandes (alerte, rumeur, pause…)
 └─────────────────────────────────────────────────────────────────────────────┘
```

## Lancer

Il faut Docker Desktop et Godot 4.3 ou plus récent (version standard, pas .NET).

1. Dans ce dossier : `docker compose up --build`
   (ou double-clic sur `lancer.bat` sous Windows, `./lancer.sh` sous Mac/Linux).
   Le premier lancement télécharge les modèles (environ 6 Go) : c'est long, laissez la fenêtre ouverte.
   La simulation démarre quand vous voyez `modèle prêt : qwen2.5:1.5b` puis des lignes `sim-1`.
2. Ouvrez Godot → Importer → `godot/project.godot` → F5. La scène se connecte toute seule à `ws://127.0.0.1:8765`.
3. Arrêt : Ctrl+C, puis `docker compose down`.

**Carte NVIDIA ?** `lancer_gpu.bat` (double-clic), `lancer.bat gpu` ou `./lancer.sh gpu` fait calculer Ollama sur la carte graphique
(équivalent : `docker compose -f docker-compose.yml -f docker-compose.gpu.yml up --build`).
Prérequis Windows : pilote NVIDIA récent et Docker Desktop avec le moteur WSL 2 ; test rapide : `docker run --rm --gpus all ubuntu nvidia-smi -L` doit lister la carte.
Vérification : `docker compose exec ollama ollama ps` doit afficher `100% GPU` dans la colonne PROCESSOR.
La variante GPU règle aussi Ollama pour une carte de 8 Go (3 appels en parallèle, Flash Attention, cache KV en q8 : voir `docker-compose.gpu.yml`).
⚠️ Utilisez **toujours** la variante GPU, y compris pour relancer : un `docker compose up` (ou `lancer.bat`) sans `gpu` recrée
le conteneur `ollama` sans accès à la carte, et les modèles repassent en `100% CPU`. Pour corriger : relancez avec la variante GPU,
puis `docker compose restart sim` pour que la simulation recharge les modèles.
**Démo en direct (présentation) ?** `lancer_demo.bat` : vraie simulation avec les LLM sur la carte NVIDIA, accélérée
(09:00 → 10:00 en 12 minutes, `docker-compose.demo.yml`), puis Godot → F5. Fermez l'éditeur Godot pendant la démo (mémoire vidéo).
**Sans carte NVIDIA ?** Le 7b est lent sur processeur : dans `.env`, mettez `LLM_BIG=qwen2.5:3b`.
**Machine modeste ?** Dans `.env`, mettez `LLM_BIG=qwen2.5:1.5b` et `LLM_SMALL=qwen2.5:0.5b`, ou `LLM_PARALLEL=1`.
**Sans aucun modèle** (pour vérifier Kafka, la passerelle et Godot) :
`docker compose -f docker-compose.yml -f docker-compose.fake.yml up --build`. Les décisions « LLM » viennent alors d'un
**faux LLM** à heuristiques (`tools/fake_ollama.py`) : ne présentez jamais ces runs comme des résultats de LLM.

## Réglages (`.env`)

Les valeurs par défaut sont dans `.env.exemple` (versionné). Pour changer un réglage sur votre machine, ou pour mettre un
**secret** comme `LLM_API_KEY`, créez un fichier `.env` avec seulement les lignes à changer : il remplace `.env.exemple`
et il est ignoré par git (`.gitignore`), il ne part donc jamais sur GitHub.

| variable                    | rôle                                                                                                                                                                                                                                                                                                            |
| --------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `N_AGENTS`                | taille de la population (**100** par défaut ; 12 = les habitants de la fiche ; au-delà, des voisins générés, en foyers de 2 à 3 personnes : contacts = leur foyer + le voisin le plus proche) — exigence C2                                                                                       |
| `LLM_AGENTS`              | qui pense avec un LLM (`A04,A05,A06,A11` par défaut) ; `none` = baseline 100 % règles (E1)                                                                                                                                                                                                                 |
| `LLM_EXTRA`               | nombre d'habitants générés qui pensent **aussi** avec un LLM (16 par défaut, répartis dans la population, un sur deux avec `LLM_BIG`) ; ignoré si `LLM_AGENTS=none`. Plus il y en a, plus la file d'attente du LLM s'allonge                                                                                    |
| `MONDE`                   | `etendu` par défaut : le quartier de la fiche au centre, plus un quartier Ouest et un quartier Est (240 m × 80 m) et trois refuges de plus (Église Saint-Roch 25 places, Collège du Plateau 30, Salle des Fêtes du Moulin 25 ; 99 places en tout). `fiche` = plan exact de la fiche (2 refuges, 19 places). **Écart à la fiche** à signaler |
| `LLM_BIG`, `LLM_SMALL`  | les deux tailles de modèle (A5) ; `LLM_PROVIDER=openai` + `LLM_API_URL` + `LLM_API_KEY` pour une API gratuite compatible OpenAI                                                                                                                                                                           |
| `LLM_MIN_GAP`             | un agent LLM réfléchit au plus une fois par `LLM_MIN_GAP` minute(s) simulée(s) (1 par défaut), même s'il reçoit plusieurs messages entre-temps : évite que la file d'attente du LLM s'emballe avec beaucoup d'agents LLM                                                                       |
| `MODE`                    | `async` (chacun à son rythme, latence réelle du LLM) ou `sync` (pas de 30 s, on attend le plus lent) — C4                                                                                                                                                                                                 |
| `TIME_SCALE`              | **1 par défaut = temps réel** : 1 s réelle = 1 s simulée, un run 09:00 → 10:00 dure 1 h et le LLM (Ollama) décide pendant que l'eau monte. 10 = accéléré (run de 6 min) pour les tests                                                                                                            |
| `WORLD_SCALE`             | 1 par défaut = **plan exact de la fiche** (120 m × 80 m). 2 = quartier simulé deux fois plus étendu (240 × 160 m) : rues, maisons et refuges écartés, rivière, ponts, bâtiments et habitants à leur taille réelle, trajets plus longs. **Écart à la fiche** à signaler si on l'utilise |
| `SCENARIO`                | `pont_ferme` (crue forte, pont fermé à 09:20) ou `pont_ouvert` (crue modérée, la rumeur est fausse)                                                                                                                                                                                                      |
| `UPDATE_AT`, `RUMOR_AT` | hypothèses H1 et H2 de la fiche (mise à jour de la mairie à 09:10 ou 09:30, rumeur à 09:20)                                                                                                                                                                                                                  |
| `BATCH`, `SEED`         | `BATCH=5` enchaîne 5 runs sans Godot (graines `SEED`, `SEED+1`…) — E2                                                                                                                                                                                                                                   |

## Dans Godot

**Style « maquette » (inspiré de la vidéo AGORA).** Le monde est un diorama : un bloc de terre suspendu dans un ciel de fin
de journée, avec ses couches de sol visibles sur les bords. Textures en gros pixels, habitants en voxels, eau bleue avec écume
et débris emportés quand la crue monte, flou « miniature » optionnel (touche **P**, coupé par défaut pour garder une image nette ; visible avec le rendu Forward+).
Chaque habitant porte une casquette d'une couleur qui lui est propre : c'est son identifiant (A01 → A12 dans l'ordre des
couleurs de la frise en bas à gauche).

**Scène 3D sans texte, texte à côté (comme la vidéo AGORA).** Rien n'est écrit dans la scène elle-même : pas de noms
au-dessus des têtes, pas de bulles, pas de panneaux 3D. Le texte est dans des panneaux autour : à gauche une fiche par
habitant (identifiant, moteur, état, risque, dernière justification ; clic = sélection), en haut le décompte, en bas le fil
en direct des messages, à droite les indicateurs, les courbes et les commandes. La touche **I** masque les panneaux pour
ne garder que la scène et ses repères visuels.

- Barre fine en haut = horloge simulée 09:00 → 10:00 (bleu calme → rouge crue), avec deux repères : fermeture du pont et mise à jour de la mairie.
- Jauge verticale à gauche = niveau de l'eau (le trait blanc = hauteur des quais).
- Pastilles en bas à gauche = un habitant chacune, couleur de son gilet ; anneau violet/cyan = agent LLM, qui pulse quand il réfléchit.
  Clic sur une pastille = sélection. Au-dessus de l'habitant sélectionné : risque perçu (barre) et trois points pont / refuge / danger (vert = croyance juste, rouge = fausse).
- Lanternes autour de chaque refuge = une par place ; allumées = occupées, toutes rouges = refuge plein.
- Point en haut à droite = liaison avec la simulation (vert en direct, orange en attente, gris déconnecté).
- Le monde est agrandi : faubourgs, routes, bois et relief jusqu'à l'horizon autour du quartier simulé (décor ; la simulation reste sur les 120 × 80 m de la fiche).
- **Gilet** de chaque habitant = son état : gris chez soi, orange prépare son départ, blanc en route, rouge bloqué, vert à l'abri.
- **Orbe** au-dessus de la tête : agent LLM (violet = grand modèle, cyan = compact). Elle pulse pendant que le LLM réfléchit.
- **Impulsions** entre habitants = messages (bleu mairie, rouge rumeur, blanc relais, jaune observation, vert correction).
- **Eau** : le plan d'eau monte avec le niveau de la simulation ; **cellules de 5 m** colorées selon la profondeur (touche G).
- **Clic sur un habitant** : profil, confiance, croyances (✓/✗ par rapport à la vérité), dernière décision et sa justification,
  documents retrouvés par le RAG, journal de mémoire. Ses contacts sont reliés par des traits.
- Panneau de droite : indicateurs calculés en flux, synchronisés sur l'horloge simulée, avec l'événement source et le décalage.
- Boutons / touches : **A** alerte officielle · **U** rumeur · **B** fermer le pont · **Espace** pause · **M** sync/async ·
  **N** scénario · **R** relancer · **G** cellules · **C** caméra ciné · **F** suivre · **Tab** habitant suivant · **T** vue de dessus.

## Rejouer un run sans LLM (E3)

```
docker compose --profile replay run --rm -e REPLAY_FILE=/data/runs/exemple_regles_pont_ferme.jsonl replay
```

Godot affiche « REJEU ». Chaque run est enregistré dans `data/runs/<run_id>.jsonl`.

## Analyser

- Lot de runs : `docker compose run --rm -e BATCH=5 -e MODE=sync -e TIME_SCALE=0 sim` (TIME_SCALE=0 : aussi vite que possible, mode sync seulement)
- Tableau par configuration (moyenne ± écart-type) : `python tools/analyse_runs.py data/runs`
- Qualité du RAG (F2) : `docker compose run --rm sim python eval_rag.py /data/runs`

## Ce qui a été testé, et ce qui ne l'a pas été

- Testé : la chaîne complète **Docker + Kafka réel + passerelle + WebSocket + Godot 4.5.1**, avec le **faux LLM** à la place d'Ollama,
  en mode async et sync, commandes de Godot (rumeur, relance, changement de mode) et rejeu. Captures d'écran prises avec le rendu
  de compatibilité (OpenGL logiciel) : sur une vraie carte graphique, Godot utilise Forward+ et l'eau, les ombres et le brouillard sont plus beaux.
- Testé : 20 runs 100 % règles (2 scénarios × 2 heures de mise à jour × 5 graines), voir [docs/resultats_baseline_regles.md](docs/resultats_baseline_regles.md).
- **Pas testé** : de vrais modèles Ollama (le téléchargement des modèles était bloqué dans l'environnement de développement).
  Lancez au moins un run réel avant la soutenance et vérifiez le temps de réponse sur votre machine.
- `data/runs/exemple_regles_pont_ferme.jsonl` : un run d'exemple 100 % règles, rejouable.

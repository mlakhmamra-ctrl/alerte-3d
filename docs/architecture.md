# ALERTE 3D : architecture et choix techniques

Ce document explique le projet en français simple : ce que l'on simule, comment les morceaux s'assemblent,
pourquoi chaque technologie est là, et ce qui reste à faire. Les commandes pour lancer sont dans le [README](../README.md).

## 1. La lecture choisie : ESSAIM appliqué à ALERTE

La thématique ESSAIM, c'est un groupe qui **prend une décision collective sans chef** (comme un essaim d'abeilles qui
choisit un nouveau nid). Le professeur a demandé de remplacer la logique « abeille » par une logique **humaine**.
La fiche ALERTE donne justement une décision collective humaine : pendant une crue, douze voisins doivent choisir
**s'ils partent, quand, par où et vers quel refuge**, sans que personne ne commande.

| idée ESSAIM (abeilles) | ce que ça devient dans ALERTE (humains) |
|---|---|
| sites de nid possibles | deux refuges : l'**École des Tilleuls** (proche, 7 places, mais en zone basse : c'est le piège) et le **Gymnase des Hauteurs** (rive nord, 12 places, le bon choix) |
| éclaireuses qui visitent les sites | habitants qui observent l'eau devant chez eux, appellent le poste municipal, lisent les messages |
| danse pour recruter | messages entre voisins (relais, observation, correction), qui changent ce que l'autre croit |
| quorum, puis départ de l'essaim | chacun part quand **son** seuil est atteint : une confirmation, deux, ou de l'eau vue de ses yeux |
| décision irréversible | partir (on ne revient pas chercher ses affaires) et arriver dans un refuge |
| information trompeuse | la rumeur « Pont Central fermé » (vraie ou fausse selon le scénario) |

Pourquoi cette lecture : elle respecte la fiche (mêmes 12 habitants, mêmes contacts, mêmes hypothèses H1 à H4) et
garde l'esprit de l'essaim (pas de chef, information locale, convergence vers un choix commun). Les humains ne suivent
pas une danse : ils ont des **seuils de confiance différents**, des contraintes (enfant, parent âgé, pas d'escaliers)
et peuvent se tromper à cause d'une rumeur. C'est exactement ce que l'on modélise.

**« Contexte commun » (exigence B1)**, en termes observables : à un instant donné, un habitant a le contexte commun
s'il a juste sur les **trois faits** qui comptent : l'état du Pont Central, le bon refuge (celui qui est encore
accessible) et le fait que le danger est réel. L'indicateur `common_context` est la part des habitants qui ont les trois
faits justes ; `common_context_avg` est sa moyenne sur tout le run.

## 2. Schéma général

```
        ┌──────────────────────────── docker compose ────────────────────────────────┐
        │                                                                             │
        │   ┌──────────┐   HTTP (JSON)   ┌──────────────────────┐                     │
        │   │  ollama  │ ◄─────────────► │  sim  (Python)       │                     │
        │   │ qwen 7b  │                 │  - monde : crue, rues│                     │
        │   │ qwen 0.5b│                 │  - 12 habitants      │ ──► data/runs/*.jsonl (historique, rejeu)
        │   │ embed    │                 │  - RAG + mémoire     │                     │
        │   └──────────┘                 └───────┬──────▲───────┘                     │
        │                         alerte.events  │      │ alerte.commands             │
        │                                 ┌──────▼──────┴───────┐                     │
        │                                 │  kafka (KRaft)      │ ── alerte.dlq       │
        │                                 └──────┬──────▲───────┘   (événements       │
        │                                        │      │            non conformes)   │
        │                                 ┌──────▼──────┴───────┐                     │
        │                                 │  gateway (Python)   │ calcule les         │
        │                                 │  valide + WebSocket │ indicateurs en flux │
        │                                 └──────┬──────▲───────┘                     │
        └────────────────────────────────────────┼──────┼─────────────────────────────┘
                                   ws://127.0.0.1:8765  │ commandes (alerte, rumeur, pause…)
                                        ┌────────▼──────┴───────┐
                                        │  Godot 4 (sur le PC)  │  scène 3D, inspecteur d'agent,
                                        │                       │  panneau d'indicateurs
                                        └───────────────────────┘
```

Le chemin d'une décision :
1. L'eau monte (`flood.update`). Karim (A04) voit 12 cm d'eau devant chez lui.
2. La simulation construit son « prompt » : sa situation, ses derniers messages, ses souvenirs, les documents retrouvés par le RAG.
3. Ollama répond en JSON : croyances, risque, action (`partir`), itinéraire (`passage_hauteurs`), justification, message aux contacts.
4. Le code Python vérifie ce JSON, applique l'action (calcul du chemin, marche), publie `agent.think` puis `agent.depart` dans Kafka.
5. La passerelle lit Kafka, vérifie le schéma, met à jour les indicateurs et envoie tout à Godot.
6. Godot fait marcher le personnage, dessine l'impulsion du message vers Maya et Nora, et met à jour le panneau.

## 3. Les technologies, et pourquoi chacune

| techno | rôle ici | pourquoi |
|---|---|---|
| **Docker / docker compose** | lance Kafka, Ollama, la simulation et la passerelle avec une seule commande | même environnement sur tous les PC, rien à installer à part Docker et Godot |
| **Kafka 3.8 (mode KRaft)** | le « fil » de tous les événements (exigence C1) | découple la simulation de l'affichage ; plusieurs lecteurs possibles (Godot, analyse) ; on peut relire le flux. KRaft = pas besoin de Zookeeper |
| **Ollama** | serveur de modèles LLM en local | gratuit, hors ligne, pas de quota, les données ne sortent pas du PC ; API HTTP simple ; sait forcer une sortie JSON qui suit un schéma |
| **Python 3 (asyncio)** | la simulation : monde, agents, RAG, horloge | asyncio permet à 12 agents de « penser » en parallèle pendant que l'eau continue de monter (mode async) |
| **aiokafka, httpx, websockets** | bibliothèques Python pour Kafka, HTTP (Ollama) et WebSocket | asynchrones, légères |
| **Godot 4** | la simulation finale en 3D (exigences D1 à D3) | moteur 3D libre ; GDScript est simple ; WebSocket intégré. Il tourne **hors Docker** car il a besoin de la carte graphique et d'une fenêtre |
| **nomic-embed-text** | transforme les documents en vecteurs pour le RAG hybride | petit modèle d'embeddings disponible dans Ollama |

Pourquoi une **passerelle** entre Kafka et Godot : Godot ne parle pas le protocole Kafka. La passerelle lit Kafka,
vérifie chaque événement, met les mauvais dans `alerte.dlq`, calcule les indicateurs (B2) et pousse le tout en
WebSocket, que Godot sait lire nativement. Elle garde aussi en mémoire le début du run, donc si vous ouvrez Godot en
retard, il rattrape l'état.

## 4. Les agents : qui pense avec un LLM, qui suit des règles

La fiche donne 12 habitants. Quatre raisonnent avec un LLM, huit suivent des règles (exigence A6).

| id | nom | situation (fiche) | moteur | règle ou raison du choix |
|---|---|---|---|---|
| A01 | Lina | accompagne un enfant, prépare un sac | règles | part après une confirmation, avec un temps de préparation long |
| A02 | Yassine | commerçant, attend une confirmation | règles | il lui faut deux sources concordantes |
| A03 | Nora | marche lentement, pas d'escaliers | règles | évite le Passage des Hauteurs (escaliers) |
| **A04** | **Karim** | a déjà vécu une crue | **grand LLM** | doit relier un souvenir (2019) à la situation : raisonnement plus riche |
| **A05** | **Salma** | vérifie l'émetteur et la date | **grand LLM** | doit juger la fiabilité des messages, repérer les contradictions |
| **A06** | **Adam** | relie plusieurs groupes, partage vite | **petit LLM** | reformule et relaie : tâche simple, il faut aller vite |
| A07 | Maya | aide un parent âgé | règles | part seulement avec lui, plus lentement |
| A08 | Rami | doute des alertes | règles | attend de voir l'eau lui-même |
| A09 | Inès | travaille au café | règles | veut savoir si le pont est ouvert ; appelle pour vérifier |
| A10 | Omar | connaît le détour par les hauteurs | règles | prend le Passage si le pont est douteux |
| **A11** | **Hiba** | suit le groupe, recoupe les messages | **petit LLM** | recoupe quand elle a le temps : tâche moyenne |
| A12 | Sami | attend une seconde confirmation | règles | deux confirmations avant de partir |

Pourquoi ce partage (exigences A5 et E5) : le **grand modèle** va aux habitants dont le rôle demande de **juger**
(mémoire, fiabilité). Le **petit modèle** va à ceux qui **transmettent**. Les habitants dont le comportement se résume
à un seuil clair restent en **règles** : un LLM coûterait des appels sans rien apporter. Cette répartition se change dans
`.env` (`LLM_AGENTS`) sans toucher au code (`none` = tout en règles, c'est la baseline E1).

### Ce que fait le LLM, et ce que fait le code

| le LLM décide | le code Python garantit |
|---|---|
| ce que l'habitant **croit** (pont, refuge, danger) | ce que l'habitant **peut savoir** : il ne lit que les messages déjà arrivés à son heure simulée (C6) |
| son **niveau de risque** et son **action** (rester, préparer, partir, vérifier, relayer, corriger…) | que l'action est possible (on ne traverse pas 50 cm d'eau, on ne prend pas d'escaliers si on ne peut pas) |
| son **itinéraire** préféré | le chemin réel (algorithme de Dijkstra sur le plan des rues) et la vitesse de marche |
| la **justification** et le **message** aux voisins, en langage naturel | la remise du message aux bons contacts, avec un délai |
| | si la réponse est vide, hors schéma, ou si l'agent « sort de son rôle » (parle comme un assistant) : un incident `llm.incident` est publié et **l'habitant repasse sur ses règles** pour ce tour (A4) |

### Mémoire (A1) et RAG (F1, F2)

- **Mémoire propre** : chaque habitant LLM garde un journal de ce qu'il a vu, lu, vérifié et décidé.
- **Base de connaissances** : 24 documents fictifs de Rive-Claire (plan, refuges, crue de 2019, consignes). Deux sont
  volontairement **peu fiables** ou périmés, pour voir si les agents s'y laissent prendre.
- **Recherche hybride** : mots-clés (TF-IDF) + embeddings (nomic-embed-text), combinés par RRF. Si les embeddings ne
  sont pas disponibles, la recherche reste lexicale.
- `sim/eval_rag.py` mesure la qualité sur 13 questions étiquetées à la main. En mode lexical : recall@3 = 0,78,
  hit@3 = 1,00, MRR = 0,92. Le mode hybride n'a pas pu être mesuré ici (pas d'Ollama réel, voir section 11).

## 5. Quel LLM choisir

Choix par défaut : **qwen2.5:7b** (grand) et **qwen2.5:0.5b** (petit), avec **nomic-embed-text** pour les embeddings
(calculés sur le processeur pour laisser la mémoire vidéo aux LLM).

Pourquoi : ces modèles parlent correctement français et suivent bien une consigne « réponds en JSON ». Le 7b raisonne
mieux que le 3b, mais il est plus lourd : environ 5 Go de mémoire vidéo pour le 7b et 0,6 Go pour le 0.5b (version
quantifiée par défaut d'Ollama). Sur une carte de 8 Go, Godot et le bureau Windows prennent déjà 1 à 1,5 Go : avec le 1.5b
à la place du 0.5b, la mémoire vidéo déborde et Windows en déplace une partie en mémoire centrale. Mesuré sur une
RTX 5070 Laptop : le 7b tombe alors de 41 à 3 tokens/s. Le 0.5b produit en revanche nettement plus de croyances sans
source que le 7b (13 sur 28 décisions contre 3 sur 36 sur le même run) : l'écart grand / petit modèle (A5) est plus marqué.

| votre machine | réglage conseillé dans `.env` |
|---|---|
| PC portable sans carte graphique, 8 Go de RAM | `LLM_BIG=qwen2.5:1.5b`, `LLM_SMALL=qwen2.5:0.5b`, `LLM_PARALLEL=1` |
| PC 16 Go de RAM, sans carte graphique | `LLM_BIG=qwen2.5:3b` (le 7b est lent sur processeur) |
| carte NVIDIA, 8 Go de mémoire vidéo | réglages par défaut (7b et 0.5b), lancés avec `lancer.bat gpu` ; vérifier avec `ollama ps` que tout est en `100% GPU` |
| carte NVIDIA, 12 Go ou plus | `LLM_SMALL=qwen2.5:1.5b` possible |

**Attention, ça évolue vite.** Les modèles qwen2.5 datent de 2024. Des familles plus récentes existent probablement
(nouvelles versions de Qwen, Llama, Gemma, Mistral…), et je ne suis pas certain de laquelle est la meilleure
aujourd'hui pour ce cas. Regardez la bibliothèque officielle sur ollama.com/library avant de figer votre choix.
Le critère utile pour le rapport : **deux tailles d'une même famille**, pour que la différence mesurée vienne de la
taille et pas d'autre chose. Changer de modèle = changer deux lignes dans `.env`.

## 6. API ou « vrai » agent IA ?

Ce n'est pas l'un ou l'autre. Il faut séparer deux choses :

- **Le modèle** (le LLM) : c'est seulement une fonction « texte en entrée, texte en sortie ». Il peut tourner en local
  (Ollama) ou chez un fournisseur (API).
- **L'agent** : c'est **notre code Python** qui entoure le modèle. Il a une mémoire, il observe le monde, il récupère des
  documents, il appelle le modèle, il vérifie la réponse, il agit, et il recommence. La boucle
  « observer → se souvenir → chercher → décider → agir » est dans `sim/agents.py` (méthodes `think`, `llm_decide`, `apply`).

Donc **on développe un vrai agent**, et Ollama ne sert que de « cerveau » interchangeable, appelé par HTTP. On n'a pas
besoin d'un framework d'agents (LangChain, CrewAI…) : pour 12 habitants dans un monde simple, une boucle écrite à la
main est plus lisible, plus facile à défendre à l'oral et plus facile à rejouer.

Ollama en local est le choix par défaut : pas de coût, pas de quota, fonctionne sans Internet le jour de la
soutenance, et les mesures de coût (E4) sont les vôtres. Une API gratuite compatible OpenAI reste possible
(`LLM_PROVIDER=openai`, `LLM_API_URL`, `LLM_API_KEY`), utile si votre PC est trop lent. Deux précautions : les quotas
gratuits changent souvent, et certains fournisseurs peuvent réutiliser les données envoyées. Vérifiez leurs conditions
actuelles.

## 7. Le modèle de temps (C3, C4)

- Horloge simulée : le run va de **09:00 à 10:00** (heure simulée). `t_sim` est en minutes depuis minuit.
- Physique (eau, marche) : un pas toutes les **5 secondes simulées**.
- **Mode async** : horloge = temps réel × `TIME_SCALE` (1 par défaut : la simulation tourne en temps réel, Godot reçoit une position par seconde). Chaque habitant pense à son rythme ; si le LLM met 20 s à
  répondre, l'eau a monté pendant ce temps. C'est le mode le plus réaliste.
- **Mode sync** : pas de 30 s simulées. À chaque pas, tous les habitants qui doivent penser pensent, et l'horloge attend
  le plus lent. Résultat : le temps de réponse du LLM ne pénalise plus personne.
- Comparer les deux modes sur le même scénario mesure l'effet de la latence sur la décision (C5).
- Un habitant pense quand il reçoit une nouvelle information, ou toutes les `THINK_EVERY` minutes sinon. Un habitant
  LLM qui marche déjà ne rappelle pas le modèle (sobriété).

La crue : le niveau monte de 0 m à 09:10 jusqu'au pic à 10:00 (0,32 m au-dessus des quais en `pont_ouvert`, 0,75 m en
`pont_ferme`). À partir de 50 cm d'eau, une rue est infranchissable. Dans `pont_ferme`, le pont ferme à 09:20.

## 8. Où vivent les données (F3)

| donnée | où | pourquoi |
|---|---|---|
| flux d'événements | Kafka, topic `alerte.events` (rétention 24 h) | transport en direct, plusieurs lecteurs |
| commandes de Godot | Kafka, topic `alerte.commands` | même chemin que le reste, tracé |
| événements non conformes | Kafka, topic `alerte.dlq` | on les garde pour diagnostiquer (F4) |
| état courant du monde | en mémoire dans `sim` (et dernier `world.tick` gardé par la passerelle) | change toutes les 5 s, pas besoin de base |
| historique des runs | `data/runs/<run_id>.jsonl`, un événement par ligne | simple, lisible, sert au rejeu (E3) et aux analyses (F5) |
| mémoire des agents | en mémoire dans `sim`, et chaque souvenir est aussi dans les événements | visible dans l'inspecteur Godot (D2) |
| base de connaissances | `sim/corpus.py` | petite, fixe, versionnée avec le code |
| modèles | volume Docker `ollama` | téléchargés une seule fois |

Le contrat de chaque événement est dans [evenements.md](evenements.md).

## 9. L'intégration Godot

- `godot/main.gd` : connexion WebSocket, réception des événements, interface (panneaux, boutons, inspecteur), caméra.
- `godot/world_builder.gd` : construit tout le décor à partir de `run.start` (terrain, rivière, rues, pont, Passage des
  Hauteurs avec escaliers, maisons, école, gymnase, arbres, lampadaires) et met l'eau à jour.
- `godot/agent_view.gd` : un habitant (silhouette, gilet coloré selon l'état, orbe pour les agents LLM, bulle de parole).
- `godot/shaders/` : eau boueuse avec vagues et écume, feuillage qui bouge au vent, terrain.

Godot ne calcule rien de la simulation : il **affiche** ce que dit Kafka et **envoie** des commandes (D3 : alerte,
rumeur, fermeture du pont, pause, mode, scénario, relance). Ainsi, ce qu'on voit est exactement ce qui est enregistré,
et le rejeu donne la même scène.

## 10. Correspondance avec les exigences du sujet

| exigence | où c'est fait |
|---|---|
| A1 mémoire propre | `Agent.mem` (journal daté, 60 entrées max), souvenirs injectés dans le prompt ; visibles dans l'inspecteur |
| A2 échanges en langage naturel | champ `message` du LLM → `agent.say` → boîte de réception des contacts |
| A3 deux profils, même situation, décisions différentes | à montrer avec de vrais runs LLM : par exemple envoyer la rumeur à Karim et Salma (`RUMOR_TO=A04,A05`) et comparer leurs justifications |
| A4 robustesse | schéma JSON, détection de sortie de rôle / posture d'assistant, retour aux règles, `llm.incident` |
| A5 deux tailles de modèles | `LLM_BIG` / `LLM_SMALL` |
| A6 agents à règles | 8 habitants à seuils explicites (`sim/agents.py`, `rule_decide`) |
| B1 définition du contexte commun | section 1 ci-dessus |
| B2 indicateur en direct | passerelle → panneau de droite dans Godot |
| B3 l'indicateur distingue deux runs | baseline : `common_context_avg` 0,19 (pont fermé, mairie à 09:10) contre 0,56 (pont ouvert), voir `resultats_baseline_regles.md` |
| B4 expliquer une variation | à faire avec de vrais runs : le JSONL garde les messages qui précèdent chaque variation |
| C1 Kafka | `alerte.events`, `alerte.commands`, `alerte.indicators`, `alerte.dlq` |
| C2 nombre d'agents au lancement | `N_AGENTS` (au-delà de 12, voisins à règles générés) |
| C3 horloge simulée | `t_sim` dans chaque événement, `Clock` dans `sim/main.py` |
| C4 sync / async | `MODE` |
| C5 comparer les modes | à faire avec de vrais runs LLM : c'est la latence du LLM qui fait la différence entre les deux modes |
| C6 pas d'information du futur | chaque message a une heure de remise ; un habitant ne lit que ce qui est déjà arrivé ; les violations sont comptées (`temporal_violations` dans `run.end`) |
| D1, D2, D3 | Godot : scène, clic sur un habitant, boutons de commande |
| E1 baseline à règles | `LLM_AGENTS=none` ; 20 runs déjà faits |
| E2 au moins 5 runs × 2 configurations | `BATCH=5` ; `tools/analyse_runs.py` avertit s'il manque des runs |
| E3 rejeu | service `replay`, sans appel LLM |
| E4 coût | appels, tokens, latence moyenne et p95 dans `run.end` ; l'énergie n'est pas mesurée |
| E5 répartition des modèles comme levier | changer `LLM_AGENTS`, `LLM_BIG`, `LLM_SMALL` et comparer avec `analyse_runs.py` |
| F1, F2 RAG | `sim/rag.py`, `sim/eval_rag.py` |
| F3 données | section 8 |
| F4 schémas | [evenements.md](evenements.md), validation dans la passerelle, `alerte.dlq` |
| F5 requêtes reproductibles | `tools/analyse_runs.py` (lit seulement les JSONL) |
| F6 traçabilité des indicateurs | le panneau montre l'événement source ; le même calcul (`sim/indicators.py`) tourne dans la passerelle et en fin de run |

## 11. Écarts avec la fiche, limites, et suite

**Écarts assumés avec la fiche**
- Les positions de Lina (A01) et Yassine (A02) données par la fiche tombent dans l'emprise de l'école. Elles ont été
  déplacées juste à côté (A01 en (-44, 36), A02 en (-32, 14)). Les positions d'origine restent dans `pos_fiche`.
- La fiche parle d'un axe Z ; dans Godot, l'axe vertical est Y. Les coordonnées (x, z) de la fiche sont donc posées au sol,
  la hauteur venant du terrain. À signaler dans le rapport.
- Le réseau de contacts de la fiche est orienté (Lina écrit à Salma, mais Salma n'écrit pas à Lina) : il est gardé tel quel.

**Limites honnêtes**
- **Aucun vrai modèle Ollama n'a été testé** pendant le développement (le téléchargement des modèles était bloqué).
  Toute la chaîne a été testée avec un faux LLM à heuristiques. Le premier vrai run peut révéler des réponses lentes,
  ou des JSON à corriger dans le prompt.
- Le rendu 3D a été vérifié en mode compatibilité (sans carte graphique). Sur un vrai PC, l'eau et les ombres seront
  plus jolies, mais il faut vérifier la fluidité.
- Seuls les runs à règles ont des résultats chiffrés. Les comparaisons LLM / règles, grand / petit modèle et
  sync / async restent à faire.
- L'énergie n'est pas mesurée (E4 la demande « quand elle est mesurable »).

**Suite conseillée**
1. Lancer un run réel avec Ollama, regarder les justifications dans l'inspecteur, ajuster le prompt si besoin.
2. Faire les lots : 5 runs × (règles seules / LLM) × (09:10 / 09:30) × (sync / async), puis `analyse_runs.py`.
3. Pour B4, choisir une forte variation de `common_context` et raconter les messages qui l'ont précédée (le JSONL les contient).
4. Lancer `eval_rag.py` avec Ollama pour avoir le score du mode hybride.

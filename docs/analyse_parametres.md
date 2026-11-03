# Analyse des paramètres et des comportements

Étude de sensibilité d'ALERTE 3D : quels paramètres décident du sort des habitants, et quels profils s'en sortent le moins bien.

## Méthode

- **105 runs** : 21 configurations × 5 graines (`SEED` 1 à 5), 100 habitants, **100 % règles** (aucun LLM), mode sync.
- Référence : scénario `pont_ferme` (pont fermé à 09:20), rumeur à 09:20, réseau OK.
- On fait varier **la vitesse de la crue** et **l'heure de l'alerte d'évacuation** de la mairie (grille complète), puis **un seul paramètre à la fois** autour de la référence (crue rapide, alerte 09:05).
- Les réglages de la page Villageois sont ignorés, pour que seuls les paramètres de la grille changent.
- Reproductible : `python tools/etude_parametres.py run`, puis `python tools/etude_parametres.py rapport`. Runs dans `data/etude/`.

Les seuils de danger (30 cm dehors, 50 cm au rez-de-chaussée pour qui ne peut pas monter, victime après 15 min) et de dégâts (5 / 20 / 50 cm) sont des **paramètres de maquette**, pas des données réelles.

## 1. Crue × heure de l'alerte

| crue | alerte | au refuge | à l'étage chez soi | victimes | maisons sinistrées | départ moyen (min après 09:00) | contexte commun | lecture juste du pont |
|---|---|---|---|---|---|---|---|---|
| lente | 09:01 | **98 %** | 1 % | 0 | 16 | 7,9 | 24 % | 0 % |
| lente | 09:05 | **96 %** | 3 % | 0 | 16 | 11,8 | 19 % | 2 % |
| lente | 09:10 | **66 %** | 34 % | 0,8 | 16 | 16,5 | 22 % | 9 % |
| lente | 09:20 | **25 %** | 75 % | 1 | 16 | 22,6 | 2 % | 48 % |
| lente | 09:30 | **25 %** | 75 % | 1 | 16 | 25,2 | 0 % | 60 % |
| rapide | 09:01 | **56 %** | 44 % | 1,2 | 16 | 7,8 | 41 % | 0 % |
| rapide | 09:05 | **12 %** | 88 % | 3 | 16 | 11,5 | 28 % | 0 % |
| rapide | 09:10 à 09:30 | **0 %** | 100 % | 3 | 16 | 14,8 à 16,6 | 0 à 12 % | 6 à 23 % |
| éclair | toutes | **0 %** | 100 % | 4,8 à 5 | 25 | 7,6 à 9,4 | 0 à 9 % | 0 % |

Écarts-types entre graines : au plus ± 5 points sur « au refuge », souvent 0. Les résultats sont stables.

**Ce qu'on lit**

1. **L'heure de l'alerte est le premier facteur.** En crue lente, passer de 09:05 à 09:20 fait tomber la part des habitants au refuge de 96 % à 25 %. Il existe une fenêtre au-delà de laquelle alerter ne sert plus à rien : les routes sont déjà inondées.
2. **Cette fenêtre se referme avec la vitesse de la crue** : environ 09:10 en crue lente, 09:05 en crue rapide. En crue éclair, elle n'existe pas : même une alerte à 09:01 ne permet à personne d'atteindre un refuge.
3. **Les habitants partent 7 à 8 minutes après l'alerte**, quelle que soit la crue. Ce délai incompressible (préparation, confirmation, famille) explique pourquoi une alerte « juste à temps » arrive trop tard.
4. **Quand l'évacuation est impossible, la mise à l'abri à l'étage protège presque tout le monde.** Les victimes restent rares (0 à 5 sur 100).
5. **Les dégâts matériels ne dépendent que de la crue** : 16 maisons sinistrées en crue lente ou rapide, 25 en crue éclair, quelle que soit l'heure de l'alerte. L'alerte sauve des personnes, pas des maisons.
6. **Une information juste peut devenir fausse.** Les habitants partis tôt n'apprennent jamais que le pont a fermé à 09:20 : la « lecture juste du pont » est de 0 % avec une alerte à 09:01, contre 60 % à 09:30. Une information juste mais périmée fait autant de dégâts qu'une rumeur.

## 2. Un paramètre à la fois

Référence : crue rapide, alerte à 09:05.

| variante | au refuge | victimes | maisons sinistrées | contexte commun |
|---|---|---|---|---|
| **référence** | 12 % | 3 | 16 | 28 % |
| sans rumeur | 12 % | 3 | 16 | 28 % |
| rumeur tôt (09:03) | 12 % | 3 | 16 | 28 % |
| réseau mobile coupé à 09:03 | 13 % | 3 | 16 | **22 %** |
| pont ouvert (crue modérée) | **98 %** | 0 | 0 | **80 %** |
| 50 habitants | **27 %** | 0,4 | 16 | 32 % |
| 200 habitants | **6 %** | **8,8** | 16 | 28 % |

**Ce qu'on lit**

- **La rumeur n'a aucun effet sur des agents à règles.** Ils font confiance 1 sur 5 à un message anonyme et l'ignorent : les deux habitants visés continuent de passer par le pont. L'effet de la rumeur ne peut donc apparaître qu'avec des agents LLM, plus crédules et plus influençables. C'est l'objet de la comparaison règles / LLM.
- **La coupure du réseau dégrade le contexte commun** (28 % → 22 %) **sans changer le bilan** : la voix et la radio suffisent à faire partir les gens, mais ils partagent moins bien la même lecture de la situation.
- **La taille de la population semble peser lourd** (27 % au refuge avec 50 habitants, 12 % avec 100, 6 % avec 200), mais **c'est un effet de la taille des foyers**, pas du nombre d'habitants. Voir la section 4.
- **Le scénario fixe le décor** : une crue modérée avec le pont ouvert donne 98 % au refuge et aucune victime.

## 3. Comportements par profil

Crue rapide, toutes heures d'alerte confondues : 2 500 « habitants-runs ». Tous les habitants finissent par décider de partir ; ce qui change, c'est **quand**, et donc **comment ça finit**.

| trait du profil | départ moyen (min) | au refuge | victimes |
|---|---|---|---|
| préparation 2 min | 11,8 | **24 %** | 0 |
| préparation 3 min | 13,3 | 15 % | 24 |
| préparation 4 à 6 min | 13,8 à 14,9 | 6 % | 0 |
| préparation 8 min | **16,3** | **2 %** | **42** |
| avec personnes à charge | 15,5 | **4 %** | 42 |
| sans personnes à charge | 13,1 | 16 % | 24 |
| ne peut pas prendre d'escaliers | 15,3 | 10 % | **66 (toutes)** |
| peut prendre les escaliers | 13,1 | 15 % | 0 |
| mobilité lente (< 0,8) | 15,1 | 14 % | 29 |
| mobilité normale | 13,3 | 14 % | 37 |
| demande 2 confirmations | 13,2 | 9 % | 0 |
| demande 1 confirmation | 13,5 | 15 % | 66 |

**Ce qu'on lit**

- **Toutes les victimes sont des personnes qui ne peuvent pas monter à l'étage.** Pour elles, la mise à l'abri verticale n'existe pas : si l'évacuation échoue, il n'y a pas de plan B. Ce sont elles qu'il faut alerter et aider en premier.
- **Le temps de préparation est le trait qui pèse le plus sur l'évacuation** : avec 2 min de préparation, 24 % arrivent au refuge ; avec 8 min, 2 %. Les personnes avec des enfants ou des proches à charge préparent plus longtemps et partent environ 2,5 min plus tard.
- **Le seuil de risque n'a presque pas d'effet en crue rapide.** Le bulletin et l'eau visible font monter le risque perçu de tout le monde au-dessus de tous les seuils (37 à 73) en quelques minutes. Les tableaux par confirmations, radio ou vérification sont dominés par ce même effet de seuil saturé et par la répartition des personnes qui ne peuvent pas monter.

## 4. Pourquoi la population change le bilan

Le **nombre** d'habitants qui atteignent un refuge reste presque constant : 13,6 par run à 50 habitants, 12,2 à 100, 11 à 200. Ce n'est pas un problème de place : aucun refuge n'est plein à 100 ou 200 habitants. Les arrivés ne sont ni plus proches d'un refuge ni plus loin de la rivière que les autres.

La cause est dans le générateur de population (`sim/profiles.py`) : les habitants générés sont répartis sur **38 emplacements de maison fixes**. Ajouter des habitants agrandit donc les foyers au lieu d'ajouter des maisons.

| population | taille moyenne d'un foyer généré | habitants générés arrivés au refuge | départ moyen des générés (min) |
|---|---|---|---|
| 50 | 1,0 | 24 % | 11,2 |
| 100 | 2,3 | 7 % | 11,7 |
| 200 | 4,9 | 3 % | 12,3 |

Un foyer attend tous ses membres avant de partir (jusqu'à 5 min), puis marche à l'allure du plus lent. Plus le foyer est grand, plus le départ est tardif et la marche lente, et plus il risque de trouver la route coupée. Les 12 habitants de la fiche, qui gardent chacun leur propre maison, s'en sortent pareil dans les trois cas (environ 5 sur 12).

**Conclusion** : c'est un artefact du modèle. Dans une vraie ville plus peuplée, il y aurait plus de maisons. Le résultat utile est ailleurs : **la taille du foyer ralentit l'évacuation**, parce qu'on part ensemble et au rythme du plus lent.

## 5. Sensibilité aux seuils de maquette

Les seuils de danger s'appuient sur des repères réels :
- 30 cm suffisent à emporter une voiture (consigne officielle) ;
- au-delà de 50 cm, une porte peut être bloquée par la pression de l'eau, et les pompiers interdisent de s'engager à pied s'il y a du courant.

La recherche montre que la stabilité d'un piéton dépend de la hauteur **et** de la vitesse du courant (Arrighi et al., HESS 2017). Un seuil sur la seule hauteur reste donc une simplification. Pour vérifier qu'elle ne fausse pas les conclusions, chaque seuil a été changé seul autour de la référence (crue rapide, alerte 09:05, 5 graines) :

| seuil changé | au refuge | victimes |
|---|---|---|
| référence (dehors 30 cm, rez-de-chaussée 50 cm, victime après 15 min) | 12 % | 3 |
| danger dehors à 20 cm | 12 % | 3 |
| danger dehors à 40 cm | 12 % | 3 |
| danger au rez-de-chaussée à 30 cm | 12 % | 3,6 |
| danger au rez-de-chaussée à 70 cm | 12 % | **0** |
| victime après 5 min | 12 % | 3 |
| victime après 30 min | 12 % | 3 |

**Ce qu'on lit**

- **L'évacuation ne dépend pas de ces seuils** : ils mesurent les conséquences, mais ne changent pas les décisions des habitants. Les conclusions des sections 1 à 4 tiennent quelles que soient leurs valeurs.
- **Le nombre de victimes ne dépend que du seuil au rez-de-chaussée.** En crue rapide, l'eau ne dépasse pas 79 cm devant les maisons : avec un seuil à 70 cm, presque personne n'est en danger. Le chiffre exact des victimes n'est donc pas fiable. Le constat qualitatif, lui, l'est : **les victimes sont toujours les personnes qui ne peuvent pas monter à l'étage**.
- Le seuil « dehors » ne joue pas : un habitant surpris par l'eau entre tout de suite dans le bâtiment le plus proche.

## Synthèse

1. **Le temps est la ressource rare** : heure de l'alerte, vitesse de la crue et temps de préparation des habitants décident presque tout.
2. **Le plan B (rester à l'étage) protège la majorité**, mais pas les personnes qui ne peuvent pas monter : ce sont les victimes dans 100 % des cas.
3. **Les dégâts matériels sont fixés par la crue**, pas par le comportement.
4. **L'information compte surtout pour la cohérence collective** (contexte commun), moins pour le bilan humain dans ces scénarios.

## Limites

- **Agents à règles seulement.** Les agents LLM restent à comparer : c'est là que la rumeur et la confiance peuvent jouer. Ces runs sont plus lents, environ 1 h en temps réel chacun avec Ollama, et limités par le quota gratuit de Groq.
- Paramètres de maquette pour la crue, le danger et les dégâts. Les ordres de grandeur comptent, pas les valeurs exactes. Le nombre de victimes dépend du seuil au rez-de-chaussée (section 5).
- Le générateur de population garde un nombre fixe de maisons : pour étudier la densité de population, il faudrait ajouter des maisons plutôt qu'agrandir les foyers (section 4).

## Sources

- Ministère de la Transition écologique, [« Boîte à risque inondation »](https://www.ecologie.gouv.fr/sites/default/files/documents/Fascicule%20de%20jeu%20Bo%C3%AEte%20%C3%A0%20risque%20inondation%202025.pdf) : 30 cm emportent une voiture, 50 cm peuvent bloquer une porte.
- DREAL PACA, [plaquette « inondation et voiture »](https://www.dre.paca.developpement-durable.gouv.fr/IMG/pdf/flyer_inondation_voiture.pdf).
- ENASIS, [fiche d'intervention en milieu inondé](https://enasis.fr/pluginfile.php/79384/mod_resource/content/3/60004_intervention_milieu_inonde_v3.pdf) : engagement à pied interdit au-delà de 50 cm avec un courant de plus de 0,2 m/s.
- Arrighi, Oumeraci et Castelli, [« Hydrodynamics of pedestrians' instability in floodwaters »](https://hess.copernicus.org/articles/21/515/2017/), Hydrology and Earth System Sciences, 2017.
- Les traits de profil ne sont pas indépendants (exemple : les profils à charge préparent plus longtemps). Le tableau 3 montre des associations, pas des causes isolées.

-- ALERTE 3D : les embeddings des souvenirs (pgvector). Un souvenir devient un vecteur de 768 nombres qui représente son SENS :
-- deux phrases qui disent la même chose avec des mots différents ont des vecteurs proches.
-- Prérequis : les vecteurs sont calculés par la simulation pendant le run, ou d'un coup par :
--     docker compose run --rm --no-deps sim python eval_rag.py souvenirs
-- pgAdmin : sélectionner UNE requête (du SELECT jusqu'au ;) puis F5. Run d'exemple 7086a147.

SET search_path = memoire, public;   -- à exécuter en premier, une fois


-- ===== 1. LE CACHE DES VECTEURS : un vecteur par contenu, pas par souvenir ===================================
-- Un message relayé se retrouve à l'identique dans des dizaines de mémoires : on ne calcule son vecteur qu'une fois.
SELECT count(*) AS souvenirs,
       count(DISTINCT s.cle) AS contenus_differents,
       count(DISTINCT v.cle) AS contenus_vectorises,
       round(100 - 100.0 * count(DISTINCT s.cle) / count(*)) AS calculs_evites_pct
FROM souvenir s LEFT JOIN vecteur v USING (cle)
WHERE s.run_id = '7086a147';


-- ===== 2. À QUOI RESSEMBLE UN VECTEUR ========================================================================
SELECT left(texte, 70) AS souvenir, modele, vector_dims(embedding) AS dimensions,
       (embedding::real[])[1:5] AS cinq_premiers_nombres
FROM vecteur WHERE texte LIKE 'rumeur me dit%' LIMIT 1;


-- ===== 3. CHERCHER PAR LE SENS : les souvenirs les plus proches de la rumeur ===================================
-- Distance cosinus (<=>) : 0 = même sens. Les plus proches parlent du pont fermé, avec d'autres mots.
WITH ref AS (SELECT embedding FROM vecteur WHERE texte LIKE 'rumeur me dit%' LIMIT 1)
SELECT round((1 - (v.embedding <=> ref.embedding))::numeric, 3) AS proximite, left(v.texte, 110) AS souvenir
FROM vecteur v, ref
ORDER BY v.embedding <=> ref.embedding
LIMIT 8;


-- ===== 4. UNE QUESTION REFORMULÉE : les mots seuls échouent, le sens retrouve ================================
-- Adam (A06) a reçu la rumeur. On lui demande, sans aucun mot de la rumeur : « un inconnu raconte qu'il ne faut plus
-- emprunter l'ouvrage ». La question a été vectorisée à l'avance (table question).

-- 4a. Par les mots seuls : rien
SELECT * FROM rag_souvenirs('7086a147', 'A06', 'un inconnu raconte qu''il ne faut plus emprunter l''ouvrage', '{}', '{}', 600, 3);

-- 4b. Par les mots + le sens : la rumeur arrive en tête (colonne sens = proximité de sens)
SELECT * FROM rag_souvenirs_hybride('7086a147', 'A06', 'un inconnu raconte qu''il ne faut plus emprunter l''ouvrage', '{}', '{}', 600,
       (SELECT embedding FROM question WHERE question = 'un inconnu raconte qu''il ne faut plus emprunter l''ouvrage'), 3, 0.5);


-- ===== 5. AUTRE EXEMPLE : « on ne peut plus traverser la rivière par le centre » (Salma, A05) ================
-- 5a. Les mots seuls
SELECT * FROM rag_souvenirs('7086a147', 'A05', 'on ne peut plus traverser la rivière par le centre', '{}', '{}', 600, 3);

-- 5b. Les mots + le sens : les souvenirs « le Pont Central est FERMÉ »
SELECT * FROM rag_souvenirs_hybride('7086a147', 'A05', 'on ne peut plus traverser la rivière par le centre', '{}', '{}', 600,
       (SELECT embedding FROM question WHERE question = 'on ne peut plus traverser la rivière par le centre'), 3, 0.5);


-- ===== 6. LE BILAN DU BANC (8 questions × habitants concernés, 69 cas) =========================================
-- Résultats de « python eval_rag.py souvenirs » (hit@3 = un souvenir pertinent dans les 3 premiers ; MRR = rang moyen) :
--   mots seuls                  hit@3 0,17   MRR 0,17
--   mots + sens (poids 0,5)     hit@3 0,90   MRR 0,80   <- réglage par défaut de la simulation (SOUVENIR_SENS)
--   mots + sens (poids 4)       hit@3 0,78   MRR 0,55   (trop de sens : les mots, l'importance et la fraîcheur ne comptent plus assez)
SELECT question, modele, vector_dims(embedding) AS dimensions FROM question ORDER BY question;

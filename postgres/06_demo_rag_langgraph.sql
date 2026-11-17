-- ALERTE 3D : le RAG des agents dans PostgreSQL, et leur cycle de décision en graphe LangGraph.
-- Avant de décider, un agent LLM appelle ces fonctions (sim/memoire_pg.py) : documents du quartier, ses souvenirs,
-- et ce que dit le groupe du quartier. pgAdmin : sélectionner UNE requête (du SELECT jusqu'au ;) puis F5.
-- Les étapes 5 et 6 lisent le dernier run écrit en direct par la simulation (docker compose up, puis un run).

SET search_path = memoire, public;   -- à exécuter en premier, une fois


-- ===== 1. LA BASE DE CONNAISSANCES DU QUARTIER, DANS POSTGRESQL ==============================================
-- Chaque document a son index plein texte (tsv) et son vecteur de sens (embedding, pgvector), calculé par Ollama.
SELECT doc_id, source, annee, fiable, vector_dims(embedding) AS dimensions_du_vecteur, left(texte, 80) AS debut_du_texte
FROM document ORDER BY doc_id LIMIT 8;


-- ===== 2. RAG, DOCUMENTS : les plus pertinents pour la question de l'agent ===================================
SELECT d.doc_id, d.source, d.annee, d.fiable, r.score, left(d.texte, 110) AS texte
FROM rag_documents('pont central fermé ouvert rumeur vérifier', 3) r
JOIN document d USING (doc_id);


-- ===== 3. RAG, SOUVENIRS : ce dont Salma se souvient, à deux moments ==========================================
-- 3a. À 09:25 : ses souvenirs sur le pont remontent (texte proche + sujets et personnes en commun + fraîcheur)
SELECT * FROM rag_souvenirs('7086a147', 'A05', 'pont central fermé ouvert rumeur vérifier',
                            '{pont,eau,message,souvenir,verification}', '{A01,A08}', 565, 3);

-- 3b. À 09:15 : rien sur le pont, elle ne peut pas se souvenir de ce qu'elle n'a pas encore vécu
SELECT * FROM rag_souvenirs('7086a147', 'A05', 'pont central fermé ouvert rumeur vérifier',
                            '{pont,eau,message,souvenir,verification}', '{A01,A08}', 555, 3);


-- ===== 4. RAG, MÉMOIRE COLLECTIVE : ce que dit le groupe du quartier ==========================================
-- Lu seulement par les habitants inscrits au groupe du quartier, et seulement si le réseau passe.
-- Run à 100 habitants : deux versions se contredisent sur le pont.
SELECT * FROM rag_collectif('c7862089');


-- ===== 5. EN DIRECT : ce que les agents LLM ont retrouvé, et le chemin suivi dans le graphe LangGraph =========
WITH dernier AS (
    SELECT e.run_id FROM evenement e JOIN run r USING (run_id)
    WHERE e.type = 'agent.think' AND e.data->>'memoire' = 'postgres'
    ORDER BY r.charge_le DESC LIMIT 1
)
SELECT hm(e.t_sim) AS heure, e.agent_id, e.data->>'decider' AS decideur, e.data->'chemin' AS chemin_langgraph,
       e.data->>'action' AS action, e.data->'retrieved' AS ce_que_le_rag_a_retrouve
FROM evenement e JOIN dernier USING (run_id)
WHERE e.type = 'agent.think' AND e.data->>'memoire' = 'postgres'
ORDER BY e.t_sim LIMIT 10;


-- ===== 6. LE GRAPHE LANGGRAPH EN CHIFFRES : quels chemins les habitants ont suivis =============================
-- observer > regles > agir                              : agent à règles
-- observer > se_souvenir > decider > croire > agir      : agent LLM, réponse valide
-- observer > se_souvenir > decider > regles > agir      : agent LLM, réponse invalide -> secours par les règles
WITH dernier AS (
    SELECT e.run_id FROM evenement e JOIN run r USING (run_id)
    WHERE e.type = 'agent.think' AND e.data->>'memoire' = 'postgres'
    ORDER BY r.charge_le DESC LIMIT 1
)
SELECT array_to_string(ARRAY(SELECT jsonb_array_elements_text(e.data->'chemin')), ' > ') AS chemin, count(*) AS reflexions
FROM evenement e JOIN dernier USING (run_id)
WHERE e.type = 'agent.think'
GROUP BY 1 ORDER BY 2 DESC;

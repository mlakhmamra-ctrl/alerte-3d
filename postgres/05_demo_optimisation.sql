-- ALERTE 3D : optimiser la mémoire des habitants. Moins stocker (fusion), mieux oublier (importance), résumer (consolidation),
-- retrouver vite (index). pgAdmin : sélectionner UNE requête (du SELECT jusqu'au ;) puis F5. Run d'exemple 7086a147.

SET search_path = memoire;   -- à exécuter en premier, une fois


-- ===== 1. LE PROBLÈME : la mémoire se remplit de répétitions ===================================================
SELECT a.nom, regexp_replace(s.texte, '^[0-9]{2}:[0-9]{2} — ', '') AS souvenir_repete, count(*) AS fois
FROM souvenir s JOIN agent a USING (run_id, agent_id)
WHERE s.run_id = '7086a147'
GROUP BY a.nom, 2 HAVING count(*) > 1
ORDER BY fois DESC LIMIT 5;


-- ===== 2. MOINS STOCKER : fusionner les souvenirs répétés ====================================================
-- 2a. Salma : combien de souvenirs avant / après fusion
SELECT (SELECT count(*) FROM souvenir         WHERE run_id = '7086a147' AND agent_id = 'A05') AS souvenirs_bruts,
       (SELECT count(*) FROM souvenir_compact WHERE run_id = '7086a147' AND agent_id = 'A05') AS apres_fusion;

-- 2b. Ce que deviennent les répétitions : une ligne, avec la période et le nombre de fois
SELECT hm(debut) AS heure, texte AS souvenir_fusionne, fois
FROM souvenir_compact
WHERE run_id = '7086a147' AND agent_id = 'A05' AND fois > 1
ORDER BY debut;

-- 2c. Le gain sur tout le quartier, pour les deux runs
SELECT r.run_id, r.n_agents AS habitants,
       (SELECT count(*) FROM souvenir s         WHERE s.run_id = r.run_id) AS souvenirs_bruts,
       (SELECT count(*) FROM souvenir_compact c WHERE c.run_id = r.run_id) AS apres_fusion,
       round(100 - 100.0 * (SELECT count(*) FROM souvenir_compact c WHERE c.run_id = r.run_id)
                         / (SELECT count(*) FROM souvenir s WHERE s.run_id = r.run_id)) AS place_gagnee_pct
FROM run r ORDER BY r.n_agents;


-- ===== 3. MIEUX OUBLIER : quand il n'y a de la place que pour 12 souvenirs, lesquels garder ? ===================
-- 3a. Le bilan en une ligne : la méthode actuelle (garder les 12 plus récents) contre l'oubli par importance
WITH recents AS (
    SELECT texte, tags FROM souvenir WHERE run_id = '7086a147' AND agent_id = 'A05' ORDER BY t_sim DESC, id DESC LIMIT 12
), importants AS (
    SELECT souvenir AS texte FROM memoire_de_travail('7086a147', 'A05', 12)
)
SELECT 'les 12 plus récents (actuel)' AS methode,
       count(*) FILTER (WHERE texte ILIKE '%pont%') AS souvenirs_sur_le_pont,
       count(*) FILTER (WHERE texte LIKE '%bloqué%' OR texte LIKE '%demi-tour%') AS repetitions_inutiles
FROM recents
UNION ALL
SELECT 'les 12 plus importants (optimisé)',
       count(*) FILTER (WHERE texte ILIKE '%pont%'),
       count(*) FILTER (WHERE texte LIKE '%bloqué%' OR texte LIKE '%demi-tour%')
FROM importants;

-- 3b. Méthode actuelle : les 12 plus récents (Salma a oublié tout ce qu'elle savait du pont)
SELECT hm(t_sim) AS heure, texte, importance(tags, texte) AS importance
FROM (SELECT * FROM souvenir WHERE run_id = '7086a147' AND agent_id = 'A05' ORDER BY t_sim DESC, id DESC LIMIT 12) x
ORDER BY t_sim, id;

-- 3c. Méthode optimisée : les 12 plus importants (5 = vu ou vérifié soi-même, 1 = répétition)
SELECT * FROM memoire_de_travail('7086a147', 'A05', 12);


-- ===== 4. RÉSUMER : remplacer les vieux souvenirs par un résumé ================================================
-- Exécuter 4a à 4d UNE PAR UNE.
-- 4a.
BEGIN;
-- 4b. Tout ce que Salma a vécu jusqu'à 09:26 devient UN souvenir-résumé (seul ce qui comptait est gardé)
SELECT * FROM consolider('7086a147', 'A05', 566);
-- 4c. Sa mémoire maintenant : le résumé, puis les souvenirs récents
SELECT hm(t_sim) AS heure, texte, importance(tags, texte) AS importance
FROM souvenir WHERE run_id = '7086a147' AND agent_id = 'A05' ORDER BY t_sim, id LIMIT 6;
-- 4d. Annuler (la démo peut être rejouée)
ROLLBACK;


-- ===== 5. RETROUVER VITE : l'index plein texte ===============================================================
-- Le plan d'exécution montre que PostgreSQL passe par l'index souvenir_tsv au lieu de lire toute la table.
EXPLAIN
SELECT texte FROM souvenir
WHERE run_id = 'c7862089' AND tsv @@ to_tsquery('french', 'pont & ferme');

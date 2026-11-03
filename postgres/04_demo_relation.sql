-- ALERTE 3D : la relation entre mémoire individuelle, partagée et collective, en suivant UNE information :
-- « le Pont Central est fermé », vue par Rami (A08) à 09:20.
-- pgAdmin : sélectionner UNE requête (du SELECT jusqu'au ;) puis F5. Run d'exemple 7086a147 (12 habitants).

SET search_path = memoire;   -- à exécuter en premier, une fois


-- ===== 1. MÉMOIRE INDIVIDUELLE : Rami voit le pont fermé et le note dans SON journal ==========================
SELECT hm(t_sim) AS heure, texte AS souvenir_de_rami, tags
FROM souvenir
WHERE run_id = '7086a147' AND agent_id = 'A08' AND t_sim BETWEEN 559 AND 561
ORDER BY t_sim, id;


-- ===== 2. MÉMOIRE PARTAGÉE : Rami envoie un message à ses contacts ===========================================
SELECT m.msg_id, m.auteur AS de, r.destinataire AS a, a.nom, r.canal, m.texte, m.claims AS ce_que_le_message_affirme
FROM message m
JOIN reception r USING (run_id, msg_id)
JOIN agent a ON a.run_id = r.run_id AND a.agent_id = r.destinataire
WHERE m.run_id = '7086a147' AND m.msg_id = 'm25';


-- ===== 3. LIEN partagé → individuel : le message est devenu un souvenir chez chacun ==========================
-- (le même message m25, mais une copie privée dans 3 journaux différents ; c'est le déclencheur message_recu qui les écrit)
SELECT s.agent_id, a.nom,
       CASE WHEN s.agent_id = m.auteur THEN 'expéditeur' ELSE 'destinataire' END AS role,
       s.texte AS souvenir_prive
FROM souvenir s
JOIN agent a   USING (run_id, agent_id)
JOIN message m USING (run_id, msg_id)
WHERE s.run_id = '7086a147' AND s.msg_id = 'm25'
ORDER BY role, s.agent_id;


-- ===== 4. MÉMOIRE COLLECTIVE : le tableau commun du quartier sur le pont ======================================
SELECT sujet, valeur, depuis, nb_messages, sources_independantes, officiel
FROM tableau_commun
WHERE run_id = '7086a147' AND sujet = 'pont';


-- ===== 5. LIEN partagé → collectif : comment 12 messages deviennent 7 sources ===============================
-- (un relais n'est pas une nouvelle source : Salma relaie la mairie, Lina relaie Salma -> écho, pas confirmation)
SELECT coalesce(origine, auteur) AS source_d_origine,
       count(*) AS messages,
       string_agg(msg_id || ' (' || coalesce(auteur, 'direct') || ')', ', ' ORDER BY t_sim) AS messages_et_qui_les_a_envoyes,
       min(hm(t_sim)) AS premiere_fois
FROM message
WHERE run_id = '7086a147' AND claims @> '{"bridge": "ferme"}'
GROUP BY coalesce(origine, auteur)
ORDER BY min(t_sim);


-- ===== 6. LES TROIS ENSEMBLE DANS LE TEMPS : l'information se répand ========================================
-- habitants qui le savent (mémoires individuelles) / messages échangés (partagée) / sources au tableau (collective)
WITH info AS (
    SELECT msg_id, t_sim, coalesce(origine, auteur) AS source
    FROM message WHERE run_id = '7086a147' AND claims @> '{"bridge": "ferme"}'
)
SELECT hm(t) AS heure,
       (SELECT count(DISTINCT s.agent_id) FROM souvenir s
         WHERE s.run_id = '7086a147' AND s.t_sim <= t
           AND (s.msg_id IN (SELECT msg_id FROM info) OR s.texte LIKE '%vu moi-même que le Pont Central est fermé%'))
           AS habitants_qui_le_savent,
       (SELECT count(*) FROM info WHERE t_sim <= t) AS messages_partages,
       (SELECT count(DISTINCT source) FROM info WHERE t_sim <= t) AS sources_au_tableau
FROM generate_series(558, 572, 2) AS t
ORDER BY t;


-- ===== 7. EN DIRECT : un seul partage met à jour les trois mémoires ==========================================
-- Exécuter les requêtes 7a à 7e UNE PAR UNE, dans l'ordre.

-- 7a. Compteur des trois mémoires AVANT
SELECT (SELECT count(*) FROM souvenir WHERE run_id = '7086a147') AS memoire_individuelle_souvenirs,
       (SELECT count(*) FROM message  WHERE run_id = '7086a147') AS memoire_partagee_messages,
       (SELECT nb_messages || ' messages, ' || cardinality(sources) || ' sources' FROM fait_collectif
         WHERE run_id = '7086a147' AND sujet = 'pont' AND valeur = 'ferme') AS memoire_collective_pont_ferme;

-- 7b. Ouvrir une transaction
BEGIN;

-- 7c. Nora (A03) partage avec Yassine (A02) et Karim (A04) : une seule ligne de SQL
SELECT partager('7086a147', 'A03', 'À 09:59 je confirme : l''eau passe sur le Pont Central.',
                '{"bridge": "ferme"}', ARRAY['A02', 'A04'], 599) AS nouveau_message;

-- 7d. Compteur APRÈS (relancer la requête 7a) : +3 souvenirs, +1 message, +1 message et +1 source au tableau.
--     Et les trois souvenirs créés automatiquement :
SELECT s.agent_id, a.nom, s.texte
FROM souvenir s JOIN agent a USING (run_id, agent_id)
WHERE s.run_id = '7086a147' AND s.msg_id LIKE 'x%'
ORDER BY s.agent_id;

-- 7e. Tout annuler (la démo peut être rejouée)
ROLLBACK;


-- ===== 8. CHACUN NE VOIT QUE SA COPIE (Row Level Security) ===================================================
-- Exécuter 8a, 8b, 8c une par une.
-- 8a.
SET ROLE agent_sim;
SET memoire.agent = 'A05';
-- 8b. Salma voit le souvenir du message de Rami dans SA mémoire, mais plus celui de Rami ni celui d'Inès ;
--     le tableau commun, lui, reste visible par tous.
SELECT (SELECT count(*) FROM souvenir WHERE run_id = '7086a147' AND msg_id = 'm25') AS copies_de_m25_visibles,
       (SELECT string_agg(agent_id, ', ') FROM souvenir WHERE run_id = '7086a147' AND msg_id = 'm25') AS dans_la_memoire_de,
       (SELECT nb_messages FROM fait_collectif WHERE run_id = '7086a147' AND sujet = 'pont' AND valeur = 'ferme') AS tableau_commun_visible;
-- 8c. Redevenir administrateur
RESET ROLE;

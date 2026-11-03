-- ALERTE 3D : démonstration de la mémoire individuelle, partagée et collective dans PostgreSQL.
-- À lancer après 01_schema.sql et 02_charger.sql (run d'exemple : 12 habitants, le pont ferme à 09:20).
--
-- psql -d alerte -f postgres/03_demo.sql                       (tout d'un coup)
-- psql -d alerte   puis   \i postgres/03_demo.sql             (ou copier les requêtes une par une)
-- Autre run : psql -d alerte -v run=c7862089 -v agent=A28 -f postgres/03_demo.sql

\encoding UTF8
\pset pager off
\pset null '·'
SET search_path = memoire;
\if :{?run}   \else \set run 7086a147 \endif
\if :{?agent} \else \set agent A05    \endif
\if :{?msg}   \else \set msg m12      \endif


\echo '\n=== 0. Ce qu''il y a dans la base ======================================================'
SELECT r.run_id, r.scenario, r.n_agents AS habitants,
       (SELECT count(*) FROM souvenir s  WHERE s.run_id = r.run_id) AS souvenirs_prives,
       (SELECT count(*) FROM message m   WHERE m.run_id = r.run_id) AS messages_partages,
       (SELECT count(*) FROM reception x WHERE x.run_id = r.run_id) AS receptions,
       (SELECT count(*) FROM fait_collectif f WHERE f.run_id = r.run_id) AS faits_au_tableau
FROM run r ORDER BY r.n_agents;


\echo '\n=== 1. MÉMOIRE INDIVIDUELLE : ce dont l''habitant se souvient, avant 09:26 ================='
\echo '    (ses observations, ses actes, et les messages reçus : un message reçu devient un souvenir)'
SELECT a.nom, hm(s.t_sim) AS heure, s.texte, s.tags
FROM souvenir s JOIN agent a USING (run_id, agent_id)
WHERE s.run_id = :'run' AND s.agent_id = :'agent' AND s.t_sim < 566
ORDER BY s.t_sim, s.id;


\echo '\n=== 2. SE SOUVENIR : les 3 souvenirs les plus utiles pour une question ==================='
\echo '    (recherche plein texte en français + sujets en commun + fraîcheur ; même logique que sim/rag.py)'
SELECT * FROM rappel(:'run', :'agent', 'le pont central est-il fermé ?', '{pont}', 3);
\echo '    ... et ce qu''il savait à 09:15, avant que quiconque ait vu le pont :'
SELECT '09:15' AS a_l_heure, count(*) AS souvenirs_sur_le_pont
FROM rappel(:'run', :'agent', 'le pont central est-il fermé ?', '{pont}', 3, 555);


\echo '\n=== 3. MÉMOIRE PARTAGÉE : qui a dit quoi à qui ==========================================='
SELECT heure, de, string_agg(a || coalesce(' (' || canal || ')', ''), ', ' ORDER BY a) AS destinataires, genre, sauts, left(texte, 70) AS texte
FROM echanges
WHERE run_id = :'run' AND NOT perdu
GROUP BY msg_id, heure, de, genre, sauts, texte
ORDER BY min(heure), msg_id
LIMIT 25;

\echo '\n    Volume du partage, par genre de message et par canal :'
SELECT genre, count(DISTINCT msg_id) AS messages, count(*) AS receptions,
       count(*) FILTER (WHERE canal = 'face_a_face') AS face_a_face,
       count(*) FILTER (WHERE canal = 'telephone')   AS telephone,
       count(*) FILTER (WHERE canal = 'groupe')      AS groupe,
       count(*) FILTER (WHERE perdu)                 AS perdues
FROM echanges WHERE run_id = :'run'
GROUP BY genre ORDER BY messages DESC;


\echo '\n=== 4. PROPAGATION : l''arbre de relais d''un message (requête récursive) ================='
WITH RECURSIVE arbre AS (
    SELECT msg_id, parent, coalesce(auteur, origine) AS de, 0 AS profondeur, t_sim, t_sim AS t0, texte,
           coalesce(auteur, origine) AS chemin
    FROM message WHERE run_id = :'run' AND msg_id = :'msg'
  UNION ALL
    SELECT m.msg_id, m.parent, m.auteur, a.profondeur + 1, m.t_sim, a.t0, m.texte, a.chemin || ' → ' || m.auteur
    FROM message m JOIN arbre a ON m.run_id = :'run' AND m.parent = a.msg_id
)
SELECT msg_id, repeat('   ', profondeur) || de AS relayé_par, profondeur AS sauts, hm(t_sim) AS heure, round(t_sim - t0, 1) AS minutes_apres,
       (SELECT string_agg(destinataire, ', ' ORDER BY destinataire) FROM reception r
         WHERE r.run_id = :'run' AND r.msg_id = arbre.msg_id AND NOT r.perdu) AS reçu_par,
       chemin
FROM arbre ORDER BY t_sim, msg_id;

\echo '\n    Portée de chaque information de départ : combien d''habitants touchés, en combien de sauts :'
WITH RECURSIVE desc_ AS (
    SELECT msg_id AS racine, msg_id, 0 AS profondeur FROM message WHERE run_id = :'run' AND parent IS NULL
  UNION ALL
    SELECT d.racine, m.msg_id, d.profondeur + 1 FROM message m JOIN desc_ d ON m.run_id = :'run' AND m.parent = d.msg_id
)
SELECT d.racine, coalesce(m.auteur, m.origine) AS source, left(m.texte, 55) AS information,
       count(DISTINCT d.msg_id) - 1 AS relais, max(d.profondeur) AS sauts_max,
       count(DISTINCT r.destinataire) AS habitants_touches
FROM desc_ d JOIN message m ON m.run_id = :'run' AND m.msg_id = d.racine
LEFT JOIN reception r ON r.run_id = :'run' AND r.msg_id = d.msg_id AND NOT r.perdu
GROUP BY d.racine, m.auteur, m.origine, m.texte
ORDER BY habitants_touches DESC LIMIT 10;


\echo '\n=== 5. MÉMOIRE COLLECTIVE : le tableau commun du quartier ================================='
\echo '    (3 relais d''une même source = 1 seule source indépendante : on distingue écho et confirmation)'
SELECT sujet, valeur, depuis, nb_messages, sources_independantes, officiel, part_des_messages_pct
FROM tableau_commun WHERE run_id = :'run' ORDER BY sujet, nb_messages DESC;

\echo '\n    Le tableau commun à trois moments (on le reconstruit depuis les messages : voyage dans le temps) :'
SELECT hm(t.t) AS a_l_heure, coalesce(m.claims->>'bridge', '(personne n''en parle)') AS le_pont_est, count(m.msg_id) AS messages,
       count(DISTINCT coalesce(m.origine, m.auteur)) AS sources, string_agg(DISTINCT coalesce(m.origine, m.auteur), ', ') AS qui
FROM (VALUES (555), (561), (571)) t(t)
LEFT JOIN message m ON m.run_id = :'run' AND m.t_sim <= t.t AND jsonb_typeof(m.claims->'bridge') = 'string'
GROUP BY t.t, m.claims->>'bridge' ORDER BY t.t;


\echo '\n=== 6. CROYANCES ET VÉRITÉ : qui a la bonne lecture de la situation à 09:25 ? =============='
SELECT agent_id, nom, pont, refuge, danger, certitude, pont_ok, refuge_ok, danger_ok, contexte_ok
FROM etat_a(:'run', 565) ORDER BY contexte_ok, agent_id;

\echo '\n    Contexte commun (B1) minute par minute : part des habitants justes sur les trois faits'
SELECT hm(t) AS heure, round(100.0 * avg(contexte_ok::int)) AS contexte_commun_pct,
       repeat('█', round(40 * avg(contexte_ok::int))::int) AS courbe,
       round(100.0 * avg(pont_ok::int)) AS pont_pct, round(100.0 * avg(danger_ok::int)) AS danger_pct
FROM generate_series((SELECT t_debut FROM run WHERE run_id = :'run'), (SELECT t_fin FROM run WHERE run_id = :'run'), 5) t,
     LATERAL etat_a(:'run', t)
GROUP BY t ORDER BY t;


\echo '\n=== 7. QUI A APPRIS QUE LE PONT EST FERMÉ, ET QUAND ? (le partage a-t-il marché ?) ========'
SELECT a.agent_id, a.nom,
       hm(min(m.t_sim)) AS appris_a,
       round(min(m.t_sim) - (SELECT min(t_sim) FROM evenement WHERE run_id = :'run' AND type = 'bridge.closed'), 1) AS minutes_apres_fermeture,
       (array_agg(coalesce(m.auteur, m.origine) ORDER BY m.t_sim))[1] AS appris_de,
       count(m.msg_id) AS fois_entendu
FROM agent a
LEFT JOIN reception r ON r.run_id = a.run_id AND r.destinataire = a.agent_id AND NOT r.perdu
LEFT JOIN message m   ON m.run_id = r.run_id AND m.msg_id = r.msg_id AND m.claims @> '{"bridge": "ferme"}'
WHERE a.run_id = :'run'
GROUP BY a.agent_id, a.nom ORDER BY min(m.t_sim) NULLS LAST;

\echo '\n    Les habitants qui ne l''ont jamais entendu (anti-jointure) :'
SELECT count(*) AS jamais_informes,
       coalesce(string_agg(a.agent_id || ' ' || a.nom, ', '), 'personne : tout le quartier a été informé') AS qui
FROM agent a
WHERE a.run_id = :'run' AND NOT EXISTS (
    SELECT 1 FROM reception r JOIN message m USING (run_id, msg_id)
    WHERE r.run_id = a.run_id AND r.destinataire = a.agent_id AND NOT r.perdu AND m.claims @> '{"bridge": "ferme"}');

\echo '\n    Les grands relais du quartier (combien d''habitants chacun a informés) :'
SELECT m.auteur, a.nom, count(DISTINCT m.msg_id) AS messages_envoyes, count(r.*) AS receptions_produites,
       count(DISTINCT r.destinataire) AS personnes_differentes
FROM message m JOIN agent a ON a.run_id = m.run_id AND a.agent_id = m.auteur
JOIN reception r ON r.run_id = m.run_id AND r.msg_id = m.msg_id AND NOT r.perdu
WHERE m.run_id = :'run' GROUP BY m.auteur, a.nom ORDER BY receptions_produites DESC LIMIT 5;


\echo '\n=== 8. PARTAGER EN DIRECT : une transaction, tout ou rien ================================='
\echo '    Omar (A10) voit l''eau sur le Pont Central et prévient A07 et A09.'
BEGIN;
SELECT partager(:'run', 'A10', 'À 09:58 l''eau passe sur le Pont Central, ne le prenez surtout pas !',
                '{"bridge": "ferme", "danger": true}', ARRAY['A07', 'A09'], 598) AS nouveau_message;
\echo '    -> le message, ses réceptions, les souvenirs des destinataires et le tableau commun sont à jour :'
SELECT de, a, canal, texte FROM echanges WHERE run_id = :'run' AND msg_id LIKE 'x%';
SELECT agent_id, texte FROM souvenir WHERE run_id = :'run' AND msg_id LIKE 'x%' ORDER BY agent_id;
SELECT sujet, valeur, nb_messages, sources_independantes FROM tableau_commun WHERE run_id = :'run' AND sujet = 'pont';
ROLLBACK;
\echo '    ROLLBACK : rien de tout cela n''est resté (la démo peut être rejouée).'
SELECT count(*) AS messages_x_restants FROM message WHERE run_id = :'run' AND msg_id LIKE 'x%';


\echo '\n=== 9. MÉMOIRE PRIVÉE PROTÉGÉE : un habitant ne lit que la sienne (Row Level Security) ====='
SET ROLE agent_sim;
SET memoire.agent = 'A05';
SELECT current_user AS role, current_setting('memoire.agent') AS je_suis,
       (SELECT count(*) FROM souvenir WHERE run_id = :'run') AS souvenirs_visibles,
       (SELECT count(DISTINCT agent_id) FROM souvenir WHERE run_id = :'run') AS memoires_visibles,
       (SELECT count(*) FROM message WHERE run_id = :'run') AS messages_visibles,
       (SELECT count(*) FROM fait_collectif WHERE run_id = :'run') AS faits_collectifs_visibles;
\echo '    (en administrateur : 268 souvenirs, 12 mémoires, 42 messages ; le tableau commun, lui, est visible en entier)'
\echo '    Les souvenirs de Rami (A08) ? Invisibles pour Salma :'
SELECT count(*) AS souvenirs_de_A08 FROM souvenir WHERE run_id = :'run' AND agent_id = 'A08';
\echo '    La même fonction de rappel, appelée par l''habitant, ne fouille que sa propre mémoire :'
SELECT 'A08 Rami' AS memoire_fouillee, count(*) AS souvenirs_trouves_par_salma FROM rappel(:'run', 'A08', 'pont fermé', '{pont}');
SELECT * FROM rappel(:'run', 'A05', 'pont fermé', '{pont}');
RESET ROLE;
RESET memoire.agent;


\echo '\n=== 10. REQUÊTER LE JOURNAL BRUT (JSONB + index GIN) ========================================'
EXPLAIN (COSTS OFF)
SELECT seq, type, agent_id FROM evenement WHERE data @> '{"claims": {"bridge": "ferme"}}';
SELECT type, count(*) AS evenements_qui_disent_pont_ferme
FROM evenement WHERE run_id = :'run' AND data @> '{"claims": {"bridge": "ferme"}}' GROUP BY type;


\echo '\n=== 11. PARTAGE EN TEMPS RÉEL (deux terminaux psql) ======================================'
\echo '    Terminal 1 :  LISTEN memoire_partagee;'
\echo '    Terminal 2 :  SELECT memoire.partager(''7086a147'', ''A10'', ''Le pont est fermé'', ''{"bridge":"ferme"}'', ARRAY[''A07'']);'
\echo '    Terminal 1 :  SELECT 1;   -> psql affiche « Notification asynchrone "memoire_partagee" ... »'
\echo '    Concurrence : lancer partager() dans les deux terminaux en même temps (BEGIN ... sans COMMIT dans le 1er) :'
\echo '    le 2e attend le verrou de la ligne (pont, ferme) du tableau commun, puis compte bien +2 messages.'

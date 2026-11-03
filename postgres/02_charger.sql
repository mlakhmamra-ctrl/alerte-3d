-- Charge un run de la simulation (data/runs/<run_id>.jsonl, un événement JSON par ligne) dans le schéma memoire.
-- Recharger le même run le remplace.
--
-- psql -d alerte -v fichier=data/runs/exemple_regles_pont_ferme.jsonl -f postgres/02_charger.sql

\set ON_ERROR_STOP on
\encoding UTF8
SET client_min_messages = warning;
SET search_path = memoire;

-- 1. lecture brute : une ligne du fichier = un document JSON (les délimiteurs \x01 \x02 n'apparaissent jamais : la ligne passe intacte)
CREATE TEMP TABLE brut (ligne text);
\set copie '\\copy brut FROM ' :'fichier' ' WITH (FORMAT csv, QUOTE E''\\x01'', DELIMITER E''\\x02'')'
:copie
CREATE TEMP TABLE ev AS
SELECT (j->>'run_id') AS run_id, (j->>'seq')::int AS seq, j->>'type' AS type, (j->>'t_sim')::numeric AS t_sim, j->>'agent' AS agent_id, j->'data' AS data
FROM (SELECT ligne::jsonb AS j FROM brut WHERE ligne <> '') x;

BEGIN;
DELETE FROM run WHERE run_id IN (SELECT DISTINCT run_id FROM ev);     -- tout le reste suit (ON DELETE CASCADE)

INSERT INTO run (run_id, scenario, graine, n_agents, t_debut, t_fin)
SELECT run_id, data->>'scenario', (data->>'seed')::int, (data->>'n_agents')::int, (data->>'t_start')::numeric, (data->>'t_end')::numeric
FROM ev WHERE type = 'run.start';

INSERT INTO evenement SELECT run_id, seq, type, t_sim, agent_id, data FROM ev;

-- 2. habitants : A01..An ; nom pris dans les profils modifiés du run, sinon dans les 12 profils de base (sim/profiles.py)
INSERT INTO agent (run_id, agent_id, nom)
SELECT r.run_id, id, coalesce(s.data #>> ARRAY['overrides', id, 'name'], b.nom)
FROM run r JOIN ev s ON s.run_id = r.run_id AND s.type = 'run.start'
CROSS JOIN LATERAL (SELECT 'A' || lpad(i::text, greatest(2, length(i::text)), '0') AS id FROM generate_series(1, r.n_agents) i) g
LEFT JOIN (VALUES ('A01','Lina'),('A02','Yassine'),('A03','Nora'),('A04','Karim'),('A05','Salma'),('A06','Adam'),
                  ('A07','Maya'),('A08','Rami'),('A09','Inès'),('A10','Omar'),('A11','Hiba'),('A12','Sami')) b(aid, nom) ON b.aid = id;

-- 3. croyances et vérité
INSERT INTO croyance SELECT run_id, agent_id, t_sim, seq, data->>'bridge', data->>'refuge', (data->>'danger')::boolean,
                            (data->>'risk')::int, (data->>'certainty')::numeric
FROM ev WHERE type = 'agent.belief';
INSERT INTO verite SELECT run_id, t_sim, seq, data->>'bridge', (data->>'danger')::boolean, ARRAY(SELECT jsonb_array_elements_text(data->'refuges'))
FROM ev WHERE type = 'world.truth';

-- 4. mémoire individuelle : ce que chacun a vu ou fait lui-même (mêmes phrases que Agent.remember dans sim/agents.py)
INSERT INTO souvenir (run_id, agent_id, t_sim, texte, tags)
SELECT run_id, agent_id, t_sim, hm(t_sim) || ' — ' || txt, tags FROM (
    SELECT run_id, agent_id, t_sim, seq,
        CASE type
            WHEN 'agent.observe' THEN CASE data->>'what'
                WHEN 'pont_ferme'  THEN 'j''ai vu moi-même que le Pont Central est fermé'
                WHEN 'rue_inondee' THEN 'trop d''eau (' || round((data->>'depth')::numeric * 100) || ' cm) ' || coalesce('rue ' || (data->>'street'), 'devant moi') || ', je fais demi-tour'
                ELSE 'l''eau monte près de chez moi (' || round((data->>'depth')::numeric * 100) || ' cm)' END
            WHEN 'agent.prepare'       THEN 'je prépare mon départ vers ' || coalesce(data->>'refuge', '?')
            WHEN 'agent.depart'        THEN 'je pars vers ' || (data->>'refuge') || ' par ' || replace(data->>'via', '_', ' ')
            WHEN 'agent.reroute'       THEN 'demi-tour (' || (data->>'reason') || ') : je vais vers ' || coalesce(data->>'refuge', 'chez moi')
            WHEN 'agent.shelter'       THEN 'je me mets à l''abri : ' || (data->>'reason')
            WHEN 'agent.blocked'       THEN 'je suis bloqué : ' || (data->>'reason')
            WHEN 'agent.verify'        THEN 'j''appelle le poste municipal pour vérifier le pont'
            WHEN 'agent.verify_result' THEN 'j''ai appelé le poste municipal : ' || (data->>'result')
            WHEN 'agent.arrive'        THEN 'je suis arrivé au refuge ' || (data->>'refuge')
            WHEN 'agent.distress'      THEN 'je suis en détresse : ' || (data->>'reason')
        END AS txt,
        CASE type
            WHEN 'agent.observe'       THEN ARRAY['observation', CASE WHEN data->>'what' = 'pont_ferme' THEN 'pont' ELSE 'eau' END]
            WHEN 'agent.verify'        THEN ARRAY['pont', 'verification']
            WHEN 'agent.verify_result' THEN ARRAY['pont', 'verification']
            WHEN 'agent.depart'        THEN ARRAY['depart', data->>'refuge']
            WHEN 'agent.reroute'       THEN ARRAY['reroute', 'pont']
            WHEN 'agent.arrive'        THEN ARRAY['refuge', data->>'refuge']
            ELSE ARRAY[replace(type, 'agent.', '')] END AS tags
    FROM ev WHERE agent_id IS NOT NULL) s
WHERE txt IS NOT NULL ORDER BY seq;

-- 5. mémoire partagée : messages puis réceptions (les déclencheurs remplissent le tableau commun et les souvenirs reçus)
CREATE TEMP TABLE msg AS
SELECT run_id, data->>'msg_id' AS msg_id, agent_id AS auteur, data->>'origin' AS origine, data->>'parent' AS parent,
       coalesce((data->>'hops')::int, 0) AS sauts, coalesce(data->>'kind', 'message') AS genre, data->>'text' AS texte,
       coalesce(data->'claims', '{}') AS claims, t_sim, seq, data->'to' AS dest, data->'canaux' AS canaux
FROM ev WHERE type = 'agent.say'
UNION ALL
SELECT run_id, data->>'msg_id', NULL, 'mairie', NULL, 0, 'officiel', data->>'text', coalesce(data->'claims', '{}'), t_sim, seq, data->'to', data->'canaux'
FROM ev WHERE type = 'official.bulletin'
UNION ALL
SELECT run_id, data->'msg_ids'->>0, NULL, 'rumeur', NULL, 0, 'rumeur', data->>'text', jsonb_build_object('bridge', data->>'claim'), t_sim, seq, data->'targets', NULL
FROM ev WHERE type = 'rumor';

-- messages relayés dont l'original n'est pas dans le journal (réponse du poste municipal à une vérification)
INSERT INTO msg
SELECT DISTINCT ON (m.run_id, m.parent) m.run_id, m.parent, NULL, 'mairie', NULL, 0, 'verification', m.texte, m.claims, m.t_sim, m.seq - 1,
       jsonb_build_array(m.auteur), NULL
FROM msg m WHERE m.parent IS NOT NULL AND NOT EXISTS (SELECT 1 FROM msg p WHERE p.run_id = m.run_id AND p.msg_id = m.parent)
ORDER BY m.run_id, m.parent, m.seq;

-- envois perdus sans aucun destinataire joint (réseau coupé) : le message existe quand même, il n'a atteint personne
INSERT INTO msg
SELECT run_id, data->>'msg_id', agent_id, agent_id, NULL, 0, coalesce(data->>'kind', 'message'), NULL, '{}', t_sim, seq, '[]', NULL
FROM ev e WHERE type = 'message.lost' AND NOT EXISTS (SELECT 1 FROM msg m WHERE m.run_id = e.run_id AND m.msg_id = e.data->>'msg_id');

INSERT INTO message (run_id, msg_id, auteur, origine, parent, sauts, genre, texte, claims, t_sim)
SELECT run_id, msg_id, auteur, origine, parent, sauts, genre, texte, claims, t_sim FROM msg ORDER BY seq;

INSERT INTO reception (run_id, msg_id, destinataire, canal)
SELECT m.run_id, m.msg_id, d, m.canaux->>d FROM msg m, jsonb_array_elements_text(m.dest) d
WHERE EXISTS (SELECT 1 FROM agent a WHERE a.run_id = m.run_id AND a.agent_id = d)
ORDER BY m.seq;

INSERT INTO reception (run_id, msg_id, destinataire, canal, perdu)
SELECT run_id, data->>'msg_id', d, NULL, true FROM ev, jsonb_array_elements_text(data->'to') d
WHERE type = 'message.lost'
ON CONFLICT DO NOTHING;
COMMIT;

ANALYZE;
SELECT r.run_id, r.scenario, r.n_agents AS habitants,
       (SELECT count(*) FROM evenement e WHERE e.run_id = r.run_id) AS evenements,
       (SELECT count(*) FROM souvenir s WHERE s.run_id = r.run_id) AS souvenirs,
       (SELECT count(*) FROM message m WHERE m.run_id = r.run_id) AS messages,
       (SELECT count(*) FROM reception x WHERE x.run_id = r.run_id) AS receptions,
       (SELECT count(*) FROM fait_collectif f WHERE f.run_id = r.run_id) AS faits_collectifs
FROM run r WHERE r.run_id IN (SELECT DISTINCT run_id FROM ev);

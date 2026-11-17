-- ALERTE 3D : mémoire individuelle, mémoire partagée et mémoire collective des habitants, dans PostgreSQL.
--
-- Trois niveaux de mémoire :
--   1. souvenir        : mémoire PRIVÉE de chaque habitant (ce qu'il a vu, fait, ou entendu).
--   2. message         : ce qui est PARTAGÉ d'un habitant à l'autre (qui a dit quoi, à qui, relayé depuis quel message).
--      reception         Recevoir un message écrit automatiquement un souvenir chez le destinataire (déclencheur).
--   3. fait_collectif  : le TABLEAU COMMUN du quartier : chaque affirmation (« le pont est fermé ») avec le nombre de
--                        messages et de sources indépendantes qui la portent. Tenu à jour par déclencheur.
-- À côté : croyance (ce que chacun croit, dans le temps) et verite (l'état réel du monde), pour mesurer le contexte commun.
--
-- psql -d alerte -f postgres/01_schema.sql      (efface et recrée le schéma « memoire »)

\set ON_ERROR_STOP on
SET client_min_messages = warning;
DROP SCHEMA IF EXISTS memoire CASCADE;
CREATE SCHEMA memoire;
SET search_path = memoire;

-- heure de simulation (minutes depuis minuit, 540 = 09:00) -> '09:00'
CREATE FUNCTION hm(t numeric) RETURNS text LANGUAGE sql IMMUTABLE AS
$$ SELECT lpad(div(floor(t), 60)::text, 2, '0') || ':' || lpad(mod(floor(t), 60)::text, 2, '0') $$;

CREATE TABLE run (
    run_id     text PRIMARY KEY,
    scenario   text,
    graine     int,
    n_agents   int,
    t_debut    numeric,
    t_fin      numeric,
    charge_le  timestamptz DEFAULT now()
);

CREATE TABLE agent (
    run_id    text REFERENCES run ON DELETE CASCADE,
    agent_id  text,
    nom       text,
    PRIMARY KEY (run_id, agent_id)
);

-- journal brut : tous les événements du run, tels que Kafka les a transportés
CREATE TABLE evenement (
    run_id    text REFERENCES run ON DELETE CASCADE,
    seq       int,
    type      text NOT NULL,
    t_sim     numeric NOT NULL,
    agent_id  text,
    data      jsonb NOT NULL,
    PRIMARY KEY (run_id, seq)
);
CREATE INDEX evenement_type ON evenement (run_id, type, t_sim);
CREATE INDEX evenement_data ON evenement USING gin (data jsonb_path_ops);

-- 1. mémoire individuelle ---------------------------------------------------------------------------------------------
CREATE TABLE souvenir (
    id        bigserial PRIMARY KEY,
    run_id    text NOT NULL,
    agent_id  text NOT NULL,
    t_sim     numeric NOT NULL,
    texte     text NOT NULL,
    tags      text[] NOT NULL DEFAULT '{}',          -- lieux, sujets, personnes concernés (comme Agent.remember)
    msg_id    text,                                   -- message d'où vient le souvenir, s'il a été transmis
    tsv       tsvector GENERATED ALWAYS AS (to_tsvector('french', texte)) STORED,
    cle       text GENERATED ALWAYS AS (md5(regexp_replace(texte, '^[0-9]{2}:[0-9]{2} — ', ''))) STORED,   -- même contenu = même clé (cache des vecteurs)
    FOREIGN KEY (run_id, agent_id) REFERENCES agent ON DELETE CASCADE
);
CREATE INDEX souvenir_agent ON souvenir (run_id, agent_id, t_sim);
CREATE INDEX souvenir_tsv   ON souvenir USING gin (tsv);
CREATE INDEX souvenir_tags  ON souvenir USING gin (tags);

-- 2. mémoire partagée -------------------------------------------------------------------------------------------------
CREATE TABLE message (
    run_id   text REFERENCES run ON DELETE CASCADE,
    msg_id   text,
    auteur   text,                 -- habitant qui l'envoie ; NULL = mairie, rumeur, poste municipal
    origine  text,                 -- source première (mairie, A02...) : se conserve de relais en relais
    parent   text,                 -- message relayé (pas de clé étrangère : un parent peut être hors du journal)
    sauts    int  NOT NULL DEFAULT 0,
    genre    text NOT NULL,        -- officiel, relais, observation, rumeur, correction, verification...
    texte    text,
    claims   jsonb NOT NULL DEFAULT '{}',   -- ce que le message affirme : {"bridge": "ferme", "danger": true}
    t_sim    numeric NOT NULL,
    PRIMARY KEY (run_id, msg_id)
);
CREATE INDEX message_parent ON message (run_id, parent);

CREATE TABLE reception (
    run_id        text,
    msg_id        text,
    destinataire  text,
    canal         text,                      -- face_a_face, telephone, groupe
    perdu         boolean NOT NULL DEFAULT false,
    t_recu        numeric,                   -- heure où le message est lu (envoi + délai du canal) ; NULL = heure d'envoi
    PRIMARY KEY (run_id, msg_id, destinataire),
    FOREIGN KEY (run_id, msg_id) REFERENCES message ON DELETE CASCADE
);
CREATE INDEX reception_dest ON reception (run_id, destinataire);

-- 3. mémoire collective : le tableau commun ---------------------------------------------------------------------------
CREATE TABLE fait_collectif (
    run_id         text REFERENCES run ON DELETE CASCADE,
    sujet          text,          -- pont, danger, niveau
    valeur         text,          -- ferme, ouvert, true, alerte...
    premiere_fois  numeric NOT NULL,
    derniere_fois  numeric NOT NULL,
    nb_messages    int NOT NULL DEFAULT 1,
    sources        text[] NOT NULL DEFAULT '{}',   -- origines distinctes : 3 relais d'une même source = 1 source
    officiel       boolean NOT NULL DEFAULT false,
    PRIMARY KEY (run_id, sujet, valeur)
);

-- croyances et vérité, pour mesurer le contexte commun ----------------------------------------------------------------
CREATE TABLE croyance (
    run_id     text,
    agent_id   text,
    t_sim      numeric,
    seq        int,
    pont       text,
    refuge     text,
    danger     boolean,
    risque     int,
    certitude  numeric,
    PRIMARY KEY (run_id, seq),
    FOREIGN KEY (run_id, agent_id) REFERENCES agent ON DELETE CASCADE
);
CREATE INDEX croyance_agent ON croyance (run_id, agent_id, t_sim DESC);

CREATE TABLE verite (
    run_id   text REFERENCES run ON DELETE CASCADE,
    t_sim    numeric,
    seq      int,
    pont     text,
    danger   boolean,
    refuges  text[],               -- refuges encore accessibles
    PRIMARY KEY (run_id, seq)
);

-- Déclencheurs : c'est ici que le partage devient mémoire ------------------------------------------------------------

-- Un message publié alimente le tableau commun (une ligne par affirmation) et prévient les clients à l'écoute.
CREATE FUNCTION message_publie() RETURNS trigger LANGUAGE plpgsql SET search_path = memoire AS $$
DECLARE c record; src text := coalesce(NEW.origine, NEW.auteur, 'inconnu');
BEGIN
    FOR c IN SELECT key, value FROM jsonb_each(NEW.claims) WHERE key IN ('bridge', 'danger', 'level') AND jsonb_typeof(value) <> 'null' LOOP
        INSERT INTO fait_collectif AS f (run_id, sujet, valeur, premiere_fois, derniere_fois, nb_messages, sources, officiel)
        VALUES (NEW.run_id, CASE c.key WHEN 'bridge' THEN 'pont' WHEN 'level' THEN 'niveau' ELSE c.key END, c.value #>> '{}',
                NEW.t_sim, NEW.t_sim, 1, ARRAY[src], src = 'mairie')
        ON CONFLICT (run_id, sujet, valeur) DO UPDATE SET           -- deux habitants qui partagent en même temps : la ligne est verrouillée,
            nb_messages   = f.nb_messages + 1,                     -- les deux mises à jour s'appliquent l'une après l'autre
            premiere_fois = least(f.premiere_fois, EXCLUDED.premiere_fois),
            derniere_fois = greatest(f.derniere_fois, EXCLUDED.derniere_fois),
            sources       = CASE WHEN src = ANY (f.sources) THEN f.sources ELSE f.sources || src END,
            officiel      = f.officiel OR EXCLUDED.officiel;
    END LOOP;
    -- memoire.direct = on : la simulation écrit elle-même les souvenirs (journal de l'agent, à l'heure exacte) ; pas de doublon
    IF NEW.auteur IS NOT NULL AND current_setting('memoire.direct', true) IS DISTINCT FROM 'on' THEN
        INSERT INTO souvenir (run_id, agent_id, t_sim, texte, tags, msg_id)
        VALUES (NEW.run_id, NEW.auteur, NEW.t_sim, hm(NEW.t_sim) || ' — j''ai partagé : « ' || coalesce(NEW.texte, '') || ' »',
                ARRAY['message'] || CASE WHEN NEW.claims ? 'bridge' THEN ARRAY['pont'] ELSE '{}' END, NEW.msg_id);
    END IF;
    PERFORM pg_notify('memoire_partagee', json_build_object('run', NEW.run_id, 'msg', NEW.msg_id, 'de', coalesce(NEW.auteur, NEW.origine),
                                                            'texte', NEW.texte, 'claims', NEW.claims)::text);
    RETURN NEW;
END $$;
CREATE TRIGGER message_publie AFTER INSERT ON message FOR EACH ROW EXECUTE FUNCTION message_publie();

-- Un message reçu devient un souvenir du destinataire (la mémoire partagée nourrit la mémoire individuelle).
CREATE FUNCTION message_recu() RETURNS trigger LANGUAGE plpgsql SET search_path = memoire AS $$
DECLARE m message;
BEGIN
    IF NEW.perdu OR current_setting('memoire.direct', true) = 'on' THEN RETURN NEW; END IF;
    SELECT * INTO m FROM message WHERE run_id = NEW.run_id AND msg_id = NEW.msg_id;
    INSERT INTO souvenir (run_id, agent_id, t_sim, texte, tags, msg_id)
    VALUES (NEW.run_id, NEW.destinataire, coalesce(NEW.t_recu, m.t_sim),
            hm(coalesce(NEW.t_recu, m.t_sim)) || ' — ' || coalesce(m.auteur, m.origine, 'quelqu''un') || ' me dit (' || coalesce(NEW.canal, m.genre) || ') : « ' || coalesce(m.texte, '') || ' »',
            ARRAY['message', m.genre] || CASE WHEN m.claims ? 'bridge' THEN ARRAY['pont'] ELSE '{}' END
                                    || CASE WHEN m.auteur IS NOT NULL THEN ARRAY[m.auteur] ELSE '{}' END,
            m.msg_id);
    RETURN NEW;
END $$;
CREATE TRIGGER message_recu AFTER INSERT ON reception FOR EACH ROW EXECUTE FUNCTION message_recu();

-- API de la mémoire -----------------------------------------------------------------------------------------------

CREATE SEQUENCE message_x;      -- numéros des messages écrits en direct (x1, x2...), distincts des m1, m2... de la simulation

-- Partager : un habitant envoie (ou relaie) un message à ses contacts. Tout ou rien, dans une seule transaction.
CREATE FUNCTION partager(p_run text, p_auteur text, p_texte text, p_claims jsonb, p_dest text[],
                         p_t numeric DEFAULT NULL, p_parent text DEFAULT NULL, p_genre text DEFAULT NULL)
RETURNS text LANGUAGE plpgsql SET search_path = memoire AS $$
DECLARE par message; id text; t numeric;
BEGIN
    SELECT * INTO par FROM message WHERE run_id = p_run AND msg_id = p_parent;
    t  := coalesce(p_t, (SELECT max(t_sim) FROM message WHERE run_id = p_run), 540);
    id := 'x' || nextval('message_x');        -- une séquence : deux sessions simultanées n'obtiennent jamais le même numéro
    INSERT INTO message (run_id, msg_id, auteur, origine, parent, sauts, genre, texte, claims, t_sim)
    VALUES (p_run, id, p_auteur, coalesce(par.origine, p_auteur), p_parent, coalesce(par.sauts + 1, 0),
            coalesce(p_genre, CASE WHEN p_parent IS NULL THEN 'observation' ELSE 'relais' END), p_texte, p_claims, t);
    INSERT INTO reception (run_id, msg_id, destinataire, canal) SELECT p_run, id, d, 'telephone' FROM unnest(p_dest) d;
    RETURN id;
END $$;

-- Se souvenir : les souvenirs d'un habitant les plus utiles pour une question (équivalent SQL de RAG.recall dans sim/rag.py) :
-- pertinence du texte (recherche plein texte en français) + sujets/personnes en commun + un peu de fraîcheur.
CREATE FUNCTION rappel(p_run text, p_agent text, p_question text, p_tags text[] DEFAULT '{}', k int DEFAULT 3, p_avant numeric DEFAULT NULL)
RETURNS TABLE (heure text, souvenir text, score numeric) LANGUAGE sql STABLE SET search_path = memoire AS $$
    WITH q AS (SELECT to_tsquery('french', nullif(replace(plainto_tsquery('french', p_question)::text, '&', '|'), '')) AS q)
    SELECT hm(s.t_sim), s.texte,
           round((coalesce(ts_rank(s.tsv, q.q), 0) + cardinality(ARRAY(SELECT unnest(s.tags) INTERSECT SELECT unnest(p_tags))) + s.t_sim / 1e6)::numeric, 3)
    FROM souvenir s, q
    WHERE s.run_id = p_run AND s.agent_id = p_agent AND (p_avant IS NULL OR s.t_sim <= p_avant)
      AND (s.tsv @@ q.q OR s.tags && p_tags)
    ORDER BY 3 DESC LIMIT k
$$;

-- État de chaque habitant à l'instant t : sa dernière croyance, et si elle est juste sur les trois faits (indicateur B1).
CREATE FUNCTION etat_a(p_run text, t numeric)
RETURNS TABLE (agent_id text, nom text, pont text, refuge text, danger boolean, certitude numeric, arrive boolean,
               pont_ok boolean, refuge_ok boolean, danger_ok boolean, contexte_ok boolean) LANGUAGE sql STABLE SET search_path = memoire AS $$
    SELECT a.agent_id, a.nom, coalesce(c.pont, 'inconnu'), coalesce(c.refuge, 'aucun'), coalesce(c.danger, false), c.certitude, arr.ok,
           coalesce(c.pont, 'inconnu') = v.pont,
           arr.ok OR coalesce(c.refuge, 'aucun') = ANY (v.refuges),
           coalesce(c.danger, false) = v.danger,
           coalesce(c.pont, 'inconnu') = v.pont AND (arr.ok OR coalesce(c.refuge, 'aucun') = ANY (v.refuges)) AND coalesce(c.danger, false) = v.danger
    FROM agent a
    JOIN LATERAL (SELECT * FROM verite WHERE run_id = p_run AND t_sim <= t ORDER BY seq DESC LIMIT 1) v ON true
    LEFT JOIN LATERAL (SELECT * FROM croyance c WHERE c.run_id = p_run AND c.agent_id = a.agent_id AND c.t_sim <= t ORDER BY c.seq DESC LIMIT 1) c ON true
    CROSS JOIN LATERAL (SELECT EXISTS (SELECT 1 FROM evenement e WHERE e.run_id = p_run AND e.agent_id = a.agent_id
                                        AND e.type = 'agent.arrive' AND e.t_sim <= t) AS ok) arr
    WHERE a.run_id = p_run
$$;

-- Vues de présentation ----------------------------------------------------------------------------------------------

-- Le tableau commun, lisible : pour chaque sujet, les versions en concurrence et leur poids.
CREATE VIEW tableau_commun AS
SELECT run_id, sujet, valeur, hm(premiere_fois) AS depuis, hm(derniere_fois) AS dernier_message, nb_messages,
       cardinality(sources) AS sources_independantes, officiel,
       round(100.0 * nb_messages / sum(nb_messages) OVER (PARTITION BY run_id, sujet), 1) AS part_des_messages_pct
FROM fait_collectif;

-- Qui a partagé quoi avec qui (une ligne par réception).
CREATE VIEW echanges AS
SELECT m.run_id, m.msg_id, hm(m.t_sim) AS heure, coalesce(m.auteur, m.origine) AS de, r.destinataire AS a, r.canal, r.perdu,
       m.genre, m.origine, m.sauts, m.texte
FROM message m JOIN reception r USING (run_id, msg_id);

-- Optimisation de la mémoire : moins stocker (fusion), mieux oublier (importance), résumer (consolidation) ---------

-- Importance d'un souvenir, de 1 (anecdotique) à 5 (essentiel) : ce qu'on a vu soi-même ou vérifié compte plus
-- qu'une répétition (« je suis bloqué » dix fois de suite).
CREATE FUNCTION importance(tags text[], texte text) RETURNS int LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE
        WHEN tags && ARRAY['souvenir', 'verification', 'resume'] OR texte LIKE '%vu moi-même%' THEN 5
        WHEN tags && ARRAY['officiel'] OR (tags && ARRAY['pont'] AND tags && ARRAY['message']) THEN 4
        WHEN tags && ARRAY['depart', 'refuge', 'prepare', 'observation'] THEN 3
        WHEN tags && ARRAY['blocked', 'reroute', 'abri'] THEN 1
        ELSE 2 END
$$;

-- Fusion : un souvenir répété n'est gardé qu'une fois, avec sa période et son nombre de répétitions.
-- security_invoker : la vue respecte la politique ma_memoire (un habitant ne fusionne que ses propres souvenirs).
CREATE VIEW souvenir_compact WITH (security_invoker = true) AS
SELECT run_id, agent_id, min(t_sim) AS debut, max(t_sim) AS fin, count(*) AS fois, max(importance(tags, texte)) AS importance,
       CASE WHEN count(*) = 1 THEN min(texte)
            ELSE hm(min(t_sim)) || '–' || hm(max(t_sim)) || ' — ' || fait || ' (' || count(*) || ' fois)' END AS texte
FROM (SELECT *, regexp_replace(texte, '^[0-9]{2}:[0-9]{2} — ', '') AS fait FROM souvenir) s
GROUP BY run_id, agent_id, fait;

-- Mémoire de travail : les k souvenirs à garder quand la place manque. On garde les plus IMPORTANTS (puis les plus
-- récents), au lieu des k plus récents comme le fait la simulation (premier entré, premier sorti).
CREATE FUNCTION memoire_de_travail(p_run text, p_agent text, k int DEFAULT 12)
RETURNS TABLE (heure text, souvenir text, importance int, fois bigint) LANGUAGE sql STABLE SET search_path = memoire AS $$
    SELECT hm(g.debut), g.texte, g.importance, g.fois FROM (
        SELECT * FROM souvenir_compact c WHERE c.run_id = p_run AND c.agent_id = p_agent
        ORDER BY c.importance DESC, c.fin DESC LIMIT k) g
    ORDER BY g.debut
$$;

-- Consolidation : tous les souvenirs jusqu'à une heure donnée sont remplacés par UN résumé de ce qui comptait
-- (importance 3 et plus). Comme un humain qui retient le résumé de sa matinée, pas chaque minute.
CREATE FUNCTION consolider(p_run text, p_agent text, p_jusqu_a numeric)
RETURNS TABLE (souvenirs_avant bigint, souvenirs_apres bigint, resume text) LANGUAGE plpgsql SET search_path = memoire AS $$
DECLARE n0 bigint; r text; t0 numeric; tg text[];
BEGIN
    SELECT count(*) INTO n0 FROM souvenir s WHERE s.run_id = p_run AND s.agent_id = p_agent;
    SELECT min(c.debut), string_agg(regexp_replace(c.texte, '^[0-9:–]+ — ', ''), ' ; ' ORDER BY c.debut)
      INTO t0, r FROM souvenir_compact c
     WHERE c.run_id = p_run AND c.agent_id = p_agent AND c.fin <= p_jusqu_a AND c.importance >= 3;
    SELECT array_agg(DISTINCT x) INTO tg FROM souvenir s, unnest(s.tags) x
     WHERE s.run_id = p_run AND s.agent_id = p_agent AND s.t_sim <= p_jusqu_a AND importance(s.tags, s.texte) >= 3;
    DELETE FROM souvenir s WHERE s.run_id = p_run AND s.agent_id = p_agent AND s.t_sim <= p_jusqu_a;
    IF r IS NOT NULL THEN
        INSERT INTO souvenir (run_id, agent_id, t_sim, texte, tags)
        VALUES (p_run, p_agent, p_jusqu_a, hm(t0) || '–' || hm(p_jusqu_a) || ' — résumé : ' || r, coalesce(tg, '{}') || ARRAY['resume']);
    END IF;
    RETURN QUERY SELECT n0, (SELECT count(*) FROM souvenir s WHERE s.run_id = p_run AND s.agent_id = p_agent),
                        hm(t0) || '–' || hm(p_jusqu_a) || ' — résumé : ' || r;
END $$;

-- RAG : ce que l'habitant retrouve avant de décider (appelé par la simulation, sim/memoire_pg.py) ---------------------

-- Base de connaissances du quartier (sim/corpus.py), copiée ici par la simulation au démarrage.
CREATE TABLE document (
    doc_id  text PRIMARY KEY,
    site    text,
    topic   text,                 -- sert à évaluer la recherche, jamais à filtrer
    annee   int,
    source  text,
    fiable  boolean,
    texte   text NOT NULL,
    tsv     tsvector GENERATED ALWAYS AS (to_tsvector('french', texte)) STORED
);
CREATE INDEX document_tsv ON document USING gin (tsv);

-- Requête « en OU » : un document ou un souvenir qui partage quelques mots avec la question compte déjà.
CREATE FUNCTION requete_ou(p_question text) RETURNS tsquery LANGUAGE sql IMMUTABLE AS $$
    SELECT to_tsquery('french', nullif(replace(plainto_tsquery('french', p_question)::text, '&', '|'), ''))
$$;

-- 1. Les k documents du quartier les plus pertinents (recherche plein texte en français).
CREATE FUNCTION rag_documents(p_question text, k int DEFAULT 2)
RETURNS TABLE (doc_id text, score numeric) LANGUAGE sql STABLE SET search_path = memoire AS $$
    SELECT d.doc_id, round(ts_rank(d.tsv, q.q)::numeric, 4)
    FROM document d, (SELECT requete_ou(p_question) AS q) q
    WHERE d.tsv @@ q.q
    ORDER BY ts_rank(d.tsv, q.q) DESC, d.doc_id LIMIT k
$$;

-- 2. Les k souvenirs les plus utiles d'un habitant à l'instant t (même logique que RAG.recall dans sim/rag.py) :
-- texte proche de la question + sujets et personnes en commun + fraîcheur, et l'importance pour départager.
-- Seuls les souvenirs déjà vécus (t_sim <= t) comptent : jamais un message avant de l'avoir reçu.
CREATE FUNCTION rag_souvenirs(p_run text, p_agent text, p_question text, p_sujets text[], p_personnes text[], t numeric, k int DEFAULT 3)
RETURNS TABLE (texte text, score numeric) LANGUAGE sql STABLE SET search_path = memoire AS $$
    SELECT x.texte, round(x.sc::numeric, 4) FROM (
        SELECT s.texte, s.t_sim,
               coalesce(ts_rank(s.tsv, q.q, 32), 0)
               + cardinality(ARRAY(SELECT unnest(s.tags) INTERSECT SELECT unnest(p_sujets)))
               + cardinality(ARRAY(SELECT unnest(s.tags) INTERSECT SELECT unnest(p_personnes)))
               + s.t_sim / 1e6 + importance(s.tags, s.texte) / 1e3 AS sc
        FROM souvenir s, (SELECT requete_ou(p_question) AS q) q
        WHERE s.run_id = p_run AND s.agent_id = p_agent AND s.t_sim <= t) x
    WHERE x.sc > 0.05
    ORDER BY x.sc DESC, x.t_sim DESC LIMIT k
$$;

-- 3. Ce que dit le groupe du quartier (mémoire collective) : pour le pont et le danger, chaque version en circulation,
-- avec son nombre de messages et de sources indépendantes. Lu seulement par les habitants qui ont le canal « groupe ».
CREATE FUNCTION rag_collectif(p_run text)
RETURNS TABLE (sujet text, valeur text, nb_messages int, sources int, officiel boolean) LANGUAGE sql STABLE SET search_path = memoire AS $$
    SELECT f.sujet, f.valeur, f.nb_messages, cardinality(f.sources), f.officiel
    FROM fait_collectif f
    WHERE f.run_id = p_run AND f.sujet IN ('pont', 'danger')
    ORDER BY f.sujet, f.nb_messages DESC
$$;

-- Recherche par le sens (pgvector), si l'extension est disponible : embeddings des documents (calculés par Ollama)
-- et fusion des deux classements (plein texte + sens) par rangs réciproques, comme le RAG hybride de sim/rag.py.
DO $$ BEGIN
    BEGIN CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;
    EXCEPTION WHEN OTHERS THEN RAISE NOTICE 'pgvector absent : recherche plein texte seulement'; END;
    IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector') THEN
        EXECUTE 'ALTER TABLE memoire.document ADD COLUMN embedding public.vector';
        EXECUTE $f$
            CREATE FUNCTION memoire.rag_documents_hybride(p_question text, p_vecteur public.vector, k int DEFAULT 2)
            RETURNS TABLE (doc_id text, score numeric) LANGUAGE sql STABLE SET search_path = memoire, public AS $q$
                WITH q AS (SELECT requete_ou(p_question) AS q),
                lex AS (SELECT d.doc_id, row_number() OVER (ORDER BY ts_rank(d.tsv, q.q) DESC, d.doc_id) AS r
                        FROM document d, q WHERE d.tsv @@ q.q),
                sens AS (SELECT d.doc_id, row_number() OVER (ORDER BY d.embedding <=> p_vecteur, d.doc_id) AS r
                         FROM document d WHERE d.embedding IS NOT NULL AND vector_dims(d.embedding) = vector_dims(p_vecteur))
                SELECT d.doc_id, round((coalesce(1.0 / (60 + lex.r), 0) + coalesce(1.0 / (60 + sens.r), 0))::numeric, 5)
                FROM document d LEFT JOIN lex USING (doc_id) LEFT JOIN sens USING (doc_id)
                WHERE lex.r IS NOT NULL OR sens.r IS NOT NULL
                ORDER BY 2 DESC, d.doc_id LIMIT k
            $q$ $f$;

        -- Embeddings des souvenirs. Un message relayé se retrouve à l'identique dans des dizaines de mémoires : le vecteur
        -- est calculé UNE fois par contenu (cle) et rangé ici. La simulation remplit cette table en arrière-plan
        -- (sim/memoire_pg.py) ; un souvenir pas encore vectorisé reste trouvable par ses mots.
        EXECUTE 'CREATE TABLE memoire.vecteur (cle text PRIMARY KEY, texte text NOT NULL, modele text NOT NULL, embedding public.vector NOT NULL)';
        -- Questions vectorisées à l'avance, pour les démos et le banc d'évaluation (python memoire_pg.py vecteurs).
        EXECUTE 'CREATE TABLE memoire.question (question text PRIMARY KEY, modele text NOT NULL, embedding public.vector NOT NULL)';

        -- Souvenirs : le score de rag_souvenirs (mots, sujets, personnes, fraîcheur) + poids × proximité de sens (1 - distance cosinus).
        -- Poids 0,5 : le meilleur réglage sur le banc de questions reformulées (python eval_rag.py souvenirs).
        -- « le pont est coupé » retrouve alors « le Pont Central est FERMÉ », sans un seul mot en commun.
        EXECUTE $f$
            CREATE FUNCTION memoire.rag_souvenirs_hybride(p_run text, p_agent text, p_question text, p_sujets text[], p_personnes text[],
                                                          t numeric, p_vecteur public.vector, k int DEFAULT 3, poids numeric DEFAULT 0.5)
            RETURNS TABLE (texte text, score numeric, sens numeric) LANGUAGE sql STABLE SET search_path = memoire, public AS $q$
                SELECT x.texte, round(x.sc::numeric, 4), round(x.sim::numeric, 4) FROM (
                    SELECT s.texte, s.t_sim, coalesce(1 - (v.embedding <=> p_vecteur), 0) AS sim,
                           coalesce(ts_rank(s.tsv, q.q, 32), 0)
                           + cardinality(ARRAY(SELECT unnest(s.tags) INTERSECT SELECT unnest(p_sujets)))
                           + cardinality(ARRAY(SELECT unnest(s.tags) INTERSECT SELECT unnest(p_personnes)))
                           + s.t_sim / 1e6 + importance(s.tags, s.texte) / 1e3
                           + poids * coalesce(1 - (v.embedding <=> p_vecteur), 0) AS sc
                    FROM souvenir s
                    CROSS JOIN (SELECT requete_ou(p_question) AS q) q
                    LEFT JOIN vecteur v ON v.cle = s.cle AND vector_dims(v.embedding) = vector_dims(p_vecteur)
                    WHERE s.run_id = p_run AND s.agent_id = p_agent AND s.t_sim <= t) x
                WHERE x.sc > 0.05
                ORDER BY x.sc DESC, x.t_sim DESC LIMIT k
            $q$ $f$;
    END IF;
END $$;
CREATE INDEX souvenir_cle ON souvenir (cle);

-- Contrôle d'accès : un habitant (rôle agent_sim) ne lit QUE sa mémoire privée, les messages qu'il a écrits ou reçus,
-- et tout le tableau commun. Le simulateur choisit l'habitant avec SET memoire.agent = 'A05'.
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'agent_sim') THEN CREATE ROLE agent_sim NOLOGIN; END IF;
END $$;
GRANT USAGE ON SCHEMA memoire TO agent_sim;
GRANT SELECT ON souvenir, souvenir_compact, message, reception, fait_collectif, tableau_commun, agent, run, document TO agent_sim;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA memoire TO agent_sim;

ALTER TABLE souvenir  ENABLE ROW LEVEL SECURITY;
ALTER TABLE reception ENABLE ROW LEVEL SECURITY;
ALTER TABLE message   ENABLE ROW LEVEL SECURITY;
CREATE POLICY ma_memoire   ON souvenir  TO agent_sim USING (agent_id = current_setting('memoire.agent', true));
CREATE POLICY mes_receptions ON reception TO agent_sim USING (destinataire = current_setting('memoire.agent', true));
CREATE POLICY mes_messages ON message   TO agent_sim USING (
    auteur = current_setting('memoire.agent', true)
    OR EXISTS (SELECT 1 FROM reception r WHERE r.run_id = message.run_id AND r.msg_id = message.msg_id AND NOT r.perdu));
    -- la sous-requête sur reception passe elle-même par la politique mes_receptions : seules MES réceptions comptent

-- En dernier : la version. Si l'installation s'arrête avant, la simulation saura que le schéma est incomplet.
COMMENT ON SCHEMA memoire IS 'ALERTE memoire v3';

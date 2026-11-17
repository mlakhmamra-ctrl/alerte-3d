"""Mémoire des habitants dans PostgreSQL pendant la simulation (schéma : postgres/01_schema.sql).
Écriture : chaque souvenir du journal de l'agent (Agent.remember, à l'heure exacte), chaque message et sa réception (à l'heure
où il est lu), chaque événement, croyance et état réel du monde. Les écritures passent par une file d'attente et une seule
connexion, dans l'ordre : la simulation n'attend jamais la base.
Lecture (RAG) : avant de décider, un agent LLM retrouve ses souvenirs (rag_souvenirs), les documents du quartier
(rag_documents, ou rag_documents_hybride avec pgvector) et ce que dit le groupe du quartier (rag_collectif).
Embeddings : un vectoriseur en arrière-plan calcule (avec Ollama) le vecteur de chaque nouveau souvenir, une fois par contenu,
et les range dans memoire.vecteur ; le rappel des souvenirs ajoute alors la proximité de sens (rag_souvenirs_hybride).
Si la base ne répond pas, ok passe à False et la simulation revient à la mémoire en RAM (sim/rag.py)."""
import asyncio, os, time

VERSION = "ALERTE memoire v3"
SCHEMA_FILE = os.getenv("SCHEMA_FILE", os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema.sql"))
SAUTER = {"world.tick", "flood.update", "tick", "metrics"}          # gros et répétitifs : ils restent dans le fichier JSONL
LIB = {"pont": {"ferme": "le Pont Central est fermé", "ouvert": "le Pont Central est ouvert"},
       "danger": {"true": "le danger est imminent", "false": "pas de danger immédiat"}}

def vecteur(v): return "[" + ",".join(f"{x:.6f}" for x in v) + "]"

def quartier(rows):
    """Lignes du tableau commun -> phrases pour le prompt : chaque version en circulation et son poids."""
    out = []
    for sujet in ("pont", "danger"):
        vs = [r for r in rows if r[0] == sujet]
        if vs: out.append(" ; ".join(f"« {LIB[sujet].get(v, v)} » : {n} message{'s' if n > 1 else ''}, {src} source{'s' if src > 1 else ''}"
                                     + (" dont la mairie" if off else "") for _, v, n, src, off in vs))
    return out

class MemoirePG:
    def __init__(s, dsn):
        s.dsn = dsn; s.ok = False; s.vector = False; s.warn = ""
        s.q = asyncio.Queue(); s.wconn = None; s.writer = None; s.pool = None
        s.n_req = 0; s.ms = 0.0; s.cache = {}; s.Jsonb = None
        s.kb = None; s.emb_ok = False; s.qvec = {}; s.vectoriseur = None; s.n_vec = 0
        s.poids = float(os.getenv("SOUVENIR_SENS", "0.5"))         # poids de la proximité de sens dans le rappel des souvenirs

    async def connect(s, docs, kb):
        """Crée le schéma s'il manque (ou s'il n'est pas à la bonne version), copie les documents du quartier et leurs
        embeddings, puis ouvre la connexion d'écriture et les connexions de lecture. Renvoie True si tout est prêt."""
        try:
            import psycopg, psycopg_pool
            from psycopg.types.json import Jsonb
            s.Jsonb = Jsonb
            async with await psycopg.AsyncConnection.connect(s.dsn, autocommit=True, connect_timeout=5) as c:
                v = await (await c.execute("SELECT obj_description(oid, 'pg_namespace') FROM pg_namespace WHERE nspname = 'memoire'")).fetchone()
                if not v or v[0] != VERSION:
                    with open(SCHEMA_FILE, encoding="utf-8") as f:     # les commandes psql (\set...) ne sont pas du SQL : on les retire
                        sql = "\n".join(l for l in f.read().splitlines() if not l.lstrip().startswith("\\"))
                    async with c.transaction(): await c.execute(sql)   # tout ou rien : jamais un schéma à moitié installé
                    print("PostgreSQL : schéma « memoire » créé", flush=True)
                s.vector = (await (await c.execute("SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'memoire' "
                                                   "AND table_name = 'document' AND column_name = 'embedding')")).fetchone())[0]
                async with c.transaction():
                    for i, d in enumerate(docs):
                        emb = vecteur(kb.emb[i]) if s.vector and kb.emb is not None else None
                        await c.execute("INSERT INTO memoire.document (doc_id, site, topic, annee, source, fiable, texte) VALUES (%s, %s, %s, %s, %s, %s, %s) "
                                        "ON CONFLICT (doc_id) DO UPDATE SET site = EXCLUDED.site, topic = EXCLUDED.topic, annee = EXCLUDED.annee, "
                                        "source = EXCLUDED.source, fiable = EXCLUDED.fiable, texte = EXCLUDED.texte",
                                        (d["id"], d["site"], d["topic"], d["year"], d["source"], d["reliable"], d["text"]))
                        if s.vector: await c.execute("UPDATE memoire.document SET embedding = %s::public.vector WHERE doc_id = %s", (emb, d["id"]))
            s.wconn = await psycopg.AsyncConnection.connect(s.dsn, connect_timeout=5)
            await s.wconn.execute("SET memoire.direct = 'on'"); await s.wconn.commit()   # la simulation écrit elle-même les souvenirs
            s.pool = psycopg_pool.AsyncConnectionPool(s.dsn, min_size=1, max_size=4, open=False, kwargs={"autocommit": True})
            await s.pool.open(wait=True, timeout=10)
            s.writer = asyncio.create_task(s._ecrire()); s.ok = True
            s.kb = kb; s.emb_ok = s.vector and kb.emb is not None
            if s.emb_ok: s.vectoriseur = asyncio.create_task(s._vectoriser())
        except Exception as ex:
            s.warn = f"PostgreSQL indisponible ({ex!r})"[:200]; s.ok = False
        return s.ok

    def panne(s, ex):
        if s.ok: s.ok = False; s.warn = f"PostgreSQL en panne ({ex!r})"[:200]; print(s.warn, "-> mémoire en RAM", flush=True)

    # ---------- écriture (file d'attente, dans l'ordre) ----------
    def _push(s, sql, args):
        if s.ok: s.q.put_nowait((sql, args))

    async def _ecrire(s):
        while True:
            lot = [await s.q.get()]
            while not s.q.empty() and len(lot) < 500: lot.append(s.q.get_nowait())
            try:
                if s.ok:
                    async with s.wconn.pipeline():
                        for sql, args in lot: await s.wconn.execute(sql, args)
                    await s.wconn.commit()
            except Exception as ex:
                try: await s.wconn.rollback()
                except Exception: pass
                s.panne(ex)
            finally:
                for _ in lot: s.q.task_done()

    async def flush(s, delai=15):
        """Attend que tout ce qui a été écrit soit dans la base (avant une lecture, ou à la fin du run)."""
        if not s.ok: return
        try: await asyncio.wait_for(s.q.join(), delai)
        except asyncio.TimeoutError as ex: s.panne(ex)

    def start_run(s, run, scenario, seed, t0, t1, agents):
        s._push("INSERT INTO memoire.run (run_id, scenario, graine, n_agents, t_debut, t_fin) VALUES (%s, %s, %s, %s, %s, %s)",
                (run, scenario, seed, len(agents), t0, t1))
        for aid, nom in agents: s._push("INSERT INTO memoire.agent (run_id, agent_id, nom) VALUES (%s, %s, %s)", (run, aid, nom))

    def souvenir(s, run, agent, t, texte, tags, msg=None):
        s._push("INSERT INTO memoire.souvenir (run_id, agent_id, t_sim, texte, tags, msg_id) VALUES (%s, %s, %s, %s, %s, %s)",
                (run, agent, t, texte, sorted(str(x) for x in tags if x), msg))

    def message(s, run, mid, auteur, origine, parent, sauts, genre, texte, claims, t, receptions):
        """receptions : [(destinataire, canal, heure de lecture, perdu)]. Le déclencheur met à jour le tableau commun."""
        s._push("INSERT INTO memoire.message (run_id, msg_id, auteur, origine, parent, sauts, genre, texte, claims, t_sim) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (run, mid, auteur, "rumeur" if origine == "anonyme" else origine, parent, sauts, genre, texte, s.Jsonb(claims or {}), t))
        for dest, canal, t_recu, perdu in receptions:
            s._push("INSERT INTO memoire.reception (run_id, msg_id, destinataire, canal, perdu, t_recu) VALUES (%s, %s, %s, %s, %s, %s) "
                    "ON CONFLICT DO NOTHING", (run, mid, dest, canal, perdu, t_recu))

    def evenement(s, ev):
        if not s.ok or ev["type"] in SAUTER or ev["run_id"] == "control": return
        d = ev["data"]
        s._push("INSERT INTO memoire.evenement (run_id, seq, type, t_sim, agent_id, data) VALUES (%s, %s, %s, %s, %s, %s)",
                (ev["run_id"], ev["seq"], ev["type"], ev["t_sim"], ev["agent"], s.Jsonb(d)))
        if ev["type"] == "agent.belief":
            s._push("INSERT INTO memoire.croyance VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (ev["run_id"], ev["agent"], ev["t_sim"], ev["seq"], d["bridge"], d["refuge"], d["danger"], d["risk"], d["certainty"]))
        elif ev["type"] == "world.truth":
            s._push("INSERT INTO memoire.verite VALUES (%s, %s, %s, %s, %s, %s)",
                    (ev["run_id"], ev["t_sim"], ev["seq"], d["bridge"], d["danger"], list(d["refuges"])))

    # ---------- lecture (RAG) ----------
    async def _lire(s, sql, args, compter=True):
        t = time.monotonic()
        async with s.pool.connection() as c: rows = await (await c.execute(sql, args)).fetchall()
        if compter: s.n_req += 1; s.ms += (time.monotonic() - t) * 1000      # seules les recherches du RAG sont comptées
        return rows

    async def vecteur_question(s, question):
        """Vecteur d'une question (calculé une fois) ; None si les embeddings ne sont pas disponibles."""
        if not s.emb_ok: return None
        if question not in s.qvec:
            try: s.qvec[question] = vecteur((await s.kb._embed(["search_query: " + question]))[0])
            except Exception: return None
        return s.qvec[question]

    async def documents(s, question, kb, k=2):
        """[(doc_id, score)] : plein texte, plus le sens (pgvector) si les embeddings sont là. Le corpus est fixe :
        même question, même réponse (cache, comme sim/rag.py)."""
        key = (question, k)
        if key in s.cache: return s.cache[key]
        v = await s.vecteur_question(question)                           # None : embeddings indisponibles, plein texte seul
        if v is not None: rows = await s._lire("SELECT doc_id, score FROM memoire.rag_documents_hybride(%s, %s::public.vector, %s::int)", (question, v, k))
        else: rows = await s._lire("SELECT doc_id, score FROM memoire.rag_documents(%s, %s::int)", (question, k))
        s.cache[key] = [(r[0], float(r[1])) for r in rows]
        return s.cache[key]

    async def rappel(s, run, agent, question, sujets, personnes, t, k=3):
        v = await s.vecteur_question(question)
        if v is not None:
            rows = await s._lire("SELECT texte FROM memoire.rag_souvenirs_hybride(%s, %s, %s, %s::text[], %s::text[], %s::numeric, %s::public.vector, %s::int, %s::numeric)",
                                 (run, agent, question, sorted(sujets), sorted(personnes), t, v, k, s.poids))
            return [r[0] for r in rows]
        rows = await s._lire("SELECT texte FROM memoire.rag_souvenirs(%s, %s, %s, %s::text[], %s::text[], %s::numeric, %s::int)",
                             (run, agent, question, sorted(sujets), sorted(personnes), t, k))
        return [r[0] for r in rows]

    async def collectif(s, run):
        return quartier(await s._lire("SELECT * FROM memoire.rag_collectif(%s)", (run,)))

    # ---------- embeddings des souvenirs (en arrière-plan) ----------
    async def vectoriser(s, lot=64):
        """Calcule les vecteurs des souvenirs qui n'en ont pas encore, un par contenu. Renvoie le nombre de vecteurs calculés."""
        rows = await s._lire("SELECT DISTINCT ON (s.cle) s.cle, regexp_replace(s.texte, '^[0-9]{2}:[0-9]{2} — ', '') FROM memoire.souvenir s "
                             "LEFT JOIN memoire.vecteur v ON v.cle = s.cle AND v.modele = %s WHERE v.cle IS NULL LIMIT %s::int", (s.kb.model, lot), compter=False)
        if not rows: return 0
        embs = await s.kb._embed(["search_document: " + t for _, t in rows])
        async with s.pool.connection() as c:
            async with c.transaction():
                for (cle, t), e in zip(rows, embs):
                    await c.execute("INSERT INTO memoire.vecteur (cle, texte, modele, embedding) VALUES (%s, %s, %s, %s::public.vector) "
                                    "ON CONFLICT (cle) DO UPDATE SET texte = EXCLUDED.texte, modele = EXCLUDED.modele, embedding = EXCLUDED.embedding",
                                    (cle, t, s.kb.model, vecteur(e)))
        s.n_vec += len(rows); return len(rows)

    async def _vectoriser(s):
        while True:
            try: n = await s.vectoriser() if s.ok else 0
            except Exception: n = 0; await asyncio.sleep(10)              # Ollama occupé ou absent : on réessaie plus tard
            await asyncio.sleep(.5 if n else 2)

    def snap(s):
        return {"memoire": "postgres" if s.ok else "ram", "pg_requetes": s.n_req, "pg_ms": round(s.ms / max(1, s.n_req), 1),
                "pg_vecteurs": s.vector, "pg_souvenirs_vectorises": s.n_vec, "pg_file": s.q.qsize()}

    async def close(s):
        await s.flush()
        for x in (s.writer, s.vectoriseur):
            if x: x.cancel()
        for c in (s.wconn,):
            try: await c.close()
            except Exception: pass
        if s.pool: await s.pool.close()

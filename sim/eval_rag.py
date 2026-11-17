"""Évaluation du RAG (exigence F2).
  python eval_rag.py             -> banc de requêtes étiquetées (recall@3, MRR), lexical puis hybride si Ollama répond
  python eval_rag.py /data/runs  -> ce que les agents LLM ont réellement retrouvé dans les runs enregistrés (pertinence, documents peu fiables)
  python eval_rag.py souvenirs   -> embeddings des souvenirs dans PostgreSQL : vectorise ce qui manque, range les questions de démo,
                                    et compare le rappel par les mots seuls au rappel hybride (mots + sens) sur des questions reformulées"""
import asyncio, glob, json, os, sys
from corpus import DOCS
from rag import RAG
OLLAMA = os.getenv("OLLAMA_URL", "http://ollama:11434"); EMBED = os.getenv("EMBED_MODEL", "nomic-embed-text")
BANC = [("le pont central est-il fermé ? qui le confirme ?", {"pont-01", "pont-02", "pont-03"}),
        ("un message anonyme dit que le pont est fermé, faut-il le croire ?", {"pont-02", "pont-04", "alerte-03", "voisins-01"}),
        ("combien de places au refuge de l'école ?", {"refuge-01"}), ("le gymnase accueille-t-il les habitants ?", {"refuge-02", "refuge-04"}),
        ("chemin sans escalier pour une personne qui marche lentement", {"plan-02", "plan-03", "soins-01"}),
        ("quelles rues sont inondées en premier ?", {"plan-04", "refuge-03", "pont-03", "crue-01"}),
        ("comment vérifier une alerte avant de la relayer ?", {"alerte-02", "alerte-03", "voisins-01"}),
        ("que veut dire le niveau évacuation de la mairie ?", {"alerte-01"}), ("à quelle heure le pic de crue est-il prévu ?", {"meteo-02"}),
        ("peut-on marcher dans 40 cm d'eau ?", {"crue-03", "crue-01"}), ("souvenir de la crue de 2019", {"crue-01", "crue-02", "pont-03"}),
        ("détour par les hauteurs et le passage", {"voisins-02", "plan-03"}), ("le café est-il dans une zone basse ?", {"cafe-01"})]

async def bench(mode):
    http = None
    if mode != "lexical":
        import httpx; http = httpx.AsyncClient()
    kb = RAG(DOCS, mode=mode, ollama=OLLAMA, model=EMBED, http=http); await kb.build()
    if mode != "lexical" and kb.emb is None: return None, kb.warn
    rec = hit = rr = 0.0; rows = []
    for q, rel in BANC:
        ids = [d["doc"]["id"] for d in await kb.search(q, 3)]; ok = set(ids) & rel
        rec += len(ok) / len(rel); hit += bool(ok); rr += next((1 / (i + 1) for i, x in enumerate(ids) if x in rel), 0.0); rows.append((q, ids, bool(ok)))
    n = len(BANC); return {"recall@3": rec / n, "hit@3": hit / n, "MRR": rr / n, "rows": rows}, ""

def runs(path):
    n = unrel = nd = 0; samples = []
    for f in sorted(glob.glob(os.path.join(path, "*.jsonl")) if os.path.isdir(path) else [path]):
        for l in open(f, encoding="utf-8"):
            e = json.loads(l)
            if e["type"] != "agent.think" or e["data"].get("decider") == "rule" or not e["data"].get("retrieved_meta"): continue
            meta = e["data"]["retrieved_meta"]; n += 1; nd += len(meta); u = [m["id"] for m in meta if not m["reliable"]]; unrel += len(u)
            if u and len(samples) < 6: samples.append((e["run_id"], e["agent"], e["data"]["action"], e["data"]["why"], u))
    return n, nd, unrel, samples

# Questions REFORMULÉES : presque aucun mot en commun avec les souvenirs recherchés (motifs ILIKE = souvenirs pertinents).
# Les deux dernières gardent les mots exacts : la recherche par le sens ne doit pas faire moins bien que les mots.
BANC_SOUVENIRS = [
    ("on ne peut plus traverser la rivière par le centre", ["%pont central est ferm%"]),
    ("les autorités ont-elles confirmé l'information ?", ["%poste municipal%"]),
    ("l'inondation a déjà commencé devant les maisons", ["%cm d'eau%", "%l'eau monte%"]),
    ("je n'arrive plus à avancer, la route est impraticable", ["%bloqué%", "%trop d'eau%"]),
    ("ordre officiel de quitter le secteur immédiatement", ["%évacuation%"]),
    ("un inconnu raconte qu'il ne faut plus emprunter l'ouvrage", ["%il ne faut plus passer par là%"]),
    ("le pont central est fermé", ["%pont central est ferm%"]),
    ("l'eau monte près de chez moi", ["%cm d'eau%", "%l'eau monte%"]),
]
RUN_BANC = os.getenv("RUN_BANC", "7086a147")

async def souvenirs():
    """Banc des souvenirs (PostgreSQL + pgvector) : pour chaque question et chaque habitant qui a au moins un souvenir pertinent,
    le premier souvenir pertinent arrive-t-il dans les 3 premiers (hit@3), et à quel rang (MRR) ?"""
    import httpx
    from memoire_pg import MemoirePG
    kb = RAG(DOCS, mode="hybrid", ollama=OLLAMA, model=EMBED, http=httpx.AsyncClient()); await kb.build()
    if kb.emb is None: print("embeddings indisponibles :", kb.warn); return
    pg = MemoirePG(os.getenv("PG_DSN", "postgresql://postgres:alerte@postgres:5432/alerte"))
    if not await pg.connect(DOCS, kb): print(pg.warn); return
    if not await pg._lire("SELECT 1 FROM memoire.run WHERE run_id = %s", (RUN_BANC,), compter=False):
        print(f"run {RUN_BANC} absent : chargez-le d'abord (lancer_postgres.bat, ou postgres/02_charger.sql)"); await pg.close(); return
    total = 0
    while (n := await pg.vectoriser(128)): total += n                  # tous les souvenirs déjà en base (runs chargés depuis un fichier)
    for q, _ in BANC_SOUVENIRS:                                         # questions rangées pour la démo SQL (postgres/07_demo_embeddings.sql)
        v = await pg.vecteur_question(q)
        async with pg.pool.connection() as c:
            await c.execute("INSERT INTO memoire.question VALUES (%s, %s, %s::public.vector) ON CONFLICT (question) DO UPDATE SET "
                            "modele = EXCLUDED.modele, embedding = EXCLUDED.embedding", (q, EMBED, v))
    modes = [("mots seuls", None)] + [(f"mots + sens (poids {w:g})", w) for w in (0.25, 0.5, 0.75, 1, 2, 4)]
    res = {m: [0, 0.0, 0] for m, _ in modes}; detail = []
    for q, motifs in BANC_SOUVENIRS:
        pert = "(" + " OR ".join(["texte ILIKE %s"] * len(motifs)) + ")"
        agents = [r[0] for r in await pg._lire(f"SELECT DISTINCT agent_id FROM memoire.souvenir WHERE run_id = %s AND {pert} ORDER BY 1",
                                               (RUN_BANC, *motifs), compter=False)]
        v = await pg.vecteur_question(q)
        for m, w in modes:
            for a in agents:
                if w is None: rows = await pg._lire("SELECT texte FROM memoire.rag_souvenirs(%s, %s, %s, '{}', '{}', 600, 3)", (RUN_BANC, a, q), compter=False)
                else: rows = await pg._lire("SELECT texte FROM memoire.rag_souvenirs_hybride(%s, %s, %s, '{}', '{}', 600, %s::public.vector, 3, %s::numeric)",
                                            (RUN_BANC, a, q, v, w), compter=False)
                ok = [i for i, (t,) in enumerate(rows) if any(_ilike(t, mo) for mo in motifs)]
                res[m][0] += bool(ok); res[m][1] += 1 / (ok[0] + 1) if ok else 0; res[m][2] += 1
                if m == "mots seuls" or w == 1:
                    if a == agents[0]: detail.append((q, m, rows[0][0] if rows else "(rien)"))
    out = ["# Embeddings des souvenirs", f"Run {RUN_BANC} ; {total} vecteurs calculés maintenant ; {len(BANC_SOUVENIRS)} questions × habitants concernés.", "",
           "| rappel | hit@3 | MRR | cas |", "|---|---|---|---|"]
    out += [f"| {m} | {h / n:.2f} | {r / n:.2f} | {n} |" for m, (h, r, n) in res.items()]
    out += ["", "Premier souvenir retrouvé pour le premier habitant concerné :"] + [f"- « {q} » [{m}] → {t[:110]}" for q, m, t in detail]
    txt = "\n".join(out); print(txt)
    if os.path.isdir("/data"): open("/data/rag_eval_souvenirs.md", "w", encoding="utf-8").write(txt)
    await pg.close()

def _ilike(t, motif):
    import re
    return re.fullmatch(".*".join(re.escape(x) for x in motif.lower().split("%")), t.lower(), re.S) is not None

async def main():
    if sys.argv[1:] == ["souvenirs"]: return await souvenirs()
    out = ["# Évaluation du RAG", f"Corpus : {len(DOCS)} documents ; banc : {len(BANC)} requêtes étiquetées à la main.", "", "| mode | recall@3 | hit@3 | MRR |", "|---|---|---|---|"]
    detail = None
    for mode in ("lexical", "hybrid"):
        r, warn = await bench(mode)
        if r is None: out.append(f"| {mode} | indisponible : {warn} | | |"); continue
        out.append(f"| {mode} | {r['recall@3']:.2f} | {r['hit@3']:.2f} | {r['MRR']:.2f} |"); detail = detail or r
    out += ["", "Requêtes sans document pertinent dans le top 3 :"] + ([f"- « {q} » → {ids}" for q, ids, ok in detail["rows"] if not ok] or ["- aucune"])
    if len(sys.argv) > 1:
        n, nd, unrel, samples = runs(sys.argv[1])
        out += ["", "## Décisions LLM des runs enregistrés", f"- décisions avec récupération : {n}, documents retrouvés : {nd}, dont peu fiables ou périmés : {unrel}"]
        out += ["", "Décisions prises alors qu'un document peu fiable était retrouvé (question Q5 du jury) :"]
        out += [f"- run {r}, {a}, action {act} — « {why} » — documents peu fiables : {u}" for r, a, act, why, u in samples] or ["- aucune"]
    txt = "\n".join(out); print(txt)
    if os.path.isdir("/data"): open("/data/rag_eval.md", "w", encoding="utf-8").write(txt)

if __name__ == "__main__":
    asyncio.run(main())

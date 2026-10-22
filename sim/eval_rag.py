"""Évaluation du RAG (exigence F2).
  python eval_rag.py             -> banc de requêtes étiquetées (recall@3, MRR), lexical puis hybride si Ollama répond
  python eval_rag.py /data/runs  -> ce que les agents LLM ont réellement retrouvé dans les runs enregistrés (pertinence, documents peu fiables)"""
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

async def main():
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

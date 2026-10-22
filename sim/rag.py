"""RAG : récupération hybride (lexicale TF-IDF + embeddings Ollama), fusion par rangs (RRF).
Mode 'lexical' : 100 % local, sans appel réseau. Mode 'hybrid' : ajoute les embeddings, avec repli lexical automatique si indisponibles."""
import math, re, time, unicodedata
STOP = set("le la les un une des de du d l et ou a au aux en dans sur pour par avec sans est sont etre ete ce cet cette ces il elle ils elles qui que dont ne pas plus tres tout tous y se sa son ses leur leurs mais comme si on nous vous je tu pres meme aussi".split())
SUF = ("issements", "issement", "ements", "ement", "ations", "ation", "ites", "ite", "ees", "er", "ir", "es", "ee", "s", "e", "x")

def norm(t): return "".join(c for c in unicodedata.normalize("NFD", t.lower()) if unicodedata.category(c) != "Mn")
def stem(w):
    for suf in SUF:
        if w.endswith(suf) and len(w) - len(suf) >= 4: return w[:-len(suf)]
    return w
def toks(t): return [stem(w) for w in re.findall(r"[a-z0-9]+", norm(t)) if w not in STOP and len(w) > 1]
def unit(v):
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]

class RAG:
    def __init__(s, docs, mode="hybrid", ollama="", model="", http=None, rrf_k=60):
        s.docs, s.mode, s.ollama, s.model, s.http, s.rrf_k = docs, mode, ollama, model, http, rrf_k
        s.tok = [toks(d["text"]) for d in docs]                  # on n'indexe que le texte : aucune métadonnée n'aide la recherche
        df = {}
        for t in s.tok:
            for w in set(t): df[w] = df.get(w, 0) + 1
        n = len(docs); s.idf = {w: math.log((n + 1) / (c + 1)) + 1 for w, c in df.items()}; s.idf0 = math.log(n + 1) + 1
        s.vec = [s._vec(t) for t in s.tok]
        s.emb = None; s.cache = {}; s.hits = 0; s.n_search = s.n_embed = 0; s.embed_ms = 0.0; s.warn = ""
    def _vec(s, tk):
        tf = {}
        for w in tk: tf[w] = tf.get(w, 0) + 1
        v = {w: (1 + math.log(c)) * s.idf.get(w, s.idf0) for w, c in tf.items()}
        n = math.sqrt(sum(x * x for x in v.values())) or 1.0
        return {w: x / n for w, x in v.items()}
    def lex(s, query):
        q = s._vec(toks(query)); return [sum(x * d.get(w, 0.0) for w, x in q.items()) for d in s.vec]
    def lex_text(s, query, text):
        q, d = s._vec(toks(query)), s._vec(toks(text)); return sum(x * d.get(w, 0.0) for w, x in q.items())
    async def _embed(s, texts):
        # num_gpu 0 : le modèle d'embeddings tourne sur le processeur et laisse la mémoire vidéo aux LLM (textes courts : rapide quand même)
        t = time.time(); r = await s.http.post(f"{s.ollama}/api/embed", json={"model": s.model, "input": texts, "options": {"num_gpu": 0}}, timeout=120)
        if r.status_code >= 400: raise RuntimeError(f"http {r.status_code}")
        e = r.json()["embeddings"]; s.n_embed += 1; s.embed_ms += (time.time() - t) * 1000; return e
    async def build(s):
        if s.mode == "lexical" or s.http is None: return
        try: s.emb = [unit(v) for v in await s._embed(["search_document: " + d["text"] for d in s.docs])]
        except Exception as ex: s.emb = None; s.warn = f"embeddings indisponibles ({ex!r}) : repli lexical"
    async def search(s, query, k=3):
        s.n_search += 1; key = (query, k)
        if key in s.cache: s.hits += 1; return s.cache[key]            # le corpus est fixe : même requête, même résultat (économise les embeddings)
        n = len(s.docs); lex = s.lex(query); score = [0.0] * n; emb = None
        if s.emb is not None:
            try:
                q = unit((await s._embed(["search_query: " + query]))[0]); emb = [sum(a * b for a, b in zip(q, d)) for d in s.emb]
            except Exception as ex: s.emb = None; s.warn = f"embeddings indisponibles ({ex!r}) : repli lexical"
        for r, i in enumerate(sorted(range(n), key=lambda i: -lex[i])):
            if lex[i] > 0: score[i] += 1 / (s.rrf_k + r + 1)
        if emb is not None:
            for r, i in enumerate(sorted(range(n), key=lambda i: -emb[i])): score[i] += 1 / (s.rrf_k + r + 1)
        top = sorted(range(n), key=lambda i: -score[i])[:k]
        res = [{"doc": s.docs[i], "score": score[i], "lex": lex[i], "emb": None if emb is None else emb[i]} for i in top if score[i] > 0]
        s.cache[key] = res; return res
    def recall(s, query, mem, sites, k=2, people=frozenset()):  # souvenirs vécus : similarité + lieux et personnes concernés + fraîcheur
        sc = lambda m: s.lex_text(query, m[1]) + len(m[2] & sites) + (len(m[3] & people) if len(m) > 3 else 0) + m[0] / 1e6
        return [m[1] for m in sorted(mem, key=sc, reverse=True)[:k] if sc(m) > 0.05]
    def snap(s): return {"rag_searches": s.n_search, "rag_cache_hits": s.hits, "rag_embed_calls": s.n_embed, "rag_embed_ms": int(s.embed_ms / max(1, s.n_embed)), "rag_mode": "hybride" if s.emb else "lexical"}

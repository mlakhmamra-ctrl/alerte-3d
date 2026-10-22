"""FAUX Ollama pour tester la chaîne sans GPU ni modèle (ce n'est PAS un LLM).
Il imite l'API d'Ollama (/api/chat, /api/embed, /api/generate, /api/tags) et répond au schéma JSON demandé avec des
heuristiques simples sur le prompt, plus des pannes volontaires (JSON cassé, sortie de rôle, lenteur) pour tester A4.
Ne jamais présenter ses résultats comme ceux d'un LLM. Lancement : python fake_ollama.py (port 11434)."""
import hashlib, json, math, os, random, re, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LAT = float(os.getenv("FAKE_LATENCY", "1.0")); BAD = float(os.getenv("FAKE_BAD_RATE", "0.05")); rng = random.Random(int(os.getenv("FAKE_SEED", "7")))

def gen(schema, key=""):
    t = schema.get("type")
    if "enum" in schema: return rng.choice(schema["enum"])
    if t == "object": return {k: gen(v, k) for k, v in schema.get("properties", {}).items()}
    if t == "integer": return rng.randint(schema.get("minimum", 0), schema.get("maximum", 100))
    if t == "number": return rng.random()
    if t == "boolean": return rng.random() < .5
    if t == "array": return []
    return f"({key})"

def smart(schema, user):
    o = gen(schema)
    if "croyance" not in o: return o
    low = user.lower()
    msgs = low.split("messages reçus")[1].split("tes contacts")[0] if "messages reçus" in low else ""
    closed = "pont central est fermé" in msgs or "pont central est fermé" in low and "mairie" in msgs
    opened = "pont central est ouvert" in msgs
    evac = "évacuation" in msgs; water = re.search(r"(\d+) cm d'eau devant", low); rumor = "message anonyme" in msgs
    c = o["croyance"]
    c["pont_central"] = "ferme" if closed and not opened else "ouvert" if opened else ("ferme" if rumor and rng.random() < .5 else "inconnu")
    c["danger_imminent"] = bool(evac or water or rumor); c["refuge"] = "gymnase" if c["danger_imminent"] else "aucun"
    o["risque"] = 90 if evac else 70 if water else 55 if rumor else rng.randint(10, 40)
    if "tu prépares" in low or "en route" in low: o["action"] = "rester"
    elif rumor and "verifier" in low and rng.random() < .5 and not evac: o["action"] = "verifier"
    elif o["risque"] >= 60: o["action"] = rng.choice(["preparer_depart", "partir", "relayer"])
    else: o["action"] = rng.choice(["rester", "rester", "relayer"])
    o["itineraire"] = "passage_hauteurs" if c["pont_central"] == "ferme" else "pont_central"
    o["justification"] = {True: "La mairie demande d'évacuer, je ne prends pas de risque.", False: "Je n'ai pas encore d'information sûre, j'attends un peu."}[evac]
    if rumor and not evac: o["justification"] = "Un message sans source dit que le pont est fermé ; je préfère vérifier avant d'y croire."
    hh = re.search(r"il est (\d\d:\d\d)", low); hh = hh.group(1) if hh else "maintenant"
    if o["action"] in ("relayer", "partir", "preparer_depart") and rng.random() < .7:
        o["message"] = f"[faux LLM] À {hh} : {'évacuation demandée par la mairie' if evac else 'l’eau monte'}, pont {c['pont_central']}, je vais au gymnase."
    else: o["message"] = ""
    return o

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _send(self, obj, code=200):
        b = json.dumps(obj).encode(); self.send_response(code); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def do_GET(self):
        self._send({"models": [{"name": "fake"}]}) if self.path.startswith("/api/tags") else self._send({"status": "ok"})
    def do_HEAD(self): self.send_response(200); self.end_headers()
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        if self.path == "/api/embed":
            vec = []
            for txt in body.get("input", []):
                v = [0.0] * 64
                for w in re.findall(r"\w+", txt.lower()): v[int(hashlib.md5(w.encode()).hexdigest(), 16) % 64] += 1
                n = math.sqrt(sum(x * x for x in v)) or 1; vec.append([x / n for x in v])
            return self._send({"embeddings": vec})
        if self.path == "/api/generate": return self._send({"response": "ok", "done": True})
        if self.path == "/api/chat":
            time.sleep(max(0.05, rng.gauss(LAT, LAT / 3)) * (2.5 if any(b in body.get("model", "") for b in ("3b", "7b")) else 1))
            fmt = body.get("format"); user = body["messages"][-1]["content"]
            o = smart(fmt, user) if isinstance(fmt, dict) else {"ok": True}
            r = rng.random()
            content = "{pas du json" if r < BAD / 2 else json.dumps({**o, "justification": "En tant qu'IA, je ne peux pas décider à votre place."}) if r < BAD else json.dumps(o, ensure_ascii=False)
            return self._send({"message": {"role": "assistant", "content": content}, "prompt_eval_count": len(user) // 4, "eval_count": len(content) // 4, "done": True})
        self._send({"error": "not found"}, 404)

if __name__ == "__main__":
    print("FAUX Ollama sur :11434 (tests seulement)", flush=True)
    ThreadingHTTPServer(("0.0.0.0", int(os.getenv("PORT", "11434"))), H).serve_forever()

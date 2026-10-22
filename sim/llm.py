"""Client LLM. Par défaut Ollama en local (API HTTP /api/chat avec sortie JSON contrainte par un schéma).
Option : une API compatible OpenAI (Groq, OpenRouter, Mistral...) via LLM_PROVIDER=openai, LLM_API_URL, LLM_API_KEY.
Le client ne « pense » pas : il envoie un prompt et renvoie un objet JSON validé. L'agent (agents.py) décide quoi en faire.
Chaque appel est compté (appels, tokens, latence) pour le bilan de sobriété (E4)."""
import asyncio, json, re, time
import httpx

DRIFT = re.compile(r"en tant qu.(ia|assistant|intelligence)|je suis (une |un )?(ia|assistant|mod[eè]le)|as an ai|language model|mod[eè]le de langage", re.I)
CAUTIOUS = re.compile(r"je ne peux pas (vous |t')?aider|je ne suis pas en mesure|consulte[rz]? (un|une|les) (professionnel|expert)|appele[rz] le 1[12]", re.I)

class Stats:
    def __init__(s): s.calls = {}; s.tin = s.tout = 0; s.inc = 0; s.kinds = {}; s.lat = []; s.viol = 0
    def snap(s):
        lat = sorted(s.lat)
        return {"calls": dict(s.calls), "calls_total": sum(s.calls.values()), "tokens_in": s.tin, "tokens_out": s.tout,
                "incidents": s.inc, "incidents_by_kind": dict(s.kinds), "temporal_violations": s.viol,
                "avg_latency_ms": int(sum(lat) / max(1, len(lat))), "p95_latency_ms": int(lat[int(.95 * (len(lat) - 1))]) if lat else 0}

class LLM:
    def __init__(s, provider, url, key, timeout, parallel, temperature, stats, on_incident):
        s.provider, s.url, s.key, s.timeout = provider, url.rstrip("/"), key, timeout
        s.http = httpx.AsyncClient(); s.sem = asyncio.Semaphore(parallel); s.temp = temperature
        s.stats, s.on_incident = stats, on_incident; s.inflight = 0

    async def close(s): await s.http.aclose()

    async def warmup(s, models):          # charge les modèles avant le run : la première requête ne fausse pas les mesures
        if s.provider != "ollama": return
        for m in dict.fromkeys(models):
            try:
                await s.http.post(f"{s.url}/api/generate", timeout=900, json={"model": m, "prompt": "ok", "stream": False, "keep_alive": "30m", "options": {"num_predict": 1}})
                print("modèle prêt :", m, flush=True)
            except Exception as ex: print("warmup", m, repr(ex), flush=True)

    async def ask(s, agent, model, system, user, schema, check_fields=("justification", "message")):
        """Renvoie un dict, ou None si le modèle ne répond pas, répond mal ou sort de son rôle (incident tracé, A4)."""
        t0 = time.time(); s.inflight += 1
        try:
            async with s.sem:
                try:
                    o, tin, tout = await (s._ollama(model, system, user, schema) if s.provider == "ollama" else s._openai(model, system, user))
                except httpx.TimeoutException as ex: await s.on_incident(agent, "timeout", repr(ex)[:100]); return None
                except (ValueError, KeyError, json.JSONDecodeError) as ex: await s.on_incident(agent, "unparsable", repr(ex)[:100]); return None
                except Exception as ex: await s.on_incident(agent, "llm_error", repr(ex)[:100]); return None
                st = s.stats; st.calls[model] = st.calls.get(model, 0) + 1; st.tin += tin; st.tout += tout; st.lat.append((time.time() - t0) * 1000)
                if not isinstance(o, dict): await s.on_incident(agent, "unparsable", "pas un objet JSON"); return None
                txt = " ".join(str(o.get(k, "")) for k in check_fields)
                if DRIFT.search(txt): await s.on_incident(agent, "role_exit", txt[:120]); return None
                if CAUTIOUS.search(txt): await s.on_incident(agent, "assistant_posture", txt[:120]); return None
                o["_latency_ms"] = int((time.time() - t0) * 1000)
                return o
        finally: s.inflight -= 1

    async def _ollama(s, model, system, user, schema):
        body = {"model": model, "stream": False, "keep_alive": "30m", "options": {"temperature": s.temp, "num_predict": 260},
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        for fmt in (schema, "json"):                                 # schéma strict d'abord, puis "json" pour un vieil Ollama
            r = await s.http.post(f"{s.url}/api/chat", timeout=s.timeout, json=dict(body, format=fmt))
            if r.status_code >= 400 and fmt is schema: continue
            if r.status_code >= 400: raise RuntimeError(f"http {r.status_code}")
            j = r.json(); return json.loads(j["message"]["content"]), j.get("prompt_eval_count", 0), j.get("eval_count", 0)
        raise RuntimeError("aucun format accepté")

    async def _openai(s, model, system, user):
        r = await s.http.post(f"{s.url}/chat/completions", timeout=s.timeout, headers={"Authorization": f"Bearer {s.key}"},
                              json={"model": model, "temperature": s.temp, "max_tokens": 260, "response_format": {"type": "json_object"},
                                    "messages": [{"role": "system", "content": system}, {"role": "user", "content": user + "\nRéponds uniquement par un objet JSON."}]})
        if r.status_code == 429: raise RuntimeError("quota atteint (429)")
        if r.status_code >= 400: raise RuntimeError(f"http {r.status_code}")
        j = r.json(); u = j.get("usage", {})
        return json.loads(j["choices"][0]["message"]["content"]), u.get("prompt_tokens", 0), u.get("completion_tokens", 0)

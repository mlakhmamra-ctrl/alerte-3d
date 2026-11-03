"""Client LLM. Par défaut Ollama en local (API HTTP /api/chat avec sortie JSON contrainte par un schéma).
Option : une API compatible OpenAI (Groq, OpenRouter, Mistral...) via LLM_PROVIDER=openai, LLM_API_URL, LLM_API_KEY.
Plusieurs API à la fois (LLM_BACKENDS) : chaque appel part vers l'API la moins chargée ; si elle échoue (quota, erreur HTTP),
elle est mise de côté LLM_COOLDOWN secondes et l'appel repart vers la suivante. Moins de calcul en local, plus de débit.
Le client ne « pense » pas : il envoie un prompt et renvoie un objet JSON validé. L'agent (agents.py) décide quoi en faire.
Chaque appel est compté (appels, tokens, latence, API utilisée) pour le bilan de sobriété (E4)."""
import asyncio, json, re, time
import httpx

DRIFT = re.compile(r"en tant qu.(ia|assistant|intelligence)|je suis (une |un )?(ia|assistant|mod[eè]le)|as an ai|language model|mod[eè]le de langage", re.I)
CAUTIOUS = re.compile(r"je ne peux pas (vous |t')?aider|je ne suis pas en mesure|consulte[rz]? (un|une|les) (professionnel|expert)|appele[rz] le 1[12]", re.I)

class Backend:
    """Une API LLM : Ollama local ou API compatible OpenAI, avec son modèle grand et son modèle compact."""
    def __init__(s, name, provider, url, key, parallel, big, small, reasoning=""):
        s.name, s.provider, s.url, s.key, s.reasoning = name, provider, url.rstrip("/"), key, reasoning
        s.models = {"big": big, "small": small}; s.parallel = max(1, parallel)
        s.sem = asyncio.Semaphore(s.parallel); s.inflight = 0; s.down_until = 0.0
    def load(s): return s.inflight / s.parallel
    def info(s): return {"name": s.name, "provider": s.provider, "models": s.models, "parallel": s.parallel}

def backends_from_env(env, ollama_url):
    """LLM_BACKENDS=local,groq : « local » reprend LLM_PROVIDER / LLM_API_URL / LLM_BIG / LLM_SMALL / LLM_PARALLEL ;
    chaque autre nom NOM lit LLM_NOM_PROVIDER (openai par défaut), LLM_NOM_URL, LLM_NOM_KEY, LLM_NOM_BIG, LLM_NOM_SMALL, LLM_NOM_PARALLEL,
    LLM_NOM_REASONING (reasoning_effort, ex. low : un modèle qui raisonne garde assez de tokens pour écrire son JSON).
    Une API sans URL est ignorée (message dans la console)."""
    out = []
    for name in [x.strip() for x in env("LLM_BACKENDS", "local").split(",") if x.strip()]:
        if name == "local":
            out.append(Backend("local", env("LLM_PROVIDER", "ollama"), env("LLM_API_URL", "") or ollama_url, env("LLM_API_KEY", ""),
                               int(env("LLM_PARALLEL", 2)), env("LLM_BIG", "qwen2.5:7b"), env("LLM_SMALL", "qwen2.5:0.5b")))
            continue
        N = name.upper(); url = env(f"LLM_{N}_URL", "")
        if not url: print(f"API LLM « {name} » ignorée : LLM_{N}_URL est vide", flush=True); continue
        big = env(f"LLM_{N}_BIG", ""); small = env(f"LLM_{N}_SMALL", "") or big
        out.append(Backend(name, env(f"LLM_{N}_PROVIDER", "openai"), url, env(f"LLM_{N}_KEY", ""), int(env(f"LLM_{N}_PARALLEL", 4)), big or small, small, env(f"LLM_{N}_REASONING", "")))
    return out

class Stats:
    def __init__(s): s.calls = {}; s.tin = s.tout = 0; s.inc = 0; s.kinds = {}; s.lat = []; s.viol = 0; s.by_api = {}; s.fallbacks = 0
    def snap(s):
        lat = sorted(s.lat)
        return {"calls": dict(s.calls), "calls_total": sum(s.calls.values()), "tokens_in": s.tin, "tokens_out": s.tout,
                "incidents": s.inc, "incidents_by_kind": dict(s.kinds), "temporal_violations": s.viol,
                "calls_by_api": dict(s.by_api), "api_fallbacks": s.fallbacks,
                "avg_latency_ms": int(sum(lat) / max(1, len(lat))), "p95_latency_ms": int(lat[int(.95 * (len(lat) - 1))]) if lat else 0}

class LLM:
    def __init__(s, backends, timeout, temperature, stats, on_incident, cooldown=60.0):
        s.backends, s.timeout, s.temp, s.cooldown = backends, timeout, temperature, cooldown
        s.http = httpx.AsyncClient(); s.stats, s.on_incident = stats, on_incident
    @property
    def inflight(s): return sum(b.inflight for b in s.backends)
    def model_of(s, tier):                # modèle affiché pour un habitant : celui de la première API
        return s.backends[0].models.get(tier) if s.backends else None

    async def close(s): await s.http.aclose()

    async def warmup(s):                  # charge les modèles avant le run : la première requête ne fausse pas les mesures
        for b in s.backends:
            if b.provider != "ollama": continue
            for m in dict.fromkeys(b.models.values()):
                try:
                    await s.http.post(f"{b.url}/api/generate", timeout=900, json={"model": m, "prompt": "ok", "stream": False, "keep_alive": "30m", "options": {"num_predict": 1}})
                    print("modèle prêt :", m, flush=True)
                except Exception as ex: print("warmup", m, repr(ex), flush=True)

    def _order(s):
        now = time.monotonic()
        up = sorted((b for b in s.backends if b.down_until <= now), key=lambda b: (b.load(), s.backends.index(b)))
        return up or sorted(s.backends, key=lambda b: b.down_until)      # toutes en pause : on retente la première à revenir

    async def ask(s, agent, tier, system, user, schema, check_fields=("justification", "message")):
        """Renvoie un dict, ou None si aucune API ne répond, répond mal ou sort de son rôle (incident tracé, A4).
        tier : "big" ou "small" ; chaque API a son propre modèle pour chaque taille."""
        t0 = time.time(); err = None
        for i, b in enumerate(s._order()):
            if i: s.stats.fallbacks += 1
            model = b.models[tier]; b.inflight += 1
            try:
                async with b.sem:
                    o, tin, tout = await (s._ollama(b, model, system, user, schema) if b.provider == "ollama" else s._openai(b, model, system, user))
            except httpx.TimeoutException as ex:
                b.down_until = time.monotonic() + s.cooldown; await s.on_incident(agent, "timeout", f"{b.name} {ex!r}"[:100]); return None
            except (ValueError, KeyError, json.JSONDecodeError) as ex: await s.on_incident(agent, "unparsable", f"{b.name} {ex!r}"[:100]); return None
            except Exception as ex:                            # quota, erreur HTTP, API injoignable : on passe à l'API suivante
                b.down_until = time.monotonic() + s.cooldown; err = f"{b.name} {ex!r}"; continue
            finally: b.inflight -= 1
            st = s.stats; st.calls[model] = st.calls.get(model, 0) + 1; st.by_api[b.name] = st.by_api.get(b.name, 0) + 1
            st.tin += tin; st.tout += tout; st.lat.append((time.time() - t0) * 1000)
            if not isinstance(o, dict): await s.on_incident(agent, "unparsable", "pas un objet JSON"); return None
            txt = " ".join(str(o.get(k, "")) for k in check_fields)
            if DRIFT.search(txt): await s.on_incident(agent, "role_exit", txt[:120]); return None
            if CAUTIOUS.search(txt): await s.on_incident(agent, "assistant_posture", txt[:120]); return None
            o["_latency_ms"] = int((time.time() - t0) * 1000); o["_model"] = model; o["_api"] = b.name
            return o
        await s.on_incident(agent, "llm_error", (err or "aucune API LLM configurée")[:100]); return None

    async def _ollama(s, b, model, system, user, schema):
        body = {"model": model, "stream": False, "keep_alive": "30m", "options": {"temperature": s.temp, "num_predict": 260},
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        for fmt in (schema, "json"):                                 # schéma strict d'abord, puis "json" pour un vieil Ollama
            r = await s.http.post(f"{b.url}/api/chat", timeout=s.timeout, json=dict(body, format=fmt))
            if r.status_code >= 400 and fmt is schema: continue
            if r.status_code >= 400: raise RuntimeError(f"http {r.status_code}")
            j = r.json(); return json.loads(j["message"]["content"]), j.get("prompt_eval_count", 0), j.get("eval_count", 0)
        raise RuntimeError("aucun format accepté")

    async def _openai(s, b, model, system, user):
        body = {"model": model, "temperature": s.temp, "max_tokens": 260, "response_format": {"type": "json_object"},
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user + "\nRéponds uniquement par un objet JSON."}]}
        if b.reasoning: body["reasoning_effort"] = b.reasoning
        r = await s.http.post(f"{b.url}/chat/completions", timeout=s.timeout, headers={"Authorization": f"Bearer {b.key}"}, json=body)
        if r.status_code == 429: raise RuntimeError("quota atteint (429)")
        if r.status_code >= 400: raise RuntimeError(f"http {r.status_code} {r.text[:120]}")
        j = r.json(); u = j.get("usage", {})
        return json.loads(j["choices"][0]["message"]["content"]), u.get("prompt_tokens", 0), u.get("completion_tokens", 0)

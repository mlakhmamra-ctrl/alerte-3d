"""ALERTE 3D — décisions humaines et évacuation collective pendant une crue (fiche ALERTE, thématique ESSAIM).
Simulation Python : monde (crue, rues, refuges) + N_AGENTS habitants (les 12 de la fiche, dont 4 LLM via Ollama, puis des voisins générés). Tous les événements vont dans Kafka
(topic alerte.events) et dans data/runs/<run_id>.jsonl. La passerelle (gateway/) les relaie à Godot.
Modes : async (chacun à son rythme, latence réelle du LLM comprise) / sync (pas de 30 s simulées, on attend le plus lent)."""
import asyncio, json, math, os, random, time, uuid
from agents import Agent, SEV, who
from corpus import DOCS
from indicators import Indicators
from llm import LLM, Stats, backends_from_env
from profiles import population, extra_llm_ids, apply_overrides, add_batch, CANAUX, MAX_AGENTS
from rag import RAG
from world import Flood, Streets, REFUGES, REFUGE_R, BRIDGE, NODES, CRUES, heightmap, hm, t_of, ekey, speed_factor, X0, X1, Z0, Z1, CELL, NX, NZ, RIVER_Z, RIVER_W, BED_Y, S as WORLD_SCALE, PASS_X, EXTENDED

def env(k, d): return os.getenv(k, str(d)).split("#")[0].strip()
KAFKA = env("KAFKA", "")                                    # vide = pas de Kafka (tests, lots de runs) : fichier JSONL seulement
OLLAMA = env("OLLAMA_URL", "http://ollama:11434"); PROVIDER = env("LLM_PROVIDER", "ollama")
API_URL = env("LLM_API_URL", "") or OLLAMA; API_KEY = env("LLM_API_KEY", "")
N = int(env("N_AGENTS", 12)); MODE = env("MODE", "async"); SCALE = float(env("TIME_SCALE", 1))   # 1 = temps réel : 1 s simulée = 1 s réelle
BIG = env("LLM_BIG", "qwen2.5:7b"); SMALL = env("LLM_SMALL", "qwen2.5:0.5b")
LLM_IDS = [x.strip() for x in env("LLM_AGENTS", "A04,A05,A06,A11").split(",") if x.strip() and x.strip() != "none"]
EXTRA_IDS, EXTRA_BIG = extra_llm_ids(N, int(env("LLM_EXTRA", 0)) if LLM_IDS else 0)   # LLM_EXTRA : habitants générés qui pensent aussi avec un LLM
LLM_IDS += [x for x in EXTRA_IDS if x not in LLM_IDS]
SCENARIO = env("SCENARIO", "pont_ferme")                    # pont_ouvert : crue modérée, rumeur fausse ; pont_ferme : crue forte, pont fermé à 09:20
UPDATE_AT = t_of(env("UPDATE_AT", "09:30"))                 # H1 : mise à jour de la mairie à 09:10 ou 09:30
RUMOR_AT = env("RUMOR_AT", "09:20"); RUMOR_TO = [x.strip() for x in env("RUMOR_TO", "A06,A09").split(",") if x.strip()]
SEED = int(env("SEED", 1)); BATCH = int(env("BATCH", 0)); REPLAY = env("REPLAY_FILE", ""); RUNS = env("RUNS_DIR", "/data/runs")
LLM_PAR = int(env("LLM_PARALLEL", 2)); TIMEOUT = float(env("LLM_TIMEOUT", 90)); TEMP = float(env("LLM_TEMPERATURE", .7))
THINK_EVERY = float(env("THINK_EVERY", 4)); LLM_MIN_GAP = float(env("LLM_MIN_GAP", 1)); RAG_MODE = env("RAG_MODE", "hybrid"); EMBED_MODEL = env("EMBED_MODEL", "nomic-embed-text")
T_START, T_END = t_of("09:00"), t_of(env("T_END", "10:00"))
SCHEMA = "alerte.event/1"; TOPIC = "alerte.events"
PEAK = {"pont_ouvert": 0.32, "pont_ferme": 0.75}
DT = 5.0                                                     # pas physique (s simulées)
# conséquences de la crue (paramètres de maquette, pas des données réelles) : en danger, victime, dégâts des maisons
OUT_DANGER = float(env("OUT_DANGER", .3))                    # m d'eau : dehors, en danger
IN_DANGER = float(env("IN_DANGER", .5))                      # m d'eau au rez-de-chaussée : en danger si l'on ne peut pas monter à l'étage
VICTIM_MIN = float(env("VICTIM_MIN", 15))                    # min en détresse avant de devenir victime
DAMAGE = ((.5, "sinistree"), (.2, "graves"), (.05, "legers"))   # m d'eau devant la maison -> niveau de dégâts
CRUE = env("CRUE", "rapide")                                 # lente, rapide (fiche) ou eclair : vitesse de montée de l'eau (world.CRUES)
PANNE_RESEAU = env("PANNE_RESEAU", "none")                   # "09:40" : le réseau mobile tombe (téléphone et groupe coupés) ; none = jamais
SPEED_MAX = 60.0                                             # vitesse maximale réglable en cours de run (60 = 1 min simulée par seconde)
FACE_DIST = 15.0                                             # m : à portée de voix, on se parle de vive voix (pas besoin de réseau)
SIREN_R = 90.0 * WORLD_SCALE                                 # m autour de la place : on entend la sirène de la mairie
FAMILY_WAIT = float(env("FAMILY_WAIT", 5))                   # min : un habitant prêt attend au plus ce temps les autres membres de son foyer
DELAY = {"face_a_face": (.1, .5), "telephone": (.3, 1.5), "groupe": (.5, 3.0)}   # min avant d'être lu, par canal
VILLAGEOIS = env("VILLAGEOIS_FILE", os.path.join(os.path.dirname(RUNS.rstrip("/")) or ".", "villageois.json"))   # réglages de l'interface de gestion

def custom():
    """Réglages enregistrés par l'interface de gestion : {"settings": {...}, "profiles": {id: {...}}}. Vide si aucun."""
    try:
        with open(VILLAGEOIS, encoding="utf-8") as f: c = json.load(f)
        return c if isinstance(c, dict) else {}
    except (OSError, ValueError): return {}

def apply_settings(cs):
    global MODE, SCENARIO, CRUE, PANNE_RESEAU, SCALE
    if cs.get("mode") in ("sync", "async"): MODE = cs["mode"]
    if cs.get("scenario") in PEAK: SCENARIO = cs["scenario"]
    if cs.get("crue") in CRUES: CRUE = cs["crue"]
    try: SCALE = max(.5, min(SPEED_MAX, float(cs["vitesse"]))) if "vitesse" in cs and SCALE > 0 else SCALE   # 0 (lots de runs) : inchangé
    except (TypeError, ValueError): pass
    p = str(cs.get("panne_reseau", PANNE_RESEAU))
    if p == "none" or (len(p) == 5 and p[2] == ":" and p.replace(":", "").isdigit()): PANNE_RESEAU = p
DEFAULTS = {"mode": MODE, "scenario": SCENARIO, "crue": CRUE, "panne_reseau": PANNE_RESEAU, "vitesse": SCALE}   # valeurs de .env, rétablies par « réinitialiser »

def ev_base(typ, agent, data, run="control", seq=0, t_sim=-1):
    return {"schema": SCHEMA, "run_id": run, "seq": seq, "type": typ, "t_sim": t_sim, "t_proc": int(time.time() * 1000), "agent": agent, "data": data}

class Bus:
    def __init__(s): s.p = None
    async def start(s):
        if KAFKA:
            from aiokafka import AIOKafkaProducer
            s.p = AIOKafkaProducer(bootstrap_servers=KAFKA, linger_ms=5); await s.p.start()
    async def send(s, ev):
        if s.p: await s.p.send(TOPIC, json.dumps(ev, ensure_ascii=False).encode(), key=str(ev["run_id"]).encode())

class Clock:
    def __init__(s, mode, scale): s.mode, s.scale = mode, scale; s.fixed = s.base = T_START; s.start = time.monotonic(); s.paused_at = None; s.lost = 0.0
    def now(s):
        if s.mode == "sync": return s.fixed
        ref = s.paused_at if s.paused_at is not None else time.monotonic()
        return s.base + (ref - s.start - s.lost) * s.scale / 60.0
    def set_scale(s, scale):                  # change la vitesse en cours de run, sans saut de l'horloge simulée
        s.base = s.now(); s.start = s.paused_at if s.paused_at is not None else time.monotonic(); s.lost = 0.0; s.scale = scale
    def pause(s):
        if s.paused_at is None: s.paused_at = time.monotonic()
    def resume(s):
        if s.paused_at is not None: s.lost += time.monotonic() - s.paused_at; s.paused_at = None
    @property
    def paused(s): return s.paused_at is not None

class World:
    def __init__(s, bus, kb, seed, mode, scenario):
        s.bus, s.kb, s.mode, s.scenario = bus, kb, mode, scenario
        s.rng = random.Random(seed); s.seed = seed; s.run = uuid.uuid4().hex[:8]; s.seq = 0
        os.makedirs(RUNS, exist_ok=True); s.file = open(f"{RUNS}/{s.run}.jsonl", "w", encoding="utf-8")
        s.clock = Clock(mode, SCALE if SCALE > 0 else 60.0); s.stats = Stats(); s.ind = Indicators(); s.stop = asyncio.Event(); s.auto = False
        s.llm = LLM(backends_from_env(env, OLLAMA), TIMEOUT, TEMP, s.stats, s.incident, float(env("LLM_COOLDOWN", 60))); s.think_every = THINK_EVERY
        s.streets = Streets(); s.flood = Flood(PEAK.get(scenario, .75), CRUE); s.network_up = True
        s.bridge_open = True; s.bridge_close_at = t_of("09:20") if scenario == "pont_ferme" else None
        cu = custom(); s.profiles = population(N, set(LLM_IDS), s.rng, big_ids=("A04", "A05", *EXTRA_BIG))
        for k, lot in enumerate((cu.get("settings") or {}).get("lots") or [], 1):      # lots ajoutés depuis l'interface de gestion
            if isinstance(lot, dict): add_batch(s.profiles, lot, s.rng, k)
        s.profiles = apply_overrides(s.profiles, cu.get("profiles", {}))
        s.agents = {}
        for p in s.profiles:
            home = s.streets.add_home(p["id"], *p["pos"])
            a = Agent(p, home, random.Random(s.rng.random())); a.model = s.llm.model_of(a.kind); s.agents[a.id] = a
            if a.kind != "rule": a.last_think = T_START - LLM_MIN_GAP + a.rng.uniform(0, LLM_MIN_GAP)   # premières réflexions LLM étalées, pas toutes à 09:00
        s.families = {}
        for a in s.agents.values(): s.families.setdefault(a.p["famille"], []).append(a.id)
        for a in s.agents.values(): a.family = set(s.families[a.p["famille"]]) - {a.id}
        s.houses = {f: {"pos": s.agents[ids[0]].p["pos"], "ids": ids, "max": 0.0, "level": None} for f, ids in s.families.items()}
        s.llm_ids = [a.id for a in s.agents.values() if a.kind != "rule"]
        s.occupancy = {r: [] for r in REFUGES}; s.msg_id = 0; s.truth_last = None; s.t_phys = T_START; s.next_tick = T_START; s.next_flood = T_START
        s.schedule = [(T_START, "bulletin", {"level": "vigilance"}), (UPDATE_AT, "bulletin", {"level": "evacuation"})]
        if RUMOR_AT and RUMOR_AT != "none": s.schedule.append((t_of(RUMOR_AT), "rumor", {}))
        if s.bridge_close_at: s.schedule.append((s.bridge_close_at, "close_bridge", {}))
        if PANNE_RESEAU != "none": s.schedule.append((t_of(PANNE_RESEAU), "network_down", {}))
        s.schedule.sort(key=lambda x: x[0]); s.t_evt = None; s.last_cmd = {}

    # ---------- événements ----------
    async def emit(s, typ, agent=None, **data):
        s.seq += 1; ev = ev_base(typ, agent, data, s.run, s.seq, round(s.t_evt if s.t_evt is not None else s.clock.now(), 3))
        s.file.write(json.dumps(ev, ensure_ascii=False) + "\n"); s.file.flush(); s.ind.feed(ev)
        await s.bus.send(ev)
    async def incident(s, a, kind, detail):
        s.stats.inc += 1; s.stats.kinds[kind] = s.stats.kinds.get(kind, 0) + 1
        await s.emit("llm.incident", a.id if a else None, kind=kind, detail=detail)

    # ---------- messages ----------
    async def send(s, a, text, claims, kind, origin, hops=0, parent=None, to=None):
        now = s.clock.now(); key = (text, kind)
        if key in a.sent_keys: return
        a.sent_keys.add(key); s.msg_id += 1; mid = f"m{s.msg_id}"; want = [c for c in (to or a.p["contacts"]) if c in s.agents]
        to, canaux, lost = [], {}, []
        for c in want:
            b = s.agents[c]; ch = s.canal(a, b, kind)
            if ch is None: lost.append(c); continue
            lo, hi = DELAY[ch]
            delay = s.rng.uniform(lo, hi) + (1 if b.state == "walking" and ch != "face_a_face" else 0)   # on lit moins vite en marchant
            b.inbox.append({"id": mid, "src": a.id, "origin": origin, "hops": hops, "text": text, "claims": claims, "kind": kind, "canal": ch,
                            "level": claims.get("level", "alerte"), "t_sent": now, "t_deliver": now + delay, "parent": parent})
            to.append(c); canaux[c] = ch
        if lost:
            a.remember(now, f"{hm(now)} — impossible de joindre {', '.join(lost)} (pas de réseau ni de moyen commun)", "message")
            await s.emit("message.lost", a.id, to=lost, kind=kind, msg_id=mid, reason="réseau coupé" if not s.network_up else "aucun canal commun")
        if not to: return
        a.remember(now, f"{hm(now)} — j'ai écrit à {', '.join(to)} : « {text} »", "message", *[c for c in to])
        await s.emit("agent.say", a.id, to=to, text=text, kind=kind, origin=origin, hops=hops, msg_id=mid, parent=parent, claims=claims, decider=a.kind, model=a.model,
                     canaux=canaux)

    def canal(s, a, b, kind):
        """Moyen par lequel a joint b : de vive voix (même foyer ou à portée de voix), sinon téléphone ou groupe du quartier
        (réseau mobile nécessaire, et il faut que les deux aient ce moyen). None : impossible de le joindre."""
        if b.id in a.family or math.dist((a.x, a.z), (b.x, b.z)) <= FACE_DIST: return "face_a_face"
        if not s.network_up: return None
        both = set(a.p.get("canaux", [])) & set(b.p.get("canaux", []))
        if kind in ("relais", "message") and "groupe" in both: return "groupe"        # on fait suivre sur le groupe du quartier
        return "telephone" if "telephone" in both else "groupe" if "groupe" in both else None

    async def network_down(s):
        if not s.network_up: return
        s.network_up = False; await s.emit("network.down", None, at=round(s.clock.now(), 2), canaux=["telephone", "groupe"])

    async def family_wait(s, a, t):
        """a est prêt : attend-il encore sa famille ? Au premier appel il prévient les membres pas encore prêts (de vive voix).
        Un membre à règles qui était encore chez lui se met à préparer son départ ; un membre LLM décide lui-même."""
        late = [m for m in (s.agents[i] for i in a.family) if m.state == "home" or (m.state == "preparing" and t < m.t_ready)]
        if not late or t >= a.t_ready + FAMILY_WAIT: return False
        if not a.family_called:
            a.family_called = True
            await s.send(a, "On part au refuge, prépare-toi vite, je t'attends.", {"danger": True, "level": "evacuation", "refuge": a.refuge if a.refuge in REFUGES else None},
                         "famille", origin=a.id, to=[m.id for m in late])
            for m in late:
                if m.state == "home" and m.kind == "rule": await m.start_prep(s, t, why=f"{a.name} me demande de partir avec la famille")
        return True

    async def family_join(s, a, t):
        """a vient de partir : les membres de son foyer prêts partent avec lui, vers le même refuge, à l'allure du plus lent."""
        group = [a] + [m for m in (s.agents[i] for i in sorted(a.family)) if m.state == "preparing" and t >= m.t_ready]
        if len(group) == 1: return
        for m in group[1:]:
            m.refuge = a.refuge; await m.depart(s, t, a.via if a.via in ("pont_central", "passage_hauteurs") else None)
        walking = [m for m in group if m.state == "walking"]
        pace = min(m.p["mob"] for m in walking)
        for m in walking: m.pace = pace

    async def observation_message(s, a, what):
        if a.kind != "rule" or a.rng.random() > max(.5, a.p["relay"]): return     # les agents LLM rédigent eux-mêmes au tour suivant
        now = s.clock.now()
        if what == "pont_ferme": txt, cl = f"J'ai vu à {hm(now)} que le Pont Central est fermé, passez par le Passage des Hauteurs.", {"bridge": "ferme", "danger": True, "level": "alerte"}
        else: txt, cl = f"À {hm(now)} il y a déjà {int(a.water_seen * 100)} cm d'eau près de chez moi.", {"danger": True, "level": "alerte"}
        await s.send(a, txt, cl, "observation", origin=a.id)

    def deliver(s, a, t):
        new = [m for m in a.inbox if s.t_phys < m["t_deliver"] <= t]
        for m in new:
            a.dirty = True
            if m["claims"].get("danger") or SEV.get(m["level"], 0) >= 60: a.confirm.add(m["origin"])
            a.remember(m["t_deliver"], f"{hm(m['t_deliver'])} — {who(m['src'], s.agents)} m'a écrit : « {m['text']} »", "message", m["src"], m["origin"],
                       *(["pont"] if m["claims"].get("bridge") else []))
        return new

    # ---------- monde ----------
    def truth(s, t):
        """Vérité : pont, danger, refuges encore accessibles depuis la place (place libre + chemin avec moins de 50 cm d'eau)."""
        blocked = set() if s.bridge_open else {ekey(*BRIDGE)}
        wet = {ekey(a, b) for (a, b) in s.streets.meta if max(s.flood.depth(*s.streets.nodes[a], t), s.flood.depth(*s.streets.nodes[b], t)) >= .5}
        acc = [r for r, R in REFUGES.items() if len(s.occupancy[r]) < R["cap"] and s.streets.path("PLACE", R["node"], blocked | wet)[0]]
        return {"bridge": "ouvert" if s.bridge_open else "ferme", "danger": t >= s.flood.t_bank, "refuges": acc}

    async def bulletin(s, level, t):
        tr = s.truth(t)
        if level == "vigilance": txt = "Mairie, 09:00 : vigilance crue. La rivière va monter ce matin. Préparez-vous à partir vers un refuge."; cl = {"danger": False, "level": "vigilance"}
        else:
            txt = (f"Mairie, {hm(t)} : évacuation des Quais. Le Pont Central est {'OUVERT' if tr['bridge'] == 'ouvert' else 'FERMÉ, prenez le Passage des Hauteurs'}. "
                   f"Refuges : Gymnase des Hauteurs (rive nord) et École des Tilleuls (7 places)."
                   + (" Ouest du quartier : Église Saint-Roch. Est du quartier : Collège du Plateau, ou Salle des Fêtes du Moulin." if EXTENDED else ""))
            cl = {"danger": True, "bridge": tr["bridge"], "refuge": "gymnase", "level": "evacuation"}
        s.msg_id += 1; mid = f"m{s.msg_id}"; who_got = []; canaux = {}; missed = []
        place = s.streets.nodes["PLACE"]
        for a in s.agents.values():
            silent = a.rng.random() < .1 and level == "vigilance"                # téléphone en silencieux : certains ratent le premier bulletin
            ch = ("officiel" if s.network_up and "telephone" in a.p.get("canaux", []) and not silent else
                  "radio" if "radio" in a.p.get("canaux", []) and a.state in ("home", "preparing", "sheltered") else
                  "sirene" if level == "evacuation" and math.dist((a.x, a.z), place) <= SIREN_R else None)
            if ch is None: missed.append(a.id); continue
            text, claims = (("Sirène de la mairie : signal d'évacuation.", {"danger": True, "level": "evacuation"}) if ch == "sirene" else (txt, cl))
            a.inbox.append({"id": mid, "src": "mairie", "origin": "mairie", "hops": 0, "text": text, "claims": claims, "kind": "officiel", "level": level, "canal": ch,
                            "t_sent": t, "t_deliver": t + a.rng.uniform(0, 3), "verified": True, "parent": None}); who_got.append(a.id); canaux[a.id] = ch
        await s.emit("official.bulletin", None, level=level, text=txt, claims=cl, msg_id=mid, to=who_got, canaux=canaux, missed=missed)

    async def rumor(s, t, targets=None):
        claim = "ferme" if s.bridge_open else "ouvert"
        txt = "Le Pont Central est fermé, il ne faut plus passer par là !" if claim == "ferme" else "Le Pont Central est rouvert, c'est le plus rapide !"
        if claim == "ferme" and s.bridge_close_at and s.bridge_close_at <= t + 1: claim_true = True
        else: claim_true = (claim == "ferme") == (not s.bridge_open)
        tg = [x for x in (targets or RUMOR_TO) if x in s.agents] or s.rng.sample(list(s.agents), min(2, len(s.agents)))
        s.msg_id += 1; mid = f"m{s.msg_id}"
        for x in tg:
            s.agents[x].inbox.append({"id": mid, "src": "anonyme", "origin": "anonyme", "hops": 0, "text": txt, "claims": {"bridge": claim, "danger": True, "level": "alerte"},
                                      "kind": "rumeur", "level": "alerte", "t_sent": t, "t_deliver": t + .2, "parent": None})
        await s.emit("rumor", None, text=txt, claim=claim, targets=tg, msg_ids=[mid], true=claim_true)

    async def close_bridge(s):
        if not s.bridge_open: return
        s.bridge_open = False; await s.emit("bridge.closed", None, at=s.clock.now())

    def mairie_knows(s, t): return t >= t_of("09:15") or (s.bridge_close_at and t >= s.bridge_close_at)

    async def verify_done(s, a, t):
        a.verify_until = None
        if not s.network_up:
            res, cl = "Pas de réseau : impossible de joindre le poste municipal.", {"level": "vigilance"}
        elif not s.mairie_knows(t) and s.bridge_open:
            res, cl = "Le poste municipal n'a pas encore d'information sur le pont.", {"level": "vigilance"}
        else:
            b = "ouvert" if s.bridge_open else "ferme"; res = f"Poste municipal, {hm(t)} : le Pont Central est {b.upper()}."; cl = {"bridge": b, "danger": t >= t_of("09:10"), "level": "alerte"}
        s.msg_id += 1; mid = f"m{s.msg_id}"
        a.inbox.append({"id": mid, "src": "mairie", "origin": "mairie", "hops": 0, "text": res, "claims": cl, "kind": "verification", "level": cl["level"],
                        "t_sent": t, "t_deliver": t, "verified": True, "parent": None})
        a.dirty = True; a.remember(t, f"{hm(t)} — j'ai appelé le poste municipal : {res}", "pont", "verification")
        await s.emit("agent.verify_result", a.id, result=res, bridge=cl.get("bridge"))
        if a.kind == "rule" and cl.get("bridge"):                           # un vérificateur à règles corrige l'erreur qu'il a reçue
            wrong = [m for m in a.inbox if m["claims"].get("bridge") not in (None, cl["bridge"]) and m["src"] != "mairie"]
            if wrong:
                await s.send(a, f"Correction : j'ai appelé la mairie à {hm(t)}, le Pont Central est {cl['bridge'].upper()}. Le message « {wrong[-1]['text'][:60]} » est faux.",
                             {"bridge": cl["bridge"], "danger": True, "level": "alerte"}, "correction", origin="mairie", parent=wrong[-1]["id"])

    def look_around(s, a, t):
        pts = [(a.x, a.z)] + [s.streets.nodes[n] for n in s.streets.nodes if math.dist(s.streets.nodes[n], (a.x, a.z)) < 12 and not n.startswith(("H_", "E_"))]
        return max(s.flood.depth(x, z, t) for x, z in pts)

    async def distress(s, a, t):
        """En danger : dehors dans OUT_DANGER m d'eau, ou à l'intérieur avec IN_DANGER m au rez-de-chaussée sans pouvoir
        monter à l'étage (no_stairs). Toujours en danger après VICTIM_MIN minutes : victime (il ne bouge plus)."""
        if a.state in ("arrived", "victim"): return
        d = s.flood.depth(a.x, a.z, t); cm = int(d * 100)
        why = (f"dehors dans {cm} cm d'eau" if a.state in ("walking", "blocked") and d >= OUT_DANGER else
               f"{cm} cm d'eau au rez-de-chaussée, ne peut pas monter à l'étage" if a.p["no_stairs"] and d >= IN_DANGER else None)
        if why and a.distress_since is None:
            a.distress_since = t; a.remember(t, f"{hm(t)} — en danger : {why}", "danger", "eau")
            await s.emit("agent.distress", a.id, reason=why, depth=round(d, 2))
        elif not why and a.distress_since is not None:
            a.distress_since = None; await s.emit("agent.distress_end", a.id)
        elif why and t - a.distress_since >= VICTIM_MIN:
            mins = round(t - a.distress_since, 1); a.state, a.path, a.distress_since = "victim", [], None
            await s.emit("agent.victim", a.id, reason=why, minutes=mins)

    async def house_damage(s, h, H, t):
        """Dégâts d'une maison : niveau atteint par la plus haute eau vue devant sa porte (ne redescend pas)."""
        d = s.flood.depth(*H["pos"], t)
        if d <= H["max"]: return
        H["max"] = d; lvl = next((n for th, n in DAMAGE if d >= th), None)
        if lvl != H["level"]:
            H["level"] = lvl; await s.emit("house.damage", None, house=h, agents=H["ids"], depth=round(d, 2), level=lvl)

    async def arrive(s, a, t):
        r = a.refuge
        if len(s.occupancy[r]) < REFUGES[r]["cap"]:
            s.occupancy[r].append(a.id); a.state, a.t_arrive = "arrived", t; a.x, a.z = s.streets.nodes[REFUGES[r]["node"]]
            ang = len(s.occupancy[r]) * 2.4; a.x += math.cos(ang) * (REFUGE_R - 2) * .7; a.z += math.sin(ang) * (REFUGE_R - 2) * .7
            a.remember(t, f"{hm(t)} — arrivé(e) au {REFUGES[r]['name']}", "arrivee", r)
            await s.emit("agent.arrive", a.id, refuge=r, t_depart=a.t_depart, load=len(s.occupancy[r]), cap=REFUGES[r]["cap"])
        else:
            a.known_full.add(r); a.remember(t, f"{hm(t)} — le {REFUGES[r]['name']} est plein", "refuge", r)
            await s.emit("refuge.full", a.id, refuge=r)
            await a.reroute(s, t, None, "refuge plein")

    async def advance(s, t_to):
        """Fait avancer le monde physique jusqu'à t_to (minutes), par pas de DT secondes. Identique dans les deux modes."""
        while s.t_phys < t_to - 1e-9:
            t = min(t_to, s.t_phys + DT / 60.0); s.t_evt = t
            while s.schedule and s.schedule[0][0] <= t:
                at, kind, arg = s.schedule.pop(0)
                if kind == "bulletin": await s.bulletin(arg["level"], at)
                elif kind == "rumor": await s.rumor(at)
                elif kind == "close_bridge": await s.close_bridge()
                elif kind == "network_down": await s.network_down()
            for a in s.agents.values():
                s.deliver(a, t)
                if a.verify_until is not None and t >= a.verify_until: await s.verify_done(a, t)
                if a.state == "preparing" and t >= a.t_ready and not await s.family_wait(a, t):
                    await a.depart(s, t, a.via_pref)
                    if a.state == "walking": await s.family_join(a, t)
                if a.state not in ("arrived", "sheltered", "victim"):       # à l'étage chez soi : plus exposé à l'eau de la rue
                    seen = s.look_around(a, t) if a.state in ("home", "preparing") else s.flood.depth(a.x, a.z, t)
                    for th in (.03, .1, .3):
                        if a.water_seen < th <= seen:
                            a.local_obs = True; a.confirm.add("observation"); a.dirty = True
                            a.remember(t, f"{hm(t)} — je vois de l'eau dans la rue ({int(seen * 100)} cm)", "eau", "observation")
                            await s.emit("agent.observe", a.id, what="eau", depth=round(seen, 2), where=[a.x, a.z])
                            if th >= .1: a.water_seen = seen; await s.observation_message(a, "eau")
                    a.water_seen = max(a.water_seen, seen)
                    if s.flood.depth(a.x, a.z, t) >= .05: a.wet += t - s.t_phys     # pas réel (peut être < DT en temps réel)
                await a.step(s, t, (t - s.t_phys) * 60.0)
                await s.distress(a, t)
            for h, H in s.houses.items(): await s.house_damage(h, H, t)
            s.t_phys = t
            tr = s.truth(t)
            if tr != s.truth_last: s.truth_last = tr; await s.emit("world.truth", None, **tr)
            if t >= s.next_tick:
                s.next_tick = t + max(1.0, s.clock.scale) / 60.0          # une position par seconde réelle (10 s simulées si TIME_SCALE=10)
                await s.emit("world.tick", None, level=s.flood.level(t), bridge=s.bridge_open,
                             agents=[{"id": a.id, "x": round(a.x, 2), "z": round(a.z, 2), "state": a.state, "wet": round(a.wet, 2),
                                      "depth": round(s.flood.depth(a.x, a.z, t), 2), "thinking": a.thinking, "distress": a.distress_since is not None} for a in s.agents.values()])
            if t >= s.next_flood:
                s.next_flood = t + 1.0; await s.emit("flood.update", None, level=s.flood.level(t), cells=s.flood.cells(t), rate=s.flood.rate(t), crue=s.flood.kind)
        s.t_evt = None
        for a in s.agents.values():                                      # voir partir ses contacts (preuve sociale)
            for c in a.p["contacts"]:
                o = s.agents.get(c)
                if o and o.t_depart is not None and c not in a.departed_contacts:
                    a.departed_contacts.add(c); a.dirty = True; a.remember(o.t_depart, f"{hm(o.t_depart)} — {o.name} est parti(e) vers un refuge", "voisin", c)

    # ---------- RAG ----------
    async def retrieve(s, a, focus):
        q = f"{focus} {a.p['situation']}"
        docs = await s.kb.search(q, 2); mems = s.kb.recall(q, a.mem, {"pont", "eau", "message", "souvenir", "verification"}, 3, set(a.p["contacts"]))
        meta = [{"id": d["doc"]["id"], "site": d["doc"]["site"], "topic": d["doc"]["topic"], "reliable": d["doc"]["reliable"], "score": round(d["score"], 4)} for d in docs]
        return {"query": q, "meta": meta, "strings": [f"[{d['doc']['id']}, {d['doc']['source']} {d['doc']['year']}] {d['doc']['text']}" for d in docs] + [f"(souvenir) {m}" for m in mems]}

    # ---------- commandes de Godot ----------
    async def command(s, c):
        t = c.get("type"); now = s.clock.now(); s.last_cmd = c
        await s.bus.send(ev_base("command.ack", None, {"cmd": t, "mode": s.mode, "paused": s.clock.paused}))
        if t == "inject_alert": await s.bulletin("evacuation", now)
        elif t == "inject_rumor": await s.rumor(now, c.get("targets"))
        elif t == "close_bridge": await s.close_bridge()
        elif t == "pause": s.clock.pause(); await s.emit("run.paused", None)
        elif t == "resume": s.clock.resume(); await s.emit("run.resumed", None)
        elif t == "network_down": await s.network_down()
        elif t == "set_speed" and s.clock.scale > 0:
            try: v = max(.5, min(SPEED_MAX, float(c.get("scale", 1))))
            except (TypeError, ValueError): v = s.clock.scale
            s.clock.set_scale(v); apply_settings({"vitesse": v}); await s.emit("run.speed", None, scale=v)   # gardée pour les runs suivants
        elif t in ("restart", "set_mode", "set_scenario", "set_profiles"): s.auto = True; s.stop.set()

    async def safe_think(s, a):
        try: await a.think(s, s.retrieve)
        except Exception as ex:
            import traceback; traceback.print_exc()
            await s.incident(a, "agent_exception", repr(ex)[:100])

    def wants(s, a, now):
        if a.state in ("arrived", "sheltered", "victim") or a.thinking: return False
        if a.kind != "rule" and now < a.last_think + LLM_MIN_GAP: return False   # un agent LLM regroupe les messages reçus dans la minute (file d'attente du LLM)
        return a.dirty or now >= a.next_think

    async def go(s, cmdq):
        tr = s.truth(T_START); s.truth_last = tr
        await s.emit("run.start", None, n_agents=len(s.agents), max_agents=MAX_AGENTS, lots=(custom().get("settings") or {}).get("lots") or [], overrides=custom().get("profiles") or {}, mode=s.mode, scenario=s.scenario, seed=s.seed, time_scale=s.clock.scale, t_start=T_START, t_end=T_END,
                     update_at=UPDATE_AT, rumor_at=RUMOR_AT, bridge_close_at=s.bridge_close_at, models={"big": s.llm.model_of("big"), "small": s.llm.model_of("small")},
                     llm_agents=s.llm_ids, apis=[b.info() for b in s.llm.backends], crue=s.flood.info(), crues={k: v["nom"] for k, v in CRUES.items()},
                     panne_reseau=PANNE_RESEAU, canaux=list(CANAUX), families=s.families, custom=bool(custom()),
                     map={"x0": X0, "x1": X1, "z0": Z0, "z1": Z1, "cell": CELL, "nx": NX, "nz": NZ, "river_z": RIVER_Z, "river_w": RIVER_W, "bed_y": BED_Y, "world_scale": WORLD_SCALE, "pass_x": PASS_X},
                     heightmap=heightmap(), streets=s.streets.export(),
                     refuges=[{"id": r, "name": R["name"], "cap": R["cap"], "x": s.streets.nodes[R["node"]][0], "z": s.streets.nodes[R["node"]][1], "radius": REFUGE_R} for r, R in REFUGES.items()],
                     agents=[{"id": a.id, "name": a.name, "kind": a.kind, "model": a.model, "situation": a.p["situation"], "x": a.x, "z": a.z,
                              "trust_off": a.p["trust_off"], "trust_nb": a.p["trust_nb"], "mob": a.p["mob"], "contacts": a.p["contacts"],
                              "threshold": a.p["threshold"], "prep": a.p["prep"], "no_stairs": a.p["no_stairs"], "needs_conf": a.p["needs_conf"], "relay": a.p["relay"],
                              "local_obs": a.p["local_obs"], "knows_detour": a.p["knows_detour"], "verifier": a.p["verifier"], "engine": a.kind,
                              "famille": a.p["famille"], "family": sorted(a.family), "dependants": a.p.get("dependants", []), "canaux": a.p.get("canaux", [])}
                             for a in s.agents.values()])
        await s.emit("world.truth", None, **tr)
        for a in s.agents.values(): await a.publish_belief(s)
        bg = [asyncio.create_task(s.metrics())]
        async def cmds():
            while not s.stop.is_set():
                try: c = cmdq.get_nowait()
                except asyncio.QueueEmpty: await asyncio.sleep(.1); continue
                await s.command(c)
        bg.append(asyncio.create_task(cmds()))
        if s.mode == "sync":
            step = 0
            while not s.stop.is_set() and s.clock.fixed < T_END:
                if s.clock.paused: await asyncio.sleep(.1); continue
                t0 = time.monotonic(); now = s.clock.fixed
                todo = [a for a in s.agents.values() if s.wants(a, now)]
                if todo:                                                   # barrière : le plus lent fixe la durée du pas
                    tick = asyncio.ensure_future(asyncio.gather(*(s.safe_think(a) for a in todo)))
                    stopw = asyncio.ensure_future(s.stop.wait())
                    await asyncio.wait({tick, stopw}, return_when=asyncio.FIRST_COMPLETED); stopw.cancel()
                    if not tick.done(): tick.cancel(); await asyncio.gather(tick, return_exceptions=True); break
                s.clock.fixed = now + 0.5; await s.advance(s.clock.fixed)
                await s.emit("tick", None, step=step, thinkers=len(todo), wall_ms=int((time.monotonic() - t0) * 1000)); step += 1
                if SCALE > 0: await asyncio.sleep(max(0, 30 / s.clock.scale - (time.monotonic() - t0)))
                else: await asyncio.sleep(0)
        else:
            async def physics():
                while not s.stop.is_set():
                    now = s.clock.now()
                    if now >= T_END: await s.advance(T_END); s.stop.set(); break
                    await s.advance(now); await asyncio.sleep(.1)
            async def life(a):
                await asyncio.sleep(a.rng.uniform(0, .5))
                while not s.stop.is_set():
                    if s.wants(a, s.clock.now()) and not s.clock.paused: await s.safe_think(a)
                    await asyncio.sleep(.1)
            tasks = [asyncio.create_task(physics())] + [asyncio.create_task(life(a)) for a in s.agents.values()]
            await s.stop.wait()
            for x in tasks: x.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        fin = s.ind.snap({})
        summary = {**s.stats.snap(), **s.kb.snap(), "indicators": fin, "scenario": s.scenario, "mode": s.mode, "seed": s.seed, "n_agents": len(s.agents), "llm_agents": s.llm_ids,
                   "update_at": hm(UPDATE_AT), "rumor_at": RUMOR_AT, "crue": s.flood.kind, "panne_reseau": PANNE_RESEAU,
                   "agents": [{"id": a.id, "state": a.state, "refuge": a.refuge, "t_depart": a.t_depart, "t_arrive": a.t_arrive, "wet": round(a.wet, 2), "via": a.via} for a in s.agents.values()]}
        await s.emit("run.end", None, **summary)
        for b in bg: b.cancel()
        await s.llm.close(); s.file.close()
        return summary

    async def metrics(s):
        while not s.stop.is_set():
            await asyncio.sleep(3)
            await s.emit("metrics", None, **s.stats.snap(), **s.kb.snap(), llm_inflight=s.llm.inflight)

async def replay(bus):   # E3 : rejeu à l'identique, sans aucun appel LLM
    evs = [json.loads(l) for l in open(REPLAY, encoding="utf-8")]; prev = evs[0]["t_sim"]
    for ev in evs:
        gap = max(0, ev["t_sim"] - prev) if ev["t_sim"] >= 0 else 0
        await asyncio.sleep(gap * 60 / max(SCALE, 1)); prev = max(prev, ev["t_sim"])
        ev["t_proc"] = int(time.time() * 1000); ev["replay"] = True; await bus.send(ev)
    print("rejeu terminé :", REPLAY, flush=True)

async def main():
    global MODE, SCENARIO
    bus = Bus(); await bus.start()
    if REPLAY: await replay(bus); return
    import httpx
    apply_settings(custom().get("settings", {}))
    backends = backends_from_env(env, OLLAMA)
    local = any(b.name == "local" and b.provider == "ollama" for b in backends)   # embeddings du RAG : seulement si Ollama tourne en local
    print("API LLM :", ", ".join(f"{b.name} ({b.provider}, {b.models['big']} / {b.models['small']}, {b.parallel} en parallèle)" for b in backends) or "aucune", flush=True)
    kb = RAG(DOCS, mode=RAG_MODE if local else "lexical", ollama=OLLAMA, model=EMBED_MODEL, http=httpx.AsyncClient()); await kb.build()
    print("RAG :", kb.warn or f"{'hybride' if kb.emb else 'lexical'}, {len(DOCS)} documents", flush=True)
    if LLM_IDS: await LLM(backends, TIMEOUT, TEMP, Stats(), None).warmup()
    cmdq = asyncio.Queue()
    if KAFKA:
        from aiokafka import AIOKafkaConsumer
        cons = AIOKafkaConsumer("alerte.commands", bootstrap_servers=KAFKA, group_id="sim", auto_offset_reset="latest", metadata_max_age_ms=5000); await cons.start()
        async def feed():
            async for m in cons:
                try: await cmdq.put(json.loads(m.value))
                except Exception: pass
        asyncio.create_task(feed())
    if BATCH:                                                            # E2 : lot de runs enchaînés, graines successives
        out = []
        for i in range(BATCH):
            w = World(bus, kb, SEED + i, MODE, SCENARIO); r = await w.go(cmdq); ind = r["indicators"]
            print(f"run {w.run} graine {SEED + i} : {ind['safe']}/{len(w.agents)} à l'abri, départ moyen +{ind['depart_delay_avg']} min, contexte commun moyen {ind['common_context_avg']} (final {ind['common_context']}), appels LLM {r['calls_total']}", flush=True)
            out.append({"run_id": w.run, **{k: r[k] for k in ("seed", "mode", "scenario", "calls_total", "tokens_in", "tokens_out", "incidents")}, **{k: v for k, v in ind.items() if not isinstance(v, dict)}})
        with open(f"{RUNS}/batch_{SCENARIO}_{MODE}_{'llm' if LLM_IDS else 'regles'}_{int(time.time())}.json", "w", encoding="utf-8") as f: json.dump(out, f, ensure_ascii=False, indent=1)
        return
    while True:
        while not cmdq.empty(): cmdq.get_nowait()
        w = World(bus, kb, SEED, MODE, SCENARIO); await w.go(cmdq)
        c = w.last_cmd if w.auto else {}
        while not w.auto:                                                # fin du run : attend « relancer » depuis Godot
            c = await cmdq.get()
            await bus.send(ev_base("command.ack", None, {"cmd": c.get("type"), "mode": MODE}))
            if c.get("type") in ("restart", "set_mode", "set_scenario", "set_profiles"): break
        if c.get("type") == "set_mode" and c.get("mode") in ("sync", "async"): MODE = c["mode"]
        if c.get("type") == "set_scenario" and c.get("scenario") in PEAK: SCENARIO = c["scenario"]
        if c.get("type") == "set_profiles": save_custom(c)

def save_custom(c):
    """Commande de l'interface de gestion : enregistre les réglages (villageois.json) et les applique au run suivant.
    reset=true efface tout et revient aux profils de la fiche."""
    if c.get("reset"):
        try: os.remove(VILLAGEOIS)
        except OSError: pass
        apply_settings(DEFAULTS); return
    data = {"settings": c.get("settings") or {}, "profiles": c.get("profiles") or {}}
    try:
        with open(VILLAGEOIS, "w", encoding="utf-8") as f: json.dump(data, f, ensure_ascii=False, indent=1)
    except OSError as ex: print("villageois.json non enregistré :", ex, flush=True)
    apply_settings(data["settings"])

if __name__ == "__main__":
    asyncio.run(main())

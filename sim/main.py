"""ALERTE 3D — décisions humaines et évacuation collective pendant une crue (fiche ALERTE, thématique ESSAIM).
Simulation Python : monde (crue, rues, refuges) + N_AGENTS habitants (les 12 de la fiche, dont 4 LLM via Ollama, puis des voisins générés). Tous les événements vont dans Kafka
(topic alerte.events) et dans data/runs/<run_id>.jsonl. La passerelle (gateway/) les relaie à Godot.
Modes : async (chacun à son rythme, latence réelle du LLM comprise) / sync (pas de 30 s simulées, on attend le plus lent)."""
import asyncio, json, math, os, random, time, uuid
from agents import Agent, SEV, who
from corpus import DOCS
from indicators import Indicators
from llm import LLM, Stats
from profiles import population, extra_llm_ids
from rag import RAG
from world import Flood, Streets, REFUGES, REFUGE_R, BRIDGE, NODES, heightmap, hm, t_of, ekey, speed_factor, X0, X1, Z0, Z1, CELL, NX, NZ, RIVER_Z, RIVER_W, BED_Y, S as WORLD_SCALE, PASS_X, EXTENDED

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
    def __init__(s, mode, scale): s.mode, s.scale = mode, scale; s.fixed = T_START; s.start = time.monotonic(); s.paused_at = None; s.lost = 0.0
    def now(s):
        if s.mode == "sync": return s.fixed
        ref = s.paused_at if s.paused_at is not None else time.monotonic()
        return T_START + (ref - s.start - s.lost) * s.scale / 60.0
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
        s.llm = LLM(PROVIDER, API_URL, API_KEY, TIMEOUT, LLM_PAR, TEMP, s.stats, s.incident); s.think_every = THINK_EVERY
        s.streets = Streets(); s.flood = Flood(PEAK.get(scenario, .75))
        s.bridge_open = True; s.bridge_close_at = t_of("09:20") if scenario == "pont_ferme" else None
        s.profiles = population(N, set(LLM_IDS), s.rng, big_ids=("A04", "A05", *EXTRA_BIG))
        s.agents = {}
        for p in s.profiles:
            home = s.streets.add_home(p["id"], *p["pos"])
            a = Agent(p, home, random.Random(s.rng.random())); a.model = {"big": BIG, "small": SMALL}.get(a.kind); s.agents[a.id] = a
            if a.kind != "rule": a.last_think = T_START - LLM_MIN_GAP + a.rng.uniform(0, LLM_MIN_GAP)   # premières réflexions LLM étalées, pas toutes à 09:00
        s.occupancy = {r: [] for r in REFUGES}; s.msg_id = 0; s.truth_last = None; s.t_phys = T_START; s.next_tick = T_START; s.next_flood = T_START
        s.schedule = [(T_START, "bulletin", {"level": "vigilance"}), (UPDATE_AT, "bulletin", {"level": "evacuation"})]
        if RUMOR_AT and RUMOR_AT != "none": s.schedule.append((t_of(RUMOR_AT), "rumor", {}))
        if s.bridge_close_at: s.schedule.append((s.bridge_close_at, "close_bridge", {}))
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
        a.sent_keys.add(key); s.msg_id += 1; mid = f"m{s.msg_id}"; to = [c for c in (to or a.p["contacts"]) if c in s.agents]
        for c in to:
            delay = s.rng.uniform(.3, 1.5) if s.agents[c].state != "walking" else s.rng.uniform(1, 3)     # on lit moins vite en marchant
            s.agents[c].inbox.append({"id": mid, "src": a.id, "origin": origin, "hops": hops, "text": text, "claims": claims, "kind": kind,
                                      "level": claims.get("level", "alerte"), "t_sent": now, "t_deliver": now + delay, "parent": parent})
        a.remember(now, f"{hm(now)} — j'ai écrit à {', '.join(to)} : « {text} »", "message", *[c for c in to])
        await s.emit("agent.say", a.id, to=to, text=text, kind=kind, origin=origin, hops=hops, msg_id=mid, parent=parent, claims=claims, decider=a.kind, model=a.model)

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
        return {"bridge": "ouvert" if s.bridge_open else "ferme", "danger": t >= t_of("09:10"), "refuges": acc}

    async def bulletin(s, level, t):
        tr = s.truth(t)
        if level == "vigilance": txt = "Mairie, 09:00 : vigilance crue. La rivière va monter ce matin. Préparez-vous à partir vers un refuge."; cl = {"danger": False, "level": "vigilance"}
        else:
            txt = (f"Mairie, {hm(t)} : évacuation des Quais. Le Pont Central est {'OUVERT' if tr['bridge'] == 'ouvert' else 'FERMÉ, prenez le Passage des Hauteurs'}. "
                   f"Refuges : Gymnase des Hauteurs (rive nord) et École des Tilleuls (7 places)."
                   + (" Ouest du quartier : Église Saint-Roch. Est du quartier : Collège du Plateau, ou Salle des Fêtes du Moulin." if EXTENDED else ""))
            cl = {"danger": True, "bridge": tr["bridge"], "refuge": "gymnase", "level": "evacuation"}
        s.msg_id += 1; mid = f"m{s.msg_id}"; who_got = []
        for a in s.agents.values():
            if a.rng.random() < .1 and level == "vigilance": continue           # certains n'entendent pas le premier bulletin
            a.inbox.append({"id": mid, "src": "mairie", "origin": "mairie", "hops": 0, "text": txt, "claims": cl, "kind": "officiel", "level": level,
                            "t_sent": t, "t_deliver": t + a.rng.uniform(0, 3), "verified": True, "parent": None}); who_got.append(a.id)
        await s.emit("official.bulletin", None, level=level, text=txt, claims=cl, msg_id=mid, to=who_got)

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
        if not s.mairie_knows(t) and s.bridge_open:
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
            for a in s.agents.values():
                s.deliver(a, t)
                if a.verify_until is not None and t >= a.verify_until: await s.verify_done(a, t)
                if a.state == "preparing" and t >= a.t_ready: await a.depart(s, t, a.via_pref)
                if a.state not in ("arrived", "sheltered"):                 # à l'étage chez soi : plus exposé à l'eau de la rue
                    seen = s.look_around(a, t) if a.state in ("home", "preparing") else s.flood.depth(a.x, a.z, t)
                    for th in (.03, .1, .3):
                        if a.water_seen < th <= seen:
                            a.local_obs = True; a.confirm.add("observation"); a.dirty = True
                            a.remember(t, f"{hm(t)} — je vois de l'eau dans la rue ({int(seen * 100)} cm)", "eau", "observation")
                            await s.emit("agent.observe", a.id, what="eau", depth=round(seen, 2), where=[a.x, a.z])
                            if th >= .1: a.water_seen = seen; await s.observation_message(a, "eau")
                    a.water_seen = max(a.water_seen, seen)
                    if s.flood.depth(a.x, a.z, t) >= .05: a.wet += DT / 60.0
                await a.step(s, t, (t - s.t_phys) * 60.0)
            s.t_phys = t
            tr = s.truth(t)
            if tr != s.truth_last: s.truth_last = tr; await s.emit("world.truth", None, **tr)
            if t >= s.next_tick:
                s.next_tick = t + max(1.0, SCALE) / 60.0          # une position par seconde réelle (10 s simulées si TIME_SCALE=10)
                await s.emit("world.tick", None, level=s.flood.level(t), bridge=s.bridge_open,
                             agents=[{"id": a.id, "x": round(a.x, 2), "z": round(a.z, 2), "state": a.state, "wet": round(a.wet, 2),
                                      "depth": round(s.flood.depth(a.x, a.z, t), 2), "thinking": a.thinking} for a in s.agents.values()])
            if t >= s.next_flood:
                s.next_flood = t + 1.0; await s.emit("flood.update", None, level=s.flood.level(t), cells=s.flood.cells(t))
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
        elif t in ("restart", "set_mode", "set_scenario"): s.auto = True; s.stop.set()

    async def safe_think(s, a):
        try: await a.think(s, s.retrieve)
        except Exception as ex:
            import traceback; traceback.print_exc()
            await s.incident(a, "agent_exception", repr(ex)[:100])

    def wants(s, a, now):
        if a.state in ("arrived", "sheltered") or a.thinking: return False
        if a.kind != "rule" and now < a.last_think + LLM_MIN_GAP: return False   # un agent LLM regroupe les messages reçus dans la minute (file d'attente du LLM)
        return a.dirty or now >= a.next_think

    async def go(s, cmdq):
        tr = s.truth(T_START); s.truth_last = tr
        await s.emit("run.start", None, n_agents=N, mode=s.mode, scenario=s.scenario, seed=s.seed, time_scale=SCALE, t_start=T_START, t_end=T_END,
                     update_at=UPDATE_AT, rumor_at=RUMOR_AT, bridge_close_at=s.bridge_close_at, models={"big": BIG, "small": SMALL}, llm_agents=LLM_IDS,
                     map={"x0": X0, "x1": X1, "z0": Z0, "z1": Z1, "cell": CELL, "nx": NX, "nz": NZ, "river_z": RIVER_Z, "river_w": RIVER_W, "bed_y": BED_Y, "world_scale": WORLD_SCALE, "pass_x": PASS_X},
                     heightmap=heightmap(), streets=s.streets.export(),
                     refuges=[{"id": r, "name": R["name"], "cap": R["cap"], "x": s.streets.nodes[R["node"]][0], "z": s.streets.nodes[R["node"]][1], "radius": REFUGE_R} for r, R in REFUGES.items()],
                     agents=[{"id": a.id, "name": a.name, "kind": a.kind, "model": a.model, "situation": a.p["situation"], "x": a.x, "z": a.z,
                              "trust_off": a.p["trust_off"], "trust_nb": a.p["trust_nb"], "mob": a.p["mob"], "contacts": a.p["contacts"],
                              "threshold": a.p["threshold"], "prep": a.p["prep"], "no_stairs": a.p["no_stairs"]} for a in s.agents.values()])
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
                if SCALE > 0: await asyncio.sleep(max(0, 30 / SCALE - (time.monotonic() - t0)))
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
        summary = {**s.stats.snap(), **s.kb.snap(), "indicators": fin, "scenario": s.scenario, "mode": s.mode, "seed": s.seed, "n_agents": N, "llm_agents": LLM_IDS,
                   "update_at": hm(UPDATE_AT), "rumor_at": RUMOR_AT,
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
    kb = RAG(DOCS, mode=RAG_MODE if PROVIDER == "ollama" else "lexical", ollama=OLLAMA, model=EMBED_MODEL, http=httpx.AsyncClient()); await kb.build()
    print("RAG :", kb.warn or f"{'hybride' if kb.emb else 'lexical'}, {len(DOCS)} documents", flush=True)
    if LLM_IDS: await LLM(PROVIDER, API_URL, API_KEY, TIMEOUT, 1, TEMP, Stats(), None).warmup([BIG, SMALL])
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
            print(f"run {w.run} graine {SEED + i} : {ind['safe']}/{N} à l'abri, départ moyen +{ind['depart_delay_avg']} min, contexte commun moyen {ind['common_context_avg']} (final {ind['common_context']}), appels LLM {r['calls_total']}", flush=True)
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
            if c.get("type") in ("restart", "set_mode", "set_scenario"): break
        if c.get("type") == "set_mode" and c.get("mode") in ("sync", "async"): MODE = c["mode"]
        if c.get("type") == "set_scenario" and c.get("scenario") in PEAK: SCENARIO = c["scenario"]

if __name__ == "__main__":
    asyncio.run(main())

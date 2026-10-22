"""Passerelle : Kafka -> WebSocket (Godot), et Godot -> Kafka (commandes).
- valide chaque événement contre le contrat alerte.event/1 (F4) ; un événement non conforme part dans alerte.dlq sans rien arrêter ;
- calcule les indicateurs en flux (indicators.py, le même code que la simulation) et les publie dans alerte.indicators (B2) ;
- chaque indicateur garde l'événement brut qui l'a produit (F6) et le décalage événement -> indicateur (C7)."""
import asyncio, json, os, time
import websockets
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from indicators import Indicators

KAFKA = os.getenv("KAFKA", "kafka:9092")
SCHEMAS = ("alerte.event/1",)
REQ = ("schema", "run_id", "seq", "type", "t_sim", "t_proc", "data")
DATA = {                                         # champs obligatoires de data, par type (docs/evenements.md)
    "run.start": ("n_agents", "agents", "refuges", "streets", "heightmap", "map"), "world.truth": ("bridge", "danger", "refuges"),
    "world.tick": ("level", "agents"), "flood.update": ("level", "cells"), "official.bulletin": ("level", "text"),
    "rumor": ("text", "claim", "targets"), "bridge.closed": (), "agent.think": ("action", "why", "decider"),
    "agent.belief": ("bridge", "refuge", "danger", "risk"), "agent.say": ("to", "text", "kind", "origin", "msg_id"),
    "agent.prepare": ("ready_at",), "agent.depart": ("refuge", "via", "path"), "agent.reroute": ("reason", "path"),
    "agent.arrive": ("refuge",), "agent.blocked": ("reason",), "agent.shelter": ("reason",), "agent.observe": ("what",), "agent.verify": ("until",),
    "agent.verify_result": ("result",), "refuge.full": ("refuge",), "llm.incident": ("kind",), "metrics": (), "tick": ("step",),
    "run.end": ("indicators",), "run.paused": (), "run.resumed": (), "command.ack": ("cmd",),
}
clients, buf, prod = set(), [], None
ind = Indicators()
last_tick = {}

def valid(ev):
    if ev.get("schema") not in SCHEMAS or not all(k in ev for k in REQ) or not isinstance(ev["data"], dict): return False
    if ev["type"] not in DATA: return False
    return all(k in ev["data"] for k in DATA[ev["type"]])

async def broadcast(msg):
    txt = json.dumps(msg, ensure_ascii=False)
    for c in list(clients):
        try: await c.send(txt)
        except Exception: clients.discard(c)

async def consume():
    c = AIOKafkaConsumer("alerte.events", bootstrap_servers=KAFKA, group_id=None, auto_offset_reset="earliest", metadata_max_age_ms=5000)
    await c.start()
    async for m in c:
        try:
            ev = json.loads(m.value); assert valid(ev)
        except Exception:
            await prod.send("alerte.dlq", m.value); continue
        if ev["type"] == "run.start": buf.clear(); last_tick.clear()
        if ev["type"] in ("world.tick", "flood.update", "metrics", "tick"): last_tick[ev["type"]] = ev   # un nouveau client n'a besoin que du dernier
        elif ev["type"] != "command.ack": buf.append(ev); del buf[:-4000]
        await broadcast(ev)
        out = ind.feed(ev)
        if out is not None:
            msg = {"schema": ev["schema"], "type": "indicator", "run_id": ev["run_id"], "t_sim": ev["t_sim"], "t_proc": int(time.time() * 1000), "agent": None,
                   "data": {**out, "source_seq": ev["seq"], "source_type": ev["type"], "lag_ms": int(time.time() * 1000) - ev["t_proc"]}}
            buf.append(msg); last_tick["indicator"] = msg
            await broadcast(msg); await prod.send("alerte.indicators", json.dumps(msg, ensure_ascii=False).encode(), key=str(ev["run_id"]).encode())

async def handler(ws):
    clients.add(ws)
    try:
        for ev in list(buf) + [v for k, v in last_tick.items() if k != "indicator"]: await ws.send(json.dumps(ev, ensure_ascii=False))
        async for msg in ws: await prod.send("alerte.commands", msg.encode() if isinstance(msg, str) else msg)
    except Exception: pass
    finally: clients.discard(ws)

async def main():
    global prod
    prod = AIOKafkaProducer(bootstrap_servers=KAFKA); await prod.start()
    async with websockets.serve(handler, "0.0.0.0", 8765, max_size=8_000_000):
        print("passerelle prête : ws://0.0.0.0:8765", flush=True)
        await consume()

asyncio.run(main())

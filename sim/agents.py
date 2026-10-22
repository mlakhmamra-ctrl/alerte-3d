"""Habitants de Rive-Claire. Un même cycle pour tous (fiche ALERTE) :
observer -> interpréter -> agir -> communiquer -> mettre à jour la mémoire.
- Agents à règles : seuils explicites (profiles.py), aucune génération de texte libre.
- Agents LLM : le LLM interprète, justifie et rédige le message ; le code garde la main sur ce qui est possible
  (contacts, temps, déplacements). Si le LLM échoue, l'agent applique ses règles pour ce tour (A4)."""
import math
from world import REFUGES, REFUGE_R, BRIDGE, STAIRS, ekey, hm, via_of, speed_factor

WALK = 1.2                                   # m/s, vitesse de référence de la fiche
SAFE_DEPTH = 0.2                             # m : au-delà, un habitant au sec refuse d'entrer dans la rue (corpus crue-03)
DANGER_DEPTH = 0.3                           # m : surpris par l'eau qui monte, il quitte la rue pour le bâtiment le plus proche
SEV = {"vigilance": 30, "alerte": 65, "evacuation": 95}
ACTIONS = ["rester", "preparer_depart", "partir", "changer_itineraire", "relayer", "verifier", "corriger"]

def who(src, agents):
    if src == "mairie": return "la mairie (bulletin officiel)"
    if src == "anonyme": return "un message anonyme sans source"
    a = agents.get(src); return f"{a.name} ({src})" if a else src

class Agent:
    def __init__(s, p, home_node, rng):
        s.p = p; s.id, s.name = p["id"], p["name"]; s.kind = p["engine"]; s.model = None; s.rng = rng
        s.x, s.z = float(p["pos"][0]), float(p["pos"][1]); s.home = home_node; s.node = home_node
        s.mem = [(540.0, m, {"souvenir"}) for m in p.get("memory", [])]
        s.inbox = []; s.seen_msgs = set(); s.sent_keys = set()
        # état mental (fiche) : risque 0-100, confiance 1-5, refuge préféré, certitude
        s.risk = 0; s.bridge = "inconnu"; s.bridge_cert = 0.0; s.danger = False; s.refuge = "aucun"; s.certainty = 0.0
        s.confirm = set(); s.local_obs = False; s.known_blocked = set(); s.water_seen = 0.0
        s.state = "home"; s.t_ready = None; s.t_depart = None; s.t_arrive = None; s.path = []; s.seg = 0; s.via = "aucun"
        s.wet = 0.0; s.why = "chez soi, ne sait pas encore qu'une crue arrive"; s.last_action = "rester"; s.last_retrieved = []
        s.dirty = True; s.next_think = 540.0; s.last_think = -1e9; s.thinking = False; s.verify_until = None; s.verify_topic = None
        s.departed_contacts = set(); s.blocked_since = None; s.going_home = False
        s.inbox_read = []; s.checked = set(); s.known_full = set(); s.bridge_seen = False; s.via_pref = None

    # ---------- perception ----------
    def remember(s, t, text, *tags): s.mem.append((t, text, set(tags))); del s.mem[:-60]

    def trust_of(s, src):
        if src == "mairie": return s.p["trust_off"]
        if src == "anonyme": return 1
        return s.p["trust_nb"]

    # ---------- interprétation à règles (aussi le repli des agents LLM) ----------
    def rule_risk(s, now, w):
        off = max([SEV.get(m["level"], 0) * s.p["trust_off"] / 5 for m in s.inbox_read if m["src"] == "mairie"] or [0])
        msg = max([SEV.get(m["level"], 0) * s.trust_of(m["src"]) / 5 for m in s.inbox_read if m["src"] != "mairie"] or [0])
        obs = 90 if s.water_seen >= .3 else 60 if s.water_seen >= .1 else 35 if s.water_seen >= .03 else 0
        soc = min(40, 15 * len(s.departed_contacts)) * s.p["trust_nb"] / 5
        parts = sorted([off, msg, obs, soc], reverse=True)
        return int(min(100, parts[0] + .3 * sum(parts[1:])))

    def rule_bridge_belief(s):
        """Dernière information crue sur le pont : la plus récente parmi celles dont la source est assez fiable."""
        best = None
        for m in s.inbox_read:
            b = m["claims"].get("bridge")
            if b not in ("ouvert", "ferme"): continue
            tr = s.trust_of(m["src"])
            if m["src"] == "mairie" or m.get("verified") or tr >= 3:
                if best is None or m["t_deliver"] >= best[1]: best = (b, m["t_deliver"], tr)
        return (best[0], min(1.0, best[2] / 5)) if best else (s.bridge, s.bridge_cert)

    def ready_to_leave(s):
        if s.risk < s.p["threshold"]: return False
        if len(s.confirm) < s.p["needs_conf"]: return False
        if s.p["local_obs"] and not (s.local_obs or any(m.get("verified") or m["src"] == "mairie" and SEV.get(m["level"], 0) >= 90 for m in s.inbox_read)): return False
        return True

    def choose_refuge_and_route(s, w, refuge=None, via=None):
        """Choisit refuge et chemin d'après ce que l'agent CROIT (pont, rues inondées qu'il connaît), pas d'après la vérité."""
        blocked = set(s.known_blocked)
        if s.bridge == "ferme": blocked.add(ekey(*BRIDGE))
        if via == "passage_hauteurs": blocked.add(ekey(*BRIDGE))
        if via == "pont_central": blocked.add(ekey(*STAIRS))
        avoid = s.p["no_stairs"]
        prefs = [refuge] if refuge in REFUGES else []
        if s.water_seen >= .2: prefs += sorted(REFUGES, key=lambda r: w.streets.path(s.node, REFUGES[r]["node"], blocked, avoid)[1])
        prefs += [s.p.get("zone", "gymnase"), "gymnase", "ecole"]      # refuge désigné par la mairie (le gymnase pour les habitants de la fiche)
        prefs += sorted(REFUGES, key=lambda r: w.streets.path(s.node, REFUGES[r]["node"], blocked, avoid)[1])   # puis les autres, du plus proche au plus loin
        if s.p["knows_detour"] and s.bridge != "ouvert" and via is None: blocked.add(ekey(*BRIDGE))
        for r in dict.fromkeys(prefs):
            if r in s.known_full: continue
            p, d = w.streets.path(s.node, REFUGES[r]["node"], blocked, avoid)
            if p is None and s.p["knows_detour"]: p, d = w.streets.path(s.node, REFUGES[r]["node"], set(s.known_blocked) | ({ekey(*BRIDGE)} if s.bridge == "ferme" else set()), avoid)
            if p: return r, p
        return None, None

    async def rule_decide(s, w, now):
        s.risk = s.rule_risk(now, w)
        b, c = s.rule_bridge_belief()
        if b != s.bridge: s.bridge, s.bridge_cert = b, c
        if s.risk >= 60 or any(m["claims"].get("danger") for m in s.inbox_read if s.trust_of(m["src"]) >= 3): s.danger = True
        # vérifier une information non officielle sur le pont (agents vérificateurs)
        unver = [m for m in s.inbox_read if m["claims"].get("bridge") and m["src"] != "mairie" and not m.get("verified") and m["id"] not in s.checked]
        if s.p["verifier"] and unver and s.state in ("home", "preparing") and s.verify_until is None:
            s.checked.update(m["id"] for m in unver)
            return dict(action="verifier", justification=f"règle : vérifie auprès du poste municipal le message de {who(unver[-1]['src'], w.agents)} sur le pont", message="")
        if s.state == "home" and s.ready_to_leave():
            r, p = s.choose_refuge_and_route(w)
            if r: s.refuge = r
            return dict(action="preparer_depart" if s.p["prep"] > 0 else "partir",
                        justification=f"règle : risque perçu {s.risk} ≥ seuil {s.p['threshold']}, {len(s.confirm)} confirmation(s) ; refuge visé : {REFUGES[r]['name'] if r else 'aucun'}", message="")
        if s.state == "home":
            r, _ = s.choose_refuge_and_route(w); s.refuge = r or "aucun"
            miss = "seuil non atteint" if s.risk < s.p["threshold"] else "attend une confirmation" if len(s.confirm) < s.p["needs_conf"] else "attend une observation locale"
            return dict(action="rester", justification=f"règle : risque {s.risk}/seuil {s.p['threshold']} — {miss}", message="")
        return dict(action="rester", justification=s.why, message="")

    # ---------- LLM ----------
    def system_prompt(s, w):
        p = s.p
        return (f"Tu es {s.name}, habitant(e) du quartier des Quais à Rive-Claire (monde fictif). {p['situation']} "
                f"Ta confiance dans les alertes officielles est de {p['trust_off']}/5, dans tes voisins de {p['trust_nb']}/5. "
                f"Tu te déplaces à {round(p['mob'] * WALK, 2)} m/s. {'Tu ne peux pas prendre d’escaliers. ' if p['no_stairs'] else ''}"
                f"{'Tu connais bien le détour par le Passage des Hauteurs. ' if p['knows_detour'] else ''}"
                f"Tu parles comme une vraie personne du quartier, en français, jamais comme un assistant. Tu ne sais que ce que tu as vu, lu ou entendu. "
                f"Réponds uniquement en JSON.")

    def schema(s, contacts):
        return {"type": "object", "properties": {
            "croyance": {"type": "object", "properties": {"pont_central": {"type": "string", "enum": ["ouvert", "ferme", "inconnu"]},
                                                          "refuge": {"type": "string", "enum": [*REFUGES, "aucun"]},
                                                          "danger_imminent": {"type": "boolean"}}, "required": ["pont_central", "refuge", "danger_imminent"]},
            "risque": {"type": "integer", "minimum": 0, "maximum": 100},
            "action": {"type": "string", "enum": ACTIONS},
            "itineraire": {"type": "string", "enum": ["pont_central", "passage_hauteurs", "aucun"]},
            "justification": {"type": "string"}, "message": {"type": "string"}},
            "required": ["croyance", "risque", "action", "itineraire", "justification", "message"]}

    async def llm_decide(s, w, now, retrieve):
        focus = "pont central fermé ouvert rumeur vérifier" if any(m["claims"].get("bridge") for m in s.inbox_read[-3:]) else "crue évacuation refuge itinéraire"
        r = await retrieve(s, focus)
        msgs = "\n".join(f"- {hm(m['t_deliver'])}, de {who(m['src'], w.agents)}" + (f" (relayé, source d'origine : {who(m['origin'], w.agents)})" if m["origin"] != m["src"] else "")
                         + f" : « {m['text']} »" + (" [vérifié]" if m.get("verified") else "") for m in s.inbox_read[-6:]) or "- (aucun)"
        here = f"{int(s.water_seen * 100)} cm d'eau devant chez toi" if s.water_seen >= .05 else "de l'eau visible dans la rue" if s.water_seen > 0 else "pas d'eau visible près de toi"
        state = {"home": "chez toi", "preparing": f"tu prépares ton départ (prêt à {hm(s.t_ready or now)})", "walking": f"en route vers {REFUGES.get(s.refuge, {}).get('name', '?')} par {s.via}",
                 "blocked": "bloqué(e) : le chemin prévu est coupé"}.get(s.state, s.state)
        contacts = ", ".join(f"{w.agents[c].name} ({c}, {'parti(e)' if c in s.departed_contacts else 'pas encore parti(e)'})" for c in s.p["contacts"] if c in w.agents)
        user = (f"Il est {hm(now)}. Tu es {state}. Observation : {here}.\n"
                f"Ce que tu crois : pont central {s.bridge}, refuge visé {s.refuge}, danger imminent {'oui' if s.danger else 'non'}.\n"
                f"Messages reçus (les plus récents) :\n{msgs}\n"
                f"Tes contacts directs : {contacts}.\nCe que tu retrouves (documents du quartier et souvenirs) :\n" + "\n".join("- " + x for x in r["strings"]) + "\n"
                f"Refuges : " + ", ".join(f"{R['name']} ({k}, rive {R['bank']}, {R['cap']} places{', ' + R['note'] if R['note'] else ''})" for k, R in REFUGES.items()) + ".\n"
                f"Actions : rester, preparer_depart, partir, changer_itineraire, relayer (transmettre à tes contacts), verifier (appeler le poste municipal, 3 min), corriger (signaler une erreur à tes contacts).\n"
                f"Réponds : croyance (pont_central, refuge, danger_imminent), risque (0-100), action, itineraire, justification (15 mots au plus, en citant ce qui t'a convaincu), "
                f"message (ce que tu écris à tes contacts, une phrase courte avec la source et l'heure, ou \"\" si tu n'écris pas).")
        o = await w.llm.ask(s, s.model, s.system_prompt(w), user, s.schema(s.p["contacts"]))
        if o is None: return None, r
        try:
            c = o["croyance"]; act = o["action"]
            assert act in ACTIONS and c["pont_central"] in ("ouvert", "ferme", "inconnu") and (c["refuge"] in REFUGES or c["refuge"] == "aucun")
            out = dict(action=act, justification=str(o.get("justification", ""))[:240], message=str(o.get("message", ""))[:300],
                       risk=int(o["risque"]), bridge=c["pont_central"], refuge=c["refuge"], danger=bool(c["danger_imminent"]),
                       via=o.get("itineraire") if o.get("itineraire") in ("pont_central", "passage_hauteurs") else None, latency_ms=o.get("_latency_ms"))
        except Exception as ex:
            await w.incident(s, "invalid_output", f"{ex!r} {str(o)[:100]}"); return None, r
        if out["bridge"] != "inconnu" and not any(m["claims"].get("bridge") for m in s.inbox_read) and not s.bridge_seen:
            await w.incident(s, "unsupported_belief", f"croit le pont {out['bridge']} sans aucune information reçue")   # C6/Q5 : croyance sans source
        return out, r

    # ---------- un tour de réflexion ----------
    async def think(s, w, retrieve):
        now = w.clock.now(); t_info = now
        s.inbox_read = [m for m in s.inbox if m["t_deliver"] <= now]
        for m in s.inbox_read:
            if m["t_deliver"] > t_info: w.stats.viol += 1           # C6 : jamais un message utilisé avant d'être remis
        s.dirty = False; s.next_think = now + w.think_every; s.last_think = now
        if s.state in ("arrived", "sheltered") or (s.state == "walking" and s.kind != "rule"): return     # en marche, un agent LLM ne délibère pas (sobriété)
        if s.state == "blocked":                                     # réessaie un autre chemin avec ce qu'il sait maintenant
            await s.reroute(w, now, None, "cherche un autre chemin")
            if s.state == "blocked": return
        decided_by, r, d = "rule", None, None
        if s.kind != "rule" and s.state not in ("walking",):
            s.thinking = True
            try: d, r = await s.llm_decide(w, t_info, retrieve)
            finally: s.thinking = False
            if d: decided_by = s.kind
        if d is None:
            d = await s.rule_decide(w, w.clock.now())
        else:
            s.risk = max(0, min(100, d["risk"]))
            if d["bridge"] != s.bridge: s.bridge, s.bridge_cert = d["bridge"], .7
            s.danger = d["danger"]
            if d["refuge"] != "aucun": s.refuge = d["refuge"]
        now2 = w.clock.now()
        s.why = d["justification"] or s.why; s.last_action = d["action"]; s.last_retrieved = r["strings"] if r else []
        await w.emit("agent.think", s.id, action=d["action"], why=s.why, decider=decided_by, model=s.model if decided_by != "rule" else None,
                     t_info=round(t_info, 2), risk=s.risk, latency_ms=d.get("latency_ms"), retrieved=s.last_retrieved, retrieved_meta=r["meta"] if r else [], query=r["query"] if r else "")
        await s.publish_belief(w)
        await s.apply(w, d, now2)

    async def publish_belief(s, w):
        b = (s.bridge, s.refuge, s.danger, s.risk // 5)
        if b != getattr(s, "_last_belief", None):
            s._last_belief = b
            await w.emit("agent.belief", s.id, bridge=s.bridge, refuge=s.refuge, danger=s.danger, risk=s.risk, certainty=round(s.bridge_cert, 2))

    async def apply(s, w, d, now):
        a = d["action"]
        if a == "verifier" and s.verify_until is None and s.state in ("home", "preparing"):
            s.verify_until = now + 3; await w.emit("agent.verify", s.id, topic="pont", until=round(s.verify_until, 2))
        elif a in ("preparer_depart", "partir") and s.state == "home":
            prep = s.p["prep"] if a == "preparer_depart" or s.p["prep"] >= 6 else 1      # Lina et Maya ne peuvent pas partir sans préparer
            s.state, s.t_ready = "preparing", now + prep
            s.remember(now, f"{hm(now)} — je prépare mon départ (prêt vers {hm(s.t_ready)})", "depart")
            await w.emit("agent.prepare", s.id, ready_at=round(s.t_ready, 2), refuge=s.refuge)
        elif a == "partir" and s.state == "preparing" and now >= s.t_ready:
            await s.depart(w, now, d.get("via"))
        elif a == "changer_itineraire" and s.state in ("walking", "blocked"):
            await s.reroute(w, now, d.get("via"), "décision")
        if d.get("via"): s.via_pref = d["via"]
        msg = d.get("message", "").strip()
        if s.kind == "rule" or not msg:
            fwd = [m for m in s.inbox_read if m["id"] not in s.seen_msgs]
            s.seen_msgs.update(m["id"] for m in fwd)
            if s.kind == "rule":
                for m in fwd:                                        # relais à règles : selon la confiance et le profil
                    if (m["src"] == "mairie" or s.trust_of(m["src"]) >= 3) and s.rng.random() < s.p["relay"]:
                        keep = s.rng.random() > (.3 if s.p["relay"] > .7 else .1)        # la source se perd parfois en route
                        await w.send(s, m["text"], m["claims"], "relais", origin=m["origin"] if keep else s.id, hops=m["hops"] + 1, parent=m["id"])
            if a == "corriger" and s.kind == "rule": pass
        elif msg:
            kind = {"relayer": "relais", "corriger": "correction"}.get(a, "message")
            claims = {"bridge": s.bridge if s.bridge != "inconnu" else None, "danger": s.danger, "refuge": s.refuge if s.refuge != "aucun" else None,
                      "level": "evacuation" if s.risk >= 80 else "alerte" if s.risk >= 50 else "vigilance"}
            src_m = next((m for m in reversed(s.inbox_read) if m["claims"].get("bridge")), None)
            origin = s.id if (s.bridge_seen or kind == "correction" or not src_m) else src_m["origin"]
            await w.send(s, msg, claims, kind, origin=origin, hops=(src_m["hops"] + 1 if src_m and origin != s.id else 0), parent=src_m["id"] if src_m else None)
            s.seen_msgs.update(m["id"] for m in s.inbox_read)

    # ---------- mouvements ----------
    async def shelter_or_block(s, w, now, reason):
        """Aucun chemin sûr vers un refuge : plutôt que d'attendre dans la rue pendant que l'eau monte, on rentre chez soi
        se mettre à l'étage (consigne de mise à l'abri) ; si le retour est coupé, on monte dans le bâtiment le plus proche.
        Bloqué seulement si l'on est déjà pris dans une eau trop haute pour marcher."""
        if s.node == s.home:
            s.state = "sheltered"; s.x, s.z = w.streets.nodes[s.home]
            s.remember(now, f"{hm(now)} — aucun chemin sûr ({reason}) : je reste chez moi, à l'étage", "abri")
            await w.emit("agent.shelter", s.id, reason=reason); return
        p, _ = w.streets.path(s.node, s.home, s.known_blocked)
        if p:
            s.path, s.seg, s.state, s.going_home, s.refuge, s.via = p, 0, "walking", True, "aucun", "retour"
            s.remember(now, f"{hm(now)} — aucun chemin sûr ({reason}) : je rentre me mettre à l'abri chez moi", "abri")
            await w.emit("agent.reroute", s.id, reason=f"{reason} : rentre chez soi", refuge=None, old_refuge=None, via="retour",
                         path=[w.streets.nodes[n] for n in p], nodes=p)
            return
        if speed_factor(w.flood.depth(s.x, s.z, now)) > 0 and not w.streets.on_crossing_node(s.node):
            s.state = "sheltered"
            s.remember(now, f"{hm(now)} — aucun chemin sûr ({reason}), ni pour rentrer : je monte dans le bâtiment le plus proche", "abri")
            await w.emit("agent.shelter", s.id, reason=f"{reason} : bâtiment le plus proche"); return
        if s.state != "blocked": s.blocked_since = now
        s.state = "blocked"; await w.emit("agent.blocked", s.id, reason=reason, at=s.node)

    async def depart(s, w, now, via=None):
        r, p = s.choose_refuge_and_route(w, s.refuge if s.refuge in REFUGES else None, via)
        if not p:
            await s.shelter_or_block(w, now, "aucun chemin que je crois praticable"); return
        s.refuge, s.path, s.seg, s.state = r, p, 0, "walking"; s.via = via_of(p)
        if s.t_depart is None: s.t_depart = now
        s.remember(now, f"{hm(now)} — je pars vers {REFUGES[r]['name']} par {s.via.replace('_', ' ')}", "depart", r)
        await w.emit("agent.depart", s.id, refuge=r, via=s.via, path=[w.streets.nodes[n] for n in p], nodes=p,
                     length=round(w.streets.length(p), 1), why=s.why)

    async def reroute(s, w, now, via, reason):
        s.node = s.path[s.seg] if s.path else s.node
        s.x, s.z = w.streets.nodes[s.node]
        old = s.refuge
        r, p = s.choose_refuge_and_route(w, None if reason == "refuge plein" else s.refuge, via)
        if not p:
            await s.shelter_or_block(w, now, reason); return
        s.refuge, s.path, s.seg, s.state, s.going_home = r, p, 0, "walking", False; s.via = via_of(p)
        s.remember(now, f"{hm(now)} — demi-tour ({reason}) : je vais vers {REFUGES[r]['name']} par {s.via.replace('_', ' ')}", "reroute", r)
        await w.emit("agent.reroute", s.id, reason=reason, refuge=r, old_refuge=old, via=s.via, path=[w.streets.nodes[n] for n in p], nodes=p)
        await s.publish_belief(w)

    async def step(s, w, now, dt_s):
        """Avance le marcheur de dt_s secondes. Voit la vérité seulement là où il est (pont fermé, eau trop haute)."""
        if s.state != "walking": return
        left = dt_s
        while left > 0 and s.state == "walking":
            if s.seg >= len(s.path) - 1:
                if s.going_home: s.going_home = False; await s.shelter_or_block(w, now, "rentré chez moi"); return
                await w.arrive(s, now); return
            a, b = s.path[s.seg], s.path[s.seg + 1]; bx, bz = w.streets.nodes[b]
            k = ekey(a, b)
            if k == ekey(*BRIDGE) and not w.bridge_open:                  # il voit la barrière : observation locale
                s.known_blocked.add(k); s.bridge, s.bridge_cert, s.bridge_seen = "ferme", 1.0, True; s.local_obs = True
                s.remember(now, f"{hm(now)} — j'ai vu moi-même que le Pont Central est fermé", "pont", "observation")
                await w.emit("agent.observe", s.id, what="pont_ferme", where=[s.x, s.z])
                await w.observation_message(s, "pont_ferme")
                await s.reroute(w, now, None, "pont fermé"); continue
            depth_next, depth_here = w.flood.depth(bx, bz, now), w.flood.depth(s.x, s.z, now)
            if depth_here >= DANGER_DEPTH and not w.streets.on_crossing_node(a):      # l'eau monte sous ses pieds : il entre dans le bâtiment le plus proche
                s.state = "sheltered"; s.going_home = False
                s.remember(now, f"{hm(now)} — l'eau monte autour de moi ({int(depth_here * 100)} cm) : je monte dans le bâtiment le plus proche", "abri", "eau")
                await w.emit("agent.shelter", s.id, reason=f"{int(depth_here * 100)} cm d'eau : bâtiment le plus proche"); return
            # au sec, on n'entre pas dans une rue où l'on voit 20 cm d'eau ou plus (au bord de la rivière en crue, c'est la noyade) ;
            # déjà dans l'eau, on continue vers la sortie, sauf si devant elle est trop haute pour marcher
            if (depth_here < SAFE_DEPTH <= depth_next) or (speed_factor(depth_next) == 0 and speed_factor(depth_here) > 0):
                s.known_blocked.add(k)
                s.remember(now, f"{hm(now)} — trop d'eau ({int(depth_next * 100)} cm) vers {w.streets.meta[k][1]}, je fais demi-tour", "eau")
                await w.emit("agent.observe", s.id, what="rue_inondee", where=[bx, bz], depth=round(depth_next, 2), street=w.streets.meta[k][1])
                await s.reroute(w, now, None, "rue inondée"); continue
            v = WALK * s.p["mob"] * speed_factor(w.flood.depth(s.x, s.z, now))
            if v <= 0:
                if s.state != "blocked": s.blocked_since = now
                s.state = "blocked"; await w.emit("agent.blocked", s.id, reason="eau trop haute ici", at=s.node); return
            d = math.hypot(bx - s.x, bz - s.z)
            if v * left >= d:
                s.x, s.z = bx, bz; left -= d / v; s.seg += 1; s.node = b
            else:
                s.x += (bx - s.x) / d * v * left; s.z += (bz - s.z) / d * v * left; left = 0

"""Indicateurs de la fiche ALERTE, calculés en flux à partir des seuls événements (même code dans la gateway et la simulation).
Contexte commun (B1) = part des habitants dont la lecture est correcte sur les TROIS faits :
  1. le Pont Central est-il ouvert ? 2. le refuge visé est-il encore accessible ? 3. le danger est-il imminent ?
La convergence (accord) est suivie à part de l'exactitude : un groupe peut être d'accord et se tromper (H4)."""
T_ALERT0 = 540.0                                     # 09:00, premier bulletin

class Indicators:
    def __init__(s): s.reset({})
    def reset(s, start):
        s.n = start.get("n_agents", 0); s.caps = {r["id"]: r["cap"] for r in start.get("refuges", [])}
        s.belief = {}; s.truth = {"bridge": "ouvert", "danger": False, "refuges": list(s.caps)}
        s.depart, s.arrive, s.wet, s.blocked, s.sheltered = {}, {}, {}, set(), set()
        s.msg = {"message": 0, "relais": 0, "correction": 0, "observation": 0, "rumeur": 0, "officiel": 0}
        s.verified = 0; s.rumor_relays = 0; s.rumor_corrected = False; s.rumor_ids = set(); s.cc_last = None; s.cc_area = 0.0; s.cc_span = 0.0
    def feed(s, ev):
        """Renvoie un dict d'indicateurs si l'événement les change, sinon None."""
        t, d, a = ev["type"], ev.get("data", {}), ev.get("agent")
        if t == "run.start": s.reset(d); return s.snap(ev)
        if t == "world.truth": s.truth = {"bridge": d["bridge"], "danger": d["danger"], "refuges": d["refuges"]}
        elif t == "agent.belief": s.belief[a] = (d["bridge"], d["refuge"], d["danger"])
        elif t == "agent.depart": s.depart.setdefault(a, ev["t_sim"])
        elif t == "agent.arrive": s.arrive[a] = d["refuge"]; s.blocked.discard(a)
        elif t == "agent.blocked": s.blocked.add(a)
        elif t == "agent.shelter": s.sheltered.add(a); s.blocked.discard(a)
        elif t in ("agent.reroute",): s.blocked.discard(a)
        elif t == "agent.say":
            k = d.get("kind", "message"); s.msg[k] = s.msg.get(k, 0) + 1
            if d.get("parent") in s.rumor_ids or d.get("origin") == "anonyme":
                s.rumor_ids.add(d["msg_id"])
                if not s.rumor_corrected: s.rumor_relays += 1
            if k == "correction": s.rumor_corrected = True
        elif t == "rumor": s.msg["rumeur"] += 1; s.rumor_ids.update(d.get("msg_ids", []))
        elif t == "official.bulletin": s.msg["officiel"] += 1
        elif t == "agent.verify_result": s.verified += 1
        elif t == "world.tick":
            for p in d.get("agents", []): s.wet[p["id"]] = p.get("wet", 0)
            return None
        else: return None
        return s.snap(ev)
    def correct(s, b):
        br, rf, dg = b
        return [br == s.truth["bridge"], rf in s.truth["refuges"], dg == s.truth["danger"]]
    def snap(s, ev):
        n = max(1, s.n); ids = [f"A{i + 1:02d}" for i in range(s.n)]
        bel = [s.belief.get(i, ("inconnu", "aucun", False)) for i in ids]
        ok = [s.correct(b) for b in bel]
        for k, i in enumerate(ids):                                   # arrivé dans un refuge : sa lecture du refuge est juste
            if i in s.arrive: ok[k][1] = True
        cc = sum(all(o) for o in ok) / n; t = ev.get("t_sim", -1) if ev else -1
        if t >= 0 and s.cc_last is not None and t > s.cc_last[0]: s.cc_area += s.cc_last[1] * (t - s.cc_last[0]); s.cc_span += t - s.cc_last[0]
        if t >= 0: s.cc_last = (t, cc)
        def mode_share(vals, exclude=()):
            c = {}
            for v in vals:
                if v not in exclude: c[v] = c.get(v, 0) + 1
            return (max(c.items(), key=lambda x: x[1]) if c else (None, 0))
        rb, rn = mode_share([b[1] for b in bel], ("aucun",)); bb, bn = mode_share([b[0] for b in bel], ("inconnu",))
        load = {r: sum(1 for v in s.arrive.values() if v == r) for r in s.caps}
        delays = [t - T_ALERT0 for t in s.depart.values()]
        out = {"safe": len(s.arrive), "safe_pct": round(len(s.arrive) / n, 3),
               "departed": len(s.depart), "depart_delay_avg": round(sum(delays) / len(delays), 1) if delays else None,
               "convergence_refuge": round(rn / n, 3), "refuge_mode": rb, "convergence_bridge": round(bn / n, 3), "bridge_mode": bb,
               "exact_bridge": round(sum(o[0] for o in ok) / n, 3), "exact_refuge": round(sum(o[1] for o in ok) / n, 3), "exact_danger": round(sum(o[2] for o in ok) / n, 3),
               "common_context": round(cc, 3), "common_context_avg": round(s.cc_area / s.cc_span, 3) if s.cc_span else round(cc, 3),
               "messages": dict(s.msg), "verified": s.verified, "rumor_relays_before_correction": s.rumor_relays, "rumor_corrected": s.rumor_corrected,
               "refuge_load": {r: [load[r], s.caps[r]] for r in s.caps}, "blocked": len(s.blocked), "sheltered": len(s.sheltered),
               "wet_avg_min": round(sum(s.wet.values()) / n, 2), "truth": dict(s.truth)}
        return out

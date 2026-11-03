"""Étude des paramètres et des comportements (règles : 5 graines par configuration ; LLM : 3 graines, 4 habitants LLM).
  python tools/etude_parametres.py run       -> lance les lots (docker compose, sans Kafka ni Godot), runs dans data/etude/<config>/
  python tools/etude_parametres.py rapport   -> tableaux Markdown (effet des paramètres, comportements par profil, règles / LLM)
  python tools/etude_parametres.py compare   -> seulement la comparaison règles / LLM
Les réglages de la page Villageois sont ignorés (VILLAGEOIS_FILE inexistant) : seuls les paramètres de la grille changent."""
import glob, json, os, statistics, subprocess, sys

SEEDS = 5
REF = {"SCENARIO": "pont_ferme", "CRUE": "rapide", "UPDATE_AT": "09:05", "RUMOR_AT": "09:20", "PANNE_RESEAU": "none", "N_AGENTS": "100"}
GRID = {}
for crue in ("lente", "rapide", "eclair"):                         # 1. vitesse de la crue x heure de l'alerte d'évacuation
    for maj in ("09:01", "09:05", "09:10", "09:20", "09:30"):
        GRID[f"crue_{crue}__alerte_{maj.replace(':', 'h')}"] = {"CRUE": crue, "UPDATE_AT": maj}
GRID["sans_rumeur"] = {"RUMOR_AT": "none"}                        # 2. un facteur à la fois, autour de la référence
GRID["rumeur_tot_09h03"] = {"RUMOR_AT": "09:03"}
GRID["reseau_coupe_09h03"] = {"PANNE_RESEAU": "09:03"}
GRID["pont_ouvert"] = {"SCENARIO": "pont_ouvert"}
GRID["population_50"] = {"N_AGENTS": "50"}
GRID["population_200"] = {"N_AGENTS": "200"}
SEUILS = {"danger_dehors_20cm": {"OUT_DANGER": ".2"}, "danger_dehors_40cm": {"OUT_DANGER": ".4"},   # 3. sensibilité aux seuils de maquette
          "danger_rdc_30cm": {"IN_DANGER": ".3"}, "danger_rdc_70cm": {"IN_DANGER": ".7"},
          "victime_apres_5min": {"VICTIM_MIN": "5"}, "victime_apres_30min": {"VICTIM_MIN": "30"}}
GRID.update(SEUILS)
COMPARE = {"CRUE": "lente", "UPDATE_AT": "09:10"}                 # 4. règles / LLM : situation non jouée d'avance (66 % au refuge en règles)
GRID["regles__rumeur_09h03"] = {**COMPARE, "RUMOR_AT": "09:03"}
GRID["regles__sans_rumeur"] = {**COMPARE, "RUMOR_AT": "none"}
GRID["llm__rumeur_09h03"] = {**COMPARE, "RUMOR_AT": "09:03"}       # les 4 habitants LLM de la fiche (A04, A05, A06, A11) ; A06 reçoit la rumeur
GRID["llm__sans_rumeur"] = {**COMPARE, "RUMOR_AT": "none"}
LLM_SEEDS = 3                                                     # environ 8 min par run LLM (Ollama + Groq)
OUT = "data/etude"

def run():
    for name, over in GRID.items():
        if glob.glob(f"{OUT}/{name}/*.jsonl"): print("déjà fait :", name); continue
        cfg = {**REF, **over}; llm = name.startswith("llm__")
        env = ["-e", "KAFKA=", "-e", f"BATCH={LLM_SEEDS if llm else SEEDS}", "-e", "TIME_SCALE=0", "-e", "MODE=sync", "-e", "LLM_EXTRA=0",
               "-e", f"LLM_AGENTS={'A04,A05,A06,A11' if llm else 'none'}", "-e", "VILLAGEOIS_FILE=/nonexistent", "-e", f"RUNS_DIR=/data/etude/{name}"] + [x for k, v in cfg.items() for x in ("-e", f"{k}={v}")]
        print("lot :", name, flush=True)
        subprocess.run(["docker", "compose", "run", "--rm", "--no-deps", *env, "sim"], check=True, env={**os.environ, "MSYS_NO_PATHCONV": "1"})

def load(name):
    out = []
    for f in sorted(glob.glob(f"{OUT}/{name}/*.jsonl")):
        start = end = None; victims = set()
        for l in open(f, encoding="utf-8"):
            e = json.loads(l)
            if e["type"] == "run.start": start = e["data"]
            elif e["type"] == "run.end": end = e["data"]
            elif e["type"] == "agent.victim": victims.add(e["agent"])
        if start and end: out.append((start, end, victims))
    return out

def ms(v, fmt="{:.1f}"):
    v = [x for x in v if x is not None]
    if not v: return "—"
    return (fmt + " ± " + fmt).format(statistics.mean(v), statistics.stdev(v)) if len(v) > 1 else fmt.format(v[0])

def pct(a, b): return f"{100 * a / b:.0f} %" if b else "—"

def run_row(name, runs):
    n = [len(s["agents"]) for s, _, _ in runs]; i = [e["indicators"] for _, e, _ in runs]
    return (f"| {name} | {len(runs)} | {ms([x['safe'] / k * 100 for x, k in zip(i, n)], '{:.0f}')} | {ms([x['sheltered'] / k * 100 for x, k in zip(i, n)], '{:.0f}')} "
            f"| {ms([x.get('victims', 0) for x in i])} | {ms([x.get('damage', {}).get('sinistree', 0) for x in i])} | {ms([x['depart_delay_avg'] for x in i])} "
            f"| {ms([x['common_context_avg'] * 100 for x in i], '{:.0f}')} | {ms([x['exact_bridge'] * 100 for x in i], '{:.0f}')} | {ms([x['wet_avg_min'] for x in i])} |")

HEAD = ("| configuration | runs | à l'abri au refuge (%) | à l'étage chez soi (%) | victimes | maisons sinistrées | départ moyen (min après 09:00) "
        "| contexte commun moyen (%) | lecture juste du pont (%) | min dans l'eau |\n|---|---|---|---|---|---|---|---|---|---|")

def behaviours(names):
    """Une ligne par habitant et par run : traits du profil (run.start) -> ce qui lui est arrivé (run.end)."""
    rows = []
    for name in names:
        for s, e, victims in load(name):
            fin = {a["id"]: a for a in e["agents"]}
            for a in s["agents"]:
                f = fin[a["id"]]
                rows.append({**a, "out": "victime" if a["id"] in victims else f["state"], "delay": None if f["t_depart"] is None else f["t_depart"] - 540, "wet": f["wet"]})
    return rows

def group(rows, label, key):
    print(f"\n**{label}**\n\n| valeur | habitants-runs | part partie | départ moyen (min) | au refuge | à l'étage | victimes | min dans l'eau |\n|---|---|---|---|---|---|---|---|")
    for v in sorted({key(r) for r in rows}, key=str):
        g = [r for r in rows if key(r) == v]; d = [r["delay"] for r in g if r["delay"] is not None]
        print(f"| {v} | {len(g)} | {pct(len(d), len(g))} | {statistics.mean(d):.1f} | {pct(sum(r['out'] == 'arrived' for r in g), len(g))} "
              f"| {pct(sum(r['out'] == 'sheltered' for r in g), len(g))} | {sum(r['out'] == 'victime' for r in g)} | {statistics.mean(r['wet'] for r in g):.1f} |" if d else f"| {v} | {len(g)} | 0 % | — | — | — | — | — |")

def rapport():
    print("## 1. Crue x heure de l'alerte d'évacuation\n\nRéférence : pont fermé à 09:20, rumeur à 09:20, réseau OK, 100 habitants, 100 % règles, 5 graines (moyenne ± écart-type).\n")
    print(HEAD)
    for name in GRID:
        if name.startswith("crue_"): print(run_row(name.replace("__", " · "), load(name)))
    print("\n## 2. Un paramètre à la fois\n\nAutour de la référence : crue rapide, alerte à 09:05.\n")
    print(HEAD); print(run_row("référence (crue rapide, alerte 09:05)", load("crue_rapide__alerte_09h05")))
    for name in GRID:
        if not name.startswith(("crue_", "regles__", "llm__")): print(run_row(name, load(name)))
    mid = [n for n in GRID if n.startswith("crue_rapide__")]
    rows = behaviours(mid)
    print(f"\n## 3. Comportements par profil\n\nCrue rapide, toutes heures d'alerte confondues : {len(rows)} habitants-runs. "
          "« part partie » = a quitté la maison ; un habitant parti peut finir à l'étage s'il n'a plus de chemin sûr.")
    group(rows, "Seuil de risque pour partir (threshold)", lambda r: "a. < 45" if r["threshold"] < 45 else "b. 45-54" if r["threshold"] < 55 else "c. 55-64" if r["threshold"] < 65 else "d. >= 65")
    group(rows, "Temps de préparation (prep, min)", lambda r: r["prep"])
    group(rows, "Confirmations demandées (needs_conf)", lambda r: r["needs_conf"])
    group(rows, "Ne peut pas prendre d'escaliers (no_stairs)", lambda r: r["no_stairs"])
    group(rows, "Personnes à charge", lambda r: "oui" if r["dependants"] else "non")
    group(rows, "Écoute la radio", lambda r: "radio" in r["canaux"])
    group(rows, "Vérifie auprès de la mairie (verifier)", lambda r: r["verifier"])
    group(rows, "Mobilité (mob)", lambda r: "lente (< 0,8)" if r["mob"] < .8 else "normale")
    compare()

def compare():
    names = [n for n in GRID if n.startswith(("regles__", "llm__"))]
    print("\n## 4. Règles / LLM\n\nCrue lente, alerte 09:10. Dans les runs « llm », A04 et A05 pensent avec le grand modèle, A06 et A11 avec le compact ; "
          "les 96 autres habitants suivent leurs règles.\n")
    print(HEAD)
    for n in names: print(run_row(n, load(n)))
    print("\n**Les 4 habitants concernés** (moyenne sur les runs)\n\n| configuration | habitant | départ (min après 09:00) | au refuge | croit le pont fermé à un moment | relaie la rumeur |\n|---|---|---|---|---|---|")
    for n in names:
        runs = glob.glob(f"{OUT}/{n}/*.jsonl")
        for aid in ("A04", "A05", "A06", "A11"):
            dep, arr, closed, relay = [], 0, 0, 0
            for f in runs:
                rumor_ids, c, r = set(), False, False
                for l in open(f, encoding="utf-8"):
                    e = json.loads(l); d = e["data"]
                    if e["type"] == "rumor": rumor_ids.update(d.get("msg_ids", []))
                    if e["agent"] != aid: continue
                    if e["type"] == "agent.depart": dep.append(e["t_sim"] - 540)
                    if e["type"] == "agent.arrive": arr += 1
                    if e["type"] == "agent.belief" and d.get("bridge") == "ferme": c = True
                    if e["type"] == "agent.say" and (d.get("parent") in rumor_ids or d.get("origin") == "anonyme"): r = True
                closed += c; relay += r
            print(f"| {n} | {aid} | {statistics.mean(dep):.1f} | {arr}/{len(runs)} | {closed}/{len(runs)} | {relay}/{len(runs)} |" if dep else f"| {n} | {aid} | — | {arr}/{len(runs)} | {closed}/{len(runs)} | {relay}/{len(runs)} |")
    llm = [e for n in names if n.startswith("llm__") for _, e, _ in load(n)]
    if llm:
        inc = {}
        for e in llm:
            for k, v in e.get("incidents_by_kind", {}).items(): inc[k] = inc.get(k, 0) + v
        print(f"\nCoût des runs LLM : {statistics.mean(e['calls_total'] for e in llm):.0f} appels par run, latence moyenne "
              f"{statistics.mean(e['avg_latency_ms'] for e in llm) / 1000:.0f} s, incidents : {inc}")

if __name__ == "__main__":
    if sys.argv[1:] == ["compare"]: compare(); sys.exit()
    {"run": run, "rapport": rapport}[sys.argv[1] if len(sys.argv) > 1 else "rapport"]()

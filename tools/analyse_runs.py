"""Compare les runs enregistrés (F5, E2) : une ligne par run, puis moyenne et écart-type par configuration.
Requête reproductible : ne lit que data/runs/*.jsonl (l'événement run.end de chaque run).
  python tools/analyse_runs.py data/runs            -> tableau Markdown
  python tools/analyse_runs.py data/runs --csv out.csv"""
import glob, json, os, statistics, sys

KEYS = ["safe", "depart_delay_avg", "common_context_avg", "convergence_refuge", "exact_bridge", "rumor_relays_before_correction", "wet_avg_min"]

def load(path):
    rows = []
    for f in sorted(glob.glob(os.path.join(path, "*.jsonl"))):
        start = end = None
        for l in open(f, encoding="utf-8"):
            e = json.loads(l)
            if e["type"] == "run.start": start = e["data"]
            elif e["type"] == "run.end": end = e["data"]
        if not (start and end): continue
        i = end["indicators"]
        cfg = f"{end['scenario']} · {end['mode']} · maj {end['update_at']} · {'LLM ' + '+'.join(end['llm_agents']) if end['llm_agents'] else 'règles seules'}"
        rows.append({"run": os.path.basename(f)[:-6], "config": cfg, "seed": end["seed"], **{k: i.get(k) for k in KEYS},
                     "calls": end.get("calls_total", 0), "tokens": end.get("tokens_in", 0) + end.get("tokens_out", 0), "incidents": end.get("incidents", 0)})
    return rows

def main():
    rows = load(sys.argv[1] if len(sys.argv) > 1 else "data/runs")
    if "--csv" in sys.argv:
        import csv
        with open(sys.argv[sys.argv.index("--csv") + 1], "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
    cols = KEYS + ["calls", "tokens", "incidents"]
    print("| configuration | n runs | " + " | ".join(cols) + " |"); print("|---" * (len(cols) + 2) + "|")
    for cfg in sorted({r["config"] for r in rows}):
        rs = [r for r in rows if r["config"] == cfg]; cells = []
        for k in cols:
            v = [r[k] for r in rs if r[k] is not None]
            cells.append("—" if not v else f"{statistics.mean(v):.2f} ± {statistics.stdev(v):.2f}" if len(v) > 1 else f"{v[0]:.2f}")
        print(f"| {cfg} | {len(rs)} | " + " | ".join(cells) + " |")
    if len({r['config'] for r in rows}) and min(len([r for r in rows if r['config'] == c]) for c in {r['config'] for r in rows}) < 5:
        print("\nAttention : moins de 5 runs dans au moins une configuration (exigence E2 : au moins 5 runs par configuration).")

if __name__ == "__main__":
    main()

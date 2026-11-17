"""Analyse Spark (batch) de tous les runs enregistrés : data/runs/*.jsonl et data/etude/<config>/*.jsonl.
Trois niveaux de données, sur le modèle bronze / argent / or :
  - bronze : les fichiers JSONL tels que la simulation les écrit (un événement par ligne) ;
  - argent : les événements lus et typés, rangés en Parquet et partitionnés par type d'événement (evenements/) ;
  - or     : les tables d'analyse (runs, par_config, habitants, par_profil, diffusion_minute, volume) et rapport_spark.md.

  spark-submit analyse_spark.py [--sortie /data/spark] [--sans-parquet] [dossier ...]      (par défaut /data/runs /data/etude)
Dans Docker : lancer_spark.bat batch   (ou docker compose --profile spark-batch run --rm spark-batch)

Même lecture que tools/analyse_runs.py et tools/etude_parametres.py (bilan run.end, profils de run.start), mais
distribuée : chaque fichier est lu en parallèle, les jointures et agrégations se répartissent sur les cœurs disponibles.
Pas de fonction Python dans les calculs (aucune UDF) : tout reste dans le moteur de Spark."""
import argparse
import os
import time

from pyspark.sql import functions as F
from pyspark.sql import types as T

from commun import ENVELOPPE, session

T_ALERTE = 540.0                                         # 09:00 en minutes depuis minuit
LOURDS = ("world.tick", "flood.update", "tick", "metrics")   # positions et grilles d'eau : volumineux, inutiles aux bilans

AGENT_FIN = T.StructType([T.StructField(k, t) for k, t in (
    ("id", T.StringType()), ("state", T.StringType()), ("refuge", T.StringType()), ("t_depart", T.DoubleType()),
    ("t_arrive", T.DoubleType()), ("wet", T.DoubleType()), ("via", T.StringType()))])
FIN = T.StructType([T.StructField(k, t) for k, t in (
    ("scenario", T.StringType()), ("mode", T.StringType()), ("seed", T.LongType()), ("n_agents", T.LongType()),
    ("llm_agents", T.ArrayType(T.StringType())), ("update_at", T.StringType()), ("rumor_at", T.StringType()),
    ("crue", T.StringType()), ("panne_reseau", T.StringType()), ("calls_total", T.LongType()), ("tokens_in", T.LongType()),
    ("tokens_out", T.LongType()), ("incidents", T.LongType()), ("p95_latency_ms", T.LongType()),
    ("indicators", T.StructType([T.StructField(k, t) for k, t in (
        ("safe", T.LongType()), ("safe_pct", T.DoubleType()), ("sheltered", T.LongType()), ("victims", T.LongType()),
        ("depart_delay_avg", T.DoubleType()), ("convergence_refuge", T.DoubleType()), ("exact_bridge", T.DoubleType()),
        ("common_context_avg", T.DoubleType()), ("wet_avg_min", T.DoubleType()),
        ("rumor_relays_before_correction", T.LongType()), ("damage", T.MapType(T.StringType(), T.LongType())))])),
    ("agents", T.ArrayType(AGENT_FIN)))])
PROFIL = T.StructType([T.StructField(k, t) for k, t in (
    ("id", T.StringType()), ("kind", T.StringType()), ("threshold", T.DoubleType()), ("prep", T.DoubleType()),
    ("needs_conf", T.LongType()), ("no_stairs", T.BooleanType()), ("mob", T.DoubleType()), ("verifier", T.BooleanType()),
    ("dependants", T.ArrayType(T.StringType())), ("canaux", T.ArrayType(T.StringType())))])
DEBUT = T.StructType([T.StructField("agents", T.ArrayType(PROFIL))])


def lire(spark, dossiers):
    """Bronze -> argent : une ligne par événement, avec le fichier d'origine et sa configuration (nom du dossier d'étude)."""
    ev = (spark.read.schema(ENVELOPPE).option("recursiveFileLookup", "true").option("pathGlobFilter", "*.jsonl").json(dossiers)
          .withColumn("fichier", F.input_file_name())
          .withColumn("dossier", F.regexp_extract("fichier", r"([^/\\]+)[/\\][^/\\]+\.jsonl$", 1)))
    return ev.filter(F.col("type").isNotNull())


def table_runs(ev):
    """Une ligne par run terminé : ses paramètres (run.end) et ses indicateurs."""
    fin = ev.filter(F.col("type") == "run.end").select("fichier", "dossier", "run_id", F.from_json("data", FIN).alias("f"))
    n_llm = F.size(F.coalesce("f.llm_agents", F.array()))
    auto = F.concat_ws(" · ", "f.scenario", F.concat(F.lit("crue "), F.coalesce("f.crue", F.lit("?"))), F.concat(F.lit("maj "), "f.update_at"),
                       F.when(n_llm > 0, F.concat(F.lit("LLM x"), n_llm.cast("string"))).otherwise(F.lit("règles seules")))
    i = "f.indicators."
    return fin.select(
        "fichier", "run_id",
        F.when(F.col("dossier").isin("runs", ""), auto).otherwise(F.col("dossier")).alias("config"),
        F.col("f.scenario").alias("scenario"), F.col("f.crue").alias("crue"), F.col("f.mode").alias("mode"),
        F.col("f.update_at").alias("maj"), F.col("f.rumor_at").alias("rumeur"), F.col("f.panne_reseau").alias("panne"),
        F.col("f.seed").alias("graine"), F.col("f.n_agents").alias("habitants"), n_llm.alias("habitants_llm"),
        (F.col(i + "safe_pct") * 100).alias("au_refuge_pct"),
        (F.col(i + "sheltered") / F.col("f.n_agents") * 100).alias("a_l_etage_pct"),
        F.coalesce(F.col(i + "victims"), F.lit(0)).alias("victimes"),
        F.col(i + "depart_delay_avg").alias("depart_moyen_min"),
        (F.col(i + "common_context_avg") * 100).alias("contexte_commun_pct"),
        (F.col(i + "exact_bridge") * 100).alias("pont_juste_pct"),
        (F.col(i + "convergence_refuge") * 100).alias("convergence_refuge_pct"),
        F.col(i + "wet_avg_min").alias("min_dans_l_eau"),
        F.col(i + "rumor_relays_before_correction").alias("relais_rumeur"),
        F.coalesce(F.col(i + "damage")["sinistree"], F.lit(0)).alias("maisons_sinistrees"),
        F.col("f.calls_total").alias("appels_llm"), (F.col("f.tokens_in") + F.col("f.tokens_out")).alias("tokens"),
        F.col("f.incidents").alias("incidents_llm"), F.col("f.p95_latency_ms").alias("latence_p95_ms"),
        F.col("f.agents").alias("agents_fin"))


MESURES = ["au_refuge_pct", "a_l_etage_pct", "victimes", "depart_moyen_min", "contexte_commun_pct", "pont_juste_pct", "min_dans_l_eau", "appels_llm"]


def table_par_config(runs):
    """Moyenne et écart-type de chaque indicateur, par configuration (une configuration = plusieurs graines)."""
    aggs = [F.count("*").alias("runs")]
    for m in MESURES:
        aggs += [F.avg(m).alias(m), F.stddev(m).alias(m + "_et")]
    return runs.groupBy("config").agg(*aggs).orderBy("config")


def table_habitants(ev, runs):
    """Une ligne par habitant et par run : son profil (run.start) joint à ce qui lui est arrivé (run.end, agent.victim)."""
    profils = (ev.filter(F.col("type") == "run.start").select("fichier", F.explode(F.from_json("data", DEBUT)["agents"]).alias("a"))
               .select("fichier", "a.*"))
    fins = runs.select("fichier", "config", "crue", F.explode("agents_fin").alias("b")).select(
        "fichier", "config", "crue", F.col("b.id").alias("id"), "b.state", "b.t_depart", "b.wet")
    victimes = ev.filter(F.col("type") == "agent.victim").select("fichier", F.col("agent").alias("id")).distinct().withColumn("victime", F.lit(True))
    return (profils.join(fins, ["fichier", "id"]).join(victimes, ["fichier", "id"], "left")
            .withColumn("victime", F.coalesce("victime", F.lit(False)))
            .withColumn("issue", F.when(F.col("victime"), "victime").otherwise(F.col("state")))
            .withColumn("depart_min", F.col("t_depart") - T_ALERTE))


def criteres():                                         # une fonction : F.col a besoin d'une session Spark déjà lancée
    return [
    ("Seuil de risque pour partir", F.when(F.col("threshold") < 45, "a. < 45").when(F.col("threshold") < 55, "b. 45-54")
     .when(F.col("threshold") < 65, "c. 55-64").otherwise("d. >= 65")),
    ("Temps de préparation (min)", F.col("prep").cast("int").cast("string")),
    ("Confirmations demandées", F.col("needs_conf").cast("string")),
    ("Ne peut pas prendre d'escaliers", F.col("no_stairs").cast("string")),
    ("Personnes à charge", F.when(F.size(F.coalesce("dependants", F.array())) > 0, "oui").otherwise("non")),
    ("Écoute la radio", F.when(F.array_contains(F.coalesce("canaux", F.array()), "radio"), "oui").otherwise("non")),
    ("Mobilité", F.when(F.col("mob") < 0.8, "lente (< 0,8)").otherwise("normale")),
    ("Moteur de décision", F.col("kind")),
]


def table_par_profil(hab):
    """Pour chaque trait de profil et chaque valeur : ce que deviennent les habitants qui l'ont (tous runs confondus)."""
    long = None
    for nom, valeur in criteres():
        part = hab.select(F.lit(nom).alias("critere"), F.coalesce(valeur, F.lit("?")).alias("valeur"), "issue", "depart_min", "wet", "victime")
        long = part if long is None else long.unionByName(part)
    return (long.groupBy("critere", "valeur").agg(
        F.count("*").alias("habitants_runs"), (F.avg(F.col("depart_min").isNotNull().cast("double")) * 100).alias("partis_pct"),
        F.avg("depart_min").alias("depart_moyen_min"), (F.avg((F.col("issue") == "arrived").cast("double")) * 100).alias("au_refuge_pct"),
        (F.avg((F.col("issue") == "sheltered").cast("double")) * 100).alias("a_l_etage_pct"),
        F.sum(F.col("victime").cast("int")).alias("victimes"), F.avg("wet").alias("min_dans_l_eau"))
        .orderBy("critere", "valeur"))


def table_diffusion(ev, runs):
    """Propagation de l'information : messages envoyés par minute simulée, par configuration et par sorte de message."""
    say = ev.filter(F.col("type") == "agent.say").select(
        "fichier", F.floor("t_sim").cast("int").alias("minute"), F.get_json_object("data", "$.kind").alias("sorte"),
        F.when(F.get_json_object("data", "$.origin") == "mairie", "mairie").otherwise("habitant").alias("source"))
    n_runs = runs.groupBy("config").agg(F.count("*").alias("n_runs"))
    return (say.join(runs.select("fichier", "config"), "fichier").groupBy("config", "minute", "sorte", "source").agg(F.count("*").alias("messages"))
            .join(n_runs, "config").withColumn("messages_par_run", F.col("messages") / F.col("n_runs")).drop("n_runs"))


def table_volume(ev):
    return ev.groupBy("type").agg(F.count("*").alias("evenements"), F.countDistinct("fichier").alias("runs")).orderBy(F.desc("evenements"))


# ---------- rapport Markdown (les tables d'or sont petites : on les rapatrie sur le driver) ----------
def f1(v, fmt="{:.1f}"):
    return "—" if v is None else fmt.format(v)


def ms(r, m, fmt="{:.1f}"):
    v, e = r[m], r[m + "_et"]
    return "—" if v is None else (fmt + " ± " + fmt).format(v, e) if e is not None and r["runs"] > 1 else fmt.format(v)


def rapport(par_config, par_profil, volume, diffusion, info):
    L = ["# Analyse Spark des runs ALERTE 3D", "",
         f"{info['fichiers']} fichiers JSONL lus ({info['mo']:.0f} Mo), {info['evenements']:,} événements, ".replace(",", " ").replace("  ", ", ")   # 1 074 348
         + f"{info['runs']} runs terminés. Spark {info['version']}, {info['coeurs']} cœurs, {info['partitions']} partitions en lecture, "
         f"calcul en {info['secondes']:.0f} s.",
         "", "## 1. Indicateurs par configuration (moyenne ± écart-type sur les graines)", "",
         "| configuration | runs | au refuge (%) | à l'étage (%) | victimes | départ moyen (min) | contexte commun (%) | pont juste (%) | min dans l'eau | appels LLM |",
         "|---|---|---|---|---|---|---|---|---|---|"]
    for r in par_config:
        L.append(f"| {r['config']} | {r['runs']} | {ms(r, 'au_refuge_pct', '{:.0f}')} | {ms(r, 'a_l_etage_pct', '{:.0f}')} | {ms(r, 'victimes')} "
                 f"| {ms(r, 'depart_moyen_min')} | {ms(r, 'contexte_commun_pct', '{:.0f}')} | {ms(r, 'pont_juste_pct', '{:.0f}')} "
                 f"| {ms(r, 'min_dans_l_eau')} | {ms(r, 'appels_llm', '{:.0f}')} |")
    L += ["", "## 2. Comportements par profil (tous runs confondus)", ""]
    crit = None
    for r in par_profil:
        if r["critere"] != crit:
            crit = r["critere"]
            L += ["", f"**{crit}**", "", "| valeur | habitants-runs | partis (%) | départ moyen (min) | au refuge (%) | à l'étage (%) | victimes | min dans l'eau |",
                  "|---|---|---|---|---|---|---|---|"]
        L.append(f"| {r['valeur']} | {r['habitants_runs']} | {f1(r['partis_pct'], '{:.0f}')} | {f1(r['depart_moyen_min'])} | {f1(r['au_refuge_pct'], '{:.0f}')} "
                 f"| {f1(r['a_l_etage_pct'], '{:.0f}')} | {r['victimes']} | {f1(r['min_dans_l_eau'])} |")
    L += ["", "## 3. Diffusion de l'information (messages par run, par sorte)", "", "| sorte | source | messages par run (moyenne sur toutes les configurations) |", "|---|---|---|"]
    for r in diffusion:
        L.append(f"| {r['sorte']} | {r['source']} | {r['par_run']:.1f} |")
    L += ["", "## 4. Volume par type d'événement", "", "| type | événements | runs |", "|---|---|---|"]
    for r in volume:
        L.append(f"| {r['type']} | {r['evenements']} | {r['runs']} |")
    return "\n".join(L) + "\n"


def main():
    p = argparse.ArgumentParser()
    p.add_argument("dossiers", nargs="*", default=["/data/runs", "/data/etude"])
    p.add_argument("--sortie", default="/data/spark")
    p.add_argument("--sans-parquet", action="store_true", help="n'écrit que le rapport (Windows sans winutils, tests)")
    a = p.parse_args()
    dossiers = [d for d in a.dossiers if os.path.exists(d)]
    if not dossiers:
        raise SystemExit(f"aucun dossier de runs trouvé parmi {a.dossiers}")
    fichiers = [os.path.join(r, f) for d in dossiers for r, _, fs in os.walk(d) for f in fs if f.endswith(".jsonl")]

    t0 = time.time()
    spark = session("alerte-analyse", **{"spark.sql.files.maxPartitionBytes": str(32 * 1024 * 1024)})
    tout = lire(spark, dossiers)
    partitions = tout.rdd.getNumPartitions()
    volume = table_volume(tout.select("type", "fichier")).persist()     # sans data : Spark ne garde que deux colonnes
    # Les positions et grilles d'eau (LOURDS) font l'essentiel du volume et ne servent à aucun bilan : filtrées avant le cache.
    ev = tout.filter(~F.col("type").isin(*LOURDS)).persist()
    runs = table_runs(ev).persist()
    hab = table_habitants(ev, runs)
    par_config, par_profil = table_par_config(runs), table_par_profil(hab)
    diffusion = table_diffusion(ev, runs)

    if not a.sans_parquet:
        # repartition par type : chaque tâche n'écrit qu'un dossier type=..., au lieu d'ouvrir un écrivain Parquet par type et par tâche
        ev.drop("fichier").repartition("type").write.mode("overwrite").partitionBy("type").parquet(f"{a.sortie}/evenements")
        for nom, df in (("runs", runs.drop("agents_fin")), ("par_config", par_config), ("habitants", hab), ("par_profil", par_profil),
                        ("diffusion_minute", diffusion), ("volume", volume)):
            df.write.mode("overwrite").parquet(f"{a.sortie}/{nom}")

    n_cfg = runs.select("config").distinct().count()
    diff = (diffusion.groupBy("sorte", "source").agg((F.sum("messages_par_run") / F.lit(max(1, n_cfg))).alias("par_run"))
            .orderBy(F.desc("par_run")).collect())
    vol = volume.collect()
    info = {"fichiers": len(fichiers), "mo": sum(os.path.getsize(f) for f in fichiers) / 1e6, "evenements": sum(r["evenements"] for r in vol),
            "runs": runs.count(), "version": spark.version, "coeurs": spark.sparkContext.defaultParallelism, "partitions": partitions}
    md = rapport(par_config.collect(), par_profil.collect(), vol, diff, {**info, "secondes": time.time() - t0})
    os.makedirs(a.sortie, exist_ok=True)
    with open(os.path.join(a.sortie, "rapport_spark.md"), "w", encoding="utf-8") as f:
        f.write(md)
    print(md, flush=True)
    print(f"Tables Parquet et rapport dans {a.sortie}" if not a.sans_parquet else f"Rapport dans {a.sortie}/rapport_spark.md", flush=True)
    spark.stop()


if __name__ == "__main__":
    main()

"""Spark Structured Streaming branché sur Kafka : lit alerte.events en direct, pendant que la simulation tourne.
Toutes les FENETRE secondes (temps réel d'émission, champ t_proc), et pour chaque run :
  événements, messages entre habitants, départs, arrivées, victimes, décisions prises par un LLM, incidents LLM,
  niveau d'eau maximal et heure simulée atteinte.
Les fenêtres sont publiées en JSON dans le topic Kafka alerte.spark (et affichées dans la console du conteneur).
Le filigrane (watermark) de RETARD secondes borne l'attente des événements en retard : au-delà, une fenêtre est figée.

Complément de la passerelle, pas un remplaçant : la passerelle calcule les indicateurs métier événement par événement
(contexte commun, convergence...) ; ce job calcule des agrégats par fenêtre de temps, comme on le ferait sur un flux
beaucoup plus gros réparti entre plusieurs machines. Le checkpoint permet de reprendre là où on s'était arrêté.

  docker compose --profile spark up spark-flux        (ou lancer_spark.bat flux)
  docker compose logs -f spark-flux                     -> les fenêtres en direct"""
import os

from pyspark.sql import functions as F

from commun import ENVELOPPE, session

KAFKA = os.getenv("KAFKA", "kafka:9092")
ENTREE, SORTIE = os.getenv("TOPIC_ENTREE", "alerte.events"), os.getenv("TOPIC_SORTIE", "alerte.spark")
FENETRE, RETARD = os.getenv("FENETRE", "10 seconds"), os.getenv("RETARD", "30 seconds")
CHECKPOINT = os.getenv("CHECKPOINT", "/data/spark/checkpoints")


def compte(cond):
    return F.sum(F.when(cond, 1).otherwise(0))


def agreger(ev, fenetre=FENETRE, retard=RETARD):
    """Événements (enveloppe alerte.event/1) -> une ligne par (fenêtre, run). Marche aussi sur un DataFrame statique (tests)."""
    t = F.col("type")
    decideur = F.get_json_object("data", "$.decider")
    return (ev.filter(F.col("run_id").isNotNull() & (F.col("run_id") != "control"))
            .withColumn("t_emission", (F.col("t_proc") / 1000).cast("timestamp"))
            .withWatermark("t_emission", retard)
            .groupBy(F.window("t_emission", fenetre).alias("fenetre"), "run_id")
            .agg(F.count("*").alias("evenements"),
                 compte(t == "agent.say").alias("messages"),
                 compte(t == "agent.depart").alias("departs"),
                 compte(t == "agent.arrive").alias("arrivees"),
                 compte(t == "agent.victim").alias("victimes"),
                 compte((t == "agent.think") & decideur.isin("big", "small")).alias("decisions_llm"),
                 compte(t == "llm.incident").alias("incidents_llm"),
                 F.max(F.when(t == "world.tick", F.get_json_object("data", "$.level").cast("double"))).alias("niveau_eau_max"),
                 F.max("t_sim").alias("t_sim_max"))
            .select(F.col("fenetre.start").alias("debut"), F.col("fenetre.end").alias("fin"), "run_id", "evenements", "messages", "departs",
                    "arrivees", "victimes", "decisions_llm", "incidents_llm", F.round("niveau_eau_max", 3).alias("niveau_eau_max"),
                    F.format_string("%02d:%02d", (F.col("t_sim_max") / 60).cast("int"), (F.col("t_sim_max") % 60).cast("int")).alias("heure_simulee")))


def main():
    spark = session("alerte-flux")
    brut = (spark.readStream.format("kafka").option("kafka.bootstrap.servers", KAFKA).option("subscribe", ENTREE)
            .option("startingOffsets", "latest").option("failOnDataLoss", "false").load())
    ev = brut.select(F.from_json(F.col("value").cast("string"), ENVELOPPE).alias("e")).select("e.*")
    fen = agreger(ev)

    vers_kafka = (fen.select(F.col("run_id").alias("key"), F.to_json(F.struct("*")).alias("value"))
                  .writeStream.format("kafka").option("kafka.bootstrap.servers", KAFKA).option("topic", SORTIE)
                  .option("checkpointLocation", f"{CHECKPOINT}/kafka").outputMode("update").trigger(processingTime="5 seconds").start())
    console = (fen.writeStream.format("console").option("truncate", "false").option("numRows", 50)
               .option("checkpointLocation", f"{CHECKPOINT}/console").outputMode("update").trigger(processingTime="5 seconds").start())
    print(f"Spark écoute {ENTREE} sur {KAFKA}, fenêtres de {FENETRE}, publie dans {SORTIE}", flush=True)
    spark.streams.awaitAnyTermination()
    vers_kafka.stop(); console.stop()


if __name__ == "__main__":
    main()

"""Ce que les deux jobs Spark partagent : la session et le schéma de l'enveloppe alerte.event/1 (docs/evenements.md).
Le champ data change selon le type d'événement : on le garde en texte JSON brut (StringType), et chaque calcul n'en
extrait que les champs qui l'intéressent (from_json avec un petit schéma, ou get_json_object)."""
from pyspark.sql import SparkSession
from pyspark.sql import types as T

ENVELOPPE = T.StructType([
    T.StructField("schema", T.StringType()), T.StructField("run_id", T.StringType()), T.StructField("seq", T.LongType()),
    T.StructField("type", T.StringType()), T.StructField("t_sim", T.DoubleType()), T.StructField("t_proc", T.LongType()),
    T.StructField("agent", T.StringType()), T.StructField("data", T.StringType()),
])


def session(nom, **conf):
    b = SparkSession.builder.appName(nom).config("spark.sql.session.timeZone", "UTC").config("spark.sql.shuffle.partitions", "16")
    for k, v in conf.items():
        b = b.config(k, v)
    s = b.getOrCreate()
    s.sparkContext.setLogLevel("WARN")
    return s

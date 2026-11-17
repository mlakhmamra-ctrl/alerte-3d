"""Jobs Spark (spark/) sur le run d'exemple versionné : les tables d'analyse doivent retrouver le bilan écrit par la simulation
(run.end), et l'agrégation du streaming doit compter chaque départ et chaque arrivée une fois. Sauté sans PySpark ou sans Java."""
import os
import shutil
import sys

import pytest
from conftest import RACINE

pytest.importorskip("pyspark")
if not (shutil.which("java") or os.getenv("JAVA_HOME")):
    pytest.skip("Spark a besoin de Java", allow_module_level=True)

SPARK = os.path.join(RACINE, "spark")
if SPARK not in sys.path:
    sys.path.insert(0, SPARK)
EXEMPLE = os.path.join(RACINE, "data", "runs", "exemple_regles_pont_ferme.jsonl")


@pytest.fixture(scope="module")
def spark(tmp_path_factory):
    from commun import session
    d = tmp_path_factory.mktemp("runs", numbered=False)
    shutil.copy(EXEMPLE, d)                      # un dossier « runs » (comme data/runs) qui ne contient que le run d'exemple
    s = session("alerte-tests", **{"spark.master": "local[2]", "spark.ui.enabled": "false"})
    s.dossier = str(d)
    yield s
    s.stop()


def test_analyse_batch_retrouve_le_bilan_du_run(spark):
    import analyse_spark as a
    ev = a.lire(spark, [spark.dossier])
    runs = a.table_runs(ev)
    r = runs.collect()
    assert len(r) == 1 and r[0]["habitants"] == 12 and r[0]["habitants_llm"] == 0
    assert r[0]["au_refuge_pct"] == pytest.approx(91.7) and r[0]["depart_moyen_min"] == pytest.approx(21.3)
    assert r[0]["config"].startswith("pont_ferme")             # hors dossier d'étude : configuration reconstruite depuis run.end

    hab = a.table_habitants(ev, runs)
    assert hab.count() == 12
    assert hab.filter("issue = 'arrived'").count() == 11        # même bilan que indicators.safe

    prof = {(x["critere"], x["valeur"]): x for x in a.table_par_profil(hab).collect()}
    assert sum(x["habitants_runs"] for (c, _), x in prof.items() if c == "Moteur de décision") == 12

    vol = {x["type"]: x["evenements"] for x in a.table_volume(ev).collect()}
    assert vol["agent.depart"] == 12 and vol["run.end"] == 1


def test_agregation_du_flux_par_fenetre(spark):
    from commun import ENVELOPPE
    from flux_spark import agreger
    ev = spark.read.schema(ENVELOPPE).json(spark.dossier)
    fen = agreger(ev, "10 seconds", "30 seconds").collect()
    assert fen and all(f["run_id"] == fen[0]["run_id"] for f in fen)
    assert sum(f["departs"] for f in fen) == 12 and sum(f["arrivees"] for f in fen) == 11
    assert sum(f["decisions_llm"] for f in fen) == 0              # run 100 % règles
    assert max(f["heure_simulee"] for f in fen) == "10:00"

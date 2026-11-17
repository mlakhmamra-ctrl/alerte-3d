"""Non-régression : la simulation à règles, en mode sync, doit donner exactement les mêmes événements pour la même graine.

Chaque scénario est lancé comme dans l'étude des paramètres (sans Kafka, sans LLM, mémoire en RAM), puis on calcule une
empreinte de tous ses événements (sans run_id, t_proc, wall_ms ni metrics, qui dépendent de l'horloge réelle).
L'empreinte est comparée à celle enregistrée dans empreintes.json :
  - un refactoring ne doit changer AUCUNE empreinte ;
  - un changement voulu de comportement se valide en régénérant : MAJ_EMPREINTES=1 python -m pytest tests
PYTHONHASHSEED est fixé à une valeur différente à chaque test : l'ordre des set ne doit pas changer le résultat."""
import glob
import hashlib
import json
import os
import subprocess
import sys

import pytest
from conftest import SIM

FICHIER = os.path.join(os.path.dirname(__file__), "empreintes.json")
BASE = {"KAFKA": "", "BATCH": "1", "TIME_SCALE": "0", "MODE": "sync", "LLM_AGENTS": "none", "MEMOIRE": "ram",
        "RAG_MODE": "lexical", "VILLAGEOIS_FILE": "/nonexistent", "MONDE": "etendu", "WORLD_SCALE": "1"}
SCENARIOS = {
    "reference": {"SCENARIO": "pont_ferme", "CRUE": "rapide", "UPDATE_AT": "09:05", "RUMOR_AT": "09:20", "N_AGENTS": "100", "SEED": "1"},
    "pont_ouvert_crue_lente": {"SCENARIO": "pont_ouvert", "CRUE": "lente", "UPDATE_AT": "09:10", "RUMOR_AT": "09:20", "N_AGENTS": "60", "SEED": "4"},
    "eclair_panne_rumeur_tot": {"SCENARIO": "pont_ferme", "CRUE": "eclair", "UPDATE_AT": "09:01", "RUMOR_AT": "09:03",
                                "PANNE_RESEAU": "09:03", "N_AGENTS": "200", "SEED": "2"},
    "plan_de_la_fiche": {"MONDE": "fiche", "SCENARIO": "pont_ferme", "CRUE": "rapide", "UPDATE_AT": "09:10", "RUMOR_AT": "09:20",
                         "N_AGENTS": "12", "SEED": "7"},
}


def empreinte(fichier):
    h, n = hashlib.sha256(), 0
    with open(fichier, encoding="utf-8") as f:
        for ligne in f:
            e = json.loads(ligne)
            if e["type"] == "metrics":
                continue
            for k in ("run_id", "t_proc", "seq"):
                e.pop(k)
            e["data"].pop("wall_ms", None)
            h.update(json.dumps(e, sort_keys=True, ensure_ascii=False).encode())
            n += 1
    return {"evenements": n, "sha256": h.hexdigest()}


def lancer(nom, dossier, hashseed):
    env = {**os.environ, **BASE, **SCENARIOS[nom], "RUNS_DIR": str(dossier), "PYTHONHASHSEED": str(hashseed), "PYTHONIOENCODING": "utf-8",
           "PANNE_RESEAU": SCENARIOS[nom].get("PANNE_RESEAU", "none")}
    r = subprocess.run([sys.executable, "main.py"], cwd=SIM, env=env, capture_output=True, text=True, encoding="utf-8", timeout=600)
    assert r.returncode == 0, r.stderr[-2000:]
    (run,) = glob.glob(os.path.join(dossier, "*.jsonl"))
    return empreinte(run)


def lire():
    if not os.path.exists(FICHIER):
        return {}
    with open(FICHIER, encoding="utf-8") as f:
        return json.load(f)


@pytest.mark.parametrize("nom", list(SCENARIOS))
def test_meme_graine_memes_evenements(nom, tmp_path):
    obtenu = lancer(nom, tmp_path, hashseed=list(SCENARIOS).index(nom) * 101 + 13)
    if os.getenv("MAJ_EMPREINTES"):
        tout = lire()
        tout[nom] = obtenu
        with open(FICHIER, "w", encoding="utf-8") as f:
            json.dump(dict(sorted(tout.items())), f, indent=1)
        pytest.skip("empreinte enregistrée")
    attendu = lire().get(nom)
    assert attendu, f"pas d'empreinte pour {nom} : lancer MAJ_EMPREINTES=1 python -m pytest tests"
    assert obtenu == attendu, f"le run {nom} a changé : comportement modifié (voulu ? régénérer les empreintes)"

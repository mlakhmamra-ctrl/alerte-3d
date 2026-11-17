"""Test de fumée du chemin LLM, sans GPU ni modèle : tools/fake_ollama.py imite Ollama (et répond parfois mal, exprès).
Vérifie que le graphe passe bien par se_souvenir -> decider -> croire, que les réponses invalides sont rattrapées par les règles
(incidents tracés, jamais d'exception d'agent) et que le run va jusqu'au bout. Pas d'empreinte : les réponses LLM arrivent
en parallèle, leur ordre dépend de l'horloge réelle."""
import glob
import json
import os
import socket
import subprocess
import sys
import time

import pytest
from conftest import RACINE, SIM
from test_reproductibilite import BASE


def port_libre():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def faux_ollama():
    port = port_libre()
    env = {**os.environ, "PORT": str(port), "FAKE_LATENCY": "0.02", "FAKE_BAD_RATE": "0.2"}
    p = subprocess.Popen([sys.executable, os.path.join(RACINE, "tools", "fake_ollama.py")], env=env,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(100):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=.1).close()
            break
        except OSError:
            time.sleep(.05)
    yield f"http://127.0.0.1:{port}"
    p.kill()


def test_agents_llm_avec_faux_ollama(faux_ollama, tmp_path):
    env = {**os.environ, **BASE, "OLLAMA_URL": faux_ollama, "LLM_AGENTS": "A04,A05,A06,A11", "N_AGENTS": "30", "SEED": "3",
           "UPDATE_AT": "09:05", "T_END": "09:30", "RUNS_DIR": str(tmp_path), "PYTHONIOENCODING": "utf-8", "LLM_BACKENDS": "local"}
    r = subprocess.run([sys.executable, "main.py"], cwd=SIM, env=env, capture_output=True, text=True, encoding="utf-8", timeout=600)
    assert r.returncode == 0, r.stderr[-2000:]
    (run,) = glob.glob(os.path.join(tmp_path, "*.jsonl"))
    with open(run, encoding="utf-8") as f:
        evs = [json.loads(ligne) for ligne in f]
    pensees = [e["data"] for e in evs if e["type"] == "agent.think"]
    llm = [p for p in pensees if p["decider"] in ("big", "small")]
    assert llm, "aucune décision prise par un LLM"
    assert all(p["chemin"] == ["observer", "se_souvenir", "decider", "croire", "agir"] for p in llm)
    assert all(p["retrieved"] for p in llm), "le RAG n'a rien retrouvé"
    secours = [p for p in pensees if p["chemin"][:3] == ["observer", "se_souvenir", "decider"] and p["decider"] == "rule"]
    fin = next(e["data"] for e in evs if e["type"] == "run.end")
    assert fin["calls_total"] > 0
    assert "agent_exception" not in fin["incidents_by_kind"]
    if fin["incidents_by_kind"].get("unparsable") or fin["incidents_by_kind"].get("role_exit"):
        assert secours, "une réponse invalide doit être rattrapée par les règles (A4)"

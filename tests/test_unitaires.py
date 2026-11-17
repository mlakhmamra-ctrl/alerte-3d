"""Tests unitaires des briques sans effet de bord : crue, rues, indicateurs, profils, RAG lexical, garde-fous du LLM, contrat d'événements."""
import asyncio
import importlib.util
import os
import random

import pytest
import world
from conftest import RACINE
from indicators import Indicators
from llm import CAUTIOUS, DRIFT
from profiles import FICHE, add_batch, apply_overrides, extra_llm_ids, population
from rag import RAG, stem, toks
from corpus import DOCS


# ---------- monde ----------
def test_heures():
    assert world.t_of("09:20") == 560
    assert world.hm(560.9) == "09:20"
    assert world.hm(-5) == "00:00"


@pytest.mark.parametrize("crue", list(world.CRUES))
def test_crue_monte_sans_redescendre_jusqu_au_pic(crue):
    f = world.Flood(0.75, crue)
    niveaux = [f.level(t) for t in range(540, 600)]
    apres_debordement = [n for t, n in zip(range(540, 600), niveaux, strict=True) if t >= f.t_bank]
    assert apres_debordement == sorted(apres_debordement)
    assert max(niveaux) == pytest.approx(f.peak)


def test_crue_inconnue_devient_rapide():
    assert world.Flood(0.75, "tsunami").kind == "rapide"


def test_crue_eclair_plus_rapide_que_lente():
    assert world.Flood(0.75, "eclair").info()["rate_max_cm_min"] > world.Flood(0.75, "lente").info()["rate_max_cm_min"]


def test_on_marche_au_sec_sur_le_pont():
    f = world.Flood(0.75, "rapide")
    assert f.depth(0, world.RIVER_Z, 600) == 0.0


@pytest.mark.parametrize(("profondeur", "facteur"), [(0, 1.0), (0.1, 0.7), (0.3, 0.45), (0.4, 0.25), (0.5, 0.0), (2, 0.0)])
def test_ralentissement_dans_l_eau(profondeur, facteur):
    assert world.speed_factor(profondeur) == facteur


def test_chemin_par_le_pont_puis_detour_quand_il_est_ferme():
    rues = world.Streets()
    p, d = rues.path("PLACE", "GYM")
    assert world.via_of(p) == "pont_central"
    p2, d2 = rues.path("PLACE", "GYM", {world.ekey(*world.BRIDGE)})
    assert world.via_of(p2) == "passage_hauteurs" and d2 > d


def test_sans_escalier_ni_pont_aucun_chemin_vers_le_gymnase():
    p, d = world.Streets().path("PLACE", "GYM", {world.ekey(*world.BRIDGE)}, avoid_stairs=True)
    assert p is None and d == float("inf")


def test_maison_reliee_au_reseau_de_rues():
    rues = world.Streets()
    maison = rues.add_home("A99", -44, 36)
    assert rues.path(maison, "ECOLE")[0] is not None


# ---------- profils ----------
def test_population_de_la_fiche():
    pop = population(12, {"A04"}, random.Random(1))
    assert [p["id"] for p in pop] == [f"A{i:02d}" for i in range(1, 13)]
    assert {p["id"] for p in pop if p["engine"] != "rule"} == {"A04"}


def test_population_generee_contacts_valides():
    pop = population(100, set(), random.Random(1))
    ids = {p["id"] for p in pop}
    assert len(pop) == 100
    assert all(c in ids and c != p["id"] for p in pop for c in p["contacts"])
    assert all(p["contacts"] for p in pop[12:])                    # personne n'est isolé parmi les voisins générés


def test_population_meme_graine_meme_resultat():
    assert population(80, set(), random.Random(3)) == population(80, set(), random.Random(3))


def test_extra_llm_ids():
    ids, big = extra_llm_ids(100, 16)
    assert len(ids) == 16 and len(set(ids)) == 16 and big == ids[::2]
    assert extra_llm_ids(12, 5) == ([], [])


def test_reglages_bornes_et_champs_inconnus_ignores():
    pop = population(12, set(), random.Random(1))
    apply_overrides(pop, {"A01": {"trust_off": 9, "mob": "x", "pirate": 1, "engine": "gpt", "contacts": ["A02", "A01", "Z9"], "relay": 3}})
    a = pop[0]
    assert a["trust_off"] == 5 and a["mob"] == FICHE[0]["mob"] and "pirate" not in a and a["engine"] == "rule"
    assert a["contacts"] == ["A02"] and a["relay"] == 1.0


def test_ajout_par_lot():
    pop = population(12, set(), random.Random(1))
    add_batch(pop, {"nombre": 10, "taille_foyer": 3, "moteur": "rule"}, random.Random(2), 1)
    assert len(pop) == 22 and len({p["id"] for p in pop}) == 22
    assert add_batch(pop, {"nombre": "beaucoup"}, random.Random(2), 2) is pop and len(pop) == 22


# ---------- indicateurs ----------
def ev(typ, agent=None, t=545.0, **data):
    return {"type": typ, "agent": agent, "t_sim": t, "data": data}


def test_contexte_commun_et_arrivees():
    ind = Indicators()
    ind.feed(ev("run.start", n_agents=2, refuges=[{"id": "gymnase", "cap": 12}], agents=[{"famille": "F1"}, {"famille": "F2"}]))
    ind.feed(ev("world.truth", bridge="ferme", danger=True, refuges=["gymnase"]))
    out = ind.feed(ev("agent.belief", "A01", bridge="ferme", refuge="gymnase", danger=True, risk=90))
    assert out["common_context"] == 0.5 and out["exact_bridge"] == 0.5
    ind.feed(ev("agent.depart", "A01", t=550.0, refuge="gymnase", via="passage_hauteurs", path=[]))
    out = ind.feed(ev("agent.arrive", "A01", t=556.0, refuge="gymnase"))
    assert out["safe"] == 1 and out["depart_delay_avg"] == 10.0 and out["refuge_load"]["gymnase"] == [1, 12]


def test_world_tick_ne_publie_pas_d_indicateur():
    ind = Indicators()
    assert ind.feed(ev("world.tick", level=0, agents=[{"id": "A01", "wet": 2.0}])) is None


def test_relais_de_rumeur_compte_jusqu_a_la_correction():
    ind = Indicators()
    ind.feed(ev("run.start", n_agents=1, refuges=[], agents=[]))
    ind.feed(ev("rumor", text="", claim="ferme", targets=["A01"], msg_ids=["m1"]))
    ind.feed(ev("agent.say", "A01", kind="relais", msg_id="m2", parent="m1", origin="anonyme", canaux={}))
    ind.feed(ev("agent.say", "A02", kind="correction", msg_id="m3", parent="m2", origin="mairie", canaux={}))
    out = ind.feed(ev("agent.say", "A03", kind="relais", msg_id="m4", parent="m2", origin="anonyme", canaux={}))
    assert out["rumor_relays_before_correction"] == 2 and out["rumor_corrected"]


# ---------- RAG lexical ----------
def test_normalisation():
    assert stem("inondations") == "inond"
    assert "pont" in toks("Le Pont est FERMÉ") and "le" not in toks("le pont")


def test_rag_lexical_trouve_le_pont():
    kb = RAG(DOCS, mode="lexical")
    res = asyncio.run(kb.search("le pont central est-il fermé ?", 3))
    assert res and res[0]["doc"]["site"] == "pont"
    asyncio.run(kb.search("le pont central est-il fermé ?", 3))
    assert kb.hits == 1                                             # deuxième fois : depuis le cache


# ---------- garde-fous du LLM ----------
@pytest.mark.parametrize("txt", ["En tant qu'IA je ne peux pas", "As an AI language model", "je suis un assistant"])
def test_sortie_de_role_detectee(txt):
    assert DRIFT.search(txt)


def test_posture_d_assistant_detectee():
    assert CAUTIOUS.search("Je ne suis pas en mesure de répondre, appelez le 112")
    assert not DRIFT.search("Je pars au gymnase par le passage, la mairie l'a dit à 09:05.")


# ---------- contrat d'événements (passerelle) ----------
def test_contrat_evenements():
    pytest.importorskip("websockets")
    pytest.importorskip("aiokafka")
    spec = importlib.util.spec_from_file_location("passerelle", os.path.join(RACINE, "gateway", "main.py"))
    passerelle = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(passerelle)
    valid = passerelle.valid
    bon = {"schema": "alerte.event/1", "run_id": "r", "seq": 1, "type": "agent.arrive", "t_sim": 560, "t_proc": 0, "data": {"refuge": "gymnase"}}
    assert valid(bon)
    assert not valid({**bon, "data": {}})
    assert not valid({**bon, "type": "agent.teleport"})
    assert not valid({**bon, "schema": "alerte.event/2"})

"""Cycle de décision d'un habitant, en graphe LangGraph. Un nœud = une étape, une arête conditionnelle = un choix :

    START ──► observer ──┬── rien à décider (à l'abri, en marche...) ─────────────────────────────► END
                         ├── agent à règles ─────────────────────────────► regles ──► agir ──► END
                         └── agent LLM ──► se_souvenir ──► decider ──┬── réponse invalide ──┘ ▲
                                           (RAG : souvenirs,          └── réponse valide ──► croire
                                            documents, groupe)

- observer    : lit les messages déjà arrivés ; un habitant bloqué cherche un autre chemin.
- se_souvenir : le RAG (PostgreSQL, ou la mémoire en RAM en secours) retrouve souvenirs, documents et ce que dit le quartier.
- decider     : le LLM répond (croyances, risque, action, message) ; sa réponse est validée (sim/agents.py, llm_decide).
- croire      : la réponse du LLM met à jour les croyances de l'habitant.
- regles      : décision par règles explicites ; c'est aussi le secours quand le LLM ne répond pas ou répond mal.
- agir        : l'action est appliquée, l'événement agent.think part sur Kafka avec le chemin suivi dans le graphe.
Le graphe orchestre ; les étapes sont les méthodes de l'agent. Il est compilé une fois et partagé par tous les habitants."""
import operator
from typing import Annotated, Any, Optional, TypedDict
from langgraph.graph import END, START, StateGraph

class Etat(TypedDict, total=False):
    agent: Any                     # l'habitant (sim/agents.py)
    monde: Any                     # la simulation (World, sim/main.py)
    chercher: Any                  # la fonction de RAG du monde
    t_info: float                  # heure de la réflexion : rien de plus récent n'est utilisé
    suite: str                     # après observer : fin, regles ou llm
    decision: Optional[dict]
    decideur: str                  # rule, big ou small
    r: Optional[dict]              # ce que le RAG a retrouvé
    chemin: Annotated[list, operator.add]

async def observer(e):
    a, w = e["agent"], e["monde"]
    now = w.clock.now()
    a.inbox_read = [m for m in a.inbox if m["t_deliver"] <= now]
    for m in a.inbox_read:
        if m["t_deliver"] > now: w.stats.viol += 1                 # C6 : jamais un message utilisé avant d'être remis
    a.dirty = False; a.next_think = now + w.think_every; a.last_think = now
    suite = "regles" if a.kind == "rule" else "llm"
    if a.state in ("arrived", "sheltered", "victim") or (a.state == "walking" and a.kind != "rule"): suite = "fin"   # en marche, un agent LLM ne délibère pas (sobriété)
    elif a.state == "blocked":                                     # réessaie un autre chemin avec ce qu'il sait maintenant
        await a.reroute(w, now, None, "cherche un autre chemin")
        if a.state == "blocked": suite = "fin"
        elif a.state == "walking": suite = "regles"
    return {"t_info": now, "suite": suite, "chemin": ["observer"]}

async def se_souvenir(e):
    a = e["agent"]; a.thinking = True
    focus = "pont central fermé ouvert rumeur vérifier" if any(m["claims"].get("bridge") for m in a.inbox_read[-3:]) else "crue évacuation refuge itinéraire"
    return {"r": await e["chercher"](a, focus), "chemin": ["se_souvenir"]}

async def decider(e):
    a = e["agent"]
    try: d = await a.llm_decide(e["monde"], e["t_info"], e["r"])
    finally: a.thinking = False
    return {"decision": d, "decideur": a.kind if d else "rule", "chemin": ["decider"]}

async def croire(e):
    a, d = e["agent"], e["decision"]
    a.risk = max(0, min(100, d["risk"]))
    if d["bridge"] != a.bridge: a.bridge, a.bridge_cert = d["bridge"], .7
    a.danger = d["danger"]
    if d["refuge"] != "aucun": a.refuge = d["refuge"]
    return {"chemin": ["croire"]}

async def regles(e):
    a, w = e["agent"], e["monde"]
    return {"decision": await a.rule_decide(w, w.clock.now()), "decideur": "rule", "chemin": ["regles"]}

async def agir(e):
    chemin = e["chemin"] + ["agir"]
    await e["agent"].conclude(e["monde"], e["decision"], e.get("decideur", "rule"), e.get("r"), e["t_info"], chemin)
    return {"chemin": ["agir"]}

# Aiguillages en async : une fonction synchrone serait exécutée par LangGraph dans un pool de threads (aller-retour à chaque réflexion)
async def apres_observer(e): return e["suite"]
async def apres_decider(e): return "croire" if e.get("decision") else "regles"

def construire():
    g = StateGraph(Etat)
    for nom, f in (("observer", observer), ("se_souvenir", se_souvenir), ("decider", decider), ("croire", croire), ("regles", regles), ("agir", agir)):
        g.add_node(nom, f)
    g.add_edge(START, "observer")
    g.add_conditional_edges("observer", apres_observer, {"fin": END, "regles": "regles", "llm": "se_souvenir"})
    g.add_edge("se_souvenir", "decider")
    g.add_conditional_edges("decider", apres_decider, {"croire": "croire", "regles": "regles"})
    g.add_edge("croire", "agir")
    g.add_edge("regles", "agir")
    g.add_edge("agir", END)
    return g.compile()

GRAPHE = construire()

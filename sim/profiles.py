"""Les douze habitants de la fiche ALERTE (A01-A12), plus des habitants générés si N_AGENTS > 12.
Les paramètres de règle (seuil, temps de préparation, exigences) traduisent la phrase « situation » de la fiche en seuils explicites.
Ce sont des choix de maquette, documentés ici pour pouvoir être discutés et variés."""
import math, random

# engine : "rule", "big" (LLM grand) ou "small" (LLM compact). trust_off / trust_nb : 1..5. mob : coefficient de mobilité (1,00 = 1,2 m/s).
FICHE = [
    dict(id="A01", name="Lina", situation="Accompagne un enfant ; retarde son départ pour préparer un sac.",
         pos=(-44, 36), pos_fiche=(-42, 27), trust_off=4, trust_nb=3, mob=.95, contacts=["A02", "A05"], engine="rule",
         threshold=55, prep=6, needs_conf=1, local_obs=False, no_stairs=False, knows_detour=False, relay=.6, verifier=False),
    dict(id="A02", name="Yassine", situation="Commerçant ; hésite à quitter sa boutique sans confirmation.",
         pos=(-32, 14), pos_fiche=(-32, 18), trust_off=3, trust_nb=4, mob=1.0, contacts=["A01", "A03", "A06"], engine="rule",
         threshold=65, prep=4, needs_conf=2, local_obs=False, no_stairs=False, knows_detour=False, relay=.5, verifier=False),
    dict(id="A03", name="Nora", situation="Marche lentement ; cherche un itinéraire sans escalier.",
         pos=(-22, 30), trust_off=4, trust_nb=2, mob=.65, contacts=["A02", "A04"], engine="rule",
         threshold=50, prep=3, needs_conf=1, local_obs=False, no_stairs=True, knows_detour=False, relay=.4, verifier=False),
    dict(id="A04", name="Karim", situation="A déjà vécu une crue ; se souvient d'une rue inondée tôt.",
         pos=(-12, 17), trust_off=4, trust_nb=3, mob=1.0, contacts=["A03", "A07"], engine="big",
         threshold=45, prep=3, needs_conf=1, local_obs=False, no_stairs=False, knows_detour=True, relay=.8, verifier=True,
         memory=["Crue de 2019 : la rue des Quais, côté ouest près de l'école, a été inondée la première, avant même l'alerte officielle."]),
    dict(id="A05", name="Salma", situation="Vérifie l'émetteur et la date avant de relayer une alerte.",
         pos=(-4, 33), trust_off=5, trust_nb=2, mob=1.0, contacts=["A01", "A08"], engine="big",
         threshold=50, prep=3, needs_conf=1, local_obs=False, no_stairs=False, knows_detour=False, relay=.5, verifier=True),
    dict(id="A06", name="Adam", situation="Relie plusieurs groupes ; partage rapidement les messages reçus.",
         pos=(5, 21), trust_off=3, trust_nb=4, mob=1.05, contacts=["A02", "A07", "A09"], engine="small",
         threshold=55, prep=2, needs_conf=1, local_obs=False, no_stairs=False, knows_detour=False, relay=.95, verifier=False),
    dict(id="A07", name="Maya", situation="Aide un parent âgé ; ne part qu'avec lui.",
         pos=(-15, 29), trust_off=3, trust_nb=4, mob=.80, contacts=["A04", "A06", "A10"], engine="rule",
         threshold=50, prep=8, needs_conf=1, local_obs=False, no_stairs=True, knows_detour=False, relay=.6, verifier=False),
    dict(id="A08", name="Rami", situation="Doute des alertes ; attend une observation locale vérifiable.",
         pos=(-2, 20), trust_off=2, trust_nb=3, mob=1.0, contacts=["A05", "A09"], engine="rule",
         threshold=60, prep=3, needs_conf=1, local_obs=True, no_stairs=False, knows_detour=False, relay=.3, verifier=False),
    dict(id="A09", name="Inès", situation="Travaille au café ; veut savoir si le passage central reste ouvert.",
         pos=(10, 24), trust_off=3, trust_nb=4, mob=1.0, contacts=["A06", "A08", "A10"], engine="rule",
         threshold=55, prep=3, needs_conf=1, local_obs=False, no_stairs=False, knows_detour=False, relay=.6, verifier=False, wants_bridge_info=True),
    dict(id="A10", name="Omar", situation="Connaît les rues secondaires et le détour par les hauteurs.",
         pos=(22, 18), trust_off=3, trust_nb=3, mob=1.10, contacts=["A07", "A09", "A11"], engine="rule",
         threshold=55, prep=2, needs_conf=1, local_obs=False, no_stairs=False, knows_detour=True, relay=.6, verifier=False),
    dict(id="A11", name="Hiba", situation="Suit le groupe de voisinage ; recoupe les messages quand le temps le permet.",
         pos=(32, 27), trust_off=4, trust_nb=5, mob=1.0, contacts=["A10", "A12"], engine="small",
         threshold=50, prep=3, needs_conf=1, local_obs=False, no_stairs=False, knows_detour=False, relay=.8, verifier=True),
    dict(id="A12", name="Sami", situation="Attend une seconde confirmation avant de quitter son domicile.",
         pos=(40, 16), trust_off=3, trust_nb=2, mob=.90, contacts=["A04", "A11"], engine="rule",
         threshold=55, prep=3, needs_conf=2, local_obs=False, no_stairs=False, knows_detour=False, relay=.4, verifier=False),
]

FREE_SPOTS = [(-28, 36), (-8, 36), (8, 36), (24, 36), (44, 36), (-10, 25), (26, 25), (44, 27), (-25, 18), (24, 13),
              (44, 13), (-15, 13), (30, 31), (-30, 31), (12, 31), (-6, 28), (48, 31), (-24, 25), (38, 19), (6, 28)]
# MONDE=etendu : portes des maisons des quartiers Ouest et Est (rive sud), face à la rue des Quais (z = 14), à la rue du Sud (z = 34)
# ou, aux deux bouts du monde, à la rue de la Digue (x = -105) et à la rue des Vergers (x = 105)
NEW_SPOTS = [(-98, 14), (-90, 14), (-82, 14), (-66, 14), (-98, 34), (-90, 34), (-82, 34), (-66, 34), (-110, 14), (-110, 24), (-110, 34),
             (58, 14), (66, 14), (58, 34), (66, 34), (110, 14), (110, 24), (110, 34)]

from world import sc, EXTENDED, zone_refuge

def extra_llm_ids(n, k):
    """k habitants générés (au-delà des 12 de la fiche) qui pensent avec un LLM, répartis régulièrement dans la population.
    Renvoie (tous, ceux qui utilisent le grand modèle) : un sur deux prend le grand modèle, pour garder la comparaison A5."""
    gen = n - 12
    if gen <= 0 or k <= 0: return [], []
    ids = [f"A{12 + int(i * gen / min(k, gen)) + 1:02d}" for i in range(min(k, gen))]
    return ids, ids[::2]

def population(n, llm_ids, rng: random.Random, big_ids=("A04", "A05"), small_ids=("A06", "A11")):
    """n habitants. llm_ids : identifiants qui pensent avec un LLM (les autres suivent leurs règles).
    Au-delà de 12, on génère des voisins inspirés des profils de la fiche (C2 : effectif fixé au lancement).
    Ils vivent en foyers : plusieurs habitants par maison, répartis sur les emplacements libres."""
    out = []
    for p in FICHE[:n]:
        q = dict(p); q["memory"] = list(p.get("memory", []))
        if q["id"] not in llm_ids: q["engine"] = "rule"
        elif q["engine"] == "rule": q["engine"] = "small"           # un id ajouté à LLM_AGENTS pense avec le petit modèle
        out.append(q)
    spots = FREE_SPOTS + (NEW_SPOTS if EXTENDED else [])
    home = {}
    for k in range(12, n):
        base = FICHE[k % 12]; aid = f"A{k + 1:02d}"; home[aid] = (k - 12) % len(spots)
        x, z = spots[home[aid]]; x += rng.uniform(-1.5, 1.5)
        q = dict(base); q.update(id=aid, name=f"Voisin {k + 1}", pos=(round(x, 1), z), engine="big" if aid in llm_ids and aid in big_ids else "small" if aid in llm_ids else "rule",
                                 situation=f"{base['situation']} (profil généré à partir de {base['id']})", memory=[], zone=zone_refuge(x),
                                 threshold=base["threshold"] + rng.randint(-8, 8), mob=round(base["mob"] * rng.uniform(.9, 1.1), 2))
        out.append(q)
    # Contacts des habitants générés : les autres membres de leur foyer (3 au plus) et le voisin le plus proche d'une autre maison.
    # Le réseau de la fiche est orienté (ex. A12 écrit à A04, mais A04 n'écrit pas à A12) : on le garde, et chaque habitant de la fiche
    # écrit en plus à 2 voisins générés au plus, sinon les nouvelles du réseau de la fiche ne sortiraient jamais vers les foyers générés.
    gen = out[12:]; added = {}
    for o in gen:
        fam = [g["id"] for g in gen if g["id"] != o["id"] and home[g["id"]] == home[o["id"]]][:3]
        others = [x for x in out if x["id"] != o["id"] and home.get(x["id"]) != home[o["id"]]]
        near = min(others, key=lambda x: math.dist(x["pos"], o["pos"]))["id"] if others else None
        o["contacts"] = fam + ([near] if near else [])
    by_id = {o["id"]: o for o in out}
    for o in gen:                                               # liens retour, sinon personne n'écrirait aux voisins générés
        for c in o["contacts"]:
            other = by_id[c]
            if o["id"] in other["contacts"] or (c not in home and added.get(c, 0) >= 2): continue
            other["contacts"] = other["contacts"] + [o["id"]]
            if c not in home: added[c] = added.get(c, 0) + 1
    for o in out: o["pos"] = sc(o["pos"])                       # WORLD_SCALE : même plan, écarté (1 = fiche)
    for o in out: o["contacts"] = [c for c in o["contacts"] if c in by_id]
    return out

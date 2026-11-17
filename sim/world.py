"""Géographie de Rive-Claire (fiche ALERTE) : relief, crue, rues, franchissements, refuges.
Repère de la fiche : 1 unité = 1 m, X d'ouest en est (-60..60), Z de -40 à +40, Y = hauteur.
Rive sud (Quais) = Z positifs, rive nord (Hauteurs, Gymnase) = Z négatifs, comme dans les coordonnées de la fiche.
Tout est déterministe : le même scénario donne la même crue, ce qui permet de comparer les runs."""
import functools, heapq, math, os
from collections import OrderedDict

# WORLD_SCALE (écart à la fiche si ≠ 1) : écarte rues, maisons, refuges et relief d'un facteur S. Les objets gardent leur taille
# (rivière de 14 m, ponts de 18 et 24 m, bâtiments, habitants) ; seuls les trajets s'allongent. 1 = dimensions exactes de la fiche.
S = float(os.getenv("WORLD_SCALE", "1").split("#")[0].strip() or 1)
def sc(p): return (round(p[0] * S, 2), round(p[1] * S, 2))
# MONDE=etendu (défaut) : le quartier de la fiche reste au centre, à l'identique, et on ajoute un quartier Ouest et un quartier Est
# (rive sud, 240 m de large au lieu de 120), la rue Haute prolongée sur la rive nord et trois refuges de plus. MONDE=fiche : plan exact.
EXTENDED = os.getenv("MONDE", "etendu").split("#")[0].strip() != "fiche"
HALF_X = 120.0 if EXTENDED else 60.0

X0, X1, Z0, Z1 = -HALF_X * S, HALF_X * S, -40.0 * S, 40.0 * S
RIVER_Z, RIVER_W = -2.0 * S, 14.0                  # axe et largeur de la rivière
RIVER_S, RIVER_N = RIVER_Z + RIVER_W / 2, RIVER_Z - RIVER_W / 2   # berge sud (+5) et berge nord (-9)
BED_Y = -1.5                                       # fond du lit ; niveau initial = fond + 0,15 m (profondeur visuelle de la fiche)
CELL = 5.0                                         # cellules de crue 5 m x 5 m
NX, NZ = int((X1 - X0) / CELL), int((Z1 - Z0) / CELL)          # 24 x 16 cellules

def t_of(hhmm):                                    # "09:20" -> minutes depuis minuit
    h, m = hhmm.split(":"); return int(h) * 60 + int(m)
def hm(t): t = max(0.0, t); return f"{int(t // 60):02d}:{int(t % 60):02d}"

@functools.lru_cache(maxsize=1 << 16)                             # le relief ne change pas : même point, même altitude
def ground(x, z):
    """Altitude du sol (m). Lit de la rivière creusé, rive sud basse (les Quais), rive nord qui monte vers les Hauteurs.
    Une ancienne boucle de la rivière rend le secteur de l'école plus bas : il est inondé plus tôt (dilemme : refuge proche mais exposé)."""
    d = abs(z - RIVER_Z)
    if d < RIVER_W / 2 - 1.5: return BED_Y                       # lit
    if d < RIVER_W / 2: return BED_Y + (1.5 - (RIVER_W / 2 - d)) / 1.5 * (0.0 - BED_Y)  # talus
    if z > RIVER_Z:                                              # rive sud : 0 m au quai, ~0,7 m en haut du quartier
        g = 0.02 * (z - RIVER_S) / S
        g -= 0.22 * math.exp(-((x / S + 30) ** 2 + (z / S - 14) ** 2) / 260)      # ancienne boucle, côté école
        g -= 0.10 * math.exp(-((x / S - 18) ** 2 + (z / S - 10) ** 2) / 120)      # point bas près du café
        if EXTENDED: g += 0.30 * math.exp(-((x / S - 90) ** 2 + (z / S - 24) ** 2) / 300)   # butte du Moulin (Salle des Fêtes)
        return round(g, 3)
    d = RIVER_N - z
    g = 0.06 * (d if d <= 3.5 else 3.5 + (d - 3.5) / S)          # rive nord : monte vite vers les Hauteurs (pente adoucie si S > 1)
    g += 0.6 * math.exp(-((x / S + 40) ** 2 + (z / S + 28) ** 2) / 300)            # colline ouest (Passage des Hauteurs)
    return round(g, 3)

# CRUE : vitesse de montée de l'eau (paramètres de maquette). bank = débordement sur les Quais, peak = heure du pic,
# k = forme de la montée après le débordement (1 = régulière, > 1 = très rapide au début puis ralentie), mult = hauteur du pic.
CRUES = {
    "lente":  {"bank": "09:10", "peak": "09:40", "k": 1.2, "mult": 1.0, "nom": "crue lente : l'eau monte régulièrement, on a le temps de voir venir"},
    "rapide": {"bank": "09:06", "peak": "09:26", "k": 2.0, "mult": 1.0, "nom": "crue rapide : débordement brusque, l'eau monte de plusieurs cm par minute"},
    "eclair": {"bank": "09:03", "peak": "09:11", "k": 3.0, "mult": 1.25, "nom": "crue éclair : l'eau monte en quelques minutes, puis reste haute"},
}

class Flood:
    """Niveau d'eau Y(t). Avant le débordement, la rivière remplit son lit (fond + 0,15 m -> berges à 0 m).
    Ensuite elle déborde sur les Quais (étape 2 de la fiche) et monte jusqu'au pic du scénario, plus ou moins vite selon CRUE."""
    def __init__(s, peak, kind="rapide", t0=540.0):           # 540 = 09:00
        c = CRUES.get(kind, CRUES["rapide"]); s.kind = kind if kind in CRUES else "rapide"
        s.peak, s.t0, s.t_bank, s.t_peak, s.k = round(peak * c["mult"], 3), t0, t_of(c["bank"]), t_of(c["peak"]), c["k"]
        s.base = BED_Y + 0.15
    def level(s, t):
        if t <= s.t0: return s.base
        if t <= s.t_bank: return round(s.base * (1 - (t - s.t0) / (s.t_bank - s.t0)), 3)
        u = min(1.0, (t - s.t_bank) / (s.t_peak - s.t_bank))
        return round(s.peak * (1 - (1 - u) ** s.k), 3)
    def rate(s, t):
        """Vitesse de montée (cm par minute simulée) à l'instant t."""
        return round(max(0.0, s.level(t + .5) - s.level(t - .5)) * 100, 1)
    def info(s):
        steps = [s.t_bank + 1 + i for i in range(int(s.t_peak - s.t_bank))]      # après le débordement : ce que voient les habitants
        return {"type": s.kind, "nom": CRUES[s.kind]["nom"], "t_bank": s.t_bank, "t_peak": s.t_peak, "peak": s.peak,
                "rate_max_cm_min": max(s.rate(t) for t in steps)}
    def depth(s, x, z, t):
        if on_crossing(x, z): return 0.0                          # tablier du pont / passerelle : au-dessus de l'eau
        return max(0.0, s.level(t) - ground(x, z))
    def cells(s, t):                                              # profondeur (cm) par cellule, hors lit de la rivière
        L = s.level(t); out = []
        for k in range(NZ):
            for i in range(NX):
                x, z = X0 + (i + .5) * CELL, Z0 + (k + .5) * CELL
                out.append(0 if abs(z - RIVER_Z) < RIVER_W / 2 else int(round(max(0.0, L - ground(x, z)) * 100)))
        return out

PASS_X = -40.0 * S                                  # axe du Passage des Hauteurs
def on_crossing(x, z):
    return (abs(x) <= 2.75 and RIVER_Z - 9 <= z <= RIVER_Z + 9) or (abs(x - PASS_X) <= 2.25 and RIVER_Z - 13.5 <= z <= RIVER_Z + 10.5)

def speed_factor(depth):
    """Ralentissement dans l'eau (paramètre de maquette, pas une donnée réelle) ; >= 0,5 m : on ne passe plus."""
    if depth < 0.05: return 1.0
    if depth < 0.2: return 0.7
    if depth < 0.35: return 0.45
    if depth < 0.5: return 0.25
    return 0.0

def heightmap(step=2.5):                                          # envoyé à Godot pour construire le terrain
    nx, nz = int((X1 - X0) / step) + 1, int((Z1 - Z0) / step) + 1
    return {"step": step, "nx": nx, "nz": nz, "x0": X0, "z0": Z0,
            "h": [ground(X0 + i * step, Z0 + k * step) for k in range(nz) for i in range(nx)]}

# ---------- rues (graphe) ----------
# Noeuds : (x, z). Rues principales 6 m, rues locales 4,5 m. Les deux franchissements sont des arêtes particulières.
NODES = {
    # rive sud : rue des Quais (principale, z = 9), rue des Tilleuls (locale, z = 24), rue du Sud (locale, z = 37)
    "Q_W": (-50, 9), "Q_P": (-40, 9), "Q_20": (-20, 9), "Q_C": (0, 9), "Q_16": (16, 9), "Q_36": (36, 9), "Q_E": (50, 9),
    "T_20": (-20, 24), "T_C": (0, 24), "T_16": (16, 24), "T_36": (36, 24), "T_E": (50, 24),
    "S_W": (-50, 37), "S_20": (-20, 37), "S_C": (0, 37), "S_16": (16, 37), "S_36": (36, 37), "S_E": (50, 37),
    "W_25": (-50, 25),                                           # rue de l'Ouest : mène au parvis de l'école
    "PLACE": (2, 13),
    # franchissements
    "PC_S": (0, 7), "PC_N": (0, -11),                             # Pont Central (18 m) : recalé sur la rivière plus bas
    "PH_S": (-40, 8.5), "PH_N": (-40, -15.5),                     # Passage des Hauteurs (24 m, escaliers) : idem
    # rive nord : rue Haute (principale, z = -16)
    "H_W": (-40, -16), "H_C": (0, -16), "H_10": (10, -16),
    # refuges (centres des parvis, zones d'arrivée de 12 m)
    "ECOLE": (-53, 25), "GYM": (10, -27),
}
NODES_EXT = {
    # quartier Ouest (rive sud) : rue des Quais et rue du Sud prolongées, deux rues transversales, rue des Peupliers au milieu
    "Q_75W": (-75, 9), "Q_105W": (-105, 9), "P_75W": (-75, 24), "P_105W": (-105, 24), "S_75W": (-75, 37), "S_105W": (-105, 37),
    # quartier Est (rive sud) : rue des Quais, des Tilleuls et du Sud prolongées ; la Salle des Fêtes sur la butte du Moulin
    "Q_75E": (75, 9), "Q_90E": (90, 9), "Q_105E": (105, 9), "T_75E": (75, 24), "S_75E": (75, 37), "S_105E": (105, 37), "SALLE": (90, 19.5),
    # rive nord : rue Haute prolongée vers l'ouest (Église), rue de la Berge vers l'est (Collège)
    "H_75W": (-75, -16), "EGLISE": (-75, -28), "B_14": (14, -12), "B_70": (70, -12), "COLLEGE": (70, -25),
}
if EXTENDED: NODES.update(NODES_EXT)
NODES = {k: sc(v) for k, v in NODES.items()}
NODES.update({"PC_S": (0.0, RIVER_Z + 9), "PC_N": (0.0, RIVER_Z - 9), "PH_S": (PASS_X, RIVER_Z + 10.5), "PH_N": (PASS_X, RIVER_Z - 13.5)})
MAIN, LOCAL = 6.0, 4.5
EDGES = [  # (a, b, largeur, nom)
    ("Q_W", "Q_P", MAIN, "rue des Quais"), ("Q_P", "Q_20", MAIN, "rue des Quais"), ("Q_20", "Q_C", MAIN, "rue des Quais"),
    ("Q_C", "Q_16", MAIN, "rue des Quais"), ("Q_16", "Q_36", MAIN, "rue des Quais"), ("Q_36", "Q_E", MAIN, "rue des Quais"),
    ("Q_C", "PLACE", MAIN, "Grand-Rue"), ("PLACE", "T_C", MAIN, "Grand-Rue"), ("T_C", "S_C", MAIN, "Grand-Rue"),
    ("Q_W", "W_25", MAIN, "rue de l'Ouest"), ("W_25", "S_W", MAIN, "rue de l'Ouest"), ("W_25", "ECOLE", MAIN, "parvis de l'école"),
    ("T_20", "T_C", LOCAL, "rue des Tilleuls"), ("T_C", "T_16", LOCAL, "rue des Tilleuls"), ("T_16", "T_36", LOCAL, "rue des Tilleuls"), ("T_36", "T_E", LOCAL, "rue des Tilleuls"),
    ("S_W", "S_20", LOCAL, "rue du Sud"), ("S_20", "S_C", LOCAL, "rue du Sud"), ("S_C", "S_16", LOCAL, "rue du Sud"), ("S_16", "S_36", LOCAL, "rue du Sud"), ("S_36", "S_E", LOCAL, "rue du Sud"),
    ("Q_20", "T_20", LOCAL, "rue du Moulin"), ("T_20", "S_20", LOCAL, "rue du Moulin"),
    ("Q_16", "T_16", LOCAL, "rue du Café"), ("T_16", "S_16", LOCAL, "rue du Café"),
    ("Q_36", "T_36", LOCAL, "rue des Saules"), ("T_36", "S_36", LOCAL, "rue des Saules"),
    ("Q_E", "T_E", LOCAL, "rue de l'Est"), ("T_E", "S_E", LOCAL, "rue de l'Est"),
    ("Q_C", "PC_S", MAIN, "Pont Central"), ("PC_S", "PC_N", 5.5, "Pont Central"), ("PC_N", "H_C", MAIN, "Pont Central"),
    ("Q_P", "PH_S", 4.5, "Passage des Hauteurs"), ("PH_S", "PH_N", 4.5, "Passage des Hauteurs"), ("PH_N", "H_W", 4.5, "Passage des Hauteurs"),
    ("H_W", "H_C", MAIN, "rue Haute"), ("H_C", "H_10", MAIN, "rue Haute"), ("H_10", "GYM", MAIN, "parvis du gymnase"),
]
EDGES_EXT = [
    ("Q_W", "Q_75W", MAIN, "rue des Quais"), ("Q_75W", "Q_105W", MAIN, "rue des Quais"),
    ("S_W", "S_75W", LOCAL, "rue du Sud"), ("S_75W", "S_105W", LOCAL, "rue du Sud"),
    ("Q_75W", "P_75W", LOCAL, "rue du Lavoir"), ("P_75W", "S_75W", LOCAL, "rue du Lavoir"),
    ("Q_105W", "P_105W", LOCAL, "rue de la Digue"), ("P_105W", "S_105W", LOCAL, "rue de la Digue"),
    ("P_75W", "P_105W", LOCAL, "rue des Peupliers"),
    ("Q_E", "Q_75E", MAIN, "rue des Quais"), ("Q_75E", "Q_90E", MAIN, "rue des Quais"), ("Q_90E", "Q_105E", MAIN, "rue des Quais"),
    ("T_E", "T_75E", LOCAL, "rue des Tilleuls"), ("S_E", "S_75E", LOCAL, "rue du Sud"), ("S_75E", "S_105E", LOCAL, "rue du Sud"),
    ("Q_75E", "T_75E", LOCAL, "rue du Moulin-Neuf"), ("T_75E", "S_75E", LOCAL, "rue du Moulin-Neuf"),
    ("Q_105E", "S_105E", LOCAL, "rue des Vergers"), ("Q_90E", "SALLE", MAIN, "parvis de la salle des fêtes"),
    ("H_W", "H_75W", MAIN, "rue Haute"), ("H_75W", "EGLISE", MAIN, "parvis de l'église"),
    ("H_10", "B_14", MAIN, "rue de la Berge"), ("B_14", "B_70", MAIN, "rue de la Berge"), ("B_70", "COLLEGE", MAIN, "parvis du collège"),
]
if EXTENDED: EDGES += EDGES_EXT
BRIDGE = ("PC_S", "PC_N")                                         # ferme quand le scénario le dit
STAIRS = ("PH_S", "PH_N")                                         # le Passage a des escaliers (contrainte de Nora)
# note : précision donnée aux agents LLM sur l'accès ; zone : partie du quartier pour laquelle la mairie désigne ce refuge
REFUGES = {"ecole": {"node": "ECOLE", "name": "École des Tilleuls", "cap": 7, "bank": "sud", "note": ""},
           "gymnase": {"node": "GYM", "name": "Gymnase des Hauteurs", "cap": 12, "bank": "nord", "note": "par le Pont Central ou le Passage des Hauteurs avec escaliers"}}
if EXTENDED:
    REFUGES.update({
        "salle": {"node": "SALLE", "name": "Salle des Fêtes du Moulin", "cap": 15, "bank": "sud", "note": "quartier Est, sur une butte"},
        "eglise": {"node": "EGLISE", "name": "Église Saint-Roch", "cap": 40, "bank": "nord", "note": "à l'ouest, par le Passage des Hauteurs ou le Pont Central"},
        "college": {"node": "COLLEGE", "name": "Collège du Plateau", "cap": 40, "bank": "nord", "note": "à l'est, par le Pont Central ou le Passage des Hauteurs"}})
REFUGE_R = 6.0                                                    # zone d'arrivée de 12 m de diamètre

def zone_refuge(x):
    """Refuge que la mairie désigne à un habitant généré d'après x, sa position sur le plan (avant WORLD_SCALE).
    Les 12 habitants de la fiche gardent le gymnase, comme dans la fiche."""
    if not EXTENDED: return "gymnase"
    return "eglise" if x < 0 else "college"

def ekey(a, b): return (a, b) if a < b else (b, a)

STAIRS_KEY = ekey(*STAIRS)
TREES_MAX = 512                                                   # arbres de plus courts chemins gardés en mémoire (LRU)

class Streets:
    def __init__(s):
        s.nodes = dict(NODES); s.adj = {}; s.meta = {}; s.trees = OrderedDict(); s.tree_hits = s.tree_miss = 0
        for a, b, w, nm in EDGES: s._link(a, b, w, nm)
    def _link(s, a, b, w=LOCAL, nm="allée"):
        (x1, z1), (x2, z2) = s.nodes[a], s.nodes[b]; d = math.hypot(x1 - x2, z1 - z2); k = ekey(a, b)
        s.adj.setdefault(a, []).append((b, d, k)); s.adj.setdefault(b, []).append((a, d, k)); s.meta[k] = (w, nm)
        s.trees.clear()                                           # le réseau change (maisons ajoutées au début) : chemins à recalculer
    def add_home(s, aid, x, z):
        """Relie la porte d'un habitant au point le plus proche d'une rue (une petite allée)."""
        best = None
        for (a, b), (_, nm) in list(s.meta.items()):
            if nm in ("Pont Central", "Passage des Hauteurs") or "parvis" in nm: continue
            (x1, z1), (x2, z2) = s.nodes[a], s.nodes[b]
            dx, dz = x2 - x1, z2 - z1; L2 = dx * dx + dz * dz or 1.0
            u = max(0.0, min(1.0, ((x - x1) * dx + (z - z1) * dz) / L2)); px, pz = x1 + u * dx, z1 + u * dz
            d = math.hypot(x - px, z - pz)
            if best is None or d < best[0]: best = (d, a, b, px, pz, u)
        _, a, b, px, pz, u = best; home, entry = f"H_{aid}", f"E_{aid}"
        s.nodes[home], s.nodes[entry] = (x, z), (round(px, 2), round(pz, 2))
        w, nm = s.meta[ekey(a, b)]
        s._link(entry, a, w, nm); s._link(entry, b, w, nm); s._link(home, entry, 2.0, "allée")
        return home
    def tree(s, src, blocked=frozenset(), avoid_stairs=False):
        """Dijkstra depuis src vers tous les nœuds : (distances, prédécesseurs). Un seul parcours sert pour tous les refuges ;
        les mêmes questions reviennent sans cesse (chaque réflexion, chaque pas de la vérité) : les arbres sont gardés (LRU).
        Mêmes chemins qu'un Dijkstra arrêté à l'arrivée : un nœud sorti du tas ne change plus de prédécesseur."""
        blocked = blocked if isinstance(blocked, frozenset) else frozenset(blocked)
        if avoid_stairs: blocked = blocked | {STAIRS_KEY}
        key = (src, blocked)
        t = s.trees.get(key)
        if t is not None: s.trees.move_to_end(key); s.tree_hits += 1; return t
        s.tree_miss += 1
        dist, prev, pq, adj, inf = {src: 0.0}, {}, [(0.0, src)], s.adj, math.inf
        pop, push = heapq.heappop, heapq.heappush
        while pq:
            d, u = pop(pq)
            if d > dist[u]: continue
            for v, L, k in adj.get(u, ()):
                if k in blocked: continue
                nd = d + L
                if nd < dist.get(v, inf): dist[v] = nd; prev[v] = u; push(pq, (nd, v))
        s.trees[key] = (dist, prev)
        if len(s.trees) > TREES_MAX: s.trees.popitem(last=False)
        return dist, prev
    def path(s, src, dst, blocked=frozenset(), avoid_stairs=False):
        """Plus court chemin de src à dst. blocked = arêtes que l'agent croit (ou sait) fermées. (None, inf) si impossible."""
        dist, prev = s.tree(src, blocked, avoid_stairs)
        if dst not in dist: return None, math.inf
        p = [dst]
        while p[-1] != src: p.append(prev[p[-1]])
        return p[::-1], dist[dst]
    def on_crossing_node(s, n): return n in (*BRIDGE, *STAIRS)       # sur un pont : pas de bâtiment où monter
    def length(s, p): return sum(math.dist(s.nodes[a], s.nodes[b]) for a, b in zip(p, p[1:])) if p else math.inf
    def export(s):
        return {"nodes": {k: [v[0], v[1]] for k, v in s.nodes.items()},
                "edges": [[a, b, w, nm] for (a, b), (w, nm) in s.meta.items()]}

def via_of(path):
    if not path: return "aucun"
    ks = {ekey(a, b) for a, b in zip(path, path[1:])}
    if ekey(*BRIDGE) in ks: return "pont_central"
    if ekey(*STAIRS) in ks: return "passage_hauteurs"
    return "sans_franchissement"

"""
Featurizer — die Glue-Schicht zwischen obs.ts (Dicts) und net.py (Tensoren).

obs.encodeVec liefert Dicts mit ROHEN Werten (Truppen in Millionen, Gold, Felder);
das Netz braucht feste, normierte Vektoren. Hier passiert die eine, kanonische
Umrechnung — Feld-Reihenfolge ist das Kopf-Layout, also fix und versioniert.

Reputation (§F) kommt aus einer externen Tabelle (trackerfront-Rang / Elo), gekeyt
auf (username, clan). Sie ist ein PRIOR mit neutralem Fallback 0.5 — das Netz muss
bei unbekannten Spielern funktionieren, deshalb NIE hart von ihr abhängig.
"""
from __future__ import annotations
import math

MAX_OPP = 24

# ── Normalisierer ──
def _log(x, s):          # log1p, auf ~[0,1] skaliert
    return math.log1p(max(0.0, float(x))) / s
def _clip01(x):
    return 0.0 if x < 0 else 1.0 if x > 1 else float(x)

# ── OWN: (Name, Funktion) in FESTER Reihenfolge = Kopf-Layout ──
OWN_FEATURES = [
    ("troopsRatio", lambda o: _clip01(o.get("troopsRatio", 0))),
    ("troops",      lambda o: _log(o.get("troops", 0), 16)),
    ("boardShare",  lambda o: _clip01(o.get("boardShare", 0))),
    ("gold",        lambda o: _log(o.get("gold", 0), 18)),
    ("tiles",       lambda o: _log(o.get("tiles", 0), 16)),
    ("borderLen",   lambda o: _log(o.get("borderLen", 0), 12)),
    ("allies",      lambda o: _clip01(o.get("allies", 0) / 8)),
    ("betrayals",   lambda o: _clip01(o.get("betrayals", 0) / 10)),
    ("traitor",     lambda o: float(o.get("traitor", 0))),
    ("isLeader",    lambda o: float(o.get("isLeader", 0))),
    ("boatsOut",    lambda o: _clip01(o.get("boatsOut", 0) / 3)),
    ("tickFrac",    lambda o: _clip01(o.get("tick", 0) / 15000)),
    ("aliveFrac",   lambda o: _clip01(o.get("alive", 0) / 64)),
    ("n_City",      lambda o: _log(o.get("n_City", 0), 6)),
    ("n_Port",      lambda o: _log(o.get("n_Port", 0), 6)),
    ("n_Factory",   lambda o: _log(o.get("n_Factory", 0), 6)),
    ("n_DefensePost", lambda o: _log(o.get("n_Defense Post", 0), 6)),
    ("n_MissileSilo", lambda o: _log(o.get("n_Missile Silo", 0), 6)),
    ("n_SAMLauncher", lambda o: _log(o.get("n_SAM Launcher", 0), 6)),
]
OWN_DIM = len(OWN_FEATURES)

# ── OPP je Gegner: (Name, Funktion). `_rep` wird separat injiziert (Reputation). ──
OPP_FEATURES = [
    ("troops",         lambda o: _log(o.get("troops", 0), 16)),
    ("gold",           lambda o: _log(o.get("gold", 0), 18)),
    ("tiles",          lambda o: _log(o.get("tiles", 0), 16)),
    ("ally",           lambda o: float(o.get("ally", 0))),
    ("sameTeam",       lambda o: float(o.get("sameTeam", 0))),
    ("traitor",        lambda o: float(o.get("traitor", 0))),
    ("human",          lambda o: float(o.get("human", 0))),
    ("bordersMe",      lambda o: float(o.get("bordersMe", 0))),
    ("contactShare",   lambda o: _clip01(o.get("contactShare", 0))),
    ("borderToMe",     lambda o: _clip01(o.get("borderToMe", 0))),
    ("borderToOther",  lambda o: _clip01(o.get("borderToOther", 0))),
    ("borderToUnowned",lambda o: _clip01(o.get("borderToUnowned", 0))),
    ("annexProof",     lambda o: float(o.get("annexProof", 0))),
    ("allyTicksLeft",  lambda o: _clip01(o.get("allyTicksLeft", 0))),
    ("isLeader",       lambda o: float(o.get("isLeader", 0))),
    ("hasSilo",        lambda o: float(o.get("hasSilo", 0))),
    ("hasSam",         lambda o: float(o.get("hasSam", 0))),
    ("inClan",         lambda o: float(o.get("inClan", 0))),
    ("reputation",     lambda o: _clip01(o.get("_rep", 0.5))),   # §F: Prior, Fallback 0.5
]
OPP_DIM = len(OPP_FEATURES)

# ── CONFIG/Modifier je SPIEL: das Regelwerk, in dem gespielt wird (aus info.config).
#    Ohne das sieht der Agent im gemischten Pool widerspruechliche Daten (Angriff geht
#    vs. blockt, Nukes da vs. disabled) ohne Kontext. Fehlende Keys -> neutraler Default.
_DIFF = {"easy": 0.0, "medium": 0.33, "hard": 0.66, "impossible": 1.0}
_SIZE = {"small": 0.0, "normal": 0.5, "large": 1.0, "huge": 1.0}
_TEAMNAME = {"duos": 2, "trios": 3, "quads": 4}   # benannte Team-Groessen

def _has_unit(units, name):
    return 1.0 if any(name.lower() in str(u).lower() for u in (units or [])) else 0.0

def _num(x, default=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default

def _team_count(c):
    # playerTeams ist entweder eine Zahl, ein Name ("Duos") oder ein Modus
    # ("Humans Vs Nations") -> letzterer zaehlt nicht als Team-Zahl.
    pt = c.get("playerTeams", 0)
    if isinstance(pt, bool):
        return 0.0
    if isinstance(pt, (int, float)):
        return float(pt)
    s = str(pt).strip().lower()
    return float(_TEAMNAME.get(s, _num(s, 0.0)))

CONFIG_FEATURES = [
    ("is_team",        lambda c: 1.0 if str(c.get("gameMode", "")).lower().startswith("team") else 0.0),
    ("team_count",     lambda c: _clip01(_team_count(c) / 8)),
    ("humans_vs_nations", lambda c: 1.0 if "human" in str(c.get("playerTeams", "")).lower() else 0.0),
    ("map_size",       lambda c: _SIZE.get(str(c.get("gameMapSize", "normal")).lower(), 0.5)),
    ("difficulty",     lambda c: _DIFF.get(str(c.get("difficulty", "medium")).lower(), 0.33)),
    ("bots_frac",      lambda c: _clip01(_num(c.get("bots", 0)) / 500)),
    ("donate_gold",    lambda c: float(bool(c.get("donateGold", False)))),
    ("donate_troops",  lambda c: float(bool(c.get("donateTroops", False)))),
    ("infinite_gold",  lambda c: float(bool(c.get("infiniteGold", False)))),
    ("infinite_troops",lambda c: float(bool(c.get("infiniteTroops", False)))),
    ("instant_build",  lambda c: float(bool(c.get("instantBuild", False)))),
    ("random_spawn",   lambda c: float(bool(c.get("randomSpawn", False)))),
    ("nukes_disabled", lambda c: max(_has_unit(c.get("disabledUnits"), "MIRV"),
                                     _has_unit(c.get("disabledUnits"), "Atom"),
                                     _has_unit(c.get("disabledUnits"), "Hydrogen"),
                                     _has_unit(c.get("disabledUnits"), "Nuke"))),
    ("sam_disabled",   lambda c: _has_unit(c.get("disabledUnits"), "SAM")),
    ("warship_disabled",lambda c: _has_unit(c.get("disabledUnits"), "Warship")),
    ("port_disabled",  lambda c: _has_unit(c.get("disabledUnits"), "Port")),
    ("silo_disabled",  lambda c: _has_unit(c.get("disabledUnits"), "Silo")),
    ("anon_names",     lambda c: float(bool(c.get("anonymizeNames", False)))),  # -> Reputation unbrauchbar
    ("doomsday",       lambda c: float(bool((c.get("doomsdayClock") or {}).get("enabled", False)))),
    ("overtime",       lambda c: float(bool((c.get("overtime") or {}).get("enabled", False)))),
]
CONFIG_DIM = len(CONFIG_FEATURES)


def featurize_config(config: dict):
    """info.config-Dict -> fester Modifier-Vektor (CONFIG_DIM)."""
    c = config or {}
    return [f(c) for _, f in CONFIG_FEATURES]


def featurize_own(own: dict):
    return [f(own) for _, f in OWN_FEATURES]


def featurize_opps(opponents: list[dict], reputation=None):
    """opponents: Liste aus encodeVec (bis MAX_OPP). reputation: optional
    {playerId oder index -> 0..1}. Rückgabe: (matrix[MAX_OPP][OPP_DIM], mask[MAX_OPP])."""
    rows, mask = [], []
    for i in range(MAX_OPP):
        if i < len(opponents):
            o = dict(opponents[i])
            if reputation is not None:
                o["_rep"] = reputation.get(o.get("id"), reputation.get(i, 0.5))
            rows.append([f(o) for _, f in OPP_FEATURES])
            mask.append(1)
        else:
            rows.append([0.0] * OPP_DIM)
            mask.append(0)
    return rows, mask


def featurize(own: dict, opponents: list[dict], reputation=None):
    """Volles Beispiel → (own_vec, opp_matrix, mask). Map-Tensor kommt separat
    (encodeMap liefert ihn schon fertig als 18-Kanal-Float-Array)."""
    ov = featurize_own(own)
    om, mask = featurize_opps(opponents, reputation)
    return ov, om, mask

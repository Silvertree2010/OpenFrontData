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

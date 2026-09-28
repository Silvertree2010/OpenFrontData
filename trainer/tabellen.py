"""Feste Tabellen für den BC-Trainer.

Quellen:
- docs/ZIELWAHL_ENTWURF.md §6: Wirkradius R je Typ, weiches Ziel σ = R/2, Kennzahl M@R
- docs/ZIELWAHL_ENTWURF.md §8: Eval-Schichten, höchstens 12 je Partie und Typ
- materializer/DESIGN.md §6: Typ → legal-Bit (dieselbe Zuordnung wie das Maskentor)

Eine "Gruppe" ist die räumliche Typklasse eines Samples. Sie bestimmt Maske,
σ und R. Nicht-räumliche Samples haben keine Gruppe (-1).
"""
from __future__ import annotations

GRUPPEN = ["bau", "hafen", "boot", "atom", "wasserstoff", "mirv",
           "kriegsschiff", "schiff_bewegen", "spawn"]
GID = {n: i for i, n in enumerate(GRUPPEN)}

# Wirkradius R in echten Kacheln (Entwurf §6). MIRV: kein räumlicher Verlust.
R_KACHELN = {"bau": 15, "hafen": 20, "boot": 15, "atom": 10, "wasserstoff": 60,
             "mirv": None, "kriegsschiff": 40, "schiff_bewegen": 40, "spawn": 15}

# legal-Bit je Gruppe (DESIGN §6 "Typ → Bit").
BIT = {"bau": 1, "hafen": 2, "boot": 3, "atom": 7, "wasserstoff": 7, "mirv": 7,
       "kriegsschiff": 4, "schiff_bewegen": 5, "spawn": 6}
# Mit LEGAL_BIT1=0 (in der .ok: legal_bit1 false) sind Bit 1 und 2 immer 0.
# Das Maskentor prüft Bauwerke und Port dann gegen Bit 0 (DESIGN §6).
BIT_OHNE_B1 = dict(BIT, bau=0, hafen=0)

_UNIT_GRUPPE = {"City": "bau", "Defense Post": "bau", "Factory": "bau",
                "SAM Launcher": "bau", "Missile Silo": "bau", "Port": "hafen",
                "Warship": "kriegsschiff", "Atom Bomb": "atom",
                "Hydrogen Bomb": "wasserstoff", "MIRV": "mirv"}
_TYP_GRUPPE = {"boat": "boot", "move_warship": "schiff_bewegen", "spawn": "spawn"}

# Entwurf D0 §4.1: Boot und Nukes bekommen den Zielspieler (dst_owner) als target.
ZIEL_GRUPPEN = {"boot", "atom", "wasserstoff", "mirv"}

# Eval-Schichten (Entwurf §8): Name → (Gruppen, Soll-Anzahl).
EVAL_SCHICHTEN = {
    "bauwerke": (("bau", "hafen"), 1500),
    "boot": (("boot",), 1500),
    "nukes": (("atom", "wasserstoff"), 800),
    "kriegsschiff": (("kriegsschiff", "schiff_bewegen"), 700),
    "spawn": (("spawn",), 500),
}
MAX_JE_PARTIE_TYP = 12


def gruppe(intent: dict) -> str | None:
    """Räumliche Gruppe eines Intents, None wenn nicht räumlich."""
    t = intent.get("type")
    if t == "build_unit":
        return _UNIT_GRUPPE.get(intent.get("unit"))
    return _TYP_GRUPPE.get(t)


def schicht_von(g: str) -> str | None:
    for name, (gs, _) in EVAL_SCHICHTEN.items():
        if g in gs:
            return name
    return None

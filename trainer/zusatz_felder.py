"""Normierung der Zusatzfelder (zusatz/src/felder.ts) für den Lader.

Die Zusatzdatei enthält ROHE Werte (Ticks, Truppen, Anzahl, Gold-Anteile), zwei Blöcke je
Sample: je Gegnerplatz OPP und einmal GLOBAL für den Spieler selbst. Hier steht, wie daraus
Netzeingaben werden — dieselbe Art wie featurize.OPP_FEATURES. So lässt sich die Normierung
ändern, ohne den Zusatzlauf zu wiederholen.

Der Lader hängt die Gegnerfelder hinten an die opp-Merkmale und die globalen Felder hinten
an den own-Vektor. Ein alter Checkpoint wird beim Laden mit Nullspalten erweitert
(train.lade_modell) und bleibt gültig.

NEUE FELDER KOMMEN HINTEN DAZU, in derselben Reihenfolge wie in felder.ts:
  - stehen im Kopf der Datei weniger Felder (ältere Datei), werden die fehlenden 0;
  - stehen andere Namen an gleicher Stelle, bricht der Lader ab.

Normierungen: "roh" (auf 0..1 geklemmt), "durchN" (x/N, geklemmt), "logN" (log1p(x)/log1p(N)).
"""
from __future__ import annotations

import math

import numpy as np

# (Name, Normierung), Reihenfolge wie OPP_FELDER in zusatz/src/felder.ts
FELDER_OPP = [
    ("anfrage_ein", "roh"), ("anfrage_ein_alter", "log300"), ("anfrage_ein_rest", "durch200"),
    ("anfrage_aus", "roh"), ("anfrage_aus_alter", "log300"), ("anfrage_aus_rest", "durch200"),
    ("kann_anfragen", "roh"), ("anfrage_sperre_rest", "durch500"),
    ("abgemeldet", "roh"), ("freundlich", "roh"), ("verbuendet", "roh"), ("buendnis_rest", "durch600"),
    ("verlaengerung_ich", "roh"), ("verlaengerung_er", "roh"),
    ("verrat_an_mir", "roh"), ("buendnisse", "durch8"), ("ally_greift_mich_an", "roh"),
    ("angriff_auf_mich", "log16"), ("angriff_auf_mich_rueckzug", "roh"),
    ("angriff_von_mir", "log16"), ("angriff_von_mir_rueckzug", "roh"), ("gebundene_truppen", "log16"),
    ("kann_angreifen", "roh"), ("kann_zielen", "roh"), ("kann_gold", "roh"), ("kann_truppen", "roh"),
    ("embargo_er", "roh"), ("embargo_ich", "roh"), ("ist_mein_ziel", "roh"), ("bin_sein_ziel", "roh"),
]

# (Name, Normierung), Reihenfolge wie GLOBAL_FELDER in zusatz/src/felder.ts
FELDER_GLOBAL = [
    ("gebundene_truppen", "log16"), ("angriff_ein_summe", "log16"), ("angriff_aus_summe", "log16"),
    ("angriffe_ein", "durch8"), ("angriffe_aus", "durch8"),
    ("raketen_ein", "durch4"), ("raketen_rest_min", "log300"), ("raketen_dist_min", "log1000"),
    ("silos_bereit", "durch8"), ("silo_timer_min", "log300"),
    ("sam_bereit", "durch8"), ("sam_timer_min", "log300"),
    ("goldrate", "log18"), ("handelspartner", "durch8"), ("verraeter_rest", "log300"),
    ("sam_alle", "durch32"), ("sam_nicht_bereit", "durch32"),
    ("kosten_city", "durch4"), ("kosten_port", "durch4"), ("kosten_factory", "durch4"),
    ("kosten_defense", "durch4"), ("kosten_sam", "durch4"), ("kosten_silo", "durch4"),
    ("kosten_warship", "durch4"), ("kosten_atom", "durch4"), ("kosten_hydrogen", "durch4"),
    ("kosten_mirv", "durch4"),
]

# Eigene Einheiten (Reihenfolge ownUnitIds) und Angriffe (ownAttackIds) — darauf zeigt own_ref
# "id" bleibt ein ganzzahliger Index (Einbettung bzw. Nachschlagen im Netz, netz_d1.py):
# art 0 = leer, 1…16 = TYP_LISTE; ziel_platz 0…23 Gegnerplatz, 24 ausserhalb Top 24, 25 kein Spieler.
FELDER_EINHEIT = [
    ("art", "id"), ("spalte", "durch180"), ("zeile", "durch90"),
    ("im_bau", "roh"), ("nachladen", "roh"), ("unterwegs", "roh"), ("alter", "log1000"),
]
FELDER_ANGRIFF = [
    ("truppen", "log16"), ("ziel_platz", "id"), ("rueckzug", "roh"), ("alter", "log1000"),
]
MAX_EINHEIT, MAX_ANGRIFF = 128, 16      # wie zusatz/src/felder.ts (MAX_EINH = actions.MAX_OWN_REF)
NAMEN_EINHEIT = [n for n, _ in FELDER_EINHEIT]
NAMEN_ANGRIFF = [n for n, _ in FELDER_ANGRIFF]
DIM_EINHEIT = len(FELDER_EINHEIT)
DIM_ANGRIFF = len(FELDER_ANGRIFF)
NAMEN_OPP = [n for n, _ in FELDER_OPP]
NAMEN_GLOBAL = [n for n, _ in FELDER_GLOBAL]
DIM_OPP = len(FELDER_OPP)
DIM_GLOBAL = len(FELDER_GLOBAL)
# Vollständige Datei: so viele Felder je Block (der Lader verlangt genau das, siehe daten.ZusatzFehlt)
DIMS = (DIM_OPP, DIM_GLOBAL, DIM_EINHEIT, DIM_ANGRIFF)
# Rückwärtskompatible Namen für den Gegnerblock (älterer Code)
NAMEN, DIM = NAMEN_OPP, DIM_OPP


def _normiere(x: np.ndarray, spec: list) -> np.ndarray:
    for j, (_, art) in enumerate(spec[:x.shape[-1]]):
        s = x[..., j]
        if art == "id":                   # ganzzahliger Index, bleibt roh (float16 speichert ihn exakt)
            np.maximum(s, 0.0, out=s)
        elif art == "roh":
            np.clip(s, 0.0, 1.0, out=s)
        elif art.startswith("durch"):
            np.clip(s / float(art[5:]), 0.0, 1.0, out=s)
        elif art.startswith("log"):
            np.log1p(np.maximum(s, 0.0, out=s), out=s)
            s /= math.log1p(float(art[3:]))
            np.clip(s, 0.0, 1.0, out=s)
        else:
            raise ValueError(f"unbekannte Normierung {art!r}")
        x[..., j] = s
    return x


def normiere_opp(roh: np.ndarray) -> np.ndarray:
    """(…, k) roh → float32 im Bereich ~[0,1]."""
    return _normiere(np.asarray(roh, np.float32).copy(), FELDER_OPP)


def normiere_global(roh: np.ndarray) -> np.ndarray:
    return _normiere(np.asarray(roh, np.float32).copy(), FELDER_GLOBAL)


def normiere_einheit(roh: np.ndarray) -> np.ndarray:
    return _normiere(np.asarray(roh, np.float32).copy(), FELDER_EINHEIT)


def normiere_angriff(roh: np.ndarray) -> np.ndarray:
    return _normiere(np.asarray(roh, np.float32).copy(), FELDER_ANGRIFF)


normiere = normiere_opp        # Rückwärtskompatibilität


def _passt(kopf_felder, namen, was: str) -> int:
    n = len(kopf_felder)
    if n > len(namen):
        raise ValueError(f"Zusatzdatei hat {n} {was}-Felder, bekannt sind {len(namen)}: {list(kopf_felder)[len(namen):]}")
    if list(kopf_felder) != namen[:n]:
        raise ValueError(f"{was}-Felder weichen ab:\n  Datei {list(kopf_felder)}\n  erwartet {namen[:n]}")
    return n


def passt(kopf) -> tuple[int, int, int, int]:
    """Kopf prüfen. Rückgabe: Felderzahl je Block (opp, global, einheit, angriff)."""
    if not isinstance(kopf, dict):
        return _passt(kopf, NAMEN_OPP, "opp"), 0, 0, 0     # alte Aufrufform: nur Namensliste
    return (_passt(kopf.get("felder_opp") or kopf.get("felder") or [], NAMEN_OPP, "opp"),
            _passt(kopf.get("felder_global") or [], NAMEN_GLOBAL, "global"),
            _passt(kopf.get("felder_einheit") or [], NAMEN_EINHEIT, "einheit"),
            _passt(kopf.get("felder_angriff") or [], NAMEN_ANGRIFF, "angriff"))

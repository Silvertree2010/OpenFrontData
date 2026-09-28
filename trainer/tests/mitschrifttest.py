#!/usr/bin/env python3
"""Test für inf_d0.mitschreiben (Fehlersuche am Live-Weg).

  python trainer/tests/mitschrifttest.py

1. Je Anfrage genau eine Zeile, gültiges JSON.
2. Grosse Felder (Karte, Zellen, Vektoren) stehen nur als Kurzform drin, alles andere vollständig —
   sonst wird die Datei in einer Partie gigantisch.
3. Ein Schreibfehler stoppt die Partie nicht.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import inf_d0 as I  # noqa: E402

FEHLER = []


def pruefe(name, ok, info=""):
    print(f"{'ok  ' if ok else 'FEHL'} {name} {info}")
    if not ok:
        FEHLER.append(name)


anfrage = {"map": "A" * 5000, "cells": list(range(300)), "tick": 640,
           "own": {"troops": 120000, "troopsRatio": 0.57, "gold": 90000},
           "opps": [{"sid": 3, "troops": 5}], "allies": [2, 7], "kurz": "ok"}
antwort = {"atype": "ATTACK", "p_handeln": 0.14, "wahl": {"magnitude": 3, "target": 2},
           "intent": {"type": "attack", "troops": 24000}}

with tempfile.TemporaryDirectory() as d:
    pfad = os.path.join(d, "mit.jsonl")
    I.mitschreiben(pfad, anfrage, antwort, "arch.example")
    I.mitschreiben(pfad, anfrage, antwort, "127.0.0.1")
    zeilen = [json.loads(z) for z in open(pfad) if z.strip()]
    pruefe("zwei Zeilen", len(zeilen) == 2)
    z = zeilen[0]
    pruefe("Antwort vollständig", z["antwort"]["intent"]["troops"] == 24000
           and z["antwort"]["wahl"]["magnitude"] == 3)
    pruefe("own bleibt ganz", z["anfrage"]["own"]["troopsRatio"] == 0.57
           and z["anfrage"]["own"]["troops"] == 120000)
    pruefe("tick und allies bleiben", z["anfrage"]["tick"] == 640 and z["anfrage"]["allies"] == [2, 7])
    pruefe("Karte nur als Kurzform", isinstance(z["anfrage"]["map"], str)
           and z["anfrage"]["map"].startswith("<str, 5000"))
    pruefe("Zellen nur als Kurzform", z["anfrage"]["cells"].startswith("<list, 300"))
    pruefe("kurze Felder unverändert", z["anfrage"]["kurz"] == "ok")
    pruefe("Zeile klein genug", len(json.dumps(z)) < 1500, f"{len(json.dumps(z))} Zeichen")
    pruefe("von wird vermerkt", z["von"] == "arch.example")

# 3. Schreibfehler darf nicht durchschlagen
try:
    I.mitschreiben("/gibt/es/nicht/mit.jsonl", anfrage, antwort, "x")
    pruefe("Schreibfehler wird geschluckt", True)
except OSError:
    pruefe("Schreibfehler wird geschluckt", False)

print(f"\n{'ALLES OK' if not FEHLER else 'FEHLER: ' + ', '.join(FEHLER)}")
sys.exit(1 if FEHLER else 0)

#!/usr/bin/env python3
"""Zwei Arena-Läufe mit derselben --saat (und inf_d0 --wahl-saat) müssen Zeile für Zeile gleich
sein, bis auf die Uhrzeit-Felder. Prüft die Kopplung des Ziehens an die Saat (arena.ts
wahl_schluessel, spielen.gleichverteilt):

  python3 arena/tests/bitgleich.py lauf1.jsonl lauf2.jsonl [--seite netz]

Exit 0 nur, wenn alle Spiel-IDs in beiden Dateien stehen und jede Zeile ohne ms, ms_inferenz,
ms_spielweise, spur, aufnahme gleich ist. Sonst nennt es die erste abweichende Kennzahl je Partie.
"""
from __future__ import annotations

import argparse
import json
import sys

ZEIT = {"ms", "ms_inferenz", "ms_spielweise", "spur", "aufnahme"}


def lade(p, seite):
    aus = {}
    for l in open(p):
        if l.strip():
            z = json.loads(l)
            if seite is None or z.get("seite") == seite:
                aus[(z["spiel_id"], z.get("ki", 1))] = {k: v for k, v in z.items() if k not in ZEIT}
    return aus


def erste_abweichung(x, y, pfad=""):
    if isinstance(x, dict) and isinstance(y, dict):
        for k in sorted(set(x) | set(y)):
            r = erste_abweichung(x.get(k), y.get(k), f"{pfad}.{k}" if pfad else k)
            if r:
                return r
        return None
    if isinstance(x, list) and isinstance(y, list) and len(x) == len(y):
        for i, (u, v) in enumerate(zip(x, y)):
            r = erste_abweichung(u, v, f"{pfad}[{i}]")
            if r:
                return r
        return None
    return None if x == y else f"{pfad}: {json.dumps(x)[:80]} ≠ {json.dumps(y)[:80]}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("a")
    ap.add_argument("b")
    ap.add_argument("--seite", default=None)
    A = ap.parse_args()
    a, b = lade(A.a, A.seite), lade(A.b, A.seite)
    fehler = 0
    if set(a) != set(b):
        print(f"FEHL Partien verschieden: nur A {len(set(a) - set(b))}, nur B {len(set(b) - set(a))}")
        fehler += 1
    for k in sorted(set(a) & set(b)):
        r = erste_abweichung(a[k], b[k])
        if r:
            fehler += 1
            print(f"FEHL {k[0]} KI {k[1]}: {r}")
    n = len(set(a) & set(b))
    print(f"{n - fehler if fehler <= n else 0} von {n} Partien bitgleich (ohne Uhrzeit-Felder)")
    print("ALLES OK" if not fehler else f"{fehler} ABWEICHUNGEN")
    return 1 if fehler else 0


if __name__ == "__main__":
    sys.exit(main())

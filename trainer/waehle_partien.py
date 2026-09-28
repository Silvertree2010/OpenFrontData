#!/usr/bin/env python3
"""Wählt Partien für einen Lauf aus und schreibt die Liste für train.py --partien-liste.

Zwei Schritte, damit der Pool nur ein einziges Mal gelesen wird:
  1. --scan: liest je Partie nur hdr.json und .ok (Metadaten, nie Samples oder Karten)
     und schreibt eine Zwischendatei (JSONL, eine Zeile je Partie).
  2. Filtern: liest die Zwischendatei, wendet die Schalter an, schreibt die Liste und
     meldet die Verteilung: Partien, Samples, Samples je Aktionstyp, Wirkung des Deckels,
     Aufteilung train/val (reader.is_val, hashbasiert).

Die Kriterien sind Schalter, nichts ist fest verdrahtet:
  --modus "Free For All"     Spielmodus aus hdr.config.gameMode ("alle" = kein Filter)
  --min-samples 200          Partien mit weniger Samples fallen weg
  --min-spieler 20           Spielerzahl aus hdr.players, Art über --spieler-art
  --spieler-art alle|mensch  alle Einträge (mit Nationen/Bots) oder nur type HUMAN
  --deckel 2000              nur für die Hochrechnung; im Training macht ihn der Lader

  python trainer/waehle_partien.py --daten ~/of-mat2-out --cache pool.jsonl --scan
  python trainer/waehle_partien.py --cache pool.jsonl --modus "Free For All" \\
      --min-samples 200 --min-spieler 20 --deckel 2000 --aus liste.txt
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter

HIER = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HIER)
for _p in (os.path.join(REPO, "materializer", "py"), HIER):   # wie in daten.py
    if _p not in sys.path:
        sys.path.insert(0, _p)

import reader as R  # noqa: E402


def _json(p):
    try:
        with open(p, "rb") as f:
            return json.loads(f.read())
    except (OSError, ValueError):
        return None


def ordner_von(wurzeln: list[str]) -> list[str]:
    out = []
    for w in wurzeln:
        w = os.path.expanduser(w)
        namen = os.listdir(w)
        if any(n.endswith(".ok") for n in namen):
            out.append(w)
        else:
            out += sorted(os.path.join(w, n) for n in namen if os.path.isdir(os.path.join(w, n)))
    return out


def scan(wurzeln: list[str], cache: str) -> int:
    """Einziger Durchgang über den Pool. Je Partie zwei kleine Dateien, nichts sonst."""
    n, fehler = 0, Counter()
    with open(cache, "w") as aus:
        for d in ordner_von(wurzeln):
            for name in sorted(os.listdir(d)):
                if not name.endswith(".ok"):
                    continue
                gid = name[:-3]
                ok = _json(os.path.join(d, name))
                hdr = _json(os.path.join(d, f"{gid}.hdr.json"))
                if ok is None or hdr is None or ok.get("format") != 2:
                    fehler["ok/hdr unlesbar"] += 1
                    continue
                spieler = hdr.get("players") or []
                res = {k: int(v.get("ok", 0)) for k, v in (ok.get("res") or {}).items()}
                aus.write(json.dumps({
                    "gid": gid, "ordner": d, "modus": (hdr.get("config") or {}).get("gameMode"),
                    "karte": hdr.get("map"), "groesse": hdr.get("mapSize"), "W": hdr.get("W"), "H": hdr.get("H"),
                    "zuege": hdr.get("numTurns"), "spieler": len(spieler),
                    "menschen": sum(1 for p in spieler if p.get("type") == "HUMAN"),
                    "samples": int(ok.get("samples", 0)), "raeumlich": int(ok.get("spatial", 0)),
                    "kinds": ok.get("by_kind") or {}, "intents": ok.get("by_intent") or {}, "res": res,
                    "geschnitten": ok.get("valid_until") is not None, "tier1": bool(hdr.get("tier1")),
                }, ensure_ascii=False) + "\n")
                n += 1
    if fehler:
        print(f"[scan] übersprungen: {dict(fehler)}", flush=True)
    return n


def lade(cache: str) -> list[dict]:
    with open(cache) as f:
        return [json.loads(z) for z in f if z.strip()]


def filtern(recs: list[dict], a) -> list[dict]:
    art = "menschen" if a.spieler_art == "mensch" else "spieler"
    out = []
    for r in recs:
        if a.modus != "alle" and r.get("modus") != a.modus:
            continue
        if r["samples"] < a.min_samples or r.get(art, 0) < a.min_spieler:
            continue
        if a.nur_ungeschnitten and r.get("geschnitten"):
            continue
        out.append(r)
    return out


def verteilung(recs: list[dict], deckel: int) -> dict:
    """Summen roh und nach Deckel. Der Deckel behält je Partie jedes Sample mit
    p = min(1, N/Samples); die Erwartungswerte skalieren also mit p."""
    s = sum(r["samples"] for r in recs)
    s_d = sum(min(r["samples"], deckel) if deckel else r["samples"] for r in recs)
    kinds, intents, res = Counter(), Counter(), Counter()
    kinds_d, intents_d, res_d = Counter(), Counter(), Counter()
    val = [r for r in recs if R.is_val(r["gid"])]
    for r in recs:
        p = min(1.0, deckel / r["samples"]) if deckel and r["samples"] else 1.0
        for zaehler, zaehler_d, quelle in ((kinds, kinds_d, r["kinds"]), (intents, intents_d, r["intents"]),
                                           (res, res_d, r["res"])):
            for k, v in quelle.items():
                zaehler[k] += v
                zaehler_d[k] += v * p
    return {"partien": len(recs), "samples": s, "samples_deckel": s_d,
            "val_partien": len(val), "val_samples": sum(r["samples"] for r in val),
            "raeumlich": sum(r["raeumlich"] for r in recs),
            "kinds": dict(kinds), "kinds_deckel": {k: round(v) for k, v in kinds_d.items()},
            "intents": dict(intents.most_common()), "intents_deckel": {k: round(v) for k, v in intents_d.most_common()},
            "res": dict(res.most_common()), "res_deckel": {k: round(v) for k, v in res_d.most_common()}}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--daten", nargs="+", default=["~/of-mat2-out"])
    ap.add_argument("--cache", default="pool.jsonl")
    ap.add_argument("--scan", action="store_true", help="Pool einmal lesen und die Zwischendatei schreiben")
    ap.add_argument("--modus", default="Free For All", help='Spielmodus oder "alle"')
    ap.add_argument("--min-samples", type=int, default=200)
    ap.add_argument("--min-spieler", type=int, default=20)
    ap.add_argument("--spieler-art", choices=("alle", "mensch"), default="alle")
    ap.add_argument("--nur-ungeschnitten", action="store_true", help="Partien mit Schnitt (Desync) weglassen")
    ap.add_argument("--deckel", type=int, default=0, help="nur für die Hochrechnung")
    ap.add_argument("--aus", default=None, help="Datei für die Liste der gids")
    ap.add_argument("--stats", default=None, help="Datei für die Verteilung (JSON)")
    a = ap.parse_args(argv)

    if a.scan:
        n = scan(a.daten, a.cache)
        print(f"[scan] {n} Partien nach {a.cache}", flush=True)
    recs = lade(a.cache)
    gefiltert = filtern(recs, a)
    alles = verteilung(recs, 0)
    v = verteilung(gefiltert, a.deckel)
    print(f"[pool]     {alles['partien']} Partien, {alles['samples']} Samples", flush=True)
    print(f"[gefiltert] {v['partien']} Partien ({v['partien'] / max(1, alles['partien']):.1%}), "
          f"{v['samples']} Samples, mit Deckel {a.deckel or '-'}: {v['samples_deckel']} "
          f"({v['samples_deckel'] / max(1, alles['samples']):.1%} des Pools)", flush=True)
    print(f"[split]    val {v['val_partien']} Partien / {v['val_samples']} Samples (reader.is_val)", flush=True)
    print(f"[art]      {v['kinds_deckel'] if a.deckel else v['kinds']}", flush=True)
    print(f"[intents]  {v['intents_deckel'] if a.deckel else v['intents']}", flush=True)
    print(f"[einheiten] {v['res_deckel'] if a.deckel else v['res']}", flush=True)
    if a.aus:
        with open(a.aus, "w") as f:
            for r in sorted(gefiltert, key=lambda r: r["gid"]):
                f.write(r["gid"] + "\n")
        print(f"[liste] {len(gefiltert)} gids nach {a.aus}", flush=True)
    if a.stats:
        with open(a.stats, "w") as f:
            json.dump({"filter": vars(a), "pool": alles, "gefiltert": v}, f, indent=1, ensure_ascii=False)
    return v


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Trajektorien aus der Arena (arena.ts --spur ORDNER) für den Trainer fertig machen.

    python trainer/trajektorie.py abschliessen ORDNER [ORDNER …]
    python trainer/trajektorie.py gewichte ORDNER_A ORDNER_B      # Φ-Gewichte anpassen/prüfen
    Schalter --ziel platz|sieg|gebiet (Endbelohnung, belohnung.py), bei gewichte --praefix A B
    python trainer/trajektorie.py zusammenfassung ORDNER

abschliessen: je Partie mit Marke <gid>.spur.json (von spur.ts) die Belohnung rechnen
(belohnung.py: R aus dem Endplatz, Φ, r_t, G_t, A_t), in jede Metazeile schreiben und dann
die .ok schreiben — erst ab hier liest daten.PartieLeser die Partie. Wiederholbar (z. B. mit
neuen Gewichten): meta.zst wird neu geschrieben, die .ok danach.

In der Metazeile danach:
  win      = A_t  (Vorteil). So nimmt verlust.adv_gewicht ohne Codeänderung den Vorteil statt
             der Siegflagge: AWR ist dann `--adv-beta β`. ACHTUNG: der Wertkopf lernt dann auf
             A_t (MSE auf win) — im RL-Lauf --value-w 0 oder eigenes Ziel (offen).
  rl.phi, rl.r, rl.G, rl.A, rl.R
  lab      die gewählten Köpfe (daten.PartieLeser nimmt sie statt der Rückrechnung aus dem Intent)
  w, w_tick = 1 je Entscheidung

Lader: D.finde_partien([ORDNER]) und LaderOpt(zusatz=ORDNER, reputation=…) — die Zusatzdatei
liegt im selben Ordner. Falle: finde_partien nimmt je gid nur die erste Partie; zwei Läufe
derselben Saat brauchen --spur-kennung in der Arena, sonst verdeckt der eine den anderen.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys

HIER = os.path.dirname(os.path.abspath(__file__))
if HIER not in sys.path:
    sys.path.insert(0, HIER)

import numpy as np  # noqa: E402

import daten as D  # noqa: E402,F401  (sys.path für reader)
import reader as R  # noqa: E402
import belohnung as B  # noqa: E402

try:  # Python ≥ 3.14
    from compression import zstd as _zstd

    def zkomp(b: bytes) -> bytes:
        return _zstd.compress(b)
except ImportError:  # pragma: no cover
    import zstandard as _zs

    def zkomp(b: bytes) -> bytes:
        return _zs.ZstdCompressor().compress(b)


def _schreibe(pfad: str, daten: bytes) -> None:
    tmp = pfad + ".tmp"
    with open(tmp, "wb") as f:
        f.write(daten)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, pfad)


def marken(ordner: str) -> list[str]:
    return sorted(glob.glob(os.path.join(os.path.expanduser(ordner), "*.spur.json")))


def lies(ordner: str, gid: str) -> tuple[dict, list[dict], dict]:
    with open(R.path_of(ordner, gid, "spur.json")) as f:
        marke = json.load(f)
    with open(R.path_of(ordner, gid, "hdr.json")) as f:
        hdr = json.load(f)
    with open(R.path_of(ordner, gid, "meta.zst"), "rb") as f:
        zeilen = [json.loads(x) for x in R.zdec(f.read()).decode("utf-8").split("\n") if x]
    return marke, zeilen, hdr


def schliesse_partie(ordner: str, gid: str, gew: B.Gewichte = B.GEWICHTE, gamma: float = B.GAMMA,
                     ziel: str = "platz", lam: float | None = None) -> dict:
    marke, zeilen, hdr = lies(ordner, gid)
    if len(zeilen) != marke["samples"]:
        raise ValueError(f"{gid}: {len(zeilen)} Metazeilen, Marke sagt {marke['samples']}")
    ergebnis = {k["clientID"]: k for k in hdr["ki"]}
    eps = B.fuer_partie(zeilen, ergebnis, gew, gamma, ziel, lam)
    for cid, e in eps.items():
        for j, i in enumerate(e["zeilen"]):
            m = zeilen[i]
            m["win"] = round(float(e["A"][j]), 6)
            m["rl"].update(phi=round(float(e["phi"][j]), 6), r=round(float(e["r"][j]), 6),
                           G=round(float(e["G"][j]), 6), A=round(float(e["A"][j]), 6), R=round(e["R"], 6))
    _schreibe(R.path_of(ordner, gid, "meta.zst"),
              # ohne Zeilenumbruch am Ende, wie der Materialisierer (reader.Game.meta verträgt keinen)
              zkomp("\n".join(json.dumps(m, separators=(",", ":")) for m in zeilen).encode("utf-8")))
    files = {}
    for name in marke["files"]:
        files[name] = os.path.getsize(os.path.join(ordner, name))
    ok = {"format": 2, "files": files, "samples": len(zeilen), "spatial": marke.get("spatial", 0),
          "by_kind": marke.get("by_kind", {}), "legal_bit1": marke.get("legal_bit1", True),
          "quelle": "arena-spur", "ergebnis": marke.get("ergebnis", {}),
          "belohnung": B.beschreibung(gew, gamma, ziel, lam),
          "ki": [{"clientID": c, "platz": ergebnis[c]["platz"], "von": ergebnis[c]["von"],
                  "platz_ohne_bots": ergebnis[c].get("platz_ohne_bots"),
                  "von_ohne_bots": ergebnis[c].get("von_ohne_bots"),
                  "R": round(e["R"], 6), "zeilen": len(e["zeilen"])} for c, e in eps.items()]}
    _schreibe(R.path_of(ordner, gid, "ok"), json.dumps(ok).encode("utf-8"))
    probleme = R.ok_problems(ordner, gid)
    if probleme:
        raise ValueError(f"{gid}: .ok besteht die Prüfung nicht: {probleme}")
    return ok


def abschliessen(ordner: str, gew: B.Gewichte = B.GEWICHTE, gamma: float = B.GAMMA,
                 ziel: str = "platz", lam: float | None = None) -> dict:
    ordner = os.path.expanduser(ordner)
    n, zeilen, fehler = 0, 0, []
    for m in marken(ordner):
        gid = os.path.basename(m)[: -len(".spur.json")]
        try:
            ok = schliesse_partie(ordner, gid, gew, gamma, ziel, lam)
            n += 1
            zeilen += ok["samples"]
        except Exception as e:                     # eine kaputte Partie hält die anderen nicht auf
            fehler.append(f"{gid}: {type(e).__name__}: {e}")
    return {"ordner": ordner, "partien": n, "zeilen": zeilen, "fehler": fehler}


# ---------------------------------------------------------------- Gewichte anpassen
def merkmale(ordner: str, ziel: str = "platz", praefix: str = "") -> dict[str, np.ndarray]:
    """Je Entscheidung: R der Episode, u, f(g), Episodennummer."""
    p, u, f, ep = [], [], [], []
    k = 0
    for m in marken(ordner):
        gid = os.path.basename(m)[: -len(".spur.json")]
        if not gid.startswith(praefix):
            continue
        _, zeilen, hdr = lies(os.path.expanduser(ordner), gid)
        erg = {x["clientID"]: x for x in hdr["ki"]}
        je: dict[str, list[dict]] = {}
        for z in zeilen:
            je.setdefault(z["clientID"], []).append(z)
        for cid, zs in je.items():
            R_ = B.endwert(erg[cid], ziel)
            for z in zs:
                rl = z["rl"]
                p.append(R_)
                u.append(float(B.anteil_tot(rl["tot"], rl["spieler"])))
                f.append(float(B.f_gebiet(rl["gebiet"], rl["spieler"])))
                ep.append(k)
            k += 1
    return {k_: np.asarray(v, np.float64) for k_, v in (("p", p), ("u", u), ("f", f), ("ep", ep))}


def gewichte(ordner_a: str, ordner_b: str, ziel: str = "platz", praefix_a: str = "", praefix_b: str = "") -> dict:
    a, b = merkmale(ordner_a, ziel, praefix_a), merkmale(ordner_b, ziel, praefix_b)
    koef, r2 = B.anpassen(a["p"], a["u"], a["f"])

    def rest_var(x, c, wu, wg):
        return float(np.var(x["p"] - (c + wu * x["u"] + wg * x["f"])))

    g = B.gewichte_fuer(ziel)
    return {"ziel": ziel, "zeilen_a": int(a["p"].size), "episoden_a": int(a["ep"].max() + 1) if a["ep"].size else 0,
            "zeilen_b": int(b["p"].size), "anpassung_a": [round(float(x), 4) for x in koef], "r2_a": round(r2, 4),
            "var_R_b": round(float(np.var(b["p"])), 5),
            "var_A_b_angepasst": round(rest_var(b, *koef), 5),
            "var_A_b_gesetzt": round(rest_var(b, g.c, g.w_u, g.w_g), 5),
            "gesetzt": [g.c, g.w_u, g.w_g]}


def zusammenfassung(ordner: str) -> dict:
    ordner = os.path.expanduser(ordner)
    ergebnis, arten, G0, R_, n = {}, {}, [], [], 0
    for m in marken(ordner):
        gid = os.path.basename(m)[: -len(".spur.json")]
        marke, zeilen, hdr = lies(ordner, gid)
        n += len(zeilen)
        for k, v in marke.get("ergebnis", {}).items():
            ergebnis[k] = ergebnis.get(k, 0) + v
        for z in zeilen:
            a = (z.get("lab") or {}).get("atype", 0)
            arten[a] = arten.get(a, 0) + 1
            if "G" in z["rl"] and z is zeilen[0]:
                pass
        je: dict[str, list[dict]] = {}
        for z in zeilen:
            je.setdefault(z["clientID"], []).append(z)
        for zs in je.values():
            if "G" in zs[0]["rl"]:
                G0.append(zs[0]["rl"]["G"])
                R_.append(zs[0]["rl"]["R"])
    return {"zeilen": n, "ergebnis": ergebnis, "atype": dict(sorted(arten.items())),
            "G0_median": float(np.median(G0)) if G0 else None, "R_median": float(np.median(R_)) if R_ else None}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("befehl", choices=["abschliessen", "gewichte", "zusammenfassung"])
    ap.add_argument("ordner", nargs="+")
    ap.add_argument("--ziel", default="platz", choices=B.ZIELE, help="Endbelohnung R (belohnung.py)")
    ap.add_argument("--gae-lambda", type=float, default=None,
                    help="Vorteil nach GAE aus rl.value statt Monte Carlo mit Φ (z. B. 0.95)")
    ap.add_argument("--praefix", nargs=2, default=("", ""), metavar=("A", "B"),
                    help="gewichte: nur Partien, deren gid so beginnt (z. B. rl3i92 rl3i93)")
    A = ap.parse_args(argv)
    if A.befehl == "abschliessen":
        aus = [abschliessen(o, B.gewichte_fuer(A.ziel), B.GAMMA, A.ziel, A.gae_lambda) for o in A.ordner]
        print(json.dumps(aus, ensure_ascii=False, indent=1))
        return 1 if any(x["fehler"] for x in aus) else 0
    if A.befehl == "gewichte":
        if len(A.ordner) != 2:
            ap.error("gewichte braucht ORDNER_A ORDNER_B")
        print(json.dumps(gewichte(*A.ordner, ziel=A.ziel, praefix_a=A.praefix[0], praefix_b=A.praefix[1]), indent=1))
        return 0
    print(json.dumps([zusammenfassung(o) for o in A.ordner], ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())

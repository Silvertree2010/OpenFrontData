#!/usr/bin/env python3
"""menschen.py auf einer synthetischen Partie mit bekannten Antworten (Python ≥ 3.14, numpy).

  python arena/tests/menschen_test.py [--mat-py <openfront-ai-trainer>/materializer/py]

Schreibt eine Partie im Format des Materialisierers v2 (hdr, ok, meta.zst, zusatz.zst) und Tier 1
(own.zst, units.zst) mit einem kleinen Encoder nach tier1.py (Byte-Layout im Kopf dort), dazu eine
Karte mit Uferbits. Liest sie mit menschen.partie() und dem echten tier1-Leser und vergleicht
mit den Sollwerten, die sich aus der Konstruktion ergeben. Zum Schluss main() über den Ordner.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import struct
import sys
import tempfile

import numpy as np
from compression import zstd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import menschen as M  # noqa: E402

FEHLER = []


def pruefe(name, ok, info=""):
    print(f"{'ok  ' if ok else 'FEHL'} {name} {info}")
    if not ok:
        FEHLER.append(name)


# ---------------------------------------------------------------- Encoder (Tier 1, Version 1)
def varints(xs):
    b = bytearray()
    for x in xs:
        while True:
            c = x & 127
            x >>= 7
            b.append(c | (128 if x else 0))
            if not x:
                break
    return bytes(b)


def planes(xs, breite):
    roh = b"".join(int(x).to_bytes(breite, "little") for x in xs)
    n = len(xs)
    return b"".join(roh[k::breite] for k in range(breite)) if n else b""


def container(pfad, art, W, H, S, bloecke, L, n_rec):
    with open(pfad, "wb") as f:
        f.write(struct.pack("<II", 0x184D2A50, 32) + struct.pack("<4s4sIIIiII", b"OFT1", art, 1, W, H, S, 512, 4096))
        for t0, nt, flags, nutz in bloecke:
            zz = zstd.compress(nutz)
            f.write(struct.pack("<II", 0x184D2A51, 16) + struct.pack("<IiII", len(zz), t0, nt, flags) + zz)
        f.write(struct.pack("<II", 0x184D2A52, 24) + struct.pack("<iIIIIi", L, len(bloecke), n_rec, 0, 0, 0))


def own_zst(pfad, W, H, S, L, aenderungen):
    """aenderungen: {t: {ref: neuer u16-Zustand}} für t in 1..L; Delta-Blöcke [1..S-1], [S..L]."""
    grenzen = [(1, S - 1), (S, L)]
    bloecke, n_rec = [], 0
    for a, b in grenzen:
        nG, nT, gcnt, gval, gaps = [], [], [], [], []
        for t in range(a, b + 1):
            ae = aenderungen.get(t, {})
            gruppen = {}
            for r, v in ae.items():
                gruppen.setdefault(v, []).append(r)
            nG.append(len(gruppen))
            nT.append(0)
            for v in sorted(gruppen):
                refs = sorted(gruppen[v])
                gcnt.append(len(refs))
                gval.append(v)
                vor = 0
                for r in refs:
                    gaps.append(r - vor)
                    vor = r
                n_rec += len(refs)
        vc, vg = varints(gcnt), varints(gaps)
        nutz = (struct.pack(f"<{len(nG)}I", *nG) + struct.pack(f"<{len(nT)}I", *nT) + struct.pack("<II", len(vc), len(vg))
                + vc + planes(gval, 2) + vg)
        bloecke.append((a, b - a + 1, 0, nutz))
    container(pfad, b"OWN\0", W, H, S, bloecke, L, n_rec)


def units_zst(pfad, W, H, S, L, zeilen):
    """zeilen: (tick, id, mask, code, flags, owner, level, pos, target), nach tick sortiert."""
    bloecke = []
    for t0 in range(0, L + 1, 512):
        zs = [z for z in zeilen if t0 <= z[0] < t0 + 512]
        if not zs:
            continue
        sp = list(zip(*zs))
        nutz = (struct.pack("<I", len(zs)) + planes(sp[0], 4) + planes(sp[1], 4) + bytes(sp[2]) + bytes(sp[3])
                + bytes(sp[4]) + planes(sp[5], 2) + planes(sp[6], 2) + planes(sp[7], 4) + planes(sp[8], 4))
        bloecke.append((t0, 512, 0, nutz))
    container(pfad, b"UNIT", W, H, S, bloecke, L, len(zeilen))


def val_gid():
    for i in range(100000):
        g = f"T{i:07d}"
        if int(hashlib.sha1(g.encode()).hexdigest()[:8], 16) % 25 == 0:
            return g


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mat-py", default=None)
    a = ap.parse_args()
    tmp = tempfile.mkdtemp(prefix="menschen_test_")
    pool, zdir, karten = (os.path.join(tmp, d) for d in ("pool/s0", "zusatz", "karten/testkarte"))
    for d in (pool, zdir, karten):
        os.makedirs(d)
    gid = val_gid()
    W, H, S, L = 40, 20, 100, 1000
    ref = lambda x, y: y * W + x  # noqa: E731

    # Karte: alles Land, Uferbit in Spalte x = 2
    terr = np.full(W * H, 0x80, np.uint8)
    terr[[ref(2, y) for y in range(H)]] |= 0x40
    terr.tofile(os.path.join(karten, "map.bin"))
    json.dump({"name": "Testkarte", "map": {"width": W, "height": H, "num_land_tiles": W * H}},
              open(os.path.join(karten, "manifest.json"), "w"))

    # own.zst: sid 1 Quadrat 5×5 ab t=10, dazu 3×3 fern ab t=300; sid 2 Block ab t=10, tot bei t=600
    # (Kacheln gehen an den Bot 3); Bot 3 besitzt Zeile 19 die ganze Partie.
    ae = {10: {}, 300: {}, 600: {}}
    for x in range(2, 7):
        for y in range(2, 7):
            ae[10][ref(x, y)] = 1
    for x in range(20, 24):
        for y in range(2, 4):
            ae[10][ref(x, y)] = 2
    for x in range(W):
        ae[10][ref(x, 19)] = 3
    for x in range(30, 33):
        for y in range(10, 13):
            ae[300][ref(x, y)] = 1
    for r in list(ae[10]):
        if ae[10][r] == 2:
            ae[600][r] = 3
    own_zst(os.path.join(pool, f"{gid}.own.zst"), W, H, S, L, ae)

    # units.zst: sid 1 City t=120 (im Bau), fertig t=170, Stufe 2 t=400, Port t=450, Port von 3 erobert
    # t=700, Boot t=200 (Ende t=260), Atombombe t=800. Sollkosten 125k + 250k + 125k + 750k = 1,25 Mio.
    NONE = 0xFFFFFFFF
    zeilen = [(120, 1, 1, 1, 1, 1, 1, ref(3, 3), NONE), (170, 1, 8, 1, 0, 1, 1, ref(3, 3), NONE),
              (200, 2, 1, 8, 0, 1, 0, ref(2, 4), ref(35, 5)), (260, 2, 64, 8, 0, 1, 0, ref(30, 5), ref(35, 5)),
              (400, 1, 4, 1, 0, 1, 2, ref(3, 3), NONE), (450, 3, 1, 2, 0, 1, 1, ref(2, 5), NONE),
              (700, 3, 2, 2, 0, 3, 1, ref(2, 5), NONE), (800, 4, 1, 9, 0, 1, 0, ref(3, 3), ref(25, 3))]
    units_zst(os.path.join(pool, f"{gid}.units.zst"), W, H, S, L, zeilen)

    # meta.zst: Samples für sid 1 und 2 alle 100 Ticks ab 50, dazu Aktionen
    meta = []

    def zeile(t, sid, kind="noop", intent=None, w=1, gold=0, allies=(), betrayals=0, tiles=0):
        meta.append({"turn": t, "tick": t, "sid": sid, "clientID": f"c{sid}", "kind": kind, "w": w, "w_tick": 1,
                     "intent": intent or {"type": "no_op"}, "gold": gold, "troops": 1000 + t,
                     "allies": list(allies), "own": {"tiles": tiles, "troopsRatio": 0.4, "betrayals": betrayals}})

    for t in range(50, 1001, 100):
        g1 = 100_000 + 2_000 * t                                 # Bestand wächst gleichmässig
        allies1 = (2,) if 350 <= t < 550 else ()
        zeile(t, 1, "spawn" if t == 50 else "noop", gold=g1, allies=allies1, betrayals=1 if t >= 650 else 0, tiles=25)
        if t <= 550:
            zeile(t, 2, "spawn" if t == 50 else "noop", gold=5000, tiles=8)
    zeile(150, 1, "act", {"type": "attack", "targetID": "p2", "troops": 100}, w=3, gold=400_000)
    zeile(250, 2, "act", {"type": "allianceRequest", "recipient": "p1"}, gold=5000)
    zeile(350, 1, "act", {"type": "embargo", "targetID": "p2", "action": "start"}, gold=800_000)
    meta.sort(key=lambda m: m["tick"])
    for m in meta:                                              # Aktionszeilen passend zu den Samples
        if m["sid"] == 1:
            m["gold"] = 100_000 + 2_000 * m["tick"]
            m["allies"] = [2] if 350 <= m["tick"] < 550 else []
    mz = zstd.compress("\n".join(json.dumps(m) for m in meta).encode())
    open(os.path.join(pool, f"{gid}.meta.zst"), "wb").write(mz)
    open(os.path.join(pool, f"{gid}.maps"), "wb").write(b"\0" * 16)
    hdr = {"format": 2, "gid": gid, "map": "Testkarte", "mapSize": "Normal", "W": W, "H": H, "landTiles": W * H,
           "spawnEndTick": S, "config": {"gameMode": "Free For All", "bots": 1},
           "players": [{"sid": 1, "clientID": "c1", "playerID": "p1", "name": "a", "team": None, "type": "HUMAN"},
                       {"sid": 2, "clientID": "c2", "playerID": "p2", "name": "b", "team": None, "type": "HUMAN"},
                       {"sid": 3, "clientID": None, "playerID": "p3", "name": "bot", "team": None, "type": "BOT"}]}
    hj = json.dumps(hdr).encode()
    open(os.path.join(pool, f"{gid}.hdr.json"), "wb").write(hj)
    files = {f"{gid}.{n}": os.path.getsize(os.path.join(pool, f"{gid}.{n}")) for n in ("hdr.json", "maps", "meta.zst")}
    json.dump({"format": 2, "files": files, "samples": len(meta)}, open(os.path.join(pool, f"{gid}.ok"), "w"))
    # zusatz: ein globales Feld angriffe_ein = 2 in allen Zeilen von sid 1
    g = np.array([[2.0 if m["sid"] == 1 else 0.0] for m in meta], np.float32)
    kopf = {"format": 2, "gid": gid, "samples": len(meta), "opp": 0, "einheit": 0, "angriff": 0, "felder_opp": [],
            "felder_global": ["angriffe_ein"], "felder_einheit": [], "felder_angriff": [], "dtype": "float32"}
    open(os.path.join(zdir, f"{gid}.zusatz.zst"), "wb").write(zstd.compress(json.dumps(kopf).encode() + b"\n" + g.tobytes()))

    opt = {"mat_py": a.mat_py, "karten": M.Karten(os.path.dirname(karten)), "records": None}
    erg = {e["sid"]: e for e in M.partie((pool, gid, None, zdir, opt))}
    pruefe("zwei Menschen gefunden", sorted(erg) == [1, 2], str(sorted(erg)))
    s1, s2 = erg[1]["summe"], erg[2]["summe"]
    v1 = erg[1]["verlauf"]
    pruefe("Tier 1, Einheiten, Zusatz gelesen", erg[1]["tier1"] and erg[1]["units"] and erg[1]["zusatz"])
    pruefe("Proben alle 250 Ticks zu Lebzeiten", v1["t"] == [250, 500, 750, 1000], str(v1["t"]))
    pruefe("Komponenten 1 → 2 nach dem zweiten Stück", v1["komponenten"] == [1, 2, 2, 2], str(v1["komponenten"]))
    pruefe("Gebiet 25 → 34 Kacheln", v1["gebiet"] == [25 / 800, 34 / 800, 34 / 800, 34 / 800], str(v1["gebiet"]))
    pruefe("Küste Median (5 von 34)", abs(s1["kueste"] - 5 / 34) < 1e-12, str(s1["kueste"]))
    # Raster 16×16 auf 40×20: Quadrat x 2..6 → Spalten {0,1,2}, y 2..6 → Zeilen {1,2,3,4} = 12;
    # fernes x 30..32 → Spalte 12 (32·16/40 = 12,8), y 10..12 → Zeilen {8,9} = 2; zusammen 14
    pruefe("Raster: 12 + 2 Felder", s1["raster_max"] == 14, str(s1["raster_max"]))
    pruefe("gebaut City 1, Port 1, Aufwertung 1", s1["gebaut_city"] == 1 and s1["gebaut_port"] == 1 and s1["aufgewertet"] == 1)
    pruefe("verloren 1 (Port erobert), Boot 1, Nuke 1",
           s1["strukturen_verloren"] == 1 and s1["boote"] == 1 and s1["nukes"] == 1,
           f"{s1['strukturen_verloren']}/{s1['boote']}/{s1['nukes']}")
    pruefe("Ausgaben = 1,25 Mio (Kostenformeln)", s1["gold_ausgegeben"] == 1_250_000, str(s1["gold_ausgegeben"]))
    # Samples von Tick 50 bis 950: Δ Bestand 1,8 Mio + Ausgaben 1,25 Mio über 900 Ticks = 1,5 min
    ein = 2_000 * (950 - 50) + 1_250_000
    pruefe("Einkommen = Δ Bestand + Ausgaben über die Sample-Spanne, je Minute",
           s1["gold_min"] == round(ein / ((950 - 50) / 600)), f"{s1['gold_min']} vs {round(ein / ((950 - 50) / 600))}")
    pruefe("Bauwerke je Probe (City ab 120, Port 450–700)", v1["strukturen"] == [1, 2, 1, 1], str(v1["strukturen"]))
    pruefe("Platz: sid 1 lebt, sid 2 stirbt zuerst", s1["platz"] in (1, 2) and s2["platz"] == 3 and s1["von"] == 3,
           f"{s1['platz']}/{s2['platz']} von {s1['von']}")
    pruefe("Überleben sid 2 endet bei der letzten Probe mit Gebiet", s2["ueberleben"] == 550 - 50, str(s2["ueberleben"]))
    pruefe("Angriffe je 1000 = 3 Klicks / 950 Ticks", abs(s1["angriffe_je_1000"] - 3000 / 950) < 1e-3)
    pruefe("Allianzanfrage von Mensch 2 erhalten", s1["anfragen_erhalten"] == 1 and s2["anfragen_gesendet"] == 1)
    pruefe("Allianz geschlossen 1, gleichzeitig max 1", s1["allianzen_neu"] == 1 and s1["allianzen_max"] == 1,
           f"{s1['allianzen_neu']}/{s1['allianzen_max']}")
    pruefe("Verrat 1 (own.betrayals), Embargo 1", s1["verrat"] == 1 and s1["embargos"] == 1,
           f"{s1['verrat']}/{s1['embargos']}")
    pruefe("laufende Angriffe auf mich (Zusatz) = 2", s1["angriffe_ein_aktiv"] == 2.0)
    pruefe("nicht ableitbar bleibt leer", s1["angriffe_erhalten_je_1000"] is None and s1["verraten_worden"] is None)

    # main() über den Ordner (Val-Stichprobe, UR-Liste mit sid 1)
    liste = os.path.join(tmp, "spieler_ur.tsv")
    open(liste, "w").write(f"# gid\tsid\tsplit\n{gid}\t1\tval\n")
    aus = os.path.join(tmp, "m.json")
    rc = M.main(["--pool", os.path.dirname(pool), "--zusatz", zdir, "--karten", os.path.dirname(karten),
                 "--ur-pool", os.path.dirname(pool), "--ur-zusatz", zdir, "--ur-liste", liste, "--aus", aus]
                + (["--mat-py", a.mat_py] if a.mat_py else []))
    b = json.load(open(aus))
    pruefe("main: beide Gruppen", rc == 0 and set(b["gruppen"]) == {"mensch", "ultimus_rex"})
    pruefe("main: UR nur sid 1, Mensch beide", b["gruppen"]["ultimus_rex"]["spieler"] == 1
           and b["gruppen"]["mensch"]["spieler"] == 2)
    pruefe("main: Kurven und Tabelle", len(b["gruppen"]["mensch"]["kurven"]["gebiet"]) == 4
           and b["gruppen"]["ultimus_rex"]["spielweise"]["gebaut_city"]["median"] == 1)
    print("\n" + ("ALLES OK" if not FEHLER else f"{len(FEHLER)} FEHLER: {FEHLER}"))
    return 1 if FEHLER else 0


if __name__ == "__main__":
    sys.exit(main())

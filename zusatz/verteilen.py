#!/usr/bin/env python3
"""Verteilt den Zusatzlauf auf die Flotte: Anteile nach Kernen, Daten und Startbefehle.

Erzeugt je Host unter <aus>/<host>/:
  auftrag.tsv   gid, commit8, Record-Pfad (relativ zur Record-Wurzel), Shard im Pool
  records.txt   Dateiliste für rsync --files-from (die Records dieses Hosts)
  meta.txt      Dateiliste für rsync --files-from (die <shard>/<gid>.meta.zst)
und druckt die Befehle: Daten schieben, Bild laden, Lauf starten, Fortschritt sehen.
Ausgeführt wird nichts (ausser mit --senden werden die rsync-Läufe gestartet).

Aufteilung nach Gewicht (Kerne), nicht gleichmässig; die gids werden nach sha1 gemischt,
damit grosse und kleine Partien überall gleich verteilt sind.

  python3 zusatz/verteilen.py --liste trainer/listen/ffa_min200_20spieler.txt \\
      --records ~/mat-dev/records.tsv --records-wurzel ~/of-records/records \\
      --pool ~/of-mat2-out --aus ~/mat-dev/zusatz-verteilung
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import subprocess
import sys

# Name, Ziel für ssh/rsync, Jobs, Heimatordner, erreichbar von, Tempo je Kern, Zeitgrenze (h, 0 = keine)
#
# Tempo ist gemessen, nicht geschätzt: gleiche V8-Einkernlast (Map/Objekte/Float, 4 s) je Host,
# arch = 1.0. Ryzen 7800X3D 22998 Runden; apollo (i7-8700K) 10964 im Container gegen arch 21424
# im selben Container = 0.51; node-1 9380 = 0.41; node-2 9475 = 0.41; node-3l (i5-4590) 9874 = 0.43.
# Die Anteile gehen deshalb nach Jobs × Tempo — nach Kernen allein wären die kleinen Rechner
# rund 2,4-mal so lange beschäftigt wie arch und arch könnte nicht früh abgeschaltet werden.
HOSTS = [
    ("arch", None, 12, "~", "lokal", 1.00, 0),
    ("apollo", "benutzer@apollo.example", 5, "~", "arch", 0.51, 0),   # Temperaturregler: höchstens 5
    ("node-1", "benutzer@node-1.example", 3, "~", "arch", 0.41, 0),
    ("node-2", "benutzer@node-2.example", 3, "~", "arch", 0.41, 0),
    ("node-3l", "lucas@node-3.example", 2, "/home/lucas", "mac", 0.43, 8),   # geliehen: 2 Jobs, endet selbst
]

META_PFLICHT = ("tick", "sid", "opps")      # ohne diese Felder ist es nicht der v2-Pool
ARCH = "benutzer@arch.example"              # Quelle für Hosts, die arch nicht erreicht (Weg über den Mac)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--liste", required=True)
    ap.add_argument("--records", help="records.tsv (Pfad, gid, commit8, …) — nur als Abkürzung; "
                                      "fehlt ein Eintrag, wird gitCommit aus der Aufzeichnung gelesen")
    ap.add_argument("--baeume", help="Ordner mit <sha8>/vendor/openfront; prüft, dass jeder Commit einen Baum hat")
    ap.add_argument("--records-wurzel", required=True, help="wo die Record-JSON auf arch liegen")
    ap.add_argument("--pool", required=True)
    ap.add_argument("--aus", required=True, help="Ordner für Auftrags- und Dateilisten")
    ap.add_argument("--tag", default="of-zusatz:2", help="Bild, auf jedem Host lokal gebaut "
                                                          "(docker build -f zusatz/docker/Dockerfile)")
    ap.add_argument("--ziel", default="zusatz", help="Ordnername auf den Hosts (unter dem Heimatordner)")
    ap.add_argument("--sekunden-je-partie", type=float, default=41.0, help="gemessen auf arch")
    ap.add_argument("--senden", action="store_true", help="rsync wirklich ausführen (sonst nur drucken)")
    a = ap.parse_args(argv)
    wurzel = os.path.expanduser(a.records_wurzel)
    pool = os.path.expanduser(a.pool)
    aus = os.path.expanduser(a.aus)

    with open(os.path.expanduser(a.liste)) as f:
        gids = [z.strip() for z in f if z.strip() and not z.startswith("#")]
    commit = {}
    if a.records and os.path.exists(os.path.expanduser(a.records)):
        with open(os.path.expanduser(a.records)) as f:
            next(f, None)
            for z in f:
                t = z.rstrip("\n").split("\t")
                if len(t) > 2:
                    commit[t[1]] = t[2]
    _pool_pruefen(pool)
    shard = {}
    for n in sorted(os.listdir(pool)):
        p = os.path.join(pool, n)
        if os.path.isdir(p):
            for d in os.listdir(p):
                if d.endswith(".meta.zst"):
                    shard[d[:-len(".meta.zst")]] = n

    offen, ohne_record, ohne_meta, ohne_commit, aus_datei = [], 0, 0, 0, 0
    for gid in gids:
        rel = f"{gid[:2]}/{gid}.json"
        voll = os.path.join(wurzel, rel)
        if not os.path.exists(voll):
            ohne_record += 1
            continue
        if gid not in shard:
            ohne_meta += 1
            continue
        c8 = commit.get(gid)
        if c8 is None:                      # Index unvollständig: gitCommit steht im Kopf der Aufzeichnung
            c8 = _commit_aus_record(voll)
            aus_datei += 1
        if not c8:
            ohne_commit += 1
            continue
        offen.append((gid, c8, rel, shard[gid]))
    offen.sort(key=lambda x: hashlib.sha1(x[0].encode()).hexdigest())   # gemischt, aber reproduzierbar
    print(f"[verteilen] {len(offen)} Partien ({aus_datei} Commits aus der Aufzeichnung gelesen), "
          f"aussen vor: {ohne_record} ohne Record, {ohne_meta} ohne Metadatei, {ohne_commit} ohne Commit")
    verteilung = {}
    for _, c8, _, _ in offen:
        verteilung[c8] = verteilung.get(c8, 0) + 1
    print(f"[verteilen] Engine-Commits: {verteilung}")
    if a.baeume:                            # ohne Baum im Bild überspringt der Treiber die Partie still
        b = os.path.expanduser(a.baeume)
        fehlend = [c for c in verteilung if not os.path.isdir(os.path.join(b, c))]
        if fehlend:
            raise SystemExit(f"[verteilen] kein Baum für {fehlend} in {b} — Bild neu bauen, sonst fallen "
                             f"{sum(verteilung[c] for c in fehlend)} Partien aus")
        print(f"[verteilen] Bäume vorhanden für alle {len(verteilung)} Commits")

    gewicht = sum(h[2] * h[5] for h in HOSTS)          # Jobs × gemessenes Tempo
    i, plan = 0, []
    for k, (name, ziel, kerne, heim, weg, tempo, grenze) in enumerate(HOSTS):
        n = len(offen) - i if k == len(HOSTS) - 1 else round(len(offen) * kerne * tempo / gewicht)
        teil, i = offen[i:i + n], i + n
        d = os.path.join(aus, name)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "auftrag.tsv"), "w") as f:
            for gid, c8, rel, sh in teil:
                f.write(f"{gid}\t{c8}\t{rel}\t{sh}\n")
        with open(os.path.join(d, "records.txt"), "w") as f:
            f.write("".join(f"{rel}\n" for _, _, rel, _ in teil))
        with open(os.path.join(d, "meta.txt"), "w") as f:
            f.write("".join(f"{sh}/{gid}.meta.zst\n" for gid, _, _, sh in teil))
        std = len(teil) * a.sekunden_je_partie / (kerne * tempo) / 3600
        plan.append((name, ziel, kerne, heim, weg, grenze, len(teil), std, d))
        print(f"  {name:8s} {kerne:2d} Jobs × {tempo:.2f}, {len(teil):5d} Partien, ~{std:.1f} h"
              + (f" (Zeitgrenze {grenze} h)" if grenze else ""))

    print("\n== Daten schieben (von arch; Hosts, die arch nicht erreicht, über den Mac) ==")
    for name, ziel, kerne, heim, weg, grenze, n, std, d in plan:
        if ziel is None:
            print(f"# {name}: nichts zu schieben, Daten liegen lokal ({wurzel}, {pool})")
            continue
        v = f"{heim}/{a.ziel}"
        if weg != "arch":
            # Über den Mac NUR als Datenstrom, nie über einen Mac-Ordner: dessen Dateisystem
            # unterscheidet Gross/Klein nicht, die gids aber schon. Beim Zusatzlauf am 13.09.
            # fielen so 425 Präfixordner auf 363 zusammen, 70 von 459 Records lagen danach
            # im falschen Ordner (Bd/ statt bd/) und die Partien brachen mit rc=1 ab.
            print(f"# {name}: von arch nicht erreichbar — vom Mac aus als Datenstrom")
            print(f"ssh {ziel} 'mkdir -p {v}/in {v}/pool {v}/out'")
            for was, quelle, unter in (("records", wurzel, "in"), ("meta", pool, "pool")):
                print(f"ssh {ARCH} 'tar -C {quelle} -cf - -T {d}/{was}.txt' | ssh {ziel} 'tar -C {v}/{unter} -xf -'")
            print(f"ssh {ARCH} 'cat {d}/auftrag.tsv' | ssh {ziel} 'cat > {v}/auftrag.tsv'")
            continue
        schritte = [["ssh", ziel, f"mkdir -p {v}/in {v}/pool {v}/out"]]
        for was, quelle, unter in (("records", wurzel, "in"), ("meta", pool, "pool")):
            schritte.append(["rsync", "-a", f"--files-from={d}/{was}.txt", f"{quelle}/", f"{ziel}:{v}/{unter}/"])
        schritte.append(["scp", "-q", f"{d}/auftrag.tsv", f"{ziel}:{v}/auftrag.tsv"])
        for s in schritte:
            print(" ".join(s))
            if a.senden:
                subprocess.run(s, check=True)                # Liste statt Shell
    print(f"\n== Starten (Bild {a.tag} ist je Host lokal gebaut; Container endet von selbst) ==")
    for name, ziel, kerne, heim, weg, grenze, n, std, d in plan:
        v = f"{heim}/{a.ziel}"
        extra = f" -e EXTRA='--max-stunden {grenze}'" if grenze else ""
        lauf = (f"docker run -d --name of-zusatz --cpus {kerne} -e JOBS={kerne}{extra} "
                f"-v {v}/in:/in:ro -v {v}/pool:/pool:ro -v {v}/out:/out "
                f"-v {v}/auftrag.tsv:/auftrag.tsv:ro {a.tag}")
        if ziel is None:
            lauf = lauf.replace(f"{v}/in:", f"{wurzel}:").replace(f"{v}/pool:", f"{pool}:")
            lauf = lauf.replace(f"{v}/auftrag.tsv:", f"{d}/auftrag.tsv:")
            print(f"# {name}\nmkdir -p {v}/out && {lauf}")
        else:
            print(f"ssh {ziel} \"{lauf}\"")
    print("\n== Fortschritt und Ende ==")
    for name, ziel, kerne, heim, weg, grenze, n, std, d in plan:
        v = f"{heim}/{a.ziel}"
        vorn = "" if ziel is None else f"ssh {ziel} "
        print(f"# {name}: fertig, wenn der Container weg ist und {n} Dateien liegen")
        print(f"{vorn}'docker ps -a --filter name=of-zusatz --format \"{{{{.Status}}}}\"; "
              f"ls {v}/out 2>/dev/null | wc -l; docker logs --tail 2 of-zusatz 2>&1 | tail -2'")


def _pool_pruefen(pool: str) -> None:
    """Erste Metazeile ansehen: ist das wirklich der v2-Pool?

    Auf der Flotte liegen noch Metadateien aus Vorstufen des Materialisierers herum
    (nur turn/clientID/oppIds, ohne tick/sid). lauf.ts rechnet damit still lauter
    Nullzeilen — deshalb hier hart abbrechen statt stundenlang Müll zu erzeugen.
    """
    import json
    import subprocess
    for wurzel, _, dateien in os.walk(pool):
        for d in dateien:
            if not d.endswith(".meta.zst"):
                continue
            roh = subprocess.run(["zstd", "-dc", os.path.join(wurzel, d)],
                                 capture_output=True).stdout.split(b"\n", 1)[0]
            if not roh:
                continue
            m = json.loads(roh)
            fehlt = [f for f in META_PFLICHT if f not in m]
            if fehlt:
                raise SystemExit(f"[verteilen] {os.path.join(wurzel, d)} hat kein {fehlt} — das ist nicht der "
                                 f"v2-Pool, sondern eine Vorstufe. Der Zusatzlauf braucht tick/sid je Zeile.")
            print(f"[verteilen] Pool geprüft: {d} hat {', '.join(META_PFLICHT)}")
            return
    raise SystemExit(f"[verteilen] keine .meta.zst unter {pool}")


def _commit_aus_record(pfad: str) -> str:
    """gitCommit aus dem Kopf der Aufzeichnung (zweiter Schlüssel, deshalb reichen wenige KB)."""
    with open(pfad, "rb") as f:
        kopf = f.read(8192)
    m = re.search(rb'"gitCommit"\s*:\s*"([0-9a-f]{8})', kopf)
    return m.group(1).decode() if m else ""


if __name__ == "__main__":
    sys.exit(main())

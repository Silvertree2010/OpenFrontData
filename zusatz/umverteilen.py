#!/usr/bin/env python3
"""Zusatzlauf umverteilen: Rest von arch auf die Flotte, damit alle etwa gleichzeitig fertig sind.

Läuft auf arch (im Repo-Baum, braucht materializer/py/reader.py für is_val). Liest je Host
Auftrag und fertige Dateien, verteilt den offenen Rest nach gemessener Rate (Partien/h)
und schreibt je angefassten Host einen neuen Auftrag:
  - Val-Partien stehen am ENDE (der Treiber nimmt von hinten), werden also zuerst gerechnet;
    so ist das Eval-Set beim Trainingsstart vollständig. Offene Val-Partien von Hosts, die
    nicht angefasst werden (node-3l, geliehen), rechnet arch zusätzlich vorneweg — doppelt
    gerechnet schadet nicht, der Einsammler nimmt, was zuerst da ist.
  - Zusätzliche Partien für einen Host kommen vom Anfang des arch-Rests (die rechnete arch
    zuletzt); ihre Eingaben schiebt rsync von arch.
Ohne --ausfuehren wird nur gerechnet und geschrieben. Mit --ausfuehren: Eingaben schieben,
dann je Host Container anhalten, Auftrag tauschen, neu starten (fertige Partien überspringt
der Treiber; laufende gehen verloren, das sind Minuten), auf apollo den Regler neu starten.

  python3 zusatz/umverteilen.py --raten arch=450,apollo=200,node-1=97,node-2=129 [--ausfuehren]
"""
from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys

HIER = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HIER), "materializer", "py"))
sys.path.insert(0, HIER)
import reader as R  # noqa: E402
from verteilen import HOSTS  # noqa: E402

APOLLO = "benutzer@apollo.example"
JOBS = {"arch": 8}                     # arch bekommt weniger Jobs: Trainer und Lader brauchen CPU


def auf(name, ziel, weg, cmd, eingabe=None):
    """Befehl auf dem Host; Hosts, die arch nicht erreicht, über apollo."""
    if ziel is None:
        argv = ["bash", "-c", cmd]
    elif weg == "mac":
        argv = ["ssh", APOLLO, f"ssh {ziel} {shlex.quote(cmd)}"]
    else:
        argv = ["ssh", ziel, cmd]
    return subprocess.run(argv, input=eingabe, capture_output=True, text=True, check=True).stdout


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--raten", required=True, help="gemessene Partien/h je angefasstem Host, name=zahl,…")
    ap.add_argument("--aus", default="~/mat-dev/zusatz-umverteilung")
    ap.add_argument("--records-wurzel", default="~/of-records/records")
    ap.add_argument("--pool", default="~/of-mat2-out")
    ap.add_argument("--tag", default="of-zusatz:2")
    ap.add_argument("--ausfuehren", action="store_true")
    a = ap.parse_args(argv)
    raten = {k: float(v) for k, v in (x.split("=") for x in a.raten.split(","))}
    aus = os.path.expanduser(a.aus)
    wurzel, pool = os.path.expanduser(a.records_wurzel), os.path.expanduser(a.pool)
    H = {h[0]: h for h in HOSTS}

    auftrag, fertig = {}, {}
    for name, ziel, jobs, heim, weg, tempo, grenze in HOSTS:
        auftrag[name] = [z.split("\t") for z in auf(name, ziel, weg, "cat ~/zusatz/auftrag.tsv").splitlines()
                         if z.strip()]
        fertig[name] = {n[:-len(".zusatz.zst")] for n in auf(name, ziel, weg, "ls ~/zusatz/out").split()
                        if n.endswith(".zusatz.zst")}
    alle_fertig = set().union(*fertig.values())
    rest = {h: [t for t in auftrag[h] if t[0] not in alle_fertig] for h in auftrag}
    anfassen = [h for h in raten if h in H]
    fremde_val = [t for h in rest if h not in anfassen for t in rest[h] if R.is_val(t[0])]
    arch_nv = [t for t in rest["arch"] if not R.is_val(t[0])]
    T = sum(len(rest[h]) for h in anfassen) / sum(raten[h] for h in anfassen)
    print(f"[umverteilen] fertig {len(alle_fertig)}, offen {sum(len(r) for r in rest.values())}; "
          f"ausgeglichen nach ~{T:.1f} h für {anfassen}")
    extra, i = {}, 0
    for h in anfassen:
        if h != "arch":
            n = max(0, round(raten[h] * T) - len(rest[h]))
            extra[h], i = arch_nv[i:i + n], i + n
    neu = {}
    for h in anfassen:
        if h == "arch":
            neu[h] = arch_nv[i:] + [t for t in rest["arch"] if R.is_val(t[0])] + fremde_val
        else:
            mine = rest[h] + extra[h]
            neu[h] = [t for t in mine if not R.is_val(t[0])] + [t for t in mine if R.is_val(t[0])]
    for h in anfassen:
        d = os.path.join(aus, h)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "auftrag.tsv"), "w") as f:
            f.write("".join("\t".join(t) + "\n" for t in neu[h]))
        ex = extra.get(h, [])
        with open(os.path.join(d, "records.txt"), "w") as f:
            f.write("".join(t[2] + "\n" for t in ex))
        with open(os.path.join(d, "meta.txt"), "w") as f:
            f.write("".join(f"{t[3]}/{t[0]}.meta.zst\n" for t in ex))
        nval = sum(R.is_val(t[0]) for t in neu[h])
        print(f"  {h:8s} offen {len(rest[h]):5d}  +{len(ex):4d} von arch  → {len(neu[h]):5d} "
              f"(davon {nval} Val zuerst), ~{len(neu[h]) / raten[h]:.1f} h bei {raten[h]:.0f}/h")
    ohne = [h for h in rest if h not in anfassen]
    for h in ohne:
        print(f"  {h:8s} offen {len(rest[h]):5d}  bleibt unverändert")
    if not a.ausfuehren:
        print("[umverteilen] nur gerechnet; mit --ausfuehren schieben und neu starten")
        return

    for h in anfassen:                        # 1. Eingaben schieben und neuen Auftrag hinlegen
        name, ziel, jobs, heim, weg, tempo, grenze = H[h]
        d = os.path.join(aus, h)
        if ziel is None:
            continue
        v = f"{heim}/zusatz"
        for was, quelle, unter in (("records", wurzel, "in"), ("meta", pool, "pool")):
            subprocess.run(["rsync", "-a", f"--files-from={d}/{was}.txt", f"{quelle}/", f"{ziel}:{v}/{unter}/"],
                           check=True)
        subprocess.run(["scp", "-q", f"{d}/auftrag.tsv", f"{ziel}:{v}/auftrag.neu"], check=True)
        print(f"[umverteilen] {h}: Eingaben und Auftrag geschoben")
    for h in anfassen:                        # 2. Container neu starten
        name, ziel, jobs, heim, weg, tempo, grenze = H[h]
        v = os.path.expanduser("~/zusatz") if ziel is None else f"{heim}/zusatz"
        if ziel is None:
            subprocess.run(["cp", os.path.join(aus, h, "auftrag.tsv"), f"{v}/auftrag.neu"], check=True)
        inn = wurzel if ziel is None else f"{v}/in"
        pl = pool if ziel is None else f"{v}/pool"
        j = JOBS.get(h, jobs)
        cpus = str(j) if h in JOBS else "$(docker inspect -f '{{.HostConfig.NanoCpus}}' of-zusatz | awk '{printf \"%.2f\", $1/1e9}')"
        extra_env = f" -e EXTRA='--max-stunden {grenze}'" if grenze else ""
        cmd = (f"c={cpus}; docker stop -t 5 of-zusatz >/dev/null; docker rm of-zusatz >/dev/null; "
               f"mv {v}/auftrag.neu {v}/auftrag.tsv; "
               f"docker run -d --name of-zusatz --cpus $c -e JOBS={j}{extra_env} -v {inn}:/in:ro -v {pl}:/pool:ro "
               f"-v {v}/out:/out -v {v}/auftrag.tsv:/auftrag.tsv:ro {a.tag} >/dev/null && echo \"läuft, $c CPUs\"")
        if h == "apollo":                     # Regler folgt dem neuen Container, Start beim aktuellen Wert
            cmd += ("; P=$(cat ~/zusatz/regler.pid 2>/dev/null); [ -n \"$P\" ] && kill $P 2>/dev/null; "
                    "h=$(awk -v c=$c 'BEGIN{printf \"%d\", c*100}'); "
                    "setsid nohup bash ~/zusatz/regler.sh $h >> ~/zusatz/regler.log 2>&1 < /dev/null & "
                    "echo $! > ~/zusatz/regler.pid; echo \"Regler neu bei $h\"")
        print(f"[umverteilen] {h}: {auf(h, ziel, weg, cmd).strip()}")


if __name__ == "__main__":
    sys.exit(main())

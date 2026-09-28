#!/usr/bin/env python3
"""Treiber für den Zusatzlauf: viele Partien parallel nachspielen.

Je Partie ein Prozess (`npx tsx zusatz/src/lauf.ts`), gruppiert nach Engine-Commit, denn
jede Aufzeichnung läuft nur auf ihrem Commit bit-genau. Zu jedem Commit gehört ein Baum
<baeume>/<sha8>/ mit vendor/openfront, node_modules und tsconfig.json (wie ~/mat-dev/w).
Der Pool wird nur gelesen, geschrieben wird allein in --aus.

  python3 zusatz/treiber.py --liste trainer/listen/ffa_min200_20spieler.txt \\
      --pool ~/of-mat2-out --records ~/mat-dev/records.tsv --baeume ~/mat-dev/w \\
      --aus ~/of-mat2-zusatz --jobs 12 [--limit 5]

Fertige Partien werden übersprungen (Datei vorhanden), der Lauf ist also wiederaufnehmbar.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from collections import Counter


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--liste", help="Datei mit gids (waehle_partien.py)")
    ap.add_argument("--auftrag", help="Auftragsdatei je Host (zusatz/verteilen.py): "
                                      "gid, commit8, Record-Pfad relativ zu --in, Shard im Pool")
    ap.add_argument("--in", dest="rein", default="", help="Wurzel der Records (mit --auftrag)")
    ap.add_argument("--pool", required=True)
    ap.add_argument("--records", help="records.tsv: Pfad, gid, commit8, … (ohne --auftrag)")
    ap.add_argument("--baeume", required=True, help="Ordner mit <sha8>/vendor/openfront")
    ap.add_argument("--aus", required=True)
    ap.add_argument("--jobs", type=int, default=8)
    ap.add_argument("--limit", type=int, default=0, help="nur die ersten N Partien (Test)")
    ap.add_argument("--nice", type=int, default=10)
    ap.add_argument("--max-stunden", type=float, default=0.0,
                    help="nach dieser Zeit keine neue Partie mehr beginnen und enden (geliehene Rechner)")
    ap.add_argument("--min-frei-mb", type=int, default=3000,
                    help="unter diesem freien Arbeitsspeicher (MemAvailable des Wirts) keine neue Partie "
                         "beginnen; 0 schaltet die Bremse ab")
    a = ap.parse_args(argv)

    # lauf.ts muss IM Baum liegen: seine Importe zeigen relativ auf ../../vendor/openfront.
    # Ausserdem muss vendor eine echte Kopie sein (cp -al), kein Symlink — sonst greift
    # useDefineForClassFields aus der tsconfig nicht und die Engine stirbt im ersten Tick.
    rec: dict[str, tuple[str, str]] = {}      # gid → (Record-Pfad, commit8)
    shard: dict[str, str] = {}                # gid → Unterordner im Pool (aus der Auftragsdatei)
    if a.auftrag:
        with open(os.path.expanduser(a.auftrag)) as f:
            for z in f:
                t = z.rstrip("\n").split("\t")
                if len(t) >= 4 and not z.startswith("#"):
                    rec[t[0]] = (os.path.join(os.path.expanduser(a.rein), t[2]), t[1])
                    shard[t[0]] = t[3]
        gids = list(rec)
    else:
        with open(os.path.expanduser(a.liste)) as f:
            gids = [z.strip() for z in f if z.strip() and not z.startswith("#")]
        with open(os.path.expanduser(a.records)) as f:
            next(f, None)
            for z in f:
                t = z.rstrip("\n").split("\t")
                if len(t) > 2:
                    rec[t[1]] = (t[0], t[2])
    pool, aus = os.path.expanduser(a.pool), os.path.expanduser(a.aus)
    os.makedirs(aus, exist_ok=True)

    offen, z = [], Counter()
    for gid in gids:
        if gid not in rec:
            z["kein_record"] += 1
            continue
        if os.path.exists(os.path.join(aus, f"{gid}.zusatz.zst")):
            z["schon_da"] += 1
            continue
        pfad, c8 = rec[gid]
        baum = os.path.join(os.path.expanduser(a.baeume), c8)
        if not _startbefehl(baum, a.nice):
            z["kein_baum_" + c8] += 1
            continue
        if os.path.isfile(os.path.join(baum, "zusatz", "src", "lauf.ts")) \
                and os.path.islink(os.path.join(baum, "vendor")):
            raise SystemExit(f"{baum}/vendor ist ein Symlink; echte Kopie nötig: "
                             f"rm vendor && cp -al <quelle>/vendor {baum}/vendor")
        ordner = os.path.join(pool, shard[gid]) if gid in shard else os.path.join(pool, _shard(pool, gid))
        offen.append((gid, pfad, baum, ordner))       # baum: eigener Baum je Engine-Commit
        if a.limit and len(offen) >= a.limit:
            break
    print(f"[treiber] {len(offen)} Partien offen, {dict(z)}", flush=True)

    laufend: list[tuple] = []
    t0, fertig, bytes_ges = time.time(), 0, 0
    frist = t0 + a.max_stunden * 3600 if a.max_stunden else float("inf")
    abgebrochen = False
    rss: dict[str, int] = {}          # laufende Partie → bisheriger Spitzenspeicher in MB
    spitzen: list[int] = []           # Spitzenspeicher der fertigen Partien
    frei_min, gemessen, bremse = _frei_mb(), 0.0, 0.0
    print(f"[treiber] {a.jobs} Jobs, {frei_min} MB frei, Bremse bei {a.min_frei_mb} MB", flush=True)
    while offen or laufend:
        if offen and time.time() >= frist:
            # laufende Partien noch fertig rechnen, keine neuen mehr: der Lauf endet von selbst
            z["nicht_begonnen"] = len(offen)
            offen = []
            abgebrochen = True
            print(f"[treiber] Zeitgrenze {a.max_stunden} h erreicht, {z['nicht_begonnen']} Partien offen "
                  f"(erneut startbar, fertige werden übersprungen)", flush=True)
        while offen and len(laufend) < a.jobs:
            frei = _frei_mb()
            if a.min_frei_mb and laufend and frei < a.min_frei_mb:
                # Speicherbremse: lieber langsamer als den Wirt aus dem Netz werfen
                if bremse == 0 or time.time() - bremse > 60:
                    print(f"[treiber] warte, nur {frei} MB frei (< {a.min_frei_mb}), {len(laufend)} laufen",
                          flush=True)
                    bremse = time.time()
                z["gebremst"] += 1
                break
            gid, pfad, baum, ordner = offen.pop()
            cmd = _startbefehl(baum, a.nice) + [pfad, ordner, gid, aus]
            laufend.append((gid, subprocess.Popen(cmd, cwd=baum, stdout=subprocess.PIPE,
                                                  stderr=subprocess.DEVNULL, text=True)))
        if time.time() - gemessen > 2.0:                # Spitzenspeicher je Partie mitschreiben
            gemessen = time.time()
            frei_min = min(frei_min, _frei_mb())
            for gid, p in laufend:
                rss[gid] = max(rss.get(gid, 0), _rss_mb(p.pid))
        for eintrag in list(laufend):
            gid, p = eintrag
            if p.poll() is None:
                continue
            laufend.remove(eintrag)
            spitzen.append(rss.pop(gid, 0))
            aus_zeile = (p.stdout.read() or "").strip().split("\n")[-1]
            if p.returncode == 0 and aus_zeile.startswith("{"):
                fertig += 1
                j = json.loads(aus_zeile)
                bytes_ges += j.get("bytes", 0)
                z["ok"] += 1
                if j.get("berechnet", 1) == 0:
                    # Nichts gerechnet heisst fast immer: der Pool ist nicht der v2-Pool
                    # (Metazeilen ohne tick/sid). Früh abbrechen statt Nullen zu schreiben.
                    z["leer"] += 1
                    if z["leer"] >= 5 and z["leer"] == fertig:
                        raise SystemExit("[treiber] die ersten 5 Partien ergaben 0 berechnete Samples — "
                                         "hat der Pool tick/sid je Metazeile? (v2-Pool nötig)")
            else:
                z["fehler"] += 1
                print(f"[treiber] {gid} rc={p.returncode}", flush=True)
            if fertig % 25 == 0 or not laufend:
                dt = time.time() - t0
                print(f"[treiber] {fertig} fertig, {dt:.0f}s, {fertig / max(dt, 1e-9) * 3600:.0f} Partien/h, "
                      f"{bytes_ges / 2 ** 20:.0f} MB", flush=True)
        time.sleep(0.2)
    s = sorted(spitzen)
    print(f"[treiber] {'Zeitgrenze' if abgebrochen else 'fertig'}: {dict(z)}, {time.time() - t0:.0f}s, "
          f"Speicher je Partie: Median {s[len(s) // 2] if s else 0} MB, Spitze {s[-1] if s else 0} MB, "
          f"am wenigsten frei: {frei_min} MB", flush=True)


def _startbefehl(baum: str, nice: int) -> list[str]:
    """Wie dieser Baum gestartet wird: esbuild-Bündel (Bild) oder tsx (Arbeitsbaum).

    Das Bündel ist der Normalfall im Container; der tsx-Weg bleibt für einen Baum
    mit Engine-Quellen (dann muss vendor eine echte Kopie sein, kein Symlink).
    """
    buendel = os.path.join(baum, "dist", "zusatz.mjs")
    if os.path.isfile(buendel):
        return ["nice", "-n", str(nice), "node", buendel]
    quelle = os.path.join(baum, "zusatz", "src", "lauf.ts")
    if os.path.isfile(quelle):
        return ["nice", "-n", str(nice), "npx", "tsx", quelle]
    return []


def _frei_mb() -> int:
    """MemAvailable des Wirts in MB (im Container bewusst der Wirt: den wollen wir schützen)."""
    try:
        with open("/proc/meminfo") as f:
            for z in f:
                if z.startswith("MemAvailable:"):
                    return int(z.split()[1]) // 1024
    except OSError:
        pass
    return 1 << 30          # unbekannt: nicht bremsen


def _rss_mb(pid: int) -> int:
    """Speicher des Prozessbaums (npx → tsx → node) in MB; nur der Kindprozess wird gezählt."""
    gesamt = 0
    for p in (pid, *_kinder(pid)):
        try:
            with open(f"/proc/{p}/statm") as f:
                gesamt += int(f.read().split()[1]) * os.sysconf("SC_PAGE_SIZE")
        except OSError:
            pass
    return gesamt // (1 << 20)


def _kinder(pid: int) -> list[int]:
    try:
        with open(f"/proc/{pid}/task/{pid}/children") as f:
            k = [int(x) for x in f.read().split()]
    except OSError:
        return []
    return k + [e for x in k for e in _kinder(x)]


def _shard(pool: str, gid: str) -> str:
    """Unterordner der Partie im Pool (s0…s21)."""
    for n in sorted(os.listdir(pool)):
        if os.path.exists(os.path.join(pool, n, f"{gid}.ok")):
            return n
    return ""


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""
Materialisierungs-Launcher für EINEN Knoten.

Liest lokale Records, gruppiert nach Engine-Commit (aus dem JSON), teilt je Gruppe
in N Häppchen und startet N Container gleichzeitig (--cpus=1 je Container, damit
Dienste nicht verhungern). Resume-fest: Spiele mit fertigem Shard werden übersprungen.

  mat_launch.py <records_dir> <out_dir> <n_parallel> [--cpus 1] [--max N]

Env-Variablen werden an die Container durchgereicht (NOOP_EVERY, NOOP_ONLY,
NOOP_ALIGN, THIN, SUFFIX) — sonst kaeme die Nichtstun-Abtastung nie im
Materializer an, weil der Code zwar live gemountet, die Umgebung aber nicht
vererbt wird.
"""
import json, os, sys, glob, subprocess, tempfile, time

def commit_of(path):
    try:
        with open(path) as f:
            return json.load(f).get("gitCommit", "")[:40]
    except Exception:
        return ""

def main():
    ENVDIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "env")
    records_dir = os.path.abspath(sys.argv[1])
    out_dir = os.path.abspath(sys.argv[2])
    n = int(sys.argv[3])
    cpus = sys.argv[sys.argv.index("--cpus")+1] if "--cpus" in sys.argv else "1"
    maxn = int(sys.argv[sys.argv.index("--max")+1]) if "--max" in sys.argv else 0
    os.makedirs(out_dir, exist_ok=True)

    # An die Container weiterzureichende Schalter (nur gesetzte).
    passthru = []
    for k in ("NOOP_EVERY", "NOOP_ONLY", "NOOP_ALIGN", "THIN", "SUFFIX"):
        if os.environ.get(k):
            passthru += ["-e", f"{k}={os.environ[k]}"]
    # Ein Nur-Nichtstun-Lauf schreibt <gid>.noop.* (siehe materialize.ts) — die
    # Resume-Pruefung muss denselben Namen suchen, sonst laeuft alles doppelt.
    suffix = os.environ.get("SUFFIX", ".noop" if os.environ.get("NOOP_ONLY") == "1" else "")
    if passthru:
        print(f"[launch] Schalter: {' '.join(passthru[1::2])}, Suffix '{suffix}'", flush=True)

    files = sorted(glob.glob(os.path.join(records_dir, "**", "*.json"), recursive=True))
    if maxn: files = files[:maxn]
    # nach Commit gruppieren, fertige überspringen
    groups = {}
    skipped = 0
    for p in files:
        gid = os.path.basename(p)[:-5]
        if os.path.exists(os.path.join(out_dir, f"{gid}{suffix}.meta.zst")):
            skipped += 1; continue
        c = commit_of(p)
        if not c: continue
        groups.setdefault(c, []).append(p)
    total = sum(len(v) for v in groups.values())
    print(f"[launch] {total} zu materialisieren, {skipped} übersprungen, "
          f"{len(groups)} Commit(s): {[(c[:8],len(v)) for c,v in groups.items()]}", flush=True)
    if total == 0:
        return

    chunkdir = tempfile.mkdtemp(prefix="matchunks_")
    procs = []
    # n = GESAMT-Container-Budget (≈ Kerne); pro Commit anteilig an Gruppengröße,
    # damit alle Container etwa gleich lang laufen (kein Leerlauf im Tail).
    for commit, paths in groups.items():
        k = max(1, round(n * len(paths) / max(total, 1)))
        chunks = [[] for _ in range(k)]
        for i, p in enumerate(paths):
            rel = os.path.relpath(p, records_dir)
            chunks[i % k].append(f"/in/{rel}")
        for ci, ch in enumerate(chunks):
            if not ch: continue
            cf = os.path.join(chunkdir, f"{commit[:8]}_{ci}.txt")
            open(cf, "w").write("\n".join(ch))
            cpuflag = [] if str(cpus) in ("0","") else [f"--cpus={cpus}"]
            # tsconfig zusätzlich mounten, falls vorhanden: die Engine braucht
            # useDefineForClassFields:false. So laufen alle Nodes identisch, egal
            # welches (evtl. ältere) of-mat-Image lokal gebacken wurde.
            tscfg = os.path.join(os.path.dirname(ENVDIR), "tsconfig.json")
            tsmount = ["-v", f"{tscfg}:/app/tsconfig.json:ro"] if os.path.exists(tscfg) else []
            cmd = ["docker", "run", "--rm", *cpuflag,
                   "-e", f"COMMIT={commit}", *passthru,
                   "-v", f"{records_dir}:/in:ro",
                   "-v", f"{out_dir}:/out",
                   "-v", f"{chunkdir}:/chunks:ro",
                   "-v", f"{ENVDIR}:/app/env:ro",   # Live-Code, kein Image-Neubau
                   *tsmount,
                   "of-mat", "--list", f"/chunks/{os.path.basename(cf)}", "/out"]
            log = open(os.path.join(out_dir, f".log_{commit[:8]}_{ci}"), "w")
            procs.append(subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT))
    print(f"[launch] {len(procs)} Container gestartet, warte…", flush=True)
    t0 = time.time()
    for p in procs:
        p.wait()
    # zählen
    done = len(glob.glob(os.path.join(out_dir, "*.meta.zst")))
    print(f"[launch] fertig in {(time.time()-t0)/60:.1f} min. Shards im Ordner: {done}", flush=True)

if __name__ == "__main__":
    main()

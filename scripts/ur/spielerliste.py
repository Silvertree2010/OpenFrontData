#!/usr/bin/env python3
"""arch: Spielerliste für train.py --spieler bauen.

Zeile: gid <TAB> sid <TAB> train|val. Nur Partien, die
  - in spieler_cid.tsv stehen (trackerfront-Name eindeutig, Siegerprobe bestanden),
  - eine .ok ohne Desync/Tick-Fehler mit hash.checked > 0 und samples > 0 haben,
  - vollständig sind (reader.ok_problems leer) und eine Zusatzdatei haben,
  - genau einen Spieler mit seiner clientID im hdr haben.
Val: nach Partie getrennt, sha1(gid) % 7 == 0 (~14 %). Partien, die auch im bc2/bc3-Pool
liegen (dort gesehen), kommen nie in Val.
Aufruf: python spielerliste.py ~/of-ur  (schreibt ~/of-ur/spieler_ur.tsv, druckt die Zählung)
"""
import collections, hashlib, json, os, sys

B = os.path.expanduser(sys.argv[1] if len(sys.argv) > 1 else "~/of-ur")
sys.path.insert(0, os.path.expanduser("~/ur-dev/code/materializer/py"))
import reader as R  # noqa: E402

cid = {}
orig = {}
for z in open(os.path.join(B, "spieler_cid.tsv")).read().split("\n")[1:]:
    t = z.split("\t")
    if len(t) >= 6:
        cid[t[0]], orig[t[0]] = t[1], t[5]
im_pool = set()
liste = os.path.expanduser("~/mat-dev/netz-dev/trainer/listen/ffa_min200_20spieler.txt")
if os.path.exists(liste):
    im_pool = {z.strip() for z in open(liste) if z.strip()}

grund, zeilen = collections.Counter(), []
samples, arten = collections.Counter(), collections.Counter()
je_commit = collections.Counter()
for host in sorted(os.listdir(os.path.join(B, "pool"))):
    d = os.path.join(B, "pool", host)
    for n in sorted(os.listdir(d)):
        if not n.endswith(".ok"):
            continue
        g = n[:-3]
        if g not in cid:
            grund["keine_zuordnung"] += 1; continue
        ok = json.load(open(os.path.join(d, n)))
        if ok.get("desync") is not None or ok.get("tick_error") or not (ok.get("hash") or {}).get("checked") \
                or not ok.get("samples"):
            grund["pruefung_nicht_bestanden"] += 1; continue
        if R.ok_problems(d, g):
            grund["unvollstaendig"] += 1; continue
        if not os.path.exists(os.path.join(B, "zusatz", f"{g}.zusatz.zst")):
            grund["keine_zusatzdatei"] += 1; continue
        hdr = json.load(open(os.path.join(d, f"{g}.hdr.json")))
        sid = [p["sid"] for p in hdr["players"] if p.get("clientID") == cid[g]]
        if len(sid) != 1:
            grund["sid_nicht_eindeutig"] += 1; continue
        val = int(hashlib.sha1(g.encode()).hexdigest()[:8], 16) % 7 == 0 and g not in im_pool
        zeilen.append((g, sid[0], "val" if val else "train"))
        with open(os.path.join(d, f"{g}.meta.zst"), "rb") as fm:
            for z in R.zdec(fm.read()).decode("utf-8").split("\n"):
                if z and json.loads(z).get("sid") == sid[0]:
                    samples["val" if val else "train"] += 1
                    arten[json.loads(z).get("kind")] += 1
        je_commit[orig[g]] += 1
        grund["ok"] += 1
with open(os.path.join(B, "spieler_ur.tsv"), "w") as f:
    f.write("# gid\tsid\tsplit — Ultimus_Rex (trackerfront 2f4e9fe5)\n")
    f.write("".join(f"{g}\t{s}\t{v}\n" for g, s, v in zeilen))
print(dict(grund))
print("train/val:", collections.Counter(v for _, _, v in zeilen))
print("auch im bc-Pool:", sum(1 for g, _, _ in zeilen if g in im_pool))
print("je Original-Commit:", je_commit.most_common())
print("seine Samples:", dict(samples), "Arten:", dict(arten))
with open(os.path.join(B, "spieler_ur.zahlen.json"), "w") as f:
    json.dump({"partien": dict(collections.Counter(v for _, _, v in zeilen)), "samples": dict(samples),
               "arten": dict(arten), "je_commit": dict(je_commit), "gruende": dict(grund)}, f)

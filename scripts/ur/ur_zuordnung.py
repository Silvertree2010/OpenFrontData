#!/usr/bin/env python3
"""Ultimus_Rex, läuft auf node-1 in ~/of-ur.

  vorbereiten  Records für die Engine-Bäume vorbereiten und auf die Hosts verteilen.
               88cc95d8/8b45be57 unverändert (Hardlink). Alle anderen Commits bekommen in
               einer Kopie gitCommit = 88cc95d8 (der Materialisierer verlangt Record-Commit ==
               Baum). Ob die Simulation passt, entscheidet je Partie die Hash-Prüfung
               (desync/tick_error in der .ok) — nur bestandene Partien werden verwendet.
  zuordnen     trackerfront nennt je Partie seinen damaligen username (public_uuid 2f4e9fe5).
               Genommen wird nur, wenn im Record GENAU ein Spieler exakt diesen Namen trägt
               (und trackerfront den Namen nur einmal führt). Gegenprobe über den Sieger.
               Ausgabe spieler_cid.tsv: gid, clientID, name, rang, probe, orig-Commit.
"""
import json, glob, os, sys, collections

UUID = "2f4e9fe5"
BAUM_A = "88cc95d8b6d74d951546da341be809bfb3cab960"
EIGENE = {"88cc95d8", "8b45be57"}
HOSTS = [("node-1", 1.23), ("node-2", 1.23), ("apollo", 1.53)]   # 3 Kerne x Tempo je Kern


def records():
    for f in sorted(glob.glob("records/*/*.json")):
        yield os.path.basename(f)[:-5], f


def vorbereiten():
    zeilen, grund = [], collections.Counter()
    for gid, f in records():
        txt = open(f).read()
        r = json.loads(txt)
        c8 = r.get("gitCommit", "")[:8]
        if r["info"]["config"].get("gameMode") != "Free For All":
            grund["kein_ffa"] += 1; continue
        ziel = os.path.join("rec", gid[:2], gid + ".json")
        os.makedirs(os.path.dirname(ziel), exist_ok=True)
        if c8 in EIGENE:
            if not os.path.exists(ziel):
                os.link(f, ziel)
        else:
            alt = f'"gitCommit":"{r["gitCommit"]}"'
            if alt not in txt[:2000]:
                grund["commit_feld_unerwartet"] += 1; continue
            with open(ziel + ".tmp", "w") as o:
                o.write(txt.replace(alt, f'"gitCommit":"{BAUM_A}"', 1))
            os.replace(ziel + ".tmp", ziel)
        zeilen.append(dict(gid=gid, orig=c8, turns=len(r.get("turns", []))))
        grund["ok"] += 1
    # grösste Partien zuerst, jeweils an den Host mit der kleinsten Last/Tempo
    last = {h: 0.0 for h, _ in HOSTS}
    tempo = dict(HOSTS)
    for z in sorted(zeilen, key=lambda z: -z["turns"]):
        h = min(last, key=lambda h: (last[h] + z["turns"]) / tempo[h])
        z["host"] = h
        last[h] += z["turns"]
    with open("verteilung.tsv", "w") as o:
        o.write("gid\torig\tturns\thost\n")
        for z in sorted(zeilen, key=lambda z: z["gid"]):
            o.write(f"{z['gid']}\t{z['orig']}\t{z['turns']}\t{z['host']}\n")
    for h, _ in HOSTS:
        with open(f"liste_{h}.txt", "w") as o:
            o.write("".join(f"{z['gid'][:2]}/{z['gid']}.json\n" for z in zeilen if z["host"] == h))
    print(dict(grund))
    print("je Commit:", collections.Counter(z["orig"] for z in zeilen).most_common())
    print("Partien je Host:", collections.Counter(z["host"] for z in zeilen))
    print("Last (Züge/Tempo):", {h: round(last[h] / tempo[h]) for h in last})


def zuordnen():
    tf = {}
    for z in open("ur_tf_spiele.jsonl"):
        d = json.loads(z)
        tf[d["game_id"]] = d
    grund, out = collections.Counter(), []
    for gid, f in records():
        t = tf.get(gid)
        if t is None:
            grund["kein_trackerfront"] += 1; continue
        ich = [p for p in t.get("players", []) if p.get("public_uuid") == UUID]
        if len(ich) != 1:
            grund["uuid_nicht_eindeutig"] += 1; continue
        name, rang = ich[0]["username"], ich[0]["game_rank"]
        if collections.Counter(p.get("username") for p in t["players"])[name] != 1:
            grund["name_bei_trackerfront_doppelt"] += 1; continue
        r = json.load(open(f))
        treffer = [p for p in r["info"]["players"] if p.get("username") == name]
        if len(treffer) != 1:
            grund[f"name_im_record_{len(treffer)}x"] += 1; continue
        cid = treffer[0]["clientID"]
        w = r["info"].get("winner")
        sieger = w[1] if isinstance(w, list) and len(w) == 2 and w[0] == "player" else None
        probe = "kein_sieger"
        if sieger is not None:
            if (rang == 1) != (sieger == cid):
                grund["sieger_widerspruch"] += 1; continue
            probe = "sieg_passt" if rang == 1 else "fremdsieg_passt"
        out.append((gid, cid, name, rang, probe, r.get("gitCommit", "")[:8]))
        grund["ok"] += 1
    with open("spieler_cid.tsv", "w") as o:
        o.write("gid\tclientID\tname\trang\tprobe\torig\n")
        o.write("".join("\t".join(map(str, z)) + "\n" for z in out))
    print(dict(grund))
    print("Gegenprobe:", collections.Counter(z[4] for z in out))
    print("Namen (häufigste):", collections.Counter(z[2] for z in out).most_common(6))


if __name__ == "__main__":
    {"vorbereiten": vorbereiten, "zuordnen": zuordnen}[sys.argv[1]]()

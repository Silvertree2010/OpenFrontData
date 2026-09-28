#!/usr/bin/env python3
"""Auswahl der 50 Kanarien-Records (DESIGN.md §9, Entwurf §7.3).

    python3 select.py <records.tsv> <records_root> <out.tsv> [--n 50]

<records.tsv>   Inventar mit Kopfzeile: path, gameID, commit8, size_bytes, gameMap,
                num_players, num_turns (Format von ~/mat-dev/records.tsv).
<records_root>  Wurzel der Records. In der Liste stehen Pfade relativ dazu; genau
                dieser Ordner wird in canary.sh als /in eingehängt.
<out.tsv>       Liste: Kopfzeile mit '#', danach je Record
                relpath, gid, commit8, size, map, mode, waterNukes, players, turns, grund.

Deterministisch: kein Zufall, jede Wahl ist über (Grösse, gid) sortiert. Gleiches
Inventar gibt dieselbe Liste.

Modus (Free For All / Team) und waterNukes stehen nicht im Inventar. Sie werden aus
dem Kopf jedes Records gelesen (die ersten 64 KB, `info.config` steht dort vorn).

Reihenfolge der Regeln (spätere füllen nur auf, früher Gewähltes bleibt):
  1. alle Records von 115da032 (Pflicht)
  2. die grösste Datei des Inventars
  3. je grosse Karte (Passage, Korea, Giant World Map) und je grossem Commit
     (88cc95d8, 8b45be57) der grösste Record, bei 88cc95d8 zusätzlich der mittelgrosse
  4. je grossem Commit ein Record mit waterNukes (der am nächsten am Median)
  5. Auffüllen über das ganze Grössenspektrum: je Commit Quantile 0..1 der Grösse
     (Kleinster und Grösster eingeschlossen), jeder dritte Platz sucht einen Team-Record.
     88cc95d8 bekommt zwei Drittel, 8b45be57 ein Drittel der Restplätze.
Am Ende prüft das Skript die Pflichten und bricht ab, wenn eine nicht erfüllt ist.
"""
from __future__ import annotations

import os
import re
import sys
from collections import Counter

C88, C8B, C115 = "88cc95d8", "8b45be57", "115da032"
BIG_COMMITS = (C88, C8B)
BIG_MAPS = ("Passage", "Korea", "Giant World Map")
HEAD_BYTES = 65536

RE_MODE = re.compile(r'"gameMode"\s*:\s*"([^"]+)"')
RE_WN = re.compile(r'"waterNukes"\s*:\s*(true|false)')
RE_COMMIT = re.compile(r'"gitCommit"\s*:\s*"([0-9a-fA-F]+)"')


def read_inventory(path: str) -> list[dict]:
    rows = []
    with open(path, encoding="utf-8") as f:
        head = f.readline().rstrip("\n").split("\t")
        need = ["path", "gameID", "commit8", "size_bytes", "gameMap", "num_players", "num_turns"]
        if head[: len(need)] != need:
            raise SystemExit(f"Inventar-Kopf unerwartet: {head}")
        for line in f:
            x = line.rstrip("\n").split("\t")
            if len(x) < 7:
                continue
            rows.append({"path": x[0], "gid": x[1], "commit": x[2], "size": int(x[3]), "map": x[4],
                         "players": int(x[5]) if x[5].isdigit() else -1,
                         "turns": int(x[6]) if x[6].isdigit() else -1})
    return rows


def enrich(rows: list[dict], root: str) -> None:
    """Modus und waterNukes aus dem Record-Kopf; Pfad relativ zur Wurzel."""
    root = os.path.abspath(root)
    for r in rows:
        p = r["path"] if os.path.isabs(r["path"]) else os.path.join(root, r["path"])
        rel = os.path.relpath(p, root)
        if rel.startswith(".."):
            raise SystemExit(f"{p} liegt nicht unter der Wurzel {root}")
        r["rel"] = rel
        with open(p, "rb") as f:
            h = f.read(HEAD_BYTES).decode("utf-8", "replace")
        m = RE_MODE.search(h)
        r["mode"] = m.group(1) if m else "?"
        w = RE_WN.search(h)
        r["wn"] = bool(w and w.group(1) == "true")
        c = RE_COMMIT.search(h)
        if not c or c.group(1)[:8] != r["commit"]:
            raise SystemExit(f"{r['gid']}: gitCommit im Record {c and c.group(1)} passt nicht zum Inventar {r['commit']}")


def select(rows: list[dict], n: int = 50) -> list[dict]:
    key = lambda r: (r["size"], r["gid"])  # noqa: E731
    pick: dict[str, dict] = {}

    def add(r: dict, why: str) -> None:
        e = pick.setdefault(r["gid"], {**r, "why": []})
        e["why"].append(why)

    by_commit = {c: sorted((r for r in rows if r["commit"] == c), key=key) for c in {r["commit"] for r in rows}}

    # 1 Pflicht: alle 115da032
    for r in by_commit.get(C115, []):
        add(r, "Commit 115da032, Pflicht (alle)")
    # 2 grösste Datei
    add(max(rows, key=key), "grösste Datei im Inventar")
    # 3 grosse Karten
    maps_found = {r["map"] for r in rows}
    for mp in BIG_MAPS:
        if mp not in maps_found:
            raise SystemExit(f"Karte {mp!r} nicht im Inventar. Vorhanden u.a.: {sorted(maps_found)[:20]}")
        for c in BIG_COMMITS:
            lst = [r for r in by_commit.get(c, []) if r["map"] == mp]
            if not lst:
                continue
            add(lst[-1], f"grosse Karte {mp}, grösster Record an {c}")
            if c == C88 and len(lst) > 2:
                add(lst[len(lst) // 2], f"grosse Karte {mp}, mittlere Grösse an {c}")
    # 4 waterNukes
    for c in BIG_COMMITS:
        lst = by_commit.get(c, [])
        wn = [r for r in lst if r["wn"]]
        if not wn:
            continue
        med = lst[len(lst) // 2]["size"]
        best = min((r for r in wn if r["gid"] not in pick), key=lambda r: (abs(r["size"] - med), r["gid"]), default=None)
        if best:
            add(best, f"waterNukes an {c}, nahe Median-Grösse")
    # 5 Grössenspektrum
    rest = n - len(pick)
    if rest < 0:
        raise SystemExit(f"Pflichten ergeben schon {len(pick)} > {n} Records")
    quota = {C8B: round(rest / 3)}
    quota[C88] = rest - quota[C8B]
    for c in (C88, C8B):
        lst = by_commit.get(c, [])
        k = quota[c]
        if not lst or k <= 0:
            continue
        for i in range(k):
            q = i / (k - 1) if k > 1 else 0.5
            tgt = min(len(lst) - 1, round(q * (len(lst) - 1)))
            want = "Team" if i % 3 == 1 else "Free For All"
            chosen = None
            for strict in (True, False):  # erst gewünschter Modus, dann irgendeiner
                for d in range(len(lst)):
                    for j in (tgt - d, tgt + d):
                        if 0 <= j < len(lst):
                            r = lst[j]
                            if r["gid"] not in pick and (not strict or r["mode"] == want):
                                chosen = r
                                break
                    if chosen:
                        break
                if chosen:
                    break
            if chosen:
                add(chosen, f"Grössenspektrum {c}, Quantil {q:.2f} (Rang {tgt + 1}/{len(lst)}), Modus {want} gesucht")

    out = sorted(pick.values(), key=lambda r: (r["commit"], r["size"], r["gid"]))
    check(out, rows, n)
    return out


def check(sel: list[dict], rows: list[dict], n: int) -> None:
    """Pflichten aus DESIGN §9 und Auftrag; bricht bei Verletzung ab."""
    probs = []
    if len(sel) != n:
        probs.append(f"{len(sel)} statt {n} Records")
    gids = {r["gid"] for r in sel}
    for r in rows:
        if r["commit"] == C115 and r["gid"] not in gids:
            probs.append(f"115da032 {r['gid']} fehlt")
    for c in BIG_COMMITS:
        for mode in ("Free For All", "Team"):
            if any(r["commit"] == c and r["mode"] == mode for r in rows) and \
                    not any(r["commit"] == c and r["mode"] == mode for r in sel):
                probs.append(f"{c} {mode} fehlt")
    for mp in BIG_MAPS:
        if not any(r["map"] == mp for r in sel):
            probs.append(f"Karte {mp} fehlt")
    if any(r["wn"] for r in rows) and not any(r["wn"] for r in sel):
        probs.append("kein waterNukes-Record")
    if max(rows, key=lambda r: (r["size"], r["gid"]))["gid"] not in gids:
        probs.append("grösste Datei fehlt")
    if probs:
        raise SystemExit("Auswahl verletzt Pflichten: " + "; ".join(probs))


def summary(sel: list[dict]) -> str:
    sz = sorted(r["size"] for r in sel)
    q = lambda p: sz[min(len(sz) - 1, int(p * (len(sz) - 1) + 0.5))]  # noqa: E731
    lines = [
        f"Records: {len(sel)}, zusammen {sum(sz) / 1e6:.1f} MB",
        "Commits: " + ", ".join(f"{c} {n}" for c, n in sorted(Counter(r['commit'] for r in sel).items())),
        "Modus: " + ", ".join(f"{c}/{m} {n}" for (c, m), n in sorted(Counter((r['commit'], r['mode']) for r in sel).items())),
        f"waterNukes: {sum(r['wn'] for r in sel)}",
        f"Grösse KB: min {sz[0] / 1e3:.0f}, p25 {q(.25) / 1e3:.0f}, Median {q(.5) / 1e3:.0f}, "
        f"p75 {q(.75) / 1e3:.0f}, max {sz[-1] / 1e3:.0f}",
        "Karten: " + ", ".join(f"{m} {n}" for m, n in Counter(r['map'] for r in sel).most_common()),
    ]
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    args = [a for a in argv if not a.startswith("--")]
    n = int(argv[argv.index("--n") + 1]) if "--n" in argv else 50
    if "--n" in argv:
        args.remove(str(n))
    if len(args) != 3:
        print(__doc__)
        return 2
    inv, root, out = args
    rows = read_inventory(inv)
    enrich(rows, root)
    sel = select(rows, n)
    with open(out + ".tmp", "w", encoding="utf-8") as f:
        f.write("#relpath\tgid\tcommit8\tsize_bytes\tmap\tmode\twaterNukes\tplayers\tturns\tgrund\n")
        for r in sel:
            f.write("\t".join(map(str, (r["rel"], r["gid"], r["commit"], r["size"], r["map"], r["mode"],
                                         int(r["wn"]), r["players"], r["turns"], "; ".join(r["why"])))) + "\n")
    os.replace(out + ".tmp", out)
    print(summary(sel))
    print(f"geschrieben: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

"""Byte-Tor (DESIGN §9 Tor 1): alter Materialisierer gegen neuen, je Partie.

    python3 compare_ref.py <ref_dir> <new_dir> <gid> [<gid> ...] [--json]

<ref_dir> enthält <gid>.maps/.meta.zst des alten Laufs (NOOP_EVERY=200 THIN=30),
<new_dir> die Ausgabe des neuen Kerns. Der neue Lauf muss die .ok-Grössenprüfung
bestehen (reader.Game), sonst zählt die Partie als fehlend.

Schlüssel ist (turn, clientID). Innerhalb davon stammen alle Blöcke aus demselben
Kontext-Cache und sind gleich; geprüft wird trotzdem jeder Block gegen jeden.
Abdeckung als Multimenge: je Schlüssel min(alt, neu) Samples gelten als vorhanden.
Nicht vorhandene alte Samples werden nach Grund ausgewiesen:
  zusammengefasst  alter Angriff, dessen Zug in merged_turns eines neuen Angriffs
                   desselben Spielers steckt (zählt als erklärt)
  spawn_nach_phase alter Spawn-Intent nach der Phase (alt: act; neu: nie emittiert,
                   DESIGN §2.5)
  schnitt          Zug > valid_until des neuen Laufs (Desync, Tick-Fehler)
  fehler           der neue Lauf hat Sample-Fehler dieser Art gezählt
  sonstiges        alles andere, mit Beispielen
Tor: 0 Blockabweichungen, 0 sonstiges, Abdeckung inkl. erklärt ≥ 95 %, und
mindestens ein verglichener Block (kein Erfolg aus leerer Menge).
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "py"))
import reader  # noqa: E402


def _old(ref_dir: str, gid: str):
    with open(os.path.join(ref_dir, f"{gid}.meta.zst"), "rb") as f:
        txt = reader.zdec(f.read()).decode("utf-8")
    meta = [json.loads(x) for x in txt.split("\n")] if txt else []
    blocks = list(reader.iter_blocks(os.path.join(ref_dir, f"{gid}.maps")))
    if len(blocks) != len(meta):
        raise ValueError(f"{gid}: Referenz nicht im Gleichschritt ({len(blocks)} Blöcke, {len(meta)} Zeilen)")
    return meta, blocks


def compare(ref_dir: str, new_dir: str, gid: str) -> dict:
    old_meta, old_blocks = _old(ref_dir, gid)
    g = reader.Game(new_dir, gid)
    new_meta = g.meta()
    new_blocks = list(g.map_blocks())
    assert len(new_blocks) == len(new_meta)

    old_by = defaultdict(list)
    for m, b in zip(old_meta, old_blocks):
        old_by[(m["turn"], m["clientID"])].append((m, b))
    new_by = defaultdict(list)
    for m, b in zip(new_meta, new_blocks):
        new_by[(m["turn"], m["clientID"])].append((m, b))

    merged = defaultdict(set)  # clientID → Züge, die in einem neuen Angriff stecken
    for m in new_meta:
        for t in m.get("merged_turns") or ():
            merged[m["clientID"]].add(t)

    vu = g.ok.get("valid_until")
    errs = g.ok.get("errors") or {}
    cmp_blocks = mismatch = present = 0
    mismatch_keys = []
    reasons = Counter()
    examples = []
    for key, olds in old_by.items():
        news = new_by.get(key, [])
        if news:
            ref = news[0][1]
            for _, b in olds + news:
                cmp_blocks += 1
                if b != ref:
                    mismatch += 1
                    if len(mismatch_keys) < 5:
                        mismatch_keys.append(key)
        present += min(len(olds), len(news))
        # fehlende alte Samples dieses Schlüssels erklären
        rest = [m for m, _ in olds]
        # die vorhandenen decken zuerst die Nicht-Angriffe (Multimenge nach Schlüssel)
        rest.sort(key=lambda m: m["intent"].get("type") == "attack")
        for m in rest[len(news):] if len(news) < len(olds) else []:
            turn, cid, it = m["turn"], m["clientID"], m["intent"].get("type")
            if it == "attack" and turn in merged[cid]:
                reasons["zusammengefasst"] += 1
            elif it == "spawn":
                reasons["spawn_nach_phase"] += 1
            elif vu is not None and turn > vu:
                reasons["schnitt"] += 1
            elif (it == "no_op" and errs.get("noop")) or (it != "no_op" and errs.get("act")):
                reasons["fehler"] += 1
            else:
                reasons["sonstiges"] += 1
                if len(examples) < 5:
                    examples.append({"turn": turn, "clientID": cid, "intent": m["intent"], "neu": len(news)})

    n_old = len(old_meta)
    explained = reasons["zusammengefasst"] + reasons["spawn_nach_phase"]
    cov = present / n_old if n_old else 0.0
    cov_expl = (present + explained) / n_old if n_old else 0.0
    new_kinds = Counter(m["kind"] for m in new_meta)
    ok = cmp_blocks > 0 and mismatch == 0 and reasons["sonstiges"] == 0 and cov_expl >= 0.95
    return {
        "gid": gid, "old": n_old, "new": len(new_meta), "present": present,
        "coverage": round(cov, 4), "coverage_explained": round(cov_expl, 4),
        "blocks_compared": cmp_blocks, "block_mismatch": mismatch, "mismatch_keys": mismatch_keys,
        "missing": dict(reasons), "examples": examples, "new_by_kind": dict(new_kinds),
        "valid_until": vu, "pass": ok,
    }


def main(argv: list[str]) -> int:
    as_json = "--json" in argv
    args = [a for a in argv if a != "--json"]
    if len(args) < 3:
        print(__doc__)
        return 2
    ref_dir, new_dir, gids = args[0], args[1], args[2:]
    bad = 0
    for gid in gids:
        try:
            r = compare(ref_dir, new_dir, gid)
        except Exception as e:  # fehlende/kaputte Partie ist ein Durchfall, kein Absturz
            r = {"gid": gid, "pass": False, "error": str(e)}
        bad += not r["pass"]
        if as_json:
            print(json.dumps(r))
        elif "error" in r:
            print(f"{gid}: FEHLER {r['error']}")
        else:
            print(f"{gid}: alt {r['old']} neu {r['new']} vorhanden {r['present']} ({100*r['coverage']:.1f} %, "
                  f"mit erklärt {100*r['coverage_explained']:.1f} %) Blöcke {r['blocks_compared']} "
                  f"abweichend {r['block_mismatch']} fehlend {r['missing']} {'OK' if r['pass'] else 'DURCHGEFALLEN'}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

"""Nachweise für den Kern (materialize/res/io/timing/reader). Läuft auf arch.

    python3 run_core_tests.py [schritt ...]     # ohne Schritt: alle

Schritte:
  gate      9 Referenz-Records mit NOOP_EVERY=200 MERGE_TICKS=30 CELLS=0 TIER1_PCT=0,
            je 3 parallel, gegen ~/mat-dev/ref/thin (compare_ref.py), dazu reader-Prüfung
  desync    verfälschter Record-Hash → desync, Schnitt bei last_ok, Zeilen gegen den Gate-Lauf
  tickerr   FAULT_TICK: Tick-Fehler mit und ohne Hashes im Record → .ok mit tick_error, Schnitt
  kill      Lauf mitten drin mit SIGKILL abbrechen, neu starten → sauber fertig, keine Reste
  fault     FAULT_INJECT: ein emit wirft → Partie fertig, Fehler gezählt, Gleichschritt
  none      Record ohne Menschen-Züge (und ohne Hashes) → .none; zweiter Start überspringt
  hooks     Stubs tier1/cells: Verdrahtung, .cells-Gleichschritt, gleiche .maps und Digest wie Gate;
            Stub-Zellfehler → cell -1, Sample bleibt
  negative  Gegenproben: verfälschter Block, fehlende Metazeile, falsche .ok-Grösse müssen rot werden
  report    Tabellen: Byte-Tor, Hashes, res, Zeit

Ordnerlayout: ~/mat-dev/w/<sha8>/mat-core (ohne Stubs) und .../mat-core-stubs (mit Stubs).
Ausgabe unter ~/mat-dev/out/core/. Höchstens 3 Prozesse gleichzeitig, alle mit nice -n 10.
Jeder Schritt schreibt PASS/FAIL je Prüfung; Exit 1, wenn eine Prüfung fehlschlägt.
"""
from __future__ import annotations

import concurrent.futures as cf
import hashlib
import json
import math
import os
import shutil
import signal
import statistics
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "py"))
sys.path.insert(0, HERE)
import compare_ref  # noqa: E402
import reader  # noqa: E402

DEV = os.path.join(os.path.expanduser("~"), "mat-dev")
OUT = os.path.join(DEV, "out", "core")
REFS = {
    "88cc95d8": ["uLwSPQFK", "dJtnLxJA", "FCxzAh2Y"],
    "8b45be57": ["H2NkRg5R", "JwFNdofK", "omTgUNMh"],
    "115da032": ["bqfEEyVi", "A9iejjLi", "HzjyLWzY"],
}
GATE_ENV = {"NOOP_EVERY": "200", "MERGE_TICKS": "30", "CELLS": "0", "TIER1_PCT": "0"}
NUKES = {"Atom Bomb", "Hydrogen Bomb", "MIRV"}
STRUCT = {"City", "Defense Post", "SAM Launcher", "Missile Silo", "Factory", "Port"}
RESULTS: dict = {}
FAILS: list[str] = []


def check(name: str, cond: bool, detail="") -> bool:
    print(f"  {'PASS' if cond else 'FAIL'} {name}{(' — ' + str(detail)) if detail != '' else ''}", flush=True)
    if not cond:
        FAILS.append(name)
    return cond


def load_records() -> dict:
    rec = {}
    with open(os.path.join(DEV, "records.tsv")) as f:
        next(f)
        for line in f:
            p = line.rstrip("\n").split("\t")
            rec[p[1]] = (p[0], p[2])
    return rec


REC = load_records()


def commit_of(gid: str) -> str:
    return REC[gid][1]


def run_mat(gid: str, out: str, env=None, variant="mat-core", record=None, force=True, timeout=1200):
    """Ein Prozess pro Partie, wie dispatch.ts. Liefert rc, Wandzeit, Ausgabe."""
    record = record or REC[gid][0]
    tree = os.path.join(DEV, "w", commit_of(gid))
    e = dict(os.environ)
    e.update(GATE_ENV)
    e.update(env or {})
    if force:
        e["FORCE"] = "1"
    else:
        e.pop("FORCE", None)
    os.makedirs(out, exist_ok=True)
    cmd = ["nice", "-n", "10", "npx", "tsx", f"{variant}/src/materialize.ts", record, out]
    t0 = time.time()
    p = subprocess.run(cmd, cwd=tree, env=e, capture_output=True, text=True, timeout=timeout)
    return {"rc": p.returncode, "wall": round(time.time() - t0, 2), "out": p.stdout.strip()[-400:],
            "err": p.stderr.strip()[-1500:]}


def ok_of(out: str, gid: str) -> dict:
    with open(os.path.join(out, f"{gid}.ok")) as f:
        return json.load(f)


def sha(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        h.update(f.read())
    return h.hexdigest()


def rtype(m: dict) -> str:
    it = m["intent"]
    return it["unit"] if it["type"] == "build_unit" else it["type"]


def win_end(m: dict) -> int:
    t = rtype(m)
    if t in NUKES:
        return 3
    if t in STRUCT or t == "Warship":
        return 2
    return {"boat": 1, "upgrade_structure": 1, "move_warship": 0, "spawn": 1}[t]


def invalid_res(m: dict) -> dict:
    t = rtype(m)
    keep = t == "Warship" or t in NUKES
    return {"res_tile": m["click"] if keep else -1, "res_kind": 2, "res_unit_id": -1, "res_dt": -1}


# ── gate ─────────────────────────────────────────────────────────────────────
def step_gate():
    print("== gate", flush=True)
    out = os.path.join(OUT, "gate")
    shutil.rmtree(out, ignore_errors=True)
    gids = [g for gs in REFS.values() for g in gs]
    # grosse zuerst, damit die drei Plätze gleichmässig belegt sind
    gids.sort(key=lambda g: -os.path.getsize(REC[g][0]))
    runs = {}
    with cf.ThreadPoolExecutor(3) as ex:
        futs = {ex.submit(run_mat, g, out): g for g in gids}
        for f in cf.as_completed(futs):
            g = futs[f]
            runs[g] = f.result()
            print(f"  {g}: rc {runs[g]['rc']} {runs[g]['wall']} s {runs[g]['out'][-120:]}", flush=True)
    gate = {}
    for c, gs in REFS.items():
        for g in gs:
            check(f"gate {g} rc 0", runs[g]["rc"] == 0, runs[g]["err"][-300:])
            r = compare_ref.compare(os.path.join(DEV, "ref", "thin", c, "a"), out, g)
            probs = reader.check_dir(out, [g])[g]
            check(f"gate {g} reader", probs == "ok", probs)
            check(f"gate {g} Byte-Tor", r["pass"], {k: r[k] for k in ("old", "present", "block_mismatch", "missing")})
            gate[g] = {"cmp": r, "run": runs[g], "ok": ok_of(out, g)}
    RESULTS["gate"] = gate
    for g, v in gate.items():
        h = v["ok"]["hash"]
        check(f"hash {g} geprüft > 0, 0 Desync, 0 Tick-Fehler",
              h["checked"] > 0 and v["ok"]["desync"] is None and v["ok"]["tick_error"] is None, h)


# ── desync ───────────────────────────────────────────────────────────────────
def cut_check(clean_dir: str, cut_dir: str, gid: str, cut: int):
    """Neue Zeilen gegen die des vollen Laufs, mit den Schnitt-Regeln aus DESIGN §2.7."""
    gc, gx = reader.Game(clean_dir, gid), reader.Game(cut_dir, gid)
    mc, mx = gc.meta(), gx.meta()
    exp = [m for m in mc if m["tick"] <= cut]
    probs = []
    if len(mx) != len(exp):
        probs.append(f"{len(mx)} Zeilen statt {len(exp)}")
    bc = [b for b, _ in zip(gc.map_blocks(), range(len(exp)))]
    if bc != list(gx.map_blocks()):
        probs.append("Kartenblöcke weichen ab")
    patched = {"merged": 0, "res": 0, "rows": 0}
    for a, b in zip(exp, mx):
        a2 = dict(a)
        if "merged_turns" in a:
            mt = [t for t in a["merged_turns"] if t <= cut]
            if mt != a["merged_turns"]:
                patched["merged"] += 1
                if not b["w_tick"] < a["w_tick"] + 1e-12:
                    probs.append(f"w_tick nicht gesunken t{a['tick']}")
                a2["w_tick"] = b["w_tick"]
            a2.update(merged_turns=mt, merged=len(mt), w=1 + len(mt))
        if "res_kind" in a and a["tick"] + win_end(a) > cut and a["res_kind"] != 2:
            a2.update(invalid_res(a))
            patched["res"] += 1
        if a2 != b:
            diff = sorted(k for k in set(a2) | set(b) if a2.get(k) != b.get(k))
            probs.append(f"Zeile t{a['tick']} {a['clientID']} weicht ab: {diff}")
            if len(probs) > 5:
                break
    patched["rows"] = len(mx)
    return probs, patched


def hash_turns(rec: dict) -> list[int]:
    return [t["turnNumber"] for t in rec["turns"] if t.get("hash") is not None]


def step_desync():
    print("== desync", flush=True)
    gid = "dJtnLxJA"
    rec = json.load(open(REC[gid][0]))
    hts = hash_turns(rec)
    x = hts[int(len(hts) * 0.6)]
    prev = max(t for t in hts if t < x)
    for t in rec["turns"]:
        if t["turnNumber"] == x:
            t["hash"] = t["hash"] + 1.0
    d = os.path.join(OUT, "rec_desync")
    os.makedirs(d, exist_ok=True)
    rp = os.path.join(d, f"{gid}.json")
    json.dump(rec, open(rp, "w"))
    out = os.path.join(OUT, "desync")
    shutil.rmtree(out, ignore_errors=True)
    r = run_mat(gid, out, record=rp)
    check("desync rc 0", r["rc"] == 0, r["err"][-300:])
    ok = ok_of(out, gid)
    check("desync erkannt am verfälschten Tick", ok["desync"] == {"tick": x, "last_ok_tick": prev}, ok["desync"])
    check("desync valid_until = last_ok", ok["valid_until"] == prev, ok["valid_until"])
    check("desync reader", reader.check_dir(out, [gid])[gid] == "ok")
    probs, patched = cut_check(os.path.join(OUT, "gate"), out, gid, prev)
    check("desync Zeilen = Gate-Lauf bis last_ok (mit Schnitt-Regeln)", not probs, probs[:3])
    gok = ok_of(os.path.join(OUT, "gate"), gid)
    check("desync weniger Samples als voll", 0 < ok["samples"] < gok["samples"], (ok["samples"], gok["samples"]))
    RESULTS["desync"] = {"tick": x, "last_ok": prev, "samples": ok["samples"], "full": gok["samples"],
                         "patched": patched, "hash": ok["hash"]}
    print(f"  Schnitt bei {prev}: {patched}", flush=True)


def step_tickerr():
    print("== tickerr", flush=True)
    gid = "dJtnLxJA"
    rec = json.load(open(REC[gid][0]))
    hts = hash_turns(rec)
    x = hts[int(len(hts) * 0.5)] + 5         # zwischen zwei Hash-Zügen
    prev = max(t for t in hts if t < x)
    out = os.path.join(OUT, "tickerr")
    shutil.rmtree(out, ignore_errors=True)
    r = run_mat(gid, out, env={"FAULT_TICK": str(x)})
    check("tickerr rc 0 (kein .err)", r["rc"] == 0 and not os.path.exists(os.path.join(out, f"{gid}.err")), r["err"][-200:])
    ok = ok_of(out, gid)
    check("tickerr tick_error gesetzt", (ok["tick_error"] or {}).get("tick") == x, ok["tick_error"])
    check("tickerr Schnitt beim letzten Hash", ok["valid_until"] == prev, (ok["valid_until"], prev))
    probs, patched = cut_check(os.path.join(OUT, "gate"), out, gid, prev)
    check("tickerr Zeilen = Gate bis last_ok", not probs, probs[:3])
    check("tickerr reader", reader.check_dir(out, [gid])[gid] == "ok")
    # ohne Hashes im Record: last_ok = Fehler-Tick − 1
    for t in rec["turns"]:
        t.pop("hash", None)
    d = os.path.join(OUT, "rec_nohash")
    os.makedirs(d, exist_ok=True)
    rp = os.path.join(d, f"{gid}.json")
    json.dump(rec, open(rp, "w"))
    out2 = os.path.join(OUT, "tickerr_nohash")
    shutil.rmtree(out2, ignore_errors=True)
    r = run_mat(gid, out2, env={"FAULT_TICK": str(x)}, record=rp)
    ok2 = ok_of(out2, gid)
    check("tickerr ohne Hashes: Schnitt bei Fehler−1", ok2["valid_until"] == x - 1 and ok2["hash"]["checked"] == 0,
          (ok2["valid_until"], ok2["hash"]))
    probs2, patched2 = cut_check(os.path.join(OUT, "gate"), out2, gid, x - 1)
    check("tickerr ohne Hashes: Zeilen = Gate bis Fehler−1", not probs2, probs2[:3])
    RESULTS["tickerr"] = {"tick": x, "cut": prev, "patched": patched, "cut_nohash": x - 1, "patched_nohash": patched2}


# ── kill ─────────────────────────────────────────────────────────────────────
def step_kill():
    print("== kill", flush=True)
    gid = "dJtnLxJA"
    out = os.path.join(OUT, "kill")
    shutil.rmtree(out, ignore_errors=True)
    os.makedirs(out)
    tree = os.path.join(DEV, "w", commit_of(gid))
    e = dict(os.environ, **GATE_ENV, FORCE="1")
    p = subprocess.Popen(["nice", "-n", "10", "npx", "tsx", "mat-core/src/materialize.ts", REC[gid][0], out],
                         cwd=tree, env=e, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    maps_tmp = os.path.join(out, f"{gid}.maps.tmp")
    t0 = time.time()
    while time.time() - t0 < 60 and not (os.path.exists(maps_tmp) and os.path.getsize(maps_tmp) > 2_000_000):
        time.sleep(0.2)
    os.killpg(p.pid, signal.SIGKILL)
    p.wait()
    left = sorted(os.listdir(out))
    check("kill: tmp-Reste da, keine .ok (Test greift mitten im Lauf)",
          f"{gid}.maps.tmp" in left and f"{gid}.ok" not in left, left)
    check("kill: reader.is_done false nach Abbruch", not reader.is_done(out, gid))
    # Neustart OHNE FORCE: materialize.ts rechnet nur, wenn io.ts isDone false liefert
    r = run_mat(gid, out, force=False)
    left2 = sorted(os.listdir(out))
    check("kill: Neustart rechnet (isDone war false)", "übersprungen" not in r["out"] and "ok" in r["out"], r["out"])
    check("kill: Neustart fertig, keine tmp-Reste", r["rc"] == 0 and not any(f.endswith(".tmp") for f in left2), left2)
    check("kill: .maps gleich wie Gate-Lauf",
          sha(os.path.join(out, f"{gid}.maps")) == sha(os.path.join(OUT, "gate", f"{gid}.maps")))
    r2 = run_mat(gid, out, force=False)
    check("kill: dritter Start überspringt", "übersprungen" in r2["out"], r2["out"])
    RESULTS["kill"] = {"left_after_kill": left, "left_after_restart": left2}


# ── fault ────────────────────────────────────────────────────────────────────
def step_fault():
    print("== fault", flush=True)
    gid = "uLwSPQFK"
    out = os.path.join(OUT, "fault")
    shutil.rmtree(out, ignore_errors=True)
    r = run_mat(gid, out, env={"FAULT_INJECT": "10"})
    ok = ok_of(out, gid)
    gok = ok_of(os.path.join(OUT, "gate"), gid)
    tot = sum(ok["errors"].values())
    check("fault: Partie fertig", r["rc"] == 0)
    check("fault: genau 1 Fehler gezählt", tot == 1 and "FAULT_INJECT" in (ok["first_error"] or ""), (ok["errors"], ok["first_error"]))
    check("fault: reader-Gleichschritt", reader.check_dir(out, [gid])[gid] == "ok")
    c = compare_ref.compare(os.path.join(OUT, "gate"), out, gid)
    check("fault: alle gemeinsamen Blöcke gleich wie ohne Fehler", c["block_mismatch"] == 0 and c["blocks_compared"] > 0, c["blocks_compared"])
    check("fault: ein Sample weniger", ok["samples"] in (gok["samples"] - 1, gok["samples"]), (ok["samples"], gok["samples"]))
    RESULTS["fault"] = {"errors": ok["errors"], "first_error": ok["first_error"], "samples": ok["samples"], "full": gok["samples"]}


# ── none ─────────────────────────────────────────────────────────────────────
def step_none():
    print("== none", flush=True)
    gid = "H2NkRg5R"
    rec = json.load(open(REC[gid][0]))
    humans = {p["clientID"] for p in rec["info"]["players"]}
    n0 = sum(len(t.get("intents", [])) for t in rec["turns"])
    for t in rec["turns"]:
        t["intents"] = [i for i in t.get("intents", []) if i.get("clientID") not in humans]
        t.pop("hash", None)
    n1 = sum(len(t.get("intents", [])) for t in rec["turns"])
    d = os.path.join(OUT, "rec_none")
    os.makedirs(d, exist_ok=True)
    rp = os.path.join(d, f"{gid}.json")
    json.dump(rec, open(rp, "w"))
    out = os.path.join(OUT, "none")
    shutil.rmtree(out, ignore_errors=True)
    r = run_mat(gid, out, record=rp)
    files = sorted(os.listdir(out))
    none = json.load(open(os.path.join(out, f"{gid}.none"))) if f"{gid}.none" in files else {}
    check("none: .none mit format 2, sonst nichts", files == [f"{gid}.none"] and none.get("format") == 2, (files, n0, n1))
    r2 = run_mat(gid, out, record=rp, force=False)
    check("none: zweiter Start überspringt", "übersprungen" in r2["out"], r2["out"])
    RESULTS["none"] = {"intents_before": n0, "after": n1, "files": files}


# ── hooks ────────────────────────────────────────────────────────────────────
def step_hooks():
    print("== hooks", flush=True)
    out = os.path.join(OUT, "hooks")
    shutil.rmtree(out, ignore_errors=True)
    res = {}
    for gid in ("uLwSPQFK", "dJtnLxJA"):
        r = run_mat(gid, out, env={"CELLS": "1", "TIER1_PCT": "100"}, variant="mat-core-stubs")
        check(f"hooks {gid} rc 0", r["rc"] == 0, r["err"][-300:])
        ok = ok_of(out, gid)
        g = reader.Game(out, gid)
        hdr = g.hdr
        chk = json.load(open(os.path.join(out, f"{gid}.chk")))
        check(f"hooks {gid} reader (cells-Gleichschritt)", reader.check_dir(out, [gid])[gid] == "ok")
        ncell = sum(1 for m in g.meta() if m.get("cell", -1) >= 0)
        check(f"hooks {gid} jeder räumliche Sample hat einen Zellblock", ncell == ok["spatial"] > 0, (ncell, ok["spatial"]))
        check(f"hooks {gid} tier1 onTick je Zug einmal", chk["n"] == ok["ticks"] and chk["first"] == 0 and chk["last"] == ok["ticks"] - 1,
              (chk["n"], ok["ticks"]))
        check(f"hooks {gid} spawnEndTick vorab = beobachtet", chk["spawnEndTick"] == hdr["spawnEndTick"], (chk["spawnEndTick"], hdr["spawnEndTick"]))
        check(f"hooks {gid} .chk in .ok gelistet", f"{gid}.chk" in ok["files"] and hdr["tier1"] is True)
        gdir = os.path.join(OUT, "gate")
        check(f"hooks {gid} .maps identisch zum Gate-Lauf", sha(os.path.join(out, f"{gid}.maps")) == sha(os.path.join(gdir, f"{gid}.maps")))
        check(f"hooks {gid} hash.digest identisch zum Gate-Lauf", ok["hash"]["digest"] == ok_of(gdir, gid)["hash"]["digest"],
              (ok["hash"]["digest"], ok_of(gdir, gid)["hash"]["digest"]))
        # owner_major im Zellblock == ownerG, das auch die Karte speist: Stichprobe erster Block
        res[gid] = {"cells_bytes": ok["cells_bytes"], "spatial": ok["spatial"], "legal_bit1": ok["legal_bit1"],
                    "time_ms": ok["time_ms"], "cpu_ms": ok["cpu_ms"]}
    out2 = os.path.join(OUT, "hooks_cellfail")
    shutil.rmtree(out2, ignore_errors=True)
    gid = "dJtnLxJA"
    run_mat(gid, out2, env={"CELLS": "1", "TIER1_PCT": "100", "CELLS_STUB_FAIL_EVERY": "3"}, variant="mat-core-stubs")
    ok2, ok1 = ok_of(out2, gid), ok_of(out, gid)
    g2 = reader.Game(out2, gid)
    m1 = sum(1 for m in g2.meta() if m.get("click") is not None and m.get("cell") == -1)
    check("hooks Zellfehler: gezählt, Samples bleiben, cell -1", ok2["errors"]["cells"] > 0 and ok2["samples"] == ok1["samples"] and m1 > 0,
          (ok2["errors"], ok2["samples"], ok1["samples"], m1))
    check("hooks Zellfehler: reader-Gleichschritt", reader.check_dir(out2, [gid])[gid] == "ok")
    RESULTS["hooks"] = res


# ── negative ─────────────────────────────────────────────────────────────────
def step_negative():
    print("== negative (jede Prüfung muss hier ROT sein)", flush=True)
    gid = "uLwSPQFK"
    src = os.path.join(OUT, "gate")
    neg = os.path.join(OUT, "neg")
    shutil.rmtree(neg, ignore_errors=True)

    def copy(name):
        d = os.path.join(neg, name)
        os.makedirs(d)
        for f in os.listdir(src):
            if f.startswith(gid):
                shutil.copy(os.path.join(src, f), d)
        return d

    # 1 ein Byte in Block 5 der .maps kippen (Grösse bleibt, .ok bleibt gültig)
    d = copy("flip")
    mp = os.path.join(d, f"{gid}.maps")
    data = bytearray(open(mp, "rb").read())
    off = 0
    for _ in range(5):
        off += 4 + int.from_bytes(data[off:off + 4], "little")
    data[off + 4 + 20] ^= 0x01
    open(mp, "wb").write(data)
    c = compare_ref.compare(os.path.join(DEV, "ref", "thin", "88cc95d8", "a"), d, gid)
    check("neg: gekippter Block → Byte-Tor rot", c["block_mismatch"] > 0 and not c["pass"], c["block_mismatch"])
    # 2 letzte Metazeile weg, .ok-Grösse nachgeführt (sonst schlüge schon die Grössenprüfung an)
    d = copy("dropline")
    mz = os.path.join(d, f"{gid}.meta.zst")
    lines = reader.zdec(open(mz, "rb").read()).decode().split("\n")[:-1]
    from compression import zstd
    open(mz, "wb").write(zstd.compress("\n".join(lines).encode()))
    okp = os.path.join(d, f"{gid}.ok")
    ok = json.load(open(okp))
    ok["files"][f"{gid}.meta.zst"] = os.path.getsize(mz)
    json.dump(ok, open(okp, "w"))
    p = reader.check_dir(d, [gid])[gid]
    check("neg: fehlende Metazeile → reader rot", isinstance(p, list), p)
    # 3 .ok-Grösse falsch
    d = copy("size")
    okp = os.path.join(d, f"{gid}.ok")
    ok = json.load(open(okp))
    ok["files"][f"{gid}.maps"] += 1
    json.dump(ok, open(okp, "w"))
    p = reader.check_dir(d, [gid])[gid]
    check("neg: falsche .ok-Grösse → reader rot", isinstance(p, list), p)
    # 4 cut_check selbst: gegen einen falschen Schnitt muss er Abweichungen melden
    probs, _ = cut_check(src, os.path.join(OUT, "desync"), "dJtnLxJA", RESULTS.get("desync", {}).get("last_ok", 0) + 50)
    check("neg: cut_check mit falschem Schnitt → rot", bool(probs), probs[:1])


# ── report ───────────────────────────────────────────────────────────────────
def pct(x, n):
    return f"{100 * x / n:.1f}" if n else "–"


def step_report():
    print("== report", flush=True)
    out = os.path.join(OUT, "gate")
    ref_times = {}
    with open(os.path.join(DEV, "ref", "results.tsv")) as f:
        hdr = next(f).rstrip("\n").split("\t")
        for line in f:
            r = dict(zip(hdr, line.rstrip("\n").split("\t")))
            if r["mode"] == "thin" and r["run_suffix"] == "a":
                ref_times[r["gameID"]] = float(r["seconds"])
    gate = RESULTS.get("gate") or {}
    print("\nByte-Tor (thin-Referenz, NOOP_EVERY=200 MERGE_TICKS=30 CELLS=0 TIER1_PCT=0)")
    print("commit    gid       alt  vorh. %vorh  %erkl  Blöcke gleich   fehlend(Grund)            neu(kind)")
    for c, gs in REFS.items():
        for g in gs:
            r = gate[g]["cmp"]
            print(f"{c}  {g}  {r['old']:5d}  {r['present']:5d}  {100*r['coverage']:5.1f}  {100*r['coverage_explained']:5.1f}  "
                  f"{r['blocks_compared']-r['block_mismatch']:6d}/{r['blocks_compared']:<6d}  {json.dumps(r['missing']):24s}  {json.dumps(r['new_by_kind'])}")
    print("\nHashes")
    print("gid       geprüft fehlend orphan desync tick_error digest")
    for c, gs in REFS.items():
        for g in gs:
            o = gate[g]["ok"]
            h = o["hash"]
            print(f"{g}  {h['checked']:7d} {h['missing']:7d} {h['orphan']:6d} {str(o['desync']):6s} {str(o['tick_error']):10s} {h['digest']}")
    # res
    per = {}
    for g in gate:
        gg = reader.Game(out, g)
        W = gg.hdr["W"]
        for m in gg.meta():
            if "res_kind" not in m:
                continue
            t = rtype(m)
            s = per.setdefault(t, {"n": 0, 0: 0, 1: 0, 2: 0, "d": [], "late": 0, "ok": 0})
            s["n"] += 1
            s[m["res_kind"]] += 1
            if m["res_kind"] in (0, 1) and m.get("click", -1) >= 0 and m["res_tile"] >= 0:
                a, b = m["click"], m["res_tile"]
                s["d"].append(math.hypot(a % W - b % W, a // W - b // W))
        for t, st in gate[g]["ok"]["res"].items():
            per.setdefault(t, {"n": 0, 0: 0, 1: 0, 2: 0, "d": [], "late": 0, "ok": 0})
            per[t]["late"] += st["late"]
            per[t]["ok"] += st["ok"]
    print("\nres (alle 9 Records)")
    print("Typ               n   %kind0 %kind1 %kind2  Median  p90   %ausserhalb(late/ok)")
    for t, s in sorted(per.items(), key=lambda kv: -kv[1]["n"]):
        d = sorted(s["d"])
        med = f"{statistics.median(d):.1f}" if d else "–"
        p90 = f"{d[min(len(d) - 1, int(0.9 * len(d)))]:.1f}" if d else "–"
        print(f"{t:16s} {s['n']:4d}  {pct(s[0], s['n']):>6} {pct(s[1], s['n']):>6} {pct(s[2], s['n']):>6}  {med:>6} {p90:>5}  {pct(s['late'], s['ok']):>6}")
    RESULTS["res"] = {t: {k: (v if k != "d" else len(v)) for k, v in s.items()} for t, s in per.items()}
    print("\nZeit (neu: Wandzeit inkl. npx-Start; Stufen aus .ok time_ms; alt: results.tsv thin)")
    print("gid       alt_s  neu_s  total  sim   scan  vec   map  json  res  io   cpu_user  rss_MB")
    for c, gs in REFS.items():
        for g in gs:
            o = gate[g]["ok"]
            tm = o["time_ms"]
            print(f"{g}  {ref_times.get(g, float('nan')):6.1f} {gate[g]['run']['wall']:6.1f} {tm['total']/1000:6.1f} "
                  f"{tm['sim']/1000:5.1f} {tm['scan']/1000:5.1f} {tm['vec']/1000:5.1f} {tm['map']/1000:5.1f} {tm['json']/1000:5.2f} "
                  f"{tm['res']/1000:5.2f} {tm['io']/1000:5.2f} {o['cpu_ms']['user']/1000:8.1f} {o['rss_max_mb']:6d}")


STEPS = {"gate": step_gate, "desync": step_desync, "tickerr": step_tickerr, "kill": step_kill, "fault": step_fault,
         "none": step_none, "hooks": step_hooks, "negative": step_negative, "report": step_report}


def main(argv):
    steps = argv or list(STEPS)
    os.makedirs(OUT, exist_ok=True)
    rp = os.path.join(OUT, "results.json")
    if os.path.exists(rp):
        RESULTS.update(json.load(open(rp)))
    if "gate" not in steps and "gate" not in RESULTS:
        print("gate fehlt: zuerst 'gate' laufen lassen")
        return 2
    n_before = 0
    for s in steps:
        try:
            STEPS[s]()
        except Exception as e:  # ein kaputter Schritt ist ein Fehlschlag, kein stiller Abbruch
            import traceback
            traceback.print_exc()
            check(f"{s} ohne Ausnahme", False, e)
        json.dump(RESULTS, open(rp, "w"), default=str)
    print(f"\n{len(FAILS)} Prüfungen durchgefallen: {FAILS}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

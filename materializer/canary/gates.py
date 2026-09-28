#!/usr/bin/env python3
"""Harte Tore des Kanarienlaufs (DESIGN.md §9), Messwerte und Zweigempfehlung (Entwurf §7.3).

    python3 gates.py --out <out_root> --list <liste.tsv> --records-root <wurzel>
                     --free-gb <GB|unbekannt> [--inventory <records.tsv>]
                     [--min-mask 50] [--min-blocks 100] [--md pfad] [--json pfad]

<out_root> enthält A/ (alt), B/ (neu mit allem), C/ (neu ohne Zusatzfelder), wie canary.sh
sie anlegt, dazu optional <Lauf>.run.json mit der Wandzeit.

Tore (jedes Nein heisst: nicht starten). Jedes Tor braucht eine Mindestmenge geprüfter
Elemente, sonst ist es rot; "0 Fehler bei 0 geprüften" ist kein Erfolg.
  1 Byte-Gleichheit A gegen B über tests/core/compare_ref.py; jede Partie mit alten
    Samples muss verglichen sein und bestehen, dazu Abdeckung gesamt ≥ 95 %
  2 Checksummen: py/tier1.py verify für jede B-Partie mit Tier 1, alle Exit 0;
    jede Partie, für die Tier 1 laut Auswahlregel (§5.3) fällig ist, muss es haben
  3 Maskenverletzungen < 1 % (Typ → Bit, §6), nur res_kind 0, am Zell-Index von res_tile
  4 CPU-Aufschlag B gegen C ≤ 15 %, Summe cpu_ms (user+system) über alle Partien
  5 Treue: Record mit Hashes → hash.checked > 0, kein Desync, kein tick_error (B und C);
    hash.digest B == C je Partie
  6 Vollständigkeit: jede Partie hat .ok (Grössenprüfung bestanden), .none oder .err,
    und keine .meta.zst < 64 Byte

Exit 0 nur, wenn alle sechs Tore grün sind.
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
# canary/select.py würde sonst das Standardmodul `select` verdecken (subprocess → selectors
# → select.select): den eigenen Ordner aus dem Suchpfad nehmen, bevor irgendetwas importiert wird.
sys.path[:] = [p for p in sys.path if os.path.abspath(p or os.getcwd()) != HERE]

import argparse  # noqa: E402
import json  # noqa: E402
import math  # noqa: E402
import re  # noqa: E402
import struct  # noqa: E402
import subprocess  # noqa: E402
from collections import Counter, defaultdict  # noqa: E402

sys.path.insert(0, os.path.join(HERE, "..", "py"))
sys.path.insert(0, os.path.join(HERE, "..", "tests", "core"))
import reader  # noqa: E402
import compare_ref  # noqa: E402

TIER1_PY = os.path.join(HERE, "..", "py", "tier1.py")
TOTAL_GAMES = 18018                       # Zielmenge laut Entwurf
MIN_META = 64
GH, GW = 90, 180
OFF_LEGAL = GH * GW * 3                   # Zellblock: owner u16[16200] ‖ own_frac u8 ‖ legal u8

# Typ → Bit (DESIGN §6). Unit-Strings wie UnitType in der Engine (an allen drei Commits gleich).
STRUCT = {"City", "Defense Post", "SAM Launcher", "Missile Silo", "Factory"}
NUKES = {"Atom Bomb", "Hydrogen Bomb", "MIRV"}
GATE_NAMES = {1: "Byte-Gleichheit", 2: "Checksummen", 3: "Maskenverletzungen", 4: "CPU-Aufschlag",
              5: "Treue des Nachspielens", 6: "Vollständigkeit"}


def type_key(intent: dict) -> str:
    return str(intent.get("unit")) if intent.get("type") == "build_unit" else str(intent.get("type"))


def required_bit(intent: dict, legal_bit1: bool) -> int | None:
    """Bit, das die Zelle von res_tile haben muss. None: keine Regel (z.B. Upgrade)."""
    t = intent.get("type")
    if t == "build_unit":
        u = intent.get("unit")
        if u in STRUCT:
            return 1 if legal_bit1 else 0
        if u == "Port":
            return 2 if legal_bit1 else 0
        if u == "Warship":
            return 4
        if u in NUKES:
            return 7
        return None
    return {"boat": 3, "move_warship": 5, "spawn": 6}.get(t)


# ─────────────────────────── Ein- und Ausgabe ────────────────────────────────

def load_list(path: str) -> list[dict]:
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.startswith("#") or not line.strip():
                continue
            x = line.rstrip("\n").split("\t")
            rows.append({"rel": x[0], "gid": x[1], "commit": x[2] if len(x) > 2 else "?",
                         "size": int(x[3]) if len(x) > 3 and x[3].isdigit() else 0,
                         "map": x[4] if len(x) > 4 else "?", "mode": x[5] if len(x) > 5 else "?"})
    return rows


def _json(p: str):
    try:
        with open(p, "rb") as f:
            return json.loads(f.read())
    except (OSError, ValueError):
        return None


def marker_new(d: str, gid: str):
    """Zustand einer Partie im neuen Format: (art, daten). art ∈ ok, none, err, ok_kaputt, fehlt."""
    none = _json(reader.path_of(d, gid, "none"))
    if isinstance(none, dict) and none.get("format") == 2:
        return "none", none
    if os.path.exists(reader.path_of(d, gid, "ok")):
        probs = reader.ok_problems(d, gid)
        return ("ok", _json(reader.path_of(d, gid, "ok"))) if not probs else ("ok_kaputt", probs)
    if os.path.exists(reader.path_of(d, gid, "err")):
        with open(reader.path_of(d, gid, "err"), errors="replace") as f:
            return "err", f.read(300)
    return "fehlt", None


def marker_old(d: str, gid: str):
    """Alter Materialisierer: .meta.zst ≥ 64 Byte = Daten, leere .none = leer, .err vom Dispatcher."""
    m = reader.path_of(d, gid, "meta.zst")
    if os.path.exists(m) and os.path.getsize(m) >= MIN_META and os.path.exists(reader.path_of(d, gid, "maps")):
        return "data", None
    if os.path.exists(reader.path_of(d, gid, "none")):
        return "none", None
    if os.path.exists(reader.path_of(d, gid, "err")):
        with open(reader.path_of(d, gid, "err"), errors="replace") as f:
            return "err", f.read(300)
    return "fehlt", None


def block_lengths(path: str) -> list[int]:
    """Längen der Blöcke [u32 LE][Block], ohne die Datei in den Speicher zu laden."""
    out = []
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        off = 0
        while off < size:
            (ln,) = struct.unpack("<I", f.read(4))
            out.append(ln)
            off += 4 + ln
            f.seek(off)
    return out


_hash_cache: dict[str, int] = {}


def record_hashes(root: str, rel: str) -> int:
    """Zahl der Züge mit Hash im Record (aus dem Record gezählt, nicht aus der Ausgabe)."""
    p = os.path.join(root, rel)
    if p not in _hash_cache:
        with open(p, "rb") as f:
            rec = json.loads(f.read())
        turns = rec.get("turns") or []
        _hash_cache[p] = sum(1 for t in turns if isinstance(t, dict) and t.get("hash") is not None)
    return _hash_cache[p]


def pct(xs: list[float], q: float):
    if not xs:
        return None
    s = sorted(xs)
    return s[min(len(s) - 1, int(q * (len(s) - 1) + 0.5))]


def gate(no: int, ok: bool, checked: int, need: int, summary: str, problems: list, **data) -> dict:
    """Ein Tor ist nur grün, wenn keine Probleme vorliegen UND die Mindestmenge geprüft ist."""
    if checked < need:
        problems = problems + [f"nur {checked} geprüft, Mindestmenge {need}"]
        ok = False
    return {"tor": no, "name": GATE_NAMES[no], "gruen": bool(ok and not problems), "geprueft": checked,
            "mindestmenge": need, "zusammenfassung": summary, "probleme": problems, **data}


# ─────────────────────────── Tore ────────────────────────────────────────────

def gate1(out: str, games: list[dict], min_blocks: int) -> dict:
    A, B = os.path.join(out, "A"), os.path.join(out, "B")
    probs, per = [], []
    reasons = Counter()
    n_old = present = blocks = mism = compared = 0
    leer = 0
    for g in games:
        gid = g["gid"]
        a, _ = marker_old(A, gid)
        b, bd = marker_new(B, gid)
        if a in ("fehlt", "err"):
            probs.append(f"{gid}: alter Lauf ohne Ergebnis ({a})")
            continue
        if a == "none":
            leer += 1
            per.append({"gid": gid, "alt": "leer", "neu": b})
            continue
        try:
            r = compare_ref.compare(A, B, gid)
        except Exception as e:  # B fehlt/kaputt: Durchfall, kein Absturz
            probs.append(f"{gid}: nicht vergleichbar: {e}")
            continue
        compared += 1
        n_old += r["old"]
        present += r["present"]
        blocks += r["blocks_compared"]
        mism += r["block_mismatch"]
        reasons.update(r["missing"])
        per.append({k: r[k] for k in ("gid", "old", "new", "present", "coverage", "coverage_explained",
                                      "blocks_compared", "block_mismatch", "missing", "pass")})
        if not r["pass"]:
            probs.append(f"{gid}: DURCHGEFALLEN (Abdeckung mit erklärt {100 * r['coverage_explained']:.1f} %, "
                         f"abweichende Blöcke {r['block_mismatch']} {r['mismatch_keys']}, fehlend {r['missing']}"
                         f"{', Beispiele ' + json.dumps(r['examples'][:2]) if r['examples'] else ''})")
    expl = reasons["zusammengefasst"] + reasons["spawn_nach_phase"]
    cov = (present + expl) / n_old if n_old else 0.0
    if n_old and cov < 0.95:
        probs.append(f"Abdeckung gesamt {100 * cov:.2f} % < 95 %")
    s = (f"{compared} Partien verglichen ({leer} alt leer), {blocks} Blöcke, {mism} abweichend; "
         f"alte Samples {n_old}, vorhanden {present}, erklärt {expl}, Abdeckung {100 * cov:.2f} %; "
         f"Rest nach Grund {dict(reasons)}")
    return gate(1, not probs, blocks, min_blocks, s, probs, partien=per, abdeckung=round(cov, 5),
                gruende=dict(reasons))


def tier1_expected(gid: str, pct_: int) -> bool:
    if pct_ <= 0:
        return False
    return reader.sha1_int(gid) % 100 < pct_ or reader.is_val(gid)


def gate2(out: str, games: list[dict], timeout_s: int) -> dict:
    B = os.path.join(out, "B")
    probs, per = [], []
    hit = tot = verified = 0
    dropped = 0
    for g in games:
        gid = g["gid"]
        k, ok = marker_new(B, gid)
        if k != "ok" or ok["samples"] == 0:
            continue
        hdr = _json(reader.path_of(B, gid, "hdr.json")) or {}
        p = int((hdr.get("params") or {}).get("TIER1_PCT", 0))
        has = bool(hdr.get("tier1")) and f"{gid}.chk" in ok["files"]
        t1err = (ok.get("errors") or {}).get("tier1", 0)
        if t1err:
            probs.append(f"{gid}: errors.tier1 = {t1err} ({ok.get('first_error')})")
        if tier1_expected(gid, p) and not has:
            probs.append(f"{gid}: Tier 1 fällig (TIER1_PCT={p}), aber keine .chk/tier1 im Kopf")
        if not has:
            continue
        dropped += ok.get("chk_dropped", 0) or 0
        try:
            cp = subprocess.run([sys.executable, TIER1_PY, "verify", B, gid], capture_output=True, text=True,
                                timeout=timeout_s)
            txt, rc = (cp.stdout + cp.stderr).strip(), cp.returncode
        except subprocess.TimeoutExpired:
            txt, rc = "Zeitlimit", -1
        m = re.search(r"chk (\d+)/(\d+)", txt)
        a, b = (int(m.group(1)), int(m.group(2))) if m else (0, 0)
        hit += a
        tot += b
        verified += rc == 0
        per.append({"gid": gid, "rc": rc, "chk": f"{a}/{b}", "zeile": txt.splitlines()[0][:300] if txt else ""})
        if rc != 0:
            probs.append(f"{gid}: tier1.py verify Exit {rc}: {' | '.join(txt.splitlines()[:4])[:400]}")
    s = f"{len(per)} Partien mit Tier 1, {verified} bestanden, .chk-Prüfpunkte {hit}/{tot}, chk_dropped {dropped}"
    return gate(2, not probs and hit == tot, min(verified, tot), 1, s, probs, partien=per)


def scan_b(out: str, games: list[dict]) -> dict:
    """Ein Durchgang über die B-Metazeilen: Tor 3, Abstand Klick → res_tile, res_kind je Typ, Spawn-Bytes."""
    B = os.path.join(out, "B")
    per_type = defaultdict(lambda: [0, 0])          # typ → [geprüft, verletzt]
    per_dt = defaultdict(lambda: [0, 0])            # (typ, res_dt) → [geprüft, verletzt]
    dist = defaultdict(list)
    kinds = defaultdict(Counter)
    notes, probs, examples = [], [], []
    cand = unchecked = 0
    spawn_bytes = Counter()
    spatial_by_game = {}
    for g in games:
        gid = g["gid"]
        k, ok = marker_new(B, gid)
        if k != "ok" or ok["samples"] == 0:
            continue
        try:
            G = reader.Game(B, gid)
            meta = G.meta()
        except Exception as e:
            probs.append(f"{gid}: nicht lesbar: {e}")
            continue
        W, H = G.hdr["W"], G.hdr["H"]
        lb1 = ok.get("legal_bit1")
        if lb1 is None:
            notes.append(f"{gid}: .ok ohne legal_bit1 (älterer Kern), Bit 1/2 angenommen")
            lb1 = True
        # Spawn-Bytes (Fixteil der Hochrechnung): Länge der Kartenblöcke der Spawn-Samples
        lens = block_lengths(reader.path_of(B, gid, "maps"))
        for m, ln in zip(meta, lens):
            if m.get("kind") == "spawn":
                spawn_bytes[gid] += ln + 4
        spatial_by_game[gid] = ok.get("spatial", 0)
        cells = G.cell_blocks()
        next_cell = 0
        for m in meta:
            c = m.get("cell", -1)
            blk = None
            if c >= 0:
                if c != next_cell:
                    probs.append(f"{gid}: cell {c} ausser Reihe (erwartet {next_cell})")
                    break
                blk = next(cells, None)
                next_cell += 1
            if "res_kind" not in m:
                continue
            tk = type_key(m["intent"])
            kinds[tk][m["res_kind"]] += 1
            click, rt = m.get("click", -1), m.get("res_tile", -1)
            if m["res_kind"] in (0, 1) and isinstance(click, int) and click >= 0 and rt >= 0:
                dist[tk].append(math.hypot(click % W - rt % W, click // W - rt // W))
            if m["res_kind"] != 0:
                continue
            bit = required_bit(m["intent"], lb1)
            if bit is None:
                continue
            cand += 1
            if blk is None or rt < 0:
                unchecked += 1
                continue
            x, y = rt % W, rt // W
            idx = int(y * GH / H) * GW + int(x * GW / W)          # wie obs.ts: (y*90/H)|0, (x*180/W)|0
            legal = reader.zdec(blk)[OFF_LEGAL + idx]
            bad = not (legal >> bit) & 1
            per_type[tk][0] += 1
            per_type[tk][1] += bad
            per_dt[(tk, m.get("res_dt"))][0] += 1
            per_dt[(tk, m.get("res_dt"))][1] += bad
            if bad and len(examples) < 5:
                examples.append({"gid": gid, "turn": m["turn"], "typ": tk, "bit": bit, "res_tile": rt,
                                 "zelle": idx, "legal": legal, "res_dt": m.get("res_dt")})
    return {"per_type": per_type, "per_dt": per_dt, "dist": dist, "kinds": kinds, "notes": notes,
            "probs": probs, "examples": examples, "cand": cand, "unchecked": unchecked,
            "spawn_bytes": spawn_bytes, "spatial_by_game": spatial_by_game}


def gate3(sc: dict, min_checked: int) -> dict:
    checked = sum(v[0] for v in sc["per_type"].values())
    viol = sum(v[1] for v in sc["per_type"].values())
    rate = viol / checked if checked else 1.0
    probs = list(sc["probs"])
    if checked and rate >= 0.01:
        probs.append(f"Verletzungsrate {100 * rate:.2f} % ≥ 1 %; Beispiele {json.dumps(sc['examples'])}")
    # Kandidaten ohne Zellblock (cell -1) sind ungeprüft. Mehr als 1 % davon hiesse, dass das Tor
    # einen Teil still auslässt: rot (eigene Präzisierung der Mindestmenge).
    if sc["cand"] and sc["unchecked"] > 0.01 * sc["cand"]:
        probs.append(f"{sc['unchecked']} von {sc['cand']} Kandidaten ohne Zellblock oder res_tile (> 1 %)")
    per_type = {k: {"geprueft": v[0], "verletzt": v[1], "rate": round(v[1] / v[0], 4) if v[0] else None}
                for k, v in sorted(sc["per_type"].items())}
    per_dt = {f"{k[0]} dt={k[1]}": {"geprueft": v[0], "verletzt": v[1]} for k, v in sorted(sc["per_dt"].items(), key=str)}
    s = f"{checked} geprüft, {viol} verletzt ({100 * rate:.3f} %), {sc['unchecked']} ungeprüft von {sc['cand']} Kandidaten"
    return gate(3, not probs, checked, min_checked, s, probs, je_typ=per_type, je_typ_dt=per_dt,
                hinweise=sc["notes"][:10])


def cpu(ok: dict | None):
    c = (ok or {}).get("cpu_ms")
    if not isinstance(c, dict) or "user" not in c:
        return None
    return c["user"] + c.get("system", 0)


def gate4(out: str, games: list[dict]) -> dict:
    B, C = os.path.join(out, "B"), os.path.join(out, "C")
    probs, per = [], []
    sb = sc = n = 0
    for g in games:
        gid = g["gid"]
        kb, ob = marker_new(B, gid)
        kc, oc = marker_new(C, gid)
        if kb == "none" and kc == "none":
            continue
        if kb != "ok" or kc != "ok":
            probs.append(f"{gid}: nicht in beiden Läufen fertig (B {kb}, C {kc})")
            continue
        cb, cc = cpu(ob), cpu(oc)
        if cb is None or cc is None:
            probs.append(f"{gid}: cpu_ms fehlt (B {cb}, C {cc})")
            continue
        if ob["samples"] != oc["samples"]:
            probs.append(f"{gid}: Samples ungleich (B {ob['samples']}, C {oc['samples']}), Vergleich ungültig")
        sb += cb
        sc += cc
        n += 1
        per.append({"gid": gid, "cpu_B_ms": cb, "cpu_C_ms": cc, "aufschlag": round(cb / cc - 1, 4) if cc else None})
    o = sb / sc - 1 if sc else None
    if o is not None and o > 0.15:
        probs.append(f"CPU-Aufschlag {100 * o:.1f} % > 15 %: Rückfallstufe 1 (LEGAL_BIT1=0), dann 2 (CELLS=0), neu messen")
    # Information alt gegen neu: A hat kein cpu_ms, also Wandzeit der ganzen Läufe
    ra, rb = _json(os.path.join(out, "A.run.json")), _json(os.path.join(out, "B.run.json"))
    info = "A gegen B: keine Laufzeiten vorhanden"
    if ra and rb and ra.get("wall_s") and rb.get("wall_s"):
        info = (f"A gegen B (nur Information, Wandzeit statt CPU, weil der alte Materialisierer kein cpu_ms "
                f"schreibt; Samples verschieden): A {ra['wall_s']} s, B {rb['wall_s']} s, "
                f"B/A {float(rb['wall_s']) / float(ra['wall_s']):.2f}")
    s = (f"{n} Partien, CPU B {sb / 1000:.1f} s, C {sc / 1000:.1f} s, Aufschlag "
         f"{'–' if o is None else f'{100 * o:.2f} %'}")
    return gate(4, not probs, n if sc > 0 else 0, 1, s, probs, partien=per, aufschlag=o, info_alt_neu=info)


def gate5(out: str, games: list[dict], root: str) -> dict:
    B, C = os.path.join(out, "B"), os.path.join(out, "C")
    probs, per = [], []
    with_hash = checked_games = 0
    tot_checked = 0
    for g in games:
        gid = g["gid"]
        try:
            rh = record_hashes(root, g["rel"])
        except Exception as e:
            probs.append(f"{gid}: Record nicht lesbar: {e}")
            continue
        with_hash += rh > 0
        row = {"gid": gid, "record_hashes": rh}
        digests = {}
        for run, d in (("B", B), ("C", C)):
            k, j = marker_new(d, gid)
            if k not in ("ok", "none"):
                probs.append(f"{gid}: {run} ohne .ok/.none ({k})")
                continue
            h = j.get("hash") or {}
            row[f"checked_{run}"] = h.get("checked")
            digests[run] = h.get("digest")
            if rh > 0 and not (h.get("checked") or 0) > 0:
                probs.append(f"{gid}: {run} hash.checked = {h.get('checked')} bei {rh} Hashes im Record")
            if j.get("desync"):
                probs.append(f"{gid}: {run} Desync {j['desync']}")
            if j.get("tick_error"):
                probs.append(f"{gid}: {run} tick_error {j['tick_error']}")
        if len(digests) == 2:
            if digests["B"] is None or digests["B"] != digests["C"]:
                probs.append(f"{gid}: hash.digest B {digests['B']} ≠ C {digests['C']}")
            elif rh > 0:
                checked_games += 1
                tot_checked += row.get("checked_B") or 0
        per.append(row)
    s = (f"{with_hash} Records mit Hashes, {checked_games} davon mit geprüften Hashes und gleichem Digest, "
         f"{tot_checked} Hashes bestätigt (B)")
    return gate(5, not probs, checked_games, 1, s, probs, partien=per)


def gate6(out: str, games: list[dict]) -> dict:
    probs = []
    count = Counter()
    for run, newfmt in (("A", False), ("B", True), ("C", True)):
        d = os.path.join(out, run)
        for g in games:
            k, v = (marker_new if newfmt else marker_old)(d, g["gid"])
            count[(run, k)] += 1
            if k not in ("ok", "none", "err", "data"):
                probs.append(f"{run}/{g['gid']}: {k}{': ' + '; '.join(v) if isinstance(v, list) else ''}")
        if os.path.isdir(d):
            for f in os.listdir(d):
                if f.endswith(".meta.zst") and os.path.getsize(os.path.join(d, f)) < MIN_META:
                    probs.append(f"{run}/{f}: {os.path.getsize(os.path.join(d, f))} Byte < {MIN_META}")
    s = ", ".join(f"{r}:{k} {n}" for (r, k), n in sorted(count.items()))
    return gate(6, not probs, len(games) if games else 0, 1, s, probs, zaehlung={f"{r}/{k}": n for (r, k), n in count.items()})


# ─────────────────────────── Messwerte, Hochrechnung, Zweig ──────────────────

def measures(out: str, games: list[dict], sc: dict, root: str) -> dict:
    B, C = os.path.join(out, "B"), os.path.join(out, "C")
    per = []
    agg = defaultdict(float)
    rss = {"B": [], "C": []}
    errs = Counter()
    for g in games:
        gid = g["gid"]
        kb, ob = marker_new(B, gid)
        kc, oc = marker_new(C, gid)
        row = {"gid": gid, "commit": g["commit"], "map": g["map"], "mode": g["mode"], "size": g["size"],
               "B": kb, "C": kc, "A": marker_old(os.path.join(out, "A"), gid)[0]}
        for run, k, o in (("B", kb, ob), ("C", kc, oc)):
            if k == "ok":
                tm = o.get("time_ms") or {}
                agg[f"sim_{run}"] += tm.get("sim", 0)
                agg[f"total_{run}"] += tm.get("total", 0)
                if o.get("rss_max_mb") is not None:
                    rss[run].append(o["rss_max_mb"])
        if kb == "ok":
            row.update(samples=ob["samples"], spatial=ob.get("spatial", 0), by_kind=ob.get("by_kind"),
                       tier1_mb=round(ob.get("tier1_bytes", 0) / 1e6, 3), cells_kb=round(ob.get("cells_bytes", 0) / 1e3, 1),
                       hash=ob.get("hash"), res=ob.get("res"))
            agg["tier1_bytes"] += ob.get("tier1_bytes", 0) or 0
            agg["tier1_games"] += (ob.get("tier1_bytes", 0) or 0) > 0
            agg["cells_bytes"] += ob.get("cells_bytes", 0) or 0
            agg["spatial_cells"] += ob.get("spatial", 0) if ob.get("cells_bytes") else 0
            agg["hash_checked"] += (ob.get("hash") or {}).get("checked", 0)
            agg["desync"] += bool(ob.get("desync"))
            agg["tick_error"] += bool(ob.get("tick_error"))
            errs.update({k: v for k, v in (ob.get("errors") or {}).items() if v})
        per.append(row)
    t1s = [r["tier1_mb"] for r in per if r.get("tier1_mb")]
    dist = {k: {"n": len(v), "median": round(pct(v, .5), 2), "p90": round(pct(v, .9), 2)}
            for k, v in sorted(sc["dist"].items()) if v}
    kinds = {k: dict(sorted(v.items())) for k, v in sorted(sc["kinds"].items())}
    by_kind = Counter()
    for r in per:
        by_kind.update(r.get("by_kind") or {})
    n_ok = sum(1 for r in per if r["B"] == "ok")
    return {
        "t1_mb_mittel": round(agg["tier1_bytes"] / agg["tier1_games"] / 1e6, 3) if agg["tier1_games"] else None,
        "t1_mb_median": pct(t1s, .5), "t1_mb_max": max(t1s) if t1s else None,
        "z_kb_je_raeumlichem_sample": round(agg["cells_bytes"] / agg["spatial_cells"] / 1e3, 3) if agg["spatial_cells"] else None,
        "s_sim_anteil_C": round(agg["sim_C"] / agg["total_C"], 4) if agg["total_C"] else None,
        "s_sim_anteil_B": round(agg["sim_B"] / agg["total_B"], 4) if agg["total_B"] else None,
        "abstand_klick_res_tile": dist, "res_kind_je_typ": kinds,
        "hashes_geprueft_B": int(agg["hash_checked"]), "desyncs_B": int(agg["desync"]),
        "tick_errors_B": int(agg["tick_error"]), "sample_fehler_B": dict(errs),
        "samples_je_partie_nach_kind": {k: round(v / n_ok, 1) for k, v in sorted(by_kind.items())} if n_ok else {},
        "ram_spitze_mb": {r: {"max": max(v), "median": pct(v, .5)} for r, v in rss.items() if v},
        "partien": per,
    }


def extrapolate(games: list[dict], M: dict, sc: dict, inventory: str | None) -> dict:
    """Hochrechnung auf 18'018 Partien. SCHÄTZUNG: Verhältnisschätzer je Grössenquartil des
    Inventars (Messgrösse / Record-Byte der Kanarienpartien im Quartil × Record-Bytes des
    Inventars im Quartil), skaliert von der Inventargrösse auf 18'018."""
    inv_rows = []
    src = "Inventar"
    if inventory and os.path.exists(inventory):
        with open(inventory, encoding="utf-8") as f:
            f.readline()
            for line in f:
                x = line.rstrip("\n").split("\t")
                if len(x) >= 4 and x[3].isdigit():
                    inv_rows.append((x[1], int(x[3])))
    if not inv_rows:
        src = "Liste (kein Inventar, verzerrt: die Auswahl übergewichtet grosse Records)"
        inv_rows = [(g["gid"], g["size"]) for g in games]
    sizes = sorted(s for _, s in inv_rows)
    edges = [sizes[int(q * (len(sizes) - 1))] for q in (0.25, 0.5, 0.75)]
    binof = lambda s: sum(s > e for e in edges)  # noqa: E731
    inv_bytes = Counter()
    for _, s in inv_rows:
        inv_bytes[binof(s)] += s
    scale = TOTAL_GAMES / len(inv_rows)
    metric = defaultdict(lambda: defaultdict(float))   # bin → messgrösse → Summe
    for r in M["partien"]:
        if r["B"] != "ok":
            continue
        b = binof(r["size"])
        metric[b]["size"] += r["size"]
        metric[b]["tier1"] += r["tier1_mb"] * 1e6
        metric[b]["cells"] += r["cells_kb"] * 1e3
        metric[b]["spatial"] += r["spatial"]
        metric[b]["spawn"] += sc["spawn_bytes"].get(r["gid"], 0)
    glob = defaultdict(float)
    for b in metric.values():
        for k, v in b.items():
            glob[k] += v
    est, empty = {}, []
    for k in ("tier1", "cells", "spatial", "spawn"):
        tot = 0.0
        for b in range(4):
            m = metric.get(b)
            if m and m["size"] > 0:
                tot += m[k] / m["size"] * inv_bytes[b]
            elif glob["size"] > 0:
                tot += glob[k] / glob["size"] * inv_bytes[b]
                if k == "tier1":
                    empty.append(b)
        est[k] = tot * scale
    share = lambda p: sum(1 for g, _ in inv_rows if tier1_expected(g, p)) / len(inv_rows)  # noqa: E731
    return {"quelle": src, "inventar_partien": len(inv_rows), "skala_auf_18018": round(scale, 4),
            "quartilgrenzen_bytes": edges, "quartile_ohne_kanarienpartie": empty,
            "tier1_gb_alle": round(est["tier1"] / 1e9, 1), "zellfakten_gb": round(est["cells"] / 1e9, 1),
            "spawn_gb": round(est["spawn"] / 1e9, 1), "raeumliche_samples_mio": round(est["spatial"] / 1e6, 2),
            "anteil_tier1_25": round(share(25), 4), "anteil_tier1_15": round(share(15), 4),
            "hinweis": "Schätzung, kein Messwert"}


def branch(M: dict, X: dict, o: float | None, free_gb) -> dict:
    """Zweigregel Entwurf §7.3."""
    fixed = X["zellfakten_gb"] + X["spawn_gb"]
    t1_all = X["tier1_gb_alle"]
    t1_mean_mb = t1_all * 1e3 / TOTAL_GAMES
    cut_lo, cut_hi = X["raeumliche_samples_mio"] * 0.5, X["raeumliche_samples_mio"] * 1.3  # GB, Entwurf-Schätzung
    need = {"A": round((t1_all + fixed) * 1.2, 1),
            "B": round((t1_all * X["anteil_tier1_25"] + fixed + cut_hi) * 1.2, 1),
            "C": round((t1_all * X["anteil_tier1_15"] + fixed) * 1.2, 1)}
    why = []
    if free_gb is None:
        z = "A" if t1_mean_mb <= 6 else "B"
        why.append(f"freier Platz unbekannt: Cs Schwelle t1 ≤ 6 MB, hochgerechnet t1 = {t1_mean_mb:.2f} MB → Zweig {z}")
    else:
        z = next((k for k in ("A", "B", "C") if need[k] <= free_gb), None)
        why.append(f"Bedarf mit 20 % Puffer: A {need['A']} GB, B {need['B']} GB (Ausschnitte obere Schätzung), "
                   f"C {need['C']} GB; frei {free_gb} GB → {'Zweig ' + z if z else 'kein Zweig passt, Platz schaffen'}")
    s = M.get("s_sim_anteil_C")
    if s is not None:
        if s <= 0.2:
            why.append(f"s = {100 * s:.1f} % ≤ 20 %: Cs Befund steht, ein späterer schlanker Replay-Lauf ist billig; "
                       "Zweig B/C verliert sein Risiko")
        elif s >= 0.5:
            why.append(f"s = {100 * s:.1f} % ≥ 50 %: Prämisse fällt, Zweig A mit Vorrang, auch wenn Platz geschaffen werden muss")
            z = "A"
        else:
            why.append(f"s = {100 * s:.1f} % zwischen 20 und 50 %: keine Übersteuerung")
    if o is not None and o > 0.15:
        why.append(f"o = {100 * o:.1f} % > 15 %: zuerst die V1-Ausschnitte streichen, dann Bit 1")
    return {"zweig": z, "bedarf_gb": need, "fixteile_gb": round(fixed, 1), "t1_mb_hochgerechnet": round(t1_mean_mb, 3),
            "ausschnitte_gb": [round(cut_lo, 1), round(cut_hi, 1)], "frei_gb": free_gb, "begruendung": why}


# ─────────────────────────── Bericht ─────────────────────────────────────────

def run_gates(a) -> dict:
    games = load_list(a.list)
    free = None
    try:
        free = float(a.free_gb)
    except (TypeError, ValueError):
        pass
    g1 = gate1(a.out, games, a.min_blocks)
    g2 = gate2(a.out, games, a.tier1_timeout)
    sc = scan_b(a.out, games)
    g3 = gate3(sc, a.min_mask)
    g4 = gate4(a.out, games)
    g5 = gate5(a.out, games, a.records_root)
    g6 = gate6(a.out, games)
    gates = [g1, g2, g3, g4, g5, g6]
    M = measures(a.out, games, sc, a.records_root)
    M["o_cpu_aufschlag"] = g4.get("aufschlag")
    X = extrapolate(games, M, sc, a.inventory)
    Z = branch(M, X, g4.get("aufschlag"), free)
    red = [g for g in gates if not g["gruen"]]
    runs = {r: _json(os.path.join(a.out, f"{r}.run.json")) for r in "ABC"}
    if red:
        verdict = "NO-GO: " + "; ".join(f"Tor {g['tor']} ({g['name']}) rot: {(g['probleme'] or ['?'])[0]}" for g in red)
    else:
        verdict = f"GO: alle sechs Tore grün, Empfehlung Zweig {Z['zweig']}."
    return {"partien": len(games), "tore": gates, "messwerte": M, "hochrechnung": X, "zweig": Z,
            "laeufe": runs, "go": not red, "urteil": verdict}


def markdown(R: dict) -> str:
    L = [f"# Kanarienlauf: Tore", "", f"Partien in der Liste: {R['partien']}", ""]
    for r, v in R["laeufe"].items():
        if v:
            L.append(f"- Lauf {r}: rc {v.get('rc')}, {v.get('wall_s')} s Wand, cpus {v.get('cpus')}, env `{v.get('env')}`"
                     + (f", **Entwicklungs-Mounts:** `{v['extra_docker_args'].strip()}`" if v.get("extra_docker_args", "").strip() else ""))
    L += ["", "| Tor | | geprüft | Ergebnis |", "|---|---|---|---|"]
    for g in R["tore"]:
        L.append(f"| {g['tor']} {g['name']} | {'grün' if g['gruen'] else '**ROT**'} | {g['geprueft']} (min {g['mindestmenge']}) | {g['zusammenfassung']} |")
    for g in R["tore"]:
        if g["probleme"]:
            L += ["", f"**Tor {g['tor']} {g['name']}: Probleme**"] + [f"- {p}" for p in g["probleme"][:15]]
            if len(g["probleme"]) > 15:
                L.append(f"- … {len(g['probleme']) - 15} weitere")
    g3 = R["tore"][2]
    if g3.get("je_typ"):
        L += ["", "**Tor 3 je Typ**", "", "| Typ | geprüft | verletzt | Rate |", "|---|---|---|---|"]
        L += [f"| {k} | {v['geprueft']} | {v['verletzt']} | {v['rate']} |" for k, v in g3["je_typ"].items()]
        L += ["", "**Tor 3 je Typ und res_dt**: " + ", ".join(f"{k}: {v['verletzt']}/{v['geprueft']}" for k, v in g3["je_typ_dt"].items())]
    if g3.get("hinweise"):
        L += ["", "Hinweise Tor 3: " + "; ".join(g3["hinweise"][:3])]
    L += ["", f"Tor 4 Info: {R['tore'][3]['info_alt_neu']}"]
    M = R["messwerte"]
    L += ["", "## Messwerte", "",
          f"- t1 (MB je Partie, Tier 1): Mittel {M['t1_mb_mittel']}, Median {M['t1_mb_median']}, Max {M['t1_mb_max']}",
          f"- z (KB je räumlichem Sample, Zellfakten): {M['z_kb_je_raeumlichem_sample']}",
          f"- s (Sim-Anteil): C {M['s_sim_anteil_C']}, B {M['s_sim_anteil_B']}",
          f"- o (CPU-Aufschlag B gegen C): {M['o_cpu_aufschlag']}",
          f"- Hashes geprüft (B) {M['hashes_geprueft_B']}, Desyncs {M['desyncs_B']}, Tick-Fehler {M['tick_errors_B']}, "
          f"Sample-Fehler {M['sample_fehler_B']}",
          f"- Samples je Partie nach kind (B, Mittel): {M['samples_je_partie_nach_kind']}",
          f"- RAM-Spitze je Worker (MB): {M['ram_spitze_mb']}",
          "", "| Typ | Abstand Klick→res_tile Median | p90 | n | res_kind 0/1/2 |", "|---|---|---|---|---|"]
    for k in sorted(set(M["abstand_klick_res_tile"]) | set(M["res_kind_je_typ"])):
        d = M["abstand_klick_res_tile"].get(k, {})
        rk = M["res_kind_je_typ"].get(k, {})
        L.append(f"| {k} | {d.get('median', '–')} | {d.get('p90', '–')} | {d.get('n', 0)} | "
                 f"{rk.get(0, 0)}/{rk.get(1, 0)}/{rk.get(2, 0)} |")
    L += ["", "| gid | Commit | Karte | Modus | KB | A | B | C | Samples | räuml. | t1 MB | Zellen KB | Hashes |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in M["partien"]:
        h = r.get("hash") or {}
        L.append(f"| {r['gid']} | {r['commit']} | {r['map']} | {r['mode']} | {r['size'] // 1000} | {r['A']} | {r['B']} | {r['C']} | "
                 f"{r.get('samples', '–')} | {r.get('spatial', '–')} | {r.get('tier1_mb', '–')} | {r.get('cells_kb', '–')} | {h.get('checked', '–')} |")
    X, Z = R["hochrechnung"], R["zweig"]
    L += ["", "## Hochrechnung auf 18'018 Partien (SCHÄTZUNG)", "",
          f"Quelle der Grössenverteilung: {X['quelle']}, {X['inventar_partien']} Partien, Faktor {X['skala_auf_18018']}. "
          f"Verhältnisschätzer je Grössenquartil (Grenzen {X['quartilgrenzen_bytes']} Byte)"
          + (f"; Quartile ohne Kanarienpartie (globaler Faktor): {X['quartile_ohne_kanarienpartie']}" if X["quartile_ohne_kanarienpartie"] else "") + ".",
          "", f"- Tier 1 für alle: {X['tier1_gb_alle']} GB (bei 25 %+Val: Anteil {X['anteil_tier1_25']}, bei 15 %+Val: {X['anteil_tier1_15']})",
          f"- Zellfakten: {X['zellfakten_gb']} GB, Spawn-Kartenblöcke: {X['spawn_gb']} GB, räumliche Samples: {X['raeumliche_samples_mio']} Mio",
          f"- V1-Ausschnitte (nur Zweig B, Entwurf-Schätzung 0,5–1,3 KB/Sample, nicht gemessen): {Z['ausschnitte_gb']} GB",
          "", "## Zweigempfehlung (Entwurf §7.3)", ""] + [f"- {w}" for w in Z["begruendung"]]
    L += ["", f"**{R['urteil']}**", ""]
    return "\n".join(L)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True)
    ap.add_argument("--list", required=True)
    ap.add_argument("--records-root", required=True)
    ap.add_argument("--free-gb", default="unbekannt")
    ap.add_argument("--inventory")
    ap.add_argument("--min-mask", type=int, default=50, help="Mindestzahl geprüfter Masken-Samples (Tor 3)")
    ap.add_argument("--min-blocks", type=int, default=100, help="Mindestzahl verglichener Kartenblöcke (Tor 1)")
    ap.add_argument("--tier1-timeout", type=int, default=7200)
    ap.add_argument("--md")
    ap.add_argument("--json")
    a = ap.parse_args(argv)
    R = run_gates(a)
    md = markdown(R)
    mdp = a.md or os.path.join(a.out, "gates.md")
    jp = a.json or os.path.join(a.out, "gates.json")
    with open(mdp, "w", encoding="utf-8") as f:
        f.write(md)
    with open(jp, "w", encoding="utf-8") as f:
        json.dump(R, f, ensure_ascii=False, indent=1, default=str)
    print(md)
    return 0 if R["go"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

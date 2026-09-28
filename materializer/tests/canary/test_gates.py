#!/usr/bin/env python3
"""Manipulationstests der Kanarien-Tore: Jedes Tor muss bei seiner Manipulation rot werden.

    python3 test_gates.py <canary_out> <liste.tsv> <records_root> [--gids g1,g2,g3]
                          [--work <ordner>] [--min-mask 10] [--min-blocks 100]

<canary_out> ist die echte Ausgabe eines (Mini-)Kanarienlaufs mit A/ B/ C/. Die Dateien der
gewählten Partien (Standard: die ersten drei der Liste mit Samples in B) werden je Fall frisch
in einen Arbeitsordner kopiert und dort gezielt verändert. Die Quelle bleibt unberührt.

Fälle:
  Grundlage          unverändert: alle sechs Tore müssen grün sein (sonst sagt der Test nichts)
  Block kippen       ein Byte im ersten Kartenblock von B (.maps, Grösse gleich)       → Tor 1
  .chk falsch        eine Ziffer der ersten CRC32 in B/.chk (Länge gleich)             → Tor 2
  Bit löschen        in B/.cells das geforderte Bit an der Zelle jedes res_tile         → Tor 3
                     löschen (neu komprimiert, .ok-Grösse nachgeführt wie ein Schreiber)
  cpu_ms aufblähen   B/.ok cpu_ms.user einer Partie um 30 % der C-Summe erhöhen        → Tor 4
  hash.digest        C/.ok hash.digest + 1                                              → Tor 5
  .ok löschen        C/.ok einer Partie löschen                                         → Tor 6
  leerer Lauf        A/B/C leer: jedes Tor rot wegen Mindestmenge                       → 1–6

Die Tabelle zeigt für jeden Fall alle roten Tore. Folgeeffekte sind erwartet und stehen
dabei (z.B. fehlt nach „.ok löschen“ die Partie auch für Tor 4 und 5, und ein Tor ohne
Daten ist nie grün). Exit 0 nur, wenn die Grundlage grün ist und jeder Fall sein Zieltor rot macht.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import tempfile

import importlib.util

HERE = os.path.dirname(os.path.abspath(__file__))
# gates über den Dateipfad laden: canary/ in sys.path würde mit canary/select.py das
# Standardmodul `select` verdecken.
_spec = importlib.util.spec_from_file_location("gates", os.path.join(HERE, "..", "..", "canary", "gates.py"))
gates = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gates)
sys.path.insert(0, os.path.join(HERE, "..", "..", "py"))
import reader  # noqa: E402

try:
    from compression import zstd as _zstd
    zcomp = _zstd.compress
except ImportError:  # pragma: no cover
    import zstandard as _zs
    zcomp = _zs.ZstdCompressor().compress


def copy_subset(src: str, dst: str, gids: list[str]) -> None:
    for r in "ABC":
        os.makedirs(os.path.join(dst, r), exist_ok=True)
        d = os.path.join(src, r)
        for f in os.listdir(d):
            if any(f.startswith(g + ".") for g in gids) and not f.endswith(".tmp"):
                shutil.copy2(os.path.join(d, f), os.path.join(dst, r, f))
        rj = os.path.join(src, f"{r}.run.json")
        if os.path.exists(rj):
            shutil.copy2(rj, os.path.join(dst, f"{r}.run.json"))


def _ok(d, gid):
    with open(reader.path_of(d, gid, "ok")) as f:
        return json.load(f)


def _write_ok(d, gid, ok):
    with open(reader.path_of(d, gid, "ok"), "w") as f:
        json.dump(ok, f)


def with_samples(w, gids):
    return [g for g in gids if os.path.exists(reader.path_of(os.path.join(w, "B"), g, "maps"))]


# ─────────────────────────── Manipulationen ──────────────────────────────────

def m_block(w, gids):
    # Nur Blöcke, deren (turn, clientID) auch der alte Lauf hat, werden verglichen (Spawn-Samples
    # sind neu). Deshalb den ersten B-Block mit einem Gegenstück in A kippen.
    g = with_samples(w, gids)[0]
    A, B = os.path.join(w, "A"), os.path.join(w, "B")
    old, _ = gates.compare_ref._old(A, g)
    keys = {(m["turn"], m["clientID"]) for m in old}
    meta = reader.Game(B, g).meta()
    i = next(i for i, m in enumerate(meta) if (m["turn"], m["clientID"]) in keys)
    off = sum(4 + ln for ln in gates.block_lengths(reader.path_of(B, g, "maps"))[:i]) + 4 + 20
    p = reader.path_of(B, g, "maps")
    with open(p, "r+b") as f:
        f.seek(off)
        b = f.read(1)
        f.seek(off)
        f.write(bytes([b[0] ^ 0xFF]))
    return f"{g}: Block {i} (Schlüssel auch in A), Byte 20 invertiert"


def m_chk(w, gids):
    for g in with_samples(w, gids):
        p = reader.path_of(os.path.join(w, "B"), g, "chk")
        if not os.path.exists(p):
            continue
        txt = open(p).read()
        m = re.search(r'"crc32"\s*:\s*\[\s*(\d+)', txt)
        if not m:
            continue
        i = m.end(1) - 1
        d = txt[i]
        txt2 = txt[:i] + str((int(d) + 1) % 10) + txt[i + 1:]
        assert len(txt2) == len(txt)
        open(p, "w").write(txt2)
        return f"{g}: letzte Ziffer der ersten crc32 {d} → {(int(d) + 1) % 10}"
    raise RuntimeError("keine .chk in B")


def m_bit(w, gids):
    B = os.path.join(w, "B")
    total = 0
    for g in with_samples(w, gids):
        G = reader.Game(B, g)
        meta = G.meta()
        W, H = G.hdr["W"], G.hdr["H"]
        lb1 = G.ok.get("legal_bit1", True)
        blocks = list(G.cell_blocks())
        if not blocks:
            continue
        raw = {}
        for m in meta:
            c = m.get("cell", -1)
            if c < 0 or m.get("res_kind") != 0 or m.get("res_tile", -1) < 0:
                continue
            bit = gates.required_bit(m["intent"], lb1)
            if bit is None:
                continue
            x, y = m["res_tile"] % W, m["res_tile"] // W
            idx = int(y * 90 / H) * 180 + int(x * 180 / W)
            r = raw.setdefault(c, bytearray(reader.zdec(blocks[c])))
            r[gates.OFF_LEGAL + idx] &= ~(1 << bit) & 0xFF
            total += 1
        for c, r in raw.items():
            blocks[c] = zcomp(bytes(r))
        p = reader.path_of(B, g, "cells")
        with open(p, "wb") as f:
            for b in blocks:
                f.write(len(b).to_bytes(4, "little") + b)
        ok = _ok(B, g)
        ok["files"][f"{g}.cells"] = os.path.getsize(p)   # ein kaputter Schreiber hätte die Grösse richtig vermerkt
        ok["cells_bytes"] = os.path.getsize(p)
        _write_ok(B, g, ok)
    if not total:
        raise RuntimeError("kein Sample mit res_kind 0 und Zelle")
    return f"{total} geforderte Bits gelöscht"


def m_cpu(w, gids):
    C, B = os.path.join(w, "C"), os.path.join(w, "B")
    sc = sum(gates.cpu(_ok(C, g)) or 0 for g in with_samples(w, gids))
    g = with_samples(w, gids)[0]
    ok = _ok(B, g)
    add = int(0.3 * sc) + 1
    ok["cpu_ms"]["user"] += add
    _write_ok(B, g, ok)
    return f"{g}: B cpu_ms.user + {add} ms (30 % der C-Summe {sc} ms)"


def m_digest(w, gids):
    g = with_samples(w, gids)[0]
    C = os.path.join(w, "C")
    ok = _ok(C, g)
    ok["hash"]["digest"] = (ok["hash"]["digest"] + 1) & 0xFFFFFFFF
    _write_ok(C, g, ok)
    return f"{g}: C hash.digest + 1"


def m_delok(w, gids):
    g = with_samples(w, gids)[-1]
    os.remove(reader.path_of(os.path.join(w, "C"), g, "ok"))
    return f"{g}: C/.ok gelöscht"


def m_empty(w, gids):
    for r in "ABC":
        d = os.path.join(w, r)
        for f in os.listdir(d):
            os.remove(os.path.join(d, f))
    return "A/B/C geleert"


CASES = [("Grundlage (unverändert)", None, set()),
         ("Block kippen", m_block, {1}),
         (".chk falsch", m_chk, {2}),
         ("Bit löschen", m_bit, {3}),
         ("cpu_ms aufblähen", m_cpu, {4}),
         ("hash.digest ändern", m_digest, {5}),
         (".ok löschen", m_delok, {6}),
         ("leerer Lauf", m_empty, {1, 2, 3, 4, 5, 6})]


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("list")
    ap.add_argument("records_root")
    ap.add_argument("--gids")
    ap.add_argument("--work")
    ap.add_argument("--min-mask", type=int, default=10)
    ap.add_argument("--min-blocks", type=int, default=100)
    a = ap.parse_args(argv)
    rows = gates.load_list(a.list)
    if a.gids:
        gids = a.gids.split(",")
    else:
        gids = [r["gid"] for r in rows if os.path.exists(reader.path_of(os.path.join(a.out, "B"), r["gid"], "maps"))][:3]
    sub = [r for r in rows if r["gid"] in gids]
    work = tempfile.mkdtemp(prefix="gates-", dir=a.work)
    listp = os.path.join(work, "liste.tsv")
    with open(listp, "w") as f:
        f.write("#relpath\tgid\tcommit8\tsize_bytes\tmap\tmode\n")
        for r in sub:
            f.write(f"{r['rel']}\t{r['gid']}\t{r['commit']}\t{r['size']}\t{r['map']}\t{r['mode']}\n")
    print(f"Partien: {', '.join(gids)}; Mindestmengen: Tor 1 {a.min_blocks} Blöcke, Tor 3 {a.min_mask} Samples\n")
    lines = ["| Fall | Veränderung | Zieltor | Zieltor rot | rote Tore | Grund Zieltor |", "|---|---|---|---|---|---|"]
    all_ok = True
    for name, fn, target in CASES:
        w = os.path.join(work, re.sub(r"\W+", "_", name))
        copy_subset(a.out, w, gids)
        what = fn(w, gids) if fn else "–"
        ns = argparse.Namespace(out=w, list=listp, records_root=a.records_root, free_gb="unbekannt", inventory=None,
                                min_mask=a.min_mask, min_blocks=a.min_blocks, tier1_timeout=3600)
        R = gates.run_gates(ns)
        red = {g["tor"] for g in R["tore"] if not g["gruen"]}
        if target:
            hit = target <= red
            why = "; ".join((g["probleme"] or ["?"])[0][:110] for g in R["tore"] if g["tor"] in target and not g["gruen"])
        else:
            hit = not red
            why = "; ".join(f"Tor {g['tor']}: {(g['probleme'] or ['?'])[0][:110]}" for g in R["tore"] if not g["gruen"]) or "alle grün"
        all_ok &= hit
        lines.append(f"| {name} | {what} | {', '.join(map(str, sorted(target))) or '–'} | "
                     f"{('ja' if hit else 'NEIN') if target else ('grün' if hit else 'NICHT grün')} | "
                     f"{', '.join(map(str, sorted(red))) or 'keins'} | {why} |")
    print("\n".join(lines))
    print(f"\nArbeitsordner: {work}")
    print("ERGEBNIS:", "alle Fälle wie erwartet" if all_ok else "MINDESTENS EIN FALL NICHT WIE ERWARTET")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

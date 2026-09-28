#!/usr/bin/env python3
"""Messung statt Augenmass: kodiert die echten Delta-Einträge und Keyframes einer
own.zst in alternativen Layouts neu und vergleicht die zstd-3-Grössen je Block.

    python enc_experiment.py <dir> <gid> [<dir> <gid> ...]

Varianten (nur Zustandswechsel, Terrain bleibt aussen vor):
  datei       tatsächliche Blockgrösse in der Datei (inkl. Terrain und 4 Byte Prüfsumme)
  ref_planes  je Eintrag nach Ref sortiert, Lücke u32 + Wert u16 als Byte-Planes (Format v0)
  ref_varint  nach Ref sortiert, Lücke varint + Wert u16 plain
  grp_varint  nach (Wert, Ref) gruppiert, Lücken varint (gewähltes Format)
  grp_planes  gruppiert, Lücken u32 als Byte-Planes
Keyframes: plain u16 (gewählt) gegen Byte-Planes.
"""
import os
import struct
import sys
from array import array
from itertools import accumulate

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../py"))
import tier1  # noqa: E402
from compression import zstd  # noqa: E402

Z = lambda b: len(zstd.compress(bytes(b), level=3))  # noqa: E731


def planes(a, w):
    raw = a.tobytes()
    return b"".join(raw[k::w] for k in range(w))


def varint(vals):
    out = bytearray()
    for v in vals:
        while v >= 0x80:
            out.append((v & 0x7F) | 0x80)
            v >>= 7
        out.append(v)
    return out


def gaps_of(sorted_refs):
    prev, out = 0, []
    for r in sorted_refs:
        out.append(r - prev)
        prev = r
    return out


def encode(entries, how):
    counts = array("I", [len(e[1]) for e in entries]).tobytes()
    if how.startswith("ref"):
        g, v = [], array("H")
        for _, refs, vals, *_ in entries:
            pr = sorted(zip(refs, vals))
            g += gaps_of([r for r, _ in pr])
            v.extend(x for _, x in pr)
        if how == "ref_planes":
            return counts + planes(array("I", g), 4) + planes(v, 2)
        return counts + bytes(varint(g)) + v.tobytes()
    ng, gc, gv, g = [], [], array("H"), []
    for _, refs, vals, *_ in entries:
        pr = sorted(zip(vals, refs))
        k = 0
        n0 = len(gc)
        while k < len(pr):
            v0 = pr[k][0]
            c0 = k
            rr = []
            while k < len(pr) and pr[k][0] == v0:
                rr.append(pr[k][1])
                k += 1
            gv.append(v0)
            gc.append(k - c0)
            g += gaps_of(rr)
        ng.append(len(gc) - n0)
    head = array("I", ng).tobytes()
    gb = bytes(varint(g)) if how == "grp_varint" else planes(array("I", g), 4)
    return head + bytes(varint(gc)) + planes(gv, 2) + gb


VARS = ("ref_planes", "ref_varint", "grp_varint", "grp_planes")


def main(args):
    tot = dict.fromkeys(("datei", *VARS, "key_plain", "key_planes", "changes"), 0)
    for d, gid in zip(args[::2], args[1::2]):
        own = tier1.Own(os.path.join(d, f"{gid}.own.zst"))
        s = dict.fromkeys(tot, 0)
        for b in own.deltas:
            e = own.block_entries(b)
            s["changes"] += sum(len(x[1]) for x in e)
            s["datei"] += b.zlen
            for how in VARS:
                s[how] += Z(encode(e, how))
        for b in own.keys:
            st, _ = own._key(b)
            s["key_plain"] += Z(st.tobytes())
            s["key_planes"] += Z(planes(st, 2))
        for k in tot:
            tot[k] += s[k]
        report(gid, s)
    report("SUMME", tot)


def report(name, s):
    n = max(1, s["changes"])
    v = "  ".join(f"{k} {s[k]/n:.3f}" for k in ("datei", *VARS))
    print(f"{name}: Wechsel {s['changes']}  B/Wechsel: {v}  | Keyframes plain {s['key_plain']} planes {s['key_planes']}")


if __name__ == "__main__":
    main(sys.argv[1:])

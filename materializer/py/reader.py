"""Leser für die Ausgabe des Materialisierers v2 (DESIGN.md §5).

Grundlage für dataset.py und den Kanarienlauf. Liest eine Partie: hdr.json,
Metazeilen, Kartenblöcke, Zellblöcke, und prüft sie.

Regel (DESIGN §5 "Leser"): gelesen werden nur Partien, deren .ok die
Grössenprüfung besteht. Eine vorhandene .meta.zst allein heisst nichts.

    python3 reader.py check <outdir> [gid ...]   # alle Partien im Ordner, wenn keine gid
    python3 reader.py show  <outdir> <gid>       # Kopf, Zählungen, erste Metazeile

zstd: Python ≥ 3.14 bringt compression.zstd mit (arch: 3.14.7), sonst das Paket
zstandard. node:zlib schreibt Frames, die beide lesen.
"""
from __future__ import annotations

import hashlib
import json
import os
import struct
import sys

try:  # Python ≥ 3.14
    from compression import zstd as _zstd

    def zdec(b: bytes) -> bytes:
        return _zstd.decompress(b)
except ImportError:  # pragma: no cover - Rückfall für ältere Pythons
    import zstandard as _zs

    def zdec(b: bytes) -> bytes:
        # decompressobj statt decompress: Frames ohne Inhaltsgrösse im Kopf
        return _zs.ZstdDecompressor().decompressobj().decompress(b)

MAP_CHANNELS, GH, GW = 18, 90, 180
MAP_BYTES = MAP_CHANNELS * GH * GW
CELL_BYTES = GH * GW * 4          # owner u16 ‖ own_frac u8 ‖ legal u8
REQUIRED_META = ("turn", "clientID", "intent", "w", "tick", "sid", "kind", "w_tick")


def sha1_int(gid: str, lo: int = 0, hi: int = 8) -> int:
    """Hex-Zeichen lo..hi von sha1(gid) als Zahl (DESIGN §5.3/§7)."""
    return int(hashlib.sha1(gid.encode("ascii")).hexdigest()[lo:hi], 16)


def is_val(gid: str) -> bool:
    """Val-Partie: sha1(gid) % 25 == 0, erste 8 Hex-Zeichen big-endian (DESIGN §5.3)."""
    return sha1_int(gid) % 25 == 0


def path_of(outdir: str, gid: str, name: str) -> str:
    return os.path.join(outdir, f"{gid}.{name}")


def _json(p: str):
    try:
        with open(p, "rb") as f:
            return json.loads(f.read())
    except (OSError, ValueError):
        return None


def ok_problems(outdir: str, gid: str) -> list[str]:
    """Grössenprüfung der .ok wie io.ts okValid. Leere Liste = bestanden."""
    ok = _json(path_of(outdir, gid, "ok"))
    if ok is None:
        return ["keine lesbare .ok"]
    if ok.get("format") != 2:
        return [f".ok format {ok.get('format')!r} statt 2"]
    files = ok.get("files")
    if not isinstance(files, dict) or not isinstance(ok.get("samples"), int):
        return [".ok ohne files/samples"]
    need = ["hdr.json", "maps", "meta.zst"] if ok["samples"] > 0 else ["hdr.json"]
    probs = [f"{gid}.{n} nicht in .ok gelistet" for n in need if f"{gid}.{n}" not in files]
    for name, size in files.items():
        p = os.path.join(outdir, name)
        if not os.path.exists(p):
            probs.append(f"{name} fehlt")
        elif os.path.getsize(p) != size:
            probs.append(f"{name}: {os.path.getsize(p)} Byte statt {size}")
    return probs


def is_done(outdir: str, gid: str, retry_err: bool = False) -> bool:
    """Wie io.ts isDone."""
    if (_json(path_of(outdir, gid, "none")) or {}).get("format") == 2:
        return True
    if not ok_problems(outdir, gid):
        return True
    return not retry_err and os.path.exists(path_of(outdir, gid, "err"))


def iter_blocks(path: str):
    """Rohe Blöcke [u32 LE Länge][Block]. Wirft bei abgeschnittener Datei."""
    with open(path, "rb") as f:
        data = f.read()
    off, n = 0, len(data)
    while off < n:
        if off + 4 > n:
            raise ValueError(f"{path}: Längenfeld abgeschnitten bei {off}")
        (ln,) = struct.unpack_from("<I", data, off)
        off += 4
        if off + ln > n:
            raise ValueError(f"{path}: Block abgeschnitten bei {off} (Länge {ln})")
        yield data[off:off + ln]
        off += ln


class Game:
    """Eine fertige Partie. Konstruktor wirft, wenn die .ok die Grössenprüfung nicht besteht."""

    def __init__(self, outdir: str, gid: str):
        probs = ok_problems(outdir, gid)
        if probs:
            raise ValueError(f"{gid}: nicht fertig: {'; '.join(probs)}")
        self.outdir, self.gid = outdir, gid
        self.ok = _json(path_of(outdir, gid, "ok"))
        self.hdr = _json(path_of(outdir, gid, "hdr.json"))
        self._meta = None

    @property
    def n(self) -> int:
        return self.ok["samples"]

    def meta(self) -> list[dict]:
        if self._meta is None:
            if self.n == 0:
                self._meta = []
            else:
                with open(path_of(self.outdir, self.gid, "meta.zst"), "rb") as f:
                    txt = zdec(f.read()).decode("utf-8")
                self._meta = [json.loads(x) for x in txt.split("\n")] if txt else []
        return self._meta

    def map_blocks(self):
        """Rohe zstd-Kartenblöcke, 1:1 zu meta()."""
        if self.n == 0:
            return iter(())
        return iter_blocks(path_of(self.outdir, self.gid, "maps"))

    def maps(self):
        """Karten als bytes u8[18·90·180], Reihenfolge Kanal, Zeile, Spalte."""
        for b in self.map_blocks():
            yield zdec(b)

    def cell_blocks(self):
        p = path_of(self.outdir, self.gid, "cells")
        return iter_blocks(p) if os.path.exists(p) else iter(())

    def cells(self):
        """Zellblöcke roh: (owner_major u16 LE[16200], own_frac u8[16200], legal u8[16200])."""
        for b in self.cell_blocks():
            raw = zdec(b)
            yield raw[:32400], raw[32400:48600], raw[48600:]

    def check(self, decode: bool = True) -> list[str]:
        """Gleichschritt und Form. Leere Liste = in Ordnung."""
        probs: list[str] = []
        meta = self.meta()
        if len(meta) != self.n:
            probs.append(f"{len(meta)} Metazeilen, .ok sagt {self.n}")
        if self.hdr is None or self.hdr.get("format") != 2 or self.hdr.get("gid") != self.gid:
            probs.append("hdr.json fehlt oder passt nicht")
        try:
            nmaps = 0
            for b in self.map_blocks():
                if decode and len(zdec(b)) != MAP_BYTES:
                    probs.append(f"Kartenblock {nmaps}: falsche Grösse")
                nmaps += 1
        except ValueError as e:
            probs.append(str(e))
            nmaps = -1
        if nmaps != len(meta):
            probs.append(f"maps-Blöcke {nmaps} ≠ Metazeilen {len(meta)}")
        cell_rows = [m.get("cell", -1) for m in meta if m.get("cell", -1) >= 0]
        if cell_rows != list(range(len(cell_rows))):
            probs.append("cell-Indizes nicht 0..n-1 in Zeilenfolge")
        try:
            ncells = 0
            for b in self.cell_blocks():
                if decode and len(zdec(b)) != CELL_BYTES:
                    probs.append(f"Zellblock {ncells}: falsche Grösse")
                ncells += 1
        except ValueError as e:
            probs.append(str(e))
            ncells = -1
        if ncells != len(cell_rows):
            probs.append(f"cells-Blöcke {ncells} ≠ Zeilen mit cell≥0 {len(cell_rows)}")
        missing = {k for m in meta for k in REQUIRED_META if k not in m}
        if missing:
            probs.append(f"Metafelder fehlen: {sorted(missing)}")
        ticks = [m["tick"] for m in meta if "tick" in m]
        if ticks != sorted(ticks):
            probs.append("Samples nicht in Tick-Reihenfolge")
        vu = self.ok.get("valid_until")
        if vu is not None and ticks and ticks[-1] > vu:
            probs.append(f"Sample nach valid_until {vu}")
        return probs


def zusatz_lesen(pfad: str):
    """Zusatzdatei <gid>.zusatz.zst (zusatz/src/lauf.ts): (Kopf, Gegnerblock, globaler Block).

    Aufbau: zstd( Kopfzeile JSON + "\\n" + [samples][opp][len(felder_opp)]
                                         + [samples][len(felder_global)] ),
    dtype float16 oder float32 laut Kopf. Der Kopf nennt Format, gid, samples, opp und
    beide Namenslisten in ihrer Reihenfolge; der Aufrufer prüft sie
    (trainer/zusatz_felder.passt). Fehlt die Datei: (None, None, None).
    numpy wird erst hier importiert, damit reader.py ohne numpy nutzbar bleibt."""
    import numpy as np

    if not os.path.exists(pfad):
        return None, None, None, None, None     # so viele Werte wie bei vorhandener Datei
    with open(pfad, "rb") as f:
        roh = zdec(f.read())
    i = roh.index(b"\n")
    kopf = json.loads(roh[:i])
    dt = np.dtype({"float16": "<f2", "float32": "<f4"}[kopf.get("dtype", "float32")])
    n, opp = int(kopf["samples"]), int(kopf["opp"])
    off = i + 1

    def block(*form):
        nonlocal off
        anz = 1
        for x in form:
            anz *= x
        a = (np.frombuffer(roh, dtype=dt, count=n * anz, offset=off).reshape(n, *form) if anz
             else np.zeros((n, *form), np.float32))
        off += n * anz * dt.itemsize
        return a

    o = block(opp, len(kopf.get("felder_opp") or kopf.get("felder") or []))
    g = block(len(kopf.get("felder_global") or []))
    e = block(int(kopf.get("einheit", 0)), len(kopf.get("felder_einheit") or []))
    a = block(int(kopf.get("angriff", 0)), len(kopf.get("felder_angriff") or []))
    return kopf, o, g, e, a


def list_gids(outdir: str) -> list[str]:
    """Alle gids mit irgendeiner Marke im Ordner."""
    out = set()
    for f in os.listdir(outdir):
        for suf in (".ok", ".none", ".err"):
            if f.endswith(suf) and not f.endswith(".tmp"):
                out.add(f[: -len(suf)])
    return sorted(out)


def check_dir(outdir: str, gids: list[str] | None = None, decode: bool = True) -> dict:
    """Prüft Partien. Ergebnis je gid: 'ok', 'none', 'err' oder Liste von Problemen."""
    gids = gids or list_gids(outdir)
    res = {}
    for gid in gids:
        if (_json(path_of(outdir, gid, "none")) or {}).get("format") == 2:
            res[gid] = "none"
            continue
        if os.path.exists(path_of(outdir, gid, "ok")):
            try:
                probs = Game(outdir, gid).check(decode)
            except ValueError as e:
                probs = [str(e)]
            res[gid] = probs or "ok"
            continue
        res[gid] = "err" if os.path.exists(path_of(outdir, gid, "err")) else ["keine Marke"]
    return res


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[0] not in ("check", "show"):
        print(__doc__)
        return 2
    cmd, outdir, gids = argv[0], argv[1], argv[2:]
    if cmd == "show":
        g = Game(outdir, gids[0])
        print(json.dumps({k: g.hdr[k] for k in ("gid", "map", "W", "H", "spawnEndTick", "tier1")}))
        print(json.dumps({k: g.ok.get(k) for k in ("samples", "by_kind", "hash", "desync", "tick_error")}))
        m = g.meta()
        if m:
            print(json.dumps({k: v for k, v in m[0].items() if k not in ("own", "opps", "oppIds", "cfg")}))
        return 0
    res = check_dir(outdir, gids or None)
    if not res:
        print(f"keine Partie in {outdir}")  # kein Erfolg aus leerer Menge
        return 2
    bad = 0
    for gid, r in res.items():
        if isinstance(r, list):
            bad += 1
            print(f"{gid}: FEHLER {'; '.join(r)}")
        else:
            print(f"{gid}: {r}")
    print(f"{len(res)} geprüft, {bad} mit Problemen")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

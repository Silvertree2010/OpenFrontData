#!/usr/bin/env python3
"""
Tier-1-Rekonstruktor (Materialisierer v2, DESIGN.md §5.3). Nur Python-Stdlib,
Python >= 3.14 (compression.zstd). Keine Engine nötig.

    python tier1.py verify <dir> <gid> [--ref <gid>.t1ref.json]   Exit 0 nur bei 100 % Treffern
    python tier1.py state  <dir> <gid> <tick> [--out datei.u16]   Zustand in Tick t, CRC32
    python tier1.py units  <dir> <gid> [--jsonl]                   Ereignislisten der Einheiten

Als Bibliothek:
    own = Own(f"{dir}/{gid}.own.zst")
    st = own.state(t)                       # array('H'), W*H, Zustand in Tick t
    st = own.state_for_sample(meta_tick)    # dasselbe (Sample mit tick t sah Tick t)
    for t, st, terr in own.replay_ticks(): ...   # sequentiell, billig
    ev = Units(f"{dir}/{gid}.units.zst").events()

=====================================================================================
BYTE-LAYOUT (verbindlich; der Schreiber ist materializer/src/tier1.ts, T1_VERSION = 1)
=====================================================================================

Tick-Konvention (DESIGN §2): „Tick t“ ist der Zustand mit game.ticks() == t, also vor
executeNextTick für Zug t. Tick 0 ist der Anfangszustand, der letzte Tick L ist
game.ticks() nach dem letzten ausgeführten Zug. Die Updates aus Zug t-1 ergeben Tick t
und stehen im Log unter t.

Alle Zahlen Little Endian.

Container (own.zst und units.zst gleich)
----------------------------------------
Die Datei ist eine gültige Multi-Frame-zstd-Datei (`zstd -t` prüft sie). Blockköpfe
stehen in zstd-Skippable-Frames (Magic 0x184D2A50..52, danach u32 Nutzlänge):

  Dateikopf   u32 0x184D2A50, u32 32,
              char[4] "OFT1", char[4] Art ("OWN\\0" | "UNIT"), u32 version (1),
              u32 W, u32 H, i32 S (spawnEndTick wie übergeben, >= 0),
              u32 BLOCK_TICKS (512), u32 KEY_EVERY (4096)
  Block       u32 0x184D2A51, u32 16,
              u32 zlen, i32 t0, u32 nt, u32 flags
              danach genau zlen Byte: ein zstd-Frame (Level 3) mit der Nutzlast
  Abschluss   u32 0x184D2A52, u32 24,
              i32 L (letzter Tick), u32 n_blocks, u64 n_records (own: Zustandswechsel,
              units: Zeilen), u32 vflags (Bit 0: valid_until gesetzt), i32 valid_until

Fehlt der Abschluss, ist die Datei unvollständig (Abbruch beim Schreiben). Ist
valid_until gesetzt (Desync oder Tick-Fehler, DESIGN §2.7), gelten nur Ticks
0..valid_until; dieser Leser liefert danach weder Zustand noch Einheiten-Zeilen.

Byte-Planes: „planesK(n)“ ist ein Array aus n Zahlen zu K Byte, abgelegt als K Ebenen
zu je n Byte: erst Byte 0 (niederwertigstes) aller Zahlen, dann Byte 1 usw. Das hilft
zstd, die hohen Ebenen sind fast nur Nullen.

own.zst
-------
Kachelzustand: u16 je Kachel, ref = y*W + x. Bits 0-11 Besitzer-smallID (0 = niemand),
Bit 13 Fallout, Bit 14 Verteidigungsbonus (headless nie gesetzt), Bits 12/15 frei.
Terrain-Byte (nur mit Water-Nukes veränderlich): Bit 7 Land, 6 Ufer, 5 Ozean, 0-4 Magnitude.

Jeder zstd-Frame trägt die zstd-Inhaltsprüfsumme (XXH64, beim Dekomprimieren geprüft).

Blocktyp Keyframe (flags & 1): t0 = K, nt = 0. Nutzlast = Engine-Wahrheit in Tick K:
  u16[W*H]      state (am Stück, Little Endian)
  u32           nk
  planes4(nk)   ref aufsteigend: alle Kacheln, deren Terrain bis K je gewechselt hat
  u8[nk]        Terrain am Partiebeginn
  u8[nk]        Terrain in Tick K
Keyframes: K = S + 4096 j (S = spawnEndTick). Zusätzlich K = 0, falls der Anfangszustand
nicht leer ist oder S == 0. Ohne Keyframe bei 0 ist Tick 0 „alles 0, kein Terrainwechsel“.

Blocktyp Delta (flags == 0): Einträge für die Ticks t0 .. t0+nt-1, lückenlos ab Tick 1
bis L. Eintrag t = Übergang von Tick t-1 zu Tick t. Blockanfänge: Tick 1, danach alle
t mit t % 512 == 0 (solange t < S), dann t == S und S + 512 j. Nutzlast:
  u32[nt]       nG[i]  Zahl der Wertgruppen im Eintrag t0+i
  u32[nt]       nT[i]  Zahl der Terrainwechsel im Eintrag t0+i
  u32           LC     Bytes des Stroms gcnt
  u32           LG     Bytes des Stroms gap
  varint(ΣnG)   gcnt   Zahl der Wechsel je Gruppe (LC Byte)
  planes2(ΣnG)  gval   neuer u16-Zustand der Gruppe (absolut)
  varint(Σgcnt) gap    Refs der Gruppe aufsteigend, gap = ref - vorheriger ref, vorheriger = 0
                       am Anfang jeder Gruppe (erster gap = ref) (LG Byte)
  planes4(ΣnT)  tgap   Terrainwechsel-Refs je Eintrag aufsteigend, gap wie oben, aber
                       vorheriger = 0 am Anfang jedes Eintrags
  u8[ΣnT]       told   Terrain vorher
  u8[ΣnT]       tnew   Terrain nachher
Gruppen: je Eintrag alle geänderten Kacheln mit gleichem neuen Wert, aufsteigend nach
Wert. varint = LEB128 (7 Bit je Byte, niederwertige zuerst, Bit 7 = es folgt ein Byte).
Nur Kacheln, deren Wert sich im Tick gegenüber dem Spiegel wirklich geändert hat
(mehrfache Updates derselben Kachel sind zusammengefasst). Die Werte sind absolut:
Anwenden ist idempotent, ein Eintrag darf also doppelt angewendet werden.

Dateireihenfolge: Delta-Blöcke aufsteigend; Keyframe K steht nach dem Delta-Block, der
mit Eintrag K-1 endet, und vor dem, der Eintrag K enthält.

units.zst
---------
Blöcke nach Zeilen-Tick: t0 = 512 j, nt = 512, alle Zeilen mit t0 <= tick < t0+512;
leere Blöcke fehlen. Nutzlast:
  u32 n, dann Spalten zu je n Einträgen:
  planes4 tick | planes4 id | u8 mask | u8 code | u8 flags | planes2 owner | planes2 level
  | planes4 pos | planes4 target
Jede Zeile ist eine Änderung einer Einheit, sichtbar ab Tick `tick`. Die Spalten tragen den
vollen Snapshot NACH der Änderung, `mask` sagt, was sich geändert hat:
  1 CREATE, 2 OWNER, 4 LEVEL, 8 CONSTR (Bau-Status), 16 POS, 32 TARGET, 64 END
flags Bit 0 = im Bau. pos/target = 0xFFFFFFFF heisst „keins“.
code: 1 City, 2 Port, 3 Factory, 4 Defense Post, 5 Missile Silo, 6 SAM Launcher,
      7 Warship, 8 Transport, 9 Atom Bomb, 10 Hydrogen Bomb, 11 MIRV, 12 MIRV Warhead
Je Klasse:
  Gebäude (1-6)    pos = Kachel; Zeilen bei Entstehen, Besitzer, Level, Bau fertig, Ende
  Kriegsschiff (7) pos in jedem Tick mit tick % 8 == 0 per unit.tile(), nur wenn geändert;
                   target = warshipState.patrolTile (aus den Unit-Updates)
  Transport (8)    pos wie Kriegsschiff; target = targetTile (beim Entstehen die
                   Landekachel, danach im 8-Tick-Takt nachgeführt)
  Nukes (9-12)     pos = Startkachel (Silo) bei CREATE, Endkachel bei END; target = Ziel
  END-Zeilen tragen die letzte echte Position (aus dem Lösch-Update).

.chk (JSON)
-----------
{ "version", "last_tick" (L), "valid_until" (null | t), "chosen": [vorab gewählte Ticks],
  "dropped": n, "ticks": [...], "crc32": [...], "units_crc32": [...], "units_n": [...],
  "terrain_crc32": [...], "terrain_n": [...] }
ticks = die gewählten Ticks (ohne die nach valid_until) plus L, falls L gültig ist.
Alles Engine-Wahrheit in Tick t:
  crc32          zlib-CRC32 über den u16-Zustandspuffer (W*H*2 Byte, Little Endian)
  terrain_crc32  CRC32 über (u32 ref, u8 terrain) aller Kacheln, deren Terrain vom
                 Partiebeginn abweicht, nach ref sortiert
  units_crc32    CRC32 über je 18 Byte `<IBHHBII` (id, code, owner, level, im Bau, pos,
                 target) aller aktiven verfolgten Einheiten, nach id sortiert. pos: Gebäude
                 immer, Schiffe nur an Ticks mit t % 8 == 0, sonst 0xFFFFFFFF. target:
                 Nukes Ziel, Kriegsschiff patrolTile, Transport nur an t % 8 == 0.
"""
from __future__ import annotations

import json
import os
import struct
import sys
import zlib
from array import array
from bisect import bisect_right
from itertools import accumulate

try:
    from compression import zstd as _zstd  # Python >= 3.14
except ImportError as e:  # pragma: no cover
    raise SystemExit("tier1.py braucht Python >= 3.14 (compression.zstd)") from e

MAGIC_FILE, MAGIC_BLOCK, MAGIC_END = 0x184D2A50, 0x184D2A51, 0x184D2A52
NONE = 0xFFFFFFFF
SHIP_SAMPLE_EVERY = 8
EV_CREATE, EV_OWNER, EV_LEVEL, EV_CONSTR, EV_POS, EV_TARGET, EV_END = 1, 2, 4, 8, 16, 32, 64
EV_NAMES = [(EV_CREATE, "create"), (EV_OWNER, "owner"), (EV_LEVEL, "level"), (EV_CONSTR, "built"),
            (EV_POS, "pos"), (EV_TARGET, "target"), (EV_END, "end")]
UNIT_NAMES = {1: "City", 2: "Port", 3: "Factory", 4: "Defense Post", 5: "Missile Silo",
              6: "SAM Launcher", 7: "Warship", 8: "Transport", 9: "Atom Bomb",
              10: "Hydrogen Bomb", 11: "MIRV", 12: "MIRV Warhead"}
C_WARSHIP, C_TRANSPORT = 7, 8

assert array("H").itemsize == 2 and array("I").itemsize == 4
_LE = sys.byteorder == "little"


def is_building(c): return 1 <= c <= 6
def is_ship(c): return c in (C_WARSHIP, C_TRANSPORT)


class NotValid(ValueError):
    """Tick liegt nach valid_until oder ausserhalb 0..L."""


# ── Auswahl (identisch zu tier1.ts) ──────────────────────────────────────────

def _sha1hex(gid: str) -> str:
    import hashlib
    return hashlib.sha1(gid.encode("ascii")).hexdigest()


def gid_hash(gid: str) -> int:
    return int(_sha1hex(gid)[:8], 16)


def tier1_selected(gid: str, pct: int) -> bool:
    h = gid_hash(gid)
    return h % 100 < pct or h % 25 == 0


def chk_ticks_for(gid: str, candidate_ticks) -> list[int]:
    t = sorted(set(int(x) for x in candidate_ticks))
    n = len(t)
    if n == 0:
        return []
    hx = _sha1hex(gid)
    h1, h2 = int(hx[24:32], 16), int(hx[32:40], 16)
    i1 = h1 % n
    if n == 1:
        return [t[i1]]
    i2 = (i1 + 1 + h2 % (n - 1)) % n
    return sorted([t[i1], t[i2]])


# ── Container ───────────────────────────────────────────────────────────────

def _unplane(buf, off: int, n: int, width: int, code: str):
    """planesK(n) ab off → (array, neues off)."""
    a = array(code)
    if n == 0:
        return a, off
    if width == 1:
        a.frombytes(bytes(buf[off:off + n]))
        return a, off + n
    ba = bytearray(n * width)
    for k in range(width):
        ba[k::width] = buf[off + k * n: off + (k + 1) * n]
    a.frombytes(ba)
    if not _LE:
        a.byteswap()
    return a, off + n * width


def _uvarints(buf, off: int, nbytes: int) -> array:
    """LEB128-Strom (nbytes Byte ab off) → array('I')."""
    out = array("I")
    app = out.append
    x = sh = 0
    for c in memoryview(buf)[off:off + nbytes]:
        if c < 128:
            app(x | (c << sh))
            x = sh = 0
        else:
            x |= (c & 127) << sh
            sh += 7
    if sh:
        raise ValueError("abgeschnittener varint")
    return out


class Block:
    __slots__ = ("off", "zlen", "t0", "nt", "flags")

    def __init__(self, off, zlen, t0, nt, flags):
        self.off, self.zlen, self.t0, self.nt, self.flags = off, zlen, t0, nt, flags


class _Container:
    def __init__(self, path: str, kind: str):
        self.path = path
        self.f = open(path, "rb")
        f = self.f
        m, sz = struct.unpack("<II", f.read(8))
        if m != MAGIC_FILE or sz != 32:
            raise ValueError(f"{path}: kein Tier-1-Kopf")
        tag, k, self.version, self.W, self.H, self.S, self.block_ticks, self.key_every = \
            struct.unpack("<4s4sIIIiII", f.read(32))
        if tag != b"OFT1" or k.rstrip(b"\0").decode() != kind:
            raise ValueError(f"{path}: Art {k!r}, erwartet {kind}")
        if self.version != 1:
            raise ValueError(f"{path}: Version {self.version} unbekannt")
        self.blocks: list[Block] = []
        self.complete = False
        self.last_tick = None
        self.valid_until = None
        self.n_records = None
        while True:
            h = f.read(8)
            if not h:
                break
            if len(h) < 8:
                raise ValueError(f"{path}: abgeschnittener Kopf")
            m, sz = struct.unpack("<II", h)
            if m == MAGIC_BLOCK and sz == 16:
                zlen, t0, nt, flags = struct.unpack("<IiII", f.read(16))
                self.blocks.append(Block(f.tell(), zlen, t0, nt, flags))
                f.seek(zlen, 1)
            elif m == MAGIC_END and sz == 24:
                self.last_tick, nb, lo, hi, vflags, vu = struct.unpack("<iIIIIi", f.read(24))
                self.n_records = lo + (hi << 32)
                self.valid_until = vu if vflags & 1 else None
                if nb != len(self.blocks):
                    raise ValueError(f"{path}: Abschluss nennt {nb} Blöcke, gefunden {len(self.blocks)}")
                self.complete = True
                if f.read(1):
                    raise ValueError(f"{path}: Daten nach dem Abschluss")
                break
            else:
                raise ValueError(f"{path}: unbekannter Frame {m:#x} bei {f.tell() - 8}")
        if self.blocks and self.blocks[-1].off + self.blocks[-1].zlen > os.path.getsize(path):
            raise ValueError(f"{path}: letzter Block abgeschnitten")

    def payload(self, b: Block) -> bytes:
        self.f.seek(b.off)
        return _zstd.decompress(self.f.read(b.zlen))

    def close(self):
        self.f.close()


# ── own.zst ─────────────────────────────────────────────────────────────────

class _Delta:
    __slots__ = ("t0", "nt", "nG", "nT", "gcnt", "gval", "gaps", "tgaps", "told", "tnew")


class Own:
    def __init__(self, path: str):
        self.c = _Container(path, "OWN")
        self.W, self.H, self.S = self.c.W, self.c.H, self.c.S
        self.n = self.W * self.H
        self.keys = sorted((b for b in self.c.blocks if b.flags & 1), key=lambda b: b.t0)
        self.deltas = [b for b in self.c.blocks if not b.flags & 1]
        self._key_t = [b.t0 for b in self.keys]
        self._delta_t0 = [b.t0 for b in self.deltas]
        prev = 1
        for b in self.deltas:  # lückenlos ab 1?
            if b.t0 != prev:
                raise ValueError(f"own: Lücke vor Eintrag {b.t0} (erwartet {prev})")
            prev = b.t0 + b.nt
        self.last_tick = prev - 1
        self.valid_until = self.c.valid_until
        self.max_tick = self.last_tick if self.valid_until is None else min(self.last_tick, self.valid_until)

    @property
    def complete(self):
        return self.c.complete and self.c.last_tick == self.last_tick

    def _check(self, t):
        if not 0 <= t <= self.max_tick:
            why = f"nach valid_until {self.valid_until}" if self.valid_until is not None and t > self.valid_until else "ausserhalb"
            raise NotValid(f"Tick {t} {why} (gültig 0..{self.max_tick})")

    def _key(self, b: Block):
        p = self.c.payload(b)
        st = array("H")
        st.frombytes(p[:2 * self.n])
        if not _LE:
            st.byteswap()
        o = 2 * self.n
        (nk,) = struct.unpack_from("<I", p, o)
        o += 4
        refs, o = _unplane(p, o, nk, 4, "I")
        init, cur = p[o:o + nk], p[o + nk:o + 2 * nk]
        if o + 2 * nk != len(p):
            raise ValueError(f"own: Keyframe {b.t0}: Länge stimmt nicht")
        return st, {r: [init[i], cur[i]] for i, r in enumerate(refs)}

    def _delta(self, b: Block) -> _Delta:
        p = self.c.payload(b)
        d = _Delta()
        d.t0, d.nt = b.t0, b.nt
        m = b.nt
        d.nG = array("I"); d.nG.frombytes(p[0:4 * m])
        d.nT = array("I"); d.nT.frombytes(p[4 * m:8 * m])
        if not _LE:
            d.nG.byteswap(); d.nT.byteswap()
        G, NT = sum(d.nG), sum(d.nT)
        LC, LG = struct.unpack_from("<II", p, 8 * m)
        o = 8 * m + 8
        d.gcnt = _uvarints(p, o, LC); o += LC
        if len(d.gcnt) != G:
            raise ValueError(f"own: Block {b.t0}: {len(d.gcnt)} Gruppengrössen, erwartet {G}")
        d.gval, o = _unplane(p, o, G, 2, "H")
        d.gaps = _uvarints(p, o, LG); o += LG
        if len(d.gaps) != sum(d.gcnt):
            raise ValueError(f"own: Block {b.t0}: {len(d.gaps)} Lücken, erwartet {sum(d.gcnt)}")
        d.tgaps, o = _unplane(p, o, NT, 4, "I")
        d.told = p[o:o + NT]; o += NT
        d.tnew = p[o:o + NT]; o += NT
        if o != len(p):
            raise ValueError(f"own: Block {b.t0}: {len(p) - o} Byte übrig")
        return d

    @staticmethod
    def _entries(st, terr, d: _Delta, t_from: int, t_to: int):
        """Einträge t_from..t_to aus d anwenden; liefert nach jedem angewendeten Eintrag t."""
        gi = i = j = 0
        gcnt, gval, gaps = d.gcnt, d.gval, d.gaps
        for k in range(d.nt):
            t = d.t0 + k
            if t > t_to:
                return
            ng, m = d.nG[k], d.nT[k]
            if t >= t_from:
                for g in range(gi, gi + ng):
                    c = gcnt[g]
                    v = gval[g]
                    for r in accumulate(gaps[i:i + c]):
                        st[r] = v
                    i += c
                if m:
                    for q, r in enumerate(accumulate(d.tgaps[j:j + m])):
                        e = terr.get(r)
                        if e is None:
                            terr[r] = [d.told[j + q], d.tnew[j + q]]
                        else:
                            e[1] = d.tnew[j + q]
                yield t
            else:
                i += sum(gcnt[gi:gi + ng])
            gi += ng
            j += m

    def block_entries(self, b: Block):
        """Einträge eines Delta-Blocks als Liste (t, refs, vals, trefs, told, tnew);
        refs/vals in Dateireihenfolge (Gruppen nach Wert, darin Refs aufsteigend)."""
        d = self._delta(b)
        out = []
        gi = i = j = 0
        for k in range(d.nt):
            refs, vals = [], []
            for g in range(gi, gi + d.nG[k]):
                c = d.gcnt[g]
                rr = list(accumulate(d.gaps[i:i + c]))
                refs += rr
                vals += [d.gval[g]] * c
                i += c
            gi += d.nG[k]
            m = d.nT[k]
            out.append((d.t0 + k, refs, vals, list(accumulate(d.tgaps[j:j + m])),
                        bytes(d.told[j:j + m]), bytes(d.tnew[j:j + m])))
            j += m
        return out

    def initial(self):
        """Tick 0: Keyframe 0, falls vorhanden, sonst leer."""
        if self.keys and self.keys[0].t0 == 0:
            return self._key(self.keys[0])
        return array("H", bytes(2 * self.n)), {}

    def replay_ticks(self, want=None):
        """Generator (t, state, terrain) für jeden gültigen Tick 0..max_tick (oder nur t in want).
        state ist das laufende array('H'), terrain {ref: [anfang, jetzt]}; nicht verändern."""
        st, terr = self.initial()
        if want is None or 0 in want:
            yield 0, st, terr
        for b in self.deltas:
            if b.t0 > self.max_tick:
                break
            for t in self._entries(st, terr, self._delta(b), 1, self.max_tick):
                if want is None or t in want:
                    yield t, st, terr

    def state(self, t: int, with_terrain: bool = False):
        """Zustand in Tick t (random access über den letzten Keyframe <= t)."""
        self._check(t)
        ki = bisect_right(self._key_t, t) - 1
        if ki >= 0:
            st, terr = self._key(self.keys[ki])
            start = self.keys[ki].t0
        else:
            st, terr = array("H", bytes(2 * self.n)), {}
            start = 0
        if t > start:
            di = max(0, bisect_right(self._delta_t0, start + 1) - 1)
            for b in self.deltas[di:]:
                if b.t0 > t:
                    break
                for _ in self._entries(st, terr, self._delta(b), start + 1, t):
                    pass
        return (st, terr) if with_terrain else st

    def state_for_sample(self, meta_tick: int):
        """Zustand, den ein Sample mit meta.tick = t sah (Konvention §2: Tick t selbst)."""
        return self.state(meta_tick)


def state_crc(st) -> int:
    return zlib.crc32(st) & 0xFFFFFFFF


def terrain_digest(terr) -> tuple[int, int]:
    refs = sorted(r for r, (a, b) in terr.items() if a != b)
    buf = bytearray()
    for r in refs:
        buf += struct.pack("<IB", r, terr[r][1])
    return zlib.crc32(buf) & 0xFFFFFFFF, len(refs)


# ── units.zst ───────────────────────────────────────────────────────────────

class Units:
    COLS = ("tick", "id", "mask", "code", "flags", "owner", "level", "pos", "target")

    def __init__(self, path: str):
        self.c = _Container(path, "UNIT")
        self.valid_until = self.c.valid_until
        self.last_tick = self.c.last_tick

    @property
    def complete(self):
        return self.c.complete

    def rows(self):
        """Iterator über Zeilen (tick, id, mask, code, flags, owner, level, pos, target),
        nur bis valid_until."""
        vu = self.valid_until
        for b in self.c.blocks:
            if vu is not None and b.t0 > vu:
                return
            p = self.c.payload(b)
            (n,) = struct.unpack_from("<I", p, 0)
            o = 4
            tick, o = _unplane(p, o, n, 4, "I")
            uid, o = _unplane(p, o, n, 4, "I")
            mask, o = _unplane(p, o, n, 1, "B")
            code, o = _unplane(p, o, n, 1, "B")
            flags, o = _unplane(p, o, n, 1, "B")
            owner, o = _unplane(p, o, n, 2, "H")
            level, o = _unplane(p, o, n, 2, "H")
            pos, o = _unplane(p, o, n, 4, "I")
            tgt, o = _unplane(p, o, n, 4, "I")
            if o != len(p):
                raise ValueError(f"units: Block {b.t0}: {len(p) - o} Byte übrig")
            for r in zip(tick, uid, mask, code, flags, owner, level, pos, tgt):
                if vu is not None and r[0] > vu:
                    return
                yield r

    def events(self) -> dict:
        """Ereignislisten je Klasse: {"buildings"|"warships"|"transports"|"nukes": [dict, ...]}.
        Je Zeile ein Ereignis mit `ev` = Liste der Änderungsarten und dem Snapshot danach."""
        out = {"buildings": [], "warships": [], "transports": [], "nukes": []}
        for t, i, m, c, f, ow, lv, pos, tg in self.rows():
            e = {"tick": t, "id": i, "type": UNIT_NAMES.get(c, c), "ev": [nm for bit, nm in EV_NAMES if m & bit],
                 "owner": ow, "level": lv, "under_construction": bool(f & 1),
                 "pos": None if pos == NONE else pos, "target": None if tg == NONE else tg}
            key = "buildings" if is_building(c) else "warships" if c == C_WARSHIP else \
                "transports" if c == C_TRANSPORT else "nukes"
            out[key].append(e)
        return out

    def digests(self, ticks) -> dict:
        """Einheiten-Digest (wie units_crc32 in .chk) an den gewünschten Ticks: {t: (crc, n)}."""
        want = sorted(set(ticks))
        res = {}
        act: dict[int, list] = {}
        wi = 0

        def emit(t):
            smp = t % SHIP_SAMPLE_EVERY == 0
            buf = bytearray()
            for uid in sorted(act):
                c, ow, lv, uc, pos, tg = act[uid]
                if is_building(c):
                    p, g = pos, NONE
                elif is_ship(c):
                    p = pos if smp else NONE
                    g = tg if c == C_WARSHIP else (tg if smp else NONE)
                else:
                    p, g = NONE, tg
                buf += struct.pack("<IBHHBII", uid, c, ow, lv, uc, p, g)
            res[t] = (zlib.crc32(buf) & 0xFFFFFFFF, len(act))

        for t, uid, m, c, f, ow, lv, pos, tg in self.rows():
            while wi < len(want) and want[wi] < t:
                emit(want[wi]); wi += 1
            if m & EV_END:
                act.pop(uid, None)
            else:
                act[uid] = [c, ow, lv, f & 1, pos, tg]
        while wi < len(want):
            emit(want[wi]); wi += 1
        return res


# ── Prüfen ──────────────────────────────────────────────────────────────────

def verify(d: str, gid: str, ref_path: str | None = None, quiet=False) -> bool:
    def say(*a):
        if not quiet:
            print(*a)

    own = Own(os.path.join(d, f"{gid}.own.zst"))
    units = Units(os.path.join(d, f"{gid}.units.zst"))
    chk = json.load(open(os.path.join(d, f"{gid}.chk")))
    ref = json.load(open(ref_path)) if ref_path else None
    fails: list[str] = []
    checked = 0

    if not own.complete:
        fails.append(f"own.zst unvollständig (Abschluss {own.c.last_tick}, Einträge bis {own.last_tick})")
    if not units.complete:
        fails.append("units.zst unvollständig")
    if chk.get("last_tick") != own.last_tick:
        fails.append(f".chk last_tick {chk.get('last_tick')} != own {own.last_tick}")
    if chk.get("valid_until") != own.valid_until or units.valid_until != own.valid_until:
        fails.append(f"valid_until uneinig: chk {chk.get('valid_until')}, own {own.valid_until}, units {units.valid_until}")

    # Sollwerte je Tick: (Quelle, state, terrain, units)
    targets: dict[int, list] = {}
    skipped = 0
    for i, t in enumerate(chk["ticks"]):
        if t > own.max_tick:
            fails.append(f".chk Tick {t} nach valid_until/L {own.max_tick}")
            continue
        targets.setdefault(t, []).append(("chk", chk["crc32"][i], (chk["terrain_crc32"][i], chk["terrain_n"][i]),
                                          (chk["units_crc32"][i], chk["units_n"][i])))
    if ref:
        for i, t in enumerate(ref["ticks"]):
            if t > own.max_tick:
                skipped += 1  # nach valid_until: absichtlich nicht mehr rekonstruierbar
                continue
            targets.setdefault(t, []).append(("ref", ref["state_crc"][i], (ref["terrain_crc"][i], ref["terrain_n"][i]),
                                              (ref["units_crc"][i], ref["units_n"][i])))
    key_ticks = {b.t0 for b in own.keys if b.t0 <= own.max_tick}
    keymap = {b.t0: b for b in own.keys}
    want = set(targets) | key_ticks

    # 1) sequentieller Durchlauf own, dabei Keyframes gegen die Delta-Summe prüfen
    got_state, got_terr = {}, {}
    key_ok = key_bad = 0
    for t, st, terr in own.replay_ticks(want):
        if t in targets:
            got_state[t] = state_crc(st)
            got_terr[t] = terrain_digest(terr)
        if t in key_ticks and not (t == 0 and own.keys[0].t0 == 0):
            kst, kterr = own._key(keymap[t])
            if kst == st and {r: v[1] for r, v in kterr.items()} == {r: v[1] for r, v in terr.items()}:
                key_ok += 1
            else:
                key_bad += 1
                fails.append(f"Keyframe {t} (Engine) != Summe der Deltas")
    # 2) Einheiten
    got_units = units.digests(targets.keys())

    hits = {"chk": [0, 0], "ref": [0, 0]}
    for t, lst in sorted(targets.items()):
        for src, s_crc, tr, un in lst:
            for what, soll, ist in (("state", s_crc, got_state.get(t)),
                                    ("terrain", tuple(tr), got_terr.get(t)),
                                    ("units", tuple(un), got_units.get(t))):
                hits[src][1] += 1
                checked += 1
                gleich = (tuple(ist) == soll) if isinstance(soll, tuple) and ist is not None else (ist == soll)
                if gleich:
                    hits[src][0] += 1
                elif len(fails) < 20:
                    fails.append(f"{src} Tick {t} {what}: erwartet {soll}, rekonstruiert {ist}")

    # 3) Random Access (Keyframe-Weg) an bis zu 10 Ticks gegen dieselben Sollwerte
    ra_ok = ra_n = 0
    ts = sorted(targets)
    pick = set(ts[int(i * (len(ts) - 1) / 7)] for i in range(8)) if len(ts) > 8 else set(ts)
    pick |= {k + 1 for k in key_ticks if k + 1 in targets}
    pick |= {k for k in key_ticks if k in targets}
    for t in sorted(pick):
        ra_n += 1
        checked += 1
        if state_crc(own.state(t)) == targets[t][0][1]:
            ra_ok += 1
        else:
            fails.append(f"random access Tick {t}: CRC weicht ab")

    # 4) valid_until: danach darf es keinen Zustand geben
    if own.valid_until is not None and own.valid_until < own.last_tick:
        checked += 1
        try:
            own.state(own.valid_until + 1)
            fails.append("state() nach valid_until liefert trotzdem einen Zustand")
        except NotValid:
            pass

    # 5) Auswahl-Funktionen gegen TS (nur mit Referenz)
    if ref and "candidate_ticks" in ref:
        checked += 1
        py = chk_ticks_for(gid, ref["candidate_ticks"])
        if py != ref["chk_ticks"]:
            fails.append(f"chk_ticks_for {py} != TS {ref['chk_ticks']}")
        for pct, v in ref.get("selected", {}).items():
            checked += 1
            if tier1_selected(gid, int(pct)) != v:
                fails.append(f"tier1_selected({pct}) != TS")

    ok = not fails and checked > 0 and hits["chk"][1] > 0
    say(f"{gid}: chk {hits['chk'][0]}/{hits['chk'][1]}  ref {hits['ref'][0]}/{hits['ref'][1]}"
        f"{f' ({skipped} nach valid_until übersprungen)' if skipped else ''}  "
        f"keyframes {key_ok}/{key_ok + key_bad}  random {ra_ok}/{ra_n}  "
        f"Ticks 0..{own.max_tick}{f' (valid_until {own.valid_until})' if own.valid_until is not None else ''}"
        f"  → {'OK' if ok else 'FEHLER'}")
    for f in fails[:20]:
        say("  -", f)
    if hits["chk"][1] == 0:
        say("  - keine .chk-Prüfpunkte (0 geprüft ist kein Erfolg)")
    return ok


def main(argv):
    if len(argv) >= 3 and argv[0] == "verify":
        ref = argv[argv.index("--ref") + 1] if "--ref" in argv else None
        try:
            ok = verify(argv[1], argv[2], ref)
        except Exception as e:  # kaputte Datei = nicht verifiziert
            print(f"{argv[2]}: FEHLER {type(e).__name__}: {e}")
            ok = False
        return 0 if ok else 1
    if len(argv) >= 4 and argv[0] == "state":
        own = Own(os.path.join(argv[1], f"{argv[2]}.own.zst"))
        t = int(argv[3])
        st = own.state(t)
        if "--out" in argv:
            out = array("H", st)
            if not _LE:
                out.byteswap()
            with open(argv[argv.index("--out") + 1], "wb") as f:
                f.write(out.tobytes())
        print(json.dumps({"tick": t, "W": own.W, "H": own.H, "crc32": state_crc(st),
                          "owned": sum(1 for v in st if v & 0xFFF)}))
        return 0
    if len(argv) >= 3 and argv[0] == "units":
        ev = Units(os.path.join(argv[1], f"{argv[2]}.units.zst")).events()
        if "--jsonl" in argv:
            for k, lst in ev.items():
                for e in lst:
                    print(json.dumps({"class": k, **e}))
        else:
            from collections import Counter
            for k, lst in ev.items():
                c = Counter(x for e in lst for x in e["ev"])
                print(k, len(lst), dict(c))
        return 0
    print(__doc__.split("=====")[0])
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

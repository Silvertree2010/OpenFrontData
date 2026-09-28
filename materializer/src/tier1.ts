/**
 * Tier 1 (P2) des Materialisierers v2 — DESIGN.md §5.3 und §8.
 *
 *   <gid>.own.zst    Besitzwechsel-Log: u16-Kachelzustand (Besitz, Fallout, Bonus) je Tick
 *                    als Delta gegen einen Spiegelpuffer, dazu Terrain-Wechsel (Water-Nukes),
 *                    Keyframes aus der Engine-Wahrheit.
 *   <gid>.units.zst  Einheiten-Log: je Änderung eine Zeile mit Änderungsmaske + Snapshot.
 *   <gid>.chk        CRC32 des vollen Zustandspuffers (Engine-Wahrheit) an den vorab
 *                    gewählten Ticks und am letzten Tick, dazu Einheiten- und Terrain-Digest.
 *
 * Das exakte Byte-Layout steht (verbindlich) im Kopf von py/tier1.py. Wer hier etwas am
 * Layout ändert, ändert es dort mit und erhöht T1_VERSION.
 *
 * Tick-Konvention (DESIGN §2): „Tick t“ = Zustand mit game.ticks() == t, also vor
 * executeNextTick für Zug t. onTick(T) läuft nach Zug T; die Updates daraus ergeben den
 * Zustand von Tick T+1 und werden unter T+1 abgelegt. Tick 0 ist der Anfangszustand.
 *
 * Kosten: pro Tick ein Durchlauf über die packedTileUpdates (Dirty-Flag statt Set),
 * Sortieren nur der wirklich geänderten Kacheln. Pro 512 Ticks ein zstd-3-Frame, sofort
 * geschrieben. RAM: Spiegel u16 + Terrain-Spiegel u8 + Terrain-Ursprung u8 + Dirty u8
 * = 5 Byte je Kachel, plus der offene Block.
 */
import { createHash } from "crypto";
import fs from "fs";
import path from "path";
import { constants as zc, crc32, zstdCompressSync } from "zlib";
import { Game, UnitType } from "../../vendor/openfront/src/core/game/Game";
import {
  GameUpdateType,
  GameUpdateViewData,
  UnitUpdate,
} from "../../vendor/openfront/src/core/game/GameUpdates";

export const T1_VERSION = 1;
export const BLOCK_TICKS = 512;
export const KEY_EVERY = 4096;
export const SHIP_SAMPLE_EVERY = 8;
export const NONE = 0xffffffff;
const ZLEVEL = 3;
const ZOPTS = { params: { [zc.ZSTD_c_compressionLevel]: ZLEVEL, [zc.ZSTD_c_checksumFlag]: 1 } };
// Sortierschlüssel Wert·2^26 + Ref (exakt in float64, Wert < 2^16, Ref < 2^26)
const KEY_SHIFT = 2 ** 26;

// zstd-Skippable-Frames (0x184D2A50..5F): die Datei bleibt eine gültige .zst
// (`zstd -t` prüft sie), und die Blockköpfe sind ohne Dekompression lesbar.
const MAGIC_FILE = 0x184d2a50;
const MAGIC_BLOCK = 0x184d2a51;
const MAGIC_END = 0x184d2a52;

// Einheiten-Codes (u8). Reihenfolge ist Format — nur anhängen, nie umnummerieren.
export const UNIT_CODES: ReadonlyArray<[string, number]> = [
  ["City", 1],
  ["Port", 2],
  ["Factory", 3],
  ["Defense Post", 4],
  ["Missile Silo", 5],
  ["SAM Launcher", 6],
  ["Warship", 7],
  ["Transport", 8],
  ["Atom Bomb", 9],
  ["Hydrogen Bomb", 10],
  ["MIRV", 11],
  ["MIRV Warhead", 12],
];
const CODE = new Map<string, number>(UNIT_CODES);
const TRACKED_TYPES = UNIT_CODES.map(([t]) => t as UnitType);
const C_WARSHIP = 7;
const C_TRANSPORT = 8;
const isBuilding = (c: number) => c >= 1 && c <= 6;
const isShip = (c: number) => c === C_WARSHIP || c === C_TRANSPORT;

// Änderungsmaske je Einheiten-Zeile
export const EV_CREATE = 1,
  EV_OWNER = 2,
  EV_LEVEL = 4,
  EV_CONSTR = 8,
  EV_POS = 16,
  EV_TARGET = 32,
  EV_END = 64;

// ── Auswahl und Prüf-Ticks ─────────────────────────────────────────────────

function sha1Hex(gid: string): string {
  return createHash("sha1").update(gid, "ascii").digest("hex");
}

/** sha1(gid) als Big-Endian-Zahl der ersten 8 Hex-Zeichen (DESIGN §5.3). */
export function gidHash(gid: string): number {
  return parseInt(sha1Hex(gid).slice(0, 8), 16);
}

export function tier1Selected(gid: string, pct: number): boolean {
  const h = gidHash(gid);
  return h % 100 < pct || h % 25 === 0;
}

/**
 * Zwei Prüf-Ticks, deterministisch aus sha1(gid), aus den Kandidaten des Vorab-Scans
 * (Züge mit Menschen-Intent nach numSpawnPhaseTurns()+1). Die Wahl nutzt die Hex-Zeichen
 * 24..31 und 32..39, also weder die Tier-1-Auswahl (0..7) noch SHARD (8..15).
 */
export function chkTicksFor(gid: string, candidateTicks: number[]): number[] {
  const t = Array.from(new Set(candidateTicks.map((x) => x | 0))).sort((a, b) => a - b);
  const n = t.length;
  if (n === 0) return [];
  const hx = sha1Hex(gid);
  const h1 = parseInt(hx.slice(24, 32), 16);
  const h2 = parseInt(hx.slice(32, 40), 16);
  const i1 = h1 % n;
  if (n === 1) return [t[i1]];
  const i2 = (i1 + 1 + (h2 % (n - 1))) % n;
  return [t[i1], t[i2]].sort((a, b) => a - b);
}

// ── Digests (Engine-Wahrheit; von .chk und vom Test-Harness benutzt) ─────────

export function stateCrc(game: Game): number {
  const st = game.tileStateBuffer();
  return crc32(Buffer.from(st.buffer, st.byteOffset, st.byteLength)) >>> 0;
}

/** Aktueller Terrain-Puffer der Engine (Uint8Array W·H), ohne Kopie wenn möglich. */
export function terrainArray(game: Game): Uint8Array {
  const m: any = game.map();
  const n = game.width() * game.height();
  if (m.terrain instanceof Uint8Array && m.terrain.length === n) return m.terrain;
  const out = new Uint8Array(n);
  for (let r = 0; r < n; r++) out[r] = m.terrainByte(r);
  return out;
}

/** CRC32 über (ref u32, terrain u8) aller Kacheln, deren Terrain vom Ursprung abweicht. */
export function terrainDigest(game: Game, init: Uint8Array): { crc: number; n: number } {
  const cur = terrainArray(game);
  const refs: number[] = [];
  for (let r = 0; r < cur.length; r++) if (cur[r] !== init[r]) refs.push(r);
  const b = Buffer.alloc(refs.length * 5);
  for (let i = 0; i < refs.length; i++) {
    b.writeUInt32LE(refs[i], i * 5);
    b[i * 5 + 4] = cur[refs[i]];
  }
  return { crc: crc32(b) >>> 0, n: refs.length };
}

/**
 * CRC32 über die aktiven verfolgten Einheiten im aktuellen Zustand (Tick t =
 * game.ticks()), nach id sortiert, je 18 Byte `<IBHHBII`: id, code, owner, level,
 * im Bau, pos, target.
 *   pos:    Gebäude immer; Schiffe nur an Ticks mit t % 8 == 0 (dann abgetastet); sonst NONE
 *   target: Nukes/MIRV/Sprengkopf targetTile; Kriegsschiff patrolTile; Transport targetTile nur
 *           an t % 8 == 0; Gebäude NONE
 * Liest nur Getter bzw. das rohe `_warshipState` (warshipState() schreibt isInCombat zurück).
 */
export function unitsDigest(game: Game, tick: number): { crc: number; n: number } {
  const sample = tick % SHIP_SAMPLE_EVERY === 0;
  const rows: number[][] = [];
  for (const u of game.units(TRACKED_TYPES) as any[]) {
    if (!u.isActive()) continue;
    const c = CODE.get(u.type())!;
    let pos = NONE,
      tgt = NONE;
    if (isBuilding(c)) pos = u.tile();
    else if (isShip(c)) {
      if (sample) pos = u.tile();
      if (c === C_WARSHIP) tgt = u._warshipState?.patrolTile ?? NONE;
      else if (sample) tgt = u.targetTile() ?? NONE;
    } else tgt = u.targetTile() ?? NONE;
    rows.push([u.id(), c, u.owner().smallID(), u.level(), u.isUnderConstruction() ? 1 : 0, pos, tgt]);
  }
  rows.sort((a, b) => a[0] - b[0]);
  const b = Buffer.alloc(rows.length * 18);
  let o = 0;
  for (const r of rows) {
    b.writeUInt32LE(r[0], o);
    b[o + 4] = r[1];
    b.writeUInt16LE(r[2], o + 5);
    b.writeUInt16LE(r[3], o + 7);
    b[o + 9] = r[4];
    b.writeUInt32LE(r[5] >>> 0, o + 10);
    b.writeUInt32LE(r[6] >>> 0, o + 14);
    o += 18;
  }
  return { crc: crc32(b) >>> 0, n: rows.length };
}

// ── Hilfen ──────────────────────────────────────────────────────────────────

type TA = Uint8Array | Uint16Array | Uint32Array;
type TACtor = { new (n: number): TA };

/** Wachsendes typisiertes Array. */
class Grow {
  a: TA;
  n: number;
  private C: TACtor;
  constructor(C: TACtor, cap = 1024) {
    this.C = C;
    this.a = new C(cap);
    this.n = 0;
  }
  reserve(extra: number) {
    const need = this.n + extra;
    if (need <= this.a.length) return;
    let c = this.a.length * 2;
    while (c < need) c *= 2;
    const b = new this.C(c);
    b.set(this.a.subarray(0, this.n) as any);
    this.a = b;
  }
  push(v: number) {
    if (this.n === this.a.length) this.reserve(1);
    this.a[this.n++] = v;
  }
  view(): TA {
    return this.a.subarray(0, this.n);
  }
}

/** Byte-Planes: erst alle Bytes 0, dann alle Bytes 1 … (Little-Endian-Bytereihenfolge). */
function putPlanes(dst: Buffer, off: number, src: TA, width: number): number {
  const n = src.length;
  if (width === 1) {
    dst.set(src as Uint8Array, off);
  } else if (width === 2) {
    const o1 = off + n;
    for (let i = 0; i < n; i++) {
      const v = src[i];
      dst[off + i] = v & 255;
      dst[o1 + i] = v >>> 8;
    }
  } else {
    const o1 = off + n,
      o2 = off + 2 * n,
      o3 = off + 3 * n;
    for (let i = 0; i < n; i++) {
      const v = src[i];
      dst[off + i] = v & 255;
      dst[o1 + i] = (v >>> 8) & 255;
      dst[o2 + i] = (v >>> 16) & 255;
      dst[o3 + i] = v >>> 24;
    }
  }
  return off + n * width;
}

/** LEB128 (7 Bit je Byte, Bit 7 = weitere Bytes folgen). */
function pushVarint(g: Grow, v: number) {
  g.reserve(5);
  const a = g.a;
  let n = g.n;
  while (v >= 128) {
    a[n++] = (v & 127) | 128;
    v >>>= 7;
  }
  a[n++] = v;
  g.n = n;
}

function putU32Plain(dst: Buffer, off: number, src: TA): number {
  for (let i = 0; i < src.length; i++) dst.writeUInt32LE(src[i], off + 4 * i);
  return off + 4 * src.length;
}

interface UState {
  code: number;
  owner: number;
  level: number;
  uc: number;
  pos: number;
  tgt: number;
}

class BlockFile {
  fd: number;
  bytes = 0;
  blocks = 0;
  tmp: string;
  constructor(tmp: string, kind: string, W: number, H: number, S: number) {
    this.tmp = tmp;
    this.fd = fs.openSync(tmp, "w");
    const h = Buffer.alloc(8 + 32);
    h.writeUInt32LE(MAGIC_FILE, 0);
    h.writeUInt32LE(32, 4);
    h.write("OFT1", 8, "ascii");
    h.write(kind.padEnd(4, "\0"), 12, "ascii");
    h.writeUInt32LE(T1_VERSION, 16);
    h.writeUInt32LE(W, 20);
    h.writeUInt32LE(H, 24);
    h.writeInt32LE(S, 28);
    h.writeUInt32LE(BLOCK_TICKS, 32);
    h.writeUInt32LE(KEY_EVERY, 36);
    this.write(h);
  }
  write(b: Buffer) {
    fs.writeSync(this.fd, b);
    this.bytes += b.length;
  }
  block(t0: number, nt: number, flags: number, payload: Buffer) {
    const z = zstdCompressSync(payload, ZOPTS);
    const h = Buffer.alloc(24);
    h.writeUInt32LE(MAGIC_BLOCK, 0);
    h.writeUInt32LE(16, 4);
    h.writeUInt32LE(z.length, 8);
    h.writeInt32LE(t0, 12);
    h.writeUInt32LE(nt, 16);
    h.writeUInt32LE(flags, 20);
    this.write(h);
    this.write(z);
    this.blocks++;
  }
  end(lastTick: number, nRecords: number, validUntil: number | null) {
    const h = Buffer.alloc(8 + 24);
    h.writeUInt32LE(MAGIC_END, 0);
    h.writeUInt32LE(24, 4);
    h.writeInt32LE(lastTick, 8);
    h.writeUInt32LE(this.blocks, 12);
    h.writeUInt32LE(nRecords % 0x100000000, 16);
    h.writeUInt32LE(Math.floor(nRecords / 0x100000000), 20);
    h.writeUInt32LE(validUntil === null ? 0 : 1, 24);
    h.writeInt32LE(validUntil === null ? 0 : validUntil, 28);
    this.write(h);
    fs.closeSync(this.fd);
    this.fd = -1;
  }
  close() {
    if (this.fd >= 0) {
      try {
        fs.closeSync(this.fd);
      } catch {}
      this.fd = -1;
    }
  }
}

// ── Writer ──────────────────────────────────────────────────────────────────

export class Tier1Writer {
  private gid: string;
  private dir: string;
  private W: number;
  private H: number;
  private S: number;
  private chosen: number[];
  private chkSet: Set<number>;
  private chk: { ticks: number[]; crc32: number[]; units_crc32: number[]; units_n: number[]; terrain_crc32: number[]; terrain_n: number[] };

  // Kachelzustand
  protected mirror: Uint16Array;
  protected tmirror: Uint8Array;
  private tinit: Uint8Array;
  private dirty: Uint8Array;
  private touched: Uint32Array;
  private oldS: Uint16Array;
  private oldT: Uint8Array;
  private changed: Uint32Array;
  private keys: Float64Array;
  private everT: Set<number>;

  // offener own-Block (Einträge für Tick bT0 .. bT0+bNt-1)
  private own: BlockFile;
  private bT0: number;
  private bNt: number;
  private cntG: Grow; // u32 je Eintrag: Zahl der Wertgruppen
  private cntT: Grow; // u32 je Eintrag: Zahl der Terrainwechsel
  private gcnt: Grow; // varint je Gruppe: Zahl der Wechsel
  private gval: Grow; // u16 je Gruppe: neuer Wert
  private gaps: Grow; // varint je Wechsel: Ref-Lücke innerhalb der Gruppe
  private tgaps: Grow;
  private told: Grow;
  private tnew: Grow;

  // Einheiten
  private unitsF: BlockFile;
  protected units: Map<number, UState>;
  private ships: Set<number>;
  private col: { tick: Grow; id: Grow; mask: Grow; code: Grow; flags: Grow; owner: Grow; level: Grow; pos: Grow; tgt: Grow };

  private nextTurn: number;
  private lastState: number; // letzter Tick (Konvention §2), dessen Zustand vorliegt
  private game: Game;
  private done: boolean;
  private st = { stateChanges: 0, groups: 0, terrainChanges: 0, keyframes: 0, unitRows: 0, tilePairs: 0, rawOwnBytes: 0, rawUnitBytes: 0 };

  constructor(opts: { dir: string; gid: string; game: Game; spawnEndTick: number; chkTicks: number[] }) {
    const game = opts.game;
    this.game = game;
    this.gid = opts.gid;
    this.dir = opts.dir;
    this.W = game.width();
    this.H = game.height();
    this.S = Math.max(0, opts.spawnEndTick | 0);
    this.chosen = [...opts.chkTicks];
    this.chkSet = new Set(opts.chkTicks);
    this.chk = { ticks: [], crc32: [], units_crc32: [], units_n: [], terrain_crc32: [], terrain_n: [] };
    const n = this.W * this.H;

    const truth = game.tileStateBuffer();
    if (truth.length !== n) throw new Error(`tier1: tileStateBuffer ${truth.length} != W*H ${n}`);
    if (game.ticks() !== 0) throw new Error(`tier1: Konstruktor bei game.ticks() ${game.ticks()} statt 0`);
    if (n > KEY_SHIFT) throw new Error(`tier1: Karte ${n} Kacheln > 2^26`);
    if (new Uint8Array(new Uint16Array([1]).buffer)[0] !== 1) throw new Error("tier1: braucht Little Endian");
    this.mirror = new Uint16Array(n);
    this.tinit = terrainArray(game).slice();
    this.tmirror = this.tinit.slice();
    this.dirty = new Uint8Array(n);
    this.touched = new Uint32Array(4096);
    this.oldS = new Uint16Array(4096);
    this.oldT = new Uint8Array(4096);
    this.changed = new Uint32Array(4096);
    this.keys = new Float64Array(4096);
    this.everT = new Set();

    this.own = new BlockFile(path.join(this.dir, `${this.gid}.own.zst.tmp`), "OWN", this.W, this.H, this.S);
    this.unitsF = new BlockFile(path.join(this.dir, `${this.gid}.units.zst.tmp`), "UNIT", this.W, this.H, this.S);
    this.bT0 = 1;
    this.bNt = 0;
    this.cntG = new Grow(Uint32Array, 512);
    this.cntT = new Grow(Uint32Array, 512);
    this.gcnt = new Grow(Uint8Array, 4096);
    this.gval = new Grow(Uint16Array, 2048);
    this.gaps = new Grow(Uint8Array, 1 << 18);
    this.tgaps = new Grow(Uint32Array, 64);
    this.told = new Grow(Uint8Array, 64);
    this.tnew = new Grow(Uint8Array, 64);

    this.units = new Map();
    this.ships = new Set();
    this.col = {
      tick: new Grow(Uint32Array), id: new Grow(Uint32Array), mask: new Grow(Uint8Array),
      code: new Grow(Uint8Array), flags: new Grow(Uint8Array), owner: new Grow(Uint16Array),
      level: new Grow(Uint16Array), pos: new Grow(Uint32Array), tgt: new Grow(Uint32Array),
    };
    this.nextTurn = 0;
    this.lastState = 0;
    this.done = false;

    // Tick 0 = Anfangszustand, erwartet leer. Nicht leer (oder S == 0): Keyframe bei 0.
    let nonzero = false;
    for (let r = 0; r < n; r++) if (truth[r] !== 0) { nonzero = true; break; }
    if (nonzero) this.mirror.set(truth);
    if (nonzero || this.isKeyTick(0)) this.writeKey(0);
    if (this.chkSet.has(0)) this.addChk(0, game);
  }

  stats() {
    return { ...this.st, ownBlocks: this.own.blocks, unitBlocks: this.unitsF.blocks, lastTick: this.lastState };
  }

  private isKeyTick(t: number): boolean {
    return t >= this.S && (t - this.S) % KEY_EVERY === 0;
  }

  /** Beginnt mit dem Eintrag für Tick t ein neuer Delta-Block? */
  private isDeltaStart(t: number): boolean {
    if (t === 1) return true;
    if (t < this.S) return t % BLOCK_TICKS === 0;
    return (t - this.S) % BLOCK_TICKS === 0;
  }

  /** Nach executeNextTick für Zug `turn`; danach gilt game.ticks() == turn + 1. */
  onTick(turn: number, gu: GameUpdateViewData, game: Game): void {
    if (this.done) throw new Error("tier1: onTick nach finish/abort");
    if (turn !== this.nextTurn) throw new Error(`tier1: Zug ${turn}, erwartet ${this.nextTurn}`);
    const t = turn + 1;
    if (game.ticks() !== t) throw new Error(`tier1: game.ticks() ${game.ticks()} != Zug+1 ${t}`);

    // Blockgrenzen VOR dem Anwenden: der alte Block endet mit Tick t-1
    if (t > 1 && this.isDeltaStart(t)) this.flushOwn();
    if (t % BLOCK_TICKS === 0) this.flushUnits();
    if (this.bNt === 0) this.bT0 = t;

    this.applyTiles(gu.packedTileUpdates);
    if (this.isKeyTick(t)) this.writeKey(t);

    const ups = gu.updates[GameUpdateType.Unit] as UnitUpdate[] | undefined;
    if (ups !== undefined) for (let i = 0; i < ups.length; i++) this.onUnit(t, ups[i]);
    if (t % SHIP_SAMPLE_EVERY === 0 && this.ships.size > 0) this.sampleShips(t, game);

    if (this.chkSet.has(t)) this.addChk(t, game);
    this.lastState = t;
    this.nextTurn = turn + 1;
  }

  private applyTiles(p: Uint32Array) {
    const np = p.length >>> 1;
    this.st.tilePairs += np;
    if (this.touched.length < np) {
      const c = Math.max(np, this.touched.length * 2);
      this.touched = new Uint32Array(c);
      this.oldS = new Uint16Array(c);
      this.oldT = new Uint8Array(c);
      this.changed = new Uint32Array(c);
      this.keys = new Float64Array(c);
    }
    const mir = this.mirror,
      tm = this.tmirror,
      dirty = this.dirty,
      touched = this.touched,
      oldS = this.oldS,
      oldT = this.oldT;
    let k = 0;
    for (let i = 0; i < p.length; i += 2) {
      const r = p[i],
        v = p[i + 1];
      if (dirty[r] === 0) {
        dirty[r] = 1;
        touched[k] = r;
        oldS[k] = mir[r];
        oldT[k] = tm[r];
        k++;
      }
      mir[r] = v & 0xffff;
      tm[r] = (v >>> 16) & 0xff;
    }
    const ch = this.changed;
    let nc = 0;
    let tRefs: number[] | null = null;
    let tOld: number[] | null = null;
    for (let j = 0; j < k; j++) {
      const r = touched[j];
      dirty[r] = 0;
      if (mir[r] !== oldS[j]) ch[nc++] = r;
      if (tm[r] !== oldT[j]) {
        if (tRefs === null) { tRefs = []; tOld = []; }
        tRefs.push(r);
        tOld!.push(oldT[j]);
      }
    }
    // Nach (neuer Wert, Ref) sortiert: jeder Wert steht einmal je Gruppe, die Refs der
    // Gruppe aufsteigend als Varint-Lücken. Gemessen −30 % gegenüber reiner Ref-Sortierung
    // mit Byte-Planes (tests/tier1/enc_experiment.py).
    const keys = this.keys;
    for (let j = 0; j < nc; j++) {
      const r = ch[j];
      keys[j] = mir[r] * KEY_SHIFT + r;
    }
    const ks = keys.subarray(0, nc).sort();
    const gaps = this.gaps;
    gaps.reserve(nc * 4); // Ref < 2^26 → höchstens 4 Varint-Bytes
    const ga = gaps.a;
    let gn = gaps.n;
    let groups = 0,
      lastV = -1,
      prev = 0,
      cnt = 0;
    for (let j = 0; j < nc; j++) {
      const key = ks[j];
      const v = Math.floor(key / KEY_SHIFT);
      const r = key - v * KEY_SHIFT;
      if (v !== lastV) {
        if (groups > 0) pushVarint(this.gcnt, cnt);
        this.gval.push(v);
        groups++;
        lastV = v;
        prev = 0;
        cnt = 0;
      }
      let g = r - prev;
      while (g >= 128) {
        ga[gn++] = (g & 127) | 128;
        g >>>= 7;
      }
      ga[gn++] = g;
      prev = r;
      cnt++;
    }
    gaps.n = gn;
    if (groups > 0) pushVarint(this.gcnt, cnt);
    this.cntG.push(groups);
    this.st.stateChanges += nc;
    this.st.groups += groups;

    if (tRefs === null) {
      this.cntT.push(0);
    } else {
      const idx = tRefs.map((_, i) => i).sort((a, b) => tRefs![a] - tRefs![b]);
      this.cntT.push(idx.length);
      let pr = 0;
      for (const i of idx) {
        const r = tRefs[i];
        this.tgaps.push(r - pr);
        pr = r;
        this.told.push(tOld![i]);
        this.tnew.push(tm[r]);
        this.everT.add(r);
      }
      this.st.terrainChanges += idx.length;
    }
    this.bNt++;
  }

  /** Keyframe = Engine-Wahrheit im Tick t (aktueller Zustand). Sofort geschrieben. */
  private writeKey(t: number) {
    const st = this.game.tileStateBuffer();
    const n = st.length;
    const cur = terrainArray(this.game);
    const refs = Uint32Array.from(this.everT).sort();
    const nk = refs.length;
    const b = Buffer.allocUnsafe(2 * n + 4 + 4 * nk + 2 * nk);
    // u16 LE am Stück (gemessen kleiner als Byte-Planes)
    Buffer.from(st.buffer, st.byteOffset, st.byteLength).copy(b, 0);
    let o = 2 * n;
    b.writeUInt32LE(nk, o);
    o += 4;
    o = putPlanes(b, o, refs, 4);
    for (let i = 0; i < nk; i++) b[o + i] = this.tinit[refs[i]];
    o += nk;
    for (let i = 0; i < nk; i++) b[o + i] = cur[refs[i]];
    o += nk;
    if (o !== b.length) throw new Error(`tier1: Keyframe-Länge ${o} != ${b.length}`);
    this.st.rawOwnBytes += o;
    this.own.block(t, 0, 1, b);
    this.st.keyframes++;
  }

  private flushOwn() {
    if (this.bNt === 0) return;
    const m = this.bNt,
      G = this.gval.n,
      LC = this.gcnt.n,
      LG = this.gaps.n,
      NT = this.tgaps.n;
    const b = Buffer.allocUnsafe(8 * m + 8 + LC + 2 * G + LG + 6 * NT);
    let o = putU32Plain(b, 0, this.cntG.view());
    o = putU32Plain(b, o, this.cntT.view());
    b.writeUInt32LE(LC, o);
    b.writeUInt32LE(LG, o + 4);
    o += 8;
    o = putPlanes(b, o, this.gcnt.view(), 1);
    o = putPlanes(b, o, this.gval.view(), 2);
    o = putPlanes(b, o, this.gaps.view(), 1);
    o = putPlanes(b, o, this.tgaps.view(), 4);
    o = putPlanes(b, o, this.told.view(), 1);
    o = putPlanes(b, o, this.tnew.view(), 1);
    if (o !== b.length) throw new Error(`tier1: Delta-Länge ${o} != ${b.length}`);
    this.st.rawOwnBytes += o;
    this.own.block(this.bT0, m, 0, b);
    this.bNt = 0;
    this.cntG.n = this.cntT.n = this.gcnt.n = this.gval.n = this.gaps.n = 0;
    this.tgaps.n = this.told.n = this.tnew.n = 0;
  }

  // ── Einheiten ──

  private row(tick: number, id: number, mask: number, s: UState) {
    const c = this.col;
    c.tick.push(tick);
    c.id.push(id);
    c.mask.push(mask);
    c.code.push(s.code);
    c.flags.push(s.uc);
    c.owner.push(s.owner);
    c.level.push(s.level);
    c.pos.push(s.pos >>> 0);
    c.tgt.push(s.tgt >>> 0);
    this.st.unitRows++;
  }

  /** Ein Unit-Update, das den Zustand von Tick `tick` herstellt. */
  protected onUnit(tick: number, u: UnitUpdate) {
    const code = CODE.get(u.unitType);
    if (code === undefined) return;
    const uc = u.underConstruction ? 1 : 0;
    const tgt = isBuilding(code)
      ? NONE
      : code === C_WARSHIP
        ? (u.warshipState?.patrolTile ?? NONE)
        : (u.targetTile ?? NONE);
    let s = this.units.get(u.id);
    if (s === undefined) {
      s = { code, owner: u.ownerID, level: u.level, uc, pos: u.pos, tgt };
      if (u.isActive) {
        this.units.set(u.id, s);
        if (isShip(code)) this.ships.add(u.id);
        this.row(tick, u.id, EV_CREATE, s);
      } else this.row(tick, u.id, EV_CREATE | EV_END, s);
      return;
    }
    let mask = 0;
    if (u.ownerID !== s.owner) { s.owner = u.ownerID; mask |= EV_OWNER; }
    if (u.level !== s.level) { s.level = u.level; mask |= EV_LEVEL; }
    if (uc !== s.uc) { s.uc = uc; mask |= EV_CONSTR; }
    // Schiffspositionen nur im 8-Tick-Takt (sampleShips), Nukes nur Start/Ende.
    if (isBuilding(code) && u.pos !== s.pos) { s.pos = u.pos; mask |= EV_POS; }
    if (!isBuilding(code) && tgt !== s.tgt) { s.tgt = tgt; mask |= EV_TARGET; }
    if (!u.isActive) {
      mask |= EV_END;
      s.pos = u.pos;
      this.units.delete(u.id);
      this.ships.delete(u.id);
    }
    if (mask !== 0) this.row(tick, u.id, mask, s);
  }

  private sampleShips(tick: number, game: Game) {
    for (const id of this.ships) {
      const unit: any = game.unit(id);
      if (!unit) continue;
      const s = this.units.get(id)!;
      let mask = 0;
      const pos = unit.tile();
      if (pos !== s.pos) { s.pos = pos; mask |= EV_POS; }
      if (s.code === C_TRANSPORT) {
        const t = unit.targetTile() ?? NONE;
        if (t !== s.tgt) { s.tgt = t; mask |= EV_TARGET; }
      }
      if (mask !== 0) this.row(tick, id, mask, s);
    }
  }

  private flushUnits() {
    const c = this.col;
    const n = c.tick.n;
    if (n === 0) return;
    const b = Buffer.allocUnsafe(4 + n * 23); // 4+4+1+1+1+2+2+4+4 Byte je Zeile
    b.writeUInt32LE(n, 0);
    let o = putPlanes(b, 4, c.tick.view(), 4);
    o = putPlanes(b, o, c.id.view(), 4);
    o = putPlanes(b, o, c.mask.view(), 1);
    o = putPlanes(b, o, c.code.view(), 1);
    o = putPlanes(b, o, c.flags.view(), 1);
    o = putPlanes(b, o, c.owner.view(), 2);
    o = putPlanes(b, o, c.level.view(), 2);
    o = putPlanes(b, o, c.pos.view(), 4);
    o = putPlanes(b, o, c.tgt.view(), 4);
    if (o !== b.length) throw new Error(`tier1: Units-Länge ${o} != ${b.length}`);
    this.st.rawUnitBytes += o;
    const t0 = c.tick.a[0] - (c.tick.a[0] % BLOCK_TICKS);
    this.unitsF.block(t0, BLOCK_TICKS, 0, b);
    for (const g of Object.values(c)) g.n = 0;
  }

  // ── Prüfsummen ──

  private addChk(tick: number, game: Game) {
    const u = unitsDigest(game, tick);
    const t = terrainDigest(game, this.tinit);
    this.chk.ticks.push(tick);
    this.chk.crc32.push(stateCrc(game));
    this.chk.units_crc32.push(u.crc);
    this.chk.units_n.push(u.n);
    this.chk.terrain_crc32.push(t.crc);
    this.chk.terrain_n.push(t.n);
  }

  /**
   * Schliesst beide Logs ab und schreibt .chk. Muss direkt nach dem letzten onTick
   * laufen, ohne weiteren executeNextTick. validUntil (Tick-Konvention §2): Zustände
   * danach gelten als ungültig (Desync, Tick-Fehler); .chk-Ticks danach fallen weg.
   */
  finish(validUntil: number | null = null): { files: Record<string, string>; bytes: number; chkDropped: number } {
    if (this.done) throw new Error("tier1: finish doppelt");
    const L = this.lastState;
    const vu = validUntil === null || validUntil === undefined ? null : validUntil | 0;
    this.flushOwn();
    this.flushUnits();
    this.own.end(L, this.st.stateChanges, vu);
    this.unitsF.end(L, this.st.unitRows, vu);
    // Zusatz-Prüfpunkt: der letzte gültige Zustand, sofern er gerade vorliegt
    if ((vu === null || L <= vu) && !this.chkSet.has(L) && this.game.ticks() === L) this.addChk(L, this.game);
    if (vu !== null) {
      const keep = this.chk.ticks.map((t) => t <= vu);
      for (const k of Object.keys(this.chk) as (keyof typeof this.chk)[]) {
        this.chk[k] = this.chk[k].filter((_, i) => keep[i]);
      }
    }
    const have = new Set(this.chk.ticks);
    const chkDropped = this.chosen.filter((t) => !have.has(t)).length;
    const chkTmp = path.join(this.dir, `${this.gid}.chk.tmp`);
    fs.writeFileSync(chkTmp, JSON.stringify({
      version: T1_VERSION, last_tick: L, valid_until: vu, chosen: this.chosen, dropped: chkDropped, ...this.chk,
    }));
    this.done = true;
    const files: Record<string, string> = {
      [`${this.gid}.own.zst`]: this.own.tmp,
      [`${this.gid}.units.zst`]: this.unitsF.tmp,
      [`${this.gid}.chk`]: chkTmp,
    };
    let bytes = 0;
    for (const f of Object.values(files)) bytes += fs.statSync(f).size;
    return { files, bytes, chkDropped };
  }

  abort(): void {
    this.done = true;
    this.own.close();
    this.unitsF.close();
    for (const f of [this.own.tmp, this.unitsF.tmp, path.join(this.dir, `${this.gid}.chk.tmp`)]) {
      try {
        fs.unlinkSync(f);
      } catch {}
    }
  }
}

/**
 * Replay-Harness fuer cells.ts (P3). Nur Tests, schreibt nichts ausser stdout/stderr.
 *
 * Aufruf (cwd = ~/mat-dev/w/<sha8>, dort liegt tsconfig.json mit useDefineForClassFields=false):
 *   nice -n 10 npx tsx mat-cells/tests/cells/harness.ts <modus> <record.json> [--opt wert ...]
 *
 * Modi:
 *   compare  cells.ts gegen reference.ts an verteilten (Tick, Mensch)-Paaren, bit-genau
 *            (--ticks N Ticks nach der Spawnphase, 6 in der Spawnphase, --per N Menschen je Tick)
 *   hash     Replay mit Hash-Pruefung; --compute 1 ruft compute an JEDEM Tick mit
 *            Menschen-Intent fuer jeden handelnden Menschen (auch in der Spawnphase)
 *   mask     Masken-Plausibilitaet: Bit laut Typ->Bit in der Zelle der Klickkachel im
 *            Entscheidungstick; zusaetzlich beobachtete Aufloesung (res-Kachel) wie DESIGN §5.2
 *   cost     Zeit je compute an den raeumlichen Samples des alten thin-Laufs (--thin <meta.zst>)
 * Allgemein: --bit1 0|1 (Standard 1). Hashes werden in jedem Modus geprueft.
 * Umgebung:  CELLS_IMPL=<Pfad relativ zu dieser Datei> ersetzt ../../src/cells.ts (Mutationstest).
 * Ausgabe:   eine JSON-Zeile auf stdout, Fortschritt auf stderr.
 */
import fs from "fs";
import path from "path";
import { fileURLToPath, pathToFileURL } from "url";
import { zstdCompressSync, zstdDecompressSync } from "zlib";
import { Config } from "../../../vendor/openfront/src/core/configuration/Config";
import { Executor } from "../../../vendor/openfront/src/core/execution/ExecutionManager";
import { Game, Player, PlayerInfo, PlayerType } from "../../../vendor/openfront/src/core/game/Game";
import { createGame } from "../../../vendor/openfront/src/core/game/GameImpl";
import { GameUpdateType } from "../../../vendor/openfront/src/core/game/GameUpdates";
import { createNationsForGame } from "../../../vendor/openfront/src/core/game/NationCreation";
import { loadTerrainMap } from "../../../vendor/openfront/src/core/game/TerrainMapLoader";
import { GameRunner } from "../../../vendor/openfront/src/core/GameRunner";
import { PseudoRandom } from "../../../vendor/openfront/src/core/PseudoRandom";
import { GameRecord, GameRecordSchema } from "../../../vendor/openfront/src/core/Schemas";
import { decompressGameRecord, simpleHash, toWireGameStartInfo } from "../../../vendor/openfront/src/core/Util";
import { NodeGameMapLoader } from "../../../vendor/openfront/tests/perf/fullgame/NodeGameMapLoader";
import { ObsEncoder } from "../../src/obs";
import { referenceCells, RefStats } from "./reference";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const TREE = path.resolve(HERE, "../../..");
const ENGINE = path.join(TREE, "vendor/openfront");
const COMMIT8 = path.basename(TREE);
const N = 16200;
const now = () => performance.now();

// ───────────────────────── Argumente ─────────────────────────
const [mode, file, ...rest] = process.argv.slice(2);
const opt: Record<string, string> = {};
for (let i = 0; i + 1 < rest.length; i += 2) opt[rest[i].replace(/^--/, "")] = rest[i + 1];
const BIT1 = (opt.bit1 ?? "1") === "1";
if (!mode || !file) { console.error("Aufruf: harness.ts <compare|hash|mask|cost> <record.json> [--opt wert]"); process.exit(2); }

function pct(a: number[], q: number): number {
  if (!a.length) return NaN;
  const s = [...a].sort((x, y) => x - y);
  return s[Math.min(s.length - 1, Math.floor(q * (s.length - 1) + 0.5))];
}
const r3 = (x: number) => Math.round(x * 1000) / 1000;

async function main() {
  console.debug = () => {};
  // Engine-Logs (console.log) nach stderr, stdout bleibt dem Ergebnis vorbehalten
  console.log = (...a: any[]) => console.error(...a);
  const cellsMod = await import(pathToFileURL(path.join(HERE, process.env.CELLS_IMPL ?? "../../src/cells.ts")).href);
  const CellFacts = cellsMod.CellFacts;

  // ───────────── Setup wie env/materialize.ts ─────────────
  const raw = JSON.parse(fs.readFileSync(file, "utf8"));
  const parsed = GameRecordSchema.safeParse(raw);
  const record: GameRecord = decompressGameRecord(parsed.success ? parsed.data : (raw as GameRecord));
  const info = record.info;
  const commit = String((record as any).gitCommit ?? raw.gitCommit ?? "");
  if (!commit.startsWith(COMMIT8)) throw new Error(`Record-Commit ${commit} passt nicht zum Baum ${COMMIT8}`);
  const gameStart = toWireGameStartInfo({
    gameID: info.gameID, lobbyCreatedAt: info.lobbyCreatedAt,
    config: info.config, players: info.players, tribes: info.tribes });
  const config = new Config(info.config, null, false);
  const terrain = await loadTerrainMap(info.config.gameMap, info.config.gameMapSize,
    new NodeGameMapLoader(path.join(ENGINE, "resources/maps")), false);
  const random = new PseudoRandom(simpleHash(gameStart.gameID));
  const humans = gameStart.players.map((p) => new PlayerInfo(
    p.username, PlayerType.Human, p.clientID, random.nextID(),
    p.isLobbyCreator ?? false, p.clanTag, p.friends ?? [], p.teamIndex ?? null));
  const nations = createNationsForGame(gameStart, terrain.nations, terrain.additionalNations, humans.length, random);
  const game: Game = createGame(humans, nations, terrain.gameMap, terrain.miniGameMap, config, terrain.teamGameSpawnAreas);
  let fatal: string | undefined;
  let cur: any = null;
  const runner = new GameRunner(game, new Executor(game, gameStart.gameID, undefined,
    gameStart.tribes?.map((t) => t.name)), (gu: any) => { if ("errMsg" in gu) fatal = gu.errMsg; else cur = gu; });
  runner.init();

  const humanCids: string[] = gameStart.players.map((p) => p.clientID);
  const humanSet = new Set(humanCids);
  const W = game.width(), H = game.height();
  const cellOf = (t: number) => (((game.y(t) * 90) / H) | 0) * 180 + (((game.x(t) * 180) / W) | 0);
  const enc = new ObsEncoder(180, 90);
  const tC = now();
  const cells = new CellFacts(game, { legalBit1: BIT1 });
  const ctorMs = now() - tC;
  const sp = game.config().numSpawnPhaseTurns();
  const nTurns = record.turns.length;

  const hash = { recordHashes: 0, ok: 0, bad: 0, missing: 0, first: null as any };
  for (const t of record.turns) if (t.hash !== undefined && t.hash !== null) hash.recordHashes++;
  let scannedTick = -1;
  const ownerG = (): Uint16Array => {
    if (scannedTick !== game.ticks()) { enc.scanTick(game); scannedTick = game.ticks(); }
    return (enc as any).ownerG as Uint16Array;
  };

  type Hook = (T: number, turn: any) => void;
  let pre: Hook = () => {};
  let post: Hook = () => {};
  let simMs = 0;

  const out: any = { mode, gid: info.gameID, commit8: COMMIT8, map: info.config.gameMap,
    gameMode: info.config.gameMode, W, H, turns: nTurns, bit1: BIT1, impl: process.env.CELLS_IMPL ?? "cells.ts" };

  // ───────────────────────── compare ─────────────────────────
  if (mode === "compare") {
    const nPost = Number(opt.ticks ?? 40), per = Number(opt.per ?? 3);
    const sel = new Set<number>();
    for (let k = 1; k <= 6; k++) sel.add(Math.min(nTurns - 1, Math.round((k * sp) / 6)));
    const a0 = sp + 2, a1 = nTurns - 1;
    if (a1 > a0) for (let k = 0; k < nPost; k++) sel.add(a0 + Math.round((k * (a1 - a0)) / Math.max(1, nPost - 1)));
    const acc = {
      pairs: 0, pairsBad: 0, ownerMism: 0, fracMism: 0, fracNonzero: 0,
      bitMism: new Array(8).fill(0), bitSet: new Array(8).fill(0), pairsWithBit: new Array(8).fill(0),
      examples: [] as any[], msCells: [] as number[], msRef: [] as number[],
      pairsAlive: 0, pairsSpawnPhase: 0, pairsTeamGame: 0,
    };
    const rs: RefStats = { spot: 0, spotMismatch: 0 };
    pre = (T) => {
      if (!sel.has(T)) return;
      const og = ownerG();
      const ps = humanCids.map((c) => game.playerByClientID(c)).filter((p): p is Player => !!p);
      const pick: Player[] = [];
      const add = (p?: Player) => { if (p && !pick.includes(p) && pick.length < per) pick.push(p); };
      add(ps.find((p) => p.isAlive() && p.units("Missile Silo" as any).some((s) => s.isActive() && !s.isInCooldown() && !s.isUnderConstruction())));
      add(ps.find((p) => p.isAlive() && p.units("Port" as any).some((s) => s.isActive() && !s.isUnderConstruction())));
      const alive = ps.filter((p) => p.isAlive());
      const pool = alive.length ? alive : ps;
      for (let k = 0; k < pool.length && pick.length < per; k++) add(pool[(T + k) % pool.length]);
      for (const p of pick) {
        let t0 = now();
        const a: Buffer = cells.compute(game, p, og);
        acc.msCells.push(now() - t0);
        t0 = now();
        const b = referenceCells(game, p, og, { legalBit1: BIT1 }, rs);
        acc.msRef.push(now() - t0);
        acc.pairs++;
        if (p.isAlive()) acc.pairsAlive++;
        if (game.inSpawnPhase()) acc.pairsSpawnPhase++;
        let bad = false;
        const seen = new Array(8).fill(false);
        for (let i = 0; i < N; i++) {
          if (a[2 * i] !== b[2 * i] || a[2 * i + 1] !== b[2 * i + 1]) { acc.ownerMism++; bad = true; }
          if (a[2 * N + i] !== b[2 * N + i]) { acc.fracMism++; bad = true; }
          if (b[2 * N + i] > 0) acc.fracNonzero++;
          const la = a[3 * N + i], lb = b[3 * N + i], x = la ^ lb;
          for (let k = 0; k < 8; k++) {
            if ((lb >> k) & 1) { acc.bitSet[k]++; seen[k] = true; }
            if ((x >> k) & 1) acc.bitMism[k]++;
          }
          if (x || a[2 * N + i] !== b[2 * N + i]) {
            bad = true;
            if (acc.examples.length < 8) acc.examples.push({ T, sid: p.smallID(), cell: i,
              legalCells: la, legalRef: lb, fracCells: a[2 * N + i], fracRef: b[2 * N + i] });
          }
        }
        for (let k = 0; k < 8; k++) if (seen[k]) acc.pairsWithBit[k]++;
        if (bad) acc.pairsBad++;
      }
      process.stderr.write(`compare T=${T} pairs=${acc.pairs} bad=${acc.pairsBad}\n`);
    };
    out.compare = acc;
    out.refStats = rs;
  }

  // ───────────────────────── hash ─────────────────────────
  let computes = 0;
  if (mode === "hash") {
    const withCompute = (opt.compute ?? "0") === "1";
    pre = (T, turn) => {
      if (!withCompute) return;
      const cids = new Set<string>();
      for (const it of (turn.intents ?? []) as any[]) {
        if (it.clientID && humanSet.has(it.clientID) && it.type !== "mark_disconnected") cids.add(it.clientID);
      }
      if (!cids.size) return;
      const og = ownerG();
      for (const cid of cids) {
        const p = game.playerByClientID(cid);
        if (!p) continue;
        cells.compute(game, p, og);
        computes++;
      }
    };
    out.withCompute = withCompute;
  }

  // ───────────────────────── mask ─────────────────────────
  if (mode === "mask") {
    // Typ -> Bit (DESIGN §6). Mit --bit1 0 pruefen Bauwerke und Port gegen Bit 0.
    const sb = BIT1 ? 1 : 0, pb = BIT1 ? 2 : 0;
    const BIT: Record<string, number> = {
      City: sb, "Defense Post": sb, "SAM Launcher": sb, "Missile Silo": sb, Factory: sb,
      Port: pb, Warship: 4, "Atom Bomb": 7, "Hydrogen Bomb": 7, MIRV: 7,
      boat: 3, move_warship: 5, spawn: 6,
    };
    const GROUP = (k: string) => STRUCT.has(k) && k !== "Port" ? "Bauwerk" : k === "Port" ? "Port"
      : NUKE.has(k) ? "Nuke" : k === "Warship" ? "Warship" : k;
    const STRUCT = new Set(["City", "Defense Post", "SAM Launcher", "Missile Silo", "Factory", "Port"]);
    const NUKE = new Set(["Atom Bomb", "Hydrogen Bomb", "MIRV"]);
    const spLimit = COMMIT8 === "115da032" ? sp : sp - 1;
    const lastSpawn = new Map<string, { T: number; idx: number }>();
    if (!game.config().isRandomSpawn()) {
      for (const t of record.turns) {
        if (t.turnNumber > spLimit) break;
        (t.intents ?? []).forEach((it: any, idx: number) => {
          if (it.type === "spawn" && humanSet.has(it.clientID)) lastSpawn.set(it.clientID, { T: t.turnNumber, idx });
        });
      }
    }
    interface S { kind: string; bit: number; T: number; cid: string; sid: number; click: number; buf: Buffer;
      clickBit: number; done: boolean; valid: boolean; res: number; unitIds?: number[]; late?: boolean }
    const all: S[] = [];
    const pending: S[] = [];
    const seenUnits = new Set<number>();
    const win = (k: string) => (k === "boat" ? 1 : NUKE.has(k) ? 3 : k === "move_warship" ? 0 : k === "spawn" ? 1 : 2);
    pre = (T, turn) => {
      const its = (turn.intents ?? []) as any[];
      its.forEach((it, idx) => {
        if (!it.clientID || !humanSet.has(it.clientID)) return;
        let kind: string | null = null, click = -1, late = false;
        const p = game.playerByClientID(it.clientID);
        if (!p) return;
        if (it.type === "spawn") {
          const ls = lastSpawn.get(it.clientID);
          if (ls && ls.T === T && ls.idx === idx) { kind = "spawn"; click = it.tile; }
          else if (COMMIT8 !== "115da032" && T > spLimit && !p.hasSpawned() && !game.config().isRandomSpawn()) { kind = "spawn"; click = it.tile; late = true; }
        } else if (!game.inSpawnPhase() && p.isAlive()) {
          if (it.type === "build_unit" && BIT[it.unit] !== undefined) { kind = it.unit; click = it.tile; }
          else if (it.type === "boat") { kind = "boat"; click = it.dst; }
          else if (it.type === "move_warship") { kind = "move_warship"; click = it.tile; }
        }
        if (kind === null || !(click >= 0 && click < W * H)) return;
        const buf: Buffer = cells.compute(game, p, ownerG());
        const bit = BIT[kind];
        const s: S = { kind, bit, T, cid: it.clientID, sid: p.smallID(), click, buf,
          clickBit: (buf[3 * N + cellOf(click)] >> bit) & 1, done: false, valid: false, res: -1,
          unitIds: it.unitIds, late };
        all.push(s); pending.push(s);
      });
    };
    post = (T) => {
      const ups = (cur?.updates?.[GameUpdateType.Unit] ?? []) as any[];
      for (const u of ups) {
        if (seenUnits.has(u.id)) continue;
        seenUnits.add(u.id);
        const s = pending.find((q) => !q.done && q.sid === u.ownerID && T >= q.T && T <= q.T + win(q.kind) && (
          (STRUCT.has(q.kind) && u.unitType === q.kind) ||
          (q.kind === "Warship" && u.unitType === "Warship" && u.warshipState?.patrolTile === q.click) ||
          (NUKE.has(q.kind) && u.unitType === q.kind && u.targetTile === q.click) ||
          (q.kind === "boat" && u.unitType === "Transport")));
        if (!s) continue;
        s.done = true; s.valid = true;
        s.res = STRUCT.has(s.kind) ? u.pos : s.kind === "boat" ? (u.targetTile ?? -1) : s.click;
      }
      for (const s of pending) {
        if (s.done) continue;
        if (s.kind === "move_warship" && T === s.T) {
          s.done = true;
          s.valid = (s.unitIds ?? []).some((id) => {
            const w = game.unit(id);
            return !!w && w.owner().smallID() === s.sid && (w as any).warshipState().patrolTile === s.click;
          });
          if (s.valid) s.res = s.click;
        } else if (s.kind === "spawn" && T === s.T + 1) {
          const p = game.playerByClientID(s.cid)!;
          s.done = true;
          s.valid = p.hasSpawned() && p.spawnTile() === s.click;
          if (s.valid) s.res = s.click;
        } else if (T > s.T + win(s.kind)) s.done = true;
      }
      for (let i = pending.length - 1; i >= 0; i--) if (pending[i].done) pending.splice(i, 1);
    };
    out.mask = () => {
      const by: Record<string, any> = {};
      const mk = (bit: number) => ({ bit, n: 0, clickBit: 0, valid: 0, clickBitValid: 0, resBitValid: 0,
        resKnown: 0, resOtherCell: 0, late: 0, violEx: [] as any[] });
      // Gruppensummen: Nukes und Spawn getrennt, "ohne_nuke_spawn" = alles andere
      const grp: Record<string, any> = {};
      for (const s of all) {
        const keys = [GROUP(s.kind), s.kind === "spawn" || NUKE.has(s.kind) ? "" : "ohne_nuke_spawn"].filter(Boolean);
        for (const g of keys) {
          const b = grp[g] ??= mk(s.bit);
          b.n++; b.clickBit += s.clickBit;
          if (!s.valid) continue;
          b.valid++; b.clickBitValid += s.clickBit;
          if (s.res >= 0) { b.resKnown++; b.resBitValid += (s.buf[3 * N + cellOf(s.res)] >> s.bit) & 1; }
        }
      }
      for (const g of Object.values(grp)) delete g.violEx;
      out.maskGroups = grp;
      for (const s of all) {
        const b = by[s.kind] ??= mk(s.bit);
        b.n++; b.clickBit += s.clickBit; if (s.late) b.late++;
        if (!s.valid) continue;
        b.valid++; b.clickBitValid += s.clickBit;
        if (s.res >= 0) {
          b.resKnown++;
          const rc = cellOf(s.res), cc = cellOf(s.click);
          if (rc !== cc) b.resOtherCell++;
          const rb = (s.buf[3 * N + rc] >> s.bit) & 1;
          b.resBitValid += rb;
          if (!rb && b.violEx.length < 6) b.violEx.push({ T: s.T, sid: s.sid, click: [game.x(s.click), game.y(s.click)],
            res: [game.x(s.res), game.y(s.res)], legalRes: s.buf[3 * N + rc], legalClick: s.buf[3 * N + cc] });
        }
      }
      return by;
    };
  }

  // ───────────────────────── cost ─────────────────────────
  if (mode === "cost") {
    const thin = opt.thin;
    if (!thin) throw new Error("--thin <meta.zst> fehlt");
    const lines = zstdDecompressSync(fs.readFileSync(thin)).toString("utf8").split("\n").filter(Boolean);
    const SPATIAL = new Set(["build_unit", "boat", "move_warship"]);
    const byTurn = new Map<number, string[]>();
    let spatialSamples = 0;
    for (const l of lines) {
      const m = JSON.parse(l);
      if (!m.intent || !SPATIAL.has(m.intent.type)) continue;
      spatialSamples++;
      if (!byTurn.has(m.turn)) byTurn.set(m.turn, []);
      byTurn.get(m.turn)!.push(m.clientID);
    }
    const acc = { metaLines: lines.length, spatialSamples, calls: 0, pairs: 0, skipped: 0,
      pairMs: [] as number[], cachedMs: 0, zMs: 0, zBytes: [] as number[], scanMs: 0 };
    pre = (T) => {
      const cids = byTurn.get(T);
      if (!cids) return;
      const ts = now();
      const og = ownerG();
      acc.scanMs += now() - ts;
      const done = new Set<string>();
      for (const cid of cids) {
        const p = game.players().find((q) => q.clientID() === cid);
        if (!p || !p.isAlive()) { acc.skipped++; continue; }
        const t0 = now();
        const buf: Buffer = cells.compute(game, p, og);
        const dt = now() - t0;
        acc.calls++;
        if (done.has(cid)) { acc.cachedMs += dt; continue; }
        done.add(cid);
        acc.pairs++; acc.pairMs.push(dt);
        const t1 = now();
        const z = zstdCompressSync(buf);
        acc.zMs += now() - t1;
        acc.zBytes.push(z.length);
      }
    };
    out.cost = () => ({
      metaLines: acc.metaLines, spatialSamples: acc.spatialSamples, calls: acc.calls, pairs: acc.pairs, skipped: acc.skipped,
      ms_median: r3(pct(acc.pairMs, 0.5)), ms_p95: r3(pct(acc.pairMs, 0.95)), ms_max: r3(Math.max(0, ...acc.pairMs)),
      ms_sum: r3(acc.pairMs.reduce((x, y) => x + y, 0) + acc.cachedMs), ms_cached: r3(acc.cachedMs),
      zstd_ms_sum: r3(acc.zMs), scan_ms_sum: r3(acc.scanMs),
      kb_mean: r3(acc.zBytes.reduce((x, y) => x + y, 0) / Math.max(1, acc.zBytes.length) / 1000),
      kb_median: r3(pct(acc.zBytes, 0.5) / 1000), kb_p95: r3(pct(acc.zBytes, 0.95) / 1000),
    });
  }

  if (!["compare", "hash", "mask", "cost"].includes(mode)) throw new Error(`unbekannter Modus ${mode}`);

  // ───────────────────────── Replay ─────────────────────────
  const tAll = now();
  for (const turn of record.turns) {
    const T = turn.turnNumber;
    if (game.ticks() !== T) throw new Error(`ticks ${game.ticks()} != turn ${T}`);
    pre(T, turn);
    runner.addTurn(turn);
    const t0 = now();
    runner.executeNextTick();
    simMs += now() - t0;
    if (fatal) throw new Error(`tick-Fehler T=${T}: ${fatal}`);
    for (const h of (cur?.updates?.[GameUpdateType.Hash] ?? []) as any[]) {
      if (h.tick !== T) continue;
      const exp = record.turns[T]?.hash;
      if (exp === undefined || exp === null) hash.missing++;
      else if (exp === h.hash) hash.ok++;
      else { hash.bad++; if (!hash.first) hash.first = { tick: T, exp, got: h.hash }; }
    }
    post(T, turn);
  }
  out.hash = hash;
  out.computes = computes;
  out.sim_ms = Math.round(simMs);
  out.wall_ms = Math.round(now() - tAll);
  out.ctor_ms = r3(ctorMs);
  out.cellStats = cells.stats;
  if (typeof out.mask === "function") out.mask = out.mask();
  if (typeof out.cost === "function") out.cost = out.cost();
  if (out.compare) { out.compare.msCells = { median: r3(pct(out.compare.msCells, 0.5)), max: r3(Math.max(0, ...out.compare.msCells)) };
    out.compare.msRef = { median: r3(pct(out.compare.msRef, 0.5)), max: r3(Math.max(0, ...out.compare.msRef)) }; }
  process.stdout.write(JSON.stringify(out) + "\n");
}

main().catch((e) => { console.error(e?.stack ?? e); process.exit(1); });

/**
 * Materialisierer v2, Kern: eine Partie → Dateien in <outdir> (DESIGN §2–§5, §8).
 *
 *   npx tsx materializer/src/materialize.ts <record.json> <outdir>
 *
 * Ein Prozess pro Partie: statischer Engine-Zustand würde sonst in die nächste
 * Partie hinüberleben (ENGINE_FACTS §11 F2). Die Liste führt dispatch.ts.
 *
 * Umgebung (Standard in Klammern):
 *   NOOP_EVERY (200)  jeder n-te intentlose Zug je Spieler wird Nichtstun-Sample, w = n
 *   MERGE_TICKS (30)  Folgeklicks auf dasselbe Angriffsziel mit t − t0 < n zusammenfassen
 *   CELLS (1)         Zellfakten (cells.ts). 0 = das Modul wird nicht einmal geladen
 *   LEGAL_BIT1 (1)    an cells.ts durchgereicht
 *   TIER1_PCT (100)   Tier-1-Auswahl (tier1.ts). 0 = das Modul wird nicht einmal geladen,
 *                     auch Val-Partien bekommen dann kein Tier 1
 *   RETRY_ERR (0)     .err-Partien neu rechnen
 *   FORCE (0)         auch fertige Partien neu rechnen (Tests)
 *   MAT_VERSION       Repo-Commit des Materialisierers, landet in hdr.json
 *   ENGINE_COMMIT     Commit des Engine-Baums (sonst Ordnername von <root> = w/<sha8>)
 *   FAULT_INJECT (0)  Test: der n-te emit wirft (Gleichschritt bei Fehlern, §3)
 *   FAULT_TICK (-1)   Test: Zug T wird nicht ausgeführt, sondern als Tick-Fehler behandelt (§3)
 *   ENGINE_LOG (0)    1 = console.warn/debug der Engine nicht stummschalten
 *
 * Exit: 0 = .ok, .none oder schon fertig; 1 = .err geschrieben; 2 = falscher Aufruf.
 *
 * Die Beobachtung ist exakt die des alten Materialisierers (env/materialize.ts):
 * gleiche Kodierung, gleiche Quantisierung, gleiches zstd. Daran hängt das Byte-Tor
 * im Kanarienlauf. obs.ts wird nicht verändert.
 *
 * Tick-Konvention (DESIGN §2): "Tick t" ist der Zustand mit game.ticks() == t, also
 * vor executeNextTick für Zug t. Ein Sample mit tick t sieht genau diesen Zustand.
 */
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";
import { crc32, zstdCompressSync, createZstdCompress } from "zlib";
import { Config } from "../../vendor/openfront/src/core/configuration/Config";
import { Executor } from "../../vendor/openfront/src/core/execution/ExecutionManager";
import { Game, Player, PlayerInfo, PlayerType } from "../../vendor/openfront/src/core/game/Game";
import { createGame } from "../../vendor/openfront/src/core/game/GameImpl";
import { GameUpdateType, GameUpdateViewData } from "../../vendor/openfront/src/core/game/GameUpdates";
import { createNationsForGame } from "../../vendor/openfront/src/core/game/NationCreation";
import { loadTerrainMap } from "../../vendor/openfront/src/core/game/TerrainMapLoader";
import { GameRunner } from "../../vendor/openfront/src/core/GameRunner";
import { PseudoRandom } from "../../vendor/openfront/src/core/PseudoRandom";
import { GameRecord, GameRecordSchema, GameStartInfo } from "../../vendor/openfront/src/core/Schemas";
import { decompressGameRecord, simpleHash, toWireGameStartInfo } from "../../vendor/openfront/src/core/Util";
import { NodeGameMapLoader } from "../../vendor/openfront/tests/perf/fullgame/NodeGameMapLoader";
import { BlockFile, cleanStale, commitGame, gamePath, isDone, writeAtomic, writeSynced } from "./io";
import { NUM_CHANNELS, ObsEncoder } from "./obs";
import { clickOf, invalidRes, isSpatial, needsRes, Pending, Resolver } from "./res";
import { rssMaxMb, Timing } from "./timing";

const GW = 180, GH = 90, MAPLEN = NUM_CHANNELS * GW * GH;
const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = process.env.MAT_ROOT ?? path.resolve(HERE, "../..");
const ENGINE = path.join(ROOT, "vendor/openfront");
const NOOP_INTENT = { type: "no_op" }; // wie bisher: actions.encode macht daraus A.NO_OP

/**
 * Spawn-Regeln je Engine-Commit (ENGINE_FACTS §3).
 *   lastOffset: letzter wirksamer Spawn-Zug = numSpawnPhaseTurns() + lastOffset.
 *     88/8b: SpawnExecution.tick in T+1 muss noch in der Phase liegen → ≤ 299.
 *     115:   queuedDuringSpawnPhase wird bei init in T gesetzt → ≤ 300.
 *   late: Menschen, die nie gespawnt haben, steigen nach der Phase noch ein (nur 88/8b).
 * Ein Commit ohne Eintrag ergibt .err: die Grenze darf nicht geraten werden.
 */
const SPAWN_RULES: Record<string, { lastOffset: number; late: boolean }> = {
  "88cc95d8": { lastOffset: -1, late: true },
  "8b45be57": { lastOffset: -1, late: true },
  "115da032": { lastOffset: 0, late: false },
};

type Kind = "act" | "noop" | "spawn";
type ErrKind = Kind | "res" | "cells" | "tier1";

/** Fehler vor dem ersten Zug (Record, Commit, Setup) → .err (DESIGN §3). */
class GameError extends Error {}

function envInt(name: string, def: number): number {
  const v = process.env[name];
  if (v === undefined || v === "") return def;
  const n = Number(v);
  if (!Number.isFinite(n)) throw new Error(`${name}=${v} ist keine Zahl`);
  return n;
}

function engineCommitOf(root: string): string {
  if (process.env.ENGINE_COMMIT) return process.env.ENGINE_COMMIT.slice(0, 8);
  const base = path.basename(root);
  if (/^[0-9a-f]{8}$/.test(base)) return base;
  // Rückfall: HEAD des Worktrees (.git ist dort eine Datei "gitdir: ...")
  try {
    const dotgit = path.join(root, "vendor/openfront/.git");
    let gitdir = dotgit;
    if (fs.statSync(dotgit).isFile()) gitdir = fs.readFileSync(dotgit, "utf8").replace(/^gitdir:\s*/, "").trim();
    const head = fs.readFileSync(path.join(gitdir, "HEAD"), "utf8").trim();
    if (/^[0-9a-f]{40}$/.test(head)) return head.slice(0, 8);
  } catch {}
  return "";
}

interface Params {
  NOOP_EVERY: number;
  MERGE_TICKS: number;
  TIER1_PCT: number;
  CELLS: number;
  LEGAL_BIT1: number;
}

/** Ein geschriebenes Sample. Die Metazeile bleibt Objekt, solange noch Felder nachkommen. */
interface Sample {
  tick: number;
  kind: Kind;
  itype: string;
  spatial: boolean;
  mapsOff: number;
  cellsOff: number;             // -1 ohne Zellblock
  obj: any | null;
  line: string | null;
  hold: number;                 // offene Nachträge: eigener Zug (w_tick), Angriffsfenster, res
  wBase: number;                // w_tick aus dem eigenen Zug
  contrib: Array<[number, number]> | null; // w_tick-Beiträge zusammengefasster Züge [Zug, Wert]
}

interface Ctx {
  sid: number;
  allies: number[];
  team: string | null;
  base: Record<string, unknown>; // alte Felder mapW..opps, in alter Reihenfolge
  zblock: Buffer;
  cellZ?: Buffer | null;         // null = compute ist in diesem Tick gescheitert
  cellErr?: string;
}

interface RunResult {
  status: "ok" | "none";
  samples: number;
  ms: number;
  note: string;
}

async function run(file: string, outdir: string, gid: string, P: Params): Promise<RunResult> {
  const tm = new Timing();
  let t = performance.now();

  // ── 1 Record ─────────────────────────────────────────────────────────────
  const raw = JSON.parse(fs.readFileSync(file, "utf8"));
  const parsed = GameRecordSchema.safeParse(raw);
  const record: GameRecord = decompressGameRecord(parsed.success ? parsed.data : (raw as GameRecord));
  const info = record.info;
  if (info.gameID !== gid) throw new GameError(`gameID ${info.gameID} passt nicht zum Dateinamen ${gid}`);

  // ── 2 Commit ─────────────────────────────────────────────────────────────
  const commit = String((record as any).gitCommit ?? "");
  const engineCommit = engineCommitOf(ROOT);
  if (!engineCommit || commit.slice(0, 8) !== engineCommit) {
    throw new GameError(`Record-Commit ${commit.slice(0, 8) || "?"} ≠ Engine-Baum ${engineCommit || "?"}`);
  }
  const rule = SPAWN_RULES[engineCommit];
  if (!rule) throw new GameError(`keine Spawn-Regel für Commit ${engineCommit} (ENGINE_FACTS §3 nachtragen)`);

  const winners = new Set<string>();
  const wi: any = info.winner;
  if (Array.isArray(wi) && wi.length > 1) {
    if (wi[0] === "team") for (const x of wi.slice(2)) { if (typeof x === "string") winners.add(x); }
    else if (wi[0] === "player" && typeof wi[1] === "string") winners.add(wi[1]);
  }

  // ── 4 Engine, wie im alten Materialisierer ──────────────────────────────
  const gameStart: GameStartInfo = toWireGameStartInfo({
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
  let gu: GameUpdateViewData | null = null;
  const runner = new GameRunner(game, new Executor(game, gameStart.gameID, undefined,
    gameStart.tribes?.map((x) => x.name)), (u: any) => {
      if ("errMsg" in u) fatal = u.errMsg; else gu = u;
    });
  runner.init();

  const W = game.width(), H = game.height();
  const humanClient = new Set(gameStart.players.map((p) => p.clientID));
  const turns = record.turns;
  const nspt = config.numSpawnPhaseTurns();
  const randomSpawn = config.isRandomSpawn();
  const singleplayer = info.config.gameType === "Singleplayer";
  // Singleplayer: Die Phase endet mit dem Spawn des Menschen, die Grenze aus
  // SPAWN_RULES gilt dort nicht. Im Inventar gibt es keine solchen Records.
  const spawnSamples = !randomSpawn && !singleplayer;
  const lastEff = nspt + rule.lastOffset;
  // SpawnTimerExecution beendet die Phase zu Beginn von Zug nspt+1; der erste Zustand
  // ohne Phase ist also Tick nspt+2 (Tick-Konvention). Tier 1 braucht den Wert vor dem
  // Lauf; hdr.json bekommt den beobachteten.
  const spawnEndPred = nspt + 2;

  // ── 3 Vorab-Scan ─────────────────────────────────────────────────────────
  // letzter wirksamer Spawn-Klick je Mensch; Kandidaten für die .chk-Ticks (§5.3):
  // Züge nach nspt+1 mit mindestens einem Menschen-Intent (ohne mark_disconnected)
  const finalSpawn = new Map<string, { T: number; k: number; intent: any }>();
  const candTicks: number[] = [];
  let recordHasHashes = false;
  for (let T = 0; T < turns.length; T++) {
    if (turns[T].hash !== undefined && turns[T].hash !== null) recordHasHashes = true;
    const its = (turns[T].intents ?? []) as any[];
    let human = false;
    for (let k = 0; k < its.length; k++) {
      const i = its[k];
      if (!i.clientID || !humanClient.has(i.clientID) || i.type === "mark_disconnected") continue;
      human = true;
      if (i.type === "spawn" && spawnSamples && T <= lastEff) finalSpawn.set(i.clientID, { T, k, intent: i });
    }
    if (human && T > nspt + 1) candTicks.push(T);
  }
  const spawnAt = new Map<number, Array<{ cid: string; k: number; intent: any }>>();
  const lastSpawnTurn = new Map<string, number>();
  for (const [cid, f] of finalSpawn) {
    const l = spawnAt.get(f.T) ?? [];
    l.push({ cid, k: f.k, intent: f.intent });
    spawnAt.set(f.T, l);
    lastSpawnTurn.set(cid, f.T);
  }
  for (const l of spawnAt.values()) l.sort((a, b) => a.k - b.k);

  // ── Hooks (§8). Abgeschaltet werden die Module gar nicht erst geladen. ──
  const errors: Record<ErrKind, number> = { act: 0, noop: 0, spawn: 0, res: 0, cells: 0, tier1: 0 };
  let firstError: string | null = null;
  const countErr = (k: ErrKind, e: any, where: string) => {
    errors[k]++;
    if (!firstError) firstError = `${k} ${where}: ${e?.message ?? e}`;
  };
  const tmpFiles: BlockFile[] = [];
  let t1: any = null;
  let cells: any = null;
  let cellBytes = 0;
  const abortT1 = () => { if (t1) { try { t1.abort(); } catch {} t1 = null; } };
  t = tm.add("setup", t);
  try {
    if (P.TIER1_PCT > 0) {
      const mod = await import("./tier1");
      if (mod.tier1Selected(gid, P.TIER1_PCT)) {
        try {
          t1 = new mod.Tier1Writer({ dir: outdir, gid, game, spawnEndTick: spawnEndPred,
            chkTicks: mod.chkTicksFor(gid, candTicks) });
        } catch (e) { countErr("tier1", e, "Konstruktor"); t1 = null; }
      }
    }
    t = tm.add("tier1", t);
    if (P.CELLS) {
      const mod = await import("./cells");
      cellBytes = mod.CELL_BYTES;
      try { cells = new mod.CellFacts(game, { legalBit1: !!P.LEGAL_BIT1 }); }
      catch (e) { countErr("cells", e, "Konstruktor"); cells = null; }
    }
    t = tm.add("cells", t);

    const maps = new BlockFile(gamePath(outdir, gid, "maps"));
    tmpFiles.push(maps);
    const cellsF = cells ? new BlockFile(gamePath(outdir, gid, "cells")) : null;
    if (cellsF) tmpFiles.push(cellsF);

    // ── Beobachtung und emit ─────────────────────────────────────────────
    const enc = new ObsEncoder(GW, GH);
    const obsBuf = new Float32Array(MAPLEN);
    const u8 = new Uint8Array(MAPLEN);
    const resolver = new Resolver(W, H);
    const samples: Sample[] = [];
    const ctxCache = new Map<string, Ctx>();
    const FAULT = envInt("FAULT_INJECT", 0);
    const FAULT_TICK = envInt("FAULT_TICK", -1);
    let emits = 0, scannedTick = -1;
    const mergedSamples = new Set<Sample>();

    // Metazeilen werden erst Text, wenn nichts mehr nachkommt; das passiert nur
    // an sicheren Stellen (flushReady), damit kein Nachtrag auf Text trifft.
    const ready: Sample[] = [];
    const release = (s: Sample) => { if (--s.hold === 0) ready.push(s); };
    const flushReady = () => {
      if (!ready.length) return;
      const t0 = performance.now();
      for (const s of ready) if (s.hold === 0 && s.obj) { s.line = JSON.stringify(s.obj); s.obj = null; }
      ready.length = 0;
      tm.add("json", t0);
    };
    /** Nachtrag in eine Metazeile, auch wenn sie schon Text ist (nur beim Schnitt). */
    const patch = (s: Sample, fn: (o: any) => void) => {
      if (s.obj) { fn(s.obj); return; }
      const o = JSON.parse(s.line!);
      fn(o);
      s.line = JSON.stringify(o);
    };

    /** Kontext + Karte je Spieler und Tick einmal (DESIGN §2). Wie alt: emit() in env/materialize.ts. */
    const buildCtx = (player: Player, T: number): Ctx => {
      let t0 = performance.now();
      if (scannedTick !== T) { enc.scanTick(game); scannedTick = T; t0 = tm.add("scan", t0); }
      const v = enc.encodeVec(game, player, 24);
      const oppIds = v.opponents.map((o) => {
        const pl = game.playerBySmallID(o.id);
        return pl?.isPlayer() ? pl.id() : null;
      });
      const opps = v.opponents.map((o) => {
        const pl = game.playerBySmallID(o.id);
        return { ...o, user: pl?.isPlayer() ? pl.name() : null, clan: pl?.isPlayer() ? pl.clanTag() : null };
      });
      const base = {
        mapW: W, mapH: H, troops: player.troops(), gold: Number(player.gold()),
        oppIds, ownUnitIds: player.units().map((u: any) => u.id()),
        ownAttackIds: player.outgoingAttacks().map((a: any) => a.id()),
        own: v.own, opps,
      };
      const allies = player.allies().map((a: any) => a.smallID());
      t0 = tm.add("vec", t0);
      enc.encodeMap(player, new Set(allies), obsBuf);
      for (let k = 0; k < MAPLEN; k++) {
        let x = obsBuf[k]; if (x < -1) x = -1; else if (x > 1) x = 1;
        u8[k] = Math.round((x + 1) * 127.5);
      }
      const zblock = zstdCompressSync(Buffer.from(u8.buffer, 0, MAPLEN));
      tm.add("map", t0);
      return { sid: player.smallID(), allies, team: (player.team() ?? null) as string | null, base, zblock };
    };

    /** Zellblock des Spielers in diesem Tick, einmal gerechnet, für alle seine Samples. */
    const cellBlock = (ctx: Ctx, player: Player): Buffer => {
      if (ctx.cellZ !== undefined) {
        if (ctx.cellZ === null) throw new Error(ctx.cellErr);
        return ctx.cellZ;
      }
      const t0 = performance.now();
      try {
        const rawCells: Buffer = cells.compute(game, player, (enc as any).ownerG);
        if (rawCells.length !== cellBytes) throw new Error(`CellFacts.compute lieferte ${rawCells.length} statt ${cellBytes} Byte`);
        ctx.cellZ = zstdCompressSync(rawCells);
        return ctx.cellZ;
      } catch (e: any) {
        ctx.cellZ = null;
        ctx.cellErr = e?.message ?? String(e);
        throw e;
      } finally {
        tm.add("cells", t0);
      }
    };

    // w_tick (§4): eigene Samples je Spieler in diesem Zug, Fenster mit Folgeklicks
    const turnOwn = new Map<string, Sample[]>();
    const turnMerge = new Map<string, Set<Sample>>();

    /**
     * Ein Sample. Erst alles bauen (Kontext, Karte, Metaobjekt, res, Zellblock),
     * dann schreiben: Wirft etwas beim Bauen, entsteht nichts, .maps und Metazeilen
     * bleiben im Gleichschritt (§3). Schreibfehler selbst beenden den Prozess (.err).
     */
    const emit = (player: Player, cid: string, T: number, intent: any, w: number, kind: Kind,
                  extra: Record<string, number> | null): Sample | null => {
      emits++;
      let ctx: Ctx | undefined;
      let obj: any;
      let cz: Buffer | null = null;
      let pend: Pending | null = null;
      const spatial = isSpatial(intent);
      try {
        ctx = ctxCache.get(cid);
        if (!ctx) { ctx = buildCtx(player, T); ctxCache.set(cid, ctx); }
        if (FAULT && emits === FAULT) throw new Error(`FAULT_INJECT ${FAULT}`);
        obj = { turn: T, clientID: cid, ...ctx.base, intent, w, win: winners.has(cid) ? 1 : 0,
          tick: game.ticks(), sid: ctx.sid, allies: ctx.allies, team: ctx.team, cfg: info.config, kind };
        if (intent.type === "attack") { obj.merged = 0; obj.merged_turns = []; }
        obj.w_tick = kind === "noop" ? w : 0; // act/spawn: am Zugende (1/Zahl der Samples)
        if (spatial) {
          const click = clickOf(intent);
          obj.click = click;
          obj.dst_owner = game.isValidRef(click) ? game.map().ownerID(click) : -1;
        }
        if (needsRes(intent)) {
          const t0 = performance.now();
          try { pend = resolver.prepare(obj, intent, ctx.sid, T, game, obj.dst_owner ?? -1); }
          catch (e) { countErr("res", e, `t${T} ${cid}`); pend = null; }
          tm.add("res", t0);
        }
        if (spatial) {
          obj.cell = -1;
          if (cells) {
            try { cz = cellBlock(ctx, player); }
            catch (e) { countErr("cells", e, `t${T} ${cid}`); cz = null; }
          }
        }
        if (extra) Object.assign(obj, extra);
      } catch (e) {
        countErr(kind, e, `t${T} ${cid}`);
        ctxCache.delete(cid);
        return null;
      }
      // schreiben (ausserhalb des try)
      const t0 = performance.now();
      const s: Sample = { tick: T, kind, itype: intent.type, spatial, mapsOff: maps.size, cellsOff: -1,
        obj, line: null, hold: 1, wBase: obj.w_tick, contrib: null };
      maps.write(ctx.zblock);
      if (cz && cellsF) { s.cellsOff = cellsF.size; obj.cell = cellsF.blocks; cellsF.write(cz); }
      samples.push(s);
      tm.add("io", t0);
      if (kind !== "noop") {
        const l = turnOwn.get(cid);
        if (l) l.push(s); else turnOwn.set(cid, [s]);
      }
      if (pend) {
        s.hold++;
        resolver.register(pend, s, () => release(s));
      }
      return s;
    };

    // ── Nichtstun-Phase, Angriffsfenster, Verbindungsstatus ─────────────
    const phases = new Map<string, number>();
    const phaseOf = (cid: string) => {
      let ph = phases.get(cid);
      if (ph === undefined) { ph = Math.abs(simpleHash(cid)) % P.NOOP_EVERY; phases.set(cid, ph); }
      return ph;
    };
    const disconnected = new Set<string>();
    const attackWin = new Map<string, { start: number; s: Sample }>();
    const winQueue: Array<{ key: string; start: number; s: Sample }> = [];
    let winHead = 0;

    let hashChecked = 0, hashMissing = 0, hashOrphan = 0, lastOkTick = -1, ticks = 0;
    let digest = 0;
    const hbuf = Buffer.alloc(12);
    let desync: { tick: number; last_ok_tick: number } | null = null;
    let tickError: { tick: number; msg: string } | null = null;
    let spawnEndTick: number | null = null;

    // ── 5–7 Replay ───────────────────────────────────────────────────────
    for (let T = 0; T < turns.length; T++) {
      const turn = turns[T];
      const intents = (turn.intents ?? []) as any[];

      // abgelaufene Angriffsfenster freigeben (FIFO: Fenster öffnen in Zugfolge)
      while (winHead < winQueue.length && winQueue[winHead].start + P.MERGE_TICKS <= T) {
        const q = winQueue[winHead++];
        if (attackWin.get(q.key)?.s === q.s) attackWin.delete(q.key);
        release(q.s);
      }
      if (winHead > 4096 && winHead * 2 > winQueue.length) { winQueue.splice(0, winHead); winHead = 0; }

      for (const i of intents) {
        if (i.type === "mark_disconnected" && i.clientID) {
          if (i.isDisconnected === false) disconnected.delete(i.clientID);
          else disconnected.add(i.clientID);
        }
      }
      ctxCache.clear();
      turnOwn.clear();
      turnMerge.clear();
      const acted = new Set<string>();

      // Spawn: letzter wirksamer Klick in der Phase
      const fin = spawnAt.get(T);
      if (fin) for (const f of fin) {
        const p = game.playerByClientID(f.cid);
        if (p) emit(p, f.cid, T, f.intent, 1, "spawn", { spawn_final: 1 });
      }
      // Späte Spawns (88/8b). Je Mensch höchstens ein offener: hasSpawned() wird erst
      // in T+1 gesetzt, aufgelöst ist der Spawn nach Zug T+1. Das gilt auch für den
      // letzten Spawn in der Phase (lastSpawnTurn ist damit vorbelegt).
      if (spawnSamples && rule.late && T > lastEff) {
        for (const i of intents) {
          if (i.type !== "spawn" || !i.clientID || !humanClient.has(i.clientID)) continue;
          const cid = i.clientID;
          const p = game.playerByClientID(cid);
          if (!p || p.hasSpawned()) continue;
          const last = lastSpawnTurn.get(cid);
          if (last !== undefined && last >= T - 1) continue;
          lastSpawnTurn.set(cid, T);
          acted.add(cid);
          emit(p, cid, T, i, 1, "spawn", { spawn_late: 1 });
        }
      }

      if (!game.inSpawnPhase()) {
        // Züge (gleiches Tor wie bisher)
        for (const i of intents) {
          if (!i.clientID || !humanClient.has(i.clientID)) continue;
          if (i.type === "mark_disconnected" || i.type === "spawn") continue;
          const cid = i.clientID;
          acted.add(cid);
          const p = game.playerByClientID(cid);
          if (!p || !p.isAlive()) continue;
          if (i.type === "attack" && P.MERGE_TICKS > 0) {
            const key = `${cid} ${JSON.stringify(i.targetID ?? null)}`;
            const win = attackWin.get(key);
            if (win && T - win.start < P.MERGE_TICKS) {
              const o = win.s.obj;          // Fenster offen ⇒ Zeile noch Objekt
              o.w += 1; o.merged += 1; o.merged_turns.push(T);
              mergedSamples.add(win.s);
              const m = turnMerge.get(cid);
              if (m) m.add(win.s); else turnMerge.set(cid, new Set([win.s]));
              continue;
            }
            const s = emit(p, cid, T, i, 1, "act", null);
            if (s) {
              s.hold++;
              attackWin.set(key, { start: T, s });
              winQueue.push({ key, start: T, s });
            }
            continue;
          }
          emit(p, cid, T, i, 1, "act", null);
        }
        // Nichtstun
        if (P.NOOP_EVERY > 0) for (const p of game.players()) {
          if (!p.isAlive()) continue;
          const cid = p.clientID();
          if (!cid || !humanClient.has(cid) || acted.has(cid) || disconnected.has(cid)) continue;
          if ((T + phaseOf(cid)) % P.NOOP_EVERY !== 0) continue;
          emit(p, cid, T, NOOP_INTENT, P.NOOP_EVERY, "noop", null);
        }
      }

      // w_tick: die Samples eines Spieler-Ticks teilen sich 1. Hat der Spieler in
      // diesem Zug nur zusammengefasste Klicks, teilen sich die k betroffenen Fenster
      // die 1 (bei k = 1 genau DESIGN §4; k > 1 siehe Bericht).
      for (const list of turnOwn.values()) {
        const v = 1 / list.length;
        for (const s of list) { s.obj.w_tick = v; s.wBase = v; release(s); }
      }
      for (const [cid, set] of turnMerge) {
        if (turnOwn.has(cid)) continue;
        const v = 1 / set.size;
        for (const s of set) { s.obj.w_tick += v; (s.contrib ??= []).push([T, v]); }
      }
      // noop-Samples dieses Zugs haben ihren Zug-Halt noch
      for (let k = samples.length - 1; k >= 0 && samples[k].tick === T; k--) {
        if (samples[k].kind === "noop") release(samples[k]);
      }
      flushReady();

      // ── Tick ──
      t = performance.now();
      const wasSpawn = game.inSpawnPhase();
      gu = null;
      fatal = undefined;
      runner.addTurn(turn);
      let stepped = false;
      let thrown: any = null;
      if (T === FAULT_TICK) thrown = new Error(`FAULT_TICK ${T}`); // Test: Tick-Fehler in Zug T
      else try { stepped = runner.executeNextTick(); } catch (e) { thrown = e; }
      t = tm.add("sim", t);
      if (fatal !== undefined || thrown || !stepped || !gu) {
        tickError = { tick: T, msg: String(fatal ?? thrown?.message ?? thrown ?? "executeNextTick lieferte kein Update") };
        break;
      }
      ticks++;
      const g: GameUpdateViewData = gu;
      if (wasSpawn && !game.inSpawnPhase()) spawnEndTick = T + 1;

      // Hash (§2.7): HashUpdate.tick ist T, gu.tick wäre T+1.
      // digest = CRC32 über (u32 LE Tick ‖ f64 LE Hash) je bestätigtem Hash, in Folge.
      const hs = (g.updates[GameUpdateType.Hash] ?? []) as any[];
      for (const h of hs) {
        if (h.tick !== T) hashOrphan++;
        const rh = turns[h.tick]?.hash;
        if (rh === undefined || rh === null) hashMissing++;
        else if (rh === h.hash) {
          hashChecked++;
          lastOkTick = h.tick;
          hbuf.writeUInt32LE(h.tick >>> 0, 0);
          hbuf.writeDoubleLE(h.hash, 4);
          digest = crc32(hbuf, digest);
        } else if (!desync) desync = { tick: h.tick, last_ok_tick: lastOkTick };
      }
      if (hs.length === 0 && turn.hash !== undefined && turn.hash !== null) hashOrphan++;
      if (desync) break;

      t = performance.now();
      resolver.afterTick(T, g, game);
      t = tm.add("res", t);
      if (t1) {
        try { t1.onTick(T, g, game); }
        catch (e) { countErr("tier1", e, `onTick t${T}`); abortT1(); }
        tm.add("tier1", t);
      }
      flushReady();
    }

    // ── 8 Ende ───────────────────────────────────────────────────────────
    t = performance.now();
    resolver.finish();
    tm.add("res", t);
    for (const s of samples) if (s.hold > 0) s.hold = 0;

    // Schnitt bei last_ok (§2.7): bei Desync und bei Tick-Fehler.
    let cut: number | null = null;
    if (desync) cut = desync.last_ok_tick;
    else if (tickError) cut = recordHasHashes ? lastOkTick : tickError.tick - 1;
    let mergedClicks = 0;
    if (cut !== null) {
      const c = cut;
      let keep = samples.findIndex((s) => s.tick > c);
      if (keep < 0) keep = samples.length;
      // Folgeklicks und ihre w_tick-Beiträge nur bis last_ok
      for (const s of mergedSamples) {
        if (s.tick > c) continue;
        patch(s, (o) => {
          o.merged_turns = o.merged_turns.filter((x: number) => x <= c);
          o.merged = o.merged_turns.length;
          o.w = 1 + o.merged;
          let wt = s.wBase;
          for (const [T, v] of s.contrib ?? []) if (T <= c) wt += v;
          o.w_tick = wt;
        });
      }
      // res, deren Fenster über last_ok hinausreicht → ungültig
      for (const p of resolver.cut(c)) {
        const s = p.ref as Sample;
        if (s.tick <= c) patch(s, (o) => Object.assign(o, invalidRes(p.cls, p.click)));
      }
      if (keep < samples.length) {
        maps.truncate(samples[keep].mapsOff, keep);
        if (cellsF) {
          const firstCell = samples.slice(keep).find((s) => s.cellsOff >= 0);
          if (firstCell) cellsF.truncate(firstCell.cellsOff, samples.slice(0, keep).filter((s) => s.cellsOff >= 0).length);
        }
      }
      samples.length = keep;
    }
    for (const s of mergedSamples) if (s.tick <= (cut ?? Infinity)) mergedClicks += s.obj ? s.obj.merged : JSON.parse(s.line!).merged;
    t = performance.now();
    for (const s of samples) if (s.obj) { s.line = JSON.stringify(s.obj); s.obj = null; }
    t = tm.add("json", t);

    const errTotal = Object.values(errors).reduce((a, b) => a + b, 0);
    const validUntil = cut;
    const hash = { checked: hashChecked, missing: hashMissing, digest, orphan: hashOrphan };
    const params = { NOOP_EVERY: P.NOOP_EVERY, MERGE_TICKS: P.MERGE_TICKS, TIER1_PCT: P.TIER1_PCT, CELLS: P.CELLS };

    // ehrlich leer: 0 Samples, 0 Fehler, kein Desync, kein Tick-Fehler (§3)
    if (samples.length === 0 && errTotal === 0 && !desync && !tickError) {
      for (const f of tmpFiles) f.discard();
      abortT1();
      writeAtomic(gamePath(outdir, gid, "none"), JSON.stringify({ format: 2, reason: "0 Samples ohne Fehler",
        params, gid, ticks, hash, time_ms: tm.report() }) + "\n");
      return { status: "none", samples: 0, ms: tm.report().total, note: "" };
    }

    // Dateien fertigstellen
    t = performance.now();
    maps.close();
    cellsF?.close();
    const metaPath = gamePath(outdir, gid, "meta.zst");
    if (samples.length > 0) await writeZstdLinesSynced(metaPath + ".tmp", samples);
    t = tm.add("io", t);
    let t1files: Record<string, string> = {};
    let t1bytes = 0, chkDropped = 0;
    if (t1 && samples.length > 0) {
      try {
        const r = t1.finish(validUntil);
        t1files = r.files ?? {};
        t1bytes = r.bytes ?? 0;
        chkDropped = r.chkDropped ?? 0;
      } catch (e) { countErr("tier1", e, "finish"); abortT1(); }
      t = tm.add("tier1", t);
    } else abortT1();

    const hdr = {
      format: 2, gid, commit, materializer: process.env.MAT_VERSION ?? null,
      map: info.config.gameMap, mapSize: info.config.gameMapSize, W, H,
      landTiles: game.numLandTiles(), numTurns: turns.length,
      spawnPhaseTurns: nspt, spawnEndTick, randomSpawn,
      config: info.config,
      players: game.allPlayers().map((p) => ({
        sid: p.smallID(), clientID: p.clientID() ?? null, playerID: p.id(), name: p.name(),
        team: p.team() ?? null, type: p.type() })),
      winners: [...winners], tier1: !!t1, valid_until: validUntil, params,
    };
    const hdrPath = gamePath(outdir, gid, "hdr.json");
    writeSynced(hdrPath + ".tmp", JSON.stringify(hdr));

    // Folge laut DESIGN §5: maps, cells, Tier 1, hdr.json, meta.zst, dann .ok.
    // Ohne Samples gibt es weder .maps noch .meta.zst (eine leere wäre < 64 Byte).
    const renames: Array<[string, string]> = [];
    if (samples.length > 0) {
      renames.push([maps.tmp, maps.final]);
      if (cellsF) renames.push([cellsF.tmp, cellsF.final]);
      for (const [name, tmp] of Object.entries(t1files)) renames.push([tmp, path.join(outdir, path.basename(name))]);
    } else {
      for (const f of tmpFiles) f.discard();
    }
    renames.push([hdrPath + ".tmp", hdrPath]);
    if (samples.length > 0) renames.push([metaPath + ".tmp", metaPath]);

    const byKind: Record<string, number> = {};
    const byIntent: Record<string, number> = {};
    let spatial = 0;
    for (const s of samples) {
      byKind[s.kind] = (byKind[s.kind] ?? 0) + 1;
      byIntent[s.itype] = (byIntent[s.itype] ?? 0) + 1;
      if (s.spatial) spatial++;
    }
    let reason: string | undefined;
    if (samples.length === 0) {
      reason = desync ? `Desync bei Tick ${desync.tick}, kein Sample bis last_ok ${desync.last_ok_tick}`
        : tickError ? `Tick-Fehler bei ${tickError.tick}, kein Sample bis last_ok ${cut}`
        : `${errTotal} Fehler, erster: ${firstError}`;
    }
    const time = tm.report();
    const ru = process.resourceUsage();
    const ok: Record<string, unknown> = {
      format: 2,
      samples: samples.length, spatial, by_kind: byKind, by_intent: byIntent, merged_clicks: mergedClicks,
      res: resolver.stats(cut ?? Infinity),
      errors, first_error: firstError,
      hash, desync, tick_error: tickError, valid_until: validUntil,
      chk_dropped: chkDropped, legal_bit1: !!(cells && P.LEGAL_BIT1),
      time_ms: time,
      cpu_ms: { user: Math.round(ru.userCPUTime / 1000), system: Math.round(ru.systemCPUTime / 1000) },
      tier1_bytes: t1bytes, cells_bytes: cellsF && samples.length > 0 ? cellsF.size : 0,
      ticks, rss_max_mb: rssMaxMb(),
      ...(reason ? { reason } : {}),
    };
    commitGame(outdir, gid, renames, ok);
    const note = desync ? `DESYNC ${JSON.stringify(desync)}` : tickError ? `TICK_ERROR ${JSON.stringify(tickError)}` : "";
    return { status: "ok", samples: samples.length, ms: time.total, note };
  } catch (e) {
    for (const f of tmpFiles) f.discard();
    abortT1();
    for (const n of ["meta.zst", "hdr.json"]) { try { fs.unlinkSync(gamePath(outdir, gid, n) + ".tmp"); } catch {} }
    throw e;
  }
}

async function main() {
  const [file, outdir] = process.argv.slice(2);
  if (!file || !outdir) {
    console.error("Aufruf: materialize.ts <record.json> <outdir>");
    process.exit(2);
  }
  const P: Params = {
    NOOP_EVERY: envInt("NOOP_EVERY", 200),
    MERGE_TICKS: envInt("MERGE_TICKS", 30),
    TIER1_PCT: envInt("TIER1_PCT", 100),
    CELLS: envInt("CELLS", 1),
    LEGAL_BIT1: envInt("LEGAL_BIT1", 1),
  };
  const gid = path.basename(file).replace(/\.json$/, "");
  fs.mkdirSync(outdir, { recursive: true });
  if (process.env.FORCE !== "1" && isDone(outdir, gid, process.env.RETRY_ERR === "1")) {
    process.stdout.write(`${gid}: fertig, übersprungen\n`);
    process.exit(0);
  }
  cleanStale(outdir, gid);
  // Engine-Geplauder (console.warn/debug bei jedem gescheiterten Bau) stumm schalten
  if (process.env.ENGINE_LOG !== "1") { console.debug = () => {}; console.warn = () => {}; console.info = () => {}; }
  try {
    const r = await run(file, outdir, gid, P);
    process.stdout.write(`${gid}: ${r.status} ${r.samples} Samples, ${(r.ms / 1000).toFixed(1)} s` +
      (r.note ? `, ${r.note}` : "") + "\n");
    process.exit(0);
  } catch (e: any) {
    const msg = `${e?.message ?? e}\n${e instanceof GameError ? "" : (e?.stack ?? "")}`;
    try { writeAtomic(gamePath(outdir, gid, "err"), msg); } catch {}
    process.stderr.write(`${gid}: FEHLER ${e?.message ?? e}\n`);
    process.exit(1);
  }
}
main();


/** Metazeilen gestreamt komprimieren statt sie per join zu EINEM String zu verbinden.
 *  V8 begrenzt Strings auf rund 2^29 Zeichen; Partien mit vielen Spielern und Bots
 *  ueberschreiten das und scheiterten erst beim Schreiben (R3Xrpgf8, 10.09.2026).
 *  Der entpackte Inhalt ist identisch zu join("\n"): Zeilen durch \n getrennt, ohne
 *  abschliessendes \n. */
async function writeZstdLinesSynced(p: string, samples: { line: string }[]): Promise<void> {
  const fd = fs.openSync(p, "w");
  const z = createZstdCompress();
  const done = new Promise<void>((res, rej) => {
    z.on("data", (c: Buffer) => { fs.writeSync(fd, c); });
    z.on("end", () => res());
    z.on("error", rej);
  });
  for (let i = 0; i < samples.length; i++) {
    const chunk = (i ? "\n" : "") + samples[i].line;
    if (!z.write(chunk)) await new Promise<void>((r) => z.once("drain", () => r()));
  }
  z.end();
  await done;
  fs.fsyncSync(fd);
  fs.closeSync(fd);
}

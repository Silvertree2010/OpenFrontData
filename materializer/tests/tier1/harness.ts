/**
 * Test-Harness für Tier 1: replayt einen Record wie der alte Materialisierer
 * (Setup aus env/materialize.ts kopiert), füttert NUR den Tier1Writer und schreibt
 * eine unabhängige Referenz aus der Engine-Wahrheit.
 *
 *   npx tsx <tree>/tests/tier1/harness.ts <record.json> <outdir> [--mode full|tier1|replay]
 *        [--break state@<tick>|unit@<tick>] [--valid-until <tick>] [--ref-every 256]
 *
 * Modi:
 *   replay  nur Simulation (Basis für Zeit und RSS)
 *   tier1   Simulation + Tier1Writer, ohne Referenz (Zeit und RSS von Tier 1)
 *   full    wie tier1, dazu <gid>.t1ref.json: an vielen Ticks CRC32 des vollen
 *           tileStateBuffer, Terrain-Digest und Einheiten-Digest (Engine-Wahrheit)
 *
 * Tick-Konvention wie DESIGN §2: Tick t = Zustand mit game.ticks() == t.
 *
 * --break baut absichtlich einen Fehler in den Writer (Unterklasse, kein Schalter im
 * Produktivcode): state@t lässt im Eintrag für Tick t alle Updates EINER wirklich
 * geänderten Kachel weg, unit@t verschluckt ab Tick t das erste Entstehen einer
 * verfolgten Einheit. state@t wird auf den nächsten Referenz-Tick gelegt, damit der
 * Fehler geprüft wird, bevor die Kachel zufällig wieder überschrieben wird.
 * --valid-until spielt den Desync-Schnitt des Kerns nach (finish(validUntil)).
 */
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";
import { Config } from "../../../vendor/openfront/src/core/configuration/Config";
import { Executor } from "../../../vendor/openfront/src/core/execution/ExecutionManager";
import { Game, PlayerInfo, PlayerType } from "../../../vendor/openfront/src/core/game/Game";
import { createGame } from "../../../vendor/openfront/src/core/game/GameImpl";
import { GameUpdateType, GameUpdateViewData, UnitUpdate } from "../../../vendor/openfront/src/core/game/GameUpdates";
import { createNationsForGame } from "../../../vendor/openfront/src/core/game/NationCreation";
import { loadTerrainMap } from "../../../vendor/openfront/src/core/game/TerrainMapLoader";
import { GameRunner } from "../../../vendor/openfront/src/core/GameRunner";
import { PseudoRandom } from "../../../vendor/openfront/src/core/PseudoRandom";
import { GameRecord, GameRecordSchema, GameStartInfo } from "../../../vendor/openfront/src/core/Schemas";
import { decompressGameRecord, simpleHash, toWireGameStartInfo } from "../../../vendor/openfront/src/core/Util";
import { NodeGameMapLoader } from "../../../vendor/openfront/tests/perf/fullgame/NodeGameMapLoader";
import {
  BLOCK_TICKS,
  KEY_EVERY,
  Tier1Writer,
  UNIT_CODES,
  chkTicksFor,
  stateCrc,
  terrainArray,
  terrainDigest,
  tier1Selected,
  unitsDigest,
} from "../../src/tier1";

const ENGINE = path.join(path.dirname(fileURLToPath(import.meta.url)), "../../../vendor/openfront");
const TRACKED = new Set(UNIT_CODES.map(([t]) => t));

function arg(name: string, def: string): string {
  const i = process.argv.indexOf(name);
  return i > 0 ? process.argv[i + 1] : def;
}

/** Writer mit eingebautem Fehler (nur für den Nachweis, dass die Prüfung anschlägt). */
class BrokenWriter extends Tier1Writer {
  brkKind = "";
  brkTick = -1;
  brkDone = "";
  onTick(turn: number, gu: GameUpdateViewData, game: Game): void {
    const t = turn + 1;
    if (!this.brkDone && this.brkKind === "state" && t >= this.brkTick) {
      const p = gu.packedTileUpdates;
      const mir = this.mirror;
      const truth = game.tileStateBuffer();
      let victim = -1;
      for (let i = 0; i < p.length; i += 2) if (truth[p[i]] !== mir[p[i]]) { victim = p[i]; break; }
      if (victim >= 0) {
        const keep: number[] = [];
        for (let i = 0; i < p.length; i += 2) if (p[i] !== victim) keep.push(p[i], p[i + 1]);
        gu = { ...gu, packedTileUpdates: Uint32Array.from(keep) };
        this.brkDone = `state: Tick ${t}, Kachel ${victim} ausgelassen (Engine ${truth[victim]}, Log bleibt ${mir[victim]})`;
      }
    }
    super.onTick(turn, gu, game);
  }
  protected onUnit(tick: number, u: UnitUpdate) {
    if (this.brkKind === "unit" && tick >= this.brkTick && TRACKED.has(u.unitType)) {
      if (!this.brkDone && !this.units.has(u.id) && u.isActive) this.brkDone = `unit: Tick ${tick}, Entstehen von ${u.unitType} #${u.id}`;
      if (this.brkDone.endsWith(` #${u.id}`)) return; // alle Updates dieser Einheit verschlucken
    }
    super.onUnit(tick, u);
  }
}

async function main() {
  console.debug = () => {};
  const file = process.argv[2];
  const outdir = process.argv[3];
  const mode = arg("--mode", "full");
  const brk = arg("--break", "");
  const vuArg = arg("--valid-until", "");
  const refEvery = Number(arg("--ref-every", "256"));
  if (!file || !outdir) { console.error("Aufruf: harness.ts <record.json> <outdir> [--mode full|tier1|replay] [--break state@t|unit@t] [--valid-until t]"); process.exit(2); }
  fs.mkdirSync(outdir, { recursive: true });
  const tStart = performance.now();

  const raw = JSON.parse(fs.readFileSync(file, "utf8"));
  const parsed = GameRecordSchema.safeParse(raw);
  const record: GameRecord = decompressGameRecord(parsed.success ? parsed.data : (raw as GameRecord));
  const info = record.info;
  const gid = info.gameID;

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
  let lastGu: GameUpdateViewData | null = null;
  const runner = new GameRunner(game, new Executor(game, gameStart.gameID, undefined,
    gameStart.tribes?.map((t) => t.name)), (gu: any) => { if ("errMsg" in gu) fatal = gu.errMsg; else lastGu = gu; });
  runner.init();

  const W = game.width(), H = game.height();
  const N = record.turns.length; // Züge 0..N-1, Zustände (Ticks) 0..N
  const spawnTurns = config.numSpawnPhaseTurns();
  // spawnEndTick = kleinster t mit !inSpawnPhase() (DESIGN §2). Laut ENGINE_FACTS §3
  // ist das spawnTurns + 2; im Lauf wird es nachgemessen (spawnEndObserved).
  const S = spawnTurns + 2;
  const humanClient = new Set(gameStart.players.map((p) => p.clientID));
  // Kandidaten wie DESIGN §5.3: Züge mit Menschen-Intent nach numSpawnPhaseTurns()+1
  const candidates: number[] = [];
  for (const t of record.turns) {
    if (t.turnNumber <= spawnTurns + 1) continue;
    if ((t.intents ?? []).some((i: any) => i.clientID && humanClient.has(i.clientID) && i.type !== "mark_disconnected")) candidates.push(t.turnNumber);
  }
  const chkTicks = chkTicksFor(gid, candidates);

  // Referenz-Ticks: Raster, Anfang, Spawnende, Blockgrenzen ±1, Keyframes ±1, die letzten 16, chk
  const ref = new Set<number>();
  for (let t = 0; t <= N; t += refEvery) ref.add(t);
  for (let t = 0; t <= 5; t++) ref.add(t);
  for (let t = S - 3; t <= S + 3; t++) ref.add(t);
  for (let t = S; t <= N + BLOCK_TICKS; t += BLOCK_TICKS) for (const d of [-1, 0, 1]) ref.add(t + d);
  for (let t = S; t <= N + KEY_EVERY; t += KEY_EVERY) for (const d of [-1, 0, 1, 2]) ref.add(t + d);
  for (let t = N - 15; t <= N; t++) ref.add(t);
  for (const t of chkTicks) ref.add(t);
  const refTicks = [...ref].filter((t) => t >= 0 && t <= N).sort((a, b) => a - b);

  let writer: Tier1Writer | null = null;
  let brkInfo = "";
  if (mode !== "replay") {
    const opts = { dir: outdir, gid, game, spawnEndTick: S, chkTicks };
    if (brk) {
      const [k, ts] = brk.split("@");
      const bw = new BrokenWriter(opts);
      bw.brkKind = k;
      let bt = ts === "chk" ? chkTicks[0] : Number(ts);
      if (k === "state") bt = refTicks.find((t) => t >= bt) ?? bt;
      bw.brkTick = bt;
      writer = bw;
    } else writer = new Tier1Writer(opts);
  }
  const initTerrain = mode === "full" ? terrainArray(game).slice() : null;

  const refOut = { ticks: [] as number[], state_crc: [] as number[], terrain_crc: [] as number[], terrain_n: [] as number[], units_crc: [] as number[], units_n: [] as number[] };
  let simMs = 0, t1Ms = 0, refMs = 0;
  let hashChecked = 0, hashMissing = 0, hashMismatch = 0, firstMismatch = -1;
  let spawnEndObserved = -1;
  let ticks = 0;
  const takeRef = (t: number) => {
    const c = performance.now();
    const u = unitsDigest(game, t);
    const tr = terrainDigest(game, initTerrain!);
    refOut.ticks.push(t);
    refOut.state_crc.push(stateCrc(game));
    refOut.terrain_crc.push(tr.crc);
    refOut.terrain_n.push(tr.n);
    refOut.units_crc.push(u.crc);
    refOut.units_n.push(u.n);
    refMs += performance.now() - c;
  };
  if (mode === "full" && ref.has(0)) takeRef(0);

  for (const turn of record.turns) {
    const T = turn.turnNumber;
    if (T !== game.ticks()) throw new Error(`Zug ${T} bei game.ticks() ${game.ticks()}`);
    if (spawnEndObserved < 0 && !game.inSpawnPhase()) spawnEndObserved = game.ticks();
    const a = performance.now();
    runner.addTurn(turn);
    runner.executeNextTick();
    simMs += performance.now() - a;
    if (fatal) throw new Error(`tick-Fehler ${gid} T${T}: ${fatal}`);
    const gu = lastGu as GameUpdateViewData | null;
    lastGu = null;
    if (!gu) throw new Error(`kein Update fuer T${T}`);
    // Desync-Prüfung wie DESIGN §2.7
    for (const h of (gu.updates[GameUpdateType.Hash] ?? []) as any[]) {
      if (h.tick !== T) continue;
      const want = (record.turns[T] as any).hash;
      if (want === undefined || want === null) { hashMissing++; continue; }
      hashChecked++;
      if (want !== h.hash) { hashMismatch++; if (firstMismatch < 0) firstMismatch = T; }
    }
    if (writer) {
      const c = performance.now();
      writer.onTick(T, gu, game);
      t1Ms += performance.now() - c;
    }
    if (mode === "full" && ref.has(T + 1)) takeRef(T + 1);
    ticks++;
  }

  let t1 = null as any;
  if (writer) {
    const c = performance.now();
    const res = writer.finish(vuArg === "" ? null : Number(vuArg));
    t1Ms += performance.now() - c;
    for (const [name, tmp] of Object.entries(res.files)) fs.renameSync(tmp, path.join(outdir, name));
    const size = (n: string) => fs.statSync(path.join(outdir, `${gid}.${n}`)).size;
    t1 = { bytes: res.bytes, chkDropped: res.chkDropped, own: size("own.zst"), units: size("units.zst"), chk: size("chk"), stats: writer.stats() };
    if (writer instanceof BrokenWriter) brkInfo = writer.brkDone || "(Fehler nicht ausgelöst)";
  }

  const rssMax = process.resourceUsage().maxRSS / 1024; // Linux: KB → MB
  const run = {
    gid, commit: (info as any).gitCommit ?? (record as any).gitCommit, map: info.config.gameMap, mapSize: info.config.gameMapSize,
    W, H, landTiles: game.numLandTiles(), turns: N, ticks, mode, brk: brk || null, brkInfo: brkInfo || null,
    validUntil: vuArg === "" ? null : Number(vuArg),
    waterNukes: (info.config as any).waterNukes ?? null,
    spawnTurns, spawnEndTick: S, spawnEndObserved, chkTicks, candidates: candidates.length,
    selected: tier1Selected(gid, 100), selected25: tier1Selected(gid, 25),
    hash: { checked: hashChecked, missing: hashMissing, mismatch: hashMismatch, first_mismatch: firstMismatch },
    time_ms: { sim: Math.round(simMs), tier1: Math.round(t1Ms), ref: Math.round(refMs), total: Math.round(performance.now() - tStart) },
    rss_max_mb: Math.round(rssMax), tier1: t1,
  };
  const suffix = (brk ? `.brk-${brk.replace("@", "-")}` : "") + (vuArg ? `.vu${vuArg}` : "");
  fs.writeFileSync(path.join(outdir, `${gid}.t1run.${mode}${suffix}.json`), JSON.stringify(run));
  if (mode === "full") {
    fs.writeFileSync(path.join(outdir, `${gid}.t1ref.json`), JSON.stringify({
      gid, W, H, chk_ticks: chkTicks, candidate_ticks: candidates,
      selected: { "100": tier1Selected(gid, 100), "25": tier1Selected(gid, 25), "0": tier1Selected(gid, 0) },
      ...refOut }));
  }
  console.log(JSON.stringify(run));
}

main().catch((e) => { console.error(e?.stack ?? e); process.exit(1); });

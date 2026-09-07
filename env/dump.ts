/**
 * Beobachtungs-Extraktion aus einem echten Spielrecord.
 *
 * Replayt eine archivierte Partie und zieht in festem Takt fuer jeden lebenden
 * menschlichen Spieler eine Beobachtung. Zweck dieses Laufs ist die Messung:
 * wie viele Trainingsbeispiele pro Sekunde bekommen wir aus einem Record?
 *
 *   npx tsx env/dump.ts <record.json> [--every 10] [--grid 128x64]
 *
 * Kernidee fuer die Geschwindigkeit: der teure Durchlauf ueber alle Kacheln
 * passiert EINMAL pro Tick und erzeugt ein Besitzer-Raster. Die spielerbezogenen
 * Kanaele leiten sich daraus in 128x64 Schritten ab, nicht in Millionen.
 */
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";
import { Config } from "../vendor/openfront/src/core/configuration/Config";
import { Executor } from "../vendor/openfront/src/core/execution/ExecutionManager";
import { Game, Player, PlayerInfo, PlayerType } from "../vendor/openfront/src/core/game/Game";
import { createGame } from "../vendor/openfront/src/core/game/GameImpl";
import { createNationsForGame } from "../vendor/openfront/src/core/game/NationCreation";
import { loadTerrainMap } from "../vendor/openfront/src/core/game/TerrainMapLoader";
import { GameRunner } from "../vendor/openfront/src/core/GameRunner";
import { PseudoRandom } from "../vendor/openfront/src/core/PseudoRandom";
import { GameRecord, GameRecordSchema, GameStartInfo } from "../vendor/openfront/src/core/Schemas";
import { decompressGameRecord, simpleHash, toWireGameStartInfo } from "../vendor/openfront/src/core/Util";
import { NodeGameMapLoader } from "../vendor/openfront/tests/perf/fullgame/NodeGameMapLoader";

const ENGINE = path.join(path.dirname(fileURLToPath(import.meta.url)), "../vendor/openfront");

// Kachelbits, gespiegelt aus GameMapImpl.
const PLAYER_ID_MASK = 0xfff;
const FALLOUT_BIT = 1 << 13;
const DEFENSE_BIT = 1 << 14;
const LAND_BIT = 1 << 7;
const MAG_MASK = 0x1f;
const IMPASSABLE = 31;

/** Kanaele der Karte, aus Sicht eines Spielers. */
const CH = ["land", "hoehe", "eigen", "verbuendet", "feind", "neutral_besetzt", "fallout", "verteidigung"] as const;

type Grid = {
  gw: number; gh: number;
  owner: Uint16Array;   // dominanter Besitzer je Zelle (0 = niemand)
  land: Float32Array;   // Landanteil je Zelle
  mag: Float32Array;    // mittlere Hoehe
  fallout: Float32Array;
  defense: Float32Array;
};

function makeGrid(gw: number, gh: number): Grid {
  const n = gw * gh;
  return { gw, gh, owner: new Uint16Array(n), land: new Float32Array(n),
           mag: new Float32Array(n), fallout: new Float32Array(n), defense: new Float32Array(n) };
}

/**
 * Ein Durchlauf ueber alle Kacheln -> Raster. Teuer, aber nur einmal pro Tick.
 * Der dominante Besitzer wird per Mehrheit in der Zelle bestimmt.
 */
function scanMap(game: Game, g: Grid, counts: Int32Array, terrain: Uint8Array, state: Uint16Array) {
  const W = game.width(), H = game.height();
  const { gw, gh } = g;
  g.owner.fill(0); g.land.fill(0); g.mag.fill(0); g.fallout.fill(0); g.defense.fill(0);
  const perCell = new Float32Array(gw * gh);

  // Besitzerzaehlung je Zelle: counts ist [zelle * (maxPlayers+1)]
  counts.fill(0);
  const stride = counts.length / (gw * gh) | 0;

  for (let y = 0; y < H; y++) {
    const gy = ((y * gh) / H) | 0;
    const row = y * W;
    for (let x = 0; x < W; x++) {
      const ref = row + x;
      const gi = gy * gw + (((x * gw) / W) | 0);
      const t = terrain[ref];
      perCell[gi]++;
      if ((t & LAND_BIT) === 0) continue;
      const mag = t & MAG_MASK;
      if (mag === IMPASSABLE) continue;
      g.land[gi]++;
      g.mag[gi] += mag;
      const s = state[ref];
      if (s & FALLOUT_BIT) g.fallout[gi]++;
      if (s & DEFENSE_BIT) g.defense[gi]++;
      const oid = s & PLAYER_ID_MASK;
      if (oid !== 0 && oid < stride) counts[gi * stride + oid]++;
    }
  }

  for (let gi = 0; gi < gw * gh; gi++) {
    const n = perCell[gi] || 1;
    const landN = g.land[gi] || 1;
    g.mag[gi] /= landN * IMPASSABLE;
    g.fallout[gi] /= landN;
    g.defense[gi] /= landN;
    g.land[gi] /= n;
    let best = 0, bestN = 0;
    for (let p = 1; p < stride; p++) {
      const c = counts[gi * stride + p];
      if (c > bestN) { bestN = c; best = p; }
    }
    g.owner[gi] = best;
  }
}

/** Spielerbezogene Kanaele aus dem geteilten Raster. Billig: gw*gh Schritte. */
function encodeFor(g: Grid, me: number, allies: Set<number>, out: Float32Array) {
  const n = g.gw * g.gh;
  out.fill(0);
  for (let i = 0; i < n; i++) {
    out[0 * n + i] = g.land[i];
    out[1 * n + i] = g.mag[i];
    const o = g.owner[i];
    if (o === 0) continue;
    if (o === me) out[2 * n + i] = 1;
    else if (allies.has(o)) out[3 * n + i] = 1;
    else out[4 * n + i] = 1;
    out[5 * n + i] = 1;
  }
  for (let i = 0; i < n; i++) {
    out[6 * n + i] = g.fallout[i];
    out[7 * n + i] = g.defense[i];
  }
}

/** Vektor + Gegnermenge. */
function encodeVec(game: Game, p: Player) {
  const own = {
    troops: p.troops(), gold: Number(p.gold()), tiles: p.numTilesOwned(),
    alive: p.isAlive() ? 1 : 0, traitor: p.isTraitor() ? 1 : 0,
    allies: p.allies().length, betrayals: p.betrayals(),
  };
  const opps: any[] = [];
  for (const o of game.players()) {
    if (o === p || !o.isAlive()) continue;
    opps.push({
      id: o.smallID(), troops: o.troops(), gold: Number(o.gold()),
      tiles: o.numTilesOwned(),
      ally: p.isAlliedWith(o) ? 1 : 0,
      friendly: p.isFriendly(o) ? 1 : 0,
      traitor: o.isTraitor() ? 1 : 0,
      border: p.sharesBorderWith(o) ? 1 : 0,
      human: o.type() === PlayerType.Human ? 1 : 0,
    });
  }
  return { own, opps };
}

// ------------------------------------------------------------------ main

async function main() {
  const args = process.argv.slice(2);
  const file = args[0];
  if (!file) { console.error("Aufruf: dump.ts <record.json> [--every N] [--grid WxH]"); process.exit(2); }
  const every = Number(args[args.indexOf("--every") + 1]) || 10;
  const gridArg = args.includes("--grid") ? args[args.indexOf("--grid") + 1] : "128x64";
  const [gw, gh] = gridArg.split("x").map(Number);

  console.debug = () => {};
  const raw = JSON.parse(fs.readFileSync(file, "utf8"));
  const parsed = GameRecordSchema.safeParse(raw);
  if (!parsed.success) console.warn("Record passt nicht aufs Schema, nehme ihn wie er ist.");
  const record: GameRecord = decompressGameRecord(parsed.success ? parsed.data : (raw as GameRecord));
  const info = record.info;

  const gameStart: GameStartInfo = toWireGameStartInfo({
    gameID: info.gameID, lobbyCreatedAt: info.lobbyCreatedAt,
    config: info.config, players: info.players, tribes: info.tribes,
  });
  const config = new Config(info.config, null, false);
  const terrain = await loadTerrainMap(
    info.config.gameMap, info.config.gameMapSize,
    new NodeGameMapLoader(path.join(ENGINE, "resources/maps")), false,
  );
  const random = new PseudoRandom(simpleHash(gameStart.gameID));
  const humans = gameStart.players.map((p) => new PlayerInfo(
    p.username, PlayerType.Human, p.clientID, random.nextID(),
    p.isLobbyCreator ?? false, p.clanTag, p.friends ?? [], p.teamIndex ?? null,
  ));
  const nations = createNationsForGame(gameStart, terrain.nations, terrain.additionalNations, humans.length, random);
  const game = createGame(humans, nations, terrain.gameMap, terrain.miniGameMap, config, terrain.teamGameSpawnAreas);

  let fatal: string | undefined;
  const runner = new GameRunner(game, new Executor(game, gameStart.gameID, undefined,
    gameStart.tribes?.map((t) => t.name)), (gu) => { if ("errMsg" in gu) fatal = `${gu.errMsg}\n${(gu as any).stack ?? ""}`; });
  runner.init();

  const W = game.width(), H = game.height();
  const map = game.map();
  const state = map.tileStateBuffer();
  // Terrain kommt nicht ueber ein oeffentliches Byte-Array; wir lesen es einmal
  // ueber terrainByte() in ein eigenes Uint8Array und wiederverwenden es.
  const terrainBuf = new Uint8Array(W * H);
  for (let r = 0; r < W * H; r++) terrainBuf[r] = map.terrainByte(r);

  const grid = makeGrid(gw, gh);
  const maxP = 512;
  const counts = new Int32Array(gw * gh * maxP);
  const obs = new Float32Array(CH.length * gw * gh);

  console.log(`${info.gameID}: ${info.config.gameMap} ${W}x${H} (${(W * H / 1e6).toFixed(2)} Mio Kacheln), ` +
    `${info.players.length} Menschen, ${record.turns.length} Turns, Raster ${gw}x${gh}x${CH.length}`);

  let scans = 0, samples = 0, scanMs = 0, encMs = 0, vecMs = 0, checksum = 0;
  const t0 = performance.now();

  for (const turn of record.turns) {
    runner.addTurn(turn);
    runner.executeNextTick();
    if (fatal) { console.error("Fehler:", fatal); process.exit(1); }
    const tick = game.ticks();
    if (tick % every !== 0 || game.inSpawnPhase()) continue;

    const alive = game.players().filter((p) => p.isAlive() && p.type() === PlayerType.Human);
    if (alive.length === 0) continue;

    let t = performance.now();
    scanMap(game, grid, counts, terrainBuf, state);
    scanMs += performance.now() - t; scans++;

    for (const p of alive) {
      const allies = new Set(p.allies().map((a) => a.smallID()));
      t = performance.now();
      encodeFor(grid, p.smallID(), allies, obs);
      encMs += performance.now() - t;
      t = performance.now();
      const v = encodeVec(game, p);
      vecMs += performance.now() - t;
      checksum += v.opps.length + (obs[2 * gw * gh + 100] > 0 ? 1 : 0);
      samples++;
    }
  }

  const total = performance.now() - t0;
  const bytes = CH.length * gw * gh * 4;
  console.log(`\n  Beobachtungen : ${samples.toLocaleString()}  (${scans} Scans, ${(samples / Math.max(scans, 1)).toFixed(1)} Spieler/Scan)`);
  console.log(`  Gesamtzeit    : ${(total / 1000).toFixed(1)}s   -> ${(samples / (total / 1000)).toFixed(0)} Beobachtungen/s`);
  console.log(`  davon Scan    : ${(scanMs / 1000).toFixed(1)}s  (${(100 * scanMs / total).toFixed(0)}%)  ${(scanMs / Math.max(scans, 1)).toFixed(1)} ms/Scan`);
  console.log(`  davon Kanaele : ${(encMs / 1000).toFixed(1)}s  (${(100 * encMs / total).toFixed(0)}%)`);
  console.log(`  davon Vektor  : ${(vecMs / 1000).toFixed(1)}s  (${(100 * vecMs / total).toFixed(0)}%)`);
  console.log(`  Rest (Sim)    : ${((total - scanMs - encMs - vecMs) / 1000).toFixed(1)}s`);
  console.log(`  eine Obs      : ${(bytes / 1024).toFixed(0)} KB float32  (${(bytes / 4096).toFixed(0)} KB als uint8)`);
  console.log(`  Pruefsumme    : ${checksum}`);
}

main();

/**
 * Exportiert eine archivierte Partie als abspielbare Frames.
 *
 * Pro Frame ein lauflaengenkodiertes Besitzraster plus Spielerstand; dazu
 * einmalig Landmaske und Spielerliste. Territorium ist stark zusammenhaengend,
 * deshalb drueckt RLE die Groesse um mehr als eine Zehnerpotenz.
 *
 *   npx tsx env/export_replay.ts <record.json> out.json [--every 20] [--grid 200x100]
 */
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";
import { Config } from "../vendor/openfront/src/core/configuration/Config";
import { Executor } from "../vendor/openfront/src/core/execution/ExecutionManager";
import { Game, PlayerInfo, PlayerType } from "../vendor/openfront/src/core/game/Game";
import { createGame } from "../vendor/openfront/src/core/game/GameImpl";
import { createNationsForGame } from "../vendor/openfront/src/core/game/NationCreation";
import { loadTerrainMap } from "../vendor/openfront/src/core/game/TerrainMapLoader";
import { GameRunner } from "../vendor/openfront/src/core/GameRunner";
import { PseudoRandom } from "../vendor/openfront/src/core/PseudoRandom";
import { GameRecord, GameRecordSchema, GameStartInfo } from "../vendor/openfront/src/core/Schemas";
import { decompressGameRecord, simpleHash, toWireGameStartInfo } from "../vendor/openfront/src/core/Util";
import { NodeGameMapLoader } from "../vendor/openfront/tests/perf/fullgame/NodeGameMapLoader";

const ENGINE = path.join(path.dirname(fileURLToPath(import.meta.url)), "../vendor/openfront");
const LAND_BIT = 1 << 7, MAG_MASK = 0x1f, IMPASSABLE = 31, PID_MASK = 0xfff;

function rle(a: Int32Array | Uint16Array): number[] {
  const out: number[] = [];
  let cur = a[0], n = 1;
  for (let i = 1; i < a.length; i++) {
    if (a[i] === cur) n++;
    else { out.push(cur, n); cur = a[i]; n = 1; }
  }
  out.push(cur, n);
  return out;
}

async function main() {
  const [file, outPath, ...rest] = process.argv.slice(2);
  if (!file || !outPath) { console.error("Aufruf: export_replay.ts <record.json> <out.json> [--every N] [--grid WxH]"); process.exit(2); }
  const every = Number(rest[rest.indexOf("--every") + 1]) || 20;
  const [gw, gh] = (rest.includes("--grid") ? rest[rest.indexOf("--grid") + 1] : "200x100").split("x").map(Number);

  console.debug = () => {};
  const raw = JSON.parse(fs.readFileSync(file, "utf8"));
  const parsed = GameRecordSchema.safeParse(raw);
  const record: GameRecord = decompressGameRecord(parsed.success ? parsed.data : (raw as GameRecord));
  const info = record.info;

  const gameStart: GameStartInfo = toWireGameStartInfo({
    gameID: info.gameID, lobbyCreatedAt: info.lobbyCreatedAt,
    config: info.config, players: info.players, tribes: info.tribes,
  });
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
  const runner = new GameRunner(game, new Executor(game, gameStart.gameID, undefined,
    gameStart.tribes?.map((t) => t.name)), (gu) => { if ("errMsg" in gu) fatal = gu.errMsg; });
  runner.init();

  const W = game.width(), H = game.height();
  const map = game.map();
  const state = map.tileStateBuffer();

  // Landmaske einmalig
  const land = new Uint16Array(gw * gh);
  const cellTiles = new Int32Array(gw * gh);
  const landTiles = new Int32Array(gw * gh);
  for (let y = 0; y < H; y++) {
    const gy = ((y * gh) / H) | 0, row = y * W;
    for (let x = 0; x < W; x++) {
      const gi = gy * gw + (((x * gw) / W) | 0);
      cellTiles[gi]++;
      const t = map.terrainByte(row + x);
      if ((t & LAND_BIT) && (t & MAG_MASK) !== IMPASSABLE) landTiles[gi]++;
    }
  }
  for (let i = 0; i < gw * gh; i++) land[i] = landTiles[i] * 2 > cellTiles[i] ? 1 : 0;

  const owner = new Uint16Array(gw * gh);
  const counts = new Int32Array(gw * gh * 600);
  const frames: any[] = [];
  const events: any[] = [];
  const wasAlive = new Set<number>();      // wer schon mal gelebt hat
  const seenDead = new Set<number>();

  const scan = () => {
    counts.fill(0);
    for (let y = 0; y < H; y++) {
      const gy = ((y * gh) / H) | 0, row = y * W;
      for (let x = 0; x < W; x++) {
        const gi = gy * gw + (((x * gw) / W) | 0);
        const t = map.terrainByte(row + x);
        if (!(t & LAND_BIT) || (t & MAG_MASK) === IMPASSABLE) continue;
        const oid = state[row + x] & PID_MASK;
        if (oid > 0 && oid < 600) counts[gi * 600 + oid]++;
      }
    }
    for (let gi = 0; gi < gw * gh; gi++) {
      let best = 0, bn = 0;
      for (let p = 1; p < 600; p++) { const c = counts[gi * 600 + p]; if (c > bn) { bn = c; best = p; } }
      owner[gi] = best;
    }
  };

  const t0 = performance.now();
  let n = 0;
  for (const turn of record.turns) {
    runner.addTurn(turn);
    runner.executeNextTick();
    if (fatal) { console.error("Fehler:", fatal); process.exit(1); }
    const tick = game.ticks();

    // players() liefert nur Lebende -- Ausgeschiedene verschwinden daraus,
    // deshalb ueber allPlayers() und den vorherigen Zustand gehen.
    for (const p of game.allPlayers()) {
      const id = p.smallID();
      if (p.isAlive()) { wasAlive.add(id); continue; }
      if (wasAlive.has(id) && !seenDead.has(id)) {
        seenDead.add(id);
        events.push({ t: tick, k: "out", p: id, n: p.name(),
                      human: p.type() === PlayerType.Human });
      }
    }
    if (tick % every !== 0) continue;

    scan();
    const ps = game.players().filter((p) => p.isAlive()).map((p) => [
      p.smallID(), p.troops() | 0, Number(p.gold()) | 0, p.numTilesOwned(),
    ]);
    frames.push({ t: tick, o: rle(owner), p: ps });
    n++;
  }

  // Spielerliste inkl. Farbe aus der Engine-Theme-Zuordnung waere hier ideal;
  // stattdessen liefern wir Typ und Name, der Betrachter faerbt selbst.
  const players = game.allPlayers().map((p) => ({
    id: p.smallID(), name: p.name(),
    clan: p.clanTag() ?? null,
    human: p.type() === PlayerType.Human,
    nation: p.type() === PlayerType.Nation,
  }));

  const out = {
    gameID: info.gameID, map: info.config.gameMap, size: info.config.gameMapSize,
    mode: info.config.gameMode, maxPlayers: info.config.maxPlayers,
    dims: [gw, gh], mapDims: [W, H], every, land: rle(land),
    players, frames, events,
    winner: info.winner ?? null, durationS: info.duration ?? null,
  };
  fs.writeFileSync(outPath, JSON.stringify(out));
  const mb = fs.statSync(outPath).size / 1e6;
  console.log(`${info.gameID} ${info.config.gameMap}: ${n} Frames, ${players.length} Spieler, ` +
    `${events.length} Ereignisse, ${mb.toFixed(1)} MB, ${((performance.now() - t0) / 1000).toFixed(0)}s`);
}
main();

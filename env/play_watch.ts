/**
 * Watch-Modus: unser BC-Modell spielt live gegen die Engine-Nationen; das Board
 * wird alle paar Ticks als JSON rausgeschrieben (fuer den Browser-Viewer).
 * Nationen agieren per Engine-AI von selbst; nur der Modell-Spieler wird per
 * Inference-Server getrieben (obs -> POST /act -> Intent -> ins Spiel).
 *
 *   OUT=/out/game_state.json npx tsx env/play_watch.ts
 * Braucht den Inference-Server auf 127.0.0.1:8650 (--network host im Container).
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
import { GameStartInfo } from "../vendor/openfront/src/core/Schemas";
import { simpleHash, toWireGameStartInfo } from "../vendor/openfront/src/core/Util";
import { NodeGameMapLoader } from "../vendor/openfront/tests/perf/fullgame/NodeGameMapLoader";
import { ObsEncoder, NUM_CHANNELS } from "./obs";

const ENGINE = path.join(path.dirname(fileURLToPath(import.meta.url)), "../vendor/openfront");
const INF = process.env.INF || "http://127.0.0.1:8650/act";
const OUT = process.env.OUT || "/out/game_state.json";
const MODEL_CID = "modelbot";
const GW = 180, GH = 90, MAPLEN = NUM_CHANNELS * GW * GH;
const DECIDE_EVERY = Number(process.env.DECIDE_EVERY ?? 8);   // Modell entscheidet alle N Ticks
const TICK_MS = Number(process.env.TICK_MS ?? 90);            // Spieltempo (sichtbar)
const MAX_TICKS = Number(process.env.MAX_TICKS ?? 6000);
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

const MAP = process.env.MAP || "Halkidiki";
const cfg: any = {
  gameMap: MAP, gameMapSize: process.env.SIZE || "Small", gameMode: "Free For All",
  difficulty: "Medium", bots: Number(process.env.BOTS ?? 60), disabledUnits: [], playerTeams: 0,
  infiniteGold: false, infiniteTroops: false, instantBuild: false, randomSpawn: false,
  donateGold: false, donateTroops: false,
};

const modelPlayer = (game: Game) => game.players().find((p) => p.type() === PlayerType.Human);

async function decide(game: Game, enc: ObsEncoder, obsBuf: Float32Array): Promise<any | null> {
  const player = modelPlayer(game);
  if (!player) return null;
  const v = enc.encodeVec(game, player, 24);
  const allies = new Set(player.allies().map((a) => a.smallID()));
  enc.encodeMap(player, allies, obsBuf);
  const oppIds = v.opponents.map((o: any) => { const pl = game.playerBySmallID(o.id); return pl.isPlayer() ? pl.id() : null; });
  const opps = v.opponents.map((o: any) => { const pl = game.playerBySmallID(o.id); return { ...o, user: pl.isPlayer() ? pl.name() : null, clan: pl.isPlayer() ? pl.clanTag() : null }; });
  const body = {
    map: Array.from(obsBuf), own: v.own, opps, config: cfg, sample: true, temp: 0.9,
    ctx: { mapW: game.width(), mapH: game.height(), troops: player.troops(), gold: Number(player.gold()),
      oppIds, ownUnitIds: player.units().map((u: any) => u.id()), ownAttackIds: player.outgoingAttacks().map((a: any) => a.id()) },
  };
  try {
    const r = await fetch(INF, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    const j: any = await r.json();
    if (!j.intent || j.intent.type === "no_op") return null;
    return { ...j.intent, clientID: player.clientID() };
  } catch (e) { return null; }
}

function dumpBoard(game: Game, modelSmallID: number) {
  const W = game.width(), H = game.height();
  const state = game.map().tileStateBuffer();
  const PLAYER_ID_MASK = 0xfff;
  const grid: number[] = new Array(GW * GH).fill(0);
  for (let gy = 0; gy < GH; gy++) {
    const y = ((gy * H) / GH) | 0;
    for (let gx = 0; gx < GW; gx++) {
      const x = ((gx * W) / GW) | 0;
      grid[gy * GW + gx] = state[y * W + x] & PLAYER_ID_MASK;
    }
  }
  const players = game.players().filter((p) => p.isAlive()).map((p) => ({
    id: p.smallID(), name: p.name(), tiles: p.numTilesOwned(),
    isModel: p.smallID() === modelSmallID, human: p.type() === PlayerType.Human,
  })).sort((a, b) => b.tiles - a.tiles);
  const model = game.players().find((p) => p.smallID() === modelSmallID);
  fs.writeFileSync(OUT, JSON.stringify({
    w: GW, h: GH, grid, players: players.slice(0, 40), modelSmallID,
    tick: (game as any).ticks?.() ?? 0, alive: players.length,
    modelAlive: model ? model.isAlive() : false, modelTiles: model ? model.numTilesOwned() : 0,
    ts: Date.now(),
  }));
}

async function main() {
  console.debug = () => {};
  const players = [{ username: "🤖 BC-MODELL", clientID: MODEL_CID, isLobbyCreator: true, clanTag: "AI", friends: [], teamIndex: null }];
  const gameStart: GameStartInfo = toWireGameStartInfo({ gameID: "watch" + Date.now(), lobbyCreatedAt: Date.now(), config: cfg, players, tribes: [] } as any);
  const config = new Config(cfg, null, false);
  const terrain = await loadTerrainMap(cfg.gameMap, cfg.gameMapSize, new NodeGameMapLoader(path.join(ENGINE, "resources/maps")), false);
  const random = new PseudoRandom(simpleHash(gameStart.gameID));
  const humans = gameStart.players.map((p) => new PlayerInfo(p.username, PlayerType.Human, p.clientID, random.nextID(), p.isLobbyCreator ?? false, p.clanTag, p.friends ?? [], p.teamIndex ?? null));
  const nations = createNationsForGame(gameStart, terrain.nations, terrain.additionalNations, humans.length, random);
  const game: Game = createGame(humans, nations, terrain.gameMap, terrain.miniGameMap, config, terrain.teamGameSpawnAreas);
  let fatal: string | undefined;
  const runner = new GameRunner(game, new Executor(game, gameStart.gameID, undefined, gameStart.tribes?.map((t: any) => t.name)), (gu: any) => { if ("errMsg" in gu) fatal = gu.errMsg; });
  runner.init();
  const enc = new ObsEncoder(GW, GH);
  const obsBuf = new Float32Array(MAPLEN);
  const W = game.width(), H = game.height(), map = game.map();
  const spawnTile = () => { for (let i = 0; i < 40000; i++) { const r = (Math.random() * W * H) | 0; const b = map.terrainByte(r); if ((b & (1 << 7)) && (b & 0x1f) !== 31) return r; } return (W * H / 2) | 0; };
  let modelSmallID = -1, wasSpawned = false;
  console.log(`WATCH gestartet: ${cfg.gameMap} ${W}x${H}, warte auf Spawn…`);

  for (let t = 0; t < MAX_TICKS; t++) {
    const intents: any[] = [];
    const player = modelPlayer(game);
    if (player) { modelSmallID = player.smallID(); if (!wasSpawned) { wasSpawned = true; console.log(`✅ MODELL GESPAWNT @${t}, smallID=${modelSmallID}`); } }
    if (!player && game.inSpawnPhase() && t % 20 === 0) {
      // Modell-Spieler bootstrappen: alle 20 Ticks einen Spawn versuchen (Retry mit
      // frischem Feld), bis er existiert — die Map ist beim Spawn manchmal waehlerisch.
      const tile = spawnTile();
      intents.push({ type: "spawn", tile, clientID: MODEL_CID });
      if (t % 100 === 0) console.log(`… Spawn-Versuch @${t} tile=${tile}`);
    } else if (player && player.isAlive() && t % DECIDE_EVERY === 0) {
      enc.scanTick(game);
      const intent = await decide(game, enc, obsBuf);
      if (intent) intents.push(intent);
    }
    runner.addTurn({ turnNumber: t, gameID: gameStart.gameID, intents } as any);
    runner.executeNextTick();
    if (fatal) { console.log(`FATAL @${t}: ${fatal}`); break; }
    if (t % 4 === 0) { try { dumpBoard(game, modelSmallID); } catch {} }
    const aliveHumans = game.players().filter((p) => p.isAlive());
    if (aliveHumans.length <= 1 && !game.inSpawnPhase() && t > 200) { console.log(`Spiel vorbei @${t}`); break; }
    await sleep(TICK_MS);
  }
  try { dumpBoard(game, modelSmallID); } catch {}
  console.log("WATCH fertig.");
}
main().catch((e) => { console.error("ERR", e?.stack ?? e); process.exit(1); });

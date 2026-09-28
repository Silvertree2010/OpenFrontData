/**
 * Obergrenze der Zusatzkosten der Spielweise-Messung: eine Partie ohne KI-Befehle (Nationen und
 * Bots spielen), alle 250 Ticks wird verteilung() für die fünf grössten Spieler gerechnet — so
 * viel Gebiet, wie eine KI höchstens hätte. Gemessen: Engine-Zeit je Tick gegen Messzeit je Probe.
 *
 *   cd <client> && npx tsx arena/tests/spielweise_bench.ts --bots 400 --ticks 4000
 */
import path from "path";
import { Config } from "../../src/core/configuration/Config";
import { Executor } from "../../src/core/execution/ExecutionManager";
import { Game, PlayerInfo, PlayerType } from "../../src/core/game/Game";
import { createGame } from "../../src/core/game/GameImpl";
import { createNationsForGame } from "../../src/core/game/NationCreation";
import { loadTerrainMap } from "../../src/core/game/TerrainMapLoader";
import { GameRunner } from "../../src/core/GameRunner";
import { PseudoRandom } from "../../src/core/PseudoRandom";
import { simpleHash, toWireGameStartInfo } from "../../src/core/Util";
import { NodeGameMapLoader } from "../../tests/perf/fullgame/NodeGameMapLoader";
import { landZellen, Puffer, verteilung } from "../spielweise";

console.log = () => {}; console.debug = () => {}; console.warn = () => {}; console.info = () => {};
const a = process.argv.slice(2);
const w = (n: string, s: string) => { const i = a.indexOf("--" + n); return i >= 0 ? a[i + 1] : s; };
const BOTS = Number(w("bots", "400")), TICKS = Number(w("ticks", "4000"));

async function main() {
  const cfg: any = { gameMap: "World", gameMapSize: "Compact", gameMode: "Free For All",
    gameType: "Singleplayer", difficulty: "Medium", nations: "default", bots: BOTS, disabledUnits: [],
    playerTeams: 0, infiniteGold: false, infiniteTroops: false, instantBuild: false, randomSpawn: false,
    donateGold: false, donateTroops: false };
  const gs: any = toWireGameStartInfo({ gameID: "bench001", lobbyCreatedAt: 0, config: cfg,
    players: [{ username: "X", clientID: "benchki01", isLobbyCreator: true, clanTag: null, friends: [] }],
    tribes: [] } as any);
  const terrain = await loadTerrainMap(cfg.gameMap, cfg.gameMapSize,
    new NodeGameMapLoader(path.resolve("resources/maps")), false);
  const zufall = new PseudoRandom(simpleHash(gs.gameID));
  const humans = gs.players.map((p: any) => new PlayerInfo(p.username, PlayerType.Human, p.clientID,
    zufall.nextID(), true, null, [], null));
  const nations = createNationsForGame(gs, terrain.nations, terrain.additionalNations, humans.length, zufall);
  const game: Game = createGame(humans, nations, terrain.gameMap, terrain.miniGameMap,
    new Config(cfg, null, false), terrain.teamGameSpawnAreas);
  const runner = new GameRunner(game, new Executor(game, gs.gameID, undefined, []), () => {});
  runner.init();
  const W = game.width(), H = game.height(), map = game.map();
  const lz = landZellen(W, H, (r) => map.isLand(r));
  const pf = new Puffer(W * H);
  let tEngine = 0n, tMess = 0n, proben = 0, kacheln = 0, maxK = 0;
  for (let t = 0; t < TICKS; t++) {
    // Einzelspiel: die Startphase endet erst mit dem Spawn des Menschen (wie arena.ts, Tick 100)
    const intents: any[] = [];
    if (t === 100) {
      let r = ((H >> 1) * W + (W >> 1)) | 0;
      while (!(map.isLand(r) && !game.hasOwner(r))) r = (r + 7919) % (W * H);
      intents.push({ type: "spawn", tile: r, clientID: "benchki01" });
    }
    runner.addTurn({ turnNumber: t, gameID: gs.gameID, intents } as any);
    const t0 = process.hrtime.bigint();
    runner.executeNextTick();
    tEngine += process.hrtime.bigint() - t0;
    if (t % 250 === 0 && !game.inSpawnPhase()) {
      const gross = game.players().filter((p) => p.isAlive())
        .sort((x, y) => y.numTilesOwned() - x.numTilesOwned()).slice(0, 5);
      const t1 = process.hrtime.bigint();
      for (const p of gross) {
        const sid = p.smallID();
        verteilung(p.tiles(), p.numTilesOwned(), W, H, (r) => map.ownerID(r) === sid,
          (r) => map.isShore(r), lz, pf);
        proben++; kacheln += p.numTilesOwned(); maxK = Math.max(maxK, p.numTilesOwned());
      }
      tMess += process.hrtime.bigint() - t1;
    }
  }
  const msE = Number(tEngine) / 1e6, msM = Number(tMess) / 1e6;
  process.stdout.write(JSON.stringify({
    bots: BOTS, ticks: TICKS, karte: `${W}x${H}`, engine_ms: Math.round(msE),
    engine_ms_je_250_ticks: Number(((msE / TICKS) * 250).toFixed(1)),
    proben, mess_ms: Number(msM.toFixed(1)), mess_ms_je_probe: Number((msM / Math.max(1, proben)).toFixed(3)),
    kacheln_mittel: Math.round(kacheln / Math.max(1, proben)), kacheln_max: maxK,
    anteil_eine_ki: Number(((msM / Math.max(1, proben)) / ((msE / TICKS) * 250) * 100).toFixed(3)) + " %",
  }) + "\n");
}
main();

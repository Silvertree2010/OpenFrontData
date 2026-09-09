/**
 * Probe: laeuft ein frisch erzeugtes Spiel von selbst (Nationen agieren per Engine-AI),
 * wenn wir KEINE aufgezeichneten Turns einspielen, sondern nur Ticks vorspulen?
 * Wenn ja -> der Watch-Modus (Modell vs Bots + Board-Rendering) ist machbar.
 *   npx tsx env/play_probe.ts
 */
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

const ENGINE = path.join(path.dirname(fileURLToPath(import.meta.url)), "../vendor/openfront");

async function main() {
  console.debug = () => {};
  const cfg: any = {
    gameMap: "Halkidiki", gameMapSize: "Small", gameMode: "Free For All",
    difficulty: "Medium", bots: 100, disabledUnits: [], playerTeams: 0,
    infiniteGold: false, infiniteTroops: false, instantBuild: false, randomSpawn: false,
    donateGold: false, donateTroops: false,
  };
  const players = [{ username: "MODEL", clientID: "modelbot", isLobbyCreator: true,
    clanTag: null, friends: [], teamIndex: null }];
  const gameStart: GameStartInfo = toWireGameStartInfo({
    gameID: "probe0001", lobbyCreatedAt: Date.now(), config: cfg, players, tribes: [] } as any);
  const config = new Config(cfg, null, false);
  const terrain = await loadTerrainMap(cfg.gameMap, cfg.gameMapSize,
    new NodeGameMapLoader(path.join(ENGINE, "resources/maps")), false);
  const random = new PseudoRandom(simpleHash(gameStart.gameID));
  const humans = gameStart.players.map((p) => new PlayerInfo(
    p.username, PlayerType.Human, p.clientID, random.nextID(),
    p.isLobbyCreator ?? false, p.clanTag, p.friends ?? [], p.teamIndex ?? null));
  const nations = createNationsForGame(gameStart, terrain.nations, terrain.additionalNations, humans.length, random);
  const game: Game = createGame(humans, nations, terrain.gameMap, terrain.miniGameMap, config, terrain.teamGameSpawnAreas);
  let fatal: string | undefined;
  const runner = new GameRunner(game, new Executor(game, gameStart.gameID, undefined,
    gameStart.tribes?.map((t: any) => t.name)), (gu: any) => { if ("errMsg" in gu) fatal = gu.errMsg; });
  runner.init();
  console.log(`Start: ${game.width()}x${game.height()}, players=${game.players().length}, nations=${nations.length}, spawnPhase=${game.inSpawnPhase()}`);
  for (let t = 0; t < 400; t++) {
    runner.addTurn({ turnNumber: t, gameID: gameStart.gameID, intents: [] } as any);
    runner.executeNextTick();
    if (fatal) { console.log(`FATAL @${t}: ${fatal}`); break; }
    if (t % 50 === 0) {
      const alive = game.players().filter((p) => p.isAlive());
      const owned = alive.reduce((s, p) => s + p.numTilesOwned(), 0);
      console.log(`tick ${t}: alive=${alive.length}/${game.players().length} spawnPhase=${game.inSpawnPhase()} owned=${owned}`);
    }
  }
  console.log("Probe fertig.");
}
main().catch((e) => { console.error("ERR", e?.message ?? e); process.exit(1); });

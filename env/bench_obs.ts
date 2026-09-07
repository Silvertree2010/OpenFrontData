/**
 * Misst den neuen Encoder auf einem echten Record und zeigt, dass die
 * Annexions-Merkmale plausible Werte liefern.
 *   npx tsx env/bench_obs.ts <record.json> [--every 10]
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
import { CHANNELS, NUM_CHANNELS, ObsEncoder } from "./obs";

const ENGINE = path.join(path.dirname(fileURLToPath(import.meta.url)), "../vendor/openfront");

async function main() {
  const file = process.argv[2];
  const every = Number(process.argv[process.argv.indexOf("--every") + 1]) || 10;
  console.debug = () => {};
  const raw = JSON.parse(fs.readFileSync(file, "utf8"));
  const parsed = GameRecordSchema.safeParse(raw);
  const record: GameRecord = decompressGameRecord(parsed.success ? parsed.data : (raw as GameRecord));
  const info = record.info;
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
  const runner = new GameRunner(game, new Executor(game, gameStart.gameID, undefined,
    gameStart.tribes?.map((t) => t.name)), (gu) => { if ("errMsg" in gu) fatal = gu.errMsg; });
  runner.init();

  const enc = new ObsEncoder(180, 90);
  enc.initTerrain(game);
  const out = new Float32Array(NUM_CHANNELS * enc.gw * enc.gh);

  let scans = 0, samples = 0, scanMs = 0, mapMs = 0, vecMs = 0;
  let nearAnnex = 0, annexProof = 0, oppSeen = 0;
  const hist = new Map<string, number>();
  const bucket = (f: number) => f >= 1 ? "100%" : f >= .95 ? "95-100%" : f >= .8 ? "80-95%"
    : f >= .5 ? "50-80%" : f >= .2 ? "20-50%" : "<20%";
  const t0 = performance.now();

  for (const turn of record.turns) {
    runner.addTurn(turn); runner.executeNextTick();
    if (fatal) { console.error("Fehler:", fatal); process.exit(1); }
    if (game.ticks() % every !== 0 || game.inSpawnPhase()) continue;
    const alive = game.players().filter((p) => p.isAlive() && p.type() === PlayerType.Human);
    if (!alive.length) continue;

    let t = performance.now(); enc.scanTick(game); scanMs += performance.now() - t; scans++;
    for (const p of alive) {
      const allies = new Set(p.allies().map((a) => a.smallID()));
      t = performance.now(); enc.encodeMap(p, allies, out); mapMs += performance.now() - t;
      t = performance.now(); const v = enc.encodeVec(game, p); vecMs += performance.now() - t;
      for (const o of v.opponents) {
        oppSeen++;
        if (o.bordersMe && o.gapsRemaining > 0 && o.gapsRemaining <= 20 && !o.annexProof) nearAnnex++;
        if (o.annexProof) annexProof++;
        if (o.bordersMe && !o.annexProof) {
          const k = bucket(o.borderToMe);
          hist.set(k, (hist.get(k) ?? 0) + 1);
        }
      }
      samples++;
    }
  }
  const total = performance.now() - t0;
  console.log(`${info.gameID} ${info.config.gameMap} ${game.width()}x${game.height()}`);
  console.log(`  Kanaele        : ${NUM_CHANNELS}  [${CHANNELS.join(", ")}]`);
  console.log(`  Beobachtungen  : ${samples.toLocaleString()} in ${(total/1000).toFixed(1)}s -> ${(samples/(total/1000)).toFixed(0)}/s`);
  console.log(`  Scan ${(100*scanMs/total).toFixed(0)}%  Karte ${(100*mapMs/total).toFixed(0)}%  Vektor ${(100*vecMs/total).toFixed(0)}%  Sim ${(100*(total-scanMs-mapMs-vecMs)/total).toFixed(0)}%`);
  console.log(`  Gegner-Eintraege: ${oppSeen.toLocaleString()}`);
  console.log(`    davon annexionsfest (Kueste/Rand): ${(100*annexProof/Math.max(oppSeen,1)).toFixed(1)}%`);
  console.log(`    davon kurz vor Einschluss (<=20 Luecken): ${nearAnnex.toLocaleString()}`);
  console.log(`  Verteilung borderToMe -- Anteil SEINER Grenze, der an MICH stoesst:`);
  const tot = [...hist.values()].reduce((a, b) => a + b, 0) || 1;
  for (const k of ["100%", "95-100%", "80-95%", "50-80%", "20-50%", "<20%"]) {
    const v = hist.get(k) ?? 0;
    console.log(`    ${k.padEnd(8)} ${String(v).padStart(7)}  ${(100*v/tot).toFixed(1)}%`);
  }
  console.log(`    (angrenzende, nicht kuestenfeste Gegner-Eintraege gesamt: ${tot.toLocaleString()})`);
}
main();

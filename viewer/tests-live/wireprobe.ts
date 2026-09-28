/**
 * Ohne Browser: geht der Weg, den die Erweiterung nehmen müsste?
 *
 * Aus einer echten Aufzeichnung werden Startbrief und Züge so kodiert, wie der Server
 * sie schickt, und mit genau der Funktion wieder gelesen, die die Erweiterung benutzt
 * (decodeServerMessage mit der Tabelle aus dem Startbrief). Danach laufen die Züge durch
 * die Engine, und aus dem Zustand wird die Beobachtung gerechnet — also der ganze Weg
 * „mitlesen → nachspielen → Beobachtung", nur ohne Netz.
 *
 *   npx tsx aitest/wireprobe.ts <record.json> [ticks]
 */
import fs from "fs";
import path from "path";
import { AiAnfrage } from "../src/core/aiAnfrage";
import { Config } from "../src/core/configuration/Config";
import { Executor } from "../src/core/execution/ExecutionManager";
import { Player, PlayerInfo, PlayerType } from "../src/core/game/Game";
import { createGame } from "../src/core/game/GameImpl";
import { createNationsForGame } from "../src/core/game/NationCreation";
import { loadTerrainMap } from "../src/core/game/TerrainMapLoader";
import { GameRunner } from "../src/core/GameRunner";
import { PseudoRandom } from "../src/core/PseudoRandom";
import { GameRecord, GameRecordSchema } from "../src/core/Schemas";
import { decompressGameRecord, simpleHash, toWireGameStartInfo } from "../src/core/Util";
import {
  createGameWireContext,
  decodeServerMessage,
  encodeServerMessage,
} from "../src/core/ZbinWire";
import { NodeGameMapLoader } from "../tests/perf/fullgame/NodeGameMapLoader";

const [file, tickArg] = process.argv.slice(2);
const TICKS = Number(tickArg ?? 300);

async function main() {
  console.debug = () => {};
  console.log = (...x: any[]) => console.error(...x);
  const raw = JSON.parse(fs.readFileSync(file, "utf8"));
  const parsed = GameRecordSchema.safeParse(raw);
  const record: GameRecord = decompressGameRecord(parsed.success ? parsed.data : (raw as GameRecord));
  const info = record.info;
  const gameStart = toWireGameStartInfo({ gameID: info.gameID, lobbyCreatedAt: info.lobbyCreatedAt,
    config: info.config, players: info.players, tribes: info.tribes });

  // 1. Startbrief: genau wie der Server ihn schickt (ohne Tabelle), und wieder lesen.
  const startBytes = encodeServerMessage(
    { type: "start", gameStartInfo: gameStart, turns: [],
      lobbyCreatedAt: info.lobbyCreatedAt ?? 0,
      myClientID: info.players[0]?.clientID } as any, undefined);
  const start: any = decodeServerMessage(startBytes, undefined);
  const ctx = createGameWireContext(start.gameStartInfo.players);

  // 2. Züge: mit Tabelle kodieren und wieder lesen, Byte- und Inhaltsvergleich.
  let bytesGesamt = 0, intents = 0, gelesen = 0, ungleich = 0;
  const groessen: number[] = [];
  const zuege: any[] = [];
  for (const turn of record.turns.slice(0, TICKS)) {
    const b = encodeServerMessage({ type: "turn", turn } as any, ctx);
    const m: any = decodeServerMessage(b, ctx);
    bytesGesamt += b.length;
    groessen.push(b.length);
    gelesen++;
    intents += m.turn.intents.length;
    if (JSON.stringify(m.turn) !== JSON.stringify(turn)) ungleich++;
    zuege.push(m.turn);
  }

  // 3. Nachspielen und Beobachtung rechnen — der Teil, der im Worker der Erweiterung liefe.
  const config = new Config(info.config, null, false);
  const terrain = await loadTerrainMap(info.config.gameMap, info.config.gameMapSize,
    new NodeGameMapLoader(path.resolve("resources/maps")), false);
  const random = new PseudoRandom(simpleHash(gameStart.gameID));
  const humans = gameStart.players.map((p) => new PlayerInfo(p.username, PlayerType.Human,
    p.clientID, random.nextID(), p.isLobbyCreator ?? false, p.clanTag, p.friends ?? [], p.teamIndex ?? null));
  const nations = createNationsForGame(gameStart, terrain.nations, terrain.additionalNations, humans.length, random);
  const game = createGame(humans, nations, terrain.gameMap, terrain.miniGameMap, config, terrain.teamGameSpawnAreas);
  let fatal: string | undefined;
  const runner = new GameRunner(game, new Executor(game, gameStart.gameID, undefined,
    gameStart.tribes?.map((t) => t.name)), (gu: any) => { if ("errMsg" in gu) fatal = gu.errMsg; });
  runner.init();
  const t0 = performance.now();
  for (const turn of zuege) { runner.addTurn(turn); runner.executeNextTick(); if (fatal) break; }
  const msNachspielen = performance.now() - t0;

  const anfr = new AiAnfrage(info.config);
  const wer = game.players().find((p) => p.type() === PlayerType.Human && p.isAlive());
  let msAnfrage = -1, anfrageBytes = -1;
  if (wer) {
    const t1 = performance.now();
    const a: any = anfr.baue(game, wer as Player, 32);
    msAnfrage = performance.now() - t1;
    anfrageBytes = JSON.stringify(a).length;
  }
  const mem = process.memoryUsage();
  groessen.sort((a, b) => a - b);
  process.stdout.write(JSON.stringify({
    karte: info.config.gameMap, spieler: gameStart.players.length,
    zuege_gelesen: gelesen, zuege_ungleich: ungleich, intents,
    bytes_gesamt: bytesGesamt, byte_je_zug_median: groessen[groessen.length >> 1],
    byte_je_zug_max: groessen[groessen.length - 1],
    nachspielen_ms: Math.round(msNachspielen),
    ms_je_tick: +(msNachspielen / Math.max(1, gelesen)).toFixed(2),
    beobachtung_ms: +msAnfrage.toFixed(1), anfrage_bytes: anfrageBytes,
    speicher_mb: Math.round(mem.heapUsed / 1048576), rss_mb: Math.round(mem.rss / 1048576),
    fatal: fatal ?? null,
    ok: ungleich === 0 && gelesen > 0 && !fatal,
  }) + "\n");
}

main().catch((e) => { console.error(e?.stack ?? e); process.exit(1); });

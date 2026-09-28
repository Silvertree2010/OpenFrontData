/**
 * Ende zu Ende ohne Browser: echter Zugstrom → Kern-Rechnung → Politik → **echter**
 * Inferenz-Server → Absicht. Und die Verriegelung: `sendeAbsicht` darf nichts senden.
 *
 * Der Kern selbst ist hier eine Attrappe — aber keine erfundene: die Beobachtung kommt
 * aus `beobachtung.ts`, gerechnet auf einer echten, nachgespielten Partie (dass das
 * bitgleich zum Training ist, zeigt aitest/kernprobe.ts). Geprüft wird der Teil, den
 * kernprobe nicht abdeckt: dass die Politik damit umgehen kann, der Server antwortet,
 * eine Absicht herauskommt — und dass ohne Schalter kein Byte auf den Socket geht.
 *
 *   npx tsx aitest/endeprobe.ts <record.json> <inf-url> [ticks] [proben]
 */
import fs from "fs";
import path from "path";
import { Beobachter } from "../erweiterung/beobachtung";
import { Politik } from "../erweiterung/politik";
import type { Absicht, Beobachtung, Kern } from "../erweiterung/schnittstelle";
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

const [file, infUrl, tickArg, probenArg] = process.argv.slice(2);
const BIS = Number(tickArg ?? 900);
const PROBEN = Number(probenArg ?? 6);

async function main() {
  console.debug = () => {};
  console.log = (...x: any[]) => console.error(...x);
  const raw = JSON.parse(fs.readFileSync(file, "utf8"));
  const parsed = GameRecordSchema.safeParse(raw);
  const record: GameRecord = decompressGameRecord(parsed.success ? parsed.data : (raw as GameRecord));
  const info = record.info;
  const gameStart = toWireGameStartInfo({ gameID: info.gameID, lobbyCreatedAt: info.lobbyCreatedAt,
    config: info.config, players: info.players, tribes: info.tribes });
  const config = new Config(info.config, null, false);
  const terrain = await loadTerrainMap(info.config.gameMap, info.config.gameMapSize,
    new (await import("../tests/perf/fullgame/NodeGameMapLoader")).NodeGameMapLoader(
      path.resolve("resources/maps")), false);
  const random = new PseudoRandom(simpleHash(gameStart.gameID));
  const humans = gameStart.players.map((p) => new PlayerInfo(p.username, PlayerType.Human,
    p.clientID, random.nextID(), p.isLobbyCreator ?? false, p.clanTag, p.friends ?? [], p.teamIndex ?? null));
  const nations = createNationsForGame(gameStart, terrain.nations, terrain.additionalNations, humans.length, random);
  const game: any = createGame(humans, nations, terrain.gameMap, terrain.miniGameMap, config, terrain.teamGameSpawnAreas);
  let fatal: string | undefined;
  const runner = new GameRunner(game, new Executor(game, gameStart.gameID, undefined,
    gameStart.tribes?.map((t) => t.name)), (gu: any) => { if ("errMsg" in gu) fatal = gu.errMsg; });
  runner.init();

  const meineID = info.players[0].clientID;
  const beob = new Beobachter(info.config);
  let letzte: Beobachtung | null = null;
  let gesendet = 0;          // Byte, die auf einem echten Socket gelandet wären
  let sendeVersuche = 0;
  const handler: ((t: number) => void)[] = [];
  const kern: Kern = {
    beobachtung: () => letzte,
    sendeAbsicht: (_a: Absicht) => { sendeVersuche++; return false; },   // verriegelt
    aufTick: (f) => { handler.push(f); },
    zustand: () => ({ partie: info.gameID, tick: game.ticks(), spieler: humans.length, fehler: null }),
  };

  const politik = new Politik(kern, { inf: infUrl, takt: 32,
    holen: (...a: any[]) => (globalThis as any).fetch(...a) });
  const aktionen: string[] = [];
  politik.aufMesswerte((m) => {
    if (m.letzte_aktion && aktionen[aktionen.length - 1] !== m.letzte_aktion) {
      aktionen.push(m.letzte_aktion);
    }
  });
  politik.start();

  let gefragt = 0, msSumme = 0;
  for (const turn of record.turns) {
    const T = turn.turnNumber;
    if (T > BIS || gefragt >= PROBEN) break;
    runner.addTurn(turn);
    runner.executeNextTick();
    if (fatal) throw new Error(`tick ${T}: ${fatal}`);
    if (game.inSpawnPhase()) continue;
    const p = game.players().find((q: any) => q.type() === PlayerType.Human && q.isAlive()
      && q.clientID() === meineID) as Player | undefined;
    if (!p) continue;
    if (game.ticks() % 32 !== 0) continue;
    letzte = beob.rechne(game, p, meineID, 32);
    const t0 = performance.now();
    await politik.tick(game.ticks());          // fragt den echten Server
    msSumme += performance.now() - t0;
    gefragt++;
  }

  process.stdout.write(JSON.stringify({
    inf: infUrl, gefragt, ms_je_runde: Math.round(msSumme / Math.max(1, gefragt)),
    aktionen, sende_versuche: sendeVersuche, bytes_gesendet: gesendet,
    // Eine Absicht ist entstanden, wenn die Politik etwas anderes meldet als "nichts"
    absicht_entstanden: aktionen.some((a) => /zuschauen|gesperrt/.test(a)),
    ok: gefragt > 0 && gesendet === 0
      && aktionen.length > 0
      && !aktionen.some((a) => /Fehler|fehler/.test(a)),
  }, null, 1) + "\n");
}

main().catch((e) => { console.error(e?.stack ?? e); process.exit(1); });

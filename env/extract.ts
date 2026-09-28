/**
 * Label-Extraktor — die Brücke Engine → Trainingslabel.
 *
 * Replayt einen Record und emittiert pro echtem Zug ein JSONL-Sample:
 *   Kontext (Gegner-Reihenfolge WIE IN obs.encodeVec, Truppen, Gold, Kartenmaße,
 *   eigene Einheiten/Angriffe) + der rohe Intent.
 * Der Kontext wird VOR dem Ausführen des Turns gelesen — das ist der Zustand,
 * den der Spieler bei der Entscheidung sah.
 *
 * Die Beobachtungs-TENSOREN werden hier NICHT geschrieben (25 TB für alle
 * Samples — sie entstehen beim Training on-the-fly). Hier geht es nur darum zu
 * beweisen, dass der echte Engine-Kontext + echte Intents saubere Labels ergeben
 * (actions.encode auf der Python-Seite). Gegner-Reihenfolge muss exakt der von
 * obs.encodeVec entsprechen, sonst zeigt der Ziel-Zeiger ins Leere.
 *
 * Nichtstun: mit NOOP_EVERY=<K> entsteht zusaetzlich je K-tem intentlosen Tick
 * und Spieler ein Sample mit intent {"type":"no_op"} und Gewicht w=K (Kehrwert
 * der Abtastwahrscheinlichkeit). Gleiche Semantik wie in materialize.ts.
 *
 *   npx tsx env/extract.ts <record.json> <out.jsonl> [--every 1]
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
import { ObsEncoder } from "./obs";

const ENGINE = path.join(path.dirname(fileURLToPath(import.meta.url)), "../vendor/openfront");

async function main() {
  const [file, outPath] = process.argv.slice(2);
  if (!file || !outPath) { console.error("Aufruf: extract.ts <record.json> <out.jsonl>"); process.exit(2); }
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
  const W = game.width(), H = game.height();
  const out = fs.createWriteStream(outPath);

  // clientID der handelnden Menschen (Nationen/Bots emittieren keine Intents mit clientID)
  const humanClient = new Set(gameStart.players.map((p) => p.clientID));

  let samples = 0, skippedNoPlayer = 0, noops = 0, noopChances = 0, byType: Record<string, number> = {};
  const t0 = performance.now();

  // Nichtstun-Abtastung (siehe materialize.ts) — 0 = aus, Verhalten wie bisher.
  const NOOP_EVERY = Number(process.env.NOOP_EVERY ?? 0);
  const NOOP_ALIGN = process.env.NOOP_ALIGN === "1";
  const NOOP_INTENT = { type: "no_op" };
  const phases = new Map<string, number>();
  const phaseOf = (cid: string) => {
    let ph = phases.get(cid);
    if (ph === undefined) { ph = NOOP_ALIGN ? 0 : Math.abs(simpleHash(cid)) % NOOP_EVERY; phases.set(cid, ph); }
    return ph;
  };
  const disconnected = new Set<string>();

  // Kontext eines Spielers im aktuellen Tick (encodeVec ist teuer → je Tick cachen).
  const ctxCache = new Map<string, any>();
  const ctxFor = (player: any, cid: string) => {
    let ctx = ctxCache.get(cid);
    if (!ctx) {
      const v = enc.encodeVec(game, player, 24);
      // Gegner-Reihenfolge → PlayerID (player.id()). Intent-Zielfelder
      // (targetID/recipient/target/requestor) sind PlayerIDs, NICHT clientIDs
      // — und Nationen/Bots haben eine id(), aber keine clientID. Der
      // Handelnde wird dagegen per clientID gefunden (playerByClientID).
      const oppIds = v.opponents.map((o) => {
        const pl = game.playerBySmallID(o.id);
        return pl.isPlayer() ? pl.id() : null;
      });
      ctx = {
        mapW: W, mapH: H,
        troops: player.troops(), gold: Number(player.gold()),
        oppIds,
        ownUnitIds: player.units().map((u: any) => u.id()),
        ownAttackIds: player.outgoingAttacks().map((a: any) => a.id()),
      };
      ctxCache.set(cid, ctx);
    }
    return ctx;
  };

  for (const turn of record.turns) {
    const intents = turn.intents ?? [];
    for (const i of intents as any[]) {
      if (i.type === "mark_disconnected" && i.clientID) {
        if (i.isDisconnected === false) disconnected.delete(i.clientID);
        else disconnected.add(i.clientID);
      }
    }
    // VOR dem Ausführen: Zustand = was der Spieler bei der Entscheidung sah
    if ((intents.length > 0 || NOOP_EVERY > 0) && !game.inSpawnPhase()) {
      ctxCache.clear();
      const acted = new Set<string>((intents as any[])
        .filter((i) => i.clientID && humanClient.has(i.clientID) && i.type !== "mark_disconnected")
        .map((i) => i.clientID));
      for (const intent of intents) {
        const cid = (intent as any).clientID;
        if (!cid || !humanClient.has(cid)) continue;
        const ty = (intent as any).type;
        byType[ty] = (byType[ty] ?? 0) + 1;

        // Spieler über clientID finden
        const player = game.players().find((p) => p.clientID() === cid);
        if (!player || !player.isAlive()) { skippedNoPlayer++; continue; }

        const ctx = ctxFor(player, cid);
        out.write(JSON.stringify({ turn: turn.turnNumber, clientID: cid, ...ctx, intent, w: 1 }) + "\n");
        samples++;
      }
      // Nichtstun: je K-tem intentlosen Tick ein Sample aus Sicht dieses Spielers
      if (NOOP_EVERY > 0) for (const player of game.players()) {
        if (!player.isAlive()) continue;
        const cid = player.clientID();
        if (!cid || !humanClient.has(cid) || acted.has(cid) || disconnected.has(cid)) continue;
        noopChances++;
        if ((turn.turnNumber + phaseOf(cid)) % NOOP_EVERY !== 0) continue;
        const ctx = ctxFor(player, cid);
        out.write(JSON.stringify({ turn: turn.turnNumber, clientID: cid, ...ctx,
          intent: NOOP_INTENT, w: NOOP_EVERY }) + "\n");
        samples++; noops++;
        byType["no_op"] = (byType["no_op"] ?? 0) + 1;
      }
    }
    runner.addTurn(turn);
    runner.executeNextTick();
    if (fatal) { console.error("Fehler:", fatal); process.exit(1); }
  }
  out.end();
  console.log(`${info.gameID} ${info.config.gameMap}: ${samples} Samples (${noops} no-op von ${noopChances} ` +
    `Gelegenheiten), ${skippedNoPlayer} ohne lebenden Spieler, ` +
    `${((performance.now() - t0) / 1000).toFixed(1)}s`);
  console.log("Intent-Typen:", JSON.stringify(byType));
}
main();

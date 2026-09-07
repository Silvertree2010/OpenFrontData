/**
 * Materializer — Record → Trainings-Shards auf Platte.
 *
 * Replayt ein Spiel und schreibt pro Entscheidungs-Sample (handelnder Mensch bei
 * seinem Zug):
 *   <id>.meta.zst  zstd(JSONL): je Zeile { ctx (fuer Label-encode), own, opps, intent }
 *   <id>.maps      Folge von [uint32 LE Laenge][zstd(Karten-uint8-Block)], 1:1 zu meta
 *
 * Die Karte (18×90×180) wird float→uint8 quantisiert und je Block einzeln zstd-t
 * (~11 KB/Sample, gemessen). Kein 25-TB-Problem: der ganze Datensatz ~205 GB.
 * Featurisieren + Label-encode passiert beim Training in Python (single source of
 * truth: featurize.py + actions.encode), damit man ohne Neu-Extraktion nachjustieren
 * kann. Die Beobachtung ist der Zustand VOR dem Zug (was der Spieler sah).
 *
 *   npx tsx env/materialize.ts <record.json> <outdir>
 */
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";
import { zstdCompressSync } from "zlib";
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
import { ObsEncoder, NUM_CHANNELS } from "./obs";

const ENGINE = path.join(path.dirname(fileURLToPath(import.meta.url)), "../vendor/openfront");
const GW = 180, GH = 90, MAPLEN = NUM_CHANNELS * GW * GH;

async function main() {
  const [file, outdir] = process.argv.slice(2);
  if (!file || !outdir) { console.error("Aufruf: materialize.ts <record.json> <outdir>"); process.exit(2); }
  console.debug = () => {};
  fs.mkdirSync(outdir, { recursive: true });

  const raw = JSON.parse(fs.readFileSync(file, "utf8"));
  const parsed = GameRecordSchema.safeParse(raw);
  const record: GameRecord = decompressGameRecord(parsed.success ? parsed.data : (raw as GameRecord));
  const info = record.info;

  // Sieger-clientIDs (fuer den Wert-Kopf: hat dieser Spieler gewonnen?)
  const winners = new Set<string>();
  const w: any = info.winner;
  if (Array.isArray(w) && w.length > 1) {
    if (w[0] === "team") for (const x of w.slice(2)) { if (typeof x === "string") winners.add(x); }
    else if (w[0] === "player" && typeof w[1] === "string") winners.add(w[1]);
  }

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

  const enc = new ObsEncoder(GW, GH);
  const W = game.width(), H = game.height();
  const humanClient = new Set(gameStart.players.map((p) => p.clientID));

  const mapsPath = path.join(outdir, `${info.gameID}.maps`);
  const mapsFd = fs.openSync(mapsPath, "w");
  const metaLines: string[] = [];
  const obsBuf = new Float32Array(MAPLEN);
  const u8 = new Uint8Array(MAPLEN);
  const lenBuf = Buffer.allocUnsafe(4);

  let samples = 0, scannedTick = -1;
  const t0 = performance.now();

  for (const turn of record.turns) {
    const intents = turn.intents ?? [];
    const acting = intents.filter((i: any) => i.clientID && humanClient.has(i.clientID) && i.type !== "mark_disconnected");
    if (acting.length > 0 && !game.inSpawnPhase()) {
      // Kontext + Karte je handelndem Spieler EINMAL pro Tick (mehrere Intents teilen sie)
      const ctxCache = new Map<string, any>();
      const mapCache = new Map<string, Buffer>();
      for (const intent of acting) {
        const cid = (intent as any).clientID;
        const player = game.players().find((p) => p.clientID() === cid);
        if (!player || !player.isAlive()) continue;

        let ctx = ctxCache.get(cid);
        let zblock = mapCache.get(cid);
        if (!ctx) {
          if (scannedTick !== turn.turnNumber) { enc.scanTick(game); scannedTick = turn.turnNumber; }
          const v = enc.encodeVec(game, player, 24);
          const allies = new Set(player.allies().map((a) => a.smallID()));
          enc.encodeMap(player, allies, obsBuf);
          for (let k = 0; k < MAPLEN; k++) {
            let x = obsBuf[k]; if (x < -1) x = -1; else if (x > 1) x = 1;
            u8[k] = Math.round((x + 1) * 127.5);
          }
          zblock = zstdCompressSync(Buffer.from(u8.buffer, 0, MAPLEN));
          // Gegner-Reihenfolge → PlayerIDs (Label-Ziele sind PlayerIDs); plus user/clan fuer Reputation
          const oppIds = v.opponents.map((o) => {
            const pl = game.playerBySmallID(o.id);
            return pl.isPlayer() ? pl.id() : null;
          });
          const opps = v.opponents.map((o, idx) => {
            const pl = game.playerBySmallID(o.id);
            return { ...o, user: pl.isPlayer() ? pl.name() : null, clan: pl.isPlayer() ? pl.clanTag() : null };
          });
          ctx = {
            mapW: W, mapH: H, troops: player.troops(), gold: Number(player.gold()),
            oppIds, ownUnitIds: player.units().map((u) => u.id()),
            ownAttackIds: player.outgoingAttacks().map((a) => a.id()),
            own: v.own, opps,
          };
          ctxCache.set(cid, ctx); mapCache.set(cid, zblock);
        }
        // meta-Zeile: alles fuer Label-encode (Python) + obs-Features
        metaLines.push(JSON.stringify({ turn: turn.turnNumber, clientID: cid,
          mapW: ctx.mapW, mapH: ctx.mapH, troops: ctx.troops, gold: ctx.gold,
          oppIds: ctx.oppIds, ownUnitIds: ctx.ownUnitIds, ownAttackIds: ctx.ownAttackIds,
          own: ctx.own, opps: ctx.opps, intent, win: winners.has(cid) ? 1 : 0 }));
        lenBuf.writeUInt32LE(zblock!.length, 0);
        fs.writeSync(mapsFd, lenBuf); fs.writeSync(mapsFd, zblock!);
        samples++;
      }
    }
    runner.addTurn(turn);
    runner.executeNextTick();
    if (fatal) { console.error("Fehler:", fatal); fs.closeSync(mapsFd); process.exit(1); }
  }
  fs.closeSync(mapsFd);
  fs.writeFileSync(path.join(outdir, `${info.gameID}.meta.zst`), zstdCompressSync(Buffer.from(metaLines.join("\n"))));

  const mb = fs.statSync(mapsPath).size / 1e6;
  console.log(`${info.gameID} ${info.config.gameMap}: ${samples} Samples, maps ${mb.toFixed(1)} MB ` +
    `(${(mb * 1000 / Math.max(samples, 1)).toFixed(1)} KB/Sample), ${((performance.now() - t0) / 1000).toFixed(0)}s`);
}
main();

/**
 * Der Beleg für den Kern der Erweiterung, ohne Browser.
 *
 * Der ganze Weg wird nachgestellt, wie er im Fenster liefe:
 *   Zugstrom als Server-Rahmen kodiert → mit `decodeServerMessage` gelesen (wie kern.ts)
 *   → Zug für Zug durch die Engine (wie engineSpiegel.ts) → Beobachtung gerechnet
 *   (wie beobachtung.ts) → Byte für Byte gegen die Trainingsblöcke des Materialisierers.
 *
 * Verglichen wird an genau den Ticks, an denen der Materialisierer ein räumliches
 * Sample geschrieben hat: Kartenblock (18*90*180) und Zellfakten (owner, frac, legal).
 *
 *   npx tsx aitest/kernprobe.ts <record.json> <materialisierer-ordner> <gid> [proben]
 */
import fs from "fs";
import path from "path";
import { zstdDecompressSync } from "zlib";
import { Beobachter } from "../erweiterung/beobachtung";
import { Config } from "../src/core/configuration/Config";
import { Executor } from "../src/core/execution/ExecutionManager";
import { Player, PlayerInfo, PlayerType } from "../src/core/game/Game";
import { createGame } from "../src/core/game/GameImpl";
import { GameUpdateType } from "../src/core/game/GameUpdates";
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

const [file, outdir, gid, probenArg] = process.argv.slice(2);
const PROBEN = Number(probenArg ?? 20);
const N = 16200;

function bloecke(p: string): Buffer[] {
  const d = fs.readFileSync(p);
  const out: Buffer[] = [];
  for (let off = 0; off < d.length; ) {
    const n = d.readUInt32LE(off);
    out.push(zstdDecompressSync(d.subarray(off + 4, off + 4 + n)));
    off += 4 + n;
  }
  return out;
}

async function main() {
  console.debug = () => {};
  console.log = (...x: any[]) => console.error(...x);
  const meta = zstdDecompressSync(fs.readFileSync(path.join(outdir, `${gid}.meta.zst`)))
    .toString("utf8").split("\n").filter((l) => l).map((l) => JSON.parse(l));
  const karten = bloecke(path.join(outdir, `${gid}.maps`));
  const zellen = bloecke(path.join(outdir, `${gid}.cells`));
  const proTick = new Map<number, { sid: number; cell: number; zeile: number }[]>();
  for (let i = 0; i < meta.length; i++) {
    const m = meta[i];
    if (!(m.cell >= 0)) continue;
    if (!proTick.has(m.tick)) proTick.set(m.tick, []);
    proTick.get(m.tick)!.push({ sid: m.sid, cell: m.cell, zeile: i });
  }
  const ticks = [...proTick.keys()].sort((a, b) => a - b);
  const n = Math.min(PROBEN, ticks.length);
  const wahl = new Set<number>();
  for (let k = 0; k < n; k++) wahl.add(ticks[Math.floor((k * ticks.length) / n)]);
  const letzter = Math.max(...wahl);

  const raw = JSON.parse(fs.readFileSync(file, "utf8"));
  const parsed = GameRecordSchema.safeParse(raw);
  const record: GameRecord = decompressGameRecord(parsed.success ? parsed.data : (raw as GameRecord));
  const info = record.info;
  const gameStart = toWireGameStartInfo({ gameID: info.gameID, lobbyCreatedAt: info.lobbyCreatedAt,
    config: info.config, players: info.players, tribes: info.tribes });

  // ── wie kern.ts: Startbrief lesen, Tabelle bauen, eigene Kennung übernehmen ──
  const startBytes = encodeServerMessage({ type: "start", gameStartInfo: gameStart, turns: [],
    lobbyCreatedAt: info.lobbyCreatedAt ?? 0, myClientID: info.players[0]?.clientID } as any, undefined);
  const start: any = decodeServerMessage(startBytes, undefined);
  const ctx = createGameWireContext(start.gameStartInfo.players);
  const meineID: string = start.myClientID ?? "";

  // ── wie engineSpiegel.ts: Partie aufsetzen ──
  const config = new Config(info.config, null, false);
  const terrain = await loadTerrainMap(info.config.gameMap, info.config.gameMapSize,
    new NodeGameMapLoader(path.resolve("resources/maps")), false);
  const random = new PseudoRandom(simpleHash(gameStart.gameID));
  const humans = gameStart.players.map((p) => new PlayerInfo(p.username, PlayerType.Human,
    p.clientID, random.nextID(), p.isLobbyCreator ?? false, p.clanTag, p.friends ?? [], p.teamIndex ?? null));
  const nations = createNationsForGame(gameStart, terrain.nations, terrain.additionalNations, humans.length, random);
  const game = createGame(humans, nations, terrain.gameMap, terrain.miniGameMap, config, terrain.teamGameSpawnAreas);
  let fatal: string | undefined;
  let cur: any = null;
  const runner = new GameRunner(game, new Executor(game, gameStart.gameID, undefined,
    gameStart.tribes?.map((t) => t.name)), (gu: any) => { if ("errMsg" in gu) fatal = gu.errMsg; else cur = gu; });
  runner.init();

  const beob = new Beobachter(info.config);
  const z = { proben: 0, karteFalsch: 0, ownerFalsch: 0, fracFalsch: 0, legalFalsch: 0,
    hashOk: 0, hashFalsch: 0, rahmen: 0, rahmenUngleich: 0,
    eigeneIDGefunden: meineID !== "" && humans.some((h) => h.clientID === meineID),
    startphaseAmAnfang: game.inSpawnPhase(), startphaseEndeTick: -1, beispiele: [] as any[] };

  for (const turn of record.turns) {
    const T = turn.turnNumber;
    if (game.ticks() !== T) throw new Error(`ticks ${game.ticks()} != turn ${T}`);
    if (z.startphaseEndeTick < 0 && !game.inSpawnPhase()) z.startphaseEndeTick = T;
    if (wahl.has(T)) {
      for (const { sid, zeile, cell } of proTick.get(T)!) {
        const spieler = game.playerBySmallID(sid) as Player;
        const b: any = beob.rechne(game, spieler, meineID, 32);
        z.proben++;
        const kref = karten[zeile];
        let falsch = b.karte.length !== kref.length;
        if (!falsch) for (let i = 0; i < kref.length; i++) if (b.karte[i] !== kref[i]) { falsch = true; break; }
        if (falsch) z.karteFalsch++;
        const cref = zellen[cell];
        let of_ = 0, ff = 0, lf = 0;
        for (let i = 0; i < N; i++) {
          if (b.owner[i] !== (cref[2 * i] | (cref[2 * i + 1] << 8))) of_++;
          if (b.frac[i] !== cref[2 * N + i]) ff++;
          if (b.legal[i] !== cref[3 * N + i]) lf++;
        }
        if (of_) z.ownerFalsch++;
        if (ff) z.fracFalsch++;
        if (lf) z.legalFalsch++;
        if ((falsch || of_ || ff || lf) && z.beispiele.length < 3) {
          z.beispiele.push({ T, sid, karte: falsch, owner: of_, frac: ff, legal: lf });
        }
        // Vektoren: nicht leer, und die eigenen Kennzahlen passen zur Metazeile
        if (z.proben === 1) {
          z.beispiele.push({ hinweis: "erste Probe", tick: b.tick, lebe: b.lebe,
            startphase: b.startphase, own0: b.own[0], oppPlaetze: b.oppMaske.reduce((a: number, c: number) => a + c, 0),
            karteBytes: b.karte.length, configLen: b.config.length });
        }
      }
    }
    if (T >= letzter) break;
    // ── wie kern.ts: Zug als Rahmen kodieren, wieder lesen, dann ausführen ──
    const bytes = encodeServerMessage({ type: "turn", turn } as any, ctx);
    const m: any = decodeServerMessage(bytes, ctx);
    z.rahmen++;
    if (JSON.stringify(m.turn) !== JSON.stringify(turn)) z.rahmenUngleich++;
    runner.addTurn(m.turn);
    runner.executeNextTick();
    if (fatal) throw new Error(`tick ${T}: ${fatal}`);
    for (const h of (cur?.updates?.[GameUpdateType.Hash] ?? []) as any[]) {
      if (h.tick !== T || record.turns[T]?.hash == null) continue;
      if (record.turns[T].hash === h.hash) z.hashOk++; else z.hashFalsch++;
    }
  }
  process.stdout.write(JSON.stringify({ gid, ...z,
    ok: z.proben > 0 && z.karteFalsch === 0 && z.ownerFalsch === 0 && z.fracFalsch === 0
      && z.legalFalsch === 0 && z.rahmenUngleich === 0 && z.hashFalsch === 0 }) + "\n");
}

main().catch((e) => { console.error(e?.stack ?? e); process.exit(1); });

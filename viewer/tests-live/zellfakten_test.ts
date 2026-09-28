/**
 * Clientseitiger Test: Die in den Viewer portierten Zellfakten (src/core/cellFacts.ts)
 * müssen bitgleich zu den .cells-Blöcken des Materialisierers sein.
 *
 * Spielt eine echte Partie im gepatchten Client nach (Setup wie
 * materializer/tests/cells/harness.ts) und rechnet an den Ticks räumlicher Samples für
 * denselben Spieler; Vergleich Byte für Byte (owner_major, own_frac, legal).
 * Tick-Konvention wie im Materialisierer: vor addTurn/executeNextTick, game.ticks() == T.
 *
 * Auf dem AKTUELLEN Client (live-client) beantwortet derselbe Lauf zwei Fragen getrennt:
 *   1. Spielt die neue Engine die Aufzeichnung noch bitgleich nach? Das sagt der
 *      Zustands-Hash je Tick (record.turns[T].hash gegen GameUpdateType.Hash).
 *   2. Rechnet der portierte Code auf identischem Zustand dieselben Zellfakten? Das
 *      gilt nur fuer Ticks VOR dem ersten Hash-Unterschied — danach vergleicht man
 *      zwei verschiedene Spiele, nicht zwei Kodierungen.
 *
 *   npx tsx aitest/zellfakten_test.ts <record.json> <materialisierer-ordner> <gid> [maxTicks]
 */
import fs from "fs";
import path from "path";
import { zstdDecompressSync } from "zlib";
import { AiAnfrage } from "../src/core/aiAnfrage";
import { CELL_BYTES, CellFacts } from "../src/core/cellFacts";
import { Config } from "../src/core/configuration/Config";
import { Executor } from "../src/core/execution/ExecutionManager";
import { Player, PlayerInfo, PlayerType } from "../src/core/game/Game";
import { createGame } from "../src/core/game/GameImpl";
import { GameUpdateType } from "../src/core/game/GameUpdates";
import { createNationsForGame } from "../src/core/game/NationCreation";
import { loadTerrainMap } from "../src/core/game/TerrainMapLoader";
import { GameRunner } from "../src/core/GameRunner";
import { ObsEncoder } from "../src/core/obsModel";
import { PseudoRandom } from "../src/core/PseudoRandom";
import { GameRecord, GameRecordSchema } from "../src/core/Schemas";
import { decompressGameRecord, simpleHash, toWireGameStartInfo } from "../src/core/Util";
import { NodeGameMapLoader } from "../tests/perf/fullgame/NodeGameMapLoader";

const [file, outdir, gid, maxArg, bisArg] = process.argv.slice(2);
const MAX_TICKS = Number(maxArg ?? 60);
// Nur Proben bis zu diesem Tick betrachten. Damit laesst sich das Fenster ausmessen,
// in dem die neue Engine die Aufzeichnung nachweislich noch bitgleich nachspielt.
const BIS_TICK = Number(bisArg ?? Infinity);
const N = 16200;

function bloecke(p: string): Buffer[] {
  const d = fs.readFileSync(p);
  const out: Buffer[] = [];
  let off = 0;
  while (off < d.length) {
    const n = d.readUInt32LE(off);
    off += 4;
    out.push(zstdDecompressSync(d.subarray(off, off + n)));
    off += n;
  }
  return out;
}

async function main() {
  console.debug = () => {};
  console.log = (...x: any[]) => console.error(...x);
  if (!file || !outdir || !gid) throw new Error("Aufruf: zellfakten_test.ts <record.json> <ordner> <gid> [maxTicks]");
  const ok = JSON.parse(fs.readFileSync(path.join(outdir, `${gid}.ok`), "utf8"));
  const meta = zstdDecompressSync(fs.readFileSync(path.join(outdir, `${gid}.meta.zst`)))
    .toString("utf8").split("\n").filter((l) => l).map((l) => JSON.parse(l));
  const ref = bloecke(path.join(outdir, `${gid}.cells`));
  const karten = bloecke(path.join(outdir, `${gid}.maps`));
  const proTick = new Map<number, { sid: number; cell: number; zeile: number }[]>();
  for (let i = 0; i < meta.length; i++) {
    const m = meta[i];
    if (!(m.cell >= 0)) continue;
    if (!proTick.has(m.tick)) proTick.set(m.tick, []);
    proTick.get(m.tick)!.push({ sid: m.sid, cell: m.cell, zeile: i });
  }
  const ticks = [...proTick.keys()].sort((x, y) => x - y).filter((t) => t <= BIS_TICK);
  const n = Math.min(MAX_TICKS, ticks.length);
  const wahl = new Set<number>();
  for (let k = 0; k < n; k++) wahl.add(ticks[Math.floor((k * ticks.length) / n)]);
  const letzter = Math.max(...wahl);

  // Setup wie materializer/tests/cells/harness.ts
  const raw = JSON.parse(fs.readFileSync(file, "utf8"));
  const parsed = GameRecordSchema.safeParse(raw);
  const record: GameRecord = decompressGameRecord(parsed.success ? parsed.data : (raw as GameRecord));
  const info = record.info;
  const gameStart = toWireGameStartInfo({
    gameID: info.gameID, lobbyCreatedAt: info.lobbyCreatedAt,
    config: info.config, players: info.players, tribes: info.tribes });
  const config = new Config(info.config, null, false);
  const terrain = await loadTerrainMap(info.config.gameMap, info.config.gameMapSize,
    new NodeGameMapLoader(path.resolve("resources/maps")), false);
  const random = new PseudoRandom(simpleHash(gameStart.gameID));
  const humans = gameStart.players.map((p) => new PlayerInfo(
    p.username, PlayerType.Human, p.clientID, random.nextID(),
    p.isLobbyCreator ?? false, p.clanTag, p.friends ?? [], p.teamIndex ?? null));
  const nations = createNationsForGame(gameStart, terrain.nations, terrain.additionalNations, humans.length, random);
  const game = createGame(humans, nations, terrain.gameMap, terrain.miniGameMap, config, terrain.teamGameSpawnAreas);
  let fatal: string | undefined;
  let cur: any = null;
  const runner = new GameRunner(game, new Executor(game, gameStart.gameID, undefined,
    gameStart.tribes?.map((t) => t.name)), (gu: any) => { if ("errMsg" in gu) fatal = gu.errMsg; else cur = gu; });
  runner.init();

  const enc = new ObsEncoder(180, 90);
  const anfr = new AiAnfrage(info.config);   // wie im Worker: gameStartInfo.config
  const cells = new CellFacts(game, { legalBit1: ok.legal_bit1 !== false });
  const acc = { paare: 0, paareFalsch: 0, owner: 0, frac: 0, legal: 0, ticks: 0, hashOk: 0, hashFalsch: 0,
    // Getrennt gezaehlt: solange der Zustand nachweislich identisch ist (Hash stimmt),
    // misst ein Unterschied die KODIERUNG. Danach misst er nur noch die Engine.
    paareVorDrift: 0, paareFalschVorDrift: 0, ersterHashFehlerTick: -1,
    // Die 18 Kartenkanaele (u8, wie im Trainingsblock) und der Vektor, ebenfalls nur
    // im Fenster vor der ersten Zustandsabweichung aussagekraeftig.
    karteVorDrift: 0, karteFalschVorDrift: 0, vektorVorDrift: 0, vektorFalschVorDrift: 0,
    vektorFelder: [] as string[],
    beispiele: [] as any[] };
  for (const turn of record.turns) {
    const T = turn.turnNumber;
    if (game.ticks() !== T) throw new Error(`ticks ${game.ticks()} != turn ${T}`);
    if (wahl.has(T)) {
      acc.ticks++;
      enc.scanTick(game);
      const og = (enc as any).ownerG as Uint16Array;
      for (const { sid, cell, zeile } of proTick.get(T)!) {
        const spieler = game.playerBySmallID(sid) as Player;
        if (acc.ersterHashFehlerTick < 0) {
          // Karte und Vektor genau wie der Worker sie schickt (aiAnfrage.ts)
          const o: any = anfr.baue(game, spieler, 32);
          acc.karteVorDrift++;
          const kref = karten[zeile];
          let kf = anfr.u8.length !== kref.length;
          if (!kf) for (let i = 0; i < kref.length; i++) if (anfr.u8[i] !== kref[i]) { kf = true; break; }
          if (kf) acc.karteFalschVorDrift++;
          acc.vektorVorDrift++;
          const felder: string[] = [];
          const vgl = (wo: string, a: any, b: any) => {
            for (const k of new Set([...Object.keys(a ?? {}), ...Object.keys(b ?? {})])) {
              if (JSON.stringify(a?.[k]) !== JSON.stringify(b?.[k])) {
                felder.push(`${wo}.${k}: ${JSON.stringify(a?.[k])} statt ${JSON.stringify(b?.[k])}`);
              }
            }
          };
          vgl("own", o.own, meta[zeile].own);
          const mo = meta[zeile].opps ?? [];
          if ((o.opps?.length ?? 0) !== mo.length) felder.push(`opps.laenge ${o.opps?.length} statt ${mo.length}`);
          for (let q = 0; q < Math.min(o.opps?.length ?? 0, mo.length); q++) vgl(`opps[${q}]`, o.opps[q], mo[q]);
          if (felder.length) {
            acc.vektorFalschVorDrift++;
            for (const f of felder.slice(0, 4)) {
              if (acc.vektorFelder.length < 12) acc.vektorFelder.push(`T${T}/sid${sid} ${f}`);
            }
          }
        }
        const a = cells.compute(game, spieler, og);
        const b = ref[cell];
        if (a.length !== CELL_BYTES || b.length !== CELL_BYTES) throw new Error(`Länge ${a.length}/${b.length}`);
        let falsch = false;
        for (let i = 0; i < 2 * N; i++) if (a[i] !== b[i]) { acc.owner++; falsch = true; }
        for (let i = 2 * N; i < 3 * N; i++) if (a[i] !== b[i]) { acc.frac++; falsch = true; }
        for (let i = 3 * N; i < 4 * N; i++) {
          if (a[i] === b[i]) continue;
          acc.legal++;
          falsch = true;
          if (acc.beispiele.length < 5) acc.beispiele.push({ T, sid, zelle: i - 3 * N, port: a[i], ref: b[i] });
        }
        acc.paare++;
        if (falsch) acc.paareFalsch++;
        if (acc.ersterHashFehlerTick < 0) {
          acc.paareVorDrift++;
          if (falsch) acc.paareFalschVorDrift++;
        }
      }
    }
    if (T >= letzter) break;
    runner.addTurn(turn);
    runner.executeNextTick();
    if (fatal) throw new Error(`tick-Fehler T=${T}: ${fatal}`);
    for (const h of (cur?.updates?.[GameUpdateType.Hash] ?? []) as any[]) {
      if (h.tick !== T || record.turns[T]?.hash == null) continue;
      if (record.turns[T].hash === h.hash) { acc.hashOk++; continue; }
      acc.hashFalsch++;
      if (acc.ersterHashFehlerTick < 0) acc.ersterHashFehlerTick = T;
    }
  }
  process.stdout.write(JSON.stringify({ gid, ...acc, ok: acc.paare > 0 && acc.paareFalsch === 0 && acc.hashFalsch === 0,
    kodierung_ok: acc.paareVorDrift > 0 && acc.paareFalschVorDrift === 0
      && acc.karteFalschVorDrift === 0 && acc.vektorFalschVorDrift === 0 }) + "\n");
}

main().catch((e) => { console.error(e?.stack ?? e); process.exit(1); });

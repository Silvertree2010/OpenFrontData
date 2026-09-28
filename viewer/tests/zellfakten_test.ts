/**
 * Clientseitiger Test: Die in den Viewer portierten Zellfakten (src/core/cellFacts.ts)
 * müssen bitgleich zu den .cells-Blöcken des Materialisierers sein.
 *
 * Spielt eine echte Partie im gepatchten Client nach (Setup wie
 * materializer/tests/cells/harness.ts) und rechnet an den Ticks räumlicher Samples für
 * denselben Spieler; Vergleich Byte für Byte (owner_major, own_frac, legal).
 * Tick-Konvention wie im Materialisierer: vor addTurn/executeNextTick, game.ticks() == T.
 *
 *   cd <OpenFrontIO bei 88cc95d8 mit ai-viewer.patch>
 *   npx tsx aitest/zellfakten_test.ts <record.json> <materialisierer-ordner> <gid> [maxTicks]
 */
import fs from "fs";
import path from "path";
import { zstdDecompressSync } from "zlib";
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

const [file, outdir, gid, maxArg] = process.argv.slice(2);
const MAX_TICKS = Number(maxArg ?? 60);
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
  const proTick = new Map<number, { sid: number; cell: number }[]>();
  for (const m of meta) {
    if (!(m.cell >= 0)) continue;
    if (!proTick.has(m.tick)) proTick.set(m.tick, []);
    proTick.get(m.tick)!.push({ sid: m.sid, cell: m.cell });
  }
  const ticks = [...proTick.keys()].sort((x, y) => x - y);
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
  const cells = new CellFacts(game, { legalBit1: ok.legal_bit1 !== false });
  const acc = { paare: 0, paareFalsch: 0, owner: 0, frac: 0, legal: 0, ticks: 0, hashOk: 0, hashFalsch: 0,
    beispiele: [] as any[] };
  for (const turn of record.turns) {
    const T = turn.turnNumber;
    if (game.ticks() !== T) throw new Error(`ticks ${game.ticks()} != turn ${T}`);
    if (wahl.has(T)) {
      acc.ticks++;
      enc.scanTick(game);
      const og = (enc as any).ownerG as Uint16Array;
      for (const { sid, cell } of proTick.get(T)!) {
        const a = cells.compute(game, game.playerBySmallID(sid) as Player, og);
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
      }
    }
    if (T >= letzter) break;
    runner.addTurn(turn);
    runner.executeNextTick();
    if (fatal) throw new Error(`tick-Fehler T=${T}: ${fatal}`);
    for (const h of (cur?.updates?.[GameUpdateType.Hash] ?? []) as any[]) {
      if (h.tick !== T || record.turns[T]?.hash == null) continue;
      if (record.turns[T].hash === h.hash) acc.hashOk++; else acc.hashFalsch++;
    }
  }
  process.stdout.write(JSON.stringify({ gid, ...acc, ok: acc.paare > 0 && acc.paareFalsch === 0 && acc.hashFalsch === 0 }) + "\n");
}

main().catch((e) => { console.error(e?.stack ?? e); process.exit(1); });

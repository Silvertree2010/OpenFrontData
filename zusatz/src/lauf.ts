/**
 * Zusatzlauf: spielt eine Aufzeichnung noch einmal nach und schreibt NUR die neuen
 * Beobachtungsfelder (zusatz/src/felder.ts) als <gid>.zusatz.zst in einen eigenen Ordner.
 *
 * Deckungsgleich zu den bestehenden Metazeilen, und zwar per Konstruktion: die vorhandene
 * <gid>.meta.zst wird gelesen, und die Felder werden genau an deren (tick, sid) berechnet,
 * in der Reihenfolge der Metazeilen und in der Gegner-Reihenfolge der Metazeile (opps[].id).
 * Es wird also keine Sample-Auswahl nachgebaut, die driften könnte, und kein ObsEncoder
 * gebraucht: der Lauf kostet fast nur die Simulation. Die .maps bleiben unangetastet.
 *
 * Datei: zstd( Kopfzeile JSON + "\n" + Gegnerblock + globaler Block ).
 *   Gegnerblock  [samples][24][F_OPP], globaler Block [samples][F_GLOB], float16 wenn
 *   verfügbar (halbe Grösse), sonst float32 — steht als "dtype" im Kopf.
 * Der Kopf nennt Format, gid, samples, opp, beide Namenslisten und den Commit; der Leser
 * (materializer/py/reader.zusatz_lesen) prüft ihn.
 *
 * Aufruf (cwd = Baum mit vendor/openfront des passenden Commits):
 *   npx tsx zusatz/src/lauf.ts <record.json> <pool-ordner> <gid> <aus-ordner>
 * Der Pool wird nur gelesen, geschrieben wird allein <aus-ordner>/<gid>.zusatz.zst.
 */
import fs from "fs";
import path from "path";
import { zstdCompressSync, zstdDecompressSync } from "zlib";
import { Config } from "../../vendor/openfront/src/core/configuration/Config";
import { Executor } from "../../vendor/openfront/src/core/execution/ExecutionManager";
import { Game, Player, PlayerInfo, PlayerType, UnitType } from "../../vendor/openfront/src/core/game/Game";
import { createGame } from "../../vendor/openfront/src/core/game/GameImpl";
import { createNationsForGame } from "../../vendor/openfront/src/core/game/NationCreation";
import { loadTerrainMap } from "../../vendor/openfront/src/core/game/TerrainMapLoader";
import { GameRunner } from "../../vendor/openfront/src/core/GameRunner";
import { PseudoRandom } from "../../vendor/openfront/src/core/PseudoRandom";
import { GameRecord, GameRecordSchema } from "../../vendor/openfront/src/core/Schemas";
import { decompressGameRecord, simpleHash, toWireGameStartInfo } from "../../vendor/openfront/src/core/Util";
import { NodeGameMapLoader } from "../../vendor/openfront/tests/perf/fullgame/NodeGameMapLoader";
import { GameUpdateType } from "../../vendor/openfront/src/core/game/GameUpdates";
import { ANGR_FELDER, EINH_FELDER, F_ANGR, F_EINH, F_GLOB, F_OPP, GLOBAL_FELDER, MAX_ANGR, MAX_EINH,
  MAX_OPP, OPP_FELDER, ZusatzSpur } from "./felder";

const FORMAT = 2;
const F16 = typeof (globalThis as any).Float16Array !== "undefined" && process.env.ZUSATZ_F32 !== "1";

function puffer(a: Float32Array): { b: Buffer; dtype: string } {
  if (!F16) return { b: Buffer.from(a.buffer, a.byteOffset, a.byteLength), dtype: "float32" };
  const F16A = (globalThis as any).Float16Array;
  const h = new F16A(a.length);
  h.set(a);
  return { b: Buffer.from(h.buffer, h.byteOffset, h.byteLength), dtype: "float16" };
}

async function main() {
  console.debug = () => {};
  console.log = (...a: any[]) => console.error(...a);
  const [file, pool, gid, ausdir] = process.argv.slice(2);
  if (!file || !pool || !gid || !ausdir) {
    console.error("Aufruf: lauf.ts <record.json> <pool-ordner> <gid> <aus-ordner>");
    process.exit(2);
  }
  const t0 = performance.now();

  // ── Metazeilen: nur tick, sid und die Gegner-Reihenfolge ──
  const meta = zstdDecompressSync(fs.readFileSync(path.join(pool, `${gid}.meta.zst`)))
    .toString("utf8").split("\n").filter((l) => l).map((l) => JSON.parse(l));
  // ZUSATZ_META_ALT=1: Metadateien aus einer Vorstufe des Materialisierers haben statt
  // tick/sid nur turn/clientID. Nur zum Prüfen der Rechenwege ohne den v2-Pool —
  // die so erzeugten Dateien sind NICHT zeilengleich zum Pool und nicht fürs Training.
  const altModus = process.env.ZUSATZ_META_ALT === "1";
  if (altModus) for (const m of meta) if (m.tick === undefined) m.tick = m.turn;
  const proTick = new Map<number, number[]>();
  for (let i = 0; i < meta.length; i++) {
    const t = meta[i].tick;
    if (!proTick.has(t)) proTick.set(t, []);
    proTick.get(t)!.push(i);
  }
  const oppW = new Float32Array(meta.length * MAX_OPP * F_OPP);
  const globW = new Float32Array(meta.length * F_GLOB);
  const einhW = new Float32Array(meta.length * MAX_EINH * F_EINH);
  const angrW = new Float32Array(meta.length * MAX_ANGR * F_ANGR);

  // ── Engine aufsetzen wie materializer/src/materialize.ts ──
  const raw = JSON.parse(fs.readFileSync(file, "utf8"));
  const parsed = GameRecordSchema.safeParse(raw);
  const record: GameRecord = decompressGameRecord(parsed.success ? parsed.data : (raw as GameRecord));
  const info = record.info;
  const commit = String((record as any).gitCommit ?? raw.gitCommit ?? "");
  const gameStart = toWireGameStartInfo({
    gameID: info.gameID, lobbyCreatedAt: info.lobbyCreatedAt,
    config: info.config, players: info.players, tribes: info.tribes });
  const config = new Config(info.config, null, false);
  const terrain = await loadTerrainMap(info.config.gameMap, info.config.gameMapSize,
    new NodeGameMapLoader(path.resolve("vendor/openfront/resources/maps")), false);
  const random = new PseudoRandom(simpleHash(gameStart.gameID));
  const humans = gameStart.players.map((p) => new PlayerInfo(
    p.username, PlayerType.Human, p.clientID, random.nextID(),
    p.isLobbyCreator ?? false, p.clanTag, p.friends ?? [], p.teamIndex ?? null));
  const nations = createNationsForGame(gameStart, terrain.nations, terrain.additionalNations, humans.length, random);
  const game: Game = createGame(humans, nations, terrain.gameMap, terrain.miniGameMap, config,
    terrain.teamGameSpawnAreas);
  let fatal: string | undefined;
  let letzte: any = null;
  const runner = new GameRunner(game, new Executor(game, gameStart.gameID, undefined,
    gameStart.tribes?.map((t) => t.name)),
    (gu: any) => { if ("errMsg" in gu) fatal = gu.errMsg; else letzte = gu; });
  runner.init();
  if (altModus) {                       // sid aus clientID nachziehen (siehe oben)
    const alle: any[] = (game as any).allPlayers?.() ?? (game as any).players?.() ?? [];
    const nachClient = new Map<string, number>();
    for (const p of alle) {
      const c = typeof p.clientID === "function" ? p.clientID() : p.clientID;
      if (c) nachClient.set(String(c), p.smallID());
    }
    for (const m of meta) if (m.sid === undefined) m.sid = nachClient.get(String(m.clientID));
  }

  // ── Replay; an den Ticks der Metazeilen die Felder rechnen ──
  // Ohne Zeilenplan: jede Metazeile ist eine Sichtung (siehe ZusatzSpur).
  const spur = new ZusatzSpur(game, UnitType);
  const felderZiel = { opp: oppW, glob: globW, einh: einhW, angr: angrW };
  const letzter = meta.length ? meta[meta.length - 1].tick : -1;
  let berechnet = 0, ohneSpieler = 0, msFelder = 0;
  for (const turn of record.turns) {
    const T = turn.turnNumber;
    if (game.ticks() !== T) throw new Error(`ticks ${game.ticks()} != turn ${T}`);
    const zeilen = proTick.get(T);
    if (zeilen) {
      const tf = performance.now();
      const jeSpieler = new Map<number, number>();       // sid → erste Zeile (Werte teilen sich)
      for (const i of zeilen) {
        const m = meta[i];
        const erste = jeSpieler.get(m.sid);
        if (erste !== undefined) {
          oppW.copyWithin(i * MAX_OPP * F_OPP, erste * MAX_OPP * F_OPP, (erste + 1) * MAX_OPP * F_OPP);
          globW.copyWithin(i * F_GLOB, erste * F_GLOB, (erste + 1) * F_GLOB);
          einhW.copyWithin(i * MAX_EINH * F_EINH, erste * MAX_EINH * F_EINH, (erste + 1) * MAX_EINH * F_EINH);
          angrW.copyWithin(i * MAX_ANGR * F_ANGR, erste * MAX_ANGR * F_ANGR, (erste + 1) * MAX_ANGR * F_ANGR);
          berechnet++;
          continue;
        }
        const p = game.playerBySmallID(m.sid);
        if (!p?.isPlayer()) { ohneSpieler++; continue; }
        const ich = p as Player;
        // ZUSATZ_TEIL: alles | opp | global | keine — nur zum Eingrenzen von Fehlern
        const teil = process.env.ZUSATZ_TEIL ?? "alles";
        const oppSids: number[] = (m.opps ?? []).map((o: any) => o.id);
        spur.schreibe(ich, oppSids, m.ownUnitIds ?? [], m.ownAttackIds ?? [], felderZiel, i, teil);
        jeSpieler.set(m.sid, i);
        berechnet++;
      }
      msFelder += performance.now() - tf;
    }
    spur.vorTick(turn);
    runner.addTurn(turn);
    runner.executeNextTick();
    if (fatal) throw new Error(`tick-Fehler T=${T}: ${fatal}`);
    spur.nachTick(letzte?.updates?.[GameUpdateType.Unit]);
    if (T >= letzter && letzter >= 0) break;              // nach dem letzten Sample ist Schluss
  }

  // ── schreiben (atomar, eigener Ordner) ──
  const a = puffer(oppW), b = puffer(globW), c = puffer(einhW), d = puffer(angrW);
  const kopf = JSON.stringify({ format: FORMAT, gid, samples: meta.length, opp: MAX_OPP,
    einheit: MAX_EINH, angriff: MAX_ANGR, felder_opp: OPP_FELDER, felder_global: GLOBAL_FELDER,
    felder_einheit: EINH_FELDER, felder_angriff: ANGR_FELDER, dtype: a.dtype, commit, berechnet,
    ohne_spieler: ohneSpieler, ticks: game.ticks(), ms: Math.round(performance.now() - t0),
    ms_felder: Math.round(msFelder) });
  const roh = Buffer.concat([Buffer.from(kopf + "\n", "utf8"), a.b, b.b, c.b, d.b]);
  fs.mkdirSync(ausdir, { recursive: true });
  const ziel = path.join(ausdir, `${gid}.zusatz.zst`);
  const tmp = `${ziel}.tmp`;
  fs.writeFileSync(tmp, zstdCompressSync(roh));
  fs.renameSync(tmp, ziel);
  process.stdout.write(JSON.stringify({ gid, samples: meta.length, berechnet, ohne_spieler: ohneSpieler,
    bytes: fs.statSync(ziel).size, roh_bytes: roh.length, dtype: a.dtype,
    ms: Math.round(performance.now() - t0), ms_felder: Math.round(msFelder) }) + "\n");
}

main().catch((e) => { console.error(e?.stack ?? e); process.exit(1); });

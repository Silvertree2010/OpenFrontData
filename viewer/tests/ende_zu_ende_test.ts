/**
 * Ende-zu-Ende ohne Browser: Client-Code (aiAnfrage.ts, aiZiel.ts) gegen den D0-Server.
 *
 * Spielt eine echte Partie im gepatchten Client nach (ohne Modell-Intents einzuspielen, die
 * Partie bleibt also die aufgezeichnete) und prüft:
 *   1. An den Ticks räumlicher Samples: Anfrage für diesen Spieler bitgleich zu den
 *      Materialisierer-Blöcken (Karte, Zellfakten) und own/opps gleich der Metazeile.
 *   2. Alle --alle Ticks für jeden lebenden Menschen: Anfrage an den Server, Antwort mit
 *      intentAusAntwort gegen die Engine aufgelöst. Gezählt werden Handlungen, Nichtstun,
 *      räumliche Aktionen mit und ohne gültige Kachel, Fehler; dazu der Handlungsabstand.
 *
 *   cd <client mit Patch> && npx tsx aitest/ende_zu_ende_test.ts <record.json> <ordner> <gid> <server-url> [alle] [maxAnfragen]
 */
import fs from "fs";
import path from "path";
import { zstdDecompressSync } from "zlib";
import { AiAnfrage } from "../src/core/aiAnfrage";
import { intentAusAntwort, zelleVon } from "../src/core/aiZiel";
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
import { NodeGameMapLoader } from "../tests/perf/fullgame/NodeGameMapLoader";

const [file, outdir, gid, url, alleArg, maxArg] = process.argv.slice(2);
const ALLE = Number(alleArg ?? 8);
const MAX_ANFRAGEN = Number(maxArg ?? 400);

function bloecke(p: string): Buffer[] {
  const d = fs.readFileSync(p);
  const out: Buffer[] = [];
  for (let off = 0; off < d.length;) {
    const n = d.readUInt32LE(off);
    out.push(zstdDecompressSync(d.subarray(off + 4, off + 4 + n)));
    off += 4 + n;
  }
  return out;
}

const gleich = (a: Uint8Array, b: Uint8Array) => a.length === b.length && a.every((x, i) => x === b[i]);

const UNIT_GRUPPE: Record<string, string> = { City: "bau", "Defense Post": "bau", Factory: "bau",
  "SAM Launcher": "bau", "Missile Silo": "bau", Port: "hafen", Warship: "kriegsschiff",
  "Atom Bomb": "atom", "Hydrogen Bomb": "wasserstoff", MIRV: "mirv" };
/** Wie trainer/tabellen.gruppe. */
function gruppeVon(it: any): string | null {
  if (!it) return null;
  if (it.type === "build_unit") return UNIT_GRUPPE[it.unit] ?? null;
  return ({ boat: "boot", move_warship: "schiff_bewegen", spawn: "spawn" } as Record<string, string>)[it.type] ?? null;
}

async function main() {
  console.debug = () => {};
  console.log = (...x: any[]) => console.error(...x);
  const meta = zstdDecompressSync(fs.readFileSync(path.join(outdir, `${gid}.meta.zst`)))
    .toString("utf8").split("\n").filter((l) => l).map((l) => JSON.parse(l));
  const karten = bloecke(path.join(outdir, `${gid}.maps`));
  const zellen = bloecke(path.join(outdir, `${gid}.cells`));
  const proben = new Map<string, number>();       // "tick/sid" → Metazeile (räumlich, mit Zellblock)
  for (let i = 0; i < meta.length; i++) if (meta[i].cell >= 0) proben.set(`${meta[i].tick}/${meta[i].sid}`, i);

  const raw = JSON.parse(fs.readFileSync(file, "utf8"));
  const parsed = GameRecordSchema.safeParse(raw);
  const record: GameRecord = decompressGameRecord(parsed.success ? parsed.data : (raw as GameRecord));
  const info = record.info;
  const gameStart = toWireGameStartInfo({ gameID: info.gameID, lobbyCreatedAt: info.lobbyCreatedAt,
    config: info.config, players: info.players, tribes: info.tribes });
  const config = new Config(info.config, null, false);
  const terrain = await loadTerrainMap(info.config.gameMap, info.config.gameMapSize,
    new NodeGameMapLoader(path.resolve("resources/maps")), false);
  const random = new PseudoRandom(simpleHash(gameStart.gameID));
  const humans = gameStart.players.map((p) => new PlayerInfo(p.username, PlayerType.Human, p.clientID,
    random.nextID(), p.isLobbyCreator ?? false, p.clanTag, p.friends ?? [], p.teamIndex ?? null));
  const nations = createNationsForGame(gameStart, terrain.nations, terrain.additionalNations, humans.length, random);
  const game = createGame(humans, nations, terrain.gameMap, terrain.miniGameMap, config, terrain.teamGameSpawnAreas);
  let fatal: string | undefined;
  const runner = new GameRunner(game, new Executor(game, gameStart.gameID, undefined,
    gameStart.tribes?.map((t) => t.name)), (gu: any) => { if ("errMsg" in gu) fatal = gu.errMsg; });
  runner.init();
  const anfr = new AiAnfrage(info.config);        // Worker: gameStartInfo.config, dasselbe Objekt

  const z: Record<string, number> = {};
  const inc = (k: string) => { z[k] = (z[k] ?? 0) + 1; };
  const beispiele: any[] = [];
  const jeSpieler = new Map<number, { fragen: number; handeln: number }>();
  let anfragen = 0, msSumme = 0;
  for (const turn of record.turns) {
    const T = turn.turnNumber;
    // 1. Bitgleich an den Probe-Ticks
    for (const p of game.players()) {
      const i = proben.get(`${T}/${p.smallID()}`);
      if (i === undefined || p.type() !== PlayerType.Human) continue;
      const o = anfr.baue(game, p as Player, ALLE);
      const m = meta[i];
      inc(gleich(anfr.u8, karten[i]) ? "karte bitgleich" : "karte FALSCH");
      inc(anfr.zellen && gleich(anfr.zellen, zellen[m.cell]) ? "zellen bitgleich" : "zellen FALSCH");
      inc(JSON.stringify(o.own) === JSON.stringify(m.own) && JSON.stringify(o.opps) === JSON.stringify(m.opps)
        ? "vektor gleich" : "vektor FALSCH");
      // Auflösung Zelle → Kachel gegen die Engine: Antwort mit der Zelle der echten res_tile
      const g = gruppeVon(m.intent);
      if (g && m.res_kind === 0 && m.res_tile >= 0) {
        const zelle = zelleVon(game, m.res_tile);
        const vorlage = m.intent.type === "boat" ? { ...m.intent, dst: m.click } : { ...m.intent, tile: m.click };
        const it = intentAusAntwort(game, p as Player, { intent: vorlage, kandidaten: [zelle], gruppe: g,
          zielSid: m.dst_owner });
        const t = it ? (it.type === "boat" ? it.dst : it.tile) : -1;
        inc(it && zelleVon(game, t) === zelle ? `aufgelöst ${g}` : `NICHT aufgelöst ${g}`);
      }
    }
    // 2. Server fragen
    if (!game.inSpawnPhase() && T % ALLE === 0 && anfragen < MAX_ANFRAGEN) {
      for (const p of game.players()) {
        if (p.type() !== PlayerType.Human || !p.isAlive() || anfragen >= MAX_ANFRAGEN) continue;
        const body = anfr.baue(game, p as Player, ALLE);
        const t0 = performance.now();
        const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
        const j: any = await r.json();
        msSumme += performance.now() - t0;
        anfragen++;
        const s = jeSpieler.get(p.smallID()) ?? { fragen: 0, handeln: 0 };
        s.fragen++;
        jeSpieler.set(p.smallID(), s);
        if (j.error) { inc("serverfehler"); if (beispiele.length < 3) beispiele.push(j.error); continue; }
        if (j.intent?.type === "no_op") { inc(j.grund ? `nichtstun (${j.grund})` : "nichtstun"); continue; }
        s.handeln++;
        const it = intentAusAntwort(game, p as Player, j);
        const art = `${j.atype}${j.gruppe ? "/" + j.gruppe : ""}`;
        if (!j.kandidaten) inc(it ? `handeln ${art}` : `handeln ${art} unvollständig`);
        else if (!it) inc(`räumlich ${art}: keine gültige Kachel in Top 5`);
        else {
          const tile = it.type === "boat" ? it.dst : it.tile;
          const rang = (j.kandidaten as number[]).indexOf(zelleVon(game, tile));
          inc(`räumlich ${art}: Kachel in Kandidat ${rang + 1}`);
          if (beispiele.length < 6) beispiele.push({ T, sid: p.smallID(), intent: it, p: j.p_handeln });
        }
      }
    }
    runner.addTurn(turn);
    runner.executeNextTick();
    if (fatal) throw new Error(`tick-Fehler T=${T}: ${fatal}`);
    if (anfragen >= MAX_ANFRAGEN && T > Math.max(...[...proben.keys()].map((k) => Number(k.split("/")[0])))) break;
  }
  const abst = [...jeSpieler.values()].filter((s) => s.fragen >= 20)
    .map((s) => (s.handeln ? (s.fragen * ALLE) / s.handeln : Infinity)).sort((a, b) => a - b);
  const median = abst.length ? abst[Math.floor(abst.length / 2)] : NaN;
  const falsch = Object.keys(z).some((k) => k.includes("FALSCH")) || (z["serverfehler"] ?? 0) > 0;
  process.stdout.write(JSON.stringify({ gid, anfragen, ms_je_anfrage: Math.round(msSumme / Math.max(1, anfragen)),
    zaehler: z, handlungsabstand_median_ticks: median, spieler: abst.length, beispiele, ok: !falsch }) + "\n");
}

main().catch((e) => { console.error(e?.stack ?? e); process.exit(1); });

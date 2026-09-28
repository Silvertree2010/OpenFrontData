/**
 * Paritätstest der Zusatzfelder: Laufzeitweg gegen die Offline-Dateien <gid>.zusatz.zst.
 *
 * Läuft IM Client-Baum (Arena-Client 88cc95d8 oder Live-Client v0.33.14 = Kern von
 * 8b45be57), eine Ebene unter der Wurzel, neben zusatzFelder.ts (byte-gleiche Kopie von
 * zusatz/src/felder.ts) und — für --beobachter — beobachtung.ts der Erweiterung:
 *
 *   cd <client> && npx tsx d1probe/paritaet.ts --records ~/of-records/records \
 *       --pool ~/of-mat2-out --zusatz ~/zusatz/alle --gids s6/w3ax9Rmv,s4/duwV8vrh \
 *       [--stich 40] [--beobachter] [--aus nutzlast.jsonl]
 *
 * Verglichen wird auf den float16-Bits, die das Training liest. Drei Prüfungen je Partie:
 *   A lauf      ZusatzSpur ohne Zeilenplan, Listen aus der Metazeile — genau lauf.ts.
 *               Zeigt, dass das umgebaute Modul dieselben Dateien schreibt wie der Container.
 *   B laufzeit  ZusatzSpur mit Zeilenplan wie Arena und Erweiterung, Felder VOR vorTick
 *               gerechnet, alle Zeilen. Dazu die nachgebildeten Metazeilen gegen die echten.
 *   C anfrage   Stichprobe: ctx aus AiAnfrage.baue (bzw. Beobachter.rechne der Erweiterung)
 *               wie zur Laufzeit, zusatz_b64 aus zusatzAnfrage dekodiert. ctx-Listen gegen
 *               die Metazeile, Werte gegen die Datei. --aus schreibt diese Nutzlasten für den
 *               Python-Vergleich (trainer/tests/zusatz_paritaet.py).
 * stdout: je Partie eine JSON-Zeile, am Ende die Summe. Alles andere nach stderr.
 */
import fs from "fs";
import path from "path";
import { zstdDecompressSync } from "zlib";
import { AiAnfrage } from "../src/core/aiAnfrage";
import { Config } from "../src/core/configuration/Config";
import { Executor } from "../src/core/execution/ExecutionManager";
import { Game, Player, PlayerInfo, PlayerType, UnitType } from "../src/core/game/Game";
import { createGame } from "../src/core/game/GameImpl";
import { GameUpdateType } from "../src/core/game/GameUpdates";
import { createNationsForGame } from "../src/core/game/NationCreation";
import { loadTerrainMap } from "../src/core/game/TerrainMapLoader";
import { GameRunner } from "../src/core/GameRunner";
import { PseudoRandom } from "../src/core/PseudoRandom";
import { GameRecord, GameRecordSchema } from "../src/core/Schemas";
import { decompressGameRecord, simpleHash, toWireGameStartInfo } from "../src/core/Util";
import { NodeGameMapLoader } from "../tests/perf/fullgame/NodeGameMapLoader";
import {
  ANGR_FELDER, EINH_FELDER, F_ANGR, F_EINH, F_GLOB, F_OPP, GLOBAL_FELDER, MAX_ANGR, MAX_EINH, MAX_OPP,
  OPP_FELDER, POOL_PLAN, ZusatzSpur, ZusatzWerte, leereWerte, zusatzAnfrage, zusatzSig,
} from "./zusatzFelder";

const log = (...x: any[]) => process.stderr.write(x.map(String).join(" ") + "\n");
console.debug = () => {};
console.log = (...x: any[]) => log(...x);
console.warn = () => {};
console.info = () => {};

const BLOCK = [
  { name: "opp", je: MAX_OPP * F_OPP, platz: F_OPP, felder: OPP_FELDER as readonly string[] },
  { name: "global", je: F_GLOB, platz: F_GLOB, felder: GLOBAL_FELDER as readonly string[] },
  { name: "einheit", je: MAX_EINH * F_EINH, platz: F_EINH, felder: EINH_FELDER as readonly string[] },
  { name: "angriff", je: MAX_ANGR * F_ANGR, platz: F_ANGR, felder: ANGR_FELDER as readonly string[] },
];

const KARTEN = new Set<string>();

function arg(n: string, s?: string): string | undefined {
  const a = process.argv.slice(2);
  const i = a.indexOf("--" + n);
  return i >= 0 && i + 1 < a.length ? a[i + 1] : s;
}

function f16bits(a: Float32Array): Uint16Array {
  const F16A = (globalThis as any).Float16Array;
  if (!F16A) throw new Error("Float16Array fehlt in diesem Node — Vergleich auf float16 unmöglich");
  const h = new F16A(a.length);
  h.set(a);                                   // wie puffer() in lauf.ts
  return new Uint16Array(h.buffer);
}

/** Offline-Datei → vier Blöcke als float16-Bits (n Zeilen). */
function offline(pfad: string) {
  const roh = zstdDecompressSync(fs.readFileSync(pfad));
  const nl = roh.indexOf(10);
  const kopf = JSON.parse(roh.subarray(0, nl).toString("utf8"));
  if (kopf.dtype !== "float16") throw new Error(`dtype ${kopf.dtype}, erwartet float16`);
  for (const [k, v] of [["felder_opp", OPP_FELDER], ["felder_global", GLOBAL_FELDER],
                        ["felder_einheit", EINH_FELDER], ["felder_angriff", ANGR_FELDER]] as const) {
    if (JSON.stringify(kopf[k]) !== JSON.stringify(v)) throw new Error(`${k} weicht vom Modul ab`);
  }
  const n = kopf.samples as number;
  const rest = roh.subarray(nl + 1);
  const u16 = new Uint16Array(rest.length / 2);
  Buffer.from(u16.buffer).set(rest);
  const out: Uint16Array[] = [];
  let off = 0;
  for (const b of BLOCK) { out.push(u16.subarray(off, off + n * b.je)); off += n * b.je; }
  if (off !== u16.length) throw new Error(`Datei hat ${u16.length} Werte, erwartet ${off}`);
  return { kopf, n, bloecke: out };
}

interface Abw { abw: number; je_block: Record<string, number>; beispiele: any[]; nicht_null: number }
function neuAbw(): Abw { return { abw: 0, je_block: {}, beispiele: [], nicht_null: 0 }; }

/** Zeile i von w (float32) gegen Zeile j der Offline-Bits. */
function vergleiche(w: ZusatzWerte, i: number, off: Uint16Array[], j: number, a: Abw, meta: any) {
  const bl = [w.opp, w.glob, w.einh, w.angr];
  for (let b = 0; b < 4; b++) {
    const B = BLOCK[b];
    const ist = f16bits(bl[b].subarray(i * B.je, (i + 1) * B.je));
    const soll = off[b].subarray(j * B.je, (j + 1) * B.je);
    for (let k = 0; k < B.je; k++) {
      if (soll[k] !== 0) a.nicht_null++;            // zeigt, dass echte Werte verglichen wurden
      if (ist[k] === soll[k]) continue;
      a.abw++;
      a.je_block[B.name] = (a.je_block[B.name] ?? 0) + 1;
      if (a.beispiele.length < 8) {
        const F16A = (globalThis as any).Float16Array;
        a.beispiele.push({ zeile: j, tick: meta.tick, sid: meta.sid, block: B.name,
          platz: Math.floor(k / B.platz), feld: B.felder[k % B.platz],
          ist: new F16A(ist.buffer.slice(ist.byteOffset + 2 * k, ist.byteOffset + 2 * k + 2))[0],
          soll: new F16A(new Uint16Array([soll[k]]).buffer)[0] });
      }
    }
  }
}

function b64f32(s: string): Float32Array {
  const b = Buffer.from(s, "base64");
  const f = new Float32Array(b.length / 4);
  Buffer.from(f.buffer).set(b);
  return f;
}

async function partie(recDir: string, poolDir: string, zusDir: string, sg: string, stich: number,
                      beob: any, aus: number | null) {
  const t0 = performance.now();
  const [shard, gid] = sg.split("/");
  const pool = path.join(poolDir, shard);
  const meta = zstdDecompressSync(fs.readFileSync(path.join(pool, `${gid}.meta.zst`)))
    .toString("utf8").split("\n").filter((l) => l).map((l) => JSON.parse(l));
  const hdr = JSON.parse(fs.readFileSync(path.join(pool, `${gid}.hdr.json`), "utf8"));
  const off = offline(path.join(zusDir, `${gid}.zusatz.zst`));
  if (off.n !== meta.length) throw new Error(`${gid}: Datei ${off.n} Zeilen, Meta ${meta.length}`);
  const params = hdr.params ?? {};
  const noopEvery = params.NOOP_EVERY ?? POOL_PLAN.noopEvery, mergeTicks = params.MERGE_TICKS ?? POOL_PLAN.mergeTicks;

  const proTick = new Map<number, number[]>();
  const paare = new Set<string>();
  for (let i = 0; i < meta.length; i++) {
    const t = meta[i].tick;
    if (!proTick.has(t)) proTick.set(t, []);
    proTick.get(t)!.push(i);
    paare.add(`${t}:${meta[i].sid}`);
  }
  const schritt = Math.max(1, Math.floor(paare.size / Math.max(1, stich)));

  const raw = JSON.parse(fs.readFileSync(path.join(recDir, gid.slice(0, 2), `${gid}.json`), "utf8"));
  const parsed = GameRecordSchema.safeParse(raw);
  const record: GameRecord = decompressGameRecord(parsed.success ? parsed.data : (raw as GameRecord));
  const info = record.info;
  const commit = String((record as any).gitCommit ?? raw.gitCommit ?? "");
  const gameStart = toWireGameStartInfo({ gameID: info.gameID, lobbyCreatedAt: info.lobbyCreatedAt,
    config: info.config, players: info.players, tribes: info.tribes } as any);
  const config = new Config(info.config, null, false);
  // loadTerrainMap gibt je Prozess dieselbe GameMap zurück, und GameImpl schreibt hinein
  // (siehe arena.ts): eine zweite Partie auf derselben Karte startet auf dem Endbrett der
  // ersten und stirbt im Replay. Darum je Karte höchstens eine Partie pro Prozess.
  if (KARTEN.has(info.config.gameMap)) {
    throw new Error(`Karte ${info.config.gameMap} lief in diesem Prozess schon — eigene Prozesse je Partie`);
  }
  KARTEN.add(info.config.gameMap);
  const terrain = await loadTerrainMap(info.config.gameMap, info.config.gameMapSize,
    new NodeGameMapLoader(path.resolve("resources/maps")), false);
  const random = new PseudoRandom(simpleHash(gameStart.gameID));
  const humans = gameStart.players.map((p: any) => new PlayerInfo(p.username, PlayerType.Human, p.clientID,
    random.nextID(), p.isLobbyCreator ?? false, p.clanTag, p.friends ?? [], p.teamIndex ?? null));
  const nations = createNationsForGame(gameStart, terrain.nations, terrain.additionalNations, humans.length, random);
  const game: Game = createGame(humans, nations, terrain.gameMap, terrain.miniGameMap, config,
    terrain.teamGameSpawnAreas);
  let fatal: string | undefined;
  let letzte: any = null;
  const runner = new GameRunner(game, new Executor(game, gameStart.gameID, undefined,
    gameStart.tribes?.map((t: any) => t.name)), (gu: any) => {
      if ("errMsg" in gu) fatal = `${gu.errMsg}\n${String(gu.stack ?? "").split("\n").slice(0, 6).join("\n")}`;
      else letzte = gu;
    });
  runner.init();

  const L = new ZusatzSpur(game, UnitType);
  const R = new ZusatzSpur(game, UnitType,
    { noopEvery, mergeTicks, phase: (c: string) => Math.abs(simpleHash(c)) % noopEvery });
  // Negativkontrolle: ohne Zeilenplan, dafür sichtet jede Anfrage im Takt 32 (der naive
  // Laufzeitweg). Muss beim Angriffsalter abweichen, sonst prüft der Test nichts.
  const N = new ZusatzSpur(game, UnitType);
  const wN = leereWerte(meta.length);
  const echt = new Set<string>();
  const menschen = new Set<string>();
  for (const m of meta) {
    if (m.clientID) { R.verfolge(m.clientID); menschen.add(m.clientID); }
    if (m.kind !== "spawn") echt.add(`${m.clientID}@${m.tick}`);
  }
  const nachgebildet = new Set<string>();
  R.beiZeile = (cid, t) => nachgebildet.add(`${cid}@${t}`);
  const wL = leereWerte(meta.length), wR = leereWerte(meta.length);
  const kopiere = (w: ZusatzWerte, i: number, e: number) => {
    const bl = [w.opp, w.glob, w.einh, w.angr];
    for (let b = 0; b < 4; b++) bl[b].copyWithin(i * BLOCK[b].je, e * BLOCK[b].je, (e + 1) * BLOCK[b].je);
  };
  const anfr = new AiAnfrage(info.config);
  const beobachter = beob ? new beob.Beobachter(info.config) : null;
  const C = { n: 0, ctx_abw: 0, ohne_zusatz: 0, sig: zusatzSig(), ...neuAbw(), ctx_beispiele: [] as any[] };
  let paarNr = 0, ohne = 0;

  const letzter = meta.length ? meta[meta.length - 1].tick : -1;
  for (const turn of record.turns) {
    const T = turn.turnNumber;
    if (game.ticks() !== T) throw new Error(`ticks ${game.ticks()} != turn ${T}`);
    if (T % 32 === 0 && !game.inSpawnPhase()) {
      for (const c of menschen) {
        const p = game.playerByClientID(c);
        if (p && p.isAlive()) N.zus.sichte(p);
      }
    }
    const zeilen = proTick.get(T);
    if (zeilen) {
      const jeSid = new Map<number, number>();
      for (const i of zeilen) {
        const m = meta[i];
        const e = jeSid.get(m.sid);
        if (e !== undefined) { kopiere(wL, i, e); kopiere(wR, i, e); kopiere(wN, i, e); continue; }
        const p = game.playerBySmallID(m.sid);
        if (!p?.isPlayer()) { ohne++; continue; }
        const ich = p as Player;
        const oppSids: number[] = (m.opps ?? []).map((o: any) => o.id);
        L.schreibe(ich, oppSids, m.ownUnitIds ?? [], m.ownAttackIds ?? [], wL, i);
        R.schreibe(ich, oppSids, m.ownUnitIds ?? [], m.ownAttackIds ?? [], wR, i);
        N.schreibe(ich, oppSids, m.ownUnitIds ?? [], m.ownAttackIds ?? [], wN, i);
        jeSid.set(m.sid, i);
        if (paarNr++ % schritt !== 0) continue;
        // ── C: wie zur Laufzeit ──
        let roh: any;
        if (beobachter) roh = beobachter.rechne(game, ich, m.clientID, 32, R)._anfrage;
        else { roh = anfr.baue(game, ich, 32); Object.assign(roh, zusatzAnfrage(R, ich, roh.ctx)); }
        C.n++;
        const lst = (x: any) => JSON.stringify(x ?? []);
        const ctxGleich = lst(roh.ctx.oppSids) === lst(oppSids) && lst(roh.ctx.ownUnitIds) === lst(m.ownUnitIds)
          && lst(roh.ctx.ownAttackIds) === lst(m.ownAttackIds);
        if (!ctxGleich) {
          C.ctx_abw++;
          if (C.ctx_beispiele.length < 4) C.ctx_beispiele.push({ zeile: i, tick: T, sid: m.sid,
            opp: [roh.ctx.oppSids, oppSids], einh: [roh.ctx.ownUnitIds?.length, m.ownUnitIds?.length],
            angr: [roh.ctx.ownAttackIds, m.ownAttackIds] });
        }
        if (!roh.zusatz_b64) { C.ohne_zusatz++; continue; }
        if (roh.zusatz_sig !== C.sig) throw new Error(`zusatz_sig ${roh.zusatz_sig} != ${C.sig}`);
        const f = b64f32(roh.zusatz_b64);
        const w: ZusatzWerte = { opp: f.subarray(0, BLOCK[0].je), glob: f.subarray(BLOCK[0].je, BLOCK[0].je + BLOCK[1].je),
          einh: f.subarray(BLOCK[0].je + BLOCK[1].je, BLOCK[0].je + BLOCK[1].je + BLOCK[2].je),
          angr: f.subarray(BLOCK[0].je + BLOCK[1].je + BLOCK[2].je) };
        vergleiche(w, 0, off.bloecke, i, C, m);
        if (aus !== null) {
          fs.writeSync(aus, JSON.stringify({ gid, pool, zeile: i, tick: T, sid: m.sid,
            zusatz_b64: roh.zusatz_b64, zusatz_sig: roh.zusatz_sig, ctx: roh.ctx }) + "\n");
        }
      }
    }
    L.vorTick(turn);
    R.vorTick(turn);
    N.vorTick(turn);
    runner.addTurn(turn);
    runner.executeNextTick();
    if (fatal) throw new Error(`tick-Fehler T=${T}: ${fatal}`);
    const uu = letzte?.updates?.[GameUpdateType.Unit];
    L.nachTick(uu);
    R.nachTick(uu);
    N.nachTick(uu);
    if (T >= letzter && letzter >= 0) break;
  }

  const A = neuAbw(), B = neuAbw(), K = neuAbw();
  let angriffsZeilen = 0;
  for (let i = 0; i < meta.length; i++) {
    vergleiche(wL, i, off.bloecke, i, A, meta[i]);
    vergleiche(wR, i, off.bloecke, i, B, meta[i]);
    vergleiche(wN, i, off.bloecke, i, K, meta[i]);
    if ((meta[i].ownAttackIds ?? []).length) angriffsZeilen++;
  }
  K.beispiele = K.beispiele.slice(0, 2);
  const fehlt = [...echt].filter((x) => !nachgebildet.has(x));
  const extra = [...nachgebildet].filter((x) => !echt.has(x));
  return { gid, commit: commit.slice(0, 8), zeilen: meta.length, ohne_spieler: ohne, ticks: game.ticks(),
    ms: Math.round(performance.now() - t0),
    A_lauf: A, B_laufzeit: B, kontrolle_naiv: { abw: K.abw, je_block: K.je_block, beispiele: K.beispiele },
    zeilen_mit_angriffen: angriffsZeilen,
    plan: { echt: echt.size, nachgebildet: nachgebildet.size, fehlt: fehlt.length, extra: extra.length,
            beispiele: { fehlt: fehlt.slice(0, 5), extra: extra.slice(0, 5) } },
    C_anfrage: C };
}

async function main() {
  const recDir = arg("records")!, poolDir = arg("pool")!, zusDir = arg("zusatz")!;
  const gids = (arg("gids") ?? "").split(",").filter(Boolean);
  const stich = Number(arg("stich", "40"));
  const beob = process.argv.includes("--beobachter") ? await import("./beobachtung") : null;
  const ausPfad = arg("aus");
  const aus = ausPfad ? fs.openSync(ausPfad, "a") : null;
  const summe = { partien: 0, zeilen: 0, werte: 0, nicht_null: 0, A_abw: 0, B_abw: 0, plan_echt: 0,
                  plan_fehlt: 0, plan_extra: 0, C_n: 0, C_ctx_abw: 0, C_abw: 0, C_ohne_zusatz: 0,
                  kontrolle_naiv_abw: 0, zeilen_mit_angriffen: 0, fehler: [] as string[] };
  for (const sg of gids) {
    try {
      const r = await partie(recDir, poolDir, zusDir, sg, stich, beob, aus);
      process.stdout.write(JSON.stringify(r) + "\n");
      summe.partien++; summe.zeilen += r.zeilen; summe.A_abw += r.A_lauf.abw; summe.B_abw += r.B_laufzeit.abw;
      summe.werte += r.zeilen * BLOCK.reduce((s, b) => s + b.je, 0); summe.nicht_null += r.B_laufzeit.nicht_null;
      summe.plan_echt += r.plan.echt; summe.kontrolle_naiv_abw += r.kontrolle_naiv.abw;
      summe.zeilen_mit_angriffen += r.zeilen_mit_angriffen;
      summe.plan_fehlt += r.plan.fehlt; summe.plan_extra += r.plan.extra; summe.C_n += r.C_anfrage.n;
      summe.C_ctx_abw += r.C_anfrage.ctx_abw; summe.C_abw += r.C_anfrage.abw;
      summe.C_ohne_zusatz += r.C_anfrage.ohne_zusatz;
      log(`[paritaet] ${sg}: ${r.zeilen} Zeilen, A ${r.A_lauf.abw}, B ${r.B_laufzeit.abw}, `
        + `Plan fehlt ${r.plan.fehlt} extra ${r.plan.extra}, C ${r.C_anfrage.n} (ctx ${r.C_anfrage.ctx_abw}, `
        + `Werte ${r.C_anfrage.abw}), ${(r.ms / 1000).toFixed(0)} s`);
    } catch (e: any) {
      summe.fehler.push(`${sg}: ${String(e?.stack ?? e).slice(0, 400)}`);
      log(`[paritaet] ${sg} FEHLER ${e?.stack ?? e}`);
    }
  }
  if (aus !== null) fs.closeSync(aus);
  process.stdout.write(JSON.stringify({ summe }) + "\n");
}

main().catch((e) => { log(e?.stack ?? e); process.exit(1); });

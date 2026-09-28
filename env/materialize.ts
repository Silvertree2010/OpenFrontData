/**
 * Materializer — Record → Trainings-Shards auf Platte.
 *
 * Replayt ein Spiel und schreibt pro Entscheidungs-Sample (handelnder Mensch bei
 * seinem Zug):
 *   <id>.meta.zst  zstd(JSONL): je Zeile { ctx (fuer Label-encode), own, opps, intent, w }
 *   <id>.maps      Folge von [uint32 LE Laenge][zstd(Karten-uint8-Block)], 1:1 zu meta
 *
 * Die Karte (18×90×180) wird float→uint8 quantisiert und je Block einzeln zstd-t
 * (~11 KB/Sample, gemessen). Kein 25-TB-Problem: der ganze Datensatz ~205 GB.
 * Featurisieren + Label-encode passiert beim Training in Python (single source of
 * truth: featurize.py + actions.encode), damit man ohne Neu-Extraktion nachjustieren
 * kann. Die Beobachtung ist der Zustand VOR dem Zug (was der Spieler sah).
 *
 * Mit NOOP_EVERY=<K> kommen Nichtstun-Samples dazu: je K-tem intentlosen Tick und
 * Spieler ein Sample mit intent {"type":"no_op"} und w=K (Kehrwert der Abtast-
 * wahrscheinlichkeit). NOOP_ONLY=1 schreibt NUR diese (Zusatzlauf zu einem
 * bestehenden Pool), NOOP_ALIGN=1 tastet alle Spieler im selben Tick ab.
 *
 *   npx tsx env/materialize.ts <record.json> <outdir>
 *   NOOP_EVERY=200 npx tsx env/materialize.ts --list liste.txt <outdir>
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

// Dateiname-Zusatz: ein Nur-Nichtstun-Lauf schreibt <gid>.noop.* und kann so
// NEBEN einem bestehenden Pool im selben Ordner liegen, ohne ihn zu ueberschreiben
// (dataset.py/list_games behandeln "<gid>.noop" wie ein eigenes Spiel).
const SUFFIX = process.env.SUFFIX ?? (process.env.NOOP_ONLY === "1" ? ".noop" : "");

// Wiederaufnahme: eine winzige .meta.zst ist KEIN fertiges Spiel. Genau daran
// hat der Pool 7827 leere Shards behalten — sie existierten, also wurden sie bei
// jedem Neustart uebersprungen. Ein leeres zstd-Frame ist ~13 Byte, ein Shard mit
// auch nur einem Sample mehrere hundert. Schwelle konfigurierbar, damit der Test
// das alte Verhalten (MIN_META=0) nachstellen kann.
// Ein Spiel, das ehrlich 0 Samples ergibt, bekommt stattdessen eine <gid>.none-
// Marke: fertig, aber leer — so wird es nicht bei jedem Resume neu gerechnet.
const MIN_META = Number(process.env.MIN_META ?? 64);

function istFertig(outdir: string, gid: string): boolean {
  if (fs.existsSync(path.join(outdir, `${gid}${SUFFIX}.none`))) return true;
  const m = path.join(outdir, `${gid}${SUFFIX}.meta.zst`);
  return fs.existsSync(m) && fs.statSync(m).size >= MIN_META;
}

async function run(file: string, outdir: string) {
  console.debug = () => {};

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

  const mapsPath = path.join(outdir, `${info.gameID}${SUFFIX}.maps`);
  const mapsFd = fs.openSync(mapsPath, "w");
  const metaLines: string[] = [];
  const obsBuf = new Float32Array(MAPLEN);
  const u8 = new Uint8Array(MAPLEN);
  const lenBuf = Buffer.allocUnsafe(4);

  let samples = 0, scannedTick = -1, thinned = 0, noops = 0, noopChances = 0, noopFehler = 0;
  const t0 = performance.now();
  // Ausduennen: pro Spieler max 1 Angriff je THIN Ticks (Rest immer behalten).
  const THIN = Number(process.env.THIN ?? 30);
  const lastAttack = new Map<string, number>();

  // ── Nichtstun (A.NO_OP) ────────────────────────────────────────────────────
  // Bisher entstand ein Sample NUR, wenn ein Mensch tatsaechlich klickte. Das
  // Netz wird aber jeden Tick gefragt und hat kein einziges Beispiel fuer
  // "jetzt nichts tun" gesehen (gemessen: 7 von 72320 Labels). Deshalb hier
  // zusaetzlich Samples aus intentlosen Ticks — aber nicht aus JEDEM: Menschen
  // handeln im Median alle 73 Ticks, ein Vollabtasten wuerde alles andere im
  // Verhaeltnis ~1:70 ertraenken. Wir nehmen jeden NOOP_EVERY-ten intentlosen
  // Tick je Spieler und schreiben die Kehrwahrscheinlichkeit als Gewicht `w`
  // in die meta-Zeile; damit kann das Training den echten Prior herstellen,
  // ohne dass wir nochmal materialisieren muessen.
  const NOOP_EVERY = Number(process.env.NOOP_EVERY ?? 0);   // 0 = aus (Verhalten wie bisher)
  const NOOP_ONLY = process.env.NOOP_ONLY === "1";          // nur Nichtstun (Zusatzlauf zu altem Pool)
  const NOOP_ALIGN = process.env.NOOP_ALIGN === "1";        // alle Spieler im selben Tick abtasten
  const NOOP_INTENT = { type: "no_op" };   // actions.encode: unbekannter Typ → A.NO_OP, decode gibt ihn zurueck
  // Phase je Spieler, damit nicht alle im selben Tick abgetastet werden.
  const phases = new Map<string, number>();
  const phaseOf = (cid: string) => {
    let ph = phases.get(cid);
    if (ph === undefined) { ph = NOOP_ALIGN ? 0 : Math.abs(simpleHash(cid)) % NOOP_EVERY; phases.set(cid, ph); }
    return ph;
  };
  // Weggeklinkte Spieler produzieren sonst endlos "Nichtstun", das keine
  // Entscheidung ist. isDisconnected=false holt sie zurueck.
  const disconnected = new Set<string>();

  const ctxCache = new Map<string, any>();     // je Tick geleert
  const mapCache = new Map<string, Buffer>();

  // Ein Sample schreiben. HIER haengt jeder weitere Intent-Typ ein: mit dem
  // rohen Intent aufrufen, alles andere (Kontext, Karte, meta-Zeile) ist geteilt.
  const emit = (player: any, cid: string, turnNumber: number, intent: any, w: number) => {
    let ctx = ctxCache.get(cid);
    let zblock = mapCache.get(cid);
    if (!ctx) {
      if (scannedTick !== turnNumber) { enc.scanTick(game); scannedTick = turnNumber; }
      const v = enc.encodeVec(game, player, 24);
      const allies = new Set(player.allies().map((a: any) => a.smallID()));
      enc.encodeMap(player, allies, obsBuf);
      for (let k = 0; k < MAPLEN; k++) {
        let x = obsBuf[k]; if (x < -1) x = -1; else if (x > 1) x = 1;
        u8[k] = Math.round((x + 1) * 127.5);
      }
      zblock = zstdCompressSync(Buffer.from(u8.buffer, 0, MAPLEN));
      // Gegner-Reihenfolge → PlayerIDs (Label-Ziele sind PlayerIDs); plus user/clan fuer Reputation
      const oppIds = v.opponents.map((o) => {
        const pl = game.playerBySmallID(o.id);
        return pl?.isPlayer() ? pl.id() : null;
      });
      const opps = v.opponents.map((o) => {
        const pl = game.playerBySmallID(o.id);
        return { ...o, user: pl?.isPlayer() ? pl.name() : null, clan: pl?.isPlayer() ? pl.clanTag() : null };
      });
      ctx = {
        mapW: W, mapH: H, troops: player.troops(), gold: Number(player.gold()),
        oppIds, ownUnitIds: player.units().map((u: any) => u.id()),
        ownAttackIds: player.outgoingAttacks().map((a: any) => a.id()),
        own: v.own, opps,
      };
      ctxCache.set(cid, ctx); mapCache.set(cid, zblock);
    }
    // meta-Zeile: alles fuer Label-encode (Python) + obs-Features.
    // Erst bauen, dann Karte schreiben, dann anhaengen: wirft irgendetwas dazwischen
    // (Nichtstun-Samples fangen wir ab), bleiben meta und maps im Gleichschritt —
    // sonst zeigt jeder Laengen-Praefix danach auf den falschen Block.
    const line = JSON.stringify({ turn: turnNumber, clientID: cid,
      mapW: ctx.mapW, mapH: ctx.mapH, troops: ctx.troops, gold: ctx.gold,
      oppIds: ctx.oppIds, ownUnitIds: ctx.ownUnitIds, ownAttackIds: ctx.ownAttackIds,
      own: ctx.own, opps: ctx.opps, intent, w, win: winners.has(cid) ? 1 : 0 });
    lenBuf.writeUInt32LE(zblock!.length, 0);
    fs.writeSync(mapsFd, lenBuf); fs.writeSync(mapsFd, zblock!);
    metaLines.push(line);
    samples++;
  };

  for (const turn of record.turns) {
    const intents = turn.intents ?? [];
    for (const i of intents as any[]) {
      if (i.type === "mark_disconnected" && i.clientID) {
        if (i.isDisconnected === false) disconnected.delete(i.clientID);
        else disconnected.add(i.clientID);
      }
    }
    const acting = intents.filter((i: any) => i.clientID && humanClient.has(i.clientID) && i.type !== "mark_disconnected");
    if ((acting.length > 0 || NOOP_EVERY > 0) && !game.inSpawnPhase()) {
      // Kontext + Karte je Spieler EINMAL pro Tick (mehrere Intents teilen sie)
      ctxCache.clear(); mapCache.clear();
      // Wer in diesem Tick geklickt hat — auch wenn wir den Klick ausduennen:
      // das ist KEIN Nichtstun-Moment und darf nicht als solcher gelabelt werden.
      const acted = new Set<string>((acting as any[]).map((i) => i.clientID));
      if (!NOOP_ONLY) for (const intent of acting) {
        const cid = (intent as any).clientID;
        const player = game.players().find((p) => p.clientID() === cid);
        if (!player || !player.isAlive()) continue;

        // Angriffs-Ausduennung: schnelle Re-Klicks desselben Spielers weglassen
        if (THIN > 0 && (intent as any).type === "attack") {
          const la = lastAttack.get(cid);
          if (la !== undefined && turn.turnNumber - la < THIN) { thinned++; continue; }
          lastAttack.set(cid, turn.turnNumber);
        }
        emit(player, cid, turn.turnNumber, intent, 1);
      }
      if (NOOP_EVERY > 0) for (const player of game.players()) {
        if (!player.isAlive()) continue;
        const cid = player.clientID();
        if (!cid || !humanClient.has(cid) || acted.has(cid) || disconnected.has(cid)) continue;
        noopChances++;
        if ((turn.turnNumber + phaseOf(cid)) % NOOP_EVERY !== 0) continue;
        // Nichtstun-Ticks fassen Zustaende an, die der alte Pfad nie beruehrt hat
        // (Spieler ohne Grenze, halb abgeraeumte Gegner). Gemessen: ohne diesen
        // Fang verlor die Abtastung 14 von 17 Partien KOMPLETT, die vorher liefen.
        // Ein einzelnes Nichtstun-Sample ist es nicht wert, eine Partie zu kosten.
        try {
          emit(player, cid, turn.turnNumber, NOOP_INTENT, NOOP_EVERY);
          noops++;
        } catch (e: any) {
          noopFehler++;
          if (noopFehler === 1) console.error(`  no-op uebersprungen (${info.gameID} t${turn.turnNumber}): ${e?.message ?? e}`);
          ctxCache.delete(cid); mapCache.delete(cid);
        }
      }
    }
    runner.addTurn(turn);
    runner.executeNextTick();
    if (fatal) {
      // EIN kaputtes Spiel darf NICHT den ganzen Chunk killen: werfen statt exit,
      // partielle .maps aufraeumen; main() faengt es und macht mit dem naechsten weiter.
      fs.closeSync(mapsFd);
      try { fs.unlinkSync(mapsPath); } catch {}
      throw new Error(`tick-Fehler ${info.gameID}: ${fatal}`);
    }
  }
  fs.closeSync(mapsFd);
  if (samples === 0) {
    // Nichts gefunden: keine Pseudo-Shard-Dateien hinterlassen, sondern eine
    // ehrliche Marke. Sonst sieht der naechste Lauf "erledigt" und fragt nie nach.
    try { fs.unlinkSync(mapsPath); } catch {}
    fs.writeFileSync(path.join(outdir, `${info.gameID}${SUFFIX}.none`), "");
    console.log(`${info.gameID} ${info.config.gameMap}: 0 Samples — als .none markiert ` +
      `(${((performance.now() - t0) / 1000).toFixed(0)}s)`);
    return;
  }
  fs.writeFileSync(path.join(outdir, `${info.gameID}${SUFFIX}.meta.zst`), zstdCompressSync(Buffer.from(metaLines.join("\n"))));

  const mb = fs.statSync(mapsPath).size / 1e6;
  console.log(`${info.gameID} ${info.config.gameMap}: ${samples} Samples (${noops} no-op von ${noopChances} ` +
    `Gelegenheiten, ${noopFehler} uebersprungen), ` +
    `maps ${mb.toFixed(1)} MB (${(mb * 1000 / Math.max(samples, 1)).toFixed(1)} KB/Sample, ${thinned} ausgeduennt), ` +
    `${((performance.now() - t0) / 1000).toFixed(0)}s`);
}

async function main() {
  const args = process.argv.slice(2);
  let files: string[], outdir: string;
  if (args[0] === "--list") {
    files = fs.readFileSync(args[1], "utf8").split("\n").map((s) => s.trim()).filter(Boolean);
    outdir = args[2];
  } else {
    files = [args[0]]; outdir = args[1];
  }
  if (!files.length || !outdir) { console.error("Aufruf: materialize.ts <record.json | --list liste.txt> <outdir>"); process.exit(2); }
  fs.mkdirSync(outdir, { recursive: true });

  let done = 0, skipped = 0, failed = 0;
  for (const f of files) {
    const gid = path.basename(f).replace(/\.json$/, "");
    if (istFertig(outdir, gid)) { skipped++; continue; }   // resume-fest (leere Shards zaehlen NICHT als fertig)
    try { await run(f, outdir); done++; }
    catch (e: any) { console.error(`FEHLER ${gid}: ${process.env.STACK === "1" ? (e?.stack ?? e) : (e?.message ?? e)}`); failed++; }
  }
  if (files.length > 1) console.log(`[fertig] ${done} materialisiert, ${skipped} übersprungen, ${failed} Fehler`);
}
main();

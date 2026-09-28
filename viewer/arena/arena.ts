/**
 * Arena — Prüfstand für die Spielstärke, ohne Browser und ohne Zuschauer.
 *
 * Warum hier und nicht in `env/`: gemessen werden soll genau der Code, der im Viewer
 * spielt. Das sind `src/core/aiAnfrage.ts` (Beobachtung + Zellfakten) und
 * `src/core/aiZiel.ts` (Zelle → Kachel, Prüfung mit der Engine) aus dem gepatchten
 * Client. `env/` zeigt dagegen auf `../vendor/openfront` (gibt es nicht mehr) und auf
 * das alte `env/obs.ts`. Ein Prüfstand, der eine andere Beobachtung baut als der
 * Viewer, misst nicht die KI, sondern sich selbst. Darum liegt die Arena im Client,
 * wie `aitest/` auch.
 *
 *   cd <client mit Patch> && npx tsx arena/arena.ts --aus /tmp/lauf.jsonl \
 *       --partien 4 --bots 30 --ticks 3000 --saat lauf1 --seiten netz,nichtstun \
 *       --inf http://127.0.0.1:8650/act
 *
 * Je Partie und Seite eine JSONL-Zeile in --aus (mit --ki N: je KI-Spieler eine). Auf
 * stdout kommt genau eine Zeile: die Zusammenfassung als JSON (für den Trainer). Alles
 * andere geht nach stderr.
 *
 * Seiten:
 *   netz       fragt den Inferenz-Server (muss laufen, --inf; mehrere durch Komma)
 *   nichtstun  handelt nie — die Messlatte. Wächst nur durch die Engine-Eroberung,
 *              die auch ohne Befehle weiterläuft. Wer schlechter ist als das, schadet sich.
 *
 * Gepaart: beide Seiten spielen dieselbe Spiel-ID, also dieselbe Karte, dieselben
 * Nationen, dieselben Bots und dieselbe Startkachel. Der ganze Zufall der Arena kommt
 * aus `PseudoRandom(simpleHash(spielId + ":spawn"))`; `Math.random` wird nicht benutzt.
 *
 * Netz D1: meldet der Server `zusatz: true` (GET /), führt die Partie ab Tick 0 eine
 * ZusatzSpur mit (`zusatzFelder.ts` = byte-gleiche Kopie von zusatz/src/felder.ts aus dem
 * Trainer-Repo; trainer/arena.py legt sie hierher) und hängt jeder Anfrage die Zusatzfelder
 * an. Zwei Netze gegeneinander: zweimal mit derselben --saat, dann
 *   python3 arena/auswertung.py bc3.jsonl bc2.jsonl --a-seite netz --b-seite netz
 *
 * Mehrere KI-Spieler (--ki N, Standard 1): N Menschen-Plätze in derselben Partie, alle vom
 * selben Server gesteuert (eine Anfrage je Spieler; die fälligen Anfragen eines Ticks gehen
 * gleichzeitig raus, inf_d0 rechnet sie nacheinander). Die ZusatzSpur verfolgt alle N.
 * Alle N setzen ihre Startkachel im selben Tick: im Einzelspiel beendet der erste
 * Menschen-Spawn die Startphase (SpawnExecution), wer später kommt, kommt nie aufs Brett.
 * Die Kacheln eines Ticks halten --start-abstand auch untereinander ein. Mit N > 1 endet die
 * Partie zusätzlich, wenn alle N tot sind oder die Engine einen Sieger meldet (+20 Ticks).
 * N = 1 ohne --aufnahme spielt genau wie vorher (gleiche clientID, gleiche Kachelfolge).
 *
 * Aufnahme (--aufnahme ORDNER): schreibt je Partie ORDNER/<spielId>.json als GameRecord
 * (GameRecordSchema, gitCommit "DEV"), den der Client als Replay abspielt (Dev-Build holt
 * ihn über getApiBase() = http://localhost:8787/game/<id>, siehe aufnahme_server.mjs). Dafür
 * braucht es IDs, die das Schema annimmt: Spiel-ID genau 8 Zeichen A-Za-z0-9 (sonst aus der
 * Saat abgeleitet, steht als `aufnahme_id` in der Zeile), clientIDs `ki000001`…, Namen
 * `KI 01`…; die Konfiguration läuft vorher durch GameConfigSchema, jeder Intent durch
 * StampedIntentSchema — die Engine spielt also genau das, was der Client nachspielt. Alle
 * 100 Ticks steht der Zustands-Hash im Record, der Client prüft ihn beim Abspielen.
 *
 * Trajektorien (--spur ORDNER, nur Seite netz): je Partie die Entscheidungen aller N KIs im
 * Format des Materialisierers v2 (spur.ts), mit Beobachtung, gewählten Köpfen (lab), log μ und
 * log π je Kopf, P(handeln), Wertkopf, Tick, Zustand für die Belohnung und dem Ergebnis im Kopf.
 * Lesbar für den Trainer erst nach `trainer/trajektorie.py abschliessen ORDNER` (schreibt .ok).
 * --spur-kennung K hängt "_K" an die gid (zwei Läufe derselben Saat im selben Datenbestand).
 */
import fs from "fs";
import path from "path";
import { AiAnfrage, Geteilt } from "../src/core/aiAnfrage";
import { intentAusAntwort, zelleVon } from "../src/core/aiZiel";
import { Config } from "../src/core/configuration/Config";
import { Executor } from "../src/core/execution/ExecutionManager";
import { Game, Player, PlayerInfo, PlayerType, UnitType } from "../src/core/game/Game";
import { createGame } from "../src/core/game/GameImpl";
import { GameUpdateType } from "../src/core/game/GameUpdates";
import { createNationsForGame } from "../src/core/game/NationCreation";
import { GameMapType, maps } from "../src/core/game/Maps.gen";
import { loadTerrainMap } from "../src/core/game/TerrainMapLoader";
import { GameRunner } from "../src/core/GameRunner";
import { PseudoRandom } from "../src/core/PseudoRandom";
import { GameConfigSchema, GameRecordSchema, GameStartInfo, StampedIntentSchema } from "../src/core/Schemas";
import { createPartialGameRecord, simpleHash, toWireGameStartInfo } from "../src/core/Util";
import { NodeGameMapLoader } from "../tests/perf/fullgame/NodeGameMapLoader";
import { ObsEncoder } from "../src/core/obsModel";
import { Spielweise } from "./spielweise";
import { SpurSchreiber } from "./spur";
import { POOL_PLAN, ZusatzSpur, zusatzAnfrage } from "./zusatzFelder";

// stdout bleibt der Zusammenfassung vorbehalten; die Engine redet viel.
const log = (...x: any[]) => process.stderr.write(x.join(" ") + "\n");
console.debug = () => {};
console.log = (...x: any[]) => log(...x.map(String));
console.warn = () => {};
console.info = () => {};

const KI_CID = "arenaki00001";
// 500 ist dazugekommen, weil die Karte dort schon voll ist (gemessen auf Bosphorus
// Straits: 98,8 % belegt bei Tick 500). Wer erst ab 1000 hinschaut, hat die Phase
// verpasst, in der das Spiel entschieden wird.
const MESSPUNKTE = [500, 1000, 2000, 3000];
// GAME_ID_REGEX aus src/core/Schemas.ts: Spiel-IDs und clientIDs im Record.
const GUELTIGE_ID = /^[A-Za-z0-9]{8}$/;

type Seite = "netz" | "nichtstun";

interface Optionen {
  seiten: Seite[];
  inf: string;
  partien: number;
  saat: string;
  karte: string;
  groesse: string;
  bots: number;
  ticks: number;
  takt: number;
  verlaufAlle: number;
  aus: string;
  jobs: number;
  startTick: number;
  startAbstand: number;
  ki: number;                 // KI-Spieler je Partie (bei kiAuto: nur Platzhalter, gilt je Partie lobby().ki)
  kiAuto: boolean;            // --ki auto: Lobbygrösse wie im öffentlichen Spiel (MapPlaylist.lobbyMaxPlayers)
  botsAuto: boolean;          // --bots auto: 100 auf kompakten Karten, sonst 400 (MapPlaylist)
  aufnahme: string | null;    // Ordner für GameRecords, null = keine Aufnahme
  spur: string | null;        // Ordner für Trajektorien (spur.ts), null = keine
  spurNurSrv: number;         // nur KIs an den ersten N Servern kommen in die Spur (0 = alle)
  spurKennung: string | null;
  einSpiel: string | null; // Kindmodus: genau diese Spiel-ID spielen
  seite: Seite | null;
}

function argumente(): Optionen {
  const a = process.argv.slice(2);
  const w = (n: string, s?: string) => {
    const i = a.indexOf("--" + n);
    return i >= 0 && i + 1 < a.length ? a[i + 1] : s;
  };
  const o: Optionen = {
    seiten: (w("seiten", "netz,nichtstun") as string).split(",").filter(Boolean) as Seite[],
    inf: w("inf", "http://127.0.0.1:8650/act")!,
    partien: Number(w("partien", "4")),
    saat: w("saat", "arena")!,
    karte: w("karte", "Bosphorus Straits")!,
    groesse: w("groesse", "Compact")!,
    bots: w("bots", "30") === "auto" ? 0 : Number(w("bots", "30")),
    ticks: Number(w("ticks", "4000")),
    takt: Number(w("takt", "32")),
    verlaufAlle: Number(w("verlauf-alle", "250")),
    aus: w("aus", "arena.jsonl")!,
    jobs: Number(w("jobs", "1")),
    startTick: Number(w("start-tick", "100")),
    startAbstand: Number(w("start-abstand", "30")),
    ki: w("ki", "1") === "auto" ? 0 : Number(w("ki", "1")),
    kiAuto: w("ki", "1") === "auto",
    botsAuto: w("bots", "30") === "auto",
    aufnahme: w("aufnahme") ?? null,
    spur: w("spur") ?? null,
    spurNurSrv: Number(w("spur-nur-srv", "0")),
    spurKennung: w("spur-kennung") ?? null,
    einSpiel: w("ein-spiel") ?? null,
    seite: (w("seite") as Seite | undefined) ?? null,
  };
  if (o.seiten.some((s) => s !== "netz" && s !== "nichtstun")) {
    throw new Error(`--seiten kennt nur netz und nichtstun, nicht ${o.seiten.join(",")}`);
  }
  if (!o.kiAuto && (!Number.isInteger(o.ki) || o.ki < 1 || o.ki > 200)) {
    throw new Error(`--ki muss eine ganze Zahl von 1 bis 200 oder auto sein, nicht ${o.ki}`);
  }
  return o;
}

/** Spiel-ID, die der Client als Aufnahme annimmt; sonst deterministisch aus der Saat. */
function aufnahmeId(spielId: string): string {
  if (GUELTIGE_ID.test(spielId)) return spielId;
  return "A" + (simpleHash(spielId) >>> 0).toString(36).padStart(7, "0").slice(-7);
}

/**
 * Landkachel für den Start, allein aus der Spiel-ID. Kein Math.random.
 *
 * Rein gleichverteilt zu ziehen wäre unfair gegen die eigene Messung: die Hälfte der
 * Züge landet mitten im Gebiet einer Nation, und dann misst die Arena nur, wie schnell
 * ein eingekesselter Start stirbt. Darum wird eine Kachel bevorzugt, in deren Umkreis
 * `abstand` niemand sitzt — so, wie ein Mensch eine freie Ecke sucht. Findet sich keine,
 * gilt die erste freie Landkachel. Beide Seiten bekommen dieselbe Kachel, weil derselbe
 * Zufallsstrom auf demselben Brett läuft.
 *
 * `reserviert`: Kacheln, die andere KI-Spieler im selben Tick schon gewählt haben. Sie
 * gehören noch niemandem (die Spawns laufen erst mit dem Tick), zählen aber als besetzt.
 * Leer (N = 1) ändert sich nichts an der Kachelfolge.
 */
function startKachel(game: Game, zufall: PseudoRandom, abstand: number,
                     reserviert: number[] = []): number {
  const W = game.width(), H = game.height(), map = game.map();
  const frei = (r: number) => {
    const b = map.terrainByte(r);
    return (b & (1 << 7)) !== 0 && (b & 0x1f) !== 31 && !game.hasOwner(r)
      && !reserviert.includes(r);
  };
  // Grober Ring-Test: 16 Richtungen, zwei Radien. Reicht, um Nachbarn zu erkennen.
  const einsam = (r: number) => {
    const x = game.x(r), y = game.y(r);
    for (const q of reserviert) {
      const dx = game.x(q) - x, dy = game.y(q) - y;
      if (dx * dx + dy * dy < abstand * abstand) return false;
    }
    for (const d of [abstand >> 1, abstand]) {
      for (let k = 0; k < 16; k++) {
        const a = (k * Math.PI) / 8;
        const px = x + Math.round(Math.cos(a) * d), py = y + Math.round(Math.sin(a) * d);
        if (px < 0 || py < 0 || px >= W || py >= H) continue;
        if (game.hasOwner(game.ref(px, py))) return false;
      }
    }
    return true;
  };
  let ersatz = -1;
  for (let i = 0; i < 4000; i++) {
    const r = zufall.nextInt(0, W * H);
    if (!frei(r)) continue;
    if (ersatz < 0) ersatz = r;
    if (abstand <= 0 || einsam(r)) return r;
  }
  return ersatz >= 0 ? ersatz : ((W * H) / 2) | 0;
}

interface Zeile {
  seite: Seite;
  spiel_id: string;
  karte: string;
  groesse: string;
  bots: number;
  takt: number;
  ckpt_step: number | null;
  schwelle: number | null;
  netz: string | null;        // was der Server meldet (d0, d1)
  zusatzfehler: number;       // Anfragen, denen die Zusatzfelder fehlten (D1 antwortet dann mit Fehler)
  start_kachel: number;
  sid: number;
  land_kacheln: number;
  gebiet: Record<string, number | null>;      // Anteil an allen Landkacheln
  gebiet_rel: Record<string, number | null>;  // eigenes Gebiet / Gebiet des Grössten
  rang: Record<string, number | null>;
  ende_tick: number;
  ueberleben_ticks: number;
  abbruchgrund: string;
  verlauf: { t: number; gebiet: number; truppen: number; gold: number; lebende: number;
             fuehrer: number }[];
  anfragen: number;
  nichtstun: number;
  ohne_kachel: number;
  serverfehler: number;
  handlungen: number;
  handlungen_je_1000: number;
  aktionen: Record<string, number>;
  platz: number;
  von: number;
  sieg: boolean;
  // Kacheln am Ende: eigene (tot = 0) und die des grössten lebenden Spielers (Belohnung „gebiet“)
  land_ende?: number;
  land_max_ende?: number;
  ms: number;
  ms_inferenz: number;
  // Nur mit --ki > 1 oder --aufnahme:
  ki?: number;                // 1..N
  ki_von?: number;
  client_id?: string;
  name?: string;
  partie?: any;               // Ende, Sieger, Führer — für alle N Zeilen derselben Partie gleich
  aufnahme_id?: string;
  aufnahme?: string;
  aufnahme_ok?: boolean;
  ungueltige_intents?: number;
  srv?: number;               // Index des Inferenz-Servers dieser KI (--inf, reihum); 0 bei einem Server
  spur?: string | null;       // Marke der Trajektorie dieser Partie (.spur.json)
  spur_zeilen?: number;       // Entscheidungen aller KIs dieser Partie in der Spur
  // Spielweise (spielweise.ts), abschaltbar mit ARENA_SPIELWEISE=0:
  spielweise?: { verlauf: Record<string, number[]>; summe: Record<string, number | null> } | null;
  ms_spielweise?: number;     // Rechenzeit der Spielweise-Messung in dieser Partie (alle KIs)
}

interface Ki {
  cid: string;
  name: string;
  z: Zeile;
  anfr: AiAnfrage;
  takt: number;
  gespawnt: boolean;
  spawnTick: number;
  ende: number;       // letzter Tick dieses Spielers (Tod oder Partieende)
  aus: boolean;       // tot oder nie aufs Brett gekommen
}

/** Lebende Spieler nach clientID (wie früher `players().find(...)`: der erste Treffer). */
function nachCid(game: Game): Map<string, Player> {
  const m = new Map<string, Player>();
  for (const p of game.players()) {
    const c = p.clientID();
    if (c !== null && c !== undefined && !m.has(c)) m.set(c, p);
  }
  return m;
}

// --karte zufall: je Partie eine Karte, gewichtet wie im oeffentlichen FFA (ffaFrequency,
// sonst multiplayerFrequency). Die Wahl haengt nur an der spielId, beide Seiten eines Paares
// bekommen also dieselbe Karte.
// Genommen wird m.type (der GameMapType-Wert, z. B. "Bering Sea"), nicht m.id ("BeringSea"):
// die Konfiguration und NodeGameMapLoader erwarten den Wert, sonst "unknown map".
const FFA_KARTEN: { id: string; gewicht: number }[] = maps
  .map((m: any) => ({ id: m.type as string,
                      gewicht: (m.ffaFrequency >= 0 ? m.ffaFrequency : m.multiplayerFrequency) as number }))
  .filter((m) => m.gewicht > 0);
const FFA_SUMME = FFA_KARTEN.reduce((s, m) => s + m.gewicht, 0);

export function karteFuer(spielId: string): string {
  let h = 2166136261;                       // FNV-1a, wie die Saat-Helfer im Trainer
  for (let i = 0; i < spielId.length; i++) {
    h ^= spielId.charCodeAt(i);
    h = Math.imul(h, 16777619) >>> 0;
  }
  // Nachmischen (fmix32 aus MurmurHead3). Ohne das liegen benachbarte Spiel-IDs nur
  // ~0,4 % auseinander und landen paarweise auf derselben Karte (gemessen 20.09.).
  h ^= h >>> 16;
  h = Math.imul(h, 2246822507) >>> 0;
  h ^= h >>> 13;
  h = Math.imul(h, 3266489909) >>> 0;
  h ^= h >>> 16;
  h = h >>> 0;
  let u = (h / 4294967296) * FFA_SUMME;
  for (const m of FFA_KARTEN) {
    u -= m.gewicht;
    if (u < 0) return m.id;
  }
  return FFA_KARTEN[FFA_KARTEN.length - 1].id;
}

/** Lobbygrösse und Botzahl wie der öffentliche Spielplan (src/server/MapPlaylist.ts), aber ohne
 *  Math.random: die Wahl gross/mittel/klein hängt an der Spiel-ID. Land der Karte aus manifest.json.
 *  Kompakte Karten: 25 % der Spieler (mindestens 3) und 100 statt 400 Bots. Deckel 125. */
export function lobby(spielId: string, karte: string, kompakt: boolean): { ki: number; bots: number } {
  const key = Object.keys(GameMapType).find((k) => (GameMapType as any)[k] === karte);
  if (key === undefined) throw new Error(`unbekannte Karte: ${karte}`);
  const man = JSON.parse(fs.readFileSync(path.join(path.resolve("resources/maps"), key.toLowerCase(), "manifest.json"), "utf8"));
  const land: number = man.map.num_land_tiles;
  const r5 = (n: number) => Math.round(n / 5) * 5;
  const gross = Math.max(r5((land / 1_000_000) * 50), 5);
  const stufen = [gross, r5(gross * 0.75), r5(gross * 0.5)];
  let h = 2166136261;
  for (const c of spielId + ":lobby") { h ^= c.charCodeAt(0); h = Math.imul(h, 16777619) >>> 0; }
  h ^= h >>> 16; h = Math.imul(h, 2246822507) >>> 0; h ^= h >>> 13; h = Math.imul(h, 3266489909) >>> 0; h ^= h >>> 16;
  const u = (h >>> 0) / 4294967296;
  let p = Math.min(u < 0.3 ? stufen[0] : u < 0.6 ? stufen[1] : stufen[2], gross);
  if (kompakt) p = Math.max(3, Math.floor(p * 0.25));
  return { ki: Math.min(p, 125), bots: kompakt ? 100 : 400 };
}

async function spielePartie(o: Optionen, spielId: string, seite: Seite,
                            info: any): Promise<Zeile[]> {
  const t0 = Date.now();
  const karte = o.karte === "zufall" ? karteFuer(spielId) : o.karte;
  const lob = o.kiAuto || o.botsAuto ? lobby(spielId, karte, o.groesse === "Compact") : null;
  const kiAnz = o.kiAuto ? lob!.ki : o.ki;
  const botAnz = o.botsAuto ? lob!.bots : o.bots;
  const aufnahme = o.aufnahme !== null;
  const neueIds = aufnahme || kiAnz > 1;
  const gid = aufnahme ? aufnahmeId(spielId) : spielId;
  const cfgRoh: any = {
    gameMap: karte, gameMapSize: o.groesse, gameMode: "Free For All",
    gameType: "Singleplayer", difficulty: process.env.ARENA_SCHWIERIGKEIT ?? "Medium", nations: "default",
    bots: botAnz, disabledUnits: [], playerTeams: 0,
    infiniteGold: false, infiniteTroops: false, instantBuild: false,
    randomSpawn: false, donateGold: false, donateTroops: false,
  };
  // Aufnahme: die Konfiguration so, wie der Client sie beim Abspielen aus dem Record liest.
  const cfg: any = aufnahme ? GameConfigSchema.parse(cfgRoh) : cfgRoh;
  const kiNamen = Array.from({ length: kiAnz }, (_, i) => neueIds
    ? { cid: `ki${String(i + 1).padStart(6, "0")}`, name: `KI ${String(i + 1).padStart(2, "0")}` }
    : { cid: KI_CID, name: "ARENA-KI" });
  const spieler = kiNamen.map((k, i) => neueIds
    ? { username: k.name, clientID: k.cid, isLobbyCreator: i === 0, clanTag: null, friends: [] }
    : { username: "ARENA-KI", clientID: KI_CID, isLobbyCreator: true,
        clanTag: null, friends: [], teamIndex: null });
  const gameStart: GameStartInfo = toWireGameStartInfo({
    gameID: gid, lobbyCreatedAt: 0, config: cfg, players: spieler, tribes: [] } as any);
  const config = new Config(cfg, null, false);
  const terrain = await loadTerrainMap(cfg.gameMap, cfg.gameMapSize,
    new NodeGameMapLoader(path.resolve("resources/maps")), false);
  const zufall = new PseudoRandom(simpleHash(gameStart.gameID));
  const humans = gameStart.players.map((p) => new PlayerInfo(p.username, PlayerType.Human,
    p.clientID, zufall.nextID(), p.isLobbyCreator ?? false, p.clanTag, p.friends ?? [],
    p.teamIndex ?? null));
  const nations = createNationsForGame(gameStart, terrain.nations, terrain.additionalNations,
    humans.length, zufall);
  const game: Game = createGame(humans, nations, terrain.gameMap, terrain.miniGameMap,
    config, terrain.teamGameSpawnAreas);
  let fatal: string | undefined;
  let sieger: string | null = null;
  let siegerRoh: any = undefined;
  let siegTick = -1;
  let letzteGu: any = null;
  const hashes = new Map<number, number>();
  let sw: Spielweise | null = null;               // Spielweise-Messung, ab dem Aufbau unten
  const runner = new GameRunner(game,
    new Executor(game, gameStart.gameID, undefined, gameStart.tribes?.map((t: any) => t.name)),
    (gu: any) => {
      if ("errMsg" in gu) { fatal = gu.errMsg; return; }
      letzteGu = gu;
      try {
        for (const u of Object.values(gu.updates ?? {}) as any[]) {
          for (const e of u ?? []) {
            if (e && "winner" in e) {
              sieger = JSON.stringify(e.winner);
              if (siegerRoh === undefined) { siegerRoh = e.winner; siegTick = game.ticks(); }
            }
          }
        }
        sw?.updates(gu.updates);
        // Wie LocalServer im Einzelspiel: nur jeder 100. Hash kommt in den Record.
        if (aufnahme) {
          for (const e of gu.updates?.[GameUpdateType.Hash] ?? []) {
            if (e.tick % 100 === 0) hashes.set(e.tick, e.hash);
          }
        }
      } catch { /* Siegermeldung ist Zugabe, darf nie kippen */ }
    });
  runner.init();

  // D1: Zusatzfelder ab Tick 0 mitführen. Der Zeilenplan (Materialisierer v2) sorgt dafür,
  // dass das Alter der Angriffe dasselbe bedeutet wie im Training, egal wie oft gefragt wird.
  const spur = seite === "netz" && info?.zusatz === true
    ? new ZusatzSpur(game, UnitType, { ...POOL_PLAN,
        phase: (c: string) => Math.abs(simpleHash(c)) % POOL_PLAN.noopEvery })
    : null;
  for (const k of kiNamen) spur?.verfolge(k.cid);
  // Trajektorien-Recorder: nur die Netz-Seite hat Entscheidungen
  const rec = o.spur !== null && seite === "netz"
    ? new SpurSchreiber(path.resolve(o.spur), gid + (o.spurKennung ? "_" + o.spurKennung : ""), spur !== null)
    : null;

  // Eigener Zufallsstrom für die Startkachel, damit Nationen/Bots exakt gleich bleiben.
  const spawnZufall = new PseudoRandom(simpleHash(gameStart.gameID + ":spawn"));
  const landKacheln = (game.map() as any).numLandTiles?.() ?? 1;
  sw = process.env.ARENA_SPIELWEISE === "0" ? null : new Spielweise(game, landKacheln);

  const neueZeile = (): Zeile => {
    const z: Zeile = {
      seite, spiel_id: spielId, karte, groesse: o.groesse, bots: botAnz, takt: o.takt,
      ckpt_step: info?.ckpt_step ?? null, schwelle: info?.schwelle ?? null,
      netz: info?.netz ?? null, zusatzfehler: 0,
      start_kachel: -1, sid: -1, land_kacheln: landKacheln,
      gebiet: {}, gebiet_rel: {}, rang: {}, ende_tick: 0, ueberleben_ticks: 0, abbruchgrund: "tick_limit",
      verlauf: [], anfragen: 0, nichtstun: 0, ohne_kachel: 0, serverfehler: 0,
      handlungen: 0, handlungen_je_1000: 0, aktionen: {},
      platz: 0, von: 0, sieg: false, ms: 0, ms_inferenz: 0,
    };
    for (const m of MESSPUNKTE) {
      z.gebiet[String(m)] = null; z.gebiet_rel[String(m)] = null; z.rang[String(m)] = null;
    }
    return z;
  };
  // Bei mehreren KIs teilen sie sich den Scan der Karte (einmal je Tick statt je KI).
  const geteilt: Geteilt | undefined = kiAnz > 1
    ? { enc: new ObsEncoder(180, 90), cells: null, cellsGame: null } : undefined;
  const kis: Ki[] = kiNamen.map((k) => ({
    cid: k.cid, name: k.name, z: neueZeile(), anfr: new AiAnfrage(cfg, geteilt), takt: o.takt,
    gespawnt: false, spawnTick: -1, ende: 0, aus: false,
  }));

  const todesTick = new Map<number, number>();   // smallID → Tick des Todes
  let letzterTick = 0;
  let grund = "tick_limit";                        // warum die Partie aufhört
  let ungueltig = 0;                               // Intents, die das Schema nicht annimmt (Aufnahme)
  const alleTurns: any[] = [];

  // Zustand für die Belohnung (belohnung.py) im Entscheidungstick: eigener Gebietsanteil,
  // ausgeschiedene und aufs Brett gekommene Spieler. Zählungen einmal je Tick.
  let zTick = -1, zTot = 0, zSpieler = 0, zLebende = 0;
  const zustand = (wir: Player, t: number) => {
    if (zTick !== t) {
      zTick = t;
      zTot = todesTick.size;
      const da = game.allPlayers().filter((p) => p.hasSpawned?.() !== false);
      zSpieler = da.length;
      zLebende = da.filter((p) => p.isAlive()).length;
    }
    return { gebiet: Number((wir.numTilesOwned() / landKacheln).toFixed(7)), tot: zTot,
             spieler: zSpieler, lebende: zLebende };
  };
  const ZIEL_GRUPPEN = new Set(["boot", "atom", "wasserstoff", "mirv"]);   // tabellen.ZIEL_GRUPPEN
  const summe = (x: Record<string, number>) => Object.values(x).reduce((s, v) => s + Number(v), 0);
  /**
   * Eine Entscheidung in die Spur. it = gesendeter Intent (null: nichts gesendet).
   * ergebnis: ausgefuehrt | nichtstun (unter der Schwelle) | ohne_kachel (keine der Zellen
   * gültig) | server_kein (Server wählte eine Aktion, fand aber nichts Ausführbares).
   * lab = die Wahl der Verhaltenspolitik je Kopf; nur sie hat die aufgezeichnete log μ.
   */
  const spurZeile = (t: number, f: { k: Ki; wir: Player; body: any; zst: any }, j: any,
                     it: any | null, ergebnis: string) => {
    if (!rec) return;
    // Gegnerpool: KIs an den hinteren Servern spielen aus alten Netzen. Ihre Entscheidungen
    // stammen aus einer anderen Verhaltenspolitik und dürfen nicht ins Training.
    if (o.spurNurSrv > 0 && (f.k.z.srv ?? 0) >= o.spurNurSrv) return;
    const b = f.body, c = b.ctx;
    const handelt = ergebnis !== "nichtstun";
    const lab: Record<string, number> = handelt ? { ...(j.wahl ?? {}) } : { atype: 0 };
    const lpV: Record<string, number> = { ...(j.logp_v ?? {}) };
    const lpN: Record<string, number> = { ...(j.logp_n ?? {}) };
    let kandI = -1, resTile = -1, mitZelle = false;
    if (it && Array.isArray(j.kandidaten)) {
      resTile = it.type === "boat" ? it.dst : it.tile;
      kandI = j.kandidaten.indexOf(zelleVon(game, resTile));
      if (kandI >= 0 && Array.isArray(j.kand_logp_n)) {      // Zellzeiger (MIRV hat keinen)
        lab.coarse = j.kandidaten[kandI];
        lpN.coarse = j.kand_logp_n[kandI];
        lpV.coarse = 0;                                     // Top-5, erste gültige: deterministisch
        mitZelle = true;
      }
    }
    const g: string | null = handelt ? (j.gruppe ?? null) : null;
    const meta: any = {
      turn: t, tick: b.tick, clientID: f.k.cid, sid: b.sid, allies: b.allies, team: null,
      kind: handelt ? "act" : "noop",
      intent: it ?? (handelt && j.intent?.type !== "no_op" ? j.intent : handelt ? {} : { type: "no_op" }),
      w: 1, w_tick: 1, win: 0,
      mapW: c.mapW, mapH: c.mapH, troops: c.troops, gold: c.gold, oppIds: c.oppIds,
      ownUnitIds: c.ownUnitIds, ownAttackIds: c.ownAttackIds, own: b.own, opps: b.opps,
      ...(g ? { res_tile: resTile, res_kind: resTile >= 0 ? 0 : 2,
                dst_owner: ZIEL_GRUPPEN.has(g) ? (j.zielSid ?? 0) : 0 } : {}),
      lab,
      rl: { ergebnis, grund: j.grund ?? null, p_handeln: j.p_handeln, value: j.value,
            schwelle: info?.schwelle ?? null, gezogen: j.gezogen ?? false,
            logp_v: lpV, logp_n: lpN, logp_v_summe: summe(lpV), logp_n_summe: summe(lpN),
            kand: j.kandidaten ?? null, kand_i: kandI, takt: f.k.takt, srv: f.k.z.srv ?? 0, ...f.zst },
    };
    rec.zeile(meta, b, mitZelle);
  };

  // KI i fragt Server i mod n (nur bei --ki > 1 mit mehreren --inf; sonst gibt es einen).
  const urls = o.inf.split(",").map((x) => x.trim()).filter(Boolean);
  const urlVon = new Map<Ki, string>(kis.map((k, i) => [k, urls[i % urls.length]]));
  for (const [i, k] of kis.entries()) k.z.srv = i % urls.length;

  for (let t = 0; t < o.ticks; t++) {
    const intents: any[] = [];
    const vorher = nachCid(game);
    const reserviert: number[] = [];               // Startkacheln dieses Ticks
    const faellig: { k: Ki; wir: Player; body: any; zst: any }[] = [];
    for (const k of kis) {
      const wir = vorher.get(k.cid);
      if (wir && !k.gespawnt) {
        k.gespawnt = true; k.spawnTick = t; k.z.sid = wir.smallID();
        sw?.verfolge(k.cid, wir, t);
      }
      if (!wir && game.inSpawnPhase()) {
        // Erst ab --start-tick: im Einzelspiel endet die Startphase, sobald ein Mensch
        // eine Kachel wählt (SpawnExecution). Wer bei Tick 0 startet, spielt gegen
        // Nationen und Bots, die noch gar nicht auf dem Brett sind. 100 Ticks ist die
        // Länge der Startphase, die die Engine im Einzelspiel selbst vorsieht.
        // Danach alle 10 Ticks ein Versuch; die Zahl der Versuche ist deterministisch,
        // also ist die Kachelfolge auf beiden Seiten dieselbe.
        if (t >= o.startTick && t % 10 === 0) {
          const kachel = startKachel(game, spawnZufall, o.startAbstand, reserviert);
          reserviert.push(kachel);
          if (k.z.start_kachel < 0) k.z.start_kachel = kachel;
          intents.push({ type: "spawn", tile: kachel, clientID: k.cid });
        }
      } else if (seite === "netz" && wir && wir.isAlive() && (t - k.spawnTick) % k.takt === 0) {
        const body = k.anfr.baue(game, wir, k.takt);
        // Schlüssel fürs Ziehen (inf_d0 --wahl-saat): je Partie, Spieler und Tick derselbe, auf
        // beiden Seiten eines Paars gleich und unabhängig davon, in welcher Reihenfolge die
        // Anfragen gleichzeitiger Partien beim Server ankommen.
        (body as any).wahl_schluessel = `${spielId}|${k.cid}|${t}`;
        if (spur) {
          try { Object.assign(body, zusatzAnfrage(spur, wir, body.ctx)); } catch { k.z.zusatzfehler++; }
        }
        faellig.push({ k, wir, body, zst: rec ? zustand(wir, t) : null });
      }
    }
    if (faellig.length) {
      // Gleichzeitig raus; der Server rechnet nacheinander (Entscheider.lock). Die Antworten
      // werden in KI-Reihenfolge verbucht, der Zug hängt nicht von der Ankunftszeit ab.
      const antworten = await Promise.all(faellig.map(async (f) => {
        const ti = Date.now();
        let j: any = null;
        try {
          const r = await fetch(urlVon.get(f.k) ?? urls[0], { method: "POST",
            headers: { "Content-Type": "application/json" }, body: JSON.stringify(f.body) });
          j = await r.json();
        } catch (e: any) {
          j = { error: String(e?.message ?? e) };
        }
        f.k.z.ms_inferenz += Date.now() - ti;
        return j;
      }));
      faellig.forEach((f, i) => {
        const j = antworten[i], z = f.k.z;
        z.anfragen++;
        if (j?.error) { z.serverfehler++; return; }
        if (typeof j.decide_every === "number" && j.decide_every > 0) f.k.takt = j.decide_every;
        if (!j.intent || j.intent.type === "no_op") {
          z.nichtstun++;
          spurZeile(t, f, j, null, j.atype && j.atype !== "NO_OP" ? "server_kein" : "nichtstun");
          return;
        }
        const it = intentAusAntwort(game, f.wir, j);
        if (!it) { z.ohne_kachel++; spurZeile(t, f, j, null, "ohne_kachel"); return; }
        const name = `${j.atype}${j.einheit ? "/" + j.einheit : j.gruppe ? "/" + j.gruppe : ""}`;
        z.aktionen[name] = (z.aktionen[name] ?? 0) + 1;
        z.handlungen++;
        intents.push({ ...it, clientID: f.k.cid });
        spurZeile(t, f, j, it, "ausgefuehrt");
      });
    }
    // Aufnahme: nur, was das Schema annimmt, und genau in der Form, die der Client nachspielt.
    let zug = intents;
    if (aufnahme) {
      zug = [];
      for (const it of intents) {
        const p = StampedIntentSchema.safeParse(it);
        if (p.success) { zug.push(p.data); continue; }
        if (ungueltig++ < 5) log(`[arena] Intent passt nicht ins Schema, weggelassen: ${JSON.stringify(it).slice(0, 300)}`);
      }
      alleTurns.push({ turnNumber: t, intents: zug });
    }
    const turn = { turnNumber: t, gameID: gameStart.gameID, intents: zug } as any;
    spur?.vorTick(turn);                     // Reihenfolge der Haken: siehe ZusatzSpur
    runner.addTurn(turn);
    const lief = runner.executeNextTick();
    if (spur && lief) spur.nachTick(letzteGu?.updates?.[GameUpdateType.Unit]);
    letzterTick = t;
    if (fatal) { grund = "engine_fehler:" + fatal; break; }

    for (const p of game.allPlayers()) {
      if (!p.isAlive() && p.hasSpawned?.() !== false && !todesTick.has(p.smallID())) {
        todesTick.set(p.smallID(), t);
      }
    }
    const lebende = game.players().filter((p) => p.isAlive());
    const nachher = nachCid(game);
    for (const k of kis) {
      if (k.aus) continue;
      const z = k.z;
      const w2 = nachher.get(k.cid);
      const anteil = w2 ? w2.numTilesOwned() / landKacheln : 0;
      if (k.gespawnt && t % o.verlaufAlle === 0) {
        // fuehrer: Gebietsanteil des Grössten. Ohne den sagt das eigene Gebiet wenig —
        // 2 % sind auf leerer Karte wenig und in der Endphase viel.
        const fuehrer = lebende.reduce((m, p) => Math.max(m, p.numTilesOwned()), 0) / landKacheln;
        z.verlauf.push({ t, gebiet: Number(anteil.toFixed(6)),
          truppen: w2 ? Math.round(w2.troops()) : 0,
          gold: w2 ? Number(w2.gold()) : 0, lebende: lebende.length,
          fuehrer: Number(fuehrer.toFixed(6)) });
        if (sw && w2 && w2.isAlive()) sw.probe(t, k.cid, w2);
      }
      for (const m of MESSPUNKTE) {
        if (t !== m) continue;
        const fuehrer = lebende.reduce((mx, p) => Math.max(mx, p.numTilesOwned()), 0);
        z.gebiet[String(m)] = Number(anteil.toFixed(6));
        z.gebiet_rel[String(m)] = fuehrer > 0 && w2
          ? Number((w2.numTilesOwned() / fuehrer).toFixed(5)) : 0;
        z.rang[String(m)] = w2 && w2.isAlive()
          ? 1 + lebende.filter((p) => p.numTilesOwned() > w2.numTilesOwned()).length : null;
      }
      if (k.gespawnt && (!w2 || !w2.isAlive())) {
        k.aus = true; k.ende = t; z.abbruchgrund = "tot";
      } else if (kiAnz > 1 && !k.gespawnt && !w2 && !game.inSpawnPhase() && t >= o.startTick) {
        k.aus = true; k.ende = t; z.abbruchgrund = "nie_gespawnt";
      }
    }
    if (kis.every((k) => k.aus)) { grund = kiAnz > 1 ? "alle_ki_tot" : "tot"; break; }
    if (!game.inSpawnPhase() && lebende.length <= 1 && t > 200) { grund = "sieg"; break; }
    if (kiAnz > 1 && siegTick >= 0 && t >= siegTick + 20) { grund = "entschieden"; break; }
  }

  const amEnde = nachCid(game);
  const alle = game.allPlayers().filter((p) => p.hasSpawned?.() !== false);
  const schl = (p: Player) => [todesTick.get(p.smallID()) ?? Infinity, p.numTilesOwned()];
  const landMaxEnde = game.players().filter((p) => p.isAlive())
    .reduce((m, p) => Math.max(m, p.numTilesOwned()), 0);
  let partie: any = undefined;
  if (neueIds) {
    const lebende = game.players().filter((p) => p.isAlive());
    const gross = lebende.reduce<Player | null>((m, p) =>
      !m || p.numTilesOwned() > m.numTilesOwned() ? p : m, null);
    let siegerName: string | null = null;
    if (Array.isArray(siegerRoh)) {
      siegerName = siegerRoh[0] === "player"
        ? (game.playerByClientID(siegerRoh[1]) as Player | null)?.name() ?? siegerRoh[1]
        : String(siegerRoh[1]);
    }
    partie = {
      grund, ende_tick: letzterTick, sieg_tick: siegTick >= 0 ? siegTick : null,
      sieger: siegerRoh ?? null, sieger_name: siegerName,
      fuehrer: gross ? { name: gross.name(), typ: gross.type(), client_id: gross.clientID(),
        gebiet: Number((gross.numTilesOwned() / landKacheln).toFixed(6)) } : null,
      lebende: lebende.length, ki_lebend: kis.filter((k) => !k.aus).length, spieler: alle.length,
    };
  }

  const zeilen: Zeile[] = [];
  kis.forEach((k, i) => {
    const z = k.z;
    if (!k.aus) { k.ende = letzterTick; z.abbruchgrund = grund; }
    const w = amEnde.get(k.cid);
    // Messpunkte hinter dem Spielende: wer tot ist, hat 0 Gebiet. Das ist eine Messung,
    // keine fehlende Angabe — sonst verschiebt jeder frühe Tod den Median nach oben.
    for (const m of MESSPUNKTE) {
      if (z.gebiet[String(m)] === null && k.ende < m && z.abbruchgrund !== "tick_limit") {
        z.gebiet[String(m)] = w && w.isAlive() ? Number((w.numTilesOwned() / landKacheln).toFixed(6)) : 0;
        z.gebiet_rel[String(m)] = 0;
      }
    }
    z.ende_tick = k.ende;
    z.ueberleben_ticks = k.gespawnt ? k.ende - k.spawnTick : 0;
    z.sieg = !!(w && w.isAlive() && z.abbruchgrund === "sieg");
    if (sieger) z.sieg = (sieger as string).includes(k.cid);
    z.land_ende = w && w.isAlive() ? w.numTilesOwned() : 0;
    z.land_max_ende = landMaxEnde;

    // Platz: wer länger lebte, steht vorn; bei gleichem Ende entscheidet das Gebiet.
    const unsSchl = w ? schl(w) : [todesTick.get(z.sid) ?? 0, 0];
    z.von = alle.length;
    z.platz = 1 + alle.filter((p) => {
      const s = schl(p);
      return s[0] > unsSchl[0] || (s[0] === unsSchl[0] && s[1] > unsSchl[1]);
    }).length;
    // Dasselbe ohne Tribes (PlayerType.Bot): Bei 400 Bots scheiden die fast von selbst aus, der
    // Platz unter allen presst gute und mittlere Partien auf R 0,9–0,99 (Belohnung, 15.09.).
    const ohneBots = alle.filter((p) => p.type() !== PlayerType.Bot);
    z.von_ohne_bots = ohneBots.length;
    z.platz_ohne_bots = 1 + ohneBots.filter((p) => {
      const s = schl(p);
      return s[0] > unsSchl[0] || (s[0] === unsSchl[0] && s[1] > unsSchl[1]);
    }).length;
    z.handlungen_je_1000 = z.ueberleben_ticks > 0
      ? Number(((z.handlungen * 1000) / z.ueberleben_ticks).toFixed(3)) : 0;
    if (sw) z.spielweise = sw.ergebnis(k.cid, z.ueberleben_ticks);
    z.ms = Date.now() - t0;
    if (neueIds) {
      z.ki = i + 1; z.ki_von = kiAnz; z.client_id = k.cid; z.name = k.name; z.partie = partie;
    }
    zeilen.push(z);
  });
  if (sw) for (const z of zeilen) z.ms_spielweise = Number(sw.ms().toFixed(1));

  if (rec) {
    // Kopf wie DESIGN §5.1 (players aus allPlayers am Ende, auch Bots), dazu das Ergebnis je KI
    const ki = kis.map((k, i) => ({
      clientID: k.cid, name: k.name, srv: zeilen[i].srv ?? 0,
      sid: zeilen[i].sid, platz: zeilen[i].platz, von: zeilen[i].von,
      platz_ohne_bots: zeilen[i].platz_ohne_bots, von_ohne_bots: zeilen[i].von_ohne_bots,
      sieg: zeilen[i].sieg, ueberleben_ticks: zeilen[i].ueberleben_ticks, ende_tick: zeilen[i].ende_tick,
      spawn_tick: k.spawnTick, abbruchgrund: zeilen[i].abbruchgrund,
      land_ende: zeilen[i].land_ende, land_max_ende: zeilen[i].land_max_ende }));
    const players = game.allPlayers().map((p) => ({ sid: p.smallID(), clientID: p.clientID() ?? null,
      playerID: p.id(), name: p.name(), team: (p as any).team?.() ?? null, type: p.type() }));
    const datei = rec.schliessen({
      map: karte, mapSize: o.groesse, W: game.width(), H: game.height(), landTiles: landKacheln,
      config: cfg, players, ki,
      partie: { grund, ende_tick: letzterTick, sieg_tick: siegTick >= 0 ? siegTick : null,
                sieger: siegerRoh ?? null },
      rl: { spiel_id: spielId, seite, schwierigkeit: cfg.difficulty, takt: o.takt, bots: botAnz,
            ticks: o.ticks, server: info } });
    for (const z of zeilen) { z.spur = datei; z.spur_zeilen = rec.n; }
    if (datei) log(`[arena] Spur ${datei}: ${rec.n} Entscheidungen`);
  }

  if (aufnahme) {
    for (const [tick, h] of hashes) if (tick < alleTurns.length) alleTurns[tick].hash = h;
    const spielerRec = gameStart.players.map((p) => ({ ...p, persistentID: null, stats: {} }));
    const rec: any = createPartialGameRecord(gameStart.gameID, cfg, spielerRec as any, alleTurns,
      t0, Date.now(), siegerRoh, 0, undefined, []);
    rec.gitCommit = "DEV";
    const pr = GameRecordSchema.safeParse(rec);
    fs.mkdirSync(o.aufnahme!, { recursive: true });
    const datei = path.resolve(o.aufnahme!, `${gameStart.gameID}.json`);
    fs.writeFileSync(datei, JSON.stringify(rec));
    log(`[arena] Aufnahme ${datei}: ${rec.info.num_turns} Züge, ${rec.turns.length} mit Inhalt, `
      + `Schema ${pr.success ? "ok" : "FEHLER"}`);
    if (!pr.success) log(`[arena] GameRecordSchema: ${pr.error.message.slice(0, 1500)}`);
    for (const z of zeilen) {
      z.aufnahme_id = gameStart.gameID; z.aufnahme = datei; z.aufnahme_ok = pr.success;
      z.ungueltige_intents = ungueltig;
    }
  }
  return zeilen;
}

/** GET auf den Server-Wurzelpfad: Checkpoint-Schritt, Schwelle, Takt. */
async function serverInfo(inf: string): Promise<any> {
  const wurzel = inf.replace(/\/act\/?$/, "/");
  const r = await fetch(wurzel);
  return await r.json();
}

/**
 * Genau eine Partie je Prozess — der Grund ist gemessen, nicht Geschmack:
 * `loadTerrainMap` gibt dieselbe `GameMap` aus einem Modul-Cache zurück, und `GameImpl`
 * arbeitet darauf im Original (kein Kopieren). Die zweite Partie im selben Prozess
 * startet also auf dem Endbrett der ersten. Beim ersten Bauversuch war das sichtbar:
 * Partie 2 hatte bei Tick 250 nur noch 3 lebende Spieler statt 43 und eine andere
 * Startkachel. Ein Prüfstand, dessen Partien sich gegenseitig anfassen, misst nichts.
 * Eigene Prozesse trennen sauber und geben `--jobs` gratis dazu.
 */
function kindArgumente(o: Optionen, spielId: string, seite: Seite, inf: string): string[] {
  const argsOhne = (a: string[], n: string) => {
    const i = a.indexOf("--" + n);
    return i < 0 ? a : [...a.slice(0, i), ...a.slice(i + 2)];
  };
  let basis = process.argv.slice(2);
  for (const n of ["jobs", "partien", "aus", "seiten", "ein-spiel", "seite", "inf"]) {
    basis = argsOhne(basis, n);
  }
  // Mit mehreren KIs je Partie bekommt das Kind alle Server: eine Partie mit 125 KIs schickt
  // je Entscheidungs-Tick 125 Anfragen und würde einen einzelnen inf_d0 (rechnet nacheinander)
  // zum Flaschenhals machen. spielePartie verteilt die KIs dann reihum auf die Server.
  return [...process.execArgv, process.argv[1], ...basis,
    "--inf", o.ki > 1 || o.kiAuto ? o.inf : inf, "--ein-spiel", spielId, "--seite", seite];
}

/** Startet die Aufgaben mit höchstens `jobs` Prozessen und sammelt die Zeilen ein. */
async function fuehreAus(o: Optionen, aufgaben: { id: string; seite: Seite }[]): Promise<Zeile[]> {
  const { spawn } = await import("child_process");
  const zeilen: Zeile[] = [];
  // Mehrere --inf durch Komma getrennt: die Kinder teilen sich die Server reihum. Ein
  // inf_d0 rechnet eine Anfrage nach der anderen (Entscheider.lock), ein einzelner Server
  // ist also die Obergrenze für --jobs, egal wie viele Kerne die Maschine hat.
  const server = o.inf.split(",").map((s) => s.trim()).filter(Boolean);
  let naechste = 0;
  const einer = async (): Promise<void> => {
    while (naechste < aufgaben.length) {
      const k = naechste++;
      const a = aufgaben[k];
      const text: string = await new Promise((res) => {
        const c = spawn(process.execPath, kindArgumente(o, a.id, a.seite, server[k % server.length]),
          { stdio: ["ignore", "pipe", "inherit"] });
        let s = "";
        c.stdout.on("data", (d: Buffer) => (s += d));
        c.on("close", () => res(s));
      });
      const letzte = text.trim().split("\n").pop() ?? "";
      let z: any;
      try { z = JSON.parse(letzte); } catch { z = { ok: false, fehler: "keine Ausgabe: " + text.slice(0, 200) }; }
      if (!Array.isArray(z) && z.ok === false) {
        log(`[arena] ${a.id} ${a.seite} FEHLER: ${z.fehler}`);
        FEHLGESCHLAGEN.push(`${a.id} ${a.seite}: ${String(z.fehler).slice(0, 200)}`);
        continue;
      }
      for (const e of (Array.isArray(z) ? z : [z]) as Zeile[]) {
        zeilen.push(e);
        fs.appendFileSync(o.aus, JSON.stringify(e) + "\n");
        log(`[arena] ${a.id} ${a.seite}${e.ki ? " " + e.name : ""}: Gebiet@500=${e.gebiet["500"]} `
          + `@1000=${e.gebiet["1000"]} Überleben=${e.ueberleben_ticks} Platz=${e.platz}/${e.von} `
          + `Handlungen/1000=${e.handlungen_je_1000} Grund=${e.abbruchgrund} ${(e.ms / 1000).toFixed(1)}s`);
      }
    }
  };
  await Promise.all(Array.from({ length: Math.max(1, Math.min(o.jobs, aufgaben.length)) }, einer));
  return zeilen;
}

// Partien, die gar keine Zeile ergaben (Kind gestorben, Karte fehlt, Engine-Fehler). Bis zum
// 20.09. gingen die still verloren: die Zusammenfassung meldete "ok" für 2 von 8 Partien.
const FEHLGESCHLAGEN: string[] = [];

function zusammenfassung(zeilen: Zeile[], info: any, o: Optionen): any {
  const med = (v: number[]) => {
    if (!v.length) return null;
    const s = [...v].sort((a, b) => a - b);
    return s.length % 2 ? s[(s.length - 1) / 2] : (s[s.length / 2 - 1] + s[s.length / 2]) / 2;
  };
  const je: Record<string, any> = {};
  for (const s of new Set(zeilen.map((z) => z.seite))) {
    const g = zeilen.filter((z) => z.seite === s);
    je[s] = {
      partien: g.length,
      ...Object.fromEntries(MESSPUNKTE.map((m) => [`gebiet_${m}_median`,
        med(g.map((z) => z.gebiet[String(m)]).filter((x): x is number => x !== null))])),
      ...Object.fromEntries(MESSPUNKTE.map((m) => [`gebiet_rel_${m}_median`,
        med(g.map((z) => z.gebiet_rel[String(m)]).filter((x): x is number => x !== null))])),
      ueberleben_median: med(g.map((z) => z.ueberleben_ticks)),
      handlungen_je_1000_median: med(g.map((z) => z.handlungen_je_1000)),
      platz_median: med(g.map((z) => z.platz)),
      siege: g.filter((z) => z.sieg).length,
      tot: g.filter((z) => z.abbruchgrund === "tot").length,
      ms_median: med(g.map((z) => z.ms)),
    };
  }
  // Mehr als 5 % Ausfall heisst: die Messung taugt nicht mehr, der Aufrufer soll abbrechen.
  // Einzelne Ausfälle (bekannter Engine-Tickfehler) lassen einen langen Lauf nicht scheitern.
  const fehlend = FEHLGESCHLAGEN.length;
  // Bei --ki auto kennt man die Zeilenzahl nicht vorab (Lobbygrösse je Karte): die Ausfallgrenze
  // rechnet dann in Partien.
  const erwartet = o.einSpiel !== null ? 1
    : o.kiAuto ? o.partien * o.seiten.length : o.partien * o.seiten.length * Math.max(1, o.ki);
  const aus: any = { ok: zeilen.length > 0 && fehlend <= Math.floor(0.05 * erwartet),
    partien: zeilen.length, erwartet, fehlende: fehlend, ckpt_step: info?.ckpt_step ?? null,
    schwelle: info?.schwelle ?? null, seiten: je };
  if (fehlend) aus.fehler_beispiele = FEHLGESCHLAGEN.slice(0, 3);
  if (o.kiAuto) aus.ki = "auto";
  else if (o.ki > 1) aus.ki = o.ki;          // dann zählt `partien` KI-Zeilen, nicht Partien
  if (o.aufnahme !== null) {
    aus.aufnahmen = [...new Set(zeilen.map((z) => z.aufnahme).filter(Boolean))];
  }
  return aus;
}

async function main() {
  const o = argumente();
  let info: any = null;
  // Im Kindmodus zählt die eine Seite dieses Prozesses, nicht die Liste des Elternteils:
  // ein Kind mit --seite nichtstun braucht keinen Server und darf an ihm nicht scheitern.
  const brauchtServer = o.einSpiel !== null ? o.seite === "netz" : o.seiten.includes("netz");
  if (brauchtServer) {
    try {
      info = await serverInfo(o.inf.split(",")[0].trim());
      log(`[arena] Server: ${JSON.stringify(info)}`);
    } catch (e: any) {
      process.stdout.write(JSON.stringify({ ok: false,
        fehler: `Inferenz-Server ${o.inf} antwortet nicht: ${e?.message ?? e}` }) + "\n");
      process.exit(2);
    }
  }
  if (o.einSpiel !== null) {                  // Kindmodus: genau eine Partie, Zeile(n) auf stdout
    const zs = await spielePartie(o, o.einSpiel, o.seite ?? "netz", info);
    process.stdout.write(JSON.stringify(zs.length === 1 && !zs[0].ki ? zs[0] : zs) + "\n");
    return;
  }
  fs.writeFileSync(o.aus, "");
  const aufgaben: { id: string; seite: Seite }[] = [];
  for (let i = 0; i < o.partien; i++) {
    const spielId = `${o.saat}-${String(i).padStart(4, "0")}`;
    for (const seite of o.seiten) aufgaben.push({ id: spielId, seite });
  }
  const zeilen = await fuehreAus(o, aufgaben);
  process.stdout.write(JSON.stringify(zusammenfassung(zeilen, info, o)) + "\n");
}

main().catch((e) => {
  process.stdout.write(JSON.stringify({ ok: false, fehler: String(e?.stack ?? e) }) + "\n");
  process.exit(1);
});

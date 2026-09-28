/**
 * Prüft das **gebaute** Kern-Skript (kern.js) ohne Browser: Übernimmt es den Socket,
 * liest es den Startbrief, findet es die eigene Kennung, reicht es die Züge an den
 * Worker weiter — und schweigt `sendeAbsicht` ohne ausdrückliche Einstellung?
 *
 * Der Worker wird hier nur nachgestellt (die Engine selbst ist in aitest/kernprobe.ts
 * belegt); gezählt wird, was der Kern ihm schickt.
 *
 *   npx tsx aitest/kernladeprobe.ts <kern.js> <record.json> [ticks]
 */
import fs from "fs";
import vm from "vm";
import { GameRecord, GameRecordSchema } from "../src/core/Schemas";
import { decompressGameRecord, toWireGameStartInfo } from "../src/core/Util";
import { createGameWireContext, encodeServerMessage } from "../src/core/ZbinWire";

const [bundle, file, tickArg] = process.argv.slice(2);
const TICKS = Number(tickArg ?? 50);

const raw = JSON.parse(fs.readFileSync(file, "utf8"));
const parsed = GameRecordSchema.safeParse(raw);
const record: GameRecord = decompressGameRecord(parsed.success ? parsed.data : (raw as GameRecord));
const info = record.info;
const gameStart = toWireGameStartInfo({ gameID: info.gameID, lobbyCreatedAt: info.lobbyCreatedAt,
  config: info.config, players: info.players, tribes: info.tribes });
const ctx = createGameWireContext(gameStart.players);
const meineID = info.players[0]?.clientID;
const rahmen: Uint8Array[] = [
  encodeServerMessage({ type: "start", gameStartInfo: gameStart, turns: [],
    lobbyCreatedAt: info.lobbyCreatedAt ?? 0, myClientID: meineID } as any, undefined),
];
for (const turn of record.turns.slice(0, TICKS)) {
  rahmen.push(encodeServerMessage({ type: "turn", turn } as any, ctx));
}

// ── nachgebautes Fenster ──
const anWorker: any[] = [];
let gesendetAufSocket = 0;
class GeruestWS extends EventTarget {
  static readonly OPEN = 1;
  readyState = 1;
  constructor(public url: string, _p?: unknown) { super(); }
  send(d: any) { gesendetAufSocket += d?.byteLength ?? d?.length ?? 0; }
  close() {}
}
class GeruestWorker {
  onmessage: ((e: any) => void) | null = null;
  constructor(public url: string) {}
  postMessage(m: any) { anWorker.push(m); }
  terminate() {}
}
const fenster: any = {
  WebSocket: GeruestWS, Worker: GeruestWorker,
  Blob: class { constructor(public teile: any[], public opt: any) {} },
  URL: { createObjectURL: () => "blob:probe", revokeObjectURL: () => {} },
  location: { href: "https://openfront.io/" },
  BOOTSTRAP_CONFIG: { cdnBase: "", assetManifest: {} },
  console, setInterval, clearInterval, setTimeout, clearTimeout,
  MessageEvent, ArrayBuffer, Uint8Array, Uint16Array, Float32Array, DataView,
  TextEncoder, TextDecoder, Math, JSON, Object, Array, Map, Set, Error, RegExp,
  Number, String, Symbol, Promise, BigInt, Date, performance, isNaN, parseInt, parseFloat,
};
fenster.window = fenster;
fenster.globalThis = fenster;
fenster.self = fenster;
// Der Kern baut URLs mit `new URL(u, location.href)`.
fenster.URL = Object.assign(function (u: string, b?: string) { return new URL(u, b); },
  { createObjectURL: () => "blob:probe", revokeObjectURL: () => {} });
const kontext = vm.createContext(fenster);
vm.runInContext(fs.readFileSync(bundle, "utf8"), kontext);

const kern = fenster.__KI_KERN__;
const uebernommen = fenster.WebSocket !== GeruestWS;
const ws = new fenster.WebSocket("wss://openfront.io/w3");
for (const b of rahmen) {
  ws.dispatchEvent(new MessageEvent("message", {
    data: b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength),
  }));
}
const start = anWorker.filter((m) => m.art === "start");
const zuege = anWorker.filter((m) => m.art === "turn");
// Senden ist aus: der Aufruf muss false liefern und den Socket in Ruhe lassen.
const ohneSchalter = kern?.sendeAbsicht({ art: "build_unit", unit: "City", tile: 1 });
const bytesNachVersuch = gesendetAufSocket;

process.stdout.write(JSON.stringify({
  socket_uebernommen: uebernommen,
  kern_vorhanden: typeof kern?.beobachtung === "function",
  worker_gestartet: start.length === 1,
  eigene_id_erkannt: start[0]?.meineID === meineID,
  partie: kern?.zustand()?.partie,
  spieler: kern?.zustand()?.spieler,
  fehler: kern?.zustand()?.fehler,
  zuege_an_spiegel: zuege.length,
  senden_ohne_schalter: ohneSchalter,
  bytes_auf_socket: bytesNachVersuch,
  ok: uebernommen && start.length === 1 && start[0]?.meineID === meineID
    && zuege.length === TICKS && ohneSchalter === false && bytesNachVersuch === 0
    && !kern?.zustand()?.fehler,
}, null, 1) + "\n");
process.exit(0);

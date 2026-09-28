/**
 * Prüft das gebündelte Erweiterungsskript ohne Browser.
 *
 * Es bekommt ein nachgebautes `window` mit einem WebSocket-Gerüst, dann werden ihm echte
 * Rahmen zugestellt (Startbrief und Züge aus einer Aufzeichnung, kodiert wie der Server
 * sie schickt). Kommt danach ein sinnvoller Bericht aus `kiStrom()`, funktioniert der
 * Weg „Socket abfangen → Rahmen entschlüsseln → Zugstrom zählen" in der Erweiterung.
 *
 *   npx tsx aitest/extprobe.ts <bundle.js> <record.json> [ticks]
 */
import fs from "fs";
import vm from "vm";
import { GameRecord, GameRecordSchema } from "../src/core/Schemas";
import { decompressGameRecord, toWireGameStartInfo } from "../src/core/Util";
import { createGameWireContext, encodeServerMessage } from "../src/core/ZbinWire";

const [bundle, file, tickArg] = process.argv.slice(2);
const TICKS = Number(tickArg ?? 200);

const raw = JSON.parse(fs.readFileSync(file, "utf8"));
const parsed = GameRecordSchema.safeParse(raw);
const record: GameRecord = decompressGameRecord(parsed.success ? parsed.data : (raw as GameRecord));
const info = record.info;
const gameStart = toWireGameStartInfo({ gameID: info.gameID, lobbyCreatedAt: info.lobbyCreatedAt,
  config: info.config, players: info.players, tribes: info.tribes });
const ctx = createGameWireContext(gameStart.players);
const rahmen: Uint8Array[] = [
  encodeServerMessage({ type: "start", gameStartInfo: gameStart, turns: [],
    lobbyCreatedAt: info.lobbyCreatedAt ?? 0 } as any, undefined),
];
for (const turn of record.turns.slice(0, TICKS)) {
  rahmen.push(encodeServerMessage({ type: "turn", turn } as any, ctx));
}

// Nachgebautes Fenster
class GeruestWS extends EventTarget {
  static readonly CLOSED = 3;
  gesendet = 0;
  constructor(public url: string, _p?: unknown) { super(); }
  send(d: any) { this.gesendet += d?.byteLength ?? 0; }
  close() {}
}
const fenster: any = { WebSocket: GeruestWS, setInterval, clearInterval, console };
fenster.window = fenster;
fenster.globalThis = fenster;
fenster.MessageEvent = MessageEvent;
fenster.ArrayBuffer = ArrayBuffer;
fenster.Blob = Blob;
fenster.Uint8Array = Uint8Array;
fenster.Date = Date;
fenster.TextEncoder = TextEncoder;
fenster.TextDecoder = TextDecoder;
fenster.DataView = DataView;
fenster.Math = Math;
fenster.JSON = JSON;
fenster.Object = Object;
fenster.Array = Array;
fenster.Map = Map;
fenster.Set = Set;
fenster.Error = Error;
fenster.RegExp = RegExp;
fenster.Number = Number;
fenster.String = String;
fenster.Symbol = Symbol;
fenster.Promise = Promise;
fenster.BigInt = BigInt;
fenster.performance = performance;
const kontext = vm.createContext(fenster);
vm.runInContext(fs.readFileSync(bundle, "utf8"), kontext);

const Gehaengt = fenster.WebSocket;
const gehaengt = Gehaengt !== GeruestWS;
const ws = new Gehaengt("wss://openfront.io/w3");
for (const b of rahmen) {
  ws.dispatchEvent(new MessageEvent("message", { data: b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength) }));
}
const bericht = fenster.kiStrom();
process.stdout.write(JSON.stringify({
  socket_uebernommen: gehaengt,
  kiStrom_vorhanden: typeof fenster.kiStrom === "function",
  bericht,
  ok: gehaengt && bericht?.partie?.gameID === info.gameID
    && (bericht?.nachrichten?.turn?.anzahl ?? 0) === TICKS
    && !(bericht?.nachrichten?.["NICHT DEKODIERT"]),
}, null, 1) + "\n");
process.exit(0);

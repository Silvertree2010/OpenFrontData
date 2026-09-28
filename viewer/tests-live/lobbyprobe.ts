/**
 * Kleine Probe: erreicht dieser Stand die oeffentliche Lobby-Liste des echten Servers,
 * und laesst sie sich mit UNSEREM Bundle dekodieren? Genau eine Verbindung, eine
 * Nachricht, dann zu. Es wird nichts betreten und nichts gesendet ausser dem
 * WebSocket-Handshake — dieselbe Verbindung, die die Startseite jedes Clients aufmacht.
 *
 *   npx tsx aitest/lobbyprobe.ts [host]
 */
import WebSocket from "ws";
import { decodeLobbyMessage } from "../src/core/ZbinWire";

const host = process.argv[2] ?? "openfront.io";
const w = Math.floor(Math.random() * 20);
const url = `wss://${host}/w${w}/lobbies`;
const ws = new WebSocket(url);
const ende = setTimeout(() => {
  console.log(JSON.stringify({ ok: false, url, grund: "keine Nachricht in 20 s" }));
  ws.close();
  process.exit(1);
}, 20000);

ws.on("open", () => console.error(`[probe] offen: ${url}`));
ws.on("message", (daten: Buffer) => {
  clearTimeout(ende);
  try {
    const m: any = decodeLobbyMessage(new Uint8Array(daten));
    const spiele = (m?.lobbies ?? m?.games ?? []).map((g: any) => ({
      id: g.gameID, karte: g.gameConfig?.gameMap, modus: g.gameConfig?.gameMode,
      spieler: g.numClients ?? g.clients, start: g.msUntilStart,
    }));
    console.log(JSON.stringify({ ok: true, url, felder: Object.keys(m ?? {}),
      anzahl: spiele.length, spiele: spiele.slice(0, 4) }));
  } catch (e: any) {
    console.log(JSON.stringify({ ok: false, url, grund: `dekodieren: ${e?.message}` }));
  }
  ws.close();
  process.exit(0);
});
ws.on("error", (e: any) => {
  clearTimeout(ende);
  console.log(JSON.stringify({ ok: false, url, grund: String(e?.message ?? e) }));
  process.exit(1);
});

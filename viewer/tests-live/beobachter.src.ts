/**
 * Machbarkeitstest: kann eine Browser-Erweiterung auf openfront.io den Zugstrom des
 * echten Clients mitlesen?
 *
 * Dieses Skript läuft in der Seite selbst (MAIN world, document_start), legt sich vor
 * `window.WebSocket` und liest mit, was der Server schickt. Entschlüsselt wird mit dem
 * Wire-Format des Spiels selbst (src/core/ZbinWire.ts, Stand v0.33.14) — dieselbe
 * Funktion, die der ausgelieferte Client benutzt.
 *
 * WAS ES NICHT TUT — und das ist die Grenze dieses Tests:
 *   · Es sendet NICHTS. `send` wird nicht überschrieben, nichts eingeschleust,
 *     keine Absicht erzeugt. Reines Zuhören.
 *   · Es fasst Turnstile, Cloudflare und die Anmeldung nicht an. Der Nutzer meldet
 *     sich an und spielt wie immer; die Erweiterung sieht nur zu.
 *   · Es schickt nichts nach draussen. Alles bleibt in der Konsole des Fensters.
 *
 * Ergebnis abrufen: in der Konsole `kiStrom()` aufrufen. Alle 5 s kommt ausserdem eine
 * Zeile mit den Zählern.
 */
import { createGameWireContext, decodeServerMessage } from "../src/core/ZbinWire";

interface Zaehler {
  anzahl: number;
  bytes: number;
}

const start = Date.now();
const zaehler = new Map<string, Zaehler>();
const proSekunde: number[] = [];
let partie: any = null;
let ersterTick: number | null = null;
let letzterTick: number | null = null;
let intentsGesamt = 0;
let intentArten = new Map<string, number>();
let fehler: string[] = [];
let sockets = 0;
let gesendetBytes = 0;   // nur gezählt, nicht verändert

function zaehl(art: string, bytes: number): void {
  const z = zaehler.get(art) ?? { anzahl: 0, bytes: 0 };
  z.anzahl++;
  z.bytes += bytes;
  zaehler.set(art, z);
}

function bericht(): any {
  const sek = Math.max(1, (Date.now() - start) / 1000);
  const arten: Record<string, any> = {};
  for (const [k, v] of [...zaehler.entries()].sort((a, b) => b[1].anzahl - a[1].anzahl)) {
    arten[k] = { anzahl: v.anzahl, kb: +(v.bytes / 1024).toFixed(1),
      byte_je_nachricht: Math.round(v.bytes / v.anzahl), je_sekunde: +(v.anzahl / sek).toFixed(2) };
  }
  return {
    laeuft_seit_s: Math.round(sek),
    sockets,
    partie: partie && { gameID: partie.gameID, karte: partie.config?.gameMap,
      modus: partie.config?.gameMode, spieler: partie.players?.length,
      eigene_clientID: partie.eigene ?? null },
    ticks: ersterTick === null ? null : { erster: ersterTick, letzter: letzterTick,
      je_sekunde: +(((letzterTick! - ersterTick!) || 0) / sek).toFixed(2) },
    intents_gesamt: intentsGesamt,
    intent_arten: Object.fromEntries([...intentArten.entries()].sort((a, b) => b[1] - a[1])),
    nachrichten: arten,
    gesendet_bytes_vom_echten_client: gesendetBytes,
    fehler: fehler.slice(0, 5),
    hinweis: "Nur mitgelesen. Diese Erweiterung sendet nichts.",
  };
}

(globalThis as any).kiStrom = () => {
  const b = bericht();
  console.log("[ki-beobachter]", b);
  return b;
};

const Echt = window.WebSocket;

class BeobachteterSocket extends Echt {
  private ctx: any = undefined;
  constructor(url: string | URL, protokolle?: string | string[]) {
    super(url as any, protokolle as any);
    const u = String(url);
    sockets++;
    console.info(`[ki-beobachter] Socket offen: ${u}`);
    this.addEventListener("message", (ev: MessageEvent) => {
      try {
        const roh = ev.data;
        if (!(roh instanceof ArrayBuffer) && !(roh instanceof Blob)) {
          zaehl(`text(${typeof roh})`, String(roh).length);
          return;
        }
        if (roh instanceof Blob) {
          // Der Client selbst stellt binaryType auf arraybuffer; wenn nicht, nachziehen.
          zaehl("blob (binaryType nicht arraybuffer)", roh.size);
          return;
        }
        const bytes = new Uint8Array(roh);
        if (/\/lobbies$/.test(u)) { zaehl("lobby-liste", bytes.length); return; }
        let m: any;
        try {
          m = decodeServerMessage(bytes, this.ctx);
        } catch (e: any) {
          zaehl("NICHT DEKODIERT", bytes.length);
          if (fehler.length < 5) fehler.push(String(e?.message ?? e).slice(0, 200));
          return;
        }
        zaehl(m.type, bytes.length);
        if (m.type === "start") {
          // Der Startbrief trägt die Spielerliste — daraus baut sich die Tabelle, mit
          // der alle weiteren Rahmen dekodiert werden. Genau wie im echten Client.
          partie = { gameID: m.gameStartInfo?.gameID, config: m.gameStartInfo?.config,
            players: m.gameStartInfo?.players, eigene: (window as any).__KI_CLIENT_ID__ ?? null };
          this.ctx = createGameWireContext(m.gameStartInfo?.players ?? []);
          console.info("[ki-beobachter] Partie erkannt:", partie.gameID, partie.config?.gameMap,
            `${partie.players?.length} Spieler`);
        } else if (m.type === "turn") {
          const t = m.turn;
          if (ersterTick === null) ersterTick = t.turnNumber;
          letzterTick = t.turnNumber;
          const n = t.intents?.length ?? 0;
          intentsGesamt += n;
          for (const i of t.intents ?? []) {
            intentArten.set(i.type, (intentArten.get(i.type) ?? 0) + 1);
          }
          proSekunde.push(Date.now());
        }
      } catch (e: any) {
        if (fehler.length < 5) fehler.push("lesen: " + String(e?.message ?? e).slice(0, 160));
      }
    });
  }
  // send bleibt ausdrücklich unverändert: nur zählen, nie eingreifen.
  send(daten: any): void {
    try {
      gesendetBytes += daten?.byteLength ?? daten?.length ?? 0;
    } catch { /* egal */ }
    super.send(daten);
  }
}

(window as any).WebSocket = BeobachteterSocket;
console.info("[ki-beobachter] aktiv — nur mitlesen, `kiStrom()` zeigt die Zähler");
setInterval(() => {
  if (zaehler.size > 0) console.log("[ki-beobachter]", bericht());
}, 5000);

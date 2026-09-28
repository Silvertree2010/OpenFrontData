/**
 * Der Kern der Erweiterung: läuft in der Seite (MAIN world, document_start).
 *
 * Er legt sich vor `window.WebSocket`, liest die Rahmen des Spielservers mit, führt im
 * Worker (`engineSpiegel.ts`) dieselbe Partie mit und hält die Beobachtung bereit.
 * Nach aussen erfüllt er `schnittstelle.ts` und legt sich unter `globalThis.__KI_KERN__`
 * ab; `politik.ts` und `einblendung.ts` reden nur darüber mit ihm.
 *
 * Zum Senden:
 *   · `SENDEN_AN` in schnittstelle.ts ist **false** und bleibt es.
 *   · Scharf wird nur, wer im Fenster ausdrücklich `__KI_SENDEN__ = true` setzt.
 *     Ohne das gibt `sendeAbsicht` false zurück und rührt den Socket nicht an.
 *   · An Turnstile, Cloudflare und der Anmeldung wird nichts verändert. Der Nutzer
 *     meldet sich selbst an, betritt selbst die Partie und setzt seinen Startpunkt.
 */
import {
  createGameWireContext,
  decodeServerMessage,
  encodeClientMessage,
} from "../src/core/ZbinWire";
import type { Absicht, Beobachtung, Kern } from "./schnittstelle";
import { SENDEN_AN } from "./schnittstelle";
import { WORKER_QUELLE } from "./workerQuelle.gen";

const g = globalThis as any;

/** Nur der Spielsocket, nicht die Lobby-Liste. */
const SPIEL_SOCKET = /\/w\d+$/;

let socket: WebSocket | null = null;
let ctx: any = undefined;
let worker: Worker | null = null;
let partie: string | null = null;
let meineID = "";
let tick = -1;
let lebe = false;
let startphase = true;
let spielerZahl = 0;
let fehler: string | null = null;
let letzte: Beobachtung | null = null;
let anfrageLaeuft = false;
let takt = 32;
const aufTickHandler: ((t: number) => void)[] = [];

function log(...x: unknown[]): void {
  console.info("[ki-kern]", ...x);
}

function starteWorker(gameStartInfo: any): void {
  if (worker) return;
  // Der Worker kommt aus einem Blob, nicht aus einer Datei: ein Skript in der Seite hat
  // keinen Zugriff auf die Dateien der Erweiterung.
  const blob = new Blob([WORKER_QUELLE], { type: "text/javascript" });
  worker = new Worker(URL.createObjectURL(blob));
  worker.onmessage = (ev: MessageEvent) => {
    const m = ev.data;
    switch (m?.art) {
      case "tick":
        tick = m.tick;
        lebe = !!m.lebe;
        startphase = !!m.startphase;
        for (const f of aufTickHandler) {
          try { f(tick); } catch { /* Rückruf darf den Kern nicht kippen */ }
        }
        // Beobachtung im Takt nachziehen, nie mehr als eine gleichzeitig.
        if (!startphase && lebe && !anfrageLaeuft && tick % takt === 0) {
          anfrageLaeuft = true;
          worker!.postMessage({ art: "beobachte" });
        }
        break;
      case "beobachtung":
        anfrageLaeuft = false;
        if (m.b) letzte = m.b as Beobachtung;
        break;
      case "bereit":
        log("Spiegel bereit, Karte", m.karte);
        break;
      case "fehler":
        fehler = String(m.text).slice(0, 300);
        console.warn("[ki-kern] Spiegel:", fehler);
        break;
    }
  };
  const bc = g.BOOTSTRAP_CONFIG ?? {};
  worker.postMessage({
    art: "start",
    gameStartInfo,
    meineID,
    cdnBase: bc.cdnBase ?? "",
    assetManifest: bc.assetManifest ?? {},
    takt,
  });
}

function lies(bytes: Uint8Array): void {
  let m: any;
  try {
    m = decodeServerMessage(bytes, ctx);
  } catch (e: any) {
    fehler = "Rahmen nicht lesbar: " + String(e?.message ?? e).slice(0, 160);
    return;
  }
  switch (m.type) {
    case "start": {
      const info = m.gameStartInfo;
      partie = info?.gameID ?? null;
      spielerZahl = info?.players?.length ?? 0;
      // Die eigene Kennung schickt der Server im Startbrief mit; ohne sie wüsste der
      // Spiegel nicht, welches Volk unseres ist.
      meineID = m.myClientID ?? "";
      ctx = createGameWireContext(info?.players ?? []);
      log(`Partie ${partie}, ${spielerZahl} Spieler, eigene Kennung ${meineID || "unbekannt"}`);
      starteWorker(info);
      // Nachgereichte Züge aus dem Startbrief (Spätbeitritt) zuerst.
      for (const t of m.turns ?? []) worker?.postMessage({ art: "turn", turn: t });
      break;
    }
    case "turn":
      worker?.postMessage({ art: "turn", turn: m.turn });
      break;
    case "desync":
      fehler = "Server meldet desync";
      break;
  }
}

class KiSocket extends (globalThis.WebSocket as typeof WebSocket) {
  constructor(url: string | URL, protokolle?: string | string[]) {
    super(url as any, protokolle as any);
    const u = String(url);
    if (!SPIEL_SOCKET.test(new URL(u, location.href).pathname)) return;
    socket = this as unknown as WebSocket;
    log("Spielsocket:", u);
    this.addEventListener("message", (ev: MessageEvent) => {
      const d = ev.data;
      if (d instanceof ArrayBuffer) lies(new Uint8Array(d));
    });
    this.addEventListener("close", () => {
      if (socket === (this as unknown as WebSocket)) socket = null;
    });
  }
}

const kern: Kern = {
  beobachtung(): Beobachtung | null {
    return letzte;
  },
  sendeAbsicht(a: Absicht): boolean {
    // Zwei Schalter, beide müssen zeigen: der Vertrag (SENDEN_AN) und die ausdrückliche
    // Einstellung im Fenster. Standard ist aus.
    if (!SENDEN_AN && g.__KI_SENDEN__ !== true) return false;
    if (!socket || socket.readyState !== WebSocket.OPEN || !ctx) return false;
    if (!a || typeof a.art !== "string") return false;
    try {
      const { art, ...rest } = a;
      const intent: any = { ...rest, type: art };
      if (intent.clientID === undefined && meineID) intent.clientID = meineID;
      socket.send(encodeClientMessage({ type: "intent", intent } as any, ctx));
      return true;
    } catch (e: any) {
      fehler = "senden: " + String(e?.message ?? e).slice(0, 160);
      return false;
    }
  },
  aufTick(f: (t: number) => void): void {
    aufTickHandler.push(f);
  },
  zustand() {
    return { partie, tick, spieler: spielerZahl, fehler };
  },
};

g.WebSocket = KiSocket;
g.__KI_KERN__ = kern;
g.__KI_TAKT__ = (n: number) => { if (n > 0) takt = n | 0; return takt; };
log("aktiv — nur mitlesen; Senden erst mit __KI_SENDEN__ = true");

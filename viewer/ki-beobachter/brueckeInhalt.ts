/**
 * Inhaltsskript im ISOLIERTEN Kontext: der einzige Teil, der `chrome.runtime` sieht.
 * Er nimmt die Fragen der Seite entgegen und reicht sie an den Hintergrunddienst weiter.
 *
 * Er nimmt nur Nachrichten aus DEMSELBEN Fenster an und nur die eine Art (`ki-bruecke-frage`).
 * Er liest nichts aus der Seite und schreibt nichts hinein ausser der Antwort.
 */
import { BRUECKE_ANTWORT, BRUECKE_DA, BRUECKE_FRAGE, BRUECKE_PING } from "./bruecke";

const c: any = (globalThis as any).chrome;

function melde(): void {
  window.postMessage({ art: BRUECKE_DA }, "*");
}

window.addEventListener("message", (ev: MessageEvent) => {
  const m: any = ev.data;
  if (ev.source !== window) return;
  if (m?.art === BRUECKE_PING) {
    melde();
    return;
  }
  if (m?.art !== BRUECKE_FRAGE) return;
  if (!c?.runtime?.sendMessage) {
    window.postMessage({ art: BRUECKE_ANTWORT, nr: m.nr, fehler: "kein chrome.runtime" }, "*");
    return;
  }
  c.runtime.sendMessage(
    { art: "hole", url: m.url, methode: m.methode, kopf: m.kopf, koerper: m.koerper },
    (antwort: any) => {
      const f = c.runtime.lastError?.message;
      window.postMessage(
        f
          ? { art: BRUECKE_ANTWORT, nr: m.nr, fehler: f }
          : { art: BRUECKE_ANTWORT, nr: m.nr, ...(antwort ?? { fehler: "leere Antwort" }) },
        "*",
      );
    },
  );
});

// Zeichen für die Seite, dass die Brücke steht. **Kein Inline-Skript**: die
// Content-Security-Policy von openfront.io verbietet das, ein eingehängtes
// <script> läuft dort nie (so gesehen am 12.09.2026). Eine Nachricht genügt —
// und weil die Reihenfolge nicht feststeht, wird sie mehrfach geschickt und
// auf Klopfen wiederholt.
melde();
document.addEventListener("DOMContentLoaded", melde);
for (const ms of [0, 50, 250, 1000]) setTimeout(melde, ms);

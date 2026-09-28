/**
 * Einstieg der Erweiterung in der Seite (MAIN world, document_start).
 *
 * Reihenfolge zählt: erst der Kern — er muss vor dem Bundle der Seite an
 * `window.WebSocket` —, dann die Politik mit der Einblendung. Die Politik holt über die
 * Brücke (siehe `bruecke.ts`), weil eine https-Seite kein http laden darf.
 *
 * Standard ist Zuschauen: `sendeAbsicht` bleibt verriegelt, bis im Fenster ausdrücklich
 * `__KI_SENDEN__ = true` gesetzt wird. An Turnstile, Anmeldung und Cloudflare rührt
 * nichts davon.
 */
import "./kern";
import { brueckeHolen } from "./bruecke";
import { starteMitKern } from "./einblendung";

const g = globalThis as Record<string, any>;

/** Adresse des Inferenz-Servers: Adresszeile, dann Speicher, dann Vorgabe. */
function infAdresse(): string {
  try {
    const ausUrl = new URLSearchParams(location.search).get("inf");
    if (ausUrl) return ausUrl;
    const gemerkt = localStorage.getItem("ki-inf");
    if (gemerkt) return gemerkt;
  } catch {
    /* ohne Speicher: Vorgabe */
  }
  return "http://arch.example:8650";
}

const inf = infAdresse();
g.__KI_INF__ = inf;
starteMitKern({ inf, holen: brueckeHolen });
console.info(`[ki] Erweiterung geladen. Inferenz: ${inf} (über die Brücke, wenn http)`);

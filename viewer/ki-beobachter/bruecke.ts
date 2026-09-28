/**
 * Die Brücke zum Inferenz-Server — und die Antwort auf das einzige echte Hindernis.
 *
 * openfront.io läuft über https, der Inferenz-Server über http. Ein Skript IN der Seite
 * darf das nicht holen: aktiver Mischinhalt, der Browser blockt. Zwei saubere Wege gäbe
 * es, keiner rührt an fremden Sicherheitsmechanismen:
 *
 *   a) über den Hintergrunddienst der Erweiterung. Der ist keine Seite, für ihn gilt die
 *      Mischinhalt-Regel nicht; er holt mit `host_permissions` und reicht durch.
 *   b) dem Server per `tailscale serve` eine https-Adresse geben.
 *
 * Gewählt ist **a**, aus drei Gründen: die Erweiterung trägt alles selbst, es muss auf
 * arch nichts eingerichtet bleiben (und an der Tailscale-Einrichtung des Nutzers wird
 * ungefragt nichts gedreht), und es hängt nicht an MagicDNS oder einem Zertifikat, das
 * ablaufen kann. Weg b bleibt offen: dafür genügt es, `inf` auf die https-Adresse zu
 * zeigen — dann geht die Anfrage direkt, ohne Brücke.
 *
 * Der Weg einer Anfrage:
 *   Politik (Seite, MAIN) → `window.postMessage` → Inhaltsskript (ISOLATED, hat
 *   `chrome.runtime`) → `chrome.runtime.sendMessage` → Hintergrunddienst → `fetch` →
 *   zurück denselben Weg.
 *
 * Es geht ausschliesslich an den eingestellten Inferenz-Server. Nichts davon berührt
 * openfront.io, seine Anmeldung oder Cloudflare.
 */

export const BRUECKE_FRAGE = "ki-bruecke-frage";
export const BRUECKE_ANTWORT = "ki-bruecke-antwort";
/** Die Brücke meldet sich mit dieser Nachricht, wenn sie steht. */
export const BRUECKE_DA = "ki-bruecke-da";
/** Und antwortet darauf noch einmal, falls die Seite später dran war. */
export const BRUECKE_PING = "ki-bruecke-ping";

let laufendeNr = 0;
let daGemeldet = false;

// Warum kein Merkzeichen im Fenster: das Inhaltsskript darf auf openfront.io kein
// Inline-Skript einhängen — die Content-Security-Policy der Seite verbietet es, das
// Skript läuft nie, und die Seite hielte die Brücke für abwesend (so gesehen am
// 12.09.2026). `window.postMessage` ist davon nicht betroffen.
globalThis.addEventListener?.("message", (ev: MessageEvent) => {
  if (ev.source !== globalThis) return;
  if ((ev.data as Record<string, unknown>)?.art === BRUECKE_DA) daGemeldet = true;
});

/** Fragt nach, ob die Brücke steht. Sie antwortet mit BRUECKE_DA. */
function klopfe(): void {
  try {
    globalThis.postMessage({ art: BRUECKE_PING }, "*");
  } catch {
    /* ohne postMessage gibt es ohnehin keine Brücke */
  }
}
klopfe();

/**
 * Wartet, bis sich die Brücke gemeldet hat. Beide Reihenfolgen sind abgedeckt: meldet
 * sie sich von selbst (sie war zuerst da), reicht die Meldung; war die Seite zuerst,
 * antwortet sie auf das Klopfen.
 */
export function warteAufBruecke(fristMs = 5000): Promise<void> {
  if (daGemeldet) return Promise.resolve();
  return new Promise((fertig, schiefgegangen) => {
    const bis = Date.now() + fristMs;
    const takt = setInterval(() => {
      if (daGemeldet) {
        clearInterval(takt);
        fertig();
        return;
      }
      if (Date.now() >= bis) {
        clearInterval(takt);
        schiefgegangen(new Error(
          "Brücke antwortet nicht — läuft das Inhaltsskript der Erweiterung? " +
          "(Erweiterung neu laden, dann die Seite neu laden)"));
        return;
      }
      klopfe();
    }, 250);
  });
}

/**
 * Ein `fetch`-Ersatz für die Politik. Gleiche Aufrufform, gleiche Antwortform —
 * nur der Weg ist ein anderer.
 */
export function brueckeHolen(
  eingabe: RequestInfo | URL,
  init?: RequestInit,
): Promise<Response> {
  const url = typeof eingabe === "string" ? eingabe : String((eingabe as URL).href ?? eingabe);
  // https bleibt direkt: dann braucht es die Brücke nicht (Weg b).
  if (/^https:/i.test(url)) return globalThis.fetch(eingabe as RequestInfo, init);

  const nr = ++laufendeNr;
  return warteAufBruecke().then(() => new Promise<Response>((fertig, schiefgegangen) => {
    const zeit = setTimeout(() => {
      globalThis.removeEventListener("message", hoeren);
      schiefgegangen(new Error("Brücke: keine Antwort in 20 s"));
    }, 20000);

    const hoeren = (ev: MessageEvent) => {
      const m = ev.data;
      if (ev.source !== globalThis || m?.art !== BRUECKE_ANTWORT || m.nr !== nr) return;
      clearTimeout(zeit);
      globalThis.removeEventListener("message", hoeren);
      if (m.fehler) {
        schiefgegangen(new Error(String(m.fehler).slice(0, 200)));
        return;
      }
      fertig(new Response(m.text ?? "", { status: m.status ?? 200,
        headers: { "Content-Type": "application/json" } }));
    };
    globalThis.addEventListener("message", hoeren);
    globalThis.postMessage({
      art: BRUECKE_FRAGE, nr, url,
      methode: init?.method ?? "GET",
      kopf: (init?.headers as Record<string, string>) ?? {},
      koerper: typeof init?.body === "string" ? init.body : undefined,
    }, "*");
  }));
}

/** Hat sich die Brücke gemeldet? */
export function brueckeDa(): boolean {
  return daGemeldet;
}

/**
 * Einblendung unten links: klein, wegklappbar, und sie verdeckt die normale Oberfläche
 * nicht. Portiert aus `kiEinblendung.ts` und `oeffentlicheLobby.ts` des Live-Clients
 * (`viewer/ai-live-v33.patch`).
 *
 * Sicherheit: fremde Texte (Spielernamen, Partie-ID, Fehlertexte des Servers, Protokoll)
 * kommen ausschliesslich über `textContent` bzw. `document.createTextNode` ins Dokument,
 * nie über `innerHTML`. Dazu gab es im Viewer schon einen Fund — der Stil bleibt.
 *
 * Zuschauen ist der Standard: der Schalter „Senden“ steht auf aus und lässt sich nur
 * umlegen, wenn `SENDEN_AN` wahr ist und die Erlaubnis noch gilt. Warum nicht, steht
 * im Klartext daneben.
 */
import type { Kern } from "./schnittstelle.ts";
import type { Messwerte, PolitikOpts } from "./politik.ts";
import { erlaubnisEnde, ERLAUBNIS, Politik } from "./politik.ts";

const KLAPP_SCHLUESSEL = "ki-einblendung-offen";

const FARBE = {
  gruen: "#63d19e",
  grau: "#8b93a3",
  hell: "#e7e9ee",
  rot: "#e0574f",
  gelb: "#d9a441",
};

/** Ein Feld „Beschriftung Wert“ als reiner Text — nie innerHTML, die Werte sind fremd. */
export function feld(beschriftung: string, wert: string): HTMLElement {
  const span = document.createElement("span");
  span.append(document.createTextNode(beschriftung + " "));
  const b = document.createElement("b");
  b.style.color = FARBE.hell;
  b.textContent = wert;
  span.append(b);
  return span;
}

function knopf(text: string, klick: () => void): HTMLButtonElement {
  const k = document.createElement("button");
  k.textContent = text;
  k.style.cssText =
    "background:#232a36;color:#e7e9ee;border:1px solid #3a4252;border-radius:7px;" +
    "padding:3px 9px;font:600 12px/1.2 system-ui,sans-serif;cursor:pointer";
  k.onclick = klick;
  return k;
}

function zahl(x: unknown, n = 0): string {
  const v = Number(x);
  return Number.isFinite(v) ? v.toFixed(n) : "—";
}

function dauer(sek: number): string {
  const s = Math.max(0, Math.round(sek));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

export interface Einblendung {
  wurzel: HTMLElement;
  zeige(m: Messwerte): void;
  entferne(): void;
}

/**
 * Baut die Einblendung und hängt sie an `wohin` (Standard: document.body).
 * Die Politik wird nur über ihre öffentlichen Methoden bedient.
 */
export function baueEinblendung(
  politik: Politik,
  wohin?: HTMLElement,
): Einblendung {
  const kasten = document.createElement("div");
  kasten.id = "ki-einblendung";
  kasten.style.cssText =
    "position:fixed;left:8px;bottom:8px;z-index:2147483000;max-width:min(46vw,560px);" +
    "background:rgba(12,15,20,.88);border:1px solid #3a4252;border-radius:10px;" +
    "color:#e7e9ee;font:12px/1.4 system-ui,sans-serif;padding:5px 8px;" +
    "box-shadow:0 2px 10px rgba(0,0,0,.45)";

  // Kopf mit Lasche zum Zuklappen
  const kopf = document.createElement("div");
  kopf.style.cssText = "display:flex;gap:8px;align-items:center;cursor:pointer";
  const lasche = document.createElement("span");
  lasche.textContent = "KI";
  lasche.style.cssText = "font-weight:700;color:" + FARBE.gruen;
  const zustand = document.createElement("span");
  zustand.style.color = FARBE.grau;
  zustand.textContent = "zuschauen";
  const pfeil = document.createElement("span");
  pfeil.style.color = FARBE.grau;
  kopf.append(lasche, zustand, pfeil);

  const koerper = document.createElement("div");

  // Zeile 1: Kennzahlen
  const werte = document.createElement("div");
  werte.style.cssText =
    "display:flex;gap:10px;flex-wrap:wrap;color:" +
    FARBE.grau +
    ";font-variant-numeric:tabular-nums";
  werte.textContent = "wartet auf den Start …";

  // Zeile 2: Knöpfe
  const leiste = document.createElement("div");
  leiste.style.cssText =
    "display:flex;gap:8px;align-items:center;flex-wrap:wrap;padding-top:4px;margin-top:4px;" +
    "border-top:1px solid #3a4252";
  const kAnhalten = knopf("KI anhalten", () => {
    if (politik.istAngehalten()) politik.weiter();
    else politik.anhalten();
    zeichneKnoepfe();
  });
  const kSenden = knopf("Senden: aus", () => {
    politik.setzeSenden(!politik.sendetGerade());
    zeichneKnoepfe();
  });
  const kProtokoll = knopf("Protokoll zeigen", () => zeigeProtokoll());
  leiste.append(kAnhalten, kSenden, kProtokoll);

  // Zeile 3: Erlaubnis und Ablauf
  const grenze = document.createElement("div");
  grenze.style.cssText = "color:" + FARBE.grau + ";padding-top:3px";
  grenze.textContent =
    `Erlaubnis: ${ERLAUBNIS.von}, ${ERLAUBNIS.am} — gültig bis ` +
    erlaubnisEnde().toLocaleString("de-CH");

  const hinweis = document.createElement("div");
  hinweis.style.cssText = "color:" + FARBE.gelb + ";padding-top:3px;display:none";

  // Protokoll-Ausklapp
  const protokoll = document.createElement("pre");
  protokoll.style.cssText =
    "display:none;max-height:38vh;max-width:min(46vw,560px);overflow:auto;margin:6px 0 0;" +
    "white-space:pre-wrap;word-break:break-all;color:" +
    FARBE.grau +
    ";font:11px/1.35 ui-monospace,monospace";

  koerper.append(werte, leiste, grenze, hinweis, protokoll);
  kasten.append(kopf, koerper);
  (wohin ?? document.body).appendChild(kasten);

  function zeigeProtokoll(): void {
    const offen = protokoll.style.display !== "none";
    protokoll.style.display = offen ? "none" : "block";
    kProtokoll.textContent = offen ? "Protokoll zeigen" : "Protokoll ausblenden";
    // Fremder Text (Partie-IDs, Ergebnisse) — nur textContent.
    if (!offen) protokoll.textContent = politik.protokoll();
  }

  function zeichneKnoepfe(): void {
    const angehalten = politik.istAngehalten();
    kAnhalten.textContent = angehalten ? "KI weiterlaufen" : "KI anhalten";
    const grund = politik.darfSenden();
    const sendet = politik.sendetGerade();
    kSenden.textContent = sendet ? "Senden: EIN" : "Senden: aus";
    kSenden.style.color = sendet ? FARBE.gruen : FARBE.hell;
    kSenden.disabled = grund !== null;
    kSenden.style.opacity = grund !== null ? "0.5" : "1";
    kSenden.style.cursor = grund !== null ? "not-allowed" : "pointer";
    if (grund !== null) {
      hinweis.style.display = "block";
      hinweis.style.color = FARBE.rot;
      hinweis.textContent = grund; // eigener Text, trotzdem textContent
    } else {
      hinweis.style.display = sendet ? "none" : "block";
      hinweis.style.color = FARBE.grau;
      hinweis.textContent =
        "Zuschauen ist der Standard. Gesendet wird erst, wenn du „Senden“ einschaltest.";
    }
    zustand.textContent = angehalten
      ? "angehalten"
      : sendet
        ? "spielt mit"
        : "zuschauen";
    zustand.style.color = sendet ? FARBE.gruen : FARBE.grau;
  }

  // Klappzustand merken
  let offen = true;
  try {
    const g = globalThis.localStorage?.getItem(KLAPP_SCHLUESSEL);
    if (g !== null && g !== undefined) offen = g === "1";
  } catch {
    /* ohne localStorage bleibt es offen */
  }
  const zeichneKlapp = () => {
    koerper.style.display = offen ? "block" : "none";
    pfeil.textContent = offen ? "▾ zuklappen" : "▸ aufklappen";
  };
  kopf.onclick = () => {
    offen = !offen;
    try {
      globalThis.localStorage?.setItem(KLAPP_SCHLUESSEL, offen ? "1" : "0");
    } catch {
      /* egal */
    }
    zeichneKlapp();
  };
  zeichneKlapp();
  zeichneKnoepfe();

  function zeige(m: Messwerte): void {
    werte.replaceChildren(
      feld("Tick", zahl(m.tick)),
      feld("Gebiet", zahl(m.gebiet * 100, 2) + " %"),
      feld("Platz", zahl(m.platz) + "/" + zahl(m.lebende)),
      feld("Truppen", zahl(m.truppen / 1000, 1) + "k"),
      feld("Handl./1000", zahl(m.handlungen_je_1000, 1)),
      feld("P(handeln)", m.p_handeln === null ? "—" : zahl(m.p_handeln, 3)),
      feld("Aktion", String(m.letzte_aktion ?? "—").slice(0, 40)),
      feld("Dauer", dauer(m.sekunden)),
    );
    if (m.fehler) {
      hinweis.style.display = "block";
      hinweis.style.color = FARBE.rot;
      hinweis.textContent = String(m.fehler).slice(0, 300);
    }
    zeichneKnoepfe();
    if (protokoll.style.display !== "none") {
      protokoll.textContent = politik.protokoll();
    }
  }

  // Der Kern taktet zehnmal je Sekunde; das braucht niemand im Bild zu sehen.
  let letzte = 0;
  politik.aufMesswerte((m) => {
    const jetzt = Date.now();
    if (jetzt - letzte < 400) return;
    letzte = jetzt;
    zeige(m);
  });
  return { wurzel: kasten, zeige, entferne: () => kasten.remove() };
}

/**
 * Einblendung anhängen und `kiProtokoll()` in der Konsole bereitstellen.
 * Aufgerufen vom Bündel der Erweiterung, sobald Kern und Politik stehen.
 */
export function installiere(politik: Politik, wohin?: HTMLElement): Einblendung {
  const e = baueEinblendung(politik, wohin);
  (globalThis as Record<string, unknown>).kiProtokoll = () => {
    const t = politik.protokoll();
    console.log(t);
    return t;
  };
  const schliessen = () => politik.beende("Fenster geschlossen");
  globalThis.addEventListener?.("beforeunload", schliessen);
  globalThis.addEventListener?.("pagehide", schliessen);
  return e;
}

/**
 * Anschluss an den Kern: er legt sich unter `globalThis.__KI_KERN__` ab (kern.ts).
 * Ist er noch nicht da, wird gewartet — der Socket der Seite geht erst später auf.
 * Aufgerufen vom Bündel der Erweiterung; nichts davon startet von selbst etwas.
 */
export function starteMitKern(
  opts: Partial<PolitikOpts> = {},
  wartenMs = 500,
): void {
  const g = globalThis as Record<string, unknown>;
  const versuch = () => {
    const kern = g.__KI_KERN__ as Kern | undefined;
    if (!kern || typeof kern.beobachtung !== "function") {
      setTimeout(versuch, wartenMs);
      return;
    }
    const p = new Politik(kern, opts);
    (g as Record<string, unknown>).__KI_POLITIK__ = p;
    installiere(p);
    p.start();
    console.info(
      "[ki] Politik aktiv — Standard ist Zuschauen. Protokoll: kiProtokoll()",
    );
  };
  versuch();
}

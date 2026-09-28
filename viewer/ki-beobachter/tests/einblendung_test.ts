/**
 * Prüft die Einblendung ohne Browser, gegen ein winziges DOM, in dem `innerHTML` wirft.
 *
 * Der Kern der Sache: fremde Texte (Partie-ID, Fehlertext des Servers, letzte Aktion,
 * Protokoll) dürfen nur als Text ins Dokument. Der Test schiebt darum überall ein
 * `<img src=x onerror=…>` hinein und verlangt, dass es wörtlich als Text wieder
 * herauskommt — und dass unterwegs kein `innerHTML` angefasst wurde.
 */
import assert from "node:assert/strict";
import test from "node:test";

import { baueEinblendung, feld } from "../einblendung.ts";
import { Politik, OPTS_STANDARD, protokollLesen } from "../politik.ts";
import type { Knoten } from "./attrappe.ts";
import { AttrappenKern, antwortHolen, festeBeobachtung, setzeDom, ZELLE_BAU_OK } from "./attrappe.ts";

// Das winzige DOM ist kein echtes HTMLElement; an der Grenze wird einmal umgedeutet.
const alsHtml = (k: Knoten) => k as unknown as HTMLElement;
const alsKnoten = (e: HTMLElement) => e as unknown as Knoten;

const BOESE = '<img src=x onerror="alert(1)">';
const JETZT_GUELTIG = () => new Date("2026-09-12T18:00:00");

function aufbau() {
  const dom = setzeDom();
  const kern = new AttrappenKern(
    festeBeobachtung({ legalExtra: { [ZELLE_BAU_OK]: 1 << 1 } }),
  );
  const p = new Politik(kern, { jetzt: JETZT_GUELTIG, holen: antwortHolen({ intent: null }) });
  return { dom, kern, p };
}

test("fremde Texte landen als Text, nie als HTML", () => {
  const { dom, kern, p } = aufbau();
  try {
    const e = baueEinblendung(p, alsHtml(dom.body));
    // Ein Feind im Namen der letzten Aktion und im Fehlertext des Servers.
    e.zeige({
      ...p.messwerte(kern.beob),
      letzte_aktion: BOESE,
      fehler: BOESE,
    });
    const text = alsKnoten(e.wurzel).textContent;
    assert.ok(text.includes(BOESE.slice(0, 30)), "der Text muss wörtlich dastehen");
    // Kein Knoten trägt ein echtes img-Element: alles ist Text.
    assert.equal(alsKnoten(e.wurzel).suche("img").length, 0);
    assert.equal(alsKnoten(e.wurzel).suche("script").length, 0);
  } finally {
    dom.aufraeumen();
  }
});

test("Protokoll wird als Text gezeigt, auch mit böser Partie-ID", () => {
  const { dom, kern, p } = aufbau();
  try {
    kern.setzePartie(BOESE);
    void p.tick(32); // legt den Protokolleintrag an
    const e = baueEinblendung(p, alsHtml(dom.body));
    const knopf = alsKnoten(e.wurzel).suche("button").find((k: Knoten) => k.textContent === "Protokoll zeigen");
    assert.ok(knopf, "Knopf „Protokoll zeigen“ muss da sein");
    knopf!.klick();
    const pre = alsKnoten(e.wurzel).suche("pre")[0];
    assert.ok(pre.textContent.includes(BOESE), "Partie-ID wörtlich als Text");
    assert.equal(alsKnoten(e.wurzel).suche("img").length, 0);
    assert.equal(protokollLesen(OPTS_STANDARD.protokollSchluessel)[0].partie, BOESE);
    knopf!.klick(); // wieder zu
    assert.equal(pre.style.display, "none");
  } finally {
    dom.aufraeumen();
  }
});

test("Knöpfe: anhalten/weiterlaufen und der gesperrte Senden-Schalter", () => {
  const { dom, p } = aufbau();
  try {
    const e = baueEinblendung(p, alsHtml(dom.body));
    const knoepfe = alsKnoten(e.wurzel).suche("button");
    const anhalten = knoepfe[0];
    assert.equal(anhalten.textContent, "KI anhalten");
    anhalten.klick();
    assert.equal(p.istAngehalten(), true);
    assert.equal(anhalten.textContent, "KI weiterlaufen");
    anhalten.klick();
    assert.equal(p.istAngehalten(), false);

    const senden = knoepfe[1];
    // Öffentliche Partien sind gesperrt → der Schalter ist aus und sagt warum.
    assert.equal(senden.disabled, true);
    senden.klick();
    assert.equal(p.sendetGerade(), false);
    assert.ok(alsKnoten(e.wurzel).textContent.includes("öffentlichen Partien"), alsKnoten(e.wurzel).textContent);
  } finally {
    dom.aufraeumen();
  }
});

test("Sperre für öffentliche Partien steht im Bild", () => {
  const { dom, p } = aufbau();
  try {
    const e = baueEinblendung(p, alsHtml(dom.body));
    const t = alsKnoten(e.wurzel).textContent;
    assert.ok(t.includes("gesperrt"), t);
  } finally {
    dom.aufraeumen();
  }
});

test("Einblendung klappt weg und merkt sich das", () => {
  const { dom, p } = aufbau();
  try {
    const e = baueEinblendung(p, alsHtml(dom.body));
    assert.ok(alsKnoten(e.wurzel).textContent.includes("▾ zuklappen"));
    alsKnoten(e.wurzel).kinder[0].klick(); // Kopfzeile
    assert.ok(alsKnoten(e.wurzel).textContent.includes("▸ aufklappen"));
    assert.equal(globalThis.localStorage.getItem("ki-einblendung-offen"), "0");
  } finally {
    dom.aufraeumen();
  }
});

test("feld() setzt nur Text", () => {
  const dom = setzeDom();
  try {
    const f = alsKnoten(feld("Aktion", BOESE));
    assert.equal(f.textContent, "Aktion " + BOESE);
  } finally {
    dom.aufraeumen();
  }
});

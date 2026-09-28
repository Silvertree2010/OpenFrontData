/**
 * Prüft die Politik ohne Browser. Lauf:
 *   node --experimental-strip-types --test viewer/ki-beobachter/tests/
 *
 * Was hier bewiesen wird:
 *   1. Die Anfrage hat genau das Format, das `trainer/spielen.py` liest (Blockgrössen).
 *   2. Die Umkehr der Featurisierung trifft die Vorwärtsrechnung aus `env/featurize.py`
 *      (gegen das echte Python gemessen, wenn python3 da ist).
 *   3. Aus einer festen Beobachtung entsteht eine gültige Absicht, und zwar die erste
 *      Kandidatenzelle, die gegen die legal-Bits zulässig ist.
 *   4. In der Startphase fragt die Politik nicht einmal.
 *   5. Nach dem Ablaufdatum wird nichts gesendet und der Grund steht in den Messwerten.
 *   6. Standard ist Zuschauen: der Kern bekommt nichts, solange SENDEN_AN aus ist.
 */
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import {
  absichtAus,
  baueAnfrage,
  CELL_BYTES,
  configDict,
  erlaubnisGueltig,
  MAPLEN,
  OPTS_STANDARD,
  ownDict,
  Politik,
  protokollLesen,
  zelleErlaubt,
  zellMitte,
} from "../politik.ts";
import {
  AttrappenKern,
  antwortHolen,
  festeBeobachtung,
  setzeDom,
  ZELLE_BAU_NEIN,
  ZELLE_BAU_OK,
} from "./attrappe.ts";

const JETZT_GUELTIG = () => new Date("2026-09-12T18:00:00");
const JETZT_ABGELAUFEN = () => new Date("2026-09-13T12:00:01");

/** Antwort des D0-Servers für eine Stadt: erst eine unzulässige, dann die zulässige Zelle. */
function antwortBau(kandidaten = [ZELLE_BAU_NEIN, ZELLE_BAU_OK]) {
  return {
    p_handeln: 0.81,
    value: 0.3,
    atype: "BUILD_UNIT",
    gruppe: "bau",
    einheit: "City",
    zielSid: null,
    kandidaten,
    decide_every: 32,
    intent: { type: "build_unit", unit: "City", tile: 12345 },
  };
}

function beobachtungMitBau() {
  return festeBeobachtung({
    legalExtra: { [ZELLE_BAU_OK]: 1 << 1 }, // Bit 1 = bau; ZELLE_BAU_NEIN bleibt ohne
  });
}

test("Anfrage hat die Blockgrössen des Trainings", () => {
  const b = beobachtungMitBau();
  const a = baueAnfrage(b, { ...OPTS_STANDARD, jetzt: JETZT_GUELTIG });
  assert.equal(Buffer.from(a.map_b64 as string, "base64").length, MAPLEN);
  assert.equal(Buffer.from(a.cells_b64 as string, "base64").length, CELL_BYTES);
  const zellen = Buffer.from(a.cells_b64 as string, "base64");
  // owner u16 LE an Platz 0, frac bei 2·16200, legal bei 3·16200
  assert.equal(zellen.readUInt16LE(2 * 7), b.owner[7]);
  assert.equal(zellen[16200 * 2 + 7], b.frac[7]);
  assert.equal(zellen[16200 * 3 + ZELLE_BAU_OK], b.legal[ZELLE_BAU_OK]);
  assert.equal((a.opps as unknown[]).length, 5);
  assert.equal(a.decide_every, 32);
  assert.equal(a.tick, b.tick);
  assert.ok(a.ctx && typeof a.ctx === "object");
});

test("fertige Anfrage des Kerns (_anfrage) wird durchgereicht", () => {
  const b = beobachtungMitBau() as Record<string, unknown>;
  const roh = {
    map_b64: "AAA",
    cells_b64: "BBB",
    own: { troops: 1 },
    opps: [{ id: 7, user: "wer" }],
    config: { gameMode: "Free For All" },
    sid: 3,
    allies: [4],
    ctx: { mapW: 360, mapH: 180, troops: 1, gold: 2, oppSids: [7] },
  };
  b._anfrage = roh;
  const a = baueAnfrage(b as never, { ...OPTS_STANDARD, takt: 32, jetzt: JETZT_GUELTIG });
  assert.equal(a.map_b64, "AAA"); // nicht neu gerechnet
  assert.deepEqual(a.ctx, roh.ctx); // ctx, sid, allies bleiben erhalten
  assert.equal(a.sid, 3);
  assert.equal(a.decide_every, 32);
  assert.equal(a.tick, 640);
  // Und die Kartengrösse für die Rückfall-Kachel kommt aus demselben ctx.
  const abs = absichtAus(b as never, antwortBau(), { ...OPTS_STANDARD, jetzt: JETZT_GUELTIG });
  assert.equal(abs!.tile, zellMitte(ZELLE_BAU_OK, 360, 180));
});

test("Rückfall aus den Vektoren trägt die Namen, die env/featurize.py liest", () => {
  const b = beobachtungMitBau();
  const own = ownDict(b.own);
  // Genau die Werte, die die Attrappe gesetzt hat — kommen sie unter den richtigen
  // Namen an, ergibt die Vorwärtsrechnung des Trainers denselben Vektor.
  const erwartet = {
    troops: 120000,
    gold: 54321,
    tiles: 1234,
    borderLen: 210,
    allies: 1,
    tick: 640,
    alive: 12,
    troopsRatio: 0.42,
    boardShare: 0.037,
    n_City: 3,
  };
  const cfg = configDict(b.config);
  let py = "";
  try {
    py = execFileSync("git", ["show", "trainer:env/featurize.py"], {
      cwd: join(import.meta.dirname, "../../.."),
      encoding: "utf8",
    });
  } catch {
    console.warn("  (übersprungen: env/featurize.py nicht auslesbar)");
    return;
  }
  const dir = mkdtempSync(join(tmpdir(), "ki-featurize-"));
  writeFileSync(join(dir, "featurize.py"), py);
  writeFileSync(
    join(dir, "eingabe.json"),
    JSON.stringify({ own, erwartet, config: cfg }),
  );
  writeFileSync(
    join(dir, "pruef.py"),
    [
      "import json, featurize as F",
      "d = json.load(open('eingabe.json'))",
      "print(json.dumps({'own': F.featurize_own(d['own']),",
      "                  'soll': F.featurize_own(d['erwartet']),",
      "                  'cfg': F.featurize_config(d['config'])}))",
    ].join("\n"),
  );
  let raus: string;
  try {
    raus = execFileSync("python3", ["pruef.py"], { cwd: dir, encoding: "utf8" });
  } catch (e) {
    console.warn("  (übersprungen: python3 fehlt)", String(e).slice(0, 80));
    return;
  }
  const { own: o, soll, cfg: c } = JSON.parse(raus) as {
    own: number[];
    soll: number[];
    cfg: number[];
  };
  assert.equal(o.length, soll.length);
  for (let i = 0; i < o.length; i++) {
    // Toleranz nur für die float32-Rundung der Beobachtung.
    assert.ok(
      Math.abs(o[i] - soll[i]) < 1e-6,
      `own[${i}]: ${o[i]} statt ${soll[i]} — Reihenfolge weicht von beobachtung.ts ab`,
    );
  }
  assert.ok(o.some((x) => x > 0), "der Vektor darf nicht leer sein");
  assert.equal(c.length, 20); // die Konfiguration geht durch, wenn auch verstümmelt
  assert.ok(Math.abs(c[5] - 30 / 500) < 1e-9, "bots landen im Konfigurationsvektor");
});

test("erste zulässige Kandidatenzelle gewinnt", () => {
  const b = beobachtungMitBau();
  assert.equal(zelleErlaubt(b, "bau", ZELLE_BAU_NEIN, null), false);
  assert.equal(zelleErlaubt(b, "bau", ZELLE_BAU_OK, null), true);
  const abs = absichtAus(b, antwortBau(), { ...OPTS_STANDARD, jetzt: JETZT_GUELTIG });
  assert.ok(abs, "eine Absicht muss entstehen");
  assert.equal(abs!.art, "build_unit");
  assert.equal(abs!.unit, "City");
  assert.equal(abs!.zelle, ZELLE_BAU_OK);
  assert.equal(abs!.gruppe, "bau");
  // Ohne bekannte Kartengrösse gibt es keine Kachel — die löst der Kern auf.
  assert.equal(abs!.tile, undefined);
  // Der Kern nimmt sie an.
  const k = new AttrappenKern(b);
  assert.equal(k.sendeAbsicht(abs!), true);
  assert.equal(k.gesendet.length, 1);
});

test("Kachel als Rückfall, wenn die Kartengrösse bekannt ist", () => {
  const b = beobachtungMitBau();
  const o = { ...OPTS_STANDARD, kartenBreite: 360, kartenHoehe: 180, jetzt: JETZT_GUELTIG };
  const abs = absichtAus(b, antwortBau(), o);
  const kachel = zellMitte(ZELLE_BAU_OK, 360, 180);
  assert.ok(kachel > 0);
  assert.equal(abs!.tile, kachel);
});

test("keine zulässige Zelle → keine Absicht", () => {
  const b = beobachtungMitBau();
  const a = antwortBau([ZELLE_BAU_NEIN, ZELLE_BAU_NEIN + 1, ZELLE_BAU_NEIN + 2]);
  assert.equal(absichtAus(b, a, { ...OPTS_STANDARD, jetzt: JETZT_GUELTIG }), null);
});

test("no_op und leere Antwort ergeben nichts", () => {
  const b = beobachtungMitBau();
  const o = { ...OPTS_STANDARD, jetzt: JETZT_GUELTIG };
  assert.equal(absichtAus(b, { intent: { type: "no_op" }, p_handeln: 0.1 }, o), null);
  assert.equal(absichtAus(b, null, o), null);
});

test("Client-Schwelle sperrt unter P(handeln)", () => {
  const b = beobachtungMitBau();
  const o = { ...OPTS_STANDARD, schwelle: 0.9, jetzt: JETZT_GUELTIG };
  assert.equal(absichtAus(b, antwortBau(), o), null); // p_handeln 0.81 < 0.9
  assert.ok(absichtAus(b, antwortBau(), { ...o, schwelle: 0.5 }));
});

test("Startphase: die Politik fragt nicht einmal", async () => {
  const dom = setzeDom();
  try {
    const b = festeBeobachtung({ startphase: true, legalExtra: { [ZELLE_BAU_OK]: 2 } });
    const kern = new AttrappenKern(b);
    let gefragt = 0;
    const p = new Politik(kern, {
      jetzt: JETZT_GUELTIG,
      holen: antwortHolen(antwortBau(), () => gefragt++),
    });
    p.start();
    await kern.tickeAn(64);
    assert.equal(gefragt, 0, "in der Startphase wird nicht gefragt");
    assert.equal(kern.gesendet.length, 0);
    assert.match(p.messwerte(b).letzte_aktion ?? "", /Startphase/);
  } finally {
    dom.aufraeumen();
  }
});

test("Takt: gefragt wird nur alle 32 Ticks, und nicht wenn tot", async () => {
  const dom = setzeDom();
  try {
    const kern = new AttrappenKern(beobachtungMitBau());
    let gefragt = 0;
    const p = new Politik(kern, {
      jetzt: JETZT_GUELTIG,
      holen: antwortHolen(antwortBau(), () => gefragt++),
    });
    p.start();
    for (const t of [1, 17, 31, 33]) await kern.tickeAn(t);
    assert.equal(gefragt, 0);
    await kern.tickeAn(64);
    assert.equal(gefragt, 1);
    kern.beob = festeBeobachtung({ lebe: false });
    await kern.tickeAn(96);
    assert.equal(gefragt, 1, "tot wird nicht gefragt");
  } finally {
    dom.aufraeumen();
  }
});

test("Standard ist Zuschauen: der Kern bekommt nichts", async () => {
  const dom = setzeDom();
  try {
    const kern = new AttrappenKern(beobachtungMitBau());
    const p = new Politik(kern, { jetzt: JETZT_GUELTIG, holen: antwortHolen(antwortBau()) });
    p.start();
    await kern.tickeAn(64);
    assert.equal(kern.gesendet.length, 0, "ohne Einschalten wird nicht gesendet");
    // SENDEN_AN ist im Repo aus; darum lässt sich das Senden gar nicht einschalten.
    assert.equal(p.setzeSenden(true), false);
    assert.equal(kern.gesendet.length, 0);
    await kern.tickeAn(96);
    assert.equal(kern.gesendet.length, 0);
    const m = p.messwerte(kern.beob);
    assert.equal(m.senden, false);
    assert.ok(m.letzte_aktion?.includes("BUILD_UNIT"), m.letzte_aktion ?? "");
  } finally {
    dom.aufraeumen();
  }
});

test("nach dem Ablaufdatum: kein Senden, Grund im Klartext", async () => {
  const dom = setzeDom();
  try {
    assert.equal(erlaubnisGueltig(new Date("2026-09-13T11:59:59")), true);
    assert.equal(erlaubnisGueltig(new Date("2026-09-13T12:00:01")), false);
    const kern = new AttrappenKern(beobachtungMitBau());
    const p = new Politik(kern, {
      jetzt: JETZT_ABGELAUFEN,
      holen: antwortHolen(antwortBau()),
    });
    p.start();
    await kern.tickeAn(64);
    assert.equal(kern.gesendet.length, 0);
    assert.equal(p.setzeSenden(true), false);
    await kern.tickeAn(96);
    assert.equal(kern.gesendet.length, 0);
    const grund = p.darfSenden();
    assert.match(grund ?? "", /Kennzeichnung/);
    assert.match(grund ?? "", /abgelaufen/);
    const m = p.messwerte(kern.beob);
    assert.equal(m.erlaubnis_gueltig, false);
    assert.match(m.letzte_aktion ?? "", /gesperrt/);
  } finally {
    dom.aufraeumen();
  }
});

test("Messwerte: Platz und Handlungsrate aus der Beobachtung", () => {
  const dom = setzeDom();
  try {
    const b = beobachtungMitBau();
    const p = new Politik(new AttrappenKern(b), { jetzt: JETZT_GUELTIG });
    const m = p.messwerte(b);
    assert.equal(m.platz, 3, "zwei Gegner haben mehr Kacheln");
    assert.equal(m.lebende, 6);
    assert.ok(Math.abs(m.kacheln - 1234) < 1);
    assert.ok(Math.abs(m.truppen - 120000) < 1);
    assert.ok(Math.abs(m.gebiet - 0.037) < 1e-6);
  } finally {
    dom.aufraeumen();
  }
});

test("Protokoll je Partie: Beginn, ID, Dauer, Ergebnis, wie beendet", async () => {
  const dom = setzeDom();
  try {
    const kern = new AttrappenKern(beobachtungMitBau());
    const p = new Politik(kern, { jetzt: JETZT_GUELTIG, holen: antwortHolen(antwortBau()) });
    p.start();
    await kern.tickeAn(64);
    let eintraege = protokollLesen(OPTS_STANDARD.protokollSchluessel);
    assert.equal(eintraege.length, 1);
    assert.equal(eintraege[0].partie, "partie-1");
    p.beende("Test");
    eintraege = protokollLesen(OPTS_STANDARD.protokollSchluessel);
    assert.equal(eintraege[0].grund, "Test");
    assert.equal(typeof eintraege[0].sekunden, "number");
    assert.match(eintraege[0].ergebnis ?? "", /Tick 640, Platz 3\/6/);
    assert.match(p.protokoll(), /partie-1/);
    // neue Partie → neuer Eintrag
    kern.setzePartie("partie-2");
    await kern.tickeAn(128);
    assert.equal(protokollLesen(OPTS_STANDARD.protokollSchluessel).length, 2);
  } finally {
    dom.aufraeumen();
  }
});

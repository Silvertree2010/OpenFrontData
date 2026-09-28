/**
 * Prüft den Händedruck der Brücke und den Weg zum Server — ohne Browser.
 *
 * Inhaltsskript und Seitenteil laufen hier in EINEM nachgebauten Fenster (im Browser
 * sind es zwei Welten, aber dasselbe `window`; die Prüfungen auf `ev.source` verlangen
 * genau diese Gleichheit). Der Hintergrunddienst läuft in einem eigenen Kontext, wie er
 * es auch im Browser tut, und holt beim echten Inferenz-Server.
 *
 * Geprüft wird:
 *   1. die Brücke meldet sich von selbst („da"),
 *   2. sie antwortet auf Klopfen (falls die Seite zuerst dran war),
 *   3. eine Anfrage geht durch und kommt zurück,
 *   4. eine fremde Gegenstelle wird abgelehnt,
 *   5. ohne Inhaltsskript kommt ein klarer Fehler statt stiller Stille.
 *
 *   npx tsx aitest/brueckeprobe.ts <ziel-ordner> <seitenbuendel.js> <inf-url>
 */
import fs from "fs";
import path from "path";
import vm from "vm";

const [ordner, seitenBuendel, infUrl] = process.argv.slice(2);

type Horcher = (m: any, a: any, antworte: (x: any) => void) => boolean;
let dienstHorcher: Horcher | null = null;
const dienstKontext: any = {
  chrome: { runtime: { onMessage: { addListener: (f: Horcher) => { dienstHorcher = f; } } } },
  fetch, console, URL,
};
dienstKontext.globalThis = dienstKontext;
dienstKontext.self = dienstKontext;
vm.runInContext(fs.readFileSync(path.join(ordner, "dienst.js"), "utf8"), vm.createContext(dienstKontext));

function baueFenster(mitInhaltsskript: boolean): any {
  const ziel = new EventTarget();
  const f: any = {
    console, setTimeout, clearTimeout, setInterval, clearInterval,
    MessageEvent, Response, fetch, URL, Date, JSON, Object, Array, Error, Math,
    document: { addEventListener: () => {} },
    chrome: {
      runtime: {
        lastError: undefined,
        sendMessage: (m: any, zurueck: (a: any) => void) => { dienstHorcher?.(m, {}, zurueck); },
      },
    },
  };
  f.window = f;
  f.self = f;
  f.globalThis = f;
  f.addEventListener = ziel.addEventListener.bind(ziel);
  f.removeEventListener = ziel.removeEventListener.bind(ziel);
  f.dispatchEvent = ziel.dispatchEvent.bind(ziel);
  const k = vm.createContext(f);
  // In einem vm-Kontext ist `globalThis` nicht das Objekt, das man hineingibt, sondern
  // dessen Stellvertreter. Die Skripte prüfen `ev.source === globalThis` — also muss die
  // Quelle genau dieser Stellvertreter sein. Im Browser ist das ohnehin dasselbe Fenster.
  vm.runInContext("globalThis.__ich__ = globalThis;", k);
  f.postMessage = (daten: any) => {
    const ev: any = new MessageEvent("message", { data: daten });
    Object.defineProperty(ev, "source", { value: f.__ich__ });
    ziel.dispatchEvent(ev);
  };
  if (mitInhaltsskript) {
    vm.runInContext(fs.readFileSync(path.join(ordner, "brueckeInhalt.js"), "utf8"), k);
  }
  vm.runInContext(fs.readFileSync(seitenBuendel, "utf8"), k);
  return f;
}

async function main() {
  const e: any = {};
  const f = baueFenster(true);
  e.meldet_sich_von_selbst = f.__KI_BRUECKE__.brueckeDa() === true;
  try {
    await f.__KI_BRUECKE__.warteAufBruecke(3000);
    e.haendedruck = true;
  } catch (x: any) { e.haendedruck = String(x?.message ?? x).slice(0, 80); }
  try {
    const r = await f.__KI_BRUECKE__.brueckeHolen(`${infUrl}/act`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
    e.status = r.status;
    e.antwort = (await r.text()).slice(0, 80);
    e.durchgereicht = true;
  } catch (x: any) { e.durchgereicht = false; e.fehler = String(x?.message ?? x).slice(0, 120); }
  try {
    await f.__KI_BRUECKE__.brueckeHolen("http://example.com/act", { method: "POST", body: "{}" });
    e.fremde_gegenstelle = "DURCHGELASSEN";
  } catch (x: any) { e.fremde_gegenstelle = String(x?.message ?? x).slice(0, 60); }

  // Ohne Inhaltsskript: klarer Fehler, keine stille Stille.
  const ohne = baueFenster(false);
  try {
    await ohne.__KI_BRUECKE__.warteAufBruecke(800);
    e.ohne_inhaltsskript = "KEIN FEHLER";
  } catch (x: any) { e.ohne_inhaltsskript = String(x?.message ?? x).slice(0, 60); }

  e.ok = e.meldet_sich_von_selbst === true && e.haendedruck === true
    && e.durchgereicht === true && /nicht erlaubt/.test(String(e.fremde_gegenstelle))
    && /antwortet nicht/.test(String(e.ohne_inhaltsskript));
  process.stdout.write(JSON.stringify(e, null, 1) + "\n");
  process.exit(0);
}
main();

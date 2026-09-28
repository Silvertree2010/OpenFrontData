/**
 * Spielweise: breite Kennzahlen je KI-Spieler, als Zeitreihe alle --verlauf-alle Ticks (Standard
 * 250) und als Zusammenfassung je Partie. Wie eine KI spielt, nicht nur welcher Platz dabei
 * herauskommt.
 *
 * Herkunft (was die Engine mitführt, wird gelesen, nicht neu gezählt):
 *   Engine-Stats   game.stats().getPlayerStats(p): Gold je Quelle (Einkommen), Strukturen
 *                  gebaut/zerstört/erobert/verloren/aufgewertet je Typ, Boote, Nukes, Verrat.
 *   Engine-Zustand p.gold(), p.troops(), config.maxTroops(p), p.units(), p.alliances(),
 *                  p.getEmbargoes(), p.outgoingAttacks(), p.incomingAttacks(), p.tiles().
 *   Ereignisse     Allianzanfragen, Antworten, Bündnisbrüche, Embargos aus den GameUpdates des
 *                  Ticks (die Engine zählt sie nicht); Zahl der Angriffe aus einem Zähler um
 *                  stats.attack (die Stats führen nur die Truppensumme). Beides liest nur mit
 *                  und verändert den Spielzustand nicht.
 *   Berechnet      Gold pro Minute (Δ Einkommen / Δ Ticks · 600, ein Tick = 100 ms laut
 *                  ServerEnv.turnIntervalMs), ausgegebenes Gold (Einkommen − Δ Bestand; Gold
 *                  verlässt einen lebenden Spieler nur über Bau und Aufwertung, Spenden sind in der
 *                  Arena aus) und die Gebietsverteilung aus p.tiles().
 *
 * Gebietsverteilung (verteilung(), in verteilung.py für die Menschen-Referenz nachgebaut):
 *   komponenten     getrennte Gebietsstücke, 4er-Nachbarschaft
 *   groesste        Anteil der Kacheln im grössten Stück
 *   raster          Felder eines 16×16-Rasters über der Karte mit mindestens einer eigenen Kachel
 *   raster_anteil   raster / Rasterfelder mit Land
 *   streuung        RMS-Abstand der Kacheln zum Schwerpunkt / Kartendiagonale
 *   streuung_rel    RMS-Abstand / RMS-Abstand einer Kreisscheibe gleicher Fläche (1 = kompakt)
 *   kueste          Anteil der eigenen Kacheln mit Uferbit (GameMap.isShore)
 *
 * Kosten: alles läuft nur alle 250 Ticks (Gebiet O(eigene Kacheln)) bzw. je Tick über die paar
 * Diplomatie-Updates. ms() liefert die eigene Rechenzeit, arena.ts schreibt sie in die Zeile.
 */
import { Game, Player, UnitType } from "../src/core/game/Game";
import { GameUpdateType } from "../src/core/game/GameUpdates";

export const TICKS_JE_MINUTE = 600;   // ServerEnv.turnIntervalMs() = 100 ms
export const RASTER = 16;

/** Name in der Zeile, UnitType, Schlüssel in PlayerStats.units */
export const STRUKTUREN: [string, UnitType, string][] = [
  ["city", UnitType.City, "city"], ["port", UnitType.Port, "port"],
  ["factory", UnitType.Factory, "fact"], ["defense", UnitType.DefensePost, "defp"],
  ["sam", UnitType.SAMLauncher, "saml"], ["silo", UnitType.MissileSilo, "silo"],
  ["warship", UnitType.Warship, "wshp"],
];
const GEBAEUDE = new Set(["city", "port", "factory", "defense", "sam", "silo"]);
const TYP_NAME = new Map<any, string>(STRUKTUREN.map(([n, ut]) => [ut, n]));

export interface Verteilung {
  komponenten: number; groesste: number; raster: number; raster_anteil: number;
  streuung: number; streuung_rel: number; kueste: number;
}

/** Wiederverwendete Puffer (eine Karte, beliebig viele Aufrufe). */
export class Puffer {
  readonly stempel: Int32Array;
  stapel: Int32Array;
  readonly zellen = new Uint8Array(RASTER * RASTER);
  marke = 0;
  constructor(n: number) {
    this.stempel = new Int32Array(n);
    this.stapel = new Int32Array(1024);
  }
}

/** Rasterfelder, in denen mindestens eine Landkachel liegt (einmal je Karte). */
export function landZellen(W: number, H: number, istLand: (r: number) => boolean): number {
  const z = new Uint8Array(RASTER * RASTER);
  for (let y = 0; y < H; y++) {
    const gy = ((y * RASTER) / H) | 0;
    for (let x = 0; x < W; x++) {
      if (istLand(y * W + x)) z[gy * RASTER + (((x * RASTER) / W) | 0)] = 1;
    }
  }
  let n = 0;
  for (const v of z) n += v;
  return n;
}

/**
 * Gebietsverteilung einer Kachelmenge. `eigen(r)` muss für genau die Kacheln aus `kacheln` wahr
 * sein (Nachbarschaftstest der Komponenten). Reine Funktion, damit verteilung.py sie
 * nachrechnen kann (tests/verteilung_paritaet.py).
 */
export function verteilung(kacheln: Iterable<number>, n: number, W: number, H: number,
                           eigen: (r: number) => boolean, ufer: (r: number) => boolean,
                           landzellen: number, pf: Puffer): Verteilung {
  const leer: Verteilung = { komponenten: 0, groesste: 0, raster: 0, raster_anteil: 0,
    streuung: 0, streuung_rel: 0, kueste: 0 };
  if (n <= 0) return leer;
  pf.zellen.fill(0);
  let sx = 0, sy = 0, sxx = 0, syy = 0, kueste = 0;
  for (const r of kacheln) {
    const x = r % W, y = (r - x) / W;
    sx += x; sy += y; sxx += x * x; syy += y * y;
    pf.zellen[(((y * RASTER) / H) | 0) * RASTER + (((x * RASTER) / W) | 0)] = 1;
    if (ufer(r)) kueste++;
  }
  // Komponenten: Tiefensuche, Stempel statt Löschen (eine Marke je Aufruf)
  if (++pf.marke >= 0x7fffffff) { pf.stempel.fill(0); pf.marke = 1; }
  const m = pf.marke, st = pf.stempel;
  let komp = 0, groesste = 0;
  const N = W * H;
  let top = 0;
  const schiebe = (q: number) => {
    if (st[q] === m || !eigen(q)) return;
    st[q] = m;
    if (top >= pf.stapel.length) {
      const neu = new Int32Array(pf.stapel.length * 2);
      neu.set(pf.stapel);
      pf.stapel = neu;
    }
    pf.stapel[top++] = q;
  };
  for (const s of kacheln) {
    if (st[s] === m) continue;
    komp++;
    let groesse = 0;
    top = 0;
    st[s] = m;
    pf.stapel[top++] = s;
    while (top > 0) {
      const r = pf.stapel[--top];
      groesse++;
      const x = r % W;
      // links, rechts, oben, unten; kein Umlauf am Kartenrand
      if (x > 0) schiebe(r - 1);
      if (x < W - 1) schiebe(r + 1);
      if (r >= W) schiebe(r - W);
      if (r + W < N) schiebe(r + W);
    }
    if (groesse > groesste) groesste = groesse;
  }
  let raster = 0;
  for (const v of pf.zellen) raster += v;
  const mx = sx / n, my = sy / n;
  const varianz = Math.max(0, sxx / n - mx * mx + syy / n - my * my);
  const rms = Math.sqrt(varianz);
  return {
    komponenten: komp, groesste: groesste / n, raster,
    raster_anteil: landzellen > 0 ? raster / landzellen : 0,
    streuung: rms / Math.sqrt(W * W + H * H),
    streuung_rel: rms / Math.sqrt(n / (2 * Math.PI)),
    kueste: kueste / n,
  };
}

const zahl = (x: any): number => (typeof x === "bigint" ? Number(x) : Number(x ?? 0));
const summe = (a: any[] | undefined): number => (a ?? []).reduce((s, v) => s + zahl(v), 0);
const r6 = (x: number) => Number(x.toFixed(6));

interface Zaehler {
  sid: number;
  p: Player | null;
  angr_aus: number; angr_ein: number;
  anfr_aus: number; anfr_ein: number; angen_aus: number; angen_ein: number;
  verraten_worden: number; embargo_start: number;
  gold0: number | null;
  letzt: { t: number; verdient: number } | null;
  spalten: Record<string, number[]>;
}

/** Punkt der Zeitreihe; die Reihenfolge der Schlüssel ist die der Spalten in der Zeile. */
export const SPALTEN = [
  "t", "gold", "gold_min", "verdient", "ausgegeben", "truppen", "truppen_max",
  ...STRUKTUREN.map(([n]) => `n_${n}`), "strukturen", "stufen",
  "angr_aus", "angr_ein", "angr_aus_aktiv", "angr_ein_aktiv", "boote", "nukes",
  "allianzen", "anfr_aus", "anfr_ein", "allianzen_neu", "verrat", "embargos",
  "gebiet", "komponenten", "groesste", "raster", "raster_anteil", "streuung", "streuung_rel", "kueste",
] as const;

export class Spielweise {
  private readonly W: number;
  private readonly H: number;
  private readonly pf: Puffer;
  private readonly landzellen: number;
  private readonly nachCid = new Map<string, Zaehler>();
  private readonly nachSid = new Map<number, Zaehler>();
  private zeit = 0;           // ns, eigene Rechenzeit

  constructor(private readonly game: Game, private readonly landKacheln: number) {
    const t0 = process.hrtime.bigint();
    this.W = game.width();
    this.H = game.height();
    this.pf = new Puffer(this.W * this.H);
    const map = game.map();
    this.landzellen = landZellen(this.W, this.H, (r) => map.isLand(r));
    // Zahl der Angriffe: die Stats führen nur die Truppensumme. Mitzählen, dann weiterreichen.
    const st: any = game.stats();
    const orig = st.attack.bind(st);
    st.attack = (p: Player, ziel: any, truppen: any) => {
      const a = this.nachSid.get(p.smallID());
      if (a) a.angr_aus++;
      if (ziel?.isPlayer?.()) {
        const b = this.nachSid.get(ziel.smallID());
        if (b) b.angr_ein++;
      }
      return orig(p, ziel, truppen);
    };
    this.zeit += Number(process.hrtime.bigint() - t0);
  }

  /** ms eigener Rechenzeit (Aufbau, Updates, Proben) */
  ms(): number { return this.zeit / 1e6; }

  /** Spieler ab dem Spawn verfolgen (t = Spawn-Tick, Bezug für den ersten Punkt Gold/min). */
  verfolge(cid: string, p: Player, t: number): void {
    if (this.nachCid.has(cid)) return;
    const s: any = this.game.stats().getPlayerStats(p) ?? {};
    const z: Zaehler = { sid: p.smallID(), p, angr_aus: 0, angr_ein: 0, anfr_aus: 0, anfr_ein: 0,
      angen_aus: 0, angen_ein: 0, verraten_worden: 0, embargo_start: 0, gold0: zahl(p.gold()),
      letzt: { t, verdient: summe(s.gold) },
      spalten: Object.fromEntries(SPALTEN.map((k) => [k, [] as number[]])) };
    this.nachCid.set(cid, z);
    this.nachSid.set(z.sid, z);
  }

  /** GameUpdates eines Ticks (Rückruf des GameRunner): Diplomatie mitzählen. */
  updates(u: any): void {
    if (!u || !this.nachSid.size) return;
    const t0 = process.hrtime.bigint();
    for (const e of u[GameUpdateType.AllianceRequest] ?? []) {
      const a = this.nachSid.get(e.requestorID), b = this.nachSid.get(e.recipientID);
      if (a) a.anfr_aus++;
      if (b) b.anfr_ein++;
    }
    for (const e of u[GameUpdateType.AllianceRequestReply] ?? []) {
      if (!e.accepted) continue;
      const a = this.nachSid.get(e.request?.requestorID), b = this.nachSid.get(e.request?.recipientID);
      if (a) a.angen_aus++;
      if (b) b.angen_ein++;
    }
    for (const e of u[GameUpdateType.BrokeAlliance] ?? []) {
      const b = this.nachSid.get(e.betrayedID);
      if (b) b.verraten_worden++;
    }
    // Nur dauerhafte Embargos zählen. Wird ein Spieler angegriffen, setzt die Engine selbst ein
    // vorübergehendes Embargo gegen den Angreifer (AttackExecution) — das ist kein Spielzug und
    // fehlt in der Menschen-Referenz (menschen.py zählt nur Embargo-Befehle). Das Ereignis sagt
    // nicht, welche Art es ist; der Eintrag beim Spieler (direkt danach gesetzt) schon.
    for (const e of u[GameUpdateType.EmbargoEvent] ?? []) {
      const a = this.nachSid.get(e.playerID);
      if (!a || e.event !== "start") continue;
      const p: any = this.game.playerBySmallID(e.playerID);
      const emb = p?.getEmbargoes?.().find((m: any) => m.target.smallID() === e.embargoedID);
      if (emb && !emb.isTemporary) a.embargo_start++;
    }
    this.zeit += Number(process.hrtime.bigint() - t0);
  }

  /** Ein Punkt der Zeitreihe für einen lebenden Spieler. */
  probe(t: number, cid: string, p: Player): void {
    const z = this.nachCid.get(cid);
    if (!z) return;
    const t0 = process.hrtime.bigint();
    const g = this.game;
    const s: any = g.stats().getPlayerStats(p) ?? {};
    const verdient = summe(s.gold);
    const gold = zahl(p.gold());
    const goldMin = z.letzt && t > z.letzt.t
      ? ((verdient - z.letzt.verdient) * TICKS_JE_MINUTE) / (t - z.letzt.t) : 0;
    z.letzt = { t, verdient };
    const anzahl: Record<string, number> = {};
    let stufen = 0;
    for (const u of p.units()) {
      const n = TYP_NAME.get(u.type());
      if (!n) continue;
      anzahl[n] = (anzahl[n] ?? 0) + 1;
      if (GEBAEUDE.has(n)) stufen += u.level();
    }
    const bomben = s.bombs ?? {};
    const map = g.map();
    const sid = p.smallID();
    const v = verteilung(p.tiles(), p.numTilesOwned(), this.W, this.H,
      (r) => map.ownerID(r) === sid, (r) => map.isShore(r), this.landzellen, this.pf);
    const punkt: Record<string, number> = {
      t, gold, gold_min: Math.round(goldMin), verdient,
      ausgegeben: Math.max(0, verdient + (z.gold0 ?? 0) - gold),
      truppen: Math.round(p.troops()), truppen_max: Math.round(g.config().maxTroops(p)),
      ...Object.fromEntries(STRUKTUREN.map(([n]) => [`n_${n}`, anzahl[n] ?? 0])),
      strukturen: STRUKTUREN.reduce((a, [n]) => a + (GEBAEUDE.has(n) ? anzahl[n] ?? 0 : 0), 0),
      stufen,
      angr_aus: z.angr_aus, angr_ein: z.angr_ein,
      angr_aus_aktiv: p.outgoingAttacks().length, angr_ein_aktiv: p.incomingAttacks().length,
      boote: zahl(s.boats?.trans?.[0]),
      nukes: zahl(bomben.abomb?.[0]) + zahl(bomben.hbomb?.[0]) + zahl(bomben.mirv?.[0]),
      allianzen: p.alliances().length, anfr_aus: z.anfr_aus, anfr_ein: z.anfr_ein,
      allianzen_neu: z.angen_aus + z.angen_ein, verrat: zahl(s.betrayals ?? p.betrayals()),
      embargos: p.getEmbargoes().filter((m) => !m.isTemporary).length,
      gebiet: r6(p.numTilesOwned() / this.landKacheln),
      komponenten: v.komponenten, groesste: r6(v.groesste), raster: v.raster,
      raster_anteil: r6(v.raster_anteil), streuung: r6(v.streuung), streuung_rel: r6(v.streuung_rel),
      kueste: r6(v.kueste),
    };
    for (const k of SPALTEN) z.spalten[k].push(punkt[k]);
    this.zeit += Number(process.hrtime.bigint() - t0);
  }

  /**
   * Ergebnis je Spieler: Zeitreihe (spaltenweise) und Zusammenfassung. `ueberleben` = Ticks
   * zwischen Spawn und Tod bzw. Partieende (arena.ts). Endstände kommen aus den Engine-Stats,
   * die nach dem Tod stehen bleiben; Zustandsgrössen aus den Proben zu Lebzeiten.
   */
  ergebnis(cid: string, ueberleben: number): any | null {
    const z = this.nachCid.get(cid);
    if (!z || !z.p) return null;
    const t0 = process.hrtime.bigint();
    const s: any = this.game.stats().getPlayerStats(z.p) ?? {};
    const sp = z.spalten;
    const med = (a: number[]) => {
      if (!a.length) return null;
      const b = [...a].sort((x, y) => x - y);
      return b.length % 2 ? b[(b.length - 1) / 2] : (b[b.length / 2 - 1] + b[b.length / 2]) / 2;
    };
    const max = (a: number[]) => (a.length ? Math.max(...a) : 0);
    const gold = s.gold ?? [];
    const verdient = summe(gold);
    const handel = zahl(gold[2]) + zahl(gold[3]) + zahl(gold[4]) + zahl(gold[5]);
    const minuten = ueberleben / TICKS_JE_MINUTE;
    const ausgegeben = max(sp.ausgegeben);
    const einheiten = s.units ?? {};
    const je1000 = (x: number) => (ueberleben > 0 ? Number(((x * 1000) / ueberleben).toFixed(4)) : 0);
    const quote = sp.truppen.map((v, i) => (sp.truppen_max[i] > 0 ? v / sp.truppen_max[i] : 0));
    const summe_: Record<string, number | null> = {
      gold_min: minuten > 0 ? Math.round(verdient / minuten) : 0,
      gold_verdient: verdient,
      gold_ausgegeben: ausgegeben,
      ausgabe_quote: verdient > 0 ? r6(ausgegeben / verdient) : 0,
      gold_max: max(sp.gold),
      handel_anteil: verdient > 0 ? r6(handel / verdient) : 0,
      ...Object.fromEntries(STRUKTUREN.map(([n, , k]) => [`gebaut_${n}`, zahl(einheiten[k]?.[0])])),
      strukturen_max: max(sp.strukturen),
      strukturen_verloren: STRUKTUREN.reduce((a, [, , k]) => a + zahl(einheiten[k]?.[3]), 0),
      strukturen_erobert: STRUKTUREN.reduce((a, [, , k]) => a + zahl(einheiten[k]?.[2]), 0),
      aufgewertet: STRUKTUREN.reduce((a, [, , k]) => a + zahl(einheiten[k]?.[4]), 0),
      truppen_max: max(sp.truppen),
      truppen_quote: med(quote) === null ? null : r6(med(quote)!),
      angriffe_je_1000: je1000(z.angr_aus),
      angriffe_erhalten_je_1000: je1000(z.angr_ein),
      angriffe_ein_aktiv: med(sp.angr_ein_aktiv),
      boote: zahl(s.boats?.trans?.[0]),
      nukes: zahl(s.bombs?.abomb?.[0]) + zahl(s.bombs?.hbomb?.[0]) + zahl(s.bombs?.mirv?.[0]),
      anfragen_gesendet: z.anfr_aus, anfragen_erhalten: z.anfr_ein,
      allianzen_neu: z.angen_aus + z.angen_ein, allianzen_max: max(sp.allianzen),
      verrat: zahl(s.betrayals), verraten_worden: z.verraten_worden, embargos: z.embargo_start,
      gebiet_max: max(sp.gebiet), komponenten: med(sp.komponenten), groesste: med(sp.groesste),
      raster_max: max(sp.raster), raster_anteil_max: max(sp.raster_anteil),
      streuung_rel: med(sp.streuung_rel), kueste: med(sp.kueste),
    };
    this.zeit += Number(process.hrtime.bigint() - t0);
    return { verlauf: sp, summe: summe_ };
  }
}

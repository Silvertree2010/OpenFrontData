/**
 * Politik: aus einer Beobachtung des Kerns wird eine Absicht.
 *
 * Alle `takt` Ticks (Standard 32, wie kalibriert) holt sie `kern.beobachtung()`, baut
 * daraus die Anfrage an den Inferenz-Server (Format wie `trainer/spielen.py`, POST /act),
 * wertet die Antwort aus und ruft `kern.sendeAbsicht`. Die Reihenfolge ist die aus
 * `viewer/ai-live-v33.patch` (`aiAnfrage.ts`, `aiZiel.ts`):
 *
 *   1. „ob handeln“: der Server prüft P(handeln) gegen die Schwelle und schickt sonst
 *      `no_op`. `opts.schwelle` ist eine zusätzliche Sperre im Client (Standard: keine,
 *      es gilt die Schwelle des Servers).
 *   2. Aktionstyp/Einheit/Zielspieler wählt der Server (Ziehen aus der Verteilung, 8650).
 *   3. Bis zu fünf Kandidatenzellen, beste zuerst. Die erste, die gegen die `legal`-Bits
 *      der Beobachtung zulässig ist, gewinnt (Top-5-Rückfall). Keine gültig → nichts.
 *
 * UNTERSCHIED zum Patch, bewusst: dort prüft die Engine jede Kachel einer Zelle
 * (`player.canBuild`, `canBuildTransportShip`, …). Hier gibt es keine Engine — die liegt
 * im Kern. Geprüft wird gegen die `legal`-Bits aus `cellFacts.ts` (DESIGN §6, Tabelle
 * `BIT` aus `trainer/tabellen.py`), also auf Zellebene. Die Absicht trägt darum die
 * **Zelle** (`zelle`), und der Kern löst sie mit seinem Engine-Spiegel auf eine Kachel
 * auf. `kachel` ist nur die Zellmitte als Rückfall, falls die Kartengrösse bekannt ist.
 *
 * Standard ist Zuschauen: gesendet wird nur, wenn `SENDEN_AN` wahr ist UND der Nutzer es
 * in der Einblendung eingeschaltet hat UND die Erlaubnis noch gilt (13.09.2026 12:00).
 *
 * ZWEI OFFENE PUNKTE an der Naht zum Kern und zum Server (beide nicht hier lösbar):
 *
 *   a) Der Weg zum Inferenz-Server. openfront.io läuft über https, der Server über
 *      http://arch.example:8650 — aktiver Mischinhalt, den der Browser aus einem
 *      Inhaltsskript heraus abweist. `opts.holen` ist darum austauschbar: entweder
 *      holt ein Hintergrunddienst der Erweiterung (mit `host_permissions`) und reicht
 *      die Antwort durch, oder der Server bekommt ein Zertifikat/einen https-Vorbau.
 *      Das Manifest gehört dem anderen Agenten; hier steht nur die Einstiegsstelle.
 *
 *   b) `ctx` (mapW, mapH, troops, gold, oppIds, oppSids, ownUnitIds, ownAttackIds) und
 *      die smallIDs der Gegner stehen nicht in der Schnittstelle. Der Kern hängt der
 *      Beobachtung darum `_anfrage` an (`beobachtung.ts`), die fertige Anfrage aus
 *      `AiAnfrage.baue` — die wird benutzt, wenn sie da ist. Fehlt sie, baut die Politik
 *      die Anfrage aus den Vektoren; dann fehlen dem Server die Kennungen (Angriff ohne
 *      `targetID`, MIRV ohne Ziel, `schiff_bewegen` ohne Einheiten-ID) und die
 *      Spielkonfiguration ist verstümmelt. Bauen, Hafen, Kriegsschiff, Atom, Wasserstoff
 *      und Spawn gehen auch so.
 */
import { SENDEN_AN } from "./schnittstelle.ts";
import type { Absicht, Beobachtung, Kern } from "./schnittstelle.ts";

// ── Grössen, wie im Training ──────────────────────────────────────────────────
export const GW = 180;
export const GH = 90;
export const NCELLS = GW * GH; // 16'200
export const MAPLEN = 18 * GH * GW; // 291'600
export const CELL_BYTES = NCELLS * 4; // 64'800

/** legal-Bit je Gruppe, wörtlich aus `trainer/tabellen.py` (BIT, mit legalBit1=1). */
export const BIT: Record<string, number> = {
  bau: 1,
  hafen: 2,
  boot: 3,
  atom: 7,
  wasserstoff: 7,
  mirv: 7,
  kriegsschiff: 4,
  schiff_bewegen: 5,
  spawn: 6,
};
/** Gruppen, die einen Zielspieler haben (Entwurf D0 §4.1). */
export const ZIEL_GRUPPEN = new Set(["boot", "atom", "wasserstoff", "mirv"]);

// ── Die Erlaubnis, portiert aus `oeffentlicheLobby.ts` ────────────────────────
export const ERLAUBNIS = {
  erteilt: true,
  von: "Entwickler von OpenFront (openfrontio)",
  am: "2026-09-12",
  art:
    "Serverseitig: die spielende IP ist als Bot markiert. Mitspieler sehen ein " +
    "Roboter-Emoji im Namen und „AI“ statt „player“. Die Kennzeichnung kommt vom " +
    "Server, nicht von dieser Erweiterung.",
  /** Danach ist die IP nicht mehr gekennzeichnet — dann wird nicht mehr gesendet. */
  laeuftAbAm: "2026-09-13T12:00:00",
};

export function erlaubnisEnde(): Date {
  return new Date(ERLAUBNIS.laeuftAbAm);
}

export function erlaubnisGueltig(jetzt: Date = new Date()): boolean {
  return ERLAUBNIS.erteilt && jetzt.getTime() < erlaubnisEnde().getTime();
}

export function abgelaufenText(): string {
  return (
    "Die Bot-Kennzeichnung der IP galt bis " +
    erlaubnisEnde().toLocaleString("de-CH") +
    ". Sie ist abgelaufen: ohne Kennzeichnung wäre das ein unmarkierter Bot unter " +
    "Menschen. Die Politik sendet darum nichts mehr."
  );
}

// ── Aus den Vektoren zurück in die Objekte, die der Server liest ──────────────
// Der Kern legt `own`, `opp` und `config` als flache Float32Array ab — ROHE Werte in
// der Reihenfolge, die `beobachtung.ts` festschreibt (nicht die featurisierte Form aus
// `env/featurize.py`; die rechnet der Server selbst). Hier bekommen sie ihre Namen
// zurück, damit `trainer/spielen.py` sie so liest wie im Training.
//
// Der Regelweg ist ein anderer: der Kern hängt der Beobachtung die fertige Anfrage als
// `_anfrage` an (`AiAnfrage.baue`, mit `ctx`, `sid`, `allies`, `opps` samt user/clan).
// Liegt sie vor, wird sie benutzt — dann geht nichts verloren. Der Weg über die Vektoren
// ist nur der Rückfall, wenn ein Kern ohne `_anfrage` liefert.

/** Reihenfolge von `beobachtung.ts:OWN_FELDER`. */
export const OWN_FELDER = [
  "troops", "gold", "tiles", "borderLen", "allies", "betrayals", "traitor",
  "tick", "alive", "troopsRatio", "boardShare", "boatsOut", "isLeader",
  "n_City", "n_Defense Post", "n_SAM Launcher", "n_Missile Silo", "n_Port", "n_Factory",
];
export const OWN_DIM = OWN_FELDER.length; // 19

/** Reihenfolge von `beobachtung.ts:OPP_FELDER`. */
export const OPP_FELDER = [
  "troops", "gold", "tiles", "ally", "sameTeam", "traitor", "human", "bordersMe",
  "contactShare", "borderToMe", "borderToOther", "borderToUnowned", "gapsRemaining",
  "annexProof", "allyTicksLeft", "isLeader", "hasSilo", "hasSam", "inClan",
];
export const OPP_DIM = OPP_FELDER.length; // 19
export const MAX_OPP = 24;

/** Reihenfolge von `beobachtung.ts:CONFIG_FELDER`. */
export const CONFIG_FELDER = [
  "maxPlayers", "bots", "infiniteGold", "infiniteTroops", "instantBuild",
  "disableNPCs", "disabledUnits", "gameMode", "difficulty",
];

/** Platz eines Feldes im own-Vektor; -1, wenn es das Feld nicht gibt. */
export function ownIdx(name: string): number {
  return OWN_FELDER.indexOf(name);
}
export function ownWert(own: ArrayLike<number>, name: string): number {
  const i = ownIdx(name);
  return i < 0 ? 0 : Number(own[i]) || 0;
}

function benenne(
  felder: string[],
  v: ArrayLike<number>,
  ab = 0,
): Record<string, number> {
  const o: Record<string, number> = {};
  for (let i = 0; i < felder.length; i++) o[felder[i]] = Number(v[ab + i]) || 0;
  return o;
}

export function ownDict(own: ArrayLike<number>): Record<string, number> {
  return benenne(OWN_FELDER, own);
}

/** Gegnerzeilen, nur die gesetzten Maskenplätze — genau die, die der Server zählt. */
export function oppListe(
  opp: ArrayLike<number>,
  maske: ArrayLike<number>,
): Record<string, unknown>[] {
  const raus: Record<string, unknown>[] = [];
  for (let i = 0; i < MAX_OPP && i * OPP_DIM < opp.length; i++) {
    if (!maske[i]) continue;
    raus.push(benenne(OPP_FELDER, opp, i * OPP_DIM));
  }
  return raus;
}

/**
 * Die Spielkonfiguration aus dem Vektor. Verlustbehaftet, darum nur der Rückfall:
 * `gameMode` und `difficulty` stehen im Spiel als Text und sind im Float32Array des
 * Kerns 0; `disabledUnits` ist dort nur noch eine Anzahl. Mit `_anfrage` kommt das
 * echte `info.config` mit und diese Funktion wird nicht gebraucht.
 */
export function configDict(c: ArrayLike<number>): Record<string, unknown> {
  const f = (n: string) => Number(c[CONFIG_FELDER.indexOf(n)]) || 0;
  return {
    maxPlayers: f("maxPlayers"),
    bots: f("bots"),
    infiniteGold: f("infiniteGold") >= 0.5,
    infiniteTroops: f("infiniteTroops") >= 0.5,
    instantBuild: f("instantBuild") >= 0.5,
    disableNPCs: f("disableNPCs") >= 0.5,
    disabledUnits: [],
  };
}

// ── Anfrage bauen ─────────────────────────────────────────────────────────────

/** Base64 aus rohen Bytes, blockweise (wie `aiAnfrage.ts`). */
export function b64(u8: Uint8Array): string {
  let s = "";
  for (let i = 0; i < u8.length; i += 0x8000) {
    s += String.fromCharCode.apply(
      null,
      Array.from(u8.subarray(i, i + 0x8000)) as number[],
    );
  }
  return btoa(s);
}

/** Zellblock roh: owner u16 LE ‖ own_frac u8 ‖ legal u8 (cellFacts.ts, 64'800 Byte). */
export function zellBlock(b: Beobachtung): Uint8Array {
  const out = new Uint8Array(CELL_BYTES);
  for (let i = 0; i < NCELLS; i++) {
    const v = b.owner[i] | 0;
    out[2 * i] = v & 0xff;
    out[2 * i + 1] = (v >>> 8) & 0xff;
  }
  out.set(b.frac.subarray(0, NCELLS), NCELLS * 2);
  out.set(b.legal.subarray(0, NCELLS), NCELLS * 3);
  return out;
}

export interface PolitikOpts {
  /** Basis des Inferenz-Servers, ohne Schrägstrich am Ende. */
  inf: string;
  /** Alle wie viele Ticks gefragt wird. Der Server darf den Wert überschreiben. */
  takt: number;
  /** Zusätzliche Client-Schwelle auf P(handeln); null = nur die Schwelle des Servers. */
  schwelle: number | null;
  /** Kartenmasse, falls der Kern sie nicht mitliefert (nur für die Rückfall-Kachel). */
  kartenBreite: number;
  kartenHoehe: number;
  /** Wie geholt wird. Austauschbar, weil eine https-Seite kein http:// laden darf. */
  holen: typeof fetch;
  jetzt: () => Date;
  protokollSchluessel: string;
}

export const OPTS_STANDARD: PolitikOpts = {
  inf: "http://arch.example:8650",
  takt: 32,
  schwelle: null,
  kartenBreite: 0,
  kartenHoehe: 0,
  holen: (...a: Parameters<typeof fetch>) => globalThis.fetch(...a),
  jetzt: () => new Date(),
  protokollSchluessel: "openfront-ki-protokoll",
};

/**
 * Zusatzfelder, die der Kern anhängt. Die Schnittstelle verlangt sie nicht und verbietet
 * sie nicht; `_anfrage` ist der Regelweg (siehe `beobachtung.ts`).
 */
type BeobachtungPlus = Beobachtung & {
  _anfrage?: Record<string, unknown>;
  sid?: number;
  allies?: number[];
  kartenBreite?: number;
  kartenHoehe?: number;
  ctx?: Record<string, unknown>;
};

/** Kartenmasse aus der Beobachtung (für die Rückfall-Kachel), sonst aus den Optionen. */
export function kartenMasse(b: Beobachtung, o: PolitikOpts): [number, number] {
  const p = b as BeobachtungPlus;
  const ctx = (p._anfrage?.ctx ?? p.ctx ?? {}) as Record<string, number>;
  return [
    Number(p.kartenBreite ?? ctx.mapW ?? o.kartenBreite) || 0,
    Number(p.kartenHoehe ?? ctx.mapH ?? o.kartenHoehe) || 0,
  ];
}

/** Anfrage im Format von `trainer/spielen.py` (POST /act). */
export function baueAnfrage(
  b: Beobachtung,
  o: PolitikOpts,
): Record<string, unknown> {
  const p = b as BeobachtungPlus;
  // Regelweg: die fertige Anfrage des Kerns. Sie trägt ctx, sid, allies und die
  // Gegner mit user/clan (Reputation) — nichts davon steckt in den Vektoren.
  if (p._anfrage) {
    return { ...p._anfrage, tick: b.tick, decide_every: o.takt };
  }
  const own = ownDict(b.own);
  const [W, H] = kartenMasse(b, o);
  const ctx = {
    mapW: W,
    mapH: H,
    troops: own.troops,
    gold: own.gold,
    oppIds: [],
    oppSids: [],
    ownUnitIds: [],
    ownAttackIds: [],
    ...(p.ctx ?? {}),
  };
  return {
    map_b64: b64(b.karte),
    cells_b64: b64(zellBlock(b)),
    own,
    opps: oppListe(b.opp, b.oppMaske),
    config: configDict(b.config),
    sid: p.sid ?? -1,
    allies: p.allies ?? [],
    tick: b.tick,
    decide_every: o.takt,
    ctx,
  };
}

// ── Antwort auswerten ─────────────────────────────────────────────────────────

export interface Antwort {
  intent?: Record<string, unknown> | null;
  kandidaten?: number[];
  gruppe?: string | null;
  einheit?: string | null;
  zielSid?: number | null;
  p_handeln?: number;
  value?: number;
  atype?: string;
  grund?: string;
  decide_every?: number;
}

/** Ist die Zelle für diese Gruppe zulässig? Prüfung gegen die `legal`-Bits. */
export function zelleErlaubt(
  b: Beobachtung,
  gruppe: string,
  zelle: number,
  zielSid: number | null,
): boolean {
  if (!Number.isInteger(zelle) || zelle < 0 || zelle >= NCELLS) return false;
  const bit = BIT[gruppe];
  if (bit === undefined) return false;
  if ((b.legal[zelle] & (1 << bit)) === 0) return false;
  // Ziel-Gruppen: die Zelle muss dem Zielspieler gehören (owner_major). Boot landet
  // auf herrenlosem Land (sid 0) oder beim Gegner; ohne bekanntes Ziel reicht das Bit.
  if (gruppe === "mirv" || gruppe === "boot") {
    if (zielSid !== null && zielSid >= 0 && b.owner[zelle] !== zielSid) return false;
  }
  return true;
}

/** Zellmitte als Kachel, nur wenn die Kartengrösse bekannt ist (sonst -1). */
export function zellMitte(zelle: number, W: number, H: number): number {
  if (!(W > 0 && H > 0)) return -1;
  const gx = zelle % GW;
  const gy = (zelle / GW) | 0;
  const x0 = Math.ceil((gx * W) / GW);
  const x1 = Math.min(W, Math.ceil(((gx + 1) * W) / GW));
  const y0 = Math.ceil((gy * H) / GH);
  const y1 = Math.min(H, Math.ceil(((gy + 1) * H) / GH));
  if (x1 <= x0 || y1 <= y0) return -1;
  const x = (x0 + x1 - 1) >> 1;
  const y = (y0 + y1 - 1) >> 1;
  return y * W + x;
}

/**
 * Antwort → Absicht oder null. Portiert aus `aiZiel.ts:intentAusAntwort`, die
 * Engine-Prüfung je Kachel ersetzt durch die `legal`-Bits der Zelle.
 */
export function absichtAus(
  b: Beobachtung,
  a: Antwort | null,
  o: PolitikOpts = OPTS_STANDARD,
): Absicht | null {
  const intent = a?.intent;
  if (!intent || typeof intent !== "object") return null;
  const art = String((intent as Record<string, unknown>).type ?? "");
  if (!art || art === "no_op") return null;
  if (o.schwelle !== null && (a?.p_handeln ?? 0) < o.schwelle) return null;

  const rest: Record<string, unknown> = { ...intent };
  delete rest.type;
  const gruppe = a?.gruppe ?? null;
  // Nicht-räumliche Aktionen (Angriff, Allianz, Emoji …) gehen unverändert durch.
  if (!gruppe || !a?.kandidaten || a.kandidaten.length === 0) {
    return { art, ...rest };
  }
  const [W, H] = kartenMasse(b, o);
  const zielSid = a.zielSid ?? null;
  for (const zelle of a.kandidaten.slice(0, 5)) {
    if (!zelleErlaubt(b, gruppe, zelle, zielSid)) continue;
    const kachel = zellMitte(zelle, W, H);
    const feld = art === "boat" ? "dst" : "tile";
    const raus: Absicht = { art, ...rest, gruppe, zelle };
    // Die Kachel löst der Kern mit seinem Engine-Spiegel auf; `tile`/`dst` ist nur
    // die Zellmitte als Rückfall und fehlt, wenn die Kartengrösse unbekannt ist.
    if (kachel >= 0) raus[feld] = kachel;
    else delete raus[feld];
    return raus;
  }
  return null; // keine der fünf Kandidatenzellen war legal
}

// ── Protokoll je Partie ───────────────────────────────────────────────────────

export interface ProtokollEintrag {
  begonnen: string;
  beendet: string | null;
  partie: string | null;
  sekunden: number | null;
  ergebnis: string | null;
  grund: string | null;
}

function speicher(): Storage | null {
  try {
    const s = globalThis.localStorage;
    if (!s) return null;
    s.getItem("__ki_probe__");
    return s;
  } catch {
    return null;
  }
}

export function protokollLesen(schluessel: string): ProtokollEintrag[] {
  try {
    const roh = JSON.parse(speicher()?.getItem(schluessel) || "[]");
    return Array.isArray(roh) ? roh : [];
  } catch {
    return [];
  }
}

function protokollSchreiben(schluessel: string, e: ProtokollEintrag[]): void {
  try {
    speicher()?.setItem(schluessel, JSON.stringify(e.slice(-500)));
  } catch {
    /* ohne Speicher kein Protokoll; die Politik läuft trotzdem */
  }
}

export function protokollText(schluessel: string): string {
  const zeilen = protokollLesen(schluessel).map((e) => {
    const d =
      e.sekunden === null
        ? "—"
        : `${Math.floor(e.sekunden / 60)}:${String(e.sekunden % 60).padStart(2, "0")}`;
    return [
      e.begonnen,
      e.partie ?? "—",
      d,
      e.ergebnis ?? "—",
      e.grund ?? "läuft",
    ].join("  |  ");
  });
  return [
    `Partien der KI (localStorage, Schlüssel ${schluessel})`,
    `Erlaubnis: ${ERLAUBNIS.von}, ${ERLAUBNIS.am}. Gültig bis ${erlaubnisEnde().toLocaleString("de-CH")}.`,
    ERLAUBNIS.art,
    "Beginn  |  Partie-ID  |  Dauer  |  Ergebnis  |  Ende",
    ...zeilen,
    `(${zeilen.length} Einträge)`,
  ].join("\n");
}

// ── Messwerte für die Einblendung ─────────────────────────────────────────────

export interface Messwerte {
  tick: number;
  gebiet: number; // Anteil an allen Landkacheln (boardShare)
  kacheln: number;
  truppen: number;
  gold: number;
  platz: number; // aus der Beobachtung geschätzt: Gegner mit mehr Kacheln + 1
  lebende: number;
  handlungen: number;
  handlungen_je_1000: number;
  p_handeln: number | null;
  letzte_aktion: string | null;
  sekunden: number;
  partie: string | null;
  senden: boolean;
  angehalten: boolean;
  erlaubnis_gueltig: boolean;
  erlaubnis_bis: string;
  fehler: string | null;
}

// ── Die Politik ───────────────────────────────────────────────────────────────

export class Politik {
  private opts: PolitikOpts;
  private laeuft = false;
  private angehalten = false;
  /** Vom Nutzer in der Einblendung ausdrücklich eingeschaltet. Standard: Zuschauen. */
  private sendenGewuenscht = false;
  private inArbeit = false;
  private handlungen = 0;
  private ersterTick: number | null = null;
  private letzterTick = 0;
  private pHandeln: number | null = null;
  private letzteAktion: string | null = null;
  private fehler: string | null = null;
  private eintrag: ProtokollEintrag | null = null;
  private begonnenMs = 0;
  private letzterStand: string | null = null;
  private horcher: ((m: Messwerte) => void)[] = [];

  private kern: Kern;

  // Kein Parameter-Property: die Erweiterung wird ohne TS-Übersetzung geladen
  // (Node strippt nur Typen, `private kern: Kern` im Kopf wäre nicht erasable).
  constructor(kern: Kern, opts: Partial<PolitikOpts> = {}) {
    this.kern = kern;
    this.opts = { ...OPTS_STANDARD, ...opts };
    this.opts.inf = this.opts.inf.replace(/\/+$/, "");
  }

  start(): void {
    if (this.laeuft) return;
    this.laeuft = true;
    // Gibt das Versprechen zurück (die Schnittstelle erlaubt das: `void` nimmt jeden
    // Rückgabewert an). So kann der Test auf einen Takt warten, ohne zu pollen.
    this.kern.aufTick((t) => this.tick(t) as unknown as void);
  }

  anhalten(): void {
    this.angehalten = true;
    this.melde();
  }
  weiter(): void {
    this.angehalten = false;
    this.melde();
  }
  istAngehalten(): boolean {
    return this.angehalten;
  }

  /** Senden ein/aus. Nur der Nutzer ruft das (Einblendung); Standard ist aus. */
  setzeSenden(an: boolean): boolean {
    this.sendenGewuenscht = an && this.darfSenden() === null;
    this.melde();
    return this.sendenGewuenscht;
  }
  sendetGerade(): boolean {
    return this.sendenGewuenscht && this.darfSenden() === null;
  }

  /**
   * null = senden erlaubt, sonst der Grund, warum nicht. Die Grenze mit dem Ablaufdatum
   * steht zuerst: sie ist die harte, und sie soll auch dann im Bild stehen, wenn ohnehin
   * schon `SENDEN_AN` aus ist.
   */
  darfSenden(): string | null {
    if (!erlaubnisGueltig(this.opts.jetzt())) return abgelaufenText();
    if (!SENDEN_AN) {
      return "SENDEN_AN ist aus (schnittstelle.ts) — die Erweiterung schaut nur zu.";
    }
    return null;
  }

  aufMesswerte(f: (m: Messwerte) => void): void {
    this.horcher.push(f);
  }

  protokoll(): string {
    return protokollText(this.opts.protokollSchluessel);
  }

  /** Ein Tick des Kerns. Handelt nur alle `takt` Ticks. */
  async tick(t: number): Promise<void> {
    this.letzterTick = t;
    const z = this.kern.zustand();
    this.partieVerfolgen(z.partie);
    if (this.angehalten || this.inArbeit) return this.melde();
    if (this.opts.takt < 1 || t % this.opts.takt !== 0) return this.melde();
    const b = this.kern.beobachtung();
    if (!b) return this.melde();
    if (b.startphase) {
      // Startphase: der Nutzer setzt seinen Startpunkt selbst (siehe ERWEITERUNG.md).
      this.letzteAktion = "Startphase — nichts tun";
      return this.melde(b);
    }
    if (!b.lebe) return this.melde(b);

    this.inArbeit = true;
    try {
      const a = await this.frage(b);
      if (a?.decide_every && a.decide_every >= 1) this.opts.takt = a.decide_every;
      this.pHandeln = typeof a?.p_handeln === "number" ? a.p_handeln : null;
      const abs = absichtAus(b, a, this.opts);
      if (!abs) {
        this.letzteAktion = a?.grund ?? a?.atype ?? "nichts";
        return;
      }
      const grund = this.darfSenden();
      if (grund !== null || !this.sendenGewuenscht) {
        this.letzteAktion =
          this.beschreibe(a, abs) + (grund !== null ? " (gesperrt)" : " (nur zuschauen)");
        if (grund !== null) this.fehler = grund;
        return;
      }
      const ok = this.kern.sendeAbsicht(abs);
      this.handlungen += ok ? 1 : 0;
      this.letzteAktion = this.beschreibe(a, abs) + (ok ? "" : " (Kern lehnte ab)");
    } catch (e) {
      this.fehler = String((e as Error)?.message ?? e).slice(0, 200);
    } finally {
      this.inArbeit = false;
      this.melde(b);
    }
  }

  private beschreibe(a: Antwort | null, abs: Absicht): string {
    const e = a?.einheit ? ` ${a.einheit}` : "";
    const z = typeof abs.zelle === "number" ? ` @${abs.zelle}` : "";
    return `${a?.atype ?? abs.art}${e}${z}`;
  }

  private async frage(b: Beobachtung): Promise<Antwort | null> {
    const r = await this.opts.holen(`${this.opts.inf}/act`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(baueAnfrage(b, this.opts)),
    });
    if (!r.ok) throw new Error(`Inferenz-Server ${r.status}`);
    const a = (await r.json()) as Antwort & { error?: string };
    if (a?.error) throw new Error(a.error);
    this.fehler = null;
    return a;
  }

  // ── Protokoll je Partie ─────────────────────────────────────────────────────
  private partieVerfolgen(partie: string | null): void {
    if (this.eintrag && this.eintrag.partie !== partie) {
      this.beende(partie === null ? "Partie beendet" : "neue Partie");
    }
    if (partie !== null && !this.eintrag) {
      this.eintrag = {
        begonnen: this.opts.jetzt().toISOString(),
        beendet: null,
        partie,
        sekunden: null,
        ergebnis: null,
        grund: null,
      };
      this.begonnenMs = this.opts.jetzt().getTime();
      this.handlungen = 0;
      this.ersterTick = null;
      const alle = protokollLesen(this.opts.protokollSchluessel);
      alle.push(this.eintrag);
      protokollSchreiben(this.opts.protokollSchluessel, alle);
    }
  }

  beende(grund: string): void {
    if (!this.eintrag || this.eintrag.beendet) return;
    this.eintrag.beendet = this.opts.jetzt().toISOString();
    this.eintrag.sekunden = Math.round(
      (this.opts.jetzt().getTime() - this.begonnenMs) / 1000,
    );
    this.eintrag.ergebnis = this.letzterStand;
    this.eintrag.grund = String(grund).slice(0, 80);
    const alle = protokollLesen(this.opts.protokollSchluessel);
    alle[alle.length - 1] = this.eintrag;
    protokollSchreiben(this.opts.protokollSchluessel, alle);
    this.eintrag = null;
    this.letzterStand = null;
  }

  // ── Messwerte ───────────────────────────────────────────────────────────────
  messwerte(b?: Beobachtung | null): Messwerte {
    const z = this.kern.zustand();
    const own = b ? ownDict(b.own) : null;
    let platz = 1;
    let lebende = 1;
    if (b) {
      // Platz ist in der Beobachtung nicht enthalten und wird geschätzt: wer mehr
      // Kacheln hat als ich, steht vor mir. `tiles` ist Platz 2 in OPP_FELDER.
      const meine = own!.tiles;
      const jTiles = OPP_FELDER.indexOf("tiles");
      for (let i = 0; i < MAX_OPP && i * OPP_DIM < b.opp.length; i++) {
        if (!b.oppMaske[i]) continue;
        lebende++;
        if (b.opp[i * OPP_DIM + jTiles] > meine) platz++;
      }
    }
    if (this.ersterTick === null && b) this.ersterTick = b.tick;
    const gelaufen = Math.max(1, this.letzterTick - (this.ersterTick ?? 0));
    const m: Messwerte = {
      tick: b?.tick ?? z.tick ?? this.letzterTick,
      gebiet: own?.boardShare ?? 0,
      kacheln: own?.tiles ?? 0,
      truppen: own?.troops ?? 0,
      gold: own?.gold ?? 0,
      platz,
      lebende,
      handlungen: this.handlungen,
      handlungen_je_1000: (this.handlungen * 1000) / gelaufen,
      p_handeln: this.pHandeln,
      letzte_aktion: this.letzteAktion,
      sekunden: this.begonnenMs
        ? Math.round((this.opts.jetzt().getTime() - this.begonnenMs) / 1000)
        : 0,
      partie: z.partie,
      senden: this.sendetGerade(),
      angehalten: this.angehalten,
      erlaubnis_gueltig: erlaubnisGueltig(this.opts.jetzt()),
      erlaubnis_bis: erlaubnisEnde().toLocaleString("de-CH"),
      fehler: this.fehler ?? z.fehler,
    };
    this.letzterStand =
      `Tick ${m.tick}, Platz ${m.platz}/${m.lebende}, ` +
      `Gebiet ${(m.gebiet * 100).toFixed(2)} %, ` +
      `${m.handlungen_je_1000.toFixed(1)} Handlungen/1000`;
    return m;
  }

  private melde(b?: Beobachtung | null): void {
    const m = this.messwerte(b);
    for (const f of this.horcher) {
      try {
        f(m);
      } catch {
        /* eine kaputte Anzeige darf die Politik nicht anhalten */
      }
    }
  }
}

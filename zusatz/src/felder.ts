/**
 * Zusatzfelder: was in der bestehenden Beobachtung fehlt (Bündnis-Zustand, Legalität,
 * laufende Angriffe, Raketen, Bezahlbarkeit).
 *
 * Der Pool (615 GB) wird nicht neu geschrieben. zusatz/src/lauf.ts spielt die Aufzeichnung
 * noch einmal nach und schreibt nur diese Felder in eine kleine Datei neben der Partie,
 * Zeile für Zeile deckungsgleich zu den bestehenden Metazeilen.
 *
 * Zwei Blöcke je Sample:
 *   OPP     24 Gegnerplätze in der Reihenfolge der Metazeile (opps[].id), je F_OPP Werte
 *   GLOBAL  F_GLOB Werte für den Spieler selbst
 * Beide Namenslisten stehen im Kopf der Datei, der Leser prüft sie.
 *
 * NEUE FELDER KOMMEN HINTEN DAZU:
 *   1. Namen ans Ende von OPP_FELDER bzw. GLOBAL_FELDER anhängen.
 *   2. In oppWerte()/globalWerte() an derselben Stelle schreiben.
 *   3. In trainer/zusatz_felder.py denselben Namen mit Normierung anhängen.
 * Ältere Dateien mit weniger Feldern bleiben lesbar, fehlende Werte sind 0.
 *
 * Werte sind ROH (Ticks, Truppen, Anzahl, Gold-Anteile). Normiert wird in Python, damit
 * sich die Normierung ohne neuen Lauf ändern lässt.
 *
 * Zwei Felder haben keinen Getter und werden aus den Zügen mitgeführt (Näherung, im
 * Bericht genannt): die Restsperre nach einer Anfrage (anfrage_sperre_rest) und die
 * Restzeit als Verräter (verraeter_rest). Beides stützt sich auf den Zeitpunkt des
 * jeweiligen Zuges plus die Dauer aus der Config.
 *
 * EIN MODUL FÜR ALLE WEGE. Offline-Lauf (lauf.ts), Arena (arena/arena.ts im Client) und
 * Erweiterung (engineSpiegel.ts, beobachtung.ts) benutzen genau diese Datei; Arena und
 * Erweiterung bekommen sie beim Bauen byte-gleich hineinkopiert (trainer/arena.py,
 * erweiterung/bauen.sh). Darum hängt sie an keinem Engine-Pfad: aus der Engine kommen nur
 * Typen (werden beim Bündeln gelöscht), und das UnitType-Enum reicht der Aufrufer hinein.
 * Die Reihenfolge der Tick-Haken steht in ZusatzSpur, nirgends sonst.
 */
import type { Game, Player } from "../../vendor/openfront/src/core/game/Game";

export const OPP_FELDER = [
  "anfrage_ein", "anfrage_ein_alter", "anfrage_ein_rest",     // seine Anfrage an mich
  "anfrage_aus", "anfrage_aus_alter", "anfrage_aus_rest",     // meine Anfrage an ihn
  "kann_anfragen", "anfrage_sperre_rest",                     // Legalität und Restsperre
  "abgemeldet", "freundlich", "verbuendet", "buendnis_rest",  // Bündniszustand
  "verlaengerung_ich", "verlaengerung_er",
  "verrat_an_mir", "buendnisse", "ally_greift_mich_an",
  "angriff_auf_mich", "angriff_auf_mich_rueckzug",
  "angriff_von_mir", "angriff_von_mir_rueckzug", "gebundene_truppen",
  "kann_angreifen", "kann_zielen", "kann_gold", "kann_truppen",
  "embargo_er", "embargo_ich", "ist_mein_ziel", "bin_sein_ziel",
] as const;

export const GLOBAL_FELDER = [
  "gebundene_truppen", "angriff_ein_summe", "angriff_aus_summe", "angriffe_ein", "angriffe_aus",
  "raketen_ein", "raketen_rest_min", "raketen_dist_min",
  "silos_bereit", "silo_timer_min", "sam_bereit", "sam_timer_min",
  "goldrate", "handelspartner", "verraeter_rest",
  // Messfelder zur SAM-Abdeckung: obsModel.scanTick stempelt sie unabhängig von Bau- und
  // Nachladezustand. Damit lässt sich zählen, wie oft die Karte dort falsch liegt.
  "sam_alle", "sam_nicht_bereit",
  "kosten_city", "kosten_port", "kosten_factory", "kosten_defense", "kosten_sam",
  "kosten_silo", "kosten_warship", "kosten_atom", "kosten_hydrogen", "kosten_mirv",
] as const;

/**
 * Eigene Einheiten und Angriffe, in genau der Reihenfolge der Metazeile (ownUnitIds bzw.
 * ownAttackIds). Der own_ref-Kopf zeigt auf diese Plätze (actions.MAX_OWN_REF = 128), heute
 * ins Leere: Abbrechen, Boot abbrechen, Kriegsschiff verschieben, Einheit löschen und
 * Aufwerten sind ohne diese Liste nicht entscheidbar.
 */
export const EINH_FELDER = ["art", "spalte", "zeile", "im_bau", "nachladen", "unterwegs", "alter"] as const;
export const ANGR_FELDER = ["truppen", "ziel_platz", "rueckzug", "alter"] as const;
export const F_OPP = OPP_FELDER.length;
export const F_GLOB = GLOBAL_FELDER.length;
export const F_EINH = EINH_FELDER.length;
export const F_ANGR = ANGR_FELDER.length;
export const MAX_OPP = 24;
export const MAX_EINH = 128;            // wie actions.MAX_OWN_REF
export const MAX_ANGR = 16;

/**
 * Art einer Einheit als Zahl; 0 = leerer Platz. Reihenfolge ist Teil des Formats und folgt
 * dem UnitType-Enum der Engine — vollständig, denn ownUnitIds enthält auch Geschosse,
 * SAM-Raketen, MIRV-Sprengköpfe und Züge, und own_ref kann auf jeden dieser Plätze zeigen.
 */
export const TYP_NAMEN = [
  "TransportShip", "Warship", "Shell", "SAMMissile", "Port",
  "AtomBomb", "HydrogenBomb", "TradeShip", "MissileSilo",
  "DefensePost", "SAMLauncher", "City", "MIRV", "MIRVWarhead",
  "Train", "Factory",
] as const;
const BAU_NAMEN: [string, string][] = [
  ["kosten_city", "City"], ["kosten_port", "Port"], ["kosten_factory", "Factory"],
  ["kosten_defense", "DefensePost"], ["kosten_sam", "SAMLauncher"],
  ["kosten_silo", "MissileSilo"], ["kosten_warship", "Warship"],
  ["kosten_atom", "AtomBomb"], ["kosten_hydrogen", "HydrogenBomb"],
  ["kosten_mirv", "MIRV"],
];
const NUKE_NAMEN = ["AtomBomb", "HydrogenBomb", "MIRVWarhead"];

/** UnitType-Enum der Engine (Werte sind Zeichenketten, z. B. Port = "Port"). */
export type UnitTypen = Record<string, any>;

/** TYP_NAMEN als Enum-Werte dieser Engine; fehlt einer, stimmt die Engine nicht. */
export function typListe(UT: UnitTypen): any[] {
  return TYP_NAMEN.map((n) => {
    if (UT[n] === undefined) throw new Error(`UnitType.${n} fehlt in dieser Engine`);
    return UT[n];
  });
}

function zahl(x: any): number {
  return typeof x === "bigint" ? Number(x) : Number(x ?? 0);
}

export class Zusatz {
  // Alles im Konstruktor gesetzt, keine Feld-Initialisierer: die Bäume bauen mit
  // useDefineForClassFields=false bzw. true, die Reihenfolge wäre sonst nicht dieselbe.
  private readonly game: Game;
  private readonly UT: UnitTypen;
  private readonly typNr: Map<any, number>;
  private readonly bauTypen: [string, any][];
  private readonly nukeTypen: any[];
  private readonly erstEinheit: Map<number, number>;     // Einheit-id → Tick der Entstehung
  private readonly erstAngriff: Map<string, number>;     // Angriff-id → erste Sichtung
  private readonly verrat: Map<number, Set<number>>;     // Brecher-sid → verratene sids
  private readonly bruchTick: Map<number, number>;       // Brecher-sid → letzter Bruch
  private readonly anfrageTick: Map<string, number>;     // "sid>sid" → letzte Anfrage
  private readonly goldVorher: Map<number, number>;
  private readonly goldRate: Map<number, number>;        // geglättete Zunahme je Tick

  constructor(game: Game, UT: UnitTypen) {
    this.game = game;
    this.UT = UT;
    this.typNr = new Map<any, number>(typListe(UT).map((t, i) => [t, i + 1]));
    this.bauTypen = BAU_NAMEN.map(([f, n]) => [f, UT[n]]);
    this.nukeTypen = NUKE_NAMEN.map((n) => UT[n]);
    this.erstEinheit = new Map();
    this.erstAngriff = new Map();
    this.verrat = new Map();
    this.bruchTick = new Map();
    this.anfrageTick = new Map();
    this.goldVorher = new Map();
    this.goldRate = new Map();
  }

  /**
   * Erste Sichtung der eigenen Angriffe, genau so, wie einheitenWerte sie beim Schreiben
   * einer Metazeile setzt (die ersten MAX_ANGR aus outgoingAttacks). Zur Laufzeit ruft
   * ZusatzSpur das nach dem Zeilenplan des Materialisierers auf, nicht bei jeder Anfrage.
   */
  sichte(ich: Player): void {
    const t = this.game.ticks();
    let k = 0;
    for (const at of ich.outgoingAttacks()) {
      if (k++ >= MAX_ANGR) break;
      if (!this.erstAngriff.has(at.id())) this.erstAngriff.set(at.id(), t);
    }
  }

  /** Vor executeNextTick: Züge mitlesen (Bündnisbruch und Anfragen haben keinen Getter). */
  merkeZug(turn: any): void {
    const g = this.game;
    const t = g.ticks();
    for (const it of (turn?.intents ?? []) as any[]) {
      if (!it?.clientID) continue;
      const a = g.playerByClientID(it.clientID);
      if (!a) continue;
      if (it.type === "allianceRequest" && it.recipient) {
        const b = this.spieler(it.recipient);
        if (b) this.anfrageTick.set(`${a.smallID()}>${b.smallID()}`, t);
      } else if (it.type === "breakAlliance" && it.recipient) {
        const b = this.spieler(it.recipient);
        if (!b || !a.isAlliedWith(b)) continue;                  // nur echte Brüche
        let s = this.verrat.get(a.smallID());
        if (!s) this.verrat.set(a.smallID(), (s = new Set()));
        s.add(b.smallID());
        this.bruchTick.set(a.smallID(), t);
      }
    }
  }

  /**
   * Nach executeNextTick mit den Updates des Ticks: neue Einheiten sind Unit-Updates mit einer
   * id, die noch nie gesehen wurde (wie in materializer/src/res.ts). Daraus kommt das Alter;
   * die Unit-Schnittstelle hat dafür keinen Getter.
   */
  merkeUpdates(updates: any[] | undefined): void {
    if (!updates) return;
    const t = this.game.ticks();
    for (const u of updates) {
      const id = u?.id;
      if (typeof id === "number" && !this.erstEinheit.has(id)) this.erstEinheit.set(id, t);
    }
  }

  /** Nach executeNextTick: Goldzuwachs je Spieler glätten (es gibt keinen Getter dafür). */
  merkeTick(): void {
    for (const p of this.game.players()) {
      const sid = p.smallID();
      const gold = zahl(p.gold());
      const vor = this.goldVorher.get(sid);
      if (vor !== undefined) {
        const d = Math.max(0, gold - vor);
        this.goldRate.set(sid, 0.9 * (this.goldRate.get(sid) ?? d) + 0.1 * d);
      }
      this.goldVorher.set(sid, gold);
    }
  }

  private spieler(id: string): Player | null {
    for (const p of this.game.allPlayers()) if (p.id() === id || p.clientID() === id) return p;
    return null;
  }

  /** Truppen, die in Angriffen und Booten gebunden sind (player.troops() ist nur der Leerlauf). */
  private gebunden(p: Player): number {
    let s = 0;
    for (const at of p.outgoingAttacks()) if (at.isActive()) s += at.troops();
    for (const u of p.units(this.UT.TransportShip)) s += zahl((u as any).troops?.() ?? 0);
    return s;
  }

  /** F_OPP Werte je Gegnerplatz, Reihenfolge wie die Metazeile. */
  oppWerte(ich: Player, oppSids: number[], out: Float32Array, offset: number): void {
    const g = this.game;
    const cfg = g.config();
    const t = g.ticks();
    const dauer = cfg.allianceRequestDuration();
    const sperre = cfg.allianceRequestCooldown();
    const buendnisDauer = cfg.allianceDuration();
    const meine = ich.smallID();

    const ein = new Map<number, number>(), aus = new Map<number, number>();
    for (const r of ich.incomingAllianceRequests()) {
      if (r.status() === "pending") ein.set(r.requestor().smallID(), r.createdAt());
    }
    for (const r of ich.outgoingAllianceRequests()) {
      if (r.status() === "pending") aus.set(r.recipient().smallID(), r.createdAt());
    }
    const aufMich = new Map<number, number>(), aufMichRz = new Map<number, number>();
    const angreifer = new Set<number>();
    for (const at of ich.incomingAttacks()) {
      if (!at.isActive()) continue;
      const s = at.attacker().smallID();
      aufMich.set(s, (aufMich.get(s) ?? 0) + at.troops());
      if (at.retreating()) aufMichRz.set(s, 1);
      angreifer.add(s);
    }
    const vonMir = new Map<number, number>(), vonMirRz = new Map<number, number>();
    for (const at of ich.outgoingAttacks()) {
      if (!at.isActive()) continue;
      const z = at.target();
      if (!z.isPlayer()) continue;
      const s = (z as Player).smallID();
      vonMir.set(s, (vonMir.get(s) ?? 0) + at.troops());
      if (at.retreating()) vonMirRz.set(s, 1);
    }
    const meineZiele = new Set(ich.targets().map((p) => p.smallID()));

    for (let k = 0; k < oppSids.length && k < MAX_OPP; k++) {
      const p = g.playerBySmallID(oppSids[k]);
      const b = offset + k * F_OPP;
      if (!p?.isPlayer()) continue;                               // Platz bleibt 0
      const o = p as Player;
      const sid = oppSids[k];
      const ce = ein.get(sid), ca = aus.get(sid);
      const bund = ich.allianceWith(o);
      const letzte = this.anfrageTick.get(`${meine}>${sid}`);
      const verb = o.allies();
      let i = b;
      out[i++] = ce === undefined ? 0 : 1;
      out[i++] = ce === undefined ? 0 : t - ce;
      out[i++] = ce === undefined ? 0 : Math.max(0, dauer - (t - ce));
      out[i++] = ca === undefined ? 0 : 1;
      out[i++] = ca === undefined ? 0 : t - ca;
      out[i++] = ca === undefined ? 0 : Math.max(0, dauer - (t - ca));
      out[i++] = ich.canSendAllianceRequest(o) ? 1 : 0;
      out[i++] = letzte === undefined ? 0 : Math.max(0, sperre + dauer - (t - letzte));
      out[i++] = o.isDisconnected() ? 1 : 0;
      out[i++] = ich.isFriendly(o) ? 1 : 0;                       // abgemeldeter Verbündeter: 0
      out[i++] = ich.isAlliedWith(o) ? 1 : 0;
      out[i++] = bund ? Math.max(0, buendnisDauer - (t - bund.createdAt())) : 0;
      out[i++] = bund?.agreedToExtend(ich) ? 1 : 0;
      out[i++] = bund?.agreedToExtend(o) ? 1 : 0;
      out[i++] = this.verrat.get(sid)?.has(meine) ? 1 : 0;
      out[i++] = verb.length;
      out[i++] = verb.some((v) => angreifer.has(v.smallID())) ? 1 : 0;
      out[i++] = aufMich.get(sid) ?? 0;
      out[i++] = aufMichRz.get(sid) ?? 0;
      out[i++] = vonMir.get(sid) ?? 0;
      out[i++] = vonMirRz.get(sid) ?? 0;
      out[i++] = this.gebunden(o);
      out[i++] = ich.canAttackPlayer(o) ? 1 : 0;                  // enthält isImmune
      out[i++] = ich.canTarget(o) ? 1 : 0;
      out[i++] = ich.canDonateGold(o) ? 1 : 0;
      out[i++] = ich.canDonateTroops(o) ? 1 : 0;
      out[i++] = o.hasEmbargoAgainst(ich) ? 1 : 0;
      out[i++] = ich.hasEmbargoAgainst(o) ? 1 : 0;
      out[i++] = meineZiele.has(sid) ? 1 : 0;
      out[i++] = o.targets().some((x) => x.smallID() === meine) ? 1 : 0;
    }
  }

  /**
   * Eigene Einheiten und Angriffe in der Reihenfolge der Metazeile, damit own_ref direkt
   * auf einen Eintrag zeigt. Nicht besetzte Plätze bleiben 0 (art 0 = leer).
   * markieren: der Aufruf ist eine Metazeile und zählt als Sichtung (Offline-Lauf). Zur
   * Laufzeit false — dort sichtet der Zeilenplan (ZusatzSpur), damit das Alter eines
   * Angriffs nicht davon abhängt, wie oft gefragt wird.
   */
  einheitenWerte(ich: Player, unitIds: number[], attackIds: string[], oppSids: number[],
                 outE: Float32Array, offE: number, outA: Float32Array, offA: number,
                 markieren = true): void {
    const g = this.game;
    const t = g.ticks();
    const nach = new Map<number, any>();
    for (const u of ich.units()) nach.set(u.id(), u);
    for (let k = 0; k < unitIds.length && k < MAX_EINH; k++) {
      const u = nach.get(unitIds[k]);
      if (!u) continue;                                          // Platz bleibt 0 (Einheit weg)
      const tile = u.tile();
      const b = offE + k * F_EINH;
      outE[b + 0] = this.typNr.get(u.type()) ?? 0;
      outE[b + 1] = ((g.x(tile) * 180) / g.width()) | 0;         // Zelle wie obs.ts, in float16 exakt
      outE[b + 2] = ((g.y(tile) * 90) / g.height()) | 0;
      outE[b + 3] = u.isUnderConstruction?.() ? 1 : 0;
      outE[b + 4] = u.isInCooldown?.() ? 1 : 0;
      outE[b + 5] = u.targetTile?.() !== undefined ? 1 : 0;      // unterwegs
      outE[b + 6] = t - (this.erstEinheit.get(unitIds[k]) ?? t);
    }
    const platz = new Map<number, number>(oppSids.map((s, i) => [s, i]));
    const meine = new Map<string, any>();
    for (const at of ich.outgoingAttacks()) meine.set(at.id(), at);
    for (let k = 0; k < attackIds.length && k < MAX_ANGR; k++) {
      const at = meine.get(attackIds[k]);
      if (!at) continue;
      if (markieren && !this.erstAngriff.has(attackIds[k])) this.erstAngriff.set(attackIds[k], t);
      const z = at.target();
      const b = offA + k * F_ANGR;
      outA[b + 0] = at.troops();
      outA[b + 1] = z.isPlayer() ? (platz.get((z as Player).smallID()) ?? MAX_OPP) : MAX_OPP + 1;
      outA[b + 2] = at.retreating() ? 1 : 0;
      outA[b + 3] = t - (this.erstAngriff.get(attackIds[k]) ?? t);   // ab erster Sichtung
    }
  }

  /** F_GLOB Werte für den Spieler selbst. */
  globalWerte(ich: Player, out: Float32Array, offset: number): void {
    const g = this.game;
    const cfg = g.config();
    const t = g.ticks();
    let einSum = 0, einZahl = 0, ausSum = 0, ausZahl = 0;
    for (const at of ich.incomingAttacks()) if (at.isActive()) { einSum += at.troops(); einZahl++; }
    for (const at of ich.outgoingAttacks()) if (at.isActive()) { ausSum += at.troops(); ausZahl++; }

    let raketen = 0, restMin = 0, distMin = 0;
    for (const u of g.units(this.nukeTypen[0], this.nukeTypen[1], this.nukeTypen[2])) {
      const ziel = u.targetTile();
      if (ziel === undefined || !g.isValidRef(ziel)) continue;
      if (g.ownerID(ziel) !== ich.smallID()) continue;
      const d = g.manhattanDist(u.tile(), ziel);
      const v = Math.max(1e-6, cfg.nukeSpeed(u.type()));
      const rest = d / v;
      if (raketen === 0 || rest < restMin) { restMin = rest; distMin = d; }
      raketen++;
    }
    const bereit = (typ: any) => {
      let n = 0, timer = 0;
      for (const u of ich.units(typ)) {
        if (!u.isActive() || u.isUnderConstruction()) continue;
        if (!u.isInCooldown()) n++;
        const q = u.missileTimerQueue?.() ?? [];
        for (const x of q) if (timer === 0 || x < timer) timer = x;
      }
      return [n, timer];
    };
    const [silos, siloT] = bereit(this.UT.MissileSilo);
    const [sams, samT] = bereit(this.UT.SAMLauncher);
    const gold = Math.max(1, zahl(ich.gold()));
    const verraeter = ich.isTraitor()
      ? Math.max(0, cfg.traitorDuration() - (t - (this.bruchTick.get(ich.smallID()) ?? t)))
      : 0;

    let i = offset;
    out[i++] = this.gebunden(ich);
    out[i++] = einSum;
    out[i++] = ausSum;
    out[i++] = einZahl;
    out[i++] = ausZahl;
    out[i++] = raketen;
    out[i++] = restMin;
    out[i++] = distMin;
    out[i++] = silos;
    out[i++] = siloT;
    out[i++] = sams;
    out[i++] = samT;
    out[i++] = this.goldRate.get(ich.smallID()) ?? 0;
    out[i++] = ich.tradingPartners().length;
    out[i++] = verraeter;
    let samAlle = 0, samNicht = 0;
    for (const u of g.units(this.UT.SAMLauncher)) {
      if (!u.isActive()) continue;
      samAlle++;
      if (u.isUnderConstruction() || u.isInCooldown()) samNicht++;
    }
    out[i++] = samAlle;
    out[i++] = samNicht;
    for (const [, typ] of this.bauTypen) {
      let kosten = 0;
      try {
        // Signatur am Aufzeichnungs-Commit: cost(game, player, extraUnits?)
        kosten = zahl((cfg.unitInfo(typ) as any).cost?.(g, ich));
      } catch {
        kosten = 0;
      }
      out[i++] = kosten / gold;                                   // Anteil am eigenen Gold
    }
  }
}

/**
 * Zeilenplan des Materialisierers v2 (materializer/src/materialize.ts): an welchen Ticks ein
 * Spieler eine Metazeile bekommt — bei jedem eigenen Zug (Folgeangriffe auf dasselbe Ziel
 * innerhalb MERGE_TICKS zählen nicht) und sonst jeden NOOP_EVERY-ten Tick. Das braucht nur
 * das Alter der Angriffe (ab erster Sichtung in einer Metazeile). Parameter stehen im hdr
 * jeder Partie (params); für den Pool v2 sind es 200 und 30 (POOL_PLAN).
 */
export interface Zeilenplan {
  noopEvery: number;
  mergeTicks: number;
  /** Math.abs(simpleHash(cid)) % noopEvery, simpleHash aus der Engine (core/Util.ts). */
  phase: (cid: string) => number;
}
export const POOL_PLAN = { noopEvery: 200, mergeTicks: 30 } as const;

export interface ZusatzWerte { opp: Float32Array; glob: Float32Array; einh: Float32Array; angr: Float32Array }

export function leereWerte(n = 1): ZusatzWerte {
  return { opp: new Float32Array(n * MAX_OPP * F_OPP), glob: new Float32Array(n * F_GLOB),
           einh: new Float32Array(n * MAX_EINH * F_EINH), angr: new Float32Array(n * MAX_ANGR * F_ANGR) };
}

/**
 * Die Zusatzfelder über eine laufende Partie. Einzige Stelle, an der die Reihenfolge der
 * Haken steht:
 *
 *   an Tick T (game.ticks() === T, vor dem Zug):  Felder rechnen (schreibe/werte)
 *   dann                                           vorTick(turn T)
 *   runner.addTurn(turn); runner.executeNextTick()
 *   danach                                         nachTick(Unit-Updates dieses Ticks)
 *
 * Ohne Zeilenplan ist jeder Aufruf von schreibe() eine Metazeile und sichtet die Angriffe
 * (Offline-Lauf, lauf.ts). Mit Zeilenplan (Arena, Erweiterung) sichtet vorTick() an genau
 * den Ticks, an denen der Materialisierer für einen verfolgten Spieler eine Zeile
 * geschrieben hätte, und Anfragen ändern den Zustand nicht. So hängt kein Feld davon ab,
 * wie oft gefragt wird. Die Spur muss ab Tick 0 mitlaufen (Verlauf: Gold, Anfragen,
 * Brüche, Entstehung der Einheiten).
 */
export class ZusatzSpur {
  readonly zus: Zusatz;
  readonly game: Game;
  private readonly plan: Zeilenplan | null;
  private readonly verfolgt: Set<string>;
  private readonly fenster: Map<string, number>;          // "cid ziel" → Beginn des Angriffsfensters
  private readonly getrennt: Set<string>;
  /** Für Tests: wird bei jeder nachgebildeten Metazeile gerufen (cid, Tick). */
  beiZeile: ((cid: string, t: number) => void) | null;

  constructor(game: Game, UT: UnitTypen, plan: Zeilenplan | null = null) {
    this.game = game;
    this.zus = new Zusatz(game, UT);
    this.plan = plan;
    this.verfolgt = new Set();
    this.fenster = new Map();
    this.getrennt = new Set();
    this.beiZeile = null;
  }

  /** Spieler (clientID), dessen Zeilenplan nachgebildet wird — der, den die KI steuert. */
  verfolge(cid: string): void {
    if (cid) this.verfolgt.add(cid);
  }

  /** Vor runner.addTurn/executeNextTick des Zugs, solange game.ticks() === turn.turnNumber. */
  vorTick(turn: any): void {
    if (this.plan && this.verfolgt.size) this.zeilenplan(turn);
    this.zus.merkeZug(turn);
  }

  /** Nach executeNextTick, mit gu.updates[GameUpdateType.Unit] dieses Ticks. */
  nachTick(unitUpdates: any[] | undefined): void {
    this.zus.merkeUpdates(unitUpdates);
    this.zus.merkeTick();
  }

  /** Felder für einen Spieler an Platz i der Blöcke w. teil nur zum Eingrenzen (lauf.ts). */
  schreibe(ich: Player, oppSids: number[], unitIds: number[], attackIds: string[],
           w: ZusatzWerte, i = 0, teil = "alles"): void {
    if (teil === "alles" || teil === "opp") this.zus.oppWerte(ich, oppSids, w.opp, i * MAX_OPP * F_OPP);
    if (teil === "alles" || teil === "global") this.zus.globalWerte(ich, w.glob, i * F_GLOB);
    if (teil === "alles" || teil === "einheiten") {
      this.zus.einheitenWerte(ich, unitIds, attackIds, oppSids, w.einh, i * MAX_EINH * F_EINH,
                              w.angr, i * MAX_ANGR * F_ANGR, this.plan === null);
    }
  }

  werte(ich: Player, oppSids: number[], unitIds: number[], attackIds: string[]): ZusatzWerte {
    const w = leereWerte(1);
    this.schreibe(ich, oppSids, unitIds, attackIds, w, 0);
    return w;
  }

  /** Nachbildung von materialize.ts (Züge, Angriffsfenster, Nichtstun-Takt, abgemeldet). */
  private zeilenplan(turn: any): void {
    const g = this.game, plan = this.plan!;
    const T = g.ticks();
    const intents: any[] = turn?.intents ?? [];
    for (const i of intents) {
      if (i?.type === "mark_disconnected" && i.clientID) {
        if (i.isDisconnected === false) this.getrennt.delete(i.clientID);
        else this.getrennt.add(i.clientID);
      }
    }
    for (const [k, s] of this.fenster) if (s + plan.mergeTicks <= T) this.fenster.delete(k);
    if (g.inSpawnPhase()) return;                   // Spawn-Zeilen: noch keine Angriffe
    for (const cid of this.verfolgt) {
      const p = g.playerByClientID(cid) as Player | null;
      let gehandelt = false, zeile = false;
      for (const i of intents) {
        if (i?.clientID !== cid || i.type === "mark_disconnected" || i.type === "spawn") continue;
        gehandelt = true;
        if (!p || !p.isAlive()) continue;
        if (i.type === "attack" && plan.mergeTicks > 0) {
          const k = `${cid} ${JSON.stringify(i.targetID ?? null)}`;
          const s = this.fenster.get(k);
          if (s !== undefined && T - s < plan.mergeTicks) continue;   // zusammengefasst, keine Zeile
          this.fenster.set(k, T);
        }
        zeile = true;
      }
      if (!gehandelt && p && p.isAlive() && !this.getrennt.has(cid)
          && (T + plan.phase(cid)) % plan.noopEvery === 0) zeile = true;
      if (zeile && p) {
        this.zus.sichte(p);
        this.beiZeile?.(cid, T);
      }
    }
  }
}

/** Fingerabdruck der Feldlisten (FNV-1a 32). Der Server prüft ihn gegen zusatz_felder.py. */
export function zusatzSig(): string {
  const s = [OPP_FELDER.join(","), GLOBAL_FELDER.join(","), EINH_FELDER.join(","), ANGR_FELDER.join(","),
             `${MAX_OPP},${MAX_EINH},${MAX_ANGR}`].join("|");
  let h = 0x811c9dc5;
  for (let i = 0; i < s.length; i++) h = Math.imul(h ^ s.charCodeAt(i), 0x01000193) >>> 0;
  return h.toString(16).padStart(8, "0");
}

function b64(u8: Uint8Array): string {
  let s = "";
  for (let i = 0; i < u8.length; i += 0x8000) {
    s += String.fromCharCode.apply(null, Array.from(u8.subarray(i, i + 0x8000)) as any);
  }
  return btoa(s);
}

/**
 * Anfrage-Felder für den Inferenz-Server: Blöcke opp ‖ global ‖ einheiten ‖ angriffe als
 * float32 LE, base64. ctx ist das ctx derselben Anfrage (AiAnfrage.baue): oppSids,
 * ownUnitIds und ownAttackIds legen fest, welcher Platz welcher Gegner bzw. welche Einheit
 * ist — own_ref zeigt in genau diese Listen.
 */
export function zusatzAnfrage(spur: ZusatzSpur, ich: Player, ctx: any): Record<string, unknown> {
  const w = spur.werte(ich, ctx?.oppSids ?? [], ctx?.ownUnitIds ?? [], ctx?.ownAttackIds ?? []);
  const alle = new Float32Array(w.opp.length + w.glob.length + w.einh.length + w.angr.length);
  let o = 0;
  for (const a of [w.opp, w.glob, w.einh, w.angr]) { alle.set(a, o); o += a.length; }
  return { zusatz_b64: b64(new Uint8Array(alle.buffer)), zusatz_sig: zusatzSig(),
           zusatz_tick: spur.game.ticks() };
}

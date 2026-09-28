/**
 * res_tile / res_kind / res_unit_id / res_dt per Beobachtung (DESIGN §5.2).
 *
 * Gelesen an 88cc95d8, 8b45be57 und 115da032 (Stellen @88). Die betroffenen
 * Executions sind an allen drei gleich: Construction, Transport, Upgrade,
 * MoveWarship und Warship.init per diff identisch, Nuke nur um nukeState ergänzt.
 *
 * - UnitUpdate trägt an allen drei id, unitType, ownerID, pos, level, targetTile
 *   und warshipState (GameUpdates.ts UnitUpdate, UnitImpl.toUpdate). toUpdate()
 *   baut bei jedem addUpdate einen frischen Schnappschuss, warshipState als Kopie.
 * - Eine neue Einheit sendet ihre erste Update in PlayerImpl.buildUnit, direkt
 *   nach dem Konstruktor. Der Konstruktor setzt targetTile und patrolTile schon
 *   aus den Parametern (UnitImpl.ts:59-78). Die erste Update trägt also die
 *   Landekachel (Boot), das Detonationszentrum (Nuke/MIRV) und den
 *   Patrouillenpunkt (Kriegsschiff). Kein Nachlesen nötig.
 * - Zug der ersten Update. addExecution legt in unInitExecs, und
 *   GameImpl.executeNextTick ruft init() nach den tick() der bestehenden
 *   Executions im selben Zug:
 *     Bauwerk     ConstructionExecution.tick → buildUnit                     T+1
 *     Warship     Construction.tick T+1 → WarshipExecution.init → buildUnit  T+1
 *     Nuke, MIRV  Construction.tick T+1 → Nuke/Mirv.init T+1 → .tick        T+2
 *     Boot        TransportShipExecution.init → buildUnit                    T
 *     Upgrade     UpgradeStructureExecution.init → increaseLevel → Update    T
 *   Die Fenster aus DESIGN §5.2 enthalten diese Züge. "Strikt" ist der erste
 *   Wert der Tabelle; ein Fund im tolerierten Zusatz-Zug zählt als `late`.
 *   Nach dem Code kann `late` nicht vorkommen; die Zahl prüft das.
 * - move_warship: Hier reicht die Update nicht. updateWarshipState sendet nur,
 *   wenn sich patrolTile ändert; ein zweiter Klick auf denselben Punkt sendet
 *   nichts. Deshalb wird nach Zug T direkt gelesen: game.unit(id) und das Feld
 *   _warshipState.patrolTile. Nicht der Getter warshipState(), der schreibt
 *   isInCombat ins Zustandsobjekt. Der geschriebene Wert wäre derselbe, den die
 *   Engine im nächsten Zug selbst schreibt, aber das Feld zu lesen ist ohne
 *   jeden Schreibzugriff. Das Feld heisst an allen drei Commits gleich.
 * - spawn: keine Unit-Update. Nach Zug T+1 gelesen: player.spawnTile() (reiner
 *   Getter, PlayerImpl.ts:626) und der Besitz um den Klick. getSpawn(center)
 *   gibt center = Klick zurück; bei Erfolg ist spawnTile der Klick. Scheitert
 *   getSpawn, hat der Spieler vorher schon alle Kacheln abgegeben
 *   (SpawnExecution.tick: relinquish vor getSpawn) und besitzt danach keine.
 *   Deshalb gilt der Spawn genau dann, wenn spawnTile == Klick ist und der Spieler
 *   im 9×9-Quadrat um den Klick eine Kachel besitzt (die Spawnscheibe hat Radius 4).
 *   DESIGN sagt "Kacheln, die er vorher nicht besass". Wörtlich genommen wäre ein
 *   erfolgreicher zweiter Klick auf dieselbe Stelle ungültig, weil der Spieler dort
 *   dieselben Kacheln zurückerobert. Der Test hier trennt Erfolg und Scheitern
 *   exakt und hat diese Lücke nicht.
 * - upgrade: game.unit(unitId) im Entscheidungstick (nur aktive Einheiten);
 *   tile() und level() sind reine Getter.
 * - Boot: owner(targetTile) wird nach dem Zug gelesen (map.ownerID, reiner Getter).
 *   Die Engine wählt die Landekachel in init, also nach allen tick() des Zugs;
 *   danach ändert sich der Besitz im selben Zug nicht mehr.
 *
 * Neue Einheit = UnitUpdate mit id > grösster bisher gesehener id (IDs laufen
 * fortlaufend, jede Einheit meldet sich in ihrem Bauzug). Zuordnung (DESIGN §5.2):
 * gierig nach aufsteigender ID zum ältesten offenen Sample desselben Spielers und
 * Typs, dessen Fenster den Zug enthält und das die Bedingung erfüllt:
 *   Bauwerk, Port  euklid²(pos, Klick) < 225 (validStructureSpawnTiles, Radius 15;
 *                  portSpawn verlangt dasselbe)
 *   Warship        warshipState.patrolTile == Klick
 *   Nuke, MIRV     targetTile == Klick
 *   Boot           owner(targetTile) == dst_owner und manhattan(targetTile, Klick) ≤ 50
 * Ein gescheiterter Klick erzeugt keine Einheit und kann keine fremde schlucken.
 * Nuke mit amount > 1: das Sample bekommt die erste Einheit, weitere passende im
 * selben Fenster zählen als extra_units.
 *
 * res_unit_id wird bei jeder neuen Einheit gesetzt (Bauwerk, Boot, Nuke,
 * Warship). DESIGN nennt sie nur für Warship und Upgrade, sonst ist sie -1.
 * Das ist eine Ergänzung: die ID kostet nichts und verbindet das Sample mit
 * dem Einheiten-Log aus Tier 1.
 *
 * Es wird nur gelesen. Spielzustand und Zufall bleiben unberührt.
 */
import { Game, UnitType } from "../../vendor/openfront/src/core/game/Game";
import { GameUpdateType, GameUpdateViewData } from "../../vendor/openfront/src/core/game/GameUpdates";

type Cls = "struct" | "warship" | "nuke" | "boat" | "upgrade" | "move" | "spawn";

/** Fenster relativ zum Entscheidungszug T: [start, end], strikt bis strict (siehe Kopf). */
const WIN: Record<Cls, { start: number; strict: number; end: number }> = {
  struct: { start: 1, strict: 1, end: 2 },
  warship: { start: 1, strict: 1, end: 2 },
  nuke: { start: 1, strict: 3, end: 3 },
  boat: { start: 0, strict: 0, end: 1 },
  upgrade: { start: 0, strict: 0, end: 1 },
  move: { start: 0, strict: 0, end: 0 },
  spawn: { start: 1, strict: 1, end: 1 },
};
/** Grösstes Fensterende, für den Schnitt (§2.7). */
export const RES_MAX_WINDOW = 3;

const STRUCT = new Set<string>([
  UnitType.City, UnitType.DefensePost, UnitType.SAMLauncher,
  UnitType.MissileSilo, UnitType.Factory, UnitType.Port,
]);
const NUKE = new Set<string>([UnitType.AtomBomb, UnitType.HydrogenBomb, UnitType.MIRV]);

/** Räumlich laut DESIGN §5.2: hat eine Klickkachel. */
export function isSpatial(intent: any): boolean {
  const t = intent?.type;
  return t === "build_unit" || t === "boat" || t === "move_warship" || t === "spawn";
}

/** Bekommt res_*-Felder: räumlich + Upgrade. */
export function needsRes(intent: any): boolean {
  return isSpatial(intent) || intent?.type === "upgrade_structure";
}

/** Rohe Klickkachel eines räumlichen Intents, sonst -1. */
export function clickOf(intent: any): number {
  switch (intent?.type) {
    case "build_unit":
    case "move_warship":
    case "spawn":
      return intent.tile;
    case "boat":
      return intent.dst;
  }
  return -1;
}

/** Schlüssel für die .ok-Statistik: Einheitentyp bei build_unit, sonst Intent-Typ. */
export function resType(intent: any): string {
  return intent.type === "build_unit" ? String(intent.unit) : String(intent.type);
}

function clsOf(intent: any): Cls | null {
  switch (intent.type) {
    case "build_unit":
      if (STRUCT.has(intent.unit)) return "struct";
      if (intent.unit === UnitType.Warship) return "warship";
      if (NUKE.has(intent.unit)) return "nuke";
      return null;
    case "boat": return "boat";
    case "upgrade_structure": return "upgrade";
    case "move_warship": return "move";
    case "spawn": return "spawn";
  }
  return null;
}

/** Die res-Felder im ungültigen Zustand (DESIGN §5.2, Ausnahme Warship/Nuke). */
export function invalidRes(cls: Cls | null, click: number) {
  return { res_tile: cls === "warship" || cls === "nuke" ? click : -1, res_kind: 2, res_unit_id: -1, res_dt: -1 };
}

export interface Pending {
  line: any | null;     // Metaobjekt, solange offen; danach null (Speicher)
  ref: unknown;         // Sample des Aufrufers (für Nachträge beim Schnitt)
  type: string;         // Statistik-Schlüssel
  cls: Cls | null;      // null = kein beobachtbarer Typ, sofort ungültig
  utype: string;        // Einheitentyp der neuen Einheit
  sid: number;
  click: number;
  dstOwner: number;     // Boot: Besitzer der Klickkachel im Entscheidungstick
  T: number;            // Entscheidungszug
  start: number;
  strict: number;
  end: number;
  unitId: number;       // upgrade
  unitIds: number[];    // move_warship
  tile: number;         // upgrade: Kachel im Entscheidungstick
  level: number;        // upgrade: Level im Entscheidungstick
  amount: number;       // upgrade/nuke: gewünschte Anzahl
  got: number;          // upgrade: zugeordnete Stufen; nuke: zugeordnete Einheiten
  extra: number;        // nuke: weitere Einheiten (extra_units)
  done: boolean;
  ok: boolean;
  late: boolean;
  release: (() => void) | null;
}

export class Resolver {
  private maxId = 0;
  private readonly byOwnerType = new Map<string, Pending[]>();
  private readonly upg = new Map<number, Pending[]>();
  private readonly upgLevel = new Map<number, number>();
  private readonly checkAt = new Map<number, Pending[]>();
  private open: Pending[] = [];
  private extras: Pending[] = [];       // aufgelöste Nukes mit amount > 1, Fenster noch offen
  private readonly all: Pending[] = [];

  constructor(private readonly W: number, private readonly H: number) {}

  private valid(ref: number): boolean {
    return Number.isInteger(ref) && ref >= 0 && ref < this.W * this.H;
  }
  private dist2(a: number, b: number): number {
    const dx = (a % this.W) - (b % this.W), dy = Math.floor(a / this.W) - Math.floor(b / this.W);
    return dx * dx + dy * dy;
  }
  private manhattan(a: number, b: number): number {
    return Math.abs((a % this.W) - (b % this.W)) + Math.abs(Math.floor(a / this.W) - Math.floor(b / this.W));
  }

  /**
   * Im Entscheidungstick (vor executeNextTick(T)): Startwerte eintragen (ungültig)
   * und die Beobachtung vorbereiten. Liest nur. Wirft, wenn die Engine beim Lesen
   * wirft; der Aufrufer zählt das als res-Fehler.
   */
  prepare(line: any, intent: any, sid: number, T: number, game: Game, dstOwner: number): Pending {
    const cls = clsOf(intent);
    const click = clickOf(intent);
    Object.assign(line, invalidRes(cls, click));
    const w = cls ? WIN[cls] : { start: 0, strict: -1, end: -1 };
    const p: Pending = {
      line, ref: null, type: resType(intent), cls,
      utype: cls === "boat" ? UnitType.TransportShip : String(intent.unit ?? ""),
      sid, click, dstOwner, T, start: T + w.start, strict: T + w.strict, end: T + w.end,
      unitId: -1, unitIds: [], tile: -1, level: 0,
      amount: Math.max(1, Number(intent.amount ?? 1) | 0), got: 0, extra: 0,
      done: cls === null, ok: false, late: false, release: null,
    };
    if (cls === "upgrade") {
      const u = game.unit(intent.unitId);
      if (!u) p.done = true;
      else { p.unitId = intent.unitId; p.tile = u.tile(); p.level = u.level(); }
    } else if (cls === "move") {
      p.unitIds = Array.isArray(intent.unitIds) ? intent.unitIds : [];
    }
    return p;
  }

  /** Nach dem Schreiben des Samples anmelden. release() kommt, sobald entschieden. */
  register(p: Pending, ref: unknown, release: () => void): void {
    p.ref = ref;
    this.all.push(p);
    if (p.done) {
      p.line = null;
      release();
      return;
    }
    p.release = release;
    this.open.push(p);
    this.index(p);
  }

  private index(p: Pending) {
    switch (p.cls) {
      case "struct": case "warship": case "nuke": case "boat": {
        const k = `${p.sid}|${p.utype}`;
        const l = this.byOwnerType.get(k);
        if (l) l.push(p); else this.byOwnerType.set(k, [p]);
        break;
      }
      case "upgrade": {
        const l = this.upg.get(p.unitId);
        if (l) l.push(p); else this.upg.set(p.unitId, [p]);
        if (!this.upgLevel.has(p.unitId)) this.upgLevel.set(p.unitId, p.level);
        break;
      }
      case "move": case "spawn": {
        const l = this.checkAt.get(p.strict);
        if (l) l.push(p); else this.checkAt.set(p.strict, [p]);
        break;
      }
    }
  }

  private fits(q: Pending, u: any, game: Game): boolean {
    switch (q.cls) {
      case "struct":
        return this.valid(q.click) && this.dist2(u.pos, q.click) < 225;
      case "warship":
        return u.warshipState?.patrolTile === q.click;
      case "nuke":
        return u.targetTile === q.click;
      case "boat":
        return u.targetTile !== undefined && this.valid(q.click) && this.valid(u.targetTile) &&
          this.manhattan(u.targetTile, q.click) <= 50 && game.map().ownerID(u.targetTile) === q.dstOwner;
    }
    return false;
  }

  private resolve(q: Pending, t: number, tile: number | undefined, kind: number, unitId: number) {
    q.line.res_tile = tile ?? -1;
    q.line.res_kind = kind;
    q.line.res_unit_id = unitId;
    q.line.res_dt = t - q.T;
    q.done = true;
    q.ok = true;
    q.late = t > q.strict;
    q.line = null;
    q.release?.();
  }

  private fail(q: Pending) {
    q.done = true;
    q.line = null;
    q.release?.();
  }

  /** Nach executeNextTick(t) mit den Updates dieses Zugs. */
  afterTick(t: number, gu: GameUpdateViewData, game: Game): void {
    const ups = (gu.updates[GameUpdateType.Unit] ?? []) as any[];
    const prevMax = this.maxId;
    let fresh: Map<number, any> | null = null;
    let upgUps: any[] | null = null;
    for (const u of ups) {
      if (u.id > prevMax) {
        if (u.id > this.maxId) this.maxId = u.id;
        if (!fresh) fresh = new Map();
        if (!fresh.has(u.id)) fresh.set(u.id, u); // erste Update = Bau-Schnappschuss
      }
      if (this.upg.size && this.upg.has(u.id)) (upgUps ??= []).push(u);
    }
    if (this.open.length === 0 && this.extras.length === 0) return;
    let changed = false;

    if (fresh && (this.byOwnerType.size || this.extras.length)) {
      const units = [...fresh.values()].sort((a, b) => a.id - b.id);
      for (const u of units) {
        const list = this.byOwnerType.get(`${u.ownerID}|${u.unitType}`);
        let q: Pending | undefined;
        if (list) for (const c of list) {
          if (!c.done && t >= c.start && t <= c.end && this.fits(c, u, game)) { q = c; break; }
        }
        if (q) {
          const tile = q.cls === "struct" ? u.pos : q.cls === "boat" ? u.targetTile : q.click;
          this.resolve(q, t, tile, 0, u.id);
          if (q.cls === "nuke" && q.amount > 1) { q.got = 1; this.extras.push(q); }
          changed = true;
          continue;
        }
        // weitere Einheiten eines Nuke-Klicks mit amount > 1
        for (const e of this.extras) {
          if (e.sid === u.ownerID && e.utype === u.unitType && e.got < e.amount &&
              t <= e.end && u.targetTile === e.click) { e.got++; e.extra++; break; }
        }
      }
    }
    if (this.extras.length) this.extras = this.extras.filter((e) => e.got < e.amount && e.end > t);

    if (upgUps) {
      for (const u of upgUps) {
        const last = this.upgLevel.get(u.id) ?? u.level;
        this.upgLevel.set(u.id, u.level);
        let inc = u.level - last;
        if (inc <= 0) continue;
        for (const q of this.upg.get(u.id)!) {  // ältestes zuerst
          if (inc <= 0) break;
          if (q.sid !== u.ownerID || t < q.start || t > q.end) continue;
          const take = Math.min(inc, q.amount - q.got);
          if (take <= 0) continue;
          q.got += take;
          inc -= take;
          if (!q.done) { this.resolve(q, t, q.tile, 1, q.unitId); changed = true; }
        }
      }
    }

    const chk = this.checkAt.get(t);
    if (chk) {
      this.checkAt.delete(t);
      for (const q of chk) {
        if (q.done) continue;
        if (q.cls === "move") {
          for (const id of q.unitIds) {
            const u: any = game.unit(id);
            if (u && u.type() === UnitType.Warship && u.owner().smallID() === q.sid &&
                u._warshipState?.patrolTile === q.click) {
              this.resolve(q, t, q.click, 0, -1);
              break;
            }
          }
        } else if (this.spawned(game, q)) {
          this.resolve(q, t, q.click, 0, -1);
        }
        changed = true;
      }
    }

    // Ablauf: wer sein Fensterende erreicht hat, ist ungültig.
    let k = 0;
    for (const q of this.open) {
      if (q.done) { changed = true; continue; }
      if (q.end <= t) { this.fail(q); changed = true; continue; }
      this.open[k++] = q;
    }
    this.open.length = k;
    if (changed) this.rebuild();
  }

  /** Spawn gilt: spawnTile == Klick und eine eigene Kachel im 9×9-Quadrat um den Klick. */
  private spawned(game: Game, q: Pending): boolean {
    const pl: any = game.playerBySmallID(q.sid);
    if (!pl?.isPlayer?.() || pl.spawnTile() !== q.click || !this.valid(q.click)) return false;
    const map = game.map();
    const cx = q.click % this.W, cy = Math.floor(q.click / this.W);
    for (let y = Math.max(0, cy - 4); y <= Math.min(this.H - 1, cy + 4); y++) {
      for (let x = Math.max(0, cx - 4); x <= Math.min(this.W - 1, cx + 4); x++) {
        if (map.ownerID(y * this.W + x) === q.sid) return true;
      }
    }
    return false;
  }

  /** Indizes aus den noch offenen Samples neu aufbauen (offen sind wenige). */
  private rebuild() {
    const keepLevels = new Map(this.upgLevel);
    this.byOwnerType.clear();
    this.upg.clear();
    this.upgLevel.clear();
    this.checkAt.clear();
    for (const q of this.open) {
      if (q.cls === "upgrade" && keepLevels.has(q.unitId)) this.upgLevel.set(q.unitId, keepLevels.get(q.unitId)!);
      this.index(q);
    }
  }

  /** Ende der Partie: alles Offene ist ungültig (DESIGN §2.8). */
  finish(): void {
    for (const q of this.open) if (!q.done) this.fail(q);
    this.open = [];
    this.extras = [];
    this.rebuild();
  }

  /**
   * Schnitt bei lastOk (DESIGN §2.7): Samples mit T ≤ lastOk, deren Fenster über
   * lastOk hinausreicht, werden ungültig. Liefert die, deren Metazeile der
   * Aufrufer auf invalidRes() zurücksetzen muss (die Zeile ist dann schon Text).
   */
  cut(lastOk: number): Pending[] {
    const fix: Pending[] = [];
    for (const p of this.all) {
      if (p.T > lastOk || p.end <= lastOk) continue;
      if (p.ok) fix.push(p);
      p.ok = false;
      p.late = false;
      p.extra = 0;
    }
    return fix;
  }

  /** .ok res: je Typ ok/invalid/late (+ extra_units bei Nukes), nur Samples mit T ≤ maxTick. */
  stats(maxTick = Infinity): Record<string, Record<string, number>> {
    const out: Record<string, Record<string, number>> = {};
    for (const p of this.all) {
      if (p.T > maxTick) continue;
      const s = (out[p.type] ??= p.cls === "nuke" ? { ok: 0, invalid: 0, late: 0, extra_units: 0 } : { ok: 0, invalid: 0, late: 0 });
      if (p.ok) s.ok++; else s.invalid++;
      if (p.late) s.late++;
      if (p.cls === "nuke") s.extra_units += p.extra;
    }
    return out;
  }
}

/**
 * Beobachtungs-Encoder.
 *
 * Aufbau in zwei Stufen, weil der teure Teil geteilt werden kann:
 *
 *   scanTick(game)      einmal pro Tick. Laeuft ueber alle Kacheln und alle
 *                       Einheiten und baut Raster, die fuer ALLE Spieler gelten
 *                       (Besitzer je Zelle, Gebaeude je Typ, Gelaende).
 *   encode(player, out) je Spieler. Leitet daraus die spielerbezogenen Kanaele
 *                       ab -- nur gw*gh Schritte statt Millionen.
 *
 * Ohne diese Trennung kostet eine 100-Spieler-Partie das Hundertfache.
 */
import { Game, Player, Structures, UnitType } from "../vendor/openfront/src/core/game/Game";
import { TileRef } from "../vendor/openfront/src/core/game/GameMap";

// Kachelbits, gespiegelt aus GameMapImpl.
const PLAYER_ID_MASK = 0xfff;
const FALLOUT_BIT = 1 << 13;
const DEFENSE_BIT = 1 << 14;
const LAND_BIT = 1 << 7;
const MAG_MASK = 0x1f;
const IMPASSABLE = 31;

/** Gebaeude, die auf eigene Kanaele gelegt werden. */
export const STRUCT_TYPES = [
  UnitType.City, UnitType.Port, UnitType.Factory,
  UnitType.MissileSilo, UnitType.SAMLauncher, UnitType.DefensePost,
] as const;

/** Bewegliche Einheiten mit eigenem Kanal. */
export const MOBILE_TYPES = [
  UnitType.Warship, UnitType.TransportShip, UnitType.TradeShip,
] as const;

/** Kanalnamen in exakt der Reihenfolge, in der sie im Tensor liegen. */
export const CHANNELS = [
  "land", "hoehe", "eigen", "verbuendet", "feindlich", "eigene_grenze",
  "fallout", "verteidigung",
  ...STRUCT_TYPES.map((t) => `bau_${t}`),
  ...MOBILE_TYPES.map((t) => `mob_${t}`),
  "sam_cover",
] as const;
export const NUM_CHANNELS = CHANNELS.length;

const C_LAND = 0, C_MAG = 1, C_OWN = 2, C_ALLY = 3, C_ENEMY = 4,
      C_BORDER = 5, C_FALLOUT = 6, C_DEFENSE = 7, C_STRUCT0 = 8;
const C_MOBILE0 = C_STRUCT0 + STRUCT_TYPES.length;
const C_SAMCOVER = C_MOBILE0 + MOBILE_TYPES.length;

/** Was ein Gegner fuer uns ist -- die Zahlen landen so im Vektor. */
export interface OpponentFeat {
  id: number;
  troops: number; gold: number; tiles: number;
  ally: number; sameTeam: number; traitor: number; human: number;
  bordersMe: number;
  /** Anteil MEINER Grenze, der an diesen Gegner stoesst. */
  contactShare: number;
  /** Zusammensetzung SEINER Grenze -- daraus liest man Annexionsnaehe ab. */
  borderToMe: number; borderToOther: number; borderToUnowned: number;
  /**
   * Grenzfelder, die einer Annexion noch im Weg stehen: alles was nicht
   * an mich stoesst. Null hiesse eingeschlossen. Naeherung, siehe unten.
   */
  gapsRemaining: number;
  /** Beruehrt Kueste oder Kartenrand -- dann NIE annektierbar. */
  annexProof: number;
  // --- Strategie-Spec (Ultimus_Rex): Signale, die man in Zuegen nicht sieht ---
  /** Restlaufzeit der Allianz mit mir, normiert auf Allianzdauer (0 = keine/abgelaufen). */
  allyTicksLeft: number;
  /** Ist dieser Gegner der Board-Anfuehrer (meiste Felder)? "focus the winner." */
  isLeader: number;
  /** Hat mind. ein Raketensilo -- kann also nuken. "check before you attack." */
  hasSilo: number;
  /** Hat mind. einen SAM-Werfer. */
  hasSam: number;
  /** Hat einen Clan-Tag (organisiert, im Schnitt staerker). §F */
  inClan: number;
}

export interface Encoded {
  own: Record<string, number>;
  opponents: OpponentFeat[];
}

export class ObsEncoder {
  readonly gw: number;
  readonly gh: number;
  private readonly n: number;

  // Geteilte Raster, einmal pro Tick gefuellt
  private readonly ownerG: Uint16Array;      // dominanter Besitzer je Zelle
  private readonly landG: Float32Array;
  private readonly magG: Float32Array;
  private readonly falloutG: Float32Array;
  private readonly defenseG: Float32Array;
  private readonly borderG: Uint16Array;     // Besitzer, falls Zelle Grenze enthaelt
  private readonly structG: Uint16Array[];   // je Gebaeudetyp: Besitzer-ID
  private readonly structLvlG: Uint8Array[];  // je Gebaeudetyp: Level (0=leer)
  private readonly mobileG: Uint16Array[];
  private readonly samCoverG: Uint16Array;    // Besitzer eines abdeckenden SAM je Zelle

  private readonly counts: Int32Array;
  private readonly cellTiles: Int32Array;
  private readonly stride: number;
  private terrain: Uint8Array | null = null;
  private nbuf: TileRef[] = new Array(8).fill(0);

  constructor(gw = 180, gh = 90, maxPlayers = 600) {
    this.gw = gw; this.gh = gh; this.n = gw * gh;
    this.stride = maxPlayers;
    this.ownerG = new Uint16Array(this.n);
    this.landG = new Float32Array(this.n);
    this.magG = new Float32Array(this.n);
    this.falloutG = new Float32Array(this.n);
    this.defenseG = new Float32Array(this.n);
    this.borderG = new Uint16Array(this.n);
    this.structG = STRUCT_TYPES.map(() => new Uint16Array(this.n));
    this.structLvlG = STRUCT_TYPES.map(() => new Uint8Array(this.n));
    this.mobileG = MOBILE_TYPES.map(() => new Uint16Array(this.n));
    this.samCoverG = new Uint16Array(this.n);
    this.counts = new Int32Array(this.n * this.stride);
    this.cellTiles = new Int32Array(this.n);
  }

  /** Gelaende einmal pro Partie einlesen -- es aendert sich nie. */
  initTerrain(game: Game) {
    const W = game.width(), H = game.height();
    this.terrain = new Uint8Array(W * H);
    for (let r = 0; r < W * H; r++) this.terrain[r] = game.map().terrainByte(r);
    this.cellTiles.fill(0);
    for (let y = 0; y < H; y++) {
      const gy = ((y * this.gh) / H) | 0;
      for (let x = 0; x < W; x++) this.cellTiles[gy * this.gw + (((x * this.gw) / W) | 0)]++;
    }
  }

  /** Der teure Durchlauf. Einmal pro Tick, gilt fuer alle Spieler. */
  scanTick(game: Game) {
    if (!this.terrain) this.initTerrain(game);
    const terrain = this.terrain!;
    const map = game.map();
    const state = map.tileStateBuffer();
    const W = game.width(), H = game.height();
    const { gw, gh, n, stride } = this;

    this.landG.fill(0); this.magG.fill(0);
    this.falloutG.fill(0); this.defenseG.fill(0);
    this.counts.fill(0);

    for (let y = 0; y < H; y++) {
      const gy = ((y * gh) / H) | 0, row = y * W;
      for (let x = 0; x < W; x++) {
        const ref = row + x;
        const t = terrain[ref];
        if ((t & LAND_BIT) === 0) continue;
        const mag = t & MAG_MASK;
        if (mag === IMPASSABLE) continue;
        const gi = gy * gw + (((x * gw) / W) | 0);
        this.landG[gi]++;
        this.magG[gi] += mag;
        const s = state[ref];
        if (s & FALLOUT_BIT) this.falloutG[gi]++;
        if (s & DEFENSE_BIT) this.defenseG[gi]++;
        const oid = s & PLAYER_ID_MASK;
        if (oid !== 0 && oid < stride) this.counts[gi * stride + oid]++;
      }
    }

    for (let gi = 0; gi < n; gi++) {
      const tiles = this.cellTiles[gi] || 1;
      const landN = this.landG[gi] || 1;
      this.magG[gi] /= landN * IMPASSABLE;
      this.falloutG[gi] /= landN;
      this.defenseG[gi] /= landN;
      this.landG[gi] /= tiles;
      let best = 0, bestN = 0;
      const base = gi * stride;
      for (let p = 1; p < stride; p++) {
        const c = this.counts[base + p];
        if (c > bestN) { bestN = c; best = p; }
      }
      this.ownerG[gi] = best;
    }

    // Gebaeude und bewegliche Einheiten einstempeln
    for (const g of this.structG) g.fill(0);
    for (const g of this.structLvlG) g.fill(0);
    for (const g of this.mobileG) g.fill(0);
    this.borderG.fill(0);
    this.samCoverG.fill(0);
    const toGrid = (ref: TileRef) =>
      ((((map.y(ref) * gh) / H) | 0) * gw) + (((map.x(ref) * gw) / W) | 0);
    const samIdx = STRUCT_TYPES.indexOf(UnitType.SAMLauncher);

    for (const p of game.players()) {
      const sid = p.smallID();
      for (const u of p.units()) {
        if (!u.isActive()) continue;
        const ti = STRUCT_TYPES.indexOf(u.type() as any);
        if (ti >= 0) {
          const gi = toGrid(u.tile());
          this.structG[ti][gi] = sid;
          this.structLvlG[ti][gi] = Math.min(255, u.level());
          if (ti === samIdx) this.stampSamCover(map, u.tile(), game.config().samRange(u.level()), sid, W, H);
          continue;
        }
        const mi = MOBILE_TYPES.indexOf(u.type() as any);
        if (mi >= 0) this.mobileG[mi][toGrid(u.tile())] = sid;
      }
      // Grenzraster: markiert, wessen Grenze durch eine Zelle laeuft
      for (const b of p.borderTiles()) this.borderG[toGrid(b)] = sid;
    }
  }

  /** Markiert alle Rasterzellen im SAM-Radius (Kacheln) mit dem Besitzer. */
  private stampSamCover(map: any, tile: TileRef, range: number, sid: number, W: number, H: number) {
    const { gw, gh } = this;
    const tx = map.x(tile), ty = map.y(tile), r2 = range * range;
    const cgx = (tx * gw / W) | 0, cgy = (ty * gh / H) | 0;
    const rgx = Math.ceil(range * gw / W), rgy = Math.ceil(range * gh / H);
    for (let gy = Math.max(0, cgy - rgy); gy <= Math.min(gh - 1, cgy + rgy); gy++) {
      const tcy = (gy + 0.5) * H / gh;
      for (let gx = Math.max(0, cgx - rgx); gx <= Math.min(gw - 1, cgx + rgx); gx++) {
        const tcx = (gx + 0.5) * W / gw;
        const dx = tcx - tx, dy = tcy - ty;
        if (dx * dx + dy * dy <= r2) this.samCoverG[gy * gw + gx] = sid;
      }
    }
  }

  /**
   * Spielerbezogene Kanaele. Billig: laeuft nur ueber das Raster.
   * `out` muss NUM_CHANNELS * gw * gh gross sein.
   */
  encodeMap(player: Player, allies: Set<number>, out: Float32Array) {
    const { n } = this;
    out.fill(0);
    const me = player.smallID();
    const rel = (id: number) => id === 0 ? 0 : id === me ? 1 : allies.has(id) ? 0.5 : -1;

    for (let i = 0; i < n; i++) {
      out[C_LAND * n + i] = this.landG[i];
      out[C_MAG * n + i] = this.magG[i];
      out[C_FALLOUT * n + i] = this.falloutG[i];
      out[C_DEFENSE * n + i] = this.defenseG[i];
      const o = this.ownerG[i];
      if (o !== 0) {
        if (o === me) out[C_OWN * n + i] = 1;
        else if (allies.has(o)) out[C_ALLY * n + i] = 1;
        else out[C_ENEMY * n + i] = 1;
      }
      if (this.borderG[i] === me) out[C_BORDER * n + i] = 1;
      for (let s = 0; s < STRUCT_TYPES.length; s++) {
        const id = this.structG[s][i];
        // Vorzeichen = Beziehung, Betrag steigt mit Level (L1=0.4 … L10=1.0)
        if (id !== 0) {
          const lvl = this.structLvlG[s][i];
          out[(C_STRUCT0 + s) * n + i] = rel(id) * (0.4 + 0.6 * Math.min(lvl - 1, 9) / 9);
        }
      }
      for (let m = 0; m < MOBILE_TYPES.length; m++) {
        const id = this.mobileG[m][i];
        if (id !== 0) out[(C_MOBILE0 + m) * n + i] = rel(id);
      }
      const sc = this.samCoverG[i];
      if (sc !== 0) out[C_SAMCOVER * n + i] = rel(sc);
    }
  }

  /**
   * Vektor und Gegnerliste, inklusive der Annexions-Merkmale.
   *
   * Naeherung: die Engine prueft Annexion pro zusammenhaengendem Klumpen
   * (PlayerExecution.removeClusters). Wir rechnen hier pro SPIELER, weil
   * echte Klumpenzerlegung fuer alle Gegner jede Sekunde zu teuer waere.
   * Fuer einen Gegner aus einem Stueck ist das exakt; bei mehreren Klumpen
   * ist `gapsRemaining` eine Obergrenze -- das Netz sieht dann eher zu wenig
   * Annexionsnaehe als zu viel, was die sichere Richtung ist.
   */
  encodeVec(game: Game, player: Player, topK = 24): Encoded {
    const map = game.map();
    const me = player.smallID();

    // Meine Grenze einmal ablaufen: wer beruehrt mich, und wie lang?
    const contact = new Map<number, number>();
    let myBorder = 0;
    for (const b of player.borderTiles()) {
      myBorder++;
      const k = map.neighbors4(b, this.nbuf);
      for (let i = 0; i < k; i++) {
        const oid = map.ownerID(this.nbuf[i]);
        if (oid !== 0 && oid !== me) contact.set(oid, (contact.get(oid) ?? 0) + 1);
      }
    }

    const others: Player[] = [];
    for (const o of game.players()) {
      if (o === player || !o.isAlive()) continue;
      others.push(o);
    }
    // Nah und gross zuerst: an wen ich grenze, dann nach Flaeche.
    others.sort((a, b) => {
      const ca = contact.get(a.smallID()) ?? 0, cb = contact.get(b.smallID()) ?? 0;
      if (ca !== cb) return cb - ca;
      return b.numTilesOwned() - a.numTilesOwned();
    });

    // Board-Anfuehrer (meiste Felder, alle Lebenden inkl. mir) — "focus the winner".
    let leaderId = 0, leaderTiles = -1;
    for (const p of game.players()) {
      if (!p.isAlive()) continue;
      const t = p.numTilesOwned();
      if (t > leaderTiles) { leaderTiles = t; leaderId = p.smallID(); }
    }
    const allianceDur = Math.max(1, game.config().allianceDuration());
    const now = game.ticks();

    const opponents: OpponentFeat[] = [];
    for (const o of others.slice(0, topK)) {
      const oid = o.smallID();
      let toMe = 0, toOther = 0, toUnowned = 0, proof = 0, total = 0;
      for (const b of o.borderTiles()) {
        if (map.isShore(b) || map.isOnEdgeOfMap(b)) proof = 1;
        const k = map.neighbors4(b, this.nbuf);
        for (let i = 0; i < k; i++) {
          const no = map.ownerID(this.nbuf[i]);
          // Eigene Nachbarn sind keine Grenze -- wuerden sie mitzaehlen,
          // koennte der Anteil rechnerisch nie 100% erreichen.
          if (no === oid) continue;
          total++;
          if (no === me) toMe++;
          else if (no === 0) toUnowned++;
          else toOther++;
        }
      }
      const denom = total || 1;
      opponents.push({
        id: oid,
        troops: o.troops(), gold: Number(o.gold()), tiles: o.numTilesOwned(),
        ally: player.isAlliedWith(o) ? 1 : 0,
        sameTeam: player.isOnSameTeam(o) ? 1 : 0,
        traitor: o.isTraitor() ? 1 : 0,
        human: o.type() === "HUMAN" ? 1 : 0,
        bordersMe: contact.has(oid) ? 1 : 0,
        contactShare: (contact.get(oid) ?? 0) / (myBorder || 1),
        borderToMe: toMe / denom,
        borderToOther: toOther / denom,
        borderToUnowned: toUnowned / denom,
        gapsRemaining: toOther + toUnowned,
        annexProof: proof,
        allyTicksLeft: (() => {
          const info = player.allianceInfo(o);
          return info ? Math.max(0, (info.expiresAt - now) / allianceDur) : 0;
        })(),
        isLeader: oid === leaderId ? 1 : 0,
        hasSilo: o.unitCount(UnitType.MissileSilo) > 0 ? 1 : 0,
        hasSam: o.unitCount(UnitType.SAMLauncher) > 0 ? 1 : 0,
        inClan: o.clanTag() !== null ? 1 : 0,
      });
    }

    const own: Record<string, number> = {
      troops: player.troops(),
      gold: Number(player.gold()),
      tiles: player.numTilesOwned(),
      borderLen: myBorder,
      allies: player.allies().length,
      betrayals: player.betrayals(),
      traitor: player.isTraitor() ? 1 : 0,
      tick: game.ticks(),
      alive: others.length + 1,
      // --- Strategie-Spec ---
      troopsRatio: player.troops() / Math.max(1, game.config().maxTroops(player)),  // S-Kurve, Optimum ~42%
      boardShare: player.numTilesOwned() / Math.max(1, game.numLandTiles()),        // Angriffstempo skaliert damit
      boatsOut: player.unitCount(UnitType.TransportShip),                            // Cap 3, Reserve halten
      isLeader: player.smallID() === leaderId ? 1 : 0,
    };
    for (const t of Structures.types) own[`n_${t}`] = player.unitCount(t);
    return { own, opponents };
  }
}

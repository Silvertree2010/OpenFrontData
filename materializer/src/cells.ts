/**
 * Zellfakten 180x90 (P3), DESIGN.md §6 und §8.
 *
 * Block je (Tick, Spieler), roh CELL_BYTES = 64'800 Byte, Zeilenhaupt 90x180:
 *   owner_major  u16 LE [16200]  = ownerG aus ObsEncoder.scanTick (nur kopiert)
 *   own_frac     u8     [16200]  = round(255 * eigene Kacheln / Kacheln der Zelle)
 *   legal        u8     [16200]  Bit gesetzt = mind. eine Kachel der Zelle erfuellt:
 *     Bit 0  eigene Kachel
 *     Bit 1  eigene Kachel, euklid² >= structureMinDist² (225) zu jedem aktiven
 *            Bauwerk jedes Besitzers, auch im Bau
 *     Bit 2  wie Bit 1 und isShore (Land mit Shoreline-Bit, auch Seeufer)
 *     Bit 3  Landekachel fuer ein Boot: isShore, Besitzer X != sid, X herrenlos
 *            oder canAttackPlayer(X), getWaterComponent(Kachel) != null und in
 *            den Komponenten der eigenen Grenzkacheln mit isShore
 *     Bit 4  Wasser, Komponente != null, ein eigener Hafen (aktiv, fertig) hat
 *            hasWaterComponent(hafen, komponente)
 *     Bit 5  Wasser (!isLand)
 *     Bit 6  passierbares Land, herrenlos ODER eigenes (Umzug in der Spawnphase:
 *            die Engine gibt die eigenen Kacheln vor der Suche frei)
 *     Bit 7  nukebar wie PlayerImpl.nukeSpawn(kachel, AtomBomb) plus isAlive
 *   legalBit1=false (LEGAL_BIT1=0): Bit 1 und Bit 2 bleiben 0.
 *   Zell-Abbildung wie obs.ts: Zeile (y*90/H)|0, Spalte (x*180/W)|0.
 *
 * Entscheidungen nach Engine-Verhalten (an 88cc95d8, 8b45be57, 115da032 gelesen,
 * alle verwendeten Funktionen sind an den drei Commits gleich):
 *   - Bit 1: Die Engine (validStructureSpawnTiles) sperrt eine Kachel, wenn ein
 *     Bauwerk mit euklid² < 225 steht (strikt). Gezaehlt werden alle Einheiten
 *     aus Structures.types (City, Defense Post, SAM, Silo, Port, Factory), aktiv,
 *     auch im Bau, jedes Besitzers. Quelle hier: game.units(Structures.types),
 *     das ist dieselbe Menge wie im UnitGrid (Einheiten verlassen beide erst
 *     bei delete()). Die Referenz im Test prueft ueber game.nearbyUnits.
 *   - Bit 3: OHNE Bootsobergrenze (3) und ohne Truppenpruefung. Die Grenze ist
 *     spielerglobal und aendert sich zwischen Beobachtung und Engine-Entscheid
 *     (Boot landet in Tick T); die Zahl der Boote steht ohnehin im Vektor.
 *     Die Engine prueft beim Boot die Klickkachel (owner(klick)) und landet an
 *     einer Kuestenkachel desselben Besitzers; das Bit ist die Legalitaet der
 *     Landekachel, also dieselben Bedingungen auf der Kachel selbst
 *     (SpatialQuery.closestReachableShore mit maxDist 0).
 *   - Bit 7: exakt nukeSpawn fuer die Atombombe:
 *       keine Spawn-Immunitaet (game.isSpawnImmunityActive, global), Kachel
 *       nicht unpassierbar (Wasser ist passierbar), Besitzer kein Teamkamerad,
 *       im Teammodus kein Teamkameraden-Bauwerk mit euklid² <= 30² (anyUnitNearby:
 *       <= statt <, und Bauwerke IM BAU zaehlen dort NICHT), mind. ein bereites
 *       eigenes Silo (aktiv, kein Cooldown, nicht im Bau). Nach Spielende
 *       (getWinner() != null) entfallen beide Teamkameraden-Pruefungen.
 *       Zusaetzlich isAlive (canBuildUnitType). Nicht geprueft: Gold und
 *       isUnitDisabled (haengen vom Typ ab), MIRV-Besitzerpflicht.
 *       Eigene Kacheln und eigene Bauwerke sperren nicht (isOnSameTeam(self)=false).
 *   - Bit 2 verlangt wie die Engine isShore, nicht isOceanShore.
 *
 * Kosten: kein Vollscan pro Aufruf.
 *   einmal pro Partie   Zellabbildung, Terrain-Kopie, Listen je Zelle: passierbares
 *                       Land (Bit 6), Kueste mit Wasserkomponente (Bit 3),
 *                       Wasserkomponenten (Bit 4), Wasser/passierbar je Zelle.
 *                       Neu gebaut, wenn sich das Terrain aendert (Water-Nukes,
 *                       memcmp gegen das Live-Terrain einmal pro Tick) oder
 *                       game.waterGraphVersion() steigt (dann nur Komponenten).
 *   einmal pro Tick     Bauwerks-Eimer (32er Raster), herrenloser Teil von Bit 6
 *                       (Landliste je Zelle, Abbruch bei der ersten herrenlosen Kachel).
 *   je (Tick, Spieler)  player.tiles() (Bit 0-2, own_frac), borderTiles (Bit 3),
 *                       Kuestenliste (Bit 3), Hafenkomponenten (Bit 4), Bit 7 nur
 *                       im Teamspiel mit Teamkameraden kachelweise, sonst statisch.
 *   Ergebnis wird je (Tick, Spieler) gecacht; derselbe Buffer kommt zurueck,
 *   der Aufrufer darf ihn nicht veraendern.
 *
 * Wasserkomponenten: getWaterComponent(t) haengt nur von der Minimap-Kachel
 * (x>>1, y>>1) und dem Graphen ab (WaterManager.ts, an allen drei Commits
 * gleich). Deshalb wird je Minimap-Kachel einmal gefragt und gemerkt.
 *
 * Rein lesend: benutzt nur Getter (tiles, borderTiles, units, getWaterComponent,
 * hasWaterComponent, canAttackPlayer, isOnSameTeam, isAlive, isInCooldown,
 * isSpawnImmunityActive, getWinner, waterGraphVersion, tileStateBuffer) und das
 * private Feld GameMapImpl.terrain nur lesend. Keine Pfadsuche, kein Zufall.
 */
import { Game, GameMode, Player, Structures, UnitType } from "../../vendor/openfront/src/core/game/Game";

export const GW = 180;
export const GH = 90;
export const NCELLS = GW * GH;
export const CELL_BYTES = NCELLS * 4;

// Offsets im Block
const OFF_FRAC = NCELLS * 2;
const OFF_LEGAL = NCELLS * 3;

// Terrain- und Zustandsbits, gespiegelt aus GameMapImpl (an allen drei Commits gleich)
const LAND = 1 << 7;
const SHORE = 1 << 6;
const MAG_MASK = 0x1f;
const IMPASSABLE = 31;
const OWNER_MASK = 0xfff;

const B0 = 1, B1 = 2, B2 = 4, B3 = 8, B4 = 16, B5 = 32, B6 = 64, B7 = 128;

/** Bucket-Kantenlaenge fuer Bauwerke. Muss >= Sperrradius sein (15 bzw. 30). */
const BUCKET = 32;

interface StructIndex {
  /** CSR ueber Eimer: start[b]..start[b+1] */
  start: Int32Array;
  x: Int32Array;
  y: Int32Array;
  bw: number;
  bh: number;
}

export class CellFacts {
  private readonly W: number;
  private readonly H: number;
  private readonly legalBit1: boolean;
  private readonly minDist2: number;
  private readonly atomOuter2: number;
  private readonly atomOuter: number;
  private readonly teamMode: boolean;

  /** Zelle je Kachel. 16200 < 65536. */
  private readonly cellOf: Uint16Array;
  private readonly cellTiles: Int32Array;
  /** Kachelspannen je Zellspalte/-zeile: x in [colStart[gx], colStart[gx+1]) */
  private readonly colStart: Int32Array;
  private readonly rowStart: Int32Array;

  // Terrain-abhaengig (neu bei Terrainaenderung)
  private terr!: Uint8Array;
  private terrBuf!: Buffer;
  private waterAny!: Uint8Array;
  private passAny!: Uint8Array;
  private landStart!: Int32Array;
  private landRefs!: Int32Array;
  private coastStart!: Int32Array;
  private coastRefs!: Int32Array;

  // Komponenten-abhaengig (neu bei Terrain- oder Graphaenderung)
  private graphVersion = -1;
  private miniComp!: Int32Array;      // -2 = unbekannt, -1 = null
  private readonly miniW: number;
  private coastComp!: Int32Array;     // parallel zu coastRefs
  private wcStart!: Int32Array;       // verschiedene Wasserkomponenten je Zelle
  private wcComps!: Int32Array;

  // Je Tick
  private tick = -1;
  private structIdx: StructIndex | null = null;
  private bit6: Uint8Array | null = null;
  private readonly cache = new Map<number, Buffer>();

  // Scratch
  private readonly cnt = new Int32Array(NCELLS);
  private readonly attackable = new Int8Array(OWNER_MASK + 1);

  /** Zaehler fuer Tests/Bericht. */
  readonly stats = { rebuildTerrain: 0, rebuildComps: 0, computed: 0, cached: 0 };

  constructor(game: Game, opts: { legalBit1: boolean }) {
    this.W = game.width();
    this.H = game.height();
    this.legalBit1 = opts.legalBit1;
    const cfg = game.config();
    const md = cfg.structureMinDist();
    this.minDist2 = md * md;
    this.atomOuter = cfg.nukeMagnitudes(UnitType.AtomBomb).outer;
    this.atomOuter2 = this.atomOuter * this.atomOuter;
    if (md > BUCKET || this.atomOuter > BUCKET) throw new Error(`cells: Radius > BUCKET (${md}, ${this.atomOuter})`);
    this.teamMode = cfg.gameConfig().gameMode === GameMode.Team;

    const { W, H } = this;
    this.colStart = new Int32Array(GW + 1);
    this.rowStart = new Int32Array(GH + 1);
    const colOf = new Int32Array(W), rowOf = new Int32Array(H);
    for (let x = 0; x < W; x++) colOf[x] = ((x * GW) / W) | 0;
    for (let y = 0; y < H; y++) rowOf[y] = ((y * GH) / H) | 0;
    // Spannen: erste Kachel je Zellspalte; Zellen koennen bei W < 180 leer sein.
    this.colStart.fill(W); this.rowStart.fill(H);
    for (let x = W - 1; x >= 0; x--) this.colStart[colOf[x]] = x;
    for (let y = H - 1; y >= 0; y--) this.rowStart[rowOf[y]] = y;
    for (let g = GW - 1; g >= 0; g--) if (this.colStart[g] > this.colStart[g + 1]) this.colStart[g] = this.colStart[g + 1];
    for (let g = GH - 1; g >= 0; g--) if (this.rowStart[g] > this.rowStart[g + 1]) this.rowStart[g] = this.rowStart[g + 1];

    this.cellOf = new Uint16Array(W * H);
    this.cellTiles = new Int32Array(NCELLS);
    for (let y = 0; y < H; y++) {
      const base = rowOf[y] * GW, row = y * W;
      for (let x = 0; x < W; x++) {
        const gi = base + colOf[x];
        this.cellOf[row + x] = gi;
        this.cellTiles[gi]++;
      }
    }
    this.miniW = (W + 1) >> 1;

    this.buildTerrain(game);
    this.buildComps(game);
  }

  // ───────────────────────── einmal pro Partie ─────────────────────────

  private liveTerrain(game: Game): Uint8Array {
    const t = (game.map() as any).terrain;
    if (!(t instanceof Uint8Array) || t.length !== this.W * this.H) {
      throw new Error("cells: GameMapImpl.terrain nicht lesbar");
    }
    return t;
  }

  private buildTerrain(game: Game) {
    const live = this.liveTerrain(game);
    this.terr = new Uint8Array(live);                       // Kopie
    this.terrBuf = Buffer.from(this.terr.buffer, this.terr.byteOffset, this.terr.byteLength);
    const { terr, cellOf, W, H } = this;
    const n = W * H;
    this.waterAny = new Uint8Array(NCELLS);
    this.passAny = new Uint8Array(NCELLS);
    const landCnt = new Int32Array(NCELLS + 1), coastCnt = new Int32Array(NCELLS + 1);
    for (let r = 0; r < n; r++) {
      const t = terr[r], gi = cellOf[r];
      if ((t & LAND) === 0) { this.waterAny[gi] = 1; this.passAny[gi] = 1; continue; }
      if ((t & MAG_MASK) === IMPASSABLE) continue;
      this.passAny[gi] = 1;
      landCnt[gi + 1]++;
      if (t & SHORE) coastCnt[gi + 1]++;
    }
    // isShore = isLand && Shoreline-Bit; auch unpassierbare Kuestenkacheln zaehlen
    // dort als Kueste. Die Engine prueft beim Boot kein isImpassable, also muessen
    // sie in die Kuestenliste. Oben wurden sie uebersprungen -> nachzaehlen.
    for (let r = 0; r < n; r++) {
      const t = terr[r];
      if ((t & LAND) && (t & MAG_MASK) === IMPASSABLE && (t & SHORE)) coastCnt[cellOf[r] + 1]++;
    }
    for (let i = 0; i < NCELLS; i++) { landCnt[i + 1] += landCnt[i]; coastCnt[i + 1] += coastCnt[i]; }
    this.landStart = landCnt.slice();
    this.coastStart = coastCnt.slice();
    this.landRefs = new Int32Array(landCnt[NCELLS]);
    this.coastRefs = new Int32Array(coastCnt[NCELLS]);
    const lp = landCnt, cp = coastCnt;                    // als Schreibzeiger weiterverwenden
    for (let r = 0; r < n; r++) {
      const t = terr[r];
      if ((t & LAND) === 0) continue;
      const gi = cellOf[r];
      const pass = (t & MAG_MASK) !== IMPASSABLE;
      if (pass) this.landRefs[lp[gi]++] = r;
      if (t & SHORE) this.coastRefs[cp[gi]++] = r;
    }
    this.stats.rebuildTerrain++;
  }

  /** getWaterComponent je Minimap-Kachel gemerkt; -1 = null. */
  private comp(game: Game, r: number): number {
    const x = r % this.W, y = (r - x) / this.W;
    const k = (y >> 1) * this.miniW + (x >> 1);
    let c = this.miniComp[k];
    if (c === -2) {
      const v = game.getWaterComponent(r);
      c = v === null ? -1 : v;
      this.miniComp[k] = c;
    }
    return c;
  }

  private buildComps(game: Game) {
    this.graphVersion = game.waterGraphVersion();
    this.miniComp = new Int32Array(this.miniW * ((this.H + 1) >> 1)).fill(-2);
    this.coastComp = new Int32Array(this.coastRefs.length);
    for (let k = 0; k < this.coastRefs.length; k++) this.coastComp[k] = this.comp(game, this.coastRefs[k]);
    // Verschiedene Wasserkomponenten je Zelle (Bit 4)
    const { terr, W } = this;
    const lists: number[][] = [];
    let total = 0;
    for (let gy = 0; gy < GH; gy++) {
      for (let gx = 0; gx < GW; gx++) {
        const gi = gy * GW + gx;
        let l: number[] | null = null;
        if (this.waterAny[gi]) {
          const x0 = this.colStart[gx], x1 = this.colStart[gx + 1];
          const y0 = this.rowStart[gy], y1 = this.rowStart[gy + 1];
          for (let y = y0; y < y1; y++) {
            for (let x = x0; x < x1; x++) {
              const r = y * W + x;
              if (terr[r] & LAND) continue;
              const c = this.comp(game, r);
              if (c < 0) continue;
              if (l === null) l = [c];
              else if (!l.includes(c)) l.push(c);
            }
          }
        }
        lists.push(l ?? []);
        total += l ? l.length : 0;
      }
    }
    this.wcStart = new Int32Array(NCELLS + 1);
    this.wcComps = new Int32Array(total);
    let p = 0;
    for (let gi = 0; gi < NCELLS; gi++) {
      this.wcStart[gi] = p;
      for (const c of lists[gi]) this.wcComps[p++] = c;
    }
    this.wcStart[NCELLS] = p;
    this.stats.rebuildComps++;
  }

  // ───────────────────────── einmal pro Tick ─────────────────────────

  private startTick(game: Game) {
    const t = game.ticks();
    if (t === this.tick) return;
    this.tick = t;
    this.cache.clear();
    this.structIdx = null;
    this.bit6 = null;
    const live = this.liveTerrain(game);
    const liveBuf = Buffer.from(live.buffer, live.byteOffset, live.byteLength);
    if (liveBuf.compare(this.terrBuf) !== 0) {
      this.buildTerrain(game);
      this.buildComps(game);
    } else if (game.waterGraphVersion() !== this.graphVersion) {
      this.buildComps(game);
    }
  }

  /** Alle aktiven Bauwerke (auch im Bau) in 32er-Eimern. */
  private structures(game: Game): StructIndex {
    if (this.structIdx) return this.structIdx;
    this.structIdx = this.bucketize(game, game.units(Structures.types).filter((u) => u.isActive()));
    return this.structIdx;
  }

  private bucketize(game: Game, units: { tile(): number }[]): StructIndex {
    const bw = Math.ceil(this.W / BUCKET), bh = Math.ceil(this.H / BUCKET);
    const start = new Int32Array(bw * bh + 1);
    const n = units.length;
    const bx = new Int32Array(n), xs = new Int32Array(n), ys = new Int32Array(n);
    for (let i = 0; i < n; i++) {
      const tile = units[i].tile();
      const x = game.x(tile), y = game.y(tile);
      xs[i] = x; ys[i] = y;
      bx[i] = ((y / BUCKET) | 0) * bw + ((x / BUCKET) | 0);
      start[bx[i] + 1]++;
    }
    for (let b = 0; b < bw * bh; b++) start[b + 1] += start[b];
    const ptr = start.slice(0, bw * bh);
    const X = new Int32Array(n), Y = new Int32Array(n);
    for (let i = 0; i < n; i++) { const p = ptr[bx[i]]++; X[p] = xs[i]; Y[p] = ys[i]; }
    return { start, x: X, y: Y, bw, bh };
  }

  /** Irgendein Eintrag mit d² < lim (strict) bzw. <= lim (strict=false)? */
  private near(idx: StructIndex, x: number, y: number, lim: number, strict: boolean): boolean {
    const cbx = (x / BUCKET) | 0, cby = (y / BUCKET) | 0;
    const { start, bw, bh } = idx;
    for (let by = cby - 1; by <= cby + 1; by++) {
      if (by < 0 || by >= bh) continue;
      for (let bx = cbx - 1; bx <= cbx + 1; bx++) {
        if (bx < 0 || bx >= bw) continue;
        const b = by * bw + bx;
        for (let k = start[b]; k < start[b + 1]; k++) {
          const dx = idx.x[k] - x, dy = idx.y[k] - y;
          const d2 = dx * dx + dy * dy;
          if (strict ? d2 < lim : d2 <= lim) return true;
        }
      }
    }
    return false;
  }

  /** Bit 6, fuer alle Spieler gleich. */
  private unownedLand(game: Game): Uint8Array {
    if (this.bit6) return this.bit6;
    const out = new Uint8Array(NCELLS);
    const state = game.map().tileStateBuffer();
    const { landStart, landRefs } = this;
    for (let gi = 0; gi < NCELLS; gi++) {
      for (let k = landStart[gi], e = landStart[gi + 1]; k < e; k++) {
        if ((state[landRefs[k]] & OWNER_MASK) === 0) { out[gi] = 1; break; }
      }
    }
    this.bit6 = out;
    return out;
  }

  // ───────────────────────── je (Tick, Spieler) ─────────────────────────

  compute(game: Game, player: Player, ownerG: Uint16Array): Buffer {
    this.startTick(game);
    const sid = player.smallID();
    const hit = this.cache.get(sid);
    if (hit) { this.stats.cached++; return hit; }
    this.stats.computed++;

    const out = Buffer.alloc(CELL_BYTES);
    // owner_major, u16 LE unabhaengig von der Plattform
    for (let i = 0; i < NCELLS; i++) {
      const v = ownerG[i];
      out[2 * i] = v & 0xff;
      out[2 * i + 1] = v >>> 8;
    }
    const legal = out.subarray(OFF_LEGAL, OFF_LEGAL + NCELLS);
    const { cellOf, terr, W } = this;
    const state = game.map().tileStateBuffer();

    // Bit 0-2 und own_frac aus den eigenen Kacheln
    const cnt = this.cnt;
    cnt.fill(0);
    const bit1 = this.legalBit1;
    const sIdx = bit1 ? this.structures(game) : null;
    const lim = this.minDist2;
    for (const t of player.tiles()) {
      const gi = cellOf[t];
      cnt[gi]++;
      const tt = terr[t];
      // Bit 6: eigenes passierbares Land zaehlt mit (Umzug in der Spawnphase)
      const l = legal[gi] | ((tt & LAND) && (tt & MAG_MASK) !== IMPASSABLE ? B6 : 0);
      if (!bit1) { legal[gi] = l | B0; continue; }
      const shore = (tt & (LAND | SHORE)) === (LAND | SHORE);
      if ((l & B1) === 0 || ((l & B2) === 0 && shore)) {
        const x = t % W, y = (t - x) / W;
        if (!this.near(sIdx!, x, y, lim, true)) {
          legal[gi] = l | B0 | B1 | (shore ? B2 : 0);
          continue;
        }
      }
      legal[gi] = l | B0;
    }
    const frac = out.subarray(OFF_FRAC, OFF_FRAC + NCELLS);
    for (let gi = 0; gi < NCELLS; gi++) {
      if (cnt[gi] > 0) frac[gi] = Math.round((255 * cnt[gi]) / this.cellTiles[gi]);
    }

    // Bit 3: Boots-Landekachel
    const reach = new Set<number>();
    for (const b of player.borderTiles()) {
      if ((terr[b] & (LAND | SHORE)) !== (LAND | SHORE)) continue;
      const c = this.comp(game, b);
      if (c >= 0) reach.add(c);
    }
    if (reach.size > 0) {
      const att = this.attackable;
      att.fill(-1);
      att[0] = 1;
      att[sid] = 0;
      const { coastStart, coastRefs, coastComp } = this;
      for (let gi = 0; gi < NCELLS; gi++) {
        for (let k = coastStart[gi], e = coastStart[gi + 1]; k < e; k++) {
          const c = coastComp[k];
          if (c < 0 || !reach.has(c)) continue;
          const o = state[coastRefs[k]] & OWNER_MASK;
          let a = att[o];
          if (a === -1) {
            const p = game.playerBySmallID(o);
            a = p.isPlayer() && player.canAttackPlayer(p as Player) ? 1 : 0;
            att[o] = a;
          }
          if (a === 1) { legal[gi] |= B3; break; }
        }
      }
    }

    // Bit 4: Wasser in der Komponente eines eigenen fertigen Hafens
    const ports = player.units(UnitType.Port).filter((u) => u.isActive() && !u.isUnderConstruction());
    if (ports.length > 0) {
      const memo = new Map<number, boolean>();
      const { wcStart, wcComps } = this;
      for (let gi = 0; gi < NCELLS; gi++) {
        for (let k = wcStart[gi], e = wcStart[gi + 1]; k < e; k++) {
          const c = wcComps[k];
          let ok = memo.get(c);
          if (ok === undefined) {
            ok = ports.some((p) => game.hasWaterComponent(p.tile(), c));
            memo.set(c, ok);
          }
          if (ok) { legal[gi] |= B4; break; }
        }
      }
    }

    // Bit 5, Bit 6
    const b6 = this.unownedLand(game);
    for (let gi = 0; gi < NCELLS; gi++) {
      if (this.waterAny[gi]) legal[gi] |= B5;
      if (b6[gi]) legal[gi] |= B6;
    }

    // Bit 7
    this.nukeBits(game, player, legal, state);

    this.cache.set(sid, out);
    return out;
  }

  private nukeBits(game: Game, player: Player, legal: Buffer, state: Uint16Array) {
    if (!player.isAlive() || game.isSpawnImmunityActive()) return;
    const ready = player.units(UnitType.MissileSilo).some(
      (s) => s.isActive() && !s.isInCooldown() && !s.isUnderConstruction());
    if (!ready) return;
    const gameOver = game.getWinner() !== null;
    const mates = new Uint8Array(OWNER_MASK + 1);
    let nMates = 0;
    if (!gameOver) {
      for (const q of game.allPlayers()) {
        if (player.isOnSameTeam(q)) { mates[q.smallID()] = 1; nMates++; }
      }
    }
    // Teamkameraden-Bauwerke: nur im Teammodus, aktiv, NICHT im Bau (anyUnitNearby-Standard)
    let mIdx: StructIndex | null = null;
    const nearCell = new Uint8Array(NCELLS);
    if (!gameOver && this.teamMode && nMates > 0) {
      const ms = game.units(Structures.types).filter((u) =>
        u.isActive() && !u.isUnderConstruction() && u.owner().isPlayer() && mates[u.owner().smallID()] === 1);
      if (ms.length > 0) {
        mIdx = this.bucketize(game, ms);
        // Zellen markieren, deren Rechteck den Radius eines Bauwerks schneidet
        const R = this.atomOuter;
        for (let k = 0; k < mIdx.x.length; k++) {
          const x = mIdx.x[k], y = mIdx.y[k];
          const gx0 = ((Math.max(0, x - R) * GW) / this.W) | 0, gx1 = ((Math.min(this.W - 1, x + R) * GW) / this.W) | 0;
          const gy0 = ((Math.max(0, y - R) * GH) / this.H) | 0, gy1 = ((Math.min(this.H - 1, y + R) * GH) / this.H) | 0;
          for (let gy = gy0; gy <= gy1; gy++) for (let gx = gx0; gx <= gx1; gx++) nearCell[gy * GW + gx] = 1;
        }
      }
    }
    if (nMates === 0) {
      for (let gi = 0; gi < NCELLS; gi++) if (this.passAny[gi]) legal[gi] |= B7;
      return;
    }
    // Kachelweise, Abbruch beim ersten Treffer je Zelle
    const { terr, W } = this;
    const lim = this.atomOuter2;
    for (let gy = 0; gy < GH; gy++) {
      const y0 = this.rowStart[gy], y1 = this.rowStart[gy + 1];
      for (let gx = 0; gx < GW; gx++) {
        const gi = gy * GW + gx;
        if (!this.passAny[gi]) continue;
        const x0 = this.colStart[gx], x1 = this.colStart[gx + 1];
        const chk = mIdx !== null && nearCell[gi] === 1;
        let found = false;
        for (let y = y0; y < y1 && !found; y++) {
          const row = y * W;
          for (let x = x0; x < x1; x++) {
            const r = row + x;
            const t = terr[r];
            if ((t & LAND) && (t & MAG_MASK) === IMPASSABLE) continue;
            if (mates[state[r] & OWNER_MASK]) continue;
            if (chk && this.near(mIdx!, x, y, lim, false)) continue;
            found = true;
            break;
          }
        }
        if (found) legal[gi] |= B7;
      }
    }
  }
}

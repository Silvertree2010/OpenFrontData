/**
 * Unabhaengige Brute-Force-Referenz fuer die Zellfakten (nur Tests).
 *
 * Laeuft ueber JEDE Kachel der Karte und fragt die Bedingungen direkt mit
 * Engine-APIs ab, ohne die Listen, Eimer, Merker und Terrain-Kopie aus cells.ts:
 *   Bit 0    game.owner(t) === player (Objektvergleich statt smallID)
 *   Bit 1/2  game.nearbyUnits(t, structureMinDist, Structures, -, inclUnderConstr)
 *            und distSquared < minDist² (derselbe Aufruf wie validStructureSpawnTiles)
 *   Bit 3    Bedingungen aus canBuildTransportShip/closestReachableShore auf der
 *            Kachel selbst; zusaetzlich Stichprobe gegen die echte Engine-Funktion
 *            SpatialQuery.closestReachableShore(owner, player, t, maxDist = 0)
 *   Bit 4    PlayerImpl.warshipSpawn(t) !== false (Engine-Funktion)
 *   Bit 5/6  game.isWater / (herrenlos oder eigen) && isLand && !isImpassable
 *   Bit 7    player.isAlive() && PlayerImpl.nukeSpawn(t, AtomBomb) !== false (Engine-Funktion)
 * Zelle per Math.floor(y*90/H), Math.floor(x*180/W); own_frac per Math.round.
 * Langsam ist egal. Einzige Abkuerzung: Ein Bit, das in der Zelle schon gesetzt
 * ist, wird fuer weitere Kacheln der Zelle nicht mehr abgefragt (ODER ist monoton).
 */
import { Game, Player, Structures, UnitType } from "../../../vendor/openfront/src/core/game/Game";
import { SpatialQuery } from "../../../vendor/openfront/src/core/pathfinding/spatial/SpatialQuery";

export interface RefStats {
  /** Stichproben Bit 3 gegen closestReachableShore */
  spot: number;
  spotMismatch: number;
}

export function referenceCells(
  game: Game, player: Player, ownerG: Uint16Array,
  opts: { legalBit1: boolean; spotEvery?: number; spotMax?: number }, stats: RefStats,
): Buffer {
  const W = game.width(), H = game.height();
  const N = 16200;
  const out = Buffer.alloc(N * 4);
  const dv = new DataView(out.buffer, out.byteOffset, out.byteLength);
  for (let i = 0; i < N; i++) dv.setUint16(2 * i, ownerG[i], true);

  const tot = new Float64Array(N), own = new Float64Array(N);
  const legal = new Uint8Array(N);
  const md = game.config().structureMinDist();
  const p = player as any;

  const reach = new Set<number>();
  for (const b of player.borderTiles()) {
    if (game.isShore(b) && game.isLand(b)) {
      const c = game.getWaterComponent(b);
      if (c !== null) reach.add(c);
    }
  }
  const alive = player.isAlive();
  const sq = new SpatialQuery(game);
  const spotEvery = opts.spotEvery ?? 37, spotMax = opts.spotMax ?? 300;
  let candidates = 0, spots = 0;

  for (let y = 0; y < H; y++) {
    const gy = Math.floor((y * 90) / H);
    for (let x = 0; x < W; x++) {
      const t = game.ref(x, y);
      const gi = gy * 180 + Math.floor((x * 180) / W);
      tot[gi]++;
      const o = game.owner(t);
      const mine = o === player;
      if (mine) { own[gi]++; legal[gi] |= 1; }

      if (opts.legalBit1 && mine) {
        const shore = game.isShore(t);
        if ((legal[gi] & 2) === 0 || ((legal[gi] & 4) === 0 && shore)) {
          const blocked = game.nearbyUnits(t, md, Structures.types, undefined, true)
            .some((e) => e.distSquared < md * md);
          if (!blocked) { legal[gi] |= 2; if (shore) legal[gi] |= 4; }
        }
      }

      if (game.isLand(t) && game.isShore(t) && !mine &&
          (!o.isPlayer() || player.canAttackPlayer(o as Player))) {
        const doSpot = spots < spotMax && (candidates++ % spotEvery === 0);
        if ((legal[gi] & 8) === 0 || doSpot) {
          const c = game.getWaterComponent(t);
          const ok = c !== null && reach.has(c);
          if (ok) legal[gi] |= 8;
          if (doSpot) {
            spots++;
            const eng = sq.closestReachableShore(o, player, t, 0) === t;
            stats.spot++;
            if (eng !== ok) stats.spotMismatch++;
          }
        }
      }

      const water = game.isWater(t);
      if (water) legal[gi] |= 32;
      if ((legal[gi] & 16) === 0 && water && p.warshipSpawn(t) !== false) legal[gi] |= 16;
      if ((legal[gi] & 64) === 0 && (!game.hasOwner(t) || mine) && game.isLand(t) && !game.isImpassable(t)) legal[gi] |= 64;
      if ((legal[gi] & 128) === 0 && alive && p.nukeSpawn(t, UnitType.AtomBomb) !== false) legal[gi] |= 128;
    }
  }
  for (let i = 0; i < N; i++) {
    out[2 * N + i] = tot[i] > 0 ? Math.round((own[i] * 255) / tot[i]) : 0;
    out[3 * N + i] = legal[i];
  }
  return out;
}

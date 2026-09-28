/**
 * Beobachtung aus dem gespiegelten Spielzustand — genau wie im Training.
 *
 * Gerechnet wird mit demselben Code, der im Materialisierer und im portierten Client
 * steht (`obsModel.ts`, `cellFacts.ts`, `aiAnfrage.ts`); hier wird er nur in die Form
 * der Schnittstelle gegossen. Bitgleichheit zu den Trainingsblöcken ist gemessen
 * (`aitest/kernprobe.ts`), darum wird an der Rechnung nichts angefasst.
 *
 * Reihenfolge der Vektoren — sie ist Vertrag, nicht Geschmack:
 *   own[]    in der Reihenfolge OWN_FELDER (unten), Rest 0
 *   opp[]    24 Steckplätze × OPP_FELDER.length, zeilenweise; leere Plätze 0
 *   oppMaske 24 Byte, 1 = Platz belegt
 *   config[] in der Reihenfolge CONFIG_FELDER, Wahrheitswerte als 0/1
 *
 * Für den Inferenz-Server (trainer/inf_d0.py) wird zusätzlich die **rohe** Anfrage
 * mitgeführt (`_anfrage`), denn der Server erwartet JSON mit `map_b64`/`cells_b64` und
 * den Objekten `own`/`opps`, nicht die flachen Vektoren. Die Schnittstelle bleibt davon
 * unberührt: `_anfrage` ist ein zusätzliches Feld, kein verändertes.
 */
import { AiAnfrage } from "../src/core/aiAnfrage";
import { CELL_BYTES, NCELLS } from "../src/core/cellFacts";
import { Game, Player } from "../src/core/game/Game";
import type { Beobachtung } from "./schnittstelle";
import { zusatzAnfrage } from "./zusatzFelder";
import type { ZusatzSpur } from "./zusatzFelder";

/** Feste Reihenfolge der eigenen Kennzahlen. Entspricht ObsEncoder.encodeVec().own. */
export const OWN_FELDER = [
  "troops", "gold", "tiles", "borderLen", "allies", "betrayals", "traitor",
  "tick", "alive", "troopsRatio", "boardShare", "boatsOut", "isLeader",
  "n_City", "n_Defense Post", "n_SAM Launcher", "n_Missile Silo", "n_Port", "n_Factory",
] as const;

/** Feste Reihenfolge je Gegner. Entspricht OpponentFeat ohne `id`. */
export const OPP_FELDER = [
  "troops", "gold", "tiles", "ally", "sameTeam", "traitor", "human", "bordersMe",
  "contactShare", "borderToMe", "borderToOther", "borderToUnowned", "gapsRemaining",
  "annexProof", "allyTicksLeft", "isLeader", "hasSilo", "hasSam", "inClan",
] as const;

/** Zahlen- und Wahrheitsfelder der Spielkonfiguration, feste Reihenfolge. */
export const CONFIG_FELDER = [
  "maxPlayers", "bots", "infiniteGold", "infiniteTroops", "instantBuild",
  "disableNPCs", "disabledUnits", "gameMode", "difficulty",
] as const;

export const OPP_PLAETZE = 24;

function zahl(x: unknown): number {
  if (typeof x === "boolean") return x ? 1 : 0;
  const n = Number(x);
  return Number.isFinite(n) ? n : 0;
}

export class Beobachter {
  private readonly anfrage: AiAnfrage;
  constructor(private readonly config: any) {
    this.anfrage = new AiAnfrage(config ?? null);
  }

  /**
   * Eine Beobachtung für diesen Spieler im JETZIGEN Zustand des Spiegels.
   * Die Puffer sind frische Kopien, damit sie an den Hauptfaden übergeben werden können.
   */
  rechne(game: Game, spieler: Player, meineID: string, takt: number,
         spur: ZusatzSpur | null = null): Beobachtung {
    const roh: any = this.anfrage.baue(game, spieler, takt);
    // Netz D1: Zusatzfelder (zusatz_b64, zusatz_sig) an die Anfrage. Immer dabei, wenn die
    // Spur läuft — ein D0-Server liest sie nicht, ein D1-Server verlangt sie. Ein Fehler
    // hier kostet nur die Zusatzfelder, nie die Beobachtung.
    if (spur) {
      try {
        Object.assign(roh, zusatzAnfrage(spur, spieler, roh.ctx));
      } catch (e: any) {
        roh.zusatz_fehler = String(e?.message ?? e).slice(0, 200);
      }
    }
    const karte = new Uint8Array(this.anfrage.u8);            // 18*90*180
    const zellen = this.anfrage.zellen;                       // 4 Blöcke à 16200
    const owner = new Uint16Array(NCELLS);
    const frac = new Uint8Array(NCELLS);
    const legal = new Uint8Array(NCELLS);
    if (zellen && zellen.length === CELL_BYTES) {
      for (let i = 0; i < NCELLS; i++) owner[i] = zellen[2 * i] | (zellen[2 * i + 1] << 8);
      frac.set(zellen.subarray(2 * NCELLS, 3 * NCELLS));
      legal.set(zellen.subarray(3 * NCELLS, 4 * NCELLS));
    }

    const own = new Float32Array(OWN_FELDER.length);
    for (let i = 0; i < OWN_FELDER.length; i++) own[i] = zahl(roh.own?.[OWN_FELDER[i]]);

    const opp = new Float32Array(OPP_PLAETZE * OPP_FELDER.length);
    const oppMaske = new Uint8Array(OPP_PLAETZE);
    const gegner: any[] = roh.opps ?? [];
    for (let g = 0; g < Math.min(OPP_PLAETZE, gegner.length); g++) {
      oppMaske[g] = 1;
      const basis = g * OPP_FELDER.length;
      for (let i = 0; i < OPP_FELDER.length; i++) opp[basis + i] = zahl(gegner[g]?.[OPP_FELDER[i]]);
    }

    const config = new Float32Array(CONFIG_FELDER.length);
    for (let i = 0; i < CONFIG_FELDER.length; i++) {
      const v = (this.config ?? {})[CONFIG_FELDER[i]];
      config[i] = Array.isArray(v) ? v.length : zahl(v);
    }

    const b: any = {
      tick: game.ticks(),
      karte, owner, frac, legal,
      own, opp, oppMaske, config,
      meineID,
      lebe: spieler.isAlive(),
      startphase: game.inSpawnPhase(),
    };
    // Zusatz, nicht Teil der Schnittstelle: die rohe Anfrage für den Inferenz-Server.
    b._anfrage = roh;
    return b as Beobachtung;
  }

  /** Übertragbare Puffer einer Beobachtung, für postMessage. */
  static puffer(b: Beobachtung): ArrayBuffer[] {
    return [
      b.karte.buffer, b.owner.buffer, b.frac.buffer, b.legal.buffer,
      b.own.buffer, b.opp.buffer, b.oppMaske.buffer, b.config.buffer,
    ] as ArrayBuffer[];
  }
}

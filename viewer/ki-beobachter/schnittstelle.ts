/**
 * Die Schnittstelle zwischen Kern und Oberfläche. Wörtlich vorgegeben, wird nicht
 * mehr geändert: `kern.ts` erfüllt sie, `politik.ts` und `einblendung.ts` lesen sie.
 *
 * Der Kern legt seine Umsetzung unter `globalThis.__KI_KERN__` ab.
 */
export type Absicht = { art: string; [feld: string]: unknown };
export type Beobachtung = {
  tick: number;
  karte: Uint8Array; // 18*90*180, wie im Training
  owner: Uint16Array; // 16200
  frac: Uint8Array; // 16200
  legal: Uint8Array; // 16200
  own: Float32Array;
  opp: Float32Array;
  oppMaske: Uint8Array;
  config: Float32Array;
  meineID: string;
  lebe: boolean;
  startphase: boolean;
};
export interface Kern {
  beobachtung(): Beobachtung | null; // null, solange nichts spielbar ist
  sendeAbsicht(a: Absicht): boolean; // false, wenn Senden aus ist oder kein Socket da
  aufTick(f: (t: number) => void): void;
  zustand(): {
    partie: string | null;
    tick: number;
    spieler: number;
    fehler: string | null;
  };
}
export const SENDEN_AN = false; // Standard aus; der andere Agent liest das nur

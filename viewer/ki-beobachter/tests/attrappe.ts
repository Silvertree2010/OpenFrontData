/**
 * Attrappen für die Prüfung ohne Browser: ein `Kern` mit fester Beobachtung, ein
 * winziges DOM und ein localStorage im Speicher.
 *
 * Die Beobachtung ist deterministisch erzeugt (fester Zufallskeim), nicht aus dem
 * Materialisierer-Pool: der liegt auf arch/node-1, nicht auf diesem Rechner. Die
 * Formate stimmen bitgenau mit dem Pool überein (18·90·180 Karte, 16'200 Zellen,
 * owner u16 / frac u8 / legal u8, 19 own-, 24×19 opp-, 20 config-Merkmale); welche
 * Zellen legal sind, wird hier absichtlich von Hand gesetzt, damit die Kandidatenprüfung
 * eine bekannte Antwort hat.
 */
import type { Absicht, Beobachtung, Kern } from "../schnittstelle.ts";
import {
  CONFIG_FELDER,
  GH,
  GW,
  MAPLEN,
  MAX_OPP,
  NCELLS,
  OPP_DIM,
  OPP_FELDER,
  OWN_DIM,
  OWN_FELDER,
} from "../politik.ts";

/** Kleiner, reproduzierbarer Zufall (LCG). */
export function keim(s: number): () => number {
  let x = s >>> 0;
  return () => {
    x = (x * 1664525 + 1013904223) >>> 0;
    return x / 4294967296;
  };
}

export interface FixturOpts {
  tick: number;
  startphase: boolean;
  lebe: boolean;
  /** Zelle → legal-Byte, wird über die Grundbelegung gelegt. */
  legalExtra: Record<number, number>;
}

export function festeBeobachtung(o: Partial<FixturOpts> = {}): Beobachtung {
  const r = keim(20260912);
  const karte = new Uint8Array(MAPLEN);
  for (let i = 0; i < MAPLEN; i++) karte[i] = (r() * 256) | 0;
  const owner = new Uint16Array(NCELLS);
  const frac = new Uint8Array(NCELLS);
  const legal = new Uint8Array(NCELLS);
  for (let i = 0; i < NCELLS; i++) {
    owner[i] = i % 7 === 0 ? 3 : i % 5 === 0 ? 9 : 0; // 3 = ich, 9 = ein Gegner, 0 = frei
    frac[i] = owner[i] === 3 ? 200 : 0;
    // Grundbelegung: nur Wasser (Bit 5) und herrenloses Land (Bit 6) sind breit gesetzt.
    legal[i] = (i % 3 === 0 ? 1 << 5 : 0) | (i % 4 === 0 ? 1 << 6 : 0);
  }
  for (const [z, b] of Object.entries(o.legalExtra ?? {})) legal[Number(z)] |= b;

  // Rohwerte in der Reihenfolge, die `beobachtung.ts` festschreibt.
  const setz = (v: Float32Array, felder: string[], werte: Record<string, number>, ab = 0) => {
    for (const [k, x] of Object.entries(werte)) {
      const i = felder.indexOf(k);
      if (i < 0) throw new Error(`Feld ${k} gibt es nicht`);
      v[ab + i] = x;
    }
  };
  const own = new Float32Array(OWN_DIM);
  setz(own, OWN_FELDER, {
    troops: 120000,
    gold: 54321,
    tiles: 1234,
    borderLen: 210,
    allies: 1,
    tick: o.tick ?? 640,
    alive: 12,
    troopsRatio: 0.42,
    boardShare: 0.037,
    n_City: 3,
  });

  const opp = new Float32Array(MAX_OPP * OPP_DIM);
  const oppMaske = new Uint8Array(MAX_OPP);
  for (let i = 0; i < 5; i++) {
    oppMaske[i] = 1;
    // Gegner 0 und 1 sind grösser als ich (1234 Kacheln) → Platz 3.
    const kacheln = [9000, 4000, 900, 400, 100][i];
    setz(opp, OPP_FELDER, {
      troops: kacheln * 20,
      gold: kacheln * 3,
      tiles: kacheln,
      human: 1,
    }, i * OPP_DIM);
  }

  const config = new Float32Array(CONFIG_FELDER.length);
  setz(config, CONFIG_FELDER, { maxPlayers: 50, bots: 30 });

  return {
    tick: o.tick ?? 640,
    karte,
    owner,
    frac,
    legal,
    own,
    opp,
    oppMaske,
    config,
    meineID: "client-abc",
    lebe: o.lebe ?? true,
    startphase: o.startphase ?? false,
  };
}

/** Eine Zelle, die für die Gruppe `bau` (Bit 1) legal ist, und eine, die es nicht ist. */
export const ZELLE_BAU_OK = 5000;
export const ZELLE_BAU_NEIN = 5001;

export class AttrappenKern implements Kern {
  beob: Beobachtung | null;
  gesendet: Absicht[] = [];
  sendenMoeglich = true;
  private ticker: ((t: number) => void)[] = [];
  private zst = {
    partie: "partie-1" as string | null,
    tick: 0,
    spieler: 6,
    fehler: null as string | null,
  };

  constructor(beob: Beobachtung | null = festeBeobachtung()) {
    this.beob = beob;
  }

  beobachtung(): Beobachtung | null {
    return this.beob;
  }
  sendeAbsicht(a: Absicht): boolean {
    if (!this.sendenMoeglich) return false;
    this.gesendet.push(a);
    return true;
  }
  aufTick(f: (t: number) => void): void {
    this.ticker.push(f);
  }
  zustand() {
    return { ...this.zst };
  }
  setzePartie(p: string | null): void {
    this.zst.partie = p;
  }
  /** Tick auslösen und auf die (asynchrone) Politik warten. */
  async tickeAn(t: number): Promise<void> {
    this.zst.tick = t;
    for (const f of this.ticker) await (f(t) as unknown as Promise<void>);
  }
}

// ── Winziges DOM ─────────────────────────────────────────────────────────────
// Nur so viel, wie die Einblendung braucht. `innerHTML` wirft absichtlich: wird es je
// benutzt, fällt der Test um.

/** Genug von CSSStyleDeclaration: `cssText` wird in Einzelwerte zerlegt. */
export class Stil {
  [name: string]: string | undefined;
  set cssText(v: string) {
    for (const teil of String(v).split(";")) {
      const i = teil.indexOf(":");
      if (i <= 0) continue;
      const name = teil
        .slice(0, i)
        .trim()
        .replace(/-([a-z])/g, (_, c: string) => c.toUpperCase());
      this[name] = teil.slice(i + 1).trim();
    }
  }
  get cssText(): string {
    return Object.keys(this)
      .map((k) => `${k}:${this[k]}`)
      .join(";");
  }
}

export class Knoten {
  tag: string;
  kinder: Knoten[] = [];
  eigenText = "";
  style = new Stil();
  id = "";
  disabled = false;
  onclick: (() => void) | null = null;

  constructor(tag: string) {
    this.tag = tag;
    Object.defineProperty(this, "innerHTML", {
      set() {
        throw new Error("innerHTML benutzt — verboten (fremde Texte nur als Text)");
      },
      get() {
        throw new Error("innerHTML gelesen — verboten");
      },
    });
  }
  get textContent(): string {
    return this.eigenText + this.kinder.map((k) => k.textContent).join("");
  }
  set textContent(v: string) {
    this.kinder = [];
    this.eigenText = String(v);
  }
  append(...k: Knoten[]): void {
    this.kinder.push(...k);
  }
  appendChild(k: Knoten): Knoten {
    this.kinder.push(k);
    return k;
  }
  replaceChildren(...k: Knoten[]): void {
    this.eigenText = "";
    this.kinder = k;
  }
  remove(): void {
    /* für den Test ohne Elternzeiger genügt das */
  }
  klick(): void {
    if (!this.disabled) this.onclick?.();
  }
  /** Alle Knoten mit diesem Tag, rekursiv. */
  suche(tag: string): Knoten[] {
    const raus: Knoten[] = [];
    for (const k of this.kinder) {
      if (k.tag === tag) raus.push(k);
      raus.push(...k.suche(tag));
    }
    return raus;
  }
}

class TextKnoten extends Knoten {
  constructor(t: string) {
    super("#text");
    this.eigenText = t;
  }
}

export function setzeDom(): { body: Knoten; aufraeumen: () => void } {
  const body = new Knoten("body");
  const g = globalThis as Record<string, unknown>;
  const vorher = { document: g.document, localStorage: g.localStorage };
  g.document = {
    body,
    createElement: (t: string) => new Knoten(t),
    createTextNode: (t: string) => new TextKnoten(t),
  };
  const daten = new Map<string, string>();
  g.localStorage = {
    getItem: (k: string) => daten.get(k) ?? null,
    setItem: (k: string, v: string) => void daten.set(k, String(v)),
    removeItem: (k: string) => void daten.delete(k),
  };
  return {
    body,
    aufraeumen: () => {
      g.document = vorher.document;
      g.localStorage = vorher.localStorage;
    },
  };
}

/** Antwort des Inferenz-Servers nachbilden. */
export function antwortHolen(
  antwort: unknown,
  aufzeichnen?: (koerper: unknown) => void,
): typeof fetch {
  return (async (_url: string, init: { body: string }) => {
    aufzeichnen?.(JSON.parse(init.body));
    return {
      ok: true,
      status: 200,
      json: async () => antwort,
    };
  }) as unknown as typeof fetch;
}

export { GH, GW, NCELLS };

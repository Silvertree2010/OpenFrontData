/**
 * Trajektorien-Recorder der Arena (`--spur ORDNER`): je Partie die Entscheidungen aller
 * Netz-Spieler im Format des Materialisierers v2, damit der Trainer-Lader (daten.PartieLeser)
 * sie ohne zweite Pipeline liest.
 *
 * Dateien je Partie (gid = Spiel-ID, mit --spur-kennung K: "<Spiel-ID>_K"):
 *   <gid>.maps        [u32 LE Länge][zstd(u8[18·90·180])] je Zeile — genau map_b64 der Anfrage
 *   <gid>.cells       dasselbe für die Zellfakten, nur Zeilen mit ausgeführtem Zellzeiger (cell ≥ 0)
 *   <gid>.meta.zst    zstd(JSONL), eine Zeile je Entscheidung (Felder wie DESIGN §5.2, dazu lab/rl)
 *   <gid>.zusatz.zst  Zusatzfelder wie zusatz/src/lauf.ts (Kopf + 4 Blöcke, float16)
 *   <gid>.hdr.json    Spielkopf (format 2, config, players, ki = Ergebnis je KI, rl = Serverstand)
 *   <gid>.spur.json   Marke "Arena fertig". Die .ok schreibt erst trajektorie.py abschliessen,
 *                     nachdem es Rückgaben und Vorteile eingetragen hat — vorher sieht der
 *                     Lader die Partie nicht (er nimmt nur Partien mit gültiger .ok).
 * Alles wird als .tmp geschrieben und umbenannt, die Marke zuletzt.
 */
import fs from "fs";
import path from "path";
import { zstdCompressSync } from "zlib";
import { ANGR_FELDER, EINH_FELDER, F_ANGR, F_EINH, F_GLOB, F_OPP, GLOBAL_FELDER, MAX_ANGR, MAX_EINH,
  MAX_OPP, OPP_FELDER } from "./zusatzFelder";

const MAP_BYTES = 18 * 90 * 180;
const CELL_BYTES = 90 * 180 * 4;
const ZUS_N = MAX_OPP * F_OPP + F_GLOB + MAX_EINH * F_EINH + MAX_ANGR * F_ANGR;

function block(b: Buffer): Buffer {
  const z = zstdCompressSync(b);
  const kopf = Buffer.alloc(4);
  kopf.writeUInt32LE(z.length, 0);
  return Buffer.concat([kopf, z]);
}

export class SpurSchreiber {
  private maps: Buffer[] = [];
  private cells: Buffer[] = [];
  private zeilen: string[] = [];
  private zus: Float32Array[] = [];
  private nachArt: Record<string, number> = {};
  private ergebnisse: Record<string, number> = {};
  readonly datei = (n: string) => path.join(this.ordner, `${this.gid}.${n}`);

  constructor(readonly ordner: string, readonly gid: string, private readonly zusatz: boolean) {
    fs.mkdirSync(ordner, { recursive: true });
  }

  get n(): number { return this.zeilen.length; }

  /**
   * Eine Entscheidung. `meta` ist die fertige Metazeile ohne `cell`; `mitZelle` sagt, ob der
   * Zellblock gebraucht wird (ausgeführter Zellzeiger). Die Blöcke kommen roh aus der Anfrage.
   */
  zeile(meta: any, body: any, mitZelle: boolean): void {
    const karte = Buffer.from(body.map_b64, "base64");
    if (karte.length !== MAP_BYTES) throw new Error(`Karte ${karte.length} Byte statt ${MAP_BYTES}`);
    meta.cell = -1;
    if (mitZelle && body.cells_b64) {
      const z = Buffer.from(body.cells_b64, "base64");
      if (z.length !== CELL_BYTES) throw new Error(`Zellfakten ${z.length} Byte statt ${CELL_BYTES}`);
      meta.cell = this.cells.length;
      this.cells.push(block(z));
    }
    if (this.zusatz) {
      const roh = Buffer.from(body.zusatz_b64 ?? "", "base64");
      if (roh.length !== ZUS_N * 4) throw new Error(`zusatz_b64 ${roh.length} Byte statt ${ZUS_N * 4}`);
      // Kopie: roh teilt sich sonst womöglich den Speicher mit dem Pool von Buffer
      this.zus.push(new Float32Array(roh.buffer.slice(roh.byteOffset, roh.byteOffset + roh.length)));
    }
    this.maps.push(block(karte));
    this.zeilen.push(JSON.stringify(meta));
    this.nachArt[meta.kind] = (this.nachArt[meta.kind] ?? 0) + 1;
    const e = meta.rl?.ergebnis ?? "?";
    this.ergebnisse[e] = (this.ergebnisse[e] ?? 0) + 1;
  }

  private schreibe(name: string, daten: Buffer | string): number {
    const ziel = this.datei(name), tmp = ziel + ".tmp";
    fs.writeFileSync(tmp, daten);
    fs.renameSync(tmp, ziel);
    return fs.statSync(ziel).size;
  }

  /** Zusatzdatei wie lauf.ts: Kopfzeile + [n][24][F_OPP] + [n][F_GLOB] + [n][128][F_EINH] + [n][16][F_ANGR]. */
  private zusatzDatei(): Buffer {
    const n = this.zus.length;
    const teile = [MAX_OPP * F_OPP, F_GLOB, MAX_EINH * F_EINH, MAX_ANGR * F_ANGR];
    const bloecke = teile.map((k) => new Float32Array(n * k));
    for (let i = 0; i < n; i++) {
      let o = 0;
      teile.forEach((k, j) => { bloecke[j].set(this.zus[i].subarray(o, o + k), i * k); o += k; });
    }
    const F16A = (globalThis as any).Float16Array;
    const dtype = F16A ? "float16" : "float32";
    const roh = bloecke.map((a) => {
      if (!F16A) return Buffer.from(a.buffer, a.byteOffset, a.byteLength);
      const h = new F16A(a.length);
      h.set(a);                                  // round-to-nearest-even, wie lauf.ts
      return Buffer.from(h.buffer, h.byteOffset, h.byteLength);
    });
    const kopf = JSON.stringify({ format: 2, gid: this.gid, samples: n, opp: MAX_OPP, einheit: MAX_EINH,
      angriff: MAX_ANGR, felder_opp: OPP_FELDER, felder_global: GLOBAL_FELDER, felder_einheit: EINH_FELDER,
      felder_angriff: ANGR_FELDER, dtype, quelle: "arena-spur" });
    return zstdCompressSync(Buffer.concat([Buffer.from(kopf + "\n", "utf8"), ...roh]));
  }

  /** Alles schreiben, die Marke .spur.json zuletzt. Ohne Zeile wird nichts geschrieben. */
  schliessen(hdr: any): string | null {
    if (!this.zeilen.length) return null;
    const files: Record<string, number> = {};
    files[`${this.gid}.maps`] = this.schreibe("maps", Buffer.concat(this.maps));
    if (this.cells.length) files[`${this.gid}.cells`] = this.schreibe("cells", Buffer.concat(this.cells));
    if (this.zusatz) files[`${this.gid}.zusatz.zst`] = this.schreibe("zusatz.zst", this.zusatzDatei());
    files[`${this.gid}.hdr.json`] = this.schreibe("hdr.json", JSON.stringify({ format: 2, gid: this.gid, ...hdr }));
    files[`${this.gid}.meta.zst`] = this.schreibe("meta.zst",
      zstdCompressSync(Buffer.from(this.zeilen.join("\n"), "utf8")));   // ohne Umbruch am Ende, wie der Materialisierer
    this.schreibe("spur.json", JSON.stringify({ format: 2, gid: this.gid, samples: this.zeilen.length,
      spatial: this.cells.length, by_kind: this.nachArt, ergebnis: this.ergebnisse, files,
      legal_bit1: true, zusatz: this.zusatz }));
    return this.datei("spur.json");
  }
}

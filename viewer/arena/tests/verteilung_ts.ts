/**
 * Rechnet spielweise.ts verteilung() für die Fälle aus tests/verteilung_paritaet.py (JSON-Datei
 * als Argument) und schreibt die Ergebnisse als JSON auf stdout. Läuft im Client:
 *   cd <client> && npx tsx arena/tests/verteilung_ts.ts faelle.json
 */
import fs from "fs";
import { landZellen, Puffer, verteilung } from "../spielweise";

const faelle = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const aus: any[] = [];
for (const f of faelle) {
  const { W, H } = f;
  const terr = Uint8Array.from(f.terrain);
  const eigen = new Uint8Array(W * H);
  for (const r of f.refs) eigen[r] = 1;
  const lz = landZellen(W, H, (r) => (terr[r] & 0x80) !== 0);
  const pf = new Puffer(W * H);
  // zweimal mit demselben Puffer: der Stempel darf zwischen Aufrufen nichts verschleppen
  verteilung(f.refs, f.refs.length, W, H, (r) => eigen[r] === 1, (r) => (terr[r] & 0xc0) === 0xc0, lz, pf);
  const v = verteilung(f.refs, f.refs.length, W, H, (r) => eigen[r] === 1,
    (r) => (terr[r] & 0xc0) === 0xc0, lz, pf);
  aus.push({ landzellen: lz, ...v });
}
process.stdout.write(JSON.stringify(aus) + "\n");

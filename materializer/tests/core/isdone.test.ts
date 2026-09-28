/**
 * isDone / cleanStale (io.ts, DESIGN §5 "Fertig heisst"). Läuft ohne Engine:
 *
 *   npx tsx materializer/tests/core/isdone.test.ts
 *
 * Jeder Fall setzt einen frischen Ordner auf und erwartet true oder false. Die
 * Fälle decken beide Richtungen ab: ein isDone, das immer true (oder immer false)
 * liefert, fällt durch. Exit 1 bei einem Fehlschlag, 2 wenn kein Fall lief.
 */
import fs from "fs";
import os from "os";
import path from "path";
import { cleanStale, isDone } from "../../src/io";

const gid = "AbCd1234";
let pass = 0, fail = 0;

function setup(files: Record<string, string | number>): string {
  const d = fs.mkdtempSync(path.join(os.tmpdir(), "isdone-"));
  for (const [name, c] of Object.entries(files)) {
    fs.writeFileSync(path.join(d, name), typeof c === "number" ? Buffer.alloc(c) : c);
  }
  return d;
}

function okJson(files: Record<string, number>, samples = 1, format = 2) {
  return JSON.stringify({ format, files, samples });
}

function expect(name: string, got: boolean, want: boolean) {
  if (got === want) pass++;
  else { fail++; console.log(`FEHLER ${name}: ${got} statt ${want}`); }
}

const full = { [`${gid}.hdr.json`]: 10, [`${gid}.maps`]: 500, [`${gid}.meta.zst`]: 300 };

// positiv
expect("gültige .ok", isDone(setup({ ...full, [`${gid}.ok`]: okJson(full) }), gid, false), true);
expect(".none format 2", isDone(setup({ [`${gid}.none`]: '{"format":2}' }), gid, false), true);
expect(".err ohne RETRY_ERR", isDone(setup({ [`${gid}.err`]: "x" }), gid, false), true);
expect(".ok 0 Samples nur hdr", isDone(setup({ [`${gid}.hdr.json`]: 10,
  [`${gid}.ok`]: okJson({ [`${gid}.hdr.json`]: 10 }, 0) }), gid, false), true);

// negativ
expect(".ok mit falscher maps-Grösse", isDone(setup({ ...full, [`${gid}.maps`]: 499,
  [`${gid}.ok`]: okJson(full) }), gid, false), false);
expect("winzige .meta.zst ohne .ok", isDone(setup({ [`${gid}.meta.zst`]: 13, [`${gid}.maps`]: 40 }), gid, false), false);
expect("grosse .meta.zst ohne .ok", isDone(setup({ [`${gid}.meta.zst`]: 100000, [`${gid}.maps`]: 400000 }), gid, false), false);
expect(".ok gelistete Datei fehlt", isDone(setup({ [`${gid}.hdr.json`]: 10, [`${gid}.meta.zst`]: 300,
  [`${gid}.ok`]: okJson(full) }), gid, false), false);
expect(".ok ohne meta in files", isDone(setup({ [`${gid}.hdr.json`]: 10, [`${gid}.maps`]: 500,
  [`${gid}.ok`]: okJson({ [`${gid}.hdr.json`]: 10, [`${gid}.maps`]: 500 }) }), gid, false), false);
expect(".ok format 1", isDone(setup({ ...full, [`${gid}.ok`]: okJson(full, 1, 1) }), gid, false), false);
expect(".ok kaputtes JSON", isDone(setup({ ...full, [`${gid}.ok`]: '{"format":2,' }), gid, false), false);
expect("leere v1-.none", isDone(setup({ [`${gid}.none`]: "" }), gid, false), false);
expect(".err mit RETRY_ERR", isDone(setup({ [`${gid}.err`]: "x" }), gid, true), false);
expect("leerer Ordner", isDone(setup({}), gid, false), false);
expect("tmp-Reste allein", isDone(setup({ [`${gid}.maps.tmp`]: 10, [`${gid}.ok.tmp`]: okJson(full) }), gid, false), false);

// cleanStale: löscht alles der Partie, nichts Fremdes
{
  const d = setup({ ...full, [`${gid}.ok`]: okJson(full), [`${gid}.maps.tmp`]: 3, [`${gid}.cells.tmp`]: 3,
    [`${gid}.own.zst.tmp`]: 3, [`${gid}.err`]: "x", "ZzZz9999.maps": 5, "ZzZz9999.ok": "{}" });
  const removed = cleanStale(d, gid).sort();
  const left = fs.readdirSync(d).sort();
  expect("cleanStale löscht 8 Dateien", removed.length === 8, true);
  expect("cleanStale lässt Fremdes", JSON.stringify(left) === JSON.stringify(["ZzZz9999.maps", "ZzZz9999.ok"]), true);
}

console.log(`isdone: ${pass} bestanden, ${fail} durchgefallen`);
process.exit(pass === 0 ? 2 : fail > 0 ? 1 : 0);

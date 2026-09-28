/**
 * Probe des GEBAUTEN Engine-Workers der Erweiterung (erweiterung/engineSpiegel.bundle.js)
 * gegen die Offline-Zusatzdateien — ohne Browser.
 *
 * Das Bündel läuft hier unverändert: ein nachgebautes `self` nimmt seine Nachrichten an,
 * fetch liest die Karten aus resources/maps. Gefüttert wird es wie vom Kern (kern.ts):
 * Startbrief, dann Zug für Zug. An den Ticks der Metazeilen des gewählten Menschen kommt
 * { art: "beobachte" }; aus der Antwort wird _anfrage.zusatz_b64 gelesen und bitweise
 * (float16, wie im Training) mit der Offline-Zeile verglichen, dazu die ctx-Listen.
 * Damit sind die Haken im Worker (vorTick, nachTick mit den Unit-Updates des Rückrufs,
 * Zeilenplan für meineID) geprüft — nicht nur das Modul.
 *
 *   cd ~/openfront-client-v33 && npx tsx d1probe/workerprobe.ts \
 *       --bundle erweiterung/engineSpiegel.bundle.js --records ~/of-records/records \
 *       --pool ~/of-mat2-out --zusatz ~/zusatz/alle --gid s17/DrheXs8N [--max 150]
 * Nur Aufzeichnungen des Kerns 8b45be57 (= v0.33.14). Eine Partie je Prozess.
 */
import fs from "fs";
import path from "path";
import vm from "vm";
import { zstdDecompressSync } from "zlib";
import { GameRecord, GameRecordSchema } from "../src/core/Schemas";
import { decompressGameRecord, toWireGameStartInfo } from "../src/core/Util";
import { F_ANGR, F_EINH, F_GLOB, F_OPP, MAX_ANGR, MAX_EINH, MAX_OPP } from "./zusatzFelder";

const log = (...x: any[]) => process.stderr.write(x.map(String).join(" ") + "\n");
const JE = [MAX_OPP * F_OPP, F_GLOB, MAX_EINH * F_EINH, MAX_ANGR * F_ANGR];
const NAMEN = ["opp", "global", "einheit", "angriff"];

function arg(n: string, s?: string): string | undefined {
  const a = process.argv.slice(2);
  const i = a.indexOf("--" + n);
  return i >= 0 && i + 1 < a.length ? a[i + 1] : s;
}

async function main() {
  const [shard, gid] = arg("gid")!.split("/");
  const pool = path.join(arg("pool")!, shard);
  const maxN = Number(arg("max", "150"));
  const meta = zstdDecompressSync(fs.readFileSync(path.join(pool, `${gid}.meta.zst`)))
    .toString("utf8").split("\n").filter((l) => l).map((l) => JSON.parse(l));
  const roh = zstdDecompressSync(fs.readFileSync(path.join(arg("zusatz")!, `${gid}.zusatz.zst`)));
  const nl = roh.indexOf(10);
  const kopf = JSON.parse(roh.subarray(0, nl).toString("utf8"));
  const u16 = new Uint16Array((roh.length - nl - 1) / 2);
  Buffer.from(u16.buffer).set(roh.subarray(nl + 1));
  const n = kopf.samples;
  const bloecke: Uint16Array[] = [];
  let o = 0;
  for (const je of JE) { bloecke.push(u16.subarray(o, o + n * je)); o += n * je; }

  // der Mensch mit den meisten Zeilen nach der Startphase
  const zahl = new Map<string, number>();
  for (const m of meta) if (m.kind !== "spawn") zahl.set(m.clientID, (zahl.get(m.clientID) ?? 0) + 1);
  const meineID = [...zahl.entries()].sort((a, b) => b[1] - a[1])[0][0];
  const zeileAn = new Map<number, number>();              // Tick → erste Zeile dieses Menschen
  // Spawn-Zeilen nicht: dort gibt es den Spieler im Spiegel noch nicht (Beobachtung null)
  meta.forEach((m, i) => {
    if (m.clientID === meineID && m.kind !== "spawn" && !zeileAn.has(m.tick)) zeileAn.set(m.tick, i);
  });

  const raw = JSON.parse(fs.readFileSync(path.join(arg("records")!, gid.slice(0, 2), `${gid}.json`), "utf8"));
  const parsed = GameRecordSchema.safeParse(raw);
  const record: GameRecord = decompressGameRecord(parsed.success ? parsed.data : (raw as GameRecord));
  const info = record.info;
  const gameStart = toWireGameStartInfo({ gameID: info.gameID, lobbyCreatedAt: info.lobbyCreatedAt,
    config: info.config, players: info.players, tribes: info.tribes } as any);

  // ── nachgebauter Worker-Rahmen ──
  const hoerer: ((ev: any) => any)[] = [];
  let raus: any[] = [];
  (globalThis as any).self = {
    postMessage: (m: any) => raus.push(m),
    addEventListener: (t: string, f: any) => { if (t === "message") hoerer.push(f); },
  };
  (globalThis as any).fetch = async (url: string) => {
    const f = path.resolve("resources/maps", String(url).replace(/^.*?maps\//, ""));
    if (!fs.existsSync(f)) return { ok: false, status: 404, statusText: `fehlt ${f}` };
    const b = fs.readFileSync(f);
    return { ok: true, status: 200, statusText: "OK",
      arrayBuffer: async () => b.buffer.slice(b.byteOffset, b.byteOffset + b.length),
      json: async () => JSON.parse(b.toString("utf8")), text: async () => b.toString("utf8") };
  };
  console.debug = () => {}; console.log = () => {}; console.info = () => {}; console.warn = () => {};
  vm.runInThisContext(fs.readFileSync(arg("bundle")!, "utf8"), { filename: "engineSpiegel.bundle.js" });
  const sende = async (m: any) => { for (const f of hoerer) await f({ data: m }); };

  await sende({ art: "start", gameStartInfo: gameStart, meineID, cdnBase: "", assetManifest: {}, takt: 32 });
  const fehler = raus.filter((m) => m.art === "fehler").map((m) => m.text);
  if (!raus.some((m) => m.art === "bereit")) throw new Error(`Worker nicht bereit: ${JSON.stringify(fehler)}`);

  const F16A = (globalThis as any).Float16Array;
  let geprueft = 0, abw = 0, ctxAbw = 0, ohne = 0, nichtNull = 0;
  const jeBlock: Record<string, number> = {};
  const beispiele: any[] = [];
  const letzter = Math.max(...zeileAn.keys());
  for (const turn of record.turns) {
    const T = turn.turnNumber;
    const i = zeileAn.get(T);
    if (i !== undefined && geprueft < maxN) {
      raus = [];
      await sende({ art: "beobachte" });
      const b = raus.find((m) => m.art === "beobachtung")?.b;
      fehler.push(...raus.filter((m) => m.art === "fehler").map((m) => m.text));
      const an = b?._anfrage;
      if (!an?.zusatz_b64) { ohne++; }
      else {
        geprueft++;
        const m = meta[i];
        const l = (x: any) => JSON.stringify(x ?? []);
        if (l(an.ctx.oppSids) !== l((m.opps ?? []).map((q: any) => q.id)) || l(an.ctx.ownUnitIds) !== l(m.ownUnitIds)
            || l(an.ctx.ownAttackIds) !== l(m.ownAttackIds)) ctxAbw++;
        if (an.tick !== T || an.zusatz_tick !== T) throw new Error(`Tick ${an.tick}/${an.zusatz_tick} statt ${T}`);
        const zb = Buffer.from(an.zusatz_b64, "base64");
        const f32 = new Float32Array(zb.length / 4);
        Buffer.from(f32.buffer).set(zb);
        const h = new F16A(f32.length); h.set(f32);
        const ist = new Uint16Array(h.buffer);
        let off = 0;
        for (let k = 0; k < 4; k++) {
          const soll = bloecke[k].subarray(i * JE[k], (i + 1) * JE[k]);
          for (let j = 0; j < JE[k]; j++) {
            if (soll[j] !== 0) nichtNull++;
            if (ist[off + j] !== soll[j]) {
              abw++; jeBlock[NAMEN[k]] = (jeBlock[NAMEN[k]] ?? 0) + 1;
              if (beispiele.length < 6) beispiele.push({ tick: T, block: NAMEN[k], j });
            }
          }
          off += JE[k];
        }
      }
    }
    raus = [];
    await sende({ art: "turn", turn });
    fehler.push(...raus.filter((m) => m.art === "fehler").map((m) => m.text));
    if (T >= letzter) break;
  }
  const erg = { gid, meineID, zeilen_des_menschen: zeileAn.size, geprueft, ohne_zusatz: ohne, ctx_abw: ctxAbw,
                abw, je_block: jeBlock, nicht_null: nichtNull, beispiele, worker_fehler: fehler.slice(0, 5) };
  process.stdout.write(JSON.stringify(erg) + "\n");
  log(`[workerprobe] ${gid}: ${geprueft} Beobachtungen, Abweichungen ${abw}, ctx ${ctxAbw}, ohne Zusatz ${ohne}, `
    + `Worker-Fehler ${fehler.length}`);
}

main().catch((e) => { log(e?.stack ?? e); process.exit(1); });

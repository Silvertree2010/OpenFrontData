/**
 * dispatch.ts — Eingang des Containers (DESIGN.md §7).
 *
 * Findet Records unter /in, waehlt pro Partie den passenden Engine-Baum anhand
 * von `gitCommit`, startet fuer jede Partie einen eigenen Kindprozess
 * (`node /app/w/<sha8>/dist/<entry>.mjs <record> /out`), haelt einen
 * Worker-Pool und erzwingt ein Zeitlimit pro Partie.
 *
 * Aufruf (im Image): `node dist/dispatch.mjs` (ENTRYPOINT, keine Argumente,
 * alles ueber Umgebungsvariablen, siehe DESIGN §7).
 *
 * Fuer lokale Tests ausserhalb des Containers sind IN_DIR/OUT_DIR/MAT_ROOT
 * ueberschreibbar (Default /in, /out, /app/w).
 */
import { spawn, ChildProcess } from "child_process";
import * as crypto from "crypto";
import * as fs from "fs";
import * as os from "os";
import * as path from "path";
import { isDone as isDoneV2 } from "./io";

// ─────────────────────────── Konfiguration ──────────────────────────────────

const IN_DIR = process.env.IN_DIR ?? "/in";
const OUT_DIR = process.env.OUT_DIR ?? "/out";
const MAT_ROOT = process.env.MAT_ROOT ?? "/app/w";
// Waehlt das Buendel je Baum. Solange src/materialize.ts nicht fertig ist,
// testen wir gegen den alten Materialisierer: MAT_ENTRY=materialize_old.mjs.
const MAT_ENTRY = process.env.MAT_ENTRY ?? "materialize.mjs";
const RETRY_ERR = process.env.RETRY_ERR === "1";
const LIMIT = process.env.LIMIT ? Number(process.env.LIMIT) : undefined;
const LIST_FILE = process.env.LIST;
const GAME_TIMEOUT_S = Number(process.env.GAME_TIMEOUT_S ?? 7200);
const MEM_PER_WORKER_MB = Number(process.env.MEM_PER_WORKER_MB ?? 2000);
// Rueckfallstufe fuer den ALTEN Materialisierer (kein .ok/.err-Format, siehe
// istFertig() in env/materialize.ts): eine .meta.zst unter dieser Groesse ist
// nicht fertig. Nur relevant, wenn MAT_ENTRY=materialize_old.mjs.
const OLD_MIN_META = Number(process.env.MIN_META ?? 64);
// Umgebungsvariablen, die 1:1 an das Kernbuendel durchgereicht werden (Kern
// liest sie selbst; dispatch.ts kennt ihre Bedeutung nicht, siehe DESIGN §7).
const PASSTHROUGH_ENV = [
  "NOOP_EVERY", "MERGE_TICKS", "TIER1_PCT", "CELLS", "LEGAL_BIT1",
  "RETRY_ERR", "MAT_VERSION",
];

// SHARD=i/n: h % n == i, h = Hex-Zeichen 8-15 von sha1(gid) als Zahl
// (DESIGN §7, Stand nach Pruefung). NICHT dieselben Zeichen wie die
// TIER1_PCT/Val-Auswahl in DESIGN §5.3 (die nimmt die ersten 8 Hex-Zeichen) —
// sonst laegen alle Val-Partien auf demselben Shard.
function shardIndex(gid: string, n: number): number {
  const hex = crypto.createHash("sha1").update(gid, "ascii").digest("hex").slice(8, 16);
  return parseInt(hex, 16) % n;
}

function parseShard(): { i: number; n: number } | null {
  const raw = process.env.SHARD;
  if (!raw) return null;
  const m = /^(\d+)\/(\d+)$/.exec(raw.trim());
  if (!m) throw new Error(`SHARD ungueltig: "${raw}", erwartet "i/n"`);
  const i = Number(m[1]), n = Number(m[2]);
  if (n <= 0 || i < 0 || i >= n) throw new Error(`SHARD ausserhalb des Bereichs: "${raw}"`);
  return { i, n };
}

// ─────────────────────────── Records finden ─────────────────────────────────

interface Rec { gid: string; file: string; }

function findRecords(dir: string): Rec[] {
  const byGid = new Map<string, string>();
  let dupes = 0;
  const walk = (d: string) => {
    let entries: fs.Dirent[];
    try { entries = fs.readdirSync(d, { withFileTypes: true }); }
    catch (e: any) { console.error(`[dispatch] kann ${d} nicht lesen: ${e?.message ?? e}`); return; }
    for (const e of entries) {
      const p = path.join(d, e.name);
      if (e.isDirectory()) { walk(p); continue; }
      if (!e.isFile() || !e.name.endsWith(".json")) continue;
      const gid = path.basename(e.name, ".json");
      if (gid.length !== 8) { console.error(`[dispatch] uebersprungen (keine 8-Zeichen-ID): ${p}`); continue; }
      if (byGid.has(gid)) { dupes++; continue; } // erster Fund gewinnt, Dubletten nur einmal
      byGid.set(gid, p);
    }
  };
  walk(dir);
  if (dupes > 0) console.error(`[dispatch] ${dupes} gid-Dubletten uebersprungen`);
  return [...byGid.entries()].map(([gid, file]) => ({ gid, file })).sort((a, b) => a.gid.localeCompare(b.gid));
}

function applyListFilter(records: Rec[], listFile: string): Rec[] {
  const wanted = new Set(
    fs.readFileSync(listFile, "utf8").split("\n").map((s) => s.trim()).filter(Boolean),
  );
  const byGid = new Map(records.map((r) => [r.gid, r]));
  const out: Rec[] = [];
  for (const gid of wanted) {
    const r = byGid.get(gid);
    if (r) out.push(r);
    else console.error(`[dispatch] LIST-Eintrag ohne Record: ${gid}`);
  }
  return out;
}

// ─────────────────────────── Commit → Baum ──────────────────────────────────

// Liest die ersten ~4 KB des Records und sucht "gitCommit" per Regex, statt
// die ganze (teils riesige) Datei zu parsen (DESIGN §7).
function readGitCommit(file: string): string | null {
  const fd = fs.openSync(file, "r");
  try {
    const buf = Buffer.alloc(4096);
    const n = fs.readSync(fd, buf, 0, buf.length, 0);
    const head = buf.toString("utf8", 0, n);
    const m = /"gitCommit"\s*:\s*"([0-9a-fA-F]+)"/.exec(head);
    return m ? m[1] : null;
  } finally {
    fs.closeSync(fd);
  }
}

function discoverTrees(root: string): Map<string, string> {
  // sha8 -> Verzeichnis /app/w/<sha8>. Der Container legt keine anderen
  // Verzeichnisse dort ab; wir lesen sie dynamisch statt sie hier fest zu
  // verdrahten, damit das Image die einzige Quelle der Wahrheit bleibt.
  const out = new Map<string, string>();
  let entries: fs.Dirent[];
  try { entries = fs.readdirSync(root, { withFileTypes: true }); }
  catch (e: any) { console.error(`[dispatch] MAT_ROOT ${root} nicht lesbar: ${e?.message ?? e}`); return out; }
  for (const e of entries) {
    if (e.isDirectory() && /^[0-9a-f]{8}$/.test(e.name)) out.set(e.name, path.join(root, e.name));
  }
  return out;
}

// ─────────────────────────── Fertig-Pruefung ────────────────────────────────

// Neuer Kern: genau die Regel aus src/io.ts (DESIGN §5 "Fertig heisst"), eine
// einzige Quelle. Nur fuer MAT_ENTRY=materialize_old.mjs (Kanarien-Referenz)
// gilt die alte Regel unten weiter, der alte Materialisierer schreibt kein .ok.
function isDone(outdir: string, gid: string, retryErr: boolean): boolean {
  const usingOldBundle = MAT_ENTRY !== "materialize.mjs";
  if (!usingOldBundle) return isDoneV2(outdir, gid, retryErr);

  const nonePath = path.join(outdir, `${gid}.none`);
  if (fs.existsSync(nonePath)) {
    if (usingOldBundle) return true; // alter Materialisierer: leere Datei ohne JSON, siehe env/materialize.ts
    try {
      const j = JSON.parse(fs.readFileSync(nonePath, "utf8"));
      if (j && j.format === 2) return true;
    } catch { /* leer/kaputt: zaehlt beim neuen Format NICHT als fertig */ }
  }

  const okPath = path.join(outdir, `${gid}.ok`);
  if (fs.existsSync(okPath)) {
    try {
      const ok = JSON.parse(fs.readFileSync(okPath, "utf8"));
      const files = ok?.files;
      if (ok?.format === 2 && files && typeof files === "object") {
        for (const [name, size] of Object.entries<number>(files)) {
          let st: fs.Stats;
          try { st = fs.statSync(path.join(outdir, name)); }
          catch { return false; }
          if (st.size !== size) return false;
        }
        return true;
      }
    } catch { /* kaputte .ok: nicht fertig, neu rechnen */ }
    return false;
  }

  if (fs.existsSync(path.join(outdir, `${gid}.err`))) return !retryErr;

  // Ruckfallstufe fuer MAT_ENTRY=materialize_old.mjs (kein .ok/.err-Format,
  // .none ist dort eine leere Datei, s.o.): eine hinreichend grosse .meta.zst
  // gilt als fertig (env/materialize.ts:istFertig).
  if (usingOldBundle) {
    const meta = path.join(outdir, `${gid}.meta.zst`);
    try {
      const st = fs.statSync(meta);
      if (st.size >= OLD_MIN_META) return true;
    } catch { /* existiert nicht */ }
  }

  return false;
}

// Vor dem (Neu-)Start: .tmp-Reste dieser Partie loeschen (DESIGN §5), damit
// eine leere/winzige Restdatei nie als fertig durchgeht.
function cleanupTmp(outdir: string, gid: string) {
  let entries: string[];
  try { entries = fs.readdirSync(outdir); } catch { return; }
  for (const name of entries) {
    if (name.startsWith(gid) && name.endsWith(".tmp")) {
      try { fs.unlinkSync(path.join(outdir, name)); } catch { /* egal */ }
    }
  }
}

// ─────────────────────────── cgroup v2: Defaults ────────────────────────────

function cgroupCpus(): number {
  try {
    const raw = fs.readFileSync("/sys/fs/cgroup/cpu.max", "utf8").trim();
    const [quota, period] = raw.split(/\s+/);
    if (quota === "max") return os.cpus().length;
    const n = Math.floor(Number(quota) / Number(period));
    return Math.max(1, n);
  } catch { return os.cpus().length; }
}

function cgroupMemMB(): number {
  try {
    const raw = fs.readFileSync("/sys/fs/cgroup/memory.max", "utf8").trim();
    if (raw === "max") return Math.floor(os.totalmem() / 1e6);
    return Math.floor(Number(raw) / 1e6);
  } catch { return Math.floor(os.totalmem() / 1e6); }
}

function defaultWorkers(): number {
  return Math.max(1, Math.min(cgroupCpus(), Math.floor(cgroupMemMB() / MEM_PER_WORKER_MB)));
}

// ─────────────────────────── Eine Partie ausfuehren ─────────────────────────

type Outcome = "ok" | "none" | "err" | "timeout" | "skipped";

function writeErr(outdir: string, gid: string, reason: string) {
  try { fs.writeFileSync(path.join(outdir, `${gid}.err`), reason); } catch { /* egal */ }
}

function runGame(rec: Rec, trees: Map<string, string>): Promise<Outcome> {
  return new Promise((resolve) => {
    const commit = readGitCommit(rec.file);
    if (!commit) {
      writeErr(OUT_DIR, rec.gid, "kein gitCommit im Record gefunden");
      return resolve("err");
    }
    const sha8 = commit.slice(0, 8);
    const treeDir = trees.get(sha8);
    if (!treeDir) {
      writeErr(OUT_DIR, rec.gid, `unbekannter Commit: ${commit}`);
      return resolve("err");
    }
    const entry = path.join(treeDir, "dist", MAT_ENTRY);
    if (!fs.existsSync(entry)) {
      writeErr(OUT_DIR, rec.gid, `Buendel fehlt: ${entry}`);
      return resolve("err");
    }

    cleanupTmp(OUT_DIR, rec.gid);

    const env: NodeJS.ProcessEnv = { ...process.env };
    for (const k of PASSTHROUGH_ENV) if (process.env[k] !== undefined) env[k] = process.env[k];
    // Der Kern leitet Engine-Pfad und Commit aus MAT_ROOT ab (= ein Baum w/<sha8>),
    // nicht aus dem Wurzelordner aller Baeume, den der Dispatcher selbst nutzt.
    env.MAT_ROOT = treeDir;
    env.ENGINE_COMMIT = sha8;

    const child: ChildProcess = spawn(process.execPath, [entry, rec.file, OUT_DIR], {
      stdio: ["ignore", "pipe", "pipe"],
      env,
    });
    activeChildren.add(child);

    let timedOut = false;
    let killedForShutdown = false;
    const stderrTail: string[] = [];
    child.stderr?.on("data", (d: Buffer) => {
      stderrTail.push(d.toString("utf8"));
      if (stderrTail.length > 50) stderrTail.shift();
    });
    child.stdout?.on("data", () => { /* Kind loggt selbst; wir zaehlen nur den Rueckgabewert */ });

    const timer = setTimeout(() => {
      timedOut = true;
      child.kill("SIGTERM");
      setTimeout(() => { if (!child.killed) child.kill("SIGKILL"); }, 5000);
    }, GAME_TIMEOUT_S * 1000);

    child.on("exit", (code, signal) => {
      clearTimeout(timer);
      activeChildren.delete(child);

      if (timedOut) {
        writeErr(OUT_DIR, rec.gid, "timeout");
        return resolve("timeout");
      }
      if (shuttingDown) {
        killedForShutdown = true;
        // Sauberes Beenden: kein .err fuer eine Partie, die wir selbst
        // unterbrochen haben. .tmp-Reste raeumt der naechste Lauf auf.
        return resolve("skipped");
      }
      if (code === 0) {
        if (isDone(OUT_DIR, rec.gid, RETRY_ERR)) return resolve("ok");
        if (fs.existsSync(path.join(OUT_DIR, `${rec.gid}.none`))) return resolve("none");
        // Exit 0, aber keine erkennbare Fertig-Marke: als Fehler behandeln,
        // sonst wuerde ein naechster Lauf still ueberspringen.
        writeErr(OUT_DIR, rec.gid, "exit 0 ohne Fertig-Marke");
        return resolve("err");
      }
      // Anormaler Tod (Signal, OOM, Exit != 0). Hat der Kern selbst schon eine
      // Marke geschrieben (eigenes .err/.none/.ok), die stehen lassen — sonst
      // schreibt der Dispatcher seinen eigenen Grund (DESIGN.md, Praezisierung).
      const hasOwnMarker = ["err", "none", "ok"].some((ext) => fs.existsSync(path.join(OUT_DIR, `${rec.gid}.${ext}`)));
      if (!hasOwnMarker) {
        const grund = signal
          ? `Kindprozess durch Signal ${signal} beendet (evtl. OOM)`
          : `exit ${code ?? "null"}: ${stderrTail.join("").slice(-2000)}`;
        writeErr(OUT_DIR, rec.gid, grund);
      }
      resolve("err");
    });
  });
}

// ─────────────────────────── Worker-Pool + Log ──────────────────────────────

const activeChildren = new Set<ChildProcess>();
let shuttingDown = false;

process.on("SIGTERM", () => {
  shuttingDown = true;
  console.error(`[dispatch] SIGTERM — beende ${activeChildren.size} laufende Partie(n), keine neuen Starts`);
  for (const c of activeChildren) c.kill("SIGTERM");
});
process.on("SIGINT", () => process.emit("SIGTERM" as any));

async function main() {
  const trees = discoverTrees(MAT_ROOT);
  if (trees.size === 0) console.error(`[dispatch] WARNUNG: keine Engine-Baeume unter ${MAT_ROOT} gefunden`);
  else console.error(`[dispatch] Engine-Baeume: ${[...trees.keys()].join(", ")}`);

  fs.mkdirSync(OUT_DIR, { recursive: true });

  let records = findRecords(IN_DIR);
  if (LIST_FILE) records = applyListFilter(records, LIST_FILE);
  const shard = parseShard();
  if (shard) records = records.filter((r) => shardIndex(r.gid, shard.n) === shard.i);
  if (LIMIT !== undefined) records = records.slice(0, LIMIT);

  const workers = Number(process.env.WORKERS) || defaultWorkers();
  console.error(`[dispatch] ${records.length} Partie(n), WORKERS=${workers}` +
    (shard ? `, SHARD=${shard.i}/${shard.n}` : "") + `, MAT_ENTRY=${MAT_ENTRY}`);

  const counts: Record<Outcome, number> = { ok: 0, none: 0, err: 0, timeout: 0, skipped: 0 };
  let idx = 0;
  const total = records.length;
  const t0 = Date.now();

  const summaryTimer = setInterval(() => {
    const done = counts.ok + counts.none + counts.err + counts.timeout + counts.skipped;
    const elapsedS = (Date.now() - t0) / 1000;
    const rate = done / Math.max(elapsedS, 1); // Partien/s
    const remaining = total - done;
    const etaS = rate > 0 ? remaining / rate : NaN;
    console.error(
      `[dispatch] ${done}/${total} fertig — ok=${counts.ok} none=${counts.none} err=${counts.err} ` +
      `timeout=${counts.timeout} rest=${remaining} eta=${Number.isFinite(etaS) ? `${Math.round(etaS)}s` : "?"}`,
    );
  }, 60_000);
  summaryTimer.unref();

  async function worker() {
    while (!shuttingDown) {
      const i = idx++;
      if (i >= records.length) return;
      const rec = records[i];
      if (isDone(OUT_DIR, rec.gid, RETRY_ERR)) { counts.skipped++; continue; }
      const outcome = await runGame(rec, trees);
      counts[outcome]++;
      console.log(`[${rec.gid}] ${outcome}`);
    }
  }

  await Promise.all(Array.from({ length: Math.max(1, workers) }, () => worker()));
  clearInterval(summaryTimer);

  const done = counts.ok + counts.none + counts.err + counts.timeout;
  console.error(
    `[dispatch] fertig: ${done}/${total} — ok=${counts.ok} none=${counts.none} err=${counts.err} ` +
    `timeout=${counts.timeout} uebersprungen=${counts.skipped} (${((Date.now() - t0) / 1000).toFixed(0)}s)`,
  );
  process.exit(shuttingDown ? 130 : (counts.err > 0 || counts.timeout > 0 ? 0 : 0));
}

main().catch((e) => { console.error(`[dispatch] fatal: ${e?.stack ?? e}`); process.exit(1); });

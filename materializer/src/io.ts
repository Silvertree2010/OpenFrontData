/**
 * Dateien einer Partie: atomar schreiben, .ok-Marke, Fertig-Prüfung (DESIGN §5).
 *
 * Jede Datei entsteht als <name>.tmp und wird erst am Ende umbenannt, die .ok
 * zuletzt. Abschluss (commitGame): fsync aller tmp-Dateien, dann Umbenennen in
 * der Folge maps, cells, Tier 1, hdr.json, meta.zst, dann .ok (tmp, fsync, rename).
 * rename(2) ist innerhalb eines Verzeichnisses atomar: ein Abbruch hinterlässt
 * nie eine halbe Enddatei. Das Verzeichnis selbst wird nicht gefsynct; nach
 * einem Stromausfall fängt isDone fehlende oder kurze Dateien über die Grössen ab.
 *
 * Namenskonvention, auf die sich cleanStale verlässt: alles einer Partie heisst
 * "<gid>.<name>" bzw. "<gid>.<name>.tmp", <name> aus DATA_NAMES/MARK_NAMES.
 * Tier 1 (tier1.ts) muss seine tmp-Dateien genauso benennen, sonst bleiben
 * Reste eines abgebrochenen Laufs liegen.
 */
import fs from "fs";
import path from "path";

/** Datendateien einer Partie (Endung nach "<gid>."). Tier-1-Dateien gehören dazu. */
export const DATA_NAMES = ["hdr.json", "maps", "meta.zst", "cells", "own.zst", "units.zst", "chk"] as const;
/** Marken. Reihenfolge = Löschreihenfolge in cleanStale: Marken zuerst. */
export const MARK_NAMES = ["ok", "none", "err"] as const;

export function gamePath(outdir: string, gid: string, name: string): string {
  return path.join(outdir, `${gid}.${name}`);
}

function readJson(p: string): any {
  try {
    return JSON.parse(fs.readFileSync(p, "utf8"));
  } catch {
    return null;
  }
}

/**
 * Fertig heisst (DESIGN §5):
 *   - .none existiert und ist JSON mit format 2 (eine leere v1-Marke zählt nicht), oder
 *   - .ok existiert, ist JSON mit format 2, und jede gelistete Datei hat genau die
 *     gelistete Grösse (okValid).
 * .err gilt als erledigt, ausser mit retryErr. Eine .meta.zst ohne gültige .ok
 * ist nie fertig, egal wie gross.
 */
export function isDone(outdir: string, gid: string, retryErr: boolean): boolean {
  if (readJson(gamePath(outdir, gid, "none"))?.format === 2) return true;
  if (okValid(outdir, gid)) return true;
  if (!retryErr && fs.existsSync(gamePath(outdir, gid, "err"))) return true;
  return false;
}

/**
 * .ok ist JSON mit format 2, alle gelisteten Grössen stimmen, und die
 * Pflichtdateien sind gelistet: immer hdr.json, bei samples > 0 auch maps und
 * meta.zst. Ohne Pflichtliste bewiese eine leere files-Liste nichts.
 */
export function okValid(outdir: string, gid: string): boolean {
  const ok = readJson(gamePath(outdir, gid, "ok"));
  if (ok?.format !== 2) return false;
  const files = ok.files;
  if (!files || typeof files !== "object" || typeof ok.samples !== "number") return false;
  const required = ok.samples > 0 ? ["hdr.json", "maps", "meta.zst"] : ["hdr.json"];
  for (const req of required) if (!(`${gid}.${req}` in files)) return false;
  for (const [name, bytes] of Object.entries(files)) {
    if (typeof bytes !== "number" || name.includes("/")) return false;
    try {
      if (fs.statSync(path.join(outdir, name)).size !== bytes) return false;
    } catch {
      return false;
    }
  }
  return true;
}

/**
 * Beim Start einer Partie: alle ihre Dateien löschen, .tmp-Reste ebenso wie
 * Endnamen und Marken (DESIGN §5). So liegt nie eine neue Meta neben einer alten
 * .maps. Marken zuerst, damit ein Abbruch mitten im Aufräumen keine gültige .ok
 * über halb gelöschten Daten stehen lässt. Liefert die gelöschten Namen.
 */
export function cleanStale(outdir: string, gid: string): string[] {
  const removed: string[] = [];
  for (const n of [...MARK_NAMES, ...DATA_NAMES]) {
    for (const f of [gamePath(outdir, gid, n), gamePath(outdir, gid, n) + ".tmp"]) {
      try {
        fs.unlinkSync(f);
        removed.push(path.basename(f));
      } catch (e: any) {
        if (e?.code !== "ENOENT") throw e;
      }
    }
  }
  return removed;
}

function writeAll(fd: number, buf: Uint8Array) {
  let off = 0;
  while (off < buf.length) off += fs.writeSync(fd, buf, off, buf.length - off);
}

/** Datei schreiben und fsyncen (ohne Umbenennen). */
export function writeSynced(p: string, data: string | Uint8Array): void {
  const fd = fs.openSync(p, "w");
  try {
    writeAll(fd, typeof data === "string" ? Buffer.from(data) : data);
    fs.fsyncSync(fd);
  } finally {
    fs.closeSync(fd);
  }
}

function fsyncPath(p: string): void {
  const fd = fs.openSync(p, "r");
  try {
    fs.fsyncSync(fd);
  } finally {
    fs.closeSync(fd);
  }
}

/** Ganze Datei atomar: <final>.tmp schreiben, fsync, umbenennen. */
export function writeAtomic(finalPath: string, data: string | Uint8Array): void {
  const tmp = finalPath + ".tmp";
  writeSynced(tmp, data);
  fs.renameSync(tmp, finalPath);
}

/**
 * Blockdatei [u32 LE Länge][Block]..., gestreamt in <final>.tmp.
 * Für .maps und .cells. truncate() schneidet nach dem Schnitt (§2.7) den Schwanz ab.
 */
export class BlockFile {
  readonly tmp: string;
  private fd: number;
  size = 0;
  blocks = 0;
  private readonly len = Buffer.alloc(4);

  constructor(readonly final: string) {
    this.tmp = final + ".tmp";
    this.fd = fs.openSync(this.tmp, "w");
  }

  write(block: Uint8Array): void {
    this.len.writeUInt32LE(block.length, 0);
    writeAll(this.fd, this.len);
    writeAll(this.fd, block);
    this.size += 4 + block.length;
    this.blocks++;
  }

  truncate(size: number, blocks: number): void {
    fs.ftruncateSync(this.fd, size);
    this.size = size;
    this.blocks = blocks;
  }

  close(): void {
    if (this.fd >= 0) {
      fs.closeSync(this.fd);
      this.fd = -1;
    }
  }

  discard(): void {
    this.close();
    try {
      fs.unlinkSync(this.tmp);
    } catch {}
  }
}

/**
 * Abschluss einer Partie. `renames` in der Folge maps, cells, Tier 1, hdr.json,
 * meta.zst (der Aufrufer ordnet). Erst fsync aller tmp-Dateien, dann umbenennen,
 * Grössen messen, zuletzt die .ok (tmp, fsync, rename). `ok` bekommt `files`
 * hinter `format` eingesetzt.
 */
export function commitGame(
  outdir: string,
  gid: string,
  renames: Array<[tmp: string, final: string]>,
  ok: Record<string, unknown>,
): Record<string, number> {
  for (const [tmp] of renames) fsyncPath(tmp);
  const files: Record<string, number> = {};
  for (const [tmp, fin] of renames) {
    fs.renameSync(tmp, fin);
    files[path.basename(fin)] = fs.statSync(fin).size;
  }
  const { format, ...rest } = ok as any;
  writeAtomic(gamePath(outdir, gid, "ok"), JSON.stringify({ format, files, ...rest }));
  return files;
}

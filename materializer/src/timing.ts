/**
 * Stufen-Zeitmessung (DESIGN §5.4 time_ms). Summen aus performance.now().
 *
 * Stufen:
 *   setup  Record lesen, Karte laden, createGame, Hooks anlegen (nicht in §5.4,
 *          ergänzt, damit total − Summe nicht unerklärt bleibt)
 *   sim    runner.addTurn + executeNextTick
 *   scan   ObsEncoder.scanTick
 *   vec    encodeVec + Kontext (Gegnerliste, Einheiten, Angriffe)
 *   map    encodeMap + Quantisieren + zstd des Kartenblocks
 *   json   JSON.stringify der Metazeilen
 *   res    Auflösung res_* (Beobachtung nach dem Tick)
 *   cells  CellFacts.compute + zstd des Zellblocks
 *   tier1  Tier1Writer (Konstruktor, onTick, finish)
 *   io     Blöcke schreiben, meta komprimieren, Dateien abschliessen
 *   total  Wandzeit vom Anlegen der Messung bis report()
 */
export const STAGES = ["setup", "sim", "scan", "vec", "map", "json", "res", "cells", "tier1", "io"] as const;
export type Stage = (typeof STAGES)[number];

export class Timing {
  private readonly t0 = performance.now();
  readonly ms = Object.fromEntries(STAGES.map((s) => [s, 0])) as Record<Stage, number>;

  /** Zeit seit `since` auf `stage` buchen, liefert die neue Uhrzeit (zum Verketten). */
  add(stage: Stage, since: number): number {
    const now = performance.now();
    this.ms[stage] += now - since;
    return now;
  }

  report(): Record<Stage | "total", number> {
    const out: any = {};
    for (const s of STAGES) out[s] = Math.round(this.ms[s]);
    out.total = Math.round(performance.now() - this.t0);
    return out;
  }
}

/** RSS-Spitze des Prozesses in MB (getrusage ru_maxrss, unter Linux in KB). */
export function rssMaxMb(): number {
  return Math.round(process.resourceUsage().maxRSS / 1024);
}

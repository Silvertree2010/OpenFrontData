/**
 * Der Spiegel: ein Worker, der die Partie aus dem Zugstrom nachrechnet.
 *
 * Die Seite bekommt vom Server nur Absichten, nie den Weltzustand — jeder Client rechnet
 * ihn selbst. Genau das tut dieser Worker noch einmal, mit derselben Engine und
 * demselben Strom: Startbrief herein, Zug für Zug ausführen, daraus die Beobachtung.
 *
 * Er redet nur mit `kern.ts` (postMessage) und mit dem CDN, von dem er die Karte holt —
 * dieselbe Datei, die die Seite ohnehin lädt. Er hat keinen Socket und sendet nie etwas
 * ins Spiel.
 *
 * Nachrichten herein:
 *   { art: "start", gameStartInfo, meineID, cdnBase, assetManifest, takt }
 *   { art: "turn",  turn }
 *   { art: "beobachte" }        einmalig eine Beobachtung anfordern
 * Nachrichten hinaus:
 *   { art: "bereit" } | { art: "tick", tick, lebe, startphase }
 *   { art: "beobachtung", b } | { art: "fehler", text }
 */
import { buildAssetUrl } from "../src/core/AssetUrls";
import { createGameRunner, GameRunner } from "../src/core/GameRunner";
import { FetchGameMapLoader } from "../src/core/game/FetchGameMapLoader";
import { Player, PlayerType, UnitType } from "../src/core/game/Game";
import { GameUpdateType } from "../src/core/game/GameUpdates";
import { simpleHash } from "../src/core/Util";
import { Beobachter } from "./beobachtung";
import type { Beobachtung } from "./schnittstelle";
import { POOL_PLAN, ZusatzSpur } from "./zusatzFelder";

const ctx: Worker = self as any;

let runner: GameRunner | null = null;
let beobachter: Beobachter | null = null;
// Zusatzfelder für Netz D1 (zusatz/src/felder.ts, beim Bauen als zusatzFelder.ts kopiert).
// Läuft ab Tick 0 mit; ein Fehler schaltet nur die Zusatzfelder ab, nie die Beobachtung.
let spur: ZusatzSpur | null = null;
let letzteGu: any = null;

function zusatzAus(wo: string, e: any): void {
  spur = null;
  melde("fehler", { text: `Zusatzfelder aus (${wo}): ${String(e?.message ?? e).slice(0, 200)}` });
}
let meineID = "";
let takt = 32;
let gestartet = false;
let letzterTick = -1;
const warteschlange: any[] = [];

function melde(art: string, mehr: Record<string, unknown> = {}): void {
  ctx.postMessage({ art, ...mehr });
}

/** Der Spieler, den wir steuern — der mit unserer clientID. */
function ich(): Player | null {
  const game: any = (runner as any)?.game;
  if (!game) return null;
  const p = game
    .players()
    .find((q: any) => q.type() === PlayerType.Human && q.clientID() === meineID);
  return (p as Player) ?? null;
}

function spieleAb(): void {
  if (!runner) return;
  while (warteschlange.length > 0) {
    const turn = warteschlange.shift();
    try {
      if (spur) { try { spur.vorTick(turn); } catch (e) { zusatzAus("vorTick", e); } }
      runner.addTurn(turn);
      const lief = runner.executeNextTick();
      if (spur && lief) {
        try { spur.nachTick(letzteGu?.updates?.[GameUpdateType.Unit]); } catch (e) { zusatzAus("nachTick", e); }
      }
    } catch (e: any) {
      melde("fehler", { text: `Tick ${turn?.turnNumber}: ${String(e?.message ?? e)}` });
      return;
    }
  }
  const game: any = (runner as any).game;
  const t = game.ticks();
  if (t === letzterTick) return;
  letzterTick = t;
  const p = ich();
  melde("tick", { tick: t, lebe: p?.isAlive() ?? false, startphase: game.inSpawnPhase() });
}

function beobachte(): void {
  const game: any = (runner as any)?.game;
  const p = ich();
  if (!game || !p || !beobachter) {
    melde("beobachtung", { b: null });
    return;
  }
  try {
    const b: Beobachtung = beobachter.rechne(game, p, meineID, takt, spur);
    ctx.postMessage({ art: "beobachtung", b }, Beobachter.puffer(b) as any);
  } catch (e: any) {
    melde("fehler", { text: "beobachten: " + String(e?.message ?? e) });
    melde("beobachtung", { b: null });
  }
}

ctx.addEventListener("message", async (ev: MessageEvent) => {
  const m = ev.data;
  try {
    switch (m?.art) {
      case "start": {
        if (gestartet) return;
        gestartet = true;
        meineID = String(m.meineID ?? "");
        takt = Number(m.takt) > 0 ? Number(m.takt) : 32;
        const manifest = m.assetManifest ?? {};
        const basis = String(m.cdnBase ?? "");
        // Dieselbe Kartendatei, die die Seite auch lädt — über denselben Weg.
        const lader = new FetchGameMapLoader((pfad: string) =>
          buildAssetUrl(`maps/${pfad}`, manifest, basis),
        );
        runner = await createGameRunner(m.gameStartInfo, meineID, lader, (gu: any) => {
          if (gu && !("errMsg" in gu)) letzteGu = gu;
        });
        beobachter = new Beobachter(m.gameStartInfo?.config ?? null);
        try {
          spur = new ZusatzSpur((runner as any).game, UnitType, { ...POOL_PLAN,
            phase: (c: string) => Math.abs(simpleHash(c)) % POOL_PLAN.noopEvery });
          spur.verfolge(meineID);
        } catch (e) { zusatzAus("start", e); }
        melde("bereit", { karte: m.gameStartInfo?.config?.gameMap });
        spieleAb();
        break;
      }
      case "turn":
        warteschlange.push(m.turn);
        if (runner) spieleAb();
        break;
      case "beobachte":
        beobachte();
        break;
    }
  } catch (e: any) {
    melde("fehler", { text: String(e?.message ?? e).slice(0, 300) });
  }
});

melde("geladen");

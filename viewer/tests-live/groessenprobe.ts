/**
 * Nur zum Messen: was müsste eine Erweiterung mitbringen, um den Zugstrom des echten
 * Clients mitzuspielen und daraus die Beobachtung zu rechnen?
 *
 * Das hier zieht genau die Bausteine herein, die dafür nötig sind — Engine, Wire-Format,
 * Beobachtungskodierung, Zellfakten, Zielwahl — und wird mit esbuild gebündelt. Die
 * Grösse der Ausgabe ist die Antwort.
 */
export { createGameRunner, GameRunner } from "../src/core/GameRunner";
export {
  createGameWireContext,
  decodeServerMessage,
  encodeClientMessage,
} from "../src/core/ZbinWire";
export { ObsEncoder } from "../src/core/obsModel";
export { CellFacts } from "../src/core/cellFacts";
export { AiAnfrage } from "../src/core/aiAnfrage";
export { intentAusAntwort } from "../src/core/aiZiel";
export { loadTerrainMap } from "../src/core/game/TerrainMapLoader";

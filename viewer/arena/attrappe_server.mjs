/**
 * Attrappe des Inferenz-Servers: spricht dasselbe Protokoll wie trainer/inf_d0.py
 * (GET /, POST /act), rechnet aber kein Netz. Sie würfelt aus Tick und smallID
 * deterministisch eine Handlung und wählt die Kandidatenzellen aus den legal-Bits
 * der mitgeschickten Zellfakten.
 *
 * Zwei Zwecke:
 *  1. Der ganze Weg (aiAnfrage → HTTP → aiZiel → Engine) lässt sich prüfen, wo kein
 *     torch installiert ist (z. B. auf dem MacBook).
 *  2. Als dritte Messlatte "Zufalls-KI": handelt im richtigen Takt, aber ohne Verstand.
 *     Wer eine trainierte KI misst, will wissen, ob sie besser ist als das.
 *
 *   node arena/attrappe_server.mjs --port 8650 [--p-handeln 0.70] [--saat 1]
 *
 * Sie ist KEIN Ersatz für inf_d0.py und darf in keiner Stärkeaussage über das Netz
 * vorkommen — nur als Vergleichsband.
 */
import http from "http";

const arg = (n, s) => {
  const i = process.argv.indexOf("--" + n);
  return i >= 0 && i + 1 < process.argv.length ? process.argv[i + 1] : s;
};
const PORT = Number(arg("port", "8650"));
const P_HANDELN = Number(arg("p-handeln", "0.70"));
const SAAT = Number(arg("saat", "1"));
const TAKT = Number(arg("decide-every", "32"));

const NCELLS = 90 * 180;
const OFF_LEGAL = NCELLS * 3; // owner u16 [2·N] ‖ own_frac u8 [N] ‖ legal u8 [N]

/** Kleiner deterministischer Generator (mulberry32) über eine ganze Zahl. */
function strom(saat) {
  let a = saat >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const GRUPPEN_BIT = { bau: 1, hafen: 2, boot: 3, atom: 7, wasserstoff: 7, mirv: 7,
  kriegsschiff: 4, schiff_bewegen: 5, spawn: 6 };

function kandidaten(cells, gruppe, r, top = 5) {
  const bit = 1 << GRUPPEN_BIT[gruppe];
  const legal = [];
  for (let i = 0; i < NCELLS; i++) if (cells[OFF_LEGAL + i] & bit) legal.push(i);
  if (!legal.length) return [];
  // Deterministisch ziehen (Fisher-Yates auf den ersten `top`).
  const aus = [];
  const kopie = legal.slice();
  for (let k = 0; k < Math.min(top, kopie.length); k++) {
    const j = k + Math.floor(r() * (kopie.length - k));
    [kopie[k], kopie[j]] = [kopie[j], kopie[k]];
    aus.push(kopie[k]);
  }
  return aus;
}

function entscheide(o) {
  const r = strom((SAAT * 2654435761 + (o.tick | 0) * 40503 + (o.sid | 0) * 2246822519) >>> 0);
  const p = r();
  const res = { p_handeln: p, value: 0, atype: "NO_OP", decide_every: TAKT };
  if (p >= P_HANDELN) return { ...res, intent: { type: "no_op" } };
  const cells = o.cells_b64 ? Buffer.from(o.cells_b64, "base64") : null;
  const sids = o.ctx?.oppSids ?? [];
  const ids = o.ctx?.oppIds ?? [];
  const w = r();

  if (w < 0.35 && ids.length) {              // nicht-räumlich: Angriff auf einen Gegner
    const k = Math.floor(r() * ids.length);
    return { ...res, atype: "ATTACK", intent: { type: "attack", targetID: ids[k] ?? null,
      troops: Math.max(1, Math.floor((o.ctx?.troops ?? 0) * 0.3)) } };
  }
  if (!cells) return { ...res, intent: { type: "no_op" }, grund: "keine Zellfakten" };
  if (w < 0.75) {                            // Bauwerk
    const einheit = ["City", "Defense Post", "Port"][Math.floor(r() * 3)];
    const gruppe = einheit === "Port" ? "hafen" : "bau";
    const kand = kandidaten(cells, gruppe, r);
    if (!kand.length) return { ...res, intent: { type: "no_op" }, grund: "keine legale Zelle" };
    return { ...res, atype: "BUILD_UNIT", gruppe, einheit, zielSid: null, kandidaten: kand,
      intent: { type: "build_unit", unit: einheit, tile: 0 } };
  }
  const kand = kandidaten(cells, "boot", r);   // Boot auf ein Nachbargebiet
  if (!kand.length) return { ...res, intent: { type: "no_op" }, grund: "keine legale Zelle" };
  const k = sids.length ? Math.floor(r() * sids.length) : -1;
  return { ...res, atype: "BOAT", gruppe: "boot", zielSid: k >= 0 ? sids[k] : 0,
    kandidaten: kand,
    intent: { type: "boat", dst: 0, troops: Math.max(1, Math.floor((o.ctx?.troops ?? 0) * 0.2)) } };
}

http.createServer((req, res) => {
  const sende = (code, obj) => {
    const b = Buffer.from(JSON.stringify(obj));
    res.writeHead(code, { "Content-Type": "application/json", "Content-Length": b.length });
    res.end(b);
  };
  if (req.method === "GET") {
    return sende(200, { ok: true, netz: "attrappe", ckpt_step: null, decide_every: TAKT,
      schwelle: P_HANDELN });
  }
  let roh = "";
  req.on("data", (c) => (roh += c));
  req.on("end", () => {
    try { sende(200, entscheide(JSON.parse(roh))); }
    catch (e) { sende(500, { error: `${e?.name}: ${e?.message}` }); }
  });
}).listen(PORT, "127.0.0.1", () => {
  process.stderr.write(`attrappe_server auf 127.0.0.1:${PORT}, P(handeln)=${P_HANDELN}, `
    + `decide_every=${TAKT}, Saat=${SAAT}\n`);
});

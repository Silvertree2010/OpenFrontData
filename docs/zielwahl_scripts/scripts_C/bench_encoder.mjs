// Micro-benchmark: mirrors the loop structure of env/obs.ts scanTick/encodeMap and
// the per-sample quantize+zstd in env/materialize.ts on SYNTHETIC state.
// Not measured: encodeVec (opponent border walks), unit stamping, JSON, sim itself.
import zlib from "zlib";
const GW = 180, GH = 90, N = GW * GH, STRIDE = 600, NCH = 18, MAPLEN = NCH * N;
function mk(W, H) {
  const terrain = new Uint8Array(W * H), state = new Uint16Array(W * H);
  for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
    const r = y * W + x;
    const land = ((Math.sin(x / 97) + Math.cos(y / 61) + Math.sin((x + y) / 43)) > -0.6);
    terrain[r] = land ? (0x80 | ((x * 7 + y * 3) % 30)) : 0;
    if (land) state[r] = ((((x / 110) | 0) + ((y / 90) | 0) * 23) % 60) + 1;
  }
  return { terrain, state };
}
function bench(W, H, reps) {
  const { terrain, state } = mk(W, H);
  const landG = new Float32Array(N), magG = new Float32Array(N), fo = new Float32Array(N), de = new Float32Array(N);
  const counts = new Int32Array(N * STRIDE), samp = new Int32Array(N), owner = new Uint16Array(N);
  const sx = Math.max(1, (W / GW / 4) | 0), sy = Math.max(1, (H / GH / 4) | 0);
  const border = new Int32Array(100000).map((_, i) => (i * 7919) % (W * H)); // synthetic 100k border tiles
  const borderG = new Uint16Array(N);
  let t0 = performance.now(), tFill = 0, tLoop = 0, tArg = 0, tBord = 0;
  for (let rep = 0; rep < reps; rep++) {
    let a = performance.now();
    landG.fill(0); magG.fill(0); fo.fill(0); de.fill(0); counts.fill(0); samp.fill(0);
    let b = performance.now(); tFill += b - a;
    for (let y = 0; y < H; y += sy) {
      const gy = ((y * GH) / H) | 0, row = y * W;
      for (let x = 0; x < W; x += sx) {
        const gi = gy * GW + (((x * GW) / W) | 0); samp[gi]++;
        const ref = row + x, t = terrain[ref];
        if ((t & 0x80) === 0) continue;
        const mag = t & 0x1f; if (mag === 31) continue;
        landG[gi]++; magG[gi] += mag;
        const s = state[ref];
        if (s & 8192) fo[gi]++; if (s & 16384) de[gi]++;
        const oid = s & 0xfff; if (oid !== 0 && oid < STRIDE) counts[gi * STRIDE + oid]++;
      }
    }
    let c = performance.now(); tLoop += c - b;
    for (let gi = 0; gi < N; gi++) {
      let best = 0, bestN = 0; const base = gi * STRIDE;
      for (let p = 1; p < STRIDE; p++) { const v = counts[base + p]; if (v > bestN) { bestN = v; best = p; } }
      owner[gi] = best;
    }
    let d = performance.now(); tArg += d - c;
    borderG.fill(0);
    for (let i = 0; i < border.length; i++) { const r = border[i]; borderG[((((r / W) | 0) * GH / H) | 0) * GW + ((((r % W) * GW) / W) | 0)] = 1; }
    tBord += performance.now() - d;
  }
  const scanMs = (performance.now() - t0) / reps;
  // per-sample: encodeMap-like fill + quantize + zstd
  const out = new Float32Array(MAPLEN), u8 = new Uint8Array(MAPLEN);
  let zbytes = 0; const sreps = reps * 4; let tEnc = 0, tQ = 0, tZ = 0;
  for (let rep = 0; rep < sreps; rep++) {
    let a = performance.now();
    out.fill(0);
    for (let i = 0; i < N; i++) {
      out[i] = landG[i] / 16; out[N + i] = magG[i] / 500; out[6 * N + i] = 0; out[7 * N + i] = 0;
      const o = owner[i]; if (o) { if (o === 1 + (rep % 60)) out[2 * N + i] = 1; else out[4 * N + i] = 1; }
      if (borderG[i]) out[5 * N + i] = 1;
      for (let s = 0; s < 10; s++) { if ((i * 31 + s) % 997 === 0) out[(8 + s) * N + i] = 0.6; }
    }
    let b = performance.now(); tEnc += b - a;
    for (let k = 0; k < MAPLEN; k++) { let x = out[k]; if (x < -1) x = -1; else if (x > 1) x = 1; u8[k] = Math.round((x + 1) * 127.5); }
    let c = performance.now(); tQ += c - b;
    const z = zlib.zstdCompressSync(Buffer.from(u8.buffer, 0, MAPLEN)); zbytes += z.length;
    tZ += performance.now() - c;
  }
  return { W, H, sx, sy, scanMs: +scanMs.toFixed(2), fillMs: +(tFill / reps).toFixed(2), tileLoopMs: +(tLoop / reps).toFixed(2),
    argmaxMs: +(tArg / reps).toFixed(2), borderMs: +(tBord / reps).toFixed(2),
    encodeMapMs: +(tEnc / sreps).toFixed(3), quantizeMs: +(tQ / sreps).toFixed(3), zstdMs: +(tZ / sreps).toFixed(3), zKB: +(zbytes / sreps / 1024).toFixed(1) };
}
for (const [W, H] of [[1000, 500], [2000, 1000], [2904, 1672]]) console.log(JSON.stringify(bench(W, H, 20)));

/* Neuronenanzeige: Canvas-2D-Netzskizze aus echten Aktivierungsquantilen.
   NetViz.create(canvasEl, tipEl) -> {update(meta,last,state), destroy()} */
(function (global) {
  // Kurzformen der Schichtnamen fuer schmale Anzeigen (Handy).
  const SHORT_LABEL = {
    "Karten-Eingang": "Eing", "Karten-CNN": "CNN", "Karten-Tiefe": "Tiefe",
    "Gegner-Transformer": "Gegner", "Eigene Zahlen": "Eigen",
    "Spielmodus": "Modus", "Kern 1": "Kern1", "Kern 2": "Kern2", "Köpfe": "Köpfe"
  };
  const reduceMotion = matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches;

  function create(canvas, tipEl) {
    const ctx = canvas.getContext("2d");
    let meta = null, last = null, state = "wartet";
    let dpr = Math.max(1, Math.min(2, window.devicePixelRatio || 1));
    let cssW = 0, cssH = 0;
    let cols = [];          // {id,label,n,nodes:[{x,y,r0},...], isHead}
    let edges = [];         // {a:{x,y},b:{x,y},w}  base weight (0..1), from column indices
    let pulses = [];        // {edgeIdx, t}  t in 0..1 along the edge
    let raf = null;
    let lastFrame = 0;
    let speed = 0;          // pulses per second target
    let running = false;
    let hoverNode = null;

    function seedRand(seed) {
      let s = seed >>> 0;
      return function () {
        s = (s * 1664525 + 1013904223) >>> 0;
        return s / 4294967296;
      };
    }

    function layout() {
      const rect = canvas.parentElement.getBoundingClientRect();
      cssW = Math.max(280, rect.width);
      cssH = window.innerWidth < 640 ? 260 : 340;
      dpr = Math.max(1, Math.min(2, window.devicePixelRatio || 1));
      canvas.width = Math.round(cssW * dpr);
      canvas.height = Math.round(cssH * dpr);
      canvas.style.width = cssW + "px";
      canvas.style.height = cssH + "px";
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      buildColumns();
    }

    function buildColumns() {
      cols = [];
      edges = [];
      if (!meta) return;
      const layers = meta.layers || [];
      const heads = meta.heads || [];
      const nColTotal = layers.length + (heads.length ? 1 : 0);
      const padX = 46, padTop = 30, padBot = 46;
      const innerW = Math.max(60, cssW - padX * 2);
      const colGap = nColTotal > 1 ? innerW / (nColTotal - 1) : 0;
      const nodeAreaTop = padTop, nodeAreaBot = cssH - padBot;
      const nodeAreaH = Math.max(40, nodeAreaBot - nodeAreaTop);

      layers.forEach((l, ci) => {
        const x = padX + colGap * ci;
        const nNodes = 12;
        const nodes = [];
        for (let k = 0; k < nNodes; k++) {
          const y = nodeAreaTop + (nodeAreaH * (k + 0.5)) / nNodes;
          nodes.push({x, y, idx: k});
        }
        cols.push({id: l.id, label: l.name, n: l.n, nodes, isHead: false});
      });
      if (heads.length) {
        const x = padX + colGap * layers.length;
        const nodes = [];
        const nNodes = heads.length;
        for (let k = 0; k < nNodes; k++) {
          const y = nodeAreaTop + (nodeAreaH * (k + 0.5)) / nNodes;
          nodes.push({x, y, idx: k, name: heads[k]});
        }
        cols.push({id: "__heads__", label: "Köpfe", n: heads.length, nodes, isHead: true});
      }

      // Deterministische, begrenzte Kanten je Nachbarspaar
      for (let c = 0; c < cols.length - 1; c++) {
        const A = cols[c].nodes, B = cols[c + 1].nodes;
        const rnd = seedRand(c * 977 + 13);
        A.forEach((na, ai) => {
          const links = 3 + Math.floor(rnd() * 2); // 3-4
          for (let j = 0; j < links; j++) {
            const bi = Math.floor(rnd() * B.length);
            edges.push({from: [c, ai], to: [c + 1, bi], seed: rnd()});
          }
        });
      }
    }

    function brightnessFor(colIdx, nodeIdx) {
      const col = cols[colIdx];
      if (!col) return 0.05;
      if (!last) return 0.06;
      if (col.isHead) {
        const hact = last.hact || {};
        const name = col.nodes[nodeIdx].name;
        const v = hact[name];
        if (v == null) return 0.06;
        // normiere gegen die groesste vorhandene hact
        const vals = Object.values(hact).filter(x => typeof x === "number");
        const mx = Math.max(1e-6, ...vals);
        return Math.max(0.06, Math.min(1, v / mx));
      }
      const act = (last.act || {})[col.id];
      if (!act || !act.q) return 0.06;
      const q = act.q;
      const mx = Math.max(1e-6, ...q);
      return Math.max(0.05, Math.min(1, q[nodeIdx] / mx));
    }

    function activeFracFor(colIdx) {
      const col = cols[colIdx];
      if (!col || col.isHead || !last) return null;
      const act = (last.act || {})[col.id];
      if (!act) return null;
      return act.a;
    }

    function rawQ(colIdx, nodeIdx) {
      const col = cols[colIdx];
      if (!col || !last) return null;
      if (col.isHead) {
        const name = col.nodes[nodeIdx].name;
        return (last.hact || {})[name];
      }
      const act = (last.act || {})[col.id];
      return act && act.q ? act.q[nodeIdx] : null;
    }

    function draw(tsRunning) {
      ctx.clearRect(0, 0, cssW, cssH);
      if (!cols.length) return;
      const hasData = !!(last && last.act);

      // Kanten
      for (const e of edges) {
        const colA = cols[e.from[0]], colB = cols[e.to[0]];
        if (!colA || !colB) continue;
        const na = colA.nodes[e.from[1]], nb = colB.nodes[e.to[1]];
        const ba = hasData ? brightnessFor(e.from[0], e.from[1]) : 0.08;
        const bb = hasData ? brightnessFor(e.to[0], e.to[1]) : 0.08;
        const w = ba * bb;
        ctx.strokeStyle = `rgba(57,135,229,${0.06 + w * 0.5})`;
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(na.x, na.y);
        ctx.lineTo(nb.x, nb.y);
        ctx.stroke();
      }

      // Pulse (Lichtpartikel entlang Kanten)
      if (tsRunning && pulses.length) {
        for (const p of pulses) {
          const e = edges[p.edgeIdx];
          if (!e) continue;
          const colA = cols[e.from[0]], colB = cols[e.to[0]];
          if (!colA || !colB) continue;
          const na = colA.nodes[e.from[1]], nb = colB.nodes[e.to[1]];
          const x = na.x + (nb.x - na.x) * p.t;
          const y = na.y + (nb.y - na.y) * p.t;
          ctx.fillStyle = "rgba(94,207,141,.9)";
          ctx.beginPath();
          ctx.arc(x, y, 1.8, 0, Math.PI * 2);
          ctx.fill();
        }
      }

      // Knoten
      cols.forEach((col, ci) => {
        col.nodes.forEach((n, ni) => {
          const b = hasData ? brightnessFor(ci, ni) : 0.10;
          const r = 3.2 + b * 4.2;
          const isHover = hoverNode && hoverNode.ci === ci && hoverNode.ni === ni;
          ctx.beginPath();
          ctx.arc(n.x, n.y, r, 0, Math.PI * 2);
          const light = 0.35 + b * 0.65;
          ctx.fillStyle = col.isHead
            ? `rgba(25,158,112,${0.25 + b * 0.75})`
            : `rgba(57,135,229,${0.25 + b * 0.75})`;
          ctx.fill();
          if (isHover) {
            ctx.strokeStyle = "rgba(230,234,240,.9)";
            ctx.lineWidth = 1.5;
            ctx.stroke();
          }
        });
      });

      // Spaltenbeschriftung. Auf schmalen Anzeigen ueberlappen die vollen Namen,
      // darum dort Kurzformen und nur noch der Anteil aktiver Einheiten.
      const gap = cols.length > 1 && cols[0].nodes[0] && cols[1].nodes[0]
        ? Math.abs(cols[1].nodes[0].x - cols[0].nodes[0].x) : 999;
      const narrow = gap < 64;
      ctx.font = (narrow ? "9px " : "10.5px ") + "ui-monospace,SFMono-Regular,Menlo,Consolas,monospace";
      ctx.fillStyle = "rgba(139,149,163,.95)";
      ctx.textAlign = "center";
      cols.forEach((col, ci) => {
        const x = col.nodes[0] ? col.nodes[0].x : 0;
        const y = cssH - 34;
        const frac = activeFracFor(ci);
        let label = col.label;
        let sub;
        if (narrow) {
          label = SHORT_LABEL[col.label] || label.slice(0, 5);
          sub = frac != null ? `${(frac * 100).toFixed(0)}%` : "";
        } else {
          sub = col.isHead ? `${col.n} Köpfe` : `${col.n} Einh.` + (frac != null ? ` · ${(frac * 100).toFixed(0)}%` : "");
        }
        ctx.fillText(label, x, y);
        if (sub) ctx.fillText(sub, x, y + 12);
      });
    }

    function stepPulses(dtSec) {
      if (!edges.length) return;
      if (speed > 0 && running) {
        const spawnRate = Math.min(30, speed);
        const nSpawn = Math.random() < spawnRate * dtSec ? 1 : 0;
        for (let i = 0; i < nSpawn; i++) {
          pulses.push({edgeIdx: Math.floor(Math.random() * edges.length), t: 0, v: 0.6 + Math.random() * 0.6});
        }
      }
      pulses.forEach(p => { p.t += (p.v || 0.8) * dtSec; });
      pulses = pulses.filter(p => p.t < 1 && pulses.length < 260);
      if (pulses.length > 260) pulses.length = 260;
    }

    function frame(ts) {
      if (document.hidden) { raf = requestAnimationFrame(frame); return; }
      const dt = lastFrame ? Math.min(0.1, (ts - lastFrame) / 1000) : 0.016;
      lastFrame = ts;
      if (!reduceMotion) stepPulses(dt);
      draw(!reduceMotion && running);
      raf = requestAnimationFrame(frame);
    }

    function update(m, l, st) {
      const layerChanged = !meta || (m && JSON.stringify((m.layers || []).map(x => x.id)) !== JSON.stringify((meta.layers || []).map(x => x.id)));
      meta = m; last = l; state = st;
      running = state === "laeuft";
      const sps = (last && typeof last.sps === "number") ? last.sps : 0;
      // Pulsrate: an sps gekoppelt, sanft begrenzt
      const target = running ? Math.max(2, Math.min(24, sps / 25)) : 0;
      speed += (target - speed) * 0.2;
      if (!running) speed *= 0.9;
      if (layerChanged) buildColumns();
    }

    function onMove(ev) {
      const rect = canvas.getBoundingClientRect();
      const x = ev.clientX - rect.left, y = ev.clientY - rect.top;
      let found = null, bd = 12;
      cols.forEach((col, ci) => {
        col.nodes.forEach((n, ni) => {
          const d = Math.hypot(n.x - x, n.y - y);
          if (d < bd) { bd = d; found = {ci, ni, n, col}; }
        });
      });
      hoverNode = found ? {ci: found.ci, ni: found.ni} : null;
      if (found && tipEl) {
        const v = rawQ(found.ci, found.ni);
        const label = found.col.isHead
          ? `Kopf ${found.col.nodes[found.ni].name}`
          : `${found.col.label} · Quantil ${found.ni + 1}/12`;
        tipEl.hidden = false;
        tipEl.innerHTML = `<b>${v == null ? "—" : v.toFixed(4)}</b><span>${label}</span>`;
        const parentRect = canvas.parentElement.getBoundingClientRect();
        tipEl.style.left = Math.max(4, Math.min(x - tipEl.offsetWidth / 2, parentRect.width - tipEl.offsetWidth - 4)) + "px";
        tipEl.style.top = Math.max(0, y - 42) + "px";
      } else if (tipEl) {
        tipEl.hidden = true;
      }
    }
    function onLeave() { hoverNode = null; if (tipEl) tipEl.hidden = true; }

    canvas.addEventListener("mousemove", onMove);
    canvas.addEventListener("mouseleave", onLeave);

    let ro = null;
    if (window.ResizeObserver) {
      ro = new ResizeObserver(() => layout());
      ro.observe(canvas.parentElement);
    } else {
      window.addEventListener("resize", layout);
    }

    layout();
    raf = requestAnimationFrame(frame);

    function destroy() {
      if (raf) cancelAnimationFrame(raf);
      if (ro) ro.disconnect();
      canvas.removeEventListener("mousemove", onMove);
      canvas.removeEventListener("mouseleave", onLeave);
    }

    return {update, destroy};
  }

  global.NetViz = {create};
})(window);

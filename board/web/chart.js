/* Kleine SVG-Diagramme, ohne Bibliothek.
   Chart.draw(host, cfg) — cfg.type: "area" | "bars" | "line" | "hbars" | "grouped".
   points: [[x,y],…] numerisch, fuer area/bars/line.
   cfg.marks: zusaetzliche Punktserie (z.B. Val-Punkte) als Marker: {points, color, label}.
   cfg.band: {points:[[x,lo,hi]], color} fuer Perzentilband um die Hauptkurve.
   cfg.rows: fuer hbars: [{label, value, valueLabel, color, dim}].
   cfg.groups: fuer grouped: [{label, a, b, dim}] mit cfg.aLabel/bLabel/aColor/bColor. */
(function (global) {
  const NS = "http://www.w3.org/2000/svg";
  const el = (n, a) => { const e = document.createElementNS(NS, n);
    for (const k in a) e.setAttribute(k, a[k]); return e; };

  function niceStep(v) {                      // 1 / 2 / 2.5 / 5 / 10 mal Zehnerpotenz
    if (v <= 0) return 1;
    const p = Math.pow(10, Math.floor(Math.log10(v)));
    for (const m of [1, 2, 2.5, 5, 10]) if (v <= m * p) return m * p;
    return 10 * p;
  }
  function axis(minRaw, maxRaw, fixedMax, fixedMin) {
    if (fixedMax != null && fixedMin != null) {
      const step = niceStep((fixedMax - fixedMin) / 4) || 1;
      return {min: fixedMin, max: fixedMax, ticks: Math.max(1, Math.round((fixedMax - fixedMin) / step))};
    }
    const step = niceStep(Math.max(maxRaw - minRaw, Math.abs(maxRaw) || 1) / 3);
    const max = fixedMax != null ? fixedMax : Math.ceil(maxRaw / step) * step;
    const min = fixedMin != null ? fixedMin : Math.min(0, Math.floor(minRaw / step) * step);
    return {min, max, ticks: Math.max(1, Math.round((max - min) / (step || 1)))};
  }

  function emptyBox(host, msg) {
    host.innerHTML = "";
    const d = document.createElement("div");
    d.className = "empty";
    d.textContent = msg || "noch keine Daten";
    host.appendChild(d);
  }

  function draw(host, cfg) {
    const type = cfg.type || cfg.kind || "line";
    if (type === "hbars") return drawHBars(host, cfg);
    if (type === "grouped") return drawGrouped(host, cfg);
    return drawXY(host, cfg, type);
  }

  function drawXY(host, cfg, type) {
    const pts = cfg.points || [];
    host.innerHTML = "";
    if (cfg.empty || !pts.length) {
      emptyBox(host, cfg.empty === true ? undefined : (typeof cfg.empty === "string" ? cfg.empty : undefined));
      if (!pts.length) return;
    }
    if (!pts.length) return;

    const W = host.clientWidth || 420, H = cfg.height || 168;
    const xs = pts.map(p => p[0]);
    let ysAll = pts.map(p => p[1]);
    if (cfg.marks && cfg.marks.points) ysAll = ysAll.concat(cfg.marks.points.map(p => p[1]));
    if (cfg.band && cfg.band.points) {
      for (const p of cfg.band.points) { ysAll.push(p[1]); ysAll.push(p[2]); }
    }
    const x0 = Math.min(...xs), x1 = Math.max(...xs);
    const yLog = !!cfg.yLog;
    let ax, Y;
    if (yLog) {
      const positives = ysAll.filter(v => v > 0);
      const lo = Math.max(1e-6, Math.min(...positives.length ? positives : [1e-3]));
      const hi = Math.max(...positives.length ? positives : [1]);
      const lLo = Math.log10(lo), lHi = Math.log10(hi === lo ? lo * 10 : hi);
      ax = {min: lLo, max: lHi, ticks: 4};
    } else {
      ax = axis(Math.min(...ysAll), Math.max(...ysAll), cfg.yMax, cfg.yMin);
    }
    const yMin = ax.min, yMax = ax.max;
    const span = (yMax - yMin) || 1;

    const CH = 6.3;
    let wid = 0;
    for (let i = 0; i <= ax.ticks; i++) {
      const v = yMin + span * i / ax.ticks;
      const real = yLog ? Math.pow(10, v) : v;
      wid = Math.max(wid, String(cfg.yfmt(real)).length);
    }
    const padL = Math.ceil(wid * CH) + 14;
    const lastP = pts[pts.length - 1];
    const padR = cfg.endLabel === false ? 12
      : Math.ceil(String(cfg.yfmt(lastP[1])).length * 6.9) + 18;
    const padT = 10, padB = 24;
    const iw = Math.max(10, W - padL - padR), ih = H - padT - padB;
    const X = v => padL + (x1 === x0 ? iw : (v - x0) / (x1 - x0) * iw);
    Y = v => {
      const vv = yLog ? Math.log10(Math.max(v, 1e-6)) : v;
      return padT + ih - (vv - yMin) / span * ih;
    };

    const svg = el("svg", {viewBox: `0 0 ${W} ${H}`, width: "100%", height: H,
      role: "img", "aria-label": cfg.title || ""});

    for (let i = 0; i <= ax.ticks; i++) {
      const v = yMin + span * i / ax.ticks;
      const real = yLog ? Math.pow(10, v) : v;
      const y = padT + ih - (v - yMin) / span * ih;
      svg.appendChild(el("line", {x1: padL, x2: padL + iw, y1: y, y2: y,
        stroke: "var(--grid)", "stroke-width": 1}));
      const t = el("text", {x: padL - 8, y: y + 3.5, "text-anchor": "end", class: "ax"});
      t.textContent = cfg.yfmt(real);
      svg.appendChild(t);
    }
    const nx = Math.min(4, pts.length);
    for (let i = 0; i < nx; i++) {
      const p = pts[Math.round(i * (pts.length - 1) / Math.max(nx - 1, 1))];
      const t = el("text", {x: X(p[0]), y: H - 7,
        "text-anchor": i === 0 ? "start" : i === nx - 1 ? "end" : "middle", class: "ax"});
      t.textContent = cfg.xfmt(p[0]);
      svg.appendChild(t);
    }

    // Perzentilband
    if (cfg.band && cfg.band.points && cfg.band.points.length) {
      const bp = cfg.band.points;
      const top = bp.map(p => `${X(p[0]).toFixed(1)} ${Y(p[2]).toFixed(1)}`).join(" L ");
      const bot = bp.slice().reverse().map(p => `${X(p[0]).toFixed(1)} ${Y(p[1]).toFixed(1)}`).join(" L ");
      svg.appendChild(el("path", {d: `M ${top} L ${bot} Z`, fill: cfg.band.color || cfg.color,
        opacity: .16, stroke: "none"}));
    }

    if (type === "bars") {
      const bw = Math.max(1.5, iw / Math.max(pts.length, 1) - 2);
      for (const [x, y] of pts) {
        if (y <= yMin) continue;
        const h = Math.max(2, Y(yMin) - Y(y));
        svg.appendChild(el("rect", {x: X(x) - bw / 2, y: Y(y), width: bw, height: h,
          rx: Math.min(3, bw / 2), fill: cfg.color}));
      }
    } else {
      const d = pts.map((p, i) => (i ? "L" : "M") + X(p[0]).toFixed(1) + " " + Y(p[1]).toFixed(1)).join(" ");
      if (type === "area") {
        const gid = "g" + Math.random().toString(36).slice(2, 8);
        const defs = el("defs");
        const lg = el("linearGradient", {id: gid, x1: 0, y1: 0, x2: 0, y2: 1});
        lg.appendChild(el("stop", {offset: "0%", "stop-color": cfg.color, "stop-opacity": .28}));
        lg.appendChild(el("stop", {offset: "100%", "stop-color": cfg.color, "stop-opacity": 0}));
        defs.appendChild(lg); svg.appendChild(defs);
        svg.appendChild(el("path", {d: d + ` L ${X(x1)} ${Y(yMin)} L ${X(x0)} ${Y(yMin)} Z`,
          fill: `url(#${gid})`}));
      }
      svg.appendChild(el("path", {d, fill: "none", stroke: cfg.color, "stroke-width": 2,
        "stroke-linejoin": "round", "stroke-linecap": "round"}));
      svg.appendChild(el("circle", {cx: X(lastP[0]), cy: Y(lastP[1]), r: 4,
        fill: cfg.color, stroke: "var(--panel)", "stroke-width": 2}));
      if (cfg.endLabel !== false) {
        const t = el("text", {x: Math.min(X(lastP[0]) + 9, W - 4), y: Y(lastP[1]) + 4, class: "endlab"});
        t.textContent = cfg.yfmt(lastP[1]);
        svg.appendChild(t);
      }
    }

    // Zusatzserien (z.B. Val-Punkte) als Marker
    if (cfg.marks && cfg.marks.points && cfg.marks.points.length) {
      for (const [mx, my] of cfg.marks.points) {
        svg.appendChild(el("circle", {cx: X(mx), cy: Y(my), r: 4.5,
          fill: cfg.marks.color || "var(--warn)", stroke: "var(--panel)", "stroke-width": 1.5}));
      }
    }

    // Legende, falls mehrere Serien
    if (cfg.legend && cfg.legend.length) {
      let lx = padL;
      for (const item of cfg.legend) {
        svg.appendChild(el("circle", {cx: lx + 4, cy: 12, r: 4, fill: item.color}));
        const t = el("text", {x: lx + 12, y: 15, class: "ax"});
        t.textContent = item.label;
        svg.appendChild(t);
        lx += 12 + item.label.length * 6.4 + 14;
      }
    }

    // Hover: Fadenkreuz + Tooltip (auf Hauptpunkte)
    const cross = el("line", {y1: padT, y2: padT + ih, stroke: "var(--fg)",
      "stroke-width": 1, opacity: 0, "stroke-dasharray": "3 3"});
    const dot = el("circle", {r: 4, fill: cfg.color, stroke: "var(--panel)",
      "stroke-width": 2, opacity: 0});
    svg.appendChild(cross); svg.appendChild(dot);
    const tip = document.createElement("div");
    tip.className = "tip"; tip.hidden = true;
    host.appendChild(svg); host.appendChild(tip);

    const hit = el("rect", {x: padL, y: padT, width: iw, height: ih, fill: "transparent"});
    svg.appendChild(hit);
    const move = ev => {
      const r = svg.getBoundingClientRect();
      const px = (ev.touches ? ev.touches[0].clientX : ev.clientX) - r.left;
      const vx = px / r.width * W;
      let best = pts[0], bd = Infinity;
      for (const p of pts) { const d2 = Math.abs(X(p[0]) - vx); if (d2 < bd) { bd = d2; best = p; } }
      cross.setAttribute("x1", X(best[0])); cross.setAttribute("x2", X(best[0]));
      cross.setAttribute("opacity", .35);
      dot.setAttribute("cx", X(best[0])); dot.setAttribute("cy", Y(best[1]));
      dot.setAttribute("opacity", 1);
      tip.hidden = false;
      tip.innerHTML = `<b>${cfg.yfmt(best[1])}</b><span>${cfg.xtip(best[0])}</span>`;
      const left = X(best[0]) / W * r.width;
      tip.style.left = Math.max(4, Math.min(left - tip.offsetWidth / 2, r.width - tip.offsetWidth - 4)) + "px";
      tip.style.top = Math.max(0, Y(best[1]) / H * r.height - tip.offsetHeight - 10) + "px";
    };
    const leave = () => { cross.setAttribute("opacity", 0); dot.setAttribute("opacity", 0); tip.hidden = true; };
    svg.addEventListener("mousemove", move);
    svg.addEventListener("mouseleave", leave);
    svg.addEventListener("touchmove", move, {passive: true});
    svg.addEventListener("touchend", leave);
  }

  // Horizontale Balkenreihen mit Beschriftung: cfg.rows=[{label,value,valueLabel,color,dim}]
  function drawHBars(host, cfg) {
    host.innerHTML = "";
    const rows = (cfg.rows || []).filter(Boolean);
    if (!rows.length) { emptyBox(host, cfg.empty); return; }
    const W = host.clientWidth || 420;
    const rh = cfg.rowHeight || 22, gap = 6;
    const H = rows.length * (rh + gap) - gap + 6;
    const max = cfg.max != null ? cfg.max : Math.max(1e-9, ...rows.map(r => r.value || 0));
    const labelW = Math.min(140, Math.max(60, W * 0.28));
    const valW = 54;
    const barW = Math.max(20, W - labelW - valW - 12);
    const svg = el("svg", {viewBox: `0 0 ${W} ${H}`, width: "100%", height: H});
    rows.forEach((r, i) => {
      const y = i * (rh + gap);
      const lab = el("text", {x: 0, y: y + rh / 2 + 4, class: "ax",
        style: `fill:${r.dim ? "var(--dim)" : "var(--fg)"}`});
      lab.textContent = r.label;
      svg.appendChild(lab);
      svg.appendChild(el("rect", {x: labelW, y, width: barW, height: rh, rx: 4,
        fill: "var(--line)"}));
      if (!r.dim) {
        const w = Math.max(0, Math.min(1, (r.value || 0) / (max || 1))) * barW;
        svg.appendChild(el("rect", {x: labelW, y, width: Math.max(2, w), height: rh, rx: 4,
          fill: r.color || "var(--accent)"}));
      }
      const vt = el("text", {x: labelW + barW + valW - 2, y: y + rh / 2 + 4,
        "text-anchor": "end", class: "ax"});
      vt.textContent = r.dim ? "nicht im Batch" : (r.valueLabel != null ? r.valueLabel : r.value);
      svg.appendChild(vt);
    });
    host.appendChild(svg);
  }

  // Gruppiertes Doppelbalkendiagramm: cfg.groups=[{label,a,b}]
  function drawGrouped(host, cfg) {
    host.innerHTML = "";
    const groups = (cfg.groups || []).filter(Boolean);
    if (!groups.length) { emptyBox(host, cfg.empty); return; }
    const W = host.clientWidth || 420;
    const crowded = groups.length > 8;
    const H = cfg.height || (crowded ? 240 : 200);
    const padL = 34, padR = 10, padT = 22, padB = crowded ? 84 : 44;
    const iw = W - padL - padR, ih = H - padT - padB;
    const max = Math.max(1e-9, ...groups.flatMap(g => [g.a || 0, g.b || 0]));
    const ax = axis(0, max, null);
    const span = ax.max || 1;
    const gW = iw / groups.length;
    const barW = Math.max(2, Math.min(22, gW * 0.32));
    const svg = el("svg", {viewBox: `0 0 ${W} ${H}`, width: "100%", height: H});
    for (let i = 0; i <= ax.ticks; i++) {
      const v = ax.min + span * i / ax.ticks;
      const y = padT + ih - (v - ax.min) / span * ih;
      svg.appendChild(el("line", {x1: padL, x2: padL + iw, y1: y, y2: y,
        stroke: "var(--grid)", "stroke-width": 1}));
      const t = el("text", {x: padL - 6, y: y + 3.5, "text-anchor": "end", class: "ax"});
      t.textContent = cfg.yfmt ? cfg.yfmt(v) : v;
      svg.appendChild(t);
    }
    groups.forEach((g, i) => {
      const cx = padL + gW * i + gW / 2;
      const yBase = padT + ih;
      const ha = Math.max(0, (g.a || 0) / span * ih);
      const hb = Math.max(0, (g.b || 0) / span * ih);
      svg.appendChild(el("rect", {x: cx - barW - 1, y: yBase - ha, width: barW, height: Math.max(1, ha),
        fill: cfg.aColor || "var(--accent)", rx: 2}));
      svg.appendChild(el("rect", {x: cx + 1, y: yBase - hb, width: barW, height: Math.max(1, hb),
        fill: cfg.bColor || "var(--acc2)", rx: 2}));
      let lab;
      if (crowded) {
        lab = el("text", {x: 0, y: 0, "text-anchor": "end", class: "ax",
          transform: `translate(${cx},${H - padB + 12}) rotate(-55)`});
      } else {
        lab = el("text", {x: cx, y: H - padB + 16, "text-anchor": "middle", class: "ax"});
      }
      lab.textContent = g.label.length > 14 ? g.label.slice(0, 13) + "…" : g.label;
      svg.appendChild(lab);
    });
    // Legende
    if (cfg.aLabel || cfg.bLabel) {
      let lx = padL;
      [[cfg.aLabel, cfg.aColor || "var(--accent)"], [cfg.bLabel, cfg.bColor || "var(--acc2)"]].forEach(([lab, col]) => {
        if (!lab) return;
        svg.appendChild(el("circle", {cx: lx + 4, cy: 10, r: 4, fill: col}));
        const t = el("text", {x: lx + 12, y: 13, class: "ax"});
        t.textContent = lab;
        svg.appendChild(t);
        lx += 12 + lab.length * 6.4 + 16;
      });
    }
    host.appendChild(svg);
  }

  global.Chart = {draw, niceStep};
})(window);

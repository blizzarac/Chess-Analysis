/* Small inline-SVG chart helpers. No dependencies; everything is drawn to one scale per chart. */
(function () {
  const NS = "http://www.w3.org/2000/svg";
  const SERIES = ["#3f7d5a", "#3b6fb6", "#c98a2b", "#b2413b", "#7b5ea7", "#4f9aa8", "#8a8a3a", "#a05a8c"];

  function el(tag, attrs, children) {
    const e = document.createElementNS(NS, tag);
    for (const k in attrs || {}) e.setAttribute(k, attrs[k]);
    for (const c of children || []) e.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
    return e;
  }
  function niceTicks(min, max, count) {
    if (min === max) { max = min + 1; }
    const span = max - min;
    const step0 = span / Math.max(1, count);
    const mag = Math.pow(10, Math.floor(Math.log10(step0)));
    const norm = step0 / mag;
    const step = (norm < 1.5 ? 1 : norm < 3 ? 2 : norm < 7 ? 5 : 10) * mag;
    const lo = Math.floor(min / step) * step;
    const hi = Math.ceil(max / step) * step;
    const ticks = [];
    for (let v = lo; v <= hi + 1e-9; v += step) ticks.push(+v.toFixed(10));
    return { ticks, lo, hi };
  }
  let tip;
  function showTip(evt, html) {
    if (!tip) { tip = document.createElement("div"); tip.className = "chart-tip"; document.body.appendChild(tip); }
    tip.innerHTML = html;
    tip.style.left = (evt.clientX + 12) + "px";
    tip.style.top = (evt.clientY + 12) + "px";
    tip.hidden = false;
  }
  function hideTip() { if (tip) tip.hidden = true; }

  /** Line chart. series: [{name, points:[{x, y, label?}], color?}]; x numeric. */
  function lineChart(opts) {
    const W = opts.width || 640, H = opts.height || 220;
    const m = { t: 14, r: 14, b: 28, l: 44 };
    const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, class: "chart", role: "img", "aria-label": opts.title || "line chart" });
    const pts = opts.series.flatMap(s => s.points);
    if (!pts.length) { svg.appendChild(el("text", { x: W / 2, y: H / 2, "text-anchor": "middle" }, ["No data"])); return svg; }
    const xs = pts.map(p => p.x), ys = pts.map(p => p.y);
    let xmin = Math.min(...xs), xmax = Math.max(...xs);
    if (xmin === xmax) { xmin -= 1; xmax += 1; }
    const yt = niceTicks(opts.yMin != null ? opts.yMin : Math.min(...ys), opts.yMax != null ? opts.yMax : Math.max(...ys), 4);
    const X = x => m.l + (x - xmin) / (xmax - xmin) * (W - m.l - m.r);
    const Y = y => H - m.b - (y - yt.lo) / (yt.hi - yt.lo) * (H - m.t - m.b);
    const grid = el("g", { class: "grid" });
    for (const t of yt.ticks) {
      grid.appendChild(el("line", { x1: m.l, x2: W - m.r, y1: Y(t), y2: Y(t) }));
      grid.appendChild(el("text", { x: m.l - 6, y: Y(t) + 4, "text-anchor": "end" }, [opts.yFormat ? opts.yFormat(t) : String(t)]));
    }
    svg.appendChild(grid);
    if (opts.refY != null && opts.refY >= yt.lo && opts.refY <= yt.hi) {
      svg.appendChild(el("line", { class: "ref", x1: m.l, x2: W - m.r, y1: Y(opts.refY), y2: Y(opts.refY) }));
    }
    // x labels
    const nx = Math.min(6, pts.length);
    const xl = el("g", { class: "axis" });
    for (let i = 0; i < nx; i++) {
      const x = xmin + (xmax - xmin) * i / Math.max(1, nx - 1);
      xl.appendChild(el("text", { x: X(x), y: H - 8, "text-anchor": i === 0 ? "start" : i === nx - 1 ? "end" : "middle" }, [opts.xFormat ? opts.xFormat(x) : String(Math.round(x))]));
    }
    svg.appendChild(xl);
    opts.series.forEach((s, si) => {
      const color = s.color || SERIES[si % SERIES.length];
      const sorted = s.points.slice().sort((a, b) => a.x - b.x);
      const d = sorted.map((p, i) => (i ? "L" : "M") + X(p.x).toFixed(1) + " " + Y(p.y).toFixed(1)).join(" ");
      if (opts.area) {
        const a = d + ` L${X(sorted[sorted.length - 1].x).toFixed(1)} ${Y(yt.lo)} L${X(sorted[0].x).toFixed(1)} ${Y(yt.lo)} Z`;
        svg.appendChild(el("path", { d: a, class: "area", fill: color }));
      }
      svg.appendChild(el("path", { d, class: "series", stroke: color }));
      if (sorted.length <= 60 || opts.markers) {
        for (const p of sorted) {
          const c = el("circle", { cx: X(p.x), cy: Y(p.y), r: sorted.length > 60 ? 2 : 3.5, fill: color, stroke: "var(--surface)", "stroke-width": 1.5 });
          c.addEventListener("mousemove", e => showTip(e, p.label || `${s.name}: ${p.y}`));
          c.addEventListener("mouseleave", hideTip);
          svg.appendChild(c);
        }
      }
    });
    // hover crosshair for long series
    const hot = el("rect", { x: m.l, y: m.t, width: W - m.l - m.r, height: H - m.t - m.b, fill: "transparent" });
    hot.addEventListener("mousemove", e => {
      const r = svg.getBoundingClientRect();
      const px = (e.clientX - r.left) / r.width * W;
      const xv = xmin + (px - m.l) / (W - m.l - m.r) * (xmax - xmin);
      let best = null, bd = Infinity;
      for (const s of opts.series) for (const p of s.points) { const d = Math.abs(p.x - xv); if (d < bd) { bd = d; best = { s, p }; } }
      if (best) showTip(e, best.p.label || `${best.s.name}: ${best.p.y}`);
    });
    hot.addEventListener("mouseleave", hideTip);
    svg.appendChild(hot);
    return svg;
  }

  /** Vertical bars. data: [{label, value, color?, tip?}] */
  function barChart(opts) {
    const W = opts.width || 640, H = opts.height || 200;
    const m = { t: 16, r: 10, b: 30, l: 40 };
    const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, class: "chart", role: "img", "aria-label": opts.title || "bar chart" });
    const data = opts.data.filter(d => d.value != null);
    if (!data.length) { svg.appendChild(el("text", { x: W / 2, y: H / 2, "text-anchor": "middle" }, ["No data"])); return svg; }
    const vmax = opts.yMax != null ? opts.yMax : Math.max(...data.map(d => d.value), 0);
    const vmin = Math.min(0, ...data.map(d => d.value));
    const yt = niceTicks(vmin, vmax, 4);
    const Y = v => H - m.b - (v - yt.lo) / (yt.hi - yt.lo) * (H - m.t - m.b);
    const grid = el("g", { class: "grid" });
    for (const t of yt.ticks) {
      grid.appendChild(el("line", { x1: m.l, x2: W - m.r, y1: Y(t), y2: Y(t) }));
      grid.appendChild(el("text", { x: m.l - 6, y: Y(t) + 4, "text-anchor": "end" }, [opts.yFormat ? opts.yFormat(t) : String(t)]));
    }
    svg.appendChild(grid);
    if (opts.refY != null) svg.appendChild(el("line", { class: "ref", x1: m.l, x2: W - m.r, y1: Y(opts.refY), y2: Y(opts.refY) }));
    const n = opts.data.length;
    const bw = (W - m.l - m.r) / n;
    opts.data.forEach((d, i) => {
      const x = m.l + i * bw + bw * 0.15;
      const w = bw * 0.7;
      if (d.value != null) {
        const y0 = Y(Math.max(0, d.value)), y1 = Y(Math.min(0, d.value));
        const r = el("rect", { x, y: y0, width: w, height: Math.max(1, y1 - y0), fill: d.color || opts.color || SERIES[0], class: "bar" });
        r.addEventListener("mousemove", e => showTip(e, d.tip || `${d.label}: ${d.value}`));
        r.addEventListener("mouseleave", hideTip);
        svg.appendChild(r);
        if (opts.valueLabels) svg.appendChild(el("text", { x: x + w / 2, y: y0 - 4, "text-anchor": "middle", class: "label" }, [opts.yFormat ? opts.yFormat(d.value) : String(d.value)]));
      }
      if (n <= 32 || i % Math.ceil(n / 16) === 0) {
        svg.appendChild(el("text", { x: x + w / 2, y: H - 10, "text-anchor": "middle" }, [String(d.label)]));
      }
    });
    return svg;
  }

  /** Horizontal bars with labels on the left. data: [{label, value, color?, tip?, extra?}] */
  function hbarChart(opts) {
    const rowH = 24, W = opts.width || 640;
    const labelW = opts.labelWidth || 200;
    const H = opts.data.length * rowH + 10;
    const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, class: "chart", role: "img", "aria-label": opts.title || "bar chart" });
    const max = opts.max != null ? opts.max : Math.max(1, ...opts.data.map(d => d.value || 0));
    const plotW = W - labelW - (opts.extraWidth || 60);
    opts.data.forEach((d, i) => {
      const y = i * rowH + 5;
      const t = el("text", { x: labelW - 8, y: y + 15, "text-anchor": "end", class: "label" }, [d.label.length > 30 ? d.label.slice(0, 29) + "…" : d.label]);
      svg.appendChild(t);
      const w = Math.max(0, (d.value || 0) / max * plotW);
      const r = el("rect", { x: labelW, y: y + 4, width: w, height: rowH - 10, fill: d.color || opts.color || SERIES[0], class: "bar" });
      r.addEventListener("mousemove", e => showTip(e, d.tip || `${d.label}: ${d.value}`));
      r.addEventListener("mouseleave", hideTip);
      svg.appendChild(r);
      svg.appendChild(el("text", { x: labelW + w + 6, y: y + 15 }, [d.extra != null ? d.extra : String(d.value)]));
    });
    if (opts.refX != null) {
      const x = labelW + opts.refX / max * plotW;
      svg.appendChild(el("line", { class: "ref", x1: x, x2: x, y1: 0, y2: H }));
    }
    return svg;
  }

  /** Stacked 100% horizontal bar of W/D/L */
  function wdlBar(w, d, l) {
    const n = w + d + l || 1;
    const div = document.createElement("span");
    div.className = "wdl";
    div.title = `${w} wins, ${d} draws, ${l} losses`;
    div.innerHTML = `<span class="w" style="width:${100 * w / n}%"></span><span class="d" style="width:${100 * d / n}%"></span><span class="l" style="width:${100 * l / n}%"></span>`;
    return div;
  }

  /** Evaluation graph for a game: values are white-POV centipawns, clamped to ±1000. */
  function evalChart(curve, opts) {
    const W = opts.width || 640, H = opts.height || 120;
    const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, class: "chart eval", role: "img", "aria-label": "evaluation graph" });
    if (!curve || curve.length < 2) return svg;
    const n = curve.length;
    const X = i => i / (n - 1) * W;
    const Y = v => H / 2 - Math.max(-1, Math.min(1, v / 1000)) * (H / 2 - 4) ;
    svg.appendChild(el("rect", { x: 0, y: 0, width: W, height: H, fill: "var(--sq-dark)", opacity: 0.35 }));
    let d = `M0 ${H / 2}`;
    curve.forEach((v, i) => { d += ` L${X(i).toFixed(1)} ${Y(v).toFixed(1)}`; });
    d += ` L${W} ${H / 2} Z`;
    // white area above midline, black below: draw white-filled polygon relative to midline
    const above = `M0 ${H} ` + curve.map((v, i) => `L${X(i).toFixed(1)} ${Y(v).toFixed(1)}`).join(" ") + ` L${W} ${H} Z`;
    svg.appendChild(el("path", { d: above, fill: "var(--white-piece)", opacity: 0.9 }));
    svg.appendChild(el("line", { x1: 0, x2: W, y1: H / 2, y2: H / 2, stroke: "var(--ink-3)", "stroke-width": 1, "stroke-dasharray": "3 3" }));
    const marker = el("line", { x1: 0, x2: 0, y1: 0, y2: H, stroke: "var(--info)", "stroke-width": 2 });
    svg.appendChild(marker);
    if (opts.marks) for (const mk of opts.marks) {
      svg.appendChild(el("circle", { cx: X(mk.index), cy: Y(mk.value), r: 4, fill: mk.color, stroke: "var(--surface)", "stroke-width": 1 }));
    }
    const hot = el("rect", { x: 0, y: 0, width: W, height: H, fill: "transparent", style: "cursor:pointer" });
    hot.addEventListener("click", e => {
      const r = svg.getBoundingClientRect();
      const i = Math.round((e.clientX - r.left) / r.width * (n - 1));
      if (opts.onSelect) opts.onSelect(i);
    });
    hot.addEventListener("mousemove", e => {
      const r = svg.getBoundingClientRect();
      const i = Math.round((e.clientX - r.left) / r.width * (n - 1));
      showTip(e, (opts.tipFor ? opts.tipFor(i) : `ply ${i}: ${(curve[i] / 100).toFixed(2)}`));
    });
    hot.addEventListener("mouseleave", hideTip);
    svg.appendChild(hot);
    svg.setMarker = i => marker.setAttribute("x1", X(i)) || marker.setAttribute("x2", X(i));
    return svg;
  }

  window.Charts = { lineChart, barChart, hbarChart, wdlBar, evalChart, SERIES };
})();

// Charts: dependency-free SVG/HTML, so the app works offline and every pixel is explainable.
// Mark specs (from the data-viz method): bars <= 24px thick, 4px rounded data-end and square at the baseline, 2px surface gap between
// stacked segments, 2px lines, >= 8px end-dots with a 2px surface ring, hairline SOLID grid, selective labels (never a number on every
// mark), text in ink tokens (never the series colour), hover tooltips with short line-keys, a bigger-than-the-mark hit area, and a
// table view for every chart (tooltips enhance, they never gate).
import { h, svg, inrC, inrAuto, showTip, hideTip, tipRow, bindTip, icon } from "./ui.js";

export const SERIES = ["var(--s1)", "var(--s2)", "var(--s3)", "var(--s4)", "var(--s5)", "var(--s6)", "var(--s7)", "var(--s8)"];
export const OTHER = "var(--ink-3)";

function niceMax(v) {
  if (v <= 0) return 1;
  const p = Math.pow(10, Math.floor(Math.log10(v)));
  for (const m of [1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10]) if (m * p >= v) return m * p;
  return 10 * p;
}
function roundedTop(x, y, w, hgt, r) {   // 4px rounded data-end, square at the baseline
  r = Math.min(r, w / 2, hgt);
  return `M${x},${y + hgt}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + hgt}Z`;
}
function fill(el, color) { el.style.fill = color; return el; }
function fmtShort(v) { return inrC(v); }

// ---- horizontal bars (HTML) ---------------------------------------------------------------------------------------------------------
/** items: [{label, value, sub?, kind?: "neg"|"pos"|"unsure", href?, onClick?, tip?: [[label, value], ...], valueText?}] */
export function hbars(items, { fmt = inrAuto, max } = {}) {
  const m = max ?? Math.max(...items.map((i) => Math.abs(i.value)), 1);
  const root = h("div", { class: "hbars", role: "list" });
  for (const it of items) {
    const w = Math.max(0.6, (Math.abs(it.value) / m) * 78);
    const lab = it.href ? h("a", { href: it.href, title: it.label, text: it.label }) : h("span", { title: it.label, text: it.label });
    const bar = h("div", { class: `bar ${it.kind || ""}`, style: { width: w + "%" } });
    const val = h("div", { class: "val", style: { left: `calc(${w}% + 8px)` } }, it.valueText ?? fmt(it.value), it.sub ? h("small", { text: it.sub }) : null);
    const row = h("div", { class: "hb", role: "listitem", tabindex: it.onClick ? "0" : null }, h("div", { class: "lab" }, lab), h("div", { class: "track" }, bar, val));
    if (it.onClick) { row.style.cursor = "pointer"; row.addEventListener("click", it.onClick); row.addEventListener("keydown", (e) => { if (e.key === "Enter") it.onClick(); }); }
    bindTip(row, () => [h("div", { class: "t-head", text: it.label }), tipRow("Amount", it.valueText ?? fmt(it.value)), ...(it.tip || []).map(([k, v]) => tipRow(k, v))]);
    root.append(row);
  }
  return root;
}

// ---- columns (SVG) --------------------------------------------------------------------------------------------------------------------------
/** data: [{label, short?, value, tip?: [[label,value]]}] */
export function columns(el, { data, height = 200, fmt = inrAuto, color = SERIES[0], highlight = null, ariaLabel = "Bar chart", labelMax = true }) {
  el.replaceChildren();
  if (!data.length) { el.append(h("p", { class: "muted", text: "No data in this period." })); return; }
  const W = Math.max(260, el.clientWidth || 600), H = height, mg = { l: 42, r: 6, t: 18, b: 22 };
  const iw = W - mg.l - mg.r, ih = H - mg.t - mg.b, n = data.length;
  const top = niceMax(Math.max(...data.map((d) => d.value), 1));
  const y = (v) => mg.t + ih - (v / top) * ih;
  const root = svg("svg", { class: "chart", viewBox: `0 0 ${W} ${H}`, width: W, height: H, role: "img", "aria-label": ariaLabel });
  for (const t of [0, top / 2, top]) {
    root.append(svg("line", { class: t === 0 ? "axis" : "grid", x1: mg.l, x2: W - mg.r, y1: y(t), y2: y(t) }));
    root.append(svg("text", { x: mg.l - 6, y: y(t) + 3.5, "text-anchor": "end", text: inrC(t) }));
  }
  const slot = iw / n, bw = Math.min(24, Math.max(2.5, slot * 0.62));
  const step = Math.max(1, Math.ceil(n / Math.max(2, Math.floor(iw / 58))));
  const maxIdx = data.reduce((b, d, i) => (d.value > data[b].value ? i : b), 0);
  data.forEach((d, i) => {
    const cx = mg.l + slot * (i + 0.5), bh = Math.max(d.value > 0 ? 1.5 : 0, (d.value / top) * ih);
    if (bh > 0) root.append(fill(svg("path", { class: "mark", d: roundedTop(cx - bw / 2, y(d.value), bw, bh, 4) }), highlight === i ? "var(--s2)" : color));
    if (i % step === 0) root.append(svg("text", { x: cx, y: H - 6, "text-anchor": "middle", text: d.short ?? d.label }));
    const hit = svg("rect", { class: "hit", x: mg.l + slot * i, y: mg.t, width: slot, height: ih + mg.b, tabindex: n <= 12 ? "0" : null, "aria-label": `${d.label}: ${fmt(d.value)}` });
    bindTip(hit, () => [h("div", { class: "t-head", text: d.label }), tipRow("Spent", fmt(d.value), color), ...(d.tip || []).map(([k, v]) => tipRow(k, v))]);
    root.append(hit);
  });
  if (labelMax && data[maxIdx].value > 0) {                                   // one selective direct label: the extreme
    const cx = mg.l + slot * (maxIdx + 0.5);
    root.append(svg("text", { class: "lbl", x: Math.min(Math.max(cx, mg.l + 14), W - 16), y: y(data[maxIdx].value) - 5, "text-anchor": "middle", text: inrC(data[maxIdx].value) }));
  }
  el.append(root);
}

// ---- stacked columns (SVG) ----------------------------------------------------------------------------------------------------------------------
/** series: [{name, color, values: []}], labels: [..]; partialLast dims the last column and says so. */
export function stacked(el, { labels, series, totals, partialLast = false, height = 230, fmt = inrAuto, ariaLabel = "Stacked bar chart" }) {
  el.replaceChildren();
  if (!labels.length) { el.append(h("p", { class: "muted", text: "No data yet." })); return; }
  const W = Math.max(260, el.clientWidth || 600), H = height, mg = { l: 42, r: 6, t: 20, b: 22 };
  const iw = W - mg.l - mg.r, ih = H - mg.t - mg.b, n = labels.length;
  const top = niceMax(Math.max(...totals, 1));
  const y = (v) => mg.t + ih - (v / top) * ih;
  const root = svg("svg", { class: "chart", viewBox: `0 0 ${W} ${H}`, width: W, height: H, role: "img", "aria-label": ariaLabel });
  for (const t of [0, top / 2, top]) {
    root.append(svg("line", { class: t === 0 ? "axis" : "grid", x1: mg.l, x2: W - mg.r, y1: y(t), y2: y(t) }));
    root.append(svg("text", { x: mg.l - 6, y: y(t) + 3.5, "text-anchor": "end", text: inrC(t) }));
  }
  const slot = iw / n, bw = Math.min(24, Math.max(6, slot * 0.5));
  labels.forEach((lab, i) => {
    const cx = mg.l + slot * (i + 0.5), x = cx - bw / 2;
    let acc = 0;
    const segs = series.map((s) => ({ s, v: s.values[i] || 0 })).filter((o) => o.v > 0);
    segs.forEach((o, k) => {
      const y0 = y(acc), y1 = y(acc + o.v), isTop = k === segs.length - 1;
      const gap = k === 0 ? 0 : 2;                                           // 2px surface gap between segments (not a stroke)
      const hh = Math.max(1, y0 - y1 - gap), yy = y1;
      const node = isTop ? svg("path", { class: "mark", d: roundedTop(x, yy, bw, hh, 4) }) : svg("rect", { class: "mark", x, y: yy, width: bw, height: hh });
      fill(node, o.s.color);
      if (partialLast && i === n - 1) node.style.opacity = "0.6";
      root.append(node);
      acc += o.v;
    });
    root.append(svg("text", { x: cx, y: H - 6, "text-anchor": "middle", text: lab + (partialLast && i === n - 1 ? "*" : "") }));
    if (totals[i] > 0) root.append(svg("text", { class: "lbl", x: cx, y: y(totals[i]) - 5, "text-anchor": "middle", text: inrC(totals[i]) }));
    const hit = svg("rect", { class: "hit", x: mg.l + slot * i, y: mg.t - 10, width: slot, height: ih + mg.b + 10, tabindex: n <= 12 ? "0" : null, "aria-label": `${lab}: ${fmt(totals[i])}` });
    bindTip(hit, () => [h("div", { class: "t-head", text: lab + (partialLast && i === n - 1 ? " (so far)" : "") }),
                        ...series.filter((s) => (s.values[i] || 0) > 0).slice().reverse().map((s) => tipRow(s.name, fmt(s.values[i]), s.color)),
                        h("div", { style: { borderTop: "1px solid var(--grid)", marginTop: "4px", paddingTop: "4px" } }, tipRow("Total", fmt(totals[i])))]);
    root.append(hit);
  });
  el.append(root, legend(series));
  if (partialLast) el.append(h("p", { class: "muted", style: { fontSize: "12px", margin: "6px 0 0" }, text: "* the latest month is not complete yet" }));
}

export function legend(series) {
  return h("div", { class: "legend" }, series.map((s) => h("span", { class: "k" }, h("span", { class: "sw", style: { background: s.color } }), s.name)));
}

// ---- lines (SVG): two periods compared on ONE axis ---------------------------------------------------------------------------------------------------
/** Cumulative spend, "this period" vs "previous period" (emphasis form: accent for now, gray for context). */
export function lines(el, { recent, base, recentLabel, baseLabel, height = 220, fmt = inrAuto, ariaLabel = "Cumulative spending" }) {
  el.replaceChildren();
  const n = Math.max(recent.length, base.length);
  if (n < 2) { el.append(h("p", { class: "muted", text: "Not enough days to draw a trend." })); return; }
  const W = Math.max(260, el.clientWidth || 600), H = height, mg = { l: 42, r: 46, t: 14, b: 22 };
  const iw = W - mg.l - mg.r, ih = H - mg.t - mg.b;
  const top = niceMax(Math.max(...recent, ...base, 1));
  const x = (i) => mg.l + (i / (n - 1)) * iw, y = (v) => mg.t + ih - (v / top) * ih;
  const root = svg("svg", { class: "chart", viewBox: `0 0 ${W} ${H}`, width: W, height: H, role: "img", "aria-label": ariaLabel });
  for (const t of [0, top / 2, top]) {
    root.append(svg("line", { class: t === 0 ? "axis" : "grid", x1: mg.l, x2: W - mg.r, y1: y(t), y2: y(t) }));
    root.append(svg("text", { x: mg.l - 6, y: y(t) + 3.5, "text-anchor": "end", text: inrC(t) }));
  }
  const path = (arr) => arr.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join("");
  const mk = (arr, color) => { const p = svg("path", { d: path(arr), fill: "none", "stroke-width": 2, "stroke-linecap": "round", "stroke-linejoin": "round" }); p.style.stroke = color; return p; };
  root.append(mk(base, OTHER), mk(recent, SERIES[0]));
  if (recent.length) {                                  // end-dot for the current period only; labels would collide where the lines meet (legend carries identity)
    const i = recent.length - 1;
    const dot = svg("circle", { cx: x(i), cy: y(recent[i]), r: 4.5, "stroke-width": 2 }); fill(dot, SERIES[0]); dot.style.stroke = "var(--surface)"; root.append(dot);
    root.append(svg("text", { class: "lbl", x: x(i) + 9, y: y(recent[i]) + 4, text: fmtShort(recent[i]) }));
  }
  const days = n, stepX = Math.max(1, Math.ceil(days / 8));
  for (let i = 0; i < n; i += stepX) root.append(svg("text", { x: x(i), y: H - 6, "text-anchor": "middle", text: String(i + 1) }));
  const cross = svg("line", { class: "grid", y1: mg.t, y2: mg.t + ih, style: { display: "none" } });
  root.append(cross);
  const hit = svg("rect", { class: "hit", x: mg.l, y: mg.t, width: iw, height: ih });
  const move = (e) => {
    const r = root.getBoundingClientRect(), px = ((e.clientX - r.left) / r.width) * W;
    const i = Math.max(0, Math.min(n - 1, Math.round(((px - mg.l) / iw) * (n - 1))));
    cross.setAttribute("x1", x(i)); cross.setAttribute("x2", x(i)); cross.style.display = "";
    showTip([h("div", { class: "t-head", text: `Day ${i + 1}` }), ...(i < recent.length ? [tipRow(recentLabel, fmt(recent[i]), SERIES[0])] : []),
             ...(i < base.length ? [tipRow(baseLabel, fmt(base[i]), OTHER)] : [])], e);
  };
  hit.addEventListener("pointermove", move);
  hit.addEventListener("pointerleave", () => { cross.style.display = "none"; hideTip(); });
  root.append(hit);
  el.append(root, legend([{ name: recentLabel, color: SERIES[0] }, { name: baseLabel, color: OTHER }]));
}

// ---- sparkline of bars -----------------------------------------------------------------------------------------------------------------------------------------
export function spark(values, { w = 120, hgt = 28 } = {}) {
  const max = Math.max(...values, 1), n = values.length || 1, slot = w / n, bw = Math.max(2, Math.min(10, slot * 0.6));
  const root = svg("svg", { class: "spark", viewBox: `0 0 ${w} ${hgt}`, width: w, height: hgt, "aria-hidden": "true" });
  values.forEach((v, i) => { const bh = Math.max(v > 0 ? 1.5 : 0, (v / max) * (hgt - 2)); if (bh) root.append(fill(svg("path", { d: roundedTop(slot * i + (slot - bw) / 2, hgt - bh, bw, bh, 2) }), SERIES[0])); });
  return root;
}

// ---- table twin + the card that holds a chart ------------------------------------------------------------------------------------------------------------
export function tableView(columnsDef, rows) {
  return h("div", { class: "tbl-wrap" }, h("table", { class: "tbl" }, h("thead", {}, h("tr", {}, columnsDef.map((c) => h("th", { class: c.right ? "right" : "", text: c.label })))),
    h("tbody", {}, rows.map((r) => h("tr", {}, r.map((c, i) => h("td", { class: columnsDef[i]?.right ? "right num" : "", text: c })))))));
}

/** A card with a title and a Chart | Table toggle. draw(el) renders the chart into el (re-called on resize); table() returns {columns, rows}. */
export function chartCard({ title, sub, draw, table, tools }) {
  const body = h("div", { class: "chart-body" });
  const tbl = h("div", { class: "hidden" });
  let mode = "chart";
  const btnChart = h("button", { "aria-pressed": "true", onclick: () => set("chart") }, "Chart");
  const btnTable = h("button", { "aria-pressed": "false", onclick: () => set("table") }, "Table");
  function set(m) {
    mode = m;
    btnChart.setAttribute("aria-pressed", String(m === "chart")); btnTable.setAttribute("aria-pressed", String(m === "table"));
    body.classList.toggle("hidden", m !== "chart"); tbl.classList.toggle("hidden", m !== "table");
    if (m === "table" && !tbl.firstChild) { const t = table(); tbl.append(tableView(t.columns, t.rows)); }
    if (m === "chart") draw(body);
  }
  const card = h("section", { class: "card" }, h("div", { class: "card-head" }, h("div", {}, h("h2", { text: title }), sub ? h("div", { class: "card-sub", text: sub }) : null),
    h("span", { class: "spacer" }), tools || null, table ? h("div", { class: "seg small chart-tools", role: "group", "aria-label": "View" }, btnChart, btnTable) : null), body, tbl);
  let lastW = 0, raf = 0;
  const ro = new ResizeObserver(() => {
    const w = body.clientWidth;
    if (mode !== "chart" || !w || Math.abs(w - lastW) < 2) return;
    lastW = w; cancelAnimationFrame(raf); raf = requestAnimationFrame(() => draw(body));
  });
  requestAnimationFrame(() => { draw(body); lastW = body.clientWidth; ro.observe(body); });
  return card;
}

// Shared UI helpers.  SECURITY RULE: text that comes from a bank statement (merchant names, narrations) is attacker-influenced
// ("Pay to <img onerror=...>").  Everything here puts text into the page with textContent / createTextNode - never innerHTML.

export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

function appendKids(el, kids) {
  for (const k of kids.flat(Infinity)) {
    if (k == null || k === false) continue;
    el.append(k instanceof Node ? k : document.createTextNode(String(k)));
  }
}

/** h("div", {class:"x", onclick: fn}, "text", childNode) */
export function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "text") el.textContent = v;
    else if (k === "style" && typeof v === "object") Object.assign(el.style, v);   // CSSOM is allowed by our CSP; style="" attributes are not
    else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
    else if (k === "dataset") Object.assign(el.dataset, v);
    else if (v === true) el.setAttribute(k, "");
    else el.setAttribute(k, String(v));
  }
  appendKids(el, kids);
  return el;
}

const SVG_NS = "http://www.w3.org/2000/svg";
export function svg(tag, attrs, ...kids) {
  const el = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === "text") el.textContent = v;
    else if (k === "style" && typeof v === "object") Object.assign(el.style, v);
    else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, String(v));
  }
  appendKids(el, kids);
  return el;
}

// ---- formatting ------------------------------------------------------------------------------------------------------------
export function inr(x, d = 0) {
  if (x == null || Number.isNaN(x)) return "–";
  const neg = x < 0;
  const s = Math.abs(x).toLocaleString("en-IN", { minimumFractionDigits: d, maximumFractionDigits: d });
  return (neg ? "−" : "") + "₹" + s;
}
export function inrAuto(x) { return inr(x, Math.abs(x) >= 100 || Number.isInteger(x) ? 0 : 2); }
export function inrC(x) {   // compact, Indian style: 1.2K, 3.4L, 1.1Cr
  const a = Math.abs(x), sign = x < 0 ? "−" : "";
  const f = (v) => v.toFixed(v >= 10 ? 0 : 1).replace(/\.0$/, "");
  if (a >= 1e7) return `${sign}₹${f(a / 1e7)}Cr`;
  if (a >= 1e5) return `${sign}₹${f(a / 1e5)}L`;
  if (a >= 1e3) return `${sign}₹${f(a / 1e3)}K`;
  return `${sign}₹${Math.round(a)}`;
}
const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
export function dmy(iso, withYear = false) {
  if (!iso) return "";
  const [y, m, d] = iso.slice(0, 10).split("-").map(Number);
  return `${d} ${MON[m - 1]}${withYear ? " " + y : ""}`;
}
export function monthLabel(ym) { const [y, m] = ym.split("-").map(Number); return `${MON[m - 1]} ${String(y).slice(2)}`; }
export const pct = (x, d = 0) => (x == null ? "–" : `${(x * 100).toFixed(d)}%`);
export const plural = (n, one, many) => `${n} ${n === 1 ? one : many || one + "s"}`;

// ---- icons (simple stroke icons, 24x24) ------------------------------------------------------------------------------------------
const ICONS = {
  dashboard: "M4 13h6V4H4zM14 20h6V4h-6zM4 20h6v-3H4z",
  list: "M8 6h12M8 12h12M8 18h12M4 6h.01M4 12h.01M4 18h.01",
  review: "M12 3l2.4 5 5.6.8-4 3.9.9 5.5L12 15.6 7.1 18.2l.9-5.5-4-3.9L9.6 8z",
  store: "M4 9l1-5h14l1 5M5 9v10h14V9M9 19v-6h6v6",
  repeat: "M17 2l4 4-4 4M3 11V9a3 3 0 013-3h15M7 22l-4-4 4-4M21 13v2a3 3 0 01-3 3H3",
  chat: "M21 12a8 8 0 01-11.6 7.1L4 20l1-4.6A8 8 0 1121 12z",
  upload: "M12 16V4M7 9l5-5 5 5M4 20h16",
  brain: "M9 4a3 3 0 00-3 3 3 3 0 00-2 5 3 3 0 002 5 3 3 0 006 0V4a3 3 0 00-3 0zM15 4a3 3 0 013 3 3 3 0 012 5 3 3 0 01-2 5 3 3 0 01-6 0",
  flask: "M9 3h6M10 3v6l-5 9a2 2 0 002 3h10a2 2 0 002-3l-5-9V3M8 14h8",
  book: "M4 5a2 2 0 012-2h13v16H6a2 2 0 00-2 2zM4 19a2 2 0 012-2h13",
  sun: "M12 17a5 5 0 100-10 5 5 0 000 10zM12 1v2M12 21v2M4.2 4.2l1.4 1.4M18.4 18.4l1.4 1.4M1 12h2M21 12h2M4.2 19.8l1.4-1.4M18.4 5.6l1.4-1.4",
  moon: "M21 12.8A9 9 0 1111.2 3a7 7 0 009.8 9.8z",
  menu: "M4 6h16M4 12h16M4 18h16",
  trend: "M3 17l6-6 4 4 8-8M15 7h6v6",
  alert: "M12 9v4M12 17h.01M10.3 3.9L2.4 18a2 2 0 001.7 3h15.8a2 2 0 001.7-3L13.7 3.9a2 2 0 00-3.4 0z",
  check: "M5 12l5 5L20 7",
  up: "M12 19V5M5 12l7-7 7 7",
  down: "M12 5v14M19 12l-7 7-7-7",
  info: "M12 8h.01M11 12h1v5h1M12 22a10 10 0 100-20 10 10 0 000 20z",
  coffee: "M5 8h12v6a5 5 0 01-5 5H10a5 5 0 01-5-5zM17 9h2a2 2 0 010 4h-2M8 2v2M12 2v2",
  users: "M17 21v-2a4 4 0 00-4-4H7a4 4 0 00-4 4v2M10 11a4 4 0 100-8 4 4 0 000 8M23 21v-2a4 4 0 00-3-3.9M16 3.1a4 4 0 010 7.8",
  x: "M6 6l12 12M18 6L6 18",
  user: "M20 21v-2a4 4 0 00-4-4H8a4 4 0 00-4 4v2M12 11a4 4 0 100-8 4 4 0 000 8",
  chevron: "M6 9l6 6 6-6",
};
export function icon(name, attrs = {}) {
  return svg("svg", { viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", "stroke-width": "1.8", "stroke-linecap": "round", "stroke-linejoin": "round",
                      "aria-hidden": "true", ...attrs }, svg("path", { d: ICONS[name] || ICONS.info }));
}

// ---- toast ----------------------------------------------------------------------------------------------------------------------------------
export function toast(message, opts = {}) {
  const box = $("#toasts");
  const node = h("div", { class: "toast", role: "status" }, message, opts.link ? [" ", h("a", { href: opts.link.href }, opts.link.text)] : null);
  box.append(node);
  setTimeout(() => node.remove(), opts.ms || 5200);
  return node;
}

// ---- drawer / modal ---------------------------------------------------------------------------------------------------------------------------
let drawerState = null;
export function closeDrawer() {
  if (!drawerState) return;
  drawerState.scrim.remove(); drawerState.el.remove();
  document.removeEventListener("keydown", drawerState.onKey);
  drawerState.opener?.focus?.();
  drawerState = null;
}
export function openDrawer(title, build) {
  closeDrawer();
  const body = h("div", { class: "drawer-body" });
  const closeBtn = h("button", { class: "btn ghost small", "aria-label": "Close", onclick: closeDrawer }, icon("x", { width: 16, height: 16 }));
  const el = h("aside", { class: "drawer", role: "dialog", "aria-modal": "true", "aria-label": title }, h("div", { class: "drawer-head" }, h("h2", { text: title }), h("span", { class: "spacer" }), closeBtn), body);
  const scrim = h("div", { class: "scrim", onclick: closeDrawer });
  const onKey = (e) => { if (e.key === "Escape") closeDrawer(); };
  document.addEventListener("keydown", onKey);
  document.body.append(scrim, el);
  drawerState = { el, scrim, onKey, opener: document.activeElement };
  closeBtn.focus();
  Promise.resolve(build(body, el.querySelector("h2"))).catch((e) => body.replaceChildren(h("p", { class: "muted", text: e.message })));
  return { body, close: closeDrawer };
}

export function modal({ title, body, actions }) {
  return new Promise((resolve) => {
    const done = (v) => { wrap.remove(); document.removeEventListener("keydown", onKey); resolve(v); };
    const onKey = (e) => { if (e.key === "Escape") done(null); };
    const box = h("div", { class: "modal-box", role: "dialog", "aria-modal": "true", "aria-label": title }, h("h2", { text: title }), body,
                  h("div", { class: "row", style: { justifyContent: "flex-end", marginTop: "16px" } }, actions.map((a) => h("button", { class: `btn ${a.cls || ""}`, onclick: () => done(a.value) }, a.label))));
    const wrap = h("div", { class: "modal" }, h("div", { class: "scrim", onclick: () => done(null) }), box);
    document.addEventListener("keydown", onKey);
    document.body.append(wrap);
    box.querySelector("button")?.focus();
  });
}
export async function confirmDialog(title, text, confirmLabel = "Confirm", danger = false) {
  const r = await modal({ title, body: h("p", { class: "ink2", text }), actions: [{ label: "Cancel", value: false }, { label: confirmLabel, value: true, cls: danger ? "danger" : "primary" }] });
  return r === true;
}

// ---- popup menu --------------------------------------------------------------------------------------------------------------------------------
let openMenu = null;
export function closeMenu() { if (openMenu) { openMenu.el.remove(); document.removeEventListener("pointerdown", openMenu.onDown, true); document.removeEventListener("keydown", openMenu.onKey); openMenu = null; } }
/** items: [{group:"Expenses"} | {label, value, checked}]; resolves with the picked value or null */
export function popMenu(anchor, items) {
  closeMenu();
  return new Promise((resolve) => {
    const el = h("div", { class: "menu", role: "menu" });
    for (const it of items) {
      if (it.group) { el.append(h("div", { class: "grp", text: it.group })); continue; }
      el.append(h("button", { role: "menuitemradio", "aria-checked": it.checked ? "true" : "false", onclick: () => { closeMenu(); resolve(it.value); } },
                  it.color ? h("span", { class: "dot", style: { background: it.color } }) : null, it.label));
    }
    document.body.append(el);
    const r = anchor.getBoundingClientRect(), mw = el.offsetWidth, mh = el.offsetHeight;
    el.style.left = Math.max(8, Math.min(r.left, innerWidth - mw - 8)) + "px";
    el.style.top = (r.bottom + mh + 12 > innerHeight ? Math.max(8, r.top - mh - 4) : r.bottom + 4) + "px";
    const onDown = (e) => { if (!el.contains(e.target)) { closeMenu(); resolve(null); } };
    const onKey = (e) => { if (e.key === "Escape") { closeMenu(); resolve(null); } };
    document.addEventListener("pointerdown", onDown, true);
    document.addEventListener("keydown", onKey);
    openMenu = { el, onDown, onKey };
    (el.querySelector('[aria-checked="true"]') || el.querySelector("button"))?.focus();
  });
}

// ---- tooltip (one shared element; rows built with textContent) ---------------------------------------------------------------------------------------
let tipEl = null;
export function showTip(nodes, evt) {
  if (!tipEl) { tipEl = h("div", { class: "tip", role: "tooltip" }); document.body.append(tipEl); }
  tipEl.replaceChildren(...nodes);
  tipEl.classList.remove("hidden");
  const pad = 14, w = tipEl.offsetWidth, hgt = tipEl.offsetHeight;
  let x = evt.clientX + pad, y = evt.clientY + pad;
  if (x + w > innerWidth - 8) x = evt.clientX - w - pad;
  if (y + hgt > innerHeight - 8) y = evt.clientY - hgt - pad;
  tipEl.style.left = Math.max(8, x) + "px"; tipEl.style.top = Math.max(8, y) + "px";
}
export function hideTip() { tipEl?.classList.add("hidden"); }
export function tipRow(label, value, color) {
  return h("div", { class: "t-row" }, h("span", { class: "t-key" }, color ? h("i", { style: { background: color } }) : null, label), h("b", { text: value }));
}
/** Attach hover + keyboard-focus tooltip to an element. build() returns an array of nodes. */
export function bindTip(el, build) {
  const show = (e) => showTip(build(), e.clientX !== undefined && e.type !== "focus" ? e : rectEvt(el));
  el.addEventListener("pointermove", show); el.addEventListener("pointerenter", show); el.addEventListener("pointerleave", hideTip);
  el.addEventListener("focus", show); el.addEventListener("blur", hideTip);
}
function rectEvt(el) { const r = el.getBoundingClientRect(); return { clientX: r.left + r.width / 2, clientY: r.top }; }

// ---- small components -------------------------------------------------------------------------------------------------------------------------------------
export function chip(text, cls = "", ico = null) { return h("span", { class: `chip ${cls}` }, ico ? icon(ico) : null, text); }
export function badgeKind(kind, isLocal) {
  const t = { merchant: isLocal ? "Local shop" : "Business", person: "Person", cash: "Cash", bank: "Bank", self: "Own wallet" }[kind] || kind;
  return chip(t, kind === "person" ? "" : "accent");
}
export function loadingLine(text = "Loading…") { return h("div", { class: "row muted", style: { padding: "24px" } }, h("span", { class: "spin" }), text); }
export function emptyState(title, text, ...actions) { return h("div", { class: "empty card" }, h("h2", { text: title }), h("p", { text }), h("div", { class: "row", style: { justifyContent: "center" } }, actions)); }
export function download(url) { const a = h("a", { href: url, download: "" }); document.body.append(a); a.click(); a.remove(); }
export function debounce(fn, ms) { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; }
export function store(key, value) {   // localStorage can be blocked: never let it break the app
  try { if (value === undefined) return JSON.parse(localStorage.getItem(key)); localStorage.setItem(key, JSON.stringify(value)); } catch (e) { return null; }
  return value;
}

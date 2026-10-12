// App shell: sidebar, router, theme, and the "learning…" banner.
import { h, svg, $, icon, store, closeDrawer, toast, loadingLine, popMenu } from "./ui.js";
import { state, onState, refreshStatus, loadTaxonomy, loadProfiles, switchProfile } from "./state.js";

const NAV = [
  ["dashboard", "Dashboard", "dashboard"], ["transactions", "Transactions", "list"], ["review", "Review", "review"], ["merchants", "Merchants", "store"],
  ["patterns", "Patterns", "repeat"], ["ask", "Ask", "chat"], ["import", "Import", "upload"], ["model", "Learning", "brain"], ["how", "Under the hood", "flask"],
  ["profile", "Profile", "user"],
];
const loaders = {
  dashboard: () => import("./pages/dashboard.js"), transactions: () => import("./pages/transactions.js"), review: () => import("./pages/review.js"),
  merchants: () => import("./pages/merchants.js"), patterns: () => import("./pages/patterns.js"), ask: () => import("./pages/ask.js"),
  import: () => import("./pages/importpage.js"), model: () => import("./pages/model.js"), how: () => import("./pages/how.js"),
  profile: () => import("./pages/profile.js"),
};

let current = null;       // {name, mod, root, params}
const main = h("main", { class: "main", id: "main", tabindex: "-1" });
const side = h("nav", { class: "side", "aria-label": "Main" });
const banner = h("div", { class: "banner hidden", role: "status" });
const badge = {};
const who = h("button", { class: "who", "aria-label": "Switch profile", "aria-haspopup": "menu", onclick: pickProfile });

async function pickProfile(e) {
  const items = [{ group: "Switch profile" }, ...state.profiles.map((p) => ({ label: p.name, value: p.id, checked: p.id === state.profile?.id })),
                 { group: "" }, { label: "Manage profiles…", value: "__manage" }];
  const v = await popMenu(e.currentTarget, items);
  if (v === "__manage") location.hash = "#/profile";
  else if (v) { try { await switchProfile(v); } catch (er) { toast(er.message); } }
}

function themeControl() {
  const mode = store("hisaab-theme") || "auto";
  const apply = (m) => {
    store("hisaab-theme", m);
    if (m === "auto") document.documentElement.removeAttribute("data-theme"); else document.documentElement.setAttribute("data-theme", m);
    for (const [k, b] of Object.entries(btns)) b.setAttribute("aria-pressed", String(k === m));
  };
  const btns = {
    auto: h("button", { title: "Follow system theme", onclick: () => apply("auto") }, "Auto"),
    light: h("button", { title: "Light theme", "aria-label": "Light theme", onclick: () => apply("light") }, icon("sun", { width: 14, height: 14 })),
    dark: h("button", { title: "Dark theme", "aria-label": "Dark theme", onclick: () => apply("dark") }, icon("moon", { width: 14, height: 14 })),
  };
  const seg = h("div", { class: "seg small", role: "group", "aria-label": "Theme" }, ...Object.values(btns));
  for (const [k, b] of Object.entries(btns)) b.setAttribute("aria-pressed", String(k === mode));
  return seg;
}

function buildShell() {
  const logo = svg("svg", { viewBox: "0 0 32 32", "aria-hidden": "true" },
    svg("rect", { width: 32, height: 32, rx: 8, style: { fill: "var(--accent)" } }),
    svg("path", { d: "M9 22V10m7 12V14m7 8V8", fill: "none", "stroke-width": 3, "stroke-linecap": "round", style: { stroke: "#fff" } }));
  const brand = h("div", { class: "brand" }, logo, h("div", {}, "Hisaab", h("small", { text: "your UPI money, explained" })));
  side.append(brand, who);
  const links = h("div", { class: "nav" });
  for (const [name, label, ico] of NAV) {
    const a = h("a", { href: `#/${name}`, "data-page": name }, icon(ico), label);
    if (name === "review") { badge.review = h("span", { class: "count hidden" }); a.append(badge.review); }
    links.append(a);
  }
  side.append(links, h("div", { class: "foot" }, themeControl(), h("span", { text: "Your data stays on this computer." })));
  const menuBtn = h("button", { class: "btn small menu-btn", "aria-label": "Menu", onclick: () => side.classList.toggle("open") }, icon("menu", { width: 18, height: 18 }), "Menu");
  document.getElementById("app").replaceChildren(h("div", { class: "shell" }, side, h("div", {}, h("div", { class: "main", style: { paddingBottom: 0, paddingTop: "10px" } }, menuBtn, banner), main)));
}

onState(() => {
  const p = state.profile;
  if (p) who.replaceChildren(h("span", { class: "av", text: p.name.trim().charAt(0).toUpperCase() || "?" }),
                             h("span", { class: "nm" }, h("small", { text: "Profile" }), p.name), icon("chevron", { width: 16, height: 16 }));
  banner.classList.toggle("hidden", !state.learning);
  if (state.learning) banner.replaceChildren(h("span", { class: "spin" }), "Learning from your corrections…");
  if (badge.review) { badge.review.textContent = state.reviewCount; badge.review.classList.toggle("hidden", !state.reviewCount); }
});

function parseHash() {
  const raw = location.hash.replace(/^#\/?/, "") || "dashboard";
  const [path, query = ""] = raw.split("?");
  return { name: path.split("/")[0] in loaders ? path.split("/")[0] : "dashboard", params: Object.fromEntries(new URLSearchParams(query)) };
}

async function route() {
  closeDrawer();
  side.classList.remove("open");
  const { name, params } = parseHash();
  for (const a of side.querySelectorAll("[data-page]")) { if (a.dataset.page === name) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current"); }
  main.replaceChildren(loadingLine());
  try {
    await loadTaxonomy();
    const mod = await loaders[name]();
    const root = h("div", { class: "page" });
    main.replaceChildren(root);
    current = { name, mod, root, params };
    await mod.render(root, params);
    document.title = `${NAV.find((n) => n[0] === name)[1]} · Hisaab`;
  } catch (e) {
    main.replaceChildren(h("div", { class: "card" }, h("h2", { text: "Something went wrong" }), h("p", { class: "muted", text: e.message })));
  }
}

window.addEventListener("hashchange", route);
window.addEventListener("hisaab:learned", () => {
  if (current && !current.mod.keepOnLearn) current.mod.render(current.root, current.params);
});
window.addEventListener("hisaab:data", async () => { await refreshStatus(); route(); });

buildShell();
Promise.all([loadProfiles(), refreshStatus()]).catch(() => {}).finally(route);

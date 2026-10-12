import * as api from "../api.js";
import { h, inrAuto, dmy, plural, debounce, badgeKind, chip } from "../ui.js";
import { state } from "../state.js";
import { chooseCategory } from "./../teach.js";
import { noData, catPill, openMerchant } from "./common.js";

export async function render(root) {
  if (!state.status?.transactions) return noData(root);
  const all = await api.get("/api/merchants");
  const f = { q: "", kind: "", local: false, review: false, sort: "total" };
  const out = h("div", {});
  const search = h("input", { class: "input", type: "search", placeholder: "Search merchants", "aria-label": "Search merchants", oninput: debounce((e) => { f.q = e.target.value.toLowerCase(); draw(); }, 150) });
  const kind = h("select", { class: "select", "aria-label": "Kind", onchange: (e) => { f.kind = e.target.value; draw(); } }, h("option", { value: "", text: "Everyone" }),
    Object.entries(state.tax.kinds).map(([k, t]) => h("option", { value: k, text: t })));
  const loc = h("label", { class: "row", style: { gap: "6px" } }, h("input", { type: "checkbox", onchange: (e) => { f.local = e.target.checked; draw(); } }), "Local shops only");
  const rev = h("label", { class: "row", style: { gap: "6px" } }, h("input", { type: "checkbox", onchange: (e) => { f.review = e.target.checked; draw(); } }), "Needs review");
  root.replaceChildren(h("div", { class: "page-head" }, h("div", {}, h("h1", { text: "Merchants" }), h("div", { class: "sub", text: "Every shop, company and person the statement mentions — merged when they are the same. Click one for its story." }))),
    h("div", { class: "filters" }, search, kind, loc, rev, h("span", { class: "spacer" }), h("span", { class: "muted", id: "mcount" })), out);

  function draw() {
    let rows = all.filter((m) => (!f.q || m.name.toLowerCase().includes(f.q)) && (!f.kind || m.kind === f.kind) && (!f.local || (m.is_local && m.kind === "merchant")) &&
                                (!f.review || ["unknown", "suggested"].includes(m.cat_status)));
    document.getElementById("mcount").textContent = plural(rows.length, "merchant");
    const body = rows.map((m) => h("tr", { class: "click", onclick: () => openMerchant(m.id) },
      h("td", {}, h("div", { class: "m-name", text: m.name }), h("div", { class: "m-sub row", style: { gap: "6px" } }, badgeKind(m.kind, m.is_local), m.kind_source === "user" ? chip("you set this", "good", "check") : null, m.placeholder ? chip("no name on statement", "warn") : null)),
      h("td", {}, catPill({ category: m.category, cat_status: m.cat_status }, async (a) => {
        const ok = await chooseCategory(a, { merchantId: m.id, merchantName: m.name, direction: m.spend >= m.received ? "debit" : "credit", current: m.category });
        if (ok) { Object.assign(m, { category: ok.category || m.category, cat_status: "confirmed" }); draw(); }
      })),
      h("td", { class: "right num", text: String(m.days) }), h("td", { class: "right num", text: String(m.n) }),
      h("td", { class: "right num", text: m.spend ? inrAuto(m.spend) : "–" }), h("td", { class: "right num amt credit", text: m.received ? "+" + inrAuto(m.received) : "" }),
      h("td", { class: "nowrap muted", text: dmy(m.last, true) })));
    out.replaceChildren(h("div", { class: "tbl-wrap" }, h("table", { class: "tbl" }, h("thead", {}, h("tr", {}, ["Merchant", "Category", "Days", "Payments", "Spent", "Received", "Last"].map((c, i) => h("th", { class: i >= 2 && i <= 5 ? "right" : "", text: c })))),
      h("tbody", {}, body.length ? body : h("tr", {}, h("td", { colspan: "7", class: "muted", style: { padding: "28px", textAlign: "center" }, text: "No merchants match." }))))));
  }
  draw();
}

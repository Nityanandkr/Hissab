import * as api from "../api.js";
import { h, icon, inrAuto, dmy, plural, loadingLine, debounce, download, chip, badgeKind } from "../ui.js";
import { state } from "../state.js";
import { chooseCategory } from "../teach.js";
import { noData, catPill, openExplain, openMerchant } from "./common.js";

export async function render(root, params) {
  if (!state.status?.transactions) return noData(root);
  const f = { q: "", category: "", kind: "", direction: "", needs_review: "", start: "", end: "", min: "", max: "", merchant_id: "", sort: "date", order: "desc", page: 1, ...params };
  const tax = state.tax;
  const body = h("div", { class: "stack" });
  const total = h("span", { class: "muted" });
  const input = (k, label, extra = {}) => h("input", { class: "input", placeholder: label, "aria-label": label, value: f[k], ...extra, oninput: debounce((e) => { f[k] = e.target.value; f.page = 1; load(); }, 250) });
  const select = (k, label, opts) => h("select", { class: "select", "aria-label": label, onchange: (e) => { f[k] = e.target.value; f.page = 1; load(); } },
    h("option", { value: "", text: label }), opts.map(([v, t]) => h("option", { value: v, selected: f[k] === v ? "" : null, text: t })));
  const bar = h("div", { class: "filters" },
    input("q", "Search merchant, category or narration", { style: { minWidth: "260px" }, type: "search" }),
    select("category", "All categories", [...tax.categories.map((c) => [c.name, c.name]), [tax.uncategorised, tax.uncategorised]]),
    select("kind", "Shops & people", Object.entries(tax.kinds)), select("direction", "In & out", [["debit", "Money out"], ["credit", "Money in"]]),
    h("label", { class: "row", style: { gap: "6px" } }, h("input", { type: "checkbox", checked: f.needs_review === "1" || f.needs_review === 1 ? "" : null, onchange: (e) => { f.needs_review = e.target.checked ? "1" : ""; f.page = 1; load(); } }), "Needs review"),
    input("start", "From", { type: "date", title: "From date" }), input("end", "To", { type: "date", title: "To date" }),
    input("min", "Min ₹", { type: "number", style: { width: "92px" } }), input("max", "Max ₹", { type: "number", style: { width: "92px" } }),
    h("span", { class: "spacer" }), total,
    h("button", { class: "btn small", onclick: () => download("/api/export/transactions.csv") }, icon("upload", { width: 14, height: 14 }), "Export CSV"));
  root.replaceChildren(h("div", { class: "page-head" }, h("div", {}, h("h1", { text: "Transactions" }), h("div", { class: "sub", text: "Click a category to correct it. Click a row to see exactly how it was understood." }))), bar, body);

  async function load() {
    body.classList.add("loading");
    const q = new URLSearchParams();
    for (const [k, v] of Object.entries(f)) if (v !== "" && v != null) q.set(k, v);
    const d = await api.get("/api/transactions?" + q);
    body.classList.remove("loading");
    total.textContent = `${plural(d.total, "transaction")} · out ${inrAuto(d.sum_debit)} · in ${inrAuto(d.sum_credit)}`;
    const th = (k, label, right) => h("th", { class: right ? "right" : "", style: { cursor: "pointer" }, onclick: () => { f.order = f.sort === k && f.order === "desc" ? "asc" : "desc"; f.sort = k; load(); } },
      label, f.sort === k ? (f.order === "desc" ? " ↓" : " ↑") : "");
    const rows = d.rows.map((t) => h("tr", { class: "click", onclick: () => openExplain(t.id) },
      h("td", { class: "nowrap" }, dmy(t.date, true), t.time ? h("div", { class: "m-sub", text: t.time }) : null),
      h("td", {}, h("a", { href: "#", class: "m-name", text: t.merchant, onclick: (e) => { e.preventDefault(); e.stopPropagation(); openMerchant(t.merchant_id); } }),
        h("div", { class: "m-sub row", style: { gap: "6px" } }, badgeKind(t.kind, t.is_local), t.mode && t.mode !== "UPI" ? h("span", { text: t.mode }) : null)),
      h("td", {}, catPill(t, async (anchor) => { const ok = await chooseCategory(anchor, { txn: t.id, merchantId: t.merchant_id, merchantName: t.merchant, direction: t.direction, current: t.category }); if (ok) load(); })),
      h("td", { class: `right amt ${t.direction}`, text: (t.direction === "credit" ? "+" : "") + inrAuto(t.amount) })));
    const tbl = h("div", { class: "tbl-wrap" }, h("table", { class: "tbl" }, h("thead", {}, h("tr", {}, th("date", "Date"), th("merchant", "Merchant"), th("category", "Category"), th("amount", "Amount", true))),
      h("tbody", {}, rows.length ? rows : h("tr", {}, h("td", { colspan: "4", class: "muted", style: { padding: "28px", textAlign: "center" }, text: "Nothing matches these filters." })))));
    const pager = d.pages > 1 ? h("div", { class: "pager" }, h("button", { class: "btn small", disabled: d.page <= 1 ? "" : null, onclick: () => { f.page = d.page - 1; load(); } }, "← Previous"),
      h("span", { class: "muted", text: `Page ${d.page} of ${d.pages}` }), h("button", { class: "btn small", disabled: d.page >= d.pages ? "" : null, onclick: () => { f.page = d.page + 1; load(); } }, "Next →")) : null;
    body.replaceChildren(tbl, pager);
  }
  await load();
}

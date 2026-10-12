// Pieces several pages share: the "no data yet" state, merchant drawer, transaction explain drawer.
import * as api from "../api.js";
import { h, icon, inr, inrAuto, dmy, pct, chip, badgeKind, openDrawer, emptyState, toast, plural } from "../ui.js";
import { chooseCategory, chooseKind, renameMerchant, mergeMerchant } from "../teach.js";
import { spark } from "../charts.js";

export function noData(root) {
  root.replaceChildren(emptyState("No transactions yet",
    "Load the built-in demo (a fictional student's last four months) to explore everything, or import your own bank / UPI statement. Your data never leaves this computer.",
    h("button", { class: "btn primary", onclick: loadDemo }, "Load demo data"),
    h("a", { class: "btn", href: "#/import" }, "Import my statement")));
}
export async function loadDemo(ev) {
  const b = ev?.currentTarget; if (b) { b.disabled = true; b.textContent = "Loading… (up to a minute on a slow server)"; }
  try {
    const r = await api.post("/api/demo/load");
    // a second click finds every row already stored: say so instead of "Loaded 0", which reads like a failure
    toast(r.new ? `Loaded ${r.new} demo transactions.` : "The demo data is already loaded.");
    location.hash = "#/dashboard";
    window.dispatchEvent(new CustomEvent("hisaab:data"));
  }
  catch (e) { toast(e.message); if (b) { b.disabled = false; b.textContent = "Load demo data"; } }
}

export function catPill(t, onPick) {
  const status = t.cat_status;
  const label = t.category;
  const tag = status === "confirmed" ? "set by you" : status === "auto" ? "automatic" : status === "suggested" ? "best guess — please confirm" : "needs review";
  return h("button", { class: `catpill ${status}`, title: `${label} · ${tag}`, onclick: (e) => { e.stopPropagation(); onPick?.(e.currentTarget); } },
    h("span", { class: "t", text: label }), status === "confirmed" ? icon("check", { width: 12, height: 12 }) : status === "suggested" ? h("span", { class: "muted", text: "?" }) : null);
}

function statusChip(t) {
  const m = { confirmed: ["Set by you", "good", "check"], auto: ["Automatic", "", null], suggested: ["Best guess", "warn", "info"], unknown: ["Needs review", "warn", "alert"] }[t.cat_status] || ["", "", null];
  return chip(m[0], m[1], m[2]);
}

// ---- explain a transaction ---------------------------------------------------------------------------------------------------------------------------------
export function openExplain(txnId) {
  openDrawer("Why is it like this?", async (body, title) => {
    body.append(h("div", { class: "row muted" }, h("span", { class: "spin" }), "Working it out…"));
    const d = await api.get(`/api/transactions/${txnId}`);
    const t = d.txn;
    title.textContent = t.merchant;
    const head = h("div", { class: "stack" },
      h("div", { class: "row wrap" }, h("span", { class: `amt ${t.direction}`, style: { fontSize: "26px", fontWeight: 650 }, text: (t.direction === "credit" ? "+" : "") + inrAuto(t.amount) }),
        badgeKind(t.kind, t.is_local), statusChip(t), h("span", { class: "muted", text: `${dmy(t.date, true)} ${t.time}` })),
      h("div", { class: "sample", text: t.description }));
    const pipe = h("div", { class: "pipe" });
    d.stages.forEach((st, i) => {
      const dl = h("dl", { class: "kv" });
      const kv = (k, v) => { if (v != null && v !== "" && v !== false) dl.append(h("dt", { text: k }), h("dd", { text: String(v) })); };
      if (st.key === "read") { const x = st.data; kv("Payment type", x.mode); kv("Bank marker", x.marker); kv("Reference", x.rrn); kv("UPI address", x.vpa); kv("App", x.app); kv("Payee text", x.payee_raw); kv("Typed remark", x.remarks); kv("Layout", x.template); kv("Parser confidence", x.confidence); }
      if (st.key === "merchant") { kv("Match method", st.data.match_method); kv("Match confidence", st.data.match_confidence); kv("Known brand", st.data.is_brand ? "yes" : "no"); }
      if (st.key === "kind") { kv("Probability it is a person", pct(st.data.p_person)); kv("Decided by", st.data.source); }
      if (st.key === "category") {
        kv("Decided by", { user: "you", rule: "a fixed rule", brand: "the brand list", model: "the learned model", lexicon: "shop-type words", none: "—" }[st.data.source] || st.data.source);
        kv("Confidence", st.data.confidence != null ? pct(st.data.confidence) : null);
      }
      const top3 = st.key === "category" && st.data.top3?.length ? h("div", { class: "stack", style: { gap: "4px", marginTop: "8px" } },
        h("div", { class: "muted", text: "Top guesses" }), ...st.data.top3.map(([n, p]) => h("div", { class: "bar2" }, h("span", { style: { width: "150px" }, text: n }), h("span", { class: "b", style: { width: `${Math.max(2, p * 160)}px` } }), pct(p)))) : null;
      pipe.append(h("div", { class: "st" }, h("div", { class: "no", text: String(i + 1) }), h("h3", { text: st.title.replace(/^\d · /, "") }), h("div", { class: "sum", text: st.summary }),
        h("ul", { class: "notes" }, (st.notes || []).map((n) => h("li", { text: n }))), dl, top3));
    });
    const actions = h("div", { class: "row wrap", style: { marginTop: "6px" } },
      h("button", { class: "btn primary", onclick: async (e) => { if (await chooseCategory(e.currentTarget, { txn: t.id, merchantId: t.merchant_id, merchantName: t.merchant, direction: t.direction, current: t.category })) openExplain(t.id); } }, "Change category"),
      h("button", { class: "btn", onclick: () => openMerchant(t.merchant_id) }, "Open merchant"));
    body.replaceChildren(head, h("div", { style: { height: "14px" } }), pipe, actions);
  });
}

// ---- merchant drawer ----------------------------------------------------------------------------------------------------------------------------------------------
export function openMerchant(mid) {
  openDrawer("Merchant", async (body, title) => {
    body.append(h("div", { class: "row muted" }, h("span", { class: "spin" }), "Loading…"));
    const [m, all] = await Promise.all([api.get(`/api/merchants/${mid}`), api.get("/api/merchants")]);
    title.textContent = m.name;
    const re = () => openMerchant(mid);
    const stats = h("div", { class: "stat-list" },
      ...[["Spent", inr(m.spend)], ["Received", inr(m.received)], ["Payments", m.n], ["Kind", { merchant: m.is_local ? "Local shop" : "Business", person: "Person", cash: "ATM / cash", bank: "Bank", self: "Own wallet" }[m.kind]]]
        .map(([k, v]) => h("div", {}, h("div", { class: "muted", text: k }), h("div", { style: { fontSize: "18px", fontWeight: 600 }, text: String(v) }))));
    const cats = m.categories.map((c) => `${c.category} ×${c.count}`).join(" · ");
    const actions = h("div", { class: "row wrap" },
      h("button", { class: "btn primary", onclick: async (e) => { const dir = m.spend >= m.received ? "debit" : "credit"; if (await chooseCategory(e.currentTarget, { merchantId: m.id, merchantName: m.name, direction: dir, current: m.categories[0]?.category })) re(); } }, "Set category"),
      h("button", { class: "btn", onclick: async () => { if (await chooseKind(m.id, m.name, m.kind)) re(); } }, "Shop or person?"),
      h("button", { class: "btn", onclick: async () => { if (await renameMerchant(m.id, m.name)) re(); } }, "Rename"),
      h("button", { class: "btn", onclick: async () => { if (await mergeMerchant(m.id, m.name, all)) re(); } }, "Merge duplicate"));
    const rec = m.recurring.length ? h("p", { class: "ink2" }, `Repeats ${m.recurring[0].period}, about ${inr(m.recurring[0].last_amount)}; next expected ${dmy(m.recurring[0].next_expected, true)} (${m.recurring[0].status}).`) : null;
    const tbl = h("div", { class: "tbl-wrap" }, h("table", { class: "tbl" }, h("tbody", {}, m.txns.slice(0, 25).map((t) => h("tr", { class: "click", onclick: () => openExplain(t.id) },
      h("td", { class: "nowrap", text: dmy(t.date) }), h("td", { text: t.category }), h("td", { class: `right amt ${t.direction}`, text: (t.direction === "credit" ? "+" : "") + inrAuto(t.amount) }))))));
    body.replaceChildren(stats, h("div", { style: { height: "10px" } }), actions,
      h("div", { style: { margin: "14px 0" } }, h("div", { class: "muted", text: "Monthly spend" }), spark(m.monthly.map((x) => x.amount), { w: 220, hgt: 36 })),
      rec, h("dl", { class: "kv" }, h("dt", { text: "Categories seen" }), h("dd", { text: cats }),
        h("dt", { text: "Names on statements" }), h("dd", { text: m.names_seen.map((n) => `${n.name} ×${n.count}`).join(", ") || "—" }),
        h("dt", { text: "UPI addresses" }), h("dd", { text: m.upi_addresses.join(", ") || "—" }),
        h("dt", { text: "Your rules" }), h("dd", { text: m.rules.map((r) => `${r.direction === "credit" ? "credits" : "payments"} → ${r.category}`).join("; ") || "none yet" })),
      h("h3", { style: { margin: "16px 0 8px" }, text: "Recent payments" }), tbl);
  });
}

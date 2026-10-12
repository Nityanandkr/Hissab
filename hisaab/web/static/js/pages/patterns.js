import * as api from "../api.js";
import { h, icon, inr, inrAuto, dmy, plural, chip } from "../ui.js";
import { state } from "../state.js";
import { hbars } from "../charts.js";
import { noData, openExplain, openMerchant } from "./common.js";

const ord = (n) => { n = Number(n); const v = n % 100; return n + (["th", "st", "nd", "rd"][(v - 20) % 10] || ["th", "st", "nd", "rd"][v] || "th"); };
const PERIOD = { weekly: "every week", fortnightly: "every 2 weeks", monthly: "every month", quarterly: "every 3 months", yearly: "every year" };

export async function render(root) {
  if (!state.status?.transactions) return noData(root);
  const d = await api.get("/api/patterns");
  const tab = { v: "recurring" };
  const out = h("div", {});
  const tabs = h("div", { class: "seg", role: "tablist" });
  const rec = d.recurring.filter((r) => r.direction === "debit"), inc = d.recurring.filter((r) => r.direction === "credit");
  const defs = [["recurring", `Recurring (${rec.length})`], ["habits", `Daily habits (${d.habits.length})`], ["unusual", `Unusual (${d.anomalies.length})`]];
  defs.forEach(([k, label]) => tabs.append(h("button", { role: "tab", "aria-pressed": String(k === tab.v), onclick: () => { tab.v = k; for (const b of tabs.children) b.setAttribute("aria-pressed", String(b.dataset.k === k)); draw(); }, dataset: { k } }, label)));
  root.replaceChildren(h("div", { class: "page-head" }, h("div", {}, h("h1", { text: "Patterns" }), h("div", { class: "sub", text: "What repeats, what is a habit, and what looks odd." })), h("span", { class: "spacer" }), tabs), out);

  function status(r) {
    if (r.status === "overdue") return chip(`Overdue ${r.days_overdue}d — stopped?`, "warn", "alert");
    return chip("On schedule", "good", "check");
  }
  function draw() {
    if (tab.v === "recurring") {
      if (!rec.length) return out.replaceChildren(h("div", { class: "card empty" }, h("h2", { text: "No recurring payments found" }), h("p", { text: "I need at least three similar payments on a regular schedule (monthly rent, subscriptions, recharges…)." })));
      const total = rec.reduce((s, r) => s + r.monthly_cost, 0);
      const rows = rec.map((r) => h("tr", { class: "click", onclick: () => openMerchant(r.merchant_id) },
        h("td", {}, h("div", { class: "m-name", text: r.merchant }), h("div", { class: "m-sub", text: `${plural(r.occurrences, "payment")} since ${dmy(r.first, true)} · ${r.amount_type === "fixed" ? "same amount every time" : "amount varies"}` })),
        h("td", { text: PERIOD[r.period] || r.period }), h("td", { class: "right amt", text: inrAuto(r.last_amount) }), h("td", { class: "right amt", text: inrAuto(r.monthly_cost) }),
        h("td", { class: "nowrap", text: dmy(r.next_expected, true) }), h("td", {}, status(r), r.price_change ? h("div", { style: { marginTop: "4px" } }, chip(`${inrAuto(r.price_change.from)} → ${inrAuto(r.price_change.to)} (${r.price_change.pct > 0 ? "+" : ""}${r.price_change.pct}%)`, r.price_change.pct > 0 ? "serious" : "good", r.price_change.pct > 0 ? "up" : "down")) : null)));
      out.replaceChildren(h("div", { class: "card", style: { marginBottom: "14px" } }, h("div", { class: "muted", text: "Fixed commitments" }), h("div", { style: { fontSize: "28px", fontWeight: 650 }, text: `≈ ${inr(total)} a month` }),
          h("div", { class: "muted", text: `across ${plural(rec.length, "recurring payment")}` })),
        h("div", { class: "tbl-wrap" }, h("table", { class: "tbl" }, h("thead", {}, h("tr", {}, ["Merchant", "Repeats", "Last amount", "≈ per month", "Next expected", "Status"].map((c, i) => h("th", { class: i === 2 || i === 3 ? "right" : "", text: c })))), h("tbody", {}, rows))),
        inc.length ? h("div", { class: "card", style: { marginTop: "14px" } }, h("h2", { text: "Regular money in" }), h("p", { class: "ink2", text: inc.map((r) => `${r.merchant}: about ${inrAuto(r.avg_amount)} ${PERIOD[r.period] || r.period}, around the ${ord(r.next_expected.slice(8))} (next ${dmy(r.next_expected, true)})`).join(" · ") })) : null);
    } else if (tab.v === "habits") {
      if (!d.habits.length) return out.replaceChildren(h("div", { class: "card empty" }, h("h2", { text: "No daily habits found" }), h("p", { text: "Habits are small payments you make again and again (tea, snacks, canteen)." })));
      out.replaceChildren(h("div", { class: "card" }, h("h2", { text: "Small spends that add up" }), h("div", { class: "card-sub", text: "Estimated cost per month, from how often and how much you pay." }), h("div", { style: { height: "10px" } }),
        hbars(d.habits.map((x) => ({ label: x.merchant, value: x.monthly_cost, sub: `${x.per_week}/week · avg ${inrAuto(x.avg_amount)}`, onClick: () => openMerchant(x.merchant_id), tip: [["Visits", String(x.visits)], ["Since", dmy(x.first, true)]] })))));
    } else {
      if (!d.anomalies.length) return out.replaceChildren(h("div", { class: "card empty" }, h("h2", { text: "Nothing unusual" }), h("p", { text: "No payment stood out against your own habits." })));
      const sev = { high: ["Check this", "bad", "alert"], medium: ["Look at this", "warn", "alert"], low: ["FYI", "", "info"] };
      out.replaceChildren(h("div", { class: "stack" }, d.anomalies.map((a) => h("section", { class: "card", style: { cursor: "pointer" }, onclick: () => openExplain(a.txn_id) },
        h("div", { class: "row wrap" }, chip(sev[a.severity][0], sev[a.severity][1], sev[a.severity][2]), h("b", { text: a.title }), h("span", { class: "spacer" }), h("span", { class: "amt", style: { fontSize: "18px", fontWeight: 650 }, text: inrAuto(a.amount) })),
        h("div", { class: "muted", style: { margin: "4px 0 6px" }, text: `${dmy(a.date, true)} ${a.time} · ${a.merchant} · ${a.category}` }),
        h("ul", { class: "notes" }, a.reasons.map((r) => h("li", { text: r.text })))))),
        h("p", { class: "muted", text: "Flags use robust statistics against your own history (median & MAD), not a black box — each one tells you why." }));
    }
  }
  draw();
}

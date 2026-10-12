// UNDER THE HOOD: paste ANY bank/UPI narration and watch each stage of the pipeline.  Nothing is stored.
import * as api from "../api.js";
import { h, icon, pct, chip, badgeKind, debounce } from "../ui.js";

export const keepOnLearn = true;
const PRESETS = [
  ["Tea stall (SBI-style)", "TO TRANSFER-UPI/DR/412345678901/SHARMA TEA ST/YESB/paytmqr2810050501011234@paytm/Payment fr", 15, "08:10"],
  ["Swiggy (legal name)", "UPI/DR/412345678902/BUNDL TECHNOLOGIES/ICIC/swiggy@icici/Payment for order", 289, "21:40"],
  ["Friend (HDFC-style)", "UPI-RAHUL KUMAR-9876543210@YBL-HDFC0001234-412345678903-lunch", 150, "14:05"],
  ["Kirana with P2M marker", "UPI/P2M/412345678904/GUPTA KIRANA STORE/Payment from PhonePe", 142, "18:20"],
  ["Address-only (no name)", "UPI/412345678905/Payment from Ph/q123456789@ybl/Yes Bank", 60, ""],
  ["UPI-app text", "Paid to Balaji Medical Hall", 118, "12:30"],
  ["ATM", "ATW-512345XXXXXX1234-S1ACMS01-CITY MAIN RD", 1000, ""],
  ["Bank charge", "SMS ALERT CHARGES 01/04/26 TO 30/06/26", 17.7, ""],
];

export async function render(root) {
  const narr = h("textarea", { class: "textarea", rows: "3", "aria-label": "Narration", placeholder: "Paste any line from a bank statement or UPI app…" });
  narr.value = PRESETS[0][1];
  const amount = h("input", { class: "input", type: "number", value: "15", style: { width: "110px" }, "aria-label": "Amount" });
  const dir = h("select", { class: "select", "aria-label": "Direction" }, h("option", { value: "debit", text: "Money out" }), h("option", { value: "credit", text: "Money in" }));
  const time = h("input", { class: "input", type: "time", style: { width: "120px" }, "aria-label": "Time of day (optional)" });
  const out = h("div", {});
  const run = debounce(async () => {
    out.classList.add("loading");
    try { draw(await api.post("/api/playground", { narration: narr.value, amount: amount.value, direction: dir.value, time: time.value })); }
    catch (e) { out.replaceChildren(h("div", { class: "banner warn" }, e.message)); }
    out.classList.remove("loading");
  }, 350);
  [narr, amount, dir, time].forEach((el) => el.addEventListener("input", run));
  const presets = h("div", { class: "chips" }, PRESETS.map(([t, n, a, tm]) => h("button", { onclick: () => { narr.value = n; amount.value = a; time.value = tm; dir.value = "debit"; run(); } }, t)));
  root.replaceChildren(h("div", { class: "page-head" }, h("div", {}, h("h1", { text: "Under the hood" }), h("div", { class: "sub", text: "Paste any narration and watch it become a merchant, a shop-or-person decision and a category — step by step. Nothing is saved." }))),
    h("div", { class: "grid g-main" }, h("section", { class: "card stack" }, h("h2", { text: "Try it" }), narr, h("div", { class: "row wrap" }, amount, dir, time), h("div", { class: "muted", text: "Examples:" }), presets),
      h("section", { class: "card" }, out)));
  run();

  function draw(r) {
    const p = r.parsed;
    const kv = (rows) => h("dl", { class: "kv" }, rows.filter(([, v]) => v != null && v !== "").flatMap(([k, v]) => [h("dt", { text: k }), h("dd", { text: String(v) })]));
    const step = (n, title, sum, notes, extra) => h("div", { class: "st" }, h("div", { class: "no", text: String(n) }), h("h3", { text: title }), h("div", { class: "sum", text: sum }),
      notes?.length ? h("ul", { class: "notes" }, notes.map((x) => h("li", { text: x }))) : null, extra || null);
    const top3 = r.category.top3?.length ? h("div", { class: "stack", style: { gap: "4px", marginTop: "8px" } }, r.category.top3.map(([n, pr]) => h("div", { class: "bar2" }, h("span", { style: { width: "170px" }, text: n }), h("span", { class: "b", style: { width: `${Math.max(2, pr * 150)}px` } }), pct(pr)))) : null;
    out.replaceChildren(h("div", { class: "pipe" },
      step(1, "Read the narration", p.mode === "UPI" ? "UPI payment" : p.mode === "OTHER" ? "Unstructured text" : p.mode, p.notes,
        kv([["Marker", p.marker], ["Reference", p.rrn], ["UPI address", p.vpa], ["App", p.app], ["Bank hint", p.bank_hint], ["Payee text", p.payee_raw], ["Typed remark", p.remarks], ["Layout", p.template], ["Flags", p.flags.join(", ")], ["Parser confidence", p.confidence]])),
      step(2, "Find the merchant", r.merchant.name, [`Method: ${r.merchant.method} (confidence ${r.merchant.confidence})`, r.merchant.existing ? `Already in your data (${r.merchant.history_rows} earlier payments are used as evidence).` : "A new merchant — nothing in your data yet.", r.merchant.is_brand ? "Recognised as a known brand." : null].filter(Boolean)),
      step(3, "Shop or person?", `${r.kind.label} · ${pct(r.kind.confidence)} sure (${r.kind.source})`, r.kind.reasons),
      step(4, "Category", `${r.category.category || "—"} · ${r.category.status || ""} ${r.category.confidence != null ? "(" + pct(r.category.confidence) + ")" : ""}`,
        [...(r.category.evidence || []), r.category.words?.length ? "Words that pushed it here: " + r.category.words.map((w) => w[0]).join(", ") : null, r.category.similar?.length ? "Similar names it knows: " + r.category.similar.map((s) => `${s[0]} → ${s[1]}`).join("; ") : null].filter(Boolean), top3)));
  }
}

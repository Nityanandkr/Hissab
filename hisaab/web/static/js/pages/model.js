// LEARNING: what the app has learned from you, the retrain log, and the Excel label workflow (label a month, see the real accuracy).
import * as api from "../api.js";
import { h, icon, inr, dmy, pct, plural, chip, toast, download } from "../ui.js";
import { state, runRetrain, refreshStatus } from "../state.js";
import { noData } from "./common.js";

export async function render(root) {
  if (!state.status?.transactions) return noData(root);
  const [d, llm] = await Promise.all([api.get("/api/learning"), api.get("/api/llm/status")]);
  const st = d.status, m = st.model;
  const last = d.runs[0];
  const stat = (label, value, sub) => h("div", { class: "card tile" }, h("div", { class: "label", text: label }), h("div", { class: "value", text: value }), sub ? h("div", { class: "muted", text: sub }) : null);
  const tiles = h("div", { class: "grid g4" }, stat("Examples the model started with", m.n_train ? String(m.n_train - m.n_user) : "—", "built-in knowledge: brands, shop words"),
    stat("Examples you taught it", String(m.n_user || 0), plural(st.rules, "merchant rule") + " · " + plural(st.user_kinds, "shop/person answer")),
    stat("Corrections made", String(st.corrections), "every one is logged below"),
    stat("Last re-learning", last ? `${last.seconds}s` : "—", last ? `${dmy(last.at, true)} · ${last.changed.length} merchants changed` : "not yet — correct something first"));
  const stale = st.model_stale ? h("div", { class: "banner warn" }, icon("info", { width: 16, height: 16 }), "You have corrections the model has not learned from yet.", h("span", { class: "spacer" }),
    h("button", { class: "btn small primary", onclick: async (e) => { e.currentTarget.disabled = true; await runRetrain(); } }, "Re-learn now")) : null;

  const forget = async (payload, label) => {
    try { await api.post("/api/forget", payload); toast(`Forgot: ${label}. Re-learning…`); await runRetrain(); render(root); } catch (e) { toast(e.message); }
  };
  const fbtn = (payload, label) => h("button", { class: "btn small ghost", title: "Undo this lesson", onclick: () => forget(payload, label) }, "Forget");
  const rows = [
    ...d.rules.map((r) => h("tr", {}, h("td", { text: r.merchant }), h("td", { text: r.direction === "credit" ? "money received → " + r.category : "payments → " + r.category }), h("td", { class: "muted", text: dmy(r.created_at, true) }), h("td", { class: "right" }, fbtn({ what: "category", merchant_id: r.merchant_id, direction: r.direction }, `${r.merchant} → ${r.category}`)))),
    ...d.kinds.map((k) => h("tr", {}, h("td", { text: k.merchant }), h("td", { text: `is a ${k.kind === "person" ? "person" : k.kind === "merchant" ? "shop / business" : k.kind}` }), h("td", { class: "muted", text: "" }), h("td", { class: "right" }, fbtn({ what: "kind", merchant_id: k.merchant_id }, `${k.merchant} kind`)))),
    ...d.locked.map((t) => h("tr", {}, h("td", { text: t.merchant }), h("td", { text: `one payment (${dmy(t.date)}, ${inr(t.amount)}) → ${t.category}` }), h("td", { class: "muted", text: "" }), h("td", { class: "right" }, fbtn({ what: "payment", txn_id: t.id }, "single payment label")))),
  ];
  const rules = h("section", { class: "card" }, h("h2", { text: "What you told it" }), h("p", { class: "card-sub", text: "Made a mistake? Forget a lesson and the app goes back to deciding by itself." }),
    rows.length ? h("div", { class: "tbl-wrap", style: { marginTop: "10px" } }, h("table", { class: "tbl" }, h("thead", {}, h("tr", {}, ["Merchant", "Lesson", "When", ""].map((c) => h("th", { text: c })))), h("tbody", {}, rows))) :
    h("p", { class: "muted", text: "Nothing yet. Fix a category on the Transactions or Review page and it appears here." }));
  const changed = last?.changed?.length ? h("section", { class: "card" }, h("h2", { text: "What changed after the last re-learning" }), h("p", { class: "card-sub", text: "These OTHER merchants changed category because of what you taught (the model generalised)." }),
    h("ul", { class: "notes" }, last.changed.map((c) => h("li", { text: `${c.merchant}: ${c.from} → ${c.to} (${plural(c.rows, "payment")})` })))) : null;
  const corr = h("section", { class: "card" }, h("h2", { text: "Correction log" }), d.corrections.length ? h("ul", { class: "notes" }, d.corrections.slice(0, 30).map((c) =>
    h("li", { text: `${dmy(c.at, true)} · ${c.what}: ${c.merchant || "transaction"} — ${c.old_value || "∅"} → ${c.new_value}${c.scope === "transaction" ? " (single payment)" : ""}` }))) : h("p", { class: "muted", text: "No corrections yet." }));

  // ---- Excel workflow
  const result = h("div", {});
  const fileIn = h("input", { type: "file", accept: ".xlsx,.xls,.csv", class: "sr-only", id: "lab", onchange: async (e) => {
    const f = e.target.files[0]; if (!f) return;
    result.replaceChildren(h("div", { class: "row muted" }, h("span", { class: "spin" }), "Reading your labels and re-learning…"));
    try { show(await api.upload("/api/labels/import", f)); await refreshStatus(); } catch (er) { result.replaceChildren(h("div", { class: "banner warn" }, er.message)); }
  } });
  function show(r) {
    const ev = r.evaluation;
    result.replaceChildren(h("div", { class: "card stack", style: { background: "var(--surface-2)" } },
      h("h3", { text: "Your labels vs what the app guessed BEFORE learning from them" }),
      h("div", { class: "stat-list" }, h("div", {}, h("div", { class: "muted", text: "Rows you labelled" }), h("div", { style: { fontSize: "22px", fontWeight: 650 }, text: String(ev.labelled_rows) })),
        h("div", {}, h("div", { class: "muted", text: "App already agreed on" }), h("div", { style: { fontSize: "22px", fontWeight: 650 }, text: pct(ev.accuracy, 1) })),
        ...Object.entries(ev.by_status).map(([s, v]) => h("div", {}, h("div", { class: "muted", text: `when it was “${s}”` }), h("div", { style: { fontSize: "22px", fontWeight: 650 }, text: pct(v.accuracy, 1) }), h("div", { class: "muted", text: plural(v.rows, "row") })))),
      h("p", { class: "ink2", text: `Learned: ${plural(r.applied.merchant_rules, "merchant rule")}, ${plural(r.applied.transaction_labels, "single-payment label")}, ${plural(r.applied.kinds, "shop/person answer")}. Re-learning took ${r.retrain.seconds}s and changed ${plural(r.retrain.n_changed_rows, "payment")}.` }),
      h("p", { class: "muted", text: "Tip: this accuracy is only honest if you labelled WITHOUT looking at the app's guesses (use the blind template)." })));
  }
  const labels = h("section", { class: "card stack" }, h("h2", { text: "Label a month in Excel — then measure the app on YOUR data" }),
    h("p", { class: "ink2", text: "Download your transactions, fill the my_category column by hand for a month, and bring it back. You get (1) a real accuracy number and (2) a model that has learned your habits." }),
    h("div", { class: "row wrap" }, h("button", { class: "btn", onclick: () => download("/api/export/labels.xlsx?assisted=0") }, icon("upload", { width: 14, height: 14 }), "Blind template (honest accuracy)"),
      h("button", { class: "btn", onclick: () => download("/api/export/labels.xlsx?assisted=1") }, "Assisted template (faster, shows guesses)"),
      h("label", { class: "btn primary", for: "lab" }, "Upload my labelled file")), fileIn, result);

  const llmCard = h("section", { class: "card" }, h("h2", { text: "Optional: language model for harder questions" }),
    h("p", { class: "ink2", text: llm.active ? `Active (${llm.model}). It only sees your question text and the list of category names — never transactions, amounts or merchants.` : "Off. Questions are understood by built-in rules, fully offline. To switch on an LLM for the questions the rules cannot parse, set HISAAB_LLM=anthropic and an API key in your environment, then restart." }),
    llm.last_error ? h("p", { class: "muted", text: `Last LLM error: ${llm.last_error}` }) : null);

  root.replaceChildren(h("div", { class: "page-head" }, h("div", {}, h("h1", { text: "Learning" }), h("div", { class: "sub", text: "How the app improves from your corrections — and how to measure it." })),
    h("span", { class: "spacer" }), h("button", { class: "btn", onclick: async (e) => { e.currentTarget.disabled = true; await runRetrain(); } }, "Re-learn now")),
    h("div", { class: "stack", style: { gap: "16px" } }, stale, tiles, labels, changed, h("div", { class: "grid g2" }, rules, corr), llmCard));
}

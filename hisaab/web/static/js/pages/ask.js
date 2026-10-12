// ASK: natural-language questions.  The answer ALWAYS shows how the question was understood, the breakdown, and the payments behind the number.
import * as api from "../api.js";
import { h, icon, inrAuto, dmy, plural, chip, badgeKind } from "../ui.js";
import { state } from "../state.js";
import { hbars } from "../charts.js";
import { noData, openExplain } from "./common.js";

export const keepOnLearn = true;

const EXAMPLES = ["How much did I spend on groceries last month?", "Which local shops do I visit most?", "Where did my spending increase?", "Show my recurring expenses.",
                  "Any unusual transactions?", "How much did I send to friends this month?", "What was my biggest expense in September?", "pichle mahine food pe kitna kharcha hua?"];
const history = [];

export async function render(root, params) {
  if (!state.status?.transactions) return noData(root);
  const log = h("div", { class: "qa" });
  const input = h("input", { class: "input", type: "text", maxlength: "300", placeholder: "Ask about your spending…  e.g. “How much did I spend on groceries last month?”", "aria-label": "Your question",
                             onkeydown: (e) => { if (e.key === "Enter") submit(); } });
  const btn = h("button", { class: "btn primary", onclick: () => submit() }, icon("chat", { width: 16, height: 16 }), "Ask");
  const chips = h("div", { class: "chips" }, EXAMPLES.map((q) => h("button", { onclick: () => { input.value = q; submit(); } }, q)));
  root.replaceChildren(h("div", { class: "page-head" }, h("div", {}, h("h1", { text: "Ask" }), h("div", { class: "sub", text: "Plain English (or Hinglish). I translate your question into a precise query, compute the answer from your transactions, and show my working." }))),
    h("div", { class: "ask-wrap" }, h("div", { class: "ask-box" }, h("div", { class: "ask-in" }, input, btn), chips), log));
  history.forEach((x) => log.append(x));
  if (params.q) { input.value = params.q; submit(); }
  input.focus();

  async function submit(text) {
    const q = (text ?? input.value).trim();
    if (!q) return;
    input.value = "";
    const qn = h("div", { class: "q", text: q });
    const an = h("div", { class: "card a" }, h("div", { class: "row muted" }, h("span", { class: "spin" }), "Working it out…"));
    log.prepend(an); log.prepend(qn);
    history.unshift(an); history.unshift(qn);
    try { an.replaceChildren(...answer(await api.post("/api/ask", { question: q }), q, submit)); }
    catch (e) { an.replaceChildren(h("p", { class: "muted", text: e.message })); }
  }
}

function answer(a, q, ask) {
  const out = [h("div", { class: "headline", text: a.headline || "" }), h("p", { class: "ink2", text: a.text || "" })];
  if (a.clarify) out.push(h("div", { class: "opts" }, a.clarify.options.map((o) => h("button", { class: "opt", onclick: () => ask(`${q} (${o.name})`) }, o.name))));
  if (a.interpretation?.length) out.push(h("div", { class: "interp", title: "How I understood your question" }, a.interpretation.map((t) => (t.includes("did not use these words") ? chip(t, "warn", "info") : chip(t, "accent")))));
  if (a.chart?.values?.length) {
    const signed = a.chart.values.some((v) => v < 0);
    const max = Math.max(...a.chart.values.map(Math.abs), 1);
    out.push(h("div", { style: { margin: "10px 0" } }, h("div", { class: "muted", style: { marginBottom: "6px" }, text: a.chart.title }),
      hbars(a.chart.labels.map((l, i) => ({ label: l, value: a.chart.values[i], kind: signed ? (a.chart.values[i] >= 0 ? "pos" : "neg") : null, valueText: signed ? (a.chart.values[i] > 0 ? "+" : "") + inrAuto(a.chart.values[i]) : undefined })), { max, fmt: (v) => (a.chart.title.includes("Days") ? plural(v, "day") : inrAuto(v)) })));
  }
  if (a.table) out.push(h("div", { class: "tbl-wrap", style: { margin: "10px 0" } }, h("table", { class: "tbl" }, h("thead", {}, h("tr", {}, a.table.columns.map((c) => h("th", { text: c })))),
    h("tbody", {}, a.table.rows.map((r) => h("tr", {}, r.map((c) => h("td", { text: String(c) }))))))));
  if (a.txns?.length) out.push(h("details", { class: "details" }, h("summary", { text: `Show the ${plural(a.txns.length, "payment")} behind this` }),
    h("div", { class: "tbl-wrap", style: { marginTop: "8px" } }, h("table", { class: "tbl" }, h("tbody", {}, a.txns.map((t) => h("tr", { class: "click", onclick: () => openExplain(t.id) },
      h("td", { class: "nowrap", text: dmy(t.date, true) }), h("td", { text: t.merchant }), h("td", { text: t.category }), h("td", { class: `right amt ${t.direction}`, text: (t.direction === "credit" ? "+" : "") + inrAuto(t.amount) }))))))));
  if (a.followups?.length && !a.clarify) out.push(h("div", { class: "chips" }, a.followups.map((f) => h("button", { onclick: () => ask(f) }, f))));
  return out;
}

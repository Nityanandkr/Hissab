// REVIEW = active learning.  The app asks only about the merchants where your answer matters most (money x uncertainty) and learns from each.
import * as api from "../api.js";
import { h, icon, inr, inrAuto, dmy, pct, plural, loadingLine, badgeKind, chip, toast } from "../ui.js";
import { state, scheduleRetrain, refreshStatus, loadTaxonomy, categoryItems } from "../state.js";
import { chooseCategory } from "../teach.js";
import { noData, openMerchant } from "./common.js";

export const keepOnLearn = true;      // never wipe the cards you are working on when a retrain finishes

export async function render(root) {
  if (!state.status?.transactions) return noData(root);
  root.replaceChildren(loadingLine());
  const d = await api.get("/api/review?limit=30");
  const header = h("div", { class: "page-head" }, h("div", {}, h("h1", { text: "Review" }),
    h("div", { class: "sub", text: "The app asks only where your answer helps the most. Each answer teaches it — similar shops get better guesses." })));
  if (!d.items.length) {
    return root.replaceChildren(header, h("div", { class: "card empty" }, h("h2", { text: "Nothing to review 🎉" }),
      h("p", { text: "Every payment has a confident category. If something still looks wrong, open Transactions and click its category." }),
      h("a", { class: "btn primary", href: "#/transactions" }, "Open transactions")));
  }
  const summary = h("div", { class: "card", style: { marginBottom: "14px" } }, h("div", { class: "row wrap" },
    h("div", {}, h("div", { class: "muted", text: "Still uncertain" }), h("div", { style: { fontSize: "24px", fontWeight: 650 }, text: inr(d.money) }), h("div", { class: "muted", text: `across ${plural(d.n_merchants, "merchant")}` })),
    h("div", { style: { flex: 1, minWidth: "220px" } }, h("div", { class: "muted", text: `The top 5 below cover ${pct(d.top5_share)} of it` }), h("div", { class: "meter", style: { marginTop: "6px" } }, h("div", { style: { width: `${Math.round(d.top5_share * 100)}%` } })))));
  const list = h("div", { class: "rv" });
  d.items.forEach((it) => list.append(card(it, d)));
  root.replaceChildren(header, summary, list);
}

function card(it, all) {
  const root = h("section", { class: "card rv-card" });
  const done = (msg) => { root.classList.add("done"); root.replaceChildren(h("div", { class: "row" }, icon("check", { width: 16, height: 16 }), h("b", { text: it.merchant }), h("span", { class: "muted", text: msg }))); };
  const top = h("div", { class: "rv-top" }, h("div", { style: { flex: 1, minWidth: "200px" } },
    h("h3", {}, h("a", { href: "#", text: it.merchant, onclick: (e) => { e.preventDefault(); openMerchant(it.merchant_id); } })),
    h("div", { class: "meta", text: `${plural(it.n, "payment")} · ${dmy(it.first)} – ${dmy(it.last)} · avg ${inrAuto(it.avg)}` })),
    h("div", { class: "right" }, h("div", { style: { fontSize: "20px", fontWeight: 650 }, text: inr(it.total) }), badgeKind(it.kind, it.is_local)));
  root.append(top, h("p", { class: "ink2", style: { margin: "6px 0 0" } }, icon("info", { width: 14, height: 14 }), " Why we are asking: ", it.why + "."));
  it.samples.slice(0, 2).forEach((s) => root.append(h("div", { class: "sample", text: s })));

  const body = h("div", {});
  root.append(body);
  const askCategory = (top3) => {
    const q = h("div", { class: "rv-q" }, h("div", { class: "qt", text: "What does it mostly sell?" }));
    const opts = h("div", { class: "opts" });
    (top3 || []).forEach(([name, p], i) => opts.append(h("button", { class: `opt ${i === 0 ? "lean" : ""}`, onclick: () => setCat(name) }, name, h("span", { class: "pct", text: pct(p) }))));
    opts.append(h("button", { class: "opt", onclick: async (e) => { await loadTaxonomy(); const { popMenu } = await import("../ui.js"); const v = await popMenu(e.currentTarget, categoryItems("debit", null)); if (v) setCat(v); } }, "Something else…"));
    q.append(opts);
    return q;
  };
  async function setCat(name) {
    try { await api.post(`/api/merchants/${it.merchant_id}/category`, { category: name }); } catch (e) { return toast(e.message); }
    await refreshStatus(); scheduleRetrain();
    done(`→ ${name} — saved for all ${plural(it.n, "payment")}`);
  }
  if (it.questions.includes("kind")) {
    const lean = it.p_person != null ? (it.p_person >= 0.5 ? "person" : "merchant") : null;
    const q = h("div", { class: "rv-q" }, h("div", { class: "qt", text: "Is this a shop or a person?" }),
      h("div", { class: "opts" },
        h("button", { class: `opt ${lean === "merchant" ? "lean" : ""}`, onclick: () => setKind("merchant") }, "Shop / business / service", h("span", { class: "pct", text: "tea stall, mess, auto driver, company…" })),
        h("button", { class: `opt ${lean === "person" ? "lean" : ""}`, onclick: () => setKind("person") }, "A person", h("span", { class: "pct", text: "friend, family, landlord…" }))),
      it.kind_reasons?.length ? h("ul", { class: "reasons" }, it.kind_reasons.map((r) => h("li", { text: r }))) : null);
    body.append(q);
    async function setKind(kind) {
      try { await api.post(`/api/merchants/${it.merchant_id}/kind`, { kind }); } catch (e) { return toast(e.message); }
      await refreshStatus(); scheduleRetrain();
      if (kind === "merchant" && it.top3_shop?.length && !it.questions.includes("category")) {      // a shop: now ask what it sells, in the same card
        q.replaceWith(h("p", { class: "ink2", style: { margin: "12px 0 0" } }, icon("check", { width: 14, height: 14 }), " Got it — a shop / business."));
        body.append(askCategory(it.top3_shop));
        return;
      }
      if (it.questions.includes("category")) { q.replaceWith(h("p", { class: "ink2", style: { margin: "12px 0 0" } }, icon("check", { width: 14, height: 14 }), ` Got it — ${kind === "person" ? "a person" : "a shop / business"}.`)); return; }
      done(`marked as ${kind === "person" ? "a person" : "a shop / business"} — thank you`);
    }
  }
  if (it.questions.includes("category")) body.append(askCategory(it.top3));
  return root;
}

import * as api from "../api.js";
import { h, icon, inr, inrAuto, dmy, pct, plural, monthLabel, store, loadingLine } from "../ui.js";
import { hbars, columns, stacked, lines, chartCard, SERIES, OTHER } from "../charts.js";
import { noData, openMerchant } from "./common.js";
import { state } from "../state.js";

const AXIS_GRAY = "var(--axis)";

export async function render(root) {
  const first = !root.firstChild;
  if (first) root.replaceChildren(loadingLine());
  else root.firstChild.classList.add("loading");               // refetch keeps the frame: dim the old render, no skeleton flash
  const key = store("hisaab-period");
  const d = await api.get("/api/dashboard" + (key ? `?period=${encodeURIComponent(key)}` : ""));
  if (d.empty) return noData(root);
  root.replaceChildren(view(d, root));
}

function deltaNode(now, before, label, upIsBad = true) {
  if (before == null || before === 0) return null;
  const ch = (now - before) / before;
  const flat = Math.abs(ch) < 0.005;
  const bad = upIsBad ? ch > 0 : ch < 0;
  return h("span", { class: `delta ${flat ? "flat" : bad ? "bad" : "good"}` }, icon(ch >= 0 ? "up" : "down", { width: 13, height: 13 }),
    `${ch >= 0 ? "+" : "−"}${Math.abs(ch * 100).toFixed(0)}%`, h("span", { class: "muted", style: { fontWeight: 400 }, text: ` vs ${label} (${inr(before)})` }));
}

// the profile's monthly budget (Profile page), shown only when a single month is selected
function budgetNode(spend, p, projection) {
  const budget = state.profile?.monthly_budget;
  const oneMonth = p.start.endsWith("-01") && p.start.slice(0, 7) === p.end.slice(0, 7);
  if (!budget || !oneMonth) return null;
  const used = spend / budget, over = spend > budget;
  const pace = projection && !over && projection.projected > budget ? ` At this pace you will go over by about ${inr(projection.projected - budget)}.` : "";
  return h("div", { style: { marginTop: "12px" } },
    h("div", { class: "row", style: { justifyContent: "space-between" } }, h("span", { class: "muted", text: `Budget ${inr(budget)}` }),
      h("span", { class: `delta ${over ? "bad" : used > 0.85 ? "flat" : "good"}`, text: over ? `${inr(spend - budget)} over` : `${inr(budget - spend)} left` })),
    h("div", { class: "meter", style: { marginTop: "6px" }, role: "meter", "aria-valuemin": "0", "aria-valuemax": String(budget), "aria-valuenow": String(Math.round(spend)), "aria-label": "Budget used" },
      h("div", { style: { width: `${Math.min(100, Math.round(used * 100))}%`, background: over ? "var(--bad)" : null } })),
    pace ? h("p", { class: "ink2", style: { margin: "6px 0 0" }, text: pace.trim() }) : null);
}

function view(d, root) {
  const ov = d.overview, p = d.period;
  const sel = h("select", { class: "select", "aria-label": "Period", onchange: (e) => { store("hisaab-period", e.target.value); render(root); } },
    d.periods.map((o) => h("option", { value: o.key, selected: o.label === p.label || o.key === store("hisaab-period") ? "" : null, text: o.label })));
  const head = h("div", { class: "page-head" }, h("div", {}, h("h1", { text: "Dashboard" }),
    h("div", { class: "sub", text: `${dmy(d.range.min, true)} – ${dmy(d.range.max, true)} · ${plural(d.status.transactions, "transaction")} · ${plural(d.status.merchants, "merchant")}` })), h("span", { class: "spacer" }), sel);

  // ---- hero + tiles
  const proj = d.projection ? h("p", { class: "ink2", style: { margin: "6px 0 0" }, text: `At this pace ${d.projection.month} will end around ${inr(d.projection.projected)} (an estimate; last month was ${inr(d.projection.last_month)}).` }) : null;
  const hero = h("section", { class: "card hero" }, h("div", { class: "label", text: `Spent · ${p.label}` }), h("div", { class: "value", text: inr(ov.spend) }),
    ov.prev ? deltaNode(ov.spend, ov.prev.spend, ov.prev.period.label) : null,
    h("p", { class: "ink2", style: { margin: "8px 0 0" }, text: `${plural(ov.n_payments, "payment")} · about ${inr(ov.avg_per_day)} a day · biggest ${inr(ov.biggest?.amount || 0)}${ov.biggest ? " at " + ov.biggest.merchant : ""}` }), proj,
    budgetNode(ov.spend, p, d.projection));
  const net = ov.net;
  const tile = (label, value, sub, extra) => h("section", { class: "card tile" }, h("div", { class: "label", text: label }), h("div", { class: "value", text: value }), sub ? h("div", { class: "muted", text: sub }) : null, extra || null);
  const peoplePct = ov.spend ? ov.p2p_spend / ov.spend : 0;
  const tiles = h("div", { class: "grid g2" },
    tile("Received", inr(ov.income), ov.refunds ? `plus ${inr(ov.refunds)} refunds & cashback` : "money in"),
    tile("Kept (received − spent)", (net >= 0 ? "+" : "−") + inr(Math.abs(net)), net >= 0 ? "you spent less than you received" : "you spent more than you received",
         h("span", { class: `delta ${net >= 0 ? "good" : "bad"}` }, icon(net >= 0 ? "up" : "down", { width: 13, height: 13 }), net >= 0 ? "ahead" : "overspent")),
    tile("Sent to people (P2P)", pct(peoplePct), `${inr(ov.p2p_spend)} to friends/family · ${inr(ov.shop_spend)} to shops & companies`),
    h("section", { class: "card tile" }, h("div", { class: "label", text: "Worth confirming" }), h("div", { class: "value", text: ov.uncertain_spend ? inr(ov.uncertain_spend) : "All clear" }),
      h("div", { class: "muted", text: ov.uncertain_spend ? `${pct(ov.uncertain_share)} of spending is a best guess or unknown` : "every payment has a confident category" }),
      h("a", { class: "more", href: "#/review", style: { fontWeight: 600, display: "inline-block", marginTop: "4px" }, text: ov.uncertain_spend ? "Teach the app →" : "Open Review →" })));
  const top = h("div", { class: "grid", style: { gridTemplateColumns: "minmax(0,1.25fr) minmax(0,1fr)" } }, hero, tiles);
  top.classList.add("top-row");

  // ---- insights
  const ins = d.insights.length ? h("section", {}, h("h2", { style: { fontSize: "15px", margin: "6px 0 10px" }, text: "Worth knowing" }),
    h("div", { class: "insights" }, d.insights.map((i) => h("div", { class: `insight ${i.tone}` }, h("div", { class: "ico" }, icon({ warn: "alert", good: "trend", info: "info" }[i.tone] || "info")),
      h("div", {}, h("h3", { text: i.title }), h("p", { text: i.text }),
        i.link ? h("a", { class: "more", href: i.link.q ? `#/ask?q=${encodeURIComponent(i.link.q)}` : `#/${i.link.page}`, text: i.link.page === "review" ? "Review them →" : i.link.page === "ask" ? "Ask about it →" : "See details →" }) : null))))) : null;

  // ---- charts
  const cats = d.categories;
  const catCard = chartCard({
    title: "Where the money went", sub: `${p.label} · ${plural(cats.length, "category", "categories")}`,
    draw: (el) => el.replaceChildren(cats.length ? hbars(cats.slice(0, 10).map((c) => ({
      label: c.category, value: c.amount, sub: pct(c.share), kind: c.category === "Needs review" ? "unsure" : null,
      onClick: () => { location.hash = `#/transactions?category=${encodeURIComponent(c.category)}&start=${p.start}&end=${p.end}&direction=debit`; },
      tip: [["Payments", String(c.count)], ["Average", inrAuto(c.avg)], ["Share", pct(c.share, 1)]] })), { fmt: inrAuto })
      : h("p", { class: "muted", text: "No spending in this period." })),
    table: () => ({ columns: [{ label: "Category" }, { label: "Amount", right: true }, { label: "Share", right: true }, { label: "Payments", right: true }], rows: cats.map((c) => [c.category, inrAuto(c.amount), pct(c.share, 1), c.count]) }),
  });
  const pace = d.pace;
  const paceCard = pace ? chartCard({
    title: "Pace: this period vs the last", sub: "Cumulative spending, day by day",
    draw: (el) => lines(el, { recent: pace.recent, base: pace.base, recentLabel: pace.recent_label, baseLabel: pace.base_label }),
    table: () => ({ columns: [{ label: "Day" }, { label: pace.recent_label, right: true }, { label: pace.base_label, right: true }],
                    rows: pace.base.map((_, i) => [String(i + 1), pace.recent[i] != null ? inrAuto(pace.recent[i]) : "–", inrAuto(pace.base[i])]).concat(pace.recent.length > pace.base.length ? pace.recent.slice(pace.base.length).map((v, k) => [String(pace.base.length + k + 1), inrAuto(v), "–"]) : []) }),
  }) : null;

  const mm = d.monthly;
  const names = Object.keys(mm.series).filter((n) => n !== "Needs review");
  const topN = names.slice(0, 5), rest = names.slice(5);
  const series = topN.map((n, i) => ({ name: n, color: SERIES[i], values: mm.series[n] }));
  if (rest.length) series.push({ name: "Other categories", color: OTHER, values: mm.months.map((_, i) => rest.reduce((s, n) => s + mm.series[n][i], 0)) });
  if (mm.series["Needs review"]) series.push({ name: "Needs review", color: AXIS_GRAY, values: mm.series["Needs review"] });
  const monthCard = chartCard({
    title: "Month by month", sub: "Spending by category (the five biggest, then the rest)",
    draw: (el) => stacked(el, { labels: mm.months.map(monthLabel), series, totals: mm.spend, partialLast: mm.partial_last }),
    table: () => ({ columns: [{ label: "Month" }, ...series.map((s) => ({ label: s.name, right: true })), { label: "Total", right: true }],
                    rows: mm.months.map((m, i) => [monthLabel(m), ...series.map((s) => inrAuto(s.values[i])), inrAuto(mm.spend[i])]) }),
  });

  const wd = d.weekday_hour;
  const dailyCard = chartCard({
    title: "Day by day", sub: `${p.label}`,
    draw: (el) => columns(el, { data: d.daily.map((x, i) => ({ label: dmy(x.date, true), short: String(Number(x.date.slice(8))), value: x.amount, tip: [] })), ariaLabel: "Daily spending" }),
    table: () => ({ columns: [{ label: "Date" }, { label: "Spent", right: true }], rows: d.daily.map((x) => [dmy(x.date, true), inrAuto(x.amount)]) }),
  });
  const wdCard = chartCard({
    title: "Which days you spend", sub: wd.hour ? "By weekday" : "By weekday (this statement has no times of day)",
    draw: (el) => columns(el, { data: wd.weekday.map((x) => ({ label: x.day, value: x.amount, tip: [["Payments", String(x.count)]] })), height: 170, ariaLabel: "Spending by weekday" }),
    table: () => ({ columns: [{ label: "Day" }, { label: "Spent", right: true }, { label: "Payments", right: true }], rows: wd.weekday.map((x) => [x.day, inrAuto(x.amount), x.count]) }),
  });
  const hourCard = wd.hour && wd.time_coverage > 0.3 ? chartCard({
    title: "What time you spend", sub: `Hour of day · times known for ${pct(wd.time_coverage)} of payments`,
    draw: (el) => columns(el, { data: wd.hour.map((x) => ({ label: `${x.hour}:00`, short: x.hour % 3 === 0 ? `${x.hour}h` : "", value: x.amount, tip: [["Payments", String(x.count)]] })), height: 170, ariaLabel: "Spending by hour" }),
    table: () => ({ columns: [{ label: "Hour" }, { label: "Spent", right: true }, { label: "Payments", right: true }], rows: wd.hour.map((x) => [`${x.hour}:00`, inrAuto(x.amount), x.count]) }),
  }) : null;

  const topM = d.top_merchants;
  const merchCard = chartCard({
    title: "Where you spent the most", sub: `${p.label} · by amount`,
    draw: (el) => el.replaceChildren(topM.length ? hbars(topM.map((m) => ({ label: m.merchant, value: m.amount, sub: `${m.visits}×`, onClick: () => openMerchant(m.merchant_id),
      tip: [["Payments", String(m.visits)], ["Days", String(m.days)], ["Category", m.category]] }))) : h("p", { class: "muted", text: "Nothing yet." })),
    table: () => ({ columns: [{ label: "Merchant" }, { label: "Spent", right: true }, { label: "Payments", right: true }, { label: "Category" }], rows: topM.map((m) => [m.merchant, inrAuto(m.amount), m.visits, m.category]) }),
  });
  const loc = d.local_shops;
  const localCard = chartCard({
    title: "Local shops you visit most", sub: `${p.label} · days visited`,
    draw: (el) => el.replaceChildren(loc.length ? hbars(loc.map((m) => ({ label: m.merchant, value: m.days, sub: inrAuto(m.amount), valueText: plural(m.days, "day"), onClick: () => openMerchant(m.merchant_id),
      tip: [["Total", inrAuto(m.amount)], ["Average", inrAuto(m.avg)], ["Category", m.category]] }))) : h("p", { class: "muted", text: "No local shops in this period." })),
    table: () => ({ columns: [{ label: "Shop" }, { label: "Days", right: true }, { label: "Spent", right: true }], rows: loc.map((m) => [m.merchant, m.days, inrAuto(m.amount)]) }),
  });

  const g = (...kids) => h("div", { class: "grid g2" }, ...kids.filter(Boolean));
  const others = [merchCard, localCard, dailyCard, wdCard, hourCard].slice(paceCard ? 0 : 1).filter(Boolean);   // without a pace chart the merchant card moves up
  const first = paceCard ? [catCard, paceCard] : [catCard, merchCard];
  const rows = [];
  for (let i = 0; i < others.length; i += 2) rows.push(g(others[i], others[i + 1]));
  return h("div", { class: "stack", style: { gap: "18px" } }, head, top, ins, g(...first), monthCard, ...rows);
}

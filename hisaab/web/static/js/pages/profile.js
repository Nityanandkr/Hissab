// PROFILE: details of the profile in use (name, budget, income, goal) + every profile (switch, create, delete).
// Each profile is a separate set of data on the server, so switching shows a different person's transactions.
import * as api from "../api.js";
import { h, icon, inr, dmy, plural, chip, toast, confirmDialog } from "../ui.js";
import { state, loadProfiles, switchProfile, refreshStatus } from "../state.js";

export const keepOnLearn = true;

const input = (name, value, attrs = {}) => h("input", { class: "input", name, value: value ?? "", ...attrs });
const money = (name, value) => input(name, value, { type: "number", min: "0", step: "100", inputmode: "numeric", placeholder: "₹" });
const field = (label, el, hint) => h("label", {}, label, el, hint ? h("span", { class: "muted", text: hint }) : null);

export async function render(root) {
  await loadProfiles();
  const p = state.profile, st = state.status || (await refreshStatus());
  root.replaceChildren(
    h("div", { class: "page-head" }, h("div", {}, h("h1", { text: "Profile" }),
      h("div", { class: "sub", text: "Each profile keeps its own transactions, merchants and learned rules — switching never mixes them." }))),
    h("div", { class: "stack", style: { gap: "16px" } }, details(p, st, root), everyone(root), creator()));
}

function details(p, st, root) {
  const f = {
    name: input("name", p.name, { required: true, maxlength: "40", autocomplete: "off" }),
    monthly_budget: money("monthly_budget", p.monthly_budget),
    monthly_income: money("monthly_income", p.monthly_income),
    income_day: input("income_day", p.income_day, { type: "number", min: "1", max: "31", placeholder: "e.g. 1" }),
    goal: input("goal", p.goal, { maxlength: "120", placeholder: "e.g. a new laptop by March" }),
  };
  const save = h("button", { class: "btn primary", type: "submit" }, "Save details");
  const form = h("form", { class: "stack", onsubmit: async (e) => {
    e.preventDefault();
    save.disabled = true;
    try {
      const body = Object.fromEntries(Object.entries(f).map(([k, el]) => [k, el.value.trim()]));
      await api.post(`/api/profiles/${encodeURIComponent(p.id)}`, body);
      toast("Profile saved.");
      return render(root);              // the list below and the sidebar show the new details too
    } catch (er) { toast(er.message); }
    save.disabled = false;
  } },
    h("div", { class: "map-grid" },
      field("Name", f.name),
      field("Monthly budget", f.monthly_budget, "the dashboard shows how much of it is used"),
      field("Money in per month", f.monthly_income, "pocket money, stipend or salary"),
      field("Day it arrives", f.income_day),
      field("Saving for", f.goal)),
    h("div", { class: "row" }, save));
  const data = st.transactions
    ? `${plural(st.transactions, "transaction")} from ${dmy(st.date_min, true)} to ${dmy(st.date_max, true)} · ${plural(st.merchants, "merchant")} · ${plural(st.imports, "import")}`
    : "No transactions yet — import a statement or load the demo data.";
  return h("section", { class: "card stack" },
    h("div", { class: "row wrap" }, h("h2", { text: "Details" }), chip(`${p.name} · in use`, "accent")),
    h("div", { class: "muted", text: data }),
    p.monthly_budget && p.monthly_income ? h("div", { class: "ink2", text: `Budget is ${Math.round((p.monthly_budget / p.monthly_income) * 100)}% of the money coming in (${inr(p.monthly_budget)} of ${inr(p.monthly_income)}).` }) : null,
    form,
    st.transactions ? null : h("div", { class: "row" }, h("a", { class: "btn", href: "#/import" }, icon("upload", { width: 16, height: 16 }), "Import statements")));
}

function everyone(root) {
  const rows = state.profiles.map((p) => {
    const using = p.id === state.profile.id;
    return h("div", { class: "row wrap prof-row" },
      h("span", { class: "av", text: p.name.charAt(0).toUpperCase() }),
      h("div", { style: { flex: 1, minWidth: "140px" } }, h("div", { style: { fontWeight: 600 }, text: p.name }),
        h("div", { class: "muted", text: [p.monthly_budget ? `budget ${inr(p.monthly_budget)}/month` : null, p.goal ? `saving for ${p.goal}` : null].filter(Boolean).join(" · ") || "no details yet" })),
      using ? chip("in use", "accent") : h("button", { class: "btn small", onclick: async () => { try { await switchProfile(p.id); } catch (e) { toast(e.message); } } }, "Switch"),
      p.id === "default" ? null : h("button", { class: "btn small ghost danger", "aria-label": `Delete ${p.name}`, onclick: () => remove(p, root) }, icon("x", { width: 14, height: 14 }), "Delete"));
  });
  return h("section", { class: "card stack" }, h("h2", { text: "All profiles" }), ...rows);
}

async function remove(p, root) {
  if (!(await confirmDialog(`Delete ${p.name}?`, `This permanently removes the profile and every transaction, rule and model stored in it. Other profiles are not touched.`, "Delete profile", true))) return;
  try {
    const wasActive = p.id === state.profile.id;
    await api.post(`/api/profiles/${encodeURIComponent(p.id)}/delete`, { confirm: "DELETE" });
    await loadProfiles();
    toast(`${p.name} deleted.`);
    if (wasActive) window.dispatchEvent(new CustomEvent("hisaab:data"));
    else render(root);
  } catch (e) { toast(e.message); }
}

function creator() {
  const name = input("new-name", "", { required: true, maxlength: "40", placeholder: "e.g. Riya, or Demo", autocomplete: "off" });
  const budget = money("new-budget", "");
  const go = h("button", { class: "btn primary", type: "submit" }, "Create and switch");
  return h("section", { class: "card stack" }, h("h2", { text: "New profile" }),
    h("p", { class: "ink2", text: "Starts empty. Use one per person, or keep a separate one for the demo data." }),
    h("form", { class: "stack", onsubmit: async (e) => {
      e.preventDefault();
      go.disabled = true;
      try {
        const p = await api.post("/api/profiles", { name: name.value.trim(), monthly_budget: budget.value.trim() });
        await loadProfiles();
        toast(`Created ${p.name}. Now import their statements.`);
        location.hash = "#/import";
        window.dispatchEvent(new CustomEvent("hisaab:data"));
      } catch (er) { toast(er.message); go.disabled = false; }
    } }, h("div", { class: "map-grid" }, field("Name", name), field("Monthly budget (optional)", budget)), h("div", { class: "row" }, go)));
}

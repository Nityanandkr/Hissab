// Shared client state + the learning loop.
//
// THE LEARNING LOOP (what happens when you correct something):
//   1. the correction is saved INSTANTLY as a rule for that merchant (certain knowledge beats any guess)
//   2. a "learning…" banner appears and a retrain is scheduled 1.4 s after your LAST correction (so labelling 10 merchants in a row
//      trains once, not ten times)
//   3. the server refits both models on seed knowledge + all your corrections, re-applies them everywhere, and reports which OTHER
//      merchants changed category - shown to you, never silent.
import * as api from "./api.js";
import { toast } from "./ui.js";

export const state = { status: null, tax: null, reviewCount: 0, learning: false, lastLearn: null, profiles: [], profile: null };
const listeners = new Set();
export const onState = (fn) => { listeners.add(fn); return () => listeners.delete(fn); };
const notify = () => listeners.forEach((fn) => fn(state));

export async function loadTaxonomy() {
  if (!state.tax) state.tax = await api.get("/api/taxonomy");
  return state.tax;
}
// ---- profiles: each one has its own data on the server; the server remembers this browser's choice in a cookie ----------------------------------
export async function loadProfiles() {
  const r = await api.get("/api/profiles");
  state.profiles = r.profiles;
  state.profile = r.profiles.find((p) => p.id === r.active) || r.profiles[0];
  notify();
  return state.profile;
}
export async function switchProfile(id) {
  if (id === state.profile?.id) return;
  await api.post(`/api/profiles/${encodeURIComponent(id)}/select`);
  await loadProfiles();
  toast(`Switched to ${state.profile.name}.`);
  window.dispatchEvent(new CustomEvent("hisaab:data"));
}

export async function refreshStatus() {
  state.status = await api.get("/api/status");
  try { state.reviewCount = state.status.transactions ? (await api.get("/api/review?limit=1")).n_merchants : 0; } catch (e) { state.reviewCount = 0; }
  notify();
  return state.status;
}

let timer = null;
export function scheduleRetrain() {
  state.learning = true;
  notify();
  clearTimeout(timer);
  timer = setTimeout(runRetrain, 1400);
}
export async function runRetrain() {
  clearTimeout(timer);
  state.learning = true; notify();
  try {
    const r = await api.post("/api/learn/retrain");
    state.lastLearn = r;
    const n = r.changed.length;
    toast(n ? `Learned from your corrections in ${r.seconds}s. ${n} other merchant${n === 1 ? "" : "s"} changed category.` : `Learned from your corrections in ${r.seconds}s. Nothing else needed to change.`,
          n ? { link: { href: "#/model", text: "See what changed" } } : {});
    await refreshStatus();
    window.dispatchEvent(new CustomEvent("hisaab:learned", { detail: r }));
    return r;
  } catch (e) {
    toast("Could not retrain: " + e.message);
  } finally {
    state.learning = false; notify();
  }
}

// ---- category helpers ------------------------------------------------------------------------------------------------------------------------------
export function categoryItems(direction, current) {
  const t = state.tax, allowed = new Set(direction === "credit" ? t.allowed_credit : t.allowed_debit);
  const items = [];
  for (const [group, title] of [["expense", "Spending"], ["income", "Money received"], ["neutral", "Not spending"]]) {
    const cats = t.categories.filter((c) => c.group === group && allowed.has(c.name));
    if (!cats.length) continue;
    items.push({ group: title });
    for (const c of cats) items.push({ label: c.name, value: c.name, checked: c.name === current });
  }
  return items;
}

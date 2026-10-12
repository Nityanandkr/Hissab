// Teaching actions shared by Transactions, Merchants and Review.  Each one: ask -> save (instant rule) -> schedule the retrain.
import * as api from "./api.js";
import { h, popMenu, modal, toast, plural } from "./ui.js";
import { categoryItems, loadTaxonomy, scheduleRetrain, refreshStatus } from "./state.js";

/** Pick a category for a transaction row or a whole merchant. Resolves true if something was saved. */
export async function chooseCategory(anchor, { txn = null, merchantId, merchantName, direction = "debit", current = null, nTxns = null }) {
  await loadTaxonomy();
  const cat = await popMenu(anchor, categoryItems(direction, current));
  if (!cat || cat === current) return false;
  let scope = "merchant";
  if (txn) {
    const choice = await modal({
      title: `Set "${cat}"`,
      body: h("div", { class: "stack" },
        h("p", { class: "ink2", text: `${merchantName} — ${nTxns ? plural(nTxns, "payment") : "all payments"}. Should this apply to all of them, or only this one?` }),
        h("p", { class: "muted", text: "Choosing 'all' also teaches the app, so similar shops get better guesses." })),
      actions: [{ label: "Cancel", value: null }, { label: "Only this payment", value: "transaction" }, { label: `All payments to ${merchantName.length > 22 ? merchantName.slice(0, 22) + "…" : merchantName}`, value: "merchant", cls: "primary" }],
    });
    if (!choice) return false;
    scope = choice;
  }
  const res = txn ? await api.post(`/api/transactions/${txn}/category`, { category: cat, scope }) : await api.post(`/api/merchants/${merchantId}/category`, { category: cat });
  toast(`Saved: ${merchantName} → ${cat}${scope === "transaction" ? " (this payment only)" : ""}`);
  await refreshStatus();
  scheduleRetrain();
  return res;
}

export async function chooseKind(merchantId, merchantName, current) {
  const picked = await modal({
    title: `What is ${merchantName}?`,
    body: h("div", { class: "stack" },
      h("p", { class: "ink2", text: "A shop or company is tracked by what it sells. A person is a P2P transfer (rent, a friend, family)." }),
      h("p", { class: "muted", text: "Tip: a tea-seller, auto driver or mess contractor who takes money on a personal QR is a shop here." })),
    actions: [{ label: "Cancel", value: null }, { label: "Person", value: "person" }, { label: "Shop / business", value: "merchant", cls: "primary" }],
  });
  if (!picked || picked === current) return false;
  const res = await api.post(`/api/merchants/${merchantId}/kind`, { kind: picked });
  toast(`Saved: ${merchantName} is a ${picked === "person" ? "person" : "shop / business"}.`);
  await refreshStatus();
  scheduleRetrain();
  return res;
}

export async function renameMerchant(merchantId, current) {
  const input = h("input", { class: "input", value: current, maxlength: "80", style: { width: "100%" } });
  const r = await modal({ title: "Rename merchant", body: h("div", { class: "stack" }, h("p", { class: "ink2", text: "Pick a name you will recognise, for example “Raju’s tea stall”." }), input),
                          actions: [{ label: "Cancel", value: null }, { label: "Save", value: true, cls: "primary" }] });
  const name = input.value.trim();
  if (!r || !name || name === current) return false;
  await api.post(`/api/merchants/${merchantId}/rename`, { name });
  toast(`Renamed to “${name}”.`);
  return true;
}

export async function mergeMerchant(keepId, keepName, merchants) {
  const select = h("select", { class: "select", style: { width: "100%" } }, h("option", { value: "", text: "Choose the duplicate…" }),
    merchants.filter((m) => m.id !== keepId).map((m) => h("option", { value: m.id, text: `${m.name} · ${m.n} payments` })));
  const r = await modal({
    title: `Merge into ${keepName}`,
    body: h("div", { class: "stack" }, h("p", { class: "ink2", text: "Use this when two entries are really the same shop (for example the same shop with two QR codes). All payments move to this merchant; this cannot be undone." }), select),
    actions: [{ label: "Cancel", value: null }, { label: "Merge", value: true, cls: "primary" }],
  });
  if (!r || !select.value) return false;
  await api.post("/api/merchants/merge", { keep: keepId, drop: Number(select.value) });
  toast("Merged. Payments from both now count as one merchant.");
  await refreshStatus();
  scheduleRetrain();
  return true;
}

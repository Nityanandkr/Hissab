// IMPORT: file -> detected mapping (you can correct it) -> preview -> commit.  Also: demo data, delete everything.
import * as api from "../api.js";
import { h, icon, inrAuto, dmy, plural, chip, toast, confirmDialog, modal } from "../ui.js";
import { state, refreshStatus } from "../state.js";
import { loadDemo } from "./common.js";

export const keepOnLearn = true;
const FIELDS = [["date", "Date"], ["time", "Time (optional)"], ["desc", "Description / narration"], ["debit", "Debit / withdrawal"], ["credit", "Credit / deposit"],
                ["amount", "Amount (single column)"], ["type", "Dr/Cr type column"], ["balance", "Balance"], ["status", "Status (to skip failed)"], ["ref", "Reference no."]];
const MODES = [["split", "Separate debit and credit columns"], ["signed", "One amount column, debits negative"], ["type", "Amount + a Dr/Cr (or DEBIT/CREDIT) column"],
               ["suffix", "Amounts like 120.00(Dr) / 500.00(Cr)"], ["unsigned", "Amount without a sign (guess direction)"]];

export async function render(root) {
  const box = h("div", { class: "stack", style: { gap: "16px" } });
  root.replaceChildren(h("div", { class: "page-head" }, h("div", {}, h("h1", { text: "Import" }), h("div", { class: "sub", text: "Bank statements (CSV / Excel) and UPI-app exports. Files are read in memory on this computer and never uploaded anywhere." }))), box);
  const fileInput = h("input", { type: "file", accept: ".csv,.xlsx,.xls,.txt,.tsv", class: "sr-only", id: "f", onchange: (e) => e.target.files[0] && start(e.target.files[0]) });
  const drop = h("label", { class: "drop", for: "f" }, icon("upload", { width: 28, height: 28 }), h("div", { style: { fontWeight: 600, margin: "6px 0" }, text: "Drop your statement here, or click to choose" }),
    h("div", { class: "muted", text: "Download it from your bank's net-banking (Account statement → Excel/CSV). You can import several files; duplicates are skipped automatically (matched by the 12-digit UPI reference)." }));
  ["dragenter", "dragover"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("over"); }));
  ["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
  drop.addEventListener("drop", (e) => { const f = e.dataTransfer.files[0]; if (f) start(f); });
  const preview = h("div", {});
  const demoCard = h("section", { class: "card" }, h("h2", { text: "No statement handy?" }), h("p", { class: "ink2", text: "Load a built-in demo: four months of a fictional hostel student's UPI life, including tea stalls, friends, subscriptions and a few things that should look suspicious." }),
    h("button", { class: "btn", onclick: loadDemo }, "Load demo data"));
  const danger = h("section", { class: "card" }, h("h2", { text: "Delete everything" }), h("p", { class: "ink2", text: "Removes all transactions, learned rules and models from this computer. The app starts blank." }),
    h("button", { class: "btn danger", onclick: wipe }, "Delete all my data"));
  box.append(drop, fileInput, preview, state.status?.transactions ? h("div", { class: "muted", text: `Currently stored: ${plural(state.status.transactions, "transaction")} (${dmy(state.status.date_min, true)} – ${dmy(state.status.date_max, true)}).` }) : null,
    h("div", { class: "grid g2" }, demoCard, danger));

  async function start(file) {
    preview.replaceChildren(h("div", { class: "row muted" }, h("span", { class: "spin" }), `Reading ${file.name}…`));
    try { show(file, await api.upload("/api/import/preview", file)); } catch (e) { preview.replaceChildren(h("div", { class: "banner warn" }, icon("alert", { width: 16, height: 16 }), e.message)); }
  }
  function show(file, p) {
    const m = p.mapping, rep = p.report;
    const cols = (m.headers || []).map((t, i) => [i, t || `column ${i + 1}`]);
    const sel = (field) => {
      const cur = (m.cols[field] || [])[0];
      return h("label", {}, FIELDS.find((x) => x[0] === field)[1], h("select", { class: "select", onchange: async (e) => {
        if (e.target.value === "") delete m.cols[field]; else m.cols[field] = [Number(e.target.value)];
        try { show(file, await api.upload("/api/import/preview", file, { mapping: m })); } catch (er) { toast(er.message); }
      } }, h("option", { value: "", text: "— none —" }), cols.map(([i, t]) => h("option", { value: i, selected: i === cur ? "" : null, text: t }))));
    };
    const modeSel = h("label", {}, "How debit / credit is shown", h("select", { class: "select", onchange: async (e) => { m.amount_mode = e.target.value; show(file, await api.upload("/api/import/preview", file, { mapping: m })); } },
      MODES.map(([v, t]) => h("option", { value: v, selected: v === m.amount_mode ? "" : null, text: t }))));
    const swap = h("label", { class: "row", style: { gap: "6px" } }, h("input", { type: "checkbox", checked: m.invert ? "" : null, onchange: async (e) => { m.invert = e.target.checked; show(file, await api.upload("/api/import/preview", file, { mapping: m })); } }), "Swap debit and credit (if the totals look upside-down)");
    const warns = [...new Set(rep.warnings)].map((w) => h("div", { class: "banner warn" }, icon("alert", { width: 16, height: 16 }), w));
    const sample = h("div", { class: "tbl-wrap" }, h("table", { class: "tbl" }, h("thead", {}, h("tr", {}, ["Date", "Time", "Description", "Amount"].map((c, i) => h("th", { class: i === 3 ? "right" : "", text: c })))),
      h("tbody", {}, p.sample.map((r) => h("tr", {}, h("td", { class: "nowrap", text: dmy(r.date, true) }), h("td", { class: "muted", text: r.time }), h("td", { style: { overflowWrap: "anywhere" }, text: r.description }),
        h("td", { class: `right amt ${r.direction}`, text: (r.direction === "credit" ? "+" : "−") + inrAuto(r.amount) }))))));
    const newRows = p.n_rows - p.n_duplicates;
    preview.replaceChildren(h("section", { class: "card stack" },
      h("div", { class: "row wrap" }, h("h2", { text: file.name }), chip(`${plural(p.n_rows, "row")} found`, "accent"), chip(`${dmy(rep.date_min, true)} – ${dmy(rep.date_max, true)}`), rep.newest_first ? chip("newest first — reordered") : null,
        p.n_duplicates ? chip(`${plural(p.n_duplicates, "duplicate")} already stored`, "warn") : null),
      rep.balance_checked ? h("div", { class: "muted", text: `Check: the running balance agrees with the amounts on ${rep.balance_matched} of ${rep.balance_checked} rows${rep.balance_matched / rep.balance_checked > 0.95 ? " — the debit/credit columns look right." : "."}` }) : null,
      ...warns,
      h("details", { class: "details", open: m.confidence < 0.8 ? "" : null }, h("summary", { text: `Detected columns (confidence ${Math.round(m.confidence * 100)}%) — change if something is wrong` }),
        h("div", { class: "map-grid", style: { marginTop: "10px" } }, modeSel, FIELDS.map(([f]) => sel(f))), h("div", { style: { marginTop: "8px" } }, swap)),
      h("div", { class: "muted", text: "First rows as I understood them:" }), sample,
      h("div", { class: "row" }, h("button", { class: "btn primary", disabled: newRows ? null : "", onclick: (e) => commit(file, m, e.currentTarget) }, newRows ? `Import ${plural(newRows, "new transaction")}` : "Nothing new to import"),
        h("button", { class: "btn ghost", onclick: () => preview.replaceChildren() }, "Cancel"))));
  }
  async function commit(file, mapping, btn) {
    btn.disabled = true; btn.textContent = "Importing…";
    try {
      const r = await api.upload("/api/import/commit", file, { mapping });
      await refreshStatus();
      preview.replaceChildren(h("section", { class: "card stack" }, h("h2", { text: "Imported ✓" }),
        h("p", { class: "ink2", text: `${plural(r.new, "new transaction")} added${r.duplicates ? `, ${plural(r.duplicates, "duplicate")} skipped` : ""}${r.time_added_to_existing ? `, time of day added to ${r.time_added_to_existing} existing rows` : ""}. Now the app has worked out the merchants, shop-or-person and categories.` }),
        h("div", { class: "row" }, h("a", { class: "btn primary", href: "#/dashboard" }, "Open dashboard"), h("a", { class: "btn", href: "#/review" }, "Review uncertain merchants"))));
    } catch (e) { toast(e.message); btn.disabled = false; btn.textContent = "Try again"; }
  }
  async function wipe() {
    if (!(await confirmDialog("Delete all data?", "This permanently removes every transaction, rule and model stored by Hisaab on this computer.", "Delete everything", true))) return;
    await api.post("/api/reset", { confirm: "DELETE" });
    await refreshStatus();
    toast("All data deleted.");
    window.dispatchEvent(new CustomEvent("hisaab:data"));
  }
}

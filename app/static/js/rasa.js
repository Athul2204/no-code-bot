(function () {
  const { h, api, $, $$, clear, toast, modal, confirmDialog, withLoading, reportError, emptyState, loadingBlock, errorBlock, fmtDate, field, select } = App;
  const root = $("#app");
  let imports = [], bots = [];

  async function load() {
    clear(root).append(loadingBlock());
    try { [imports, bots] = [(await api.get("/api/rasa/imports")).imports, (await api.get("/api/chatbots")).chatbots]; }
    catch (e) { clear(root).append(errorBlock(e.message, load)); return; }
    render();
  }

  function render() {
    clear(root).append(
      h("div", { class: "mb-6 rounded-xl border border-slate-200 bg-white p-4 text-sm text-slate-600" }, "Your original files are stored exactly as uploaded and are never modified. Exports are generated as new files that you can preview and download."),
      importCard(), h("h2", { class: "mb-3 mt-8 text-base font-semibold" }, "Imported files"), listCard(), h("h2", { class: "mb-3 mt-8 text-base font-semibold" }, "Export a Rasa project"), exportCard());
  }

  function importCard() {
    const input = h("input", { type: "file", multiple: true, accept: ".yml,.yaml", class: "block w-full text-sm text-slate-600 file:mr-3 file:rounded-lg file:border-0 file:bg-slate-100 file:px-3 file:py-2 file:text-sm file:font-medium hover:file:bg-slate-200" });
    const go = h("button", { class: "btn-primary", type: "button" }, "Import files");
    const sample = h("button", { class: "btn-secondary", type: "button" }, "Import the sample Rasa bot");
    go.addEventListener("click", () => withLoading(go, async () => {
      if (!input.files.length) { toast("Choose one or more .yml / .yaml files first.", "error"); return; }
      const fd = new FormData(); Array.from(input.files).forEach((f) => fd.append("files", f));
      try { const r = await api.upload("/api/rasa/import", fd); toast(`Imported ${r.imports.length} file(s).`, "success"); load(); } catch (e) { reportError(e); }
    }));
    sample.addEventListener("click", () => withLoading(sample, async () => {
      try { const r = await api.post("/api/rasa/import-sample"); toast(`Imported ${r.imports.length} sample file(s).`, "success"); load(); } catch (e) { reportError(e); }
    }));
    return h("div", { class: "card space-y-3 p-5" }, h("h2", { class: "text-base font-semibold" }, "Import existing Rasa files"),
      h("p", { class: "text-sm text-slate-500" }, "Select domain.yml, nlu.yml, rules.yml, stories.yml, config.yml… (max 1 MB each)."), input, h("div", { class: "flex flex-wrap gap-2" }, go, sample));
  }

  function summaryText(s) {
    const parts = [];
    if (s.kinds && s.kinds.length) parts.push(s.kinds.join(", "));
    if (s.intents && s.intents.length) parts.push(`${s.intents.length} intent(s)`);
    const r = Object.keys(s.responses || {}).length; if (r) parts.push(`${r} response(s)`);
    for (const [k, v] of Object.entries(s.counts || {})) parts.push(`${v} ${k.replace(/_/g, " ")}`);
    return parts.join(" · ") || "No recognised Rasa content";
  }

  function listCard() {
    if (!imports.length) return emptyState("Nothing imported yet", "Import files above, or try the sample bot to see how it works.");
    return h("div", { class: "card overflow-hidden" }, h("ul", { class: "divide-y divide-slate-100" }, imports.map((i) => {
      const hasResp = Object.keys((i.summary || {}).responses || {}).length > 0;
      return h("li", { class: "flex flex-wrap items-center gap-3 px-4 py-3 text-sm" },
        h("div", { class: "min-w-0 flex-1" }, h("div", { class: "flex flex-wrap items-center gap-2" }, h("span", { class: "font-medium" }, i.filename), i.parse_ok ? h("span", { class: "badge-green" }, "valid YAML") : h("span", { class: "badge-red" }, "invalid YAML")),
          h("p", { class: "text-xs " + (i.parse_ok ? "text-slate-500" : "text-red-600") }, i.parse_ok ? summaryText(i.summary || {}) : i.parse_error), h("p", { class: "text-xs text-slate-400" }, `${(i.size / 1024).toFixed(1)} KB · ${fmtDate(i.created_at)}`)),
        h("div", { class: "flex flex-wrap gap-2" },
          h("button", { class: "btn-secondary btn-sm", type: "button", onclick: () => view(i) }, "View original"),
          hasResp ? h("button", { class: "btn-secondary btn-sm", type: "button", onclick: (e) => makeActions(i, e.currentTarget) }, "Create actions from responses") : null,
          h("button", { class: "btn-ghost btn-sm text-red-600 hover:bg-red-50", type: "button", onclick: () => remove(i) }, "Delete")));
    })));
  }

  async function view(i) {
    const body = h("div", {}, loadingBlock()); modal(i.filename, body, { wide: true });
    try { const full = await api.get("/api/rasa/imports/" + i.id); clear(body).append(h("p", { class: "mb-2 text-xs text-slate-500" }, "SHA-256: " + full.sha256), h("pre", { class: "max-h-[60vh] overflow-auto rounded-lg bg-slate-900 p-3 text-xs text-slate-100" }, full.content)); }
    catch (e) { clear(body).append(errorBlock(e.message)); }
  }
  async function makeActions(i, btn) {
    await withLoading(btn, async () => {
      try {
        const r = await api.post(`/api/rasa/imports/${i.id}/create-actions`);
        toast(`Created ${r.created.length} action(s)` + (r.skipped.length ? `, skipped ${r.skipped.length} that already exist.` : "."), r.created.length ? "success" : "info");
      } catch (e) { reportError(e); }
    });
  }
  async function remove(i) {
    if (!(await confirmDialog("Delete imported file?", `"${i.filename}" will be removed from this app. Your original file on disk is untouched.`, "Delete", true))) return;
    try { await api.del("/api/rasa/imports/" + i.id); toast("Deleted.", "success"); load(); } catch (e) { reportError(e); }
  }

  function exportCard() {
    if (!bots.length) return emptyState("Create a chatbot first", "Exports are generated from a chatbot's enabled actions plus the files you import.", [h("a", { class: "btn-primary", href: "/chatbots/new" }, "Create a chatbot")]);
    const botSel = select(bots.map((b) => [b.id, b.name]), bots[0].id, { "aria-label": "Chatbot" });
    const valid = imports.filter((i) => i.parse_ok);
    const checks = valid.map((i) => h("label", { class: "flex items-center gap-2 text-sm" }, h("input", { type: "checkbox", value: i.id, checked: true, class: "h-4 w-4 rounded border-slate-300 text-brand-600" }), i.filename));
    const out = h("div", { class: "mt-4" });
    const preview = h("button", { class: "btn-primary", type: "button" }, "Preview export");
    const download = h("button", { class: "btn-secondary", type: "button", disabled: true }, "Download .zip");
    const payload = () => ({ chatbot_id: parseInt(botSel.value, 10), import_ids: checks.map((c) => $("input", c)).filter((i) => i.checked).map((i) => parseInt(i.value, 10)) });
    preview.addEventListener("click", () => withLoading(preview, async () => {
      try {
        const r = await api.post("/api/rasa/export/preview", payload());
        download.disabled = !r.validation.ok;
        clear(out).append(
          h("div", { class: "rounded-lg border px-3 py-2 text-sm " + (r.validation.ok ? "border-emerald-200 bg-emerald-50 text-emerald-900" : "border-red-200 bg-red-50 text-red-800") }, r.validation.ok ? "Validation passed." : "Validation found problems. Fix them before downloading."),
          (r.validation.errors || []).length ? h("ul", { class: "mt-2 list-disc pl-5 text-sm text-red-700" }, r.validation.errors.map((x) => h("li", {}, x))) : null,
          (r.validation.warnings || []).length ? h("ul", { class: "mt-2 list-disc pl-5 text-sm text-amber-700" }, r.validation.warnings.map((x) => h("li", {}, x))) : null,
          (r.notes || []).length ? h("ul", { class: "mt-2 list-disc pl-5 text-xs text-slate-500" }, r.notes.map((x) => h("li", {}, x))) : null,
          h("div", { class: "mt-3 space-y-2" }, Object.entries(r.files).map(([name, content]) => h("details", { class: "rounded-lg border border-slate-200" }, h("summary", { class: "cursor-pointer px-3 py-2 text-sm font-medium" }, name),
            h("pre", { class: "max-h-72 overflow-auto rounded-b-lg bg-slate-900 p-3 text-xs text-slate-100" }, content)))));
      } catch (e) { reportError(e); }
    }));
    download.addEventListener("click", () => withLoading(download, async () => {
      try {
        const res = await api.raw("POST", "/api/rasa/export/download", payload());
        const blob = await res.blob(); const a = h("a", { href: URL.createObjectURL(blob), download: "rasa-export.zip" }); document.body.append(a); a.click(); a.remove(); toast("Download started.", "success");
      } catch (e) { reportError(e); }
    }));
    return h("div", { class: "card space-y-4 p-5" }, field("Chatbot", botSel, "The chatbot's enabled actions and welcome message are included."),
      valid.length ? h("div", {}, h("p", { class: "label" }, "Include imported files"), h("div", { class: "space-y-1.5" }, checks)) : h("p", { class: "text-sm text-slate-500" }, "No imported files selected. The export will contain only what's generated from the chatbot."),
      h("div", { class: "flex gap-2" }, preview, download), out);
  }
  load();
})();

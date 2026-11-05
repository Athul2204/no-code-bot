(function () {
  const { h, api, $, clear, toast, modal, confirmDialog, withLoading, reportError, clearFieldErrors, emptyState, loadingBlock, errorBlock, fmtDate, field, select, dataTable } = App;
  const root = $("#app");
  let state = { connections: [], engines: [], files: [] };

  async function load() {
    clear(root).append(loadingBlock());
    try { state = await api.get("/api/connections"); } catch (e) { clear(root).append(errorBlock(e.message, load)); return; }
    render();
  }

  function render() {
    clear(root);
    root.append(h("div", { class: "grid gap-6 lg:grid-cols-3" },
      h("div", { class: "space-y-4 lg:col-span-2" }, listView()),
      h("div", {}, addForm())));
  }

  // ----------------------------------------------------------------------------- list
  function listView() {
    if (!state.connections.length) return emptyState("No databases connected", "Add the included sample store database or upload your own SQLite file using the form.");
    return state.connections.map(connCard);
  }

  function connCard(c) {
    const body = h("div", { class: "mt-4 hidden border-t border-slate-100 pt-4" });
    const toggle = h("button", { class: "btn-secondary btn-sm", type: "button" }, "Browse tables");
    toggle.addEventListener("click", async () => {
      const open = body.classList.toggle("hidden") === false;
      toggle.textContent = open ? "Hide tables" : "Browse tables";
      if (open && !body.dataset.loaded) { body.dataset.loaded = "1"; await schemaBrowser(c, body); }
    });
    const writeBadge = h("span", { class: c.allow_writes ? "badge-amber" : "badge-green" }, c.allow_writes ? "writes allowed" : "read-only");
    const testBtn = h("button", { class: "btn-secondary btn-sm", type: "button" }, "Test connection");
    testBtn.addEventListener("click", () => withLoading(testBtn, async () => {
      try { const r = await api.post(`/api/connections/${c.id}/test`); toast(r.message, r.ok ? "success" : "error"); } catch (e) { reportError(e); }
    }));
    const writeBtn = h("button", { class: "btn-ghost btn-sm", type: "button" }, c.allow_writes ? "Make read-only" : "Allow writes…");
    writeBtn.addEventListener("click", async () => {
      const enabling = !c.allow_writes;
      if (enabling && !(await confirmDialog("Allow writes on this database?",
        "Chatbots will be able to insert or update rows, but only through actions you approve, and only after the user confirms each change in chat. Test mode never changes real data.\n\nYou can switch this off at any time.", "Allow writes"))) return;
      await withLoading(writeBtn, async () => { try { await api.post(`/api/connections/${c.id}/writes`, { allow: enabling }); toast(enabling ? "Writes enabled." : "Connection is read-only again.", "success"); load(); } catch (e) { reportError(e); } });
    });
    const delBtn = h("button", { class: "btn-ghost btn-sm text-red-600 hover:bg-red-50", type: "button" }, "Delete");
    delBtn.addEventListener("click", async () => {
      if (!(await confirmDialog("Delete connection?", `"${c.name}" will be removed. The database file itself is not deleted. Actions using it will stop working.`, "Delete", true))) return;
      try { await api.del("/api/connections/" + c.id); toast("Connection deleted.", "success"); load(); } catch (e) { reportError(e); }
    });
    return h("div", { class: "card p-5" },
      h("div", { class: "flex flex-wrap items-start justify-between gap-2" },
        h("div", {}, h("h3", { class: "text-base font-semibold" }, c.name),
          h("p", { class: "text-xs text-slate-500" }, `${c.db_type.toUpperCase()}${c.config.file ? " · " + c.config.file : ""} · added ${fmtDate(c.created_at)}`)), writeBadge),
      h("div", { class: "mt-3 flex flex-wrap gap-2" }, toggle, testBtn, writeBtn, delBtn), body);
  }

  // ----------------------------------------------------------------------------- schema browser + preview
  async function schemaBrowser(c, body) {
    body.append(loadingBlock("Reading tables…"));
    let tables;
    try { tables = (await api.get(`/api/connections/${c.id}/schema`)).tables; } catch (e) { clear(body).append(errorBlock(e.message)); return; }
    clear(body);
    if (!tables.length) { body.append(h("p", { class: "text-sm text-slate-500" }, "This database has no tables.")); return; }
    const tableSel = select(tables.map((t) => [t.name, t.name]), tables[0].name, { "aria-label": "Table" });
    const colBox = h("div", { class: "mt-3" });
    const preview = h("div", { class: "mt-3" });
    const previewBtn = h("button", { class: "btn-primary btn-sm", type: "button" }, "Preview rows");

    function drawColumns() {
      const t = tables.find((x) => x.name === tableSel.value);
      clear(preview);
      clear(colBox).append(h("p", { class: "mb-2 text-xs font-medium uppercase tracking-wide text-slate-500" }, "Columns (tick the ones to preview)"),
        h("div", { class: "grid gap-1.5 sm:grid-cols-2" }, t.columns.map((col) => h("label", { class: "flex items-center gap-2 text-sm" },
          h("input", { type: "checkbox", checked: true, value: col.name, class: "h-4 w-4 rounded border-slate-300 text-brand-600" }),
          h("span", {}, col.name), h("span", { class: "text-xs text-slate-400" }, (col.type || "any").toLowerCase() + (col.pk ? " · key" : "") + (col.notnull && !col.pk ? " · required" : ""))))));
    }
    tableSel.addEventListener("change", drawColumns);
    previewBtn.addEventListener("click", () => withLoading(previewBtn, async () => {
      const cols = App.$$("input[type=checkbox]:checked", colBox).map((i) => i.value);
      if (!cols.length) { toast("Tick at least one column.", "error"); return; }
      try { const r = await api.post(`/api/connections/${c.id}/preview`, { table: tableSel.value, columns: cols, limit: 10 }); clear(preview).append(dataTable(r.rows), h("p", { class: "hint" }, "Showing up to 10 rows (read-only preview).")); }
      catch (e) { reportError(e); }
    }));
    body.append(h("div", { class: "flex flex-wrap items-center gap-2" }, h("span", { class: "text-sm font-medium" }, "Table"), tableSel, previewBtn), colBox, preview);
    drawColumns();
  }

  // ----------------------------------------------------------------------------- add form
  function addForm() {
    const engines = state.engines;
    const type = select(engines.map((e) => [e.value, e.label + (e.available ? "" : " (coming soon)")]), "sqlite", { name: "db_type" });
    engines.forEach((e, i) => { if (!e.available) type.options[i].disabled = true; });
    const name = h("input", { class: "input", name: "name", type: "text", maxlength: 80, placeholder: "e.g. Store database" });
    const fileSel = h("select", { class: "input", name: "file" });
    const fillFiles = (selected) => {
      clear(fileSel);
      if (!state.files.length) fileSel.append(h("option", { value: "" }, "No database files yet. Upload one below"));
      state.files.forEach((f) => fileSel.append(h("option", { value: f, selected: f === selected }, f)));
    };
    fillFiles(state.files.includes("sample_store.db") ? "sample_store.db" : state.files[0]);
    const upload = h("input", { type: "file", accept: ".db,.sqlite,.sqlite3", class: "block w-full text-sm text-slate-600 file:mr-3 file:rounded-lg file:border-0 file:bg-slate-100 file:px-3 file:py-2 file:text-sm file:font-medium hover:file:bg-slate-200" });
    const uploadBtn = h("button", { class: "btn-secondary btn-sm", type: "button" }, "Upload file");
    uploadBtn.addEventListener("click", () => withLoading(uploadBtn, async () => {
      if (!upload.files.length) { toast("Choose a .db, .sqlite or .sqlite3 file first.", "error"); return; }
      const fd = new FormData(); fd.append("file", upload.files[0]);
      try { const r = await api.upload("/api/connections/upload", fd); state.files = r.files; fillFiles(r.file); upload.value = ""; toast("Uploaded " + r.file, "success"); } catch (e) { reportError(e); }
    }));
    const result = h("div", { class: "text-sm" });
    const testBtn = h("button", { class: "btn-secondary", type: "button" }, "Test");
    const saveBtn = h("button", { class: "btn-primary", type: "submit" }, "Save connection");
    const payload = () => ({ name: name.value, db_type: type.value, config: { file: fileSel.value } });
    const form = h("form", { class: "card space-y-4 p-5", novalidate: true, onsubmit: (e) => { e.preventDefault(); save(); } },
      h("h2", { class: "text-base font-semibold" }, "Add a connection"),
      field("Name *", name, null, "name"), field("Database type", type, "MySQL and PostgreSQL are planned.", "db_type"),
      field("Database file", fileSel, "Files live in the server's data/user_databases folder.", "file"),
      h("div", { class: "space-y-2 rounded-lg bg-slate-50 p-3" }, h("p", { class: "text-xs font-medium text-slate-600" }, "Or upload a SQLite file (max 10 MB)"), upload, uploadBtn),
      h("p", { class: "rounded-lg bg-emerald-50 px-3 py-2 text-xs text-emerald-800" }, "New connections are read-only. You decide later whether chatbots may change data."),
      h("div", { class: "flex gap-2" }, saveBtn, testBtn), result);
    testBtn.addEventListener("click", () => withLoading(testBtn, async () => {
      clearFieldErrors(form);
      try { const r = await api.post("/api/connections/test", payload()); clear(result).append(h("p", { class: r.ok ? "text-emerald-700" : "text-red-700" }, r.message)); } catch (e) { reportError(e, form); }
    }));
    async function save() {
      clearFieldErrors(form); clear(result);
      await withLoading(saveBtn, async () => { try { await api.post("/api/connections", payload()); toast("Connection saved.", "success"); load(); } catch (e) { reportError(e, form); } });
    }
    return form;
  }
  load();
})();

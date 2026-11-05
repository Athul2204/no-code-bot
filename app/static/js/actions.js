(function () {
  const { h, api, $, $$, clear, toast, modal, confirmDialog, withLoading, reportError, clearFieldErrors, emptyState, loadingBlock, errorBlock, fmtDate, field, select, renderData } = App;
  const root = $("#app");
  const pageActions = $("#page-actions");

  let S = { actions: [], connections: [], templates: [], pythonEnabled: false };
  let viewMode = "flow"; // "flow" (canvas editor, default) | "list"
  const schemaCache = {};

  const TYPE_LABELS = [["string", "Text"], ["integer", "Whole number"], ["number", "Number"], ["boolean", "Yes / No"]];
  const OPERATORS = [["eq", "is equal to"], ["neq", "is not"], ["contains", "contains"], ["gt", "is greater than"], ["gte", "is at least"], ["lt", "is less than"], ["lte", "is at most"]];
  const ICONS = { search: "🔎", id: "#️⃣", plus: "➕", edit: "✏️", globe: "🌐", message: "💬", wrench: "🛠️" };

  const slug = (t, max) => { let s = String(t || "").toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, ""); if (!s || !/^[a-z]/.test(s)) s = "field_" + s; return s.slice(0, max || 40).replace(/_+$/, "") || "field"; };
  const colType = (c) => { const t = (c.type || "").toUpperCase(); if (t.includes("INT")) return "integer"; if (/REAL|FLOA|DOUB|NUM|DEC/.test(t)) return "number"; return "string"; };

  // ============================================================================= list view
  async function load() {
    clear(root).append(loadingBlock());
    try {
      const [a, c, t] = await Promise.all([api.get("/api/actions"), api.get("/api/connections"), api.get("/api/action-templates")]);
      S = { actions: a.actions, connections: c.connections, templates: t.templates, pythonEnabled: t.python_enabled };
    } catch (e) { clear(root).append(errorBlock(e.message, load)); return; }
    showList();
  }

  function showList() {
    const tab = (mode, label) => h("button", { type: "button", class: viewMode === mode ? "btn-primary btn-sm" : "btn-secondary btn-sm", onclick: () => { viewMode = mode; showList(); } }, label);
    clear(pageActions).append(...[h("div", { class: "flex gap-1" }, tab("list", "List"), tab("flow", "Flow")), viewMode === "flow" ? null : h("button", { class: "btn-primary", type: "button", onclick: showPicker }, "+ New action")].filter(Boolean));
    clear(root);
    if (viewMode === "flow") {
      const box = h("div", {});
      root.append(box);
      App.flowEditor.mount(box, { actions: S.actions, templates: S.templates, connections: S.connections, icons: ICONS, describe,
        newAction: (type, pos) => showBuilder(type, null, pos), edit: (a) => showBuilder(a.template_type, a),
        test: (a) => App.testAction(a), approve, remove });
      root.append(h("h2", { class: "mb-3 mt-10 text-base font-semibold" }, "Execution log"), logPanel());
      return;
    }
    if (!S.actions.length) root.append(emptyState("No actions yet", "Actions let your chatbot look things up, add records, call web services or reply with fixed text. Pick a template to begin.", [h("button", { class: "btn-primary", type: "button", onclick: showPicker }, "Create your first action")]));
    else root.append(h("div", { class: "space-y-4" }, S.actions.map(actionCard)));
    root.append(h("h2", { class: "mb-3 mt-10 text-base font-semibold" }, "Execution log"), logPanel());
  }

  function describe(a) {
    const c = a.config, p = a.parameters.filter((x) => !(x.extra && x.extra.role === "key")).map((x) => x.label);
    switch (a.template_type) {
      case "db_search": return `Searches "${c.table}" in ${a.connection_name || "a database"} by ${p.join(", ") || "—"}. Read-only.`;
      case "db_get_by_id": return `Looks up one record in "${c.table}" by ${c.id_column}. Read-only.`;
      case "db_insert": return `ADDS a new row to "${c.table}" in ${a.connection_name} using: ${p.join(", ")}.`;
      case "db_update": return `CHANGES existing rows in "${c.table}" (found by ${c.id_column}): can set ${p.join(", ")}.`;
      case "rest_api": return `Sends a ${c.method} request to ${c.url}${c.method === "GET" ? "" : ". This can change data on that service"}.`;
      case "static_response": return "Replies with fixed text.";
      default: return c.python_code ? "Runs approved advanced Python in an isolated process." : "Builds a reply from a template.";
    }
  }

  function actionCard(a) {
    const needsApproval = a.is_write;
    const toggle = h("input", { type: "checkbox", checked: a.enabled, class: "h-4 w-4 rounded border-slate-300 text-brand-600", "aria-label": "Enabled" });
    toggle.addEventListener("change", async () => {
      try { await api.post(`/api/actions/${a.id}/enabled`, { enabled: toggle.checked }); a.enabled = toggle.checked; toast(toggle.checked ? "Action enabled." : "Action disabled.", "success"); }
      catch (e) { toggle.checked = !toggle.checked; reportError(e); }
    });
    const badges = [h("span", { class: "badge-gray" }, a.template_label), h("span", { class: a.is_write ? "badge-amber" : "badge-green" }, a.is_write ? "changes data" : "read-only")];
    if (a.requires_confirmation && !a.is_write) badges.push(h("span", { class: "badge-blue" }, "asks for confirmation"));
    if (needsApproval) badges.push(h("span", { class: a.approved ? "badge-green" : "badge-red" }, a.approved ? "approved" : "needs your approval"));
    if (!a.enabled) badges.push(h("span", { class: "badge-red" }, "disabled"));
    const btns = [h("button", { class: "btn-primary btn-sm", type: "button", onclick: () => App.testAction(a) }, "Test"),
      h("button", { class: "btn-secondary btn-sm", type: "button", onclick: () => showBuilder(a.template_type, a) }, "Edit")];
    if (needsApproval) btns.push(h("button", { class: a.approved ? "btn-ghost btn-sm" : "btn-secondary btn-sm", type: "button", onclick: () => approve(a) }, a.approved ? "Revoke approval" : "Review & approve"));
    btns.push(h("button", { class: "btn-ghost btn-sm text-red-600 hover:bg-red-50", type: "button", onclick: () => remove(a) }, "Delete"));
    return h("div", { class: "card p-5" },
      h("div", { class: "flex flex-wrap items-start justify-between gap-3" },
        h("div", { class: "min-w-0" }, h("h3", { class: "text-base font-semibold" }, a.name), h("p", { class: "mt-0.5 text-sm text-slate-600" }, a.description)),
        h("label", { class: "flex items-center gap-2 text-sm text-slate-600" }, toggle, "Enabled")),
      h("div", { class: "mt-2 flex flex-wrap gap-1.5" }, badges),
      h("p", { class: "mt-3 rounded-lg bg-slate-50 px-3 py-2 text-xs text-slate-600" }, describe(a)),
      a.when_to_use ? h("p", { class: "mt-2 text-xs text-slate-500" }, h("span", { class: "font-medium" }, "Used when: "), a.when_to_use) : null,
      h("p", { class: "mt-2 text-xs text-slate-500" }, h("span", { class: "font-medium" }, "Intent: "), h("span", { class: "font-mono" }, a.intent || ""),
        (a.commands || []).length ? h("span", {}, " · Commands: ", (a.commands || []).slice(0, 4).join(", ")) : null,
        (a.rules || []).length ? h("span", {}, ` · ${a.rules.length} rule${a.rules.length === 1 ? "" : "s"}`) : null),
      h("div", { class: "mt-4 flex flex-wrap gap-2 border-t border-slate-100 pt-4" }, btns));
  }

  async function approve(a) {
    if (a.approved) {
      try { await api.post(`/api/actions/${a.id}/approve`, { approved: false }); toast("Approval revoked. Chatbots can no longer use this action.", "success"); load(); } catch (e) { reportError(e); }
      return;
    }
    const msg = `${a.name}\n\n${describe(a)}\n\nIf you approve, chatbots that have this action ticked can ask to run it. Even then, every single use needs the end user to click Confirm, and test mode only simulates the change.`;
    if (!(await confirmDialog("Approve this action?", msg, "Approve"))) return;
    try { await api.post(`/api/actions/${a.id}/approve`, { approved: true }); toast("Action approved.", "success"); load(); } catch (e) { reportError(e); }
  }
  async function remove(a) {
    if (!(await confirmDialog("Delete action?", `"${a.name}" will be removed from every chatbot that uses it.`, "Delete", true))) return;
    try { await api.del("/api/actions/" + a.id); toast("Action deleted.", "success"); load(); } catch (e) { reportError(e); }
  }

  // ----------------------------------------------------------------------------- log
  function logPanel() {
    const box = h("div", { class: "card overflow-hidden" }, loadingBlock());
    const refresh = async () => {
      try {
        const { logs } = await api.get("/api/logs?limit=30");
        clear(box);
        if (!logs.length) { box.append(h("p", { class: "p-5 text-sm text-slate-500" }, "Nothing has run yet.")); return; }
        box.append(h("div", { class: "overflow-x-auto" }, h("table", { class: "table-simple" },
          h("thead", {}, h("tr", {}, ["When", "Action", "Result", "Summary", "Source"].map((x) => h("th", {}, x)))),
          h("tbody", { class: "divide-y divide-slate-100" }, logs.map((l) => h("tr", {},
            h("td", { class: "whitespace-nowrap text-xs" }, fmtDate(l.created_at)), h("td", { class: "font-medium" }, l.action_name),
            h("td", {}, h("span", { class: l.status === "ok" ? "badge-green" : l.status === "error" || l.status === "rejected" ? "badge-red" : "badge-amber" }, l.status.replace("_", " ")), l.test_mode ? h("span", { class: "badge-gray ml-1" }, "test") : null),
            h("td", { class: "max-w-xs truncate" }, l.result_summary || l.error || ""), h("td", { class: "text-xs text-slate-500" }, l.source)))))));
      } catch (e) { clear(box).append(errorBlock(e.message, refresh)); }
    };
    refresh();
    return box;
  }

  // ============================================================================= template picker
  /** "+ New action": the template picker now lives in the canvas palette (left side). */
  function showPicker() {
    viewMode = "flow"; showList();
    toast("Drag an action from the left panel onto the canvas, or click one to add it.", "info");
  }

  // ============================================================================= parameter editor
  /** mode: "column" (database templates) | "plain". Returns {node, collect, addRow}. */
  function paramEditor(o) {
    const rows = [];
    const list = h("div", { class: "space-y-3", "data-field": "parameters" });
    const addBtn = h("button", { class: "btn-secondary btn-sm", type: "button", onclick: () => addRow({}) }, "+ Add input");
    const extraBtns = [];
    if (o.addAll) extraBtns.push(h("button", { class: "btn-ghost btn-sm", type: "button", onclick: () => { rows.filter((r) => r.col && !r.col.value).forEach((r) => { rows.splice(rows.indexOf(r), 1); r.el.remove(); }); o.columns.filter((c) => !c.pk).forEach((c) => { if (!rows.some((r) => r.col && r.col.value === c.name)) addRow({ maps_to: c.name, label: c.name.replace(/_/g, " ") }); }); reindex(); } }, "Add all columns"));
    const empty = h("p", { class: "rounded-lg border border-dashed border-slate-300 p-4 text-center text-sm text-slate-500" }, o.emptyText || "No inputs yet.");

    function reindex() {
      empty.classList.toggle("hidden", rows.length > 0);
      rows.forEach((r, i) => { $$("[data-key]", r.el).forEach((c) => c.setAttribute("data-field", `parameters.${i}.${c.dataset.key}`)); r.title.textContent = "Input " + (i + 1); });
    }
    function addRow(p) {
      const x = p.extra || {};
      const r = { initialName: p.name || "" };
      const k = (key, el) => { el.setAttribute("data-key", key); return el; };
      r.label = k("label", h("input", { class: "input", type: "text", maxlength: 60, value: p.label || "", placeholder: o.mode === "column" ? "Name shown to the AI, e.g. Max price" : "e.g. City" }));
      r.desc = k("description", h("input", { class: "input", type: "text", maxlength: 200, value: p.description || "", placeholder: "Optional: help the AI understand this input" }));
      r.required = h("input", { type: "checkbox", checked: !!p.required, class: "h-4 w-4 rounded border-slate-300 text-brand-600" });
      const cells = [];
      if (o.mode === "column") {
        r.col = k("maps_to", select([["", "Choose a column…"]].concat(o.columns.map((c) => [c.name, c.name])), p.maps_to || ""));
        r.col.addEventListener("change", () => { if (!r.label.value && r.col.value) r.label.value = r.col.value.replace(/_/g, " "); });
        cells.push(h("div", {}, h("label", { class: "label" }, "Database column"), r.col));
        if (o.operator) { r.op = k("operator", select(OPERATORS, x.operator || "eq")); cells.push(h("div", {}, h("label", { class: "label" }, "Match when value"), r.op)); }
      } else {
        r.name = k("name", h("input", { class: "input font-mono", type: "text", maxlength: 40, value: p.name || "", placeholder: "auto from label" }));
        let touched = !!p.name;
        r.name.addEventListener("input", () => { touched = true; });
        r.label.addEventListener("input", () => { if (!touched) r.name.value = slug(r.label.value); });
        r.type = k("param_type", select(TYPE_LABELS, p.param_type || "string"));
        cells.push(h("div", {}, h("label", { class: "label" }, "Field name"), r.name), h("div", {}, h("label", { class: "label" }, "Type"), r.type));
        if (o.locations) { r.loc = k("location", select(o.locations, x.location || "query")); cells.push(h("div", {}, h("label", { class: "label" }, "Sent as"), r.loc)); }
        r.choices = k("choices", h("input", { class: "input", type: "text", value: (x.choices || []).join(", "), placeholder: "Allowed values, comma separated (optional)" }));
      }
      r.title = h("span", { class: "text-xs font-semibold uppercase tracking-wide text-slate-500" });
      r.el = h("div", { class: "rounded-lg border border-slate-200 bg-slate-50/60 p-3" },
        h("div", { class: "mb-2 flex items-center justify-between" }, r.title, h("button", { class: "btn-ghost btn-sm text-red-600", type: "button", onclick: () => { rows.splice(rows.indexOf(r), 1); r.el.remove(); reindex(); } }, "Remove")),
        h("div", { class: "grid gap-3 sm:grid-cols-2" }, h("div", {}, h("label", { class: "label" }, "Label"), r.label), ...cells,
          h("div", { class: "sm:col-span-2" }, h("label", { class: "label" }, "Description"), r.desc),
          r.choices ? h("div", { class: "sm:col-span-2" }, r.choices) : null),
        o.showRequired === false ? null : h("label", { class: "mt-3 flex items-center gap-2 text-sm" }, r.required, "Required (the chatbot must ask the user if it's missing)"));
      rows.push(r); list.append(r.el); reindex();
    }
    (o.initial || []).forEach(addRow);
    if (!(o.initial || []).length && o.startWithOne) addRow({});

    function collect() {
      const used = new Set(rows.map((r) => r.initialName).filter(Boolean));
      return rows.map((r) => {
        let name = r.name ? (r.name.value.trim() || slug(r.label.value)) : r.initialName;
        const maps = r.col ? r.col.value : "";
        if (!name) { name = slug(r.label.value || maps); if (name === "id") name = "id_value"; let n = name, i = 2; while (used.has(n)) n = name.slice(0, 36) + "_" + i++; name = n; used.add(name); }
        const out = { name, label: r.label.value.trim(), description: r.desc.value.trim(), required: o.showRequired === false ? false : r.required.checked, extra: {} };
        if (o.mode === "column") {
          const col = o.columns.find((c) => c.name === maps);
          out.maps_to = maps; out.param_type = col ? colType(col) : "string";
          if (r.op) out.extra.operator = r.op.value;
        } else {
          out.param_type = r.type.value; out.maps_to = "";
          if (r.loc) out.extra.location = r.loc.value;
          const ch = r.choices.value.split(",").map((s) => s.trim()).filter(Boolean);
          if (ch.length) out.extra.choices = ch;
        }
        return out;
      });
    }
    const node = h("div", { class: "space-y-3" }, list, empty, h("div", { class: "flex flex-wrap gap-2" }, addBtn, ...extraBtns));
    reindex();
    return { node, collect, names: () => rows.map((r) => (r.name ? r.name.value.trim() || slug(r.label.value) : r.initialName || slug(r.label.value))).filter(Boolean) };
  }

  // ============================================================================= builder
  async function getSchema(connId) {
    if (!schemaCache[connId]) schemaCache[connId] = (await api.get(`/api/connections/${connId}/schema`)).tables;
    return schemaCache[connId];
  }

  function checkGroup(cols, selected, key) {
    const box = h("div", { class: "grid gap-1.5 sm:grid-cols-2", "data-field": key },
      cols.map((c) => h("label", { class: "flex items-center gap-2 text-sm" }, h("input", { type: "checkbox", value: c.name, checked: (selected || []).includes(c.name), class: "h-4 w-4 rounded border-slate-300 text-brand-600" }), c.name)));
    return { node: box, value: () => $$("input:checked", box).map((i) => i.value) };
  }

  /** Builds the template-specific part of the form. Returns {node, collect()}. */
  function dbSection(type, tables, init) {
    const cfg = (init && init.config) || {};
    const initParams = ((init && init.parameters) || []).filter((p) => !(p.extra && p.extra.role === "key"));
    const holder = h("div", { class: "space-y-4" });
    const tableSel = select([["", "Choose a table…"]].concat(tables.map((t) => [t.name, t.name])), cfg.table || "", { "data-field": "table" });
    let cur = null;
    function drawTable(first) {
      clear(holder);
      const t = tables.find((x) => x.name === tableSel.value);
      cur = null;
      if (!t) return;
      const cols = t.columns, c = {};
      const keep = first ? cfg : {};
      if (type === "db_get_by_id" || type === "db_update") {
        const pk = cols.find((x) => x.pk);
        c.idCol = select([["", "Choose the ID column…"]].concat(cols.map((x) => [x.name, x.name])), keep.id_column || (pk ? pk.name : ""), { "data-field": "id_column" });
        holder.append(field(type === "db_get_by_id" ? "Which column identifies a record?" : "Which column identifies the row to update?", c.idCol, "Usually the ID or primary key.", null));
      }
      if (type === "db_get_by_id") {
        c.idDesc = h("input", { class: "input", type: "text", maxlength: 200, value: keep.id_description || "", "data-field": "id_description", placeholder: "e.g. The order number the customer gives you" });
        holder.append(field("What does the user give you?", c.idDesc, null, null));
      }
      if (type === "db_search" || type === "db_get_by_id") {
        c.out = checkGroup(cols, keep.output_fields, "output_fields");
        holder.append(h("div", {}, h("label", { class: "label" }, "Fields to show in the answer"), c.out.node, h("p", { class: "hint" }, "Leave all unticked to show every column.")));
      }
      if (type === "db_search") {
        c.limit = h("input", { class: "input w-28", type: "number", min: 1, max: 50, value: keep.limit || 10, "data-field": "limit" });
        holder.append(field("Maximum results", c.limit, "1 to 50.", null));
      }
      if (type !== "db_get_by_id") {
        const intro = { db_search: "Which filters can the chatbot use?", db_insert: "Which columns can the chatbot fill in for the new record?", db_update: "Which columns can the chatbot change?" }[type];
        c.params = paramEditor({ mode: "column", columns: cols, operator: type === "db_search", showRequired: type !== "db_update", addAll: type === "db_insert", initial: first ? initParams : [], startWithOne: true,
          emptyText: "Add at least one input." });
        holder.append(h("div", {}, h("label", { class: "label" }, intro), c.params.node));
      }
      cur = c;
    }
    tableSel.addEventListener("change", () => drawTable(false));
    drawTable(true);
    const node = h("div", { class: "space-y-4" }, field("Table *", tableSel, null, null), holder);
    return {
      node,
      collect() {
        const config = { table: tableSel.value };
        if (cur && cur.idCol) config.id_column = cur.idCol.value;
        if (cur && cur.idDesc) config.id_description = cur.idDesc.value;
        if (cur && cur.out) config.output_fields = cur.out.value();
        if (cur && cur.limit) config.limit = parseInt(cur.limit.value || "10", 10);
        return { config, parameters: cur && cur.params ? cur.params.collect() : [] };
      },
    };
  }

  function restSection(init, hasSecret) {
    const c = (init && init.config) || {};
    const url = h("input", { class: "input font-mono", type: "text", value: c.url || "", placeholder: "https://api.example.com/items/{item_id}", "data-field": "url" });
    const method = select(["GET", "POST", "PUT", "PATCH", "DELETE"], c.method || "GET", { "data-field": "method" });
    const timeout = h("input", { class: "input w-28", type: "number", min: 1, max: 30, step: 1, value: c.timeout || 10, "data-field": "timeout" });
    const outputs = h("input", { class: "input font-mono", type: "text", value: (c.output_fields || []).join(", "), placeholder: "e.g. name, main.temp, items.0.title", "data-field": "output_fields" });
    const header = h("input", { class: "input", type: "text", value: c.auth_header || "", placeholder: "e.g. Authorization or X-Api-Key", "data-field": "auth_header" });
    const secret = h("input", { class: "input", type: "password", autocomplete: "off", placeholder: hasSecret ? "•••••••• saved. Leave blank to keep" : "Secret value", "data-field": "auth_value" });
    const params = paramEditor({ mode: "plain", locations: [["query", "Query string (?name=value)"], ["path", "Inside the URL ({name})"], ["body", "Request body (JSON)"]], initial: (init && init.parameters) || [], emptyText: "No inputs. The request is sent exactly as written." });
    const node = h("div", { class: "space-y-4" },
      h("div", { class: "grid gap-4 sm:grid-cols-3" }, h("div", { class: "sm:col-span-2" }, field("Web address (URL) *", url, "Use {name} to insert a value into the address. Only public https/http addresses are allowed.", null)), field("Method", method, "POST, PUT, PATCH and DELETE change data.", null)),
      field("Fields to show from the response", outputs, "Optional. Comma-separated paths; leave empty to show everything (shortened).", null),
      field("Timeout (seconds)", timeout, null, null),
      h("details", { class: "rounded-lg border border-slate-200 p-3" }, h("summary", { class: "cursor-pointer text-sm font-medium" }, "API key / authentication (optional)"),
        h("div", { class: "mt-3 grid gap-4 sm:grid-cols-2" }, field("Header name", header, null, null), field("Header value", secret, "Stored encrypted. Never shown to the browser or the AI.", null))),
      h("div", {}, h("label", { class: "label" }, "Inputs the chatbot can send"), params.node));
    return {
      node,
      collect() {
        return {
          config: { url: url.value.trim(), method: method.value, timeout: parseFloat(timeout.value || "10"), output_fields: outputs.value.split(",").map((s) => s.trim()).filter(Boolean), auth_header: header.value.trim() },
          parameters: params.collect(),
          secrets: secret.value ? { auth_value: secret.value } : header.value.trim() ? {} : { clear: true },
        };
      },
    };
  }

  function textSection(type, init) {
    const c = (init && init.config) || {};
    const params = paramEditor({ mode: "plain", initial: (init && init.parameters) || [], emptyText: type === "static_response" ? "No inputs. The same text is always sent." : "No inputs yet." });
    const isStatic = type === "static_response";
    const text = h("textarea", { class: "input", rows: 4, maxlength: 2000, "data-field": isStatic ? "response_text" : "result_template", placeholder: isStatic ? "e.g. Our store is open 9–5, Monday to Saturday." : "e.g. Thanks {name}! We'll reply to {email} soon." }, isStatic ? c.response_text || "" : c.result_template || "");
    const chips = h("div", { class: "mt-2 flex flex-wrap gap-1.5" });
    const drawChips = () => { clear(chips); params.names().forEach((n) => chips.append(h("button", { type: "button", class: "badge-blue font-mono hover:bg-brand-200", onclick: () => { const s = text.selectionStart || text.value.length; text.value = text.value.slice(0, s) + "{" + n + "}" + text.value.slice(text.selectionEnd || s); text.focus(); } }, "{" + n + "}"))); };
    params.node.addEventListener("input", drawChips); params.node.addEventListener("click", () => setTimeout(drawChips, 0)); drawChips();
    let code = null;
    const sections = [h("div", {}, h("label", { class: "label" }, "Inputs the chatbot collects"), params.node),
      h("div", {}, h("label", { class: "label" }, isStatic ? "Response text *" : "Response template"), text, chips, h("p", { class: "hint" }, "Click a {field} chip to insert the value the user provided."))];
    if (!isStatic) {
      code = h("textarea", { class: "input font-mono text-xs", rows: 8, "data-field": "python_code", placeholder: "def run(args):\n    # args is a dict of the inputs\n    return {\"message\": \"hello\"}", disabled: !S.pythonEnabled }, c.python_code || "");
      sections.push(h("details", { class: "rounded-lg border border-slate-200 p-3" }, h("summary", { class: "cursor-pointer text-sm font-medium" }, "Advanced: Python code (optional)"),
        h("div", { class: "mt-3 space-y-2" },
          h("p", { class: "rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-900" }, S.pythonEnabled ? "Code runs in a separate, restricted process with no file, network or system access, and the action needs your approval before use." : "Python actions are switched off on this server (ENABLE_PYTHON_ACTIONS=false). Ask the server owner to enable them."),
          code)));
    }
    return {
      node: h("div", { class: "space-y-4" }, sections),
      collect() {
        const config = isStatic ? { response_text: text.value } : { result_template: text.value, python_code: code ? code.value : "" };
        return { config, parameters: params.collect() };
      },
    };
  }


  // ============================================================================= intent, commands & rules
  function flowSection(full, getNames, getToolName) {
    const intent = h("input", { class: "input font-mono", type: "text", maxlength: 60, value: full ? full.intent || "" : "", placeholder: "auto: ask_<technical_name>", "data-field": "intent" });
    const cmds = h("textarea", { class: "input font-mono text-sm", rows: 4, "data-field": "commands", placeholder: "/products\nshow me tents under 200\nwhat do you sell?" }, ((full && full.commands) || []).join("\n"));
    const rules = [];
    const list = h("div", { class: "space-y-3", "data-field": "rules" });
    const MISS = [["ask", "Ask the user for what's missing"], ["skip", "Skip this action (dashed link)"]];

    function addRule(r) {
      r = r || {};
      const row = { name: h("input", { class: "input", type: "text", maxlength: 80, value: r.name || "", placeholder: "e.g. Need a price before searching" }),
        miss: select(MISS, r.on_missing || "ask"), reply: h("input", { class: "input", type: "text", maxlength: 300, value: r.reply_if_missing || "", placeholder: "Optional message when something is missing" }),
        boxes: h("div", { class: "flex flex-wrap gap-3" }), chosen: new Set(r.requires || []),
        yaml: h("textarea", { class: "input font-mono text-xs", rows: 7, spellcheck: false, placeholder: "Saved as Rasa YAML. Leave empty to generate it from the fields above." }, r.yaml || "") };
      const resetYaml = () => { row.yaml.value = ""; };  // fields changed: regenerate the YAML on save
      [row.name, row.miss, row.reply].forEach((el) => el.addEventListener("input", resetYaml));
      const drawBoxes = () => {
        clear(row.boxes);
        const names = getNames();
        if (!names.length) row.boxes.append(h("span", { class: "text-xs text-slate-500" }, "Add inputs above to require them here."));
        names.forEach((n) => { const cb = h("input", { type: "checkbox", checked: row.chosen.has(n), class: "h-4 w-4 rounded border-slate-300 text-brand-600" });
          cb.addEventListener("change", () => { cb.checked ? row.chosen.add(n) : row.chosen.delete(n); resetYaml(); });
          row.boxes.append(h("label", { class: "flex items-center gap-1.5 text-sm" }, cb, h("span", { class: "font-mono" }, n))); });
      };
      row.draw = drawBoxes; drawBoxes();
      row.el = h("div", { class: "rounded-lg border border-slate-200 bg-slate-50/60 p-3", onmouseenter: drawBoxes },
        h("div", { class: "mb-2 flex items-center justify-between" }, h("span", { class: "text-xs font-semibold uppercase tracking-wide text-slate-500" }, "Rule"),
          h("button", { class: "btn-ghost btn-sm text-red-600", type: "button", onclick: () => { rules.splice(rules.indexOf(row), 1); row.el.remove(); } }, "Remove")),
        h("div", { class: "grid gap-3 sm:grid-cols-2" }, h("div", { class: "sm:col-span-2" }, h("label", { class: "label" }, "Rule name"), row.name),
          h("div", { class: "sm:col-span-2" }, h("label", { class: "label" }, "Run only when the user has given"), row.boxes),
          h("div", {}, h("label", { class: "label" }, "If something is missing"), row.miss), h("div", {}, h("label", { class: "label" }, "Question to ask (becomes an utter_ response)"), row.reply),
          h("div", { class: "sm:col-span-2" }, h("label", { class: "label" }, "Rasa rule (rules.yml)"), row.yaml, h("p", { class: "hint" }, "This exact YAML is exported to data/rules.yml. You may edit it; only this action's intent and utter_/action_ steps are allowed."))));
      rules.push(row); list.append(row.el);
    }
    ((full && full.rules) || []).forEach(addRule);

    const suggest = () => {
      const tool = getToolName();
      if (!intent.value.trim()) intent.value = "ask_" + tool;
      if (!cmds.value.trim()) cmds.value = ["/" + tool.slice(0, 40), tool.replace(/_/g, " ")].join("\n");
      if (!rules.length) { const req = getNames(); if (req.length) { addRule({ name: "Collect inputs first", requires: req, on_missing: "ask" }); } }
    };
    const node = h("div", { class: "space-y-4" },
      h("div", { class: "grid gap-4 sm:grid-cols-2" }, field("Intent", intent, "The goal the user has when they trigger this action. Used for Rasa export and shown to the AI.", null)),
      field("Commands", cmds, "One per line. Start with / for a slash-command (like /products) or write a phrase users might say.", null),
      h("div", {}, h("label", { class: "label" }, "Rules"), list, h("div", { class: "mt-2 flex gap-2" }, h("button", { class: "btn-secondary btn-sm", type: "button", onclick: () => addRule({}) }, "+ Add rule"),
        h("button", { class: "btn-ghost btn-sm", type: "button", onclick: suggest }, "Suggest from this action"))));
    return { node, collect: () => ({ intent: intent.value.trim(), commands: cmds.value.split("\n").map((x) => x.trim()).filter(Boolean),
      rules: rules.map((r) => ({ name: r.name.value.trim(), requires: [...r.chosen].filter((n) => getNames().includes(n)), on_missing: r.miss.value, reply_if_missing: r.reply.value.trim(), yaml: r.yaml.value.trim() })) }) };
  }

  async function showBuilder(type, existing, dropPos) {
    clear(pageActions);
    const tpl = S.templates.find((t) => t.type === type);
    clear(root).append(loadingBlock());
    let full = existing;
    if (existing) { try { full = await api.get("/api/actions/" + existing.id); } catch (e) { clear(root).append(errorBlock(e.message, showList)); return; } }
    const isWrite = (type === "db_insert" || type === "db_update");

    const name = h("input", { class: "input", type: "text", maxlength: 80, value: full ? full.name : "", placeholder: "e.g. Find products", "data-field": "name" });
    const desc = h("textarea", { class: "input", rows: 2, maxlength: 500, "data-field": "description", placeholder: "What does it do? The AI reads this to understand the action." }, full ? full.description : "");
    const when = h("textarea", { class: "input", rows: 2, maxlength: 500, "data-field": "when_to_use", placeholder: "e.g. When the customer asks about products, prices or stock." }, full ? full.when_to_use : "");
    const confirmCb = h("input", { type: "checkbox", class: "h-4 w-4 rounded border-slate-300 text-brand-600", checked: full ? full.requires_confirmation : false });
    const enabledCb = h("input", { type: "checkbox", class: "h-4 w-4 rounded border-slate-300 text-brand-600", checked: full ? full.enabled : true });
    const toolName = h("input", { class: "input font-mono", type: "text", maxlength: 48, value: full ? full.tool_name : "", placeholder: "auto-generated", "data-field": "tool_name" });
    const connSel = select([["", "Choose a database…"]].concat(S.connections.map((c) => [c.id, c.name + (c.allow_writes ? " (writes allowed)" : "")])), full && full.connection_id ? full.connection_id : S.connections.length === 1 ? S.connections[0].id : "", { "data-field": "connection_id" });
    const sectionBox = h("div", {});
    let section = null;
    const flowSec = flowSection(full, () => { try { return section ? section.collect().parameters.map((p) => p.name).filter((n) => n && n !== "id") : []; } catch (e) { return []; } },
      () => (toolName.value.trim() || slug(name.value || "action")));

    async function drawSection(first) {
      clear(sectionBox); section = null;
      if (tpl.needs_connection) {
        if (!connSel.value) { sectionBox.append(h("p", { class: "text-sm text-slate-500" }, "Choose a database to continue.")); return; }
        sectionBox.append(loadingBlock("Reading tables…"));
        let tables;
        try { tables = await getSchema(connSel.value); } catch (e) { clear(sectionBox).append(errorBlock(e.message, () => drawSection(first))); return; }
        clear(sectionBox);
        section = dbSection(type, tables, first ? full : null);
      } else if (type === "rest_api") section = restSection(first ? full : null, full && full.has_secret);
      else section = textSection(type, first ? full : null);
      sectionBox.append(section.node);
    }
    connSel.addEventListener("change", () => drawSection(false));

    const saveBtn = h("button", { class: "btn-primary", type: "submit" }, existing ? "Save changes" : "Create action");
    const form = h("form", { class: "space-y-6", novalidate: true, onsubmit: (e) => { e.preventDefault(); save(); } },
      h("section", { class: "card space-y-4 p-5" },
        h("div", { class: "flex items-center gap-2" }, h("span", { class: "text-xl" }, ICONS[tpl.icon] || "⚙️"), h("h2", { class: "text-base font-semibold" }, tpl.label)),
        field("Action name *", name, null, null), field("What does it do? *", desc, null, null), field("When should the chatbot use it?", when, "Helps the AI choose this action at the right moment.", null)),
      h("section", { class: "card space-y-4 p-5" }, h("h2", { class: "text-base font-semibold" }, "Set it up"),
        tpl.needs_connection ? field("Data source *", connSel, h("span", {}, "Don't see yours? "), null) : null, sectionBox),
      h("section", { class: "card space-y-4 p-5" }, h("h2", { class: "text-base font-semibold" }, "Intent, commands & rules"),
        h("p", { class: "text-xs text-slate-500" }, "Decide what triggers this action and what must be true before it runs. You can also edit these directly on the canvas."), flowSec.node),
      h("section", { class: "card space-y-3 p-5" }, h("h2", { class: "text-base font-semibold" }, "Safety"),
        h("label", { class: "flex items-start gap-2 text-sm" }, confirmCb, h("span", {}, "Ask the user to confirm before this runs", isWrite ? h("span", { class: "ml-1 text-xs text-slate-500" }, "(always on for actions that change data)") : null)),
        h("label", { class: "flex items-center gap-2 text-sm" }, enabledCb, "Enabled"),
        h("p", { class: "rounded-lg bg-slate-50 px-3 py-2 text-xs text-slate-600" }, "Actions that change data start unapproved. After saving, use “Review & approve” on the actions list. Editing such an action later removes the approval so you can re-check it."),
        h("details", {}, h("summary", { class: "cursor-pointer text-sm font-medium" }, "Advanced"), h("div", { class: "mt-3 max-w-sm" }, field("Technical name", toolName, "The name the AI uses internally. Lowercase letters, numbers and underscores.", null)))),
      h("div", { class: "flex justify-end gap-2" }, h("button", { class: "btn-secondary", type: "button", onclick: showList }, "Cancel"), saveBtn));
    if (isWrite) { confirmCb.checked = true; confirmCb.disabled = true; }

    clear(root).append(h("button", { class: "btn-ghost -ml-2 mb-4", type: "button", onclick: showList }, "← Back to actions"),
      h("h2", { class: "mb-4 text-lg font-semibold" }, existing ? "Edit action" : "New action"), form);
    await drawSection(true);

    async function save() {
      clearFieldErrors(form);
      if (!name.value.trim() || !desc.value.trim()) { reportError(new App.ApiError("Please fill in the name and description.", 400, { name: name.value.trim() ? undefined : "This field is required.", description: desc.value.trim() ? undefined : "This field is required." }), form); return; }
      if (!section) { toast("Finish the setup section first.", "error"); return; }
      const part = section.collect();
      const payload = { name: name.value, description: desc.value, when_to_use: when.value, tool_name: toolName.value, template_type: type,
        connection_id: tpl.needs_connection && connSel.value ? parseInt(connSel.value, 10) : null, enabled: enabledCb.checked, requires_confirmation: confirmCb.checked,
        config: part.config, parameters: part.parameters, ...flowSec.collect() };
      if (part.secrets) payload.secrets = part.secrets;
      await withLoading(saveBtn, async () => {
        try {
          const saved = existing ? await api.put("/api/actions/" + existing.id, payload) : await api.post("/api/actions", payload);
          if (!existing && dropPos && saved && saved.id) App.flowEditor.placeLane(saved.id, dropPos.x, dropPos.y);
          toast(saved.is_write ? "Saved. Approve it before chatbots can use it." : "Action saved.", "success");
          await load();
        } catch (e) { reportError(e, form); }
      });
    }
  }

  load();
})();

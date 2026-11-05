/* Shared "test an action directly" dialog (works without any LLM). Used by Custom Actions and Chat Playground. */
(function () {
  const { h, api, $$, modal, confirmDialog, withLoading, reportError, renderData, clear } = App;

  function paramInput(p) {
    const common = { "data-param": p.name, "aria-label": p.label };
    if (p.param_type === "boolean") return h("select", Object.assign({ class: "input" }, common), h("option", { value: "" }, "—"), h("option", { value: "true" }, "Yes"), h("option", { value: "false" }, "No"));
    if (p.extra && p.extra.choices && p.extra.choices.length) return h("select", Object.assign({ class: "input" }, common), h("option", { value: "" }, "—"), p.extra.choices.map((c) => h("option", { value: c }, c)));
    return h("input", Object.assign({ class: "input", type: p.param_type === "string" ? "text" : "number", step: p.param_type === "integer" ? "1" : "any" }, common));
  }

  function testModal(a) {
    const params = a.parameters;
    const out = h("div", { class: "mt-4" });
    const testMode = h("input", { type: "checkbox", checked: true, class: "h-4 w-4 rounded border-slate-300 text-brand-600" });
    const run = h("button", { class: "btn-primary", type: "submit" }, "Run test");
    const form = h("form", { class: "space-y-3", onsubmit: (e) => { e.preventDefault(); go(); } },
      h("p", { class: "text-sm text-slate-600" }, a.description),
      params.length ? params.map((p) => h("div", {}, h("label", { class: "label" }, p.label + (p.required ? " *" : "")), paramInput(p), p.description ? h("p", { class: "hint" }, p.description) : null)) : h("p", { class: "text-sm text-slate-500" }, "This action needs no inputs."),
      a.is_write ? h("label", { class: "flex items-start gap-2 rounded-lg bg-amber-50 p-3 text-sm text-amber-900" }, testMode, h("span", {}, h("b", {}, "Test mode. "), "Simulate only. Untick to run against real data (requires approval and a write-enabled connection).")) : null,
      h("div", { class: "flex justify-end" }, run));
    async function go() {
      const args = {};
      $$("[data-param]", form).forEach((el) => { if (el.value !== "") args[el.dataset.param] = el.type === "number" ? Number(el.value) : el.tagName === "SELECT" && (el.value === "true" || el.value === "false") ? el.value === "true" : el.value; });
      const tm = a.is_write ? testMode.checked : true;
      if (a.is_write && !tm && !(await confirmDialog("Run on real data?", "This will really change data in the connected database/service.", "Run it", true))) return;
      await withLoading(run, async () => {
        try {
          const r = await api.post(`/api/actions/${a.id}/test`, { arguments: args, test_mode: tm });
          clear(out).append(
            h("div", { class: "rounded-lg border px-3 py-2 text-sm " + (r.ok ? "border-emerald-200 bg-emerald-50 text-emerald-900" : "border-red-200 bg-red-50 text-red-800") },
              r.simulated ? h("b", {}, "TEST MODE: nothing was changed. ") : null, r.summary || r.error || r.status),
            r.missing && r.missing.length ? h("p", { class: "mt-2 text-sm text-amber-700" }, "Missing: " + r.missing.join(", ")) : null,
            r.data !== null && r.data !== undefined ? h("div", { class: "mt-3" }, renderData(r.data)) : null);
        } catch (e) { reportError(e); }
      });
    }
    modal("Test: " + a.name, h("div", {}, form, out), { wide: true });
  }


  App.testAction = testModal;
})();

(function () {
  const { h, api, $, clear, toast, withLoading, reportError, clearFieldErrors, loadingBlock, errorBlock, field, select } = App;
  const root = $("#app");

  async function load() {
    clear(root).append(loadingBlock());
    let s;
    try { s = await api.get("/api/settings"); } catch (e) { clear(root).append(errorBlock(e.message, load)); return; }
    render(s);
  }

  function render(s) {
    const status = h("div", { class: "rounded-lg px-3 py-2 text-sm " + (s.configured ? "bg-emerald-50 text-emerald-800" : "bg-amber-50 text-amber-900") },
      s.configured ? `AI is configured${s.key_source === "env" ? " (key from the server's .env file)" : ""}.` : s.setup_message);
    const provider = select(s.providers.map((p) => [p, p === "anthropic" ? "Anthropic (Claude)" : "OpenAI-compatible (OpenAI, OpenRouter, Ollama…)"]), s.provider, { name: "provider" });
    const model = h("input", { class: "input", name: "model", value: s.model || "", type: "text", maxlength: 100 });
    const base = h("input", { class: "input", name: "base_url", value: s.base_url || "", type: "text", placeholder: "Leave empty for the provider's default", maxlength: 300 });
    const key = h("input", { class: "input", name: "api_key", type: "password", autocomplete: "off", placeholder: s.has_key ? "•••••••• (saved. Type to replace)" : "Paste your API key" });
    const clearKey = h("input", { type: "checkbox", class: "h-4 w-4 rounded border-slate-300 text-brand-600" });
    const updateModelHint = () => { model.placeholder = "Default: " + (s.defaults[provider.value] || ""); };
    provider.addEventListener("change", updateModelHint); updateModelHint();

    const result = h("div", { class: "text-sm" });
    const saveBtn = h("button", { class: "btn-primary", type: "submit" }, "Save settings");
    const testBtn = h("button", { class: "btn-secondary", type: "button" }, "Test connection");
    const form = h("form", { class: "card max-w-2xl space-y-4 p-5", novalidate: true, onsubmit: (e) => { e.preventDefault(); save(); } },
      status,
      field("Provider", provider, null, "provider"), field("Model", model, "Leave empty to use the default.", "model"),
      field("API base URL (optional)", base, "Only needed for OpenAI-compatible services such as OpenRouter or a local Ollama server.", "base_url"),
      field("API key", key, "Stored encrypted on the server. It is never shown again and never sent to the chatbot's AI as text.", "api_key"),
      s.has_key && s.key_source === "user" ? h("label", { class: "flex items-center gap-2 text-sm text-slate-600" }, clearKey, "Remove my saved key") : null,
      h("div", { class: "flex flex-wrap items-center gap-2" }, saveBtn, testBtn), result);

    function payload() { return { provider: provider.value, model: model.value, base_url: base.value, api_key: key.value || undefined, clear_api_key: clearKey.checked }; }
    async function save() {
      clearFieldErrors(form);
      await withLoading(saveBtn, async () => { try { const n = await api.put("/api/settings", payload()); toast("Settings saved.", "success"); render(n); } catch (e) { reportError(e, form); } });
    }
    testBtn.addEventListener("click", async () => {
      await withLoading(testBtn, async () => {
        try {
          await api.put("/api/settings", payload());
          const r = await api.post("/api/settings/test-llm");
          clear(result).append(h("p", { class: r.ok ? "text-emerald-700" : "text-red-700" }, r.message));
        } catch (e) { reportError(e, form); }
      });
    });
    clear(root).append(form,
      h("p", { class: "mt-4 max-w-2xl text-xs text-slate-500" }, "Without an API key you can still create chatbots, connect databases and test every action directly from the Custom Actions page."));
  }
  load();
})();

(function () {
  const { h, api, $, $$, clear, toast, withLoading, reportError, clearFieldErrors, loadingBlock, errorBlock, field } = App;
  const root = $("#app");
  const botId = parseInt(root.dataset.botId || "0", 10) || 0;

  function textInput(name, value, attrs) { return h("input", Object.assign({ class: "input", name, value: value || "", type: "text" }, attrs || {})); }
  function textArea(name, value, rows, attrs) { return h("textarea", Object.assign({ class: "input", name, rows: rows || 3 }, attrs || {}), value || ""); }

  function actionBadges(a) {
    const out = [h("span", { class: "badge-gray" }, a.template_label)];
    if (a.is_write) out.push(h("span", { class: "badge-amber" }, "changes data"));
    if (a.is_write && !a.approved) out.push(h("span", { class: "badge-red" }, "not approved yet"));
    if (!a.enabled) out.push(h("span", { class: "badge-red" }, "disabled"));
    return out;
  }

  async function init() {
    clear(root).append(loadingBlock());
    let bot = { name: "", description: "", purpose: "", welcome_message: "Hi! How can I help you today?", system_instructions: "", enabled_actions: [], temperature: 0.2, model_override: "", test_mode_default: true };
    let actions;
    try {
      actions = (await api.get("/api/actions")).actions;
      if (botId) bot = await api.get("/api/chatbots/" + botId);
    } catch (e) { clear(root).append(errorBlock(e.status === 404 ? "That chatbot no longer exists." : e.message, e.status === 404 ? null : init)); return; }
    render(bot, actions);
  }

  function render(bot, actions) {
    const selected = new Set(bot.enabled_actions);
    const form = h("form", { class: "space-y-6", novalidate: true, onsubmit: (e) => { e.preventDefault(); save(); } });

    form.append(h("section", { class: "card space-y-4 p-5" },
      h("h2", { class: "text-base font-semibold" }, "Basics"),
      field("Chatbot name *", textInput("name", bot.name, { placeholder: "e.g. Store Assistant", maxlength: 80, required: true }), null, "name"),
      field("Short description", textInput("description", bot.description, { placeholder: "One line shown on the chatbot card", maxlength: 500 }), null, "description"),
      field("What is this chatbot for?", textArea("purpose", bot.purpose, 3, { placeholder: "Describe in plain English. For example: Help customers find outdoor gear and check the status of their orders.", maxlength: 2000 }),
        "The AI uses this to understand its job.", "purpose")));

    form.append(h("section", { class: "card space-y-4 p-5" },
      h("h2", { class: "text-base font-semibold" }, "Conversation"),
      field("Welcome message", textInput("welcome_message", bot.welcome_message, { maxlength: 500 }), "The first thing users see.", "welcome_message"),
      field("Extra instructions", textArea("system_instructions", bot.system_instructions, 4, { placeholder: "Tone, rules, things to avoid. For example: Be friendly and brief. Never discuss pricing of other stores.", maxlength: 4000 }),
        "Optional guidance the AI follows in every reply.", "system_instructions")));

    const list = h("div", { class: "divide-y divide-slate-100 rounded-lg border border-slate-200" });
    if (!actions.length) {
      list.append(h("p", { class: "p-4 text-sm text-slate-500" }, "You haven't created any actions yet. "), h("a", { class: "block px-4 pb-4 text-sm font-medium text-brand-600 hover:underline", href: "/actions" }, "Create an action →"));
    } else actions.forEach((a) => {
      const cb = h("input", { type: "checkbox", class: "mt-1 h-4 w-4 rounded border-slate-300 text-brand-600 focus:ring-brand-500", checked: selected.has(a.id), "data-action-id": a.id });
      list.append(h("label", { class: "flex cursor-pointer items-start gap-3 p-3 hover:bg-slate-50" }, cb,
        h("div", { class: "min-w-0" }, h("p", { class: "text-sm font-medium" }, a.name), h("p", { class: "text-xs text-slate-500" }, a.description),
          h("div", { class: "mt-1.5 flex flex-wrap gap-1.5" }, actionBadges(a)))));
    });
    form.append(h("section", { class: "card space-y-3 p-5" },
      h("div", { class: "flex items-center justify-between" }, h("h2", { class: "text-base font-semibold" }, "Actions this chatbot can use"), h("a", { class: "text-sm font-medium text-brand-600 hover:underline", href: "/actions" }, "Manage actions")),
      h("p", { class: "text-xs text-slate-500" }, "The chatbot can only use actions that are ticked here. Actions that change data also need your approval on the Custom Actions page."),
      list));

    const temp = h("input", { type: "range", min: "0", max: "1", step: "0.1", value: String(bot.temperature), class: "w-full accent-indigo-600", name: "temperature" });
    const tempLabel = h("span", { class: "text-sm font-medium" }, String(bot.temperature));
    temp.addEventListener("input", () => { tempLabel.textContent = temp.value; });
    const testMode = h("input", { type: "checkbox", name: "test_mode_default", class: "h-4 w-4 rounded border-slate-300 text-brand-600 focus:ring-brand-500", checked: bot.test_mode_default });
    form.append(h("details", { class: "card p-5" },
      h("summary", { class: "cursor-pointer text-base font-semibold" }, "Advanced settings"),
      h("div", { class: "mt-4 space-y-4" },
        h("div", {}, h("label", { class: "label" }, "Creativity ", tempLabel), temp, h("p", { class: "hint" }, "Lower is more predictable, higher is more creative.")),
        field("Model override", textInput("model_override", bot.model_override, { placeholder: "Leave empty to use the model from Settings", maxlength: 100 }), null, "model_override"),
        h("label", { class: "flex items-center gap-2 text-sm" }, testMode, "Start new conversations in test mode (actions that change data are only simulated)"))));

    const saveBtn = h("button", { class: "btn-primary", type: "submit" }, botId ? "Save changes" : "Create chatbot");
    form.append(h("div", { class: "flex flex-wrap items-center justify-end gap-2" },
      h("a", { class: "btn-secondary", href: "/chatbots" }, "Cancel"),
      botId ? h("a", { class: "btn-secondary", href: "/playground?bot=" + botId }, "Open playground") : null, saveBtn));

    async function save() {
      clearFieldErrors(form);
      const payload = {
        name: form.name.value, description: form.description.value, purpose: form.purpose.value,
        welcome_message: form.welcome_message.value, system_instructions: form.system_instructions.value,
        temperature: parseFloat(form.temperature.value), model_override: form.model_override.value, test_mode_default: form.test_mode_default.checked,
        enabled_actions: $$("[data-action-id]", form).filter((c) => c.checked).map((c) => parseInt(c.dataset.actionId, 10)),
      };
      if (!payload.name.trim()) { reportError(new App.ApiError("Please give your chatbot a name.", 400, { name: "This field is required." }), form); return; }
      await withLoading(saveBtn, async () => {
        try {
          const saved = botId ? await api.put("/api/chatbots/" + botId, payload) : await api.post("/api/chatbots", payload);
          toast(botId ? "Changes saved." : "Chatbot created.", "success");
          if (!botId) window.location.href = "/playground?bot=" + saved.id; else render(saved, actions);
        } catch (e) { reportError(e, form); }
      });
    }
    clear(root).append(form);
  }
  init();
})();

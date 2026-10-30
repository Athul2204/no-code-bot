(function () {
  const { h, api, $, clear, toast, confirmDialog, withLoading, reportError, emptyState, loadingBlock, errorBlock, fmtDate, select, renderData } = App;
  const root = $("#app");

  let bots = [], actions = [], bot = null, convId = null, testMode = true, sending = false, llmOk = true;
  let els = {};

  async function init() {
    clear(root).append(loadingBlock());
    try {
      const [b, a, st] = await Promise.all([api.get("/api/chatbots"), api.get("/api/actions"), api.get("/api/status")]);
      bots = b.chatbots; actions = a.actions; llmOk = st.llm_configured;
    } catch (e) { clear(root).append(errorBlock(e.message, init)); return; }
    if (!bots.length) { clear(root).append(emptyState("Create a chatbot to test it here", "The playground lets you chat with a chatbot and see every action it runs.", [h("a", { class: "btn-primary", href: "/chatbots/new" }, "Create a chatbot")])); return; }
    const want = parseInt(root.dataset.botId || "0", 10);
    bot = bots.find((x) => x.id === want) || bots[0];
    testMode = bot.test_mode_default;
    build();
    await loadConversations();
    newChat();
  }

  // ----------------------------------------------------------------------------- layout
  function build() {
    els.botSel = select(bots.map((b) => [b.id, b.name]), bot.id, { "aria-label": "Chatbot" });
    els.botSel.addEventListener("change", () => { bot = bots.find((b) => b.id === parseInt(els.botSel.value, 10)); testMode = bot.test_mode_default; els.test.checked = testMode; syncMode(); history.replaceState(null, "", "/playground?bot=" + bot.id); drawDirect(); loadConversations(); newChat(); });
    els.test = h("input", { type: "checkbox", checked: testMode, class: "h-4 w-4 rounded border-slate-300 text-brand-600" });
    els.test.addEventListener("change", async () => {
      if (!els.test.checked && !(await confirmDialog("Turn off test mode?", "Actions that change data will really change it, but only after you click Confirm each time.", "Turn off", true))) { els.test.checked = true; return; }
      testMode = els.test.checked; syncMode();
    });
    els.modeNote = h("p", { class: "mt-1 text-xs" });
    els.convs = h("ul", { class: "max-h-56 divide-y divide-slate-100 overflow-y-auto text-sm" });
    els.direct = h("div", { class: "space-y-1.5" });
    els.log = h("div", { class: "flex-1 space-y-3 overflow-y-auto px-4 py-4", role: "log", "aria-live": "polite" });
    els.banner = h("div", {});
    els.input = h("textarea", { class: "input min-h-[2.5rem] resize-none", rows: 1, placeholder: "Type a message…", maxlength: 4000, "aria-label": "Message" });
    els.input.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } });
    els.input.addEventListener("input", () => { els.input.style.height = "auto"; els.input.style.height = Math.min(els.input.scrollHeight, 160) + "px"; });
    els.sendBtn = h("button", { class: "btn-primary self-end", type: "button", onclick: send }, "Send");

    const side = h("aside", { class: "space-y-4 lg:w-72 lg:shrink-0" },
      h("div", { class: "card space-y-3 p-4" },
        h("div", {}, h("label", { class: "label" }, "Chatbot"), els.botSel, h("a", { class: "mt-1 inline-block text-xs font-medium text-brand-600 hover:underline", href: `/chatbots/${bot.id}/edit`, id: "edit-link" }, "Edit this chatbot")),
        h("div", {}, h("label", { class: "flex items-center gap-2 text-sm font-medium" }, els.test, "Test mode"), els.modeNote)),
      h("div", { class: "card p-4" }, h("div", { class: "mb-2 flex items-center justify-between" }, h("h2", { class: "text-sm font-semibold" }, "Conversations"),
        h("button", { class: "btn-secondary btn-sm", type: "button", onclick: newChat }, "+ New")), els.convs),
      h("div", { class: "card p-4" }, h("h2", { class: "text-sm font-semibold" }, "Try actions directly"), h("p", { class: "mb-2 mt-0.5 text-xs text-slate-500" }, "Runs an action without the AI. Works even without an API key."), els.direct));

    const main = h("section", { class: "card flex min-h-[28rem] min-w-0 flex-1 flex-col overflow-hidden", style: "height: calc(100vh - 11rem)" },
      h("div", { class: "flex items-center justify-between border-b border-slate-200 px-4 py-2.5" },
        h("div", { class: "min-w-0" }, (els.title = h("p", { class: "truncate text-sm font-semibold" }, bot.name))),
        h("button", { class: "btn-ghost btn-sm", type: "button", onclick: clearChat }, "Clear conversation")),
      els.banner, els.log,
      h("div", { class: "flex gap-2 border-t border-slate-200 p-3" }, els.input, els.sendBtn));
    clear(root).append(h("div", { class: "flex flex-col gap-4 lg:flex-row" }, side, main));
    syncMode(); drawDirect();
  }

  function syncMode() {
    els.modeNote.className = "mt-1 text-xs " + (testMode ? "text-emerald-700" : "text-amber-700");
    els.modeNote.textContent = testMode ? "Actions that would change data are only simulated." : "LIVE: confirmed actions will change real data.";
    clear(els.banner);
    if (!testMode) els.banner.append(h("div", { class: "border-b border-amber-200 bg-amber-50 px-4 py-1.5 text-xs font-medium text-amber-900" }, "Live mode: approved write actions run for real after you confirm."));
    if (!llmOk) els.banner.append(h("div", { class: "flex flex-wrap items-center justify-between gap-2 border-b border-slate-200 bg-slate-50 px-4 py-1.5 text-xs text-slate-600" },
      h("span", {}, "No AI provider is set up, so chatting is disabled. You can still run actions directly from the side panel."), h("a", { class: "font-medium text-brand-600 hover:underline", href: "/settings" }, "Set up AI")));
    const link = $("#edit-link"); if (link) link.href = `/chatbots/${bot.id}/edit`;
  }

  function drawDirect() {
    clear(els.direct);
    const mine = actions.filter((a) => bot.enabled_actions.includes(a.id));
    if (!mine.length) { els.direct.append(h("p", { class: "text-xs text-slate-500" }, "This chatbot has no actions. Edit it to tick some.")); return; }
    mine.forEach((a) => els.direct.append(h("button", { type: "button", class: "flex w-full items-center justify-between gap-2 rounded-lg border border-slate-200 px-3 py-2 text-left text-sm hover:bg-slate-50", onclick: () => App.testAction(a) },
      h("span", { class: "truncate" }, a.name), a.is_write && !a.approved ? h("span", { class: "badge-red shrink-0" }, "unapproved") : h("span", { class: "text-xs text-brand-600" }, "Run"))));
  }

  // ----------------------------------------------------------------------------- conversations
  async function loadConversations() {
    clear(els.convs).append(h("li", { class: "py-2 text-xs text-slate-400" }, "Loading…"));
    try {
      const { conversations } = await api.get(`/api/chatbots/${bot.id}/conversations`);
      clear(els.convs);
      if (!conversations.length) els.convs.append(h("li", { class: "py-2 text-xs text-slate-500" }, "No conversations yet."));
      conversations.forEach((c) => els.convs.append(h("li", { class: "flex items-center gap-1 py-1.5" + (c.id === convId ? " font-semibold" : "") },
        h("button", { class: "min-w-0 flex-1 truncate rounded px-1 text-left hover:text-brand-700", type: "button", title: fmtDate(c.updated_at), onclick: () => openConversation(c.id) }, c.title),
        h("button", { class: "px-1 text-slate-400 hover:text-red-600", type: "button", "aria-label": "Delete conversation", onclick: () => deleteConversation(c) }, "✕"))));
    } catch (e) { clear(els.convs).append(h("li", { class: "py-2 text-xs text-red-600" }, e.message)); }
  }
  function welcome() { if (bot.welcome_message) els.log.append(h("div", { class: "bubble-bot" }, bot.welcome_message)); }
  function newChat() { convId = null; clear(els.log); welcome(); els.title.textContent = bot.name; els.input.focus(); loadConversationsQuiet(); }
  function loadConversationsQuiet() { /* highlight refresh handled after each send */ }
  async function openConversation(id) {
    try {
      const r = await api.get("/api/conversations/" + id);
      convId = id; testMode = r.conversation.test_mode; els.test.checked = testMode; syncMode();
      clear(els.log); welcome(); r.events.forEach(renderEvent); scrollDown(); els.title.textContent = bot.name + " · " + r.conversation.title; loadConversations();
    } catch (e) { reportError(e); }
  }
  async function deleteConversation(c) {
    if (!(await confirmDialog("Delete conversation?", `"${c.title}" will be permanently deleted.`, "Delete", true))) return;
    try { await api.del("/api/conversations/" + c.id); if (convId === c.id) newChat(); toast("Conversation deleted.", "success"); loadConversations(); } catch (e) { reportError(e); }
  }
  async function clearChat() {
    if (!convId) { newChat(); return; }
    if (!(await confirmDialog("Clear this conversation?", "All messages in this conversation will be removed.", "Clear", true))) return;
    try { await api.post(`/api/conversations/${convId}/clear`); clear(els.log); welcome(); els.title.textContent = bot.name; loadConversations(); } catch (e) { reportError(e); }
  }

  // ----------------------------------------------------------------------------- rendering
  const scrollDown = () => { els.log.scrollTop = els.log.scrollHeight; };

  function argChips(args) {
    const keys = Object.keys(args || {});
    if (!keys.length) return null;
    return h("div", { class: "mt-1.5 flex flex-wrap gap-1.5" }, keys.map((k) => h("span", { class: "badge-gray font-mono" }, `${k}: ${typeof args[k] === "object" ? JSON.stringify(args[k]) : args[k]}`)));
  }

  function toolCard(ev) {
    const ok = ev.ok, tone = ok ? "border-emerald-200 bg-emerald-50/60" : ev.status === "declined" || ev.status === "needs_input" ? "border-amber-200 bg-amber-50/60" : "border-red-200 bg-red-50/60";
    const hasData = ev.data !== null && ev.data !== undefined && !(Array.isArray(ev.data) && !ev.data.length);
    return h("div", { class: "mr-auto w-full max-w-[85%] rounded-xl border px-3.5 py-2.5 text-sm " + tone },
      h("div", { class: "flex flex-wrap items-center gap-2" }, h("span", { "aria-hidden": "true" }, ok ? "⚡" : "⚠️"), h("span", { class: "font-medium" }, ev.display_name || ev.name),
        h("span", { class: ok ? "badge-green" : "badge-amber" }, String(ev.status || (ok ? "ok" : "error")).replace("_", " ")),
        ev.simulated ? h("span", { class: "badge-blue" }, "test mode: nothing changed") : null),
      argChips(ev.arguments),
      ev.summary ? h("p", { class: "mt-1.5 text-slate-700" }, ev.summary) : null,
      hasData ? h("details", { class: "mt-2" }, h("summary", { class: "cursor-pointer text-xs font-medium text-brand-700" }, "View result"), h("div", { class: "mt-2" }, renderData(ev.data))) : null);
  }

  function confirmCard(ev) {
    const card = h("div", { class: "mr-auto w-full max-w-[85%] rounded-xl border border-brand-200 bg-brand-50 px-3.5 py-3 text-sm" });
    const yes = h("button", { class: "btn-primary btn-sm", type: "button" }, ev.is_write ? "Confirm change" : "Yes, run it");
    const no = h("button", { class: "btn-secondary btn-sm", type: "button" }, "Cancel");
    card.append(h("p", { class: "font-medium" }, "Please confirm: " + ev.action_name),
      ev.description ? h("p", { class: "text-xs text-slate-600" }, ev.description) : null, argChips(ev.arguments),
      h("p", { class: "mt-2 text-xs " + (testMode ? "text-emerald-700" : "text-amber-700") }, ev.is_write ? (testMode ? "Test mode: this will be simulated, nothing will change." : "This will change real data.") : "This action only reads data."),
      h("div", { class: "mt-3 flex gap-2" }, yes, no));
    const decide = async (approve) => {
      yes.disabled = no.disabled = true;
      setBusy(true);
      try {
        const r = await api.post("/api/chat/confirm", { token: ev.token, approve });
        card.replaceWith(h("div", { class: "mr-auto text-xs text-slate-500" }, (approve ? "✔ Confirmed: " : "✖ Cancelled: ") + ev.action_name));
        if (r.error) toast(r.error, "error");
        (r.events || []).forEach(renderEvent); scrollDown(); loadConversations();
      } catch (e) { yes.disabled = no.disabled = false; reportError(e); }
      finally { setBusy(false); }
    };
    yes.addEventListener("click", () => decide(true)); no.addEventListener("click", () => decide(false));
    return card;
  }

  function renderEvent(ev) {
    let node = null;
    switch (ev.type) {
      case "user": node = h("div", { class: "bubble-user" }, ev.content); break;
      case "assistant": node = h("div", { class: "bubble-bot" }, ev.content); break;
      case "tool": node = toolCard(ev); break;
      case "confirm": node = confirmCard(ev); break;
      case "setup": node = h("div", { class: "mr-auto max-w-[85%] rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900" }, h("p", {}, ev.content), h("a", { class: "btn-secondary btn-sm mt-2", href: "/settings" }, "Open Settings")); break;
      default: node = h("div", { class: "mr-auto max-w-[85%] rounded-xl border border-red-200 bg-red-50 px-4 py-2.5 text-sm text-red-800", role: "alert" }, ev.content);
    }
    els.log.append(node);
    return node;
  }

  // ----------------------------------------------------------------------------- sending
  function setBusy(b) {
    sending = b; els.sendBtn.disabled = b; els.input.disabled = b;
    const old = $("#typing"); if (old) old.remove();
    if (b) els.log.append(h("div", { id: "typing", class: "bubble-bot flex w-16 items-center justify-center gap-1", "aria-label": "Assistant is typing" }, [0, 1, 2].map((i) => h("span", { class: "h-1.5 w-1.5 animate-bounce rounded-full bg-slate-400", style: `animation-delay:${i * 120}ms` }))));
    scrollDown();
    if (!b) els.input.focus();
  }

  async function send() {
    const text = els.input.value.trim();
    if (!text || sending) return;
    els.input.value = ""; els.input.style.height = "auto";
    renderEvent({ type: "user", content: text });
    setBusy(true);
    try {
      const body = { message: text, test_mode: testMode };
      if (convId) body.conversation_id = convId;
      const r = await api.post(`/api/chatbots/${bot.id}/chat`, body);
      const first = !convId; convId = r.conversation_id;
      setBusy(false);
      (r.events || []).forEach(renderEvent);
      if (r.setup_required) { llmOk = false; syncMode(); }
      if (first) els.title.textContent = bot.name + " · " + r.title;
      scrollDown(); loadConversations();
    } catch (e) {
      setBusy(false);
      const msg = e.status === 429 ? "You're sending messages too quickly. Please wait a moment." : e.message;
      renderEvent({ type: "error", content: msg });
      scrollDown();
    }
  }
  init();
})();

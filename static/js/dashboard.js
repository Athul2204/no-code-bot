(function () {
  const { h, api, $, clear, emptyState, loadingBlock, errorBlock, fmtDate } = App;
  const root = $("#app");

  function stat(label, value, href) {
    return h("a", { href, class: "card block p-5 transition hover:border-brand-300 hover:shadow" },
      h("p", { class: "text-sm text-slate-500" }, label), h("p", { class: "mt-1 text-3xl font-semibold" }, String(value)));
  }
  function statusBadge(s) {
    const map = { ok: "badge-green", error: "badge-red", rejected: "badge-red", declined: "badge-amber", invalid: "badge-amber", needs_input: "badge-amber" };
    return h("span", { class: map[s] || "badge-gray" }, s.replace("_", " "));
  }

  async function load() {
    clear(root).append(loadingBlock());
    let d;
    try { d = await api.get("/api/dashboard"); } catch (e) { clear(root).append(errorBlock(e.message, load)); return; }
    clear(root);
    if (!d.llm_configured) {
      root.append(h("div", { class: "mb-6 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900" },
        h("span", {}, "No AI provider is configured yet. You can still build chatbots and test actions directly; add an API key to chat naturally."),
        h("a", { class: "btn-secondary btn-sm", href: "/settings" }, "Set up AI")));
    }
    if (d.pending_approvals) {
      root.append(h("div", { class: "mb-6 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-brand-200 bg-brand-50 px-4 py-3 text-sm text-brand-900" },
        h("span", {}, `${d.pending_approvals} action${d.pending_approvals === 1 ? " needs" : "s need"} your approval before chatbots can use ${d.pending_approvals === 1 ? "it" : "them"}.`),
        h("a", { class: "btn-secondary btn-sm", href: "/actions" }, "Review actions")));
    }
    root.append(h("div", { class: "grid grid-cols-2 gap-4 lg:grid-cols-4" },
      stat("Chatbots", d.counts.chatbots, "/chatbots"), stat("Custom actions", d.counts.actions, "/actions"),
      stat("Database connections", d.counts.connections, "/connections"), stat("Conversations", d.counts.conversations, "/playground")));

    if (!d.counts.chatbots) {
      root.append(h("div", { class: "mt-8" }, emptyState("Build your first chatbot", "It takes about a minute: describe what it does, add an action, then chat with it.",
        [h("a", { class: "btn-primary", href: "/chatbots/new" }, "Create a chatbot")])));
    } else {
      root.append(h("div", { class: "mt-8 grid gap-4 sm:grid-cols-3" },
        quick("1. Connect data", "Register a SQLite database and browse its tables.", "/connections", "Connect database"),
        quick("2. Add actions", "Pick a template: search, look up, insert, call an API…", "/actions", "Build an action"),
        quick("3. Test it", "Chat with your bot in test mode. Nothing real changes.", "/playground", "Open playground")));
    }

    const logs = d.recent_logs || [];
    root.append(h("h2", { class: "mb-3 mt-8 text-base font-semibold" }, "Recent action activity"));
    if (!logs.length) root.append(emptyState("No activity yet", "When an action runs (from chat or a manual test), it shows up here."));
    else root.append(h("div", { class: "card overflow-hidden" }, h("ul", { class: "divide-y divide-slate-100" }, logs.map((l) =>
      h("li", { class: "flex flex-wrap items-center gap-x-3 gap-y-1 px-4 py-3 text-sm" },
        h("span", { class: "font-medium" }, l.action_name), statusBadge(l.status),
        l.test_mode ? h("span", { class: "badge-gray" }, "test mode") : null,
        h("span", { class: "min-w-0 flex-1 truncate text-slate-500" }, l.result_summary || ""),
        h("span", { class: "text-xs text-slate-400" }, fmtDate(l.created_at)))))));
  }
  function quick(title, text, href, cta) {
    return h("div", { class: "card flex flex-col p-5" }, h("p", { class: "font-semibold" }, title),
      h("p", { class: "mt-1 flex-1 text-sm text-slate-500" }, text), h("a", { class: "btn-secondary mt-4 self-start", href }, cta));
  }
  load();
})();

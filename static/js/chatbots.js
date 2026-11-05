(function () {
  const { h, api, $, clear, toast, modal, confirmDialog, withLoading, emptyState, loadingBlock, errorBlock, fmtDate, reportError } = App;
  const root = $("#app");
  $("#page-actions").append(h("a", { class: "btn-primary", href: "/chatbots/new" }, "+ New chatbot"));

  async function load() {
    clear(root).append(loadingBlock());
    let data;
    try { data = await api.get("/api/chatbots"); } catch (e) { clear(root).append(errorBlock(e.message, load)); return; }
    clear(root);
    const bots = data.chatbots;
    if (!bots.length) { root.append(emptyState("No chatbots yet", "Create one to get started. You'll describe its purpose in plain English.", [h("a", { class: "btn-primary", href: "/chatbots/new" }, "Create a chatbot")])); return; }
    root.append(h("div", { class: "grid gap-4 md:grid-cols-2 xl:grid-cols-3" }, bots.map(card)));
  }

  function card(b) {
    return h("div", { class: "card flex flex-col p-5" },
      h("div", { class: "flex items-start justify-between gap-2" },
        h("h3", { class: "text-base font-semibold" }, b.name),
        h("span", { class: "badge-gray shrink-0" }, "v" + b.version)),
      h("p", { class: "mt-1 line-clamp-2 min-h-[2.5rem] text-sm text-slate-500" }, b.description || b.purpose || "No description."),
      h("div", { class: "mt-3 flex flex-wrap gap-2 text-xs text-slate-500" },
        h("span", { class: "badge-blue" }, `${b.enabled_actions.length} action${b.enabled_actions.length === 1 ? "" : "s"}`),
        h("span", { class: "badge-gray" }, `${b.conversation_count} conversation${b.conversation_count === 1 ? "" : "s"}`),
        h("span", {}, "Updated " + fmtDate(b.updated_at))),
      h("div", { class: "mt-4 flex flex-wrap gap-2 border-t border-slate-100 pt-4" },
        h("a", { class: "btn-primary btn-sm", href: "/playground?bot=" + b.id }, "Open playground"),
        h("a", { class: "btn-secondary btn-sm", href: `/chatbots/${b.id}/edit` }, "Edit"),
        h("button", { class: "btn-secondary btn-sm", type: "button", onclick: (e) => duplicate(b, e.currentTarget) }, "Duplicate"),
        h("button", { class: "btn-ghost btn-sm", type: "button", onclick: () => versions(b) }, "History"),
        h("button", { class: "btn-ghost btn-sm text-red-600 hover:bg-red-50", type: "button", onclick: () => remove(b) }, "Delete")));
  }

  async function duplicate(b, btn) {
    await withLoading(btn, async () => {
      try { await api.post(`/api/chatbots/${b.id}/duplicate`); toast("Chatbot duplicated.", "success"); await load(); } catch (e) { reportError(e); }
    });
  }
  async function remove(b) {
    if (!(await confirmDialog("Delete chatbot?", `"${b.name}" and its conversations will be permanently deleted. Your actions and database connections are kept.`, "Delete", true))) return;
    try { await api.del("/api/chatbots/" + b.id); toast("Chatbot deleted.", "success"); load(); } catch (e) { reportError(e); }
  }
  async function versions(b) {
    const body = h("div", {}, loadingBlock());
    const m = modal(`Version history: ${b.name}`, body);
    try {
      const { versions } = await api.get(`/api/chatbots/${b.id}/versions`);
      clear(body).append(h("ul", { class: "divide-y divide-slate-100" }, versions.map((v) => h("li", { class: "flex items-center justify-between gap-3 py-2.5 text-sm" },
        h("div", {}, h("span", { class: "font-medium" }, "Version " + v.version_number), v.version_number === b.version ? h("span", { class: "badge-green ml-2" }, "current") : null,
          h("p", { class: "text-xs text-slate-500" }, `${fmtDate(v.created_at)} · ${v.action_count} action(s)`)),
        v.version_number === b.version ? null : h("button", { class: "btn-secondary btn-sm", type: "button", onclick: async (e) => {
          if (!(await confirmDialog("Restore this version?", "The chatbot's settings will be replaced. This creates a new version, so nothing is lost.", "Restore"))) return;
          await withLoading(e.currentTarget, async () => { try { await api.post(`/api/chatbots/${b.id}/versions/${v.id}/restore`); toast("Version restored.", "success"); m.close(); load(); } catch (err) { reportError(err); } });
        } }, "Restore")))));
    } catch (e) { clear(body).append(errorBlock(e.message)); }
  }
  load();
})();

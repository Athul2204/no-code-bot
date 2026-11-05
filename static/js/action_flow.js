/* Flow view for custom actions: each action is its own lane  [Intent] -> [Rule(s)] -> [Action].
   Solid links always run; dashed links are conditional (rule set to "skip"). Click a node to edit its action. */
(function () {
  const { h, api, clear, loadingBlock, errorBlock } = App;
  const NS = "http://www.w3.org/2000/svg";
  const W = 210, H = 66;
  const ICON = { intent: "🎯", rule: "🧩", db_search: "🔎", db_get_by_id: "#️⃣", db_insert: "➕", db_update: "✏️", rest_api: "🌐", static_response: "💬", custom_form: "🛠️" };

  const svg = (tag, attrs) => { const el = document.createElementNS(NS, tag); Object.entries(attrs || {}).forEach(([k, v]) => el.setAttribute(k, v)); return el; };

  function nodeEl(n, onOpen) {
    const dim = n.kind === "action" && n.enabled === false;
    const card = h("button", { type: "button", class: `flow-node flow-${n.kind}${dim ? " flow-off" : ""}`, style: `left:${n.x}px;top:${n.y}px;width:${W}px;height:${H}px`, "data-node": n.id,
      title: n.kind === "intent" && n.commands.length ? n.commands.join("\n") : n.label, onclick: () => onOpen(n.action_id) },
      h("span", { class: "flow-port flow-port-in" }), h("span", { class: "flow-port flow-port-out" }),
      h("span", { class: "flow-head" }, h("span", { class: "flow-icon" }, ICON[n.icon] || "⚙️"), h("span", { class: "flow-label" }, n.label), n.write ? h("span", { class: "flow-tag" }, "writes") : null),
      h("span", { class: "flow-meter" }, h("span", { class: "flow-bar" }, h("span", { style: `width:${Math.max(8, Math.min(100, n.fill))}%` })), h("span", { class: "flow-meta" }, n.meta)));
    return card;
  }

  function draw(box, g, onOpen) {
    clear(box);
    const stage = h("div", { class: "flow-stage", style: `height:${g.height}px;min-width:${620 + W + 60}px` });
    const edges = svg("svg", { class: "flow-edges", width: "100%", height: g.height });
    const byId = Object.fromEntries(g.nodes.map((n) => [n.id, n]));
    g.edges.forEach((e) => {
      const a = byId[e.from], b = byId[e.to];
      if (!a || !b) return;
      const x1 = a.x + W, y1 = a.y + H / 2, x2 = b.x, y2 = b.y + H / 2, dx = Math.max(40, (x2 - x1) / 2);
      edges.append(svg("path", { d: `M${x1},${y1} C${x1 + dx},${y1} ${x2 - dx},${y2} ${x2},${y2}`, class: `flow-edge ${e.style === "dashed" ? "flow-edge-dashed" : ""}` }));
      if (e.label) { const t = svg("text", { x: (x1 + x2) / 2, y: (y1 + y2) / 2 - 8, class: "flow-edge-label", "text-anchor": "middle" }); t.textContent = e.label === "ask" ? "asks if missing" : "skips if missing"; edges.append(t); }
    });
    stage.append(edges, ...g.nodes.map((n) => nodeEl(n, onOpen)));
    box.append(h("div", { class: "flow-canvas" }, stage),
      h("p", { class: "mt-2 text-xs text-slate-500" }, "Each action is its own pipeline for now: intent → rules → action. Solid lines always run, dashed lines are optional. Click any node to edit that action."));
  }

  App.renderFlow = async function (container, onOpen) {
    clear(container).append(loadingBlock("Drawing flow…"));
    try {
      const g = await api.get("/api/actions/graph");
      if (!g.nodes.length) { clear(container).append(h("p", { class: "p-6 text-sm text-slate-500" }, "No actions yet. Create one and it will appear here.")); return; }
      draw(container, g, onOpen);
    } catch (e) { clear(container).append(errorBlock(e.message, () => App.renderFlow(container, onOpen))); }
  };
})();

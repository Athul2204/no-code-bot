/* Interactive flow editor for custom actions (React-Flow style, vanilla JS so it works under the strict CSP).

   Every action is a lane:   [Intent]  ->  [Rule] ... ->  [Action]
   - Drag nodes, pan the canvas (drag background) and zoom (mouse wheel / buttons).
   - Drag from a node's right-hand port to connect:
       Intent  -> empty canvas    creates a new rule on that action
       Rule    -> Action          attaches or moves the rule to that action
   - Drag "Rule" or an action type from the left palette onto the canvas.
   - Click a node or a link to edit it in the inspector. Delete key removes the selection.
   Intent / commands / rules are saved with PUT /api/actions/<id>/flow. Node positions live in this browser (localStorage). */
(function () {
  const { h, api, clear, toast, field, select, reportError } = App;
  const NS = "http://www.w3.org/2000/svg";
  const W = 210, H = 70, DX = 290, LANE = 140, ROWR = 84, KEY = "bb.flow.v2";
  const ICON = { intent: "🎯", rule: "🧩", db_search: "🔎", db_get_by_id: "#️⃣", db_insert: "➕", db_update: "✏️", rest_api: "🌐", static_response: "💬", custom_form: "🛠️" };
  const SHORT = { db_search: "Search records", db_get_by_id: "Get by ID", db_insert: "Insert record", db_update: "Update record", rest_api: "REST API call", static_response: "Fixed reply", custom_form: "Custom form" };
  const OPS = [["eq", "is equal to"], ["neq", "is not"], ["contains", "contains"], ["gt", "is greater than"], ["gte", "is at least"], ["lt", "is less than"], ["lte", "is at most"], ["empty", "is empty"], ["not_empty", "is filled in"]];
  const ruleOut = (r) => ({ name: r.name, requires: r.requires || [], on_missing: r.on_missing || "ask", reply_if_missing: r.reply_if_missing || "", conditions: r.conditions || [], match: r.match || "all", yaml: r.yaml || "" });
  const ruleSummary = (r) => { const q = r.requires || [], c = (r.conditions || []).length; return [q.length ? "needs " + q.join(", ") : "", c ? `${c} condition${c === 1 ? "" : "s"}` : ""].filter(Boolean).join(" · ") || "always"; };
  const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
  const uid = () => "f" + Date.now().toString(36) + Math.random().toString(36).slice(2, 5);
  const svgEl = (tag, attrs) => { const el = document.createElementNS(NS, tag); Object.entries(attrs || {}).forEach(([k, v]) => el.setAttribute(k, v)); return el; };

  // ------------------------------------------------------------------ persistence (positions, loose rules, viewport)
  function readStore() {
    try { const v = JSON.parse(localStorage.getItem(KEY) || "{}"); return { pos: v.pos || {}, floating: v.floating || [], drafts: v.drafts || [], view: v.view || null }; }
    catch (e) { return { pos: {}, floating: [], drafts: [], view: null }; }
  }
  const store = readStore();
  let pendingSelect = null;   // node id to select after the page reloads its actions (set after saving a form)
  const persist = () => { try { localStorage.setItem(KEY, JSON.stringify(store)); } catch (e) { /* storage unavailable: layout just isn't remembered */ } };

  function mount(container, ctx) {
    let nodes = [], edges = [], byId = {}, elById = {};
    let selected = null;                    // {type:"node"|"edge", id}
    const view = Object.assign({ x: 40, y: 40, k: 1 }, store.view || {});
    let viewTimer = null;

    // ---------------------------------------------------------------- skeleton
    const grid = h("div", { class: "fe-grid" });
    const svg = svgEl("svg", { class: "fe-edges" });
    const vp = h("div", { class: "fe-vp" });
    const zoomLabel = h("span", {}, "100%");
    const empty = h("div", { class: "fe-empty" }, h("div", {}, h("b", {}, "Start building your flow"), "Drag an action from the left onto the canvas.", h("br"), "Then drag from its intent's right-hand dot to add rules."));
    const bar = h("div", { class: "fe-bar" },
      h("button", { type: "button", title: "Zoom out", "aria-label": "Zoom out", onclick: () => zoomBy(1 / 1.25) }, "−"), zoomLabel,
      h("button", { type: "button", title: "Zoom in", "aria-label": "Zoom in", onclick: () => zoomBy(1.25) }, "+"),
      h("button", { type: "button", title: "Fit everything in view", onclick: () => fit(false) }, "Fit"),
      h("button", { type: "button", title: "Reset node positions", onclick: autoLayout }, "Auto-layout"));
    const legend = h("div", { class: "fe-legend" }, h("span", {}, h("i", {}), "always runs"), h("span", {}, h("i", { class: "d" }), "conditional or skipped"));
    const canvas = h("div", { class: "fe-canvas", tabindex: "0", role: "application", "aria-label": "Action flow canvas. Select a node and press Delete to remove it." }, grid, vp, bar, legend, empty);
    const pal = h("aside", { class: "fe-pal", "aria-label": "Palette" });
    const insp = h("aside", { class: "fe-insp", "aria-label": "Inspector" });
    const feEl = h("div", { class: "fe" }, h("div", { class: "fe-main" }, pal, canvas), insp);
    clear(container).append(feEl);

    // ---------------------------------------------------------------- coordinates / viewport
    const toGraph = (cx, cy) => { const r = canvas.getBoundingClientRect(); return { x: (cx - r.left - view.x) / view.k, y: (cy - r.top - view.y) / view.k }; };
    const centerPoint = () => { const r = canvas.getBoundingClientRect(), p = toGraph(r.left + r.width / 2, r.top + r.height / 2), j = (nodes.length % 5) * 24; return { x: p.x - W / 2 + j, y: p.y - H / 2 + j }; };
    function applyView() {
      vp.style.transform = `translate(${view.x}px, ${view.y}px) scale(${view.k})`;
      grid.style.backgroundSize = `${22 * view.k}px ${22 * view.k}px`;
      grid.style.backgroundPosition = `${view.x}px ${view.y}px`;
      zoomLabel.textContent = Math.round(view.k * 100) + "%";
      clearTimeout(viewTimer); viewTimer = setTimeout(() => { store.view = { x: view.x, y: view.y, k: view.k }; persist(); }, 300);
    }
    function zoomAt(cx, cy, f) {
      const r = canvas.getBoundingClientRect(), k = clamp(view.k * f, 0.3, 2), px = cx - r.left, py = cy - r.top;
      view.x = px - (px - view.x) * (k / view.k); view.y = py - (py - view.y) * (k / view.k); view.k = k; applyView();
    }
    function zoomBy(f) { const r = canvas.getBoundingClientRect(); zoomAt(r.left + r.width / 2, r.top + r.height / 2, f); }
    function fit(initial) {
      if (!nodes.length) { Object.assign(view, { x: 40, y: 40, k: 1 }); applyView(); return; }
      const r = canvas.getBoundingClientRect(), pad = 60;
      const x1 = Math.min(...nodes.map((n) => n.x)), y1 = Math.min(...nodes.map((n) => n.y));
      const x2 = Math.max(...nodes.map((n) => n.x + W)), y2 = Math.max(...nodes.map((n) => n.y + H));
      const k = clamp(Math.min((r.width - pad * 2) / (x2 - x1), (r.height - pad * 2) / (y2 - y1), 1), 0.3, 1);
      if (initial === true) {   // first visit: stay readable (>= 80%) and start at the top-left; pan/zoom for the rest
        const ik = clamp((r.width - 80) / (x2 - x1), 0.6, 1); view.k = ik; view.x = 40 - x1 * ik; view.y = 30 - y1 * ik; applyView(); return;
      }
      view.k = k; view.x = (r.width - (x2 - x1) * k) / 2 - x1 * k; view.y = (r.height - (y2 - y1) * k) / 2 - y1 * k; applyView();
    }
    function autoLayout() {
      Object.keys(store.pos).forEach((k) => { if (!k.startsWith("f")) delete store.pos[k]; });   // keep loose rules where they are
      persist(); refresh(); fit();
    }

    // ---------------------------------------------------------------- graph model (derived from the actions every time)
    function buildGraph() {
      nodes = []; edges = []; byId = {};
      const add = (n) => { nodes.push(n); byId[n.id] = n; };
      let y = 60;
      ctx.actions.forEach((a) => {
        const rules = a.rules || [], iid = "i" + a.id, aid = "a" + a.id;
        const ip = store.pos[iid] || { x: 40, y }, ap = store.pos[aid] || { x: ip.x + 2 * DX, y: ip.y };
        add({ id: iid, kind: "intent", a, x: ip.x, y: ip.y });
        add({ id: aid, kind: "action", a, x: ap.x, y: ap.y });
        if (!rules.length) edges.push({ id: "e" + a.id, from: iid, to: aid, style: "solid", kind: "direct" });
        rules.forEach((r, i) => {
          const rid = `r${a.id}_${i}`, rp = store.pos[rid] || { x: ip.x + DX, y: ip.y + (i - (rules.length - 1) / 2) * ROWR };
          add({ id: rid, kind: "rule", a, rule: r, index: i, x: rp.x, y: rp.y });
          edges.push({ id: rid + "_in", from: iid, to: rid, style: "solid", kind: "in" });
          edges.push({ id: rid + "_out", from: rid, to: aid, style: r.on_missing === "skip" || (r.conditions || []).length ? "dashed" : "solid", kind: "out",
            label: (r.conditions || []).length ? `runs if ${r.match === "any" ? "any" : "all"} match` : r.on_missing === "skip" ? "skips if missing" : "asks if missing" });
        });
        y += Math.max(LANE, rules.length * ROWR + 60);
      });
      store.drafts.forEach((d) => { const p = store.pos[d.id] || d; add({ id: d.id, kind: "draft", draft: d, type: d.type, x: p.x, y: p.y }); });
      store.floating.forEach((f) => { const p = store.pos[f.id] || f; add({ id: f.id, kind: "rule", floating: true, rule: f, x: p.x, y: p.y }); });
    }

    // ---------------------------------------------------------------- rendering
    function nodeEl(n) {
      const a = n.a, off = n.kind === "action" && !a.enabled;
      let label, meta, fill, tag = null, icon;
      if (n.kind === "draft") {
        label = "New " + (SHORT[n.type] || "action").toLowerCase(); icon = ICON[n.type] || "⚙️"; meta = "not saved: fill in the form →"; fill = 10;
      } else if (n.kind === "intent") {
        const c = (a.commands || []).length; label = a.intent || "intent"; icon = ICON.intent; meta = `${c} command${c === 1 ? "" : "s"}`; fill = Math.min(100, 20 + 15 * c);
      } else if (n.kind === "rule") {
        const q = n.rule.requires || []; label = n.rule.name || "Untitled rule"; icon = ICON.rule;
        meta = n.floating ? "not connected: drag to an action" : ruleSummary(n.rule); fill = 20 + 10 * q.length + 12 * (n.rule.conditions || []).length;
      } else {
        const ps = a.parameters || [], req = ps.filter((p) => p.required).length; label = a.name; icon = ICON[a.template_type] || "⚙️";
        meta = ps.length ? `${req}/${ps.length} required` : "no inputs"; fill = ps.length ? Math.max(15, Math.round(100 * req / ps.length)) : 100;
        if (a.is_write && !a.approved) tag = h("span", { class: "flow-tag fe-tag-red" }, "needs approval"); else if (a.is_write) tag = h("span", { class: "flow-tag" }, "writes");
      }
      return h("div", { class: `flow-node fe-node flow-${n.kind === "draft" ? "action fe-draft fe-floating" : n.kind}${n.floating ? " fe-floating" : ""}${off ? " flow-off" : ""}${selected && selected.id === n.id ? " fe-selected" : ""}`, "data-id": n.id,
        style: `left:${n.x}px;top:${n.y}px;width:${W}px;height:${H}px`, title: label },
        h("span", { class: "flow-port flow-port-in" }), h("span", { class: "flow-port flow-port-out" }),
        h("span", { class: "flow-head" }, h("span", { class: "flow-icon" }, icon), h("span", { class: "flow-label" }, label), tag),
        h("span", { class: "flow-meter" }, h("span", { class: "flow-bar" }, h("span", { style: `width:${clamp(fill, 8, 100)}%` })), h("span", { class: "flow-meta fe-sub" }, meta)));
    }
    const curve = (x1, y1, x2, y2) => { const dx = Math.max(50, Math.abs(x2 - x1) / 2); return `M${x1},${y1} C${x1 + dx},${y1} ${x2 - dx},${y2} ${x2},${y2}`; };
    function drawEdges() {
      clear(svg);
      edges.forEach((e) => {
        const a = byId[e.from], b = byId[e.to]; if (!a || !b) return;
        const x1 = a.x + W, y1 = a.y + H / 2, x2 = b.x, y2 = b.y + H / 2, d = curve(x1, y1, x2, y2);
        const g = svgEl("g", { class: selected && selected.type === "edge" && selected.id === e.id ? "fe-edge-sel" : "" });
        g.append(svgEl("path", { d, class: "fe-edge-hit", "data-edge": e.id }), svgEl("path", { d, class: `fe-edge${e.style === "dashed" ? " flow-edge-dashed" : ""}` }));
        if (e.label) { const t = svgEl("text", { x: (x1 + x2) / 2, y: (y1 + y2) / 2 - 8, class: "fe-edge-label", "text-anchor": "middle" }); t.textContent = e.label; g.append(t); }
        svg.append(g);
      });
    }
    function drawNodes() {
      elById = {}; clear(vp).append(svg);
      nodes.forEach((n) => { const el = nodeEl(n); elById[n.id] = el; vp.append(el); });
      empty.style.display = nodes.length ? "none" : "grid";
    }
    function refresh() {
      buildGraph();
      if (selected && !(selected.type === "node" ? byId[selected.id] : edges.some((e) => e.id === selected.id))) selected = null;
      drawNodes(); drawEdges(); renderInspector(); applyView();
    }
    function pick(sel) {
      selected = sel;
      Object.entries(elById).forEach(([id, el]) => el.classList.toggle("fe-selected", !!sel && sel.type === "node" && sel.id === id));
      drawEdges(); renderInspector();
    }

    // ---------------------------------------------------------------- saving helpers
    async function saveFlow(a, patch) {
      const rules = (patch.rules || a.rules || []).map(ruleOut);
      const saved = await api.put(`/api/actions/${a.id}/flow`, Object.assign({ intent: a.intent, commands: a.commands || [] }, patch, { rules }));
      Object.assign(a, saved);   // keep the shared action object current
      return saved;
    }
    function shiftRulePos(aid, removed) {       // rule node ids are r<action>_<index>: close the gap after a removal
      const n = (ctx.actions.find((x) => x.id === aid) || {}).rules || [];
      for (let i = removed; i <= n.length; i++) { const from = `r${aid}_${i + 1}`, to = `r${aid}_${i}`; if (store.pos[from]) store.pos[to] = store.pos[from]; else delete store.pos[to]; delete store.pos[from]; }
    }
    const freshRule = (a, name) => ({ name: name || `Rule ${((a.rules || []).length + 1)}`, requires: [], on_missing: "ask", reply_if_missing: "", yaml: "" });

    async function addRuleTo(a, rule, at) {
      const idx = (a.rules || []).length;
      await saveFlow(a, { rules: [...(a.rules || []), Object.assign({}, rule, { yaml: "" })] });
      if (at) store.pos[`r${a.id}_${idx}`] = { x: at.x, y: at.y };
      persist(); selected = { type: "node", id: `r${a.id}_${idx}` }; refresh();
    }
    async function removeRule(n, { toFloating }) {
      const a = n.a, rules = a.rules.filter((_, i) => i !== n.index);
      await saveFlow(a, { rules });
      if (toFloating) { const f = Object.assign({}, n.rule, { id: uid(), requires: [], conditions: [], x: n.x, y: n.y + 90 }); delete f.yaml; store.floating.push(f); selected = { type: "node", id: f.id }; } else selected = null;
      delete store.pos[n.id]; shiftRulePos(a.id, n.index); persist(); refresh();
    }
    async function attachFloating(f, a, at) {
      const names = (a.parameters || []).map((p) => p.name);
      store.floating = store.floating.filter((x) => x.id !== f.id); delete store.pos[f.id];
      try { await addRuleTo(a, { name: f.rule.name, requires: (f.rule.requires || []).filter((r) => names.includes(r)), on_missing: f.rule.on_missing, reply_if_missing: f.rule.reply_if_missing,
        conditions: (f.rule.conditions || []).filter((c) => names.includes(c.field)), match: f.rule.match }, at); }
      catch (e) { store.floating.push(f.rule); persist(); throw e; }
    }

    // ---------------------------------------------------------------- connecting nodes
    function plan(from, to) {
      const bad = (msg) => ({ type: "bad", msg });
      if (from.kind === "intent") {
        if (!to) return { type: "newRule", a: from.a };
        if (to.kind === "action") return to.a.id === from.a.id ? { type: "noop" } : bad("An intent only triggers its own action. Add a rule and connect it to the other action instead.");
        if (to.kind === "rule") return to.floating ? { type: "attach", rule: to, a: from.a } : to.a.id === from.a.id ? { type: "noop" } : bad("That rule belongs to a different action.");
        return bad("Connect an intent to a rule.");
      }
      if (from.kind === "rule") {
        if (to && to.kind === "action") return !from.floating && to.a.id === from.a.id ? { type: "noop" } : { type: from.floating ? "attach" : "move", rule: from, a: to.a };
        return to ? bad("A rule's output connects to an action.") : { type: "cancel" };
      }
      return { type: "cancel" };
    }
    async function runPlan(p, drop) {
      if (p.type === "bad") { toast(p.msg, "error"); return; }
      if (p.type === "noop") { toast("Already connected.", "info"); return; }
      try {
        if (p.type === "newRule") { await addRuleTo(p.a, freshRule(p.a), { x: drop.x - W / 2, y: drop.y - H / 2 }); toast("Rule added. Edit it on the right.", "success"); }
        else if (p.type === "attach") { await attachFloating(p.rule, p.a, { x: p.rule.x, y: p.rule.y }); toast("Rule connected.", "success"); }
        else if (p.type === "move") {
          const names = (p.a.parameters || []).map((q) => q.name), src = p.rule, kept = (src.rule.requires || []).filter((r) => names.includes(r));
          const moved = { name: src.rule.name, requires: kept, on_missing: src.rule.on_missing, reply_if_missing: src.rule.reply_if_missing,
            conditions: (src.rule.conditions || []).filter((c) => names.includes(c.field)), match: src.rule.match };
          await addRuleTo(p.a, moved, { x: src.x, y: src.y });
          const keepSel = selected; await removeRule(src, { toFloating: false }); selected = keepSel; refresh();
          toast(kept.length < (src.rule.requires || []).length ? "Rule moved. Inputs this action doesn't have were dropped." : "Rule moved.", "success");
        }
      } catch (e) { reportError(e); refresh(); }
    }

    // ---------------------------------------------------------------- pointer interactions on the canvas
    function listen(move, up) {
      const end = (e) => { window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", end); window.removeEventListener("pointercancel", end); up(e); };
      window.addEventListener("pointermove", move); window.addEventListener("pointerup", end); window.addEventListener("pointercancel", end);
    }
    function startDrag(e, n, el) {
      e.preventDefault();
      const s = { x: e.clientX, y: e.clientY, ox: n.x, oy: n.y, moved: false };
      listen((ev) => {
        const dx = (ev.clientX - s.x) / view.k, dy = (ev.clientY - s.y) / view.k;
        if (!s.moved && Math.hypot(ev.clientX - s.x, ev.clientY - s.y) < 4) return;
        if (!s.moved) { s.moved = true; el.classList.add("fe-dragging"); if (n.a && !n.floating) nodes.filter((m) => m.a === n.a && !m.floating).forEach((m) => { store.pos[m.id] = { x: m.x, y: m.y }; }); }
        n.x = s.ox + dx; n.y = s.oy + dy; el.style.left = n.x + "px"; el.style.top = n.y + "px"; drawEdges();
      }, () => {
        el.classList.remove("fe-dragging");
        if (s.moved) { store.pos[n.id] = { x: Math.round(n.x), y: Math.round(n.y) }; persist(); } else pick({ type: "node", id: n.id });
      });
    }
    function startPan(e) {
      const s = { x: e.clientX, y: e.clientY, vx: view.x, vy: view.y, moved: false };
      canvas.classList.add("fe-panning");
      listen((ev) => { if (Math.hypot(ev.clientX - s.x, ev.clientY - s.y) > 3) s.moved = true; view.x = s.vx + ev.clientX - s.x; view.y = s.vy + ev.clientY - s.y; applyView(); },
        () => { canvas.classList.remove("fe-panning"); if (!s.moved) pick(null); });
    }
    function startConnect(e, from) {
      e.preventDefault(); e.stopPropagation();
      const preview = svgEl("path", { class: "fe-edge-preview", d: "" }); svg.append(preview);
      canvas.classList.add("fe-connecting");
      nodes.forEach((n) => { if (n !== from && ["attach", "move"].includes(plan(from, n).type)) elById[n.id].classList.add("fe-valid"); });
      const x1 = from.x + W, y1 = from.y + H / 2;
      const draw = (ev) => { const p = toGraph(ev.clientX, ev.clientY); preview.setAttribute("d", curve(x1, y1, p.x, p.y)); };
      draw(e);
      listen(draw, (ev) => {
        preview.remove(); canvas.classList.remove("fe-connecting"); Object.values(elById).forEach((el) => el.classList.remove("fe-valid"));
        const hit = document.elementFromPoint(ev.clientX, ev.clientY), r = canvas.getBoundingClientRect();
        if (ev.clientX < r.left || ev.clientX > r.right || ev.clientY < r.top || ev.clientY > r.bottom) return;
        const t = hit && hit.closest && hit.closest(".fe-node"), to = t ? byId[t.dataset.id] : null;
        if (!to && hit && hit.closest && hit.closest(".fe-bar")) return;
        runPlan(plan(from, to), toGraph(ev.clientX, ev.clientY));
      });
    }
    canvas.addEventListener("pointerdown", (e) => {
      if (e.button !== 0 || e.target.closest(".fe-bar")) return;
      canvas.focus({ preventScroll: true });
      const nodeEl = e.target.closest(".fe-node"), hit = e.target.closest(".fe-edge-hit");
      if (nodeEl && e.target.closest(".flow-port-out")) return startConnect(e, byId[nodeEl.dataset.id]);
      if (nodeEl) return startDrag(e, byId[nodeEl.dataset.id], nodeEl);
      if (hit) { pick({ type: "edge", id: hit.getAttribute("data-edge") }); return; }
      startPan(e);
    });
    canvas.addEventListener("wheel", (e) => { e.preventDefault(); zoomAt(e.clientX, e.clientY, Math.exp(-e.deltaY * (e.ctrlKey ? 0.01 : 0.0012))); }, { passive: false });
    canvas.addEventListener("keydown", (e) => {
      if (/^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName)) return;
      if (e.key === "Escape") pick(null);
      if ((e.key === "Delete" || e.key === "Backspace") && selected) { e.preventDefault(); deleteSelected(); }
    });
    async function deleteSelected() {
      if (!selected) return;
      try {
        if (selected.type === "edge") { const e = edges.find((x) => x.id === selected.id), n = e && e.kind === "out" && byId[e.from]; if (n) await removeRule(n, { toFloating: true }); else toast("This link is part of the action itself. Select a rule to remove it.", "info"); return; }
        const n = byId[selected.id]; if (!n) return;
        if (n.kind === "draft") { store.drafts = store.drafts.filter((d) => d.id !== n.id); delete store.pos[n.id]; selected = null; persist(); refresh(); }
        else if (n.kind === "rule" && n.floating) { store.floating = store.floating.filter((f) => f.id !== n.id); delete store.pos[n.id]; selected = null; persist(); refresh(); }
        else if (n.kind === "rule") { await removeRule(n, { toFloating: false }); toast("Rule removed.", "success"); }
        else if (n.kind === "action") ctx.remove(n.a);
        else toast("An intent belongs to its action. Delete the action to remove it.", "info");
      } catch (e) { reportError(e); refresh(); }
    }

    // ---------------------------------------------------------------- palette (drag onto the canvas, or click)
    function dropItem(o, p) {
      if (o.kind === "rule") { const f = { id: uid(), name: "New rule", requires: [], on_missing: "ask", reply_if_missing: "", x: Math.round(p.x), y: Math.round(p.y) }; store.floating.push(f); persist(); selected = { type: "node", id: f.id }; refresh(); toast("Drag the rule's right-hand dot onto an action to connect it.", "info"); }
      else {
        const d = { id: uid().replace(/^f/, "d"), type: o.type, x: Math.round(p.x), y: Math.round(p.y) };
        store.drafts.push(d); persist(); selected = { type: "node", id: d.id }; refresh();
      }
    }
    function startPalDrag(e, o) {
      if (e.button !== 0) return;
      e.preventDefault();
      const s = { x: e.clientX, y: e.clientY, ghost: null };
      listen((ev) => {
        if (!s.ghost && Math.hypot(ev.clientX - s.x, ev.clientY - s.y) > 4) { s.ghost = h("div", { class: "fe-ghost" }, o.label); document.body.append(s.ghost); }
        if (s.ghost) { s.ghost.style.left = ev.clientX + "px"; s.ghost.style.top = ev.clientY + "px"; }
      }, (ev) => {
        const dragged = !!s.ghost; if (s.ghost) s.ghost.remove();
        if (!dragged) { dropItem(o, centerPoint()); return; }
        const r = canvas.getBoundingClientRect();
        if (ev.clientX >= r.left && ev.clientX <= r.right && ev.clientY >= r.top && ev.clientY <= r.bottom) { const p = toGraph(ev.clientX, ev.clientY); dropItem(o, { x: p.x - W / 2, y: p.y - H / 2 }); }
      });
    }
    function palItem(o) {
      const b = h("button", { type: "button", class: "fe-item" + (o.kind === "rule" ? " fe-item-rule" : ""), disabled: o.disabled, title: (o.hint ? o.hint + ". " : "") + o.desc + " (drag onto the canvas, or click to add)" },
        h("span", { class: "fe-item-ico" }, o.icon), h("span", { class: "fe-item-t" }, o.label));
      b.addEventListener("pointerdown", (e) => startPalDrag(e, o));
      b.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); dropItem(o, centerPoint()); } });
      return b;
    }
    function renderPalette() {
      clear(pal).append(...[h("span", { class: "fe-h" }, "Add"),
        palItem({ kind: "rule", label: "Rule", desc: "Conditions that must hold before an action runs", icon: ICON.rule }),
        h("span", { class: "fe-sep" }),
        ...ctx.templates.map((t) => { const off = t.needs_connection && !ctx.connections.length; return palItem({ kind: "action", type: t.type, label: SHORT[t.type] || t.label, desc: t.description, icon: ctx.icons[t.icon] || "⚙️", disabled: off, hint: off ? "Connect a database first" : "" }); }),
        ctx.connections.length ? null : h("a", { class: "fe-link", href: "/connections" }, "Connect a database →")].filter(Boolean));
    }

    // ---------------------------------------------------------------- inspector
    const btn = (label, cls, fn) => h("button", { type: "button", class: cls, onclick: fn }, label);
    const kindBadge = (k, text) => h("span", { class: `fe-kind fe-kind-${k}` }, text);
    async function guarded(button, fn) { button.disabled = true; try { await fn(); } catch (e) { reportError(e); } finally { button.disabled = false; } }

    function renderInspector() {
      clear(insp); feEl.classList.remove("fe-wide");
      if (!selected) {
        insp.append(h("div", { class: "fe-h" }, "Inspector"),
          h("div", { class: "stack text-sm text-slate-600" },
            h("p", {}, "Click a node or a link to edit it here."),
            h("ul", { class: "list-disc space-y-1 pl-5 text-xs text-slate-500" },
              h("li", {}, "Drag nodes to arrange them, drag the background to pan, scroll to zoom."),
              h("li", {}, "Drag from the right-hand dot of an intent into empty space to add a rule."),
              h("li", {}, "Drag a rule's dot onto an action to attach or move it."),
              h("li", {}, "Select anything and press Delete to remove it.")),
            h("p", { class: "text-xs text-slate-500" }, `${ctx.actions.length} action${ctx.actions.length === 1 ? "" : "s"} on the canvas.`)));
        return;
      }
      if (selected.type === "edge") {
        const e = edges.find((x) => x.id === selected.id); if (!e) return;
        const body = h("div", { class: "stack" }, h("div", { class: "fe-h" }, "Link"),
          h("p", { class: "text-sm text-slate-600" }, e.kind === "out" ? (e.style === "dashed" ? "This rule skips the action when an input is missing." : "This rule asks the user for any missing input before the action runs.") : "This link is part of the action's own pipeline."));
        if (e.kind === "out") body.append(btn("Disconnect rule", "btn-secondary btn-sm", () => guarded(body, () => removeRule(byId[e.from], { toFloating: true }))), h("p", { class: "text-xs text-slate-500" }, "The rule stays on the canvas, unconnected, so you can attach it to another action."));
        insp.append(body); return;
      }
      const n = byId[selected.id]; if (!n) return;
      ({ intent: inspectIntent, rule: inspectRule, action: inspectAction, draft: inspectDraft })[n.kind](n);
    }

    function inspectIntent(n) {
      const a = n.a;
      const intent = h("input", { class: "input font-mono", type: "text", maxlength: 60, value: a.intent || "" });
      const cmds = h("textarea", { class: "input font-mono text-sm", rows: 6, placeholder: "/products\nshow me tents under 200\nwhat do you sell?" }, (a.commands || []).join("\n"));
      const save = btn("Apply", "btn-primary btn-sm", () => guarded(save, async () => {
        const changed = intent.value.trim() !== a.intent;
        await saveFlow(a, { intent: intent.value.trim(), commands: cmds.value.split("\n").map((x) => x.trim()).filter(Boolean), rules: (a.rules || []).map((r) => (changed ? Object.assign({}, r, { yaml: "" }) : r)) });
        toast("Intent saved.", "success"); refresh();
      }));
      insp.append(h("div", { class: "stack" }, kindBadge("intent", "🎯 Intent · trigger"), h("p", { class: "text-xs text-slate-500" }, `Starts the action “${a.name}”.`),
        field("Intent name", intent, "Rasa-style name, e.g. ask_search_products.", null),
        field("Commands", cmds, "One per line. /slash-commands or phrases users might say.", null), h("div", {}, save)));
    }

    function inspectRule(n) {
      const r = n.rule, a = n.a, names = a ? (a.parameters || []).map((p) => p.name).filter((x) => x && x !== "id") : [];
      const name = h("input", { class: "input", type: "text", maxlength: 80, value: r.name || "" });
      const miss = select([["ask", "Ask the user for what's missing"], ["skip", "Skip the action (dashed link)"]], r.on_missing || "ask");
      const reply = h("input", { class: "input", type: "text", maxlength: 300, value: r.reply_if_missing || "", placeholder: "Optional question to ask" });
      const chosen = new Set(r.requires || []);
      const boxes = h("div", { class: "fe-checks" }, !a ? h("span", { class: "text-xs text-slate-500" }, "Connect this rule to an action, then choose which inputs it requires.")
        : !names.length ? h("span", { class: "text-xs text-slate-500" }, "This action has no inputs yet. Add some in “Edit settings”.")
          : names.map((nm) => { const cb = h("input", { type: "checkbox", checked: chosen.has(nm), class: "h-4 w-4 rounded border-slate-300 text-brand-600" }); cb.addEventListener("change", () => (cb.checked ? chosen.add(nm) : chosen.delete(nm)));
            return h("label", { class: "flex items-center gap-1.5 text-sm" }, cb, h("span", { class: "font-mono" }, nm)); }));
      // ---- conditions ("only run when ...")
      const conds = (r.conditions || []).map((c) => Object.assign({}, c)), condBox = h("div", { class: "space-y-2" });
      const match = select([["all", "ALL conditions are true"], ["any", "ANY condition is true"]], r.match || "all");
      const allNames = a ? (a.parameters || []).map((p) => p.name).filter(Boolean) : [];
      function drawConds() {
        clear(condBox);
        if (!a) { condBox.append(h("span", { class: "text-xs text-slate-500" }, "Connect this rule to an action to add conditions on its inputs.")); return; }
        if (!conds.length) condBox.append(h("span", { class: "text-xs text-slate-500" }, "No conditions: the rule only checks the inputs above."));
        conds.forEach((c, i) => {
          const f = select(allNames.map((x) => [x, x]), c.field || allNames[0], { "aria-label": "Input" }), o = select(OPS, c.op || "eq", { "aria-label": "Comparison" });
          const v = h("input", { class: "input", type: "text", maxlength: 200, value: c.value || "", placeholder: "value", "aria-label": "Value" });
          const sync = () => { c.field = f.value; c.op = o.value; c.value = v.value; v.style.display = ["empty", "not_empty"].includes(o.value) ? "none" : ""; };
          [f, o, v].forEach((el) => el.addEventListener("input", sync)); sync();
          condBox.append(h("div", { class: "rounded-lg border border-slate-200 bg-slate-50/70 p-2 space-y-1.5" }, f, o, v,
            h("button", { type: "button", class: "text-xs font-medium text-red-600 hover:underline", onclick: () => { conds.splice(i, 1); drawConds(); } }, "Remove condition")));
        });
        if (conds.length < 6 && allNames.length) condBox.append(h("button", { type: "button", class: "btn-secondary btn-sm", onclick: () => { conds.push({ field: allNames[0], op: "eq", value: "" }); drawConds(); } }, "+ Add condition"));
      }
      drawConds();
      const save = btn("Apply", "btn-primary btn-sm", () => guarded(save, async () => {
        const upd = { name: name.value.trim(), requires: [...chosen].filter((x) => names.includes(x)), on_missing: miss.value, reply_if_missing: reply.value.trim(), conditions: conds, match: match.value };
        if (n.floating) { Object.assign(r, upd); persist(); toast("Rule updated.", "success"); refresh(); return; }
        await saveFlow(a, { rules: a.rules.map((x, i) => (i === n.index ? Object.assign({}, upd, { yaml: "" }) : x)) });
        toast("Rule saved.", "success"); refresh();
      }));
      const del = btn("Delete rule", "btn-ghost btn-sm text-red-600 hover:bg-red-50", () => guarded(del, deleteSelected));
      insp.append(h("div", { class: "stack" }, kindBadge("rule", "🧩 Rule" + (n.floating ? " · not connected" : "")),
        h("p", { class: "text-xs text-slate-500" }, n.floating ? "Drag this rule's right-hand dot onto an action to connect it." : `Runs before “${a.name}”.`),
        field("Rule name", name, null, null), field("Needs these inputs", boxes, null, null), field("If an input is missing", miss, null, null),
        field("Only run when", h("div", { class: "space-y-2" }, match, condBox), "Checked on every run, for every action type. Conditions run in this app; the Rasa export covers inputs only.", null),
        field("Message when this rule stops the action", reply, "Also the question asked for a missing input. Leave empty for a default message.", null),
        h("div", { class: "flex gap-2" }, save, del)));
    }

    /** Embeds the full action form in the inspector (wider panel). Used for new drafts and for "Edit settings". */
    function embedForm(type, existing, { onSaved, onCancel }) {
      clear(insp); feEl.classList.add("fe-wide");
      const host = h("div", {}); insp.append(host);
      ctx.buildForm(host, type, { existing, onSaved, onCancel });
    }
    function inspectDraft(n) {
      const done = () => { store.drafts = store.drafts.filter((d) => d.id !== n.id); delete store.pos[n.id]; persist(); };
      embedForm(n.type, null, {
        onCancel: () => { done(); selected = null; refresh(); },
        onSaved: (saved) => { App.flowEditor.placeLane(saved.id, n.x, n.y); done(); pendingSelect = "a" + saved.id; ctx.reload(); },
      });
    }
    function inspectAction(n) {
      const a = n.a;
      const en = h("input", { type: "checkbox", checked: a.enabled, class: "h-4 w-4 rounded border-slate-300 text-brand-600" });
      en.addEventListener("change", async () => { try { await api.post(`/api/actions/${a.id}/enabled`, { enabled: en.checked }); a.enabled = en.checked; toast(en.checked ? "Action enabled." : "Action disabled.", "success"); refresh(); } catch (e) { en.checked = !en.checked; reportError(e); } });
      const badges = [h("span", { class: "badge-gray" }, a.template_label), h("span", { class: a.is_write ? "badge-amber" : "badge-green" }, a.is_write ? "changes data" : "read-only")];
      if (a.is_write) badges.push(h("span", { class: a.approved ? "badge-green" : "badge-red" }, a.approved ? "approved" : "needs your approval"));
      insp.append(h("div", { class: "stack" }, kindBadge("action", `${ICON[a.template_type] || "⚙️"} Action`), h("h3", { class: "text-base font-semibold" }, a.name),
        h("div", { class: "flex flex-wrap gap-1.5" }, badges), h("p", { class: "rounded-lg bg-slate-50 px-3 py-2 text-xs text-slate-600" }, ctx.describe(a)),
        a.when_to_use ? h("p", { class: "text-xs text-slate-500" }, h("span", { class: "font-medium" }, "Used when: "), a.when_to_use) : null,
        h("p", { class: "text-xs text-slate-500" }, `${(a.rules || []).length} rule${(a.rules || []).length === 1 ? "" : "s"} · intent `, h("span", { class: "font-mono" }, a.intent)),
        h("label", { class: "flex items-center gap-2 text-sm" }, en, "Enabled"),
        h("div", { class: "flex flex-wrap gap-2" }, btn("Test", "btn-primary btn-sm", () => ctx.test(a)), btn("Edit settings", "btn-secondary btn-sm", () => embedForm(a.template_type, a, { onCancel: () => renderInspector(), onSaved: () => { pendingSelect = "a" + a.id; ctx.reload(); } })),
          a.is_write ? btn(a.approved ? "Revoke approval" : "Review & approve", a.approved ? "btn-ghost btn-sm" : "btn-secondary btn-sm", () => ctx.approve(a)) : null,
          btn("Delete", "btn-ghost btn-sm text-red-600 hover:bg-red-50", () => ctx.remove(a)))));
    }

    renderPalette(); refresh();
    if (pendingSelect && byId[pendingSelect]) pick({ type: "node", id: pendingSelect });
    pendingSelect = null;
    if (!store.view) requestAnimationFrame(() => fit(true));
  }

  // A newly created action lands where it was dropped: intent at the drop point, action two columns to the right.
  function placeLane(id, x, y) { store.pos["i" + id] = { x, y }; store.pos["a" + id] = { x: x + 2 * DX, y }; persist(); }

  App.flowEditor = { mount, placeLane };
})();

/* Shared helpers. No inline scripts are allowed by the CSP, so every page loads this file first.
 * All user-supplied text is inserted with textContent (via h()), never innerHTML. */
(function () {
  "use strict";

  const csrf = () => document.querySelector('meta[name="csrf-token"]').content;

  /** Hyperscript-style element builder. h("div", {class: "x", onclick: fn}, "text", childNode) */
  function h(tag, attrs, ...children) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v === null || v === undefined || v === false) continue;
      if (k === "class") el.className = v;
      else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
      else if (k === "value") el.value = v;
      else if (k === "checked" || k === "disabled" || k === "selected" || k === "required") el[k] = !!v;
      else el.setAttribute(k, v === true ? "" : v);
    }
    append(el, children);
    return el;
  }
  function append(el, children) {
    for (const c of children.flat(Infinity)) {
      if (c === null || c === undefined || c === false) continue;
      el.append(c instanceof Node ? c : document.createTextNode(String(c)));
    }
    return el;
  }
  const $ = (sel, root) => (root || document).querySelector(sel);
  const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));
  function clear(el) { while (el.firstChild) el.removeChild(el.firstChild); return el; }

  /** JSON API wrapper. Throws ApiError with .message, .fields, .status. */
  class ApiError extends Error {
    constructor(message, status, fields, body) { super(message); this.status = status; this.fields = fields || {}; this.body = body || {}; }
  }
  async function request(method, url, body, opts) {
    const init = { method, headers: { "X-CSRF-Token": csrf(), Accept: "application/json" }, credentials: "same-origin" };
    if (body instanceof FormData) init.body = body;
    else if (body !== undefined) { init.headers["Content-Type"] = "application/json"; init.body = JSON.stringify(body); }
    let res;
    try { res = await fetch(url, init); }
    catch (e) { throw new ApiError("Could not reach the server. Check your connection and try again.", 0); }
    if (res.status === 401) { window.location.href = "/login?next=" + encodeURIComponent(location.pathname); throw new ApiError("Please sign in.", 401); }
    if (opts && opts.raw) { if (!res.ok) throw new ApiError("Request failed.", res.status); return res; }
    let data = {};
    try { data = await res.json(); } catch (e) { /* non-JSON error page */ }
    if (!res.ok) throw new ApiError(data.error || "Something went wrong. Please try again.", res.status, data.fields, data);
    return data;
  }
  const api = {
    get: (u) => request("GET", u),
    post: (u, b) => request("POST", u, b === undefined ? {} : b),
    put: (u, b) => request("PUT", u, b),
    del: (u) => request("DELETE", u),
    upload: (u, fd) => request("POST", u, fd),
    raw: (m, u, b) => request(m, u, b, { raw: true }),
  };

  /** Toast notifications. */
  function toast(message, kind) {
    const styles = { success: "bg-emerald-600", error: "bg-red-600", info: "bg-slate-800" };
    const el = h("div", { class: "pointer-events-auto max-w-md rounded-lg px-4 py-2.5 text-sm font-medium text-white shadow-lg " + (styles[kind] || styles.info), role: kind === "error" ? "alert" : "status" }, message);
    $("#toasts").append(el);
    setTimeout(() => { el.classList.add("opacity-0", "transition-opacity"); setTimeout(() => el.remove(), 300); }, kind === "error" ? 6000 : 3200);
  }

  /** Modal dialog. Returns {close, body}. `content` is a Node. */
  function modal(title, content, opts) {
    opts = opts || {};
    const root = $("#modal-root");
    const prevFocus = document.activeElement;
    const close = () => { backdrop.remove(); document.removeEventListener("keydown", onKey); if (prevFocus && prevFocus.focus) prevFocus.focus(); if (opts.onClose) opts.onClose(); };
    const onKey = (e) => { if (e.key === "Escape") close(); };
    const panel = h("div", { class: "relative max-h-[90vh] w-full overflow-y-auto rounded-xl bg-white shadow-xl " + (opts.wide ? "max-w-3xl" : "max-w-lg"), role: "dialog", "aria-modal": "true", "aria-label": title },
      h("div", { class: "flex items-center justify-between border-b border-slate-200 px-5 py-3" },
        h("h2", { class: "text-base font-semibold" }, title),
        h("button", { class: "btn-ghost -mr-2 px-2", type: "button", "aria-label": "Close", onclick: close }, "✕")),
      h("div", { class: "p-5" }, content));
    const backdrop = h("div", { class: "fixed inset-0 z-50 flex items-center justify-center bg-slate-900/50 p-4", onmousedown: (e) => { if (e.target === backdrop) close(); } }, panel);
    root.append(backdrop);
    document.addEventListener("keydown", onKey);
    const first = $("input, select, textarea, button.btn-primary", panel);
    if (first) first.focus();
    return { close, panel };
  }

  /** Promise-based confirmation dialog. */
  function confirmDialog(title, message, confirmLabel, danger) {
    return new Promise((resolve) => {
      let done = false;
      const finish = (v) => { if (!done) { done = true; resolve(v); } m.close(); };
      const body = h("div", { class: "space-y-5" },
        h("p", { class: "whitespace-pre-line text-sm text-slate-600" }, message),
        h("div", { class: "flex justify-end gap-2" },
          h("button", { class: "btn-secondary", type: "button", onclick: () => finish(false) }, "Cancel"),
          h("button", { class: danger ? "btn-danger" : "btn-primary", type: "button", onclick: () => finish(true) }, confirmLabel || "Confirm")));
      const m = modal(title, body, { onClose: () => { if (!done) { done = true; resolve(false); } } });
    });
  }

  /** Disable a button and show a spinner while an async fn runs. */
  async function withLoading(btn, fn) {
    if (!btn) return fn();
    if (btn.dataset.busy) return;
    btn.dataset.busy = "1"; btn.disabled = true;
    const spin = h("span", { class: "spinner" });
    btn.prepend(spin);
    try { return await fn(); }
    finally { spin.remove(); btn.disabled = false; delete btn.dataset.busy; }
  }

  /** Show server-side field errors next to inputs named by data-field (or name). Returns true if any matched. */
  function showFieldErrors(form, fields) {
    $$(".field-error", form).forEach((e) => e.remove());
    $$(".input-error", form).forEach((e) => e.classList.remove("input-error"));
    let first = null, any = false;
    for (const [key, msg] of Object.entries(fields || {})) {
      const el = form.querySelector(`[data-field="${CSS.escape(key)}"]`) || form.querySelector(`[name="${CSS.escape(key)}"]`);
      if (!el) continue;
      any = true;
      el.classList.add("input-error");
      el.insertAdjacentElement("afterend", h("p", { class: "field-error", role: "alert" }, msg));
      first = first || el;
    }
    if (first) first.focus();
    return any;
  }
  function clearFieldErrors(form) { showFieldErrors(form, {}); }

  /** Report an ApiError: field errors inline if possible, otherwise a toast. */
  function reportError(err, form) {
    const fields = err.fields || {};
    const matched = form ? showFieldErrors(form, fields) : false;
    const unmatched = Object.entries(fields).filter(([k]) => !(form && (form.querySelector(`[data-field="${CSS.escape(k)}"]`) || form.querySelector(`[name="${CSS.escape(k)}"]`))));
    let msg = err.message || "Something went wrong.";
    if (!matched || unmatched.length) msg += unmatched.length ? " — " + unmatched.map(([k, v]) => `${k.replace(/[._]/g, " ")}: ${v}`).join("; ") : "";
    toast(msg, "error");
  }

  function emptyState(title, text, actions) {
    return h("div", { class: "card flex flex-col items-center px-6 py-14 text-center" },
      h("p", { class: "text-base font-semibold text-slate-800" }, title),
      h("p", { class: "mt-1 max-w-md text-sm text-slate-500" }, text),
      actions ? h("div", { class: "mt-5 flex flex-wrap justify-center gap-2" }, actions) : null);
  }
  function loadingBlock(text) {
    return h("div", { class: "flex items-center justify-center gap-2 py-10 text-sm text-slate-500" }, h("span", { class: "spinner" }), text || "Loading…");
  }
  function errorBlock(text, retry) {
    return h("div", { class: "card flex flex-col items-center px-6 py-10 text-center" },
      h("p", { class: "text-sm font-medium text-red-700" }, text),
      retry ? h("button", { class: "btn-secondary mt-4", type: "button", onclick: retry }, "Try again") : null);
  }

  function fmtDate(iso) {
    if (!iso) return "";
    const d = new Date(iso);
    return isNaN(d) ? iso : d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
  }

  /** Render an array of row objects as a table (cells via textContent). */
  function dataTable(rows) {
    if (!rows || !rows.length) return h("p", { class: "text-sm text-slate-500" }, "No rows.");
    const cols = Object.keys(rows[0]);
    return h("div", { class: "overflow-x-auto rounded-lg border border-slate-200" },
      h("table", { class: "table-simple" },
        h("thead", {}, h("tr", {}, cols.map((c) => h("th", {}, c)))),
        h("tbody", { class: "divide-y divide-slate-100 bg-white" },
          rows.map((r) => h("tr", {}, cols.map((c) => h("td", {}, r[c] === null || r[c] === undefined ? "—" : typeof r[c] === "object" ? JSON.stringify(r[c]) : String(r[c]))))))));
  }
  /** Render any action result data (list of rows, object, or scalar). */
  function renderData(data) {
    if (data === null || data === undefined) return null;
    if (Array.isArray(data) && data.length && data.every((r) => r && typeof r === "object" && !Array.isArray(r))) return dataTable(data);
    if (data && typeof data === "object" && !Array.isArray(data)) {
      const keys = Object.keys(data);
      if (keys.length && keys.every((k) => data[k] === null || typeof data[k] !== "object")) return dataTable([data]);
    }
    return h("pre", { class: "max-h-64 overflow-auto rounded-lg bg-slate-900 p-3 text-xs text-slate-100" }, typeof data === "string" ? data : JSON.stringify(data, null, 2));
  }

  /** Form-field builders used across pages. */
  function field(label, control, hint, key) {
    if (key && control.setAttribute) control.setAttribute("data-field", key);
    return h("div", {}, h("label", { class: "label" }, label), control, hint ? h("p", { class: "hint" }, hint) : null);
  }
  function select(options, value, attrs) {
    const el = h("select", Object.assign({ class: "input" }, attrs || {}),
      options.map((o) => { const [v, l] = Array.isArray(o) ? o : [o, o]; return h("option", { value: v, selected: String(v) === String(value) }, l); }));
    return el;
  }

  // Mobile navigation drawer
  document.addEventListener("DOMContentLoaded", () => {
    const sb = $("#sidebar"), bd = $("#sidebar-backdrop"), btn = $("#menu-btn");
    const set = (open) => { sb.classList.toggle("-translate-x-full", !open); bd.classList.toggle("hidden", !open); };
    if (btn) btn.addEventListener("click", () => set(true));
    if (bd) bd.addEventListener("click", () => set(false));
  });

  window.App = { h, $, $$, clear, api, ApiError, toast, modal, confirmDialog, withLoading, showFieldErrors, clearFieldErrors, reportError,
    emptyState, loadingBlock, errorBlock, fmtDate, dataTable, renderData, field, select };
})();

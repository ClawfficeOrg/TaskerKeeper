/* TaskerKeeper OpenChamber rail panel.
 *
 * Dependency-free IIFE (no imports): the sandbox iframe loads this as a
 * classic script, so there is no build step. If @openchamber/sdk imports are
 * ever added, bundle with:
 *   bunx openchamber-guest-bundle panel/main.js panel/main.js
 * (or esbuild --format=iife --platform=browser) and ship the built file.
 *
 * Data: TaskerKeeper serve API over host.request (integration token
 * apiOrigin points at the TaskerKeeper server):
 *   GET /api/<slug>/ready?disjoint=1 -> { ready, in_progress, blocked, deferred, conflicts }
 *   GET /api/<slug>/list             -> { phases: [{ id, title, complete, tasks }], summary }
 * Five sections mirror taskerkeeper/sidebar.py: Current, Concurrent,
 * Upcoming, phase tree, overall. Click a task to attach it to the chat.
 * Read-only: no prompt/sessions/files capabilities needed.
 */
(function () {
  "use strict";

  var DEFAULT_SLUG = "taskerkeeper";
  var DEFAULT_POLL_MS = 15000;
  var MIN_POLL_MS = 5000;

  function resolveConnectHost() {
    try {
      var w = typeof window !== "undefined" ? window : null;
      if (!w) return null;
      var cands = [
        w.connectHost,
        w.openchamber && w.openchamber.connectHost,
        w.OpenChamber && w.OpenChamber.connectHost,
        w.OpenChamberSdk && w.OpenChamberSdk.connectHost
      ];
      for (var i = 0; i < cands.length; i++) {
        if (typeof cands[i] === "function") return cands[i];
      }
    } catch (e) { /* no window access */ }
    return null;
  }

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  var root = document.getElementById("root");
  var host = null;
  var ctxState = { surface: "panel", settings: {}, item: null };
  var slug = DEFAULT_SLUG;
  var pollMs = DEFAULT_POLL_MS;
  var timer = null;
  var generation = 0;
  var lastUpdated = null;
  var view = { ready: null, list: null, error: null };
  var connected = false;

  function settingsSlug(s) {
    var v = s && s.slug != null ? String(s.slug).trim() : "";
    return v || DEFAULT_SLUG;
  }

  function settingsPollMs(s) {
    var raw = s && s.pollSeconds != null ? Number(s.pollSeconds) : NaN;
    if (!isFinite(raw) || raw <= 0) return DEFAULT_POLL_MS;
    return Math.max(MIN_POLL_MS, Math.round(raw * 1000));
  }

  function apiPath(rest) {
    return "/api/" + slug + rest;
  }

  function requestJson(path, query) {
    return host.request({ method: "GET", path: path, query: query || {} }).then(function (res) {
      if (!res || res.status !== 200) {
        throw new Error("server answered " + (res && res.status));
      }
      return JSON.parse(res.body);
    });
  }

  function refresh() {
    if (!host) return;
    if (!connected) {
      view.error = "Not connected. Paste a TaskerKeeper token in Settings -> Integrations -> TaskerKeeper.";
      render();
      return;
    }
    var gen = ++generation;
    Promise.all([
      requestJson(apiPath("/ready"), { disjoint: "1" }),
      requestJson(apiPath("/list"), {})
    ]).then(function (parts) {
      if (gen !== generation) return;
      view.ready = parts[0];
      view.list = parts[1];
      view.error = null;
      lastUpdated = new Date();
      render();
      updateBadge();
    }).catch(function (err) {
      if (gen !== generation) return;
      view.error = err && err.code ? err.code + ": " + err.message : String(err);
      render();
    });
  }

  function updateBadge() {
    if (!host || typeof host.setBadge !== "function") return;
    try {
      var n = view.ready && view.ready.ready ? view.ready.ready.length : 0;
      var m = view.ready && view.ready.in_progress ? view.ready.in_progress.length : 0;
      var total = n + m;
      host.setBadge(total > 0 ? total : null);
    } catch (e) { /* badge is best-effort */ }
  }

  function attachTask(t) {
    if (!host) return;
    var text = (t.goal || t.title || t.id) + " [" + (t.status || "pending") + "]";
    host.attach({ providerId: "taskerkeeper", id: String(t.id), title: String(t.title || t.id), text: String(text).slice(0, 16000) }).then(function () {
      if (ctxState.surface === "dialog" && typeof host.close === "function") host.close();
      if (typeof host.toast === "function") host.toast({ kind: "success", message: "Attached " + t.id });
    }).catch(function (err) {
      if (typeof host.toast === "function") {
        host.toast({ kind: "error", message: "Attach failed: " + (err && err.message ? err.message : err) });
      }
    });
  }

  /* Mirror sidebar_payload anchor: first phase holding a current/upcoming id,
   * else first incomplete phase, else first phase. */
  function currentPhase(phases, anchorIds) {
    var i, j;
    for (i = 0; i < phases.length; i++) {
      var tasks = phases[i].tasks || [];
      for (j = 0; j < tasks.length; j++) {
        if (anchorIds.indexOf(tasks[j].id) !== -1) return phases[i];
      }
    }
    for (i = 0; i < phases.length; i++) {
      if (!phases[i].complete) return phases[i];
    }
    return phases[0] || null;
  }

  function taskButton(t, sub) {
    var hl = ctxState.item && (ctxState.item.id === t.id) ? " hl" : "";
    var html = "<button class=\"tk-task" + hl + "\" data-id=\"" + esc(t.id) + "\">";
    html += "<span class=\"tk-id\">" + esc(t.id) + "</span> &mdash; " + esc(t.title || "");
    if (sub) html += "<br><span class=\"tk-sub\">" + esc(sub) + "</span>";
    html += "</button>";
    return html;
  }

  function render() {
    var html = "";
    html += "<div class=\"tk-head\"><div class=\"tk-title\">TaskerKeeper" + (slug ? " &middot; " + esc(slug) : "") + "</div>";
    html += "<button class=\"tk-refresh\" id=\"tk-refresh\" type=\"button\">Refresh</button></div>";
    html += "<div class=\"tk-meta\">" + (lastUpdated ? "Updated " + esc(lastUpdated.toLocaleTimeString()) : "Not loaded yet") + "</div>";
    if (view.error) {
      html += "<div class=\"tk-error\">" + esc(view.error) + "</div>";
    }
    if (!view.ready && !view.list) {
      html += "<div class=\"tk-none\">Loading&hellip;</div>";
      root.innerHTML = html;
      wire();
      return;
    }

    var ready = (view.ready && view.ready.ready) || [];
    var inProgress = (view.ready && view.ready.in_progress) || [];
    var deferred = (view.ready && view.ready.deferred) || [];
    var blocked = (view.ready && view.ready.blocked) || [];
    var phases = (view.list && view.list.phases) || [];

    /* Current */
    html += "<div class=\"tk-section\"><h2>Current</h2>";
    if (!inProgress.length) html += "<div class=\"tk-none\">(none)</div>";
    inProgress.slice(0, 6).forEach(function (t) {
      html += taskButton(t, t.owner || t.claimed_by || t.status);
    });
    if (inProgress.length > 6) html += "<div class=\"tk-more\">&hellip;" + (inProgress.length - 6) + " more</div>";
    html += "</div>";

    /* Concurrent (safe to fan out: disjoint set from ?disjoint=1) */
    html += "<div class=\"tk-section\"><h2>Concurrent</h2>";
    if (!ready.length) html += "<div class=\"tk-none\">(none)</div>";
    ready.slice(0, 4).forEach(function (t) {
      html += taskButton(t, t.status);
    });
    if (ready.length > 4) html += "<div class=\"tk-more\">&hellip;" + (ready.length - 4) + " more</div>";
    html += "</div>";

    /* Upcoming: deferred (path overlap) + top blocked */
    var upcoming = [];
    deferred.forEach(function (d) {
      upcoming.push({ task: d.task || d, sub: "waits on files: " + ((d.conflicts_with || []).join(", ") || "?") });
    });
    blocked.forEach(function (b) {
      upcoming.push({ task: { id: b.id, title: b.title, status: "pending", goal: "" }, sub: "waits: " + ((b.blocked_by || []).join(", ") || "?") });
    });
    html += "<div class=\"tk-section\"><h2>Upcoming</h2>";
    if (!upcoming.length) html += "<div class=\"tk-none\">(none)</div>";
    upcoming.slice(0, 5).forEach(function (u) {
      html += taskButton(u.task, u.sub);
    });
    if (upcoming.length > 5) html += "<div class=\"tk-more\">&hellip;" + (upcoming.length - 5) + " more</div>";
    html += "</div>";

    /* Phase tree + overall */
    var anchorIds = inProgress.map(function (t) { return t.id; })
      .concat(ready.map(function (t) { return t.id; }))
      .concat(blocked.map(function (b) { return b.id; }));
    var phase = currentPhase(phases, anchorIds);
    html += "<div class=\"tk-cols\">";
    html += "<div class=\"tk-section\"><h2>" + (phase ? "Phase " + esc(phase.id) : "Phase") + "</h2>";
    if (phase && phase.tasks && phase.tasks.length) {
      html += "<ul class=\"tk-tree\">";
      phase.tasks.slice(0, 20).forEach(function (t) {
        html += "<li>[" + esc(t.status || "?") + "] <span class=\"tk-id\">" + esc(t.id) + "</span> &mdash; " + esc(t.title || "") + "</li>";
      });
      html += "</ul>";
      if (phase.tasks.length > 20) html += "<div class=\"tk-more\">&hellip;" + (phase.tasks.length - 20) + " more</div>";
    } else {
      html += "<div class=\"tk-none\">(none)</div>";
    }
    html += "</div>";
    html += "<div class=\"tk-section\"><h2>Overall</h2>";
    if (!phases.length) html += "<div class=\"tk-none\">(none)</div>";
    else {
      html += "<ul class=\"tk-tree\">";
      phases.forEach(function (p) {
        var done = (p.tasks || []).filter(function (t) { return t.status === "done"; }).length;
        var total = (p.tasks || []).length;
        html += "<li>Phase " + esc(p.id) + ": " + done + "/" + total + " done" + (p.complete ? " (complete)" : "") + "</li>";
      });
      html += "</ul>";
    }
    html += "</div></div>";

    root.innerHTML = html;
    wire();
  }

  function lookup(id) {
    var pools = [];
    if (view.ready) {
      pools = pools.concat(view.ready.in_progress || [], view.ready.ready || []);
      (view.ready.deferred || []).forEach(function (d) { if (d.task) pools.push(d.task); });
    }
    for (var i = 0; i < pools.length; i++) {
      if (String(pools[i].id) === String(id)) return pools[i];
    }
    return { id: id, title: id, status: "pending" };
  }

  function wire() {
    var btn = document.getElementById("tk-refresh");
    if (btn) btn.addEventListener("click", refresh);
    var nodes = root.querySelectorAll(".tk-task");
    for (var i = 0; i < nodes.length; i++) {
      (function (el) {
        el.addEventListener("click", function () { attachTask(lookup(el.getAttribute("data-id"))); });
      })(nodes[i]);
    }
  }

  function reschedule() {
    if (timer) { clearInterval(timer); timer = null; }
    if (host) timer = setInterval(refresh, pollMs);
  }

  function applyCtx(ctx) {
    if (!ctx) return;
    if (ctx.surface) {
      ctxState.surface = ctx.surface;
      document.body.dataset.surface = ctx.surface;
    }
    if (ctx.theme && ctx.theme.mode) document.body.dataset.theme = ctx.theme.mode;
    if (ctx.settings) ctxState.settings = ctx.settings;
    if ("item" in ctx) ctxState.item = ctx.item || null;
    var nextSlug = settingsSlug(ctxState.settings);
    var nextPoll = settingsPollMs(ctxState.settings);
    if (nextSlug !== slug || nextPoll !== pollMs) {
      slug = nextSlug;
      pollMs = nextPoll;
      reschedule();
      refresh();
    } else {
      render();
    }
  }

  function boot() {
    document.body.dataset.surface = ctxState.surface;
    var connectHost = resolveConnectHost();
    if (typeof connectHost !== "function") {
      root.innerHTML = "<div class=\"tk-error\">OpenChamber host not found. Open this panel inside OpenChamber (Settings -&gt; Extensions), or bundle with <code>bunx openchamber-guest-bundle</code> per README.</div>";
      return;
    }
    host = connectHost();
    if (host.onReady) host.onReady(applyCtx);
    if (host.onConnection) host.onConnection(function (c) {
      var key = JSON.stringify(c);
      var was = connected;
      connected = !!(c && c.connected);
      if (connected !== was || key !== boot._lastConn) { boot._lastConn = key; refresh(); }
    });
    if (host.onSettings) host.onSettings(function (s) {
      ctxState.settings = s || {};
      applyCtx({ settings: ctxState.settings });
    });
    reschedule();
    /* Paint once even before the first onReady so the rail never looks dead. */
    render();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();

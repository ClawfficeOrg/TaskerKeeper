// Paseo client half: TaskerKeeper nav badge + workspace panel.
//
// Registers (via the @getpaseo/client SDK shape):
//   - addSidebarItem: "TaskerKeeper" nav entry whose badge shows the
//     in-progress count.
//   - addWorkspacePanel: "TaskerKeeper" tab rendering the five sidebar
//     sections (current with details, concurrent, upcoming with waits,
//     current-phase tree, overall tree) fetched through the server RPC
//     `taskerkeeper.sidebar`. Polls every 15s; every failure renders
//     fail-soft inside the panel (never throws into the host).
"use strict";

const POLL_MS = 15000;
const RPC_METHOD = "taskerkeeper.sidebar";

function esc(value) {
  return String(value === undefined || value === null ? "" : value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function fmtTask(t) {
  t = t || {};
  let head = esc(t.icon || "") + " " + esc(t.id || "?") + " — " + esc(t.title || "");
  let extra = "";
  if (t.owner) {
    extra += " [" + esc(t.owner) + (t.age ? " " + esc(t.age) : "") + "]";
  }
  if (t.blocked_by && t.blocked_by.length) {
    extra += " [waits: " + t.blocked_by.map(esc).join(", ") + "]";
  }
  let html = "<li><div>" + head + esc(extra) + "</div>";
  if (t.goal) {
    html += '<div class="tk-dim">' + esc(t.goal) + "</div>";
  }
  if (t.touches && t.touches.length) {
    html += '<div class="tk-dim">touches: ' + t.touches.map(esc).join(", ") + "</div>";
  }
  return html + "</li>";
}

function section(title, inner, more) {
  let html = "<section><h3>" + esc(title) + "</h3>";
  html += inner || '<p class="tk-dim">(none)</p>';
  if (more) {
    html += '<p class="tk-dim">…' + esc(more) + " more</p>";
  }
  return html + "</section>";
}

function renderPayload(payload) {
  if (!payload) {
    return '<p class="tk-dim">No data yet.</p>';
  }
  const cur = payload.current || { tasks: [] };
  const con = payload.concurrent || { tasks: [] };
  const up = payload.upcoming || { tasks: [] };
  const ph = payload.phase || {};
  const ov = payload.overall || {};
  let html = "";
  html += section(
    "Current",
    "<ul>" + (cur.tasks || []).map(fmtTask).join("") + "</ul>",
    cur.more
  );
  html += section(
    "Concurrent (safe to fan out)",
    "<ul>" + (con.tasks || []).map(fmtTask).join("") + "</ul>",
    con.more
  );
  html += section(
    "Upcoming",
    "<ul>" + (up.tasks || []).map(fmtTask).join("") + "</ul>",
    up.more
  );
  html += section(
    "Phase " + (ph.id || "?") + ": " + (ph.title || ""),
    "<ul>" + ((ph.tree || []).map((l) => "<li>" + esc(l) + "</li>").join("")) + "</ul>",
    ph.more
  );
  html += section(
    "Overall",
    "<ul>" + ((ov.tree || []).map((l) => "<li>" + esc(l) + "</li>").join("")) + "</ul>",
    ov.more
  );
  return html;
}

// Call the server RPC through whatever the host client exposes.
// Supports ctx.callServer / ctx.rpc / client.call in that order so the
// panel works across Paseo client revisions.
async function fetchSidebar(ctx) {
  const params = {
    todo: ctx && ctx.settings ? ctx.settings.todo : undefined,
    width: ctx && ctx.settings ? ctx.settings.width : undefined,
    height: ctx && ctx.settings ? ctx.settings.height : undefined,
  };
  if (ctx && typeof ctx.callServer === "function") {
    return ctx.callServer(RPC_METHOD, params);
  }
  if (ctx && typeof ctx.rpc === "function") {
    return ctx.rpc(RPC_METHOD, params);
  }
  if (ctx && ctx.client && typeof ctx.client.call === "function") {
    return ctx.client.call(RPC_METHOD, params);
  }
  throw new Error("no server-RPC channel on Paseo client context");
}

// `paseo plugin scaffold` shape: the client module exports activate(ctx).
// ctx provides addSidebarItem, addWorkspacePanel, addTheme (unused here),
// and the plugin settings. Everything is read-only.
function activate(ctx) {
  const els = {};
  let timer = null;
  let stopped = false;

  async function refresh() {
    if (stopped) {
      return;
    }
    try {
      const res = await fetchSidebar(ctx);
      if (!res || !res.ok) {
        const msg = (res && res.error) || "sidebar RPC failed";
        if (els.panel) {
          els.panel.innerHTML = '<p class="tk-dim">TaskerKeeper: ' + esc(msg) + "</p>";
        }
        if (els.badge) {
          els.badge.textContent = "";
        }
        return;
      }
      const payload = res.payload || {};
      const count = ((payload.current || {}).tasks || []).length;
      if (els.badge) {
        els.badge.textContent = count ? String(count) : "";
      }
      if (els.panel) {
        els.panel.innerHTML = renderPayload(payload);
      }
    } catch (err) {
      if (els.panel) {
        els.panel.innerHTML =
          '<p class="tk-dim">TaskerKeeper: ' + esc((err && err.message) || err) + "</p>";
      }
    }
  }

  if (ctx && typeof ctx.addSidebarItem === "function") {
    const item = ctx.addSidebarItem({
      id: "taskerkeeper",
      title: "TaskerKeeper",
      badge: () => (els.badge ? els.badge.textContent : ""),
      onClick: () => {
        if (ctx && typeof ctx.openWorkspacePanel === "function") {
          ctx.openWorkspacePanel("taskerkeeper");
        }
      },
    });
    // Hosts differ: some return the node, some fill it in. Keep our own
    // badge span so the count works either way.
    els.badge = item && item.badgeEl ? item.badgeEl : null;
  }

  if (ctx && typeof ctx.addWorkspacePanel === "function") {
    ctx.addWorkspacePanel({
      id: "taskerkeeper",
      title: "TaskerKeeper",
      render: (root) => {
        const div = root.ownerDocument.createElement("div");
        div.className = "tk-panel";
        div.innerHTML = '<p class="tk-dim">Loading TaskerKeeper…</p>';
        root.appendChild(div);
        els.panel = div;
        refresh();
        if (timer) {
          clearInterval(timer);
        }
        timer = setInterval(refresh, POLL_MS);
        return () => {
          stopped = true;
          if (timer) {
            clearInterval(timer);
            timer = null;
          }
        };
      },
    });
  } else {
    // No panel slot on this host: still poll so the sidebar badge stays live.
    refresh();
    timer = setInterval(refresh, POLL_MS);
  }

  return () => {
    stopped = true;
    if (timer) {
      clearInterval(timer);
      timer = null;
    }
  };
}

module.exports = { activate, renderPayload, POLL_MS };

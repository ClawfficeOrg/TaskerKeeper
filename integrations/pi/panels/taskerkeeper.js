"use strict";

/**
 * Render the shared TaskerKeeper sidebar payload as fixed-width text lines.
 *
 * The payload comes from `taskerkeeper sidebar <todo> --json` (built by
 * taskerkeeper/sidebar.py). This module formats, never decides: no ready /
 * blocked / disjoint computation lives here. Caps and more-counts arrive
 * pre-computed in the payload; we only truncate lines to the sidebar width.
 */

function truncate(text, width) {
  const s = String(text == null ? "" : text).replace(/\n/g, " ");
  if (width < 2 || s.length <= width) return s;
  return s.slice(0, Math.max(0, width - 1)) + "…";
}

function unavailableLine() {
  return "TaskerKeeper: unavailable (taskerkeeper CLI not found)";
}

function isPayload(payload) {
  return (
    payload &&
    typeof payload === "object" &&
    payload.current &&
    payload.concurrent &&
    payload.upcoming &&
    payload.phase &&
    payload.overall
  );
}

function formatTask(t, width) {
  const icon = t.icon || "?";
  const id = t.id || "?";
  const title = t.title || "";
  let extra = "";
  if (t.owner) {
    extra += " [" + t.owner + (t.age ? " " + t.age : "") + "]";
  }
  if (Array.isArray(t.blocked_by) && t.blocked_by.length > 0) {
    extra += " [waits: " + t.blocked_by.join(", ") + "]";
  }
  const out = [truncate(icon + " " + id + " — " + title + extra, Math.max(2, width - 2))];
  if (t.goal) {
    out.push(truncate("  " + t.goal, Math.max(2, width - 2)));
  }
  if (Array.isArray(t.touches) && t.touches.length > 0) {
    out.push(truncate("  touches: " + t.touches.join(", "), Math.max(2, width - 2)));
  }
  return out;
}

function pushSection(lines, title, rows, more, width) {
  lines.push(title);
  lines.push("─".repeat(Math.min(width, 50)));
  if (!rows || rows.length === 0) {
    lines.push("  (none)");
  } else {
    for (const r of rows) lines.push("  " + r);
  }
  if (more) lines.push("  …" + more + " more");
}

/**
 * Render payload to an array of lines fitting width.
 * Fail-soft: any bad payload becomes a single unavailable line.
 */
function renderPanel(payload, width) {
  const w = Math.max(20, Number(width) || 45);
  if (!isPayload(payload)) return [unavailableLine()];
  try {
    const lines = ["TaskerKeeper", "═".repeat(Math.min(w, 50))];

    const cur = payload.current;
    const curRows = [];
    for (const t of cur.tasks || []) {
      for (const l of formatTask(t, w)) curRows.push(l);
    }
    pushSection(lines, "Current", curRows, cur.more || 0, w);

    const con = payload.concurrent;
    const conRows = [];
    for (const t of con.tasks || []) {
      for (const l of formatTask(t, w)) conRows.push(l);
    }
    if (con.deferred) conRows.push("+" + con.deferred + " overlapping");
    pushSection(lines, "Concurrent (safe to fan out)", conRows, con.more || 0, w);

    const up = payload.upcoming;
    const upRows = [];
    for (const t of up.tasks || []) {
      for (const l of formatTask(t, w)) upRows.push(l);
    }
    pushSection(lines, "Upcoming", upRows, up.more || 0, w);

    const ph = payload.phase;
    pushSection(
      lines,
      "Phase " + (ph.id || "?") + ": " + (ph.title || ""),
      (ph.tree || []).map((l) => truncate(l, Math.max(2, w - 2))),
      ph.more || 0,
      w
    );

    const ov = payload.overall;
    pushSection(
      lines,
      "Overall",
      (ov.tree || []).map((l) => truncate(l, Math.max(2, w - 2))),
      ov.more || 0,
      w
    );

    return lines;
  } catch (_err) {
    return [unavailableLine()];
  }
}

function renderText(payload, width) {
  return renderPanel(payload, width).join("\n") + "\n";
}

module.exports = {
  renderPanel,
  renderText,
  truncate,
  unavailableLine,
};

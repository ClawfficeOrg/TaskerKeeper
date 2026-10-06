# TaskerKeeper panel for OpenChamber

Read-only rail panel for [OpenChamber](https://docs.openchamber.dev/sdk/): Current,
Concurrent, Upcoming, phase tree and overall tree, read live from the
TaskerKeeper serve API. Click any task to attach it to the chat via
`host.attach`. No build step: `panel/main.js` is a hand-written,
dependency-free IIFE (classic `<script>`, no ES modules) because the panel
loads in a sandboxed iframe.

## Install

Settings -> Extensions -> Add, then one of:

- folder: paste the absolute path of `integrations/openchamber` (runs from the
  folder; edit, rebuild, reload),
- git URL: link to this repo (picks up updates when `version` in
  `package.json` is raised; pin `#tag`/`#branch` to follow one),
- local `.zip` of this folder.

Approve the dialog. The TaskerKeeper icon appears on the rail; the full board
opens from Extension pages above the session list (`contributes.page: true`);
the `+` menu next to the chat box offers task attach (`contributes.attach:
"dialog"`).

## Configure

1. Serve TaskerKeeper (the panel cannot spawn processes; it only speaks HTTP):
   `taskerkeeper serve --port 8471` with `TK_API_TOKEN` set in its environment.
2. Settings -> Integrations -> TaskerKeeper: paste the same token, then set
   `slug` to the project slug from `deploy/registry.json` (default
   `taskerkeeper`) and optionally `pollSeconds` (default 15, minimum 5).
3. The manifest `token.apiOrigin` is `http://127.0.0.1:8471`. Serving on
   another host/port (or behind a reverse proxy) means editing `apiOrigin` to
   match: `host.request` only calls that origin and the token never reaches
   the panel page.

## Capabilities approval note

This extension declares **no capabilities**, and that is on purpose. Drawing
the panel, reading `ctx.settings`, `host.attach`, `host.toast` and `host.close`
need no permission; `network` is added automatically for the declared
integration. It never lists/starts sessions, sends prompts, generates text or
touches files, so `prompt`, `sessions`, `files`, `model` are all omitted.
Read-only end to end: every write still goes through the CLI or serve API by a
human/agent outside the panel.

## Serve API mapping

| Panel section | Serve call | Shape used |
|---|---|---|
| Current | `GET /api/<slug>/ready?disjoint=1` | `in_progress[]` (id, title, status, claimed_by) |
| Concurrent | same | `ready[]` (already the disjoint safe-to-fan-out set) |
| Upcoming | same | `deferred[]` (task + `conflicts_with`) plus `blocked[]` (id, title, `blocked_by`) |
| Phase tree | `GET /api/<slug>/list` | `phases[]`; anchor = first phase holding a current/upcoming id, else first incomplete, else first (mirrors `sidebar_payload`) |
| Overall | same | `phases[]` done/total per phase + `complete` flag |

Task click calls `host.attach({ providerId: "taskerkeeper", id, title, text })`
where `text` is the goal plus status (16k-char cap respected). In the attach
dialog surface the window closes after attach. The badge shows
ready + in-progress count; opening the panel clears it.

## Bundle note

No bundling needed as shipped (zero imports). If `@openchamber/sdk` imports
(such as `@openchamber/sdk/ui` kit controls) are ever added, bundle to a
single classic file and ship it:

```sh
bunx openchamber-guest-bundle panel/main.js panel/main.js
# without Bun: esbuild panel/main.js --bundle --format=iife --platform=browser --outfile=panel/main.js
```

Commit the built `panel/main.js`; OpenChamber never installs npm dependencies
or builds extensions. Then bump `package.json` version so git installs offer
the update.

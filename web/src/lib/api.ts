// web/src/lib/api.ts — fleet client for the TaskerKeeper core API.
//
// Shapes mirror taskerkeeper/serve.py handle_get payloads:
//   GET /api/projects      -> { projects: [{ slug, name, repo_url, todo_path, ... }] }
//   GET /api/<slug>/list   -> cli.list_payload: { phases, summary }
//   GET /api/stream        -> SSE frames, one JSON event per `data:` line
//                            with `?slugs=a,b&since=SEQ` replay params.
//
// BetterAuth OIDC note (Authentik is the IdP only):
//   Browser login must NOT point at the core API directly — /api takes the
//   TK_API_TOKEN bearer (serve.py check_bearer), which EventSource/fetch from
//   a browser cannot attach. The wiring that lands with the auth step is:
//     1. better-auth (see dependency in web/package.json) configured with a
//        generic-OAuth/OIDC provider whose `issuer` is AUTHENTIK_ISSUER
//        (compose passes it as env; never hardcode the URL or any secret here).
//     2. SvelteKit server load (+page.server.ts / hooks.server.ts) holds the
//        BetterAuth session and proxies core reads server-side, attaching the
//        bearer from server env. This client module then keeps calling the
//        same-origin /api/* paths and needs no token of its own.
//   Until that proxy lands, local dev can point API_BASE at the core port
//   (http://127.0.0.1:8471) and pass the dev bearer explicitly.

export interface ProjectEntry {
  slug: string;
  name?: string;
  repo_url?: string;
  todo_path?: string;
  default_branch?: string;
}

export interface PhaseTask {
  id: string;
  title: string;
  status: string;
}

export interface PhaseSummary {
  id: string;
  title: string;
  complete: boolean;
  tasks: PhaseTask[];
}

export interface ListPayload {
  phases: PhaseSummary[];
  summary: Record<string, number>;
}

export interface StreamEvent {
  slug?: string;
  event?: string;
  task?: string;
  phase_complete?: boolean;
  [key: string]: unknown;
}

// Same-origin by default: dashboard served at tk.example.com, API under
// Traefik PathPrefix(`/api`) on the same host (deploy/compose.yml).
// Override for local dev, e.g. "http://127.0.0.1:8471".
export const API_BASE: string =
  (typeof import.meta !== 'undefined' &&
    (import.meta as unknown as { env?: Record<string, string> }).env?.['VITE_API_BASE']) ||
  '';

export interface FetchOptions {
  /** Dev-only bearer; prod attaches it in the server-side proxy (see note above). */
  token?: string;
}

function headers(token?: string): Record<string, string> {
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function getJson<T>(path: string, opts: FetchOptions = {}): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, { headers: headers(opts.token) });
  if (!res.ok) {
    throw new Error(`GET ${path}: ${res.status}`);
  }
  return (await res.json()) as T;
}

/** Fleet registry: every slug the core API serves. */
export function fetchProjects(opts: FetchOptions = {}): Promise<ProjectEntry[]> {
  return getJson<{ projects: ProjectEntry[] }>('/api/projects', opts).then(
    (d) => d.projects ?? []
  );
}

/** Per-project phase/task snapshot (counts, bars, badges all derive from this). */
export function fetchProjectList(slug: string, opts: FetchOptions = {}): Promise<ListPayload> {
  return getJson<ListPayload>(`/api/${encodeURIComponent(slug)}/list`, opts);
}

export interface StreamOptions extends FetchOptions {
  since?: number;
}

/**
 * Live updates via GET /api/stream (serve.py SSE: replay `since`, then
 * 1s poll frames plus `: heartbeat` comments; Traefik buffering off).
 * Returns the EventSource so the caller can close() it on destroy.
 */
export function subscribeStream(
  slugs: string[],
  onEvent: (event: StreamEvent) => void,
  opts: StreamOptions = {}
): EventSource {
  const params = new URLSearchParams();
  if (slugs.length > 0) params.set('slugs', slugs.join(','));
  if (opts.since) params.set('since', String(opts.since));
  const query = params.toString();
  const url = `${API_BASE}/api/stream${query ? `?${query}` : ''}`;
  // NOTE: EventSource cannot send Authorization headers; authed streaming
  // goes through the server-side proxy (see module note). The `token`
  // option is accepted for API symmetry and ignored here until then.
  void opts.token;
  const source = new EventSource(url);
  source.onmessage = (msg: MessageEvent) => {
    try {
      onEvent(JSON.parse(msg.data) as StreamEvent);
    } catch {
      // Ignore malformed frames; the next poll replay covers gaps.
    }
  };
  return source;
}

export interface AgentSession {
  agent_id: string;
  slug?: string | null;
  task_id?: string | null;
  repo?: string | null;
  branch?: string | null;
  worktree?: string | null;
  host?: string | null;
  model?: string | null;
  detail?: string | null;
  last_seen: string;
  lease_expires_at?: string | null;
  stale?: boolean;
}

export interface ReadyTask {
  id: string;
  title: string;
  status: string;
  goal?: string;
  touches?: string[];
  success?: string[];
  tests?: string;
  prerequisites?: string[];
  parallel_group?: string;
  complexity?: string;
  agent?: string;
  claimed_by?: string;
  claimed_at?: string;
  lease_expires_at?: string;
  lease_expired?: boolean;
  provider?: string;
  model?: string;
}

export interface DeferredEntry {
  id: string;
  title: string;
  conflicts_with: string[];
}

export interface BlockedEntry {
  id: string;
  title: string;
  blocked_by: string[];
}

export interface ReadyPayload {
  ready: ReadyTask[];
  disjoint: boolean;
  deferred: DeferredEntry[];
  conflicts: unknown;
  in_progress: ReadyTask[];
  blocked: BlockedEntry[];
}

export interface HistoryEvent {
  at?: string;
  event?: string;
  task?: string;
  owner?: string;
  from?: string;
  to?: string;
  [key: string]: unknown;
}

/** Presence for one slug: GET /api/<slug>/sessions (sessions.py list_sessions). */
export function fetchSessions(slug: string, opts: FetchOptions = {}): Promise<AgentSession[]> {
  return getJson<{ sessions: AgentSession[] }>(
    `/api/${encodeURIComponent(slug)}/sessions`,
    opts
  ).then((d) => d.sessions ?? []);
}

/** Ready + disjoint split + blockers: GET /api/<slug>/ready?disjoint=1. */
export function fetchReady(
  slug: string,
  disjoint = true,
  opts: FetchOptions = {}
): Promise<ReadyPayload> {
  const query = disjoint ? '?disjoint=1' : '';
  return getJson<ReadyPayload>(`/api/${encodeURIComponent(slug)}/ready${query}`, opts);
}

/** Event tail: GET /api/<slug>/history?limit=N, oldest first. */
export function fetchHistory(
  slug: string,
  limit = 50,
  opts: FetchOptions = {}
): Promise<HistoryEvent[]> {
  return getJson<{ events: HistoryEvent[] }>(
    `/api/${encodeURIComponent(slug)}/history?limit=${limit}`,
    opts
  ).then((d) => d.events ?? []);
}

/** "3m ago" for last_seen / claimed_at ...Z stamps. Empty when unparseable. */
export function timeAgo(ts: string | null | undefined, nowMs?: number): string {
  if (!ts) return '—';
  const ms = Date.parse(ts);
  if (Number.isNaN(ms)) return '—';
  const delta = Math.max(0, (nowMs ?? Date.now()) - ms);
  const secs = Math.floor(delta / 1000);
  if (secs < 60) return `${secs}s ago`;
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 48) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

/**
 * Shared EventSource hub over subscribeStream: one connection per slug set,
 * fanned out to every subscriber. Pages subscribe on mount and unsubscribe
 * on destroy; the socket closes when the last subscriber leaves.
 * (Single-page-at-a-time assumption: a new slug set reconnects the hub.)
 */
export type StreamHandler = (event: StreamEvent) => void;

let sharedSource: EventSource | null = null;
let sharedKey = '';
const sharedHandlers = new Set<StreamHandler>();

function ensureSharedStream(slugs: string[]): void {
  const key = [...slugs].sort().join(',');
  if (sharedSource && key === sharedKey) return;
  try {
    sharedSource?.close();
  } catch {
    // Ignore close errors; a fresh connection replaces it below.
  }
  sharedKey = key;
  sharedSource = subscribeStream(slugs, (event) => {
    for (const handler of [...sharedHandlers]) {
      try {
        handler(event);
      } catch {
        // One bad subscriber must not break fanout to the rest.
      }
    }
  });
}

export function subscribeSharedStream(slugs: string[], onEvent: StreamHandler): () => void {
  sharedHandlers.add(onEvent);
  ensureSharedStream(slugs);
  return () => {
    sharedHandlers.delete(onEvent);
    if (sharedHandlers.size === 0) {
      try {
        sharedSource?.close();
      } catch {
        // Ignore close errors on last-unsubscribe teardown.
      }
      sharedSource = null;
      sharedKey = '';
    }
  };
}

export interface PhaseStats {
  total: number;
  done: number;
  pending: number;
  inProgress: number;
  pct: number;
}

/** Done/total bar inputs for one phase. Terminal states (done/cancelled/moved) count as finished. */
export function phaseStats(phase: PhaseSummary): PhaseStats {
  const total = phase.tasks.length;
  const done = phase.tasks.filter((t) => t.status !== 'pending' && t.status !== 'in_progress').length;
  const pending = phase.tasks.filter((t) => t.status === 'pending').length;
  const inProgress = phase.tasks.filter((t) => t.status === 'in_progress').length;
  return { total, done, pending, inProgress, pct: total === 0 ? 100 : Math.round((done / total) * 100) };
}

export interface ProjectCounts {
  pending: number;
  inProgress: number;
  done: number;
}

/** Fleet-table counts, summed across phases. */
export function projectCounts(list: ListPayload): ProjectCounts {
  let pending = 0;
  let inProgress = 0;
  let done = 0;
  for (const phase of list.phases) {
    for (const task of phase.tasks) {
      if (task.status === 'pending') pending += 1;
      else if (task.status === 'in_progress') inProgress += 1;
      else if (task.status === 'done') done += 1;
    }
  }
  return { pending, inProgress, done };
}

/**
 * release_ready badge proxy: true when no task is still open (pending or
 * in_progress). Authority is serve.py release_state().tag_on_complete, which
 * /api/<slug>/list does not expose yet — once the core adds a release field
 * to the list payload, read it here instead of deriving completion locally.
 */
export function isReleaseReady(list: ListPayload): boolean {
  return list.phases.length > 0 && list.phases.every((p) => p.complete);
}

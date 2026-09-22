<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import {
    fetchProjects,
    fetchSessions,
    fetchReady,
    subscribeSharedStream,
    timeAgo
  } from '$lib/api';
  import type { AgentSession, ReadyTask, StreamEvent } from '$lib/api';

  interface AgentRow {
    session: AgentSession;
    task: ReadyTask | null;
  }

  let rows: AgentRow[] = [];
  let slugs: string[] = [];
  let error: string | null = null;
  let loading = true;
  let live = false;
  let unsubscribe: (() => void) | null = null;
  // Tick for claim-age / last-seen labels; interval cleared on destroy.
  let clock = 0;
  let timer: ReturnType<typeof setInterval> | null = null;

  function taskById(index: Map<string, ReadyTask>, id: string | null | undefined): ReadyTask | null {
    if (!id) return null;
    return index.get(id) ?? null;
  }

  async function loadSlug(slug: string): Promise<{ sessions: AgentSession[]; tasks: Map<string, ReadyTask> }> {
    const [sessions, ready] = await Promise.all([fetchSessions(slug), fetchReady(slug, false)]);
    const tasks = new Map<string, ReadyTask>();
    for (const t of [...ready.in_progress, ...ready.ready]) tasks.set(t.id, t);
    return { sessions, tasks };
  }

  async function loadAll(): Promise<void> {
    const projects = await fetchProjects();
    slugs = projects.map((p) => p.slug);
    const perSlug = await Promise.all(slugs.map((s) => loadSlug(s)));
    const next: AgentRow[] = [];
    for (const { sessions, tasks } of perSlug) {
      for (const session of sessions) {
        next.push({ session, task: taskById(tasks, session.task_id) });
      }
    }
    next.sort((a, b) => a.session.agent_id.localeCompare(b.session.agent_id));
    rows = next;
  }

  async function refreshSlug(slug: string): Promise<void> {
    try {
      const { sessions, tasks } = await loadSlug(slug);
      const fresh = sessions.map((session) => ({
        session,
        task: taskById(tasks, session.task_id)
      }));
      const keep = rows.filter((r) => (r.session.slug ?? '') !== slug);
      rows = [...keep, ...fresh].sort((a, b) =>
        a.session.agent_id.localeCompare(b.session.agent_id)
      );
    } catch {
      // Keep stale rows; next heartbeat/poll retries via replay.
    }
  }

  function onStreamEvent(event: StreamEvent): void {
    live = true;
    const slug = typeof event.slug === 'string' ? event.slug : null;
    if (slug && slugs.includes(slug)) void refreshSlug(slug);
  }

  function repoBranch(session: AgentSession): string {
    const repo = (session.repo ?? '').trim();
    const branch = (session.branch ?? '').trim();
    if (repo && branch) return `${repo}@${branch}`;
    return repo || branch || '—';
  }

  onMount(async () => {
    timer = setInterval(() => {
      clock += 1;
    }, 30000);
    try {
      await loadAll();
      if (slugs.length > 0) unsubscribe = subscribeSharedStream(slugs, onStreamEvent);
    } catch (e) {
      error = e instanceof Error ? e.message : String(e);
    } finally {
      loading = false;
    }
  });

  onDestroy(() => {
    unsubscribe?.();
    unsubscribe = null;
    if (timer) clearInterval(timer);
    timer = null;
  });
</script>

<main>
  <header>
    <h1>Agents</h1>
    <nav>
      <a href="/">Fleet</a>
      <span class="muted"> · Agents</span>
    </nav>
    <div class="auth">
      {#if live}
        <span class="live" title="Receiving /api/stream events">● live</span>
      {:else}
        <span class="stale" title="No /api/stream events yet">○ polling</span>
      {/if}
      <!-- Reactive tick: re-renders claim-age / last-seen labels every 30s. -->
      <span class="tick" aria-hidden="true">{clock >= 0 ? '' : ''}</span>
    </div>
  </header>

  {#if loading}
    <p>Loading agents…</p>
  {:else if error}
    <p class="error">Failed to load agents: {error}</p>
  {:else if rows.length === 0}
    <p>No heartbeats yet. Agents appear here after their first POST heartbeat.</p>
  {:else}
    <table>
      <thead>
        <tr>
          <th>Agent</th>
          <th>Slug / task</th>
          <th>repo@branch</th>
          <th>Claim age</th>
          <th>Touches</th>
          <th>Last seen</th>
        </tr>
      </thead>
      <tbody>
        {#each rows as row (row.session.agent_id)}
          {@const stale = row.session.stale === true}
          <tr class:grey={stale} title={stale ? 'Stale: no heartbeat for 180s+ (sessions.py STALE_AFTER_SECONDS)' : ''}>
            <td>
              <strong>{row.session.agent_id}</strong>
              {#if row.session.host}
                <span class="muted"> · {row.session.host}</span>
              {/if}
              {#if stale}
                <span class="badge-stale">stale</span>
              {/if}
            </td>
            <td>
              {#if row.session.slug}
                <a href="/p/{encodeURIComponent(row.session.slug)}">{row.session.slug}</a>
              {:else}
                <span class="muted">—</span>
              {/if}
              {#if row.session.task_id}
                <span class="muted"> · {row.session.task_id}</span>
              {/if}
            </td>
            <td>{repoBranch(row.session)}</td>
            <td>
              {#if row.task?.claimed_at}
                <span title="claimed_at {row.task.claimed_at}; lease to {row.task.lease_expires_at ?? '—'}">
                  {timeAgo(row.task.claimed_at)}
                </span>
              {:else}
                <span class="muted">—</span>
              {/if}
            </td>
            <td>
              {#if row.task?.touches?.length}
                <span title={row.task.touches.join('\n')}>{row.task.touches.join(', ')}</span>
              {:else}
                <span class="muted">—</span>
              {/if}
            </td>
            <td title={row.session.last_seen}>{timeAgo(row.session.last_seen)}</td>
          </tr>
        {/each}
      </tbody>
    </table>
  {/if}
</main>

<style>
  main {
    max-width: 1080px;
    margin: 2rem auto;
    padding: 0 1rem;
    font-family: system-ui, sans-serif;
  }
  header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 1rem;
  }
  nav a {
    color: #1a73e8;
  }
  table {
    width: 100%;
    border-collapse: collapse;
    margin-top: 1rem;
  }
  th,
  td {
    text-align: left;
    padding: 0.5rem;
    border-bottom: 1px solid #ddd;
    vertical-align: top;
  }
  .muted {
    color: #777;
  }
  .error {
    color: #a00;
  }
  .live {
    color: #0a0;
  }
  .stale {
    color: #999;
  }
  tr.grey {
    color: #999;
    background: #f7f7f7;
  }
  tr.grey a {
    color: #999;
  }
  .tick {
    display: none;
  }
  .badge-stale {
    background: #eee;
    color: #666;
    border-radius: 4px;
    padding: 0.1rem 0.4rem;
    font-size: 0.8rem;
    margin-left: 0.5rem;
    white-space: nowrap;
  }
</style>

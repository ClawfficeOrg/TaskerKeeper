<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import { page } from '$app/stores';
  import {
    fetchProjectList,
    fetchReady,
    fetchHistory,
    subscribeSharedStream,
    phaseStats,
    timeAgo
  } from '$lib/api';
  import type { ListPayload, ReadyPayload, HistoryEvent, StreamEvent } from '$lib/api';

  $: slug = $page.params.slug ?? '';

  let list: ListPayload | null = null;
  let ready: ReadyPayload | null = null;
  let events: HistoryEvent[] = [];
  let error: string | null = null;
  let loading = true;
  let live = false;
  let unsubscribe: (() => void) | null = null;

  async function loadAll(target: string): Promise<void> {
    const [projectList, readyPayload, history] = await Promise.all([
      fetchProjectList(target),
      fetchReady(target, true),
      fetchHistory(target, 50)
    ]);
    list = projectList;
    ready = readyPayload;
    events = history;
  }

  async function refresh(target: string): Promise<void> {
    try {
      const [projectList, readyPayload] = await Promise.all([
        fetchProjectList(target),
        fetchReady(target, true)
      ]);
      list = projectList;
      ready = readyPayload;
    } catch {
      // Keep stale view; next heartbeat/poll retries via replay.
    }
  }

  function onStreamEvent(event: StreamEvent): void {
    if (typeof event.slug === 'string' && event.slug !== slug) return;
    live = true;
    if (typeof event.slug === 'string') {
      // Append the live frame to the tail (cap 50); full history reloads on remount.
      events = [...events, event as HistoryEvent].slice(-50);
      void refresh(slug);
    }
  }

  function statusClass(status: string): string {
    if (status === 'done') return 'done';
    if (status === 'in_progress') return 'active';
    return '';
  }

  $: blocked = ready?.blocked ?? [];
  $: deferred = ready?.deferred ?? [];
  $: tail = [...events].reverse().slice(0, 20);

  onMount(async () => {
    try {
      await loadAll(slug);
      unsubscribe = subscribeSharedStream([slug], onStreamEvent);
    } catch (e) {
      error = e instanceof Error ? e.message : String(e);
    } finally {
      loading = false;
    }
  });

  onDestroy(() => {
    unsubscribe?.();
    unsubscribe = null;
  });
</script>

<main>
  <header>
    <div>
      <h1>Project {slug}</h1>
      <nav>
        <a href="/">Fleet</a>
        <span class="muted"> · </span>
        <a href="/agents">Agents</a>
      </nav>
    </div>
    <div class="auth">
      {#if live}
        <span class="live" title="Receiving /api/stream events">● live</span>
      {:else}
        <span class="stale" title="No /api/stream events yet">○ polling</span>
      {/if}
    </div>
  </header>

  {#if loading}
    <p>Loading {slug}…</p>
  {:else if error}
    <p class="error">Failed to load {slug}: {error}</p>
  {:else}
    <section>
      <h2>Phases &amp; tasks</h2>
      {#if list && list.phases.length > 0}
        {#each list.phases as phase (phase.id)}
          {@const stats = phaseStats(phase)}
          <div class="phase">
            <h3>
              {phase.id} — {phase.title}
              <span class="muted"> · {stats.done}/{stats.total} ({stats.pct}%)</span>
              {#if phase.complete}
                <span class="badge">✓ complete</span>
              {/if}
            </h3>
            <table>
              <tbody>
                {#each phase.tasks as task (task.id)}
                  <tr>
                    <td class="id">{task.id}</td>
                    <td>{task.title}</td>
                    <td><span class="status {statusClass(task.status)}">{task.status}</span></td>
                  </tr>
                {/each}
              </tbody>
            </table>
          </div>
        {/each}
      {:else}
        <p class="muted">No phases in list payload.</p>
      {/if}
    </section>

    <section>
      <h2>Blockers</h2>
      {#if blocked.length === 0}
        <p class="muted">Nothing blocked — every pending task is runnable or done.</p>
      {:else}
        <ul>
          {#each blocked as item (item.id)}
            <li><strong>{item.id}</strong> — {item.title} <span class="muted">[waiting on: {item.blocked_by.join(', ')}]</span></li>
          {/each}
        </ul>
      {/if}
    </section>

    <section>
      <h2>Deferred — disjoint collisions</h2>
      {#if deferred.length === 0}
        <p class="muted">No deferred tasks: disjoint selection matches ready.</p>
      {:else}
        <ul>
          {#each deferred as item (item.id)}
            <li>
              <strong>{item.id}</strong> — {item.title}
              <span class="muted">[overlaps: {item.conflicts_with.join(', ')}]</span>
            </li>
          {/each}
        </ul>
        <p class="muted">Source: GET /api/{slug}/ready?disjoint=1 — greedy ID-order split; in-progress tasks hold their paths.</p>
      {/if}
    </section>

    <section>
      <h2>Event tail</h2>
      {#if tail.length === 0}
        <p class="muted">No events yet for this project.</p>
      {:else}
        <table>
          <thead>
            <tr>
              <th>At</th>
              <th>Event</th>
              <th>Task</th>
              <th>Owner</th>
            </tr>
          </thead>
          <tbody>
            {#each tail as event ((event.at ?? '') + (event.event ?? '') + (event.task ?? ''))}
              <tr>
                <td title={event.at ?? ''}>{timeAgo(event.at)}</td>
                <td>{event.event ?? '—'}</td>
                <td>{event.task ?? '—'}</td>
                <td>{event.owner ?? '—'}</td>
              </tr>
            {/each}
          </tbody>
        </table>
        <p class="muted">Live tail: shared EventSource store (api.ts subscribeSharedStream) over GET /api/stream?slugs={slug}.</p>
      {/if}
    </section>
  {/if}
</main>

<style>
  main {
    max-width: 960px;
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
  section {
    margin-top: 2rem;
  }
  table {
    width: 100%;
    border-collapse: collapse;
    margin-top: 0.5rem;
  }
  th,
  td {
    text-align: left;
    padding: 0.4rem 0.5rem;
    border-bottom: 1px solid #ddd;
    vertical-align: top;
  }
  .id {
    font-variant-numeric: tabular-nums;
    white-space: nowrap;
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
  .badge {
    background: #e6f4ea;
    color: #137333;
    border-radius: 4px;
    padding: 0.15rem 0.5rem;
    font-size: 0.85rem;
    white-space: nowrap;
  }
  .status {
    font-size: 0.85rem;
    padding: 0.1rem 0.4rem;
    border-radius: 4px;
    background: #eee;
    white-space: nowrap;
  }
  .status.done {
    background: #e6f4ea;
    color: #137333;
  }
  .status.active {
    background: #e8f0fe;
    color: #1a73e8;
  }
  .phase h3 {
    margin-bottom: 0.25rem;
  }
</style>

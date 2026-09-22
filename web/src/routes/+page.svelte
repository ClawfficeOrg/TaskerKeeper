<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import {
    fetchProjects,
    fetchProjectList,
    subscribeStream,
    phaseStats,
    projectCounts,
    isReleaseReady
  } from '$lib/api';
  import type { ProjectEntry, ListPayload, StreamEvent } from '$lib/api';

  let projects: ProjectEntry[] = [];
  let lists: Record<string, ListPayload> = {};
  let error: string | null = null;
  let loading = true;
  let live = false;
  let stream: EventSource | null = null;

  async function loadFleet(): Promise<void> {
    projects = await fetchProjects();
    const entries = await Promise.all(
      projects.map(async (p) => [p.slug, await fetchProjectList(p.slug)] as const)
    );
    const next: Record<string, ListPayload> = {};
    for (const [slug, list] of entries) next[slug] = list;
    lists = next;
  }

  function onStreamEvent(event: StreamEvent): void {
    live = true;
    // Refresh the touched project so counts/bars/badges track the core.
    // Poll replay (`since`) covers anything missed between frames.
    const slug = typeof event.slug === 'string' ? event.slug : null;
    if (slug && lists[slug]) {
      fetchProjectList(slug)
        .then((list) => {
          lists = { ...lists, [slug]: list };
        })
        .catch(() => {
          // Keep stale row; next heartbeat/poll retries via replay.
        });
    }
  }

  onMount(async () => {
    try {
      await loadFleet();
      const slugs = projects.map((p) => p.slug);
      stream = subscribeStream(slugs, onStreamEvent);
    } catch (e) {
      error = e instanceof Error ? e.message : String(e);
    } finally {
      loading = false;
    }
  });

  onDestroy(() => {
    stream?.close();
    stream = null;
  });
</script>

<main>
  <header>
    <h1>TaskerKeeper Fleet</h1>
    <div class="auth">
      <!-- BetterAuth OIDC: sign-in goes through Authentik (the OIDC IdP).
           Wiring lands with the auth step: better-auth generic-OAuth provider
           with issuer from AUTHENTIK_ISSUER env (compose), session checked in
           hooks.server.ts; no secrets ever live in this client bundle. -->
      <button disabled title="BetterAuth OIDC via AUTHENTIK_ISSUER — wiring lands with auth step">
        Sign in with Authentik
      </button>
      {#if live}
        <span class="live" title="Receiving /api/stream events">● live</span>
      {:else}
        <span class="stale" title="No /api/stream events yet">○ polling</span>
      {/if}
    </div>
  </header>

  {#if loading}
    <p>Loading fleet…</p>
  {:else if error}
    <p class="error">Failed to load fleet: {error}</p>
  {:else if projects.length === 0}
    <p>No projects in registry.</p>
  {:else}
    <table>
      <thead>
        <tr>
          <th>Slug</th>
          <th>Phases</th>
          <th>Pending</th>
          <th>In progress</th>
          <th>Done</th>
          <th>Release</th>
        </tr>
      </thead>
      <tbody>
        {#each projects as p (p.slug)}
          {@const list = lists[p.slug]}
          {@const counts = list ? projectCounts(list) : null}
          {@const ready = list ? isReleaseReady(list) : false}
          <tr>
            <td>
              <strong>{p.slug}</strong>
              {#if p.name && p.name !== p.slug}
                <span class="muted"> · {p.name}</span>
              {/if}
            </td>
            <td class="phases">
              {#if list}
                {#each list.phases as phase (phase.id)}
                  {@const stats = phaseStats(phase)}
                  <div class="phase" title="{phase.id} {phase.title}: {stats.done}/{stats.total} done">
                    <span class="phase-id">{phase.id}</span>
                    <span class="bar"><span class="fill" style="width: {stats.pct}%"></span></span>
                    <span class="muted">{stats.done}/{stats.total}</span>
                  </div>
                {/each}
              {:else}
                <span class="muted">—</span>
              {/if}
            </td>
            <td>{counts ? counts.pending : '—'}</td>
            <td>{counts ? counts.inProgress : '—'}</td>
            <td>{counts ? counts.done : '—'}</td>
            <td>
              {#if ready}
                <span class="badge" title="No open tasks; core release_state().tag_on_complete is authoritative (see api.ts)">
                  ✓ release_ready
                </span>
              {:else}
                <span class="muted">—</span>
              {/if}
            </td>
          </tr>
        {/each}
      </tbody>
    </table>
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
  .auth {
    display: flex;
    align-items: center;
    gap: 0.75rem;
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
  .badge {
    background: #e6f4ea;
    color: #137333;
    border-radius: 4px;
    padding: 0.15rem 0.5rem;
    font-size: 0.85rem;
    white-space: nowrap;
  }
  .phase {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    margin-bottom: 0.25rem;
  }
  .phase-id {
    min-width: 3rem;
    font-variant-numeric: tabular-nums;
  }
  .bar {
    flex: 1;
    min-width: 80px;
    height: 8px;
    background: #eee;
    border-radius: 4px;
    overflow: hidden;
  }
  .fill {
    display: block;
    height: 100%;
    background: #1a73e8;
  }
</style>

// The five `tidetable sidebar` sections plus the live-run banner, in React
// Native: this renders on desktop, web, iOS and Android, so no DOM.
import type { PluginTheme } from "@getpaseo/plugin";
import { ScrollView } from "@getpaseo/plugin/client/react-native";
import { useMemo, type ReactNode } from "react";
import { Text, View } from "react-native";
import type { SidebarPayload, SidebarTask } from "../shared/sidebar";
import { useSidebar } from "./data";

interface Props {
  theme: PluginTheme;
  compact: boolean;
  // A todo file to read locally; omitted = the one from the plugin settings.
  todo?: string;
}

export function makeStyles(theme: PluginTheme, compact: boolean) {
  return {
    root: { flex: 1, backgroundColor: theme.colors.surface0 },
    content: { padding: compact ? 12 : 20, gap: compact ? 12 : 16 },
    section: { gap: 4 },
    heading: { color: theme.colors.foreground, fontWeight: "600" as const, fontSize: 14 },
    row: { color: theme.colors.foreground, fontSize: 13 },
    dim: { color: theme.colors.foregroundMuted, fontSize: 12 },
    error: { color: theme.colors.statusDanger, fontSize: 13 },
    running: { color: theme.colors.statusSuccess, fontSize: 13 },
    task: { gap: 1, paddingVertical: 2 },
  };
}

export type Styles = ReturnType<typeof makeStyles>;

function Section({ title, more, styles, children }: { title: string; more?: number; styles: Styles; children: ReactNode }) {
  return (
    <View style={styles.section}>
      <Text style={styles.heading}>{title}</Text>
      {children}
      {more ? <Text style={styles.dim}>…{more} more</Text> : null}
    </View>
  );
}

function TaskRow({ task, styles }: { task: SidebarTask; styles: Styles }) {
  let extra = "";
  if (task.owner) extra += ` [${task.owner}${task.age ? " " + task.age : ""}]`;
  if (task.blocked_by?.length) extra += ` [waits: ${task.blocked_by.join(", ")}]`;
  return (
    <View style={styles.task}>
      <Text style={styles.row} selectable>
        {`${task.icon ?? ""} ${task.id} — ${task.title}${extra}`}
      </Text>
      {task.goal ? <Text style={styles.dim}>{task.goal}</Text> : null}
      {task.touches?.length ? <Text style={styles.dim}>touches: {task.touches.join(", ")}</Text> : null}
    </View>
  );
}

function TaskList({ tasks, styles }: { tasks: SidebarTask[]; styles: Styles }) {
  if (!tasks.length) return <Text style={styles.dim}>(none)</Text>;
  return (
    <>
      {tasks.map((t) => (
        <TaskRow key={t.id} task={t} styles={styles} />
      ))}
    </>
  );
}

function Lines({ lines, styles }: { lines: string[]; styles: Styles }) {
  if (!lines.length) return <Text style={styles.dim}>(none)</Text>;
  return (
    <>
      {lines.map((line, i) => (
        <Text key={i} style={styles.row} selectable>
          {line}
        </Text>
      ))}
    </>
  );
}

function RunBanner({ run, styles }: { run: NonNullable<SidebarPayload["run"]>; styles: Styles }) {
  const live = run.status === "running";
  const bits = live
    ? [`► running${run.date ? " " + run.date : ""}`, run.branch, run.deadline && `deadline ${run.deadline}`]
    : [`· ${run.status}`, run.date];
  return (
    <Section title="Run" styles={styles}>
      <Text style={live ? styles.running : styles.row}>{bits.filter(Boolean).join(" · ")}</Text>
      {run.last ? <Text style={styles.dim}>{run.last}</Text> : null}
    </Section>
  );
}

export function SidebarSections({ payload, styles }: { payload: SidebarPayload; styles: Styles }) {
  const { current, concurrent, upcoming, phase, overall, run } = payload;
  return (
    <>
      {run?.status ? <RunBanner run={run} styles={styles} /> : null}
      <Section title="Current" more={current.more} styles={styles}>
        <TaskList tasks={current.tasks} styles={styles} />
      </Section>
      <Section title="Concurrent (safe to fan out)" more={concurrent.more} styles={styles}>
        <TaskList tasks={concurrent.tasks} styles={styles} />
      </Section>
      <Section title="Upcoming" more={upcoming.more} styles={styles}>
        <TaskList tasks={upcoming.tasks} styles={styles} />
      </Section>
      <Section title={`Phase ${phase.id}: ${phase.title}`} more={phase.more} styles={styles}>
        <Lines lines={phase.tree} styles={styles} />
      </Section>
      <Section title="Overall" more={overall.more} styles={styles}>
        <Lines lines={overall.tree} styles={styles} />
      </Section>
    </>
  );
}

// Every failure renders inside the view; nothing throws into the host.
export function SidebarView({ theme, compact, todo }: Props) {
  const styles = useMemo(() => makeStyles(theme, compact), [theme, compact]);
  return (
    <ScrollView style={styles.root} contentContainerStyle={styles.content}>
      <SidebarBody styles={styles} todo={todo} />
    </ScrollView>
  );
}

export function SidebarBody({ styles, todo }: { styles: Styles; todo?: string }) {
  const query = useSidebar(todo);
  let body: ReactNode;
  if (query.isPending) {
    body = <Text style={styles.dim}>Loading TideTable…</Text>;
  } else if (query.isError) {
    body = <Text style={styles.error}>TideTable: {String(query.error?.message ?? query.error)}</Text>;
  } else if (!query.data.ok) {
    body = <Text style={styles.dim}>TideTable: {query.data.error}</Text>;
  } else {
    body = <SidebarSections payload={query.data.payload} styles={styles} />;
  }
  return <>{body}</>;
}

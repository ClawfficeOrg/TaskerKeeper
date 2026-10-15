// The right-sidebar (Explorer) panel: the TideTable todos of the open
// workspace's own directory. With several, a row of chips picks one.
import type { PluginWorkspacePanelProps } from "@getpaseo/plugin/client";
import { useWorkspace } from "@getpaseo/plugin/client";
import { ScrollView } from "@getpaseo/plugin/client/react-native";
import { useMemo, useState } from "react";
import { Pressable, Text, View } from "react-native";
import { useTodoFiles } from "./data";
import { makeStyles, SidebarBody } from "./sidebar-view";

export function WorkspaceTodoPanel({ theme, workspaceId }: PluginWorkspacePanelProps) {
  // The Explorer column is narrow whatever the window size: always compact.
  const styles = useMemo(() => makeStyles(theme, true), [theme]);
  const workspace = useWorkspace(workspaceId, (w) => ({ directory: w.directory }));
  const directory = workspace?.directory ?? null;
  const query = useTodoFiles(directory);
  const [picked, setPicked] = useState<string | null>(null);

  let body;
  if (!directory) {
    body = <Text style={styles.dim}>Workspace unavailable.</Text>;
  } else if (query.isPending) {
    body = <Text style={styles.dim}>Looking for TideTable todos…</Text>;
  } else if (query.isError) {
    body = <Text style={styles.error}>TideTable: {String(query.error?.message ?? query.error)}</Text>;
  } else if (!query.data.files.length) {
    body = (
      <Text style={styles.dim}>
        No TideTable todo files in this project{query.data.truncated ? " (scan stopped early: large tree)" : ""}.
      </Text>
    );
  } else {
    const files = query.data.files;
    // A remembered pick that has since disappeared falls back to the newest file.
    const current = files.find((f) => f.path === picked) ?? files[0];
    body = (
      <>
        {files.length > 1 ? (
          <View style={{ flexDirection: "row", flexWrap: "wrap", gap: 6 }}>
            {files.map((f) => {
              const active = f.path === current.path;
              return (
                <Pressable
                  key={f.path}
                  accessibilityRole="button"
                  accessibilityState={{ selected: active }}
                  accessibilityLabel={`Show ${f.relative}`}
                  onPress={() => setPicked(f.path)}
                  style={{
                    paddingHorizontal: 8,
                    paddingVertical: 4,
                    borderRadius: 6,
                    borderWidth: 1,
                    borderColor: theme.colors.border,
                    backgroundColor: active ? theme.colors.accent : theme.colors.surface1,
                  }}
                >
                  <Text style={{ fontSize: 12, color: active ? theme.colors.accentForeground : theme.colors.foreground }}>
                    {f.relative}
                  </Text>
                </Pressable>
              );
            })}
          </View>
        ) : null}
        <Text style={styles.heading}>{current.name}</Text>
        {files.length === 1 ? <Text style={styles.dim}>{current.relative}</Text> : null}
        <SidebarBody styles={styles} todo={current.path} />
      </>
    );
  }

  return (
    <ScrollView style={styles.root} contentContainerStyle={styles.content}>
      {body}
    </ScrollView>
  );
}

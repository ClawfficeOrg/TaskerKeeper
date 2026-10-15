import type {
  PluginClientContext,
  PluginScreenProps,
  PluginSidebarItemProps,
  PluginWorkspacePanelProps,
} from "@getpaseo/plugin/client";
import { SidebarRow } from "@getpaseo/plugin/client/ui";
import { Text } from "react-native";
import { useSidebar } from "./client/data";
import { SettingsScreen } from "./client/settings";
import { SidebarView } from "./client/sidebar-view";

// "tidetable" was the old addSidebarItem id; reusing it for the screen keeps
// saved /plugin/paseo-tidetable/tidetable links working.
const SCREEN = "tidetable";

function TideTableScreen({ theme, layout }: PluginScreenProps) {
  return <SidebarView theme={theme} compact={layout.compact} />;
}

function TideTablePanel({ theme, layout }: PluginWorkspacePanelProps) {
  return <SidebarView theme={theme} compact={layout.compact} />;
}

// Badge: in-progress count, prefixed ► while a TKRun is live. Blank on error.
function TideTableItem({ theme, currentScreen, openScreen }: PluginSidebarItemProps) {
  const { data } = useSidebar();
  let badge = "";
  if (data?.ok) {
    const count = data.payload.current.tasks.length;
    badge = (data.payload.run?.status === "running" ? "►" : "") + (count ? String(count) : "");
  }
  return (
    <SidebarRow
      icon="ListTodo"
      active={currentScreen?.screenId === SCREEN}
      onPress={() => openScreen({ screenId: SCREEN })}
      trailing={badge ? <Text style={{ color: theme.colors.foregroundMuted, fontSize: 12 }}>{badge}</Text> : undefined}
    />
  );
}

export default function contribute(client: PluginClientContext) {
  client.addScreen({ id: SCREEN, title: "TideTable", Component: TideTableScreen });
  client.addSidebarHeaderItem({ id: SCREEN, title: "TideTable", Component: TideTableItem });
  client.addWorkspacePanel({
    id: "tidetable",
    title: "TideTable",
    icon: "ListTodo",
    context: "workspace",
    Component: TideTablePanel,
  });
  client.addSettingsScreen({ id: "connection", title: "TideTable", icon: "ListTodo", Component: SettingsScreen });
  client.addCommandCenterItem({
    id: "open-tidetable",
    title: "Open TideTable",
    icon: "ListTodo",
    context: "global",
    onSelect({ openScreen }) {
      openScreen({ screenId: SCREEN });
    },
  });
  return () => {};
}

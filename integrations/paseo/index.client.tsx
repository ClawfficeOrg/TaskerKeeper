import type { PluginClientContext, PluginScreenProps } from "@getpaseo/plugin/client";
import { SettingsScreen } from "./client/settings";
import { SidebarView } from "./client/sidebar-view";
import { WorkspaceTodoPanel } from "./client/workspace-panel";

// "tidetable" was the old addSidebarItem id; reusing it for the screen keeps
// saved /plugin/paseo-tidetable/tidetable links working.
const SCREEN = "tidetable";
const PANEL = "tidetable";

// The todo (or hub project) named in the plugin settings, not tied to a workspace.
function TideTableScreen({ theme, layout }: PluginScreenProps) {
  return <SidebarView theme={theme} compact={layout.compact} />;
}

export default function contribute(client: PluginClientContext) {
  // Explorer is the right sidebar; the panel follows the open workspace and
  // reads the todo files in its directory.
  client.addWorkspacePanel({
    id: PANEL,
    title: "TideTable",
    icon: "ListTodo",
    context: "workspace",
    locations: ["explorer", "workspace"],
    Component: WorkspaceTodoPanel,
  });
  client.addScreen({ id: SCREEN, title: "TideTable", Component: TideTableScreen });
  client.addSettingsScreen({ id: "connection", title: "TideTable", icon: "ListTodo", Component: SettingsScreen });
  client.addCommandCenterItem({
    id: "show-tidetable",
    title: "Show TideTable for this project",
    icon: "ListTodo",
    context: "workspace",
    onSelect({ openPanel }) {
      openPanel(PANEL, { location: "explorer" });
    },
  });
  client.addCommandCenterItem({
    id: "open-tidetable",
    title: "Open TideTable (configured todo or hub)",
    icon: "ListTodo",
    context: "global",
    onSelect({ openScreen }) {
      openScreen({ screenId: SCREEN });
    },
  });
  return () => {};
}

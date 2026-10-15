import type { PluginServerContext } from "@getpaseo/plugin/server";
import { discoverTodos } from "./server/discover";
import { readSidebar, resolveConfig } from "./server/sidebar";
import { discoverRpc } from "./shared/discover";
import { connection } from "./shared/settings";
import { sidebarRpc } from "./shared/sidebar";

export default function contribute(server: PluginServerContext) {
  const settings = server.registerSettings(connection);

  server.handle(sidebarRpc, async ({ todo }) => {
    // Read per call: a settings edit applies on the next poll, no reload.
    const state = await settings.read();
    if (state.status !== "ready") {
      return { ok: false as const, error: "plugin settings are invalid: " + state.error };
    }
    const config = resolveConfig(state.values);
    // A file the panel discovered in its workspace is always local, whatever
    // hub the settings or the daemon's environment name.
    return readSidebar(todo ? { ...config, todo, server: "", token: "" } : config);
  });

  server.handle(discoverRpc, ({ directory }) => discoverTodos(directory));

  return () => {};
}

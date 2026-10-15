import type { PluginServerContext } from "@getpaseo/plugin/server";
import { readSidebar, resolveConfig } from "./server/sidebar";
import { connection } from "./shared/settings";
import { sidebarRpc } from "./shared/sidebar";

export default function contribute(server: PluginServerContext) {
  const settings = server.registerSettings(connection);

  server.handle(sidebarRpc, async () => {
    // Read per call: a settings edit applies on the next poll, no reload.
    const state = await settings.read();
    if (state.status !== "ready") {
      return { ok: false as const, error: "plugin settings are invalid: " + state.error };
    }
    return readSidebar(resolveConfig(state.values));
  });

  return () => {};
}

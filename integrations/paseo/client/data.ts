import { useRpc } from "@getpaseo/plugin/client";
import { useQuery } from "@tanstack/react-query";
import { sidebarRpc } from "../shared/sidebar";

export const POLL_MS = 15_000;

// One query key for the sidebar row, the screen and the workspace panel, so
// they share a single poll through the installation's query cache.
export function useSidebar() {
  const readSidebar = useRpc(sidebarRpc);
  return useQuery({
    queryKey: ["tidetable", "sidebar"],
    queryFn: () => readSidebar({}),
    refetchInterval: POLL_MS,
  });
}

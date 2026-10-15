import { useRpc } from "@getpaseo/plugin/client";
import { useQuery } from "@tanstack/react-query";
import { discoverRpc } from "../shared/discover";
import { sidebarRpc } from "../shared/sidebar";

export const POLL_MS = 15_000;
// New todo files are rare; a slow rescan keeps big repos cheap.
const DISCOVER_MS = 60_000;

// Keyed by todo, so every view of one file shares a single poll through the
// installation's query cache. No todo = the file or hub slug from settings.
export function useSidebar(todo?: string) {
  const readSidebar = useRpc(sidebarRpc);
  return useQuery({
    queryKey: ["tidetable", "sidebar", todo ?? "settings"],
    queryFn: () => readSidebar(todo ? { todo } : {}),
    refetchInterval: POLL_MS,
  });
}

export function useTodoFiles(directory: string | null) {
  const discover = useRpc(discoverRpc);
  return useQuery({
    queryKey: ["tidetable", "discover", directory],
    queryFn: () => discover({ directory: directory ?? "" }),
    enabled: !!directory,
    refetchInterval: DISCOVER_MS,
  });
}

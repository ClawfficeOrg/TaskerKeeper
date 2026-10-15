// Settings → Plugins → paseo-tidetable. A draft editor: inputs edit a local
// copy, and Save writes it against the revision the draft was opened from, so
// a save cannot silently clobber a newer document (the host rejects it).
import type { PluginSurfaceProps } from "@getpaseo/plugin/client";
import { useSettings } from "@getpaseo/plugin/client";
import { SettingsAction, SettingsCard, SettingsInput, SettingsSection } from "@getpaseo/plugin/client/ui";
import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Text } from "react-native";
import { connection } from "../shared/settings";

type Values = { todo: string; server: string; token: string; width: number; height: number };

function Editor({ initial, revision, save, saving, saveError }: {
  initial: Values;
  revision: string;
  save(values: Values, revision: string): Promise<boolean>;
  saving: boolean;
  saveError: string | null;
}) {
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState<Values>(initial);
  const [width, setWidth] = useState(String(initial.width));
  const [height, setHeight] = useState(String(initial.height));
  const w = Number.parseInt(width, 10);
  const h = Number.parseInt(height, 10);
  const widthError = Number.isInteger(w) && w >= 20 ? null : "A whole number, 20 or more";
  const heightError = Number.isInteger(h) && h >= 10 ? null : "A whole number, 10 or more";

  async function onSave() {
    if (await save({ ...draft, width: w, height: h }, revision)) {
      await queryClient.invalidateQueries({ queryKey: ["tidetable", "sidebar"] });
    }
  }

  return (
    <>
      <SettingsSection title="Source">
        <SettingsCard>
          <SettingsInput
            label="Todo"
            hint="Todo file path on the daemon host, or the project slug when a hub is set. Empty uses TIDETABLE_TODO."
            initialValue={initial.todo}
            placeholder="/path/to/todo.json"
            onChangeText={(todo) => setDraft((d) => ({ ...d, todo }))}
          />
          <SettingsInput
            label="Hub URL"
            hint="Base URL of a `tidetable serve` hub. Empty reads the local file (or TIDETABLE_SERVER)."
            initialValue={initial.server}
            placeholder="https://tidetable.example.com"
            onChangeText={(server) => setDraft((d) => ({ ...d, server }))}
          />
          <SettingsInput
            label="Hub token"
            hint="Stored as plain host-side JSON that every connected client can read. Prefer TIDETABLE_TOKEN in the daemon's environment."
            initialValue={initial.token}
            secureTextEntry
            onChangeText={(token) => setDraft((d) => ({ ...d, token }))}
          />
        </SettingsCard>
      </SettingsSection>
      <SettingsSection title="Layout">
        <SettingsCard>
          <SettingsInput label="Width" hint="Line budget, passed as --width" initialValue={width} error={widthError} onChangeText={setWidth} />
          <SettingsInput label="Height" hint="Row budget, passed as --height" initialValue={height} error={heightError} onChangeText={setHeight} />
        </SettingsCard>
      </SettingsSection>
      <SettingsCard>
        <SettingsAction
          label="Save"
          error={saveError}
          actionLabel={saving ? "Saving…" : "Save"}
          disabled={saving || !!widthError || !!heightError}
          onPress={onSave}
        />
      </SettingsCard>
    </>
  );
}

export function SettingsScreen({ theme }: PluginSurfaceProps) {
  const settings = useSettings(connection);
  if (settings.status === "loading") {
    return <Text style={{ color: theme.colors.foregroundMuted }}>Loading…</Text>;
  }
  if (settings.status !== "ready") {
    return (
      <SettingsCard>
        <SettingsAction label="Settings unreadable" hint={settings.error} actionLabel="Reset to defaults" onPress={() => settings.reset()} />
      </SettingsCard>
    );
  }
  // Keyed by revision: a save elsewhere remounts the inputs with the new values.
  return (
    <Editor
      key={settings.revision}
      initial={settings.values}
      revision={settings.revision}
      save={settings.save}
      saving={settings.saving}
      saveError={settings.saveError}
    />
  );
}

"""Which provider and model handles each kind of task.

A task names an agent tier (`basic_dev_agent`, `mid_dev_agent`, ...). This module
turns that tier into a concrete provider and model, so a supervisor reading
`taskerkeeper ready --json` can dispatch without a second lookup.

Configuration is layered, last wins:

    1. built-in defaults          the `anthropic` preset in this file
    2. user config                ~/.config/taskerkeeper/agents.json
    3. repo config                <repo>/.taskerkeeper/agents.json
    4. the todo file itself       agent_config

Each of layers 2-4 contributes twice: first the preset named by its `provider`
key, then its own per-tier settings. So a scope can switch every tier at once
(`agents use opencode-go`) and still override one tier by hand.

Model choice is a property of whoever is running the agents, not of the roadmap,
so the user layer is the normal place for it. The repo layer lets a project pin
something different, and the todo layer lets one milestone do the same. A single
task can override the tier entirely with its own `provider` / `model`.
"""

from __future__ import annotations

import os
from pathlib import Path

from taskerkeeper.jsonio import FileLock, read_json, write_json

#: Where a tier's settings came from, for `agents show`.
LAYER_BUILTIN = "built-in"
LAYER_USER = "user"
LAYER_REPO = "repo"
LAYER_TODO = "todo"

SCOPES = (LAYER_USER, LAYER_REPO, LAYER_TODO)

#: A whole tier table per provider, so switching providers is one command rather
#: than four. Every preset covers every tier the schema's `agent` enum allows,
#: cheap-and-fast at the bottom and most capable at the top. Starting points,
#: not recommendations — override any tier with `agents set`.
PROVIDER_PRESETS: dict[str, dict[str, dict[str, str]]] = {
    "anthropic": {
        "basic_dev_agent": {"provider": "anthropic", "model": "claude-haiku-4-5"},
        "mid_dev_agent": {"provider": "anthropic", "model": "claude-sonnet-5"},
        "pro_dev_agent": {"provider": "anthropic", "model": "claude-opus-5"},
        "flagship": {"provider": "anthropic", "model": "claude-fable-5-1"},
    },
    "opencode-go": {
        "basic_dev_agent": {"provider": "opencode-go", "model": "glm-5.3-flash"},
        "mid_dev_agent": {"provider": "opencode-go", "model": "glm-5.3-flash"},
        "pro_dev_agent": {"provider": "opencode-go", "model": "deepseek-v4-pro"},
        "flagship": {"provider": "opencode-go", "model": "qwen3.8-max"},
    },
}

#: The preset used when no layer selects one.
DEFAULT_PROVIDER = "anthropic"

#: Kept as a name because it reads well at call sites and in the docs.
DEFAULT_TIERS = PROVIDER_PRESETS[DEFAULT_PROVIDER]


def preset_tiers(provider: str) -> dict[str, dict[str, str]]:
    """A fresh copy of one provider's tier table, empty for an unknown name."""
    return {tier: dict(cfg) for tier, cfg in PROVIDER_PRESETS.get(provider, {}).items()}

CONFIG_FILENAME = "agents.json"
REPO_CONFIG_DIR = ".taskerkeeper"


# ---------------------------------------------------------------------------
# Where the config files live
# ---------------------------------------------------------------------------


def user_config_path() -> Path:
    """The per-machine config file.

    `TASKERKEEPER_CONFIG_HOME` overrides everything, which is also what the tests
    use to stay out of the real home directory.
    """
    override = os.environ.get("TASKERKEEPER_CONFIG_HOME")
    if override:
        return Path(override) / CONFIG_FILENAME

    if os.name == "nt":
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / "taskerkeeper" / CONFIG_FILENAME

    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "taskerkeeper" / CONFIG_FILENAME


def find_repo_config(start: str | Path | None = None) -> Path | None:
    """An existing `.taskerkeeper/agents.json` at or above `start`."""
    for directory in _ancestors(start):
        candidate = directory / REPO_CONFIG_DIR / CONFIG_FILENAME
        if candidate.is_file():
            return candidate
    return None


def repo_config_path(start: str | Path | None = None) -> Path:
    """Where a repo-scoped config should be written.

    An existing file wins; otherwise the repository root, identified by `.git`;
    otherwise the starting directory.
    """
    existing = find_repo_config(start)
    if existing:
        return existing
    for directory in _ancestors(start):
        if (directory / ".git").exists():
            return directory / REPO_CONFIG_DIR / CONFIG_FILENAME
    return _start_dir(start) / REPO_CONFIG_DIR / CONFIG_FILENAME


def _start_dir(start: str | Path | None) -> Path:
    if start is None:
        return Path.cwd()
    path = Path(start).resolve()
    return path if path.is_dir() else path.parent


def _ancestors(start: str | Path | None):
    directory = _start_dir(start)
    yield directory
    yield from directory.parents


# ---------------------------------------------------------------------------
# Reading and merging the layers
# ---------------------------------------------------------------------------


def read_config(path: str | Path) -> dict:
    """A config file, or an empty config if there is none."""
    try:
        data = read_json(path)
    except (FileNotFoundError, NotADirectoryError):
        return {}
    return data if isinstance(data, dict) else {}


def read_tiers(path: str | Path) -> dict[str, dict]:
    """Just the `tiers` mapping from a config file."""
    tiers = read_config(path).get("tiers")
    return tiers if isinstance(tiers, dict) else {}


def scoped_configs(todo_data: dict | None = None,
                   todo_path: str | Path | None = None) -> list[tuple[str, dict]]:
    """The user, repo, and todo configs, lowest priority first."""
    start = todo_path or Path.cwd()
    repo = find_repo_config(start)
    out = [
        (LAYER_USER, read_config(user_config_path())),
        (LAYER_REPO, read_config(repo) if repo else {}),
    ]
    if todo_data:
        config = todo_data.get("agent_config")
        out.append((LAYER_TODO, config if isinstance(config, dict) else {}))
    return out


def active_provider(todo_data: dict | None = None,
                    todo_path: str | Path | None = None) -> tuple[str, str]:
    """The selected preset and the layer that selected it."""
    provider, source = DEFAULT_PROVIDER, LAYER_BUILTIN
    for label, config in scoped_configs(todo_data, todo_path):
        if config.get("provider"):
            provider, source = config["provider"], label
    return provider, source


def layers(todo_data: dict | None = None, todo_path: str | Path | None = None) -> list[tuple[str, dict]]:
    """Every configuration layer, lowest priority first.

    A scope that names a provider contributes that preset just below its own
    per-tier settings, so `agents use` moves every tier while a hand-set tier in
    the same scope still wins.
    """
    out = [(LAYER_BUILTIN, preset_tiers(DEFAULT_PROVIDER))]
    for label, config in scoped_configs(todo_data, todo_path):
        provider = config.get("provider")
        if provider:
            out.append((f"{label} preset", preset_tiers(provider)))
        tiers = config.get("tiers")
        out.append((label, tiers if isinstance(tiers, dict) else {}))
    return out


def resolve_tiers(
    todo_data: dict | None = None, todo_path: str | Path | None = None
) -> tuple[dict[str, dict], dict[str, dict[str, str]]]:
    """Merge the layers. Returns (tiers, sources) where sources[tier][key] is a layer."""
    tiers: dict[str, dict] = {}
    sources: dict[str, dict[str, str]] = {}
    for label, layer in layers(todo_data, todo_path):
        for tier, config in layer.items():
            if not isinstance(config, dict):
                continue
            merged = tiers.setdefault(tier, {})
            origin = sources.setdefault(tier, {})
            for key, value in config.items():
                merged[key] = value
                origin[key] = label
    return tiers, sources


def resolve_task(task: dict, tiers: dict[str, dict]) -> dict:
    """The provider/model settings for one task.

    The task's tier supplies the defaults; anything set directly on the task
    (`provider`, `model`, and any other key the tier defines) overrides them.
    """
    resolved = dict(tiers.get(task.get("agent"), {}))
    for key in set(resolved) | {"provider", "model"}:
        if task.get(key) is not None:
            resolved[key] = task[key]
    return {k: v for k, v in resolved.items() if v is not None}


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------


def scope_path(scope: str, todo_path: str | Path | None = None) -> Path:
    """The file a scope writes to."""
    if scope == LAYER_USER:
        return user_config_path()
    if scope == LAYER_REPO:
        return repo_config_path(todo_path)
    if scope == LAYER_TODO:
        if not todo_path:
            raise ValueError("the todo scope needs a todo file")
        return Path(todo_path)
    raise ValueError(f"unknown scope: {scope}")


def set_provider(scope: str, provider: str, todo_path: str | Path | None = None) -> Path:
    """Point a scope at a provider preset. Returns the file written."""
    path = scope_path(scope, todo_path)
    with FileLock(path):
        if scope == LAYER_TODO:
            data = read_json(path)
            data.setdefault("agent_config", {})["provider"] = provider
        else:
            data = _read_config_file(path)
            data["provider"] = provider
        write_json(data, path)
    return path


def clear_provider(scope: str, todo_path: str | Path | None = None) -> tuple[Path, bool]:
    """Drop a scope's preset selection. Returns (path, changed)."""
    path = scope_path(scope, todo_path)
    with FileLock(path):
        if scope == LAYER_TODO:
            data = read_json(path)
            target = data.get("agent_config") or {}
        else:
            data = _read_config_file(path)
            target = data
        if "provider" not in target:
            return path, False
        del target["provider"]
        write_json(data, path)
    return path, True


def set_tier(scope: str, tier: str, settings: dict[str, str], todo_path: str | Path | None = None) -> Path:
    """Merge `settings` into one tier in one scope. Returns the file written."""
    path = scope_path(scope, todo_path)
    with FileLock(path):
        if scope == LAYER_TODO:
            data = read_json(path)
            config = data.setdefault("agent_config", {})
            tiers = config.setdefault("tiers", {})
            tiers.setdefault(tier, {}).update(settings)
        else:
            data = _read_config_file(path)
            data.setdefault("tiers", {}).setdefault(tier, {}).update(settings)
        write_json(data, path)
    return path


def unset_tier(scope: str, tier: str, keys: list[str] | None = None,
               todo_path: str | Path | None = None) -> tuple[Path, bool]:
    """Remove a tier, or named keys from it, in one scope.

    Returns (path, changed). Removing everything from a tier removes the tier.
    """
    path = scope_path(scope, todo_path)
    with FileLock(path):
        if scope == LAYER_TODO:
            data = read_json(path)
            tiers = (data.get("agent_config") or {}).get("tiers") or {}
        else:
            data = _read_config_file(path)
            tiers = data.get("tiers") or {}

        if tier not in tiers:
            return path, False
        if keys:
            for key in keys:
                tiers[tier].pop(key, None)
            if not tiers[tier]:
                del tiers[tier]
        else:
            del tiers[tier]
        write_json(data, path)
    return path, True


def _read_config_file(path: Path) -> dict:
    try:
        data = read_json(path)
    except (FileNotFoundError, NotADirectoryError):
        return {"version": 1, "tiers": {}}
    if not isinstance(data.get("tiers"), dict):
        data["tiers"] = {}
    data.setdefault("version", 1)
    return data

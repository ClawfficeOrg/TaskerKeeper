"""
Unattended task runner: `tidetable overnight run <todo.json>`.

Works through a todo file one task at a time on a throwaway branch
(`overnight/<date>` cut from the base branch). Per task it claims the task,
runs the agent the task's tier resolves to (anthropic -> `claude -p`,
opencode-go -> `opencode run`), then the *runner* — not the agent — runs the
repo's gate commands, asks a read-only reviewer, commits, and closes the task.
It never touches the base branch, never pushes, never tags.

Everything repo-specific lives in `<repo>/.tidetable/overnight.json`
(`tidetable overnight init` writes a starter from the detected stack).

This is the one supervisor-side module. The rest of TideTable never calls a
provider or touches git; keep that split — `cli.py` only registers the
subcommand and no scheduling logic may depend on this module.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import copy
import fnmatch
import json
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from tidetable import jsonio

CONFIG_NAME = "overnight.json"
CONFIG_DIR = ".tidetable"
LEGACY_CONFIG_DIR = ".taskerkeeper"
RUN_DIR_NAME = ".overnight"
RATE_FALLBACK_SEC = 900

# Safe for every repo: read-only inspection. Stack-specific entries come from
# the config (or `init`).
BASE_SHELL_ALLOW = [
    "git status*", "git diff*", "git log*", "git show*", "git ls-files*",
    "tidetable list*", "tidetable deps*", "tidetable history*",
    "ls *", "ls", "pwd", "diff *", "wc *",
]
# The runner owns git history and tidetable state; the agent never does.
BASE_SHELL_DENY = [
    "git push*", "git tag*", "git commit*", "git checkout*", "git switch*",
    "git reset*", "git merge*", "git rebase*", "git branch*", "git stash*",
    "git clean*",
    "tidetable start*", "tidetable done*", "tidetable reset*",
    "tidetable status*",
]
REVIEW_ALLOWED = ["Read", "Glob", "Grep", "Bash(git status*)", "Bash(git diff*)",
                  "Bash(git show*)", "Bash(git log*)"]

# Criteria that need a person looking at the running app; the agent may not claim them.
DEFAULT_HUMAN_CHECK = (r"(?i)\b(on[- ]screen|visual(ly)?|looks?|shows?|displays?|dropdown|picker|"
                       r"window|click|hover|dims?|opacity|blur|renders?|menu|preview|native)\b")

DEFAULTS: dict = {
    "version": 1,
    "base_branch": "main",
    "branch_prefix": "overnight",
    "project": "",                 # one line for the prompt: what this repo is
    "prompt_prefix": "",           # prepended verbatim (e.g. a terse-output mode line)
    "rules": [],                   # extra hard rules appended to the prompt
    "extra_paths": [],             # paths besides `touches` the agent may edit
    "human_check_pattern": DEFAULT_HUMAN_CHECK,
    "human_check_exempt": "",
    "shell_allow": [],
    "shell_deny": [],
    "deny_edit": [],               # globs the agent must never edit (generated files)
    "external_read": [],           # dirs outside the repo the agent may read
    "gate": [],                    # see _gate_steps
    "worktree_dir": "",            # default: <repo>-overnight-<date> beside the repo
    "siblings": [],                # other repos the task may change: {path, scope[]}
    "required_changed": [],        # a DONE that did not touch these becomes PARTIAL
    # `enabled`/`model` are the per-task review (read-only, after the gate, before a task
    # closes). `phase` is a second, optional review that runs once when the last task of a
    # phase closes; it defaults to the task reviewer's model. `task` may also be written as
    # its own object: {"task": {...}, "phase": {...}}.
    "review": {"enabled": True, "model": "anthropic/claude-opus-5-5",
               "phase": {"enabled": False, "model": "", "on_fail": "continue"}},
    # `jobs` > 1 runs that many tasks at once, each in its own worktree on its own branch,
    # merged back onto the run branch as it finishes. Only tasks whose `touches` do not overlap
    # (and which declare `touches`) run together. `job_env` is added to every agent and gate
    # process; `{home}` expands to the checkout, e.g. {"CARGO_TARGET_DIR": "{home}/.overnight/target"}
    # to share one cargo target dir between jobs (cargo serialises the builds).
    "parallel": {"jobs": 1, "job_env": {}},
    "limits": {"task_minutes": 120, "gate_minutes": 45,
               "max_consecutive_failures": 2, "lease_minutes": 480},
    "agent_commands": {},          # provider -> {cmd:[...with {model}], stdin:bool}
    "lanes": {},                   # leased-capacity providers, see LANE_DEFAULTS
}

# A leased-lane provider (e.g. singularity): an OpenAI-compatible endpoint that only answers
# inside a reserved window. The runner reads the window from the reservations API (it never
# books), uses the lane until `margin_minutes` before the window ends, then routes to `fallback`.
# Secrets are only ever read from the environment (run under `infisical run`).
LANE_DEFAULTS: dict = {
    "api_base": "https://app.singularityapi.tech/api/v1",
    "reservation_key_env": "SINGULARITY_API_LANE_RESERVATION_API_KEY",
    "endpoint_env": "SINGULARITY_API_LANE_ENDPOINT",
    "key_env": "SINGULARITY_LANE_APIKEY_SECRET",
    "margin_minutes": 20,
    "fallback": "",                # provider/model used when the lane is closed (required)
    "model_options": {"reasoning_effort": "none"},
    "check_seconds": 60,
}


def _ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def lane_window(reservations: list[dict], now: datetime) -> tuple[datetime, datetime] | None:
    """The merged window of live reservations that covers `now`, or None."""
    live = sorted((_ts(r["starts_at"]), _ts(r["ends_at"])) for r in reservations
                  if not r.get("cancelled_at") and r.get("starts_at") and r.get("ends_at"))
    start = end = None
    for s, e in live:
        if end is not None and s <= end:
            end = max(end, e)
        elif end is not None and start <= now < end:
            break
        else:
            start, end = s, e
    if start is not None and start <= now < end:
        return start, end
    return None

STACKS = {
    "Cargo.toml": {
        "gate": [{"name": "clippy", "cmd": ["cargo", "clippy", "--all-targets", "--", "-D", "warnings"]},
                 {"name": "test", "cmd": ["cargo", "test"]}],
        "shell_allow": ["cargo test*", "cargo clippy*", "cargo check*", "cargo build*", "cargo tree*"],
        "shell_deny": ["cargo install*", "cargo publish*"],
    },
    "package.json": {
        "gate": [{"name": "test", "cmd": ["npm", "test", "--silent"]}],
        "shell_allow": ["npm test*", "npm run *", "npx tsc*"],
        "shell_deny": ["npm publish*"],
    },
    "pyproject.toml": {
        "gate": [{"name": "pytest", "cmd": [sys.executable or "python", "-m", "pytest", "-q"]}],
        "shell_allow": ["python -m pytest*", "python -m unittest*"],
        "shell_deny": ["pip install*", "pip uninstall*"],
    },
    "go.mod": {
        "gate": [{"name": "vet", "cmd": ["go", "vet", "./..."]},
                 {"name": "test", "cmd": ["go", "test", "./..."]}],
        "shell_allow": ["go test*", "go vet*", "go build*"],
        "shell_deny": ["go install*"],
    },
}


# ── process + git helpers ────────────────────────────────────────────────────

@dataclass
class Proc:
    code: int
    out: str = ""
    err: str = ""
    timed_out: bool = False


def _kill_tree(p: subprocess.Popen) -> None:
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"],
                           capture_output=True, check=False)
        else:
            import signal
            os.killpg(os.getpgid(p.pid), signal.SIGKILL)
    except OSError:
        p.kill()


def run_proc(argv: list[str], cwd: str | Path, stdin: str | None = None,
             timeout: int = 0, env: dict | None = None, follow: bool = False,
             prefix: str = "") -> Proc:
    """Run argv (no shell) with an optional stdin and a hard timeout in seconds.

    `follow` echoes the child's output to this console as it arrives; the full
    output is still captured and returned.
    """
    exe = shutil.which(argv[0])
    if exe is None:
        return Proc(127, "", f"{argv[0]}: not found on PATH")
    full_env = {**os.environ, **(env or {})}
    full_env.setdefault("PYTHONIOENCODING", "utf-8")
    p = subprocess.Popen([exe, *argv[1:]], cwd=str(cwd), env=full_env,
                         stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         encoding="utf-8", errors="replace",
                         start_new_session=(os.name != "nt"))
    timed_out = False
    if follow:
        import threading
        bufs: dict[str, list[str]] = {"out": [], "err": []}

        def pump(stream, key):
            for line in stream:
                bufs[key].append(line)
                try:
                    sys.stdout.write(prefix + line)
                    sys.stdout.flush()
                except (OSError, ValueError):
                    pass
        threads = [threading.Thread(target=pump, args=(p.stdout, "out"), daemon=True),
                   threading.Thread(target=pump, args=(p.stderr, "err"), daemon=True)]
        for t in threads:
            t.start()
        if stdin is not None:
            try:
                p.stdin.write(stdin)
                p.stdin.close()
            except OSError:
                pass
        try:
            p.wait(timeout=timeout or None)
        except subprocess.TimeoutExpired:
            timed_out = True
            _kill_tree(p)
            p.wait()
        for t in threads:
            t.join(timeout=5)
        return Proc(p.returncode, "".join(bufs["out"]), "".join(bufs["err"]), timed_out)
    try:
        out, err = p.communicate(stdin, timeout=timeout or None)
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_tree(p)
        out, err = p.communicate()
    return Proc(p.returncode, out or "", err or "", timed_out)


def touches_overlap(a: list[str], b: list[str]) -> bool:
    """True when any path in `a` is the same as, inside, or contains a path in `b`."""
    for x in a:
        for y in b:
            x, y = x.replace("\\", "/").rstrip("/"), y.replace("\\", "/").rstrip("/")
            if x == y or x.startswith(y + "/") or y.startswith(x + "/"):
                return True
    return False


def git(cwd: str | Path, *args: str) -> Proc:
    return run_proc(["git", "-C", str(cwd), *args], cwd)


def changed_paths(cwd: str | Path, pathspec: list[str] | None = None) -> list[str]:
    """Changed + untracked paths (repo-relative, forward slashes), rename-aware."""
    args = ["status", "--porcelain=v1", "-z", "--untracked-files=all"]
    if pathspec:
        args += ["--", *pathspec]
    parts = git(cwd, *args).out.split("\0")
    paths: list[str] = []
    i = 0
    while i < len(parts):
        entry = parts[i]
        i += 1
        if len(entry) < 4:
            continue
        paths.append(entry[3:])
        if entry[0] in "RC" and i < len(parts) and parts[i]:
            paths.append(parts[i])
            i += 1
    return list(dict.fromkeys(paths))


def clear_index_lock(cwd: str | Path) -> None:
    gd = git(cwd, "rev-parse", "--absolute-git-dir").out.strip()
    lock = Path(gd) / "index.lock"
    if lock.exists():
        lock.unlink(missing_ok=True)


def matches(path: str, globs: list[str]) -> bool:
    p = path.replace("\\", "/")
    return any(fnmatch.fnmatch(p, g) or p.startswith(g.rstrip("/") + "/") for g in globs)


# ── config ───────────────────────────────────────────────────────────────────

def repo_root_for(todo_path: Path) -> Path:
    d = todo_path.resolve().parent
    out = git(d, "rev-parse", "--show-toplevel")
    if out.code != 0:
        raise ValueError(f"{todo_path} is not inside a git repository")
    return Path(out.out.strip())


def detect_stacks(root: Path) -> list[str]:
    return [name for name in STACKS if (root / name).exists()]


def config_path(root: Path) -> Path:
    """`<repo>/.tidetable/overnight.json`, or the legacy `.taskerkeeper/` one if only it exists."""
    current = root / CONFIG_DIR / CONFIG_NAME
    legacy = root / LEGACY_CONFIG_DIR / CONFIG_NAME
    return legacy if legacy.is_file() and not current.is_file() else current


def load_config(root: Path) -> tuple[dict, Path | None]:
    path = config_path(root)
    cfg = json.loads(json.dumps(DEFAULTS))
    if path.is_file():
        user = json.loads(path.read_text(encoding="utf-8"))
        for k, v in user.items():
            if k not in DEFAULTS:
                raise ValueError(f"{path}: unknown key '{k}'")
            if isinstance(DEFAULTS[k], dict) and isinstance(v, dict):
                cfg[k].update(v)
            else:
                cfg[k] = v
        return cfg, path
    # Zero-config: infer the gate from the stack so a bare repo still gets verified.
    for name in detect_stacks(root):
        s = STACKS[name]
        cfg["gate"] += s["gate"]
        cfg["shell_allow"] += s["shell_allow"]
        cfg["shell_deny"] += s["shell_deny"]
    return cfg, None


def starter_config(root: Path) -> dict:
    cfg: dict = {"version": 1, "project": f"{root.name} repository", "gate": [],
                 "shell_allow": [], "shell_deny": [], "rules": [], "extra_paths": []}
    for name in detect_stacks(root):
        s = STACKS[name]
        cfg["gate"] += s["gate"]
        cfg["shell_allow"] += s["shell_allow"]
        cfg["shell_deny"] += s["shell_deny"]
    return cfg


# ── runner state ─────────────────────────────────────────────────────────────

@dataclass
class Runner:
    todo: Path
    root: Path
    cfg: dict
    date: str
    deadline: datetime
    model_override: str = ""
    no_review: bool = False
    review_model: str = ""
    no_phase_review: bool = False
    follow: bool = False             # echo the agent's output to the console as it runs
    phase_review_model: str = ""
    jobs: int = 0                    # 0 = use config `parallel.jobs`
    dry_run: bool = False
    base_override: str = ""
    hub: dict | None = None          # {"server", "token", "project"}: todo lives on a hub
    exhausted: set = field(default_factory=set)
    lane_cache: dict = field(default_factory=dict)
    lane_last: dict = field(default_factory=dict)
    sib_ready: set = field(default_factory=set)
    sib_start: dict = field(default_factory=dict)
    main_sha: str = ""
    log_enabled: bool = False

    # derived paths / names
    def __post_init__(self):
        self.branch = f"{self.cfg['branch_prefix']}/{self.date}"
        # On a hub several machines may run the same date; the owner names the machine
        # too, or one runner would count as "the same agent" and refresh another's claim.
        self.owner = f"overnight-{self.date}@{socket.gethostname()}" if self.hub \
            else f"overnight-{self.date}"
        self.home = self.root.resolve()            # the checkout you started from; never edited
        self.base = self.base_override or self.cfg["base_branch"]
        wt = self.cfg["worktree_dir"]
        self.wt_path = Path(os.path.expanduser(wt)).resolve() if wt else \
            self.home.parent / f"{self.home.name}-overnight-{self.date}"
        self.run_dir = self.home / RUN_DIR_NAME / self.date
        self.log_file = self.home / RUN_DIR_NAME / "log.md"
        self.stop_file = self.home / RUN_DIR_NAME / "STOP"
        self.kill_file = self.home / RUN_DIR_NAME / "KILL"
        self.pid_file = self.home / RUN_DIR_NAME / "pid"
        if self.hub:
            # The todo file is not in this repo: nothing of it is committed or diffed here.
            self.todo_rel = ""
            self.todo_paths = []
        else:
            self.todo_rel = self.todo.resolve().relative_to(self.home).as_posix()
            self.todo_paths = [self.todo_rel, self.todo_rel + ".events.jsonl"]
        lim = self.cfg["limits"]
        self.task_sec = int(lim["task_minutes"]) * 60
        self.gate_sec = int(lim["gate_minutes"]) * 60
        self.lease = int(lim["lease_minutes"])
        self.max_fail = int(lim["max_consecutive_failures"])
        self.rate_margin = int(os.environ.get("OVERNIGHT_RATE_MARGIN_SEC", "120"))
        self.claude_bin = shlex.split(os.environ.get("OVERNIGHT_CLAUDE", "claude"))
        self.opencode_bin = shlex.split(os.environ.get("OVERNIGHT_OPENCODE", "opencode"))
        self.siblings = []
        for sc in self.cfg["siblings"]:
            h = (self.home / sc["path"]).resolve()
            self.siblings.append({"path": sc["path"], "home": h, "scope": list(sc.get("scope", [])),
                                  "root": h.parent / f"{h.name}-overnight-{self.date}"})
        # {sibling:P} = the sibling's worktree (edit here); {sibling_home:P} = the real checkout
        # (read-only use of its built tools, e.g. a prebuilt binary)
        sub = {"{sibling:%s}" % x["path"]: str(x["root"]) for x in self.siblings}
        sub.update({"{sibling_home:%s}" % x["path"]: str(x["home"]) for x in self.siblings})

        def expand(v):
            if isinstance(v, str):
                for k, val in sub.items():
                    v = v.replace(k, val)
                return v
            if isinstance(v, list):
                return [expand(i) for i in v]
            if isinstance(v, dict):
                return {k: expand(i) for k, i in v.items()}
            return v
        for k in ("rules", "shell_allow", "shell_deny", "external_read", "gate", "extra_paths", "prompt_prefix"):
            self.cfg[k] = expand(self.cfg[k])
        self.shell_allow = BASE_SHELL_ALLOW + list(self.cfg["shell_allow"])
        self.shell_deny = BASE_SHELL_DENY + list(self.cfg["shell_deny"])
        rv = self.cfg["review"]
        task_rv = {"enabled": rv.get("enabled", True), "model": rv.get("model", ""), **rv.get("task", {})}
        self.review_enabled = bool(task_rv["enabled"]) and not self.no_review
        self.review_spec = self.review_model or task_rv["model"] or "anthropic/claude-opus-5-5"
        phase_rv = {"enabled": False, "model": "", "on_fail": "continue", **rv.get("phase", {})}
        self.phase_enabled = (bool(phase_rv["enabled"]) or bool(self.phase_review_model)) \
            and not self.no_phase_review
        self.phase_spec = self.phase_review_model or phase_rv["model"] or self.review_spec
        self.phase_on_fail = phase_rv["on_fail"]
        if self.phase_on_fail not in ("continue", "stop"):
            raise ValueError("review.phase.on_fail must be 'continue' or 'stop'")
        self.phase_reviewed: set[str] = set()
        par = self.cfg["parallel"]
        self.job_count = max(1, int(self.jobs or par.get("jobs", 1)))
        self.job_env = {k: str(v).replace("{home}", str(self.home)) for k, v in par.get("job_env", {}).items()}
        self.follow_prefix = ""
        self.merge_lock = threading.Lock()
        for name, lane in self.cfg["lanes"].items():
            merged = {**LANE_DEFAULTS, **lane}
            if "/" not in str(merged["fallback"]):
                raise ValueError(f"lanes.{name}.fallback must be 'provider/model' (used when the lane is closed)")
            self.cfg["lanes"][name] = merged

    # ── leased lanes
    def lane_reservations(self, lane: dict) -> list[dict]:
        """Reservations from the booking API (read-only). Raises on any failure."""
        import urllib.request
        key = os.environ.get(lane["reservation_key_env"], "")
        if not key:
            raise RuntimeError(f"{lane['reservation_key_env']} is not set (run under `infisical run`)")
        req = urllib.request.Request(lane["api_base"].rstrip("/") + "/reservations?status=all",
                                     headers={"Authorization": f"Bearer {key}"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8")).get("reservations", [])

    def lane_open(self, name: str, lane: dict) -> tuple[bool, str]:
        """(usable, why). Cached for check_seconds; any failure means closed."""
        from datetime import timezone
        now = datetime.now(timezone.utc)
        hit = self.lane_cache.get(name)
        if hit and (now - hit[0]).total_seconds() < float(lane["check_seconds"]):
            return hit[1]
        try:
            win = lane_window(self.lane_reservations(lane), now)
            if win is None:
                res = (False, "no active reservation")
            else:
                left = (win[1] - now).total_seconds() / 60
                margin = float(lane["margin_minutes"])
                res = (left > margin, f"{left:.0f} min left in the lease (margin {margin:.0f})")
        except Exception as e:  # network, auth, bad JSON: never route work to a lane we cannot confirm
            res = (False, f"cannot read reservations: {e}")
        self.lane_cache[name] = (now, res)
        return res

    def lane_route(self, provider: str, model: str) -> tuple[str, str]:
        lane = self.cfg["lanes"].get(provider)
        if not lane:
            return provider, model
        ok, why = self.lane_open(provider, lane)
        state = "lane" if ok else "fallback"
        if self.lane_last.get(provider) != state:
            self.lane_last[provider] = state
            self.log(f"LANE {provider}: {'using the lane' if ok else 'closed, routing to ' + lane['fallback']} ({why})")
        return (provider, model) if ok else self.split_agent(lane["fallback"])

    @property
    def opencode_cfg(self) -> Path:
        return self.root / "opencode.json"

    # ── logging
    def say(self, msg: str) -> None:
        print(f"[overnight] {msg}", flush=True)

    def log(self, msg: str) -> None:
        self.say(msg)
        if self.log_enabled:
            with open(self.log_file, "a", encoding="utf-8") as f:
                f.write(f"- {datetime.now():%Y-%m-%d %H:%M:%S} {msg}\n")

    # ── tidetable
    def tk(self, *args: str) -> Proc:
        env = {"TIDETABLE_OWNER": self.owner, "TASKERKEEPER_OWNER": self.owner}
        head: list[str] = []
        if self.hub:
            env["TIDETABLE_SERVER"] = self.hub["server"]
            env["TIDETABLE_TOKEN"] = self.hub.get("token", "")
        else:
            head = ["--local"]    # a $TIDETABLE_SERVER in the environment must not hijack a path
        return run_proc([sys.executable, "-m", "tidetable", *head, *args], self.root, env=env)

    @property
    def todo_arg(self) -> str:
        """What the CLI is told to operate on: the hub project slug, or the todo file."""
        return self.hub["project"] if self.hub else str(self.todo)

    def todo_changes(self) -> list[str]:
        """Changed todo/event-log paths in the worktree; always empty on a hub.

        (An empty pathspec would mean "everything" to `changed_paths`.)
        """
        return changed_paths(self.root, self.todo_paths) if self.todo_paths else []

    def next_task(self, skip: list[str]) -> dict | None:
        r = self.tk("ready", self.todo_arg, "--json")
        try:
            ready = json.loads(r.out).get("ready", [])
        except json.JSONDecodeError:
            raise RuntimeError(f"tidetable ready failed: {r.out}{r.err}")
        for t in ready:
            if t.get("status") == "pending" and t["id"] not in skip:
                return t
        return None

    # ── agent selection
    @staticmethod
    def split_agent(spec: str) -> tuple[str, str]:
        if "/" in spec:
            prov, model = spec.split("/", 1)
        else:
            prov, model = "anthropic", spec
        return ("anthropic" if prov == "claude-code" else prov), model

    def resolve_agent(self, task: dict) -> tuple[str, str]:
        if self.model_override:
            return self.split_agent(self.model_override)
        tier = str(task.get("agent", ""))
        env = os.environ.get("OVERNIGHT_MODEL_" + re.sub(r"_agent$", "", tier).upper())
        if env:
            return self.split_agent(env)
        if task.get("provider") and task.get("model"):
            return self.split_agent(f"{task['provider']}/{task['model']}")
        raise ValueError(f"tidetable gave no provider/model for {task['id']} "
                         f"(tier '{tier}'); see `tidetable agents show`")

    # ── prompts
    def human_check(self, task: dict) -> list[str]:
        pat, ex = self.cfg["human_check_pattern"], self.cfg["human_check_exempt"]
        if not pat:
            return []
        return [s for s in task.get("success", [])
                if re.search(pat, s) and not (ex and re.search(ex, s))]

    def gate_commands_text(self) -> str:
        steps = [" ".join(map(shlex.quote, s["cmd"])) for s in self.cfg["gate"]]
        return "\n".join(f"  {s}" for s in steps) or "  (none configured)"

    def build_prompt(self, task: dict, spec: str, resume: bool) -> str:
        c = self.cfg
        lines = [f"Task: {task['id']} - {task.get('title', '')}"]
        for f in ("goal", "complexity", "tests", "changelog", "decision"):
            if task.get(f):
                lines.append(f"{f}: {task[f]}")
        if task.get("touches"):
            lines.append("Owned paths (touches): " + ", ".join(task["touches"]))
        if task.get("success"):
            lines.append("Success criteria:")
            lines += [f"  - {s}" for s in task["success"]]
        human = self.human_check(task)
        human_note = ("The runner flags these criteria as likely needing a human to look at the running app:\n"
                      + "\n".join(f"  - {s}" for s in human)) if human else \
            "The runner flagged no criteria as human-check, but apply the rule below anyway."
        resume_note = ("\nNOTE: an earlier run of this task was interrupted (usage limit). The working tree "
                       "may already hold its partial work; inspect `git status`/`git diff` and continue it "
                       "rather than starting over.\n") if resume else ""
        sib = "".join(f"\nAlso editable: {s['root']} (another repo's worktree, absolute path), only under "
                      f"{', '.join(s['scope']) or 'its whole tree'}." for s in self.siblings)
        extra = ", ".join(c["extra_paths"])
        rules = [
            "Scope: edit only the owned paths above" + (f", plus {extra}" if extra else "")
            + ". Nothing else unless the task cannot work without it; say so in your summary.",
            "Do not run git commit/checkout/switch/branch/stash/reset/push/tag or tidetable "
            "start/done/reset/status. The runner does all of that.",
            "Do not install anything and do not take screenshots or drive a GUI.",
            "After you finish, the runner runs these itself; run them first for what you touched:\n"
            + self.gate_commands_text(),
            "Write tests for every behaviour you can test headlessly.",
        ] + list(c["rules"])
        numbered = "\n".join(f"{i}. {r}" for i, r in enumerate(rules, 1))
        project = c["project"] or "this repository"
        prefix = c["prompt_prefix"].rstrip() + "\n\n" if c["prompt_prefix"] else ""
        return f"""{prefix}You are the unattended overnight agent for {project}.
The owner is asleep. Implement exactly one task, verify it, and report. Model: {spec}.
Working directory: {self.root} (absolute), branch {self.branch}, a git worktree cut from {self.base}.
Stay inside it. The original checkout at {self.home} belongs to the owner and must never be read for state or edited.{sib}
{resume_note}
TASK
{chr(10).join(lines)}

{human_note}

RULES (hard)
{numbered}

HUMAN-CHECK RULE (how the task gets closed)
A success criterion counts as verified only if an automated test you ran proves it, or it is a mechanical fact (file/doc/key exists, config parses, generated output unchanged).
A criterion that can only be confirmed by a person looking at or clicking the running app is NOT verified: implement it as far as possible, cover the logic behind it with tests, and list it as needing human check.
The runner closes the task only on DONE; on PARTIAL it commits your work and leaves the task in progress for the owner.

FINAL OUTPUT (required, last lines, exact format)
OVERNIGHT_SUMMARY: <one line, imperative, <= 72 chars, for the commit subject>
OVERNIGHT_RESULT: DONE                                   (every criterion verified, gate commands pass)
OVERNIGHT_RESULT: PARTIAL <criteria needing a human check, ';'-separated>
OVERNIGHT_RESULT: FAILED <reason>                        (could not make it work; leave the tree as is)
"""

    def build_review_prompt(self, task: dict, changed: list[str], sib_changed: dict) -> str:
        succ = "\n".join(f"  - {s}" for s in task.get("success", []))
        stat = git(self.root, "diff", "HEAD", "--stat").out
        sibs = "\n".join(f"Files changed in {p}: {', '.join(v)} (inspect with git -C {p} diff)"
                         for p, v in sib_changed.items() if v)
        forbid = ", ".join(self.cfg["deny_edit"])
        return f"""You are a strict read-only reviewer. Do not edit anything. Review the uncommitted work for
task {task['id']} - {task.get('title', '')}, in the current directory.
Goal: {task.get('goal', '')}
Success criteria:
{succ}

Changed/new files: {', '.join(changed)}
{sibs}
Diff stat:
{stat}

Inspect with git diff / git status and Read (new files are untracked, read them directly).
The gate commands already passed; do not rerun them.
FAIL the review if: a success criterion is claimed but not implemented or not covered by a
test; behaviour is stubbed or faked to make tests pass; files outside the task's scope were
changed without need;{f' these were hand-edited: {forbid};' if forbid else ''} there is an obvious bug, panic/crash path or
security problem. Criteria that can only be checked on screen are out of scope for you.

Last line, exactly one of:
REVIEW_RESULT: PASS
REVIEW_RESULT: FAIL <short reasons, ';'-separated>
"""

    def build_phase_review_prompt(self, phase: dict, goal: str, ids: list[str], commits: list[str]) -> str:
        crit = []
        for t in phase.get("tasks", []):
            if t["id"] in ids:
                crit.append(f"  {t['id']} - {t.get('title', '')}")
        done = "\n".join(crit)
        cl = "\n".join(f"  {c}" for c in commits) or "  (none made in this run; the work is already on the base branch)"
        return f"""You are a strict read-only reviewer. Do not edit anything. Every task of phase {phase['id']}
- {phase.get('title', '')} has now closed. Review the phase as a whole, in the current directory.
Phase goal: {goal}
Tasks in the phase:
{done}

Commits made for this phase in this run:
{cl}
The full run so far is `git diff {self.base}...HEAD`; inspect with git show / git diff and Read.
Each task was already reviewed on its own and the gate passed; do not rerun the gate. Judge what
per-task review cannot see: does the phase goal hold end to end, do the tasks fit together, was
a task's work quietly undone or duplicated by another, is anything stubbed, left half-wired, or
inconsistent between modules, and are there security or crash paths across task boundaries.
Criteria that can only be checked on screen are out of scope for you.

Last line, exactly one of:
REVIEW_RESULT: PASS
REVIEW_RESULT: FAIL <short reasons, ';'-separated>
"""

    def review_phase(self, tid: str) -> str | None:
        """Review the phase `tid` belongs to, once, when that phase has just completed.

        Returns PASS / FAIL, "error" when the reviewer did not finish, or None when
        nothing was reviewed (phase still open, already reviewed, reviewer out of quota).
        """
        r = self.tk("list", self.todo_arg, "--json")
        try:
            phases = json.loads(r.out).get("phases", [])
        except json.JSONDecodeError:
            return None
        phase = next((ph for ph in phases if any(t["id"] == tid for t in ph.get("tasks", []))), None)
        if not phase or not phase.get("complete") or phase["id"] in self.phase_reviewed:
            return None
        self.phase_reviewed.add(phase["id"])
        rp, rm = self.split_agent(self.phase_spec)
        if rp in self.exhausted:
            self.log(f"PHASE REVIEW {phase['id']} not run (reviewer out of quota)")
            return None
        goal = phase.get("goal") or phase.get("title", "")
        if not self.hub and self.todo.is_file():
            try:
                full = json.loads(self.todo.read_text(encoding="utf-8")).get("phases", [])
                goal = next((x.get("goal") for x in full if x.get("id") == phase["id"] and x.get("goal")), goal)
            except (OSError, json.JSONDecodeError):
                pass
        ids = [t["id"] for t in phase.get("tasks", [])]
        lines = git(self.root, "log", "--format=%h %s", f"{self.base}..HEAD").out.splitlines()
        commits = [ln for ln in lines if any(ln.split(" ", 1)[-1].startswith(f"{i}:")
                                             or ln.split(" ", 1)[-1].startswith(f"wip({i}):") for i in ids)]
        self.say(f"phase review {phase['id']}: {rp}/{rm}")
        prompt = self.build_phase_review_prompt(phase, goal, ids, commits)
        rrun = self.run_agent(rp, rm, lambda _resume: prompt, f"phase-{phase['id']}-review", "review")
        hits = re.findall(r"(?m)^\s*REVIEW_RESULT:\s*(PASS|FAIL)\b[ \t]*(.*)$", rrun["res"].out)
        if rrun["status"] == "limited":
            self.exhausted.add(rp)
        if rrun["status"] != "ok" or not hits:
            rel = rrun["transcript"].relative_to(self.home).as_posix()
            self.log(f"PHASE REVIEW {phase['id']} not completed ({rrun['status']}); see {rel}")
            return "error"
        if hits[-1][0] == "FAIL":
            self.log(f"PHASE REVIEW FAIL {phase['id']}: {hits[-1][1].strip()}")
            return "FAIL"
        self.log(f"PHASE REVIEW PASS {phase['id']}")
        return "PASS"

    # ── agent invocation
    def lane_provider_block(self, provider: str, model: str) -> dict:
        """opencode custom provider for a lane. Secrets stay in the environment ({env:...})."""
        lane = self.cfg["lanes"][provider]
        return {provider: {
            "npm": "@ai-sdk/openai-compatible", "name": provider,
            "options": {"baseURL": "{env:%s}" % lane["endpoint_env"], "apiKey": "{env:%s}" % lane["key_env"]},
            "models": {model: {"name": model, "options": dict(lane["model_options"])}}}}

    def opencode_config(self, extra_provider: dict | None = None) -> str:
        bash = {"*": "deny"}
        bash.update({p: "allow" for p in self.shell_allow})
        bash.update({p: "deny" for p in self.shell_deny})
        ext = {"*": "deny"}
        for d in self.cfg["external_read"]:
            ext[str(Path(os.path.expandvars(os.path.expanduser(d))).resolve()) + os.sep + "**"] = "allow"
        for s in self.siblings:
            for sc in s["scope"] or ["."]:
                ext[str((s["root"] / sc).resolve()) + os.sep + "**"] = "allow"
        edit = {"*": "allow"}
        edit.update({f"*{Path(g).name}" if "/" not in g else g: "deny" for g in self.cfg["deny_edit"]})
        cfg = {"$schema": "https://opencode.ai/config.json", "permission": {
            "bash": bash, "edit": edit, "external_directory": ext,
            "question": "deny", "webfetch": "allow"}}
        if extra_provider:
            cfg["provider"] = extra_provider
        return json.dumps(cfg, indent=2)

    def agent_call(self, provider: str, model: str, prompt: str, kind: str = "dev") -> dict:
        allowed = ["Read", "Edit", "Write", "Glob", "Grep", "WebFetch", "WebSearch"] \
            + [f"Bash({p})" for p in self.shell_allow]
        denied = [f"Bash({p})" for p in self.shell_deny] \
            + [f"{t}(**/{g})" for g in self.cfg["deny_edit"] for t in ("Edit", "Write")]
        custom = self.cfg["agent_commands"].get(provider)
        if custom:
            argv = [a.format(model=model, prompt=prompt) for a in custom["cmd"]]
            return {"argv": argv, "stdin": prompt if custom.get("stdin") else None, "opencode": None}
        if provider == "anthropic":
            common = ["-p", "--model", model, "--output-format", "text", "--permission-prompts", "none",
                      "--setting-sources", "project,local", "--strict-mcp-config"]
            if kind == "review":
                a = self.claude_bin + common + ["--tools", "Read,Glob,Grep,Bash", "--allowedTools",
                                                *REVIEW_ALLOWED, "--disallowedTools", *denied]
            else:
                a = self.claude_bin + common + ["--permission-mode", "acceptEdits",
                                                "--tools", "Read,Edit,Write,Glob,Grep,Bash,WebFetch,WebSearch",
                                                "--allowedTools", *allowed, "--disallowedTools", *denied]
                for s in self.siblings:
                    for sc in s["scope"] or ["."]:
                        a += ["--add-dir", str((s["root"] / sc).resolve())]
            return {"argv": a, "stdin": prompt, "opencode": None}
        if provider in self.cfg["lanes"]:
            a = self.opencode_bin + ["run", "--standalone", "--auto", "-m", f"{provider}/{model}",
                                     "--title", f"overnight {self.date}", prompt]
            return {"argv": a, "stdin": None,
                    "opencode": self.opencode_config(self.lane_provider_block(provider, model))}
        if provider == "opencode-go":
            a = self.opencode_bin + ["run", "--standalone", "--auto", "-m", f"{provider}/{model}",
                                     "--title", f"overnight {self.date}", prompt]
            return {"argv": a, "stdin": None, "opencode": self.opencode_config()}
        raise ValueError(f"no CLI for provider '{provider}' (add it under agent_commands)")

    def rate_limit_reset(self, text: str, now: datetime | None = None) -> datetime | None:
        """None when the output is not a usage/rate limit, else when to resume."""
        now = now or datetime.now()
        if not re.search(r"(?i)\b429\b|rate.?limit|usage limit|session limit|limit reached|"
                         r"hit your limit|too many requests|overloaded|\b529\b", text):
            return None
        margin = timedelta(seconds=self.rate_margin)
        m = re.search(r"\|(\d{10})\b", text)
        if m:
            return datetime.fromtimestamp(int(m.group(1))) + margin
        m = re.search(r"(?i)resets?\s+(?:at\s+)?(\d{4}-\d{2}-\d{2}[T ][\d:\.]+(?:Z|[+-]\d{2}:?\d{2})?)", text)
        if m:
            try:
                dt = datetime.fromisoformat(m.group(1).replace("Z", "+00:00"))
                return (dt.astimezone().replace(tzinfo=None) if dt.tzinfo else dt) + margin
            except ValueError:
                pass
        m = (re.search(r"(?i)resets?\s+(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", text)
             or re.search(r"(?i)resets?\s+(?:at\s+)?(\d{1,2}):(\d{2})()\b", text))
        if m:
            h, mi, ap = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or "").lower()
            if ap == "pm" and h < 12:
                h += 12
            elif ap == "am" and h == 12:
                h = 0
            t = now.replace(hour=h % 24, minute=mi, second=0, microsecond=0)
            if t <= now:
                t += timedelta(days=1)
            return t + margin
        return now + timedelta(seconds=RATE_FALLBACK_SEC)

    def run_agent(self, provider: str, model: str, make_prompt, label: str, kind: str = "dev") -> dict:
        """One agent call, sleeping through usage limits. status: ok | limited | stop."""
        resume, n = False, 0
        while True:
            n += 1
            call = self.agent_call(provider, model, make_prompt(resume), kind)
            # The deadline only stops new tasks from starting; a started task is
            # never cut short by it, only by the per-task limit.
            cap = self.task_sec
            if call["opencode"]:
                if self.opencode_cfg.exists():
                    raise RuntimeError(f"{self.opencode_cfg} already exists; remove it (the runner writes its own)")
                self.opencode_cfg.write_text(call["opencode"], encoding="utf-8")
            try:
                res = run_proc(call["argv"], self.root, stdin=call["stdin"], timeout=cap, follow=self.follow,
                               prefix=self.follow_prefix,
                               env={"TIDETABLE_OWNER": self.owner, "TASKERKEEPER_OWNER": self.owner,
                                    **self.job_env})
            finally:
                if call["opencode"]:
                    self.opencode_cfg.unlink(missing_ok=True)
            tr = self.run_dir / f"{label}-{n}.txt"
            tr.write_text(f"{provider}/{model} exit {res.code} timedOut={res.timed_out} cap={cap}s\n"
                          f"--- stdout\n{res.out}\n--- stderr\n{res.err}\n", encoding="utf-8")
            self.assert_safe()
            answered = re.search(r"(?m)^\s*(OVERNIGHT_RESULT|REVIEW_RESULT):", res.out)
            if not answered and not res.timed_out:
                tail = "\n".join((res.out + "\n" + res.err).splitlines()[-25:])
                until = self.rate_limit_reset(tail)
                if until is not None:
                    if self.deadline <= until:
                        self.log(f"RATE LIMIT {provider} on {label}; reset {until:%H:%M} is past the deadline")
                        return {"status": "limited", "res": res, "transcript": tr}
                    self.log(f"RATE LIMIT {provider} on {label}; sleeping until {until:%H:%M:%S}")
                    while datetime.now() < until:
                        if self.stop_file.exists() or self.kill_file.exists():
                            return {"status": "stop", "res": res, "transcript": tr}
                        time.sleep(max(1, min(60, (until - datetime.now()).total_seconds())))
                    resume = True
                    continue
            return {"status": "ok", "res": res, "transcript": tr, "cap": cap}

    # ── git safety, commits, parking
    def assert_safe(self) -> None:
        cur = git(self.root, "rev-parse", "--abbrev-ref", "HEAD").out.strip()
        if cur != self.branch:
            raise RuntimeError(f"SAFETY: on '{cur}', expected '{self.branch}'. Aborting run.")
        base = git(self.root, "rev-parse", self.base).out.strip()
        if base != self.main_sha:
            raise RuntimeError(f"SAFETY: {self.base} moved ({self.main_sha[:7]} -> {base[:7]}). Aborting run.")

    def commit_paths(self, cwd: Path, paths: list[str], message: str) -> bool:
        if not paths:
            return False
        clear_index_lock(cwd)
        msg = self.run_dir / f"commitmsg-{os.getpid()}-{threading.get_ident()}.txt"
        msg.write_text(message, encoding="utf-8")
        try:
            r = git(cwd, "add", "-A", "--", *paths)
            if r.code:
                raise RuntimeError(f"git add failed in {cwd}: {r.out}{r.err}")
            r = git(cwd, "commit", "--no-verify", "-F", str(msg), "--", *paths)
            if r.code:
                raise RuntimeError(f"git commit failed in {cwd}: {r.out}{r.err}")
        finally:
            msg.unlink(missing_ok=True)
        return True

    def ensure_sibling_worktree(self, sib: dict) -> None:
        """A sibling repo gets its own worktree too, cut from the branch it is on."""
        if sib["path"] in self.sib_ready:
            return
        wt, home = sib["root"], sib["home"]
        if wt.exists():
            head = git(wt, "rev-parse", "--abbrev-ref", "HEAD").out.strip()
            if head != self.branch:
                raise RuntimeError(f"{wt} exists but is on '{head}', not {self.branch}; remove it.")
        else:
            exists = git(home, "rev-parse", "--verify", "--quiet", f"refs/heads/{self.branch}").code == 0
            args = ["worktree", "add", str(wt), self.branch] if exists else \
                ["worktree", "add", "-b", self.branch, str(wt), self.sib_start[sib["path"]]]
            r = git(home, *args)
            if r.code:
                raise RuntimeError(f"could not create worktree {wt}: {r.out}{r.err}")
            self.log(f"{sib['path']}: worktree {wt} on {self.branch} (from {self.sib_start[sib['path']]})")
        self.sib_ready.add(sib["path"])

    def sibling_changes(self) -> dict[str, list[str]]:
        return {s["path"]: (changed_paths(s["root"], s["scope"] or None) if s["root"].exists() else [])
                for s in self.siblings}

    def park_failure(self, task: dict, reason: str) -> None:
        tid = task["id"]
        tag = f"overnight {self.date} {tid} failed"
        code = [p for p in changed_paths(self.root) if p not in self.todo_paths]
        if code:
            clear_index_lock(self.root)
            r = git(self.root, "stash", "push", "-u", "-m", tag, "--", *code)
            if r.code:
                raise RuntimeError(f"could not stash failed work for {tid}: {r.out}{r.err}")
        for s in self.siblings:
            z = changed_paths(s["root"], s["scope"] or None) if s["root"].exists() else []
            if z:
                clear_index_lock(s["root"])
                r = git(s["root"], "stash", "push", "-u", "-m", tag, "--", *z)
                if r.code:
                    raise RuntimeError(f"could not stash {s['path']} work for {tid}: {r.out}{r.err}")
        r = self.tk("reset", self.todo_arg, tid, "--owner", self.owner)
        if r.code:
            self.log(f"WARN tidetable reset {tid} failed: {r.out}{r.err}")
        todo = self.todo_changes()
        if todo:
            self.commit_paths(self.root, todo, f"chore(overnight): {tid} attempt failed, back to pending\n\n{reason}")
        self.log(f"FAILED {tid} - {reason} (work stashed as '{tag}' if any)")

    def commit_task(self, task: dict, spec: str, parsed: dict, kind: str, sib_changed: dict) -> None:
        tid = task["id"]
        subject = parsed["summary"] or task.get("title", tid)
        note = ("Closed by the overnight runner: gate commands passed." if kind == "done"
                else f"Left in progress: needs human check - {parsed['detail']}")
        for s in self.siblings:
            if sib_changed.get(s["path"]):
                self.ensure_sibling_worktree(s)
                self.commit_paths(s["root"], sib_changed[s["path"]],
                                  f"feat: {subject} ({tid})\n\nOvernight run {self.date}, model {spec}.")
        prefix = tid if kind == "done" else f"wip({tid})"
        self.commit_paths(self.root, changed_paths(self.root),
                          f"{prefix}: {subject}\n\n{note}\nOvernight run {self.date}, model {spec}.")

    # ── gate (the runner's own checks; agent claims are not trusted)
    def gate(self, tid: str, changed: list[str], sib_changed: dict) -> str:
        steps = []
        for s in self.cfg["gate"]:
            repo = s.get("repo", ".")
            root = self.root if repo == "." else next(
                (x["root"] for x in self.siblings if x["path"] == repo), None)
            if root is None:
                return f"gate step '{s.get('name')}' names unknown repo '{repo}'"
            files = changed if repo == "." else sib_changed.get(repo, [])
            wr = s.get("when_repo_changed")
            if wr and not (changed if wr == "." else sib_changed.get(wr, [])):
                continue
            if s.get("when_changed") and not any(matches(f, s["when_changed"]) for f in files):
                continue
            cmd = list(s["cmd"])
            if s.get("append_changed"):
                hit = [f for f in files if matches(f, [s["append_changed"]])
                       and not matches(f, s.get("exclude", [])) and (root / f).exists()]
                if not hit:
                    continue
                cmd += hit
            steps.append((s.get("name", cmd[0]), cmd, root / s.get("cwd", "."), s.get("fail_output_regex")))
        gate_log = self.run_dir / f"{tid}-gate.txt"
        for name, cmd, cwd, bad in steps:
            self.say(f"gate: {name}")
            r = run_proc(cmd, cwd, timeout=self.gate_sec, env=self.job_env or None)
            with open(gate_log, "a", encoding="utf-8") as f:
                f.write(f"== {name} exit {r.code}\n{r.out}\n{r.err}\n")
            if r.timed_out:
                return f"{name} timed out after {self.gate_sec // 60} min"
            if r.code == 0 and bad and re.search(bad, r.out + r.err):
                return f"{name} output matched /{bad}/; see {RUN_DIR_NAME}/{self.date}/{tid}-gate.txt"
            if r.code != 0:
                return f"{name} failed (exit {r.code}); see {RUN_DIR_NAME}/{self.date}/{tid}-gate.txt"
        return ""

    @staticmethod
    def parse_result(out: str) -> dict:
        m = re.findall(r"(?m)^\s*OVERNIGHT_RESULT:\s*(DONE|PARTIAL|FAILED)\b[ \t]*(.*)$", out)
        s = re.findall(r"(?m)^\s*OVERNIGHT_SUMMARY:\s*(.+)$", out)
        return {"status": m[-1][0] if m else "", "detail": m[-1][1].strip() if m else "",
                "summary": s[-1].strip() if s else ""}

    # ── one task end to end → done | partial | skipped | failed | limited | stop
    def run_task(self, task: dict) -> str:
        tid = task["id"]
        provider, model = self.lane_route(*self.resolve_agent(task))
        spec = f"{provider}/{model}"
        if provider not in ("anthropic", "opencode-go") and provider not in self.cfg["agent_commands"] \
                and provider not in self.cfg["lanes"]:
            self.log(f"SKIP {tid} - provider '{provider}' has no CLI here; not claimed")
            return "skipped"
        if provider in self.exhausted:
            self.log(f"SKIP {tid} - {provider} is out of quota until after the deadline; not claimed")
            return "skipped"
        r = self.tk("start", self.todo_arg, tid, "--owner", self.owner, "--lease", str(self.lease))
        if r.code:
            self.log(f"SKIP {tid} - claim failed: {r.out}{r.err}")
            return "skipped"
        self.log(f"START {tid} '{task.get('title', '')}' agent={spec}")

        run = self.run_agent(provider, model, lambda resume: self.build_prompt(task, spec, resume), tid)
        if run["status"] == "stop":
            self.park_failure(task, "stop requested while rate limited")
            return "stop"
        if run["status"] == "limited":
            self.exhausted.add(provider)
            self.park_failure(task, f"{provider} usage limit until after the deadline")
            return "limited"
        res = run["res"]
        if res.timed_out:
            self.park_failure(task, f"agent run timed out after {run['cap'] // 60} min")
            return "failed"
        parsed = self.parse_result(res.out)
        if not parsed["status"]:
            rel = run["transcript"].relative_to(self.home).as_posix()
            self.park_failure(task, f"no OVERNIGHT_RESULT line (exit {res.code}); see {rel}")
            return "failed"
        if parsed["status"] == "FAILED":
            self.park_failure(task, f"agent: {parsed['detail']}")
            return "failed"

        changed = [p for p in changed_paths(self.root) if p not in self.todo_paths]
        sib_changed = self.sibling_changes()
        if not changed and not any(sib_changed.values()):
            if parsed["status"] == "PARTIAL":
                self.tk("reset", self.todo_arg, tid, "--owner", self.owner)
                todo = self.todo_changes()
                if todo:
                    self.commit_paths(self.root, todo, f"chore(overnight): {tid} needs a human, nothing done")
                self.log(f"SKIPPED {tid} - needs human, no changes: {parsed['detail']}")
                return "skipped"
            self.park_failure(task, "agent reported DONE but changed nothing")
            return "failed"

        why = self.gate(tid, changed, sib_changed)
        if why:
            self.park_failure(task, f"gate: {why}")
            return "failed"

        missing = [p for p in self.cfg["required_changed"] if p not in changed]
        if parsed["status"] == "DONE" and missing:
            parsed.update(status="PARTIAL", detail=f"no change to {', '.join(missing)}")

        if parsed["status"] == "DONE" and self.review_enabled:
            rp, rm = self.split_agent(self.review_spec)
            if rp in self.exhausted:
                parsed.update(status="PARTIAL", detail="review not run (reviewer out of quota)")
            else:
                self.say(f"review: {rp}/{rm}")
                prompt = self.build_review_prompt(task, changed, sib_changed)
                rrun = self.run_agent(rp, rm, lambda _resume: prompt, f"{tid}-review", "review")
                hits = re.findall(r"(?m)^\s*REVIEW_RESULT:\s*(PASS|FAIL)\b[ \t]*(.*)$", rrun["res"].out)
                if rrun["status"] == "limited":
                    self.exhausted.add(rp)
                if rrun["status"] != "ok" or not hits:
                    rel = rrun["transcript"].relative_to(self.home).as_posix()
                    parsed.update(status="PARTIAL", detail=f"review not completed ({rrun['status']}); see {rel}")
                elif hits[-1][0] == "FAIL":
                    parsed.update(status="PARTIAL", detail=f"review failed: {hits[-1][1].strip()}")
                else:
                    self.log(f"REVIEW PASS {tid}")

        if parsed["status"] == "DONE":
            cl = task.get("changelog") or task.get("title", tid)
            r = self.tk("done", self.todo_arg, tid, "--owner", self.owner, "--changelog", cl)
            if r.code:
                parsed.update(status="PARTIAL", detail=f"tidetable done refused: {r.out}{r.err}")
        if parsed["status"] == "DONE":
            self.commit_task(task, spec, parsed, "done", sib_changed)
            self.log(f"DONE {tid} - {parsed['summary']}")
            return "done"
        self.commit_task(task, spec, parsed, "partial", sib_changed)
        self.log(f"PARTIAL {tid} - committed, left in_progress (lease {self.lease}m). Needs human: {parsed['detail']}")
        return "partial"

    # ── preflight, dry run, main loop
    def preflight(self) -> str:
        """Check the repo is safe to run in; returns the current branch."""
        if not shutil.which("git"):
            raise RuntimeError("git not in PATH")
        if self.hub:
            r = self.tk("list", self.todo_arg, "--json")
            if r.code:
                raise RuntimeError(f"hub {self.hub['server']} project '{self.todo_arg}' "
                                   f"not reachable: {(r.err or r.out).strip()[-300:]}")
        elif not self.todo.is_file():
            raise RuntimeError(f"todo file missing: {self.todo}")
        base = self.base
        r = git(self.root, "rev-parse", base)
        if r.code:
            raise RuntimeError(f"base branch '{base}' does not exist")
        self.main_sha = r.out.strip()
        return git(self.root, "rev-parse", "--abbrev-ref", "HEAD").out.strip()

    def show_dry_run(self, cur: str) -> int:
        gates = self.cfg["gate"]
        self.say("DRY RUN - nothing will be changed")
        self.say(f"repo {self.root} (on {cur}, {self.base} {self.main_sha[:7]})")
        self.say(f"would create worktree {self.wt_path} on {self.branch} from {self.base}; "
                 f"{self.home} is never edited")
        self.say(f"branch {self.branch}; owner {self.owner}; deadline {self.deadline:%Y-%m-%d %H:%M}; lease {self.lease}m")
        self.say(f"review: {self.review_spec if self.review_enabled else 'off'}")
        self.say(f"jobs: {self.job_count}"
                 + (" (a task runs alongside others only if its touches do not overlap)"
                    if self.job_count > 1 else ""))
        self.say(f"phase review: {self.phase_spec if self.phase_enabled else 'off'}"
                 + (f" (on FAIL: {self.phase_on_fail})" if self.phase_enabled else ""))
        if not gates:
            self.say("WARNING: no gate configured; run `tidetable overnight init` or tasks close on review alone")
        for s in gates:
            self.say(f"gate: {s.get('name')}: {' '.join(s['cmd'])}"
                     + (f" (repo {s['repo']})" if s.get("repo") else ""))
        r = self.tk("ready", self.todo_arg, "--json")
        ready = json.loads(r.out).get("ready", []) if r.out.strip().startswith("{") else []
        self.say("Routing for ready tasks (in order):")
        for q in ready:
            try:
                p, m = self.resolve_agent(q)
            except ValueError as e:
                self.say(f"  {q['id']:<8} {e}")
                continue
            lp, lm = self.lane_route(p, m)
            note = f"  (lane closed -> {lp}/{lm}: {self.lane_cache[p][1][1]})" if (lp, lm) != (p, m) else ""
            self.say(f"  {q['id']:<8} {q.get('agent', ''):<16} {p}/{m}{note}")
        t = next((q for q in ready if q.get("status") == "pending"), None)
        if not t:
            self.say("No pending ready task.")
            return 0
        p, m = self.resolve_agent(t)
        self.say(f"Next task: {t['id']} - {t.get('title', '')}  (human-check criteria: "
                 f"{len(self.human_check(t))} of {len(t.get('success', []))})")
        call = self.agent_call(p, m, "<PROMPT>")
        self.say("command: " + " ".join(shlex.quote(a) for a in call["argv"])
                 + ("   (prompt on stdin)" if call["stdin"] else ""))
        if call["opencode"]:
            print(f"----- opencode.json (written to {self.opencode_cfg} only while opencode runs) -----")
            print(call["opencode"])
        print("----- prompt -----")
        print(self.build_prompt(t, f"{p}/{m}", False))
        print("------------------")
        return 0

    def exclude_runtime_files(self) -> None:
        """Keep the run dir and generated opencode.json out of `git status`."""
        p = git(self.home, "rev-parse", "--git-path", "info/exclude").out.strip()
        path = Path(p) if Path(p).is_absolute() else self.home / p
        path.parent.mkdir(parents=True, exist_ok=True)
        have = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
        add = [x for x in (f"{RUN_DIR_NAME}/", "opencode.json") if x not in have]
        if add:
            with open(path, "a", encoding="utf-8") as f:
                f.write("\n".join(add) + "\n")

    def make_worktree(self) -> None:
        """Every run happens in its own worktree; the checkout you started from is never touched."""
        if self.wt_path.exists():
            head = git(self.wt_path, "rev-parse", "--abbrev-ref", "HEAD").out.strip()
            if head != self.branch:
                raise RuntimeError(f"{self.wt_path} exists but is on '{head}', not {self.branch}; remove it "
                                   "(`git worktree remove`) or set worktree_dir.")
            if git(self.wt_path, "status", "--porcelain", "--untracked-files=all").out.strip():
                raise RuntimeError(f"resume worktree {self.wt_path} is not clean; commit or stash there first.")
        else:
            exists = git(self.home, "rev-parse", "--verify", "--quiet", f"refs/heads/{self.branch}").code == 0
            args = ["worktree", "add", str(self.wt_path), self.branch] if exists else \
                ["worktree", "add", "-b", self.branch, str(self.wt_path), self.base]
            r = git(self.home, *args)
            if r.code:
                raise RuntimeError(f"could not create worktree {self.wt_path}: {r.out}{r.err}")
        self.root = self.wt_path
        if not self.hub:
            self.todo = self.wt_path / self.todo_rel
            self.todo_paths = [self.todo_rel, self.todo_rel + ".events.jsonl"]

    def halt_request(self) -> str | None:
        """Consume a STOP/KILL sentinel if present; None otherwise.

        KILL is the hard stop: `tkrun kill` also tree-kills the runner, so
        reaching here with KILL set means the signal missed and the run must
        halt at the next check anyway.
        """
        for path, why in ((self.kill_file, "KILL file"), (self.stop_file, "STOP file")):
            if path.exists():
                path.unlink()
                return why
        return None

    # ── parallel jobs
    def next_compatible(self, skip: list[str], running: list[dict]) -> dict | None:
        """The next ready task that may start next to `running`, or None.

        A task with no `touches` is exclusive: it waits for the running ones to drain
        (and nothing starts behind it, so it cannot starve).
        """
        r = self.tk("ready", self.todo_arg, "--json")
        try:
            ready = json.loads(r.out).get("ready", [])
        except json.JSONDecodeError:
            raise RuntimeError(f"tidetable ready failed: {r.out}{r.err}")
        busy = [p for t in running for p in (t.get("touches") or [])]
        for t in ready:
            if t.get("status") != "pending" or t["id"] in skip:
                continue
            tt = t.get("touches") or []
            if not running:
                return t
            if not tt:
                return None
            if not touches_overlap(tt, busy):
                return t
        return None

    def make_job(self, task: dict) -> "Runner":
        """A runner for one task: own worktree and branch cut from the run branch."""
        tid = task["id"]
        branch = f"{self.branch}-{tid}"
        path = self.wt_path.parent / f"{self.wt_path.name}-{tid}"
        if path.exists():
            self.log(f"WARN removing leftover job worktree {path}")
            git(self.home, "worktree", "remove", "--force", str(path))
        r = git(self.home, "worktree", "add", "-B", branch, str(path), self.branch)
        if r.code:
            raise RuntimeError(f"could not create job worktree {path}: {r.out}{r.err}")
        job = copy.copy(self)
        job.root = path
        job.branch = branch
        job.follow_prefix = f"[{tid}] "
        return job

    def integrate(self, task: dict, job: "Runner", outcome: str) -> str:
        """Merge a finished job back onto the run branch and drop its worktree.

        Serialised. A merge conflict leaves the job branch and worktree in place for you,
        puts the task back to pending, and counts as a failure.
        """
        tid, keep = task["id"], False
        with self.merge_lock:
            if outcome in ("done", "partial"):
                r = git(self.root, "merge", "--no-ff", "-m", f"merge: {tid} ({outcome})", job.branch)
                if r.code:
                    git(self.root, "merge", "--abort")
                    self.log(f"MERGE CONFLICT {tid}: kept {job.branch} at {job.root}; "
                             f"task back to pending ({(r.out + r.err).strip()[-200:]})")
                    q = self.tk("reset", self.todo_arg, tid, "--owner", self.owner)
                    if q.code:
                        self.log(f"WARN could not reopen {tid}: {q.out}{q.err}")
                    outcome, keep = "failed", True
            self.commit_paths(self.root, self.todo_changes(),
                              f"chore(overnight): todo state after {tid} ({outcome})")
        if not keep:
            git(self.home, "worktree", "remove", "--force", str(job.root))
            git(self.home, "branch", "-D", job.branch)
        return outcome

    def run_parallel(self, counts: dict, tried: list[str]) -> str:
        """The main loop with up to `job_count` tasks in flight. Returns why it ended."""
        inflight: dict = {}
        consecutive, stopping, why = 0, "", "no more ready tasks"
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.job_count) as pool:
            while True:
                if not stopping:
                    halted = self.halt_request()
                    if halted:
                        stopping = halted
                    elif datetime.now() >= self.deadline:
                        stopping = "deadline"
                    elif consecutive >= self.max_fail:
                        stopping = f"{consecutive} consecutive failures"
                while not stopping and len(inflight) < self.job_count:
                    t = self.next_compatible(tried, [x[0] for x in inflight.values()])
                    if not t:
                        break
                    tried.append(t["id"])
                    job = self.make_job(t)
                    inflight[pool.submit(job.run_task, t)] = (t, job)
                if not inflight:
                    break
                finished, _ = concurrent.futures.wait(
                    list(inflight), timeout=5, return_when=concurrent.futures.FIRST_COMPLETED)
                for fut in finished:
                    t, job = inflight.pop(fut)
                    try:
                        outcome = fut.result()
                    except Exception as e:  # noqa: BLE001 - one job must not end the others
                        self.log(f"ERROR {t['id']}: {e}")
                        outcome = "failed"
                    if outcome == "stop":
                        stopping = stopping or "STOP while waiting out a usage limit"
                        outcome = "failed"
                    outcome = self.integrate(t, job, outcome)
                    counts[outcome] += 1
                    if outcome == "done" and self.phase_enabled:
                        if self.review_phase(t["id"]) == "FAIL" and self.phase_on_fail == "stop":
                            stopping = stopping or "phase review FAIL"
                    if outcome == "failed":
                        consecutive += 1
                    elif outcome in ("done", "partial"):
                        consecutive = 0
        return stopping or why

    def run_sequential(self, counts: dict, tried: list[str]) -> str:
        """The main loop, one task at a time. Returns why it ended."""
        consecutive = 0
        while True:
            halted = self.halt_request()
            if halted:
                return halted
            if datetime.now() >= self.deadline:
                return "deadline"
            t = self.next_task(tried)
            if not t:
                return "no more ready tasks"
            tried.append(t["id"])
            outcome = self.run_task(t)
            if outcome == "stop":
                return "STOP while waiting out a usage limit"
            counts[outcome] += 1
            if outcome == "done" and self.phase_enabled:
                if self.review_phase(t["id"]) == "FAIL" and self.phase_on_fail == "stop":
                    return "phase review FAIL"
            if outcome == "failed":
                consecutive += 1
            elif outcome in ("done", "partial"):
                consecutive = 0
            if consecutive >= self.max_fail:
                return f"{consecutive} consecutive failures"

    def execute(self) -> int:
        cur = self.preflight()
        if self.dry_run:
            return self.show_dry_run(cur)
        if not self.cfg["gate"] and not self.review_enabled and not self.phase_enabled:
            raise RuntimeError("no gate and no review configured: nothing would verify a task. "
                               "Run `tidetable overnight init` or enable review.")
        if self.pid_file.exists():
            old = self.pid_file.read_text().strip()
            if old.isdigit() and jsonio._pid_alive(int(old)):
                raise RuntimeError(f"already running (pid {old}, {self.pid_file})")
        if self.job_count > 1 and self.cfg["siblings"]:
            raise RuntimeError("--jobs > 1 is not supported together with `siblings` yet")
        self.exclude_runtime_files()
        base = self.base
        if not self.hub and git(self.home, "diff", "--quiet", "HEAD", "--", self.todo_rel).code != 0:
            raise RuntimeError(f"{self.todo_rel} has uncommitted changes; commit them first "
                               "(the run works from the committed copy in its worktree).")
        for sib in self.siblings:
            start = git(sib["home"], "rev-parse", "--abbrev-ref", "HEAD").out.strip()
            self.sib_start[sib["path"]] = start
            if git(sib["home"], "status", "--porcelain", "--untracked-files=no", "--ignore-submodules=all").out.strip():
                self.say(f"WARN {sib['path']} has uncommitted changes; the worktree is cut from committed {start}")
        self.make_worktree()
        for sib in self.siblings:
            self.ensure_sibling_worktree(sib)
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.pid_file.write_text(str(os.getpid()), encoding="ascii")
        self.log_enabled = True
        if not self.log_file.exists():
            self.log_file.write_text("# Overnight runner log (append-only)\n", encoding="utf-8")
        with open(self.log_file, "a", encoding="utf-8") as f:
            f.write(f"\n## {self.date} run (pid {os.getpid()})\n\n")
        self.log(f"worktree {self.wt_path} on {self.branch}; deadline {self.deadline:%H:%M}; "
                 f"stop: tidetable overnight stop "
                 + (f"--repo {self.home} {self.todo_arg}" if self.hub else str(self.home / self.todo_rel)))

        counts = dict.fromkeys(("done", "partial", "skipped", "failed", "limited"), 0)
        tried: list[str] = []
        why = "no more ready tasks"
        try:
            why = self.run_parallel(counts, tried) if self.job_count > 1                 else self.run_sequential(counts, tried)
        except KeyboardInterrupt:
            why = "interrupted (Ctrl-C)"
            self.log("ABORT interrupted; the task in flight keeps its claim and uncommitted work "
                     f"in {self.wt_path}")
        except Exception as e:  # noqa: BLE001 - the log must record why the night ended
            why = f"error: {e}"
            self.log(f"ABORT {why}")
        finally:
            for sib in self.siblings:
                if sib["path"] in self.sib_ready:
                    self.log(f"{sib['path']}: worktree kept at {sib['root']} (branch {self.branch})")
            self.log(f"worktree kept at {self.wt_path}; review `git log {self.base}..{self.branch}`, merge it "
                     f"yourself, then `git worktree remove {self.wt_path}`")
            self.log(f"END ({why}): done {counts['done']}, partial {counts['partial']}, skipped "
                     f"{counts['skipped']}, failed {counts['failed']}, quota-limited {counts['limited']}")
            self.pid_file.unlink(missing_ok=True)
        return 0 if why in ("no more ready tasks", "deadline", "STOP file", "KILL file") else 1


# ── CLI ──────────────────────────────────────────────────────────────────────

def hub_for(args) -> dict | None:
    """The hub to run against, or None for a local todo file.

    Hub mode = a server is configured (`--server` / `$TIDETABLE_SERVER`) and
    `--local` is absent; `todo_file` is then the project slug.
    """
    from tidetable import remote

    if getattr(args, "local", False):
        return None
    server = remote.server_url(args)
    if not server:
        return None
    return {"server": server, "token": remote.token(args), "project": args.todo_file}


def resolve_target(args) -> tuple[Path, Path, dict | None]:
    """(todo path, repo root, hub) for run/init/stop/kill."""
    hub = hub_for(args)
    if hub is None:
        todo = Path(args.todo_file).resolve()
        return todo, repo_root_for(todo), None
    repo = Path(getattr(args, "repo", None) or ".").resolve()
    # repo_root_for walks up from a file's parent; a placeholder child gives it one.
    return Path(args.todo_file), repo_root_for(repo / "_"), hub


def cmd_run(args) -> int:
    todo, root, hub = resolve_target(args)
    cfg, path = load_config(root)
    hours, minutes = args.hours, args.minutes
    if minutes and args.hours is None:
        hours = 0
    elif hours is None:
        hours = 8
    runner = Runner(todo=todo, root=root, cfg=cfg, date=datetime.now().strftime("%Y-%m-%d"),
                    deadline=datetime.now() + timedelta(hours=hours, minutes=minutes),
                    model_override=args.model or "", no_review=args.no_review,
                    review_model=args.review_model or "", dry_run=args.dry_run,
                    phase_review_model=args.phase_review_model or "", jobs=args.jobs or 0,
                    no_phase_review=args.no_phase_review,
                    follow=sys.stdout.isatty() if args.follow is None else args.follow,
                    base_override=args.base or "", hub=hub)
    if path is None:
        runner.say("no .tidetable/overnight.json; using detected stack defaults "
                   "(`tidetable overnight init` writes one)")
    try:
        return runner.execute()
    except RuntimeError as e:
        print(f"Error: {e}")
        return 1


def cmd_init(args) -> int:
    _, root, _ = resolve_target(args)
    path = config_path(root)
    if path.exists() and not args.force:
        print(f"Error: {path} exists (use --force to overwrite)")
        return 1
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(starter_config(root), indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {path}")
    print("Edit it: add rules, deny_edit globs, required_changed (e.g. CHANGELOG.md), siblings.")
    return 0


def cmd_stop(args) -> int:
    _, root, _ = resolve_target(args)
    stop = root / RUN_DIR_NAME / "STOP"
    stop.parent.mkdir(parents=True, exist_ok=True)
    stop.write_text("stop\n", encoding="utf-8")
    print(f"STOP file written: {stop} (the runner stops after the current task)")
    return 0


def _tree_kill(pid: int) -> bool:
    """Kill a runner and its agent children. True if a signal was delivered."""
    if os.name == "nt":
        r = subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                           capture_output=True, text=True)
        return r.returncode == 0
    try:
        os.killpg(os.getpgid(pid), 9)
        return True
    except (OSError, ProcessLookupError):
        pass
    try:
        os.kill(pid, 9)
        return True
    except (OSError, ProcessLookupError):
        return False


def cmd_kill(args) -> int:
    """Hard stop: sentinel + tree-kill the runner PID.

    Unlike `stop` (cooperative, checked between tasks), this delivers a real
    signal so a mid-task agent subprocess dies too. The KILL sentinel is also
    written so a runner that survives the signal still halts at its next check
    instead of claiming more work. Task claims are left alone: their leases
    expire on their own and the partial work stays in the run worktree.
    """
    from tidetable.jsonio import _pid_alive  # local import: supervisor-side only

    _, root, _ = resolve_target(args)
    rundir = root / RUN_DIR_NAME
    rundir.mkdir(parents=True, exist_ok=True)
    (rundir / "KILL").write_text("kill\n", encoding="utf-8")
    pid_file = rundir / "pid"
    if not pid_file.exists():
        print(f"KILL sentinel written: {rundir / 'KILL'} (no runner pid file; nothing to signal)")
        return 0
    raw = pid_file.read_text(encoding="ascii").strip()
    if not raw.isdigit():
        pid_file.unlink(missing_ok=True)
        print(f"KILL sentinel written; removed unreadable pid file ({raw!r})")
        return 0
    pid = int(raw)
    if _pid_alive(pid) is False:
        pid_file.unlink(missing_ok=True)
        print(f"KILL sentinel written; runner pid {pid} already dead (stale pid file removed)")
        return 0
    if _tree_kill(pid):
        print(f"KILL sentinel written; signal delivered to runner pid {pid} (tree)")
        return 0
    print(f"KILL sentinel written; could not signal runner pid {pid} "
          f"(unknown state) — it will halt at its next check")
    return 1


def add_parser(sub) -> None:
    """`tidetable tkrun run|init|stop|kill <todo.json>` (`overnight` kept as an alias)."""
    p = sub.add_parser("tkrun", aliases=["overnight"], help="TKRun: task runner on a throwaway branch")
    inner = p.add_subparsers(dest="overnight_command", required=True)

    run = inner.add_parser("run", help="Work through ready tasks until done, stopped or out of time")
    run.add_argument("todo_file", help="Path to todo JSON file (project slug with --server)")
    run.add_argument("--hours", type=float, default=None, help="Time budget (default 8; 0 with --minutes)")
    run.add_argument("--minutes", type=float, default=0, help="Added to --hours (alone = minutes only)")
    run.add_argument("--dry-run", action="store_true", help="Show routing, command and prompt; change nothing")
    run.add_argument("--model", help="Force provider/model for every task")
    run.add_argument("--review-model", "--task-review-model", dest="review_model",
                     help="Override the per-task reviewer (provider/model)")
    run.add_argument("--phase-review-model", help="Review each phase once when it completes, "
                                                  "with this reviewer (provider/model)")
    run.add_argument("--follow", action=argparse.BooleanOptionalAction, default=None,
                     help="Echo the agent's output to the console while it runs "
                          "(default: on when attached to a terminal)")
    run.add_argument("--jobs", type=int, default=0, metavar="N",
                     help="Run up to N tasks at once, each in its own worktree "
                          "(default: config parallel.jobs, 1)")
    run.add_argument("--no-phase-review", action="store_true",
                     help="Skip the phase review even if the config enables it")
    run.add_argument("--base", help="Branch the worktree is cut from (default: base_branch, main)")
    run.add_argument("--no-review", "--no-task-review", dest="no_review", action="store_true",
                     help="Skip the per-task review: close tasks on the gate alone")
    run.add_argument("--repo", metavar="PATH",
                     help="With a hub (--server): the git checkout to work in (default: cwd)")
    run.set_defaults(func=cmd_run)

    init = inner.add_parser("init", help="Write a starter .tidetable/overnight.json for this repo")
    init.add_argument("todo_file", help="Path to todo JSON file (locates the repo)")
    init.add_argument("--force", action="store_true", help="Overwrite an existing config")
    init.add_argument("--repo", metavar="PATH",
                      help="With a hub (--server): the git checkout to work in (default: cwd)")
    init.set_defaults(func=cmd_init)

    stop = inner.add_parser("stop", help="Ask a running TKRun to stop after the current task")
    stop.add_argument("todo_file", help="Path to todo JSON file (locates the repo)")
    stop.add_argument("--repo", metavar="PATH",
                      help="With a hub (--server): the git checkout to work in (default: cwd)")
    stop.set_defaults(func=cmd_stop)

    kill = inner.add_parser("kill", help="Hard-stop a running TKRun: signal its process tree now")
    kill.add_argument("todo_file", help="Path to todo JSON file (locates the repo)")
    kill.add_argument("--repo", metavar="PATH",
                      help="With a hub (--server): the git checkout to work in (default: cwd)")
    kill.set_defaults(func=cmd_kill)

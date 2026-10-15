#!/usr/bin/env python3
"""TideTable adjudicator bridge for Claude Code (a `PreToolUse` hook).

TKRun runs `claude -p` unattended, so nobody can answer a permission prompt. For a Bash
command on neither the allow nor the deny list, the supervisor's decision model (clef)
settles it. Claude Code owns its shell, so this hook asks the supervisor's loopback
service, exactly as `integrations/opencode/tk-adjudicator.ts` does for opencode, and
answers Claude Code with an explicit `allow` or `deny`. It never answers `ask`, so the
agent is never left waiting on a person.

Contract (all the logic lives supervisor-side; this file only asks):
    stdin   Claude Code's hook event JSON (`tool_name`, `tool_input.command`, `cwd`)
    socket  one line of JSON over loopback TCP: {"command": "...", "agent": "claude"} + "\\n"
            -> {"decision": "allow"|"deny", "confidence": number, "reason": string}
    stdout  {"hookSpecificOutput": {"hookEventName": "PreToolUse",
             "permissionDecision": "allow"|"deny", "permissionDecisionReason": "..."}}

Fail-closed: no service address, a socket error, a timeout, an unparseable verdict or any
non-`allow` answer is a deny. Only `Bash` is judged; every other tool is left to the
`--tools`/`--allowedTools`/`--disallowedTools` lists the runner already passes.

Stdlib only and standalone (no `tidetable` import), so it runs under any interpreter the
runner hands it, from any worktree. The service address comes from the environment the
runner sets (`TIDETABLE_ADJUDICATOR_HOST`/`_PORT`) or, failing that, the discovery file
`.tkrun/adjudicator.json` found by walking up from the hook's working directory. The
decision model's endpoint and key never reach this process.
"""

from __future__ import annotations

import json
import os
import socket
import sys
from pathlib import Path

TIMEOUT_SEC = 20
DISCOVERY = (Path(".tkrun") / "adjudicator.json", Path(".overnight") / "adjudicator.json")


def discover(start: str) -> tuple[str, int] | None:
    host = os.environ.get("TIDETABLE_ADJUDICATOR_HOST", "")
    try:
        port = int(os.environ.get("TIDETABLE_ADJUDICATOR_PORT", "") or 0)
    except ValueError:
        port = 0
    if host and port > 0:
        return host, port
    here = Path(start or ".").resolve()
    for d in (here, *here.parents):
        for rel in DISCOVERY:
            try:
                found = json.loads((d / rel).read_text(encoding="utf-8"))
                if isinstance(found.get("host"), str) and isinstance(found.get("port"), int):
                    return found["host"], found["port"]
            except (OSError, ValueError, AttributeError):
                continue
    return None


def ask(addr: tuple[str, int], command: str) -> dict:
    try:
        with socket.create_connection(addr, timeout=TIMEOUT_SEC) as sock:
            sock.settimeout(TIMEOUT_SEC)
            sock.sendall((json.dumps({"command": command, "agent": "claude"}) + "\n").encode("utf-8"))
            data = b""
            while not data.endswith(b"\n"):
                chunk = sock.recv(65536)
                if not chunk:
                    break
                data += chunk
        verdict = json.loads(data.decode("utf-8"))
        return verdict if isinstance(verdict, dict) else {"decision": "deny", "reason": "unparseable verdict"}
    except (OSError, ValueError) as e:
        return {"decision": "deny", "reason": f"adjudicator unreachable: {e}"}


def emit(decision: str, reason: str) -> None:
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": decision,
        "permissionDecisionReason": reason}}))


def main() -> int:
    try:
        event = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        emit("deny", "TideTable adjudicator: unreadable hook event")
        return 0
    if event.get("tool_name") != "Bash":
        return 0                                    # not ours: the static lists decide
    command = (event.get("tool_input") or {}).get("command")
    if not isinstance(command, str) or not command.strip():
        return 0
    addr = discover(str(event.get("cwd") or ""))
    if addr is None:
        emit("deny", "TideTable adjudicator: no service address (fail-closed)")
        return 0
    verdict = ask(addr, command)
    if verdict.get("decision") == "allow":
        why = verdict.get("reason") or "cleared by the TideTable adjudicator"
        emit("allow", str(why))
    else:
        why = verdict.get("reason") or "not cleared"
        emit("deny", f"TideTable adjudicator denied this command ({why}): {command}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

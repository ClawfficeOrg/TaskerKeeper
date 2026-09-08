#!/usr/bin/env bash
# ralph-json.sh — thin wrapper around the taskerkeeper CLI.
#
# This used to be a second, jq-based implementation of the same DAG logic. Two
# implementations drift: they disagreed on task ordering and on whether unmet
# prerequisites block a `done`. There is now one implementation, in Python, and
# this script just forwards to it. No jq required.
#
# Usage:
#   ./ralph-json.sh next <todo.json> [--json] [--no-resume]
#   ./ralph-json.sh ready <todo.json> [--json]
#   ./ralph-json.sh start <todo.json> <task_id>
#   ./ralph-json.sh done <todo.json> <task_id> [--force] [--changelog TEXT]
#   ./ralph-json.sh reset <todo.json> <task_id>
#   ./ralph-json.sh list <todo.json> [--json]
#   ./ralph-json.sh parallel <todo.json> [--json]
#   ./ralph-json.sh deps <todo.json> <task_id> [--json]
#   ./ralph-json.sh validate <todo.json>
#
# Run `taskerkeeper --help` for the full command set.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

if command -v taskerkeeper >/dev/null 2>&1; then
    exec taskerkeeper "$@"
fi

for py in python3 python; do
    if command -v "$py" >/dev/null 2>&1; then
        PYTHONPATH="$REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}" exec "$py" -m taskerkeeper "$@"
    fi
done

echo "Error: neither the 'taskerkeeper' command nor python3 is available." >&2
echo "Install with: pip install -e $REPO_ROOT" >&2
exit 1

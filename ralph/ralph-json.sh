#!/usr/bin/env bash
# ralph-json.sh — JSON-aware ralph variant for TaskerKeeper
# Reads a JSON todo file and finds/manages tasks.
#
# Usage:
#   ./ralph-json.sh next <todo.json>           — Find next task (all deps satisfied)
#   ./ralph-json.sh done <todo.json> <task_id> — Mark task as done
#   ./ralph-json.sh list <todo.json>           — List all tasks with status
#   ./ralph-json.sh parallel <todo.json>       — Show parallel groups
#   ./ralph-json.sh validate <todo.json>       — Validate JSON against schema
#   ./ralph-json.sh deps <todo.json> <task_id> — Show dependency chain
#
# Dependencies: jq (required), python3 (for schema validation only)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCHEMA_DIR="$SCRIPT_DIR/../schema"

usage() {
    echo "Usage: $0 <command> <todo.json> [args]"
    echo ""
    echo "Commands:"
    echo "  next <todo.json>           Find next task with all deps satisfied"
    echo "  done <todo.json> <task_id> Mark task as done"
    echo "  list <todo.json>           List all tasks with status"
    echo "  parallel <todo.json>       Show parallel groups"
    echo "  validate <todo.json>       Validate against schema"
    echo "  deps <todo.json> <task_id> Show dependency chain"
    exit 1
}

[[ $# -lt 2 ]] && usage

COMMAND="$1"
TODO_FILE="$2"
shift 2

# Verify file exists
[[ ! -f "$TODO_FILE" ]] && echo "Error: $TODO_FILE not found" && exit 1

# Check jq is available
command -v jq >/dev/null 2>&1 || { echo "Error: jq is required but not installed"; exit 1; }

cmd_next() {
    # Find tasks where status=pending AND all prerequisites are done
    # Step 1: Collect all done task IDs
    local done_ids
    done_ids=$(jq -r '[.phases[].tasks[] | select(.status == "done") | .id] | .[]' "$TODO_FILE")
    
    # Step 2: Find pending tasks where all prereqs are in done_ids
    local next_task
    next_task=$(jq -r --argjson done_ids "$(echo "$done_ids" | jq -R . | jq -s .)" '
        [.phases[].tasks[] | select(.status == "pending") | select(
            # Check if all prerequisites are in done_ids
            (.prerequisites // []) | all(. as $id | $done_ids | index($id) != null)
        ) | select(.prerequisites | length > 0 or . == null or . == [])] |
        # If no deps, just pick the first pending task
        if length == 0 then
            [.phases[].tasks[] | select(.status == "pending")][0]
        else
            .[0]
        end
    ' "$TODO_FILE" 2>/dev/null)
    
    if [[ -z "$next_task" || "$next_task" == "null" ]]; then
        echo "No pending tasks with satisfied prerequisites."
        echo ""
        echo "Remaining tasks:"
        jq -r '.phases[].tasks[] | select(.status == "pending") | "  \(.id) — \(.title) [deps: \(.prerequisites // [] | join(", ") // "none")]"' "$TODO_FILE"
        return 1
    fi
    
    # Display the task
    echo "$next_task" | jq -r '
        "═══════════════════════════════════════════",
        "Task: \(.id) — \(.title)",
        "═══════════════════════════════════════════",
        "",
        "Goal:",
        .goal,
        "",
        "Complexity: \(.complexity // "unset") | Agent: \(.agent // "unset")",
        "",
        "Owned paths:",
        (.touches // [] | map("  • " + .) | join("\n")),
        "",
        "Success criteria:",
        (.success // [] | map("  ✓ " + .) | join("\n")),
        "",
        "Tests: \(.tests // "See goal")"
    '
}

cmd_done() {
    local task_id="$1"
    [[ -z "$task_id" ]] && echo "Error: task ID required" && exit 1
    
    # Verify task exists
    local exists
    exists=$(jq -r --arg id "$task_id" '
        [.phases[].tasks[] | select(.id == $id)] | length
    ' "$TODO_FILE")
    
    [[ "$exists" == "0" ]] && echo "Error: task $task_id not found" && exit 1
    
    # Get current status
    local current_status
    current_status=$(jq -r --arg id "$task_id" '
        [.phases[].tasks[] | select(.id == $id)][0].status
    ' "$TODO_FILE")
    
    [[ "$current_status" == "done" ]] && echo "Task $task_id is already done." && return 0
    
    # Check if all prerequisites are met
    local unmet
    unmet=$(jq -r --arg id "$task_id" '
        . as $root |
        [$root.phases[].tasks[] | select(.id == $id)][0] |
        .prerequisites // [] |
        map(select(. as $dep |
            [$root.phases[].tasks[] | select(.id == $dep and .status == "done")] | length == 0
        )) |
        join(", ")
    ' "$TODO_FILE")
    
    if [[ -n "$unmet" ]]; then
        echo "Warning: unmet prerequisites: $unmet"
        echo "Marking anyway (manual override). Use 'next' to see proper order."
    fi
    
    # Mark done
    jq --arg id "$task_id" '
        .phases[].tasks[] |= (select(.id == $id) | .status = "done" | .updated_at = (now | todate))
    ' "$TODO_FILE" > "${TODO_FILE}.tmp" && mv "${TODO_FILE}.tmp" "$TODO_FILE"
    
    echo "✓ Task $task_id marked as done."
    
    # Check if this unblocks any other tasks
    local unblocked
    unblocked=$(jq -r --arg id "$task_id" '
        . as $root |
        [$root.phases[].tasks[] | select(
            .status == "pending" and
            (.prerequisites // [] | index($id) != null) and
            (.prerequisites // [] | all(. as $dep |
                [$root.phases[].tasks[] | select(.id == $dep and .status == "done")] | length > 0
            ))
        ) | .id]
    ' "$TODO_FILE")
    
    if [[ -n "$unblocked" ]]; then
        echo ""
        echo "Unblocked tasks:"
        echo "$unblocked" | while read -r tid; do
            local title
            title=$(jq -r --arg id "$tid" '[.phases[].tasks[] | select(.id == $id)][0].title' "$TODO_FILE")
            echo "  → $tid — $title"
        done
    fi
}

cmd_list() {
    echo "Task Status Overview"
    echo "═══════════════════════════════════════════"
    jq -r '
        .phases[] |
        "\nPhase \(.id): \(.title)",
        "───────────────────────────────────────────",
        (.tasks[] |
            "  [\(.status | if . == "done" then "✓" elif . == "in_progress" then "►" elif . == "cancelled" then "✗" elif . == "moved" then "→" else "·" end)] \(.id) — \(.title)"
        )
    ' "$TODO_FILE"
    
    # Summary
    echo ""
    echo "Summary:"
    jq -r '
        [.phases[].tasks[]] |
        "  Total: \(length)",
        "  Done: \(map(select(.status == "done")) | length)",
        "  In Progress: \(map(select(.status == "in_progress")) | length)",
        "  Pending: \(map(select(.status == "pending")) | length)",
        "  Cancelled: \(map(select(.status == "cancelled")) | length)"
    ' "$TODO_FILE"
}

cmd_parallel() {
    echo "Parallel Groups"
    echo "═══════════════════════════════════════════"
    jq -r '
        [.phases[].tasks[] | select(.parallel_group != null)] |
        group_by(.parallel_group) |
        .[] |
        "\nGroup: \(.[0].parallel_group)",
        "───────────────────────────────────────────",
        (.[] |
            "  \(.id) — \(.title) [\(.status)]"
        ),
        "  → All tasks in this group can run in parallel when deps are met"
    ' "$TODO_FILE"
}

cmd_validate() {
    local schema_file="$SCHEMA_DIR/todo-v1.schema.json"
    if [[ ! -f "$schema_file" ]]; then
        echo "Error: Schema not found at $schema_file"
        exit 1
    fi
    
    # Use python3 for JSON Schema validation
    python3 -c "
import json, sys
try:
    from jsonschema import validate, ValidationError
    with open('$TODO_FILE') as f:
        data = json.load(f)
    with open('$schema_file') as f:
        schema = json.load(f)
    validate(instance=data, schema=schema)
    print('✓ Valid: $TODO_FILE matches the schema')
except ImportError:
    print('jsonschema not installed. Install with: pip install jsonschema')
    print('Falling back to basic JSON parse check...')
    with open('$TODO_FILE') as f:
        json.load(f)
    print('✓ Valid JSON (schema validation skipped)')
except ValidationError as e:
    print(f'✗ Invalid: {e.message}')
    print(f'  Path: {list(e.absolute_path)}')
    sys.exit(1)
except json.JSONDecodeError as e:
    print(f'✗ Invalid JSON: {e}')
    sys.exit(1)
"
}

cmd_deps() {
    local task_id="$1"
    [[ -z "$task_id" ]] && echo "Error: task ID required" && exit 1
    
    echo "Dependency chain for $task_id"
    echo "═══════════════════════════════════════════"
    
    # Get task info
    local task_info
    task_info=$(jq -r --arg id "$task_id" '
        [.phases[].tasks[] | select(.id == $id)][0]
    ' "$TODO_FILE")
    
    if [[ -z "$task_info" || "$task_info" == "null" ]]; then
        echo "Error: task $task_id not found"
        exit 1
    fi
    
    echo "$task_info" | jq -r '"Task: \(.id) — \(.title)\nStatus: \(.status)"'
    echo ""
    echo "Prerequisites:"
    echo "$task_info" | jq -r '
        .prerequisites // [] |
        if length == 0 then "  (none)"
        else
            .[] |
            "  • \(.)"
        end
    '
    echo ""
    echo "Tasks this unblocks:"
    jq -r --arg id "$task_id" '
        [.phases[].tasks[] | select(
            (.prerequisites // []) | index($id) != null
        ) | "\(.id) — \(.title) [\(.status)]"] |
        if length == 0 then "  (none)"
        else
            .[] |
            "  • \(.)"
        end
    ' "$TODO_FILE"
}

case "$COMMAND" in
    next)     cmd_next ;;
    done)     cmd_done "$@" ;;
    list)     cmd_list ;;
    parallel) cmd_parallel ;;
    validate) cmd_validate ;;
    deps)     cmd_deps "$@" ;;
    *)        usage ;;
esac

# TaskerKeeper Integration Guide

## Standalone Usage

TaskerKeeper works as a standalone tool. No frameworks or platforms required.

### Quick Start

```bash
# Clone the repo
git clone https://github.com/ClawfficeOrg/TaskerKeeper.git
cd TaskerKeeper

# Validate a todo file
python3 scripts/taskerkeeper.py validate my-todo.json

# Find the next task to work on
python3 scripts/taskerkeeper.py next my-todo.json

# Mark a task as done
python3 scripts/taskerkeeper.py done my-todo.json 7.0.1

# Show all tasks
python3 scripts/taskerkeeper.py list my-todo.json

# Show statistics
python3 scripts/taskerkeeper.py status my-todo.json
```

### Using ralph-json.sh

The bash script variant provides the same functionality with `jq`:

```bash
./ralph/ralph-json.sh next my-todo.json
./ralph/ralph-json.sh done my-todo.json 7.0.1
./ralph/ralph-json.sh list my-todo.json
./ralph/ralph-json.sh parallel my-todo.json
./ralph/ralph-json.sh status my-todo.json
```

## Integration with ZoidMatter

TaskerKeeper is designed to work as a built-in component for ZoidMatter projects.
When a project uses ZoidMatter, TaskerKeeper provides the structured task format
that ralph consumes.

### Setup

1. **Add the JSON schema** to your project:
   ```bash
   cp schema/todo-v1.schema.json your-project/schema/
   ```

2. **Create your first todo file**:
   ```bash
   cp examples/simple-project.json your-project/docs/todo-v1.json
   ```

3. **Install the Hermes skill** (optional, for AI-assisted task management):
   ```bash
   cp -r skills/taskerkeeper ~/.hermes/skills/
   ```

4. **Configure ralph** to use the JSON variant:
   ```bash
   # In your AGENTS.md or ralph config:
   # Use ralph-json.sh instead of ralph.sh for JSON todo files
   ```

### Migration from Markdown

If you have existing `docs/todo-v*.md` files:

```bash
# Convert markdown to JSON
python3 scripts/taskerkeeper.py convert-md docs/todo-v7.md

# Verify the result
python3 scripts/taskerkeeper.py validate docs/todo-v7.json

# Compare side by side
diff <(python3 scripts/taskerkeeper.py convert-json docs/todo-v7.json) docs/todo-v7.md
```

### File Layout

A typical ZoidMatter project with TaskerKeeper:

```
your-project/
├── schema/
│   └── todo-v1.schema.json
├── docs/
│   ├── todo-v7.json              # Current roadmap (JSON)
│   ├── todo-v7.md                # Human-readable version (optional)
│   ├── plan.md                   # Architecture overview
│   ├── memory.md                 # Session log
│   └── learnings.md              # Technical discoveries
├── scripts/
│   └── ralph-json.sh             # JSON-aware ralph variant
├── AGENTS.md                     # Agent instructions
└── skills/
    └── taskerkeeper/
        └── SKILL.md              # Hermes skill
```

## CI/CD Integration

### Pre-commit Hook

```bash
#!/bin/bash
# .git/hooks/pre-commit

# Validate any changed todo JSON files
for file in $(git diff --cached --name-only | grep '\.json$'); do
    if grep -q '"schema_version"' "$file" 2>/dev/null; then
        python3 scripts/taskerkeeper.py validate "$file" || exit 1
    fi
done
```

### GitHub Actions

```yaml
# .github/workflows/validate-todos.yml
name: Validate Todo Files
on: [push, pull_request]
jobs:
  validate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Validate todo JSON files
        run: |
          for f in docs/todo-*.json; do
            if [ -f "$f" ]; then
              python3 scripts/taskerkeeper.py validate "$f"
            fi
          done
```

## Advanced Usage

### Dependency Graph Analysis

```bash
# See what a task blocks
python3 scripts/taskerkeeper.py deps my-todo.json 7.0.1

# Find all tasks ready to start
jq '.phases[].tasks[] |
  select(.status == "pending") |
  select(.prerequisites == [] or
    (.prerequisites | all(. as $p |
      [.phases[].tasks[] | select(.id == $p and .status == "done")] | length > 0
    ))
  ) | "\(.id) — \(.title)"
' my-todo.json
```

### Adding Tasks Programmatically

```bash
# Add a task with prerequisites
python3 scripts/taskerkeeper.py add-task my-todo.json 7.1 \
  "Add logging" \
  --prereqs 7.0.1,7.0.2 \
  --complexity Medium \
  --agent mid_dev_agent
```

### Batch Status Updates

```bash
# Mark multiple tasks as done
for id in 7.0.1 7.0.2 7.0.3; do
  python3 scripts/taskerkeeper.py done my-todo.json "$id"
done
```

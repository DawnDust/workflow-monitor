# Advanced usage

All commands run through the executable in the repository root.

## Daily commands

```powershell
.\workflow-monitor.exe context --format markdown
.\workflow-monitor.exe start --help
.\workflow-monitor.exe end --help
.\workflow-monitor.exe check
.\workflow-monitor.exe diagnostics status
.\workflow-monitor.exe diagnostics export
```

Run `./workflow-monitor.exe --help-all` for the complete command tree and `<command> --help` for exact arguments.

## Recovery

```powershell
.\workflow-monitor.exe task recover
.\workflow-monitor.exe task abandon --reason "Why work will not continue"
```

Abandonment appends an auditable record and releases active state. It does not delete or reset research files, staged changes, or branches.

## Updates

With no active task, a clean worktree, and synchronized `main`:

```powershell
.\workflow-monitor.exe update --check
.\workflow-monitor.exe update
```

The updater reads the official manifest, verifies SHA-256, stores versioned runtime files under `.project_hooks/runtime/`, migrates the project template, and rebuilds the local SQLite projection. It does not automatically commit, push, merge, or overwrite the permanent event log.

## Data model

- `maintenance/events.jsonl` is the Git-tracked append-only event source.
- `.project_hooks/maintenance.sqlite3` is a local rebuildable query projection.
- `.project_hooks/active-task.json` is recoverable active-task state.
- `resources/` stores registered project evidence and outputs.
- `workbench/` stores indexed reusable Markdown aids.

Do not manually rewrite historical event lines or edit SQLite.

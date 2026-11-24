# Getting started

## Requirements

- Windows 10 or Windows 11 on x64 hardware
- Git for Windows available on `PATH`
- Microsoft Edge WebView2 Runtime, normally included with current Windows installations

Python is not required for normal use.

## Install in a new project

1. Download `workflow-monitor.exe` from the official GitHub Release.
2. Create an empty project folder and place the executable at its root.
3. Keep the filename exactly `workflow-monitor.exe`.
4. Double-click it.

The first launch creates a `main` Git repository, installs the project workflow files, and opens the local Dashboard. Future launches reuse the same project.

## Add it to an existing Git project

Place the executable in the repository root and run:

```powershell
.\workflow-monitor.exe init .
.\workflow-monitor.exe check
```

Review existing repository guidance before replacing files. Initialization does not publish, push, or merge the project.

## Work with an AI coding agent

Describe the intended outcome in ordinary language inside **Antigravity**, **Codex**, or any compatible AI coding tool:

- **Shared repository contract**: The agent reads `AGENTS.md`, loads the current workflow context, starts a task before writing, records meaningful progress, runs the required tests, and ends the task with evidence.
- **Native Antigravity plugin**: Initialized and updated repositories include `.agents/plugins/workflow-monitor/` with progressive-disclosure Skills and lifecycle guard hooks (`SessionStart` context injection and `PreToolUse` protection against direct `events.jsonl`/SQLite edits or unconfirmed pushes/merges).
- **Viewing the Dashboard**: Double-click `workflow-monitor.exe` at the repository root (or run `Start-Process .\workflow-monitor.exe`) at any time to open the standalone read-only WebView2 Dashboard.
- **Sandbox vs. Turbo mode**: Read-only commands (`context`, `status`, `check`) run directly in Antigravity sandbox mode. Lifecycle writes touching `.git/workflow-monitor/` or test runners invoking a system Python outside the workspace (for example on drive `C:`) require Turbo mode or explicit path allowance.

You should not need to copy lifecycle commands into every prompt. You must still explicitly approve abandonment, migration, database rebuild, software update, merge, or publication when requested.

## Interface language

Open **Settings → Language** and choose Simplified Chinese or English. The change is immediate and stored only in the local WebView2 user profile. If storage is unavailable or invalid, the interface falls back to Simplified Chinese.

Only system navigation, buttons, prompts, table headings, and known status labels are translated. Project descriptions, task text, resource titles, event records, and other user data stay exactly as written.

# Workflow Monitor user manual

Workflow Monitor is a local Windows Dashboard for AI-assisted scientific Git projects. It makes the current goal, task lifecycle, Research Chapters, attempts, resources, diagnostics, and release state visible in one place.

![Workflow Monitor project overview](../assets/workflow-monitor-overview.png)

## Core guarantees

- The graphical Dashboard is read-only for project business data.
- No server port, CDN, telemetry, or automatic diagnostic upload is used.
- Permanent history remains in append-only project files; SQLite is a rebuildable local projection.
- Changing the interface language never translates user-entered project content or recorded data.
- Destructive or high-risk lifecycle actions require explicit confirmation in the AI conversation.

Start with [Getting started](getting-started.md), then learn the [Dashboard](workbench.md) and the distinction between [maintenance and research](lifecycle.md).

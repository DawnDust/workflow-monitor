# Using the workbench

The workbench is an observation and navigation surface. Lifecycle writes are performed by the structured service through the AI agent or advanced CLI.

## Main views

- **Workflow:** active and historical tasks, action availability, stages, research attempts, and decisions.
- **Overview:** project description, current stage, judgment, breakpoint, blockers, next steps, delivery state, and resource summary.
- **Search:** searches tasks, attempts, decisions, and registered resources without translating their content.
- **Resources:** navigates the seven standard resource directories and lazily loads registered entries.
- **Workbench:** indexes user-created or imported Markdown tools, instructions, methods, workflows, checklists, references, and templates.
- **Research stages:** groups the main goal, completed work, related attempts, and resources by stage.
- **Settings:** language, status explanations, advanced delivery or diagnostic views, and product information.

## Settings

**Language** changes local system text only. **Explanations** describes known workflow and Git states. **Advanced view** preserves version status, action availability, diagnostics, and raw events; expensive sections load only when expanded. **About** identifies DawnDust as the author and provides the official repository, manual, version, build commit, MIT License, issue, and private vulnerability-reporting links.

External links open only after an explicit click. Project content and diagnostic data are never uploaded automatically.

![Workflow Monitor Settings page](../assets/workflow-monitor-settings.png)

The theme and refresh controls remain in the top bar. They are not part of Settings.

## Resources and workbench items

Resources live under the standard `resources/` directories and are registered in the catalog. Workbench items are reusable Markdown aids stored under `workbench/`. They are never automatically executed, installed, checked online, or injected into daily context. Text is copied for the AI agent only after an explicit selection.

## Diagnostics

Internal failures can create local, rotating, redacted diagnostic records. Export produces a ZIP on the local machine. Review every file before attaching it to an Issue; nothing is uploaded automatically.

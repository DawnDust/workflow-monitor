# Using the Dashboard

The Dashboard is an observation and navigation surface. Lifecycle writes are performed by the structured service through the AI agent or advanced CLI.

## Main views

- **Workflow:** active and historical tasks, action availability, Research Chapters, and research attempts.
- **Overview:** project description, current Research Chapter, judgment, breakpoint, blockers, next steps, delivery state, and resource summary.
- **Search:** searches tasks, attempts, and registered resources without translating their content.
- **Resources:** navigates the standard resource directories and lazily loads registered entries.
- **Research Chapters:** groups chapter goals, completed work, related attempts, and resources.
- **Settings:** language, status explanations, advanced delivery or diagnostic views, and product information.

## Settings

**Language** changes local system text only. **Explanations** describes known workflow and Git states. **Advanced view** preserves version status, action availability, diagnostics, and raw events; expensive sections load only when expanded. **About** identifies DawnDust as the author and provides the official repository, manual, version, build commit, MIT License, issue, and private vulnerability-reporting links.

External links open only after an explicit click. Project content and diagnostic data are never uploaded automatically.

![Workflow Monitor Settings page](../assets/workflow-monitor-settings.png)

The theme and refresh controls remain in the top bar. They are not part of Settings.

## Resources and Sparks

Resources live under the standard `resources/` directories and can be registered in the catalog. `resources/sparks/` is a low-constraint Markdown idea pool: unregistered Sparks are valid, while optionally registered Sparks appear in resources and cross-record search.

## Research attention

The Overview shows at most three deterministic research-attention signals derived from existing structured facts, such as missing registered resources, explicit contradictory evidence, a blocked chapter, or an active exploration without continuity steps. The same sourced signals enter the AI Context and link only to existing read-only pages. They are not scientific conclusions and never trigger automatic repairs. Unregistered Sparks remain free-form and are not historically tracked.

## Diagnostics

Internal failures can create local, rotating, redacted diagnostic records. Export produces a ZIP on the local machine. Review every file before attaching it to an Issue; nothing is uploaded automatically.

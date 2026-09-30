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

## Resource paths and folders

Resources is a lightweight folder guide and registration list. The left tree shows names, nonzero counts and exceptions. The right side shows the current folder purpose, location and open action, followed only by immediate subfolders and files. Unregistered and missing materials have badges; normal registration needs no repeated status. Details open on demand. Empty folders remain visible, bundle contents need no separate registration, and global search still finds registered materials across folders. Refresh preserves expansion, selection and keyboard focus. The folder panel can collapse in narrow windows. Register custom folders with `catalog folder add --path <directory> --name <name> --purpose <purpose>`; update/list/archive/restore remain compatible.

Before every resource create, edit, import, move, rename or delete, the AI rereads the read-only `catalog folder index` (`catalog.folder.index` structured action; `--format json` for JSON). It lists presets, registered, unregistered and missing folders with purposes, hierarchy and counts. Create and register new folders with a name and purpose, then refresh the guide; ask when placement is unclear. Registration guides file management without replacing the filesystem. Import output with `catalog ingest <source> --kind output --folder resources/analysis/program/outputs`; the target must be an existing preset or registered folder. Omitting it requests a choice and never recreates top-level outputs. Code follows the code repository structure.

New presets are `resources/tutorials/` for literature tutorials, `resources/translations/` for translations and `resources/plans/` for next steps from Sparks. The shared `resources/outputs/` directory is optional and is no longer created by default. Existing entries remain; program outputs can live in `resources/analysis/program/outputs/`. Directory kinds provide defaults; move repairs preserve original material kinds.

When a registered path is missing, first run `catalog reconcile` for a read-only project search. `catalog reconcile --apply` repairs uniquely identified SHA-256 matches while preserving IDs, metadata and relationships. Scan performs reconciliation before creating entries and reserves unresolved candidates. Names and sizes only suggest candidates. Missing historical hashes, multiple identical copies and unknown destinations require user input. An explicitly confirmed location uses `catalog reconcile --apply --item-id <ID> --path <path>`. Locations outside resources must first be moved back. Files are never automatically moved, deleted or archived.

Start records existing resource issues. Repeatable `--depends-on catalog:<ID>` or `--depends-on path:<relative-path>` options on start/report/end declare direct dependencies; `--deliverable` uses the same reference format. Structured input supports these fields. Unrelated, unchanged historical resource issues remain pending in the completion receipt; new issues, missing deliverables and missing direct dependencies still block completed. Global check continues to report all issues, and journal/database integrity gates remain mandatory.

For a genuinely blocked task, use `end --result blocked --note <summary> --blocker <reason> --evidence <evidence> --next <recovery-condition>`. Unfinished verification is recorded accurately. Command failure retains the active task; task abandon requires explicit abandonment. Repairs append workflow events without rewriting historical abandoned outcomes or existing journal lines.

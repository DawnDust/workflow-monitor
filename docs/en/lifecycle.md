# Maintenance and research

Workflow Monitor keeps predictable maintenance separate from uncertain exploration.

## Stable maintenance

Use the `stable` track on `main` for documentation, known fixes, routine refactoring, and other changes whose intended behavior is understood. A task records its goal, acceptance criteria, progress, evidence, final judgment, and handoff.

## Research and experiments

Use `research`, `experiment`, or `sandbox` with a topic when testing a new theory, algorithm, architecture, or uncertain behavior. Each attempt has a branch, hypothesis, evidence, conclusion, and disposition. A validated attempt may prepare a Squash pull request, but merging always waits for explicit user approval.

## Typical lifecycle

1. Read the current context and relevant repository rules.
2. Start a task with scope and acceptance criteria.
3. Work on the correct stable or exploratory track.
4. Update structured progress and preserve evidence at useful checkpoints.
5. Run the required tests and integrity checks.
6. End with evidence and a precise next step.

Crashes do not justify deleting state. Use recovery commands to resume or explicitly abandon the task with a reason.

## Research Chapters, Sparks, and resources

Large goals are organized into nonlinear Research Chapters. A chapter may be updated repeatedly, contain multiple explorations, pause while another chapter becomes active, and later resume. `paused` records either an interruption or a temporary closure; temporary closure is still resumable, while `completed` is permanently sealed. When new evidence overturns an earlier judgment, a revision records the reason, the new current summary, and its evidence as append-only chapter history rather than a separate decision system.

Research materials belong in the standard `resources/` subdirectories and are registered in the catalog. Free-form Markdown Sparks live in `resources/sparks/`; registration is optional until a Spark should become searchable. `check` verifies registered files and indexes while leaving unregistered Sparks unconstrained.

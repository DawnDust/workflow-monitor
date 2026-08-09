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
4. Update structured progress and record route-changing decisions.
5. Run the required tests and integrity checks.
6. End with evidence and a precise next step.

Crashes do not justify deleting state. Use recovery commands to resume or explicitly abandon the task with a reason.

## Project stages and resources

Large goals are organized into stages. Research materials belong only in the seven standard `resources/` subdirectories and must be registered in the catalog. `check` verifies that files and indexes remain consistent.

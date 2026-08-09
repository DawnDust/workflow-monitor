# Contributing to Workflow Monitor

Thanks for helping improve Workflow Monitor. User-facing documentation is maintained in English and Simplified Chinese; product changes should keep both interfaces and manuals aligned.

## Before opening a change

- Use GitHub Discussions for design or usage questions.
- Search existing Issues before reporting a bug or proposing a feature.
- Never post secrets or unreviewed diagnostic bundles publicly.
- Read `AGENTS.md`, then run `./workflow-monitor.exe context --format markdown` before repository work.

## Development workflow

Workflow Monitor protects its own maintenance lifecycle:

- Stable fixes and documentation are developed from `main` with the `stable` track.
- New algorithms, theories, experiments, or uncertain changes use `research`, `experiment`, or `sandbox` with a topic branch.
- Start a structured task before the first write, update its state while working, and finish it with `end`.
- Do not rewrite existing lines in `maintenance/events.jsonl`, edit SQLite directly, delete active state, bypass `end`, or trial uncertain changes on `main`.

## Tests

After code changes:

```powershell
python scripts/run_tests.py fast
```

Before submitting a pull request:

```powershell
python scripts/run_tests.py full
python -m mkdocs build --strict
```

Release maintainers additionally run `python scripts/run_tests.py release`. Existing scenarios must not be deleted to shorten the suite.

## Pull requests

Keep each pull request focused. Explain the user-visible outcome, testing evidence, compatibility impact, and whether English/Chinese UI or documentation changed. Do not include generated `site/`, local databases, diagnostics, credentials, or user research data.

Changes to public CLI behavior, the Python bridge, event schemas, SQLite schemas, update manifests, or package formats require explicit compatibility notes and dedicated tests.

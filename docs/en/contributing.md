# Contributor guide

Read the root [contribution guide](https://github.com/DawnDust/workflow-monitor/blob/main/CONTRIBUTING.md) and `AGENTS.md` before changing the repository.

## Architecture

The Python application follows four layers: core, application, infrastructure, and UI. Dependencies point inward; UI code does not own persistence or system processes. Public CLI behavior, the WebView2 bridge, event Schema 3, and existing project data must remain compatible unless a separately approved migration says otherwise.

## Documentation

English is the default site and Simplified Chinese is maintained in parallel under `docs/zh/`. Keep page names and navigation symmetric. System interface text may be translated; examples containing project or recorded data must preserve that data verbatim.

Build locally with:

```powershell
python -m pip install -r requirements-docs.txt
python -m mkdocs build --strict
```

Do not commit the generated `site/` directory.

## Release preparation

Build a local test executable with `python scripts/build_windows_exe.py --channel test`. Keep its filename fixed as `workflow-monitor.exe`; the `version` command and About page identify it as `<app-version>-test.<YYYYMMDD>.<short-build-id>`. Stable release builds use `--channel stable` and keep the same filename. Test-channel builds cannot produce a release manifest.

Update the application version and changelog, run fast and full tests, build the documentation strictly, then run the release suite. A `v*` tag triggers the Windows release workflow, but creating a tag or Release requires explicit maintainer approval.

## Test build delivery

Test builds read `TEST_BUILD_NOTES.md` by default or accept `--notes <path>`; required sections must contain text. Notes are embedded and also generated as `dist/test-build-notes.md`, with automatic build identity and an end-of-build input check. `build-notes --format markdown|json` works outside projects. Report source, running executable and project-template state separately; a binary alone does not synchronize source. Preview a local project upgrade with `update --from-exe <path> --check`, then omit `--check` to apply under the existing preconditions. Preserve customized files and report conflicts.

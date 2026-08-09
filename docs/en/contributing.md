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

Update the application version and changelog, run fast and full tests, build the documentation strictly, then run the release suite. A `v*` tag triggers the Windows release workflow, but creating a tag or Release requires explicit maintainer approval.

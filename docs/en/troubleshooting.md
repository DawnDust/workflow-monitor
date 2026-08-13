# Troubleshooting

## The executable does not open

Confirm that the filename is `workflow-monitor.exe`, Git for Windows is installed, and the WebView2 Runtime is available. Run the executable from PowerShell to see a structured error.

## Windows SmartScreen warns about the file

The project does not currently use a commercial code-signing certificate. Download only from the official Release and verify the SHA-256 value against `release-manifest.json`.

## The project reports an unfinished task

Run `./workflow-monitor.exe context --format markdown`. If the prior process crashed, use `task recover`. If work is intentionally abandoned, use `task abandon --reason ...`; never delete the sidecar or database manually.

## The interface shows stale data

Use the refresh button. If a refresh fails, the Dashboard intentionally retains the last known good snapshot and shows the error. Export diagnostics if the failure persists.

## Language preference is not saved

The preference is stored in the local WebView2 profile. Storage failures fall back to Simplified Chinese and never affect project files or events.

## Reporting a bug

Export a diagnostic ZIP from **Settings → Advanced view → Diagnostics**, inspect and redact it, then use the Bug Report form. Include the application version, incident ID when available, reproduction steps, expected behavior, and actual behavior.

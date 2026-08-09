# Workflow Monitor

[简体中文](README.zh-CN.md) · [User manual](https://dawndust.github.io/workflow-monitor/) · [Download](https://github.com/DawnDust/workflow-monitor/releases/latest)

[![Release](https://img.shields.io/github/v/release/DawnDust/workflow-monitor?display_name=tag)](https://github.com/DawnDust/workflow-monitor/releases/latest)
[![CI](https://github.com/DawnDust/workflow-monitor/actions/workflows/ci.yml/badge.svg)](https://github.com/DawnDust/workflow-monitor/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
![Platform](https://img.shields.io/badge/platform-Windows%2010%2F11-0078D4)

Workflow Monitor is a local, single-file workflow companion for AI-assisted scientific Git projects on Windows. It keeps tasks, research attempts, decisions, handoffs, resources, and release readiness visible without running a server or uploading project data.

![Workflow Monitor project overview](docs/assets/workflow-monitor-overview.png)

## Why Workflow Monitor

- **One executable:** users only download `workflow-monitor.exe`; Python is not required.
- **Natural-language operation:** describe work to your AI coding agent and let the project rules drive the structured lifecycle.
- **Stable work and experiments stay distinct:** maintenance remains on `main`, while uncertain research uses explicit tracks and branches.
- **Local and auditable:** permanent records are append-only project files; the SQLite database is a rebuildable local projection.
- **Read-only workbench:** inspect stages, actions, resources, diagnostics, and release state without exposing a network port.
- **Bilingual interface:** switch between Simplified Chinese and English locally. User-authored content and recorded data are never translated.

## Quick start

Prerequisites: Windows 10/11 x64, [Git for Windows](https://git-scm.com/download/win), and the Microsoft Edge WebView2 Runtime included with current Windows installations.

1. Download `workflow-monitor.exe` from the [latest release](https://github.com/DawnDust/workflow-monitor/releases/latest).
2. Put it in a new or existing project folder. Keep the filename unchanged.
3. Double-click it. A new folder is initialized as a Git repository and the local workbench opens.
4. In your AI coding agent, describe the task in plain language. The repository guidance handles lifecycle commands and safety checks.

Windows SmartScreen may warn about the unsigned executable. Verify that it came from the official GitHub Release and compare its SHA-256 value with `release-manifest.json` before continuing.

## Documentation and community

- [User manual](https://dawndust.github.io/workflow-monitor/)
- [中文用户手册](https://dawndust.github.io/workflow-monitor/zh/)
- [Troubleshooting](https://dawndust.github.io/workflow-monitor/troubleshooting/)
- [Changelog](CHANGELOG.md)
- [Contributing](CONTRIBUTING.md)
- [Support](SUPPORT.md)
- [Security policy](SECURITY.md)

Please use [GitHub Discussions](https://github.com/DawnDust/workflow-monitor/discussions) for usage questions, [Issues](https://github.com/DawnDust/workflow-monitor/issues) for reproducible bugs and feature proposals, and GitHub private vulnerability reporting for security issues.

## Privacy

The desktop UI binds no network port, uses no CDN, contains no telemetry, and does not automatically upload diagnostics or project data. Network access only occurs for explicit update/release checks. Diagnostic bundles are created locally and must be reviewed and attached manually.

## License

Workflow Monitor is available under the [MIT License](LICENSE).

# Workflow Monitor

<p align="center"><img src="docs/assets/workflow-monitor-wordmark.svg" alt="Workflow Monitor" width="430"></p>

[English](README.md) · [用户手册](https://dawndust.github.io/workflow-monitor/zh/) · [下载](https://github.com/DawnDust/workflow-monitor/releases/latest)

[![Release](https://img.shields.io/github/v/release/DawnDust/workflow-monitor?display_name=tag)](https://github.com/DawnDust/workflow-monitor/releases/latest)
[![CI](https://github.com/DawnDust/workflow-monitor/actions/workflows/ci.yml/badge.svg)](https://github.com/DawnDust/workflow-monitor/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
![Platform](https://img.shields.io/badge/platform-Windows%2010%2F11-0078D4)

Workflow Monitor 是面向 Windows 科研 Git 项目的本地单文件工作流工具。它让 AI 协作中的任务、探索、决策、交接、资料和发布状态保持清晰，同时不启动服务器，也不上传项目数据。

![Workflow Monitor 项目概览](docs/assets/workflow-monitor-overview.png)

## 为什么使用 Workflow Monitor

- **单文件交付：**普通用户只需下载 `workflow-monitor.exe`，不需要安装 Python。
- **自然语言操作：**在 AI 编码工具中描述目标，仓库规则会驱动结构化生命周期与安全检查。
- **稳定维护与探索分离：**稳定工作留在 `main`，不确定研究使用明确的轨道和分支。
- **本地且可审计：**永久记录使用追加式项目文件，SQLite 只是可重建的本地查询投影。
- **只读工作台：**集中查看阶段、动作、资料、诊断和发布状态，不开放网络端口。
- **双语界面：**简体中文和 English 仅改变本机系统界面；用户填写的内容和数据记载始终保持原文。

## 四步开始

系统要求：Windows 10/11 x64、[Git for Windows](https://git-scm.com/download/win)，以及当前 Windows 通常已经包含的 Microsoft Edge WebView2 Runtime。

1. 从[最新 Release](https://github.com/DawnDust/workflow-monitor/releases/latest)下载 `workflow-monitor.exe`。
2. 将它放入新建或已有的项目目录，并保持文件名不变。
3. 双击运行。空目录会自动初始化 Git 仓库并打开本地工作台。
4. 在 AI 编码工具中直接用自然语言描述任务；仓库规范会处理生命周期命令和安全边界。

程序暂未使用商业代码签名，因此 Windows SmartScreen 可能在首次运行时提示。请确认文件来自本项目正式 GitHub Release，并可使用 `release-manifest.json` 核对 SHA-256。

## 文档与社区

- [中文用户手册](https://dawndust.github.io/workflow-monitor/zh/)
- [English manual](https://dawndust.github.io/workflow-monitor/)
- [故障排查](https://dawndust.github.io/workflow-monitor/zh/troubleshooting/)
- [更新日志](CHANGELOG.md)
- [贡献指南](CONTRIBUTING.md)
- [使用支持](SUPPORT.md)
- [安全策略](SECURITY.md)

使用问题请前往 [GitHub Discussions](https://github.com/DawnDust/workflow-monitor/discussions)，可复现 Bug 和功能建议请提交 [Issue](https://github.com/DawnDust/workflow-monitor/issues)，安全漏洞请使用 GitHub 私密漏洞报告。

## 隐私

桌面界面不监听网络端口、不使用 CDN、没有遥测，也不会自动上传诊断或项目数据。只有用户明确检查更新或 Release 时才会联网。诊断包只在本地生成，必须由用户检查后手动附加到反馈中。

## 许可证

Workflow Monitor 使用 [MIT License](LICENSE)。

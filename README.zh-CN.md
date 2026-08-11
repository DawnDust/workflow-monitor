# Workflow Monitor

<p align="center"><img src="docs/assets/workflow-monitor-social-preview.png" alt="Workflow Monitor——本地、可审计、为科研而构建" width="960"></p>

[English](README.md) · [用户手册](https://dawndust.github.io/workflow-monitor/zh/) · [下载](https://github.com/DawnDust/workflow-monitor/releases/latest)

[![Release](https://img.shields.io/github/v/release/DawnDust/workflow-monitor?display_name=tag)](https://github.com/DawnDust/workflow-monitor/releases/latest)
[![CI](https://github.com/DawnDust/workflow-monitor/actions/workflows/ci.yml/badge.svg)](https://github.com/DawnDust/workflow-monitor/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
![Platform](https://img.shields.io/badge/platform-Windows%2010%2F11-0078D4)

Workflow Monitor 是面向 Windows 科研 Git 项目的本地单文件工作流工具。它让 AI 协作中的任务、探索、决策、交接、资料和发布状态保持清晰，同时不启动服务器，也不上传项目数据。

## 更多输出，不等于更可控

今天的 AI 已经可以比人更快地产生代码、笔记、计划、实验和报告。但输出越来越多，并不自然等于科研质量越来越高，反而可能让真正重要的问题更难回答：当前目标是什么？哪个结果经过了验证？这次改动属于稳定工作还是探索？结论来自哪里？哪些外部行动真正得到研究者授权？

许多自动化科研方案仍在追求生成更多内容、调用更多智能体和执行更长流程。Workflow Monitor 选择了一条更克制的路线。它不是“自主科学家”，也不是替人做决定的智能体编排器，而是一套**半自动科研工作流与可视化监控工具**：AI 执行用户要求的工作，工作流记录权威事实并设置检查点，研究者始终保留科学判断、路线变更、合并和发布的控制权。

它追求的不是自动化程度最大化，而是让 AI 参与的科研过程保持**可见、有边界、可验证、可恢复**——让能力增加，而不是让控制减少。

![Workflow Monitor 项目概览](docs/assets/workflow-monitor-overview.png)

## 工作流程

自然语言请求会进入一套结构化但轻量的生命周期：只读问题保持只读；需要修改时明确区分稳定任务与探索任务；完成前必须通过验证和阶段审阅；提交、推送、合并与发布等外部交付动作仍然需要用户授权。

<p align="center"><img src="docs/assets/workflow-monitor-lifecycle.svg" alt="Workflow Monitor 从自然语言请求、任务执行、验证到授权交付的完整生命周期" width="760"></p>

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

未签名的程序可能触发 Windows SmartScreen。请确认文件来自本项目正式 GitHub Release，使用 `release-manifest.json` 核对 SHA-256、检查 `workflow-monitor.spdx.json`，并运行 `gh attestation verify workflow-monitor.exe --repo DawnDust/workflow-monitor` 验证构建来源；manifest 会明确说明是否应用了 Authenticode 签名。

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

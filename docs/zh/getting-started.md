# 快速开始

## 系统要求

- Windows 10 或 Windows 11 x64
- `PATH` 中可以使用 Git for Windows
- Microsoft Edge WebView2 Runtime；当前 Windows 通常已经包含

普通使用不需要安装 Python。

## 在新项目中安装

1. 从官方 GitHub Release 下载 `workflow-monitor.exe`。
2. 新建空项目目录，将 EXE 放在目录根部。
3. 保持文件名严格为 `workflow-monitor.exe`。
4. 双击运行。

首次启动会创建 `main` Git 仓库、安装项目工作流文件并打开本地仪表盘。以后继续在同一项目中运行即可。

## 添加到已有 Git 项目

将 EXE 放到仓库根目录并运行：

```powershell
.\workflow-monitor.exe init .
.\workflow-monitor.exe check
```

覆盖任何已有文件前先检查仓库规范。初始化不会自动发布、推送或合并项目。

## 与 AI 编码工具协作

直接在 **Antigravity**、**Codex** 或其他兼容 AI 编码工具中用自然语言描述希望完成的结果：

- **通用规范**：AI 会读取 `AGENTS.md`，加载当前上下文、在首次写入前开始任务、记录重要进展、运行规定测试，并以证据结束任务。
- **Antigravity 原生支持**：初始化或升级后的项目会自动生成 `.agents/plugins/workflow-monitor/` 插件，提供按需加载的生命周期/资料/多工作树 Skills，以及 `SessionStart` 上下文注入与 `PreToolUse` 安全护栏（拦截直改 `events.jsonl`、SQLite 与未确认的推送/合并）。
- **查看完整看板**：随时双击项目根目录的 `workflow-monitor.exe`（或由 AI 执行 `Start-Process .\workflow-monitor.exe`）打开独立的只读 WebView2 桌面看板。
- **沙箱与 Turbo 提示**：在 Antigravity 中，纯只读命令（如 `context`、`status`、`check`）可在沙箱模式下直接运行；当生命周期写入需要更新 `.git/workflow-monitor/` 注册表，或测试执行器需要调用安装在 `C:` 盘等工作区外路径的系统 Python 时，请开启 Turbo 模式或放行对应路径。

你不需要在每次提示中复制生命周期命令。放弃、迁移、数据库重建、软件更新、合并或发布仍必须由你明确同意。

## 界面语言

打开“设置 → 语言”，选择简体中文或 English。界面会立即切换，偏好只保存在本机 WebView2 用户目录。存储不可用或内容异常时默认回退到简体中文。

只有系统导航、按钮、提示、表头和已知状态标签会翻译。项目描述、任务文字、资料标题、决策、事件记载和其他用户数据始终保持原文。

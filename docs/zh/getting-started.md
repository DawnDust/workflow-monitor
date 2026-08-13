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

直接用自然语言描述希望完成的结果。兼容的 AI 会读取 `AGENTS.md`、加载当前上下文、在首次写入前开始任务、记录重要进展、运行规定测试，并以证据结束任务。

你不需要在每次提示中复制生命周期命令。放弃、迁移、数据库重建、软件更新、合并或发布仍必须由你明确同意。

## 界面语言

打开“设置 → 语言”，选择简体中文或 English。界面会立即切换，偏好只保存在本机 WebView2 用户目录。存储不可用或内容异常时默认回退到简体中文。

只有系统导航、按钮、提示、表头和已知状态标签会翻译。项目描述、任务文字、资料标题、决策、事件记载和其他用户数据始终保持原文。

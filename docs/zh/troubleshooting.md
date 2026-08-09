# 故障排查

## 双击 EXE 没有打开

确认文件名是 `workflow-monitor.exe`、已安装 Git for Windows，并且 WebView2 Runtime 可用。在 PowerShell 中运行 EXE 可以看到结构化错误信息。

## Windows SmartScreen 提示风险

项目暂未使用商业代码签名证书。只从官方 Release 下载，并使用 `release-manifest.json` 核对 SHA-256。

## 项目提示存在未完成任务

运行 `./workflow-monitor.exe context --format markdown`。如果上次程序崩溃，使用 `task recover`；如果明确不再继续，使用 `task abandon --reason ...`。不要手工删除 sidecar 或数据库。

## 界面数据似乎没有更新

点击刷新按钮。刷新失败时，工作台会保留最后一次正常快照并显示错误，这是预期的安全行为。如果问题持续，请导出诊断。

## 语言选择没有保存

语言偏好保存在本机 WebView2 用户目录。存储失败时回退中文，且不会影响项目文件或事件记载。

## 报告 Bug

从“设置 → 高级查看 → 诊断”导出 ZIP，检查并脱敏后再使用 Bug 报告表单。请提供程序版本、可用的事件编号、复现步骤、预期结果和实际结果。

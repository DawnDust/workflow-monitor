# 高级使用

所有命令均通过仓库根目录的 EXE 运行。

## 日常命令

```powershell
.\workflow-monitor.exe context --format markdown
.\workflow-monitor.exe start --help
.\workflow-monitor.exe end --help
.\workflow-monitor.exe check
.\workflow-monitor.exe diagnostics status
.\workflow-monitor.exe diagnostics export
```

使用 `./workflow-monitor.exe --help-all` 查看完整命令树，使用 `<命令> --help` 查看准确参数。

## 崩溃恢复

```powershell
.\workflow-monitor.exe task recover
.\workflow-monitor.exe task abandon --reason "不再继续的原因"
```

放弃只会追加可审计记录并解除活动状态，不会删除或重置科研文件、暂存改动或分支。

## 软件更新

在没有活动任务、工作区干净且 `main` 已同步时运行：

```powershell
.\workflow-monitor.exe update --check
.\workflow-monitor.exe update
```

更新器读取官方 manifest、校验 SHA-256、将版本化运行时保存到 `.project_hooks/runtime/`，迁移项目模板并重建本地 SQLite 投影。它不会自动提交、推送、合并或覆盖永久事件日志。

## 数据模型

- `maintenance/events.jsonl` 是 Git 跟踪的追加式事件源。
- `.project_hooks/maintenance.sqlite3` 是可重建的本地查询投影。
- `.project_hooks/active-task.json` 保存可恢复的活动任务状态。
- `resources/` 保存登记的项目证据与输出。
- `resources/sparks/` 保存自由 Markdown 灵感，可选择性通过 catalog 登记。

不要手工改写历史事件行或编辑 SQLite。

## 验证发行产物

每个发行版都会发布 `workflow-monitor.exe`、`release-manifest.json` 和 SPDX JSON SBOM。安装前请验证下载的可执行文件：

```powershell
(Get-FileHash .\workflow-monitor.exe -Algorithm SHA256).Hash
gh attestation verify .\workflow-monitor.exe --repo DawnDust/workflow-monitor
gh attestation verify .\workflow-monitor.spdx.json --repo DawnDust/workflow-monitor
```

将 SHA-256 与 `release-manifest.json` 对照。Manifest 也会明确说明 EXE 是否经过 Authenticode 签名；未签名构建仍受支持，但会被清楚标记。

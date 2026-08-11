# 安装、恢复与交付

- 新 clone/worktree：放置根目录 EXE，运行 `.\workflow-monitor.exe install`。
- 升级：在无活动任务、干净且同步的 main 上运行 `.\workflow-monitor.exe update`。首次写入 Schema v4 后不可降级到仅支持 v1-v3 的版本。
- 恢复：异常中断运行 `task recover`；明确放弃运行 `task abandon --reason <原因>`。
- 诊断：使用 `diagnostics status/export`；诊断不自动上传。
- 验证：代码写入后 fast，任务结束前按 Context 的 required_actions 补齐 full/release，再运行 `check` 与 `db verify`。
- 交付：Dashboard 只读。提交、推送、PR、合并、正式 EXE 构建和发布分别等待用户确认。

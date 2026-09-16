# 安装、恢复与交付

- 新 clone/worktree：放置根目录 EXE，运行 `.\workflow-monitor.exe install`。
- 升级：在无活动任务、干净且同步的 main 上运行 `.\workflow-monitor.exe update`。首次写入 Schema v4 后不可降级到仅支持 v1-v3 的版本。
- 恢复：异常中断运行 `task recover`；明确放弃运行 `task abandon --reason <原因>`。
- 诊断：使用 `diagnostics status/export`；诊断不自动上传。
- 验证：完成一批相关代码修改后使用 report --verify fast；结束时自动补齐同一软件输入指纹的回执（软件改动 fast/full，release profile 为 fast/release；纯文档和治理改动使用内置检查），正常收尾使用 `end` 内置检查，不重复运行独立 `check`；专项诊断可按需检查，涉及持久化或本机交付时运行 `db verify`。
- 交付：Dashboard 只读。提交、推送、PR、合并、正式 EXE 构建和发布分别等待用户确认。

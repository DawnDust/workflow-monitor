# Workflow Monitor 项目维护索引

动态事实只写入 `maintenance/events.jsonl`，SQLite、Context、Dashboard、交接和研究篇章视图均为可重建投影。

- [CORE.md](CORE.md)：新会话、上下文丢失或规范变更时读取的生命周期、轨道、安全、测试和研究篇章契约。
- [RESEARCH.md](RESEARCH.md)：研究篇章、探索、Catalog 与 Sparks。
- [OPERATIONS.md](OPERATIONS.md)：安装、升级、恢复、诊断、交付与发布。
- `ARCHITECTURE.md`：代码边界和读写模型架构。

`maintenance/events.jsonl` 只追加且不得改写历史行；`.project_hooks/maintenance.sqlite3` 和测试回执均为本地生成物。

## Codex 简短调用

- 默认读取 `context --view brief --format markdown`；只查结束门禁使用 `context --view verification`，追溯全部事实使用 `context --view full`。篇章和交接详情按需使用 `stage show`、`history`。
- `start` 可省略任务 ID，由工作流自动分配；`end` 可省略 ID，使用当前活动任务。两者及 `report` 可加 `--compact` 返回简短回执；复杂填报支持 `--input-json <文件或 ->`。
- 中途仅在阶段完成、判断变化、阻塞或交接时保存 checkpoint。短任务可通过 `end --current-step ... --next ...` 同时保存最终进展，不在结束前重复调用内容相同的 `state update`。
- 精简显示不改变测试、篇章审阅和授权门禁；失败与警告必须处理。不要用无参 EXE 获取上下文，无参冻结 EXE 会启动桌面界面。

# Workflow Monitor 项目维护索引

动态事实只写入 `maintenance/events.jsonl`，SQLite、Context、Dashboard、交接和阶段视图均为可重建投影。

- [CORE.md](CORE.md)：每次 AI 任务必读的生命周期、轨道、安全、测试和阶段契约。
- [RESEARCH.md](RESEARCH.md)：阶段、探索、Catalog 与 Workbench。
- [OPERATIONS.md](OPERATIONS.md)：安装、升级、恢复、诊断、交付与发布。
- `ARCHITECTURE.md`：代码边界和读写模型架构。

`maintenance/events.jsonl` 只追加且不得改写历史行；`.project_hooks/maintenance.sqlite3` 和测试回执均为本地生成物。

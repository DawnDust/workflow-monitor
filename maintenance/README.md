# 项目维护索引

本目录保留静态规范和一份 Git 可合并的动态事件日志。SQLite 工作库位于 `.project_hooks/maintenance.sqlite3`，不纳入版本控制。

| 文件 | 唯一职责 |
|:---|:---|
| [project_context.md](./project_context.md) | 稳定的项目边界、架构、命令和约定 |
| [branch_workflow.md](./branch_workflow.md) | 稳定主线、探索分支、成功合并与失败归档规则 |
| [workflow_spec.md](./workflow_spec.md) | 生命周期、更新顺序和 Git 归属规则 |
| [events.jsonl](./events.jsonl) | 追加式动态事件源，用于 Git 审阅、合并和数据库重建 |

不要手工改写既有事件行或直接编辑 SQLite。使用 `context`、`history`、`decisions`、`explorations` 和 `attempt show` 查询，或运行 `python -m project_hooks dashboard` 打开只读桌面窗口；使用结构化 CLI 写入。

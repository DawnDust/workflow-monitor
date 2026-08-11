# Workflow Monitor 核心契约

1. 每次任务先运行 `.\workflow-monitor.exe context --format markdown`，再读取本文件；首次写入前运行 `start`。
2. 稳定维护只在 `main` 使用 `--track stable`；不确定改动使用 `research|experiment|sandbox`。无法判断时询问用户。
3. 动态事实只记录一次：任务目标和验收来自 `task.started`，项目资料来自 `project.profile_updated`，任务进度来自 `task.checkpointed`，结束结果来自 `task.finished`。
4. 使用 `state update --current-step ...` 保存 checkpoint；路线变化使用 `decision add`；探索证据使用 `attempt update`。
5. 代码写入后运行 `python scripts/run_tests.py fast`。结束门禁按实际改动要求同一指纹的 fast/full；release profile 要求 fast/release。纯文档和治理改动由 `end` 内置检查验证。
6. 有关联阶段的任务必须在 `end --stage-review updated|reviewed-no-change` 明确审阅。`updated` 必须有本任务产生的阶段事件。
7. 最后必须运行 `end`。禁止改写既有事件、直接编辑 SQLite、删除活动 sidecar、在 main 试改、自动合并。
8. 推送、PR、合并、数据库重建、软件升级和正式发布必须取得用户授权。

# Workflow Monitor 核心契约

1. 每次任务先运行 `.\workflow-monitor.exe context --view brief --format markdown`，新会话、上下文丢失或本文件变更时读取本文件，同一任务内无需重复读取未变规范；首次写入前运行 `start`。
   纯只读咨询和审查不启动写入生命周期，也不调用 `end`；准备首次写入时再判断轨道。已启动的任务必须通过 `end` 或规定的放弃流程收尾。
2. 稳定维护只在 `main` 使用 `--track stable`；不确定改动使用 `research|experiment|sandbox`。无法判断时询问用户。
3. 动态事实只记录一次：任务目标和验收来自 `task.started`，项目资料来自 `project.profile_updated`，任务进度来自 `task.checkpointed`，结束结果来自 `task.finished`。
4. 使用 `report` 一次填报进度和科研证据，程序自动保存 checkpoint；短任务可直接在 `end` 填报最终进展。
5. 完成一批相关代码修改后运行 `report --current-step ... --verify fast`；已通过的检查仅在新改动、失败或未解决问题影响其结果时重跑。结束时程序按实际门禁自动补齐同一软件输入指纹的回执：软件改动要求 fast/full，release profile 要求 fast/release；纯文档和治理改动由 `end` 内置检查验证。
6. 有关联研究篇章的任务必须在 `end --stage-review updated|reviewed-no-change` 明确审阅。`updated` 必须有本任务产生的篇章事件；新证据改变判断时以 `stage update --revision ... --summary ... --evidence ...` 追加修订。
7. 已启动的任务完成时运行 `end`。禁止改写既有事件、直接编辑 SQLite、删除活动 sidecar、在 main 试改、自动合并。
8. 推送、PR、合并、数据库重建、软件升级和正式发布必须取得用户授权。
9. 按篇章、探索、资料分类维护相关待审阅事项；`review show` 默认只给出精简变化，完整来源按需使用 `--full`。正式研究使用变化触发和 14 天提醒，稳定参考不作周期提醒，示例仅提示完整性问题。审阅随 `report` 或 `end` 一次提交；无关事项延后，无变化不改写结论。
10. 审阅状态由程序按待办及完整性问题确定；完整覆盖随成功的审阅批次自动记入事件，延后或完整性问题不算完成。最后整体审阅只显示已记录事件，新变化保留历史时间。

自动填报、组合篇章启动及本地执行器配置见 [AUTOMATION.md](AUTOMATION.md)，使用这些功能或排查失败时按需读取。

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


- 资料路径失效先运行 `catalog reconcile` 搜索可能的移动位置；唯一内容哈希匹配可通过 `--apply` 修复原登记，保留资料身份和关系。歧义、无历史哈希或范围外位置需要用户选择新位置或确认归档，不自动删除或归档。
- 文件夹导航登记使用 `catalog folder add/update/list/archive/restore`。教程、翻译用于文献加工，plans 用于 Sparks 的下一步；登记文件夹不替代内部文件登记。
- `start` 保存既有资料问题；AI 在 start/report/end 用 depends_on、deliverable 登记直接依赖和交付物（catalog:ID 或 path:相对路径）。无关且未恶化的既有资料问题保留待处理，不阻止 completed；任务交付物和直接依赖缺失仍阻止完成。全局 check 继续报告全部问题。
- 确实受阻时使用 `end --result blocked --blocker <原因> --evidence <证据> --next <恢复条件>`。检查失败保留活动任务，不默认 abandon；只有用户明确放弃才运行 task abandon。所有修复通过工作流命令或结构化服务追加事件，不直接编辑 SQLite 或既有事件，历史 abandoned 不改写。
- 每次资料创建、编辑、导入、移动、重命名或删除前，重新读取 `catalog folder index`，按目录用途选择位置；代码文件按仓库结构处理。新目录先创建并用 `catalog folder add` 登记名称和用途，再刷新索引；归属不明确时询问用户。输出导入显式指定 `--folder`，不自动重建顶层 outputs。

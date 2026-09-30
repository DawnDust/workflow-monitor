# 科研与研究篇章规范

每个分支最多一个 active 研究篇章。探索任务可创建或修订关联篇章，其他分支版本只读展示；任务引用篇章和探索 ID，不复制目标状态。

篇章允许反复推进和关联多轮探索。进入 `paused` 时必须记录 `interruption`（暂时中断）或 `temporary-closure`（暂时收束）及说明；暂停后可插入另一篇章，待其不再 active 后恢复旧篇章。`completed` 与 `cancelled` 永久封存，不得重开。

新证据改变当前判断时，使用 `stage update --revision ... --summary ... --evidence ...` 追加判断修订。修订必须同步最新篇章概况并引用至少一项证据；它属于篇章历史，不恢复独立决策体系。

探索启动使用 `--stage auto|new|none|篇章ID`；新篇章通过 `--new-stage` 同次创建，无需额外维护周期。已有活动篇章时必须明确暂停原因，不自动替换。维护默认不关联篇章，探索不关联时须填写 `--without-stage-reason`。

科研资料放在 `resources/` 的预设或已登记自定义文件夹，并通过 `catalog` 登记。每次资料文件操作前重新读取 `catalog folder index`，按目录用途选择位置。`resources/sparks/` 保存自由 Markdown 灵感，未登记内容不触发 `check` 警告；需要检索时可选择性登记。Markdown 公式使用 Markdown/LaTeX 语法。

只有证据完整且状态为 `validated` 的探索可准备 Squash PR；合并等待用户明确确认。

分支、篇章、探索和资料的审阅覆盖具体事件与文件版本，不能用无关活动刷新内容更新时间。`review list --kind ...` 分类查看，`review show` 默认返回精简变化，完整来源按需展开。正式研究使用变化触发和 14 天提醒，稳定参考不作周期提醒，示例只保留完整性检查；详见 [AUTOMATION.md](AUTOMATION.md)。


- 资料路径失效先运行 `catalog reconcile` 搜索可能的移动位置；唯一内容哈希匹配可通过 `--apply` 修复原登记，保留资料身份和关系。歧义、无历史哈希或范围外位置需要用户选择新位置或确认归档，不自动删除或归档。
- 文件夹导航登记使用 `catalog folder add/update/list/archive/restore`。教程、翻译用于文献加工，plans 用于 Sparks 的下一步；登记文件夹不替代内部文件登记。
- `start` 保存既有资料问题；AI 在 start/report/end 用 depends_on、deliverable 登记直接依赖和交付物（catalog:ID 或 path:相对路径）。无关且未恶化的既有资料问题保留待处理，不阻止 completed；任务交付物和直接依赖缺失仍阻止完成。全局 check 继续报告全部问题。
- 确实受阻时使用 `end --result blocked --blocker <原因> --evidence <证据> --next <恢复条件>`。检查失败保留活动任务，不默认 abandon；只有用户明确放弃才运行 task abandon。所有修复通过工作流命令或结构化服务追加事件，不直接编辑 SQLite 或既有事件，历史 abandoned 不改写。

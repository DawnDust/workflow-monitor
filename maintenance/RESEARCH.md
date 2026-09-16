# 科研与研究篇章规范

每个分支最多一个 active 研究篇章。探索任务可创建或修订关联篇章，其他分支版本只读展示；任务引用篇章和探索 ID，不复制目标状态。

篇章允许反复推进和关联多轮探索。进入 `paused` 时必须记录 `interruption`（暂时中断）或 `temporary-closure`（暂时收束）及说明；暂停后可插入另一篇章，待其不再 active 后恢复旧篇章。`completed` 与 `cancelled` 永久封存，不得重开。

新证据改变当前判断时，使用 `stage update --revision ... --summary ... --evidence ...` 追加判断修订。修订必须同步最新篇章概况并引用至少一项证据；它属于篇章历史，不恢复独立决策体系。

探索启动使用 `--stage auto|new|none|篇章ID`；新篇章通过 `--new-stage` 同次创建，无需额外维护周期。已有活动篇章时必须明确暂停原因，不自动替换。维护默认不关联篇章，探索不关联时须填写 `--without-stage-reason`。

科研资料只放在 `resources/` 的标准子目录并通过 `catalog` 登记。`resources/sparks/` 保存自由 Markdown 灵感，未登记内容不触发 `check` 警告；需要检索时可选择性登记。Markdown 公式使用 Markdown/LaTeX 语法。

只有证据完整且状态为 `validated` 的探索可准备 Squash PR；合并等待用户明确确认。

分支、篇章和资料的审阅覆盖具体事件与文件版本，不能用无关更新刷新审阅时间。`review list/show` 只读查看待维护范围，随 `report/end` 的 `reviews` 填报结果。允许当前任务内自主维护与主动审阅；未关联事项不自动扩大任务范围。默认活跃内容 14 天兜底、暂停后恢复检查，详见 [AUTOMATION.md](AUTOMATION.md)。

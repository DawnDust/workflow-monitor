# 科研与研究篇章规范

全项目最多一个 active 研究篇章。使用内部 `stage start/update` 管理篇章事实；探索只引用篇章和任务 ID，不复制目标状态。

篇章允许反复推进和关联多轮探索。进入 `paused` 时必须记录 `interruption`（暂时中断）或 `temporary-closure`（暂时收束）及说明；暂停后可插入另一篇章，待其不再 active 后恢复旧篇章。`completed` 与 `cancelled` 永久封存，不得重开。

新证据改变当前判断时，使用 `stage update --revision ... --summary ... --evidence ...` 追加判断修订。修订必须同步最新篇章概况并引用至少一项证据；它属于篇章历史，不恢复独立决策体系。

探索轨道启动前若没有 active 研究篇章，工作流返回篇章草案并阻止启动。用户确认后先用短 stable 生命周期创建篇章；用户明确不需要篇章时，用 `--without-stage-reason` 记录理由。

科研资料只放在 `resources/` 的标准子目录并通过 `catalog` 登记。`resources/sparks/` 保存自由 Markdown 灵感，未登记内容不触发 `check` 警告；需要检索时可选择性登记。Markdown 公式使用 Markdown/LaTeX 语法。

只有证据完整且状态为 `validated` 的探索可准备 Squash PR；合并等待用户明确确认。

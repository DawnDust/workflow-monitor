# 科研与阶段规范

全项目最多一个 active 阶段。使用 `stage start/update` 管理阶段事实；探索只引用阶段和任务 ID，不复制目标状态。

探索轨道启动前若没有 active 阶段，工作流返回阶段草案并阻止启动。用户确认后先用短 stable 生命周期创建阶段；用户明确不需要阶段时，用 `--without-stage-reason` 记录理由。

科研资料只放在 `resources/source|data|theory|analysis|outputs|others|reports/` 并通过 `catalog` 登记。工作台内容通过 `workbench item/package` 建立索引；不保存凭据或绝对路径，不自动执行、安装、启动或联网检查。Markdown 公式使用 Markdown/LaTeX 语法。

只有证据完整且状态为 `validated` 的探索可准备 Squash PR；合并等待用户明确确认。

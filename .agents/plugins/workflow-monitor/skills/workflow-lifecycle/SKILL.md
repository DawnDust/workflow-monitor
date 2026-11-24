---
name: workflow-lifecycle
description: >-
  Use when starting, reporting, verifying, or finishing tasks in a Workflow
  Monitor repository, or when opening the standalone desktop Dashboard.
---

# Workflow Monitor 任务生命周期

## 日常执行顺序

1. 新会话或上下文不清时先读取精简上下文：
   `.\workflow-monitor.exe context --view brief --format markdown`
2. **只读咨询与审查**不运行 `start`，也不运行 `end`。
3. **首次写入前**运行 `start`（主项目与链接工作区统一调用主目录入口）：
   - 稳定维护（仅限 `main`）：`start --track stable --kind code --scope "..." --acceptance "..." --compact`
   - 探索/实验/研究：`start --track experiment --topic <slug> --kind code --scope "..." --acceptance "..." --compact`
   - 无法确定轨道时必须在对话中询问用户，不得静默选择。
   - 探索启动返回 `worktree` 路径后，后续读写与 `--project <worktree>` 均在工作区内执行。
4. 阶段完成、判断变化或代码批次后运行 `report`（可加 `--verify fast` 与 `--compact`）；复杂填报使用 `--input-json <文件或 ->`。
5. 结束前检查门禁：`context --view verification --format markdown`，随后运行 `end --result completed --note "..." --current-step "..." --compact`（关联篇章需填 `--stage-review`）。
6. 中断恢复运行 `task recover`；受阻使用 `end --result blocked ...`；仅在用户明确确认时才运行 `task abandon`、`git push` 或合并。

## 查看独立桌面看板

- 无参运行或双击项目根目录 `workflow-monitor.exe` 即可打开独立 WebView2 只读看板。
- 需要从终端或对话协助打开时运行：`Start-Process .\workflow-monitor.exe`（切勿直接无参同步阻塞执行 `workflow-monitor.exe` 获取上下文）。

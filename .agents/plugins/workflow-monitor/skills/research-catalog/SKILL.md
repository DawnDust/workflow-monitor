---
name: research-catalog
description: >-
  Use when creating, moving, organizing, or reconciling research materials in
  resources/ or placing code and outputs in program/.
---

# 科研资料与目录归属

1. **资料操作前先查目录索引**：运行 `.\workflow-monitor.exe catalog folder index` 确认现有目录用途；新增自定义目录先用 `catalog folder add` 登记。归属不明确时询问用户。
2. **严格区分 `resources/` 与 `program/`**：
   - `resources/sparks/`：低约束自由 Markdown 灵感，可选登记。
   - `resources/plans/`：Sparks 形成的可执行下一步或研究方案。
   - `resources/tutorials/`、`resources/translations/`：文献加工与学习材料。
   - `program/`：新建程序、实验代码、Notebook、依赖及运行输出统一放在这里，**不要放入 `resources/`，也不要登记为资料**。
3. **路径失效与移动**：移动已登记资料时保留原 catalog ID 并更新路径；路径失效先运行 `catalog reconcile` 检查内容哈希匹配，唯一匹配再加 `--apply` 修复。

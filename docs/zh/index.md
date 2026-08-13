# Workflow Monitor 用户手册

Workflow Monitor 是面向 Windows 科研 Git 项目的本地仪表盘，用于集中查看 AI 协作中的当前目标、任务生命周期、科研探索、研究篇章、资料、诊断和发布状态。

![Workflow Monitor 项目概览](../assets/workflow-monitor-overview.png)

## 核心保证

- 图形仪表盘对项目业务数据保持只读。
- 不开放服务器端口，不使用 CDN、遥测或自动诊断上传。
- 永久历史保存在追加式项目文件中；SQLite 只是可重建的本地查询投影。
- 切换界面语言不会翻译用户填写的项目内容或数据记载。
- 破坏性或高风险生命周期操作必须在 AI 对话中获得明确确认。

建议先阅读[快速开始](getting-started.md)，再了解[仪表盘](workbench.md)以及[稳定维护与科研探索](lifecycle.md)的区别。

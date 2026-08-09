# 维护者指南

修改仓库前先阅读根目录[贡献指南](https://github.com/DawnDust/workflow-monitor/blob/main/CONTRIBUTING.md)和 `AGENTS.md`。

## 架构

Python 应用分为 core、application、infrastructure 和 UI 四层，依赖方向向内；UI 不直接拥有持久化或系统进程能力。除非另有明确批准的迁移方案，否则公共 CLI、WebView2 bridge、事件 Schema 3 和既有项目数据必须保持兼容。

## 文档

英文是默认站点，简体中文在 `docs/zh/` 中对等维护。两种语言保持相同的页面名和导航。系统界面文字可以翻译，但包含项目内容或数据记载的示例必须保持原文。

本地构建：

```powershell
python -m pip install -r requirements-docs.txt
python -m mkdocs build --strict
```

不要提交生成的 `site/` 目录。

## 发布准备

同步更新程序版本和更新日志，运行 fast、full 和严格文档构建，最后运行 release 测试。`v*` 标签会触发 Windows 发布工作流，但创建标签或 Release 必须获得维护者明确批准。

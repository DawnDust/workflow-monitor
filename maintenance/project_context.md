# 项目稳定背景

## 项目目的

作为可直接克隆的项目模板，为后续项目提供清晰的内容分区，以及仓库内生命周期、交接、永久变更档案与 Git pre-commit 门禁。

## 范围与非目标

- 范围：项目内容目录、`project_hooks`、`.githooks`、`.codex` 标记和 `maintenance` 文档。
- 非目标：安装 Codex 插件、注册用户级/系统级 Hook、修改全局 Git 配置或自动推送远程。

## 架构与关键路径

| 路径 | 职责 |
|:---|:---|
| `.codex/project-maintenance-workflow.json` | 项目维护工作流的启用标记与策略 |
| `project_hooks/` | 生命周期、检查、项目 Hook 启用和安全自动提交 |
| `.githooks/pre-commit` | 随仓库克隆的 Git 提交门禁入口 |
| `maintenance/` | 稳定背景、当前交接、决策与永久变更档案 |
| `source/` | 外部原始资料与来源证据 |
| `data/` | 原始、过程和处理后数据 |
| `theory/` | 理论框架、假设、定义和推导 |
| `analysis/` | 分析代码、Notebook、实验与过程记录 |
| `outputs/` | 报告、图表、模型和可交付成果 |

## 标准命令

```text
构建：无需构建
测试：python -m project_hooks check
安装项目 Hook：python -m project_hooks install
状态：python -m project_hooks status
```

## 项目约定

- 所有维护机制必须保留在仓库内；禁止依赖全局 Codex Hook 或全局 Git Hook。
- 普通 `git clone` 不复制 `.git/config`；新克隆首次使用时必须运行一次 `python -m project_hooks install`。
- 原始资料不覆盖，分析过程与交付成果分开，同一文件只保留一个权威位置。
- 通用文件名优先使用 `YYYYMMDD_topic_v01.ext`；目录规模增大后再增加子层级。
- `maintenance/README.md` 保留标准大写名称；其余维护文件使用小写 `snake_case.md`。

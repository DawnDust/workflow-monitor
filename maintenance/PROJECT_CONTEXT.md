# 项目稳定背景

## 项目目的

作为可直接克隆的项目维护模板，为任意后续项目提供仓库内生命周期、交接、永久变更档案与 Git pre-commit 门禁。

## 范围与非目标

- 范围：项目内 `project_hooks`、`.githooks`、`.codex` 标记和 `maintenance` 文档。
- 非目标：安装 Codex 插件、注册用户级/系统级 Hook、修改全局 Git 配置或自动推送远程。

## 架构与关键路径

| 路径 | 职责 |
|:---|:---|
| `.codex/project-maintenance-workflow.json` | 项目维护工作流的启用标记与策略 |
| `project_hooks/` | 生命周期、检查、项目 Hook 启用和安全自动提交 |
| `.githooks/pre-commit` | 随仓库克隆的 Git 提交门禁入口 |
| `maintenance/` | 稳定背景、当前交接、决策与永久变更档案 |

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

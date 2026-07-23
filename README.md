# Project Maintenance Template

这是一个只使用项目内 Hook 的可克隆维护模板，不需要安装 Codex 插件，也不会创建或使用全局 Codex/Git Hook。

## 开始使用

```powershell
git clone <repository-url> <new-project>
Set-Location <new-project>
python -m project_hooks install
python -m project_hooks check
```

普通 `git clone` 出于安全原因不会复制 `.git/config`，因此每个新 clone 或 worktree 需要运行一次 `install`。该命令只为当前仓库设置 `core.hooksPath=.githooks`；Hook 文件本身已在版本控制中。

之后由仓库内 [AGENTS.md](./AGENTS.md) 约束 Codex：每次任务先通过 `context` 读取 SQLite 动态上下文并执行 `start`，通过结构化命令更新状态，最后执行 `end`。

`main` 只保存稳定内容。新理论、算法、实验或不确定改动从干净的 `main` 启动探索任务；验证成功后通过 Squash PR 合并，失败、暂停或不可判决的尝试保存在 `archive/` 分支。完整规则见 [maintenance/README.md](./maintenance/README.md)。

## 项目结构

| 目录 | 放什么 |
|:---|:---|
| `source/` | 论文、网页存档、需求、图片等外部原始资料 |
| `data/` | 数据集、表格及数据处理过程文件 |
| `theory/` | 理论框架、假设、定义、公式与推导 |
| `analysis/` | 分析代码、Notebook、实验和过程记录 |
| `outputs/` | 报告、图表、模型及其他可交付成果 |
| [maintenance/](./maintenance/) | 项目状态、决策、交接与永久变更档案 |

前五个内容目录不使用占位文件，首次存放内容时按需创建。简单规则：原始资料不覆盖，过程与成果分开；文件名优先使用 `YYYYMMDD_topic_v01.ext`，同一文件只保留一个权威位置。

## 常用命令

```powershell
python -m project_hooks install
python -m project_hooks check
python -m project_hooks status
python -m project_hooks branch-status
python -m project_hooks context --format markdown
python -m project_hooks db verify
python -m project_hooks dashboard
```

动态维护数据由 `.project_hooks/maintenance.sqlite3` 查询和事务管理；Git 只跟踪追加式 `maintenance/events.jsonl`。数据库缺失、损坏或切换分支后会从事件日志重建。

`context` 和 `dashboard` 将持久化进度显示为“工作断点”，并从本地 HEAD 与 `origin/main` 跟踪引用实时生成“真实断点”；不会自动联网刷新。任务发布状态同样从关联提交推导，不需要发布专用任务或事件。

`dashboard` 使用 Python 自带的 Tkinter 打开只读桌面窗口，日常仅保留概览、搜索、任务、时间线和记录五个分页；任务列表默认只显示时间、任务、结果与状态，完整信息位于详情。原始事件与数据库技术信息集中在独立的“高级查看”窗口。窗口可跨记录搜索和双向定位，提供最近任务、失败探索与未合并分支快捷筛选；时间线超过 50 个提交时自动折叠旧记录。窗口不会修改维护数据或执行网络操作。可使用 `--refresh-seconds 0` 关闭自动刷新，或用 `--branch <name>` 查看指定分支流。

项目维护文档索引见 [maintenance/README.md](./maintenance/README.md)。

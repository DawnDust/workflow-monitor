# Project Maintenance Template

这是一个可克隆的项目维护模板，只使用仓库内 Hook、SQLite 和追加式事件日志，不依赖全局 Hook 或插件。

## 开始使用

```powershell
git clone <repository-url> <new-project>
Set-Location <new-project>
python -m project_hooks install
python -m project_hooks check
```

新 clone 或 worktree 需要执行一次 `install`。之后无参数运行 `python -m project_hooks` 即可查看当前行动概览。

## 日常入口

| 命令 | 用途 |
|:---|:---|
| `python -m project_hooks context --format markdown` | 读取完整动态上下文 |
| `python -m project_hooks start ...` | 开始任务生命周期 |
| `python -m project_hooks end ...` | 完成任务并收尾 |
| `python -m project_hooks dashboard` | 打开只读管理窗口 |
| `python -m project_hooks catalog list` | 查询文献、数据、理论、模拟和输出索引 |

使用 `python -m project_hooks --help-all` 查看高级命令及分类。

## 项目目录

| 目录 | 内容 |
|:---|:---|
| `source/` | 外部原始资料与来源证据 |
| `data/` | 原始、过程和处理后数据 |
| `theory/` | 理论、假设、定义和推导 |
| `analysis/` | 分析代码、Notebook 和实验 |
| `outputs/` | 报告、图表、模型和交付成果 |
| `maintenance/` | 维护规范与永久事件记录 |

前五个目录按需创建，不使用占位文件。完整生命周期、分支策略、高级命令和记录模型见 [maintenance/README.md](./maintenance/README.md)。

科研资料文件仍保存在上述目录，SQLite 只保存索引、简短说明、标签和关系。使用
`catalog ingest <文件>` 一步复制并登记单个文件，使用 `catalog scan` 扫描项目文件，
也可在 Dashboard 资料页一键复制扫描提示词，再粘贴到 Codex 对话中执行扫描登记。
使用 `catalog add|update|bulk-update|link` 补充信息，使用
`catalog context` 直接向终端输出供 AI 使用的 Markdown 或 JSON 上下文。

# 探索分支维护方法

## 稳定主线

`main` 只接收文档整理、Hook 维护、已确认小修和验证后成果。新理论、算法、实验及结果不确定的改动不得直接在 `main` 尝试。

## 分支类型

| 分支格式 | 用途 |
|:---|:---|
| `research/<topic>` | 理论、模型和知识路线探索 |
| `experiment/<topic>` | 可验证的实验、数据或算法尝试 |
| `sandbox/<topic>` | 方向尚未稳定的短期试验 |
| `archive/<type>/<topic>` | 失败、暂停或不可判决尝试的长期保留 |

`topic` 只使用小写字母、数字和连字符。一个探索分支对应一个 `maintenance/attempts/<task-id>.md`；同一分支上的后续任务复用该记录。

## 启动与继续

从干净且无活动任务的 `main` 启动：

```powershell
python -m project_hooks start 20260722_example_001 --kind analysis --scope "..." --acceptance "..." --track research --topic example
```

命令会检查 detached HEAD、脏工作树、非法 topic、同名本地或远程分支以及本地 `main` 与已知 `origin/main` 的一致性；全部通过后才创建分支和尝试记录。已经位于有效探索分支时，可省略 `--track` 和 `--topic` 继续该尝试。

## 结束与判决

探索任务结束时必须显式选择状态：

- `active`：仍需继续。
- `validated`：验收成立，证据与结论已填写，可准备 PR。
- `negative`：结果否定原假设。
- `inconclusive`：证据不足，暂不可判决。
- `paused`：主动暂停，保留现场。

只有 `validated` 可运行 `python -m project_hooks prepare-pr`。该命令要求干净工作树、完整证据、已完成维护归档，并基于最新 `main`，只输出 Squash PR 的标题和正文。Codex 随后可推送并创建 Draft PR，但必须等待用户明确确认才能 Squash Merge；冲突只在探索分支解决。合并后删除原探索分支。

`negative`、`inconclusive` 或 `paused` 使用 `python -m project_hooks archive-attempt` 改名为 `archive/<type>/<topic>`。推送归档分支后回到 `main` 启动稳定治理任务，只把结论、证据链接和归档分支写入 `exploration_log.md`，不得把探索内容合并进 `main`。

## 网络边界

`project_hooks` 不自动 push、不创建 PR、不删除远程分支，也不自动合并。所有网络操作由 Codex/GitHub 流程显式执行。

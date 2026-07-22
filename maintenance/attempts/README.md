# 探索尝试记录

每个探索分支首次启动时自动创建唯一的 `<task-id>.md`，后续任务复用同一文件。记录必须包含目标、假设、验收标准、基线提交、证据、结论和处置状态。

不要手工复制模板创建记录；使用 `python -m project_hooks start ... --track <type> --topic <slug>`，让 Hook 在通过分支安全检查后生成。

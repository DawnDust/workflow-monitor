# 统一工作流与并行工作区

项目根目录的 `workflow-monitor.exe` 是统一入口。左栏的「工作流」显示主线生命周期和交付状态。「工作区」汇总并行状态、最近更新时间和未提交改动，原地展开查看目录及冲突来源。「搜索」可按工作区或分支查看任务、验证回执、研究篇章和已登记资料。

## 开始和继续

在主工作区提交初始基线后，从共享入口开始探索。

```powershell
.\workflow-monitor.exe start --kind analysis --scope "验证模型" --acceptance "记录对照结果" --track experiment --topic active-abp --stage none --without-stage-reason "独立验证"
```

工具返回工作流 ID、任务 ID 和工作区路径。工作区位于 `.worktrees/<首任务ID>-<主题>`，独立检出分支文件并共享 Git 历史。主线的稳定任务可以同时运行。

```powershell
.\workflow-monitor.exe --project "<工作区路径>" report --current-step "完成第一组对照"
.\workflow-monitor.exe start --workflow "<工作流ID>" --kind analysis --scope "继续对照" --acceptance "保存第二组结果"
.\workflow-monitor.exe workflow show "<工作流ID>"
```

原任务结束后，后续任务可复用分支和目录。暂停或阻塞的任务使用 `task resume` 继续。使用 `status --all` 查看项目进展，`conflict list/show` 核对冲突来源。研究判断与结论取舍由用户确认。

## 升级与收尾

旧项目在主目录运行 `update --from-exe "<新版EXE>" --check`，查看旧工作区的移动目标和阻塞原因。相关任务全部结束后，确认并执行升级。迁移会保存未提交文件、任务 ID 和日志，通过 Git 移动工作区。失败后用 `worktree migrate --check` 检查进度，确认后用 `worktree migrate --apply` 续办。共享程序的更新在主项目执行一次。

通过现有验证和发布流程处理分支。已验证的探索在确认合入主线后归档。负面或未决探索可在保存证据后直接归档。先执行 `workflow close <工作流ID> --cleanup` 查看清理预览，检查未跟踪或被忽略的实验输出，再在确认后添加 `--confirmed`。省略 `--cleanup` 可只归档。归档保留任务证据和分支。清理后可以在「工作区」的已归档筛选找到该工作流，并从「搜索此工作区」查看保存的回执。

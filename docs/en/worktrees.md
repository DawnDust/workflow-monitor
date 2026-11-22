# Project workflows and parallel workspaces

Open the project's `workflow-monitor.exe`. **Workflow** shows the main checkout's lifecycle and delivery status. **Workspaces** lists live status, the last update and uncommitted changes. Expand a row to see its path and conflict sources. **Search** filters tasks, verification receipts, research chapters and registered resources by workspace or branch.

## Start and continue work

Commit an initial baseline on `main` before starting an exploration from the shared entry point.

```powershell
.\workflow-monitor.exe start --kind analysis --scope "Verify the model" --acceptance "Record a comparison" --track experiment --topic active-abp --stage none --without-stage-reason "Independent verification"
```

The response gives the workflow ID, task ID and workspace path. Git checks out branch files separately under `.worktrees/<first-task-ID>-<topic>` and shares repository history. Stable tasks can run on the main checkout at the same time.

```powershell
.\workflow-monitor.exe --project "<workspace-path>" report --current-step "Finished the first comparison"
.\workflow-monitor.exe start --workflow "<workflow-ID>" --kind analysis --scope "Continue the comparison" --acceptance "Save the second result"
.\workflow-monitor.exe workflow show "<workflow-ID>"
```

A later task can reuse a workflow's branch and workspace after the previous task ends. Resume paused or blocked tasks with `task resume`. Use `status --all` for project progress and `conflict list/show` for conflict sources. The user confirms research decisions.

## Upgrade and close workflows

From the main project, run `update --from-exe "<new-executable>" --check` to review proposed workspace moves and blockers. Finish all outstanding tasks, then apply the confirmed upgrade. Migration uses Git to move workspaces and preserves unpublished files, task IDs and journals. Use `worktree migrate --check` to inspect an interrupted move and `worktree migrate --apply` to resume it. Upgrade the shared program once from the main project.

Use the existing validation and integration workflow. Verified explorations can be archived after confirmed integration. Negative or inconclusive explorations can be archived after saving their evidence. Run `workflow close <workflow-ID> --cleanup` to preview cleanup and check untracked and ignored outputs, then add `--confirmed` once approved. Omit `--cleanup` to retain the checkout. The archive retains the branch and task evidence. Use the Archived filter in Workspaces and the Search this workspace button to find saved receipts.

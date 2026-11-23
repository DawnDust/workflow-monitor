# 安装、恢复与交付

- 新 clone 在主项目根目录放置 EXE 并执行 install。项目内的工作区由统一入口管理。
- 旧项目升级先预览 worktree migrate --check，确认未结束任务和迁移冲突已处置，再通过升级流程迁入 .worktrees/。
- 清理前先运行 workflow close <ID> --cleanup 查看预览，确认后再带 --confirmed 执行。
- 升级：在无活动任务、干净且同步的 main 上运行 `.\workflow-monitor.exe update`。首次写入 Schema v4 后不可降级到仅支持 v1-v3 的版本。
- 恢复：异常中断运行 `task recover`；明确放弃运行 `task abandon --reason <原因>`。
- 旧程序目录：运行 `program migrate-analysis` 预览；确认目标无冲突后在 main 稳定任务中加 `--apply` 迁入 `program/`。
- 诊断：使用 `diagnostics status/export`；诊断不自动上传。
- 验证：完成一批相关代码修改后使用 report --verify fast；结束时自动补齐同一软件输入指纹的回执（软件改动 fast/full，release profile 为 fast/release；纯文档和治理改动使用内置检查），正常收尾使用 `end` 内置检查，不重复运行独立 `check`；专项诊断可按需检查，涉及持久化或本机交付时运行 `db verify`。
- 交付：Dashboard 只读。提交、推送、PR、合并、正式 EXE 构建和发布分别等待用户确认。

## 本地测试 EXE 交付

- 收到测试 EXE 时分别核对源码同步、实际运行构建和项目模板升级。源码仓库先比较构建指纹；一致则报告已一致，无需源码修改，不一致须有对应提交或补丁。科研项目使用 `update --from-exe <路径> --check` 预览，再在授权范围内实际升级；文件替换不等于模板升级。存在定制冲突时报告需要处理，不能笼统宣称更新完成。测试构建提供 `build-notes`，构建成功不代表功能测试通过。

测试版使用 `build-notes --format markdown|json` 读取内嵌说明，构建同时生成独立说明文件。实际升级仍要求无活动任务、干净且同步的 main，保留定制文件；冲突需要处理，不采用强制覆盖。测试构建成功和功能测试通过分别说明。

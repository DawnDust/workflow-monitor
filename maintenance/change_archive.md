# 永久变更档案

这里只保留每次维护探索或任务的最小永久记录。成功、失败、阻塞、撤销和不可判决不分表，禁止静默删除。

| 探索简单总结 | 任务/批次 | 任务或产物时间（Asia/Shanghai） | 时间证据 | 状态摘要 |
|:---|:---|:---|:---|:---|
| 将 Project Maintenance Hooks 插件源码发布到 GitHub 私有仓库 | 20260719_publish_plugin_001 | 2026-07-19 01:00:28 | Git 提交 `d73192b1cc792fd627613457cef1085b37e82cfb` 与远程 `main` 一致 | 成功；16 个文件、内置测试 4/4 通过，敏感信息扫描无异常 |
| 将当前工作区改为无需插件和全局 Hook 的可克隆项目维护模板 | 20260722_project_hooks_only_001 | 2026-07-22 17:04:48 | 新仓库冒烟测试中 `core.hooksPath=.githooks`，未归档提交被门禁拒绝；全局 Hook 搜索无残留 | 成功；项目检查通过，旧插件已卸载，本地模板等待选择 GitHub 发布目标 |
| 创建并发布私有模板仓库 `DawnDust/project-maintenance-template` | 20260722_publish_template_001 | 2026-07-22 17:16:34 | `gh repo create` 返回 `Resource not accessible by personal access token`；内置浏览器连接 GitHub 失败 | 阻塞；本地已切换 `main` 并配置目标 `origin`，远程仓库尚未创建、没有提交或推送 |
| 恢复私有模板仓库发布 | 20260722_publish_template_002 | 2026-07-22 17:25:21 | GitHub CLI OAuth 权限包含 `repo`；私有空仓库创建成功 | 进行中；准备提交并推送 `main`，随后从远程 clone 验证 |
| 完成私有模板仓库发布与远程验证 | 20260722_publish_template_002 | 2026-07-22 17:37:29 | 远程 clone 得到 `2cef663488836c898e64d7c083022bccc1efc872`；`install`、`check` 和 `.githooks` 配置通过 | 成功；仓库为 private template，默认分支 `main`，首次发布与 clone 验证完成 |
| 优化模板工作区内容架构 | 20260722_optimize_structure_001 | 2026-07-22 18:24:07 | `source/`、`data/`、`theory/`、`analysis/`、`outputs/` 及各自说明存在；项目检查通过 | 成功；根目录提供一览表和统一命名规则，目录保持扁平且可按规模扩展 |
| 统一维护文档文件名为小写 | 20260722_lowercase_maintenance_001 | 2026-07-22 18:28:31 | 6 个 `maintenance/` 文件均通过严格小写检查；全仓库无旧大写路径引用；项目检查通过 | 成功；配置、生命周期代码、AGENTS 和 Markdown 链接全部同步为小写路径 |
| 保留维护索引的标准 README 文件名 | 20260722_keep_readme_case_001 | 2026-07-22 18:54:01 | `maintenance/README.md` 存在；根目录链接和生命周期检查使用相同大小写 | 成功；仅 README 恢复标准名称，其余维护文件保持小写 `snake_case.md` |
| 发布工作区架构与维护文件命名优化 | 20260722_publish_structure_001 | 2026-07-22 19:03:22 | 项目检查与暂存区格式检查通过；目标为 `origin/main` | 进行中；准备提交并推送主体改动 |
| 完成工作区架构与维护文件命名优化发布 | 20260722_publish_structure_001 | 2026-07-22 19:04:02 | 本地与远程 `main` 均为 `98345f53c9e1f7f38a49aeec217f5b9738360b8f` | 成功；五个业务目录、维护文件命名和 README 例外已发布 |

# Changelog

## 1.4.1

- 增加项目级跨进程单写者锁、可恢复活动任务 sidecar 与幂等 `end`。
- 增加 `task recover`、`task abandon`、构建身份及冻结 EXE UTF-8 输出保护。
- 增加本地脱敏诊断导出、Dashboard Bug 报告入口与分层并行测试。
- 精简 Dashboard 阶段展示，并在任务完成但阶段未更新时给出非阻塞提醒。

## Unreleased

- 新增本地轮转、脱敏且不自动上传的诊断记录，以及 CLI/Dashboard 诊断 ZIP 导出和 GitHub Bug 报告入口。
- 为错误与冲突增加稳定分类、事件编号和安全下一步提示，Dashboard 刷新失败继续保留上次正常数据。
- 新增 fast/full/release 分层并行测试入口、分支覆盖率门禁及 PR/main 持续集成。

## 1.3.0

- 以结构化“阶段”页替代 Git DAG 时间线，移除提交搜索和提交跳转。
- 新增 `project show/update` 与 `stage list/show/start/update`，并为探索记录增加当前步骤、进展和下一步。
- 项目概览按项目资料、当前大阶段、当前执行、探索和资料分区显示，保留轻量发布状态判断和旧事件兼容。
- 启动事件投影失败时原子恢复事件日志、活动任务和临时探索分支；只读入口主动识别并重建结构不完整的 v3 数据库。
- 初始化固定创建 `resources/` 七类目录，资料命令与检查强制路径和索引一致，并提供旧布局安全迁移命令。
- Dashboard 资料页新增目录健康入口并精简文件控件；资料类型增加 `other`、`report`，工作台增加对外项目总结提示词。

## 1.2.0

- Dashboard 顶部新增当前 EXE/项目版本显示和非阻塞“检查更新”按钮，并移除已由原生流程覆盖的五项软件提示词。
- 收敛为项目目录内 Windows EXE-only，不再发布或支持 pipx、wheel 和 core ZIP。
- 将升级运行时移入项目自身的 `.project_hooks/runtime/`，Release 仅包含 EXE 和自动读取的 manifest。
- 新增 Windows x64 单文件 `project-hooks.exe`，支持空文件夹首次双击自动建仓、初始化并打开 Dashboard。
- 同一 EXE 保留完整 CLI 和 Git Hook 能力，并通过校验后的版本化 EXE 缓存完成便携升级。
- GitHub Release 同时发布 Windows EXE，并在 manifest 中记录下载地址和 SHA-256。

## 1.1.0

- 提升 Windows 升级可靠性：替换版本化核心目录遇到短暂文件占用时进行有限重试。
- 保持持久权限错误显式失败，避免静默掩盖无法完成的核心切换。

## 1.0.3

- 修复 GitHub Release 工作流中版本一致性校验的 Bash/Python 引号错误。
- 使用可读的 heredoc 校验标签、包和 `pyproject.toml` 三处版本，避免嵌套转义。

## 1.0.2

- 修复普通 CLI 在 Windows 英文系统传统代码页下输出中文时的编码失败。
- 统一 CLI 子进程测试的 UTF-8 解码，并兼容 Windows 8.3 短路径与规范路径的差异。

## 1.0.1

- 修复 Windows 英文环境中升级子进程无法输出中文 JSON 的编码错误。
- 启动器和升级器显式使用 UTF-8，并增加非 UTF-8 父环境回归测试。

## 1.0.0

- 将项目内复制源码的模板改为可通过 `pipx` 安装的全局工具。
- 增加 `init`、`update`、`version` 和公共 `--project` 接口。
- 增加 GitHub Release 核心包校验、版本化缓存、项目迁移与失败回滚。
- 增加受管文件冲突保护、安装清单和升级审计事件。
- 保持既有任务、探索、Dashboard、资料索引和事件 schema 1/2 兼容。

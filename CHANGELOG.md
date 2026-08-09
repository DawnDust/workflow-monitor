# Changelog

## Unreleased

No changes yet.

## 1.6.0

- Replaced the unrelated crafting-table icon with the original Workflow Beacon identity: three connected workflow nodes and a local status beacon, delivered as reproducible SVG, PNG, multi-size Windows ICO, favicon, wordmark, and social preview assets.
- Added a bottom-left Settings entry with Language, Explanations, and Advanced view sections; Simplified Chinese and English system text switches immediately and persists only in local WebView2 storage, while user content and recorded data remain unchanged.
- Added a concise bilingual public README, a bilingual MkDocs user and maintainer manual, an MIT license, community governance files, structured Issue/PR templates, documentation CI, Pages deployment, and dependency update configuration.
- Renamed public repository links from the legacy template address to `DawnDust/workflow-monitor` across downloads, updates, diagnostics, manifests, and support entry points.

- Web 工作台 2.3 为版本主线直接显示建立目标与最终判断，删除独立探索对比页，并让高级查看折叠在差量刷新时保持稳定。
- 资料页改为七类标准目录的导航与定位中心；WebView2 主窗口显示后会立即隐藏启动壳并在退出时清理。

- Web 工作台 2.2 修复解释折叠自动重开和中文搜索输入被后台刷新打断的问题，并增加任务、探索、决策和资料快捷筛选。
- 科研地图改为 v5-v19 主目标版本演进线，以聚合任务、探索和严格筛选的关键文件替代全量孤立节点。

- WebView2 现为唯一图形工作台；无参数 EXE 直接启动 Web，并移除 `dashboard`、`--ui` 与 legacy 回退。
- 补齐实时生命周期、资料目录/文件打开、完整双语解释、10 条分页搜索，以及高级查看中的折叠动作、诊断和原始事件。

- 探索改为严格按分支聚合：分支名作为稳定探索 ID，同一分支内的多个任务和提交只更新一条探索记录，旧事件在数据库重建时自动折叠且仍保留原始审计；搜索页同时显示进行中、待合并、已合并、待归档或已遗弃的处置状态。
- 工作台分类升级为互斥主类型、受控多用途和自由标签；新增待复核状态，旧六类索引与 v1 包在升级或导入时保守迁移到 v2。
- Web 工作台支持主类型与科研用途组合筛选，复制给 Codex 的来源头会显示分类、用途、标签和复核状态。
- 工作台分类界面改为左侧主类型导航与顶部用途多选下拉；窄屏使用主类型下拉并保持表格横向可读。

- 工作台移除所有内置科研提示词，改为只读索引用户创建或外部导入的外置工具、提示词、科研流程、理论验证方法、常用理论和其他 Markdown 条目。
- 新增安全的工作台包预览、导入和导出，保留来源、作者、版本与内容哈希；内容仅在用户明确选择后复制给 Codex，不进入日常上下文，也不会自动执行、安装或联网。
- 既有 `workbench external` 事件和命令继续兼容，SQLite Schema 仍为 3。

- 新增实验性 Web Dashboard 2.0：`dashboard --ui auto|web|legacy`、本地 WebView2 白名单桥接、随单 EXE 打包的原生 Web 静态资源，以及初始化失败时的显式 Tkinter 回退；探索阶段 `auto` 仍保持旧界面。
- 新增科研地图、探索对比和证据矩阵；显式 catalog 关系与过程派生关系分开呈现，证据矩阵只接受已登记的 `supports`、`validates`、`contradicts`，未登记单元格不做负面推断。
- 增加桥接契约、路径限制、刷新合并、最后正常快照、Runtime 回退、前端无浏览器状态转换、静态资源打包与冻结 EXE bridge-ready 测试。

- 将对外产品名更新为 **Workflow Monitor**，Windows 可执行文件更名为 `workflow-monitor.exe`，并加入工作台图标与版本资源。

## 1.5.0

- 将 Dashboard 收敛为只读观察台，统一展示当前任务、阶段、探索、动作可用性和历史。
- 修复动作状态缓存失效导致的启动假死；窗口先显示，Git、SQLite、版本和刷新全部在后台单飞执行。
- 新增单快照动作可用性矩阵、差量页面更新和四态只读动作提示。
- 将独立记录页并入工作流，以任务、动作、阶段、探索和决策五类折叠树避免内容混杂；动作完整保留四种可用状态。
- 资料页改为标准目录折叠索引，展开目录时才加载条目，并在右侧显示只读详情。
- 生命周期条增加工作区、提交、推送和正式 Release 四项独立状态；恢复概览中的总体状态、判决、断点、阻塞、下一步和最近完成信息，并沿用分割线布局。
- 将全局搜索控件移入“搜索”页并移除快捷筛选；恢复用户触发的脱敏诊断 ZIP 导出，新增中英文状态“解释”页。
- 新增独立“诊断”页，按故障指纹展示当前问题、导出批次覆盖和清理回执；升级成功后分级清理旧版本的普通诊断并保留内部与数据完整性问题。
- 工作台改为“内置 / 外置”折叠树，新增追加式外置工具登记、更新、暂停、恢复和停用命令，不改变 SQLite Schema 3，也不自动运行外部程序。
- 将简洁 AI 生命周期规则内置到项目规范；用户无需复制命令或长提示词，高风险动作在对话中确认。
- CLI、事件 Schema 3、sidecar、SQLite 投影和本地 Git 安全边界保持兼容。

## 1.4.1

- 增加项目级跨进程单写者锁、可恢复活动任务 sidecar 与幂等 `end`。
- 增加 `task recover`、`task abandon`、构建身份及冻结 EXE UTF-8 输出保护。
- 增加本地脱敏诊断导出、Dashboard Bug 报告入口与分层并行测试。
- 精简 Dashboard 阶段展示，并在任务完成但阶段未更新时给出非阻塞提醒。

## Historical development notes

- Dashboard 将软件交付移出主窗口并收纳到“高级查看”：显示 EXE 与仓库源码一致性、最近正式 Release 的版本与时间，以及 Release 后尚未发布的软件文件修改；左上角明确标注稳定或探索分支。
- Dashboard 有未提交修改时不再重复显示任务文件数，也不再把仅有 HEAD 同步表述为修改已推送。

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

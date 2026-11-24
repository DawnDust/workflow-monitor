"""Project scaffolding, managed-file migration, and auditable upgrades."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from ... import EXECUTABLE_NAME
from ..config_paths import (
    PRIMARY_CONFIG_PATH,
    PRIMARY_INSTALLATION_PATH,
    has_project_config,
    read_project_config,
    resolve_config_path,
    resolve_config_relative,
    resolve_installation_path,
    resolve_installation_relative,
)
from ..git.build_identity import build_identity
from ..git import client as git_client
from .resource_layout import PROGRAM_DIRECTORY, RESOURCE_DIRECTORIES, ensure_resource_directories
from ..persistence.store import (
    SCHEMA_VERSION,
    append_events,
    canonical_json,
    ensure_database,
    load_events,
    new_event,
    validate_projection,
)
from ..persistence.database import integrity_check, latest_active_task_id
from .runtime import write_pointer, cached_executable, legacy_runtime_root


TEMPLATE_VERSION = 21
INSTALLATION_PATH = PRIMARY_INSTALLATION_PATH
CONFIG_PATH = PRIMARY_CONFIG_PATH
AGENTS_BEGIN = "<!-- project-maintenance-hooks:begin -->"
AGENTS_END = "<!-- project-maintenance-hooks:end -->"
GITIGNORE_BEGIN = "# project-maintenance-hooks:begin"
GITIGNORE_END = "# project-maintenance-hooks:end"
GITATTRIBUTES_BEGIN = "# project-maintenance-hooks:begin"
GITATTRIBUTES_END = "# project-maintenance-hooks:end"

HOOK_TEMPLATE = """#!/bin/sh
# project-maintenance-hooks managed
if [ -x "./workflow-monitor.exe" ]; then
  ./workflow-monitor.exe pre-commit
else
  echo "缺少项目根目录的 workflow-monitor.exe，无法执行提交检查" >&2
  exit 1
fi
"""

AGENTS_BLOCK = """<!-- project-maintenance-hooks:begin -->
## 项目维护生命周期

- 项目生命周期操作和维护状态写入使用仓库根目录的 `.\\workflow-monitor.exe`；文件读取、搜索和测试使用相应工具。
- 在主项目保留一个 `workflow-monitor.exe` 入口。任务工作区通过主入口的 `--project <工作区路径>` 调用生命周期。
- 每次任务先运行 `.\\workflow-monitor.exe context --view brief --format markdown`，按 `core_read_order` 读取尚未掌握或已变更的规范，首次写入前运行 `start`。
- 纯只读咨询和审查不启动写入生命周期，也不调用 `end`；准备首次写入时再判断轨道。已启动的任务必须通过 `end` 或规定的放弃流程收尾。
- 用户只需用自然语言描述任务；AI 提取目标、验收和证据，任务 ID、时间、分支、状态令牌和安全默认值由工作流代码处理。
- 稳定维护只在 `main` 使用 `--track stable`；研究和实验使用独立探索分支；无法可靠判断轨道时必须在对话中询问用户。
- 一个探索分支只对应一条探索记录；同一分支的后续任务复用该记录，任务回执和 Git 提交仅作为探索内部的过程证据。
- 已启动的任务使用 `state update --current-step ...` 和 `attempt update` 保存进展，完成时运行 `end`。
- 完成一批相关代码修改后运行 fast；结束时程序按实际门禁自动补齐同一软件输入指纹的回执：软件改动要求 fast/full，release profile 要求 fast/release；纯文档和治理改动由 `end` 内置检查验证。
- 探索启动前必须有关联研究篇章或记录明确的无篇章理由；有关联篇章的任务结束时必须显式审阅篇章。
- 项目资料和研究篇章只通过 `project update` 与内部 `stage` 指令记录；新证据改变判断时用 `stage update --revision ... --summary ... --evidence ...` 追加修订；探索进度使用 `attempt update` 的结构化步骤字段。
- 科研资料放在 `resources/` 预设或已登记的自定义文件夹；文件夹通过 `catalog folder add/update/list/archive/restore` 登记（教程、翻译用于文献加工，plans 用于 Sparks 的下一步；登记文件夹不替代内部文件登记），模拟资料包除外。`resources/sparks/` 可自由保存 Markdown。
- 理论物理任务涉及研究方向与问题收窄、模型分析、理论推导、数值验证、反例压力测试或文献查新时，按需阅读 `maintenance/PHYSICS_RESEARCH_ROADMAP.md`。它只提供研究思路，不是强制阶段、验收项或门禁。
- 灵感和未成形问题归入 `sparks`，可执行的下一步或研究方案归入 `plans`，成型的来源解读和学习材料归入 `tutorials`。
- 新建程序、实验代码、Notebook、依赖和运行输出统一放在仓库根目录 `program/`；不要放入 `resources/`，不要登记为资料。程序仍由 Git 跟踪。
- 每次资料创建、编辑、导入、移动、重命名或删除前，重新读取 `catalog folder index`，按目录用途选择位置；移动已登记资料时保留原 catalog ID 并更新路径，分类无法可靠判断时先询问用户。新目录先创建并用 `catalog folder add` 登记名称和用途再刷新索引；输出导入显式指定 `--folder`，不自动重建顶层 outputs。
- 资料路径失效先运行 `catalog reconcile` 搜索可能的移动位置；唯一内容哈希匹配可通过 `--apply` 修复原登记，保留资料身份和关系。歧义、无历史哈希或范围外位置需要用户选择新位置或确认归档，不自动删除或归档。
- 只有 `validated` 尝试可准备 Squash PR；推送和合并必须由用户明确确认。成功探索合入 `main` 后，在 `main` 稳定任务中使用 `exploration archive-validated <分支>` 记录归档；失败、未决和暂停探索使用 `archive-attempt`。
- 禁止改写既有 `maintenance/events.jsonl` 行、直接编辑 SQLite、删除活动状态或绕过 `end`。
- 崩溃后运行 `task recover`；明确放弃时运行 `task abandon --reason <原因>`，不得手工删除 sidecar 或活动任务行。
- 遇到故障时使用 `diagnostics status/export` 生成脱敏本地诊断；程序不自动上传数据。
- Dashboard 只读展示任务、研究篇章、动作可用性和阻塞原因；生命周期写入由 AI 调用结构化服务，高风险动作必须在对话中取得用户确认。
- `start` 保存既有资料问题；AI 在 start/report/end 用 depends_on、deliverable 登记直接依赖和交付物（catalog:ID 或 path:相对路径）。无关且未恶化的既有资料问题保留待处理，不阻止 completed；任务交付物和直接依赖缺失仍阻止完成。全局 check 继续报告全部问题。
- 确实受阻时使用 `end --result blocked --blocker <原因> --evidence <证据> --next <恢复条件>`。检查失败保留活动任务，不默认 abandon；只有用户明确放弃才运行 task abandon。所有修复通过工作流命令或结构化服务追加事件，不直接编辑 SQLite 或既有事件，历史 abandoned 不改写。

## 多工作树并行

- 新项目默认统一管理工作区。旧项目升级前预览移动方案，所有未结束任务都需先结束。主树探索 start 自动返回工作流、任务和工作区路径。
- 每树最多关联一个未结束任务。任务结束后，后续任务用 `start --workflow <ID>` 复用分支和目录。暂停或阻塞时使用 `task resume`。
- `status --all` 与 Dashboard 汇总项目任务；`conflict list/show` 提供文件、篇章和资料的重叠来源。AI 比较证据并提出方案，研究判断及结论取舍由用户本人最终确认。
- 同 ID 同内容事件机械去重；分歧保留版本，不按更新时间选结论。清理、迁移和正式合并继续取得用户确认。

## AI 代理简短调用

- 默认读取 `context --view brief --format markdown`；只查结束门禁使用 `context --view verification`，追溯全部事实使用 `context --view full`。篇章和交接详情按需使用 `stage show`、`history`。
- `start` 可省略任务 ID，由工作流自动分配；`end` 可省略 ID，使用当前活动任务。两者及 `report` 可加 `--compact` 返回简短回执；复杂填报支持 `--input-json <文件或 ->`。
- 中途仅在阶段完成、判断变化、阻塞或交接时保存 checkpoint。短任务可通过 `end --current-step ... --next ...` 同时保存最终进展，不在结束前重复调用内容相同的 `state update`。
- 精简显示不改变测试、篇章审阅和授权门禁；失败与警告必须处理。不要用无参 EXE 获取上下文，无参冻结 EXE 会启动桌面界面。
- Antigravity 原生工作区插件位于 `.agents/plugins/workflow-monitor/`（提供按需 Skills 与 `SessionStart`/`PreToolUse` 护栏）；查看完整看板仍通过无参运行或双击 `workflow-monitor.exe` 打开独立 WebView2 窗口（可运行 `Start-Process .\\workflow-monitor.exe`）。
<!-- project-maintenance-hooks:end -->"""

GITIGNORE_BLOCK = """# project-maintenance-hooks:begin
.project_hooks/
__pycache__/
*.py[cod]
.pytest_cache/
/.coverage
/.coverage.*
.venv/
build/
dist/
/workflow-monitor.exe
/.worktrees/
/diagnostics-export/
# project-maintenance-hooks:end"""

GITATTRIBUTES_BLOCK = """# project-maintenance-hooks:begin
.githooks/* text eol=lf
.gitignore text eol=lf
.gitattributes text eol=lf
AGENTS.md text eol=lf
.agents/plugins/workflow-monitor/** text eol=lf
maintenance/*.md text eol=lf
maintenance/events.jsonl text eol=lf merge=union
# project-maintenance-hooks:end"""

MAINTENANCE_README = """# Workflow Monitor 项目维护索引

动态事实只写入 `maintenance/events.jsonl`，SQLite、Context、Dashboard、交接和研究篇章视图均为可重建投影。

- [CORE.md](CORE.md)：新会话、上下文丢失或规范变更时读取的生命周期、轨道、安全、测试和篇章契约。
- [RESEARCH.md](RESEARCH.md)：研究篇章、探索、Catalog 与 Sparks。
- [PHYSICS_RESEARCH_ROADMAP.md](PHYSICS_RESEARCH_ROADMAP.md)：理论物理研究路线参考；相关研究任务按需阅读，不作为门禁。
- [OPERATIONS.md](OPERATIONS.md)：安装、升级、恢复、诊断、交付与发布。

`maintenance/events.jsonl` 只追加且不得改写历史行；`.project_hooks/maintenance.sqlite3` 和测试回执均为本地生成物。
"""

MAINTENANCE_CORE = """# Workflow Monitor 核心契约

1. 每次任务先运行 `.\\workflow-monitor.exe context --view brief --format markdown`，新会话、上下文丢失或本文件变更时读取本文件，同一任务内无需重复读取未变规范；首次写入前运行 `start`。
   纯只读咨询和审查不启动写入生命周期，也不调用 `end`；准备首次写入时再判断轨道。已启动的任务必须通过 `end` 或规定的放弃流程收尾。
2. 稳定维护只在 `main` 使用 `--track stable`；不确定改动使用 `research|experiment|sandbox`。无法判断时询问用户。
3. 动态事实只记录一次：任务目标和验收来自 `task.started`，项目资料来自 `project.profile_updated`，任务进度来自 `task.checkpointed`，结束结果来自 `task.finished`。
4. 使用 `report` 一次填报进度和科研证据，程序自动保存 checkpoint；短任务可直接在 `end` 填报最终进展。
5. 完成一批相关代码修改后运行 `report --current-step ... --verify fast`；已通过的检查仅在新改动、失败或未解决问题影响其结果时重跑。结束时程序按实际门禁自动补齐同一软件输入指纹的回执：软件改动要求 fast/full，release profile 要求 fast/release；纯文档和治理改动由 `end` 内置检查验证。
6. 有关联研究篇章的任务必须在 `end --stage-review updated|reviewed-no-change` 明确审阅。`updated` 必须有本任务产生的篇章事件；新证据改变判断时以 `stage update --revision ... --summary ... --evidence ...` 追加修订。
7. 已启动的任务完成时运行 `end`。禁止改写既有事件、直接编辑 SQLite、删除活动 sidecar、在 main 试改、自动合并。
8. 推送、PR、合并、数据库重建、软件升级和正式发布必须取得用户授权。
"""

MAINTENANCE_RESEARCH = """# 科研与研究篇章规范

每个分支最多一个 active 研究篇章。探索任务可创建或修订关联篇章，其他分支版本只读展示；任务引用篇章和探索 ID，不复制目标状态。

篇章允许反复推进和关联多轮探索。进入 `paused` 时必须记录 `interruption`（暂时中断）或 `temporary-closure`（暂时收束）及说明；暂停后可插入另一篇章，待其不再 active 后恢复旧篇章。`completed` 与 `cancelled` 永久封存，不得重开。

新证据改变当前判断时，使用 `stage update --revision ... --summary ... --evidence ...` 追加判断修订。修订必须同步最新篇章概况并引用至少一项证据；它属于篇章历史，不恢复独立决策体系。

探索启动使用 `--stage auto|new|none|篇章ID`；新篇章通过 `--new-stage` 同次创建，无需额外维护周期。已有活动篇章时必须明确暂停原因，不自动替换。维护默认不关联篇章，探索不关联时须填写 `--without-stage-reason`。

科研资料放在 `resources/` 的资料预设或已登记自定义文件夹。新建程序、实验代码、Notebook、依赖和运行输出统一放在仓库根目录 `program/`；不登记为资料，也不纳入资料页、catalog、资料完整性检查或资料审阅，程序仍由 Git 跟踪。旧项目可用 `program migrate-analysis` 预览迁移 `resources/analysis/`，确认后加 `--apply`；迁移保留旧资料 ID 和关联历史，冲突时不覆盖。灵感和未成形问题放入 `sparks`，形成下一步行动或研究方案后放入 `plans`，成型的来源解读和学习材料放入 `tutorials`。顶层 `outputs` 仅兼容保留，不默认创建。移动已登记资料时保留 catalog ID 并更新路径；操作前刷新 `catalog folder index`，分类不明确时先询问用户。Sparks 可自由保存 Markdown，登记到 `catalog` 是可选的；其他资料通过 `catalog` 登记。Markdown 公式使用 Markdown/LaTeX 语法。

只有证据完整且状态为 `validated` 的探索可准备 Squash PR；合并等待用户明确确认。成功合入 `main` 后，使用 `exploration archive-validated <分支>` 记录归档。
"""

MAINTENANCE_OPERATIONS = """# 安装、恢复与交付

- 新 clone：在主目录放置唯一 EXE，运行 `.\\workflow-monitor.exe install`。
- 升级：在无活动任务、干净且同步的 main 上运行 `.\\workflow-monitor.exe update`。首次写入 Schema v6 后不可降级到仅支持 v1-v5 的版本。
- 恢复：异常中断运行 `task recover`；明确放弃运行 `task abandon --reason <原因>`。
- 诊断：使用 `diagnostics status/export`；诊断不自动上传。
- 验证：完成一批相关代码修改后使用 report --verify fast；结束时自动补齐同一软件输入指纹的回执（软件改动 fast/full，release profile 为 fast/release；纯文档和治理改动使用内置检查），正常收尾使用 `end` 内置检查，不重复运行独立 `check`；专项诊断可按需检查，涉及持久化或本机交付时运行 `db verify`。
- 交付：Dashboard 只读。提交、推送、PR、合并、正式 EXE 构建和发布分别等待用户确认。
"""

LEGACY_MAINTENANCE_README = """# 项目维护规范

本项目使用 Workflow Monitor 保存项目资料、研究篇章、任务生命周期、研究尝试、交接和科研资料索引。
`maintenance/events.jsonl` 是追加式永久事件源；`.project_hooks/maintenance.sqlite3`
是可从事件源重建的本地查询投影。

## 日常流程

所有命令使用主项目的 `.\\workflow-monitor.exe`。

1. 运行 `.\\workflow-monitor.exe context --format markdown`。
2. 首次写入前运行 `.\\workflow-monitor.exe start ...`。
3. 使用 `.\\workflow-monitor.exe state update` 更新断点；探索证据使用 `attempt update`。
4. 最后运行 `.\\workflow-monitor.exe end ...`，不得删除活动状态或绕过收尾。

项目说明和长期大目标通过 `.\\workflow-monitor.exe project update` 记录。全项目同一时间最多
一个 active 研究篇章，使用内部 `stage start` 和 `stage update` 推进。探索分支自动关联创建时的
当前篇章，并通过 `attempt update --current-step ... --progress ... --next-step ...` 保存进度。
Dashboard 是只读观察台：“工作流”页合并当前任务、当前研究篇章、探索、动作可用性和历史记录，
顶部近实时显示当前周期、文件变化、进度、写锁和阻塞原因。用户只需在 AI 对话中描述任务，
AI 按受管理规则调用结构化服务；搜索集中在搜索页，诊断页显示故障、导出覆盖和清理回执，解释页说明中英文状态。Dashboard 不执行业务写入、push、创建 PR、合并或发布。

稳定维护只在 `main` 使用 `--track stable`。新理论、算法、实验和不确定改动使用
`--track research|experiment|sandbox --topic <slug>`。只有 `validated` 尝试可以准备
Squash PR，合并必须等待用户明确确认。分支名是稳定的探索身份；同一探索分支内可以有多个任务和提交，但只投影为一条探索记录。

## 项目目录

| 路径 | 内容 |
|:---|:---|
| `resources/source/` | 外部资料和来源证据 |
| `resources/data/` | 原始、过程和处理后数据 |
| `resources/theory/` | 理论、假设、定义和推导 |
| `program/` | 程序、实验代码、Notebook、依赖和运行输出；不纳入资料管理 |
| `resources/outputs/` | 兼容旧登记，不默认创建 |
| `resources/others/` | 暂时无法可靠分类的资料 |
| `resources/reports/` | 面向外部受众的项目总结与报告 |
| `resources/sparks/` | 自由 Markdown 灵感；可选择性登记 |
| `resources/tutorials/` | 文献教程、解读和学习材料 |
| `resources/translations/` | 文献翻译及对照材料 |
| `resources/plans/` | 灵感的下一步行动计划 |

原始资料不覆盖；过程与成果分开。Markdown 文件中的公式使用 Markdown/LaTeX 语法。
`catalog scan` 扫描 resources 内的预设和自定义目录，Sparks 默认不强制登记。根目录 `program/` 不扫描、不登记、不参与资料完整性或审阅；程序输出留在对应程序目录。旧项目可运行 `program migrate-analysis` 预览迁移旧 `resources/analysis/`，确认后加 `--apply`。
`check` 会报告缺失目录、错位条目和未索引文件。旧项目先运行
`.\\workflow-monitor.exe catalog migrate-layout --dry-run`，确认无冲突后再执行实际迁移。
事件日志只追加，不得手工修改既有行；SQLite 不纳入 Git，也不是唯一备份。

## 故障反馈

运行 `.\\workflow-monitor.exe diagnostics status` 查看本地故障摘要，运行
`.\\workflow-monitor.exe diagnostics export` 导出脱敏 ZIP。诊断记录位于被 Git 忽略的
`.project_hooks/diagnostics/`，不包含项目文件、完整事件日志、环境变量或 Git 远程地址，且不会自动上传。

Dashboard 只读显示诊断状态，并对不可执行动作提前显示稳定原因代码、证据和安全下一步；用户点击后可向所选位置导出脱敏诊断 ZIP，也可由 AI 或 CLI 执行导出。

诊断导出保存不含绝对路径的本地回执，以便确认每个事件是否进入诊断包。参数错误、工作流前置条件和普通冲突只显示提示，不持久化为 Bug 诊断；内部异常、数据完整性和外部依赖故障保留待复查。用户可在诊断页确认解决后原子删除对应指纹，故障再次出现时会重新记录。

## Sparks 灵感池

`resources/sparks/` 保存低约束的自由 Markdown 灵感，不要求模板、状态或登记。需要跨记录检索时可选择性使用 `catalog` 登记；未登记 Sparks 不触发目录一致性警告。

## 崩溃恢复

活动任务异常中断后运行 `.\\workflow-monitor.exe task recover`。确认放弃时运行
`.\\workflow-monitor.exe task abandon --reason <原因>`；命令保留科研文件、暂存区和分支。
"""


class ProjectManagerError(RuntimeError):
    pass


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def text_hash(content: str) -> str:
    return sha256_bytes(content.encode("utf-8"))


def default_config() -> dict:
    return {
        "version": 3,
        "enabled": True,
        "timezone": "Asia/Shanghai",
        "state_dir": ".project_hooks",
        "backend": "project_hooks",
        "verification_commands": {},
        "review_interval_days": 14,
        "core_read_order": ["maintenance/CORE.md"],
        "context_command": ".\\workflow-monitor.exe context --view brief --format markdown",
        "maintenance_store": {
            "engine": "sqlite",
            "database": ".project_hooks/maintenance.sqlite3",
            "journal": "maintenance/events.jsonl",
            "schema_version": SCHEMA_VERSION,
        },
        "branch_policy": {
            "default_branch": "main",
            "exploration_types": ["research", "experiment", "sandbox"],
            "archive_prefix": "archive",
            "success_integration": "squash_pr",
            "require_user_merge_confirmation": True,
        },
        "git_auto_commit": {"enabled": True, "eligible_task_sizes": ["large"]},
    }


MAINTENANCE_CORE += """\n
- 资料路径失效先运行 `catalog reconcile` 搜索可能的移动位置；唯一内容哈希匹配可通过 `--apply` 修复原登记，保留资料身份和关系。歧义、无历史哈希或范围外位置需要用户选择新位置或确认归档，不自动删除或归档。
- 文件夹导航登记使用 `catalog folder add/update/list/archive/restore`。教程、翻译用于文献加工，plans 用于 Sparks 的下一步；登记文件夹不替代内部文件登记。
- 创建或调整程序代码时统一放在仓库根目录 `program/`；不放入 `resources/`，不登记为资料。旧项目用 `program migrate-analysis` 预览迁移 `resources/analysis/`，确认后加 `--apply`。
- 每次资料创建、编辑、导入、移动、重命名或删除前，重新读取 `catalog folder index`，按目录用途选择位置。新目录先创建并用 `catalog folder add` 登记名称和用途，再刷新索引；归属不明确时询问用户。输出导入显式指定 `--folder`，不自动重建顶层 outputs。
- `start` 保存既有资料问题；AI 在 start/report/end 用 depends_on、deliverable 登记直接依赖和交付物（catalog:ID 或 path:相对路径）。无关且未恶化的既有资料问题保留待处理，不阻止 completed；任务交付物和直接依赖缺失仍阻止完成。全局 check 继续报告全部问题。
- 确实受阻时使用 `end --result blocked --blocker <原因> --evidence <证据> --next <恢复条件>`。检查失败保留活动任务，不默认 abandon；只有用户明确放弃才运行 task abandon。所有修复通过工作流命令或结构化服务追加事件，不直接编辑 SQLite 或既有事件，历史 abandoned 不改写。

自动填报、组合篇章启动及本地执行器配置见 [AUTOMATION.md](AUTOMATION.md)，使用这些功能或排查失败时按需读取。\n"""

MAINTENANCE_CORE += '\n9. 科研审阅按篇章、探索、资料分类；review show 默认返回精简变化，--full 按需展开。正式研究使用变化触发和 14 天提醒，稳定参考不作周期提醒，示例只提示完整性问题。\n'
MAINTENANCE_CORE += '10. 审阅状态与完整覆盖由程序自动判定和记录；延期与完整性问题不算完成，新变化不清除旧整体审阅记录。\n'
MAINTENANCE_RESEARCH += '\n审阅按篇章、探索、资料分类；正式研究按变化和 14 天提醒，稳定参考不作周期提醒，示例只检查完整性；详见 AUTOMATION.md。\n'
AGENTS_BLOCK = AGENTS_BLOCK.replace("<!-- project-maintenance-hooks:end -->", '- 科研审阅按篇章、探索、资料分类；review list --kind 可筛选，review show 默认精简、--full 展开。正式研究使用变化和 14 天提醒，稳定参考不作周期提醒，示例只提示完整性问题。\n<!-- project-maintenance-hooks:end -->')
AGENTS_BLOCK = AGENTS_BLOCK.replace("<!-- project-maintenance-hooks:end -->", '- 审阅状态与完整覆盖由程序自动判定和记录；AI 只选择必要的审阅结果，完整性问题不能靠延期清除。\n<!-- project-maintenance-hooks:end -->')
MAINTENANCE_AUTOMATION = '# 自动填报与验证\n\n日常使用 `start → report（按需）→ end`。短任务只需开始和结束两次写调用。旧 `state update`、`attempt update` 继续兼容，不要求重复调用。\n\n三个入口支持 `--input-json <文件或 ->`；`-` 从标准输入读取 JSON 对象。键使用参数名称的下划线形式，例如 `current_step`、`stage_update`。同名字段不得同时从 JSON 和命令行提供。省略表示保持原值；`clear` 可列出要清空的进展、判断、阻塞、假设或结论字段。证据追加去重，不通过清空删除历史。\n\n`start` 提供 `kind`、`scope`、`acceptance`，按需选择 `track` 和 `topic`。`stage` 默认为 `auto`：维护不关联篇章，探索关联当前分支活动篇章。`new` 同时提供 `new_stage` 对象，包含 `title`、`goal`、`acceptance`，可选 `stage_id`（默认 topic）。已有活动篇章时，必须以 `previous_stage` 提供 `status: paused` 和 `reason`；不自动结束其他篇章。`none` 用于不关联，探索必须填写 `without_stage_reason`。\n\n`report` 接收 `current_step`、`judgment`、`blocker`、`breakpoint`、`next`，探索可同时填 `hypothesis`、`progress`、`evidence`、`conclusion`。程序将科研记录关联到 checkpoint，避免重复输入。`stage_update` 接收关联篇章的 `summary`、`revision`、`evidence`、状态等现有篇章字段。改变判断必须同时提交修订说明、概况和证据。\n\n完成代码批次使用 `report --current-step ... --verify fast`。`end` 填写 `result`、`note`，可同时提交汇报字段；程序复用已有进展，并在尚无进展时使用结束说明。关联篇章且没有修订时必须提供 `stage_review: reviewed-no-change`。科研结果 `negative`、`inconclusive` 需要结论和证据；暂停需要 `progress` 说明原因和 `next` 给出恢复条件。\n\n`end` 自动补齐所需的本地测试；`--check-only` 只检查，不保存填报或运行测试。项目配置 `verification_commands` 按套件设置 `argv` 参数数组、`cwd`（项目内，默认 `.`）、`timeout_seconds`（默认 1800）。未配置时明确报缺项，不猜测 Python 环境。新项目默认不配置执行器；配置受项目维护权限控制。执行器必须是本地验证命令，不包含发布操作。\n\n有效回执不重跑；失败或超时停止，保留任务和汇报。日志保存到 `.project_hooks/verification-logs/`，命令返回日志位置；修复后再次结束即可。运行期间任务、HEAD 或软件输入改变，结果不可作为通过凭据。恢复使用 `task recover`，不要删除活动状态。\n\n`stage list --all-branches` 查看本地已知分支的篇章版本；`stage show <ID> --source <完整ref或working-tree>` 明确查看来源。当前分支是本任务的权威版本；其他分支未合并的判断不会覆盖它。分叉判断由 AI 比较证据后在当前关联篇章追加修订，不自动合并或按时间选择。\n\n操作后更新记录和监控视图，不增加常驻监控服务。完整输出继续可用，日常加 `--compact`。提交、推送、PR、合并及对外发布仍遵循原有授权边界。\n\n## 按变化审阅\n\nAI 自主处理当前任务有关的登记与审阅，无关事项留待相关任务处理。程序根据明确关联生成变化摘要，不按关键词猜测研究关系。审阅分为篇章、探索和资料；资料用途由 `metadata.review_usage` 明确为 `formal`、`reference` 或 `example`。正式研究按变化和 `review_interval_days` 提醒，稳定参考只在初次或变化时提醒，示例只提示完整性问题。内容更新、关联活动、审阅和验证时间分别记录。\n\n`review list` 查看当前相关的待审阅对象，`review list --all` 查看全项目对象，`--kind stage|attempt|resource` 按类别筛选。`review show <object>` 返回有界的变化摘要和短范围令牌，`--full` 才展开完整来源；没有变化时不会回退输出全部历史。读取不产生已读记录。\n\n`report` 或 `end` 可提交 `reviews` 数组，成员包含 `token`、`result`，按需填写 `reason`、`until`、`evidence`。结果选择 `updated`（程序关联本任务对应修改事件，可用 evidence 指定事件 ID）、`reviewed-no-change` 或 `deferred`。延后必须说明原因，`until` 使用项目时区的 ISO 日期时间，或暂停对象使用 `resume`；省略时默认延后 14 天。延期内出现新变化时重新提示。短令牌提交时重新构建并校验完整范围；旧令牌继续兼容。\n\n关联篇章的 `end --stage-review` 自动形成同一套审阅记录；已通过 `reviews` 填报的篇章不要求再填同义确认。延后不等于完成审阅，也不能替代既有篇章、资料一致性或测试门禁。无变化的重复填报不新增记录；允许审阅后不修改内容，禁止为刷新时间而改写科学结论。\n\n`end --check-only` 无需结果和说明，只读返回阻塞及审阅摘要。正常结束仍需 `result` 和 `note`。精简失败回执在标准错误流输出 JSON，说明本次已保存事件、最近 checkpoint、日志位置及重试方式；执行测试时该流还可能包含进度文字。保存状态为未知时先检查，不假设操作成功或重复提交。恢复仍使用 `task recover`。\n'


MAINTENANCE_AUTOMATION += ('\nDashboard 固定展示「当前正常／建议审阅／需要处理」，只高亮程序计算的状态。完整性问题不会因审阅或延期消失；待办按篇章、探索、资料展开，延期另行显示。'
                           '最后整体审阅时间只来自随 `report` 或 `end` 自动生成的 `review.coverage_completed` 事件：所有适用对象精确覆盖且无待办、延期及完整性问题时写入。'
                           '不从历史单项时间推算或补写；新变化保留旧记录。AI 不另填整体状态。\n')


UNDERSTANDING_HINT = "- 消化科研结果、阅读长证明或整理理解卡点时，按需阅读 `maintenance/RESEARCH_UNDERSTANDING_GUIDE.md`；它不设置理解评分、强制阶段或完成门禁。"
EXE_HANDOFF_HINT = "- 收到测试 EXE 时分别核对源码同步、实际运行构建和项目模板升级。源码仓库先比较构建指纹；一致则报告已一致，无需源码修改，不一致须有对应提交或补丁。科研项目使用 `update --from-exe <路径> --check` 预览，再在授权范围内实际升级；文件替换不等于模板升级。存在定制冲突时报告需要处理，不能笼统宣称更新完成。测试构建提供 `build-notes`，构建成功不代表功能测试通过。"
AGENTS_BLOCK = AGENTS_BLOCK.replace(AGENTS_END, UNDERSTANDING_HINT + "\n" + EXE_HANDOFF_HINT + "\n" + AGENTS_END)
MAINTENANCE_README = MAINTENANCE_README.replace("- [OPERATIONS.md]", "- [RESEARCH_UNDERSTANDING_GUIDE.md](RESEARCH_UNDERSTANDING_GUIDE.md)：科研结果理解与证明阅读参考，按需使用，不作为门禁。\n- [OPERATIONS.md]")
MAINTENANCE_OPERATIONS += "\n## 本地测试 EXE 交付\n\n" + EXE_HANDOFF_HINT + "\n\n- 测试版说明包含待测变化、步骤与预期、已知限制、升级注意事项和源码依据；`build-notes --format markdown|json` 在项目外也可读取。正式更新日志记录功能变化，不重复记录每次构建。\n- 实际升级保留 main、无活动任务、干净且同步工作树的前置检查；先处理阻塞，不强制覆盖定制文件。\n"


PLUGIN_JSON = (
    '{\n'
    '  "name": "workflow-monitor",\n'
    '  "description": "Workflow Monitor lifecycle skills and safety guard hooks for research Git repositories."\n'
    '}\n'
)

_SESSION_START_HOOK_CMD = (
    "if exist ..\\..\\..\\workflow-monitor.exe (..\\..\\..\\workflow-monitor.exe agent-hook) "
    "else if exist ..\\..\\..\\..\\..\\workflow-monitor.exe (..\\..\\..\\..\\..\\workflow-monitor.exe agent-hook) "
    "else (echo {})"
)

_PRE_TOOL_GUARD_PS1_CMD = (
    "powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File hooks\\pre-tool-guard.ps1"
)

PLUGIN_PRE_TOOL_GUARD_PS1 = r"""$ErrorActionPreference = 'Stop'
$ask = '{"decision":"ask"}'

function Get-Prop($obj, [string]$name) {
    if ($null -eq $obj) { return $null }
    $prop = $obj.PSObject.Properties[$name]
    if ($null -eq $prop) { return $null }
    return $prop.Value
}

function Find-WorkflowProjectRoot([object]$payload) {
    $candidates = New-Object System.Collections.Generic.List[string]
    $cwd = Get-Prop $payload 'cwd'
    if ($cwd -is [string] -and -not [string]::IsNullOrWhiteSpace($cwd)) {
        $candidates.Add($cwd)
    }
    $workspacePaths = Get-Prop $payload 'workspacePaths'
    if ($workspacePaths -is [System.Collections.IEnumerable] -and -not ($workspacePaths -is [string])) {
        foreach ($item in $workspacePaths) {
            if ($item -is [string] -and -not [string]::IsNullOrWhiteSpace($item)) {
                $candidates.Add($item)
            }
        }
    }
    foreach ($raw in $candidates) {
        try {
            $dir = New-Object System.IO.DirectoryInfo ([System.IO.Path]::GetFullPath($raw))
        } catch {
            continue
        }
        while ($null -ne $dir) {
            $primary = Join-Path $dir.FullName '.workflow-monitor\workflow.json'
            $legacy = Join-Path $dir.FullName '.codex\project-maintenance-workflow.json'
            if ((Test-Path -LiteralPath $primary -PathType Leaf) -or (Test-Path -LiteralPath $legacy -PathType Leaf)) {
                return $dir.FullName
            }
            $dir = $dir.Parent
        }
    }
    return $null
}

function Test-ProtectedRelative([string]$relative) {
    $norm = $relative.Replace('\', '/').Trim().ToLowerInvariant()
    $list = @($norm)
    if ($norm -match '^\.worktrees/[^/]+/(.+)$') {
        $list += $Matches[1]
    }
    foreach ($cand in $list) {
        if ($cand -eq 'maintenance/events.jsonl') { return $true }
        if ($cand.StartsWith('.git/workflow-monitor/')) { return $true }
        if ($cand.StartsWith('.project_hooks/') -and ($cand.Contains('.sqlite3') -or $cand.StartsWith('.project_hooks/active-task'))) {
            return $true
        }
    }
    return $false
}

try {
    $rawInput = [Console]::In.ReadToEnd()
    if ([string]::IsNullOrWhiteSpace($rawInput)) {
        Write-Output $ask
        exit 0
    }
    $payload = $rawInput | ConvertFrom-Json
    if ($null -eq $payload) {
        Write-Output $ask
        exit 0
    }
    $projectRoot = Find-WorkflowProjectRoot $payload
    if ($null -eq $projectRoot) {
        Write-Output $ask
        exit 0
    }
    $toolCall = Get-Prop $payload 'toolCall'
    $name = [string](Get-Prop $toolCall 'name')
    $toolArgs = Get-Prop $toolCall 'args'
    if ($null -eq $toolArgs) {
        Write-Output $ask
        exit 0
    }

    if ($name -eq 'write_file' -or $name -eq 'edit_file') {
        $rawPath = Get-Prop $toolArgs 'Path'
        if (-not ($rawPath -is [string]) -or [string]::IsNullOrWhiteSpace($rawPath)) {
            $rawPath = Get-Prop $toolArgs 'path'
        }
        if (-not ($rawPath -is [string]) -or [string]::IsNullOrWhiteSpace($rawPath)) {
            Write-Output $ask
            exit 0
        }
        $cwd = Get-Prop $payload 'cwd'
        $baseDir = if ($cwd -is [string] -and -not [string]::IsNullOrWhiteSpace($cwd)) { $cwd } else { $projectRoot }
        $targetFull = if ([System.IO.Path]::IsPathRooted($rawPath)) {
            [System.IO.Path]::GetFullPath($rawPath)
        } else {
            [System.IO.Path]::GetFullPath([System.IO.Path]::Combine([string]$baseDir, $rawPath))
        }
        $rootFull = [System.IO.Path]::GetFullPath($projectRoot).Replace('\', '/').TrimEnd('/')
        $targetNorm = $targetFull.Replace('\', '/').TrimEnd('/')
        $rootPrefix = $rootFull + '/'
        $rel = $null
        if ($targetNorm.StartsWith($rootPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
            $rel = $targetNorm.Substring($rootPrefix.Length)
        } elseif ($targetNorm.Equals($rootFull, [System.StringComparison]::OrdinalIgnoreCase)) {
            $rel = ''
        } else {
            Write-Output $ask
            exit 0
        }
        if (Test-ProtectedRelative $rel) {
            Write-Output '{"decision":"deny","reason":"\u7981\u6b62\u76f4\u63a5\u4fee\u6539\u7ef4\u62a4\u4e8b\u4ef6\u65e5\u5fd7\u3001\u6d3b\u52a8\u72b6\u6001\u6216 SQLite \u6570\u636e\u5e93\uff1b\u8bf7\u4f7f\u7528 workflow-monitor.exe \u751f\u547d\u5468\u671f\u547d\u4ee4\u3002"}'
            exit 0
        }
        Write-Output '{"decision":"allow"}'
        exit 0
    }

    if ($name -eq 'run_command') {
        $cmdLine = Get-Prop $toolArgs 'CommandLine'
        if ($null -eq $cmdLine) { $cmdLine = Get-Prop $toolArgs 'command' }
        $command = ([string]$cmdLine).Trim()
        if ([string]::IsNullOrWhiteSpace($command)) {
            Write-Output $ask
            exit 0
        }
        $wfRe = '\bworkflow-monitor(?:\.exe)?\b'
        $targetRe = '(?:maintenance[\\/]events\.jsonl|\.project_hooks[\\/]active-task(?:\.json)?|\.project_hooks[\\/]maintenance\.sqlite3(?:-wal|-shm)?|\.git[\\/]workflow-monitor[\\/]registry\.sqlite3(?:-wal|-shm)?)'
        $opRe = '(?:\b(?:rm|rmdir|del|erase|remove-item|ri|clear-content|set-content|out-file|truncate|sqlite3)\b|>>?)'
        $highRiskRe = '(?:\bgit\s+(?:push|merge)\b|\bgh\s+pr\s+merge\b|\btask\s+abandon\b|\bdb\s+rebuild\b)'
        if ((-not ($command -match $wfRe)) -and ($command -match $targetRe) -and ($command -match $opRe)) {
            Write-Output '{"decision":"deny","reason":"\u7981\u6b62\u901a\u8fc7\u7ec8\u7aef\u547d\u4ee4\u76f4\u63a5\u5220\u9664\u6d3b\u52a8\u72b6\u6001\u6216\u6539\u5199\u4e8b\u4ef6\u65e5\u5fd7/SQLite\uff1b\u8bf7\u4f7f\u7528 workflow-monitor.exe \u547d\u4ee4\u3002"}'
            exit 0
        }
        if ($command -match $highRiskRe) {
            Write-Output '{"decision":"force_ask","reason":"\u8be5\u547d\u4ee4\u6d89\u53ca\u5916\u90e8\u4ea4\u4ed8\u6216\u9ad8\u98ce\u9669\u7ef4\u62a4\u64cd\u4f5c\uff0c\u6309 Workflow Monitor \u89c4\u8303\u5fc5\u987b\u83b7\u5f97\u7528\u6237\u660e\u786e\u786e\u8ba4\u3002"}'
            exit 0
        }
        Write-Output $ask
        exit 0
    }

    Write-Output $ask
    exit 0
} catch {
    Write-Output $ask
    exit 0
}
"""

PLUGIN_HOOKS_JSON = json.dumps(
    {
        "workflow-monitor-session": {
            "SessionStart": [
                {
                    "type": "command",
                    "command": _SESSION_START_HOOK_CMD,
                    "timeout": 30,
                }
            ],
        },
        "workflow-monitor-guard": {
            "PreToolUse": [
                {
                    "matcher": "write_file|edit_file",
                    "hooks": [
                        {
                            "type": "command",
                            "command": _PRE_TOOL_GUARD_PS1_CMD,
                            "timeout": 10,
                        }
                    ],
                },
                {
                    "matcher": "run_command",
                    "hooks": [
                        {
                            "type": "command",
                            "command": _PRE_TOOL_GUARD_PS1_CMD,
                            "timeout": 10,
                        }
                    ],
                },
            ],
        },
    },
    ensure_ascii=False,
    indent=2,
) + "\n"

SKILL_LIFECYCLE = """---
name: workflow-lifecycle
description: >-
  Use when starting, reporting, verifying, or finishing tasks in a Workflow
  Monitor repository, or when opening the standalone desktop Dashboard.
---

# Workflow Monitor 任务生命周期

## 日常执行顺序

1. 新会话或上下文不清时先读取精简上下文：
   `.\\workflow-monitor.exe context --view brief --format markdown`
2. **只读咨询与审查**不运行 `start`，也不运行 `end`。
3. **首次写入前**运行 `start`（主项目与链接工作区统一调用主目录入口）：
   - 稳定维护（仅限 `main`）：`start --track stable --kind code --scope "..." --acceptance "..." --compact`
   - 探索/实验/研究：`start --track experiment --topic <slug> --kind code --scope "..." --acceptance "..." --compact`
   - 无法确定轨道时必须在对话中询问用户，不得静默选择。
   - 探索启动返回 `worktree` 路径后，后续读写与 `--project <worktree>` 均在工作区内执行。
4. 阶段完成、判断变化或代码批次后运行 `report`（可加 `--verify fast` 与 `--compact`）；复杂填报使用 `--input-json <文件或 ->`。
5. 结束前检查门禁：`context --view verification --format markdown`，随后运行 `end --result completed --note "..." --current-step "..." --compact`（关联篇章需填 `--stage-review`）。
6. 中断恢复运行 `task recover`；受阻使用 `end --result blocked ...`；仅在用户明确确认时才运行 `task abandon`、`git push` 或合并。

## 查看独立桌面看板

- 无参运行或双击项目根目录 `workflow-monitor.exe` 即可打开独立 WebView2 只读看板。
- 需要从终端或对话协助打开时运行：`Start-Process .\\workflow-monitor.exe`（切勿直接无参同步阻塞执行 `workflow-monitor.exe` 获取上下文）。
"""

SKILL_CATALOG = """---
name: research-catalog
description: >-
  Use when creating, moving, organizing, or reconciling research materials in
  resources/ or placing code and outputs in program/.
---

# 科研资料与目录归属

1. **资料操作前先查目录索引**：运行 `.\\workflow-monitor.exe catalog folder index` 确认现有目录用途；新增自定义目录先用 `catalog folder add` 登记。归属不明确时询问用户。
2. **严格区分 `resources/` 与 `program/`**：
   - `resources/sparks/`：低约束自由 Markdown 灵感，可选登记。
   - `resources/plans/`：Sparks 形成的可执行下一步或研究方案。
   - `resources/tutorials/`、`resources/translations/`：文献加工与学习材料。
   - `program/`：新建程序、实验代码、Notebook、依赖及运行输出统一放在这里，**不要放入 `resources/`，也不要登记为资料**。
3. **路径失效与移动**：移动已登记资料时保留原 catalog ID 并更新路径；路径失效先运行 `catalog reconcile` 检查内容哈希匹配，唯一匹配再加 `--apply` 修复。
"""

SKILL_WORKTREE = """---
name: multi-worktree-review
description: >-
  Use when resuming linked worktrees, checking parallel task status or research
  conflicts, or submitting change-based scientific reviews.
---

# 多工作树并行与科研审阅

1. **续办与汇总**：
   - 查看所有工作区与任务：`status --all` 或 `context --all`。
   - 同一工作流续办任务：`start --workflow <workflow_id> ...`。
   - 暂停/恢复任务：`task pause` / `task resume`。
2. **分歧与冲突**：运行 `conflict list` 与 `conflict show <id>` 核对版本证据；研究判断取舍与正式合并必须由用户确认。
3. **按变化审阅**：
   - 查看待办：`review list`（或 `review list --all` / `--kind stage|attempt|resource`）。
   - 查看精简变化与短令牌：`review show <object>`（需要完整来源时加 `--full`）。
   - 审阅结果随 `report` 或 `end` 的 `--reviews` 一次性提交，无变化不改写科学结论。
"""


def template_files() -> dict[str, str]:
    template_root = Path(__file__).resolve().parents[3]
    roadmap_source = template_root / "maintenance" / "PHYSICS_RESEARCH_ROADMAP.md"
    physics_roadmap = roadmap_source.read_text(encoding="utf-8")
    understanding_guide = (template_root / "maintenance" / "RESEARCH_UNDERSTANDING_GUIDE.md").read_text(encoding="utf-8")
    files = {
        ".githooks/pre-commit": HOOK_TEMPLATE,
        "maintenance/README.md": MAINTENANCE_README,
        "maintenance/CORE.md": MAINTENANCE_CORE,
        "maintenance/AUTOMATION.md": MAINTENANCE_AUTOMATION,
        "maintenance/RESEARCH.md": MAINTENANCE_RESEARCH,
        "maintenance/PHYSICS_RESEARCH_ROADMAP.md": physics_roadmap,
        "maintenance/RESEARCH_UNDERSTANDING_GUIDE.md": understanding_guide,
        "maintenance/OPERATIONS.md": MAINTENANCE_OPERATIONS,
        ".agents/plugins/workflow-monitor/plugin.json": PLUGIN_JSON,
        ".agents/plugins/workflow-monitor/hooks.json": PLUGIN_HOOKS_JSON,
        ".agents/plugins/workflow-monitor/hooks/pre-tool-guard.ps1": PLUGIN_PRE_TOOL_GUARD_PS1,
        ".agents/plugins/workflow-monitor/skills/workflow-lifecycle/SKILL.md": SKILL_LIFECYCLE,
        ".agents/plugins/workflow-monitor/skills/research-catalog/SKILL.md": SKILL_CATALOG,
        ".agents/plugins/workflow-monitor/skills/multi-worktree-review/SKILL.md": SKILL_WORKTREE,
    }
    files.update({f"{item.relative_path}/.gitkeep": "\n" for item in RESOURCE_DIRECTORIES if item.required})
    files[f"{PROGRAM_DIRECTORY}/.gitkeep"] = "\n"
    return files


def extract_block(text: str, begin: str, end: str) -> str | None:
    start = text.find(begin)
    finish = text.find(end, start + len(begin)) if start >= 0 else -1
    if start < 0 or finish < 0:
        return None
    return text[start:finish + len(end)]


def replace_or_append_block(text: str, begin: str, end: str, block: str) -> str:
    old = extract_block(text, begin, end)
    if old is not None:
        return text.replace(old, block, 1)
    separator = "" if not text else ("\n" if text.endswith("\n") else "\n\n")
    return text + separator + block + "\n"


def managed_hashes(root: Path) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    for relative, template in template_files().items():
        path = root / relative
        content = path.read_bytes() if path.is_file() else template.encode("utf-8")
        result[relative] = {
            "hash": sha256_bytes(content),
            "customized": content != template.encode("utf-8"),
        }
    for relative, begin, end, template in (
        ("AGENTS.md:block", AGENTS_BEGIN, AGENTS_END, AGENTS_BLOCK),
        (".gitignore:block", GITIGNORE_BEGIN, GITIGNORE_END, GITIGNORE_BLOCK),
        (".gitattributes:block", GITATTRIBUTES_BEGIN, GITATTRIBUTES_END, GITATTRIBUTES_BLOCK),
    ):
        path = root / relative.removesuffix(":block")
        block = extract_block(path.read_text(encoding="utf-8") if path.is_file() else "", begin, end)
        result[relative] = {
            "hash": text_hash(block or template),
            "customized": block is not None and block != template,
        }
    return result


def installation_record(root: Path, application_version: str) -> dict:
    return {
        "format": 1,
        "application_version": application_version,
        "build_identity": build_identity(),
        "template_version": TEMPLATE_VERSION,
        "managed_files": managed_hashes(root),
    }


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return git_client.run(root, list(args), check=False)


def require_git_repository(root: Path) -> None:
    if git(root, "rev-parse", "--is-inside-work-tree").returncode != 0:
        raise ProjectManagerError("目标目录必须已经是 Git 仓库")


def initialize_project(root: Path, application_version: str, *, unified=True) -> dict:
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    require_git_repository(root)
    if has_project_config(root):
        raise ProjectManagerError("项目已经初始化；请使用 .\\workflow-monitor.exe update")
    for relative, content in template_files().items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
    ensure_resource_directories(root)
    agents = root / "AGENTS.md"
    agents_text = agents.read_text(encoding="utf-8") if agents.is_file() else "# Agent 操作规范\n"
    agents.write_text(
        replace_or_append_block(agents_text, AGENTS_BEGIN, AGENTS_END, AGENTS_BLOCK),
        encoding="utf-8",
        newline="\n",
    )
    ignore = root / ".gitignore"
    ignore_text = ignore.read_text(encoding="utf-8") if ignore.is_file() else ""
    ignore.write_text(
        replace_or_append_block(ignore_text, GITIGNORE_BEGIN, GITIGNORE_END, GITIGNORE_BLOCK),
        encoding="utf-8",
        newline="\n",
    )
    attributes = root / ".gitattributes"
    attributes_text = attributes.read_text(encoding="utf-8") if attributes.is_file() else ""
    attributes.write_text(
        replace_or_append_block(
            attributes_text,
            GITATTRIBUTES_BEGIN,
            GITATTRIBUTES_END,
            GITATTRIBUTES_BLOCK,
        ),
        encoding="utf-8",
        newline="\n",
    )
    write_json(root / CONFIG_PATH, default_config())
    journal = root / "maintenance/events.jsonl"
    journal.touch(exist_ok=True)
    event = new_event(
        "workflow.initialized",
        branch=git(root, "branch", "--show-current").stdout.strip() or "main",
        task_id=None,
        payload={"application_version": application_version, "template_version": TEMPLATE_VERSION},
        timezone="Asia/Shanghai",
    )
    append_events(journal, root / ".project_hooks", [event])
    write_json(root / INSTALLATION_PATH, installation_record(root, application_version))
    connection = ensure_database(root / ".project_hooks/maintenance.sqlite3", journal)
    connection.close()
    hook = root / ".githooks/pre-commit"
    try:
        hook.chmod(0o755)
    except OSError:
        pass
    configured = git(root, "config", "--local", "core.hooksPath", ".githooks")
    if configured.returncode != 0:
        raise ProjectManagerError(configured.stderr.strip() or "无法配置 Git hooksPath")
    if unified:
        from .multi_worktree import initialize_unified
        initialize_unified(root)
    return {"status": "initialized", "project": str(root), "application_version": application_version}


def preflight_update(root: Path) -> None:
    require_git_repository(root)
    from ..persistence.project_registry import registry_for, OCCUPIED
    registry = registry_for(root)
    active = [item for item in registry.tasks() if item["status"] in OCCUPIED] if registry else []
    if active:
        raise ProjectManagerError("项目工作树仍有未结束任务: " + ", ".join(item["task_id"] + " @ " + item["path"] for item in active))
    installation = resolve_installation_path(root)
    if not installation.is_file():
        raise ProjectManagerError("缺少安装清单；请先使用当前 EXE执行 .\\workflow-monitor.exe install 进行接管")
    branch = git(root, "branch", "--show-current").stdout.strip()
    if branch != "main":
        raise ProjectManagerError(f"升级只能在 main 执行，当前分支为 {branch or '未知'}")
    dirty = git(root, "status", "--porcelain", "--untracked-files=all").stdout.splitlines()
    if dirty:
        raise ProjectManagerError("升级前工作树必须干净")
    database = root / ".project_hooks/maintenance.sqlite3"
    active_task_id = latest_active_task_id(database)
    if active_task_id:
        raise ProjectManagerError(f"存在活动任务 {active_task_id}，请先结束任务")
    upstream = git(root, "show-ref", "--verify", "--quiet", "refs/remotes/origin/main")
    if upstream.returncode == 0:
        local = git(root, "rev-parse", "main").stdout.strip()
        remote = git(root, "rev-parse", "origin/main").stdout.strip()
        if local != remote:
            raise ProjectManagerError("本地 main 与已知 origin/main 不同步")


def _backup(paths: list[Path], root: Path) -> tuple[Path, dict[Path, bool]]:
    folder = Path(tempfile.mkdtemp(prefix="project-hooks-update-"))
    existed: dict[Path, bool] = {}
    for path in paths:
        relative = path.relative_to(root)
        existed[path] = path.exists()
        if path.is_file():
            target = folder / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
    return folder, existed


def _restore(folder: Path, existed: dict[Path, bool], root: Path) -> None:
    for path, was_present in existed.items():
        saved = folder / path.relative_to(root)
        if saved.is_file():
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(saved, path)
        elif not was_present and path.exists():
            path.unlink()


def migrate_config(root: Path) -> None:
    path = resolve_config_path(root)
    current = json.loads(path.read_text(encoding="utf-8"))
    defaults = default_config()
    current["version"] = defaults["version"]
    current.setdefault("enabled", True)
    current.setdefault("timezone", defaults["timezone"])
    current.setdefault("state_dir", defaults["state_dir"])
    current.setdefault("backend", defaults["backend"])
    old_order = current.get("core_read_order")
    if old_order in (None, ["maintenance/README.md"]):
        current["core_read_order"] = defaults["core_read_order"]
    else:
        current["core_read_order"] = list(dict.fromkeys([
            "maintenance/CORE.md", *old_order,
        ]))
    if current.get("context_command") in (None, ".\\workflow-monitor.exe context --format markdown"):
        current["context_command"] = defaults["context_command"]
    current.setdefault("maintenance_store", defaults["maintenance_store"])
    current["maintenance_store"]["schema_version"] = SCHEMA_VERSION
    current.setdefault("branch_policy", defaults["branch_policy"])
    current.setdefault("git_auto_commit", defaults["git_auto_commit"])
    write_json(path, current)


def validate_project(root: Path) -> None:
    config = read_project_config(root)
    required = default_config()
    for key in required:
        if key not in config:
            raise ProjectManagerError(f"升级后配置缺少字段: {key}")
    for relative in (
        resolve_installation_relative(root),
        Path(".githooks/pre-commit"),
        Path("maintenance/README.md"),
        Path("maintenance/CORE.md"),
        Path("maintenance/RESEARCH.md"),
        Path("maintenance/PHYSICS_RESEARCH_ROADMAP.md"),
        Path("maintenance/RESEARCH_UNDERSTANDING_GUIDE.md"),
        Path("maintenance/OPERATIONS.md"),
        Path("maintenance/events.jsonl"),
        Path(".agents/plugins/workflow-monitor/plugin.json"),
        Path(".agents/plugins/workflow-monitor/hooks.json"),
    ):
        if not (root / relative).is_file():
            raise ProjectManagerError(f"升级后缺少文件: {relative.as_posix()}")
    for item in RESOURCE_DIRECTORIES:
        if item.required and not (root / item.relative_path).is_dir():
            raise ProjectManagerError(f"升级后缺少资源目录: {item.relative_path}")
    if not (root / PROGRAM_DIRECTORY).is_dir():
        raise ProjectManagerError(f"升级后缺少程序目录: {PROGRAM_DIRECTORY}")
    load_events(root / "maintenance/events.jsonl")
    configured = git(root, "config", "--local", "--get", "core.hooksPath").stdout.strip()
    from ..git.worktrees import repository_context
    shared_hooks = repository_context(root).shared_dir / "hooks"
    if configured not in {".githooks", shared_hooks.as_posix()}:
        raise ProjectManagerError("升级后 core.hooksPath 不是 .githooks")


def _preserving_append(journal: Path, event: dict) -> None:
    original = journal.read_bytes() if journal.exists() else b""
    events = load_events(journal)
    validate_projection([*events, event])
    addition = (b"" if not original or original.endswith(b"\n") else b"\n")
    addition += canonical_json(event).encode("utf-8") + b"\n"
    descriptor, name = tempfile.mkstemp(prefix="events-", suffix=".jsonl", dir=journal.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(original)
            handle.write(addition)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, journal)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def profile_authority_migration(events: list[dict], timezone: str) -> dict | None:
    profiles = [
        event for event in events
        if event["event_type"] == "project.profile_updated" and event["branch"] == "main"
    ]
    latest_profile = profiles[-1] if profiles else None
    if latest_profile and "main_goal_version" in latest_profile["payload"]:
        return None
    legacy_states = [
        event for event in events
        if event["event_type"] in {"project_state.updated", "legacy.project_state_imported"}
        and event["branch"] == "main" and event["payload"].get("main_goal_version")
    ]
    if not legacy_states:
        return None
    legacy = legacy_states[-1]
    profile = latest_profile["payload"] if latest_profile else {}
    return new_event(
        "project.profile_updated", branch="main", task_id=None,
        payload={
            "description": profile.get("description", ""),
            "big_goal": profile.get("big_goal", legacy["payload"].get("goal", "")),
            "main_goal_version": legacy["payload"]["main_goal_version"],
            "migration_source_event_id": legacy["event_id"],
        },
        timezone=timezone,
    )


def apply_project_update(
    root: Path, target_version: str, *, executable_path: Path | None = None,
    cache_build_id: str | None = None,
) -> dict:
    root = root.resolve()
    preflight_update(root)
    from .worktree_migration import plan_migration, migrate_project
    workspace_plan = plan_migration(root)
    if workspace_plan["blockers"]:
        raise ProjectManagerError("工作区迁移受阻: " + canonical_json(workspace_plan["blockers"]))
    staged_in_legacy = False
    if executable_path is not None:
        expected_executable = cached_executable(root, target_version, cache_build_id).resolve()
        executable_path = Path(executable_path).resolve()
        legacy_executable = cached_executable(root, target_version, cache_build_id, legacy=True).resolve()
        staged_in_legacy = executable_path == legacy_executable
        if executable_path not in {expected_executable, legacy_executable} or not executable_path.is_file():
            raise ProjectManagerError("迁移 EXE 不在目标版本的本地缓存位置")
        digest_file = executable_path.parent / ".exe-sha256"
        try:
            expected_digest = digest_file.read_text(encoding="ascii").strip()
        except OSError as exc:
            raise ProjectManagerError("迁移 EXE 缺少本地验签摘要") from exc
        if not expected_digest or sha256_bytes(executable_path.read_bytes()) != expected_digest:
            raise ProjectManagerError("迁移 EXE 与本地验签摘要不匹配")
        if cache_build_id and build_identity().get("build_id") != cache_build_id:
            raise ProjectManagerError("实际运行构建与目标缓存构建不一致")
    install_path = resolve_installation_path(root)
    config_path = resolve_config_path(root)
    installation = json.loads(install_path.read_text(encoding="utf-8"))
    old_version = installation.get("application_version", installation.get("core_version"))
    old_records = installation.get("managed_files", {})
    database = root / ".project_hooks/maintenance.sqlite3"
    paths = [config_path, install_path, root / "maintenance/events.jsonl",
             legacy_runtime_root(root) / "current.json",
             database, Path(str(database) + "-wal"), Path(str(database) + "-shm")]
    paths.extend(root / item for item in template_files())
    paths.extend((root / "AGENTS.md", root / ".gitignore", root / ".gitattributes"))
    backup, existed = _backup(paths, root)
    changed: list[str] = []
    conflicts: list[str] = []
    try:
        migrate_config(root)
        changed.append(resolve_config_relative(root).as_posix())
        candidates = root / ".project_hooks/update-conflicts" / target_version
        for relative, content in template_files().items():
            path = root / relative
            current = path.read_bytes() if path.is_file() else b""
            previous = old_records.get(relative, {})
            expected = previous.get("hash")
            new = content.encode("utf-8")
            if current == new or (not previous.get("customized") and
                                  (not current or sha256_bytes(current) == expected)):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(new)
                changed.append(relative)
            else:
                candidate = candidates / relative
                candidate.parent.mkdir(parents=True, exist_ok=True)
                candidate.write_bytes(new)
                conflicts.append(relative)
        for relative, begin, end, block in (
            ("AGENTS.md", AGENTS_BEGIN, AGENTS_END, AGENTS_BLOCK),
            (".gitignore", GITIGNORE_BEGIN, GITIGNORE_END, GITIGNORE_BLOCK),
            (".gitattributes", GITATTRIBUTES_BEGIN, GITATTRIBUTES_END, GITATTRIBUTES_BLOCK),
        ):
            path = root / relative
            text = path.read_text(encoding="utf-8") if path.is_file() else ""
            current_block = extract_block(text, begin, end)
            key = relative + ":block"
            previous = old_records.get(key, {})
            expected = previous.get("hash")
            if current_block == block or (not previous.get("customized") and
                                          (current_block is None or text_hash(current_block) == expected)):
                path.write_text(replace_or_append_block(text, begin, end, block),
                                encoding="utf-8", newline="\n")
                changed.append(key)
            else:
                candidate = candidates / (relative + ".new")
                candidate.parent.mkdir(parents=True, exist_ok=True)
                candidate.write_text(block + "\n", encoding="utf-8")
                conflicts.append(key)
        installation = installation_record(root, target_version)
        for key in conflicts:
            installation["managed_files"][key]["customized"] = True
        write_json(install_path, installation)
        ensure_resource_directories(root)
        journal = root / "maintenance/events.jsonl"
        timezone = read_project_config(root).get("timezone", "Asia/Shanghai")
        existing_events = load_events(journal)
        profile_migration = profile_authority_migration(existing_events, timezone)
        if profile_migration:
            _preserving_append(journal, profile_migration)
            changed.append("maintenance/events.jsonl:project-profile-authority-v4")
            existing_events.append(profile_migration)
        event = new_event(
            "workflow.upgraded",
            branch="main",
            task_id=None,
            payload={
                "from_version": old_version,
                "to_version": target_version,
                "template_version": TEMPLATE_VERSION,
                "build_id": build_identity().get("build_id"),
                "conflicts": conflicts,
            },
            timezone=timezone,
        )
        _preserving_append(journal, event)
        database = root / ".project_hooks/maintenance.sqlite3"
        for database_file in (database, Path(str(database) + "-wal"), Path(str(database) + "-shm")):
            if database_file.exists():
                database_file.unlink()
        connection = ensure_database(database, journal)
        integrity = integrity_check(connection)
        connection.close()
        if integrity != "ok":
            raise ProjectManagerError(f"SQLite integrity_check: {integrity}")
        validate_project(root)
        if executable_path is not None and staged_in_legacy:
            write_pointer(target_version, root, executable_path, build_id=cache_build_id, legacy=True)
        try:
            from .diagnostics import cleanup_after_update
            diagnostic_cleanup = cleanup_after_update(
                root, from_version=str(old_version or "unknown"), to_version=target_version,
            )
        except Exception as exc:
            diagnostic_cleanup = {"status": "failed", "error_type": type(exc).__name__, "records": 0}
        result = {
            "status": "needs-attention" if conflicts else "updated",
            "project": str(root),
            "application_version": target_version,
            "changed": sorted(set(changed)),
            "conflicts": conflicts,
            "running_build_id": build_identity().get("build_id"),
            "template_version": TEMPLATE_VERSION,
            "verification": {"database": "ok", "project": "passed", "templates": "conflicts" if conflicts else "updated"},
            "diagnostic_cleanup": diagnostic_cleanup,
            "commit": "请检查变更后自行执行 git add、git commit 和 git push",
        }
    except Exception:
        _restore(backup, existed, root)
        shutil.rmtree(root / ".project_hooks/update-conflicts" / target_version, ignore_errors=True)
        raise
    finally:
        shutil.rmtree(backup, ignore_errors=True)
    try:
        result["linked_worktree_migration"] = migrate_project(root)
        if executable_path is not None:
            shared = cached_executable(root, target_version, cache_build_id)
            if executable_path != shared:
                shared.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(executable_path, shared)
                shutil.copy2(executable_path.parent / ".exe-sha256", shared.parent / ".exe-sha256")
            write_pointer(target_version, root, shared, build_id=cache_build_id)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        result["status"] = "needs-attention"
        result["linked_worktree_migration"] = {"status": "incomplete", "reason": str(exc),
                                              "next": "worktree migrate --apply"}
    return result

"""Project scaffolding, managed-file migration, and auditable upgrades."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from ..git.build_identity import build_identity
from ..git import client as git_client
from .resource_layout import RESOURCE_DIRECTORIES, ensure_resource_directories
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


TEMPLATE_VERSION = 7
INSTALLATION_PATH = Path(".codex/project-maintenance-installation.json")
CONFIG_PATH = Path(".codex/project-maintenance-workflow.json")
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

- 所有命令使用仓库根目录的 `.\\workflow-monitor.exe`。
- 新 clone 或 worktree 首次使用时，将 `workflow-monitor.exe` 放到仓库根目录并运行 `.\\workflow-monitor.exe install`。
- 每次任务先运行 `.\\workflow-monitor.exe context --format markdown`，按 `core_read_order` 阅读规范，首次写入前运行 `start`。
- 用户只需用自然语言描述任务；AI 提取目标、验收和证据，任务 ID、时间、分支、状态令牌和安全默认值由工作流代码处理。
- 稳定维护只在 `main` 使用 `--track stable`；研究和实验使用独立探索分支；无法可靠判断轨道时必须在对话中询问用户。
- 一个探索分支只对应一条探索记录；同一分支的后续任务复用该记录，任务回执和 Git 提交仅作为探索内部的过程证据。
- 使用 `state update --current-step ...` 和 `attempt update` 保存进展，最后必须运行 `end`。
- 代码写入后运行 fast；结束时按 Context 的测试回执门禁补齐 full 或 release。
- 探索启动前必须有关联研究篇章或记录明确的无篇章理由；有关联篇章的任务结束时必须显式审阅篇章。
- 项目资料和研究篇章只通过 `project update` 与内部 `stage` 指令记录；新证据改变判断时用 `stage update --revision ... --summary ... --evidence ...` 追加修订；探索进度使用 `attempt update` 的结构化步骤字段。
- 科研资料文件只放在 `resources/` 的八个标准子目录；`resources/sparks/` 可自由保存，其他资料通过 `catalog` 登记。
- 只有 `validated` 尝试可准备 Squash PR；推送和合并必须由用户明确确认。
- 禁止改写既有 `maintenance/events.jsonl` 行、直接编辑 SQLite、删除活动状态或绕过 `end`。
- 崩溃后运行 `task recover`；明确放弃时运行 `task abandon --reason <原因>`，不得手工删除 sidecar 或活动任务行。
- 遇到故障时使用 `diagnostics status/export` 生成脱敏本地诊断；程序不自动上传数据。
- Dashboard 只读展示任务、研究篇章、动作可用性和阻塞原因；生命周期写入由 AI 调用结构化服务，高风险动作必须在对话中取得用户确认。
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
/diagnostics-export/
# project-maintenance-hooks:end"""

GITATTRIBUTES_BLOCK = """# project-maintenance-hooks:begin
.githooks/* text eol=lf
.gitignore text eol=lf
.gitattributes text eol=lf
AGENTS.md text eol=lf
maintenance/*.md text eol=lf
maintenance/events.jsonl text eol=lf merge=union
# project-maintenance-hooks:end"""

MAINTENANCE_README = """# Workflow Monitor 项目维护索引

动态事实只写入 `maintenance/events.jsonl`，SQLite、Context、Dashboard、交接和研究篇章视图均为可重建投影。

- [CORE.md](CORE.md)：每次 AI 任务必读的生命周期、轨道、安全、测试和篇章契约。
- [RESEARCH.md](RESEARCH.md)：研究篇章、探索、Catalog 与 Sparks。
- [OPERATIONS.md](OPERATIONS.md)：安装、升级、恢复、诊断、交付与发布。
- `ARCHITECTURE.md`：代码边界和读写模型架构。

`maintenance/events.jsonl` 只追加且不得改写历史行；`.project_hooks/maintenance.sqlite3` 和测试回执均为本地生成物。
"""

MAINTENANCE_CORE = """# Workflow Monitor 核心契约

1. 每次任务先运行 `.\\workflow-monitor.exe context --format markdown`，再读取本文件；首次写入前运行 `start`。
2. 稳定维护只在 `main` 使用 `--track stable`；不确定改动使用 `research|experiment|sandbox`。无法判断时询问用户。
3. 动态事实只记录一次：任务目标和验收来自 `task.started`，项目资料来自 `project.profile_updated`，任务进度来自 `task.checkpointed`，结束结果来自 `task.finished`。
4. 使用 `state update --current-step ...` 保存 checkpoint；探索证据使用 `attempt update`。
5. 代码写入后运行 `python scripts/run_tests.py fast`。结束门禁按实际改动要求同一指纹的 fast/full；release profile 要求 fast/release。纯文档和治理改动由 `end` 内置检查验证。
6. 有关联研究篇章的任务必须在 `end --stage-review updated|reviewed-no-change` 明确审阅。`updated` 必须有本任务产生的篇章事件；新证据改变判断时以 `stage update --revision ... --summary ... --evidence ...` 追加修订。
7. 最后必须运行 `end`。禁止改写既有事件、直接编辑 SQLite、删除活动 sidecar、在 main 试改、自动合并。
8. 推送、PR、合并、数据库重建、软件升级和正式发布必须取得用户授权。
"""

MAINTENANCE_RESEARCH = """# 科研与研究篇章规范

全项目最多一个 active 研究篇章。使用内部 `stage start/update` 管理篇章事实；探索只引用篇章和任务 ID，不复制目标状态。

篇章允许反复推进和关联多轮探索。进入 `paused` 时必须记录 `interruption`（暂时中断）或 `temporary-closure`（暂时收束）及说明；暂停后可插入另一篇章，待其不再 active 后恢复旧篇章。`completed` 与 `cancelled` 永久封存，不得重开。

新证据改变当前判断时，使用 `stage update --revision ... --summary ... --evidence ...` 追加判断修订。修订必须同步最新篇章概况并引用至少一项证据；它属于篇章历史，不恢复独立决策体系。

探索轨道启动前若没有 active 研究篇章，工作流返回篇章草案并阻止启动。用户确认后先用短 stable 生命周期创建篇章；用户明确不需要篇章时，用 `--without-stage-reason` 记录理由。

科研资料放在 `resources/source|data|theory|analysis|outputs|others|reports|sparks/`。Sparks 可自由保存 Markdown，登记到 `catalog` 是可选的；其他资料通过 `catalog` 登记。Markdown 公式使用 Markdown/LaTeX 语法。

只有证据完整且状态为 `validated` 的探索可准备 Squash PR；合并等待用户明确确认。
"""

MAINTENANCE_OPERATIONS = """# 安装、恢复与交付

- 新 clone/worktree：放置根目录 EXE，运行 `.\\workflow-monitor.exe install`。
- 升级：在无活动任务、干净且同步的 main 上运行 `.\\workflow-monitor.exe update`。首次写入 Schema v5 后不可降级到仅支持 v1-v4 的版本。
- 恢复：异常中断运行 `task recover`；明确放弃运行 `task abandon --reason <原因>`。
- 诊断：使用 `diagnostics status/export`；诊断不自动上传。
- 验证：代码写入后 fast，任务结束前按 Context 的 required_actions 补齐 full/release，再运行 `check` 与 `db verify`。
- 交付：Dashboard 只读。提交、推送、PR、合并、正式 EXE 构建和发布分别等待用户确认。
"""

LEGACY_MAINTENANCE_README = """# 项目维护规范

本项目使用 Workflow Monitor 保存项目资料、研究篇章、任务生命周期、研究尝试、交接和科研资料索引。
`maintenance/events.jsonl` 是追加式永久事件源；`.project_hooks/maintenance.sqlite3`
是可从事件源重建的本地查询投影。

## 日常流程

所有命令使用仓库根目录的 `.\\workflow-monitor.exe`。

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
| `resources/analysis/` | 分析代码、Notebook 和实验 |
| `resources/outputs/` | 图表、模型和其他成果 |
| `resources/others/` | 暂时无法可靠分类的资料 |
| `resources/reports/` | 面向外部受众的项目总结与报告 |
| `resources/sparks/` | 自由 Markdown 灵感；可选择性登记 |

原始资料不覆盖；过程与成果分开。Markdown 文件中的公式使用 Markdown/LaTeX 语法。
`catalog scan` 固定扫描以上七个目录；`add`、`update` 和 `ingest` 拒绝目录与资料类型不一致。
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
        "core_read_order": ["maintenance/CORE.md"],
        "context_command": ".\\workflow-monitor.exe context --format markdown",
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


def template_files() -> dict[str, str]:
    files = {
        ".githooks/pre-commit": HOOK_TEMPLATE,
        "maintenance/README.md": MAINTENANCE_README,
        "maintenance/CORE.md": MAINTENANCE_CORE,
        "maintenance/RESEARCH.md": MAINTENANCE_RESEARCH,
        "maintenance/OPERATIONS.md": MAINTENANCE_OPERATIONS,
    }
    files.update({f"{item.relative_path}/.gitkeep": "\n" for item in RESOURCE_DIRECTORIES})
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


def initialize_project(root: Path, application_version: str) -> dict:
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    require_git_repository(root)
    if (root / CONFIG_PATH).exists():
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
    return {"status": "initialized", "project": str(root), "application_version": application_version}


def preflight_update(root: Path) -> None:
    require_git_repository(root)
    installation = root / INSTALLATION_PATH
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
    path = root / CONFIG_PATH
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
    current.setdefault("context_command", defaults["context_command"])
    current.setdefault("maintenance_store", defaults["maintenance_store"])
    current["maintenance_store"]["schema_version"] = SCHEMA_VERSION
    current.setdefault("branch_policy", defaults["branch_policy"])
    current.setdefault("git_auto_commit", defaults["git_auto_commit"])
    write_json(path, current)


def validate_project(root: Path) -> None:
    config = json.loads((root / CONFIG_PATH).read_text(encoding="utf-8"))
    required = default_config()
    for key in required:
        if key not in config:
            raise ProjectManagerError(f"升级后配置缺少字段: {key}")
    for relative in (
        INSTALLATION_PATH,
        Path(".githooks/pre-commit"),
        Path("maintenance/README.md"),
        Path("maintenance/CORE.md"),
        Path("maintenance/RESEARCH.md"),
        Path("maintenance/OPERATIONS.md"),
        Path("maintenance/events.jsonl"),
    ):
        if not (root / relative).is_file():
            raise ProjectManagerError(f"升级后缺少文件: {relative.as_posix()}")
    for item in RESOURCE_DIRECTORIES:
        if not (root / item.relative_path).is_dir():
            raise ProjectManagerError(f"升级后缺少资源目录: {item.relative_path}")
    load_events(root / "maintenance/events.jsonl")
    configured = git(root, "config", "--local", "--get", "core.hooksPath").stdout.strip()
    if configured != ".githooks":
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


def apply_project_update(root: Path, target_version: str) -> dict:
    root = root.resolve()
    preflight_update(root)
    install_path = root / INSTALLATION_PATH
    installation = json.loads(install_path.read_text(encoding="utf-8"))
    old_version = installation.get("application_version", installation.get("core_version"))
    old_records = installation.get("managed_files", {})
    database = root / ".project_hooks/maintenance.sqlite3"
    paths = [root / CONFIG_PATH, install_path, root / "maintenance/events.jsonl",
             database, Path(str(database) + "-wal"), Path(str(database) + "-shm")]
    paths.extend(root / item for item in template_files())
    paths.extend((root / "AGENTS.md", root / ".gitignore", root / ".gitattributes"))
    backup, existed = _backup(paths, root)
    changed: list[str] = []
    conflicts: list[str] = []
    try:
        migrate_config(root)
        changed.append(CONFIG_PATH.as_posix())
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
        timezone = json.loads((root / CONFIG_PATH).read_text(encoding="utf-8")).get("timezone", "Asia/Shanghai")
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
        try:
            from .diagnostics import cleanup_after_update
            diagnostic_cleanup = cleanup_after_update(
                root, from_version=str(old_version or "unknown"), to_version=target_version,
            )
        except Exception as exc:
            diagnostic_cleanup = {"status": "failed", "error_type": type(exc).__name__, "records": 0}
        return {
            "status": "updated",
            "project": str(root),
            "application_version": target_version,
            "changed": sorted(set(changed)),
            "conflicts": conflicts,
            "diagnostic_cleanup": diagnostic_cleanup,
            "commit": "请检查变更后自行执行 git add、git commit 和 git push",
        }
    except Exception:
        _restore(backup, existed, root)
        shutil.rmtree(root / ".project_hooks/update-conflicts" / target_version, ignore_errors=True)
        raise
    finally:
        shutil.rmtree(backup, ignore_errors=True)

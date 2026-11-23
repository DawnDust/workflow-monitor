"""Durable clone-local task authority. This database is not a rebuildable projection."""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from ...core.events import canonical_json
from ..git.worktrees import repository_context


OCCUPIED = ("starting", "active", "paused", "blocked", "finishing")
REGISTRY_SCHEMA = 2

WORKFLOWS_SQL = """
CREATE TABLE IF NOT EXISTS workflows(
    workflow_id TEXT PRIMARY KEY, branch TEXT NOT NULL UNIQUE, track TEXT NOT NULL,
    topic TEXT, path TEXT, worktree_id TEXT, base_commit TEXT,
    status TEXT NOT NULL DEFAULT 'open', archive_branch TEXT, predecessor_id TEXT,
    closure_json TEXT
)
"""


class RegistryError(RuntimeError):
    pass


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def legacy_active_rows(database: Path):
    if not database.is_file():
        return []
    connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
    try:
        if connection.execute("SELECT 1 FROM sqlite_master WHERE name='active_tasks'").fetchone():
            return connection.execute("SELECT record_json,state_updated FROM active_tasks").fetchall()
        return []
    except sqlite3.Error as exc:
        raise RegistryError("维护数据库无法读取: " + str(exc)) from exc
    finally:
        connection.close()


def registry_for(root: Path):
    context = repository_context(root)
    path = context.shared_dir / "registry.sqlite3"
    if not path.exists():
        return None
    registry = ProjectRegistry(path)
    return registry if registry.enabled else None


class ProjectRegistry:
    def __init__(self, path: Path):
        self.path = path

    def connect(self):
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=10000")
        connection.execute("PRAGMA synchronous=FULL")
        if connection.execute("PRAGMA user_version").fetchone()[0] not in {1, REGISTRY_SCHEMA}:
            connection.close()
            raise RegistryError("注册表版本不兼容；保留原数据库，请使用兼容版本")
        return connection

    @contextmanager
    def read(self):
        connection = self.connect()
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def transaction(self):
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @classmethod
    def create(cls, path: Path):
        if path.exists():
            registry = cls(path)
            with registry.read():
                pass
            return registry
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path, timeout=10)
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS counters(day TEXT PRIMARY KEY, value INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS tasks(
                    task_id TEXT PRIMARY KEY, worktree_id TEXT NOT NULL, path TEXT NOT NULL,
                    branch TEXT NOT NULL, status TEXT NOT NULL, started_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 0,
                    state_json TEXT NOT NULL, token TEXT, owner_json TEXT, workflow_id TEXT);
                CREATE UNIQUE INDEX IF NOT EXISTS one_open_task_per_worktree ON tasks(worktree_id)
                    WHERE status IN ('starting','active','paused','blocked','finishing');
                CREATE TABLE IF NOT EXISTS used_ids(task_id TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS audit(
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL UNIQUE,
                    worktree_id TEXT NOT NULL, path TEXT NOT NULL, event_json TEXT NOT NULL,
                    synced INTEGER NOT NULL DEFAULT 0, error TEXT);
                CREATE TABLE IF NOT EXISTS migration_conflicts(
                    conflict_id TEXT PRIMARY KEY, source_json TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS resolutions(
                    conflict_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, decision_json TEXT NOT NULL);
                PRAGMA user_version=2;
            """)
            connection.execute(WORKFLOWS_SQL)
            connection.commit()
        finally:
            connection.close()
        return cls(path)

    @property
    def enabled(self):
        with self.read() as connection:
            row = connection.execute("SELECT value FROM settings WHERE key='enabled'").fetchone()
            return bool(row and row[0] == "true")

    def setting(self, key: str):
        with self.read() as connection:
            row = connection.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
            return json.loads(row[0]) if row else None

    def configure(self, **values):
        with self.transaction() as connection:
            for key, value in values.items():
                connection.execute("INSERT INTO settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, canonical_json(value)))

    def tasks(self):
        with self.read() as connection:
            return [dict(row) for row in connection.execute("SELECT * FROM tasks ORDER BY started_at,task_id")]

    def task(self, task_id):
        with self.read() as connection:
            row = connection.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
            return dict(row) if row else None

    def active(self, root: Path):
        identity = repository_context(root).worktree_id
        with self.read() as connection:
            rows = connection.execute("SELECT * FROM tasks WHERE worktree_id=? AND status IN ('starting','active','paused','blocked','finishing')", (identity,)).fetchall()
        return dict(rows[0]) if rows else None

    @property
    def schema(self):
        with self.read() as connection:
            return connection.execute("PRAGMA user_version").fetchone()[0]

    def upgrade_schema(self):
        with self.transaction() as connection:
            if connection.execute("PRAGMA user_version").fetchone()[0] == 1:
                connection.execute("ALTER TABLE tasks ADD COLUMN workflow_id TEXT")
            connection.execute(WORKFLOWS_SQL)
            connection.execute("PRAGMA user_version=2")

    def workflows(self):
        if self.schema < REGISTRY_SCHEMA:
            return []
        with self.read() as connection:
            return [dict(row) for row in connection.execute(
                "SELECT * FROM workflows ORDER BY workflow_id<>'main', workflow_id")]

    def workflow(self, workflow_id):
        return next((row for row in self.workflows() if row["workflow_id"] == workflow_id), None)

    def register_workflow(self, *, workflow_id, branch, track, topic=None, path=None,
                          worktree_id=None, base_commit=None, archive_branch=None, predecessor_id=None):
        with self.transaction() as connection:
            connection.execute(
                """INSERT OR IGNORE INTO workflows
                   (workflow_id,branch,track,topic,path,worktree_id,base_commit,status,archive_branch,predecessor_id)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (workflow_id, branch, track, topic, str(path) if path else None, worktree_id,
                 base_commit, "archived" if archive_branch else "open", archive_branch, predecessor_id))
            connection.execute("UPDATE tasks SET workflow_id=? WHERE branch=? AND workflow_id IS NULL",
                               (workflow_id, branch))

    def set_workflow_location(self, workflow_id, path, identity):
        with self.transaction() as connection:
            connection.execute("UPDATE workflows SET path=?,worktree_id=? WHERE workflow_id=?",
                               (str(path) if path else None, identity, workflow_id))
            if path:
                connection.execute("UPDATE tasks SET path=?,worktree_id=? WHERE workflow_id=?",
                                   (str(path), identity, workflow_id))
                connection.execute("UPDATE audit SET path=? WHERE worktree_id=? AND synced=0",
                                   (str(path), identity))

    def relocate_worktree(self, identity, path):
        with self.transaction() as connection:
            connection.execute("UPDATE workflows SET path=? WHERE worktree_id=?", (str(path), identity))
            connection.execute("UPDATE tasks SET path=? WHERE worktree_id=?", (str(path), identity))
            connection.execute("UPDATE audit SET path=? WHERE worktree_id=? AND synced=0", (str(path), identity))

    def set_closure(self, workflow_id, status, closure, archive_branch=None):
        with self.transaction() as connection:
            connection.execute(
                "UPDATE workflows SET status=?,closure_json=?,archive_branch=? WHERE workflow_id=?",
                (status, canonical_json(closure), archive_branch, workflow_id))

    def events(self):
        with self.read() as connection:
            return [json.loads(row[0]) for row in connection.execute("SELECT event_json FROM audit ORDER BY sequence")]

    def reserve(self, root: Path, day: str, *, track: str, topic: str | None = None,
                workflow_id=None, requested_id=None, base_commit=None, predecessor_id=None):
        context = repository_context(root)
        primary = Path(self.setting("primary_root"))
        managed = bool(self.setting("unified"))
        with self.transaction() as connection:
            workflow = connection.execute("SELECT * FROM workflows WHERE workflow_id=?", (workflow_id,)).fetchone() if workflow_id else None
            if workflow_id and (not workflow or workflow["status"] != "open"):
                raise RegistryError("工作流不存在或已经归档")
            row = connection.execute("SELECT value FROM counters WHERE day=?", (day,)).fetchone()
            index = (row[0] if row else 0) + 1
            while connection.execute("SELECT 1 FROM used_ids WHERE task_id=?", (f"{day}-{index:03d}",)).fetchone():
                index += 1
            task_id = requested_id or f"{day}-{index:03d}"
            if connection.execute("SELECT 1 FROM used_ids WHERE task_id=?", (task_id,)).fetchone():
                raise RegistryError("任务 ID 已使用: " + task_id)
            if not requested_id:
                connection.execute("INSERT INTO counters VALUES (?,?) ON CONFLICT(day) DO UPDATE SET value=excluded.value", (day, index))
            connection.execute("INSERT INTO used_ids VALUES (?)", (task_id,))
            branch = "main" if track == "stable" else f"{track}/{task_id}-{topic}"
            directory = primary / ".worktrees" if managed else primary.parent / (primary.name + ".worktrees")
            path = primary if track == "stable" else directory / f"{task_id}-{topic}"
            identity = context.worktree_id if track == "stable" else "reserved:" + task_id
            if workflow:
                branch, path = workflow["branch"], Path(workflow["path"])
                identity = workflow["worktree_id"] or "reserved:" + workflow_id
            else:
                workflow_id = "main" if track == "stable" else task_id
                connection.execute(
                    """INSERT OR IGNORE INTO workflows
                       (workflow_id,branch,track,topic,path,worktree_id,base_commit,predecessor_id)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (workflow_id, branch, track, topic, str(path), identity, base_commit, predecessor_id))
            reservation_id = uuid.uuid4().hex
            timestamp = now()
            try:
                connection.execute(
                    """INSERT INTO tasks
                       (task_id,worktree_id,path,branch,status,started_at,updated_at,state_json,token,workflow_id)
                       VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (task_id, identity, str(path), branch, "starting", timestamp, timestamp, "{}", reservation_id, workflow_id))
            except sqlite3.IntegrityError as exc:
                raise RegistryError("同一工作树已有未结束任务") from exc
        return self.task(task_id)

    def bind(self, root: Path, task_id: str, reservation_id: str):
        context = repository_context(root)
        with self.transaction() as connection:
            row = connection.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
            if not row or row["token"] != reservation_id or row["status"] != "starting" or Path(row["path"]).resolve() != root.resolve():
                raise RegistryError("任务预留与目标工作树不匹配")
            connection.execute("UPDATE tasks SET worktree_id=? WHERE task_id=?", (context.worktree_id, task_id))
            connection.execute("UPDATE workflows SET worktree_id=? WHERE workflow_id=?",
                               (context.worktree_id, row["workflow_id"]))

    def prepare(self, task_id, arguments, base_commit):
        with self.transaction() as connection:
            connection.execute("UPDATE tasks SET state_json=? WHERE task_id=? AND status='starting'", (canonical_json({"request_args": arguments, "base_commit": base_commit}), task_id))

    def save(self, root: Path, record: dict, *, state_updated=False, phase="active", finish=None):
        with self.transaction() as connection:
            row = connection.execute("SELECT * FROM tasks WHERE task_id=?", (record["task_id"],)).fetchone()
            if not row or row["worktree_id"] != repository_context(root).worktree_id:
                raise RegistryError("任务未在本工作树登记")
            if row["status"] not in OCCUPIED:
                raise RegistryError("任务已结束，拒绝覆盖终态")
            if row["status"] == "finishing" and phase != "finishing":
                raise RegistryError("任务正在收尾，请恢复或重试原 end")
            if "_registry_revision" not in record and row["revision"]:
                raise RegistryError("任务状态缺少版本，请重新读取后重试")
            expected = record.get("_registry_revision", row["revision"])
            if row["revision"] != expected:
                raise RegistryError("任务状态已更新，请重新读取后重试")
            if row["status"] in {"paused", "blocked"} and phase == "active":
                phase = row["status"]
            revision = row["revision"] + 1
            durable = {k: v for k, v in record.items() if k != "_registry_revision"}
            prior_state = json.loads(row["state_json"])
            if prior_state.get("record", {}).get("checkpoint"):
                durable["checkpoint"] = prior_state["record"]["checkpoint"]
            state = {"record": durable, "phase": phase, "checkpoint_recorded": bool(state_updated), "finish": finish}
            connection.execute("UPDATE tasks SET state_json=?,status=?,updated_at=?,revision=? WHERE task_id=?",
                               (canonical_json(state), phase, now(), revision, record["task_id"]))
            record["_registry_revision"] = revision

    def record_events(self, root: Path, events: list[dict], *, worktree_id: str | None = None):
        identity = worktree_id or repository_context(root).worktree_id
        with self.transaction() as connection:
            for event in events:
                encoded = canonical_json(event)
                prior = connection.execute("SELECT event_json FROM audit WHERE event_id=?", (event["event_id"],)).fetchone()
                if prior and prior[0] != encoded:
                    raise RegistryError("事件 ID 内容冲突: " + event["event_id"])
                if prior:
                    continue
                connection.execute("INSERT INTO audit(event_id,worktree_id,path,event_json) VALUES (?,?,?,?)", (event["event_id"], identity, str(root), encoded))
                payload = event["payload"]
                if event["event_type"] == "task.checkpointed":
                    row = connection.execute("SELECT state_json FROM tasks WHERE task_id=? AND worktree_id=?", (event["task_id"], identity)).fetchone()
                    if row:
                        state = json.loads(row[0])
                        checkpoint = dict(payload)
                        if payload.get("source_event_id"):
                            source = connection.execute("SELECT event_json FROM audit WHERE event_id=?", (payload["source_event_id"],)).fetchone()
                            if source:
                                source_payload = json.loads(source[0])["payload"]
                                for name in ("current_step", "progress", "blocker"):
                                    if name in source_payload:
                                        checkpoint.setdefault(name, source_payload[name])
                                if source_payload.get("next_step"):
                                    checkpoint.setdefault("next_actions", [source_payload["next_step"]])
                        state.setdefault("record", {}).setdefault("checkpoint", {}).update(checkpoint)
                        connection.execute("UPDATE tasks SET state_json=?,updated_at=? WHERE task_id=?", (canonical_json(state), now(), event["task_id"]))
                elif event["event_type"] == "task.finished":
                    status = "abandoned" if payload["result"] == "abandoned" else "finishing"
                    connection.execute("UPDATE tasks SET status=?,updated_at=?,revision=revision+1 WHERE task_id=? AND worktree_id=?", (status, now(), event["task_id"], identity))
                elif event["event_type"] == "task.lifecycle_changed":
                    status = payload["status"]
                    connection.execute("UPDATE tasks SET status=?,updated_at=?,revision=revision+1 WHERE task_id=? AND worktree_id=?", (status, now(), event["task_id"], identity))

    def relocate(self, root, task_id):
        identity = repository_context(root).worktree_id
        with self.transaction() as connection:
            row = connection.execute("SELECT worktree_id FROM tasks WHERE task_id=?", (task_id,)).fetchone()
            if not row or row[0] != identity:
                raise RegistryError("工作树身份不匹配，拒绝迁移任务归属")
            connection.execute("UPDATE tasks SET path=? WHERE task_id=?", (str(root), task_id))
            if self.schema >= REGISTRY_SCHEMA:
                connection.execute("UPDATE workflows SET path=? WHERE worktree_id=?", (str(root), identity))
            connection.execute("UPDATE audit SET path=? WHERE worktree_id=? AND synced=0", (str(root), identity))

    def pending(self):
        with self.read() as connection:
            return [dict(row) for row in connection.execute("SELECT * FROM audit WHERE synced=0 ORDER BY sequence")]

    def sync_result(self, event_ids, error=None):
        with self.transaction() as connection:
            connection.executemany("UPDATE audit SET synced=?,error=? WHERE event_id=?", [(int(error is None), error, event_id) for event_id in event_ids])

    def heartbeat(self, task_id, owner=None):
        with self.transaction() as connection:
            row = connection.execute("SELECT status FROM tasks WHERE task_id=?", (task_id,)).fetchone()
            if not row or row[0] not in OCCUPIED:
                raise RegistryError("任务不存在或已经结束")
            connection.execute("UPDATE tasks SET updated_at=?,owner_json=COALESCE(?,owner_json) WHERE task_id=?", (now(), canonical_json(owner) if owner else None, task_id))

    def attempt_id(self, branch):
        with self.read() as connection:
            row = connection.execute("SELECT json_extract(event_json,'$.payload.attempt_id') FROM audit WHERE json_extract(event_json,'$.event_type')='attempt.started' AND json_extract(event_json,'$.branch')=? ORDER BY sequence DESC LIMIT 1", (branch,)).fetchone()
            return row[0] if row else None

    def finalize(self, task_id):
        with self.transaction() as connection:
            connection.execute("UPDATE tasks SET status='completed',updated_at=? WHERE task_id=? AND status='finishing'", (now(), task_id))

    def import_task(self, root: Path, state: dict):
        context = repository_context(root)
        record = state["record"]
        task_id = record["task_id"]
        with self.transaction() as connection:
            prior = connection.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
            if prior:
                if prior["worktree_id"] != context.worktree_id:
                    raise RegistryError("任务 ID 归属冲突: " + task_id)
                return
            connection.execute("INSERT OR IGNORE INTO used_ids VALUES (?)", (task_id,))
            connection.execute("INSERT INTO tasks(task_id,worktree_id,path,branch,status,started_at,updated_at,state_json) VALUES (?,?,?,?,?,?,?,?)",
                               (task_id, context.worktree_id, str(root), record["git"]["branch"], state.get("phase", "active"), record["started_at"], now(), canonical_json(state)))

    def reserve_history(self, ids):
        with self.transaction() as connection:
            connection.executemany("INSERT OR IGNORE INTO used_ids VALUES (?)", [(item,) for item in ids if item])

    def import_events(self, root, events):
        identity = repository_context(root).worktree_id
        with self.transaction() as connection:
            for event in events:
                encoded = canonical_json(event)
                prior = connection.execute("SELECT event_json FROM audit WHERE event_id=?", (event["event_id"],)).fetchone()
                if prior and prior[0] != encoded:
                    raise RegistryError("历史事件内容冲突: " + event["event_id"])
                connection.execute("INSERT OR IGNORE INTO audit(event_id,worktree_id,path,event_json,synced) VALUES (?,?,?,?,1)", (event["event_id"], identity, str(root), encoded))

    def migration_conflicts(self, conflicts=None):
        if conflicts is not None:
            with self.transaction() as connection:
                for item in conflicts:
                    key = uuid.uuid5(uuid.NAMESPACE_URL, canonical_json(item)).hex
                    connection.execute("INSERT OR IGNORE INTO migration_conflicts VALUES (?,?)", (key, canonical_json(item)))
        with self.read() as connection:
            return [json.loads(row[0]) for row in connection.execute("SELECT source_json FROM migration_conflicts")]

    def resolution(self, conflict_id):
        with self.read() as connection:
            row = connection.execute("SELECT * FROM resolutions WHERE conflict_id=?", (conflict_id,)).fetchone()
            return dict(row) if row else None

    def resolve(self, conflict_id, fingerprint, decision):
        with self.transaction() as connection:
            connection.execute("INSERT INTO resolutions VALUES (?,?,?) ON CONFLICT(conflict_id) DO UPDATE SET fingerprint=excluded.fingerprint,decision_json=excluded.decision_json", (conflict_id, fingerprint, canonical_json(decision)))

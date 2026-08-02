from __future__ import annotations

import json
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from project_hooks.diagnostics import (
    append_record,
    bug_report_url,
    create_failure_record,
    cleanup_after_update,
    cleanup_resolved_diagnostics,
    diagnostics_overview,
    diagnostics_status,
    export_diagnostics,
    load_records,
    resolve_diagnostic,
    sanitize_text,
)


class DiagnosticsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "project"
        self.root.mkdir()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def record(self, exc: BaseException, command: str = "check") -> dict:
        return create_failure_record(
            self.root, exc, command=command, application_version="1.3.0",
            schema_version=3, execution_mode="source",
        )

    def test_sanitizes_paths_urls_and_credentials(self) -> None:
        raw = (
            f"failed at {self.root / 'secret.txt'} "
            "token=ghp_abcdefghijklmnopqrstuvwxyz1234 "
            "https://example.invalid/api?api_key=secret"
        )
        clean = sanitize_text(raw, project_root=self.root)
        self.assertNotIn(str(self.root), clean)
        self.assertNotIn("abcdefghijklmnopqrstuvwxyz", clean)
        self.assertNotIn("api_key=secret", clean)
        self.assertIn("<redacted", clean)

    def test_validation_is_not_internal_and_fingerprint_is_stable(self) -> None:
        first = self.record(ValueError("参数 12345 无效"))
        second = self.record(ValueError("参数 67890 无效"))
        self.assertEqual(first["category"], "validation")
        self.assertEqual(first["code"], "PH-E100")
        self.assertEqual(first["fingerprint"], second["fingerprint"])
        self.assertNotEqual(first["incident_id"], second["incident_id"])

    def test_concurrent_append_and_rotation_are_bounded(self) -> None:
        def worker(index: int) -> None:
            append_record(self.root, {"incident_id": str(index), "category": "validation"})

        with patch("project_hooks.diagnostics.MAX_RECORDS", 10):
            threads = [threading.Thread(target=worker, args=(index,)) for index in range(35)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
        records = load_records(self.root)
        self.assertEqual(len(records), 35)
        history = list((self.root / ".project_hooks/diagnostics").glob("events.*.jsonl"))
        self.assertLessEqual(len(history), 3)

    def test_export_uses_allowlist_and_survives_failed_checks(self) -> None:
        append_record(self.root, self.record(RuntimeError("boom")))
        database = self.root / ".project_hooks/maintenance.sqlite3"
        database.parent.mkdir(parents=True, exist_ok=True)
        database.write_bytes(b"corrupt but untouched")
        before = database.read_bytes()
        output = self.root / "bundle.zip"

        result = export_diagnostics(
            self.root, output, application_version="1.3.0", schema_version=3,
            execution_mode="source", checks=lambda: (_ for _ in ()).throw(RuntimeError("db failed")),
        )

        self.assertEqual(result["status"], "exported")
        self.assertEqual(database.read_bytes(), before)
        with zipfile.ZipFile(output) as archive:
            self.assertEqual(
                set(archive.namelist()),
                {"manifest.json", "report.md", "environment.json", "checks.json", "diagnostics.jsonl"},
            )
            checks = json.loads(archive.read("checks.json"))
            self.assertEqual(checks["status"], "failed")
            combined = b"".join(archive.read(name) for name in archive.namelist())
            self.assertNotIn(b"corrupt but untouched", combined)
            manifest = json.loads(archive.read("manifest.json"))
            self.assertEqual(manifest["export_id"], result["export_id"])
            self.assertEqual(manifest["incident_ids"], [result["latest_incident_id"]])
        overview = diagnostics_overview(self.root, application_version="1.3.0")
        self.assertTrue(overview["issues"][0]["exported"])
        self.assertEqual(overview["issues"][0]["last_export_id"], result["export_id"])

    def test_status_and_issue_url_do_not_upload(self) -> None:
        append_record(self.root, self.record(RuntimeError("unexpected")))
        status = diagnostics_status(self.root)
        self.assertEqual(status["records"], 1)
        self.assertFalse(status["automatic_upload"])
        url = bug_report_url(incident_id=status["latest_incident_id"], version="1.3.0")
        self.assertIn("github.com", url)
        self.assertIn("labels=bug", url)
        self.assertIn("body=", url)

    def test_conflict_and_integrity_have_distinct_codes(self) -> None:
        conflict = self.record(RuntimeError("目标冲突，拒绝覆盖"))
        integrity = self.record(RuntimeError("SQLite integrity check failed"))
        internal = self.record(RuntimeError("unexpected renderer failure"))
        self.assertEqual(conflict["code"], "PH-C200")
        self.assertEqual(integrity["code"], "PH-D300")
        self.assertEqual(internal["code"], "PH-I500")

    def test_update_cleanup_removes_only_old_validation_and_conflict(self) -> None:
        validation = self.record(ValueError("bad input"))
        conflict = self.record(RuntimeError("branch conflict"))
        protected = self.record(RuntimeError("unexpected renderer failure"))
        current = create_failure_record(
            self.root, ValueError("new bad input"), command="check",
            application_version="1.5.0", schema_version=3, execution_mode="source",
        )
        for record in (validation, conflict, protected, current):
            append_record(self.root, record)
        result = cleanup_after_update(self.root, from_version="1.3.0", to_version="1.5.0")
        remaining = load_records(self.root)
        self.assertEqual(result["status"], "cleaned")
        self.assertEqual(result["records"], 2)
        self.assertEqual(
            {item["incident_id"] for item in remaining},
            {protected["incident_id"], current["incident_id"]},
        )
        overview = diagnostics_overview(self.root, application_version="1.5.0")
        old = next(item for item in overview["issues"] if item["fingerprint"] == protected["fingerprint"])
        self.assertEqual(old["status"], "old_version_protected")

    def test_resolve_then_cleanup_is_explicit_and_recurrence_reappears(self) -> None:
        record = self.record(RuntimeError("unexpected renderer failure"))
        append_record(self.root, record)
        resolve_diagnostic(
            self.root, record["fingerprint"], reason="升级后已复核",
            application_version="1.5.0",
        )
        overview = diagnostics_overview(self.root, application_version="1.5.0")
        self.assertEqual(overview["issues"][0]["status"], "resolved")
        cleaned = cleanup_resolved_diagnostics(self.root)
        self.assertEqual(cleaned["records"], 1)
        self.assertEqual(load_records(self.root), [])
        recurrent = self.record(RuntimeError("unexpected renderer failure"))
        append_record(self.root, recurrent)
        self.assertEqual(len(load_records(self.root)), 1)


if __name__ == "__main__":
    unittest.main()

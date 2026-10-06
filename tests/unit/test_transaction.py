from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from project_hooks.infrastructure.persistence.transaction import mutation_lock


class MutationLockTests(unittest.TestCase):
    def test_another_process_reports_the_active_writer_as_busy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_dir = Path(directory)
            script = """
import json, sys
from pathlib import Path
from project_hooks.infrastructure.persistence.transaction import MutationLockError, mutation_lock
try:
    with mutation_lock(Path(sys.argv[1]), command='catalog.ingest', timeout=0):
        pass
except MutationLockError as exc:
    print(json.dumps({'lock_owner': exc.lock_owner, 'retry_after_seconds': exc.retry_after_seconds}))
"""
            with mutation_lock(state_dir, command="update", timeout=1):
                result = subprocess.run(
                    [sys.executable, "-c", script, str(state_dir)],
                    cwd=Path(__file__).resolve().parents[2],
                    text=True, encoding="utf-8", capture_output=True, check=True,
                )

            receipt = json.loads(result.stdout)
            self.assertEqual(receipt["lock_owner"]["command"], "update")
            self.assertEqual(receipt["lock_owner"]["pid"], __import__("os").getpid())
            self.assertEqual(receipt["retry_after_seconds"], 1)


if __name__ == "__main__":
    unittest.main()

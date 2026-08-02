"""Layered, parallel test runner for project-hooks maintainers."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
MODULES = (
    "tests.test_diagnostics", "tests.test_workflow_actions",
    "tests.test_workbench", "tests.test_project_hooks", "tests.test_distribution",
)
FAST_CLASSES = (
    "tests.test_diagnostics.DiagnosticsTests",
    "tests.test_workflow_actions.WorkflowActionTests",
    "tests.test_workflow_actions.DashboardActionWidgetTests",
    "tests.test_workbench.ExternalWorkbenchTests",
    "tests.test_project_hooks.DashboardPresentationTests",
)
CORE_SMOKE = (
    "tests.test_project_hooks.ProjectHooksSqliteTests.test_install_rebuilds_database_and_context_is_available",
    "tests.test_project_hooks.ProjectHooksSqliteTests.test_record_events_rolls_back_journal_when_projection_fails",
    "tests.test_project_hooks.ProjectHooksSqliteTests.test_failed_exploration_start_restores_branch_active_state_and_journal",
    "tests.test_project_hooks.ProjectHooksSqliteTests.test_end_can_update_final_state_in_one_step_with_auto_commit",
    "tests.test_project_hooks.ProjectHooksSqliteTests.test_catalog_cli_scan_relations_context_and_rebuild",
)
DISTRIBUTION_CLASS = "tests.test_distribution.DistributionTests"
# The first measured branch baseline is 46%. CLI integration tests execute copied
# project modules in child processes, so this conservative floor guards regressions
# without pretending those child paths are uncovered product behavior.
COVERAGE_FLOOR = 46


def flatten(suite: unittest.TestSuite) -> list[str]:
    result: list[str] = []
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            result.extend(flatten(item))
        else:
            result.append(item.id())
    return result


def load_ids(names: tuple[str, ...] | list[str]) -> list[str]:
    return flatten(unittest.defaultTestLoader.loadTestsFromNames(list(names)))


def changed_files() -> set[str]:
    completed = subprocess.run(
        ["git", "status", "--porcelain"], cwd=ROOT, text=True,
        encoding="utf-8", errors="replace", capture_output=True, check=False,
    )
    return {line[3:].replace("\\", "/") for line in completed.stdout.splitlines() if len(line) > 3}


def fast_ids() -> list[str]:
    changed = changed_files()
    names: list[str] = list(FAST_CLASSES)
    core_changed = any(
        path.startswith(("project_hooks/", "tests/")) or path in {"AGENTS.md", "maintenance/README.md"}
        for path in changed
    )
    distribution_changed = any(
        path.startswith(("scripts/", ".github/"))
        or path in {"project_hooks/project_manager.py", "project_hooks/updater.py", "project_hooks/windows_entry.py"}
        for path in changed
    )
    if core_changed or not changed:
        names.extend(CORE_SMOKE)
    if distribution_changed or any(path.startswith("tests/") for path in changed):
        names.append(DISTRIBUTION_CLASS)
    ids = load_ids(names)
    return list(dict.fromkeys(ids))


def chunks(items: list[str], count: int) -> list[list[str]]:
    result = [[] for _ in range(max(1, min(count, len(items))))]
    for index, item in enumerate(items):
        result[index % len(result)].append(item)
    return [chunk for chunk in result if chunk]


def run_chunk(test_ids: list[str], coverage: bool) -> tuple[int, float, str]:
    command = [sys.executable]
    if coverage:
        command += ["-m", "coverage", "run", "--branch", "--parallel-mode", "--source=project_hooks"]
    command += ["-m", "unittest", *test_ids]
    started = time.perf_counter()
    completed = subprocess.run(
        command, cwd=ROOT, text=True, encoding="utf-8", errors="replace",
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False,
    )
    return completed.returncode, time.perf_counter() - started, completed.stdout


def clean_coverage_data() -> None:
    candidates = [ROOT / ".coverage", *ROOT.glob(".coverage.*")]
    for path in candidates:
        if path.is_file():
            path.unlink()


def run_parallel(test_ids: list[str], *, jobs: int, coverage: bool) -> int:
    if coverage:
        clean_coverage_data()
    started = time.perf_counter()
    groups = chunks(test_ids, jobs)
    failures: list[tuple[int, float, str]] = []
    timings: list[float] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(groups)) as executor:
        futures = [executor.submit(run_chunk, group, coverage) for group in groups]
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            timings.append(result[1])
            if result[0] != 0:
                failures.append(result)
    elapsed = time.perf_counter() - started
    for _, duration, output in failures:
        print(f"\n--- failed worker ({duration:.2f}s) ---\n{output}", file=sys.stderr)
    print(json.dumps({
        "tests": len(test_ids), "workers": len(groups), "seconds": round(elapsed, 3),
        "slowest_worker_seconds": round(max(timings, default=0), 3), "failures": len(failures),
    }, ensure_ascii=False))
    if failures:
        return 1
    if coverage:
        combine = subprocess.run([sys.executable, "-m", "coverage", "combine"], cwd=ROOT, check=False)
        if combine.returncode:
            return combine.returncode
        report = subprocess.run(
            [sys.executable, "-m", "coverage", "report", "--show-missing", f"--fail-under={COVERAGE_FLOOR}"],
            cwd=ROOT, check=False,
        )
        if report.returncode:
            return report.returncode
    return 0


def validate_release_tag(application_version: str, tag: str | None = None) -> None:
    tag = os.environ.get("GITHUB_REF_NAME", "") if tag is None else tag
    if tag.startswith("v") and tag[1:] != application_version:
        raise RuntimeError(f"release tag/version mismatch: tag={tag}, application={application_version}")


def visible_window_title(process_id: int) -> str | None:
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes

    class ProcessEntry(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260),
        ]

    kernel32 = ctypes.windll.kernel32
    snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
    parent_by_pid: dict[int, int] = {}
    entry = ProcessEntry()
    entry.dwSize = ctypes.sizeof(ProcessEntry)
    if snapshot not in (0, -1) and kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
        while True:
            parent_by_pid[int(entry.th32ProcessID)] = int(entry.th32ParentProcessID)
            if not kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                break
        kernel32.CloseHandle(snapshot)
    process_ids = {process_id}
    changed = True
    while changed:
        changed = False
        for child, parent in parent_by_pid.items():
            if parent in process_ids and child not in process_ids:
                process_ids.add(child)
                changed = True

    titles: list[str] = []
    user32 = ctypes.windll.user32

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def collect(window, _parameter):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(window, ctypes.byref(owner))
        if owner.value in process_ids and user32.IsWindowVisible(window):
            length = user32.GetWindowTextLengthW(window)
            if length:
                buffer = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(window, buffer, length + 1)
                titles.append(buffer.value)
        return True

    user32.EnumWindows(collect, 0)
    return next((title for title in titles if title), None)


def wait_for_dashboard_window(process: subprocess.Popen, timeout: float) -> tuple[str | None, float]:
    started = time.monotonic()
    deadline = started + timeout
    title = None
    while process.poll() is None and time.monotonic() < deadline:
        title = visible_window_title(process.pid)
        if title:
            break
        time.sleep(0.05)
    return title, time.monotonic() - started


def release_smoke() -> int:
    from project_hooks import __version__
    validate_release_tag(__version__)
    subprocess.run([sys.executable, "scripts/build_windows_exe.py"], cwd=ROOT, check=True)
    subprocess.run(
        [sys.executable, "scripts/build_release.py", "--version", __version__], cwd=ROOT, check=True,
    )
    executable = ROOT / "dist" / "project-hooks.exe"
    version_process = subprocess.run(
        [str(executable), "version"], cwd=ROOT, check=True, capture_output=True,
    )
    version_text = version_process.stdout.decode("utf-8", "strict")
    version_data = json.loads(version_text)
    if version_data["application_version"] != __version__ or not version_data["build_identity"].get("build_id"):
        raise RuntimeError("frozen version/build identity smoke failed")
    manifest = json.loads((ROOT / "dist/release-manifest.json").read_text(encoding="utf-8"))
    if manifest["build_identity"].get("build_id") != version_data["build_identity"].get("build_id"):
        raise RuntimeError("release manifest build identity does not match executable")
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
        portable = Path(directory)
        copied = portable / "project-hooks.exe"
        shutil.copy2(executable, copied)
        process = subprocess.Popen(
            [str(copied)], cwd=portable, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        marker = portable / ".codex/project-maintenance-workflow.json"
        deadline = time.monotonic() + 20
        while not marker.is_file() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.1)
        if not marker.is_file():
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=5)
            stdout, stderr = process.communicate(timeout=5)
            raise RuntimeError(
                "frozen empty-folder bootstrap failed: "
                + (stderr or stdout).decode("utf-8", "replace")
            )
        if os.name == "nt":
            title, _elapsed = wait_for_dashboard_window(process, 3.0)
            if title != "Project Maintenance":
                raise RuntimeError("frozen no-argument startup did not expose a Dashboard window within 3 seconds")
        if process.poll() is None:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
                )
            else:
                process.terminate()
            process.wait(timeout=5)
        subprocess.run([str(copied), "check"], cwd=portable, check=True, capture_output=True)
        chinese = subprocess.run(
            [str(copied), "context", "--format", "markdown"], cwd=portable,
            check=True, capture_output=True,
        ).stdout.decode("utf-8", "strict")
        if "项目" not in chinese:
            raise RuntimeError("frozen UTF-8 Chinese output smoke failed")
        dashboard = subprocess.Popen(
            [str(copied), "dashboard", "--refresh-seconds", "0"], cwd=portable,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        title, elapsed = wait_for_dashboard_window(dashboard, 2.0)
        if dashboard.poll() is not None:
            stdout, stderr = dashboard.communicate(timeout=5)
            raise RuntimeError(
                "frozen operational Dashboard exited early: "
                + (stderr or stdout).decode("utf-8", "replace")
            )
        if os.name == "nt" and title != "Project Maintenance":
            raise RuntimeError(
                f"frozen Dashboard had no visible window after {elapsed:.2f}s"
            )
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(dashboard.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
            )
        else:
            dashboard.terminate()
        dashboard.wait(timeout=5)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("suite", choices=("list", "fast", "full", "release"))
    parser.add_argument("--jobs", type=int, default=min(6, os.cpu_count() or 1))
    args = parser.parse_args(argv)
    all_ids = load_ids(MODULES)
    if args.suite == "list":
        print("\n".join(all_ids))
        print(f"\n{len(all_ids)} scenarios")
        return 0
    selected = fast_ids() if args.suite == "fast" else all_ids
    code = run_parallel(selected, jobs=max(1, args.jobs), coverage=args.suite in {"full", "release"})
    if code or args.suite != "release":
        return code
    return release_smoke()


if __name__ == "__main__":
    raise SystemExit(main())

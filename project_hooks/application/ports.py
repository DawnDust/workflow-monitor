"""Small structural interfaces used by application services.

The project intentionally uses ``Protocol`` instead of a dependency-injection
framework. Concrete implementations are assembled in ``project_hooks.composition``.
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any, Callable, Protocol


class GitGateway(Protocol):
    def run(self, *arguments: str, check: bool = True) -> str: ...


class EventRepository(Protocol):
    def load(self) -> list[dict[str, Any]]: ...

    def record(self, events: list[dict[str, Any]]) -> None: ...


class SnapshotProvider(Protocol):
    def __call__(self) -> dict[str, Any]: ...


class MutationLockFactory(Protocol):
    def __call__(
        self, state_dir: Path, *, command: str, task_id: str | None, timeout: float,
    ) -> AbstractContextManager[Any]: ...


class FailureRecorder(Protocol):
    def __call__(self, root: Path, error: Exception, **metadata: Any) -> dict[str, Any]: ...


ProgressReporter = Callable[[Any], None]

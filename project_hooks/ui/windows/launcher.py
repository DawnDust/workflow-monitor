"""Stable launcher for the project-local Windows executable runtime."""

from __future__ import annotations

import sys
from ...infrastructure.system.runtime import (
    ACTIVE_ENV,
    PORTABLE_ROOT_ENV,
    configure_utf8_stdio,
    is_frozen,
    run_selected_executable,
    runtime_root,
    selected_executable,
)


def main(argv: list[str] | None = None) -> int:
    """Source-tree development entrypoint; releases use windows_entry.main."""
    configure_utf8_stdio()
    from ..cli.main import main as cli_main

    return cli_main(list(sys.argv[1:] if argv is None else argv))

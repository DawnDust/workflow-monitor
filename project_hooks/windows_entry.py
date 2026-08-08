"""Stable PyInstaller entrypoint for the Windows executable."""

from project_hooks.ui.windows.main import *  # noqa: F401,F403 - compatibility facade


if __name__ == "__main__":
    raise SystemExit(main())

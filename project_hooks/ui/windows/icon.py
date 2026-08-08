"""Application icon helpers shared by source and frozen Windows builds."""

from __future__ import annotations

from pathlib import Path


ASSET_DIRECTORY = Path(__file__).resolve().parent / "assets"
PNG_ICON = ASSET_DIRECTORY / "crafting_table_icon.png"


def apply_window_icon(window, tk) -> bool:
    """Apply the bundled icon to a Tk window without blocking startup on failure."""
    try:
        image = tk.PhotoImage(file=str(PNG_ICON))
        window.iconphoto(True, image)
    except Exception:
        return False
    # Tk only retains the image while a Python reference remains alive.
    window._project_hooks_icon = image
    return True

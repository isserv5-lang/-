"""Configuration and local data vault paths for screenwriter-kb."""

from __future__ import annotations

import os
from pathlib import Path

DEFAULT_DATA_DIR = Path("D:/ScreenwriterKB")
SUBDIRS = ("sources", "cards", "exports", "index")


def ensure_data_subdirs(base_dir: Path) -> Path:
    """Create the base data directory and required subdirectories if missing."""
    base_dir.mkdir(parents=True, exist_ok=True)
    for name in SUBDIRS:
        (base_dir / name).mkdir(parents=True, exist_ok=True)
    return base_dir


def get_data_dir() -> Path:
    """Return the local data directory, honoring SWKB_DATA_DIR env var."""
    env_val = os.environ.get("SWKB_DATA_DIR", "").strip()
    base = Path(env_val).expanduser() if env_val else DEFAULT_DATA_DIR
    return ensure_data_subdirs(base)


def get_db_path() -> Path:
    """Return the SQLite database file path under the local data directory."""
    return get_data_dir() / "index" / "kb.sqlite"


def resolve_data_subdir(subdir: str, db_path: Path | None = None) -> Path:
    """Resolve a data subdirectory ('sources', 'cards', 'exports') aligned with db_path.

    When db_path is None, uses get_data_dir(). When a custom db_path is provided
    (e.g. in tests), places subdirs alongside the custom DB root rather than
    polluting the global data vault.
    """
    if db_path is None:
        return get_data_dir() / subdir
    db_path = Path(db_path)
    root = db_path.parent.parent if db_path.parent.name == "index" else db_path.parent
    target = root / subdir
    target.mkdir(parents=True, exist_ok=True)
    return target

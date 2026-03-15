"""
config_controller.py — Persistent application configuration.

Stores settings (like the database file path) in a JSON file so they
survive application restarts.  The config file lives alongside the
default database at `reqman/data/config.json`.
"""

import json
from pathlib import Path
from typing import Optional

# Config file sits next to the default database.
_CONFIG_DIR = Path(__file__).resolve().parent.parent / "data"
_CONFIG_PATH = _CONFIG_DIR / "config.json"


def _read_config() -> dict:
    """Load the config file, returning an empty dict if it doesn't exist."""
    if _CONFIG_PATH.exists():
        try:
            return json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _write_config(data: dict) -> None:
    """Persist the config dict to disk."""
    _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    _CONFIG_PATH.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def get_custom_db_path() -> Optional[str]:
    """Return the user-configured database path, or None for default."""
    return _read_config().get("db_path")


def set_custom_db_path(path: str) -> None:
    """Save a custom database path to the config file."""
    cfg = _read_config()
    cfg["db_path"] = path
    _write_config(cfg)


def clear_custom_db_path() -> None:
    """Remove the custom database path (revert to default)."""
    cfg = _read_config()
    cfg.pop("db_path", None)
    _write_config(cfg)

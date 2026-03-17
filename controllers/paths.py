"""
paths.py — Centralised path resolution for the application.

Writable data (database, config, user media) is stored under a
platform-appropriate user data directory so the application works
correctly even when installed to a read-only location like
C:\\Program Files.

    Windows:  %LOCALAPPDATA%/ReqMan
    macOS:    ~/Library/Application Support/ReqMan
    Linux:    ~/.local/share/ReqMan

Read-only assets (AI model) remain relative to the installation directory.
"""

import sys
from pathlib import Path

# ── Installation directory (read-only assets like the AI model) ───
# When frozen by Nuitka, __file__ may point inside the dist folder.
# Two levels up from controllers/paths.py → reqman root.
INSTALL_DIR = Path(__file__).resolve().parent.parent

# ── Writable user data directory ──────────────────────────────────
if sys.platform == "win32":
    _base = Path.home() / "AppData" / "Local"
elif sys.platform == "darwin":
    _base = Path.home() / "Library" / "Application Support"
else:
    _base = Path.home() / ".local" / "share"

APP_DATA_DIR = _base / "ReqMan"

# Ensure it exists on import.
APP_DATA_DIR.mkdir(parents=True, exist_ok=True)

# ── Concrete paths ────────────────────────────────────────────────
DATA_DIR = APP_DATA_DIR / "data"
DEFAULT_DB_PATH = DATA_DIR / "reqman.db"
CONFIG_DIR = DATA_DIR
MEDIA_DIR = APP_DATA_DIR / "project_media"

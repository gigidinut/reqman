#!/usr/bin/env python3
"""
main.py — Application entry point for the Requirements Manager.

Startup sequence
────────────────
1.  Create the QApplication and apply qdarktheme for a modern dark UI.
2.  Initialise the SQLAlchemy engine and ensure all tables exist.
3.  Seed a default admin account if the users table is empty (first run).
4.  Show the AuthWindow (login / create account / reset password flow).
5.  On successful authentication, print a confirmation to the console
    and close the auth window.  (The main project screen will be wired
    here in a future step.)

Run from the project root:
    python -m reqman.main

Or directly:
    python reqman/main.py

Dependencies:
    pip install PySide6 pyqtdarktheme sqlalchemy werkzeug
"""

import sys
from pathlib import Path

# ── Ensure the project root is importable when run as a script ───
_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from PySide6.QtWidgets import QApplication, QMessageBox

# qdarktheme is optional — the app degrades gracefully if it's missing.
try:
    import qdarktheme
    HAS_DARK_THEME = True
except ImportError:
    HAS_DARK_THEME = False

from database.models import Base, get_engine
from controllers.db_controllers import (
    init_engine,
    create_user,
    list_users,
    update_user,
)
from views.auth_view import AuthWindow
from views.main_view import MainScreen
from views.project_view import ProjectScreen
from controllers.config_controller import get_custom_db_path
from controllers.paths import DEFAULT_DB_PATH as _DEFAULT_DB_PATH


# ═══════════════════════════════════════════════════════════════════
# DATABASE BOOTSTRAP
# ═══════════════════════════════════════════════════════════════════


def _resolve_db_path() -> Path:
    """Return the configured database path, falling back to the default."""
    custom = get_custom_db_path()
    if custom:
        p = Path(custom)
        if p.exists():
            return p
        # Custom path no longer valid — fall back to default.
        print(f"[startup] Custom DB path not found: {custom} — using default.")
    return _DEFAULT_DB_PATH


DB_PATH = _resolve_db_path()

# Default admin credentials for first-run seeding.  The temporary flag
# forces a password change on first login.
DEFAULT_ADMIN_USERNAME = "admin"
DEFAULT_ADMIN_DISPLAY = "Administrator"
DEFAULT_ADMIN_PASSWORD = "passwordtemp"


def bootstrap_database() -> None:
    """
    Initialise the database engine, create tables if needed, and seed
    a default admin user on first run.

    The database file lives in `reqman/data/reqman.db`.  The `data/`
    directory is created automatically if it doesn't exist.

    Schema verification
    ───────────────────
    After create_all(), we verify that every table defined in the ORM
    actually exists in the database file.  If any are missing (e.g. the
    database was created by an older version of the code and create_all
    couldn't reconcile the schema), we drop everything and rebuild from
    scratch.  This is safe during development — a production app would
    use Alembic migrations instead.
    """
    from sqlalchemy import inspect as sa_inspect

    # Ensure the data directory exists.
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    # Initialise the module-level engine used by all controllers.
    engine = init_engine(db_path=str(DB_PATH), echo=False)

    # Create any missing tables (safe to call repeatedly — existing
    # tables are left untouched).
    Base.metadata.create_all(engine)

    # ── Verify all expected tables exist ─────────────────────────
    # create_all with checkfirst=True (the default) silently skips
    # tables that already exist, even if their columns are outdated.
    # If we detect missing tables, the DB file is from an older schema
    # and we must rebuild it entirely.
    expected_tables = set(Base.metadata.tables.keys())
    actual_tables = set(sa_inspect(engine).get_table_names())
    missing = expected_tables - actual_tables

    if missing:
        print(
            f"[startup] ⚠  Stale database detected — missing tables: {missing}\n"
            f"          Rebuilding database from scratch..."
        )
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)
        print("[startup] Database rebuilt successfully.")

    # ── Lightweight column migrations ──────────────────────────
    # create_all() won't add new columns to existing tables, so we
    # use ALTER TABLE to add any missing columns for schema evolution.
    from sqlalchemy import text as sa_text
    inspector = sa_inspect(engine)
    entity_columns = {c["name"] for c in inspector.get_columns("entities")}
    if "master_test_template_path" not in entity_columns:
        with engine.connect() as conn:
            conn.execute(sa_text(
                "ALTER TABLE entities ADD COLUMN master_test_template_path VARCHAR(500)"
            ))
            conn.commit()
        print("[startup] Migrated: added master_test_template_path column.")

    if "generated_test_file_path" not in entity_columns:
        with engine.connect() as conn:
            conn.execute(sa_text(
                "ALTER TABLE entities ADD COLUMN generated_test_file_path VARCHAR(500)"
            ))
            conn.commit()
        print("[startup] Migrated: added generated_test_file_path column.")

    if "sort_order" not in entity_columns:
        with engine.connect() as conn:
            conn.execute(sa_text(
                "ALTER TABLE entities ADD COLUMN sort_order INTEGER NOT NULL DEFAULT 0"
            ))
            conn.commit()
        print("[startup] Migrated: added sort_order column.")

    # ── User table migrations ─────────────────────────────────
    user_columns = {c["name"] for c in inspector.get_columns("users")}

    if "is_admin" not in user_columns:
        with engine.connect() as conn:
            conn.execute(sa_text(
                "ALTER TABLE users ADD COLUMN is_admin BOOLEAN NOT NULL DEFAULT 0"
            ))
            # Set the original 'admin' user as admin if it exists.
            conn.execute(sa_text(
                "UPDATE users SET is_admin = 1 WHERE username = 'admin'"
            ))
            conn.commit()
        print("[startup] Migrated: added is_admin column.")

    if "email_verified" not in user_columns:
        with engine.connect() as conn:
            conn.execute(sa_text(
                "ALTER TABLE users ADD COLUMN email_verified BOOLEAN NOT NULL DEFAULT 0"
            ))
            conn.commit()
        print("[startup] Migrated: added email_verified column.")

    # ── SecurityCode table ────────────────────────────────────
    if "security_codes" not in actual_tables:
        Base.metadata.tables["security_codes"].create(engine, checkfirst=True)
        print("[startup] Created security_codes table.")

    # ── RecoveryKey table ─────────────────────────────────────
    if "recovery_keys" not in actual_tables:
        Base.metadata.tables["recovery_keys"].create(engine, checkfirst=True)
        print("[startup] Created recovery_keys table.")

    print(f"[startup] Database ready at {DB_PATH}")

    # ── Seed the default admin on first run ──────────────────────
    existing_users = list_users(active_only=False)
    if len(existing_users) == 0:
        admin = create_user(
            username=DEFAULT_ADMIN_USERNAME,
            display_name=DEFAULT_ADMIN_DISPLAY,
            email="admin@reqman.local",
            password=DEFAULT_ADMIN_PASSWORD,
            temporary_password=True,  # force password change on first login
        )
        # Flag this user as admin.
        update_user(
            user_id=admin.id,
            acting_user_id=admin.id,
            updates={"is_admin": True},
        )
        print(
            f"[startup] Default admin account created:\n"
            f"          username: {DEFAULT_ADMIN_USERNAME}\n"
            f"          password: {DEFAULT_ADMIN_PASSWORD}  (temporary)\n"
        )
    else:
        print(f"[startup] {len(existing_users)} user(s) found — skipping seed.")


# ═══════════════════════════════════════════════════════════════════
# SCREEN NAVIGATION MANAGER
# ═══════════════════════════════════════════════════════════════════

# Module-level references to live windows so they are not garbage-collected.
_main_screen = None
_project_screen = None
_auth_window = None


def on_login_successful(user, auth_window: AuthWindow) -> None:
    """
    Called when the user completes the full authentication flow.

    Hides the auth window, creates the MainScreen, and wires the
    project_opened signal to transition into the ProjectScreen.
    """
    global _main_screen, _auth_window
    _auth_window = auth_window

    print(
        f"\n[auth] Login successful!\n"
        f"       User:  {user.display_name} ({user.username})\n"
        f"       ID:    {user.id}\n"
    )

    auth_window.hide()

    # Create the main menu screen.
    _main_screen = MainScreen(user=user)

    # When a project is opened/created, transition to the project workspace.
    _main_screen.project_opened.connect(
        lambda project: _open_project(project, user)
    )

    # When the user logs out, return to the auth window.
    _main_screen.logout_requested.connect(_logout)

    _main_screen.show()


def _open_project(project, user) -> None:
    """
    Transition from the MainScreen to the ProjectScreen.

    Hides the main menu and shows the project workspace.  Wires
    the go_back signal to return to the main menu.
    """
    global _project_screen

    print(f"[nav] Opening project: {project.name} (id={project.id})")

    _main_screen.hide()

    _project_screen = ProjectScreen(project=project, user=user)

    # When the user clicks "Back to Menu", return to the main screen.
    _project_screen.go_back.connect(
        lambda: _return_to_menu()
    )

    _project_screen.show()


def _return_to_menu() -> None:
    """
    Transition from the ProjectScreen back to the MainScreen.

    Closes the project workspace and re-shows the main menu.
    """
    global _project_screen

    print("[nav] Returning to main menu.")

    if _project_screen is not None:
        _project_screen.close()
        _project_screen = None

    if _main_screen is not None:
        _main_screen.show()


def _logout() -> None:
    """
    Close the main screen (and any open project screen) and
    re-show the authentication window for a fresh login.
    """
    global _main_screen, _project_screen

    print("[nav] Logging out — returning to login screen.")

    if _project_screen is not None:
        _project_screen.close()
        _project_screen = None

    if _main_screen is not None:
        _main_screen.close()
        _main_screen = None

    if _auth_window is not None:
        _auth_window.show()


# ═══════════════════════════════════════════════════════════════════
# APPLICATION ENTRY POINT
# ═══════════════════════════════════════════════════════════════════

def main() -> None:
    """Create the QApplication, apply theming, bootstrap the DB,
    and launch the authentication window."""

    # ── Create the Qt application ────────────────────────────────
    app = QApplication(sys.argv)
    app.setApplicationName("Requirements Manager")

    # ── Apply qdarktheme if available ────────────────────────────
    # The library has two API generations:
    #   v2.x+ exposes  qdarktheme.setup_theme()   — sets palette + stylesheet
    #   v1.x  only has qdarktheme.load_stylesheet() — returns a CSS string
    # We try v2 first, then fall back to v1, so it works with any version.
    if HAS_DARK_THEME:
        _extra_qss = "QWidget { font-size: 14px; }"
        try:
            # v2.x API — applies palette, stylesheet, and icon overrides.
            qdarktheme.setup_theme(
                theme="dark",
                additional_qss=_extra_qss,
            )
            print("[startup] qdarktheme applied via setup_theme (v2.x).")
        except AttributeError:
            # v1.x API — returns a stylesheet string; we apply it manually.
            stylesheet = qdarktheme.load_stylesheet("dark")
            app.setStyleSheet(stylesheet + "\n" + _extra_qss)
            print("[startup] qdarktheme applied via load_stylesheet (v1.x).")
    else:
        print(
            "[startup] qdarktheme not installed — using default system theme.\n"
            "          Install it with:  pip install pyqtdarktheme"
        )

    # ── Bootstrap the database ───────────────────────────────────
    try:
        bootstrap_database()
    except Exception as exc:
        QMessageBox.critical(
            None,
            "Database Error",
            f"Failed to initialise the database:\n\n{exc}\n\n"
            "The application will now exit.",
        )
        sys.exit(1)

    # ── Launch the authentication window ─────────────────────────
    auth_window = AuthWindow()

    # Connect the final login-success signal to our handler.
    # We use a lambda to pass the auth_window reference alongside the user.
    auth_window.login_successful.connect(
        lambda user: on_login_successful(user, auth_window)
    )

    auth_window.show()

    # ── Enter the Qt event loop ──────────────────────────────────
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

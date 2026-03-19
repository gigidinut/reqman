"""
main_view.py — Post-login main menu for the Requirements Manager.

Shown immediately after a successful authentication.  This is the
application "hub" from which the user can:

  • Create a new project  → prompts for name + description, writes to DB.
  • Open an existing project → shows a list dialog populated from the DB.
  • Edit their account details → dialog to change display name.
  • Toggle light / dark theme → switches qdarktheme dynamically at runtime.

Architecture
────────────
MainScreen (QMainWindow)
  ├─ _TopBar (QWidget)              — account button + theme toggle
  ├─ _CenterPanel (QWidget)         — hero buttons
  ├─ AccountDialog (QDialog)        — edit display name, view username
  ├─ OpenProjectDialog (QDialog)    — list of projects from the DB
  └─ CreateProjectDialog (QDialog)  — name + description input form

State management
────────────────
The authenticated User object is passed into MainScreen.__init__ so that
every controller call can include the correct `user_id` for audit logging.
When the user edits their profile in AccountDialog, the local `self._user`
reference is refreshed from the DB to stay in sync.

Theme toggle
────────────
The toggle calls qdarktheme with version-proof try/except logic identical
to main.py.  The current theme name ("dark" or "light") is tracked in
`self._current_theme` so each click flips it.
"""

from typing import Optional, List

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QKeyEvent, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSpacerItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

# qdarktheme may or may not be installed — mirror the main.py pattern.
try:
    import qdarktheme
    HAS_DARK_THEME = True
except ImportError:
    HAS_DARK_THEME = False

from controllers.db_controllers import (
    admin_reset_user_password,
    count_unused_recovery_keys,
    create_entity,
    generate_recovery_keys,
    get_accessible_projects,
    get_all_projects,
    get_project_access,
    get_user,
    get_user_by_username,
    grant_project_access,
    init_engine,
    is_admin,
    revoke_project_access,
    search_users,
    update_password,
    update_user,
    user_can_access_project,
)
from controllers.config_controller import get_custom_db_path, set_custom_db_path
from controllers.email_controller import (
    get_smtp_config,
    is_smtp_configured,
    save_smtp_config,
    test_smtp_connection,
)


# ═══════════════════════════════════════════════════════════════════
# STYLE CONSTANTS  (layer on top of qdarktheme without conflicting)
# ═══════════════════════════════════════════════════════════════════

# "Hero" button style — the two large centre buttons.
HERO_BTN_STYLE = """
    QPushButton {{
        background-color: {bg};
        color: white;
        border: none;
        border-radius: 12px;
        padding: 32px 24px;
        font-size: 18px;
        font-weight: 600;
        min-width: 220px;
    }}
    QPushButton:hover {{
        background-color: {hover};
    }}
    QPushButton:pressed {{
        background-color: {pressed};
    }}
"""

# Accent colour variants for the two hero buttons.
HERO_CREATE = HERO_BTN_STYLE.format(bg="#2ecc71", hover="#27ae60", pressed="#1e8449")
HERO_OPEN   = HERO_BTN_STYLE.format(bg="#3498db", hover="#2980b9", pressed="#1f6fa5")

# Small toolbar-style button used in the top bar.
TOPBAR_BTN_STYLE = """
    QPushButton {
        background: transparent;
        border: 1px solid #555;
        border-radius: 6px;
        padding: 6px 14px;
        font-size: 13px;
    }
    QPushButton:hover {
        background-color: rgba(255, 255, 255, 0.08);
    }
"""

# Primary button used inside dialogs.
DIALOG_BTN_STYLE = """
    QPushButton {
        background-color: #3498db;
        color: white;
        border: none;
        border-radius: 6px;
        padding: 8px 18px;
        font-size: 14px;
        font-weight: 600;
    }
    QPushButton:hover { background-color: #2980b9; }
    QPushButton:pressed { background-color: #1f6fa5; }
"""

# Standard input field styling for dialogs.
INPUT_STYLE = "padding: 8px; font-size: 14px;"

# Feedback label colours (same as auth_view for consistency).
ERROR_STYLE  = "color: #e74c3c; font-size: 13px; padding: 2px 0;"
SUCCESS_STYLE = "color: #2ecc71; font-size: 13px; padding: 2px 0;"


# ═══════════════════════════════════════════════════════════════════
# HELPER: inline feedback (shared with auth_view pattern)
# ═══════════════════════════════════════════════════════════════════

def _make_feedback_label() -> QLabel:
    """Create a hidden label for inline error/success messages."""
    label = QLabel("")
    label.setAlignment(Qt.AlignCenter)
    label.setWordWrap(True)
    label.setVisible(False)
    return label


def _show_error(label: QLabel, message: str):
    """Show a red error message in the given label."""
    label.setStyleSheet(ERROR_STYLE)
    label.setText(message)
    label.setVisible(True)


def _show_success(label: QLabel, message: str):
    """Show a green success message in the given label."""
    label.setStyleSheet(SUCCESS_STYLE)
    label.setText(message)
    label.setVisible(True)


def _clear_feedback(label: QLabel):
    """Hide the feedback label."""
    label.setText("")
    label.setVisible(False)


# ═══════════════════════════════════════════════════════════════════
# DIALOG: Account / User Info
# ═══════════════════════════════════════════════════════════════════

class AccountDialog(QDialog):
    """
    Modal dialog for viewing and editing the current user's full profile.

    Sections
    ────────
    1. Username (read-only — shown for reference).
    2. Display Name (editable).
    3. Email (editable).
    4. Password change section — new password + confirm.  Both fields
       are optional: if left blank, the password is not changed.  If
       only one is filled, a validation error is shown.

    On save, profile fields (display_name, email) are persisted via
    `update_user()`, and password (if provided) via `update_password()`.
    Both calls write audit log entries automatically.
    """

    def __init__(self, user, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("Account Details")
        self.setFixedSize(440, 580)
        # Store a mutable reference — the caller passes the latest
        # User object; we update it if the save succeeds.
        self._user = user
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(24, 24, 24, 24)

        # ── Heading ──────────────────────────────────────────────
        heading = QLabel("Account Details")
        heading.setAlignment(Qt.AlignCenter)
        font = QFont()
        font.setPointSize(16)
        font.setBold(True)
        heading.setFont(font)
        layout.addWidget(heading)

        # ── Username (read-only) ─────────────────────────────────
        layout.addWidget(QLabel("Username"))
        self.username_label = QLineEdit(self._user.username)
        self.username_label.setReadOnly(True)
        self.username_label.setStyleSheet(
            INPUT_STYLE + " background-color: rgba(128,128,128,0.15);"
        )
        layout.addWidget(self.username_label)

        # ── Display Name (editable) ─────────────────────────────
        layout.addWidget(QLabel("Display Name"))
        self.display_name_input = QLineEdit(self._user.display_name)
        self.display_name_input.setStyleSheet(INPUT_STYLE)
        layout.addWidget(self.display_name_input)

        # ── Email (editable) ─────────────────────────────────────
        layout.addWidget(QLabel("Email"))
        self.email_input = QLineEdit(self._user.email if self._user.email else "")
        self.email_input.setPlaceholderText("user@example.com")
        self.email_input.setStyleSheet(INPUT_STYLE)
        layout.addWidget(self.email_input)

        # ── Password change section ──────────────────────────────
        # A subtle separator label to visually group the password fields.
        pw_heading = QLabel("Change Password")
        pw_heading.setStyleSheet("font-weight: bold; padding-top: 8px; font-size: 13px;")
        layout.addWidget(pw_heading)

        pw_hint = QLabel("Leave both fields blank to keep your current password.")
        pw_hint.setStyleSheet("color: #999; font-size: 12px; padding-bottom: 2px;")
        layout.addWidget(pw_hint)

        self.new_pw_input = QLineEdit()
        self.new_pw_input.setPlaceholderText("New Password")
        self.new_pw_input.setEchoMode(QLineEdit.Password)
        self.new_pw_input.setStyleSheet(INPUT_STYLE)
        layout.addWidget(self.new_pw_input)

        self.confirm_pw_input = QLineEdit()
        self.confirm_pw_input.setPlaceholderText("Confirm New Password")
        self.confirm_pw_input.setEchoMode(QLineEdit.Password)
        self.confirm_pw_input.setStyleSheet(INPUT_STYLE)
        layout.addWidget(self.confirm_pw_input)

        # ── Recovery Keys section ──────────────────────────────────
        rk_heading = QLabel("Recovery Keys")
        rk_heading.setStyleSheet("font-weight: bold; padding-top: 8px; font-size: 13px;")
        layout.addWidget(rk_heading)

        unused = count_unused_recovery_keys(self._user.id)
        self.rk_status = QLabel(f"You have {unused} unused recovery key(s) remaining.")
        self.rk_status.setStyleSheet("color: #999; font-size: 12px; padding-bottom: 2px;")
        layout.addWidget(self.rk_status)

        self.regen_keys_btn = QPushButton("Regenerate Recovery Keys")
        self.regen_keys_btn.setStyleSheet(DIALOG_BTN_STYLE)
        self.regen_keys_btn.setCursor(Qt.PointingHandCursor)
        self.regen_keys_btn.setToolTip(
            "Generate a fresh set of 5 recovery keys (replaces any unused keys)"
        )
        self.regen_keys_btn.clicked.connect(self._on_regenerate_keys)
        layout.addWidget(self.regen_keys_btn)

        # ── Feedback label ───────────────────────────────────────
        self.feedback = _make_feedback_label()
        layout.addWidget(self.feedback)

        # ── Buttons row ──────────────────────────────────────────
        btn_row = QHBoxLayout()
        self.save_btn = QPushButton("Save Changes")
        self.save_btn.setStyleSheet(DIALOG_BTN_STYLE)
        self.save_btn.setCursor(Qt.PointingHandCursor)
        self.save_btn.clicked.connect(self._on_save)

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self.reject)

        btn_row.addStretch()
        btn_row.addWidget(self.cancel_btn)
        btn_row.addWidget(self.save_btn)
        layout.addLayout(btn_row)

    def _on_save(self):
        """Validate all fields and persist profile + password changes.

        Profile fields (display_name, email) are saved via update_user().
        Password is saved via update_password() only if the user filled
        in the password fields.  Both operations are independent — if
        the profile save succeeds but the password fails, the profile
        changes are kept and the error is shown for the password part.
        """
        _clear_feedback(self.feedback)

        new_name = self.display_name_input.text().strip()
        new_email = self.email_input.text().strip()
        new_pw = self.new_pw_input.text()
        confirm_pw = self.confirm_pw_input.text()

        # ── Validate required profile fields ─────────────────────
        if not new_name:
            _show_error(self.feedback, "Display name cannot be empty.")
            self.display_name_input.setFocus()
            return

        if not new_email:
            _show_error(self.feedback, "Email address is required.")
            self.email_input.setFocus()
            return

        # Basic email format check.
        if "@" not in new_email or "." not in new_email.split("@")[-1]:
            _show_error(self.feedback, "Please enter a valid email address.")
            self.email_input.setFocus()
            return

        # ── Validate password fields (only if user is attempting a change) ─
        wants_pw_change = bool(new_pw or confirm_pw)
        if wants_pw_change:
            if not new_pw:
                _show_error(self.feedback, "Please enter the new password.")
                self.new_pw_input.setFocus()
                return
            if len(new_pw) < 6:
                _show_error(self.feedback, "Password must be at least 6 characters.")
                self.new_pw_input.setFocus()
                return
            if new_pw != confirm_pw:
                _show_error(self.feedback, "Passwords do not match.")
                self.confirm_pw_input.clear()
                self.confirm_pw_input.setFocus()
                return

        # ── Save profile fields (display_name, email) ────────────
        # Build the updates dict — only include fields that actually changed
        # so the audit log accurately reflects what was modified.
        profile_updates = {}
        if new_name != self._user.display_name:
            profile_updates["display_name"] = new_name
        if new_email != (self._user.email or ""):
            profile_updates["email"] = new_email

        if profile_updates:
            try:
                updated = update_user(
                    user_id=self._user.id,
                    acting_user_id=self._user.id,
                    updates=profile_updates,
                )
            except Exception as exc:
                _show_error(self.feedback, f"Profile save failed: {exc}")
                return

            if updated:
                self._user = updated
            else:
                _show_error(self.feedback, "User not found — save failed.")
                return

        # ── Save password (if requested) ─────────────────────────
        if wants_pw_change:
            try:
                pw_ok = update_password(
                    user_id=self._user.id,
                    new_password=new_pw,
                    clear_temporary_flag=True,
                    acting_user_id=self._user.id,
                )
            except Exception as exc:
                _show_error(self.feedback, f"Password update failed: {exc}")
                return

            if not pw_ok:
                _show_error(self.feedback, "Password update failed — user not found.")
                return

        # ── All saves succeeded — close the dialog ───────────────
        self.accept()

    def _on_regenerate_keys(self):
        """Generate a fresh set of recovery keys and display them."""
        confirm = QMessageBox.question(
            self,
            "Regenerate Recovery Keys",
            "This will invalidate all your current unused recovery keys\n"
            "and generate 5 new ones.\n\nContinue?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return

        try:
            keys = generate_recovery_keys(user_id=self._user.id, count=5)
        except Exception as exc:
            _show_error(self.feedback, f"Failed to generate keys: {exc}")
            return

        # Show the keys using the shared dialog from auth_view.
        from views.auth_view import RecoveryKeysDialog
        dlg = RecoveryKeysDialog(keys, parent=self)
        dlg.exec()

        # Update the status label.
        unused = count_unused_recovery_keys(self._user.id)
        self.rk_status.setText(f"You have {unused} unused recovery key(s) remaining.")

    def get_updated_user(self):
        """Return the (possibly updated) User object after the dialog closes."""
        return self._user


# ═══════════════════════════════════════════════════════════════════
# DIALOG: Open Project  (list selection)
# ═══════════════════════════════════════════════════════════════════

class OpenProjectDialog(QDialog):
    """
    Modal dialog that lists projects the current user has access to.

    Admin sees all projects.  Other users see only projects where
    they have a ProjectAccess row (manager or member).
    """

    def __init__(self, user, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("Open Project")
        self.setFixedSize(460, 420)
        self._user = user
        self._selected_project = None
        self._projects = []           # populated in _load_projects
        self._build_ui()
        self._load_projects()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(24, 24, 24, 24)

        # ── Heading ──────────────────────────────────────────────
        heading = QLabel("Select a Project")
        heading.setAlignment(Qt.AlignCenter)
        font = QFont()
        font.setPointSize(16)
        font.setBold(True)
        heading.setFont(font)
        layout.addWidget(heading)

        # ── Project list ─────────────────────────────────────────
        self.project_list = QListWidget()
        self.project_list.setStyleSheet("font-size: 14px; padding: 4px;")
        # Double-click to open immediately.
        self.project_list.itemDoubleClicked.connect(self._on_open)
        layout.addWidget(self.project_list)

        # ── Empty-state message (hidden by default) ──────────────
        self.empty_label = QLabel("No projects found.\nCreate one from the main menu.")
        self.empty_label.setAlignment(Qt.AlignCenter)
        self.empty_label.setStyleSheet("color: #999; font-size: 14px; padding: 20px;")
        self.empty_label.setVisible(False)
        layout.addWidget(self.empty_label)

        # ── Feedback label ───────────────────────────────────────
        self.feedback = _make_feedback_label()
        layout.addWidget(self.feedback)

        # ── Buttons row ──────────────────────────────────────────
        btn_row = QHBoxLayout()
        self.open_btn = QPushButton("Open")
        self.open_btn.setStyleSheet(DIALOG_BTN_STYLE)
        self.open_btn.setCursor(Qt.PointingHandCursor)
        self.open_btn.clicked.connect(self._on_open)

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self.reject)

        btn_row.addStretch()
        btn_row.addWidget(self.cancel_btn)
        btn_row.addWidget(self.open_btn)
        layout.addLayout(btn_row)

    def _load_projects(self):
        """Fetch projects the user has access to and populate the list."""
        try:
            self._projects = get_accessible_projects(self._user)
        except Exception as exc:
            _show_error(self.feedback, f"Error loading projects: {exc}")
            self._projects = []

        self.project_list.clear()

        if not self._projects:
            # No projects — show the empty-state message and disable Open.
            self.project_list.setVisible(False)
            self.empty_label.setVisible(True)
            self.open_btn.setEnabled(False)
            return

        self.project_list.setVisible(True)
        self.empty_label.setVisible(False)
        self.open_btn.setEnabled(True)

        for proj in self._projects:
            # Format: "Name   (status)   —   description snippet"
            desc_snippet = ""
            if proj.description:
                # Truncate long descriptions for the list view.
                desc_snippet = f"  —  {proj.description[:60]}"
                if len(proj.description) > 60:
                    desc_snippet += "..."

            display_text = f"{proj.name}   ({proj.status}){desc_snippet}"
            item = QListWidgetItem(display_text)
            # Store the project object in the item's data role so we
            # can retrieve it on selection without index math.
            item.setData(Qt.UserRole, proj)
            self.project_list.addItem(item)

        # Pre-select the first item.
        self.project_list.setCurrentRow(0)

    def _on_open(self):
        """Validate selection and accept the dialog."""
        _clear_feedback(self.feedback)
        current_item = self.project_list.currentItem()

        if current_item is None:
            _show_error(self.feedback, "Please select a project.")
            return

        self._selected_project = current_item.data(Qt.UserRole)
        self.accept()

    def get_selected_project(self):
        """Return the Project entity chosen by the user, or None."""
        return self._selected_project


# ═══════════════════════════════════════════════════════════════════
# DIALOG: Create New Project
# ═══════════════════════════════════════════════════════════════════

class CreateProjectDialog(QDialog):
    """
    Modal dialog for creating a new project.

    Fields: project name (required) and description (optional).
    Calls `create_entity(entity_type="project", ...)` on the controller
    and stores the newly created project for retrieval by the caller.
    """

    def __init__(self, user_id: int, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("Create New Project")
        self.setFixedSize(460, 360)
        self._user_id = user_id
        self._created_project = None
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(24, 24, 24, 24)

        # ── Heading ──────────────────────────────────────────────
        heading = QLabel("Create New Project")
        heading.setAlignment(Qt.AlignCenter)
        font = QFont()
        font.setPointSize(16)
        font.setBold(True)
        heading.setFont(font)
        layout.addWidget(heading)

        # ── Project Name ─────────────────────────────────────────
        layout.addWidget(QLabel("Project Name"))
        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("e.g. Satellite Comms Program")
        self.name_input.setStyleSheet(INPUT_STYLE)
        layout.addWidget(self.name_input)

        # ── Description (optional) ───────────────────────────────
        layout.addWidget(QLabel("Description (optional)"))
        self.desc_input = QTextEdit()
        self.desc_input.setPlaceholderText("Brief description of the project scope...")
        self.desc_input.setStyleSheet(INPUT_STYLE)
        self.desc_input.setMaximumHeight(90)
        layout.addWidget(self.desc_input)

        # ── Feedback label ───────────────────────────────────────
        self.feedback = _make_feedback_label()
        layout.addWidget(self.feedback)

        # ── Buttons row ──────────────────────────────────────────
        btn_row = QHBoxLayout()
        self.create_btn = QPushButton("Create Project")
        self.create_btn.setStyleSheet(DIALOG_BTN_STYLE)
        self.create_btn.setCursor(Qt.PointingHandCursor)
        self.create_btn.clicked.connect(self._on_create)

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self.reject)

        btn_row.addStretch()
        btn_row.addWidget(self.cancel_btn)
        btn_row.addWidget(self.create_btn)
        layout.addLayout(btn_row)

    def keyPressEvent(self, event: QKeyEvent):
        """Allow Enter to submit (but not inside the multiline QTextEdit)."""
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            # Only auto-submit if the name field is focused; the QTextEdit
            # needs Enter for newlines.
            if self.name_input.hasFocus():
                self._on_create()
                return
        super().keyPressEvent(event)

    def _on_create(self):
        """Validate inputs and create the project in the database."""
        _clear_feedback(self.feedback)

        name = self.name_input.text().strip()
        description = self.desc_input.toPlainText().strip() or None

        if not name:
            _show_error(self.feedback, "Project name is required.")
            self.name_input.setFocus()
            return

        try:
            project = create_entity(
                entity_type="project",
                name=name,
                user_id=self._user_id,
                description=description,
            )
        except Exception as exc:
            _show_error(self.feedback, f"Error creating project: {exc}")
            return

        self._created_project = project
        self.accept()

    def get_created_project(self):
        """Return the newly created Project entity, or None."""
        return self._created_project


# ═══════════════════════════════════════════════════════════════════
# DIALOG: Manage DB Managers  (admin-only)
# ═══════════════════════════════════════════════════════════════════

class ManageDBManagersDialog(QDialog):
    """
    Admin-only dialog to assign/revoke the 'Project Database Manager'
    role.  A DB Manager can manage access for the projects they are
    assigned to.

    Layout:
      • Left list: all non-admin users
      • Right list: all projects
      • Select a user + one or more projects → Assign / Revoke
    """

    def __init__(self, admin_user, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("Manage Project Database Managers")
        self.setFixedSize(680, 520)
        self._admin = admin_user
        self._users = []
        self._projects = []
        self._build_ui()
        self._load_data()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(20, 20, 20, 20)

        heading = QLabel("Assign Project Database Managers")
        heading.setAlignment(Qt.AlignCenter)
        f = QFont(); f.setPointSize(15); f.setBold(True)
        heading.setFont(f)
        layout.addWidget(heading)

        hint = QLabel(
            "Select a user and a project, then assign them as a manager.\n"
            "Managers can grant other users access to their assigned projects."
        )
        hint.setStyleSheet("color: #999; font-size: 12px;")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        # ── Two-column lists ──────────────────────────────────────
        lists_row = QHBoxLayout()

        # Users (with search)
        user_col = QVBoxLayout()
        user_col.addWidget(QLabel("Users"))
        self.user_search = QLineEdit()
        self.user_search.setPlaceholderText("Search by name, username, or email...")
        self.user_search.setStyleSheet(INPUT_STYLE)
        self.user_search.textChanged.connect(self._on_user_search_changed)
        user_col.addWidget(self.user_search)
        self.user_list = QListWidget()
        self.user_list.setStyleSheet("font-size: 13px;")
        self.user_list.currentRowChanged.connect(self._on_user_selected)
        user_col.addWidget(self.user_list)
        lists_row.addLayout(user_col)

        # Projects
        proj_col = QVBoxLayout()
        proj_col.addWidget(QLabel("Projects"))
        self.project_list = QListWidget()
        self.project_list.setStyleSheet("font-size: 13px;")
        self.project_list.currentRowChanged.connect(self._on_user_selected)
        proj_col.addWidget(self.project_list)
        lists_row.addLayout(proj_col)

        layout.addLayout(lists_row)

        # ── Current role label ─────────────────────────────────────
        self.role_label = QLabel("")
        self.role_label.setStyleSheet("font-size: 12px; color: #aaa; padding: 2px 0;")
        layout.addWidget(self.role_label)

        # ── Feedback ───────────────────────────────────────────────
        self.feedback = _make_feedback_label()
        layout.addWidget(self.feedback)

        # ── Buttons ────────────────────────────────────────────────
        btn_row = QHBoxLayout()

        self.assign_btn = QPushButton("Assign as Manager")
        self.assign_btn.setStyleSheet(DIALOG_BTN_STYLE)
        self.assign_btn.setCursor(Qt.PointingHandCursor)
        self.assign_btn.clicked.connect(self._on_assign)
        btn_row.addWidget(self.assign_btn)

        self.revoke_btn = QPushButton("Revoke Manager Role")
        self.revoke_btn.setStyleSheet(
            DIALOG_BTN_STYLE.replace("#3498db", "#e74c3c")
                            .replace("#2980b9", "#c0392b")
                            .replace("#1f6fa5", "#a93226")
        )
        self.revoke_btn.setCursor(Qt.PointingHandCursor)
        self.revoke_btn.clicked.connect(self._on_revoke)
        btn_row.addWidget(self.revoke_btn)

        btn_row.addStretch()

        self.close_btn = QPushButton("Close")
        self.close_btn.clicked.connect(self.accept)
        btn_row.addWidget(self.close_btn)

        layout.addLayout(btn_row)

    def _load_data(self):
        self._populate_user_list("")
        self._projects = get_all_projects()
        self.project_list.clear()
        for p in self._projects:
            item = QListWidgetItem(p.name)
            item.setData(Qt.UserRole, p)
            self.project_list.addItem(item)

    def _on_user_search_changed(self, text: str):
        self._populate_user_list(text)

    def _populate_user_list(self, query: str):
        self.user_list.clear()
        self._users = search_users(query, active_only=True, exclude_admin=True)
        for u in self._users:
            item = QListWidgetItem(
                f"{u.display_name}  ({u.username})  —  {u.email or ''}"
            )
            item.setData(Qt.UserRole, u)
            self.user_list.addItem(item)

    def _on_user_selected(self):
        """Update the role label when a user is selected."""
        _clear_feedback(self.feedback)
        user = self._get_selected_user()
        project = self._get_selected_project()
        if user and project:
            self._update_role_label(user, project)
        else:
            self.role_label.setText("")

    def _update_role_label(self, user, project):
        access = get_project_access(project.id)
        for entry in access:
            if entry["user_id"] == user.id:
                self.role_label.setText(
                    f"Current role: {entry['role'].upper()} on '{project.name}'"
                )
                return
        self.role_label.setText(f"No access to '{project.name}'")

    def _get_selected_user(self):
        item = self.user_list.currentItem()
        return item.data(Qt.UserRole) if item else None

    def _get_selected_project(self):
        item = self.project_list.currentItem()
        return item.data(Qt.UserRole) if item else None

    def _on_assign(self):
        _clear_feedback(self.feedback)
        user = self._get_selected_user()
        project = self._get_selected_project()
        if not user or not project:
            _show_error(self.feedback, "Select both a user and a project.")
            return
        try:
            grant_project_access(
                user_id=user.id,
                project_id=project.id,
                role="manager",
                granted_by_user_id=self._admin.id,
            )
            _show_success(
                self.feedback,
                f"{user.display_name} is now a manager of '{project.name}'.",
            )
            self._update_role_label(user, project)
        except Exception as exc:
            _show_error(self.feedback, f"Failed: {exc}")

    def _on_revoke(self):
        _clear_feedback(self.feedback)
        user = self._get_selected_user()
        project = self._get_selected_project()
        if not user or not project:
            _show_error(self.feedback, "Select both a user and a project.")
            return
        try:
            removed = revoke_project_access(
                user_id=user.id,
                project_id=project.id,
                revoked_by_user_id=self._admin.id,
            )
            if removed:
                _show_success(
                    self.feedback,
                    f"Removed {user.display_name}'s access to '{project.name}'.",
                )
            else:
                _show_error(self.feedback, "User does not have access to this project.")
            self._update_role_label(user, project)
        except Exception as exc:
            _show_error(self.feedback, f"Failed: {exc}")


# ═══════════════════════════════════════════════════════════════════
# DIALOG: Manage Project Access  (admin + project managers)
# ═══════════════════════════════════════════════════════════════════

class ManageProjectAccessDialog(QDialog):
    """
    Dialog for granting/revoking 'member' access to a specific project.

    Usable by:
      • Admin — for any project.
      • Project Database Manager — for their assigned projects only.
    """

    def __init__(self, project, acting_user, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle(f"Manage Access — {project.name}")
        self.setFixedSize(520, 480)
        self._project = project
        self._acting_user = acting_user
        self._users = []
        self._build_ui()
        self._load_data()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(20, 20, 20, 20)

        heading = QLabel(f"Access Control — {self._project.name}")
        heading.setAlignment(Qt.AlignCenter)
        f = QFont(); f.setPointSize(14); f.setBold(True)
        heading.setFont(f)
        layout.addWidget(heading)

        # ── Current access list ────────────────────────────────────
        layout.addWidget(QLabel("Users with access:"))
        self.access_list = QListWidget()
        self.access_list.setStyleSheet("font-size: 13px;")
        layout.addWidget(self.access_list)

        # ── Grant section ──────────────────────────────────────────
        grant_label = QLabel("Grant access to:")
        grant_label.setStyleSheet("font-weight: bold; padding-top: 6px;")
        layout.addWidget(grant_label)

        self.user_search = QLineEdit()
        self.user_search.setPlaceholderText("Search by name, username, or email...")
        self.user_search.setStyleSheet(INPUT_STYLE)
        self.user_search.textChanged.connect(self._on_user_search_changed)
        layout.addWidget(self.user_search)

        grant_row = QHBoxLayout()
        self.user_combo = QListWidget()
        self.user_combo.setMaximumHeight(120)
        self.user_combo.setStyleSheet("font-size: 13px;")
        grant_row.addWidget(self.user_combo)

        btn_col = QVBoxLayout()
        self.grant_btn = QPushButton("Grant Member Access")
        self.grant_btn.setStyleSheet(DIALOG_BTN_STYLE)
        self.grant_btn.setCursor(Qt.PointingHandCursor)
        self.grant_btn.clicked.connect(self._on_grant)
        btn_col.addWidget(self.grant_btn)

        self.revoke_btn = QPushButton("Revoke Selected")
        self.revoke_btn.setStyleSheet(
            DIALOG_BTN_STYLE.replace("#3498db", "#e74c3c")
                            .replace("#2980b9", "#c0392b")
                            .replace("#1f6fa5", "#a93226")
        )
        self.revoke_btn.setCursor(Qt.PointingHandCursor)
        self.revoke_btn.clicked.connect(self._on_revoke)
        btn_col.addWidget(self.revoke_btn)

        btn_col.addStretch()
        grant_row.addLayout(btn_col)
        layout.addLayout(grant_row)

        # ── Feedback ───────────────────────────────────────────────
        self.feedback = _make_feedback_label()
        layout.addWidget(self.feedback)

        # ── Close button ───────────────────────────────────────────
        close_row = QHBoxLayout()
        close_row.addStretch()
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        close_row.addWidget(close_btn)
        layout.addLayout(close_row)

    def _load_data(self):
        # Current access entries
        self._refresh_access_list()

        # All users (exclude admin and those already with access)
        self._refresh_user_combo()

    def _refresh_access_list(self):
        self.access_list.clear()
        access = get_project_access(self._project.id)
        for entry in access:
            text = (
                f"{entry['display_name']}  ({entry['username']})  "
                f"— {entry['role'].upper()}"
            )
            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, entry)
            self.access_list.addItem(item)

    def _on_user_search_changed(self, text: str):
        self._refresh_user_combo(text)

    def _refresh_user_combo(self, query: str = ""):
        self.user_combo.clear()
        access = get_project_access(self._project.id)
        existing_ids = {e["user_id"] for e in access}
        results = search_users(query, active_only=True, exclude_admin=True)
        self._users = [u for u in results if u.id not in existing_ids]
        for u in self._users:
            item = QListWidgetItem(
                f"{u.display_name}  ({u.username})  —  {u.email or ''}"
            )
            item.setData(Qt.UserRole, u)
            self.user_combo.addItem(item)

    def _on_grant(self):
        _clear_feedback(self.feedback)
        item = self.user_combo.currentItem()
        if not item:
            _show_error(self.feedback, "Select a user to grant access.")
            return
        user = item.data(Qt.UserRole)
        try:
            grant_project_access(
                user_id=user.id,
                project_id=self._project.id,
                role="member",
                granted_by_user_id=self._acting_user.id,
            )
            _show_success(
                self.feedback,
                f"Granted {user.display_name} access to this project.",
            )
            self._refresh_access_list()
            self._refresh_user_combo()
        except Exception as exc:
            _show_error(self.feedback, f"Failed: {exc}")

    def _on_revoke(self):
        _clear_feedback(self.feedback)
        item = self.access_list.currentItem()
        if not item:
            _show_error(self.feedback, "Select a user from the access list to revoke.")
            return
        entry = item.data(Qt.UserRole)
        # Prevent revoking a manager if the acting user is not admin
        if entry["role"] == "manager" and not is_admin(self._acting_user):
            _show_error(
                self.feedback,
                "Only the administrator can revoke a manager's access.",
            )
            return
        try:
            revoke_project_access(
                user_id=entry["user_id"],
                project_id=self._project.id,
                revoked_by_user_id=self._acting_user.id,
            )
            _show_success(
                self.feedback,
                f"Revoked {entry['display_name']}'s access.",
            )
            self._refresh_access_list()
            self._refresh_user_combo()
        except Exception as exc:
            _show_error(self.feedback, f"Failed: {exc}")


# ═══════════════════════════════════════════════════════════════════
# MAIN SCREEN  (post-login hub)
# ═══════════════════════════════════════════════════════════════════

class MainScreen(QMainWindow):
    """
    The primary application window shown after successful authentication.

    Layout
    ──────
    ┌─────────────────────────────────────────────────────────┐
    │  [☀ / ☾ toggle]                 Welcome, Name  [Account]│  ← top bar
    ├─────────────────────────────────────────────────────────┤
    │                                                         │
    │           [ Create New Project ]  [ Open Project ]      │  ← centre
    │                                                         │
    └─────────────────────────────────────────────────────────┘

    Signals
    ───────
    project_opened(project) : emitted when the user opens or creates a
                              project and is ready to enter the project
                              workspace.  (Connected in a future phase.)
    logout_requested        : emitted if a logout feature is added later.
    """

    # Emitted with the selected/created Project entity when the user
    # is ready to enter the project workspace.
    project_opened = Signal(object)

    # Emitted when the user requests to log out (future use).
    logout_requested = Signal()

    WINDOW_WIDTH  = 720
    WINDOW_HEIGHT = 480

    def __init__(self, user, parent: Optional[QWidget] = None):
        """
        Args:
            user:  The authenticated User ORM object, passed from the
                   auth flow so we always know whose session this is.
        """
        super().__init__(parent)
        self._user = user
        # Track the current theme name so the toggle can flip it.
        self._current_theme = "dark"

        self.setWindowTitle("Requirements Manager")
        self.setMinimumSize(self.WINDOW_WIDTH, self.WINDOW_HEIGHT)

        self._build_ui()

    # ─────────────────────────────────────────────────────────────
    # UI Construction
    # ─────────────────────────────────────────────────────────────

    def _build_ui(self):
        """Assemble the top bar + centre panel into a single central widget."""
        # The central widget holds everything in a vertical stack:
        # [top bar] → [stretch] → [hero buttons] → [stretch]
        central = QWidget()
        self.setCentralWidget(central)
        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # ── Top bar ──────────────────────────────────────────────
        root_layout.addWidget(self._build_top_bar())

        # ── Centre panel (hero buttons) ──────────────────────────
        root_layout.addStretch(1)
        root_layout.addLayout(self._build_centre_panel())
        root_layout.addStretch(1)

    def _build_top_bar(self) -> QWidget:
        """
        Construct the top bar containing:
          • Left:  theme toggle button (☀ / ☾)
          • Right: welcome text + account button
        """
        bar = QWidget()
        bar.setStyleSheet("padding: 8px 16px;")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(16, 10, 16, 10)

        # ── Theme toggle (left side) ─────────────────────────────
        self.theme_btn = QPushButton("☀  Light Mode")
        self.theme_btn.setStyleSheet(TOPBAR_BTN_STYLE)
        self.theme_btn.setCursor(Qt.PointingHandCursor)
        self.theme_btn.setToolTip("Switch between dark and light theme")
        self.theme_btn.clicked.connect(self._on_toggle_theme)
        layout.addWidget(self.theme_btn)

        # ── Admin-only buttons ─────────────────────────────────────
        if is_admin(self._user):
            self.relocate_db_btn = QPushButton("Relocate Database")
            self.relocate_db_btn.setStyleSheet(TOPBAR_BTN_STYLE)
            self.relocate_db_btn.setCursor(Qt.PointingHandCursor)
            self.relocate_db_btn.setToolTip("Move the database file to a new location")
            self.relocate_db_btn.clicked.connect(self._on_relocate_database)
            layout.addWidget(self.relocate_db_btn)

            self.manage_managers_btn = QPushButton("Manage DB Managers")
            self.manage_managers_btn.setStyleSheet(TOPBAR_BTN_STYLE)
            self.manage_managers_btn.setCursor(Qt.PointingHandCursor)
            self.manage_managers_btn.setToolTip(
                "Assign users as Project Database Managers"
            )
            self.manage_managers_btn.clicked.connect(self._on_manage_db_managers)
            layout.addWidget(self.manage_managers_btn)

            self.smtp_btn = QPushButton("Email Settings")
            self.smtp_btn.setStyleSheet(TOPBAR_BTN_STYLE)
            self.smtp_btn.setCursor(Qt.PointingHandCursor)
            self.smtp_btn.setToolTip("Configure SMTP settings for email verification and password reset")
            self.smtp_btn.clicked.connect(self._on_smtp_settings)
            layout.addWidget(self.smtp_btn)

            self.reset_user_pw_btn = QPushButton("Reset User Password")
            self.reset_user_pw_btn.setStyleSheet(TOPBAR_BTN_STYLE)
            self.reset_user_pw_btn.setCursor(Qt.PointingHandCursor)
            self.reset_user_pw_btn.setToolTip("Reset a user's password to a temporary value")
            self.reset_user_pw_btn.clicked.connect(self._on_reset_user_password)
            layout.addWidget(self.reset_user_pw_btn)

        # Push everything else to the right.
        layout.addStretch(1)

        # ── Welcome label ────────────────────────────────────────
        self.welcome_label = QLabel(f"Welcome, {self._user.display_name}")
        self.welcome_label.setStyleSheet("font-size: 14px; padding-right: 10px;")
        layout.addWidget(self.welcome_label)

        # ── Account button ───────────────────────────────────────
        self.account_btn = QPushButton("Account")
        self.account_btn.setStyleSheet(TOPBAR_BTN_STYLE)
        self.account_btn.setCursor(Qt.PointingHandCursor)
        self.account_btn.setToolTip("View and edit your account details")
        self.account_btn.clicked.connect(self._on_account_clicked)
        layout.addWidget(self.account_btn)

        # ── Logout button ─────────────────────────────────────────
        self.logout_btn = QPushButton("Logout")
        self.logout_btn.setStyleSheet(TOPBAR_BTN_STYLE)
        self.logout_btn.setCursor(Qt.PointingHandCursor)
        self.logout_btn.setToolTip("Log out and return to the login screen")
        self.logout_btn.clicked.connect(self._on_logout)
        layout.addWidget(self.logout_btn)

        return bar

    def _build_centre_panel(self) -> QHBoxLayout:
        """
        Construct the two hero buttons arranged side by side in the
        centre of the window with comfortable spacing.
        """
        row = QHBoxLayout()
        row.setSpacing(32)

        # Horizontal padding so buttons don't stretch edge-to-edge.
        row.addStretch(1)

        # ── "Create New Project" button ──────────────────────────
        self.create_btn = QPushButton("＋  Create New Project")
        self.create_btn.setStyleSheet(HERO_CREATE)
        self.create_btn.setCursor(Qt.PointingHandCursor)
        self.create_btn.setToolTip("Start a new project from scratch")
        self.create_btn.clicked.connect(self._on_create_project)
        row.addWidget(self.create_btn)

        # ── "Open Project" button ────────────────────────────────
        self.open_btn = QPushButton("📂  Open Project")
        self.open_btn.setStyleSheet(HERO_OPEN)
        self.open_btn.setCursor(Qt.PointingHandCursor)
        self.open_btn.setToolTip("Browse and open an existing project")
        self.open_btn.clicked.connect(self._on_open_project)
        row.addWidget(self.open_btn)

        row.addStretch(1)
        return row

    # ─────────────────────────────────────────────────────────────
    # Slots: Account
    # ─────────────────────────────────────────────────────────────

    def _on_account_clicked(self):
        """Open the Account Details dialog.  If the user edits their
        display name, refresh the welcome label immediately."""
        dialog = AccountDialog(self._user, parent=self)
        if dialog.exec() == QDialog.Accepted:
            # The dialog may have updated the user — pull the latest.
            self._user = dialog.get_updated_user()
            self.welcome_label.setText(f"Welcome, {self._user.display_name}")

    def _on_logout(self):
        """Log out and return to the login screen."""
        self.logout_requested.emit()

    # ─────────────────────────────────────────────────────────────
    # Slots: Theme Toggle
    # ─────────────────────────────────────────────────────────────

    def _on_toggle_theme(self):
        """Flip between dark and light theme at runtime.

        Uses the same version-proof try/except pattern as main.py:
        try setup_theme (v2.x), fall back to load_stylesheet (v1.x)."""

        if not HAS_DARK_THEME:
            QMessageBox.information(
                self,
                "Theme Unavailable",
                "qdarktheme is not installed.\n"
                "Install with:  pip install pyqtdarktheme",
            )
            return

        # Flip the theme.
        new_theme = "light" if self._current_theme == "dark" else "dark"
        extra_qss = "QWidget { font-size: 14px; }"

        try:
            # v2.x API — full palette + stylesheet + icon override.
            qdarktheme.setup_theme(theme=new_theme, additional_qss=extra_qss)
        except AttributeError:
            # v1.x API — stylesheet only, applied to the QApplication.
            app = QApplication.instance()
            if app:
                stylesheet = qdarktheme.load_stylesheet(new_theme)
                app.setStyleSheet(stylesheet + "\n" + extra_qss)

        # Update tracked state and button label.
        self._current_theme = new_theme
        if new_theme == "dark":
            self.theme_btn.setText("☀  Light Mode")
        else:
            self.theme_btn.setText("☾  Dark Mode")

    # ─────────────────────────────────────────────────────────────
    # Slots: Create / Open Project
    # ─────────────────────────────────────────────────────────────

    def _on_create_project(self):
        """Show the Create Project dialog.  On success, auto-grant the
        creator 'manager' access and emit `project_opened`."""
        dialog = CreateProjectDialog(user_id=self._user.id, parent=self)
        if dialog.exec() == QDialog.Accepted:
            project = dialog.get_created_project()
            if project:
                # Auto-grant the creator manager access (unless admin,
                # who has implicit access to everything).
                if not is_admin(self._user):
                    grant_project_access(
                        user_id=self._user.id,
                        project_id=project.id,
                        role="manager",
                        granted_by_user_id=self._user.id,
                    )
                self.project_opened.emit(project)

    def _on_open_project(self):
        """Show the Open Project dialog.  On selection, emit
        `project_opened` with the chosen Project entity."""
        dialog = OpenProjectDialog(user=self._user, parent=self)
        if dialog.exec() == QDialog.Accepted:
            project = dialog.get_selected_project()
            if project:
                self.project_opened.emit(project)

    # ─────────────────────────────────────────────────────────────
    # Slots: Relocate Database  (admin-only)
    # ─────────────────────────────────────────────────────────────

    def _on_relocate_database(self):
        """Let the administrator move the database file to a new directory.

        Flow:
        1. Show a directory picker.
        2. Copy the current DB file to the chosen directory.
        3. Update the config so the new path is used on next startup.
        4. Reinitialise the engine to point to the new file immediately.
        """
        import shutil
        from pathlib import Path
        from database.models import Base

        # Resolve current DB path (custom or default).
        from controllers.paths import DEFAULT_DB_PATH
        custom = get_custom_db_path()
        if custom and Path(custom).exists():
            current_db = Path(custom)
        else:
            current_db = DEFAULT_DB_PATH

        # Let the admin pick a destination directory.
        new_dir = QFileDialog.getExistingDirectory(
            self,
            "Select New Database Location",
            str(current_db.parent),
        )
        if not new_dir:
            return  # user cancelled

        new_path = Path(new_dir) / "reqman.db"

        # Guard: same location — nothing to do.
        if new_path.resolve() == current_db.resolve():
            QMessageBox.information(
                self,
                "No Change",
                "The selected directory already contains the current database.",
            )
            return

        # Confirm with the administrator.
        reply = QMessageBox.question(
            self,
            "Confirm Database Relocation",
            f"The database will be copied to:\n\n{new_path}\n\n"
            "The application will then use this new location.\n"
            "Continue?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        try:
            # Copy the database file to the new location.
            shutil.copy2(str(current_db), str(new_path))

            # Persist the new path in config.
            set_custom_db_path(str(new_path))

            # Reinitialise the engine so all subsequent DB operations
            # use the new file without restarting the application.
            engine = init_engine(db_path=str(new_path), echo=False)
            Base.metadata.create_all(engine)

            QMessageBox.information(
                self,
                "Database Relocated",
                f"Database successfully moved to:\n\n{new_path}\n\n"
                "All subsequent operations will use this location.",
            )
            print(f"[admin] Database relocated to {new_path}")

        except Exception as exc:
            QMessageBox.critical(
                self,
                "Relocation Failed",
                f"Could not relocate the database:\n\n{exc}",
            )

    def _on_manage_db_managers(self):
        """Open the Manage DB Managers dialog (admin-only)."""
        dialog = ManageDBManagersDialog(admin_user=self._user, parent=self)
        dialog.exec()

    def _on_smtp_settings(self):
        """Open the SMTP settings dialog (admin-only)."""
        dialog = SmtpSettingsDialog(parent=self)
        dialog.exec()

    def _on_reset_user_password(self):
        """Open the admin Reset User Password dialog."""
        dialog = AdminResetPasswordDialog(admin_user=self._user, parent=self)
        dialog.exec()


# ═══════════════════════════════════════════════════════════════════
# DIALOG: Admin Reset User Password  (admin-only)
# ═══════════════════════════════════════════════════════════════════

class AdminResetPasswordDialog(QDialog):
    """
    Admin dialog for resetting another user's password to a temporary value.

    The admin searches for a user by username, then sets a new temporary
    password.  The target user will be forced to change the password on
    next login.
    """

    def __init__(self, admin_user, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Reset User Password")
        self.setFixedSize(440, 380)
        self._admin = admin_user
        self._target_user = None
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(24, 24, 24, 24)

        heading = QLabel("Reset User Password")
        heading.setAlignment(Qt.AlignCenter)
        font = QFont()
        font.setPointSize(16)
        font.setBold(True)
        heading.setFont(font)
        layout.addWidget(heading)

        hint = QLabel(
            "Look up a user by username and set a temporary password.\n"
            "The user will be forced to change it on next login."
        )
        hint.setAlignment(Qt.AlignCenter)
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #999; font-size: 12px; padding-bottom: 6px;")
        layout.addWidget(hint)

        # ── Username lookup ───────────────────────────────────────
        layout.addWidget(QLabel("Username"))
        lookup_row = QHBoxLayout()
        self.username_input = QLineEdit()
        self.username_input.setPlaceholderText("Enter username to look up")
        self.username_input.setStyleSheet(INPUT_STYLE)
        lookup_row.addWidget(self.username_input)

        self.lookup_btn = QPushButton("Look Up")
        self.lookup_btn.setStyleSheet(DIALOG_BTN_STYLE)
        self.lookup_btn.setCursor(Qt.PointingHandCursor)
        self.lookup_btn.clicked.connect(self._on_lookup)
        lookup_row.addWidget(self.lookup_btn)
        layout.addLayout(lookup_row)

        self.user_info = QLabel("")
        self.user_info.setStyleSheet("font-size: 13px; padding: 4px 0;")
        self.user_info.setVisible(False)
        layout.addWidget(self.user_info)

        # ── New temporary password ────────────────────────────────
        layout.addWidget(QLabel("New Temporary Password"))
        self.new_pw_input = QLineEdit()
        self.new_pw_input.setPlaceholderText("Temporary password for the user")
        self.new_pw_input.setStyleSheet(INPUT_STYLE)
        self.new_pw_input.setEnabled(False)
        layout.addWidget(self.new_pw_input)

        # ── Feedback + buttons ────────────────────────────────────
        self.feedback = _make_feedback_label()
        layout.addWidget(self.feedback)

        btn_row = QHBoxLayout()
        self.reset_btn = QPushButton("Reset Password")
        self.reset_btn.setStyleSheet(DIALOG_BTN_STYLE)
        self.reset_btn.setCursor(Qt.PointingHandCursor)
        self.reset_btn.setEnabled(False)
        self.reset_btn.clicked.connect(self._on_reset)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)

        btn_row.addStretch()
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(self.reset_btn)
        layout.addLayout(btn_row)

    def _on_lookup(self):
        _clear_feedback(self.feedback)
        username = self.username_input.text().strip()
        if not username:
            _show_error(self.feedback, "Please enter a username.")
            return

        user = get_user_by_username(username)
        if user is None:
            _show_error(self.feedback, f"No user found with username '{username}'.")
            self._target_user = None
            self.new_pw_input.setEnabled(False)
            self.reset_btn.setEnabled(False)
            self.user_info.setVisible(False)
            return

        if user.id == self._admin.id:
            _show_error(self.feedback, "You cannot reset your own password here.\nUse the Account dialog instead.")
            return

        self._target_user = user
        self.user_info.setText(
            f"Found: {user.display_name} ({user.username}) — ID {user.id}"
        )
        self.user_info.setVisible(True)
        self.new_pw_input.setEnabled(True)
        self.reset_btn.setEnabled(True)
        self.new_pw_input.setFocus()

    def _on_reset(self):
        _clear_feedback(self.feedback)
        if self._target_user is None:
            _show_error(self.feedback, "Please look up a user first.")
            return

        new_pw = self.new_pw_input.text().strip()
        if not new_pw:
            _show_error(self.feedback, "Please enter a temporary password.")
            self.new_pw_input.setFocus()
            return
        if len(new_pw) < 6:
            _show_error(self.feedback, "Password must be at least 6 characters.")
            self.new_pw_input.setFocus()
            return

        try:
            ok = admin_reset_user_password(
                target_user_id=self._target_user.id,
                new_temporary_password=new_pw,
                acting_user_id=self._admin.id,
            )
        except Exception as exc:
            _show_error(self.feedback, f"Reset failed: {exc}")
            return

        if ok:
            QMessageBox.information(
                self,
                "Password Reset",
                f"Password for '{self._target_user.username}' has been reset.\n\n"
                f"Temporary password: {new_pw}\n\n"
                "The user will be required to change it on next login.",
            )
            self.accept()
        else:
            _show_error(self.feedback, "Reset failed — user not found.")


# ═══════════════════════════════════════════════════════════════════
# DIALOG: SMTP Email Settings  (admin-only)
# ═══════════════════════════════════════════════════════════════════

class SmtpSettingsDialog(QDialog):
    """Dialog for configuring SMTP settings used for email verification
    and password reset codes."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Email (SMTP) Settings")
        self.setMinimumWidth(450)
        self._build_ui()
        self._load_existing()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        layout.addWidget(QLabel(
            "Configure SMTP settings for sending verification and\n"
            "password reset emails. These settings are saved locally."
        ))

        # ── SMTP fields ──────────────────────────────────────────
        self.host_input = QLineEdit()
        self.host_input.setPlaceholderText("SMTP Host (e.g. smtp.gmail.com)")
        layout.addWidget(QLabel("SMTP Host:"))
        layout.addWidget(self.host_input)

        self.port_input = QLineEdit()
        self.port_input.setPlaceholderText("Port (e.g. 587)")
        layout.addWidget(QLabel("Port:"))
        layout.addWidget(self.port_input)

        self.username_input = QLineEdit()
        self.username_input.setPlaceholderText("SMTP Username / Email")
        layout.addWidget(QLabel("Username:"))
        layout.addWidget(self.username_input)

        self.password_input = QLineEdit()
        self.password_input.setPlaceholderText("SMTP Password / App Password")
        self.password_input.setEchoMode(QLineEdit.Password)
        layout.addWidget(QLabel("Password:"))
        layout.addWidget(self.password_input)

        self.sender_input = QLineEdit()
        self.sender_input.setPlaceholderText("Sender email address (From:)")
        layout.addWidget(QLabel("Sender Email:"))
        layout.addWidget(self.sender_input)

        # ── TLS checkbox (using a button toggle for simplicity) ──
        from PySide6.QtWidgets import QCheckBox
        self.tls_checkbox = QCheckBox("Use TLS (recommended)")
        self.tls_checkbox.setChecked(True)
        layout.addWidget(self.tls_checkbox)

        # ── Feedback ──────────────────────────────────────────────
        self.feedback = QLabel("")
        self.feedback.setWordWrap(True)
        self.feedback.setVisible(False)
        layout.addWidget(self.feedback)

        # ── Buttons ───────────────────────────────────────────────
        btn_row = QHBoxLayout()

        self.test_btn = QPushButton("Test Connection")
        self.test_btn.clicked.connect(self._on_test)
        btn_row.addWidget(self.test_btn)

        self.save_btn = QPushButton("Save")
        self.save_btn.clicked.connect(self._on_save)
        btn_row.addWidget(self.save_btn)

        self.close_btn = QPushButton("Close")
        self.close_btn.clicked.connect(self.close)
        btn_row.addWidget(self.close_btn)

        layout.addLayout(btn_row)

    def _load_existing(self):
        """Pre-fill fields from existing config."""
        smtp = get_smtp_config()
        if smtp:
            self.host_input.setText(smtp.get("host", ""))
            self.port_input.setText(str(smtp.get("port", "")))
            self.username_input.setText(smtp.get("username", ""))
            self.password_input.setText(smtp.get("password", ""))
            self.sender_input.setText(smtp.get("sender_email", ""))
            self.tls_checkbox.setChecked(smtp.get("use_tls", True))

    def _show_feedback(self, message: str, is_error: bool = False):
        color = "#e74c3c" if is_error else "#2ecc71"
        self.feedback.setStyleSheet(f"color: {color}; font-size: 13px;")
        self.feedback.setText(message)
        self.feedback.setVisible(True)

    def _gather_values(self) -> dict:
        port_text = self.port_input.text().strip()
        try:
            port = int(port_text) if port_text else 587
        except ValueError:
            port = 587
        return {
            "host": self.host_input.text().strip(),
            "port": port,
            "username": self.username_input.text().strip(),
            "password": self.password_input.text(),
            "sender_email": self.sender_input.text().strip(),
            "use_tls": self.tls_checkbox.isChecked(),
        }

    def _on_save(self):
        vals = self._gather_values()
        if not vals["host"]:
            self._show_feedback("SMTP host is required.", is_error=True)
            return
        if not vals["sender_email"]:
            self._show_feedback("Sender email is required.", is_error=True)
            return

        save_smtp_config(**vals)
        self._show_feedback("SMTP settings saved successfully.")

    def _on_test(self):
        """Save current values and test the connection."""
        vals = self._gather_values()
        if not vals["host"]:
            self._show_feedback("Enter SMTP host first.", is_error=True)
            return

        # Save before testing so test_smtp_connection reads the config.
        save_smtp_config(**vals)
        self._show_feedback("Testing connection...", is_error=False)
        QApplication.processEvents()

        ok, msg = test_smtp_connection()
        self._show_feedback(msg, is_error=not ok)

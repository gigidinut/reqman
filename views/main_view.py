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
    create_entity,
    get_all_projects,
    get_user,
    update_password,
    update_user,
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
        self.setFixedSize(440, 480)
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

    def get_updated_user(self):
        """Return the (possibly updated) User object after the dialog closes."""
        return self._user


# ═══════════════════════════════════════════════════════════════════
# DIALOG: Open Project  (list selection)
# ═══════════════════════════════════════════════════════════════════

class OpenProjectDialog(QDialog):
    """
    Modal dialog that lists all existing projects from the database.

    The user selects one and clicks "Open".  The selected project's
    entity object is retrievable via `get_selected_project()` after
    the dialog is accepted.

    If there are no projects yet, a helpful message is shown instead
    of an empty list.
    """

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("Open Project")
        self.setFixedSize(460, 420)
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
        """Fetch all projects from the database and populate the list."""
        try:
            self._projects = get_all_projects()
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
        """Show the Create Project dialog.  On success, emit
        `project_opened` with the new Project entity."""
        dialog = CreateProjectDialog(user_id=self._user.id, parent=self)
        if dialog.exec() == QDialog.Accepted:
            project = dialog.get_created_project()
            if project:
                self.project_opened.emit(project)

    def _on_open_project(self):
        """Show the Open Project dialog.  On selection, emit
        `project_opened` with the chosen Project entity."""
        dialog = OpenProjectDialog(parent=self)
        if dialog.exec() == QDialog.Accepted:
            project = dialog.get_selected_project()
            if project:
                self.project_opened.emit(project)

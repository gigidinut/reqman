"""
auth_view.py — PySide6 Authentication UI for the Requirements Manager.

This module implements the complete authentication flow as a single
QMainWindow containing a QStackedWidget with four screens:

    Page 0 — LoginScreen
             Username + password fields, "Log In" button.
             Links to "Create Account" and "Reset Password" screens.
             On successful login, emits `login_successful(user)` signal.
             If the user has a temporary password, automatically transitions
             to the ChangePasswordScreen before granting access.

    Page 1 — CreateAccountScreen
             Username, display name, password, confirm-password fields.
             Creates the account via `db_controllers.create_user()` with
             `temporary_password=False` (self-registered users chose their
             own password, so no forced change needed).

    Page 2 — ResetPasswordScreen
             Username field only.  Looks up the user by name and calls
             `db_controllers.reset_password()` to set the password to the
             default temporary value "passwordtemp".  On next login the
             temporary-password flag will force a password change.

    Page 3 — ChangePasswordScreen
             Shown automatically when a user logs in with a temporary
             password.  Two fields: new password + confirm.  Calls
             `db_controllers.update_password()` to persist the change
             and clear the temporary flag.

Design decisions
────────────────
•  Every screen is a plain QWidget composed with QVBoxLayout/QHBoxLayout.
   No Designer .ui files — everything is created in code for portability.

•  Error feedback uses inline red QLabel text beneath the input fields,
   NOT modal QMessageBox dialogs — this feels more modern and doesn't
   block the event loop.  Success messages use green text.

•  The AuthWindow (QMainWindow) owns the QStackedWidget and exposes a
   single `login_successful` Signal that main.py connects to in order
   to transition to the main application window (built in a future step).

•  qdarktheme is applied at the QApplication level in main.py, so this
   module does not import or reference it — clean separation of concerns.

•  All database calls are wrapped in try/except so SQLAlchemy errors
   (e.g. duplicate username) surface as user-friendly messages rather
   than stack traces.
"""

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QKeyEvent
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QSizePolicy,
    QSpacerItem,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from controllers.db_controllers import (
    authenticate_user,
    create_user,
    list_users,
    reset_password,
    update_password,
)


# ═══════════════════════════════════════════════════════════════════
# STYLE CONSTANTS
# ═══════════════════════════════════════════════════════════════════
# These are applied as inline stylesheets so they layer on top of
# qdarktheme's base palette without conflicting with it.

# Fixed-width card that centres all auth forms on the window.
CARD_MAX_WIDTH = 420

# Font size for the main heading on each screen.
HEADING_FONT_SIZE = 22

# Inline feedback label colours (work well on both dark and light themes).
ERROR_STYLE = "color: #e74c3c; font-size: 13px; padding: 2px 0;"
SUCCESS_STYLE = "color: #2ecc71; font-size: 13px; padding: 2px 0;"

# Uniform input field height for visual consistency.
INPUT_STYLE = "padding: 8px; font-size: 14px;"

# Primary action button — accent colour that pops on dark backgrounds.
PRIMARY_BTN_STYLE = """
    QPushButton {
        background-color: #3498db;
        color: white;
        border: none;
        border-radius: 6px;
        padding: 10px;
        font-size: 15px;
        font-weight: 600;
    }
    QPushButton:hover {
        background-color: #2980b9;
    }
    QPushButton:pressed {
        background-color: #1f6fa5;
    }
"""

# Secondary "link-style" text button (no background, just underlined text).
LINK_BTN_STYLE = """
    QPushButton {
        background: transparent;
        border: none;
        color: #3498db;
        font-size: 13px;
        text-decoration: underline;
        padding: 4px;
    }
    QPushButton:hover {
        color: #2ecc71;
    }
"""


# ═══════════════════════════════════════════════════════════════════
# HELPER: reusable widget factory functions
# ═══════════════════════════════════════════════════════════════════

def _make_heading(text: str) -> QLabel:
    """Create a large, bold heading label centred on the screen."""
    label = QLabel(text)
    label.setAlignment(Qt.AlignCenter)
    font = QFont()
    font.setPointSize(HEADING_FONT_SIZE)
    font.setBold(True)
    label.setFont(font)
    return label


def _make_subheading(text: str) -> QLabel:
    """Create a smaller centred subheading for screen descriptions."""
    label = QLabel(text)
    label.setAlignment(Qt.AlignCenter)
    label.setStyleSheet("color: #999; font-size: 13px; padding-bottom: 8px;")
    return label


def _make_input(placeholder: str, is_password: bool = False) -> QLineEdit:
    """Create a styled QLineEdit with placeholder text.
    If `is_password` is True, characters are masked with bullets."""
    field = QLineEdit()
    field.setPlaceholderText(placeholder)
    field.setStyleSheet(INPUT_STYLE)
    if is_password:
        field.setEchoMode(QLineEdit.Password)
    return field


def _make_primary_button(text: str) -> QPushButton:
    """Create a wide accent-coloured action button."""
    btn = QPushButton(text)
    btn.setStyleSheet(PRIMARY_BTN_STYLE)
    btn.setCursor(Qt.PointingHandCursor)
    return btn


def _make_link_button(text: str) -> QPushButton:
    """Create an underlined text-only "link" button."""
    btn = QPushButton(text)
    btn.setStyleSheet(LINK_BTN_STYLE)
    btn.setCursor(Qt.PointingHandCursor)
    return btn


def _make_feedback_label() -> QLabel:
    """Create an initially-hidden label used for inline error/success messages."""
    label = QLabel("")
    label.setAlignment(Qt.AlignCenter)
    label.setWordWrap(True)
    # Start invisible — we show it only when there's a message.
    label.setVisible(False)
    return label


def _show_error(label: QLabel, message: str):
    """Display a red error message in the given feedback label."""
    label.setStyleSheet(ERROR_STYLE)
    label.setText(message)
    label.setVisible(True)


def _show_success(label: QLabel, message: str):
    """Display a green success message in the given feedback label."""
    label.setStyleSheet(SUCCESS_STYLE)
    label.setText(message)
    label.setVisible(True)


def _clear_feedback(label: QLabel):
    """Hide the feedback label and reset its text."""
    label.setText("")
    label.setVisible(False)


# ═══════════════════════════════════════════════════════════════════
# PAGE 0 — LOGIN SCREEN
# ═══════════════════════════════════════════════════════════════════

class LoginScreen(QWidget):
    """
    Username + password login form.

    Signals
    -------
    go_create_account : emitted when the user clicks "Create Account".
    go_reset_password : emitted when the user clicks "Reset Password".
    login_ok          : emitted on successful authentication; carries the
                        User object AND a flag indicating whether the
                        password is temporary (so the parent can route
                        to the ChangePasswordScreen).
    """

    # Signal payloads: (user_object, is_temporary: bool)
    # We use `object` as the type because PySide6 signals don't support
    # custom ORM model types directly.
    go_create_account = Signal()
    go_reset_password = Signal()
    login_ok = Signal(object, bool)  # (User, needs_password_change)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._build_ui()

    # ── UI construction ──────────────────────────────────────────

    def _build_ui(self):
        """Assemble the login form layout."""
        # Outer layout centres the card vertically and horizontally.
        outer = QVBoxLayout(self)
        outer.setAlignment(Qt.AlignCenter)

        # Card container — fixed-width column for the form elements.
        card = QWidget()
        card.setMaximumWidth(CARD_MAX_WIDTH)
        card_layout = QVBoxLayout(card)
        card_layout.setSpacing(12)

        # ── Heading ──────────────────────────────────────────────
        card_layout.addWidget(_make_heading("Requirements Manager"))
        card_layout.addWidget(_make_subheading("Sign in to your account"))

        # ── Input fields ─────────────────────────────────────────
        self.username_input = _make_input("Username")
        self.password_input = _make_input("Password", is_password=True)
        card_layout.addWidget(self.username_input)
        card_layout.addWidget(self.password_input)

        # ── Feedback label (errors / success) ────────────────────
        self.feedback = _make_feedback_label()
        card_layout.addWidget(self.feedback)

        # ── Login button ─────────────────────────────────────────
        self.login_btn = _make_primary_button("Log In")
        self.login_btn.clicked.connect(self._on_login_clicked)
        card_layout.addWidget(self.login_btn)

        # ── Navigation links (Create Account | Reset Password) ───
        links_row = QHBoxLayout()
        self.create_btn = _make_link_button("Create Account")
        self.reset_btn = _make_link_button("Reset Password")
        links_row.addWidget(self.create_btn)
        links_row.addStretch()
        links_row.addWidget(self.reset_btn)
        card_layout.addLayout(links_row)

        # Wire navigation signals.
        self.create_btn.clicked.connect(self.go_create_account.emit)
        self.reset_btn.clicked.connect(self.go_reset_password.emit)

        outer.addWidget(card)

    # ── Keyboard shortcut: Enter key submits the form ────────────

    def keyPressEvent(self, event: QKeyEvent):
        """Allow pressing Enter/Return to submit the login form."""
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            self._on_login_clicked()
        else:
            super().keyPressEvent(event)

    # ── Slot: handle the login attempt ───────────────────────────

    def _on_login_clicked(self):
        """Validate inputs and call the authentication controller."""
        _clear_feedback(self.feedback)

        username = self.username_input.text().strip()
        password = self.password_input.text()

        # ── Client-side validation ───────────────────────────────
        if not username:
            _show_error(self.feedback, "Please enter your username.")
            self.username_input.setFocus()
            return
        if not password:
            _show_error(self.feedback, "Please enter your password.")
            self.password_input.setFocus()
            return

        # ── Call the database controller ─────────────────────────
        try:
            success, user, message = authenticate_user(username, password)
        except Exception as exc:
            # Catch any unexpected DB errors and surface them gracefully.
            _show_error(self.feedback, f"Database error: {exc}")
            return

        if not success:
            # Map controller message codes to human-readable strings.
            error_map = {
                "invalid_credentials": "Invalid username or password.",
                "account_disabled": "This account has been disabled. Contact an administrator.",
            }
            _show_error(
                self.feedback,
                error_map.get(message, "Login failed. Please try again."),
            )
            # Clear the password field so the user can re-type.
            self.password_input.clear()
            self.password_input.setFocus()
            return

        # ── Success — emit the signal with the temp-password flag ─
        needs_change = (message == "temporary_password")
        self.login_ok.emit(user, needs_change)

    # ── Public helpers ───────────────────────────────────────────

    def reset_fields(self):
        """Clear all inputs and feedback.  Called when navigating back
        to this screen so stale data doesn't linger."""
        self.username_input.clear()
        self.password_input.clear()
        _clear_feedback(self.feedback)


# ═══════════════════════════════════════════════════════════════════
# PAGE 1 — CREATE ACCOUNT SCREEN
# ═══════════════════════════════════════════════════════════════════

class CreateAccountScreen(QWidget):
    """
    Registration form: username, display name, password, confirm password.

    On success, shows a green confirmation message and provides a link
    back to the login screen.

    Signal
    ------
    go_back : emitted to return to the login screen.
    """

    go_back = Signal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setAlignment(Qt.AlignCenter)

        card = QWidget()
        card.setMaximumWidth(CARD_MAX_WIDTH)
        card_layout = QVBoxLayout(card)
        card_layout.setSpacing(12)

        # ── Heading ──────────────────────────────────────────────
        card_layout.addWidget(_make_heading("Create Account"))
        card_layout.addWidget(_make_subheading("Fill in the details below"))

        # ── Input fields ─────────────────────────────────────────
        self.username_input = _make_input("Username")
        self.display_name_input = _make_input("Display Name")
        self.email_input = _make_input("Email Address")
        self.password_input = _make_input("Password", is_password=True)
        self.confirm_input = _make_input("Confirm Password", is_password=True)

        card_layout.addWidget(self.username_input)
        card_layout.addWidget(self.display_name_input)
        card_layout.addWidget(self.email_input)
        card_layout.addWidget(self.password_input)
        card_layout.addWidget(self.confirm_input)

        # ── Feedback label ───────────────────────────────────────
        self.feedback = _make_feedback_label()
        card_layout.addWidget(self.feedback)

        # ── Create button ────────────────────────────────────────
        self.create_btn = _make_primary_button("Create Account")
        self.create_btn.clicked.connect(self._on_create_clicked)
        card_layout.addWidget(self.create_btn)

        # ── Back link ────────────────────────────────────────────
        self.back_btn = _make_link_button("← Back to Login")
        self.back_btn.clicked.connect(self.go_back.emit)
        card_layout.addWidget(self.back_btn, alignment=Qt.AlignCenter)

        outer.addWidget(card)

    def keyPressEvent(self, event: QKeyEvent):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            self._on_create_clicked()
        else:
            super().keyPressEvent(event)

    def _on_create_clicked(self):
        """Validate fields and create the user account."""
        _clear_feedback(self.feedback)

        username = self.username_input.text().strip()
        display_name = self.display_name_input.text().strip()
        email = self.email_input.text().strip()
        password = self.password_input.text()
        confirm = self.confirm_input.text()

        # ── Client-side validation ───────────────────────────────
        if not username:
            _show_error(self.feedback, "Username is required.")
            self.username_input.setFocus()
            return
        if not display_name:
            _show_error(self.feedback, "Display name is required.")
            self.display_name_input.setFocus()
            return
        if not email:
            _show_error(self.feedback, "Email address is required.")
            self.email_input.setFocus()
            return
        # Basic email format check — must contain @ with text on both sides.
        if "@" not in email or "." not in email.split("@")[-1]:
            _show_error(self.feedback, "Please enter a valid email address.")
            self.email_input.setFocus()
            return
        if not password:
            _show_error(self.feedback, "Password is required.")
            self.password_input.setFocus()
            return
        if len(password) < 6:
            _show_error(self.feedback, "Password must be at least 6 characters.")
            self.password_input.setFocus()
            return
        if password != confirm:
            _show_error(self.feedback, "Passwords do not match.")
            self.confirm_input.clear()
            self.confirm_input.setFocus()
            return

        # ── Call the controller ──────────────────────────────────
        try:
            # Self-registration: temporary_password=False because the user
            # has just chosen their own password — no forced change needed.
            user = create_user(
                username=username,
                display_name=display_name,
                email=email,
                password=password,
                temporary_password=False,
            )
        except Exception as exc:
            # IntegrityError (duplicate username) surfaces here.
            error_text = str(exc)
            if "UNIQUE" in error_text.upper() or "unique" in error_text:
                _show_error(self.feedback, f"Username '{username}' is already taken.")
            else:
                _show_error(self.feedback, f"Error creating account: {exc}")
            return

        # ── Success feedback ─────────────────────────────────────
        _show_success(
            self.feedback,
            f"Account '{user.username}' created! You can now log in.",
        )
        # Disable the create button to prevent double-submission.
        self.create_btn.setEnabled(False)

    def reset_fields(self):
        """Clear all inputs and re-enable the button when navigating here."""
        self.username_input.clear()
        self.display_name_input.clear()
        self.email_input.clear()
        self.password_input.clear()
        self.confirm_input.clear()
        _clear_feedback(self.feedback)
        self.create_btn.setEnabled(True)


# ═══════════════════════════════════════════════════════════════════
# PAGE 2 — RESET PASSWORD SCREEN
# ═══════════════════════════════════════════════════════════════════

# The default temporary password assigned on reset.  Kept as a module
# constant so it can be changed in one place if policy evolves.
DEFAULT_TEMP_PASSWORD = "passwordtemp"


class ResetPasswordScreen(QWidget):
    """
    Admin / self-service password reset.

    The user enters a username; the controller sets that account's
    password to DEFAULT_TEMP_PASSWORD and flips the temporary flag.
    On next login the ChangePasswordScreen will force a new password.

    Signal
    ------
    go_back : return to the login screen.
    """

    go_back = Signal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setAlignment(Qt.AlignCenter)

        card = QWidget()
        card.setMaximumWidth(CARD_MAX_WIDTH)
        card_layout = QVBoxLayout(card)
        card_layout.setSpacing(12)

        # ── Heading ──────────────────────────────────────────────
        card_layout.addWidget(_make_heading("Reset Password"))
        card_layout.addWidget(_make_subheading(
            "Enter the username to reset.\n"
            f"The temporary password will be set to \"{DEFAULT_TEMP_PASSWORD}\"."
        ))

        # ── Input field ──────────────────────────────────────────
        self.username_input = _make_input("Username")
        card_layout.addWidget(self.username_input)

        # ── Feedback label ───────────────────────────────────────
        self.feedback = _make_feedback_label()
        card_layout.addWidget(self.feedback)

        # ── Reset button ─────────────────────────────────────────
        self.reset_btn = _make_primary_button("Reset Password")
        self.reset_btn.clicked.connect(self._on_reset_clicked)
        card_layout.addWidget(self.reset_btn)

        # ── Back link ────────────────────────────────────────────
        self.back_btn = _make_link_button("← Back to Login")
        self.back_btn.clicked.connect(self.go_back.emit)
        card_layout.addWidget(self.back_btn, alignment=Qt.AlignCenter)

        outer.addWidget(card)

    def keyPressEvent(self, event: QKeyEvent):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            self._on_reset_clicked()
        else:
            super().keyPressEvent(event)

    def _on_reset_clicked(self):
        """Look up the user and reset their password."""
        _clear_feedback(self.feedback)

        username = self.username_input.text().strip()
        if not username:
            _show_error(self.feedback, "Please enter a username.")
            self.username_input.setFocus()
            return

        # ── Find the user by username ────────────────────────────
        # `list_users` returns all active users; we filter client-side
        # to find the target.  This avoids exposing a get-by-username
        # controller that could be abused for enumeration in a larger app.
        try:
            all_users = list_users(active_only=False)
        except Exception as exc:
            _show_error(self.feedback, f"Database error: {exc}")
            return

        target_user = None
        for u in all_users:
            if u.username == username:
                target_user = u
                break

        if target_user is None:
            _show_error(self.feedback, f"No account found with username '{username}'.")
            return

        # ── Perform the reset ────────────────────────────────────
        # We use the target user's own ID as `acting_user_id` here because
        # this is a self-service flow — no admin is logged in yet.
        # In a production system you'd require an authenticated admin session.
        try:
            ok = reset_password(
                user_id=target_user.id,
                new_temporary_password=DEFAULT_TEMP_PASSWORD,
                acting_user_id=target_user.id,
            )
        except Exception as exc:
            _show_error(self.feedback, f"Error resetting password: {exc}")
            return

        if ok:
            _show_success(
                self.feedback,
                f"Password for '{username}' has been reset.\n"
                f"Temporary password: \"{DEFAULT_TEMP_PASSWORD}\"\n"
                "You will be asked to set a new password on next login.",
            )
        else:
            _show_error(self.feedback, "Reset failed — user not found.")

    def reset_fields(self):
        self.username_input.clear()
        _clear_feedback(self.feedback)


# ═══════════════════════════════════════════════════════════════════
# PAGE 3 — CHANGE PASSWORD SCREEN  (forced on temp-password login)
# ═══════════════════════════════════════════════════════════════════

class ChangePasswordScreen(QWidget):
    """
    Mandatory password change form shown when a user logs in with a
    temporary password.

    The screen receives the authenticated User object via `set_user()`
    before being displayed.  On successful update it emits `password_changed`
    with the User object so the parent can proceed to the main app.

    Signal
    ------
    password_changed : (User) emitted after the password is updated.
    go_back          : return to login (e.g. if user wants to cancel).
    """

    password_changed = Signal(object)  # carries the User
    go_back = Signal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._user = None  # set before the screen is shown
        self._build_ui()

    def set_user(self, user):
        """Store the authenticated user so we know whose password to update.
        Must be called BEFORE showing this screen."""
        self._user = user

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setAlignment(Qt.AlignCenter)

        card = QWidget()
        card.setMaximumWidth(CARD_MAX_WIDTH)
        card_layout = QVBoxLayout(card)
        card_layout.setSpacing(12)

        # ── Heading ──────────────────────────────────────────────
        card_layout.addWidget(_make_heading("Set New Password"))
        card_layout.addWidget(_make_subheading(
            "Your password is temporary.\n"
            "Please choose a new permanent password to continue."
        ))

        # ── Input fields ─────────────────────────────────────────
        self.new_password_input = _make_input("New Password", is_password=True)
        self.confirm_input = _make_input("Confirm New Password", is_password=True)
        card_layout.addWidget(self.new_password_input)
        card_layout.addWidget(self.confirm_input)

        # ── Feedback label ───────────────────────────────────────
        self.feedback = _make_feedback_label()
        card_layout.addWidget(self.feedback)

        # ── Save button ──────────────────────────────────────────
        self.save_btn = _make_primary_button("Save New Password")
        self.save_btn.clicked.connect(self._on_save_clicked)
        card_layout.addWidget(self.save_btn)

        # ── Cancel link (back to login) ──────────────────────────
        self.back_btn = _make_link_button("← Cancel and return to Login")
        self.back_btn.clicked.connect(self.go_back.emit)
        card_layout.addWidget(self.back_btn, alignment=Qt.AlignCenter)

        outer.addWidget(card)

    def keyPressEvent(self, event: QKeyEvent):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            self._on_save_clicked()
        else:
            super().keyPressEvent(event)

    def _on_save_clicked(self):
        """Validate and persist the new password."""
        _clear_feedback(self.feedback)

        new_pw = self.new_password_input.text()
        confirm = self.confirm_input.text()

        # ── Client-side validation ───────────────────────────────
        if not new_pw:
            _show_error(self.feedback, "Please enter a new password.")
            self.new_password_input.setFocus()
            return
        if len(new_pw) < 6:
            _show_error(self.feedback, "Password must be at least 6 characters.")
            self.new_password_input.setFocus()
            return
        if new_pw != confirm:
            _show_error(self.feedback, "Passwords do not match.")
            self.confirm_input.clear()
            self.confirm_input.setFocus()
            return
        if self._user is None:
            _show_error(self.feedback, "Internal error: no user context. Please log in again.")
            return

        # ── Call the controller ──────────────────────────────────
        try:
            ok = update_password(
                user_id=self._user.id,
                new_password=new_pw,
                clear_temporary_flag=True,
                acting_user_id=self._user.id,
            )
        except Exception as exc:
            _show_error(self.feedback, f"Error updating password: {exc}")
            return

        if ok:
            # Emit success — the parent AuthWindow will transition to the
            # main application screen.
            self.password_changed.emit(self._user)
        else:
            _show_error(self.feedback, "Failed to update password. Please try again.")

    def reset_fields(self):
        self.new_password_input.clear()
        self.confirm_input.clear()
        _clear_feedback(self.feedback)
        self._user = None


# ═══════════════════════════════════════════════════════════════════
# AUTH WINDOW  (top-level container)
# ═══════════════════════════════════════════════════════════════════

class AuthWindow(QMainWindow):
    """
    Top-level authentication window.

    Contains a QStackedWidget that cycles through the four auth screens.
    Emits `login_successful(user)` once the user has fully authenticated
    (including changing a temporary password if required).

    main.py creates this window, connects `login_successful` to launch
    the main project UI, and then shows the window.

    Page index mapping
    ──────────────────
    0 = LoginScreen
    1 = CreateAccountScreen
    2 = ResetPasswordScreen
    3 = ChangePasswordScreen
    """

    # Final signal: user is fully authenticated and ready to use the app.
    login_successful = Signal(object)  # carries the User object

    # Fixed window dimensions for the auth flow.
    WINDOW_WIDTH = 520
    WINDOW_HEIGHT = 520

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Requirements Manager — Authentication")
        self.setFixedSize(self.WINDOW_WIDTH, self.WINDOW_HEIGHT)

        # ── Build the stacked widget with all four screens ───────
        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)

        # Page 0: Login
        self.login_screen = LoginScreen()
        self.stack.addWidget(self.login_screen)

        # Page 1: Create Account
        self.create_screen = CreateAccountScreen()
        self.stack.addWidget(self.create_screen)

        # Page 2: Reset Password
        self.reset_screen = ResetPasswordScreen()
        self.stack.addWidget(self.reset_screen)

        # Page 3: Change Password (forced after temp-password login)
        self.change_pw_screen = ChangePasswordScreen()
        self.stack.addWidget(self.change_pw_screen)

        # ── Wire inter-screen navigation signals ─────────────────

        # Login → Create Account
        self.login_screen.go_create_account.connect(
            lambda: self._switch_to(1, self.create_screen)
        )
        # Login → Reset Password
        self.login_screen.go_reset_password.connect(
            lambda: self._switch_to(2, self.reset_screen)
        )
        # Create Account → back to Login
        self.create_screen.go_back.connect(
            lambda: self._switch_to(0, self.login_screen)
        )
        # Reset Password → back to Login
        self.reset_screen.go_back.connect(
            lambda: self._switch_to(0, self.login_screen)
        )
        # Change Password → cancel, back to Login
        self.change_pw_screen.go_back.connect(
            lambda: self._switch_to(0, self.login_screen)
        )

        # ── Login success routing ────────────────────────────────
        # If the password is temporary, divert to Change Password screen.
        # Otherwise, emit the final login_successful signal.
        self.login_screen.login_ok.connect(self._handle_login_result)

        # After password change, emit final login_successful.
        self.change_pw_screen.password_changed.connect(self._handle_password_changed)

        # Start on the login screen.
        self.stack.setCurrentIndex(0)

    # ── Navigation helper ────────────────────────────────────────

    def _switch_to(self, index: int, screen: QWidget):
        """Reset the target screen's fields and switch the stack to it."""
        if hasattr(screen, "reset_fields"):
            screen.reset_fields()
        self.stack.setCurrentIndex(index)

    # ── Login result routing ─────────────────────────────────────

    def _handle_login_result(self, user, needs_password_change: bool):
        """Called when LoginScreen emits login_ok.

        If the user's password is temporary, we redirect to the
        ChangePasswordScreen.  Otherwise we emit the final signal
        to transition to the main application."""
        if needs_password_change:
            # Pass the user to the change-password screen so it knows
            # whose password to update.
            self.change_pw_screen.set_user(user)
            self.change_pw_screen.reset_fields()
            # Restore the user reference after reset_fields cleared it.
            self.change_pw_screen.set_user(user)
            self.stack.setCurrentIndex(3)
        else:
            # Fully authenticated — hand off to main application.
            self.login_successful.emit(user)

    def _handle_password_changed(self, user):
        """Called after the user successfully changes their temporary password.
        Emits the final login_successful signal."""
        self.login_successful.emit(user)

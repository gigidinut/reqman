"""
auth_view.py — PySide6 Authentication UI for the Requirements Manager.

This module implements the complete authentication flow as a single
QMainWindow containing a QStackedWidget with screens:

    Page 0 — LoginScreen
    Page 1 — CreateAccountScreen
    Page 2 — ResetPasswordScreen (multi-step: username → email code → new password)
    Page 3 — ChangePasswordScreen (forced on temp-password login; admin also sets email)
    Page 4 — EmailVerifyScreen (enter code sent to email)
"""

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QKeyEvent
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSpacerItem,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from controllers.db_controllers import (
    authenticate_user,
    create_security_code,
    create_user,
    generate_recovery_keys,
    get_user_by_username,
    mark_email_verified,
    update_password,
    update_user_email,
    verify_recovery_key,
    verify_security_code,
)
from controllers.email_controller import (
    generate_security_code,
    is_smtp_configured,
    send_password_reset_email,
    send_verification_email,
)


# ═══════════════════════════════════════════════════════════════════
# STYLE CONSTANTS
# ═══════════════════════════════════════════════════════════════════

CARD_MAX_WIDTH = 420
HEADING_FONT_SIZE = 22

ERROR_STYLE = "color: #e74c3c; font-size: 13px; padding: 2px 0;"
SUCCESS_STYLE = "color: #2ecc71; font-size: 13px; padding: 2px 0;"
INFO_STYLE = "color: #3498db; font-size: 13px; padding: 2px 0;"

INPUT_STYLE = "padding: 8px; font-size: 14px;"

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
    label = QLabel(text)
    label.setAlignment(Qt.AlignCenter)
    font = QFont()
    font.setPointSize(HEADING_FONT_SIZE)
    font.setBold(True)
    label.setFont(font)
    return label


def _make_subheading(text: str) -> QLabel:
    label = QLabel(text)
    label.setAlignment(Qt.AlignCenter)
    label.setStyleSheet("color: #999; font-size: 13px; padding-bottom: 8px;")
    return label


def _make_input(placeholder: str, is_password: bool = False) -> QLineEdit:
    field = QLineEdit()
    field.setPlaceholderText(placeholder)
    field.setStyleSheet(INPUT_STYLE)
    if is_password:
        field.setEchoMode(QLineEdit.Password)
    return field


def _make_primary_button(text: str) -> QPushButton:
    btn = QPushButton(text)
    btn.setStyleSheet(PRIMARY_BTN_STYLE)
    btn.setCursor(Qt.PointingHandCursor)
    return btn


def _make_link_button(text: str) -> QPushButton:
    btn = QPushButton(text)
    btn.setStyleSheet(LINK_BTN_STYLE)
    btn.setCursor(Qt.PointingHandCursor)
    return btn


def _make_feedback_label() -> QLabel:
    label = QLabel("")
    label.setAlignment(Qt.AlignCenter)
    label.setWordWrap(True)
    label.setVisible(False)
    return label


def _show_error(label: QLabel, message: str):
    label.setStyleSheet(ERROR_STYLE)
    label.setText(message)
    label.setVisible(True)


def _show_success(label: QLabel, message: str):
    label.setStyleSheet(SUCCESS_STYLE)
    label.setText(message)
    label.setVisible(True)


def _show_info(label: QLabel, message: str):
    label.setStyleSheet(INFO_STYLE)
    label.setText(message)
    label.setVisible(True)


def _clear_feedback(label: QLabel):
    label.setText("")
    label.setVisible(False)


def _validate_email(email: str) -> bool:
    """Basic email format check."""
    return "@" in email and "." in email.split("@")[-1]


# ═══════════════════════════════════════════════════════════════════
# RECOVERY KEYS DIALOG  (shown once after generation)
# ═══════════════════════════════════════════════════════════════════

class RecoveryKeysDialog(QDialog):
    """
    Modal dialog that displays a set of recovery keys to the user.

    These keys are shown ONCE — they cannot be retrieved later.
    The user is instructed to write them down or print them.
    """

    def __init__(self, keys: list, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Your Recovery Keys")
        self.setMinimumWidth(420)
        self.setModal(True)
        self._build_ui(keys)

    def _build_ui(self, keys: list):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        heading = QLabel("Recovery Keys")
        heading.setAlignment(Qt.AlignCenter)
        font = QFont()
        font.setPointSize(16)
        font.setBold(True)
        heading.setFont(font)
        layout.addWidget(heading)

        warning = QLabel(
            "Write these down or print them now.\n"
            "They will NOT be shown again.\n\n"
            "Each key can be used once to reset your password\n"
            "without needing email access."
        )
        warning.setAlignment(Qt.AlignCenter)
        warning.setStyleSheet("color: #e67e22; font-size: 13px; padding: 4px;")
        warning.setWordWrap(True)
        layout.addWidget(warning)

        # Display keys in a read-only text area for easy copying.
        keys_text = QTextEdit()
        keys_text.setReadOnly(True)
        keys_text.setStyleSheet(
            "font-family: 'Consolas', 'Courier New', monospace; "
            "font-size: 15px; padding: 10px; letter-spacing: 1px;"
        )
        keys_text.setPlainText("\n".join(f"  {i+1}.  {k}" for i, k in enumerate(keys)))
        keys_text.setFixedHeight(min(40 * len(keys), 250))
        layout.addWidget(keys_text)

        # Copy button.
        copy_btn = QPushButton("Copy to Clipboard")
        copy_btn.setCursor(Qt.PointingHandCursor)
        copy_btn.clicked.connect(
            lambda: (
                QApplication.clipboard().setText("\n".join(keys)),
                copy_btn.setText("Copied!"),
            )
        )
        layout.addWidget(copy_btn)

        # Close button.
        close_btn = QPushButton("I have saved my keys")
        close_btn.setStyleSheet(PRIMARY_BTN_STYLE)
        close_btn.setCursor(Qt.PointingHandCursor)
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn)


def _show_recovery_keys(keys: list, parent=None):
    """Helper to show the RecoveryKeysDialog."""
    if keys:
        dlg = RecoveryKeysDialog(keys, parent=parent)
        dlg.exec()


# ═══════════════════════════════════════════════════════════════════
# PAGE 0 — LOGIN SCREEN
# ═══════════════════════════════════════════════════════════════════

class LoginScreen(QWidget):
    go_create_account = Signal()
    go_reset_password = Signal()
    login_ok = Signal(object, bool)  # (User, needs_password_change)

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

        card_layout.addWidget(_make_heading("Requirements Manager"))
        card_layout.addWidget(_make_subheading("Sign in to your account"))

        self.username_input = _make_input("Username")
        self.password_input = _make_input("Password", is_password=True)
        card_layout.addWidget(self.username_input)
        card_layout.addWidget(self.password_input)

        self.feedback = _make_feedback_label()
        card_layout.addWidget(self.feedback)

        self.login_btn = _make_primary_button("Log In")
        self.login_btn.clicked.connect(self._on_login_clicked)
        card_layout.addWidget(self.login_btn)

        links_row = QHBoxLayout()
        self.create_btn = _make_link_button("Create Account")
        self.reset_btn = _make_link_button("Reset Password")
        links_row.addWidget(self.create_btn)
        links_row.addStretch()
        links_row.addWidget(self.reset_btn)
        card_layout.addLayout(links_row)

        self.create_btn.clicked.connect(self.go_create_account.emit)
        self.reset_btn.clicked.connect(self.go_reset_password.emit)

        outer.addWidget(card)

    def keyPressEvent(self, event: QKeyEvent):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            self._on_login_clicked()
        else:
            super().keyPressEvent(event)

    def _on_login_clicked(self):
        _clear_feedback(self.feedback)

        username = self.username_input.text().strip()
        password = self.password_input.text()

        if not username:
            _show_error(self.feedback, "Please enter your username.")
            self.username_input.setFocus()
            return
        if not password:
            _show_error(self.feedback, "Please enter your password.")
            self.password_input.setFocus()
            return

        try:
            success, user, message = authenticate_user(username, password)
        except Exception as exc:
            _show_error(self.feedback, f"Database error: {exc}")
            return

        if not success:
            error_map = {
                "invalid_credentials": "Invalid username or password.",
                "account_disabled": "This account has been disabled. Contact an administrator.",
            }
            _show_error(
                self.feedback,
                error_map.get(message, "Login failed. Please try again."),
            )
            self.password_input.clear()
            self.password_input.setFocus()
            return

        needs_change = (message == "temporary_password")
        self.login_ok.emit(user, needs_change)

    def reset_fields(self):
        self.username_input.clear()
        self.password_input.clear()
        _clear_feedback(self.feedback)


# ═══════════════════════════════════════════════════════════════════
# PAGE 1 — CREATE ACCOUNT SCREEN
# ═══════════════════════════════════════════════════════════════════

class CreateAccountScreen(QWidget):
    go_back = Signal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._created_user = None
        self._email_code_sent = False
        self._build_ui()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setAlignment(Qt.AlignCenter)

        card = QWidget()
        card.setMaximumWidth(CARD_MAX_WIDTH)
        card_layout = QVBoxLayout(card)
        card_layout.setSpacing(12)

        card_layout.addWidget(_make_heading("Create Account"))
        self._subheading = _make_subheading("Fill in the details below")
        card_layout.addWidget(self._subheading)

        # ── Registration fields ───────────────────────────────────
        self.username_input = _make_input("Username")
        self.display_name_input = _make_input("Display Name")
        self.email_input = _make_input("Email Address (optional)")
        self.password_input = _make_input("Password", is_password=True)
        self.confirm_input = _make_input("Confirm Password", is_password=True)

        card_layout.addWidget(self.username_input)
        card_layout.addWidget(self.display_name_input)
        card_layout.addWidget(self.email_input)
        card_layout.addWidget(self.password_input)
        card_layout.addWidget(self.confirm_input)

        # ── Email verification fields (hidden until account created) ──
        self.verify_code_input = _make_input("6-digit verification code")
        self.verify_code_input.setVisible(False)
        card_layout.addWidget(self.verify_code_input)

        self.email_feedback = _make_feedback_label()
        card_layout.addWidget(self.email_feedback)

        self.verify_btn = _make_primary_button("Verify Email")
        self.verify_btn.setVisible(False)
        self.verify_btn.clicked.connect(self._on_verify_code)
        card_layout.addWidget(self.verify_btn)

        self.resend_btn = _make_link_button("Resend code")
        self.resend_btn.setVisible(False)
        self.resend_btn.clicked.connect(self._on_resend_code)
        card_layout.addWidget(self.resend_btn, alignment=Qt.AlignCenter)

        self.skip_verify_btn = _make_link_button("Skip — verify later from Account settings")
        self.skip_verify_btn.setVisible(False)
        self.skip_verify_btn.clicked.connect(self._on_skip_verification)
        card_layout.addWidget(self.skip_verify_btn, alignment=Qt.AlignCenter)

        # ── Main feedback + create button ─────────────────────────
        self.feedback = _make_feedback_label()
        card_layout.addWidget(self.feedback)

        self.create_btn = _make_primary_button("Create Account")
        self.create_btn.clicked.connect(self._on_create_clicked)
        card_layout.addWidget(self.create_btn)

        self.back_btn = _make_link_button("← Back to Login")
        self.back_btn.clicked.connect(self.go_back.emit)
        card_layout.addWidget(self.back_btn, alignment=Qt.AlignCenter)

        outer.addWidget(card)

    def keyPressEvent(self, event: QKeyEvent):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            if self._created_user and self.verify_code_input.isVisible():
                self._on_verify_code()
            else:
                self._on_create_clicked()
        else:
            super().keyPressEvent(event)

    # ── Account creation ──────────────────────────────────────────

    def _on_create_clicked(self):
        _clear_feedback(self.feedback)

        username = self.username_input.text().strip()
        display_name = self.display_name_input.text().strip()
        email = self.email_input.text().strip()
        password = self.password_input.text()
        confirm = self.confirm_input.text()

        if not username:
            _show_error(self.feedback, "Username is required.")
            self.username_input.setFocus()
            return
        if not display_name:
            _show_error(self.feedback, "Display name is required.")
            self.display_name_input.setFocus()
            return
        if email and not _validate_email(email):
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

        try:
            user = create_user(
                username=username,
                display_name=display_name,
                email=email,
                password=password,
                temporary_password=False,
            )
        except Exception as exc:
            error_text = str(exc)
            if "UNIQUE" in error_text.upper() or "unique" in error_text:
                _show_error(self.feedback, f"Username '{username}' is already taken.")
            else:
                _show_error(self.feedback, f"Error creating account: {exc}")
            return

        self._created_user = user

        # Transition to email verification step.
        self._show_verification_step()

    # ── Email verification step ───────────────────────────────────

    def _show_verification_step(self):
        """Hide registration fields and show email verification UI."""
        # Hide registration inputs.
        self.username_input.setVisible(False)
        self.display_name_input.setVisible(False)
        self.email_input.setVisible(False)
        self.password_input.setVisible(False)
        self.confirm_input.setVisible(False)
        self.create_btn.setVisible(False)
        _clear_feedback(self.feedback)

        has_email = bool(self._created_user.email and self._created_user.email.strip())

        if has_email and is_smtp_configured():
            # Send a verification code.
            self._subheading.setText(
                f"Account created! A verification code has been\n"
                f"sent to {self._created_user.email}."
            )
            self.verify_code_input.setVisible(True)
            self.verify_btn.setVisible(True)
            self.resend_btn.setVisible(True)
            self.skip_verify_btn.setVisible(True)
            self._send_verification_code()
        else:
            # No email provided or SMTP not configured — skip to recovery keys.
            self._subheading.setText("Account created!")
            self._finish_with_recovery_keys()

    def _send_verification_code(self) -> bool:
        """Generate and send a verification code to the user's email."""
        _clear_feedback(self.email_feedback)
        code = generate_security_code()
        try:
            create_security_code(
                user_id=self._created_user.id,
                purpose="email_verify",
                code=code,
            )
        except Exception as exc:
            _show_error(self.email_feedback, f"Error creating code: {exc}")
            return False

        ok, msg = send_verification_email(self._created_user.email, code)
        if not ok:
            _show_error(self.email_feedback, f"Failed to send email:\n{msg}")
            return False

        self._email_code_sent = True
        _show_success(self.email_feedback, "Verification code sent! Check your email.")
        return True

    def _on_resend_code(self):
        """Resend the verification code."""
        self._send_verification_code()

    def _on_verify_code(self):
        """Verify the code entered by the user."""
        _clear_feedback(self.email_feedback)

        code = self.verify_code_input.text().strip()
        if not code:
            _show_error(self.email_feedback, "Please enter the 6-digit code.")
            self.verify_code_input.setFocus()
            return

        try:
            valid = verify_security_code(
                user_id=self._created_user.id,
                purpose="email_verify",
                code=code,
            )
        except Exception as exc:
            _show_error(self.email_feedback, f"Error verifying code: {exc}")
            return

        if not valid:
            _show_error(self.email_feedback, "Invalid or expired code. Try again.")
            self.verify_code_input.clear()
            self.verify_code_input.setFocus()
            return

        # Mark email as verified.
        try:
            mark_email_verified(self._created_user.id)
        except Exception as exc:
            _show_error(self.email_feedback, f"Error: {exc}")
            return

        _show_success(self.email_feedback, "Email verified!")
        self.verify_code_input.setVisible(False)
        self.verify_btn.setVisible(False)
        self.resend_btn.setVisible(False)
        self.skip_verify_btn.setVisible(False)

        self._finish_with_recovery_keys()

    def _on_skip_verification(self):
        """Skip email verification — user can do it later."""
        self.verify_code_input.setVisible(False)
        self.verify_btn.setVisible(False)
        self.resend_btn.setVisible(False)
        self.skip_verify_btn.setVisible(False)
        _clear_feedback(self.email_feedback)

        self._finish_with_recovery_keys()

    def _finish_with_recovery_keys(self):
        """Generate recovery keys and show them, then display final message."""
        try:
            keys = generate_recovery_keys(user_id=self._created_user.id, count=5)
            _show_recovery_keys(keys, parent=self)
        except Exception:
            pass  # Non-critical — user can regenerate from Account settings.

        self._subheading.setText(
            f"Account '{self._created_user.username}' is ready!\n"
            "You can now log in."
        )
        _clear_feedback(self.email_feedback)

    def reset_fields(self):
        self._created_user = None
        self._email_code_sent = False
        self.username_input.clear()
        self.username_input.setVisible(True)
        self.display_name_input.clear()
        self.display_name_input.setVisible(True)
        self.email_input.clear()
        self.email_input.setVisible(True)
        self.password_input.clear()
        self.password_input.setVisible(True)
        self.confirm_input.clear()
        self.confirm_input.setVisible(True)
        self.verify_code_input.clear()
        self.verify_code_input.setVisible(False)
        self.verify_btn.setVisible(False)
        self.resend_btn.setVisible(False)
        self.skip_verify_btn.setVisible(False)
        self.create_btn.setVisible(True)
        self.create_btn.setEnabled(True)
        _clear_feedback(self.feedback)
        _clear_feedback(self.email_feedback)
        self._subheading.setText("Fill in the details below")


# ═══════════════════════════════════════════════════════════════════
# PAGE 2 — RESET PASSWORD SCREEN  (multi-step: email verification)
# ═══════════════════════════════════════════════════════════════════

class ResetPasswordScreen(QWidget):
    """
    Password reset with two methods:
      Method A — Email: username → send code to email → enter code → new password
      Method B — Recovery Key: username → enter recovery key → new password

    Step 1 — Enter username, choose method
    Step 2a — Enter email code  /  Step 2b — Enter recovery key
    Step 3 — Enter and confirm a new password
    """

    go_back = Signal()

    # Reset methods
    _METHOD_EMAIL = "email"
    _METHOD_RECOVERY = "recovery"

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._target_user = None
        self._step = 1
        self._method = None
        self._build_ui()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setAlignment(Qt.AlignCenter)

        card = QWidget()
        card.setMaximumWidth(CARD_MAX_WIDTH)
        self._card_layout = QVBoxLayout(card)
        self._card_layout.setSpacing(12)

        self._heading = _make_heading("Reset Password")
        self._subheading = _make_subheading(
            "Enter your username to begin."
        )
        self._card_layout.addWidget(self._heading)
        self._card_layout.addWidget(self._subheading)

        # Step 1: Username
        self.username_input = _make_input("Username")
        self._card_layout.addWidget(self.username_input)

        # Step 2a: Email code entry (hidden initially)
        self.code_input = _make_input("6-digit code from email")
        self.code_input.setVisible(False)
        self._card_layout.addWidget(self.code_input)

        # Step 2b: Recovery key entry (hidden initially)
        self.recovery_key_input = _make_input("Recovery Key (e.g. FROG-XXXX-XXXX-XXXX)")
        self.recovery_key_input.setVisible(False)
        self._card_layout.addWidget(self.recovery_key_input)

        # Step 3: New password (hidden initially)
        self.new_password_input = _make_input("New Password", is_password=True)
        self.confirm_input = _make_input("Confirm New Password", is_password=True)
        self.new_password_input.setVisible(False)
        self.confirm_input.setVisible(False)
        self._card_layout.addWidget(self.new_password_input)
        self._card_layout.addWidget(self.confirm_input)

        self.feedback = _make_feedback_label()
        self._card_layout.addWidget(self.feedback)

        # Method choice buttons (shown after username lookup)
        self._method_row = QWidget()
        method_layout = QHBoxLayout(self._method_row)
        method_layout.setContentsMargins(0, 0, 0, 0)
        self.email_method_btn = _make_primary_button("Send Email Code")
        self.recovery_method_btn = _make_primary_button("Use Recovery Key")
        self.recovery_method_btn.setStyleSheet(
            self.recovery_method_btn.styleSheet().replace("#3498db", "#e67e22").replace("#2980b9", "#d35400").replace("#1f6fa5", "#c0392b")
        )
        self.email_method_btn.clicked.connect(self._on_choose_email)
        self.recovery_method_btn.clicked.connect(self._on_choose_recovery)
        method_layout.addWidget(self.email_method_btn)
        method_layout.addWidget(self.recovery_method_btn)
        self._method_row.setVisible(False)
        self._card_layout.addWidget(self._method_row)

        # Main action button (hidden during method choice, used for steps 2/3)
        self.action_btn = _make_primary_button("Look Up Account")
        self.action_btn.clicked.connect(self._on_action_clicked)
        self._card_layout.addWidget(self.action_btn)

        # Resend link (hidden initially)
        self.resend_btn = _make_link_button("Resend code")
        self.resend_btn.setVisible(False)
        self.resend_btn.clicked.connect(self._on_resend_code)
        self._card_layout.addWidget(self.resend_btn, alignment=Qt.AlignCenter)

        self.back_btn = _make_link_button("← Back to Login")
        self.back_btn.clicked.connect(self.go_back.emit)
        self._card_layout.addWidget(self.back_btn, alignment=Qt.AlignCenter)

        outer.addWidget(card)

    def keyPressEvent(self, event: QKeyEvent):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            self._on_action_clicked()
        else:
            super().keyPressEvent(event)

    def _on_action_clicked(self):
        if self._step == 1:
            self._step1_lookup_user()
        elif self._step == 2:
            if self._method == self._METHOD_EMAIL:
                self._step2a_verify_email_code()
            else:
                self._step2b_verify_recovery_key()
        elif self._step == 3:
            self._step3_set_password()

    # ── Step 1: Look up user and present method choices ───────────

    def _step1_lookup_user(self):
        _clear_feedback(self.feedback)

        username = self.username_input.text().strip()
        if not username:
            _show_error(self.feedback, "Please enter a username.")
            self.username_input.setFocus()
            return

        try:
            user = get_user_by_username(username)
        except Exception as exc:
            _show_error(self.feedback, f"Database error: {exc}")
            return

        if user is None:
            _show_error(self.feedback, "No account found with that username.")
            return

        self._target_user = user

        # Determine available methods.
        has_email = (
            user.email
            and _validate_email(user.email)
            and user.email_verified
            and is_smtp_configured()
        )
        from controllers.db_controllers import count_unused_recovery_keys
        has_keys = count_unused_recovery_keys(user.id) > 0

        if not has_email and not has_keys:
            _show_error(
                self.feedback,
                "No reset method available for this account.\n"
                "No verified email and no recovery keys.\n"
                "Contact your administrator.",
            )
            return

        if has_email and has_keys:
            # Show both options.
            self.username_input.setVisible(False)
            self.action_btn.setVisible(False)
            self._method_row.setVisible(True)
            self._subheading.setText("Choose a reset method:")
        elif has_email:
            # Only email available — go straight to email flow.
            self._on_choose_email()
        else:
            # Only recovery keys available — go straight to recovery flow.
            self._on_choose_recovery()

    # ── Method choice handlers ────────────────────────────────────

    def _on_choose_email(self):
        self._method = self._METHOD_EMAIL
        self._method_row.setVisible(False)
        self.username_input.setVisible(False)

        ok = self._send_reset_code()
        if not ok:
            # Show method choice again on failure.
            self._method_row.setVisible(True)
            return

        self._step = 2
        self.code_input.setVisible(True)
        self.resend_btn.setVisible(True)
        self.action_btn.setText("Verify Code")
        self.action_btn.setVisible(True)
        self._subheading.setText(
            f"A 6-digit code has been sent to the email address\n"
            f"associated with '{self._target_user.username}'."
        )

    def _on_choose_recovery(self):
        self._method = self._METHOD_RECOVERY
        self._method_row.setVisible(False)
        self.username_input.setVisible(False)

        self._step = 2
        self.recovery_key_input.setVisible(True)
        self.action_btn.setText("Verify Key")
        self.action_btn.setVisible(True)
        self._subheading.setText(
            "Enter one of your recovery keys.\n"
            "Each key can only be used once."
        )
        _clear_feedback(self.feedback)

    # ── Step 2a: Email code ───────────────────────────────────────

    def _send_reset_code(self) -> bool:
        code = generate_security_code()
        try:
            create_security_code(
                user_id=self._target_user.id,
                purpose="password_reset",
                code=code,
            )
        except Exception as exc:
            _show_error(self.feedback, f"Error creating security code: {exc}")
            return False

        ok, msg = send_password_reset_email(self._target_user.email, code)
        if not ok:
            _show_error(self.feedback, f"Failed to send email:\n{msg}")
            return False

        _show_success(self.feedback, "Reset code sent! Check your email.")
        return True

    def _on_resend_code(self):
        _clear_feedback(self.feedback)
        if self._target_user:
            self._send_reset_code()

    def _step2a_verify_email_code(self):
        _clear_feedback(self.feedback)

        code = self.code_input.text().strip()
        if not code:
            _show_error(self.feedback, "Please enter the 6-digit code.")
            self.code_input.setFocus()
            return

        try:
            valid = verify_security_code(
                user_id=self._target_user.id,
                purpose="password_reset",
                code=code,
            )
        except Exception as exc:
            _show_error(self.feedback, f"Error verifying code: {exc}")
            return

        if not valid:
            _show_error(
                self.feedback,
                "Invalid or expired code. Please try again or resend.",
            )
            self.code_input.clear()
            self.code_input.setFocus()
            return

        self._transition_to_step3()

    # ── Step 2b: Recovery key ─────────────────────────────────────

    def _step2b_verify_recovery_key(self):
        _clear_feedback(self.feedback)

        key = self.recovery_key_input.text().strip()
        if not key:
            _show_error(self.feedback, "Please enter a recovery key.")
            self.recovery_key_input.setFocus()
            return

        try:
            valid = verify_recovery_key(
                user_id=self._target_user.id,
                plaintext_key=key,
            )
        except Exception as exc:
            _show_error(self.feedback, f"Error verifying key: {exc}")
            return

        if not valid:
            _show_error(
                self.feedback,
                "Invalid or already-used recovery key.",
            )
            self.recovery_key_input.clear()
            self.recovery_key_input.setFocus()
            return

        self._transition_to_step3()

    # ── Step 3: New password ──────────────────────────────────────

    def _transition_to_step3(self):
        self._step = 3
        self.code_input.setVisible(False)
        self.recovery_key_input.setVisible(False)
        self.resend_btn.setVisible(False)
        self.new_password_input.setVisible(True)
        self.confirm_input.setVisible(True)
        self.action_btn.setText("Set New Password")
        self._subheading.setText("Verified! Choose a new password.")
        _clear_feedback(self.feedback)

    def _step3_set_password(self):
        _clear_feedback(self.feedback)

        new_pw = self.new_password_input.text()
        confirm = self.confirm_input.text()

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

        try:
            ok = update_password(
                user_id=self._target_user.id,
                new_password=new_pw,
                clear_temporary_flag=True,
                acting_user_id=self._target_user.id,
            )
        except Exception as exc:
            _show_error(self.feedback, f"Error updating password: {exc}")
            return

        if ok:
            _show_success(
                self.feedback,
                "Password updated successfully! You can now log in.",
            )
            self.action_btn.setEnabled(False)
        else:
            _show_error(self.feedback, "Failed to update password.")

    def reset_fields(self):
        self._target_user = None
        self._step = 1
        self._method = None
        self.username_input.clear()
        self.username_input.setVisible(True)
        self.code_input.clear()
        self.code_input.setVisible(False)
        self.recovery_key_input.clear()
        self.recovery_key_input.setVisible(False)
        self.new_password_input.clear()
        self.new_password_input.setVisible(False)
        self.confirm_input.clear()
        self.confirm_input.setVisible(False)
        self.resend_btn.setVisible(False)
        self._method_row.setVisible(False)
        self.action_btn.setText("Look Up Account")
        self.action_btn.setVisible(True)
        self.action_btn.setEnabled(True)
        self._subheading.setText("Enter your username to begin.")
        _clear_feedback(self.feedback)


# ═══════════════════════════════════════════════════════════════════
# PAGE 3 — CHANGE PASSWORD SCREEN  (forced on temp-password login)
#
# For admin users, this also requires setting up a verified email
# address (needed for future password resets).
# ═══════════════════════════════════════════════════════════════════

class ChangePasswordScreen(QWidget):
    """
    Mandatory password change form.  For admin users on first login,
    also collects and verifies an email address.
    """

    password_changed = Signal(object)  # carries the User
    go_back = Signal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._user = None
        self._email_code_sent = False
        self._email_verified = False
        self._build_ui()

    def set_user(self, user):
        self._user = user
        # Show email fields as optional for admin users without a verified email.
        show_email = (
            user is not None
            and getattr(user, "is_admin", False)
            and not getattr(user, "email_verified", False)
        )
        self._show_email_section(show_email)

    def _show_email_section(self, show: bool):
        """Toggle visibility of the email setup fields."""
        self.email_input.setVisible(show)
        self.send_code_btn.setVisible(show)
        self.email_code_input.setVisible(False)
        self.verify_code_btn.setVisible(False)
        self.email_status.setVisible(False)
        if show:
            self._subheading.setText(
                "Your password is temporary.\n"
                "Please set a new password. You may optionally\n"
                "provide an email for account recovery."
            )
        else:
            self._subheading.setText(
                "Your password is temporary.\n"
                "Please choose a new permanent password to continue."
            )

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setAlignment(Qt.AlignCenter)

        card = QWidget()
        card.setMaximumWidth(CARD_MAX_WIDTH)
        card_layout = QVBoxLayout(card)
        card_layout.setSpacing(10)

        card_layout.addWidget(_make_heading("Set New Password"))
        self._subheading = _make_subheading(
            "Your password is temporary.\n"
            "Please choose a new permanent password to continue."
        )
        card_layout.addWidget(self._subheading)

        # ── Password fields ───────────────────────────────────────
        self.new_password_input = _make_input("New Password", is_password=True)
        self.confirm_input = _make_input("Confirm New Password", is_password=True)
        card_layout.addWidget(self.new_password_input)
        card_layout.addWidget(self.confirm_input)

        # ── Email setup section (admin first login) ───────────────
        self.email_input = _make_input("Email Address (for account recovery)")
        self.email_input.setVisible(False)
        card_layout.addWidget(self.email_input)

        self.send_code_btn = _make_link_button("Send verification code")
        self.send_code_btn.setVisible(False)
        self.send_code_btn.clicked.connect(self._on_send_email_code)
        card_layout.addWidget(self.send_code_btn, alignment=Qt.AlignCenter)

        self.email_code_input = _make_input("6-digit verification code")
        self.email_code_input.setVisible(False)
        card_layout.addWidget(self.email_code_input)

        self.verify_code_btn = _make_link_button("Verify code")
        self.verify_code_btn.setVisible(False)
        self.verify_code_btn.clicked.connect(self._on_verify_email_code)
        card_layout.addWidget(self.verify_code_btn, alignment=Qt.AlignCenter)

        self.email_status = _make_feedback_label()
        card_layout.addWidget(self.email_status)

        # ── Main feedback + save button ───────────────────────────
        self.feedback = _make_feedback_label()
        card_layout.addWidget(self.feedback)

        self.save_btn = _make_primary_button("Save New Password")
        self.save_btn.clicked.connect(self._on_save_clicked)
        card_layout.addWidget(self.save_btn)

        self.back_btn = _make_link_button("← Cancel and return to Login")
        self.back_btn.clicked.connect(self.go_back.emit)
        card_layout.addWidget(self.back_btn, alignment=Qt.AlignCenter)

        outer.addWidget(card)

    def keyPressEvent(self, event: QKeyEvent):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            self._on_save_clicked()
        else:
            super().keyPressEvent(event)

    # ── Email verification flow ───────────────────────────────────

    def _on_send_email_code(self):
        """Send a verification code to the entered email address."""
        _clear_feedback(self.email_status)
        email = self.email_input.text().strip()

        if not email:
            _show_error(self.email_status, "Please enter an email address.")
            self.email_input.setFocus()
            return
        if not _validate_email(email):
            _show_error(self.email_status, "Please enter a valid email address.")
            self.email_input.setFocus()
            return

        if not is_smtp_configured():
            _show_error(
                self.email_status,
                "Email is not configured yet. You can set up SMTP\n"
                "settings after login via the admin menu, then verify\n"
                "your email from your profile.",
            )
            # Allow proceeding without email verification for initial setup.
            self._email_verified = True
            _show_info(self.email_status, "Email setup skipped — configure SMTP later.")
            return

        # Save email first (unverified).
        try:
            update_user_email(
                user_id=self._user.id,
                new_email=email,
                acting_user_id=self._user.id,
            )
        except Exception as exc:
            _show_error(self.email_status, f"Error saving email: {exc}")
            return

        # Generate and send code.
        code = generate_security_code()
        try:
            create_security_code(
                user_id=self._user.id,
                purpose="email_verify",
                code=code,
            )
        except Exception as exc:
            _show_error(self.email_status, f"Error creating code: {exc}")
            return

        ok, msg = send_verification_email(email, code)
        if not ok:
            _show_error(self.email_status, f"Failed to send email:\n{msg}")
            return

        self._email_code_sent = True
        self.email_code_input.setVisible(True)
        self.verify_code_btn.setVisible(True)
        _show_success(self.email_status, f"Verification code sent to {email}")

    def _on_verify_email_code(self):
        """Verify the email code entered by the user."""
        _clear_feedback(self.email_status)

        code = self.email_code_input.text().strip()
        if not code:
            _show_error(self.email_status, "Please enter the 6-digit code.")
            self.email_code_input.setFocus()
            return

        try:
            valid = verify_security_code(
                user_id=self._user.id,
                purpose="email_verify",
                code=code,
            )
        except Exception as exc:
            _show_error(self.email_status, f"Error verifying code: {exc}")
            return

        if not valid:
            _show_error(self.email_status, "Invalid or expired code. Try again.")
            self.email_code_input.clear()
            self.email_code_input.setFocus()
            return

        # Mark email as verified.
        try:
            mark_email_verified(self._user.id)
        except Exception as exc:
            _show_error(self.email_status, f"Error: {exc}")
            return

        self._email_verified = True
        _show_success(self.email_status, "Email verified!")
        self.email_code_input.setVisible(False)
        self.verify_code_btn.setVisible(False)
        self.send_code_btn.setVisible(False)
        self.email_input.setEnabled(False)

    # ── Save password ─────────────────────────────────────────────

    def _on_save_clicked(self):
        _clear_feedback(self.feedback)

        new_pw = self.new_password_input.text()
        confirm = self.confirm_input.text()

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
            # Generate and display recovery keys for the user.
            try:
                keys = generate_recovery_keys(user_id=self._user.id, count=5)
                _show_recovery_keys(keys, parent=self)
            except Exception:
                pass  # Non-critical — user can regenerate from account settings.
            self.password_changed.emit(self._user)
        else:
            _show_error(self.feedback, "Failed to update password. Please try again.")

    def reset_fields(self):
        self.new_password_input.clear()
        self.confirm_input.clear()
        self.email_input.clear()
        self.email_input.setEnabled(True)
        self.email_code_input.clear()
        _clear_feedback(self.feedback)
        _clear_feedback(self.email_status)
        self._user = None
        self._email_code_sent = False
        self._email_verified = False
        self.email_input.setVisible(False)
        self.send_code_btn.setVisible(False)
        self.email_code_input.setVisible(False)
        self.verify_code_btn.setVisible(False)
        self.email_status.setVisible(False)


# ═══════════════════════════════════════════════════════════════════
# AUTH WINDOW  (top-level container)
# ═══════════════════════════════════════════════════════════════════

class AuthWindow(QMainWindow):
    """
    Top-level authentication window.

    Page index mapping:
        0 = LoginScreen
        1 = CreateAccountScreen
        2 = ResetPasswordScreen  (multi-step email verification)
        3 = ChangePasswordScreen (temp password + admin email setup)
    """

    login_successful = Signal(object)

    WINDOW_WIDTH = 520
    WINDOW_HEIGHT = 620

    def __init__(self):
        super().__init__()
        self.setWindowTitle("FROG — Authentication")
        self.setFixedSize(self.WINDOW_WIDTH, self.WINDOW_HEIGHT)

        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)

        # Page 0: Login
        self.login_screen = LoginScreen()
        self.stack.addWidget(self.login_screen)

        # Page 1: Create Account
        self.create_screen = CreateAccountScreen()
        self.stack.addWidget(self.create_screen)

        # Page 2: Reset Password (email-based)
        self.reset_screen = ResetPasswordScreen()
        self.stack.addWidget(self.reset_screen)

        # Page 3: Change Password (+ admin email setup)
        self.change_pw_screen = ChangePasswordScreen()
        self.stack.addWidget(self.change_pw_screen)

        # ── Wire inter-screen navigation ──────────────────────────
        self.login_screen.go_create_account.connect(
            lambda: self._switch_to(1, self.create_screen)
        )
        self.login_screen.go_reset_password.connect(
            lambda: self._switch_to(2, self.reset_screen)
        )
        self.create_screen.go_back.connect(
            lambda: self._switch_to(0, self.login_screen)
        )
        self.reset_screen.go_back.connect(
            lambda: self._switch_to(0, self.login_screen)
        )
        self.change_pw_screen.go_back.connect(
            lambda: self._switch_to(0, self.login_screen)
        )

        self.login_screen.login_ok.connect(self._handle_login_result)
        self.change_pw_screen.password_changed.connect(self._handle_password_changed)

        self.stack.setCurrentIndex(0)

    def _switch_to(self, index: int, screen: QWidget):
        if hasattr(screen, "reset_fields"):
            screen.reset_fields()
        self.stack.setCurrentIndex(index)

    def _handle_login_result(self, user, needs_password_change: bool):
        if needs_password_change:
            self.change_pw_screen.set_user(user)
            self.change_pw_screen.reset_fields()
            self.change_pw_screen.set_user(user)
            self.stack.setCurrentIndex(3)
        else:
            self.login_successful.emit(user)

    def _handle_password_changed(self, user):
        self.login_successful.emit(user)

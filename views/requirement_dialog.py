"""
requirement_dialog.py — Add / Edit dialogs for Requirements.

Requirements have a richer set of fields than standard entities:

    • Name              (QLineEdit, required)
    • Body              (QTextEdit, the full requirement statement)
    • Priority          (QComboBox: low / medium / high / critical)
    • Linked To         (M2M panel reused from entity_dialogs.py)
    • Link to test plan (QFileDialog → stores an absolute path;
                         rendered as a clickable link that opens
                         the file with the OS default handler)
    • Ticket link       (QLineEdit → stores a URL; rendered as a
                         clickable link that opens in the browser)
    • Check with AI     (QPushButton, greyed out — future feature)
    • Generate test     (QPushButton, greyed out — future feature)
      template

File path handling
──────────────────
Paths are stored as absolute strings.  The "open" action uses
`QDesktopServices.openUrl(QUrl.fromLocalFile(...))` which delegates
to the OS default handler on all platforms (Windows, macOS, Linux).

URL handling
────────────
`QDesktopServices.openUrl(QUrl(url))` opens the URL in the system's
default web browser on all platforms.
"""

from typing import Optional, List, Dict

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QFont
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from controllers.db_controllers import (
    clear_master_template_path,
    create_entity,
    get_linked_entities,
    get_master_template_path,
    link_entities,
    set_master_template_path,
    unlink_entities,
    update_entity,
)
from controllers.ai_controller import AiWorker
from views.entity_dialogs import LinkedToPanel


# ═══════════════════════════════════════════════════════════════════
# STYLE CONSTANTS
# ═══════════════════════════════════════════════════════════════════

INPUT_STYLE = "padding: 8px; font-size: 14px;"

SAVE_BTN_STYLE = """
    QPushButton {
        background-color: #2ecc71; color: white; border: none;
        border-radius: 6px; padding: 8px 18px;
        font-size: 14px; font-weight: 600;
    }
    QPushButton:hover { background-color: #27ae60; }
    QPushButton:pressed { background-color: #1e8449; }
"""

BROWSE_FILE_BTN_STYLE = """
    QPushButton {
        background-color: #8e44ad; color: white; border: none;
        border-radius: 5px; padding: 6px 14px;
        font-size: 12px; font-weight: 600;
    }
    QPushButton:hover { background-color: #71368a; }
"""

DISABLED_BTN_STYLE = """
    QPushButton {
        background-color: #555; color: #999; border: none;
        border-radius: 5px; padding: 6px 14px;
        font-size: 12px; font-weight: 600;
    }
"""

GEN_TEST_BTN_STYLE = """
    QPushButton {
        background-color: #00b894; color: white; border: none;
        border-radius: 5px; padding: 6px 14px;
        font-size: 12px; font-weight: 600;
    }
    QPushButton:hover { background-color: #00a381; }
    QPushButton:pressed { background-color: #008e6e; }
"""

CHANGE_TEMPLATE_BTN_STYLE = """
    QPushButton {
        background-color: #636e72; color: white; border: none;
        border-radius: 5px; padding: 6px 14px;
        font-size: 12px; font-weight: 600;
    }
    QPushButton:hover { background-color: #535c60; }
    QPushButton:pressed { background-color: #434a4e; }
"""

# Active AI button — vibrant blue-purple to signal it's functional.
AI_BTN_STYLE = """
    QPushButton {
        background-color: #6c5ce7; color: white; border: none;
        border-radius: 5px; padding: 6px 14px;
        font-size: 12px; font-weight: 600;
    }
    QPushButton:hover { background-color: #5a4bd1; }
    QPushButton:pressed { background-color: #4834b5; }
"""

# Style applied while the AI is running inference.
AI_BTN_LOADING_STYLE = """
    QPushButton {
        background-color: #fdcb6e; color: #2d3436; border: none;
        border-radius: 5px; padding: 6px 14px;
        font-size: 12px; font-weight: 600;
    }
"""

LINK_LABEL_STYLE = """
    QLabel {
        color: #3498db;
        font-size: 13px;
        text-decoration: underline;
    }
    QLabel:hover {
        color: #2ecc71;
    }
"""

CLEAR_BTN_STYLE = """
    QPushButton {
        background-color: #e74c3c; color: white; border: none;
        border-radius: 4px; padding: 3px 10px; font-size: 11px;
    }
    QPushButton:hover { background-color: #c0392b; }
"""

ERROR_STYLE = "color: #e74c3c; font-size: 13px; padding: 2px 0;"

PRIORITY_OPTIONS = ["low", "medium", "high", "critical"]

# The four lifecycle statuses a requirement can have.
# "proposed" is the default for newly created requirements.
REQUIREMENT_STATUSES = ["proposed", "under-review", "approved", "tested"]


# ═══════════════════════════════════════════════════════════════════
# HELPER: feedback label
# ═══════════════════════════════════════════════════════════════════

def _make_feedback_label() -> QLabel:
    label = QLabel("")
    label.setAlignment(Qt.AlignCenter)
    label.setWordWrap(True)
    label.setVisible(False)
    return label


def _show_error(label: QLabel, msg: str):
    label.setStyleSheet(ERROR_STYLE)
    label.setText(msg)
    label.setVisible(True)


def _clear_feedback(label: QLabel):
    label.setText("")
    label.setVisible(False)


# ═══════════════════════════════════════════════════════════════════
# REUSABLE: File Path Picker Widget
# ═══════════════════════════════════════════════════════════════════

class FilePathWidget(QWidget):
    """
    Composite widget for the "Link to test plan" field.

    Layout:  [clickable path label]  [Browse]  [Clear]

    • Browse opens a QFileDialog to select any file.
    • The path label is clickable — opens the file with the OS handler.
    • Clear removes the stored path.
    • The stored value is always an absolute path string (or empty).
    """

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._path = ""
        self._build_ui()

    def _build_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        # ── Clickable path label ─────────────────────────────────
        self.path_label = QLabel("No file selected")
        self.path_label.setStyleSheet("color: #888; font-size: 13px;")
        self.path_label.setCursor(Qt.PointingHandCursor)
        self.path_label.setWordWrap(True)
        self.path_label.mousePressEvent = self._on_label_clicked
        layout.addWidget(self.path_label, stretch=1)

        # ── Browse button ────────────────────────────────────────
        self.browse_btn = QPushButton("📂 Browse")
        self.browse_btn.setStyleSheet(BROWSE_FILE_BTN_STYLE)
        self.browse_btn.setCursor(Qt.PointingHandCursor)
        self.browse_btn.clicked.connect(self._on_browse)
        layout.addWidget(self.browse_btn)

        # ── Clear button ─────────────────────────────────────────
        self.clear_btn = QPushButton("✕")
        self.clear_btn.setStyleSheet(CLEAR_BTN_STYLE)
        self.clear_btn.setFixedWidth(28)
        self.clear_btn.setCursor(Qt.PointingHandCursor)
        self.clear_btn.setToolTip("Remove file link")
        self.clear_btn.clicked.connect(self._on_clear)
        self.clear_btn.setVisible(False)
        layout.addWidget(self.clear_btn)

    def set_path(self, path: str):
        """Set the file path programmatically (e.g. loading from DB)."""
        self._path = path.strip() if path else ""
        self._update_display()

    def get_path(self) -> str:
        """Return the current absolute file path, or empty string."""
        return self._path

    def _update_display(self):
        """Update the label text and clear-button visibility."""
        if self._path:
            # Show the filename for brevity; tooltip shows full path.
            import os
            filename = os.path.basename(self._path)
            self.path_label.setText(f"📎 {filename}")
            self.path_label.setToolTip(f"Click to open:\n{self._path}")
            self.path_label.setStyleSheet(LINK_LABEL_STYLE)
            self.clear_btn.setVisible(True)
        else:
            self.path_label.setText("No file selected")
            self.path_label.setToolTip("")
            self.path_label.setStyleSheet("color: #888; font-size: 13px;")
            self.clear_btn.setVisible(False)

    def _on_browse(self):
        """Open a file dialog to pick any file."""
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Test Plan File",
            "",  # start in the current directory
            "All Files (*);;PDF Files (*.pdf);;Word Files (*.docx);;Text Files (*.txt)",
        )
        if path:
            self._path = path
            self._update_display()

    def _on_clear(self):
        """Remove the stored path."""
        self._path = ""
        self._update_display()

    def _on_label_clicked(self, event):
        """Open the file with the OS default handler when the label is clicked."""
        if self._path:
            url = QUrl.fromLocalFile(self._path)
            QDesktopServices.openUrl(url)


# ═══════════════════════════════════════════════════════════════════
# REUSABLE: Ticket Link Widget
# ═══════════════════════════════════════════════════════════════════

class TicketLinkWidget(QWidget):
    """
    Composite widget for the "Ticket link" field.

    Layout:  [URL input field]  [Open ↗]

    • The input stores a URL string (e.g. a Jira ticket).
    • The "Open" button launches it in the default web browser.
    """

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("https://jira.example.com/browse/PROJ-123")
        self.url_input.setStyleSheet(INPUT_STYLE)
        layout.addWidget(self.url_input, stretch=1)

        self.open_btn = QPushButton("Open ↗")
        self.open_btn.setStyleSheet(BROWSE_FILE_BTN_STYLE)
        self.open_btn.setCursor(Qt.PointingHandCursor)
        self.open_btn.setToolTip("Open URL in browser")
        self.open_btn.clicked.connect(self._on_open)
        layout.addWidget(self.open_btn)

    def set_url(self, url: str):
        self.url_input.setText(url or "")

    def get_url(self) -> str:
        return self.url_input.text().strip()

    def _on_open(self):
        """Open the URL in the default web browser."""
        url = self.get_url()
        if not url:
            return
        # Prepend https:// if no scheme is present so the browser doesn't
        # interpret it as a local file path.
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        QDesktopServices.openUrl(QUrl(url))


# ═══════════════════════════════════════════════════════════════════
# ADD REQUIREMENT DIALOG
# ═══════════════════════════════════════════════════════════════════

class AddRequirementDialog(QDialog):
    """
    Dialog for creating a new Requirement with all specialised fields.

    On Save:
      1. Creates the requirement via `create_entity()` with extra_fields
         for priority, body, test_plan_path, ticket_link.
      2. Creates M2M links via `link_entities()`.
    """

    def __init__(
        self,
        *,
        parent_id: int,
        parent_name: str,
        user_id: int,
        project_id: int,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self._parent_id = parent_id
        self._user_id = user_id
        self._project_id = project_id
        self._created_entity = None
        # AI evaluation result — set when the user accepts the AI score.
        self._ai_score = None
        # Reference to the background worker so it isn't garbage-collected.
        self._ai_worker = None

        self.setWindowTitle("Add Requirement")
        self.setMinimumSize(600, 720)
        self._build_ui(parent_name)

    def _build_ui(self, parent_name: str):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(8)

        # ── Heading ──────────────────────────────────────────────
        heading = QLabel("Create New Requirement")
        heading.setAlignment(Qt.AlignCenter)
        hfont = QFont()
        hfont.setPointSize(16)
        hfont.setBold(True)
        heading.setFont(hfont)
        layout.addWidget(heading)

        parent_label = QLabel(f"Parent: {parent_name}")
        parent_label.setStyleSheet("color: #999; font-size: 13px;")
        parent_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(parent_label)

        # ── Requirement ID (user-facing, e.g. REQ-001) ──────────
        layout.addWidget(QLabel("Requirement ID"))
        self.req_id_input = QLineEdit()
        self.req_id_input.setPlaceholderText("e.g. REQ-001, SYS-RF-003")
        self.req_id_input.setStyleSheet(INPUT_STYLE)
        layout.addWidget(self.req_id_input)

        # ── Name ─────────────────────────────────────────────────
        layout.addWidget(QLabel("Name"))
        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("Short descriptive name for this requirement")
        self.name_input.setStyleSheet(INPUT_STYLE)
        layout.addWidget(self.name_input)

        # ── Body (main requirement statement) ────────────────────
        layout.addWidget(QLabel("Body"))
        self.body_input = QTextEdit()
        self.body_input.setPlaceholderText(
            "The system shall..."
        )
        self.body_input.setStyleSheet(INPUT_STYLE)
        self.body_input.setMaximumHeight(100)
        layout.addWidget(self.body_input)

        # ── Status + Priority row ────────────────────────────────
        status_prio_row = QHBoxLayout()

        status_prio_row.addWidget(QLabel("Status"))
        self.status_combo = QComboBox()
        self.status_combo.addItems(REQUIREMENT_STATUSES)
        self.status_combo.setCurrentText("proposed")
        self.status_combo.setStyleSheet("padding: 5px; font-size: 13px;")
        status_prio_row.addWidget(self.status_combo)

        status_prio_row.addSpacing(16)

        status_prio_row.addWidget(QLabel("Priority"))
        self.priority_combo = QComboBox()
        self.priority_combo.addItems(PRIORITY_OPTIONS)
        self.priority_combo.setCurrentText("medium")
        self.priority_combo.setStyleSheet("padding: 5px; font-size: 13px;")
        status_prio_row.addWidget(self.priority_combo)

        status_prio_row.addStretch()

        # ── AI / Test buttons ────────────────────────────────────
        self.ai_btn = QPushButton("🤖 Check with AI")
        self.ai_btn.setStyleSheet(AI_BTN_STYLE)
        self.ai_btn.setCursor(Qt.PointingHandCursor)
        self.ai_btn.setToolTip("Evaluate this requirement against INCOSE best practices")
        self.ai_btn.clicked.connect(self._on_ai_check)
        status_prio_row.addWidget(self.ai_btn)

        test_btn_row = QHBoxLayout()
        test_btn_row.setSpacing(6)

        self.gen_test_btn = QPushButton("🧪 Generate Test Template")
        self.gen_test_btn.setStyleSheet(GEN_TEST_BTN_STYLE)
        self.gen_test_btn.setCursor(Qt.PointingHandCursor)
        self.gen_test_btn.setToolTip("Copy the master template as a test file for this requirement")
        self.gen_test_btn.clicked.connect(self._on_generate_test)
        test_btn_row.addWidget(self.gen_test_btn)

        self.change_template_btn = QPushButton("📝 Change Master Template")
        self.change_template_btn.setStyleSheet(CHANGE_TEMPLATE_BTN_STYLE)
        self.change_template_btn.setCursor(Qt.PointingHandCursor)
        self.change_template_btn.setToolTip("Select a different master test template file for this project")
        self.change_template_btn.clicked.connect(self._on_change_master_template)
        test_btn_row.addWidget(self.change_template_btn)

        status_prio_row.addLayout(test_btn_row)

        layout.addLayout(status_prio_row)

        # ── AI Score display (shown after evaluation) ────────────
        self.ai_score_label = QLabel("")
        self.ai_score_label.setStyleSheet("font-size: 13px; font-weight: bold; padding: 2px 0;")
        self.ai_score_label.setVisible(False)
        layout.addWidget(self.ai_score_label)

        # ── Linked To ────────────────────────────────────────────
        self.links_panel = LinkedToPanel(self_entity_id=None, parent=self)
        layout.addWidget(self.links_panel)

        # ── Link to test plan (file) ─────────────────────────────
        layout.addWidget(QLabel("Link to Test Plan"))
        self.file_widget = FilePathWidget(parent=self)
        layout.addWidget(self.file_widget)

        # ── Ticket link (URL) ────────────────────────────────────
        layout.addWidget(QLabel("Ticket Link"))
        self.ticket_widget = TicketLinkWidget(parent=self)
        layout.addWidget(self.ticket_widget)

        # ── Feedback ─────────────────────────────────────────────
        self.feedback = _make_feedback_label()
        layout.addWidget(self.feedback)

        # ── Buttons ──────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        save_btn = QPushButton("Create Requirement")
        save_btn.setStyleSheet(SAVE_BTN_STYLE)
        save_btn.setCursor(Qt.PointingHandCursor)
        save_btn.clicked.connect(self._on_save)
        btn_row.addWidget(save_btn)
        layout.addLayout(btn_row)

    # ─────────────────────────────────────────────────────────────
    # AI Evaluation (background thread)
    # ─────────────────────────────────────────────────────────────

    def _on_ai_check(self):
        """Launch the AI evaluation in a background thread.

        Disables the button and shows a loading state while the LLM
        runs inference.  The result or error arrives via signals.
        """
        body = self.body_input.toPlainText().strip()
        if not body:
            _show_error(self.feedback, "Write a requirement in the Body field before checking with AI.")
            return

        # ── Show loading state ───────────────────────────────────
        _clear_feedback(self.feedback)
        self.ai_btn.setEnabled(False)
        self.ai_btn.setText("⏳ Evaluating...")
        self.ai_btn.setStyleSheet(AI_BTN_LOADING_STYLE)

        # ── Launch background worker ─────────────────────────────
        self._ai_worker = AiWorker(body)
        self._ai_worker.finished_signal.connect(self._on_ai_result)
        self._ai_worker.error_signal.connect(self._on_ai_error)
        self._ai_worker.start()

    def _on_ai_result(self, score: str, critique: str):
        """Called on the main thread when AI inference completes.

        Shows a popup with the score and critique.  If the user clicks
        "Accept", the score is stored in self._ai_score and will be
        saved to the database when the user clicks Save.
        """
        # ── Restore button ───────────────────────────────────────
        self.ai_btn.setEnabled(True)
        self.ai_btn.setText("🤖 Check with AI")
        self.ai_btn.setStyleSheet(AI_BTN_STYLE)

        # ── Show result popup ────────────────────────────────────
        msg = QMessageBox(self)
        msg.setWindowTitle("AI Requirement Evaluation")
        msg.setIcon(QMessageBox.Information)
        msg.setText(f"<b>Score: {score}</b>")
        msg.setInformativeText(f"<b>Critique:</b><br>{critique}")
        msg.setStandardButtons(QMessageBox.Ok | QMessageBox.Discard)
        msg.button(QMessageBox.Ok).setText("Accept Score")
        msg.button(QMessageBox.Discard).setText("Dismiss")

        result = msg.exec()
        if result == QMessageBox.Ok:
            # Store the score — it will be saved to DB on Save.
            self._ai_score = score
            self.ai_score_label.setText(f"🤖 AI Score: {score}")
            self.ai_score_label.setStyleSheet(
                "font-size: 13px; font-weight: bold; color: #6c5ce7; padding: 2px 0;"
            )
            self.ai_score_label.setVisible(True)

    def _on_ai_error(self, error_msg: str):
        """Called on the main thread if AI inference fails."""
        self.ai_btn.setEnabled(True)
        self.ai_btn.setText("🤖 Check with AI")
        self.ai_btn.setStyleSheet(AI_BTN_STYLE)
        QMessageBox.warning(self, "AI Evaluation Error", error_msg)

    # ─────────────────────────────────────────────────────────────
    # Generate Test Template / Change Master Template
    # ─────────────────────────────────────────────────────────────

    def _on_generate_test(self):
        """Copy the master template to a user-chosen location."""
        import os
        import shutil

        req_id = self.req_id_input.text().strip()
        if not req_id:
            QMessageBox.warning(
                self, "Missing Requirement ID",
                "Please enter a Requirement ID before generating a test template."
            )
            return

        # ── Ensure a master template is set ──────────────────────
        master_path = get_master_template_path(self._project_id)
        if not master_path:
            master_path = self._prompt_for_master_template()
            if not master_path:
                return  # user cancelled

        # ── Build default save filename ──────────────────────────
        _, ext = os.path.splitext(master_path)
        default_name = f"{req_id}_TEST{ext}"

        save_path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Test File",
            default_name,
            "All Files (*)",
        )
        if not save_path:
            return  # user cancelled

        # ── Copy the master template ─────────────────────────────
        try:
            shutil.copy2(master_path, save_path)
            QMessageBox.information(
                self, "Test Template Generated",
                f"Test file saved to:\n{save_path}"
            )
        except FileNotFoundError:
            QMessageBox.warning(
                self, "Master Template Missing",
                f"The master template file could not be found:\n\n"
                f"{master_path}\n\n"
                "It may have been moved or deleted. "
                "Please select a new master template."
            )
            clear_master_template_path(
                project_id=self._project_id, user_id=self._user_id
            )
            new_path = self._prompt_for_master_template()
            if new_path:
                self._on_generate_test()  # retry with the new template
        except Exception as exc:
            QMessageBox.warning(
                self, "Copy Failed",
                f"Failed to copy the template:\n\n{exc}"
            )

    def _on_change_master_template(self):
        """Let the user pick a new master template and save it to the project."""
        self._prompt_for_master_template()

    def _prompt_for_master_template(self) -> str:
        """Open a file dialog to select a master template; saves to DB.
        Returns the selected path, or empty string if cancelled."""
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Master Test Template",
            "",
            "All Files (*);;Word Files (*.docx);;PDF Files (*.pdf);;Text Files (*.txt)",
        )
        if path:
            set_master_template_path(
                project_id=self._project_id,
                path=path,
                user_id=self._user_id,
            )
        return path

    # ─────────────────────────────────────────────────────────────
    # Save
    # ─────────────────────────────────────────────────────────────

    def _on_save(self):
        _clear_feedback(self.feedback)
        name = self.name_input.text().strip()

        if not name:
            _show_error(self.feedback, "Name is required.")
            self.name_input.setFocus()
            return

        req_id = self.req_id_input.text().strip() or None
        body = self.body_input.toPlainText().strip() or None
        status = self.status_combo.currentText()
        priority = self.priority_combo.currentText()
        test_plan_path = self.file_widget.get_path() or None
        ticket_link = self.ticket_widget.get_url() or None

        # ── Step 1: Create the requirement ───────────────────────
        try:
            entity = create_entity(
                entity_type="requirement",
                name=name,
                user_id=self._user_id,
                parent_id=self._parent_id,
                description=body,
                status=status,
                extra_fields={
                    "req_id": req_id,
                    "priority": priority,
                    "body": body,
                    "test_plan_path": test_plan_path,
                    "ticket_link": ticket_link,
                    "ai_score": self._ai_score,
                },
            )
        except Exception as exc:
            _show_error(self.feedback, f"Failed to create requirement: {exc}")
            return

        # ── Step 2: Create M2M links ─────────────────────────────
        link_errors = []
        for target_id in self.links_panel.get_current_ids():
            try:
                link_entities(
                    source_id=entity.id,
                    target_id=target_id,
                    user_id=self._user_id,
                )
            except Exception as exc:
                link_errors.append(f"→ id {target_id}: {exc}")

        if link_errors:
            QMessageBox.warning(
                self, "Partial Link Errors",
                f"Requirement created, but some links failed:\n\n"
                + "\n".join(link_errors),
            )

        self._created_entity = entity
        self.accept()

    def get_created_entity(self):
        return self._created_entity


# ═══════════════════════════════════════════════════════════════════
# EDIT REQUIREMENT DIALOG
# ═══════════════════════════════════════════════════════════════════

class EditRequirementDialog(QDialog):
    """
    Dialog for editing an existing Requirement.

    Pre-populates all fields from the entity.  On Save:
      1. Updates changed fields via `update_entity()`.
      2. Diffs M2M links and applies adds/removes.
    """

    def __init__(
        self,
        *,
        entity,
        user_id: int,
        project_id: int,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self._entity = entity
        self._user_id = user_id
        self._project_id = project_id
        self._saved = False
        # AI evaluation result — None means unchanged from DB value.
        self._ai_score = entity.ai_score if hasattr(entity, "ai_score") else None
        self._ai_worker = None

        self.setWindowTitle(f"Edit Requirement — {entity.name}")
        self.setMinimumSize(600, 720)
        self._build_ui()
        self._load_existing_links()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(8)

        # ── Heading ──────────────────────────────────────────────
        heading = QLabel("Edit Requirement")
        heading.setAlignment(Qt.AlignCenter)
        hfont = QFont()
        hfont.setPointSize(16)
        hfont.setBold(True)
        heading.setFont(hfont)
        layout.addWidget(heading)

        db_id_label = QLabel(f"Database ID: {self._entity.id}")
        db_id_label.setStyleSheet("color: #999; font-size: 13px;")
        db_id_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(db_id_label)

        # ── Requirement ID (user-facing) ─────────────────────────
        layout.addWidget(QLabel("Requirement ID"))
        self.req_id_input = QLineEdit(self._entity.req_id or "")
        self.req_id_input.setPlaceholderText("e.g. REQ-001, SYS-RF-003")
        self.req_id_input.setStyleSheet(INPUT_STYLE)
        layout.addWidget(self.req_id_input)

        # ── Name ─────────────────────────────────────────────────
        layout.addWidget(QLabel("Name"))
        self.name_input = QLineEdit(self._entity.name)
        self.name_input.setStyleSheet(INPUT_STYLE)
        layout.addWidget(self.name_input)

        # ── Body ─────────────────────────────────────────────────
        layout.addWidget(QLabel("Body"))
        self.body_input = QTextEdit()
        self.body_input.setPlainText(self._entity.body or "")
        self.body_input.setStyleSheet(INPUT_STYLE)
        self.body_input.setMaximumHeight(100)
        layout.addWidget(self.body_input)

        # ── Status + Priority + AI buttons ───────────────────────
        status_prio_row = QHBoxLayout()

        status_prio_row.addWidget(QLabel("Status"))
        self.status_combo = QComboBox()
        self.status_combo.addItems(REQUIREMENT_STATUSES)
        # Pre-select the current status; fall back to "proposed" if the
        # stored value doesn't match the new options (e.g. old "draft").
        current_status = self._entity.status or "proposed"
        if current_status in REQUIREMENT_STATUSES:
            self.status_combo.setCurrentText(current_status)
        else:
            self.status_combo.setCurrentText("proposed")
        self.status_combo.setStyleSheet("padding: 5px; font-size: 13px;")
        status_prio_row.addWidget(self.status_combo)

        status_prio_row.addSpacing(16)

        status_prio_row.addWidget(QLabel("Priority"))
        self.priority_combo = QComboBox()
        self.priority_combo.addItems(PRIORITY_OPTIONS)
        current_prio = self._entity.priority or "medium"
        if current_prio in PRIORITY_OPTIONS:
            self.priority_combo.setCurrentText(current_prio)
        self.priority_combo.setStyleSheet("padding: 5px; font-size: 13px;")
        status_prio_row.addWidget(self.priority_combo)

        status_prio_row.addStretch()

        self.ai_btn = QPushButton("🤖 Check with AI")
        self.ai_btn.setStyleSheet(AI_BTN_STYLE)
        self.ai_btn.setCursor(Qt.PointingHandCursor)
        self.ai_btn.setToolTip("Evaluate this requirement against INCOSE best practices")
        self.ai_btn.clicked.connect(self._on_ai_check)
        status_prio_row.addWidget(self.ai_btn)

        test_btn_row = QHBoxLayout()
        test_btn_row.setSpacing(6)

        self.gen_test_btn = QPushButton("🧪 Generate Test Template")
        self.gen_test_btn.setStyleSheet(GEN_TEST_BTN_STYLE)
        self.gen_test_btn.setCursor(Qt.PointingHandCursor)
        self.gen_test_btn.setToolTip("Copy the master template as a test file for this requirement")
        self.gen_test_btn.clicked.connect(self._on_generate_test)
        test_btn_row.addWidget(self.gen_test_btn)

        self.change_template_btn = QPushButton("📝 Change Master Template")
        self.change_template_btn.setStyleSheet(CHANGE_TEMPLATE_BTN_STYLE)
        self.change_template_btn.setCursor(Qt.PointingHandCursor)
        self.change_template_btn.setToolTip("Select a different master test template file for this project")
        self.change_template_btn.clicked.connect(self._on_change_master_template)
        test_btn_row.addWidget(self.change_template_btn)

        status_prio_row.addLayout(test_btn_row)
        layout.addLayout(status_prio_row)

        # ── AI Score display (shows current or newly evaluated score) ─
        self.ai_score_label = QLabel("")
        self.ai_score_label.setStyleSheet(
            "font-size: 13px; font-weight: bold; color: #6c5ce7; padding: 2px 0;"
        )
        # If the entity already has a saved AI score, show it.
        if self._ai_score:
            self.ai_score_label.setText(f"🤖 AI Score: {self._ai_score}")
            self.ai_score_label.setVisible(True)
        else:
            self.ai_score_label.setVisible(False)
        layout.addWidget(self.ai_score_label)

        # ── Linked To ────────────────────────────────────────────
        self.links_panel = LinkedToPanel(
            self_entity_id=self._entity.id, parent=self
        )
        layout.addWidget(self.links_panel)

        # ── Link to test plan ────────────────────────────────────
        layout.addWidget(QLabel("Link to Test Plan"))
        self.file_widget = FilePathWidget(parent=self)
        self.file_widget.set_path(self._entity.test_plan_path or "")
        layout.addWidget(self.file_widget)

        # ── Ticket link ──────────────────────────────────────────
        layout.addWidget(QLabel("Ticket Link"))
        self.ticket_widget = TicketLinkWidget(parent=self)
        self.ticket_widget.set_url(self._entity.ticket_link or "")
        layout.addWidget(self.ticket_widget)

        # ── Feedback ─────────────────────────────────────────────
        self.feedback = _make_feedback_label()
        layout.addWidget(self.feedback)

        # ── Buttons ──────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        save_btn = QPushButton("Save Changes")
        save_btn.setStyleSheet(SAVE_BTN_STYLE)
        save_btn.setCursor(Qt.PointingHandCursor)
        save_btn.clicked.connect(self._on_save)
        btn_row.addWidget(save_btn)
        layout.addLayout(btn_row)

    def _load_existing_links(self):
        try:
            linked = get_linked_entities(self._entity.id, direction="outgoing")
        except Exception:
            linked = []
        self.links_panel.set_links(linked)

    # ─────────────────────────────────────────────────────────────
    # AI Evaluation (background thread)
    # ─────────────────────────────────────────────────────────────

    def _on_ai_check(self):
        """Launch the AI evaluation in a background thread."""
        body = self.body_input.toPlainText().strip()
        if not body:
            _show_error(self.feedback, "Write a requirement in the Body field before checking with AI.")
            return

        _clear_feedback(self.feedback)
        self.ai_btn.setEnabled(False)
        self.ai_btn.setText("⏳ Evaluating...")
        self.ai_btn.setStyleSheet(AI_BTN_LOADING_STYLE)

        self._ai_worker = AiWorker(body)
        self._ai_worker.finished_signal.connect(self._on_ai_result)
        self._ai_worker.error_signal.connect(self._on_ai_error)
        self._ai_worker.start()

    def _on_ai_result(self, score: str, critique: str):
        """Called on the main thread when AI inference completes."""
        self.ai_btn.setEnabled(True)
        self.ai_btn.setText("🤖 Check with AI")
        self.ai_btn.setStyleSheet(AI_BTN_STYLE)

        msg = QMessageBox(self)
        msg.setWindowTitle("AI Requirement Evaluation")
        msg.setIcon(QMessageBox.Information)
        msg.setText(f"<b>Score: {score}</b>")
        msg.setInformativeText(f"<b>Critique:</b><br>{critique}")
        msg.setStandardButtons(QMessageBox.Ok | QMessageBox.Discard)
        msg.button(QMessageBox.Ok).setText("Accept Score")
        msg.button(QMessageBox.Discard).setText("Dismiss")

        result = msg.exec()
        if result == QMessageBox.Ok:
            self._ai_score = score
            self.ai_score_label.setText(f"🤖 AI Score: {score}")
            self.ai_score_label.setStyleSheet(
                "font-size: 13px; font-weight: bold; color: #6c5ce7; padding: 2px 0;"
            )
            self.ai_score_label.setVisible(True)

    def _on_ai_error(self, error_msg: str):
        """Called on the main thread if AI inference fails."""
        self.ai_btn.setEnabled(True)
        self.ai_btn.setText("🤖 Check with AI")
        self.ai_btn.setStyleSheet(AI_BTN_STYLE)
        QMessageBox.warning(self, "AI Evaluation Error", error_msg)

    # ─────────────────────────────────────────────────────────────
    # Generate Test Template / Change Master Template
    # ─────────────────────────────────────────────────────────────

    def _on_generate_test(self):
        """Copy the master template to a user-chosen location."""
        import os
        import shutil

        req_id = self.req_id_input.text().strip()
        if not req_id:
            QMessageBox.warning(
                self, "Missing Requirement ID",
                "Please enter a Requirement ID before generating a test template."
            )
            return

        # ── Ensure a master template is set ──────────────────────
        master_path = get_master_template_path(self._project_id)
        if not master_path:
            master_path = self._prompt_for_master_template()
            if not master_path:
                return

        # ── Build default save filename ──────────────────────────
        _, ext = os.path.splitext(master_path)
        default_name = f"{req_id}_TEST{ext}"

        save_path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Test File",
            default_name,
            "All Files (*)",
        )
        if not save_path:
            return

        # ── Copy the master template ─────────────────────────────
        try:
            shutil.copy2(master_path, save_path)
            QMessageBox.information(
                self, "Test Template Generated",
                f"Test file saved to:\n{save_path}"
            )
        except FileNotFoundError:
            QMessageBox.warning(
                self, "Master Template Missing",
                f"The master template file could not be found:\n\n"
                f"{master_path}\n\n"
                "It may have been moved or deleted. "
                "Please select a new master template."
            )
            clear_master_template_path(
                project_id=self._project_id, user_id=self._user_id
            )
            new_path = self._prompt_for_master_template()
            if new_path:
                self._on_generate_test()
        except Exception as exc:
            QMessageBox.warning(
                self, "Copy Failed",
                f"Failed to copy the template:\n\n{exc}"
            )

    def _on_change_master_template(self):
        """Let the user pick a new master template and save it to the project."""
        self._prompt_for_master_template()

    def _prompt_for_master_template(self) -> str:
        """Open a file dialog to select a master template; saves to DB."""
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Master Test Template",
            "",
            "All Files (*);;Word Files (*.docx);;PDF Files (*.pdf);;Text Files (*.txt)",
        )
        if path:
            set_master_template_path(
                project_id=self._project_id,
                path=path,
                user_id=self._user_id,
            )
        return path

    # ─────────────────────────────────────────────────────────────
    # Save
    # ─────────────────────────────────────────────────────────────

    def _on_save(self):
        _clear_feedback(self.feedback)
        name = self.name_input.text().strip()

        if not name:
            _show_error(self.feedback, "Name is required.")
            self.name_input.setFocus()
            return

        req_id = self.req_id_input.text().strip() or None
        body = self.body_input.toPlainText().strip() or None
        status = self.status_combo.currentText()
        priority = self.priority_combo.currentText()
        test_plan_path = self.file_widget.get_path() or None
        ticket_link = self.ticket_widget.get_url() or None

        # ── Step 1: Build updates dict (only changed fields) ─────
        updates = {}
        if name != self._entity.name:
            updates["name"] = name
        if req_id != (self._entity.req_id or None):
            updates["req_id"] = req_id
        if body != (self._entity.body or None):
            updates["body"] = body
        # Also keep description in sync with body for consistency.
        if body != (self._entity.description or None):
            updates["description"] = body
        if status != (self._entity.status or "proposed"):
            updates["status"] = status
        if priority != (self._entity.priority or "medium"):
            updates["priority"] = priority
        if test_plan_path != (self._entity.test_plan_path or None):
            updates["test_plan_path"] = test_plan_path
        if ticket_link != (self._entity.ticket_link or None):
            updates["ticket_link"] = ticket_link
        # Save the AI score if it was updated (either newly evaluated
        # or re-evaluated — compare against the DB value).
        old_ai_score = self._entity.ai_score if hasattr(self._entity, "ai_score") else None
        if self._ai_score != (old_ai_score or None):
            updates["ai_score"] = self._ai_score

        if updates:
            try:
                updated = update_entity(
                    entity_id=self._entity.id,
                    user_id=self._user_id,
                    updates=updates,
                )
                if updated is None:
                    _show_error(self.feedback, "Entity not found — may have been deleted.")
                    return
                self._entity = updated
            except Exception as exc:
                _show_error(self.feedback, f"Save failed: {exc}")
                return

        # ── Step 2: Diff and apply link changes ──────────────────
        added_ids = self.links_panel.get_added_ids()
        removed_ids = self.links_panel.get_removed_ids()
        link_errors = []

        for target_id in added_ids:
            try:
                link_entities(
                    source_id=self._entity.id,
                    target_id=target_id,
                    user_id=self._user_id,
                )
            except Exception as exc:
                link_errors.append(f"Link → id {target_id}: {exc}")

        for target_id in removed_ids:
            try:
                unlink_entities(
                    source_id=self._entity.id,
                    target_id=target_id,
                    user_id=self._user_id,
                )
            except Exception as exc:
                link_errors.append(f"Unlink → id {target_id}: {exc}")

        if link_errors:
            QMessageBox.warning(
                self, "Partial Link Errors",
                "Fields saved, but some link changes failed:\n\n"
                + "\n".join(link_errors),
            )

        self._saved = True
        self.accept()

    def was_saved(self) -> bool:
        return self._saved

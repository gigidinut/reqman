"""
rich_text_editor.py — Reusable rich-text editor widget with formatting toolbar.

Provides a QTextEdit with a compact toolbar for:
  - Bold, Italic, Underline
  - Bulleted and Numbered Lists
  - Insert Image (copies to project_media/ with UUID filename)

Image storage
-------------
Images are copied into `<app_root>/project_media/` with UUID-based filenames
to avoid collisions.  The HTML stored in the database references images via
a special placeholder `%%PROJECT_MEDIA%%/filename.ext` which is resolved to
an absolute path at load time.  This keeps the database portable even if the
project folder is relocated.

Usage
-----
    from views.rich_text_editor import RichTextEditor

    editor = RichTextEditor(parent=self)
    editor.set_html(entity.description or "")
    html = editor.get_html()          # ready for DB storage
    plain = editor.get_plain_text()   # fallback if needed
"""

import os
import shutil
import uuid
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import (
    QFont,
    QIcon,
    QImage,
    QKeySequence,
    QTextCharFormat,
    QTextCursor,
    QTextListFormat,
)
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


# ═══════════════════════════════════════════════════════════════════
# CONSTANTS
# ═══════════════════════════════════════════════════════════════════

# Portable placeholder embedded in stored HTML instead of absolute paths.
_MEDIA_PLACEHOLDER = "%%PROJECT_MEDIA%%"

# Resolve the project_media directory relative to the app root.
# This file lives at <root>/reqman/views/rich_text_editor.py
# App root is two levels up → <root>/reqman
_APP_ROOT = Path(__file__).resolve().parent.parent
MEDIA_DIR = _APP_ROOT / "project_media"

# ── Toolbar button style ─────────────────────────────────────────
_TB_BTN_STYLE = """
    QPushButton {
        border: 1px solid #555;
        border-radius: 4px;
        padding: 4px 8px;
        font-size: 13px;
        font-weight: 600;
        min-width: 28px;
    }
    QPushButton:hover {
        background-color: rgba(255, 255, 255, 0.08);
    }
    QPushButton:pressed {
        background-color: rgba(255, 255, 255, 0.15);
    }
    QPushButton:checked {
        background-color: rgba(100, 100, 255, 0.25);
        border-color: #6c5ce7;
    }
"""

_IMG_BTN_STYLE = """
    QPushButton {
        border: 1px solid #555;
        border-radius: 4px;
        padding: 4px 10px;
        font-size: 12px;
        font-weight: 600;
    }
    QPushButton:hover {
        background-color: rgba(255, 255, 255, 0.08);
    }
    QPushButton:pressed {
        background-color: rgba(255, 255, 255, 0.15);
    }
"""


# ═══════════════════════════════════════════════════════════════════
# PATH HELPERS  (portable image references)
# ═══════════════════════════════════════════════════════════════════

def _abs_media_path() -> str:
    """Return the absolute path to project_media/ as a string with forward slashes."""
    return str(MEDIA_DIR).replace("\\", "/")


def _html_to_storage(html: str) -> str:
    """Replace absolute project_media paths with the portable placeholder."""
    return html.replace(_abs_media_path(), _MEDIA_PLACEHOLDER)


def _html_from_storage(html: str) -> str:
    """Replace the portable placeholder with the current absolute path."""
    return html.replace(_MEDIA_PLACEHOLDER, _abs_media_path())


# ═══════════════════════════════════════════════════════════════════
# RICH TEXT EDITOR WIDGET
# ═══════════════════════════════════════════════════════════════════

class RichTextEditor(QWidget):
    """
    Composite widget: formatting toolbar + QTextEdit.

    The toolbar provides Bold, Italic, Underline, Bulleted List,
    Numbered List, and Insert Image.

    The editor stores/loads HTML with portable image path placeholders.
    """

    def __init__(
        self,
        *,
        placeholder: str = "",
        max_height: int = 0,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self._placeholder = placeholder
        self._max_height = max_height
        self._build_ui()

    # ─────────────────────────────────────────────────────────────
    # UI Construction
    # ─────────────────────────────────────────────────────────────

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        # ── Toolbar ──────────────────────────────────────────────
        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(0, 0, 0, 0)
        toolbar.setSpacing(4)

        self.bold_btn = self._make_toggle_btn("B", "Bold (Ctrl+B)")
        bold_font = self.bold_btn.font()
        bold_font.setBold(True)
        self.bold_btn.setFont(bold_font)
        self.bold_btn.clicked.connect(self._toggle_bold)
        toolbar.addWidget(self.bold_btn)

        self.italic_btn = self._make_toggle_btn("I", "Italic (Ctrl+I)")
        italic_font = self.italic_btn.font()
        italic_font.setItalic(True)
        self.italic_btn.setFont(italic_font)
        self.italic_btn.clicked.connect(self._toggle_italic)
        toolbar.addWidget(self.italic_btn)

        self.underline_btn = self._make_toggle_btn("U", "Underline (Ctrl+U)")
        underline_font = self.underline_btn.font()
        underline_font.setUnderline(True)
        self.underline_btn.setFont(underline_font)
        self.underline_btn.clicked.connect(self._toggle_underline)
        toolbar.addWidget(self.underline_btn)

        # Separator
        toolbar.addSpacing(8)

        self.bullet_btn = self._make_toggle_btn("  \u2022  ", "Bulleted List")
        self.bullet_btn.clicked.connect(self._toggle_bullet_list)
        toolbar.addWidget(self.bullet_btn)

        self.number_btn = self._make_toggle_btn(" 1. ", "Numbered List")
        self.number_btn.clicked.connect(self._toggle_number_list)
        toolbar.addWidget(self.number_btn)

        # Separator
        toolbar.addSpacing(8)

        self.image_btn = QPushButton("Insert Image")
        self.image_btn.setStyleSheet(_IMG_BTN_STYLE)
        self.image_btn.setCursor(Qt.PointingHandCursor)
        self.image_btn.setToolTip("Insert an image from file")
        self.image_btn.clicked.connect(self._insert_image)
        toolbar.addWidget(self.image_btn)

        toolbar.addStretch()
        layout.addLayout(toolbar)

        # ── Text Editor ──────────────────────────────────────────
        self.editor = QTextEdit()
        self.editor.setAcceptRichText(True)
        if self._placeholder:
            self.editor.setPlaceholderText(self._placeholder)
        if self._max_height > 0:
            self.editor.setMaximumHeight(self._max_height)
        self.editor.setStyleSheet("padding: 8px; font-size: 14px;")

        # Track cursor position to update toggle states.
        self.editor.cursorPositionChanged.connect(self._update_toolbar_state)

        layout.addWidget(self.editor)

    def _make_toggle_btn(self, text: str, tooltip: str) -> QPushButton:
        """Create a checkable toolbar button."""
        btn = QPushButton(text)
        btn.setCheckable(True)
        btn.setStyleSheet(_TB_BTN_STYLE)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setToolTip(tooltip)
        return btn

    # ─────────────────────────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────────────────────────

    def set_html(self, html: str):
        """Load HTML content (from DB) into the editor, resolving image paths."""
        if html and "<" in html:
            self.editor.setHtml(_html_from_storage(html))
        else:
            # Plain text or empty — just set it directly.
            self.editor.setPlainText(html or "")

    def get_html(self) -> str:
        """Return the editor content as HTML with portable image paths.
        Returns empty string if the editor is effectively empty."""
        plain = self.editor.toPlainText().strip()
        if not plain:
            return ""
        html = self.editor.toHtml()
        return _html_to_storage(html)

    def get_plain_text(self) -> str:
        """Return the editor content as plain text (no formatting)."""
        return self.editor.toPlainText().strip()

    # ─────────────────────────────────────────────────────────────
    # Formatting Actions
    # ─────────────────────────────────────────────────────────────

    def _toggle_bold(self):
        fmt = QTextCharFormat()
        cursor = self.editor.textCursor()
        current_weight = cursor.charFormat().fontWeight()
        new_weight = QFont.Weight.Normal if current_weight == QFont.Weight.Bold else QFont.Weight.Bold
        fmt.setFontWeight(new_weight)
        cursor.mergeCharFormat(fmt)
        self.editor.setFocus()

    def _toggle_italic(self):
        fmt = QTextCharFormat()
        cursor = self.editor.textCursor()
        fmt.setFontItalic(not cursor.charFormat().fontItalic())
        cursor.mergeCharFormat(fmt)
        self.editor.setFocus()

    def _toggle_underline(self):
        fmt = QTextCharFormat()
        cursor = self.editor.textCursor()
        fmt.setFontUnderline(not cursor.charFormat().fontUnderline())
        cursor.mergeCharFormat(fmt)
        self.editor.setFocus()

    def _toggle_bullet_list(self):
        self._toggle_list(QTextListFormat.Style.ListDisc)

    def _toggle_number_list(self):
        self._toggle_list(QTextListFormat.Style.ListDecimal)

    def _toggle_list(self, style: QTextListFormat.Style):
        """Toggle a list format on the current block(s)."""
        cursor = self.editor.textCursor()
        current_list = cursor.currentList()

        if current_list and current_list.format().style() == style:
            # Remove from list — unindent the block.
            block_fmt = cursor.blockFormat()
            block_fmt.setIndent(0)
            cursor.setBlockFormat(block_fmt)
            # Remove the block from the list.
            current_list.remove(cursor.block())
        else:
            # Create or switch to this list style.
            list_fmt = QTextListFormat()
            list_fmt.setStyle(style)
            list_fmt.setIndent(1)
            cursor.createList(list_fmt)

        self.editor.setFocus()
        self._update_toolbar_state()

    # ─────────────────────────────────────────────────────────────
    # Image Insertion
    # ─────────────────────────────────────────────────────────────

    def _insert_image(self):
        """Open a file dialog to pick an image, copy it to project_media/,
        and insert an <img> tag at the cursor."""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Image",
            "",
            "Images (*.png *.jpg *.jpeg *.gif *.bmp *.svg *.webp);;All Files (*)",
        )
        if not file_path:
            return

        # Ensure the media directory exists.
        MEDIA_DIR.mkdir(parents=True, exist_ok=True)

        # Copy with a UUID-based filename to prevent collisions.
        ext = os.path.splitext(file_path)[1].lower()
        unique_name = f"{uuid.uuid4().hex}{ext}"
        dest_path = MEDIA_DIR / unique_name

        try:
            shutil.copy2(file_path, dest_path)
        except Exception as exc:
            QMessageBox.warning(
                self, "Image Copy Failed",
                f"Could not copy image to project_media:\n\n{exc}"
            )
            return

        # Insert the image at cursor using the absolute path (will be
        # converted to portable placeholder on save via get_html()).
        abs_path = str(dest_path).replace("\\", "/")
        cursor = self.editor.textCursor()
        cursor.insertHtml(
            f'<img src="file:///{abs_path}" width="300" />'
        )
        self.editor.setFocus()

    # ─────────────────────────────────────────────────────────────
    # Toolbar State Sync
    # ─────────────────────────────────────────────────────────────

    def _update_toolbar_state(self):
        """Sync the toggle buttons with the format at the current cursor."""
        cursor = self.editor.textCursor()
        char_fmt = cursor.charFormat()

        # Block signals to avoid recursive updates.
        self.bold_btn.blockSignals(True)
        self.bold_btn.setChecked(char_fmt.fontWeight() == QFont.Weight.Bold)
        self.bold_btn.blockSignals(False)

        self.italic_btn.blockSignals(True)
        self.italic_btn.setChecked(char_fmt.fontItalic())
        self.italic_btn.blockSignals(False)

        self.underline_btn.blockSignals(True)
        self.underline_btn.setChecked(char_fmt.fontUnderline())
        self.underline_btn.blockSignals(False)

        # List state.
        current_list = cursor.currentList()
        is_bullet = False
        is_number = False
        if current_list:
            style = current_list.format().style()
            is_bullet = style == QTextListFormat.Style.ListDisc
            is_number = style == QTextListFormat.Style.ListDecimal

        self.bullet_btn.blockSignals(True)
        self.bullet_btn.setChecked(is_bullet)
        self.bullet_btn.blockSignals(False)

        self.number_btn.blockSignals(True)
        self.number_btn.setChecked(is_number)
        self.number_btn.blockSignals(False)

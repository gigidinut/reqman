"""
entity_dialogs.py — Add / Edit dialogs for Systems, Sub-systems, and Elements.

These dialogs share a common form layout:
    • Name            (QLineEdit, required)
    • Description     (QTextEdit, optional)
    • Linked To       (QListWidget showing current links + Browse button)

The "Linked To" section implements the Many-to-Many relationship:
    • Each linked entity appears as a row with its type icon and a Remove button.
    • The "Browse / Add Link" button opens a LinkBrowserDialog which lets the
      user search the entire database and select multiple entities to link.
    • Links are persisted via `link_entities()` / `unlink_entities()` only on
      Save — the dialog accumulates adds/removes in memory and applies them
      in a single batch so a Cancel truly discards everything.

Dialog modes
────────────
AddEntityDialog   — creates a new entity via `create_entity()`, then links.
EditEntityDialog  — updates an existing entity via `update_entity()`, diffs
                    the link set, and applies only the link changes.

Both pass the authenticated user's ID through to every controller call so
the audit log always records who performed the action.
"""

from typing import Optional, List, Set, Dict

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QColor
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from controllers.db_controllers import (
    create_entity,
    get_entity,
    get_linked_entities,
    link_entities,
    search_entities,
    unlink_entities,
    update_entity,
)
from views.rich_text_editor import RichTextEditor


# ═══════════════════════════════════════════════════════════════════
# STYLE CONSTANTS
# ═══════════════════════════════════════════════════════════════════

INPUT_STYLE = "padding: 8px; font-size: 14px;"

PRIMARY_BTN_STYLE = """
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

SAVE_BTN_STYLE = """
    QPushButton {
        background-color: #2ecc71;
        color: white;
        border: none;
        border-radius: 6px;
        padding: 8px 18px;
        font-size: 14px;
        font-weight: 600;
    }
    QPushButton:hover { background-color: #27ae60; }
    QPushButton:pressed { background-color: #1e8449; }
"""

REMOVE_BTN_STYLE = """
    QPushButton {
        background-color: #e74c3c;
        color: white;
        border: none;
        border-radius: 4px;
        padding: 3px 10px;
        font-size: 11px;
    }
    QPushButton:hover { background-color: #c0392b; }
"""

ERROR_STYLE = "color: #e74c3c; font-size: 13px; padding: 2px 0;"

# Human-readable labels and icons for entity types (mirrors project_view).
ENTITY_DISPLAY: Dict[str, Dict[str, str]] = {
    "project":     {"icon": "📁", "label": "Project"},
    "system":      {"icon": "⚙️", "label": "System"},
    "subsystem":   {"icon": "🔧", "label": "Sub-system"},
    "element":     {"icon": "📦", "label": "Element"},
    "requirement": {"icon": "📋", "label": "Requirement"},
}


# ═══════════════════════════════════════════════════════════════════
# HELPER: feedback label (shared pattern)
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
# LINK BROWSER DIALOG  (mini-search to pick entities for M2M links)
# ═══════════════════════════════════════════════════════════════════

class LinkBrowserDialog(QDialog):
    """
    Modal dialog for searching the database and selecting entities
    to link to the entity being created or edited.

    Layout
    ──────
    ┌────────────────────────────────────────┐
    │  Search: [___________________________] │
    │                                        │
    │  ⚙️  Transponder       (system)        │
    │  🔧  RF Front-End      (subsystem)  ☑  │
    │  📦  LNA Module         (element)   ☑  │
    │  📋  REQ-001            (requirement)   │
    │                                        │
    │              [Cancel]  [Add Selected]   │
    └────────────────────────────────────────┘

    The search results list supports multi-selection.  Results exclude
    the entity being edited (to prevent self-links) and any entities
    already in the "Linked To" list (to prevent duplicates).

    Returns the list of selected Entity objects via `get_selected()`.
    """

    def __init__(
        self,
        *,
        exclude_ids: Optional[Set[int]] = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Browse / Add Links")
        self.setMinimumSize(520, 460)
        # IDs to exclude from results (self + already-linked entities).
        self._exclude_ids = exclude_ids or set()
        self._selected_entities: List = []
        self._result_cache: Dict[int, object] = {}
        self._build_ui()
        # Show initial results immediately.
        self._on_search()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        # ── Heading ──────────────────────────────────────────────
        heading = QLabel("Search for entities to link")
        heading.setStyleSheet("font-weight: bold; font-size: 14px;")
        layout.addWidget(heading)

        # ── Search input ─────────────────────────────────────────
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Type to search by name...")
        self.search_input.setStyleSheet(INPUT_STYLE)
        self.search_input.setClearButtonEnabled(True)
        # Live search on every keystroke.
        self.search_input.textChanged.connect(self._on_search)
        layout.addWidget(self.search_input)

        # ── Results list (multi-select) ──────────────────────────
        self.results_list = QListWidget()
        self.results_list.setSelectionMode(QListWidget.MultiSelection)
        self.results_list.setStyleSheet("font-size: 13px;")
        layout.addWidget(self.results_list, stretch=1)

        # ── Info label ───────────────────────────────────────────
        self.info_label = QLabel("")
        self.info_label.setStyleSheet("color: #999; font-size: 12px;")
        layout.addWidget(self.info_label)

        # ── Buttons ──────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        self.add_btn = QPushButton("Add Selected")
        self.add_btn.setStyleSheet(PRIMARY_BTN_STYLE)
        self.add_btn.setCursor(Qt.PointingHandCursor)
        self.add_btn.clicked.connect(self._on_add)
        btn_row.addWidget(self.add_btn)

        layout.addLayout(btn_row)

    def _on_search(self):
        """Run the search query and populate the results list."""
        query = self.search_input.text().strip()
        try:
            results = search_entities(
                query,
                exclude_ids=list(self._exclude_ids) if self._exclude_ids else None,
                limit=50,
            )
        except Exception as exc:
            self.info_label.setText(f"Search error: {exc}")
            return

        self.results_list.clear()
        self._result_cache.clear()

        if not results:
            self.info_label.setText("No matching entities found.")
            return

        self.info_label.setText(f"{len(results)} result(s)")

        for entity in results:
            info = ENTITY_DISPLAY.get(entity.entity_type, {})
            icon = info.get("icon", "•")
            label = info.get("label", entity.entity_type)
            display = f"{icon}  {entity.name}    ({label})"

            item = QListWidgetItem(display)
            item.setData(Qt.UserRole, entity.id)
            self.results_list.addItem(item)
            # Cache the entity object keyed by ID for retrieval later.
            self._result_cache[entity.id] = entity

    def _on_add(self):
        """Collect selected items and accept the dialog."""
        self._selected_entities = []
        for item in self.results_list.selectedItems():
            entity_id = item.data(Qt.UserRole)
            entity = self._result_cache.get(entity_id)
            if entity:
                self._selected_entities.append(entity)
        self.accept()

    def get_selected(self) -> List:
        """Return the list of Entity objects chosen by the user."""
        return self._selected_entities


# ═══════════════════════════════════════════════════════════════════
# LINKED-TO WIDGET  (reusable panel for managing M2M links)
# ═══════════════════════════════════════════════════════════════════

class LinkedToPanel(QWidget):
    """
    Reusable widget that displays the "Linked To" list and a Browse button.

    Used by both AddEntityDialog and EditEntityDialog.  It tracks links
    in memory as a set of entity IDs and entity objects, so the parent
    dialog can batch-apply changes only on save.

    Public API
    ──────────
    set_links(entities)   — initialise with a list of Entity objects.
    get_current_ids()     — return the set of IDs currently in the list.
    get_added_ids()       — IDs added since set_links() was called.
    get_removed_ids()     — IDs removed since set_links() was called.
    """

    def __init__(self, *, self_entity_id: Optional[int] = None, parent: Optional[QWidget] = None):
        """
        Args:
            self_entity_id: The ID of the entity being edited (excluded from
                            the browse dialog to prevent self-links).  None
                            for new entities that don't have an ID yet.
        """
        super().__init__(parent)
        self._self_id = self_entity_id
        # Current link set — keyed by entity ID for O(1) membership tests.
        self._linked: Dict[int, object] = {}
        # Snapshot of the original IDs when set_links() was called.
        self._original_ids: Set[int] = set()
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        # ── Header row: label + Browse button ────────────────────
        header = QHBoxLayout()
        lbl = QLabel("Linked To")
        lbl.setStyleSheet("font-weight: bold; font-size: 13px;")
        header.addWidget(lbl)
        header.addStretch()

        self.browse_btn = QPushButton("＋ Browse / Add Link")
        self.browse_btn.setStyleSheet(PRIMARY_BTN_STYLE)
        self.browse_btn.setCursor(Qt.PointingHandCursor)
        self.browse_btn.clicked.connect(self._on_browse)
        header.addWidget(self.browse_btn)
        layout.addLayout(header)

        # ── Link list ────────────────────────────────────────────
        self.link_list = QListWidget()
        self.link_list.setMaximumHeight(160)
        self.link_list.setStyleSheet("font-size: 13px;")
        layout.addWidget(self.link_list)

        # ── Empty-state hint ─────────────────────────────────────
        self.empty_label = QLabel("No links yet.  Use the Browse button above to add links.")
        self.empty_label.setStyleSheet("color: #888; font-size: 12px; padding: 4px;")
        layout.addWidget(self.empty_label)

    # ── Public API ───────────────────────────────────────────────

    def set_links(self, entities: List):
        """Initialise the panel with an existing set of linked entities.
        Called once when the Edit dialog opens."""
        self._linked.clear()
        for e in entities:
            self._linked[e.id] = e
        self._original_ids = set(self._linked.keys())
        self._refresh_list()

    def get_current_ids(self) -> Set[int]:
        """Return the IDs currently in the link list."""
        return set(self._linked.keys())

    def get_added_ids(self) -> Set[int]:
        """Return IDs that were added since set_links()."""
        return self.get_current_ids() - self._original_ids

    def get_removed_ids(self) -> Set[int]:
        """Return IDs that were removed since set_links()."""
        return self._original_ids - self.get_current_ids()

    # ── Internal ─────────────────────────────────────────────────

    def _refresh_list(self):
        """Rebuild the QListWidget from the in-memory link dict."""
        self.link_list.clear()

        if not self._linked:
            self.empty_label.setVisible(True)
            self.link_list.setVisible(False)
            return

        self.empty_label.setVisible(False)
        self.link_list.setVisible(True)

        for entity_id, entity in sorted(self._linked.items(), key=lambda kv: kv[1].name):
            info = ENTITY_DISPLAY.get(entity.entity_type, {})
            icon = info.get("icon", "•")
            label = info.get("label", entity.entity_type)

            # Build a custom widget for the row: icon + name + type + Remove button.
            row_widget = QWidget()
            row_layout = QHBoxLayout(row_widget)
            row_layout.setContentsMargins(4, 2, 4, 2)
            row_layout.setSpacing(8)

            text_label = QLabel(f"{icon}  {entity.name}   ({label}, id:{entity.id})")
            text_label.setStyleSheet("font-size: 13px;")
            row_layout.addWidget(text_label, stretch=1)

            remove_btn = QPushButton("✕ Remove")
            remove_btn.setStyleSheet(REMOVE_BTN_STYLE)
            remove_btn.setCursor(Qt.PointingHandCursor)
            # Capture entity_id in the lambda via default arg.
            remove_btn.clicked.connect(lambda checked=False, eid=entity_id: self._remove_link(eid))
            row_layout.addWidget(remove_btn)

            # Create a QListWidgetItem sized to fit the custom widget.
            item = QListWidgetItem()
            item.setSizeHint(row_widget.sizeHint())
            self.link_list.addItem(item)
            self.link_list.setItemWidget(item, row_widget)

    def _remove_link(self, entity_id: int):
        """Remove a link from the in-memory set and refresh the list."""
        if entity_id in self._linked:
            del self._linked[entity_id]
            self._refresh_list()

    def _on_browse(self):
        """Open the LinkBrowserDialog and add selected entities."""
        # Exclude self (if editing) and all currently-linked IDs.
        exclude = set(self._linked.keys())
        if self._self_id is not None:
            exclude.add(self._self_id)

        dialog = LinkBrowserDialog(exclude_ids=exclude, parent=self)
        if dialog.exec() == QDialog.Accepted:
            for entity in dialog.get_selected():
                if entity.id not in self._linked:
                    self._linked[entity.id] = entity
            self._refresh_list()


# ═══════════════════════════════════════════════════════════════════
# ADD ENTITY DIALOG
# ═══════════════════════════════════════════════════════════════════

class AddEntityDialog(QDialog):
    """
    Dialog for creating a new System, Sub-system, or Element.

    On Save:
      1. Creates the entity via `create_entity()`.
      2. Creates all links via `link_entities()`.

    Both calls pass `user_id` for audit logging.

    The new entity's parent is determined by the `parent_id` parameter
    (which comes from the currently selected tree item in project_view).
    """

    def __init__(
        self,
        *,
        entity_type: str,
        parent_id: int,
        parent_name: str,
        user_id: int,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self._entity_type = entity_type
        self._parent_id = parent_id
        self._user_id = user_id
        self._created_entity = None

        info = ENTITY_DISPLAY.get(entity_type, {})
        type_label = info.get("label", entity_type)

        self.setWindowTitle(f"Add {type_label}")
        self.setMinimumSize(520, 520)

        self._build_ui(type_label, parent_name)

    def _build_ui(self, type_label: str, parent_name: str):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(10)

        # ── Heading ──────────────────────────────────────────────
        heading = QLabel(f"Create New {type_label}")
        heading.setAlignment(Qt.AlignCenter)
        hfont = QFont()
        hfont.setPointSize(16)
        hfont.setBold(True)
        heading.setFont(hfont)
        layout.addWidget(heading)

        # ── Parent info ──────────────────────────────────────────
        parent_label = QLabel(f"Parent: {parent_name}")
        parent_label.setStyleSheet("color: #999; font-size: 13px;")
        parent_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(parent_label)

        # ── Name field ───────────────────────────────────────────
        layout.addWidget(QLabel("Name"))
        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText(f"Enter {type_label.lower()} name...")
        self.name_input.setStyleSheet(INPUT_STYLE)
        layout.addWidget(self.name_input)

        # ── Description field (rich text) ────────────────────────
        layout.addWidget(QLabel("Description (optional)"))
        self.desc_editor = RichTextEditor(
            placeholder="Brief description...",
            max_height=160,
            parent=self,
        )
        layout.addWidget(self.desc_editor)

        # ── Linked To panel ──────────────────────────────────────
        # No self_entity_id yet (entity doesn't exist until Save).
        self.links_panel = LinkedToPanel(self_entity_id=None, parent=self)
        layout.addWidget(self.links_panel)

        # ── Feedback label ───────────────────────────────────────
        self.feedback = _make_feedback_label()
        layout.addWidget(self.feedback)

        # ── Buttons ──────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        save_btn = QPushButton(f"Create {type_label}")
        save_btn.setStyleSheet(SAVE_BTN_STYLE)
        save_btn.setCursor(Qt.PointingHandCursor)
        save_btn.clicked.connect(self._on_save)
        btn_row.addWidget(save_btn)

        layout.addLayout(btn_row)

    def _on_save(self):
        """Validate, create the entity, then create all links."""
        _clear_feedback(self.feedback)

        name = self.name_input.text().strip()
        description = self.desc_editor.get_html() or None

        if not name:
            _show_error(self.feedback, "Name is required.")
            self.name_input.setFocus()
            return

        # ── Step 1: Create the entity ────────────────────────────
        try:
            entity = create_entity(
                entity_type=self._entity_type,
                name=name,
                user_id=self._user_id,
                parent_id=self._parent_id,
                description=description,
            )
        except Exception as exc:
            _show_error(self.feedback, f"Failed to create entity: {exc}")
            return

        # ── Step 2: Create all links ─────────────────────────────
        link_ids = self.links_panel.get_current_ids()
        link_errors = []
        for target_id in link_ids:
            try:
                link_entities(
                    source_id=entity.id,
                    target_id=target_id,
                    user_id=self._user_id,
                )
            except Exception as exc:
                link_errors.append(f"→ id {target_id}: {exc}")

        if link_errors:
            # Entity was created but some links failed — warn but don't block.
            QMessageBox.warning(
                self,
                "Partial Link Errors",
                f"Entity '{name}' was created successfully, but some links "
                f"could not be saved:\n\n" + "\n".join(link_errors),
            )

        self._created_entity = entity
        self.accept()

    def get_created_entity(self):
        """Return the newly created Entity, or None if cancelled."""
        return self._created_entity


# ═══════════════════════════════════════════════════════════════════
# EDIT ENTITY DIALOG
# ═══════════════════════════════════════════════════════════════════

class EditEntityDialog(QDialog):
    """
    Dialog for editing an existing System, Sub-system, or Element.

    On Save:
      1. Updates changed fields via `update_entity()`.
      2. Diffs the link set:
         • New links    → `link_entities()` for each added ID.
         • Removed links → `unlink_entities()` for each removed ID.

    All calls pass `user_id` for audit logging.
    """

    def __init__(
        self,
        *,
        entity,
        user_id: int,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self._entity = entity
        self._user_id = user_id
        self._saved = False

        info = ENTITY_DISPLAY.get(entity.entity_type, {})
        type_label = info.get("label", entity.entity_type)

        self.setWindowTitle(f"Edit {type_label} — {entity.name}")
        self.setMinimumSize(520, 520)

        self._build_ui(type_label)
        self._load_existing_links()

    def _build_ui(self, type_label: str):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(10)

        # ── Heading ──────────────────────────────────────────────
        heading = QLabel(f"Edit {type_label}")
        heading.setAlignment(Qt.AlignCenter)
        hfont = QFont()
        hfont.setPointSize(16)
        hfont.setBold(True)
        heading.setFont(hfont)
        layout.addWidget(heading)

        # ── Entity ID / type info ────────────────────────────────
        id_label = QLabel(f"ID: {self._entity.id}   |   Type: {type_label}")
        id_label.setStyleSheet("color: #999; font-size: 13px;")
        id_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(id_label)

        # ── Name field ───────────────────────────────────────────
        layout.addWidget(QLabel("Name"))
        self.name_input = QLineEdit(self._entity.name)
        self.name_input.setStyleSheet(INPUT_STYLE)
        layout.addWidget(self.name_input)

        # ── Description field (rich text) ────────────────────────
        layout.addWidget(QLabel("Description"))
        self.desc_editor = RichTextEditor(
            placeholder="Brief description...",
            max_height=160,
            parent=self,
        )
        self.desc_editor.set_html(self._entity.description or "")
        layout.addWidget(self.desc_editor)

        # ── Linked To panel ──────────────────────────────────────
        self.links_panel = LinkedToPanel(
            self_entity_id=self._entity.id, parent=self
        )
        layout.addWidget(self.links_panel)

        # ── Feedback label ───────────────────────────────────────
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
        """Fetch current links from the database and populate the panel."""
        try:
            linked = get_linked_entities(
                self._entity.id, direction="outgoing"
            )
        except Exception:
            linked = []
        self.links_panel.set_links(linked)

    def _on_save(self):
        """Validate, update fields, then diff and apply link changes."""
        _clear_feedback(self.feedback)

        name = self.name_input.text().strip()
        description = self.desc_editor.get_html() or None

        if not name:
            _show_error(self.feedback, "Name is required.")
            self.name_input.setFocus()
            return

        # ── Step 1: Update entity fields (only if changed) ───────
        updates = {}
        if name != self._entity.name:
            updates["name"] = name
        if description != (self._entity.description or None):
            updates["description"] = description

        if updates:
            try:
                updated = update_entity(
                    entity_id=self._entity.id,
                    user_id=self._user_id,
                    updates=updates,
                )
                if updated is None:
                    _show_error(self.feedback, "Entity not found — it may have been deleted.")
                    return
                self._entity = updated
            except Exception as exc:
                _show_error(self.feedback, f"Failed to save: {exc}")
                return

        # ── Step 2: Apply link diff ──────────────────────────────
        added_ids = self.links_panel.get_added_ids()
        removed_ids = self.links_panel.get_removed_ids()
        link_errors = []

        # Create new links.
        for target_id in added_ids:
            try:
                link_entities(
                    source_id=self._entity.id,
                    target_id=target_id,
                    user_id=self._user_id,
                )
            except Exception as exc:
                link_errors.append(f"Link → id {target_id}: {exc}")

        # Remove old links.
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
                self,
                "Partial Link Errors",
                "Entity fields saved, but some link changes failed:\n\n"
                + "\n".join(link_errors),
            )

        self._saved = True
        self.accept()

    def was_saved(self) -> bool:
        """Return True if the user clicked Save (not Cancel)."""
        return self._saved

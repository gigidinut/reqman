"""
project_view.py — Project workspace with hierarchical entity tree.

Shown when the user opens or creates a project from the main menu.
This is the primary working screen of the application.

Layout
──────
┌──────────────────────────────────────────────────────────────────┐
│  [← Back to Menu]    Project: <name>               [Account]    │ top bar
├──────────────┬───────────────────────────────────────────────────┤
│  🔍 Search   │                                                   │
│──────────────│          (central content area)                   │
│  ▸ System A  │          — reserved for future                    │
│    ▸ Sub-1   │            Add/Edit forms —                       │
│      Elem-1  │                                                   │
│      REQ-001 │                                                   │
│  ▸ System B  │                                                   │
│──────────────│                                                   │
│ [＋Sys][＋Sub]│                                                   │
│ [＋Elm][＋Req]│                                                   │
│──────────────│                                                   │
│ [Edit][Del]  │                                                   │
│ [History]    │                                                   │
└──────────────┴───────────────────────────────────────────────────┘

Tree population
───────────────
The tree is loaded recursively from the database using `get_children()`.
Each QTreeWidgetItem stores the full Entity object in `Qt.UserRole` so
we never need index-to-ID lookups.

Add buttons
───────────
Four "＋" buttons are always visible at the bottom of the left panel.
They add a child of the given type under the currently selected node
(or directly under the project root if nothing is selected).

Context buttons
───────────────
When any tree item is selected, Edit / Delete / History buttons appear.
The "＋ Requirement" button is hidden if the selected item is itself a
Requirement (leaf nodes cannot have children).

Delete cascade warning
──────────────────────
Deleting a non-leaf entity shows a QMessageBox.warning listing how many
descendants will also be removed.  The actual deletion is performed by
`delete_entity()` which cascades at the database level.

Search
──────
The search field filters tree items by name in real-time.  Items whose
name contains the search text (case-insensitive) are shown; all others
are hidden.  When the search is cleared, the full tree is restored.
"""

from typing import Optional, List, Dict

from PySide6.QtCore import Qt, Signal, QUrl
from PySide6.QtGui import QFont, QColor, QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from controllers.db_controllers import (
    create_entity,
    delete_entity,
    get_audit_log,
    get_children,
    get_entity,
    get_linked_entities,
    update_entity,
)
from views.entity_dialogs import AddEntityDialog, EditEntityDialog
from views.requirement_dialog import AddRequirementDialog, EditRequirementDialog


# ═══════════════════════════════════════════════════════════════════
# CONSTANTS
# ═══════════════════════════════════════════════════════════════════

# Human-readable labels and unicode icons for each entity type.
# Used in the tree, buttons, and dialogs for visual consistency.
ENTITY_DISPLAY: Dict[str, Dict[str, str]] = {
    "project":     {"icon": "📁", "label": "Project"},
    "system":      {"icon": "⚙️", "label": "System"},
    "subsystem":   {"icon": "🔧", "label": "Sub-system"},
    "element":     {"icon": "📦", "label": "Element"},
    "requirement": {"icon": "📋", "label": "Requirement"},
}

# The four child types that can be added under a parent.
# Order matches the button row layout.
ADDABLE_TYPES = ["system", "subsystem", "element", "requirement"]

# ── Inline stylesheet fragments ──────────────────────────────────

# Small action button used in the left panel toolbar rows.
ACTION_BTN_STYLE = """
    QPushButton {{
        background-color: {bg};
        color: white;
        border: none;
        border-radius: 5px;
        padding: 5px 10px;
        font-size: 12px;
        font-weight: 600;
    }}
    QPushButton:hover {{ background-color: {hover}; }}
    QPushButton:pressed {{ background-color: {pressed}; }}
    QPushButton:disabled {{
        background-color: #555;
        color: #888;
    }}
"""

ADD_BTN     = ACTION_BTN_STYLE.format(bg="#2ecc71", hover="#27ae60", pressed="#1e8449")
EDIT_BTN    = ACTION_BTN_STYLE.format(bg="#3498db", hover="#2980b9", pressed="#1f6fa5")
DELETE_BTN  = ACTION_BTN_STYLE.format(bg="#e74c3c", hover="#c0392b", pressed="#96281b")
HISTORY_BTN = ACTION_BTN_STYLE.format(bg="#8e44ad", hover="#71368a", pressed="#5b2d8e")

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

SEARCH_STYLE = "padding: 7px; font-size: 13px;"


# ═══════════════════════════════════════════════════════════════════
# HELPER: count descendants recursively via tree items
# ═══════════════════════════════════════════════════════════════════

def _count_descendants(item: QTreeWidgetItem) -> int:
    """Return the total number of descendants (children, grandchildren, etc.)
    beneath the given tree item.  Used to build the cascade-delete warning."""
    count = 0
    for i in range(item.childCount()):
        count += 1  # the child itself
        count += _count_descendants(item.child(i))  # its subtree
    return count


def _collect_visible_items(item: QTreeWidgetItem) -> List[QTreeWidgetItem]:
    """Recursively collect all items in the subtree (including the item itself)."""
    result = [item]
    for i in range(item.childCount()):
        result.extend(_collect_visible_items(item.child(i)))
    return result


# ═══════════════════════════════════════════════════════════════════
# DIALOG: Audit History for a single entity
# ═══════════════════════════════════════════════════════════════════

class EntityHistoryDialog(QDialog):
    """
    Modal dialog showing the audit history for a specific entity.

    Fetches entries from `get_audit_log(entity_id=...)` and displays
    them in a read-only QTableWidget with columns:
      Action | Timestamp | Details
    """

    def __init__(self, entity, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._entity = entity
        self.setWindowTitle(f"History — {entity.name}")
        self.setMinimumSize(600, 400)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)

        # ── Heading ──────────────────────────────────────────────
        info = ENTITY_DISPLAY.get(self._entity.entity_type, {})
        icon = info.get("icon", "")
        label_text = info.get("label", self._entity.entity_type)
        heading = QLabel(f"{icon}  {label_text}: {self._entity.name}")
        heading_font = QFont()
        heading_font.setPointSize(14)
        heading_font.setBold(True)
        heading.setFont(heading_font)
        layout.addWidget(heading)

        # ── Table ────────────────────────────────────────────────
        self.table = QTableWidget()
        self.table.setColumnCount(3)
        self.table.setHorizontalHeaderLabels(["Action", "Timestamp", "Details"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeToContents
        )
        self.table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeToContents
        )
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.verticalHeader().setVisible(False)
        layout.addWidget(self.table)

        # ── Close button ─────────────────────────────────────────
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn, alignment=Qt.AlignRight)

        # ── Populate the table ───────────────────────────────────
        self._load_history()

    def _load_history(self):
        """Fetch audit entries for this entity and fill the table rows."""
        try:
            entries = get_audit_log(entity_id=self._entity.id, limit=200)
        except Exception as exc:
            self.table.setRowCount(1)
            self.table.setItem(0, 0, QTableWidgetItem(f"Error: {exc}"))
            return

        if not entries:
            self.table.setRowCount(1)
            error_item = QTableWidgetItem("No history found for this entity.")
            error_item.setForeground(QColor("#999"))
            self.table.setItem(0, 0, error_item)
            return

        self.table.setRowCount(len(entries))
        for row, entry in enumerate(entries):
            # Action column — colour-coded for quick scanning.
            action_item = QTableWidgetItem(entry.action)
            colour_map = {
                "CREATE": "#2ecc71",
                "UPDATE": "#3498db",
                "DELETE": "#e74c3c",
                "LINK":   "#f39c12",
                "UNLINK": "#e67e22",
            }
            colour = colour_map.get(entry.action, "#cccccc")
            action_item.setForeground(QColor(colour))
            font = action_item.font()
            font.setBold(True)
            action_item.setFont(font)
            self.table.setItem(row, 0, action_item)

            # Timestamp column.
            ts_str = ""
            if entry.timestamp:
                ts_str = entry.timestamp.strftime("%Y-%m-%d  %H:%M:%S")
            self.table.setItem(row, 1, QTableWidgetItem(ts_str))

            # Details column — show the raw JSON string, truncated.
            details_str = entry.details or ""
            if len(details_str) > 120:
                details_str = details_str[:117] + "..."
            self.table.setItem(row, 2, QTableWidgetItem(details_str))


# ═══════════════════════════════════════════════════════════════════
# PROJECT SCREEN  (main workspace)
# ═══════════════════════════════════════════════════════════════════

class ProjectScreen(QMainWindow):
    """
    Full project workspace with a left-side hierarchy tree and a
    central content area (blank placeholder for now).

    Parameters
    ----------
    project : Entity
        The Project entity being worked on.
    user : User
        The authenticated user (for audit-logged DB operations).

    Signals
    -------
    go_back : emitted when the user clicks "← Back to Menu".
    """

    go_back = Signal()

    WINDOW_WIDTH  = 1100
    WINDOW_HEIGHT = 700

    def __init__(self, project, user, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._project = project
        self._user = user

        self.setWindowTitle(f"Requirements Manager — {project.name}")
        self.setMinimumSize(self.WINDOW_WIDTH, self.WINDOW_HEIGHT)

        self._build_ui()
        self._load_tree()

    # ─────────────────────────────────────────────────────────────
    # UI Construction
    # ─────────────────────────────────────────────────────────────

    def _build_ui(self):
        """Assemble the top bar, left panel (tree + buttons), and
        central placeholder into a QSplitter layout."""

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Top bar ──────────────────────────────────────────────
        root.addWidget(self._build_top_bar())

        # ── Splitter: left panel | central content ───────────────
        self.splitter = QSplitter(Qt.Horizontal)
        self.splitter.setHandleWidth(1)
        root.addWidget(self.splitter, stretch=1)

        # Left panel — search + tree + buttons.
        self.splitter.addWidget(self._build_left_panel())

        # Central content area — blank placeholder for future forms.
        self.splitter.addWidget(self._build_central_placeholder())

        # Set initial proportions: ~30% left, ~70% right.
        self.splitter.setSizes([300, 700])

    # ── Top bar ──────────────────────────────────────────────────

    def _build_top_bar(self) -> QWidget:
        """Construct the top bar with back button and project name."""
        bar = QWidget()
        bar.setStyleSheet("padding: 4px 8px;")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(12, 8, 12, 8)

        # ── Back button ──────────────────────────────────────────
        self.back_btn = QPushButton("←  Back to Menu")
        self.back_btn.setStyleSheet(TOPBAR_BTN_STYLE)
        self.back_btn.setCursor(Qt.PointingHandCursor)
        self.back_btn.clicked.connect(self.go_back.emit)
        layout.addWidget(self.back_btn)

        layout.addStretch(1)

        # ── Project title ────────────────────────────────────────
        title = QLabel(f"📁  Project: {self._project.name}")
        title_font = QFont()
        title_font.setPointSize(14)
        title_font.setBold(True)
        title.setFont(title_font)
        layout.addWidget(title)

        layout.addStretch(1)

        # ── User label (right side) ─────────────────────────────
        user_label = QLabel(f"{self._user.display_name}")
        user_label.setStyleSheet("color: #aaa; font-size: 13px;")
        layout.addWidget(user_label)

        return bar

    # ── Left panel ───────────────────────────────────────────────

    def _build_left_panel(self) -> QWidget:
        """Build the left sidebar: search bar, tree view, and button rows."""
        panel = QWidget()
        panel.setMinimumWidth(240)
        panel.setMaximumWidth(420)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # ── Search field ─────────────────────────────────────────
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("🔍  Search by name...")
        self.search_input.setStyleSheet(SEARCH_STYLE)
        self.search_input.setClearButtonEnabled(True)
        # Filter the tree as the user types.
        self.search_input.textChanged.connect(self._on_search_changed)
        layout.addWidget(self.search_input)

        # ── Tree widget ──────────────────────────────────────────
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setAnimated(True)
        self.tree.setIndentation(20)
        # Selecting an item updates the context buttons.
        self.tree.currentItemChanged.connect(self._on_tree_selection_changed)
        layout.addWidget(self.tree, stretch=1)

        # ── Separator line ───────────────────────────────────────
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setFrameShadow(QFrame.Sunken)
        layout.addWidget(sep)

        # ── Add-child buttons (always visible) ───────────────────
        # These add a child entity under the currently selected node,
        # or directly under the project root if nothing is selected.
        add_label = QLabel("Add Child Entity")
        add_label.setStyleSheet("font-weight: bold; font-size: 12px; padding-top: 4px;")
        layout.addWidget(add_label)

        add_row_1 = QHBoxLayout()
        self.add_system_btn = QPushButton("＋ System")
        self.add_subsystem_btn = QPushButton("＋ Sub-system")
        self.add_system_btn.setStyleSheet(ADD_BTN)
        self.add_subsystem_btn.setStyleSheet(ADD_BTN)
        self.add_system_btn.setCursor(Qt.PointingHandCursor)
        self.add_subsystem_btn.setCursor(Qt.PointingHandCursor)
        self.add_system_btn.clicked.connect(lambda: self._on_add_entity("system"))
        self.add_subsystem_btn.clicked.connect(lambda: self._on_add_entity("subsystem"))
        add_row_1.addWidget(self.add_system_btn)
        add_row_1.addWidget(self.add_subsystem_btn)
        layout.addLayout(add_row_1)

        add_row_2 = QHBoxLayout()
        self.add_element_btn = QPushButton("＋ Element")
        self.add_requirement_btn = QPushButton("＋ Requirement")
        self.add_element_btn.setStyleSheet(ADD_BTN)
        self.add_requirement_btn.setStyleSheet(ADD_BTN)
        self.add_element_btn.setCursor(Qt.PointingHandCursor)
        self.add_requirement_btn.setCursor(Qt.PointingHandCursor)
        self.add_element_btn.clicked.connect(lambda: self._on_add_entity("element"))
        self.add_requirement_btn.clicked.connect(lambda: self._on_add_entity("requirement"))
        add_row_2.addWidget(self.add_element_btn)
        add_row_2.addWidget(self.add_requirement_btn)
        layout.addLayout(add_row_2)

        # ── Context action buttons (shown when an item is selected) ─
        # These are wrapped in a container widget so we can show/hide
        # the entire group with one call.
        self.context_widget = QWidget()
        ctx_layout = QVBoxLayout(self.context_widget)
        ctx_layout.setContentsMargins(0, 6, 0, 0)
        ctx_layout.setSpacing(4)

        ctx_sep = QFrame()
        ctx_sep.setFrameShape(QFrame.HLine)
        ctx_sep.setFrameShadow(QFrame.Sunken)
        ctx_layout.addWidget(ctx_sep)

        ctx_label = QLabel("Selected Item Actions")
        ctx_label.setStyleSheet("font-weight: bold; font-size: 12px; padding-top: 2px;")
        ctx_layout.addWidget(ctx_label)

        ctx_row = QHBoxLayout()
        self.edit_btn = QPushButton("✏️  Edit")
        self.delete_btn = QPushButton("🗑️  Delete")
        self.history_btn = QPushButton("📜  History")
        self.edit_btn.setStyleSheet(EDIT_BTN)
        self.delete_btn.setStyleSheet(DELETE_BTN)
        self.history_btn.setStyleSheet(HISTORY_BTN)
        self.edit_btn.setCursor(Qt.PointingHandCursor)
        self.delete_btn.setCursor(Qt.PointingHandCursor)
        self.history_btn.setCursor(Qt.PointingHandCursor)
        self.edit_btn.clicked.connect(self._on_edit)
        self.delete_btn.clicked.connect(self._on_delete)
        self.history_btn.clicked.connect(self._on_history)
        ctx_row.addWidget(self.edit_btn)
        ctx_row.addWidget(self.delete_btn)
        ctx_row.addWidget(self.history_btn)
        ctx_layout.addLayout(ctx_row)

        # Initially hidden — shown when the user selects a tree item.
        self.context_widget.setVisible(False)
        layout.addWidget(self.context_widget)

        return panel

    # ── Central placeholder ──────────────────────────────────────

    def _build_central_placeholder(self) -> QWidget:
        """Build the central detail viewer panel.

        Uses a QScrollArea containing a QVBoxLayout of QLabels.  When a
        tree item is selected, `_display_entity_details()` clears and
        rebuilds this layout with the entity's fields.  For requirements,
        the test-plan path and ticket link render as clickable labels.
        """
        # Outer container — the scroll area lives inside this.
        container = QWidget()
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(0, 0, 0, 0)

        # Scroll area wraps the detail content so long descriptions
        # don't overflow the window.
        self.detail_scroll = QScrollArea()
        self.detail_scroll.setWidgetResizable(True)
        self.detail_scroll.setFrameShape(QFrame.NoFrame)

        # The actual detail widget whose layout gets cleared/rebuilt
        # on each selection change.
        self.detail_widget = QWidget()
        self.detail_layout = QVBoxLayout(self.detail_widget)
        self.detail_layout.setContentsMargins(20, 20, 20, 20)
        self.detail_layout.setSpacing(10)
        self.detail_layout.setAlignment(Qt.AlignTop)

        # Default message when nothing is selected.
        self._detail_default_msg = QLabel(
            "Select an item from the tree\nto view its details."
        )
        self._detail_default_msg.setAlignment(Qt.AlignCenter)
        self._detail_default_msg.setStyleSheet("color: #777; font-size: 16px; padding: 40px;")
        self.detail_layout.addWidget(self._detail_default_msg)

        self.detail_scroll.setWidget(self.detail_widget)
        container_layout.addWidget(self.detail_scroll)

        return container

    # ─────────────────────────────────────────────────────────────
    # Detail Viewer — display entity details in the central panel
    # ─────────────────────────────────────────────────────────────

    def _clear_detail_layout(self):
        """Remove all widgets from the detail layout."""
        while self.detail_layout.count():
            item = self.detail_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

    def _add_detail_heading(self, text: str):
        """Add a large bold heading to the detail panel."""
        label = QLabel(text)
        font = QFont()
        font.setPointSize(16)
        font.setBold(True)
        label.setFont(font)
        label.setWordWrap(True)
        self.detail_layout.addWidget(label)

    def _add_detail_field(self, name: str, value: str, is_link: bool = False,
                          link_url: str = "", is_file_link: bool = False):
        """Add a labelled field row to the detail panel.

        Args:
            name:          Field label (e.g. "Status").
            value:         Display value.
            is_link:       If True, renders as a clickable blue label.
            link_url:      The URL to open on click (for web links).
            is_file_link:  If True, opens as a local file instead of URL.
        """
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(8)

        name_label = QLabel(f"<b>{name}:</b>")
        name_label.setStyleSheet("font-size: 13px; min-width: 120px;")
        name_label.setAlignment(Qt.AlignTop)
        row_layout.addWidget(name_label)

        if is_link and (link_url or value):
            # Clickable link label.
            val_label = QLabel(f"<a style='color: #3498db;'>{value}</a>")
            val_label.setStyleSheet("font-size: 13px;")
            val_label.setCursor(Qt.PointingHandCursor)
            val_label.setWordWrap(True)
            val_label.setToolTip(f"Click to open: {link_url or value}")
            url_to_open = link_url or value
            file_flag = is_file_link
            val_label.mousePressEvent = lambda ev, u=url_to_open, f=file_flag: (
                QDesktopServices.openUrl(QUrl.fromLocalFile(u)) if f
                else QDesktopServices.openUrl(QUrl(u if u.startswith(("http://", "https://")) else "https://" + u))
            )
        else:
            val_label = QLabel(value or "—")
            val_label.setStyleSheet("font-size: 13px;")
            val_label.setWordWrap(True)
            val_label.setTextInteractionFlags(Qt.TextSelectableByMouse)

        row_layout.addWidget(val_label, stretch=1)
        self.detail_layout.addWidget(row)

    def _add_detail_separator(self):
        """Add a horizontal line separator."""
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setFrameShadow(QFrame.Sunken)
        self.detail_layout.addWidget(sep)

    def _display_entity_details(self, entity):
        """Populate the central detail panel with the given entity's fields.

        Called whenever the tree selection changes.  Shows all base entity
        fields, and for requirements also shows body, priority, test plan
        path (clickable), ticket link (clickable), and linked entities.
        """
        self._clear_detail_layout()

        if entity is None:
            # No selection — show default message.
            msg = QLabel("Select an item from the tree\nto view its details.")
            msg.setAlignment(Qt.AlignCenter)
            msg.setStyleSheet("color: #777; font-size: 16px; padding: 40px;")
            self.detail_layout.addWidget(msg)
            return

        info = ENTITY_DISPLAY.get(entity.entity_type, {})
        icon = info.get("icon", "")
        type_label = info.get("label", entity.entity_type)

        # ── Heading ──────────────────────────────────────────────
        self._add_detail_heading(f"{icon}  {entity.name}")

        # ── Base fields ──────────────────────────────────────────
        self._add_detail_field("Type", type_label)
        self._add_detail_field("ID", str(entity.id))
        self._add_detail_field("Status", entity.status)
        if entity.description and entity.entity_type != "requirement":
            # For requirements, body is shown separately below.
            self._add_detail_field("Description", entity.description)

        # ── Requirement-specific fields ──────────────────────────
        if entity.entity_type == "requirement":
            self._add_detail_separator()

            if hasattr(entity, "req_id") and entity.req_id:
                self._add_detail_field("Requirement ID", entity.req_id)
            if hasattr(entity, "body") and entity.body:
                self._add_detail_field("Body", entity.body)
            if hasattr(entity, "priority") and entity.priority:
                self._add_detail_field("Priority", entity.priority)
            if hasattr(entity, "ai_score") and entity.ai_score:
                self._add_detail_field("AI Score", f"🤖 {entity.ai_score}")
            if hasattr(entity, "rationale") and entity.rationale:
                self._add_detail_field("Rationale", entity.rationale)

            # Test plan path — clickable file link.
            if hasattr(entity, "test_plan_path") and entity.test_plan_path:
                import os
                filename = os.path.basename(entity.test_plan_path)
                self._add_detail_field(
                    "Test Plan",
                    f"📎 {filename}",
                    is_link=True,
                    link_url=entity.test_plan_path,
                    is_file_link=True,
                )

            # Ticket link — clickable URL.
            if hasattr(entity, "ticket_link") and entity.ticket_link:
                self._add_detail_field(
                    "Ticket",
                    entity.ticket_link,
                    is_link=True,
                    link_url=entity.ticket_link,
                )

        # ── Timestamps ───────────────────────────────────────────
        self._add_detail_separator()
        created = entity.created_at.strftime("%Y-%m-%d %H:%M") if entity.created_at else "—"
        updated = entity.updated_at.strftime("%Y-%m-%d %H:%M") if entity.updated_at else "—"
        self._add_detail_field("Created", created)
        self._add_detail_field("Updated", updated)

        # ── Linked entities ──────────────────────────────────────
        try:
            linked = get_linked_entities(entity.id, direction="both")
        except Exception:
            linked = []

        if linked:
            self._add_detail_separator()
            links_heading = QLabel(f"<b>Linked Entities ({len(linked)})</b>")
            links_heading.setStyleSheet("font-size: 13px; padding-top: 4px;")
            self.detail_layout.addWidget(links_heading)

            for le in linked:
                le_info = ENTITY_DISPLAY.get(le.entity_type, {})
                le_icon = le_info.get("icon", "•")
                le_type = le_info.get("label", le.entity_type)
                link_label = QLabel(f"  {le_icon}  {le.name}  ({le_type}, id:{le.id})")
                link_label.setStyleSheet("font-size: 12px; color: #aaa;")
                self.detail_layout.addWidget(link_label)

        # Push remaining space to the bottom so content is top-aligned.
        self.detail_layout.addStretch(1)

    # ─────────────────────────────────────────────────────────────
    # Tree Population
    # ─────────────────────────────────────────────────────────────

    def _load_tree(self):
        """Fetch the entire hierarchy from the database and populate
        the QTreeWidget recursively.

        The project itself is the invisible root — its direct children
        become the top-level items in the tree.
        """
        self.tree.clear()

        # Fetch the project's direct children from the DB.
        children = get_children(self._project.id)

        for child in children:
            item = self._build_tree_item(child)
            self.tree.addTopLevelItem(item)
            # Recursively load grandchildren, great-grandchildren, etc.
            self._load_children_recursive(item, child.id)

        # Expand the first level by default for quick orientation.
        self.tree.expandToDepth(0)

    def _build_tree_item(self, entity) -> QTreeWidgetItem:
        """Create a QTreeWidgetItem for the given entity.

        The entity's type determines the icon prefix.  For requirements,
        the user-facing req_id (if set) is shown in brackets before the
        name, e.g. "📋  [REQ-001] Noise figure limit".

        The full Entity object is stored in Qt.UserRole so we can
        retrieve it from any tree interaction without index lookups.
        """
        info = ENTITY_DISPLAY.get(entity.entity_type, {})
        icon = info.get("icon", "•")

        # For requirements, prepend the user-facing ID if it exists.
        if entity.entity_type == "requirement" and hasattr(entity, "req_id") and entity.req_id:
            display_text = f"{icon}  [{entity.req_id}] {entity.name}"
        else:
            display_text = f"{icon}  {entity.name}"

        item = QTreeWidgetItem([display_text])
        # Store the entity object for later retrieval.
        item.setData(0, Qt.UserRole, entity)

        # Subtle colour tint so entity types are distinguishable at a glance.
        type_colours = {
            "system":      QColor("#5dade2"),
            "subsystem":   QColor("#48c9b0"),
            "element":     QColor("#f5b041"),
            "requirement": QColor("#af7ac5"),
        }
        colour = type_colours.get(entity.entity_type)
        if colour:
            item.setForeground(0, colour)

        return item

    def _load_children_recursive(self, parent_item: QTreeWidgetItem, parent_id: int):
        """Recursively fetch and attach children to a tree item.

        This performs one `get_children()` DB call per non-leaf node,
        which is efficient for typical engineering projects (hundreds to
        low thousands of entities).  For very large trees, lazy loading
        on expand would be more appropriate.
        """
        children = get_children(parent_id)
        for child in children:
            child_item = self._build_tree_item(child)
            parent_item.addChild(child_item)
            # Requirements are leaf nodes — skip the recursive call.
            if child.entity_type != "requirement":
                self._load_children_recursive(child_item, child.id)

    def _get_selected_entity(self):
        """Return the Entity object stored in the currently selected tree item,
        or None if nothing is selected."""
        item = self.tree.currentItem()
        if item is None:
            return None
        return item.data(0, Qt.UserRole)

    # ─────────────────────────────────────────────────────────────
    # Search / Filter
    # ─────────────────────────────────────────────────────────────

    def _on_search_changed(self, text: str):
        """Filter tree items by name as the user types.

        Strategy: iterate all items in the tree.  If an item's name
        matches the search text (case-insensitive), show it AND all
        its ancestors (so the path to the match is visible).  If no
        match, hide it.  When the search text is empty, show everything.
        """
        query = text.strip().lower()

        if not query:
            # Reset: show all items and restore normal state.
            self._set_all_items_visible(True)
            return

        # First pass: hide everything.
        self._set_all_items_visible(False)

        # Second pass: for each item that matches, show it + ancestors.
        root = self.tree.invisibleRootItem()
        self._filter_recursive(root, query)

    def _set_all_items_visible(self, visible: bool):
        """Show or hide every item in the tree."""
        root = self.tree.invisibleRootItem()
        for item in _collect_visible_items(root):
            if item is not root:
                item.setHidden(not visible)

    def _filter_recursive(self, item: QTreeWidgetItem, query: str) -> bool:
        """Recursively check if this item or any descendant matches the query.

        If a match is found, unhide the item (and propagate upward via
        the boolean return value so ancestors also become visible).

        Returns True if this item or any of its children matched.
        """
        entity = item.data(0, Qt.UserRole)
        # Check this item's name against the query.
        self_matches = False
        if entity is not None:
            self_matches = query in entity.name.lower()

        # Check children.
        child_matched = False
        for i in range(item.childCount()):
            if self._filter_recursive(item.child(i), query):
                child_matched = True

        # If this item or any descendant matched, make it visible.
        if self_matches or child_matched:
            item.setHidden(False)
            # Expand items that have matching descendants so the user
            # can see the results without manually opening branches.
            if child_matched:
                item.setExpanded(True)
            return True

        return False

    # ─────────────────────────────────────────────────────────────
    # Tree Selection → Context Buttons
    # ─────────────────────────────────────────────────────────────

    def _on_tree_selection_changed(self, current: QTreeWidgetItem, previous: QTreeWidgetItem):
        """Called whenever the selected tree item changes.

        Updates two things:
        1. The context action buttons (show/hide, enable/disable).
        2. The central detail viewer panel with the selected entity's fields.
        """
        entity = None
        if current is not None:
            entity = current.data(0, Qt.UserRole)

        # ── Update detail viewer ─────────────────────────────────
        # Re-fetch from DB to get the latest field values (in case
        # the entity was just edited and the tree item's cached
        # object is stale).
        if entity is not None:
            fresh = get_entity(entity.id)
            if fresh is not None:
                entity = fresh
                # Update the tree item's cached object too.
                current.setData(0, Qt.UserRole, fresh)
        self._display_entity_details(entity)

        # ── Update context buttons ───────────────────────────────
        if entity is None:
            self.context_widget.setVisible(False)
            self._set_add_buttons_enabled(True, allow_requirement=True)
            return

        self.context_widget.setVisible(True)

        # Requirements are leaf nodes — disable all add buttons.
        if entity.entity_type == "requirement":
            self._set_add_buttons_enabled(False, allow_requirement=False)
        else:
            self._set_add_buttons_enabled(True, allow_requirement=True)

    def _set_add_buttons_enabled(self, enabled: bool, allow_requirement: bool):
        """Enable or disable the four add-child buttons.

        `allow_requirement` is a separate flag so we can disable ONLY
        the requirement button when the selected item is a requirement
        (leaf nodes cannot have children of any kind).
        """
        self.add_system_btn.setEnabled(enabled)
        self.add_subsystem_btn.setEnabled(enabled)
        self.add_element_btn.setEnabled(enabled)
        self.add_requirement_btn.setEnabled(allow_requirement and enabled)

    # ─────────────────────────────────────────────────────────────
    # Slot: Add Entity
    # ─────────────────────────────────────────────────────────────

    def _on_add_entity(self, entity_type: str):
        """Open the Add Entity dialog to create a new child entity.

        If a tree item is selected, the new entity becomes its child.
        Otherwise it is added directly under the project root.
        Requirements use a separate dialog (not built yet) — if the
        entity_type is 'requirement', we fall back to a simple name-only
        prompt until that dialog is ready.
        """
        # Determine the parent: selected item's entity, or the project root.
        selected = self._get_selected_entity()
        if selected is not None:
            parent_id = selected.id
            parent_name = selected.name
        else:
            parent_id = self._project.id
            parent_name = self._project.name

        info = ENTITY_DISPLAY.get(entity_type, {})
        type_label = info.get("label", entity_type)

        # Requirements use a specialised dialog with extra fields.
        if entity_type == "requirement":
            dialog = AddRequirementDialog(
                parent_id=parent_id,
                parent_name=parent_name,
                user_id=self._user.id,
                parent=self,
            )
            if dialog.exec() == QDialog.Accepted:
                self._refresh_tree_preserving_state()
            return

        # ── Full Add dialog for system / subsystem / element ─────
        dialog = AddEntityDialog(
            entity_type=entity_type,
            parent_id=parent_id,
            parent_name=parent_name,
            user_id=self._user.id,
            parent=self,
        )

        if dialog.exec() == QDialog.Accepted:
            self._refresh_tree_preserving_state()

    # ─────────────────────────────────────────────────────────────
    # Slot: Edit
    # ─────────────────────────────────────────────────────────────

    def _on_edit(self):
        """Open the appropriate Edit dialog for the selected tree item.

        Routes to EditRequirementDialog for requirements and
        EditEntityDialog for all other entity types.
        """
        entity = self._get_selected_entity()
        if entity is None:
            return

        if entity.entity_type == "requirement":
            # ── Requirement editor with full specialised fields ──
            dialog = EditRequirementDialog(
                entity=entity,
                user_id=self._user.id,
                parent=self,
            )
            if dialog.exec() == QDialog.Accepted and dialog.was_saved():
                self._refresh_tree_preserving_state()
            return

        # ── Standard editor for system / subsystem / element ─────
        dialog = EditEntityDialog(
            entity=entity,
            user_id=self._user.id,
            parent=self,
        )

        if dialog.exec() == QDialog.Accepted and dialog.was_saved():
            self._refresh_tree_preserving_state()

    # ─────────────────────────────────────────────────────────────
    # Slot: Delete (with cascade warning)
    # ─────────────────────────────────────────────────────────────

    def _on_delete(self):
        """Delete the selected entity after confirming with the user.

        If the entity has children, the warning message lists how many
        descendants will also be removed (via ON DELETE CASCADE).
        """
        current_item = self.tree.currentItem()
        if current_item is None:
            return
        entity = current_item.data(0, Qt.UserRole)
        if entity is None:
            return

        info = ENTITY_DISPLAY.get(entity.entity_type, {})
        type_label = info.get("label", entity.entity_type)
        descendant_count = _count_descendants(current_item)

        # ── Build the warning message ────────────────────────────
        message = (
            f"Are you sure you want to delete this {type_label}?\n\n"
            f"  Name:  {entity.name}\n"
            f"  Type:  {type_label}\n"
            f"  ID:      {entity.id}\n"
        )

        if descendant_count > 0:
            # Emphasise the cascade impact.
            message += (
                f"\n⚠️  WARNING: This will also permanently delete "
                f"{descendant_count} child entit{'y' if descendant_count == 1 else 'ies'} "
                f"(and all their links).\n"
            )

        message += "\nThis action cannot be undone."

        # ── Show the confirmation dialog ─────────────────────────
        reply = QMessageBox.warning(
            self,
            f"Delete {type_label}",
            message,
            QMessageBox.Yes | QMessageBox.Cancel,
            QMessageBox.Cancel,  # default button
        )

        if reply != QMessageBox.Yes:
            return

        # ── Perform the deletion ─────────────────────────────────
        try:
            ok = delete_entity(entity_id=entity.id, user_id=self._user.id)
        except Exception as exc:
            QMessageBox.critical(
                self, "Delete Failed",
                f"An error occurred while deleting:\n\n{exc}",
            )
            return

        if ok:
            # Refresh the tree to reflect the deletion.
            self._refresh_tree_preserving_state()
        else:
            QMessageBox.warning(self, "Delete Failed", "Entity not found in the database.")

    # ─────────────────────────────────────────────────────────────
    # Slot: History
    # ─────────────────────────────────────────────────────────────

    def _on_history(self):
        """Show the audit history dialog for the selected entity."""
        entity = self._get_selected_entity()
        if entity is None:
            return

        dialog = EntityHistoryDialog(entity, parent=self)
        dialog.exec()

    # ─────────────────────────────────────────────────────────────
    # Tree Refresh Utility
    # ─────────────────────────────────────────────────────────────

    def _refresh_tree_preserving_state(self):
        """Reload the tree from the database while preserving the
        currently expanded branches as closely as possible.

        Strategy: before reload, collect the IDs of all expanded items.
        After reload, re-expand items with matching IDs.
        """
        # Collect IDs of expanded items.
        expanded_ids = set()
        root = self.tree.invisibleRootItem()
        self._collect_expanded_ids(root, expanded_ids)

        # Also remember the selected item's ID (if any).
        selected_entity = self._get_selected_entity()
        selected_id = selected_entity.id if selected_entity else None

        # Reload from database.
        self._load_tree()

        # Restore expanded state.
        self._restore_expanded_ids(root, expanded_ids)

        # Restore selection.
        if selected_id is not None:
            self._select_item_by_entity_id(root, selected_id)

    def _collect_expanded_ids(self, item: QTreeWidgetItem, id_set: set):
        """Recursively collect entity IDs of expanded tree items."""
        for i in range(item.childCount()):
            child = item.child(i)
            if child.isExpanded():
                entity = child.data(0, Qt.UserRole)
                if entity:
                    id_set.add(entity.id)
            self._collect_expanded_ids(child, id_set)

    def _restore_expanded_ids(self, item: QTreeWidgetItem, id_set: set):
        """Recursively expand tree items whose entity IDs are in the set."""
        for i in range(item.childCount()):
            child = item.child(i)
            entity = child.data(0, Qt.UserRole)
            if entity and entity.id in id_set:
                child.setExpanded(True)
            self._restore_expanded_ids(child, id_set)

    def _select_item_by_entity_id(self, item: QTreeWidgetItem, entity_id: int) -> bool:
        """Recursively find and select the tree item matching the given entity ID.
        Returns True if found."""
        for i in range(item.childCount()):
            child = item.child(i)
            entity = child.data(0, Qt.UserRole)
            if entity and entity.id == entity_id:
                self.tree.setCurrentItem(child)
                return True
            if self._select_item_by_entity_id(child, entity_id):
                return True
        return False

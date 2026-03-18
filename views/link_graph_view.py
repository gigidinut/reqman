"""
link_graph_view.py — Force-directed graph visualisation of entity links.

Experimental feature: opens in a standalone window so it cannot
interfere with the main project workspace.

Uses QGraphicsScene / QGraphicsView with a simple force-directed
layout computed in Python (no external graph libraries required).
"""

import math
import random
from typing import Dict, List, Optional, Set, Tuple

from PySide6.QtCore import Qt, QTimer, QRectF, QPointF
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QPainter,
    QPen,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QGraphicsEllipseItem,
    QGraphicsItem,
    QGraphicsLineItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsSimpleTextItem,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from controllers.db_controllers import get_children, get_entity, get_linked_entities

# ═══════════════════════════════════════════════════════════════════
# CONSTANTS
# ═══════════════════════════════════════════════════════════════════

NODE_COLORS: Dict[str, str] = {
    "project":     "#6c5ce7",
    "system":      "#5dade2",
    "subsystem":   "#48c9b0",
    "element":     "#f5b041",
    "requirement": "#af7ac5",
}

NODE_ICONS: Dict[str, str] = {
    "project":     "\U0001f4c1",
    "system":      "\U0001f5a5\ufe0f",
    "subsystem":   "\U0001f527",
    "element":     "\u2699\ufe0f",
    "requirement": "\U0001f4cb",
}

NODE_W = 150
NODE_H = 38
CORNER_R = 10

# Force-directed layout parameters
REPULSION_K = 6000.0
ATTRACTION_K = 0.005
IDEAL_LENGTH = 200.0
DAMPING = 0.82
MIN_DIST = 30.0
MAX_VEL = 18.0
CENTER_GRAVITY = 0.001
CONVERGE_THRESHOLD = 0.3

TOOLBAR_STYLE = (
    "QPushButton { background: #3a3a4a; color: #ddd; border: 1px solid #555; "
    "border-radius: 4px; padding: 4px 12px; font-size: 12px; } "
    "QPushButton:hover { background: #4a4a5a; }"
)


# ═══════════════════════════════════════════════════════════════════
# DATA CONTAINERS
# ═══════════════════════════════════════════════════════════════════

class GraphNode:
    """Mutable layout state for a single node."""

    __slots__ = ("entity", "x", "y", "vx", "vy", "pinned")

    def __init__(self, entity, x: float = 0.0, y: float = 0.0):
        self.entity = entity
        self.x = x
        self.y = y
        self.vx = 0.0
        self.vy = 0.0
        self.pinned = False


# ═══════════════════════════════════════════════════════════════════
# GRAPHICS ITEMS
# ═══════════════════════════════════════════════════════════════════

class NodeItem(QGraphicsRectItem):
    """Visual rounded-rect node in the scene."""

    def __init__(self, gnode: GraphNode, on_click=None):
        super().__init__(0, 0, NODE_W, NODE_H)
        self.gnode = gnode
        self._on_click = on_click
        self._edges: List["EdgeItem"] = []  # edges connected to this node

        etype = gnode.entity.entity_type
        color = QColor(NODE_COLORS.get(etype, "#888888"))

        self.setBrush(QBrush(color))
        self.setPen(QPen(color.darker(130), 1.5))
        self.setFlag(QGraphicsItem.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.ItemSendsGeometryChanges, True)
        self.setCursor(Qt.OpenHandCursor)
        self.setZValue(1)

        # Label
        icon = NODE_ICONS.get(etype, "")
        name = gnode.entity.name
        if len(name) > 18:
            name = name[:17] + "\u2026"
        label = f"{icon} {name}"

        self._text = QGraphicsSimpleTextItem(label, self)
        text_font = QFont("Segoe UI", 9)
        text_font.setBold(True)
        self._text.setFont(text_font)
        self._text.setBrush(QBrush(QColor("#ffffff")))

        # Centre text in rect
        tr = self._text.boundingRect()
        tx = (NODE_W - tr.width()) / 2
        ty = (NODE_H - tr.height()) / 2
        self._text.setPos(tx, ty)

        self.setPos(gnode.x - NODE_W / 2, gnode.y - NODE_H / 2)

    def paint(self, painter, option, widget=None):
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setBrush(self.brush())
        painter.setPen(self.pen())
        painter.drawRoundedRect(self.rect(), CORNER_R, CORNER_R)

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionHasChanged:
            self.gnode.x = value.x() + NODE_W / 2
            self.gnode.y = value.y() + NODE_H / 2
            self.gnode.pinned = True
            # Keep connected edges in sync while dragging
            for edge in self._edges:
                edge.update_positions()
        return super().itemChange(change, value)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.setCursor(Qt.ClosedHandCursor)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        self.setCursor(Qt.OpenHandCursor)
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if self._on_click:
            self._on_click(self.gnode.entity)
        event.accept()

    def sync_from_gnode(self):
        """Push gnode position into the item (used by simulation ticks)."""
        self.setPos(self.gnode.x - NODE_W / 2, self.gnode.y - NODE_H / 2)


class EdgeItem(QGraphicsLineItem):
    """Visual edge between two NodeItems."""

    def __init__(self, src: NodeItem, dst: NodeItem):
        super().__init__()
        self.src = src
        self.dst = dst
        self.setPen(QPen(QColor(160, 160, 180, 100), 1.5))
        self.setZValue(0)
        self.update_positions()

    def update_positions(self):
        sx = self.src.gnode.x
        sy = self.src.gnode.y
        dx = self.dst.gnode.x
        dy = self.dst.gnode.y
        self.setLine(sx, sy, dx, dy)


# ═══════════════════════════════════════════════════════════════════
# CUSTOM VIEW (zoom + pan)
# ═══════════════════════════════════════════════════════════════════

class LinkGraphView(QGraphicsView):
    """QGraphicsView with mouse-wheel zoom and rubber-band selection disabled."""

    def __init__(self, scene, parent=None):
        super().__init__(scene, parent)
        self.setRenderHint(QPainter.Antialiasing)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorUnderMouse)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setViewportUpdateMode(QGraphicsView.SmartViewportUpdate)
        self.setBackgroundBrush(QBrush(QColor("#1e1e2e")))
        self._zoom = 1.0

    def wheelEvent(self, event: QWheelEvent):
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        new_zoom = self._zoom * factor
        if 0.1 < new_zoom < 6.0:
            self._zoom = new_zoom
            self.scale(factor, factor)
        event.accept()


# ═══════════════════════════════════════════════════════════════════
# MAIN GRAPH WINDOW
# ═══════════════════════════════════════════════════════════════════

class LinkGraphWindow(QMainWindow):
    """Standalone window showing the force-directed entity link graph."""

    def __init__(self, project, parent=None):
        super().__init__(parent)
        self._project = project

        self.setWindowTitle(f"\U0001f517 Link Graph \u2014 {project.name}")
        self.setMinimumSize(900, 650)
        self.setAttribute(Qt.WA_DeleteOnClose)

        # Data structures
        self.nodes: Dict[int, GraphNode] = {}
        self.edges: List[Tuple[int, int]] = []
        self.node_items: Dict[int, NodeItem] = {}
        self.edge_items: List[EdgeItem] = []

        self._collect_graph_data()
        self._build_ui()
        self._populate_scene()
        self._start_simulation()

    # ─────────────────────────────────────────────────────────────
    # Data collection
    # ─────────────────────────────────────────────────────────────

    def _collect_graph_data(self):
        """Recursively collect all entities and links in the project."""
        all_entities: Dict[int, object] = {}
        self._collect_entities_recursive(self._project.id, all_entities)

        # Build nodes with random initial positions in a circle
        n = max(len(all_entities), 1)
        radius = math.sqrt(n) * 100
        for i, (eid, entity) in enumerate(all_entities.items()):
            angle = 2 * math.pi * i / n
            x = radius * math.cos(angle) + random.uniform(-30, 30)
            y = radius * math.sin(angle) + random.uniform(-30, 30)
            self.nodes[eid] = GraphNode(entity, x, y)

        # Collect all unique edges (deduplicated)
        edge_set: Set[Tuple[int, int]] = set()
        entity_ids = set(all_entities.keys())
        for eid in entity_ids:
            linked = get_linked_entities(eid, direction="both")
            for le in linked:
                if le.id in entity_ids:
                    pair = (min(eid, le.id), max(eid, le.id))
                    edge_set.add(pair)
        self.edges = list(edge_set)

    def _collect_entities_recursive(self, parent_id: int, result: Dict[int, object]):
        children = get_children(parent_id)
        for child in children:
            result[child.id] = child
            if child.entity_type != "requirement":
                self._collect_entities_recursive(child.id, result)

    # ─────────────────────────────────────────────────────────────
    # UI
    # ─────────────────────────────────────────────────────────────

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Toolbar
        toolbar = QWidget()
        toolbar.setStyleSheet("background: #2a2a3a; padding: 4px;")
        tb_layout = QHBoxLayout(toolbar)
        tb_layout.setContentsMargins(10, 4, 10, 4)

        self._reset_btn = QPushButton("\u21bb  Reset Layout")
        self._reset_btn.setStyleSheet(TOOLBAR_STYLE)
        self._reset_btn.setCursor(Qt.PointingHandCursor)
        self._reset_btn.clicked.connect(self._on_reset_layout)
        tb_layout.addWidget(self._reset_btn)

        tb_layout.addSpacing(20)

        # Legend
        for etype, color in NODE_COLORS.items():
            swatch = QLabel(f"  \u25a0 {etype.capitalize()}")
            swatch.setStyleSheet(f"color: {color}; font-size: 11px; font-weight: bold;")
            tb_layout.addWidget(swatch)

        tb_layout.addStretch(1)

        info = QLabel(f"{len(self.nodes)} nodes \u00b7 {len(self.edges)} links")
        info.setStyleSheet("color: #999; font-size: 11px;")
        tb_layout.addWidget(info)

        layout.addWidget(toolbar)

        # Scene + View
        self._scene = QGraphicsScene()
        self._view = LinkGraphView(self._scene)
        layout.addWidget(self._view, stretch=1)

        # Empty state
        if not self.nodes:
            msg = QGraphicsSimpleTextItem("No linked entities found in this project.")
            msg.setFont(QFont("Segoe UI", 14))
            msg.setBrush(QBrush(QColor("#888")))
            self._scene.addItem(msg)

    def _populate_scene(self):
        """Create NodeItems and EdgeItems from collected data."""
        for eid, gnode in self.nodes.items():
            ni = NodeItem(gnode, on_click=self._on_node_clicked)
            self._scene.addItem(ni)
            self.node_items[eid] = ni

        for src_id, dst_id in self.edges:
            src_ni = self.node_items.get(src_id)
            dst_ni = self.node_items.get(dst_id)
            if src_ni and dst_ni:
                ei = EdgeItem(src_ni, dst_ni)
                self._scene.addItem(ei)
                self.edge_items.append(ei)
                # Register the edge on both nodes for drag updates
                src_ni._edges.append(ei)
                dst_ni._edges.append(ei)

    # ─────────────────────────────────────────────────────────────
    # Force-directed simulation
    # ─────────────────────────────────────────────────────────────

    def _start_simulation(self):
        self._sim_timer = QTimer(self)
        self._sim_timer.timeout.connect(self._tick)
        self._sim_timer.start(30)
        self._tick_count = 0

    def _tick(self):
        self._tick_count += 1
        nodes = list(self.nodes.values())
        n = len(nodes)
        if n == 0:
            self._sim_timer.stop()
            return

        # Accumulate forces
        fx: Dict[int, float] = {nd.entity.id: 0.0 for nd in nodes}
        fy: Dict[int, float] = {nd.entity.id: 0.0 for nd in nodes}

        # Repulsion (all pairs)
        for i in range(n):
            for j in range(i + 1, n):
                a, b = nodes[i], nodes[j]
                dx = a.x - b.x
                dy = a.y - b.y
                dist = max(math.sqrt(dx * dx + dy * dy), MIN_DIST)
                force = REPULSION_K / (dist * dist)
                ux, uy = dx / dist, dy / dist
                fx[a.entity.id] += ux * force
                fy[a.entity.id] += uy * force
                fx[b.entity.id] -= ux * force
                fy[b.entity.id] -= uy * force

        # Attraction (edges)
        for src_id, dst_id in self.edges:
            a = self.nodes.get(src_id)
            b = self.nodes.get(dst_id)
            if not a or not b:
                continue
            dx = b.x - a.x
            dy = b.y - a.y
            dist = max(math.sqrt(dx * dx + dy * dy), MIN_DIST)
            force = ATTRACTION_K * (dist - IDEAL_LENGTH)
            ux, uy = dx / dist, dy / dist
            fx[a.entity.id] += ux * force
            fy[a.entity.id] += uy * force
            fx[b.entity.id] -= ux * force
            fy[b.entity.id] -= uy * force

        # Centre gravity + velocity update
        total_kinetic = 0.0
        for nd in nodes:
            eid = nd.entity.id
            if nd.pinned:
                nd.vx = 0.0
                nd.vy = 0.0
                continue
            fx[eid] -= CENTER_GRAVITY * nd.x
            fy[eid] -= CENTER_GRAVITY * nd.y
            nd.vx = (nd.vx + fx[eid]) * DAMPING
            nd.vy = (nd.vy + fy[eid]) * DAMPING
            # Clamp
            speed = math.sqrt(nd.vx ** 2 + nd.vy ** 2)
            if speed > MAX_VEL:
                nd.vx = nd.vx / speed * MAX_VEL
                nd.vy = nd.vy / speed * MAX_VEL
            nd.x += nd.vx
            nd.y += nd.vy
            total_kinetic += nd.vx ** 2 + nd.vy ** 2

        # Sync visuals
        for eid, ni in self.node_items.items():
            ni.sync_from_gnode()
        for ei in self.edge_items:
            ei.update_positions()

        # Auto-stop when converged or after enough ticks
        if total_kinetic < CONVERGE_THRESHOLD or self._tick_count > 800:
            self._sim_timer.stop()

    # ─────────────────────────────────────────────────────────────
    # Toolbar actions
    # ─────────────────────────────────────────────────────────────

    def _on_reset_layout(self):
        n = max(len(self.nodes), 1)
        radius = math.sqrt(n) * 100
        for i, gnode in enumerate(self.nodes.values()):
            angle = 2 * math.pi * i / n
            gnode.x = radius * math.cos(angle) + random.uniform(-30, 30)
            gnode.y = radius * math.sin(angle) + random.uniform(-30, 30)
            gnode.vx = 0.0
            gnode.vy = 0.0
            gnode.pinned = False
        self._tick_count = 0
        self._sim_timer.start(30)

    # ─────────────────────────────────────────────────────────────
    # Node interaction
    # ─────────────────────────────────────────────────────────────

    def _on_node_clicked(self, entity):
        """Open EntityViewerWindow for the double-clicked node."""
        fresh = get_entity(entity.id)
        if fresh is None:
            return
        # Import here to avoid circular import
        from views.project_view import EntityViewerWindow
        viewer = EntityViewerWindow(fresh, parent=self)
        viewer.show()

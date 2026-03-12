import sys
import os
import re
import json
from datetime import datetime
import xml.etree.ElementTree as ET

import math

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QPushButton, QVBoxLayout, QWidget, QHBoxLayout,
    QFrame, QScrollArea, QLabel, QFileDialog, QCheckBox, QMessageBox,
    QGridLayout, QGraphicsScene, QColorDialog, QLineEdit, QToolTip, QSlider,
    QTreeWidget, QTreeWidgetItem, QAbstractItemView, QInputDialog, QMenu, QSizePolicy, QSplitter,
    QSpinBox, QDialog, QListWidget, QDialogButtonBox,
)
from PyQt6.QtCore import Qt, QPointF, QRectF, QTimer, QLineF, QEvent, QSize
from PyQt6.QtGui import QColor, QPen, QPixmap, QPainter, QFont, QIcon, QPolygonF, QTransform, QTextCharFormat, QBrush, QImage, QShortcut, QKeySequence
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtSvgWidgets import QGraphicsSvgItem

from elements import DraggableElement, LaserPath, CanvasTextItem
from view import CustomGraphicsView
from dialogs import CollapsibleCategory
from holes import HoleManagerDialog
from canvas import CanvasState, CanvasNode, LAYER, GROUP, ITEM

class LayersTreeWidget(QTreeWidget):
    """QTreeWidget subclass that intercepts drag-drop to reorder the CanvasState tree."""

    def __init__(self, main_app, parent=None):
        super().__init__(parent)
        self.main_app = main_app
        self.setHeaderHidden(True)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked)
        self.setStyleSheet(
            "QTreeWidget { border: none; background: transparent; font-size: 12pt; }"
            "QTreeWidget::item { padding: 5px 2px; }"
            "QTreeWidget::item:selected { background: #CCE5FF; color: black; }"
        )
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)

    def dropEvent(self, event):
        """After Qt moves the visual rows, sync the CanvasState order to match.

        We do NOT call refresh_layers_panel here — the widget already shows
        the correct post-drop order (Qt moved the rows itself).  Calling a
        full rebuild would duplicate rows.  We only sync the data model.
        """
        # Capture the dragged item's node before the drop moves it
        dragged_wi = self.currentItem()

        # Find drop target and position so we can handle "above first row"
        drop_indicator = self.dropIndicatorPosition()
        # QDropEvent in Qt6 uses position() not pos()
        pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
        target_wi = self.itemAt(pos)

        super().dropEvent(event)

        # If the drop landed above the first top-level row Qt sometimes refuses
        # to move it (the item stays where it was).  Force it to index 0.
        if (dragged_wi and target_wi
                and drop_indicator == QAbstractItemView.DropIndicatorPosition.AboveItem
                and self.indexOfTopLevelItem(target_wi) == 0):
            # Qt may not have moved it — do it explicitly
            root = self.invisibleRootItem()
            idx = self.indexOfTopLevelItem(dragged_wi)
            if idx > 0:
                root.takeChild(idx)
                root.insertChild(0, dragged_wi)
                self.setCurrentItem(dragged_wi)

        self.main_app._sync_tree_from_widget()

    def mousePressEvent(self, event):
        super().mousePressEvent(event)
        item = self.itemAt(event.pos())
        if item:
            node = self.main_app._node_from_item(item)
            if node and node.node_type == LAYER:
                self.main_app._active_layer_name = node.name
                self.main_app._refresh_active_layer_display()


class LegoDesigner(QMainWindow):
    """
    Main application window for the Lego-style optical breadboard designer.
    Handles:
      - UI layout (sidebar, toolbar, overlays)
      - Scene and breadboard
      - Snapping, undo/redo, layout save/load
      - Integration with hole editor JSON database
    """
    def __init__(self):
        super().__init__()

        # ---------- Core state ----------
        self._is_loading = False
        self.breadboard_holes = []
        self.draw_mode = False
        self.eraser_mode = False
        self.undo_stack = []
        self.redo_stack = []
        self.current_laser_color = QColor(255, 0, 0, 180)
        self._clipboard = []     # list of dicts for copied items
        self._canvas_rotation = 0  # current canvas rotation in degrees

        # Hierarchical layer tree (mirrors the flat QGraphicsScene)
        # Text is always on top (index 0), then Laser Paths, then Elements.
        self.canvas_state = CanvasState()
        self.canvas_state.add_layer("Text")
        self.canvas_state.add_layer("Laser Paths")
        self.canvas_state.add_layer("Elements")
        self._active_layer_name = "Elements"

        self.setWindowTitle("Lego Designer")
        self.resize(1300, 900)

        self.base_dir = os.path.dirname(os.path.abspath(__file__))
        self.icons_root = os.path.join(self.base_dir, "icons")

        # ---------- Global stylesheet ----------
        self.setStyleSheet("""
            QMainWindow { background-color: #F0F0F0; }
            QPushButton#ToolBtn {
                background: white;
                color: black;
                border: 1px solid #999;
                border-radius: 16px;
                font-size: 14pt;
            }
            QPushButton#ToolBtn:checked {
                background: #CCE4F7;
                border-color: #0078D7;
            }
            QFrame#Toolbar {
                background: #E0E0E0;
                border-bottom: 1px solid #AAA;
            }
            QToolTip {
                background-color: white;
                color: #666666;
                border: 1px solid #666666;
                border-radius: 8px;
                padding: 0px 2px;
                font-size: 12pt;
            }
        """)

        # ---------- Main layout ----------
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        self.main_layout = QHBoxLayout(main_widget)
        self.main_layout.setContentsMargins(0, 0, 0, 0)

        # =========================================================
        #   SIDEBAR (categories + SEARCH)
        # =========================================================
        sidebar = QFrame()
        sidebar.setFixedWidth(280)
        sidebar.setStyleSheet("background: #E0E0E0; border-right: 1px solid #AAA;")
        side_layout = QVBoxLayout(sidebar)
        self._side_layout = side_layout
        side_layout.setContentsMargins(8, 8, 8, 8)
        side_layout.setSpacing(6)

        # --- Inventory panel (header inside same box) ---
        self._inventory_content = QFrame()
        self._inventory_content.setMinimumWidth(260)
        self._inventory_content.setStyleSheet(
            "background: #FFFFFF; border: none; border-radius: 6px;"
        )
        inv_layout = QVBoxLayout(self._inventory_content)
        inv_layout.setContentsMargins(8, 8, 8, 8)
        inv_layout.setSpacing(6)

        self._btn_inventory_toggle = QPushButton("▾ Inventory")
        self._btn_inventory_toggle.setCheckable(True)
        self._btn_inventory_toggle.setChecked(True)
        self._btn_inventory_toggle.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._btn_inventory_toggle.setStyleSheet(
            """
            QPushButton {
                text-align: left;
                font-weight: bold;
                font-size: 14pt;
                background: transparent;
                border: none;
                height: 28px;
                padding-top: 0px;
                padding-bottom: 4px;
                padding-left: 8px;
            }
            """
        )
        inv_layout.addWidget(self._btn_inventory_toggle)

        self._inventory_body = QWidget()
        inv_body_layout = QVBoxLayout(self._inventory_body)
        inv_body_layout.setContentsMargins(0, 0, 0, 0)
        inv_body_layout.setSpacing(4)

        search_layout = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search parts...")
        self.search_input.setStyleSheet("padding: 5px; border-radius: 3px; border: 1px solid #AAA;")
        self.search_input.textChanged.connect(self.filter_sidebar)
        search_layout.addWidget(self.search_input)
        inv_body_layout.addLayout(search_layout)

        self.no_results_label = QLabel("Nothing found")
        self.no_results_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.no_results_label.setStyleSheet("color: gray; font-style: italic; margin-top: 20px;")
        self.no_results_label.hide()
        inv_body_layout.addWidget(self.no_results_label)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("border: none;")
        self.acc_layout = QVBoxLayout()
        self.acc_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        content = QWidget()
        content.setLayout(self.acc_layout)
        scroll.setWidget(content)
        inv_body_layout.addWidget(scroll)
        inv_layout.addWidget(self._inventory_body)

        # --- Layers host (actual panel built in setup_overlays) ---
        # Resizable split between inventory and layers
        # _layers_panel is added directly to the splitter in setup_overlays
        self._layers_host = None  # unused; kept for compat references
        self._left_splitter = QSplitter(Qt.Orientation.Vertical)
        self._left_splitter.setChildrenCollapsible(False)
        self._left_splitter.addWidget(self._inventory_content)
        self._left_splitter.setStretchFactor(0, 1)
        self._left_splitter.setStretchFactor(1, 1)
        side_layout.addWidget(self._left_splitter, 1)
        side_layout.addStretch(1)
        self._left_splitter_ratio_applied = False

        def _update_splitter_stretch():
            """When both collapsed: splitter at top (stretch 0). When either expanded: splitter fills (stretch 1)."""
            if not hasattr(self, "_left_splitter") or not hasattr(self, "_side_layout"):
                return
            # Use button state, not body visibility: isVisible() can be False before window is shown
            inv_expanded = self._btn_inventory_toggle.isChecked()
            layers_expanded = self._btn_layers_toggle.isChecked() if hasattr(self, "_btn_layers_toggle") else False
            both_collapsed = not inv_expanded and not layers_expanded
            # splitter is index 0, stretch is index 1 in side_layout
            if both_collapsed:
                self._side_layout.setStretch(0, 0)
                self._side_layout.setStretch(1, 1)
            else:
                self._side_layout.setStretch(0, 1)
                self._side_layout.setStretch(1, 0)
        self._update_splitter_stretch = _update_splitter_stretch

        def _toggle_inventory(opened: bool):
            """Show/hide inventory body and adjust splitter so only header remains when closed."""
            self._inventory_body.setVisible(opened)
            self._btn_inventory_toggle.setText(("▾ " if opened else "▸ ") + "Inventory")

            if not hasattr(self, "_left_splitter"):
                return

            if opened:
                # Restore flexible height
                self._inventory_content.setSizePolicy(QSizePolicy.Policy.Preferred,
                                                      QSizePolicy.Policy.Expanding)
                self._inventory_content.setMaximumHeight(16777215)
                self._left_splitter.setMaximumHeight(16777215)
                # Back to roughly 50/50
                self._left_splitter.setSizes([1000, 1000])
            else:
                # Collapse so only the header is visible
                header_h = self._btn_inventory_toggle.sizeHint().height() + 12
                self._inventory_content.setSizePolicy(QSizePolicy.Policy.Preferred,
                                                      QSizePolicy.Policy.Fixed)
                self._inventory_content.setMaximumHeight(header_h)
                # When both collapsed, keep splitter at top by limiting its height
                layers_collapsed = not self._layers_body.isVisible()
                if layers_collapsed:
                    layers_header_h = self._btn_layers_toggle.sizeHint().height() + 12
                    self._left_splitter.setMaximumHeight(header_h + layers_header_h)
                    self._left_splitter.setSizes([header_h, layers_header_h])
                else:
                    self._left_splitter.setMaximumHeight(16777215)
                    self._left_splitter.setSizes([header_h, 1000])
            self._update_splitter_stretch()

        self._btn_inventory_toggle.toggled.connect(_toggle_inventory)

        self.main_layout.addWidget(sidebar)

        # =========================================================
        #   VIEW + TOOLBAR
        # =========================================================
        view_container = QWidget()
        self.view_layout = QVBoxLayout(view_container)
        self.view_layout.setContentsMargins(0, 0, 0, 0)

        # ----- Toolbar -----
        self.toolbar = QFrame()
        self.toolbar.setObjectName("Toolbar")
        self.toolbar.setFixedHeight(60)
        tl = QHBoxLayout(self.toolbar)

        for sym, func, tooltip in [
            ("💾", self.export_svg, "Save as SVG"),
            ("OPEN_DROPDOWN", None, "Open"),
            ("↩️", self.undo_action, "Undo (Ctrl+Z)"),
            ("↪️", self.redo_action, "Redo (Ctrl+Y)"),
            ("🧹", self.clear_screen, "Clear All"),
        ]:
            if sym == "OPEN_DROPDOWN":
                open_btn = QPushButton("📥")
                open_btn.setObjectName("ToolBtn")
                open_btn.setFixedSize(50, 32)
                open_btn.setToolTip("Open")
                open_btn.setToolTipDuration(0)
                open_btn.installEventFilter(self)
                open_menu = QMenu()
                open_menu.addAction("Open SVG...", self.import_svg)
                open_menu.addAction("Open from autosave", self.open_from_autosave)
                open_menu.setStyleSheet(
                    "QMenu { background: white; border: 1px solid #ccc; border-radius: 4px; padding: 4px 0; font-size: 12pt; }"
                    "QMenu::item { padding: 4px 20px; }"
                    "QMenu::item:selected { background: #e0e0e0; }"
                )
                open_btn.clicked.connect(
                    lambda checked=False, btn=open_btn, m=open_menu: m.popup(btn.mapToGlobal(btn.rect().bottomLeft()))
                )
                tl.addWidget(open_btn)
                continue
            b = QPushButton(sym)
            b.setObjectName("ToolBtn")
            b.setFixedSize(50, 32)
            b.setToolTip(tooltip)
            b.setToolTipDuration(0) # Show until mouse moves away
            b.installEventFilter(self)
            b.clicked.connect(func)
            tl.addWidget(b)

        tl.addSpacing(25)

        # Select / draw / eraser
        self.btn_sel = QPushButton("👆")
        self.btn_sel.setObjectName("ToolBtn")
        self.btn_sel.setFixedSize(50, 32)
        self.btn_sel.setCheckable(True)
        self.btn_sel.setChecked(True)
        self.btn_sel.setToolTip("Select Tool (Space)")
        self.btn_sel.setToolTipDuration(0)
        self.btn_sel.installEventFilter(self)
        self.btn_sel.clicked.connect(self.toggle_select)
        tl.addWidget(self.btn_sel)

        self.btn_draw = QPushButton("🖍️")
        self.btn_draw.setObjectName("ToolBtn")
        self.btn_draw.setFixedSize(50, 32)
        self.btn_draw.setCheckable(True)
        self.btn_draw.setToolTip("Laser Path Tool")
        self.btn_draw.setToolTipDuration(0)
        self.btn_draw.installEventFilter(self)
        self.btn_draw.clicked.connect(self.toggle_draw)
        tl.addWidget(self.btn_draw)

        self.btn_eraser = QPushButton("🧽")
        self.btn_eraser.setObjectName("ToolBtn")
        self.btn_eraser.setFixedSize(50, 32)
        self.btn_eraser.setCheckable(True)
        self.btn_eraser.setToolTip("Eraser Tool")
        self.btn_eraser.setToolTipDuration(0)
        self.btn_eraser.installEventFilter(self)
        self.btn_eraser.clicked.connect(self.toggle_eraser)
        tl.addWidget(self.btn_eraser)

        self.btn_text = QPushButton("𝐓")
        self.btn_text.setObjectName("ToolBtn")
        self.btn_text.setFixedSize(50, 32)
        self.btn_text.setIconSize(QSize(22, 22))
        self.btn_text.setToolTip("Add Text Box")
        self.btn_text.setToolTipDuration(0)
        self.btn_text.installEventFilter(self)
        self.btn_text.clicked.connect(self.add_textbox)
        tl.addWidget(self.btn_text)

        tl.addStretch()

        # Breadboard choice (left of Review Holes)
        self.btn_breadboard = QPushButton("📋 Breadboard")
        self.btn_breadboard.setObjectName("ToolBtn")
        self.btn_breadboard.setFixedSize(120, 32)
        self.btn_breadboard.setToolTip("Choose breadboard")
        self.btn_breadboard.setToolTipDuration(0)
        self.btn_breadboard.installEventFilter(self)
        self.btn_breadboard.clicked.connect(self.open_choose_breadboard_dialog)
        tl.addWidget(self.btn_breadboard)

        # Hole manager (rightmost on ribbon)
        self.btn_review = QPushButton("🔍 Review Holes")
        self.btn_review.setObjectName("ToolBtn")
        self.btn_review.setFixedSize(140, 32)
        self.btn_review.setToolTip("Review and Edit Part Holes")
        self.btn_review.setToolTipDuration(0)
        self.btn_review.installEventFilter(self)
        self.btn_review.clicked.connect(self.open_hole_manager)
        tl.addWidget(self.btn_review)
        self.view_layout.addWidget(self.toolbar)

        # ----- Scene + view -----
        self.scene = QGraphicsScene()
        self.scene.setBackgroundBrush(QBrush(QColor("white")))
        self.view = CustomGraphicsView(self.scene, self)
        self.view.setStyleSheet("background: white; border: none;")
        self.view_layout.addWidget(self.view)

        self.main_layout.addWidget(view_container)

        # =========================================================
        #   BREADBOARD + HOLES
        # =========================================================
        self._breadboard_path = os.path.join(self.base_dir, "Breadboards", "Lego_Board_275x350_M3_M6.svg")
        self.breadboard = QGraphicsSvgItem(self._breadboard_path)
        self.breadboard.setZValue(-1)
        self.breadboard.setCacheMode(QGraphicsSvgItem.CacheMode.DeviceCoordinateCache)
        self.scene.addItem(self.breadboard)

        self.detect_breadboard_holes()
        self.view.breadboard_holes = self.breadboard_holes

        self._generate_fine_grid()  
        #print(self.fine_grid_points)

        # =========================================================
        #   OVERLAYS (zoom, minimap, nudge)
        # =========================================================
        self.setup_overlays()
        self.view.setSceneRect(self.breadboard.boundingRect())

        # =========================================================
        #   ICON CATEGORIES
        # =========================================================
        self.categories = {}
        if os.path.exists(self.icons_root):
            for cat in sorted(os.listdir(self.icons_root)):
                p = os.path.join(self.icons_root, cat)
                if os.path.isdir(p):
                    imgs = [
                        (os.path.join(p, f), f)
                        for f in os.listdir(p)
                        if f.lower().endswith(".svg")
                    ]
                    self.categories[cat] = imgs
                    self.acc_layout.addWidget(CollapsibleCategory(cat, imgs, self))

        # =========================================================
        #   MINIMAP TIMER + AUTOSAVE TIMER + INITIAL STATE
        # =========================================================
        self.map_timer = QTimer()
        self.map_timer.timeout.connect(self.update_minimap)
        self.map_timer.start(33)

        self.autosave_dir = os.path.join(self.base_dir, "autosave")
        self._autosave_timer = QTimer()
        self._autosave_timer.timeout.connect(self._do_autosave)
        self._autosave_timer.start(120_000)  # 2 minutes

        self.set_laser_color("#FF0000")
        self.save_undo_state(initial=True)
        QShortcut(QKeySequence.StandardKey.ZoomIn, self, activated=lambda: self.view.scale(1.2, 1.2))
        QShortcut(QKeySequence.StandardKey.ZoomOut, self, activated=lambda: self.view.scale(1 / 1.2, 1 / 1.2))

    # =============================================================
    #   OVERLAYS
    # =============================================================
    def setup_overlays(self):
        # ----- Nudge box -----
        self.nudge_box = QFrame(self.view)
        self.nudge_box.setFixedSize(145, 145)
        self.nudge_box.hide()
        self.nudge_box.setStyleSheet(
            "background-color: rgba(128, 128, 128, 128); "
            "border: 2px solid #555; border-radius: 10px;"
        )

        grid = QGridLayout(self.nudge_box)
        grid.setSpacing(2)
        nudge_style = (
            "background: white; border: 1px solid #333; "
            "border-radius: 5px; font-size: 24pt; font-weight: bold;"
        )

        directions = [
            ("◸", 0, 0, -1, -1), ("△", 0, 1, 0, -1), ("◹", 0, 2, 1, -1),
            ("◁", 1, 0, -1,  0), ("↺", 1, 1, 0,  0), ("▷", 1, 2, 1,  0),
            ("◺", 2, 0, -1,  1), ("▽", 2, 1, 0,  1), ("◿", 2, 2, 1,  1),
        ]

        for sym, r, c, dx, dy in directions:
            btn = QPushButton(sym)
            btn.setFixedSize(40, 40)
            btn.setStyleSheet(nudge_style)
	
            if sym == "↺":
                btn.clicked.connect(self.rotate_selected_90)
            else:
                btn.clicked.connect(lambda ch, x=dx, y=dy: self.nudge_selected(x, y))
            grid.addWidget(btn, r, c)

        # Center rotate button (duplicate, but keeps layout explicit)
        #rot_btn = QPushButton("🔄")
        #rot_btn.setFixedSize(38, 38)
        #rot_btn.setStyleSheet(nudge_style)
        #rot_btn.clicked.connect(self.rotate_selected_90)
        #grid.addWidget(rot_btn, 1, 1)

        # ----- Zoom / delete overlay -----
        self.overlay = QWidget(self.view)
        ol = QVBoxLayout(self.overlay)
        ol.setContentsMargins(0, 0, 0, 0)

        self.btn_plus = QPushButton("+")
        self.btn_minus = QPushButton("-")
        self.btn_rotate_overlay = QPushButton("↻")
        self.btn_del_btn = QPushButton("🗑")

        style = (
            "background: rgba(255,255,255,180); border: 1px solid #999; "
            "border-radius: 22px; font-weight: bold; font-size: 22pt;"
        )
        for b in [self.btn_plus, self.btn_minus, self.btn_rotate_overlay, self.btn_del_btn]:
            b.setStyleSheet(style)
            b.setFixedSize(45, 45)

        self.btn_plus.clicked.connect(lambda: self.view.scale(1.2, 1.2))
        self.btn_minus.clicked.connect(lambda: self.view.scale(1 / 1.2, 1 / 1.2))
        self.btn_rotate_overlay.clicked.connect(self.rotate_canvas_90)
        self.btn_del_btn.clicked.connect(self.delete_selected)

        ol.addWidget(self.btn_plus)
        ol.addWidget(self.btn_minus)
        ol.addWidget(self.btn_rotate_overlay)
        ol.addWidget(self.btn_del_btn)

        # ----- Minimap (container with slit button to move left/right) -----
        self._minimap_at_left = False
        self._minimap_container = QWidget(self.view)
        self._minimap_container.setFixedSize(238, 160)
        minimap_layout = QHBoxLayout(self._minimap_container)
        minimap_layout.setContentsMargins(0, 0, 0, 0)
        minimap_layout.setSpacing(0)

        self._minimap_slit_btn = QPushButton("‹")
        self._minimap_slit_btn.setFixedSize(18, 160)
        self._minimap_slit_btn.setStyleSheet(
            "background: #E8E8E8; border: 1px solid #CCC; font-size: 14pt; color: #555;"
        )
        self._minimap_slit_btn.setToolTip("Move minimap to bottom left")
        self._minimap_slit_btn.clicked.connect(self._toggle_minimap_side)

        self.minimap = QLabel(self._minimap_container)
        self.minimap.setFixedSize(220, 160)
        self.minimap.setStyleSheet("background: #D0D0D0; border: 2px solid #999;")

        minimap_layout.addWidget(self._minimap_slit_btn)
        minimap_layout.addWidget(self.minimap)

        self.reposition_overlays()
        self.scene.selectionChanged.connect(self._on_selection_changed)

        # ----- Pen options overlay -----
        self.pen_options_box = QFrame(self)
        self.pen_options_box.setVisible(False)
        self.pen_options_box.setStyleSheet(
            "background: white; border: 1px solid #AAA; border-radius: 8px;"
        )
        self.pen_options_box.setFixedWidth(280)
        self.pen_options_box.setFixedHeight(85)

        pob_main_layout = QVBoxLayout(self.pen_options_box)
        pob_main_layout.setContentsMargins(12, 8, 12, 8)
        pob_main_layout.setSpacing(6)

        # Top row: Colors (Left) and Arrow (Right)
        top_row = QHBoxLayout()
        
        # Colors on the left
        self.color_preview = QPushButton()
        self.color_preview.setFixedSize(24, 24)
        self.color_preview.setToolTip("Custom Color Picker")
        self.color_preview.clicked.connect(self.pick_color)
        top_row.addWidget(self.color_preview)
        top_row.addSpacing(5)

        for c, name in [("#FF0000", "Red"), ("#00FF00", "Green"), ("#0000FF", "Blue")]:
            cb = QPushButton()
            cb.setFixedSize(20, 20)
            cb.setToolTip(f"Solid {name}")
            cb.setStyleSheet(f"background: {c}; border-radius: 10px; border: 1px solid #999;")
            cb.clicked.connect(lambda ch, col=c: self.set_laser_color_solid(col))
            top_row.addWidget(cb)
            top_row.addSpacing(2)

        top_row.addStretch()

        # Arrow on the right
        self.arrow_check = QCheckBox("Arrow")
        self.arrow_check.setChecked(True)
        self.arrow_check.toggled.connect(self._on_arrow_toggled)
        top_row.addWidget(self.arrow_check)
        
        pob_main_layout.addLayout(top_row)

        # Bottom row: Opacity Slider + Input
        bot_row = QHBoxLayout()
        bot_row.addWidget(QLabel("Opacity:"))
        
        self.opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self.opacity_slider.setRange(20, 255)
        self.opacity_slider.setValue(255) # Default to solid
        self.opacity_slider.setToolTip("Adjust Laser Path Opacity")
        self.opacity_slider.valueChanged.connect(self.update_laser_opacity_from_slider)
        bot_row.addWidget(self.opacity_slider)

        self.opacity_input = QLineEdit("100")
        self.opacity_input.setFixedWidth(35)
        self.opacity_input.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.opacity_input.setToolTip("Enter opacity (0-100%)")
        self.opacity_input.setStyleSheet("border: 1px solid #AAA; border-radius: 3px; padding: 1px;")
        self.opacity_input.textChanged.connect(self.update_laser_opacity_from_input)
        bot_row.addWidget(self.opacity_input)
        
        percent_label = QLabel("%")
        percent_label.setStyleSheet("border: none; background: transparent; padding: 0px;")
        bot_row.addWidget(percent_label)
        
        pob_main_layout.addLayout(bot_row)

        # ----- Text options overlay -----
        self.text_options_box = QFrame(self)
        self.text_options_box.setVisible(False)
        self.text_options_box.setStyleSheet(
            "QFrame { background: white; border: 1px solid #AAA; border-radius: 6px; } "
            "QLabel { border: none; background: transparent; } "
            "QSpinBox { border: 2px solid #888; border-radius: 3px; padding: 2px 4px; min-height: 18px; } "
            "QSpinBox::up-button { width: 20px; min-width: 20px; height: 10px; min-height: 10px; } "
            "QSpinBox::down-button { width: 20px; min-width: 20px; height: 10px; min-height: 10px; }"
        )
        self.text_options_box.setFixedWidth(138)
        self.text_options_box.setFixedHeight(52)

        tob_main = QVBoxLayout(self.text_options_box)
        tob_main.setContentsMargins(5, 3, 5, 3)
        tob_main.setSpacing(1)

        # Row 1: Font size (label and spinbox tight, no expansion)
        row1 = QHBoxLayout()
        row1.setSpacing(0)
        row1.setContentsMargins(0, 0, 0, 0)
        size_label = QLabel("Size:")
        size_label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        row1.addWidget(size_label)
        self.text_font_size = QSpinBox()
        self.text_font_size.setRange(6, 72)
        self.text_font_size.setValue(20)
        self.text_font_size.setSuffix(" pt")
        self.text_font_size.setToolTip("Font size")
        self.text_font_size.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        self.text_font_size.valueChanged.connect(self._on_text_font_size_changed)
        row1.addWidget(self.text_font_size)
        tob_main.addLayout(row1)

        # Row 2: Bold, Italic, Underline (toggle buttons, tight; reversed when active)
        _biu_style = (
            "QPushButton { background: transparent; border: none; color: #444; "
            "font-size: 12px; padding: 3px 2px; border-radius: 3px; min-width: 22px; min-height: 20px; } "
            "QPushButton:checked { background: #ddd; } "
            "QPushButton:hover { background: #eee; } "
            "QPushButton:checked:hover { background: #d0d0d0; }"
        )
        row2 = QHBoxLayout()
        row2.setSpacing(0)
        row2.setContentsMargins(0, 0, 0, 0)
        self.text_bold_btn = QPushButton("B")
        self.text_bold_btn.setCheckable(True)
        self.text_bold_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        self.text_bold_btn.setStyleSheet(_biu_style + " QPushButton { font-weight: bold; }")
        self.text_bold_btn.setToolTip("Bold")
        self.text_bold_btn.toggled.connect(self._on_text_style_changed)
        row2.addWidget(self.text_bold_btn)
        self.text_italic_btn = QPushButton("I")
        self.text_italic_btn.setCheckable(True)
        self.text_italic_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        self.text_italic_btn.setStyleSheet(_biu_style + " QPushButton { font-style: italic; }")
        self.text_italic_btn.setToolTip("Italic")
        self.text_italic_btn.toggled.connect(self._on_text_style_changed)
        row2.addWidget(self.text_italic_btn)
        self.text_underline_btn = QPushButton("U")
        self.text_underline_btn.setCheckable(True)
        self.text_underline_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        u_font = self.text_underline_btn.font()
        u_font.setUnderline(True)
        self.text_underline_btn.setFont(u_font)
        self.text_underline_btn.setStyleSheet(_biu_style)
        self.text_underline_btn.setToolTip("Underline")
        self.text_underline_btn.toggled.connect(self._on_text_style_changed)
        row2.addWidget(self.text_underline_btn)
        tob_main.addLayout(row2)

        self._block_text_controls = False

        # ----- Layers panel (embedded in left sidebar) -----
        self._layers_panel = QFrame()
        self._layers_panel.setVisible(True)
        self._layers_panel.setMinimumWidth(260)
        self._layers_panel.setStyleSheet(
            "QFrame { background: #FFFFFF; border: 1px solid #CCC; border-radius: 6px; padding: 0px 0px 0px 0px; }"
        )
        self._layers_panel.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self._layers_panel.setMinimumHeight(0)

        lp_layout = QVBoxLayout(self._layers_panel)
        lp_layout.setContentsMargins(8, 8, 8, 8)
        lp_layout.setSpacing(4)

        self._btn_layers_toggle = QPushButton("▾ Layers")
        self._btn_layers_toggle.setCheckable(True)
        self._btn_layers_toggle.setChecked(True)
        self._btn_layers_toggle.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._btn_layers_toggle.setStyleSheet(
            """
            QPushButton {
                text-align: left;
                font-weight: bold;
                font-size: 14pt;
                background: transparent;
                border: none;
                height: 30px;
                padding-top: 0px;
                padding-bottom: 4px;
                padding-left: 8px;
            }
            """
        )
        lp_layout.addWidget(self._btn_layers_toggle)

        self._layers_body = QWidget()
        self._layers_body.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        layers_body_layout = QVBoxLayout(self._layers_body)
        layers_body_layout.setContentsMargins(0, 0, 0, 0)
        layers_body_layout.setSpacing(4)

        # Button toolbar
        btn_row = QHBoxLayout()
        btn_style = (
            "QPushButton { background: white; border: 1px solid #AAA; border-radius: 4px;"
            " font-size: 9pt; padding: 2px 6px; }"
            "QPushButton:hover { background: #EEF; }"
        )
        self._btn_add_layer = QPushButton("+ Layer")
        self._btn_add_layer.setStyleSheet(btn_style)
        self._btn_add_layer.setToolTip("Add a new layer")
        self._btn_add_layer.clicked.connect(self._add_layer_dialog)

        self._btn_group = QPushButton("Group")
        self._btn_group.setStyleSheet(btn_style)
        self._btn_group.setToolTip("Group selected items (Ctrl+G)")
        self._btn_group.clicked.connect(self.group_selected)

        self._btn_del_layer = QPushButton("Delete")
        self._btn_del_layer.setStyleSheet(btn_style)
        self._btn_del_layer.setToolTip("Delete selected layer or group")
        self._btn_del_layer.clicked.connect(self._delete_selected_layer_node)

        for b in [self._btn_add_layer, self._btn_group, self._btn_del_layer]:
            btn_row.addWidget(b)
        layers_body_layout.addLayout(btn_row)

        # Tree
        self._layers_tree = LayersTreeWidget(self) 
        self._layers_tree.setIconSize(QSize(24, 24))
        self._layers_tree.itemChanged.connect(self._on_layer_item_renamed)
        self._layers_tree.customContextMenuRequested.connect(self._layers_context_menu)
        layers_body_layout.addWidget(self._layers_tree)
        # id(CanvasNode) → CanvasNode; rebuilt on every refresh so no Qt item holds
        # a reference to a Python object that Qt might try to pickle during D&D.
        self._node_id_map: dict[int, object] = {}
        self._svg_icon_cache: dict[str, QIcon] = {}  # svg_path → QIcon thumbnail

        hint_label = QLabel("Drag rows to reorder  •  Double-click to rename")
        hint_label.setStyleSheet(
            "font-size: 8pt; color: #888; border: none; background: transparent;"
        )
        hint_label.setWordWrap(True)
        layers_body_layout.addWidget(hint_label)
        lp_layout.addWidget(self._layers_body, 1)

        def _toggle_layers(opened: bool):
            """Show/hide layers body and adjust splitter so only header remains when closed."""
            self._layers_body.setVisible(opened)
            self._btn_layers_toggle.setText(("▾ " if opened else "▸ ") + "Layers")

            if not hasattr(self, "_left_splitter"):
                return

            if opened:
                self._layers_panel.setSizePolicy(QSizePolicy.Policy.Preferred,
                                                 QSizePolicy.Policy.Expanding)
                self._layers_panel.setMaximumHeight(16777215)
                self._left_splitter.setMaximumHeight(16777215)
                # Restore same 50/50 default as startup instead of fixed 1000,1000 (avoids bouncing too high)
                h = self._left_splitter.height()
                if h > 100:
                    half = h // 2
                    self._left_splitter.setSizes([half, h - half])
                else:
                    self._left_splitter.setSizes([1000, 1000])
            else:
                header_h = self._btn_layers_toggle.sizeHint().height() + 12
                self._layers_panel.setSizePolicy(QSizePolicy.Policy.Preferred,
                                                 QSizePolicy.Policy.Fixed)
                self._layers_panel.setMaximumHeight(header_h)
                # When both collapsed, keep splitter at top by limiting its height
                inv_collapsed = not self._inventory_body.isVisible()
                if inv_collapsed:
                    inv_header_h = self._btn_inventory_toggle.sizeHint().height() + 12
                    self._left_splitter.setMaximumHeight(inv_header_h + header_h)
                    self._left_splitter.setSizes([inv_header_h, header_h])
                else:
                    self._left_splitter.setMaximumHeight(16777215)
                    self._left_splitter.setSizes([1000, header_h])
            self._update_splitter_stretch()

        self._btn_layers_toggle.toggled.connect(_toggle_layers)
        self._left_splitter.addWidget(self._layers_panel)
        self._left_splitter.setCollapsible(0, True) # Allow top to collapse
        self._left_splitter.setCollapsible(1, True) # Allow bottom to collapse
        self._left_splitter.setStretchFactor(0, 1)  # Give equal weight
        self._left_splitter.setStretchFactor(1, 1)  # Give equal weight
        self._update_splitter_stretch()  # splitter fills when both expanded, stays at top when both collapsed
        # Apply 50/50 split once layout has given the splitter a real height (delay so sidebar is sized)
        QTimer.singleShot(350, self._apply_left_splitter_default_ratio)

    def _toggle_minimap_side(self):
        """Move minimap between bottom-right and bottom-left; update slit button side and arrow."""
        self._minimap_at_left = not self._minimap_at_left
        layout = self._minimap_container.layout()
        # Remove both widgets and re-add in the right order
        layout.removeWidget(self._minimap_slit_btn)
        layout.removeWidget(self.minimap)
        if self._minimap_at_left:
            layout.addWidget(self.minimap)
            layout.addWidget(self._minimap_slit_btn)
            self._minimap_slit_btn.setText("›")
            self._minimap_slit_btn.setToolTip("Move minimap to bottom right")
        else:
            layout.addWidget(self._minimap_slit_btn)
            layout.addWidget(self.minimap)
            self._minimap_slit_btn.setText("‹")
            self._minimap_slit_btn.setToolTip("Move minimap to bottom left")
        self.reposition_overlays()

    def reposition_overlays(self):
        """Position zoom controls (top-right) and minimap (bottom-right or bottom-left) over the view."""
        vw, vh = self.view.width(), self.view.height()

        # Zoom / delete overlay: top-right with small margin (slightly left of edge)
        self.overlay.move(vw - 70, 20)

        # Minimap container: bottom-right (default) or bottom-left with 20px margin
        cw, ch = self._minimap_container.width(), self._minimap_container.height()
        if getattr(self, '_minimap_at_left', False):
            self._minimap_container.move(20, vh - ch - 20)
        else:
            self._minimap_container.move(vw - cw - 20, vh - ch - 20)

        # Nudge pad: centered at bottom
        self.nudge_box.move((vw - 130) // 2, vh - 150)

        if hasattr(self, 'pen_options_box') and self.pen_options_box.isVisible():
            p = self.btn_draw.mapTo(self, self.btn_draw.rect().bottomLeft())
            self.pen_options_box.move(p.x(), p.y())
        if hasattr(self, 'text_options_box') and self.text_options_box.isVisible():
            p = self.btn_text.mapTo(self, self.btn_text.rect().bottomLeft())
            self.text_options_box.move(p.x(), p.y())

    def _apply_left_splitter_default_ratio(self):
        """Set initial sidebar split to 50/50, filling the available height."""
        if self._left_splitter_ratio_applied or not hasattr(self, '_left_splitter'):
            return
        h = self._left_splitter.height()
        if h > 100:
            half = h // 2
            self._left_splitter.setSizes([half, h - half])
        else:
            self._left_splitter.setSizes([1000, 1000])
        self._left_splitter_ratio_applied = True

    def resizeEvent(self, e):
        self._apply_left_splitter_default_ratio()
        self.reposition_overlays()
        super().resizeEvent(e)

    # =============================================================
    #   BREADBOARD HOLES
    # =============================================================
    def detect_breadboard_holes(self):
        try:
            tree = ET.parse(self._breadboard_path)
            root = tree.getroot()

            viewBox = root.get("viewBox")
            if viewBox:
                x0, y0, w0, h0 = map(float, viewBox.split())
            else:
                w0 = float(root.get("width", 1))
                h0 = float(root.get("height", 1))
                x0 = y0 = 0

            br = self.breadboard.boundingRect()
            sx = br.width() / w0
            sy = br.height() / h0

            holes = []
            for elem in root.iter():
                if elem.tag.endswith("circle"):
                    cx = float(elem.get("cx"))
                    cy = float(elem.get("cy"))
                    local_pt = QPointF(cx * sx, cy * sy)
                    scene_pt = self.breadboard.mapToScene(QPointF(cx, cy))
                    holes.append(scene_pt)

            self.breadboard_holes = holes

        except Exception as e:
            print("Hole detection failed:", e)

    def open_choose_breadboard_dialog(self):
        """Show dialog to choose a breadboard from the Breadboards folder."""
        breadboards_dir = os.path.join(self.base_dir, "Breadboards")
        if not os.path.isdir(breadboards_dir):
            QMessageBox.information(self, "Choose breadboard", "Breadboards folder not found.")
            return
        svgs = sorted(
            [f for f in os.listdir(breadboards_dir) if f.lower().endswith(".svg")],
            key=lambda x: x.lower()
        )
        if not svgs:
            QMessageBox.information(self, "Choose breadboard", "No SVG files in Breadboards folder.")
            return

        dlg = QDialog(self)
        dlg.setWindowTitle("Choose breadboard")
        layout = QVBoxLayout(dlg)
        list_widget = QListWidget()
        paths = []
        current_idx = 0
        for f in svgs:
            full_path = os.path.join(breadboards_dir, f)
            paths.append(full_path)
            display = os.path.splitext(f)[0]
            list_widget.addItem(display)
            if os.path.normpath(full_path) == os.path.normpath(self._breadboard_path):
                current_idx = len(paths) - 1
        list_widget.setCurrentRow(current_idx)
        layout.addWidget(list_widget)

        btn_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btn_box.accepted.connect(dlg.accept)
        btn_box.rejected.connect(dlg.reject)
        layout.addWidget(btn_box)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        idx = list_widget.currentRow()
        if idx < 0:
            return
        chosen_path = paths[idx]

        self._breadboard_path = chosen_path
        self.scene.removeItem(self.breadboard)
        self.breadboard = QGraphicsSvgItem(chosen_path)
        self.breadboard.setZValue(-1)
        self.breadboard.setCacheMode(QGraphicsSvgItem.CacheMode.DeviceCoordinateCache)
        self.scene.addItem(self.breadboard)
        self.detect_breadboard_holes()
        self.view.breadboard_holes = self.breadboard_holes
        self.view.setSceneRect(self.breadboard.boundingRect())
        self._generate_fine_grid()

    # =============================================================
    #   HOLE PATTERN (JSON) FOR PARTS
    # =============================================================
    def load_hole_pattern(self, svg_path):
        # Cache hole_database.json in memory — load_state calls this hundreds of times per undo
        if not hasattr(self, "_hole_db_cache") or self._hole_db_cache is None:
            try:
                with open(os.path.join(self.icons_root, "hole_database.json"), "r") as f:
                    self._hole_db_cache = json.load(f)
            except Exception:
                self._hole_db_cache = {}
        db = self._hole_db_cache

        key = os.path.basename(svg_path)
        if key not in db:
            return []

        return [QPointF(float(h["x"]), float(h["y"])) for h in db[key]]

    def _get_svg_renderer(self, svg_path):
        """Cache QSvgRenderer per path — load_state creates hundreds of elements sharing few paths."""
        if not hasattr(self, "_svg_renderer_cache"):
            self._svg_renderer_cache = {}
        if svg_path not in self._svg_renderer_cache:
            self._svg_renderer_cache[svg_path] = QSvgRenderer(svg_path)
        return self._svg_renderer_cache[svg_path]

    def calculate_grid_spacing(self):
        if len(self.breadboard_holes) < 2:
            return 35.0

        import math
        distances = []

        for i, p1 in enumerate(self.breadboard_holes[:30]):  # limit to first 30 holes
            for p2 in self.breadboard_holes[i+1:i+15]:
                dist = math.hypot(p1.x() - p2.x(), p1.y() - p2.y())
                if 10 < dist < 80:
                    distances.append(round(dist, 1))

        if not distances:
            return 35.0

        # Sort and take median of small distances
        distances.sort()
        median_idx = len(distances) // 2
        spacing = distances[median_idx]

        #print(f"Estimated grid spacing: {spacing:.1f} px")
        return spacing


    def _fine_grid_cache_path(self):
        """Path to the fine-grid cache for the current breadboard (in Breadboards folder)."""
        name = os.path.splitext(os.path.basename(self._breadboard_path))[0]
        return os.path.join(self.base_dir, "Breadboards", f"{name}_fine_grids.json")

    def _generate_fine_grid(self):
        breadboards_dir = os.path.join(self.base_dir, "Breadboards")
        cache_path = self._fine_grid_cache_path()

        # 1. Try to load from cache for this breadboard
        if os.path.exists(cache_path):
            try:
                with open(cache_path, "r") as f:
                    data = json.load(f)
                self.fine_grid_points = [QPointF(x, y) for x, y in data]
                return
            except Exception:
                pass

        if not self.breadboard_holes:
            self.fine_grid_points = []
            return

        S = self.calculate_grid_spacing()
        point_set = set()

        # Add original breadboard holes (The main grid)
        for p in self.breadboard_holes:
            point_set.add((round(p.x(), 2), round(p.y(), 2)))

        # 2. Generate ONLY the rotated square centers
        # We look for holes that are DIAGONAL to each other (dist ≈ 1.41 * S)
        for i, p1 in enumerate(self.breadboard_holes):
            for p2 in self.breadboard_holes[i+1:]:
                dist = QLineF(p1, p2).length()
                
                # We target the range between 1.3*S and 1.5*S.
                # This excludes the direct neighbors (1.0*S) which create the 
                # midpoints you called "unnecessary".
                if (S * 1.3) < dist < (S * 1.5):
                    mid_x = (p1.x() + p2.x()) / 2
                    mid_y = (p1.y() + p2.y()) / 2
                    point_set.add((round(mid_x, 2), round(mid_y, 2)))

        self.fine_grid_points = [QPointF(x, y) for x, y in point_set]

        # 3. Save to cache in Breadboards folder
        try:
            os.makedirs(breadboards_dir, exist_ok=True)
            data = [(p.x(), p.y()) for p in self.fine_grid_points]
            with open(cache_path, "w") as f:
                json.dump(data, f)
        except Exception:
            pass
        
    # =============================================================
    #   MINIMAP
    # =============================================================
    def update_minimap(self):
        try:
            rect = self.breadboard.boundingRect()
            if rect.width() <= 0:
                return

            # Use the actual label size
            thumb = QPixmap(self.minimap.size())
            thumb.fill(QColor("#D0D0D0"))

            p = QPainter(thumb)
            # Rotate the painter itself to match canvas rotation
            if self._canvas_rotation != 0:
                p.translate(thumb.width() / 2, thumb.height() / 2)
                p.rotate(self._canvas_rotation)
                p.translate(-thumb.width() / 2, -thumb.height() / 2)
            
            self.scene.render(p, QRectF(thumb.rect()), rect)
            p.end()

            # Get the visible area in scene coords
            vr = self.view.mapToScene(self.view.viewport().rect()).boundingRect()
            xr = thumb.width() / rect.width()
            yr = thumb.height() / rect.height()

            p = QPainter(thumb)
            # Rotate for the viewport box too
            if self._canvas_rotation != 0:
                p.translate(thumb.width() / 2, thumb.height() / 2)
                p.rotate(self._canvas_rotation)
                p.translate(-thumb.width() / 2, -thumb.height() / 2)
                
            p.setPen(QPen(QColor("#333333"), 2))
            p.drawRect(QRectF(
                (vr.x() - rect.x()) * xr,
                (vr.y() - rect.y()) * yr,
                vr.width() * xr,
                vr.height() * yr,
            ))
            p.end()

            self.minimap.setPixmap(thumb)
        except:
            pass

    # =============================================================
    #   NUDGE + SNAPPING HELPERS
    # =============================================================
    def nudge_selected(self, dx, dy):
        items = [i for i in self.scene.selectedItems() if isinstance(i, (DraggableElement, LaserPath, CanvasTextItem))]
        if not items:
            return

        has_elem_grid = bool(self.breadboard_holes)
        has_laser_grid = bool(getattr(self, "fine_grid_points", []))
        if not has_elem_grid and not has_laser_grid:
            return

        # Pick one anchor: nudge only it, then apply same delta to all others (group nudge)
        anchor = None
        for i in items:
            if isinstance(i, DraggableElement) and has_elem_grid and i.holes:
                anchor = i
                break
        if anchor is None:
            for i in items:
                if isinstance(i, LaserPath) and has_laser_grid:
                    anchor = i
                    break
        if anchor is None:
            return

        moved_count = 0
        delta_scene = QPointF(0, 0)

        # Tolerance for "same column/row" in cardinal directions
        ALIGN_TOL = 6.0

        # For diagonal: how close Δx and Δy should be (ratio)
        DIAGONAL_BALANCE_TOL = 0.35

        for item in items:
            if item is not anchor:
                continue
            # ---------------------------------------------------------
            # DraggableElement nudge (breadboard hole grid)
            # ---------------------------------------------------------
            if isinstance(item, DraggableElement):
                if not has_elem_grid or not item.holes:
                    continue

                ref = item.mapToScene(item.holes[0])
                current = min(
                    self.breadboard_holes,
                    key=lambda h: (h - ref).manhattanLength(),
                    default=None
                )
                if current is None:
                    continue

                best_target = None
                best_dist = float('inf')

                is_vertical   = abs(dx) < 0.5 and abs(dy) > 0.5
                is_horizontal = abs(dy) < 0.5 and abs(dx) > 0.5
                is_diagonal   = abs(dx) > 0.5 and abs(dy) > 0.5

                for h in self.breadboard_holes:
                    if h == current:
                        continue

                    delta = h - current
                    dist = delta.manhattanLength()

                    if is_vertical:
                        if abs(h.x() - current.x()) > ALIGN_TOL:
                            continue
                        if dy < 0 and h.y() >= current.y():
                            continue
                        if dy > 0 and h.y() <= current.y():
                            continue

                    elif is_horizontal:
                        if abs(h.y() - current.y()) > ALIGN_TOL:
                            continue
                        if dx < 0 and h.x() >= current.x():
                            continue
                        if dx > 0 and h.x() <= current.x():
                            continue

                    elif is_diagonal:
                        if dx < 0 and delta.x() >= 0: continue
                        if dx > 0 and delta.x() <= 0: continue
                        if dy < 0 and delta.y() >= 0: continue
                        if dy > 0 and delta.y() <= 0: continue

                        abs_dx = abs(delta.x())
                        abs_dy = abs(delta.y())
                        if abs_dx < 5 or abs_dy < 5:
                            continue

                        balance = abs(abs_dx - abs_dy) / max(abs_dx, abs_dy, 1)
                        if balance > DIAGONAL_BALANCE_TOL:
                            continue

                    else:
                        continue

                    if dist < best_dist:
                        best_dist = dist
                        best_target = h

                if best_target is None:
                    continue

                delta_scene = best_target - ref
                move_len = delta_scene.manhattanLength()
                if 1 < move_len < 300:
                    item.setPos(item.pos() + delta_scene)
                    moved_count += 1
                continue

            # ---------------------------------------------------------
            # LaserPath nudge (fine grid)
            # ---------------------------------------------------------
            if isinstance(item, LaserPath):
                if not has_laser_grid:
                    continue

                # Use p1 as the anchor point (same locking strategy as drag snap)
                p1_scene = item.mapToScene(item.line().p1())
                current = min(
                    self.fine_grid_points,
                    key=lambda h: (h - p1_scene).manhattanLength(),
                    default=None
                )
                if current is None:
                    continue

                best_target = None
                best_dist = float('inf')
                is_vertical   = abs(dx) < 0.5 and abs(dy) > 0.5
                is_horizontal = abs(dy) < 0.5 and abs(dx) > 0.5
                is_diagonal   = abs(dx) > 0.5 and abs(dy) > 0.5

                for h in self.fine_grid_points:
                    if h == current:
                        continue
                    delta = h - current
                    dist = delta.manhattanLength()

                    if is_vertical:
                        if abs(h.x() - current.x()) > ALIGN_TOL:
                            continue
                        if dy < 0 and h.y() >= current.y():
                            continue
                        if dy > 0 and h.y() <= current.y():
                            continue
                    elif is_horizontal:
                        if abs(h.y() - current.y()) > ALIGN_TOL:
                            continue
                        if dx < 0 and h.x() >= current.x():
                            continue
                        if dx > 0 and h.x() <= current.x():
                            continue
                    elif is_diagonal:
                        if dx < 0 and delta.x() >= 0: continue
                        if dx > 0 and delta.x() <= 0: continue
                        if dy < 0 and delta.y() >= 0: continue
                        if dy > 0 and delta.y() <= 0: continue
                        abs_dx = abs(delta.x())
                        abs_dy = abs(delta.y())
                        if abs_dx < 2 or abs_dy < 2:
                            continue
                        balance = abs(abs_dx - abs_dy) / max(abs_dx, abs_dy, 1)
                        if balance > DIAGONAL_BALANCE_TOL:
                            continue
                    else:
                        continue

                    if dist < best_dist:
                        best_dist = dist
                        best_target = h

                if best_target is None:
                    continue

                delta_scene = best_target - p1_scene
                move_len = delta_scene.manhattanLength()
                if 0.5 < move_len < 300:
                    item.moveBy(delta_scene.x(), delta_scene.y())
                    moved_count += 1

        # Apply same delta to all other selected items (group nudge)
        if moved_count > 0 and (delta_scene.x() != 0 or delta_scene.y() != 0):
            for other in items:
                if other is anchor:
                    continue
                if isinstance(other, (DraggableElement, CanvasTextItem)):
                    other.setPos(other.pos() + delta_scene)
                elif isinstance(other, LaserPath):
                    other.moveBy(delta_scene.x(), delta_scene.y())

        if moved_count > 0 and not self._is_loading:
            self.save_undo_state()

    def rotate_selected_90(self):
        """Rotate all selected items 90° CW around the group centroid, preserving relative positions."""
        selected = [
            i for i in self.scene.selectedItems()
            if isinstance(i, (DraggableElement, LaserPath, CanvasTextItem))
        ]
        if not selected:
            return
        self._suppress_undo_save = True
        try:
            # Normalize LaserPaths to position+orientation form so each has distinct pos and correct orientation.
            for item in selected:
                if isinstance(item, LaserPath):
                    item.normalize_to_center_orientation()

            # Reference point per item: hole 0 if DraggableElement has holes, else center. Pivot = centroid of these.
            def get_ref(item):
                if isinstance(item, DraggableElement):
                    ref_local = item.boundingRect().center()
                    ref_scene = item.mapToScene(ref_local)
                    return ref_scene, ref_local
                if isinstance(item, CanvasTextItem):
                    ref_local = item.boundingRect().center()
                    return item.mapToScene(ref_local), ref_local
                # LaserPath
                ref_local = item.line().center()
                return item.mapToScene(ref_local), ref_local

            refs = [get_ref(item) for item in selected]
            centers = [r[0] for r in refs]
            cx = sum(p.x() for p in centers) / len(centers)
            cy = sum(p.y() for p in centers) / len(centers)

            # 90° CW around (cx, cy): (x, y) -> (cx + (y - cy), cy - (x - cx))
            def rotate_90_cw(sc_x, sc_y):
                return QPointF(cx + (sc_y - cy), cy - (sc_x - cx))

            # Offset for pos(): pivot in scene = pos() + ref_local for DraggableElement (transform origin at ref).
            # LaserPath has ref_local=(0,0). CanvasTextItem: pos() + R(rot)*ref_local.
            def offset_for_pos(item, ref_local, new_rot_deg):
                if isinstance(item, DraggableElement):
                    return ref_local
                if isinstance(item, LaserPath):
                    return QPointF(0, 0)
                t = QTransform().rotate(new_rot_deg)
                return t.map(ref_local)

            for item, (ref_scene, ref_local) in zip(selected, refs):
                # DraggableElement: set transform origin to ref (hole 0 or center) so rotation is around it
                if isinstance(item, DraggableElement):
                    item.setTransformOriginPoint(ref_local)
                new_ref = rotate_90_cw(ref_scene.x(), ref_scene.y())
                # Use -90 for all: Qt positive angle = CCW, so -90 = 90° CW; matches rotate_90_cw and keeps arrows correct
                new_rot = item.rotation() - 90
                offset_pt = offset_for_pos(item, ref_local, new_rot)
                new_pos = new_ref - offset_pt
                item.setRotation(new_rot)
                item.setPos(new_pos)
        finally:
            self._suppress_undo_save = False
        # Snap one anchor (first DraggableElement) to grid, then displace all others by the same delta (like group drag).
        anchor = next((i for i in selected if isinstance(i, DraggableElement)), None)
        if anchor is not None:
            old_pos = anchor.pos()
            anchor.snap_to_grid()
            delta = anchor.pos() - old_pos
            for item in selected:
                if item is anchor:
                    continue
                if isinstance(item, (DraggableElement, LaserPath, CanvasTextItem)):
                    item.setPos(item.pos() + delta)
        self.save_undo_state()

    def rotate_canvas_90(self):
        self._canvas_rotation = (self._canvas_rotation + 90) % 360
        self.view.setTransform(
            self.view.transform().rotate(90)
        )

    # =============================================================
    #   COPY / PASTE
    # =============================================================
    def copy_selected(self):
        self._clipboard = []
        for item in self.scene.selectedItems():
            if isinstance(item, DraggableElement):
                self._clipboard.append({
                    "t": "i",
                    "p": item.file_path,
                    "x": item.pos().x(),
                    "y": item.pos().y(),
                    "r": item.rotation(),
                    "z": item.zValue(),
                })
            elif isinstance(item, LaserPath):
                p1 = item.mapToScene(item.line().p1())
                p2 = item.mapToScene(item.line().p2())
                self._clipboard.append({
                    "t": "l",
                    "x1": p1.x(), "y1": p1.y(),
                    "x2": p2.x(), "y2": p2.y(),
                    "c": item.color.name(QColor.NameFormat.HexArgb),
                    "a": item.has_arrow,
                })
            elif isinstance(item, CanvasTextItem):
                self._clipboard.append({
                    "t": "text",
                    "x": item.pos().x(),
                    "y": item.pos().y(),
                    "content": item.toPlainText() or "",
                })

    def paste_items(self):
        if not self._clipboard:
            return
        
        offset = 20  # Paste with a slight offset so it's visible
        self.scene.clearSelection()

        for d in self._clipboard:
            if d["t"] == "i":
                item = DraggableElement(d["p"], "item", self)
                item.holes = self.load_hole_pattern(d["p"])
                item.snapping_enabled = False
                item.setPos(d["x"] + offset, d["y"] + offset)
                item.setRotation(d["r"])
                item.setZValue(d.get("z", 5))
                self.scene.addItem(item)
                item.setSelected(True)
                item.snapping_enabled = True
            elif d["t"] == "l":
                p1 = QPointF(d["x1"] + offset, d["y1"] + offset)
                p2 = QPointF(d["x2"] + offset, d["y2"] + offset)
                lp = LaserPath.from_scene_endpoints(p1, p2, QColor(d["c"]), d.get("a", True))
                lp.color = QColor(d["c"])
                lp.setPen(QPen(lp.color, 7, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                self.scene.addItem(lp)
                lp.setSelected(True)
            elif d["t"] == "text":
                txt = CanvasTextItem(d.get("content") or "Text", self)
                txt.setPos(d["x"] + offset, d["y"] + offset)
                self.scene.addItem(txt)
                txt.setSelected(True)

        self.save_undo_state()

    # =============================================================
    #   UNDO / REDO
    # =============================================================
    def _rebuild_canvas_tree(self):
        """Rebuild the CanvasState tree to mirror the current QGraphicsScene.

        Preserves any existing layers; items not already present in the tree
        are placed into the active layer.
        """
        # Build a set of Qt items already tracked in the tree so we don't duplicate
        tracked = {node.item for node in self.canvas_state.all_item_nodes()
                   if node.item is not None}

        # Ensure default layers exist; then enforce Text on top.
        elem_layer  = self.canvas_state.get_or_create_layer("Elements")
        laser_layer = self.canvas_state.get_or_create_layer("Laser Paths")
        text_layer  = self.canvas_state.get_or_create_layer("Text")
        self._ensure_text_layer_on_top()
        # Re-fetch after reorder (node refs still valid but order changed)
        text_layer  = self.canvas_state.get_layer("Text")
        laser_layer = self.canvas_state.get_layer("Laser Paths")
        elem_layer  = self.canvas_state.get_layer("Elements")

        # scene.items() returns items in descending z-order (highest z first).
        # Iterating in reverse (lowest z first) and inserting at index 0 means
        # the item that had the highest z ends up at index 0 (top of the layer),
        # preserving the visual stacking correctly.
        for i in reversed(self.scene.items()):
            if i in tracked:
                continue
            if isinstance(i, DraggableElement):
                elem_layer.insert_child(
                    0, CanvasNode(os.path.basename(i.file_path), ITEM, item=i,
                                  data={"path": i.file_path})
                )
            elif isinstance(i, LaserPath):
                laser_layer.insert_child(
                    0, CanvasNode("Laser Path", ITEM, item=i, data={})
                )
            elif isinstance(i, CanvasTextItem):
                snippet = (i.toPlainText() or "Text")[:20]
                text_layer.insert_child(
                    0, CanvasNode(f"Text: {snippet}", ITEM, item=i, data={})
                )

        # Prune nodes whose Qt item has been removed from the scene
        scene_items = set(self.scene.items())
        for node in list(self.canvas_state.all_item_nodes()):
            if node.item not in scene_items and node.parent:
                node.parent.remove_child(node)

        self._sync_z_order()

    def _sync_z_order(self):
        """Assign Qt zValues based on position in the canvas tree.

        Layers listed FIRST (top of the panel) render ON TOP (higher z).
        Within a layer, items listed FIRST are on top.
        The breadboard background stays at z=0.
        """
        layers = self.canvas_state.layers()
        n_layers = len(layers)
        LAYER_STEP = 1000  # z-range reserved per layer

        for layer_idx, layer in enumerate(layers):
            # Layer at index 0 (top of panel) gets the highest z base
            layer_z_base = (n_layers - layer_idx) * LAYER_STEP

            # Collect all item nodes in this layer in display order
            item_nodes = [n for n in layer.all_nodes_flat()
                          if n.node_type == ITEM and n.item is not None]
            n_items = len(item_nodes)

            # item_nodes[0] is the topmost in the panel → give it the highest z
            for rank, node in enumerate(item_nodes):
                node.item.setZValue(layer_z_base + (n_items - rank))

    def _ensure_text_layer_on_top(self):
        """Reorder layers so 'Text' is always first (on top). Used after load/rebuild/add layer."""
        layers = self.canvas_state.layers()
        names = [l.name for l in layers]
        if "Text" not in names:
            return
        new_order = ["Text"] + [n for n in names if n != "Text"]
        self.canvas_state.reorder_layers(new_order)

    def save_undo_state(self, initial=False):
        if self._is_loading:
            return
        if getattr(self, "_suppress_undo_save", False):
            return
        # Sync tree before snapshotting so layer structure is current
        self._rebuild_canvas_tree()

        items_list = []
        for i in self.scene.items():
            if isinstance(i, DraggableElement):
                items_list.append({
                    "t": "i",
                    "p": i.file_path,
                    "x": i.pos().x(),
                    "y": i.pos().y(),
                    "r": i.rotation(),
                    "z": i.zValue(),
                })
            elif isinstance(i, LaserPath):
                p1 = i.mapToScene(i.line().p1())
                p2 = i.mapToScene(i.line().p2())
                items_list.append({
                    "t": "l",
                    "x1": p1.x(), "y1": p1.y(),
                    "x2": p2.x(), "y2": p2.y(),
                    "c": i.color.name(QColor.NameFormat.HexArgb),
                    "a": i.has_arrow,
                })
            elif isinstance(i, CanvasTextItem):
                items_list.append({
                    "t": "text",
                    "x": i.pos().x(),
                    "y": i.pos().y(),
                    "content": i.toPlainText() or "",
                })

        # Encode layer structure alongside items using item-path/coords as keys
        # so we can re-assign items to the correct layers on load.
        layer_structure = self._encode_layer_structure()

        snapshot = json.dumps({
            "items": items_list,
            "layers": layer_structure,
            "active_layer": self._active_layer_name,
        })

        self.undo_stack.append(snapshot)
        if not initial:
            self.redo_stack.clear()

        # Skip panel refresh when called from _sync_tree_from_widget — the
        # widget already shows the correct post-drag state and a rebuild would
        # duplicate rows.
        if (not getattr(self, '_is_syncing_tree', False)
                and hasattr(self, '_layers_panel')
                and self._layers_panel.isVisible()):
            self.refresh_layers_panel()

    def _encode_layer_structure(self) -> list:
        """Encode the layer tree as a plain list, referencing items by index.

        Each layer/group entry stores its name, type, and a list of child
        descriptors.  ITEM nodes store a "key" that identifies the Qt item
        so load_state can re-assign items to their correct position.
        """
        def _encode_node(node):
            from canvas import ITEM as _ITEM
            if node.node_type == _ITEM:
                # Build a key from the item's content
                item = node.item
                if isinstance(item, DraggableElement):
                    key = {"kt": "i", "p": item.file_path,
                           "x": round(item.pos().x(), 1),
                           "y": round(item.pos().y(), 1)}
                elif isinstance(item, LaserPath):
                    p1 = item.mapToScene(item.line().p1())
                    p2 = item.mapToScene(item.line().p2())
                    key = {"kt": "l",
                           "x1": round(p1.x(), 1), "y1": round(p1.y(), 1),
                           "x2": round(p2.x(), 1), "y2": round(p2.y(), 1)}
                elif isinstance(item, CanvasTextItem):
                    key = {"kt": "text",
                           "x": round(item.pos().x(), 1),
                           "y": round(item.pos().y(), 1),
                           "content": (item.toPlainText() or "")[:50]}
                else:
                    return None
                return {"type": "item", "name": node.name, "key": key}
            else:
                children = [_encode_node(c) for c in node.children]
                children = [c for c in children if c is not None]
                return {"type": node.node_type, "name": node.name,
                        "children": children}

        result = []
        for layer in self.canvas_state.layers():
            enc = _encode_node(layer)
            if enc:
                result.append(enc)
        return result

    def undo_action(self):
        if len(self.undo_stack) > 1:
            self.redo_stack.append(self.undo_stack.pop())
            self.load_state(self.undo_stack[-1])

    def redo_action(self):
        if self.redo_stack:
            state = self.redo_stack.pop()
            self.undo_stack.append(state)
            self.load_state(state)

    def load_state(self, js):
        self._is_loading = True

        snapshot = json.loads(js)

        # Support both old format (plain list) and new format (dict with layers)
        if isinstance(snapshot, list):
            items_list = snapshot
            layer_structure = None
            active_layer = self._active_layer_name
        else:
            items_list = snapshot.get("items", [])
            layer_structure = snapshot.get("layers")
            active_layer = snapshot.get("active_layer", self._active_layer_name)

        # ── Clear all live items and recreate from snapshot ───────────────────
        # Key-based reconciliation is unreliable when many items share the same
        # SVG path at close positions (colliding keys). Always do a clean rebuild.
        for qt_item in list(self.scene.items()):
            if isinstance(qt_item, (DraggableElement, LaserPath, CanvasTextItem)):
                self.scene.removeItem(qt_item)

        reconstructed: list[tuple[dict, object]] = []

        for d in items_list:
            if d["t"] == "i":
                renderer = self._get_svg_renderer(d["p"])
                item = DraggableElement(d["p"], "item", self, renderer=renderer)
                item.holes = self.load_hole_pattern(d["p"])
                item.snapping_enabled = False
                item.setPos(d["x"], d["y"])
                item.setRotation(d["r"])
                item.setZValue(d.get("z", 5))
                self.scene.addItem(item)
                item.snapping_enabled = True
                reconstructed.append((d, item))
            elif d["t"] == "text":
                txt = CanvasTextItem(d.get("content") or "Text", self)
                txt.setPos(d["x"], d["y"])
                self.scene.addItem(txt)
                reconstructed.append((d, txt))
            else:
                saved_color = QColor(d.get("c", "#FF0000"))
                lp = LaserPath.from_scene_endpoints(
                    QPointF(d["x1"], d["y1"]), QPointF(d["x2"], d["y2"]),
                    saved_color, d.get("a", True),
                )
                lp.color = saved_color
                lp.setPen(QPen(saved_color, 7, Qt.PenStyle.SolidLine,
                               Qt.PenCapStyle.RoundCap))
                self.scene.addItem(lp)
                reconstructed.append((d, lp))

        # ── Restore layer structure ───────────────────────────────────────────
        self._active_layer_name = active_layer
        if layer_structure:
            self._restore_layer_structure(layer_structure, reconstructed)
            self._ensure_text_layer_on_top()
        else:
            self.canvas_state = CanvasState()
            self.canvas_state.add_layer("Text")
            self.canvas_state.add_layer("Laser Paths")
            self.canvas_state.add_layer("Elements")
            self._rebuild_canvas_tree()

        self._sync_z_order()
        self._is_loading = False

        if hasattr(self, '_layers_panel') and self._layers_panel.isVisible():
            self.refresh_layers_panel()

    def _restore_layer_structure(self, layer_structure: list, reconstructed: list):
        """Rebuild canvas_state from the saved layer_structure, mapping encoded
        keys back to the freshly-created Qt items."""

        # Build lookup: key-tuple → qt_item
        def _make_key(d):
            if d["t"] == "i":
                return ("i", d["p"], round(d["x"], 1), round(d["y"], 1))
            elif d["t"] == "text":
                return ("text", round(d["x"], 1), round(d["y"], 1), (d.get("content") or "")[:50])
            else:
                return ("l", round(d["x1"], 1), round(d["y1"], 1),
                               round(d["x2"], 1), round(d["y2"], 1))

        def _enc_key(k):
            if k["kt"] == "i":
                return ("i", k["p"], k["x"], k["y"])
            elif k["kt"] == "text":
                return ("text", k["x"], k["y"], (k.get("content") or "")[:50])
            else:
                return ("l", k["x1"], k["y1"], k["x2"], k["y2"])

        item_lookup = {_make_key(d): qt_item for d, qt_item in reconstructed}

        self.canvas_state = CanvasState()
        assigned = set()

        def _build_node(enc) -> "CanvasNode | None":
            from canvas import CanvasNode as _CN, LAYER as _L, GROUP as _G, ITEM as _I
            ntype = enc.get("type", "item")
            name = enc.get("name", "")
            if ntype == "item":
                key = _enc_key(enc["key"])
                qt_item = item_lookup.get(key)
                if qt_item is None:
                    return None
                assigned.add(id(qt_item))
                return _CN(name, _I, item=qt_item)
            else:
                node_type = _L if ntype == "Layer" else _G
                node = _CN(name, node_type)
                for child_enc in enc.get("children", []):
                    child = _build_node(child_enc)
                    if child is not None:
                        node.add_child(child)
                return node

        for layer_enc in layer_structure:
            layer_node = _build_node(layer_enc)
            if layer_node is not None:
                self.canvas_state.root.add_child(layer_node)

        # Any items not matched go into the active layer (safety net)
        active_layer = self.canvas_state.get_or_create_layer(self._active_layer_name)
        for d, qt_item in reconstructed:
            if id(qt_item) not in assigned:
                from canvas import CanvasNode as _CN, ITEM as _I
                import os as _os
                if isinstance(qt_item, DraggableElement):
                    name = _os.path.basename(qt_item.file_path)
                elif isinstance(qt_item, CanvasTextItem):
                    name = "Text: " + (qt_item.toPlainText() or "Text")[:20]
                else:
                    name = "Laser Path"
                active_layer.insert_child(0, _CN(name, _I, item=qt_item))

    # =============================================================
    #   LAYERS PANEL
    # =============================================================
    def toggle_layers_panel(self):
        # Backward-compatible API: now toggles the embedded sidebar section.
        self._btn_layers_toggle.toggle()

    def _position_layers_panel(self):
        # No-op: layers panel is embedded in sidebar, not floating anymore.
        return

    def _node_from_item(self, wi) -> "CanvasNode | None":
        """Look up the CanvasNode for a QTreeWidgetItem using the id map."""
        nid = wi.data(0, Qt.ItemDataRole.UserRole)
        if nid is None:
            return None
        return self._node_id_map.get(nid)

    def _element_thumbnail(self, svg_path: str, rotation: float, size: int = 24) -> QIcon:
        """Render an SVG thumbnail preserving aspect ratio, with rotation applied."""
        key = (svg_path, round(rotation, 1))
        if key in self._svg_icon_cache:
            return self._svg_icon_cache[key]
        renderer = QSvgRenderer(svg_path)
        # Compute a render rect that fits inside size×size while keeping aspect ratio
        vp = renderer.viewBoxF()
        if vp.isNull() or vp.width() == 0 or vp.height() == 0:
            vp = QRectF(0, 0, size, size)
        aspect = vp.width() / vp.height()
        if aspect >= 1.0:
            rw, rh = float(size), size / aspect
        else:
            rw, rh = size * aspect, float(size)
        rx = (size - rw) / 2
        ry = (size - rh) / 2
        render_rect = QRectF(rx, ry, rw, rh)

        px = QPixmap(size, size)
        px.fill(Qt.GlobalColor.transparent)
        painter = QPainter(px)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        # Rotate around the centre of the thumbnail
        painter.translate(size / 2, size / 2)
        painter.rotate(rotation)
        painter.translate(-size / 2, -size / 2)
        renderer.render(painter, render_rect)
        painter.end()
        icon = QIcon(px)
        self._svg_icon_cache[key] = icon
        return icon

    def _laser_thumbnail(self, item, size: int = 24) -> QIcon:
        """Render a thumbnail that zooms the full laser path to fill the icon."""
        import math as _math
        PAD = 3  # px padding on each side

        px = QPixmap(size, size)
        px.fill(Qt.GlobalColor.transparent)
        p = QPainter(px)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        line = item.line()
        col  = item.color if hasattr(item, "color") else QColor(255, 0, 0)

        # Scene-space endpoints
        sx1, sy1 = line.x1(), line.y1()
        sx2, sy2 = line.x2(), line.y2()

        # Bounding box of the line in scene space
        min_x, max_x = min(sx1, sx2), max(sx1, sx2)
        min_y, max_y = min(sy1, sy2), max(sy1, sy2)
        span_x = max_x - min_x or 1   # avoid div/0 for perfectly H or V lines
        span_y = max_y - min_y or 1

        # Available drawing area inside padding
        draw = size - 2 * PAD

        # Uniform scale so the whole line fits, preserving aspect ratio
        scale = draw / max(span_x, span_y)

        def _map(sx, sy):
            # Centre the scaled line in the thumbnail
            tx = PAD + (sx - min_x) * scale + (draw - span_x * scale) / 2
            ty = PAD + (sy - min_y) * scale + (draw - span_y * scale) / 2
            return tx, ty

        tx1, ty1 = _map(sx1, sy1)
        tx2, ty2 = _map(sx2, sy2)

        p.setPen(QPen(col, 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(QPointF(tx1, ty1), QPointF(tx2, ty2))

        # Arrowhead centred on the midpoint of the thumbnail line
        if getattr(item, "has_arrow", False):
            angle  = _math.atan2(-(ty2 - ty1), tx2 - tx1)
            mcx    = (tx1 + tx2) / 2
            mcy    = (ty1 + ty2) / 2
            arr    = size * 0.28        # arrowhead size relative to thumbnail
            half_a = arr / 2
            tip_x  = mcx + _math.cos(angle) * half_a
            tip_y  = mcy - _math.sin(angle) * half_a
            bx     = mcx - _math.cos(angle) * half_a
            by     = mcy + _math.sin(angle) * half_a
            w1x    = bx + _math.sin(angle) * half_a
            w1y    = by + _math.cos(angle) * half_a
            w2x    = bx - _math.sin(angle) * half_a
            w2y    = by - _math.cos(angle) * half_a
            p.setBrush(col)
            p.setPen(Qt.PenStyle.NoPen)
            p.drawPolygon(QPolygonF([
                QPointF(tip_x, tip_y),
                QPointF(w1x, w1y),
                QPointF(w2x, w2y),
            ]))

        p.end()
        return QIcon(px)


    def refresh_layers_panel(self):
        """Rebuild the tree widget from the current canvas_state."""
        self._layers_tree.blockSignals(True)
        self._layers_tree.clear()
        self._node_id_map.clear()
        self._svg_icon_cache.clear()  # Flush so rotations/colors are always fresh

        def _add_node(parent_widget, node):
            # Register this node in the id map
            self._node_id_map[id(node)] = node

            if node.node_type == LAYER:
                label = f"▤  {node.name}"
                wi = QTreeWidgetItem([label])
            elif node.node_type == GROUP:
                label = f"⬡  {node.name}"
                wi = QTreeWidgetItem([label])
            else:
                # ITEM — use live thumbnail reflecting current rotation / geometry
                svg_path = node.data.get("path") if node.data else None
                label = f"  {node.name}"
                wi = QTreeWidgetItem([label])
                if svg_path and os.path.isfile(svg_path):
                    rot = node.item.rotation() if node.item else 0.0
                    wi.setIcon(0, self._element_thumbnail(svg_path, rot, 24))
                elif node.item is not None and isinstance(node.item, LaserPath):
                    wi.setIcon(0, self._laser_thumbnail(node.item, 24))
            # Store only the integer id — never the Python object itself,
            # because Qt may try to pickle UserRole data during drag-and-drop.
            wi.setData(0, Qt.ItemDataRole.UserRole, id(node))

            # Make layers and groups editable (double-click)
            if node.node_type in (LAYER, GROUP):
                wi.setFlags(wi.flags() | Qt.ItemFlag.ItemIsEditable
                            | Qt.ItemFlag.ItemIsDragEnabled
                            | Qt.ItemFlag.ItemIsDropEnabled)
            else:
                wi.setFlags(wi.flags() | Qt.ItemFlag.ItemIsDragEnabled)
                wi.setFlags(wi.flags() & ~Qt.ItemFlag.ItemIsDropEnabled)

            # Bold + colored dot for active layer
            if node.node_type == LAYER and node.name == self._active_layer_name:
                f = wi.font(0)
                f.setBold(True)
                wi.setFont(0, f)
                wi.setText(0, f"● {node.name}")

            if isinstance(parent_widget, QTreeWidget):
                parent_widget.addTopLevelItem(wi)
            else:
                parent_widget.addChild(wi)

            for child in node.children:
                _add_node(wi, child)
            wi.setExpanded(True)

        for top_node in self.canvas_state.root.children:
            _add_node(self._layers_tree, top_node)

        self._layers_tree.blockSignals(False)
        # Resize panel to fit content (capped)
        row_h = self._layers_tree.sizeHintForRow(0) or 22
        n_rows = sum(1 for _ in self.canvas_state.root.all_nodes_flat()) - 1
        tree_h = max(80, min(350, n_rows * (row_h + 4)))
        # Embedded panel: avoid hard fixed heights so it shares sidebar space.
        self._layers_panel.setMinimumHeight(max(180, tree_h + 60))

    def _refresh_active_layer_display(self):
        """Re-bold the active layer row without a full panel rebuild."""
        self._layers_tree.blockSignals(True)
        try:
            root = self._layers_tree.invisibleRootItem()
            for i in range(root.childCount()):
                wi = root.child(i)
                node = self._node_from_item(wi)
                if node and node.node_type == LAYER:
                    f = QFont()
                    is_active = node.name == self._active_layer_name
                    f.setBold(is_active)
                    wi.setFont(0, f)
                    wi.setText(0, f"{'● ' if is_active else '▤  '}{node.name}")
        finally:
            self._layers_tree.blockSignals(False)

    def _on_layer_item_renamed(self, widget_item, col):
        """Handle double-click rename of a layer or group node."""
        node = self._node_from_item(widget_item)
        if node is None or node.node_type == ITEM:
            return
        new_name = widget_item.text(col).strip()
        if not new_name or new_name == node.name:
            return
        # Update active layer name if this is the active layer
        if node.node_type == LAYER and node.name == self._active_layer_name:
            self._active_layer_name = new_name
        node.name = new_name
        self.refresh_layers_panel()

    def _sync_tree_from_widget(self):
        """After a drag-drop reorder in the widget, sync canvas_state order.

        The widget already shows the correct post-drop state so we must NOT
        call refresh_layers_panel — that would rebuild and duplicate rows.
        """
        root_wi = self._layers_tree.invisibleRootItem()
        new_layer_order = []
        for i in range(root_wi.childCount()):
            wi = root_wi.child(i)
            node = self._node_from_item(wi)
            if node and node.node_type == LAYER:
                new_layer_order.append(node.name)
                self._sync_subtree(wi, node)

        self.canvas_state.reorder_layers(new_layer_order)
        self._sync_z_order()

        # Save snapshot but suppress the panel refresh — widget is already correct
        self._is_syncing_tree = True
        try:
            self.save_undo_state()
        finally:
            self._is_syncing_tree = False

    def _sync_subtree(self, parent_wi, parent_node):
        """Recursively sync children order of parent_node from parent_wi."""
        child_nodes_in_order = []
        for i in range(parent_wi.childCount()):
            wi = parent_wi.child(i)
            node = self._node_from_item(wi)
            if node:
                child_nodes_in_order.append(node)
                self._sync_subtree(wi, node)
        parent_node.children = child_nodes_in_order
        for n in parent_node.children:
            n.parent = parent_node

    def _add_layer_dialog(self):
        name, ok = QInputDialog.getText(self, "New Layer", "Layer name:")
        if ok and name.strip():
            name = name.strip()
            self.canvas_state.add_layer_at(name, 0)
            self._ensure_text_layer_on_top()  # keep Text layer on top
            self._active_layer_name = name
            self.save_undo_state()
            self.refresh_layers_panel()

    def _delete_selected_layer_node(self):
        selected = self._layers_tree.selectedItems()
        if not selected:
            return
        wi = selected[0]
        node = self._node_from_item(wi)
        if node is None:
            return
        if node.node_type == ITEM:
            return  # don't delete individual items this way

        # Move children items to the layer below (or first remaining layer)
        all_layers = self.canvas_state.layers()
        if node.node_type == LAYER:
            # Find a fallback layer
            fallback = None
            for lyr in all_layers:
                if lyr is not node:
                    fallback = lyr
                    break
            if fallback is None and node.node_type == LAYER and len(all_layers) <= 1:
                QMessageBox.warning(self, "Cannot Delete", "You must have at least one layer.")
                return
            # Move all items to fallback (if any)
            if fallback:
                for child in list(node.all_item_nodes_flat()):
                    if child.parent:
                        child.parent.remove_child(child)
                    fallback.add_child(child)
            self.canvas_state.root.remove_child(node)
            # Update active layer
            if self._active_layer_name == node.name:
                remaining = self.canvas_state.layers()
                self._active_layer_name = remaining[0].name if remaining else "Layer 1"
                if not remaining:
                    self.canvas_state.add_layer(self._active_layer_name)
        elif node.node_type == GROUP:
            self.canvas_state.ungroup(node)

        self._sync_z_order()
        self.save_undo_state()
        self.refresh_layers_panel()

    def _layers_context_menu(self, pos):
        wi = self._layers_tree.itemAt(pos)
        if wi is None:
            return
        node = self._node_from_item(wi)
        if node is None:
            return

        menu = QMenu(self)
        if node.node_type == LAYER:
            act_rename = menu.addAction("Rename Layer")
            act_set_active = menu.addAction("Set as Active Layer")
            act_delete = menu.addAction("Delete Layer")
            chosen = menu.exec(self._layers_tree.viewport().mapToGlobal(pos))
            if chosen == act_rename:
                self._layers_tree.editItem(wi, 0)
            elif chosen == act_set_active:
                self._active_layer_name = node.name
                self._refresh_active_layer_display()
            elif chosen == act_delete:
                self._delete_selected_layer_node()
        elif node.node_type == GROUP:
            act_rename = menu.addAction("Rename Group")
            act_ungroup = menu.addAction("Ungroup")
            act_delete = menu.addAction("Delete Group")
            chosen = menu.exec(self._layers_tree.viewport().mapToGlobal(pos))
            if chosen == act_rename:
                self._layers_tree.editItem(wi, 0)
            elif chosen == act_ungroup:
                self.canvas_state.ungroup(node)
                self._sync_z_order()
                self.save_undo_state()
                self.refresh_layers_panel()
            elif chosen == act_delete:
                self._delete_selected_layer_node()
        elif node.node_type == ITEM:
            act_group = menu.addAction("Group with Selection")
            chosen = menu.exec(self._layers_tree.viewport().mapToGlobal(pos))
            if chosen == act_group:
                self.group_selected()

    # =============================================================
    #   GROUPING
    # =============================================================
    def group_selected(self):
        """Group currently selected canvas items under a new Group node."""
        selected = self.scene.selectedItems()
        nodes = [self.canvas_state.find_node_for_item(i) for i in selected]
        nodes = [n for n in nodes if n is not None]
        if len(nodes) < 2:
            return
        self.canvas_state.make_group(nodes, "Group")
        self._sync_z_order()
        self.save_undo_state()
        if hasattr(self, '_layers_panel') and self._layers_panel.isVisible():
            self.refresh_layers_panel()

    def ungroup_selected(self):
        """Ungroup any selected Group nodes in the layers tree."""
        # Find group nodes whose items are all selected, or that are selected
        # via the layers panel.  We look for GROUP nodes in the tree whose
        # children are all selected on the canvas.
        from canvas import GROUP as _GROUP
        ungrouped = False
        for node in list(self.canvas_state.root.all_nodes_flat()):
            if node.node_type == _GROUP:
                child_items = [c.item for c in node.children if c.item is not None]
                if child_items and all(i.isSelected() for i in child_items):
                    self.canvas_state.ungroup(node)
                    ungrouped = True
        if ungrouped:
            self._sync_z_order()
            self.save_undo_state()
            if hasattr(self, '_layers_panel') and self._layers_panel.isVisible():
                self.refresh_layers_panel()

    # =============================================================
    #   MODE TOGGLES
    # =============================================================
    def toggle_select(self):
        self.draw_mode = False
        self.eraser_mode = False
        self.btn_draw.setChecked(False)
        self.btn_eraser.setChecked(False)
        self.btn_sel.setChecked(True)
        self.pen_options_box.hide()

    def toggle_draw(self):
        self.draw_mode = self.btn_draw.isChecked()
        self.eraser_mode = False
        self.btn_eraser.setChecked(False)
        self.btn_sel.setChecked(not self.draw_mode)
        self.pen_options_box.setVisible(self.draw_mode)
        self.reposition_overlays()

    def toggle_eraser(self):
        self.eraser_mode = self.btn_eraser.isChecked()
        self.draw_mode = False
        self.btn_draw.setChecked(False)
        self.btn_sel.setChecked(not self.eraser_mode)
        # Keep pen_options_box visibility driven by selection (show when lasers selected)
        if not self.eraser_mode:
            self.pen_options_box.setVisible(False)
        else:
            self._on_selection_changed()

    # =============================================================
    #   SCENE ITEM MANAGEMENT
    # =============================================================
    def delete_selected(self):
        for i in self.scene.selectedItems():
            self.scene.removeItem(i)
        self.save_undo_state()

    def clear_screen(self):
        if QMessageBox.question(self, "Clear", "Clear all?") == QMessageBox.StandardButton.Yes:
            for i in self.scene.items():
                if isinstance(i, (DraggableElement, LaserPath, CanvasTextItem)):
                    self.scene.removeItem(i)
            self.save_undo_state()

    # =============================================================
    #   COLOR / LASER
    # =============================================================
    def _on_selection_changed(self):
        selected = self.scene.selectedItems()
        self.nudge_box.setVisible(len(selected) > 0)

        # Show pen_options_box whenever lasers are selected (not in draw mode,
        # where it's already shown for new-path settings).
        selected_lasers = [i for i in selected if isinstance(i, LaserPath)]
        if not self.draw_mode:
            if selected_lasers:
                # Seed controls from the first selected laser
                first = selected_lasers[0]
                self._block_laser_controls(True)
                self.opacity_slider.setValue(first.color.alpha())
                pct = int((first.color.alpha() / 255.0) * 100)
                self.opacity_input.setText(str(pct))
                self.arrow_check.setChecked(first.has_arrow)
                self._block_laser_controls(False)
                # Update color preview without touching current_laser_color
                col = first.color
                rgba = (f"rgba({col.red()},{col.green()},{col.blue()},"
                        f"{col.alphaF():.3f})")
                self.color_preview.setStyleSheet(
                    f"background: {rgba}; border: 1px solid black; border-radius: 4px;"
                )
                self.pen_options_box.setVisible(True)
                self._position_pen_options_for_selection()
            else:
                self.pen_options_box.setVisible(False)

        # Show text_options_box when text items are selected
        selected_text = [i for i in selected if isinstance(i, CanvasTextItem)]
        if selected_text:
            first = selected_text[0]
            # When editing, reflect format of selection or cursor; else use item font
            focus_item = self.scene.focusItem()
            if (
                focus_item is first
                and (first.textInteractionFlags() & Qt.TextInteractionFlag.TextEditorInteraction)
            ):
                cursor = first.textCursor()
                fmt = cursor.charFormat()
                f = fmt.font()
            else:
                f = first.font()
            self._block_text_controls = True
            self.text_font_size.setValue(f.pointSize() if f.pointSize() > 0 else 20)
            self.text_bold_btn.setChecked(f.bold())
            self.text_italic_btn.setChecked(f.italic())
            self.text_underline_btn.setChecked(f.underline())
            self._block_text_controls = False
            self.text_options_box.setVisible(True)
            self._position_text_options_for_selection()
        else:
            self.text_options_box.setVisible(False)

    def _position_pen_options_for_selection(self):
        """Position pen_options_box below the draw button (same as draw mode)."""
        p = self.btn_draw.mapTo(self, self.btn_draw.rect().bottomLeft())
        self.pen_options_box.move(p.x(), p.y())

    def _position_text_options_for_selection(self):
        """Position text_options_box below the Text button."""
        p = self.btn_text.mapTo(self, self.btn_text.rect().bottomLeft())
        self.text_options_box.move(p.x(), p.y())

    def _selected_text_items(self) -> list:
        return [i for i in self.scene.selectedItems() if isinstance(i, CanvasTextItem)]

    def _scene_update_items(self, items, margin: float = 2.0):
        """Invalidate only the region covered by items instead of the whole scene."""
        if not items:
            self.scene.update()
            return
        union = QRectF()
        for item in items:
            r = item.sceneBoundingRect()
            union = union.united(r) if not union.isNull() else r
        if union.isValid():
            self.scene.update(union.adjusted(-margin, -margin, margin, margin))

    def _on_text_font_size_changed(self, value: int):
        if self._block_text_controls:
            return
        items = self._selected_text_items()
        for item in items:
            f = item.font()
            f.setPointSize(value)
            item.setFont(f)
        self._scene_update_items(items)
        self.save_undo_state()

    def _on_text_style_changed(self):
        if self._block_text_controls:
            return
        bold = self.text_bold_btn.isChecked()
        italic = self.text_italic_btn.isChecked()
        underline = self.text_underline_btn.isChecked()
        size = self.text_font_size.value()
        focus_item = self.scene.focusItem()
        # When editing with cursor/selection, apply BIU only to selection (or cursor format)
        if isinstance(focus_item, CanvasTextItem) and (
            focus_item.textInteractionFlags() & Qt.TextInteractionFlag.TextEditorInteraction
        ):
            cursor = focus_item.textCursor()
            fmt = QTextCharFormat()
            fmt.setFontWeight(QFont.Weight.Bold if bold else QFont.Weight.Normal)
            fmt.setFontItalic(italic)
            fmt.setFontUnderline(underline)
            if cursor.hasSelection():
                cursor.mergeCharFormat(fmt)
                fmt_size = QTextCharFormat()
                fmt_size.setFontPointSize(size)
                cursor.mergeCharFormat(fmt_size)
            else:
                cursor.mergeCharFormat(fmt)
            focus_item.setTextCursor(cursor)
            self._scene_update_items([focus_item])
            self.save_undo_state()
            return
        # Not editing: apply to whole item (legacy)
        items = self._selected_text_items()
        for item in items:
            f = item.font()
            f.setPointSize(size)
            f.setBold(bold)
            f.setItalic(italic)
            f.setUnderline(underline)
            item.setFont(f)
        self._scene_update_items(items)
        self.save_undo_state()

    def _block_laser_controls(self, block: bool):
        self.opacity_slider.blockSignals(block)
        self.opacity_input.blockSignals(block)
        self.arrow_check.blockSignals(block)

    def _selected_lasers(self) -> list:
        return [i for i in self.scene.selectedItems() if isinstance(i, LaserPath)]

    def _apply_to_selected_lasers(self, color: "QColor | None" = None,
                                   alpha: "int | None" = None,
                                   has_arrow: "bool | None" = None):
        """Apply color/alpha/arrow changes to all currently selected LaserPaths."""
        lasers = self._selected_lasers()
        if not lasers:
            return
        for lp in lasers:
            new_col = QColor(lp.color)
            if color is not None:
                new_col.setRed(color.red())
                new_col.setGreen(color.green())
                new_col.setBlue(color.blue())
            if alpha is not None:
                new_col.setAlpha(alpha)
            lp.color = new_col
            lp.setPen(QPen(new_col, 7, Qt.PenStyle.SolidLine,
                           Qt.PenCapStyle.RoundCap))
            if has_arrow is not None:
                lp.has_arrow = has_arrow
            lp.update()
        self._scene_update_items(lasers)
        self.save_undo_state()

    def set_laser_color(self, color):
        if isinstance(color, str):
            alpha = self.current_laser_color.alpha()
            self.current_laser_color = QColor(color)
            self.current_laser_color.setAlpha(alpha)
        else:
            self.current_laser_color = color
            
        rgba = f"rgba({self.current_laser_color.red()}, {self.current_laser_color.green()}, {self.current_laser_color.blue()}, {self.current_laser_color.alphaF()})"
        self.color_preview.setStyleSheet(
            f"background: {rgba}; border: 1px solid black; border-radius: 4px;"
        )

    def set_laser_color_solid(self, hex_code):
        """Sets color to 100% opacity and updates slider."""
        new_col = QColor(hex_code)
        new_col.setAlpha(255)
        self.current_laser_color = QColor(new_col)
        self._block_laser_controls(True)
        self.opacity_slider.setValue(255)
        self.opacity_input.setText("100")
        self._block_laser_controls(False)
        self.update_laser_opacity_ui()
        self._apply_to_selected_lasers(color=new_col, alpha=255)

    def update_laser_opacity_from_slider(self, value):
        self.current_laser_color.setAlpha(value)
        percent = int((value / 255.0) * 100)
        self.opacity_input.blockSignals(True)
        self.opacity_input.setText(str(percent))
        self.opacity_input.blockSignals(False)
        self.update_laser_opacity_ui()
        self._apply_to_selected_lasers(alpha=value)

    def update_laser_opacity_from_input(self, text):
        try:
            percent = int(text)
            percent = max(0, min(100, percent))
            alpha = int((percent / 100.0) * 255)
            self.current_laser_color.setAlpha(alpha)
            self.opacity_slider.blockSignals(True)
            self.opacity_slider.setValue(alpha)
            self.opacity_slider.blockSignals(False)
            self.update_laser_opacity_ui()
            self._apply_to_selected_lasers(alpha=alpha)
        except ValueError:
            pass

    def update_laser_opacity_ui(self):
        rgba = (f"rgba({self.current_laser_color.red()},"
                f"{self.current_laser_color.green()},"
                f"{self.current_laser_color.blue()},"
                f"{self.current_laser_color.alphaF():.3f})")
        self.color_preview.setStyleSheet(
            f"background: {rgba}; border: 1px solid black; border-radius: 4px;"
        )

    def _on_arrow_toggled(self, checked: bool):
        self._apply_to_selected_lasers(has_arrow=checked)

    def pick_color(self):
        # Seed dialog from first selected laser if in selection mode
        lasers = self._selected_lasers()
        seed = lasers[0].color if lasers else self.current_laser_color
        col = QColorDialog.getColor(seed, self, "Select Laser Color")
        if col.isValid():
            alpha = seed.alpha()
            col.setAlpha(alpha)
            self.set_laser_color(col)
            self._apply_to_selected_lasers(color=col)


    # =============================================================
    #   ADD ELEMENTS / SAVE / LOAD
    # =============================================================
    def add_to_scene(self, path, name):
        item = DraggableElement(path, name, self)
        item.holes = self.load_hole_pattern(path)
        
        # Place at center of view
        center_pos = self.view.mapToScene(self.view.viewport().rect().center())
        item.setPos(center_pos)
        
        self.scene.addItem(item)
        item.setZValue(99999)  # Ensure it's seen as newest/highest by _rebuild_canvas_tree

        # Snap FIRST, then save the state so the undo point is correct
        item.snap_to_grid()
        self.save_undo_state()

    def add_textbox(self):
        """Add a text box at the center of the view; it goes in the 'Text' layer by default."""
        text_layer = self.canvas_state.get_layer("Text")
        if text_layer is None:
            text_layer = self.canvas_state.add_layer_at("Text", 0)  # Text layer always on top
        center_pos = self.view.mapToScene(self.view.viewport().rect().center())
        item = CanvasTextItem("Text", self)
        item.setPos(center_pos)
        self.scene.addItem(item)
        item.setZValue(99999)
        self._rebuild_canvas_tree()
        self.save_undo_state()


    def import_svg(self):
        p, _ = QFileDialog.getOpenFileName(self, "Open SVG", "", "SVG (*.svg)")
        if p:
            self._load_svg_file(p)

    def open_from_autosave(self):
        """Open a file from the autosave folder."""
        if not os.path.isdir(self.autosave_dir):
            QMessageBox.information(self, "Open from autosave", "No autosave files found.")
            return
        files = [f for f in os.listdir(self.autosave_dir) if f.lower().endswith(".svg")]
        if not files:
            QMessageBox.information(self, "Open from autosave", "No autosave files found.")
            return
        p, _ = QFileDialog.getOpenFileName(
            self, "Open from autosave", self.autosave_dir, "SVG (*.svg)"
        )
        if p:
            self._load_svg_file(p)

    def _load_svg_file(self, path):
        """Parse an app-exported (or Illustrator-edited) SVG and reconstruct
        the full canvas state: layers, groups, DraggableElements, LaserPaths."""
        import xml.etree.ElementTree as _ET
        import re as _re

        SVG_NS = "http://www.w3.org/2000/svg"
        INK_NS = "http://www.inkscape.org/namespaces/inkscape"

        # ── Build icon lookup: basename → full path ──────────────────────────
        icon_map = {}
        for _root, _dirs, _files in os.walk(self.icons_root):
            for _f in _files:
                if _f.lower().endswith(".svg"):
                    icon_map[_f] = os.path.join(_root, _f)

        # ── Parse SVG ────────────────────────────────────────────────────────
        try:
            tree = _ET.parse(path)
        except Exception as e:
            from PyQt6.QtWidgets import QMessageBox as _QMB
            _QMB.warning(self, "Open SVG", f"Could not parse SVG:\n{e}")
            return

        svg_root = tree.getroot()

        # ── Clear scene ──────────────────────────────────────────────────────
        self._is_loading = True
        for i in self.scene.items():
            if isinstance(i, (DraggableElement, LaserPath, CanvasTextItem)):
                self.scene.removeItem(i)
        self.canvas_state = CanvasState()

        # ── Transform parser ─────────────────────────────────────────────────
        def _parse_transform(t):
            """Return (x, y, rot) from an SVG transform string like
            'translate(x,y)' or 'translate(x,y) rotate(deg,cx,cy)'."""
            x = y = rot = 0.0
            m = _re.search(r'translate\(\s*([\-\d.]+)[,\s]+([\-\d.]+)\s*\)', t)
            if m:
                x, y = float(m.group(1)), float(m.group(2))
            m = _re.search(r'rotate\(\s*([\-\d.]+)', t)
            if m:
                rot = float(m.group(1))
            return x, y, rot

        # ── Recursive node parser ─────────────────────────────────────────────
        def _parse_node(xml_g, canvas_parent):
            tag = xml_g.tag.split("}")[-1] if "}" in xml_g.tag else xml_g.tag
            if tag != "g":
                return

            gid       = xml_g.get("id", "")
            label     = xml_g.get(f"{{{INK_NS}}}label", "")
            groupmode = xml_g.get(f"{{{INK_NS}}}groupmode", "")

            # Skip the breadboard layer
            if gid == "layer_Breadboard":
                return

            # ── Layer ────────────────────────────────────────────────────────
            if groupmode == "layer":
                name = label or gid
                layer = self.canvas_state.get_layer(name)
                if layer is None:
                    # SVG doc order is bottom-first (export appends reversed).
                    # Insert at 0 so last layer seen = topmost (index 0).
                    layer = self.canvas_state.add_layer_at(name, 0)
                # SVG children are written bottom-first (index 0 = bottom,
                # last = top). panel children[0] = topmost.
                # Iterate SVG children in reverse (last=top first) and
                # add_child each → children[0] = topmost. Correct.
                for child in reversed(list(xml_g)):
                    _parse_node(child, layer)
                return

            # ── Text (CanvasTextItem) ────────────────────────────────────────
            if gid.startswith("text_"):
                rect_el = xml_g.find(f"{{{SVG_NS}}}rect")
                text_el = xml_g.find(f"{{{SVG_NS}}}text")
                if rect_el is not None and text_el is not None:
                    # Use rect position (top-left of box), not text anchor, so position matches export
                    x = float(rect_el.get("x", 0))
                    y = float(rect_el.get("y", 0))
                    # Collect first line from <text> and subsequent lines from <tspan> children
                    parts = [(text_el.text or "").strip()]
                    for child in text_el:
                        if child.tag == f"{{{SVG_NS}}}tspan" or (child.tag and child.tag.split("}")[-1] == "tspan"):
                            parts.append((child.text or "").strip())
                    content = "\n".join(p for p in parts if p)
                    item = CanvasTextItem(content or "Text", self)
                    item.setPos(x, y)
                    self.scene.addItem(item)
                    node = CanvasNode("Text", ITEM, item=item, data={})
                    canvas_parent.add_child(node)
                return

            # ── DraggableElement ─────────────────────────────────────────────
            if gid.startswith("elem_"):
                transform = xml_g.get("transform", "")
                x, y, rot = _parse_transform(transform)
                svg_name  = label   # e.g. "Lens_1in.svg"
                svg_path  = icon_map.get(svg_name)
                if not svg_path:
                    return          # unknown element — skip gracefully
                item = DraggableElement(svg_path, svg_name, self)
                item.holes = self.load_hole_pattern(svg_path)
                item.snapping_enabled = False
                item.setPos(x, y)
                item.setRotation(rot)
                self.scene.addItem(item)
                item.snapping_enabled = True
                node = CanvasNode(svg_name, ITEM, item=item,
                                  data={"path": svg_path})
                canvas_parent.add_child(node)  # append: first processed = index 0 = top
                return

            # ── LaserPath ────────────────────────────────────────────────────
            if gid.startswith("laser_"):
                line_el = xml_g.find(f"{{{SVG_NS}}}line")
                poly_el = xml_g.find(f"{{{SVG_NS}}}polygon")
                if line_el is None:
                    return
                x1 = float(line_el.get("x1", 0))
                y1 = float(line_el.get("y1", 0))
                x2 = float(line_el.get("x2", 0))
                y2 = float(line_el.get("y2", 0))
                hex_col   = line_el.get("stroke", "#ff0000")
                alpha     = int(float(line_el.get("stroke-opacity", "1")) * 255)
                color     = QColor(hex_col)
                color.setAlpha(alpha)
                has_arrow = poly_el is not None
                lp = LaserPath.from_scene_endpoints(
                    QPointF(x1, y1), QPointF(x2, y2), color, has_arrow,
                )
                lp.color = color
                lp.setPen(QPen(color, 7, Qt.PenStyle.SolidLine,
                               Qt.PenCapStyle.RoundCap))
                self.scene.addItem(lp)
                node = CanvasNode("Laser Path", ITEM, item=lp, data={})
                canvas_parent.add_child(node)  # append: first processed = index 0 = top
                return

            # ── Group (any other <g>) ─────────────────────────────────────────
            group_node = CanvasNode(label or gid, GROUP)
            canvas_parent.add_child(group_node)
            for child in reversed(list(xml_g)):
                _parse_node(child, group_node)

        # ── Walk top-level <g> elements ──────────────────────────────────────
        for xml_g in svg_root:
            _parse_node(xml_g, self.canvas_state.root)

        # ── Ensure default layers exist; Text always on top ───────────────────
        existing_names = {l.name for l in self.canvas_state.layers()}
        if not existing_names:
            self.canvas_state.add_layer("Text")
            self.canvas_state.add_layer("Laser Paths")
            self.canvas_state.add_layer("Elements")
        self._ensure_text_layer_on_top()
        # Active layer defaults to Elements if present, otherwise first layer
        if "Elements" in existing_names:
            self._active_layer_name = "Elements"
        else:
            self._active_layer_name = self.canvas_state.layers()[0].name

        self._sync_z_order()
        self._is_loading = False

        # Reset undo stack to this clean state
        self.undo_stack = []
        self.redo_stack = []
        self.save_undo_state()

        if hasattr(self, '_layers_panel') and self._layers_panel.isVisible():
            self.refresh_layers_panel()

    # =============================================================
    #   SVG EXPORT
    # =============================================================
    def export_svg(self):
        """Export the canvas to SVG or PNG (format chosen in dialog)."""
        p, selected = QFileDialog.getSaveFileName(
            self, "Export", "", "SVG (*.svg);;PNG (*.png)"
        )
        if not p:
            return
        if "PNG" in (selected or "") or p.lower().endswith(".png"):
            if not p.lower().endswith(".png"):
                p += ".png"
            self._write_png_to_path(p)
        else:
            if not p.lower().endswith(".svg"):
                p += ".svg"
            self._write_svg_to_path(p)

    def _write_png_to_path(self, path):
        """Render the canvas to a PNG image (same area as SVG viewBox) at 2x resolution."""
        bb_rect = self.breadboard.boundingRect()
        scale = 2
        w = max(1, int(bb_rect.width() * scale))
        h = max(1, int(bb_rect.height() * scale))
        image = QImage(w, h, QImage.Format.Format_ARGB32)
        image.fill(Qt.GlobalColor.white)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.scene.render(
            painter,
            QRectF(0, 0, w, h),
            bb_rect,
        )
        painter.end()
        if not image.save(path, "PNG"):
            QMessageBox.warning(self, "Export PNG", f"Failed to save {path}")

    def _write_svg_to_path(self, path):
        """Build the full SVG tree and write it to path.

        Layer structure:
          - Each CanvasState layer → <g inkscape:groupmode="layer">
          - Groups → nested <g>
          - DraggableElement → inlined SVG content with transform
          - LaserPath → <line> + optional <polygon> arrow
          - Breadboard → bottom layer with inlined breadboard.svg content
        Layers are written bottom-first in the SVG so the topmost layer
        in the panel renders on top (later in document = higher paint order).
        """
        import xml.etree.ElementTree as _ET
        import math as _math

        # ── Namespaces ──────────────────────────────────────────────────────
        SVG_NS  = "http://www.w3.org/2000/svg"
        INK_NS  = "http://www.inkscape.org/namespaces/inkscape"
        SODI_NS = "http://sodipodi.sourceforge.net/DTD/sodipodi-0.0.dtd"

        # Register so ET uses clean prefixes (no ns0/ns1 garbage)
        _ET.register_namespace("",         SVG_NS)
        _ET.register_namespace("inkscape", INK_NS)
        _ET.register_namespace("sodipodi", SODI_NS)

        INK  = f"{{{INK_NS}}}"
        SODI = f"{{{SODI_NS}}}"

        # ── viewBox from breadboard bounding rect ────────────────────────────
        bb_rect = self.breadboard.boundingRect()
        vx = round(bb_rect.x(), 2)
        vy = round(bb_rect.y(), 2)
        vw = round(bb_rect.width(), 2)
        vh = round(bb_rect.height(), 2)

        # ── Root <svg> — only non-namespace attributes here ──────────────────
        # xmlns declarations come from ET.register_namespace; adding them
        # manually as attributes would cause duplicates and invalid XML.
        root_svg = _ET.Element(f"{{{SVG_NS}}}svg", {
            "width":   f"{vw}",
            "height":  f"{vh}",
            "viewBox": f"{vx} {vy} {vw} {vh}",
            f"{INK}document-units": "px",
        })

        # ── Helper: QColor → (#rrggbb, opacity_float) ───────────────────────
        # Illustrator does not support CSS rgba() in SVG paint attributes.
        # Use a hex color + a separate "opacity" attribute instead.
        def _color_parts(qcolor):
            r, g, b, a = qcolor.red(), qcolor.green(), qcolor.blue(), qcolor.alpha()
            return f"#{r:02x}{g:02x}{b:02x}", f"{a/255:.3f}"

        # ── Helper: strip SVG namespace from a tag ───────────────────────────
        def _local(tag):
            return tag.split("}")[-1] if "}" in tag else tag

        def _rewrite_url_refs(value, prefix):
            """Replace url(#id) with url(#prefix_id) so refs stay unique."""
            if not value or not prefix:
                return value
            return re.sub(r"url\s*\(\s*#([^)]+)\s*\)", lambda m: f"url(#{prefix}_{m.group(1)})", value)

        def _rewrite_class_in_css(css_text, prefix):
            """In <style> content, prefix class selectors so they don't clash across inlined SVGs."""
            if not css_text or not prefix:
                return css_text
            # .className -> .prefix_className (class selectors only; avoid matching numbers like 1.5)
            css_text = re.sub(r"\.([a-zA-Z_][a-zA-Z0-9_.-]*)", rf".{re.escape(prefix)}_\1", css_text)
            css_text = _rewrite_url_refs(css_text, prefix)
            return css_text

        # ── Helper: copy an ET element tree, optionally with unique IDs/classes ─
        def _copy_elem(src, prefix=None):
            """Deep-copy an ET element. If prefix is set, rewrite id, class, and url(#id)
            so multiple inlined SVGs don't override each other's styles or defs."""
            local = _local(src.tag)
            new_attrib = {}
            for k, v in (src.attrib or {}).items():
                if k == "id" and prefix:
                    v = f"{prefix}_{v}"
                elif k == "class" and prefix:
                    v = " ".join(f"{prefix}_{c}" for c in v.split())
                else:
                    v = _rewrite_url_refs(str(v), prefix) if prefix else v
                new_attrib[k] = v
            dst = _ET.Element(f"{{{SVG_NS}}}{local}", new_attrib)
            if src.text is not None:
                if local == "style" and prefix:
                    dst.text = _rewrite_class_in_css(src.text, prefix)
                else:
                    dst.text = _rewrite_url_refs(src.text, prefix) if prefix else src.text
            else:
                dst.text = src.text
            dst.tail = _rewrite_url_refs(src.tail, prefix) if (prefix and src.tail) else src.tail
            for child in src:
                dst.append(_copy_elem(child, prefix))
            return dst

        # ── Helper: get viewBox (min-x, min-y, width, height) from SVG root ──
        def _get_viewbox(svg_root):
            vb = svg_root.get("viewBox")
            if vb:
                parts = vb.strip().replace(",", " ").split()
                if len(parts) == 4:
                    return tuple(float(p) for p in parts)
            w = svg_root.get("width")
            h = svg_root.get("height")
            if w is not None and h is not None:
                try:
                    return (0, 0, float(w.replace("px", "").strip()), float(h.replace("px", "").strip()))
                except (ValueError, TypeError):
                    pass
            return None

        # ── Helper: inline an SVG file's children into a <g> ────────────────
        def _inline_svg_file(svg_path, prefix=None):
            """Return a list of ET elements from the root of an SVG file.
            If prefix is set (e.g. 'elem_12345'), ids and CSS classes are made unique
            so multiple inlined components don't clash."""
            try:
                tree = _ET.parse(svg_path)
                svg_root = tree.getroot()
                return [_copy_elem(c, prefix) for c in svg_root]
            except Exception:
                return []

        # ── Helper: build SVG transform string for a DraggableElement ───────
        def _element_transform(item):
            pos = item.pos()
            rot = item.rotation()
            cx  = item.transformOriginPoint().x()
            cy  = item.transformOriginPoint().y()
            # Qt applies: translate(pos) then rotate around transformOriginPoint
            # In SVG: translate to pos, rotate around the origin point
            if rot:
                return (f"translate({pos.x():.2f},{pos.y():.2f}) "
                        f"rotate({rot:.4f},{cx:.2f},{cy:.2f})")
            return f"translate({pos.x():.2f},{pos.y():.2f})"

        # ── Helper: serialise a LaserPath to ET elements ─────────────────────
        def _laser_elements(item):
            elems = []
            line  = item.line()
            # Map line endpoints from item-local → scene coords
            p1 = item.mapToScene(line.p1())
            p2 = item.mapToScene(line.p2())
            col_hex, opacity = _color_parts(item.color)

            line_el = _ET.Element(f"{{{SVG_NS}}}line", {
                "x1": f"{p1.x():.2f}", "y1": f"{p1.y():.2f}",
                "x2": f"{p2.x():.2f}", "y2": f"{p2.y():.2f}",
                "stroke":         col_hex,
                "stroke-width":   "7",
                "stroke-linecap": "round",
                "stroke-opacity": opacity,
                "fill":           "none",
            })
            elems.append(line_el)

            if item.has_arrow and line.length() >= 30:
                sx1, sy1 = p1.x(), p1.y()
                sx2, sy2 = p2.x(), p2.y()
                mx, my  = (sx1 + sx2) / 2, (sy1 + sy2) / 2
                angle   = _math.atan2(-(sy2 - sy1), sx2 - sx1)
                sz      = 30
                half    = sz / 2
                # Tip forward, base behind — arrowhead centred on midpoint
                tx  = mx + _math.cos(angle) * half
                ty  = my - _math.sin(angle) * half
                bx  = mx - _math.cos(angle) * half
                by  = my + _math.sin(angle) * half
                ax1 = bx + _math.sin(angle) * half
                ay1 = by + _math.cos(angle) * half
                ax2 = bx - _math.sin(angle) * half
                ay2 = by - _math.cos(angle) * half
                pts = f"{tx:.2f},{ty:.2f} {ax1:.2f},{ay1:.2f} {ax2:.2f},{ay2:.2f}"
                poly = _ET.Element(f"{{{SVG_NS}}}polygon", {
                    "points":       pts,
                    "fill":         col_hex,
                    "fill-opacity": opacity,
                    "stroke":       "none",
                })
                elems.append(poly)

            return elems

        def _text_elements(item):
            """Serialise a CanvasTextItem to SVG as a white rounded box + centered text."""
            # Box: use the item's boundingRect mapped into scene coordinates
            br = item.boundingRect()
            top_left = item.mapToScene(br.topLeft())
            rect_el = _ET.Element(f"{{{SVG_NS}}}rect", {
                "x": f"{top_left.x():.2f}",
                "y": f"{top_left.y():.2f}",
                "width": f"{br.width():.2f}",
                "height": f"{br.height():.2f}",
                "rx": "2",
                "ry": "2",
                "fill": "#ffffff",
                "stroke": "#000000",
                "stroke-width": "3",
            })

            # Text: position inside box using document margin and item font
            content = item.toPlainText() or ""
            font = item.font()
            font_pt = font.pointSize() if font.pointSize() > 0 else 20
            margin = item.document().documentMargin()
            # First line baseline: top of box + margin + ~80% of font size (typical ascent)
            baseline_y = top_left.y() + margin + font_pt * 0.8
            center_x = top_left.x() + br.width() / 2
            text_el = _ET.Element(f"{{{SVG_NS}}}text", {
                "x": f"{center_x:.2f}",
                "y": f"{baseline_y:.2f}",
                "text-anchor": "middle",
                "fill": "#000000",
                "font-family": font.family() or "sans-serif",
                "font-size": str(font_pt),
            })
            lines = content.split("\n")
            if not lines:
                text_el.text = ""
            else:
                text_el.text = lines[0]
                for line in lines[1:]:
                    tspan = _ET.SubElement(text_el, f"{{{SVG_NS}}}tspan", {
                        "x": f"{center_x:.2f}",
                        "dy": "1.2em",
                    })
                    tspan.text = line
            return [rect_el, text_el]

        # ── Recursive node serialiser ────────────────────────────────────────
        def _node_to_g(node):
            """Convert a CanvasNode to an ET element (or list of elements)."""
            from canvas import ITEM as _ITEM, LAYER as _LAYER, GROUP as _GROUP

            if node.node_type == _ITEM:
                item = node.item
                if item is None:
                    return []
                if isinstance(item, DraggableElement):
                    elem_prefix = f"elem_{id(item)}"
                    tf = _element_transform(item)
                    g = _ET.Element(f"{{{SVG_NS}}}g", {
                        "id":          elem_prefix,
                        f"{INK}label": node.name,
                        "transform":   tf,
                    })
                    inlined = _inline_svg_file(item.file_path, prefix=elem_prefix)
                    try:
                        _tree = _ET.parse(item.file_path)
                        _svg_root = _tree.getroot()
                        vb = _get_viewbox(_svg_root)
                    except Exception:
                        vb = None
                    if vb is not None and len(vb) == 4:
                        vx, vy, vw, vh = vb
                        inner_svg = _ET.Element(f"{{{SVG_NS}}}svg", {
                            "viewBox": f"{vx} {vy} {vw} {vh}",
                            "width": f"{vw}",
                            "height": f"{vh}",
                            "preserveAspectRatio": "xMinYMin meet",
                            "overflow": "visible",
                        })
                        for child_el in inlined:
                            inner_svg.append(child_el)
                        g.append(inner_svg)
                    else:
                        for child_el in inlined:
                            g.append(child_el)
                    return [g]
                elif isinstance(item, LaserPath):
                    g = _ET.Element(f"{{{SVG_NS}}}g", {
                        "id":          f"laser_{id(item)}",
                        f"{INK}label": node.name,
                    })
                    for el in _laser_elements(item):
                        g.append(el)
                    return [g]
                elif isinstance(item, CanvasTextItem):
                    g = _ET.Element(f"{{{SVG_NS}}}g", {
                        "id":          f"text_{id(item)}",
                        f"{INK}label": "Text",
                    })
                    for el in _text_elements(item):
                        g.append(el)
                    return [g]
                return []

            # LAYER or GROUP
            is_layer = (node.node_type == _LAYER)
            attribs = {
                "id":            f"layer_{node.name.replace(' ', '_')}_{id(node)}",
                f"{INK}label":   node.name,
            }
            if is_layer:
                attribs[f"{INK}groupmode"] = "layer"
                attribs[f"{SODI}insensitive"] = "false"

            g = _ET.Element(f"{{{SVG_NS}}}g", attribs)
            # children[0] is topmost in the panel (highest z).
            # In SVG, later elements paint on top, so iterate in reverse:
            # bottom child first → top child last → top child renders on top.
            for child in reversed(node.children):
                for el in _node_to_g(child):
                    g.append(el)
            return [g]

        # ── Build layers (bottom-first in SVG = rendered first = behind) ─────
        # canvas_state.layers()[0] is the topmost panel layer → goes last in SVG
        layers = self.canvas_state.layers()
        layer_elements = []
        for layer_node in layers:
            for el in _node_to_g(layer_node):
                layer_elements.append(el)

        # ── Breadboard layer (bottom); locked so it stays locked in Illustrator ─
        bb_g = _ET.Element(f"{{{SVG_NS}}}g", {
            "id":                f"layer_Breadboard",
            f"{INK}label":       "Breadboard",
            f"{INK}groupmode":   "layer",
            f"{SODI}insensitive":"true",
        })
        for child_el in _inline_svg_file(self._breadboard_path):
            bb_g.append(child_el)

        # Append breadboard first (bottom), then user layers bottom→top
        root_svg.append(bb_g)
        for el in reversed(layer_elements):   # reversed → bottom layer first
            root_svg.append(el)

        # ── Write file ───────────────────────────────────────────────────────
        try:
            _ET.indent(root_svg, space="  ")
        except AttributeError:
            pass  # Python < 3.9 fallback

        tree_out = _ET.ElementTree(root_svg)
        with open(path, "w", encoding="utf-8") as f:
            f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
            f.write(_ET.tostring(root_svg, encoding="unicode"))

    def _do_autosave(self):
        """Save a timestamped SVG to autosave/ and keep at most 10 files."""
        if self._is_loading:
            return
        os.makedirs(self.autosave_dir, exist_ok=True)
        ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        path = os.path.join(self.autosave_dir, f"autosave_{ts}.svg")
        try:
            self._write_svg_to_path(path)
        except Exception:
            return
        # Cap at 10 files: list *.svg by mtime newest first, delete oldest if > 10
        try:
            files = [
                os.path.join(self.autosave_dir, f)
                for f in os.listdir(self.autosave_dir)
                if f.lower().endswith(".svg")
            ]
            files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
            for old in files[10:]:
                try:
                    os.remove(old)
                except Exception:
                    pass
        except Exception:
            pass

    # =============================================================
    #   HOLE MANAGER
    # =============================================================
    def open_hole_manager(self):
        dlg = HoleManagerDialog(self.categories, self)
        dlg.exec()


    def closeEvent(self, event):
        reply = QMessageBox.question(
            self, "Confirm Exit",
            "Are you sure you want to close Lego Designer?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )

        if reply == QMessageBox.StandardButton.Yes:
            try:
                self.scene.selectionChanged.disconnect()
            except:
                pass
            event.accept()
        else:
            event.ignore()

    # =============================================================
    #   Search
    # =============================================================
    def filter_sidebar(self, text):
        search_text = text.lower().strip()
        any_found = False

        # Iterate through all CollapsibleCategory widgets in the layout
        for i in range(self.acc_layout.count()):
            widget = self.acc_layout.itemAt(i).widget()
            if isinstance(widget, CollapsibleCategory):
                # We tell the category to filter its own internal buttons
                has_matches = widget.apply_filter(search_text)
                
                # If a category has matches, show it, otherwise hide the whole category
                widget.setVisible(has_matches)
                if has_matches:
                    any_found = True

        # Show/Hide the "Nothing found" label
        self.no_results_label.setVisible(not any_found and search_text != "")

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.ToolTip:
            # If the object has a tooltip, show it immediately
            if isinstance(obj, QWidget) and obj.toolTip():
                # Position it tight to the cursor. 
                # We'll use a negative Y offset to bring it up closer to the tip.
                pos = event.globalPos()
                pos.setX(pos.x() + 10)
                pos.setY(pos.y() - 30) 
                QToolTip.showText(pos, obj.toolTip(), obj)
                return True
        return super().eventFilter(obj, event)



if __name__ == "__main__":
    app = QApplication(sys.argv)
    
    window = LegoDesigner()
    window.show()
    sys.exit(app.exec())

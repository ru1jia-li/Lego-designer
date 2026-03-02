import os, json
import xml.etree.ElementTree as ET

from PyQt6.QtWidgets import (
    QDialog, QHBoxLayout, QVBoxLayout, QScrollArea, QWidget, QLabel,
    QPushButton, QGraphicsScene, QGraphicsView, QMessageBox,
    QGraphicsEllipseItem, QGraphicsItem, QLineEdit
)
from PyQt6.QtGui import (
    QIcon, QPainter, QBrush, QColor, QPen, QKeySequence, QShortcut, QTransform
)
from PyQt6.QtCore import (
    Qt, QPointF, QSize, QLineF, QEvent
)
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtSvgWidgets import QGraphicsSvgItem

from elements import DraggableElement
from canvas import CanvasState, CanvasNode, LAYER, ITEM


HOLE_DB_PATH = "icons/hole_database.json"


def load_hole_db():
    if not os.path.exists(HOLE_DB_PATH):
        return {}
    try:
        with open(HOLE_DB_PATH, "r") as f:
            return json.load(f)
    except:
        return {}


def save_hole_db(db):
    with open(HOLE_DB_PATH, "w") as f:
        json.dump(db, f, indent=4)


class HoleManagerDialog(QDialog):
    def __init__(self, categories_data, parent_app):
        super().__init__(parent_app)
        self.setWindowTitle("Precision Hole Editor")
        self.resize(1200, 800)
        self.parent_app = parent_app
        self.current_svg_path = None
        self.hole_db = load_hole_db()

        # State
        self.construction_step = 0
        self.temp_center = None
        self.ghost_circle = None

        self.undo_stack = []
        self.undo_index = -1
        self.baseline_index = -1  # cannot undo past this
        self.copied_holes = []
        self._move_undo_armed = False  # for move-undo
        self.canvas_state = CanvasState()  # hierarchical layer tree

        main_layout = QHBoxLayout(self)

        # --- LEFT SIDE: Palette ---
        palette_scroll = QScrollArea()
        palette_scroll.setFixedWidth(260)
        palette_scroll.setWidgetResizable(True)
        palette_scroll.setStyleSheet("""
            QScrollArea {
                border: 1px solid #ccc;
                background: #f8f8f8;
            }
        """)

        palette_widget = QWidget()
        palette_layout = QVBoxLayout(palette_widget)
        palette_layout.setContentsMargins(8, 8, 8, 8)
        palette_layout.setSpacing(3)

        palette_widget.setStyleSheet("""
            QWidget {
                background-color: #f8f8f8;
            }
            QLabel#catHeader {
                font-weight: bold;
                font-size: 14px;
                padding: 4px 2px 2px 2px;
                color: #555;
            }
            QPushButton {
                text-align: left;
                padding: 4px 8px;
                margin: 1px 0;
                background-color: white;
                border: 1px solid #ccc;
                border-radius: 4px;
                font-size: 14px;
            }
            QPushButton:hover {
                background-color: #e6f0ff;
                border-color: #88aaff;
            }
        """)

        # --- SEARCH BAR ---
        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("Search parts...")
        self._search_input.setStyleSheet("padding: 4px; border-radius: 3px; border: 1px solid #AAA; background: white;")
        self._search_input.textChanged.connect(self._filter_palette)
        palette_layout.addWidget(self._search_input)

        self._no_results_label = QLabel("Nothing found")
        self._no_results_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._no_results_label.setStyleSheet("color: gray; font-style: italic; margin-top: 10px; font-weight: normal;")
        self._no_results_label.hide()
        palette_layout.addWidget(self._no_results_label)

        # Store categories as (header_label, [(btn, name_lower), ...])
        self._palette_categories = []

        for cat_name, icons in categories_data.items():
            header = QLabel(cat_name)
            header.setObjectName("catHeader")
            palette_layout.addWidget(header)

            cat_buttons = []
            for path, name in icons:
                btn = QPushButton(name)
                btn.setIcon(QIcon(path))
                btn.setIconSize(QSize(20, 20))
                btn.clicked.connect(lambda ch, p=path: self.load_preview(p))
                palette_layout.addWidget(btn)
                cat_buttons.append((btn, name.lower()))

            self._palette_categories.append((header, cat_buttons))

        palette_scroll.setWidget(palette_widget)
        main_layout.addWidget(palette_scroll)

        palette_layout.addStretch()

        # --- RIGHT SIDE: Workspace ---
        workspace_container = QWidget()
        workspace_layout = QVBoxLayout(workspace_container)

        ribbon_layout = QHBoxLayout()

        self.btn_auto = QPushButton("Auto‑Detect")
        self.btn_auto.clicked.connect(self.auto_detect_holes)
        ribbon_layout.addWidget(self.btn_auto)

        self.file_label = QLabel("")
        self.file_label.setStyleSheet("font-weight: bold; font-size: 14px;")
        ribbon_layout.addWidget(self.file_label)

        ribbon_layout.addStretch()

        self.instr = QLabel("Mode: Select & Move")
        self.instr.setStyleSheet("font-size: 13px; color: #444;")
        ribbon_layout.addWidget(self.instr)

        self.btn_add = QPushButton("➕")
        self.btn_add.clicked.connect(self.enter_add_mode)

        self.btn_del = QPushButton("🗑️")
        self.btn_del.clicked.connect(self.delete_selected)

        self.btn_save = QPushButton("💾")
        self.btn_save.clicked.connect(self.save_holes)

        for b in [self.btn_add, self.btn_del, self.btn_save]:
            b.setFixedSize(40, 40)
            ribbon_layout.addWidget(b)

        workspace_layout.addLayout(ribbon_layout)

        self.scene = QGraphicsScene()
        self.view = QGraphicsView(self.scene)
        self.view.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.view.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
        workspace_layout.addWidget(self.view)

        main_layout.addWidget(workspace_container, stretch=3)

        self.view.viewport().installEventFilter(self)
        self.view.setMouseTracking(True)

        # Floating vertical circular toolbar on top-left
        self.floating_tools = QWidget(self.view.viewport())
        self.floating_tools.setStyleSheet("""
            QWidget {
                background: rgba(255, 255, 255, 200);
                border: 1px solid #aaa;
                border-radius: 10px;
            }
            QPushButton {
                border: 1px solid #888;
                background: white;
                border-radius: 18px;
                min-width: 36px;
                min-height: 36px;
                max-width: 36px;
                max-height: 36px;
                font-size: 16px;
            }
            QPushButton:hover {
                background: #e6f0ff;
            }
        """)
        tools_layout = QVBoxLayout(self.floating_tools)
        tools_layout.setContentsMargins(6, 6, 6, 6)
        tools_layout.setSpacing(6)

        btn_copy = QPushButton("✂️")
        btn_copy.clicked.connect(self.copy_selected)
        btn_paste = QPushButton("📋")
        btn_paste.clicked.connect(self.paste)
        btn_undo = QPushButton("↩️")
        btn_undo.clicked.connect(self.undo)
        btn_redo = QPushButton("↪️")
        btn_redo.clicked.connect(self.redo)

        for b in [btn_copy, btn_paste, btn_undo, btn_redo]:
            tools_layout.addWidget(b)

        self.floating_tools.adjustSize()
        self.floating_tools.move(10, 10)
        self.floating_tools.raise_()

        # Shortcuts
        QShortcut(QKeySequence("Ctrl+C"), self, activated=self.copy_selected)
        QShortcut(QKeySequence("Ctrl+V"), self, activated=self.paste)
        QShortcut(QKeySequence("Ctrl+Z"), self, activated=self.undo)
        QShortcut(QKeySequence("Ctrl+Shift+Z"), self, activated=self.redo)
        QShortcut(QKeySequence("Ctrl+Y"), self, activated=self.redo)

    # ----------------------------------------------------------
    # LOAD SVG + HOLES
    # ----------------------------------------------------------
    def load_preview(self, svg_path):
        self.current_svg_path = svg_path
        self.scene.clear()
        self.undo_stack = []
        self.undo_index = -1
        self.baseline_index = -1
        self._move_undo_armed = False

        # Reset the canvas state tree for this SVG
        self.canvas_state = CanvasState()
        self._svg_layer   = self.canvas_state.add_layer("SVG Layer")
        self._holes_layer = self.canvas_state.add_layer("Holes")

        self.renderer = QSvgRenderer(svg_path)
        self.svg_item = QGraphicsSvgItem()
        self.svg_item.setSharedRenderer(self.renderer)
        self.scene.addItem(self.svg_item)

        # Register the SVG item in the tree
        self._svg_layer.add_child(CanvasNode("svg", ITEM, item=self.svg_item,
                                             data={"path": svg_path}))

        self.file_label.setText(os.path.basename(svg_path))

        tree = ET.parse(svg_path)
        root = tree.getroot()

        viewBox = root.get("viewBox")
        if viewBox:
            x0, y0, w0, h0 = map(float, viewBox.split())
        else:
            w0 = float(root.get("width", 1))
            h0 = float(root.get("height", 1))
            x0 = y0 = 0

        br = self.svg_item.boundingRect()
        self.svg_scale_x = br.width() / w0
        self.svg_scale_y = br.height() / h0

        key = os.path.basename(svg_path)

        if key in self.hole_db:
            for hole in self.hole_db[key]:
                sx = hole["x"] * self.svg_scale_x
                sy = hole["y"] * self.svg_scale_y
                sr = hole["r"] * self.svg_scale_x
                self._create_hole_item(QPointF(sx, sy), sr)
        else:
            self.auto_detect_holes()

        self.view.setSceneRect(self.svg_item.boundingRect())
        self.view.fitInView(self.svg_item.boundingRect(), Qt.AspectRatioMode.KeepAspectRatio)

        # Baseline snapshot (cannot undo past this)
        self._push_snapshot()
        self.baseline_index = self.undo_index

    # ----------------------------------------------------------
    # AUTO DETECT HOLES
    # ----------------------------------------------------------
    def auto_detect_holes(self):
        if not self.current_svg_path:
            return

        for item in list(self.svg_item.childItems()):
            if getattr(item, "is_hole_marker", False):
                self.scene.removeItem(item)

        tree = ET.parse(self.current_svg_path)
        root = tree.getroot()

        circles = []
        for elem in root.iter():
            if elem.tag.lower().endswith("circle"):
                try:
                    cx = float(elem.get("cx"))
                    cy = float(elem.get("cy"))
                    r  = float(elem.get("r"))
                    circles.append((cx, cy, r))
                except:
                    pass

        # Remove concentric duplicates (keep smallest)
        filtered = []
        for cx, cy, r in circles:
            is_dup = False
            for fx, fy, fr in filtered:
                if abs(cx - fx) < 0.1 and abs(cy - fy) < 0.1:
                    if r >= fr:
                        is_dup = True
                    else:
                        filtered.remove((fx, fy, fr))
                    break
            if not is_dup:
                filtered.append((cx, cy, r))

        for cx, cy, r in filtered:
            sx = cx * self.svg_scale_x
            sy = cy * self.svg_scale_y
            sr = r  * self.svg_scale_x
            self._create_hole_item(QPointF(sx, sy), sr)

        self.instr.setText(f"Auto‑detected {len(filtered)} holes")

    # ----------------------------------------------------------
    # INTERNAL: CREATE HOLE ITEM (no undo)
    # ----------------------------------------------------------
    def _create_hole_item(self, center, r):
        dot = QGraphicsEllipseItem(-r, -r, r*2, r*2)
        dot.setParentItem(self.svg_item)
        dot.setZValue(9999)
        dot.setPos(center)
        dot.setBrush(QBrush(QColor(255, 0, 0, 90)))
        dot.setPen(QPen(QColor(255, 0, 0), 0.8))
        dot.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsMovable |
            QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
        )
        dot.is_hole_marker = True

        # Register this hole in the canvas state tree
        if hasattr(self, "_holes_layer"):
            idx = len(self._holes_layer.children)
            node = CanvasNode(
                f"hole_{idx}", ITEM, item=dot,
                data={"cx": center.x(), "cy": center.y(), "r": r},
            )
            self._holes_layer.add_child(node)

        return dot

    # ----------------------------------------------------------
    # PUBLIC: ADD HOLE (with undo)
    # ----------------------------------------------------------
    def add_hole_marker(self, center, r):
        dot = self._create_hole_item(center, r)
        self._push_snapshot()
        return dot

    # ----------------------------------------------------------
    # ADD MODE
    # ----------------------------------------------------------
    def enter_add_mode(self):
        self.construction_step = 1
        self.instr.setText("➕ Click Center")

    # ----------------------------------------------------------
    # EVENT FILTER
    # ----------------------------------------------------------
    def eventFilter(self, source, event):
        if not hasattr(self, "svg_item") or self.svg_item is None:
            return super().eventFilter(source, event)

        # Arm undo for move when clicking on a hole in normal mode
        if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
            scene_pos = self.view.mapToScene(event.pos())
            item = self.scene.itemAt(scene_pos, QTransform())
            if (
                item is not None and
                getattr(item, "is_hole_marker", False) and
                self.construction_step == 0
            ):
                self._move_undo_armed = True

        if event.type() == QEvent.Type.MouseButtonRelease and self._move_undo_armed:
            self._move_undo_armed = False
            # snapshot AFTER move completes
            self._push_snapshot()

        # Manual add mode
        if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
            scene_pos = self.view.mapToScene(event.pos())
            local_pos = self.svg_item.mapFromScene(scene_pos)

            svg_x = local_pos.x() / self.svg_scale_x
            svg_y = local_pos.y() / self.svg_scale_y

            if self.construction_step == 1:
                self.temp_center = QPointF(svg_x, svg_y)
                self.construction_step = 2
                self.instr.setText("➕ Move for Radius")
                self.ghost_circle = QGraphicsEllipseItem(0, 0, 0, 0)
                self.ghost_circle.setParentItem(self.svg_item)
                self.ghost_circle.setPen(QPen(Qt.GlobalColor.red, 1, Qt.PenStyle.DashLine))
                return True

            elif self.construction_step == 2:
                radius = QLineF(self.temp_center, QPointF(svg_x, svg_y)).length()
                cx = self.temp_center.x() * self.svg_scale_x
                cy = self.temp_center.y() * self.svg_scale_y
                cr = radius * self.svg_scale_x

                self.add_hole_marker(QPointF(cx, cy), cr)

                if self.ghost_circle:
                    self.scene.removeItem(self.ghost_circle)
                self.ghost_circle = None
                self.construction_step = 0
                self.instr.setText("Mode: Select & Move")
                return True

        elif event.type() == QEvent.Type.MouseMove:
            if self.construction_step == 2 and self.ghost_circle:
                scene_pos = self.view.mapToScene(event.pos())
                local_pos = self.svg_item.mapFromScene(scene_pos)

                svg_x = local_pos.x() / self.svg_scale_x
                svg_y = local_pos.y() / self.svg_scale_y

                radius = QLineF(self.temp_center, QPointF(svg_x, svg_y)).length()
                cr = radius * self.svg_scale_x

                self.ghost_circle.setRect(-cr, -cr, cr*2, cr*2)
                self.ghost_circle.setPos(
                    self.temp_center.x() * self.svg_scale_x,
                    self.temp_center.y() * self.svg_scale_y
                )
                return True

        return super().eventFilter(source, event)

    # ----------------------------------------------------------
    # DELETE
    # ----------------------------------------------------------
    def delete_selected(self):
        changed = False
        for item in self.scene.selectedItems():
            if getattr(item, "is_hole_marker", False):
                self.scene.removeItem(item)
                changed = True
        if changed:
            self._push_snapshot()

    # ----------------------------------------------------------
    # SAVE HOLES → JSON
    # ----------------------------------------------------------
    def save_holes(self):
        if not self.current_svg_path:
            #print("[SAVE HOLES] No current_svg_path → aborting")
            return

        key = os.path.basename(self.current_svg_path)
        holes = []

        #print(f"[SAVE HOLES] Scanning children of svg_item for key: {key}")

        for item in self.svg_item.childItems():
            if getattr(item, "is_hole_marker", False):
                px = item.pos().x() / self.svg_scale_x
                py = item.pos().y() / self.svg_scale_y
                r = (item.rect().width() / 2) / self.svg_scale_x
                holes.append({"x": px, "y": py, "r": r})
                #print(f"  → found hole: x={px:.3f}, y={py:.3f}, r={r:.3f}")

        #print(f"[SAVE HOLES] Collected {len(holes)} holes")

        self.hole_db[key] = holes

        full_path = HOLE_DB_PATH
        #print(f"[SAVE HOLES] Attempting to write to: {os.path.abspath(full_path)}")

        try:
            save_hole_db(self.hole_db)
            #print("[SAVE HOLES] Write successful")
            QMessageBox.information(self, "Saved", "Hole data updated.")
            
            # Refresh holes in main application immediately
            if self.parent_app:
                # Re-detect breadboard holes if the current file is the breadboard
                bb_basename = os.path.basename(getattr(self.parent_app, "_breadboard_path", ""))
                if key == bb_basename:
                    self.parent_app.detect_breadboard_holes()
                    self.parent_app.view.breadboard_holes = self.parent_app.breadboard_holes
                
                # Update all existing items in the scene to reflect new hole positions
                for item in self.parent_app.scene.items():
                    if isinstance(item, DraggableElement):
                        # Reload hole pattern for this item type
                        item.holes = self.parent_app.load_hole_pattern(item.file_path)
                        # Re-snap to the potentially updated grid
                        item.snap_to_grid()
        except Exception as e:
            #print(f"[SAVE HOLES] ERROR writing file: {e}")
            QMessageBox.warning(self, "Save Failed", f"Could not save:\n{str(e)}")

    # ----------------------------------------------------------
    # UNDO/REDO CORE
    # ----------------------------------------------------------
    def _snapshot_state(self):
        state = []
        for item in self.svg_item.childItems():
            if getattr(item, "is_hole_marker", False):
                px = item.pos().x()
                py = item.pos().y()
                r = item.rect().width() / 2
                state.append((px, py, r))
        return state

    def _restore_state(self, state):
        # Clear existing markers
        for item in list(self.svg_item.childItems()):
            if getattr(item, "is_hole_marker", False):
                self.scene.removeItem(item)
        # Restore from state
        for px, py, r in state:
            self._create_hole_item(QPointF(px, py), r)

    def _push_snapshot(self):
        # Trim redo history
        self.undo_stack = self.undo_stack[:self.undo_index + 1]
        
        new_state = self._snapshot_state()
        # Only push if different from current top
        if not self.undo_stack or self.undo_stack[self.undo_index] != new_state:
            self.undo_stack.append(new_state)
            self.undo_index += 1

    def undo(self):
        if self.undo_index <= 0:
            return
        self.undo_index -= 1
        self._restore_state(self.undo_stack[self.undo_index])

    def redo(self):
        if self.undo_index >= len(self.undo_stack) - 1:
            return
        self.undo_index += 1
        self._restore_state(self.undo_stack[self.undo_index])

    # ----------------------------------------------------------
    # COPY / PASTE
    # ----------------------------------------------------------
    def copy_selected(self):
        self.copied_holes = []
        for item in self.scene.selectedItems():
            if getattr(item, "is_hole_marker", False):
                px = item.pos().x()
                py = item.pos().y()
                r = item.rect().width() / 2
                self.copied_holes.append((px, py, r))

    def paste(self):
        if not self.copied_holes:
            return

        # Deselect everything first so only pasted items end up selected
        for item in self.scene.selectedItems():
            item.setSelected(False)

        for px, py, r in self.copied_holes:
            dot = self._create_hole_item(QPointF(px + 10, py + 10), r)
            dot.setSelected(True)

        self._push_snapshot()

    # ----------------------------------------------------------
    # SEARCH / FILTER PALETTE
    # ----------------------------------------------------------
    def _filter_palette(self, text):
        search = text.lower().strip()
        any_visible = False
        for header, cat_buttons in self._palette_categories:
            cat_has_match = False
            for btn, name in cat_buttons:
                visible = (search in name) if search else True
                btn.setVisible(visible)
                if visible:
                    cat_has_match = True
            # Hide the whole category header if none of its items match
            header.setVisible(cat_has_match)
            if cat_has_match:
                any_visible = True
        self._no_results_label.setVisible(bool(search) and not any_visible)

    # ----------------------------------------------------------
    # KEEP FLOATING TOOLS IN CORNER ON RESIZE
    # ----------------------------------------------------------
    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.floating_tools is not None:
            self.floating_tools.move(10, 10)
            self.floating_tools.raise_()

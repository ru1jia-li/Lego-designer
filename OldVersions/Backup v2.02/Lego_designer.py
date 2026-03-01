import sys
import os
import json
import xml.etree.ElementTree as ET

import math

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QPushButton, QVBoxLayout, QWidget, QHBoxLayout,
    QFrame, QScrollArea, QLabel, QFileDialog, QCheckBox, QMessageBox,
    QGridLayout, QGraphicsScene, QColorDialog
)
from PyQt6.QtCore import Qt, QPointF, QRectF, QTimer, QLineF
from PyQt6.QtGui import QPixmap, QPainter, QIcon, QColor, QPen
from PyQt6.QtSvgWidgets import QGraphicsSvgItem

from elements import DraggableElement, LaserPath
from view import CustomGraphicsView
from dialogs import PropertyPopup, CollapsibleCategory
from holes import HoleManagerDialog

import logging




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
        """)

        # ---------- Main layout ----------
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        self.main_layout = QHBoxLayout(main_widget)
        self.main_layout.setContentsMargins(0, 0, 0, 0)

        # =========================================================
        #   SIDEBAR (categories)
        # =========================================================
        sidebar = QFrame()
        sidebar.setFixedWidth(280)
        sidebar.setStyleSheet("background: #E0E0E0; border-right: 1px solid #AAA;")

        side_layout = QVBoxLayout(sidebar)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("border: none;")

        self.acc_layout = QVBoxLayout()
        self.acc_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        content = QWidget()
        content.setLayout(self.acc_layout)
        scroll.setWidget(content)
        side_layout.addWidget(scroll)

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

        for sym, func in [
            ("💾", self.save_layout),
            ("📤", self.import_layout),
            ("↩️", self.undo_action),
            ("↪️", self.redo_action),
            ("🧹", self.clear_screen),
        ]:
            b = QPushButton(sym)
            b.setObjectName("ToolBtn")
            b.setFixedSize(50, 32)
            b.clicked.connect(func)
            tl.addWidget(b)

        tl.addSpacing(25)

        # Select / draw / eraser
        self.btn_sel = QPushButton("👆")
        self.btn_sel.setObjectName("ToolBtn")
        self.btn_sel.setFixedSize(50, 32)
        self.btn_sel.setCheckable(True)
        self.btn_sel.setChecked(True)
        self.btn_sel.clicked.connect(self.toggle_select)
        tl.addWidget(self.btn_sel)

        self.btn_draw = QPushButton("🖍️")
        self.btn_draw.setObjectName("ToolBtn")
        self.btn_draw.setFixedSize(50, 32)
        self.btn_draw.setCheckable(True)
        self.btn_draw.clicked.connect(self.toggle_draw)
        tl.addWidget(self.btn_draw)

        self.btn_eraser = QPushButton("🧽")
        self.btn_eraser.setObjectName("ToolBtn")
        self.btn_eraser.setFixedSize(50, 32)
        self.btn_eraser.setCheckable(True)
        self.btn_eraser.clicked.connect(self.toggle_eraser)
        tl.addWidget(self.btn_eraser)

        # Hole manager
        self.btn_review = QPushButton("🔍 Review Holes")
        self.btn_review.setObjectName("ToolBtn")
        self.btn_review.setFixedSize(140, 32)
        self.btn_review.clicked.connect(self.open_hole_manager)
        tl.addWidget(self.btn_review)

        tl.addStretch()
        self.view_layout.addWidget(self.toolbar)

        # ----- Scene + view -----
        self.scene = QGraphicsScene()
        self.view = CustomGraphicsView(self.scene, self)
        self.view.setStyleSheet("background: white; border: none;")
        self.view_layout.addWidget(self.view)

        self.main_layout.addWidget(view_container)

        # =========================================================
        #   BREADBOARD + HOLES
        # =========================================================
        self.breadboard = QGraphicsSvgItem("breadboard.svg")
        self.breadboard.setZValue(-1)
        self.scene.addItem(self.breadboard)

        self.detect_breadboard_holes()
        self.view.breadboard_holes = self.breadboard_holes


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
        #   MINIMAP TIMER + INITIAL STATE
        # =========================================================
        self.map_timer = QTimer()
        self.map_timer.timeout.connect(self.update_minimap)
        self.map_timer.start(33)

        self.set_laser_color("#FF0000")
        self.save_undo_state(initial=True)

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
            ("◁", 1, 0, -1,  0), ("↻", 1, 1, 0,  0), ("▷", 1, 2, 1,  0),
            ("◺", 2, 0, -1,  1), ("▽", 2, 1, 0,  1), ("◿", 2, 2, 1,  1),
        ]

        for sym, r, c, dx, dy in directions:
            btn = QPushButton(sym)
            btn.setFixedSize(40, 40)
            btn.setStyleSheet(nudge_style)
	
            if sym == "↻":
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
        self.btn_del_btn = QPushButton("🗑")

        style = (
            "background: rgba(255,255,255,180); border: 1px solid #999; "
            "border-radius: 22px; font-weight: bold; font-size: 22pt;"
        )
        for b in [self.btn_plus, self.btn_minus, self.btn_del_btn]:
            b.setStyleSheet(style)
            b.setFixedSize(45, 45)

        self.btn_plus.clicked.connect(lambda: self.view.scale(1.2, 1.2))
        self.btn_minus.clicked.connect(lambda: self.view.scale(1 / 1.2, 1 / 1.2))
        self.btn_del_btn.clicked.connect(self.delete_selected)

        ol.addWidget(self.btn_plus)
        ol.addWidget(self.btn_minus)
        ol.addWidget(self.btn_del_btn)

        # ----- Minimap -----
        self.minimap = QLabel(self.view)
        self.minimap.setFixedSize(220, 160)
        self.minimap.setStyleSheet("background: #D0D0D0; border: 2px solid #999;")

        self.reposition_overlays()
        self.scene.selectionChanged.connect(
            lambda: self.nudge_box.setVisible(len(self.scene.selectedItems()) > 0)
        )

        # ----- Pen options overlay -----
        self.pen_options_box = QFrame(self)
        self.pen_options_box.setVisible(False)
        self.pen_options_box.setStyleSheet(
            "background: white; border: 1px solid #AAA; border-radius: 5px;"
        )
        self.pen_options_box.setFixedWidth(210)
        self.pen_options_box.setFixedHeight(45)

        pob_layout = QHBoxLayout(self.pen_options_box)
        self.arrow_check = QCheckBox("Arrow")
        self.arrow_check.setChecked(True)
        pob_layout.addWidget(self.arrow_check)

        self.color_preview = QPushButton()
        self.color_preview.setFixedSize(22, 22)
        self.color_preview.clicked.connect(self.pick_color)
        pob_layout.addWidget(self.color_preview)

        for c in ["#FF0000", "#00FF00", "#0000FF"]:
            cb = QPushButton()
            cb.setFixedSize(16, 16)
            cb.setStyleSheet(f"background: {c}; border-radius: 8px;")
            cb.clicked.connect(lambda ch, col=c: self.set_laser_color(col))
            pob_layout.addWidget(cb)

    def reposition_overlays(self):
        self.overlay.move(self.view.width() - 60, 20)
        self.minimap.move(self.view.width() - 240, self.view.height() - 180)
        self.nudge_box.move((self.view.width() - 130) // 2, self.view.height() - 150)
        if self.draw_mode:
            p = self.btn_draw.mapTo(self, self.btn_draw.rect().bottomLeft())
            self.pen_options_box.move(p.x(), p.y())

    def resizeEvent(self, e):
        self.reposition_overlays()
        super().resizeEvent(e)

    # =============================================================
    #   BREADBOARD HOLES
    # =============================================================
    def detect_breadboard_holes(self):
        try:
            tree = ET.parse("breadboard.svg")
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


    # =============================================================
    #   HOLE PATTERN (JSON) FOR PARTS
    # =============================================================
    def load_hole_pattern(self, svg_path):
        try:
            with open(os.path.join(self.icons_root, "hole_database.json"), "r") as f:
                db = json.load(f)
        except: return []

        key = os.path.basename(svg_path)
        if key not in db: return []

        holes = []
        # Create a dummy item just to use its mapToScene capability
        # This ensures we use the item's NATURAL coordinate system
        temp_item = QGraphicsSvgItem(svg_path)
        
        for h in db[key]:
            # NO MORE sx or sy multiplication. 
            # We use the raw numbers from the JSON database.
            lx, ly = float(h["x"]), float(h["y"])
            holes.append(QPointF(lx, ly))
        return holes


    
    # =============================================================
    #   MINIMAP
    # =============================================================
    def update_minimap(self):
        try:
            rect = self.breadboard.boundingRect()
            if rect.width() <= 0:
                return

            thumb = QPixmap(self.minimap.size())
            thumb.fill(QColor("#D0D0D0"))

            p = QPainter(thumb)
            self.scene.render(p, QRectF(thumb.rect()), rect)
            p.end()

            vr = self.view.mapToScene(self.view.viewport().rect()).boundingRect()
            xr = thumb.width() / rect.width()
            yr = thumb.height() / rect.height()

            p = QPainter(thumb)
            p.setPen(QPen(QColor("#666666"), 2))
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
        # Toggle debug messages here (change to False when you don't need them)
        debug_nudge = False

        if debug_nudge:
            print(f"[NUDGE] called with dx={dx}, dy={dy}")

        items = self.scene.selectedItems()
        if not items:
            if debug_nudge:
                print("[NUDGE] no items selected")
            return

        if not self.breadboard_holes:
            if debug_nudge:
                print("[NUDGE] no breadboard holes")
            return

        moved_count = 0

        # Tolerance for "same column/row" in cardinal directions
        ALIGN_TOL = 6.0

        # For diagonal: how close Δx and Δy should be (ratio)
        DIAGONAL_BALANCE_TOL = 0.35

        for item in items:
            if not isinstance(item, DraggableElement) or not item.holes:
                if debug_nudge:
                    print(f"[NUDGE] skip {getattr(item, 'name', 'item')} — no holes")
                continue

            ref = item.mapToScene(item.holes[0])
            current = min(
                self.breadboard_holes,
                key=lambda h: (h - ref).manhattanLength(),
                default=None
            )
            if current is None:
                if debug_nudge:
                    print(f"[NUDGE] no current hole for {getattr(item,'name','item')}")
                continue

            if debug_nudge:
                print(f"[NUDGE] {getattr(item, 'name', 'item')} | ref ≈ ({ref.x():.1f}, {ref.y():.1f})")
                print(f"  current: ({current.x():.1f}, {current.y():.1f})")

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
                if debug_nudge:
                    print(f"[NUDGE] no valid target for {getattr(item,'name','item')}")
                continue

            delta_scene = best_target - ref
            move_len = delta_scene.manhattanLength()

            if debug_nudge:
                print(f"[NUDGE] → moving to ({best_target.x():.1f}, {best_target.y():.1f})  len = {move_len:.1f}")

            if 1 < move_len < 300:
                item.setPos(item.pos() + delta_scene)
                moved_count += 1
                if debug_nudge:
                    print(f"  → MOVED to ≈ ({item.pos().x():.1f}, {item.pos().y():.1f})")

        if moved_count > 0 and not self._is_loading:
            if debug_nudge:
                print(f"[NUDGE] moved {moved_count} item(s)")
            self.save_undo_state()
        elif debug_nudge:
            print("[NUDGE] nothing moved")

    def rotate_selected_90(self):
        for i in self.scene.selectedItems():
            if isinstance(i, DraggableElement):
                i.setRotation(i.rotation() + 90)
        self.save_undo_state()

    # =============================================================
    #   UNDO / REDO
    # =============================================================
    def save_undo_state(self, initial=False):
        if self._is_loading:
            return

        state = []
        for i in self.scene.items():
            if isinstance(i, DraggableElement):
                state.append({
                    "t": "i",
                    "p": i.file_path,
                    "x": i.pos().x(),
                    "y": i.pos().y(),
                    "r": i.rotation(),
                    "z": i.zValue(),
                })
            elif isinstance(i, LaserPath):
                state.append({
                    "t": "l",
                    "x1": i.line().x1(),
                    "y1": i.line().y1(),
                    "x2": i.line().x2(),
                    "y2": i.line().y2(),
                    "c": i.color.name(),
                    "a": i.has_arrow,
                })

        self.undo_stack.append(json.dumps(state))
        if not initial:
            self.redo_stack.clear()

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
        items = json.loads(js)

        for i in self.scene.items():
            if isinstance(i, (DraggableElement, LaserPath)):
                self.scene.removeItem(i)

        for d in items:
            if d["t"] == "i":
                item = DraggableElement(d["p"], "item", self)
                # critical: load holes from JSON
                item.holes = self.load_hole_pattern(d["p"])
                item.setPos(d["x"], d["y"])
                item.setRotation(d["r"])
                item.setZValue(d.get("z", 5))
                self.scene.addItem(item)
            else:
                lp = LaserPath(
                    QLineF(d["x1"], d["y1"], d["x2"], d["y2"]),
                    QColor(d.get("c", "#FF0000")),
                    d.get("a", True),
                )
                self.scene.addItem(lp)

        self._is_loading = False

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
        self.pen_options_box.hide()

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
                if isinstance(i, (DraggableElement, LaserPath)):
                    self.scene.removeItem(i)
            self.save_undo_state()

    # =============================================================
    #   COLOR / LASER
    # =============================================================
    def set_laser_color(self, hex_code):
        self.current_laser_color = QColor(hex_code)
        self.current_laser_color.setAlpha(180)
        self.color_preview.setStyleSheet(
            f"background: {hex_code}; border: 1px solid black;"
        )

    def pick_color(self):
        col = QColorDialog.getColor(self.current_laser_color)
        if col.isValid():
            self.set_laser_color(col.name())

    # =============================================================
    #   ADD ELEMENTS / SAVE / LOAD
    # =============================================================
    def add_to_scene(self, path, name):
        item = DraggableElement(path, name, self)
        item.holes = self.load_hole_pattern(path)
        if item.holes:
            item.setTransformOriginPoint(item.holes[0])
        item.setPos(self.view.mapToScene(self.view.viewport().rect().center()))
        self.scene.addItem(item)
        item.snap_to_grid()
        self.save_undo_state()


    def save_layout(self):
        p, _ = QFileDialog.getSaveFileName(self, "Save", "", "JSON (*.json)")
        if p and self.undo_stack:
            with open(p, "w") as f:
                f.write(self.undo_stack[-1])

    def import_layout(self):
        p, _ = QFileDialog.getOpenFileName(self, "Open", "", "JSON (*.json)")
        if p:
            with open(p, "r") as f:
                self.undo_stack = [f.read()]
                self.load_state(self.undo_stack[-1])

    # =============================================================
    #   HOLE MANAGER
    # =============================================================
    def open_hole_manager(self):
        dlg = HoleManagerDialog(self.categories, self)
        dlg.exec()


    def closeEvent(self, event):
        try:
            self.scene.selectionChanged.disconnect()
        except:
            pass
        super().closeEvent(event)



if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = LegoDesigner()
    window.show()
    sys.exit(app.exec())

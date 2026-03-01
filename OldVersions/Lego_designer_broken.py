import sys
import os
import json
import xml.etree.ElementTree as ET

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

SNAP_TOLERANCE = 12.0   # pixels in scene space
MIN_HOLES_TO_LOCK = 1   # or 2 if you want stricter locking



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
            "border-radius: 5px; font-size: 14pt;"
        )

        directions = [
            ("↖", 0, 0, -1, -1), ("▲", 0, 1, 0, -1), ("↗", 0, 2, 1, -1),
            ("◀", 1, 0, -1,  0), ("⟳", 1, 1, 0,  0), ("▶", 1, 2, 1,  0),
            ("↙", 2, 0, -1,  1), ("▼", 2, 1, 0,  1), ("↘", 2, 2, 1,  1),
        ]

        for sym, r, c, dx, dy in directions:
            btn = QPushButton(sym)
            btn.setFixedSize(40, 40)
            btn.setStyleSheet(nudge_style)
            if sym == "⟳":
                btn.clicked.connect(self.rotate_selected_90)
            else:
                btn.clicked.connect(lambda ch, x=dx, y=dy: self.nudge_selected(x, y))
            grid.addWidget(btn, r, c)

        # Center rotate button (duplicate, but keeps layout explicit)
        rot_btn = QPushButton("⟳")
        rot_btn.setFixedSize(38, 38)
        rot_btn.setStyleSheet(nudge_style)
        rot_btn.clicked.connect(self.rotate_selected_90)
        grid.addWidget(rot_btn, 1, 1)

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
        """Fix: Use raw coordinates. mapToScene handles scaling automatically."""
        try:
            tree = ET.parse("breadboard.svg")
            root = tree.getroot()
            holes = []
            for elem in root.iter():
                if elem.tag.endswith("circle"):
                    # 1. Get raw SVG coordinates from XML
                    cx = float(elem.get("cx"))
                    cy = float(elem.get("cy"))
                    # 2. Map directly to scene. mapToScene correctly translates
                    #    the SVG's internal units to actual scene pixels.
                    scene_pt = self.breadboard.mapToScene(QPointF(cx, cy))
                    #holes.append(scene_pt)
            self.breadboard_holes = holes
        except Exception as e:
            print("Hole detection failed:", e)

    def load_hole_pattern(self, svg_path):
        """Fix: Return raw coordinates without manual multipliers."""
        try:
            with open(os.path.join(self.icons_root, "hole_database.json"), "r") as f:
                db = json.load(f)
        except:
            return []

        key = os.path.basename(svg_path)
        if key not in db:
            return []

        # Return raw points. DraggableElement (a QGraphicsSvgItem) treats 
        # these as 'local' coordinates relative to its own internal SVG viewBox.
        return [QPointF(float(h["x"]), float(h["y"])) for h in db[key]]

    def detect_lattice(self):
        """Automatically find the basis vectors of the breadboard pattern."""
        if not self.breadboard_holes or len(self.breadboard_holes) < 10:
            return None, None, None

        # 1. Use the center hole as our origin reference
        origin = self.breadboard_holes[len(self.breadboard_holes) // 2]
        
        # 2. Find neighbors to determine spacing
        deltas = []
        for h in self.breadboard_holes[:500]: # Sample first 500 for speed
            diff = h - origin
            dist = math.sqrt(diff.x()**2 + diff.y()**2)
            if 5 < dist < 50: # Only look at nearby holes
                deltas.append((diff.x(), diff.y()))
        
        if not deltas: return None, None, None

        # 3. Identify the two primary basis vectors (v1: horizontal, v2: diagonal/vertical)
        deltas.sort(key=lambda d: d[0]**2 + d[1]**2)
        v1 = deltas[0] # The closest neighbor
        
        v2 = None
        for d in deltas[1:]:
            # Check for linear independence (ensure v2 isn't just v1 in reverse)
            dot = abs(v1[0]*d[0] + v1[1]*d[1])
            mag_prod = math.sqrt(v1[0]**2 + v1[1]**2) * math.sqrt(d[0]**2 + d[1]**2)
            if dot / mag_prod < 0.9: # If angle is significant, it's our second vector
                v2 = d
                break
        
        return origin, v1, v2

    def initialize_grid(self):
        """Call this once to set up the mathematical grid."""
        self.grid_origin, self.grid_v1, self.grid_v2 = self.detect_lattice()


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
        items = self.scene.selectedItems()
        if not items or not self.breadboard_holes:
            return

        # direction as a vector in scene space
        dir_vec = QPointF(dx, dy)
        if dir_vec == QPointF(0, 0):
            return

        for item in items:
            if not isinstance(item, DraggableElement) or not item.holes:
                continue

            # use the first hole as reference
            primary_global = item.mapToScene(item.holes[0])

            # find the current nearest breadboard hole (where we "are" now)
            current = min(
                self.breadboard_holes,
                key=lambda h: (h - primary_global).manhattanLength()
            )

            # now find the next breadboard hole in the requested direction
            best_candidate = None
            best_proj = None

            for h in self.breadboard_holes:
                vec = h - current
                # skip if it's the same hole
                if vec == QPointF(0, 0):
                    continue

                # projection of vec onto dir_vec (sign tells direction)
                dot = vec.x() * dir_vec.x() + vec.y() * dir_vec.y()
                if dot <= 0:
                    continue  # behind or perpendicular

                # we want the closest hole in that direction → smallest positive dot
                if best_proj is None or dot < best_proj:
                    best_proj = dot
                    best_candidate = h

            if best_candidate is None:
                continue  # nothing in that direction

            delta = best_candidate - primary_global
            item.setPos(item.pos() + delta)

        self.save_undo_state()


   


    def find_best_hole_near(self, item, target_pos):
        """Used by nudge: search for best snap near a target position."""
        min_dist = 40.0
        best_pos = target_pos
        found = False

        for my_hole in item.holes:
            future_hole_scene = target_pos + my_hole

            for other in self.scene.items():
                if other == item or not hasattr(other, "holes"):
                    continue
                for target_hole in other.holes:
                    target_hole_scene = other.mapToScene(target_hole)
                    delta = target_hole_scene - future_hole_scene
                    dist = (delta.x() ** 2 + delta.y() ** 2) ** 0.5
                    if dist < min_dist:
                        min_dist = dist
                        best_pos = target_pos + delta
                        found = True

        return best_pos if found else None

    def get_snapped_at_point(self, target_scene_pos, dragging_item):
        """
        Used during drag: find best snap position for dragging_item
        if it were placed at target_scene_pos.
        """
        best_pos = target_scene_pos
        min_dist = 20.0
        found = False

        other_items = [
            i for i in self.scene.items()
            if isinstance(i, DraggableElement) and i != dragging_item
        ]

        for my_hole in dragging_item.holes:
            my_hole_future = target_scene_pos + my_hole

            for other in other_items:
                for target_hole in other.holes:
                    target_hole_scene = other.mapToScene(target_hole)
                    dist_vec = target_hole_scene - my_hole_future
                    dist = (dist_vec.x() ** 2 + dist_vec.y() ** 2) ** 0.5

                    if dist < min_dist:
                        min_dist = dist
                        best_pos = target_scene_pos + dist_vec
                        found = True

        return best_pos

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

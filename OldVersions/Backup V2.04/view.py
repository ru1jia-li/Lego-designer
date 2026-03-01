from PyQt6.QtWidgets import QGraphicsView
from PyQt6.QtCore import Qt, QPointF, QLineF, QRectF
from PyQt6.QtGui import QColor
from elements import DraggableElement, LaserPath

class CustomGraphicsView(QGraphicsView):
    def __init__(self, scene, parent=None):
        super().__init__(scene, parent)
        self.main_app = parent
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.FullViewportUpdate)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self._is_panning = self._is_drawing = self._is_erasing = False

        # IMPORTANT: this will be filled by main window:
        # self.breadboard_holes = [...]
        # (LegoDesigner sets this after detect_breadboard_holes)
    
    # ---------------------------------------------------------
    # UNIVERSAL SNAP FUNCTION (breadboard holes only)
    # ---------------------------------------------------------
    def snap_to_nearest_hole(self, scene_pos):
        """Return the nearest breadboard hole to scene_pos."""
        if not hasattr(self, "breadboard_holes"):
            return scene_pos

        best = None
        best_dist = 999999

        for hole in self.breadboard_holes:
            d = (hole.x() - scene_pos.x())**2 + (hole.y() - scene_pos.y())**2
            if d < best_dist:
                best_dist = d
                best = hole

        return best if best else scene_pos

    def snap_laser_to_fine_grid(self, scene_pos):
        """Laser-specific snap: strictly uses the high-density fine grid."""
        grid = getattr(self.main_app, "fine_grid_points", [])
        if not grid:
            return scene_pos # Fallback if cache isn't loaded

        # Minimal one-liner to find the closest fine-grid point
        return min(grid, key=lambda h: (h.x() - scene_pos.x())**2 + (h.y() - scene_pos.y())**2)



    # ---------------------------------------------------------
    # ZOOM
    # ---------------------------------------------------------
    def wheelEvent(self, event):
        f = 1.08
        if event.angleDelta().y() > 0:
            self.scale(f, f)
        else:
            self.scale(1/f, 1/f)

    # ---------------------------------------------------------
    # KEYBOARD SHORTCUTS
    # ---------------------------------------------------------
    def keyPressEvent(self, event):
        modifiers = event.modifiers()
        is_ctrl = modifiers & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier)

        if is_ctrl:
            if event.key() == Qt.Key.Key_Z:
                self.main_app.undo_action()
                return
            elif event.key() == Qt.Key.Key_Y:
                self.main_app.redo_action()
                return
            elif event.key() == Qt.Key.Key_C:
                self.main_app.copy_selected()
                return
            elif event.key() == Qt.Key.Key_V:
                self.main_app.paste_items()
                return

        if event.key() == Qt.Key.Key_Space:
            self.main_app.toggle_select()
        elif event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.main_app.delete_selected()

        super().keyPressEvent(event)

    # ---------------------------------------------------------
    # MOUSE PRESS
    # ---------------------------------------------------------
    def mousePressEvent(self, event):
        item = self.itemAt(event.position().toPoint())

        # --- ERASER MODE ---
        if self.main_app.eraser_mode:
            self._is_erasing = True
            # Use a small area to find items, making it easier to hit thin lines
            click_pos = self.mapToScene(event.position().toPoint())
            rect = QRectF(click_pos.x() - 5, click_pos.y() - 5, 10, 10)
            items = self.scene().items(rect)
            for item in items:
                if isinstance(item, LaserPath):
                    self.scene().removeItem(item)
                    self.main_app.save_undo_state()
                    break
            return

        # --- DRAW MODE (LASER) ---
        if self.main_app.draw_mode:
            self._is_drawing = True

            # Convert → snap
            scene_pt = self.mapToScene(event.position().toPoint())
            scene_pt = self.snap_laser_to_fine_grid(scene_pt) 


            self._temp_line = LaserPath(
                QLineF(scene_pt, scene_pt),
                QColor(self.main_app.current_laser_color),
                self.main_app.arrow_check.isChecked()
            )
            self.scene().addItem(self._temp_line)
            return

        # --- PANNING ---
        if event.button() == Qt.MouseButton.LeftButton and (item is None or item == self.main_app.breadboard):
            self._is_panning = True
            self._last_pan_pos = event.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            return

        # --- RUBBER BAND ---
        if event.button() == Qt.MouseButton.RightButton and (item is None or item == self.main_app.breadboard):
            self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)

        # --- DEFAULT (dragging items) ---
        super().mousePressEvent(event)

    # ---------------------------------------------------------
    # MOUSE MOVE
    # ---------------------------------------------------------
    def mouseMoveEvent(self, event):
        if self._is_panning:
            delta = event.position() - self._last_pan_pos
            self._last_pan_pos = event.position()
            self.horizontalScrollBar().setValue(int(self.horizontalScrollBar().value() - delta.x()))
            self.verticalScrollBar().setValue(int(self.verticalScrollBar().value() - delta.y()))
            return

        if self._is_drawing:
            scene_pt = self.mapToScene(event.position().toPoint())
            
            # CHANGE THIS LINE:
            scene_pt = self.snap_laser_to_fine_grid(scene_pt)
            
            self._temp_line.setLine(QLineF(self._temp_line.line().p1(), scene_pt))
            return
        super().mouseMoveEvent(event)

    # ---------------------------------------------------------
    # MOUSE RELEASE
    # ---------------------------------------------------------
    def mouseReleaseEvent(self, event):
        if self._is_drawing:
            if hasattr(self, '_temp_line') and self._temp_line:
                if self._temp_line.line().length() < 1.0:
                    self.scene().removeItem(self._temp_line)
                else:
                    self.main_app.save_undo_state()
            self._temp_line = None

        self._is_panning = self._is_drawing = self._is_erasing = False
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setCursor(Qt.CursorShape.ArrowCursor)

        super().mouseReleaseEvent(event)

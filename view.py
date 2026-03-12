import math
from PyQt6.QtWidgets import QGraphicsView
from PyQt6.QtCore import Qt, QLineF, QRectF, QPointF
from PyQt6.QtGui import QColor
from elements import LaserPath

class CustomGraphicsView(QGraphicsView):
    def __init__(self, scene, parent=None):
        super().__init__(scene, parent)
        self.main_app = parent
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.SmartViewportUpdate)
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
            from elements import CanvasTextItem
            focus_item = self.scene().focusItem()
            # When editing a text box, let Ctrl+C / Ctrl+V do text copy/paste
            editing_text = (
                isinstance(focus_item, CanvasTextItem)
                and (focus_item.textInteractionFlags() & Qt.TextInteractionFlag.TextEditorInteraction)
            )
            if event.key() == Qt.Key.Key_Z:
                self.main_app.undo_action()
                return
            elif event.key() == Qt.Key.Key_Y:
                self.main_app.redo_action()
                return
            elif event.key() == Qt.Key.Key_C:
                if editing_text:
                    super().keyPressEvent(event)
                    return
                self.main_app.copy_selected()
                return
            elif event.key() == Qt.Key.Key_V:
                if editing_text:
                    super().keyPressEvent(event)
                    return
                self.main_app.paste_items()
                return
            elif event.key() == Qt.Key.Key_G:
                is_shift = modifiers & Qt.KeyboardModifier.ShiftModifier
                if is_shift:
                    self.main_app.ungroup_selected()
                else:
                    self.main_app.group_selected()
                return
            elif event.key() in (Qt.Key.Key_Plus, Qt.Key.Key_Equal):
                self.scale(1.2, 1.2)
                return
            elif event.key() == Qt.Key.Key_Minus:
                self.scale(1 / 1.2, 1 / 1.2)
                return

        if event.key() == Qt.Key.Key_Space:
            self.main_app.toggle_select()
        elif event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            from elements import CanvasTextItem
            focus_item = self.scene().focusItem()
            # Only delete the box when selected but not editing text; else let Backspace edit text
            if isinstance(focus_item, CanvasTextItem) and (
                focus_item.textInteractionFlags() & Qt.TextInteractionFlag.TextEditorInteraction
            ):
                super().keyPressEvent(event)
                return
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
            self.erase_at_pos(event.position().toPoint())
            return

        # --- DRAW MODE (LASER) — left-click only so right-click can do rubber-band ---
        if self.main_app.draw_mode and event.button() == Qt.MouseButton.LeftButton:
            self._is_drawing = True

            # Convert → snap
            scene_pt = self.mapToScene(event.position().toPoint())
            scene_pt = self.snap_laser_to_fine_grid(scene_pt) 


            self._temp_line = LaserPath.from_scene_endpoints(
                scene_pt, scene_pt,
                QColor(self.main_app.current_laser_color),
                self.main_app.arrow_check.isChecked()
            )
            self._temp_line.setZValue(99999)
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

        if self._is_erasing:
            self.erase_at_pos(event.position().toPoint())
            return

        if self._is_drawing:
            scene_pt = self.mapToScene(event.position().toPoint())
            
            # CHANGE THIS LINE:
            scene_pt = self.snap_laser_to_fine_grid(scene_pt)
            
            # Keep building line; will normalize on release
            p1 = self._temp_line.mapToScene(self._temp_line.line().p1())
            self._temp_line.setLine(QLineF(-1, 0, 1, 0))  # placeholder
            self._temp_line.setPos(QPointF((p1.x() + scene_pt.x()) / 2, (p1.y() + scene_pt.y()) / 2))
            dx = scene_pt.x() - p1.x()
            dy = scene_pt.y() - p1.y()
            length = max(math.hypot(dx, dy), 1e-6)
            angle_deg = math.degrees(math.atan2(dy, dx))
            self._temp_line.setLine(QLineF(-length / 2, 0, length / 2, 0))
            self._temp_line.setRotation(angle_deg)
            return
        super().mouseMoveEvent(event)

    def erase_at_pos(self, viewport_pos):
        """Helper to erase laser paths or text boxes at a given viewport position."""
        from elements import LaserPath, CanvasTextItem
        scene_pt = self.mapToScene(viewport_pos)
        rect = QRectF(scene_pt.x() - 8, scene_pt.y() - 8, 16, 16)
        items = self.scene().items(rect)

        erased = False
        for item in items:
            if isinstance(item, (LaserPath, CanvasTextItem)):
                self.scene().removeItem(item)
                erased = True

        if erased:
            self.main_app.save_undo_state()

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

        # When finishing a rubber-band drag, explicitly select every item whose bbox intersects the rect
        # so that "everything in the box" is selected (Qt's default can miss some items).
        if self.dragMode() == QGraphicsView.DragMode.RubberBandDrag:
            vp_rect = self.rubberBandRect()
            if not vp_rect.isEmpty():
                tl = self.mapToScene(vp_rect.topLeft())
                br = self.mapToScene(vp_rect.bottomRight())
                scene_rect = QRectF(tl, br).normalized()
                self.scene().clearSelection()
                for item in self.scene().items(scene_rect):
                    if item == getattr(self.main_app, "breadboard", None):
                        continue
                    if item.flags() & item.GraphicsItemFlag.ItemIsSelectable:
                        item.setSelected(True)

        self._is_panning = self._is_drawing = self._is_erasing = False
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setCursor(Qt.CursorShape.ArrowCursor)

        super().mouseReleaseEvent(event)

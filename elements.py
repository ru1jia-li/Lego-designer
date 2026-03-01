import os
import math
import xml.etree.ElementTree as ET
from PyQt6.QtWidgets import QGraphicsLineItem, QGraphicsEllipseItem
from PyQt6.QtCore import Qt, QPointF, QLineF, QRectF
from PyQt6.QtGui import QColor, QPen, QPolygonF, QBrush
from PyQt6.QtSvgWidgets import QGraphicsSvgItem

from dialogs import PropertyPopup


# ─────────────────────────────────────────────────────────────────────────────
# Endpoint handle for LaserPath
# ─────────────────────────────────────────────────────────────────────────────
_HANDLE_RADIUS = 7

class _EndpointHandle(QGraphicsEllipseItem):
    """Draggable dot attached to one endpoint of a LaserPath."""

    def __init__(self, laser_path: "LaserPath", endpoint_index: int):
        r = _HANDLE_RADIUS
        super().__init__(-r, -r, 2 * r, 2 * r)
        self._lp = laser_path
        self._idx = endpoint_index          # 0 = p1, 1 = p2
        self.setBrush(QBrush(QColor(255, 255, 255)))
        self.setPen(QPen(QColor(0, 120, 215), 2))
        self.setZValue(100000)
        self.setFlags(self.GraphicsItemFlag.ItemIsMovable)
        self.setCursor(Qt.CursorShape.SizeAllCursor)

    def itemChange(self, change, value):
        if change == self.GraphicsItemChange.ItemPositionHasChanged:
            self._update_laser_endpoint(self.pos())
        return super().itemChange(change, value)

    def _update_laser_endpoint(self, scene_pt):
        line = self._lp.line()
        local = self._lp.mapFromScene(scene_pt)
        if self._idx == 0:
            self._lp.setLine(QLineF(local, line.p2()))
        else:
            self._lp.setLine(QLineF(line.p1(), local))

    def mousePressEvent(self, event):
        # Temporarily disable the parent laser's movability so dragging
        # this handle doesn't also drag the whole line.
        self._lp.setFlag(self._lp.GraphicsItemFlag.ItemIsMovable, False)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        # Snap to fine grid on release
        scene = self.scene()
        views = scene.views() if scene else []
        for v in views:
            if hasattr(v, 'snap_laser_to_fine_grid'):
                snapped = v.snap_laser_to_fine_grid(self.pos())
                self.setPos(snapped)
                self._update_laser_endpoint(snapped)
                break
        # Re-enable parent's movability
        self._lp.setFlag(self._lp.GraphicsItemFlag.ItemIsMovable, True)
        # Save undo
        for v in views:
            main_app = getattr(v, 'main_app', None)
            if main_app and not main_app._is_loading:
                main_app.save_undo_state()
                break


# ─────────────────────────────────────────────────────────────────────────────
# LaserPath
# ─────────────────────────────────────────────────────────────────────────────
class LaserPath(QGraphicsLineItem):
    def __init__(self, line, color=QColor(255, 0, 0, 180), has_arrow=True):
        super().__init__(line)
        self.color = color
        self.has_arrow = has_arrow
        self.setPen(QPen(self.color, 7, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        self.setZValue(10)
        self.setFlags(self.GraphicsItemFlag.ItemIsSelectable
                      | self.GraphicsItemFlag.ItemIsMovable
                      | self.GraphicsItemFlag.ItemSendsGeometryChanges)
        self._handles: list[_EndpointHandle] = []

    # ── Handle management ─────────────────────────────────────────────────
    def _show_handles(self):
        if self._handles:
            return
        scene = self.scene()
        if scene is None:
            return
        for idx in (0, 1):
            h = _EndpointHandle(self, idx)
            scene.addItem(h)
            self._handles.append(h)
        self._position_handles()

    def _hide_handles(self):
        for h in self._handles:
            scene = h.scene()
            if scene:
                scene.removeItem(h)
        self._handles.clear()

    def _position_handles(self):
        if len(self._handles) < 2:
            return
        line = self.line()
        self._handles[0].setPos(self.mapToScene(line.p1()))
        self._handles[1].setPos(self.mapToScene(line.p2()))

    def itemChange(self, change, value):
        if change == self.GraphicsItemChange.ItemSelectedChange:
            if value:
                self._show_handles()
            else:
                self._hide_handles()
        if change == self.GraphicsItemChange.ItemPositionHasChanged:
            self._position_handles()
        if change == self.GraphicsItemChange.ItemSceneChange:
            # About to be removed from scene — clean up handles
            if value is None:
                self._hide_handles()
        return super().itemChange(change, value)

    # ── Mouse events (existing) ───────────────────────────────────────────
    def mousePressEvent(self, event):
        self._drag_z = self.zValue()
        self.setZValue(99999)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        scene = self.scene()
        views = scene.views() if scene else []
        view = next((v for v in views if hasattr(v, 'snap_laser_to_fine_grid')), None)
        # Snap the first endpoint to the fine grid and shift the whole item
        # by the resulting offset — preserves line length and angle exactly.
        if view:
            p1_scene = self.mapToScene(self.line().p1())
            p1_snapped = view.snap_laser_to_fine_grid(p1_scene)
            offset = p1_snapped - p1_scene
            self.moveBy(offset.x(), offset.y())

        self._position_handles()

        main_app = getattr(view, 'main_app', None) if view else None
        if main_app and hasattr(main_app, '_sync_z_order'):
            main_app._sync_z_order()
        else:
            self.setZValue(getattr(self, '_drag_z', 10))

        if main_app and not main_app._is_loading:
            main_app.save_undo_state()

    # ── Paint ─────────────────────────────────────────────────────────────
    def paint(self, painter, option, widget=None):
        if self.isSelected():
            painter.setPen(QPen(Qt.GlobalColor.blue, 2, Qt.PenStyle.DashLine))
            painter.drawRect(self.boundingRect())

        pen = self.pen()
        pen.setColor(self.color)
        painter.setPen(pen)
        painter.drawLine(self.line())

        if self.has_arrow:
            line = self.line()
            if line.length() < 30: return
            mid = line.center()
            angle = math.atan2(-line.dy(), line.dx())
            arrow_size = 30
            half = arrow_size / 2
            tip  = mid + QPointF( math.cos(angle) * half, -math.sin(angle) * half)
            base = mid - QPointF( math.cos(angle) * half, -math.sin(angle) * half)
            p1 = base + QPointF(-math.sin(angle) * half, -math.cos(angle) * half)
            p2 = base - QPointF(-math.sin(angle) * half, -math.cos(angle) * half)

            painter.setBrush(QBrush(self.color))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawPolygon(QPolygonF([tip, p1, p2]))


class DraggableElement(QGraphicsSvgItem):
    def __init__(self, svg_path, name, parent_app):
        super().__init__(svg_path)
        self.file_path, self.name, self.parent_app = svg_path, name, parent_app
        self.holes = []
        self.snapping_enabled = True
        self.setFlags(self.GraphicsItemFlag.ItemIsMovable | self.GraphicsItemFlag.ItemIsSelectable)
        self.setZValue(5)

        self.setTransformOriginPoint(self.boundingRect().center())

        #if self.holes: 
        #    self.setTransformOriginPoint(self.holes[0]) 
        #else: # fallback: rotate around center 
        #    self.setTransformOriginPoint(self.boundingRect().center())



    def detect_holes_from_svg(self):
        txt_path = os.path.splitext(self.file_path)[0] + ".txt"
        self.holes = []
        
        # 1. Try loading from .txt first
        if os.path.exists(txt_path):
            try:
                with open(txt_path, 'r') as f:
                    for line in f:
                        if ',' in line:
                            x, y = map(float, line.strip().split(','))
                            self.holes.append(QPointF(x, y))
                return
            except: pass

        # 2. Otherwise extract from SVG and auto-generate the .txt
        try:
            tree = ET.parse(self.file_path)
            for circle in tree.getroot().findall('.//{http://www.w3.org/2000/svg}circle'):
                cx, cy = float(circle.get('cx', 0)), float(circle.get('cy', 0))
                self.holes.append(QPointF(cx, cy))
            
            with open(txt_path, 'w') as f:
                for h in self.holes:
                    f.write(f"{h.x()},{h.y()}\n")
        except: pass

    def get_global_holes(self):
        return [self.mapToScene(h) for h in self.holes]

    def snap_to_grid(self):
        if not self.snapping_enabled or not self.parent_app.breadboard_holes or not self.holes:
            return
            
        # Get the primary hole in scene coordinates
        primary_local = self.holes[0]
        primary_scene = self.mapToScene(primary_local)
        
        # Find the closest breadboard hole
        best_dist = float('inf')
        target_hole = None
        
        for b_hole in self.parent_app.breadboard_holes:
            dist = QLineF(primary_scene, b_hole).length()
            if dist < best_dist:
                best_dist = dist
                target_hole = b_hole
                
        # Use a tighter tolerance (e.g., 25px) to prevent "jumping" 
        # when items are far away from a valid hole
        if target_hole and best_dist < 25:
            offset = target_hole - primary_scene
            self.moveBy(offset.x(), offset.y())

    def _compute_alignment_error(self):
        """Current total alignment error — lower is better"""
        if not self.holes:
            return 9999.0
        total = 0.0
        count = 0
        for local in self.holes:
            pos = self.mapToScene(local)
            nearest = min(self.parent_app.breadboard_holes,
                         key=lambda p: (p - pos).manhattanLength(),
                         default=None)
            if nearest is not None:
                total += (nearest - pos).manhattanLength()
                count += 1
        return total / max(count, 1) if count > 0 else 9999.0

    def _compute_error_after_delta(self, delta):
        """Estimate error & match count if we apply this delta"""
        matches = 0
        total_error = 0.0
        for local in self.holes:
            pos = self.mapToScene(local) + delta
            nearest = min(self.parent_app.breadboard_holes,
                         key=lambda p: (p - pos).manhattanLength(),
                         default=None)
            if nearest is None:
                continue
            d = (nearest - pos).manhattanLength()
            total_error += d
            if d < 7.5:          # ← tight match threshold
                matches += 1
        avg_error = total_error / max(len(self.holes), 1)
        return avg_error, matches

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.RightButton:
            view = event.widget()
            main_app = view.main_app if hasattr(view, 'main_app') else None
            pop = PropertyPopup(self, main_app, event.widget())
            pop.move(event.screenPos())
            pop.show()
            event.accept()
        else:
            # Raise above everything while being dragged
            self._drag_z = self.zValue()
            self.setZValue(99999)
            super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if self.snapping_enabled: self.snap_to_grid()
        # Restore z from the canvas tree so layer order is respected
        if hasattr(self.parent_app, '_sync_z_order'):
            self.parent_app._sync_z_order()
        else:
            self.setZValue(getattr(self, '_drag_z', 5))
        super().mouseReleaseEvent(event)
        # Save exactly once, after snap — this is the only undo point per move
        if not self.parent_app._is_loading:
            self.parent_app.save_undo_state()

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.setRotation(self.rotation() + 90)
            if not self.parent_app._is_loading: self.parent_app.save_undo_state()
        super().mouseDoubleClickEvent(event)

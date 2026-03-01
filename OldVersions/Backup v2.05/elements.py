import os
import math
import xml.etree.ElementTree as ET
from PyQt6.QtWidgets import QGraphicsLineItem 
from PyQt6.QtCore import Qt, QPointF, QLineF
from PyQt6.QtGui import QColor, QPen, QPolygonF, QBrush
from PyQt6.QtSvgWidgets import QGraphicsSvgItem

from dialogs import PropertyPopup

class LaserPath(QGraphicsLineItem):
    def __init__(self, line, color=QColor(255, 0, 0, 180), has_arrow=True):
        super().__init__(line)
        self.color = color
        self.has_arrow = has_arrow
        self.setPen(QPen(self.color, 7, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        self.setZValue(10)
        self.setFlags(self.GraphicsItemFlag.ItemIsSelectable | self.GraphicsItemFlag.ItemIsMovable)

    def paint(self, painter, option, widget=None):
        if self.isSelected():
            painter.setPen(QPen(Qt.GlobalColor.blue, 2, Qt.PenStyle.DashLine))
            painter.drawRect(self.boundingRect())
        
        # Use the item's own color for the line
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
            p1 = mid - QPointF(math.cos(angle + 0.5)*arrow_size, -math.sin(angle + 0.5)*arrow_size)
            p2 = mid - QPointF(math.cos(angle - 0.5)*arrow_size, -math.sin(angle - 0.5)*arrow_size)
            
            # Use the item's own color for the arrow
            painter.setBrush(QBrush(self.color))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawPolygon(QPolygonF([mid, p1, p2]))


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
            
            # screenPos() is used to avoid the AttributeError from globalPosition()
            pop.move(event.screenPos()) 
            pop.show()
            event.accept()
        else:
            view = event.widget()
            if hasattr(view, 'main_app') and not view.main_app._is_loading:
                view.main_app.save_undo_state()

            self.setZValue(11)
            super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if self.snapping_enabled: self.snap_to_grid()
        self.setZValue(5)
        super().mouseReleaseEvent(event)
        if not self.parent_app._is_loading: self.parent_app.save_undo_state()

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.setRotation(self.rotation() + 90)
            if not self.parent_app._is_loading: self.parent_app.save_undo_state()
        super().mouseDoubleClickEvent(event)

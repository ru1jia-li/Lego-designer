import os
import math
import xml.etree.ElementTree as ET
from PyQt6.QtWidgets import (QGraphicsEllipseItem, QGraphicsItem, QDialog, 
                             QVBoxLayout, QLabel, QHBoxLayout, QLineEdit, 
                             QCheckBox, QPushButton, QGraphicsLineItem) 
from PyQt6.QtCore import Qt, QPointF, QLineF
from PyQt6.QtGui import QColor, QPen, QPolygonF, QBrush
from PyQt6.QtSvgWidgets import QGraphicsSvgItem

from dialogs import PropertyPopup

SNAP_TOLERANCE = 12.0   # pixels in scene space
MIN_HOLES_TO_LOCK = 1   # or 2 if you want stricter locking


class LaserPath(QGraphicsLineItem):
    def __init__(self, line, color=QColor(255, 0, 0, 180), has_arrow=True):
        super().__init__(line)
        self.color = color
        self.has_arrow = has_arrow
        self.setPen(QPen(self.color, 7, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        self.setZValue(10)
        self.setFlags(self.GraphicsItemFlag.ItemIsSelectable | self.GraphicsItemFlag.ItemIsMovable)

    def paint(self, painter, option, widget):
        if self.isSelected():
            painter.setPen(QPen(Qt.GlobalColor.blue, 2, Qt.PenStyle.DashLine))
            painter.drawRect(self.boundingRect())
        super().paint(painter, option, widget)
        if self.has_arrow:
            line = self.line()
            if line.length() < 30: return
            mid = line.center()
            angle = math.atan2(-line.dy(), line.dx())
            arrow_size = 30 
            p1 = mid - QPointF(math.cos(angle + 0.5)*arrow_size, -math.sin(angle + 0.5)*arrow_size)
            p2 = mid - QPointF(math.cos(angle - 0.5)*arrow_size, -math.sin(angle - 0.5)*arrow_size)
            painter.setBrush(self.color)
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

        if self.holes: 
            self.setTransformOriginPoint(self.holes[0]) 
        else: # fallback: rotate around center 
            self.setTransformOriginPoint(self.boundingRect().center())



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
    # 1. Safety checks
    if not self.parent_app.breadboard_holes or not self.holes:
        return

    # 2. Get the "Primary Hole" (the first one) in Scene Coordinates
    # mapToScene converts the local SVG hole coord to where it is on the screen
    primary_hole_local = self.holes[0]
    primary_hole_scene = self.mapToScene(primary_hole_local)

    # 3. Find the ABSOLUTE closest hole on the breadboard
    best_dist = float('inf')
    target_breadboard_hole = None

    for b_hole in self.parent_app.breadboard_holes:
        # Calculate distance between part's first hole and this breadboard hole
        line = QLineF(primary_hole_scene, b_hole)
        dist = line.length()
        
        if dist < best_dist:
            best_dist = dist
            target_breadboard_hole = b_hole

    # 4. If we are close enough (e.g., 20 pixels), snap it!
    if target_breadboard_hole and best_dist < 20:
        # Calculate the offset needed to move the part
        delta = target_breadboard_hole - primary_hole_scene
        # Move the entire item by that offset
        self.moveBy(delta.x(), delta.y())

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

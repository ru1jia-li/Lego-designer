import os
from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QLabel, QHBoxLayout, 
                             QLineEdit, QCheckBox, QPushButton, QGraphicsScene, 
                             QGraphicsView, QGraphicsEllipseItem, QGraphicsItem,
                             QMessageBox, QWidget, QScrollArea) # Added QScrollArea
from PyQt6.QtCore import Qt, QPointF, QLineF, QSize # Added QSize
from PyQt6.QtGui import QColor, QPen, QBrush, QPainter, QIcon # Added QPainter, QIcon
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtSvgWidgets import QGraphicsSvgItem

class PropertyPopup(QDialog):
    def __init__(self, item, main_app, parent=None):
        super().__init__(parent)
        self.item = item
        self.main_app = main_app 
        self.setWindowFlags(Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setFixedSize(180, 200) # Slightly taller for calibration button
        self.setStyleSheet("QDialog { background: white; border: 2px solid #555; border-radius: 5px; }")
        
        layout = QVBoxLayout(self)
        title = QLabel(f"<b>{self.item.name}</b>")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)
        
        angle_layout = QHBoxLayout()
        angle_layout.addWidget(QLabel("Angle:"))
        self.angle_input = QLineEdit(str(int(self.item.rotation())))
        angle_layout.addWidget(self.angle_input)
        layout.addLayout(angle_layout)
        
        self.front_check = QCheckBox("Bring to Front")
        self.front_check.setChecked(self.item.zValue() >= 10)
        layout.addWidget(self.front_check)
        
        self.snap_check = QCheckBox("Snap to Grid")
        self.snap_check.setChecked(self.item.snapping_enabled)
        layout.addWidget(self.snap_check)
        
        btn = QPushButton("Apply")
        btn.clicked.connect(self.apply_properties)
        layout.addWidget(btn)

    def open_calibration(self):
        dlg = HoleCalibrationDialog(self.item.file_path, self.item.holes, self)
        if dlg.exec():
            self.item.holes = dlg.holes
            self.item.snap_to_grid()
        self.close()

    def apply_properties(self):
        try:
            self.item.setRotation(float(self.angle_input.text()))
            self.item.setZValue(15 if self.front_check.isChecked() else 5)
            self.item.snapping_enabled = self.snap_check.isChecked()
            if self.item.snapping_enabled: self.item.snap_to_grid()
            if self.main_app: self.main_app.save_undo_state()
        except ValueError: pass
        self.close()


class CollapsibleCategory(QWidget):
    def __init__(self, name, icon_data, parent_app):
        super().__init__()
        self.parent_app = parent_app
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)
        
        self.btn = QPushButton(f"  ▶  {name}")
        self.btn.setCheckable(True)
        self.btn.setStyleSheet("""
            text-align: left; padding: 12px; background: #D0D0D0; 
            font-weight: bold; border-bottom: 1px solid #AAA; 
            border-radius: 0px; font-size: 11pt;
        """)
        self.btn.clicked.connect(self.toggle)
        
        self.area = QWidget()
        self.area.setVisible(False)
        self.content = QVBoxLayout(self.area)
        self.layout.addWidget(self.btn)
        self.layout.addWidget(self.area)

        for p, f in icon_data:
            row = QHBoxLayout()
            # Standard button, no special mousePressEvent
            b = QPushButton() 
            b.setIcon(QIcon(p))
            b.setFixedSize(50, 50)
            b.setIconSize(QSize(35, 35))
            b.setToolTip(f)
            b.setStyleSheet("background: white; border: 1px solid #AAA; border-radius: 4px;")
            
            # Left-click still adds to scene
            b.clicked.connect(lambda ch, path=p, name=f: self.parent_app.add_to_scene(path, name))
            
            row.addWidget(b)
            row.addWidget(QLabel(f))
            row.addStretch()
            self.content.addLayout(row)
    
    def toggle(self):
        is_visible = not self.area.isVisible()
        self.area.setVisible(is_visible)
        self.btn.setText(f"  {'▼' if is_visible else '▶'}  {self.btn.text()[5:]}")


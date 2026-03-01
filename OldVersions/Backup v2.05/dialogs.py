import os
from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QLabel, QHBoxLayout, 
                             QLineEdit, QCheckBox, QPushButton, QWidget, 
                             QSlider, QGridLayout) 
from PyQt6.QtCore import Qt, QSize 
from PyQt6.QtGui import QColor, QIcon 


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
            border-radius: 0px; font-size: 14pt;
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
            
            lbl = QLabel(f)
            lbl.setStyleSheet("font-size: 14pt;")
            row.addWidget(b)
            row.addWidget(lbl)
            row.addStretch()
            self.content.addLayout(row)
    
    def toggle(self):
        is_visible = not self.area.isVisible()
        self.area.setVisible(is_visible)
        self.btn.setText(f"  {'▼' if is_visible else '▶'}  {self.btn.text()[5:]}")

    def apply_filter(self, search_text):
        matches_in_this_cat = 0
        
        # Iterate through the rows in the category content
        for i in range(self.content.count()):
            layout_item = self.content.itemAt(i)
            if layout_item.layout():
                # Find the Label in the row (which has the part name)
                row_layout = layout_item.layout()
                label = row_layout.itemAt(1).widget() # The QLabel(f)
                row_container = row_layout.parentWidget() # Or handle visibility via a wrapper
                
                # Check if name matches
                if search_text in label.text().lower():
                    # Show this specific row
                    self.set_row_visible(row_layout, True)
                    matches_in_this_cat += 1
                else:
                    # Hide this specific row
                    self.set_row_visible(row_layout, False)
        
        # Auto-expand the category if we are searching and there are matches
        if search_text != "":
            self.area.setVisible(matches_in_this_cat > 0)
            name = self.btn.text()[5:]
            self.btn.setText(f"  {'▼' if matches_in_this_cat > 0 else '▶'}  {name}")
        else:
            # Collapse and reset arrow when search is cleared
            self.area.setVisible(False)
            name = self.btn.text()[5:]
            self.btn.setText(f"  ▶  {name}")
            
        return matches_in_this_cat > 0

    def set_row_visible(self, layout, visible):
        for i in range(layout.count()):
            w = layout.itemAt(i).widget()
            if w:
                w.setVisible(visible)

class LaserColorDialog(QDialog):
    def __init__(self, current_color, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Laser Path Settings")
        self.setWindowFlags(Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setFixedSize(220, 260)
        self.setStyleSheet("""
            QDialog { 
                background: white; 
                border: 2px solid #666; 
                border-radius: 12px; 
            }
            QLabel { color: #444; font-weight: bold; }
            QSlider::handle:horizontal {
                background: #666;
                border: 1px solid #444;
                width: 14px;
                height: 14px;
                margin: -5px 0;
                border-radius: 7px;
            }
            QSlider::groove:horizontal {
                border: 1px solid #bbb;
                height: 4px;
                background: #eee;
                margin: 2px 0;
                border-radius: 2px;
            }
            QPushButton {
                background: #f0f0f0;
                border: 1px solid #ccc;
                border-radius: 6px;
                padding: 6px;
                font-weight: bold;
            }
            QPushButton:hover { background: #e0e0e0; }
        """)

        self.color = QColor(current_color)
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(15, 15, 15, 15)

        title = QLabel("Laser Path Settings")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("font-size: 11pt; color: #222;")
        layout.addWidget(title)

        # Color Grid
        color_layout = QGridLayout()
        color_layout.setSpacing(8)
        colors = [
            "#FF0000", "#00FF00", "#0000FF", 
            "#FFFF00", "#FF00FF", "#00FFFF",
            "#FFA500", "#800080", "#000000"
        ]
        for i, c in enumerate(colors):
            btn = QPushButton()
            btn.setFixedSize(24, 24)
            btn.setStyleSheet(f"background: {c}; border: 1px solid #999; border-radius: 12px;")
            btn.clicked.connect(lambda ch, col=c: self.set_color(col))
            color_layout.addWidget(btn, i // 3, i % 3)
        
        # Custom color button
        self.custom_btn = QPushButton("Custom...")
        self.custom_btn.setStyleSheet("font-size: 9pt; padding: 4px;")
        self.custom_btn.clicked.connect(self.pick_custom_color)
        color_layout.addWidget(self.custom_btn, 3, 0, 1, 3)
        
        layout.addLayout(color_layout)

        # Opacity Slider
        opacity_label = QLabel(f"Opacity: {int(self.color.alphaF() * 100)}%")
        layout.addWidget(opacity_label)
        
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(20, 255) # Min 20 to keep it visible
        self.slider.setValue(self.color.alpha())
        self.slider.valueChanged.connect(lambda v: self.update_opacity(v, opacity_label))
        layout.addWidget(self.slider)

        # Close button
        close_btn = QPushButton("Done")
        close_btn.setStyleSheet("background: #666; color: white; border: none;")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn)

    def set_color(self, hex_code):
        alpha = self.color.alpha()
        self.color = QColor(hex_code)
        self.color.setAlpha(alpha)

    def update_opacity(self, value, label):
        self.color.setAlpha(value)
        label.setText(f"Opacity: {int(self.color.alphaF() * 100)}%")

    def pick_custom_color(self):
        from PyQt6.QtWidgets import QColorDialog
        col = QColorDialog.getColor(self.color, self, "Select Laser Color")
        if col.isValid():
            alpha = self.color.alpha()
            self.color = col
            self.color.setAlpha(alpha)
            self.custom_btn.setStyleSheet(f"background: {col.name()}; color: {'white' if col.lightness() < 128 else 'black'}; font-size: 9pt; padding: 4px;")


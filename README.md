# Lego Designer

A desktop app for designing optical breadboard layouts in a Lego-style, drag-and-drop canvas. Place optical components (mirrors, lenses, lasers, etc.) on a chosen breadboard, draw laser paths, add text labels, and export to SVG for use in Illustrator or other tools.

## Requirements

- **Python 3**
- **PyQt6**

```bash
pip install PyQt6
```

## Run

From this folder:

```bash
python Lego_designer.py
```

## Project structure

| File / folder      | Role |
|--------------------|------|
| `Lego_designer.py` | Main window, UI, scene, undo/redo, SVG export/import, toolbar, layers |
| `elements.py`      | Canvas items: `DraggableElement` (SVG), `LaserPath`, `CanvasTextItem` |
| `view.py`          | Graphics view: pan, zoom, draw laser, eraser, rubber-band selection, snapping |
| `dialogs.py`       | Dialogs and collapsible categories (e.g. property popup) |
| `holes.py`         | Precision hole editor and hole database UI |
| `canvas.py`        | Layer tree: `CanvasState`, `CanvasNode` (layers / groups / items) |
| `icons/`           | SVG components and `hole_database.json` for hole patterns |
| `Breadboards/`     | Breadboard background SVGs and per-board fine-grid caches |

## Main features

- **Breadboard**: Choose a background from `Breadboards/`; fine grid is cached per board.
- **Elements**: Drag components from the Inventory onto the canvas; they snap to the grid and can be rotated (toolbar or double-click).
- **Laser paths**: Draw lines with optional arrow; set color and opacity in the pen options; snap to fine grid.
- **Text**: Add text boxes with font size, bold/italic/underline in the text options panel.
- **Layers**: Text, Laser Paths, Elements; reorder in the Layers panel; drag to reorder.
- **Undo / redo**: Full state snapshots; one step per rotation (toolbar or double-click).
- **Export / import**: Save and open SVG; open from the timestamped autosave folder (capped at 10 files).
- **Minimap**: Toggle position (bottom-left / bottom-right) via the arrow on its rim.
- **Holes**: Review and edit precision hole positions per component via the hole editor and Backspace in the hole dialog.

## Autosave

Autosave runs every 2 minutes. Files are stored under `autosave/` (up to 10; oldest removed when over the cap). Use the **Open** dropdown → “Open from autosave” to restore.

## License / repo

Check the repo for license and contribution details. `OldVersions/`, `autosave/`, and `.cursor/` are ignored by git.

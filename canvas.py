"""
canvas.py — Hierarchical CanvasState tree for LegoDesigner.

Structure:
    CanvasState (root manager)
    └── CanvasNode (Layer / Group / Item)
        ├── CanvasNode ...
        └── CanvasNode ...

This is a lightweight, pure-Python tree that sits alongside the Qt scene.
The Qt scene remains the single source of truth for rendering; this tree
provides named layers, Illustrator-compatible grouping, and structured
serialization/deserialization.
"""

import json


# ---------------------------------------------------------------------------
# Node types
# ---------------------------------------------------------------------------
LAYER = "Layer"
GROUP = "Group"
ITEM  = "Item"


class CanvasNode:
    """A node in the canvas tree.

    Parameters
    ----------
    name : str
        Human-readable label (e.g. "SVG Layer", "Holes", "Mirror #1").
    node_type : str
        One of LAYER, GROUP, or ITEM.
    item : QGraphicsItem | None
        The Qt graphics item this node wraps (None for Layer/Group nodes).
    data : dict | None
        Optional serialisable metadata (e.g. hole coordinates, svg path).
    """

    def __init__(self, name, node_type=ITEM, item=None, data=None):
        self.name = name
        self.node_type = node_type
        self.item = item
        self.data = data or {}
        self.children: list["CanvasNode"] = []
        self.parent: "CanvasNode | None" = None

    # ------------------------------------------------------------------
    # Tree helpers
    # ------------------------------------------------------------------
    def add_child(self, node: "CanvasNode") -> "CanvasNode":
        node.parent = self
        self.children.append(node)
        return node

    def insert_child(self, index: int, node: "CanvasNode") -> "CanvasNode":
        node.parent = self
        self.children.insert(index, node)
        return node

    def remove_child(self, node: "CanvasNode") -> bool:
        if node in self.children:
            self.children.remove(node)
            node.parent = None
            return True
        return False

    def move_child_up(self, node: "CanvasNode") -> bool:
        """Move *node* one position earlier (higher in visual stack = more 'above')."""
        if node not in self.children:
            return False
        idx = self.children.index(node)
        if idx == 0:
            return False
        self.children[idx], self.children[idx - 1] = self.children[idx - 1], self.children[idx]
        return True

    def move_child_down(self, node: "CanvasNode") -> bool:
        """Move *node* one position later (lower in visual stack = more 'below')."""
        if node not in self.children:
            return False
        idx = self.children.index(node)
        if idx == len(self.children) - 1:
            return False
        self.children[idx], self.children[idx + 1] = self.children[idx + 1], self.children[idx]
        return True

    def index_of(self, node: "CanvasNode") -> int:
        return self.children.index(node) if node in self.children else -1

    def all_items(self):
        """Yield all QGraphicsItems in this subtree (depth-first)."""
        if self.item is not None:
            yield self.item
        for child in self.children:
            yield from child.all_items()

    def all_nodes_flat(self):
        """Yield self then all descendants depth-first."""
        yield self
        for child in self.children:
            yield from child.all_nodes_flat()

    def all_item_nodes_flat(self):
        """Yield all ITEM-type descendant nodes (depth-first)."""
        for node in self.all_nodes_flat():
            if node.node_type == ITEM:
                yield node

    def find_by_name(self, name: str) -> "CanvasNode | None":
        """Return the first node with the given name, or None."""
        if self.name == name:
            return self
        for child in self.children:
            result = child.find_by_name(name)
            if result is not None:
                return result
        return None

    def find_by_item(self, item) -> "CanvasNode | None":
        """Return the node wrapping a specific QGraphicsItem, or None."""
        if self.item is item:
            return self
        for child in self.children:
            result = child.find_by_item(item)
            if result is not None:
                return result
        return None

    # ------------------------------------------------------------------
    # Serialization (metadata only — Qt items are NOT serialised here)
    # ------------------------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "type": self.node_type,
            "data": self.data,
            "children": [c.to_dict() for c in self.children],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "CanvasNode":
        node = cls(name=d["name"], node_type=d["type"], data=d.get("data", {}))
        for child_d in d.get("children", []):
            node.add_child(cls.from_dict(child_d))
        return node

    def __repr__(self):
        return f"CanvasNode(name={self.name!r}, type={self.node_type}, children={len(self.children)})"


# ---------------------------------------------------------------------------
# CanvasState — the root manager
# ---------------------------------------------------------------------------
class CanvasState:
    """Manages the canvas tree and provides convenience helpers.

    Usage (HoleManagerDialog)
    -------------------------
        state = CanvasState()
        svg_layer = state.add_layer("SVG Layer")
        holes_layer = state.add_layer("Holes")
        holes_layer.add_child(CanvasNode("hole_0", ITEM, item=dot, data={...}))

    Usage (LegoDesigner main scene)
    --------------------------------
        state = CanvasState()
        layer = state.add_layer("Layer 1")
        layer.add_child(CanvasNode(name, ITEM, item=draggable))
    """

    def __init__(self):
        self.root = CanvasNode("root", node_type=LAYER)

    # ------------------------------------------------------------------
    # Layer helpers
    # ------------------------------------------------------------------
    def add_layer(self, name: str) -> CanvasNode:
        """Add a top-level layer and return it."""
        layer = CanvasNode(name, node_type=LAYER)
        self.root.add_child(layer)
        return layer

    def add_layer_at(self, name: str, index: int) -> CanvasNode:
        """Insert a top-level layer at *index* and return it."""
        layer = CanvasNode(name, node_type=LAYER)
        self.root.insert_child(index, layer)
        return layer

    def get_layer(self, name: str) -> "CanvasNode | None":
        """Return an existing top-level layer by name."""
        for child in self.root.children:
            if child.name == name and child.node_type == LAYER:
                return child
        return None

    def get_or_create_layer(self, name: str) -> CanvasNode:
        """Return an existing layer or create it if it doesn't exist."""
        layer = self.get_layer(name)
        if layer is None:
            layer = self.add_layer(name)
        return layer

    def layers(self) -> list[CanvasNode]:
        """Return all top-level layer nodes in order."""
        return [c for c in self.root.children if c.node_type == LAYER]

    def reorder_layers(self, new_order: list[str]) -> None:
        """Re-order top-level layers to match *new_order* (list of names).
        Layers not in new_order are appended at the end."""
        named = {c.name: c for c in self.root.children if c.node_type == LAYER}
        reordered = [named[n] for n in new_order if n in named]
        rest = [c for c in self.root.children if c not in reordered]
        self.root.children = reordered + rest

    # ------------------------------------------------------------------
    # Group helpers
    # ------------------------------------------------------------------
    def make_group(self, nodes: list[CanvasNode], name: str = "Group") -> CanvasNode:
        """Wrap *nodes* into a new GROUP node inserted at the position of the first node.

        All nodes must share the same parent. If they come from different parents,
        the group is inserted into the first node's parent.
        """
        if not nodes:
            return CanvasNode(name, GROUP)

        parent = nodes[0].parent
        if parent is None:
            parent = self.root

        # Find the earliest index among all nodes to anchor the group
        indices = []
        for n in nodes:
            if n in parent.children:
                indices.append(parent.children.index(n))
        insert_at = min(indices) if indices else len(parent.children)

        # Remove nodes from parent
        for n in nodes:
            parent.remove_child(n)

        # Create the group and insert it
        group = CanvasNode(name, GROUP)
        parent.insert_child(insert_at, group)
        for n in nodes:
            group.add_child(n)

        return group

    def ungroup(self, group_node: CanvasNode) -> list[CanvasNode]:
        """Move children of *group_node* back to its parent, then remove the group.
        Returns the list of un-grouped children."""
        if group_node.node_type != GROUP:
            return []
        parent = group_node.parent
        if parent is None:
            parent = self.root

        idx = parent.index_of(group_node)
        parent.remove_child(group_node)

        children = list(group_node.children)
        for i, child in enumerate(children):
            group_node.remove_child(child)
            parent.insert_child(idx + i, child)

        return children

    # ------------------------------------------------------------------
    # Lookup helpers
    # ------------------------------------------------------------------
    def find_node_for_item(self, item) -> "CanvasNode | None":
        """Find the CanvasNode wrapping a Qt graphics item."""
        return self.root.find_by_item(item)

    def remove_item(self, item) -> bool:
        """Remove the node wrapping *item* from the tree."""
        node = self.find_node_for_item(item)
        if node and node.parent:
            return node.parent.remove_child(node)
        return False

    def all_items_in_layer(self, layer_name: str):
        """Yield all QGraphicsItems belonging to a named layer."""
        layer = self.get_layer(layer_name)
        if layer:
            yield from layer.all_items()

    def all_item_nodes(self):
        """Yield every ITEM CanvasNode in the tree (depth-first)."""
        for node in self.root.all_nodes_flat():
            if node.node_type == ITEM and node.item is not None:
                yield node

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------
    def to_json(self) -> str:
        return json.dumps(self.root.to_dict(), indent=2)

    @classmethod
    def from_json(cls, js: str) -> "CanvasState":
        state = cls()
        root_d = json.loads(js)
        # Restore children but keep the in-memory root node
        for child_d in root_d.get("children", []):
            state.root.add_child(CanvasNode.from_dict(child_d))
        return state

    def __repr__(self):
        def _fmt(node, indent=0):
            lines = [" " * indent + repr(node)]
            for child in node.children:
                lines.extend(_fmt(child, indent + 2))
            return lines
        return "\n".join(_fmt(self.root))

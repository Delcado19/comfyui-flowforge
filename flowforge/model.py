"""
Datenmodell für ComfyUI-Workflow-JSONs.

Nur Datenstrukturen — keine Logik, keine I/O.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

# Node-Typen die keine Datenfluss-Kanten haben (nur Kommentare/Dekoration).
# Werden vom Layout-Algorithmus separat behandelt.
DECORATIVE_TYPES: frozenset[str] = frozenset({
    "Note",
    "MarkdownNote",
    "Label (rgthree)",
})

# SetNode/GetNode (comfyui-kjnodes): virtuelle Verbindungen via widgets_values[0].
SET_NODE_TYPE = "SetNode"
GET_NODE_TYPE = "GetNode"

# Reroute: normaler Graph-Knoten (1 Input, 1 Output), kein Sonderfall im Algorithmus.
REROUTE_TYPE = "Reroute"

# Sub-Graphen haben eine UUID als type-String.
_UUID_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)


@dataclass
class NodeInput:
    name: str
    type: str
    link: Optional[int]  # Link-ID oder None wenn nicht verbunden


@dataclass
class NodeOutput:
    name: str
    type: str
    links: list[int] = field(default_factory=list)  # kann leer oder mehrfach sein


@dataclass
class Node:
    id: int
    type: str
    pos: list[float]          # [x, y] — wird vom Layout überschrieben
    size: list[float]         # [width, height] — normalisiert aus beiden JSON-Varianten
    mode: int                 # 0 = aktiv, 4 = bypassed
    order: int                # Ausführungsreihenfolge (wird nicht verändert)
    inputs: list[NodeInput] = field(default_factory=list)
    outputs: list[NodeOutput] = field(default_factory=list)
    title: Optional[str] = None  # gesetzter Titel überschreibt den type-Namen

    # --- Geometrie-Shortcuts ---

    @property
    def x(self) -> float:
        return self.pos[0]

    @x.setter
    def x(self, value: float) -> None:
        self.pos[0] = value

    @property
    def y(self) -> float:
        return self.pos[1]

    @y.setter
    def y(self, value: float) -> None:
        self.pos[1] = value

    @property
    def width(self) -> float:
        return self.size[0]

    @property
    def height(self) -> float:
        return self.size[1]

    @property
    def label(self) -> str:
        """Angezeigter Name: gesetzter Titel, sonst type."""
        return self.title if self.title else self.type

    # --- Klassifizierung ---

    @property
    def is_decorative(self) -> bool:
        """Dekorativer Node ohne Datenfluss (Note, MarkdownNote, Label)."""
        return self.type in DECORATIVE_TYPES

    @property
    def is_bypassed(self) -> bool:
        return self.mode == 4

    @property
    def is_set_node(self) -> bool:
        return self.type == SET_NODE_TYPE

    @property
    def is_get_node(self) -> bool:
        return self.type == GET_NODE_TYPE

    @property
    def is_reroute(self) -> bool:
        return self.type == REROUTE_TYPE

    @property
    def is_subgraph(self) -> bool:
        """Sub-Graph-Nodes haben eine UUID als type."""
        return bool(_UUID_PATTERN.match(self.type))

    @property
    def is_layout_node(self) -> bool:
        """True für alle Nodes die im Sugiyama-Algorithmus positioniert werden."""
        return not self.is_decorative


@dataclass
class Link:
    id: int
    source_node: int   # Node-ID
    source_slot: int   # Output-Slot-Index
    target_node: int   # Node-ID
    target_slot: int   # Input-Slot-Index
    link_type: str     # z.B. "LATENT", "IMAGE", "MODEL"


@dataclass
class Group:
    id: int
    title: str
    bounding: list[float]   # [x, y, width, height] — wird nach dem Layout neu berechnet
    color: str = "#3f789e"
    font_size: int = 24

    @property
    def x(self) -> float:
        return self.bounding[0]

    @property
    def y(self) -> float:
        return self.bounding[1]

    @property
    def width(self) -> float:
        return self.bounding[2]

    @property
    def height(self) -> float:
        return self.bounding[3]

    def contains(self, node: Node) -> bool:
        """Prüft ob ein Node innerhalb der Bounding-Box liegt."""
        return (
            self.x <= node.x < self.x + self.width
            and self.y <= node.y < self.y + self.height
        )


@dataclass
class Workflow:
    nodes: list[Node]
    links: list[Link]
    groups: list[Group]
    raw: dict   # Original-JSON-Dict; wird für den Writer direkt modifiziert

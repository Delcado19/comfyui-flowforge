"""Data model for ComfyUI workflow JSON files.

Pure data structures — no logic, no I/O.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

# Node types that carry no dataflow edges (comments/decoration only).
# Handled separately by the layout algorithm.
DECORATIVE_TYPES: frozenset[str] = frozenset({
    "Note",
    "MarkdownNote",
    "Label (rgthree)",
})

# SetNode/GetNode (comfyui-kjnodes): virtual connections keyed by widgets_values[0].
SET_NODE_TYPE = "SetNode"
GET_NODE_TYPE = "GetNode"

# Reroute: ordinary graph node (1 input, 1 output), no special-casing needed.
REROUTE_TYPE = "Reroute"

# Sub-graph nodes use a UUID string as their type.
_UUID_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)


@dataclass
class NodeInput:
    name: str
    type: str
    link: Optional[int]  # link ID, or None when unconnected


@dataclass
class NodeOutput:
    name: str
    type: str
    links: list[int] = field(default_factory=list)  # empty or fan-out to multiple


@dataclass
class Node:
    id: int
    type: str
    pos: list[float]          # [x, y] — overwritten by the layout algorithm
    size: list[float]         # [width, height] — normalised from both JSON variants
    mode: int                 # 0 = active, 4 = bypassed
    order: int                # execution order (never modified)
    inputs: list[NodeInput] = field(default_factory=list)
    outputs: list[NodeOutput] = field(default_factory=list)
    title: Optional[str] = None  # user-set title; overrides the type name when present

    # --- geometry shortcuts ---

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
        """Display name: user title if set, otherwise the type string."""
        return self.title if self.title else self.type

    # --- classification ---

    @property
    def is_decorative(self) -> bool:
        """True for comment/decoration nodes that carry no dataflow (Note, MarkdownNote, Label)."""
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
        """True when the type string is a UUID (inline sub-graph node)."""
        return bool(_UUID_PATTERN.match(self.type))

    @property
    def is_layout_node(self) -> bool:
        """True for all nodes positioned by the Sugiyama algorithm."""
        return not self.is_decorative


@dataclass
class Link:
    id: int
    source_node: int   # node ID
    source_slot: int   # output slot index
    target_node: int   # node ID
    target_slot: int   # input slot index
    link_type: str     # e.g. "LATENT", "IMAGE", "MODEL"


@dataclass
class Group:
    id: int
    title: str
    bounding: list[float]   # [x, y, width, height] — recalculated after layout
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
        """Returns True if the node's origin point lies within this bounding box."""
        return (
            self.x <= node.x < self.x + self.width
            and self.y <= node.y < self.y + self.height
        )


@dataclass
class Workflow:
    nodes: list[Node]
    links: list[Link]
    groups: list[Group]
    raw: dict   # original JSON dict; mutated in place by the writer

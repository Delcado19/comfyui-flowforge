"""
Data models for ComfyUI FlowForge.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class Node:
    id: int
    type: str = ""
    x: float = 0.0
    y: float = 0.0
    size: List[float] = field(default_factory=lambda: [0.0, 0.0])
    mode: int = 0
    order: int = 0
    input_links: List[int] = field(default_factory=list)
    output_links: List[int] = field(default_factory=list)
    widgets_values: List[Any] = field(default_factory=list)  # For SetNode/GetNode


@dataclass
class Link:
    id: int
    source: int  # node id
    source_port: int  # output port index
    target: int  # node id
    target_port: int  # input port index
    type: Optional[str] = None  # link type


@dataclass
class Group:
    id: int
    name: str
    nodes: List[Node] = field(default_factory=list)
    # Bounding box: [x, y, width, height]
    bounding: List[float] = field(default_factory=lambda: [0.0, 0.0, 0.0, 0.0])


@dataclass
class Workflow:
    nodes: Dict[int, Node] = field(default_factory=dict)
    links: Dict[int, Link] = field(default_factory=dict)
    groups: List[Group] = field(default_factory=list)
    ungrouped_nodes: List[Node] = field(default_factory=list)
    source_json: Optional[Dict[str, Any]] = None
    layout_report: Optional[Any] = None

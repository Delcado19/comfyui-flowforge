"""Parser: reads a ComfyUI workflow JSON file and returns a Workflow object."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Union

from flowforge.model import (
    GET_NODE_TYPE,
    SET_NODE_TYPE,
    Group,
    Link,
    Node,
    NodeInput,
    NodeOutput,
    Workflow,
)


def load(path: Union[str, Path]) -> Workflow:
    """Parse a ComfyUI workflow JSON file into a Workflow object."""
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    return _parse(data)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _parse(data: dict) -> Workflow:
    raw_nodes = data.get("nodes", [])
    nodes = [_parse_node(n) for n in raw_nodes]
    links = [_parse_link(lnk) for lnk in data.get("links", [])]
    groups = [_parse_group(g) for g in data.get("groups", [])]
    synthetic = _build_virtual_links(raw_nodes, links)
    return Workflow(
        nodes=nodes,
        links=links + synthetic,
        groups=groups,
        raw=data,
    )


def _parse_node(raw: dict) -> Node:
    inputs = [
        NodeInput(
            name=inp.get("name", ""),
            type=inp.get("type", "*"),
            link=inp.get("link"),          # int or None
        )
        for inp in raw.get("inputs", [])
    ]
    outputs = [
        NodeOutput(
            name=out.get("name", ""),
            type=out.get("type", "*"),
            links=out.get("links") or [],  # null in JSON becomes []
        )
        for out in raw.get("outputs", [])
    ]
    return Node(
        id=raw["id"],
        type=raw["type"],
        pos=_parse_pos(raw.get("pos", [0.0, 0.0])),
        size=_parse_size(raw.get("size", [200.0, 100.0])),
        mode=raw.get("mode", 0),
        order=raw.get("order", 0),
        inputs=inputs,
        outputs=outputs,
        title=raw.get("title") or None,   # empty string → None
    )


def _parse_pos(raw) -> list[float]:
    # pos is always [x, y]; copy to avoid sharing the JSON list reference
    return [float(raw[0]), float(raw[1])]


def _parse_size(raw) -> list[float]:
    # ComfyUI uses either [w, h] or {"width": w, "height": h}
    if isinstance(raw, dict):
        return [float(raw["width"]), float(raw["height"])]
    return [float(raw[0]), float(raw[1])]


def _parse_link(raw: list) -> Link:
    # Format: [link_id, src_node, src_slot, dst_node, dst_slot, "TYPE"]
    return Link(
        id=int(raw[0]),
        source_node=int(raw[1]),
        source_slot=int(raw[2]),
        target_node=int(raw[3]),
        target_slot=int(raw[4]),
        link_type=str(raw[5]),
    )


def _parse_group(raw: dict) -> Group:
    return Group(
        id=raw.get("id", 0),
        title=raw.get("title", ""),
        bounding=list(raw.get("bounding", [0.0, 0.0, 100.0, 100.0])),
        color=raw.get("color", "#3f789e"),
        font_size=int(raw.get("font_size", 24)),
    )


def _build_virtual_links(raw_nodes: list[dict], existing_links: list[Link]) -> list[Link]:
    """Build synthetic links for SetNode→GetNode pairs (comfyui-kjnodes).

    SetNode and GetNode communicate via a shared name key stored in
    widgets_values[0] rather than through the links array. Synthetic links
    let the layout algorithm treat them as connected for topological sorting.
    IDs are assigned above the highest real link ID to avoid collisions.
    Synthetic links are never written back to the JSON.
    """
    set_nodes: dict[str, dict] = {}          # name → raw node dict
    get_nodes: dict[str, list[dict]] = {}    # name → list of raw node dicts

    for raw in raw_nodes:
        node_type = raw.get("type", "")
        if node_type not in (SET_NODE_TYPE, GET_NODE_TYPE):
            continue
        widgets = raw.get("widgets_values", [])
        if not widgets or not isinstance(widgets[0], str):
            continue
        name = widgets[0]
        if node_type == SET_NODE_TYPE:
            set_nodes[name] = raw
        else:
            get_nodes.setdefault(name, []).append(raw)

    if not set_nodes or not get_nodes:
        return []

    next_id = max((lnk.id for lnk in existing_links), default=0) + 1
    synthetic: list[Link] = []

    for name, set_raw in set_nodes.items():
        for get_raw in get_nodes.get(name, []):
            synthetic.append(Link(
                id=next_id,
                source_node=set_raw["id"],
                source_slot=0,
                target_node=get_raw["id"],
                target_slot=0,
                link_type="*",
                synthetic=True,
            ))
            next_id += 1

    return synthetic

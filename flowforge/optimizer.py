"""Workflow optimizer: replace high-fanout MODEL/CLIP/VAE wires with Set/Get pairs.

For every output whose type is in VIRTUAL_TYPES and that fans out to MIN_FANOUT
or more downstream nodes, the optimizer inserts:
  - one SetNode  (stores the value under a unique name key)
  - one GetNode per target  (retrieves the value)

The direct wires are removed.  The result is semantically equivalent but has
fewer long-distance connections, which reduces edge crossings after layout and
breaks inter-group cycles that the layout algorithm cannot resolve otherwise.
"""

from __future__ import annotations

from flowforge.model import (
    GET_NODE_TYPE,
    SET_NODE_TYPE,
    Link,
    Node,
    NodeInput,
    NodeOutput,
    Workflow,
)

# Output types that benefit from virtualisation when fan-out is high.
VIRTUAL_TYPES: frozenset[str] = frozenset({"MODEL", "CLIP", "VAE"})

# Minimum number of downstream consumers before we insert a Set/Get pair.
MIN_FANOUT: int = 2

# Default node dimensions for KJ-nodes Set/Get (matches ComfyUI defaults).
_SET_SIZE: list[float] = [210.0, 46.0]
_GET_SIZE: list[float] = [210.0, 46.0]


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def optimize(workflow: Workflow) -> None:
    """Insert Set/Get node pairs for high-fanout MODEL/CLIP/VAE connections.

    Mutates the workflow in place: adds new nodes and links to both the model
    objects and the raw dict, and removes the replaced direct links.
    No-op when no qualifying outputs are found.
    """
    node_by_id: dict[int, Node] = {n.id: n for n in workflow.nodes}
    raw_node_by_id: dict[int, dict] = {
        rn["id"]: rn for rn in workflow.raw.get("nodes", [])
    }
    link_by_id: dict[int, Link] = {
        lnk.id: lnk for lnk in workflow.links if not lnk.synthetic
    }

    candidates = [
        (node, slot, out)
        for node in workflow.nodes
        for slot, out in enumerate(node.outputs)
        if out.type in VIRTUAL_TYPES
        and len(out.links) >= MIN_FANOUT
        and node.type not in (SET_NODE_TYPE, GET_NODE_TYPE)
    ]

    if not candidates:
        return

    next_node_id = max(n.id for n in workflow.nodes) + 1
    next_link_id = (
        max((lnk.id for lnk in workflow.links if not lnk.synthetic), default=0) + 1
    )
    used_names: set[str] = set()

    for source_node, slot, output in candidates:
        name = _unique_name(output.name or output.type, used_names)
        used_names.add(name)

        # ------------------------------------------------------------------ #
        # SetNode: receives the value from the source output                  #
        # ------------------------------------------------------------------ #
        set_link_id = next_link_id;  next_link_id += 1
        set_id      = next_node_id;  next_node_id += 1

        set_node, set_raw = _make_set_node(
            set_id, name, output.type, set_link_id, source_node
        )
        link_to_set = Link(
            id=set_link_id,
            source_node=source_node.id,
            source_slot=slot,
            target_node=set_id,
            target_slot=0,
            link_type=output.type,
        )

        # ------------------------------------------------------------------ #
        # GetNodes: one per downstream target                                 #
        # ------------------------------------------------------------------ #
        old_link_ids = set(output.links)      # snapshot before we clear them
        link_replacement: dict[int, int] = {} # old_lid → new_lid

        get_nodes_model:  list[Node]  = []
        get_nodes_raw:    list[dict]  = []
        get_links_model:  list[Link]  = []
        get_links_raw:    list[list]  = []

        for old_lid in old_link_ids:
            orig = link_by_id.get(old_lid)
            if orig is None:
                continue

            get_id  = next_node_id;  next_node_id += 1
            new_lid = next_link_id;  next_link_id += 1

            get_node, get_raw = _make_get_node(
                get_id, name, output.type, new_lid, source_node
            )
            link_from_get = Link(
                id=new_lid,
                source_node=get_id,
                source_slot=0,
                target_node=orig.target_node,
                target_slot=orig.target_slot,
                link_type=output.type,
            )

            link_replacement[old_lid] = new_lid
            get_nodes_model.append(get_node)
            get_nodes_raw.append(get_raw)
            get_links_model.append(link_from_get)
            get_links_raw.append(
                [new_lid, get_id, 0, orig.target_node, orig.target_slot, output.type]
            )

        # ------------------------------------------------------------------ #
        # Apply mutations                                                     #
        # ------------------------------------------------------------------ #

        # a) Update source output's link list
        output.links[:] = [set_link_id]
        raw_src = raw_node_by_id.get(source_node.id)
        if raw_src:
            raw_outs = raw_src.get("outputs", [])
            if slot < len(raw_outs):
                raw_outs[slot]["links"] = [set_link_id]

        # b) Retarget each downstream node's input to the new GetNode link
        for old_lid, new_lid in link_replacement.items():
            orig = link_by_id[old_lid]
            tgt = node_by_id.get(orig.target_node)
            if tgt:
                for inp in tgt.inputs:
                    if inp.link == old_lid:
                        inp.link = new_lid
                        break
            raw_tgt = raw_node_by_id.get(orig.target_node)
            if raw_tgt:
                for ri in raw_tgt.get("inputs", []):
                    if ri.get("link") == old_lid:
                        ri["link"] = new_lid
                        break

        # c) Remove old direct links from model and raw
        workflow.links = [lnk for lnk in workflow.links if lnk.id not in old_link_ids]
        workflow.raw["links"] = [
            rl for rl in workflow.raw.get("links", [])
            if isinstance(rl, (list, tuple)) and rl[0] not in old_link_ids
        ]

        # d) Append new nodes and links
        workflow.nodes.extend([set_node] + get_nodes_model)
        workflow.links.extend([link_to_set] + get_links_model)

        workflow.raw["nodes"].extend([set_raw] + get_nodes_raw)
        workflow.raw.setdefault("links", []).append(
            [set_link_id, source_node.id, slot, set_id, 0, output.type]
        )
        workflow.raw["links"].extend(get_links_raw)

        # e) Keep local indices up to date for subsequent candidates
        node_by_id[set_id] = set_node
        raw_node_by_id[set_id] = set_raw
        link_by_id[set_link_id] = link_to_set
        for gn, gr, gl in zip(get_nodes_model, get_nodes_raw, get_links_model):
            node_by_id[gn.id] = gn
            raw_node_by_id[gn.id] = gr
            link_by_id[gl.id] = gl


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _unique_name(base: str, used: set[str]) -> str:
    """Return base if not yet used, otherwise base_2, base_3, …"""
    if base not in used:
        return base
    i = 2
    while f"{base}_{i}" in used:
        i += 1
    return f"{base}_{i}"


def _make_set_node(
    node_id: int,
    name: str,
    value_type: str,
    incoming_link_id: int,
    near_node: Node,
) -> tuple[Node, dict]:
    node = Node(
        id=node_id,
        type=SET_NODE_TYPE,
        pos=[near_node.x, near_node.y],
        size=list(_SET_SIZE),
        mode=0,
        order=0,
        inputs=[NodeInput(name=name, type=value_type, link=incoming_link_id)],
        outputs=[],
        title=None,
    )
    raw: dict = {
        "id": node_id,
        "type": SET_NODE_TYPE,
        "pos": [near_node.x, near_node.y],
        "size": list(_SET_SIZE),
        "mode": 0,
        "order": 0,
        "inputs": [{"name": name, "type": value_type, "link": incoming_link_id}],
        "outputs": [],
        "widgets_values": [name],
    }
    return node, raw


def _make_get_node(
    node_id: int,
    name: str,
    value_type: str,
    outgoing_link_id: int,
    near_node: Node,
) -> tuple[Node, dict]:
    node = Node(
        id=node_id,
        type=GET_NODE_TYPE,
        pos=[near_node.x + 300.0, near_node.y],
        size=list(_GET_SIZE),
        mode=0,
        order=0,
        inputs=[],
        outputs=[NodeOutput(name=name, type=value_type, links=[outgoing_link_id])],
        title=None,
    )
    raw: dict = {
        "id": node_id,
        "type": GET_NODE_TYPE,
        "pos": [near_node.x + 300.0, near_node.y],
        "size": list(_GET_SIZE),
        "mode": 0,
        "order": 0,
        "inputs": [],
        "outputs": [{"name": name, "type": value_type, "links": [outgoing_link_id]}],
        "widgets_values": [name],
    }
    return node, raw

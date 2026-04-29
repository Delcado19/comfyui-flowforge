"""Group-aware Sugiyama layout for ComfyUI workflows.

Mutates node.pos and group.bounding in place.
Does not touch any other field — links, widgets_values, mode, etc. are preserved.

Algorithm overview:
  1. Assign nodes to groups (geometric containment in original positions).
  2. Build a directed graph between groups from cross-group links.  Remove
     back-edges (cycle breaking via iterative DFS) to produce a DAG, then
     topologically sort groups into left-to-right columns.
  3. Within each group, apply the Sugiyama algorithm:
       a. Assign layers (longest-path, topological order).
       b. Order nodes within each layer (barycenter heuristic, 2 passes).
       c. Assign x/y coordinates relative to (0, 0).
  4. Compute global offsets for every group and translate node positions.
  5. Place decorative nodes (Note/MarkdownNote/Label) by preserving their
     original offset vector to the nearest layout node.
  6. Recalculate group bounding boxes from final node positions.
"""

from __future__ import annotations

from collections import defaultdict, deque

from flowforge.model import Group, Link, Node, Workflow

# ---------------------------------------------------------------------------
# Spacing constants (pixels)
# ---------------------------------------------------------------------------

NODE_H_GAP: int = 80      # horizontal gap between layers within a group
NODE_V_GAP: int = 40      # vertical gap between nodes in the same layer
GROUP_H_GAP: int = 200    # horizontal gap between group columns
GROUP_V_GAP: int = 100    # vertical gap between groups in the same column
GROUP_PADDING: int = 50   # padding inside a group's bounding box

_UNGROUPED: int = -1      # sentinel ID for nodes outside every group


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def apply(workflow: Workflow) -> None:
    """Lay out the workflow in place.

    Nodes are arranged left-to-right following dataflow. Groups are treated
    as blocks: the inter-group order is determined first, then nodes within
    each group are arranged independently.
    """
    layout_nodes = [n for n in workflow.nodes if n.is_layout_node]
    deco_nodes   = [n for n in workflow.nodes if n.is_decorative]

    if not layout_nodes:
        return

    layout_ids = {n.id for n in layout_nodes}
    links = [
        lnk for lnk in workflow.links
        if lnk.source_node in layout_ids and lnk.target_node in layout_ids
    ]

    # Save original positions before we start moving things — needed for
    # group membership detection and decorative placement later.
    orig_pos: dict[int, tuple[float, float]] = {
        n.id: (n.x, n.y) for n in layout_nodes
    }

    # --- Phase 1: group membership (uses original positions) ---
    node_to_group, group_to_nodes = _assign_groups(layout_nodes, workflow.groups)

    # --- Phase 2: inter-group topology ---
    group_ids = list(group_to_nodes.keys())
    group_succs = _build_group_graph(node_to_group, links)
    group_succs = _break_group_cycles(group_ids, group_succs)
    group_columns = _topo_sort_groups(group_ids, group_succs)

    # Within each column, preserve the original top-to-bottom order of groups.
    orig_centroid_y = {
        gid: _centroid_y(group_to_nodes[gid], orig_pos)
        for gid in group_ids
    }
    for col in group_columns:
        col.sort(key=lambda gid: orig_centroid_y.get(gid, 0.0))

    # --- Phase 3: internal Sugiyama per group (positions relative to origin) ---
    group_sizes: dict[int, tuple[float, float]] = {}
    for gid, nodes in group_to_nodes.items():
        internal_links = [
            lnk for lnk in links
            if node_to_group.get(lnk.source_node) == gid
            and node_to_group.get(lnk.target_node) == gid
        ]
        group_sizes[gid] = _layout_group(nodes, internal_links)

    # --- Phase 4: global offsets ---
    _apply_global_offsets(group_columns, group_sizes, group_to_nodes)

    # --- Phase 5: decorative nodes ---
    _place_decorative(deco_nodes, layout_nodes, orig_pos)

    # --- Phase 6: group bounding boxes ---
    _update_group_bounds(workflow.groups, group_to_nodes)


# ---------------------------------------------------------------------------
# Phase 1 – group membership
# ---------------------------------------------------------------------------

def _assign_groups(
    nodes: list[Node],
    groups: list[Group],
) -> tuple[dict[int, int], dict[int, list[Node]]]:
    """Map every layout node to a group ID using original (pre-layout) positions.

    Returns (node_to_group, group_to_nodes).
    Nodes not inside any group bounding box receive group ID _UNGROUPED.
    When groups overlap, the first matching group in workflow order wins.
    """
    node_to_group: dict[int, int] = {}
    group_to_nodes: dict[int, list[Node]] = defaultdict(list)

    for node in nodes:
        gid = _UNGROUPED
        for group in groups:
            if group.contains(node):
                gid = group.id
                break
        node_to_group[node.id] = gid
        group_to_nodes[gid].append(node)

    return node_to_group, dict(group_to_nodes)


# ---------------------------------------------------------------------------
# Phase 2 – inter-group topology
# ---------------------------------------------------------------------------

def _build_group_graph(
    node_to_group: dict[int, int],
    links: list[Link],
) -> dict[int, set[int]]:
    """Build a directed graph of group-to-group dependencies."""
    succs: dict[int, set[int]] = defaultdict(set)
    for lnk in links:
        src_g = node_to_group.get(lnk.source_node)
        dst_g = node_to_group.get(lnk.target_node)
        if src_g is not None and dst_g is not None and src_g != dst_g:
            succs[src_g].add(dst_g)
    return dict(succs)


def _break_group_cycles(
    group_ids: list[int],
    group_succs: dict[int, set[int]],
) -> dict[int, set[int]]:
    """Return an acyclic copy of group_succs with back-edges removed.

    Uses an iterative DFS with white/grey/black colouring.  Back-edges
    (pointing to a node already on the current DFS stack) are discarded so
    that _topo_sort_groups receives a DAG and can assign a valid left-to-right
    column to every group — including workflows where two groups exchange data
    bidirectionally (which would otherwise produce a large number of
    right-to-left connections after layout).
    """
    acyclic: dict[int, set[int]] = {gid: set(s) for gid, s in group_succs.items()}

    WHITE, GREY, BLACK = 0, 1, 2
    color: dict[int, int] = {gid: WHITE for gid in group_ids}

    for start in group_ids:
        if color.get(start) != WHITE:
            continue
        # Each stack entry: (node, iterator over a snapshot of its outgoing edges)
        stack = [(start, iter(sorted(acyclic.get(start, set()))))]
        color[start] = GREY
        while stack:
            u, it = stack[-1]
            v = next(it, None)
            if v is None:                              # all neighbours explored
                color[u] = BLACK
                stack.pop()
            elif color.get(v) == GREY:                 # back-edge → remove
                acyclic[u].discard(v)
            elif color.get(v, WHITE) == WHITE:         # tree edge → recurse
                color[v] = GREY
                stack.append((v, iter(sorted(acyclic.get(v, set())))))
            # BLACK: cross/forward edge → keep as-is

    return acyclic


def _topo_sort_groups(
    group_ids: list[int],
    group_succs: dict[int, set[int]],
) -> list[list[int]]:
    """Assign each group to a left-to-right column by longest-path depth.

    Returns a list of columns (each column is a list of group IDs).
    Groups in the same column have no ordering dependency between them.
    Groups involved in cycles or with no connections are placed last.
    """
    # Build predecessor lists
    preds: dict[int, list[int]] = {gid: [] for gid in group_ids}
    for src, dsts in group_succs.items():
        for dst in dsts:
            if dst in preds:
                preds[dst].append(src)

    # Kahn's topological sort
    in_deg = {gid: len(preds[gid]) for gid in group_ids}
    queue: deque[int] = deque(gid for gid in group_ids if in_deg[gid] == 0)
    topo: list[int] = []

    while queue:
        gid = queue.popleft()
        topo.append(gid)
        for succ in group_succs.get(gid, set()):
            if succ in in_deg:
                in_deg[succ] -= 1
                if in_deg[succ] == 0:
                    queue.append(succ)

    # Append any groups caught in a cycle
    in_topo = set(topo)
    topo.extend(gid for gid in group_ids if gid not in in_topo)

    # Longest-path depth in topological order
    depth: dict[int, int] = {}
    for gid in topo:
        ps = preds.get(gid, [])
        depth[gid] = (1 + max(depth.get(p, 0) for p in ps)) if ps else 0

    columns: dict[int, list[int]] = defaultdict(list)
    for gid, d in depth.items():
        columns[d].append(gid)

    return [columns[d] for d in sorted(columns.keys())]


# ---------------------------------------------------------------------------
# Phase 3 – internal Sugiyama layout
# ---------------------------------------------------------------------------

def _layout_group(
    nodes: list[Node],
    links: list[Link],
) -> tuple[float, float]:
    """Position nodes within a group with origin at (0, 0).

    Returns (content_width, content_height).
    Mutates node.x and node.y in place.
    """
    if not nodes:
        return 0.0, 0.0

    if len(nodes) == 1:
        nodes[0].x = 0.0
        nodes[0].y = 0.0
        return nodes[0].width, nodes[0].height

    # Layer assignment
    node_layer = _assign_layers(nodes, links)

    # Group into layers
    layers: dict[int, list[Node]] = defaultdict(list)
    for node in nodes:
        layers[node_layer[node.id]].append(node)

    # Within each layer: active nodes first (by execution order), bypassed last
    for layer_nodes in layers.values():
        layer_nodes.sort(key=lambda n: (n.is_bypassed, n.order))

    # Crossing minimisation (skip if only one layer)
    max_layer = max(layers.keys())
    if max_layer > 0:
        _barycenter(layers, links, max_layer)

    # Coordinate assignment
    x = 0.0
    layer_widths: list[float] = []
    for idx in sorted(layers.keys()):
        layer_nodes = layers[idx]
        lw = max(n.width for n in layer_nodes)
        layer_widths.append(lw)
        y = 0.0
        for node in layer_nodes:
            node.x = x
            node.y = y
            y += node.height + NODE_V_GAP
        x += lw + NODE_H_GAP

    n_layers = len(layer_widths)
    content_w = sum(layer_widths) + NODE_H_GAP * (n_layers - 1)
    content_h = max(
        sum(n.height for n in lns) + NODE_V_GAP * (len(lns) - 1)
        for lns in layers.values()
    )
    return content_w, content_h


def _assign_layers(nodes: list[Node], links: list[Link]) -> dict[int, int]:
    """Longest-path layer assignment via topological order (Kahn's algorithm).

    Nodes in cycles receive layer 0 (safe fallback).
    """
    node_ids = {n.id for n in nodes}

    preds: dict[int, list[int]] = {n.id: [] for n in nodes}
    succs: dict[int, list[int]] = {n.id: [] for n in nodes}
    for lnk in links:
        if lnk.source_node in node_ids and lnk.target_node in node_ids:
            preds[lnk.target_node].append(lnk.source_node)
            succs[lnk.source_node].append(lnk.target_node)

    in_deg = {n.id: len(preds[n.id]) for n in nodes}
    queue: deque[int] = deque(nid for nid in in_deg if in_deg[nid] == 0)
    topo: list[int] = []

    while queue:
        nid = queue.popleft()
        topo.append(nid)
        for s in succs[nid]:
            in_deg[s] -= 1
            if in_deg[s] == 0:
                queue.append(s)

    # Nodes not reached are in cycles — treat them as sources
    in_topo = set(topo)
    topo.extend(n.id for n in nodes if n.id not in in_topo)

    layer: dict[int, int] = {}
    for nid in topo:
        ps = preds[nid]
        layer[nid] = (1 + max(layer.get(p, 0) for p in ps)) if ps else 0

    return layer


def _barycenter(
    layers: dict[int, list[Node]],
    links: list[Link],
    max_layer: int,
) -> None:
    """Reduce crossing count using the barycenter heuristic (forward + backward pass).

    Mutates layers in place by reordering each layer's node list.
    Bypassed nodes are always pushed to the end of their layer.
    """
    pos: dict[int, float] = {}
    for layer_nodes in layers.values():
        for i, n in enumerate(layer_nodes):
            pos[n.id] = float(i)

    preds_map: dict[int, list[int]] = defaultdict(list)
    succs_map: dict[int, list[int]] = defaultdict(list)
    for lnk in links:
        preds_map[lnk.target_node].append(lnk.source_node)
        succs_map[lnk.source_node].append(lnk.target_node)

    def _sort_by_bary(layer_idx: int, nbr_map: dict[int, list[int]]) -> None:
        scored: list[tuple[float, bool, Node]] = []
        for node in layers[layer_idx]:
            nbrs = [n for n in nbr_map.get(node.id, []) if n in pos]
            bary = (sum(pos[n] for n in nbrs) / len(nbrs)) if nbrs else pos[node.id]
            scored.append((bary, node.is_bypassed, node))
        scored.sort(key=lambda t: (t[0], t[1]))
        layers[layer_idx] = [t[2] for t in scored]
        for i, node in enumerate(layers[layer_idx]):
            pos[node.id] = float(i)

    for i in range(1, max_layer + 1):           # forward pass
        _sort_by_bary(i, preds_map)
    for i in range(max_layer - 1, -1, -1):      # backward pass
        _sort_by_bary(i, succs_map)


# ---------------------------------------------------------------------------
# Phase 4 – global offsets
# ---------------------------------------------------------------------------

def _apply_global_offsets(
    group_columns: list[list[int]],
    group_sizes: dict[int, tuple[float, float]],
    group_to_nodes: dict[int, list[Node]],
) -> None:
    """Translate every node from group-local coordinates to global coordinates."""
    # Column width = widest group content + left/right padding
    col_widths: list[float] = [
        max(group_sizes[gid][0] for gid in col) + 2 * GROUP_PADDING
        for col in group_columns
    ]

    col_x: list[float] = []
    x = 0.0
    for w in col_widths:
        col_x.append(x)
        x += w + GROUP_H_GAP

    for col_idx, col in enumerate(group_columns):
        y = 0.0
        for gid in col:
            x_off = col_x[col_idx] + GROUP_PADDING
            y_off = y + GROUP_PADDING
            for node in group_to_nodes[gid]:
                node.x += x_off
                node.y += y_off
            content_h = group_sizes[gid][1]
            y += content_h + 2 * GROUP_PADDING + GROUP_V_GAP


# ---------------------------------------------------------------------------
# Phase 5 – decorative nodes
# ---------------------------------------------------------------------------

def _place_decorative(
    deco_nodes: list[Node],
    layout_nodes: list[Node],
    orig_pos: dict[int, tuple[float, float]],
) -> None:
    """Position each decorative node by keeping its original offset from
    the nearest layout node (measured in original coordinates)."""
    if not layout_nodes or not deco_nodes:
        return

    for deco in deco_nodes:
        nearest = min(
            layout_nodes,
            key=lambda n: _dist2(deco.x, deco.y, orig_pos[n.id][0], orig_pos[n.id][1]),
        )
        ox, oy = orig_pos[nearest.id]
        deco.x = nearest.x + (deco.x - ox)
        deco.y = nearest.y + (deco.y - oy)


def _dist2(x1: float, y1: float, x2: float, y2: float) -> float:
    return (x1 - x2) ** 2 + (y1 - y2) ** 2


# ---------------------------------------------------------------------------
# Phase 6 – group bounding boxes
# ---------------------------------------------------------------------------

def _update_group_bounds(
    groups: list[Group],
    group_to_nodes: dict[int, list[Node]],
) -> None:
    """Recalculate group.bounding from the final node positions."""
    for group in groups:
        nodes = group_to_nodes.get(group.id, [])
        if not nodes:
            continue
        min_x = min(n.x for n in nodes) - GROUP_PADDING
        min_y = min(n.y for n in nodes) - GROUP_PADDING
        max_x = max(n.x + n.width for n in nodes) + GROUP_PADDING
        max_y = max(n.y + n.height for n in nodes) + GROUP_PADDING
        group.bounding[:] = [min_x, min_y, max_x - min_x, max_y - min_y]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _centroid_y(
    nodes: list[Node],
    orig_pos: dict[int, tuple[float, float]],
) -> float:
    """Average original Y position of a list of nodes."""
    if not nodes:
        return 0.0
    return sum(orig_pos[n.id][1] for n in nodes) / len(nodes)

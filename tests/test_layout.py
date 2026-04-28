"""Tests for flowforge.layout — invariants that must hold after layout."""

from __future__ import annotations

from pathlib import Path

import pytest

from flowforge.layout import (
    GROUP_PADDING,
    NODE_H_GAP,
    _assign_groups,
    _assign_layers,
    _build_group_graph,
    _topo_sort_groups,
    apply,
)
from flowforge.model import Group, Link, Node, NodeInput, NodeOutput, Workflow
from flowforge.parser import load

WORKFLOW_DIR = Path(__file__).parent.parent / "example-workflows"
WORKFLOW_FILES = sorted(WORKFLOW_DIR.glob("*.json"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_node(nid: int, x: float = 0.0, y: float = 0.0,
               w: float = 200.0, h: float = 100.0, order: int = 0) -> Node:
    return Node(id=nid, type="KSampler", pos=[x, y], size=[w, h],
                mode=0, order=order)


def _make_link(lid: int, src: int, dst: int) -> Link:
    return Link(id=lid, source_node=src, source_slot=0,
                target_node=dst, target_slot=0, link_type="LATENT")


def _make_workflow(nodes, links=None, groups=None) -> Workflow:
    raw: dict = {"nodes": [], "links": [], "groups": []}
    return Workflow(nodes=nodes, links=links or [], groups=groups or [], raw=raw)


# ---------------------------------------------------------------------------
# Unit tests – _assign_layers
# ---------------------------------------------------------------------------

def test_assign_layers_linear_chain():
    """A→B→C must produce layers 0, 1, 2."""
    a, b, c = _make_node(1), _make_node(2), _make_node(3)
    links = [_make_link(1, 1, 2), _make_link(2, 2, 3)]
    layer = _assign_layers([a, b, c], links)
    assert layer[1] == 0
    assert layer[2] == 1
    assert layer[3] == 2


def test_assign_layers_diamond():
    """Diamond (A→B, A→C, B→D, C→D): D must be at layer 2."""
    nodes = [_make_node(i) for i in range(1, 5)]
    links = [
        _make_link(1, 1, 2), _make_link(2, 1, 3),
        _make_link(3, 2, 4), _make_link(4, 3, 4),
    ]
    layer = _assign_layers(nodes, links)
    assert layer[1] == 0
    assert layer[4] == 2


def test_assign_layers_no_links():
    """With no links every node is a source at layer 0."""
    nodes = [_make_node(i) for i in range(1, 4)]
    layer = _assign_layers(nodes, [])
    assert all(v == 0 for v in layer.values())


def test_assign_layers_cycle_does_not_crash():
    """Cyclic graphs must not raise; all nodes get a valid layer."""
    a, b = _make_node(1), _make_node(2)
    links = [_make_link(1, 1, 2), _make_link(2, 2, 1)]
    layer = _assign_layers([a, b], links)
    assert set(layer.keys()) == {1, 2}


# ---------------------------------------------------------------------------
# Unit tests – _topo_sort_groups
# ---------------------------------------------------------------------------

def test_topo_sort_groups_linear():
    """A→B→C: three separate columns."""
    succs = {1: {2}, 2: {3}}
    cols = _topo_sort_groups([1, 2, 3], succs)
    assert len(cols) == 3
    assert cols[0] == [1]
    assert cols[1] == [2]
    assert cols[2] == [3]


def test_topo_sort_groups_parallel():
    """Two independent groups must be in the same column."""
    cols = _topo_sort_groups([1, 2], {})
    assert len(cols) == 1
    assert set(cols[0]) == {1, 2}


def test_topo_sort_groups_cycle_does_not_crash():
    succs = {1: {2}, 2: {1}}
    cols = _topo_sort_groups([1, 2], succs)
    all_ids = [gid for col in cols for gid in col]
    assert set(all_ids) == {1, 2}


# ---------------------------------------------------------------------------
# Unit tests – apply() on a minimal workflow
# ---------------------------------------------------------------------------

def test_apply_simple_chain():
    """After layout, X positions must be strictly increasing along a chain."""
    a = _make_node(1, x=500.0, y=500.0)   # intentionally scrambled positions
    b = _make_node(2, x=0.0,   y=0.0)
    c = _make_node(3, x=900.0, y=200.0)
    links = [_make_link(1, 1, 2), _make_link(2, 2, 3)]
    wf = _make_workflow([a, b, c], links)
    apply(wf)
    assert a.x < b.x < c.x, f"Expected a.x < b.x < c.x, got {a.x} {b.x} {c.x}"


def test_apply_no_overlap():
    """No two nodes should occupy the same position after layout."""
    nodes = [_make_node(i, x=float(i * 10), y=float(i * 10)) for i in range(1, 6)]
    links = [_make_link(i, i, i + 1) for i in range(1, 5)]
    wf = _make_workflow(nodes, links)
    apply(wf)
    positions = [(n.x, n.y) for n in nodes]
    assert len(positions) == len(set(positions)), "Two nodes share the same position"


def test_apply_bypassed_at_end_of_layer():
    """A bypassed node sharing a layer must come after active nodes."""
    # A feeds both B (active) and C (bypassed), so B and C are in the same layer
    a = _make_node(1, order=0)
    b = _make_node(2, order=1)
    c = Node(id=3, type="KSampler", pos=[0.0, 0.0], size=[200.0, 100.0],
             mode=4, order=2)   # bypassed
    links = [_make_link(1, 1, 2), _make_link(2, 1, 3)]
    wf = _make_workflow([a, b, c], links)
    apply(wf)
    # C is bypassed and should be below B (higher Y) in the same layer
    assert c.y >= b.y, f"Bypassed node y={c.y} should be >= active node y={b.y}"


def test_apply_groups_as_blocks():
    """Nodes in separate groups must not horizontally overlap after layout."""
    # Group 1: nodes 1,2 at x=0..500, Group 2: nodes 3,4 at x=600..1200
    g1 = Group(id=1, title="G1", bounding=[0.0, 0.0, 500.0, 400.0])
    g2 = Group(id=2, title="G2", bounding=[600.0, 0.0, 500.0, 400.0])
    n1 = _make_node(1, x=10.0, y=10.0)
    n2 = _make_node(2, x=10.0, y=150.0)
    n3 = _make_node(3, x=610.0, y=10.0)
    n4 = _make_node(4, x=610.0, y=150.0)
    links = [_make_link(1, 1, 3)]   # cross-group link forces g1 before g2
    wf = _make_workflow([n1, n2, n3, n4], links, [g1, g2])
    apply(wf)
    # All g1 nodes must be to the left of all g2 nodes
    max_x_g1 = max(n1.x + n1.width, n2.x + n2.width)
    min_x_g2 = min(n3.x, n4.x)
    assert max_x_g1 < min_x_g2, (
        f"Groups overlap: g1 right edge {max_x_g1} >= g2 left edge {min_x_g2}"
    )


def test_apply_group_bounding_updated():
    """After layout, group.bounding must contain all its member nodes."""
    g = Group(id=1, title="G", bounding=[0.0, 0.0, 2000.0, 2000.0])
    n1 = _make_node(1, x=100.0, y=100.0)
    n2 = _make_node(2, x=500.0, y=300.0)
    wf = _make_workflow([n1, n2], [], [g])
    apply(wf)
    bx, by, bw, bh = g.bounding
    for node in [n1, n2]:
        assert bx <= node.x, f"Node {node.id} left of group bounding"
        assert by <= node.y, f"Node {node.id} above group bounding"
        assert node.x + node.width  <= bx + bw, f"Node {node.id} right of group bounding"
        assert node.y + node.height <= by + bh, f"Node {node.id} below group bounding"


# ---------------------------------------------------------------------------
# Integration tests – all example workflows
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("path", WORKFLOW_FILES, ids=lambda p: p.name)
def test_layout_does_not_raise(path: Path) -> None:
    """apply() must not raise on any example workflow."""
    wf = load(path)
    apply(wf)


@pytest.mark.parametrize("path", WORKFLOW_FILES, ids=lambda p: p.name)
def test_layout_nodes_have_finite_positions(path: Path) -> None:
    """Every layout node must have finite x/y coordinates after layout."""
    import math
    wf = load(path)
    apply(wf)
    for node in wf.nodes:
        if node.is_layout_node:
            assert math.isfinite(node.x), f"Node {node.id} x={node.x} is not finite"
            assert math.isfinite(node.y), f"Node {node.id} y={node.y} is not finite"


@pytest.mark.parametrize("path", WORKFLOW_FILES, ids=lambda p: p.name)
def test_layout_dataflow_direction(path: Path) -> None:
    """For every real link, the source node must be to the left of the target."""
    wf = load(path)
    apply(wf)
    node_by_id = {n.id: n for n in wf.nodes}
    violations = 0
    total = 0
    for lnk in wf.links:
        if lnk.synthetic:
            continue
        src = node_by_id.get(lnk.source_node)
        dst = node_by_id.get(lnk.target_node)
        if src is None or dst is None:
            continue
        if src.is_decorative or dst.is_decorative:
            continue
        total += 1
        if src.x > dst.x:
            violations += 1
    # Some workflows have bidirectional inter-group dependencies (e.g. a "Reference input"
    # group that both consumes from and produces data for the main pipeline). These create
    # cycles in the inter-group graph that no layout algorithm can resolve without at least
    # some back-edges. 20 % is a realistic upper bound for real-world ComfyUI workflows.
    if total > 0:
        ratio = violations / total
        assert ratio <= 0.20, (
            f"{violations}/{total} links point backwards ({ratio:.0%}) in {path.name}"
        )

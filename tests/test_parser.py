"""Tests for flowforge.parser — loads all example workflows and checks invariants."""

from __future__ import annotations

import glob
from pathlib import Path

import pytest

from flowforge.model import GET_NODE_TYPE, SET_NODE_TYPE, Workflow
from flowforge.parser import load

WORKFLOW_DIR = Path(__file__).parent.parent / "example-workflows"
WORKFLOW_FILES = sorted(WORKFLOW_DIR.glob("*.json"))


@pytest.mark.parametrize("path", WORKFLOW_FILES, ids=lambda p: p.name)
def test_load_does_not_raise(path: Path) -> None:
    """Every example workflow must load without errors."""
    wf = load(path)
    assert isinstance(wf, Workflow)


@pytest.mark.parametrize("path", WORKFLOW_FILES, ids=lambda p: p.name)
def test_nodes_have_valid_pos_and_size(path: Path) -> None:
    """Every node must have a two-element pos and size list with finite floats."""
    wf = load(path)
    for node in wf.nodes:
        assert len(node.pos) == 2, f"Node {node.id} pos length != 2"
        assert len(node.size) == 2, f"Node {node.id} size length != 2"
        assert node.width > 0, f"Node {node.id} width <= 0"
        assert node.height > 0, f"Node {node.id} height <= 0"


@pytest.mark.parametrize("path", WORKFLOW_FILES, ids=lambda p: p.name)
def test_link_node_ids_exist(path: Path) -> None:
    """Every real link must reference node IDs that exist in the workflow."""
    wf = load(path)
    node_ids = {n.id for n in wf.nodes}
    # wf.links contains real + synthetic; check only real links (positive IDs up
    # to the original count — synthetic IDs are assigned above the max real ID)
    real_links = [lnk for lnk in wf.links if lnk.link_type != "*"]
    for lnk in real_links:
        assert lnk.source_node in node_ids, (
            f"Link {lnk.id}: source node {lnk.source_node} not found"
        )
        assert lnk.target_node in node_ids, (
            f"Link {lnk.id}: target node {lnk.target_node} not found"
        )


@pytest.mark.parametrize("path", WORKFLOW_FILES, ids=lambda p: p.name)
def test_virtual_links_connect_set_to_get(path: Path) -> None:
    """Synthetic links must run from a SetNode to a GetNode."""
    wf = load(path)
    node_by_id = {n.id: n for n in wf.nodes}
    synthetic = [lnk for lnk in wf.links if lnk.synthetic]
    for lnk in synthetic:
        src = node_by_id.get(lnk.source_node)
        dst = node_by_id.get(lnk.target_node)
        assert src is not None and src.is_set_node, (
            f"Synthetic link {lnk.id}: source is not a SetNode"
        )
        assert dst is not None and dst.is_get_node, (
            f"Synthetic link {lnk.id}: target is not a GetNode"
        )


def test_size_normalisation() -> None:
    """Both JSON size formats must produce the same [width, height] list."""
    from flowforge.parser import _parse_size
    assert _parse_size([220, 60]) == [220.0, 60.0]
    assert _parse_size({"width": 220, "height": 60}) == [220.0, 60.0]


def test_no_example_workflows_missing() -> None:
    """Sanity check: at least one workflow file must be present."""
    assert len(WORKFLOW_FILES) > 0, f"No JSON files found in {WORKFLOW_DIR}"

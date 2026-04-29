"""Tests for flowforge.optimizer."""

from __future__ import annotations

from pathlib import Path

import pytest

from flowforge.model import GET_NODE_TYPE, SET_NODE_TYPE, Link, Node, NodeInput, NodeOutput, Workflow
from flowforge.optimizer import MIN_FANOUT, VIRTUAL_TYPES, optimize
from flowforge.parser import load

WORKFLOW_DIR = Path(__file__).parent.parent / "example-workflows"
WORKFLOW_FILES = sorted(WORKFLOW_DIR.glob("*.json"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fan_out_workflow(fanout: int = 2, signal_type: str = "VAE") -> Workflow:
    """Build a minimal workflow: one output fanning out to `fanout` targets."""
    loader = Node(
        id=1, type="VAELoader", pos=[0.0, 0.0], size=[200.0, 100.0],
        mode=0, order=0,
        outputs=[NodeOutput(name=signal_type, type=signal_type,
                            links=list(range(1, fanout + 1)))],
    )
    targets = [
        Node(
            id=i + 2, type="VAEDecode", pos=[300.0, float(i * 150)],
            size=[200.0, 100.0], mode=0, order=i + 1,
            inputs=[NodeInput(name="vae", type=signal_type, link=i + 1)],
        )
        for i in range(fanout)
    ]
    links = [
        Link(id=i + 1, source_node=1, source_slot=0,
             target_node=i + 2, target_slot=0, link_type=signal_type)
        for i in range(fanout)
    ]
    raw_nodes = [
        {
            "id": 1, "type": "VAELoader", "pos": [0, 0], "size": [200, 100],
            "mode": 0, "order": 0, "inputs": [],
            "outputs": [{"name": signal_type, "type": signal_type,
                         "links": list(range(1, fanout + 1))}],
        }
    ] + [
        {
            "id": i + 2, "type": "VAEDecode", "pos": [300, i * 150],
            "size": [200, 100], "mode": 0, "order": i + 1,
            "inputs": [{"name": "vae", "type": signal_type, "link": i + 1}],
            "outputs": [],
        }
        for i in range(fanout)
    ]
    raw_links = [[i + 1, 1, 0, i + 2, 0, signal_type] for i in range(fanout)]
    raw = {"nodes": raw_nodes, "links": raw_links, "groups": []}
    return Workflow(nodes=[loader] + targets, links=links, groups=[], raw=raw)


# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------

def test_optimize_inserts_set_node():
    wf = _fan_out_workflow(fanout=2)
    optimize(wf)
    assert any(n.type == SET_NODE_TYPE for n in wf.nodes)


def test_optimize_inserts_get_nodes():
    wf = _fan_out_workflow(fanout=2)
    optimize(wf)
    get_count = sum(1 for n in wf.nodes if n.type == GET_NODE_TYPE)
    assert get_count == 2


def test_optimize_one_set_node_per_output():
    wf = _fan_out_workflow(fanout=3)
    optimize(wf)
    set_count = sum(1 for n in wf.nodes if n.type == SET_NODE_TYPE)
    assert set_count == 1


def test_optimize_removes_direct_links():
    """No link should connect the loader directly to any target after optimize."""
    wf = _fan_out_workflow(fanout=2)
    target_ids = {n.id for n in wf.nodes if n.type != "VAELoader"}
    optimize(wf)
    direct = [
        lnk for lnk in wf.links
        if lnk.source_node == 1 and lnk.target_node in target_ids
    ]
    assert len(direct) == 0


def test_optimize_target_inputs_updated():
    """After optimize, target node inputs must reference new GetNode links."""
    wf = _fan_out_workflow(fanout=2)
    old_link_ids = {1, 2}
    optimize(wf)
    for node in wf.nodes:
        if node.type == "VAEDecode":
            for inp in node.inputs:
                assert inp.link not in old_link_ids, (
                    f"Node {node.id} still references old link {inp.link}"
                )


def test_optimize_raw_synced():
    """raw['nodes'] and raw['links'] must be consistent with the model after optimize."""
    wf = _fan_out_workflow(fanout=2)
    optimize(wf)
    model_ids = {n.id for n in wf.nodes}
    raw_ids = {rn["id"] for rn in wf.raw["nodes"]}
    assert model_ids == raw_ids, "raw node IDs do not match model node IDs"

    model_link_ids = {lnk.id for lnk in wf.links if not lnk.synthetic}
    raw_link_ids = {rl[0] for rl in wf.raw["links"]}
    assert model_link_ids == raw_link_ids, "raw link IDs do not match model link IDs"


def test_optimize_no_op_below_threshold():
    """fanout=1 (< MIN_FANOUT) must not insert any Set/Get nodes."""
    wf = _fan_out_workflow(fanout=1)
    optimize(wf)
    assert all(n.type not in (SET_NODE_TYPE, GET_NODE_TYPE) for n in wf.nodes)


def test_optimize_unique_names_for_multiple_outputs():
    """Two separate VAE fan-outs must receive distinct name keys."""
    loader_a = Node(id=1, type="VAELoader", pos=[0.0, 0.0], size=[200.0, 100.0],
                    mode=0, order=0,
                    outputs=[NodeOutput(name="VAE", type="VAE", links=[1, 2])])
    loader_b = Node(id=2, type="VAELoader", pos=[0.0, 200.0], size=[200.0, 100.0],
                    mode=0, order=1,
                    outputs=[NodeOutput(name="VAE", type="VAE", links=[3, 4])])
    targets = [
        Node(id=3, type="VAEDecode", pos=[300.0, 0.0], size=[200.0, 100.0],
             mode=0, order=2, inputs=[NodeInput(name="vae", type="VAE", link=1)]),
        Node(id=4, type="VAEEncode", pos=[300.0, 150.0], size=[200.0, 100.0],
             mode=0, order=3, inputs=[NodeInput(name="vae", type="VAE", link=2)]),
        Node(id=5, type="VAEDecode", pos=[300.0, 300.0], size=[200.0, 100.0],
             mode=0, order=4, inputs=[NodeInput(name="vae", type="VAE", link=3)]),
        Node(id=6, type="VAEEncode", pos=[300.0, 450.0], size=[200.0, 100.0],
             mode=0, order=5, inputs=[NodeInput(name="vae", type="VAE", link=4)]),
    ]
    links = [
        Link(id=1, source_node=1, source_slot=0, target_node=3, target_slot=0, link_type="VAE"),
        Link(id=2, source_node=1, source_slot=0, target_node=4, target_slot=0, link_type="VAE"),
        Link(id=3, source_node=2, source_slot=0, target_node=5, target_slot=0, link_type="VAE"),
        Link(id=4, source_node=2, source_slot=0, target_node=6, target_slot=0, link_type="VAE"),
    ]
    raw = {
        "nodes": [
            {"id": 1, "type": "VAELoader", "pos": [0, 0], "size": [200, 100],
             "mode": 0, "order": 0, "inputs": [],
             "outputs": [{"name": "VAE", "type": "VAE", "links": [1, 2]}]},
            {"id": 2, "type": "VAELoader", "pos": [0, 200], "size": [200, 100],
             "mode": 0, "order": 1, "inputs": [],
             "outputs": [{"name": "VAE", "type": "VAE", "links": [3, 4]}]},
            {"id": 3, "type": "VAEDecode", "pos": [300, 0], "size": [200, 100],
             "mode": 0, "order": 2, "inputs": [{"name": "vae", "type": "VAE", "link": 1}], "outputs": []},
            {"id": 4, "type": "VAEEncode", "pos": [300, 150], "size": [200, 100],
             "mode": 0, "order": 3, "inputs": [{"name": "vae", "type": "VAE", "link": 2}], "outputs": []},
            {"id": 5, "type": "VAEDecode", "pos": [300, 300], "size": [200, 100],
             "mode": 0, "order": 4, "inputs": [{"name": "vae", "type": "VAE", "link": 3}], "outputs": []},
            {"id": 6, "type": "VAEEncode", "pos": [300, 450], "size": [200, 100],
             "mode": 0, "order": 5, "inputs": [{"name": "vae", "type": "VAE", "link": 4}], "outputs": []},
        ],
        "links": [[1,1,0,3,0,"VAE"],[2,1,0,4,0,"VAE"],[3,2,0,5,0,"VAE"],[4,2,0,6,0,"VAE"]],
        "groups": [],
    }
    wf = Workflow(nodes=[loader_a, loader_b] + targets, links=links, groups=[], raw=raw)
    optimize(wf)

    set_nodes = [n for n in wf.nodes if n.type == SET_NODE_TYPE]
    assert len(set_nodes) == 2
    names = [n.outputs[0].name if n.outputs else (n.inputs[0].name if n.inputs else "") for n in set_nodes]
    # The two SetNodes must have distinct widget names
    set_names = {rn["widgets_values"][0] for rn in wf.raw["nodes"] if rn["type"] == SET_NODE_TYPE}
    assert len(set_names) == 2, f"Set/Get name collision: {set_names}"


# ---------------------------------------------------------------------------
# Integration tests – all example workflows
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("path", WORKFLOW_FILES, ids=lambda p: p.name)
def test_optimize_does_not_raise(path: Path) -> None:
    wf = load(path)
    optimize(wf)


@pytest.mark.parametrize("path", WORKFLOW_FILES, ids=lambda p: p.name)
def test_optimize_then_layout_does_not_raise(path: Path) -> None:
    from flowforge.layout import apply
    wf = load(path)
    optimize(wf)
    apply(wf)


@pytest.mark.parametrize("path", WORKFLOW_FILES, ids=lambda p: p.name)
def test_optimize_raw_consistency(path: Path) -> None:
    """After optimize(), raw node IDs and link IDs must match the model."""
    wf = load(path)
    optimize(wf)
    model_ids = {n.id for n in wf.nodes}
    raw_ids = {rn["id"] for rn in wf.raw["nodes"]}
    assert model_ids == raw_ids

    model_link_ids = {lnk.id for lnk in wf.links if not lnk.synthetic}
    raw_link_ids = {rl[0] for rl in wf.raw["links"]}
    assert model_link_ids == raw_link_ids

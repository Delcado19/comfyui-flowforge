"""Tests for local workflow validation helpers."""

from copy import deepcopy

import pytest

from flowforge.workflow_validation import validate_layout_roundtrip


WORKFLOW = {
    "nodes": [
        {
            "id": 1,
            "type": "LoadImage",
            "pos": [100, 100],
            "size": [200, 100],
            "mode": 0,
            "order": 0,
            "inputs": [],
            "outputs": [{"name": "IMAGE", "links": [10]}],
            "properties": {"Node name for S&R": "LoadImage"},
            "widgets_values": ["example.png"],
        },
        {
            "id": 2,
            "type": "PreviewImage",
            "pos": [500, 200],
            "size": [200, 100],
            "mode": 0,
            "order": 1,
            "inputs": [{"name": "images", "link": 10}],
            "outputs": [],
        },
    ],
    "links": [[10, 1, 0, 2, 0, "IMAGE"]],
    "groups": [{"id": 5, "title": "Images", "bounding": [0, 0, 900, 700], "color": "#334455"}],
    "last_node_id": 2,
    "last_link_id": 10,
    "version": 0.4,
    "extra": {"ds": {"scale": 1.0, "offset": [0, 0]}},
}


def test_validate_layout_roundtrip_accepts_layout_only_changes():
    validate_layout_roundtrip(deepcopy(WORKFLOW))


def test_validate_layout_roundtrip_rejects_missing_source_metadata():
    workflow = deepcopy(WORKFLOW)
    workflow["nodes"][0].pop("id")

    with pytest.raises(ValueError, match="node metadata changed"):
        validate_layout_roundtrip(workflow)

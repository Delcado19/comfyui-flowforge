"""Compatibility runtime for the v2 layout engine.

The v2 candidate search intentionally works on deep copies so losing candidates
cannot mutate caller state. The established FlowForge ``apply`` contract,
however, mutates the supplied Workflow and preserves Node/Group object identity.
This module commits only the selected layout geometry back onto those original
objects.
"""

from __future__ import annotations

from .layout import LayoutSettings
from .layout_engine_v2 import apply_best_layout
from .model import Workflow


def apply(workflow: Workflow, settings: LayoutSettings | None = None) -> Workflow:
    """Run v2 candidate search and commit the selected geometry in place."""
    selected = apply_best_layout(workflow, settings)
    if selected is workflow:
        return workflow
    _commit_layout_state(workflow, selected)
    return workflow


def _commit_layout_state(target: Workflow, selected: Workflow) -> None:
    """Copy layout-only state while preserving caller-owned model objects."""
    for node_id, selected_node in selected.nodes.items():
        target_node = target.nodes.get(node_id)
        if target_node is None:
            continue
        target_node.x = selected_node.x
        target_node.y = selected_node.y
        target_node.size = list(selected_node.size)

    target_groups_by_id = {group.id: group for group in target.groups}
    reordered_groups = []
    for selected_group in selected.groups:
        target_group = target_groups_by_id.get(selected_group.id)
        if target_group is None:
            continue
        target_group.bounding = list(selected_group.bounding)
        target_group.nodes = [
            target.nodes[node.id]
            for node in selected_group.nodes
            if node.id in target.nodes
        ]
        reordered_groups.append(target_group)
    target.groups = reordered_groups

    target.ungrouped_nodes = [
        target.nodes[node.id]
        for node in selected.ungrouped_nodes
        if node.id in target.nodes
    ]
    target.layout_report = selected.layout_report

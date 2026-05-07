"""Validation helpers for ComfyUI workflow roundtrip checks."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .api import _workflow_to_comfyui_json
from .layout import apply as apply_layout
from .parser import parse_comfyui_workflow

LAYOUTED_SUFFIX = "_layouted.json"


@dataclass(frozen=True)
class WorkflowValidationFailure:
    """A single workflow validation failure."""

    path: str
    message: str


@dataclass(frozen=True)
class WorkflowValidationSummary:
    """Aggregate result for a local workflow validation run."""

    discovered_files: int = 0
    checked_workflows: int = 0
    skipped_files: int = 0
    failures: list[WorkflowValidationFailure] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failures


def discover_workflow_files(
    root: Path,
    *,
    include_layouted: bool = False,
) -> list[Path]:
    """Find candidate ComfyUI workflow JSON files below a root directory."""
    return [
        path
        for path in sorted(root.rglob("*.json"))
        if not _has_hidden_path_part(path, root)
        and (include_layouted or not path.name.endswith(LAYOUTED_SUFFIX))
    ]


def validate_workflow_files(
    root: Path,
    *,
    include_layouted: bool = False,
    limit: int | None = None,
) -> WorkflowValidationSummary:
    """Validate all discovered workflow files below a local ComfyUI root."""
    files = discover_workflow_files(root, include_layouted=include_layouted)
    if limit is not None:
        files = files[: max(0, limit)]

    checked = 0
    skipped = 0
    failures: list[WorkflowValidationFailure] = []

    for path in files:
        relative_path = str(path.relative_to(root))
        try:
            data = _load_json(path)
            if not _looks_like_ui_workflow(data):
                skipped += 1
                continue
            validate_layout_roundtrip(data)
            checked += 1
        except Exception as exc:
            failures.append(
                WorkflowValidationFailure(
                    path=relative_path,
                    message=f"{type(exc).__name__}: {exc}",
                )
            )

    return WorkflowValidationSummary(
        discovered_files=len(files),
        checked_workflows=checked,
        skipped_files=skipped,
        failures=failures,
    )


def validate_layout_roundtrip(data: dict[str, Any]) -> None:
    """Validate that a layout roundtrip changes only layout-owned fields."""
    result = _workflow_to_comfyui_json(apply_layout(parse_comfyui_workflow(data)))

    _assert_equal(
        _top_level_without_layout_fields(data),
        _top_level_without_layout_fields(result),
        "top-level fields changed outside nodes/groups",
    )
    _assert_equal(data.get("links", []), result.get("links", []), "links changed")
    _assert_equal(_nodes_without_positions(data), _nodes_without_positions(result), "node metadata changed")
    _assert_equal(_groups_without_bounding(data), _groups_without_bounding(result), "group metadata changed")


def _load_json(path: Path) -> Any:
    import json

    return json.loads(path.read_text(encoding="utf-8-sig"))


def _looks_like_ui_workflow(data: Any) -> bool:
    return isinstance(data, dict) and isinstance(data.get("nodes"), list) and isinstance(data.get("links"), list)


def _top_level_without_layout_fields(data: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in data.items() if key not in {"nodes", "groups"}}


def _nodes_without_positions(data: dict[str, Any]) -> list[dict[str, Any]]:
    nodes = data.get("nodes", [])
    if not isinstance(nodes, list):
        return []
    return [
        {key: value for key, value in node.items() if key != "pos"}
        for node in nodes
        if isinstance(node, dict)
    ]


def _groups_without_bounding(data: dict[str, Any]) -> dict[Any, dict[str, Any]]:
    groups = data.get("groups", [])
    if not isinstance(groups, list):
        return {}
    return {
        group.get("id"): {key: value for key, value in group.items() if key != "bounding"}
        for group in groups
        if isinstance(group, dict)
    }


def _assert_equal(left: Any, right: Any, message: str) -> None:
    if left != right:
        raise ValueError(message)


def _has_hidden_path_part(path: Path, root: Path) -> bool:
    return any(part.startswith(".") for part in path.relative_to(root).parts)

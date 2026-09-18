"""Read-only layout quality reporting for ComfyUI UI workflows."""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any

from .api import _workflow_to_comfyui_json
from .layout import (
    LayoutReport,
    _group_flow_layers,
    _is_decorative_node,
    _node_visual_width,
    _ungrouped_flow_layers,
    apply as apply_layout,
)
from .optimizer import optimize as optimize_workflow
from .parser import parse_comfyui_workflow
from .workflow_validation import discover_workflow_files


@dataclass(frozen=True)
class GeometryMetrics:
    """Geometry metrics for a workflow layout state."""

    width: float
    height: float
    area: float
    link_length: float
    right_to_left_links: int
    link_crossings: int


@dataclass(frozen=True)
class LayoutStructureMetrics:
    """Structural width diagnostics for a laid-out workflow."""

    group_span_width: float
    ungrouped_span_width: float
    group_flow_layers: int
    ungrouped_flow_layers: int
    group_columns: int
    ungrouped_columns: int
    widest_group_name: str | None
    widest_group_width: float
    widest_ungrouped_type: str | None
    widest_ungrouped_width: float
    left_edge_owner: str | None
    right_edge_owner: str | None


@dataclass(frozen=True)
class WorkflowQualityReport:
    """Layout quality report for one ComfyUI UI workflow."""

    path: str
    node_count: int
    link_count: int
    laid_out_node_count: int
    laid_out_link_count: int
    group_count: int
    original: GeometryMetrics
    laid_out: GeometryMetrics
    moved_nodes: int
    structure: LayoutStructureMetrics | None = None
    laid_out_crossing_categories: dict[str, int] = field(default_factory=dict)
    laid_out_right_to_left_categories: dict[str, int] = field(default_factory=dict)
    layout_candidate_count: int | None = None
    selected_layout_candidate: int | None = None
    layout_score: float | None = None


@dataclass(frozen=True)
class WorkflowQualityFailure:
    """A single layout quality report failure."""

    path: str
    message: str


@dataclass(frozen=True)
class WorkflowQualitySummary:
    """Aggregate layout quality report for a workflow root."""

    root: str
    optimize_first: bool = False
    discovered_files: int = 0
    checked_workflows: int = 0
    skipped_files: int = 0
    reports: list[WorkflowQualityReport] = field(default_factory=list)
    failures: list[WorkflowQualityFailure] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failures

    def to_json_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""
        return asdict(self)


def build_quality_summary(
    root: Path,
    *,
    include_layouted: bool = False,
    optimize_first: bool = False,
    limit: int | None = None,
    structure_top: int = 0,
) -> WorkflowQualitySummary:
    """Build read-only layout quality reports for UI workflow JSON files."""
    files = discover_workflow_files(root, include_layouted=include_layouted)
    if limit is not None:
        files = files[: max(0, limit)]

    checked = 0
    skipped = 0
    reports: list[WorkflowQualityReport] = []
    failures: list[WorkflowQualityFailure] = []
    laid_out_workflows: dict[str, Any] = {}

    for path in files:
        relative_path = str(path.relative_to(root))
        try:
            data = _load_json(path)
            if not _looks_like_ui_workflow(data):
                skipped += 1
                continue
            report, laid_out_workflow = _build_workflow_quality_report_and_layout(
                data,
                relative_path,
                optimize_first=optimize_first,
                include_structure=False,
            )
            reports.append(report)
            if structure_top > 0:
                laid_out_workflows[relative_path] = laid_out_workflow
            checked += 1
        except Exception as exc:
            failures.append(
                WorkflowQualityFailure(
                    path=relative_path,
                    message=f"{type(exc).__name__}: {exc}",
                )
            )

    if structure_top > 0 and reports:
        selected_paths = {
            report.path
            for report in sorted(
                reports,
                key=lambda item: item.laid_out.area,
                reverse=True,
            )[: max(0, structure_top)]
        }
        reports = [
            replace(
                report,
                structure=_layout_structure_metrics(laid_out_workflows[report.path]),
            )
            if report.path in selected_paths
            else report
            for report in reports
        ]

    return WorkflowQualitySummary(
        root=str(root),
        optimize_first=optimize_first,
        discovered_files=len(files),
        checked_workflows=checked,
        skipped_files=skipped,
        reports=reports,
        failures=failures,
    )


def build_workflow_quality_report(
    data: dict[str, Any],
    path: str = "<memory>",
    *,
    optimize_first: bool = False,
) -> WorkflowQualityReport:
    """Build a quality report for one parsed UI workflow dictionary."""
    report, _laid_out_workflow = _build_workflow_quality_report_and_layout(
        data,
        path,
        optimize_first=optimize_first,
        include_structure=True,
    )
    return report


def _build_workflow_quality_report_and_layout(
    data: dict[str, Any],
    path: str,
    *,
    optimize_first: bool,
    include_structure: bool,
) -> tuple[WorkflowQualityReport, Any]:
    """Build one report and retain its laid-out workflow for optional diagnostics."""
    workflow = parse_comfyui_workflow(data)
    if optimize_first:
        workflow = optimize_workflow(workflow)
    laid_out_workflow = apply_layout(workflow)
    laid_out_data = _workflow_to_comfyui_json(laid_out_workflow)
    layout_report = laid_out_workflow.layout_report
    category_source = laid_out_data if optimize_first else data

    original_metrics = _geometry_metrics(data)
    laid_out_metrics = _geometry_metrics(laid_out_data)
    report = WorkflowQualityReport(
        path=path,
        node_count=len(data.get("nodes", [])),
        link_count=len(data.get("links", [])),
        laid_out_node_count=len(laid_out_data.get("nodes", [])),
        laid_out_link_count=len(laid_out_data.get("links", [])),
        group_count=len(data.get("groups", []))
        if isinstance(data.get("groups", []), list)
        else 0,
        original=original_metrics,
        laid_out=laid_out_metrics,
        moved_nodes=_count_moved_nodes(data, laid_out_data),
        structure=(
            _layout_structure_metrics(laid_out_workflow)
            if include_structure
            else None
        ),
        laid_out_crossing_categories=_count_crossing_categories(
            category_source, laid_out_data
        ),
        laid_out_right_to_left_categories=_count_right_to_left_categories(
            category_source, laid_out_data
        ),
        layout_candidate_count=_layout_candidate_count(layout_report),
        selected_layout_candidate=_selected_layout_candidate(layout_report),
        layout_score=_layout_score(layout_report),
    )
    return report, laid_out_workflow


def summarize_quality_text(
    summary: WorkflowQualitySummary,
    *,
    top: int = 10,
    include_structure: bool = False,
) -> str:
    """Format a compact text summary for terminal output."""
    lines = [
        f"Root: {summary.root}",
        f"Optimization: {'enabled' if summary.optimize_first else 'disabled'}",
        f"Discovered JSON files: {summary.discovered_files}",
        f"Checked UI workflows: {summary.checked_workflows}",
        f"Skipped non-workflow files: {summary.skipped_files}",
        f"Failures: {len(summary.failures)}",
    ]
    if summary.reports:
        total_nodes = sum(report.node_count for report in summary.reports)
        total_links = sum(report.link_count for report in summary.reports)
        laid_out_nodes = sum(report.laid_out_node_count for report in summary.reports)
        laid_out_links = sum(report.laid_out_link_count for report in summary.reports)
        original_crossings = sum(report.original.link_crossings for report in summary.reports)
        laid_out_crossings = sum(report.laid_out.link_crossings for report in summary.reports)
        original_rtl = sum(report.original.right_to_left_links for report in summary.reports)
        laid_out_rtl = sum(report.laid_out.right_to_left_links for report in summary.reports)
        crossing_categories = _merge_category_counts(
            report.laid_out_crossing_categories for report in summary.reports
        )
        rtl_categories = _merge_category_counts(
            report.laid_out_right_to_left_categories for report in summary.reports
        )
        lines.extend(
            [
                _format_count_change("Total nodes", total_nodes, laid_out_nodes),
                _format_count_change("Total links", total_links, laid_out_links),
                f"Straight-line crossings: {original_crossings} -> {laid_out_crossings}",
                f"Right-to-left links: {original_rtl} -> {laid_out_rtl}",
            ]
        )
        if crossing_categories:
            lines.append("Top laid-out crossing categories:")
            for name, count in _top_category_counts(crossing_categories, top=5):
                lines.append(f"- {name}: {count}")
        if rtl_categories:
            lines.append("Top laid-out right-to-left categories:")
            for name, count in _top_category_counts(rtl_categories, top=5):
                lines.append(f"- {name}: {count}")
        largest = sorted(summary.reports, key=lambda item: item.laid_out.area, reverse=True)[:top]
        if largest:
            lines.append(f"Largest laid-out workflows (top {len(largest)}):")
            for report in largest:
                lines.append(
                    "- "
                    f"{report.path}: nodes={report.node_count} links={report.link_count} "
                    f"size={report.laid_out.width:.0f}x{report.laid_out.height:.0f} "
                    f"crossings={report.original.link_crossings}->{report.laid_out.link_crossings} "
                    f"rtl={report.original.right_to_left_links}->{report.laid_out.right_to_left_links}"
                )
                if include_structure and report.structure is not None:
                    structure = report.structure
                    widest_group = (
                        f"{structure.widest_group_name} ({structure.widest_group_width:.0f})"
                        if structure.widest_group_name is not None
                        else "none"
                    )
                    widest_ungrouped = (
                        f"{structure.widest_ungrouped_type} ({structure.widest_ungrouped_width:.0f})"
                        if structure.widest_ungrouped_type is not None
                        else "none"
                    )
                    lines.append(
                        "  structure: "
                        f"groups span={structure.group_span_width:.0f} "
                        f"layers={structure.group_flow_layers} cols={structure.group_columns} "
                        f"widest={widest_group}; "
                        f"ungrouped span={structure.ungrouped_span_width:.0f} "
                        f"layers={structure.ungrouped_flow_layers} cols={structure.ungrouped_columns} "
                        f"widest={widest_ungrouped}; "
                        f"edges={structure.left_edge_owner} -> {structure.right_edge_owner}"
                    )
    for failure in summary.failures[:20]:
        lines.append(f"- {failure.path}: {failure.message}")
    return "\n".join(lines)



def _layout_structure_metrics(workflow) -> LayoutStructureMetrics:
    """Measure which laid-out structures dominate horizontal workflow span."""
    groups = [
        group
        for group in workflow.groups
        if group.nodes and len(group.bounding) >= 4 and group.bounding[2] > 0
    ]
    ungrouped = [
        node
        for node in workflow.ungrouped_nodes
        if node.id in workflow.nodes and not _is_decorative_node(node)
    ]

    group_span_width = _span_width(
        (group.bounding[0], group.bounding[0] + group.bounding[2])
        for group in groups
    )
    ungrouped_span_width = _span_width(
        (node.x, node.x + _node_visual_width(node))
        for node in ungrouped
    )

    group_layers = _group_flow_layers(workflow) if groups else {}
    ungrouped_layers = _ungrouped_flow_layers(workflow, ungrouped) if ungrouped else {}

    widest_group = max(groups, key=lambda group: group.bounding[2], default=None)
    widest_ungrouped = max(
        ungrouped,
        key=_node_visual_width,
        default=None,
    )

    edge_items: list[tuple[float, float, str]] = []
    for group in groups:
        label = f"group:{group.name or group.id}"
        edge_items.append(
            (group.bounding[0], group.bounding[0] + group.bounding[2], label)
        )
    for node in ungrouped:
        label = f"node:{node.type}#{node.id}"
        edge_items.append((node.x, node.x + _node_visual_width(node), label))

    left_owner = min(edge_items, key=lambda item: item[0])[2] if edge_items else None
    right_owner = max(edge_items, key=lambda item: item[1])[2] if edge_items else None

    return LayoutStructureMetrics(
        group_span_width=group_span_width,
        ungrouped_span_width=ungrouped_span_width,
        group_flow_layers=_layer_count(group_layers),
        ungrouped_flow_layers=_layer_count(ungrouped_layers),
        group_columns=len({round(group.bounding[0], 3) for group in groups}),
        ungrouped_columns=len({round(node.x, 3) for node in ungrouped}),
        widest_group_name=(
            (widest_group.name or f"group-{widest_group.id}")
            if widest_group is not None
            else None
        ),
        widest_group_width=(
            float(widest_group.bounding[2]) if widest_group is not None else 0.0
        ),
        widest_ungrouped_type=(
            widest_ungrouped.type if widest_ungrouped is not None else None
        ),
        widest_ungrouped_width=(
            _node_visual_width(widest_ungrouped)
            if widest_ungrouped is not None
            else 0.0
        ),
        left_edge_owner=left_owner,
        right_edge_owner=right_owner,
    )


def _span_width(spans) -> float:
    spans = list(spans)
    if not spans:
        return 0.0
    return max(right for _left, right in spans) - min(left for left, _right in spans)


def _layer_count(layers: dict[int, int]) -> int:
    return max(layers.values(), default=-1) + 1


def _geometry_metrics(data: dict[str, Any]) -> GeometryMetrics:
    centers = _node_centers(data)
    return GeometryMetrics(
        width=_workflow_width(data),
        height=_workflow_height(data),
        area=_workflow_area(data),
        link_length=_total_link_length(data, centers),
        right_to_left_links=_count_right_to_left_links(data, centers),
        link_crossings=_count_link_crossings(data, centers),
    )


def _format_count_change(label: str, original: int, laid_out: int) -> str:
    if original == laid_out:
        return f"{label}: {original}"
    return f"{label}: {original} -> {laid_out}"


def _node_centers(data: dict[str, Any]) -> dict[int, tuple[float, float]]:
    centers: dict[int, tuple[float, float]] = {}
    nodes = data.get("nodes", [])
    if not isinstance(nodes, list):
        return centers
    for node in nodes:
        if not isinstance(node, dict) or "id" not in node:
            continue
        x, y = _point(node.get("pos", [0.0, 0.0]))
        width, height = _size(node.get("size", [0.0, 0.0]))
        centers[int(node["id"])] = (x + width / 2.0, y + height / 2.0)
    return centers


def _workflow_bounds(data: dict[str, Any]) -> tuple[float, float, float, float]:
    boxes: list[tuple[float, float, float, float]] = []
    nodes = data.get("nodes", [])
    if isinstance(nodes, list):
        for node in nodes:
            if not isinstance(node, dict):
                continue
            x, y = _point(node.get("pos", [0.0, 0.0]))
            width, height = _size(node.get("size", [0.0, 0.0]))
            boxes.append((x, y, x + width, y + height))

    groups = data.get("groups", [])
    if isinstance(groups, list):
        for group in groups:
            if not isinstance(group, dict):
                continue
            x, y, width, height = _box(group.get("bounding", [0.0, 0.0, 0.0, 0.0]))
            boxes.append((x, y, x + width, y + height))

    if not boxes:
        return (0.0, 0.0, 0.0, 0.0)
    min_x = min(box[0] for box in boxes)
    min_y = min(box[1] for box in boxes)
    max_x = max(box[2] for box in boxes)
    max_y = max(box[3] for box in boxes)
    return (min_x, min_y, max_x, max_y)


def _workflow_width(data: dict[str, Any]) -> float:
    min_x, _, max_x, _ = _workflow_bounds(data)
    return max(0.0, max_x - min_x)


def _workflow_height(data: dict[str, Any]) -> float:
    _, min_y, _, max_y = _workflow_bounds(data)
    return max(0.0, max_y - min_y)


def _workflow_area(data: dict[str, Any]) -> float:
    return _workflow_width(data) * _workflow_height(data)


def _total_link_length(data: dict[str, Any], centers: dict[int, tuple[float, float]]) -> float:
    total = 0.0
    for source, target in _link_node_pairs(data):
        if source not in centers or target not in centers:
            continue
        sx, sy = centers[source]
        tx, ty = centers[target]
        total += math.hypot(tx - sx, ty - sy)
    return total


def _count_right_to_left_links(data: dict[str, Any], centers: dict[int, tuple[float, float]]) -> int:
    count = 0
    for source, target in _link_node_pairs(data):
        if source not in centers or target not in centers:
            continue
        if centers[target][0] < centers[source][0]:
            count += 1
    return count


def _count_link_crossings(data: dict[str, Any], centers: dict[int, tuple[float, float]]) -> int:
    segments: list[tuple[int, int, tuple[float, float], tuple[float, float]]] = []
    for source, target in _link_node_pairs(data):
        if source not in centers or target not in centers:
            continue
        segments.append((source, target, centers[source], centers[target]))

    crossings = 0
    for index, first in enumerate(segments):
        for second in segments[index + 1 :]:
            if first[0] in second[:2] or first[1] in second[:2]:
                continue
            if _segments_intersect(first[2], first[3], second[2], second[3]):
                crossings += 1
    return crossings


def _count_crossing_categories(original: dict[str, Any], laid_out: dict[str, Any]) -> dict[str, int]:
    centers = _node_centers(laid_out)
    categories = _link_categories(original)
    segments: list[tuple[int, int, str, tuple[float, float], tuple[float, float]]] = []
    for source, target in _link_node_pairs(laid_out):
        if source not in centers or target not in centers:
            continue
        category = categories.get((source, target), "unknown")
        segments.append((source, target, category, centers[source], centers[target]))

    counts: dict[str, int] = {}
    for index, first in enumerate(segments):
        for second in segments[index + 1 :]:
            if first[0] in second[:2] or first[1] in second[:2]:
                continue
            if _segments_intersect(first[3], first[4], second[3], second[4]):
                name = " x ".join(sorted((first[2], second[2])))
                counts[name] = counts.get(name, 0) + 1
    return counts


def _count_right_to_left_categories(original: dict[str, Any], laid_out: dict[str, Any]) -> dict[str, int]:
    centers = _node_centers(laid_out)
    categories = _link_categories(original)
    counts: dict[str, int] = {}
    for source, target in _link_node_pairs(laid_out):
        if source not in centers or target not in centers:
            continue
        if centers[target][0] < centers[source][0]:
            category = categories.get((source, target), "unknown")
            counts[category] = counts.get(category, 0) + 1
    return counts


def _link_categories(data: dict[str, Any]) -> dict[tuple[int, int], str]:
    node_groups = _node_group_membership(data)
    categories: dict[tuple[int, int], str] = {}
    for source, target in _link_node_pairs(data):
        source_group = node_groups.get(source)
        target_group = node_groups.get(target)
        if source_group is None and target_group is None:
            category = "ungrouped->ungrouped"
        elif source_group is None:
            category = "ungrouped->group"
        elif target_group is None:
            category = "group->ungrouped"
        elif source_group == target_group:
            category = "within_group"
        else:
            category = "group->group"
        categories[(source, target)] = category
    return categories


def _node_group_membership(data: dict[str, Any]) -> dict[int, int | None]:
    memberships: dict[int, int | None] = {}
    groups = data.get("groups", [])
    nodes = data.get("nodes", [])
    if not isinstance(nodes, list):
        return memberships

    group_boxes: list[tuple[int, tuple[float, float, float, float]]] = []
    if isinstance(groups, list):
        for group_index, group in enumerate(groups):
            if not isinstance(group, dict):
                continue
            group_id = group.get("id", group_index)
            group_boxes.append((int(group_id), _box(group.get("bounding", [0.0, 0.0, 0.0, 0.0]))))

    for node in nodes:
        if not isinstance(node, dict) or "id" not in node:
            continue
        node_id = int(node["id"])
        x, y = _point(node.get("pos", [0.0, 0.0]))
        memberships[node_id] = None
        for group_id, (gx, gy, width, height) in group_boxes:
            if gx <= x <= gx + width and gy <= y <= gy + height:
                memberships[node_id] = group_id
                break
    return memberships


def _segments_intersect(
    a: tuple[float, float],
    b: tuple[float, float],
    c: tuple[float, float],
    d: tuple[float, float],
) -> bool:
    return (
        _orientation(a, b, c) * _orientation(a, b, d) < 0
        and _orientation(c, d, a) * _orientation(c, d, b) < 0
    )


def _orientation(
    a: tuple[float, float],
    b: tuple[float, float],
    c: tuple[float, float],
) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _link_node_pairs(data: dict[str, Any]) -> list[tuple[int, int]]:
    pairs: list[tuple[int, int]] = []
    links = data.get("links", [])
    if not isinstance(links, list):
        return pairs
    for link in links:
        if isinstance(link, list) and len(link) >= 4:
            pairs.append((int(link[1]), int(link[3])))
    return pairs


def _merge_category_counts(category_sets) -> dict[str, int]:
    totals: dict[str, int] = {}
    for categories in category_sets:
        for name, count in categories.items():
            totals[name] = totals.get(name, 0) + count
    return totals


def _top_category_counts(categories: dict[str, int], *, top: int) -> list[tuple[str, int]]:
    return sorted(categories.items(), key=lambda item: item[1], reverse=True)[:top]


def _count_moved_nodes(original: dict[str, Any], laid_out: dict[str, Any]) -> int:
    original_positions = _node_positions(original)
    laid_out_positions = _node_positions(laid_out)
    return sum(
        1
        for node_id, original_position in original_positions.items()
        if node_id in laid_out_positions and laid_out_positions[node_id] != original_position
    )


def _node_positions(data: dict[str, Any]) -> dict[int, tuple[float, float]]:
    positions: dict[int, tuple[float, float]] = {}
    nodes = data.get("nodes", [])
    if not isinstance(nodes, list):
        return positions
    for node in nodes:
        if isinstance(node, dict) and "id" in node:
            positions[int(node["id"])] = _point(node.get("pos", [0.0, 0.0]))
    return positions


def _point(value: Any) -> tuple[float, float]:
    if isinstance(value, list) and len(value) >= 2:
        return (float(value[0]), float(value[1]))
    return (0.0, 0.0)


def _size(value: Any) -> tuple[float, float]:
    if isinstance(value, dict):
        return (float(value.get("width", 0.0)), float(value.get("height", 0.0)))
    if isinstance(value, list) and len(value) >= 2:
        return (float(value[0]), float(value[1]))
    return (0.0, 0.0)


def _box(value: Any) -> tuple[float, float, float, float]:
    if isinstance(value, list) and len(value) >= 4:
        return (float(value[0]), float(value[1]), float(value[2]), float(value[3]))
    return (0.0, 0.0, 0.0, 0.0)


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _looks_like_ui_workflow(data: Any) -> bool:
    return isinstance(data, dict) and isinstance(data.get("nodes"), list) and isinstance(data.get("links"), list)


def _layout_candidate_count(report: Any) -> int | None:
    return report.candidate_count if isinstance(report, LayoutReport) else None


def _selected_layout_candidate(report: Any) -> int | None:
    return report.selected_candidate if isinstance(report, LayoutReport) else None


def _layout_score(report: Any) -> float | None:
    return report.score.total if isinstance(report, LayoutReport) else None

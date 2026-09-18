"""Targeted diagnostics for Phase 4 ungrouped-flow compaction."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from flowforge.layout_engine_v2_phase3 import apply_best_layout as apply_phase3_layout
from flowforge.layout_engine_v2_phase4 import diagnose_phase4
from flowforge.optimizer import optimize as optimize_workflow
from flowforge.parser import parse_comfyui_workflow
from flowforge.workflow_validation import discover_workflow_files


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Explain why Phase 4 accepts or rejects selected workflows."
    )
    parser.add_argument("root", type=Path, help="Workflow corpus root.")
    parser.add_argument(
        "--match",
        action="append",
        default=[],
        help="Case-insensitive substring filter; may be repeated.",
    )
    parser.add_argument(
        "--optimize",
        action="store_true",
        help="Run FlowForge Optimize before the Phase 3 layout baseline.",
    )
    args = parser.parse_args()

    files = discover_workflow_files(args.root)
    matches = [value.casefold() for value in args.match]
    selected = [
        path
        for path in files
        if not matches
        or any(value in str(path.relative_to(args.root)).casefold() for value in matches)
    ]

    if not selected:
        print("No matching workflow files found.")
        return 1

    for path in selected:
        relative = path.relative_to(args.root)
        data = json.loads(path.read_text(encoding="utf-8"))
        workflow = parse_comfyui_workflow(data)
        if args.optimize:
            workflow = optimize_workflow(workflow)
        baseline = apply_phase3_layout(workflow)
        diag = diagnose_phase4(baseline)

        print(relative)
        print(
            "  ungrouped: "
            f"total={diag.total_ungrouped} eligible={diag.eligible_nodes} "
            f"excluded[pinned={diag.excluded_pinned}, deco={diag.excluded_decorative}, "
            f"hub={diag.excluded_virtual_hub}, control={diag.excluded_control}, "
            f"preview={diag.excluded_text_preview}]; "
            f"direct_group={diag.direct_group_nodes}"
        )
        print(
            "  components: "
            f"linked={diag.linked_components} candidates={diag.candidate_components} "
            f"compactable={diag.compactable_components} "
            f"largest_nodes={diag.largest_component_nodes} "
            f"largest_span={diag.largest_component_span:.0f} "
            f"max_potential={diag.max_potential_reduction * 100:.1f}%"
        )
        if diag.attempted:
            print(
                "  proposal: "
                f"{diag.baseline_width:.0f}x{diag.baseline_height:.0f} -> "
                f"{diag.proposed_width:.0f}x{diag.proposed_height:.0f}; "
                f"crossings={diag.baseline_crossings}->{diag.proposed_crossings}; "
                f"rtl={diag.baseline_rtl}->{diag.proposed_rtl}; "
                f"accepted={diag.accepted}; reason={diag.rejection_reason}"
            )
        else:
            print(
                "  proposal: not attempted; "
                f"reason={diag.rejection_reason}"
            )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

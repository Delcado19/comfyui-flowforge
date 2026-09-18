"""Targeted diagnostics for Phase 5 mixed global-flow refinement."""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from flowforge.layout_engine_v2_phase4 import apply_best_layout as apply_phase4_layout
from flowforge.layout_engine_v2_phase5 import diagnose_phase5
from flowforge.optimizer import optimize as optimize_workflow
from flowforge.parser import parse_comfyui_workflow
from flowforge.workflow_validation import discover_workflow_files


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Explain why Phase 5 accepts or rejects selected workflows."
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
        help="Run FlowForge Optimize before the Phase 4 baseline.",
    )
    parser.add_argument(
        "--accepted-only",
        action="store_true",
        help=(
            "Print only workflows whose Phase 5 candidate passes the full gate "
            "and suppress routine INFO logging."
        ),
    )
    args = parser.parse_args()

    if args.accepted_only:
        logging.disable(logging.INFO)

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

    attempted_count = 0
    accepted_count = 0
    rejected_count = 0
    skipped_count = 0

    for path in selected:
        relative = path.relative_to(args.root)
        data = json.loads(path.read_text(encoding="utf-8"))
        workflow = parse_comfyui_workflow(data)
        if args.optimize:
            workflow = optimize_workflow(workflow)
        baseline = apply_phase4_layout(workflow)
        diag = diagnose_phase5(baseline)

        if diag.attempted:
            attempted_count += 1
            if diag.accepted:
                accepted_count += 1
            else:
                rejected_count += 1
        else:
            skipped_count += 1

        if args.accepted_only and not diag.accepted:
            continue

        print(relative)
        print(
            "  mixed-graph: "
            f"groups={diag.group_vertices} "
            f"ungrouped={diag.ungrouped_vertices} "
            f"edges={diag.graph_edges} "
            f"layers={diag.layers} "
            f"candidates={diag.candidate_count}"
        )
        if diag.attempted:
            print(
                "  proposal: "
                f"variant={diag.proposal_variant}; "
                f"{diag.baseline_width:.0f}x{diag.baseline_height:.0f} -> "
                f"{diag.proposed_width:.0f}x{diag.proposed_height:.0f}; "
                f"port-crossings={diag.baseline_crossings}"
                f"->{diag.proposed_crossings}; "
                f"center-crossings={diag.baseline_center_crossings}"
                f"->{diag.proposed_center_crossings}; "
                f"port-rtl={diag.baseline_rtl}->{diag.proposed_rtl}; "
                f"center-rtl={diag.baseline_center_rtl}"
                f"->{diag.proposed_center_rtl}; "
                f"accepted={diag.accepted}; reason={diag.rejection_reason}"
            )
        else:
            print(
                "  proposal: not attempted; "
                f"reason={diag.rejection_reason}"
            )

    print(
        "Summary: "
        f"selected={len(selected)} "
        f"attempted={attempted_count} "
        f"accepted={accepted_count} "
        f"rejected={rejected_count} "
        f"skipped={skipped_count}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

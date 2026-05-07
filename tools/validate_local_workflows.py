"""Validate local ComfyUI workflow JSON files against FlowForge layout."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import asdict
from pathlib import Path

from flowforge.workflow_validation import validate_workflow_files

DEFAULT_WORKFLOW_ROOT = Path(r"H:\ComfyUI-Easy-Install\ComfyUI\user\default\workflows")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate local ComfyUI UI workflow JSON files with FlowForge.",
    )
    parser.add_argument(
        "root",
        nargs="?",
        type=Path,
        default=DEFAULT_WORKFLOW_ROOT,
        help=f"Workflow root to scan (default: {DEFAULT_WORKFLOW_ROOT})",
    )
    parser.add_argument(
        "--include-layouted",
        action="store_true",
        help="Include generated *_layouted.json files.",
    )
    parser.add_argument("--limit", type=int, help="Validate only the first N discovered JSON files.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON output.")
    args = parser.parse_args()

    logging.disable(logging.INFO)
    summary = validate_workflow_files(
        args.root,
        include_layouted=args.include_layouted,
        limit=args.limit,
    )

    if args.json:
        print(json.dumps(asdict(summary), indent=2))
    else:
        print(f"Discovered JSON files: {summary.discovered_files}")
        print(f"Checked UI workflows: {summary.checked_workflows}")
        print(f"Skipped non-workflow files: {summary.skipped_files}")
        print(f"Failures: {len(summary.failures)}")
        for failure in summary.failures[:20]:
            print(f"- {failure.path}: {failure.message}")

    return 0 if summary.ok else 1


if __name__ == "__main__":
    sys.exit(main())

"""Report read-only layout quality metrics for ComfyUI workflow JSON files."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from flowforge.layout_quality import build_quality_summary, summarize_quality_text


DEFAULT_WORKFLOW_ROOT = Path("example-workflows")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Report read-only FlowForge layout quality metrics for ComfyUI UI workflows.",
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
    parser.add_argument("--limit", type=int, help="Report only the first N discovered JSON files.")
    parser.add_argument("--top", type=int, default=10, help="Number of largest workflows to print.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON output.")
    args = parser.parse_args()

    logging.disable(logging.INFO)
    summary = build_quality_summary(
        args.root,
        include_layouted=args.include_layouted,
        limit=args.limit,
    )

    if args.json:
        print(json.dumps(summary.to_json_dict(), indent=2))
    else:
        print(summarize_quality_text(summary, top=max(0, args.top)))

    return 0 if summary.ok else 1


if __name__ == "__main__":
    sys.exit(main())

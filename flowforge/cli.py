"""Command-line interface for flowforge.

Usage:
    python flowforge.py workflow.json
    python flowforge.py workflow.json -o arranged.json
    python flowforge.py workflow.json --inplace
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from flowforge import layout, parser
from flowforge.model import Workflow


def main() -> None:
    args = _parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        sys.exit(f"Error: file not found: {input_path}")

    output_path = _resolve_output(input_path, args)

    wf = parser.load(input_path)
    layout.apply(wf)
    _write(wf, output_path)

    print(f"Written: {output_path}")


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="flowforge",
        description="Rearrange ComfyUI workflow nodes to minimise spaghetti connections.",
    )
    p.add_argument("input", help="Path to the input workflow JSON file.")
    p.add_argument(
        "-o", "--output",
        metavar="PATH",
        help="Output path (default: <input>_layouted.json in the same directory).",
    )
    p.add_argument(
        "--inplace",
        action="store_true",
        help="Overwrite the input file instead of creating a new one.",
    )
    return p.parse_args()


def _resolve_output(input_path: Path, args: argparse.Namespace) -> Path:
    if args.inplace:
        return input_path
    if args.output:
        return Path(args.output)
    return input_path.with_stem(input_path.stem + "_layouted")


# ---------------------------------------------------------------------------
# Writer — syncs mutated model back into raw dict, then dumps JSON
# ---------------------------------------------------------------------------

def _write(wf: Workflow, output_path: Path) -> None:
    raw = wf.raw

    # Sync node positions (only pos — all other fields are untouched)
    node_by_id = {n.id: n for n in wf.nodes}
    for raw_node in raw.get("nodes", []):
        node = node_by_id.get(raw_node["id"])
        if node is not None:
            raw_node["pos"] = [node.x, node.y]

    # Sync group bounding boxes
    group_by_id = {g.id: g for g in wf.groups}
    for raw_group in raw.get("groups", []):
        group = group_by_id.get(raw_group.get("id"))
        if group is not None:
            raw_group["bounding"] = list(group.bounding)

    # Reset viewport so ComfyUI opens with the workflow in view
    ds = raw.get("extra", {}).get("ds")
    if isinstance(ds, dict):
        ds["offset"] = [0.0, 0.0]
        ds["scale"] = 1.0

    output_path.write_text(
        json.dumps(raw, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )

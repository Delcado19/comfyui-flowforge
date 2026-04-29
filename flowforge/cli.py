"""Command-line interface for flowforge.

Usage:
    python flowforge.py                          # opens file picker
    python flowforge.py workflow.json
    python flowforge.py workflow.json -o arranged.json
    python flowforge.py workflow.json --inplace
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from flowforge import layout, optimizer, parser
from flowforge.model import Workflow


def main() -> None:
    args = _parse_args()

    if args.input is None:
        input_path = _pick_file()
        if input_path is None:
            sys.exit(0)  # user cancelled the dialog
    else:
        input_path = Path(args.input)

    if not input_path.exists():
        sys.exit(f"Error: file not found: {input_path}")

    output_path = _resolve_output(input_path, args)

    wf = parser.load(input_path)
    if args.optimize:
        optimizer.optimize(wf)
    layout.apply(wf)
    _write(wf, output_path)

    print(f"Written: {output_path}")


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="flowforge",
        description=(
            "Rearrange ComfyUI workflow nodes to minimise spaghetti connections. "
            "Opens a file picker when called without arguments."
        ),
    )
    p.add_argument(
        "input",
        nargs="?",
        default=None,
        help="Path to the input workflow JSON file. Omit to open a file picker.",
    )
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
    p.add_argument(
        "--optimize",
        action="store_true",
        help=(
            "Before layout: replace high-fanout MODEL/CLIP/VAE connections with "
            "Set/Get node pairs (comfyui-kjnodes). Reduces crossing lines and "
            "breaks inter-group cycles. Adds new nodes to the output workflow."
        ),
    )
    return p.parse_args()


def _resolve_output(input_path: Path, args: argparse.Namespace) -> Path:
    if args.inplace:
        return input_path
    if args.output:
        return Path(args.output)
    return input_path.with_stem(input_path.stem + "_layouted")


# ---------------------------------------------------------------------------
# File picker (tkinter — stdlib, native dialog on every OS)
# ---------------------------------------------------------------------------

def _pick_file() -> Path | None:
    """Open a native file-picker dialog and return the chosen path, or None."""
    try:
        import tkinter as tk
        from tkinter import filedialog
    except ImportError:
        sys.exit(
            "Error: tkinter is not available on this system.\n"
            "Install python3-tk (Linux) or pass the file path as an argument."
        )

    root = tk.Tk()
    root.withdraw()          # hide the blank root window
    root.attributes("-topmost", True)   # bring the dialog to the front
    root.update()

    path_str = filedialog.askopenfilename(
        parent=root,
        title="Select ComfyUI workflow",
        filetypes=[
            ("ComfyUI workflow", "*.json"),
            ("All files", "*.*"),
        ],
    )
    root.destroy()

    return Path(path_str) if path_str else None


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

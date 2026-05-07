"""
Command-line interface for ComfyUI FlowForge.
Provides commands to run the API server and process workflow files.
"""

import argparse
import json
import sys

from .parser import parse_comfyui_workflow
from .layout import apply as apply_layout
from .optimizer import optimize as optimize_workflow
from .api import create_app


def main():
    parser = argparse.ArgumentParser(
        description="ComfyUI FlowForge - Automatically rearrange ComfyUI workflow nodes"
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Serve command - start API server
    serve_parser = subparsers.add_parser("serve", help="Start the API server")
    serve_parser.add_argument("--port", type=int, default=8000, help="Port to run the server on (default: 8000)")

    # Layout command - process a workflow file
    layout_parser = subparsers.add_parser("layout", help="Apply layout to a workflow file")
    layout_parser.add_argument("input", help="Input workflow JSON file")
    layout_parser.add_argument("output", help="Output file for the laid-out workflow")
    layout_parser.add_argument("--optimize", action="store_true", help="Run optimizer before layout")

    # Optimize command - optimize a workflow file
    optimize_parser = subparsers.add_parser("optimize", help="Optimize a workflow file")
    optimize_parser.add_argument("input", help="Input workflow JSON file")
    optimize_parser.add_argument("output", help="Output file for optimized workflow")

    args = parser.parse_args()

    if args.command == "serve":
        run_server(args.port)
    elif args.command == "layout":
        process_layout(args.input, args.output, args.optimize)
    elif args.command == "optimize":
        process_optimize(args.input, args.output)
    else:
        parser.print_help()
        sys.exit(1)


def run_server(port: int):
    """Start the aiohttp API server."""
    from .logger import setup_logger
    logger = setup_logger(__name__)
    logger.info(f"Starting FlowForge API server on port {port}")
    app = create_app()
    from aiohttp import web
    web.run_app(app, port=port)


def process_layout(input_path: str, output_path: str, optimize_first: bool):
    """Apply layout to a workflow file."""
    try:
        with open(input_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception as e:
        print(f"Error reading input file: {e}", file=sys.stderr)
        sys.exit(1)

    workflow = parse_comfyui_workflow(data)

    if optimize_first:
        workflow = optimize_workflow(workflow)

    layouted = apply_layout(workflow)

    result = _workflow_to_comfyui_json(layouted)

    try:
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(result, f, indent=2)
        print(f"Layout applied. Output written to {output_path}")
    except Exception as e:
        print(f"Error writing output file: {e}", file=sys.stderr)
        sys.exit(1)


def process_optimize(input_path: str, output_path: str):
    """Optimize a workflow file."""
    try:
        with open(input_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception as e:
        print(f"Error reading input file: {e}", file=sys.stderr)
        sys.exit(1)

    workflow = parse_comfyui_workflow(data)
    optimized = optimize_workflow(workflow)
    result = _workflow_to_comfyui_json(optimized)

    try:
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(result, f, indent=2)
        print(f"Optimization applied. Output written to {output_path}")
    except Exception as e:
        print(f"Error writing output file: {e}", file=sys.stderr)
        sys.exit(1)


def _workflow_to_comfyui_json(workflow):
    """Convert a Workflow model back to ComfyUI JSON format."""
    from .api import _workflow_to_comfyui_json as convert
    return convert(workflow)

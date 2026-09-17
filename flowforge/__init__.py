"""
ComfyUI FlowForge package initialization.
"""

from .model import Node, Link, Group, Workflow
from .parser import parse_comfyui_workflow
from . import layout as _layout_module
from .layout_engine_v2 import apply as apply_layout
from .optimizer import optimize as optimize_workflow

# Keep the established ``flowforge.layout`` import surface working while the
# v2 engine lives in its own module. Modules such as api.py, cli.py, and
# layout_quality.py import ``apply`` from flowforge.layout after package
# initialization, so they transparently use v2 without duplicating the mature
# geometry helpers that remain in layout.py.
_layout_module.apply = apply_layout

__all__ = [
    'Node', 'Link', 'Group', 'Workflow',
    'parse_comfyui_workflow',
    'apply_layout',
    'optimize_workflow',
]

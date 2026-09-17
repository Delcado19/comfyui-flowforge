"""
ComfyUI FlowForge package initialization.
"""

from .model import Node, Link, Group, Workflow
from .parser import parse_comfyui_workflow
from . import layout as _layout_module
from .layout_engine_v2_runtime import apply as apply_layout
from .optimizer import optimize as optimize_workflow

# Keep the established ``flowforge.layout`` import surface working while the
# v2 engine lives in separate modules. The runtime adapter preserves the legacy
# in-place mutation and object-identity contract after v2 finishes its isolated
# candidate search.
_layout_module.apply = apply_layout

__all__ = [
    'Node', 'Link', 'Group', 'Workflow',
    'parse_comfyui_workflow',
    'apply_layout',
    'optimize_workflow',
]

# Top-level row wrapping for dataflow columns

## Problem

`apply_best_layout` produces workflows that are x-axis dominant: available
vertical space is left mostly unused while width grows unbounded with graph
depth. Measured across the local (gitignored) `example-workflows/` corpus,
the worst cases were:

- `Qwen/QWEN_IMAGE_EDIT_WORKFLOW.json`: 13176x3260 (ratio 4.04, 45 nodes)
- `SDXL 1.0/TooReal Studio - SDXL screenshot to realistic human v2.0.json`:
  6846x1447 (ratio 4.73, 36 nodes)

Root cause: two placement paths assign one x-column per topological layer and
never wrap into additional rows, unlike the existing group-internal layout
and the disconnected-group fallback, which already wrap:

- `_position_groups_by_flow_layers` — places groups that have dataflow
  dependencies on each other into unbounded columns (`current_x` grows every
  layer, never resets). Dominates the TooReal case (6 layers, mostly 1 group
  per layer).
- `_position_linked_ungrouped_nodes` / `_node_layer_x_positions` — same
  unbounded-column pattern for linked ungrouped nodes. Dominates the Qwen
  case (12 ungrouped nodes, no group-flow edges so groups already wrap via
  the disconnected-group fallback).

The existing wrap mechanisms (`_internal_layer_positions` for group-internal
chains, the disconnected-group fallback in `_position_groups_globally`) reset
to the left margin on each new row. That is safe there because there are no
data links between the wrapped items (group-internal reordering is cheap;
disconnected groups have no edges between them by definition). The two paths
above wrap items that *do* have real dataflow links between consecutive
layers, so a naive reset-to-left wrap would draw a long diagonal cable from
the last item of one row back to the first item of the next — worse than the
unbounded-width status quo. A boustrophedon (snake) wrap keeps consecutive
layers physically adjacent across the row break.

## Approach

### Shared wrap helper

Add one pure helper, used by both affected call sites:

```
_wrap_layer_columns(
    layer_sizes: dict[int, tuple[float, float]],  # layer index -> (width, height)
    row_width_cap: float,
) -> dict[int, tuple[int, float, bool]]            # layer index -> (row_index, x_in_row, right_to_left)
```

Rows fill left-to-right until adding the next layer's width would exceed
`row_width_cap`, then start a new row. Row direction alternates
(boustrophedon): even rows left-to-right, odd rows right-to-left. Callers
combine `(row_index, x_in_row, right_to_left)` with their own per-row y
offset and per-layer content to place nodes/groups, mirroring how
`_internal_layer_positions` already turns layer positions into node
coordinates.

Both `_position_groups_by_flow_layers` and `_position_linked_ungrouped_nodes`
/ `_node_layer_x_positions` are changed to call this helper instead of
incrementing `current_x` monotonically.

### Row width cap

Reuse the existing area/target-aspect-ratio heuristic pattern from
`_estimate_group_row_width` (which already does this for the
disconnected-group fallback), parameterized by a new constant analogous to
`GROUP_INTERNAL_WRAP_ASPECT_RATIO`. Applied independently at each of the two
call sites using that path's own layer sizes.

### Candidate integration (not a fixed always-on switch)

Add an internal-only `wrap_columns: bool = False` field to `LayoutSettings`.
It is not exposed through `LayoutSettings.from_payload` — it is not a
user-facing spacing knob, it is a layout strategy the candidate search
chooses between, same way `apply_best_layout` already picks between
different `node_x_distance`/`node_y_distance` scale profiles.

`_build_layout_candidates` additionally emits `wrap_columns=True` variants
for the first 1-2 profiles in `LAYOUT_CANDIDATE_PROFILES` (not all of them,
to avoid doubling total candidate count for every workflow). The existing
`_score_layout_candidate` requires no changes: it already penalizes width,
aspect ratio, crossings, and right-to-left links, which is sufficient to let
the scorer reject a wrap that makes things worse (e.g. adds more crossings
than it saves in width) and accept one that helps. The existing
`LAYOUT_CANDIDATE_MIN_EVALUATIONS`/`LAYOUT_CANDIDATE_PATIENCE` early-stop
logic is unchanged and continues to bound total runtime.

A single `wrap_columns` flag controls both affected paths together (not
independently toggleable per path) — both paths share the same structural
problem and there is no plausible case for wrapping one but not the other;
keeping it as one flag avoids a 4-way candidate combinatorial expansion.

## Data flow

No changes to `Workflow`/`Node`/`Group` models. The change is confined to
internal positioning math inside `layout.py`'s Phase 2/4 (group ordering,
global positioning) and the ungrouped-node positioning phase. Output shape
(`pos`, `bounding` fields only) is unchanged; JSON I/O, parser, and CLI are
untouched.

## Error handling

`_wrap_layer_columns` is a pure geometry function with no I/O. Edge cases:
empty `layer_sizes` returns an empty dict; a single layer (or any case where
total width never exceeds `row_width_cap`) returns everything in row 0,
left-to-right — identical output to today's unbounded behavior. No new
exception paths.

## Testing

`example-workflows/` is gitignored/private (per `AGENTS.md`'s local artifact
policy) and is not referenced by the committed test suite, so it cannot be
the basis for a regression test. Committed tests instead use synthetic
fixtures, following existing `test_layout.py` conventions:

- Unit test for `_wrap_layer_columns` directly: given known layer sizes and
  a cap, assert row assignment and boustrophedon x-direction alternation.
- Integration test with a synthetic chain of ~6 sequentially-connected
  groups (mirroring the TooReal shape) asserting the wrapped candidate wins
  and the resulting aspect ratio is bounded.
- Integration test with a synthetic chain of sequentially-connected
  ungrouped nodes (mirroring the Qwen shape), same assertion.

The local `example-workflows/` corpus is used ad hoc (not committed) to
confirm the fix before/after on real-world workflows.

## Documentation

Per `AGENTS.md`'s Documentation Maintenance Agent policy: add a `CHANGELOG.md`
entry under `[Unreleased]` describing the new wrap behavior, and a short
in-code comment on `_wrap_layer_columns` and at both call sites explaining
*why* boustrophedon direction is required there (unlike the existing
reset-to-left wraps) — the reasoning above about avoiding long diagonal
return cables for layers with real dataflow links.

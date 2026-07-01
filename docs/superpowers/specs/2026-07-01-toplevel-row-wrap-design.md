# Top-level row wrapping for dataflow columns

## Problem

`apply_best_layout` produces workflows that are x-axis dominant: available
vertical space is left mostly unused while width grows unbounded with graph
depth. Measured across the local (gitignored) `example-workflows/` corpus,
the worst case driven by this root cause is:

- `SDXL 1.0/TooReal Studio - SDXL screenshot to realistic human v2.0.json`:
  6846x1447 (ratio 4.73, 36 nodes)

Root cause: two placement paths assign one x-column per topological layer and
never wrap into additional rows, unlike the existing group-internal layout
and the disconnected-group fallback, which already wrap:

- `_position_groups_by_flow_layers` — places groups that have dataflow
  dependencies on each other into unbounded columns (`current_x` grows every
  layer, never resets). Dominates the TooReal case (6 layers, mostly 1 group
  per layer). **Validated by prototyping the fix below: ratio 4.73 -> 1.01,
  width 6846 -> 3800.**
- `_position_linked_ungrouped_nodes` / `_node_layer_x_positions` — same
  unbounded-column pattern for linked ungrouped nodes, for the case where
  ungrouped nodes have data links to each other (`_has_internal_links` is
  true for them). No real-corpus example was found with this specific shape
  during prototyping, but it is the exact same structural bug as the group
  case, just one call site over.

`Qwen/QWEN_IMAGE_EDIT_WORKFLOW.json` (13176x3260, ratio 4.04) was originally
also listed here as a target case, on the assumption that its 12 ungrouped
nodes drove the width through the second path above. That assumption was
wrong and was caught by prototyping the fix before writing the implementation
plan: Qwen's ungrouped nodes have no links to each other at all
(`_has_internal_links` is false for them), so
`_position_linked_ungrouped_nodes` is never even called for this workflow.
Applying the fix to Qwen confirmed this — its width was unchanged. Its actual
width driver is a single `Label (rgthree)` annotation node authored at
7940px wide, kept at its saved size by the existing (0.3.0) size-preservation
rule for annotation nodes. Per explicit product decision, annotation/label
nodes carry no wiring and are out of scope for this or any dataflow-layout
fix — only nodes that are actually linked into the graph are considered.
Qwen is dropped from this spec's target cases; it is not fixed by this
change and is not expected to be.

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
    base_x: float,
    base_y: float,
    h_gap: float,
    v_gap: float,
) -> dict[int, tuple[float, float]]                # layer index -> (x, y), final and absolute
```

This mirrors the existing convention of `_internal_layer_positions` and the
current `_node_layer_x_positions`: callers get back a ready-to-use `(x, y)`
per layer and place their own content (groups or nodes) starting there,
incrementing y within that column as today.

Rows fill left-to-right until adding the next layer's width would exceed
`row_width_cap`, then start a new row. Row direction alternates
(boustrophedon) by iterating a row's layers in reverse order whenever the row
index is odd, while still assigning x left-to-right from `base_x` — this
naturally places the layer that continues the dataflow from the previous
row's end at that row's far edge, without needing a separate "fill from the
right" code path. When `row_width_cap` is never exceeded (e.g.
`wrap_columns=False`, which passes `math.inf`), everything lands in one row
in natural order — output is byte-identical to today's unbounded behavior.

Both `_position_groups_by_flow_layers` and `_position_linked_ungrouped_nodes`
/ `_node_layer_x_positions` are changed to call this helper instead of
incrementing `current_x` monotonically.

### Row width cap

Reuse the existing area/target-aspect-ratio heuristic pattern from
`_estimate_group_row_width` (which already does this for the
disconnected-group fallback), in a new `_toplevel_wrap_row_width_cap`
function parameterized by a new constant `TOPLEVEL_WRAP_TARGET_ASPECT_RATIO`
(set to 2.8, matching `GROUP_INTERNAL_WRAP_ASPECT_RATIO`'s value as a
starting point). Applied independently at each of the two call sites using
that path's own layer sizes; reuses the existing
`LAYOUT_GROUP_ROW_MAX_AVERAGE_WIDTHS` constant rather than adding a
near-duplicate.

### Candidate integration (not a fixed always-on switch)

Add an internal-only `wrap_columns: bool = False` field to `LayoutSettings`.
It is not exposed through `LayoutSettings.from_payload` — it is not a
user-facing spacing knob, it is a layout strategy the candidate search
chooses between, same way `apply_best_layout` already picks between
different `node_x_distance`/`node_y_distance` scale profiles.

`_build_layout_candidates` prepends exactly one `wrap_columns=True` variant
(base `node_x_distance`/`node_y_distance`, unscaled) ahead of the existing
scale-profile variants, rather than emitting a wrap twin for every profile —
one extra evaluation per workflow, not doubling. It must be prepended, not
appended: `apply_best_layout`'s early-stop breaks after
`LAYOUT_CANDIDATE_MIN_EVALUATIONS` (3) evaluations once
`LAYOUT_CANDIDATE_PATIENCE` (2) of them were no improvement, and an appended
candidate could be skipped entirely for exactly the wide workflows this
exists to help. Prepending guarantees it is always evaluated. The existing
`_score_layout_candidate` requires no changes: it already penalizes width,
aspect ratio, crossings, and right-to-left links, which is sufficient to let
the scorer reject a wrap that makes things worse and accept one that helps.
`_build_layout_candidates`'s total candidate count becomes
`requested_count + 1` instead of `requested_count`.

This was validated by prototyping: on the TooReal fixture the wrap candidate
won outright (selected_candidate index 1, aspect ratio 4.73 -> 1.01). Running
the full test suite against the prototype surfaced exactly one pre-existing
test that hardcodes the exact candidate sequence
(`test_best_layout_tries_multiple_candidates_and_picks_best`) and needs its
expected list updated to include the new leading wrap-candidate entry; every
other test (102 total, one unrelated pre-existing local-corpus-size failure
aside) passed unchanged.

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
- Integration test with a synthetic chain of sequentially-connected,
  data-linked ungrouped nodes (enough layers to exceed the row width cap),
  same assertion, exercising `_position_linked_ungrouped_nodes` directly
  since no real corpus example was found that hits this path.
- Update `test_best_layout_tries_multiple_candidates_and_picks_best` in
  `tests/test_layout.py` for the new leading wrap-candidate entry (see
  Candidate integration above for the exact expected values).

The local `example-workflows/` corpus is used ad hoc (not committed) to
confirm the fix before/after on real-world workflows; annotation/label nodes
and any width they contribute are explicitly out of scope (see Problem
section) and are not part of this fix's success criteria.

## Documentation

Per `AGENTS.md`'s Documentation Maintenance Agent policy: add a `CHANGELOG.md`
entry under `[Unreleased]` describing the new wrap behavior, and a short
in-code comment on `_wrap_layer_columns` and at both call sites explaining
*why* boustrophedon direction is required there (unlike the existing
reset-to-left wraps) — the reasoning above about avoiding long diagonal
return cables for layers with real dataflow links.

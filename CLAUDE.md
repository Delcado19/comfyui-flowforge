# ComfyUI Layout Tool

## Goal

Read ComfyUI workflow JSON files and rearrange nodes and connections so the graph is easier to read and wire crossings are reduced.

## Technology

- Keep the project lightweight.
- Prefer small, testable steps.
- Avoid framework or architecture changes unless they solve a concrete problem.

## ComfyUI Workflow Format

- Workflows are JSON files.
- Nodes use `pos: [x, y]`.
- Node `size` can be either `[width, height]` or `{ "width": width, "height": height }`.
- Connections are stored in the top-level `links` array: `[link_id, src_node, src_slot, dst_node, dst_slot, "TYPE"]`.
- `inputs[].link` stores one link ID for a single connected input.
- `outputs[].links` stores an array because one output can connect to multiple inputs.
- `mode: 0` means active.
- `mode: 4` means bypassed or disabled.
- `order` is ComfyUI's computed execution order and must not be changed by layout.
- Groups use `bounding: [x, y, width, height]`.
- Group membership is inferred from node position and group bounding boxes.
- Layout compacts saved node sizes before computing graph geometry, then shrinks group rectangles to the laid-out node contents plus padding.
- Decorative nodes (`Note`, `MarkdownNote`, `Label`) are placed first in a left-side annotation column and should not influence group assignment or graph layout.
- Layout spacing is parameterized through `LayoutSettings(node_x_distance, node_y_distance)`. The GUI exposes live X and Y controls in the 20-240 px range. Horizontal gap, vertical gap, group spacing, and padding all derive from those axis values. The public layout pass evaluates a dynamic candidate count by workflow size: 3 for small graphs, 5 for medium graphs, and 7 for larger ones. It keeps the best compactness score, with extra penalties for layouts that stretch too far in X relative to Y or leave large horizontal gaps, and candidate search can stop early once the score stops improving. Candidate order is biased by the workflow aspect ratio. The API exposes the selected candidate through `X-FlowForge-Layout-Stats` for the UI.
- The GUI exposes group CRUD controls: start drag-based group creation from the canvas, delete a single group from the canvas, and clear all groups from the toolbar. The toolbar no longer has a dedicated `+ Group` button.
- Linked ungrouped nodes are positioned by local dataflow layers before vertical placement, instead of being ordered only by original Y position.
- Internal grouped node ordering uses forward/backward barycenter sweeps against adjacent layer order to reduce crossings.
- FlowForge works on UI workflow JSON, not API prompt JSON.
- Layout should preserve all unknown top-level and node-level fields.
- Local UI workflow roundtrip validation is available with `uv run python tools/validate_local_workflows.py`.
- Read-only layout quality reporting is available with `uv run python tools/report_layout_quality.py example-workflows`; it includes aggregate crossing/right-to-left metrics and laid-out link-category breakdowns.

## Development Rules

- Understand the current code and file state before changing behavior.
- Keep changes small and verifiable.
- Ask before large rewrites.
- Documentation is part of the definition of done. For behavior, command, dependency, or workflow-format changes, follow `AGENTS.md` and `docs/DOCUMENTATION_MAINTENANCE.md`.
- GitHub Actions CI mirrors the repository gates for backend tests, Ruff, Mypy, fixture workflow validation, frontend typecheck, and frontend build. Private local ComfyUI workflow validation remains local-only.
- uv uses `.python-version` to pin development and CI parity to Python 3.12.
- Keep release history in `CHANGELOG.md`; changelog entries and release notes are written in English.
- The frontend Vite config sets esbuild `tsconfigRaw` for app transforms and dependency scanning so local builds do not inherit unrelated TypeScript config files from parent directories.
- The GUI launcher serves packaged `flowforge/frontend_dist` first, then source `frontend/dist`, then falls back to the Vite development server. Use `uv run python tools/build_package_assets.py` before Python package builds that should include frontend assets.
- Release readiness can be checked with `uv run python tools/check_release_ready.py`; after a tag and GitHub Release exist, use `uv run python tools/check_release_ready.py --tag vX.Y.Z --github`.
- Chat communication should be mostly German.
- Code, comments, docstrings, documentation files, commit messages, and release notes must be English.

## ComfyUI Installation

Local ComfyUI is available for read-only validation under:

```text
H:\ComfyUI-Easy-Install\ComfyUI
```

Scan data is under:

```text
H:\ComfyUI-Easy-Install\comfyui_scan\
```

Known scan summary:

- 37 custom nodes installed, 35 active.
- 76 model files.
- Details are in `comfyui_summary.json`, `custom_nodes.json`, and `models.json`.

## Installed Custom Nodes Relevant To Parsing

### Decorative Nodes

Decorative nodes do not carry dataflow edges and should be positioned separately.

| Node type | Package |
| --- | --- |
| `Note` | comfy-core |
| `MarkdownNote` | comfyui-itools |
| `Label (rgthree)` | rgthree-comfy |

### Virtual Connections

These connections are not represented directly in the `links` array and may require synthetic graph handling.

| Node type | Package | Mechanism |
| --- | --- | --- |
| `SetNode` | comfyui-kjnodes | Stores a value name in `widgets_values[0]` |
| `GetNode` | comfyui-kjnodes | Reads a value name from `widgets_values[0]` |

A `SetNode` named `"VAE"` and a `GetNode` named `"VAE"` are virtually connected.
The optimizer is reroute-aware and cost-based: it should treat `Reroute` as a pass-through node when detecting high-fanout MODEL/CLIP/VAE paths, and it should only rewrite when the estimated routing cost goes down. Set/Get pairs are virtual connections keyed by matching widget values, so the optimizer should not add physical `SetNode -> GetNode` links. Cross-group links are weighted slightly higher so broad inter-group fanouts are prioritized. Candidate savings should be recomputed greedily after each rewrite so the best remaining candidate is always chosen on the current graph. The inserted `SetNode` should be anchored near the vertical center of its consumer cluster instead of being fixed to the source Y coordinate.

### Reroute

| Node type | Package |
| --- | --- |
| `Reroute` | comfy-core |

Reroute has one input and one output and should be treated as a real graph node.

### Subgraphs

Newer workflows can contain nodes whose `type` is a UUID, for example `ce575129-b994-4bea-81b7-07c2b68948a9`.

Their internal structure is stored under `extra.definitions.subgraphs[]`. The layout algorithm should treat them as opaque nodes in the main graph and should not rearrange internal subgraph nodes.

### Bypasser

| Node type | Package |
| --- | --- |
| `Fast Groups Bypasser (rgthree)` | rgthree-comfy |

This node controls groups and can expose an `OPT_CONNECTION` output that does not connect to regular dataflow.

### Switch Nodes

| Node type | Package |
| --- | --- |
| `Switch latent [Crystools]` | ComfyUI-Crystools |
| `ComfySwitchNode` | comfy-core |

Switch nodes choose between inputs and should use normal dataflow handling.

## Installed Packages And Workflow Node Types

### comfy-core

`VAELoader`, `VAEDecode`, `VAEEncode`, `VAEDecodeTiled`, `VAEEncodeTiled`,
`CLIPTextEncode`, `ConditioningZeroOut`, `FluxGuidance`, `ReferenceLatent`,
`KSampler`, `KSamplerSelect`, `SamplerCustomAdvanced`, `CFGGuider`, `RandomNoise`,
`EmptySD3LatentImage`, `EmptyFlux2LatentImage`, `EmptyLatentImage`,
`Flux2Scheduler`, `SetLatentNoiseMask`, `LatentUpscaleBy`,
`LoadImage`, `SaveImage`, `PreviewImage`,
`ImageScaleToTotalPixels`, `ImageScaleBy`, `ImageStitch`, `GetImageSize`, `ImageUpscaleWithModel`,
`GrowMask`, `UNETLoader`, `CLIPLoader`, `LoraLoaderModelOnly`, `ModelSamplingAuraFlow`,
`UpscaleModelLoader`, `ComfySwitchNode`, `TextEncodeQwenImageEdit`,
`Note`, `Reroute`

### ComfyUI-GGUF

`UnetLoaderGGUF`, `CLIPLoaderGGUF`, `VaeGGUF`

### rgthree-comfy

`Power Lora Loader (rgthree)`, `Fast Groups Bypasser (rgthree)`,
`Image Comparer (rgthree)`, `Label (rgthree)`, `Seed (rgthree)`

### comfyui-kjnodes

`SetNode`, `GetNode`, `VRAM_Debug`

### ComfyUI-Easy-Use

`easy loraStack`, `easy loraStackApply`, `easy seed`,
`easy cleanGpuUsed`, `easy clearCacheAll`

### ComfyUI-Crystools

`Switch latent [Crystools]`, `List of strings [Crystools]`, `Show any [Crystools]`

### comfyui-easy-sam3

`easy sam3ImageSegmentation`, `easy sam3ModelLoader`, `easy framesEditor`

### ComfyUI-Qwen3.5-Uncensored / comfyui-rmbg

`AILab_QwenVL_GGUF_Advanced`, `AILab_ImageResize`

### ComfyUI_essentials

`MaskPreview+`

### RES4LYF

`ClownsharKSampler_Beta`, `SharkOptions_Beta`, `ClownOptions_DetailBoost_Beta`,
`EmptyLatentImageCustom`

### comfyui-vrgamedevgirl

`FastFilmGrain`, `FastLaplacianSharpen`

### comfyui-vton-mask-tools

`VTONMaskCleanup`

### seedvr2_videoupscaler

`SeedVR2VideoUpscaler`, `SeedVR2LoadDiTModel`, `SeedVR2LoadVAEModel`

### seedvarianceenhancer

`SeedVarianceEnhancer`

### wlsh_nodes

`Upscale by Factor with Model (WLSH)`

### comfyui-itools

`MarkdownNote`

### comfyui-save-image-organized

`SaveImageClean`

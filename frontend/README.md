# ComfyUI FlowForge Frontend

A Vue 3 application for loading, displaying, laying out, and saving ComfyUI workflow JSON on a pan/zoom canvas.

## Setup

```bash
npm install
npm run dev
```

Open a browser at http://localhost:5173 for direct Vite development.

The repository-level launcher `uv run flowforge-gui` starts the backend API and either serves `frontend/dist` or starts the Vite development server. It opens the selected local frontend URL and automatically falls back from port 5173 when the port is unavailable. Windows users can double-click `start-flowforge.bat`; Linux users can run `sh start-flowforge.sh`.
The frontend process receives the selected backend port through `FLOWFORGE_API_PORT`, so local dev and the packaged launcher both talk to the same API instance.
The `dev` and `build` scripts run Vite through the JavaScript API with the local shared config, which keeps builds independent from unrelated TypeScript config files in parent directories.
Python package builds can stage the built frontend into `flowforge/frontend_dist` with `uv run python tools/build_package_assets.py`; installed GUI launches serve that packaged copy before falling back to source `frontend/dist` or the Vite dev server.

## Features

- **ComfyCanvas.vue** - Main canvas with wheel zoom and pan
- **ComfyNode.vue** - ComfyUI-like node component using workflow dimensions, colors, slots, and widget controls. Widget-backed inputs render on their widget rows, while unconnected widget inputs are not duplicated as separate free sockets.
- **ComfyConnection.vue** - SVG Bezier curves connecting ports
- **useWorkflowStore.ts** - Pinia store for full ComfyUI workflow JSON state
- **Toolbar Optimize + Layout Action** - Calls `/optimize` to insert Set/Get hubs for eligible high-fanout `MODEL`, `CLIP`, and `VAE` wiring, then calls `/layout`
- **Group and Minimap Layers** - Background group regions, draggable group titles, per-group delete buttons, resize handles, and a minimap for navigation
- **Toolbar Spacing Control** - Live X/Y sliders plus numeric fields that re-run `/layout` with the current spacing values; the backend compacts saved node sizes, evaluates multiple spacing candidates, keeps the most compact result, and reports the selected candidate in a response header
- **Compact Group Side Branches** - Layout keeps debug previews and prompt control side branches beside their inspected data instead of letting them stretch groups into unnecessary extra columns
- **ComfyUI Pin Respect** - Layout keeps nodes and groups with `flags.pinned: true` at their saved positions, including nodes inside pinned groups
- **Group Geometry Clearance** - Layout keeps group member nodes inside their group rectangle and moves unpinned groups as whole units when compacted group surfaces would overlap other groups or outside nodes
- **Wide Group Column Breaks** - Very wide groups start a fresh column instead of being stacked below narrow groups, keeping broad post-processing chains readable
- **Pin Controls** - Node and group title bars include pin buttons, and the canvas toolbar can clear all pinned nodes and groups
- **Toolbar Group Actions** - Button to clear all groups in the workflow; drag-based group creation starts from the canvas overlay controls
- **Before/After Layout Comparison** - After a layout run, the canvas can show the previous node positions as ghost outlines behind the current layout

## Usage

- **Open** - Load a workflow JSON file
- **Optimize + Layout** - POST the full workflow JSON to `/optimize`, then `/layout`; this is the normal cleanup flow for high-fanout workflows
- **Layout Only** - POST the full workflow JSON to `/layout` without adding Set/Get nodes
- **Before/After** - Toggle the previous node positions after a layout run
- **Save** - Download the current full workflow JSON

Canvas navigation:

- Mouse wheel zooms smoothly around the cursor position.
- Drag the canvas background with the left mouse button to pan the workflow.
- Use the canvas group creation control to drag on empty canvas space, define a group rectangle, and enter a title when prompted.
- Drag a group title to move the group and all nodes currently inside it.
- Click the pin button in a node or group title to toggle ComfyUI `flags.pinned`.
- Click the group delete button to remove a single group.
- Drag a group edge or corner to resize the group. Resize constraints keep contained nodes inside the group rectangle.
- Use the toolbar group buttons to start a new group or clear every group from the workflow.
- Use the toolbar pin button to unpin every pinned node and group in the workflow.
- Adjust the X and Y spacing controls to change layout density in real time. Each control is clamped to 20-240 px.
- Existing ComfyUI pinned nodes and groups stay fixed when layout runs.
- Optimize + Layout does not insert Set/Get hubs into pinned node or group geometry.
- Set/Get hub nodes follow their physical endpoint during layout and do not expose pin controls in the canvas.

The frontend derives display connections from the workflow `links` array. It does not maintain a separate custom connection format.

## Development

```bash
npm run typecheck  # TypeScript check
npm run build      # Production build
```

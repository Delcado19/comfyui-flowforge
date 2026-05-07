# ComfyUI FlowForge Frontend

A Vue 3 application for loading, displaying, layouting, and saving ComfyUI workflow JSON with a pan/zoom canvas.

## Setup

```bash
npm install
npm run dev
```

Open browser to http://localhost:5173 for direct Vite development.

The repository-level launcher `uv run flowforge-gui` starts the backend API and either serves `frontend/dist` or starts the Vite development server. It opens the selected local frontend URL and automatically falls back from port 5173 when the port is unavailable. Windows users can double-click `start-flowforge.bat`; Linux users can run `sh start-flowforge.sh`.
The frontend process receives the selected backend port through `FLOWFORGE_API_PORT`, so local dev and the packaged launcher both talk to the same API instance.
The `dev` and `build` scripts run Vite through the JavaScript API with the local shared config, which keeps builds independent from unrelated TypeScript config files in parent directories.

## Features

- **ComfyCanvas.vue** - Main canvas with wheel zoom and pan
- **ComfyNode.vue** - ComfyUI-like node component using workflow dimensions, colors, slots, and widget controls. Widget-backed inputs render on their widget rows, while unconnected widget inputs are not duplicated as separate free sockets.
- **ComfyConnection.vue** - SVG Bezier curves connecting ports
- **useWorkflowStore.ts** - Pinia store for full ComfyUI workflow JSON state
- **Group and Minimap Layers** - Background group regions, draggable group titles, per-group delete buttons, resize handles, and a minimap for navigation
- **Toolbar Spacing Control** - Live X/Y sliders plus numeric fields that re-run `/layout` with the current spacing values; the backend evaluates multiple spacing candidates, keeps the most compact result, and reports the selected candidate in a response header
- **Toolbar Group Actions** - Button to clear all groups in the workflow; drag-based group creation starts from the canvas overlay controls
- **Before/After Layout Comparison** - After a layout run, the canvas can show the previous node positions as ghost outlines behind the current layout

## Usage

- **Open** - Load a workflow JSON file
- **Layout** - POST the full workflow JSON to `/layout` for auto-layout
- **Compare** - Toggle the previous node positions after a layout run
- **Save** - Download the current full workflow JSON

Canvas navigation:

- Mouse wheel zooms smoothly around the cursor position.
- Drag the canvas background with the left mouse button to pan the workflow.
- Use the canvas group creation control to drag on empty canvas space, define a group rectangle, and enter a title when prompted.
- Drag a group title to move the group and all nodes currently inside it.
- Click the group delete button to remove a single group.
- Drag a group edge or corner to resize the group. Resize constraints keep contained nodes inside the group rectangle.
- Use the toolbar group buttons to start a new group or clear every group from the workflow.
- Adjust the X and Y spacing controls to change layout density in real time. Each control is clamped to 20-240 px.

The frontend derives display connections from the workflow `links` array. It does not maintain a separate custom connection format.

## Development

```bash
npm run typecheck  # TypeScript check
npm run build      # Production build
```

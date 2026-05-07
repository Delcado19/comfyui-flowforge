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

## Features

- **ComfyCanvas.vue** - Main canvas with wheel zoom and pan
- **ComfyNode.vue** - ComfyUI-like node component using workflow dimensions, colors, slots, and widget controls. Widget-backed inputs render on their widget rows, while unconnected widget inputs are not duplicated as separate free sockets.
- **ComfyConnection.vue** - SVG Bezier curves connecting ports
- **useWorkflowStore.ts** - Pinia store for full ComfyUI workflow JSON state
- **Group and Minimap Layers** - Background group regions, draggable group titles, and a minimap for navigation

## Usage

- **Open** - Load a workflow JSON file
- **Layout** - POST the full workflow JSON to `/layout` for auto-layout
- **Save** - Download the current full workflow JSON

Canvas navigation:

- Mouse wheel zooms smoothly around the cursor position.
- Drag the canvas background with the left mouse button to pan the workflow.

The frontend derives display connections from the workflow `links` array. It does not maintain a separate custom connection format.

## Development

```bash
npm run typecheck  # TypeScript check
npm run build      # Production build
```

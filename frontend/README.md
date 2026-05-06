# ComfyUI FlowForge Frontend

A Vue 3 application for loading, displaying, layouting, and saving ComfyUI workflow JSON with a pan/zoom canvas.

## Setup

```bash
npm install
npm run dev
```

Open browser to http://localhost:5173

The repository-level launcher `uv run flowforge-gui` starts the backend API and either serves `frontend/dist` or starts the Vite development server. Windows users can double-click `start-flowforge.bat`; Linux users can run `sh start-flowforge.sh`.

## Features

- **ComfyCanvas.vue** - Main canvas with wheel zoom and pan
- **ComfyNode.vue** - Node component with title, inputs (left), outputs (right)
- **ComfyConnection.vue** - SVG Bezier curves connecting ports
- **useWorkflowStore.ts** - Pinia store for full ComfyUI workflow JSON state

## Usage

- **Open** - Load a workflow JSON file
- **Layout** - POST the full workflow JSON to http://localhost:8000/layout for auto-layout
- **Save** - Download the current full workflow JSON

The frontend derives display connections from the workflow `links` array. It does not maintain a separate custom connection format.

## Development

```bash
npm run typecheck  # TypeScript check
npm run build      # Production build
```

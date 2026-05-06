# ComfyUI FlowForge Frontend

A Vue 3 application for displaying ComfyUI workflows with pan/zoom canvas.

## Setup

```bash
npm install
npm run dev
```

Open browser to http://localhost:5173

## Features

- **ComfyCanvas.vue** - Main canvas with wheel zoom and pan
- **ComfyNode.vue** - Node component with title, inputs (left), outputs (right)
- **ComfyConnection.vue** - SVG Bezier curves connecting ports
- **useWorkflowStore.ts** - Pinia store for workflow state

## Usage

- **Open** - Load a workflow JSON file
- **Layout** - POST to http://localhost:8000/layout for auto-layout
- **Save** - Download current workflow as JSON

## Development

```bash
npm run typecheck  # TypeScript check
npm run build      # Production build
```
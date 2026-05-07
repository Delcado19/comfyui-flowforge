<script setup lang="ts">
import { ref, watch } from 'vue'
import { useWorkflowStore } from './stores/useWorkflowStore'
import ComfyCanvas from './components/ComfyCanvas.vue'

const store = useWorkflowStore()
const fileInput = ref<HTMLInputElement | null>(null)
const MIN_NODE_DISTANCE_DEFAULT = 80
const MIN_NODE_DISTANCE_MIN = 20
const MIN_NODE_DISTANCE_MAX = 200

const minNodeDistance = ref(MIN_NODE_DISTANCE_DEFAULT)

let layoutTimer: ReturnType<typeof window.setTimeout> | undefined
let layoutRequestId = 0

function openFile() {
  fileInput.value?.click()
}

function onFileSelected(e: Event) {
  const input = e.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  
  const reader = new FileReader()
  reader.onload = () => {
    try {
      const data = JSON.parse(reader.result as string)
      store.loadWorkflow(data)
    } catch (err) {
      alert('Invalid workflow file')
    }
  }
  reader.readAsText(file)
}

function scheduleLayout(immediate = false) {
  if (!store.workflow) return

  if (layoutTimer !== undefined) {
    clearTimeout(layoutTimer)
    layoutTimer = undefined
  }

  const requestId = ++layoutRequestId

  const run = async () => {
    if (requestId !== layoutRequestId) return

    try {
      const response = await fetch('/layout', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          workflow: store.workflow,
          layout: {
            min_node_distance: minNodeDistance.value,
          },
        }),
      })
      const data = await response.json()
      if (!response.ok) {
        throw new Error(data.error || 'Layout request failed')
      }
      if (requestId !== layoutRequestId) return
      store.loadWorkflow(data)
    } catch (err) {
      if (requestId !== layoutRequestId) return
      alert('Layout failed: ' + err)
    }
  }

  if (immediate) {
    void run()
    return
  }

  layoutTimer = window.setTimeout(() => {
    void run()
  }, 180)
}

function layout() {
  scheduleLayout(true)
}

function save() {
  if (!store.workflow) return

  const data = store.workflow
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = 'workflow.json'
  a.click()
  URL.revokeObjectURL(url)
}

watch(minNodeDistance, () => {
  if (Number.isFinite(minNodeDistance.value)) {
    scheduleLayout()
  }
})
</script>

<template>
  <div class="app">
    <div class="toolbar">
      <button @click="openFile">Open</button>
      <button @click="layout">Layout</button>
      <div class="spacing-control">
        <span>Min distance</span>
        <input
          v-model.number="minNodeDistance"
          type="range"
          :min="MIN_NODE_DISTANCE_MIN"
          :max="MIN_NODE_DISTANCE_MAX"
          step="5"
        />
        <input
          v-model.number="minNodeDistance"
          type="number"
          :min="MIN_NODE_DISTANCE_MIN"
          :max="MIN_NODE_DISTANCE_MAX"
          step="5"
        />
      </div>
      <button @click="save">Save</button>
      <span class="zoom-info">Zoom: {{ Math.round(store.scale * 100) }}%</span>
    </div>
    <ComfyCanvas class="canvas" />
    <input ref="fileInput" type="file" accept=".json" @change="onFileSelected" style="display: none" />
  </div>
</template>

<style>
* {
  margin: 0;
  padding: 0;
  box-sizing: border-box;
}
body {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
  background: #1a1a1a;
  color: #ddd;
}
.app {
  display: flex;
  flex-direction: column;
  height: 100vh;
}
.toolbar {
  display: flex;
  gap: 8px;
  padding: 8px;
  background: #222;
  border-bottom: 1px solid #333;
  align-items: center;
}
.toolbar button {
  padding: 6px 12px;
  background: #333;
  border: 1px solid #444;
  color: #ddd;
  cursor: pointer;
  border-radius: 4px;
}
.toolbar button:hover {
  background: #444;
}
.spacing-control {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 0 8px;
  color: #bbb;
  font-size: 12px;
}
.spacing-control input[type='range'] {
  width: 220px;
}
.spacing-control input[type='number'] {
  width: 72px;
  padding: 6px 8px;
  background: #111;
  border: 1px solid #444;
  color: #ddd;
  border-radius: 4px;
}
.zoom-info {
  margin-left: auto;
  color: #888;
  font-size: 12px;
  padding: 6px 12px;
}
.canvas {
  flex: 1;
}
</style>

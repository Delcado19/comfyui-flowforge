<script setup lang="ts">
import { ref, watch } from 'vue'
import { useWorkflowStore } from './stores/useWorkflowStore'
import ComfyCanvas from './components/ComfyCanvas.vue'

const store = useWorkflowStore()
const fileInput = ref<HTMLInputElement | null>(null)
const layoutStatus = ref('')
const canvasRef = ref<{
  deleteAllGroups: () => void
} | null>(null)
const NODE_DISTANCE_DEFAULT = 80
const NODE_DISTANCE_MIN = 20
const NODE_DISTANCE_MAX = 240

const nodeXDistance = ref(NODE_DISTANCE_DEFAULT)
const nodeYDistance = ref(NODE_DISTANCE_DEFAULT)

let layoutTimer: ReturnType<typeof window.setTimeout> | undefined
let layoutRequestId = 0

type LayoutStats = {
  candidate_count: number
  selected_candidate: number
  score: {
    total: number
  }
}

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
      layoutStatus.value = ''
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
            node_x_distance: nodeXDistance.value,
            node_y_distance: nodeYDistance.value,
          },
        }),
      })
      const data = await response.json()
      if (!response.ok) {
        throw new Error(data.error || 'Layout request failed')
      }
      if (requestId !== layoutRequestId) return
      const layoutStats = parseLayoutStats(response.headers.get('X-FlowForge-Layout-Stats'))
      layoutStatus.value = layoutStats
        ? `Layout: ${layoutStats.selected_candidate}/${layoutStats.candidate_count} candidates, score ${layoutStats.score.total.toFixed(1)}`
        : 'Layout completed'
      store.loadWorkflow(data)
    } catch (err) {
      if (requestId !== layoutRequestId) return
      layoutStatus.value = ''
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

function deleteAllGroups() {
  canvasRef.value?.deleteAllGroups()
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

function parseLayoutStats(raw: string | null) {
  if (!raw) return null
  try {
    const parsed = JSON.parse(raw) as Partial<LayoutStats>
    if (
      typeof parsed.candidate_count === 'number' &&
      typeof parsed.selected_candidate === 'number' &&
      parsed.score !== undefined &&
      typeof parsed.score.total === 'number'
    ) {
      return parsed as LayoutStats
    }
  } catch {
    return null
  }
  return null
}

watch([nodeXDistance, nodeYDistance], () => {
  if (Number.isFinite(nodeXDistance.value) && Number.isFinite(nodeYDistance.value)) {
    scheduleLayout()
  }
})
</script>

<template>
  <div class="app">
    <div class="toolbar">
      <button @click="openFile">Open</button>
      <button @click="layout">Layout</button>
      <button :disabled="!(store.workflow?.groups?.length ?? 0)" @click="deleteAllGroups">Clear Groups</button>
      <div class="spacing-control">
        <label class="spacing-row">
          <span>X</span>
          <input
            v-model.number="nodeXDistance"
            type="range"
            :min="NODE_DISTANCE_MIN"
            :max="NODE_DISTANCE_MAX"
            step="5"
          />
          <input
            v-model.number="nodeXDistance"
            type="number"
            :min="NODE_DISTANCE_MIN"
            :max="NODE_DISTANCE_MAX"
            step="5"
          />
        </label>
        <label class="spacing-row">
          <span>Y</span>
          <input
            v-model.number="nodeYDistance"
            type="range"
            :min="NODE_DISTANCE_MIN"
            :max="NODE_DISTANCE_MAX"
            step="5"
          />
          <input
            v-model.number="nodeYDistance"
            type="number"
            :min="NODE_DISTANCE_MIN"
            :max="NODE_DISTANCE_MAX"
            step="5"
          />
        </label>
      </div>
      <button @click="save">Save</button>
      <span v-if="layoutStatus" class="layout-info">{{ layoutStatus }}</span>
      <span class="zoom-info">Zoom: {{ Math.round(store.scale * 100) }}%</span>
    </div>
    <ComfyCanvas ref="canvasRef" class="canvas" />
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
.toolbar button:disabled {
  cursor: default;
  opacity: 0.45;
}
.spacing-control {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 0 8px;
  color: #bbb;
  font-size: 12px;
}
.spacing-row {
  display: flex;
  align-items: center;
  gap: 8px;
}
.spacing-row > span {
  width: 14px;
  text-align: center;
}
.spacing-row input[type='range'] {
  width: 220px;
}
.spacing-row input[type='number'] {
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
.layout-info {
  color: #9aa8b8;
  font-size: 12px;
  padding: 6px 0 6px 8px;
  max-width: 320px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.canvas {
  flex: 1;
}
</style>

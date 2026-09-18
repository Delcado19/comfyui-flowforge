<script setup lang="ts">
import { onMounted, onUnmounted, ref, watch } from 'vue'
import { useWorkflowStore, type ComfyNode, type ComfyWorkflow, type NodeSize } from './stores/useWorkflowStore'
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
const comparisonNodes = ref<ComfyNode[]>([])
const showComparison = ref(false)

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
      cancelPendingLayout()
      store.loadWorkflow(data)
      layoutStatus.value = ''
      clearComparison()
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
  const historyLabel = immediate ? 'Layout Only' : 'Spacing Layout'

  const run = async () => {
    if (requestId !== layoutRequestId) return
    const beforeWorkflow = store.snapshotWorkflow()
    if (!beforeWorkflow) return
    const beforeLayout = captureComparisonNodes(beforeWorkflow.nodes)

    try {
      const { data, stats } = await requestLayout(beforeWorkflow)
      if (requestId !== layoutRequestId) return
      layoutStatus.value = stats
        ? `Layout Only: ${stats.selected_candidate}/${stats.candidate_count} candidates, score ${stats.score.total.toFixed(1)}`
        : 'Layout completed'
      store.applyWorkflowTransformation(data, historyLabel, beforeWorkflow)
      comparisonNodes.value = beforeLayout
      showComparison.value = beforeLayout.length > 0
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
    layoutTimer = undefined
    void run()
  }, 180)
}

function layout() {
  scheduleLayout(true)
}

async function optimizeAndLayout() {
  if (!store.workflow) return

  cancelPendingLayout()
  const requestId = ++layoutRequestId
  const beforeWorkflow = store.snapshotWorkflow()
  if (!beforeWorkflow) return
  const beforeLayout = captureComparisonNodes(beforeWorkflow.nodes)

  try {
    const optimized = await requestOptimize(beforeWorkflow)
    if (requestId !== layoutRequestId) return
    const { data, stats } = await requestLayout(optimized)
    if (requestId !== layoutRequestId) return
    store.applyWorkflowTransformation(data, 'Optimize + Layout', beforeWorkflow)
    comparisonNodes.value = beforeLayout
    showComparison.value = beforeLayout.length > 0
    const optimizeSummary = `${countNodesByType(data, 'SetNode')} Set, ${countNodesByType(data, 'GetNode')} Get`
    layoutStatus.value = stats
      ? `Optimize + Layout: ${optimizeSummary}, ${stats.selected_candidate}/${stats.candidate_count} candidates`
      : `Optimize + Layout: ${optimizeSummary}`
  } catch (err) {
    if (requestId !== layoutRequestId) return
    layoutStatus.value = ''
    alert('Optimize + Layout failed: ' + err)
  }
}

async function requestOptimize(workflow: ComfyWorkflow): Promise<ComfyWorkflow> {
  const response = await fetch('/optimize', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(workflow),
  })
  const data = await response.json()
  if (!response.ok) {
    throw new Error(data.error || 'Optimize request failed')
  }
  return data as ComfyWorkflow
}

async function requestLayout(workflow: ComfyWorkflow): Promise<{ data: ComfyWorkflow; stats: LayoutStats | null }> {
  const response = await fetch('/layout', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      workflow,
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
  return {
    data: data as ComfyWorkflow,
    stats: parseLayoutStats(response.headers.get('X-FlowForge-Layout-Stats')),
  }
}

function toggleComparison() {
  if (comparisonNodes.value.length === 0) return
  showComparison.value = !showComparison.value
}

function clearComparison() {
  comparisonNodes.value = []
  showComparison.value = false
}

function cancelPendingLayout() {
  if (layoutTimer !== undefined) {
    clearTimeout(layoutTimer)
    layoutTimer = undefined
  }
  layoutRequestId += 1
}

function undoTransformation() {
  if (!store.canUndo) return
  cancelPendingLayout()
  const label = store.undoWorkflowTransformation()
  if (!label) return
  clearComparison()
  layoutStatus.value = `Undo: ${label}`
}

function redoTransformation() {
  if (!store.canRedo) return
  cancelPendingLayout()
  const label = store.redoWorkflowTransformation()
  if (!label) return
  clearComparison()
  layoutStatus.value = `Redo: ${label}`
}

function isEditableShortcutTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false
  return (
    target.isContentEditable ||
    target.tagName === 'INPUT' ||
    target.tagName === 'TEXTAREA' ||
    target.tagName === 'SELECT'
  )
}

function onHistoryShortcut(event: KeyboardEvent) {
  if (!(event.ctrlKey || event.metaKey) || event.altKey) return
  if (isEditableShortcutTarget(event.target)) return

  const key = event.key.toLowerCase()
  if (key === 'z') {
    if (event.shiftKey) {
      if (!store.canRedo) return
      event.preventDefault()
      redoTransformation()
      return
    }
    if (!store.canUndo) return
    event.preventDefault()
    undoTransformation()
    return
  }

  if (key === 'y' && store.canRedo) {
    event.preventDefault()
    redoTransformation()
  }
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

function captureComparisonNodes(nodes: ComfyNode[] = store.nodes): ComfyNode[] {
  return nodes.map((node) => ({
    ...node,
    pos: [node.pos[0], node.pos[1]],
    size: copyNodeSize(node.size),
  }))
}

function copyNodeSize(size: NodeSize | undefined): NodeSize | undefined {
  if (Array.isArray(size)) return [size[0], size[1]]
  if (size && typeof size === 'object') return { ...size }
  return size
}

function countNodesByType(workflow: unknown, type: string): number {
  if (!workflow || typeof workflow !== 'object' || !('nodes' in workflow)) return 0
  const nodes = (workflow as { nodes?: unknown }).nodes
  if (!Array.isArray(nodes)) return 0
  return nodes.filter((node) => {
    return Boolean(node && typeof node === 'object' && (node as { type?: unknown }).type === type)
  }).length
}

watch([nodeXDistance, nodeYDistance], () => {
  if (Number.isFinite(nodeXDistance.value) && Number.isFinite(nodeYDistance.value)) {
    scheduleLayout()
  }
})

onMounted(() => {
  window.addEventListener('keydown', onHistoryShortcut)
})

onUnmounted(() => {
  window.removeEventListener('keydown', onHistoryShortcut)
  cancelPendingLayout()
})
</script>

<template>
  <div class="app">
    <div class="toolbar">
      <button @click="openFile">Open</button>
      <button :disabled="!store.workflow" @click="optimizeAndLayout">Optimize + Layout</button>
      <button :disabled="!store.workflow" @click="layout">Layout Only</button>
      <button
        :disabled="!store.canUndo"
        :title="store.undoLabel ? `Undo ${store.undoLabel} (Ctrl/Cmd+Z)` : 'Nothing to undo'"
        @click="undoTransformation"
      >
        Undo
      </button>
      <button
        :disabled="!store.canRedo"
        :title="store.redoLabel ? `Redo ${store.redoLabel} (Ctrl/Cmd+Shift+Z or Ctrl/Cmd+Y)` : 'Nothing to redo'"
        @click="redoTransformation"
      >
        Redo
      </button>
      <button
        :class="{ active: showComparison }"
        :disabled="comparisonNodes.length === 0"
        title="Show previous node positions from the last layout run"
        @click="toggleComparison"
      >
        Before/After
      </button>
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
    <ComfyCanvas
      ref="canvasRef"
      class="canvas"
      :comparison-nodes="comparisonNodes"
      :show-comparison="showComparison"
    />
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
.toolbar button.active {
  border-color: #6aa8d8;
  background: #24425c;
  color: #eef7ff;
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

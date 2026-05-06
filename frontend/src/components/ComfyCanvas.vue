<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useWorkflowStore, type ComfyNode as WorkflowNode, type Connection } from '../stores/useWorkflowStore'
import ComfyNode from './ComfyNode.vue'
import ComfyConnection from './ComfyConnection.vue'

const store = useWorkflowStore()
const TITLE_HEIGHT = 26
const SLOT_ROW_HEIGHT = 20
const SLOT_ROW_OFFSET = 8

const canvasRef = ref<HTMLElement | null>(null)
const isDraggingCanvas = ref(false)
const dragStart = ref({ x: 0, y: 0 })
const panStart = ref({ x: 0, y: 0 })
const activePointerId = ref<number | null>(null)

const canvasStyle = computed(() => ({
  transform: `translate(${store.offsetX}px, ${store.offsetY}px) scale(${store.scale})`,
  transformOrigin: '0 0',
}))

function getNodeSize(node: WorkflowNode): [number, number] {
  if (Array.isArray(node.size)) return node.size
  return [node.size?.width ?? 200, node.size?.height ?? 100]
}

function getPortPosition(node: WorkflowNode, portIndex: number, isOutput: boolean): [number, number] {
  const nodeLeft = node.pos[0]
  const portY = node.pos[1] + TITLE_HEIGHT + SLOT_ROW_OFFSET + SLOT_ROW_HEIGHT / 2 + portIndex * SLOT_ROW_HEIGHT
  const nodeSize = getNodeSize(node)
  
  if (isOutput) {
    return [nodeLeft + nodeSize[0], portY]
  } else {
    return [nodeLeft, portY]
  }
}

function getConnectionSourcePos(conn: Connection): [number, number] {
  const sourceNode = store.nodes.find(n => n.id === conn.source)
  if (!sourceNode) return [0, 0]
  return getPortPosition(sourceNode, conn.sourcePort, true)
}

function getConnectionTargetPos(conn: Connection): [number, number] {
  const targetNode = store.nodes.find(n => n.id === conn.target)
  if (!targetNode) return [0, 0]
  return getPortPosition(targetNode, conn.targetPort, false)
}

function onWheel(e: WheelEvent) {
  e.preventDefault()
  const canvas = canvasRef.value
  if (!canvas) return

  const rect = canvas.getBoundingClientRect()
  const cursorX = e.clientX - rect.left
  const cursorY = e.clientY - rect.top
  const previousScale = store.scale
  const zoomFactor = Math.exp(-e.deltaY * 0.0015)
  const newScale = Math.max(0.1, Math.min(3, previousScale * zoomFactor))

  if (newScale === previousScale) return

  const worldX = (cursorX - store.offsetX) / previousScale
  const worldY = (cursorY - store.offsetY) / previousScale
  const nextOffsetX = cursorX - worldX * newScale
  const nextOffsetY = cursorY - worldY * newScale

  store.setView(newScale, nextOffsetX, nextOffsetY)
}

function onCanvasPointerDown(e: PointerEvent) {
  if (e.button !== 0 && e.button !== 1) return
  const target = e.target as HTMLElement
  if (target.closest('.comfy-node') || target.closest('.empty-hint')) return

  e.preventDefault()
  isDraggingCanvas.value = true
  activePointerId.value = e.pointerId
  dragStart.value = { x: e.clientX, y: e.clientY }
  panStart.value = { x: store.offsetX, y: store.offsetY }
  canvasRef.value?.setPointerCapture(e.pointerId)
}

function onPointerMove(e: PointerEvent) {
  if (!isDraggingCanvas.value) return
  if (activePointerId.value !== e.pointerId) return
  
  const dx = e.clientX - dragStart.value.x
  const dy = e.clientY - dragStart.value.y
  store.setView(store.scale, panStart.value.x + dx, panStart.value.y + dy)
}

function onPointerUp(e: PointerEvent) {
  if (activePointerId.value !== e.pointerId) return
  canvasRef.value?.releasePointerCapture(e.pointerId)
  isDraggingCanvas.value = false
  activePointerId.value = null
}

onMounted(() => {
  window.addEventListener('pointermove', onPointerMove)
  window.addEventListener('pointerup', onPointerUp)
  window.addEventListener('pointercancel', onPointerUp)
})

onUnmounted(() => {
  window.removeEventListener('pointermove', onPointerMove)
  window.removeEventListener('pointerup', onPointerUp)
  window.removeEventListener('pointercancel', onPointerUp)
})

function addSampleWorkflow() {
  store.loadWorkflow({
    version: 0.4,
    last_node_id: 3,
    last_link_id: 1,
    nodes: [
      {
        id: 1,
        type: 'LoadImage',
        pos: [100, 100],
        size: [200, 100],
        title: 'Load Image',
        inputs: [],
        outputs: [{ name: 'IMAGE', type: 'IMAGE', links: [1] }]
      },
      {
        id: 2,
        type: 'KSampler',
        pos: [400, 100],
        size: [200, 120],
        title: 'KSampler',
        inputs: [
          { name: 'LATENT', type: 'LATENT' },
          { name: 'MODEL', type: 'MODEL' }
        ],
        outputs: [{ name: 'LATENT', type: 'LATENT', links: [] }]
      },
      {
        id: 3,
        type: 'SaveImage',
        pos: [700, 100],
        size: [200, 80],
        title: 'Save Image',
        inputs: [{ name: 'images', type: 'IMAGE', link: 1 }],
        outputs: []
      }
    ],
    links: [[1, 1, 0, 3, 0, 'IMAGE']],
    groups: [],
  })
}
</script>

<template>
  <div
    ref="canvasRef"
    class="canvas-container"
    :class="{ 'is-panning': isDraggingCanvas }"
    @wheel="onWheel"
    @pointerdown="onCanvasPointerDown"
  >
    <div class="canvas-bg"></div>
    <svg class="connections-svg" :style="{ width: '100%', height: '100%' }">
      <g :style="canvasStyle">
        <ComfyConnection
          v-for="conn in store.connections"
          :key="conn.id"
          :connection="conn"
          :sourcePos="getConnectionSourcePos(conn)"
          :targetPos="getConnectionTargetPos(conn)"
        />
      </g>
    </svg>
    <div class="nodes-container" :style="canvasStyle">
      <ComfyNode
        v-for="node in store.nodes"
        :key="node.id"
        :node="node"
      />
    </div>
    <div v-if="store.nodes.length === 0" class="empty-hint" @click="addSampleWorkflow">
      Click to add sample workflow
    </div>
  </div>
</template>

<style scoped>
.canvas-container {
  position: relative;
  width: 100%;
  height: 100%;
  overflow: hidden;
  background: #1a1a1a;
  cursor: grab;
  touch-action: none;
}
.canvas-container.is-panning {
  cursor: grabbing;
}
.canvas-bg {
  position: absolute;
  inset: 0;
  background: radial-gradient(circle, #2a2a2a 1px, transparent 1px);
  background-size: 20px 20px;
}
.connections-svg {
  position: absolute;
  inset: 0;
  pointer-events: none;
}
.nodes-container {
  position: absolute;
  inset: 0;
  pointer-events: none;
}
:deep(.comfy-node) {
  pointer-events: auto;
}
.empty-hint {
  position: absolute;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  color: #666;
  font-size: 14px;
  padding: 20px 40px;
  border: 2px dashed #444;
  border-radius: 8px;
  cursor: pointer;
}
.empty-hint:hover {
  color: #888;
  border-color: #666;
}
</style>

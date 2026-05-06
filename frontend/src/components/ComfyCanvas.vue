<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useWorkflowStore, type ComfyNode as WorkflowNode, type Connection } from '../stores/useWorkflowStore'
import ComfyNode from './ComfyNode.vue'
import ComfyConnection from './ComfyConnection.vue'

const store = useWorkflowStore()

const canvasRef = ref<HTMLElement | null>(null)
const isDraggingCanvas = ref(false)
const dragStart = ref({ x: 0, y: 0 })
const panStart = ref({ x: 0, y: 0 })

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
  const nodeTop = node.pos[1] + 30
  const portYOffset = nodeTop + 20
  const nodeSize = getNodeSize(node)
  
  if (isOutput) {
    return [nodeLeft + nodeSize[0], portYOffset + portIndex * 18]
  } else {
    return [nodeLeft, portYOffset + portIndex * 18]
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
  const delta = -e.deltaY * 0.001
  const newScale = Math.max(0.1, Math.min(3, store.scale + delta))
  store.setView(newScale, store.offsetX, store.offsetY)
}

function onCanvasMouseDown(e: MouseEvent) {
  if (e.button !== 0) return
  if (e.target !== canvasRef.value && !(e.target as HTMLElement).classList?.contains('canvas-bg')) return
  
  isDraggingCanvas.value = true
  dragStart.value = { x: e.clientX, y: e.clientY }
  panStart.value = { x: store.offsetX, y: store.offsetY }
}

function onMouseMove(e: MouseEvent) {
  if (!isDraggingCanvas.value) return
  
  const dx = e.clientX - dragStart.value.x
  const dy = e.clientY - dragStart.value.y
  store.setView(store.scale, panStart.value.x + dx, panStart.value.y + dy)
}

function onMouseUp() {
  isDraggingCanvas.value = false
}

onMounted(() => {
  window.addEventListener('mousemove', onMouseMove)
  window.addEventListener('mouseup', onMouseUp)
})

onUnmounted(() => {
  window.removeEventListener('mousemove', onMouseMove)
  window.removeEventListener('mouseup', onMouseUp)
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
  <div ref="canvasRef" class="canvas-container" @wheel="onWheel" @mousedown="onCanvasMouseDown">
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
}
.canvas-container:active {
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

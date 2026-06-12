<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, watch, nextTick } from 'vue'
import { useWorkflowStore, type ComfyNode as WorkflowNode, type ComfyGroup, type Connection } from '../stores/useWorkflowStore'
import { TITLE_HEIGHT, ROW_HEIGHT, SLOT_ROW_OFFSET, REROUTE_SLOT_OFFSET, getNodeDisplaySize, getNodeInputPortY } from '../utils/nodeGeometry'
import { getVisibleInputPortIndexes } from '../utils/nodeWidgets'
import ComfyNode from './ComfyNode.vue'
import ComfyConnection from './ComfyConnection.vue'

const store = useWorkflowStore()
const props = withDefaults(defineProps<{
  comparisonNodes?: WorkflowNode[]
  showComparison?: boolean
}>(), {
  comparisonNodes: () => [],
  showComparison: false,
})
const PORT_CENTER_OFFSET = 12
const GROUP_CONTENT_PADDING = 24
const GROUP_HEADER_CLEARANCE = 36
const MIN_GROUP_WIDTH = 120
const MIN_GROUP_HEIGHT = 80
const GROUP_CREATE_MIN_SIZE = 24
const resizeHandles = ['nw', 'n', 'ne', 'e', 'se', 's', 'sw', 'w'] as const
type ResizeHandle = (typeof resizeHandles)[number]

const canvasRef = ref<HTMLElement | null>(null)
const minimapRef = ref<HTMLElement | null>(null)
const minimapBodyRef = ref<HTMLElement | null>(null)
const isDraggingCanvas = ref(false)
const isCreatingGroup = ref(false)
const dragStart = ref({ x: 0, y: 0 })
const panStart = ref({ x: 0, y: 0 })
const activePointerId = ref<number | null>(null)
const isDraggingMinimap = ref(false)
const minimapSize = ref({ width: 0, height: 0 })
const activeGroupCreate = ref<{
  pointerId: number
  startX: number
  startY: number
  currentX: number
  currentY: number
  handle: HTMLElement | null
} | null>(null)
const activeGroupDrag = ref<{
  groupId: number | string
  pointerId: number
  lastX: number
  lastY: number
  nodeIds: Array<number | string>
  handle: HTMLElement | null
} | null>(null)
const activeGroupResize = ref<{
  groupId: number | string
  pointerId: number
  handleName: ResizeHandle
  startX: number
  startY: number
  startBounding: [number, number, number, number]
  contentBounds: [number, number, number, number] | null
  handle: HTMLElement | null
} | null>(null)
const minimapContent = computed(() => {
  const nodes = store.nodes
  if (nodes.length === 0) {
    return { minX: 0, minY: 0, width: 1, height: 1 }
  }

  let minX = Number.POSITIVE_INFINITY
  let minY = Number.POSITIVE_INFINITY
  let maxX = Number.NEGATIVE_INFINITY
  let maxY = Number.NEGATIVE_INFINITY

  for (const node of nodes) {
    const [width, height] = getNodeDisplaySize(node)
    minX = Math.min(minX, node.pos[0])
    minY = Math.min(minY, node.pos[1])
    maxX = Math.max(maxX, node.pos[0] + width)
    maxY = Math.max(maxY, node.pos[1] + height)
  }

  for (const group of groups.value) {
    const [x, y, width, height] = getGroupVisualBounds(group)
    minX = Math.min(minX, x)
    minY = Math.min(minY, y)
    maxX = Math.max(maxX, x + width)
    maxY = Math.max(maxY, y + height)
  }

  const padding = 80
  return {
    minX: minX - padding,
    minY: minY - padding,
    width: Math.max(1, maxX - minX + padding * 2),
    height: Math.max(1, maxY - minY + padding * 2),
  }
})
const minimapScale = computed(() => {
  const size = minimapSize.value
  if (size.width <= 0 || size.height <= 0) return 1
  const content = minimapContent.value
  return Math.min(size.width / content.width, size.height / content.height)
})
const minimapOffset = computed(() => {
  const content = minimapContent.value
  const size = minimapSize.value
  const scale = minimapScale.value
  return {
    x: (size.width - content.width * scale) / 2,
    y: (size.height - content.height * scale) / 2,
  }
})
const groups = computed(() => store.workflow?.groups ?? [])

const canvasStyle = computed(() => ({
  transform: `translate(${store.offsetX}px, ${store.offsetY}px) scale(${store.scale})`,
  transformOrigin: '0 0',
}))
const viewportStyle = computed(() => {
  const canvas = canvasRef.value
  if (!canvas) {
    return { display: 'none' }
  }

  const content = minimapContent.value
  const scale = minimapScale.value
  const offset = minimapOffset.value
  const viewportWidth = canvas.clientWidth / store.scale
  const viewportHeight = canvas.clientHeight / store.scale
  const left = (-store.offsetX / store.scale - content.minX) * scale + offset.x
  const top = (-store.offsetY / store.scale - content.minY) * scale + offset.y
  return {
    left: `${left}px`,
    top: `${top}px`,
    width: `${viewportWidth * scale}px`,
    height: `${viewportHeight * scale}px`,
  }
})
const minimapNodeStyle = computed(() => (node: WorkflowNode) => {
  const [width, height] = getNodeDisplaySize(node)
  const content = minimapContent.value
  const scale = minimapScale.value
  const offset = minimapOffset.value
  return {
    left: `${(node.pos[0] - content.minX) * scale + offset.x}px`,
    top: `${(node.pos[1] - content.minY) * scale + offset.y}px`,
    width: `${Math.max(6, width * scale)}px`,
    height: `${Math.max(6, height * scale)}px`,
  }
})
const comparisonNodeStyle = computed(() => (node: WorkflowNode) => {
  const [width, height] = getNodeDisplaySize(node)
  return {
    left: `${node.pos[0]}px`,
    top: `${node.pos[1]}px`,
    width: `${width}px`,
    height: `${height}px`,
  }
})

function worldToMinimapPoint(x: number, y: number): [number, number] {
  const content = minimapContent.value
  const scale = minimapScale.value
  const offset = minimapOffset.value
  return [
    (x - content.minX) * scale + offset.x,
    (y - content.minY) * scale + offset.y,
  ]
}

function getMinimapConnectionStyle(conn: Connection) {
  const sourceNode = store.nodes.find((node) => node.id === conn.source)
  const targetNode = store.nodes.find((node) => node.id === conn.target)
  if (!sourceNode || !targetNode) {
    return { x1: 0, y1: 0, x2: 0, y2: 0 }
  }

  const [sourceX, sourceY] = getMinimapNodeEdgePoint(sourceNode, targetNode)
  const [targetX, targetY] = getMinimapNodeEdgePoint(targetNode, sourceNode)
  const [x1, y1] = worldToMinimapPoint(sourceX, sourceY)
  const [x2, y2] = worldToMinimapPoint(targetX, targetY)
  return { x1, y1, x2, y2 }
}

function getMinimapNodeEdgePoint(node: WorkflowNode, targetNode: WorkflowNode): [number, number] {
  const [width, height] = getNodeSize(node)
  const centerX = node.pos[0] + width / 2
  const centerY = node.pos[1] + height / 2
  const [targetWidth, targetHeight] = getNodeSize(targetNode)
  const targetCenterX = targetNode.pos[0] + targetWidth / 2
  const targetCenterY = targetNode.pos[1] + targetHeight / 2
  const dx = targetCenterX - centerX
  const dy = targetCenterY - centerY

  if (dx === 0 && dy === 0) {
    return [centerX, centerY]
  }

  const xScale = dx === 0 ? Number.POSITIVE_INFINITY : (width / 2) / Math.abs(dx)
  const yScale = dy === 0 ? Number.POSITIVE_INFINITY : (height / 2) / Math.abs(dy)
  const edgeScale = Math.min(xScale, yScale)

  return [
    centerX + dx * edgeScale,
    centerY + dy * edgeScale,
  ]
}

function getNodeSize(node: WorkflowNode): [number, number] {
  if (Array.isArray(node.size)) return node.size
  return [node.size?.width ?? 200, node.size?.height ?? 100]
}

function getNodeBounds(node: WorkflowNode): [number, number, number, number] {
  const [width, height] = getNodeDisplaySize(node)
  return [node.pos[0], node.pos[1], width, height]
}

function isNodeInsideGroup(node: WorkflowNode, group: ComfyGroup): boolean {
  const bounds = group.bounding
  if (!bounds) return false
  const [x, y] = getNodeBounds(node)
  const [gx, gy, gw, gh] = bounds
  return x >= gx && y >= gy && x <= gx + gw && y <= gy + gh
}

function getGroupStyle(group: ComfyGroup) {
  const [x, y, width, height] = getGroupVisualBounds(group)
  return {
    left: `${x}px`,
    top: `${y}px`,
    width: `${Math.max(0, width)}px`,
    height: `${Math.max(0, height)}px`,
    borderColor: group.color ?? '#3f789e',
  }
}

function getGroupTitleStyle(group: ComfyGroup) {
  return {
    color: group.color ?? '#3f789e',
    fontSize: `${group.font_size ?? 24}px`,
  }
}

function getGroupVisualBounds(group: ComfyGroup): [number, number, number, number] {
  const bounds = group.bounding ?? [0, 0, 0, 0]
  let [minX, minY, width, height] = bounds
  let maxX = minX + width
  let maxY = minY + height

  for (const node of getNodesInGroup(group)) {
    const [x, y, nodeWidth, nodeHeight] = getNodeBounds(node)
    minX = Math.min(minX, x)
    minY = Math.min(minY, y - GROUP_HEADER_CLEARANCE)
    maxX = Math.max(maxX, x + nodeWidth)
    maxY = Math.max(maxY, y + nodeHeight)
  }

  return [minX, minY, maxX - minX, maxY - minY]
}

function getGroupTitle(group: ComfyGroup): string {
  return group.title?.trim() || `Group ${group.id}`
}

function getNodesInGroup(group: ComfyGroup) {
  const bounds = group.bounding
  if (!bounds) return []
  return store.nodes.filter((node) => isNodeInsideGroup(node, group))
}

function getGroupContentBounds(group: ComfyGroup): [number, number, number, number] | null {
  const nodes = getNodesInGroup(group)
  if (nodes.length === 0) return null

  let minX = Number.POSITIVE_INFINITY
  let minY = Number.POSITIVE_INFINITY
  let maxX = Number.NEGATIVE_INFINITY
  let maxY = Number.NEGATIVE_INFINITY

  for (const node of nodes) {
    const [x, y, width, height] = getNodeBounds(node)
    minX = Math.min(minX, x)
    minY = Math.min(minY, y)
    maxX = Math.max(maxX, x + width)
    maxY = Math.max(maxY, y + height)
  }

  return [minX, minY, maxX, maxY]
}

function getCanvasWorldPoint(clientX: number, clientY: number): { x: number; y: number } | null {
  const canvas = canvasRef.value
  if (!canvas || store.scale <= 0) return null

  const box = canvas.getBoundingClientRect()
  return {
    x: (clientX - box.left - store.offsetX) / store.scale,
    y: (clientY - box.top - store.offsetY) / store.scale,
  }
}

function beginGroupCreation() {
  if (!store.workflow) return
  if (isCreatingGroup.value || activeGroupCreate.value) {
    cancelGroupCreation()
    return
  }
  isCreatingGroup.value = true
}

function cancelGroupCreation() {
  isCreatingGroup.value = false
  activeGroupCreate.value = null
}

function finalizeGroupCreation() {
  const draft = activeGroupCreate.value
  if (!draft) return

  const left = Math.min(draft.startX, draft.currentX)
  const top = Math.min(draft.startY, draft.currentY)
  const width = Math.abs(draft.currentX - draft.startX)
  const height = Math.abs(draft.currentY - draft.startY)
  const handle = draft.handle
  if (handle?.hasPointerCapture(draft.pointerId)) {
    handle.releasePointerCapture(draft.pointerId)
  }

  activeGroupCreate.value = null
  isCreatingGroup.value = false

  if (width < GROUP_CREATE_MIN_SIZE || height < GROUP_CREATE_MIN_SIZE) {
    return
  }

  const defaultTitle = `Group ${groups.value.length + 1}`
  const title = window.prompt('Group title', defaultTitle)
  if (title === null) {
    return
  }

  store.createGroup([left, top, width, height], title.trim() || defaultTitle)
}

function deleteAllGroups() {
  if (!groups.value.length) return
  if (!window.confirm('Delete all groups?')) return
  store.deleteAllGroups()
}

function deleteGroup(groupId: number | string) {
  store.deleteGroup(groupId)
}

function renameGroup(group: ComfyGroup) {
  const currentTitle = getGroupTitle(group)
  const title = window.prompt('Group title', currentTitle)
  if (title === null) return
  store.renameGroup(group.id, title.trim() || currentTitle)
}

function isGroupPinned(group: ComfyGroup): boolean {
  return Boolean(group.flags && group.flags.pinned === true)
}

function toggleGroupPinned(group: ComfyGroup) {
  store.toggleGroupPinned(group.id)
}

function unpinAll() {
  store.unpinAll()
}

function getPortPosition(node: WorkflowNode, portIndex: number, isOutput: boolean): [number, number] {
  const nodeLeft = node.pos[0]
  const baseOffset = typeof node.type === 'string' && node.type.toLowerCase().includes('reroute') ? REROUTE_SLOT_OFFSET : SLOT_ROW_OFFSET
  const portY = isOutput
    ? node.pos[1] + TITLE_HEIGHT + baseOffset + ROW_HEIGHT / 2 + portIndex * ROW_HEIGHT
    : node.pos[1] + getNodeInputPortY(node, portIndex)
  const nodeSize = getNodeSize(node)
  
  if (isOutput) {
    return [nodeLeft + nodeSize[0] - PORT_CENTER_OFFSET, portY]
  } else {
    return [nodeLeft + PORT_CENTER_OFFSET, portY]
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

function getPortType(port: { type?: unknown }): string {
  return typeof port.type === 'string' ? port.type : ''
}

function getNodePorts(node: WorkflowNode) {
  return {
    inputs: getVisibleInputPortIndexes(node).map((index) => {
      const input = (node.inputs ?? [])[index]
      const [x, y] = getPortPosition(node, index, false)
      return {
        key: `input-${node.id}-${index}`,
        x,
        y,
        color: slotColor(getPortType(input)),
      }
    }),
    outputs: (node.outputs ?? []).map((output, index) => {
      const [x, y] = getPortPosition(node, index, true)
      return {
        key: `output-${node.id}-${index}`,
        x,
        y,
        color: slotColor(getPortType(output)),
      }
    }),
  }
}

function onGroupPointerDown(e: PointerEvent, group: ComfyGroup) {
  if (e.button !== 0) return
  e.preventDefault()
  e.stopPropagation()

  const handle = e.currentTarget as HTMLElement | null
  const nodeIds = getNodesInGroup(group).map((node) => node.id)
  if (!handle) return

  activeGroupDrag.value = {
    groupId: group.id,
    pointerId: e.pointerId,
    lastX: e.clientX,
    lastY: e.clientY,
    nodeIds,
    handle,
  }
  handle.setPointerCapture(e.pointerId)
}

function onGroupResizePointerDown(e: PointerEvent, group: ComfyGroup, handleName: ResizeHandle) {
  if (e.button !== 0 || !Array.isArray(group.bounding)) return
  e.preventDefault()
  e.stopPropagation()

  const handle = e.currentTarget as HTMLElement | null
  activeGroupResize.value = {
    groupId: group.id,
    pointerId: e.pointerId,
    handleName,
    startX: e.clientX,
    startY: e.clientY,
    startBounding: [...group.bounding],
    contentBounds: getGroupContentBounds(group),
    handle,
  }
  handle?.setPointerCapture(e.pointerId)
}

function onGroupPointerMove(e: PointerEvent) {
  const drag = activeGroupDrag.value
  if (!drag || drag.pointerId !== e.pointerId) return

  const dx = (e.clientX - drag.lastX) / store.scale
  const dy = (e.clientY - drag.lastY) / store.scale
  if (dx === 0 && dy === 0) return

  store.moveGroupByDelta(drag.groupId, dx, dy, drag.nodeIds)
  drag.lastX = e.clientX
  drag.lastY = e.clientY
}

function onGroupResizePointerMove(e: PointerEvent) {
  const resize = activeGroupResize.value
  if (!resize || resize.pointerId !== e.pointerId) return

  const dx = (e.clientX - resize.startX) / store.scale
  const dy = (e.clientY - resize.startY) / store.scale
  const [startX, startY, startWidth, startHeight] = resize.startBounding
  let left = startX
  let top = startY
  let right = startX + startWidth
  let bottom = startY + startHeight

  if (resize.handleName.includes('w')) left += dx
  if (resize.handleName.includes('e')) right += dx
  if (resize.handleName.includes('n')) top += dy
  if (resize.handleName.includes('s')) bottom += dy

  const minBounds = resize.contentBounds
  if (minBounds) {
    const [contentLeft, contentTop, contentRight, contentBottom] = minBounds
    left = Math.min(left, contentLeft - GROUP_CONTENT_PADDING)
    top = Math.min(top, contentTop - GROUP_HEADER_CLEARANCE)
    right = Math.max(right, contentRight + GROUP_CONTENT_PADDING)
    bottom = Math.max(bottom, contentBottom + GROUP_CONTENT_PADDING)
  }

  if (right - left < MIN_GROUP_WIDTH) {
    if (resize.handleName.includes('w')) left = right - MIN_GROUP_WIDTH
    else right = left + MIN_GROUP_WIDTH
  }
  if (bottom - top < MIN_GROUP_HEIGHT) {
    if (resize.handleName.includes('n')) top = bottom - MIN_GROUP_HEIGHT
    else bottom = top + MIN_GROUP_HEIGHT
  }

  store.resizeGroup(resize.groupId, [left, top, right - left, bottom - top])
}

function onGroupPointerUp(e: PointerEvent) {
  const drag = activeGroupDrag.value
  if (!drag || drag.pointerId !== e.pointerId) return

  const handle = drag.handle
  if (handle?.hasPointerCapture(e.pointerId)) {
    handle.releasePointerCapture(e.pointerId)
  }
  activeGroupDrag.value = null
}

function onGroupResizePointerUp(e: PointerEvent) {
  const resize = activeGroupResize.value
  if (!resize || resize.pointerId !== e.pointerId) return

  const handle = resize.handle
  if (handle?.hasPointerCapture(e.pointerId)) {
    handle.releasePointerCapture(e.pointerId)
  }
  activeGroupResize.value = null
}

function slotColor(type: string | undefined): string {
  switch (type) {
    case 'MODEL':
      return '#b39ddb'
    case 'CLIP':
      return '#ffd166'
    case 'VAE':
      return '#f78c6c'
    case 'CONDITIONING':
      return '#ffa6d9'
    case 'LATENT':
      return '#ff9f43'
    case 'IMAGE':
      return '#64b5f6'
    case 'MASK':
      return '#81c784'
    default:
      return '#9a9a9a'
  }
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

function syncMinimapSize() {
  const body = minimapBodyRef.value
  if (!body) return
  minimapSize.value = {
    width: body.clientWidth,
    height: body.clientHeight,
  }
}

function fitWorkflowToCanvas() {
  const canvas = canvasRef.value
  const nodes = store.nodes
  if (!canvas || (nodes.length === 0 && groups.value.length === 0)) return

  let minX = Number.POSITIVE_INFINITY
  let minY = Number.POSITIVE_INFINITY
  let maxX = Number.NEGATIVE_INFINITY
  let maxY = Number.NEGATIVE_INFINITY

  for (const node of nodes) {
    const [width, height] = getNodeSize(node)
    minX = Math.min(minX, node.pos[0])
    minY = Math.min(minY, node.pos[1])
    maxX = Math.max(maxX, node.pos[0] + width)
    maxY = Math.max(maxY, node.pos[1] + height)
  }

  for (const group of groups.value) {
    const [x, y, width, height] = getGroupVisualBounds(group)
    minX = Math.min(minX, x)
    minY = Math.min(minY, y)
    maxX = Math.max(maxX, x + width)
    maxY = Math.max(maxY, y + height)
  }

  const padding = 120
  const contentWidth = Math.max(1, maxX - minX + padding * 2)
  const contentHeight = Math.max(1, maxY - minY + padding * 2)
  const scale = Math.min(
    1.2,
    Math.max(0.2, Math.min(canvas.clientWidth / contentWidth, canvas.clientHeight / contentHeight)),
  )
  const centerX = minX + (maxX - minX) / 2
  const centerY = minY + (maxY - minY) / 2
  const offsetX = canvas.clientWidth / 2 - centerX * scale
  const offsetY = canvas.clientHeight / 2 - centerY * scale

  store.setView(scale, offsetX, offsetY)
}

function onCanvasPointerDown(e: PointerEvent) {
  if (e.button !== 0 && e.button !== 1) return
  const target = e.target as HTMLElement
  if (target.closest('.comfy-node') || target.closest('.minimap')) return

  if (isCreatingGroup.value) {
    const point = getCanvasWorldPoint(e.clientX, e.clientY)
    if (!point) return

    e.preventDefault()
    e.stopPropagation()
    const handle = e.currentTarget as HTMLElement | null
    activeGroupCreate.value = {
      pointerId: e.pointerId,
      startX: point.x,
      startY: point.y,
      currentX: point.x,
      currentY: point.y,
      handle,
    }
    handle?.setPointerCapture(e.pointerId)
    return
  }

  e.preventDefault()
  isDraggingCanvas.value = true
  activePointerId.value = e.pointerId
  dragStart.value = { x: e.clientX, y: e.clientY }
  panStart.value = { x: store.offsetX, y: store.offsetY }
  canvasRef.value?.setPointerCapture(e.pointerId)
}

function onPointerMove(e: PointerEvent) {
  if (activeGroupCreate.value && activeGroupCreate.value.pointerId === e.pointerId) {
    const point = getCanvasWorldPoint(e.clientX, e.clientY)
    if (!point) return
    activeGroupCreate.value.currentX = point.x
    activeGroupCreate.value.currentY = point.y
    return
  }
  if (activeGroupResize.value && activeGroupResize.value.pointerId === e.pointerId) {
    onGroupResizePointerMove(e)
    return
  }
  if (activeGroupDrag.value && activeGroupDrag.value.pointerId === e.pointerId) {
    onGroupPointerMove(e)
    return
  }
  if (!isDraggingCanvas.value) return
  if (activePointerId.value !== e.pointerId) return
  
  const dx = e.clientX - dragStart.value.x
  const dy = e.clientY - dragStart.value.y
  store.setView(store.scale, panStart.value.x + dx, panStart.value.y + dy)
}

function onPointerUp(e: PointerEvent) {
  if (activeGroupCreate.value && activeGroupCreate.value.pointerId === e.pointerId) {
    finalizeGroupCreation()
    return
  }
  if (activeGroupResize.value && activeGroupResize.value.pointerId === e.pointerId) {
    onGroupResizePointerUp(e)
    return
  }
  if (activeGroupDrag.value && activeGroupDrag.value.pointerId === e.pointerId) {
    onGroupPointerUp(e)
    return
  }
  if (activePointerId.value !== e.pointerId) return
  canvasRef.value?.releasePointerCapture(e.pointerId)
  isDraggingCanvas.value = false
  activePointerId.value = null
}

function minimapPointToWorld(clientX: number, clientY: number): { x: number; y: number } | null {
  const box = minimapBodyRef.value?.getBoundingClientRect()
  if (!box) return null

  const content = minimapContent.value
  const scale = minimapScale.value
  const offset = minimapOffset.value
  const x = (clientX - box.left - offset.x) / scale + content.minX
  const y = (clientY - box.top - offset.y) / scale + content.minY
  return { x, y }
}

function centerViewOnWorldPoint(x: number, y: number) {
  const canvas = canvasRef.value
  if (!canvas) return

  const targetOffsetX = canvas.clientWidth / 2 - x * store.scale
  const targetOffsetY = canvas.clientHeight / 2 - y * store.scale
  store.setView(store.scale, targetOffsetX, targetOffsetY)
}

function onMinimapPointerDown(e: PointerEvent) {
  const target = minimapPointToWorld(e.clientX, e.clientY)
  if (!target) return

  e.preventDefault()
  isDraggingMinimap.value = true
  minimapBodyRef.value?.setPointerCapture(e.pointerId)
  centerViewOnWorldPoint(target.x, target.y)
}

function onMinimapPointerMove(e: PointerEvent) {
  if (!isDraggingMinimap.value) return
  const target = minimapPointToWorld(e.clientX, e.clientY)
  if (!target) return
  centerViewOnWorldPoint(target.x, target.y)
}

function onMinimapPointerUp(e: PointerEvent) {
  if (!isDraggingMinimap.value) return
  minimapBodyRef.value?.releasePointerCapture(e.pointerId)
  isDraggingMinimap.value = false
}

function onKeyDown(e: KeyboardEvent) {
  if (e.key === 'Escape' && (isCreatingGroup.value || activeGroupCreate.value)) {
    cancelGroupCreation()
  }
}

defineExpose({
  beginGroupCreation,
  cancelGroupCreation,
  deleteAllGroups,
})

onMounted(() => {
  syncMinimapSize()
  window.addEventListener('pointermove', onPointerMove)
  window.addEventListener('pointerup', onPointerUp)
  window.addEventListener('pointercancel', onPointerUp)
  window.addEventListener('keydown', onKeyDown)
})

onUnmounted(() => {
  window.removeEventListener('pointermove', onPointerMove)
  window.removeEventListener('pointerup', onPointerUp)
  window.removeEventListener('pointercancel', onPointerUp)
  window.removeEventListener('keydown', onKeyDown)
})

watch(
  () => store.nodes.length,
  async () => {
    await nextTick()
    syncMinimapSize()
  },
  { immediate: true },
)

watch(
  () => store.workflow,
  async () => {
    await nextTick()
    fitWorkflowToCanvas()
    syncMinimapSize()
  },
  { immediate: true },
)

</script>

<template>
  <div
    ref="canvasRef"
    class="canvas-container"
    :class="{ 'is-panning': isDraggingCanvas, 'is-creating-group': isCreatingGroup }"
    @wheel="onWheel"
    @pointerdown="onCanvasPointerDown"
  >
    <div class="canvas-bg"></div>
    <div class="group-toolbar">
      <button
        type="button"
        title="Create group by dragging"
        @pointerdown.stop
        @click="beginGroupCreation"
      >
        +
      </button>
      <button
        type="button"
        class="toolbar-pin-button"
        title="Unpin all nodes and groups"
        :disabled="!store.hasPinnedItems"
        @pointerdown.stop
        @click="unpinAll"
      >
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <path class="pin-shadow" d="M8.2 15.3 3.6 20c-.4.4-.4 1 0 1.4s1 .4 1.4 0l4.7-4.6z" />
          <path class="pin-needle" d="M8.2 15.3 3.6 20c-.4.4-.4 1 0 1.4s1 .4 1.4 0l4.7-4.6z" />
          <path class="pin-body" d="M7.2 6.5c-.9 0-1.6.7-1.6 1.6 0 .4.2.8.5 1.1l2.4 2.4-2.3 2.3c-.5.5-.5 1.4 0 1.9l2 2c.5.5 1.4.5 1.9 0l2.3-2.3 2.4 2.4c.3.3.7.5 1.1.5.9 0 1.6-.7 1.6-1.6v-4.1l3.2-3.2c.5-.5.5-1.4 0-1.9l-4.3-4.3c-.5-.5-1.4-.5-1.9 0l-3.2 3.2z" />
          <path class="pin-highlight" d="M7.4 7.8c-.3 0-.5.2-.5.5 0 .1.1.2.1.3l1.9 1.9h6.7l3.2-3.2-2.9-2.9-3.2 3.2z" />
          <path class="pin-slash" d="M4 4l16 16" />
        </svg>
      </button>
      <button
        type="button"
        title="Delete all groups"
        :disabled="groups.length === 0"
        @pointerdown.stop
        @click="deleteAllGroups"
      >
        ×
      </button>
    </div>
    <div class="groups-layer" :style="canvasStyle">
      <div
        v-if="activeGroupCreate"
        class="group-create-preview"
        :style="{
          left: `${Math.min(activeGroupCreate.startX, activeGroupCreate.currentX)}px`,
          top: `${Math.min(activeGroupCreate.startY, activeGroupCreate.currentY)}px`,
          width: `${Math.abs(activeGroupCreate.currentX - activeGroupCreate.startX)}px`,
          height: `${Math.abs(activeGroupCreate.currentY - activeGroupCreate.startY)}px`,
        }"
      ></div>
      <div
        v-for="group in groups"
        :key="`group-${group.id}`"
        class="workflow-group"
        :class="{ 'is-pinned': isGroupPinned(group) }"
        :style="getGroupStyle(group)"
      >
        <div
          class="workflow-group-title"
          :style="getGroupTitleStyle(group)"
          @pointerdown="onGroupPointerDown($event, group)"
        >
          <span class="workflow-group-title-text">{{ getGroupTitle(group) }}</span>
          <button
            type="button"
            class="workflow-group-rename-button"
            title="Rename group"
            @pointerdown.stop
            @click.stop="renameGroup(group)"
          >
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path d="M5 17.5V20h2.5L18.1 9.4l-2.5-2.5L5 17.5z" />
              <path d="M17 5.5 18.5 4a1.4 1.4 0 0 1 2 2L19 7.5z" />
            </svg>
          </button>
          <button
            type="button"
            class="workflow-group-pin-button"
            :class="{ active: isGroupPinned(group) }"
            :title="isGroupPinned(group) ? 'Unpin group' : 'Pin group'"
            @pointerdown.stop
            @click.stop="toggleGroupPinned(group)"
          >
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path class="pin-shadow" d="M8.2 15.3 3.6 20c-.4.4-.4 1 0 1.4s1 .4 1.4 0l4.7-4.6z" />
              <path class="pin-needle" d="M8.2 15.3 3.6 20c-.4.4-.4 1 0 1.4s1 .4 1.4 0l4.7-4.6z" />
              <path class="pin-body" d="M7.2 6.5c-.9 0-1.6.7-1.6 1.6 0 .4.2.8.5 1.1l2.4 2.4-2.3 2.3c-.5.5-.5 1.4 0 1.9l2 2c.5.5 1.4.5 1.9 0l2.3-2.3 2.4 2.4c.3.3.7.5 1.1.5.9 0 1.6-.7 1.6-1.6v-4.1l3.2-3.2c.5-.5.5-1.4 0-1.9l-4.3-4.3c-.5-.5-1.4-.5-1.9 0l-3.2 3.2z" />
              <path class="pin-highlight" d="M7.4 7.8c-.3 0-.5.2-.5.5 0 .1.1.2.1.3l1.9 1.9h6.7l3.2-3.2-2.9-2.9-3.2 3.2z" />
            </svg>
          </button>
          <button
            type="button"
            class="workflow-group-delete-button"
            title="Delete group"
            @pointerdown.stop
            @click.stop="deleteGroup(group.id)"
          >
            ×
          </button>
        </div>
        <div
          v-for="handle in resizeHandles"
          :key="`group-${group.id}-resize-${handle}`"
          class="group-resize-handle"
          :class="`resize-${handle}`"
          @pointerdown="onGroupResizePointerDown($event, group, handle)"
        ></div>
      </div>
    </div>
    <div v-if="props.showComparison" class="comparison-layer" :style="canvasStyle">
      <div
        v-for="node in props.comparisonNodes"
        :key="`compare-${node.id}`"
        class="comparison-node"
        :class="{ 'is-reroute': typeof node.type === 'string' && node.type.toLowerCase().includes('reroute') }"
        :style="comparisonNodeStyle(node)"
      ></div>
    </div>
    <div class="nodes-container" :style="canvasStyle">
      <ComfyNode
        v-for="node in store.nodes"
        :key="node.id"
        :node="node"
      />
    </div>
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
    <div class="ports-overlay" :style="canvasStyle">
      <div
        v-for="node in store.nodes"
        :key="`ports-${node.id}`"
        class="ports-layer"
      >
        <div
          v-for="port in getNodePorts(node).inputs"
          :key="port.key"
          class="port-dot port-dot-input"
          :style="{ left: `${port.x}px`, top: `${port.y}px`, backgroundColor: port.color }"
        />
        <div
          v-for="port in getNodePorts(node).outputs"
          :key="port.key"
          class="port-dot port-dot-output"
          :style="{ left: `${port.x}px`, top: `${port.y}px`, backgroundColor: port.color }"
        />
      </div>
    </div>
    <div
      v-if="store.nodes.length > 0"
      ref="minimapRef"
      class="minimap"
      :class="{ 'is-dragging': isDraggingMinimap }"
    >
      <div class="minimap-header">Map</div>
      <div
        ref="minimapBodyRef"
        class="minimap-body"
        @pointerdown="onMinimapPointerDown"
        @pointermove="onMinimapPointerMove"
        @pointerup="onMinimapPointerUp"
        @pointercancel="onMinimapPointerUp"
      >
        <svg class="minimap-links" aria-hidden="true">
          <line
          v-for="conn in store.connections"
          :key="`mini-link-${conn.id}`"
          v-bind="getMinimapConnectionStyle(conn)"
          />
        </svg>
        <div
          v-for="node in store.nodes"
          :key="`mini-${node.id}`"
          class="minimap-node"
          :class="{ 'is-collapsed': Boolean(node.flags && typeof node.flags === 'object' && 'collapsed' in node.flags && node.flags.collapsed), 'is-reroute': typeof node.type === 'string' && node.type.toLowerCase().includes('reroute') }"
          :style="minimapNodeStyle(node)"
        ></div>
        <div class="minimap-viewport" :style="viewportStyle"></div>
      </div>
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
.canvas-container.is-creating-group {
  cursor: crosshair;
}
.canvas-bg {
  position: absolute;
  inset: 0;
  background: radial-gradient(circle, #2a2a2a 1px, transparent 1px);
  background-size: 20px 20px;
}
.group-toolbar {
  position: absolute;
  top: 10px;
  left: 10px;
  display: flex;
  gap: 6px;
  z-index: 6;
  pointer-events: auto;
}
.group-toolbar button,
.workflow-group-rename-button,
.workflow-group-delete-button,
.workflow-group-pin-button {
  width: 28px;
  height: 28px;
  padding: 0;
  border: 1px solid #555;
  border-radius: 4px;
  background: #252525;
  color: #ddd;
  cursor: pointer;
}
.group-toolbar button:hover:not(:disabled),
.workflow-group-rename-button:hover,
.workflow-group-delete-button:hover,
.workflow-group-pin-button:hover,
.workflow-group-pin-button.active {
  background: #3a3a3a;
}
.toolbar-pin-button svg,
.workflow-group-rename-button svg,
.workflow-group-pin-button svg {
  display: block;
  width: 18px;
  height: 18px;
  margin: 4px auto;
}
.workflow-group-rename-button svg {
  fill: #cfcfcf;
}
.toolbar-pin-button svg,
.workflow-group-pin-button svg {
  transform: rotate(-42deg);
}
.toolbar-pin-button .pin-slash {
  fill: none;
  stroke: #f0f0f0;
  stroke-linecap: round;
  stroke-width: 2.4;
}
.pin-body {
  fill: #8a8a8a;
}
.pin-highlight {
  fill: #b8b8b8;
}
.pin-needle {
  fill: #9a9a9a;
}
.pin-shadow {
  fill: rgb(0 0 0 / 45%);
  transform: translate(1px, 1px);
}
.workflow-group-pin-button {
  flex: 0 0 auto;
}
.workflow-group-pin-button.active {
  border-color: rgb(255 92 140 / 65%);
}
.workflow-group-pin-button.active .pin-body,
.toolbar-pin-button:not(:disabled) .pin-body {
  fill: #e92866;
}
.workflow-group-pin-button.active .pin-highlight,
.toolbar-pin-button:not(:disabled) .pin-highlight {
  fill: #ff5f8d;
}
.workflow-group-pin-button.active .pin-needle,
.toolbar-pin-button:not(:disabled) .pin-needle {
  fill: #b8b8b8;
}
.group-toolbar button:disabled {
  cursor: default;
  opacity: 0.45;
}
.group-create-preview {
  position: absolute;
  box-sizing: border-box;
  border: 1px dashed rgb(180 220 255 / 90%);
  background: rgb(90 150 210 / 12%);
  border-radius: 2px;
  pointer-events: none;
  z-index: 5;
}
.nodes-container {
  position: absolute;
  inset: 0;
  pointer-events: none;
  z-index: 3;
}
.groups-layer {
  position: absolute;
  inset: 0;
  pointer-events: none;
  z-index: 0;
}
.comparison-layer {
  position: absolute;
  inset: 0;
  pointer-events: none;
  z-index: 1;
}
.comparison-node {
  position: absolute;
  box-sizing: border-box;
  border: 1px dashed rgb(255 214 102 / 75%);
  border-radius: 4px;
  background: rgb(255 214 102 / 8%);
}
.comparison-node.is-reroute {
  border-radius: 999px;
}
.workflow-group {
  position: absolute;
  box-sizing: border-box;
  border: 1px solid rgb(63 120 158 / 45%);
  border-radius: 2px;
  background: rgb(63 120 158 / 10%);
}
.workflow-group.is-pinned {
  box-shadow:
    inset 0 0 0 1px rgb(255 209 102 / 45%),
    0 0 0 1px rgb(255 209 102 / 18%);
}
.workflow-group-title {
  position: absolute;
  top: 4px;
  left: 8px;
  right: 8px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 0;
  background: transparent;
  border: 0;
  font-weight: 700;
  line-height: 1;
  text-shadow: 0 1px 1px rgb(0 0 0 / 85%);
  white-space: nowrap;
  z-index: 4;
  pointer-events: auto;
}
.workflow-group-title-text {
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
}
.workflow-group-rename-button,
.workflow-group-delete-button {
  flex: 0 0 auto;
}
.workflow-group-title {
  cursor: move;
  user-select: none;
}
.group-resize-handle {
  position: absolute;
  pointer-events: auto;
  z-index: 5;
}
.resize-n,
.resize-s {
  left: 10px;
  right: 10px;
  height: 8px;
}
.resize-e,
.resize-w {
  top: 10px;
  bottom: 10px;
  width: 8px;
}
.resize-n {
  top: -4px;
  cursor: ns-resize;
}
.resize-s {
  bottom: -4px;
  cursor: ns-resize;
}
.resize-e {
  right: -4px;
  cursor: ew-resize;
}
.resize-w {
  left: -4px;
  cursor: ew-resize;
}
.resize-nw,
.resize-ne,
.resize-se,
.resize-sw {
  width: 14px;
  height: 14px;
}
.resize-nw {
  top: -7px;
  left: -7px;
  cursor: nwse-resize;
}
.resize-ne {
  top: -7px;
  right: -7px;
  cursor: nesw-resize;
}
.resize-se {
  right: -7px;
  bottom: -7px;
  cursor: nwse-resize;
}
.resize-sw {
  bottom: -7px;
  left: -7px;
  cursor: nesw-resize;
}
.connections-svg {
  position: absolute;
  inset: 0;
  pointer-events: none;
  z-index: 1;
}
.ports-overlay {
  position: absolute;
  inset: 0;
  pointer-events: none;
  z-index: 4;
}
:deep(.comfy-node) {
  pointer-events: auto;
}
.port-dot {
  position: absolute;
  width: 8px;
  height: 8px;
  margin-left: -4px;
  margin-top: -4px;
  border: 1px solid #101010;
  border-radius: 50%;
  box-shadow:
    inset 0 0 0 1px rgb(255 255 255 / 18%),
    0 0 2px rgb(0 0 0 / 80%);
}
.minimap {
  position: absolute;
  right: 14px;
  bottom: 14px;
  width: 220px;
  height: 160px;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  border: 1px solid #2f2f2f;
  border-radius: 10px;
  background: rgb(18 18 18 / 94%);
  box-shadow: 0 8px 24px rgb(0 0 0 / 45%);
  z-index: 6;
}
.minimap.is-dragging {
  cursor: grabbing;
}
.minimap-header {
  flex: 0 0 auto;
  height: 26px;
  padding: 0 10px;
  border-bottom: 1px solid #2d2d2d;
  color: #a8a8a8;
  font-size: 11px;
  line-height: 26px;
  letter-spacing: 0;
}
.minimap-body {
  position: relative;
  flex: 1 1 auto;
  overflow: hidden;
  background:
    linear-gradient(rgb(255 255 255 / 2%) 1px, transparent 1px),
    linear-gradient(90deg, rgb(255 255 255 / 2%) 1px, transparent 1px);
  background-size: 16px 16px;
}
.minimap-links {
  position: absolute;
  inset: 0;
  pointer-events: none;
}
.minimap-links line {
  stroke: rgb(230 230 230 / 32%);
  stroke-width: 1;
  vector-effect: non-scaling-stroke;
}
.minimap-node {
  position: absolute;
  border-radius: 2px;
  background: rgb(56 128 211 / 88%);
  outline: 1px solid rgb(130 200 255 / 20%);
  box-shadow: 0 0 0 1px rgb(0 0 0 / 20%);
}
.minimap-node.is-collapsed {
  opacity: 0.8;
}
.minimap-node.is-reroute {
  border-radius: 999px;
  background: rgb(220 220 220 / 88%);
}
.minimap-viewport {
  position: absolute;
  border: 2px solid rgb(255 255 255 / 85%);
  background: rgb(255 255 255 / 10%);
  box-shadow: 0 0 0 1px rgb(0 0 0 / 50%);
  pointer-events: none;
}
</style>

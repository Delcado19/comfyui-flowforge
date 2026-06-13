<script setup lang="ts">
import { computed, ref, onMounted, onUnmounted } from 'vue'
import { useWorkflowStore, type ComfyNode, type ComfyPort } from '../stores/useWorkflowStore'
import {
  TITLE_HEIGHT,
  ROW_HEIGHT,
  SLOT_ROW_OFFSET,
  getNodeDisplayHeight,
  getNodeDisplaySize,
  getNodeResizeMinimumSize,
  getNodeSize,
  getNodeStoredHeightForDisplayHeight,
} from '../utils/nodeGeometry'
import { getNodeDisplayRows, type DisplayRow } from '../utils/nodeWidgets'

interface Props {
  node: ComfyNode
}

const props = defineProps<Props>()
const store = useWorkflowStore()
const titleRef = ref<HTMLElement | null>(null)
const resizeHandleRef = ref<HTMLElement | null>(null)

const typeColors: Record<string, string> = {
  default: '#353535',
  loadimage: '#223c5f',
  previewimage: '#223c5f',
  saveimage: '#223c5f',
  checkpointloader: '#3d3a1f',
  checkpointloadersimple: '#3d3a1f',
  ksampler: '#322342',
  sampleradvanced: '#322342',
  samplercustomadvanced: '#322342',
  cliptextencode: '#332233',
  emptylatentimage: '#243322',
}

const nodeSize = computed<[number, number]>(() => {
  return getNodeSize(props.node)
})

const title = computed(() => props.node.title || props.node.type)

const outputs = computed(() => props.node.outputs ?? [])
const isReroute = computed(() => props.node.type.toLowerCase().includes('reroute'))

const titleColor = computed(() => {
  if (typeof props.node.color === 'string') return props.node.color
  return typeColors[props.node.type.toLowerCase()] || typeColors.default
})
const bodyColor = computed(() => {
  if (typeof props.node.bgcolor === 'string') return props.node.bgcolor
  return '#2b2b2b'
})

const titleBarColor = computed(() => {
  const color = bodyColor.value
  return shadeColor(color, -10)
})

const isBypassed = computed(() => props.node.mode === 4)
const isCollapsed = computed(() => {
  const flags = props.node.flags
  return typeof flags === 'object' && flags !== null && 'collapsed' in flags && Boolean(flags.collapsed)
})
const isPinned = computed(() => {
  if (isVirtualHub.value) return false
  const flags = props.node.flags
  return typeof flags === 'object' && flags !== null && flags.pinned === true
})
const isVirtualHub = computed(() => props.node.type === 'SetNode' || props.node.type === 'GetNode')

const bodyStyle = computed(() => ({
  position: 'absolute' as const,
  left: props.node.pos[0] + 'px',
  top: props.node.pos[1] + 'px',
  width: nodeSize.value[0] + 'px',
  height: nodeHeight.value + 'px',
  backgroundColor: bodyColor.value,
  opacity: isBypassed.value ? 0.55 : 1,
}))

const titleStyle = computed(() => ({
  backgroundColor: titleBarColor.value,
  height: TITLE_HEIGHT + 'px',
  lineHeight: TITLE_HEIGHT + 'px',
}))

const nodeHeight = computed(() => {
  return getNodeDisplayHeight(props.node)
})

const contentHeight = computed(() => Math.max(0, nodeHeight.value - TITLE_HEIGHT))

const isDragging = ref(false)
const dragPointerId = ref<number | null>(null)
const dragStart = ref({ x: 0, y: 0 })
const nodeStart = ref({ x: 0, y: 0 })
const isResizing = ref(false)
const resizePointerId = ref<number | null>(null)
const resizeStart = ref({ x: 0, y: 0, width: 0, height: 0 })

const displayRows = computed<DisplayRow[]>(() => {
  if (isCollapsed.value || isReroute.value) {
    return []
  }
  return getNodeDisplayRows(props.node)
})

const inputRows = computed(() => {
  return displayRows.value.filter((row) => row.kind === 'input')
})

function getPortName(port: ComfyPort): string {
  return port.localized_name || port.name || ''
}

function getPortType(port: ComfyPort): string {
  return typeof port.type === 'string' ? port.type : ''
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

function rowTop(index: number): string {
  const baseOffset = isReroute.value ? 6 : SLOT_ROW_OFFSET
  return baseOffset + index * ROW_HEIGHT + 'px'
}

function shadeColor(color: string, amount: number): string {
  const hex = color.replace('#', '')
  if (!/^([0-9a-f]{6})$/i.test(hex)) return color

  const clamp = (value: number) => Math.max(0, Math.min(255, value))
  const r = clamp(parseInt(hex.slice(0, 2), 16) + amount)
  const g = clamp(parseInt(hex.slice(2, 4), 16) + amount)
  const b = clamp(parseInt(hex.slice(4, 6), 16) + amount)
  return `rgb(${r}, ${g}, ${b})`
}

function onTitlePointerDown(e: PointerEvent) {
  if (e.button !== 0) return
  const target = e.target as HTMLElement
  if (target.closest('.title-dot') || target.closest('.pin-button') || target.closest('.node-resize-handle')) return

  e.preventDefault()
  e.stopPropagation()
  isDragging.value = true
  dragPointerId.value = e.pointerId
  dragStart.value = { x: e.clientX, y: e.clientY }
  nodeStart.value = { x: props.node.pos[0], y: props.node.pos[1] }
  titleRef.value?.setPointerCapture(e.pointerId)
}

function onResizePointerDown(e: PointerEvent) {
  if (e.button !== 0) return

  e.preventDefault()
  e.stopPropagation()
  isDragging.value = false
  isResizing.value = true
  resizePointerId.value = e.pointerId

  const [width, height] = getNodeDisplaySize(props.node)
  resizeStart.value = {
    x: e.clientX,
    y: e.clientY,
    width,
    height,
  }
  resizeHandleRef.value?.setPointerCapture(e.pointerId)
}

function togglePinned() {
  store.toggleNodePinned(props.node.id)
}

function onPointerMove(e: PointerEvent) {
  if (isResizing.value && resizePointerId.value === e.pointerId) {
    const dx = (e.clientX - resizeStart.value.x) / store.scale
    const dy = (e.clientY - resizeStart.value.y) / store.scale
    const [minWidth, minHeight] = getNodeResizeMinimumSize(props.node)
    const width = Math.max(minWidth, resizeStart.value.width + dx)
    const displayHeight = Math.max(minHeight, resizeStart.value.height + dy)
    const storedHeight = getNodeStoredHeightForDisplayHeight(props.node, displayHeight)
    store.resizeNode(props.node.id, width, storedHeight)
    return
  }

  if (!isDragging.value || dragPointerId.value !== e.pointerId) return

  const dx = (e.clientX - dragStart.value.x) / store.scale
  const dy = (e.clientY - dragStart.value.y) / store.scale
  store.moveNode(props.node.id, nodeStart.value.x + dx, nodeStart.value.y + dy)
}

function onPointerUp(e: PointerEvent) {
  if (resizePointerId.value === e.pointerId) {
    if (resizeHandleRef.value?.hasPointerCapture(e.pointerId)) {
      resizeHandleRef.value.releasePointerCapture(e.pointerId)
    }
    isResizing.value = false
    resizePointerId.value = null
    return
  }

  if (dragPointerId.value !== e.pointerId) return
  if (titleRef.value?.hasPointerCapture(e.pointerId)) {
    titleRef.value.releasePointerCapture(e.pointerId)
  }
  isDragging.value = false
  dragPointerId.value = null
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
</script>

<template>
  <div
    :style="bodyStyle"
    class="comfy-node"
    :class="{ 'is-bypassed': isBypassed, 'is-collapsed': isCollapsed, 'is-reroute': isReroute, 'is-pinned': isPinned, 'is-resizing': isResizing }"
  >
    <div ref="titleRef" :style="titleStyle" class="node-title" @pointerdown="onTitlePointerDown">
      <span class="title-dot" :style="{ backgroundColor: titleColor }"></span>
      <span class="title-text">{{ title }}</span>
      <button
        v-if="!isVirtualHub"
        type="button"
        class="pin-button node-pin-button"
        :class="{ active: isPinned }"
        :title="isPinned ? 'Unpin node' : 'Pin node'"
        @pointerdown.stop
        @click.stop="togglePinned"
      >
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <path class="pin-shadow" d="M8.2 15.3 3.6 20c-.4.4-.4 1 0 1.4s1 .4 1.4 0l4.7-4.6z" />
          <path class="pin-needle" d="M8.2 15.3 3.6 20c-.4.4-.4 1 0 1.4s1 .4 1.4 0l4.7-4.6z" />
          <path class="pin-body" d="M7.2 6.5c-.9 0-1.6.7-1.6 1.6 0 .4.2.8.5 1.1l2.4 2.4-2.3 2.3c-.5.5-.5 1.4 0 1.9l2 2c.5.5 1.4.5 1.9 0l2.3-2.3 2.4 2.4c.3.3.7.5 1.1.5.9 0 1.6-.7 1.6-1.6v-4.1l3.2-3.2c.5-.5.5-1.4 0-1.9l-4.3-4.3c-.5-.5-1.4-.5-1.9 0l-3.2 3.2z" />
          <path class="pin-highlight" d="M7.4 7.8c-.3 0-.5.2-.5.5 0 .1.1.2.1.3l1.9 1.9h6.7l3.2-3.2-2.9-2.9-3.2 3.2z" />
        </svg>
      </button>
    </div>
    <div class="node-body" :style="{ height: contentHeight + 'px' }">
      <div
        v-for="(row, index) in inputRows"
        :key="row.key"
        class="slot-row input-row"
        :style="{ top: row.top + 'px', height: row.height + 'px' }"
      >
        <span
          class="slot-dot input-dot"
          :style="{ backgroundColor: slotColor(getPortType(row.input!)) }"
        ></span>
        <span class="slot-label">{{ row.name }}</span>
      </div>
      <div
        v-for="(output, index) in outputs"
        :key="'output-' + index"
        class="slot-row output-row"
        :style="{ top: rowTop(index) }"
      >
        <span class="slot-label">{{ getPortName(output) }}</span>
        <span
          class="slot-dot output-dot"
          :style="{ backgroundColor: slotColor(getPortType(output)) }"
        ></span>
      </div>
      <div
        v-for="widget in displayRows.filter((row) => row.kind === 'widget')"
        :key="widget.key"
        class="widget-row"
        :class="[`widget-kind-${widget.widgetKind}`]"
        :style="{ top: widget.top + 'px', height: widget.height + 'px' }"
      >
        <span class="widget-name">{{ widget.name }}</span>
        <span v-if="widget.widgetKind === 'text'" class="widget-text">{{ widget.value }}</span>
        <span v-else class="widget-value">{{ widget.value }}</span>
      </div>
    </div>
    <div
      ref="resizeHandleRef"
      class="node-resize-handle"
      title="Resize node"
      @pointerdown="onResizePointerDown"
    ></div>
  </div>
</template>

<style scoped>
.comfy-node {
  user-select: none;
  cursor: default;
  box-sizing: border-box;
  overflow: visible;
  border-width: 1px;
  border-style: solid;
  border-color: #101010 !important;
  border-radius: 6px;
  box-shadow: 0 2px 7px rgb(0 0 0 / 45%);
  color: #d7d7d7;
  font-family: Arial, Helvetica, sans-serif;
  font-size: 11px;
}
.comfy-node.is-pinned {
  border-color: #d7ac4d !important;
  box-shadow:
    0 0 0 1px rgb(215 172 77 / 45%),
    0 2px 7px rgb(0 0 0 / 45%);
}
.comfy-node.is-resizing {
  box-shadow:
    0 0 0 1px rgb(106 168 216 / 55%),
    0 2px 7px rgb(0 0 0 / 45%);
}
.node-title {
  position: relative;
  cursor: move;
  box-sizing: border-box;
  display: flex;
  align-items: center;
  gap: 6px;
  overflow: hidden;
  justify-content: flex-start;
  border-radius: 5px 5px 0 0;
  border-bottom: 1px solid rgb(0 0 0 / 45%);
  color: #fff;
  font-size: 12px;
  font-weight: 700;
  text-shadow: 0 1px 1px #000;
  touch-action: none;
  white-space: nowrap;
}
.title-dot {
  flex: 0 0 auto;
  width: 8px;
  height: 8px;
  margin-left: 8px;
  border-radius: 50%;
  box-shadow: 0 0 0 1px rgb(0 0 0 / 55%);
}
.title-text {
  flex: 1 1 auto;
  display: block;
  overflow: hidden;
  padding: 0 28px 0 4px;
  text-overflow: ellipsis;
  text-align: center;
}
.pin-button {
  position: absolute;
  top: 2px;
  right: 2px;
  z-index: 6;
  width: 21px;
  height: 21px;
  padding: 3px;
  border: 1px solid rgb(255 255 255 / 16%);
  border-radius: 4px;
  background: rgb(0 0 0 / 48%);
  cursor: pointer;
  opacity: 0.86;
}
.pin-button:hover,
.pin-button.active {
  background: rgb(255 56 116 / 16%);
  border-color: rgb(255 92 140 / 72%);
  opacity: 1;
}
.pin-button svg {
  display: block;
  width: 100%;
  height: 100%;
  transform: rotate(-42deg);
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
.pin-button.active .pin-body {
  fill: #e92866;
}
.pin-button.active .pin-highlight {
  fill: #ff5f8d;
}
.pin-button.active .pin-needle {
  fill: #b8b8b8;
}
.node-body {
  position: relative;
  overflow: visible;
}
.node-resize-handle {
  position: absolute;
  right: 2px;
  bottom: 2px;
  z-index: 12;
  width: 16px;
  height: 16px;
  border: 1px solid rgb(12 24 36 / 80%);
  border-bottom-right-radius: 4px;
  background:
    linear-gradient(135deg, transparent 0 38%, rgb(114 190 245 / 82%) 39% 100%),
    rgb(32 80 118 / 70%);
  box-shadow:
    inset -1px -1px 0 rgb(255 255 255 / 44%),
    0 0 0 1px rgb(114 190 245 / 40%),
    0 1px 3px rgb(0 0 0 / 55%);
  cursor: nwse-resize;
  pointer-events: auto;
  touch-action: none;
}
.node-resize-handle:hover {
  background:
    linear-gradient(135deg, transparent 0 38%, rgb(153 215 255 / 95%) 39% 100%),
    rgb(44 104 148 / 90%);
}
.node-resize-handle::before,
.node-resize-handle::after {
  content: '';
  position: absolute;
  right: 3px;
  bottom: 3px;
  border-right: 2px solid rgb(255 255 255 / 95%);
  border-bottom: 2px solid rgb(255 255 255 / 95%);
}
.node-resize-handle::before {
  width: 7px;
  height: 7px;
}
.node-resize-handle::after {
  width: 3px;
  height: 3px;
}
.slot-row {
  position: absolute;
  display: flex;
  align-items: center;
  height: 20px;
  min-width: 0;
  pointer-events: none;
}
.input-row {
  left: 0;
  right: 0;
  padding-left: 22px;
  padding-right: 18px;
}
.output-row {
  right: 0;
  z-index: 1;
  width: calc(50% - 4px);
  justify-content: flex-end;
  padding-left: 18px;
  padding-right: 22px;
  text-align: right;
}
.slot-dot {
  position: absolute;
  top: 50%;
  transform: translateY(-50%);
  width: 8px;
  height: 8px;
  opacity: 0;
  border: 1px solid #101010;
  border-radius: 50%;
  z-index: 2;
  box-shadow:
    inset 0 0 0 1px rgb(255 255 255 / 18%),
    0 0 2px rgb(0 0 0 / 80%);
}
.input-dot {
  left: -4px;
}
.output-dot {
  right: -4px;
}
.slot-label {
  flex: 0 1 auto;
  overflow: hidden;
  color: #d4d4d4;
  line-height: 20px;
  text-overflow: ellipsis;
  text-shadow: 0 1px 1px #000;
  white-space: nowrap;
}
.input-row .slot-label {
  padding-left: 8px;
}
.output-row .slot-label {
  padding-right: 8px;
}
.widget-row {
  position: absolute;
  left: 10px;
  right: 10px;
  display: grid;
  grid-template-columns: minmax(64px, 38%) minmax(0, 1fr);
  align-items: center;
  min-height: 16px;
  gap: 8px;
  padding: 1px 6px;
  border: 1px solid #111;
  border-radius: 3px;
  background: #1f1f1f;
  color: #cfcfcf;
  font-size: 10px;
  line-height: 14px;
}
.widget-kind-text {
  align-items: stretch;
  padding-top: 5px;
  padding-bottom: 5px;
}
.widget-name,
.widget-value {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.widget-name {
  color: #9f9f9f;
}
.widget-value {
  color: #e0e0e0;
  text-align: right;
}
.widget-text {
  grid-column: 1 / -1;
  overflow: hidden;
  color: #f1f1f1;
  white-space: pre-wrap;
  word-break: break-word;
  line-height: 1.3;
}
.is-collapsed .widget-row,
.is-reroute .widget-row {
  display: none;
}
.is-reroute .node-title {
  font-size: 11px;
}
.is-reroute .node-body {
  overflow: visible;
}
</style>

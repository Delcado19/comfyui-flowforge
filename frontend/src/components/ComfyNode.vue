<script setup lang="ts">
import { computed, ref, onMounted, onUnmounted } from 'vue'
import { useWorkflowStore, type ComfyNode, type ComfyPort } from '../stores/useWorkflowStore'

interface Props {
  node: ComfyNode
}

const props = defineProps<Props>()
const store = useWorkflowStore()
const titleRef = ref<HTMLElement | null>(null)

const TITLE_HEIGHT = 26
const ROW_HEIGHT = 20
const SLOT_ROW_OFFSET = 8
const WIDGET_GAP = 4
const PORT_CENTER_OFFSET = 12
const NODE_BOTTOM_PADDING = 12

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
  if (Array.isArray(props.node.size)) return props.node.size
  return [props.node.size?.width ?? 200, props.node.size?.height ?? 100]
})

const title = computed(() => props.node.title || props.node.type)

const inputs = computed(() => props.node.inputs ?? [])
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

const portRowCount = computed(() => Math.max(inputs.value.length, outputs.value.length, isReroute.value ? 1 : 0))
const widgetBlockHeight = computed(() => {
  if (isCollapsed.value || isReroute.value) return 0
  return widgetRows.value.reduce((total, row) => total + row.height + 4, 0)
})

const minimumContentHeight = computed(() => {
  const slotHeight = SLOT_ROW_OFFSET + portRowCount.value * ROW_HEIGHT
  const widgetHeight = widgetRows.value.length > 0 && !isCollapsed.value && !isReroute.value ? WIDGET_GAP + widgetBlockHeight.value : 0
  return slotHeight + widgetHeight + NODE_BOTTOM_PADDING
})

const nodeHeight = computed(() => {
  const baseHeight = nodeSize.value[1] + NODE_BOTTOM_PADDING
  if (isReroute.value) {
    return Math.max(TITLE_HEIGHT + minimumContentHeight.value, TITLE_HEIGHT + ROW_HEIGHT + 10)
  }
  if (isCollapsed.value) {
    return Math.max(TITLE_HEIGHT + minimumContentHeight.value, TITLE_HEIGHT + ROW_HEIGHT + 8)
  }
  return Math.max(baseHeight, TITLE_HEIGHT + minimumContentHeight.value)
})

const contentHeight = computed(() => Math.max(0, nodeHeight.value - TITLE_HEIGHT))

const isDragging = ref(false)
const dragPointerId = ref<number | null>(null)
const dragStart = ref({ x: 0, y: 0 })
const nodeStart = ref({ x: 0, y: 0 })

interface WidgetRow {
  name: string
  value: string
  kind: 'text' | 'select' | 'toggle' | 'number' | 'compact'
  height: number
}

const widgetRows = computed<WidgetRow[]>(() => {
  if (isCollapsed.value || isReroute.value) {
    return []
  }

  const rows: WidgetRow[] = []
  const values = Array.isArray(props.node.widgets_values) ? props.node.widgets_values : []
  let widgetIndex = 0

  for (const input of inputs.value) {
    if (!input.widget) continue
    const value = values[widgetIndex]
    rows.push(buildWidgetRow(input, value, widgetIndex))
    widgetIndex += 1
  }

  for (let index = widgetIndex; index < values.length; index += 1) {
    const value = values[index]
    rows.push({
      name: `value_${index}`,
      value: formatWidgetValue(value),
      kind: inferWidgetKind(value, undefined),
      height: inferWidgetHeight(value, undefined),
    })
  }

  return rows
})

const inputRows = computed(() => {
  return inputs.value.map((input) => {
    return {
      input,
    }
  })
})

const fallbackWidgetStart = computed(() => {
  const slotRows = Math.max(inputs.value.length, outputs.value.length)
  return SLOT_ROW_OFFSET + slotRows * ROW_HEIGHT + WIDGET_GAP
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

function widgetTopOffset(index: number): number {
  const previous = widgetRows.value.slice(0, index)
  return previous.reduce((total, row) => total + row.height + 4, 0)
}

function rowTop(index: number): string {
  const baseOffset = isReroute.value ? 6 : SLOT_ROW_OFFSET
  return baseOffset + index * ROW_HEIGHT + 'px'
}

function buildWidgetRow(input: ComfyPort, value: unknown, index: number): WidgetRow {
  const widget = input.widget ?? {}
  const name = getWidgetName(input, index)
  const kind = inferWidgetKind(value, widget)
  return {
    name,
    value: formatWidgetValue(value),
    kind,
    height: inferWidgetHeight(value, widget),
  }
}

function getWidgetName(input: ComfyPort, index: number): string {
  const widget = input.widget
  const widgetName = widget && typeof widget.name === 'string' ? widget.name : ''
  return widgetName || getPortName(input) || `value_${index}`
}

function inferWidgetKind(value: unknown, widget: Record<string, unknown> | undefined): WidgetRow['kind'] {
  const widgetType = typeof widget?.type === 'string' ? widget.type.toLowerCase() : ''
  if (widgetType.includes('toggle') || widgetType.includes('checkbox') || typeof value === 'boolean') {
    return 'toggle'
  }
  if (widgetType.includes('combo') || widgetType.includes('select') || widgetType.includes('dropdown')) {
    return 'select'
  }
  if (widgetType.includes('number') || widgetType.includes('slider') || typeof value === 'number') {
    return 'number'
  }
  if (widgetType.includes('text') || widgetType.includes('string')) {
    return isMultilineWidget(value) ? 'text' : 'compact'
  }
  return isMultilineWidget(value) ? 'text' : 'compact'
}

function inferWidgetHeight(value: unknown, widget: Record<string, unknown> | undefined): number {
  const kind = inferWidgetKind(value, widget)
  if (kind === 'text') {
    const text = formatWidgetValue(value)
    const lines = Math.max(3, text.split(/\r?\n/).length)
    const estimated = Math.ceil(text.length / 54)
    return Math.min(180, Math.max(60, Math.max(lines, estimated) * 18 + 10))
  }
  return 20
}

function isMultilineWidget(value: unknown): boolean {
  const text = formatWidgetValue(value)
  return text.includes('\n') || text.length > 54
}

function formatWidgetValue(value: unknown): string {
  if (value === null || value === undefined) return ''
  if (typeof value === 'string') return value
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  return JSON.stringify(value)
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
  if (target.closest('.title-dot')) return

  e.preventDefault()
  e.stopPropagation()
  isDragging.value = true
  dragPointerId.value = e.pointerId
  dragStart.value = { x: e.clientX, y: e.clientY }
  nodeStart.value = { x: props.node.pos[0], y: props.node.pos[1] }
  titleRef.value?.setPointerCapture(e.pointerId)
}

function onPointerMove(e: PointerEvent) {
  if (!isDragging.value || dragPointerId.value !== e.pointerId) return

  const dx = (e.clientX - dragStart.value.x) / store.scale
  const dy = (e.clientY - dragStart.value.y) / store.scale
  store.moveNode(props.node.id, nodeStart.value.x + dx, nodeStart.value.y + dy)
}

function onPointerUp(e: PointerEvent) {
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
    :class="{ 'is-bypassed': isBypassed, 'is-collapsed': isCollapsed, 'is-reroute': isReroute }"
  >
    <div ref="titleRef" :style="titleStyle" class="node-title" @pointerdown="onTitlePointerDown">
      <span class="title-dot" :style="{ backgroundColor: titleColor }"></span>
      <span class="title-text">{{ title }}</span>
    </div>
    <div class="node-body" :style="{ height: contentHeight + 'px' }">
      <div
        v-for="(row, index) in inputRows"
        :key="'input-' + index"
        class="slot-row input-row"
        :style="{ top: rowTop(index) }"
      >
        <span
          class="slot-dot input-dot"
          :style="{ backgroundColor: slotColor(getPortType(row.input)) }"
        ></span>
        <span class="slot-label">{{ getPortName(row.input) }}</span>
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
        v-for="(widget, index) in widgetRows"
        :key="'widget-' + index"
        class="widget-row"
        :class="[`widget-kind-${widget.kind}`]"
        :style="{ top: fallbackWidgetStart + widgetTopOffset(index) + 'px', height: widget.height + 'px' }"
      >
        <span class="widget-name">{{ widget.name }}</span>
        <span v-if="widget.kind === 'text'" class="widget-text">{{ widget.value }}</span>
        <span v-else class="widget-value">{{ widget.value }}</span>
      </div>
    </div>
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
.node-title {
  cursor: move;
  box-sizing: border-box;
  display: flex;
  align-items: center;
  gap: 8px;
  overflow: hidden;
  justify-content: center;
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
  margin-left: 10px;
  border-radius: 50%;
  box-shadow: 0 0 0 1px rgb(0 0 0 / 55%);
}
.title-text {
  flex: 1 1 auto;
  display: block;
  overflow: hidden;
  padding: 0 9px;
  text-overflow: ellipsis;
  text-align: center;
}
.node-body {
  position: relative;
  overflow: visible;
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

<script setup lang="ts">
import { computed } from 'vue'
import type { ComfyNode, ComfyPort } from '../stores/useWorkflowStore'

interface Props {
  node: ComfyNode
}

const props = defineProps<Props>()

const TITLE_HEIGHT = 26
const ROW_HEIGHT = 20
const SLOT_ROW_OFFSET = 8

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

const titleColor = computed(() => {
  if (typeof props.node.color === 'string') return props.node.color
  return typeColors[props.node.type.toLowerCase()] || typeColors.default
})

const bodyColor = computed(() => {
  if (typeof props.node.bgcolor === 'string') return props.node.bgcolor
  return '#2b2b2b'
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
  height: nodeSize.value[1] + 'px',
  backgroundColor: bodyColor.value,
  opacity: isBypassed.value ? 0.55 : 1,
}))

const titleStyle = computed(() => ({
  backgroundColor: titleColor.value,
  height: TITLE_HEIGHT + 'px',
  lineHeight: TITLE_HEIGHT + 'px',
}))

const contentHeight = computed(() => Math.max(0, nodeSize.value[1] - TITLE_HEIGHT))

const widgetRows = computed(() => {
  const values = Array.isArray(props.node.widgets_values) ? props.node.widgets_values : []
  return values.map((value, index) => ({
    name: `value_${index}`,
    value: formatWidgetValue(value),
  }))
})

const inputRows = computed(() => {
  let widgetIndex = 0
  return inputs.value.map((input) => {
    const widget = input.widget ? { value: formatWidgetValue(getWidgetValue(widgetIndex)) } : null
    if (input.widget) widgetIndex += 1

    return {
      input,
      widget,
    }
  })
})

const fallbackWidgets = computed(() => {
  const hasInputWidgets = inputs.value.some((input) => input.widget)
  return hasInputWidgets ? [] : widgetRows.value
})

const fallbackWidgetStart = computed(() => {
  const slotRows = Math.max(inputs.value.length, outputs.value.length)
  return SLOT_ROW_OFFSET + slotRows * ROW_HEIGHT
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
  return SLOT_ROW_OFFSET + index * ROW_HEIGHT + 'px'
}

function getWidgetValue(index: number): unknown {
  const values = Array.isArray(props.node.widgets_values) ? props.node.widgets_values : []
  return values[index]
}

function formatWidgetValue(value: unknown): string {
  if (value === null || value === undefined) return ''
  if (typeof value === 'string') return value
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  return JSON.stringify(value)
}
</script>

<template>
  <div
    :style="bodyStyle"
    class="comfy-node"
    :class="{ 'is-bypassed': isBypassed, 'is-collapsed': isCollapsed }"
  >
    <div :style="titleStyle" class="node-title">
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
        <span v-if="row.widget" class="inline-widget-value">{{ row.widget.value }}</span>
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
        v-for="(widget, index) in fallbackWidgets"
        :key="'widget-' + index"
        class="widget-row"
        :style="{ top: fallbackWidgetStart + index * ROW_HEIGHT + 'px' }"
      >
        <span class="widget-name">{{ widget.name }}</span>
        <span class="widget-value">{{ widget.value }}</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.comfy-node {
  user-select: none;
  cursor: default;
  box-sizing: border-box;
  overflow: hidden;
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
  overflow: hidden;
  border-radius: 5px 5px 0 0;
  border-bottom: 1px solid rgb(0 0 0 / 45%);
  color: #fff;
  font-size: 12px;
  font-weight: 700;
  text-align: center;
  text-shadow: 0 1px 1px #000;
  white-space: nowrap;
}
.title-text {
  display: block;
  overflow: hidden;
  padding: 0 9px;
  text-overflow: ellipsis;
}
.node-body {
  position: relative;
  overflow: hidden;
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
  padding-left: 11px;
  padding-right: 11px;
}
.output-row {
  right: 0;
  z-index: 1;
  width: calc(50% - 4px);
  justify-content: flex-end;
  padding-left: 4px;
  padding-right: 11px;
  text-align: right;
}
.slot-dot {
  position: absolute;
  top: 50%;
  transform: translateY(-50%);
  width: 8px;
  height: 8px;
  border: 1px solid #101010;
  border-radius: 50%;
  box-shadow:
    inset 0 0 0 1px rgb(255 255 255 / 18%),
    0 0 2px rgb(0 0 0 / 80%);
}
.input-dot {
  left: -5px;
}
.output-dot {
  right: -5px;
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
.inline-widget-value {
  flex: 1 1 auto;
  min-width: 48px;
  height: 16px;
  margin-left: 8px;
  padding: 0 6px;
  overflow: hidden;
  border: 1px solid #111;
  border-radius: 3px;
  background: #1e1e1e;
  color: #f1f1f1;
  font-size: 10px;
  line-height: 14px;
  text-align: right;
  text-overflow: ellipsis;
  white-space: nowrap;
  box-shadow: inset 0 1px 0 rgb(255 255 255 / 6%);
}
.widget-row {
  position: absolute;
  left: 10px;
  right: 10px;
  display: grid;
  grid-template-columns: minmax(64px, 38%) minmax(0, 1fr);
  align-items: center;
  height: 16px;
  gap: 8px;
  padding: 0 6px;
  border: 1px solid #111;
  border-radius: 3px;
  background: #1f1f1f;
  color: #cfcfcf;
  font-size: 10px;
  line-height: 14px;
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
.is-collapsed .node-body {
  display: none;
}
</style>

import type { ComfyNode, ComfyPort } from '../stores/useWorkflowStore'

export const TITLE_HEIGHT = 26
export const ROW_HEIGHT = 20
export const SLOT_ROW_OFFSET = 8
export const WIDGET_GAP = 4
export const NODE_BOTTOM_PADDING = 12
export const REROUTE_SLOT_OFFSET = 6

export function getNodeSize(node: ComfyNode): [number, number] {
  if (Array.isArray(node.size)) return node.size
  return [node.size?.width ?? 200, node.size?.height ?? 100]
}

export function isRerouteNode(node: ComfyNode): boolean {
  return node.type.toLowerCase().includes('reroute')
}

export function isCollapsedNode(node: ComfyNode): boolean {
  const flags = node.flags
  return typeof flags === 'object' && flags !== null && 'collapsed' in flags && Boolean(flags.collapsed)
}

export function getNodeDisplayHeight(node: ComfyNode): number {
  const [_, baseHeight] = getNodeSize(node)
  if (isRerouteNode(node)) {
    return Math.max(TITLE_HEIGHT + SLOT_ROW_OFFSET + ROW_HEIGHT + NODE_BOTTOM_PADDING, TITLE_HEIGHT + ROW_HEIGHT + 10)
  }
  if (isCollapsedNode(node)) {
    return Math.max(TITLE_HEIGHT + SLOT_ROW_OFFSET + ROW_HEIGHT + NODE_BOTTOM_PADDING, TITLE_HEIGHT + ROW_HEIGHT + 8)
  }

  const inputs = node.inputs ?? []
  const outputs = node.outputs ?? []
  const widgetRows = getWidgetRows(node)
  const slotRowCount = Math.max(inputs.length, outputs.length)
  const slotHeight = SLOT_ROW_OFFSET + slotRowCount * ROW_HEIGHT
  const widgetHeight = widgetRows.length > 0 ? WIDGET_GAP + widgetRows.reduce((total, row) => total + row.height + 4, 0) : 0
  return Math.max(baseHeight + NODE_BOTTOM_PADDING, TITLE_HEIGHT + slotHeight + widgetHeight + NODE_BOTTOM_PADDING)
}

export function getNodeDisplaySize(node: ComfyNode): [number, number] {
  const [width] = getNodeSize(node)
  return [width, getNodeDisplayHeight(node)]
}

export function getNodePortY(node: ComfyNode, portIndex: number): number {
  const baseOffset = isRerouteNode(node) ? REROUTE_SLOT_OFFSET : SLOT_ROW_OFFSET
  return TITLE_HEIGHT + baseOffset + ROW_HEIGHT / 2 + portIndex * ROW_HEIGHT
}

interface WidgetRowEstimate {
  height: number
}

function getWidgetRows(node: ComfyNode): WidgetRowEstimate[] {
  if (isCollapsedNode(node) || isRerouteNode(node)) {
    return []
  }

  const rows: WidgetRowEstimate[] = []
  const values = Array.isArray(node.widgets_values) ? node.widgets_values : []
  let widgetIndex = 0

  for (const input of node.inputs ?? []) {
    if (!input.widget) continue
    const value = values[widgetIndex]
    rows.push({ height: estimateWidgetHeight(value, input.widget) })
    widgetIndex += 1
  }

  for (let index = widgetIndex; index < values.length; index += 1) {
    rows.push({ height: estimateWidgetHeight(values[index], undefined) })
  }

  return rows
}

function estimateWidgetHeight(value: unknown, widget: ComfyPort['widget']): number {
  const widgetType = typeof widget?.type === 'string' ? widget.type.toLowerCase() : ''
  const text = formatWidgetValue(value)
  if (widgetType.includes('text') || widgetType.includes('string') || text.includes('\n') || text.length > 54) {
    const lines = Math.max(3, text.split(/\r?\n/).length)
    const estimated = Math.ceil(text.length / 54)
    return Math.min(180, Math.max(60, Math.max(lines, estimated) * 18 + 10))
  }
  return 20
}

function formatWidgetValue(value: unknown): string {
  if (value === null || value === undefined) return ''
  if (typeof value === 'string') return value
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  return JSON.stringify(value)
}

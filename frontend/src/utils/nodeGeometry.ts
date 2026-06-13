import type { ComfyNode } from '../stores/useWorkflowStore'
import { getNodeDisplayRows, getInputPortCenterY, ROW_GAP, type DisplayRow } from './nodeWidgets'

export const TITLE_HEIGHT = 26
export const ROW_HEIGHT = 20
export const SLOT_ROW_OFFSET = 8
export const NODE_BOTTOM_PADDING = 12
export const REROUTE_SLOT_OFFSET = 6
// Multiline text widgets render as tall as their content, but that height must
// not become the node's minimum: ComfyUI lets long prompts (e.g. CLIPTextEncode)
// be shrunk so the text scrolls/clips. Cap the text contribution to the minimum
// so a long prompt no longer blocks resizing the node smaller.
const TEXT_WIDGET_MIN_HEIGHT = 60
const NODE_RESIZE_MIN_WIDTH = 120
const REROUTE_NODE_RESIZE_MIN_WIDTH = 40
const DECORATIVE_NODE_RESIZE_MIN_WIDTH = 120
const DECORATIVE_NODE_RESIZE_MIN_HEIGHT = 60

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
  const [, baseHeight] = getNodeSize(node)
  if (preservesAuthoredNodeSize(node)) {
    return baseHeight
  }
  if (isRerouteNode(node)) {
    return Math.max(baseHeight, getRerouteMinimumHeight())
  }
  if (isCollapsedNode(node)) {
    return Math.max(baseHeight, getCollapsedMinimumHeight())
  }

  return Math.max(baseHeight + NODE_BOTTOM_PADDING, getNodeContentMinimumHeight(node))
}

export function getNodeDisplaySize(node: ComfyNode): [number, number] {
  const [width] = getNodeSize(node)
  return [width, getNodeDisplayHeight(node)]
}

export function getNodeResizeMinimumSize(node: ComfyNode): [number, number] {
  if (preservesAuthoredNodeSize(node)) {
    return [DECORATIVE_NODE_RESIZE_MIN_WIDTH, DECORATIVE_NODE_RESIZE_MIN_HEIGHT]
  }
  if (isRerouteNode(node)) {
    return [REROUTE_NODE_RESIZE_MIN_WIDTH, getRerouteMinimumHeight()]
  }
  if (isCollapsedNode(node)) {
    return [NODE_RESIZE_MIN_WIDTH, getCollapsedMinimumHeight()]
  }

  return [NODE_RESIZE_MIN_WIDTH, getNodeContentMinimumHeight(node)]
}

export function getNodeStoredHeightForDisplayHeight(node: ComfyNode, displayHeight: number): number {
  if (preservesAuthoredNodeSize(node) || isRerouteNode(node) || isCollapsedNode(node)) {
    return displayHeight
  }

  // Regular nodes render a small bottom gutter outside the saved ComfyUI size.
  return Math.max(0, displayHeight - NODE_BOTTOM_PADDING)
}

function getNodeContentMinimumHeight(node: ComfyNode): number {
  const outputs = node.outputs ?? []
  const rows = getNodeDisplayRows(node)
  // Restack the rows using capped text-widget heights so a long multiline
  // prompt does not inflate the node's minimum height; the row tops are
  // recomputed here instead of using row.top because a capped text row also
  // shifts every row below it up.
  let top = SLOT_ROW_OFFSET
  let rowsHeight = SLOT_ROW_OFFSET
  for (const row of rows) {
    const height = minimumRowHeight(row)
    rowsHeight = Math.max(rowsHeight, top + height)
    top += height + ROW_GAP
  }
  const outputHeight = SLOT_ROW_OFFSET + outputs.length * ROW_HEIGHT
  return TITLE_HEIGHT + Math.max(rowsHeight, outputHeight) + NODE_BOTTOM_PADDING
}

function minimumRowHeight(row: DisplayRow): number {
  if (row.kind === 'widget' && row.widgetKind === 'text') {
    return Math.min(row.height, TEXT_WIDGET_MIN_HEIGHT)
  }
  return row.height
}

function getRerouteMinimumHeight(): number {
  return Math.max(TITLE_HEIGHT + SLOT_ROW_OFFSET + ROW_HEIGHT + NODE_BOTTOM_PADDING, TITLE_HEIGHT + ROW_HEIGHT + 10)
}

function getCollapsedMinimumHeight(): number {
  return Math.max(TITLE_HEIGHT + SLOT_ROW_OFFSET + ROW_HEIGHT + NODE_BOTTOM_PADDING, TITLE_HEIGHT + ROW_HEIGHT + 8)
}

export function preservesAuthoredNodeSize(node: ComfyNode): boolean {
  return isImageIoNode(node) || isDecorativeNode(node)
}

export function isDecorativeNode(node: ComfyNode): boolean {
  const normalizedType = normalizeNodeType(node.type)
  return normalizedType.includes('note') || normalizedType.startsWith('label')
}

export function isImageIoNode(node: ComfyNode): boolean {
  const normalizedType = normalizeNodeType(node.type)
  return normalizedType.startsWith('loadimage') || normalizedType.startsWith('saveimage')
}

function normalizeNodeType(type: string): string {
  return type.toLowerCase().replace(/[^a-z0-9]/g, '')
}

export function getNodeInputPortY(node: ComfyNode, inputIndex: number): number {
  if (isRerouteNode(node)) {
    return TITLE_HEIGHT + REROUTE_SLOT_OFFSET + ROW_HEIGHT / 2 + inputIndex * ROW_HEIGHT
  }
  return TITLE_HEIGHT + getInputPortCenterY(node, inputIndex, ROW_HEIGHT, SLOT_ROW_OFFSET)
}

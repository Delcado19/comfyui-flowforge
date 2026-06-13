import type { ComfyNode } from '../stores/useWorkflowStore'
import { getNodeDisplayRows, getInputPortCenterY } from './nodeWidgets'

export const TITLE_HEIGHT = 26
export const ROW_HEIGHT = 20
export const SLOT_ROW_OFFSET = 8
export const NODE_BOTTOM_PADDING = 12
export const REROUTE_SLOT_OFFSET = 6
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
  const rowsHeight = rows.reduce((bottom, row) => Math.max(bottom, row.top + row.height), SLOT_ROW_OFFSET)
  const outputHeight = SLOT_ROW_OFFSET + outputs.length * ROW_HEIGHT
  return TITLE_HEIGHT + Math.max(rowsHeight, outputHeight) + NODE_BOTTOM_PADDING
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

import type { ComfyNode } from '../stores/useWorkflowStore'
import { getNodeDisplayRows, getInputPortCenterY } from './nodeWidgets'

export const TITLE_HEIGHT = 26
export const ROW_HEIGHT = 20
export const SLOT_ROW_OFFSET = 8
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

  const outputs = node.outputs ?? []
  const rows = getNodeDisplayRows(node)
  const rowsHeight = rows.reduce((bottom, row) => Math.max(bottom, row.top + row.height), SLOT_ROW_OFFSET)
  const outputHeight = SLOT_ROW_OFFSET + outputs.length * ROW_HEIGHT
  return Math.max(baseHeight + NODE_BOTTOM_PADDING, TITLE_HEIGHT + Math.max(rowsHeight, outputHeight) + NODE_BOTTOM_PADDING)
}

export function getNodeDisplaySize(node: ComfyNode): [number, number] {
  const [width] = getNodeSize(node)
  return [width, getNodeDisplayHeight(node)]
}

export function getNodeInputPortY(node: ComfyNode, inputIndex: number): number {
  if (isRerouteNode(node)) {
    return TITLE_HEIGHT + REROUTE_SLOT_OFFSET + ROW_HEIGHT / 2 + inputIndex * ROW_HEIGHT
  }
  return TITLE_HEIGHT + getInputPortCenterY(node, inputIndex, ROW_HEIGHT, SLOT_ROW_OFFSET)
}

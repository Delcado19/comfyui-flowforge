import { defineStore } from 'pinia'

export type NodeId = number | string
export type NodeSize = [number, number] | { width?: number; height?: number }
export type ComfyLink = [number, NodeId, number, NodeId, number, string?]

export interface ComfyPort {
  name?: string
  localized_name?: string
  type?: string
  link?: number | null
  links?: number[] | null
  widget?: {
    name?: string
    [key: string]: unknown
  } | null
  [key: string]: unknown
}

export interface ComfyNode {
  id: NodeId
  type: string
  pos: [number, number]
  size?: NodeSize
  title?: string
  flags?: Record<string, unknown>
  inputs?: ComfyPort[]
  outputs?: ComfyPort[]
  [key: string]: unknown
}

export interface Connection {
  id: number
  source: NodeId
  sourcePort: number
  target: NodeId
  targetPort: number
  type?: string
}

export interface ComfyWorkflow {
  nodes: ComfyNode[]
  links?: ComfyLink[]
  groups?: ComfyGroup[]
  [key: string]: unknown
}

export interface ComfyGroup {
  id: number | string
  title?: string
  bounding?: [number, number, number, number]
  color?: string
  font_size?: number
  flags?: Record<string, unknown>
  [key: string]: unknown
}

export const useWorkflowStore = defineStore('workflow', {
  state: () => ({
    workflow: null as ComfyWorkflow | null,
    scale: 1,
    offsetX: 0,
    offsetY: 0
  }),
  getters: {
    nodes: (state): ComfyNode[] => state.workflow?.nodes ?? [],
    hasPinnedItems: (state): boolean => {
      return Boolean(
        state.workflow?.nodes.some((node) => isPinned(node)) ||
        state.workflow?.groups?.some((group) => isPinned(group)),
      )
    },
    connections: (state): Connection[] => {
      return (state.workflow?.links ?? []).map((link) => ({
        id: link[0],
        source: link[1],
        sourcePort: link[2],
        target: link[3],
        targetPort: link[4],
        type: link[5],
      }))
    },
  },
  actions: {
    setWorkflow(workflow: ComfyWorkflow) {
      this.workflow = workflow
    },
    setView(scale: number, offsetX: number, offsetY: number) {
      this.scale = scale
      this.offsetX = offsetX
      this.offsetY = offsetY
    },
    moveNode(nodeId: NodeId, x: number, y: number) {
      const node = this.workflow?.nodes.find((item) => item.id === nodeId)
      if (!node) return
      node.pos = [x, y]
    },
    resizeNode(nodeId: NodeId, width: number, height: number) {
      const node = this.workflow?.nodes.find((item) => item.id === nodeId)
      if (!node) return

      if (Array.isArray(node.size)) {
        node.size = [width, height]
        return
      }

      if (node.size && typeof node.size === 'object') {
        node.size = { ...node.size, width, height }
        return
      }

      node.size = [width, height]
    },
    moveGroupByDelta(groupId: number | string, dx: number, dy: number, nodeIds: NodeId[] = []) {
      const group = this.workflow?.groups?.find((item) => item.id === groupId)
      if (!group || !Array.isArray(group.bounding)) return

      const [x, y, width, height] = group.bounding
      group.bounding = [x + dx, y + dy, width, height]

      for (const nodeId of nodeIds) {
        const node = this.workflow?.nodes.find((item) => item.id === nodeId)
        if (!node) continue
        node.pos = [node.pos[0] + dx, node.pos[1] + dy]
      }
    },
    resizeGroup(groupId: number | string, bounding: [number, number, number, number]) {
      const group = this.workflow?.groups?.find((item) => item.id === groupId)
      if (!group) return
      group.bounding = bounding
    },
    renameGroup(groupId: number | string, title: string) {
      const group = this.workflow?.groups?.find((item) => item.id === groupId)
      if (!group) return
      group.title = title
    },
    toggleNodePinned(nodeId: NodeId) {
      const node = this.workflow?.nodes.find((item) => item.id === nodeId)
      if (!node) return
      setPinned(node, !isPinned(node))
    },
    toggleGroupPinned(groupId: number | string) {
      const group = this.workflow?.groups?.find((item) => item.id === groupId)
      if (!group) return
      setPinned(group, !isPinned(group))
    },
    unpinAll() {
      if (!this.workflow) return
      for (const node of this.workflow.nodes) {
        setPinned(node, false)
      }
      for (const group of this.workflow.groups ?? []) {
        setPinned(group, false)
      }
    },
    createGroup(bounding: [number, number, number, number], title?: string) {
      if (!this.workflow) return

      const groups = this.workflow.groups ?? (this.workflow.groups = [])
      const nextId = groups.reduce((max, group) => {
        return typeof group.id === 'number' && Number.isFinite(group.id)
          ? Math.max(max, group.id)
          : max
      }, 0) + 1

      groups.push({
        id: nextId,
        title: title || `Group ${nextId}`,
        bounding,
      })
    },
    deleteGroup(groupId: number | string) {
      if (!this.workflow?.groups) return
      this.workflow.groups = this.workflow.groups.filter((group) => group.id !== groupId)
    },
    deleteAllGroups() {
      if (!this.workflow) return
      this.workflow.groups = []
    },
    loadWorkflow(data: ComfyWorkflow) {
      if (!Array.isArray(data.nodes)) {
        throw new Error('ComfyUI workflow is missing a nodes array')
      }
      this.workflow = data
    }
  }
})

function isPinned(item: { flags?: Record<string, unknown> }): boolean {
  return Boolean(item.flags && item.flags.pinned === true)
}

function setPinned(item: { flags?: Record<string, unknown> }, pinned: boolean): void {
  if (pinned) {
    item.flags = { ...(item.flags ?? {}), pinned: true }
    return
  }

  if (!item.flags || !('pinned' in item.flags)) return

  const flags = { ...item.flags }
  delete flags.pinned
  item.flags = Object.keys(flags).length > 0 ? flags : undefined
}

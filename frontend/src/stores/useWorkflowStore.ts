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
  groups?: unknown[]
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
    loadWorkflow(data: ComfyWorkflow) {
      if (!Array.isArray(data.nodes)) {
        throw new Error('ComfyUI workflow is missing a nodes array')
      }
      this.workflow = data
    }
  }
})

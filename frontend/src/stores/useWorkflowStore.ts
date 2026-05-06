import { defineStore } from 'pinia'

export interface Port {
  id: string
  name: string
  type?: string
}

export interface Node {
  id: string
  type: string
  pos: [number, number]
  size: [number, number]
  title?: string
  inputs: Port[]
  outputs: Port[]
}

export interface Connection {
  id: string
  source: string
  sourcePort: string
  target: string
  targetPort: string
  type?: string
}

export const useWorkflowStore = defineStore('workflow', {
  state: () => ({
    nodes: [] as Node[],
    connections: [] as Connection[],
    scale: 1,
    offsetX: 0,
    offsetY: 0
  }),
  actions: {
    addNode(node: Node) {
      this.nodes.push(node)
    },
    removeNode(id: string) {
      this.nodes = this.nodes.filter(n => n.id !== id)
      this.connections = this.connections.filter(c => c.source !== id && c.target !== id)
    },
    addConnection(connection: Connection) {
      this.connections.push(connection)
    },
    removeConnection(id: string) {
      this.connections = this.connections.filter(c => c.id !== id)
    },
    setNodes(nodes: Node[]) {
      this.nodes = nodes
    },
    setConnections(connections: Connection[]) {
      this.connections = connections
    },
    setView(scale: number, offsetX: number, offsetY: number) {
      this.scale = scale
      this.offsetX = offsetX
      this.offsetY = offsetY
    },
    loadWorkflow(data: { nodes: Node[]; connections: Connection[] }) {
      this.nodes = data.nodes || []
      this.connections = data.connections || []
    }
  }
})
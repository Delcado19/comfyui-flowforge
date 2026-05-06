<script setup lang="ts">
import { computed } from 'vue'
import type { ComfyNode } from '../stores/useWorkflowStore'

interface Props {
  node: ComfyNode
}

const props = defineProps<Props>()

const nodeColors: Record<string, string> = {
  default: '#5a5a5a',
  loadimage: '#4a6fa0',
  checkpointloader: '#4a6fa0',
  ksampler: '#6a4a80',
  emptylatentimage: '#6a804a',
}

const headerColor = computed(() => {
  return nodeColors[props.node.type.toLowerCase()] || nodeColors.default
})

const nodeSize = computed<[number, number]>(() => {
  if (Array.isArray(props.node.size)) return props.node.size
  return [props.node.size?.width ?? 200, props.node.size?.height ?? 100]
})

const bodyStyle = computed(() => ({
  position: 'absolute' as const,
  left: props.node.pos[0] + 'px',
  top: props.node.pos[1] + 'px',
  width: nodeSize.value[0] + 'px',
  minHeight: '80px',
  backgroundColor: '#2a2a2a',
  border: '1px solid #444',
  borderRadius: '4px',
  boxShadow: '0 2px 8px rgba(0,0,0,0.3)',
}))

const titleStyle = computed(() => ({
  backgroundColor: headerColor.value,
  padding: '8px 12px',
  fontSize: '13px',
  fontWeight: 'bold',
  color: '#fff',
  borderBottom: '1px solid #444',
}))

const portSize = 8
</script>

<template>
  <div :style="bodyStyle" class="comfy-node">
    <div :style="titleStyle" class="node-title">
      {{ node.title || node.type }}
    </div>
    <div class="node-body" style="padding: 8px;">
      <div class="inputs" style="margin-bottom: 4px;">
        <div v-for="(input, index) in node.inputs ?? []" :key="index" class="input-port"
          style="display: flex; align-items: center; margin: 2px 0; font-size: 12px;">
          <div :style="{
            width: portSize + 'px',
            height: portSize + 'px',
            border: '1px solid #888',
            borderRadius: '2px',
            marginRight: '4px',
            backgroundColor: input.type === 'IMAGE' ? '#4a6fa0' : input.type === 'MASK' ? '#6a4a80' : '#555'
          }"></div>
          <span style="color: #ccc;">{{ input.name }}</span>
        </div>
      </div>
      <div class="outputs">
        <div v-for="(output, index) in node.outputs ?? []" :key="index" class="output-port"
          style="display: flex; align-items: center; margin: 2px 0; font-size: 12px; justify-content: flex-end;">
          <span style="color: #ccc;">{{ output.name }}</span>
          <div :style="{
            width: portSize + 'px',
            height: portSize + 'px',
            border: '1px solid #888',
            borderRadius: '2px',
            marginLeft: '4px',
            backgroundColor: output.type === 'IMAGE' ? '#4a6fa0' : output.type === 'MASK' ? '#6a4a80' : '#555'
          }"></div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.comfy-node {
  user-select: none;
  cursor: default;
}
.node-title {
  cursor: move;
}
</style>

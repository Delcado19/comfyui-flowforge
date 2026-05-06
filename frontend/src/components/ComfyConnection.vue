<script setup lang="ts">
import type { Connection } from '../stores/useWorkflowStore'
import { computed } from 'vue'

interface Props {
  connection: Connection
  sourcePos?: [number, number]
  targetPos?: [number, number]
}

const props = defineProps<Props>()

const path = computed(() => {
  const sx = props.sourcePos?.[0] ?? 0
  const sy = props.sourcePos?.[1] ?? 0
  const tx = props.targetPos?.[0] ?? 0
  const ty = props.targetPos?.[1] ?? 0
  
  const midX = (sx + tx) / 2
  const curve = Math.abs(sx - tx) * 0.5
  
  return `M ${sx} ${sy} C ${midX} ${sy}, ${midX} ${ty}, ${tx} ${ty}`
})

const strokeColor = computed(() => {
  if (props.connection.type === 'IMAGE') return '#4a6fa0'
  if (props.connection.type === 'MASK') return '#6a4a80'
  return '#888'
})
</script>

<template>
  <path
    :d="path"
    :stroke="strokeColor"
    stroke-width="2"
    fill="none"
    stroke-linecap="round"
  />
  <circle
    :cx="targetPos?.[0]"
    :cy="targetPos?.[1]"
    r="4"
    :fill="strokeColor"
  />
</template>
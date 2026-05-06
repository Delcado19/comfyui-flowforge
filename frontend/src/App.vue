<script setup lang="ts">
import { ref } from 'vue'
import { useWorkflowStore } from './stores/useWorkflowStore'
import ComfyCanvas from './components/ComfyCanvas.vue'

const store = useWorkflowStore()
const fileInput = ref<HTMLInputElement | null>(null)

function openFile() {
  fileInput.value?.click()
}

function onFileSelected(e: Event) {
  const input = e.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  
  const reader = new FileReader()
  reader.onload = () => {
    try {
      const data = JSON.parse(reader.result as string)
      store.loadWorkflow(data)
    } catch (err) {
      alert('Invalid workflow file')
    }
  }
  reader.readAsText(file)
}

async function layout() {
  if (!store.workflow) return

  try {
    const response = await fetch('/layout', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(store.workflow)
    })
    const data = await response.json()
    if (!response.ok) {
      throw new Error(data.error || 'Layout request failed')
    }
    store.loadWorkflow(data)
  } catch (err) {
    alert('Layout failed: ' + err)
  }
}

function save() {
  if (!store.workflow) return

  const data = store.workflow
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = 'workflow.json'
  a.click()
  URL.revokeObjectURL(url)
}
</script>

<template>
  <div class="app">
    <div class="toolbar">
      <button @click="openFile">Open</button>
      <button @click="layout">Layout</button>
      <button @click="save">Save</button>
      <span class="zoom-info">Zoom: {{ Math.round(store.scale * 100) }}%</span>
    </div>
    <ComfyCanvas class="canvas" />
    <input ref="fileInput" type="file" accept=".json" @change="onFileSelected" style="display: none" />
  </div>
</template>

<style>
* {
  margin: 0;
  padding: 0;
  box-sizing: border-box;
}
body {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
  background: #1a1a1a;
  color: #ddd;
}
.app {
  display: flex;
  flex-direction: column;
  height: 100vh;
}
.toolbar {
  display: flex;
  gap: 8px;
  padding: 8px;
  background: #222;
  border-bottom: 1px solid #333;
}
.toolbar button {
  padding: 6px 12px;
  background: #333;
  border: 1px solid #444;
  color: #ddd;
  cursor: pointer;
  border-radius: 4px;
}
.toolbar button:hover {
  background: #444;
}
.zoom-info {
  margin-left: auto;
  color: #888;
  font-size: 12px;
  padding: 6px 12px;
}
.canvas {
  flex: 1;
}
</style>

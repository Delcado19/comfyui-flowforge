import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      '/health': apiProxyTarget(),
      '/layout': apiProxyTarget(),
      '/optimize': apiProxyTarget(),
    },
  }
})

function apiProxyTarget(): string {
  const port = Number.parseInt(process.env.FLOWFORGE_API_PORT ?? '8000', 10)
  return `http://127.0.0.1:${Number.isFinite(port) ? port : 8000}`
}

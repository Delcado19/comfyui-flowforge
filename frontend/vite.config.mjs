import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

const esbuildTsconfigRaw = JSON.stringify({
  compilerOptions: {
    target: 'ES2022',
    useDefineForClassFields: true,
  },
})

export default defineConfig({
  plugins: [vue()],
  esbuild: {
    tsconfigRaw: esbuildTsconfigRaw,
  },
  optimizeDeps: {
    esbuildOptions: {
      tsconfigRaw: esbuildTsconfigRaw,
    },
  },
  server: {
    port: 5173,
    proxy: {
      '/health': apiProxyTarget(),
      '/layout': apiProxyTarget(),
      '/optimize': apiProxyTarget(),
    },
  },
})

function apiProxyTarget() {
  const port = Number.parseInt(process.env.FLOWFORGE_API_PORT ?? '8000', 10)
  return `http://127.0.0.1:${Number.isFinite(port) ? port : 8000}`
}

import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// The build is served by FastAPI as static files, so assets are hashed and the dev
// server proxies /api to the backend rather than running a second origin.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  // Stamped into every diagnostic so "which bundle is that phone actually
  // running" is a fact in the logs, not a guess.
  define: {
    __BUILD_ID__: JSON.stringify(new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19)),
  },
  server: {
    port: 5173,
    proxy: {
      '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true },
      '/health': { target: 'http://127.0.0.1:8000', changeOrigin: true },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
  },
})

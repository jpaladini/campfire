import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Build straight into the bundle's app source so the built SPA is
// committed and the bundle deploys with zero Node toolchain.
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: '../src/static',
    emptyOutDir: true,
  },
  server: {
    proxy: {
      '/api': 'http://localhost:8000',
    },
  },
})

import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Relative base so the built dashboard can be served from a sub-path of the
// EIAR website (e.g. /research-tools/wwp/) without rebuilding.
export default defineConfig({
  plugins: [react()],
  base: './',
  server: {
    port: 5173,
    proxy: { '/api': 'http://127.0.0.1:8000' },
  },
})

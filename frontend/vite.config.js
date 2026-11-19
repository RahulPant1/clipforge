import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// /api is proxied to FastAPI so the UI and API share one origin in dev
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    host: true, // reachable from Windows browser when running inside WSL
    proxy: { '/api': 'http://localhost:8000' },
  },
})

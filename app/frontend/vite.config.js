import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    // Default host 'localhost' can bind only IPv6 (::1) on Windows; http://127.0.0.1:5173 then fails.
    // true uses Node's default bind (dual-stack friendly) so localhost and 127.0.0.1 both work.
    host: true,
    proxy: {
      '/api': { target: 'http://localhost:8000', changeOrigin: true },
      '/setup': { target: 'http://localhost:8000', changeOrigin: true },
      '/ping': { target: 'http://localhost:8000', changeOrigin: true },
      '/ws': { target: 'ws://localhost:8000', ws: true },
    },
  },
})

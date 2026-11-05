import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      // API_TARGET lets tests use their own backend, away from one that's already running.
      '/api': process.env.API_TARGET ?? 'http://127.0.0.1:8000',
    },
  },
})

import { fileURLToPath, URL } from 'node:url'

import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    port: 5173,
    host: true,
  },
  test: {
    environment: 'jsdom',
    // The forks pool times out spawning workers on Windows/OneDrive paths.
    pool: 'threads',
    testTimeout: 20_000,
    hookTimeout: 60_000,
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    css: true,
  },
})

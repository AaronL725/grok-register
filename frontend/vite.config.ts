import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  base: '/',
  build: { outDir: 'dist', emptyOutDir: true },
  server: {
    host: '127.0.0.1',
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8092',
        configure: proxy => {
          proxy.on('proxyReq', proxyRequest => proxyRequest.removeHeader('origin'))
        },
      },
      '/health': 'http://127.0.0.1:8092',
    },
  },
})

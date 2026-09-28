import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

/**
 * The frontend runs standalone: with no backend it falls back to demo data.
 *
 * When the FastAPI backend IS running, the dev server proxies /api and /ws to
 * it so there is no CORS setup and VITE_API_URL can be left empty. Set
 * VITE_API_URL / VITE_WS_URL (or VITE_PROXY_TARGET) to point somewhere else.
 */
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const target = env.VITE_PROXY_TARGET || 'http://127.0.0.1:8000'

  return {
    plugins: [react()],
    server: {
      port: 5173,
      strictPort: false,
      proxy: {
        '/api': { target, changeOrigin: true },
        '/ws': { target, ws: true },
      },
    },
    build: {
      outDir: 'dist',
      sourcemap: false,
      rollupOptions: {
        output: {
          manualChunks: {
            react: ['react', 'react-dom', 'react-router-dom'],
            charts: ['recharts'],
          },
        },
      },
    },
  }
})

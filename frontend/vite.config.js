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

    // GitHub Pages serves the site from https://<user>.github.io/<repo>/,
    // so every asset URL and router route needs the repo name as a prefix.
    // Set to '/' for local dev and for any root-hosted deploy (Vercel, etc).
    base: env.VITE_BASE_PATH || (env.GITHUB_ACTIONS ? '/Log-Anomaly-Detector/' : '/'),

    server: {
      // 'localhost' resolves to IPv6 ::1 first on Windows, which is what the
      // documented http://localhost:5173 URL needs. Node cannot listen on
      // ::1 and 127.0.0.1 in one server, so use VITE_HOST=127.0.0.1 if you
      // prefer the IPv4 literal (or 0.0.0.0 to reach it from a phone).
      host: env.VITE_HOST || 'localhost',
      // Fail loudly instead of silently sliding to 5174, so the documented
      // URL http://localhost:5173 is always the one that answers.
      strictPort: true,
      port: 5173,
      // Open the dashboard as soon as the dev server is ready.
      open: env.VITE_OPEN_BROWSER !== 'false',
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

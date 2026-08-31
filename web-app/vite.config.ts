import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: {
      // The frontend never computes a number (UI_SPEC.md §0) — every API
      // call goes through this proxy to the real FastAPI app (recon.cli
      // serve), never to a mock. No mock data in the frontend at any point.
      //
      // Only /api/* and the one pre-P6 endpoint this app still calls
      // (/report, for "whatever the latest run was") are proxied — NOT a
      // bare /ask, even though recon/api.py serves one. A bare path here
      // shadows any client-side route of the same name (react-router's
      // /ask would otherwise get proxied straight to the backend, which
      // 405s a GET) — found exactly that way while building this screen.
      // src/routes/AskConsole.tsx calls /api/ask instead, so this isn't needed.
      '/api': 'http://127.0.0.1:8000',
      '/report': 'http://127.0.0.1:8000',
    },
  },
})

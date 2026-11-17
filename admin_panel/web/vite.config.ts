import { resolve } from "node:path"
import tailwindcss from "@tailwindcss/vite"
import react from "@vitejs/plugin-react"
import { defineConfig } from "vite"

// The built page goes to admin_panel/static, which the bot serves (admin_panel/server.py).
// `npm run dev` serves the page with hot reload and sends /api to the panel's dev server
// (python -m admin_panel.devserver, port 8781).
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": resolve(import.meta.dirname, "./src"),
    },
  },
  build: {
    outDir: resolve(import.meta.dirname, "../static"),
    emptyOutDir: true,
    chunkSizeWarningLimit: 2000,
  },
  server: {
    port: 5180,
    proxy: { "/api": "http://127.0.0.1:8781" },
  },
})

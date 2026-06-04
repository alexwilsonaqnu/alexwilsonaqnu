import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Build into ../web/dist (served by the Node backend in production).
// In dev, proxy /api to the backend on :8787 so the SSE chat stream works.
const BACKEND = process.env.FPNA_WEB_PORT ? `http://localhost:${process.env.FPNA_WEB_PORT}` : "http://localhost:8787";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": { target: BACKEND, changeOrigin: true },
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
});

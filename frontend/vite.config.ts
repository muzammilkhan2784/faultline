import { fileURLToPath, URL } from "node:url";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const API = process.env.VITE_API_TARGET ?? "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  server: {
    port: 5173,
    proxy: { "/api": { target: API, changeOrigin: true } },
  },
  preview: {
    port: 4173,
    proxy: { "/api": { target: API, changeOrigin: true } },
  },
  build: {
    sourcemap: true,
    rollupOptions: {
      output: {
        // Cytoscape is large and changes rarely: keep it out of the app chunk.
        manualChunks: { cytoscape: ["cytoscape", "cytoscape-dagre"] },
      },
    },
  },
});

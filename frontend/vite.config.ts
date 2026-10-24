/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

const API = process.env.GRIDPULSE_API_URL ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  envDir: "..",
  // MapLibre's worker is an ES module (it imports a shared chunk), so bundle workers as ES modules.
  worker: { format: "es" },
  server: {
    port: 5173,
    proxy: { "/api": { target: API, changeOrigin: false } },
  },
  preview: {
    port: 4173,
    proxy: { "/api": { target: API, changeOrigin: false } },
  },
  test: {
    environment: "jsdom",
    // Tests never use a developer's real map keys from the root .env; Google-specific tests stub their own.
    env: { VITE_GOOGLE_MAPS_API_KEY: "", VITE_GOOGLE_MAPS_TRAFFIC_LAYER: "false" },
    setupFiles: ["./src/test/setup.ts"],
    css: false,
  },
});

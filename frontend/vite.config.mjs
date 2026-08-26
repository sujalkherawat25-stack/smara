import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath, URL } from "node:url";

// JavaScript config keeps Vite's esbuild config loader out of the Windows
// parent-directory resolution path that affected the original shell.
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  server: {
    port: 5173,
    proxy: {
      "/v1": {
        target: process.env.VITE_API_URL ?? "http://localhost:8080",
        changeOrigin: true,
        ws: true,
      },
    },
  },
});

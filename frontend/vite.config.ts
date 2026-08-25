import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "path";

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      // Allows imports like: import Foo from "@/components/Foo"
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    port: 5173,
    // Proxy API requests to FastAPI backend during development.
    // Avoids CORS issues when running frontend and backend separately.
    proxy: {
      "/v1": {
        target: process.env.VITE_API_URL ?? "http://localhost:8000",
        changeOrigin: true,
        ws: true,
      },
    },
  },
});

import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";

// JavaScript config keeps Vite's esbuild config loader out of the Windows
// parent-directory resolution path that affected the original shell.
export default defineConfig({
  plugins: [react()],
  // The frontend is served at the canonical Smara root. Keep this configurable
  // for local previews, while production builds use "/".
  base: process.env.VITE_BASE_PATH ?? "/",
  resolve: {
    // Resolve from the config's working directory.  `fileURLToPath(new URL())`
    // can be mis-parsed by esbuild on Windows and resolve the alias outside
    // the project (making production builds fail with an access-denied path).
    alias: { "@": path.resolve(process.cwd(), "src") },
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

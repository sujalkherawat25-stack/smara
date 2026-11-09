// Preview only: `npm run preview:ui` serves the real UI with the Tauri backend replaced by sample data (preview/mock-*.ts).
import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const here = (file: string) => fileURLToPath(new URL(file, import.meta.url));

export default defineConfig({
  root: here("../"),
  plugins: [react()],
  clearScreen: false,
  resolve: {
    alias: [
      { find: /^@tauri-apps\/api\/core$/, replacement: here("./mock-core.ts") },
      { find: /^@tauri-apps\/api\/event$/, replacement: here("./mock-event.ts") },
      { find: /^@tauri-apps\/api\/app$/, replacement: here("./mock-app.ts") },
    ],
  },
  server: { port: 1421, strictPort: true },
});

import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// index.html is served by the backend at `/`; hashed assets live under `/app/*`.
// base stays "/" so asset URLs come out as absolute `/app/...` paths.
export default defineConfig({
  plugins: [react()],
  base: "/",
  build: {
    outDir: "../src/cortex/server/webdist",
    emptyOutDir: true,
    assetsDir: "app",
    rollupOptions: {
      output: {
        manualChunks: {
          react: ["react", "react-dom"],
          codemirror: [
            "codemirror",
            "@codemirror/view",
            "@codemirror/state",
            "@codemirror/language",
            "@codemirror/commands",
            "@codemirror/lang-markdown",
            "@codemirror/theme-one-dark",
          ],
          marked: ["marked"],
        },
      },
    },
  },
  server: {
    proxy: {
      "/api": "http://localhost:8642",
      "/ws": { target: "ws://localhost:8642", ws: true },
      "/assets": "http://localhost:8642",
      "/health": "http://localhost:8642",
    },
  },
});

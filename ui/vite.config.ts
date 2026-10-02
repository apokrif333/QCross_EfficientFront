import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
const proxy = {
  "/api": {
    target: process.env.QCROSS_API_URL || "http://127.0.0.1:8000",
    changeOrigin: true,
  },
};
export default defineConfig({
  plugins: [react()],
  server: { port: 5173, strictPort: true, proxy },
  preview: { proxy },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    exclude: ["e2e/**", "node_modules/**"],
    css: true,
  },
});

import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  base: "/",
  plugins: [react()],
  build: {
    outDir: "dist-renderer",
    emptyOutDir: true,
    sourcemap: false,
    rolldownOptions: {
      output: {
        assetFileNames: "assets/[name][extname]",
        chunkFileNames: "assets/[name].js",
        codeSplitting: {
          groups: [
            {
              name: "vendor",
              test: /[\\/]node_modules[\\/]|[\\/]src[\\/]renderer[\\/]creation-output-grant-state\.ts$/u,
            },
          ],
        },
        entryFileNames: "assets/index.js",
      },
    },
  },
});

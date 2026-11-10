import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Ports and origins live in the project's config.json ("review" section), shared with the
// Python server: `port` is where it listens, `dev_origins[0]` is this dev server's origin.
const projectConfig = JSON.parse(
  readFileSync(new URL("../../config.json", import.meta.url), "utf-8")
);
const { host, port, dev_origins: devOrigins } = projectConfig.review;
// REVIEW_API overrides the target when the Python server moved to another port.
const apiTarget = process.env.REVIEW_API ?? `http://${host}:${port}`;

export default defineConfig({
  plugins: [react()],
  base: "/",
  resolve: {
    // Tools import the shell only through this one entry (no deep imports).
    alias: { "@shell": fileURLToPath(new URL("./src/shell/index.js", import.meta.url)) },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
  server: {
    port: Number(new URL(devOrigins[0]).port),
    strictPort: true,
    proxy: {
      "/api": apiTarget,
    },
  },
});

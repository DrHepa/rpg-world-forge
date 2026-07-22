import path from "node:path";
import { fileURLToPath } from "node:url";

import { build } from "esbuild";

const appRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

const common = {
  absWorkingDir: appRoot,
  bundle: true,
  external: ["electron"],
  legalComments: "none",
  logLevel: "info",
  minify: false,
  platform: "node",
  sourcemap: true,
  target: "node24",
};

await Promise.all([
  build({
    ...common,
    entryPoints: ["src/main/index.ts"],
    format: "cjs",
    outfile: "dist-electron/main/index.cjs",
  }),
  build({
    ...common,
    entryPoints: ["src/preload/index.ts"],
    format: "cjs",
    outfile: "dist-electron/preload/index.cjs",
  }),
]);

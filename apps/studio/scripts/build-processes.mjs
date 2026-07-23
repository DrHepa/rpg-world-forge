import { rm } from "node:fs/promises";
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
  sourcemap: false,
  target: "node24",
};

export async function cleanProcessOutput(root = appRoot) {
  await rm(path.join(root, "dist-electron"), {
    force: true,
    recursive: true,
  });
}

async function main() {
  await cleanProcessOutput();
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
}

if (
  process.argv[1] &&
  path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)
) {
  await main();
}

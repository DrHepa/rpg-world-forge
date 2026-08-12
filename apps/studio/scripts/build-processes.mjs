import { cp, rm } from "node:fs/promises";
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
      entryPoints: ["src/main/generic-asset-runtime.ts"],
      format: "cjs",
      outfile: "dist-electron/main/generic-asset-runtime.cjs",
    }),
    build({
      ...common,
      entryPoints: ["src/main/generic-runtime-contracts.ts"],
      format: "cjs",
      outfile: "dist-electron/main/generic-runtime-contracts.cjs",
    }),
    build({
      ...common,
      entryPoints: ["src/main/generic-materialization-contracts.ts"],
      format: "cjs",
      outfile: "dist-electron/main/generic-materialization-contracts.cjs",
    }),
    build({
      ...common,
      entryPoints: ["src/main/generic-game-runtime-bundle.ts"],
      format: "cjs",
      outfile: "dist-electron/main/generic-game-runtime-bundle.cjs",
    }),
    build({
      ...common,
      entryPoints: ["src/main/generic-game-package.ts"],
      format: "cjs",
      outfile: "dist-electron/main/generic-game-package.cjs",
    }),
    build({
      ...common,
      entryPoints: ["src/main/generic-game-persistence.ts"],
      format: "cjs",
      outfile: "dist-electron/main/generic-game-persistence.cjs",
    }),
    build({
      ...common,
      entryPoints: ["src/main/generic-headless-evidence.ts"],
      format: "cjs",
      outfile: "dist-electron/main/generic-headless-evidence.cjs",
    }),
    build({
      ...common,
      entryPoints: ["src/preload/index.ts"],
      format: "cjs",
      outfile: "dist-electron/preload/index.cjs",
    }),
    build({
      ...common,
      entryPoints: ["src/authority-modal/preload.ts"],
      format: "cjs",
      outfile: "dist-electron/authority-modal/preload.cjs",
    }),
    cp("src/authority-modal/index.html", "dist-electron/authority-modal/index.html"),
    cp("src/authority-modal/renderer.js", "dist-electron/authority-modal/renderer.js"),
    cp("src/authority-modal/style.css", "dist-electron/authority-modal/style.css"),
  ]);
}

if (
  process.argv[1] &&
  path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)
) {
  await main();
}

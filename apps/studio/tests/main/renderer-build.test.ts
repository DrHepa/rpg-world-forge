import { createHash } from "node:crypto";
import { mkdtemp, readFile, readdir, rm, stat } from "node:fs/promises";
import os from "node:os";
import path from "node:path";

import { afterEach, describe, expect, it } from "vitest";
import { build } from "vite";

const studioRoot = path.resolve(import.meta.dirname, "../..");
const rendererChunkLimitBytes = 500_000;
const temporaryRoots: string[] = [];

type BuiltFile = {
  bytes: number;
  path: string;
  sha256: string;
};

afterEach(async () => {
  await Promise.all(
    temporaryRoots.splice(0).map(async (root) => rm(root, { force: true, recursive: true })),
  );
});

async function buildRenderer(label: string): Promise<BuiltFile[]> {
  const outDir = await mkdtemp(path.join(os.tmpdir(), `world-forge-renderer-${label}-`));
  temporaryRoots.push(outDir);
  await build({
    build: {
      emptyOutDir: true,
      outDir,
    },
    configFile: path.join(studioRoot, "vite.config.ts"),
    logLevel: "silent",
    root: studioRoot,
  });

  const assetNames = (await readdir(path.join(outDir, "assets"))).sort();
  const relativePaths = ["index.html", ...assetNames.map((name) => `assets/${name}`)];
  return Promise.all(
    relativePaths.map(async (relativePath) => {
      const absolutePath = path.join(outDir, ...relativePath.split("/"));
      const [bytes, document] = await Promise.all([stat(absolutePath), readFile(absolutePath)]);
      return {
        bytes: bytes.size,
        path: relativePath,
        sha256: createHash("sha256").update(document).digest("hex"),
      };
    }),
  );
}

describe("renderer production build", () => {
  it("emits a deterministic closed chunk inventory below Vite's 500 kB boundary", async () => {
    const first = await buildRenderer("first");
    const second = await buildRenderer("second");

    expect(first).toEqual(second);
    expect(first.map((entry) => entry.path)).toEqual([
      "index.html",
      "assets/index.css",
      "assets/index.js",
      "assets/vendor.js",
    ]);
    const chunks = first.filter((entry) => entry.path.endsWith(".js"));
    expect(chunks).toHaveLength(2);
    for (const chunk of chunks) {
      expect(chunk.bytes, chunk.path).toBeLessThanOrEqual(rendererChunkLimitBytes);
    }
  });

  it("keeps every renderer build output in the electron-builder allowlist", async () => {
    const packageDocument = JSON.parse(
      await readFile(path.join(studioRoot, "package.json"), "utf8"),
    ) as { build: { files: string[] } };
    const rendererFiles = packageDocument.build.files.filter((entry) =>
      entry.startsWith("dist-renderer/"),
    );

    expect(rendererFiles).toEqual([
      "dist-renderer/index.html",
      "dist-renderer/assets/index.css",
      "dist-renderer/assets/index.js",
      "dist-renderer/assets/vendor.js",
    ]);
  });
});

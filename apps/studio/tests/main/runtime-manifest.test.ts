import { chmod, mkdir, mkdtemp, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";

import { afterEach, describe, expect, it } from "vitest";

import { resolveForgeServiceLaunch } from "../../src/main/runtime-manifest";

const roots: string[] = [];

afterEach(async () => {
  const { rm } = await import("node:fs/promises");
  await Promise.all(roots.splice(0).map(async (root) => rm(root, { recursive: true, force: true })));
});

async function temporaryRoot(): Promise<string> {
  const root = await mkdtemp(path.join(os.tmpdir(), "rwf-studio-runtime-"));
  roots.push(root);
  return root;
}

describe("runtime manifest", () => {
  it("requires an explicitly configured absolute interpreter in development", async () => {
    await expect(
      resolveForgeServiceLaunch({
        packaged: false,
        resourcesPath: "/unused",
        dataDir: path.join(os.tmpdir(), "rwf-data"),
        environment: {},
      }),
    ).rejects.toThrow(/RWF_STUDIO_DEV_PYTHON/u);
  });

  it("builds fixed isolated service arguments and strips injection environment", async () => {
    const dataDir = path.join(os.tmpdir(), "rwf-data");
    const spec = await resolveForgeServiceLaunch({
      packaged: false,
      resourcesPath: "/unused",
      dataDir,
      environment: {
        RWF_STUDIO_DEV_PYTHON: process.execPath,
        NODE_OPTIONS: "--inspect",
        PYTHONPATH: "/attacker",
        HOME: os.homedir(),
      },
    });

    expect(spec.executable).toBe(process.execPath);
    expect(spec.args).toEqual(["-I", "-m", "worldforge.studio", "--data-dir", dataDir]);
    expect(spec.env).not.toHaveProperty("NODE_OPTIONS");
    expect(spec.env).not.toHaveProperty("PYTHONPATH");
    expect(spec.env.PYTHONUTF8).toBe("1");
  });

  it("resolves a packaged executable only from a closed manifest under resources", async () => {
    const root = await temporaryRoot();
    const executable = path.join(root, "runtime/python/bin/python3");
    await mkdir(path.dirname(executable), { recursive: true });
    await writeFile(executable, "fixture", "utf8");
    await chmod(executable, 0o755);
    await writeManifest(root, {});

    const spec = await resolveForgeServiceLaunch({
      packaged: true,
      resourcesPath: root,
      dataDir: path.join(root, "data"),
      environment: { PATH: "/attacker" },
      platform: "linux",
      architecture: "x64",
    });

    expect(spec.executable).toBe(executable);
    expect(spec.env).not.toHaveProperty("PATH");
  });

  it("rejects unknown manifest fields and traversal paths", async () => {
    const unknownRoot = await temporaryRoot();
    await writeManifest(unknownRoot, { unexpected: true });
    await expectPackagedFailure(unknownRoot, /unknown or missing/u);

    const traversalRoot = await temporaryRoot();
    await writeManifest(traversalRoot, {}, "../python");
    await expectPackagedFailure(traversalRoot, /not portable/u);
  });
});

async function writeManifest(
  root: string,
  extras: Record<string, unknown>,
  linuxPath = "runtime/python/bin/python3",
): Promise<void> {
  await writeFile(
    path.join(root, "runtime-manifest.json"),
    `${JSON.stringify({
      format: "rpg-world-forge.studio_runtime_manifest",
      version: 1,
      python: {
        module: "worldforge.studio",
        linux_x64: linuxPath,
        win32_x64: "runtime/python/python.exe",
      },
      ...extras,
    })}\n`,
    "utf8",
  );
}

async function expectPackagedFailure(root: string, pattern: RegExp): Promise<void> {
  await expect(
    resolveForgeServiceLaunch({
      packaged: true,
      resourcesPath: root,
      dataDir: path.join(root, "data"),
      platform: "linux",
      architecture: "x64",
    }),
  ).rejects.toThrow(pattern);
}

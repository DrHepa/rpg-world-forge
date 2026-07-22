import { chmod, mkdir, mkdtemp, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";

import { afterEach, describe, expect, it } from "vitest";

import {
  CODEX_VERSION,
  resolveCodexRuntime,
  resolveForgeServiceLaunch,
} from "../../src/main/runtime-manifest";

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
  it("requires explicitly configured absolute development runtimes", async () => {
    const options = {
      packaged: false,
      resourcesPath: "/unused",
      dataDir: path.join(os.tmpdir(), "rwf-data"),
      environment: {},
    };
    await expect(resolveForgeServiceLaunch(options)).rejects.toThrow(/RWF_STUDIO_DEV_PYTHON/u);
    await expect(resolveCodexRuntime(options)).rejects.toThrow(/RWF_STUDIO_DEV_CODEX/u);
  });

  it("builds fixed isolated service arguments and resolves canonical Codex binaries", async () => {
    const dataDir = path.join(os.tmpdir(), "rwf-data");
    const environment = {
      RWF_STUDIO_DEV_PYTHON: process.execPath,
      RWF_STUDIO_DEV_CODEX: process.execPath,
      NODE_OPTIONS: "--inspect",
      PYTHONPATH: "/attacker",
      HOME: os.homedir(),
    };
    const spec = await resolveForgeServiceLaunch({
      packaged: false,
      resourcesPath: "/unused",
      dataDir,
      environment,
    });
    const codex = await resolveCodexRuntime({
      packaged: false,
      resourcesPath: "/unused",
      dataDir,
      environment,
    });

    expect(spec.executable).toBe(process.execPath);
    expect(spec.args).toEqual(["-I", "-m", "worldforge.studio", "--data-dir", dataDir]);
    expect(spec.env).not.toHaveProperty("NODE_OPTIONS");
    expect(spec.env).not.toHaveProperty("PYTHONPATH");
    expect(spec.env.PYTHONUTF8).toBe("1");
    expect(codex).toEqual({
      codexExecutable: process.execPath,
      pythonExecutable: process.execPath,
      version: CODEX_VERSION,
    });
  });

  it("resolves packaged Python and Codex only from a closed manifest under resources", async () => {
    const root = await temporaryRoot();
    const python = path.join(root, "runtime/python/linux-x64/bin/python3");
    const codex = path.join(root, "runtime/codex/linux-x64/codex");
    await createExecutable(python);
    await createExecutable(codex);
    await writeProtocolManifest(root);
    await writeManifest(root, {});

    const spec = await resolveForgeServiceLaunch({
      packaged: true,
      resourcesPath: root,
      dataDir: path.join(root, "data"),
      environment: { PATH: "/attacker" },
      platform: "linux",
      architecture: "x64",
    });
    const runtime = await resolveCodexRuntime({
      packaged: true,
      resourcesPath: root,
      dataDir: path.join(root, "data"),
      platform: "linux",
      architecture: "x64",
    });

    expect(spec.executable).toBe(python);
    expect(spec.env).not.toHaveProperty("PATH");
    expect(runtime).toEqual({
      codexExecutable: codex,
      pythonExecutable: python,
      version: CODEX_VERSION,
    });
  });

  it("rejects unknown manifest fields, traversal paths, and incompatible provenance", async () => {
    const unknownRoot = await temporaryRoot();
    await writeManifest(unknownRoot, { unexpected: true });
    await expectPackagedFailure(unknownRoot, /unknown or missing/u);

    const traversalRoot = await temporaryRoot();
    await writeManifest(traversalRoot, {}, "../python");
    await expectPackagedFailure(traversalRoot, /not portable/u);

    const provenanceRoot = await temporaryRoot();
    await createExecutable(path.join(provenanceRoot, "runtime/python/linux-x64/bin/python3"));
    await createExecutable(path.join(provenanceRoot, "runtime/codex/linux-x64/codex"));
    await writeManifest(provenanceRoot, {});
    await writeProtocolManifest(provenanceRoot, { codex_cli_version: "0.145.0" });
    await expect(
      resolveCodexRuntime({
        packaged: true,
        resourcesPath: provenanceRoot,
        dataDir: path.join(provenanceRoot, "data"),
        platform: "linux",
        architecture: "x64",
      }),
    ).rejects.toThrow(/provenance is incompatible/u);
  });
});

async function createExecutable(filename: string): Promise<void> {
  await mkdir(path.dirname(filename), { recursive: true });
  await writeFile(filename, "fixture", "utf8");
  await chmod(filename, 0o755);
}

async function writeManifest(
  root: string,
  extras: Record<string, unknown>,
  linuxPythonPath = "runtime/python/linux-x64/bin/python3",
): Promise<void> {
  await writeFile(
    path.join(root, "runtime-manifest.json"),
    `${JSON.stringify({
      codex: {
        linux_arm64: "runtime/codex/linux-arm64/codex",
        linux_x64: "runtime/codex/linux-x64/codex",
        version: CODEX_VERSION,
        win32_arm64: "runtime/codex/win32-arm64/codex.exe",
        win32_x64: "runtime/codex/win32-x64/codex.exe",
      },
      codex_protocol: {
        manifest: "protocol/codex-app-server-0.144.6/manifest.json",
        version: CODEX_VERSION,
      },
      format: "rpg-world-forge.studio_runtime_manifest",
      python: {
        linux_arm64: "runtime/python/linux-arm64/bin/python3",
        linux_x64: linuxPythonPath,
        mcp_module: "worldforge.studio.mcp_server",
        service_module: "worldforge.studio",
        win32_arm64: "runtime/python/win32-arm64/python.exe",
        win32_x64: "runtime/python/win32-x64/python.exe",
      },
      version: 2,
      ...extras,
    })}\n`,
    "utf8",
  );
}

async function writeProtocolManifest(
  root: string,
  overrides: Record<string, unknown> = {},
): Promise<void> {
  const filename = path.join(root, "protocol/codex-app-server-0.144.6/manifest.json");
  await mkdir(path.dirname(filename), { recursive: true });
  await writeFile(
    filename,
    `${JSON.stringify({
      artifacts: {
        json_schema: {
          bytes: 2_719_809,
          files: 267,
          sha256: "fe9e9099c388569380a5595e75015321be54bf2215885c5e0a0696f6c717b81d",
        },
        typescript: {
          bytes: 322_075,
          files: 598,
          sha256: "125dc17b4ef299a13428dd348c26fbc2c5436cbade0da19c2087217da90931f6",
        },
      },
      codex_cli_version: CODEX_VERSION,
      commands: {},
      experimental: false,
      format: "rpg-world-forge.codex_app_server_protocol_provenance",
      format_version: 1,
      mcp_protocol_version: "2025-11-25",
      ...overrides,
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

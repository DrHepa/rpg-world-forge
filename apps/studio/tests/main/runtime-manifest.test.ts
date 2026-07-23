import { createHash } from "node:crypto";
import {
  chmod,
  link,
  mkdir,
  mkdtemp,
  readFile,
  symlink,
  writeFile,
} from "node:fs/promises";
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
  it("ships only the two audited x64 target declarations", async () => {
    const manifest = JSON.parse(
      await readFile(
        path.resolve(import.meta.dirname, "../../resources/runtime-manifest.json"),
        "utf8",
      ),
    ) as {
      version: number;
      python: Record<string, unknown>;
      codex: Record<string, unknown>;
      package_manifest: Record<string, unknown>;
    };
    expect(manifest.version).toBe(3);
    expect(Object.keys(manifest.python).sort()).toEqual([
      "linux_x64",
      "mcp_module",
      "service_module",
      "win32_x64",
    ]);
    expect(Object.keys(manifest.codex).sort()).toEqual([
      "linux_x64",
      "version",
      "win32_x64",
    ]);
    expect(manifest.package_manifest).toEqual({
      format_version: 1,
      path: "runtime-package-manifest.json",
    });
  });

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
    const codex = path.join(root, "runtime/codex/linux-x64/bin/codex");
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
    await expectPackagedFailure(unknownRoot, /bytes are not canonical/u);

    const traversalRoot = await temporaryRoot();
    await writeManifest(traversalRoot, {}, "../python");
    await expectPackagedFailure(traversalRoot, /bytes are not canonical/u);

    const provenanceRoot = await temporaryRoot();
    await createExecutable(path.join(provenanceRoot, "runtime/python/linux-x64/bin/python3"));
    await createExecutable(path.join(provenanceRoot, "runtime/codex/linux-x64/bin/codex"));
    await writeProtocolManifest(provenanceRoot, { codex_cli_version: "0.145.0" });
    await writeManifest(provenanceRoot, {});
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

  it("rejects resealed launch redirects and package component tampering", async () => {
    const redirected = await temporaryRoot();
    await createExecutable(path.join(redirected, "runtime/python/linux-x64/bin/python3"));
    await createExecutable(path.join(redirected, "runtime/codex/linux-x64/bin/codex"));
    await writeProtocolManifest(redirected);
    await writeManifest(redirected, {}, "runtime/codex/linux-x64/bin/codex");
    await expectPackagedFailure(redirected, /bytes are not canonical/u);

    const componentTamper = await temporaryRoot();
    await createExecutable(path.join(componentTamper, "runtime/python/linux-x64/bin/python3"));
    await createExecutable(path.join(componentTamper, "runtime/codex/linux-x64/bin/codex"));
    await writeProtocolManifest(componentTamper);
    await writeManifest(componentTamper, {});
    await mutatePackageManifest(componentTamper, (manifest) => {
      const inventory = manifest.inventory as Array<Record<string, unknown>>;
      const codex = inventory.find(
        (entry) => entry.path === "runtime/codex/linux-x64/bin/codex",
      );
      if (codex) {
        codex.component = "forge";
      }
    });
    await expectPackagedFailure(componentTamper, /semantic contract/u);
  });

  it("rejects formatted and duplicate-key runtime manifests after inventory resealing", async () => {
    for (const variant of ["formatted", "duplicate-key"] as const) {
      const root = await temporaryRoot();
      await createExecutable(path.join(root, "runtime/python/linux-x64/bin/python3"));
      await createExecutable(path.join(root, "runtime/codex/linux-x64/bin/codex"));
      await writeProtocolManifest(root);
      await writeManifest(root, {});
      const filename = path.join(root, "runtime-manifest.json");
      const parsed = JSON.parse(await readFile(filename, "utf8")) as Record<string, unknown>;
      const bytes =
        variant === "formatted"
          ? Buffer.from(`${JSON.stringify(parsed, null, 2)}\n`, "utf8")
          : Buffer.from(
              canonicalJsonBytes(parsed)
                .toString("utf8")
                .replace('"version":3}\n', '"version":3,"version":3}\n'),
              "utf8",
            );
      await writeFile(filename, bytes);
      await resealRuntimeManifestInventory(root, bytes);
      await expectPackagedFailure(root, /bytes are not canonical/u);
    }
  });

  it("binds packaged executables to the package inventory bytes", async () => {
    const root = await temporaryRoot();
    const python = path.join(root, "runtime/python/linux-x64/bin/python3");
    await createExecutable(python);
    await createExecutable(path.join(root, "runtime/codex/linux-x64/bin/codex"));
    await writeProtocolManifest(root);
    await writeManifest(root, {});
    await writeFile(python, "tampered", "utf8");
    await expectPackagedFailure(root, /does not match its package inventory/u);
  });

  it("descriptor-reads and authenticates the packaged Linux normalization receipt", async () => {
    const canonical = await readFile(
      path.resolve(
        import.meta.dirname,
        "../../packaging/runtime-archive-normalization-linux-x64.json",
      ),
    );
    const accepted = await linuxPbsPackageRoot(canonical);
    await expectPackagedFailure(accepted, /does not match its package inventory/u);

    const altered = Buffer.from(canonical);
    altered[altered.length - 2] ^= 1;
    const noncanonical = Buffer.concat([
      canonical.subarray(0, canonical.indexOf(0x3a) + 1),
      Buffer.from(" ", "utf8"),
      canonical.subarray(canonical.indexOf(0x3a) + 1, canonical.length - 1),
    ]);
    expect(noncanonical).toHaveLength(canonical.length);
    const duplicate = Buffer.from('{"format":"x","format":"x"}\n', "utf8");
    for (const [label, bytes] of [
      ["altered", altered],
      ["noncanonical", noncanonical],
      ["duplicate", duplicate],
    ] as const) {
      const root = await linuxPbsPackageRoot(bytes);
      await expectPackagedFailure(root, /normalization/u);
      expect(label).toBeTruthy();
    }

    const missing = await linuxPbsPackageRoot();
    await expectPackagedFailure(missing, /ENOENT|no such file/u);

    const hardlinked = await linuxPbsPackageRoot();
    const hardlinkSource = path.join(hardlinked, "receipt-source.json");
    await writeFile(hardlinkSource, canonical);
    await link(hardlinkSource, normalizationFilename(hardlinked));
    await expectPackagedFailure(hardlinked, /bounded standalone regular file/u);

    const linked = await linuxPbsPackageRoot();
    const symlinkSource = path.join(linked, "receipt-source.json");
    await writeFile(symlinkSource, canonical);
    await symlink(symlinkSource, normalizationFilename(linked));
    await expectPackagedFailure(linked, /bounded standalone regular file/u);
  });

  it("descriptor-reads and inventory-binds the exact packaged runtime source provenance", async () => {
    const canonical = await readFile(
      path.resolve(import.meta.dirname, "../../packaging/runtime-sources.json"),
    );
    const accepted = await verifiedLinuxPackageRoot(canonical);
    await expectPackagedFailure(accepted, /does not match its package inventory/u);

    const altered = Buffer.from(canonical);
    altered[altered.length - 2] ^= 1;
    const alteredRoot = await verifiedLinuxPackageRoot(altered);
    await expectPackagedFailure(alteredRoot, /semantic contract/u);

    const missing = await verifiedLinuxPackageRoot();
    await expectPackagedFailure(missing, /ENOENT|no such file/u);

    const hardlinked = await verifiedLinuxPackageRoot();
    const hardlinkSource = path.join(hardlinked, "runtime-sources-source.json");
    await writeFile(hardlinkSource, canonical);
    await link(hardlinkSource, path.join(hardlinked, "runtime-sources.json"));
    await expectPackagedFailure(hardlinked, /bounded standalone regular file/u);

    const linked = await verifiedLinuxPackageRoot();
    const symlinkSource = path.join(linked, "runtime-sources-source.json");
    await writeFile(symlinkSource, canonical);
    await symlink(symlinkSource, path.join(linked, "runtime-sources.json"));
    await expectPackagedFailure(linked, /bounded standalone regular file/u);

    const resealed = await verifiedLinuxPackageRoot(canonical);
    await mutatePackageManifest(resealed, (manifest) => {
      const inventory = manifest.inventory as Array<Record<string, unknown>>;
      const provenance = inventory.find(
        (entry) => entry.path === "runtime-sources.json",
      );
      if (!provenance) {
        throw new Error("runtime source provenance inventory entry is missing");
      }
      provenance.sha256 = "0".repeat(64);
    });
    await expectPackagedFailure(resealed, /semantic contract/u);
  });

  it("fails closed for unsupported ARM64 package claims", async () => {
    const root = await temporaryRoot();
    await writeManifest(root, {});
    await expect(
      resolveForgeServiceLaunch({
        packaged: true,
        resourcesPath: root,
        dataDir: path.join(root, "data"),
        platform: "linux",
        architecture: "arm64",
      }),
    ).rejects.toThrow(/not defined for linux-arm64/u);
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
  linuxCodexPath = "runtime/codex/linux-x64/bin/codex",
): Promise<void> {
  const manifest = {
      codex: {
        linux_x64: linuxCodexPath,
        version: CODEX_VERSION,
        win32_x64: "runtime/codex/win32-x64/bin/codex.exe",
      },
      codex_protocol: {
        manifest: "protocol/codex-app-server-0.144.6/manifest.json",
        version: CODEX_VERSION,
      },
      format: "rpg-world-forge.studio_runtime_manifest",
      package_manifest: {
        format_version: 1,
        path: "runtime-package-manifest.json",
      },
      python: {
        linux_x64: linuxPythonPath,
        mcp_module: "worldforge.studio.mcp_server",
        service_module: "worldforge.studio",
        win32_x64: "runtime/python/win32-x64/python.exe",
      },
      version: 3,
      ...extras,
    };
  await writeFile(
    path.join(root, "runtime-manifest.json"),
    canonicalJsonBytes(manifest),
  );
  await writePackageManifest(root);
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

async function writePackageManifest(root: string): Promise<void> {
  const candidates = [
    ["runtime/codex/linux-x64/bin/codex", "codex", 493],
    ["runtime/python/linux-x64/bin/python3", "python", 493],
    ["protocol/codex-app-server-0.144.6/manifest.json", "forge", 420],
    ["runtime-manifest.json", "control", 420],
  ] as const;
  const inventory: Array<Record<string, unknown>> = [];
  for (const [relativePath, component, mode] of candidates) {
    try {
      const bytes = await readFile(path.join(root, ...relativePath.split("/")));
      inventory.push({
        component,
        mode,
        path: relativePath,
        sha256: digest(bytes),
        size: bytes.length,
      });
    } catch {
      // Invalid runtime-manifest tests fail before package inventory is consulted.
    }
  }
  inventory.sort((left, right) =>
    Buffer.compare(
      Buffer.from(left.path as string, "utf8"),
      Buffer.from(right.path as string, "utf8"),
    ),
  );
  const forgeInventory = inventory.filter((entry) => entry.component === "forge");
  const packageManifest = {
    assembly_kind: "synthetic_test_fixture",
    format: "rpg-world-forge.studio_runtime_package_manifest",
    format_version: 1,
    inventory,
    launch: {
      codex: "runtime/codex/linux-x64/bin/codex",
      mcp_module: "worldforge.studio.mcp_server",
      python: "runtime/python/linux-x64/bin/python3",
      service_module: "worldforge.studio",
    },
    open_blocker_codes: ["synthetic_non_publishable_inputs"],
    redistribution_status: "blocked",
    release_ready: false,
    schema_id:
      "https://rpg-world-forge.local/schemas/studio-runtime-package-manifest.schema.json",
    source_date_epoch: 1_700_000_000,
    sources: {
      codex: {
        archive: {
          entrypoint: "package/vendor/x86_64-unknown-linux-musl/bin/codex",
          filename: "codex.tgz",
          payload_root: "package/vendor/x86_64-unknown-linux-musl",
          sha256: "d".repeat(64),
          size: 100,
        },
        version: "0.144.6",
      },
      forge: {
        inventory_sha256: digest(canonicalJsonBytes(forgeInventory)),
        version: "0.7.0",
      },
      python: {
        archive: {
          entrypoint: "python/bin/python3",
          filename: "python.tar.gz",
          payload_root: "python",
          sha256: "f".repeat(64),
          size: 100,
        },
        normalization: null,
        version: "3.12.13",
      },
      runtime_sources: null,
      runtime_sources_sha256: "0".repeat(64),
    },
    target_id: "linux-x64",
  };
  await writeFile(
    path.join(root, "runtime-package-manifest.json"),
    canonicalJsonBytes(packageManifest),
  );
}

function normalizationFilename(root: string): string {
  return path.join(
    root,
    "runtime/python/linux-x64/runtime-archive-normalization.json",
  );
}

async function linuxPbsPackageRoot(receiptBytes?: Buffer): Promise<string> {
  const root = await temporaryRoot();
  await createExecutable(path.join(root, "runtime/python/linux-x64/bin/python3"));
  await createExecutable(path.join(root, "runtime/codex/linux-x64/bin/codex"));
  await writeProtocolManifest(root);
  await writeManifest(root, {});
  const filename = path.join(root, "runtime-package-manifest.json");
  const manifest = JSON.parse(await readFile(filename, "utf8")) as Record<
    string,
    unknown
  >;
  const canonicalReceipt = await readFile(
    path.resolve(
      import.meta.dirname,
      "../../packaging/runtime-archive-normalization-linux-x64.json",
    ),
  );
  const receipt = JSON.parse(canonicalReceipt.toString("utf8")) as {
    files: Array<{
      mode: number;
      sha256: string;
      size: number;
      source: string;
    }>;
  };
  const inventory = (manifest.inventory as Array<Record<string, unknown>>).filter(
    (entry) => entry.component !== "python",
  );
  inventory.push(
    ...receipt.files.map((entry) => ({
      component: "python",
      mode: entry.mode,
      path: `runtime/python/linux-x64/${entry.source}`,
      sha256: entry.sha256,
      size: entry.size,
    })),
    {
      component: "control",
      mode: 420,
      path: "runtime/python/linux-x64/runtime-archive-normalization.json",
      sha256:
        "3c4fea7af2d435c036d412a56d7b762131e780560b339cbffe80e7637416db0e",
      size: 1_031_213,
    },
  );
  inventory.sort((left, right) =>
    Buffer.compare(
      Buffer.from(String(left.path), "utf8"),
      Buffer.from(String(right.path), "utf8"),
    ),
  );
  manifest.inventory = inventory;
  const sources = manifest.sources as Record<string, unknown>;
  sources.python = {
    archive: {
      entrypoint: "python/bin/python3",
      filename:
        "cpython-3.12.13+20260718-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz",
      payload_root: "python",
      sha256:
        "5854aa6ec71cad00334d5065633c210b2e7feb40956767a59a91791cadcf0b79",
      size: 34_199_823,
    },
    normalization: {
      archive_sha256:
        "5854aa6ec71cad00334d5065633c210b2e7feb40956767a59a91791cadcf0b79",
      format: "rpg-world-forge.studio_runtime_archive_normalization",
      format_version: 1,
      path: "runtime/python/linux-x64/runtime-archive-normalization.json",
      sha256:
        "3c4fea7af2d435c036d412a56d7b762131e780560b339cbffe80e7637416db0e",
      size: 1_031_213,
    },
    version: "3.12.13",
  };
  await writeFile(filename, canonicalJsonBytes(manifest));
  await mkdir(path.dirname(normalizationFilename(root)), { recursive: true });
  if (receiptBytes !== undefined) {
    await writeFile(normalizationFilename(root), receiptBytes);
  }
  return root;
}

async function verifiedLinuxPackageRoot(
  runtimeSourcesBytes?: Buffer,
): Promise<string> {
  const receipt = await readFile(
    path.resolve(
      import.meta.dirname,
      "../../packaging/runtime-archive-normalization-linux-x64.json",
    ),
  );
  const root = await linuxPbsPackageRoot(receipt);
  await mutatePackageManifest(root, (manifest) => {
    manifest.assembly_kind = "verified_development_runtime";
    manifest.open_blocker_codes = [
      "codex_ripgrep_static_dependency_notice_sbom_incomplete",
      "linux_bwrap_lgpl_corresponding_source_relink_build_materials_incomplete",
      "linux_bwrap_musl_provenance_incomplete",
      "pbs_zlib_ng_license_incomplete",
      "linux_berkeley_db_dbm_route_unresolved",
      "windows_vc_runtime_redistribution_authority_unresolved",
      "github_attestation_trust_root_rfc3161_verification_pending",
    ];
    const sources = manifest.sources as Record<string, unknown>;
    sources.codex = {
      archive: {
        entrypoint: "package/vendor/x86_64-unknown-linux-musl/bin/codex",
        filename: "codex-0.144.6-linux-x64.tgz",
        payload_root: "package/vendor/x86_64-unknown-linux-musl",
        sha256:
          "b6752eb2e8c10e6fcc96ac5c1c8ad8342cdb9a74504fb84686addf081a7d2868",
        size: 131_212_687,
      },
      version: "0.144.6",
    };
    sources.runtime_sources = {
      format: "rpg-world-forge.studio_runtime_sources",
      format_version: 1,
      path: "runtime-sources.json",
      sha256:
        "99419da1ccc87cb8ea6c279e7e8e6bbc1d6b4d08eb6a67ae6ac7bf66d1182414",
      size: 13_717,
    };
    sources.runtime_sources_sha256 =
      "99419da1ccc87cb8ea6c279e7e8e6bbc1d6b4d08eb6a67ae6ac7bf66d1182414";
    const inventory = manifest.inventory as Array<Record<string, unknown>>;
    inventory.push({
      component: "control",
      mode: 420,
      path: "runtime-sources.json",
      sha256:
        "99419da1ccc87cb8ea6c279e7e8e6bbc1d6b4d08eb6a67ae6ac7bf66d1182414",
      size: 13_717,
    });
    inventory.sort((left, right) =>
      Buffer.compare(
        Buffer.from(String(left.path), "utf8"),
        Buffer.from(String(right.path), "utf8"),
      ),
    );
  });
  if (runtimeSourcesBytes !== undefined) {
    await writeFile(path.join(root, "runtime-sources.json"), runtimeSourcesBytes);
  }
  return root;
}

async function mutatePackageManifest(
  root: string,
  mutate: (manifest: Record<string, unknown>) => void,
): Promise<void> {
  const filename = path.join(root, "runtime-package-manifest.json");
  const manifest = JSON.parse(await readFile(filename, "utf8")) as Record<string, unknown>;
  mutate(manifest);
  await writeFile(filename, canonicalJsonBytes(manifest));
}

async function resealRuntimeManifestInventory(root: string, bytes: Buffer): Promise<void> {
  await mutatePackageManifest(root, (manifest) => {
    const inventory = manifest.inventory as Array<Record<string, unknown>>;
    const control = inventory.find((entry) => entry.path === "runtime-manifest.json");
    if (!control) {
      throw new Error("runtime manifest control entry is missing");
    }
    control.sha256 = digest(bytes);
    control.size = bytes.length;
  });
}

function digest(bytes: Uint8Array): string {
  return createHash("sha256").update(bytes).digest("hex");
}

function canonicalJsonBytes(value: unknown): Buffer {
  return Buffer.from(`${canonicalJson(value)}\n`, "utf8");
}

function canonicalJson(value: unknown): string {
  if (
    value === null ||
    typeof value === "boolean" ||
    typeof value === "number" ||
    typeof value === "string"
  ) {
    return JSON.stringify(value);
  }
  if (Array.isArray(value)) {
    return `[${value.map((item) => canonicalJson(item)).join(",")}]`;
  }
  const record = value as Record<string, unknown>;
  return `{${Object.keys(record)
    .sort()
    .map((key) => `${JSON.stringify(key)}:${canonicalJson(record[key])}`)
    .join(",")}}`;
}

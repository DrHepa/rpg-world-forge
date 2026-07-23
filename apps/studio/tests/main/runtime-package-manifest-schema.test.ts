import { spawn, spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import Ajv2020 from "ajv/dist/2020.js";
import { describe, expect, it } from "vitest";

import {
  PACKAGE_MANIFEST_MAX_BYTES,
  PACKAGE_MANIFEST_MAX_INVENTORY,
  PACKAGE_MANIFEST_MAX_JSON_DEPTH,
  PACKAGE_MANIFEST_MAX_JSON_NODES,
  parseStrictUtf8Json,
  validateRuntimePackageManifestSemantic,
} from "../../src/main/runtime-manifest";

type JsonScalar = boolean | null | number | string;
type JsonValue = JsonObject | JsonScalar | JsonValue[];
type JsonObject = { [key: string]: JsonValue };

const testRoot = fileURLToPath(new URL(".", import.meta.url));
const studioRoot = path.resolve(testRoot, "../..");
const repoRoot = path.resolve(studioRoot, "../..");
const normalizationPath = path.join(
  studioRoot,
  "packaging/runtime-archive-normalization-linux-x64.json",
);
const normalizationPackagePath =
  "runtime/python/linux-x64/runtime-archive-normalization.json";
const schema = JSON.parse(
  readFileSync(
    path.join(studioRoot, "packaging/runtime-package-manifest.schema.json"),
    "utf8",
  ),
) as JsonObject;

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

function digest(value: Uint8Array): string {
  return createHash("sha256").update(value).digest("hex");
}

function jsonTreeMetrics(value: unknown): { depth: number; nodes: number } {
  const stack: Array<{ depth: number; value: unknown }> = [{ depth: 0, value }];
  let depth = 0;
  let nodes = 0;
  while (stack.length > 0) {
    const current = stack.pop()!;
    depth = Math.max(depth, current.depth);
    nodes += 1;
    if (Array.isArray(current.value)) {
      const children = current.value as unknown[];
      stack.push(
        ...children.map((child) => ({
          depth: current.depth + 1,
          value: child,
        })),
      );
    } else if (
      current.value !== null &&
      typeof current.value === "object"
    ) {
      const record = current.value as Record<string, unknown>;
      stack.push(
        ...Object.values(record).map((child) => ({
          depth: current.depth + 1,
          value: child,
        })),
      );
    }
  }
  return { depth, nodes };
}

function validManifest(): JsonObject {
  const forgeInventory: JsonObject[] = [];
  return {
    assembly_kind: "synthetic_test_fixture",
    format: "rpg-world-forge.studio_runtime_package_manifest",
    format_version: 1,
    inventory: [
      {
        component: "control",
        mode: 420,
        path: "runtime-manifest.json",
        sha256: "c".repeat(64),
        size: 10,
      },
      {
        component: "codex",
        mode: 493,
        path: "runtime/codex/linux-x64/bin/codex",
        sha256: "a".repeat(64),
        size: 10,
      },
      {
        component: "python",
        mode: 493,
        path: "runtime/python/linux-x64/bin/python3",
        sha256: "b".repeat(64),
        size: 10,
      },
    ],
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
}

function manifestWithInventoryCount(count: number): JsonObject {
  const manifest = validManifest();
  manifest.open_blocker_codes = [
    "codex_ripgrep_static_dependency_notice_sbom_incomplete",
    "linux_bwrap_lgpl_corresponding_source_relink_build_materials_incomplete",
    "linux_bwrap_musl_provenance_incomplete",
    "pbs_zlib_ng_license_incomplete",
    "linux_berkeley_db_dbm_route_unresolved",
    "windows_vc_runtime_redistribution_authority_unresolved",
    "github_attestation_trust_root_rfc3161_verification_pending",
  ];
  const inventory = manifest.inventory as JsonObject[];
  if (count < inventory.length) {
    throw new Error("requested inventory is smaller than the required fixture");
  }
  const emptyDigest = digest(Buffer.alloc(0));
  for (let index = inventory.length; index < count; index += 1) {
    inventory.push({
      component: "forge",
      mode: 420,
      path:
        "runtime/python/linux-x64/lib/package-budget/" +
        `filler-${String(index).padStart(5, "0")}.py`,
      sha256: emptyDigest,
      size: 0,
    });
  }
  inventory.sort((left, right) =>
    Buffer.compare(
      Buffer.from(jsonString(left.path), "utf8"),
      Buffer.from(jsonString(right.path), "utf8"),
    ),
  );
  resealForgeInventory(manifest);
  return manifest;
}

function canonicalLinuxPbsManifest(): {
  manifest: JsonObject;
  receiptBytes: Buffer;
} {
  const manifest = validManifest();
  const receiptBytes = readFileSync(normalizationPath);
  const receipt = JSON.parse(receiptBytes.toString("utf8")) as {
    files: Array<{
      mode: number;
      sha256: string;
      size: number;
      source: string;
    }>;
  };
  const files = receipt.files;
  const inventory = (manifest.inventory as JsonObject[]).filter(
    (entry) => entry.component !== "python",
  );
  inventory.push(
    ...files.map(
      (entry): JsonObject => ({
        component: "python",
        mode: entry.mode,
        path: `runtime/python/linux-x64/${entry.source}`,
        sha256: entry.sha256,
        size: entry.size,
      }),
    ),
    {
      component: "control",
      mode: 420,
      path: normalizationPackagePath,
      sha256: digest(receiptBytes),
      size: receiptBytes.length,
    },
  );
  inventory.sort((left, right) =>
    Buffer.compare(
      Buffer.from(jsonString(left.path), "utf8"),
      Buffer.from(jsonString(right.path), "utf8"),
    ),
  );
  manifest.inventory = inventory;
  const sources = manifest.sources as JsonObject;
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
      path: normalizationPackagePath,
      sha256: digest(receiptBytes),
      size: receiptBytes.length,
    },
    version: "3.12.13",
  };
  return { manifest, receiptBytes };
}

function verifiedManifest(target: "linux-x64" | "win32-x64"): {
  manifest: JsonObject;
  receiptBytes?: Buffer;
  runtimeSourcesBytes: Buffer;
} {
  const linux = target === "linux-x64";
  const canonical = linux ? canonicalLinuxPbsManifest() : null;
  const manifest = canonical?.manifest ?? validManifest();
  const runtimeSourcesBytes = readFileSync(
    path.join(studioRoot, "packaging/runtime-sources.json"),
  );
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
  const sources = manifest.sources as JsonObject;
  sources.runtime_sources = {
    format: "rpg-world-forge.studio_runtime_sources",
    format_version: 1,
    path: "runtime-sources.json",
    sha256: digest(runtimeSourcesBytes),
    size: runtimeSourcesBytes.length,
  };
  sources.runtime_sources_sha256 = digest(runtimeSourcesBytes);
  const codex = sources.codex as JsonObject;
  const python = sources.python as JsonObject;
  codex.archive = linux
    ? {
        entrypoint: "package/vendor/x86_64-unknown-linux-musl/bin/codex",
        filename: "codex-0.144.6-linux-x64.tgz",
        payload_root: "package/vendor/x86_64-unknown-linux-musl",
        sha256:
          "b6752eb2e8c10e6fcc96ac5c1c8ad8342cdb9a74504fb84686addf081a7d2868",
        size: 131_212_687,
      }
    : {
        entrypoint: "package/vendor/x86_64-pc-windows-msvc/bin/codex.exe",
        filename: "codex-0.144.6-win32-x64.tgz",
        payload_root: "package/vendor/x86_64-pc-windows-msvc",
        sha256:
          "e04afbe9841be306455d075ad414993a946c94a399e55d7f9ec223f734cd4101",
        size: 145_169_047,
      };
  python.archive = linux
    ? {
        entrypoint: "python/bin/python3",
        filename:
          "cpython-3.12.13+20260718-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz",
        payload_root: "python",
        sha256:
          "5854aa6ec71cad00334d5065633c210b2e7feb40956767a59a91791cadcf0b79",
        size: 34_199_823,
      }
    : {
        entrypoint: "python/python.exe",
        filename:
          "cpython-3.12.13+20260718-x86_64-pc-windows-msvc-install_only_stripped.tar.gz",
        payload_root: "python",
        sha256:
          "0d422a1439ec308e03f47df551bc30f5994727c456e414b026d202bcda9b7c1c",
        size: 21_932_298,
      };
  const inventory = manifest.inventory as JsonObject[];
  if (!linux) {
    manifest.target_id = "win32-x64";
    manifest.launch = {
      codex: "runtime/codex/win32-x64/bin/codex.exe",
      mcp_module: "worldforge.studio.mcp_server",
      python: "runtime/python/win32-x64/python.exe",
      service_module: "worldforge.studio",
    };
    for (const entry of inventory) {
      if (entry.component === "codex") {
        entry.path = "runtime/codex/win32-x64/bin/codex.exe";
      } else if (entry.component === "python") {
        entry.path = "runtime/python/win32-x64/python.exe";
      }
    }
  }
  inventory.push({
    component: "control",
    mode: 420,
    path: "runtime-sources.json",
    sha256: digest(runtimeSourcesBytes),
    size: runtimeSourcesBytes.length,
  });
  inventory.sort((left, right) =>
    Buffer.compare(
      Buffer.from(jsonString(left.path), "utf8"),
      Buffer.from(jsonString(right.path), "utf8"),
    ),
  );
  return {
    manifest,
    receiptBytes: canonical?.receiptBytes,
    runtimeSourcesBytes,
  };
}

describe("Studio runtime package manifest schema", () => {
  it("accepts the closed non-publishable x64 contract in both validation layers", () => {
    const ajv = new Ajv2020({ allErrors: true, strict: true });
    const validate = ajv.compile(schema);
    const manifest = validManifest();
    expect(validate(manifest), JSON.stringify(validate.errors)).toBe(true);
    expect(() => validateRuntimePackageManifestSemantic(manifest)).not.toThrow();
  });

  it("enforces exact strict JSON byte, node, and depth limits", () => {
    const exactBytes = Buffer.from("{}      ", "utf8");
    expect(
      parseStrictUtf8Json(exactBytes, {
        maxBytes: exactBytes.length,
        maxDepth: 0,
        maxNodes: 1,
      }),
    ).toEqual({});
    expect(() =>
      parseStrictUtf8Json(Buffer.concat([exactBytes, Buffer.from(" ")]), {
        maxBytes: exactBytes.length,
        maxDepth: 0,
        maxNodes: 1,
      }),
    ).toThrow(/contract limits/u);

    const exactNodes = Buffer.from('{"values":[0,0]}', "utf8");
    expect(
      parseStrictUtf8Json(exactNodes, {
        maxBytes: exactNodes.length,
        maxDepth: 2,
        maxNodes: 4,
      }),
    ).toEqual({ values: [0, 0] });
    expect(() =>
      parseStrictUtf8Json(exactNodes, {
        maxBytes: exactNodes.length,
        maxDepth: 2,
        maxNodes: 3,
      }),
    ).toThrow(/tree exceeds/u);

    const exactDepth = Buffer.from('{"a":{"b":{"c":{"d":0}}}}', "utf8");
    expect(
      parseStrictUtf8Json(exactDepth, {
        maxBytes: exactDepth.length,
        maxDepth: 4,
        maxNodes: 5,
      }),
    ).toEqual({ a: { b: { c: { d: 0 } } } });
    const overDepth = Buffer.from(
      '{"a":{"b":{"c":{"d":{"e":0}}}}}',
      "utf8",
    );
    expect(() =>
      parseStrictUtf8Json(overDepth, {
        maxBytes: overDepth.length,
        maxDepth: 4,
        maxNodes: 6,
      }),
    ).toThrow(/tree exceeds/u);
  });

  it("closes the 17023-entry package parser and inventory inconsistency", async () => {
    const ajv = new Ajv2020({ allErrors: true, strict: true });
    const validate = ajv.compile(schema);
    const limits = {
      maxBytes: PACKAGE_MANIFEST_MAX_BYTES,
      maxDepth: PACKAGE_MANIFEST_MAX_JSON_DEPTH,
      maxNodes: PACKAGE_MANIFEST_MAX_JSON_NODES,
    };
    const exact = manifestWithInventoryCount(PACKAGE_MANIFEST_MAX_INVENTORY);
    expect(jsonTreeMetrics(exact)).toEqual({
      depth: PACKAGE_MANIFEST_MAX_JSON_DEPTH,
      nodes: PACKAGE_MANIFEST_MAX_INVENTORY * 6 + 46,
    });
    const exactBytes = canonicalJsonBytes(exact);
    expect(parseStrictUtf8Json(exactBytes, limits)).toEqual(exact);
    expect(validate(exact), JSON.stringify(validate.errors)).toBe(true);
    expect(() => validateRuntimePackageManifestSemantic(exact)).not.toThrow();

    const oneOver = manifestWithInventoryCount(
      PACKAGE_MANIFEST_MAX_INVENTORY + 1,
    );
    expect(parseStrictUtf8Json(canonicalJsonBytes(oneOver), limits)).toEqual(
      oneOver,
    );
    expect(validate(oneOver)).toBe(false);
    expect(() => validateRuntimePackageManifestSemantic(oneOver)).toThrow(
      /inventory/u,
    );

    const inconsistent = manifestWithInventoryCount(17_023);
    expect(jsonTreeMetrics(inconsistent)).toEqual({ depth: 4, nodes: 102_184 });
    expect(() =>
      parseStrictUtf8Json(canonicalJsonBytes(inconsistent), limits),
    ).toThrow(/tree exceeds/u);
    expect(validate(inconsistent)).toBe(false);
    expect(() => validateRuntimePackageManifestSemantic(inconsistent)).toThrow(
      /inventory/u,
    );
    await expect(
      validateWithPython(
        [exact, oneOver, inconsistent].map((value) =>
          canonicalJsonBytes(value).toString("utf8"),
        ),
      ),
    ).resolves.toEqual([true, false, false]);
  }, 20_000);

  it("matches Python across structural and portable semantic mutations", async () => {
    const ajv = new Ajv2020({ allErrors: true, strict: true });
    const validate = ajv.compile(schema);
    const mutations: Array<[string, (manifest: JsonObject) => void]> = [
      ["publication claim", (manifest) => {
        manifest.release_ready = true;
      }],
      ["synthetic relabeled verified", (manifest) => {
        manifest.assembly_kind = "verified_development_runtime";
      }],
      ["ARM64 target", (manifest) => {
        manifest.target_id = "linux-arm64";
      }],
      ["traversal", (manifest) => {
        const inventory = manifest.inventory as JsonObject[];
        inventory[1].path = "../codex";
      }],
      ["unknown root field", (manifest) => {
        manifest.unexpected = true;
      }],
      ["wrong source version", (manifest) => {
        const sources = manifest.sources as JsonObject;
        const codex = sources.codex as JsonObject;
        codex.version = "0.145.0";
      }],
      ["reserved archive filename", (manifest) => {
        sourceArchive(manifest, "codex").filename = "CON";
      }],
      ["backslash archive filename", (manifest) => {
        sourceArchive(manifest, "codex").filename = "bad\\name.tgz";
      }],
      ["non-NFC archive filename", (manifest) => {
        sourceArchive(manifest, "codex").filename = "cafe\u0301.tgz";
      }],
      ["trailing-dot archive filename", (manifest) => {
        sourceArchive(manifest, "codex").filename = "codex.";
      }],
      ["trailing-space archive filename", (manifest) => {
        sourceArchive(manifest, "codex").filename = "codex ";
      }],
      ["archive filename alias", (manifest) => {
        sourceArchive(manifest, "python").filename = "CODEX.TGZ";
      }],
      ["target executable case alias", (manifest) => {
        const launch = manifest.launch as JsonObject;
        launch.codex = "runtime/codex/linux-x64/bin/CODEX";
      }],
      ["Windows reserved path", (manifest) => {
        const inventory = manifest.inventory as JsonObject[];
        inventory[0].path = "CON";
      }],
      ["non-NFC Unicode path", (manifest) => {
        const inventory = manifest.inventory as JsonObject[];
        inventory[0].path = "cafe\u0301";
      }],
      ["case-insensitive alias", (manifest) => {
        const inventory = manifest.inventory as JsonObject[];
        inventory.unshift(
          {
            component: "forge",
            mode: 420,
            path: "protocol/A.txt",
            sha256: "1".repeat(64),
            size: 1,
          },
          {
            component: "forge",
            mode: 420,
            path: "protocol/a.txt",
            sha256: "2".repeat(64),
            size: 1,
          },
        );
      }],
      ["intermediate directory alias", (manifest) => {
        const inventory = manifest.inventory as JsonObject[];
        inventory.push(
          {
            component: "forge",
            mode: 420,
            path: "runtime/python/linux-x64/lib/A/x.py",
            sha256: "1".repeat(64),
            size: 1,
          },
          {
            component: "forge",
            mode: 420,
            path: "runtime/python/linux-x64/lib/a/y.py",
            sha256: "2".repeat(64),
            size: 1,
          },
        );
        inventory.sort((left, right) =>
          Buffer.compare(
            Buffer.from(jsonString(left.path), "utf8"),
            Buffer.from(jsonString(right.path), "utf8"),
          ),
        );
        resealForgeInventory(manifest);
      }],
      ["noncanonical inventory order", (manifest) => {
        const inventory = manifest.inventory as JsonObject[];
        [inventory[0], inventory[1]] = [inventory[1], inventory[0]];
      }],
      ["wrong target component prefix", (manifest) => {
        const inventory = manifest.inventory as JsonObject[];
        inventory[1].path = "runtime/codex/win32-x64/bin/codex";
      }],
      ["source entrypoint mismatch", (manifest) => {
        const sources = manifest.sources as JsonObject;
        const codex = sources.codex as JsonObject;
        const archive = codex.archive as JsonObject;
        archive.entrypoint =
          "package/vendor/x86_64-unknown-linux-musl/bin/other";
      }],
      ["wrong required component", (manifest) => {
        const inventory = manifest.inventory as JsonObject[];
        inventory[1].component = "forge";
      }],
      ["wrong Forge inventory digest", (manifest) => {
        const sources = manifest.sources as JsonObject;
        const forge = sources.forge as JsonObject;
        forge.inventory_sha256 = "e".repeat(64);
      }],
    ];
    const documents: JsonObject[] = [validManifest()];
    const labels = ["valid manifest"];
    const semanticExpected = [true];
    const schemaExpected = [true];
    const schemaSemanticOnly = new Set([
      "archive filename alias",
      "Windows reserved path",
      "case-insensitive alias",
      "intermediate directory alias",
      "noncanonical inventory order",
      "wrong target component prefix",
      "source entrypoint mismatch",
      "wrong required component",
      "wrong Forge inventory digest",
    ]);
    for (const [label, mutate] of mutations) {
      const manifest = structuredClone(validManifest());
      mutate(manifest);
      documents.push(manifest);
      labels.push(label);
      semanticExpected.push(false);
      schemaExpected.push(schemaSemanticOnly.has(label));
    }
    const ajvResults = documents.map((manifest, index) => {
      const accepted = Boolean(validate(manifest));
      if (accepted !== schemaExpected[index]) {
        throw new Error(
          `Ajv mismatch for ${labels[index]}: ${JSON.stringify(validate.errors)}`,
        );
      }
      return accepted;
    });
    const typescriptSemanticResults = documents.map((manifest, index) => {
      const accepted = (() => {
        try {
          validateRuntimePackageManifestSemantic(manifest);
          return true;
        } catch {
          return false;
        }
      })();
      if (accepted !== semanticExpected[index]) {
        throw new Error(`TypeScript semantic mismatch for ${labels[index]}`);
      }
      return accepted;
    });
    const pythonResults = await validateWithPython(
      documents.map((document) => canonicalJsonBytes(document).toString("utf8")),
    );
    expect(ajvResults).toEqual(schemaExpected);
    expect(typescriptSemanticResults).toEqual(semanticExpected);
    expect(pythonResults).toEqual(semanticExpected);
  });

  it("binds the real Linux PBS receipt across schema, TypeScript, and Python", async () => {
    const ajv = new Ajv2020({ allErrors: true, strict: true });
    const validate = ajv.compile(schema);
    const canonical = canonicalLinuxPbsManifest();
    const canonicalReceipt = JSON.parse(
      canonical.receiptBytes.toString("utf8"),
    ) as { casefold_directories: JsonObject[] };
    expect(canonicalReceipt.casefold_directories).toContainEqual({
      first: "share/terminfo/A",
      second: "share/terminfo/a",
    });
    expect(validate(canonical.manifest), JSON.stringify(validate.errors)).toBe(true);
    expect(() =>
      validateRuntimePackageManifestSemantic(
        canonical.manifest,
        canonical.receiptBytes,
      ),
    ).not.toThrow();

    const cases: Array<{
      label: string;
      manifest: JsonObject;
      receipt: Buffer;
      schemaAccepted: boolean;
    }> = [
      {
        label: "canonical",
        manifest: canonical.manifest,
        receipt: canonical.receiptBytes,
        schemaAccepted: true,
      },
    ];

    const incomplete = structuredClone(canonical.manifest);
    incomplete.inventory = (incomplete.inventory as JsonObject[]).filter(
      (entry) => entry.path !== "runtime/python/linux-x64/bin/python3.12",
    );
    cases.push({
      label: "incomplete inventory",
      manifest: incomplete,
      receipt: canonical.receiptBytes,
      schemaAccepted: true,
    });

    const missing = structuredClone(canonical.manifest);
    const missingSources = missing.sources as JsonObject;
    const missingPython = missingSources.python as JsonObject;
    missingPython.normalization = null;
    cases.push({
      label: "missing receipt identity",
      manifest: missing,
      receipt: canonical.receiptBytes,
      schemaAccepted: false,
    });

    const alteredBytes = Buffer.from(canonical.receiptBytes);
    alteredBytes[alteredBytes.length - 2] ^= 1;
    cases.push({
      label: "altered receipt bytes",
      manifest: structuredClone(canonical.manifest),
      receipt: alteredBytes,
      schemaAccepted: true,
    });

    const receiptDocument = JSON.parse(
      canonical.receiptBytes.toString("utf8"),
    ) as JsonObject;
    const casefoldFiles = receiptDocument.casefold_files as JsonObject[];
    casefoldFiles.push(structuredClone(casefoldFiles[0]));
    const resealedBytes = canonicalJsonBytes(receiptDocument);
    const resealed = structuredClone(canonical.manifest);
    resealNormalization(resealed, resealedBytes);
    cases.push({
      label: "resealed receipt",
      manifest: resealed,
      receipt: resealedBytes,
      schemaAccepted: false,
    });

    const extraGroup = structuredClone(canonical.manifest);
    const extraInventory = extraGroup.inventory as JsonObject[];
    const python3 = extraInventory.find(
      (entry) => entry.path === "runtime/python/linux-x64/bin/python3",
    )!;
    extraInventory.push({
      ...python3,
      path: "runtime/python/linux-x64/bin/PYTHON3",
    });
    extraInventory.sort((left, right) =>
      Buffer.compare(
        Buffer.from(jsonString(left.path), "utf8"),
        Buffer.from(jsonString(right.path), "utf8"),
      ),
    );
    cases.push({
      label: "extra casefold group",
      manifest: extraGroup,
      receipt: canonical.receiptBytes,
      schemaAccepted: true,
    });

    for (const testCase of cases) {
      const schemaResult = Boolean(validate(testCase.manifest));
      if (schemaResult !== testCase.schemaAccepted) {
        throw new Error(
          `Ajv mismatch for ${testCase.label}: ${JSON.stringify(validate.errors)}`,
        );
      }
      const typescriptAccepted = (() => {
        try {
          validateRuntimePackageManifestSemantic(
            testCase.manifest,
            testCase.receipt,
          );
          return true;
        } catch {
          return false;
        }
      })();
      expect(typescriptAccepted, testCase.label).toBe(testCase.label === "canonical");
    }
    const pythonResults = await validateWithPython(
      cases.map((testCase) =>
        canonicalJsonBytes(testCase.manifest).toString("utf8"),
      ),
      cases.map((testCase) => testCase.receipt),
    );
    expect(pythonResults).toEqual(cases.map((testCase) => testCase.label === "canonical"));
  });

  it("accepts only the two exact verified target provenance contracts", async () => {
    const ajv = new Ajv2020({ allErrors: true, strict: true });
    const validate = ajv.compile(schema);
    const manifests = [
      verifiedManifest("linux-x64"),
      verifiedManifest("win32-x64"),
    ];
    for (const candidate of manifests) {
      expect(
        validate(candidate.manifest),
        JSON.stringify(validate.errors),
      ).toBe(true);
      expect(() =>
        validateRuntimePackageManifestSemantic(
          candidate.manifest,
          candidate.receiptBytes,
          candidate.runtimeSourcesBytes,
        ),
      ).not.toThrow();
    }
    const pythonResults = await validateWithPython(
      manifests.map((candidate) =>
        canonicalJsonBytes(candidate.manifest).toString("utf8"),
      ),
      manifests.map((candidate) => candidate.receiptBytes),
      manifests.map((candidate) => candidate.runtimeSourcesBytes),
    );
    expect(pythonResults).toEqual([true, true]);
  });
});

function resealNormalization(manifest: JsonObject, receiptBytes: Buffer): void {
  const sources = manifest.sources as JsonObject;
  const python = sources.python as JsonObject;
  const identity = python.normalization as JsonObject;
  identity.sha256 = digest(receiptBytes);
  identity.size = receiptBytes.length;
  const inventory = manifest.inventory as JsonObject[];
  const control = inventory.find(
    (entry) => entry.path === normalizationPackagePath,
  )!;
  control.sha256 = digest(receiptBytes);
  control.size = receiptBytes.length;
}

function resealForgeInventory(manifest: JsonObject): void {
  const inventory = manifest.inventory as JsonObject[];
  const forgeInventory = inventory.filter((entry) => entry.component === "forge");
  const sources = manifest.sources as JsonObject;
  const forge = sources.forge as JsonObject;
  forge.inventory_sha256 = digest(canonicalJsonBytes(forgeInventory));
}

function jsonString(value: JsonValue | undefined): string {
  if (typeof value !== "string") {
    throw new Error("expected JSON string");
  }
  return value;
}

function sourceArchive(
  manifest: JsonObject,
  component: "codex" | "python",
): JsonObject {
  const sources = manifest.sources as JsonObject;
  const source = sources[component] as JsonObject;
  return source.archive as JsonObject;
}

function findPython(): { command: string; prefix: string[] } {
  const candidates = [
    process.env.PYTHON ? { command: process.env.PYTHON, prefix: [] } : null,
    { command: "python3", prefix: [] },
    { command: "python", prefix: [] },
    { command: "py", prefix: ["-3"] },
  ].filter((candidate): candidate is { command: string; prefix: string[] } =>
    Boolean(candidate),
  );
  for (const candidate of candidates) {
    const probe = spawnSync(candidate.command, [...candidate.prefix, "--version"], {
      cwd: repoRoot,
      stdio: "ignore",
    });
    if (probe.status === 0) {
      return candidate;
    }
  }
  throw new Error("supported Python interpreter was not found");
}

async function validateWithPython(
  documents: string[],
  receipts: Array<Buffer | undefined> = documents.map(() => undefined),
  provenances: Array<Buffer | undefined> = documents.map(() => undefined),
): Promise<boolean[]> {
  const python = findPython();
  const program = [
    "import base64,json,sys",
    "from scripts.studio_runtime_assembly import MAX_PACKAGE_JSON_DEPTH,MAX_PACKAGE_JSON_NODES,MAX_PACKAGE_MANIFEST_BYTES,RuntimeAssemblyError,validate_package_manifest",
    "from scripts.studio_runtime_sources import RuntimeSourcesError,load_strict_json_bytes",
    "results=[]",
    "for item in json.load(sys.stdin):",
    "    try:",
    "        receipt=base64.b64decode(item['receipt']) if item['receipt'] is not None else None",
    "        provenance=base64.b64decode(item['provenance']) if item['provenance'] is not None else None",
    "        validate_package_manifest(load_strict_json_bytes(base64.b64decode(item['document']),max_bytes=MAX_PACKAGE_MANIFEST_BYTES,max_depth=MAX_PACKAGE_JSON_DEPTH,max_nodes=MAX_PACKAGE_JSON_NODES),normalization_receipt=receipt,runtime_sources_provenance=provenance)",
    "    except (RuntimeAssemblyError,RuntimeSourcesError):",
    "        results.append(False)",
    "    else:",
    "        results.append(True)",
    "json.dump(results,sys.stdout,separators=(',',':'))",
  ].join("\n");
  const encoded = documents.map((document, index) => ({
    document: Buffer.from(document, "utf8").toString("base64"),
    receipt: receipts[index]?.toString("base64") ?? null,
    provenance: provenances[index]?.toString("base64") ?? null,
  }));
  const stdout = await new Promise<string>((resolve, reject) => {
    const child = spawn(python.command, [...python.prefix, "-c", program], {
      cwd: repoRoot,
      stdio: ["pipe", "pipe", "pipe"],
    });
    let output = "";
    child.stdout.setEncoding("utf8");
    child.stdout.on("data", (chunk: string) => {
      output += chunk;
    });
    child.once("error", () => {
      reject(new Error("Python runtime package validator could not start"));
    });
    child.once("close", (status) => {
      if (status !== 0) {
        reject(new Error("Python runtime package validator failed"));
        return;
      }
      resolve(output);
    });
    child.stdin.end(JSON.stringify(encoded), "utf8");
  });
  return JSON.parse(stdout) as boolean[];
}

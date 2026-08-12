import {
  copyFile,
  mkdir,
  mkdtemp,
  readFile,
  rm,
  writeFile,
} from "node:fs/promises";
import { createRequire } from "node:module";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import {
  canonicalGenericRuntimeDerivedId,
  GENERIC_RUNTIME_EXECUTION_POLICY,
  hasCoherentGenericRuntimeContract,
} from "../../scripts/generic-runtime-validation.mjs";
import {
  canonicalGenericAssetContentHash,
} from "../../scripts/generic-asset-validation.mjs";
import {
  inspectGenericRuntimeSupport,
  validateGenericRuntimeContract,
} from "../../src/main/generic-runtime-contracts";
import {
  GENERIC_RUNTIME_CONTRACTS_ENTRY,
  verifyGenericRuntimeArtifact,
} from "../../scripts/verify-generic-runtime.mjs";

const require = createRequire(import.meta.url);
const asar = require("@electron/asar") as {
  createPackage(source: string, destination: string): Promise<void>;
};
const asarWrappedFs = (
  require("@electron/asar/lib/wrapped-fs.js") as {
    default: {
      readSync: (
        descriptor: number,
        buffer: Buffer,
        offset: number,
        length: number,
        position: number | null,
      ) => number;
    };
  }
).default;

const studioRoot = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../..",
);
const repositoryRoot = path.resolve(studioRoot, "../..");

async function fixture(relative: string): Promise<Record<string, unknown>> {
  return JSON.parse(
    await readFile(path.join(repositoryRoot, ...relative.split("/")), "utf8"),
  ) as Record<string, unknown>;
}

function resealRuntime(
  value: Record<string, unknown>,
  kind:
    | "game-runtime-composition"
    | "game-runtime-snapshot"
    | "generic-runtime-adapter-registry"
    | "generic-runtime-evidence"
    | "generic-runtime-support-report"
    | "runtime-support-authority",
): Record<string, unknown> {
  if (kind === "game-runtime-snapshot") {
    value.tree_hash = canonicalGenericAssetContentHash({
      files: value.files,
    });
  }
  const idField = {
    "game-runtime-composition": "composition_id",
    "game-runtime-snapshot": "snapshot_id",
    "generic-runtime-adapter-registry": "registry_id",
    "generic-runtime-evidence": "evidence_id",
    "generic-runtime-support-report": "report_id",
    "runtime-support-authority": "authority_id",
  }[kind];
  value[idField] = canonicalGenericRuntimeDerivedId(value, kind);
  value.content_hash = canonicalGenericAssetContentHash(value);
  return value;
}

describe("generic runtime contracts", () => {
  it("validates every packaged canonical runtime fixture with semantic parity", async () => {
    const paths = [
      "examples/multigenre-contracts/runtime/adapters/gamepack_raylib_2d_puzzle.json",
      "examples/multigenre-contracts/runtime/adapters/gamepack_raylib_2d_text.json",
      "examples/multigenre-contracts/runtime/snapshot.json",
      "examples/multigenre-contracts/runtime/registry.json",
      "examples/multigenre-contracts/abstract-puzzle/runtime/composition.json",
      "examples/multigenre-contracts/abstract-puzzle/runtime/support-report.json",
      "examples/multigenre-contracts/branching-narrative/runtime/composition.json",
      "examples/multigenre-contracts/branching-narrative/runtime/support-report.json",
    ];
    for (const relative of paths) {
      const document = await fixture(relative);
      expect(
        hasCoherentGenericRuntimeContract(
          document,
          relative.includes("support-report")
            ? "generic-runtime-support-report"
            : relative.includes("composition")
              ? "game-runtime-composition"
              : relative.endsWith("snapshot.json")
                ? "game-runtime-snapshot"
                : relative.endsWith("registry.json")
                  ? "generic-runtime-adapter-registry"
                  : "generic-runtime-adapter",
        ),
      ).toBe(true);
      expect(validateGenericRuntimeContract(document)).toEqual(document);
    }
  });

  it("validates external runtime authorities without granting Studio v4 authority", async () => {
    const paths = [
      "examples/multigenre-contracts/abstract-puzzle/runtime/support-authority.json",
      "examples/multigenre-contracts/branching-narrative/runtime/support-authority.json",
    ];
    for (const relative of paths) {
      const authority = await fixture(relative);
      expect(
        hasCoherentGenericRuntimeContract(
          authority,
          "runtime-support-authority",
        ),
      ).toBe(true);
      expect(() => validateGenericRuntimeContract(authority)).toThrow(
        /generic runtime contract failed schema or coherence validation/i,
      );

      const noncanonicalReasons = structuredClone(authority);
      const reasons = noncanonicalReasons.reason_codes as string[];
      [reasons[0], reasons[1]] = [reasons[1], reasons[0]];
      resealRuntime(noncanonicalReasons, "runtime-support-authority");
      expect(
        hasCoherentGenericRuntimeContract(
          noncanonicalReasons,
          "runtime-support-authority",
        ),
      ).toBe(false);
    }
  });

  it("derives IDs and rejects a canonically rehashed support overclaim", async () => {
    const report = await fixture(
      "examples/multigenre-contracts/abstract-puzzle/runtime/support-report.json",
    );
    expect(
      canonicalGenericRuntimeDerivedId(
        report,
        "generic-runtime-support-report",
      ),
    ).toBe(report.report_id);
    const overclaim = structuredClone(report);
    overclaim.supported = true;
    (overclaim.dimensions as Record<string, unknown>).release = "ready";
    expect(
      hasCoherentGenericRuntimeContract(
        overclaim,
        "generic-runtime-support-report",
      ),
    ).toBe(false);
    expect(() => validateGenericRuntimeContract(overclaim)).toThrow(
      /generic runtime contract/i,
    );
  });

  it("inspects blocked support without confusing validity with execution", async () => {
    const report = await fixture(
      "examples/multigenre-contracts/branching-narrative/runtime/support-report.json",
    );
    expect(inspectGenericRuntimeSupport(report)).toEqual({
      adapter: "declared",
      compatibilityStatus: "partially_supported",
      release: "blocked",
      reasonCodes: [
        "adapter_not_verified",
        "headless_evidence_missing",
        "native_evidence_missing",
        "packaging_evidence_missing",
        "save_replay_evidence_missing",
      ],
      supported: false,
    });
  });

  it("pins execution semantics in adapters and nested registries", async () => {
    expect(GENERIC_RUNTIME_EXECUTION_POLICY).toEqual({
      content_hash:
        "f43fa43e4c54a2910ae8a99fbbfc0b2556359f95c1c88abef59a2508c9ea5983",
      version: 1,
    });
    const adapter = await fixture(
      "examples/multigenre-contracts/runtime/adapters/gamepack_raylib_2d_puzzle.json",
    );
    expect(adapter.execution_semantics).toEqual(
      GENERIC_RUNTIME_EXECUTION_POLICY,
    );
    const tamperedAdapter = structuredClone(adapter);
    (
      tamperedAdapter.execution_semantics as Record<string, unknown>
    ).content_hash = "f".repeat(64);
    tamperedAdapter.content_hash =
      canonicalGenericAssetContentHash(tamperedAdapter);
    expect(() => validateGenericRuntimeContract(tamperedAdapter)).toThrow(
      /generic runtime contract/i,
    );

    const registry = await fixture(
      "examples/multigenre-contracts/runtime/registry.json",
    );
    const tamperedRegistry = structuredClone(registry);
    const nested = (
      tamperedRegistry.adapters as Array<Record<string, unknown>>
    )[0];
    (
      nested.execution_semantics as Record<string, unknown>
    ).content_hash = "e".repeat(64);
    nested.content_hash = canonicalGenericAssetContentHash(nested);
    resealRuntime(
      tamperedRegistry,
      "generic-runtime-adapter-registry",
    );
    expect(() => validateGenericRuntimeContract(tamperedRegistry)).toThrow(
      /generic runtime contract/i,
    );
  });

  it("rejects resealed platform projection and aggregate-size drift", async () => {
    const composition = await fixture(
      "examples/multigenre-contracts/abstract-puzzle/runtime/composition.json",
    );
    const crossed = structuredClone(composition);
    const crossedPlatform = (
      crossed.platforms as Array<Record<string, unknown>>
    )[0];
    crossedPlatform.platform_family = "platform:windows";
    resealRuntime(crossed, "game-runtime-composition");
    expect(() => validateGenericRuntimeContract(crossed)).toThrow(
      /generic runtime contract/i,
    );

    const snapshot = await fixture(
      "examples/multigenre-contracts/runtime/snapshot.json",
    );
    const oversized = structuredClone(snapshot);
    const files = oversized.files as Array<Record<string, unknown>>;
    for (let index = 0; index < 9; index += 1) {
      files.push({
        path: `gamepack_runtime/oversized-${String(index).padStart(2, "0")}.py`,
        sha256: (index + 1).toString(16).padStart(64, "0"),
        size_bytes: 4 * 1024 * 1024,
      });
    }
    files.sort((left, right) =>
      Buffer.compare(
        Buffer.from(String(left.path), "utf8"),
        Buffer.from(String(right.path), "utf8"),
      ),
    );
    resealRuntime(oversized, "game-runtime-snapshot");
    expect(() => validateGenericRuntimeContract(oversized)).toThrow(
      /generic runtime contract/i,
    );
  });

  it("rejects an exactly resealed evidence-free positive support claim", async () => {
    const report = await fixture(
      "examples/multigenre-contracts/abstract-puzzle/runtime/support-report.json",
    );
    const overclaim = structuredClone(report);
    overclaim.evidence = [];
    const dimensions = overclaim.dimensions as Record<string, unknown>;
    dimensions.adapter = "verified";
    dimensions.packaging = "verified";
    dimensions.release = "ready";
    for (const execution of dimensions.execution as Array<
      Record<string, unknown>
    >) {
      execution.status = "native_verified";
      execution.evidence_ids = [];
    }
    for (const mechanic of overclaim.mechanics as Array<
      Record<string, unknown>
    >) {
      mechanic.status = "supported_current";
      mechanic.reason_codes = [];
      mechanic.test_evidence = [];
      mechanic.native_evidence = [];
    }
    for (const feature of overclaim.features as Array<
      Record<string, unknown>
    >) {
      feature.status = "supported_current";
      feature.reason_codes = [];
      feature.evidence_ids = [];
    }
    overclaim.compatibility_status = "supported";
    overclaim.missing_capabilities = [];
    overclaim.reason_codes = [];
    overclaim.supported = true;
    resealRuntime(overclaim, "generic-runtime-support-report");
    expect(() => validateGenericRuntimeContract(overclaim)).toThrow(
      /generic runtime contract/i,
    );
  });

  it("applies the shared canonical reseal corpus without policy drift", async () => {
    const corpus = await fixture(
      "tests/fixtures/generic-runtime/parity-corpus.json",
    );
    expect(corpus.execution_semantics).toEqual(
      GENERIC_RUNTIME_EXECUTION_POLICY,
    );
    const valid = corpus.valid as Array<Record<string, unknown>>;
    const invalid = corpus.invalid as Array<Record<string, unknown>>;
    expect(valid.length).toBeGreaterThanOrEqual(7);
    expect(invalid.length).toBeGreaterThanOrEqual(21);
    for (const testCase of valid) {
      expect(validateGenericRuntimeContract(testCase.document)).toEqual(
        testCase.document,
      );
    }
    for (const testCase of invalid) {
      expect(() =>
        validateGenericRuntimeContract(testCase.document),
      ).toThrow(/generic runtime contract/i);
    }
  });

  it("runs the same corpus through the built CJS and an ASAR extraction", async () => {
    const built = path.join(
      studioRoot,
      ...GENERIC_RUNTIME_CONTRACTS_ENTRY.split("/"),
    );
    await expect(
      verifyGenericRuntimeArtifact({
        artifactKind: "module",
        artifactPath: built,
      }),
    ).resolves.toMatchObject({
      artifact_kind: "module",
      invalid_documents_rejected: 30,
      status: "verified",
      valid_documents_accepted: 8,
    });

    const temporary = await mkdtemp(
      path.join(os.tmpdir(), "world-forge-runtime-contract-asar-"),
    );
    try {
      const source = path.join(temporary, "source");
      const target = path.join(
        source,
        ...GENERIC_RUNTIME_CONTRACTS_ENTRY.split("/"),
      );
      await mkdir(path.dirname(target), { recursive: true });
      await copyFile(built, target);
      const archive = path.join(temporary, "app.asar");
      await asar.createPackage(source, archive);
      await expect(
        verifyGenericRuntimeArtifact({
          artifactKind: "asar",
          artifactPath: archive,
        }),
      ).resolves.toMatchObject({
        artifact_kind: "asar",
        invalid_documents_rejected: 30,
        status: "verified",
        valid_documents_accepted: 8,
      });
    } finally {
      await rm(temporary, { force: true, recursive: true });
    }
  });

  it("retains an internal module-load cause without changing the public code", async () => {
    const temporary = await mkdtemp(
      path.join(os.tmpdir(), "world-forge-runtime-load-cause-"),
    );
    try {
      const modulePath = path.join(temporary, "broken-runtime.cjs");
      await writeFile(
        modulePath,
        "throw new Error('deterministic module-load sentinel');\n",
        { flag: "wx", mode: 0o600 },
      );
      await expect(
        verifyGenericRuntimeArtifact({
          artifactKind: "module",
          artifactPath: modulePath,
        }),
      ).rejects.toMatchObject({
        cause: {
          message: "deterministic module-load sentinel",
        },
        message: "generic_runtime_contract_smoke:module_load_failed",
      });
    } finally {
      await rm(temporary, { force: true, recursive: true });
    }
  });

  it("loads exact ASAR bytes when the dependency returns a short payload read", async () => {
    const temporary = await mkdtemp(
      path.join(os.tmpdir(), "world-forge-runtime-short-asar-read-"),
    );
    const originalReadSync = asarWrappedFs.readSync;
    try {
      const source = path.join(temporary, "source");
      const target = path.join(
        source,
        ...GENERIC_RUNTIME_CONTRACTS_ENTRY.split("/"),
      );
      await mkdir(path.dirname(target), { recursive: true });
      await copyFile(
        path.join(studioRoot, ...GENERIC_RUNTIME_CONTRACTS_ENTRY.split("/")),
        target,
      );
      const archive = path.join(temporary, "app.asar");
      await asar.createPackage(source, archive);
      asarWrappedFs.readSync = (
        descriptor,
        buffer,
        offset,
        length,
        position,
      ) =>
        originalReadSync(
          descriptor,
          buffer,
          offset,
          length > 64 * 1024 ? Math.floor(length / 2) : length,
          position,
        );
      await expect(
        verifyGenericRuntimeArtifact({
          artifactKind: "asar",
          artifactPath: archive,
        }),
      ).resolves.toMatchObject({
        artifact_kind: "asar",
        invalid_documents_rejected: 30,
        status: "verified",
        valid_documents_accepted: 8,
      });
    } finally {
      asarWrappedFs.readSync = originalReadSync;
      await rm(temporary, { force: true, recursive: true });
    }
  });

  it("isolates concurrent ASAR loads and cache retirement", async () => {
    const temporary = await mkdtemp(
      path.join(os.tmpdir(), "world-forge-runtime-concurrent-asar-"),
    );
    try {
      const source = path.join(temporary, "source");
      const target = path.join(
        source,
        ...GENERIC_RUNTIME_CONTRACTS_ENTRY.split("/"),
      );
      await mkdir(path.dirname(target), { recursive: true });
      await copyFile(
        path.join(studioRoot, ...GENERIC_RUNTIME_CONTRACTS_ENTRY.split("/")),
        target,
      );
      const archive = path.join(temporary, "app.asar");
      await asar.createPackage(source, archive);
      const reports = await Promise.all(
        Array.from({ length: 16 }, () =>
          verifyGenericRuntimeArtifact({
            artifactKind: "asar",
            artifactPath: archive,
          }),
        ),
      );
      expect(reports).toHaveLength(16);
      for (const report of reports) {
        expect(report).toMatchObject({
          artifact_kind: "asar",
          invalid_documents_rejected: 30,
          status: "verified",
          valid_documents_accepted: 8,
        });
      }
      await expect(
        verifyGenericRuntimeArtifact({
          artifactKind: "asar",
          artifactPath: archive,
        }),
      ).resolves.toMatchObject({
        artifact_kind: "asar",
        status: "verified",
      });
    } finally {
      await rm(temporary, { force: true, recursive: true });
    }
  });
});

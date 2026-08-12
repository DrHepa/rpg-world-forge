import { createHash } from "node:crypto";
import {
  copyFile,
  mkdir,
  mkdtemp,
  readFile,
  rm,
} from "node:fs/promises";
import { createRequire } from "node:module";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import {
  canonicalMaterializationDerivedId,
  hasAuditedRuntimePlatformLock,
  hasCoherentGameMaterializationBundle,
  hasCoherentRuntimeImplementation,
} from "../../scripts/materialization-contract-validation.mjs";
import {
  canonicalGenericAssetContentHash,
} from "../../scripts/generic-asset-validation.mjs";
import {
  inspectRuntimeImplementation,
  inspectRuntimePlatformLock,
  validateGenericMaterializationContract,
} from "../../src/main/generic-materialization-contracts";
import {
  GENERIC_MATERIALIZATION_CONTRACTS_ENTRY,
  verifyGenericMaterializationArtifact,
  verifyGenericMaterializationSnapshot,
} from "../../scripts/verify-materialization-contracts.mjs";

const require = createRequire(import.meta.url);
const asar = require("@electron/asar") as {
  createPackage(source: string, destination: string): Promise<void>;
};

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

function reseal(
  value: Record<string, unknown>,
  idField:
    | "implementation_id"
    | "lock_id"
    | "materialization_bundle_id",
) {
  value[idField] =
    canonicalMaterializationDerivedId(value, idField) ?? "";
  value.content_hash =
    canonicalGenericAssetContentHash(value) ?? "";
  return value;
}

describe("generic executable materialization contracts", () => {
  it("validates the exact four audited platform locks", async () => {
    const lockRoot = path.join(
      repositoryRoot,
      "examples/multigenre-contracts/runtime/platform-locks",
    );
    const lockNames = [
      "runtime_platform_lock_58fa72d2c53923bcaf61292799529209c310b435.json",
      "runtime_platform_lock_81596ec3acdfdafef473811996b0ac3381cc24df.json",
      "runtime_platform_lock_c3f9a4ae7f6fb435e60039e201777a2444b7f4ac.json",
      "runtime_platform_lock_cdcf772abbac162dec0de8a93894f92b85393e1d.json",
    ];
    for (const name of lockNames) {
      const document = JSON.parse(
        await readFile(path.join(lockRoot, name), "utf8"),
      ) as Record<string, unknown>;
      expect(hasAuditedRuntimePlatformLock(document)).toBe(true);
      expect(validateGenericMaterializationContract(document)).toEqual(document);
      expect(inspectRuntimePlatformLock(document).status).toBe("audited");
    }
  });

  it("rejects a self-resealed wheel artifact and ABI mutation", async () => {
    const original = await fixture(
      "examples/multigenre-contracts/runtime/platform-locks/" +
        "runtime_platform_lock_cdcf772abbac162dec0de8a93894f92b85393e1d.json",
    );
    const artifactTamper = structuredClone(original);
    (
      (artifactTamper.dependency as Record<string, unknown>)
        .artifact as Record<string, unknown>
    ).sha256 = "0".repeat(64);
    reseal(artifactTamper, "lock_id");
    expect(hasAuditedRuntimePlatformLock(artifactTamper)).toBe(false);

    const abiTamper = structuredClone(original);
    (abiTamper.python as Record<string, unknown>).abi = "cp312";
    reseal(abiTamper, "lock_id");
    expect(() => validateGenericMaterializationContract(abiTamper)).toThrow(
      /materialization contract/i,
    );
  });

  it("validates closed package projections and semantic entry points", async () => {
    const implementation = await fixture(
      "examples/multigenre-contracts/abstract-puzzle/runtime/" +
        "runtime-implementation.json",
    );
    expect(hasCoherentRuntimeImplementation(implementation)).toBe(true);
    expect(validateGenericMaterializationContract(implementation)).toEqual(
      implementation,
    );
    expect(inspectRuntimeImplementation(implementation)).toEqual({
      adapterId: "gamepack_raylib_2d_puzzle",
      materializationReady: false,
      platformLockCount: 4,
      status: "declared",
    });

    const entrypointTamper = structuredClone(implementation);
    (
      entrypointTamper.entry_points as Array<Record<string, unknown>>
    )[0].symbol = "eval";
    reseal(entrypointTamper, "implementation_id");
    expect(hasCoherentRuntimeImplementation(entrypointTamper)).toBe(false);

    const packageTamper = structuredClone(implementation);
    const firstPackage = (
      packageTamper.packages as Array<Record<string, unknown>>
    )[0];
    (firstPackage.files as Array<Record<string, unknown>>)[0].sha256 =
      "0".repeat(64);
    firstPackage.tree_hash = canonicalGenericAssetContentHash({
      files: firstPackage.files,
    });
    reseal(packageTamper, "implementation_id");
    expect(() =>
      validateGenericMaterializationContract(packageTamper),
    ).toThrow(/materialization contract/i);

    const lockReferenceTamper = structuredClone(implementation);
    (
      lockReferenceTamper.platform_locks as Array<Record<string, unknown>>
    )[0].content_hash = "0".repeat(64);
    reseal(lockReferenceTamper, "implementation_id");
    expect(() =>
      validateGenericMaterializationContract(lockReferenceTamper),
    ).toThrow(/materialization contract/i);
  });

  it("keeps the outer envelope contract-only and self-bound", () => {
    const sha = "0".repeat(64);
    const files = [
      {
        path: "contracts/platform-locks/runtime_platform_lock_a.json",
        sha256: sha,
        size_bytes: 1,
      },
      {
        path: "contracts/platform-locks/runtime_platform_lock_b.json",
        sha256: sha,
        size_bytes: 1,
      },
      {
        path: "contracts/platform-locks/runtime_platform_lock_c.json",
        sha256: sha,
        size_bytes: 1,
      },
      {
        path: "contracts/platform-locks/runtime_platform_lock_d.json",
        sha256: sha,
        size_bytes: 1,
      },
      {
        path: "contracts/runtime-implementation.json",
        sha256: sha,
        size_bytes: 1,
      },
      {
        path: "launchers/materialization-policy.json",
        sha256: sha,
        size_bytes: 1,
      },
      {
        path: "licenses/world-forge-mit.txt",
        sha256:
          "2e55c53ff294650e049d844f2544fec947c3516440aeffca4b2334cf94b13eeb",
        size_bytes: 1063,
      },
      {
        path: "runtime-bundle/game-runtime-bundle.json",
        sha256: sha,
        size_bytes: 1,
      },
    ];
    const locks = ["a", "b", "c", "d"].map((id, index) => ({
      path: `contracts/platform-locks/runtime_platform_lock_${id}.json`,
      format: "world-forge.runtime_platform_lock",
      format_version: 1,
      id: `runtime_platform_lock_${id}`,
      content_hash: sha,
      os: index < 2 ? "linux" : "windows",
      python_minor: index % 2 === 0 ? "3.11" : "3.12",
      abi: index % 2 === 0 ? "cp311" : "cp312",
    }));
    const lockSetHash = canonicalGenericAssetContentHash({ locks });
    const policyFile = files.find(
      (item) => item.path === "launchers/materialization-policy.json",
    );
    const document: Record<string, unknown> = {
      format: "world-forge.game_materialization_bundle",
      format_version: 1,
      materialization_bundle_id: "",
      state: "contract_only",
      materialization_ready: false,
      missing_launcher_roles: [
        "game_launcher",
        "game_packager",
        "game_verifier",
        "native_smoke_launcher",
      ],
      runtime_bundle: {
        root: "runtime-bundle",
        manifest: {
          path: "runtime-bundle/game-runtime-bundle.json",
          format: "world-forge.game_runtime_bundle",
          format_version: 1,
          id: `game_runtime_bundle_${"1".repeat(48)}`,
          content_hash: sha,
          tree_hash: sha,
        },
      },
      runtime_implementation: {
        path: "contracts/runtime-implementation.json",
        format: "world-forge.runtime_implementation",
        format_version: 1,
        id: `runtime_implementation_${"1".repeat(40)}`,
        content_hash: sha,
      },
      platform_locks: {
        root: "contracts/platform-locks",
        set_hash: lockSetHash,
        locks,
      },
      launchers: {
        root: "launchers",
        policy_version: 1,
        required_roles: [
          "game_launcher",
          "game_packager",
          "game_verifier",
          "native_smoke_launcher",
        ],
        inventory: [
          {
            ...policyFile,
            output_path: "materialization-policy.json",
            role: "materialization_policy",
          },
        ],
        tree_hash: canonicalGenericAssetContentHash({
          files: [policyFile],
        }),
      },
      lineage: {
        gamepack_hash: sha,
        assetpack_hash: sha,
        assetpack_root_hash: sha,
        assetpack_inventory_hash: sha,
        runtime_snapshot_hash: sha,
        runtime_snapshot_tree_hash: sha,
        adapter_hash: sha,
        registry_hash: sha,
        composition_hash: sha,
        support_report_hash: sha,
        runtime_bundle_hash: sha,
        runtime_bundle_tree_hash: sha,
        runtime_implementation_hash: sha,
        platform_lock_set_hash: lockSetHash,
      },
      legal: {
        bundle_license: {
          ...files.find(
            (item) => item.path === "licenses/world-forge-mit.txt",
          ),
        },
      },
      files,
      tree_hash: canonicalGenericAssetContentHash({ files }),
      content_hash: "",
    };
    reseal(document, "materialization_bundle_id");
    expect(hasCoherentGameMaterializationBundle(document)).toBe(true);
    expect(validateGenericMaterializationContract(document)).toEqual(document);

    const ready = structuredClone(document);
    ready.state = "materialization_ready";
    ready.materialization_ready = true;
    ready.missing_launcher_roles = [];
    const templateRoles = [
      [".gitignore", "gitignore"],
      ["README.md", "game_readme"],
      ["THIRD_PARTY_NOTICES.md", "third_party_notices"],
      ["pyproject.toml", "game_package"],
      ["requirements.txt", "requirements"],
      ["run_game.py", "game_launcher"],
      ["scripts/native_smoke.py", "native_smoke_launcher"],
      ["scripts/offline_smoke.py", "offline_smoke_launcher"],
      ["scripts/package_game.py", "game_packager"],
      ["scripts/verify_game.py", "game_verifier"],
      ["src/game/__init__.py", "game_source"],
      ["src/game/runner.py", "game_source"],
      ["tests/test_game_shell.py", "game_test"],
    ] as const;
    const readyLaunchers = (
      ready.launchers as {
        inventory: Array<Record<string, unknown>>;
        tree_hash: string;
      }
    ).inventory;
    const readyFiles = ready.files as Array<{
      path: string;
      sha256: string;
      size_bytes: number;
    }>;
    for (const [outputPath, role] of templateRoles) {
      const record = {
        path: `launchers/templates/${outputPath}`,
        output_path: outputPath,
        role,
        sha256: "8".repeat(64),
        size_bytes: 1,
      };
      readyLaunchers.push(record);
      readyFiles.push({
        path: record.path,
        sha256: record.sha256,
        size_bytes: record.size_bytes,
      });
    }
    readyLaunchers.sort((left, right) =>
      Buffer.compare(
        Buffer.from(String(left.path), "utf8"),
        Buffer.from(String(right.path), "utf8"),
      ),
    );
    readyFiles.sort((left, right) =>
      Buffer.compare(
        Buffer.from(left.path, "utf8"),
        Buffer.from(right.path, "utf8"),
      ),
    );
    (
      ready.launchers as {
        inventory: Array<Record<string, unknown>>;
        tree_hash: string;
      }
    ).tree_hash =
      canonicalGenericAssetContentHash({
        files: readyLaunchers.map((item) => ({
          path: item.path,
          sha256: item.sha256,
          size_bytes: item.size_bytes,
        })),
      }) ?? "";
    ready.tree_hash = canonicalGenericAssetContentHash({ files: readyFiles });
    reseal(ready, "materialization_bundle_id");
    expect(hasCoherentGameMaterializationBundle(ready)).toBe(true);
    expect(validateGenericMaterializationContract(ready)).toEqual(ready);

    const overclaim = structuredClone(document);
    overclaim.materialization_ready = true;
    reseal(overclaim, "materialization_bundle_id");
    expect(hasCoherentGameMaterializationBundle(overclaim)).toBe(false);

    const launcherMismatch = structuredClone(document);
    const launcherSection = launcherMismatch.launchers as {
      inventory: Array<{
        path: string;
        role: string;
        sha256: string;
        size_bytes: number;
      }>;
      tree_hash: string;
    };
    launcherSection.inventory[0].sha256 = "1".repeat(64);
    launcherSection.inventory[0].size_bytes = 2;
    launcherSection.tree_hash =
      canonicalGenericAssetContentHash({
        files: [
          {
            path: launcherSection.inventory[0].path,
            sha256: launcherSection.inventory[0].sha256,
            size_bytes: launcherSection.inventory[0].size_bytes,
          },
        ],
      }) ?? "";
    const launcherFile = (
      launcherMismatch.files as Array<{
        path: string;
        sha256: string;
        size_bytes: number;
      }>
    ).find(
      (item) =>
        item.path === "launchers/materialization-policy.json",
    );
    expect(launcherFile).toMatchObject({
      sha256: sha,
      size_bytes: 1,
    });
    reseal(launcherMismatch, "materialization_bundle_id");
    expect(hasCoherentGameMaterializationBundle(launcherMismatch)).toBe(
      false,
    );

    const licenseMismatch = structuredClone(document);
    const licenseFiles = licenseMismatch.files as Array<{
      path: string;
      sha256: string;
      size_bytes: number;
    }>;
    const licenseFile = licenseFiles.find(
      (item) => item.path === "licenses/world-forge-mit.txt",
    );
    expect(licenseFile).toBeDefined();
    if (!licenseFile) {
      throw new Error("license fixture missing");
    }
    licenseFile.sha256 = "0".repeat(64);
    licenseFile.size_bytes = 1;
    licenseMismatch.tree_hash =
      canonicalGenericAssetContentHash({ files: licenseFiles });
    reseal(licenseMismatch, "materialization_bundle_id");
    expect(hasCoherentGameMaterializationBundle(licenseMismatch)).toBe(
      false,
    );
  });

  it("runs the same closed policy through built CJS and ASAR bytes", async () => {
    const built = path.join(
      studioRoot,
      ...GENERIC_MATERIALIZATION_CONTRACTS_ENTRY.split("/"),
    );
    await expect(
      verifyGenericMaterializationArtifact({
        artifactKind: "module",
        artifactPath: built,
      }),
    ).resolves.toEqual({
      artifact_kind: "module",
      invalid_documents_rejected: 7,
      status: "verified",
      valid_documents_accepted: 6,
    });

    const temporary = await mkdtemp(
      path.join(os.tmpdir(), "world-forge-materialization-asar-"),
    );
    try {
      const source = path.join(temporary, "source");
      const target = path.join(
        source,
        ...GENERIC_MATERIALIZATION_CONTRACTS_ENTRY.split("/"),
      );
      await mkdir(path.dirname(target), { recursive: true });
      await copyFile(built, target);
      const archive = path.join(temporary, "app.asar");
      await asar.createPackage(source, archive);
      await expect(
        verifyGenericMaterializationArtifact({
          artifactKind: "asar",
          artifactPath: archive,
        }),
      ).resolves.toEqual({
        artifact_kind: "asar",
        invalid_documents_rejected: 7,
        status: "verified",
        valid_documents_accepted: 6,
      });
      const archiveBytes = await readFile(archive);
      await expect(
        verifyGenericMaterializationSnapshot({
          artifactBytes: archiveBytes,
          expectedSha256: createHash("sha256")
            .update(archiveBytes)
            .digest("hex"),
          expectedSize: archiveBytes.length,
        }),
      ).resolves.toMatchObject({
        artifact_kind: "asar",
        artifact_size_bytes: archiveBytes.length,
        invalid_documents_rejected: 7,
        status: "verified",
        valid_documents_accepted: 6,
      });
    } finally {
      await rm(temporary, { force: true, recursive: true });
    }
  });
});

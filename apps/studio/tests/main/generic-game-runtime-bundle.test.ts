import {
  mkdtemp,
  readFile,
  rm,
  writeFile,
} from "node:fs/promises";
import os from "node:os";
import path from "node:path";

import { afterAll, beforeAll, describe, expect, it } from "vitest";

import {
  canonicalGameRuntimeBundleId,
} from "../../scripts/game-runtime-bundle-validation.mjs";
import {
  canonicalGenericAssetContentHash,
} from "../../scripts/generic-asset-validation.mjs";
import {
  applySelfResealedGameRuntimeBundleMutation,
  buildGameRuntimeBundleFixture,
  writeGameRuntimeBundleFixture,
} from "../../scripts/verify-game-runtime-bundle.mjs";
import {
  GENERIC_GAME_RUNTIME_BUNDLE_VALIDATOR_RUNTIME,
  noFollowOpenFlagForPlatform,
  validateGenericGameRuntimeBundle,
  verifyGenericGameRuntimeBundleDirectory,
} from "../../src/main/generic-game-runtime-bundle";

const sha = "0".repeat(64);
let temporaryRoot: string;

beforeAll(async () => {
  temporaryRoot = await mkdtemp(
    path.join(os.tmpdir(), "world-forge-studio-runtime-bundle-test-"),
  );
});

afterAll(async () => {
  await rm(temporaryRoot, { force: true, recursive: true });
});

function record(path: string, sizeBytes: number) {
  return {
    path,
    sha256: sha,
    size_bytes: sizeBytes,
  };
}

function buildStructuralBundle() {
  const files = [
    record("assetpack/assetpack.json", 29),
    record("assetpack/assets/board.png", 17),
    record("contracts/gamepack.json", 31),
    record("contracts/runtime-adapter-registry.json", 37),
    record("contracts/runtime-composition.json", 41),
    record("contracts/runtime-snapshot.json", 43),
    record("licenses/world-forge-mit.txt", 1063),
    record(
      "runtime/snapshot-tree/descriptors/puzzle_2d@1.0.0.json",
      47,
    ),
    record("runtime/snapshot-tree/gamepack_runtime/__init__.py", 53),
    record("status/runtime-support-report.json", 59),
  ];
  const runtimeFiles = files
    .filter((entry) => entry.path.startsWith("runtime/snapshot-tree/"))
    .map((entry) => ({
      ...entry,
      path: entry.path.slice("runtime/snapshot-tree/".length),
    }));
  const document = {
    format: "world-forge.game_runtime_bundle",
    format_version: 1,
    bundle_id: "",
    state: "pre_execution",
    contracts: {
      gamepack: {
        path: "contracts/gamepack.json",
        format: "world-forge.gamepack",
        format_version: 1,
        id: "abstract_puzzle",
        content_hash: sha,
      },
      runtime_snapshot: {
        path: "contracts/runtime-snapshot.json",
        format: "world-forge.game_runtime_snapshot",
        format_version: 1,
        id: "runtime_snapshot",
        content_hash: sha,
      },
      runtime_adapter: {
        path: "runtime/snapshot-tree/descriptors/puzzle_2d@1.0.0.json",
        format: "world-forge.runtime_adapter",
        format_version: 1,
        id: "puzzle_2d",
        adapter_version: "1.0.0",
        content_hash: sha,
      },
      runtime_adapter_registry: {
        path: "contracts/runtime-adapter-registry.json",
        format: "world-forge.runtime_adapter_registry",
        format_version: 1,
        id: "runtime_registry",
        content_hash: sha,
      },
      runtime_composition: {
        path: "contracts/runtime-composition.json",
        format: "world-forge.game_runtime_composition",
        format_version: 1,
        id: "runtime_composition",
        content_hash: sha,
      },
      runtime_support_report: {
        path: "status/runtime-support-report.json",
        format: "world-forge.runtime_support_report",
        format_version: 1,
        id: "runtime_support",
        content_hash: sha,
      },
    },
    assetpack: {
      root: "assetpack",
      manifest: {
        path: "assetpack/assetpack.json",
        format: "world-forge.assetpack",
        format_version: 1,
        id: "assetpack_fixture",
        content_hash: sha,
      },
      root_hash: sha,
      inventory_hash: sha,
    },
    runtime_snapshot_tree: {
      root: "runtime/snapshot-tree",
      runtime_api: {
        id: "gamepack_runtime",
        version: "1.0.0",
      },
      tree_hash: canonicalGenericAssetContentHash({ files: runtimeFiles }),
      file_count: runtimeFiles.length,
      total_bytes: runtimeFiles.reduce(
        (total, entry) => total + entry.size_bytes,
        0,
      ),
    },
    bindings: [
      {
        binding_id: "board_ui",
        asset_id: "board_ui",
        role: "texture",
        media_type: "image/png",
        runtime_path: "assets/board.png",
        bundle_path: "assetpack/assets/board.png",
        sha256: sha,
        size_bytes: 17,
      },
    ],
    legal: {
      bundle_license: {
        path: "licenses/world-forge-mit.txt",
        sha256:
          "2e55c53ff294650e049d844f2544fec947c3516440aeffca4b2334cf94b13eeb",
        size_bytes: 1063,
      },
      asset_notices: [],
    },
    files,
    tree_hash: canonicalGenericAssetContentHash({ files }),
    content_hash: "",
  };
  document.bundle_id = canonicalGameRuntimeBundleId(document) ?? "";
  document.content_hash =
    canonicalGenericAssetContentHash(document) ?? "";
  return document;
}

describe("generic game runtime bundle validation", () => {
  it("uses no raw O_NOFOLLOW flag on Windows while keeping it elsewhere", () => {
    expect(noFollowOpenFlagForPlatform("win32", 0x20000)).toBe(0);
    expect(noFollowOpenFlagForPlatform("linux", 0x20000)).toBe(0x20000);
  });

  it("keeps structural validation separate from integral runtime evidence", () => {
    expect(
      GENERIC_GAME_RUNTIME_BUNDLE_VALIDATOR_RUNTIME.contract_formats,
    ).toEqual(["world-forge.game_runtime_bundle"]);
    const document = buildStructuralBundle();
    const validated = validateGenericGameRuntimeBundle(document);
    expect(validated).not.toBeNull();
    expect(Object.isFrozen(validated)).toBe(true);
    expect(validated).not.toHaveProperty("integrity");

    const crossed = structuredClone(document);
    crossed.bindings[0].sha256 = "1".repeat(64);
    crossed.bundle_id = canonicalGameRuntimeBundleId(crossed) ?? "";
    crossed.content_hash =
      canonicalGenericAssetContentHash(crossed) ?? "";
    expect(validateGenericGameRuntimeBundle(crossed)).toBeNull();
  });

  it("integrally verifies exact transferred bytes without advancing support", async () => {
    const fixture = await buildGameRuntimeBundleFixture("abstract-puzzle");
    expect(validateGenericGameRuntimeBundle(fixture.document)).not.toBeNull();
    const root = path.join(temporaryRoot, "valid");
    await writeGameRuntimeBundleFixture(root, fixture);
    const persistedManifest = JSON.parse(
      await readFile(path.join(root, "game-runtime-bundle.json"), "utf8"),
    ) as unknown;
    expect(validateGenericGameRuntimeBundle(persistedManifest)).not.toBeNull();

    const stages: string[] = [];
    const evidence = await verifyGenericGameRuntimeBundleDirectory(root, {
      verificationHook: (event) => {
        stages.push(event);
      },
    });
    expect(stages).toContain("after_assetpack_verification");
    expect(evidence).toEqual({
      bundle_id: fixture.document.bundle_id,
      content_hash: fixture.document.content_hash,
      integrity: "valid",
      release: "blocked",
      state: "pre_execution",
      supported: false,
    });
    expect(Object.isFrozen(evidence)).toBe(true);

    const gamepackPath = path.join(root, "contracts", "gamepack.json");
    const original = await readFile(gamepackPath);
    const tampered = Buffer.from(original);
    tampered[tampered.length - 2] ^= 1;
    await writeFile(gamepackPath, tampered);
    expect(await verifyGenericGameRuntimeBundleDirectory(root)).toBeNull();
  });

  it("rejects the shared self-resealed closure, provenance, and lineage corpus", async () => {
    const fixture = await buildGameRuntimeBundleFixture("abstract-puzzle");
    for (const mutation of [
      "extra_authoring_file",
      "runtime_source",
      "assetpack_gamepack_id",
      "composition_assetpack_id",
      "composition_asset_inventory_id",
    ] as const) {
      const root = path.join(temporaryRoot, `resealed-${mutation}`);
      await writeGameRuntimeBundleFixture(root, fixture);
      await applySelfResealedGameRuntimeBundleMutation(root, mutation);
      const persistedManifest = JSON.parse(
        await readFile(path.join(root, "game-runtime-bundle.json"), "utf8"),
      ) as unknown;
      expect(
        validateGenericGameRuntimeBundle(persistedManifest),
        mutation,
      ).not.toBeNull();
      expect(
        await verifyGenericGameRuntimeBundleDirectory(root),
        mutation,
      ).toBeNull();
    }
  });
});

import path from "node:path";

import { describe, expect, it } from "vitest";

import {
  canonicalGamePackageId,
} from "../../scripts/game-package-validation.mjs";
import {
  verifyGamePackageArtifact,
} from "../../scripts/verify-game-package.mjs";
import {
  canonicalGenericAssetContentHash,
} from "../../scripts/generic-asset-validation.mjs";
import {
  GENERIC_GAME_PACKAGE_INSPECTOR_RUNTIME,
  inspectGenericGamePackage,
  validateGenericGamePackage,
} from "../../src/main/generic-game-package";

const sha = "0".repeat(64);

function buildPackageManifest() {
  const document: Record<string, unknown> = {
    format: "world-forge.game_package",
    format_version: 1,
    package_id: "",
    game_id: "abstract_puzzle",
    lineage: {
      gamepack_hash: sha,
      assetpack_hash: "1".repeat(64),
      runtime_snapshot_hash: "2".repeat(64),
      runtime_composition_hash: "3".repeat(64),
      runtime_bundle_hash: "4".repeat(64),
    },
    standalone_game: {
      format: "world-forge.standalone_game",
      format_version: 1,
      game_id: "abstract_puzzle",
      content_hash: "5".repeat(64),
    },
    payload_lock: {
      format: "world-forge.standalone_game_lock",
      format_version: 1,
      id: "abstract_puzzle",
      content_hash: "6".repeat(64),
      tree_hash: "7".repeat(64),
    },
    files: [
      {
        path: "game-manifest.json",
        sha256: "8".repeat(64),
        size_bytes: 101,
      },
      {
        path: "game.lock.json",
        sha256: "9".repeat(64),
        size_bytes: 103,
      },
    ],
    content_hash: "",
  };
  document.package_id = canonicalGamePackageId(document) ?? "";
  document.content_hash =
    canonicalGenericAssetContentHash(document) ?? "";
  return document;
}

describe("generic game package inspection", () => {
  it("keeps structural manifest inspection separate from Python archive verification", () => {
    const document = buildPackageManifest();
    const validated = validateGenericGamePackage(document);

    expect(validated).not.toBeNull();
    expect(inspectGenericGamePackage(validated)).toEqual({
      content_hash: document.content_hash,
      file_count: 2,
      package_id: document.package_id,
      payload_lock_hash: "6".repeat(64),
      semantic_verification: "required_python",
      standalone_game_hash: "5".repeat(64),
      status: "structurally_valid",
    });
    expect(GENERIC_GAME_PACKAGE_INSPECTOR_RUNTIME).toEqual({
      contract_format: "world-forge.game_package",
      format: "world-forge.studio_internal_game_package_inspector",
      format_version: 1,
      semantic_boundary: "packaged_python_required",
      verification_scope: "package_manifest_structural_validation",
    });
  });

  it("retains an immutable owned snapshot", () => {
    const validated = validateGenericGamePackage(buildPackageManifest());
    expect(validated).not.toBeNull();
    if (validated === null) {
      throw new Error("synthetic package manifest did not validate");
    }
    const lineage = validated.lineage as Record<string, unknown>;
    const files = validated.files as Array<Record<string, unknown>>;

    expect(Object.isFrozen(validated)).toBe(true);
    expect(Object.isFrozen(lineage)).toBe(true);
    expect(Object.isFrozen(files)).toBe(true);
    expect(Object.isFrozen(files[0])).toBe(true);
    expect(() => {
      files[0].path = "changed";
    }).toThrow(TypeError);
  });

  it("rejects self-resealed lineage, ordering, and path collisions", () => {
    const lineageMismatch = buildPackageManifest();
    (
      lineageMismatch.standalone_game as Record<string, unknown>
    ).game_id = "other_game";
    lineageMismatch.package_id =
      canonicalGamePackageId(lineageMismatch) ?? "";
    lineageMismatch.content_hash =
      canonicalGenericAssetContentHash(lineageMismatch) ?? "";
    expect(validateGenericGamePackage(lineageMismatch)).toBeNull();

    const outOfOrder = buildPackageManifest();
    (
      outOfOrder.files as Array<Record<string, unknown>>
    ).reverse();
    outOfOrder.package_id = canonicalGamePackageId(outOfOrder) ?? "";
    outOfOrder.content_hash =
      canonicalGenericAssetContentHash(outOfOrder) ?? "";
    expect(validateGenericGamePackage(outOfOrder)).toBeNull();

    const collision = buildPackageManifest();
    (collision.files as Array<Record<string, unknown>>).push({
      path: "GAME-MANIFEST.json",
      sha256: "a".repeat(64),
      size_bytes: 1,
    });
    (collision.files as Array<Record<string, unknown>>).sort(
      (left, right) =>
        Buffer.compare(
          Buffer.from(String(left.path), "utf8"),
          Buffer.from(String(right.path), "utf8"),
        ),
    );
    collision.package_id = canonicalGamePackageId(collision) ?? "";
    collision.content_hash =
      canonicalGenericAssetContentHash(collision) ?? "";
    expect(validateGenericGamePackage(collision)).toBeNull();
  });

  it("rejects the cross-runtime v1 identifier, version, and path boundary", () => {
    const reseal = (document: Record<string, unknown>) => {
      document.package_id = canonicalGamePackageId(document) ?? "";
      document.content_hash =
        canonicalGenericAssetContentHash(document) ?? "";
    };
    const reject = (
      mutate: (document: Record<string, unknown>) => void,
    ) => {
      const document = buildPackageManifest();
      mutate(document);
      reseal(document);
      expect(validateGenericGamePackage(document)).toBeNull();
    };
    const addPath = (
      document: Record<string, unknown>,
      filePath: string,
    ) => {
      const files = document.files as Array<Record<string, unknown>>;
      files.push({
        path: filePath,
        sha256: "a".repeat(64),
        size_bytes: 1,
      });
      files.sort((left, right) =>
        Buffer.compare(
          Buffer.from(String(left.path), "utf8"),
          Buffer.from(String(right.path), "utf8"),
        ),
      );
    };

    reject((document) => {
      document.format_version = true;
    });
    reject((document) => {
      (
        document.standalone_game as Record<string, unknown>
      ).format_version = true;
    });
    reject((document) => {
      (
        document.payload_lock as Record<string, unknown>
      ).format_version = true;
    });
    for (const value of ["", "con", "a".repeat(65)]) {
      reject((document) => {
        document.game_id = value;
        (
          document.standalone_game as Record<string, unknown>
        ).game_id = value;
      });
    }
    for (const value of ["", "lpt1", "Invalid"]) {
      reject((document) => {
        (
          document.payload_lock as Record<string, unknown>
        ).id = value;
      });
    }
    for (const filePath of [
      "asset. ",
      "CON.txt",
      "é.png",
      "asset?.png",
      "GAME-MANIFEST.json/child",
    ]) {
      reject((document) => addPath(document, filePath));
    }
  });

  it("runs the same structural smoke through the built CJS module", async () => {
    await expect(
      verifyGamePackageArtifact({
        artifactKind: "module",
        artifactPath: path.resolve(
          import.meta.dirname,
          "../../dist-electron/main/generic-game-package.cjs",
        ),
      }),
    ).resolves.toEqual({
      artifact_kind: "module",
      contract_format: "world-forge.game_package",
      format: "world-forge.studio_game_package_smoke",
      format_version: 1,
      manifests_verified: 1,
      release: "blocked",
      semantic_boundary: "packaged_python_required",
      status: "verified",
      supported: false,
      tamper_rejections: 8,
    });
  });
});

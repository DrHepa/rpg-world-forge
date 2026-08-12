import { createHash } from "node:crypto";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { createRequire } from "node:module";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

import {
  canonicalGamePackageId,
} from "./game-package-validation.mjs";
import {
  canonicalGenericAssetContentHash,
} from "./generic-asset-validation.mjs";

const require = createRequire(import.meta.url);
const asar = require("@electron/asar");

export const GAME_PACKAGE_ENTRY =
  "dist-electron/main/generic-game-package.cjs";

function fail(code) {
  throw new Error(`game_package_smoke:${code}`);
}

function digest(payload) {
  return createHash("sha256").update(payload).digest("hex");
}

function loadRuntimeModule(modulePath) {
  let resolved;
  try {
    resolved = require.resolve(modulePath);
  } catch {
    fail("module_missing");
  }
  try {
    return require(resolved);
  } catch {
    fail("module_load_failed");
  }
}

async function loadRuntimeFromAsarBytes(archiveBytes) {
  const temporaryRoot = await mkdtemp(
    path.join(os.tmpdir(), "world-forge-game-package-"),
  );
  const archivePath = path.join(temporaryRoot, "app.asar");
  const modulePath = path.join(
    temporaryRoot,
    "generic-game-package.cjs",
  );
  try {
    await writeFile(archivePath, archiveBytes, {
      flag: "wx",
      mode: 0o600,
    });
    const payload = Buffer.from(
      asar.extractFile(
        archivePath,
        GAME_PACKAGE_ENTRY.split("/").join(path.sep),
        false,
      ),
    );
    await writeFile(modulePath, payload, {
      flag: "wx",
      mode: 0o600,
    });
    return {
      close: async () => {
        try {
          delete require.cache[require.resolve(modulePath)];
        } catch {
          // The retained smoke is already complete if the module disappeared.
        }
        asar.uncache(archivePath);
        await rm(temporaryRoot, { force: true, recursive: true });
      },
      runtime: loadRuntimeModule(modulePath),
    };
  } catch (error) {
    asar.uncache(archivePath);
    await rm(temporaryRoot, { force: true, recursive: true });
    throw error;
  }
}

function reseal(document) {
  document.package_id = canonicalGamePackageId(document);
  document.content_hash =
    canonicalGenericAssetContentHash(document);
}

function buildPackageManifest() {
  const document = {
    format: "world-forge.game_package",
    format_version: 1,
    package_id: "",
    game_id: "abstract_puzzle",
    lineage: {
      gamepack_hash: "0".repeat(64),
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
  reseal(document);
  return document;
}

function smokeRuntime(runtime, artifactKind) {
  const descriptor = runtime.GENERIC_GAME_PACKAGE_INSPECTOR_RUNTIME;
  if (
    descriptor?.contract_format !== "world-forge.game_package" ||
    descriptor?.format !==
      "world-forge.studio_internal_game_package_inspector" ||
    descriptor?.format_version !== 1 ||
    descriptor?.semantic_boundary !== "packaged_python_required" ||
    descriptor?.verification_scope !==
      "package_manifest_structural_validation" ||
    typeof runtime.validateGenericGamePackage !== "function" ||
    typeof runtime.inspectGenericGamePackage !== "function"
  ) {
    fail("surface_invalid");
  }

  const document = buildPackageManifest();
  const validated = runtime.validateGenericGamePackage(document);
  const inspection = runtime.inspectGenericGamePackage(validated);
  if (
    validated === null ||
    inspection?.status !== "structurally_valid" ||
    inspection?.semantic_verification !== "required_python" ||
    inspection?.content_hash !== document.content_hash
  ) {
    fail("manifest_rejected");
  }

  const invalid = [];
  const tampered = structuredClone(document);
  tampered.content_hash = "f".repeat(64);
  invalid.push(tampered);

  const lineageMismatch = structuredClone(document);
  lineageMismatch.standalone_game.game_id = "other_game";
  reseal(lineageMismatch);
  invalid.push(lineageMismatch);

  const outOfOrder = structuredClone(document);
  outOfOrder.files.reverse();
  reseal(outOfOrder);
  invalid.push(outOfOrder);

  const collision = structuredClone(document);
  collision.files.push({
    path: "GAME-MANIFEST.json",
    sha256: "a".repeat(64),
    size_bytes: 1,
  });
  collision.files.sort((left, right) =>
    Buffer.compare(
      Buffer.from(left.path, "utf8"),
      Buffer.from(right.path, "utf8"),
    ),
  );
  reseal(collision);
  invalid.push(collision);

  const casefoldPrefix = structuredClone(document);
  casefoldPrefix.files.push({
    path: "GAME-MANIFEST.json/child",
    sha256: "b".repeat(64),
    size_bytes: 1,
  });
  casefoldPrefix.files.sort((left, right) =>
    Buffer.compare(
      Buffer.from(left.path, "utf8"),
      Buffer.from(right.path, "utf8"),
    ),
  );
  reseal(casefoldPrefix);
  invalid.push(casefoldPrefix);

  const booleanVersion = structuredClone(document);
  booleanVersion.format_version = true;
  reseal(booleanVersion);
  invalid.push(booleanVersion);

  const invalidIdentifier = structuredClone(document);
  invalidIdentifier.game_id = "con";
  invalidIdentifier.standalone_game.game_id = "con";
  reseal(invalidIdentifier);
  invalid.push(invalidIdentifier);

  const nonportablePath = structuredClone(document);
  nonportablePath.files.push({
    path: "asset. ",
    sha256: "c".repeat(64),
    size_bytes: 1,
  });
  nonportablePath.files.sort((left, right) =>
    Buffer.compare(
      Buffer.from(left.path, "utf8"),
      Buffer.from(right.path, "utf8"),
    ),
  );
  reseal(nonportablePath);
  invalid.push(nonportablePath);

  for (const candidate of invalid) {
    if (runtime.validateGenericGamePackage(candidate) !== null) {
      fail("tamper_accepted");
    }
  }
  return Object.freeze({
    artifact_kind: artifactKind,
    contract_format: descriptor.contract_format,
    format: "world-forge.studio_game_package_smoke",
    format_version: 1,
    manifests_verified: 1,
    release: "blocked",
    semantic_boundary: descriptor.semantic_boundary,
    status: "verified",
    supported: false,
    tamper_rejections: invalid.length,
  });
}

export async function verifyGamePackageArtifact({
  artifactKind,
  artifactPath,
}) {
  if (
    !["asar", "module"].includes(artifactKind) ||
    typeof artifactPath !== "string" ||
    !path.isAbsolute(artifactPath)
  ) {
    fail("artifact_invalid");
  }
  if (artifactKind === "module") {
    return smokeRuntime(loadRuntimeModule(artifactPath), artifactKind);
  }
  let archiveBytes;
  try {
    archiveBytes = await readFile(artifactPath);
  } catch {
    fail("asar_entry_missing");
  }
  const loaded = await loadRuntimeFromAsarBytes(archiveBytes);
  try {
    return smokeRuntime(loaded.runtime, artifactKind);
  } finally {
    await loaded.close();
  }
}

export async function verifyGamePackageSnapshot({
  artifactBytes,
  expectedSha256,
  expectedSize,
}) {
  if (
    !Buffer.isBuffer(artifactBytes) ||
    !Number.isSafeInteger(expectedSize) ||
    expectedSize < 1 ||
    artifactBytes.length !== expectedSize ||
    typeof expectedSha256 !== "string" ||
    !/^[0-9a-f]{64}$/u.test(expectedSha256) ||
    digest(artifactBytes) !== expectedSha256
  ) {
    fail("snapshot_identity_mismatch");
  }
  const retainedBytes = Buffer.from(artifactBytes);
  const loaded = await loadRuntimeFromAsarBytes(retainedBytes);
  try {
    const report = smokeRuntime(loaded.runtime, "asar");
    if (
      retainedBytes.length !== expectedSize ||
      digest(retainedBytes) !== expectedSha256
    ) {
      fail("snapshot_identity_mismatch");
    }
    return Object.freeze({
      ...report,
      artifact_sha256: expectedSha256,
      artifact_size_bytes: expectedSize,
    });
  } finally {
    await loaded.close();
  }
}

async function run(argv = process.argv.slice(2)) {
  if (
    argv.length !== 2 ||
    !["--asar", "--module"].includes(argv[0])
  ) {
    fail("arguments_invalid");
  }
  const report = await verifyGamePackageArtifact({
    artifactKind: argv[0] === "--asar" ? "asar" : "module",
    artifactPath: path.resolve(argv[1]),
  });
  process.stdout.write(`${JSON.stringify(report)}\n`);
}

if (
  process.argv[1] &&
  path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)
) {
  try {
    await run();
  } catch (error) {
    const message =
      error instanceof Error ? error.message : "game_package_smoke:unknown";
    process.stderr.write(
      `Studio game package verification failed: ${message}\n`,
    );
    process.exitCode = 1;
  }
}

import { createHash } from "node:crypto";
import {
  mkdtemp,
  readFile,
  rm,
  writeFile,
} from "node:fs/promises";
import { createRequire } from "node:module";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

import {
  canonicalGenericAssetContentHash,
} from "./generic-asset-validation.mjs";
import {
  canonicalMaterializationDerivedId,
} from "./materialization-contract-validation.mjs";

const require = createRequire(import.meta.url);
const asar = require("@electron/asar");
const scriptRoot = path.dirname(fileURLToPath(import.meta.url));
const repositoryRoot = path.resolve(scriptRoot, "../../..");
const expectedFormats = Object.freeze([
  "world-forge.game_materialization_bundle",
  "world-forge.runtime_implementation",
  "world-forge.runtime_platform_lock",
  "world-forge.standalone_game",
  "world-forge.standalone_game_lock",
  "world-forge.standalone_platform",
]);

export const GENERIC_MATERIALIZATION_CONTRACTS_ENTRY =
  "dist-electron/main/generic-materialization-contracts.cjs";

function fail(code) {
  throw new Error(`generic_materialization_contract_smoke:${code}`);
}

function loadModule(modulePath) {
  try {
    return require(require.resolve(modulePath));
  } catch {
    fail("module_load_failed");
  }
}

async function loadFromAsar(archivePath) {
  const temporary = await mkdtemp(
    path.join(os.tmpdir(), "world-forge-materialization-contracts-"),
  );
  const modulePath = path.join(
    temporary,
    "generic-materialization-contracts.cjs",
  );
  try {
    const payload = Buffer.from(
      asar.extractFile(
        archivePath,
        GENERIC_MATERIALIZATION_CONTRACTS_ENTRY.split("/").join(path.sep),
        false,
      ),
    );
    await writeFile(modulePath, payload, { flag: "wx", mode: 0o600 });
    return {
      close: async () => {
        try {
          delete require.cache[require.resolve(modulePath)];
        } catch {
          // Temporary module may already have been released.
        }
        asar.uncache(archivePath);
        await rm(temporary, { force: true, recursive: true });
      },
      runtime: loadModule(modulePath),
    };
  } catch (error) {
    asar.uncache(archivePath);
    await rm(temporary, { force: true, recursive: true });
    throw error;
  }
}

async function loadFromAsarBytes(archiveBytes) {
  const temporary = await mkdtemp(
    path.join(os.tmpdir(), "world-forge-materialization-contract-snapshot-"),
  );
  const archivePath = path.join(temporary, "app.asar");
  try {
    await writeFile(archivePath, archiveBytes, {
      flag: "wx",
      mode: 0o600,
    });
    const loaded = await loadFromAsar(archivePath);
    return {
      close: async () => {
        try {
          await loaded.close();
        } finally {
          asar.uncache(archivePath);
          await rm(temporary, { force: true, recursive: true });
        }
      },
      runtime: loaded.runtime,
    };
  } catch (error) {
    asar.uncache(archivePath);
    await rm(temporary, { force: true, recursive: true });
    throw error;
  }
}

function validateSurface(runtime) {
  const descriptor = runtime?.GENERIC_MATERIALIZATION_VALIDATOR_RUNTIME;
  if (
    descriptor === null ||
    typeof descriptor !== "object" ||
    descriptor.format !==
      "world-forge.studio_internal_materialization_contract_validator" ||
    descriptor.format_version !== 2 ||
    descriptor.verification_scope !== "structural_transfer_validation" ||
    JSON.stringify(descriptor.contract_formats) !==
      JSON.stringify(expectedFormats) ||
    typeof runtime?.validateGenericMaterializationContract !== "function" ||
    typeof runtime?.inspectRuntimeImplementation !== "function" ||
    typeof runtime?.inspectRuntimePlatformLock !== "function"
  ) {
    fail("runtime_surface_invalid");
  }
  return runtime.validateGenericMaterializationContract;
}

async function fixture(relative) {
  return JSON.parse(
    await readFile(
      path.join(repositoryRoot, ...relative.split("/")),
      "utf8",
    ),
  );
}

function reseal(value, idField) {
  value[idField] =
    canonicalMaterializationDerivedId(value, idField) ?? "";
  value.content_hash = canonicalGenericAssetContentHash(value) ?? "";
  return value;
}

async function corpus() {
  const puzzle = await fixture(
    "examples/multigenre-contracts/abstract-puzzle/runtime/" +
      "runtime-implementation.json",
  );
  const narrative = await fixture(
    "examples/multigenre-contracts/branching-narrative/runtime/" +
      "runtime-implementation.json",
  );
  const lock = await fixture(
    "examples/multigenre-contracts/runtime/platform-locks/" +
      "runtime_platform_lock_cdcf772abbac162dec0de8a93894f92b85393e1d.json",
  );

  const entrypointTamper = structuredClone(puzzle);
  entrypointTamper.entry_points[0].symbol = "eval";
  reseal(entrypointTamper, "implementation_id");

  const packageTamper = structuredClone(narrative);
  packageTamper.packages[0].files[0].sha256 = "0".repeat(64);
  packageTamper.packages[0].tree_hash =
    canonicalGenericAssetContentHash({
      files: packageTamper.packages[0].files,
    });
  reseal(packageTamper, "implementation_id");

  const artifactTamper = structuredClone(lock);
  artifactTamper.dependency.artifact.sha256 = "0".repeat(64);
  reseal(artifactTamper, "lock_id");

  const lockReferenceTamper = structuredClone(puzzle);
  lockReferenceTamper.platform_locks[0].content_hash =
    "0".repeat(64);
  reseal(lockReferenceTamper, "implementation_id");

  const standaloneLock = {
    format: "world-forge.standalone_game_lock",
    format_version: 1,
    lock_id: "",
    files: [
      {
        path: "README.md",
        sha256: "1".repeat(64),
        size_bytes: 1,
      },
    ],
    tree_hash: "",
    content_hash: "",
  };
  standaloneLock.tree_hash =
    canonicalGenericAssetContentHash({ files: standaloneLock.files }) ?? "";
  standaloneLock.lock_id =
    `standalone_game_lock_${standaloneLock.tree_hash.slice(0, 40)}`;
  standaloneLock.content_hash =
    canonicalGenericAssetContentHash(standaloneLock) ?? "";

  const platformSeed = {
    requires_python: ">=3.11,<3.13",
    dependency: {
      distribution: "raylib",
      version: "6.0.1.0",
      pin: "raylib==6.0.1.0",
      import_module: "pyray",
      native_api: "raylib-5.5",
    },
    adapter: puzzle.adapter,
    runtime_implementation: {
      implementation_id: puzzle.implementation_id,
      content_hash: puzzle.content_hash,
    },
    runtime_snapshot: puzzle.snapshot,
    platform_locks: puzzle.platform_locks,
  };
  const platformDigest =
    canonicalGenericAssetContentHash(platformSeed) ?? "";
  const standalonePlatform = {
    format: "world-forge.standalone_platform",
    format_version: 1,
    platform_set_id:
      `standalone_platform_${platformDigest.slice(0, 40)}`,
    ...platformSeed,
    content_hash: "",
  };
  standalonePlatform.content_hash =
    canonicalGenericAssetContentHash(standalonePlatform) ?? "";

  const standaloneGame = {
    format: "world-forge.standalone_game",
    format_version: 1,
    game_id: "contract_fixture",
    state: "materialized",
    lineage: {
      gamepack_hash: "2".repeat(64),
      assetpack_hash: "3".repeat(64),
      runtime_snapshot_hash: puzzle.snapshot.content_hash,
      runtime_composition_hash: "4".repeat(64),
      runtime_bundle_hash: "5".repeat(64),
    },
    materialization_bundle: {
      format: "world-forge.game_materialization_bundle",
      format_version: 1,
      id: `game_materialization_bundle_${"6".repeat(36)}`,
      content_hash: "7".repeat(64),
    },
    runtime_implementation: {
      format: "world-forge.runtime_implementation",
      format_version: 1,
      id: puzzle.implementation_id,
      content_hash: puzzle.content_hash,
    },
    platform_set: {
      format: "world-forge.standalone_platform",
      format_version: 1,
      id: standalonePlatform.platform_set_id,
      content_hash: standalonePlatform.content_hash,
    },
    payload_lock: {
      format: "world-forge.standalone_game_lock",
      format_version: 1,
      id: standaloneLock.lock_id,
      content_hash: standaloneLock.content_hash,
      tree_hash: standaloneLock.tree_hash,
    },
    entry_points: {
      game: "run_game.py",
      verifier: "scripts/verify_game.py",
      offline_smoke: "scripts/offline_smoke.py",
      native_smoke: "scripts/native_smoke.py",
    },
    content_hash: "",
  };
  standaloneGame.content_hash =
    canonicalGenericAssetContentHash(standaloneGame) ?? "";

  const standaloneLockTamper = structuredClone(standaloneLock);
  standaloneLockTamper.tree_hash = "0".repeat(64);
  standaloneLockTamper.content_hash =
    canonicalGenericAssetContentHash(standaloneLockTamper) ?? "";
  const standalonePlatformTamper = structuredClone(standalonePlatform);
  standalonePlatformTamper.runtime_snapshot.content_hash = "0".repeat(64);
  standalonePlatformTamper.platform_set_id =
    `standalone_platform_${(
      canonicalGenericAssetContentHash({
        requires_python: standalonePlatformTamper.requires_python,
        dependency: standalonePlatformTamper.dependency,
        adapter: standalonePlatformTamper.adapter,
        runtime_implementation:
          standalonePlatformTamper.runtime_implementation,
        runtime_snapshot: standalonePlatformTamper.runtime_snapshot,
        platform_locks: standalonePlatformTamper.platform_locks,
      }) ?? ""
    ).slice(0, 40)}`;
  standalonePlatformTamper.content_hash =
    canonicalGenericAssetContentHash(standalonePlatformTamper) ?? "";
  const standaloneGameTamper = structuredClone(standaloneGame);
  standaloneGameTamper.payload_lock.id =
    `standalone_game_lock_${"0".repeat(40)}`;
  standaloneGameTamper.content_hash =
    canonicalGenericAssetContentHash(standaloneGameTamper) ?? "";

  return {
    invalid: [
      entrypointTamper,
      packageTamper,
      artifactTamper,
      lockReferenceTamper,
      standaloneLockTamper,
      standalonePlatformTamper,
      standaloneGameTamper,
    ],
    valid: [
      puzzle,
      narrative,
      lock,
      standaloneLock,
      standalonePlatform,
      standaloneGame,
    ],
  };
}

async function smoke(runtime, artifactKind) {
  const validate = validateSurface(runtime);
  const documents = await corpus();
  for (const document of documents.valid) {
    try {
      validate(document);
    } catch {
      fail("valid_document_rejected");
    }
  }
  for (const document of documents.invalid) {
    try {
      validate(document);
    } catch {
      continue;
    }
    fail("invalid_document_accepted");
  }
  return Object.freeze({
    artifact_kind: artifactKind,
    invalid_documents_rejected: documents.invalid.length,
    status: "verified",
    valid_documents_accepted: documents.valid.length,
  });
}

export async function verifyGenericMaterializationArtifact({
  artifactKind,
  artifactPath,
}) {
  if (
    !["asar", "module"].includes(artifactKind) ||
    typeof artifactPath !== "string" ||
    !path.isAbsolute(artifactPath) ||
    path.normalize(artifactPath) !== artifactPath
  ) {
    fail("invalid_arguments");
  }
  if (artifactKind === "module") {
    return smoke(loadModule(artifactPath), artifactKind);
  }
  const loaded = await loadFromAsar(artifactPath);
  try {
    return await smoke(loaded.runtime, artifactKind);
  } finally {
    await loaded.close();
  }
}

export async function verifyGenericMaterializationSnapshot({
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
    createHash("sha256").update(artifactBytes).digest("hex") !==
      expectedSha256
  ) {
    fail("snapshot_identity_mismatch");
  }
  const retainedBytes = Buffer.from(artifactBytes);
  const loaded = await loadFromAsarBytes(retainedBytes);
  try {
    const report = await smoke(loaded.runtime, "asar");
    if (
      retainedBytes.length !== expectedSize ||
      createHash("sha256").update(retainedBytes).digest("hex") !==
        expectedSha256
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

function parseArguments(argv) {
  if (
    argv.length !== 2 ||
    !["--asar", "--module"].includes(argv[0])
  ) {
    fail("invalid_arguments");
  }
  return {
    artifactKind: argv[0] === "--asar" ? "asar" : "module",
    artifactPath: path.resolve(argv[1]),
  };
}

if (
  process.argv[1] &&
  path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)
) {
  try {
    const result = await verifyGenericMaterializationArtifact(
      parseArguments(process.argv.slice(2)),
    );
    process.stdout.write(`${JSON.stringify(result)}\n`);
  } catch (error) {
    process.stderr.write(
      `${
        error instanceof Error
          ? error.message
          : "generic_materialization_contract_smoke:failed"
      }\n`,
    );
    process.exitCode = 1;
  }
}

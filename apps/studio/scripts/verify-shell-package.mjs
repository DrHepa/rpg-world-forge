import path from "node:path";
import { fileURLToPath } from "node:url";

import {
  ShellPackageError,
  verifyPackagedShell,
} from "./shell-package-verifier.mjs";
import { verifyGamePackageSnapshot } from "./verify-game-package.mjs";
import { verifyGameRuntimeBundleSnapshot } from "./verify-game-runtime-bundle.mjs";
import { verifyGamePersistenceSnapshot } from "./verify-game-persistence.mjs";
import { verifyGenericAssetRuntimeSnapshot } from "./verify-generic-asset-runtime.mjs";
import { verifyGenericHeadlessSnapshot } from "./verify-generic-headless.mjs";
import {
  verifyGenericMaterializationSnapshot,
} from "./verify-materialization-contracts.mjs";
import { verifyGenericRuntimeSnapshot } from "./verify-generic-runtime.mjs";

export function parseShellPackageArguments(argv) {
  if (
    argv.length !== 4 ||
    argv[0] !== "--path" ||
    argv[2] !== "--target" ||
    !path.isAbsolute(argv[1]) ||
    path.normalize(argv[1]) !== argv[1] ||
    !["linux-x64", "win32-x64"].includes(argv[3])
  ) {
    throw new ShellPackageError(
      "invalid_arguments",
      "Usage: node scripts/verify-shell-package.mjs --path /absolute/package --target linux-x64|win32-x64",
    );
  }
  return { outputPath: argv[1], targetId: argv[3] };
}

export async function verifyRetainedAsarContracts({
  bytes,
  sha256,
  size,
}) {
  const genericAssetRuntime = await verifyGenericAssetRuntimeSnapshot({
    artifactBytes: bytes,
    expectedSha256: sha256,
    expectedSize: size,
  });
  const genericRuntimeContracts = await verifyGenericRuntimeSnapshot({
    artifactBytes: bytes,
    expectedSha256: sha256,
    expectedSize: size,
  });
  const gameRuntimeBundle = await verifyGameRuntimeBundleSnapshot({
    artifactBytes: bytes,
    expectedSha256: sha256,
    expectedSize: size,
  });
  const gamePackage = await verifyGamePackageSnapshot({
    artifactBytes: bytes,
    expectedSha256: sha256,
    expectedSize: size,
  });
  const gamePersistence = await verifyGamePersistenceSnapshot({
    artifactBytes: bytes,
    expectedSha256: sha256,
    expectedSize: size,
  });
  const genericHeadless = await verifyGenericHeadlessSnapshot({
    artifactBytes: bytes,
    expectedSha256: sha256,
    expectedSize: size,
  });
  const genericMaterializationContracts =
    await verifyGenericMaterializationSnapshot({
      artifactBytes: bytes,
      expectedSha256: sha256,
      expectedSize: size,
    });
  return Object.freeze({
    artifact_sha256: sha256,
    artifact_size_bytes: size,
    game_package: gamePackage,
    game_runtime_bundle: gameRuntimeBundle,
    game_persistence: gamePersistence,
    generic_headless: genericHeadless,
    generic_asset_runtime: genericAssetRuntime,
    generic_materialization_contracts: genericMaterializationContracts,
    generic_runtime_contracts: genericRuntimeContracts,
    status: "verified",
  });
}

export async function run(argv = process.argv.slice(2)) {
  const options = parseShellPackageArguments(argv);
  const result = await verifyPackagedShell({
    ...options,
    retainedAsarVerifier: verifyRetainedAsarContracts,
  });
  const runtimeEvidence = result.retained_asar_verification;
  if (runtimeEvidence === null) {
    throw new ShellPackageError("generic_asset_runtime_smoke_missing");
  }
  process.stdout.write(
    `${JSON.stringify({
      app_asar_sha256: result.app_asar.sha256,
      app_asar_size_bytes: result.app_asar.size,
      generic_asset_runtime_sha256: runtimeEvidence.artifact_sha256,
      game_package_manifests_verified:
        runtimeEvidence.game_package.manifests_verified,
      game_runtime_bundles_verified:
        runtimeEvidence.game_runtime_bundle.bundles_verified,
      game_persistence_documents_verified:
        runtimeEvidence.game_persistence.documents_verified,
      generic_headless_documents_verified:
        runtimeEvidence.generic_headless.documents_verified,
      generic_materialization_invalid_documents_rejected:
        runtimeEvidence.generic_materialization_contracts
          .invalid_documents_rejected,
      generic_materialization_valid_documents_accepted:
        runtimeEvidence.generic_materialization_contracts
          .valid_documents_accepted,
      generic_runtime_invalid_documents_rejected:
        runtimeEvidence.generic_runtime_contracts.invalid_documents_rejected,
      generic_runtime_valid_documents_accepted:
        runtimeEvidence.generic_runtime_contracts.valid_documents_accepted,
      package_kind: result.package_kind,
      redistribution_status: result.redistribution_status,
      release_ready: result.release_ready,
      status: "verified",
      target_id: result.target_id,
      verified_files: result.verified_files,
    })}\n`,
  );
}

const invokedPath = process.argv[1]
  ? path.resolve(process.argv[1])
  : "";
if (invokedPath === fileURLToPath(import.meta.url)) {
  try {
    await run();
  } catch (error) {
    const code =
      error instanceof ShellPackageError
        ? error.code
        : "shell_package_verification_failed";
    process.stderr.write(`Studio shell package verification failed: ${code}\n`);
    process.exitCode = 1;
  }
}

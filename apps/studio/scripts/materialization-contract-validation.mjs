import {
  canonicalGenericAssetContentHash,
  hasCanonicalGenericAssetContentHash,
} from "./generic-asset-validation.mjs";
import {
  GENERIC_RUNTIME_TRUSTED_SNAPSHOT,
} from "./generic-runtime-trusted-files.mjs";

const requiredLauncherRoles = Object.freeze([
  "game_launcher",
  "game_packager",
  "game_verifier",
  "native_smoke_launcher",
]);
const entryPointPolicy = Object.freeze([
  Object.freeze({
    role: "application_factory",
    module: "gamepack_raylib_2d.app",
    symbol: "RuntimeApp.from_bundle",
  }),
  Object.freeze({
    role: "backend_factory",
    module: "gamepack_raylib_2d.backend",
    symbol: "PyrayBackend",
  }),
  Object.freeze({
    role: "bundle_loader",
    module: "gamepack_raylib_2d.resources",
    symbol: "load_runtime_bundle",
  }),
  Object.freeze({
    role: "native_smoke",
    module: "gamepack_raylib_2d.native_smoke",
    symbol: "native_smoke",
  }),
]);
const packagePolicy = Object.freeze([
  Object.freeze({
    classification: "immutable_runtime_source",
    destination_root: "src/gamepack_raylib_2d",
    package: "gamepack_raylib_2d",
    role: "raylib_2d_adapter",
    source_prefix: "gamepack_raylib_2d",
  }),
  Object.freeze({
    classification: "immutable_runtime_source",
    destination_root: "src/gamepack_runtime",
    package: "gamepack_runtime",
    role: "deterministic_kernel",
    source_prefix: "gamepack_runtime",
  }),
]);
const adapterHashes = Object.freeze({
  gamepack_raylib_2d_puzzle:
    "75d8480ada2c1f773f3c0e1d7f1427773b370beb212fb6a91aaad2875ed290e1",
  gamepack_raylib_2d_text:
    "4d0d0a8d9c17eccc2f040a2275e487cf0a21a5af63a8cbfda90811f2dbfc599b",
});
const snapshotIdentity = Object.freeze({
  snapshot_id: GENERIC_RUNTIME_TRUSTED_SNAPSHOT.snapshot_id,
  content_hash: GENERIC_RUNTIME_TRUSTED_SNAPSHOT.content_hash,
  tree_hash: GENERIC_RUNTIME_TRUSTED_SNAPSHOT.tree_hash,
});
const wheelPolicy = Object.freeze({
  "linux|3.11": Object.freeze({
    abi: "cp311",
    filename:
      "raylib-6.0.1.0-cp311-cp311-manylinux2014_x86_64." +
      "manylinux_2_17_x86_64.whl",
    sha256:
      "6b126a8b9e9a0d36dc796fb0ae1bd7473464a4b126315e332079e5eca7215116",
    size_bytes: 2302782,
  }),
  "linux|3.12": Object.freeze({
    abi: "cp312",
    filename:
      "raylib-6.0.1.0-cp312-cp312-manylinux2014_x86_64." +
      "manylinux_2_17_x86_64.whl",
    sha256:
      "bcd224e184c5d64fb6d57bbdabc07124a6f64455ec711d748a0c148b3b26b914",
    size_bytes: 2320911,
  }),
  "windows|3.11": Object.freeze({
    abi: "cp311",
    filename: "raylib-6.0.1.0-cp311-cp311-win_amd64.whl",
    sha256:
      "a665bd824128396f70435f959399d76c2bb460ce1867fb9d19b41490b70a0d2a",
    size_bytes: 2297998,
  }),
  "windows|3.12": Object.freeze({
    abi: "cp312",
    filename: "raylib-6.0.1.0-cp312-cp312-win_amd64.whl",
    sha256:
      "64ee5407b3e222045a2b4e6c41ede77a7be05c90335e0679c4765d0e5bcf3ba6",
    size_bytes: 2300464,
  }),
});
const runtimePlatformLockReferences = Object.freeze([
  Object.freeze({
    lock_id:
      "runtime_platform_lock_58fa72d2c53923bcaf61292799529209c310b435",
    content_hash:
      "0fb5497ec872afeae238441a0fd06d8a025de2950b5bb1c50f03b72d6cd1c25d",
    os: "linux",
    python_minor: "3.12",
    abi: "cp312",
  }),
  Object.freeze({
    lock_id:
      "runtime_platform_lock_81596ec3acdfdafef473811996b0ac3381cc24df",
    content_hash:
      "e0489398cbb6a815b8cb72a54e8dc7bbf38adfe4bc0db0c6a9db8a70a2467b39",
    os: "windows",
    python_minor: "3.11",
    abi: "cp311",
  }),
  Object.freeze({
    lock_id:
      "runtime_platform_lock_c3f9a4ae7f6fb435e60039e201777a2444b7f4ac",
    content_hash:
      "b40a2b67d9051ffde33ea0eafeb4d38a1fa5b7cd1dcf6a32a2944a42cd08f7b1",
    os: "windows",
    python_minor: "3.12",
    abi: "cp312",
  }),
  Object.freeze({
    lock_id:
      "runtime_platform_lock_cdcf772abbac162dec0de8a93894f92b85393e1d",
    content_hash:
      "c04258a531a0d96c1f5400ffd064897831039ed8e41fe0553ddbea89102829a1",
    os: "linux",
    python_minor: "3.11",
    abi: "cp311",
  }),
]);

function isRecord(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function utf8Compare(left, right) {
  return Buffer.compare(Buffer.from(left, "utf8"), Buffer.from(right, "utf8"));
}

function sameJson(left, right) {
  return isDeepStrictEqual(left, right);
}

function hashSeed(value, idField) {
  if (!isRecord(value)) {
    return null;
  }
  return Object.fromEntries(
    Object.entries(value).filter(
      ([key]) => key !== idField && key !== "content_hash",
    ),
  );
}

export function canonicalMaterializationDerivedId(value, idField) {
  const policies = {
    implementation_id: ["runtime_implementation_", 40],
    lock_id: ["runtime_platform_lock_", 40],
    materialization_bundle_id: ["game_materialization_bundle_", 36],
  };
  const policy = policies[idField];
  const seed = hashSeed(value, idField);
  if (policy === undefined || seed === null) {
    return null;
  }
  const digest = canonicalGenericAssetContentHash(seed);
  return digest === null ? null : `${policy[0]}${digest.slice(0, policy[1])}`;
}

function canonicalRecords(records) {
  if (!Array.isArray(records) || records.length === 0) {
    return false;
  }
  const paths = records.map((item) =>
    isRecord(item) && typeof item.path === "string" ? item.path : null,
  );
  if (paths.some((item) => item === null)) {
    return false;
  }
  const exact = new Set(paths);
  const folded = new Set(paths.map((item) => item.normalize("NFC").toLowerCase()));
  return (
    exact.size === paths.length &&
    folded.size === paths.length &&
    paths.every((item) => item.normalize("NFC") === item) &&
    paths.every(
      (item, index) =>
        index === 0 || utf8Compare(paths[index - 1], item) < 0,
    )
  );
}

function expectedPackageRecords(prefix) {
  const marker = `${prefix}/`;
  return GENERIC_RUNTIME_TRUSTED_SNAPSHOT.files
    .filter((item) => item.path.startsWith(marker))
    .map((item) => ({
      path: item.path.slice(marker.length),
      sha256: item.sha256,
      size_bytes: item.size_bytes,
    }))
    .sort((left, right) => utf8Compare(left.path, right.path));
}

export function hasAuditedRuntimePlatformLock(value) {
  if (
    !isRecord(value) ||
    value.format !== "world-forge.runtime_platform_lock" ||
    value.format_version !== 1 ||
    value.lock_id !== canonicalMaterializationDerivedId(value, "lock_id") ||
    !hasCanonicalGenericAssetContentHash(value) ||
    !isRecord(value.platform) ||
    !isRecord(value.python) ||
    !isRecord(value.dependency) ||
    !isRecord(value.dependency.artifact)
  ) {
    return false;
  }
  const expected = wheelPolicy[
    `${String(value.platform.os)}|${String(value.python.minor)}`
  ];
  return (
    expected !== undefined &&
    value.platform.architecture === "x86_64" &&
    value.platform.backend === "backend:raylib" &&
    value.platform.renderer === "raylib" &&
    value.python.implementation === "cpython" &&
    value.python.abi === expected.abi &&
    value.python.requires_python === ">=3.11,<3.13" &&
    value.dependency.distribution === "raylib" &&
    value.dependency.version === "6.0.1.0" &&
    value.dependency.pin === "raylib==6.0.1.0" &&
    value.dependency.import_module === "pyray" &&
    value.dependency.native_api === "raylib-5.5" &&
    value.dependency.artifact.filename === expected.filename &&
    value.dependency.artifact.size_bytes === expected.size_bytes &&
    value.dependency.artifact.sha256 === expected.sha256 &&
    value.dependency.artifact.url ===
      "https://pypi.org/project/raylib/6.0.1.0/#files"
  );
}

export function hasCoherentRuntimeImplementation(value) {
  if (
    !isRecord(value) ||
    value.format !== "world-forge.runtime_implementation" ||
    value.format_version !== 1 ||
    value.implementation_id !==
      canonicalMaterializationDerivedId(value, "implementation_id") ||
    !hasCanonicalGenericAssetContentHash(value) ||
    !isRecord(value.adapter) ||
    !isRecord(value.snapshot) ||
    !isRecord(value.runtime_api) ||
    !Array.isArray(value.packages) ||
    !Array.isArray(value.entry_points) ||
    !Array.isArray(value.platform_locks) ||
    !isRecord(value.materialization_policy)
  ) {
    return false;
  }
  const adapterHash = adapterHashes[value.adapter.adapter_id];
  if (
    adapterHash === undefined ||
    value.adapter.adapter_version !== "1.1.0" ||
    value.adapter.content_hash !== adapterHash ||
    !sameJson(value.snapshot, snapshotIdentity) ||
    !sameJson(value.runtime_api, {
      id: "gamepack_runtime",
      version: "1.0.0",
    }) ||
    !sameJson(value.entry_points, entryPointPolicy) ||
    !sameJson(value.materialization_policy, {
      version: 1,
      standalone_source_root: "src",
      immutable_runtime: true,
      runtime_ai: false,
    }) ||
    value.packages.length !== packagePolicy.length ||
    value.platform_locks.length !== 4
  ) {
    return false;
  }
  for (let index = 0; index < packagePolicy.length; index += 1) {
    const actual = value.packages[index];
    const policy = packagePolicy[index];
    if (!isRecord(actual)) {
      return false;
    }
    for (const [field, expected] of Object.entries(policy)) {
      if (actual[field] !== expected) {
        return false;
      }
    }
    const records = expectedPackageRecords(policy.source_prefix);
    if (
      !sameJson(actual.files, records) ||
      actual.tree_hash !== canonicalGenericAssetContentHash({ files: records })
    ) {
      return false;
    }
  }
  return sameJson(value.platform_locks, runtimePlatformLockReferences);
}

function sameFile(left, right) {
  return (
    isRecord(left) &&
    isRecord(right) &&
    left.path === right.path &&
    left.sha256 === right.sha256 &&
    left.size_bytes === right.size_bytes
  );
}

export function hasCoherentGameMaterializationBundle(value) {
  const readyState =
    value?.state === "materialization_ready" &&
    value?.materialization_ready === true &&
    sameJson(value?.missing_launcher_roles, []);
  const blockedState =
    value?.state === "contract_only" &&
    value?.materialization_ready === false &&
    sameJson(value?.missing_launcher_roles, requiredLauncherRoles);
  if (
    !isRecord(value) ||
    value.format !== "world-forge.game_materialization_bundle" ||
    value.format_version !== 1 ||
    value.materialization_bundle_id !==
      canonicalMaterializationDerivedId(
        value,
        "materialization_bundle_id",
      ) ||
    !hasCanonicalGenericAssetContentHash(value) ||
    (!readyState && !blockedState) ||
    !isRecord(value.runtime_bundle) ||
    !isRecord(value.runtime_bundle.manifest) ||
    !isRecord(value.runtime_implementation) ||
    !isRecord(value.platform_locks) ||
    !Array.isArray(value.platform_locks.locks) ||
    !isRecord(value.launchers) ||
    !Array.isArray(value.launchers.inventory) ||
    !isRecord(value.lineage) ||
    !isRecord(value.legal) ||
    !Array.isArray(value.files) ||
    !canonicalRecords(value.files)
  ) {
    return false;
  }
  const locks = value.platform_locks.locks;
  const lockSetHash = canonicalGenericAssetContentHash({ locks });
  const policyFile = value.launchers.inventory[0];
  const byPath = new Map(value.files.map((item) => [item.path, item]));
  const launcherRecords = value.launchers.inventory.map((item) =>
    isRecord(item)
      ? {
          path: item.path,
          sha256: item.sha256,
          size_bytes: item.size_bytes,
        }
      : null,
  );
  const launcherRoles = new Set(
    value.launchers.inventory
      .filter((item) => isRecord(item))
      .map((item) => item.role),
  );
  const launcherPaths = value.launchers.inventory
    .filter((item) => isRecord(item))
    .map((item) => item.path);
  const outputPaths = value.launchers.inventory
    .filter((item) => isRecord(item))
    .map((item) => item.output_path);
  const allowedExact = new Set([
    "contracts/runtime-implementation.json",
    "launchers/materialization-policy.json",
    "licenses/world-forge-mit.txt",
    ...locks.map((item) => item.path),
    ...launcherPaths,
  ]);
  return (
    value.runtime_bundle.root === "runtime-bundle" &&
    value.runtime_bundle.manifest.path ===
      "runtime-bundle/game-runtime-bundle.json" &&
    value.runtime_bundle.manifest.format ===
      "world-forge.game_runtime_bundle" &&
    value.runtime_bundle.manifest.format_version === 1 &&
    value.runtime_implementation.path ===
      "contracts/runtime-implementation.json" &&
    value.runtime_implementation.format ===
      "world-forge.runtime_implementation" &&
    value.runtime_implementation.format_version === 1 &&
    value.platform_locks.root === "contracts/platform-locks" &&
    locks.length === 4 &&
    locks.every(
      (item, index) =>
        isRecord(item) &&
        item.path ===
          `contracts/platform-locks/${String(item.id)}.json` &&
        item.format === "world-forge.runtime_platform_lock" &&
        item.format_version === 1 &&
        (index === 0 ||
          utf8Compare(locks[index - 1].id, item.id) < 0),
    ) &&
    value.platform_locks.set_hash === lockSetHash &&
    value.launchers.root === "launchers" &&
    value.launchers.policy_version === 1 &&
    sameJson(value.launchers.required_roles, requiredLauncherRoles) &&
    value.launchers.inventory.length === (readyState ? 14 : 1) &&
    launcherRecords.every((item) => item !== null) &&
    canonicalRecords(launcherRecords) &&
    new Set(launcherPaths).size === launcherPaths.length &&
    new Set(
      outputPaths.map((item) =>
        typeof item === "string" ? item.normalize("NFC").toLowerCase() : null,
      ),
    ).size === outputPaths.length &&
    requiredLauncherRoles.every((role) =>
      readyState ? launcherRoles.has(role) : !launcherRoles.has(role),
    ) &&
    isRecord(policyFile) &&
    policyFile.path === "launchers/materialization-policy.json" &&
    policyFile.role === "materialization_policy" &&
    value.launchers.tree_hash ===
      canonicalGenericAssetContentHash({ files: launcherRecords }) &&
    value.lineage.runtime_bundle_hash ===
      value.runtime_bundle.manifest.content_hash &&
    value.lineage.runtime_bundle_tree_hash ===
      value.runtime_bundle.manifest.tree_hash &&
    value.lineage.runtime_implementation_hash ===
      value.runtime_implementation.content_hash &&
    value.lineage.platform_lock_set_hash === lockSetHash &&
    value.launchers.inventory.every((item) =>
      sameFile(byPath.get(item.path), item),
    ) &&
    sameFile(
      byPath.get("licenses/world-forge-mit.txt"),
      value.legal.bundle_license,
    ) &&
    value.legal.bundle_license.sha256 ===
      "2e55c53ff294650e049d844f2544fec947c3516440aeffca4b2334cf94b13eeb" &&
    value.legal.bundle_license.size_bytes === 1063 &&
    value.files.every(
      (item) =>
        allowedExact.has(item.path) ||
        item.path.startsWith("runtime-bundle/"),
    ) &&
    byPath.has("runtime-bundle/game-runtime-bundle.json") &&
    byPath.has("contracts/runtime-implementation.json") &&
    locks.every((item) => byPath.has(item.path)) &&
    value.tree_hash ===
      canonicalGenericAssetContentHash({ files: value.files })
  );
}

export function hasCoherentStandaloneGame(value) {
  return (
    isRecord(value) &&
    value.format === "world-forge.standalone_game" &&
    value.format_version === 1 &&
    value.state === "materialized" &&
    hasCanonicalGenericAssetContentHash(value) &&
    isRecord(value.payload_lock) &&
    typeof value.payload_lock.tree_hash === "string" &&
    value.payload_lock.id ===
      `standalone_game_lock_${value.payload_lock.tree_hash.slice(0, 40)}` &&
    isRecord(value.runtime_implementation) &&
    isRecord(value.platform_set)
  );
}

export function hasCoherentStandaloneGameLock(value) {
  if (
    !isRecord(value) ||
    value.format !== "world-forge.standalone_game_lock" ||
    value.format_version !== 1 ||
    !Array.isArray(value.files) ||
    !canonicalRecords(value.files) ||
    !hasCanonicalGenericAssetContentHash(value)
  ) {
    return false;
  }
  const treeHash = canonicalGenericAssetContentHash({ files: value.files });
  return (
    value.tree_hash === treeHash &&
    value.lock_id === `standalone_game_lock_${String(treeHash).slice(0, 40)}`
  );
}

export function hasCoherentStandalonePlatform(value) {
  if (
    !isRecord(value) ||
    value.format !== "world-forge.standalone_platform" ||
    value.format_version !== 1 ||
    value.requires_python !== ">=3.11,<3.13" ||
    !hasCanonicalGenericAssetContentHash(value) ||
    !isRecord(value.adapter) ||
    !isRecord(value.runtime_implementation) ||
    !isRecord(value.runtime_snapshot) ||
    !Array.isArray(value.platform_locks)
  ) {
    return false;
  }
  const seed = {
    requires_python: value.requires_python,
    dependency: value.dependency,
    adapter: value.adapter,
    runtime_implementation: value.runtime_implementation,
    runtime_snapshot: value.runtime_snapshot,
    platform_locks: value.platform_locks,
  };
  const digest = canonicalGenericAssetContentHash(seed);
  return (
    digest !== null &&
    value.platform_set_id === `standalone_platform_${digest.slice(0, 40)}` &&
    adapterHashes[value.adapter.adapter_id] === value.adapter.content_hash &&
    value.adapter.adapter_version === "1.1.0" &&
    sameJson(value.runtime_snapshot, snapshotIdentity) &&
    sameJson(value.platform_locks, runtimePlatformLockReferences) &&
    sameJson(value.dependency, {
      distribution: "raylib",
      import_module: "pyray",
      native_api: "raylib-5.5",
      pin: "raylib==6.0.1.0",
      version: "6.0.1.0",
    })
  );
}
import { isDeepStrictEqual } from "node:util";

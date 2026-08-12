import {
  canonicalGenericAssetContentHash,
  hasCanonicalGenericAssetContentHash,
} from "./generic-asset-validation.mjs";

const manifestPath = "game-runtime-bundle.json";
const runtimePrefix = "runtime/snapshot-tree/";

function utf8Compare(left, right) {
  return Buffer.compare(Buffer.from(left, "utf8"), Buffer.from(right, "utf8"));
}

function isRecord(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
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

function canonicalPaths(entries) {
  if (!Array.isArray(entries)) {
    return false;
  }
  const paths = [];
  const folded = new Set();
  const exact = new Set();
  for (let index = 0; index < entries.length; index += 1) {
    const entry = entries[index];
    if (!isRecord(entry) || typeof entry.path !== "string") {
      return false;
    }
    const candidate = entry.path;
    if (
      candidate.length === 0 ||
      candidate.normalize("NFC") !== candidate ||
      candidate === manifestPath
    ) {
      return false;
    }
    const key = candidate.toLocaleLowerCase("und");
    if (exact.has(candidate) || folded.has(key)) {
      return false;
    }
    exact.add(candidate);
    folded.add(key);
    paths.push(candidate);
  }
  const sorted = [...paths].sort(utf8Compare);
  if (paths.some((candidate, index) => candidate !== sorted[index])) {
    return false;
  }
  for (const candidate of paths) {
    const parts = candidate.split("/");
    for (let index = 1; index < parts.length; index += 1) {
      if (exact.has(parts.slice(0, index).join("/"))) {
        return false;
      }
    }
  }
  return true;
}

function bundleSeed(value) {
  if (!isRecord(value)) {
    return null;
  }
  return Object.fromEntries(
    Object.entries(value).filter(
      ([key]) => key !== "bundle_id" && key !== "content_hash",
    ),
  );
}

export function canonicalGameRuntimeBundleId(value) {
  const seed = bundleSeed(value);
  if (seed === null) {
    return null;
  }
  const digest = canonicalGenericAssetContentHash(seed);
  return digest === null
    ? null
    : `game_runtime_bundle_${digest.slice(0, 48)}`;
}

export function hasCoherentGameRuntimeBundle(value) {
  if (
    !isRecord(value) ||
    value.format !== "world-forge.game_runtime_bundle" ||
    value.format_version !== 1 ||
    value.state !== "pre_execution" ||
    !hasCanonicalGenericAssetContentHash(value) ||
    value.bundle_id !== canonicalGameRuntimeBundleId(value) ||
    !Array.isArray(value.files) ||
    !canonicalPaths(value.files) ||
    !Array.isArray(value.bindings) ||
    !isRecord(value.runtime_snapshot_tree) ||
    !isRecord(value.contracts) ||
    !isRecord(value.assetpack) ||
    !isRecord(value.legal)
  ) {
    return false;
  }
  const filesByPath = new Map();
  for (const entry of value.files) {
    filesByPath.set(entry.path, entry);
  }
  if (
    value.tree_hash !==
    canonicalGenericAssetContentHash({ files: value.files })
  ) {
    return false;
  }
  const runtimeEntries = value.files.filter((entry) =>
    entry.path.startsWith(runtimePrefix),
  );
  const runtimeRecords = runtimeEntries.map((entry) => ({
    path: entry.path.slice(runtimePrefix.length),
    sha256: entry.sha256,
    size_bytes: entry.size_bytes,
  }));
  if (
    value.runtime_snapshot_tree.file_count !== runtimeEntries.length ||
    value.runtime_snapshot_tree.total_bytes !==
      runtimeEntries.reduce((total, entry) => total + entry.size_bytes, 0) ||
    value.runtime_snapshot_tree.tree_hash !==
      canonicalGenericAssetContentHash({ files: runtimeRecords })
  ) {
    return false;
  }
  const adapter = value.contracts.runtime_adapter;
  if (
    !isRecord(adapter) ||
    adapter.path !==
      `${runtimePrefix}descriptors/${String(adapter.id)}@${String(
        adapter.adapter_version,
      )}.json`
  ) {
    return false;
  }
  const bindingIds = new Set();
  let previousBinding = null;
  for (const binding of value.bindings) {
    if (
      !isRecord(binding) ||
      binding.bundle_path !== `assetpack/${String(binding.runtime_path)}` ||
      bindingIds.has(binding.binding_id) ||
      (previousBinding !== null &&
        utf8Compare(previousBinding, binding.binding_id) >= 0)
    ) {
      return false;
    }
    const record = filesByPath.get(binding.bundle_path);
    if (!sameFile(record, {
      path: binding.bundle_path,
      sha256: binding.sha256,
      size_bytes: binding.size_bytes,
    })) {
      return false;
    }
    bindingIds.add(binding.binding_id);
    previousBinding = binding.binding_id;
  }
  if (!Array.isArray(value.legal.asset_notices)) {
    return false;
  }
  let previousNotice = null;
  for (const notice of value.legal.asset_notices) {
    if (
      !isRecord(notice) ||
      (previousNotice !== null &&
        utf8Compare(previousNotice, notice.path) >= 0) ||
      !sameFile(filesByPath.get(notice.path), notice)
    ) {
      return false;
    }
    previousNotice = notice.path;
  }
  return true;
}

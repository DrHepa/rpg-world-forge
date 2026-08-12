import {
  canonicalGenericAssetContentHash,
  hasCanonicalGenericAssetContentHash,
} from "./generic-asset-validation.mjs";

const SHA256 = /^[0-9a-f]{64}$/u;
const PACKAGE_ID = /^game_package_[0-9a-f]{40}$/u;
const CONTRACT_ID =
  /^(?!(?:aux|con|nul|prn|com[1-9]|lpt[1-9])$)[a-z][a-z0-9_]{1,63}$/u;
const PACKAGE_PATH =
  /^(?!(?:aux|con|nul|prn|com[1-9]|lpt[1-9])(?:[./]|$))[A-Za-z0-9_.@ -]*[A-Za-z0-9_@-](?:\/(?!(?:aux|con|nul|prn|com[1-9]|lpt[1-9])(?:[./]|$))[A-Za-z0-9_.@ -]*[A-Za-z0-9_@-])*$/iu;
const LINEAGE_FIELDS = Object.freeze([
  "assetpack_hash",
  "gamepack_hash",
  "runtime_bundle_hash",
  "runtime_composition_hash",
  "runtime_snapshot_hash",
]);

function isRecord(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function utf8Compare(left, right) {
  return Buffer.compare(Buffer.from(left, "utf8"), Buffer.from(right, "utf8"));
}

function exactFields(value, fields) {
  return (
    isRecord(value) &&
    JSON.stringify(Object.keys(value).sort(utf8Compare)) ===
      JSON.stringify([...fields].sort(utf8Compare))
  );
}

function portableFileRecords(value) {
  if (!Array.isArray(value) || value.length < 2 || value.length > 768) {
    return false;
  }
  const paths = [];
  let total = 0;
  for (const item of value) {
    if (
      !exactFields(item, ["path", "sha256", "size_bytes"]) ||
      typeof item.path !== "string" ||
      item.path === "PACKAGE-MANIFEST.json" ||
      item.path.normalize("NFC") !== item.path ||
      Buffer.byteLength(item.path, "utf8") > 1024 ||
      !PACKAGE_PATH.test(item.path) ||
      typeof item.sha256 !== "string" ||
      !SHA256.test(item.sha256) ||
      !Number.isSafeInteger(item.size_bytes) ||
      item.size_bytes < 0 ||
      item.size_bytes > 32 * 1024 * 1024
    ) {
      return false;
    }
    total += item.size_bytes;
    if (total > 256 * 1024 * 1024) {
      return false;
    }
    paths.push(item.path);
  }
  if (
    !paths.includes("game-manifest.json") ||
    !paths.includes("game.lock.json") ||
    paths.some(
      (item, index) =>
        index > 0 && utf8Compare(paths[index - 1], item) >= 0,
    )
  ) {
    return false;
  }
  const folded = new Set(paths.map((item) => item.toLowerCase()));
  if (folded.size !== paths.length) {
    return false;
  }
  for (const item of paths) {
    const parts = item.split("/");
    for (let depth = 1; depth < parts.length; depth += 1) {
      if (
        folded.has(
          parts.slice(0, depth).join("/").toLowerCase(),
        )
      ) {
        return false;
      }
    }
  }
  return true;
}

function packageIdSeed(value) {
  if (!isRecord(value)) {
    return null;
  }
  return {
    files: value.files,
    game_id: value.game_id,
    lineage: value.lineage,
    payload_lock: value.payload_lock,
    standalone_game: value.standalone_game,
  };
}

export function canonicalGamePackageId(value) {
  const seed = packageIdSeed(value);
  const digest =
    seed === null ? null : canonicalGenericAssetContentHash(seed);
  return digest === null ? null : `game_package_${digest.slice(0, 40)}`;
}

export function hasCoherentGamePackage(value) {
  if (
    !exactFields(value, [
      "content_hash",
      "files",
      "format",
      "format_version",
      "game_id",
      "lineage",
      "package_id",
      "payload_lock",
      "standalone_game",
    ]) ||
    value.format !== "world-forge.game_package" ||
    value.format_version !== 1 ||
    typeof value.package_id !== "string" ||
    !PACKAGE_ID.test(value.package_id) ||
    value.package_id !== canonicalGamePackageId(value) ||
    typeof value.game_id !== "string" ||
    !CONTRACT_ID.test(value.game_id) ||
    !hasCanonicalGenericAssetContentHash(value) ||
    !exactFields(value.lineage, LINEAGE_FIELDS) ||
    !LINEAGE_FIELDS.every(
      (field) =>
        typeof value.lineage[field] === "string" &&
        SHA256.test(value.lineage[field]),
    ) ||
    !exactFields(value.standalone_game, [
      "content_hash",
      "format",
      "format_version",
      "game_id",
    ]) ||
    value.standalone_game.format !== "world-forge.standalone_game" ||
    value.standalone_game.format_version !== 1 ||
    value.standalone_game.game_id !== value.game_id ||
    typeof value.standalone_game.content_hash !== "string" ||
    !SHA256.test(value.standalone_game.content_hash) ||
    !exactFields(value.payload_lock, [
      "content_hash",
      "format",
      "format_version",
      "id",
      "tree_hash",
    ]) ||
    value.payload_lock.format !== "world-forge.standalone_game_lock" ||
    value.payload_lock.format_version !== 1 ||
    typeof value.payload_lock.id !== "string" ||
    !CONTRACT_ID.test(value.payload_lock.id) ||
    typeof value.payload_lock.content_hash !== "string" ||
    !SHA256.test(value.payload_lock.content_hash) ||
    typeof value.payload_lock.tree_hash !== "string" ||
    !SHA256.test(value.payload_lock.tree_hash) ||
    !portableFileRecords(value.files)
  ) {
    return false;
  }
  return true;
}

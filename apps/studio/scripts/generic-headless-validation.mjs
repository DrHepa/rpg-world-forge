import {
  canonicalGenericAssetContentHash,
  hasCanonicalGenericAssetContentHash,
} from "./generic-asset-validation.mjs";

function utf8Compare(left, right) {
  return Buffer.compare(Buffer.from(left, "utf8"), Buffer.from(right, "utf8"));
}

function isRecord(value) {
  const prototype =
    value !== null && typeof value === "object"
      ? Object.getPrototypeOf(value)
      : undefined;
  return (
    value !== null &&
    typeof value === "object" &&
    !Array.isArray(value) &&
    (prototype === Object.prototype || prototype === null)
  );
}

function canonicalStrings(values) {
  if (!Array.isArray(values)) {
    return false;
  }
  for (let index = 0; index < values.length; index += 1) {
    const value = values[index];
    if (
      typeof value !== "string" ||
      (index > 0 && utf8Compare(values[index - 1], value) >= 0)
    ) {
      return false;
    }
  }
  return true;
}

function headlessIdSeed(value) {
  if (!isRecord(value)) {
    return null;
  }
  if (value.format === "world-forge.game_execution_script") {
    return Object.fromEntries(
      Object.entries(value).filter(
        ([key]) => key !== "script_id" && key !== "content_hash",
      ),
    );
  }
  if (value.format === "world-forge.headless_execution_receipt") {
    return Object.fromEntries(
      Object.entries(value).filter(
        ([key]) => key !== "receipt_id" && key !== "content_hash",
      ),
    );
  }
  if (value.format === "world-forge.headless_evidence_set") {
    const fields = [
      "state",
      "runtime_bundle",
      "execution_script",
      "headless_receipt",
      "runtime_evidence",
      "support",
      "files",
      "tree_hash",
      "file_count",
      "total_bytes",
    ];
    if (!fields.every((field) => Object.hasOwn(value, field))) {
      return null;
    }
    return Object.fromEntries(fields.map((field) => [field, value[field]]));
  }
  return null;
}

export function canonicalGenericHeadlessContentHash(value) {
  return canonicalGenericAssetContentHash(value);
}

export function canonicalGenericHeadlessId(value) {
  const seed = headlessIdSeed(value);
  if (seed === null) {
    return null;
  }
  const hash = canonicalGenericAssetContentHash(seed);
  if (hash === null) {
    return null;
  }
  if (value.format === "world-forge.game_execution_script") {
    return `game_execution_script_${hash.slice(0, 40)}`;
  }
  if (value.format === "world-forge.headless_execution_receipt") {
    return `headless_execution_receipt_${hash.slice(0, 40)}`;
  }
  return `headless_evidence_set_${hash.slice(0, 40)}`;
}

function coherentScript(value) {
  if (
    value.script_id !== canonicalGenericHeadlessId(value) ||
    !Array.isArray(value.scenarios) ||
    value.scenarios.length === 0
  ) {
    return false;
  }
  const ids = [];
  for (let index = 0; index < value.scenarios.length; index += 1) {
    const scenario = value.scenarios[index];
    ids.push(isRecord(scenario) ? scenario.scenario_id : null);
  }
  return canonicalStrings(ids);
}

function coherentReceipt(value) {
  if (
    value.receipt_id !== canonicalGenericHeadlessId(value) ||
    value.native_execution !== false ||
    value.status !== "passed" ||
    value.failure !== null ||
    !Array.isArray(value.scenarios) ||
    value.scenarios.length === 0 ||
    !Array.isArray(value.checks) ||
    value.checks.length !== 2 ||
    value.checks[0]?.check_id !== "check:headless_determinism" ||
    value.checks[1]?.check_id !== "check:save_replay"
  ) {
    return false;
  }
  for (let index = 0; index < value.checks.length; index += 1) {
    if (value.checks[index]?.status !== "passed") {
      return false;
    }
  }
  const ids = [];
  for (let index = 0; index < value.scenarios.length; index += 1) {
    const scenario = value.scenarios[index];
    if (
      !isRecord(scenario) ||
      !isRecord(scenario.save) ||
      !isRecord(scenario.replay) ||
      scenario.save.restored_state_hash !== scenario.final_state_hash ||
      scenario.replay.replayed_state_hash !== scenario.final_state_hash
    ) {
      return false;
    }
    ids.push(scenario.scenario_id);
  }
  return canonicalStrings(ids);
}

function portableEvidencePath(value) {
  if (
    typeof value !== "string" ||
    value.length === 0 ||
    value.normalize("NFC") !== value ||
    value.startsWith("/") ||
    value.endsWith("/") ||
    value.includes("\\")
  ) {
    return false;
  }
  const parts = value.split("/");
  return parts.every(
    (part) =>
      part.length > 0 &&
      part !== "." &&
      part !== ".." &&
      !part.endsWith(".") &&
      !part.endsWith(" "),
  );
}

function coherentEvidenceSet(value) {
  if (
    value.evidence_set_id !== canonicalGenericHeadlessId(value) ||
    value.state !== "committed" ||
    value.runtime_evidence?.execution_status !== "headless_verified" ||
    value.support?.compatibility_status !== "partially_supported" ||
    value.support?.release !== "blocked" ||
    value.support?.supported !== false ||
    !Array.isArray(value.files) ||
    value.files.length === 0
  ) {
    return false;
  }
  const paths = [];
  let total = 0;
  const folded = new Set();
  for (let index = 0; index < value.files.length; index += 1) {
    const entry = value.files[index];
    if (
      !isRecord(entry) ||
      !portableEvidencePath(entry.path) ||
      !Number.isSafeInteger(entry.size_bytes) ||
      entry.size_bytes < 1
    ) {
      return false;
    }
    const foldedPath = entry.path.toLocaleLowerCase("und");
    if (folded.has(foldedPath)) {
      return false;
    }
    folded.add(foldedPath);
    paths.push(entry.path);
    total += entry.size_bytes;
  }
  if (
    !canonicalStrings(paths) ||
    value.file_count !== value.files.length ||
    value.total_bytes !== total ||
    value.tree_hash !==
      canonicalGenericAssetContentHash({ files: value.files })
  ) {
    return false;
  }
  return true;
}

export function hasCoherentGenericHeadlessContract(value, kind) {
  if (
    !isRecord(value) ||
    value.format_version !== 1 ||
    !hasCanonicalGenericAssetContentHash(value)
  ) {
    return false;
  }
  if (
    kind === "game_execution_script" &&
    value.format === "world-forge.game_execution_script"
  ) {
    return coherentScript(value);
  }
  if (
    kind === "headless_execution_receipt" &&
    value.format === "world-forge.headless_execution_receipt"
  ) {
    return coherentReceipt(value);
  }
  return (
    kind === "headless_evidence_set" &&
    value.format === "world-forge.headless_evidence_set" &&
    coherentEvidenceSet(value)
  );
}

import {
  canonicalGenericAssetContentHash,
  hasCanonicalGenericAssetContentHash,
} from "./generic-asset-validation.mjs";
import {
  GENERIC_RUNTIME_EXECUTION_POLICY,
} from "./generic-runtime-policy.mjs";

export { GENERIC_RUNTIME_EXECUTION_POLICY };

const derivedIdPolicies = Object.freeze({
  "runtime-support-authority": Object.freeze({
    fields: Object.freeze([
      "format",
      "format_version",
      "gamepack",
      "asset_inventory",
      "composition",
      "assetpack",
      "asset_release_authority",
      "adapter",
      "registry",
      "runtime_snapshot",
      "headless_evidence",
      "package_evidence",
      "runtime_evidence",
      "runtime_support_report",
      "native_status",
      "release_status",
      "supported",
      "reason_codes",
    ]),
    idField: "authority_id",
    prefix: "runtime_authority_",
  }),
  "game-runtime-composition": Object.freeze({
    fields: Object.freeze([
      "gamepack",
      "asset_inventory",
      "assetpack",
      "adapter",
      "registry",
      "runtime_snapshot",
      "platforms",
      "bindings",
    ]),
    idField: "composition_id",
    prefix: "runtime_composition_",
  }),
  "game-runtime-snapshot": Object.freeze({
    fields: Object.freeze([
      "runtime_api",
      "adapter_descriptors",
      "files",
      "tree_hash",
    ]),
    idField: "snapshot_id",
    prefix: "runtime_snapshot_",
  }),
  "generic-runtime-adapter-registry": Object.freeze({
    fields: Object.freeze(["runtime_snapshot", "adapters"]),
    idField: "registry_id",
    prefix: "runtime_registry_",
  }),
  "generic-runtime-evidence": Object.freeze({
    fields: Object.freeze([
      "composition",
      "adapter",
      "platform",
      "execution_status",
      "packaging_status",
      "checks",
    ]),
    idField: "evidence_id",
    prefix: "runtime_evidence_",
  }),
  "generic-runtime-support-report": Object.freeze({
    fields: Object.freeze([
      "gamepack",
      "composition",
      "adapter",
      "evidence",
      "dimensions",
      "compatibility_status",
      "mechanics",
      "features",
      "missing_capabilities",
      "reason_codes",
      "supported",
    ]),
    idField: "report_id",
    prefix: "runtime_support_",
  }),
});

const forbiddenAdapterFieldNames = new Set([
  "absolute_path",
  "authoring_path",
  "callback",
  "command",
  "credential",
  "credentials",
  "endpoint",
  "executable",
  "executable_script",
  "import",
  "javascript",
  "model",
  "model_id",
  "module",
  "mutable_path",
  "native_code",
  "prompt",
  "provider",
  "provider_credentials",
  "provider_details",
  "provider_id",
  "python",
  "runtime_ai",
  "script",
  "source_path",
  "token",
  "tool",
]);

const evidenceKinds = Object.freeze({
  "check:headless_determinism": "headless",
  "check:native_raylib": "native",
  "check:package_verification": "packaging",
  "check:save_replay": "save_replay",
});

function isRecord(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function ownValue(value, key) {
  if (!isRecord(value)) {
    return undefined;
  }
  const descriptor = Object.getOwnPropertyDescriptor(value, key);
  return descriptor !== undefined && "value" in descriptor
    ? descriptor.value
    : undefined;
}

function buildSeed(value, fields) {
  const seed = Object.create(null);
  for (let index = 0; index < fields.length; index += 1) {
    const field = fields[index];
    const fieldValue = ownValue(value, field);
    if (fieldValue === undefined) {
      return null;
    }
    Object.defineProperty(seed, field, {
      configurable: true,
      enumerable: true,
      value: fieldValue,
      writable: true,
    });
  }
  return seed;
}

function compareUtf8(left, right) {
  return Buffer.compare(Buffer.from(left, "utf8"), Buffer.from(right, "utf8"));
}

function isCanonicalString(value) {
  return typeof value === "string" && value === value.normalize("NFC");
}

function isCanonicalArray(value, field, { casefold = true } = {}) {
  if (!Array.isArray(value)) {
    return false;
  }
  const seen = new Set();
  let previous;
  for (let index = 0; index < value.length; index += 1) {
    const item = value[index];
    const current =
      field === null
        ? item
        : isRecord(item)
          ? ownValue(item, field)
          : undefined;
    if (!isCanonicalString(current)) {
      return false;
    }
    const collisionKey = casefold
      ? current.normalize("NFC").toLowerCase()
      : current;
    if (seen.has(collisionKey)) {
      return false;
    }
    seen.add(collisionKey);
    if (
      previous !== undefined &&
      compareUtf8(previous, current) >= 0
    ) {
      return false;
    }
    previous = current;
  }
  return true;
}

function isCanonicalTupleArray(value, fields) {
  if (!Array.isArray(value) || value.length < 1) {
    return false;
  }
  const seen = new Set();
  let previous;
  for (let index = 0; index < value.length; index += 1) {
    const item = value[index];
    if (!isRecord(item)) {
      return false;
    }
    const tuple = [];
    for (let fieldIndex = 0; fieldIndex < fields.length; fieldIndex += 1) {
      const part = ownValue(item, fields[fieldIndex]);
      if (!isCanonicalString(part)) {
        return false;
      }
      tuple.push(part);
    }
    const identity = JSON.stringify(tuple);
    if (seen.has(identity)) {
      return false;
    }
    seen.add(identity);
    if (previous !== undefined) {
      let order = 0;
      for (let fieldIndex = 0; fieldIndex < tuple.length; fieldIndex += 1) {
        order = compareUtf8(previous[fieldIndex], tuple[fieldIndex]);
        if (order !== 0) {
          break;
        }
      }
      if (order >= 0) {
        return false;
      }
    }
    previous = tuple;
  }
  return true;
}

function hasNoForbiddenAdapterField(value) {
  const pending = [value];
  while (pending.length > 0) {
    const current = pending.pop();
    if (current === null || typeof current !== "object") {
      continue;
    }
    const keys = Reflect.ownKeys(current);
    for (let index = 0; index < keys.length; index += 1) {
      const key = keys[index];
      if (typeof key !== "string" || forbiddenAdapterFieldNames.has(key)) {
        return false;
      }
      const descriptor = Object.getOwnPropertyDescriptor(current, key);
      if (descriptor === undefined || !("value" in descriptor)) {
        return false;
      }
      if (descriptor.value !== null && typeof descriptor.value === "object") {
        pending.push(descriptor.value);
      }
    }
  }
  return true;
}

function hasConcretePlatform(value) {
  if (!isRecord(value)) {
    return false;
  }
  const platformId = ownValue(value, "platform_id");
  const expected = {
    "platform:linux_x86_64": Object.freeze({
      architecture: "architecture:x86_64",
      backend: "backend:raylib",
      platform_family: "platform:linux",
      renderer: "raylib",
    }),
    "platform:windows_x86_64": Object.freeze({
      architecture: "architecture:x86_64",
      backend: "backend:raylib",
      platform_family: "platform:windows",
      renderer: "raylib",
    }),
  }[platformId];
  return (
    expected !== undefined &&
    ownValue(value, "platform_family") === expected.platform_family &&
    ownValue(value, "architecture") === expected.architecture &&
    ownValue(value, "backend") === expected.backend &&
    ownValue(value, "renderer") === expected.renderer
  );
}

function hasConcreteCanonicalPlatforms(value) {
  if (!Array.isArray(value) || value.length < 1) {
    return false;
  }
  if (!isCanonicalArray(value, "platform_id")) {
    return false;
  }
  for (let index = 0; index < value.length; index += 1) {
    const platform = value[index];
    if (!hasConcretePlatform(platform)) {
      return false;
    }
  }
  return true;
}

function hasCoherentAdapter(value) {
  const semantics = ownValue(value, "execution_semantics");
  return (
    ownValue(value, "format") === "world-forge.runtime_adapter" &&
    ["declared", "verified"].includes(ownValue(value, "state")) &&
    hasNoForbiddenAdapterField(value) &&
    isRecord(semantics) &&
    ownValue(semantics, "version") ===
      GENERIC_RUNTIME_EXECUTION_POLICY.version &&
    ownValue(semantics, "content_hash") ===
      GENERIC_RUNTIME_EXECUTION_POLICY.content_hash &&
    isCanonicalArray(ownValue(value, "supported_profiles"), null) &&
    isCanonicalArray(ownValue(value, "supported_features"), null) &&
    isCanonicalTupleArray(ownValue(value, "presentations"), [
      "mode",
      "camera",
      "perspective",
      "requested_renderer",
    ]) &&
    isCanonicalArray(ownValue(value, "asset_formats"), null) &&
    isCanonicalArray(ownValue(value, "asset_bindings"), "binding_id") &&
    isCanonicalArray(ownValue(value, "platforms"), "platform_id") &&
    hasConcreteCanonicalPlatforms(ownValue(value, "platforms")) &&
    isCanonicalArray(ownValue(value, "input_capabilities"), null) &&
    isCanonicalArray(ownValue(value, "evidence_requirements"), null) &&
    ownValue(ownValue(value, "implementation"), "backend") ===
      "backend:raylib" &&
    ownValue(ownValue(value, "implementation"), "renderer") === "raylib"
  );
}

function hasCoherentSnapshot(value) {
  const files = ownValue(value, "files");
  const descriptors = ownValue(value, "adapter_descriptors");
  if (
    !isCanonicalArray(files, "path") ||
    !isCanonicalArray(descriptors, "id") ||
    ownValue(value, "tree_hash") !==
      canonicalGenericAssetContentHash({ files })
  ) {
    return false;
  }
  let totalBytes = 0;
  for (let index = 0; index < files.length; index += 1) {
    const size = ownValue(files[index], "size_bytes");
    if (
      !Number.isSafeInteger(size) ||
      size < 0 ||
      size > 4 * 1024 * 1024
    ) {
      return false;
    }
    totalBytes += size;
    if (!Number.isSafeInteger(totalBytes) || totalBytes > 32 * 1024 * 1024) {
      return false;
    }
  }
  for (let index = 0; index < descriptors.length; index += 1) {
    const descriptorId = ownValue(descriptors[index], "id");
    let matches = 0;
    for (let fileIndex = 0; fileIndex < files.length; fileIndex += 1) {
      const filePath = ownValue(files[fileIndex], "path");
      if (
        typeof filePath === "string" &&
        filePath.startsWith(`descriptors/${descriptorId}@`)
      ) {
        matches += 1;
      }
    }
    if (matches !== 1) {
      return false;
    }
  }
  return true;
}

function hasCoherentRegistry(value) {
  const adapters = ownValue(value, "adapters");
  if (!isCanonicalArray(adapters, "adapter_id")) {
    return false;
  }
  for (let index = 0; index < adapters.length; index += 1) {
    if (!hasCanonicalGenericAssetContentHash(adapters[index])) {
      return false;
    }
    if (!hasCoherentAdapter(adapters[index])) {
      return false;
    }
  }
  return true;
}

function hasCoherentComposition(value) {
  const bindings = ownValue(value, "bindings");
  if (
    !hasConcreteCanonicalPlatforms(ownValue(value, "platforms")) ||
    !isCanonicalArray(bindings, "binding_id")
  ) {
    return false;
  }
  const bindingKeys = new Set();
  for (let index = 0; index < bindings.length; index += 1) {
    const binding = bindings[index];
    const size = ownValue(binding, "size_bytes");
    if (
      !Number.isSafeInteger(size) ||
      size < 1 ||
      size > 16 * 1024 * 1024
    ) {
      return false;
    }
    const key = JSON.stringify([
      ownValue(binding, "binding_id"),
      ownValue(binding, "asset_id"),
      ownValue(binding, "role"),
      ownValue(binding, "media_type"),
      ownValue(binding, "runtime_path"),
    ]);
    if (bindingKeys.has(key)) {
      return false;
    }
    bindingKeys.add(key);
  }
  return true;
}

function hasCoherentEvidence(value) {
  const checks = ownValue(value, "checks");
  const platform = ownValue(value, "platform");
  if (
    !Array.isArray(checks) ||
    checks.length < 1 ||
    !hasConcretePlatform(platform) ||
    !isCanonicalArray(checks, "check_id")
  ) {
    return false;
  }
  const passed = new Set();
  const failed = new Set();
  const externalEvidenceIds = new Set();
  let packagingStatus;
  for (let index = 0; index < checks.length; index += 1) {
    const check = checks[index];
    const checkId = ownValue(check, "check_id");
    const expectedKind = evidenceKinds[checkId];
    const kind = ownValue(check, "kind");
    const status = ownValue(check, "status");
    const contentHash = ownValue(check, "content_hash");
    const externalEvidenceId = ownValue(check, "evidence_id");
    if (
      expectedKind === undefined ||
      kind !== expectedKind ||
      !["passed", "failed"].includes(status) ||
      !isCanonicalString(externalEvidenceId) ||
      externalEvidenceIds.has(externalEvidenceId.toLowerCase()) ||
      typeof contentHash !== "string" ||
      contentHash === "0".repeat(64)
    ) {
      return false;
    }
    externalEvidenceIds.add(externalEvidenceId.toLowerCase());
    (status === "passed" ? passed : failed).add(kind);
    if (kind === "packaging") {
      packagingStatus = status;
    }
  }
  const executionStatus = ownValue(value, "execution_status");
  const packageClaim = ownValue(value, "packaging_status");
  if (
    (executionStatus === "headless_verified" &&
      !(passed.has("headless") && passed.has("save_replay"))) ||
    (executionStatus === "native_verified" &&
      !(
        passed.has("headless") &&
        passed.has("native") &&
        passed.has("save_replay")
      )) ||
    (executionStatus === "failed" && failed.size === 0) ||
    (packageClaim === "verified" && packagingStatus !== "passed") ||
    (packageClaim === "failed" && packagingStatus !== "failed")
  ) {
    return false;
  }
  return true;
}

function allResolvedItemsComplete(items) {
  if (!Array.isArray(items)) {
    return false;
  }
  for (let index = 0; index < items.length; index += 1) {
    if (
      !["supported_current", "game_extension_verified"].includes(
        ownValue(items[index], "status"),
      )
    ) {
      return false;
    }
  }
  return true;
}

function sameStrings(left, right) {
  return (
    Array.isArray(left) &&
    Array.isArray(right) &&
    JSON.stringify(left) === JSON.stringify(right)
  );
}

function hasCanonicalExecutionPlatforms(execution) {
  if (!Array.isArray(execution) || execution.length < 1) {
    return false;
  }
  const seen = new Set();
  let previous;
  for (let index = 0; index < execution.length; index += 1) {
    const platform = ownValue(execution[index], "platform");
    const platformId = ownValue(platform, "platform_id");
    if (
      !hasConcretePlatform(platform) ||
      !isCanonicalString(platformId) ||
      seen.has(platformId)
    ) {
      return false;
    }
    seen.add(platformId);
    if (
      previous !== undefined &&
      compareUtf8(previous, platformId) >= 0
    ) {
      return false;
    }
    previous = platformId;
  }
  return true;
}

function hasCoherentSupportEvidenceReference(reference) {
  const passed = ownValue(reference, "passed_check_kinds");
  const executionStatus = ownValue(reference, "execution_status");
  const packagingStatus = ownValue(reference, "packaging_status");
  if (
    !isRecord(reference) ||
    ownValue(reference, "format") !== "world-forge.runtime_evidence" ||
    ownValue(reference, "format_version") !== 1 ||
    !hasConcretePlatform(ownValue(reference, "platform")) ||
    !isCanonicalArray(passed, null) ||
    !passed.every((kind) =>
      ["headless", "native", "packaging", "save_replay"].includes(kind),
    )
  ) {
    return false;
  }
  if (
    executionStatus === "headless_verified" &&
    !(passed.includes("headless") && passed.includes("save_replay"))
  ) {
    return false;
  }
  if (
    executionStatus === "native_verified" &&
    !(
      passed.includes("headless") &&
      passed.includes("native") &&
      passed.includes("save_replay")
    )
  ) {
    return false;
  }
  return (
    packagingStatus !== "verified" ||
    passed.includes("packaging")
  );
}

function hasCoherentSupportReport(value) {
  const dimensions = ownValue(value, "dimensions");
  const execution = ownValue(dimensions, "execution");
  const mechanics = ownValue(value, "mechanics");
  const features = ownValue(value, "features");
  const evidence = ownValue(value, "evidence");
  const missing = ownValue(value, "missing_capabilities");
  const reasons = ownValue(value, "reason_codes");
  if (
    !isRecord(dimensions) ||
    !Array.isArray(evidence) ||
    evidence.length > 64 ||
    !hasCanonicalExecutionPlatforms(execution) ||
    !isCanonicalArray(mechanics, "mechanic_id") ||
    !isCanonicalArray(features, "feature_id") ||
    !isCanonicalArray(missing, null) ||
    !isCanonicalArray(reasons, null)
  ) {
    return false;
  }
  const evidenceIds = [];
  const evidencePlatformIds = [];
  const evidenceById = new Map();
  let previousPlatform;
  for (let index = 0; index < evidence.length; index += 1) {
    const reference = evidence[index];
    const id = ownValue(reference, "id");
    const platformId = ownValue(
      ownValue(reference, "platform"),
      "platform_id",
    );
    if (
      !hasCoherentSupportEvidenceReference(reference) ||
      !isCanonicalString(id) ||
      !isCanonicalString(platformId) ||
      evidenceById.has(id.toLowerCase()) ||
      evidencePlatformIds.includes(platformId) ||
      (previousPlatform !== undefined &&
        compareUtf8(previousPlatform, platformId) >= 0)
    ) {
      return false;
    }
    evidenceById.set(id.toLowerCase(), reference);
    evidenceIds.push(id);
    evidencePlatformIds.push(platformId);
    previousPlatform = platformId;
  }

  const referencedEvidence = [];
  for (let index = 0; index < execution.length; index += 1) {
    const item = execution[index];
    const platform = ownValue(item, "platform");
    const status = ownValue(item, "status");
    const ids = ownValue(item, "evidence_ids");
    if (!isCanonicalArray(ids, null)) {
      return false;
    }
    if (status === "untested") {
      if (ids.length !== 0) {
        return false;
      }
      continue;
    }
    if (ids.length !== 1) {
      return false;
    }
    const reference = evidenceById.get(ids[0].toLowerCase());
    if (
      reference === undefined ||
      JSON.stringify(ownValue(reference, "platform")) !==
        JSON.stringify(platform) ||
      ownValue(reference, "execution_status") !== status
    ) {
      return false;
    }
    referencedEvidence.push(ids[0]);
  }
  if (!sameStrings(referencedEvidence, evidenceIds)) {
    return false;
  }

  const evidenceComplete = evidence.length === execution.length;
  const allHeadless =
    evidenceComplete &&
    execution.every((item) =>
      ["headless_verified", "native_verified"].includes(
        ownValue(item, "status"),
      ),
    );
  const allNative =
    evidenceComplete &&
    execution.every(
      (item) => ownValue(item, "status") === "native_verified",
    );
  const allSaveReplay =
    evidenceComplete &&
    evidence.every((item) =>
      ownValue(item, "passed_check_kinds").includes("save_replay"),
    );
  const anyExecutionFailed = execution.some(
    (item) => ownValue(item, "status") === "failed",
  );
  const anyPackagingFailed = evidence.some(
    (item) => ownValue(item, "packaging_status") === "failed",
  );
  const allPackaging =
    evidenceComplete &&
    evidence.every(
      (item) =>
        ownValue(item, "packaging_status") === "verified" &&
        ownValue(item, "passed_check_kinds").includes("packaging"),
    );
  const expectedPackaging = anyPackagingFailed
    ? "failed"
    : allPackaging
      ? "verified"
      : "unverified";
  if (
    ownValue(dimensions, "packaging") !== expectedPackaging ||
    (ownValue(dimensions, "adapter") === "verified" &&
      evidence.length === 0)
  ) {
    return false;
  }

  const expectedTestEvidence = evidence
    .filter((item) => {
      const kinds = ownValue(item, "passed_check_kinds");
      return kinds.includes("headless") && kinds.includes("save_replay");
    })
    .map((item) => ownValue(item, "id"))
    .sort(compareUtf8);
  const expectedNativeEvidence = evidence
    .filter(
      (item) =>
        ownValue(item, "execution_status") === "native_verified" &&
        ownValue(item, "passed_check_kinds").includes("native"),
    )
    .map((item) => ownValue(item, "id"))
    .sort(compareUtf8);
  const expectedFeatureEvidence = [...evidenceIds].sort(compareUtf8);
  for (let index = 0; index < mechanics.length; index += 1) {
    const mechanic = mechanics[index];
    const status = ownValue(mechanic, "status");
    const itemReasons = ownValue(mechanic, "reason_codes");
    const testEvidence = ownValue(mechanic, "test_evidence");
    const nativeEvidence = ownValue(mechanic, "native_evidence");
    if (
      !isCanonicalArray(itemReasons, null) ||
      !isCanonicalArray(testEvidence, null) ||
      !isCanonicalArray(nativeEvidence, null)
    ) {
      return false;
    }
    if (["supported_current", "game_extension_verified"].includes(status)) {
      if (
        itemReasons.length !== 0 ||
        testEvidence.length === 0 ||
        nativeEvidence.length === 0 ||
        !sameStrings(testEvidence, expectedTestEvidence) ||
        !sameStrings(nativeEvidence, expectedNativeEvidence)
      ) {
        return false;
      }
    } else {
      const expectedItemReasons =
        status === "blocked"
          ? ["required_feature_unsupported"]
          : ownValue(dimensions, "adapter") !== "verified"
            ? ["adapter_not_verified", "execution_evidence_missing"]
            : ["execution_evidence_missing"];
      if (
        !sameStrings(itemReasons, expectedItemReasons) ||
        testEvidence.length !== 0 ||
        nativeEvidence.length !== 0
      ) {
        return false;
      }
    }
  }
  for (let index = 0; index < features.length; index += 1) {
    const feature = features[index];
    const status = ownValue(feature, "status");
    const itemReasons = ownValue(feature, "reason_codes");
    const itemEvidence = ownValue(feature, "evidence_ids");
    if (
      !isCanonicalArray(itemReasons, null) ||
      !isCanonicalArray(itemEvidence, null)
    ) {
      return false;
    }
    if (["supported_current", "game_extension_verified"].includes(status)) {
      if (
        itemReasons.length !== 0 ||
        itemEvidence.length === 0 ||
        !sameStrings(itemEvidence, expectedFeatureEvidence)
      ) {
        return false;
      }
    } else {
      const expectedItemReasons =
        status === "blocked"
          ? ["required_feature_unsupported"]
          : ownValue(dimensions, "adapter") !== "verified"
            ? ["adapter_not_verified", "execution_evidence_missing"]
            : ["execution_evidence_missing"];
      if (
        !sameStrings(itemReasons, expectedItemReasons) ||
        itemEvidence.length !== 0
      ) {
        return false;
      }
    }
  }
  const expectedMissing = features
    .filter((item) => ownValue(item, "status") === "blocked")
    .map((item) => ownValue(item, "feature_id"))
    .sort(compareUtf8);
  if (!sameStrings(missing, expectedMissing)) {
    return false;
  }
  const expectedReasons = [];
  if (missing.length > 0) {
    expectedReasons.push("required_feature_unsupported");
  }
  if (ownValue(dimensions, "adapter") !== "verified") {
    expectedReasons.push("adapter_not_verified");
  }
  if (!allHeadless) {
    expectedReasons.push("headless_evidence_missing");
  }
  if (!allNative) {
    expectedReasons.push("native_evidence_missing");
  }
  if (!allPackaging) {
    expectedReasons.push(
      anyPackagingFailed
        ? "packaging_evidence_failed"
        : "packaging_evidence_missing",
    );
  }
  if (!allSaveReplay) {
    expectedReasons.push("save_replay_evidence_missing");
  }
  if (anyExecutionFailed) {
    expectedReasons.push("execution_evidence_failed");
  }
  expectedReasons.sort(compareUtf8);
  if (!sameStrings(reasons, [...new Set(expectedReasons)])) {
    return false;
  }
  const expectedSupported =
    ownValue(dimensions, "authoring") === "valid" &&
    ownValue(dimensions, "compilation") === "compiled" &&
    ownValue(dimensions, "assets") === "sealed" &&
    ownValue(dimensions, "adapter") === "verified" &&
    allNative &&
    allSaveReplay &&
    allPackaging &&
    !anyExecutionFailed &&
    !anyPackagingFailed &&
    missing.length === 0 &&
    reasons.length === 0 &&
    allResolvedItemsComplete(mechanics) &&
    allResolvedItemsComplete(features);
  const supported = ownValue(value, "supported");
  const expectedCompatibility = expectedSupported
    ? "supported"
    : missing.length > 0
      ? "unsupported"
      : "partially_supported";
  return (
    supported === expectedSupported &&
    ownValue(value, "compatibility_status") === expectedCompatibility &&
    ownValue(dimensions, "release") ===
      (expectedSupported ? "ready" : "blocked")
  );
}

function samePlatform(left, right) {
  return (
    isRecord(left) &&
    isRecord(right) &&
    ownValue(left, "platform_id") === ownValue(right, "platform_id") &&
    ownValue(left, "platform_family") === ownValue(right, "platform_family") &&
    ownValue(left, "architecture") === ownValue(right, "architecture") &&
    ownValue(left, "backend") === ownValue(right, "backend") &&
    ownValue(left, "renderer") === ownValue(right, "renderer")
  );
}

function hasCoherentRuntimeSupportAuthority(value) {
  const headless = ownValue(value, "headless_evidence");
  const runtimeEvidence = ownValue(value, "runtime_evidence");
  const packageEvidence = ownValue(value, "package_evidence");
  const reasons = ownValue(value, "reason_codes");
  if (
    !Array.isArray(headless) ||
    headless.length > 32 ||
    !Array.isArray(runtimeEvidence) ||
    runtimeEvidence.length > 32 ||
    !isCanonicalArray(reasons, null, { casefold: false }) ||
    reasons.length < 1 ||
    reasons.length > 64 ||
    !reasons.includes("runtime_support_authority_native_unavailable") ||
    ownValue(value, "native_status") !== "unavailable" ||
    ownValue(value, "release_status") !== "blocked" ||
    ownValue(value, "supported") !== false
  ) {
    return false;
  }

  const headlessByPlatform = new Map();
  let previousPlatform;
  for (let index = 0; index < headless.length; index += 1) {
    const record = headless[index];
    const platform = ownValue(record, "platform");
    const runtimeReference = ownValue(record, "runtime_evidence");
    const platformId = ownValue(platform, "platform_id");
    if (
      !isRecord(record) ||
      !hasConcretePlatform(platform) ||
      !isRecord(runtimeReference) ||
      !samePlatform(platform, ownValue(runtimeReference, "platform")) ||
      ownValue(runtimeReference, "execution_status") !== "headless_verified" ||
      !["unverified", "verified"].includes(
        ownValue(runtimeReference, "packaging_status"),
      ) ||
      typeof platformId !== "string" ||
      headlessByPlatform.has(platformId) ||
      (previousPlatform !== undefined &&
        compareUtf8(previousPlatform, platformId) >= 0)
    ) {
      return false;
    }
    headlessByPlatform.set(platformId, ownValue(runtimeReference, "id"));
    previousPlatform = platformId;
  }

  const packagePresent = packageEvidence !== null;
  const runtimePlatforms = [];
  for (let index = 0; index < runtimeEvidence.length; index += 1) {
    const reference = runtimeEvidence[index];
    const platform = ownValue(reference, "platform");
    const platformId = ownValue(platform, "platform_id");
    if (
      !isRecord(reference) ||
      !hasConcretePlatform(platform) ||
      ownValue(reference, "execution_status") !== "headless_verified" ||
      ownValue(reference, "packaging_status") !==
        (packagePresent ? "verified" : "unverified") ||
      typeof platformId !== "string" ||
      (!packagePresent &&
        headlessByPlatform.get(platformId) !== ownValue(reference, "id"))
    ) {
      return false;
    }
    runtimePlatforms.push(platformId);
  }
  if (
    JSON.stringify(runtimePlatforms) !==
    JSON.stringify([...headlessByPlatform.keys()])
  ) {
    return false;
  }

  if (packagePresent) {
    if (!isRecord(packageEvidence)) {
      return false;
    }
    const runtimeBundleHash = ownValue(packageEvidence, "runtime_bundle_hash");
    const bundleHashes = new Set(
      headless.map((record) =>
        ownValue(ownValue(record, "runtime_bundle"), "content_hash"),
      ),
    );
    if (
      typeof runtimeBundleHash !== "string" ||
      bundleHashes.size !== 1 ||
      !bundleHashes.has(runtimeBundleHash)
    ) {
      return false;
    }
  }

  const support = ownValue(value, "runtime_support_report");
  return (
    isRecord(support) &&
    ownValue(support, "release_status") === "blocked" &&
    ownValue(support, "supported") === false
  );
}

export function canonicalGenericRuntimeDerivedId(value, kind) {
  try {
    if (!isRecord(value)) {
      return null;
    }
    const policy = derivedIdPolicies[kind];
    if (policy === undefined) {
      return null;
    }
    const seed = buildSeed(value, policy.fields);
    const digest =
      seed === null ? null : canonicalGenericAssetContentHash(seed);
    return digest === null ? null : `${policy.prefix}${digest.slice(0, 40)}`;
  } catch {
    return null;
  }
}

export function hasCoherentGenericRuntimeContract(value, kind) {
  try {
    if (!isRecord(value) || !hasCanonicalGenericAssetContentHash(value)) {
      return false;
    }
    if (kind === "generic-runtime-adapter") {
      return hasCoherentAdapter(value);
    }
    const policy = derivedIdPolicies[kind];
    if (
      policy === undefined ||
      ownValue(value, policy.idField) !==
        canonicalGenericRuntimeDerivedId(value, kind)
    ) {
      return false;
    }
    return {
      "game-runtime-composition": hasCoherentComposition,
      "game-runtime-snapshot": hasCoherentSnapshot,
      "generic-runtime-adapter-registry": hasCoherentRegistry,
      "generic-runtime-evidence": hasCoherentEvidence,
      "generic-runtime-support-report": hasCoherentSupportReport,
      "runtime-support-authority": hasCoherentRuntimeSupportAuthority,
    }[kind](value);
  } catch {
    return false;
  }
}

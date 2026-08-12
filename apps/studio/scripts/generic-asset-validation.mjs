import { createHash } from "node:crypto";

export const GENERIC_ASSET_ID_PATTERN =
  "^(?!(?:aux|con|nul|prn|com[1-9]|lpt[1-9])$)[a-z][a-z0-9_]{1,63}$";
export const GENERIC_ASSET_GLYPH_RANGE_PATTERN =
  "^U\\+[0-9A-F]{4,6}-[0-9A-F]{4,6}$";
export const GENERIC_ASSET_RUNTIME_STRING_PATTERN =
  "^(?!\\s*(?:[A-Za-z][A-Za-z0-9+.-]*://|[Ff][Ii][Ll][Ee]:[\\\\/]|[A-Za-z]:[\\\\/]|[\\\\/]{2}|/|@[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:@[^\\s/\\\\]+)?\\s*$|(?:[Pp][Rr][Oo][Vv][Ii][Dd][Ee][Rr](?:_[Ii][Dd])?|[Mm][Oo][Dd][Ee][Ll](?:_[Ii][Dd])?|[Cc][Rr][Ee][Dd][Ee][Nn][Tt][Ii][Aa][Ll][Ss]?|[Pp][Rr][Oo][Mm][Pp][Tt]|[Tt][Oo][Kk][Ee][Nn]|[Ee][Nn][Dd][Pp][Oo][Ii][Nn][Tt])\\s*(?:=|:\\s*|\\s+[Ii][Ss]\\s+)|\\.{1,2}(?:[\\\\/]|\\s*$)))(?![\\s\\S]*[\\\\/]\\.{1,2}(?:[\\\\/]|\\s*$))(?!\\s*[^/\\\\\\r\\n]+(?:[\\\\/][^/\\\\\\r\\n]+)+\\.[A-Za-z0-9][A-Za-z0-9._-]*\\s*$)[\\s\\S]*$";

const genericAssetId = new RegExp(GENERIC_ASSET_ID_PATTERN, "u");
const glyphRange = new RegExp(GENERIC_ASSET_GLYPH_RANGE_PATTERN, "u");
const runtimeString = new RegExp(GENERIC_ASSET_RUNTIME_STRING_PATTERN, "u");
const authoringOnlyNotice = new RegExp(
  "\\b(?:apis?|credentials?|datasets?|endpoints?|instructions?|mcps?|models?|prompts?|providers?|seeds?|tokens?|weights?)\\b",
  "iu",
);
const secretLikeValue =
  /(?:api[_ -]?key|authorization|bearer|credential|password|private[_ -]?key|token)\s*(?:=|:)/iu;
const credentialLikeNotice =
  /(?:^[ \t]*bearer[ \t]+[A-Za-z0-9._~+/=-]{8,}[ \t]*$|\bsk-[A-Za-z0-9_-]{12,}\b|\bAKIA[0-9A-Z]{16}\b|\bgh[pousr]_[A-Za-z0-9]{36,255}\b|\bxox[baprs]-[A-Za-z0-9-]{10,255}\b|\bAIza[0-9A-Za-z_-]{35}\b|^[ \t]*(?:authorization|proxy-authorization|x-api-key|api-key)[ \t]*:[ \t]*\S+|-----BEGIN(?: [A-Z0-9]+)* PRIVATE KEY-----)/imu;
const jwtSegment = /^[A-Za-z0-9_-]+$/u;
const urlLikeValue = /(?:[A-Za-z][A-Za-z0-9+.-]*:\/\/|www\.)/iu;
const runtimeNoticeMaxCodePoints = 1024;
const runtimeNoticeMaxUtf8Bytes = 4096;
const jwtHeaderMaxCharacters = 512;
const windowsReservedNames = new Set([
  "aux",
  "con",
  "nul",
  "prn",
  ...Array.from({ length: 9 }, (_, index) => `com${String(index + 1)}`),
  ...Array.from({ length: 9 }, (_, index) => `lpt${String(index + 1)}`),
]);

const arrayEveryIntrinsic = Array.prototype.every;
const arrayIncludesIntrinsic = Array.prototype.includes;
const arrayJoinIntrinsic = Array.prototype.join;
const arrayReduceIntrinsic = Array.prototype.reduce;
const arraySomeIntrinsic = Array.prototype.some;
const arraySortIntrinsic = Array.prototype.sort;
const numberIsSafeIntegerIntrinsic = Number.isSafeInteger;

function isolatedArray(length = 0) {
  const value = new Array(length);
  Reflect.setPrototypeOf(value, null);
  return value;
}

function appendArray(value, item) {
  Object.defineProperty(value, String(value.length), {
    configurable: true,
    enumerable: true,
    value: item,
    writable: true,
  });
}

function arrayEvery(value, callback) {
  return Reflect.apply(arrayEveryIntrinsic, value, [callback]);
}

function arrayJoin(value, separator) {
  return Reflect.apply(arrayJoinIntrinsic, value, [separator]);
}

function arrayIncludes(value, item) {
  return Reflect.apply(arrayIncludesIntrinsic, value, [item]);
}

function arrayMap(value, callback) {
  const result = isolatedArray(value.length);
  for (let index = 0; index < value.length; index += 1) {
    Object.defineProperty(result, String(index), {
      configurable: true,
      enumerable: true,
      value: callback(value[index], index, value),
      writable: true,
    });
  }
  return result;
}

function arrayReduce(value, callback, initialValue) {
  return Reflect.apply(arrayReduceIntrinsic, value, [callback, initialValue]);
}

function arraySlice(value, start, end) {
  const boundedStart = Math.max(0, Math.min(value.length, start));
  const boundedEnd = Math.max(boundedStart, Math.min(value.length, end));
  const result = isolatedArray(boundedEnd - boundedStart);
  for (let index = boundedStart; index < boundedEnd; index += 1) {
    Object.defineProperty(result, String(index - boundedStart), {
      configurable: true,
      enumerable: true,
      value: value[index],
      writable: true,
    });
  }
  return result;
}

function arraySome(value, callback) {
  return Reflect.apply(arraySomeIntrinsic, value, [callback]);
}

function arraySort(value, callback) {
  Reflect.apply(arraySortIntrinsic, value, [callback]);
  return value;
}

export function isGenericAssetIdentifier(value) {
  return (
    typeof value === "string" &&
    value.normalize("NFC") === value &&
    genericAssetId.test(value)
  );
}

function canonicalCodepoint(value) {
  return value.toString(16).toUpperCase().padStart(4, "0");
}

export function areCanonicalGenericAssetGlyphRanges(value) {
  if (!Array.isArray(value) || value.length < 1 || value.length > 256) {
    return false;
  }
  let previousEnd = -1;
  const exact = new Set();
  for (let index = 0; index < value.length; index += 1) {
    const candidate = value[index];
    if (
      typeof candidate !== "string" ||
      !glyphRange.test(candidate) ||
      exact.has(candidate)
    ) {
      return false;
    }
    exact.add(candidate);
    const bounds = candidate.slice(2).split("-", 2);
    const startText = bounds[0];
    const endText = bounds[1];
    const start = Number.parseInt(startText, 16);
    const end = Number.parseInt(endText, 16);
    if (
      startText !== canonicalCodepoint(start) ||
      endText !== canonicalCodepoint(end) ||
      end > 0x10ffff ||
      start > end ||
      start <= previousEnd
    ) {
      return false;
    }
    previousEnd = end;
  }
  return true;
}

export function isPortableGenericAssetRuntimePath(value) {
  if (
    typeof value !== "string" ||
    value.length === 0 ||
    value.normalize("NFC") !== value ||
    !/^[\x20-\x7e]+$/u.test(value) ||
    Buffer.byteLength(value, "utf8") > 1024 ||
    value.startsWith("/") ||
    value.includes("\\") ||
    containsInvalidUnicode(value)
  ) {
    return false;
  }
  const components = value.split("/");
  if (components.length < 1 || components.length > 16) {
    return false;
  }
  return arrayEvery(components, (component) => {
    if (
      component.length === 0 ||
      component === "." ||
      component === ".." ||
      component.endsWith(" ") ||
      component.endsWith(".") ||
      Buffer.byteLength(component, "utf8") > 255
    ) {
      return false;
    }
    for (const character of component) {
      if (character.codePointAt(0) < 32 || '<>:"/\\|?*'.includes(character)) {
        return false;
      }
    }
    return !windowsReservedNames.has(component.split(".", 1)[0].toLowerCase());
  });
}

export function hasDistinctGenericAssetContentHashes(value) {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    return false;
  }
  const document = value;
  const hashes = isolatedArray();
  if (Array.isArray(document.input_artifacts)) {
    for (let index = 0; index < document.input_artifacts.length; index += 1) {
      appendArray(hashes, document.input_artifacts[index]?.sha256);
    }
  }
  if (document.format === "world-forge.asset_production_receipt") {
    if (Array.isArray(document.lineage_parents)) {
      for (let index = 0; index < document.lineage_parents.length; index += 1) {
        appendArray(hashes, document.lineage_parents[index]?.content_hash);
      }
    }
    if (Array.isArray(document.outputs)) {
      for (let index = 0; index < document.outputs.length; index += 1) {
        appendArray(hashes, document.outputs[index]?.sha256);
      }
    }
    appendArray(hashes, document.content_hash);
  }
  if (!arrayEvery(hashes, (digest) => typeof digest === "string")) {
    return false;
  }
  const distinct = new Set();
  for (let index = 0; index < hashes.length; index += 1) {
    distinct.add(hashes[index]);
  }
  return distinct.size === hashes.length;
}

export function isRuntimeSafeGenericAssetNotice(value) {
  return (
    isSafeGenericAssetRuntimeText(value) &&
    !authoringOnlyNotice.test(value) &&
    !credentialLikeNotice.test(value) &&
    !containsStandaloneJwt(value)
  );
}

export function preflightGenericAssetRuntimeText(value) {
  if (typeof value !== "string") {
    return false;
  }
  let codePointCount = 0;
  let utf8Bytes = 0;
  for (let index = 0; index < value.length; ) {
    const codePoint = value.codePointAt(index);
    if (
      codePoint === undefined ||
      (codePoint >= 0xd800 && codePoint <= 0xdfff)
    ) {
      return false;
    }
    codePointCount += 1;
    if (codePointCount > runtimeNoticeMaxCodePoints) {
      return false;
    }
    utf8Bytes +=
      codePoint <= 0x7f
        ? 1
        : codePoint <= 0x7ff
          ? 2
          : codePoint <= 0xffff
            ? 3
            : 4;
    if (utf8Bytes > runtimeNoticeMaxUtf8Bytes) {
      return false;
    }
    index += codePoint > 0xffff ? 2 : 1;
  }
  return codePointCount >= 1;
}

export function isSafeGenericAssetRuntimeText(value) {
  if (
    !preflightGenericAssetRuntimeText(value) ||
    value.normalize("NFC") !== value
  ) {
    return false;
  }
  return (
    runtimeString.test(value) &&
    !urlLikeValue.test(value) &&
    !secretLikeValue.test(value)
  );
}

function containsStandaloneJwt(value) {
  const lines = value.split(/\r\n?|\n/u);
  for (let lineIndex = 0; lineIndex < lines.length; lineIndex += 1) {
    const rawLine = lines[lineIndex];
    const candidate = rawLine.trim();
    const segments = candidate.split(".");
    if (
      segments.length !== 3 ||
      !arrayEvery(segments, (segment) => jwtSegment.test(segment))
    ) {
      continue;
    }
    if (segments[0].length > jwtHeaderMaxCharacters) {
      return true;
    }
    try {
      const headerBytes = Buffer.from(segments[0], "base64url");
      const header = JSON.parse(
        new TextDecoder("utf-8", { fatal: true }).decode(headerBytes),
      );
      if (
        header !== null &&
        typeof header === "object" &&
        !Array.isArray(header) &&
        Object.hasOwn(header, "alg") &&
        typeof header.alg === "string" &&
        header.alg.length > 0
      ) {
        return true;
      }
    } catch {
      continue;
    }
  }
  return false;
}

export function hasMatchingGenericAssetTextSha256(value) {
  return (
    value !== null &&
    typeof value === "object" &&
    !Array.isArray(value) &&
    typeof value.text === "string" &&
    typeof value.sha256 === "string" &&
    createHash("sha256").update(value.text, "utf8").digest("hex") === value.sha256
  );
}

function canonicalJson(value) {
  if (value === null || typeof value === "boolean" || typeof value === "string") {
    return JSON.stringify(value);
  }
  if (typeof value === "number") {
    return Number.isSafeInteger(value) ? JSON.stringify(value) : null;
  }
  if (Array.isArray(value)) {
    const encoded = isolatedArray();
    for (let index = 0; index < value.length; index += 1) {
      const item = canonicalJson(value[index]);
      if (item === null) {
        return null;
      }
      appendArray(encoded, item);
    }
    return `[${arrayJoin(encoded, ",")}]`;
  }
  if (typeof value !== "object") {
    return null;
  }
  const keys = Object.keys(value);
  Reflect.setPrototypeOf(keys, null);
  arraySort(keys, (left, right) =>
    Buffer.compare(Buffer.from(left, "utf8"), Buffer.from(right, "utf8")),
  );
  const fields = isolatedArray();
  for (let index = 0; index < keys.length; index += 1) {
    const key = keys[index];
    const encoded = canonicalJson(value[key]);
    if (encoded === null) {
      return null;
    }
    appendArray(fields, `${JSON.stringify(key)}:${encoded}`);
  }
  return `{${arrayJoin(fields, ",")}}`;
}

export function canonicalGenericAssetContentHash(value) {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    return null;
  }
  const payload = Object.create(null);
  const keys = Object.keys(value);
  for (let index = 0; index < keys.length; index += 1) {
    const key = keys[index];
    if (key !== "content_hash") {
      Object.defineProperty(payload, key, {
        configurable: true,
        enumerable: true,
        value: value[key],
        writable: true,
      });
    }
  }
  const encoded = canonicalJson(payload);
  return encoded === null
    ? null
    : createHash("sha256").update(encoded, "utf8").digest("hex");
}

export function hasCanonicalGenericAssetContentHash(value) {
  return (
    value !== null &&
    typeof value === "object" &&
    !Array.isArray(value) &&
    typeof value.content_hash === "string" &&
    canonicalGenericAssetContentHash(value) === value.content_hash
  );
}

export function hasCoherentGenericAssetProductionRequest(value) {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    return false;
  }
  const operation = value.operation;
  const toolchain = value.toolchain_requirements;
  const reproducibility = value.reproducibility;
  if (
    operation === null ||
    typeof operation !== "object" ||
    Array.isArray(operation) ||
    toolchain === null ||
    typeof toolchain !== "object" ||
    Array.isArray(toolchain) ||
    reproducibility === null ||
    typeof reproducibility !== "object" ||
    Array.isArray(reproducibility) ||
    operation.operation_id !== toolchain.operation_id ||
    value.production_class !== toolchain.production_class
  ) {
    return false;
  }
  if (value.production_class === "human" || value.production_class === "external_authoring") {
    return reproducibility.seed_policy === "forbidden";
  }
  if (value.production_class === "procedural_offline") {
    return (
      (reproducibility.seed_policy === "fixed" && Number.isSafeInteger(toolchain.seed)) ||
      (reproducibility.seed_policy === "forbidden" && toolchain.seed === null) ||
      (reproducibility.seed_policy === "recorded" &&
        Number.isSafeInteger(toolchain.seed))
    );
  }
  return (
    value.production_class === "generative_authoring" &&
    (reproducibility.seed_policy === "fixed" ||
      reproducibility.seed_policy === "recorded") &&
    reproducibility.seed_policy === toolchain.seed_policy
  );
}

export function hasExactGenericAssetReceiptLineageRoots(value) {
  if (
    value === null ||
    typeof value !== "object" ||
    Array.isArray(value) ||
    value.receipt === null ||
    typeof value.receipt !== "object" ||
    Array.isArray(value.receipt) ||
    value.receipt_lineage === null ||
    typeof value.receipt_lineage !== "object" ||
    Array.isArray(value.receipt_lineage) ||
    !Array.isArray(value.receipt_lineage.closures) ||
    !Array.isArray(value.rejected_candidates)
  ) {
    return false;
  }
  const roots = isolatedArray();
  appendArray(roots, value.receipt);
  for (let index = 0; index < value.rejected_candidates.length; index += 1) {
    appendArray(roots, value.rejected_candidates[index]?.receipt);
  }
  if (
    !arrayEvery(
      roots,
      (root) =>
        root !== null &&
        typeof root === "object" &&
        !Array.isArray(root) &&
        typeof root.id === "string" &&
        typeof root.content_hash === "string",
    )
  ) {
    return false;
  }
  const expectedById = new Map();
  for (let index = 0; index < roots.length; index += 1) {
    const root = roots[index];
    if (expectedById.has(root.id)) {
      return false;
    }
    expectedById.set(root.id, root);
  }
  if (value.receipt_lineage.closures.length !== expectedById.size) {
    return false;
  }
  const actualIds = new Set();
  for (
    let closureIndex = 0;
    closureIndex < value.receipt_lineage.closures.length;
    closureIndex += 1
  ) {
    const closure = value.receipt_lineage.closures[closureIndex];
    if (
      closure === null ||
      typeof closure !== "object" ||
      Array.isArray(closure) ||
      closure.root === null ||
      typeof closure.root !== "object" ||
      Array.isArray(closure.root) ||
      !Array.isArray(closure.parents) ||
      actualIds.has(closure.root.id)
    ) {
      return false;
    }
    const expected = expectedById.get(closure.root.id);
    if (expected === undefined || canonicalJson(expected) !== canonicalJson(closure.root)) {
      return false;
    }
    actualIds.add(closure.root.id);
    if (
      arraySome(
        closure.parents,
        (parent) =>
          parent !== null &&
          typeof parent === "object" &&
          !Array.isArray(parent) &&
          expectedById.has(parent.id),
      )
    ) {
      return false;
    }
  }
  return actualIds.size === expectedById.size;
}

export function hasMatchingGenericAssetGlyphCount(value) {
  if (
    value === null ||
    typeof value !== "object" ||
    Array.isArray(value) ||
    !Number.isSafeInteger(value.glyph_count) ||
    !areCanonicalGenericAssetGlyphRanges(value.glyph_ranges)
  ) {
    return false;
  }
  const covered = arrayReduce(value.glyph_ranges, (total, candidate) => {
    const bounds = candidate.slice(2).split("-", 2);
    const startText = bounds[0];
    const endText = bounds[1];
    return total + Number.parseInt(endText, 16) - Number.parseInt(startText, 16) + 1;
  }, 0);
  return value.glyph_count === covered;
}

function objectField(value, path) {
  let current = value;
  const components = path.split(".");
  for (let index = 0; index < components.length; index += 1) {
    const component = components[index];
    if (
      current === null ||
      typeof current !== "object" ||
      Array.isArray(current) ||
      !Object.hasOwn(current, component)
    ) {
      return undefined;
    }
    current = current[component];
  }
  return current;
}

function compareUtf8Tuples(left, right) {
  for (let index = 0; index < left.length; index += 1) {
    const leftPart = left[index];
    const rightPart = right[index];
    const compared =
      typeof leftPart === "number" && typeof rightPart === "number"
        ? leftPart - rightPart
        : Buffer.compare(Buffer.from(leftPart), Buffer.from(rightPart));
    if (compared !== 0) {
      return compared;
    }
  }
  return 0;
}

export function isCanonicalGenericAssetObjectArray(value, policy) {
  if (
    !Array.isArray(value) ||
    policy === null ||
    typeof policy !== "object" ||
    Array.isArray(policy) ||
    !Array.isArray(policy.orderBy) ||
    !Array.isArray(policy.uniqueBy) ||
    policy.orderBy.length < 1 ||
    policy.uniqueBy.length < 1
  ) {
    return false;
  }
  const order = isolatedArray();
  const seenByDomain = arrayMap(policy.uniqueBy, () => new Set());
  for (let itemIndex = 0; itemIndex < value.length; itemIndex += 1) {
    const item = value[itemIndex];
    if (item === null || typeof item !== "object" || Array.isArray(item)) {
      return false;
    }
    const orderKey = arrayMap(policy.orderBy, (field) => objectField(item, field));
    if (
      !arrayEvery(
        orderKey,
        (field) =>
          typeof field === "string" ||
          (typeof field === "number" &&
            Reflect.apply(numberIsSafeIntegerIntrinsic, Number, [field])),
      )
    ) {
      return false;
    }
    appendArray(order, orderKey);
    for (let index = 0; index < policy.uniqueBy.length; index += 1) {
      const fields = policy.uniqueBy[index];
      if (!Array.isArray(fields) || fields.length < 1) {
        return false;
      }
      const parts = arrayMap(fields, (field) => objectField(item, field));
      if (
        !arrayEvery(
          parts,
          (field) =>
            typeof field === "string" ||
            (typeof field === "number" &&
              Reflect.apply(numberIsSafeIntegerIntrinsic, Number, [field])),
        )
      ) {
        return false;
      }
      const normalized = JSON.stringify(
        arrayMap(parts, (field) =>
          typeof field === "string"
            ? `s:${field.normalize("NFC").toLowerCase()}`
            : `n:${String(field)}`,
        ),
      );
      if (seenByDomain[index].has(normalized)) {
        return false;
      }
      seenByDomain[index].add(normalized);
    }
  }
  return arrayEvery(
    order,
    (entry, index) => index === 0 || compareUtf8Tuples(order[index - 1], entry) < 0,
  );
}

export function isCanonicalGenericAssetStringArray(value) {
  if (
    !Array.isArray(value) ||
    !arrayEvery(value, (item) => typeof item === "string")
  ) {
    return false;
  }
  const normalized = arrayMap(value, (item) => item.normalize("NFC").toLowerCase());
  const distinct = new Set();
  for (let index = 0; index < normalized.length; index += 1) {
    distinct.add(normalized[index]);
  }
  if (distinct.size !== normalized.length) {
    return false;
  }
  return arrayEvery(
    value,
    (item, index) =>
      index === 0 || Buffer.compare(Buffer.from(value[index - 1]), Buffer.from(item)) < 0,
  );
}

export function hasPortableGenericAssetPathTree(value, field) {
  if (!Array.isArray(value) || typeof field !== "string") {
    return false;
  }
  const paths = arrayMap(value, (item) => objectField(item, field));
  return hasPortableGenericAssetPathList(paths);
}

function hasPortableGenericAssetPathList(paths) {
  if (!arrayEvery(paths, (item) => isPortableGenericAssetRuntimePath(item))) {
    return false;
  }
  // isPortableGenericAssetRuntimePath narrows paths to printable ASCII, where
  // lowercase and Unicode casefold are identical.
  const components = arrayMap(paths, (item) =>
    arrayMap(item.split("/"), (component) => component.toLowerCase()),
  );
  for (let left = 0; left < components.length; left += 1) {
    for (let right = left + 1; right < components.length; right += 1) {
      const shared = Math.min(components[left].length, components[right].length);
      if (
        arrayEvery(
          arraySlice(components[left], 0, shared),
          (component, index) => component === components[right][index],
        )
      ) {
        return false;
      }
    }
  }
  return true;
}

function hasDistinctCasefoldStrings(values) {
  if (!Array.isArray(values)) {
    return false;
  }
  const distinct = new Set();
  for (let index = 0; index < values.length; index += 1) {
    const value = values[index];
    if (typeof value !== "string") {
      return false;
    }
    distinct.add(value.normalize("NFC").toLowerCase());
  }
  return distinct.size === values.length;
}

function equalCanonicalJson(left, right) {
  const encodedLeft = canonicalJson(left);
  return encodedLeft !== null && encodedLeft === canonicalJson(right);
}

function hasCoherentRecipe(value) {
  if (!Array.isArray(value.licenses) || !Array.isArray(value.steps)) {
    return false;
  }
  if (
    value.licenses.length !== value.steps.length ||
    value.steps.length < 1
  ) {
    return false;
  }
  const paths = isolatedArray();
  for (let index = 0; index < value.steps.length; index += 1) {
    const binding = value.licenses[index];
    const step = value.steps[index];
    if (
      binding === null ||
      typeof binding !== "object" ||
      Array.isArray(binding) ||
      step === null ||
      typeof step !== "object" ||
      Array.isArray(step) ||
      binding.candidate_artifact_id !== step.candidate_artifact_id ||
      binding.role !== step.role ||
      !equalCanonicalJson(binding.license_record, step.license_record) ||
      step.license_record?.candidate_artifact_id !==
        step.candidate_artifact_id ||
      step.license_record?.role !== step.role
    ) {
      return false;
    }
    appendArray(paths, step.source_locator);
    appendArray(paths, step.runtime_path);
    appendArray(paths, step.output_locator);
  }
  return hasPortableGenericAssetPathList(paths);
}

function hasCoherentReceipt(value) {
  if (
    !Array.isArray(value.outputs) ||
    !Array.isArray(value.failure_reasons) ||
    !hasDistinctCasefoldStrings(arrayMap(value.outputs, (output) => output?.role))
  ) {
    return false;
  }
  if (value.status === "completed") {
    return (
      value.outputs.length > 0 &&
      value.failure_reasons.length === 0 &&
      value.recovery === null
    );
  }
  if (
    value.status !== "failed" ||
    value.outputs.length !== 0 ||
    value.failure_reasons.length !== 1 ||
    value.recovery === null ||
    typeof value.recovery !== "object" ||
    Array.isArray(value.recovery) ||
    value.recovery.failure_code !== value.failure_reasons[0] ||
    !equalCanonicalJson(value.recovery.recipe, value.recipe) ||
    !hasCanonicalGenericAssetContentHash(value.recovery) ||
    !Array.isArray(value.recovery.retained_artifacts)
  ) {
    return false;
  }
  const retained = value.recovery.retained_artifacts;
  if (!hasDistinctCasefoldStrings(arrayMap(retained, (artifact) => artifact?.role))) {
    return false;
  }
  return hasPortableGenericAssetPathList(
    arrayMap(retained, (artifact) => artifact?.locator),
  );
}

function qaOutputBlockers(value) {
  if (!Array.isArray(value.outputs) || !Array.isArray(value.acceptance_criteria)) {
    return null;
  }
  const blockers = isolatedArray();
  const roles = arrayMap(value.outputs, (output) => output?.role);
  const candidates = arrayMap(
    value.outputs,
    (output) => output?.candidate_artifact_id,
  );
  if (
    !hasDistinctCasefoldStrings(roles) ||
    !hasDistinctCasefoldStrings(candidates)
  ) {
    return null;
  }
  const paths = isolatedArray();
  for (let outputIndex = 0; outputIndex < value.outputs.length; outputIndex += 1) {
    const output = value.outputs[outputIndex];
    if (
      output === null ||
      typeof output !== "object" ||
      Array.isArray(output) ||
      !Array.isArray(output.checks)
    ) {
      return null;
    }
    appendArray(paths, output.runtime_path);
    appendArray(paths, output.locator);
    const mediaCheck = {
      "application/json": "json",
      "audio/wav": "wav",
      "font/otf": "font",
      "font/ttf": "font",
      "image/png": "png",
      "model/gltf-binary": "glb",
      "text/x-glsl": "glsl",
    }[output.media_type];
    if (typeof mediaCheck !== "string" || output.checks.length !== 10) {
      return null;
    }
    for (let checkIndex = 0; checkIndex < output.checks.length; checkIndex += 1) {
      const check = output.checks[checkIndex];
      const expectedId = [
        "hash",
        "media",
        "path",
        "license",
        "png",
        "wav",
        "font",
        "glsl",
        "json",
        "glb",
      ][checkIndex];
      if (
        check === null ||
        typeof check !== "object" ||
        Array.isArray(check) ||
        check.check_id !== expectedId
      ) {
        return null;
      }
      const applicable = arrayIncludes(
        ["hash", "media", "path", "license", mediaCheck],
        expectedId,
      );
      if (
        (applicable && !arrayIncludes(["passed", "failed"], check.status)) ||
        (!applicable && check.status !== "not_applicable") ||
        (arrayIncludes(["path", "license"], expectedId) &&
          check.status !== "passed")
      ) {
        return null;
      }
      if (check.status === "failed") {
        appendArray(
          blockers,
          `output_${String(output.role)}_${String(expectedId)}_failed`,
        );
      }
    }
    const byId = Object.create(null);
    for (let index = 0; index < output.checks.length; index += 1) {
      byId[output.checks[index].check_id] = output.checks[index].status;
    }
    if (
      (output.metadata === null &&
        (byId.media !== "failed" || byId[mediaCheck] !== "failed")) ||
      (output.metadata !== null && byId[mediaCheck] !== "passed")
    ) {
      return null;
    }
  }
  if (!hasPortableGenericAssetPathList(paths)) {
    return null;
  }
  const criterionHashes = isolatedArray();
  for (
    let criterionIndex = 0;
    criterionIndex < value.acceptance_criteria.length;
    criterionIndex += 1
  ) {
    const criterion = value.acceptance_criteria[criterionIndex];
    if (
      criterion === null ||
      typeof criterion !== "object" ||
      Array.isArray(criterion) ||
      criterion.criterion_index !== criterionIndex
    ) {
      return null;
    }
    appendArray(criterionHashes, criterion.criterion_sha256);
    if (criterion.status === "failed") {
      appendArray(
        blockers,
        `acceptance_criterion_${String(criterionIndex)}_failed`,
      );
    }
  }
  if (!hasDistinctCasefoldStrings(criterionHashes)) {
    return null;
  }
  arraySort(blockers, (left, right) =>
    Buffer.compare(Buffer.from(left, "utf8"), Buffer.from(right, "utf8")),
  );
  return blockers;
}

function hasCoherentQa(value) {
  const blockers = qaOutputBlockers(value);
  return (
    blockers !== null &&
    Array.isArray(value.blockers) &&
    equalCanonicalJson(blockers, value.blockers) &&
    value.status === (blockers.length > 0 ? "failed" : "passed") &&
    value.multi_output_check?.status ===
      (value.outputs.length > 1 ? "passed" : "not_applicable") &&
    equalCanonicalJson(
      value.multi_output_check?.roles,
      arrayMap(value.outputs, (output) => output.role),
    )
  );
}

function hasCoherentManifest(value) {
  if (
    !arrayIncludes(["produced", "processed", "release_ready"], value.state) ||
    !Array.isArray(value.assets)
  ) {
    return false;
  }
  const paths = isolatedArray();
  for (let index = 0; index < value.assets.length; index += 1) {
    const asset = value.assets[index];
    if (
      asset === null ||
      typeof asset !== "object" ||
      Array.isArray(asset) ||
      asset.state !== value.state ||
      !Array.isArray(asset.outputs)
    ) {
      return false;
    }
    const expectedPresence = {
      produced: [false, false, false],
      processed: [true, true, false],
      release_ready: [true, true, true],
    }[value.state];
    const actualPresence = [
      asset.processing_recipe !== null,
      asset.processing_receipt !== null,
      asset.qa_report !== null,
    ];
    if (!equalCanonicalJson(actualPresence, expectedPresence)) {
      return false;
    }
    for (let outputIndex = 0; outputIndex < asset.outputs.length; outputIndex += 1) {
      appendArray(paths, asset.outputs[outputIndex]?.runtime_path);
      appendArray(paths, asset.outputs[outputIndex]?.locator);
    }
  }
  return hasPortableGenericAssetPathList(paths);
}

export function hasCoherentGenericAssetD2bContract(value, kind) {
  if (
    value === null ||
    typeof value !== "object" ||
    Array.isArray(value) ||
    typeof kind !== "string"
  ) {
    return false;
  }
  if (kind === "recipe") {
    return hasCoherentRecipe(value);
  }
  if (kind === "receipt") {
    return hasCoherentReceipt(value);
  }
  if (kind === "qa") {
    return hasCoherentQa(value);
  }
  return kind === "manifest" && hasCoherentManifest(value);
}

function containsInvalidUnicode(value) {
  for (let index = 0; index < value.length; index += 1) {
    const code = value.charCodeAt(index);
    if (code >= 0xd800 && code <= 0xdbff) {
      const following = value.charCodeAt(index + 1);
      if (index + 1 >= value.length || following < 0xdc00 || following > 0xdfff) {
        return true;
      }
      index += 1;
    } else if (code >= 0xdc00 && code <= 0xdfff) {
      return true;
    }
  }
  return false;
}

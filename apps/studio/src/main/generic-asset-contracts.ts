import { Script, createContext } from "node:vm";

import Ajv2020 from "ajv/dist/2020.js";
import {
  _,
  Name,
  strConcat,
} from "ajv/dist/compile/codegen/index.js";
import type { KeywordCxt } from "ajv/dist/compile/validate/index.js";
import standaloneCode from "ajv/dist/standalone/index.js";

import genericAssetInventorySchema from "../../../../schemas/generic-asset-inventory.schema.json";
import genericAssetLicenseRecordSchema from "../../../../schemas/generic-asset-license-record.schema.json";
import genericAssetManifestSchema from "../../../../schemas/generic-asset-manifest.schema.json";
import genericAssetProcessingReceiptSchema from "../../../../schemas/generic-asset-processing-receipt.schema.json";
import genericAssetProcessingRecipeSchema from "../../../../schemas/generic-asset-processing-recipe.schema.json";
import genericAssetProductionReceiptSchema from "../../../../schemas/generic-asset-production-receipt.schema.json";
import genericAssetProductionRequestSchema from "../../../../schemas/generic-asset-production-request.schema.json";
import genericAssetProvenanceRecordSchema from "../../../../schemas/generic-asset-provenance-record.schema.json";
import genericAssetQaReportSchema from "../../../../schemas/generic-asset-qa-report.schema.json";
import genericAssetSelectionSchema from "../../../../schemas/generic-asset-selection.schema.json";
import genericAssetSpecSchema from "../../../../schemas/generic-asset-spec.schema.json";
import genericAssetStyleSchema from "../../../../schemas/generic-asset-style.schema.json";
import genericAssetSubjectSchema from "../../../../schemas/generic-asset-subject.schema.json";
import genericAssetTargetSchema from "../../../../schemas/generic-asset-target.schema.json";
import {
  areCanonicalGenericAssetGlyphRanges,
  hasCanonicalGenericAssetContentHash,
  hasCoherentGenericAssetD2bContract,
  hasCoherentGenericAssetProductionRequest,
  hasExactGenericAssetReceiptLineageRoots,
  hasMatchingGenericAssetGlyphCount,
  hasMatchingGenericAssetTextSha256,
  hasPortableGenericAssetPathTree,
  hasDistinctGenericAssetContentHashes,
  isCanonicalGenericAssetObjectArray,
  isCanonicalGenericAssetStringArray,
  isPortableGenericAssetRuntimePath,
  preflightGenericAssetRuntimeText,
  isRuntimeSafeGenericAssetNotice,
  isSafeGenericAssetRuntimeText,
} from "../../scripts/generic-asset-validation.mjs";
import { snapshotStrictJsonObject } from "../../scripts/strict-json.mjs";
import type {
  WorldForgeAssetSpecificationV1,
  WorldForgeAssetProductionReceiptV1,
  WorldForgeAssetProductionRequestV1,
  WorldForgeAssetSubjectV1,
  WorldForgeDeterministicAssetInventoryV1,
  WorldForgeDeterministicAssetProcessingReceiptV1,
  WorldForgeDeterministicAssetProcessingRecipeV1,
  WorldForgeGenericAssetReleaseManifestV1,
  WorldForgeReviewedAssetSelectionV1,
  WorldForgeReviewedAssetStyleV1,
  WorldForgeReviewedAssetTargetV1,
  WorldForgeRetainedByteAssetQAReportV1,
  WorldForgeRuntimeSafeAssetLicenseRecordV1,
  WorldForgeSelectedAssetProvenanceRecordV1,
} from "../generated/world-forge-contracts";

export type GenericAssetContract =
  | WorldForgeAssetSubjectV1
  | WorldForgeReviewedAssetTargetV1
  | WorldForgeReviewedAssetStyleV1
  | WorldForgeDeterministicAssetInventoryV1
  | WorldForgeAssetSpecificationV1
  | WorldForgeAssetProductionRequestV1
  | WorldForgeAssetProductionReceiptV1
  | WorldForgeReviewedAssetSelectionV1
  | WorldForgeSelectedAssetProvenanceRecordV1
  | WorldForgeRuntimeSafeAssetLicenseRecordV1
  | WorldForgeDeterministicAssetProcessingRecipeV1
  | WorldForgeDeterministicAssetProcessingReceiptV1
  | WorldForgeRetainedByteAssetQAReportV1
  | WorldForgeGenericAssetReleaseManifestV1;

declare const validatedGenericAssetContractBrand: unique symbol;
export type ValidatedGenericAssetContract = Readonly<GenericAssetContract> & {
  readonly [validatedGenericAssetContractBrand]: true;
};

const schemas = [
  genericAssetSubjectSchema,
  genericAssetTargetSchema,
  genericAssetStyleSchema,
  genericAssetInventorySchema,
  genericAssetSpecSchema,
  genericAssetProductionRequestSchema,
  genericAssetProductionReceiptSchema,
  genericAssetSelectionSchema,
  genericAssetProvenanceRecordSchema,
  genericAssetLicenseRecordSchema,
  genericAssetProcessingRecipeSchema,
  genericAssetProcessingReceiptSchema,
  genericAssetQaReportSchema,
  genericAssetManifestSchema,
] as const;
const ajv = new Ajv2020({
  allErrors: true,
  code: { source: true },
  ownProperties: true,
  strict: true,
});
type SemanticHandler = (schema: unknown, value: unknown) => boolean;
type IsolatedSemanticHandler = (
  schema: unknown,
  privateValue: unknown,
  instancePath: unknown,
) => boolean;
type SemanticSchemaType = "boolean" | "object" | "string";
type SemanticValueType = "array" | "object" | "string";

const freezeObject = Object.freeze;
const getOwnPropertyDescriptor = Object.getOwnPropertyDescriptor;
const ownKeys = Reflect.ownKeys;
const setPrototypeOf = Reflect.setPrototypeOf;
const ajvInstancePath = new Name("instancePath");
const semanticHandlers = Object.create(null) as Record<
  string,
  SemanticHandler
>;
let semanticHandlerIndex = 0;

function addSemanticKeyword(
  keyword: string,
  schemaType: SemanticSchemaType,
  type: SemanticValueType,
  handler: SemanticHandler,
): void {
  const handlerIndex = semanticHandlerIndex;
  semanticHandlerIndex += 1;
  semanticHandlers[`handler${String(handlerIndex)}`] = handler;
  ajv.addKeyword({
    code(context: KeywordCxt) {
      const scopedHandler = context.gen.scopeValue("func", {
        code: _`globalThis.__worldForgeAssetSemantics.handler${handlerIndex}`,
        ref: handler,
      });
      context.pass(
        _`${scopedHandler}(${context.schemaCode}, ${context.data}, ${strConcat(ajvInstancePath, context.it.errorPath)})`,
      );
    },
    keyword,
    schemaType,
    type,
  });
}

addSemanticKeyword(
  "x-world-forge-canonical-glyph-ranges",
  "boolean",
  "array",
  (required, value) =>
    required !== true || areCanonicalGenericAssetGlyphRanges(value),
);
addSemanticKeyword(
  "x-world-forge-portable-runtime-path",
  "boolean",
  "string",
  (required, value) =>
    required !== true || isPortableGenericAssetRuntimePath(value),
);
addSemanticKeyword(
  "x-world-forge-distinct-content-hashes",
  "boolean",
  "object",
  (required, value) =>
    required !== true || hasDistinctGenericAssetContentHashes(value),
);
addSemanticKeyword(
  "x-world-forge-canonical-content-hash",
  "boolean",
  "object",
  (required, value) =>
    required !== true || hasCanonicalGenericAssetContentHash(value),
);
addSemanticKeyword(
  "x-world-forge-d2b-coherent",
  "string",
  "object",
  (kind, value) => hasCoherentGenericAssetD2bContract(value, kind),
);
addSemanticKeyword(
  "x-world-forge-production-request-coherent",
  "boolean",
  "object",
  (required, value) =>
    required !== true || hasCoherentGenericAssetProductionRequest(value),
);
addSemanticKeyword(
  "x-world-forge-receipt-lineage-roots",
  "boolean",
  "object",
  (required, value) =>
    required !== true || hasExactGenericAssetReceiptLineageRoots(value),
);
addSemanticKeyword(
  "x-world-forge-runtime-safe-notice",
  "boolean",
  "string",
  (required, value) =>
    required !== true || isRuntimeSafeGenericAssetNotice(value),
);
addSemanticKeyword(
  "x-world-forge-safe-runtime-text",
  "boolean",
  "string",
  (required, value) =>
    required !== true || isSafeGenericAssetRuntimeText(value),
);
addSemanticKeyword(
  "x-world-forge-sha256-text-match",
  "boolean",
  "object",
  (required, value) =>
    required !== true || hasMatchingGenericAssetTextSha256(value),
);
addSemanticKeyword(
  "x-world-forge-glyph-count-match",
  "boolean",
  "object",
  (required, value) =>
    required !== true || hasMatchingGenericAssetGlyphCount(value),
);
addSemanticKeyword(
  "x-world-forge-canonical-object-array",
  "object",
  "array",
  (policy, value) => isCanonicalGenericAssetObjectArray(value, policy),
);
addSemanticKeyword(
  "x-world-forge-canonical-string-array",
  "boolean",
  "array",
  (required, value) =>
    required !== true || isCanonicalGenericAssetStringArray(value),
);
addSemanticKeyword(
  "x-world-forge-portable-path-tree",
  "string",
  "array",
  (field, value) => hasPortableGenericAssetPathTree(value, field),
);

const validatorExports = Object.create(null) as Record<string, string>;
const validatorKeys = new Map<string, string>();
for (let schemaIndex = 0; schemaIndex < schemas.length; schemaIndex += 1) {
  const schema = schemas[schemaIndex];
  ajv.addSchema(schema);
  const exportName = `validator${String(schemaIndex)}`;
  validatorExports[exportName] = schema.$id;
  validatorKeys.set(schema.properties.format.const, exportName);
}

type IsolatedValidator = (
  validatorName: string,
  value: Record<string, unknown>,
) => boolean;

function createIsolatedValidator(
  validatorSource: string,
  handlers: Record<string, IsolatedSemanticHandler>,
): IsolatedValidator {
  const sandbox = Object.create(null) as {
    __validateGenericAssetContract?: IsolatedValidator;
    __worldForgeAssetSemantics: Record<string, IsolatedSemanticHandler>;
  };
  sandbox.__worldForgeAssetSemantics = handlers;
  const context = createContext(sandbox, {
    codeGeneration: {
      strings: false,
      wasm: false,
    },
    name: "world-forge-generic-asset-validation",
  });
  new Script(ISOLATED_VALIDATOR_BOOTSTRAP, {
    filename: "world-forge-generic-asset-bootstrap.cjs",
  }).runInContext(context);
  new Script(validatorSource, {
    filename: "world-forge-generic-asset-validators.cjs",
  }).runInContext(context);
  new Script(ISOLATED_VALIDATOR_WRAPPER, {
    filename: "world-forge-generic-asset-wrapper.cjs",
  }).runInContext(context);
  const validator = sandbox.__validateGenericAssetContract;
  if (typeof validator !== "function") {
    throw new Error("Failed to initialize isolated generic asset validators");
  }
  return validator;
}

const ISOLATED_VALIDATOR_BOOTSTRAP = String.raw`
"use strict";
const __equalJson = function equalJson(left, right) {
  if (left === right) {
    return true;
  }
  if (
    left === null ||
    right === null ||
    typeof left !== "object" ||
    typeof right !== "object" ||
    left.constructor !== right.constructor
  ) {
    return false;
  }
  if (Array.isArray(left)) {
    if (!Array.isArray(right) || left.length !== right.length) {
      return false;
    }
    for (let index = left.length - 1; index >= 0; index -= 1) {
      if (!equalJson(left[index], right[index])) {
        return false;
      }
    }
    return true;
  }
  const leftKeys = Object.keys(left);
  const rightKeys = Object.keys(right);
  if (leftKeys.length !== rightKeys.length) {
    return false;
  }
  for (let index = leftKeys.length - 1; index >= 0; index -= 1) {
    const key = leftKeys[index];
    if (
      !Object.prototype.hasOwnProperty.call(right, key) ||
      !equalJson(left[key], right[key])
    ) {
      return false;
    }
  }
  return true;
};
const __ucs2Length = (value) => {
  let length = 0;
  let position = 0;
  while (position < value.length) {
    length += 1;
    const high = value.charCodeAt(position);
    position += 1;
    if (high >= 0xd800 && high <= 0xdbff && position < value.length) {
      const low = value.charCodeAt(position);
      if ((low & 0xfc00) === 0xdc00) {
        position += 1;
      }
    }
  }
  return length;
};
const __runtimeModules = Object.freeze({
  "ajv/dist/runtime/equal": Object.freeze({ default: __equalJson }),
  "ajv/dist/runtime/ucs2length": Object.freeze({ default: __ucs2Length }),
});
Object.defineProperty(globalThis, "exports", {
  configurable: false,
  enumerable: false,
  value: Object.create(null),
  writable: false,
});
Object.defineProperty(globalThis, "require", {
  configurable: false,
  enumerable: false,
  value: (identifier) => {
    if (!Object.prototype.hasOwnProperty.call(__runtimeModules, identifier)) {
      throw new Error("Unsupported isolated validator runtime module");
    }
    return __runtimeModules[identifier];
  },
  writable: false,
});
const __privateObjectPrototype = Object.prototype;
const __privateArrayPrototype = Array.prototype;
Object.setPrototypeOf(__privateArrayPrototype, null);
Object.freeze(__privateObjectPrototype);
Object.freeze(__privateArrayPrototype);
Object.freeze(Function.prototype);
Object.freeze(RegExp.prototype);
Object.freeze(String.prototype);
`;

const ISOLATED_VALIDATOR_WRAPPER = String.raw`
"use strict";
const __parseJson = JSON.parse;
const __stringifyJson = JSON.stringify;
Object.defineProperty(globalThis, "__validateGenericAssetContract", {
  configurable: false,
  enumerable: false,
  value: (validatorName, snapshot) => {
    const validator = exports[validatorName];
    if (typeof validator !== "function") {
      return false;
    }
    const candidate = __parseJson(__stringifyJson(snapshot));
    return validator(candidate) === true;
  },
  writable: false,
});
`;

type HostSnapshotLookup =
  | { readonly found: false }
  | { readonly found: true; readonly value: unknown };

const MISSING_HOST_SNAPSHOT_VALUE: HostSnapshotLookup = Object.freeze({
  found: false,
});
let activeHostSnapshot: Record<string, unknown> | undefined;

function decodeJsonPointerSegment(
  pointer: string,
  start: number,
  end: number,
): string | null {
  let decoded = "";
  for (let index = start; index < end; index += 1) {
    const character = pointer[index];
    if (character !== "~") {
      decoded += character;
      continue;
    }
    index += 1;
    if (index >= end) {
      return null;
    }
    const escaped = pointer[index];
    if (escaped === "0") {
      decoded += "~";
    } else if (escaped === "1") {
      decoded += "/";
    } else {
      return null;
    }
  }
  return decoded;
}

function hostSnapshotValueAt(
  root: Record<string, unknown>,
  instancePath: unknown,
): HostSnapshotLookup {
  if (typeof instancePath !== "string") {
    return MISSING_HOST_SNAPSHOT_VALUE;
  }
  if (instancePath.length === 0) {
    return { found: true, value: root };
  }
  if (instancePath[0] !== "/") {
    return MISSING_HOST_SNAPSHOT_VALUE;
  }
  let current: unknown = root;
  let segmentStart = 1;
  for (let index = 1; index <= instancePath.length; index += 1) {
    if (index !== instancePath.length && instancePath[index] !== "/") {
      continue;
    }
    const key = decodeJsonPointerSegment(instancePath, segmentStart, index);
    if (key === null || current === null || typeof current !== "object") {
      return MISSING_HOST_SNAPSHOT_VALUE;
    }
    const descriptor = getOwnPropertyDescriptor(current, key);
    if (descriptor === undefined || !("value" in descriptor)) {
      return MISSING_HOST_SNAPSHOT_VALUE;
    }
    current = descriptor.value;
    segmentStart = index + 1;
  }
  return { found: true, value: current };
}

function bridgeSemanticHandlers(
  handlers: Record<string, SemanticHandler>,
): Record<string, IsolatedSemanticHandler> {
  const bridged = Object.create(null) as Record<
    string,
    IsolatedSemanticHandler
  >;
  const handlerKeys = ownKeys(handlers);
  for (let index = 0; index < handlerKeys.length; index += 1) {
    const key = handlerKeys[index];
    if (typeof key !== "string") {
      throw new Error("Semantic handler names must be strings");
    }
    const handler = handlers[key];
    bridged[key] = (schema, _privateValue, instancePath) => {
      const root = activeHostSnapshot;
      if (root === undefined) {
        return false;
      }
      const lookup = hostSnapshotValueAt(root, instancePath);
      return lookup.found && handler(schema, lookup.value);
    };
  }
  return bridged;
}

function freezeHostSnapshot<T extends Record<string, unknown>>(root: T): T {
  const pending: object[] = [root];
  setPrototypeOf(pending, null);
  while (pending.length > 0) {
    const index = pending.length - 1;
    const current = pending[index];
    pending.length = index;
    const keys = ownKeys(current);
    for (let keyIndex = 0; keyIndex < keys.length; keyIndex += 1) {
      const key = keys[keyIndex];
      const descriptor = getOwnPropertyDescriptor(current, key);
      const child: unknown =
        descriptor !== undefined && "value" in descriptor
          ? descriptor.value
          : undefined;
      if (
        child !== null &&
        typeof child === "object"
      ) {
        pending[pending.length] = child;
      }
    }
    freezeObject(current);
  }
  return root;
}

const isolatedValidator = createIsolatedValidator(
  standaloneCode(ajv, validatorExports),
  bridgeSemanticHandlers(semanticHandlers),
);

export function validateGenericAssetContract(
  value: unknown,
): ValidatedGenericAssetContract | null {
  if (value === null || typeof value !== "object") {
    return null;
  }
  let candidate: Record<string, unknown>;
  try {
    candidate = snapshotStrictJsonObject(value, {
      context: "generic asset contract",
    });
  } catch {
    return null;
  }
  if (!passesRawLicenseNoticePreflight(candidate)) {
    return null;
  }
  if (
    !Object.hasOwn(candidate, "format")
  ) {
    return null;
  }
  const format = (candidate as { format?: unknown }).format;
  if (typeof format !== "string") {
    return null;
  }
  const validatorName = validatorKeys.get(format);
  if (validatorName === undefined) {
    return null;
  }
  const previousHostSnapshot = activeHostSnapshot;
  activeHostSnapshot = candidate;
  try {
    if (!isolatedValidator(validatorName, candidate)) {
      return null;
    }
  } catch {
    return null;
  } finally {
    activeHostSnapshot = previousHostSnapshot;
  }
  return freezeHostSnapshot(candidate) as ValidatedGenericAssetContract;
}

function passesRawLicenseNoticePreflight(value: object): boolean {
  try {
    const format = Object.getOwnPropertyDescriptor(value, "format");
    if (format === undefined) {
      return true;
    }
    if (!("value" in format)) {
      return false;
    }
    if (format.value !== "world-forge.asset_license_record") {
      return true;
    }
    const notice = Object.getOwnPropertyDescriptor(value, "runtime_notice");
    if (
      notice === undefined ||
      !("value" in notice) ||
      notice.value === null ||
      typeof notice.value !== "object" ||
      Array.isArray(notice.value)
    ) {
      return false;
    }
    const text = Object.getOwnPropertyDescriptor(notice.value, "text");
    return (
      text !== undefined &&
      "value" in text &&
      preflightGenericAssetRuntimeText(text.value)
    );
  } catch {
    return false;
  }
}

import Ajv2020, { type ValidateFunction } from "ajv/dist/2020.js";

import gameRuntimeCompositionSchema from "../../../../schemas/game-runtime-composition.schema.json";
import gameRuntimeSnapshotSchema from "../../../../schemas/game-runtime-snapshot.schema.json";
import runtimeAdapterRegistrySchema from "../../../../schemas/generic-runtime-adapter-registry.schema.json";
import runtimeAdapterSchema from "../../../../schemas/generic-runtime-adapter.schema.json";
import runtimeEvidenceSchema from "../../../../schemas/generic-runtime-evidence.schema.json";
import runtimeSupportReportSchema from "../../../../schemas/generic-runtime-support-report.schema.json";
import {
  hasCanonicalGenericAssetContentHash,
} from "../../scripts/generic-asset-validation.mjs";
import {
  GENERIC_RUNTIME_EXECUTION_POLICY,
  hasCoherentGenericRuntimeContract,
} from "../../scripts/generic-runtime-validation.mjs";
import { snapshotStrictJsonObject } from "../../scripts/strict-json.mjs";

declare const validatedGenericRuntimeContractBrand: unique symbol;
export type ValidatedGenericRuntimeContract = Readonly<
  Record<string, unknown>
> & {
  readonly [validatedGenericRuntimeContractBrand]: true;
};

export interface GenericRuntimeSupportInspection {
  readonly adapter: "absent" | "declared" | "verified";
  readonly compatibilityStatus:
    | "partially_supported"
    | "supported"
    | "unsupported";
  readonly release: "blocked" | "ready";
  readonly reasonCodes: readonly string[];
  readonly supported: boolean;
}

export const GENERIC_RUNTIME_VALIDATOR_RUNTIME = Object.freeze({
  contract_formats: Object.freeze([
    "world-forge.game_runtime_composition",
    "world-forge.game_runtime_snapshot",
    "world-forge.runtime_adapter",
    "world-forge.runtime_adapter_registry",
    "world-forge.runtime_evidence",
    "world-forge.runtime_support_report",
  ]),
  execution_semantics: GENERIC_RUNTIME_EXECUTION_POLICY,
  format: "world-forge.studio_internal_generic_runtime_validator",
  format_version: 1,
});

const schemas = [
  runtimeAdapterSchema,
  runtimeAdapterRegistrySchema,
  gameRuntimeSnapshotSchema,
  gameRuntimeCompositionSchema,
  runtimeEvidenceSchema,
  runtimeSupportReportSchema,
] as const;
const ajv = new Ajv2020({
  allErrors: true,
  ownProperties: true,
  strict: true,
});

ajv.addKeyword({
  keyword: "x-world-forge-canonical-content-hash",
  schemaType: "boolean",
  type: "object",
  validate: (required: boolean, value: unknown) =>
    required !== true || hasCanonicalGenericAssetContentHash(value),
});
ajv.addKeyword({
  keyword: "x-world-forge-generic-runtime-coherent",
  schemaType: "string",
  type: "object",
  validate: (kind: string, value: unknown) =>
    hasCoherentGenericRuntimeContract(
      value,
      kind as Parameters<typeof hasCoherentGenericRuntimeContract>[1],
    ),
});

const validators = new Map<string, ValidateFunction>();
for (const schema of schemas) {
  const format = schema.properties.format.const;
  validators.set(format, ajv.compile(schema));
}

function deepFreeze<T extends object>(root: T): T {
  const pending: object[] = [root];
  while (pending.length > 0) {
    const current = pending.pop();
    if (current === undefined) {
      continue;
    }
    for (const key of Reflect.ownKeys(current)) {
      const descriptor = Object.getOwnPropertyDescriptor(current, key);
      const child: unknown =
        descriptor !== undefined && "value" in descriptor
          ? descriptor.value
          : undefined;
      if (
        child !== null &&
        typeof child === "object"
      ) {
        pending.push(child);
      }
    }
    Object.freeze(current);
  }
  return root;
}

export function validateGenericRuntimeContract(
  value: unknown,
): ValidatedGenericRuntimeContract {
  let candidate: Record<string, unknown>;
  try {
    candidate = snapshotStrictJsonObject(value, {
      context: "generic runtime contract",
    });
  } catch (error) {
    throw new Error("generic runtime contract is not strict JSON", {
      cause: error,
    });
  }
  const format = candidate.format;
  const validator = typeof format === "string" ? validators.get(format) : undefined;
  let validationCandidate: Record<string, unknown>;
  try {
    validationCandidate = JSON.parse(
      JSON.stringify(candidate),
    ) as Record<string, unknown>;
  } catch (error) {
    throw new Error("generic runtime contract could not be isolated", {
      cause: error,
    });
  }
  let accepted: boolean;
  try {
    accepted =
      validator !== undefined && validator(validationCandidate) === true;
  } catch {
    accepted = false;
  }
  if (!accepted) {
    throw new Error("generic runtime contract failed schema or coherence validation");
  }
  return deepFreeze(candidate) as ValidatedGenericRuntimeContract;
}

export function inspectGenericRuntimeSupport(
  value: unknown,
): GenericRuntimeSupportInspection {
  const report = validateGenericRuntimeContract(value);
  if (report.format !== "world-forge.runtime_support_report") {
    throw new Error("generic runtime support inspection requires a support report");
  }
  const dimensions = report.dimensions;
  if (
    dimensions === null ||
    typeof dimensions !== "object" ||
    Array.isArray(dimensions)
  ) {
    throw new Error("generic runtime support report dimensions are invalid");
  }
  const reasonCodes = report.reason_codes;
  if (!Array.isArray(reasonCodes)) {
    throw new Error("generic runtime support report reason codes are invalid");
  }
  const adapter = Reflect.get(dimensions, "adapter") as unknown;
  const release = Reflect.get(dimensions, "release") as unknown;
  const compatibilityStatus = report.compatibility_status;
  const supported = report.supported;
  if (
    !["absent", "declared", "verified"].includes(String(adapter)) ||
    !["blocked", "ready"].includes(String(release)) ||
    !["partially_supported", "supported", "unsupported"].includes(
      String(compatibilityStatus),
    ) ||
    typeof supported !== "boolean"
  ) {
    throw new Error("generic runtime support report summary is invalid");
  }
  const reasonCodeSnapshot: string[] = [];
  for (let index = 0; index < reasonCodes.length; index += 1) {
    const reason = reasonCodes[index] as unknown;
    if (typeof reason !== "string") {
      throw new Error("generic runtime support report reason codes are invalid");
    }
    reasonCodeSnapshot.push(reason);
  }
  return Object.freeze({
    adapter,
    compatibilityStatus,
    release,
    reasonCodes: Object.freeze(reasonCodeSnapshot),
    supported,
  }) as GenericRuntimeSupportInspection;
}

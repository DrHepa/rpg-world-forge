import Ajv2020, { type ValidateFunction } from "ajv/dist/2020.js";

import gameMaterializationBundleSchema from "../../../../schemas/game-materialization-bundle.schema.json";
import runtimeImplementationSchema from "../../../../schemas/runtime-implementation.schema.json";
import runtimePlatformLockSchema from "../../../../schemas/runtime-platform-lock.schema.json";
import standaloneGameLockSchema from "../../../../schemas/standalone-game-lock.schema.json";
import standaloneGameSchema from "../../../../schemas/standalone-game.schema.json";
import standalonePlatformSchema from "../../../../schemas/standalone-platform.schema.json";
import {
  hasCanonicalGenericAssetContentHash,
  isCanonicalGenericAssetObjectArray,
} from "../../scripts/generic-asset-validation.mjs";
import {
  hasAuditedRuntimePlatformLock,
  hasCoherentGameMaterializationBundle,
  hasCoherentRuntimeImplementation,
  hasCoherentStandaloneGame,
  hasCoherentStandaloneGameLock,
  hasCoherentStandalonePlatform,
} from "../../scripts/materialization-contract-validation.mjs";
import { snapshotStrictJsonObject } from "../../scripts/strict-json.mjs";

declare const validatedGenericMaterializationContractBrand: unique symbol;
export type ValidatedGenericMaterializationContract = Readonly<
  Record<string, unknown>
> & {
  readonly [validatedGenericMaterializationContractBrand]: true;
};

export const GENERIC_MATERIALIZATION_VALIDATOR_RUNTIME = Object.freeze({
  contract_formats: Object.freeze([
    "world-forge.game_materialization_bundle",
    "world-forge.runtime_implementation",
    "world-forge.runtime_platform_lock",
    "world-forge.standalone_game",
    "world-forge.standalone_game_lock",
    "world-forge.standalone_platform",
  ]),
  format: "world-forge.studio_internal_materialization_contract_validator",
  format_version: 2,
  verification_scope: "structural_transfer_validation",
});

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
  keyword: "x-world-forge-canonical-object-array",
  schemaType: "object",
  type: "array",
  validate: (policy: unknown, value: unknown) =>
    isCanonicalGenericAssetObjectArray(value, policy),
});
ajv.addKeyword({
  keyword: "x-world-forge-runtime-platform-lock-audited",
  schemaType: "boolean",
  type: "object",
  validate: (required: boolean, value: unknown) =>
    required !== true || hasAuditedRuntimePlatformLock(value),
});
ajv.addKeyword({
  keyword: "x-world-forge-runtime-implementation-coherent",
  schemaType: "boolean",
  type: "object",
  validate: (required: boolean, value: unknown) =>
    required !== true || hasCoherentRuntimeImplementation(value),
});
ajv.addKeyword({
  keyword: "x-world-forge-game-materialization-bundle-coherent",
  schemaType: "boolean",
  type: "object",
  validate: (required: boolean, value: unknown) =>
    required !== true || hasCoherentGameMaterializationBundle(value),
});
ajv.addKeyword({
  keyword: "x-world-forge-standalone-game-coherent",
  schemaType: "boolean",
  type: "object",
  validate: (required: boolean, value: unknown) =>
    required !== true || hasCoherentStandaloneGame(value),
});
ajv.addKeyword({
  keyword: "x-world-forge-standalone-game-lock-coherent",
  schemaType: "boolean",
  type: "object",
  validate: (required: boolean, value: unknown) =>
    required !== true || hasCoherentStandaloneGameLock(value),
});
ajv.addKeyword({
  keyword: "x-world-forge-standalone-platform-coherent",
  schemaType: "boolean",
  type: "object",
  validate: (required: boolean, value: unknown) =>
    required !== true || hasCoherentStandalonePlatform(value),
});

const validators = new Map<string, ValidateFunction>();
for (const schema of [
  gameMaterializationBundleSchema,
  runtimeImplementationSchema,
  runtimePlatformLockSchema,
  standaloneGameSchema,
  standaloneGameLockSchema,
  standalonePlatformSchema,
] as const) {
  validators.set(schema.properties.format.const, ajv.compile(schema));
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
      if (child !== null && typeof child === "object") {
        pending.push(child);
      }
    }
    Object.freeze(current);
  }
  return root;
}

export function validateGenericMaterializationContract(
  value: unknown,
): ValidatedGenericMaterializationContract {
  let candidate: Record<string, unknown>;
  try {
    candidate = snapshotStrictJsonObject(value, {
      context: "generic materialization contract",
    });
  } catch (error) {
    throw new Error("generic materialization contract is not strict JSON", {
      cause: error,
    });
  }
  const validator =
    typeof candidate.format === "string"
      ? validators.get(candidate.format)
      : undefined;
  let isolated: Record<string, unknown>;
  try {
    isolated = JSON.parse(JSON.stringify(candidate)) as Record<
      string,
      unknown
    >;
  } catch (error) {
    throw new Error(
      "generic materialization contract could not be isolated",
      { cause: error },
    );
  }
  let accepted: boolean;
  try {
    accepted = validator !== undefined && validator(isolated) === true;
  } catch {
    throw new Error(
      "generic materialization contract failed schema or coherence validation",
    );
  }
  if (!accepted) {
    throw new Error(
      "generic materialization contract failed schema or coherence validation",
    );
  }
  return deepFreeze(candidate) as ValidatedGenericMaterializationContract;
}

export function inspectRuntimePlatformLock(
  value: unknown,
): Readonly<{
  abi: string;
  os: string;
  pythonMinor: string;
  status: "audited";
}> {
  const lock = validateGenericMaterializationContract(value);
  if (lock.format !== "world-forge.runtime_platform_lock") {
    throw new Error("runtime platform lock inspection requires a lock");
  }
  const platform = lock.platform as Readonly<Record<string, unknown>>;
  const python = lock.python as Readonly<Record<string, unknown>>;
  return Object.freeze({
    abi: String(python.abi),
    os: String(platform.os),
    pythonMinor: String(python.minor),
    status: "audited",
  });
}

export function inspectRuntimeImplementation(
  value: unknown,
): Readonly<{
  adapterId: string;
  materializationReady: false;
  platformLockCount: number;
  status: "declared";
}> {
  const implementation = validateGenericMaterializationContract(value);
  if (implementation.format !== "world-forge.runtime_implementation") {
    throw new Error(
      "runtime implementation inspection requires an implementation",
    );
  }
  const adapter = implementation.adapter as Readonly<
    Record<string, unknown>
  >;
  const locks = implementation.platform_locks as readonly unknown[];
  return Object.freeze({
    adapterId: String(adapter.adapter_id),
    materializationReady: false,
    platformLockCount: locks.length,
    status: "declared",
  });
}

import Ajv2020 from "ajv/dist/2020.js";

import gamePackageSchema from "../../../../schemas/game-package.schema.json";
import {
  hasCanonicalGenericAssetContentHash,
  isCanonicalGenericAssetObjectArray,
} from "../../scripts/generic-asset-validation.mjs";
import {
  hasCoherentGamePackage,
} from "../../scripts/game-package-validation.mjs";
import { snapshotStrictJsonObject } from "../../scripts/strict-json.mjs";

declare const validatedGenericGamePackageBrand: unique symbol;
export type ValidatedGenericGamePackage = Readonly<
  Record<string, unknown>
> & {
  readonly [validatedGenericGamePackageBrand]: true;
};

export const GENERIC_GAME_PACKAGE_INSPECTOR_RUNTIME = Object.freeze({
  contract_format: "world-forge.game_package",
  format: "world-forge.studio_internal_game_package_inspector",
  format_version: 1,
  semantic_boundary: "packaged_python_required",
  verification_scope: "package_manifest_structural_validation",
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
  keyword: "x-world-forge-game-package-coherent",
  schemaType: "boolean",
  type: "object",
  validate: (required: boolean, value: unknown) =>
    required !== true || hasCoherentGamePackage(value),
});
const validatePackage = ajv.compile(gamePackageSchema);

function deepFreeze<T extends object>(root: T): T {
  const pending: object[] = [root];
  while (pending.length > 0) {
    const current = pending.pop();
    if (current === undefined || Object.isFrozen(current)) {
      continue;
    }
    for (const child of Object.values(
      current as Record<string, unknown>,
    )) {
      if (child !== null && typeof child === "object") {
        pending.push(child);
      }
    }
    Object.freeze(current);
  }
  return root;
}

export function validateGenericGamePackage(
  value: unknown,
): ValidatedGenericGamePackage | null {
  let owned: Record<string, unknown>;
  let schemaInput: Record<string, unknown>;
  try {
    owned = snapshotStrictJsonObject(value, {
      context: "generic game package manifest",
      maxDepth: 64,
      maxNodes: 100_000,
    });
    schemaInput = JSON.parse(JSON.stringify(owned)) as Record<
      string,
      unknown
    >;
  } catch {
    return null;
  }
  return validatePackage(schemaInput)
    ? (deepFreeze(owned) as ValidatedGenericGamePackage)
    : null;
}

export function inspectGenericGamePackage(
  value: ValidatedGenericGamePackage | null,
) {
  if (value === null) {
    return null;
  }
  const standalone = value.standalone_game as Readonly<
    Record<string, unknown>
  >;
  const lock = value.payload_lock as Readonly<Record<string, unknown>>;
  const files = value.files as readonly unknown[];
  return Object.freeze({
    content_hash: value.content_hash,
    file_count: files.length,
    package_id: value.package_id,
    payload_lock_hash: lock.content_hash,
    semantic_verification: "required_python" as const,
    standalone_game_hash: standalone.content_hash,
    status: "structurally_valid" as const,
  });
}

export function canonicalMaterializationDerivedId(
  value: unknown,
  idField:
    | "implementation_id"
    | "lock_id"
    | "materialization_bundle_id",
): string | null;

export function hasAuditedRuntimePlatformLock(value: unknown): boolean;
export function hasCoherentRuntimeImplementation(value: unknown): boolean;
export function hasCoherentGameMaterializationBundle(value: unknown): boolean;
export function hasCoherentStandaloneGame(value: unknown): boolean;
export function hasCoherentStandaloneGameLock(value: unknown): boolean;
export function hasCoherentStandalonePlatform(value: unknown): boolean;

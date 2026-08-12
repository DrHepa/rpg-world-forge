export const GENERIC_RUNTIME_EXECUTION_POLICY: Readonly<{
  content_hash: string;
  version: 1;
}>;

export function canonicalGenericRuntimeDerivedId(
  value: unknown,
  kind:
    | "game-runtime-composition"
    | "game-runtime-snapshot"
    | "generic-runtime-adapter-registry"
    | "generic-runtime-evidence"
    | "generic-runtime-support-report"
    | "runtime-support-authority",
): string | null;

export function hasCoherentGenericRuntimeContract(
  value: unknown,
  kind:
    | "game-runtime-composition"
    | "game-runtime-snapshot"
    | "generic-runtime-adapter"
    | "generic-runtime-adapter-registry"
    | "generic-runtime-evidence"
    | "generic-runtime-support-report"
    | "runtime-support-authority",
): boolean;

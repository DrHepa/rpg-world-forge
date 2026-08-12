export const MAX_STRICT_JSON_BYTES: number;
export const MAX_STRICT_JSON_DEPTH: number;
export const MAX_STRICT_JSON_NODES: number;
export const MAX_STRICT_JSON_KEYS: number;
export const MAX_STRICT_JSON_STRING_CODE_UNITS: number;

export type StrictJsonObject = Record<string, unknown>;

export interface StrictJsonDecodeOptions {
  context?: string;
  maxBytes?: number;
  maxDepth?: number;
}

export interface StrictJsonSnapshotOptions {
  context?: string;
  maxDepth?: number;
  maxNodes?: number;
  maxKeys?: number;
  maxStringCodeUnits?: number;
}

export function decodeStrictJsonObject(
  source: string | Uint8Array,
  options?: StrictJsonDecodeOptions,
): StrictJsonObject;

export function snapshotStrictJsonObject(
  value: unknown,
  options?: StrictJsonSnapshotOptions,
): StrictJsonObject;

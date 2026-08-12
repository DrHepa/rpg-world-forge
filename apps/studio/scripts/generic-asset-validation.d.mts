export const GENERIC_ASSET_ID_PATTERN: string;
export const GENERIC_ASSET_GLYPH_RANGE_PATTERN: string;
export const GENERIC_ASSET_RUNTIME_STRING_PATTERN: string;

export function isGenericAssetIdentifier(value: unknown): value is string;
export function areCanonicalGenericAssetGlyphRanges(value: unknown): value is string[];
export function isPortableGenericAssetRuntimePath(value: unknown): value is string;
export function hasDistinctGenericAssetContentHashes(value: unknown): boolean;
export function isRuntimeSafeGenericAssetNotice(value: unknown): value is string;
export function preflightGenericAssetRuntimeText(value: unknown): value is string;
export function isSafeGenericAssetRuntimeText(value: unknown): value is string;
export function hasMatchingGenericAssetTextSha256(value: unknown): boolean;
export function canonicalGenericAssetContentHash(value: unknown): string | null;
export function hasCanonicalGenericAssetContentHash(value: unknown): boolean;
export function hasCoherentGenericAssetProductionRequest(value: unknown): boolean;
export function hasCoherentGenericAssetD2bContract(
  value: unknown,
  kind: unknown,
): boolean;
export function hasExactGenericAssetReceiptLineageRoots(value: unknown): boolean;
export function hasMatchingGenericAssetGlyphCount(value: unknown): boolean;
export function isCanonicalGenericAssetObjectArray(
  value: unknown,
  policy: unknown,
): boolean;
export function isCanonicalGenericAssetStringArray(value: unknown): value is string[];
export function hasPortableGenericAssetPathTree(value: unknown, field: unknown): boolean;

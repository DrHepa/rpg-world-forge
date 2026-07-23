import type {
  StudioAssetCatalogEntry,
  StudioAssetInspection,
} from "../shared/studio-api";

export const ASSET_PREVIEW_CHUNK_BYTES = 65_536;
export const ASSET_PREVIEW_MAX_BYTES = 64 * 1_024 * 1_024;
export const ASSET_PREVIEW_MAX_CHUNKS = 1_024;

const SERVICE_ASSET_PREVIEW_MAX_BYTES = 512 * 1_024 * 1_024;
const HANDLE_PATTERN = /^[A-Za-z0-9_-]{43}$/u;
const SHA256_PATTERN = /^[0-9a-f]{64}$/u;
const ENTRY_ID_PATTERN = /^asset_[0-9a-f]{64}$/u;
const PREVIEW_CATEGORIES = new Set<StudioAssetCatalogEntry["category"]>([
  "production_output",
  "processing_output",
  "runtime_output",
]);

export type AssetBinaryPreviewKind = "png" | "wav";
export type AssetBinaryPreviewInspection = Extract<
  StudioAssetInspection,
  { kind: AssetBinaryPreviewKind }
>;
export type AssetBinaryPreviewLifecycle =
  | "idle"
  | "opening"
  | "reading"
  | "closing"
  | "ready"
  | "error";

export interface AssetBinaryPreviewContext {
  active: boolean;
  catalogCurrent: boolean;
  workspaceId: string | null;
  generation: number;
  manifestRevision: string | null;
}

export interface AssetBinaryPreviewIdentity {
  workspaceId: string;
  generation: number;
  manifestRevision: string;
  entryId: string;
  kind: AssetBinaryPreviewKind;
  mediaType: "image/png" | "audio/wav";
  category: StudioAssetCatalogEntry["category"];
  sha256: string;
}

export interface DecodedAssetPreviewOpen {
  handle: string;
  byteLength: number;
  sha256: string;
  chunkBytes: typeof ASSET_PREVIEW_CHUNK_BYTES;
}

export interface AssetPreviewChunkExpectation {
  handle: string;
  sequence: number;
  cumulativeBytes: number;
  declaredBytes: number;
  declaredSha256: string;
  seenViews: WeakSet<object>;
  seenBuffers: WeakSet<object>;
}

export interface DecodedAssetPreviewChunk {
  bytes: Uint8Array<ArrayBuffer>;
  cumulativeBytes: number;
  cumulativeSha256: string;
  eof: boolean;
}

export type AssetPreviewDecodeResult<T> =
  | { ok: true; value: T }
  | { ok: false; handle: string | null };

export function assetBinaryPreviewIdentity(
  context: AssetBinaryPreviewContext | undefined,
  entry: StudioAssetCatalogEntry | null,
  inspection: StudioAssetInspection | null,
): AssetBinaryPreviewIdentity | null {
  if (
    !context?.active ||
    !context.catalogCurrent ||
    !context.workspaceId ||
    !Number.isSafeInteger(context.generation) ||
    context.generation < 0 ||
    !context.manifestRevision ||
    !SHA256_PATTERN.test(context.manifestRevision) ||
    !entry ||
    !ENTRY_ID_PATTERN.test(entry.entry_id) ||
    !SHA256_PATTERN.test(entry.sha256) ||
    !PREVIEW_CATEGORIES.has(entry.category) ||
    !inspection ||
    (inspection.kind !== "png" && inspection.kind !== "wav")
  ) {
    return null;
  }
  const mediaType = inspection.kind === "png" ? "image/png" : "audio/wav";
  if (entry.media_type !== mediaType) return null;
  return Object.freeze({
    workspaceId: context.workspaceId,
    generation: context.generation,
    manifestRevision: context.manifestRevision,
    entryId: entry.entry_id,
    kind: inspection.kind,
    mediaType,
    category: entry.category,
    sha256: entry.sha256,
  });
}

export function assetBinaryPreviewIdentityKey(
  identity: AssetBinaryPreviewIdentity | null,
): string {
  if (!identity) return "idle";
  return JSON.stringify([
    identity.workspaceId,
    identity.generation,
    identity.manifestRevision,
    identity.entryId,
    identity.kind,
    identity.category,
    identity.mediaType,
    identity.sha256,
  ]);
}

export function decodeAssetPreviewOpen(
  rawReply: unknown,
  identity: AssetBinaryPreviewIdentity,
): AssetPreviewDecodeResult<DecodedAssetPreviewOpen> {
  const envelope = responseEnvelope(rawReply, "asset.preview.open");
  const result = asRecord(envelope?.result);
  const handle = validHandle(result?.handle) ? result.handle : null;
  if (
    !result ||
    !handle ||
    result.manifest_revision !== identity.manifestRevision ||
    result.entry_id !== identity.entryId ||
    result.media_type !== identity.mediaType ||
    !safeIntegerBetween(result.byte_length, 1, SERVICE_ASSET_PREVIEW_MAX_BYTES) ||
    result.sha256 !== identity.sha256 ||
    !SHA256_PATTERN.test(result.sha256) ||
    result.chunk_bytes !== ASSET_PREVIEW_CHUNK_BYTES
  ) {
    return { ok: false, handle };
  }
  return {
    ok: true,
    value: {
      handle,
      byteLength: result.byte_length,
      sha256: result.sha256,
      chunkBytes: ASSET_PREVIEW_CHUNK_BYTES,
    },
  };
}

export function decodeAssetPreviewChunk(
  rawReply: unknown,
  expected: AssetPreviewChunkExpectation,
): AssetPreviewDecodeResult<DecodedAssetPreviewChunk> {
  const envelope = responseEnvelope(rawReply, "asset.preview.read");
  const result = asRecord(envelope?.result);
  if (!result || result.handle !== expected.handle) {
    return { ok: false, handle: expected.handle };
  }
  const bytes = result.bytes;
  const remaining = expected.declaredBytes - expected.cumulativeBytes;
  const expectedByteLength = Math.min(ASSET_PREVIEW_CHUNK_BYTES, remaining);
  if (
    !(bytes instanceof Uint8Array) ||
    Object.getPrototypeOf(bytes) !== Uint8Array.prototype ||
    !(bytes.buffer instanceof ArrayBuffer) ||
    bytes.byteOffset !== 0 ||
    bytes.byteLength !== bytes.buffer.byteLength
  ) {
    return { ok: false, handle: expected.handle };
  }
  if (
    result.sequence !== expected.sequence ||
    !Number.isSafeInteger(result.sequence) ||
    result.sequence < 0 ||
    result.sequence >= ASSET_PREVIEW_MAX_CHUNKS ||
    expected.seenViews.has(bytes) ||
    expected.seenBuffers.has(bytes.buffer) ||
    expectedByteLength <= 0 ||
    result.byte_length !== expectedByteLength ||
    bytes.byteLength !== expectedByteLength ||
    !Number.isSafeInteger(result.cumulative_bytes) ||
    result.cumulative_bytes !== expected.cumulativeBytes + expectedByteLength ||
    result.cumulative_bytes > expected.declaredBytes ||
    result.cumulative_bytes > ASSET_PREVIEW_MAX_BYTES ||
    typeof result.eof !== "boolean" ||
    result.eof !== (result.cumulative_bytes === expected.declaredBytes) ||
    typeof result.cumulative_sha256 !== "string" ||
    !SHA256_PATTERN.test(result.cumulative_sha256) ||
    (result.eof && result.cumulative_sha256 !== expected.declaredSha256)
  ) {
    return { ok: false, handle: expected.handle };
  }
  expected.seenViews.add(bytes);
  expected.seenBuffers.add(bytes.buffer);
  const ownedBytes: Uint8Array<ArrayBuffer> = new Uint8Array(bytes);
  return {
    ok: true,
    value: {
      bytes: ownedBytes,
      cumulativeBytes: result.cumulative_bytes,
      cumulativeSha256: result.cumulative_sha256,
      eof: result.eof,
    },
  };
}

export function decodeAssetPreviewClose(
  rawReply: unknown,
  handle: string,
): boolean {
  const envelope = responseEnvelope(rawReply, "asset.preview.close");
  const result = asRecord(envelope?.result);
  return result?.handle === handle && result.closed === true;
}

export function previewHandleFromOpenReply(rawReply: unknown): string | null {
  const client = asRecord(rawReply);
  const envelope = client?.ok === true ? asRecord(client.value) : null;
  const result = asRecord(envelope?.result);
  return validHandle(result?.handle) ? result.handle : null;
}

function responseEnvelope(rawReply: unknown, method: string): Record<string, unknown> | null {
  const client = asRecord(rawReply);
  if (!client || client.ok !== true) return null;
  const envelope = asRecord(client.value);
  if (
    !envelope ||
    envelope.protocol !== "rpg-world-forge.studio_protocol" ||
    envelope.protocol_version !== 1 ||
    envelope.kind !== "response" ||
    envelope.method !== method
  ) {
    return null;
  }
  return envelope;
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function validHandle(value: unknown): value is string {
  return typeof value === "string" && HANDLE_PATTERN.test(value);
}

function safeIntegerBetween(value: unknown, minimum: number, maximum: number): value is number {
  return (
    typeof value === "number" &&
    Number.isSafeInteger(value) &&
    value >= minimum &&
    value <= maximum
  );
}

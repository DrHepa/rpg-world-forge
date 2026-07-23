import type {
  StudioAssetCatalogEntry,
  StudioAssetCatalogInspectResponse,
  StudioAssetCatalogListResponse,
  StudioAssetInspection,
  StudioAssetCatalogPage,
} from "../shared/studio-api";
import { boundedMessage } from "./authoring-state";

const MAX_PAGE_ENTRIES = 64;
const MAX_INSPECTION_TEXT_BYTES = 262_144;
const MAX_JSON_NODES = 2_000;
const MAX_JSON_DEPTH = 12;
const SHA256_PATTERN = /^[0-9a-f]{64}$/u;
const ASSET_ENTRY_ID_PATTERN = /^asset_[0-9a-f]{64}$/u;

export type AssetCatalogCategory = StudioAssetCatalogEntry["category"];
export type AssetInspectionKind = StudioAssetInspection["kind"];
export type AssetCatalogConsistency = "unbound" | "current" | "stale" | "conflict";
export type AssetCatalogListMode = "initial" | "refresh" | "next" | "previous";

/** Maximum retained back-navigation offsets, independent of total catalog size. */
export const MAX_VISITED_CATALOG_OFFSETS = 64;

export const ASSET_CATALOG_CATEGORY_LABELS = {
  manifest: "Manifest",
  target: "Target",
  visual_bible: "Visual bible",
  audio_bible: "Audio bible",
  inventory: "Inventory",
  specification: "Specification",
  production_receipt: "Production receipt",
  production_request: "Production request",
  production_output: "Production output",
  processing_receipt: "Processing receipt",
  processing_recipe: "Processing recipe",
  processing_output: "Processing output",
  license: "License",
  qa: "QA",
  runtime_output: "Runtime output",
} satisfies Record<AssetCatalogCategory, string>;

export const ASSET_INSPECTION_KIND_LABELS = {
  json: "JSON",
  glsl: "GLSL",
  png: "PNG",
  wav: "WAV",
  font: "Font",
  glb: "GLB",
  unavailable: "Unavailable",
} satisfies Record<AssetInspectionKind, string>;

export interface AssetCatalogListIntent {
  readonly workspaceId: string;
  readonly generation: number;
  readonly token: number;
  readonly mode: AssetCatalogListMode;
  readonly offset: number;
  readonly expectedManifestRevision: string | null;
  readonly page: Readonly<StudioAssetCatalogPage> | undefined;
  readonly visitedOffsetsAfterSuccess: readonly number[];
}

export interface AssetCatalogInspectIntent {
  readonly workspaceId: string;
  readonly generation: number;
  readonly token: number;
  readonly manifestRevision: string;
  readonly entryId: string;
}

export interface AssetCatalogState {
  workspaceId: string | null;
  generation: number;
  listToken: number;
  inspectToken: number;
  listRequest: AssetCatalogListIntent | null;
  inspectRequest: AssetCatalogInspectIntent | null;
  consistency: AssetCatalogConsistency;
  status: string | null;
  error: string | null;
  staleMessage: string | null;
  manifestRevision: string | null;
  currentOffset: number;
  visitedOffsets: number[];
  entries: StudioAssetCatalogEntry[];
  nextOffset: number | null;
  selectedCategory: AssetCatalogCategory | null;
  selectedEntry: StudioAssetCatalogEntry | null;
  inspection: StudioAssetInspection | null;
}

export interface AssetCatalogTransition<TIntent> {
  state: AssetCatalogState;
  intent: TIntent;
}

export type AssetCatalogDecoded<T> =
  | { ok: true; value: T }
  | {
      ok: false;
      kind: "client-error" | "protocol-error" | "conflict" | "invalid-response";
      message: string;
    };

export function createInitialAssetCatalogState(): AssetCatalogState {
  return {
    workspaceId: null,
    generation: 0,
    listToken: 0,
    inspectToken: 0,
    listRequest: null,
    inspectRequest: null,
    consistency: "unbound",
    status: null,
    error: null,
    staleMessage: null,
    manifestRevision: null,
    currentOffset: 0,
    visitedOffsets: [],
    entries: [],
    nextOffset: null,
    selectedCategory: null,
    selectedEntry: null,
    inspection: null,
  };
}

export function bindAssetCatalogWorkspace(
  state: AssetCatalogState,
  workspaceId: string,
  generation: number,
): AssetCatalogState {
  if (!workspaceId || !Number.isSafeInteger(generation) || generation < 0) {
    throw new TypeError("Asset catalog workspace context is invalid");
  }
  return {
    ...createInitialAssetCatalogState(),
    workspaceId,
    generation,
    listToken: state.listToken,
    inspectToken: state.inspectToken,
  };
}

export function beginAssetCatalogList(
  state: AssetCatalogState,
  mode: AssetCatalogListMode,
): AssetCatalogTransition<AssetCatalogListIntent> | null {
  if (!state.workspaceId) return null;

  const manifestRevision = state.manifestRevision;
  let offset = 0;
  let expectedManifestRevision: string | null = null;
  let visitedOffsetsAfterSuccess: number[] = [];

  if (mode === "next") {
    if (
      state.nextOffset === null ||
      !manifestRevision ||
      state.consistency !== "current"
    ) {
      return null;
    }
    offset = state.nextOffset;
    expectedManifestRevision = manifestRevision;
    visitedOffsetsAfterSuccess = appendVisitedOffset(
      state.visitedOffsets,
      state.currentOffset,
    );
  } else if (mode === "previous") {
    if (
      state.visitedOffsets.length === 0 ||
      !manifestRevision ||
      state.consistency !== "current"
    ) {
      return null;
    }
    offset = state.visitedOffsets[state.visitedOffsets.length - 1] ?? 0;
    expectedManifestRevision = manifestRevision;
    visitedOffsetsAfterSuccess = state.visitedOffsets.slice(
      Math.max(0, state.visitedOffsets.length - MAX_VISITED_CATALOG_OFFSETS),
      -1,
    );
  }

  const token = nextRequestToken(state.listToken);
  const intentValues: AssetCatalogListIntent = {
    workspaceId: state.workspaceId,
    generation: state.generation,
    token,
    mode,
    offset,
    expectedManifestRevision,
    page:
      expectedManifestRevision === null
        ? undefined
        : { offset, manifestRevision: expectedManifestRevision },
    visitedOffsetsAfterSuccess,
  };
  const stateIntent = immutableListIntent(intentValues);
  const callerIntent = immutableListIntent(intentValues);
  const cleared =
    mode === "initial" || mode === "refresh" ? clearCatalogPage(state) : state;

  return {
    intent: callerIntent,
    state: {
      ...cleared,
      listToken: token,
      listRequest: stateIntent,
      inspectRequest: null,
      status: mode === "refresh" ? "Refreshing asset catalog…" : "Loading asset catalog…",
      error: null,
      staleMessage: null,
      consistency:
        mode === "initial" || mode === "refresh" ? "unbound" : state.consistency,
    },
  };
}

export function receiveAssetCatalogList(
  state: AssetCatalogState,
  intent: AssetCatalogListIntent,
  rawReply: unknown,
): AssetCatalogState {
  const request = matchingListRequest(state, intent);
  if (!request) return state;

  const decoded = decodeAssetCatalogListReply(rawReply);
  if (!decoded.ok) {
    if (decoded.kind === "conflict") {
      return staleCatalog(state, "conflict", decoded.message);
    }
    return {
      ...state,
      listRequest: null,
      status: null,
      error: boundedMessage(decoded.message),
    };
  }

  const page = decoded.value;
  if (
    page.offset !== request.offset ||
    (request.expectedManifestRevision !== null &&
      page.manifest_revision !== request.expectedManifestRevision)
  ) {
    return staleCatalog(
      state,
      "stale",
      "The asset catalog changed while paging. Refresh it before continuing.",
    );
  }

  return {
    ...state,
    listRequest: null,
    inspectRequest: null,
    consistency: "current",
    status: "Asset catalog page loaded.",
    error: null,
    staleMessage: null,
    manifestRevision: page.manifest_revision,
    currentOffset: page.offset,
    visitedOffsets: [...request.visitedOffsetsAfterSuccess],
    entries: page.entries.map((entry) => ({ ...entry })),
    nextOffset: page.next_offset,
    selectedCategory: null,
    selectedEntry: null,
    inspection: null,
  };
}

export function beginAssetCatalogInspection(
  state: AssetCatalogState,
  entryId: string,
): AssetCatalogTransition<AssetCatalogInspectIntent> | null {
  if (
    !state.workspaceId ||
    !state.manifestRevision ||
    state.consistency !== "current" ||
    state.listRequest
  ) {
    return null;
  }
  const entry = state.entries.find((candidate) => candidate.entry_id === entryId);
  if (!entry?.inspectable) return null;

  const token = nextRequestToken(state.inspectToken);
  const intentValues: AssetCatalogInspectIntent = {
    workspaceId: state.workspaceId,
    generation: state.generation,
    token,
    manifestRevision: state.manifestRevision,
    entryId,
  };
  const stateIntent = immutableInspectIntent(intentValues);
  const callerIntent = immutableInspectIntent(intentValues);
  return {
    intent: callerIntent,
    state: {
      ...state,
      inspectToken: token,
      inspectRequest: stateIntent,
      selectedEntry: { ...entry },
      inspection: null,
      status: "Inspecting asset metadata…",
      error: null,
    },
  };
}

export function receiveAssetCatalogInspection(
  state: AssetCatalogState,
  intent: AssetCatalogInspectIntent,
  rawReply: unknown,
): AssetCatalogState {
  if (!matchesInspectRequest(state, intent)) return state;

  const decoded = decodeAssetCatalogInspectReply(rawReply);
  if (!decoded.ok) {
    if (decoded.kind === "conflict") {
      return staleCatalog(state, "conflict", decoded.message);
    }
    return {
      ...state,
      inspectRequest: null,
      status: null,
      error: boundedMessage(decoded.message),
      inspection: null,
    };
  }

  const result = decoded.value;
  if (result.manifest_revision !== intent.manifestRevision) {
    return staleCatalog(
      state,
      "stale",
      "The asset catalog changed before inspection completed. Refresh it before continuing.",
    );
  }
  if (result.entry.entry_id !== intent.entryId) {
    return {
      ...state,
      inspectRequest: null,
      selectedEntry: null,
      inspection: null,
      status: null,
      error: "Asset inspection did not match the selected catalog entry.",
    };
  }

  return {
    ...state,
    inspectRequest: null,
    selectedEntry: { ...result.entry },
    inspection: cloneInspection(result.inspection),
    status: "Asset metadata inspected.",
    error: null,
  };
}

export function selectAssetCatalogCategory(
  state: AssetCatalogState,
  category: AssetCatalogCategory | null,
): AssetCatalogState {
  if (
    category !== null &&
    !state.entries.some((entry) => entry.category === category)
  ) {
    return state;
  }
  return {
    ...state,
    selectedCategory: category,
    selectedEntry: null,
    inspection: null,
    inspectRequest: null,
  };
}

export function assetCatalogPageCategories(
  state: AssetCatalogState,
): AssetCatalogCategory[] {
  return [...new Set(state.entries.map((entry) => entry.category))];
}

export function decodeAssetCatalogListReply(
  value: unknown,
): AssetCatalogDecoded<StudioAssetCatalogListResponse["result"]> {
  try {
    return decodeClientResult(value, "asset.catalog.list", decodeListResult);
  } catch {
    return invalidResponse();
  }
}

export function decodeAssetCatalogInspectReply(
  value: unknown,
): AssetCatalogDecoded<StudioAssetCatalogInspectResponse["result"]> {
  try {
    return decodeClientResult(value, "asset.catalog.inspect", decodeInspectResult);
  } catch {
    return invalidResponse();
  }
}

function decodeClientResult<TResult>(
  value: unknown,
  method: "asset.catalog.list" | "asset.catalog.inspect",
  decodeResult: (value: unknown) => TResult | null,
): AssetCatalogDecoded<TResult> {
  const client = exactRecord(value, ["ok", "value"]) ?? exactRecord(value, ["ok", "error"]);
  if (!client || typeof client.ok !== "boolean") return invalidResponse();

  if (!client.ok) {
    const error = exactRecord(client.error, ["code", "message"]);
    if (!error || !isClientErrorCode(error.code) || typeof error.message !== "string") {
      return invalidResponse();
    }
    return {
      ok: false,
      kind: "client-error",
      message: clientErrorMessage(error.code),
    };
  }

  const envelope = asRecord(client.value);
  if (!envelope) return invalidResponse();
  if (envelope.kind === "error") return decodeProtocolError(envelope);
  const response = exactRecord(envelope, [
    "protocol",
    "protocol_version",
    "kind",
    "request_id",
    "method",
    "result",
  ]);
  if (
    !response ||
    response.protocol !== "rpg-world-forge.studio_protocol" ||
    response.protocol_version !== 1 ||
    response.kind !== "response" ||
    typeof response.request_id !== "string" ||
    response.request_id.length === 0 ||
    response.method !== method
  ) {
    return invalidResponse();
  }
  const result = decodeResult(response.result);
  if (!result) return invalidResponse();
  return { ok: true, value: result };
}

function decodeProtocolError(value: Record<string, unknown>): AssetCatalogDecoded<never> {
  const envelope = exactRecord(value, [
    "protocol",
    "protocol_version",
    "kind",
    "request_id",
    "error",
  ]);
  const error = envelope ? exactRecord(envelope.error, ["code", "message", "details"]) : null;
  if (
    !envelope ||
    envelope.protocol !== "rpg-world-forge.studio_protocol" ||
    envelope.protocol_version !== 1 ||
    envelope.kind !== "error" ||
    !(envelope.request_id === null || typeof envelope.request_id === "string") ||
    !error ||
    !isProtocolErrorCode(error.code) ||
    typeof error.message !== "string" ||
    !asRecord(error.details)
  ) {
    return invalidResponse();
  }
  if (error.code === "conflict") {
    return {
      ok: false,
      kind: "conflict",
      message: "The asset catalog revision conflicted with this request. Refresh before continuing.",
    };
  }
  return {
    ok: false,
    kind: "protocol-error",
    message: protocolErrorMessage(error.code),
  };
}

function decodeListResult(
  value: unknown,
): StudioAssetCatalogListResponse["result"] | null {
  const result = exactRecord(value, [
    "manifest_revision",
    "offset",
    "limit",
    "entries",
    "next_offset",
  ]);
  if (
    !result ||
    !isSha256(result.manifest_revision) ||
    !isNonNegativeInteger(result.offset) ||
    !isIntegerInRange(result.limit, 1, MAX_PAGE_ENTRIES) ||
    !Array.isArray(result.entries) ||
    result.entries.length > MAX_PAGE_ENTRIES ||
    !(
      result.next_offset === null ||
      (isNonNegativeInteger(result.next_offset) && result.next_offset > result.offset)
    )
  ) {
    return null;
  }
  const entries: StudioAssetCatalogEntry[] = [];
  for (const value of result.entries) {
    const entry = decodeEntry(value);
    if (!entry) return null;
    entries.push(entry);
  }
  return {
    manifest_revision: result.manifest_revision,
    offset: result.offset,
    limit: result.limit,
    entries,
    next_offset: result.next_offset,
  };
}

function decodeInspectResult(
  value: unknown,
): StudioAssetCatalogInspectResponse["result"] | null {
  const result = exactRecord(value, ["manifest_revision", "entry", "inspection"]);
  if (!result || !isSha256(result.manifest_revision)) return null;
  const entry = decodeEntry(result.entry);
  const inspection = decodeInspection(result.inspection);
  return entry && inspection
    ? { manifest_revision: result.manifest_revision, entry, inspection }
    : null;
}

function decodeEntry(value: unknown): StudioAssetCatalogEntry | null {
  const entry = exactRecord(value, [
    "entry_id",
    "asset_id",
    "category",
    "role",
    "path",
    "sha256",
    "media_type",
    "selected",
    "inspectable",
  ]);
  if (
    !entry ||
    typeof entry.entry_id !== "string" ||
    !ASSET_ENTRY_ID_PATTERN.test(entry.entry_id) ||
    !isNullableBoundedString(entry.asset_id, 128) ||
    !isAssetCategory(entry.category) ||
    !isNullableBoundedString(entry.role, 128) ||
    !(entry.path === null || isPortableCatalogPath(entry.path)) ||
    !isSha256(entry.sha256) ||
    !isNullableBoundedString(entry.media_type, 128) ||
    typeof entry.selected !== "boolean" ||
    typeof entry.inspectable !== "boolean"
  ) {
    return null;
  }
  if (
    (entry.path === null &&
      (entry.category !== "processing_recipe" || entry.selected || entry.inspectable)) ||
    (entry.selected && entry.category !== "production_output")
  ) {
    return null;
  }
  return {
    entry_id: entry.entry_id,
    asset_id: entry.asset_id,
    category: entry.category,
    role: entry.role,
    path: entry.path,
    sha256: entry.sha256,
    media_type: entry.media_type,
    selected: entry.selected,
    inspectable: entry.inspectable,
  };
}

function decodeInspection(value: unknown): StudioAssetInspection | null {
  const inspection = asRecord(value);
  if (!inspection || !isAssetInspectionKind(inspection.kind)) return null;
  switch (inspection.kind) {
    case "json": {
      const exact = exactRecord(inspection, ["kind", "encoding", "content", "value"]);
      const jsonValue = exact ? asRecord(exact.value) : null;
      if (
        !exact ||
        exact.encoding !== "utf-8" ||
        !isBoundedUtf8Text(exact.content) ||
        !jsonValue
      ) {
        return null;
      }
      const parsed = parseBoundedJsonObject(exact.content);
      if (
        !parsed ||
        !isBoundedJsonObject(jsonValue) ||
        !jsonValuesEqual(parsed, jsonValue)
      ) {
        return null;
      }
      return {
        kind: "json",
        encoding: "utf-8",
        content: exact.content,
        value: parsed,
      };
    }
    case "glsl": {
      const exact = exactRecord(inspection, ["kind", "encoding", "content"]);
      return exact && exact.encoding === "utf-8" && isBoundedUtf8Text(exact.content)
        ? { kind: "glsl", encoding: "utf-8", content: exact.content }
        : null;
    }
    case "png": {
      const exact = exactRecord(inspection, [
        "kind",
        "width",
        "height",
        "bit_depth",
        "color_type",
        "interlaced",
      ]);
      return exact &&
        isPositiveInteger(exact.width) &&
        isPositiveInteger(exact.height) &&
        isPositiveInteger(exact.bit_depth) &&
        isNonNegativeInteger(exact.color_type) &&
        typeof exact.interlaced === "boolean"
        ? {
            kind: "png",
            width: exact.width,
            height: exact.height,
            bit_depth: exact.bit_depth,
            color_type: exact.color_type,
            interlaced: exact.interlaced,
          }
        : null;
    }
    case "wav": {
      const exact = exactRecord(inspection, [
        "kind",
        "channels",
        "sample_rate",
        "sample_width_bits",
        "frame_count",
        "duration_ms",
      ]);
      return exact &&
        isPositiveInteger(exact.channels) &&
        isPositiveInteger(exact.sample_rate) &&
        isPositiveInteger(exact.sample_width_bits) &&
        isNonNegativeInteger(exact.frame_count) &&
        isNonNegativeInteger(exact.duration_ms)
        ? {
            kind: "wav",
            channels: exact.channels,
            sample_rate: exact.sample_rate,
            sample_width_bits: exact.sample_width_bits,
            frame_count: exact.frame_count,
            duration_ms: exact.duration_ms,
          }
        : null;
    }
    case "font": {
      const exact = exactRecord(inspection, ["kind", "flavor", "table_count"]);
      return exact &&
        (exact.flavor === "truetype" || exact.flavor === "opentype") &&
        isPositiveInteger(exact.table_count)
        ? { kind: "font", flavor: exact.flavor, table_count: exact.table_count }
        : null;
    }
    case "glb":
      return decodeGlbInspection(inspection);
    case "unavailable": {
      const exact = exactRecord(inspection, ["kind", "reason"]);
      return exact &&
        (exact.reason === "identity_only" || exact.reason === "unsupported_media_type")
        ? { kind: "unavailable", reason: exact.reason }
        : null;
    }
    default:
      return null;
  }
}

function decodeGlbInspection(
  value: Record<string, unknown>,
): Extract<StudioAssetInspection, { kind: "glb" }> | null {
  const inspection = exactRecord(value, [
    "kind",
    "byte_length",
    "json_chunk_bytes",
    "bin_chunk_bytes",
    "extensions_used",
    "extensions_required",
    "external_uris",
    "embedded_uris",
    "max_texture_dimension",
    "metrics",
  ]);
  if (
    !inspection ||
    !isNonNegativeInteger(inspection.byte_length) ||
    !isNonNegativeInteger(inspection.json_chunk_bytes) ||
    !isNonNegativeInteger(inspection.bin_chunk_bytes) ||
    !isBoundedStringArray(inspection.extensions_used) ||
    !isBoundedStringArray(inspection.extensions_required) ||
    !isBoundedStringArray(inspection.external_uris, isSafeExternalUri) ||
    !isNonNegativeInteger(inspection.embedded_uris) ||
    !isNonNegativeInteger(inspection.max_texture_dimension)
  ) {
    return null;
  }
  const metrics = exactRecord(inspection.metrics, [
    "nodes",
    "meshes",
    "materials",
    "textures",
    "skins",
    "bones",
    "influences",
    "animations",
    "vertices",
    "triangles",
    "external_uris",
  ]);
  if (!isGlbMetrics(metrics)) return null;
  return {
    kind: "glb",
    byte_length: inspection.byte_length,
    json_chunk_bytes: inspection.json_chunk_bytes,
    bin_chunk_bytes: inspection.bin_chunk_bytes,
    extensions_used: [...inspection.extensions_used],
    extensions_required: [...inspection.extensions_required],
    external_uris: [...inspection.external_uris],
    embedded_uris: inspection.embedded_uris,
    max_texture_dimension: inspection.max_texture_dimension,
    metrics: {
      nodes: metrics.nodes,
      meshes: metrics.meshes,
      materials: metrics.materials,
      textures: metrics.textures,
      skins: metrics.skins,
      bones: metrics.bones,
      influences: metrics.influences,
      animations: metrics.animations,
      vertices: metrics.vertices,
      triangles: metrics.triangles,
      external_uris: metrics.external_uris,
    },
  };
}

function isGlbMetrics(
  value: Record<string, unknown> | null,
): value is Record<
  | "nodes"
  | "meshes"
  | "materials"
  | "textures"
  | "skins"
  | "bones"
  | "influences"
  | "animations"
  | "vertices"
  | "triangles"
  | "external_uris",
  number
> {
  return Boolean(value && Object.values(value).every(isNonNegativeInteger));
}

function cloneInspection(value: StudioAssetInspection): StudioAssetInspection {
  const decoded = decodeInspection(value);
  if (!decoded) throw new TypeError("Decoded inspection could not be cloned");
  return decoded;
}

function parseBoundedJsonObject(content: string): Record<string, unknown> | null {
  let parsed: unknown;
  try {
    parsed = JSON.parse(content) as unknown;
  } catch {
    return null;
  }
  const record = asRecord(parsed);
  return record && isBoundedJsonObject(record) ? record : null;
}

function isBoundedJsonObject(value: Record<string, unknown>): boolean {
  const budget = { nodes: 0, semanticBytes: 0 };
  if (!visitBoundedJson(value, 0, budget)) return false;
  const serialized = JSON.stringify(value);
  return typeof serialized === "string" && isBoundedUtf8Text(serialized);
}

function visitBoundedJson(
  value: unknown,
  depth: number,
  budget: { nodes: number; semanticBytes: number },
): boolean {
  budget.nodes += 1;
  if (budget.nodes > MAX_JSON_NODES || depth > MAX_JSON_DEPTH) return false;
  if (value === null || typeof value === "boolean") return true;
  if (typeof value === "number") return Number.isFinite(value);
  if (typeof value === "string") return addJsonSemanticBytes(value, budget);
  if (Array.isArray(value)) {
    if (Object.getPrototypeOf(value) !== Array.prototype) return false;
    return value.every((item) => visitBoundedJson(item, depth + 1, budget));
  }
  const record = asRecord(value);
  if (!record) return false;
  const prototype = Reflect.getPrototypeOf(record);
  if (prototype !== Object.prototype && prototype !== null) return false;
  const keys = Object.keys(record);
  if (keys.length > MAX_JSON_NODES) return false;
  for (const key of keys) {
    if (
      key === "__proto__" ||
      key === "constructor" ||
      key === "prototype" ||
      !addJsonSemanticBytes(key, budget) ||
      !visitBoundedJson(record[key], depth + 1, budget)
    ) {
      return false;
    }
  }
  return true;
}

function addJsonSemanticBytes(
  value: string,
  budget: { semanticBytes: number },
): boolean {
  if (value.length > MAX_INSPECTION_TEXT_BYTES - budget.semanticBytes) return false;
  budget.semanticBytes += new TextEncoder().encode(value).byteLength;
  return budget.semanticBytes <= MAX_INSPECTION_TEXT_BYTES;
}

function jsonValuesEqual(left: unknown, right: unknown): boolean {
  if (
    left === null ||
    right === null ||
    typeof left !== "object" ||
    typeof right !== "object"
  ) {
    return left === right;
  }
  if (Array.isArray(left) || Array.isArray(right)) {
    return (
      Array.isArray(left) &&
      Array.isArray(right) &&
      left.length === right.length &&
      left.every((value, index) => jsonValuesEqual(value, right[index]))
    );
  }
  const leftRecord = asRecord(left);
  const rightRecord = asRecord(right);
  if (!leftRecord || !rightRecord) return false;
  const keys = Object.keys(leftRecord);
  return (
    keys.length === Object.keys(rightRecord).length &&
    keys.every(
      (key) =>
        Object.hasOwn(rightRecord, key) &&
        jsonValuesEqual(leftRecord[key], rightRecord[key]),
    )
  );
}

function clearCatalogPage(state: AssetCatalogState): AssetCatalogState {
  return {
    ...state,
    listRequest: null,
    inspectRequest: null,
    manifestRevision: null,
    currentOffset: 0,
    visitedOffsets: [],
    entries: [],
    nextOffset: null,
    selectedCategory: null,
    selectedEntry: null,
    inspection: null,
  };
}

function staleCatalog(
  state: AssetCatalogState,
  consistency: "stale" | "conflict",
  message: string,
): AssetCatalogState {
  const safeMessage = boundedMessage(message);
  return {
    ...clearCatalogPage(state),
    consistency,
    status: null,
    error: safeMessage,
    staleMessage: safeMessage,
  };
}

function matchingListRequest(
  state: AssetCatalogState,
  intent: AssetCatalogListIntent,
): AssetCatalogListIntent | null {
  const request = state.listRequest;
  if (!request) return null;
  try {
    return state.workspaceId === request.workspaceId &&
      state.workspaceId === intent.workspaceId &&
      state.generation === request.generation &&
      state.generation === intent.generation &&
      state.listToken === request.token &&
      state.listToken === intent.token &&
      request.mode === intent.mode &&
      request.offset === intent.offset &&
      request.expectedManifestRevision === intent.expectedManifestRevision &&
      sameCatalogPage(request.page, intent.page) &&
      request.visitedOffsetsAfterSuccess.length <= MAX_VISITED_CATALOG_OFFSETS &&
      sameOffsets(
        request.visitedOffsetsAfterSuccess,
        intent.visitedOffsetsAfterSuccess,
      )
      ? request
      : null;
  } catch {
    return null;
  }
}

function matchesInspectRequest(
  state: AssetCatalogState,
  intent: AssetCatalogInspectIntent,
): boolean {
  const request = state.inspectRequest;
  return Boolean(
    request &&
      state.workspaceId === request.workspaceId &&
      state.workspaceId === intent.workspaceId &&
      state.generation === request.generation &&
      state.generation === intent.generation &&
      state.inspectToken === request.token &&
      state.inspectToken === intent.token &&
      state.manifestRevision === intent.manifestRevision &&
      state.selectedEntry?.entry_id === intent.entryId &&
      request.token === intent.token &&
      request.manifestRevision === intent.manifestRevision &&
      request.entryId === intent.entryId,
  );
}

function immutableListIntent(values: AssetCatalogListIntent): AssetCatalogListIntent {
  const page = values.page
    ? Object.freeze({
        offset: values.page.offset,
        manifestRevision: values.page.manifestRevision,
      })
    : undefined;
  const history = Object.freeze(
    values.visitedOffsetsAfterSuccess.slice(-MAX_VISITED_CATALOG_OFFSETS),
  );
  return Object.freeze({
    workspaceId: values.workspaceId,
    generation: values.generation,
    token: values.token,
    mode: values.mode,
    offset: values.offset,
    expectedManifestRevision: values.expectedManifestRevision,
    page,
    visitedOffsetsAfterSuccess: history,
  });
}

function immutableInspectIntent(
  values: AssetCatalogInspectIntent,
): AssetCatalogInspectIntent {
  return Object.freeze({ ...values });
}

function appendVisitedOffset(offsets: readonly number[], offset: number): number[] {
  const start = Math.max(0, offsets.length - (MAX_VISITED_CATALOG_OFFSETS - 1));
  return [...offsets.slice(start), offset];
}

function sameCatalogPage(
  left: Readonly<StudioAssetCatalogPage> | undefined,
  right: Readonly<StudioAssetCatalogPage> | undefined,
): boolean {
  return (
    left === right ||
    (left !== undefined &&
      right !== undefined &&
      left.offset === right.offset &&
      left.manifestRevision === right.manifestRevision)
  );
}

function sameOffsets(left: readonly number[], right: readonly number[]): boolean {
  return (
    Array.isArray(right) &&
    right.length <= MAX_VISITED_CATALOG_OFFSETS &&
    left.length === right.length &&
    left.every(
      (offset, index) =>
        isNonNegativeInteger(offset) && offset === right[index],
    )
  );
}

function nextRequestToken(current: number): number {
  if (!Number.isSafeInteger(current) || current < 0 || current === Number.MAX_SAFE_INTEGER) {
    throw new RangeError("Asset catalog request token is exhausted");
  }
  return current + 1;
}

function exactRecord(
  value: unknown,
  keys: readonly string[],
): Record<string, unknown> | null {
  const record = asRecord(value);
  if (!record) return null;
  const actualKeys = Object.keys(record);
  return actualKeys.length === keys.length &&
    keys.every((key) => Object.hasOwn(record, key))
    ? record
    : null;
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function invalidResponse<T>(): AssetCatalogDecoded<T> {
  return {
    ok: false,
    kind: "invalid-response",
    message: "The asset catalog returned an invalid response.",
  };
}

function isSha256(value: unknown): value is string {
  return typeof value === "string" && SHA256_PATTERN.test(value);
}

function isNonNegativeInteger(value: unknown): value is number {
  return Number.isSafeInteger(value) && (value as number) >= 0;
}

function isPositiveInteger(value: unknown): value is number {
  return Number.isSafeInteger(value) && (value as number) >= 1;
}

function isIntegerInRange(value: unknown, minimum: number, maximum: number): value is number {
  return Number.isSafeInteger(value) && (value as number) >= minimum && (value as number) <= maximum;
}

function isNullableBoundedString(value: unknown, maximum: number): value is string | null {
  return value === null || (typeof value === "string" && value.length <= maximum);
}

function isBoundedUtf8Text(value: unknown): value is string {
  return (
    typeof value === "string" &&
    value.length <= MAX_INSPECTION_TEXT_BYTES &&
    new TextEncoder().encode(value).byteLength <= MAX_INSPECTION_TEXT_BYTES
  );
}

function isBoundedStringArray(
  value: unknown,
  predicate: (value: string) => boolean = () => true,
): value is string[] {
  return (
    Array.isArray(value) &&
    value.length <= MAX_PAGE_ENTRIES &&
    value.every(
      (item) => typeof item === "string" && item.length <= 256 && predicate(item),
    )
  );
}

function isPortableCatalogPath(value: unknown): value is string {
  if (
    typeof value !== "string" ||
    value.length === 0 ||
    value.length > 4_096 ||
    value !== value.normalize("NFC") ||
    value.startsWith("/") ||
    value.includes("\\") ||
    containsControlCharacter(value) ||
    /[<>:"|?*]/u.test(value)
  ) {
    return false;
  }
  return value.split("/").every((component) => {
    if (
      !component ||
      component === "." ||
      component === ".." ||
      component.endsWith(".") ||
      component.endsWith(" ")
    ) {
      return false;
    }
    const stem = component.split(".")[0]?.toUpperCase();
    return !(
      stem === "CON" ||
      stem === "PRN" ||
      stem === "AUX" ||
      stem === "NUL" ||
      /^COM[1-9]$/u.test(stem ?? "") ||
      /^LPT[1-9]$/u.test(stem ?? "")
    );
  });
}

function isSafeExternalUri(value: string): boolean {
  return (
    value.length > 0 &&
    !value.startsWith("/") &&
    !value.startsWith("\\") &&
    !/^[A-Za-z]:[\\/]/u.test(value) &&
    !/^file:/iu.test(value) &&
    !containsControlCharacter(value, true)
  );
}

function containsControlCharacter(value: string, includeDelete = false): boolean {
  return Array.from(value).some((character) => {
    const code = character.codePointAt(0) ?? 0;
    return code < 32 || (includeDelete && code === 127);
  });
}

function isAssetCategory(value: unknown): value is AssetCatalogCategory {
  return (
    typeof value === "string" &&
    Object.hasOwn(ASSET_CATALOG_CATEGORY_LABELS, value)
  );
}

function isAssetInspectionKind(value: unknown): value is AssetInspectionKind {
  return (
    typeof value === "string" &&
    Object.hasOwn(ASSET_INSPECTION_KIND_LABELS, value)
  );
}

function isClientErrorCode(
  value: unknown,
): value is "invalid_request" | "service_unavailable" | "timeout" | "cancelled" | "internal_error" {
  return (
    value === "invalid_request" ||
    value === "service_unavailable" ||
    value === "timeout" ||
    value === "cancelled" ||
    value === "internal_error"
  );
}

function clientErrorMessage(
  code: "invalid_request" | "service_unavailable" | "timeout" | "cancelled" | "internal_error",
): string {
  switch (code) {
    case "invalid_request":
      return "The asset catalog request was rejected.";
    case "service_unavailable":
      return "The local Forge service is unavailable.";
    case "timeout":
      return "The asset catalog request timed out.";
    case "cancelled":
      return "The asset catalog request was cancelled.";
    case "internal_error":
      return "The asset catalog request failed.";
  }
}

function isProtocolErrorCode(
  value: unknown,
): value is "invalid_request" | "not_found" | "conflict" | "invalid_state" | "internal_error" {
  return (
    value === "invalid_request" ||
    value === "not_found" ||
    value === "conflict" ||
    value === "invalid_state" ||
    value === "internal_error"
  );
}

function protocolErrorMessage(
  code: "invalid_request" | "not_found" | "invalid_state" | "internal_error",
): string {
  switch (code) {
    case "invalid_request":
      return "The asset catalog request was rejected.";
    case "not_found":
      return "The requested asset catalog entry is unavailable.";
    case "invalid_state":
      return "The asset catalog is not available in the current workspace state.";
    case "internal_error":
      return "The local Forge service could not read the asset catalog.";
  }
}

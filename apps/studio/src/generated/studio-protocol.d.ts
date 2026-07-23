/* AUTO-GENERATED from schemas/studio-protocol.schema.json. Do not edit by hand. */

export type ForgeStudioNDJSONApplicationProtocolV1 = {
  protocol: "rpg-world-forge.studio_protocol";
  protocol_version: 1;
  kind: "request" | "response" | "error" | "event";
  request_id: string | null;
  [k: string]: unknown;
} & (Request | Response | Error | Event);
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "method".
 */
export type Method =
  | "service.initialize"
  | "workspace.register"
  | "workspace.list"
  | "workspace.get"
  | "workspace.overview"
  | "source.list"
  | "source.read"
  | "asset.catalog.list"
  | "asset.catalog.inspect"
  | "asset.preview.open"
  | "asset.preview.read"
  | "asset.preview.close"
  | "world.validate"
  | "world.analyze"
  | "events.list"
  | "changeset.create"
  | "changeset.get"
  | "changeset.list"
  | "changeset.diff"
  | "changeset.approve"
  | "changeset.reject"
  | "changeset.apply"
  | "job.create"
  | "job.get"
  | "job.list"
  | "job.transition"
  | "job.cancel";
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "legacyMethod".
 */
export type LegacyMethod =
  | "service.initialize"
  | "workspace.register"
  | "workspace.list"
  | "workspace.get"
  | "events.list"
  | "job.get"
  | "job.list"
  | "job.transition";
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "workspaceScopedAuthoringMethod".
 */
export type WorkspaceScopedAuthoringMethod =
  "workspace.overview" | "source.list" | "world.validate" | "world.analyze";
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "workspaceId".
 */
export type WorkspaceId = string;
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "entityId".
 */
export type EntityId = string;
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "sha256".
 */
export type Sha256 = string;
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "portableSourcePath".
 */
export type PortableSourcePath = string;
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "assetEntryId".
 */
export type AssetEntryId = string;
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "assetPreviewHandle".
 */
export type AssetPreviewHandle = string;
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "assetPreviewBase64".
 */
export type AssetPreviewBase64 = string;
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "portableAssetCatalogPath".
 */
export type PortableAssetCatalogPath = string;
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "assetCatalogListParams".
 */
export type AssetCatalogListParams =
  | {
      workspace_id: WorkspaceId;
      offset?: 0;
      limit?: number;
      expected_manifest_revision?: Sha256;
    }
  | {
      workspace_id: WorkspaceId;
      offset: number;
      limit?: number;
      expected_manifest_revision: Sha256;
    };
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "assetCatalogEntry".
 */
export type AssetCatalogEntry = {
  [k: string]: unknown;
} & {
  entry_id: AssetEntryId;
  asset_id: string | null;
  category:
    | "manifest"
    | "target"
    | "visual_bible"
    | "audio_bible"
    | "inventory"
    | "specification"
    | "production_receipt"
    | "production_request"
    | "production_output"
    | "processing_receipt"
    | "processing_recipe"
    | "processing_output"
    | "license"
    | "qa"
    | "runtime_output";
  role: string | null;
  path: PortableAssetCatalogPath | null;
  sha256: Sha256;
  media_type: string | null;
  selected: boolean;
  inspectable: boolean;
};
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "assetInspection".
 */
export type AssetInspection =
  | AssetJsonInspection
  | AssetGlslInspection
  | AssetPngInspection
  | AssetWavInspection
  | AssetFontInspection
  | AssetGlbInspection
  | AssetUnavailableInspection;
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "changesetJsonPointerChange".
 */
export type ChangesetJsonPointerChange =
  | {
      operation: "add";
      pointer: string;
      value: unknown;
    }
  | {
      operation: "remove";
      pointer: string;
      old_value: unknown;
    }
  | {
      operation: "replace";
      pointer: string;
      old_value: unknown;
      value: unknown;
    };
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "changesetDiffOperation".
 */
export type ChangesetDiffOperation = {
  [k: string]: unknown;
} & {
  path: PortableSourcePath;
  operation: "create" | "replace" | "delete";
  base_sha256: Sha256 | null;
  base_size: number;
  proposed_sha256: Sha256 | null;
  size: number;
  /**
   * @maxItems 20000
   */
  text_hunks: ChangesetTextHunk[];
  json_pointer_changes: ChangesetJsonPointerChange[] | null;
};
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "changesetDiff".
 */
export type ChangesetDiff =
  | {
      changeset_id: EntityId;
      changeset_format_version: 1;
      available: false;
      unavailable_reason: "legacy_base_bytes_not_retained";
      review_sha256: null;
      /**
       * @maxItems 0
       */
      operations: [];
    }
  | {
      changeset_id: EntityId;
      changeset_format_version: 2;
      available: true;
      unavailable_reason: null;
      review_sha256: Sha256;
      /**
       * @minItems 1
       * @maxItems 256
       */
      operations: [ChangesetDiffOperation, ...ChangesetDiffOperation[]];
    };
export type ForgeStudioReviewableFileChangesetV2 = ChangesetV1 | ChangesetV2;
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "request".
 */
export type Request =
  | LegacyRequest
  | WorkspaceScopedAuthoringRequest
  | SourceReadRequest
  | AssetCatalogListRequest
  | AssetCatalogInspectRequest
  | AssetPreviewOpenRequest
  | AssetPreviewReadRequest
  | AssetPreviewCloseRequest
  | ChangesetCreateRequest
  | ChangesetGetRequest
  | ChangesetListRequest
  | ChangesetDiffRequest
  | ChangesetApproveRequest
  | ChangesetRejectRequest
  | ChangesetApplyRequest
  | JobCreateRequest
  | JobCancelRequest;
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "managedJobV2".
 */
export type ManagedStudioJobRecordV2 = ManagedStudioJobV2CommonRecord &
  (
    | AssetReceiptValidateOperation
    | AssetpackVerifyOperation
    | RuntimeHeadlessOperation
    | RuntimeReplayOperation
  ) & {
    [k: string]: unknown;
  };
export type ForgeStudioDurableJobRecordV2WithV1ReadCompatibility = {
  format?: "rpg-world-forge.studio_job";
  format_version?: 1 | 2;
  [k: string]: unknown;
} & (LegacyStudioJobRecordV1 | ManagedStudioJobRecordV2);
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "response".
 */
export type Response =
  | LegacyResponse
  | WorkspaceOverviewResponse
  | SourceListResponse
  | SourceReadResponse
  | AssetCatalogListResponse
  | AssetCatalogInspectResponse
  | AssetPreviewOpenResponse
  | AssetPreviewReadResponse
  | AssetPreviewCloseResponse
  | WorldValidateResponse
  | WorldAnalyzeResponse
  | ChangesetCreateResponse
  | ChangesetGetResponse
  | ChangesetListResponse
  | ChangesetDiffResponse
  | ChangesetApproveResponse
  | ChangesetRejectResponse
  | ChangesetApplyResponse
  | JobCreateResponse
  | JobCancelResponse;
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "errorCode".
 */
export type ErrorCode =
  "invalid_request" | "not_found" | "conflict" | "invalid_state" | "internal_error";
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "error".
 */
export type Error = Base & {
  protocol: "rpg-world-forge.studio_protocol";
  protocol_version: 1;
  kind: "error";
  request_id: string | null;
  error: {
    code: ErrorCode;
    message: string;
    details: {
      [k: string]: unknown;
    };
  };
};
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "event".
 */
export type Event = Base & {
  protocol: "rpg-world-forge.studio_protocol";
  protocol_version: 1;
  kind: "event";
  request_id: null;
  event: {
    [k: string]: unknown;
  };
};

/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "base".
 */
export interface Base {
  protocol: "rpg-world-forge.studio_protocol";
  protocol_version: 1;
  kind: "request" | "response" | "error" | "event";
  request_id: string | null;
  [k: string]: unknown;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "workspaceScopedParams".
 */
export interface WorkspaceScopedParams {
  workspace_id: WorkspaceId;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "sourceReadParams".
 */
export interface SourceReadParams {
  workspace_id: WorkspaceId;
  path: PortableSourcePath;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "assetCatalogInspectParams".
 */
export interface AssetCatalogInspectParams {
  workspace_id: WorkspaceId;
  entry_id: AssetEntryId;
  expected_manifest_revision: Sha256;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "assetPreviewOpenParams".
 */
export interface AssetPreviewOpenParams {
  workspace_id: WorkspaceId;
  manifest_revision: Sha256;
  entry_id: AssetEntryId;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "assetPreviewReadParams".
 */
export interface AssetPreviewReadParams {
  handle: AssetPreviewHandle;
  sequence: number;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "assetPreviewCloseParams".
 */
export interface AssetPreviewCloseParams {
  handle: AssetPreviewHandle;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "assetJsonInspection".
 */
export interface AssetJsonInspection {
  kind: "json";
  encoding: "utf-8";
  content: string;
  value: {
    [k: string]: unknown;
  };
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "assetGlslInspection".
 */
export interface AssetGlslInspection {
  kind: "glsl";
  encoding: "utf-8";
  content: string;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "assetPngInspection".
 */
export interface AssetPngInspection {
  kind: "png";
  width: number;
  height: number;
  bit_depth: number;
  color_type: number;
  interlaced: boolean;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "assetWavInspection".
 */
export interface AssetWavInspection {
  kind: "wav";
  channels: number;
  sample_rate: number;
  sample_width_bits: number;
  frame_count: number;
  duration_ms: number;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "assetFontInspection".
 */
export interface AssetFontInspection {
  kind: "font";
  flavor: "truetype" | "opentype";
  table_count: number;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "assetGlbMetrics".
 */
export interface AssetGlbMetrics {
  nodes: number;
  meshes: number;
  materials: number;
  textures: number;
  skins: number;
  bones: number;
  influences: number;
  animations: number;
  vertices: number;
  triangles: number;
  external_uris: number;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "assetGlbInspection".
 */
export interface AssetGlbInspection {
  kind: "glb";
  byte_length: number;
  json_chunk_bytes: number;
  bin_chunk_bytes: number;
  /**
   * @maxItems 64
   */
  extensions_used: string[];
  /**
   * @maxItems 64
   */
  extensions_required: string[];
  /**
   * @maxItems 64
   */
  external_uris: string[];
  embedded_uris: number;
  max_texture_dimension: number;
  metrics: AssetGlbMetrics;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "assetUnavailableInspection".
 */
export interface AssetUnavailableInspection {
  kind: "unavailable";
  reason: "identity_only" | "unsupported_media_type";
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "assetCatalogListResult".
 */
export interface AssetCatalogListResult {
  manifest_revision: Sha256;
  offset: number;
  limit: number;
  /**
   * @maxItems 64
   */
  entries: AssetCatalogEntry[];
  next_offset: number | null;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "assetCatalogInspectResult".
 */
export interface AssetCatalogInspectResult {
  manifest_revision: Sha256;
  entry: AssetCatalogEntry;
  inspection: AssetInspection;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "assetPreviewOpenResult".
 */
export interface AssetPreviewOpenResult {
  handle: AssetPreviewHandle;
  manifest_revision: Sha256;
  entry_id: AssetEntryId;
  media_type: "image/png" | "audio/wav";
  byte_length: number;
  sha256: Sha256;
  chunk_bytes: 65536;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "assetPreviewReadResult".
 */
export interface AssetPreviewReadResult {
  handle: AssetPreviewHandle;
  sequence: number;
  data_base64: AssetPreviewBase64;
  byte_length: number;
  cumulative_bytes: number;
  cumulative_sha256: Sha256;
  eof: boolean;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "assetPreviewCloseResult".
 */
export interface AssetPreviewCloseResult {
  handle: AssetPreviewHandle;
  closed: true;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "sourceDocumentSummary".
 */
export interface SourceDocumentSummary {
  path: PortableSourcePath;
  kind: string;
  size: number;
  sha256: Sha256;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "sourceDocument".
 */
export interface SourceDocument {
  path: PortableSourcePath;
  kind: string;
  size: number;
  sha256: Sha256;
  encoding: "utf-8";
  content: string;
  json: {
    [k: string]: unknown;
  };
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "diagnostic".
 */
export interface Diagnostic {
  severity: "error";
  code: "source_error" | "validation_error";
  path: string;
  message: string;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "worldValidation".
 */
export interface WorldValidation {
  valid: boolean;
  profile: "release";
  world_id: string | null;
  object_count: number;
  /**
   * @maxItems 512
   */
  diagnostics: Diagnostic[];
  diagnostics_truncated: boolean;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "workspaceOverview".
 */
export interface WorkspaceOverview {
  workspace_id: WorkspaceId;
  project: {
    world_id: string;
    title: string;
    world_version: string | null;
  };
  status: {
    current_phase: string | null;
    revision: number;
    canon_locked: boolean;
    worldpack_hash: Sha256 | null;
  };
  repositories: {
    game_registered: boolean;
    bundle_registered: boolean;
  };
  capabilities: {
    providers: false;
    source_inspection: true;
    world_validation: true;
    narrative_analysis: true;
    staged_changesets: true;
    asset_catalog_inspection: true;
  };
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "workspaceOverviewResult".
 */
export interface WorkspaceOverviewResult {
  overview: WorkspaceOverview;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "sourceListResult".
 */
export interface SourceListResult {
  /**
   * @maxItems 1024
   */
  documents: SourceDocumentSummary[];
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "sourceReadResult".
 */
export interface SourceReadResult {
  document: SourceDocument;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "worldValidateResult".
 */
export interface WorldValidateResult {
  validation: WorldValidation;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "worldAnalyzeResult".
 */
export interface WorldAnalyzeResult {
  validation: WorldValidation;
  analysis: NarrativeAnalysis | null;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "narrativeAnalysis".
 */
export interface NarrativeAnalysis {
  format: "rpg-world-forge.narrative_analysis";
  format_version: 1;
  world_id: string;
  summary: {
    [k: string]: unknown;
  };
  findings: NarrativeFinding[];
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "narrativeFinding".
 */
export interface NarrativeFinding {
  severity: "error" | "warning" | "info";
  code: string;
  path: string;
  message: string;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "changesetStageCreateOperation".
 */
export interface ChangesetStageCreateOperation {
  path: PortableSourcePath;
  operation: "create";
  content: string;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "changesetStageReplaceOperation".
 */
export interface ChangesetStageReplaceOperation {
  path: PortableSourcePath;
  operation: "replace";
  expected_base_sha256: Sha256;
  content: string;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "changesetStageDeleteOperation".
 */
export interface ChangesetStageDeleteOperation {
  path: PortableSourcePath;
  operation: "delete";
  expected_base_sha256: Sha256;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "changesetCreateParams".
 */
export interface ChangesetCreateParams {
  changeset_id?: EntityId;
  workspace_id: WorkspaceId;
  /**
   * @minItems 1
   * @maxItems 256
   */
  operations: [
    ChangesetStageCreateOperation | ChangesetStageReplaceOperation | ChangesetStageDeleteOperation,
    ...(
      ChangesetStageCreateOperation | ChangesetStageReplaceOperation | ChangesetStageDeleteOperation
    )[],
  ];
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "changesetIdParams".
 */
export interface ChangesetIdParams {
  changeset_id: EntityId;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "changesetListParams".
 */
export interface ChangesetListParams {
  workspace_id?: WorkspaceId;
  status?: "staged" | "approved" | "applying" | "rejected" | "applied";
  limit?: number;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "changesetActionParams".
 */
export interface ChangesetActionParams {
  changeset_id: EntityId;
  expected_review_sha256?: Sha256;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "changesetTextDiffLine".
 */
export interface ChangesetTextDiffLine {
  kind: "context" | "remove" | "add";
  text: string;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "changesetTextHunk".
 */
export interface ChangesetTextHunk {
  base_start: number;
  base_count: number;
  proposed_start: number;
  proposed_count: number;
  /**
   * @minItems 1
   * @maxItems 40000
   */
  lines: [ChangesetTextDiffLine, ...ChangesetTextDiffLine[]];
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "changesetResult".
 */
export interface ChangesetResult {
  changeset: ForgeStudioReviewableFileChangesetV2;
}
export interface ChangesetV1 {
  format: "rpg-world-forge.studio_changeset";
  format_version: 1;
  changeset_id: string;
  workspace_id: string;
  status: "staged" | "approved" | "applying" | "rejected" | "applied";
  /**
   * @minItems 1
   * @maxItems 256
   */
  operations: [
    (
      | {
          path: string;
          operation: "create";
          base_sha256: null;
          proposed_sha256: string;
          size: number;
        }
      | {
          path: string;
          operation: "replace";
          base_sha256: string;
          proposed_sha256: string;
          size: number;
        }
      | {
          path: string;
          operation: "delete";
          base_sha256: string;
          proposed_sha256: null;
          size: 0;
        }
    ),
    ...(
      | {
          path: string;
          operation: "create";
          base_sha256: null;
          proposed_sha256: string;
          size: number;
        }
      | {
          path: string;
          operation: "replace";
          base_sha256: string;
          proposed_sha256: string;
          size: number;
        }
      | {
          path: string;
          operation: "delete";
          base_sha256: string;
          proposed_sha256: null;
          size: 0;
        }
    )[],
  ];
  created_at: string;
  updated_at: string;
}
export interface ChangesetV2 {
  format: "rpg-world-forge.studio_changeset";
  format_version: 2;
  changeset_id: string;
  workspace_id: string;
  status: "staged" | "approved" | "applying" | "rejected" | "applied";
  /**
   * @minItems 1
   * @maxItems 256
   */
  operations: [
    (
      | {
          path: string;
          operation: "create";
          base_sha256: null;
          base_size: 0;
          proposed_sha256: string;
          size: number;
        }
      | {
          path: string;
          operation: "replace";
          base_sha256: string;
          base_size: number;
          proposed_sha256: string;
          size: number;
        }
      | {
          path: string;
          operation: "delete";
          base_sha256: string;
          base_size: number;
          proposed_sha256: null;
          size: 0;
        }
    ),
    ...(
      | {
          path: string;
          operation: "create";
          base_sha256: null;
          base_size: 0;
          proposed_sha256: string;
          size: number;
        }
      | {
          path: string;
          operation: "replace";
          base_sha256: string;
          base_size: number;
          proposed_sha256: string;
          size: number;
        }
      | {
          path: string;
          operation: "delete";
          base_sha256: string;
          base_size: number;
          proposed_sha256: null;
          size: 0;
        }
    )[],
  ];
  review_sha256: string;
  created_at: string;
  updated_at: string;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "changesetListResult".
 */
export interface ChangesetListResult {
  /**
   * @maxItems 1000
   */
  changesets: ForgeStudioReviewableFileChangesetV2[];
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "changesetDiffResult".
 */
export interface ChangesetDiffResult {
  diff: ChangesetDiff;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "legacyRequest".
 */
export interface LegacyRequest {
  protocol: "rpg-world-forge.studio_protocol";
  protocol_version: 1;
  kind: "request";
  request_id: string;
  method: LegacyMethod;
  params: {
    [k: string]: unknown;
  };
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "workspaceScopedAuthoringRequest".
 */
export interface WorkspaceScopedAuthoringRequest {
  protocol: "rpg-world-forge.studio_protocol";
  protocol_version: 1;
  kind: "request";
  request_id: string;
  method: WorkspaceScopedAuthoringMethod;
  params: WorkspaceScopedParams;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "sourceReadRequest".
 */
export interface SourceReadRequest {
  protocol: "rpg-world-forge.studio_protocol";
  protocol_version: 1;
  kind: "request";
  request_id: string;
  method: "source.read";
  params: SourceReadParams;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "assetCatalogListRequest".
 */
export interface AssetCatalogListRequest {
  protocol: "rpg-world-forge.studio_protocol";
  protocol_version: 1;
  kind: "request";
  request_id: string;
  method: "asset.catalog.list";
  params: AssetCatalogListParams;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "assetCatalogInspectRequest".
 */
export interface AssetCatalogInspectRequest {
  protocol: "rpg-world-forge.studio_protocol";
  protocol_version: 1;
  kind: "request";
  request_id: string;
  method: "asset.catalog.inspect";
  params: AssetCatalogInspectParams;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "assetPreviewOpenRequest".
 */
export interface AssetPreviewOpenRequest {
  protocol: "rpg-world-forge.studio_protocol";
  protocol_version: 1;
  kind: "request";
  request_id: string;
  method: "asset.preview.open";
  params: AssetPreviewOpenParams;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "assetPreviewReadRequest".
 */
export interface AssetPreviewReadRequest {
  protocol: "rpg-world-forge.studio_protocol";
  protocol_version: 1;
  kind: "request";
  request_id: string;
  method: "asset.preview.read";
  params: AssetPreviewReadParams;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "assetPreviewCloseRequest".
 */
export interface AssetPreviewCloseRequest {
  protocol: "rpg-world-forge.studio_protocol";
  protocol_version: 1;
  kind: "request";
  request_id: string;
  method: "asset.preview.close";
  params: AssetPreviewCloseParams;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "changesetCreateRequest".
 */
export interface ChangesetCreateRequest {
  protocol: "rpg-world-forge.studio_protocol";
  protocol_version: 1;
  kind: "request";
  request_id: string;
  method: "changeset.create";
  params: ChangesetCreateParams;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "changesetGetRequest".
 */
export interface ChangesetGetRequest {
  protocol: "rpg-world-forge.studio_protocol";
  protocol_version: 1;
  kind: "request";
  request_id: string;
  method: "changeset.get";
  params: ChangesetIdParams;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "changesetListRequest".
 */
export interface ChangesetListRequest {
  protocol: "rpg-world-forge.studio_protocol";
  protocol_version: 1;
  kind: "request";
  request_id: string;
  method: "changeset.list";
  params: ChangesetListParams;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "changesetDiffRequest".
 */
export interface ChangesetDiffRequest {
  protocol: "rpg-world-forge.studio_protocol";
  protocol_version: 1;
  kind: "request";
  request_id: string;
  method: "changeset.diff";
  params: ChangesetIdParams;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "changesetApproveRequest".
 */
export interface ChangesetApproveRequest {
  protocol: "rpg-world-forge.studio_protocol";
  protocol_version: 1;
  kind: "request";
  request_id: string;
  method: "changeset.approve";
  params: ChangesetActionParams;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "changesetRejectRequest".
 */
export interface ChangesetRejectRequest {
  protocol: "rpg-world-forge.studio_protocol";
  protocol_version: 1;
  kind: "request";
  request_id: string;
  method: "changeset.reject";
  params: ChangesetActionParams;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "changesetApplyRequest".
 */
export interface ChangesetApplyRequest {
  protocol: "rpg-world-forge.studio_protocol";
  protocol_version: 1;
  kind: "request";
  request_id: string;
  method: "changeset.apply";
  params: ChangesetActionParams;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "jobCreateRequest".
 */
export interface JobCreateRequest {
  protocol: "rpg-world-forge.studio_protocol";
  protocol_version: 1;
  kind: "request";
  request_id: string;
  method: "job.create";
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "jobCreateParams".
   */
  params:
    | AssetReceiptValidateCreateParams
    | AssetpackVerifyCreateParams
    | RuntimeHeadlessCreateParams
    | RuntimeReplayCreateParams;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "assetReceiptValidateCreateParams".
 */
export interface AssetReceiptValidateCreateParams {
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "jobId".
   */
  job_id?: string;
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "workspaceId".
   */
  workspace_id: string;
  operation: "asset.receipt.validate";
  input: AssetReceiptValidateInput;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "assetReceiptValidateInput".
 */
export interface AssetReceiptValidateInput {
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "portableRelativePath".
   */
  receipt: string;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "assetpackVerifyCreateParams".
 */
export interface AssetpackVerifyCreateParams {
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "jobId".
   */
  job_id?: string;
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "workspaceId".
   */
  workspace_id: string;
  operation: "assetpack.verify";
  input: AssetpackVerifyInput;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "assetpackVerifyInput".
 */
export interface AssetpackVerifyInput {
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "portableRelativePath".
   */
  assetpack: string;
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "portableRelativePath".
   */
  worldpack: string;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "runtimeHeadlessCreateParams".
 */
export interface RuntimeHeadlessCreateParams {
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "jobId".
   */
  job_id?: string;
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "workspaceId".
   */
  workspace_id: string;
  operation: "runtime.headless";
  input: RuntimeHeadlessInput;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "runtimeHeadlessInput".
 */
export interface RuntimeHeadlessInput {
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "portableRelativePath".
   */
  worldpack: string;
  ticks: number;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "runtimeReplayCreateParams".
 */
export interface RuntimeReplayCreateParams {
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "jobId".
   */
  job_id?: string;
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "workspaceId".
   */
  workspace_id: string;
  operation: "runtime.replay";
  input: RuntimeReplayInput;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "runtimeReplayInput".
 */
export interface RuntimeReplayInput {
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "portableRelativePath".
   */
  worldpack: string;
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "portableRelativePath".
   */
  replay: string;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "jobCancelRequest".
 */
export interface JobCancelRequest {
  protocol: "rpg-world-forge.studio_protocol";
  protocol_version: 1;
  kind: "request";
  request_id: string;
  method: "job.cancel";
  params: JobCancelParams;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "jobCancelParams".
 */
export interface JobCancelParams {
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "jobId".
   */
  job_id: string;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "legacyResponse".
 */
export interface LegacyResponse {
  protocol: "rpg-world-forge.studio_protocol";
  protocol_version: 1;
  kind: "response";
  request_id: string;
  method: LegacyMethod;
  result: {
    [k: string]: unknown;
  };
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "workspaceOverviewResponse".
 */
export interface WorkspaceOverviewResponse {
  protocol: "rpg-world-forge.studio_protocol";
  protocol_version: 1;
  kind: "response";
  request_id: string;
  method: "workspace.overview";
  result: WorkspaceOverviewResult;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "sourceListResponse".
 */
export interface SourceListResponse {
  protocol: "rpg-world-forge.studio_protocol";
  protocol_version: 1;
  kind: "response";
  request_id: string;
  method: "source.list";
  result: SourceListResult;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "sourceReadResponse".
 */
export interface SourceReadResponse {
  protocol: "rpg-world-forge.studio_protocol";
  protocol_version: 1;
  kind: "response";
  request_id: string;
  method: "source.read";
  result: SourceReadResult;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "assetCatalogListResponse".
 */
export interface AssetCatalogListResponse {
  protocol: "rpg-world-forge.studio_protocol";
  protocol_version: 1;
  kind: "response";
  request_id: string;
  method: "asset.catalog.list";
  result: AssetCatalogListResult;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "assetCatalogInspectResponse".
 */
export interface AssetCatalogInspectResponse {
  protocol: "rpg-world-forge.studio_protocol";
  protocol_version: 1;
  kind: "response";
  request_id: string;
  method: "asset.catalog.inspect";
  result: AssetCatalogInspectResult;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "assetPreviewOpenResponse".
 */
export interface AssetPreviewOpenResponse {
  protocol: "rpg-world-forge.studio_protocol";
  protocol_version: 1;
  kind: "response";
  request_id: string;
  method: "asset.preview.open";
  result: AssetPreviewOpenResult;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "assetPreviewReadResponse".
 */
export interface AssetPreviewReadResponse {
  protocol: "rpg-world-forge.studio_protocol";
  protocol_version: 1;
  kind: "response";
  request_id: string;
  method: "asset.preview.read";
  result: AssetPreviewReadResult;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "assetPreviewCloseResponse".
 */
export interface AssetPreviewCloseResponse {
  protocol: "rpg-world-forge.studio_protocol";
  protocol_version: 1;
  kind: "response";
  request_id: string;
  method: "asset.preview.close";
  result: AssetPreviewCloseResult;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "worldValidateResponse".
 */
export interface WorldValidateResponse {
  protocol: "rpg-world-forge.studio_protocol";
  protocol_version: 1;
  kind: "response";
  request_id: string;
  method: "world.validate";
  result: WorldValidateResult;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "worldAnalyzeResponse".
 */
export interface WorldAnalyzeResponse {
  protocol: "rpg-world-forge.studio_protocol";
  protocol_version: 1;
  kind: "response";
  request_id: string;
  method: "world.analyze";
  result: WorldAnalyzeResult;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "changesetCreateResponse".
 */
export interface ChangesetCreateResponse {
  protocol: "rpg-world-forge.studio_protocol";
  protocol_version: 1;
  kind: "response";
  request_id: string;
  method: "changeset.create";
  result: ChangesetResult;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "changesetGetResponse".
 */
export interface ChangesetGetResponse {
  protocol: "rpg-world-forge.studio_protocol";
  protocol_version: 1;
  kind: "response";
  request_id: string;
  method: "changeset.get";
  result: ChangesetResult;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "changesetListResponse".
 */
export interface ChangesetListResponse {
  protocol: "rpg-world-forge.studio_protocol";
  protocol_version: 1;
  kind: "response";
  request_id: string;
  method: "changeset.list";
  result: ChangesetListResult;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "changesetDiffResponse".
 */
export interface ChangesetDiffResponse {
  protocol: "rpg-world-forge.studio_protocol";
  protocol_version: 1;
  kind: "response";
  request_id: string;
  method: "changeset.diff";
  result: ChangesetDiffResult;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "changesetApproveResponse".
 */
export interface ChangesetApproveResponse {
  protocol: "rpg-world-forge.studio_protocol";
  protocol_version: 1;
  kind: "response";
  request_id: string;
  method: "changeset.approve";
  result: ChangesetResult;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "changesetRejectResponse".
 */
export interface ChangesetRejectResponse {
  protocol: "rpg-world-forge.studio_protocol";
  protocol_version: 1;
  kind: "response";
  request_id: string;
  method: "changeset.reject";
  result: ChangesetResult;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "changesetApplyResponse".
 */
export interface ChangesetApplyResponse {
  protocol: "rpg-world-forge.studio_protocol";
  protocol_version: 1;
  kind: "response";
  request_id: string;
  method: "changeset.apply";
  result: ChangesetResult;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "jobCreateResponse".
 */
export interface JobCreateResponse {
  protocol: "rpg-world-forge.studio_protocol";
  protocol_version: 1;
  kind: "response";
  request_id: string;
  method: "job.create";
  result: {
    job: ManagedStudioJobRecordV2;
  };
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "managedJobV2Common".
 */
export interface ManagedStudioJobV2CommonRecord {
  format: "rpg-world-forge.studio_job";
  format_version: 2;
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "jobId".
   */
  job_id: string;
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "workspaceId".
   */
  workspace_id: string;
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "jobOperation".
   */
  operation: "asset.receipt.validate" | "assetpack.verify" | "runtime.headless" | "runtime.replay";
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "jobState".
   */
  state:
    | "queued"
    | "running"
    | "awaiting_approval"
    | "awaiting_user"
    | "paused"
    | "succeeded"
    | "failed"
    | "canceled"
    | "orphaned";
  input:
    AssetReceiptValidateInput | AssetpackVerifyInput | RuntimeHeadlessInput | RuntimeReplayInput;
  result:
    | AssetReceiptValidateResult
    | AssetpackVerifyResult
    | RuntimeHeadlessResult
    | RuntimeReplayResult
    | null;
  error: JobError | null;
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "timestamp".
   */
  created_at: string;
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "timestamp".
   */
  updated_at: string;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "assetReceiptValidateResult".
 */
export interface AssetReceiptValidateResult {
  operation: "asset.receipt.validate";
  valid: boolean;
  issue_count: number;
  issues_truncated: boolean;
  /**
   * @maxItems 256
   */
  issues: ReceiptIssue[];
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "receiptIssue".
 */
export interface ReceiptIssue {
  path: string;
  message: string;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "assetpackVerifyResult".
 */
export interface AssetpackVerifyResult {
  operation: "assetpack.verify";
  valid: true;
  world_id: string;
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "sha256".
   */
  world_content_hash: string;
  target_id: string;
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "sha256".
   */
  target_hash: string;
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "sha256".
   */
  content_hash: string;
  asset_count: number;
  file_count: number;
  binding_count: number;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "runtimeHeadlessResult".
 */
export interface RuntimeHeadlessResult {
  operation: "runtime.headless";
  world_id: string;
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "sha256".
   */
  world_content_hash: string;
  ticks: number;
  state_tick: number;
  absolute_minute: number;
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "sha256".
   */
  state_digest: string;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "runtimeReplayResult".
 */
export interface RuntimeReplayResult {
  operation: "runtime.replay";
  world_id: string;
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "sha256".
   */
  world_content_hash: string;
  action_count: number;
  state_tick: number;
  absolute_minute: number;
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "sha256".
   */
  state_digest: string;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "jobError".
 */
export interface JobError {
  code: "execution_failed" | "invalid_workspace" | "timeout" | "worker_crashed" | "worker_protocol";
  message: string;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "assetReceiptValidateOperation".
 */
export interface AssetReceiptValidateOperation {
  operation: "asset.receipt.validate";
  input: AssetReceiptValidateInput;
  result: AssetReceiptValidateResult | null;
  [k: string]: unknown;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "assetpackVerifyOperation".
 */
export interface AssetpackVerifyOperation {
  operation: "assetpack.verify";
  input: AssetpackVerifyInput;
  result: AssetpackVerifyResult | null;
  [k: string]: unknown;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "runtimeHeadlessOperation".
 */
export interface RuntimeHeadlessOperation {
  operation: "runtime.headless";
  input: RuntimeHeadlessInput;
  result: RuntimeHeadlessResult | null;
  [k: string]: unknown;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "runtimeReplayOperation".
 */
export interface RuntimeReplayOperation {
  operation: "runtime.replay";
  input: RuntimeReplayInput;
  result: RuntimeReplayResult | null;
  [k: string]: unknown;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "jobCancelResponse".
 */
export interface JobCancelResponse {
  protocol: "rpg-world-forge.studio_protocol";
  protocol_version: 1;
  kind: "response";
  request_id: string;
  method: "job.cancel";
  result: {
    job: ForgeStudioDurableJobRecordV2WithV1ReadCompatibility;
  };
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "legacyJobV1".
 */
export interface LegacyStudioJobRecordV1 {
  format: "rpg-world-forge.studio_job";
  format_version: 1;
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "jobId".
   */
  job_id: string;
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "workspaceId".
   */
  workspace_id: string;
  operation: string;
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "jobState".
   */
  state:
    | "queued"
    | "running"
    | "awaiting_approval"
    | "awaiting_user"
    | "paused"
    | "succeeded"
    | "failed"
    | "canceled"
    | "orphaned";
  input: {
    [k: string]: unknown;
  };
  result: {
    [k: string]: unknown;
  } | null;
  error: {
    [k: string]: unknown;
  } | null;
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "timestamp".
   */
  created_at: string;
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "timestamp".
   */
  updated_at: string;
}

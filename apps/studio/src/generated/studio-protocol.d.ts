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
  | "world.validate"
  | "world.analyze"
  | "events.list"
  | "changeset.create"
  | "changeset.get"
  | "changeset.list"
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
  | "changeset.create"
  | "changeset.get"
  | "changeset.list"
  | "changeset.approve"
  | "changeset.reject"
  | "changeset.apply"
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
 * via the `definition` "request".
 */
export type Request =
  | LegacyRequest
  | WorkspaceScopedAuthoringRequest
  | SourceReadRequest
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
  | WorldValidateResponse
  | WorldAnalyzeResponse
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

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
  | "job.create"
  | "job.get"
  | "job.list"
  | "job.transition"
  | "job.cancel";
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
export type Request = LegacyRequest | WorkspaceScopedAuthoringRequest | SourceReadRequest;
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
  | WorldAnalyzeResponse;
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

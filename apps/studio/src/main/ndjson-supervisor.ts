import {
    spawn,
    type ChildProcess,
    type ChildProcessWithoutNullStreams,
} from "node:child_process";
import path from "node:path";

import type {
    AssetCatalogInspectRequest as StudioAssetCatalogInspectRequest,
    AssetCatalogInspectResponse as StudioAssetCatalogInspectResponse,
    AssetCatalogListRequest as StudioAssetCatalogListRequest,
    AssetCatalogListResponse as StudioAssetCatalogListResponse,
    AssetPreviewCloseRequest as StudioAssetPreviewCloseRequest,
    AssetPreviewCloseResponse as StudioAssetPreviewCloseResponse,
    AssetPreviewOpenRequest as StudioAssetPreviewOpenRequest,
    AssetPreviewOpenResponse as StudioAssetPreviewOpenResponse,
    AssetPreviewReadRequest as StudioAssetPreviewReadRequest,
    AssetPreviewReadResponse as StudioAssetPreviewReadResponse,
    ChangesetApplyRequest as StudioChangesetApplyRequest,
    ChangesetApplyResponse as StudioChangesetApplyResponse,
    ChangesetApproveRequest as StudioChangesetApproveRequest,
    ChangesetApproveResponse as StudioChangesetApproveResponse,
    ChangesetCreateRequest as StudioChangesetCreateRequest,
    ChangesetCreateResponse as StudioChangesetCreateResponse,
    ChangesetDiffRequest as StudioChangesetDiffRequest,
    ChangesetDiffResponse as StudioChangesetDiffResponse,
    ChangesetGetRequest as StudioChangesetGetRequest,
    ChangesetGetResponse as StudioChangesetGetResponse,
    ChangesetListRequest as StudioChangesetListRequest,
    ChangesetListResponse as StudioChangesetListResponse,
    ChangesetRejectRequest as StudioChangesetRejectRequest,
    ChangesetRejectResponse as StudioChangesetRejectResponse,
    Error as StudioErrorEnvelope,
    Event as StudioEventEnvelope,
    JobCancelRequest as StudioJobCancelRequest,
    JobCancelResponse as StudioJobCancelResponse,
    JobCreateRequest as StudioJobCreateRequest,
    JobCreateResponse as StudioJobCreateResponse,
    LegacyResponse as StudioLegacyResponse,
    Method as StudioMethod,
    Response as StudioResponseEnvelope,
    SourceListResponse as StudioSourceListResponse,
    SourceReadParams as StudioSourceReadParams,
    SourceReadResponse as StudioSourceReadResponse,
    WorkspaceOverviewResponse as StudioWorkspaceOverviewResponse,
    WorkspaceScopedAuthoringMethod as StudioWorkspaceScopedAuthoringMethod,
    WorkspaceScopedParams as StudioWorkspaceScopedParams,
    WorldAnalyzeResponse as StudioWorldAnalyzeResponse,
    WorldValidateResponse as StudioWorldValidateResponse,
} from "../generated/studio-protocol";
import type {
    ErrorEnvelope as StudioV2ErrorEnvelope,
    GrantCreateParams as StudioV2GrantCreateParams,
    GrantIdParams as StudioV2GrantIdParams,
    JobCreateParams as StudioV2JobCreateParams,
    JobIdParams as StudioV2JobIdParams,
    JobListParams as StudioV2JobListParams,
    JobRecoverParams as StudioV2JobRecoverParams,
    Method as StudioV2Method,
    Response as StudioV2ResponseEnvelope,
} from "../generated/studio-protocol-v2";
import type {
    ChangesetActionParams as StudioV3ChangesetActionParams,
    ChangesetApplyParams as StudioV3ChangesetApplyParams,
    ChangesetApplyResult as StudioV3ChangesetApplyResult,
    ChangesetCreateParams as StudioV3ChangesetCreateParams,
    ChangesetDiffResult as StudioV3ChangesetDiffResult,
    ChangesetIdParams as StudioV3ChangesetIdParams,
    ChangesetListParams as StudioV3ChangesetListParams,
    ChangesetListResult as StudioV3ChangesetListResult,
    ChangesetRecoverParams as StudioV3ChangesetRecoverParams,
    ChangesetRecoverResult as StudioV3ChangesetRecoverResult,
    ChangesetResult as StudioV3ChangesetResult,
    DocumentListResult as StudioV3DocumentListResult,
    DocumentReadParams as StudioV3DocumentReadParams,
    DocumentReadResult as StudioV3DocumentReadResult,
    ErrorEnvelope as StudioV3ErrorEnvelope,
    GrantCreateParams as StudioV3GeneratedGrantCreateParams,
    GrantIdParams as StudioV3GrantIdParams,
    GrantMutationParams as StudioV3GrantMutationParams,
    GrantResult as StudioV3GrantResult,
    InitializeResult as StudioV3InitializeResult,
    Method as StudioV3Method,
    PhaseCompleteParams as StudioV3PhaseCompleteParams,
    PhaseReadParams as StudioV3PhaseReadParams,
    PhaseReadResult as StudioV3PhaseReadResult,
    PhaseReportParams as StudioV3PhaseReportParams,
    PhaseReopenParams as StudioV3PhaseReopenParams,
    PhaseValidateResult as StudioV3PhaseValidateResult,
    ReadinessResult as StudioV3ReadinessResult,
    RevisionParams as StudioV3RevisionParams,
    Sha256 as StudioV3Sha256,
    Response as StudioV3ResponseEnvelope,
    WorkflowResult as StudioV3WorkflowResult,
    WorkflowReconcileParams as StudioV3WorkflowReconcileParams,
    WorkspaceCreateParams as StudioV3WorkspaceCreateParams,
    WorkspaceIdParams as StudioV3WorkspaceIdParams,
    WorkspaceListResult as StudioV3WorkspaceListResult,
    WorkspaceOpenResult as StudioV3WorkspaceOpenResult,
    WorkspaceRecoverParams as StudioV3WorkspaceRecoverParams,
    WorkspaceRecoverResult as StudioV3WorkspaceRecoverResult,
    WorkspaceRegisterParams as StudioV3WorkspaceRegisterParams,
    WorkspaceResult as StudioV3WorkspaceResult,
    WorkspaceWorkflowResult as StudioV3WorkspaceWorkflowResult,
} from "../generated/studio-protocol-v3";
import type {
    ArtifactInspectParams as StudioV4ArtifactInspectParams,
    ArtifactInspectResult as StudioV4ArtifactInspectResult,
    ArtifactListParams as StudioV4ArtifactListParams,
    ArtifactListResult as StudioV4ArtifactListResult,
    EmptyParams as StudioV4EmptyParams,
    ErrorEnvelope as StudioV4ErrorEnvelope,
    EvidenceInspectParams as StudioV4EvidenceInspectParams,
    EvidenceInspectResult as StudioV4EvidenceInspectResult,
    CreationPreviewCloseParams as StudioV4CreationPreviewCloseParams,
    CreationPreviewCloseResult as StudioV4CreationPreviewCloseResult,
    CreationPreviewOpenParams as StudioV4CreationPreviewOpenParams,
    CreationPreviewOpenResult as StudioV4CreationPreviewOpenResult,
    CreationPreviewReadParams as StudioV4CreationPreviewReadParams,
    CreationPreviewReadResult as StudioV4CreationPreviewReadResult,
    EventListParams as StudioV4EventListParams,
    EventListResult as StudioV4EventListResult,
    InitializeResult as StudioV4InitializeResult,
    JobCancelParams as StudioV4JobCancelParams,
    JobCreateParams as StudioV4JobCreateParams,
    JobGetParams as StudioV4JobGetParams,
    JobListParams as StudioV4JobListParams,
    JobListResult as StudioV4JobListResult,
    JobRecoverParams as StudioV4JobRecoverParams,
    JobResult as StudioV4JobResult,
    Method as StudioV4Method,
    OutputGrantCreateParams as StudioV4OutputGrantCreateParams,
    OutputGrantGetParams as StudioV4OutputGrantGetParams,
    OutputGrantResult as StudioV4OutputGrantResult,
    OutputGrantRevokeParams as StudioV4OutputGrantRevokeParams,
    Response as StudioV4ResponseEnvelope,
} from "../generated/studio-protocol-v4";
import type {
    ArtifactInspectParams as StudioV5ArtifactInspectParams,
    ArtifactInspectResult as StudioV5ArtifactInspectResult,
    ArtifactListParams as StudioV5ArtifactListParams,
    ArtifactListResult as StudioV5ArtifactListResult,
    EmptyParams as StudioV5EmptyParams,
    ErrorEnvelope as StudioV5ErrorEnvelope,
    EvidenceInspectParams as StudioV5EvidenceInspectParams,
    EvidenceInspectResult as StudioV5EvidenceInspectResult,
    CreationPreviewCloseParams as StudioV5CreationPreviewCloseParams,
    CreationPreviewCloseResult as StudioV5CreationPreviewCloseResult,
    CreationPreviewOpenParams as StudioV5CreationPreviewOpenParams,
    CreationPreviewOpenResult as StudioV5CreationPreviewOpenResult,
    CreationPreviewReadParams as StudioV5CreationPreviewReadParams,
    CreationPreviewReadResult as StudioV5CreationPreviewReadResult,
    EventListParams as StudioV5EventListParams,
    EventListResult as StudioV5EventListResult,
    InitializeResult as StudioV5InitializeResult,
    JobCancelParams as StudioV5JobCancelParams,
    JobCreateParams as StudioV5JobCreateParams,
    JobGetParams as StudioV5JobGetParams,
    JobListParams as StudioV5JobListParams,
    JobListResult as StudioV5JobListResult,
    JobRecoverParams as StudioV5JobRecoverParams,
    JobResult as StudioV5JobResult,
    Method as StudioV5Method,
    OutputGrantCreateParams as StudioV5OutputGrantCreateParams,
    OutputGrantGetParams as StudioV5OutputGrantGetParams,
    OutputGrantListParams as StudioV5OutputGrantListParams,
    OutputGrantListResult as StudioV5OutputGrantListResult,
    OutputGrantResult as StudioV5OutputGrantResult,
    OutputGrantRevokeParams as StudioV5OutputGrantRevokeParams,
    Response as StudioV5ResponseEnvelope,
} from "../generated/studio-protocol-v5";
import {
    describeProtocolErrors,
    validateStudioEnvelope,
} from "./protocol-validator";

export const DEFAULT_MAX_NDJSON_LINE_BYTES = 1024 * 1024;
export const DEFAULT_MAX_STDERR_BYTES = 64 * 1024;
export const DEFAULT_MAX_PENDING_REQUESTS = 128;
export const DEFAULT_MAX_OUTSTANDING_REQUEST_BYTES = 8 * 1024 * 1024;
export const DEFAULT_MAX_IGNORED_REQUEST_IDS = 1024;
export const DEFAULT_GRACEFUL_STOP_TIMEOUT_MS = 12_000;
export const DEFAULT_TERMINATE_STOP_TIMEOUT_MS = 2_000;
export const DEFAULT_KILL_STOP_TIMEOUT_MS = 2_000;

const PROTOCOL = "rpg-world-forge.studio_protocol" as const;
const PROTOCOL_VERSION = 1 as const;
export type StudioProtocolVersion = 1 | 2 | 3 | 4 | 5;

export interface FixedSpawnSpec {
    executable: string;
    args: readonly string[];
    cwd?: string;
    env: Readonly<Record<string, string>>;
}

export type TransportState =
    "stopped" | "starting" | "running" | "stopping" | "crashed";

type ProcessTreeTerminationPlan =
    "posix-process-group" | "windows-taskkill" | "fail-closed";
type ProcessTreeTerminationStage = "none" | "terminate" | "kill";

interface OwnedProcessTree {
    generation: number;
    pid: number;
    processGroupId: number | null;
    platform: NodeJS.Platform;
}

export type TransportEvent =
    | {
          type: "state";
          state: TransportState;
          pid: number | null;
          message: string;
      }
    | { type: "event"; envelope: StudioEventEnvelope }
    | { type: "stderr"; text: string };

export class StudioTransportError extends Error {
    public constructor(message: string) {
        super(message);
        this.name = "StudioTransportError";
    }
}

export class StudioProtocolError extends StudioTransportError {
    public constructor(message: string, options?: ErrorOptions) {
        super(message);
        this.name = "StudioProtocolError";
        if (options?.cause !== undefined) {
            this.cause = options.cause;
        }
    }
}

export class StudioRequestTimeoutError extends StudioTransportError {
    public constructor(requestId: string) {
        super(`Studio request ${requestId} timed out`);
        this.name = "StudioRequestTimeoutError";
    }
}

export class StudioRequestCancelledError extends StudioTransportError {
    public constructor(requestId: string) {
        super(`Studio request ${requestId} was cancelled`);
        this.name = "StudioRequestCancelledError";
    }
}

export class StudioOverloadError extends StudioTransportError {
    public constructor(message: string) {
        super(message);
        this.name = "StudioOverloadError";
    }
}

class BoundedLineDecoder {
    readonly #decoder = new TextDecoder("utf-8", { fatal: true });
    readonly #maxLineBytes: number;
    #pending = Buffer.alloc(0);

    public constructor(maxLineBytes: number) {
        if (!Number.isSafeInteger(maxLineBytes) || maxLineBytes < 32) {
            throw new TypeError(
                "maxLineBytes must be an integer of at least 32",
            );
        }
        this.#maxLineBytes = maxLineBytes;
    }

    public push(chunk: Buffer): string[] {
        const lines: string[] = [];
        let offset = 0;
        while (offset < chunk.length) {
            const newline = chunk.indexOf(0x0a, offset);
            const end = newline === -1 ? chunk.length : newline;
            const segment = chunk.subarray(offset, end);
            if (this.#pending.length + segment.length > this.#maxLineBytes) {
                throw new StudioProtocolError(
                    `Studio output exceeds the ${this.#maxLineBytes}-byte NDJSON line limit`,
                );
            }
            if (segment.length > 0) {
                this.#pending = Buffer.concat([this.#pending, segment]);
            }
            if (newline === -1) {
                break;
            }
            lines.push(this.#decodePending());
            this.#pending = Buffer.alloc(0);
            offset = newline + 1;
        }
        return lines;
    }

    public finish(): string[] {
        if (this.#pending.length === 0) {
            return [];
        }
        const finalLine = this.#decodePending();
        this.#pending = Buffer.alloc(0);
        return [finalLine];
    }

    #decodePending(): string {
        let payload = this.#pending;
        if (payload.at(-1) === 0x0d) {
            payload = payload.subarray(0, -1);
        }
        try {
            return this.#decoder.decode(payload);
        } catch (error) {
            throw new StudioProtocolError("Studio output is not valid UTF-8", {
                cause: error,
            });
        }
    }
}

class BoundedTextTail {
    readonly #maxBytes: number;
    #buffer = Buffer.alloc(0);

    public constructor(maxBytes: number) {
        this.#maxBytes = maxBytes;
    }

    public append(chunk: Buffer): string {
        const combined = Buffer.concat([this.#buffer, chunk]);
        this.#buffer =
            combined.length <= this.#maxBytes
                ? combined
                : combined.subarray(combined.length - this.#maxBytes);
        return this.#buffer.toString("utf8");
    }

    public text(): string {
        return this.#buffer.toString("utf8");
    }
}

interface PendingRequest {
    method:
        | StudioMethod
        | StudioV2Method
        | StudioV3Method
        | StudioV4Method
        | StudioV5Method;
    protocolVersion: StudioProtocolVersion;
    resolve: (
        envelope:
            | StudioReplyEnvelope
            | StudioV2ReplyEnvelope
            | StudioV3ReplyEnvelope
            | StudioV4ReplyEnvelope
            | StudioV5ReplyEnvelope,
    ) => void;
    reject: (error: Error) => void;
    timer: NodeJS.Timeout;
    payload: Buffer;
    payloadBytes: number;
    writeState: "queued" | "writing" | "sent";
}

export type StudioRequestParams<M extends StudioMethod> = M extends "job.create"
    ? StudioJobCreateRequest["params"]
    : M extends "job.cancel"
      ? StudioJobCancelRequest["params"]
      : M extends "source.read"
        ? StudioSourceReadParams
        : M extends "changeset.create"
          ? StudioChangesetCreateRequest["params"]
          : M extends "changeset.get"
            ? StudioChangesetGetRequest["params"]
            : M extends "changeset.list"
              ? StudioChangesetListRequest["params"]
              : M extends "changeset.diff"
                ? StudioChangesetDiffRequest["params"]
                : M extends "changeset.approve"
                  ? StudioChangesetApproveRequest["params"]
                  : M extends "changeset.reject"
                    ? StudioChangesetRejectRequest["params"]
                    : M extends "changeset.apply"
                      ? StudioChangesetApplyRequest["params"]
                      : M extends "asset.catalog.list"
                        ? StudioAssetCatalogListRequest["params"]
                        : M extends "asset.catalog.inspect"
                          ? StudioAssetCatalogInspectRequest["params"]
                          : M extends "asset.preview.open"
                            ? StudioAssetPreviewOpenRequest["params"]
                            : M extends "asset.preview.read"
                              ? StudioAssetPreviewReadRequest["params"]
                              : M extends "asset.preview.close"
                                ? StudioAssetPreviewCloseRequest["params"]
                                : M extends StudioWorkspaceScopedAuthoringMethod
                                  ? StudioWorkspaceScopedParams
                                  : Record<string, unknown>;

export type StudioSuccessForMethod<M extends StudioMethod> =
    M extends "workspace.overview"
        ? StudioWorkspaceOverviewResponse
        : M extends "source.list"
          ? StudioSourceListResponse
          : M extends "source.read"
            ? StudioSourceReadResponse
            : M extends "changeset.create"
              ? StudioChangesetCreateResponse
              : M extends "changeset.get"
                ? StudioChangesetGetResponse
                : M extends "changeset.list"
                  ? StudioChangesetListResponse
                  : M extends "changeset.diff"
                    ? StudioChangesetDiffResponse
                    : M extends "changeset.approve"
                      ? StudioChangesetApproveResponse
                      : M extends "changeset.reject"
                        ? StudioChangesetRejectResponse
                        : M extends "changeset.apply"
                          ? StudioChangesetApplyResponse
                          : M extends "world.validate"
                            ? StudioWorldValidateResponse
                            : M extends "world.analyze"
                              ? StudioWorldAnalyzeResponse
                              : M extends "job.create"
                                ? StudioJobCreateResponse
                                : M extends "job.cancel"
                                  ? StudioJobCancelResponse
                                  : M extends "asset.catalog.list"
                                    ? StudioAssetCatalogListResponse
                                    : M extends "asset.catalog.inspect"
                                      ? StudioAssetCatalogInspectResponse
                                      : M extends "asset.preview.open"
                                        ? StudioAssetPreviewOpenResponse
                                        : M extends "asset.preview.read"
                                          ? StudioAssetPreviewReadResponse
                                          : M extends "asset.preview.close"
                                            ? StudioAssetPreviewCloseResponse
                                            : StudioLegacyResponse;

export type StudioReplyForMethod<M extends StudioMethod> =
    StudioSuccessForMethod<M> | StudioErrorEnvelope;

export type StudioV2RequestParams<M extends StudioV2Method> =
    M extends "external_grant.create"
        ? StudioV2GrantCreateParams
        : M extends "external_grant.get" | "external_grant.revoke"
          ? StudioV2GrantIdParams
          : M extends "job.create"
            ? StudioV2JobCreateParams
            : M extends "job.get" | "job.cancel"
              ? StudioV2JobIdParams
              : M extends "job.list"
                ? StudioV2JobListParams
                : M extends "job.recover"
                  ? StudioV2JobRecoverParams
                  : Record<string, never>;

export type StudioV2ReplyEnvelope =
    StudioV2ResponseEnvelope | StudioV2ErrorEnvelope;
export type StudioV3ReplyEnvelope =
    StudioV3ResponseEnvelope | StudioV3ErrorEnvelope;
type StudioV3GrantCreateBaseParams = Pick<
    StudioV3GeneratedGrantCreateParams,
    "grant_id" | "display_name" | "path"
>;
type StudioV3GrantCreateParams = StudioV3GrantCreateBaseParams &
    (
        | { role: "existing_root"; expected_project_hash: StudioV3Sha256 }
        | { role: "new_target"; expected_project_hash: null }
    );
export type StudioV3RequestParams<M extends StudioV3Method> = M extends
    "service.initialize" | "creation_workspace.list"
    ? Record<string, never>
    : M extends "creation_root_grant.create"
      ? StudioV3GrantCreateParams
      : M extends "creation_root_grant.get"
        ? StudioV3GrantIdParams
        : M extends "creation_root_grant.revoke"
          ? StudioV3GrantMutationParams
          : M extends "creation_workspace.create"
            ? StudioV3WorkspaceCreateParams
            : M extends "creation_workspace.recover"
              ? StudioV3WorkspaceRecoverParams
              : M extends "creation_workspace.register"
                ? StudioV3WorkspaceRegisterParams
                : M extends
                        | "creation_workspace.get"
                        | "creation_workspace.open"
                        | "creation_workflow.get"
                        | "creation_readiness.inspect"
                  ? StudioV3WorkspaceIdParams
                  : M extends "creation_document.list"
                    ? StudioV3RevisionParams
                    : M extends "creation_document.read"
                      ? StudioV3DocumentReadParams
                      : M extends "creation_changeset.create"
                        ? StudioV3ChangesetCreateParams
                        : M extends
                                | "creation_changeset.get"
                                | "creation_changeset.diff"
                          ? StudioV3ChangesetIdParams
                          : M extends "creation_changeset.list"
                            ? StudioV3ChangesetListParams
                            : M extends
                                    | "creation_changeset.approve"
                                    | "creation_changeset.reject"
                              ? StudioV3ChangesetActionParams
                              : M extends "creation_changeset.apply"
                                ? StudioV3ChangesetApplyParams
                                : M extends "creation_changeset.recover"
                                  ? StudioV3ChangesetRecoverParams
                                  : M extends "creation_workflow.reconcile"
                                    ? StudioV3WorkflowReconcileParams
                                    : M extends "creation_phase.read"
                                      ? StudioV3PhaseReadParams
                                      : M extends "creation_phase.validate"
                                        ? StudioV3PhaseReportParams
                                        : M extends "creation_phase.complete"
                                          ? StudioV3PhaseCompleteParams
                                          : M extends "creation_phase.reopen"
                                            ? StudioV3PhaseReopenParams
                                            : never;
export type StudioV3ResultForMethod<M extends StudioV3Method> =
    M extends "service.initialize"
        ? StudioV3InitializeResult
        : M extends
                | "creation_root_grant.create"
                | "creation_root_grant.get"
                | "creation_root_grant.revoke"
          ? StudioV3GrantResult
          : M extends
                  | "creation_workspace.create"
                  | "creation_workspace.register"
                  | "creation_workspace.get"
            ? StudioV3WorkspaceResult
            : M extends "creation_workspace.recover"
              ? StudioV3WorkspaceRecoverResult
              : M extends "creation_workspace.list"
                ? StudioV3WorkspaceListResult
                : M extends "creation_workspace.open"
                  ? StudioV3WorkspaceOpenResult
                  : M extends "creation_document.list"
                    ? StudioV3DocumentListResult
                    : M extends "creation_document.read"
                      ? StudioV3DocumentReadResult
                      : M extends "creation_workflow.get"
                        ? StudioV3WorkflowResult
                        : M extends
                                | "creation_changeset.create"
                                | "creation_changeset.get"
                                | "creation_changeset.approve"
                                | "creation_changeset.reject"
                          ? StudioV3ChangesetResult
                          : M extends "creation_changeset.list"
                            ? StudioV3ChangesetListResult
                            : M extends "creation_changeset.diff"
                              ? StudioV3ChangesetDiffResult
                              : M extends "creation_changeset.apply"
                                ? StudioV3ChangesetApplyResult
                                : M extends "creation_changeset.recover"
                                  ? StudioV3ChangesetRecoverResult
                                  : M extends
                                          | "creation_workflow.reconcile"
                                          | "creation_phase.complete"
                                          | "creation_phase.reopen"
                                    ? StudioV3WorkspaceWorkflowResult
                                    : M extends "creation_phase.read"
                                      ? StudioV3PhaseReadResult
                                      : M extends "creation_phase.validate"
                                        ? StudioV3PhaseValidateResult
                                        : M extends "creation_readiness.inspect"
                                          ? StudioV3ReadinessResult
                                          : never;
export type StudioV3SuccessForMethod<M extends StudioV3Method> =
    StudioV3ResponseEnvelope & {
        protocol: "rpg-world-forge.studio_protocol";
        protocol_version: 3;
        kind: "response";
        request_id: string;
        method: M;
        result: StudioV3ResultForMethod<M>;
    };
export type StudioV3ReplyForMethod<M extends StudioV3Method> =
    StudioV3SuccessForMethod<M> | StudioV3ErrorEnvelope;
export type StudioV4ReplyEnvelope =
    StudioV4ResponseEnvelope | StudioV4ErrorEnvelope;
export type StudioV4RequestParams<M extends StudioV4Method> =
    M extends "service.initialize"
        ? StudioV4EmptyParams
        : M extends "creation_artifact.list"
          ? StudioV4ArtifactListParams
          : M extends "creation_artifact.inspect"
            ? StudioV4ArtifactInspectParams
            : M extends "creation_evidence.inspect"
              ? StudioV4EvidenceInspectParams
              : M extends "creation_output_grant.create"
                ? StudioV4OutputGrantCreateParams
                : M extends "creation_output_grant.get"
                  ? StudioV4OutputGrantGetParams
                  : M extends "creation_output_grant.revoke"
                    ? StudioV4OutputGrantRevokeParams
                    : M extends "creation_job.create"
                      ? StudioV4JobCreateParams
                      : M extends "creation_job.get"
                        ? StudioV4JobGetParams
                        : M extends "creation_job.list"
                          ? StudioV4JobListParams
                          : M extends "creation_job.cancel"
                            ? StudioV4JobCancelParams
                            : M extends "creation_job.recover"
                              ? StudioV4JobRecoverParams
                              : M extends "creation_event.list"
                                ? StudioV4EventListParams
                                : M extends "creation_preview.open"
                                  ? StudioV4CreationPreviewOpenParams
                                  : M extends "creation_preview.read"
                                    ? StudioV4CreationPreviewReadParams
                                    : M extends "creation_preview.close"
                                      ? StudioV4CreationPreviewCloseParams
                                      : never;
export type StudioV4ResultForMethod<M extends StudioV4Method> =
    M extends "service.initialize"
        ? StudioV4InitializeResult
        : M extends "creation_artifact.list"
          ? StudioV4ArtifactListResult
          : M extends "creation_artifact.inspect"
            ? StudioV4ArtifactInspectResult
            : M extends "creation_evidence.inspect"
              ? StudioV4EvidenceInspectResult
              : M extends
                      | "creation_output_grant.create"
                      | "creation_output_grant.get"
                      | "creation_output_grant.revoke"
                ? StudioV4OutputGrantResult
                : M extends
                        | "creation_job.create"
                        | "creation_job.get"
                        | "creation_job.cancel"
                        | "creation_job.recover"
                  ? StudioV4JobResult
                  : M extends "creation_job.list"
                    ? StudioV4JobListResult
                    : M extends "creation_event.list"
                      ? StudioV4EventListResult
                      : M extends "creation_preview.open"
                        ? StudioV4CreationPreviewOpenResult
                        : M extends "creation_preview.read"
                          ? StudioV4CreationPreviewReadResult
                          : M extends "creation_preview.close"
                            ? StudioV4CreationPreviewCloseResult
                            : never;
export type StudioV4SuccessForMethod<M extends StudioV4Method> =
    StudioV4ResponseEnvelope & {
        protocol: "rpg-world-forge.studio_protocol";
        protocol_version: 4;
        kind: "response";
        request_id: string;
        method: M;
        result: StudioV4ResultForMethod<M>;
    };
export type StudioV4ReplyForMethod<M extends StudioV4Method> =
    StudioV4SuccessForMethod<M> | StudioV4ErrorEnvelope;
export type StudioV5ReplyEnvelope =
    StudioV5ResponseEnvelope | StudioV5ErrorEnvelope;
export type StudioV5RequestParams<M extends StudioV5Method> =
    M extends "service.initialize"
        ? StudioV5EmptyParams
        : M extends "creation_artifact.list"
          ? StudioV5ArtifactListParams
          : M extends "creation_artifact.inspect"
            ? StudioV5ArtifactInspectParams
            : M extends "creation_evidence.inspect"
              ? StudioV5EvidenceInspectParams
              : M extends "creation_output_grant.create"
                ? StudioV5OutputGrantCreateParams
                : M extends "creation_output_grant.get"
                  ? StudioV5OutputGrantGetParams
                  : M extends "creation_output_grant.list"
                    ? StudioV5OutputGrantListParams
                    : M extends "creation_output_grant.revoke"
                      ? StudioV5OutputGrantRevokeParams
                      : M extends "creation_job.create"
                        ? StudioV5JobCreateParams
                        : M extends "creation_job.get"
                          ? StudioV5JobGetParams
                          : M extends "creation_job.list"
                            ? StudioV5JobListParams
                            : M extends "creation_job.cancel"
                              ? StudioV5JobCancelParams
                              : M extends "creation_job.recover"
                                ? StudioV5JobRecoverParams
                                : M extends "creation_event.list"
                                  ? StudioV5EventListParams
                                  : M extends "creation_preview.open"
                                    ? StudioV5CreationPreviewOpenParams
                                    : M extends "creation_preview.read"
                                      ? StudioV5CreationPreviewReadParams
                                      : M extends "creation_preview.close"
                                        ? StudioV5CreationPreviewCloseParams
                                        : never;
export type StudioV5ResultForMethod<M extends StudioV5Method> =
    M extends "service.initialize"
        ? StudioV5InitializeResult
        : M extends "creation_artifact.list"
          ? StudioV5ArtifactListResult
          : M extends "creation_artifact.inspect"
            ? StudioV5ArtifactInspectResult
            : M extends "creation_evidence.inspect"
              ? StudioV5EvidenceInspectResult
              : M extends
                      | "creation_output_grant.create"
                      | "creation_output_grant.get"
                      | "creation_output_grant.revoke"
                ? StudioV5OutputGrantResult
                : M extends "creation_output_grant.list"
                  ? StudioV5OutputGrantListResult
                  : M extends
                          | "creation_job.create"
                          | "creation_job.get"
                          | "creation_job.cancel"
                          | "creation_job.recover"
                    ? StudioV5JobResult
                    : M extends "creation_job.list"
                      ? StudioV5JobListResult
                      : M extends "creation_event.list"
                        ? StudioV5EventListResult
                        : M extends "creation_preview.open"
                          ? StudioV5CreationPreviewOpenResult
                          : M extends "creation_preview.read"
                            ? StudioV5CreationPreviewReadResult
                            : M extends "creation_preview.close"
                              ? StudioV5CreationPreviewCloseResult
                              : never;
export type StudioV5SuccessForMethod<M extends StudioV5Method> =
    StudioV5ResponseEnvelope & {
        protocol: "rpg-world-forge.studio_protocol";
        protocol_version: 5;
        kind: "response";
        request_id: string;
        method: M;
        result: StudioV5ResultForMethod<M>;
    };
export type StudioV5ReplyForMethod<M extends StudioV5Method> =
    StudioV5SuccessForMethod<M> | StudioV5ErrorEnvelope;
type StudioReplyEnvelope = StudioResponseEnvelope | StudioErrorEnvelope;

export interface NdjsonSupervisorOptions {
    maxLineBytes?: number;
    maxStderrBytes?: number;
    defaultTimeoutMs?: number;
    maxPendingRequests?: number;
    maxOutstandingRequestBytes?: number;
    maxIgnoredRequestIds?: number;
    gracefulStopTimeoutMs?: number;
    terminateStopTimeoutMs?: number;
    killStopTimeoutMs?: number;
}

export interface TransportDiagnostics {
    pendingRequests: number;
    outstandingRequestBytes: number;
    queuedWrites: number;
    backpressureWaits: number;
    ignoredReplyIds: number;
    rootExited: boolean;
    terminationStage: ProcessTreeTerminationStage;
}

export class NdjsonSupervisor {
    readonly #spec: FixedSpawnSpec;
    readonly #maxLineBytes: number;
    readonly #defaultTimeoutMs: number;
    readonly #maxPendingRequests: number;
    readonly #maxOutstandingRequestBytes: number;
    readonly #maxIgnoredRequestIds: number;
    readonly #gracefulStopTimeoutMs: number;
    readonly #terminateStopTimeoutMs: number;
    readonly #killStopTimeoutMs: number;
    readonly #stderr: BoundedTextTail;
    readonly #listeners = new Set<(event: TransportEvent) => void>();
    readonly #pending = new Map<string, PendingRequest>();
    readonly #ignoredRequestIds = new Set<string>();
    readonly #writeQueue: string[] = [];
    #decoder: BoundedLineDecoder;
    #child: ChildProcessWithoutNullStreams | null = null;
    #state: TransportState = "stopped";
    #expectedStop = false;
    #protocolFailed = false;
    #writing = false;
    #outstandingRequestBytes = 0;
    #backpressureWaits = 0;
    #generation = 0;
    #stopPromise: Promise<void> | null = null;
    #treeOwner: OwnedProcessTree | null = null;
    #rootExitedGeneration: number | null = null;
    #terminationStage: ProcessTreeTerminationStage = "none";

    public constructor(
        spec: FixedSpawnSpec,
        options: NdjsonSupervisorOptions = {},
    ) {
        assertFixedSpawnSpec(spec);
        this.#spec = Object.freeze({
            executable: spec.executable,
            args: Object.freeze([...spec.args]),
            cwd: spec.cwd,
            env: Object.freeze({ ...spec.env }),
        });
        this.#maxLineBytes =
            options.maxLineBytes ?? DEFAULT_MAX_NDJSON_LINE_BYTES;
        this.#defaultTimeoutMs = options.defaultTimeoutMs ?? 10_000;
        this.#maxPendingRequests =
            options.maxPendingRequests ?? DEFAULT_MAX_PENDING_REQUESTS;
        this.#maxOutstandingRequestBytes =
            options.maxOutstandingRequestBytes ??
            DEFAULT_MAX_OUTSTANDING_REQUEST_BYTES;
        this.#maxIgnoredRequestIds =
            options.maxIgnoredRequestIds ?? DEFAULT_MAX_IGNORED_REQUEST_IDS;
        this.#gracefulStopTimeoutMs =
            options.gracefulStopTimeoutMs ?? DEFAULT_GRACEFUL_STOP_TIMEOUT_MS;
        this.#terminateStopTimeoutMs =
            options.terminateStopTimeoutMs ?? DEFAULT_TERMINATE_STOP_TIMEOUT_MS;
        this.#killStopTimeoutMs =
            options.killStopTimeoutMs ?? DEFAULT_KILL_STOP_TIMEOUT_MS;
        assertPositiveLimit(this.#maxPendingRequests, "maxPendingRequests");
        assertPositiveLimit(
            this.#maxOutstandingRequestBytes,
            "maxOutstandingRequestBytes",
        );
        assertPositiveLimit(this.#maxIgnoredRequestIds, "maxIgnoredRequestIds");
        assertBoundedStopTimeout(
            this.#gracefulStopTimeoutMs,
            "gracefulStopTimeoutMs",
        );
        assertBoundedStopTimeout(
            this.#terminateStopTimeoutMs,
            "terminateStopTimeoutMs",
        );
        assertBoundedStopTimeout(this.#killStopTimeoutMs, "killStopTimeoutMs");
        this.#stderr = new BoundedTextTail(
            options.maxStderrBytes ?? DEFAULT_MAX_STDERR_BYTES,
        );
        this.#decoder = new BoundedLineDecoder(this.#maxLineBytes);
    }

    public get state(): TransportState {
        return this.#state;
    }

    public get pid(): number | null {
        return this.#child?.pid ?? null;
    }

    public get stderrTail(): string {
        return this.#stderr.text();
    }

    public get diagnostics(): TransportDiagnostics {
        return {
            pendingRequests: this.#pending.size,
            outstandingRequestBytes: this.#outstandingRequestBytes,
            queuedWrites: this.#writeQueue.length + (this.#writing ? 1 : 0),
            backpressureWaits: this.#backpressureWaits,
            ignoredReplyIds: this.#ignoredRequestIds.size,
            rootExited:
                this.#child !== null &&
                this.#rootExitedGeneration === this.#generation,
            terminationStage: this.#terminationStage,
        };
    }

    public subscribe(listener: (event: TransportEvent) => void): () => void {
        this.#listeners.add(listener);
        return () => this.#listeners.delete(listener);
    }

    public async start(): Promise<void> {
        if (this.#state === "running") {
            return;
        }
        if (this.#child || this.#stopPromise) {
            throw new StudioTransportError(
                "Studio child is already starting or stopping",
            );
        }
        this.#expectedStop = false;
        this.#protocolFailed = false;
        this.#ignoredRequestIds.clear();
        this.#generation += 1;
        this.#treeOwner = null;
        this.#rootExitedGeneration = null;
        this.#terminationStage = "none";
        this.#writing = false;
        this.#decoder = new BoundedLineDecoder(this.#maxLineBytes);
        this.#setState("starting", "Starting Forge Studio service");

        let child: ChildProcessWithoutNullStreams;
        try {
            child = spawn(this.#spec.executable, [...this.#spec.args], {
                cwd: this.#spec.cwd,
                detached: process.platform !== "win32",
                env: { ...this.#spec.env },
                shell: false,
                stdio: ["pipe", "pipe", "pipe"],
                windowsHide: true,
            });
        } catch (error) {
            const failure = new StudioTransportError(
                `Failed to spawn Forge Studio service: ${describeError(error)}`,
            );
            this.#setState("crashed", failure.message);
            throw failure;
        }
        this.#child = child;
        this.#captureTreeOwner(child);
        this.#attachChild(child);

        await new Promise<void>((resolve, reject) => {
            const onSpawn = (): void => {
                cleanup();
                this.#captureTreeOwner(child);
                if (
                    this.#child !== child ||
                    this.#expectedStop ||
                    this.#state === "stopping"
                ) {
                    reject(
                        new StudioTransportError(
                            "Forge Studio service stopped during startup",
                        ),
                    );
                    return;
                }
                this.#setState("running", "Forge Studio service is running");
                resolve();
            };
            const onError = (error: Error): void => {
                cleanup();
                reject(
                    new StudioTransportError(
                        `Failed to spawn Forge Studio service: ${describeError(error)}`,
                    ),
                );
            };
            const cleanup = (): void => {
                child.off("spawn", onSpawn);
                child.off("error", onError);
            };
            child.once("spawn", onSpawn);
            child.once("error", onError);
        });
    }

    #captureTreeOwner(child: ChildProcessWithoutNullStreams): void {
        const pid = child.pid;
        if (!pid || this.#child !== child) {
            return;
        }
        const current = this.#treeOwner;
        if (current?.generation === this.#generation && current.pid === pid) {
            return;
        }
        this.#treeOwner = Object.freeze({
            generation: this.#generation,
            pid,
            processGroupId: process.platform === "win32" ? null : pid,
            platform: process.platform,
        });
    }

    public request<M extends StudioMethod>(
        requestId: string,
        method: M,
        params: StudioRequestParams<M>,
        timeoutMs?: number,
        protocolVersion?: 1,
    ): Promise<StudioReplyForMethod<M>>;
    public request<M extends StudioV2Method>(
        requestId: string,
        method: M,
        params: StudioV2RequestParams<M>,
        timeoutMs: number,
        protocolVersion: 2,
    ): Promise<StudioV2ReplyEnvelope>;
    public request<M extends StudioV4Method>(
        requestId: string,
        method: M,
        params: StudioV4RequestParams<M>,
        timeoutMs: number,
        protocolVersion: 4,
    ): Promise<StudioV4ReplyForMethod<M>>;
    public request<M extends StudioV5Method>(
        requestId: string,
        method: M,
        params: StudioV5RequestParams<M>,
        timeoutMs: number,
        protocolVersion: 5,
    ): Promise<StudioV5ReplyForMethod<M>>;
    public request<M extends StudioV3Method>(
        requestId: string,
        method: M,
        params: StudioV3RequestParams<M>,
        timeoutMs: number,
        protocolVersion: 3,
    ): Promise<StudioV3ReplyForMethod<M>>;
    public async request(
        requestId: string,
        method:
            | StudioMethod
            | StudioV2Method
            | StudioV3Method
            | StudioV4Method
            | StudioV5Method,
        params: object,
        timeoutMs = this.#defaultTimeoutMs,
        protocolVersion: StudioProtocolVersion = PROTOCOL_VERSION,
    ): Promise<
        | StudioReplyEnvelope
        | StudioV2ReplyEnvelope
        | StudioV3ReplyEnvelope
        | StudioV4ReplyEnvelope
        | StudioV5ReplyEnvelope
    > {
        if (this.#state !== "running" || !this.#child) {
            throw new StudioTransportError(
                "Forge Studio service is not running",
            );
        }
        if (
            this.#pending.has(requestId) ||
            this.#ignoredRequestIds.has(requestId)
        ) {
            throw new StudioTransportError(
                `Studio request ID ${requestId} is already in use`,
            );
        }
        if (
            !Number.isSafeInteger(timeoutMs) ||
            timeoutMs < 100 ||
            timeoutMs > 60_000
        ) {
            throw new TypeError(
                "timeoutMs must be an integer from 100 to 60000",
            );
        }
        if (
            protocolVersion !== 1 &&
            protocolVersion !== 2 &&
            protocolVersion !== 3 &&
            protocolVersion !== 4 &&
            protocolVersion !== 5
        ) {
            throw new TypeError("protocolVersion must be 1, 2, 3, 4 or 5");
        }

        const envelope = {
            protocol: PROTOCOL,
            protocol_version: protocolVersion,
            kind: "request",
            request_id: requestId,
            method,
            params,
        };
        if (!validateStudioEnvelope(envelope)) {
            throw new StudioProtocolError(
                `Invalid Studio request: ${describeProtocolErrors()}`,
            );
        }
        const payload = Buffer.from(`${JSON.stringify(envelope)}\n`, "utf8");
        if (payload.length - 1 > this.#maxLineBytes) {
            throw new StudioProtocolError(
                `Studio request exceeds the ${this.#maxLineBytes}-byte NDJSON line limit`,
            );
        }
        if (this.#pending.size >= this.#maxPendingRequests) {
            throw new StudioOverloadError(
                `Studio request limit reached (${this.#maxPendingRequests} pending requests)`,
            );
        }
        if (
            payload.length > this.#maxOutstandingRequestBytes ||
            this.#outstandingRequestBytes >
                this.#maxOutstandingRequestBytes - payload.length
        ) {
            throw new StudioOverloadError(
                `Studio request byte limit reached (${this.#maxOutstandingRequestBytes} outstanding bytes)`,
            );
        }

        return await new Promise<
            | StudioReplyEnvelope
            | StudioV2ReplyEnvelope
            | StudioV3ReplyEnvelope
            | StudioV4ReplyEnvelope
            | StudioV5ReplyEnvelope
        >((resolve, reject) => {
            const timer = setTimeout(() => {
                this.#abandonRequest(
                    requestId,
                    new StudioRequestTimeoutError(requestId),
                );
            }, timeoutMs);
            timer.unref();
            this.#pending.set(requestId, {
                method,
                protocolVersion,
                resolve,
                reject,
                timer,
                payload,
                payloadBytes: payload.length,
                writeState: "queued",
            });
            this.#outstandingRequestBytes += payload.length;
            this.#writeQueue.push(requestId);
            this.#pumpWrites();
        });
    }

    public cancelRequest(requestId: string): boolean {
        return this.#abandonRequest(
            requestId,
            new StudioRequestCancelledError(requestId),
        );
    }

    public stop(): Promise<void> {
        if (this.#stopPromise) {
            return this.#stopPromise;
        }
        const child = this.#child;
        let resolveStop!: () => void;
        let rejectStop!: (error: unknown) => void;
        const operation = new Promise<void>((resolve, reject) => {
            resolveStop = resolve;
            rejectStop = reject;
        });
        const stopping = operation.finally(() => {
            if (this.#stopPromise === stopping) {
                this.#stopPromise = null;
            }
        });
        this.#stopPromise = stopping;
        if (!child) {
            this.#setState("stopped", "Forge Studio service is stopped");
            resolveStop();
            return stopping;
        }
        const generation = this.#generation;
        void this.#stopChild(child, generation).then(resolveStop, rejectStop);
        return stopping;
    }

    async #stopChild(
        child: ChildProcessWithoutNullStreams,
        generation: number,
    ): Promise<void> {
        this.#expectedStop = true;
        this.#setState("stopping", "Stopping Forge Studio service");
        this.#rejectAll(
            new StudioTransportError("Forge Studio service is stopping"),
        );
        this.#endInput(child);

        if (
            await this.#waitForRelease(
                child,
                generation,
                this.#gracefulStopTimeoutMs,
            )
        ) {
            return;
        }
        if (
            await this.#terminateAndWait(
                child,
                generation,
                false,
                this.#terminateStopTimeoutMs,
            )
        ) {
            return;
        }
        if (
            await this.#terminateAndWait(
                child,
                generation,
                true,
                this.#killStopTimeoutMs,
            )
        ) {
            return;
        }

        if (this.#child === child && this.#generation === generation) {
            this.#expectedStop = false;
            const failure = new StudioTransportError(
                "Complete Forge Studio process-tree release could not be proven within the bounded shutdown deadline",
            );
            this.#setState("crashed", failure.message);
            throw failure;
        }
    }

    #endInput(child: ChildProcessWithoutNullStreams): void {
        if (
            this.#child !== child ||
            child.stdin.destroyed ||
            child.stdin.writableEnded
        ) {
            return;
        }
        try {
            child.stdin.end();
        } catch {
            // The bounded process escalation below owns recovery from a failed half-close.
        }
    }

    async #terminateAndWait(
        child: ChildProcessWithoutNullStreams,
        generation: number,
        force: boolean,
        timeoutMs: number,
    ): Promise<boolean> {
        if (this.#child !== child || this.#generation !== generation) {
            return true;
        }
        const deadline = performance.now() + timeoutMs;
        const owner = this.#treeOwner;
        try {
            if (
                !owner ||
                owner.generation !== generation ||
                owner.pid !== child.pid
            ) {
                throw new StudioTransportError(
                    "Forge Studio process-tree ownership is unavailable",
                );
            }
            this.#terminationStage = force ? "kill" : "terminate";
            await terminateChildTree(
                child,
                owner,
                force,
                remainingTimeout(deadline),
            );
        } catch {
            // Continue to the next bounded escalation stage unless the child closes.
        }
        return await this.#waitForRelease(
            child,
            generation,
            remainingTimeout(deadline),
        );
    }

    async #waitForRelease(
        child: ChildProcessWithoutNullStreams,
        generation: number,
        timeoutMs: number,
    ): Promise<boolean> {
        if (this.#child !== child || this.#generation !== generation) {
            return true;
        }
        await waitForClose(child, timeoutMs);
        return this.#child !== child || this.#generation !== generation;
    }

    #pumpWrites(): void {
        if (this.#writing || this.#state !== "running") {
            return;
        }
        const child = this.#child;
        if (!child) {
            return;
        }
        let requestId: string | undefined;
        let pending: PendingRequest | undefined;
        while ((requestId = this.#writeQueue.shift()) !== undefined) {
            pending = this.#pending.get(requestId);
            if (pending) {
                break;
            }
        }
        if (!requestId || !pending) {
            return;
        }

        this.#writing = true;
        pending.writeState = "writing";
        const generation = this.#generation;
        void this.#flushWrite(child, pending.payload)
            .then(() => {
                const current = this.#pending.get(requestId);
                if (current === pending) {
                    current.writeState = "sent";
                }
            })
            .catch((error: unknown) => {
                if (
                    this.#expectedStop ||
                    this.#protocolFailed ||
                    this.#state !== "running" ||
                    this.#child !== child
                ) {
                    return;
                }
                const failure = new StudioTransportError(
                    `Failed to write Studio request: ${describeError(error)}`,
                );
                const current = this.#takePending(requestId);
                current?.reject(failure);
                this.#rejectAll(failure);
                const owner = this.#treeOwner;
                if (
                    owner?.generation === generation &&
                    owner.pid === child.pid
                ) {
                    void terminateChildTree(
                        child,
                        owner,
                        true,
                        DEFAULT_KILL_STOP_TIMEOUT_MS,
                    ).catch(() => undefined);
                }
            })
            .finally(() => {
                if (this.#generation !== generation) {
                    return;
                }
                this.#writing = false;
                this.#pumpWrites();
            });
    }

    async #flushWrite(
        child: ChildProcessWithoutNullStreams,
        payload: Buffer,
    ): Promise<void> {
        await new Promise<void>((resolve, reject) => {
            const stream = child.stdin;
            let callbackDone = false;
            let drainDone = true;
            let settled = false;

            const cleanup = (): void => {
                stream.off("drain", onDrain);
                stream.off("error", onError);
                stream.off("close", onClose);
            };
            const finish = (error?: Error): void => {
                if (settled) {
                    return;
                }
                if (error) {
                    settled = true;
                    cleanup();
                    reject(error);
                    return;
                }
                if (callbackDone && drainDone) {
                    settled = true;
                    cleanup();
                    resolve();
                }
            };
            const onDrain = (): void => {
                drainDone = true;
                finish();
            };
            const onError = (error: Error): void => finish(error);
            const onClose = (): void =>
                finish(new Error("Studio service stdin closed"));

            stream.once("error", onError);
            stream.once("close", onClose);
            const accepted = stream.write(payload, (error?: Error | null) => {
                if (error) {
                    finish(error);
                    return;
                }
                callbackDone = true;
                finish();
            });
            if (!accepted) {
                drainDone = false;
                this.#backpressureWaits += 1;
                stream.once("drain", onDrain);
            }
        });
    }

    #attachChild(child: ChildProcessWithoutNullStreams): void {
        const generation = this.#generation;
        child.stdin.on("error", (error) => {
            if (this.#expectedStop || this.#child !== child) {
                return;
            }
            this.#failProtocol(
                new StudioTransportError(
                    `Forge Studio service stdin failed: ${describeError(error)}`,
                ),
            );
        });
        child.stdout.on("data", (chunk: Buffer) => {
            if (this.#child !== child) {
                return;
            }
            try {
                for (const line of this.#decoder.push(chunk)) {
                    this.#handleLine(line);
                }
            } catch (error) {
                this.#handleOutputFailure(error);
            }
        });
        child.stdout.on("end", () => {
            if (this.#child !== child) {
                return;
            }
            try {
                for (const line of this.#decoder.finish()) {
                    this.#handleLine(line);
                }
            } catch (error) {
                this.#handleOutputFailure(error);
            }
        });
        child.stderr.on("data", (chunk: Buffer) => {
            if (this.#child !== child) {
                return;
            }
            this.#emit({ type: "stderr", text: this.#stderr.append(chunk) });
        });
        child.once("error", (error) => {
            if (!child.pid) {
                this.#finalizeChild(child, `spawn error: ${error.message}`);
                return;
            }
            if (this.#expectedStop || this.#child !== child) {
                return;
            }
            this.#failProtocol(
                new StudioTransportError(
                    `Forge Studio service process failed: ${describeError(error)}`,
                ),
            );
        });
        child.once("exit", () => {
            if (this.#child === child && this.#generation === generation) {
                this.#rootExitedGeneration = generation;
            }
        });
        child.once("close", (code, signal) => {
            const detail = signal
                ? `signal ${signal}`
                : `close code ${String(code)}`;
            this.#finalizeChild(child, detail);
        });
    }

    #finalizeChild(
        child: ChildProcessWithoutNullStreams,
        detail: string,
    ): void {
        if (this.#child !== child) {
            return;
        }
        this.#child = null;
        this.#treeOwner = null;
        this.#rootExitedGeneration = null;
        this.#generation += 1;
        this.#writing = false;
        this.#ignoredRequestIds.clear();
        const expected = this.#expectedStop;
        this.#expectedStop = false;
        if (expected) {
            this.#rejectAll(
                new StudioTransportError("Forge Studio service stopped"),
            );
            this.#setState("stopped", "Forge Studio service is stopped");
            return;
        }
        const message = this.#protocolFailed
            ? `Forge Studio service was terminated after a protocol failure (${detail})`
            : `Forge Studio service exited unexpectedly (${detail})`;
        this.#rejectAll(new StudioTransportError(message));
        this.#setState("crashed", message);
    }

    #handleLine(line: string): void {
        if (this.#expectedStop || this.#state === "stopping") {
            return;
        }
        let value: unknown;
        try {
            value = JSON.parse(line);
        } catch (error) {
            throw new StudioProtocolError(
                "Forge Studio service emitted malformed JSON",
                { cause: error },
            );
        }
        if (!validateStudioEnvelope(value)) {
            throw new StudioProtocolError(
                `Forge Studio service emitted an invalid envelope: ${describeProtocolErrors()}`,
            );
        }
        if (value.kind === "event") {
            this.#emit({ type: "event", envelope: value });
            return;
        }
        if (value.kind === "request") {
            throw new StudioProtocolError(
                "Forge Studio service emitted a request envelope",
            );
        }
        const requestId = value.request_id;
        if (requestId === null) {
            throw new StudioProtocolError(
                "Forge Studio service emitted an uncorrelated reply",
            );
        }
        if (this.#ignoredRequestIds.delete(requestId)) {
            return;
        }
        const pending = this.#pending.get(requestId);
        if (!pending) {
            throw new StudioProtocolError(
                `Forge Studio service emitted an unexpected reply for ${requestId}`,
            );
        }
        if (value.kind === "response" && value.method !== pending.method) {
            throw new StudioProtocolError(
                `Forge Studio service replied to ${requestId} with method ${value.method}; expected ${pending.method}`,
            );
        }
        if (value.protocol_version !== pending.protocolVersion) {
            throw new StudioProtocolError(
                `Forge Studio service replied to ${requestId} with protocol version ${
                    value.protocol_version
                }; expected ${pending.protocolVersion}`,
            );
        }
        this.#takePending(requestId);
        pending.resolve(value);
    }

    #handleOutputFailure(error: unknown): void {
        if (this.#expectedStop || this.#state === "stopping") {
            return;
        }
        this.#failProtocol(error);
    }

    #failProtocol(error: unknown): void {
        if (
            this.#protocolFailed ||
            this.#expectedStop ||
            this.#state === "stopping"
        ) {
            return;
        }
        this.#protocolFailed = true;
        const failure =
            error instanceof StudioTransportError
                ? error
                : new StudioProtocolError(
                      "Forge Studio service protocol failed",
                      { cause: error },
                  );
        this.#rejectAll(failure);
        this.#setState("crashed", failure.message);
        const child = this.#child;
        const owner = this.#treeOwner;
        if (
            child &&
            owner?.generation === this.#generation &&
            owner.pid === child.pid
        ) {
            void terminateChildTree(
                child,
                owner,
                true,
                DEFAULT_KILL_STOP_TIMEOUT_MS,
            ).catch(() => undefined);
        }
    }

    #rejectAll(error: Error): void {
        for (const pending of this.#pending.values()) {
            clearTimeout(pending.timer);
            pending.reject(error);
        }
        this.#pending.clear();
        this.#outstandingRequestBytes = 0;
        this.#writeQueue.length = 0;
    }

    #takePending(requestId: string): PendingRequest | undefined {
        const pending = this.#pending.get(requestId);
        if (!pending) {
            return undefined;
        }
        clearTimeout(pending.timer);
        this.#pending.delete(requestId);
        if (pending.writeState === "queued") {
            const queuedIndex = this.#writeQueue.indexOf(requestId);
            if (queuedIndex !== -1) {
                this.#writeQueue.splice(queuedIndex, 1);
            }
        }
        this.#outstandingRequestBytes -= pending.payloadBytes;
        if (this.#outstandingRequestBytes < 0) {
            this.#outstandingRequestBytes = 0;
        }
        return pending;
    }

    #abandonRequest(requestId: string, error: Error): boolean {
        const pending = this.#takePending(requestId);
        if (!pending) {
            return false;
        }

        if (
            pending.writeState !== "queued" &&
            !this.#rememberIgnoredRequest(requestId)
        ) {
            this.#failProtocol(
                new StudioProtocolError(
                    `Studio ignored-reply capacity reached (${this.#maxIgnoredRequestIds} request IDs)`,
                ),
            );
        }
        pending.reject(error);

        if (pending.writeState === "writing" && !this.#protocolFailed) {
            this.#failProtocol(
                new StudioProtocolError(
                    `Studio request ${requestId} was abandoned while its write was incomplete`,
                ),
            );
        }
        return true;
    }

    #rememberIgnoredRequest(requestId: string): boolean {
        if (this.#ignoredRequestIds.has(requestId)) {
            return true;
        }
        if (this.#ignoredRequestIds.size >= this.#maxIgnoredRequestIds) {
            return false;
        }
        this.#ignoredRequestIds.add(requestId);
        return true;
    }

    #setState(state: TransportState, message: string): void {
        this.#state = state;
        this.#emit({ type: "state", state, pid: this.pid, message });
    }

    #emit(event: TransportEvent): void {
        for (const listener of this.#listeners) {
            listener(event);
        }
    }
}

function assertFixedSpawnSpec(spec: FixedSpawnSpec): void {
    if (!path.isAbsolute(spec.executable) || containsControl(spec.executable)) {
        throw new TypeError(
            "Studio executable must be an absolute path without control characters",
        );
    }
    if (spec.cwd && (!path.isAbsolute(spec.cwd) || containsControl(spec.cwd))) {
        throw new TypeError(
            "Studio cwd must be an absolute path without control characters",
        );
    }
    for (const argument of spec.args) {
        if (containsControl(argument)) {
            throw new TypeError(
                "Studio arguments must not contain control characters",
            );
        }
    }
}

function containsControl(value: string): boolean {
    return [...value].some((character) => {
        const code = character.codePointAt(0);
        return code !== undefined && (code <= 0x1f || code === 0x7f);
    });
}

function assertPositiveLimit(value: number, name: string): void {
    if (!Number.isSafeInteger(value) || value < 1) {
        throw new TypeError(`${name} must be a positive safe integer`);
    }
}

function assertBoundedStopTimeout(value: number, name: string): void {
    if (!Number.isSafeInteger(value) || value < 1 || value > 60_000) {
        throw new TypeError(`${name} must be an integer from 1 to 60000`);
    }
}

function describeError(error: unknown): string {
    return error instanceof Error ? error.message : "unknown process error";
}

async function waitForClose(
    child: ChildProcess,
    timeoutMs: number,
): Promise<void> {
    if (timeoutMs <= 0) {
        return;
    }
    await new Promise<void>((resolve) => {
        const timer = setTimeout(() => {
            cleanup();
            resolve();
        }, timeoutMs);
        timer.unref();
        const onClose = (): void => {
            cleanup();
            resolve();
        };
        const cleanup = (): void => {
            clearTimeout(timer);
            child.off("close", onClose);
        };
        child.once("close", onClose);
    });
}

async function terminateChildTree(
    child: ChildProcessWithoutNullStreams,
    owner: OwnedProcessTree,
    force: boolean,
    timeoutMs: number,
): Promise<void> {
    const rootExited = child.exitCode !== null || child.signalCode !== null;
    const plan = planProcessTreeTermination(owner.platform, rootExited);
    if (plan === "posix-process-group") {
        const processGroupId = owner.processGroupId;
        if (!processGroupId) {
            throw new StudioTransportError(
                "Forge Studio process-group ownership is unavailable",
            );
        }
        try {
            process.kill(-processGroupId, force ? "SIGKILL" : "SIGTERM");
        } catch (error) {
            if ((error as NodeJS.ErrnoException).code !== "ESRCH") {
                throw error;
            }
        }
        return;
    }
    if (plan === "fail-closed") {
        throw new StudioTransportError(
            "Windows Forge Studio tree release could not be proven after the root process exited",
        );
    }

    const systemRoot = process.env.SystemRoot ?? process.env.WINDIR;
    if (!systemRoot || !path.win32.isAbsolute(systemRoot)) {
        throw new StudioTransportError(
            "Windows process-tree termination is unavailable without SystemRoot",
        );
    }
    const taskkill = path.win32.join(systemRoot, "System32", "taskkill.exe");
    await new Promise<void>((resolve, reject) => {
        let settled = false;
        const killer = spawn(
            taskkill,
            ["/PID", String(owner.pid), "/T", ...(force ? ["/F"] : [])],
            {
                shell: false,
                stdio: "ignore",
                windowsHide: true,
            },
        );
        killer.unref();
        const finish = (error?: Error): void => {
            if (settled) {
                return;
            }
            settled = true;
            clearTimeout(timer);
            killer.removeAllListeners();
            if (error) {
                reject(error);
            } else {
                resolve();
            }
        };
        const timer = setTimeout(
            () => {
                killer.kill();
                finish(
                    new StudioTransportError(
                        "Windows process-tree termination timed out",
                    ),
                );
            },
            Math.max(1, timeoutMs),
        );
        timer.unref();
        killer.once("error", (error) => {
            finish(
                new StudioTransportError(
                    `Windows process-tree termination failed: ${describeError(error)}`,
                ),
            );
        });
        killer.once("exit", (code) => {
            finish(
                code === 0
                    ? undefined
                    : new StudioTransportError(
                          `Windows process-tree termination exited with code ${String(code)}`,
                      ),
            );
        });
    });
}

export function planProcessTreeTermination(
    platform: NodeJS.Platform,
    rootExited: boolean,
): ProcessTreeTerminationPlan {
    if (platform !== "win32") {
        return "posix-process-group";
    }
    return rootExited ? "fail-closed" : "windows-taskkill";
}

function remainingTimeout(deadline: number): number {
    return Math.max(0, Math.ceil(deadline - performance.now()));
}

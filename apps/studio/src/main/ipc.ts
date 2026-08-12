import { createHash, randomUUID } from "node:crypto";
import { constants as fsConstants } from "node:fs";
import { open } from "node:fs/promises";
import path from "node:path";

import type {
    BrowserWindow,
    IpcMain,
    IpcMainInvokeEvent,
    OpenDialogOptions,
    OpenDialogReturnValue,
    SaveDialogOptions,
    SaveDialogReturnValue,
} from "electron";

import type {
    AssetCatalogInspectRequest as StudioAssetCatalogInspectRequest,
    AssetCatalogListRequest as StudioAssetCatalogListRequest,
    AssetPreviewCloseRequest as StudioAssetPreviewCloseRequest,
    AssetPreviewOpenRequest as StudioAssetPreviewOpenRequest,
    AssetPreviewReadRequest as StudioAssetPreviewReadRequest,
} from "../generated/studio-protocol";
import {
    DEFAULT_CREATION_CONTENT_MODE,
    isCreationContentMode,
} from "../generated/creation-content-modes";
import {
    IPC_CHANNELS,
    validateStudioCreationAssetAcceptanceResults,
    type ChangesetsListParams,
    type CreateExternalGrantParams,
    type EventsListParams,
    type ForgeServiceStatus,
    type JobsListParams,
    type StudioActivityEvent,
    type StudioAssetCatalogInspectReply,
    type StudioAssetCatalogListReply,
    type StudioAssetPreviewChunkReply,
    type StudioAssetPreviewCloseReply,
    type StudioAssetPreviewOpenReply,
    type StudioCapabilityMethod,
    type StudioClientError,
    type StudioClientResult,
    type StudioExternalArtifactKind,
    type StudioExternalJobsListParams,
    type StudioExternalOperation,
    type StudioExtractGamePackageParams,
    type StudioMaterializeGameParams,
    type StudioPackageGameParams,
    type StudioCreationProjectCreateParams,
    type StudioCreationWorkspaceReplyEnvelope,
    type StudioCreationAuthorityCapabilities,
    type StudioReadMethod,
    type StudioReplyEnvelope,
    type StudioV2Method,
    type StudioV2ReplyEnvelope,
    type StudioV3ReplyEnvelope,
    type StudioV4ReplyEnvelope,
    type StudioV5ReplyEnvelope,
} from "../shared/studio-api";
import type { CodexBridgeClient } from "./codex-bridge";
import { CodexTransportError } from "./codex-supervisor";
import type { ForgeServiceClient } from "./forge-service";
import {
    StudioRequestCancelledError,
    StudioProtocolError,
    StudioRequestTimeoutError,
    StudioTransportError,
} from "./ndjson-supervisor";
import {
    AUTHORITY_JOB_OPERATIONS,
    deriveAssetQaReviewJobCreateParams,
    deriveAssetReleaseAuthorizeJobCreateParams,
    deriveRuntimeHeadlessVerifyJobCreateParams,
    readVerifiedCreationPreviewBytes,
    validateAuthorityReviewReply,
    validateRendererCreationJobCreateBoundary,
    type AuthorityReviewReply,
    type CreationAuthoritySnapshot,
    type RuntimeHeadlessArtifactSelections,
} from "./creation-authority-actions";
import {
    decodeCanonicalAssetPreviewBase64,
    isPortableRelativePath,
    isPortableSourcePath,
    validateStudioEnvelope,
} from "./protocol-validator";
import { isTrustedStudioSender } from "./security";

const DEFAULT_REQUEST_TIMEOUT_MS = 10_000;
const ASSET_CATALOG_REQUEST_TIMEOUT_MS = 60_000;
const ASSET_CATALOG_PAGE_SIZE = 64;
const ASSET_PREVIEW_REQUEST_TIMEOUT_MS = 60_000;
const ASSET_PREVIEW_CHUNK_BYTES = 64 * 1024;
const MAX_ASSET_PREVIEW_SEQUENCE = 8191;
const MAX_CREATION_PREVIEW_SEQUENCE = 1023;
const MAX_CREATION_OUTPUT_GRANT_PAGE_SIZE = 8;
const WORKSPACE_ID_PATTERN = /^[a-z][a-z0-9_-]{1,63}$/u;
const ASSET_ENTRY_ID_PATTERN = /^asset_[0-9a-f]{64}$/u;
const ASSET_PREVIEW_HANDLE_PATTERN = /^[A-Za-z0-9_-]{43}$/u;
const JOB_ID_PATTERN = /^[a-z0-9][a-z0-9_-]{0,127}$/u;
const CHANGESET_ID_PATTERN = JOB_ID_PATTERN;
const SHA256_PATTERN = /^[0-9a-f]{64}$/u;
const CREATION_ARTIFACT_LIFECYCLES = new Set([
    "active",
    "invalidated",
    "historical",
    "candidate",
]);
const MAX_CREATION_ARTIFACT_PAGE_SIZE = 64;
const MAX_CREATION_ADMISSION_DOCUMENT_BYTES = 768 * 1024;
const MAX_CREATION_JOB_DEPENDENCIES = 128;
const MAX_CREATION_ASSET_LICENSES = 4;
const MAX_CREATION_JOB_PAGE_SIZE = 8;
const MAX_CREATION_EVENT_PAGE_SIZE = 256;
const CREATION_AUTHORITY_OUTPUT_ROLES = new Set([
    "audio",
    "data",
    "model",
    "sprite",
    "texture",
    "ui",
    "video",
]);
const CREATION_JOB_STATES = new Set([
    "queued",
    "running",
    "succeeded",
    "failed",
    "canceled",
    "orphaned",
]);
const CREATION_JOB_RECOVERY_MODES = new Set(["resume", "rollback", "cleanup"]);
const MAX_SOURCE_DOCUMENT_BYTES = 256 * 1024;
const MAX_CREATION_PROJECT_BYTES = 1024 * 1024;
const MAX_CREATION_PROFILE_BYTES = 4 * 1024 * 1024;
const CREATION_PHASE_IDS = new Set([
    "p00_brief",
    "p01_genre_style",
    "p02_world_laws",
    "p03_geography",
    "p04_timeline",
    "p05_societies",
    "p06_characters",
    "p07_systems",
    "p08_world_arcs",
    "p09_narrative_content",
    "p10_canon_lock",
    "p11_art_audio",
    "p12_asset_specs",
    "p13_asset_production",
    "p14_handoff",
]);
const CREATION_MODULE_COLLECTIONS = Object.freeze({
    "world-forge.world_module": "world_modules",
    "world-forge.activity_module": "activity_modules",
    "world-forge.narrative_module": "narrative_modules",
    "world-forge.system_module": "system_modules",
    "world-forge.logic_module": "logic_modules",
} as const);
const MAX_RUNTIME_TICKS = 1_000_000;
const EXTERNAL_JOB_STATES = new Set([
    "queued",
    "running",
    "succeeded",
    "failed",
    "canceled",
    "orphaned",
]);
const EXTERNAL_ARTIFACT_KINDS: Readonly<
    Record<
        StudioExternalOperation,
        Readonly<Record<"source" | "target", StudioExternalArtifactKind>>
    >
> = Object.freeze({
    "game.materialize": Object.freeze({
        source: "game_materialization_bundle",
        target: "standalone_game",
    }),
    "game.package": Object.freeze({
        source: "standalone_game",
        target: "game_package",
    }),
    "game.package.extract": Object.freeze({
        source: "game_package",
        target: "standalone_game",
    }),
});
type NamedJobOperation =
    | "asset.receipt.validate"
    | "assetpack.verify"
    | "runtime.headless"
    | "runtime.replay";
type ChangesetActionMethod =
    "changeset.approve" | "changeset.reject" | "changeset.apply";
type ChangesetActionStatus = "approved" | "rejected" | "applied";
const CHANGESET_STATUSES = new Set([
    "staged",
    "approved",
    "applying",
    "rejected",
    "applied",
]);
const JOB_STATES = new Set([
    "queued",
    "running",
    "awaiting_approval",
    "awaiting_user",
    "paused",
    "succeeded",
    "failed",
    "canceled",
    "orphaned",
]);

interface AssetPreviewPreviousChunk {
    sequence: number;
    bytes: Uint8Array;
    byteLength: number;
    cumulativeBytes: number;
    cumulativeSha256: string;
    eof: boolean;
}

interface AssetPreviewState {
    manifestRevision: string;
    entryId: string;
    mediaType: "image/png" | "audio/wav";
    byteLength: number;
    sha256: string;
    chunkBytes: number;
    nextSequence: number;
    cumulativeBytes: number;
    digest: ReturnType<typeof createHash>;
    previous: AssetPreviewPreviousChunk | null;
    eof: boolean;
}

export interface StudioDialogClient {
    showOpenDialog(
        window: BrowserWindow,
        options: OpenDialogOptions,
    ): Promise<OpenDialogReturnValue>;
    showSaveDialog(
        window: BrowserWindow,
        options: SaveDialogOptions,
    ): Promise<SaveDialogReturnValue>;
}

export interface StudioCreationProjectSelectionClient {
    readProjectIdentity(
        rootPath: string,
    ): Promise<{ contentHash: string; displayName: string }>;
}

export interface StudioAuthorityModalClient {
    requestReview(
        window: BrowserWindow,
        payload: {
            nonce: string;
            title: string;
            preview: {
                artifactId: string;
                subject: {
                    format: string;
                    formatVersion: number;
                    id: string;
                    contentHash: string;
                };
                mediaType: "image/png" | "audio/wav" | "text/plain";
                data: Uint8Array;
                sha256: string;
                byteLength: number;
            };
            criteria: readonly string[];
        },
    ): Promise<AuthorityReviewReply>;
}

export interface StudioIpcOptions {
    authorityModal?: StudioAuthorityModalClient;
}

const DEFAULT_CREATION_PROJECT_SELECTION: StudioCreationProjectSelectionClient =
    {
        readProjectIdentity: readSelectedCreationProjectIdentity,
    };

const DEFAULT_AUTHORITY_MODAL: StudioAuthorityModalClient = {
    async requestReview(): Promise<AuthorityReviewReply> {
        return await Promise.resolve({
            nonce: "",
            action: "cancel",
            criterionDecisions: [],
        });
    },
};

export function registerStudioIpc(
    ipcMain: IpcMain,
    window: BrowserWindow,
    service: ForgeServiceClient,
    codex: CodexBridgeClient,
    dialogs: StudioDialogClient,
    creationProjectSelection: StudioCreationProjectSelectionClient = DEFAULT_CREATION_PROJECT_SELECTION,
    options: StudioIpcOptions = {},
): () => void {
    const trusted = (event: IpcMainInvokeEvent): boolean =>
        isTrustedStudioSender(event, window.webContents);
    const assetPreviews = new Map<string, AssetPreviewState>();
    const authorityModal = options.authorityModal ?? DEFAULT_AUTHORITY_MODAL;

    ipcMain.handle(
        IPC_CHANNELS.initialize,
        async (event, ...args: unknown[]) => {
            const invalid = rejectUntrustedOrUnexpectedArguments(
                trusted(event),
                args,
            );
            return invalid ?? (await capture(() => service.initialize()));
        },
    );

    ipcMain.handle(IPC_CHANNELS.status, (event, ...args: unknown[]) => {
        const invalid = rejectUntrustedOrUnexpectedArguments(
            trusted(event),
            args,
        );
        return invalid ?? success(service.status);
    });

    ipcMain.handle(
        IPC_CHANNELS.listWorkspaces,
        async (event, ...args: unknown[]) => {
            const invalid = rejectUntrustedOrUnexpectedArguments(
                trusted(event),
                args,
            );
            return (
                invalid ?? (await requestRead(service, "workspace.list", {}))
            );
        },
    );

    ipcMain.handle(
        IPC_CHANNELS.listEvents,
        async (event, value: unknown = {}) => {
            if (!trusted(event)) {
                return failure(
                    "invalid_request",
                    "Rejected Studio IPC from an untrusted sender",
                );
            }
            return await captureValidated(
                () => validateEventsListParams(value),
                (params) => requestRead(service, "events.list", { ...params }),
            );
        },
    );

    ipcMain.handle(
        IPC_CHANNELS.listChangesets,
        async (event, value: unknown = {}) => {
            if (!trusted(event)) {
                return failure(
                    "invalid_request",
                    "Rejected Studio IPC from an untrusted sender",
                );
            }
            return await captureValidated(
                () => validateChangesetsListParams(value),
                (params) =>
                    requestRead(service, "changeset.list", { ...params }),
            );
        },
    );

    ipcMain.handle(
        IPC_CHANNELS.listJobs,
        async (event, value: unknown = {}) => {
            if (!trusted(event)) {
                return failure(
                    "invalid_request",
                    "Rejected Studio IPC from an untrusted sender",
                );
            }
            return await captureValidated(
                () => validateJobsListParams(value),
                (params) => requestRead(service, "job.list", { ...params }),
            );
        },
    );

    ipcMain.handle(
        IPC_CHANNELS.listCreationWorkspaces,
        async (event, ...args: unknown[]) => {
            const invalid = rejectUntrustedOrUnexpectedArguments(
                trusted(event),
                args,
            );
            return (
                invalid ??
                (await requestCreationNamed(
                    service,
                    "creation_workspace.list",
                    {},
                ))
            );
        },
    );

    ipcMain.handle(
        IPC_CHANNELS.registerCreationProject,
        async (event, ...args: unknown[]) => {
            const invalid = rejectUntrustedOrUnexpectedArguments(
                trusted(event),
                args,
            );
            if (invalid) return invalid;
            return await registerSelectedCreationProject(
                service,
                dialogs,
                window,
                creationProjectSelection,
            );
        },
    );

    ipcMain.handle(
        IPC_CHANNELS.createCreationProject,
        async (event, ...args: unknown[]) => {
            if (!trusted(event)) return untrustedFailure();
            return await captureValidated(
                () =>
                    validateSingleArgument(
                        args,
                        validateCreationProjectCreateArgument,
                    ),
                (argument) =>
                    createSelectedCreationProject(
                        service,
                        dialogs,
                        window,
                        argument,
                    ),
            );
        },
    );

    ipcMain.handle(
        IPC_CHANNELS.openCreationWorkspace,
        async (event, ...args: unknown[]) => {
            if (!trusted(event)) return untrustedFailure();
            return await captureValidated(
                () => validateSingleArgument(args, validateWorkspaceArgument),
                ({ workspaceId }) =>
                    requestCreationNamed(service, "creation_workspace.open", {
                        workspace_id: workspaceId,
                    }),
            );
        },
    );

    ipcMain.handle(
        IPC_CHANNELS.listCreationDocuments,
        async (event, ...args: unknown[]) => {
            if (!trusted(event)) return untrustedFailure();
            return await captureValidated(
                () =>
                    validateSingleArgument(
                        args,
                        validateCreationRevisionArgument,
                    ),
                ({ workspaceId, expectedSourceRevision }) =>
                    requestCreationNamed(service, "creation_document.list", {
                        workspace_id: workspaceId,
                        expected_source_revision: expectedSourceRevision,
                    }),
            );
        },
    );

    ipcMain.handle(
        IPC_CHANNELS.readCreationDocument,
        async (event, ...args: unknown[]) => {
            if (!trusted(event)) return untrustedFailure();
            return await captureValidated(
                () =>
                    validateSingleArgument(
                        args,
                        validateCreationDocumentArgument,
                    ),
                ({ workspaceId, expectedSourceRevision, path: documentPath }) =>
                    requestCreationNamed(service, "creation_document.read", {
                        workspace_id: workspaceId,
                        expected_source_revision: expectedSourceRevision,
                        path: documentPath,
                    }),
            );
        },
    );

    ipcMain.handle(
        IPC_CHANNELS.getCreationWorkflow,
        async (event, ...args: unknown[]) => {
            if (!trusted(event)) return untrustedFailure();
            return await captureValidated(
                () => validateSingleArgument(args, validateWorkspaceArgument),
                ({ workspaceId }) =>
                    requestCreationNamed(service, "creation_workflow.get", {
                        workspace_id: workspaceId,
                    }),
            );
        },
    );

    ipcMain.handle(
        IPC_CHANNELS.inspectCreationReadiness,
        async (event, ...args: unknown[]) => {
            if (!trusted(event)) return untrustedFailure();
            return await captureValidated(
                () => validateSingleArgument(args, validateWorkspaceArgument),
                ({ workspaceId }) =>
                    requestCreationNamed(
                        service,
                        "creation_readiness.inspect",
                        {
                            workspace_id: workspaceId,
                        },
                    ),
            );
        },
    );

    ipcMain.handle(
        IPC_CHANNELS.listCreationArtifacts,
        async (event, ...args: unknown[]) => {
            if (!trusted(event)) return untrustedFailure();
            return await captureValidated(
                () =>
                    validateSingleArgument(
                        args,
                        validateCreationArtifactListArgument,
                    ),
                (argument) =>
                    requestCreationEvidenceNamed(
                        service,
                        "creation_artifact.list",
                        {
                            workspace_id: argument.workspaceId,
                            expected_root_generation:
                                argument.expectedRootGeneration,
                            expected_source_revision:
                                argument.expectedSourceRevision,
                            expected_workflow_status_hash:
                                argument.expectedWorkflowStatusHash,
                            expected_artifact_snapshot_hash:
                                argument.expectedArtifactSnapshotHash,
                            lifecycle: argument.lifecycle,
                            cursor: argument.cursor,
                            limit: argument.limit,
                        },
                    ),
            );
        },
    );

    ipcMain.handle(
        IPC_CHANNELS.inspectCreationArtifact,
        async (event, ...args: unknown[]) => {
            if (!trusted(event)) return untrustedFailure();
            return await captureValidated(
                () =>
                    validateSingleArgument(
                        args,
                        validateCreationArtifactInspectArgument,
                    ),
                (argument) =>
                    requestCreationEvidenceNamed(
                        service,
                        "creation_artifact.inspect",
                        {
                            workspace_id: argument.workspaceId,
                            expected_root_generation:
                                argument.expectedRootGeneration,
                            expected_source_revision:
                                argument.expectedSourceRevision,
                            expected_workflow_status_hash:
                                argument.expectedWorkflowStatusHash,
                            expected_artifact_snapshot_hash:
                                argument.expectedArtifactSnapshotHash,
                            artifact_id: argument.artifactId,
                        },
                    ),
            );
        },
    );

    ipcMain.handle(
        IPC_CHANNELS.inspectCreationEvidence,
        async (event, ...args: unknown[]) => {
            if (!trusted(event)) return untrustedFailure();
            return await captureValidated(
                () =>
                    validateSingleArgument(
                        args,
                        validateCreationEvidenceInspectArgument,
                    ),
                (argument) =>
                    requestCreationEvidenceNamed(
                        service,
                        "creation_evidence.inspect",
                        {
                            workspace_id: argument.workspaceId,
                            expected_root_generation:
                                argument.expectedRootGeneration,
                            expected_source_revision:
                                argument.expectedSourceRevision,
                            expected_workflow_status_hash:
                                argument.expectedWorkflowStatusHash,
                            expected_artifact_snapshot_hash:
                                argument.expectedArtifactSnapshotHash,
                        },
                    ),
            );
        },
    );

    ipcMain.handle(
        IPC_CHANNELS.openCreationPreview,
        async (event, ...args: unknown[]) => {
            if (!trusted(event)) return untrustedFailure();
            return await captureValidated(
                () =>
                    validateSingleArgument(
                        args,
                        validateCreationPreviewOpenArgument,
                    ),
                (argument) =>
                    requestCreationPreviewNamed(
                        service,
                        "creation_preview.open",
                        {
                            workspace_id: argument.workspaceId,
                            expected_root_generation:
                                argument.expectedRootGeneration,
                            expected_source_revision:
                                argument.expectedSourceRevision,
                            expected_workflow_status_hash:
                                argument.expectedWorkflowStatusHash,
                            expected_artifact_snapshot_hash:
                                argument.expectedArtifactSnapshotHash,
                            assetpack_artifact_id: argument.assetpackArtifactId,
                            output_grant_id: argument.outputGrantId,
                            expected_output_grant_generation:
                                argument.expectedOutputGrantGeneration,
                            asset_id: argument.assetId,
                        },
                    ),
            );
        },
    );

    ipcMain.handle(
        IPC_CHANNELS.readCreationPreviewChunk,
        async (event, ...args: unknown[]) => {
            if (!trusted(event)) return untrustedFailure();
            return await captureValidated(
                () =>
                    validateSingleArgument(
                        args,
                        validateCreationPreviewReadArgument,
                    ),
                ({ handle, sequence }) =>
                    requestCreationPreviewNamed(
                        service,
                        "creation_preview.read",
                        {
                            handle,
                            sequence,
                        },
                    ),
            );
        },
    );

    ipcMain.handle(
        IPC_CHANNELS.closeCreationPreview,
        async (event, ...args: unknown[]) => {
            if (!trusted(event)) return untrustedFailure();
            return await captureValidated(
                () =>
                    validateSingleArgument(
                        args,
                        validateCreationPreviewCloseArgument,
                    ),
                ({ handle }) =>
                    requestCreationPreviewNamed(
                        service,
                        "creation_preview.close",
                        { handle },
                    ),
            );
        },
    );

    ipcMain.handle(
        IPC_CHANNELS.compileCreationProject,
        async (event, ...args: unknown[]) => {
            if (!trusted(event)) return untrustedFailure();
            return await captureValidated(
                () =>
                    validateSingleArgument(
                        args,
                        validateCreationCompileArgument,
                    ),
                (argument) =>
                    requestCreationEvidenceNamed(
                        service,
                        "creation_job.create",
                        {
                            ...(argument.jobId === undefined
                                ? {}
                                : { job_id: argument.jobId }),
                            workspace_id: argument.workspaceId,
                            operation: "creation.compile",
                            expected_root_generation:
                                argument.expectedRootGeneration,
                            expected_source_revision:
                                argument.expectedSourceRevision,
                            expected_workflow_status_hash:
                                argument.expectedWorkflowStatusHash,
                            expected_artifact_snapshot_hash:
                                argument.expectedArtifactSnapshotHash,
                        },
                    ),
            );
        },
    );

    ipcMain.handle(
        IPC_CHANNELS.admitCreationArtifact,
        async (event, ...args: unknown[]) => {
            if (!trusted(event)) return untrustedFailure();
            return await captureValidated(
                () =>
                    validateSingleArgument(
                        args,
                        validateCreationArtifactAdmissionArgument,
                    ),
                (argument) =>
                    requestCreationEvidenceNamed(
                        service,
                        "creation_job.create",
                        {
                            ...(argument.jobId === undefined
                                ? {}
                                : { job_id: argument.jobId }),
                            workspace_id: argument.workspaceId,
                            operation: "artifact.admit",
                            expected_root_generation:
                                argument.expectedRootGeneration,
                            expected_source_revision:
                                argument.expectedSourceRevision,
                            expected_workflow_status_hash:
                                argument.expectedWorkflowStatusHash,
                            expected_artifact_snapshot_hash:
                                argument.expectedArtifactSnapshotHash,
                            document: argument.document,
                            dependency_artifact_ids:
                                argument.dependencyArtifactIds,
                        },
                    ),
            );
        },
    );

    ipcMain.handle(
        IPC_CHANNELS.processCreationAsset,
        async (event, ...args: unknown[]) => {
            if (!trusted(event)) return untrustedFailure();
            return await captureValidated(
                () =>
                    validateSingleArgument(
                        args,
                        validateCreationAssetProcessArgument,
                    ),
                (argument) =>
                    requestCreationEvidenceNamed(
                        service,
                        "creation_job.create",
                        {
                            ...(argument.jobId === undefined
                                ? {}
                                : { job_id: argument.jobId }),
                            workspace_id: argument.workspaceId,
                            operation: "asset.process",
                            expected_root_generation:
                                argument.expectedRootGeneration,
                            expected_source_revision:
                                argument.expectedSourceRevision,
                            expected_workflow_status_hash:
                                argument.expectedWorkflowStatusHash,
                            expected_artifact_snapshot_hash:
                                argument.expectedArtifactSnapshotHash,
                            license_artifact_ids: argument.licenseArtifactIds,
                            recipe_id: argument.recipeId,
                            processing_receipt_id: argument.processingReceiptId,
                            qa_report_id: argument.qaReportId,
                            acceptance_results: argument.acceptanceResults.map(
                                (result) => ({
                                    criterion_index: result.criterionIndex,
                                    criterion_sha256: result.criterionSha256,
                                    status: result.status,
                                    evidence_hashes: result.evidenceHashes,
                                }),
                            ),
                        },
                    ),
            );
        },
    );

    ipcMain.handle(
        IPC_CHANNELS.selectCreationAssetpackOutput,
        async (event, ...args: unknown[]) => {
            if (!trusted(event)) return untrustedFailure();
            return await captureValidated(
                () =>
                    validateSingleArgument(
                        args,
                        validateCreationOutputGrantSelectArgument,
                    ),
                (argument) =>
                    selectCreationAssetpackOutput(
                        service,
                        dialogs,
                        window,
                        argument,
                    ),
            );
        },
    );

    ipcMain.handle(
        IPC_CHANNELS.selectCreationRuntimeBundleOutput,
        async (event, ...args: unknown[]) => {
            if (!trusted(event)) return untrustedFailure();
            return await captureValidated(
                () =>
                    validateSingleArgument(
                        args,
                        validateCreationOutputGrantSelectArgument,
                    ),
                (argument) =>
                    selectCreationRuntimeBundleOutput(
                        service,
                        dialogs,
                        window,
                        argument,
                    ),
            );
        },
    );

    ipcMain.handle(
        IPC_CHANNELS.selectCreationMaterializationBundleOutput,
        async (event, ...args: unknown[]) => {
            if (!trusted(event)) return untrustedFailure();
            return await captureValidated(
                () =>
                    validateSingleArgument(
                        args,
                        validateCreationOutputGrantSelectArgument,
                    ),
                (argument) =>
                    selectCreationMaterializationBundleOutput(
                        service,
                        dialogs,
                        window,
                        argument,
                    ),
            );
        },
    );

    ipcMain.handle(
        IPC_CHANNELS.selectCreationStandaloneGameOutput,
        async (event, ...args: unknown[]) => {
            if (!trusted(event)) return untrustedFailure();
            return await captureValidated(
                () =>
                    validateSingleArgument(
                        args,
                        validateCreationOutputGrantSelectArgument,
                    ),
                (argument) =>
                    selectCreationStandaloneGameOutput(
                        service,
                        dialogs,
                        window,
                        argument,
                    ),
            );
        },
    );

    ipcMain.handle(
        IPC_CHANNELS.selectCreationGamePackageOutput,
        async (event, ...args: unknown[]) => {
            if (!trusted(event)) return untrustedFailure();
            return await captureValidated(
                () =>
                    validateSingleArgument(
                        args,
                        validateCreationOutputGrantSelectArgument,
                    ),
                (argument) =>
                    selectCreationGamePackageOutput(
                        service,
                        dialogs,
                        window,
                        argument,
                    ),
            );
        },
    );

    ipcMain.handle(
        IPC_CHANNELS.selectCreationGamePackageExtractionOutput,
        async (event, ...args: unknown[]) => {
            if (!trusted(event)) return untrustedFailure();
            return await captureValidated(
                () =>
                    validateSingleArgument(
                        args,
                        validateCreationOutputGrantSelectArgument,
                    ),
                (argument) =>
                    selectCreationGamePackageExtractionOutput(
                        service,
                        dialogs,
                        window,
                        argument,
                    ),
            );
        },
    );

    ipcMain.handle(
        IPC_CHANNELS.reviewCreationAssetQa,
        async (event, ...args: unknown[]) => {
            if (!trusted(event)) return untrustedFailure();
            return await captureValidated(
                () =>
                    validateSingleArgument(
                        args,
                        validateCreationAuthorityReviewArgument,
                    ),
                (argument) =>
                    requestMainOwnedAssetQaReview(
                        service,
                        authorityModal,
                        window,
                        argument,
                    ),
            );
        },
    );

    ipcMain.handle(
        IPC_CHANNELS.authorizeCreationAssetRelease,
        async (event, ...args: unknown[]) => {
            if (!trusted(event)) return untrustedFailure();
            return await captureValidated(
                () =>
                    validateSingleArgument(
                        args,
                        validateCreationAuthorityReleaseArgument,
                    ),
                (argument) =>
                    requestMainOwnedAssetReleaseAuthorization(
                        service,
                        argument,
                    ),
            );
        },
    );

    ipcMain.handle(
        IPC_CHANNELS.selectCreationHeadlessEvidenceOutput,
        async (event, ...args: unknown[]) => {
            if (!trusted(event)) return untrustedFailure();
            return await captureValidated(
                () =>
                    validateSingleArgument(
                        args,
                        validateCreationOutputGrantSelectArgument,
                    ),
                (argument) =>
                    selectCreationHeadlessEvidenceOutput(
                        service,
                        dialogs,
                        window,
                        argument,
                    ),
            );
        },
    );

    ipcMain.handle(
        IPC_CHANNELS.verifyCreationHeadless,
        async (event, ...args: unknown[]) => {
            if (!trusted(event)) return untrustedFailure();
            return await captureValidated(
                () =>
                    validateSingleArgument(
                        args,
                        validateCreationAuthorityHeadlessArgument,
                    ),
                (argument) =>
                    requestMainOwnedRuntimeHeadlessVerification(
                        service,
                        argument,
                    ),
            );
        },
    );

    ipcMain.handle(
        IPC_CHANNELS.requestCreationJobCancel,
        async (event, ...args: unknown[]) => {
            if (!trusted(event)) return untrustedFailure();
            return await captureValidated(
                () =>
                    validateSingleArgument(
                        args,
                        validateCreationAuthorityJobActionArgument,
                    ),
                (argument) =>
                    requestMainOwnedAuthorityJobAction(
                        service,
                        "creation_job.cancel",
                        argument,
                    ),
            );
        },
    );

    ipcMain.handle(
        IPC_CHANNELS.requestCreationJobRecovery,
        async (event, ...args: unknown[]) => {
            if (!trusted(event)) return untrustedFailure();
            return await captureValidated(
                () =>
                    validateSingleArgument(
                        args,
                        validateCreationAuthorityJobActionArgument,
                    ),
                (argument) =>
                    requestMainOwnedAuthorityJobAction(
                        service,
                        "creation_job.recover",
                        argument,
                    ),
            );
        },
    );

    ipcMain.handle(
        IPC_CHANNELS.getCreationAssetpackOutput,
        async (event, ...args: unknown[]) => {
            if (!trusted(event)) return untrustedFailure();
            return await captureValidated(
                () =>
                    validateSingleArgument(
                        args,
                        validateCreationOutputGrantGetArgument,
                    ),
                (argument) =>
                    requestCreationEvidenceNamed(
                        service,
                        "creation_output_grant.get",
                        {
                            grant_id: argument.grantId,
                        },
                    ),
            );
        },
    );

    ipcMain.handle(
        IPC_CHANNELS.getCreationAuthorityCapabilities,
        async (event, ...args: unknown[]) => {
            const invalid = rejectUntrustedOrUnexpectedArguments(
                trusted(event),
                args,
            );
            return (
                invalid ??
                (await capture(() =>
                    getCreationAuthorityCapabilities(service),
                ))
            );
        },
    );

    ipcMain.handle(
        IPC_CHANNELS.listCreationOutputGrants,
        async (event, ...args: unknown[]) => {
            if (!trusted(event)) return untrustedFailure();
            return await captureValidated(
                () =>
                    validateSingleArgument(
                        args,
                        validateCreationOutputGrantListArgument,
                    ),
                (argument) =>
                    requestCreationEvidenceNamed(
                        service,
                        "creation_output_grant.list",
                        {
                            workspace_id: argument.workspaceId,
                            expected_root_generation:
                                argument.expectedRootGeneration,
                            expected_source_revision:
                                argument.expectedSourceRevision,
                            expected_workflow_status_hash:
                                argument.expectedWorkflowStatusHash,
                            expected_artifact_snapshot_hash:
                                argument.expectedArtifactSnapshotHash,
                            cursor: argument.cursor,
                            limit: argument.limit,
                        },
                    ),
            );
        },
    );

    ipcMain.handle(
        IPC_CHANNELS.listCreationAuthorityOutputGrants,
        async (event, ...args: unknown[]) => {
            if (!trusted(event)) return untrustedFailure();
            return await captureValidated(
                () =>
                    validateSingleArgument(
                        args,
                        validateCreationOutputGrantListArgument,
                    ),
                async (argument) => {
                    await getCreationAuthorityCapabilities(service);
                    return await requestCreationAuthorityNamed(
                        service,
                        "creation_output_grant.list",
                        {
                            workspace_id: argument.workspaceId,
                            expected_root_generation:
                                argument.expectedRootGeneration,
                            expected_source_revision:
                                argument.expectedSourceRevision,
                            expected_workflow_status_hash:
                                argument.expectedWorkflowStatusHash,
                            expected_artifact_snapshot_hash:
                                argument.expectedArtifactSnapshotHash,
                            cursor: argument.cursor,
                            limit: argument.limit,
                        },
                    );
                },
            );
        },
    );

    ipcMain.handle(
        IPC_CHANNELS.revokeCreationAssetpackOutput,
        async (event, ...args: unknown[]) => {
            if (!trusted(event)) return untrustedFailure();
            return await captureValidated(
                () =>
                    validateSingleArgument(
                        args,
                        validateCreationOutputGrantRevokeArgument,
                    ),
                (argument) =>
                    requestCreationEvidenceNamed(
                        service,
                        "creation_output_grant.revoke",
                        {
                            grant_id: argument.grantId,
                            expected_generation: argument.expectedGeneration,
                        },
                    ),
            );
        },
    );

    ipcMain.handle(
        IPC_CHANNELS.sealCreationAssetRelease,
        async (event, ...args: unknown[]) => {
            if (!trusted(event)) return untrustedFailure();
            return await captureValidated(
                () =>
                    validateSingleArgument(
                        args,
                        validateCreationAssetReleaseSealArgument,
                    ),
                (argument) =>
                    requestCreationEvidenceNamed(
                        service,
                        "creation_job.create",
                        {
                            ...(argument.jobId === undefined
                                ? {}
                                : { job_id: argument.jobId }),
                            workspace_id: argument.workspaceId,
                            operation: "asset.release.seal",
                            expected_root_generation:
                                argument.expectedRootGeneration,
                            expected_source_revision:
                                argument.expectedSourceRevision,
                            expected_workflow_status_hash:
                                argument.expectedWorkflowStatusHash,
                            expected_artifact_snapshot_hash:
                                argument.expectedArtifactSnapshotHash,
                            qa_report_artifact_ids:
                                argument.qaReportArtifactIds,
                            manifest_id: argument.manifestId,
                            target_grant_id: argument.targetGrantId,
                            expected_target_grant_generation:
                                argument.expectedTargetGrantGeneration,
                        },
                    ),
            );
        },
    );

    ipcMain.handle(
        IPC_CHANNELS.composeCreationRuntime,
        async (event, ...args: unknown[]) => {
            if (!trusted(event)) return untrustedFailure();
            return await captureValidated(
                () =>
                    validateSingleArgument(
                        args,
                        validateCreationRuntimeComposeArgument,
                    ),
                (argument) =>
                    requestCreationEvidenceNamed(
                        service,
                        "creation_job.create",
                        {
                            ...(argument.jobId === undefined
                                ? {}
                                : { job_id: argument.jobId }),
                            workspace_id: argument.workspaceId,
                            operation: "runtime.compose",
                            expected_root_generation:
                                argument.expectedRootGeneration,
                            expected_source_revision:
                                argument.expectedSourceRevision,
                            expected_workflow_status_hash:
                                argument.expectedWorkflowStatusHash,
                            expected_artifact_snapshot_hash:
                                argument.expectedArtifactSnapshotHash,
                            gamepack_artifact_id: argument.gamepackArtifactId,
                            asset_inventory_artifact_id:
                                argument.assetInventoryArtifactId,
                            assetpack_artifact_id: argument.assetpackArtifactId,
                            target_grant_id: argument.targetGrantId,
                            expected_target_grant_generation:
                                argument.expectedTargetGrantGeneration,
                        },
                    ),
            );
        },
    );

    ipcMain.handle(
        IPC_CHANNELS.buildCreationRuntimeBundle,
        async (event, ...args: unknown[]) => {
            if (!trusted(event)) return untrustedFailure();
            return await captureValidated(
                () =>
                    validateSingleArgument(
                        args,
                        validateCreationRuntimeBundleBuildArgument,
                    ),
                (argument) =>
                    requestCreationEvidenceNamed(
                        service,
                        "creation_job.create",
                        {
                            ...(argument.jobId === undefined
                                ? {}
                                : { job_id: argument.jobId }),
                            workspace_id: argument.workspaceId,
                            operation: "runtime.bundle.build",
                            expected_root_generation:
                                argument.expectedRootGeneration,
                            expected_source_revision:
                                argument.expectedSourceRevision,
                            expected_workflow_status_hash:
                                argument.expectedWorkflowStatusHash,
                            expected_artifact_snapshot_hash:
                                argument.expectedArtifactSnapshotHash,
                            gamepack_artifact_id: argument.gamepackArtifactId,
                            asset_inventory_artifact_id:
                                argument.assetInventoryArtifactId,
                            assetpack_artifact_id: argument.assetpackArtifactId,
                            runtime_snapshot_artifact_id:
                                argument.runtimeSnapshotArtifactId,
                            runtime_adapter_registry_artifact_id:
                                argument.runtimeAdapterRegistryArtifactId,
                            runtime_composition_artifact_id:
                                argument.runtimeCompositionArtifactId,
                            runtime_support_report_artifact_id:
                                argument.runtimeSupportReportArtifactId,
                            source_grant_id: argument.sourceGrantId,
                            expected_source_grant_generation:
                                argument.expectedSourceGrantGeneration,
                            target_grant_id: argument.targetGrantId,
                            expected_target_grant_generation:
                                argument.expectedTargetGrantGeneration,
                        },
                    ),
            );
        },
    );

    ipcMain.handle(
        IPC_CHANNELS.buildCreationMaterializationBundle,
        async (event, ...args: unknown[]) => {
            if (!trusted(event)) return untrustedFailure();
            return await captureValidated(
                () =>
                    validateSingleArgument(
                        args,
                        validateCreationMaterializationBundleBuildArgument,
                    ),
                (argument) =>
                    requestCreationEvidenceNamed(
                        service,
                        "creation_job.create",
                        {
                            ...(argument.jobId === undefined
                                ? {}
                                : { job_id: argument.jobId }),
                            workspace_id: argument.workspaceId,
                            operation: "game.materialization.bundle.build",
                            expected_root_generation:
                                argument.expectedRootGeneration,
                            expected_source_revision:
                                argument.expectedSourceRevision,
                            expected_workflow_status_hash:
                                argument.expectedWorkflowStatusHash,
                            expected_artifact_snapshot_hash:
                                argument.expectedArtifactSnapshotHash,
                            runtime_bundle_artifact_id:
                                argument.runtimeBundleArtifactId,
                            source_grant_id: argument.sourceGrantId,
                            expected_source_grant_generation:
                                argument.expectedSourceGrantGeneration,
                            target_grant_id: argument.targetGrantId,
                            expected_target_grant_generation:
                                argument.expectedTargetGrantGeneration,
                        },
                    ),
            );
        },
    );

    ipcMain.handle(
        IPC_CHANNELS.materializeCreationGame,
        async (event, ...args: unknown[]) => {
            if (!trusted(event)) return untrustedFailure();
            return await captureValidated(
                () =>
                    validateSingleArgument(
                        args,
                        validateCreationGameMaterializeArgument,
                    ),
                (argument) =>
                    requestCreationEvidenceNamed(
                        service,
                        "creation_job.create",
                        {
                            ...(argument.jobId === undefined
                                ? {}
                                : { job_id: argument.jobId }),
                            workspace_id: argument.workspaceId,
                            operation: "game.materialize",
                            expected_root_generation:
                                argument.expectedRootGeneration,
                            expected_source_revision:
                                argument.expectedSourceRevision,
                            expected_workflow_status_hash:
                                argument.expectedWorkflowStatusHash,
                            expected_artifact_snapshot_hash:
                                argument.expectedArtifactSnapshotHash,
                            materialization_bundle_artifact_id:
                                argument.materializationBundleArtifactId,
                            source_grant_id: argument.sourceGrantId,
                            expected_source_grant_generation:
                                argument.expectedSourceGrantGeneration,
                            target_grant_id: argument.targetGrantId,
                            expected_target_grant_generation:
                                argument.expectedTargetGrantGeneration,
                        },
                    ),
            );
        },
    );

    ipcMain.handle(
        IPC_CHANNELS.packageCreationGame,
        async (event, ...args: unknown[]) => {
            if (!trusted(event)) return untrustedFailure();
            return await captureValidated(
                () =>
                    validateSingleArgument(
                        args,
                        validateCreationGamePackageArgument,
                    ),
                (argument) =>
                    requestCreationEvidenceNamed(
                        service,
                        "creation_job.create",
                        {
                            ...(argument.jobId === undefined
                                ? {}
                                : { job_id: argument.jobId }),
                            workspace_id: argument.workspaceId,
                            operation: "game.package",
                            expected_root_generation:
                                argument.expectedRootGeneration,
                            expected_source_revision:
                                argument.expectedSourceRevision,
                            expected_workflow_status_hash:
                                argument.expectedWorkflowStatusHash,
                            expected_artifact_snapshot_hash:
                                argument.expectedArtifactSnapshotHash,
                            standalone_game_artifact_id:
                                argument.standaloneGameArtifactId,
                            source_grant_id: argument.sourceGrantId,
                            expected_source_grant_generation:
                                argument.expectedSourceGrantGeneration,
                            target_grant_id: argument.targetGrantId,
                            expected_target_grant_generation:
                                argument.expectedTargetGrantGeneration,
                        },
                    ),
            );
        },
    );

    ipcMain.handle(
        IPC_CHANNELS.extractCreationGamePackage,
        async (event, ...args: unknown[]) => {
            if (!trusted(event)) return untrustedFailure();
            return await captureValidated(
                () =>
                    validateSingleArgument(
                        args,
                        validateCreationGamePackageExtractArgument,
                    ),
                (argument) =>
                    requestCreationEvidenceNamed(
                        service,
                        "creation_job.create",
                        {
                            ...(argument.jobId === undefined
                                ? {}
                                : { job_id: argument.jobId }),
                            workspace_id: argument.workspaceId,
                            operation: "game.package.extract",
                            expected_root_generation:
                                argument.expectedRootGeneration,
                            expected_source_revision:
                                argument.expectedSourceRevision,
                            expected_workflow_status_hash:
                                argument.expectedWorkflowStatusHash,
                            expected_artifact_snapshot_hash:
                                argument.expectedArtifactSnapshotHash,
                            game_package_artifact_id:
                                argument.gamePackageArtifactId,
                            source_grant_id: argument.sourceGrantId,
                            expected_source_grant_generation:
                                argument.expectedSourceGrantGeneration,
                            target_grant_id: argument.targetGrantId,
                            expected_target_grant_generation:
                                argument.expectedTargetGrantGeneration,
                        },
                    ),
            );
        },
    );

    ipcMain.handle(
        IPC_CHANNELS.getCreationJob,
        async (event, ...args: unknown[]) => {
            if (!trusted(event)) return untrustedFailure();
            return await captureValidated(
                () =>
                    validateSingleArgument(args, validateCreationJobIdArgument),
                ({ jobId }) =>
                    requestCreationEvidenceNamed(service, "creation_job.get", {
                        job_id: jobId,
                    }),
            );
        },
    );

    ipcMain.handle(
        IPC_CHANNELS.listCreationJobs,
        async (event, ...args: unknown[]) => {
            if (!trusted(event)) return untrustedFailure();
            return await captureValidated(
                () =>
                    validateSingleArgument(
                        args,
                        validateCreationJobListArgument,
                    ),
                (argument) =>
                    requestCreationEvidenceNamed(service, "creation_job.list", {
                        workspace_id: argument.workspaceId,
                        state: argument.state,
                        after_sequence: argument.afterSequence,
                        limit: argument.limit,
                    }),
            );
        },
    );

    ipcMain.handle(
        IPC_CHANNELS.cancelCreationJob,
        async (event, ...args: unknown[]) => {
            if (!trusted(event)) return untrustedFailure();
            return await captureValidated(
                () =>
                    validateSingleArgument(
                        args,
                        validateCreationJobMutationArgument,
                    ),
                (argument) =>
                    requestCreationEvidenceNamed(
                        service,
                        "creation_job.cancel",
                        {
                            job_id: argument.jobId,
                            expected_generation: argument.expectedGeneration,
                            expected_record_hash: argument.expectedRecordHash,
                        },
                    ),
            );
        },
    );

    ipcMain.handle(
        IPC_CHANNELS.recoverCreationJob,
        async (event, ...args: unknown[]) => {
            if (!trusted(event)) return untrustedFailure();
            return await captureValidated(
                () =>
                    validateSingleArgument(
                        args,
                        validateCreationJobRecoveryArgument,
                    ),
                (argument) =>
                    requestCreationEvidenceNamed(
                        service,
                        "creation_job.recover",
                        {
                            job_id: argument.jobId,
                            mode: argument.mode,
                            expected_generation: argument.expectedGeneration,
                            expected_record_hash: argument.expectedRecordHash,
                        },
                    ),
            );
        },
    );

    ipcMain.handle(
        IPC_CHANNELS.listCreationEvents,
        async (event, ...args: unknown[]) => {
            if (!trusted(event)) return untrustedFailure();
            return await captureValidated(
                () =>
                    validateSingleArgument(
                        args,
                        validateCreationEventListArgument,
                    ),
                (argument) =>
                    requestCreationEvidenceNamed(
                        service,
                        "creation_event.list",
                        {
                            workspace_id: argument.workspaceId,
                            after_id: argument.afterId,
                            limit: argument.limit,
                        },
                    ),
            );
        },
    );

    ipcMain.handle(
        IPC_CHANNELS.stageCreationProfile,
        async (event, ...args: unknown[]) => {
            if (!trusted(event)) return untrustedFailure();
            return await captureValidated(
                () =>
                    validateSingleArgument(
                        args,
                        validateCreationProfileStageArgument,
                    ),
                (argument) => requestCreationProfileStage(service, argument),
            );
        },
    );

    ipcMain.handle(
        IPC_CHANNELS.stageCreationModuleChange,
        async (event, ...args: unknown[]) => {
            if (!trusted(event)) return untrustedFailure();
            return await captureValidated(
                () =>
                    validateSingleArgument(
                        args,
                        validateCreationModuleStageArgument,
                    ),
                (argument) => requestCreationModuleStage(service, argument),
            );
        },
    );

    ipcMain.handle(
        IPC_CHANNELS.reconcileCreationWorkflow,
        async (event, ...args: unknown[]) => {
            if (!trusted(event)) return untrustedFailure();
            return await captureValidated(
                () =>
                    validateSingleArgument(
                        args,
                        validateCreationWorkflowReconcileArgument,
                    ),
                (argument) =>
                    requestCreationAuthorityAction(
                        service,
                        "creation_workflow.reconcile",
                        argument,
                    ),
            );
        },
    );

    ipcMain.handle(
        IPC_CHANNELS.readCreationPhase,
        async (event, ...args: unknown[]) => {
            if (!trusted(event)) return untrustedFailure();
            return await captureValidated(
                () =>
                    validateSingleArgument(
                        args,
                        validateCreationPhaseReadArgument,
                    ),
                (argument) =>
                    requestCreationAuthorityAction(
                        service,
                        "creation_phase.read",
                        argument,
                    ),
            );
        },
    );

    ipcMain.handle(
        IPC_CHANNELS.validateCreationPhase,
        async (event, ...args: unknown[]) => {
            if (!trusted(event)) return untrustedFailure();
            return await captureValidated(
                () =>
                    validateSingleArgument(
                        args,
                        validateCreationPhaseReportArgument,
                    ),
                (argument) =>
                    requestCreationAuthorityAction(
                        service,
                        "creation_phase.validate",
                        argument,
                    ),
            );
        },
    );

    ipcMain.handle(
        IPC_CHANNELS.completeCreationPhase,
        async (event, ...args: unknown[]) => {
            if (!trusted(event)) return untrustedFailure();
            return await captureValidated(
                () =>
                    validateSingleArgument(
                        args,
                        validateCreationPhaseCompleteArgument,
                    ),
                (argument) =>
                    requestCreationAuthorityAction(
                        service,
                        "creation_phase.complete",
                        argument,
                    ),
            );
        },
    );

    ipcMain.handle(
        IPC_CHANNELS.reopenCreationPhase,
        async (event, ...args: unknown[]) => {
            if (!trusted(event)) return untrustedFailure();
            return await captureValidated(
                () =>
                    validateSingleArgument(
                        args,
                        validateCreationPhaseReopenArgument,
                    ),
                (argument) =>
                    requestCreationAuthorityAction(
                        service,
                        "creation_phase.reopen",
                        argument,
                    ),
            );
        },
    );

    ipcMain.handle(
        IPC_CHANNELS.getCreationChangeset,
        async (event, ...args: unknown[]) => {
            if (!trusted(event)) return untrustedFailure();
            return await captureValidated(
                () => validateSingleArgument(args, validateChangesetIdArgument),
                ({ changesetId }) =>
                    requestCreationNamed(service, "creation_changeset.get", {
                        changeset_id: changesetId,
                    }),
            );
        },
    );

    ipcMain.handle(
        IPC_CHANNELS.diffCreationChangeset,
        async (event, ...args: unknown[]) => {
            if (!trusted(event)) return untrustedFailure();
            return await captureValidated(
                () => validateSingleArgument(args, validateChangesetIdArgument),
                ({ changesetId }) =>
                    requestCreationNamed(service, "creation_changeset.diff", {
                        changeset_id: changesetId,
                    }),
            );
        },
    );

    ipcMain.handle(
        IPC_CHANNELS.approveCreationChangeset,
        async (event, ...args: unknown[]) => {
            if (!trusted(event)) return untrustedFailure();
            return await captureValidated(
                () =>
                    validateSingleArgument(
                        args,
                        validateCreationChangesetActionArgument,
                    ),
                (argument) =>
                    requestCreationNamed(
                        service,
                        "creation_changeset.approve",
                        {
                            changeset_id: argument.changesetId,
                            expected_record_hash: argument.expectedRecordHash,
                            expected_review_sha256:
                                argument.expectedReviewSha256,
                        },
                    ),
            );
        },
    );

    ipcMain.handle(
        IPC_CHANNELS.applyCreationChangeset,
        async (event, ...args: unknown[]) => {
            if (!trusted(event)) return untrustedFailure();
            return await captureValidated(
                () =>
                    validateSingleArgument(
                        args,
                        validateCreationChangesetApplyArgument,
                    ),
                (argument) =>
                    requestCreationNamed(service, "creation_changeset.apply", {
                        changeset_id: argument.changesetId,
                        expected_record_hash: argument.expectedRecordHash,
                        expected_review_sha256: argument.expectedReviewSha256,
                        expected_root_generation:
                            argument.expectedRootGeneration,
                    }),
            );
        },
    );

    ipcMain.handle(
        IPC_CHANNELS.recoverCreationChangeset,
        async (event, ...args: unknown[]) => {
            if (!trusted(event)) return untrustedFailure();
            return await captureValidated(
                () =>
                    validateSingleArgument(
                        args,
                        validateCreationChangesetRecoveryArgument,
                    ),
                (argument) =>
                    requestCreationNamed(
                        service,
                        "creation_changeset.recover",
                        {
                            changeset_id: argument.changesetId,
                            mode: argument.mode,
                            expected_record_hash: argument.expectedRecordHash,
                            expected_review_sha256:
                                argument.expectedReviewSha256,
                            expected_root_generation:
                                argument.expectedRootGeneration,
                        },
                    ),
            );
        },
    );

    ipcMain.handle(
        IPC_CHANNELS.getWorkspaceOverview,
        async (event, ...args: unknown[]) => {
            if (!trusted(event)) return untrustedFailure();
            return await captureValidated(
                () => validateSingleArgument(args, validateWorkspaceArgument),
                ({ workspaceId }) =>
                    requestNamed(service, "workspace.overview", {
                        workspace_id: workspaceId,
                    }),
            );
        },
    );

    ipcMain.handle(
        IPC_CHANNELS.listSourceDocuments,
        async (event, ...args: unknown[]) => {
            if (!trusted(event)) return untrustedFailure();
            return await captureValidated(
                () => validateSingleArgument(args, validateWorkspaceArgument),
                ({ workspaceId }) =>
                    requestNamed(service, "source.list", {
                        workspace_id: workspaceId,
                    }),
            );
        },
    );

    ipcMain.handle(
        IPC_CHANNELS.readSourceDocument,
        async (event, ...args: unknown[]) => {
            if (!trusted(event)) return untrustedFailure();
            return await captureValidated(
                () => validateSingleArgument(args, validateSourceReadArgument),
                ({ workspaceId, path }) =>
                    requestNamed(service, "source.read", {
                        workspace_id: workspaceId,
                        path,
                    }),
            );
        },
    );

    ipcMain.handle(
        IPC_CHANNELS.listAssetCatalog,
        async (event, ...args: unknown[]) => {
            if (!trusted(event)) return untrustedFailure();
            return await captureValidated(
                () =>
                    validateSingleArgument(
                        args,
                        validateAssetCatalogListArgument,
                    ),
                (argument) => requestAssetCatalogList(service, argument),
            );
        },
    );

    ipcMain.handle(
        IPC_CHANNELS.inspectAssetCatalogEntry,
        async (event, ...args: unknown[]) => {
            if (!trusted(event)) return untrustedFailure();
            return await captureValidated(
                () =>
                    validateSingleArgument(
                        args,
                        validateAssetCatalogInspectArgument,
                    ),
                ({ workspaceId, manifestRevision, entryId }) =>
                    requestAssetCatalogInspect(
                        service,
                        workspaceId,
                        manifestRevision,
                        entryId,
                    ),
            );
        },
    );

    ipcMain.handle(
        IPC_CHANNELS.openAssetPreview,
        async (event, ...args: unknown[]) => {
            if (!trusted(event)) return untrustedFailure();
            return await captureValidated(
                () =>
                    validateSingleArgument(
                        args,
                        validateAssetPreviewOpenArgument,
                    ),
                (argument) =>
                    requestAssetPreviewOpen(service, assetPreviews, argument),
            );
        },
    );

    ipcMain.handle(
        IPC_CHANNELS.readAssetPreviewChunk,
        async (event, ...args: unknown[]) => {
            if (!trusted(event)) return untrustedFailure();
            return await captureValidated(
                () =>
                    validateSingleArgument(
                        args,
                        validateAssetPreviewReadArgument,
                    ),
                ({ handle, sequence }) =>
                    requestAssetPreviewRead(
                        service,
                        assetPreviews,
                        handle,
                        sequence,
                    ),
            );
        },
    );

    ipcMain.handle(
        IPC_CHANNELS.closeAssetPreview,
        async (event, ...args: unknown[]) => {
            if (!trusted(event)) return untrustedFailure();
            return await captureValidated(
                () =>
                    validateSingleArgument(
                        args,
                        validateAssetPreviewCloseArgument,
                    ),
                ({ handle }) =>
                    requestAssetPreviewClose(service, assetPreviews, handle),
            );
        },
    );

    ipcMain.handle(
        IPC_CHANNELS.stageSourceDocument,
        async (event, ...args: unknown[]) => {
            if (!trusted(event)) return untrustedFailure();
            return await captureValidated(
                () =>
                    validateSingleArgument(
                        args,
                        validateStageSourceDocumentArgument,
                    ),
                ({ workspaceId, path, baseSha256, content }) =>
                    requestStageSourceDocument(
                        service,
                        workspaceId,
                        path,
                        baseSha256,
                        content,
                    ),
            );
        },
    );

    ipcMain.handle(
        IPC_CHANNELS.getChangeset,
        async (event, ...args: unknown[]) => {
            if (!trusted(event)) return untrustedFailure();
            return await captureValidated(
                () => validateSingleArgument(args, validateChangesetIdArgument),
                ({ changesetId }) => requestChangesetGet(service, changesetId),
            );
        },
    );

    ipcMain.handle(
        IPC_CHANNELS.readChangesetDiff,
        async (event, ...args: unknown[]) => {
            if (!trusted(event)) return untrustedFailure();
            return await captureValidated(
                () => validateSingleArgument(args, validateChangesetIdArgument),
                ({ changesetId }) => requestChangesetDiff(service, changesetId),
            );
        },
    );

    ipcMain.handle(
        IPC_CHANNELS.approveChangeset,
        async (event, ...args: unknown[]) => {
            if (!trusted(event)) return untrustedFailure();
            return await captureValidated(
                () =>
                    validateSingleArgument(
                        args,
                        validateChangesetActionArgument,
                    ),
                ({ changesetId, expectedReviewSha256 }) =>
                    requestChangesetAction(
                        service,
                        "changeset.approve",
                        "approved",
                        changesetId,
                        expectedReviewSha256,
                    ),
            );
        },
    );

    ipcMain.handle(
        IPC_CHANNELS.rejectChangeset,
        async (event, ...args: unknown[]) => {
            if (!trusted(event)) return untrustedFailure();
            return await captureValidated(
                () =>
                    validateSingleArgument(
                        args,
                        validateChangesetActionArgument,
                    ),
                ({ changesetId, expectedReviewSha256 }) =>
                    requestChangesetAction(
                        service,
                        "changeset.reject",
                        "rejected",
                        changesetId,
                        expectedReviewSha256,
                    ),
            );
        },
    );

    ipcMain.handle(
        IPC_CHANNELS.applyChangeset,
        async (event, ...args: unknown[]) => {
            if (!trusted(event)) return untrustedFailure();
            return await captureValidated(
                () =>
                    validateSingleArgument(
                        args,
                        validateChangesetActionArgument,
                    ),
                ({ changesetId, expectedReviewSha256 }) =>
                    requestChangesetAction(
                        service,
                        "changeset.apply",
                        "applied",
                        changesetId,
                        expectedReviewSha256,
                    ),
            );
        },
    );

    ipcMain.handle(
        IPC_CHANNELS.validateWorld,
        async (event, ...args: unknown[]) => {
            if (!trusted(event)) return untrustedFailure();
            return await captureValidated(
                () => validateSingleArgument(args, validateWorkspaceArgument),
                ({ workspaceId }) =>
                    requestNamed(service, "world.validate", {
                        workspace_id: workspaceId,
                    }),
            );
        },
    );

    ipcMain.handle(
        IPC_CHANNELS.analyzeWorld,
        async (event, ...args: unknown[]) => {
            if (!trusted(event)) return untrustedFailure();
            return await captureValidated(
                () => validateSingleArgument(args, validateWorkspaceArgument),
                ({ workspaceId }) =>
                    requestNamed(service, "world.analyze", {
                        workspace_id: workspaceId,
                    }),
            );
        },
    );

    ipcMain.handle(
        IPC_CHANNELS.validateAssetReceipt,
        async (event, ...args: unknown[]) => {
            if (!trusted(event)) return untrustedFailure();
            return await captureValidated(
                () =>
                    validateSingleArgument(args, validateAssetReceiptArgument),
                ({ workspaceId, input }) =>
                    requestJobCreate(
                        service,
                        workspaceId,
                        "asset.receipt.validate",
                        input,
                    ),
            );
        },
    );

    ipcMain.handle(
        IPC_CHANNELS.verifyAssetpack,
        async (event, ...args: unknown[]) => {
            if (!trusted(event)) return untrustedFailure();
            return await captureValidated(
                () => validateSingleArgument(args, validateAssetpackArgument),
                ({ workspaceId, input }) =>
                    requestJobCreate(
                        service,
                        workspaceId,
                        "assetpack.verify",
                        input,
                    ),
            );
        },
    );

    ipcMain.handle(
        IPC_CHANNELS.runHeadless,
        async (event, ...args: unknown[]) => {
            if (!trusted(event)) return untrustedFailure();
            return await captureValidated(
                () => validateSingleArgument(args, validateHeadlessArgument),
                ({ workspaceId, input }) =>
                    requestJobCreate(
                        service,
                        workspaceId,
                        "runtime.headless",
                        input,
                    ),
            );
        },
    );

    ipcMain.handle(
        IPC_CHANNELS.runReplay,
        async (event, ...args: unknown[]) => {
            if (!trusted(event)) return untrustedFailure();
            return await captureValidated(
                () => validateSingleArgument(args, validateReplayArgument),
                ({ workspaceId, input }) =>
                    requestJobCreate(
                        service,
                        workspaceId,
                        "runtime.replay",
                        input,
                    ),
            );
        },
    );

    ipcMain.handle(
        IPC_CHANNELS.cancelJob,
        async (event, ...args: unknown[]) => {
            if (!trusted(event)) return untrustedFailure();
            return await captureValidated(
                () => validateSingleArgument(args, validateCancelJobArgument),
                ({ jobId }) =>
                    requestNamed(service, "job.cancel", { job_id: jobId }),
            );
        },
    );

    ipcMain.handle(
        IPC_CHANNELS.createExternalGrant,
        async (event, ...args: unknown[]) => {
            if (!trusted(event)) return untrustedFailure();
            return await captureValidated(
                () =>
                    validateSingleArgument(
                        args,
                        validateExternalGrantCreateArgument,
                    ),
                async (argument) => {
                    let selectedPath: string | null;
                    try {
                        selectedPath = await selectExternalArtifactPath(
                            dialogs,
                            window,
                            argument,
                        );
                    } catch {
                        return failure(
                            "internal_error",
                            "External artifact selection failed",
                        );
                    }
                    if (selectedPath === null) {
                        return failure(
                            "cancelled",
                            "External artifact selection was cancelled",
                        );
                    }
                    return await requestExternalGrantCreate(
                        service,
                        argument,
                        selectedPath,
                    );
                },
            );
        },
    );

    ipcMain.handle(
        IPC_CHANNELS.getExternalGrant,
        async (event, ...args: unknown[]) => {
            if (!trusted(event)) return untrustedFailure();
            return await captureValidated(
                () =>
                    validateSingleArgument(
                        args,
                        validateExternalGrantIdArgument,
                    ),
                ({ grantId }) =>
                    requestExternalGrantById(
                        service,
                        "external_grant.get",
                        grantId,
                    ),
            );
        },
    );

    ipcMain.handle(
        IPC_CHANNELS.revokeExternalGrant,
        async (event, ...args: unknown[]) => {
            if (!trusted(event)) return untrustedFailure();
            return await captureValidated(
                () =>
                    validateSingleArgument(
                        args,
                        validateExternalGrantIdArgument,
                    ),
                ({ grantId }) =>
                    requestExternalGrantById(
                        service,
                        "external_grant.revoke",
                        grantId,
                    ),
            );
        },
    );

    ipcMain.handle(
        IPC_CHANNELS.materializeGame,
        async (event, ...args: unknown[]) => {
            if (!trusted(event)) return untrustedFailure();
            return await captureValidated(
                () =>
                    validateSingleArgument(
                        args,
                        validateMaterializeGameArgument,
                    ),
                ({
                    workspaceId,
                    sourceGrantId,
                    targetGrantId,
                    expectedMaterializationHash,
                }) =>
                    requestExternalJobCreate(
                        service,
                        workspaceId,
                        "game.materialize",
                        {
                            source_grant_id: sourceGrantId,
                            target_grant_id: targetGrantId,
                            expected_materialization_hash:
                                expectedMaterializationHash,
                        },
                    ),
            );
        },
    );

    ipcMain.handle(
        IPC_CHANNELS.packageGame,
        async (event, ...args: unknown[]) => {
            if (!trusted(event)) return untrustedFailure();
            return await captureValidated(
                () => validateSingleArgument(args, validatePackageGameArgument),
                ({
                    workspaceId,
                    sourceGrantId,
                    targetGrantId,
                    expectedGameHash,
                }) =>
                    requestExternalJobCreate(
                        service,
                        workspaceId,
                        "game.package",
                        {
                            source_grant_id: sourceGrantId,
                            target_grant_id: targetGrantId,
                            expected_game_hash: expectedGameHash,
                        },
                    ),
            );
        },
    );

    ipcMain.handle(
        IPC_CHANNELS.extractGamePackage,
        async (event, ...args: unknown[]) => {
            if (!trusted(event)) return untrustedFailure();
            return await captureValidated(
                () =>
                    validateSingleArgument(
                        args,
                        validateExtractGamePackageArgument,
                    ),
                ({
                    workspaceId,
                    sourceGrantId,
                    targetGrantId,
                    expectedPackageHash,
                }) =>
                    requestExternalJobCreate(
                        service,
                        workspaceId,
                        "game.package.extract",
                        {
                            source_grant_id: sourceGrantId,
                            target_grant_id: targetGrantId,
                            expected_package_hash: expectedPackageHash,
                        },
                    ),
            );
        },
    );

    ipcMain.handle(
        IPC_CHANNELS.getExternalJob,
        async (event, ...args: unknown[]) => {
            if (!trusted(event)) return untrustedFailure();
            return await captureValidated(
                () =>
                    validateSingleArgument(args, validateExternalJobIdArgument),
                ({ jobId }) =>
                    requestExternalJobById(service, "job.get", jobId),
            );
        },
    );

    ipcMain.handle(
        IPC_CHANNELS.listExternalJobs,
        async (event, value: unknown = {}) => {
            if (!trusted(event)) return untrustedFailure();
            return await captureValidated(
                () => validateExternalJobsListParams(value),
                (params) => requestExternalJobList(service, params),
            );
        },
    );

    ipcMain.handle(
        IPC_CHANNELS.cancelExternalJob,
        async (event, ...args: unknown[]) => {
            if (!trusted(event)) return untrustedFailure();
            return await captureValidated(
                () =>
                    validateSingleArgument(args, validateExternalJobIdArgument),
                ({ jobId }) =>
                    requestExternalJobById(service, "job.cancel", jobId),
            );
        },
    );

    ipcMain.handle(
        IPC_CHANNELS.recoverExternalJob,
        async (event, ...args: unknown[]) => {
            if (!trusted(event)) return untrustedFailure();
            return await captureValidated(
                () =>
                    validateSingleArgument(
                        args,
                        validateExternalJobRecoveryArgument,
                    ),
                ({ jobId, action }) =>
                    requestExternalJobRecovery(service, jobId, action),
            );
        },
    );

    ipcMain.handle(IPC_CHANNELS.codexStatus, (event, ...args: unknown[]) => {
        const invalid = rejectUntrustedOrUnexpectedArguments(
            trusted(event),
            args,
        );
        return invalid ?? success(codex.status);
    });
    ipcMain.handle(
        IPC_CHANNELS.codexBindWorkspace,
        async (event, value: unknown) =>
            trusted(event)
                ? await captureValidated(
                      () => validateWorkspaceArgument(value),
                      (params) =>
                          capture(() =>
                              codex.bindWorkspace(params.workspaceId),
                          ),
                  )
                : failure(
                      "invalid_request",
                      "Rejected Studio IPC from an untrusted sender",
                  ),
    );
    ipcMain.handle(
        IPC_CHANNELS.codexReadAccount,
        async (event, ...args: unknown[]) => {
            const invalid = rejectUntrustedOrUnexpectedArguments(
                trusted(event),
                args,
            );
            return invalid ?? (await capture(() => codex.readAccount()));
        },
    );
    ipcMain.handle(
        IPC_CHANNELS.codexStartLogin,
        async (event, value: unknown) =>
            trusted(event)
                ? await captureValidated(
                      () => validateLoginArgument(value),
                      (params) => capture(() => codex.startLogin(params.mode)),
                  )
                : failure(
                      "invalid_request",
                      "Rejected Studio IPC from an untrusted sender",
                  ),
    );
    ipcMain.handle(
        IPC_CHANNELS.codexStartThread,
        async (event, ...args: unknown[]) => {
            const invalid = rejectUntrustedOrUnexpectedArguments(
                trusted(event),
                args,
            );
            return invalid ?? (await capture(() => codex.startThread()));
        },
    );
    ipcMain.handle(
        IPC_CHANNELS.codexResumeThread,
        async (event, value: unknown) =>
            trusted(event)
                ? await captureValidated(
                      () => validateThreadArgument(value),
                      (params) =>
                          capture(() => codex.resumeThread(params.threadId)),
                  )
                : failure(
                      "invalid_request",
                      "Rejected Studio IPC from an untrusted sender",
                  ),
    );
    ipcMain.handle(
        IPC_CHANNELS.codexForkThread,
        async (event, value: unknown) =>
            trusted(event)
                ? await captureValidated(
                      () => validateThreadArgument(value),
                      (params) =>
                          capture(() => codex.forkThread(params.threadId)),
                  )
                : failure(
                      "invalid_request",
                      "Rejected Studio IPC from an untrusted sender",
                  ),
    );
    ipcMain.handle(
        IPC_CHANNELS.codexStartTurn,
        async (event, value: unknown) =>
            trusted(event)
                ? await captureValidated(
                      () => validateStartTurnArgument(value),
                      (params) =>
                          capture(() =>
                              codex.startTurn(params.threadId, params.text),
                          ),
                  )
                : failure(
                      "invalid_request",
                      "Rejected Studio IPC from an untrusted sender",
                  ),
    );
    ipcMain.handle(
        IPC_CHANNELS.codexSteerTurn,
        async (event, value: unknown) =>
            trusted(event)
                ? await captureValidated(
                      () => validateSteerTurnArgument(value),
                      (params) =>
                          capture(() =>
                              codex.steerTurn(
                                  params.threadId,
                                  params.turnId,
                                  params.text,
                              ),
                          ),
                  )
                : failure(
                      "invalid_request",
                      "Rejected Studio IPC from an untrusted sender",
                  ),
    );
    ipcMain.handle(
        IPC_CHANNELS.codexInterruptTurn,
        async (event, value: unknown) =>
            trusted(event)
                ? await captureValidated(
                      () => validateInterruptTurnArgument(value),
                      (params) =>
                          capture(() =>
                              codex.interruptTurn(
                                  params.threadId,
                                  params.turnId,
                              ),
                          ),
                  )
                : failure(
                      "invalid_request",
                      "Rejected Studio IPC from an untrusted sender",
                  ),
    );
    ipcMain.handle(
        IPC_CHANNELS.codexAnswerUserInput,
        async (event, value: unknown) =>
            trusted(event)
                ? await captureValidated(
                      () => validateUserInputArgument(value),
                      (params) =>
                          capture(() =>
                              codex.answerUserInput(
                                  params.token,
                                  params.answers,
                              ),
                          ),
                  )
                : failure(
                      "invalid_request",
                      "Rejected Studio IPC from an untrusted sender",
                  ),
    );

    const unsubscribe = service.subscribe((activity) => {
        if (!window.isDestroyed() && !window.webContents.isDestroyed()) {
            window.webContents.send(IPC_CHANNELS.event, activity);
        }
    });
    const unsubscribeCodex = codex.subscribe((activity) => {
        if (!window.isDestroyed() && !window.webContents.isDestroyed()) {
            window.webContents.send(IPC_CHANNELS.codexEvent, activity);
        }
    });

    return () => {
        unsubscribe();
        unsubscribeCodex();
        ipcMain.removeHandler(IPC_CHANNELS.initialize);
        ipcMain.removeHandler(IPC_CHANNELS.status);
        ipcMain.removeHandler(IPC_CHANNELS.listWorkspaces);
        ipcMain.removeHandler(IPC_CHANNELS.listEvents);
        ipcMain.removeHandler(IPC_CHANNELS.listChangesets);
        ipcMain.removeHandler(IPC_CHANNELS.listJobs);
        ipcMain.removeHandler(IPC_CHANNELS.listCreationWorkspaces);
        ipcMain.removeHandler(IPC_CHANNELS.registerCreationProject);
        ipcMain.removeHandler(IPC_CHANNELS.createCreationProject);
        ipcMain.removeHandler(IPC_CHANNELS.openCreationWorkspace);
        ipcMain.removeHandler(IPC_CHANNELS.listCreationDocuments);
        ipcMain.removeHandler(IPC_CHANNELS.readCreationDocument);
        ipcMain.removeHandler(IPC_CHANNELS.getCreationWorkflow);
        ipcMain.removeHandler(IPC_CHANNELS.inspectCreationReadiness);
        ipcMain.removeHandler(IPC_CHANNELS.listCreationArtifacts);
        ipcMain.removeHandler(IPC_CHANNELS.inspectCreationArtifact);
        ipcMain.removeHandler(IPC_CHANNELS.inspectCreationEvidence);
        ipcMain.removeHandler(IPC_CHANNELS.openCreationPreview);
        ipcMain.removeHandler(IPC_CHANNELS.readCreationPreviewChunk);
        ipcMain.removeHandler(IPC_CHANNELS.closeCreationPreview);
        ipcMain.removeHandler(IPC_CHANNELS.compileCreationProject);
        ipcMain.removeHandler(IPC_CHANNELS.admitCreationArtifact);
        ipcMain.removeHandler(IPC_CHANNELS.processCreationAsset);
        ipcMain.removeHandler(IPC_CHANNELS.selectCreationAssetpackOutput);
        ipcMain.removeHandler(IPC_CHANNELS.selectCreationRuntimeBundleOutput);
        ipcMain.removeHandler(
            IPC_CHANNELS.selectCreationMaterializationBundleOutput,
        );
        ipcMain.removeHandler(IPC_CHANNELS.selectCreationStandaloneGameOutput);
        ipcMain.removeHandler(IPC_CHANNELS.selectCreationGamePackageOutput);
        ipcMain.removeHandler(
            IPC_CHANNELS.selectCreationGamePackageExtractionOutput,
        );
        ipcMain.removeHandler(IPC_CHANNELS.getCreationAssetpackOutput);
        ipcMain.removeHandler(IPC_CHANNELS.getCreationAuthorityCapabilities);
        ipcMain.removeHandler(IPC_CHANNELS.listCreationOutputGrants);
        ipcMain.removeHandler(IPC_CHANNELS.listCreationAuthorityOutputGrants);
        ipcMain.removeHandler(IPC_CHANNELS.revokeCreationAssetpackOutput);
        ipcMain.removeHandler(IPC_CHANNELS.sealCreationAssetRelease);
        ipcMain.removeHandler(IPC_CHANNELS.composeCreationRuntime);
        ipcMain.removeHandler(IPC_CHANNELS.buildCreationRuntimeBundle);
        ipcMain.removeHandler(IPC_CHANNELS.buildCreationMaterializationBundle);
        ipcMain.removeHandler(IPC_CHANNELS.materializeCreationGame);
        ipcMain.removeHandler(IPC_CHANNELS.packageCreationGame);
        ipcMain.removeHandler(IPC_CHANNELS.extractCreationGamePackage);
        ipcMain.removeHandler(IPC_CHANNELS.reviewCreationAssetQa);
        ipcMain.removeHandler(IPC_CHANNELS.authorizeCreationAssetRelease);
        ipcMain.removeHandler(
            IPC_CHANNELS.selectCreationHeadlessEvidenceOutput,
        );
        ipcMain.removeHandler(IPC_CHANNELS.verifyCreationHeadless);
        ipcMain.removeHandler(IPC_CHANNELS.requestCreationJobCancel);
        ipcMain.removeHandler(IPC_CHANNELS.requestCreationJobRecovery);
        ipcMain.removeHandler(IPC_CHANNELS.getCreationJob);
        ipcMain.removeHandler(IPC_CHANNELS.listCreationJobs);
        ipcMain.removeHandler(IPC_CHANNELS.cancelCreationJob);
        ipcMain.removeHandler(IPC_CHANNELS.recoverCreationJob);
        ipcMain.removeHandler(IPC_CHANNELS.listCreationEvents);
        ipcMain.removeHandler(IPC_CHANNELS.stageCreationProfile);
        ipcMain.removeHandler(IPC_CHANNELS.stageCreationModuleChange);
        ipcMain.removeHandler(IPC_CHANNELS.reconcileCreationWorkflow);
        ipcMain.removeHandler(IPC_CHANNELS.readCreationPhase);
        ipcMain.removeHandler(IPC_CHANNELS.validateCreationPhase);
        ipcMain.removeHandler(IPC_CHANNELS.completeCreationPhase);
        ipcMain.removeHandler(IPC_CHANNELS.reopenCreationPhase);
        ipcMain.removeHandler(IPC_CHANNELS.getCreationChangeset);
        ipcMain.removeHandler(IPC_CHANNELS.diffCreationChangeset);
        ipcMain.removeHandler(IPC_CHANNELS.approveCreationChangeset);
        ipcMain.removeHandler(IPC_CHANNELS.applyCreationChangeset);
        ipcMain.removeHandler(IPC_CHANNELS.recoverCreationChangeset);
        ipcMain.removeHandler(IPC_CHANNELS.getWorkspaceOverview);
        ipcMain.removeHandler(IPC_CHANNELS.listSourceDocuments);
        ipcMain.removeHandler(IPC_CHANNELS.readSourceDocument);
        ipcMain.removeHandler(IPC_CHANNELS.listAssetCatalog);
        ipcMain.removeHandler(IPC_CHANNELS.inspectAssetCatalogEntry);
        ipcMain.removeHandler(IPC_CHANNELS.openAssetPreview);
        ipcMain.removeHandler(IPC_CHANNELS.readAssetPreviewChunk);
        ipcMain.removeHandler(IPC_CHANNELS.closeAssetPreview);
        ipcMain.removeHandler(IPC_CHANNELS.stageSourceDocument);
        ipcMain.removeHandler(IPC_CHANNELS.getChangeset);
        ipcMain.removeHandler(IPC_CHANNELS.readChangesetDiff);
        ipcMain.removeHandler(IPC_CHANNELS.approveChangeset);
        ipcMain.removeHandler(IPC_CHANNELS.rejectChangeset);
        ipcMain.removeHandler(IPC_CHANNELS.applyChangeset);
        ipcMain.removeHandler(IPC_CHANNELS.validateWorld);
        ipcMain.removeHandler(IPC_CHANNELS.analyzeWorld);
        ipcMain.removeHandler(IPC_CHANNELS.validateAssetReceipt);
        ipcMain.removeHandler(IPC_CHANNELS.verifyAssetpack);
        ipcMain.removeHandler(IPC_CHANNELS.runHeadless);
        ipcMain.removeHandler(IPC_CHANNELS.runReplay);
        ipcMain.removeHandler(IPC_CHANNELS.cancelJob);
        ipcMain.removeHandler(IPC_CHANNELS.createExternalGrant);
        ipcMain.removeHandler(IPC_CHANNELS.getExternalGrant);
        ipcMain.removeHandler(IPC_CHANNELS.revokeExternalGrant);
        ipcMain.removeHandler(IPC_CHANNELS.materializeGame);
        ipcMain.removeHandler(IPC_CHANNELS.packageGame);
        ipcMain.removeHandler(IPC_CHANNELS.extractGamePackage);
        ipcMain.removeHandler(IPC_CHANNELS.getExternalJob);
        ipcMain.removeHandler(IPC_CHANNELS.listExternalJobs);
        ipcMain.removeHandler(IPC_CHANNELS.cancelExternalJob);
        ipcMain.removeHandler(IPC_CHANNELS.recoverExternalJob);
        ipcMain.removeHandler(IPC_CHANNELS.codexStatus);
        ipcMain.removeHandler(IPC_CHANNELS.codexBindWorkspace);
        ipcMain.removeHandler(IPC_CHANNELS.codexReadAccount);
        ipcMain.removeHandler(IPC_CHANNELS.codexStartLogin);
        ipcMain.removeHandler(IPC_CHANNELS.codexStartThread);
        ipcMain.removeHandler(IPC_CHANNELS.codexResumeThread);
        ipcMain.removeHandler(IPC_CHANNELS.codexForkThread);
        ipcMain.removeHandler(IPC_CHANNELS.codexStartTurn);
        ipcMain.removeHandler(IPC_CHANNELS.codexSteerTurn);
        ipcMain.removeHandler(IPC_CHANNELS.codexInterruptTurn);
        ipcMain.removeHandler(IPC_CHANNELS.codexAnswerUserInput);
        assetPreviews.clear();
    };
}

export function validateWorkspaceArgument(value: unknown): {
    workspaceId: string;
} {
    const params = validateClosedParams(value, ["workspaceId"]);
    return { workspaceId: validateWorkspaceId(params.workspaceId) };
}

export type CreationProjectCreateArgument = StudioCreationProjectCreateParams;

export function validateCreationProjectCreateArgument(
    value: unknown,
): CreationProjectCreateArgument {
    const params = validateClosedParams(value, [
        "projectKind",
        "projectId",
        "title",
        "defaultLocale",
        "projectVersion",
        "gameplayFamily",
        "initialCoreVerb",
        "initialCoreLoop",
        "worldPresence",
        "narrativeRequirement",
        "narrativeAuthorship",
        "narrativeTopology",
        "presentationMode",
        "runtimeSupportIntent",
        "assetContentMode",
    ]);
    if (
        params.projectKind !== "game" &&
        params.projectKind !== "asset_library" &&
        params.projectKind !== "universe_library"
    ) {
        throw new TypeError("Studio creation project kind is invalid");
    }
    const projectId = validateEntityId(params.projectId, "creation project");
    const title = validateCreationScaffoldText(
        params.title,
        "creation project title",
        256,
    );
    if (
        typeof params.defaultLocale !== "string" ||
        !/^[a-z]{2,3}(?:-[A-Z]{2})?$/u.test(params.defaultLocale)
    ) {
        throw new TypeError("Studio creation project locale is invalid");
    }
    if (
        typeof params.projectVersion !== "string" ||
        !/^(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?$/u.test(
            params.projectVersion,
        )
    ) {
        throw new TypeError("Studio creation project version is invalid");
    }
    const base = {
        projectKind: params.projectKind,
        projectId,
        title,
        defaultLocale: params.defaultLocale,
        projectVersion: params.projectVersion,
    };
    const facetNames = [
        "gameplayFamily",
        "initialCoreVerb",
        "initialCoreLoop",
        "worldPresence",
        "narrativeRequirement",
        "narrativeAuthorship",
        "narrativeTopology",
        "presentationMode",
        "runtimeSupportIntent",
    ] as const;
    if (params.projectKind !== "game") {
        if (facetNames.some((field) => field in params) || "assetContentMode" in params) {
            throw new TypeError("Studio library creation cannot include game facets");
        }
        return base as CreationProjectCreateArgument;
    }
    if (!facetNames.every((field) => field in params)) {
        throw new TypeError("Studio game creation requires every initial facet");
    }
    const gameplayFamilies = new Set([
        "action",
        "adventure",
        "educational",
        "narrative",
        "puzzle",
        "rhythm",
        "role_playing",
        "sandbox",
        "simulation",
        "sports",
        "strategy",
    ]);
    const worldPresences = new Set(["none", "abstract", "symbolic", "diegetic"]);
    const narrativeRequirements = new Set(["none", "optional", "required"]);
    const narrativeAuthorship = new Set([
        "none",
        "authored",
        "emergent",
        "procedural",
        "player_authored",
        "social",
        "hybrid",
    ]);
    const narrativeTopologies = new Set([
        "none",
        "linear",
        "foldback",
        "branching",
        "branch_and_bottleneck",
        "hub_and_spoke",
        "modular",
        "storylet",
        "loop_reset",
        "episodic",
        "seasonal",
        "open_ended",
    ]);
    const presentationModes = new Set(["text", "2d", "2_5d", "3d", "mixed", "vr", "ar"]);
    const runtimeSupportIntents = new Set(["authoring_only", "compatibility_assessment"]);
    if (!gameplayFamilies.has(params.gameplayFamily as string)) {
        throw new TypeError("Studio game gameplay family is invalid");
    }
    if (
        typeof params.initialCoreVerb !== "string" ||
        !/^[a-z][a-z0-9_]{1,63}$/u.test(params.initialCoreVerb)
    ) {
        throw new TypeError("Studio initial core verb is invalid");
    }
    const initialCoreVerb = params.initialCoreVerb;
    const initialCoreLoop = validateCreationScaffoldText(
        params.initialCoreLoop,
        "initial core loop",
        512,
    );
    if (!worldPresences.has(params.worldPresence as string)) {
        throw new TypeError("Studio game world presence is invalid");
    }
    if (!narrativeRequirements.has(params.narrativeRequirement as string)) {
        throw new TypeError("Studio game narrative requirement is invalid");
    }
    if (!narrativeAuthorship.has(params.narrativeAuthorship as string)) {
        throw new TypeError("Studio game narrative authorship is invalid");
    }
    if (!narrativeTopologies.has(params.narrativeTopology as string)) {
        throw new TypeError("Studio game narrative topology is invalid");
    }
    if (
        params.narrativeRequirement === "none" &&
        (params.narrativeAuthorship !== "none" || params.narrativeTopology !== "none")
    ) {
        throw new TypeError("Studio narrative:none has incompatible facets");
    }
    if (
        params.narrativeRequirement !== "none" &&
        (params.narrativeAuthorship === "none" || params.narrativeTopology === "none")
    ) {
        throw new TypeError("Studio narrative facets must be explicit");
    }
    if (!presentationModes.has(params.presentationMode as string)) {
        throw new TypeError("Studio game presentation mode is invalid");
    }
    if (!runtimeSupportIntents.has(params.runtimeSupportIntent as string)) {
        throw new TypeError("Studio game runtime support intent is invalid");
    }
    const assetContentMode =
        "assetContentMode" in params
            ? params.assetContentMode
            : DEFAULT_CREATION_CONTENT_MODE;
    if (!isCreationContentMode(assetContentMode)) {
        throw new TypeError("Studio game asset content mode is invalid");
    }
    return {
        ...base,
        projectKind: "game",
        gameplayFamily: params.gameplayFamily,
        initialCoreVerb,
        initialCoreLoop,
        worldPresence: params.worldPresence,
        narrativeRequirement: params.narrativeRequirement,
        narrativeAuthorship: params.narrativeAuthorship,
        narrativeTopology: params.narrativeTopology,
        presentationMode: params.presentationMode,
        runtimeSupportIntent: params.runtimeSupportIntent,
        ...("assetContentMode" in params ? { assetContentMode } : {}),
    } as CreationProjectCreateArgument;
}

export function validateCreationRevisionArgument(value: unknown): {
    workspaceId: string;
    expectedSourceRevision: string;
} {
    const params = validateClosedParams(value, [
        "workspaceId",
        "expectedSourceRevision",
    ]);
    return {
        workspaceId: validateWorkspaceId(params.workspaceId),
        expectedSourceRevision: validateSha256(
            params.expectedSourceRevision,
            "creation source revision",
        ),
    };
}

export function validateCreationDocumentArgument(value: unknown): {
    workspaceId: string;
    expectedSourceRevision: string;
    path: string;
} {
    const params = validateClosedParams(value, [
        "workspaceId",
        "expectedSourceRevision",
        "path",
    ]);
    if (
        typeof params.path !== "string" ||
        !isPortableRelativePath(params.path)
    ) {
        throw new TypeError("Studio creation document path is invalid");
    }
    return {
        workspaceId: validateWorkspaceId(params.workspaceId),
        expectedSourceRevision: validateSha256(
            params.expectedSourceRevision,
            "creation source revision",
        ),
        path: params.path,
    };
}

export interface CreationProfileStageArgument {
    workspaceId: string;
    expectedRootGeneration: number;
    expectedSourceRevision: string;
    expectedWorkflowStatusHash: string | null;
    path: string;
    expectedBaseFileSha256: string;
    proposedProfile: Record<string, unknown>;
}

export function validateCreationProfileStageArgument(
    value: unknown,
): CreationProfileStageArgument {
    const params = validateClosedParams(value, [
        "workspaceId",
        "expectedRootGeneration",
        "expectedSourceRevision",
        "expectedWorkflowStatusHash",
        "path",
        "expectedBaseFileSha256",
        "proposedProfile",
    ]);
    if (
        typeof params.path !== "string" ||
        !isPortableRelativePath(params.path)
    ) {
        throw new TypeError("Studio creation profile path is invalid");
    }
    if (!isRecord(params.proposedProfile)) {
        throw new TypeError(
            "Studio proposed creation profile must be an object",
        );
    }
    return {
        workspaceId: validateWorkspaceId(params.workspaceId),
        expectedRootGeneration: validateGeneration(
            params.expectedRootGeneration,
            "creation root generation",
        ),
        expectedSourceRevision: validateSha256(
            params.expectedSourceRevision,
            "creation source revision",
        ),
        expectedWorkflowStatusHash: validateNullableSha256(
            params.expectedWorkflowStatusHash,
            "creation workflow status hash",
        ),
        path: params.path,
        expectedBaseFileSha256: validateSha256(
            params.expectedBaseFileSha256,
            "creation base file SHA-256",
        ),
        proposedProfile: normalizeJsonObject(
            params.proposedProfile,
            "proposed creation profile",
        ),
    };
}

type CreationModuleFormat = keyof typeof CREATION_MODULE_COLLECTIONS;

export interface CreationAuthorityArgument {
    workspaceId: string;
    expectedRootGeneration: number;
    expectedSourceRevision: string;
    expectedWorkflowStatusHash: string | null;
}

export interface CreationEvidenceAuthorityArgument extends CreationAuthorityArgument {
    expectedArtifactSnapshotHash: string | null;
}

export interface CreationArtifactListArgument extends CreationEvidenceAuthorityArgument {
    lifecycle: "active" | "invalidated" | "historical" | "candidate" | null;
    cursor: string | null;
    limit: number;
}

export interface CreationArtifactInspectArgument extends CreationEvidenceAuthorityArgument {
    expectedArtifactSnapshotHash: string;
    artifactId: string;
}

export function validateCreationArtifactListArgument(
    value: unknown,
): CreationArtifactListArgument {
    const params = validateClosedParams(value, [
        "workspaceId",
        "expectedRootGeneration",
        "expectedSourceRevision",
        "expectedWorkflowStatusHash",
        "expectedArtifactSnapshotHash",
        "lifecycle",
        "cursor",
        "limit",
    ]);
    const authority = validateCreationEvidenceAuthorityFields(params, false);
    if (
        params.lifecycle !== null &&
        (typeof params.lifecycle !== "string" ||
            !CREATION_ARTIFACT_LIFECYCLES.has(params.lifecycle))
    ) {
        throw new TypeError("Studio creation artifact lifecycle is invalid");
    }
    if (
        !Number.isSafeInteger(params.limit) ||
        Number(params.limit) < 1 ||
        Number(params.limit) > MAX_CREATION_ARTIFACT_PAGE_SIZE
    ) {
        throw new TypeError("Studio creation artifact page limit is invalid");
    }
    return {
        ...authority,
        lifecycle:
            params.lifecycle as CreationArtifactListArgument["lifecycle"],
        cursor:
            params.cursor === null
                ? null
                : validateEntityId(params.cursor, "creation artifact cursor"),
        limit: Number(params.limit),
    };
}

export function validateCreationArtifactInspectArgument(
    value: unknown,
): CreationArtifactInspectArgument {
    const params = validateClosedParams(value, [
        "workspaceId",
        "expectedRootGeneration",
        "expectedSourceRevision",
        "expectedWorkflowStatusHash",
        "expectedArtifactSnapshotHash",
        "artifactId",
    ]);
    const authority = validateCreationEvidenceAuthorityFields(params, true);
    return {
        ...authority,
        expectedArtifactSnapshotHash: validateSha256(
            authority.expectedArtifactSnapshotHash,
            "creation artifact snapshot hash",
        ),
        artifactId: validateEntityId(params.artifactId, "creation artifact"),
    };
}

export function validateCreationEvidenceInspectArgument(
    value: unknown,
): CreationEvidenceAuthorityArgument {
    const params = validateClosedParams(value, [
        "workspaceId",
        "expectedRootGeneration",
        "expectedSourceRevision",
        "expectedWorkflowStatusHash",
        "expectedArtifactSnapshotHash",
    ]);
    return validateCreationEvidenceAuthorityFields(params, false);
}

export interface CreationPreviewOpenArgument extends CreationEvidenceAuthorityArgument {
    expectedArtifactSnapshotHash: string;
    assetpackArtifactId: string;
    outputGrantId: string;
    expectedOutputGrantGeneration: number;
    assetId: string;
}

export function validateCreationPreviewOpenArgument(
    value: unknown,
): CreationPreviewOpenArgument {
    const params = validateClosedParams(value, [
        "workspaceId",
        "expectedRootGeneration",
        "expectedSourceRevision",
        "expectedWorkflowStatusHash",
        "expectedArtifactSnapshotHash",
        "assetpackArtifactId",
        "outputGrantId",
        "expectedOutputGrantGeneration",
        "assetId",
    ]);
    const authority = validateCreationEvidenceAuthorityFields(params, true);
    return {
        ...authority,
        expectedArtifactSnapshotHash: validateSha256(
            authority.expectedArtifactSnapshotHash,
            "creation preview artifact snapshot hash",
        ),
        assetpackArtifactId: validateEntityId(
            params.assetpackArtifactId,
            "creation preview assetpack artifact",
        ),
        outputGrantId: validateEntityId(
            params.outputGrantId,
            "creation preview output grant",
        ),
        expectedOutputGrantGeneration: validateGeneration(
            params.expectedOutputGrantGeneration,
            "creation preview output grant generation",
        ),
        assetId: validateEntityId(params.assetId, "creation preview asset"),
    };
}

export function validateCreationPreviewReadArgument(value: unknown): {
    handle: string;
    sequence: number;
} {
    const params = validateClosedParams(value, ["handle", "sequence"]);
    if (
        !Number.isSafeInteger(params.sequence) ||
        (params.sequence as number) < 0 ||
        (params.sequence as number) > MAX_CREATION_PREVIEW_SEQUENCE
    ) {
        throw new TypeError(
            `Studio creation preview sequence must be an integer from 0 to ${MAX_CREATION_PREVIEW_SEQUENCE}`,
        );
    }
    return {
        handle: validateAssetPreviewHandle(params.handle),
        sequence: params.sequence as number,
    };
}

export function validateCreationPreviewCloseArgument(value: unknown): {
    handle: string;
} {
    const params = validateClosedParams(value, ["handle"]);
    return { handle: validateAssetPreviewHandle(params.handle) };
}

export interface CreationJobAuthorityArgument extends CreationAuthorityArgument {
    expectedArtifactSnapshotHash: string;
    jobId?: string;
}

export type CreationCompileArgument = CreationJobAuthorityArgument;

export interface CreationArtifactAdmissionArgument extends CreationJobAuthorityArgument {
    document: Record<string, unknown>;
    dependencyArtifactIds: string[];
}

export interface CreationAssetAcceptanceResultArgument {
    criterionIndex: number;
    criterionSha256: string;
    status: "failed" | "passed";
    evidenceHashes: string[];
}

export interface CreationAssetProcessArgument extends CreationJobAuthorityArgument {
    licenseArtifactIds: string[];
    recipeId: string;
    processingReceiptId: string;
    qaReportId: string;
    acceptanceResults: CreationAssetAcceptanceResultArgument[];
}

export interface CreationAssetReleaseSealArgument extends CreationJobAuthorityArgument {
    qaReportArtifactIds: string[];
    manifestId: string;
    targetGrantId: string;
    expectedTargetGrantGeneration: number;
}

export interface CreationRuntimeComposeArgument extends CreationJobAuthorityArgument {
    gamepackArtifactId: string;
    assetInventoryArtifactId: string;
    assetpackArtifactId: string;
    targetGrantId: string;
    expectedTargetGrantGeneration: number;
}

export interface CreationRuntimeBundleBuildArgument extends CreationJobAuthorityArgument {
    gamepackArtifactId: string;
    assetInventoryArtifactId: string;
    assetpackArtifactId: string;
    runtimeSnapshotArtifactId: string;
    runtimeAdapterRegistryArtifactId: string;
    runtimeCompositionArtifactId: string;
    runtimeSupportReportArtifactId: string;
    sourceGrantId: string;
    expectedSourceGrantGeneration: number;
    targetGrantId: string;
    expectedTargetGrantGeneration: number;
}

export interface CreationMaterializationBundleBuildArgument extends CreationJobAuthorityArgument {
    runtimeBundleArtifactId: string;
    sourceGrantId: string;
    expectedSourceGrantGeneration: number;
    targetGrantId: string;
    expectedTargetGrantGeneration: number;
}

export interface CreationGameMaterializeArgument extends CreationJobAuthorityArgument {
    materializationBundleArtifactId: string;
    sourceGrantId: string;
    expectedSourceGrantGeneration: number;
    targetGrantId: string;
    expectedTargetGrantGeneration: number;
}

export interface CreationGamePackageArgument extends CreationJobAuthorityArgument {
    standaloneGameArtifactId: string;
    sourceGrantId: string;
    expectedSourceGrantGeneration: number;
    targetGrantId: string;
    expectedTargetGrantGeneration: number;
}

export interface CreationGamePackageExtractArgument extends CreationJobAuthorityArgument {
    gamePackageArtifactId: string;
    sourceGrantId: string;
    expectedSourceGrantGeneration: number;
    targetGrantId: string;
    expectedTargetGrantGeneration: number;
}

export interface CreationAuthorityReviewArgument {
    workspaceId: string;
    qaReportArtifactId: string;
    outputRole: string;
}

export interface CreationAuthorityReleaseArgument {
    workspaceId: string;
    reviewReceiptArtifactIds: string[];
    targetGrantId: string;
}

export interface CreationAuthorityHeadlessArgument {
    workspaceId: string;
    runtimeBundleArtifactId: string;
    sourceGrantId: string;
    headlessScriptArtifactId: string;
    targetGrantId: string;
    platformId: string;
}

export interface CreationAuthorityJobActionArgument {
    workspaceId: string;
    jobId: string;
}

export interface CreationOutputGrantSelectArgument {
    workspaceId: string;
}

export interface CreationOutputGrantGetArgument {
    grantId: string;
}

export interface CreationOutputGrantListArgument extends CreationAuthorityArgument {
    expectedArtifactSnapshotHash: string;
    cursor: string | null;
    limit: number;
}

export interface CreationOutputGrantRevokeArgument extends CreationOutputGrantGetArgument {
    expectedGeneration: number;
}

export type CreationJobState =
    "queued" | "running" | "succeeded" | "failed" | "canceled" | "orphaned";

export interface CreationJobListArgument {
    workspaceId: string;
    state: CreationJobState | null;
    afterSequence: number;
    limit: number;
}

export interface CreationJobMutationArgument {
    jobId: string;
    expectedGeneration: number;
    expectedRecordHash: string;
}

export interface CreationJobRecoveryArgument extends CreationJobMutationArgument {
    mode: "resume" | "rollback" | "cleanup";
}

export interface CreationEventListArgument {
    workspaceId: string;
    afterId: number;
    limit: number;
}

export function validateCreationCompileArgument(
    value: unknown,
): CreationCompileArgument {
    const params = validateClosedParams(value, [
        "workspaceId",
        "expectedRootGeneration",
        "expectedSourceRevision",
        "expectedWorkflowStatusHash",
        "expectedArtifactSnapshotHash",
        "jobId",
    ]);
    return validateCreationJobAuthorityFields(params);
}

export function validateCreationArtifactAdmissionArgument(
    value: unknown,
): CreationArtifactAdmissionArgument {
    const params = validateClosedParams(value, [
        "workspaceId",
        "expectedRootGeneration",
        "expectedSourceRevision",
        "expectedWorkflowStatusHash",
        "expectedArtifactSnapshotHash",
        "jobId",
        "document",
        "dependencyArtifactIds",
    ]);
    const authority = validateCreationJobAuthorityFields(params);
    const document = normalizeJsonObject(
        params.document,
        "creation artifact admission document",
    );
    const documentBytes = Buffer.byteLength(
        JSON.stringify(sortJsonValue(document)),
        "utf8",
    );
    if (documentBytes > MAX_CREATION_ADMISSION_DOCUMENT_BYTES) {
        throw new TypeError(
            "Studio creation artifact admission document is too large",
        );
    }
    if (
        !Array.isArray(params.dependencyArtifactIds) ||
        params.dependencyArtifactIds.length > MAX_CREATION_JOB_DEPENDENCIES
    ) {
        throw new TypeError(
            "Studio creation artifact dependencies are invalid",
        );
    }
    const dependencyArtifactIds = params.dependencyArtifactIds.map((item) =>
        validateEntityId(item, "creation artifact dependency"),
    );
    const canonicalDependencies = [...new Set(dependencyArtifactIds)].sort();
    if (
        dependencyArtifactIds.length !== canonicalDependencies.length ||
        dependencyArtifactIds.some(
            (item, index) => item !== canonicalDependencies[index],
        )
    ) {
        throw new TypeError(
            "Studio creation artifact dependencies must be unique and canonical",
        );
    }
    return { ...authority, document, dependencyArtifactIds };
}

export function validateCreationAssetProcessArgument(
    value: unknown,
): CreationAssetProcessArgument {
    const params = validateClosedParams(value, [
        "workspaceId",
        "expectedRootGeneration",
        "expectedSourceRevision",
        "expectedWorkflowStatusHash",
        "expectedArtifactSnapshotHash",
        "jobId",
        "licenseArtifactIds",
        "recipeId",
        "processingReceiptId",
        "qaReportId",
        "acceptanceResults",
    ]);
    const authority = validateCreationJobAuthorityFields(params);
    if (
        !Array.isArray(params.licenseArtifactIds) ||
        params.licenseArtifactIds.length < 1 ||
        params.licenseArtifactIds.length > MAX_CREATION_ASSET_LICENSES
    ) {
        throw new TypeError("Studio creation asset licenses are invalid");
    }
    const licenseArtifactIds = params.licenseArtifactIds.map((item) =>
        validateEntityId(item, "creation asset license"),
    );
    const canonicalLicenses = [...new Set(licenseArtifactIds)].sort();
    if (
        licenseArtifactIds.length !== canonicalLicenses.length ||
        licenseArtifactIds.some(
            (item, index) => item !== canonicalLicenses[index],
        )
    ) {
        throw new TypeError(
            "Studio creation asset licenses must be unique and canonical",
        );
    }
    const acceptanceResults = validateStudioCreationAssetAcceptanceResults(
        params.acceptanceResults,
    );
    return {
        ...authority,
        licenseArtifactIds,
        recipeId: validateEntityId(params.recipeId, "creation asset recipe"),
        processingReceiptId: validateEntityId(
            params.processingReceiptId,
            "creation asset processing receipt",
        ),
        qaReportId: validateEntityId(
            params.qaReportId,
            "creation asset QA report",
        ),
        acceptanceResults,
    };
}

export function validateCreationAssetReleaseSealArgument(
    value: unknown,
): CreationAssetReleaseSealArgument {
    const params = validateClosedParams(value, [
        "workspaceId",
        "expectedRootGeneration",
        "expectedSourceRevision",
        "expectedWorkflowStatusHash",
        "expectedArtifactSnapshotHash",
        "jobId",
        "qaReportArtifactIds",
        "manifestId",
        "targetGrantId",
        "expectedTargetGrantGeneration",
    ]);
    const authority = validateCreationJobAuthorityFields(params);
    if (
        !Array.isArray(params.qaReportArtifactIds) ||
        params.qaReportArtifactIds.length < 1 ||
        params.qaReportArtifactIds.length > MAX_CREATION_JOB_DEPENDENCIES
    ) {
        throw new TypeError(
            "Studio creation asset release QA reports are invalid",
        );
    }
    const qaReportArtifactIds = params.qaReportArtifactIds.map((item) =>
        validateEntityId(item, "creation asset release QA report"),
    );
    const canonicalQaReports = [...new Set(qaReportArtifactIds)].sort();
    if (
        qaReportArtifactIds.length !== canonicalQaReports.length ||
        qaReportArtifactIds.some(
            (item, index) => item !== canonicalQaReports[index],
        )
    ) {
        throw new TypeError(
            "Studio creation asset release QA reports must be unique and canonical",
        );
    }
    return {
        ...authority,
        qaReportArtifactIds,
        manifestId: validateEntityId(
            params.manifestId,
            "creation asset release manifest",
        ),
        targetGrantId: validateEntityId(
            params.targetGrantId,
            "creation asset release output grant",
        ),
        expectedTargetGrantGeneration: validateGeneration(
            params.expectedTargetGrantGeneration,
            "creation asset release output grant generation",
        ),
    };
}

export function validateCreationRuntimeComposeArgument(
    value: unknown,
): CreationRuntimeComposeArgument {
    const params = validateClosedParams(value, [
        "workspaceId",
        "expectedRootGeneration",
        "expectedSourceRevision",
        "expectedWorkflowStatusHash",
        "expectedArtifactSnapshotHash",
        "jobId",
        "gamepackArtifactId",
        "assetInventoryArtifactId",
        "assetpackArtifactId",
        "targetGrantId",
        "expectedTargetGrantGeneration",
    ]);
    const authority = validateCreationJobAuthorityFields(params);
    const gamepackArtifactId = validateEntityId(
        params.gamepackArtifactId,
        "creation runtime gamepack",
    );
    const assetInventoryArtifactId = validateEntityId(
        params.assetInventoryArtifactId,
        "creation runtime asset inventory",
    );
    const assetpackArtifactId = validateEntityId(
        params.assetpackArtifactId,
        "creation runtime assetpack",
    );
    if (
        new Set([
            gamepackArtifactId,
            assetInventoryArtifactId,
            assetpackArtifactId,
        ]).size !== 3
    ) {
        throw new TypeError(
            "Studio creation runtime artifact IDs must be distinct",
        );
    }
    return {
        ...authority,
        gamepackArtifactId,
        assetInventoryArtifactId,
        assetpackArtifactId,
        targetGrantId: validateEntityId(
            params.targetGrantId,
            "creation runtime assetpack output grant",
        ),
        expectedTargetGrantGeneration: validateGeneration(
            params.expectedTargetGrantGeneration,
            "creation runtime assetpack output grant generation",
        ),
    };
}

export function validateCreationRuntimeBundleBuildArgument(
    value: unknown,
): CreationRuntimeBundleBuildArgument {
    const params = validateClosedParams(value, [
        "workspaceId",
        "expectedRootGeneration",
        "expectedSourceRevision",
        "expectedWorkflowStatusHash",
        "expectedArtifactSnapshotHash",
        "jobId",
        "gamepackArtifactId",
        "assetInventoryArtifactId",
        "assetpackArtifactId",
        "runtimeSnapshotArtifactId",
        "runtimeAdapterRegistryArtifactId",
        "runtimeCompositionArtifactId",
        "runtimeSupportReportArtifactId",
        "sourceGrantId",
        "expectedSourceGrantGeneration",
        "targetGrantId",
        "expectedTargetGrantGeneration",
    ]);
    const authority = validateCreationJobAuthorityFields(params);
    const artifactIds = [
        ["gamepackArtifactId", "creation runtime bundle gamepack"],
        ["assetInventoryArtifactId", "creation runtime bundle asset inventory"],
        ["assetpackArtifactId", "creation runtime bundle assetpack"],
        ["runtimeSnapshotArtifactId", "creation runtime bundle snapshot"],
        [
            "runtimeAdapterRegistryArtifactId",
            "creation runtime bundle adapter registry",
        ],
        ["runtimeCompositionArtifactId", "creation runtime bundle composition"],
        [
            "runtimeSupportReportArtifactId",
            "creation runtime bundle support report",
        ],
    ].map(([field, context]) => validateEntityId(params[field], context));
    if (new Set(artifactIds).size !== artifactIds.length) {
        throw new TypeError(
            "Studio creation runtime bundle artifact IDs must be distinct",
        );
    }
    const sourceGrantId = validateEntityId(
        params.sourceGrantId,
        "creation runtime bundle source grant",
    );
    const targetGrantId = validateEntityId(
        params.targetGrantId,
        "creation runtime bundle output grant",
    );
    if (sourceGrantId === targetGrantId) {
        throw new TypeError(
            "Studio creation runtime bundle source and target grants must be distinct",
        );
    }
    return {
        ...authority,
        gamepackArtifactId: artifactIds[0],
        assetInventoryArtifactId: artifactIds[1],
        assetpackArtifactId: artifactIds[2],
        runtimeSnapshotArtifactId: artifactIds[3],
        runtimeAdapterRegistryArtifactId: artifactIds[4],
        runtimeCompositionArtifactId: artifactIds[5],
        runtimeSupportReportArtifactId: artifactIds[6],
        sourceGrantId,
        expectedSourceGrantGeneration: validateGeneration(
            params.expectedSourceGrantGeneration,
            "creation runtime bundle source grant generation",
        ),
        targetGrantId,
        expectedTargetGrantGeneration: validateGeneration(
            params.expectedTargetGrantGeneration,
            "creation runtime bundle output grant generation",
        ),
    };
}

export function validateCreationMaterializationBundleBuildArgument(
    value: unknown,
): CreationMaterializationBundleBuildArgument {
    const params = validateClosedParams(value, [
        "workspaceId",
        "expectedRootGeneration",
        "expectedSourceRevision",
        "expectedWorkflowStatusHash",
        "expectedArtifactSnapshotHash",
        "jobId",
        "runtimeBundleArtifactId",
        "sourceGrantId",
        "expectedSourceGrantGeneration",
        "targetGrantId",
        "expectedTargetGrantGeneration",
    ]);
    const authority = validateCreationJobAuthorityFields(params);
    const sourceGrantId = validateEntityId(
        params.sourceGrantId,
        "creation materialization bundle source grant",
    );
    const targetGrantId = validateEntityId(
        params.targetGrantId,
        "creation materialization bundle output grant",
    );
    if (sourceGrantId === targetGrantId) {
        throw new TypeError(
            "Studio creation materialization bundle source and target grants must be distinct",
        );
    }
    return {
        ...authority,
        runtimeBundleArtifactId: validateEntityId(
            params.runtimeBundleArtifactId,
            "creation materialization runtime bundle",
        ),
        sourceGrantId,
        expectedSourceGrantGeneration: validateGeneration(
            params.expectedSourceGrantGeneration,
            "creation materialization bundle source grant generation",
        ),
        targetGrantId,
        expectedTargetGrantGeneration: validateGeneration(
            params.expectedTargetGrantGeneration,
            "creation materialization bundle output grant generation",
        ),
    };
}

export function validateCreationGameMaterializeArgument(
    value: unknown,
): CreationGameMaterializeArgument {
    const params = validateClosedParams(value, [
        "workspaceId",
        "expectedRootGeneration",
        "expectedSourceRevision",
        "expectedWorkflowStatusHash",
        "expectedArtifactSnapshotHash",
        "jobId",
        "materializationBundleArtifactId",
        "sourceGrantId",
        "expectedSourceGrantGeneration",
        "targetGrantId",
        "expectedTargetGrantGeneration",
    ]);
    const authority = validateCreationJobAuthorityFields(params);
    const sourceGrantId = validateEntityId(
        params.sourceGrantId,
        "creation standalone source grant",
    );
    const targetGrantId = validateEntityId(
        params.targetGrantId,
        "creation standalone output grant",
    );
    if (sourceGrantId === targetGrantId) {
        throw new TypeError(
            "Studio creation standalone source and target grants must be distinct",
        );
    }
    return {
        ...authority,
        materializationBundleArtifactId: validateEntityId(
            params.materializationBundleArtifactId,
            "creation standalone materialization bundle",
        ),
        sourceGrantId,
        expectedSourceGrantGeneration: validateGeneration(
            params.expectedSourceGrantGeneration,
            "creation standalone source grant generation",
        ),
        targetGrantId,
        expectedTargetGrantGeneration: validateGeneration(
            params.expectedTargetGrantGeneration,
            "creation standalone output grant generation",
        ),
    };
}

export function validateCreationGamePackageArgument(
    value: unknown,
): CreationGamePackageArgument {
    const params = validateClosedParams(value, [
        "workspaceId",
        "expectedRootGeneration",
        "expectedSourceRevision",
        "expectedWorkflowStatusHash",
        "expectedArtifactSnapshotHash",
        "jobId",
        "standaloneGameArtifactId",
        "sourceGrantId",
        "expectedSourceGrantGeneration",
        "targetGrantId",
        "expectedTargetGrantGeneration",
    ]);
    const authority = validateCreationJobAuthorityFields(params);
    const sourceGrantId = validateEntityId(
        params.sourceGrantId,
        "creation game package source grant",
    );
    const targetGrantId = validateEntityId(
        params.targetGrantId,
        "creation game package output grant",
    );
    if (sourceGrantId === targetGrantId) {
        throw new TypeError(
            "Studio creation game package source and target grants must be distinct",
        );
    }
    return {
        ...authority,
        standaloneGameArtifactId: validateEntityId(
            params.standaloneGameArtifactId,
            "creation standalone game",
        ),
        sourceGrantId,
        expectedSourceGrantGeneration: validateGeneration(
            params.expectedSourceGrantGeneration,
            "creation game package source grant generation",
        ),
        targetGrantId,
        expectedTargetGrantGeneration: validateGeneration(
            params.expectedTargetGrantGeneration,
            "creation game package output grant generation",
        ),
    };
}

export function validateCreationGamePackageExtractArgument(
    value: unknown,
): CreationGamePackageExtractArgument {
    const params = validateClosedParams(value, [
        "workspaceId",
        "expectedRootGeneration",
        "expectedSourceRevision",
        "expectedWorkflowStatusHash",
        "expectedArtifactSnapshotHash",
        "jobId",
        "gamePackageArtifactId",
        "sourceGrantId",
        "expectedSourceGrantGeneration",
        "targetGrantId",
        "expectedTargetGrantGeneration",
    ]);
    const authority = validateCreationJobAuthorityFields(params);
    const sourceGrantId = validateEntityId(
        params.sourceGrantId,
        "creation game package extraction source grant",
    );
    const targetGrantId = validateEntityId(
        params.targetGrantId,
        "creation game package extraction output grant",
    );
    if (sourceGrantId === targetGrantId) {
        throw new TypeError(
            "Studio creation game package extraction source and target grants must be distinct",
        );
    }
    return {
        ...authority,
        gamePackageArtifactId: validateEntityId(
            params.gamePackageArtifactId,
            "creation game package",
        ),
        sourceGrantId,
        expectedSourceGrantGeneration: validateGeneration(
            params.expectedSourceGrantGeneration,
            "creation game package extraction source grant generation",
        ),
        targetGrantId,
        expectedTargetGrantGeneration: validateGeneration(
            params.expectedTargetGrantGeneration,
            "creation game package extraction output grant generation",
        ),
    };
}

export function validateCreationAuthorityReviewArgument(
    value: unknown,
): CreationAuthorityReviewArgument {
    const params = validateClosedParams(value, [
        "workspaceId",
        "qaReportArtifactId",
        "outputRole",
    ]);
    if (
        typeof params.outputRole !== "string" ||
        !CREATION_AUTHORITY_OUTPUT_ROLES.has(params.outputRole)
    ) {
        throw new TypeError("Studio creation authority output role is invalid");
    }
    return {
        workspaceId: validateWorkspaceId(params.workspaceId),
        qaReportArtifactId: validateEntityId(
            params.qaReportArtifactId,
            "creation QA report artifact",
        ),
        outputRole: params.outputRole,
    };
}

export function validateCreationAuthorityReleaseArgument(
    value: unknown,
): CreationAuthorityReleaseArgument {
    const params = validateClosedParams(value, [
        "workspaceId",
        "reviewReceiptArtifactIds",
        "targetGrantId",
    ]);
    if (
        !Array.isArray(params.reviewReceiptArtifactIds) ||
        params.reviewReceiptArtifactIds.length < 1 ||
        params.reviewReceiptArtifactIds.length > MAX_CREATION_JOB_DEPENDENCIES
    ) {
        throw new TypeError(
            "Studio creation release authority review receipts are invalid",
        );
    }
    const reviewReceiptArtifactIds = params.reviewReceiptArtifactIds.map(
        (item) => validateEntityId(item, "creation review receipt artifact"),
    );
    const canonicalReviewReceipts = [
        ...new Set(reviewReceiptArtifactIds),
    ].sort();
    if (
        reviewReceiptArtifactIds.length !== canonicalReviewReceipts.length ||
        reviewReceiptArtifactIds.some(
            (item, index) => item !== canonicalReviewReceipts[index],
        )
    ) {
        throw new TypeError(
            "Studio creation release authority review receipts must be unique and canonical",
        );
    }
    return {
        workspaceId: validateWorkspaceId(params.workspaceId),
        reviewReceiptArtifactIds,
        targetGrantId: validateEntityId(
            params.targetGrantId,
            "creation release target grant",
        ),
    };
}

export function validateCreationAuthorityHeadlessArgument(
    value: unknown,
): CreationAuthorityHeadlessArgument {
    const params = validateClosedParams(value, [
        "workspaceId",
        "runtimeBundleArtifactId",
        "sourceGrantId",
        "headlessScriptArtifactId",
        "targetGrantId",
        "platformId",
    ]);
    const sourceGrantId = validateEntityId(
        params.sourceGrantId,
        "creation headless source grant",
    );
    const targetGrantId = validateEntityId(
        params.targetGrantId,
        "creation headless target grant",
    );
    if (sourceGrantId === targetGrantId) {
        throw new TypeError(
            "Studio creation headless source and target grants must be distinct",
        );
    }
    return {
        workspaceId: validateWorkspaceId(params.workspaceId),
        runtimeBundleArtifactId: validateEntityId(
            params.runtimeBundleArtifactId,
            "creation headless runtime bundle",
        ),
        sourceGrantId,
        headlessScriptArtifactId: validateEntityId(
            params.headlessScriptArtifactId,
            "creation headless script artifact",
        ),
        targetGrantId,
        platformId: validatePlatformId(params.platformId),
    };
}

export function validateCreationAuthorityJobActionArgument(
    value: unknown,
): CreationAuthorityJobActionArgument {
    const params = validateClosedParams(value, ["workspaceId", "jobId"]);
    return {
        workspaceId: validateWorkspaceId(params.workspaceId),
        jobId: validateEntityId(params.jobId, "creation authority job"),
    };
}

export function validateCreationOutputGrantSelectArgument(
    value: unknown,
): CreationOutputGrantSelectArgument {
    const params = validateClosedParams(value, ["workspaceId"]);
    return { workspaceId: validateWorkspaceId(params.workspaceId) };
}

export function validateCreationOutputGrantGetArgument(
    value: unknown,
): CreationOutputGrantGetArgument {
    const params = validateClosedParams(value, ["grantId"]);
    return {
        grantId: validateEntityId(params.grantId, "creation output grant"),
    };
}

export function validateCreationOutputGrantListArgument(
    value: unknown,
): CreationOutputGrantListArgument {
    const params = validateClosedParams(value, [
        "workspaceId",
        "expectedRootGeneration",
        "expectedSourceRevision",
        "expectedWorkflowStatusHash",
        "expectedArtifactSnapshotHash",
        "cursor",
        "limit",
    ]);
    const authority = validateCreationEvidenceAuthorityFields(params, true);
    if (
        !Number.isSafeInteger(params.limit) ||
        Number(params.limit) < 1 ||
        Number(params.limit) > MAX_CREATION_OUTPUT_GRANT_PAGE_SIZE
    ) {
        throw new TypeError(
            "Studio creation output grant page limit is invalid",
        );
    }
    return {
        ...authority,
        expectedArtifactSnapshotHash: validateSha256(
            authority.expectedArtifactSnapshotHash,
            "creation artifact snapshot hash",
        ),
        cursor:
            params.cursor === null
                ? null
                : validateEntityId(
                      params.cursor,
                      "creation output grant cursor",
                  ),
        limit: Number(params.limit),
    };
}

export function validateCreationOutputGrantRevokeArgument(
    value: unknown,
): CreationOutputGrantRevokeArgument {
    const params = validateClosedParams(value, [
        "grantId",
        "expectedGeneration",
    ]);
    return {
        grantId: validateEntityId(params.grantId, "creation output grant"),
        expectedGeneration: validateGeneration(
            params.expectedGeneration,
            "creation output grant generation",
        ),
    };
}

export function validateCreationJobIdArgument(value: unknown): {
    jobId: string;
} {
    const params = validateClosedParams(value, ["jobId"]);
    return { jobId: validateEntityId(params.jobId, "creation job") };
}

export function validateCreationJobListArgument(
    value: unknown,
): CreationJobListArgument {
    const params = validateClosedParams(value, [
        "workspaceId",
        "state",
        "afterSequence",
        "limit",
    ]);
    if (
        params.state !== null &&
        (typeof params.state !== "string" ||
            !CREATION_JOB_STATES.has(params.state))
    ) {
        throw new TypeError("Studio creation job state is invalid");
    }
    const afterSequence = validateGeneration(
        params.afterSequence,
        "creation job sequence",
    );
    if (
        !Number.isSafeInteger(params.limit) ||
        Number(params.limit) < 1 ||
        Number(params.limit) > MAX_CREATION_JOB_PAGE_SIZE
    ) {
        throw new TypeError("Studio creation job page limit is invalid");
    }
    return {
        workspaceId: validateWorkspaceId(params.workspaceId),
        state: params.state as CreationJobState | null,
        afterSequence,
        limit: Number(params.limit),
    };
}

export function validateCreationJobMutationArgument(
    value: unknown,
): CreationJobMutationArgument {
    const params = validateClosedParams(value, [
        "jobId",
        "expectedGeneration",
        "expectedRecordHash",
    ]);
    return {
        jobId: validateEntityId(params.jobId, "creation job"),
        expectedGeneration: validateGeneration(
            params.expectedGeneration,
            "creation job generation",
        ),
        expectedRecordHash: validateSha256(
            params.expectedRecordHash,
            "creation job record hash",
        ),
    };
}

export function validateCreationJobRecoveryArgument(
    value: unknown,
): CreationJobRecoveryArgument {
    const params = validateClosedParams(value, [
        "jobId",
        "mode",
        "expectedGeneration",
        "expectedRecordHash",
    ]);
    if (
        typeof params.mode !== "string" ||
        !CREATION_JOB_RECOVERY_MODES.has(params.mode)
    ) {
        throw new TypeError("Studio creation job recovery mode is invalid");
    }
    const mutation = validateCreationJobMutationArgument({
        jobId: params.jobId,
        expectedGeneration: params.expectedGeneration,
        expectedRecordHash: params.expectedRecordHash,
    });
    return {
        ...mutation,
        mode: params.mode as CreationJobRecoveryArgument["mode"],
    };
}

export function validateCreationEventListArgument(
    value: unknown,
): CreationEventListArgument {
    const params = validateClosedParams(value, [
        "workspaceId",
        "afterId",
        "limit",
    ]);
    const afterId = validateGeneration(params.afterId, "creation event cursor");
    if (
        !Number.isSafeInteger(params.limit) ||
        Number(params.limit) < 1 ||
        Number(params.limit) > MAX_CREATION_EVENT_PAGE_SIZE
    ) {
        throw new TypeError("Studio creation event page limit is invalid");
    }
    return {
        workspaceId: validateWorkspaceId(params.workspaceId),
        afterId,
        limit: Number(params.limit),
    };
}

function validateCreationJobAuthorityFields(
    params: Record<string, unknown>,
): CreationJobAuthorityArgument {
    const authority = validateCreationEvidenceAuthorityFields(params, true);
    return {
        ...authority,
        expectedArtifactSnapshotHash: validateSha256(
            authority.expectedArtifactSnapshotHash,
            "creation artifact snapshot hash",
        ),
        ...(params.jobId === undefined
            ? {}
            : { jobId: validateEntityId(params.jobId, "creation job") }),
    };
}

function validateCreationEvidenceAuthorityFields(
    params: Record<string, unknown>,
    requireArtifactSnapshot: boolean,
): CreationEvidenceAuthorityArgument {
    return {
        ...validateCreationAuthorityFields(params, false),
        expectedArtifactSnapshotHash: requireArtifactSnapshot
            ? validateSha256(
                  params.expectedArtifactSnapshotHash,
                  "creation artifact snapshot hash",
              )
            : validateNullableSha256(
                  params.expectedArtifactSnapshotHash,
                  "creation artifact snapshot hash",
              ),
    };
}

export interface CreationModuleStageArgument extends CreationAuthorityArgument {
    operation: "create" | "replace" | "delete";
    path: string;
    format: CreationModuleFormat;
    expectedBaseFileSha256: string | null;
    proposedModule: Record<string, unknown> | null;
}

export function validateCreationModuleStageArgument(
    value: unknown,
): CreationModuleStageArgument {
    const params = validateClosedParams(value, [
        "workspaceId",
        "expectedRootGeneration",
        "expectedSourceRevision",
        "expectedWorkflowStatusHash",
        "operation",
        "path",
        "format",
        "expectedBaseFileSha256",
        "proposedModule",
    ]);
    const authority = validateCreationAuthorityFields(params, false);
    if (
        params.operation !== "create" &&
        params.operation !== "replace" &&
        params.operation !== "delete"
    ) {
        throw new TypeError("Studio creation module operation is invalid");
    }
    if (
        typeof params.path !== "string" ||
        !isPortableRelativePath(params.path)
    ) {
        throw new TypeError("Studio creation module path is invalid");
    }
    if (
        typeof params.format !== "string" ||
        !Object.hasOwn(CREATION_MODULE_COLLECTIONS, params.format)
    ) {
        throw new TypeError("Studio creation module format is unsupported");
    }
    const hasProposed = Object.hasOwn(params, "proposedModule");
    if (params.operation === "create") {
        if (
            params.expectedBaseFileSha256 !== null ||
            !hasProposed ||
            !isRecord(params.proposedModule)
        ) {
            throw new TypeError(
                "Studio module create requires a new document and null base identity",
            );
        }
    } else if (params.operation === "replace") {
        validateSha256(
            params.expectedBaseFileSha256,
            "creation module base file SHA-256",
        );
        if (!hasProposed || !isRecord(params.proposedModule)) {
            throw new TypeError(
                "Studio module replace requires a proposed document",
            );
        }
    } else {
        validateSha256(
            params.expectedBaseFileSha256,
            "creation module base file SHA-256",
        );
        if (hasProposed) {
            throw new TypeError(
                "Studio module delete cannot include a proposed document",
            );
        }
    }
    return {
        ...authority,
        operation: params.operation,
        path: params.path,
        format: params.format as CreationModuleFormat,
        expectedBaseFileSha256:
            params.operation === "create"
                ? null
                : validateSha256(
                      params.expectedBaseFileSha256,
                      "creation module base file SHA-256",
                  ),
        proposedModule:
            params.operation === "delete"
                ? null
                : normalizeJsonObject(
                      params.proposedModule,
                      "proposed creation module",
                  ),
    };
}

export interface CreationWorkflowReconcileArgument extends CreationAuthorityArgument {
    artifactRegistry: Record<string, unknown>[];
}

export function validateCreationWorkflowReconcileArgument(
    value: unknown,
): CreationWorkflowReconcileArgument {
    const params = validateClosedParams(value, [
        "workspaceId",
        "expectedRootGeneration",
        "expectedSourceRevision",
        "expectedWorkflowStatusHash",
        "artifactRegistry",
    ]);
    return {
        ...validateCreationAuthorityFields(params, false),
        artifactRegistry: validateCreationArtifactRegistry(
            params.artifactRegistry,
        ),
    };
}

export interface CreationPhaseReadArgument extends CreationAuthorityArgument {
    expectedWorkflowStatusHash: string;
    phaseId: string;
}

export function validateCreationPhaseReadArgument(
    value: unknown,
): CreationPhaseReadArgument {
    const params = validateClosedParams(value, [
        "workspaceId",
        "expectedRootGeneration",
        "expectedSourceRevision",
        "expectedWorkflowStatusHash",
        "phaseId",
    ]);
    return {
        ...validateCreationAuthorityFields(params, true),
        expectedWorkflowStatusHash: validateSha256(
            params.expectedWorkflowStatusHash,
            "creation workflow status hash",
        ),
        phaseId: validateCreationPhaseId(params.phaseId),
    };
}

export interface CreationPhaseReportArgument extends CreationAuthorityArgument {
    report: Record<string, unknown>;
    artifactRegistry: Record<string, unknown>[];
}

export function validateCreationPhaseReportArgument(
    value: unknown,
): CreationPhaseReportArgument {
    return validateCreationPhaseReportFields(value, false);
}

export function validateCreationPhaseCompleteArgument(
    value: unknown,
): CreationPhaseReportArgument & { expectedWorkflowStatusHash: string } {
    const result = validateCreationPhaseReportFields(value, true);
    return {
        ...result,
        expectedWorkflowStatusHash: validateSha256(
            result.expectedWorkflowStatusHash,
            "creation workflow status hash",
        ),
    };
}

export interface CreationPhaseReopenArgument extends CreationAuthorityArgument {
    expectedWorkflowStatusHash: string;
    phaseId: string;
    reason: string;
    approvedBy: string;
}

export function validateCreationPhaseReopenArgument(
    value: unknown,
): CreationPhaseReopenArgument {
    const params = validateClosedParams(value, [
        "workspaceId",
        "expectedRootGeneration",
        "expectedSourceRevision",
        "expectedWorkflowStatusHash",
        "phaseId",
        "reason",
        "approvedBy",
    ]);
    return {
        ...validateCreationAuthorityFields(params, true),
        expectedWorkflowStatusHash: validateSha256(
            params.expectedWorkflowStatusHash,
            "creation workflow status hash",
        ),
        phaseId: validateCreationPhaseId(params.phaseId),
        reason: validateBoundedText(
            params.reason,
            "creation phase reopen reason",
            512,
        ),
        approvedBy: validateEntityId(
            params.approvedBy,
            "creation phase reviewer",
        ),
    };
}

function validateCreationPhaseReportFields(
    value: unknown,
    requireWorkflowHash: boolean,
): CreationPhaseReportArgument {
    const params = validateClosedParams(value, [
        "workspaceId",
        "expectedRootGeneration",
        "expectedSourceRevision",
        "expectedWorkflowStatusHash",
        "report",
        "artifactRegistry",
    ]);
    if (!isRecord(params.report)) {
        throw new TypeError("Studio creation phase report must be an object");
    }
    return {
        ...validateCreationAuthorityFields(params, requireWorkflowHash),
        report: normalizeJsonObject(params.report, "creation phase report"),
        artifactRegistry: validateCreationArtifactRegistry(
            params.artifactRegistry,
        ),
    };
}

function validateCreationAuthorityFields(
    params: Record<string, unknown>,
    requireWorkflowHash: boolean,
): CreationAuthorityArgument {
    return {
        workspaceId: validateWorkspaceId(params.workspaceId),
        expectedRootGeneration: validateGeneration(
            params.expectedRootGeneration,
            "creation root generation",
        ),
        expectedSourceRevision: validateSha256(
            params.expectedSourceRevision,
            "creation source revision",
        ),
        expectedWorkflowStatusHash: requireWorkflowHash
            ? validateSha256(
                  params.expectedWorkflowStatusHash,
                  "creation workflow status hash",
              )
            : validateNullableSha256(
                  params.expectedWorkflowStatusHash,
                  "creation workflow status hash",
              ),
    };
}

function validateCreationArtifactRegistry(
    value: unknown,
): Record<string, unknown>[] {
    if (!Array.isArray(value) || value.length > 1024) {
        throw new TypeError(
            "Studio creation artifact registry must contain at most 1024 entries",
        );
    }
    return value.map((entry) =>
        normalizeJsonObject(entry, "creation artifact registry entry"),
    );
}

function validateCreationPhaseId(value: unknown): string {
    if (typeof value !== "string" || !CREATION_PHASE_IDS.has(value)) {
        throw new TypeError("Studio creation phase ID is invalid");
    }
    return value;
}

export interface CreationChangesetActionArgument {
    changesetId: string;
    expectedRecordHash: string;
    expectedReviewSha256: string;
}

export function validateCreationChangesetActionArgument(
    value: unknown,
): CreationChangesetActionArgument {
    const params = validateClosedParams(value, [
        "changesetId",
        "expectedRecordHash",
        "expectedReviewSha256",
    ]);
    return {
        changesetId: validateChangesetId(params.changesetId),
        expectedRecordHash: validateSha256(
            params.expectedRecordHash,
            "creation changeset record hash",
        ),
        expectedReviewSha256: validateSha256(
            params.expectedReviewSha256,
            "creation changeset review hash",
        ),
    };
}

export function validateCreationChangesetApplyArgument(
    value: unknown,
): CreationChangesetActionArgument & { expectedRootGeneration: number } {
    const params = validateClosedParams(value, [
        "changesetId",
        "expectedRecordHash",
        "expectedReviewSha256",
        "expectedRootGeneration",
    ]);
    const action = validateCreationChangesetActionArgument({
        changesetId: params.changesetId,
        expectedRecordHash: params.expectedRecordHash,
        expectedReviewSha256: params.expectedReviewSha256,
    });
    return {
        ...action,
        expectedRootGeneration: validateGeneration(
            params.expectedRootGeneration,
            "creation root generation",
        ),
    };
}

export function validateCreationChangesetRecoveryArgument(
    value: unknown,
): CreationChangesetActionArgument & {
    mode: "resume" | "rollback";
    expectedRootGeneration: number;
} {
    const params = validateClosedParams(value, [
        "changesetId",
        "mode",
        "expectedRecordHash",
        "expectedReviewSha256",
        "expectedRootGeneration",
    ]);
    if (params.mode !== "resume" && params.mode !== "rollback") {
        throw new TypeError("Studio creation recovery mode is invalid");
    }
    const action = validateCreationChangesetActionArgument({
        changesetId: params.changesetId,
        expectedRecordHash: params.expectedRecordHash,
        expectedReviewSha256: params.expectedReviewSha256,
    });
    return {
        ...action,
        mode: params.mode,
        expectedRootGeneration: validateGeneration(
            params.expectedRootGeneration,
            "creation root generation",
        ),
    };
}

export function validateSourceReadArgument(value: unknown): {
    workspaceId: string;
    path: string;
} {
    const params = validateClosedParams(value, ["workspaceId", "path"]);
    if (typeof params.path !== "string" || !isPortableSourcePath(params.path)) {
        throw new TypeError("Studio source path is invalid");
    }
    return {
        workspaceId: validateWorkspaceId(params.workspaceId),
        path: params.path,
    };
}

export type AssetCatalogListArgument =
    | { workspaceId: string }
    | {
          workspaceId: string;
          offset: number;
          expectedManifestRevision: string;
      };

export function validateAssetCatalogListArgument(
    value: unknown,
): AssetCatalogListArgument {
    const params = validateClosedParams(value, [
        "workspaceId",
        "offset",
        "expectedManifestRevision",
    ]);
    const workspaceId = validateWorkspaceId(params.workspaceId);
    const hasOffset = Object.hasOwn(params, "offset");
    const hasExpectedRevision = Object.hasOwn(
        params,
        "expectedManifestRevision",
    );
    if (hasOffset !== hasExpectedRevision) {
        throw new TypeError(
            "Studio asset catalog pages require both offset and expected manifest revision",
        );
    }
    if (!hasOffset) {
        return { workspaceId };
    }
    if (!Number.isSafeInteger(params.offset) || (params.offset as number) < 0) {
        throw new TypeError(
            "Studio asset catalog offset must be a non-negative safe integer",
        );
    }
    return {
        workspaceId,
        offset: params.offset as number,
        expectedManifestRevision: validateSha256(
            params.expectedManifestRevision,
            "asset catalog manifest revision",
        ),
    };
}

export function validateAssetCatalogInspectArgument(value: unknown): {
    workspaceId: string;
    manifestRevision: string;
    entryId: string;
} {
    const params = validateClosedParams(value, [
        "workspaceId",
        "manifestRevision",
        "entryId",
    ]);
    if (
        typeof params.entryId !== "string" ||
        !ASSET_ENTRY_ID_PATTERN.test(params.entryId)
    ) {
        throw new TypeError("Studio asset catalog entry ID is invalid");
    }
    return {
        workspaceId: validateWorkspaceId(params.workspaceId),
        manifestRevision: validateSha256(
            params.manifestRevision,
            "asset catalog manifest revision",
        ),
        entryId: params.entryId,
    };
}

export function validateAssetPreviewOpenArgument(value: unknown): {
    workspaceId: string;
    manifestRevision: string;
    entryId: string;
} {
    const params = validateClosedParams(value, [
        "workspaceId",
        "manifestRevision",
        "entryId",
    ]);
    if (
        typeof params.entryId !== "string" ||
        !ASSET_ENTRY_ID_PATTERN.test(params.entryId)
    ) {
        throw new TypeError("Studio asset preview entry ID is invalid");
    }
    return {
        workspaceId: validateWorkspaceId(params.workspaceId),
        manifestRevision: validateSha256(
            params.manifestRevision,
            "asset preview manifest revision",
        ),
        entryId: params.entryId,
    };
}

export function validateAssetPreviewReadArgument(value: unknown): {
    handle: string;
    sequence: number;
} {
    const params = validateClosedParams(value, ["handle", "sequence"]);
    if (
        !Number.isSafeInteger(params.sequence) ||
        (params.sequence as number) < 0 ||
        (params.sequence as number) > MAX_ASSET_PREVIEW_SEQUENCE
    ) {
        throw new TypeError(
            `Studio asset preview sequence must be an integer from 0 to ${MAX_ASSET_PREVIEW_SEQUENCE}`,
        );
    }
    return {
        handle: validateAssetPreviewHandle(params.handle),
        sequence: params.sequence as number,
    };
}

export function validateAssetPreviewCloseArgument(value: unknown): {
    handle: string;
} {
    const params = validateClosedParams(value, ["handle"]);
    return { handle: validateAssetPreviewHandle(params.handle) };
}

export function validateStageSourceDocumentArgument(value: unknown): {
    workspaceId: string;
    path: string;
    baseSha256: string;
    content: string;
} {
    const params = validateClosedParams(value, [
        "workspaceId",
        "path",
        "baseSha256",
        "content",
    ]);
    if (typeof params.path !== "string" || !isPortableSourcePath(params.path)) {
        throw new TypeError("Studio source path is invalid");
    }
    if (
        typeof params.baseSha256 !== "string" ||
        !SHA256_PATTERN.test(params.baseSha256)
    ) {
        throw new TypeError("Studio base SHA-256 is invalid");
    }
    if (
        typeof params.content !== "string" ||
        containsInvalidUnicode(params.content) ||
        Buffer.byteLength(params.content, "utf8") > MAX_SOURCE_DOCUMENT_BYTES
    ) {
        throw new TypeError(
            `Studio source content must be valid UTF-8 of at most ${MAX_SOURCE_DOCUMENT_BYTES} bytes`,
        );
    }
    return {
        workspaceId: validateWorkspaceId(params.workspaceId),
        path: params.path,
        baseSha256: params.baseSha256,
        content: params.content,
    };
}

export function validateChangesetIdArgument(value: unknown): {
    changesetId: string;
} {
    const params = validateClosedParams(value, ["changesetId"]);
    return { changesetId: validateChangesetId(params.changesetId) };
}

export function validateChangesetActionArgument(value: unknown): {
    changesetId: string;
    expectedReviewSha256?: string;
} {
    const params = validateClosedParams(value, [
        "changesetId",
        "expectedReviewSha256",
    ]);
    const changesetId = validateChangesetId(params.changesetId);
    if (!("expectedReviewSha256" in params)) {
        return { changesetId };
    }
    if (
        typeof params.expectedReviewSha256 !== "string" ||
        !SHA256_PATTERN.test(params.expectedReviewSha256)
    ) {
        throw new TypeError("Studio expected review SHA-256 is invalid");
    }
    return { changesetId, expectedReviewSha256: params.expectedReviewSha256 };
}

export function validateAssetReceiptArgument(value: unknown): {
    workspaceId: string;
    input: { receipt: string };
} {
    const { workspaceId, input } = validateWorkspaceJobArgument(value, [
        "receipt",
    ]);
    return {
        workspaceId,
        input: { receipt: validateJobPath(input.receipt, "receipt") },
    };
}

export function validateAssetpackArgument(value: unknown): {
    workspaceId: string;
    input: { assetpack: string; worldpack: string };
} {
    const { workspaceId, input } = validateWorkspaceJobArgument(value, [
        "assetpack",
        "worldpack",
    ]);
    return {
        workspaceId,
        input: {
            assetpack: validateJobPath(input.assetpack, "assetpack"),
            worldpack: validateJobPath(input.worldpack, "worldpack"),
        },
    };
}

export function validateHeadlessArgument(value: unknown): {
    workspaceId: string;
    input: { worldpack: string; ticks: number };
} {
    const { workspaceId, input } = validateWorkspaceJobArgument(value, [
        "worldpack",
        "ticks",
    ]);
    if (
        !Number.isSafeInteger(input.ticks) ||
        (input.ticks as number) < 0 ||
        (input.ticks as number) > MAX_RUNTIME_TICKS
    ) {
        throw new TypeError(
            `Studio headless ticks must be an integer from 0 to ${MAX_RUNTIME_TICKS}`,
        );
    }
    return {
        workspaceId,
        input: {
            worldpack: validateJobPath(input.worldpack, "worldpack"),
            ticks: input.ticks as number,
        },
    };
}

export function validateReplayArgument(value: unknown): {
    workspaceId: string;
    input: { worldpack: string; replay: string };
} {
    const { workspaceId, input } = validateWorkspaceJobArgument(value, [
        "worldpack",
        "replay",
    ]);
    return {
        workspaceId,
        input: {
            worldpack: validateJobPath(input.worldpack, "worldpack"),
            replay: validateJobPath(input.replay, "replay"),
        },
    };
}

export function validateCancelJobArgument(value: unknown): { jobId: string } {
    const params = validateClosedParams(value, ["jobId"]);
    if (
        typeof params.jobId !== "string" ||
        !JOB_ID_PATTERN.test(params.jobId)
    ) {
        throw new TypeError("Studio job ID is invalid");
    }
    return { jobId: params.jobId };
}

export function validateExternalGrantCreateArgument(
    value: unknown,
): CreateExternalGrantParams {
    const params = validateClosedParams(value, [
        "workspaceId",
        "operation",
        "role",
        "artifactKind",
        "expectedContentHash",
    ]);
    const workspaceId = validateWorkspaceId(params.workspaceId);
    if (
        typeof params.operation !== "string" ||
        !Object.hasOwn(EXTERNAL_ARTIFACT_KINDS, params.operation)
    ) {
        throw new TypeError("Studio external operation is invalid");
    }
    const operation = params.operation as StudioExternalOperation;
    if (params.role !== "source" && params.role !== "target") {
        throw new TypeError("Studio external grant role is invalid");
    }
    const role = params.role;
    if (params.artifactKind !== EXTERNAL_ARTIFACT_KINDS[operation][role]) {
        throw new TypeError("Studio external artifact kind is invalid");
    }
    const expectedContentHash =
        params.expectedContentHash === null
            ? null
            : validateSha256(
                  params.expectedContentHash,
                  "external expected content hash",
              );
    if (
        (role === "source" && expectedContentHash === null) ||
        (role === "target" && expectedContentHash !== null)
    ) {
        throw new TypeError(
            "Studio external grant hash does not match its authority role",
        );
    }
    return {
        workspaceId,
        operation,
        role,
        artifactKind: params.artifactKind as StudioExternalArtifactKind,
        expectedContentHash,
    };
}

export function validateExternalGrantIdArgument(value: unknown): {
    grantId: string;
} {
    const params = validateClosedParams(value, ["grantId"]);
    return {
        grantId: validateEntityId(params.grantId, "external grant"),
    };
}

function validateExternalJobArgument(
    value: unknown,
    expectedHashField:
        | "expectedMaterializationHash"
        | "expectedGameHash"
        | "expectedPackageHash",
): {
    workspaceId: string;
    sourceGrantId: string;
    targetGrantId: string;
    expectedHash: string;
} {
    const params = validateClosedParams(value, [
        "workspaceId",
        "sourceGrantId",
        "targetGrantId",
        expectedHashField,
    ]);
    const sourceGrantId = validateEntityId(
        params.sourceGrantId,
        "source external grant",
    );
    const targetGrantId = validateEntityId(
        params.targetGrantId,
        "target external grant",
    );
    if (sourceGrantId === targetGrantId) {
        throw new TypeError(
            "Studio external source and target grants must differ",
        );
    }
    return {
        workspaceId: validateWorkspaceId(params.workspaceId),
        sourceGrantId,
        targetGrantId,
        expectedHash: validateSha256(
            params[expectedHashField],
            "external job expected hash",
        ),
    };
}

export function validateMaterializeGameArgument(
    value: unknown,
): StudioMaterializeGameParams {
    const validated = validateExternalJobArgument(
        value,
        "expectedMaterializationHash",
    );
    return {
        workspaceId: validated.workspaceId,
        sourceGrantId: validated.sourceGrantId,
        targetGrantId: validated.targetGrantId,
        expectedMaterializationHash: validated.expectedHash,
    };
}

export function validatePackageGameArgument(
    value: unknown,
): StudioPackageGameParams {
    const validated = validateExternalJobArgument(value, "expectedGameHash");
    return {
        workspaceId: validated.workspaceId,
        sourceGrantId: validated.sourceGrantId,
        targetGrantId: validated.targetGrantId,
        expectedGameHash: validated.expectedHash,
    };
}

export function validateExtractGamePackageArgument(
    value: unknown,
): StudioExtractGamePackageParams {
    const validated = validateExternalJobArgument(value, "expectedPackageHash");
    return {
        workspaceId: validated.workspaceId,
        sourceGrantId: validated.sourceGrantId,
        targetGrantId: validated.targetGrantId,
        expectedPackageHash: validated.expectedHash,
    };
}

export function validateExternalJobIdArgument(value: unknown): {
    jobId: string;
} {
    const params = validateClosedParams(value, ["jobId"]);
    return { jobId: validateEntityId(params.jobId, "external job") };
}

export function validateExternalJobsListParams(value: unknown): {
    workspace_id?: string;
    state?: StudioExternalJobsListParams["state"];
    limit?: number;
} {
    const params = validateClosedParams(value, [
        "workspaceId",
        "state",
        "limit",
    ]);
    const result: {
        workspace_id?: string;
        state?: StudioExternalJobsListParams["state"];
        limit?: number;
    } = {};
    if (params.workspaceId !== undefined) {
        result.workspace_id = validateWorkspaceId(params.workspaceId);
    }
    if (params.state !== undefined) {
        if (
            typeof params.state !== "string" ||
            !EXTERNAL_JOB_STATES.has(params.state)
        ) {
            throw new TypeError("Studio external job state filter is unknown");
        }
        result.state = params.state as StudioExternalJobsListParams["state"];
    }
    if (params.limit !== undefined) {
        result.limit = validateLimit(params.limit);
    }
    return result;
}

export function validateExternalJobRecoveryArgument(value: unknown): {
    jobId: string;
    action: "resume" | "rollback";
} {
    const params = validateClosedParams(value, ["jobId", "action"]);
    if (params.action !== "resume" && params.action !== "rollback") {
        throw new TypeError("Studio external job recovery action is invalid");
    }
    return {
        jobId: validateEntityId(params.jobId, "external job"),
        action: params.action,
    };
}

export function validateLoginArgument(value: unknown): {
    mode: "browser" | "device-code";
} {
    const params = validateClosedParams(value, ["mode"]);
    if (params.mode !== "browser" && params.mode !== "device-code") {
        throw new TypeError("Codex login mode is invalid");
    }
    return { mode: params.mode };
}

export function validateThreadArgument(value: unknown): { threadId: string } {
    const params = validateClosedParams(value, ["threadId"]);
    return { threadId: validateCodexId(params.threadId, "thread") };
}

export function validateStartTurnArgument(value: unknown): {
    threadId: string;
    text: string;
} {
    const params = validateClosedParams(value, ["threadId", "text"]);
    return {
        threadId: validateCodexId(params.threadId, "thread"),
        text: validateTurnText(params.text),
    };
}

export function validateSteerTurnArgument(value: unknown): {
    threadId: string;
    turnId: string;
    text: string;
} {
    const params = validateClosedParams(value, ["threadId", "turnId", "text"]);
    return {
        threadId: validateCodexId(params.threadId, "thread"),
        turnId: validateCodexId(params.turnId, "turn"),
        text: validateTurnText(params.text),
    };
}

export function validateInterruptTurnArgument(value: unknown): {
    threadId: string;
    turnId: string;
} {
    const params = validateClosedParams(value, ["threadId", "turnId"]);
    return {
        threadId: validateCodexId(params.threadId, "thread"),
        turnId: validateCodexId(params.turnId, "turn"),
    };
}

export function validateUserInputArgument(value: unknown): {
    token: string;
    answers: Record<string, readonly string[]>;
} {
    const params = validateClosedParams(value, ["token", "answers"]);
    if (
        typeof params.token !== "string" ||
        !/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/u.test(
            params.token,
        ) ||
        !isRecord(params.answers)
    ) {
        throw new TypeError("Codex user-input response is invalid");
    }
    const answers: Record<string, readonly string[]> = {};
    const entries = Object.entries(params.answers);
    if (entries.length < 1 || entries.length > 3) {
        throw new TypeError(
            "Codex user-input response has an invalid question count",
        );
    }
    for (const [questionId, raw] of entries) {
        validateCodexId(questionId, "question");
        if (
            !Array.isArray(raw) ||
            raw.length < 1 ||
            raw.length > 8 ||
            !raw.every(
                (item: unknown) =>
                    typeof item === "string" &&
                    Buffer.byteLength(item, "utf8") <= 8_192,
            )
        ) {
            throw new TypeError("Codex user-input answer is invalid");
        }
        answers[questionId] = raw.map((item: unknown) => String(item));
    }
    return { token: params.token, answers };
}

function validateCodexId(value: unknown, context: string): string {
    if (
        typeof value !== "string" ||
        !/^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$/u.test(value)
    ) {
        throw new TypeError(`Codex ${context} ID is invalid`);
    }
    return value;
}

function validateTurnText(value: unknown): string {
    if (
        typeof value !== "string" ||
        value.length < 1 ||
        Buffer.byteLength(value, "utf8") > 128 * 1024
    ) {
        throw new TypeError("Codex turn text exceeds the supported contract");
    }
    return value;
}

export function validateEventsListParams(value: unknown): EventsListParams {
    const params = validateClosedParams(value, [
        "workspace_id",
        "after_id",
        "limit",
    ]);
    const result: EventsListParams = {};
    if (params.workspace_id !== undefined) {
        result.workspace_id = validateWorkspaceId(params.workspace_id);
    }
    if (params.after_id !== undefined) {
        if (
            !Number.isSafeInteger(params.after_id) ||
            (params.after_id as number) < 0
        ) {
            throw new TypeError(
                "Studio event cursor must be a non-negative safe integer",
            );
        }
        result.after_id = params.after_id as number;
    }
    if (params.limit !== undefined) {
        result.limit = validateLimit(params.limit);
    }
    return result;
}

export function validateChangesetsListParams(
    value: unknown,
): ChangesetsListParams {
    const params = validateClosedParams(value, [
        "workspace_id",
        "status",
        "limit",
    ]);
    const result: ChangesetsListParams = {};
    if (params.workspace_id !== undefined) {
        result.workspace_id = validateWorkspaceId(params.workspace_id);
    }
    if (params.status !== undefined) {
        if (
            typeof params.status !== "string" ||
            !CHANGESET_STATUSES.has(params.status)
        ) {
            throw new TypeError("Studio changeset status filter is unknown");
        }
        result.status = params.status as ChangesetsListParams["status"];
    }
    if (params.limit !== undefined) {
        result.limit = validateLimit(params.limit);
    }
    return result;
}

export function validateJobsListParams(value: unknown): JobsListParams {
    const params = validateClosedParams(value, [
        "workspace_id",
        "state",
        "limit",
    ]);
    const result: JobsListParams = {};
    if (params.workspace_id !== undefined) {
        result.workspace_id = validateWorkspaceId(params.workspace_id);
    }
    if (params.state !== undefined) {
        if (typeof params.state !== "string" || !JOB_STATES.has(params.state)) {
            throw new TypeError("Studio job state filter is unknown");
        }
        result.state = params.state as JobsListParams["state"];
    }
    if (params.limit !== undefined) {
        result.limit = validateLimit(params.limit);
    }
    return result;
}

async function registerSelectedCreationProject(
    service: ForgeServiceClient,
    dialogs: StudioDialogClient,
    window: BrowserWindow,
    selectionClient: StudioCreationProjectSelectionClient,
): Promise<StudioClientResult<StudioV3ReplyEnvelope>> {
    let selected: OpenDialogReturnValue;
    try {
        selected = await dialogs.showOpenDialog(window, {
            title: "Register existing creation project",
            buttonLabel: "Register project",
            properties: ["openDirectory"],
        });
    } catch {
        return failure("internal_error", "Creation project selection failed");
    }
    if (selected.canceled || selected.filePaths.length === 0) {
        return failure("cancelled", "Creation project selection was cancelled");
    }
    if (selected.filePaths.length !== 1) {
        return failure(
            "invalid_request",
            "Creation project selection is invalid",
        );
    }
    let identity: { contentHash: string; displayName: string };
    try {
        identity = await selectionClient.readProjectIdentity(
            selected.filePaths[0],
        );
        validateSha256(identity.contentHash, "selected creation project hash");
        validateBoundedText(
            identity.displayName,
            "selected creation project name",
            256,
        );
    } catch {
        return failure(
            "invalid_request",
            "Selected creation project could not be inspected",
        );
    }
    const grantReply = await requestCreationNamed(
        service,
        "creation_root_grant.create",
        {
            role: "existing_root",
            display_name: identity.displayName,
            path: selected.filePaths[0],
            expected_project_hash: identity.contentHash,
        },
    );
    const grant = creationGrantFromReply(
        grantReply,
        "creation_root_grant.create",
    );
    if (grant === null) return grantReply;
    const workspaceReply = await requestCreationNamed(
        service,
        "creation_workspace.register",
        {
            workspace_id: newCreationWorkspaceId(),
            grant_id: grant.grant_id,
            expected_grant_generation: grant.generation,
            expected_project_hash: identity.contentHash,
        },
    );
    if (!workspaceReply.ok || workspaceReply.value.kind === "error") {
        await revokePrivateCreationGrant(service, grant);
    }
    return workspaceReply;
}

async function selectCreationAssetpackOutput(
    service: ForgeServiceClient,
    dialogs: StudioDialogClient,
    window: BrowserWindow,
    argument: CreationOutputGrantSelectArgument,
): Promise<StudioClientResult<StudioV4ReplyEnvelope>> {
    let selected: SaveDialogReturnValue;
    try {
        selected = await dialogs.showSaveDialog(window, {
            title: "Select asset pack output",
            buttonLabel: "Select output",
            nameFieldLabel: "Asset pack folder",
            showsTagField: false,
        });
    } catch {
        return failure("internal_error", "Asset pack output selection failed");
    }
    if (selected.canceled || !selected.filePath) {
        return failure(
            "cancelled",
            "Asset pack output selection was cancelled",
        );
    }
    let selectedPath: string;
    try {
        selectedPath = validateSelectedExternalPath(selected.filePath);
    } catch {
        return failure(
            "invalid_request",
            "Asset pack output selection is invalid",
        );
    }
    return await requestCreationEvidenceNamed(
        service,
        "creation_output_grant.create",
        {
            workspace_id: argument.workspaceId,
            kind: "generic_assetpack_directory",
            display_name: path.basename(selectedPath),
            path: selectedPath,
        },
    );
}

async function selectCreationRuntimeBundleOutput(
    service: ForgeServiceClient,
    dialogs: StudioDialogClient,
    window: BrowserWindow,
    argument: CreationOutputGrantSelectArgument,
): Promise<StudioClientResult<StudioV4ReplyEnvelope>> {
    let selected: SaveDialogReturnValue;
    try {
        selected = await dialogs.showSaveDialog(window, {
            title: "Select runtime bundle output",
            buttonLabel: "Select output",
            nameFieldLabel: "Runtime bundle folder",
            showsTagField: false,
        });
    } catch {
        return failure(
            "internal_error",
            "Runtime bundle output selection failed",
        );
    }
    if (selected.canceled || !selected.filePath) {
        return failure(
            "cancelled",
            "Runtime bundle output selection was cancelled",
        );
    }
    let selectedPath: string;
    try {
        selectedPath = validateSelectedExternalPath(selected.filePath);
    } catch {
        return failure(
            "invalid_request",
            "Runtime bundle output selection is invalid",
        );
    }
    return await requestCreationEvidenceNamed(
        service,
        "creation_output_grant.create",
        {
            workspace_id: argument.workspaceId,
            kind: "game_runtime_bundle_directory",
            display_name: path.basename(selectedPath),
            path: selectedPath,
        },
    );
}

async function selectCreationMaterializationBundleOutput(
    service: ForgeServiceClient,
    dialogs: StudioDialogClient,
    window: BrowserWindow,
    argument: CreationOutputGrantSelectArgument,
): Promise<StudioClientResult<StudioV4ReplyEnvelope>> {
    let selected: SaveDialogReturnValue;
    try {
        selected = await dialogs.showSaveDialog(window, {
            title: "Select game materialization bundle output",
            buttonLabel: "Select output",
            nameFieldLabel: "Materialization bundle folder",
            showsTagField: false,
        });
    } catch {
        return failure(
            "internal_error",
            "Game materialization bundle output selection failed",
        );
    }
    if (selected.canceled || !selected.filePath) {
        return failure(
            "cancelled",
            "Game materialization bundle output selection was cancelled",
        );
    }
    let selectedPath: string;
    try {
        selectedPath = validateSelectedExternalPath(selected.filePath);
    } catch {
        return failure(
            "invalid_request",
            "Game materialization bundle output selection is invalid",
        );
    }
    return await requestCreationEvidenceNamed(
        service,
        "creation_output_grant.create",
        {
            workspace_id: argument.workspaceId,
            kind: "game_materialization_bundle_directory",
            display_name: path.basename(selectedPath),
            path: selectedPath,
        },
    );
}

async function selectCreationStandaloneGameOutput(
    service: ForgeServiceClient,
    dialogs: StudioDialogClient,
    window: BrowserWindow,
    argument: CreationOutputGrantSelectArgument,
): Promise<StudioClientResult<StudioV4ReplyEnvelope>> {
    let selected: SaveDialogReturnValue;
    try {
        selected = await dialogs.showSaveDialog(window, {
            title: "Select standalone game output",
            buttonLabel: "Select output",
            nameFieldLabel: "Standalone game folder",
            showsTagField: false,
        });
    } catch {
        return failure(
            "internal_error",
            "Standalone game output selection failed",
        );
    }
    if (selected.canceled || !selected.filePath) {
        return failure(
            "cancelled",
            "Standalone game output selection was cancelled",
        );
    }
    let selectedPath: string;
    try {
        selectedPath = validateSelectedExternalPath(selected.filePath);
    } catch {
        return failure(
            "invalid_request",
            "Standalone game output selection is invalid",
        );
    }
    return await requestCreationEvidenceNamed(
        service,
        "creation_output_grant.create",
        {
            workspace_id: argument.workspaceId,
            kind: "standalone_game_directory",
            display_name: path.basename(selectedPath),
            path: selectedPath,
        },
    );
}

async function selectCreationGamePackageOutput(
    service: ForgeServiceClient,
    dialogs: StudioDialogClient,
    window: BrowserWindow,
    argument: CreationOutputGrantSelectArgument,
): Promise<StudioClientResult<StudioV4ReplyEnvelope>> {
    let selected: SaveDialogReturnValue;
    try {
        selected = await dialogs.showSaveDialog(window, {
            title: "Select game package output",
            buttonLabel: "Select output",
            nameFieldLabel: "Game package file",
            showsTagField: false,
            filters: [
                {
                    name: "World Forge game package",
                    extensions: ["wfgame"],
                },
            ],
        });
    } catch {
        return failure(
            "internal_error",
            "Game package output selection failed",
        );
    }
    if (selected.canceled || !selected.filePath) {
        return failure(
            "cancelled",
            "Game package output selection was cancelled",
        );
    }
    let selectedPath: string;
    try {
        selectedPath = validateSelectedExternalPath(selected.filePath);
    } catch {
        return failure(
            "invalid_request",
            "Game package output selection is invalid",
        );
    }
    return await requestCreationEvidenceNamed(
        service,
        "creation_output_grant.create",
        {
            workspace_id: argument.workspaceId,
            kind: "game_package_file",
            display_name: path.basename(selectedPath),
            path: selectedPath,
        },
    );
}

async function selectCreationGamePackageExtractionOutput(
    service: ForgeServiceClient,
    dialogs: StudioDialogClient,
    window: BrowserWindow,
    argument: CreationOutputGrantSelectArgument,
): Promise<StudioClientResult<StudioV4ReplyEnvelope>> {
    let selected: SaveDialogReturnValue;
    try {
        selected = await dialogs.showSaveDialog(window, {
            title: "Select game package extraction output",
            buttonLabel: "Select output",
            nameFieldLabel: "Extracted standalone game folder",
            showsTagField: false,
        });
    } catch {
        return failure(
            "internal_error",
            "Game package extraction output selection failed",
        );
    }
    if (selected.canceled || !selected.filePath) {
        return failure(
            "cancelled",
            "Game package extraction output selection was cancelled",
        );
    }
    let selectedPath: string;
    try {
        selectedPath = validateSelectedExternalPath(selected.filePath);
    } catch {
        return failure(
            "invalid_request",
            "Game package extraction output selection is invalid",
        );
    }
    return await requestCreationEvidenceNamed(
        service,
        "creation_output_grant.create",
        {
            workspace_id: argument.workspaceId,
            kind: "standalone_game_directory",
            display_name: path.basename(selectedPath),
            path: selectedPath,
        },
    );
}

async function selectCreationHeadlessEvidenceOutput(
    service: ForgeServiceClient,
    dialogs: StudioDialogClient,
    window: BrowserWindow,
    argument: CreationOutputGrantSelectArgument,
): Promise<StudioClientResult<StudioV5ReplyEnvelope>> {
    let selected: SaveDialogReturnValue;
    try {
        selected = await dialogs.showSaveDialog(window, {
            title: "Select headless evidence output",
            buttonLabel: "Select output",
            nameFieldLabel: "Headless evidence folder",
            showsTagField: false,
        });
    } catch {
        return failure(
            "internal_error",
            "Headless evidence output selection failed",
        );
    }
    if (selected.canceled || !selected.filePath) {
        return failure(
            "cancelled",
            "Headless evidence output selection was cancelled",
        );
    }
    let selectedPath: string;
    try {
        selectedPath = validateSelectedExternalPath(selected.filePath);
    } catch {
        return failure(
            "invalid_request",
            "Headless evidence output selection is invalid",
        );
    }
    return await requestCreationAuthorityNamed(
        service,
        "creation_output_grant.create",
        {
            workspace_id: argument.workspaceId,
            kind: "headless_evidence_directory",
            display_name: path.basename(selectedPath),
            path: selectedPath,
        },
    );
}

async function createSelectedCreationProject(
    service: ForgeServiceClient,
    dialogs: StudioDialogClient,
    window: BrowserWindow,
    argument: CreationProjectCreateArgument,
): Promise<StudioClientResult<StudioCreationWorkspaceReplyEnvelope>> {
    let selected: SaveDialogReturnValue;
    try {
        selected = await dialogs.showSaveDialog(window, {
            title:
                argument.projectKind === "game"
                    ? "Create game project"
                    : argument.projectKind === "asset_library"
                      ? "Create asset library"
                      : "Create universe library",
            buttonLabel: "Create project",
            nameFieldLabel: "Project folder",
            showsTagField: false,
        });
    } catch {
        return failure(
            "internal_error",
            "Creation project target selection failed",
        );
    }
    if (selected.canceled || !selected.filePath) {
        return failure(
            "cancelled",
            "Creation project target selection was cancelled",
        );
    }
    const grantReply = await requestCreationNamed(
        service,
        "creation_root_grant.create",
        {
            role: "new_target",
            display_name: argument.title,
            path: selected.filePath,
            expected_project_hash: null,
        },
    );
    const grant = creationGrantFromReply(
        grantReply,
        "creation_root_grant.create",
    );
    if (grant === null) return grantReply;
    const workspaceId = newCreationWorkspaceId();
    const gameFacets =
        argument.projectKind === "game"
            ? {
                  gameplay_family: argument.gameplayFamily,
                  initial_core_verb: argument.initialCoreVerb,
                  initial_core_loop: argument.initialCoreLoop,
                  world_presence: argument.worldPresence,
                  narrative_requirement: argument.narrativeRequirement,
                  narrative_authorship: argument.narrativeAuthorship,
                  narrative_topology: argument.narrativeTopology,
                  presentation_mode: argument.presentationMode,
                  runtime_support_intent: argument.runtimeSupportIntent,
                  ...("assetContentMode" in argument
                      ? { asset_content_mode: argument.assetContentMode }
                      : {}),
              }
            : {};
    const createParams = {
        workspace_id: workspaceId,
        grant_id: grant.grant_id,
        expected_grant_generation: grant.generation,
        project_kind: argument.projectKind,
        project_id: argument.projectId,
        title: argument.title,
        default_locale: argument.defaultLocale,
        project_version: argument.projectVersion,
        ...gameFacets,
    };
    const workspaceReply =
        "assetContentMode" in argument
            ? await requestCreationNamed(
                  service,
                  "creation_workspace.create",
                  createParams,
                  5,
              )
            : await requestCreationNamed(
                  service,
                  "creation_workspace.create",
                  createParams,
              );
    if (workspaceReply.ok && workspaceReply.value.kind !== "error") {
        return workspaceReply;
    }
    const recovery = await requestCreationNamed(
        service,
        "creation_workspace.recover",
        { workspace_id: workspaceId, expected_root_generation: 0 },
    );
    if (recovery.ok && recovery.value.kind !== "error") return recovery;
    await revokePrivateCreationGrant(service, grant);
    return workspaceReply;
}

function creationGrantFromReply(
    reply: StudioClientResult<StudioV3ReplyEnvelope>,
    expectedMethod: string,
): { grant_id: string; generation: number } | null {
    if (!reply.ok || reply.value.kind === "error") return null;
    if (
        reply.value.method !== expectedMethod ||
        !isRecord(reply.value.result)
    ) {
        return null;
    }
    const grant = reply.value.result.grant;
    if (
        !isRecord(grant) ||
        grant.format !== "world-forge.studio_creation_root_grant" ||
        grant.format_version !== 1 ||
        typeof grant.grant_id !== "string" ||
        !Number.isSafeInteger(grant.generation) ||
        (grant.generation as number) < 0
    ) {
        return null;
    }
    return { grant_id: grant.grant_id, generation: grant.generation as number };
}

async function revokePrivateCreationGrant(
    service: ForgeServiceClient,
    grant: { grant_id: string; generation: number },
): Promise<void> {
    await requestCreationNamed(service, "creation_root_grant.revoke", {
        grant_id: grant.grant_id,
        expected_generation: grant.generation,
    }).catch(() => undefined);
}

function newCreationWorkspaceId(): string {
    return `creation_${randomUUID().replaceAll("-", "")}`;
}

async function readSelectedCreationProjectIdentity(
    rootPath: string,
): Promise<{ contentHash: string; displayName: string }> {
    const projectPath = path.join(rootPath, "project.json");
    const noFollow = process.platform === "win32" ? 0 : fsConstants.O_NOFOLLOW;
    const handle = await open(projectPath, fsConstants.O_RDONLY | noFollow);
    try {
        const info = await handle.stat();
        if (
            !info.isFile() ||
            info.nlink !== 1 ||
            info.size > MAX_CREATION_PROJECT_BYTES
        ) {
            throw new Error("Selected creation project descriptor is invalid");
        }
        const payload = await handle.readFile();
        if (payload.byteLength !== info.size) {
            throw new Error("Selected creation project changed while reading");
        }
        const document = JSON.parse(
            new TextDecoder("utf-8", { fatal: true }).decode(payload),
        ) as unknown;
        if (
            !isRecord(document) ||
            document.format !== "world-forge.project" ||
            document.format_version !== 1 ||
            typeof document.content_hash !== "string" ||
            !SHA256_PATTERN.test(document.content_hash)
        ) {
            throw new Error("Selected creation project descriptor is invalid");
        }
        const displayName =
            typeof document.title === "string" && document.title.trim()
                ? document.title.trim()
                : path.basename(rootPath);
        return { contentHash: document.content_hash, displayName };
    } finally {
        await handle.close();
    }
}

async function selectExternalArtifactPath(
    dialogs: StudioDialogClient,
    window: BrowserWindow,
    argument: CreateExternalGrantParams,
): Promise<string | null> {
    if (argument.role === "source") {
        const isPackage = argument.artifactKind === "game_package";
        const options: OpenDialogOptions = {
            title: "Select external artifact source",
            properties: [isPackage ? "openFile" : "openDirectory"],
            ...(isPackage
                ? {
                      filters: [
                          {
                              name: "World Forge game package",
                              extensions: ["wfgame"],
                          },
                      ],
                  }
                : {}),
        };
        const selection = await dialogs.showOpenDialog(window, options);
        if (selection.canceled) {
            return null;
        }
        if (selection.filePaths.length !== 1) {
            throw new TypeError(
                "External source selection must contain one path",
            );
        }
        return validateSelectedExternalPath(selection.filePaths[0]);
    }

    const isPackage = argument.artifactKind === "game_package";
    const options: SaveDialogOptions = {
        title: "Select external artifact target",
        ...(isPackage
            ? {
                  filters: [
                      {
                          name: "World Forge game package",
                          extensions: ["wfgame"],
                      },
                  ],
              }
            : {}),
    };
    const selection = await dialogs.showSaveDialog(window, options);
    if (selection.canceled) {
        return null;
    }
    if (selection.filePath === undefined) {
        throw new TypeError("External target selection did not return a path");
    }
    return validateSelectedExternalPath(selection.filePath);
}

function validateSelectedExternalPath(value: string): string {
    if (
        !path.isAbsolute(value) ||
        value.normalize("NFC") !== value ||
        containsInvalidUnicode(value) ||
        [...value].some((character) => {
            const code = character.charCodeAt(0);
            return code === 0 || code === 10 || code === 13;
        })
    ) {
        throw new TypeError(
            "External artifact selection returned an invalid path",
        );
    }
    return value;
}

async function requestExternalGrantCreate(
    service: ForgeServiceClient,
    argument: CreateExternalGrantParams,
    selectedPath: string,
): Promise<StudioClientResult<StudioV2ReplyEnvelope>> {
    const requestId = randomUUID();
    const params = {
        workspace_id: argument.workspaceId,
        operation: argument.operation,
        role: argument.role,
        artifact_kind: argument.artifactKind,
        display_name: path.basename(selectedPath),
        path: selectedPath,
        expected_content_hash: argument.expectedContentHash,
    };
    return await capture(() =>
        service
            .request(
                requestId,
                "external_grant.create",
                params,
                DEFAULT_REQUEST_TIMEOUT_MS,
                2,
            )
            .then((reply) => {
                const validated = validateExternalNamedReply(
                    reply,
                    requestId,
                    "external_grant.create",
                );
                if (validated.kind === "error") {
                    return validated;
                }
                const grant = requireExternalGrant(validated.result);
                if (
                    grant.workspace_id !== argument.workspaceId ||
                    grant.operation !== argument.operation ||
                    grant.role !== argument.role ||
                    grant.artifact_kind !== argument.artifactKind ||
                    grant.display_name !== path.basename(selectedPath) ||
                    grant.expected_content_hash !== argument.expectedContentHash
                ) {
                    throw new StudioProtocolError(
                        "Forge Studio returned a mismatched external grant",
                    );
                }
                return validated;
            }),
    );
}

async function requestExternalGrantById(
    service: ForgeServiceClient,
    method: "external_grant.get" | "external_grant.revoke",
    grantId: string,
): Promise<StudioClientResult<StudioV2ReplyEnvelope>> {
    const requestId = randomUUID();
    return await capture(() =>
        service
            .request(
                requestId,
                method,
                { grant_id: grantId },
                DEFAULT_REQUEST_TIMEOUT_MS,
                2,
            )
            .then((reply) => {
                const validated = validateExternalNamedReply(
                    reply,
                    requestId,
                    method,
                );
                if (validated.kind === "error") {
                    return validated;
                }
                const grant = requireExternalGrant(validated.result);
                if (
                    grant.grant_id !== grantId ||
                    (method === "external_grant.revoke" &&
                        grant.state !== "revoked")
                ) {
                    throw new StudioProtocolError(
                        "Forge Studio returned a mismatched external grant",
                    );
                }
                return validated;
            }),
    );
}

async function requestExternalJobCreate(
    service: ForgeServiceClient,
    workspaceId: string,
    operation: StudioExternalOperation,
    input: Readonly<Record<string, unknown>>,
): Promise<StudioClientResult<StudioV2ReplyEnvelope>> {
    const requestId = randomUUID();
    return await capture(() =>
        service
            .request(
                requestId,
                "job.create",
                { workspace_id: workspaceId, operation, input },
                DEFAULT_REQUEST_TIMEOUT_MS,
                2,
            )
            .then((reply) => {
                const validated = validateExternalNamedReply(
                    reply,
                    requestId,
                    "job.create",
                );
                if (validated.kind === "error") {
                    return validated;
                }
                const job = requireExternalJob(validated.result);
                if (
                    job.workspace_id !== workspaceId ||
                    job.operation !== operation ||
                    !hasExactScalarFields(job.input, input)
                ) {
                    throw new StudioProtocolError(
                        "Forge Studio returned a mismatched external job",
                    );
                }
                return validated;
            }),
    );
}

async function requestExternalJobById(
    service: ForgeServiceClient,
    method: "job.get" | "job.cancel",
    jobId: string,
): Promise<StudioClientResult<StudioV2ReplyEnvelope>> {
    const requestId = randomUUID();
    return await capture(() =>
        service
            .request(
                requestId,
                method,
                { job_id: jobId },
                DEFAULT_REQUEST_TIMEOUT_MS,
                2,
            )
            .then((reply) => {
                const validated = validateExternalNamedReply(
                    reply,
                    requestId,
                    method,
                );
                if (validated.kind === "error") {
                    return validated;
                }
                const job = requireExternalJob(validated.result);
                if (job.job_id !== jobId) {
                    throw new StudioProtocolError(
                        "Forge Studio returned a mismatched external job",
                    );
                }
                return validated;
            }),
    );
}

async function requestExternalJobList(
    service: ForgeServiceClient,
    params: {
        workspace_id?: string;
        state?: StudioExternalJobsListParams["state"];
        limit?: number;
    },
): Promise<StudioClientResult<StudioV2ReplyEnvelope>> {
    const requestId = randomUUID();
    return await capture(() =>
        service
            .request(
                requestId,
                "job.list",
                params,
                DEFAULT_REQUEST_TIMEOUT_MS,
                2,
            )
            .then((reply) => {
                const validated = validateExternalNamedReply(
                    reply,
                    requestId,
                    "job.list",
                );
                if (validated.kind === "error") {
                    return validated;
                }
                if (
                    !isRecord(validated.result) ||
                    !Array.isArray(validated.result.jobs)
                ) {
                    throw new StudioProtocolError(
                        "Forge Studio returned an invalid external job list",
                    );
                }
                return {
                    ...validated,
                    result: {
                        jobs: validated.result.jobs.filter(
                            (job) => isRecord(job) && job.format_version === 3,
                        ),
                    },
                } as StudioV2ReplyEnvelope;
            }),
    );
}

async function requestExternalJobRecovery(
    service: ForgeServiceClient,
    jobId: string,
    action: "resume" | "rollback",
): Promise<StudioClientResult<StudioV2ReplyEnvelope>> {
    const requestId = randomUUID();
    return await capture(() =>
        service
            .request(
                requestId,
                "job.recover",
                { job_id: jobId, action },
                DEFAULT_REQUEST_TIMEOUT_MS,
                2,
            )
            .then((reply) => {
                const validated = validateExternalNamedReply(
                    reply,
                    requestId,
                    "job.recover",
                );
                if (validated.kind === "error") {
                    return validated;
                }
                if (requireExternalJob(validated.result).job_id !== jobId) {
                    throw new StudioProtocolError(
                        "Forge Studio returned a mismatched external recovery job",
                    );
                }
                return validated;
            }),
    );
}

function validateExternalNamedReply(
    value: unknown,
    requestId: string,
    method: StudioV2Method,
): StudioV2ReplyEnvelope {
    if (
        !validateStudioEnvelope(value) ||
        value.protocol_version !== 2 ||
        (value.kind !== "response" && value.kind !== "error") ||
        value.request_id !== requestId ||
        (value.kind === "response" && value.method !== method)
    ) {
        throw new StudioProtocolError(
            `Forge Studio returned an invalid ${method} v2 reply`,
        );
    }
    if (value.kind === "error") {
        return {
            ...value,
            error: {
                code: value.error.code,
                message: value.error.message,
                details: {},
            },
        };
    }
    return value;
}

function requireExternalGrant(result: unknown): Record<string, unknown> {
    if (
        !isRecord(result) ||
        !isRecord(result.grant) ||
        result.grant.format !== "rpg-world-forge.studio_external_grant" ||
        result.grant.format_version !== 1
    ) {
        throw new StudioProtocolError(
            "Forge Studio returned an invalid external grant",
        );
    }
    return result.grant;
}

function requireExternalJob(result: unknown): Record<string, unknown> {
    if (
        !isRecord(result) ||
        !isRecord(result.job) ||
        result.job.format !== "rpg-world-forge.studio_job" ||
        result.job.format_version !== 3
    ) {
        throw new StudioProtocolError(
            "Forge Studio returned an invalid external job",
        );
    }
    return result.job;
}

async function requestRead(
    service: ForgeServiceClient,
    method: StudioReadMethod,
    params: Record<string, unknown>,
): Promise<StudioClientResult<StudioReplyEnvelope>> {
    return await requestNamed(service, method, params);
}

async function requestNamed(
    service: ForgeServiceClient,
    method: StudioCapabilityMethod,
    params: Record<string, unknown>,
): Promise<StudioClientResult<StudioReplyEnvelope>> {
    const requestId = randomUUID();
    return await capture(() =>
        service
            .request(requestId, method, params, DEFAULT_REQUEST_TIMEOUT_MS)
            .then((reply) => validateNamedReply(reply, requestId, method)),
    );
}

type CreationProtocolMethod =
    | "creation_root_grant.create"
    | "creation_root_grant.revoke"
    | "creation_workspace.create"
    | "creation_workspace.recover"
    | "creation_workspace.register"
    | "creation_workspace.list"
    | "creation_workspace.open"
    | "creation_document.list"
    | "creation_document.read"
    | "creation_changeset.create"
    | "creation_changeset.get"
    | "creation_changeset.diff"
    | "creation_changeset.approve"
    | "creation_changeset.apply"
    | "creation_changeset.recover"
    | "creation_workflow.get"
    | "creation_workflow.reconcile"
    | "creation_phase.read"
    | "creation_phase.validate"
    | "creation_phase.complete"
    | "creation_phase.reopen"
    | "creation_readiness.inspect";

async function requestCreationNamed(
    service: ForgeServiceClient,
    method: CreationProtocolMethod,
    params: Record<string, unknown>,
): Promise<StudioClientResult<StudioV3ReplyEnvelope>>;
async function requestCreationNamed(
    service: ForgeServiceClient,
    method: CreationProtocolMethod,
    params: Record<string, unknown>,
    protocolVersion: 3,
): Promise<StudioClientResult<StudioV3ReplyEnvelope>>;
async function requestCreationNamed(
    service: ForgeServiceClient,
    method: CreationProtocolMethod,
    params: Record<string, unknown>,
    protocolVersion: 5,
): Promise<StudioClientResult<StudioV5ReplyEnvelope>>;
async function requestCreationNamed(
    service: ForgeServiceClient,
    method: CreationProtocolMethod,
    params: Record<string, unknown>,
    protocolVersion: 3 | 5 = 3,
): Promise<StudioClientResult<StudioCreationWorkspaceReplyEnvelope>> {
    const requestId = randomUUID();
    return await capture(() =>
        service
            .request(requestId, method, params, DEFAULT_REQUEST_TIMEOUT_MS, protocolVersion)
            .then((reply) =>
                protocolVersion === 5
                    ? validateCreationNamedReply(
                          reply,
                          requestId,
                          method,
                          protocolVersion,
                      )
                    : (reply as StudioV3ReplyEnvelope),
            ),
    );
}

function validateCreationNamedReply(
    value: unknown,
    requestId: string,
    method: string,
    protocolVersion: 3 | 5,
): StudioCreationWorkspaceReplyEnvelope {
    if (
        !validateStudioEnvelope(value) ||
        value.protocol_version !== protocolVersion ||
        (value.kind !== "response" && value.kind !== "error") ||
        value.request_id !== requestId ||
        (value.kind === "response" && value.method !== method)
    ) {
        throw new StudioProtocolError(
            `Forge Studio returned an invalid ${method} reply`,
        );
    }
    return value;
}

async function requestCreationEvidenceNamed(
    service: ForgeServiceClient,
    method:
        | "creation_artifact.list"
        | "creation_artifact.inspect"
        | "creation_evidence.inspect"
        | "creation_output_grant.create"
        | "creation_output_grant.get"
        | "creation_output_grant.list"
        | "creation_output_grant.revoke"
        | "creation_job.create"
        | "creation_job.get"
        | "creation_job.list"
        | "creation_job.cancel"
        | "creation_job.recover"
        | "creation_event.list",
    params: Record<string, unknown>,
): Promise<StudioClientResult<StudioV4ReplyEnvelope>> {
    const requestId = randomUUID();
    if (method === "creation_job.create") {
        validateRendererCreationJobCreateBoundary(params);
    }
    return await capture(() =>
        service
            .request(requestId, method, params, DEFAULT_REQUEST_TIMEOUT_MS, 4)
            .then((reply) => reply as StudioV4ReplyEnvelope),
    );
}

async function getCreationAuthorityCapabilities(
    service: ForgeServiceClient,
): Promise<StudioCreationAuthorityCapabilities> {
    const reply = await service.request(
        randomUUID(),
        "service.initialize",
        {},
        DEFAULT_REQUEST_TIMEOUT_MS,
        5,
    );
    if (
        reply.kind !== "response" ||
        reply.protocol !== "rpg-world-forge.studio_protocol" ||
        reply.protocol_version !== 5 ||
        reply.method !== "service.initialize" ||
        !isRecord(reply.result)
    ) {
        throw new Error("Forge Studio authority capability projection failed closed");
    }
    const capabilities = reply.result.capabilities;
    if (
        reply.result.service !== "world-forge.studio" ||
        reply.result.service_version !== 5 ||
        reply.result.protocol !== "rpg-world-forge.studio_protocol" ||
        reply.result.protocol_version !== 5 ||
        !isRecord(capabilities) ||
        !hasExactKeys(capabilities, [
            "asset_authority_reviews",
            "asset_previews",
            "asset_release_authority",
            "creation_asset_previews",
            "creation_evidence_projection",
            "creation_jobs",
            "creation_materialization_bundle",
            "creation_output_grants",
            "creation_preview_pre_release",
            "creation_runtime_bundle",
            "creation_runtime_compose",
            "game_package_extraction",
            "game_packaging",
            "materialization_execution",
            "runtime_headless_authority",
        ]) ||
        capabilities.asset_authority_reviews !== true ||
        capabilities.asset_release_authority !== true ||
        capabilities.runtime_headless_authority !== true ||
        capabilities.creation_preview_pre_release !== true
    ) {
        throw new Error("Forge Studio authority capability projection failed closed");
    }
    return {
        protocolVersion: 5,
        asset_authority_reviews: true,
        asset_release_authority: true,
        runtime_headless_authority: true,
        creation_preview_pre_release: true,
    };
}

function hasExactKeys(value: Record<string, unknown>, keys: readonly string[]): boolean {
    const actual = Object.keys(value).sort();
    const expected = [...keys].sort();
    return actual.length === expected.length && actual.every((key, index) => key === expected[index]);
}

async function requestCreationAuthorityNamed(
    service: ForgeServiceClient,
    method:
        | "creation_artifact.inspect"
        | "creation_output_grant.create"
        | "creation_output_grant.get"
        | "creation_output_grant.list"
        | "creation_preview.open"
        | "creation_preview.read"
        | "creation_preview.close"
        | "creation_job.create"
        | "creation_job.get"
        | "creation_job.cancel"
        | "creation_job.recover",
    params: Record<string, unknown>,
): Promise<StudioClientResult<StudioV5ReplyEnvelope>> {
    const requestId = randomUUID();
    return await capture(() =>
        service
            .request(requestId, method, params, DEFAULT_REQUEST_TIMEOUT_MS, 5)
            .then((reply) => reply as StudioV5ReplyEnvelope),
    );
}

async function requestMainOwnedAssetQaReview(
    service: ForgeServiceClient,
    modal: StudioAuthorityModalClient,
    window: BrowserWindow,
    argument: CreationAuthorityReviewArgument,
): Promise<StudioClientResult<StudioV5ReplyEnvelope>> {
    const inspected = await inspectAuthorityArtifact(
        service,
        argument.qaReportArtifactId,
    );
    if (!inspected.ok) return inspected.error;
    let qaSubject: ReturnType<typeof requireExactArtifactSubject>;
    let criteria: string[];
    try {
        qaSubject = requireExactArtifactSubject(
            inspected.artifact,
            "world-forge.asset_qa_report",
            1,
        );
        criteria = extractCriteria(inspected.projection);
    } catch (error) {
        return failure("invalid_request", describeUnknown(error));
    }
    const authority = inspected.authority;
    if (authority.workspaceId !== argument.workspaceId) {
        return failure(
            "invalid_request",
            "Creation authority QA report is outside the selected workspace",
        );
    }
    const preview = await openReadAndCloseQaCandidatePreview(
        service,
        authority,
        argument,
    );
    if (!preview.ok) return preview.error;
    const nonce = randomUUID();
    const reply = await modal.requestReview(window, {
        nonce,
        title: "Asset QA Review",
        preview: {
            artifactId: argument.qaReportArtifactId,
            subject: qaSubject,
            mediaType: preview.mediaType,
            data: new Uint8Array(preview.bytes),
            sha256: preview.sha256,
            byteLength: preview.byteLength,
        },
        criteria,
    });
    const gesture = validateAuthorityReviewReply(reply, {
        expectedNonce: nonce,
        expectedDecisionCount: criteria.length,
    });
    const params = deriveAssetQaReviewJobCreateParams({
        authority,
        qaReportArtifactId: argument.qaReportArtifactId,
        outputRole: argument.outputRole,
        reviewReceiptId: stableMainEntityId("review_receipt", [
            authority.workspaceId,
            argument.qaReportArtifactId,
            argument.outputRole,
            preview.sha256,
        ]),
        criterionHashes: criteria,
        gesture,
    });
    return await requestCreationAuthorityNamed(
        service,
        "creation_job.create",
        params,
    );
}

async function requestMainOwnedAssetReleaseAuthorization(
    service: ForgeServiceClient,
    argument: CreationAuthorityReleaseArgument,
): Promise<StudioClientResult<StudioV5ReplyEnvelope>> {
    const inspectedReviews = [];
        let authority: CreationAuthoritySnapshot | null = null;
    for (const artifactId of argument.reviewReceiptArtifactIds) {
        const inspected = await inspectAuthorityArtifact(service, artifactId);
        if (!inspected.ok) return inspected.error;
        let subject: ReturnType<typeof requireExactArtifactSubject>;
        let status: "approved" | "rejected";
        try {
            subject = requireExactArtifactSubject(
                inspected.artifact,
                "world-forge.asset_qa_review_receipt",
                1,
            );
            status = requireClosedReviewStatus(inspected.projection.status);
        } catch (error) {
            return failure("invalid_request", describeUnknown(error));
        }
        if (authority === null) {
            authority = inspected.authority;
        } else if (!sameAuthority(authority, inspected.authority)) {
            return failure(
                "invalid_request",
                "Creation authority review artifacts cross authority boundaries",
            );
        }
        inspectedReviews.push({
            artifactId,
            receiptId: subject.id,
            status,
        });
    }
    if (authority === null) {
        return failure("invalid_request", "Creation authority review is required");
    }
    if (authority.workspaceId !== argument.workspaceId) {
        return failure(
            "invalid_request",
            "Creation authority review is outside the selected workspace",
        );
    }
    const targetGrant = await getAuthorityGrant(service, argument.targetGrantId, {
        kind: "generic_assetpack_directory",
        state: "ready",
        publication: "none",
        label: "assetpack release target grant",
    });
    if (!targetGrant.ok) return targetGrant.error;
    const params = deriveAssetReleaseAuthorizeJobCreateParams({
        authority,
        reviewArtifacts: inspectedReviews,
        targetGrant: {
            grantId: targetGrant.grantId,
            generation: targetGrant.generation,
        },
    });
    return await requestCreationAuthorityNamed(
        service,
        "creation_job.create",
        params,
    );
}

async function requestMainOwnedRuntimeHeadlessVerification(
    service: ForgeServiceClient,
    argument: CreationAuthorityHeadlessArgument,
): Promise<StudioClientResult<StudioV5ReplyEnvelope>> {
    const runtimeBundle = await inspectAuthorityArtifact(
        service,
        argument.runtimeBundleArtifactId,
    );
    if (!runtimeBundle.ok) return runtimeBundle.error;
    if (runtimeBundle.authority.workspaceId !== argument.workspaceId) {
        return failure(
            "invalid_request",
            "Creation authority runtime bundle is outside the selected workspace",
        );
    }
    let runtimeBundleSubject: ReturnType<typeof requireExactArtifactSubject>;
    try {
        runtimeBundleSubject = requireExactArtifactSubject(
            runtimeBundle.artifact,
            "world-forge.game_runtime_bundle",
            1,
        );
        requireFutureCandidateProducerJobId(
            runtimeBundle.artifact,
            "Creation authority runtime bundle producer is invalid",
        );
    } catch (error) {
        return failure("invalid_request", describeUnknown(error));
    }
    const script = await inspectAuthorityArtifact(
        service,
        argument.headlessScriptArtifactId,
    );
    if (!script.ok) return script.error;
    if (!sameAuthority(runtimeBundle.authority, script.authority)) {
        return failure(
            "invalid_request",
            "Creation authority headless script crosses authority boundaries",
        );
    }
    let scriptSubject: ReturnType<typeof requireExactArtifactSubject>;
    try {
        scriptSubject = requireExactArtifactSubject(
            script.artifact,
            "world-forge.game_execution_script",
            1,
        );
    } catch (error) {
        return failure("invalid_request", describeUnknown(error));
    }
    const sourceGrant = await getAuthorityGrant(service, argument.sourceGrantId, {
        kind: "game_runtime_bundle_directory",
        state: "published",
        publication: "runtime_bundle",
        label: "runtime bundle grant",
        authority: runtimeBundle.authority,
        formatVersion: 2,
        runtimeBundle: {
            artifactId: extractArtifactId(runtimeBundle.artifact),
            subject: runtimeBundleSubject,
        },
    });
    if (!sourceGrant.ok) return sourceGrant.error;
    const artifacts = await deriveHeadlessArtifactsFromInspectedLineage(
        service,
        runtimeBundle.authority,
        extractArtifactId(runtimeBundle.artifact),
        runtimeBundleSubject,
        runtimeBundle.projection.lineage,
        extractArtifactId(script.artifact),
        scriptSubject.contentHash,
    );
    if (!artifacts.ok) return artifacts.error;
    const targetGrant = await getAuthorityGrant(service, argument.targetGrantId, {
        kind: "headless_evidence_directory",
        state: "ready",
        publication: "none",
        label: "headless evidence target grant",
        authority: runtimeBundle.authority,
        formatVersion: 6,
    });
    if (!targetGrant.ok) return targetGrant.error;
    const params = deriveRuntimeHeadlessVerifyJobCreateParams({
        authority: runtimeBundle.authority,
        artifacts: artifacts.artifacts,
        sourceGrant: {
            grantId: sourceGrant.grantId,
            generation: sourceGrant.generation,
        },
        targetGrant: {
            grantId: targetGrant.grantId,
            generation: targetGrant.generation,
        },
        platformId: validateRuntimeHeadlessPlatformId(argument.platformId),
    });
    return await requestCreationAuthorityNamed(
        service,
        "creation_job.create",
        params,
    );
}

async function inspectAuthorityArtifact(
    service: ForgeServiceClient,
    artifactId: string,
): Promise<
    | {
          ok: true;
          authority: CreationAuthoritySnapshot;
          artifact: Record<string, unknown>;
          projection: Record<string, unknown> & {
              status: string | null;
              facts: unknown[];
              lineage: { artifact_id: string }[];
          };
      }
    | { ok: false; error: StudioClientResult<StudioV5ReplyEnvelope> }
> {
    const inspected = await requestCreationAuthorityNamed(
        service,
        "creation_artifact.inspect",
        { artifact_id: artifactId },
    );
    if (!inspected.ok || inspected.value.kind === "error") {
        return { ok: false, error: inspected };
    }
    const result = inspected.value.result;
    if (!isRecord(result)) {
        return { ok: false, error: failure("invalid_request", "Creation artifact result is invalid") };
    }
    const authority = result.authority;
    const artifactSnapshotHash = validateSha256(
        result.artifact_snapshot_hash,
        "creation artifact snapshot hash",
    );
    const artifact = result.artifact;
    const projection = result.projection;
    if (!isRecord(authority) || !isRecord(artifact) || !isRecord(projection)) {
        return { ok: false, error: failure("invalid_request", "Creation artifact projection is invalid") };
    }
    const lineage = Array.isArray(projection.lineage)
        ? projection.lineage
              .filter(isRecord)
              .map((item) => ({ artifact_id: validateEntityId(item.artifact_id, "creation lineage artifact") }))
        : [];
    return {
        ok: true,
        authority: {
            workspaceId: validateWorkspaceId(authority.workspace_id),
            rootGeneration: validateGeneration(
                authority.root_generation,
                "creation authority root generation",
            ),
            sourceRevision: validateSha256(
                authority.source_revision,
                "creation authority source revision",
            ),
            workflowStatusHash: validateNullableSha256(
                authority.workflow_status_hash,
                "creation authority workflow status hash",
            ),
            artifactSnapshotHash,
        },
        artifact,
        projection: {
            ...projection,
            status:
                typeof projection.status === "string"
                    ? projection.status
                    : null,
            facts: Array.isArray(projection.facts) ? projection.facts : [],
            lineage,
        },
    };
}

async function getAuthorityGrant(
    service: ForgeServiceClient,
    grantId: string,
    expected?: {
        kind: string;
        state: string;
        publication: "none" | "runtime_bundle";
        label: string;
        authority?: CreationAuthoritySnapshot;
        formatVersion?: number;
        runtimeBundle?: {
            artifactId: string;
            subject: ReturnType<typeof requireExactArtifactSubject>;
        };
    },
): Promise<
    | { ok: true; grantId: string; generation: number }
    | { ok: false; error: StudioClientResult<StudioV5ReplyEnvelope> }
> {
    const grantResult = await requestCreationAuthorityNamed(
        service,
        "creation_output_grant.get",
        { grant_id: grantId },
    );
    if (!grantResult.ok || grantResult.value.kind === "error") {
        return { ok: false, error: grantResult };
    }
    const result = grantResult.value.result;
    const grant = isRecord(result) ? result.grant : null;
    if (!isRecord(grant)) {
        return { ok: false, error: failure("invalid_request", "Creation output grant result is invalid") };
    }
    const validatedGrantId = validateEntityId(grant.grant_id, "creation output grant");
    const generation = validateGeneration(
        grant.generation,
        "creation output grant generation",
    );
    if (expected !== undefined) {
        const publication = isRecord(grant.publication) ? grant.publication : null;
        if (
            expected.formatVersion !== undefined &&
            (grant.format !== "world-forge.studio_creation_output_grant" ||
                grant.format_version !== expected.formatVersion)
        ) {
            return {
                ok: false,
                error: failure(
                    "invalid_request",
                    `Creation authority ${expected.label} must be exact v${expected.formatVersion}`,
                ),
            };
        }
        if (
            expected.authority !== undefined &&
            grant.workspace_id !== expected.authority.workspaceId
        ) {
            return {
                ok: false,
                error: failure(
                    "invalid_request",
                    `Creation authority ${expected.label} workspace is invalid`,
                ),
            };
        }
        if (grant.kind !== expected.kind || grant.state !== expected.state) {
            return {
                ok: false,
                error: failure(
                    "invalid_request",
                    `Creation authority ${expected.label} kind or state is invalid`,
                ),
            };
        }
        if (expected.publication === "none" && grant.publication !== null) {
            return {
                ok: false,
                error: failure(
                    "invalid_request",
                    `Creation authority ${expected.label} publication is invalid`,
                ),
            };
        }
        if (expected.publication === "runtime_bundle") {
            const runtimeBundle = publication?.runtime_bundle;
            if (
                publication === null ||
                publication.kind !== "game_runtime_bundle_directory" ||
                publication.state !== "published" ||
                !isRecord(runtimeBundle) ||
                runtimeBundle.format !== "world-forge.game_runtime_bundle" ||
                runtimeBundle.format_version !== 1
            ) {
                return {
                    ok: false,
                    error: failure(
                        "invalid_request",
                        `Creation authority ${expected.label} publication is invalid`,
                    ),
                };
            }
            if (expected.runtimeBundle !== undefined) {
                const publicationArtifactId =
                    typeof publication?.artifact_id === "string"
                        ? publication.artifact_id
                        : null;
                if (
                    publicationArtifactId !== expected.runtimeBundle.artifactId ||
                    runtimeBundle.id !== expected.runtimeBundle.subject.id ||
                    runtimeBundle.content_hash !==
                        expected.runtimeBundle.subject.contentHash
                ) {
                    return {
                        ok: false,
                        error: failure(
                            "invalid_request",
                            `Creation authority ${expected.label} is not bound to the selected runtime bundle`,
                        ),
                    };
                }
            }
        }
    }
    return {
        ok: true,
        grantId: validatedGrantId,
        generation,
    };
}

async function openReadAndCloseQaCandidatePreview(
    service: ForgeServiceClient,
    authority: CreationAuthoritySnapshot,
    argument: CreationAuthorityReviewArgument,
): Promise<
    | {
          ok: true;
          sha256: string;
          byteLength: number;
          mediaType: "image/png" | "audio/wav" | "text/plain";
          bytes: Uint8Array;
      }
    | { ok: false; error: StudioClientResult<StudioV5ReplyEnvelope> }
> {
    let handle: string | null = null;
    let primaryError: StudioClientResult<StudioV5ReplyEnvelope> | null = null;
    let verified:
        | {
              bytes: Uint8Array;
              sha256: string;
              byteLength: number;
              mediaType: "image/png" | "audio/wav" | "text/plain";
          }
        | null = null;
    const opened = await requestCreationAuthorityNamed(
        service,
        "creation_preview.open",
        {
            source_kind: "qa_review_candidate",
            workspace_id: authority.workspaceId,
            expected_root_generation: authority.rootGeneration,
            expected_source_revision: authority.sourceRevision,
            expected_workflow_status_hash: authority.workflowStatusHash,
            expected_artifact_snapshot_hash: authority.artifactSnapshotHash,
            qa_report_artifact_id: argument.qaReportArtifactId,
            asset_id: argument.qaReportArtifactId,
            output_role: argument.outputRole,
        },
    );
    if (!opened.ok || opened.value.kind === "error") {
        return { ok: false, error: opened };
    }
    const result = opened.value.result;
    const preview = isRecord(result) ? result.preview : null;
    if (!isRecord(preview)) {
        return { ok: false, error: failure("invalid_request", "Creation authority preview is invalid") };
    }
    handle = validateAssetPreviewHandle(preview.handle);
    const byteLength = validateGeneration(
        preview.byte_length,
        "creation authority preview byte length",
    );
    const sha256 = validateSha256(
        preview.sha256,
        "creation authority preview sha256",
    );
    const mediaType = validateAuthorityPreviewMediaType(preview.media_type);
    let closeError: StudioClientResult<StudioV5ReplyEnvelope> | null = null;
    try {
        const bytes = await readVerifiedCreationPreviewBytes({
            handle,
            byteLength,
            sha256,
            read: async (_handle, sequence) => {
                const read = await requestCreationAuthorityNamed(
                    service,
                    "creation_preview.read",
                    { handle, sequence },
                );
                if (!read.ok || read.value.kind === "error") {
                    throw new Error("Creation authority preview read failed");
                }
                const readResult = read.value.result;
                return {
                    sequence: validateGeneration(
                        isRecord(readResult) ? readResult.sequence : null,
                        "creation authority preview sequence",
                    ),
                    dataBase64: String(
                        isRecord(readResult) ? readResult.data_base64 : "",
                    ),
                    cumulativeBytes: validateGeneration(
                        isRecord(readResult)
                            ? readResult.cumulative_bytes
                            : null,
                        "creation authority preview cumulative bytes",
                    ),
                    cumulativeSha256: validateSha256(
                        isRecord(readResult)
                            ? readResult.cumulative_sha256
                            : null,
                        "creation authority preview cumulative sha256",
                    ),
                    eof: isRecord(readResult) && readResult.eof === true,
                };
            },
        });
        verified = { bytes, sha256, byteLength, mediaType };
    } catch (error) {
        primaryError = failure(
            "invalid_request",
            error instanceof Error
                ? error.message
                : "Creation authority preview verification failed",
        );
    } finally {
        const closed = await requestCreationAuthorityNamed(
            service,
            "creation_preview.close",
            { handle },
        );
        if (
            !closed.ok ||
            closed.value.kind === "error" ||
            !isRecord(closed.value.result) ||
            closed.value.result.handle !== handle ||
            closed.value.result.closed !== true
        ) {
            closeError = failure(
                "invalid_request",
                "Creation authority preview close failed",
            );
        }
    }
    if (primaryError !== null) {
        return { ok: false, error: primaryError };
    }
    if (closeError !== null) {
        return { ok: false, error: closeError };
    }
    if (verified === null) {
        return { ok: false, error: failure("invalid_request", "Creation authority preview verification failed") };
    }
    return { ok: true, ...verified };
}

async function deriveHeadlessArtifactsFromInspectedLineage(
    service: ForgeServiceClient,
    authority: CreationAuthoritySnapshot,
    runtimeBundleArtifactId: string,
    runtimeBundleSubject: ReturnType<typeof requireExactArtifactSubject>,
    lineage: readonly { artifact_id: string }[],
    headlessScriptArtifactId: string,
    headlessScriptHash: string,
): Promise<
    | { ok: true; artifacts: RuntimeHeadlessArtifactSelections }
    | { ok: false; error: StudioClientResult<StudioV5ReplyEnvelope> }
> {
    const byFormat = new Map<string, {
        artifactId: string;
        subject: ReturnType<typeof requireExactArtifactSubject>;
        producerJobId: string;
    }>();
    byFormat.set("world-forge.game_runtime_bundle", {
        artifactId: runtimeBundleArtifactId,
        subject: runtimeBundleSubject,
        producerJobId: "selected_runtime_bundle",
    });
    let matchedHeadlessScript = false;
    for (const item of lineage) {
        const inspected = await inspectAuthorityArtifact(service, item.artifact_id);
        if (!inspected.ok) return inspected;
        if (!sameAuthority(authority, inspected.authority)) {
            return {
                ok: false,
                error: failure(
                    "invalid_request",
                    "Creation authority headless lineage crosses authority snapshot boundaries",
                ),
            };
        }
        try {
            const subject = requireAnyExactArtifactSubject(inspected.artifact);
            const producerJobId = requireFutureCandidateProducerJobId(
                inspected.artifact,
                "Creation authority headless lineage producer is invalid",
            );
            if (
                subject.format === "world-forge.game_execution_script" &&
                item.artifact_id === headlessScriptArtifactId &&
                subject.contentHash === headlessScriptHash
            ) {
                matchedHeadlessScript = true;
            }
            if (byFormat.has(subject.format)) {
                return {
                    ok: false,
                    error: failure(
                        "invalid_request",
                        "Creation authority headless lineage is duplicated or ambiguous",
                    ),
                };
            }
            byFormat.set(subject.format, {
                artifactId: item.artifact_id,
                subject,
                producerJobId,
            });
        } catch (error) {
            return { ok: false, error: failure("invalid_request", describeUnknown(error)) };
        }
    }
    if (!matchedHeadlessScript) {
        return {
            ok: false,
            error: failure(
                "invalid_request",
                "Creation authority headless script is not in the selected runtime lineage",
            ),
        };
    }
    const artifacts = {
        gamepack: byFormat.get("world-forge.gamepack")?.artifactId,
        assetInventory: byFormat.get("world-forge.asset_inventory")?.artifactId,
        assetpack: byFormat.get("world-forge.assetpack")?.artifactId,
        assetReleaseAuthority: byFormat.get("world-forge.asset_release_authority")?.artifactId,
        runtimeSnapshot: byFormat.get("world-forge.game_runtime_snapshot")?.artifactId,
        runtimeAdapterRegistry: byFormat.get("world-forge.runtime_adapter_registry")?.artifactId,
        runtimeComposition: byFormat.get("world-forge.game_runtime_composition")?.artifactId,
        runtimeBundle: byFormat.get("world-forge.game_runtime_bundle")?.artifactId,
        headlessScript: byFormat.get("world-forge.game_execution_script")?.artifactId,
    };
    if (Object.values(artifacts).some((value) => value === undefined)) {
        return {
            ok: false,
            error: failure(
                "invalid_request",
                "Creation authority headless lineage projection is incomplete",
            ),
        };
    }
    const releaseProof = await proveExactRetainedV11ReleaseAuthority(
        service,
        byFormat,
        authority,
    );
    if (!releaseProof.ok) return releaseProof;
    return { ok: true, artifacts: artifacts as RuntimeHeadlessArtifactSelections };
}

async function proveExactRetainedV11ReleaseAuthority(
    service: ForgeServiceClient,
    byFormat: ReadonlyMap<string, {
        artifactId: string;
        subject: ReturnType<typeof requireExactArtifactSubject>;
        producerJobId: string;
    }>,
    authority: CreationAuthoritySnapshot,
): Promise<{ ok: true } | { ok: false; error: StudioClientResult<StudioV5ReplyEnvelope> }> {
    const manifest = byFormat.get("world-forge.asset_manifest");
    const assetpack = byFormat.get("world-forge.assetpack");
    const releaseAuthority = byFormat.get("world-forge.asset_release_authority");
    if (manifest === undefined || assetpack === undefined || releaseAuthority === undefined) {
        return {
            ok: false,
            error: failure(
                "invalid_request",
                "Creation authority retained v11 release authority lineage is incomplete",
            ),
        };
    }
    const releaseProducerIds = new Set([
        manifest.producerJobId,
        assetpack.producerJobId,
        releaseAuthority.producerJobId,
    ]);
    if (releaseProducerIds.size !== 1) {
        return {
            ok: false,
            error: failure(
                "invalid_request",
                "Creation authority retained v11 release authority lineage has ambiguous producers",
            ),
        };
    }
    const jobResult = await requestCreationAuthorityNamed(
        service,
        "creation_job.get",
        { job_id: manifest.producerJobId },
    );
    if (!jobResult.ok || jobResult.value.kind === "error") {
        return {
            ok: false,
            error: failure(
                "invalid_request",
                "Creation authority retained v11 release authority job is unavailable",
            ),
        };
    }
    const job = isRecord(jobResult.value.result) ? jobResult.value.result.job : null;
    if (!isRecord(job)) {
        return {
            ok: false,
            error: failure("invalid_request", "Creation authority retained v11 release authority job is invalid"),
        };
    }
    const params = isRecord(job.operation_params) ? job.operation_params : null;
    const result = isRecord(job.result) ? job.result : null;
    const jobAuthority = isRecord(job.authority) ? job.authority : null;
    const resultOutputArtifacts = Array.isArray(result?.output_artifact_ids)
        ? result.output_artifact_ids
        : [];
    const resultReasonCodes = Array.isArray(result?.reason_codes)
        ? result.reason_codes
        : null;
    const paramBlockers = Array.isArray(params?.blockers) ? params.blockers : null;
    if (
        job.format !== "world-forge.studio_creation_job" ||
        job.format_version !== 11 ||
        job.operation !== "asset.release.authorize" ||
        job.state !== "succeeded" ||
        job.error !== null ||
        job.workspace_id !== authority.workspaceId ||
        jobAuthority?.root_generation !== authority.rootGeneration ||
        jobAuthority?.source_revision !== authority.sourceRevision ||
        jobAuthority?.workflow_status_hash !== authority.workflowStatusHash ||
        jobAuthority?.artifact_snapshot_hash !== authority.artifactSnapshotHash ||
        params === null ||
        params.manifest_id !== manifest.subject.id ||
        params.assetpack_id !== assetpack.subject.id ||
        params.release_authority_id !== releaseAuthority.subject.id ||
        paramBlockers === null ||
        paramBlockers.length !== 0 ||
        result === null ||
        result.release_status !== "authorized" ||
        result.analysis_status !== "passed" ||
        result.cleanup_pending !== false ||
        resultReasonCodes === null ||
        resultReasonCodes.length !== 0 ||
        resultOutputArtifacts.length !== 3 ||
        resultOutputArtifacts[0] !== manifest.artifactId ||
        resultOutputArtifacts[1] !== assetpack.artifactId ||
        resultOutputArtifacts[2] !== releaseAuthority.artifactId ||
        !matchesReleaseManifestResult(result.asset_manifest, manifest.subject) ||
        !matchesReleaseAssetpackResult(result.assetpack, assetpack.subject) ||
        !matchesReleaseAuthorityResult(
            result.asset_release_authority,
            releaseAuthority.subject,
        )
    ) {
        return {
            ok: false,
            error: failure(
                "invalid_request",
                "Creation authority retained v11 asset.release.authorize proof is not exact or authorized",
            ),
        };
    }
    return { ok: true };
}

function extractCriteria(projection: { facts: unknown[] }): string[] {
    let criteria: string[] | null = null;
    for (const fact of projection.facts) {
        if (!isRecord(fact) || fact.key !== "criterion_hashes") continue;
        if (
            Array.isArray(fact.value) &&
            fact.value.every((value) => typeof value === "string")
        ) {
            if (criteria !== null) {
                throw new TypeError("Creation review criteria are duplicated");
            }
            criteria = fact.value.map((value) =>
                validateSha256(value, "creation review criterion hash"),
            );
        } else {
            throw new TypeError("Creation review criteria are malformed");
        }
    }
    if (criteria === null || criteria.length < 1) {
        throw new TypeError("Creation review criteria are required");
    }
    if (new Set(criteria).size !== criteria.length) {
        throw new TypeError("Creation review criteria are duplicated");
    }
    return criteria;
}

function requireAnyExactArtifactSubject(
    artifact: Record<string, unknown>,
): ReturnType<typeof requireExactArtifactSubject> {
    const subject = artifact.subject;
    if (
        !isRecord(subject) ||
        typeof subject.format !== "string" ||
        subject.format_version !== 1
    ) {
        throw new TypeError("Creation authority artifact subject is invalid");
    }
    return requireExactArtifactSubject(artifact, subject.format, 1);
}

function requireFutureCandidateProducerJobId(
    artifact: Record<string, unknown>,
    message: string,
): string {
    const producer = artifact.producer;
    if (
        !isRecord(producer) ||
        producer.kind !== "future_candidate" ||
        producer.phase_id !== null ||
        typeof producer.reference_id !== "string"
    ) {
        throw new TypeError(message);
    }
    return validateEntityId(producer.reference_id, "creation authority producer job");
}

function matchesReleaseManifestResult(
    value: unknown,
    subject: ReturnType<typeof requireExactArtifactSubject>,
): boolean {
    return (
        isRecord(value) &&
        value.manifest_id === subject.id &&
        value.content_hash === subject.contentHash
    );
}

function matchesReleaseAssetpackResult(
    value: unknown,
    subject: ReturnType<typeof requireExactArtifactSubject>,
): boolean {
    return (
        isRecord(value) &&
        value.assetpack_id === subject.id &&
        value.content_hash === subject.contentHash
    );
}

function matchesReleaseAuthorityResult(
    value: unknown,
    subject: ReturnType<typeof requireExactArtifactSubject>,
): boolean {
    return (
        isRecord(value) &&
        value.format === "world-forge.asset_release_authority" &&
        value.format_version === 1 &&
        value.release_authority_id === subject.id &&
        value.content_hash === subject.contentHash
    );
}

function extractArtifactId(artifact: Record<string, unknown>): string {
    return validateEntityId(artifact.artifact_id, "creation authority artifact");
}

function requireExactArtifactSubject(
    artifact: Record<string, unknown>,
    format: string,
    formatVersion: number,
): {
    format: string;
    formatVersion: number;
    id: string;
    contentHash: string;
} {
    const subject = artifact.subject;
    if (
        !isRecord(subject) ||
        subject.format !== format ||
        subject.format_version !== formatVersion
    ) {
        throw new TypeError(`Creation authority ${format} subject is invalid`);
    }
    return {
        format,
        formatVersion,
        id: validateEntityId(subject.id, "creation authority artifact subject"),
        contentHash: validateSha256(
            subject.content_hash,
            "creation authority artifact subject content hash",
        ),
    };
}

function requireClosedReviewStatus(
    status: string | null,
): "approved" | "rejected" {
    if (status !== "approved" && status !== "rejected") {
        throw new TypeError("Creation authority review status is invalid");
    }
    return status;
}

function validateAuthorityPreviewMediaType(
    value: unknown,
): "image/png" | "audio/wav" | "text/plain" {
    if (
        value !== "image/png" &&
        value !== "audio/wav" &&
        value !== "text/plain"
    ) {
        throw new TypeError("Creation authority preview media type is invalid");
    }
    return value;
}

function validateRuntimeHeadlessPlatformId(
    value: string,
): "platform:linux_x86_64" | "platform:windows_x86_64" {
    if (
        value !== "platform:linux_x86_64" &&
        value !== "platform:windows_x86_64"
    ) {
        throw new TypeError("Creation headless platform is invalid");
    }
    return value;
}

function sameAuthority(
    left: CreationAuthoritySnapshot,
    right: CreationAuthoritySnapshot,
): boolean {
    return (
        left.workspaceId === right.workspaceId &&
        left.rootGeneration === right.rootGeneration &&
        left.sourceRevision === right.sourceRevision &&
        left.workflowStatusHash === right.workflowStatusHash &&
        left.artifactSnapshotHash === right.artifactSnapshotHash
    );
}

function stableMainEntityId(prefix: string, parts: readonly string[]): string {
    return `${prefix}_${createHash("sha256")
        .update(JSON.stringify(parts))
        .digest("hex")
        .slice(0, 48)}`;
}

async function requestMainOwnedAuthorityJobAction(
    service: ForgeServiceClient,
    method: "creation_job.cancel" | "creation_job.recover",
    argument: CreationAuthorityJobActionArgument,
): Promise<StudioClientResult<StudioV5ReplyEnvelope>> {
    const current = await requestCreationAuthorityNamed(
        service,
        "creation_job.get",
        { job_id: argument.jobId },
    );
    if (!current.ok || current.value.kind === "error") {
        return current;
    }
    const result = current.value.result as { job?: unknown };
    const job = result.job;
    if (!isRecord(job) || job.workspace_id !== argument.workspaceId) {
        return failure(
            "invalid_request",
            "Creation authority job no longer belongs to the selected workspace",
        );
    }
    if (
        typeof job.operation !== "string" ||
        !AUTHORITY_JOB_OPERATIONS.has(job.operation as never)
    ) {
        return failure(
            "invalid_request",
            "Creation authority job action is limited to authority jobs",
        );
    }
    if (
        typeof job.state !== "string" ||
        !CREATION_JOB_STATES.has(job.state)
    ) {
        return failure(
            "invalid_request",
            "Creation authority job state is invalid",
        );
    }
    const generation = validateGeneration(
        job.generation,
        "creation authority job generation",
    );
    const recordHash = validateSha256(
        job.record_hash,
        "creation authority job record hash",
    );
    return await requestCreationAuthorityNamed(
        service,
        method,
        method === "creation_job.cancel"
            ? {
                  job_id: argument.jobId,
                  expected_generation: generation,
                  expected_record_hash: recordHash,
              }
            : {
                  job_id: argument.jobId,
                  expected_generation: generation,
                  expected_record_hash: recordHash,
                  mode: "resume",
              },
    );
}

async function requestCreationPreviewNamed(
    service: ForgeServiceClient,
    method:
        | "creation_preview.open"
        | "creation_preview.read"
        | "creation_preview.close",
    params: Record<string, unknown>,
): Promise<StudioClientResult<StudioV4ReplyEnvelope>> {
    const requestId = randomUUID();
    return await capture(() =>
        service
            .request(
                requestId,
                method,
                params,
                ASSET_PREVIEW_REQUEST_TIMEOUT_MS,
                4,
            )
            .then((reply) => reply as StudioV4ReplyEnvelope),
    );
}

async function requestCreationProfileStage(
    service: ForgeServiceClient,
    argument: CreationProfileStageArgument,
): Promise<StudioClientResult<StudioV3ReplyEnvelope>> {
    const loaded = await loadCreationGraph(service, argument);
    if (!loaded.ok) return loaded.reply;
    const { project, manifest, profile } = loaded.value;
    if (
        argument.path !== profile.path ||
        argument.path !==
            requireCreationReference(
                project.document.profile,
                "project profile",
            ).path ||
        argument.path !==
            requireCreationReference(
                manifest.document.profile,
                "manifest profile",
            ).path ||
        argument.expectedBaseFileSha256 !== profile.fileSha256
    ) {
        return failure(
            "invalid_request",
            "Creation profile authority does not match the current project graph",
        );
    }
    if (
        argument.proposedProfile.format !== "world-forge.creation_profile" ||
        argument.proposedProfile.format_version !== 1 ||
        argument.proposedProfile.profile_id !== profile.document.profile_id ||
        argument.proposedProfile.project_id !== profile.document.project_id
    ) {
        return failure(
            "invalid_request",
            "Proposed creation profile identity is unsupported",
        );
    }
    const proposedProfile = withCreationContentHash(argument.proposedProfile);
    const proposedProfileFile = proposedCreationFile(
        profile.path,
        proposedProfile,
    );
    const proposedManifest = withCreationContentHash({
        ...manifest.document,
        profile: withReferenceContentHash(
            requireCreationReference(
                manifest.document.profile,
                "manifest profile",
            ),
            String(proposedProfile.content_hash),
        ),
    });
    const proposedManifestFile = proposedCreationFile(
        manifest.path,
        proposedManifest,
    );
    const proposedProject = withCreationContentHash({
        ...project.document,
        profile: withReferenceContentHash(
            requireCreationReference(
                project.document.profile,
                "project profile",
            ),
            String(proposedProfile.content_hash),
        ),
        source_manifest: withReferenceContentHash(
            requireCreationReference(
                project.document.source_manifest,
                "project source manifest",
            ),
            String(proposedManifest.content_hash),
        ),
    });
    const proposedProjectFile = proposedCreationFile(
        project.path,
        proposedProject,
    );
    return await requestCreationNamed(service, "creation_changeset.create", {
        ...creationAuthorityWire(argument),
        operations: [
            replacementCreationOperation(profile, proposedProfileFile),
            replacementCreationOperation(manifest, proposedManifestFile),
            replacementCreationOperation(project, proposedProjectFile),
        ],
    });
}

async function requestCreationModuleStage(
    service: ForgeServiceClient,
    argument: CreationModuleStageArgument,
): Promise<StudioClientResult<StudioV3ReplyEnvelope>> {
    const loaded = await loadCreationGraph(service, argument);
    if (!loaded.ok) return loaded.reply;
    const { project, manifest, documents } = loaded.value;
    const collection = CREATION_MODULE_COLLECTIONS[argument.format];
    const modules = requireCreationModules(manifest.document);
    const references = requireCreationReferenceArray(
        modules[collection],
        collection,
    );
    const manifestDirectory = path.posix.dirname(manifest.path);
    const referenced = references.find(
        (reference) =>
            path.posix.join(manifestDirectory, String(reference.path)) ===
            argument.path,
    );
    const existingSummary =
        documents.find(
            (document) =>
                unicodeKey(document.path) === unicodeKey(argument.path),
        ) ?? null;
    let existing: LoadedCreationFile | null = null;
    if (argument.operation !== "create" && existingSummary) {
        const read = await readCreationGraphFile(
            service,
            argument,
            existingSummary,
        );
        if (!read.ok) return read.reply;
        existing = read.value;
    }
    if (argument.operation === "create") {
        if (referenced || existingSummary || !argument.proposedModule) {
            return failure(
                "invalid_request",
                "Creation module create target already exists",
            );
        }
    } else if (
        !referenced ||
        !existing ||
        existing.format !== argument.format ||
        existing.fileSha256 !== argument.expectedBaseFileSha256
    ) {
        return failure(
            "invalid_request",
            "Creation module authority does not match the project graph",
        );
    }

    let moduleOperation: Record<string, unknown>;
    let nextReferences: Record<string, unknown>[];
    if (argument.operation === "delete") {
        if (!referenced || !existing) {
            return failure(
                "invalid_request",
                "Creation module delete target is absent",
            );
        }
        moduleOperation = deletionCreationOperation(existing);
        nextReferences = references.filter(
            (reference) => reference !== referenced,
        );
    } else {
        const proposed = argument.proposedModule;
        if (
            !proposed ||
            proposed.format !== argument.format ||
            proposed.format_version !== 1
        ) {
            return failure(
                "invalid_request",
                "Proposed creation module format is unsupported",
            );
        }
        const moduleId = validateEntityId(
            proposed.module_id,
            "creation module",
        );
        if (proposed.project_id !== project.document.project_id) {
            return failure(
                "invalid_request",
                "Proposed creation module project identity differs",
            );
        }
        if (referenced && referenced.id !== moduleId) {
            return failure(
                "invalid_request",
                "Proposed creation module identity differs",
            );
        }
        const collision = allCreationModuleReferences(modules).find(
            (reference) =>
                reference !== referenced &&
                (unicodeKey(reference.id) === unicodeKey(moduleId) ||
                    unicodeKey(
                        path.posix.join(
                            manifestDirectory,
                            String(reference.path),
                        ),
                    ) === unicodeKey(argument.path)),
        );
        if (collision) {
            return failure(
                "invalid_request",
                "Creation module identity or path collides",
            );
        }
        const proposedDocument = withCreationContentHash(proposed);
        const proposedFile = proposedCreationFile(
            argument.path,
            proposedDocument,
        );
        moduleOperation =
            argument.operation === "create"
                ? creationOperation(proposedFile)
                : replacementCreationOperation(
                      existing as LoadedCreationFile,
                      proposedFile,
                  );
        const relativePath = path.posix.relative(
            manifestDirectory,
            argument.path,
        );
        if (!isPortableRelativePath(relativePath)) {
            return failure(
                "invalid_request",
                "Creation module path is outside the source manifest",
            );
        }
        const nextReference = {
            format: argument.format,
            format_version: 1,
            id: moduleId,
            path: relativePath,
            content_hash: proposedDocument.content_hash,
        };
        nextReferences = [
            ...references.filter((reference) => reference !== referenced),
            nextReference,
        ].sort((left, right) => compareUtf8(String(left.id), String(right.id)));
    }

    const proposedManifest = withCreationContentHash({
        ...manifest.document,
        modules: {
            ...modules,
            [collection]: nextReferences,
        },
    });
    const proposedManifestFile = proposedCreationFile(
        manifest.path,
        proposedManifest,
    );
    const proposedProject = withCreationContentHash({
        ...project.document,
        source_manifest: withReferenceContentHash(
            requireCreationReference(
                project.document.source_manifest,
                "project source manifest",
            ),
            String(proposedManifest.content_hash),
        ),
    });
    const proposedProjectFile = proposedCreationFile(
        project.path,
        proposedProject,
    );
    return await requestCreationNamed(service, "creation_changeset.create", {
        ...creationAuthorityWire(argument),
        operations: [
            moduleOperation,
            replacementCreationOperation(manifest, proposedManifestFile),
            replacementCreationOperation(project, proposedProjectFile),
        ],
    });
}

type CreationAuthorityActionMethod =
    | "creation_workflow.reconcile"
    | "creation_phase.read"
    | "creation_phase.validate"
    | "creation_phase.complete"
    | "creation_phase.reopen";

async function requestCreationAuthorityAction(
    service: ForgeServiceClient,
    method: CreationAuthorityActionMethod,
    argument:
        | CreationWorkflowReconcileArgument
        | CreationPhaseReadArgument
        | CreationPhaseReportArgument
        | CreationPhaseReopenArgument,
): Promise<StudioClientResult<StudioV3ReplyEnvelope>> {
    const params: Record<string, unknown> = creationAuthorityWire(argument);
    if ("artifactRegistry" in argument)
        params.artifact_registry = argument.artifactRegistry;
    if ("report" in argument) params.report = argument.report;
    if ("phaseId" in argument) params.phase_id = argument.phaseId;
    if ("reason" in argument) {
        params.reason = argument.reason;
        params.approved_by = argument.approvedBy;
    }
    return await requestCreationNamed(service, method, params);
}

interface CreationDocumentSummary {
    path: string;
    format: string;
    formatVersion: number;
    id: string;
    contentHash: string;
    fileSha256: string;
}

interface LoadedCreationFile extends CreationDocumentSummary {
    document: Record<string, unknown>;
    bytes: Buffer;
}

interface LoadedCreationGraph {
    project: LoadedCreationFile;
    manifest: LoadedCreationFile;
    profile: LoadedCreationFile;
    documents: CreationDocumentSummary[];
}

type CreationLoadResult<T> =
    | { ok: true; value: T }
    | { ok: false; reply: StudioClientResult<StudioV3ReplyEnvelope> };

async function loadCreationGraph(
    service: ForgeServiceClient,
    authority: CreationAuthorityArgument,
): Promise<CreationLoadResult<LoadedCreationGraph>> {
    const listed = await requestCreationSuccess(
        service,
        "creation_document.list",
        {
            workspace_id: authority.workspaceId,
            expected_source_revision: authority.expectedSourceRevision,
        },
    );
    if (!listed.ok) return listed;
    const documents = requireCreationDocumentList(
        listed.value,
        authority.expectedSourceRevision,
    );
    const projectSummaries = documents.filter(
        (document) =>
            document.format === "world-forge.project" &&
            document.formatVersion === 1,
    );
    if (projectSummaries.length !== 1) {
        throw new StudioProtocolError(
            "Forge Studio returned an ambiguous creation project graph",
        );
    }
    const projectResult = await readCreationGraphFile(
        service,
        authority,
        projectSummaries[0],
    );
    if (!projectResult.ok) return projectResult;
    const project = projectResult.value;
    const manifestReference = requireCreationReference(
        project.document.source_manifest,
        "project source manifest",
    );
    const profileReference = requireCreationReference(
        project.document.profile,
        "project profile",
    );
    const manifestSummary = requireCreationSummary(
        documents,
        manifestReference,
    );
    const profileSummary = requireCreationSummary(documents, profileReference);
    const manifestResult = await readCreationGraphFile(
        service,
        authority,
        manifestSummary,
    );
    if (!manifestResult.ok) return manifestResult;
    const profileResult = await readCreationGraphFile(
        service,
        authority,
        profileSummary,
    );
    if (!profileResult.ok) return profileResult;
    const manifest = manifestResult.value;
    const profile = profileResult.value;
    const manifestProfile = requireCreationReference(
        manifest.document.profile,
        "manifest profile",
    );
    if (
        manifest.format !== "world-forge.creation_source_manifest" ||
        profile.format !== "world-forge.creation_profile" ||
        manifestProfile.path !== profile.path ||
        manifestProfile.id !== profile.id ||
        manifestProfile.content_hash !== profile.contentHash
    ) {
        throw new StudioProtocolError(
            "Forge Studio returned an incoherent creation project graph",
        );
    }
    return { ok: true, value: { project, manifest, profile, documents } };
}

async function requestCreationSuccess(
    service: ForgeServiceClient,
    method: "creation_document.list" | "creation_document.read",
    params: Record<string, unknown>,
): Promise<CreationLoadResult<unknown>> {
    const reply = await requestCreationNamed(service, method, params);
    if (!reply.ok || reply.value.kind === "error") return { ok: false, reply };
    if (reply.value.method !== method) {
        throw new StudioProtocolError(
            "Forge Studio returned a mismatched creation reply",
        );
    }
    return { ok: true, value: reply.value.result };
}

async function readCreationGraphFile(
    service: ForgeServiceClient,
    authority: CreationAuthorityArgument,
    summary: CreationDocumentSummary,
): Promise<CreationLoadResult<LoadedCreationFile>> {
    const read = await requestCreationSuccess(
        service,
        "creation_document.read",
        {
            workspace_id: authority.workspaceId,
            expected_source_revision: authority.expectedSourceRevision,
            path: summary.path,
        },
    );
    if (!read.ok) return read;
    if (
        !isRecord(read.value) ||
        read.value.source_revision !== authority.expectedSourceRevision
    ) {
        throw new StudioProtocolError(
            "Forge Studio returned stale creation document authority",
        );
    }
    const wire = read.value.document;
    if (!isRecord(wire) || !isRecord(wire.document)) {
        throw new StudioProtocolError(
            "Forge Studio returned an invalid creation document",
        );
    }
    const document = normalizeJsonObject(wire.document, "creation document");
    const bytes = canonicalCreationDocumentBytes(document);
    const fileSha256 = createHash("sha256").update(bytes).digest("hex");
    const sealed = withCreationContentHash(document);
    if (
        wire.path !== summary.path ||
        wire.format !== summary.format ||
        wire.format_version !== summary.formatVersion ||
        wire.id !== summary.id ||
        wire.content_hash !== summary.contentHash ||
        wire.file_sha256 !== summary.fileSha256 ||
        document.format !== summary.format ||
        document.format_version !== summary.formatVersion ||
        creationDocumentId(document) !== summary.id ||
        document.content_hash !== summary.contentHash ||
        sealed.content_hash !== summary.contentHash ||
        fileSha256 !== summary.fileSha256
    ) {
        throw new StudioProtocolError(
            "Forge Studio returned mismatched creation document evidence",
        );
    }
    return { ok: true, value: { ...summary, document, bytes } };
}

function requireCreationDocumentList(
    value: unknown,
    sourceRevision: string,
): CreationDocumentSummary[] {
    if (
        !isRecord(value) ||
        value.source_revision !== sourceRevision ||
        !Array.isArray(value.documents)
    ) {
        throw new StudioProtocolError(
            "Forge Studio returned an invalid creation document list",
        );
    }
    const result = value.documents.map((item) => {
        if (
            !isRecord(item) ||
            typeof item.path !== "string" ||
            !isPortableRelativePath(item.path)
        ) {
            throw new StudioProtocolError(
                "Forge Studio returned an invalid creation document summary",
            );
        }
        return {
            path: item.path,
            format: String(item.format),
            formatVersion: Number(item.format_version),
            id: validateEntityId(item.id, "creation document"),
            contentHash: validateSha256(
                item.content_hash,
                "creation document content hash",
            ),
            fileSha256: validateSha256(
                item.file_sha256,
                "creation document file hash",
            ),
        };
    });
    const paths = new Set<string>();
    for (const summary of result) {
        const key = unicodeKey(summary.path);
        if (paths.has(key)) {
            throw new StudioProtocolError(
                "Forge Studio returned colliding creation document paths",
            );
        }
        paths.add(key);
    }
    return result;
}

function requireCreationSummary(
    documents: CreationDocumentSummary[],
    reference: Record<string, unknown>,
): CreationDocumentSummary {
    const match = documents.find(
        (document) => document.path === reference.path,
    );
    if (
        !match ||
        match.format !== reference.format ||
        match.formatVersion !== reference.format_version ||
        match.id !== reference.id ||
        match.contentHash !== reference.content_hash
    ) {
        throw new StudioProtocolError(
            "Forge Studio returned an unresolved creation reference",
        );
    }
    return match;
}

function requireCreationReference(
    value: unknown,
    context: string,
): Record<string, unknown> {
    if (
        !isRecord(value) ||
        typeof value.path !== "string" ||
        !isPortableRelativePath(value.path) ||
        typeof value.format !== "string" ||
        value.format_version !== 1
    ) {
        throw new StudioProtocolError(
            `Forge Studio returned an invalid ${context} reference`,
        );
    }
    validateEntityId(value.id, context);
    validateSha256(value.content_hash, `${context} content hash`);
    return value;
}

function requireCreationModules(
    document: Record<string, unknown>,
): Record<string, unknown> {
    if (!isRecord(document.modules)) {
        throw new StudioProtocolError(
            "Forge Studio returned an invalid creation module manifest",
        );
    }
    for (const collection of Object.values(CREATION_MODULE_COLLECTIONS)) {
        if (!Array.isArray(document.modules[collection])) {
            throw new StudioProtocolError(
                "Forge Studio returned an incomplete creation module manifest",
            );
        }
    }
    return document.modules;
}

function requireCreationReferenceArray(
    value: unknown,
    context: string,
): Record<string, unknown>[] {
    if (!Array.isArray(value)) {
        throw new StudioProtocolError(
            `Forge Studio returned an invalid ${context} collection`,
        );
    }
    return value.map((reference) =>
        requireCreationReference(reference, context),
    );
}

function allCreationModuleReferences(
    modules: Record<string, unknown>,
): Record<string, unknown>[] {
    return Object.values(CREATION_MODULE_COLLECTIONS).flatMap((collection) =>
        requireCreationReferenceArray(modules[collection], collection),
    );
}

function creationAuthorityWire(
    argument: CreationAuthorityArgument,
): Record<string, unknown> {
    return {
        workspace_id: argument.workspaceId,
        expected_root_generation: argument.expectedRootGeneration,
        expected_source_revision: argument.expectedSourceRevision,
        expected_workflow_status_hash: argument.expectedWorkflowStatusHash,
    };
}

function proposedCreationFile(
    path: string,
    document: Record<string, unknown>,
): LoadedCreationFile {
    const bytes = canonicalCreationDocumentBytes(document);
    if (bytes.byteLength > MAX_CREATION_PROFILE_BYTES) {
        throw new TypeError(
            "Studio proposed creation document exceeds the size limit",
        );
    }
    return {
        path,
        format: String(document.format),
        formatVersion: Number(document.format_version),
        id: creationDocumentId(document),
        contentHash: validateSha256(
            document.content_hash,
            "creation document content hash",
        ),
        fileSha256: createHash("sha256").update(bytes).digest("hex"),
        document,
        bytes,
    };
}

function creationDocumentId(document: Record<string, unknown>): string {
    const key =
        document.format === "world-forge.creation_profile"
            ? "profile_id"
            : document.format === "world-forge.project" ||
                document.format === "world-forge.creation_source_manifest"
              ? "project_id"
              : "module_id";
    return validateEntityId(document[key], "creation document");
}

function replacementCreationOperation(
    base: LoadedCreationFile,
    proposed: LoadedCreationFile,
): Record<string, unknown> {
    return {
        operation: "replace",
        path: base.path,
        expected_base_file_sha256: base.fileSha256,
        expected_base_size: base.bytes.byteLength,
        proposed_file_sha256: proposed.fileSha256,
        proposed_size: proposed.bytes.byteLength,
        document: proposed.document,
    };
}

function creationOperation(
    proposed: LoadedCreationFile,
): Record<string, unknown> {
    return {
        operation: "create",
        path: proposed.path,
        expected_base_file_sha256: null,
        expected_base_size: null,
        proposed_file_sha256: proposed.fileSha256,
        proposed_size: proposed.bytes.byteLength,
        document: proposed.document,
    };
}

function deletionCreationOperation(
    base: LoadedCreationFile,
): Record<string, unknown> {
    return {
        operation: "delete",
        path: base.path,
        expected_base_file_sha256: base.fileSha256,
        expected_base_size: base.bytes.byteLength,
        proposed_file_sha256: null,
        proposed_size: null,
    };
}

function withReferenceContentHash(
    reference: Record<string, unknown>,
    contentHash: string,
): Record<string, unknown> {
    return {
        ...reference,
        content_hash: validateSha256(contentHash, "creation reference hash"),
    };
}

function unicodeKey(value: unknown): string {
    return String(value).normalize("NFC").toLowerCase();
}

function compareUtf8(left: string, right: string): number {
    return Buffer.compare(
        Buffer.from(left, "utf8"),
        Buffer.from(right, "utf8"),
    );
}

function withCreationContentHash(
    profile: Record<string, unknown>,
): Record<string, unknown> {
    const normalized = normalizeJsonObject(
        profile,
        "proposed creation profile",
    );
    const payload = { ...normalized };
    delete payload.content_hash;
    const contentHash = createHash("sha256")
        .update(JSON.stringify(sortJsonValue(payload)), "utf8")
        .digest("hex");
    return { ...normalized, content_hash: contentHash };
}

function canonicalCreationDocumentBytes(value: unknown): Buffer {
    const normalized = normalizeJsonValue(value, "creation profile", 0);
    return Buffer.from(
        `${JSON.stringify(sortJsonValue(normalized), null, 2)}\n`,
        "utf8",
    );
}

function sortJsonValue(value: unknown): unknown {
    if (Array.isArray(value)) return value.map(sortJsonValue);
    if (!isRecord(value)) return value;
    const result: Record<string, unknown> = {};
    for (const key of Object.keys(value).sort())
        result[key] = sortJsonValue(value[key]);
    return result;
}

function normalizeJsonObject(
    value: unknown,
    context: string,
): Record<string, unknown> {
    const normalized = normalizeJsonValue(value, context, 0);
    if (!isRecord(normalized))
        throw new TypeError(`Studio ${context} must be an object`);
    return normalized;
}

function normalizeJsonValue(
    value: unknown,
    context: string,
    depth: number,
): unknown {
    if (depth > 64)
        throw new TypeError(`Studio ${context} exceeds the JSON depth limit`);
    if (value === null || typeof value === "boolean") return value;
    if (typeof value === "string") {
        if (containsInvalidUnicode(value)) {
            throw new TypeError(`Studio ${context} contains invalid Unicode`);
        }
        return value;
    }
    if (typeof value === "number") {
        if (!Number.isFinite(value))
            throw new TypeError(`Studio ${context} numbers must be finite`);
        return value;
    }
    if (Array.isArray(value)) {
        return value.map((item) =>
            normalizeJsonValue(item, context, depth + 1),
        );
    }
    if (!isRecord(value))
        throw new TypeError(`Studio ${context} contains unsupported JSON`);
    const result: Record<string, unknown> = {};
    for (const key of Object.keys(value)) {
        if (
            containsInvalidUnicode(key) ||
            key === "__proto__" ||
            key === "constructor" ||
            key === "prototype"
        ) {
            throw new TypeError(
                `Studio ${context} contains an unsafe object key`,
            );
        }
        result[key] = normalizeJsonValue(value[key], context, depth + 1);
    }
    return result;
}

async function requestAssetCatalogList(
    service: ForgeServiceClient,
    argument: AssetCatalogListArgument,
): Promise<StudioClientResult<StudioAssetCatalogListReply>> {
    const requestId = randomUUID();
    const offset = "offset" in argument ? argument.offset : 0;
    const expectedManifestRevision =
        "expectedManifestRevision" in argument
            ? argument.expectedManifestRevision
            : undefined;
    const params =
        expectedManifestRevision === undefined
            ? ({
                  workspace_id: argument.workspaceId,
                  offset: 0,
                  limit: ASSET_CATALOG_PAGE_SIZE,
              } satisfies StudioAssetCatalogListRequest["params"])
            : ({
                  workspace_id: argument.workspaceId,
                  offset,
                  limit: ASSET_CATALOG_PAGE_SIZE,
                  expected_manifest_revision: expectedManifestRevision,
              } satisfies StudioAssetCatalogListRequest["params"]);
    return await capture(() =>
        service
            .request(
                requestId,
                "asset.catalog.list",
                params,
                ASSET_CATALOG_REQUEST_TIMEOUT_MS,
            )
            .then((reply) =>
                validateAssetCatalogListReply(
                    reply,
                    requestId,
                    offset,
                    expectedManifestRevision,
                ),
            ),
    );
}

async function requestAssetCatalogInspect(
    service: ForgeServiceClient,
    workspaceId: string,
    manifestRevision: string,
    entryId: string,
): Promise<StudioClientResult<StudioAssetCatalogInspectReply>> {
    const requestId = randomUUID();
    const params = {
        workspace_id: workspaceId,
        expected_manifest_revision: manifestRevision,
        entry_id: entryId,
    } satisfies StudioAssetCatalogInspectRequest["params"];
    return await capture(() =>
        service
            .request(
                requestId,
                "asset.catalog.inspect",
                params,
                ASSET_CATALOG_REQUEST_TIMEOUT_MS,
            )
            .then((reply) =>
                validateAssetCatalogInspectReply(
                    reply,
                    requestId,
                    manifestRevision,
                    entryId,
                ),
            ),
    );
}

async function requestAssetPreviewOpen(
    service: ForgeServiceClient,
    previews: Map<string, AssetPreviewState>,
    argument: {
        workspaceId: string;
        manifestRevision: string;
        entryId: string;
    },
): Promise<StudioClientResult<StudioAssetPreviewOpenReply>> {
    const requestId = randomUUID();
    const params = {
        workspace_id: argument.workspaceId,
        manifest_revision: argument.manifestRevision,
        entry_id: argument.entryId,
    } satisfies StudioAssetPreviewOpenRequest["params"];
    return await capture(() =>
        service
            .request(
                requestId,
                "asset.preview.open",
                params,
                ASSET_PREVIEW_REQUEST_TIMEOUT_MS,
            )
            .then((reply) => {
                const validated = validateNamedReply(
                    reply,
                    requestId,
                    "asset.preview.open",
                );
                if (validated.kind === "error") return validated;
                if (
                    validated.method !== "asset.preview.open" ||
                    validated.result.manifest_revision !==
                        argument.manifestRevision ||
                    validated.result.entry_id !== argument.entryId ||
                    validated.result.chunk_bytes !==
                        ASSET_PREVIEW_CHUNK_BYTES ||
                    previews.has(validated.result.handle)
                ) {
                    throw new StudioProtocolError(
                        "Forge Studio returned a mismatched asset preview authority",
                    );
                }
                previews.set(validated.result.handle, {
                    manifestRevision: validated.result.manifest_revision,
                    entryId: validated.result.entry_id,
                    mediaType: validated.result.media_type,
                    byteLength: validated.result.byte_length,
                    sha256: validated.result.sha256,
                    chunkBytes: validated.result.chunk_bytes,
                    nextSequence: 0,
                    cumulativeBytes: 0,
                    digest: createHash("sha256"),
                    previous: null,
                    eof: false,
                });
                return validated;
            }),
    );
}

async function requestAssetPreviewRead(
    service: ForgeServiceClient,
    previews: Map<string, AssetPreviewState>,
    handle: string,
    sequence: number,
): Promise<StudioClientResult<StudioAssetPreviewChunkReply>> {
    const state = previews.get(handle);
    const replay = state?.previous?.sequence === sequence;
    if (
        state === undefined ||
        (!replay && (state.eof || sequence !== state.nextSequence))
    ) {
        return failure(
            "invalid_request",
            "Studio asset preview handle or sequence is unavailable",
        );
    }
    const requestId = randomUUID();
    const params = {
        handle,
        sequence,
    } satisfies StudioAssetPreviewReadRequest["params"];
    return await capture(() =>
        service
            .request(
                requestId,
                "asset.preview.read",
                params,
                ASSET_PREVIEW_REQUEST_TIMEOUT_MS,
            )
            .then((reply) =>
                validateAssetPreviewReadReply(
                    reply,
                    requestId,
                    handle,
                    sequence,
                    state,
                ),
            ),
    );
}

async function requestAssetPreviewClose(
    service: ForgeServiceClient,
    previews: Map<string, AssetPreviewState>,
    handle: string,
): Promise<StudioClientResult<StudioAssetPreviewCloseReply>> {
    if (!previews.has(handle)) {
        return failure(
            "invalid_request",
            "Studio asset preview handle is unavailable",
        );
    }
    const requestId = randomUUID();
    const params = {
        handle,
    } satisfies StudioAssetPreviewCloseRequest["params"];
    return await capture(() =>
        service
            .request(
                requestId,
                "asset.preview.close",
                params,
                ASSET_PREVIEW_REQUEST_TIMEOUT_MS,
            )
            .then((reply) => {
                const validated = validateNamedReply(
                    reply,
                    requestId,
                    "asset.preview.close",
                );
                if (validated.kind === "error") return validated;
                if (
                    validated.method !== "asset.preview.close" ||
                    validated.result.handle !== handle ||
                    validated.result.closed !== true
                ) {
                    throw new StudioProtocolError(
                        "Forge Studio returned a mismatched asset preview close",
                    );
                }
                previews.delete(handle);
                return validated;
            }),
    );
}

async function requestJobCreate(
    service: ForgeServiceClient,
    workspaceId: string,
    operation: NamedJobOperation,
    input: Readonly<Record<string, unknown>>,
): Promise<StudioClientResult<StudioReplyEnvelope>> {
    const requestId = randomUUID();
    return await capture(() =>
        service
            .request(
                requestId,
                "job.create",
                { workspace_id: workspaceId, operation, input },
                DEFAULT_REQUEST_TIMEOUT_MS,
            )
            .then((reply) =>
                validateJobCreateReply(
                    reply,
                    requestId,
                    workspaceId,
                    operation,
                    input,
                ),
            ),
    );
}

async function requestStageSourceDocument(
    service: ForgeServiceClient,
    workspaceId: string,
    path: string,
    baseSha256: string,
    content: string,
): Promise<StudioClientResult<StudioReplyEnvelope>> {
    const requestId = randomUUID();
    return await capture(() =>
        service
            .request(
                requestId,
                "changeset.create",
                {
                    workspace_id: workspaceId,
                    operations: [
                        {
                            path,
                            operation: "replace",
                            expected_base_sha256: baseSha256,
                            content,
                        },
                    ],
                },
                DEFAULT_REQUEST_TIMEOUT_MS,
            )
            .then((reply) =>
                validateStageSourceDocumentReply(
                    reply,
                    requestId,
                    workspaceId,
                    path,
                    baseSha256,
                    content,
                ),
            ),
    );
}

async function requestChangesetGet(
    service: ForgeServiceClient,
    changesetId: string,
): Promise<StudioClientResult<StudioReplyEnvelope>> {
    const requestId = randomUUID();
    return await capture(() =>
        service
            .request(
                requestId,
                "changeset.get",
                { changeset_id: changesetId },
                DEFAULT_REQUEST_TIMEOUT_MS,
            )
            .then((reply) =>
                validateChangesetGetReply(reply, requestId, changesetId),
            ),
    );
}

async function requestChangesetDiff(
    service: ForgeServiceClient,
    changesetId: string,
): Promise<StudioClientResult<StudioReplyEnvelope>> {
    const requestId = randomUUID();
    return await capture(() =>
        service
            .request(
                requestId,
                "changeset.diff",
                { changeset_id: changesetId },
                DEFAULT_REQUEST_TIMEOUT_MS,
            )
            .then((reply) =>
                validateChangesetDiffReply(reply, requestId, changesetId),
            ),
    );
}

async function requestChangesetAction(
    service: ForgeServiceClient,
    method: ChangesetActionMethod,
    status: ChangesetActionStatus,
    changesetId: string,
    expectedReviewSha256: string | undefined,
): Promise<StudioClientResult<StudioReplyEnvelope>> {
    const requestId = randomUUID();
    return await capture(() =>
        service
            .request(
                requestId,
                method,
                {
                    changeset_id: changesetId,
                    ...(expectedReviewSha256 === undefined
                        ? {}
                        : { expected_review_sha256: expectedReviewSha256 }),
                },
                DEFAULT_REQUEST_TIMEOUT_MS,
            )
            .then((reply) =>
                validateChangesetActionReply(
                    reply,
                    requestId,
                    method,
                    status,
                    changesetId,
                    expectedReviewSha256,
                ),
            ),
    );
}

function validateNamedReply(
    value: unknown,
    requestId: string,
    method: StudioCapabilityMethod,
): StudioReplyEnvelope {
    if (
        !validateStudioEnvelope(value) ||
        value.protocol_version !== 1 ||
        (value.kind !== "response" && value.kind !== "error") ||
        value.request_id !== requestId ||
        (value.kind === "response" && value.method !== method)
    ) {
        throw new StudioProtocolError(
            `Forge Studio returned an invalid ${method} reply`,
        );
    }
    return value;
}

function validateAssetCatalogListReply(
    value: unknown,
    requestId: string,
    offset: number,
    expectedManifestRevision: string | undefined,
): StudioAssetCatalogListReply {
    const reply = validateNamedReply(value, requestId, "asset.catalog.list");
    if (reply.kind === "error") return reply;
    if (reply.method !== "asset.catalog.list") {
        throw new StudioProtocolError(
            "Forge Studio returned an invalid asset.catalog.list reply",
        );
    }
    const { result } = reply;
    const entryIds = result.entries.map((entry) => entry.entry_id);
    if (
        result.offset !== offset ||
        result.limit !== ASSET_CATALOG_PAGE_SIZE ||
        (expectedManifestRevision !== undefined &&
            result.manifest_revision !== expectedManifestRevision) ||
        new Set(entryIds).size !== entryIds.length ||
        (result.next_offset !== null &&
            (!Number.isSafeInteger(result.next_offset) ||
                result.entries.length !== ASSET_CATALOG_PAGE_SIZE ||
                result.next_offset <= offset ||
                result.next_offset !== offset + result.entries.length))
    ) {
        throw new StudioProtocolError(
            "Forge Studio returned a mismatched asset catalog page",
        );
    }
    return reply;
}

function validateAssetCatalogInspectReply(
    value: unknown,
    requestId: string,
    manifestRevision: string,
    entryId: string,
): StudioAssetCatalogInspectReply {
    const reply = validateNamedReply(value, requestId, "asset.catalog.inspect");
    if (reply.kind === "error") return reply;
    if (
        reply.method !== "asset.catalog.inspect" ||
        reply.result.manifest_revision !== manifestRevision ||
        reply.result.entry.entry_id !== entryId
    ) {
        throw new StudioProtocolError(
            "Forge Studio returned a mismatched asset catalog inspection",
        );
    }
    return reply;
}

function validateAssetPreviewReadReply(
    value: unknown,
    requestId: string,
    handle: string,
    sequence: number,
    state: AssetPreviewState,
): StudioAssetPreviewChunkReply {
    const reply = validateNamedReply(value, requestId, "asset.preview.read");
    if (reply.kind === "error") return reply;
    if (reply.method !== "asset.preview.read") {
        throw new StudioProtocolError(
            "Forge Studio returned an invalid asset preview read",
        );
    }
    const { result } = reply;
    const bytes = decodeCanonicalAssetPreviewBase64(result.data_base64);
    if (
        bytes === null ||
        result.handle !== handle ||
        result.sequence !== sequence ||
        result.byte_length !== bytes.byteLength
    ) {
        throw new StudioProtocolError(
            "Forge Studio returned a mismatched asset preview chunk",
        );
    }

    const previous = state.previous;
    const replay = previous !== null && previous.sequence === sequence;
    if (replay) {
        if (
            previous.byteLength !== result.byte_length ||
            previous.cumulativeBytes !== result.cumulative_bytes ||
            previous.cumulativeSha256 !== result.cumulative_sha256 ||
            previous.eof !== result.eof ||
            !equalBytes(previous.bytes, bytes)
        ) {
            throw new StudioProtocolError(
                "Forge Studio returned a changed asset preview replay",
            );
        }
        return assetPreviewChunkReply(reply, bytes);
    }

    if (state.eof || sequence !== state.nextSequence) {
        throw new StudioProtocolError(
            "Forge Studio returned an unexpected asset preview sequence",
        );
    }
    const expectedCumulative = state.cumulativeBytes + bytes.byteLength;
    const pendingDigest = state.digest.copy();
    pendingDigest.update(bytes);
    const computedSha256 = pendingDigest.copy().digest("hex");
    if (
        result.cumulative_bytes !== expectedCumulative ||
        result.cumulative_sha256 !== computedSha256 ||
        result.cumulative_bytes > state.byteLength ||
        (!result.eof &&
            (result.byte_length !== state.chunkBytes ||
                result.cumulative_bytes >= state.byteLength)) ||
        (result.eof &&
            (result.cumulative_bytes !== state.byteLength ||
                result.cumulative_sha256 !== state.sha256))
    ) {
        throw new StudioProtocolError(
            "Forge Studio returned an inconsistent asset preview stream",
        );
    }

    state.digest = pendingDigest;
    state.cumulativeBytes = result.cumulative_bytes;
    state.nextSequence += 1;
    state.eof = result.eof;
    state.previous = {
        sequence,
        bytes: new Uint8Array(bytes),
        byteLength: result.byte_length,
        cumulativeBytes: result.cumulative_bytes,
        cumulativeSha256: result.cumulative_sha256,
        eof: result.eof,
    };
    return assetPreviewChunkReply(reply, bytes);
}

function assetPreviewChunkReply(
    reply: Extract<StudioReplyEnvelope, { method: "asset.preview.read" }>,
    bytes: Uint8Array,
): StudioAssetPreviewChunkReply {
    return {
        ...reply,
        result: {
            handle: reply.result.handle,
            sequence: reply.result.sequence,
            byte_length: reply.result.byte_length,
            cumulative_bytes: reply.result.cumulative_bytes,
            cumulative_sha256: reply.result.cumulative_sha256,
            eof: reply.result.eof,
            bytes: new Uint8Array(bytes),
        },
    };
}

function equalBytes(left: Uint8Array, right: Uint8Array): boolean {
    if (left.byteLength !== right.byteLength) return false;
    return left.every((value, index) => value === right[index]);
}

function validateJobCreateReply(
    value: unknown,
    requestId: string,
    workspaceId: string,
    operation: NamedJobOperation,
    input: Readonly<Record<string, unknown>>,
): StudioReplyEnvelope {
    const reply = validateNamedReply(value, requestId, "job.create");
    if (reply.kind === "error") {
        return reply;
    }
    if (reply.method !== "job.create") {
        throw new StudioProtocolError(
            "Forge Studio returned an invalid job.create reply",
        );
    }
    const { job } = reply.result;
    if (
        job.format_version !== 2 ||
        job.workspace_id !== workspaceId ||
        job.operation !== operation ||
        !hasExactScalarFields(job.input, input)
    ) {
        throw new StudioProtocolError(
            "Forge Studio returned a mismatched job.create result",
        );
    }
    return reply;
}

function validateStageSourceDocumentReply(
    value: unknown,
    requestId: string,
    workspaceId: string,
    path: string,
    baseSha256: string,
    content: string,
): StudioReplyEnvelope {
    const reply = validateNamedReply(value, requestId, "changeset.create");
    if (reply.kind === "error") return reply;
    if (reply.method !== "changeset.create") {
        throw new StudioProtocolError(
            "Forge Studio returned an invalid changeset.create reply",
        );
    }
    const record = requireChangesetIdentity(reply.result.changeset);
    const operations = record.operations;
    const operation: unknown = Array.isArray(operations)
        ? operations[0]
        : undefined;
    const proposedSha256 = createHash("sha256")
        .update(content, "utf8")
        .digest("hex");
    if (
        record.format_version !== 2 ||
        record.workspace_id !== workspaceId ||
        record.status !== "staged" ||
        !Array.isArray(operations) ||
        operations.length !== 1 ||
        !isRecord(operation) ||
        operation.path !== path ||
        operation.operation !== "replace" ||
        operation.base_sha256 !== baseSha256 ||
        operation.proposed_sha256 !== proposedSha256 ||
        operation.size !== Buffer.byteLength(content, "utf8") ||
        !hasValidV2ReviewIdentity(record)
    ) {
        throw new StudioProtocolError(
            "Forge Studio returned a mismatched staged source document",
        );
    }
    return reply;
}

function validateChangesetGetReply(
    value: unknown,
    requestId: string,
    changesetId: string,
): StudioReplyEnvelope {
    const reply = validateNamedReply(value, requestId, "changeset.get");
    if (reply.kind === "error") return reply;
    if (reply.method !== "changeset.get") {
        throw new StudioProtocolError(
            "Forge Studio returned an invalid changeset.get reply",
        );
    }
    requireChangesetIdentity(reply.result.changeset, changesetId);
    return reply;
}

function validateChangesetDiffReply(
    value: unknown,
    requestId: string,
    changesetId: string,
): StudioReplyEnvelope {
    const reply = validateNamedReply(value, requestId, "changeset.diff");
    if (reply.kind === "error") return reply;
    if (reply.method !== "changeset.diff") {
        throw new StudioProtocolError(
            "Forge Studio returned an invalid changeset.diff reply",
        );
    }
    const diff = reply.result.diff;
    if (diff.changeset_id !== changesetId) {
        throw new StudioProtocolError(
            "Forge Studio returned a mismatched changeset diff",
        );
    }
    if (
        diff.changeset_format_version === 2 &&
        computeReviewSha256(diff.operations) !== diff.review_sha256
    ) {
        throw new StudioProtocolError(
            "Forge Studio returned a mismatched changeset diff review",
        );
    }
    return reply;
}

function validateChangesetActionReply(
    value: unknown,
    requestId: string,
    method: ChangesetActionMethod,
    status: ChangesetActionStatus,
    changesetId: string,
    expectedReviewSha256: string | undefined,
): StudioReplyEnvelope {
    const reply = validateNamedReply(value, requestId, method);
    if (reply.kind === "error") return reply;
    if (reply.method !== method) {
        throw new StudioProtocolError(
            `Forge Studio returned an invalid ${method} reply`,
        );
    }
    const record = requireChangesetIdentity(
        reply.result.changeset,
        changesetId,
    );
    if (record.status !== status) {
        throw new StudioProtocolError(
            `Forge Studio returned a mismatched ${method} status`,
        );
    }
    if (
        (record.format_version === 2 &&
            record.review_sha256 !== expectedReviewSha256) ||
        (record.format_version === 1 && expectedReviewSha256 !== undefined)
    ) {
        throw new StudioProtocolError(
            `Forge Studio returned a mismatched ${method} review identity`,
        );
    }
    return reply;
}

function requireChangesetIdentity(
    value: unknown,
    expectedChangesetId?: string,
): Record<string, unknown> {
    if (
        !isRecord(value) ||
        typeof value.changeset_id !== "string" ||
        !CHANGESET_ID_PATTERN.test(value.changeset_id) ||
        (expectedChangesetId !== undefined &&
            value.changeset_id !== expectedChangesetId) ||
        (value.format_version === 2 && !hasValidV2ReviewIdentity(value))
    ) {
        throw new StudioProtocolError(
            "Forge Studio returned a mismatched changeset identity",
        );
    }
    return value;
}

function hasValidV2ReviewIdentity(value: Record<string, unknown>): boolean {
    return (
        value.format_version === 2 &&
        typeof value.review_sha256 === "string" &&
        Array.isArray(value.operations) &&
        computeReviewSha256(value.operations) === value.review_sha256
    );
}

function computeReviewSha256(operations: readonly unknown[]): string | null {
    const projected: Record<string, unknown>[] = [];
    for (const value of operations) {
        if (!isRecord(value)) return null;
        const projection = {
            base_sha256: value.base_sha256,
            base_size: value.base_size,
            operation: value.operation,
            path: value.path,
            proposed_sha256: value.proposed_sha256,
            size: value.size,
        };
        if (Object.values(projection).some((item) => item === undefined))
            return null;
        projected.push(projection);
    }
    const canonical = JSON.stringify({
        format: "rpg-world-forge.studio_changeset_review",
        format_version: 1,
        operations: projected,
    });
    return createHash("sha256").update(canonical, "utf8").digest("hex");
}

async function captureValidated<T, U>(
    validate: () => T,
    operation: (value: T) => Promise<StudioClientResult<U>>,
): Promise<StudioClientResult<U>> {
    let value: T;
    try {
        value = validate();
    } catch (error) {
        return failure("invalid_request", describeUnknown(error));
    }
    return await operation(value);
}

function rejectUntrustedOrUnexpectedArguments(
    isTrusted: boolean,
    args: readonly unknown[],
): StudioClientResult<never> | null {
    if (!isTrusted) {
        return failure(
            "invalid_request",
            "Rejected Studio IPC from an untrusted sender",
        );
    }
    if (args.length !== 0) {
        return failure(
            "invalid_request",
            "Studio operation does not accept arguments",
        );
    }
    return null;
}

function untrustedFailure(): StudioClientResult<never> {
    return failure(
        "invalid_request",
        "Rejected Studio IPC from an untrusted sender",
    );
}

function validateSingleArgument<T>(
    args: readonly unknown[],
    validate: (value: unknown) => T,
): T {
    if (args.length !== 1) {
        throw new TypeError(
            "Studio operation requires exactly one argument object",
        );
    }
    return validate(args[0]);
}

function validateWorkspaceJobArgument(
    value: unknown,
    inputFields: readonly string[],
): { workspaceId: string; input: Record<string, unknown> } {
    const params = validateClosedParams(value, ["workspaceId", "input"]);
    return {
        workspaceId: validateWorkspaceId(params.workspaceId),
        input: validateClosedParams(params.input, inputFields),
    };
}

function validateJobPath(value: unknown, field: string): string {
    if (typeof value !== "string" || !isPortableRelativePath(value)) {
        throw new TypeError(`Studio ${field} path is invalid`);
    }
    return value;
}

function validateClosedParams(
    value: unknown,
    allowed: readonly string[],
): Record<string, unknown> {
    if (
        !isRecord(value) ||
        !Object.keys(value).every((key) => allowed.includes(key))
    ) {
        throw new TypeError("Studio IPC arguments must be a closed object");
    }
    return value;
}

function validateWorkspaceId(value: unknown): string {
    if (typeof value !== "string" || !WORKSPACE_ID_PATTERN.test(value)) {
        throw new TypeError("Studio workspace ID is invalid");
    }
    return value;
}

function validateChangesetId(value: unknown): string {
    if (typeof value !== "string" || !CHANGESET_ID_PATTERN.test(value)) {
        throw new TypeError("Studio changeset ID is invalid");
    }
    return value;
}

function validateEntityId(value: unknown, context: string): string {
    if (typeof value !== "string" || !JOB_ID_PATTERN.test(value)) {
        throw new TypeError(`Studio ${context} ID is invalid`);
    }
    return value;
}

function validatePlatformId(value: unknown): string {
    if (
        typeof value !== "string" ||
        !/^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/u.test(value)
    ) {
        throw new TypeError("Studio creation authority platform ID is invalid");
    }
    return value;
}

function validateSha256(value: unknown, context: string): string {
    if (typeof value !== "string" || !SHA256_PATTERN.test(value)) {
        throw new TypeError(`Studio ${context} is invalid`);
    }
    return value;
}

function validateNullableSha256(
    value: unknown,
    context: string,
): string | null {
    return value === null ? null : validateSha256(value, context);
}

function validateGeneration(value: unknown, context: string): number {
    if (!Number.isSafeInteger(value) || (value as number) < 0) {
        throw new TypeError(`Studio ${context} is invalid`);
    }
    return value as number;
}

function validateBoundedText(
    value: unknown,
    context: string,
    maxBytes: number,
): string {
    if (
        typeof value !== "string" ||
        value.trim().length === 0 ||
        Buffer.byteLength(value, "utf8") > maxBytes ||
        containsInvalidUnicode(value)
    ) {
        throw new TypeError(`Studio ${context} is invalid`);
    }
    return value.trim();
}

function validateCreationScaffoldText(
    value: unknown,
    context: string,
    maxCharacters: number,
): string {
    if (typeof value !== "string" || containsInvalidUnicode(value)) {
        throw new TypeError(`Studio ${context} is invalid`);
    }
    const normalized = value.trim();
    if (
        normalized.length === 0 ||
        Array.from(normalized).length > maxCharacters
    ) {
        throw new TypeError(`Studio ${context} is invalid`);
    }
    return normalized;
}

function validateAssetPreviewHandle(value: unknown): string {
    if (
        typeof value !== "string" ||
        !ASSET_PREVIEW_HANDLE_PATTERN.test(value)
    ) {
        throw new TypeError("Studio asset preview handle is invalid");
    }
    return value;
}

function containsInvalidUnicode(value: string): boolean {
    for (let index = 0; index < value.length; index += 1) {
        const code = value.charCodeAt(index);
        if (code >= 0xd800 && code <= 0xdbff) {
            const following = value.charCodeAt(index + 1);
            if (
                index + 1 >= value.length ||
                following < 0xdc00 ||
                following > 0xdfff
            )
                return true;
            index += 1;
        } else if (code >= 0xdc00 && code <= 0xdfff) {
            return true;
        }
    }
    return false;
}

function validateLimit(value: unknown): number {
    if (
        !Number.isSafeInteger(value) ||
        (value as number) < 1 ||
        (value as number) > 1_000
    ) {
        throw new TypeError(
            "Studio list limit must be an integer from 1 to 1000",
        );
    }
    return value as number;
}

async function capture<T>(
    operation: () => Promise<T>,
): Promise<StudioClientResult<T>> {
    try {
        return success(await operation());
    } catch (error) {
        return { ok: false, error: classifyError(error) };
    }
}

function classifyError(error: unknown): StudioClientError {
    if (error instanceof StudioRequestTimeoutError) {
        return { code: "timeout", message: error.message };
    }
    if (error instanceof StudioRequestCancelledError) {
        return { code: "cancelled", message: error.message };
    }
    if (error instanceof StudioTransportError) {
        return { code: "service_unavailable", message: error.message };
    }
    if (error instanceof CodexTransportError) {
        return { code: "service_unavailable", message: error.message };
    }
    return { code: "internal_error", message: describeUnknown(error) };
}

function success<T>(value: T): StudioClientResult<T> {
    return { ok: true, value };
}

function failure(
    code: StudioClientError["code"],
    message: string,
): StudioClientResult<never> {
    return { ok: false, error: { code, message } };
}

function describeUnknown(error: unknown): string {
    return error instanceof Error ? error.message : "Unknown Studio error";
}

function hasExactScalarFields(
    value: unknown,
    expected: Readonly<Record<string, unknown>>,
): boolean {
    if (!isRecord(value)) {
        return false;
    }
    const expectedKeys = Object.keys(expected).sort();
    const actualKeys = Object.keys(value).sort();
    return (
        expectedKeys.length === actualKeys.length &&
        expectedKeys.every(
            (key, index) =>
                key === actualKeys[index] && value[key] === expected[key],
        )
    );
}

function isRecord(value: unknown): value is Record<string, unknown> {
    if (typeof value !== "object" || value === null || Array.isArray(value)) {
        return false;
    }
    const prototype = Object.getPrototypeOf(value) as unknown;
    return prototype === Object.prototype || prototype === null;
}

export type StudioIpcResult = StudioClientResult<
    StudioReplyEnvelope | ForgeServiceStatus | StudioActivityEvent
>;

import type {
    AssetCatalogInspectResponse as StudioAssetCatalogInspectResponse,
    AssetCatalogListResponse as StudioAssetCatalogListResponse,
    AssetCatalogEntry as StudioAssetCatalogEntry,
    AssetInspection as StudioAssetInspection,
    AssetPreviewCloseResponse as StudioAssetPreviewCloseResponse,
    AssetPreviewOpenResponse as StudioAssetPreviewOpenResponse,
    AssetPreviewReadResponse as StudioWireAssetPreviewReadResponse,
    AssetReceiptValidateOperation as StudioAssetReceiptValidateOperation,
    AssetReceiptValidateInput as StudioAssetReceiptValidateInput,
    AssetpackVerifyOperation as StudioAssetpackVerifyOperation,
    AssetpackVerifyInput as StudioAssetpackVerifyInput,
    ChangesetApplyResponse as StudioChangesetApplyResponse,
    ChangesetApproveResponse as StudioChangesetApproveResponse,
    ChangesetCreateResponse as StudioChangesetCreateResponse,
    ChangesetDiff as StudioChangesetDiff,
    ChangesetDiffResponse as StudioChangesetDiffResponse,
    ChangesetGetResponse as StudioChangesetGetResponse,
    ChangesetRejectResponse as StudioChangesetRejectResponse,
    Error as StudioErrorEnvelope,
    Event as StudioEventEnvelope,
    ForgeStudioDurableJobRecordV2WithV1ReadCompatibility as StudioJob,
    ForgeStudioReviewableFileChangesetV2 as StudioChangeset,
    JobCancelRequest as StudioJobCancelRequest,
    JobCancelResponse as StudioJobCancelResponse,
    JobCreateRequest as StudioJobCreateRequest,
    JobCreateResponse as StudioJobCreateResponse,
    ManagedStudioJobV2CommonRecord as StudioManagedJobV2CommonRecord,
    Method as StudioMethod,
    Response as StudioResponseEnvelope,
    RuntimeHeadlessOperation as StudioRuntimeHeadlessOperation,
    RuntimeHeadlessInput as StudioRuntimeHeadlessInput,
    RuntimeReplayOperation as StudioRuntimeReplayOperation,
    RuntimeReplayInput as StudioRuntimeReplayInput,
    SourceListResponse as StudioSourceListResponse,
    SourceListResult as StudioSourceListResult,
    SourceReadParams as StudioSourceReadParams,
    SourceReadRequest as StudioSourceReadRequest,
    SourceReadResponse as StudioSourceReadResponse,
    SourceReadResult as StudioSourceReadResult,
    WorkspaceOverviewResponse as StudioWorkspaceOverviewResponse,
    WorkspaceOverviewResult as StudioWorkspaceOverviewResult,
    WorkspaceScopedAuthoringMethod as StudioWorkspaceScopedAuthoringMethod,
    WorkspaceScopedAuthoringRequest as StudioWorkspaceScopedAuthoringRequest,
    WorkspaceScopedParams as StudioWorkspaceScopedParams,
    WorldAnalyzeResponse as StudioWorldAnalyzeResponse,
    WorldAnalyzeResult as StudioWorldAnalyzeResult,
    WorldValidateResponse as StudioWorldValidateResponse,
    WorldValidateResult as StudioWorldValidateResult,
} from "../generated/studio-protocol";
import type {
    ErrorEnvelope as StudioV2ErrorEnvelope,
    ForgeStudioExternalArtifactGrantV1 as StudioExternalGrant,
    ForgeStudioExternalArtifactJobV3 as StudioExternalJob,
    Method as StudioV2Method,
    Response as StudioV2ResponseEnvelope,
} from "../generated/studio-protocol-v2";
import type {
    ChangesetApplyResult as StudioCreationChangesetApplyResult,
    ChangesetDiffResult as StudioCreationChangesetDiffResult,
    ChangesetRecoverResult as StudioCreationChangesetRecoverResult,
    DocumentListResult as StudioCreationDocumentListResult,
    DocumentReadResult as StudioCreationDocumentReadResult,
    ErrorEnvelope as StudioV3ErrorEnvelope,
    ReadinessResult as StudioCreationReadinessResult,
    Response as StudioV3ResponseEnvelope,
    WorkflowResult as StudioCreationWorkflowResult,
    WorkspaceOpenResult as StudioCreationWorkspaceOpenResult,
    WorkspaceRecoverResult as StudioCreationWorkspaceRecoverResult,
    WorkspaceResult as StudioCreationWorkspaceResult,
    WorkspaceListResult as StudioCreationWorkspaceListResult,
    WorldForgeStudioCreationChangesetV1 as StudioCreationChangeset,
    WorldForgeStudioCreationWorkspaceV1 as StudioCreationWorkspace,
} from "../generated/studio-protocol-v3";
import type {
    ArtifactInspectResult as StudioCreationArtifactInspectResult,
    ArtifactListResult as StudioCreationArtifactListResult,
    ErrorEnvelope as StudioV4ErrorEnvelope,
    EvidenceInspectResult as StudioCreationEvidenceInspectResult,
    EventListResult as StudioCreationEventListResult,
    JobListResult as StudioCreationJobListResult,
    JobResult as StudioCreationJobResult,
    Method as StudioV4Method,
    OutputGrantListResult as StudioCreationOutputGrantListResult,
    Response as StudioV4ResponseEnvelope,
    WorldForgeStudioCreationArtifactEvidenceV1 as StudioCreationArtifact,
    WorldForgeStudioCreationEvidenceProjectionV1 as StudioCreationEvidence,
    WorldForgeStudioCreationJobV9 as StudioCreationJobV9,
    WorldForgeStudioCreationOutputGrantV5 as StudioCreationOutputGrantV5,
} from "../generated/studio-protocol-v4";
import type {
    ErrorEnvelope as StudioV5ErrorEnvelope,
    JobCreateParams as StudioV5CreationJobCreateParams,
    Method as StudioV5Method,
    Response as StudioV5ResponseEnvelope,
    WorldForgeStudioCreationJobV12 as StudioCreationJob,
    WorldForgeStudioCreationJobV12 as StudioCreationJobV12,
    WorldForgeStudioCreationOutputGrantV6 as StudioCreationOutputGrantV6,
} from "../generated/studio-protocol-v5";
import type { CreationContentMode } from "../generated/creation-content-modes";

export type {
    StudioAssetCatalogEntry,
    StudioAssetCatalogInspectResponse,
    StudioAssetCatalogListResponse,
    StudioAssetInspection,
    StudioErrorEnvelope,
    StudioEventEnvelope,
    StudioAssetReceiptValidateInput,
    StudioAssetpackVerifyInput,
    StudioChangeset,
    StudioChangesetApplyResponse,
    StudioChangesetApproveResponse,
    StudioChangesetCreateResponse,
    StudioChangesetDiff,
    StudioChangesetDiffResponse,
    StudioChangesetGetResponse,
    StudioChangesetRejectResponse,
    StudioJob,
    StudioJobCancelRequest,
    StudioJobCancelResponse,
    StudioJobCreateRequest,
    StudioJobCreateResponse,
    StudioMethod,
    StudioResponseEnvelope,
    StudioRuntimeHeadlessInput,
    StudioRuntimeReplayInput,
    StudioSourceListResponse,
    StudioSourceListResult,
    StudioSourceReadParams,
    StudioSourceReadRequest,
    StudioSourceReadResponse,
    StudioSourceReadResult,
    StudioWorkspaceOverviewResponse,
    StudioWorkspaceOverviewResult,
    StudioWorkspaceScopedAuthoringMethod,
    StudioWorkspaceScopedAuthoringRequest,
    StudioWorkspaceScopedParams,
    StudioWorldAnalyzeResponse,
    StudioWorldAnalyzeResult,
    StudioWorldValidateResponse,
    StudioWorldValidateResult,
};
export type {
    StudioExternalGrant,
    StudioExternalJob,
    StudioV2ErrorEnvelope,
    StudioV2Method,
    StudioV2ResponseEnvelope,
};
export type {
    StudioCreationChangeset,
    StudioCreationChangesetApplyResult,
    StudioCreationChangesetDiffResult,
    StudioCreationChangesetRecoverResult,
    StudioCreationDocumentListResult,
    StudioCreationDocumentReadResult,
    StudioCreationReadinessResult,
    StudioCreationWorkflowResult,
    StudioCreationWorkspace,
    StudioCreationWorkspaceListResult,
    StudioCreationWorkspaceOpenResult,
    StudioCreationWorkspaceRecoverResult,
    StudioCreationWorkspaceResult,
    StudioV3ErrorEnvelope,
    StudioV3ResponseEnvelope,
};
export type {
    StudioCreationArtifact,
    StudioCreationArtifactInspectResult,
    StudioCreationArtifactListResult,
    StudioCreationEvidence,
    StudioCreationOutputGrantV5,
    StudioCreationOutputGrantListResult,
    StudioCreationEvidenceInspectResult,
    StudioCreationEventListResult,
    StudioCreationJob,
    StudioCreationJobV9,
    StudioCreationJobListResult,
    StudioCreationJobResult,
    StudioV4ErrorEnvelope,
    StudioV4Method,
    StudioV4ResponseEnvelope,
};
export type {
    StudioCreationJobV12,
    StudioCreationOutputGrantV6,
    StudioV5CreationJobCreateParams,
    StudioV5ErrorEnvelope,
    StudioV5Method,
    StudioV5ResponseEnvelope,
};

export type StudioReplyEnvelope = StudioResponseEnvelope | StudioErrorEnvelope;
export type StudioCreationOutputGrant = StudioCreationOutputGrantV6;
export type StudioV2ReplyEnvelope =
    StudioV2ResponseEnvelope | StudioV2ErrorEnvelope;
export type StudioV3ReplyEnvelope =
    StudioV3ResponseEnvelope | StudioV3ErrorEnvelope;
export type StudioV4ReplyEnvelope =
    StudioV4ResponseEnvelope | StudioV4ErrorEnvelope;
export type StudioV5ReplyEnvelope =
    StudioV5ResponseEnvelope | StudioV5ErrorEnvelope;
export type StudioCreationWorkspaceReplyEnvelope =
    StudioV3ReplyEnvelope | StudioV5ReplyEnvelope;
export type StudioWorkspaceOverviewReply =
    StudioWorkspaceOverviewResponse | StudioErrorEnvelope;
export type StudioSourceListReply =
    StudioSourceListResponse | StudioErrorEnvelope;
export type StudioSourceReadReply =
    StudioSourceReadResponse | StudioErrorEnvelope;
export type StudioAssetCatalogListReply =
    StudioAssetCatalogListResponse | StudioErrorEnvelope;
export type StudioAssetCatalogInspectReply =
    StudioAssetCatalogInspectResponse | StudioErrorEnvelope;
export type StudioAssetPreviewOpenReply =
    StudioAssetPreviewOpenResponse | StudioErrorEnvelope;
export type StudioAssetPreviewCloseReply =
    StudioAssetPreviewCloseResponse | StudioErrorEnvelope;
export type StudioAssetPreviewChunkResponse = Omit<
    StudioWireAssetPreviewReadResponse,
    "result"
> & {
    result: Omit<
        StudioWireAssetPreviewReadResponse["result"],
        "data_base64"
    > & {
        bytes: Uint8Array;
    };
};
export type StudioAssetPreviewChunkReply =
    StudioAssetPreviewChunkResponse | StudioErrorEnvelope;
export type StudioWorldValidateReply =
    StudioWorldValidateResponse | StudioErrorEnvelope;
export type StudioWorldAnalyzeReply =
    StudioWorldAnalyzeResponse | StudioErrorEnvelope;
export type StudioJobCreateReply =
    StudioJobCreateResponse | StudioErrorEnvelope;
export type StudioJobCancelReply =
    StudioJobCancelResponse | StudioErrorEnvelope;
export type StudioChangesetCreateReply =
    StudioChangesetCreateResponse | StudioErrorEnvelope;
export type StudioChangesetGetReply =
    StudioChangesetGetResponse | StudioErrorEnvelope;
export type StudioChangesetDiffReply =
    StudioChangesetDiffResponse | StudioErrorEnvelope;
export type StudioChangesetApproveReply =
    StudioChangesetApproveResponse | StudioErrorEnvelope;
export type StudioChangesetRejectReply =
    StudioChangesetRejectResponse | StudioErrorEnvelope;
export type StudioChangesetApplyReply =
    StudioChangesetApplyResponse | StudioErrorEnvelope;

export type StudioAssetReceiptValidateJob = StudioManagedJobV2CommonRecord &
    StudioAssetReceiptValidateOperation;
export type StudioAssetpackVerifyJob = StudioManagedJobV2CommonRecord &
    StudioAssetpackVerifyOperation;
export type StudioRuntimeHeadlessJob = StudioManagedJobV2CommonRecord &
    StudioRuntimeHeadlessOperation;
export type StudioRuntimeReplayJob = StudioManagedJobV2CommonRecord &
    StudioRuntimeReplayOperation;

type StudioJobCreateResponseWithJob<TJob> = Omit<
    StudioJobCreateResponse,
    "result"
> & {
    result: { job: TJob };
};

export type StudioAssetReceiptValidateResponse =
    StudioJobCreateResponseWithJob<StudioAssetReceiptValidateJob>;
export type StudioAssetpackVerifyResponse =
    StudioJobCreateResponseWithJob<StudioAssetpackVerifyJob>;
export type StudioRuntimeHeadlessResponse =
    StudioJobCreateResponseWithJob<StudioRuntimeHeadlessJob>;
export type StudioRuntimeReplayResponse =
    StudioJobCreateResponseWithJob<StudioRuntimeReplayJob>;

export type StudioAssetReceiptValidateReply =
    StudioAssetReceiptValidateResponse | StudioErrorEnvelope;
export type StudioAssetpackVerifyReply =
    StudioAssetpackVerifyResponse | StudioErrorEnvelope;
export type StudioRuntimeHeadlessReply =
    StudioRuntimeHeadlessResponse | StudioErrorEnvelope;
export type StudioRuntimeReplayReply =
    StudioRuntimeReplayResponse | StudioErrorEnvelope;

export type ForgeServiceState =
    "stopped" | "starting" | "ready" | "unavailable" | "crashed";

export interface ForgeServiceStatus {
    state: ForgeServiceState;
    message: string;
    pid: number | null;
}

export type StudioReadMethod =
    "workspace.list" | "events.list" | "changeset.list" | "job.list";

export type StudioCapabilityMethod =
    | StudioReadMethod
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
    | "changeset.create"
    | "changeset.get"
    | "changeset.diff"
    | "changeset.approve"
    | "changeset.reject"
    | "changeset.apply"
    | "job.create"
    | "job.cancel";

export interface EventsListParams {
    workspace_id?: string;
    after_id?: number;
    limit?: number;
}

export interface ChangesetsListParams {
    workspace_id?: string;
    status?: "staged" | "approved" | "applying" | "rejected" | "applied";
    limit?: number;
}

export interface JobsListParams {
    workspace_id?: string;
    state?:
        | "queued"
        | "running"
        | "awaiting_approval"
        | "awaiting_user"
        | "paused"
        | "succeeded"
        | "failed"
        | "canceled"
        | "orphaned";
    limit?: number;
}

export type StudioExternalOperation =
    "game.materialize" | "game.package" | "game.package.extract";
export type StudioExternalArtifactKind =
    "game_materialization_bundle" | "standalone_game" | "game_package";

export interface CreateExternalGrantParams {
    workspaceId: string;
    operation: StudioExternalOperation;
    role: "source" | "target";
    artifactKind: StudioExternalArtifactKind;
    expectedContentHash: string | null;
}

export interface StudioMaterializeGameParams {
    workspaceId: string;
    sourceGrantId: string;
    targetGrantId: string;
    expectedMaterializationHash: string;
}

export interface StudioPackageGameParams {
    workspaceId: string;
    sourceGrantId: string;
    targetGrantId: string;
    expectedGameHash: string;
}

export interface StudioExtractGamePackageParams {
    workspaceId: string;
    sourceGrantId: string;
    targetGrantId: string;
    expectedPackageHash: string;
}

export interface StudioExternalJobsListParams {
    workspaceId?: string;
    state?:
        "queued" | "running" | "succeeded" | "failed" | "canceled" | "orphaned";
    limit?: number;
}

interface StudioCreationProjectCreateBase {
    projectId: string;
    title: string;
    defaultLocale: string;
    projectVersion: string;
}

type StudioCreationGameBase = StudioCreationProjectCreateBase & {
    projectKind: "game";
    gameplayFamily:
        | "action"
        | "adventure"
        | "educational"
        | "narrative"
        | "puzzle"
        | "rhythm"
        | "role_playing"
        | "sandbox"
        | "simulation"
        | "sports"
        | "strategy";
    initialCoreVerb: string;
    initialCoreLoop: string;
    worldPresence: "none" | "abstract" | "symbolic" | "diegetic";
    presentationMode: "text" | "2d" | "2_5d" | "3d" | "mixed" | "vr" | "ar";
    runtimeSupportIntent: "authoring_only" | "compatibility_assessment";
    assetContentMode?: CreationContentMode;
};

type StudioCreationGameWithoutNarrative = StudioCreationGameBase & {
    narrativeRequirement: "none";
    narrativeAuthorship: "none";
    narrativeTopology: "none";
};

type StudioCreationGameWithNarrative = StudioCreationGameBase & {
    narrativeRequirement: "optional" | "required";
    narrativeAuthorship:
        | "authored"
        | "emergent"
        | "procedural"
        | "player_authored"
        | "social"
        | "hybrid";
    narrativeTopology:
        | "linear"
        | "foldback"
        | "branching"
        | "branch_and_bottleneck"
        | "hub_and_spoke"
        | "modular"
        | "storylet"
        | "loop_reset"
        | "episodic"
        | "seasonal"
        | "open_ended";
};

export type StudioCreationProjectCreateParams =
    | (StudioCreationProjectCreateBase & {
          projectKind: "universe_library" | "asset_library";
      })
    | StudioCreationGameWithoutNarrative
    | StudioCreationGameWithNarrative;

export interface StudioCreationProfileStageParams {
    workspaceId: string;
    expectedRootGeneration: number;
    expectedSourceRevision: string;
    expectedWorkflowStatusHash: string | null;
    path: string;
    expectedBaseFileSha256: string;
    proposedProfile: Record<string, unknown>;
}

export type StudioCreationModuleFormat =
    | "world-forge.world_module"
    | "world-forge.activity_module"
    | "world-forge.narrative_module"
    | "world-forge.system_module"
    | "world-forge.logic_module";

export interface StudioCreationAuthorityParams {
    workspaceId: string;
    expectedRootGeneration: number;
    expectedSourceRevision: string;
    expectedWorkflowStatusHash: string | null;
}

export interface StudioCreationEvidenceAuthorityParams extends StudioCreationAuthorityParams {
    expectedArtifactSnapshotHash: string | null;
}

export interface StudioCreationArtifactListParams extends StudioCreationEvidenceAuthorityParams {
    lifecycle: "active" | "invalidated" | "historical" | "candidate" | null;
    cursor: string | null;
    limit: number;
}

export interface StudioCreationArtifactInspectParams extends StudioCreationEvidenceAuthorityParams {
    expectedArtifactSnapshotHash: string;
    artifactId: string;
}

export interface StudioCreationPreviewOpenParams extends StudioCreationEvidenceAuthorityParams {
    expectedArtifactSnapshotHash: string;
    assetpackArtifactId: string;
    outputGrantId: string;
    expectedOutputGrantGeneration: number;
    assetId: string;
}

export interface StudioCreationJobAuthorityParams extends StudioCreationAuthorityParams {
    expectedArtifactSnapshotHash: string;
    jobId?: string;
}

export type StudioCreationCompileParams = StudioCreationJobAuthorityParams;

export interface StudioCreationAdmissionParams extends StudioCreationJobAuthorityParams {
    document: Record<string, unknown>;
    dependencyArtifactIds: string[];
}

export interface StudioCreationAssetAcceptanceResult {
    criterionIndex: number;
    criterionSha256: string;
    status: "failed" | "passed";
    evidenceHashes: string[];
}

export const MAX_STUDIO_ASSET_ACCEPTANCE_ITEMS = 64;

const STUDIO_ASSET_ACCEPTANCE_FIELDS = [
    "criterionIndex",
    "criterionSha256",
    "evidenceHashes",
    "status",
] as const;
const STUDIO_ASSET_SHA256 = /^[0-9a-f]{64}$/u;

function isDenseArray(value: readonly unknown[]): boolean {
    for (let index = 0; index < value.length; index += 1) {
        if (!Object.hasOwn(value, index)) return false;
    }
    return true;
}

export function validateStudioCreationAssetAcceptanceResults(
    value: unknown,
): StudioCreationAssetAcceptanceResult[] {
    if (
        !Array.isArray(value) ||
        value.length < 1 ||
        value.length > MAX_STUDIO_ASSET_ACCEPTANCE_ITEMS
    ) {
        throw new TypeError(
            "Studio creation asset acceptance results are invalid",
        );
    }
    const criterionHashes = new Set<string>();
    const checked: StudioCreationAssetAcceptanceResult[] = [];
    const values = value as readonly unknown[];
    for (let index = 0; index < values.length; index += 1) {
        if (!Object.hasOwn(values, index)) {
            throw new TypeError(
                "Studio creation asset acceptance result is invalid",
            );
        }
        const raw = values[index];
        if (typeof raw !== "object" || raw === null || Array.isArray(raw)) {
            throw new TypeError(
                "Studio creation asset acceptance result is invalid",
            );
        }
        const item = raw as Record<string, unknown>;
        const fields = Object.keys(item).sort();
        if (
            fields.length !== STUDIO_ASSET_ACCEPTANCE_FIELDS.length ||
            fields.some(
                (field, fieldIndex) =>
                    field !== STUDIO_ASSET_ACCEPTANCE_FIELDS[fieldIndex],
            )
        ) {
            throw new TypeError(
                "Studio creation asset acceptance result fields are invalid",
            );
        }
        if (
            !Number.isInteger(item.criterionIndex) ||
            item.criterionIndex !== index
        ) {
            throw new TypeError(
                "Studio creation asset criterion index is not canonical",
            );
        }
        if (
            typeof item.criterionSha256 !== "string" ||
            !STUDIO_ASSET_SHA256.test(item.criterionSha256)
        ) {
            throw new TypeError(
                "Studio creation asset criterion hash is invalid",
            );
        }
        if (criterionHashes.has(item.criterionSha256)) {
            throw new TypeError(
                "Studio creation asset criterion hashes repeat",
            );
        }
        criterionHashes.add(item.criterionSha256);
        if (item.status !== "failed" && item.status !== "passed") {
            throw new TypeError(
                "Studio creation asset criterion status is invalid",
            );
        }
        if (
            !Array.isArray(item.evidenceHashes) ||
            item.evidenceHashes.length < 1 ||
            item.evidenceHashes.length > MAX_STUDIO_ASSET_ACCEPTANCE_ITEMS ||
            !isDenseArray(item.evidenceHashes) ||
            item.evidenceHashes.some(
                (digest) =>
                    typeof digest !== "string" ||
                    !STUDIO_ASSET_SHA256.test(digest),
            )
        ) {
            throw new TypeError(
                "Studio creation asset criterion evidence is invalid",
            );
        }
        const evidenceHashes = item.evidenceHashes as string[];
        const canonicalEvidence = [...new Set(evidenceHashes)].sort();
        if (
            evidenceHashes.length !== canonicalEvidence.length ||
            evidenceHashes.some(
                (digest, evidenceIndex) =>
                    digest !== canonicalEvidence[evidenceIndex],
            )
        ) {
            throw new TypeError(
                "Studio creation asset criterion evidence must be unique and canonical",
            );
        }
        checked.push({
            criterionIndex: index,
            criterionSha256: item.criterionSha256,
            status: item.status,
            evidenceHashes: [...evidenceHashes],
        });
    }
    return checked;
}

export interface StudioCreationAssetProcessParams extends StudioCreationJobAuthorityParams {
    licenseArtifactIds: string[];
    recipeId: string;
    processingReceiptId: string;
    qaReportId: string;
    acceptanceResults: StudioCreationAssetAcceptanceResult[];
}

export interface StudioCreationAssetReleaseSealParams extends StudioCreationJobAuthorityParams {
    qaReportArtifactIds: string[];
    manifestId: string;
    targetGrantId: string;
    expectedTargetGrantGeneration: number;
}

export interface StudioCreationRuntimeComposeParams extends StudioCreationJobAuthorityParams {
    gamepackArtifactId: string;
    assetInventoryArtifactId: string;
    assetpackArtifactId: string;
    targetGrantId: string;
    expectedTargetGrantGeneration: number;
}

export interface StudioCreationRuntimeBundleBuildParams extends StudioCreationJobAuthorityParams {
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

export interface StudioCreationMaterializationBundleBuildParams extends StudioCreationJobAuthorityParams {
    runtimeBundleArtifactId: string;
    sourceGrantId: string;
    expectedSourceGrantGeneration: number;
    targetGrantId: string;
    expectedTargetGrantGeneration: number;
}

export interface StudioCreationGameMaterializeParams extends StudioCreationJobAuthorityParams {
    materializationBundleArtifactId: string;
    sourceGrantId: string;
    expectedSourceGrantGeneration: number;
    targetGrantId: string;
    expectedTargetGrantGeneration: number;
}

export interface StudioCreationGamePackageParams extends StudioCreationJobAuthorityParams {
    standaloneGameArtifactId: string;
    sourceGrantId: string;
    expectedSourceGrantGeneration: number;
    targetGrantId: string;
    expectedTargetGrantGeneration: number;
}

export interface StudioCreationGamePackageExtractParams extends StudioCreationJobAuthorityParams {
    gamePackageArtifactId: string;
    sourceGrantId: string;
    expectedSourceGrantGeneration: number;
    targetGrantId: string;
    expectedTargetGrantGeneration: number;
}

export interface StudioCreationAuthorityReviewParams {
    workspaceId: string;
    qaReportArtifactId: string;
    outputRole: string;
}

export interface StudioCreationAuthorityReleaseParams {
    workspaceId: string;
    reviewReceiptArtifactIds: string[];
    targetGrantId: string;
}

export interface StudioCreationAuthorityHeadlessParams {
    workspaceId: string;
    runtimeBundleArtifactId: string;
    sourceGrantId: string;
    headlessScriptArtifactId: string;
    targetGrantId: string;
    platformId: string;
}

export interface StudioCreationAuthorityJobActionParams {
    workspaceId: string;
    jobId: string;
}

export interface StudioCreationAuthorityCapabilities {
    protocolVersion: 5;
    asset_authority_reviews: true;
    asset_release_authority: true;
    runtime_headless_authority: true;
    creation_preview_pre_release: true;
}

export interface StudioCreationOutputGrantMutationParams {
    grantId: string;
    expectedGeneration: number;
}

export interface StudioCreationOutputGrantListParams extends StudioCreationAuthorityParams {
    expectedArtifactSnapshotHash: string;
    cursor: string | null;
    limit: number;
}

export type StudioCreationJobState =
    "queued" | "running" | "succeeded" | "failed" | "canceled" | "orphaned";

export interface StudioCreationJobListParams {
    workspaceId: string;
    state: StudioCreationJobState | null;
    afterSequence: number;
    limit: number;
}

export interface StudioCreationJobMutationParams {
    jobId: string;
    expectedGeneration: number;
    expectedRecordHash: string;
}

export interface StudioCreationJobRecoveryParams extends StudioCreationJobMutationParams {
    mode: "resume" | "rollback" | "cleanup";
}

export interface StudioCreationEventListParams {
    workspaceId: string;
    afterId: number;
    limit: number;
}

export interface StudioCreationModuleStageParams extends StudioCreationAuthorityParams {
    operation: "create" | "replace" | "delete";
    path: string;
    format: StudioCreationModuleFormat;
    expectedBaseFileSha256: string | null;
    proposedModule?: Record<string, unknown>;
}

export interface StudioCreationWorkflowReconcileParams extends StudioCreationAuthorityParams {
    artifactRegistry: Record<string, unknown>[];
}

export interface StudioCreationPhaseReadParams extends StudioCreationAuthorityParams {
    expectedWorkflowStatusHash: string;
    phaseId: string;
}

export interface StudioCreationPhaseReportParams extends StudioCreationAuthorityParams {
    report: Record<string, unknown>;
    artifactRegistry: Record<string, unknown>[];
}

export interface StudioCreationPhaseCompleteParams extends StudioCreationPhaseReportParams {
    expectedWorkflowStatusHash: string;
}

export interface StudioCreationPhaseReopenParams extends StudioCreationAuthorityParams {
    expectedWorkflowStatusHash: string;
    phaseId: string;
    reason: string;
    approvedBy: string;
}

export interface StudioAssetCatalogPage {
    offset: number;
    manifestRevision: string;
}

export type StudioActivityEvent =
    | {
          type: "service-status";
          status: ForgeServiceStatus;
      }
    | {
          type: "studio-event";
          envelope: StudioEventEnvelope;
      }
    | {
          type: "service-stderr";
          text: string;
      };

export type CodexBridgeState =
    "unbound" | "starting" | "ready" | "unavailable" | "crashed";

export interface CodexBridgeStatus {
    state: CodexBridgeState;
    message: string;
    pid: number | null;
    workspaceId: string | null;
}

export interface CodexUserInputOption {
    label: string;
    description: string;
}

export interface CodexUserInputQuestion {
    id: string;
    header: string;
    question: string;
    isOther: boolean;
    isSecret: boolean;
    options: CodexUserInputOption[] | null;
}

export type CodexActivityEvent =
    | { type: "codex-status"; status: CodexBridgeStatus }
    | { type: "codex-stderr"; text: string }
    | {
          type: "codex-notification";
          method: string;
          params: unknown;
          authoritative: boolean;
      }
    | {
          type: "codex-user-input";
          token: string;
          threadId: string;
          turnId: string;
          questions: CodexUserInputQuestion[];
      };

export type CodexLoginMode = "browser" | "device-code";

export interface CodexAccountSummary {
    requiresOpenaiAuth: boolean;
    account:
        | null
        | { type: "apiKey" }
        | { type: "chatgpt"; email: string | null; planType: string };
}

export type CodexLoginStart =
    | { type: "chatgpt"; loginId: string; authUrl: string }
    | {
          type: "chatgptDeviceCode";
          loginId: string;
          verificationUrl: string;
          userCode: string;
      };

export interface CodexThreadSummary {
    threadId: string;
}

export interface CodexTurnSummary {
    turnId: string;
    status: string;
}

export interface StudioClientError {
    code:
        | "invalid_request"
        | "service_unavailable"
        | "timeout"
        | "cancelled"
        | "internal_error";
    message: string;
}

export type StudioClientResult<T> =
    { ok: true; value: T } | { ok: false; error: StudioClientError };

export interface ForgeStudioApi {
    initialize(): Promise<StudioClientResult<StudioReplyEnvelope>>;
    getServiceStatus(): Promise<StudioClientResult<ForgeServiceStatus>>;
    listWorkspaces(): Promise<StudioClientResult<StudioReplyEnvelope>>;
    listEvents(
        params?: EventsListParams,
    ): Promise<StudioClientResult<StudioReplyEnvelope>>;
    listChangesets(
        params?: ChangesetsListParams,
    ): Promise<StudioClientResult<StudioReplyEnvelope>>;
    listJobs(
        params?: JobsListParams,
    ): Promise<StudioClientResult<StudioReplyEnvelope>>;
    listCreationWorkspaces(): Promise<
        StudioClientResult<StudioV3ReplyEnvelope>
    >;
    registerCreationProject(): Promise<
        StudioClientResult<StudioV3ReplyEnvelope>
    >;
    createCreationProject(
        params: StudioCreationProjectCreateParams,
    ): Promise<StudioClientResult<StudioCreationWorkspaceReplyEnvelope>>;
    openCreationWorkspace(
        workspaceId: string,
    ): Promise<StudioClientResult<StudioV3ReplyEnvelope>>;
    listCreationDocuments(
        workspaceId: string,
        expectedSourceRevision: string,
    ): Promise<StudioClientResult<StudioV3ReplyEnvelope>>;
    readCreationDocument(
        workspaceId: string,
        expectedSourceRevision: string,
        path: string,
    ): Promise<StudioClientResult<StudioV3ReplyEnvelope>>;
    getCreationWorkflow(
        workspaceId: string,
    ): Promise<StudioClientResult<StudioV3ReplyEnvelope>>;
    inspectCreationReadiness(
        workspaceId: string,
    ): Promise<StudioClientResult<StudioV3ReplyEnvelope>>;
    listCreationArtifacts(
        params: StudioCreationArtifactListParams,
    ): Promise<StudioClientResult<StudioV4ReplyEnvelope>>;
    inspectCreationArtifact(
        params: StudioCreationArtifactInspectParams,
    ): Promise<StudioClientResult<StudioV4ReplyEnvelope>>;
    inspectCreationEvidence(
        params: StudioCreationEvidenceAuthorityParams,
    ): Promise<StudioClientResult<StudioV4ReplyEnvelope>>;
    openCreationPreview(
        params: StudioCreationPreviewOpenParams,
    ): Promise<StudioClientResult<StudioV4ReplyEnvelope>>;
    readCreationPreviewChunk(
        handle: string,
        sequence: number,
    ): Promise<StudioClientResult<StudioV4ReplyEnvelope>>;
    closeCreationPreview(
        handle: string,
    ): Promise<StudioClientResult<StudioV4ReplyEnvelope>>;
    compileCreationProject(
        params: StudioCreationCompileParams,
    ): Promise<StudioClientResult<StudioV4ReplyEnvelope>>;
    admitCreationArtifact(
        params: StudioCreationAdmissionParams,
    ): Promise<StudioClientResult<StudioV4ReplyEnvelope>>;
    processCreationAsset(
        params: StudioCreationAssetProcessParams,
    ): Promise<StudioClientResult<StudioV4ReplyEnvelope>>;
    selectCreationAssetpackOutput(
        workspaceId: string,
    ): Promise<StudioClientResult<StudioV4ReplyEnvelope>>;
    selectCreationRuntimeBundleOutput(
        workspaceId: string,
    ): Promise<StudioClientResult<StudioV4ReplyEnvelope>>;
    selectCreationMaterializationBundleOutput(
        workspaceId: string,
    ): Promise<StudioClientResult<StudioV4ReplyEnvelope>>;
    selectCreationStandaloneGameOutput(
        workspaceId: string,
    ): Promise<StudioClientResult<StudioV4ReplyEnvelope>>;
    selectCreationGamePackageOutput(
        workspaceId: string,
    ): Promise<StudioClientResult<StudioV4ReplyEnvelope>>;
    selectCreationGamePackageExtractionOutput(
        workspaceId: string,
    ): Promise<StudioClientResult<StudioV4ReplyEnvelope>>;
    getCreationAssetpackOutput(
        grantId: string,
    ): Promise<StudioClientResult<StudioV4ReplyEnvelope>>;
    getCreationAuthorityCapabilities?(): Promise<
        StudioClientResult<StudioCreationAuthorityCapabilities>
    >;
    listCreationOutputGrants(
        params: StudioCreationOutputGrantListParams,
    ): Promise<StudioClientResult<StudioV4ReplyEnvelope>>;
    listCreationAuthorityOutputGrants?(
        params: StudioCreationOutputGrantListParams,
    ): Promise<StudioClientResult<StudioV5ReplyEnvelope>>;
    revokeCreationAssetpackOutput(
        params: StudioCreationOutputGrantMutationParams,
    ): Promise<StudioClientResult<StudioV4ReplyEnvelope>>;
    sealCreationAssetRelease(
        params: StudioCreationAssetReleaseSealParams,
    ): Promise<StudioClientResult<StudioV4ReplyEnvelope>>;
    composeCreationRuntime(
        params: StudioCreationRuntimeComposeParams,
    ): Promise<StudioClientResult<StudioV4ReplyEnvelope>>;
    buildCreationRuntimeBundle(
        params: StudioCreationRuntimeBundleBuildParams,
    ): Promise<StudioClientResult<StudioV4ReplyEnvelope>>;
    buildCreationMaterializationBundle(
        params: StudioCreationMaterializationBundleBuildParams,
    ): Promise<StudioClientResult<StudioV4ReplyEnvelope>>;
    materializeCreationGame(
        params: StudioCreationGameMaterializeParams,
    ): Promise<StudioClientResult<StudioV4ReplyEnvelope>>;
    packageCreationGame(
        params: StudioCreationGamePackageParams,
    ): Promise<StudioClientResult<StudioV4ReplyEnvelope>>;
    extractCreationGamePackage(
        params: StudioCreationGamePackageExtractParams,
    ): Promise<StudioClientResult<StudioV4ReplyEnvelope>>;
    reviewCreationAssetQa(
        params: StudioCreationAuthorityReviewParams,
    ): Promise<StudioClientResult<StudioV5ReplyEnvelope>>;
    authorizeCreationAssetRelease(
        params: StudioCreationAuthorityReleaseParams,
    ): Promise<StudioClientResult<StudioV5ReplyEnvelope>>;
    selectCreationHeadlessEvidenceOutput(
        workspaceId: string,
    ): Promise<StudioClientResult<StudioV5ReplyEnvelope>>;
    verifyCreationHeadless(
        params: StudioCreationAuthorityHeadlessParams,
    ): Promise<StudioClientResult<StudioV5ReplyEnvelope>>;
    requestCreationJobCancel(
        params: StudioCreationAuthorityJobActionParams,
    ): Promise<StudioClientResult<StudioV5ReplyEnvelope>>;
    requestCreationJobRecovery(
        params: StudioCreationAuthorityJobActionParams,
    ): Promise<StudioClientResult<StudioV5ReplyEnvelope>>;
    getCreationJob(
        jobId: string,
    ): Promise<StudioClientResult<StudioV4ReplyEnvelope>>;
    listCreationJobs(
        params: StudioCreationJobListParams,
    ): Promise<StudioClientResult<StudioV4ReplyEnvelope>>;
    cancelCreationJob(
        params: StudioCreationJobMutationParams,
    ): Promise<StudioClientResult<StudioV4ReplyEnvelope>>;
    recoverCreationJob(
        params: StudioCreationJobRecoveryParams,
    ): Promise<StudioClientResult<StudioV4ReplyEnvelope>>;
    listCreationEvents(
        params: StudioCreationEventListParams,
    ): Promise<StudioClientResult<StudioV4ReplyEnvelope>>;
    stageCreationProfile(
        params: StudioCreationProfileStageParams,
    ): Promise<StudioClientResult<StudioV3ReplyEnvelope>>;
    stageCreationModuleChange(
        params: StudioCreationModuleStageParams,
    ): Promise<StudioClientResult<StudioV3ReplyEnvelope>>;
    reconcileCreationWorkflow(
        params: StudioCreationWorkflowReconcileParams,
    ): Promise<StudioClientResult<StudioV3ReplyEnvelope>>;
    readCreationPhase(
        params: StudioCreationPhaseReadParams,
    ): Promise<StudioClientResult<StudioV3ReplyEnvelope>>;
    validateCreationPhase(
        params: StudioCreationPhaseReportParams,
    ): Promise<StudioClientResult<StudioV3ReplyEnvelope>>;
    completeCreationPhase(
        params: StudioCreationPhaseCompleteParams,
    ): Promise<StudioClientResult<StudioV3ReplyEnvelope>>;
    reopenCreationPhase(
        params: StudioCreationPhaseReopenParams,
    ): Promise<StudioClientResult<StudioV3ReplyEnvelope>>;
    getCreationChangeset(
        changesetId: string,
    ): Promise<StudioClientResult<StudioV3ReplyEnvelope>>;
    diffCreationChangeset(
        changesetId: string,
    ): Promise<StudioClientResult<StudioV3ReplyEnvelope>>;
    approveCreationChangeset(
        changesetId: string,
        expectedRecordHash: string,
        expectedReviewSha256: string,
    ): Promise<StudioClientResult<StudioV3ReplyEnvelope>>;
    applyCreationChangeset(
        changesetId: string,
        expectedRecordHash: string,
        expectedReviewSha256: string,
        expectedRootGeneration: number,
    ): Promise<StudioClientResult<StudioV3ReplyEnvelope>>;
    recoverCreationChangeset(
        changesetId: string,
        mode: "resume" | "rollback",
        expectedRecordHash: string,
        expectedReviewSha256: string,
        expectedRootGeneration: number,
    ): Promise<StudioClientResult<StudioV3ReplyEnvelope>>;
    getWorkspaceOverview(
        workspaceId: string,
    ): Promise<StudioClientResult<StudioWorkspaceOverviewReply>>;
    listSourceDocuments(
        workspaceId: string,
    ): Promise<StudioClientResult<StudioSourceListReply>>;
    readSourceDocument(
        workspaceId: string,
        path: string,
    ): Promise<StudioClientResult<StudioSourceReadReply>>;
    listAssetCatalog(
        workspaceId: string,
        page?: StudioAssetCatalogPage,
    ): Promise<StudioClientResult<StudioAssetCatalogListReply>>;
    inspectAssetCatalogEntry(
        workspaceId: string,
        manifestRevision: string,
        entryId: string,
    ): Promise<StudioClientResult<StudioAssetCatalogInspectReply>>;
    openAssetPreview(
        workspaceId: string,
        manifestRevision: string,
        entryId: string,
    ): Promise<StudioClientResult<StudioAssetPreviewOpenReply>>;
    readAssetPreviewChunk(
        handle: string,
        sequence: number,
    ): Promise<StudioClientResult<StudioAssetPreviewChunkReply>>;
    closeAssetPreview(
        handle: string,
    ): Promise<StudioClientResult<StudioAssetPreviewCloseReply>>;
    stageSourceDocument(
        workspaceId: string,
        path: string,
        baseSha256: string,
        content: string,
    ): Promise<StudioClientResult<StudioChangesetCreateReply>>;
    getChangeset(
        changesetId: string,
    ): Promise<StudioClientResult<StudioChangesetGetReply>>;
    readChangesetDiff(
        changesetId: string,
    ): Promise<StudioClientResult<StudioChangesetDiffReply>>;
    approveChangeset(
        changesetId: string,
        expectedReviewSha256?: string,
    ): Promise<StudioClientResult<StudioChangesetApproveReply>>;
    rejectChangeset(
        changesetId: string,
        expectedReviewSha256?: string,
    ): Promise<StudioClientResult<StudioChangesetRejectReply>>;
    applyChangeset(
        changesetId: string,
        expectedReviewSha256?: string,
    ): Promise<StudioClientResult<StudioChangesetApplyReply>>;
    validateWorld(
        workspaceId: string,
    ): Promise<StudioClientResult<StudioWorldValidateReply>>;
    analyzeWorld(
        workspaceId: string,
    ): Promise<StudioClientResult<StudioWorldAnalyzeReply>>;
    validateAssetReceipt(
        workspaceId: string,
        input: StudioAssetReceiptValidateInput,
    ): Promise<StudioClientResult<StudioAssetReceiptValidateReply>>;
    verifyAssetpack(
        workspaceId: string,
        input: StudioAssetpackVerifyInput,
    ): Promise<StudioClientResult<StudioAssetpackVerifyReply>>;
    runHeadless(
        workspaceId: string,
        input: StudioRuntimeHeadlessInput,
    ): Promise<StudioClientResult<StudioRuntimeHeadlessReply>>;
    runReplay(
        workspaceId: string,
        input: StudioRuntimeReplayInput,
    ): Promise<StudioClientResult<StudioRuntimeReplayReply>>;
    cancelJob(jobId: string): Promise<StudioClientResult<StudioJobCancelReply>>;
    createExternalGrant(
        params: CreateExternalGrantParams,
    ): Promise<StudioClientResult<StudioV2ReplyEnvelope>>;
    getExternalGrant(
        grantId: string,
    ): Promise<StudioClientResult<StudioV2ReplyEnvelope>>;
    revokeExternalGrant(
        grantId: string,
    ): Promise<StudioClientResult<StudioV2ReplyEnvelope>>;
    materializeGame(
        params: StudioMaterializeGameParams,
    ): Promise<StudioClientResult<StudioV2ReplyEnvelope>>;
    packageGame(
        params: StudioPackageGameParams,
    ): Promise<StudioClientResult<StudioV2ReplyEnvelope>>;
    extractGamePackage(
        params: StudioExtractGamePackageParams,
    ): Promise<StudioClientResult<StudioV2ReplyEnvelope>>;
    getExternalJob(
        jobId: string,
    ): Promise<StudioClientResult<StudioV2ReplyEnvelope>>;
    listExternalJobs(
        params?: StudioExternalJobsListParams,
    ): Promise<StudioClientResult<StudioV2ReplyEnvelope>>;
    cancelExternalJob(
        jobId: string,
    ): Promise<StudioClientResult<StudioV2ReplyEnvelope>>;
    recoverExternalJob(
        jobId: string,
        action: "resume" | "rollback",
    ): Promise<StudioClientResult<StudioV2ReplyEnvelope>>;
    onEvent(listener: (event: StudioActivityEvent) => void): () => void;
    getCodexStatus(): Promise<StudioClientResult<CodexBridgeStatus>>;
    bindCodexWorkspace(
        workspaceId: string,
    ): Promise<StudioClientResult<CodexBridgeStatus>>;
    readCodexAccount(): Promise<StudioClientResult<CodexAccountSummary>>;
    startCodexLogin(
        mode: CodexLoginMode,
    ): Promise<StudioClientResult<CodexLoginStart>>;
    startCodexThread(): Promise<StudioClientResult<CodexThreadSummary>>;
    resumeCodexThread(
        threadId: string,
    ): Promise<StudioClientResult<CodexThreadSummary>>;
    forkCodexThread(
        threadId: string,
    ): Promise<StudioClientResult<CodexThreadSummary>>;
    startCodexTurn(
        threadId: string,
        text: string,
    ): Promise<StudioClientResult<CodexTurnSummary>>;
    steerCodexTurn(
        threadId: string,
        turnId: string,
        text: string,
    ): Promise<StudioClientResult<void>>;
    interruptCodexTurn(
        threadId: string,
        turnId: string,
    ): Promise<StudioClientResult<void>>;
    answerCodexUserInput(
        token: string,
        answers: Record<string, string[]>,
    ): Promise<StudioClientResult<void>>;
    onCodexEvent(listener: (event: CodexActivityEvent) => void): () => void;
}

export const STUDIO_V12_HEADLESS_LINEAGE_ARTIFACT_FIELDS = [
    "gamepack_artifact_id",
    "asset_inventory_artifact_id",
    "assetpack_artifact_id",
    "asset_release_authority_artifact_id",
    "runtime_snapshot_artifact_id",
    "runtime_adapter_registry_artifact_id",
    "runtime_composition_artifact_id",
    "runtime_bundle_artifact_id",
    "headless_script_artifact_id",
] as const;

export function hasDistinctStudioV12HeadlessAuthorityIdentities(
    value: unknown,
): boolean {
    if (typeof value !== "object" || value === null || Array.isArray(value)) {
        return false;
    }
    const params = value as Record<string, unknown>;
    const lineageArtifactIds: string[] = [];
    for (const field of STUDIO_V12_HEADLESS_LINEAGE_ARTIFACT_FIELDS) {
        const artifactId = params[field];
        if (typeof artifactId !== "string") {
            return false;
        }
        lineageArtifactIds.push(artifactId);
    }
    return (
        new Set(lineageArtifactIds).size === lineageArtifactIds.length &&
        typeof params.source_grant_id === "string" &&
        typeof params.target_grant_id === "string" &&
        params.source_grant_id !== params.target_grant_id
    );
}

export const STUDIO_V1_METHODS: ReadonlySet<StudioMethod> = new Set([
    "service.initialize",
    "workspace.register",
    "workspace.list",
    "workspace.get",
    "workspace.overview",
    "source.list",
    "source.read",
    "asset.catalog.list",
    "asset.catalog.inspect",
    "asset.preview.open",
    "asset.preview.read",
    "asset.preview.close",
    "world.validate",
    "world.analyze",
    "events.list",
    "changeset.create",
    "changeset.get",
    "changeset.list",
    "changeset.diff",
    "changeset.approve",
    "changeset.reject",
    "changeset.apply",
    "job.create",
    "job.get",
    "job.list",
    "job.transition",
    "job.cancel",
]);
export const STUDIO_METHODS = STUDIO_V1_METHODS;

export const STUDIO_V2_METHODS: ReadonlySet<StudioV2Method> = new Set([
    "service.initialize",
    "external_grant.create",
    "external_grant.get",
    "external_grant.revoke",
    "job.create",
    "job.get",
    "job.list",
    "job.cancel",
    "job.recover",
]);

export const STUDIO_V4_METHODS: ReadonlySet<StudioV4Method> = new Set([
    "service.initialize",
    "creation_artifact.list",
    "creation_artifact.inspect",
    "creation_evidence.inspect",
    "creation_output_grant.create",
    "creation_output_grant.get",
    "creation_output_grant.list",
    "creation_output_grant.revoke",
    "creation_preview.open",
    "creation_preview.read",
    "creation_preview.close",
    "creation_job.create",
    "creation_job.get",
    "creation_job.list",
    "creation_job.cancel",
    "creation_job.recover",
    "creation_event.list",
]);

export const STUDIO_V5_METHODS: ReadonlySet<StudioV5Method> = new Set([
    ...STUDIO_V4_METHODS,
    "creation_workspace.create",
]);

export const STUDIO_READ_METHODS: ReadonlySet<StudioReadMethod> = new Set([
    "workspace.list",
    "events.list",
    "changeset.list",
    "job.list",
]);

export const IPC_CHANNELS = Object.freeze({
    initialize: "studio:initialize",
    status: "studio:get-service-status",
    listWorkspaces: "studio:list-workspaces",
    listEvents: "studio:list-events",
    listChangesets: "studio:list-changesets",
    listJobs: "studio:list-jobs",
    listCreationWorkspaces: "studio:list-creation-workspaces",
    registerCreationProject: "studio:register-creation-project",
    createCreationProject: "studio:create-creation-project",
    openCreationWorkspace: "studio:open-creation-workspace",
    listCreationDocuments: "studio:list-creation-documents",
    readCreationDocument: "studio:read-creation-document",
    getCreationWorkflow: "studio:get-creation-workflow",
    inspectCreationReadiness: "studio:inspect-creation-readiness",
    listCreationArtifacts: "studio:list-creation-artifacts",
    inspectCreationArtifact: "studio:inspect-creation-artifact",
    inspectCreationEvidence: "studio:inspect-creation-evidence",
    openCreationPreview: "studio:open-creation-preview",
    readCreationPreviewChunk: "studio:read-creation-preview-chunk",
    closeCreationPreview: "studio:close-creation-preview",
    compileCreationProject: "studio:compile-creation-project",
    admitCreationArtifact: "studio:admit-creation-artifact",
    processCreationAsset: "studio:process-creation-asset",
    selectCreationAssetpackOutput: "studio:select-creation-assetpack-output",
    selectCreationRuntimeBundleOutput:
        "studio:select-creation-runtime-bundle-output",
    selectCreationMaterializationBundleOutput:
        "studio:select-creation-materialization-bundle-output",
    selectCreationStandaloneGameOutput:
        "studio:select-creation-standalone-game-output",
    selectCreationGamePackageOutput:
        "studio:select-creation-game-package-output",
    selectCreationGamePackageExtractionOutput:
        "studio:select-creation-game-package-extraction-output",
    getCreationAssetpackOutput: "studio:get-creation-assetpack-output",
    getCreationAuthorityCapabilities:
        "studio:get-creation-authority-capabilities",
    listCreationOutputGrants: "studio:list-creation-output-grants",
    listCreationAuthorityOutputGrants:
        "studio:list-creation-authority-output-grants",
    revokeCreationAssetpackOutput: "studio:revoke-creation-assetpack-output",
    sealCreationAssetRelease: "studio:seal-creation-asset-release",
    composeCreationRuntime: "studio:compose-creation-runtime",
    buildCreationRuntimeBundle: "studio:build-creation-runtime-bundle",
    buildCreationMaterializationBundle:
        "studio:build-creation-materialization-bundle",
    materializeCreationGame: "studio:materialize-creation-game",
    packageCreationGame: "studio:package-creation-game",
    extractCreationGamePackage: "studio:extract-creation-game-package",
    reviewCreationAssetQa: "studio:review-creation-asset-qa",
    authorizeCreationAssetRelease:
        "studio:authorize-creation-asset-release",
    selectCreationHeadlessEvidenceOutput:
        "studio:select-creation-headless-evidence-output",
    verifyCreationHeadless: "studio:verify-creation-headless",
    requestCreationJobCancel: "studio:request-creation-job-cancel",
    requestCreationJobRecovery: "studio:request-creation-job-recovery",
    getCreationJob: "studio:get-creation-job",
    listCreationJobs: "studio:list-creation-jobs",
    cancelCreationJob: "studio:cancel-creation-job",
    recoverCreationJob: "studio:recover-creation-job",
    listCreationEvents: "studio:list-creation-events",
    stageCreationProfile: "studio:stage-creation-profile",
    stageCreationModuleChange: "studio:stage-creation-module-change",
    reconcileCreationWorkflow: "studio:reconcile-creation-workflow",
    readCreationPhase: "studio:read-creation-phase",
    validateCreationPhase: "studio:validate-creation-phase",
    completeCreationPhase: "studio:complete-creation-phase",
    reopenCreationPhase: "studio:reopen-creation-phase",
    getCreationChangeset: "studio:get-creation-changeset",
    diffCreationChangeset: "studio:diff-creation-changeset",
    approveCreationChangeset: "studio:approve-creation-changeset",
    applyCreationChangeset: "studio:apply-creation-changeset",
    recoverCreationChangeset: "studio:recover-creation-changeset",
    getWorkspaceOverview: "studio:get-workspace-overview",
    listSourceDocuments: "studio:list-source-documents",
    readSourceDocument: "studio:read-source-document",
    listAssetCatalog: "studio:list-asset-catalog",
    inspectAssetCatalogEntry: "studio:inspect-asset-catalog-entry",
    openAssetPreview: "studio:open-asset-preview",
    readAssetPreviewChunk: "studio:read-asset-preview-chunk",
    closeAssetPreview: "studio:close-asset-preview",
    stageSourceDocument: "studio:stage-source-document",
    getChangeset: "studio:get-changeset",
    readChangesetDiff: "studio:read-changeset-diff",
    approveChangeset: "studio:approve-changeset",
    rejectChangeset: "studio:reject-changeset",
    applyChangeset: "studio:apply-changeset",
    validateWorld: "studio:validate-world",
    analyzeWorld: "studio:analyze-world",
    validateAssetReceipt: "studio:validate-asset-receipt",
    verifyAssetpack: "studio:verify-assetpack",
    runHeadless: "studio:run-headless",
    runReplay: "studio:run-replay",
    cancelJob: "studio:cancel-job",
    createExternalGrant: "studio:create-external-grant",
    getExternalGrant: "studio:get-external-grant",
    revokeExternalGrant: "studio:revoke-external-grant",
    materializeGame: "studio:materialize-game",
    packageGame: "studio:package-game",
    extractGamePackage: "studio:extract-game-package",
    getExternalJob: "studio:get-external-job",
    listExternalJobs: "studio:list-external-jobs",
    cancelExternalJob: "studio:cancel-external-job",
    recoverExternalJob: "studio:recover-external-job",
    event: "studio:event",
    codexStatus: "studio:codex-status",
    codexBindWorkspace: "studio:codex-bind-workspace",
    codexReadAccount: "studio:codex-read-account",
    codexStartLogin: "studio:codex-start-login",
    codexStartThread: "studio:codex-start-thread",
    codexResumeThread: "studio:codex-resume-thread",
    codexForkThread: "studio:codex-fork-thread",
    codexStartTurn: "studio:codex-start-turn",
    codexSteerTurn: "studio:codex-steer-turn",
    codexInterruptTurn: "studio:codex-interrupt-turn",
    codexAnswerUserInput: "studio:codex-answer-user-input",
    codexEvent: "studio:codex-event",
});

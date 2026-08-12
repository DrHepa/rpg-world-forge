import {
    IPC_CHANNELS,
    validateStudioCreationAssetAcceptanceResults,
    type ForgeStudioApi,
    type CodexActivityEvent,
    type StudioAssetCatalogInspectReply,
    type StudioAssetCatalogListReply,
    type StudioAssetPreviewChunkReply,
    type StudioAssetPreviewCloseReply,
    type StudioAssetPreviewOpenReply,
    type StudioAssetReceiptValidateReply,
    type StudioAssetpackVerifyReply,
    type StudioActivityEvent,
    type StudioClientResult,
    type StudioChangesetApplyReply,
    type StudioChangesetApproveReply,
    type StudioChangesetCreateReply,
    type StudioChangesetDiffReply,
    type StudioChangesetGetReply,
    type StudioChangesetRejectReply,
    type StudioJobCancelReply,
    type StudioReplyEnvelope,
    type StudioRuntimeHeadlessReply,
    type StudioRuntimeReplayReply,
    type StudioSourceListReply,
    type StudioSourceReadReply,
    type StudioV2ReplyEnvelope,
    type StudioV3ReplyEnvelope,
    type StudioV4ReplyEnvelope,
    type StudioV5ReplyEnvelope,
    type StudioWorkspaceOverviewReply,
    type StudioWorldAnalyzeReply,
    type StudioWorldValidateReply,
} from "../shared/studio-api";

export interface PreloadTransport {
    invoke(channel: string, ...args: unknown[]): Promise<unknown>;
    on(
        channel: string,
        listener: (event: unknown, payload: unknown) => void,
    ): void;
    removeListener(
        channel: string,
        listener: (event: unknown, payload: unknown) => void,
    ): void;
}

export function createStudioApi(transport: PreloadTransport): ForgeStudioApi {
    const api: ForgeStudioApi = {
        async initialize() {
            return asClientResult<StudioReplyEnvelope>(
                await transport.invoke(IPC_CHANNELS.initialize),
            );
        },
        async getServiceStatus() {
            return asClientResult(await transport.invoke(IPC_CHANNELS.status));
        },
        async listWorkspaces() {
            return asClientResult<StudioReplyEnvelope>(
                await transport.invoke(IPC_CHANNELS.listWorkspaces),
            );
        },
        async listEvents(params = {}) {
            return asClientResult<StudioReplyEnvelope>(
                await transport.invoke(IPC_CHANNELS.listEvents, params),
            );
        },
        async listChangesets(params = {}) {
            return asClientResult<StudioReplyEnvelope>(
                await transport.invoke(IPC_CHANNELS.listChangesets, params),
            );
        },
        async listJobs(params = {}) {
            return asClientResult<StudioReplyEnvelope>(
                await transport.invoke(IPC_CHANNELS.listJobs, params),
            );
        },
        async listCreationWorkspaces() {
            return asClientResult<StudioV3ReplyEnvelope>(
                await transport.invoke(IPC_CHANNELS.listCreationWorkspaces),
            );
        },
        async registerCreationProject() {
            return asClientResult<StudioV3ReplyEnvelope>(
                await transport.invoke(IPC_CHANNELS.registerCreationProject),
            );
        },
        async createCreationProject(params) {
            return asClientResult<StudioV3ReplyEnvelope>(
                await transport.invoke(
                    IPC_CHANNELS.createCreationProject,
                    params,
                ),
            );
        },
        async openCreationWorkspace(workspaceId) {
            return asClientResult<StudioV3ReplyEnvelope>(
                await transport.invoke(IPC_CHANNELS.openCreationWorkspace, {
                    workspaceId,
                }),
            );
        },
        async listCreationDocuments(workspaceId, expectedSourceRevision) {
            return asClientResult<StudioV3ReplyEnvelope>(
                await transport.invoke(IPC_CHANNELS.listCreationDocuments, {
                    workspaceId,
                    expectedSourceRevision,
                }),
            );
        },
        async readCreationDocument(workspaceId, expectedSourceRevision, path) {
            return asClientResult<StudioV3ReplyEnvelope>(
                await transport.invoke(IPC_CHANNELS.readCreationDocument, {
                    workspaceId,
                    expectedSourceRevision,
                    path,
                }),
            );
        },
        async getCreationWorkflow(workspaceId) {
            return asClientResult<StudioV3ReplyEnvelope>(
                await transport.invoke(IPC_CHANNELS.getCreationWorkflow, {
                    workspaceId,
                }),
            );
        },
        async inspectCreationReadiness(workspaceId) {
            return asClientResult<StudioV3ReplyEnvelope>(
                await transport.invoke(IPC_CHANNELS.inspectCreationReadiness, {
                    workspaceId,
                }),
            );
        },
        async listCreationArtifacts(params) {
            return asClientResult<StudioV4ReplyEnvelope>(
                await transport.invoke(
                    IPC_CHANNELS.listCreationArtifacts,
                    params,
                ),
            );
        },
        async inspectCreationArtifact(params) {
            return asClientResult<StudioV4ReplyEnvelope>(
                await transport.invoke(
                    IPC_CHANNELS.inspectCreationArtifact,
                    params,
                ),
            );
        },
        async inspectCreationEvidence(params) {
            return asClientResult<StudioV4ReplyEnvelope>(
                await transport.invoke(
                    IPC_CHANNELS.inspectCreationEvidence,
                    params,
                ),
            );
        },
        async openCreationPreview(params) {
            return asClientResult<StudioV4ReplyEnvelope>(
                await transport.invoke(
                    IPC_CHANNELS.openCreationPreview,
                    params,
                ),
            );
        },
        async readCreationPreviewChunk(handle, sequence) {
            return asClientResult<StudioV4ReplyEnvelope>(
                await transport.invoke(IPC_CHANNELS.readCreationPreviewChunk, {
                    handle,
                    sequence,
                }),
            );
        },
        async closeCreationPreview(handle) {
            return asClientResult<StudioV4ReplyEnvelope>(
                await transport.invoke(IPC_CHANNELS.closeCreationPreview, {
                    handle,
                }),
            );
        },
        async compileCreationProject(params) {
            return asClientResult<StudioV4ReplyEnvelope>(
                await transport.invoke(
                    IPC_CHANNELS.compileCreationProject,
                    params,
                ),
            );
        },
        async admitCreationArtifact(params) {
            return asClientResult<StudioV4ReplyEnvelope>(
                await transport.invoke(
                    IPC_CHANNELS.admitCreationArtifact,
                    params,
                ),
            );
        },
        async processCreationAsset(params) {
            const acceptanceResults =
                validateStudioCreationAssetAcceptanceResults(
                    params.acceptanceResults,
                );
            return asClientResult<StudioV4ReplyEnvelope>(
                await transport.invoke(
                    IPC_CHANNELS.processCreationAsset,
                    { ...params, acceptanceResults },
                ),
            );
        },
        async selectCreationAssetpackOutput(workspaceId) {
            return asClientResult<StudioV4ReplyEnvelope>(
                await transport.invoke(
                    IPC_CHANNELS.selectCreationAssetpackOutput,
                    {
                        workspaceId,
                    },
                ),
            );
        },
        async selectCreationRuntimeBundleOutput(workspaceId) {
            return asClientResult<StudioV4ReplyEnvelope>(
                await transport.invoke(
                    IPC_CHANNELS.selectCreationRuntimeBundleOutput,
                    {
                        workspaceId,
                    },
                ),
            );
        },
        async selectCreationMaterializationBundleOutput(workspaceId) {
            return asClientResult<StudioV4ReplyEnvelope>(
                await transport.invoke(
                    IPC_CHANNELS.selectCreationMaterializationBundleOutput,
                    {
                        workspaceId,
                    },
                ),
            );
        },
        async selectCreationStandaloneGameOutput(workspaceId) {
            return asClientResult<StudioV4ReplyEnvelope>(
                await transport.invoke(
                    IPC_CHANNELS.selectCreationStandaloneGameOutput,
                    {
                        workspaceId,
                    },
                ),
            );
        },
        async selectCreationGamePackageOutput(workspaceId) {
            return asClientResult<StudioV4ReplyEnvelope>(
                await transport.invoke(
                    IPC_CHANNELS.selectCreationGamePackageOutput,
                    {
                        workspaceId,
                    },
                ),
            );
        },
        async selectCreationGamePackageExtractionOutput(workspaceId) {
            return asClientResult<StudioV4ReplyEnvelope>(
                await transport.invoke(
                    IPC_CHANNELS.selectCreationGamePackageExtractionOutput,
                    { workspaceId },
                ),
            );
        },
        async getCreationAssetpackOutput(grantId) {
            return asClientResult<StudioV4ReplyEnvelope>(
                await transport.invoke(
                    IPC_CHANNELS.getCreationAssetpackOutput,
                    { grantId },
                ),
            );
        },
        async getCreationAuthorityCapabilities() {
            return asClientResult(
                await transport.invoke(
                    IPC_CHANNELS.getCreationAuthorityCapabilities,
                ),
            );
        },
        async listCreationOutputGrants(params) {
            return asClientResult<StudioV4ReplyEnvelope>(
                await transport.invoke(
                    IPC_CHANNELS.listCreationOutputGrants,
                    params,
                ),
            );
        },
        async listCreationAuthorityOutputGrants(params) {
            return asClientResult<StudioV5ReplyEnvelope>(
                await transport.invoke(
                    IPC_CHANNELS.listCreationAuthorityOutputGrants,
                    params,
                ),
            );
        },
        async revokeCreationAssetpackOutput(params) {
            return asClientResult<StudioV4ReplyEnvelope>(
                await transport.invoke(
                    IPC_CHANNELS.revokeCreationAssetpackOutput,
                    params,
                ),
            );
        },
        async sealCreationAssetRelease(params) {
            return asClientResult<StudioV4ReplyEnvelope>(
                await transport.invoke(
                    IPC_CHANNELS.sealCreationAssetRelease,
                    params,
                ),
            );
        },
        async composeCreationRuntime(params) {
            return asClientResult<StudioV4ReplyEnvelope>(
                await transport.invoke(
                    IPC_CHANNELS.composeCreationRuntime,
                    params,
                ),
            );
        },
        async buildCreationRuntimeBundle(params) {
            return asClientResult<StudioV4ReplyEnvelope>(
                await transport.invoke(
                    IPC_CHANNELS.buildCreationRuntimeBundle,
                    params,
                ),
            );
        },
        async buildCreationMaterializationBundle(params) {
            return asClientResult<StudioV4ReplyEnvelope>(
                await transport.invoke(
                    IPC_CHANNELS.buildCreationMaterializationBundle,
                    params,
                ),
            );
        },
        async materializeCreationGame(params) {
            return asClientResult<StudioV4ReplyEnvelope>(
                await transport.invoke(
                    IPC_CHANNELS.materializeCreationGame,
                    params,
                ),
            );
        },
        async packageCreationGame(params) {
            return asClientResult<StudioV4ReplyEnvelope>(
                await transport.invoke(
                    IPC_CHANNELS.packageCreationGame,
                    params,
                ),
            );
        },
        async extractCreationGamePackage(params) {
            return asClientResult<StudioV4ReplyEnvelope>(
                await transport.invoke(
                    IPC_CHANNELS.extractCreationGamePackage,
                    params,
                ),
            );
        },
        async reviewCreationAssetQa(params) {
            return asClientResult<StudioV5ReplyEnvelope>(
                await transport.invoke(
                    IPC_CHANNELS.reviewCreationAssetQa,
                    params,
                ),
            );
        },
        async authorizeCreationAssetRelease(params) {
            return asClientResult<StudioV5ReplyEnvelope>(
                await transport.invoke(
                    IPC_CHANNELS.authorizeCreationAssetRelease,
                    params,
                ),
            );
        },
        async selectCreationHeadlessEvidenceOutput(workspaceId) {
            return asClientResult<StudioV5ReplyEnvelope>(
                await transport.invoke(
                    IPC_CHANNELS.selectCreationHeadlessEvidenceOutput,
                    { workspaceId },
                ),
            );
        },
        async verifyCreationHeadless(params) {
            return asClientResult<StudioV5ReplyEnvelope>(
                await transport.invoke(
                    IPC_CHANNELS.verifyCreationHeadless,
                    params,
                ),
            );
        },
        async requestCreationJobCancel(params) {
            return asClientResult<StudioV5ReplyEnvelope>(
                await transport.invoke(
                    IPC_CHANNELS.requestCreationJobCancel,
                    params,
                ),
            );
        },
        async requestCreationJobRecovery(params) {
            return asClientResult<StudioV5ReplyEnvelope>(
                await transport.invoke(
                    IPC_CHANNELS.requestCreationJobRecovery,
                    params,
                ),
            );
        },
        async getCreationJob(jobId) {
            return asClientResult<StudioV4ReplyEnvelope>(
                await transport.invoke(IPC_CHANNELS.getCreationJob, { jobId }),
            );
        },
        async listCreationJobs(params) {
            return asClientResult<StudioV4ReplyEnvelope>(
                await transport.invoke(IPC_CHANNELS.listCreationJobs, params),
            );
        },
        async cancelCreationJob(params) {
            return asClientResult<StudioV4ReplyEnvelope>(
                await transport.invoke(IPC_CHANNELS.cancelCreationJob, params),
            );
        },
        async recoverCreationJob(params) {
            return asClientResult<StudioV4ReplyEnvelope>(
                await transport.invoke(IPC_CHANNELS.recoverCreationJob, params),
            );
        },
        async listCreationEvents(params) {
            return asClientResult<StudioV4ReplyEnvelope>(
                await transport.invoke(IPC_CHANNELS.listCreationEvents, params),
            );
        },
        async stageCreationProfile(params) {
            return asClientResult<StudioV3ReplyEnvelope>(
                await transport.invoke(
                    IPC_CHANNELS.stageCreationProfile,
                    params,
                ),
            );
        },
        async stageCreationModuleChange(params) {
            return asClientResult<StudioV3ReplyEnvelope>(
                await transport.invoke(
                    IPC_CHANNELS.stageCreationModuleChange,
                    params,
                ),
            );
        },
        async reconcileCreationWorkflow(params) {
            return asClientResult<StudioV3ReplyEnvelope>(
                await transport.invoke(
                    IPC_CHANNELS.reconcileCreationWorkflow,
                    params,
                ),
            );
        },
        async readCreationPhase(params) {
            return asClientResult<StudioV3ReplyEnvelope>(
                await transport.invoke(IPC_CHANNELS.readCreationPhase, params),
            );
        },
        async validateCreationPhase(params) {
            return asClientResult<StudioV3ReplyEnvelope>(
                await transport.invoke(
                    IPC_CHANNELS.validateCreationPhase,
                    params,
                ),
            );
        },
        async completeCreationPhase(params) {
            return asClientResult<StudioV3ReplyEnvelope>(
                await transport.invoke(
                    IPC_CHANNELS.completeCreationPhase,
                    params,
                ),
            );
        },
        async reopenCreationPhase(params) {
            return asClientResult<StudioV3ReplyEnvelope>(
                await transport.invoke(
                    IPC_CHANNELS.reopenCreationPhase,
                    params,
                ),
            );
        },
        async getCreationChangeset(changesetId) {
            return asClientResult<StudioV3ReplyEnvelope>(
                await transport.invoke(IPC_CHANNELS.getCreationChangeset, {
                    changesetId,
                }),
            );
        },
        async diffCreationChangeset(changesetId) {
            return asClientResult<StudioV3ReplyEnvelope>(
                await transport.invoke(IPC_CHANNELS.diffCreationChangeset, {
                    changesetId,
                }),
            );
        },
        async approveCreationChangeset(
            changesetId,
            expectedRecordHash,
            expectedReviewSha256,
        ) {
            return asClientResult<StudioV3ReplyEnvelope>(
                await transport.invoke(IPC_CHANNELS.approveCreationChangeset, {
                    changesetId,
                    expectedRecordHash,
                    expectedReviewSha256,
                }),
            );
        },
        async applyCreationChangeset(
            changesetId,
            expectedRecordHash,
            expectedReviewSha256,
            expectedRootGeneration,
        ) {
            return asClientResult<StudioV3ReplyEnvelope>(
                await transport.invoke(IPC_CHANNELS.applyCreationChangeset, {
                    changesetId,
                    expectedRecordHash,
                    expectedReviewSha256,
                    expectedRootGeneration,
                }),
            );
        },
        async recoverCreationChangeset(
            changesetId,
            mode,
            expectedRecordHash,
            expectedReviewSha256,
            expectedRootGeneration,
        ) {
            return asClientResult<StudioV3ReplyEnvelope>(
                await transport.invoke(IPC_CHANNELS.recoverCreationChangeset, {
                    changesetId,
                    mode,
                    expectedRecordHash,
                    expectedReviewSha256,
                    expectedRootGeneration,
                }),
            );
        },
        async getWorkspaceOverview(workspaceId) {
            return asClientResult<StudioWorkspaceOverviewReply>(
                await transport.invoke(IPC_CHANNELS.getWorkspaceOverview, {
                    workspaceId,
                }),
            );
        },
        async listSourceDocuments(workspaceId) {
            return asClientResult<StudioSourceListReply>(
                await transport.invoke(IPC_CHANNELS.listSourceDocuments, {
                    workspaceId,
                }),
            );
        },
        async readSourceDocument(workspaceId, path) {
            return asClientResult<StudioSourceReadReply>(
                await transport.invoke(IPC_CHANNELS.readSourceDocument, {
                    workspaceId,
                    path,
                }),
            );
        },
        async listAssetCatalog(workspaceId, page) {
            return asClientResult<StudioAssetCatalogListReply>(
                await transport.invoke(
                    IPC_CHANNELS.listAssetCatalog,
                    page === undefined
                        ? { workspaceId }
                        : {
                              workspaceId,
                              offset: page.offset,
                              expectedManifestRevision: page.manifestRevision,
                          },
                ),
            );
        },
        async inspectAssetCatalogEntry(workspaceId, manifestRevision, entryId) {
            return asClientResult<StudioAssetCatalogInspectReply>(
                await transport.invoke(IPC_CHANNELS.inspectAssetCatalogEntry, {
                    workspaceId,
                    manifestRevision,
                    entryId,
                }),
            );
        },
        async openAssetPreview(workspaceId, manifestRevision, entryId) {
            return asClientResult<StudioAssetPreviewOpenReply>(
                await transport.invoke(IPC_CHANNELS.openAssetPreview, {
                    workspaceId,
                    manifestRevision,
                    entryId,
                }),
            );
        },
        async readAssetPreviewChunk(handle, sequence) {
            return asClientResult<StudioAssetPreviewChunkReply>(
                await transport.invoke(IPC_CHANNELS.readAssetPreviewChunk, {
                    handle,
                    sequence,
                }),
            );
        },
        async closeAssetPreview(handle) {
            return asClientResult<StudioAssetPreviewCloseReply>(
                await transport.invoke(IPC_CHANNELS.closeAssetPreview, {
                    handle,
                }),
            );
        },
        async stageSourceDocument(workspaceId, path, baseSha256, content) {
            return asClientResult<StudioChangesetCreateReply>(
                await transport.invoke(IPC_CHANNELS.stageSourceDocument, {
                    workspaceId,
                    path,
                    baseSha256,
                    content,
                }),
            );
        },
        async getChangeset(changesetId) {
            return asClientResult<StudioChangesetGetReply>(
                await transport.invoke(IPC_CHANNELS.getChangeset, {
                    changesetId,
                }),
            );
        },
        async readChangesetDiff(changesetId) {
            return asClientResult<StudioChangesetDiffReply>(
                await transport.invoke(IPC_CHANNELS.readChangesetDiff, {
                    changesetId,
                }),
            );
        },
        async approveChangeset(changesetId, expectedReviewSha256) {
            return asClientResult<StudioChangesetApproveReply>(
                await transport.invoke(IPC_CHANNELS.approveChangeset, {
                    changesetId,
                    ...(expectedReviewSha256 === undefined
                        ? {}
                        : { expectedReviewSha256 }),
                }),
            );
        },
        async rejectChangeset(changesetId, expectedReviewSha256) {
            return asClientResult<StudioChangesetRejectReply>(
                await transport.invoke(IPC_CHANNELS.rejectChangeset, {
                    changesetId,
                    ...(expectedReviewSha256 === undefined
                        ? {}
                        : { expectedReviewSha256 }),
                }),
            );
        },
        async applyChangeset(changesetId, expectedReviewSha256) {
            return asClientResult<StudioChangesetApplyReply>(
                await transport.invoke(IPC_CHANNELS.applyChangeset, {
                    changesetId,
                    ...(expectedReviewSha256 === undefined
                        ? {}
                        : { expectedReviewSha256 }),
                }),
            );
        },
        async validateWorld(workspaceId) {
            return asClientResult<StudioWorldValidateReply>(
                await transport.invoke(IPC_CHANNELS.validateWorld, {
                    workspaceId,
                }),
            );
        },
        async analyzeWorld(workspaceId) {
            return asClientResult<StudioWorldAnalyzeReply>(
                await transport.invoke(IPC_CHANNELS.analyzeWorld, {
                    workspaceId,
                }),
            );
        },
        async validateAssetReceipt(workspaceId, input) {
            return asClientResult<StudioAssetReceiptValidateReply>(
                await transport.invoke(IPC_CHANNELS.validateAssetReceipt, {
                    workspaceId,
                    input,
                }),
            );
        },
        async verifyAssetpack(workspaceId, input) {
            return asClientResult<StudioAssetpackVerifyReply>(
                await transport.invoke(IPC_CHANNELS.verifyAssetpack, {
                    workspaceId,
                    input,
                }),
            );
        },
        async runHeadless(workspaceId, input) {
            return asClientResult<StudioRuntimeHeadlessReply>(
                await transport.invoke(IPC_CHANNELS.runHeadless, {
                    workspaceId,
                    input,
                }),
            );
        },
        async runReplay(workspaceId, input) {
            return asClientResult<StudioRuntimeReplayReply>(
                await transport.invoke(IPC_CHANNELS.runReplay, {
                    workspaceId,
                    input,
                }),
            );
        },
        async cancelJob(jobId) {
            return asClientResult<StudioJobCancelReply>(
                await transport.invoke(IPC_CHANNELS.cancelJob, { jobId }),
            );
        },
        async createExternalGrant(params) {
            return asClientResult<StudioV2ReplyEnvelope>(
                await transport.invoke(
                    IPC_CHANNELS.createExternalGrant,
                    params,
                ),
            );
        },
        async getExternalGrant(grantId) {
            return asClientResult<StudioV2ReplyEnvelope>(
                await transport.invoke(IPC_CHANNELS.getExternalGrant, {
                    grantId,
                }),
            );
        },
        async revokeExternalGrant(grantId) {
            return asClientResult<StudioV2ReplyEnvelope>(
                await transport.invoke(IPC_CHANNELS.revokeExternalGrant, {
                    grantId,
                }),
            );
        },
        async materializeGame(params) {
            return asClientResult<StudioV2ReplyEnvelope>(
                await transport.invoke(IPC_CHANNELS.materializeGame, params),
            );
        },
        async packageGame(params) {
            return asClientResult<StudioV2ReplyEnvelope>(
                await transport.invoke(IPC_CHANNELS.packageGame, params),
            );
        },
        async extractGamePackage(params) {
            return asClientResult<StudioV2ReplyEnvelope>(
                await transport.invoke(IPC_CHANNELS.extractGamePackage, params),
            );
        },
        async getExternalJob(jobId) {
            return asClientResult<StudioV2ReplyEnvelope>(
                await transport.invoke(IPC_CHANNELS.getExternalJob, { jobId }),
            );
        },
        async listExternalJobs(params = {}) {
            return asClientResult<StudioV2ReplyEnvelope>(
                await transport.invoke(IPC_CHANNELS.listExternalJobs, params),
            );
        },
        async cancelExternalJob(jobId) {
            return asClientResult<StudioV2ReplyEnvelope>(
                await transport.invoke(IPC_CHANNELS.cancelExternalJob, {
                    jobId,
                }),
            );
        },
        async recoverExternalJob(jobId, action) {
            return asClientResult<StudioV2ReplyEnvelope>(
                await transport.invoke(IPC_CHANNELS.recoverExternalJob, {
                    jobId,
                    action,
                }),
            );
        },
        onEvent(listener: (event: StudioActivityEvent) => void) {
            if (typeof listener !== "function") {
                throw new TypeError("Studio event listener must be a function");
            }
            const wrapped = (_event: unknown, payload: unknown): void => {
                if (isActivityEvent(payload)) {
                    listener(payload);
                }
            };
            transport.on(IPC_CHANNELS.event, wrapped);
            return () => transport.removeListener(IPC_CHANNELS.event, wrapped);
        },
        async getCodexStatus() {
            return asClientResult(
                await transport.invoke(IPC_CHANNELS.codexStatus),
            );
        },
        async bindCodexWorkspace(workspaceId) {
            return asClientResult(
                await transport.invoke(IPC_CHANNELS.codexBindWorkspace, {
                    workspaceId,
                }),
            );
        },
        async readCodexAccount() {
            return asClientResult(
                await transport.invoke(IPC_CHANNELS.codexReadAccount),
            );
        },
        async startCodexLogin(mode) {
            return asClientResult(
                await transport.invoke(IPC_CHANNELS.codexStartLogin, { mode }),
            );
        },
        async startCodexThread() {
            return asClientResult(
                await transport.invoke(IPC_CHANNELS.codexStartThread),
            );
        },
        async resumeCodexThread(threadId) {
            return asClientResult(
                await transport.invoke(IPC_CHANNELS.codexResumeThread, {
                    threadId,
                }),
            );
        },
        async forkCodexThread(threadId) {
            return asClientResult(
                await transport.invoke(IPC_CHANNELS.codexForkThread, {
                    threadId,
                }),
            );
        },
        async startCodexTurn(threadId, text) {
            return asClientResult(
                await transport.invoke(IPC_CHANNELS.codexStartTurn, {
                    threadId,
                    text,
                }),
            );
        },
        async steerCodexTurn(threadId, turnId, text) {
            return asClientResult(
                await transport.invoke(IPC_CHANNELS.codexSteerTurn, {
                    threadId,
                    turnId,
                    text,
                }),
            );
        },
        async interruptCodexTurn(threadId, turnId) {
            return asClientResult(
                await transport.invoke(IPC_CHANNELS.codexInterruptTurn, {
                    threadId,
                    turnId,
                }),
            );
        },
        async answerCodexUserInput(token, answers) {
            return asClientResult(
                await transport.invoke(IPC_CHANNELS.codexAnswerUserInput, {
                    token,
                    answers,
                }),
            );
        },
        onCodexEvent(listener: (event: CodexActivityEvent) => void) {
            if (typeof listener !== "function") {
                throw new TypeError("Codex event listener must be a function");
            }
            const wrapped = (_event: unknown, payload: unknown): void => {
                if (isCodexActivityEvent(payload)) listener(payload);
            };
            transport.on(IPC_CHANNELS.codexEvent, wrapped);
            return () =>
                transport.removeListener(IPC_CHANNELS.codexEvent, wrapped);
        },
    };
    return Object.freeze(api);
}

function isCodexActivityEvent(value: unknown): value is CodexActivityEvent {
    if (typeof value !== "object" || value === null || !("type" in value))
        return false;
    const type = (value as { type?: unknown }).type;
    return (
        type === "codex-status" ||
        type === "codex-stderr" ||
        type === "codex-notification" ||
        type === "codex-user-input"
    );
}

function asClientResult<T>(value: unknown): StudioClientResult<T> {
    if (
        typeof value !== "object" ||
        value === null ||
        !("ok" in value) ||
        typeof (value as { ok?: unknown }).ok !== "boolean"
    ) {
        return {
            ok: false,
            error: {
                code: "internal_error",
                message: "Main process returned an invalid Studio result",
            },
        };
    }
    return value as StudioClientResult<T>;
}

function isActivityEvent(value: unknown): value is StudioActivityEvent {
    if (typeof value !== "object" || value === null || !("type" in value)) {
        return false;
    }
    const type = (value as { type?: unknown }).type;
    return (
        type === "service-status" ||
        type === "studio-event" ||
        type === "service-stderr"
    );
}

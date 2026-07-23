import type {
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

export type {
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

export type StudioReplyEnvelope = StudioResponseEnvelope | StudioErrorEnvelope;
export type StudioWorkspaceOverviewReply =
  | StudioWorkspaceOverviewResponse
  | StudioErrorEnvelope;
export type StudioSourceListReply = StudioSourceListResponse | StudioErrorEnvelope;
export type StudioSourceReadReply = StudioSourceReadResponse | StudioErrorEnvelope;
export type StudioWorldValidateReply = StudioWorldValidateResponse | StudioErrorEnvelope;
export type StudioWorldAnalyzeReply = StudioWorldAnalyzeResponse | StudioErrorEnvelope;
export type StudioJobCreateReply = StudioJobCreateResponse | StudioErrorEnvelope;
export type StudioJobCancelReply = StudioJobCancelResponse | StudioErrorEnvelope;
export type StudioChangesetCreateReply = StudioChangesetCreateResponse | StudioErrorEnvelope;
export type StudioChangesetGetReply = StudioChangesetGetResponse | StudioErrorEnvelope;
export type StudioChangesetDiffReply = StudioChangesetDiffResponse | StudioErrorEnvelope;
export type StudioChangesetApproveReply = StudioChangesetApproveResponse | StudioErrorEnvelope;
export type StudioChangesetRejectReply = StudioChangesetRejectResponse | StudioErrorEnvelope;
export type StudioChangesetApplyReply = StudioChangesetApplyResponse | StudioErrorEnvelope;

export type StudioAssetReceiptValidateJob = StudioManagedJobV2CommonRecord &
  StudioAssetReceiptValidateOperation;
export type StudioAssetpackVerifyJob = StudioManagedJobV2CommonRecord &
  StudioAssetpackVerifyOperation;
export type StudioRuntimeHeadlessJob = StudioManagedJobV2CommonRecord &
  StudioRuntimeHeadlessOperation;
export type StudioRuntimeReplayJob = StudioManagedJobV2CommonRecord &
  StudioRuntimeReplayOperation;

type StudioJobCreateResponseWithJob<TJob> = Omit<StudioJobCreateResponse, "result"> & {
  result: { job: TJob };
};

export type StudioAssetReceiptValidateResponse = StudioJobCreateResponseWithJob<
  StudioAssetReceiptValidateJob
>;
export type StudioAssetpackVerifyResponse = StudioJobCreateResponseWithJob<
  StudioAssetpackVerifyJob
>;
export type StudioRuntimeHeadlessResponse = StudioJobCreateResponseWithJob<
  StudioRuntimeHeadlessJob
>;
export type StudioRuntimeReplayResponse = StudioJobCreateResponseWithJob<StudioRuntimeReplayJob>;

export type StudioAssetReceiptValidateReply =
  | StudioAssetReceiptValidateResponse
  | StudioErrorEnvelope;
export type StudioAssetpackVerifyReply = StudioAssetpackVerifyResponse | StudioErrorEnvelope;
export type StudioRuntimeHeadlessReply = StudioRuntimeHeadlessResponse | StudioErrorEnvelope;
export type StudioRuntimeReplayReply = StudioRuntimeReplayResponse | StudioErrorEnvelope;

export type ForgeServiceState =
  | "stopped"
  | "starting"
  | "ready"
  | "unavailable"
  | "crashed";

export interface ForgeServiceStatus {
  state: ForgeServiceState;
  message: string;
  pid: number | null;
}

export type StudioReadMethod =
  | "workspace.list"
  | "events.list"
  | "changeset.list"
  | "job.list";

export type StudioCapabilityMethod =
  | StudioReadMethod
  | "workspace.overview"
  | "source.list"
  | "source.read"
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

export type CodexBridgeState = "unbound" | "starting" | "ready" | "unavailable" | "crashed";

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
  | { type: "codex-notification"; method: string; params: unknown; authoritative: boolean }
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
  account: null | { type: "apiKey" } | { type: "chatgpt"; email: string | null; planType: string };
}

export type CodexLoginStart =
  | { type: "chatgpt"; loginId: string; authUrl: string }
  | { type: "chatgptDeviceCode"; loginId: string; verificationUrl: string; userCode: string };

export interface CodexThreadSummary {
  threadId: string;
}

export interface CodexTurnSummary {
  turnId: string;
  status: string;
}

export interface StudioClientError {
  code: "invalid_request" | "service_unavailable" | "timeout" | "cancelled" | "internal_error";
  message: string;
}

export type StudioClientResult<T> =
  | { ok: true; value: T }
  | { ok: false; error: StudioClientError };

export interface ForgeStudioApi {
  initialize(): Promise<StudioClientResult<StudioReplyEnvelope>>;
  getServiceStatus(): Promise<StudioClientResult<ForgeServiceStatus>>;
  listWorkspaces(): Promise<StudioClientResult<StudioReplyEnvelope>>;
  listEvents(params?: EventsListParams): Promise<StudioClientResult<StudioReplyEnvelope>>;
  listChangesets(params?: ChangesetsListParams): Promise<StudioClientResult<StudioReplyEnvelope>>;
  listJobs(params?: JobsListParams): Promise<StudioClientResult<StudioReplyEnvelope>>;
  getWorkspaceOverview(
    workspaceId: string,
  ): Promise<StudioClientResult<StudioWorkspaceOverviewReply>>;
  listSourceDocuments(workspaceId: string): Promise<StudioClientResult<StudioSourceListReply>>;
  readSourceDocument(
    workspaceId: string,
    path: string,
  ): Promise<StudioClientResult<StudioSourceReadReply>>;
  stageSourceDocument(
    workspaceId: string,
    path: string,
    baseSha256: string,
    content: string,
  ): Promise<StudioClientResult<StudioChangesetCreateReply>>;
  getChangeset(changesetId: string): Promise<StudioClientResult<StudioChangesetGetReply>>;
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
  validateWorld(workspaceId: string): Promise<StudioClientResult<StudioWorldValidateReply>>;
  analyzeWorld(workspaceId: string): Promise<StudioClientResult<StudioWorldAnalyzeReply>>;
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
  onEvent(listener: (event: StudioActivityEvent) => void): () => void;
  getCodexStatus(): Promise<StudioClientResult<CodexBridgeStatus>>;
  bindCodexWorkspace(workspaceId: string): Promise<StudioClientResult<CodexBridgeStatus>>;
  readCodexAccount(): Promise<StudioClientResult<CodexAccountSummary>>;
  startCodexLogin(mode: CodexLoginMode): Promise<StudioClientResult<CodexLoginStart>>;
  startCodexThread(): Promise<StudioClientResult<CodexThreadSummary>>;
  resumeCodexThread(threadId: string): Promise<StudioClientResult<CodexThreadSummary>>;
  forkCodexThread(threadId: string): Promise<StudioClientResult<CodexThreadSummary>>;
  startCodexTurn(threadId: string, text: string): Promise<StudioClientResult<CodexTurnSummary>>;
  steerCodexTurn(threadId: string, turnId: string, text: string): Promise<StudioClientResult<void>>;
  interruptCodexTurn(threadId: string, turnId: string): Promise<StudioClientResult<void>>;
  answerCodexUserInput(
    token: string,
    answers: Record<string, string[]>,
  ): Promise<StudioClientResult<void>>;
  onCodexEvent(listener: (event: CodexActivityEvent) => void): () => void;
}

export const STUDIO_METHODS: ReadonlySet<StudioMethod> = new Set([
  "service.initialize",
  "workspace.register",
  "workspace.list",
  "workspace.get",
  "workspace.overview",
  "source.list",
  "source.read",
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
  getWorkspaceOverview: "studio:get-workspace-overview",
  listSourceDocuments: "studio:list-source-documents",
  readSourceDocument: "studio:read-source-document",
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

import type {
  Error as StudioErrorEnvelope,
  Event as StudioEventEnvelope,
  Method as StudioMethod,
  Response as StudioResponseEnvelope,
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
  StudioMethod,
  StudioResponseEnvelope,
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

export interface EventsListParams {
  workspace_id?: string;
  after_id?: number;
  limit?: number;
}

export interface ChangesetsListParams {
  workspace_id?: string;
  status?: "staged" | "approved" | "rejected" | "applied";
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

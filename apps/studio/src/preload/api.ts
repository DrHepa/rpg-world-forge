import {
  IPC_CHANNELS,
  type ForgeStudioApi,
  type CodexActivityEvent,
  type StudioAssetCatalogInspectReply,
  type StudioAssetCatalogListReply,
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
  type StudioWorkspaceOverviewReply,
  type StudioWorldAnalyzeReply,
  type StudioWorldValidateReply,
} from "../shared/studio-api";

export interface PreloadTransport {
  invoke(channel: string, ...args: unknown[]): Promise<unknown>;
  on(channel: string, listener: (event: unknown, payload: unknown) => void): void;
  removeListener(channel: string, listener: (event: unknown, payload: unknown) => void): void;
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
    async getWorkspaceOverview(workspaceId) {
      return asClientResult<StudioWorkspaceOverviewReply>(
        await transport.invoke(IPC_CHANNELS.getWorkspaceOverview, { workspaceId }),
      );
    },
    async listSourceDocuments(workspaceId) {
      return asClientResult<StudioSourceListReply>(
        await transport.invoke(IPC_CHANNELS.listSourceDocuments, { workspaceId }),
      );
    },
    async readSourceDocument(workspaceId, path) {
      return asClientResult<StudioSourceReadReply>(
        await transport.invoke(IPC_CHANNELS.readSourceDocument, { workspaceId, path }),
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
        await transport.invoke(IPC_CHANNELS.getChangeset, { changesetId }),
      );
    },
    async readChangesetDiff(changesetId) {
      return asClientResult<StudioChangesetDiffReply>(
        await transport.invoke(IPC_CHANNELS.readChangesetDiff, { changesetId }),
      );
    },
    async approveChangeset(changesetId, expectedReviewSha256) {
      return asClientResult<StudioChangesetApproveReply>(
        await transport.invoke(IPC_CHANNELS.approveChangeset, {
          changesetId,
          ...(expectedReviewSha256 === undefined ? {} : { expectedReviewSha256 }),
        }),
      );
    },
    async rejectChangeset(changesetId, expectedReviewSha256) {
      return asClientResult<StudioChangesetRejectReply>(
        await transport.invoke(IPC_CHANNELS.rejectChangeset, {
          changesetId,
          ...(expectedReviewSha256 === undefined ? {} : { expectedReviewSha256 }),
        }),
      );
    },
    async applyChangeset(changesetId, expectedReviewSha256) {
      return asClientResult<StudioChangesetApplyReply>(
        await transport.invoke(IPC_CHANNELS.applyChangeset, {
          changesetId,
          ...(expectedReviewSha256 === undefined ? {} : { expectedReviewSha256 }),
        }),
      );
    },
    async validateWorld(workspaceId) {
      return asClientResult<StudioWorldValidateReply>(
        await transport.invoke(IPC_CHANNELS.validateWorld, { workspaceId }),
      );
    },
    async analyzeWorld(workspaceId) {
      return asClientResult<StudioWorldAnalyzeReply>(
        await transport.invoke(IPC_CHANNELS.analyzeWorld, { workspaceId }),
      );
    },
    async validateAssetReceipt(workspaceId, input) {
      return asClientResult<StudioAssetReceiptValidateReply>(
        await transport.invoke(IPC_CHANNELS.validateAssetReceipt, { workspaceId, input }),
      );
    },
    async verifyAssetpack(workspaceId, input) {
      return asClientResult<StudioAssetpackVerifyReply>(
        await transport.invoke(IPC_CHANNELS.verifyAssetpack, { workspaceId, input }),
      );
    },
    async runHeadless(workspaceId, input) {
      return asClientResult<StudioRuntimeHeadlessReply>(
        await transport.invoke(IPC_CHANNELS.runHeadless, { workspaceId, input }),
      );
    },
    async runReplay(workspaceId, input) {
      return asClientResult<StudioRuntimeReplayReply>(
        await transport.invoke(IPC_CHANNELS.runReplay, { workspaceId, input }),
      );
    },
    async cancelJob(jobId) {
      return asClientResult<StudioJobCancelReply>(
        await transport.invoke(IPC_CHANNELS.cancelJob, { jobId }),
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
      return asClientResult(await transport.invoke(IPC_CHANNELS.codexStatus));
    },
    async bindCodexWorkspace(workspaceId) {
      return asClientResult(await transport.invoke(IPC_CHANNELS.codexBindWorkspace, { workspaceId }));
    },
    async readCodexAccount() {
      return asClientResult(await transport.invoke(IPC_CHANNELS.codexReadAccount));
    },
    async startCodexLogin(mode) {
      return asClientResult(await transport.invoke(IPC_CHANNELS.codexStartLogin, { mode }));
    },
    async startCodexThread() {
      return asClientResult(await transport.invoke(IPC_CHANNELS.codexStartThread));
    },
    async resumeCodexThread(threadId) {
      return asClientResult(await transport.invoke(IPC_CHANNELS.codexResumeThread, { threadId }));
    },
    async forkCodexThread(threadId) {
      return asClientResult(await transport.invoke(IPC_CHANNELS.codexForkThread, { threadId }));
    },
    async startCodexTurn(threadId, text) {
      return asClientResult(await transport.invoke(IPC_CHANNELS.codexStartTurn, { threadId, text }));
    },
    async steerCodexTurn(threadId, turnId, text) {
      return asClientResult(
        await transport.invoke(IPC_CHANNELS.codexSteerTurn, { threadId, turnId, text }),
      );
    },
    async interruptCodexTurn(threadId, turnId) {
      return asClientResult(
        await transport.invoke(IPC_CHANNELS.codexInterruptTurn, { threadId, turnId }),
      );
    },
    async answerCodexUserInput(token, answers) {
      return asClientResult(
        await transport.invoke(IPC_CHANNELS.codexAnswerUserInput, { token, answers }),
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
      return () => transport.removeListener(IPC_CHANNELS.codexEvent, wrapped);
    },
  };
  return Object.freeze(api);
}

function isCodexActivityEvent(value: unknown): value is CodexActivityEvent {
  if (typeof value !== "object" || value === null || !("type" in value)) return false;
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
      error: { code: "internal_error", message: "Main process returned an invalid Studio result" },
    };
  }
  return value as StudioClientResult<T>;
}

function isActivityEvent(value: unknown): value is StudioActivityEvent {
  if (typeof value !== "object" || value === null || !("type" in value)) {
    return false;
  }
  const type = (value as { type?: unknown }).type;
  return type === "service-status" || type === "studio-event" || type === "service-stderr";
}

import { createHash, randomUUID } from "node:crypto";

import type { BrowserWindow, IpcMain, IpcMainInvokeEvent } from "electron";

import {
  IPC_CHANNELS,
  type ChangesetsListParams,
  type EventsListParams,
  type ForgeServiceStatus,
  type JobsListParams,
  type StudioActivityEvent,
  type StudioCapabilityMethod,
  type StudioClientError,
  type StudioClientResult,
  type StudioReadMethod,
  type StudioReplyEnvelope,
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
  isPortableRelativePath,
  isPortableSourcePath,
  validateStudioEnvelope,
} from "./protocol-validator";
import { isTrustedStudioSender } from "./security";

const DEFAULT_REQUEST_TIMEOUT_MS = 10_000;
const WORKSPACE_ID_PATTERN = /^[a-z][a-z0-9_-]{1,63}$/u;
const JOB_ID_PATTERN = /^[a-z0-9][a-z0-9_-]{0,127}$/u;
const CHANGESET_ID_PATTERN = JOB_ID_PATTERN;
const SHA256_PATTERN = /^[0-9a-f]{64}$/u;
const MAX_SOURCE_DOCUMENT_BYTES = 256 * 1024;
const MAX_RUNTIME_TICKS = 1_000_000;
type NamedJobOperation =
  | "asset.receipt.validate"
  | "assetpack.verify"
  | "runtime.headless"
  | "runtime.replay";
type ChangesetActionMethod =
  | "changeset.approve"
  | "changeset.reject"
  | "changeset.apply";
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

export function registerStudioIpc(
  ipcMain: IpcMain,
  window: BrowserWindow,
  service: ForgeServiceClient,
  codex: CodexBridgeClient,
): () => void {
  const trusted = (event: IpcMainInvokeEvent): boolean =>
    isTrustedStudioSender(event, window.webContents);

  ipcMain.handle(IPC_CHANNELS.initialize, async (event, ...args: unknown[]) => {
    const invalid = rejectUntrustedOrUnexpectedArguments(trusted(event), args);
    return invalid ?? await capture(() => service.initialize());
  });

  ipcMain.handle(IPC_CHANNELS.status, (event, ...args: unknown[]) => {
    const invalid = rejectUntrustedOrUnexpectedArguments(trusted(event), args);
    return invalid ?? success(service.status);
  });

  ipcMain.handle(IPC_CHANNELS.listWorkspaces, async (event, ...args: unknown[]) => {
    const invalid = rejectUntrustedOrUnexpectedArguments(trusted(event), args);
    return invalid ?? await requestRead(service, "workspace.list", {});
  });

  ipcMain.handle(IPC_CHANNELS.listEvents, async (event, value: unknown = {}) => {
    if (!trusted(event)) {
      return failure("invalid_request", "Rejected Studio IPC from an untrusted sender");
    }
    return await captureValidated(
      () => validateEventsListParams(value),
      (params) => requestRead(service, "events.list", { ...params }),
    );
  });

  ipcMain.handle(IPC_CHANNELS.listChangesets, async (event, value: unknown = {}) => {
    if (!trusted(event)) {
      return failure("invalid_request", "Rejected Studio IPC from an untrusted sender");
    }
    return await captureValidated(
      () => validateChangesetsListParams(value),
      (params) => requestRead(service, "changeset.list", { ...params }),
    );
  });

  ipcMain.handle(IPC_CHANNELS.listJobs, async (event, value: unknown = {}) => {
    if (!trusted(event)) {
      return failure("invalid_request", "Rejected Studio IPC from an untrusted sender");
    }
    return await captureValidated(
      () => validateJobsListParams(value),
      (params) => requestRead(service, "job.list", { ...params }),
    );
  });

  ipcMain.handle(IPC_CHANNELS.getWorkspaceOverview, async (event, ...args: unknown[]) => {
    if (!trusted(event)) return untrustedFailure();
    return await captureValidated(
      () => validateSingleArgument(args, validateWorkspaceArgument),
      ({ workspaceId }) =>
        requestNamed(service, "workspace.overview", { workspace_id: workspaceId }),
    );
  });

  ipcMain.handle(IPC_CHANNELS.listSourceDocuments, async (event, ...args: unknown[]) => {
    if (!trusted(event)) return untrustedFailure();
    return await captureValidated(
      () => validateSingleArgument(args, validateWorkspaceArgument),
      ({ workspaceId }) => requestNamed(service, "source.list", { workspace_id: workspaceId }),
    );
  });

  ipcMain.handle(IPC_CHANNELS.readSourceDocument, async (event, ...args: unknown[]) => {
    if (!trusted(event)) return untrustedFailure();
    return await captureValidated(
      () => validateSingleArgument(args, validateSourceReadArgument),
      ({ workspaceId, path }) =>
        requestNamed(service, "source.read", { workspace_id: workspaceId, path }),
    );
  });

  ipcMain.handle(IPC_CHANNELS.stageSourceDocument, async (event, ...args: unknown[]) => {
    if (!trusted(event)) return untrustedFailure();
    return await captureValidated(
      () => validateSingleArgument(args, validateStageSourceDocumentArgument),
      ({ workspaceId, path, baseSha256, content }) =>
        requestStageSourceDocument(service, workspaceId, path, baseSha256, content),
    );
  });

  ipcMain.handle(IPC_CHANNELS.getChangeset, async (event, ...args: unknown[]) => {
    if (!trusted(event)) return untrustedFailure();
    return await captureValidated(
      () => validateSingleArgument(args, validateChangesetIdArgument),
      ({ changesetId }) => requestChangesetGet(service, changesetId),
    );
  });

  ipcMain.handle(IPC_CHANNELS.readChangesetDiff, async (event, ...args: unknown[]) => {
    if (!trusted(event)) return untrustedFailure();
    return await captureValidated(
      () => validateSingleArgument(args, validateChangesetIdArgument),
      ({ changesetId }) => requestChangesetDiff(service, changesetId),
    );
  });

  ipcMain.handle(IPC_CHANNELS.approveChangeset, async (event, ...args: unknown[]) => {
    if (!trusted(event)) return untrustedFailure();
    return await captureValidated(
      () => validateSingleArgument(args, validateChangesetActionArgument),
      ({ changesetId, expectedReviewSha256 }) =>
        requestChangesetAction(
          service,
          "changeset.approve",
          "approved",
          changesetId,
          expectedReviewSha256,
        ),
    );
  });

  ipcMain.handle(IPC_CHANNELS.rejectChangeset, async (event, ...args: unknown[]) => {
    if (!trusted(event)) return untrustedFailure();
    return await captureValidated(
      () => validateSingleArgument(args, validateChangesetActionArgument),
      ({ changesetId, expectedReviewSha256 }) =>
        requestChangesetAction(
          service,
          "changeset.reject",
          "rejected",
          changesetId,
          expectedReviewSha256,
        ),
    );
  });

  ipcMain.handle(IPC_CHANNELS.applyChangeset, async (event, ...args: unknown[]) => {
    if (!trusted(event)) return untrustedFailure();
    return await captureValidated(
      () => validateSingleArgument(args, validateChangesetActionArgument),
      ({ changesetId, expectedReviewSha256 }) =>
        requestChangesetAction(
          service,
          "changeset.apply",
          "applied",
          changesetId,
          expectedReviewSha256,
        ),
    );
  });

  ipcMain.handle(IPC_CHANNELS.validateWorld, async (event, ...args: unknown[]) => {
    if (!trusted(event)) return untrustedFailure();
    return await captureValidated(
      () => validateSingleArgument(args, validateWorkspaceArgument),
      ({ workspaceId }) => requestNamed(service, "world.validate", { workspace_id: workspaceId }),
    );
  });

  ipcMain.handle(IPC_CHANNELS.analyzeWorld, async (event, ...args: unknown[]) => {
    if (!trusted(event)) return untrustedFailure();
    return await captureValidated(
      () => validateSingleArgument(args, validateWorkspaceArgument),
      ({ workspaceId }) => requestNamed(service, "world.analyze", { workspace_id: workspaceId }),
    );
  });

  ipcMain.handle(IPC_CHANNELS.validateAssetReceipt, async (event, ...args: unknown[]) => {
    if (!trusted(event)) return untrustedFailure();
    return await captureValidated(
      () => validateSingleArgument(args, validateAssetReceiptArgument),
      ({ workspaceId, input }) =>
        requestJobCreate(service, workspaceId, "asset.receipt.validate", input),
    );
  });

  ipcMain.handle(IPC_CHANNELS.verifyAssetpack, async (event, ...args: unknown[]) => {
    if (!trusted(event)) return untrustedFailure();
    return await captureValidated(
      () => validateSingleArgument(args, validateAssetpackArgument),
      ({ workspaceId, input }) =>
        requestJobCreate(service, workspaceId, "assetpack.verify", input),
    );
  });

  ipcMain.handle(IPC_CHANNELS.runHeadless, async (event, ...args: unknown[]) => {
    if (!trusted(event)) return untrustedFailure();
    return await captureValidated(
      () => validateSingleArgument(args, validateHeadlessArgument),
      ({ workspaceId, input }) =>
        requestJobCreate(service, workspaceId, "runtime.headless", input),
    );
  });

  ipcMain.handle(IPC_CHANNELS.runReplay, async (event, ...args: unknown[]) => {
    if (!trusted(event)) return untrustedFailure();
    return await captureValidated(
      () => validateSingleArgument(args, validateReplayArgument),
      ({ workspaceId, input }) =>
        requestJobCreate(service, workspaceId, "runtime.replay", input),
    );
  });

  ipcMain.handle(IPC_CHANNELS.cancelJob, async (event, ...args: unknown[]) => {
    if (!trusted(event)) return untrustedFailure();
    return await captureValidated(
      () => validateSingleArgument(args, validateCancelJobArgument),
      ({ jobId }) => requestNamed(service, "job.cancel", { job_id: jobId }),
    );
  });

  ipcMain.handle(IPC_CHANNELS.codexStatus, (event, ...args: unknown[]) => {
    const invalid = rejectUntrustedOrUnexpectedArguments(trusted(event), args);
    return invalid ?? success(codex.status);
  });
  ipcMain.handle(IPC_CHANNELS.codexBindWorkspace, async (event, value: unknown) =>
    trusted(event)
      ? await captureValidated(() => validateWorkspaceArgument(value), (params) => capture(() => codex.bindWorkspace(params.workspaceId)))
      : failure("invalid_request", "Rejected Studio IPC from an untrusted sender"),
  );
  ipcMain.handle(IPC_CHANNELS.codexReadAccount, async (event, ...args: unknown[]) => {
    const invalid = rejectUntrustedOrUnexpectedArguments(trusted(event), args);
    return invalid ?? await capture(() => codex.readAccount());
  });
  ipcMain.handle(IPC_CHANNELS.codexStartLogin, async (event, value: unknown) =>
    trusted(event)
      ? await captureValidated(() => validateLoginArgument(value), (params) => capture(() => codex.startLogin(params.mode)))
      : failure("invalid_request", "Rejected Studio IPC from an untrusted sender"),
  );
  ipcMain.handle(IPC_CHANNELS.codexStartThread, async (event, ...args: unknown[]) => {
    const invalid = rejectUntrustedOrUnexpectedArguments(trusted(event), args);
    return invalid ?? await capture(() => codex.startThread());
  });
  ipcMain.handle(IPC_CHANNELS.codexResumeThread, async (event, value: unknown) =>
    trusted(event)
      ? await captureValidated(() => validateThreadArgument(value), (params) => capture(() => codex.resumeThread(params.threadId)))
      : failure("invalid_request", "Rejected Studio IPC from an untrusted sender"),
  );
  ipcMain.handle(IPC_CHANNELS.codexForkThread, async (event, value: unknown) =>
    trusted(event)
      ? await captureValidated(() => validateThreadArgument(value), (params) => capture(() => codex.forkThread(params.threadId)))
      : failure("invalid_request", "Rejected Studio IPC from an untrusted sender"),
  );
  ipcMain.handle(IPC_CHANNELS.codexStartTurn, async (event, value: unknown) =>
    trusted(event)
      ? await captureValidated(() => validateStartTurnArgument(value), (params) => capture(() => codex.startTurn(params.threadId, params.text)))
      : failure("invalid_request", "Rejected Studio IPC from an untrusted sender"),
  );
  ipcMain.handle(IPC_CHANNELS.codexSteerTurn, async (event, value: unknown) =>
    trusted(event)
      ? await captureValidated(() => validateSteerTurnArgument(value), (params) => capture(() => codex.steerTurn(params.threadId, params.turnId, params.text)))
      : failure("invalid_request", "Rejected Studio IPC from an untrusted sender"),
  );
  ipcMain.handle(IPC_CHANNELS.codexInterruptTurn, async (event, value: unknown) =>
    trusted(event)
      ? await captureValidated(() => validateInterruptTurnArgument(value), (params) => capture(() => codex.interruptTurn(params.threadId, params.turnId)))
      : failure("invalid_request", "Rejected Studio IPC from an untrusted sender"),
  );
  ipcMain.handle(IPC_CHANNELS.codexAnswerUserInput, async (event, value: unknown) =>
    trusted(event)
      ? await captureValidated(() => validateUserInputArgument(value), (params) => capture(() => codex.answerUserInput(params.token, params.answers)))
      : failure("invalid_request", "Rejected Studio IPC from an untrusted sender"),
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
    ipcMain.removeHandler(IPC_CHANNELS.getWorkspaceOverview);
    ipcMain.removeHandler(IPC_CHANNELS.listSourceDocuments);
    ipcMain.removeHandler(IPC_CHANNELS.readSourceDocument);
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
  };
}

export function validateWorkspaceArgument(value: unknown): { workspaceId: string } {
  const params = validateClosedParams(value, ["workspaceId"]);
  return { workspaceId: validateWorkspaceId(params.workspaceId) };
}

export function validateSourceReadArgument(
  value: unknown,
): { workspaceId: string; path: string } {
  const params = validateClosedParams(value, ["workspaceId", "path"]);
  if (typeof params.path !== "string" || !isPortableSourcePath(params.path)) {
    throw new TypeError("Studio source path is invalid");
  }
  return { workspaceId: validateWorkspaceId(params.workspaceId), path: params.path };
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
  if (typeof params.baseSha256 !== "string" || !SHA256_PATTERN.test(params.baseSha256)) {
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

export function validateChangesetIdArgument(value: unknown): { changesetId: string } {
  const params = validateClosedParams(value, ["changesetId"]);
  return { changesetId: validateChangesetId(params.changesetId) };
}

export function validateChangesetActionArgument(value: unknown): {
  changesetId: string;
  expectedReviewSha256?: string;
} {
  const params = validateClosedParams(value, ["changesetId", "expectedReviewSha256"]);
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

export function validateAssetReceiptArgument(
  value: unknown,
): { workspaceId: string; input: { receipt: string } } {
  const { workspaceId, input } = validateWorkspaceJobArgument(value, ["receipt"]);
  return {
    workspaceId,
    input: { receipt: validateJobPath(input.receipt, "receipt") },
  };
}

export function validateAssetpackArgument(
  value: unknown,
): { workspaceId: string; input: { assetpack: string; worldpack: string } } {
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

export function validateHeadlessArgument(
  value: unknown,
): { workspaceId: string; input: { worldpack: string; ticks: number } } {
  const { workspaceId, input } = validateWorkspaceJobArgument(value, ["worldpack", "ticks"]);
  if (
    !Number.isSafeInteger(input.ticks) ||
    (input.ticks as number) < 0 ||
    (input.ticks as number) > MAX_RUNTIME_TICKS
  ) {
    throw new TypeError(`Studio headless ticks must be an integer from 0 to ${MAX_RUNTIME_TICKS}`);
  }
  return {
    workspaceId,
    input: {
      worldpack: validateJobPath(input.worldpack, "worldpack"),
      ticks: input.ticks as number,
    },
  };
}

export function validateReplayArgument(
  value: unknown,
): { workspaceId: string; input: { worldpack: string; replay: string } } {
  const { workspaceId, input } = validateWorkspaceJobArgument(value, ["worldpack", "replay"]);
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
  if (typeof params.jobId !== "string" || !JOB_ID_PATTERN.test(params.jobId)) {
    throw new TypeError("Studio job ID is invalid");
  }
  return { jobId: params.jobId };
}

export function validateLoginArgument(value: unknown): { mode: "browser" | "device-code" } {
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

export function validateStartTurnArgument(value: unknown): { threadId: string; text: string } {
  const params = validateClosedParams(value, ["threadId", "text"]);
  return {
    threadId: validateCodexId(params.threadId, "thread"),
    text: validateTurnText(params.text),
  };
}

export function validateSteerTurnArgument(
  value: unknown,
): { threadId: string; turnId: string; text: string } {
  const params = validateClosedParams(value, ["threadId", "turnId", "text"]);
  return {
    threadId: validateCodexId(params.threadId, "thread"),
    turnId: validateCodexId(params.turnId, "turn"),
    text: validateTurnText(params.text),
  };
}

export function validateInterruptTurnArgument(
  value: unknown,
): { threadId: string; turnId: string } {
  const params = validateClosedParams(value, ["threadId", "turnId"]);
  return {
    threadId: validateCodexId(params.threadId, "thread"),
    turnId: validateCodexId(params.turnId, "turn"),
  };
}

export function validateUserInputArgument(
  value: unknown,
): { token: string; answers: Record<string, readonly string[]> } {
  const params = validateClosedParams(value, ["token", "answers"]);
  if (
    typeof params.token !== "string" ||
    !/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/u.test(params.token) ||
    !isRecord(params.answers)
  ) {
    throw new TypeError("Codex user-input response is invalid");
  }
  const answers: Record<string, readonly string[]> = {};
  const entries = Object.entries(params.answers);
  if (entries.length < 1 || entries.length > 3) {
    throw new TypeError("Codex user-input response has an invalid question count");
  }
  for (const [questionId, raw] of entries) {
    validateCodexId(questionId, "question");
    if (
      !Array.isArray(raw) ||
      raw.length < 1 ||
      raw.length > 8 ||
      !raw.every((item: unknown) => typeof item === "string" && Buffer.byteLength(item, "utf8") <= 8_192)
    ) {
      throw new TypeError("Codex user-input answer is invalid");
    }
    answers[questionId] = raw.map((item: unknown) => String(item));
  }
  return { token: params.token, answers };
}

function validateCodexId(value: unknown, context: string): string {
  if (typeof value !== "string" || !/^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$/u.test(value)) {
    throw new TypeError(`Codex ${context} ID is invalid`);
  }
  return value;
}

function validateTurnText(value: unknown): string {
  if (typeof value !== "string" || value.length < 1 || Buffer.byteLength(value, "utf8") > 128 * 1024) {
    throw new TypeError("Codex turn text exceeds the supported contract");
  }
  return value;
}

export function validateEventsListParams(value: unknown): EventsListParams {
  const params = validateClosedParams(value, ["workspace_id", "after_id", "limit"]);
  const result: EventsListParams = {};
  if (params.workspace_id !== undefined) {
    result.workspace_id = validateWorkspaceId(params.workspace_id);
  }
  if (params.after_id !== undefined) {
    if (!Number.isSafeInteger(params.after_id) || (params.after_id as number) < 0) {
      throw new TypeError("Studio event cursor must be a non-negative safe integer");
    }
    result.after_id = params.after_id as number;
  }
  if (params.limit !== undefined) {
    result.limit = validateLimit(params.limit);
  }
  return result;
}

export function validateChangesetsListParams(value: unknown): ChangesetsListParams {
  const params = validateClosedParams(value, ["workspace_id", "status", "limit"]);
  const result: ChangesetsListParams = {};
  if (params.workspace_id !== undefined) {
    result.workspace_id = validateWorkspaceId(params.workspace_id);
  }
  if (params.status !== undefined) {
    if (typeof params.status !== "string" || !CHANGESET_STATUSES.has(params.status)) {
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
  const params = validateClosedParams(value, ["workspace_id", "state", "limit"]);
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
      .then((reply) => validateJobCreateReply(reply, requestId, operation, input)),
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
      .then((reply) => validateChangesetGetReply(reply, requestId, changesetId)),
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
      .then((reply) => validateChangesetDiffReply(reply, requestId, changesetId)),
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
    (value.kind !== "response" && value.kind !== "error") ||
    value.request_id !== requestId ||
    (value.kind === "response" && value.method !== method)
  ) {
    throw new StudioProtocolError(`Forge Studio returned an invalid ${method} reply`);
  }
  return value;
}

function validateJobCreateReply(
  value: unknown,
  requestId: string,
  operation: NamedJobOperation,
  input: Readonly<Record<string, unknown>>,
): StudioReplyEnvelope {
  const reply = validateNamedReply(value, requestId, "job.create");
  if (reply.kind === "error") {
    return reply;
  }
  if (reply.method !== "job.create") {
    throw new StudioProtocolError("Forge Studio returned an invalid job.create reply");
  }
  const { job } = reply.result;
  if (
    job.format_version !== 2 ||
    job.operation !== operation ||
    !hasExactScalarFields(job.input, input)
  ) {
    throw new StudioProtocolError("Forge Studio returned a mismatched job.create result");
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
    throw new StudioProtocolError("Forge Studio returned an invalid changeset.create reply");
  }
  const record = requireChangesetIdentity(reply.result.changeset);
  const operations = record.operations;
  const operation: unknown = Array.isArray(operations) ? operations[0] : undefined;
  const proposedSha256 = createHash("sha256").update(content, "utf8").digest("hex");
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
    throw new StudioProtocolError("Forge Studio returned a mismatched staged source document");
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
    throw new StudioProtocolError("Forge Studio returned an invalid changeset.get reply");
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
    throw new StudioProtocolError("Forge Studio returned an invalid changeset.diff reply");
  }
  const diff = reply.result.diff;
  if (diff.changeset_id !== changesetId) {
    throw new StudioProtocolError("Forge Studio returned a mismatched changeset diff");
  }
  if (
    diff.changeset_format_version === 2 &&
    computeReviewSha256(diff.operations) !== diff.review_sha256
  ) {
    throw new StudioProtocolError("Forge Studio returned a mismatched changeset diff review");
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
    throw new StudioProtocolError(`Forge Studio returned an invalid ${method} reply`);
  }
  const record = requireChangesetIdentity(reply.result.changeset, changesetId);
  if (record.status !== status) {
    throw new StudioProtocolError(`Forge Studio returned a mismatched ${method} status`);
  }
  if (
    (record.format_version === 2 && record.review_sha256 !== expectedReviewSha256) ||
    (record.format_version === 1 && expectedReviewSha256 !== undefined)
  ) {
    throw new StudioProtocolError(`Forge Studio returned a mismatched ${method} review identity`);
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
    (expectedChangesetId !== undefined && value.changeset_id !== expectedChangesetId) ||
    (value.format_version === 2 && !hasValidV2ReviewIdentity(value))
  ) {
    throw new StudioProtocolError("Forge Studio returned a mismatched changeset identity");
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
    if (Object.values(projection).some((item) => item === undefined)) return null;
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
    return failure("invalid_request", "Rejected Studio IPC from an untrusted sender");
  }
  if (args.length !== 0) {
    return failure("invalid_request", "Studio operation does not accept arguments");
  }
  return null;
}

function untrustedFailure(): StudioClientResult<never> {
  return failure("invalid_request", "Rejected Studio IPC from an untrusted sender");
}

function validateSingleArgument<T>(
  args: readonly unknown[],
  validate: (value: unknown) => T,
): T {
  if (args.length !== 1) {
    throw new TypeError("Studio operation requires exactly one argument object");
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
  if (!isRecord(value) || !Object.keys(value).every((key) => allowed.includes(key))) {
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

function containsInvalidUnicode(value: string): boolean {
  for (let index = 0; index < value.length; index += 1) {
    const code = value.charCodeAt(index);
    if (code >= 0xd800 && code <= 0xdbff) {
      const following = value.charCodeAt(index + 1);
      if (index + 1 >= value.length || following < 0xdc00 || following > 0xdfff) return true;
      index += 1;
    } else if (code >= 0xdc00 && code <= 0xdfff) {
      return true;
    }
  }
  return false;
}

function validateLimit(value: unknown): number {
  if (!Number.isSafeInteger(value) || (value as number) < 1 || (value as number) > 1_000) {
    throw new TypeError("Studio list limit must be an integer from 1 to 1000");
  }
  return value as number;
}

async function capture<T>(operation: () => Promise<T>): Promise<StudioClientResult<T>> {
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
      (key, index) => key === actualKeys[index] && value[key] === expected[key],
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

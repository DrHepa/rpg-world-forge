import { randomUUID } from "node:crypto";

import type { BrowserWindow, IpcMain, IpcMainInvokeEvent } from "electron";

import {
  IPC_CHANNELS,
  type ChangesetsListParams,
  type EventsListParams,
  type ForgeServiceStatus,
  type JobsListParams,
  type StudioActivityEvent,
  type StudioClientError,
  type StudioClientResult,
  type StudioReadMethod,
  type StudioReplyEnvelope,
} from "../shared/studio-api";
import type { ForgeServiceClient } from "./forge-service";
import {
  StudioRequestCancelledError,
  StudioRequestTimeoutError,
  StudioTransportError,
} from "./ndjson-supervisor";
import { isTrustedStudioSender } from "./security";

const DEFAULT_REQUEST_TIMEOUT_MS = 10_000;
const WORKSPACE_ID_PATTERN = /^[a-z][a-z0-9_-]{1,63}$/u;
const CHANGESET_STATUSES = new Set(["staged", "approved", "rejected", "applied"]);
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

  const unsubscribe = service.subscribe((activity) => {
    if (!window.isDestroyed() && !window.webContents.isDestroyed()) {
      window.webContents.send(IPC_CHANNELS.event, activity);
    }
  });

  return () => {
    unsubscribe();
    ipcMain.removeHandler(IPC_CHANNELS.initialize);
    ipcMain.removeHandler(IPC_CHANNELS.status);
    ipcMain.removeHandler(IPC_CHANNELS.listWorkspaces);
    ipcMain.removeHandler(IPC_CHANNELS.listEvents);
    ipcMain.removeHandler(IPC_CHANNELS.listChangesets);
    ipcMain.removeHandler(IPC_CHANNELS.listJobs);
  };
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
  return await capture(() =>
    service.request(randomUUID(), method, params, DEFAULT_REQUEST_TIMEOUT_MS),
  );
}

async function captureValidated<T>(
  validate: () => T,
  operation: (value: T) => Promise<StudioClientResult<StudioReplyEnvelope>>,
): Promise<StudioClientResult<StudioReplyEnvelope>> {
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

function validateClosedParams(
  value: unknown,
  allowed: readonly string[],
): Record<string, unknown> {
  if (!isRecord(value) || !Object.keys(value).every((key) => allowed.includes(key))) {
    throw new TypeError("Studio list filters must be a closed object");
  }
  return value;
}

function validateWorkspaceId(value: unknown): string {
  if (typeof value !== "string" || !WORKSPACE_ID_PATTERN.test(value)) {
    throw new TypeError("Studio workspace ID is invalid");
  }
  return value;
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

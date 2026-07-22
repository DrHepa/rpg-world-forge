import {
  IPC_CHANNELS,
  type ForgeStudioApi,
  type CodexActivityEvent,
  type StudioActivityEvent,
  type StudioClientResult,
  type StudioReplyEnvelope,
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

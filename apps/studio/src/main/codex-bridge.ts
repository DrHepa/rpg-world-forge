import type {
  CodexAccountSummary,
  CodexActivityEvent,
  CodexBridgeStatus,
  CodexLoginMode,
  CodexLoginStart,
  CodexThreadSummary,
  CodexTurnSummary,
} from "../shared/studio-api";
import { prepareCodexLaunch, type CodexLaunchSpec } from "./codex-config";
import {
  CodexSupervisor,
  type CodexEvent,
} from "./codex-supervisor";
import type { ForgeServiceClient } from "./forge-service";
import type { CodexRuntime } from "./runtime-manifest";

const WORKSPACE_ID_PATTERN = /^[a-z][a-z0-9_-]{1,63}$/u;
const ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$/u;
const MAX_TURN_TEXT = 128 * 1024;

export interface CodexSupervisorClient {
  readonly state: string;
  readonly pid: number | null;
  subscribe(listener: (event: CodexEvent) => void): () => void;
  start(): Promise<unknown>;
  request(method: string, params: Record<string, unknown>, timeoutMs?: number): Promise<unknown>;
  answerUserInput(token: string, answers: Record<string, readonly string[]>): Promise<void>;
  stop(): Promise<void>;
}

export interface CodexBridgeClient {
  readonly status: CodexBridgeStatus;
  subscribe(listener: (event: CodexActivityEvent) => void): () => void;
  bindWorkspace(workspaceId: string): Promise<CodexBridgeStatus>;
  readAccount(): Promise<CodexAccountSummary>;
  startLogin(mode: CodexLoginMode): Promise<CodexLoginStart>;
  startThread(): Promise<CodexThreadSummary>;
  resumeThread(threadId: string): Promise<CodexThreadSummary>;
  forkThread(threadId: string): Promise<CodexThreadSummary>;
  startTurn(threadId: string, text: string): Promise<CodexTurnSummary>;
  steerTurn(threadId: string, turnId: string, text: string): Promise<void>;
  interruptTurn(threadId: string, turnId: string): Promise<void>;
  answerUserInput(token: string, answers: Record<string, readonly string[]>): Promise<void>;
  stop(): Promise<void>;
}

export interface CodexBridgeOptions {
  service: ForgeServiceClient;
  runtime: CodexRuntime;
  codexHome: string;
  dataDir: string;
  environment?: NodeJS.ProcessEnv;
  createSupervisor?: (spec: CodexLaunchSpec) => CodexSupervisorClient;
}

export class WorkspaceCodexBridge implements CodexBridgeClient {
  readonly #listeners = new Set<(event: CodexActivityEvent) => void>();
  readonly #createSupervisor: (spec: CodexLaunchSpec) => CodexSupervisorClient;
  #status: CodexBridgeStatus = {
    state: "unbound",
    message: "Codex is not bound to a Forge workspace",
    pid: null,
    workspaceId: null,
  };
  #supervisor: CodexSupervisorClient | null = null;
  #unsubscribeSupervisor: (() => void) | null = null;
  #workspaceRoot: string | null = null;
  #transition: Promise<void> = Promise.resolve();

  public constructor(private readonly options: CodexBridgeOptions) {
    this.#createSupervisor = options.createSupervisor ?? ((spec) => new CodexSupervisor(spec));
  }

  public get status(): CodexBridgeStatus {
    return { ...this.#status };
  }

  public subscribe(listener: (event: CodexActivityEvent) => void): () => void {
    this.#listeners.add(listener);
    queueMicrotask(() => listener({ type: "codex-status", status: this.status }));
    return () => this.#listeners.delete(listener);
  }

  public async bindWorkspace(workspaceId: string): Promise<CodexBridgeStatus> {
    if (!WORKSPACE_ID_PATTERN.test(workspaceId)) {
      throw new TypeError("Forge workspace ID is invalid");
    }
    return await this.#serialized(async () => {
      if (this.#status.state === "ready" && this.#status.workspaceId === workspaceId) {
        return this.status;
      }
      const binding = await this.options.service.getWorkspace(workspaceId);
      await this.#stopSupervisor();
      this.#setStatus({
        state: "starting",
        message: `Binding Codex to ${workspaceId}`,
        pid: null,
        workspaceId,
      });
      try {
        const spec = await prepareCodexLaunch({
          runtime: this.options.runtime,
          codexHome: this.options.codexHome,
          dataDir: this.options.dataDir,
          workspaceId,
          workspaceRoot: binding.worldRoot,
          environment: this.options.environment,
        });
        const supervisor = this.#createSupervisor(spec);
        this.#supervisor = supervisor;
        this.#workspaceRoot = spec.cwd;
        this.#unsubscribeSupervisor = supervisor.subscribe((event) => {
          if (this.#supervisor === supervisor) this.#handleSupervisorEvent(event);
        });
        await supervisor.start();
        this.#setStatus({
          state: "ready",
          message: `Codex is bound to ${workspaceId}`,
          pid: supervisor.pid,
          workspaceId,
        });
        return this.status;
      } catch (error) {
        await this.#stopSupervisor();
        this.#workspaceRoot = null;
        this.#setStatus({
          state: "unavailable",
          message: describe(error),
          pid: null,
          workspaceId,
        });
        throw error;
      }
    });
  }

  public async readAccount(): Promise<CodexAccountSummary> {
    return parseAccount(await this.#request("account/read", { refreshToken: false }));
  }

  public async startLogin(mode: CodexLoginMode): Promise<CodexLoginStart> {
    const params = mode === "browser" ? { type: "chatgpt" } : { type: "chatgptDeviceCode" };
    return parseLogin(await this.#request("account/login/start", params));
  }

  public async startThread(): Promise<CodexThreadSummary> {
    return parseThread(
      await this.#request("thread/start", this.#threadBoundary()),
    );
  }

  public async resumeThread(threadId: string): Promise<CodexThreadSummary> {
    assertId(threadId, "Codex thread ID");
    return parseThread(
      await this.#request("thread/resume", { threadId, ...this.#threadBoundary() }),
    );
  }

  public async forkThread(threadId: string): Promise<CodexThreadSummary> {
    assertId(threadId, "Codex thread ID");
    return parseThread(
      await this.#request("thread/fork", { threadId, ...this.#threadBoundary() }),
    );
  }

  public async startTurn(threadId: string, text: string): Promise<CodexTurnSummary> {
    assertId(threadId, "Codex thread ID");
    assertTurnText(text);
    const root = this.#requireWorkspaceRoot();
    return parseTurn(
      await this.#request("turn/start", {
        threadId,
        input: [{ type: "text", text, text_elements: [] }],
        cwd: root,
        approvalPolicy: "never",
        sandboxPolicy: { type: "readOnly", networkAccess: false },
      }, 120_000),
    );
  }

  public async steerTurn(threadId: string, turnId: string, text: string): Promise<void> {
    assertId(threadId, "Codex thread ID");
    assertId(turnId, "Codex turn ID");
    assertTurnText(text);
    await this.#request("turn/steer", {
      threadId,
      expectedTurnId: turnId,
      input: [{ type: "text", text, text_elements: [] }],
    });
  }

  public async interruptTurn(threadId: string, turnId: string): Promise<void> {
    assertId(threadId, "Codex thread ID");
    assertId(turnId, "Codex turn ID");
    await this.#request("turn/interrupt", { threadId, turnId });
  }

  public async answerUserInput(
    token: string,
    answers: Record<string, readonly string[]>,
  ): Promise<void> {
    await this.#requireSupervisor().answerUserInput(token, answers);
  }

  public async stop(): Promise<void> {
    await this.#serialized(async () => {
      await this.#stopSupervisor();
      this.#workspaceRoot = null;
      this.#setStatus({
        state: "unbound",
        message: "Codex is not bound to a Forge workspace",
        pid: null,
        workspaceId: null,
      });
    });
  }

  async #request(
    method: string,
    params: Record<string, unknown>,
    timeoutMs?: number,
  ): Promise<unknown> {
    return await this.#requireSupervisor().request(method, params, timeoutMs);
  }

  #threadBoundary(): Record<string, unknown> {
    return {
      cwd: this.#requireWorkspaceRoot(),
      approvalPolicy: "never",
      sandbox: "read-only",
    };
  }

  #requireSupervisor(): CodexSupervisorClient {
    if (this.#status.state !== "ready" || !this.#supervisor) {
      throw new Error("Codex is not bound to a ready Forge workspace");
    }
    return this.#supervisor;
  }

  #requireWorkspaceRoot(): string {
    if (!this.#workspaceRoot) {
      throw new Error("Codex has no bound Forge workspace root");
    }
    return this.#workspaceRoot;
  }

  async #stopSupervisor(): Promise<void> {
    this.#unsubscribeSupervisor?.();
    this.#unsubscribeSupervisor = null;
    const supervisor = this.#supervisor;
    this.#supervisor = null;
    if (supervisor) await supervisor.stop();
  }

  #handleSupervisorEvent(event: CodexEvent): void {
    switch (event.type) {
      case "state":
        if (event.state === "crashed") {
          this.#setStatus({
            state: "crashed",
            message: event.message,
            pid: null,
            workspaceId: this.#status.workspaceId,
          });
        }
        return;
      case "stderr":
        this.#emit({ type: "codex-stderr", text: event.text });
        return;
      case "notification":
        this.#emit({
          type: "codex-notification",
          method: event.method,
          params: event.params,
          authoritative: event.authoritative,
        });
        return;
      case "user-input":
        this.#emit({
          type: "codex-user-input",
          token: event.token,
          threadId: event.threadId,
          turnId: event.turnId,
          questions: event.questions,
        });
    }
  }

  #setStatus(status: CodexBridgeStatus): void {
    this.#status = status;
    this.#emit({ type: "codex-status", status: this.status });
  }

  #emit(event: CodexActivityEvent): void {
    for (const listener of this.#listeners) listener(event);
  }

  async #serialized<T>(operation: () => Promise<T>): Promise<T> {
    const result = this.#transition.then(operation);
    this.#transition = result.then(() => undefined, () => undefined);
    return await result;
  }
}

export class UnavailableCodexBridge implements CodexBridgeClient {
  public constructor(private readonly reason: string) {}
  public get status(): CodexBridgeStatus {
    return { state: "unavailable", message: this.reason, pid: null, workspaceId: null };
  }
  public subscribe(listener: (event: CodexActivityEvent) => void): () => void {
    queueMicrotask(() => listener({ type: "codex-status", status: this.status }));
    return () => undefined;
  }
  public bindWorkspace(): Promise<never> { return Promise.reject(new Error(this.reason)); }
  public readAccount(): Promise<never> { return Promise.reject(new Error(this.reason)); }
  public startLogin(): Promise<never> { return Promise.reject(new Error(this.reason)); }
  public startThread(): Promise<never> { return Promise.reject(new Error(this.reason)); }
  public resumeThread(): Promise<never> { return Promise.reject(new Error(this.reason)); }
  public forkThread(): Promise<never> { return Promise.reject(new Error(this.reason)); }
  public startTurn(): Promise<never> { return Promise.reject(new Error(this.reason)); }
  public steerTurn(): Promise<never> { return Promise.reject(new Error(this.reason)); }
  public interruptTurn(): Promise<never> { return Promise.reject(new Error(this.reason)); }
  public answerUserInput(): Promise<never> { return Promise.reject(new Error(this.reason)); }
  public stop(): Promise<void> { return Promise.resolve(); }
}

function parseAccount(value: unknown): CodexAccountSummary {
  if (!isRecord(value) || typeof value.requiresOpenaiAuth !== "boolean") {
    throw new Error("Codex returned an invalid account response");
  }
  if (value.account === null) {
    return { account: null, requiresOpenaiAuth: value.requiresOpenaiAuth };
  }
  if (!isRecord(value.account) || typeof value.account.type !== "string") {
    throw new Error("Codex returned an invalid account response");
  }
  if (value.account.type === "apiKey") {
    return { account: { type: "apiKey" }, requiresOpenaiAuth: value.requiresOpenaiAuth };
  }
  if (
    value.account.type === "chatgpt" &&
    (value.account.email === null || typeof value.account.email === "string") &&
    typeof value.account.planType === "string"
  ) {
    return {
      account: {
        type: "chatgpt",
        email: value.account.email,
        planType: value.account.planType,
      },
      requiresOpenaiAuth: value.requiresOpenaiAuth,
    };
  }
  throw new Error("Codex returned an unsupported account type");
}

function parseLogin(value: unknown): CodexLoginStart {
  if (!isRecord(value) || typeof value.type !== "string") {
    throw new Error("Codex returned an invalid login response");
  }
  if (
    value.type === "chatgpt" &&
    isBoundedString(value.loginId, 256) &&
    isSafeHttpUrl(value.authUrl)
  ) {
    return { type: "chatgpt", loginId: value.loginId, authUrl: value.authUrl };
  }
  if (
    value.type === "chatgptDeviceCode" &&
    isBoundedString(value.loginId, 256) &&
    isSafeHttpUrl(value.verificationUrl) &&
    isBoundedString(value.userCode, 128)
  ) {
    return {
      type: "chatgptDeviceCode",
      loginId: value.loginId,
      verificationUrl: value.verificationUrl,
      userCode: value.userCode,
    };
  }
  throw new Error("Codex returned an unsupported login response");
}

function parseThread(value: unknown): CodexThreadSummary {
  const thread = isRecord(value) && isRecord(value.thread) ? value.thread : null;
  if (!thread || !isBoundedString(thread.id, 256)) {
    throw new Error("Codex returned an invalid thread response");
  }
  return { threadId: thread.id };
}

function parseTurn(value: unknown): CodexTurnSummary {
  const turn = isRecord(value) && isRecord(value.turn) ? value.turn : null;
  if (!turn || !isBoundedString(turn.id, 256) || !isBoundedString(turn.status, 64)) {
    throw new Error("Codex returned an invalid turn response");
  }
  return { turnId: turn.id, status: turn.status };
}

function assertId(value: string, label: string): void {
  if (!ID_PATTERN.test(value)) throw new TypeError(`${label} is invalid`);
}

function assertTurnText(value: string): void {
  if (typeof value !== "string" || value.length < 1 || Buffer.byteLength(value, "utf8") > MAX_TURN_TEXT) {
    throw new TypeError("Codex turn text must contain 1 to 131072 UTF-8 bytes");
  }
}

function isSafeHttpUrl(value: unknown): value is string {
  if (!isBoundedString(value, 4_096)) return false;
  try {
    const url = new URL(value);
    return (url.protocol === "https:" || url.protocol === "http:") && url.username === "" && url.password === "";
  } catch {
    return false;
  }
}

function isBoundedString(value: unknown, maximum: number): value is string {
  return typeof value === "string" && value.length > 0 && value.length <= maximum;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function describe(error: unknown): string {
  return error instanceof Error ? error.message : "Codex bridge failed";
}

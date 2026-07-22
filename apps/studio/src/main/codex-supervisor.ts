import { execFile, spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { randomUUID } from "node:crypto";
import path from "node:path";

import type { CodexLaunchSpec } from "./codex-config";

export const CODEX_MAX_LINE_BYTES = 8 * 1024 * 1024;
export const CODEX_MAX_PENDING_REQUESTS = 64;
export const CODEX_MAX_OUTSTANDING_BYTES = 16 * 1024 * 1024;
export const CODEX_MAX_STDERR_BYTES = 64 * 1024;
export const CODEX_MAX_RENDERER_EVENTS = 256;
export const CODEX_MAX_QUEUED_WRITES = 256;

const MAX_JSON_DEPTH = 64;
const MAX_JSON_NODES = 100_000;
const MAX_IGNORED_REPLIES = 128;
const DEFAULT_TIMEOUT_MS = 30_000;
const DELTA_FLUSH_MS = 25;
const ALLOWED_CLIENT_METHODS = new Set([
  "account/read",
  "account/login/start",
  "thread/start",
  "thread/resume",
  "thread/fork",
  "turn/start",
  "turn/steer",
  "turn/interrupt",
]);
const DELTA_METHODS = new Set([
  "item/agentMessage/delta",
  "item/plan/delta",
  "item/reasoning/summaryTextDelta",
  "item/reasoning/textDelta",
]);

type JsonRpcId = string | number;

export type CodexState = "stopped" | "starting" | "ready" | "crashed";

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

export type CodexEvent =
  | { type: "state"; state: CodexState; message: string; pid: number | null }
  | { type: "stderr"; text: string }
  | { type: "notification"; method: string; params: unknown; authoritative: boolean }
  | {
      type: "user-input";
      token: string;
      threadId: string;
      turnId: string;
      questions: CodexUserInputQuestion[];
    };

export interface CodexResponse {
  id: JsonRpcId;
  result?: unknown;
  error?: { code: number; message: string; data?: unknown };
}

export interface CodexSupervisorOptions {
  maxLineBytes?: number;
  maxPendingRequests?: number;
  maxOutstandingBytes?: number;
  maxStderrBytes?: number;
  maxRendererEvents?: number;
  defaultTimeoutMs?: number;
  verifyVersion?: (spec: CodexLaunchSpec) => Promise<void>;
}

interface PendingRequest {
  resolve: (response: CodexResponse) => void;
  reject: (error: Error) => void;
  timer: NodeJS.Timeout;
  frame: OutboundFrame;
}

interface OutboundFrame {
  payload: Buffer;
  state: "queued" | "writing" | "sent";
  requestId: number | null;
  resolveWrite?: () => void;
  rejectWrite?: (error: Error) => void;
}

interface PendingUserInput {
  requestId: JsonRpcId;
  questionIds: ReadonlySet<string>;
}

interface QueuedEvent {
  event: CodexEvent;
  authoritative: boolean;
}

interface CoalescedDelta {
  method: string;
  params: Record<string, unknown>;
  delta: string;
  timer: NodeJS.Timeout;
}

export class CodexTransportError extends Error {
  public constructor(message: string, options?: ErrorOptions) {
    super(message, options);
    this.name = "CodexTransportError";
  }
}

export class CodexProtocolError extends CodexTransportError {
  public constructor(message: string, options?: ErrorOptions) {
    super(message, options);
    this.name = "CodexProtocolError";
  }
}

export class CodexOverloadError extends CodexTransportError {
  public constructor(message: string) {
    super(message);
    this.name = "CodexOverloadError";
  }
}

export class CodexRequestTimeoutError extends CodexTransportError {
  public constructor(requestId: number) {
    super(`Codex request ${String(requestId)} timed out`);
    this.name = "CodexRequestTimeoutError";
  }
}

export class CodexSupervisor {
  readonly #spec: CodexLaunchSpec;
  readonly #listeners = new Set<(event: CodexEvent) => void>();
  readonly #pending = new Map<number, PendingRequest>();
  readonly #ignoredReplies = new Set<number>();
  readonly #writeQueue: OutboundFrame[] = [];
  readonly #pendingUserInput = new Map<string, PendingUserInput>();
  readonly #eventQueue: QueuedEvent[] = [];
  readonly #coalescedDeltas = new Map<string, CoalescedDelta>();
  readonly #maxLineBytes: number;
  readonly #maxPendingRequests: number;
  readonly #maxOutstandingBytes: number;
  readonly #maxRendererEvents: number;
  readonly #defaultTimeoutMs: number;
  readonly #verifyVersion: (spec: CodexLaunchSpec) => Promise<void>;
  readonly #stderr: BoundedTextTail;
  #decoder: BoundedLineDecoder;
  #child: ChildProcessWithoutNullStreams | null = null;
  #state: CodexState = "stopped";
  #expectedStop = false;
  #protocolFailed = false;
  #writing: OutboundFrame | null = null;
  #outstandingBytes = 0;
  #nextRequestId = 1;
  #eventsScheduled = false;

  public constructor(spec: CodexLaunchSpec, options: CodexSupervisorOptions = {}) {
    assertLaunchSpec(spec);
    this.#spec = Object.freeze({
      ...spec,
      args: Object.freeze([spec.args[0], spec.args[1], spec.args[2]] as const),
      env: Object.freeze({ ...spec.env }),
    });
    this.#maxLineBytes = options.maxLineBytes ?? CODEX_MAX_LINE_BYTES;
    this.#maxPendingRequests = options.maxPendingRequests ?? CODEX_MAX_PENDING_REQUESTS;
    this.#maxOutstandingBytes = options.maxOutstandingBytes ?? CODEX_MAX_OUTSTANDING_BYTES;
    this.#maxRendererEvents = options.maxRendererEvents ?? CODEX_MAX_RENDERER_EVENTS;
    this.#defaultTimeoutMs = options.defaultTimeoutMs ?? DEFAULT_TIMEOUT_MS;
    this.#verifyVersion = options.verifyVersion ?? verifyExactVersion;
    assertLimit(this.#maxLineBytes, 64, "maxLineBytes");
    assertLimit(this.#maxPendingRequests, 1, "maxPendingRequests");
    assertLimit(this.#maxOutstandingBytes, 64, "maxOutstandingBytes");
    assertLimit(this.#maxRendererEvents, 1, "maxRendererEvents");
    this.#stderr = new BoundedTextTail(options.maxStderrBytes ?? CODEX_MAX_STDERR_BYTES);
    this.#decoder = new BoundedLineDecoder(this.#maxLineBytes);
  }

  public get state(): CodexState {
    return this.#state;
  }

  public get pid(): number | null {
    return this.#child?.pid ?? null;
  }

  public get diagnostics() {
    return {
      pendingRequests: this.#pending.size,
      outstandingBytes: this.#outstandingBytes,
      queuedWrites: this.#writeQueue.length + (this.#writing ? 1 : 0),
      pendingUserInput: this.#pendingUserInput.size,
      rendererEvents: this.#eventQueue.length,
      stderrBytes: Buffer.byteLength(this.#stderr.text(), "utf8"),
    };
  }

  public subscribe(listener: (event: CodexEvent) => void): () => void {
    this.#listeners.add(listener);
    return () => this.#listeners.delete(listener);
  }

  public async start(): Promise<unknown> {
    if (this.#state === "ready") {
      return {};
    }
    if (this.#child) {
      throw new CodexTransportError("Codex app-server is already starting or stopping");
    }
    this.#setState("starting", "Starting Codex app-server");
    try {
      await this.#verifyVersion(this.#spec);
    } catch (error) {
      this.#setState("crashed", `Codex version verification failed: ${describe(error)}`);
      throw error;
    }
    this.#expectedStop = false;
    this.#protocolFailed = false;
    this.#decoder = new BoundedLineDecoder(this.#maxLineBytes);
    this.#nextRequestId = 1;

    let child: ChildProcessWithoutNullStreams;
    try {
      child = spawn(this.#spec.executable, [...this.#spec.args], {
        cwd: this.#spec.cwd,
        env: { ...this.#spec.env },
        shell: false,
        windowsHide: true,
        detached: process.platform !== "win32",
        stdio: ["pipe", "pipe", "pipe"],
      });
    } catch (error) {
      const failure = new CodexTransportError(`Failed to spawn Codex app-server: ${describe(error)}`);
      this.#setState("crashed", failure.message);
      throw failure;
    }
    this.#child = child;
    this.#attachChild(child);
    await waitForSpawn(child);
    try {
      const response = await this.#requestInternal(
        "initialize",
        {
          clientInfo: {
            name: "rpg-world-forge-studio",
            title: "RPG World Forge Studio",
            version: "0.1.0",
          },
          capabilities: {
            experimentalApi: false,
            requestAttestation: false,
          },
        },
        10_000,
      );
      if (response.error) {
        throw new CodexProtocolError(`Codex initialize failed: ${response.error.message}`);
      }
      await this.#enqueueObject({ method: "initialized" });
      this.#setState("ready", "Codex app-server is ready");
      return response.result;
    } catch (error) {
      this.#failProtocol(error);
      throw error;
    }
  }

  public async request(
    method: string,
    params: Record<string, unknown>,
    timeoutMs = this.#defaultTimeoutMs,
  ): Promise<unknown> {
    if (this.#state !== "ready") {
      throw new CodexTransportError("Codex app-server is not ready");
    }
    if (!ALLOWED_CLIENT_METHODS.has(method)) {
      throw new CodexProtocolError(`Codex client method is not allowed: ${method}`);
    }
    const response = await this.#requestInternal(method, params, timeoutMs);
    if (response.error) {
      throw new CodexProtocolError(`Codex ${method} failed: ${response.error.message}`);
    }
    return response.result;
  }

  public async answerUserInput(
    token: string,
    answers: Record<string, readonly string[]>,
  ): Promise<void> {
    if (!isOpaqueToken(token)) {
      throw new TypeError("Codex user-input response is invalid");
    }
    const pending = this.#pendingUserInput.get(token);
    if (!pending) {
      throw new CodexProtocolError("Codex user-input request is no longer pending");
    }
    const keys = Object.keys(answers);
    if (
      keys.length !== pending.questionIds.size ||
      !keys.every((key) => pending.questionIds.has(key))
    ) {
      throw new TypeError("Codex user-input answers must match every requested question");
    }
    const safeAnswers: Record<string, { answers: string[] }> = {};
    for (const questionId of keys) {
      safeAnswers[questionId] = { answers: validateAnswerValues(answers[questionId]) };
    }
    this.#pendingUserInput.delete(token);
    await this.#enqueueObject({ id: pending.requestId, result: { answers: safeAnswers } });
  }

  public async stop(): Promise<void> {
    this.#expectedStop = true;
    this.#clearTransientState(new CodexTransportError("Codex app-server stopped"));
    const child = this.#child;
    if (!child) {
      this.#setState("stopped", "Codex app-server is stopped");
      return;
    }
    await terminateChildTree(child, false);
    if (!(await waitForExit(child, 1_000))) {
      await terminateChildTree(child, true);
      await waitForExit(child, 1_000);
    }
  }

  async #requestInternal(
    method: string,
    params: Record<string, unknown>,
    timeoutMs: number,
  ): Promise<CodexResponse> {
    if (!this.#child || (this.#state !== "starting" && this.#state !== "ready")) {
      throw new CodexTransportError("Codex app-server is not running");
    }
    if (this.#pending.size >= this.#maxPendingRequests) {
      throw new CodexOverloadError("Codex pending request capacity reached");
    }
    if (!Number.isSafeInteger(timeoutMs) || timeoutMs < 100 || timeoutMs > 120_000) {
      throw new TypeError("Codex timeout must be an integer from 100 to 120000");
    }
    const requestId = this.#nextRequestId++;
    if (!Number.isSafeInteger(requestId)) {
      throw new CodexProtocolError("Codex request ID space is exhausted");
    }
    const payload = encodeLine({ id: requestId, method, params }, this.#maxLineBytes);
    const frame: OutboundFrame = { payload, state: "queued", requestId };
    return await new Promise<CodexResponse>((resolve, reject) => {
      const timer = setTimeout(() => {
        this.#abandonRequest(requestId, new CodexRequestTimeoutError(requestId));
      }, timeoutMs);
      timer.unref();
      this.#pending.set(requestId, { resolve, reject, timer, frame });
      try {
        this.#queueFrame(frame);
      } catch (error) {
        clearTimeout(timer);
        this.#pending.delete(requestId);
        reject(error instanceof Error ? error : new CodexTransportError(describe(error)));
      }
    });
  }

  async #enqueueObject(value: Record<string, unknown>): Promise<void> {
    const payload = encodeLine(value, this.#maxLineBytes);
    await new Promise<void>((resolve, reject) => {
      this.#queueFrame({ payload, state: "queued", requestId: null, resolveWrite: resolve, rejectWrite: reject });
    });
  }

  #queueFrame(frame: OutboundFrame): void {
    if (!this.#child || (this.#state !== "starting" && this.#state !== "ready")) {
      throw new CodexTransportError("Codex app-server is not running");
    }
    if (this.#writeQueue.length + (this.#writing ? 1 : 0) >= CODEX_MAX_QUEUED_WRITES) {
      throw new CodexOverloadError("Codex outbound frame capacity reached");
    }
    if (
      frame.payload.length > this.#maxOutstandingBytes ||
      this.#outstandingBytes > this.#maxOutstandingBytes - frame.payload.length
    ) {
      throw new CodexOverloadError("Codex outbound byte capacity reached");
    }
    this.#outstandingBytes += frame.payload.length;
    this.#writeQueue.push(frame);
    this.#pumpWrites();
  }

  #pumpWrites(): void {
    if (this.#writing || !this.#child) {
      return;
    }
    const frame = this.#writeQueue.shift();
    if (!frame) {
      return;
    }
    const child = this.#child;
    this.#writing = frame;
    frame.state = "writing";
    void flushWrite(child, frame.payload)
      .then(() => {
        frame.state = "sent";
        frame.resolveWrite?.();
      })
      .catch((error: unknown) => {
        frame.rejectWrite?.(error instanceof Error ? error : new Error(describe(error)));
        this.#failProtocol(new CodexTransportError(`Failed to write Codex frame: ${describe(error)}`));
      })
      .finally(() => {
        this.#outstandingBytes -= frame.payload.length;
        if (this.#outstandingBytes < 0) {
          this.#outstandingBytes = 0;
        }
        if (this.#writing === frame) {
          this.#writing = null;
        }
        this.#pumpWrites();
      });
  }

  #attachChild(child: ChildProcessWithoutNullStreams): void {
    child.stdin.on("error", (error) => {
      if (!this.#expectedStop && this.#child === child) {
        this.#failProtocol(new CodexTransportError(`Codex stdin failed: ${error.message}`));
      }
    });
    child.stdout.on("data", (chunk: Buffer) => {
      try {
        for (const line of this.#decoder.push(chunk)) {
          this.#handleLine(line);
        }
      } catch (error) {
        this.#failProtocol(error);
      }
    });
    child.stdout.on("end", () => {
      try {
        for (const line of this.#decoder.finish()) {
          this.#handleLine(line);
        }
      } catch (error) {
        this.#failProtocol(error);
      }
    });
    child.stderr.on("data", (chunk: Buffer) => {
      this.#queueEvent({ type: "stderr", text: this.#stderr.append(chunk) }, false);
    });
    child.once("error", (error) => this.#finalizeChild(child, `spawn error: ${error.message}`));
    child.once("exit", (code, signal) => {
      this.#finalizeChild(child, signal ? `signal ${signal}` : `exit code ${String(code)}`);
    });
  }

  #handleLine(line: string): void {
    let value: unknown;
    try {
      value = JSON.parse(line);
    } catch (error) {
      throw new CodexProtocolError("Codex emitted malformed JSON", { cause: error });
    }
    validateJsonTree(value);
    if (!isRecord(value)) {
      throw new CodexProtocolError("Codex emitted a non-object message");
    }
    if (typeof value.method === "string") {
      if ("id" in value) {
        assertExactKeys(value, ["id", "method", "params"]);
        this.#handleServerRequest(value.id, value.method, value.params);
      } else {
        assertExactKeys(value, ["method", "params"]);
        this.#handleNotification(value.method, value.params);
      }
      return;
    }
    if ("id" in value && ("result" in value || "error" in value)) {
      this.#handleResponse(value);
      return;
    }
    throw new CodexProtocolError("Codex emitted an unknown message shape");
  }

  #handleResponse(value: Record<string, unknown>): void {
    const hasResult = "result" in value;
    const hasError = "error" in value;
    if (hasResult === hasError) {
      throw new CodexProtocolError("Codex response must contain exactly result or error");
    }
    assertExactKeys(value, hasResult ? ["id", "result"] : ["error", "id"]);
    if (!Number.isSafeInteger(value.id)) {
      throw new CodexProtocolError("Codex response ID is invalid");
    }
    const requestId = value.id as number;
    if (this.#ignoredReplies.delete(requestId)) {
      return;
    }
    const pending = this.#pending.get(requestId);
    if (!pending) {
      throw new CodexProtocolError(`Codex emitted an unexpected response for ${String(requestId)}`);
    }
    this.#pending.delete(requestId);
    clearTimeout(pending.timer);
    if (hasError) {
      if (!isRecord(value.error) || typeof value.error.code !== "number" || typeof value.error.message !== "string") {
        throw new CodexProtocolError("Codex error response is malformed");
      }
      pending.resolve({
        id: requestId,
        error: {
          code: value.error.code,
          message: value.error.message,
          ...(value.error.data === undefined ? {} : { data: value.error.data }),
        },
      });
      return;
    }
    pending.resolve({ id: requestId, result: value.result });
  }

  #handleServerRequest(id: unknown, method: string, params: unknown): void {
    if (!isRpcId(id)) {
      throw new CodexProtocolError("Codex server request ID is invalid");
    }
    if (method === "item/tool/requestUserInput") {
      this.#handleUserInputRequest(id, params);
      return;
    }
    const result = denialForServerRequest(method);
    if (result !== undefined) {
      void this.#enqueueObject({ id, result }).catch((error: unknown) => this.#failProtocol(error));
      return;
    }
    void this.#rejectUnknownServerRequest(id, method);
  }

  async #rejectUnknownServerRequest(id: JsonRpcId, method: string): Promise<void> {
    try {
      await this.#enqueueObject({
        id,
        error: { code: -32601, message: "Studio does not permit this Codex server request" },
      });
    } finally {
      this.#failProtocol(new CodexProtocolError(`Codex server request is not allowed: ${method}`));
    }
  }

  #handleUserInputRequest(id: JsonRpcId, params: unknown): void {
    const parsed = parseUserInput(params);
    if (this.#pendingUserInput.size >= this.#maxPendingRequests) {
      void this.#enqueueObject({
        id,
        error: { code: -32000, message: "Studio user-input capacity reached" },
      }).catch((error: unknown) => this.#failProtocol(error));
      return;
    }
    const token = randomUUID();
    this.#pendingUserInput.set(token, {
      requestId: id,
      questionIds: new Set(parsed.questions.map((question) => question.id)),
    });
    this.#queueEvent({ type: "user-input", token, ...parsed }, true);
  }

  #handleNotification(method: string, params: unknown): void {
    if (method.length < 1 || method.length > 256) {
      throw new CodexProtocolError("Codex notification method is invalid");
    }
    const safe = sanitizeRendererValue(params);
    if (DELTA_METHODS.has(method) && isRecord(safe) && typeof safe.delta === "string") {
      this.#coalesceDelta(method, safe);
      return;
    }
    const authoritative = method === "turn/completed";
    this.#queueEvent({ type: "notification", method, params: safe, authoritative }, authoritative);
  }

  #coalesceDelta(method: string, params: Record<string, unknown>): void {
    const delta = params.delta;
    if (typeof delta !== "string") {
      return;
    }
    const key = `${method}:${stringField(params, "threadId")}:${stringField(params, "turnId")}:${stringField(params, "itemId")}`;
    const existing = this.#coalescedDeltas.get(key);
    if (existing) {
      existing.delta = `${existing.delta}${delta}`.slice(-65_536);
      return;
    }
    if (this.#coalescedDeltas.size >= 64) {
      const oldest = this.#coalescedDeltas.keys().next().value;
      if (oldest) {
        this.#flushDelta(oldest);
      }
    }
    const timer = setTimeout(() => this.#flushDelta(key), DELTA_FLUSH_MS);
    timer.unref();
    this.#coalescedDeltas.set(key, {
      method,
      params: { ...params },
      delta,
      timer,
    });
  }

  #flushDelta(key: string): void {
    const entry = this.#coalescedDeltas.get(key);
    if (!entry) {
      return;
    }
    clearTimeout(entry.timer);
    this.#coalescedDeltas.delete(key);
    this.#queueEvent(
      {
        type: "notification",
        method: entry.method,
        params: { ...entry.params, delta: entry.delta },
        authoritative: false,
      },
      false,
    );
  }

  #queueEvent(event: CodexEvent, authoritative: boolean): void {
    if (this.#eventQueue.length >= this.#maxRendererEvents) {
      const disposable = this.#eventQueue.findIndex((queued) => !queued.authoritative);
      if (disposable >= 0) {
        this.#eventQueue.splice(disposable, 1);
      } else if (!authoritative) {
        return;
      } else {
        this.#failProtocol(new CodexOverloadError("Authoritative Codex event capacity reached"));
        return;
      }
    }
    this.#eventQueue.push({ event, authoritative });
    if (!this.#eventsScheduled) {
      this.#eventsScheduled = true;
      queueMicrotask(() => this.#drainEvents());
    }
  }

  #drainEvents(): void {
    this.#eventsScheduled = false;
    while (this.#eventQueue.length > 0) {
      const next = this.#eventQueue.shift();
      if (!next) {
        break;
      }
      for (const listener of this.#listeners) {
        listener(next.event);
      }
    }
  }

  #abandonRequest(requestId: number, error: Error): void {
    const pending = this.#pending.get(requestId);
    if (!pending) {
      return;
    }
    this.#pending.delete(requestId);
    clearTimeout(pending.timer);
    if (pending.frame.state === "queued") {
      const index = this.#writeQueue.indexOf(pending.frame);
      if (index >= 0) {
        this.#writeQueue.splice(index, 1);
        this.#outstandingBytes -= pending.frame.payload.length;
      }
    } else if (this.#ignoredReplies.size >= MAX_IGNORED_REPLIES) {
      this.#failProtocol(new CodexOverloadError("Codex ignored response capacity reached"));
    } else {
      this.#ignoredReplies.add(requestId);
    }
    pending.reject(error);
    if (pending.frame.state === "writing") {
      this.#failProtocol(new CodexProtocolError("A Codex request timed out during an uncertain write"));
    }
  }

  #failProtocol(error: unknown): void {
    if (this.#protocolFailed) {
      return;
    }
    this.#protocolFailed = true;
    const failure =
      error instanceof Error
        ? error
        : new CodexProtocolError("Codex protocol failed", { cause: error });
    this.#clearTransientState(failure);
    this.#setState("crashed", failure.message);
    const child = this.#child;
    if (child) {
      void terminateChildTree(child, true).catch(() => child.kill());
    }
  }

  #clearTransientState(error: Error): void {
    for (const pending of this.#pending.values()) {
      clearTimeout(pending.timer);
      pending.reject(error);
    }
    this.#pending.clear();
    this.#ignoredReplies.clear();
    for (const frame of this.#writeQueue) {
      frame.rejectWrite?.(error);
    }
    this.#writeQueue.length = 0;
    this.#writing?.rejectWrite?.(error);
    this.#outstandingBytes = 0;
    this.#pendingUserInput.clear();
    for (const delta of this.#coalescedDeltas.values()) {
      clearTimeout(delta.timer);
    }
    this.#coalescedDeltas.clear();
  }

  #finalizeChild(child: ChildProcessWithoutNullStreams, detail: string): void {
    if (this.#child !== child) {
      return;
    }
    this.#child = null;
    const expected = this.#expectedStop;
    this.#expectedStop = false;
    if (expected) {
      this.#setState("stopped", "Codex app-server is stopped");
      return;
    }
    const message = this.#protocolFailed
      ? `Codex app-server terminated after a protocol failure (${detail})`
      : `Codex app-server exited unexpectedly (${detail})`;
    this.#clearTransientState(new CodexTransportError(message));
    this.#setState("crashed", message);
  }

  #setState(state: CodexState, message: string): void {
    this.#state = state;
    this.#queueEvent({ type: "state", state, message, pid: this.pid }, true);
  }
}

function denialForServerRequest(method: string): Record<string, unknown> | undefined {
  switch (method) {
    case "item/commandExecution/requestApproval":
    case "item/fileChange/requestApproval":
      return { decision: "decline" };
    case "applyPatchApproval":
    case "execCommandApproval":
      return { decision: "denied" };
    case "mcpServer/elicitation/request":
      return { action: "decline", content: null, _meta: null };
    case "item/permissions/requestApproval":
      return { permissions: {}, scope: "turn", strictAutoReview: true };
    case "item/tool/call":
      return { contentItems: [], success: false };
    default:
      return undefined;
  }
}

function parseUserInput(params: unknown): {
  threadId: string;
  turnId: string;
  questions: CodexUserInputQuestion[];
} {
  if (!isRecord(params)) {
    throw new CodexProtocolError("Codex user-input params are malformed");
  }
  assertExactKeys(params, ["autoResolutionMs", "itemId", "questions", "threadId", "turnId"]);
  if (
    !isBoundedId(params.threadId) ||
    !isBoundedId(params.turnId) ||
    !isBoundedId(params.itemId) ||
    !Array.isArray(params.questions) ||
    params.questions.length < 1 ||
    params.questions.length > 3 ||
    !(params.autoResolutionMs === null ||
      (Number.isSafeInteger(params.autoResolutionMs) &&
        (params.autoResolutionMs as number) >= 60_000 &&
        (params.autoResolutionMs as number) <= 240_000))
  ) {
    throw new CodexProtocolError("Codex user-input params exceed the supported contract");
  }
  const ids = new Set<string>();
  const questions = params.questions.map((value): CodexUserInputQuestion => {
    if (!isRecord(value)) {
      throw new CodexProtocolError("Codex user-input question is malformed");
    }
    assertExactKeys(value, ["header", "id", "isOther", "isSecret", "options", "question"]);
    if (
      !isBoundedId(value.id) ||
      typeof value.header !== "string" ||
      value.header.length > 64 ||
      typeof value.question !== "string" ||
      value.question.length > 2_048 ||
      typeof value.isOther !== "boolean" ||
      typeof value.isSecret !== "boolean"
    ) {
      throw new CodexProtocolError("Codex user-input question exceeds the supported contract");
    }
    if (ids.has(value.id)) {
      throw new CodexProtocolError("Codex user-input question IDs must be unique");
    }
    ids.add(value.id);
    let options: CodexUserInputOption[] | null = null;
    if (value.options !== null) {
      if (!Array.isArray(value.options) || value.options.length < 1 || value.options.length > 3) {
        throw new CodexProtocolError("Codex user-input options exceed the supported contract");
      }
      options = value.options.map((option) => {
        if (!isRecord(option)) {
          throw new CodexProtocolError("Codex user-input option is malformed");
        }
        assertExactKeys(option, ["description", "label"]);
        if (
          typeof option.label !== "string" ||
          option.label.length > 128 ||
          typeof option.description !== "string" ||
          option.description.length > 1_024
        ) {
          throw new CodexProtocolError("Codex user-input option exceeds the supported contract");
        }
        return { label: option.label, description: option.description };
      });
    }
    return {
      id: value.id,
      header: value.header,
      question: value.question,
      isOther: value.isOther,
      isSecret: value.isSecret,
      options,
    };
  });
  return { threadId: params.threadId, turnId: params.turnId, questions };
}

function sanitizeRendererValue(value: unknown): unknown {
  let nodes = 0;
  let stringBytes = 0;
  const visit = (current: unknown, depth: number, key = ""): unknown => {
    nodes += 1;
    if (nodes > 4_096 || depth > 16 || stringBytes > 192 * 1024) {
      return "[truncated]";
    }
    if (isSensitiveRendererKey(key)) {
      return "[redacted]";
    }
    if (typeof current === "string") {
      const clipped = current.length <= 16_384 ? current : `${current.slice(0, 16_384)}…`;
      stringBytes += Buffer.byteLength(clipped, "utf8");
      return stringBytes <= 192 * 1024 ? clipped : "[truncated]";
    }
    if (typeof current === "number" || typeof current === "boolean" || current === null) {
      return current;
    }
    if (Array.isArray(current)) {
      return current.slice(0, 256).map((item) => visit(item, depth + 1));
    }
    if (isRecord(current)) {
      const result: Record<string, unknown> = {};
      for (const [childKey, child] of Object.entries(current).slice(0, 256)) {
        const safeKey = childKey.length <= 128 ? childKey : `${childKey.slice(0, 128)}…`;
        if (!(safeKey in result)) result[safeKey] = visit(child, depth + 1, childKey);
      }
      return result;
    }
    return null;
  };
  return visit(value, 0);
}

function isSensitiveRendererKey(key: string): boolean {
  const normalized = key.toLowerCase();
  return (
    normalized.includes("token") ||
    normalized.includes("secret") ||
    normalized.includes("credential") ||
    normalized.includes("password") ||
    normalized.includes("apikey") ||
    normalized === "env" ||
    normalized.endsWith("env") ||
    normalized === "cwd" ||
    normalized.endsWith("cwd") ||
    normalized === "path" ||
    normalized.endsWith("path") ||
    normalized === "command" ||
    normalized.endsWith("command")
  );
}

function validateJsonTree(value: unknown): void {
  let nodes = 0;
  const stack: Array<{ value: unknown; depth: number }> = [{ value, depth: 0 }];
  while (stack.length > 0) {
    const current = stack.pop();
    if (!current) {
      break;
    }
    nodes += 1;
    if (nodes > MAX_JSON_NODES || current.depth > MAX_JSON_DEPTH) {
      throw new CodexProtocolError("Codex JSON exceeds structural limits");
    }
    if (typeof current.value === "number" && !Number.isFinite(current.value)) {
      throw new CodexProtocolError("Codex JSON contains a non-finite number");
    }
    if (Array.isArray(current.value)) {
      for (const child of current.value) {
        stack.push({ value: child, depth: current.depth + 1 });
      }
    } else if (isRecord(current.value)) {
      for (const child of Object.values(current.value)) {
        stack.push({ value: child, depth: current.depth + 1 });
      }
    }
  }
}

class BoundedLineDecoder {
  readonly #decoder = new TextDecoder("utf-8", { fatal: true });
  #pending = Buffer.alloc(0);
  public constructor(private readonly maxBytes: number) {}

  public push(chunk: Buffer): string[] {
    const lines: string[] = [];
    let offset = 0;
    while (offset < chunk.length) {
      const newline = chunk.indexOf(0x0a, offset);
      const end = newline < 0 ? chunk.length : newline;
      const segment = chunk.subarray(offset, end);
      if (this.#pending.length + segment.length > this.maxBytes) {
        throw new CodexProtocolError("Codex output line exceeds the configured byte limit");
      }
      if (segment.length > 0) {
        this.#pending = Buffer.concat([this.#pending, segment]);
      }
      if (newline < 0) {
        break;
      }
      lines.push(this.#decode());
      this.#pending = Buffer.alloc(0);
      offset = newline + 1;
    }
    return lines;
  }

  public finish(): string[] {
    if (this.#pending.length === 0) {
      return [];
    }
    const line = this.#decode();
    this.#pending = Buffer.alloc(0);
    return [line];
  }

  #decode(): string {
    const bytes = this.#pending.at(-1) === 0x0d ? this.#pending.subarray(0, -1) : this.#pending;
    try {
      return this.#decoder.decode(bytes);
    } catch (error) {
      throw new CodexProtocolError("Codex output is not valid UTF-8", { cause: error });
    }
  }
}

class BoundedTextTail {
  #buffer = Buffer.alloc(0);
  public constructor(private readonly maxBytes: number) {}
  public append(chunk: Buffer): string {
    const combined = Buffer.concat([this.#buffer, chunk]);
    this.#buffer = combined.length <= this.maxBytes
      ? combined
      : combined.subarray(combined.length - this.maxBytes);
    return this.text();
  }
  public text(): string {
    return this.#buffer.toString("utf8");
  }
}

async function flushWrite(child: ChildProcessWithoutNullStreams, payload: Buffer): Promise<void> {
  await new Promise<void>((resolve, reject) => {
    let callbackDone = false;
    let drainDone = true;
    let settled = false;
    const stream = child.stdin;
    const cleanup = (): void => {
      clearTimeout(timer);
      stream.off("drain", onDrain);
      stream.off("error", onError);
      stream.off("close", onClose);
    };
    const finish = (error?: Error): void => {
      if (settled) return;
      if (error) {
        settled = true;
        cleanup();
        reject(error);
      } else if (callbackDone && drainDone) {
        settled = true;
        cleanup();
        resolve();
      }
    };
    const onDrain = (): void => { drainDone = true; finish(); };
    const onError = (error: Error): void => finish(error);
    const onClose = (): void => finish(new Error("Codex stdin closed"));
    const timer = setTimeout(() => finish(new Error("Codex stdin write timed out")), 5_000);
    timer.unref();
    stream.once("error", onError);
    stream.once("close", onClose);
    const accepted = stream.write(payload, (error?: Error | null) => {
      if (error) finish(error);
      else { callbackDone = true; finish(); }
    });
    if (!accepted) {
      drainDone = false;
      stream.once("drain", onDrain);
    }
  });
}

async function verifyExactVersion(spec: CodexLaunchSpec): Promise<void> {
  const output = await new Promise<string>((resolve, reject) => {
    execFile(
      spec.executable,
      ["--version"],
      { cwd: spec.cwd, env: { ...spec.env }, windowsHide: true, timeout: 5_000, maxBuffer: 4_096 },
      (error, stdout) => error ? reject(new CodexTransportError(error.message, { cause: error })) : resolve(stdout),
    );
  });
  if (output.trim() !== `codex-cli ${spec.expectedVersion}`) {
    throw new CodexTransportError(`Codex version mismatch; expected ${spec.expectedVersion}`);
  }
}

function assertLaunchSpec(spec: CodexLaunchSpec): void {
  if (!path.isAbsolute(spec.executable) || !path.isAbsolute(spec.cwd) || !path.isAbsolute(spec.codexHome)) {
    throw new TypeError("Codex launch paths must be absolute");
  }
  if (spec.args.join("\0") !== ["app-server", "--stdio", "--strict-config"].join("\0")) {
    throw new TypeError("Codex launch arguments are not the fixed app-server contract");
  }
  if (Object.keys(spec.env).some((key) => key.toUpperCase() === "PATH" || key === "NODE_OPTIONS")) {
    throw new TypeError("Codex launch environment contains an injection-capable variable");
  }
}

function encodeLine(value: Record<string, unknown>, maxBytes: number): Buffer {
  validateJsonTree(value);
  const payload = Buffer.from(`${JSON.stringify(value)}\n`, "utf8");
  if (payload.length - 1 > maxBytes) {
    throw new CodexProtocolError("Codex outbound line exceeds the configured byte limit");
  }
  return payload;
}

function assertExactKeys(value: Record<string, unknown>, expected: readonly string[]): void {
  const actual = Object.keys(value).sort();
  const sorted = [...expected].sort();
  if (actual.length !== sorted.length || actual.some((key, index) => key !== sorted[index])) {
    throw new CodexProtocolError("Codex message contains unknown or missing fields");
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isRpcId(value: unknown): value is JsonRpcId {
  return (typeof value === "string" && value.length > 0 && value.length <= 256) || Number.isSafeInteger(value);
}

function isBoundedId(value: unknown): value is string {
  return typeof value === "string" && value.length > 0 && value.length <= 256;
}

function isOpaqueToken(value: string): boolean {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/u.test(value);
}

function validateAnswerValues(value: unknown): string[] {
  if (
    !Array.isArray(value) ||
    value.length < 1 ||
    value.length > 8 ||
    !value.every((item: unknown) => typeof item === "string" && item.length <= 8_192)
  ) {
    throw new TypeError("Codex user-input answer values are invalid");
  }
  return value.map((item: unknown) => String(item));
}

function stringField(value: Record<string, unknown>, key: string): string {
  return typeof value[key] === "string" ? value[key] : "";
}

function assertLimit(value: number, minimum: number, label: string): void {
  if (!Number.isSafeInteger(value) || value < minimum) {
    throw new TypeError(`${label} is invalid`);
  }
}

function describe(error: unknown): string {
  return error instanceof Error ? error.message : "unknown error";
}

async function waitForSpawn(child: ChildProcessWithoutNullStreams): Promise<void> {
  await new Promise<void>((resolve, reject) => {
    const cleanup = (): void => {
      child.off("spawn", onSpawn);
      child.off("error", onError);
    };
    const onSpawn = (): void => { cleanup(); resolve(); };
    const onError = (error: Error): void => { cleanup(); reject(error); };
    child.once("spawn", onSpawn);
    child.once("error", onError);
  });
}

async function waitForExit(child: ChildProcessWithoutNullStreams, timeoutMs: number): Promise<boolean> {
  if (child.exitCode !== null || child.signalCode !== null) return true;
  return await new Promise<boolean>((resolve) => {
    const timer = setTimeout(() => { cleanup(); resolve(false); }, timeoutMs);
    const onExit = (): void => { cleanup(); resolve(true); };
    const cleanup = (): void => { clearTimeout(timer); child.off("exit", onExit); };
    child.once("exit", onExit);
  });
}

async function terminateChildTree(child: ChildProcessWithoutNullStreams, force: boolean): Promise<void> {
  const pid = child.pid;
  if (!pid || child.exitCode !== null || child.signalCode !== null) return;
  const signal: NodeJS.Signals = force ? "SIGKILL" : "SIGTERM";
  if (process.platform === "win32") {
    const systemRoot = process.env.SystemRoot ?? process.env.SYSTEMROOT ?? process.env.WINDIR;
    if (!systemRoot || !path.isAbsolute(systemRoot)) {
      child.kill(signal);
      return;
    }
    const taskkill = path.join(systemRoot, "System32", "taskkill.exe");
    await new Promise<void>((resolve) => {
      const killer = spawn(taskkill, ["/PID", String(pid), "/T", ...(force ? ["/F"] : [])], {
        windowsHide: true,
        stdio: "ignore",
      });
      killer.once("error", () => { child.kill(signal); resolve(); });
      killer.once("exit", () => resolve());
    });
    return;
  }
  try {
    process.kill(-pid, signal);
  } catch {
    child.kill(signal);
  }
}

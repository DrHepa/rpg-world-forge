import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import path from "node:path";

import type {
  Error as StudioErrorEnvelope,
  Event as StudioEventEnvelope,
  Method as StudioMethod,
  Request as StudioRequestEnvelope,
  Response as StudioResponseEnvelope,
} from "../generated/studio-protocol";
import { describeProtocolErrors, validateStudioEnvelope } from "./protocol-validator";

export const DEFAULT_MAX_NDJSON_LINE_BYTES = 1024 * 1024;
export const DEFAULT_MAX_STDERR_BYTES = 64 * 1024;
export const DEFAULT_MAX_PENDING_REQUESTS = 128;
export const DEFAULT_MAX_OUTSTANDING_REQUEST_BYTES = 8 * 1024 * 1024;
export const DEFAULT_MAX_IGNORED_REQUEST_IDS = 1024;

const PROTOCOL = "rpg-world-forge.studio_protocol" as const;
const PROTOCOL_VERSION = 1 as const;

export interface FixedSpawnSpec {
  executable: string;
  args: readonly string[];
  cwd?: string;
  env: Readonly<Record<string, string>>;
}

export type TransportState = "stopped" | "starting" | "running" | "crashed";

export type TransportEvent =
  | { type: "state"; state: TransportState; pid: number | null; message: string }
  | { type: "event"; envelope: StudioEventEnvelope }
  | { type: "stderr"; text: string };

export class StudioTransportError extends Error {
  public constructor(message: string) {
    super(message);
    this.name = "StudioTransportError";
  }
}

export class StudioProtocolError extends StudioTransportError {
  public constructor(message: string, options?: ErrorOptions) {
    super(message);
    this.name = "StudioProtocolError";
    if (options?.cause !== undefined) {
      this.cause = options.cause;
    }
  }
}

export class StudioRequestTimeoutError extends StudioTransportError {
  public constructor(requestId: string) {
    super(`Studio request ${requestId} timed out`);
    this.name = "StudioRequestTimeoutError";
  }
}

export class StudioRequestCancelledError extends StudioTransportError {
  public constructor(requestId: string) {
    super(`Studio request ${requestId} was cancelled`);
    this.name = "StudioRequestCancelledError";
  }
}

export class StudioOverloadError extends StudioTransportError {
  public constructor(message: string) {
    super(message);
    this.name = "StudioOverloadError";
  }
}

class BoundedLineDecoder {
  readonly #decoder = new TextDecoder("utf-8", { fatal: true });
  readonly #maxLineBytes: number;
  #pending = Buffer.alloc(0);

  public constructor(maxLineBytes: number) {
    if (!Number.isSafeInteger(maxLineBytes) || maxLineBytes < 32) {
      throw new TypeError("maxLineBytes must be an integer of at least 32");
    }
    this.#maxLineBytes = maxLineBytes;
  }

  public push(chunk: Buffer): string[] {
    const lines: string[] = [];
    let offset = 0;
    while (offset < chunk.length) {
      const newline = chunk.indexOf(0x0a, offset);
      const end = newline === -1 ? chunk.length : newline;
      const segment = chunk.subarray(offset, end);
      if (this.#pending.length + segment.length > this.#maxLineBytes) {
        throw new StudioProtocolError(
          `Studio output exceeds the ${this.#maxLineBytes}-byte NDJSON line limit`,
        );
      }
      if (segment.length > 0) {
        this.#pending = Buffer.concat([this.#pending, segment]);
      }
      if (newline === -1) {
        break;
      }
      lines.push(this.#decodePending());
      this.#pending = Buffer.alloc(0);
      offset = newline + 1;
    }
    return lines;
  }

  public finish(): string[] {
    if (this.#pending.length === 0) {
      return [];
    }
    const finalLine = this.#decodePending();
    this.#pending = Buffer.alloc(0);
    return [finalLine];
  }

  #decodePending(): string {
    let payload = this.#pending;
    if (payload.at(-1) === 0x0d) {
      payload = payload.subarray(0, -1);
    }
    try {
      return this.#decoder.decode(payload);
    } catch (error) {
      throw new StudioProtocolError("Studio output is not valid UTF-8", { cause: error });
    }
  }
}

class BoundedTextTail {
  readonly #maxBytes: number;
  #buffer = Buffer.alloc(0);

  public constructor(maxBytes: number) {
    this.#maxBytes = maxBytes;
  }

  public append(chunk: Buffer): string {
    const combined = Buffer.concat([this.#buffer, chunk]);
    this.#buffer =
      combined.length <= this.#maxBytes ? combined : combined.subarray(combined.length - this.#maxBytes);
    return this.#buffer.toString("utf8");
  }

  public text(): string {
    return this.#buffer.toString("utf8");
  }
}

interface PendingRequest {
  resolve: (envelope: StudioResponseEnvelope | StudioErrorEnvelope) => void;
  reject: (error: Error) => void;
  timer: NodeJS.Timeout;
  payload: Buffer;
  payloadBytes: number;
  writeState: "queued" | "writing" | "sent";
}

export interface NdjsonSupervisorOptions {
  maxLineBytes?: number;
  maxStderrBytes?: number;
  defaultTimeoutMs?: number;
  maxPendingRequests?: number;
  maxOutstandingRequestBytes?: number;
  maxIgnoredRequestIds?: number;
}

export interface TransportDiagnostics {
  pendingRequests: number;
  outstandingRequestBytes: number;
  queuedWrites: number;
  backpressureWaits: number;
  ignoredReplyIds: number;
}

export class NdjsonSupervisor {
  readonly #spec: FixedSpawnSpec;
  readonly #maxLineBytes: number;
  readonly #defaultTimeoutMs: number;
  readonly #maxPendingRequests: number;
  readonly #maxOutstandingRequestBytes: number;
  readonly #maxIgnoredRequestIds: number;
  readonly #stderr: BoundedTextTail;
  readonly #listeners = new Set<(event: TransportEvent) => void>();
  readonly #pending = new Map<string, PendingRequest>();
  readonly #ignoredRequestIds = new Set<string>();
  readonly #writeQueue: string[] = [];
  #decoder: BoundedLineDecoder;
  #child: ChildProcessWithoutNullStreams | null = null;
  #state: TransportState = "stopped";
  #expectedStop = false;
  #protocolFailed = false;
  #writing = false;
  #outstandingRequestBytes = 0;
  #backpressureWaits = 0;
  #generation = 0;

  public constructor(spec: FixedSpawnSpec, options: NdjsonSupervisorOptions = {}) {
    assertFixedSpawnSpec(spec);
    this.#spec = Object.freeze({
      executable: spec.executable,
      args: Object.freeze([...spec.args]),
      cwd: spec.cwd,
      env: Object.freeze({ ...spec.env }),
    });
    this.#maxLineBytes = options.maxLineBytes ?? DEFAULT_MAX_NDJSON_LINE_BYTES;
    this.#defaultTimeoutMs = options.defaultTimeoutMs ?? 10_000;
    this.#maxPendingRequests =
      options.maxPendingRequests ?? DEFAULT_MAX_PENDING_REQUESTS;
    this.#maxOutstandingRequestBytes =
      options.maxOutstandingRequestBytes ?? DEFAULT_MAX_OUTSTANDING_REQUEST_BYTES;
    this.#maxIgnoredRequestIds =
      options.maxIgnoredRequestIds ?? DEFAULT_MAX_IGNORED_REQUEST_IDS;
    assertPositiveLimit(this.#maxPendingRequests, "maxPendingRequests");
    assertPositiveLimit(
      this.#maxOutstandingRequestBytes,
      "maxOutstandingRequestBytes",
    );
    assertPositiveLimit(this.#maxIgnoredRequestIds, "maxIgnoredRequestIds");
    this.#stderr = new BoundedTextTail(options.maxStderrBytes ?? DEFAULT_MAX_STDERR_BYTES);
    this.#decoder = new BoundedLineDecoder(this.#maxLineBytes);
  }

  public get state(): TransportState {
    return this.#state;
  }

  public get pid(): number | null {
    return this.#child?.pid ?? null;
  }

  public get stderrTail(): string {
    return this.#stderr.text();
  }

  public get diagnostics(): TransportDiagnostics {
    return {
      pendingRequests: this.#pending.size,
      outstandingRequestBytes: this.#outstandingRequestBytes,
      queuedWrites: this.#writeQueue.length + (this.#writing ? 1 : 0),
      backpressureWaits: this.#backpressureWaits,
      ignoredReplyIds: this.#ignoredRequestIds.size,
    };
  }

  public subscribe(listener: (event: TransportEvent) => void): () => void {
    this.#listeners.add(listener);
    return () => this.#listeners.delete(listener);
  }

  public async start(): Promise<void> {
    if (this.#state === "running") {
      return;
    }
    if (this.#child) {
      throw new StudioTransportError("Studio child is already starting or stopping");
    }
    this.#expectedStop = false;
    this.#protocolFailed = false;
    this.#ignoredRequestIds.clear();
    this.#generation += 1;
    this.#writing = false;
    this.#decoder = new BoundedLineDecoder(this.#maxLineBytes);
    this.#setState("starting", "Starting Forge Studio service");

    let child: ChildProcessWithoutNullStreams;
    try {
      child = spawn(this.#spec.executable, [...this.#spec.args], {
        cwd: this.#spec.cwd,
        detached: process.platform !== "win32",
        env: { ...this.#spec.env },
        shell: false,
        stdio: ["pipe", "pipe", "pipe"],
        windowsHide: true,
      });
    } catch (error) {
      const failure = new StudioTransportError(
        `Failed to spawn Forge Studio service: ${describeError(error)}`,
      );
      this.#setState("crashed", failure.message);
      throw failure;
    }
    this.#child = child;
    this.#attachChild(child);

    await new Promise<void>((resolve, reject) => {
      const onSpawn = (): void => {
        cleanup();
        this.#setState("running", "Forge Studio service is running");
        resolve();
      };
      const onError = (error: Error): void => {
        cleanup();
        reject(error);
      };
      const cleanup = (): void => {
        child.off("spawn", onSpawn);
        child.off("error", onError);
      };
      child.once("spawn", onSpawn);
      child.once("error", onError);
    });
  }

  public async request(
    requestId: string,
    method: StudioMethod,
    params: Record<string, unknown>,
    timeoutMs = this.#defaultTimeoutMs,
  ): Promise<StudioResponseEnvelope | StudioErrorEnvelope> {
    if (this.#state !== "running" || !this.#child) {
      throw new StudioTransportError("Forge Studio service is not running");
    }
    if (this.#pending.has(requestId) || this.#ignoredRequestIds.has(requestId)) {
      throw new StudioTransportError(`Studio request ID ${requestId} is already in use`);
    }
    if (!Number.isSafeInteger(timeoutMs) || timeoutMs < 100 || timeoutMs > 60_000) {
      throw new TypeError("timeoutMs must be an integer from 100 to 60000");
    }

    const envelope: StudioRequestEnvelope = {
      protocol: PROTOCOL,
      protocol_version: PROTOCOL_VERSION,
      kind: "request",
      request_id: requestId,
      method,
      params,
    };
    if (!validateStudioEnvelope(envelope)) {
      throw new StudioProtocolError(`Invalid Studio request: ${describeProtocolErrors()}`);
    }
    const payload = Buffer.from(`${JSON.stringify(envelope)}\n`, "utf8");
    if (payload.length - 1 > this.#maxLineBytes) {
      throw new StudioProtocolError(
        `Studio request exceeds the ${this.#maxLineBytes}-byte NDJSON line limit`,
      );
    }
    if (this.#pending.size >= this.#maxPendingRequests) {
      throw new StudioOverloadError(
        `Studio request limit reached (${this.#maxPendingRequests} pending requests)`,
      );
    }
    if (
      payload.length > this.#maxOutstandingRequestBytes ||
      this.#outstandingRequestBytes > this.#maxOutstandingRequestBytes - payload.length
    ) {
      throw new StudioOverloadError(
        `Studio request byte limit reached (${this.#maxOutstandingRequestBytes} outstanding bytes)`,
      );
    }

    return await new Promise<StudioResponseEnvelope | StudioErrorEnvelope>((resolve, reject) => {
      const timer = setTimeout(() => {
        this.#abandonRequest(requestId, new StudioRequestTimeoutError(requestId));
      }, timeoutMs);
      timer.unref();
      this.#pending.set(requestId, {
        resolve,
        reject,
        timer,
        payload,
        payloadBytes: payload.length,
        writeState: "queued",
      });
      this.#outstandingRequestBytes += payload.length;
      this.#writeQueue.push(requestId);
      this.#pumpWrites();
    });
  }

  public cancelRequest(requestId: string): boolean {
    return this.#abandonRequest(requestId, new StudioRequestCancelledError(requestId));
  }

  public async stop(): Promise<void> {
    const child = this.#child;
    if (!child) {
      this.#setState("stopped", "Forge Studio service is stopped");
      return;
    }
    this.#expectedStop = true;
    this.#rejectAll(new StudioTransportError("Forge Studio service stopped"));
    await terminateChildTree(child, false);
    if (child.exitCode === null && child.signalCode === null) {
      const exited = await waitForExit(child, 1_000);
      if (!exited) {
        await terminateChildTree(child, true);
        await waitForExit(child, 1_000);
      }
    }
  }

  #pumpWrites(): void {
    if (this.#writing || this.#state !== "running") {
      return;
    }
    const child = this.#child;
    if (!child) {
      return;
    }
    let requestId: string | undefined;
    let pending: PendingRequest | undefined;
    while ((requestId = this.#writeQueue.shift()) !== undefined) {
      pending = this.#pending.get(requestId);
      if (pending) {
        break;
      }
    }
    if (!requestId || !pending) {
      return;
    }

    this.#writing = true;
    pending.writeState = "writing";
    const generation = this.#generation;
    void this.#flushWrite(child, pending.payload)
      .then(() => {
        const current = this.#pending.get(requestId);
        if (current === pending) {
          current.writeState = "sent";
        }
      })
      .catch((error: unknown) => {
        if (
          this.#expectedStop ||
          this.#protocolFailed ||
          this.#state !== "running" ||
          this.#child !== child
        ) {
          return;
        }
        const failure = new StudioTransportError(
          `Failed to write Studio request: ${describeError(error)}`,
        );
        const current = this.#takePending(requestId);
        current?.reject(failure);
        this.#rejectAll(failure);
        void terminateChildTree(child, true).catch(() => child.kill());
      })
      .finally(() => {
        if (this.#generation !== generation) {
          return;
        }
        this.#writing = false;
        this.#pumpWrites();
      });
  }

  async #flushWrite(child: ChildProcessWithoutNullStreams, payload: Buffer): Promise<void> {
    await new Promise<void>((resolve, reject) => {
      const stream = child.stdin;
      let callbackDone = false;
      let drainDone = true;
      let settled = false;

      const cleanup = (): void => {
        stream.off("drain", onDrain);
        stream.off("error", onError);
        stream.off("close", onClose);
      };
      const finish = (error?: Error): void => {
        if (settled) {
          return;
        }
        if (error) {
          settled = true;
          cleanup();
          reject(error);
          return;
        }
        if (callbackDone && drainDone) {
          settled = true;
          cleanup();
          resolve();
        }
      };
      const onDrain = (): void => {
        drainDone = true;
        finish();
      };
      const onError = (error: Error): void => finish(error);
      const onClose = (): void => finish(new Error("Studio service stdin closed"));

      stream.once("error", onError);
      stream.once("close", onClose);
      const accepted = stream.write(payload, (error?: Error | null) => {
        if (error) {
          finish(error);
          return;
        }
        callbackDone = true;
        finish();
      });
      if (!accepted) {
        drainDone = false;
        this.#backpressureWaits += 1;
        stream.once("drain", onDrain);
      }
    });
  }

  #attachChild(child: ChildProcessWithoutNullStreams): void {
    child.stdin.on("error", (error) => {
      if (this.#expectedStop || this.#child !== child) {
        return;
      }
      this.#failProtocol(
        new StudioTransportError(
          `Forge Studio service stdin failed: ${describeError(error)}`,
        ),
      );
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
      this.#emit({ type: "stderr", text: this.#stderr.append(chunk) });
    });
    child.once("error", (error) => this.#finalizeChild(child, `spawn error: ${error.message}`));
    child.once("exit", (code, signal) => {
      const detail = signal ? `signal ${signal}` : `exit code ${String(code)}`;
      this.#finalizeChild(child, detail);
    });
    child.once("close", (code, signal) => {
      const detail = signal ? `signal ${signal}` : `close code ${String(code)}`;
      this.#finalizeChild(child, detail);
    });
  }

  #finalizeChild(child: ChildProcessWithoutNullStreams, detail: string): void {
    if (this.#child !== child) {
      return;
    }
    this.#child = null;
    this.#generation += 1;
    this.#writing = false;
    this.#ignoredRequestIds.clear();
    const expected = this.#expectedStop;
    this.#expectedStop = false;
    if (expected) {
      this.#rejectAll(new StudioTransportError("Forge Studio service stopped"));
      this.#setState("stopped", "Forge Studio service is stopped");
      return;
    }
    const message = this.#protocolFailed
      ? `Forge Studio service was terminated after a protocol failure (${detail})`
      : `Forge Studio service exited unexpectedly (${detail})`;
    this.#rejectAll(new StudioTransportError(message));
    this.#setState("crashed", message);
  }

  #handleLine(line: string): void {
    let value: unknown;
    try {
      value = JSON.parse(line);
    } catch (error) {
      throw new StudioProtocolError("Forge Studio service emitted malformed JSON", { cause: error });
    }
    if (!validateStudioEnvelope(value)) {
      throw new StudioProtocolError(
        `Forge Studio service emitted an invalid envelope: ${describeProtocolErrors()}`,
      );
    }
    if (value.kind === "event") {
      this.#emit({ type: "event", envelope: value });
      return;
    }
    if (value.kind === "request") {
      throw new StudioProtocolError("Forge Studio service emitted a request envelope");
    }
    const requestId = value.request_id;
    if (requestId === null) {
      throw new StudioProtocolError("Forge Studio service emitted an uncorrelated reply");
    }
    if (this.#ignoredRequestIds.delete(requestId)) {
      return;
    }
    const pending = this.#pending.get(requestId);
    if (!pending) {
      throw new StudioProtocolError(
        `Forge Studio service emitted an unexpected reply for ${requestId}`,
      );
    }
    this.#takePending(requestId);
    pending.resolve(value);
  }

  #failProtocol(error: unknown): void {
    if (this.#protocolFailed) {
      return;
    }
    this.#protocolFailed = true;
    const failure =
      error instanceof StudioTransportError
        ? error
        : new StudioProtocolError("Forge Studio service protocol failed", { cause: error });
    this.#rejectAll(failure);
    this.#setState("crashed", failure.message);
    const child = this.#child;
    if (child) {
      void terminateChildTree(child, true).catch(() => child.kill());
    }
  }

  #rejectAll(error: Error): void {
    for (const pending of this.#pending.values()) {
      clearTimeout(pending.timer);
      pending.reject(error);
    }
    this.#pending.clear();
    this.#outstandingRequestBytes = 0;
    this.#writeQueue.length = 0;
  }

  #takePending(requestId: string): PendingRequest | undefined {
    const pending = this.#pending.get(requestId);
    if (!pending) {
      return undefined;
    }
    clearTimeout(pending.timer);
    this.#pending.delete(requestId);
    if (pending.writeState === "queued") {
      const queuedIndex = this.#writeQueue.indexOf(requestId);
      if (queuedIndex !== -1) {
        this.#writeQueue.splice(queuedIndex, 1);
      }
    }
    this.#outstandingRequestBytes -= pending.payloadBytes;
    if (this.#outstandingRequestBytes < 0) {
      this.#outstandingRequestBytes = 0;
    }
    return pending;
  }

  #abandonRequest(requestId: string, error: Error): boolean {
    const pending = this.#takePending(requestId);
    if (!pending) {
      return false;
    }

    if (pending.writeState !== "queued" && !this.#rememberIgnoredRequest(requestId)) {
      this.#failProtocol(
        new StudioProtocolError(
          `Studio ignored-reply capacity reached (${this.#maxIgnoredRequestIds} request IDs)`,
        ),
      );
    }
    pending.reject(error);

    if (pending.writeState === "writing" && !this.#protocolFailed) {
      this.#failProtocol(
        new StudioProtocolError(
          `Studio request ${requestId} was abandoned while its write was incomplete`,
        ),
      );
    }
    return true;
  }

  #rememberIgnoredRequest(requestId: string): boolean {
    if (this.#ignoredRequestIds.has(requestId)) {
      return true;
    }
    if (this.#ignoredRequestIds.size >= this.#maxIgnoredRequestIds) {
      return false;
    }
    this.#ignoredRequestIds.add(requestId);
    return true;
  }

  #setState(state: TransportState, message: string): void {
    this.#state = state;
    this.#emit({ type: "state", state, pid: this.pid, message });
  }

  #emit(event: TransportEvent): void {
    for (const listener of this.#listeners) {
      listener(event);
    }
  }
}

function assertFixedSpawnSpec(spec: FixedSpawnSpec): void {
  if (!path.isAbsolute(spec.executable) || containsControl(spec.executable)) {
    throw new TypeError("Studio executable must be an absolute path without control characters");
  }
  if (spec.cwd && (!path.isAbsolute(spec.cwd) || containsControl(spec.cwd))) {
    throw new TypeError("Studio cwd must be an absolute path without control characters");
  }
  for (const argument of spec.args) {
    if (containsControl(argument)) {
      throw new TypeError("Studio arguments must not contain control characters");
    }
  }
}

function containsControl(value: string): boolean {
  return [...value].some((character) => {
    const code = character.codePointAt(0);
    return code !== undefined && (code <= 0x1f || code === 0x7f);
  });
}

function assertPositiveLimit(value: number, name: string): void {
  if (!Number.isSafeInteger(value) || value < 1) {
    throw new TypeError(`${name} must be a positive safe integer`);
  }
}

function describeError(error: unknown): string {
  return error instanceof Error ? error.message : "unknown process error";
}

async function waitForExit(child: ChildProcessWithoutNullStreams, timeoutMs: number): Promise<boolean> {
  if (child.exitCode !== null || child.signalCode !== null) {
    return true;
  }
  return await new Promise<boolean>((resolve) => {
    const timer = setTimeout(() => {
      cleanup();
      resolve(false);
    }, timeoutMs);
    const onExit = (): void => {
      cleanup();
      resolve(true);
    };
    const cleanup = (): void => {
      clearTimeout(timer);
      child.off("exit", onExit);
    };
    child.once("exit", onExit);
  });
}

async function terminateChildTree(
  child: ChildProcessWithoutNullStreams,
  force: boolean,
): Promise<void> {
  const pid = child.pid;
  if (!pid || child.exitCode !== null || child.signalCode !== null) {
    return;
  }
  if (process.platform !== "win32") {
    try {
      process.kill(-pid, force ? "SIGKILL" : "SIGTERM");
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== "ESRCH") {
        throw error;
      }
    }
    return;
  }

  const systemRoot = process.env.SystemRoot ?? process.env.WINDIR;
  if (!systemRoot || !path.win32.isAbsolute(systemRoot)) {
    child.kill();
    return;
  }
  const taskkill = path.win32.join(systemRoot, "System32", "taskkill.exe");
  await new Promise<void>((resolve) => {
    const killer = spawn(taskkill, ["/PID", String(pid), "/T", ...(force ? ["/F"] : [])], {
      shell: false,
      stdio: "ignore",
      windowsHide: true,
    });
    killer.once("error", () => {
      child.kill();
      resolve();
    });
    killer.once("exit", () => resolve());
  });
}

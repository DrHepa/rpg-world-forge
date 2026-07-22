import { randomUUID } from "node:crypto";

import type {
  ForgeServiceStatus,
  StudioActivityEvent,
  StudioReadMethod,
  StudioReplyEnvelope,
} from "../shared/studio-api";
import { STUDIO_METHODS } from "../shared/studio-api";
import {
  NdjsonSupervisor,
  StudioRequestCancelledError,
  StudioRequestTimeoutError,
  type FixedSpawnSpec,
  type TransportEvent,
} from "./ndjson-supervisor";

interface InitializationResult {
  [key: string]: unknown;
  service: string;
  service_version: number;
  protocol: string;
  protocol_version: number;
  methods: string[];
  capabilities: Record<string, unknown>;
}

export interface ForgeServiceClient {
  readonly status: ForgeServiceStatus;
  subscribe(listener: (event: StudioActivityEvent) => void): () => void;
  initialize(): Promise<StudioReplyEnvelope>;
  request(
    requestId: string,
    method: StudioReadMethod,
    params: Record<string, unknown>,
    timeoutMs: number,
  ): Promise<StudioReplyEnvelope>;
  stop(): Promise<void>;
}

export class ForgeServiceSupervisor implements ForgeServiceClient {
  readonly #transport: NdjsonSupervisor;
  readonly #listeners = new Set<(event: StudioActivityEvent) => void>();
  #status: ForgeServiceStatus = {
    state: "stopped",
    message: "Forge Studio service is stopped",
    pid: null,
  };
  #startPromise: Promise<StudioReplyEnvelope> | null = null;

  public constructor(spec: FixedSpawnSpec) {
    this.#transport = new NdjsonSupervisor(spec);
    this.#transport.subscribe((event) => this.#handleTransportEvent(event));
  }

  public get status(): ForgeServiceStatus {
    return { ...this.#status };
  }

  public subscribe(listener: (event: StudioActivityEvent) => void): () => void {
    this.#listeners.add(listener);
    return () => this.#listeners.delete(listener);
  }

  public async initialize(): Promise<StudioReplyEnvelope> {
    if (this.#status.state === "ready") {
      return await this.#transport.request(
        randomUUID(),
        "service.initialize",
        {},
        10_000,
      );
    }
    if (!this.#startPromise) {
      this.#startPromise = this.#startAndHandshake().finally(() => {
        this.#startPromise = null;
      });
    }
    return await this.#startPromise;
  }

  public async request(
    requestId: string,
    method: StudioReadMethod,
    params: Record<string, unknown>,
    timeoutMs: number,
  ): Promise<StudioReplyEnvelope> {
    if (this.#status.state !== "ready") {
      await this.initialize();
    }
    return await this.#transport.request(requestId, method, params, timeoutMs);
  }

  public async stop(): Promise<void> {
    await this.#transport.stop();
    this.#setStatus({
      state: "stopped",
      message: "Forge Studio service is stopped",
      pid: null,
    });
  }

  async #startAndHandshake(): Promise<StudioReplyEnvelope> {
    this.#setStatus({
      state: "starting",
      message: "Starting Forge Studio service",
      pid: null,
    });
    try {
      await this.#transport.start();
      const reply = await this.#transport.request(
        randomUUID(),
        "service.initialize",
        {},
        10_000,
      );
      if (reply.kind === "error") {
        throw new Error(`Forge Studio handshake failed: ${reply.error.message}`);
      }
      assertInitializationResult(reply.result);
      this.#setStatus({
        state: "ready",
        message: "Forge Studio service is ready",
        pid: this.#transport.pid,
      });
      return reply;
    } catch (error) {
      const message = describeError(error);
      this.#setStatus({ state: "unavailable", message, pid: this.#transport.pid });
      await this.#transport.stop().catch(() => undefined);
      throw error;
    }
  }

  #handleTransportEvent(event: TransportEvent): void {
    if (event.type === "event") {
      this.#emit({ type: "studio-event", envelope: event.envelope });
      return;
    }
    if (event.type === "stderr") {
      this.#emit({ type: "service-stderr", text: event.text });
      return;
    }
    if (event.state === "crashed") {
      this.#setStatus({ state: "crashed", message: event.message, pid: null });
    }
  }

  #setStatus(status: ForgeServiceStatus): void {
    this.#status = status;
    this.#emit({ type: "service-status", status: { ...status } });
  }

  #emit(event: StudioActivityEvent): void {
    for (const listener of this.#listeners) {
      listener(event);
    }
  }
}

export class UnavailableForgeService implements ForgeServiceClient {
  readonly #listeners = new Set<(event: StudioActivityEvent) => void>();

  public constructor(private readonly reason: string) {}

  public get status(): ForgeServiceStatus {
    return { state: "unavailable", message: this.reason, pid: null };
  }

  public subscribe(listener: (event: StudioActivityEvent) => void): () => void {
    this.#listeners.add(listener);
    queueMicrotask(() => listener({ type: "service-status", status: this.status }));
    return () => this.#listeners.delete(listener);
  }

  public initialize(): Promise<never> {
    return Promise.reject(new Error(this.reason));
  }

  public request(
    requestId: string,
    method: StudioReadMethod,
    params: Record<string, unknown>,
    timeoutMs: number,
  ): Promise<never> {
    void requestId;
    void method;
    void params;
    void timeoutMs;
    return Promise.reject(new Error(this.reason));
  }

  public stop(): Promise<void> {
    return Promise.resolve();
  }
}

function assertInitializationResult(value: Record<string, unknown>): asserts value is InitializationResult {
  const methods = value.methods;
  if (
    value.service !== "rpg-world-forge.studio" ||
    value.service_version !== 1 ||
    value.protocol !== "rpg-world-forge.studio_protocol" ||
    value.protocol_version !== 1 ||
    !Array.isArray(methods) ||
    !methods.every((method) => typeof method === "string") ||
    ![...STUDIO_METHODS].every((method) => methods.includes(method)) ||
    typeof value.capabilities !== "object" ||
    value.capabilities === null ||
    Array.isArray(value.capabilities)
  ) {
    throw new Error("Forge Studio service returned an incompatible handshake");
  }
}

export function describeError(error: unknown): string {
  if (error instanceof StudioRequestTimeoutError) {
    return "Forge Studio service handshake timed out";
  }
  if (error instanceof StudioRequestCancelledError) {
    return "Forge Studio service handshake was cancelled";
  }
  if (error instanceof Error) {
    return error.message;
  }
  return "Unknown Forge Studio service failure";
}

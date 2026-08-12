import { randomUUID } from "node:crypto";

import type {
    ForgeServiceStatus,
    StudioActivityEvent,
    StudioCapabilityMethod,
    StudioReplyEnvelope,
    StudioV4Method,
    StudioV4ReplyEnvelope,
    StudioV5Method,
    StudioV5ReplyEnvelope,
} from "../shared/studio-api";
import {
    STUDIO_METHODS,
    STUDIO_V4_METHODS,
    STUDIO_V5_METHODS,
} from "../shared/studio-api";
import type { Method as StudioV2Method } from "../generated/studio-protocol-v2";
import type { Method as StudioV3Method } from "../generated/studio-protocol-v3";
import {
    NdjsonSupervisor,
    StudioRequestCancelledError,
    StudioRequestTimeoutError,
    type FixedSpawnSpec,
    type StudioProtocolVersion,
    type StudioV2ReplyEnvelope,
    type StudioV3ReplyEnvelope,
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

type ForgeServiceMethod =
    | StudioCapabilityMethod
    | StudioV2Method
    | StudioV3Method
    | StudioV4Method
    | StudioV5Method
    | "workspace.get";
type ForgeServiceReplyEnvelope =
    | StudioReplyEnvelope
    | StudioV2ReplyEnvelope
    | StudioV3ReplyEnvelope
    | StudioV4ReplyEnvelope
    | StudioV5ReplyEnvelope;
type RuntimeValidatedTransportRequest = (
    requestId: string,
    method: ForgeServiceMethod,
    params: Record<string, unknown>,
    timeoutMs: number,
    protocolVersion: StudioProtocolVersion,
) => Promise<ForgeServiceReplyEnvelope>;

export interface ForgeServiceClient {
    readonly status: ForgeServiceStatus;
    subscribe(listener: (event: StudioActivityEvent) => void): () => void;
    initialize(): Promise<StudioReplyEnvelope>;
    getWorkspace(workspaceId: string): Promise<ForgeWorkspaceBinding>;
    request(
        requestId: string,
        method: ForgeServiceMethod,
        params: Record<string, unknown>,
        timeoutMs: number,
        protocolVersion?: StudioProtocolVersion,
    ): Promise<ForgeServiceReplyEnvelope>;
    stop(): Promise<void>;
}

export interface ForgeWorkspaceBinding {
    workspaceId: string;
    worldRoot: string;
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
    #stopPromise: Promise<void> | null = null;
    #stopping = false;
    #stopped = false;

    public constructor(spec: FixedSpawnSpec) {
        this.#transport = new NdjsonSupervisor(spec);
        this.#transport.subscribe((event) => this.#handleTransportEvent(event));
    }

    public get status(): ForgeServiceStatus {
        return { ...this.#status };
    }

    public subscribe(
        listener: (event: StudioActivityEvent) => void,
    ): () => void {
        this.#listeners.add(listener);
        return () => this.#listeners.delete(listener);
    }

    public async initialize(): Promise<StudioReplyEnvelope> {
        if (this.#stopping || this.#stopped) {
            throw new Error("Forge Studio service is stopping");
        }
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
        method: ForgeServiceMethod,
        params: Record<string, unknown>,
        timeoutMs: number,
        protocolVersion: StudioProtocolVersion = 1,
    ): Promise<ForgeServiceReplyEnvelope> {
        if (this.#stopping || this.#stopped) {
            throw new Error("Forge Studio service is stopping");
        }
        if (this.#status.state !== "ready") {
            await this.initialize();
        }
        const runtimeValidatedRequest = this.#transport.request.bind(
            this.#transport,
        ) as RuntimeValidatedTransportRequest;
        return await runtimeValidatedRequest(
            requestId,
            method,
            params,
            timeoutMs,
            protocolVersion,
        );
    }

    public async getWorkspace(
        workspaceId: string,
    ): Promise<ForgeWorkspaceBinding> {
        const reply = await this.request(
            randomUUID(),
            "workspace.get",
            { workspace_id: workspaceId },
            10_000,
        );
        if (reply.kind === "error") {
            throw new Error(
                `Forge workspace lookup failed: ${reply.error.message}`,
            );
        }
        return parseWorkspaceBinding(reply.result, workspaceId);
    }

    public stop(): Promise<void> {
        if (this.#stopPromise) {
            return this.#stopPromise;
        }
        this.#stopping = true;
        const stopping = this.#transport.stop().then(
            () => {
                this.#stopped = true;
                this.#stopping = false;
                this.#setStatus({
                    state: "stopped",
                    message: "Forge Studio service is stopped",
                    pid: null,
                });
            },
            (error: unknown) => {
                this.#stopping = false;
                if (this.#stopPromise === stopping) {
                    this.#stopPromise = null;
                }
                this.#setStatus({
                    state: "crashed",
                    message: describeError(error),
                    pid: this.#transport.pid,
                });
                throw error;
            },
        );
        this.#stopPromise = stopping;
        return stopping;
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
                throw new Error(
                    `Forge Studio handshake failed: ${reply.error.message}`,
                );
            }
            assertInitializationResult(reply.result);
            let evidenceReply: StudioV4ReplyEnvelope;
            try {
                evidenceReply = await this.#transport.request(
                    randomUUID(),
                    "service.initialize",
                    {},
                    10_000,
                    4,
                );
            } catch (error) {
                throw new Error(
                    `Forge Studio evidence handshake failed: ${describeError(error)}`,
                    { cause: error },
                );
            }
            if (evidenceReply.kind === "error") {
                throw new Error(
                    `Forge Studio evidence handshake failed: ${evidenceReply.error.message}`,
                );
            }
            assertEvidenceInitializationResult(evidenceReply.result);
            let authorityReply: StudioV5ReplyEnvelope;
            try {
                authorityReply = await this.#transport.request(
                    randomUUID(),
                    "service.initialize",
                    {},
                    10_000,
                    5,
                );
            } catch (error) {
                throw new Error(
                    `Forge Studio authority handshake failed: ${describeError(error)}`,
                    { cause: error },
                );
            }
            if (authorityReply.kind === "error") {
                throw new Error(
                    `Forge Studio authority handshake failed: ${authorityReply.error.message}`,
                );
            }
            assertAuthorityInitializationResult(authorityReply.result);
            if (this.#stopping || this.#stopped) {
                throw new Error("Forge Studio service stopped during startup");
            }
            this.#setStatus({
                state: "ready",
                message: "Forge Studio service is ready",
                pid: this.#transport.pid,
            });
            return reply;
        } catch (error) {
            if (this.#stopping || this.#stopped) {
                throw error;
            }
            const message = describeError(error);
            this.#setStatus({
                state: "unavailable",
                message,
                pid: this.#transport.pid,
            });
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
        if (event.state === "crashed" && !this.#stopping && !this.#stopped) {
            this.#setStatus({
                state: "crashed",
                message: event.message,
                pid: null,
            });
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

    public subscribe(
        listener: (event: StudioActivityEvent) => void,
    ): () => void {
        this.#listeners.add(listener);
        queueMicrotask(() =>
            listener({ type: "service-status", status: this.status }),
        );
        return () => this.#listeners.delete(listener);
    }

    public initialize(): Promise<never> {
        return Promise.reject(new Error(this.reason));
    }

    public request(
        requestId: string,
        method: ForgeServiceMethod,
        params: Record<string, unknown>,
        timeoutMs: number,
        protocolVersion: StudioProtocolVersion = 1,
    ): Promise<never> {
        void requestId;
        void method;
        void params;
        void timeoutMs;
        void protocolVersion;
        return Promise.reject(new Error(this.reason));
    }

    public getWorkspace(workspaceId: string): Promise<never> {
        void workspaceId;
        return Promise.reject(new Error(this.reason));
    }

    public stop(): Promise<void> {
        return Promise.resolve();
    }
}

function parseWorkspaceBinding(
    result: unknown,
    expectedWorkspaceId: string,
): ForgeWorkspaceBinding {
    if (
        !isRecord(result) ||
        !hasExactKeys(result, ["workspace"]) ||
        !isRecord(result.workspace)
    ) {
        throw new Error("Forge workspace lookup returned an invalid result");
    }
    const workspace = result.workspace;
    if (
        !hasExactKeys(workspace, [
            "bundle_root",
            "created_at",
            "forge_root",
            "format",
            "format_version",
            "game_root",
            "world_root",
            "workspace_id",
        ]) ||
        workspace.format !== "rpg-world-forge.forge_workspace" ||
        workspace.format_version !== 1 ||
        workspace.workspace_id !== expectedWorkspaceId ||
        typeof workspace.world_root !== "string" ||
        workspace.world_root.length === 0
    ) {
        throw new Error(
            "Forge workspace lookup returned an incompatible workspace",
        );
    }
    return {
        workspaceId: expectedWorkspaceId,
        worldRoot: workspace.world_root,
    };
}

function hasExactKeys(
    value: Record<string, unknown>,
    expected: readonly string[],
): boolean {
    const actual = Object.keys(value).sort();
    const sorted = [...expected].sort();
    return (
        actual.length === sorted.length &&
        actual.every((key, index) => key === sorted[index])
    );
}

function isRecord(value: unknown): value is Record<string, unknown> {
    return typeof value === "object" && value !== null && !Array.isArray(value);
}

function assertInitializationResult(
    value: Record<string, unknown>,
): asserts value is InitializationResult {
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
        throw new Error(
            "Forge Studio service returned an incompatible handshake",
        );
    }
}

function assertEvidenceInitializationResult(
    value: unknown,
): asserts value is InitializationResult {
    if (!isRecord(value)) {
        throw new Error("Forge Studio evidence handshake is incompatible");
    }
    const methods = value.methods;
    const capabilities = value.capabilities;
    if (
        value.service !== "world-forge.studio" ||
        value.service_version !== 4 ||
        value.protocol !== "rpg-world-forge.studio_protocol" ||
        value.protocol_version !== 4 ||
        !Array.isArray(methods) ||
        methods.length !== STUDIO_V4_METHODS.size ||
        ![...STUDIO_V4_METHODS].every((method) => methods.includes(method)) ||
        !isRecord(capabilities) ||
        !hasExactKeys(capabilities, [
            "asset_previews",
            "creation_asset_previews",
            "creation_evidence_projection",
            "creation_jobs",
            "creation_materialization_bundle",
            "creation_output_grants",
            "creation_runtime_bundle",
            "creation_runtime_compose",
            "game_package_extraction",
            "game_packaging",
            "materialization_execution",
        ]) ||
        capabilities.creation_evidence_projection !== true ||
        capabilities.creation_asset_previews !== true ||
        capabilities.creation_jobs !== true ||
        capabilities.creation_materialization_bundle !== true ||
        capabilities.creation_output_grants !== true ||
        capabilities.creation_runtime_bundle !== true ||
        capabilities.creation_runtime_compose !== true ||
        capabilities.game_package_extraction !== true ||
        capabilities.game_packaging !== true ||
        capabilities.asset_previews !== false ||
        capabilities.materialization_execution !== true
    ) {
        throw new Error("Forge Studio evidence handshake is incompatible");
    }
}

function assertAuthorityInitializationResult(
    value: unknown,
): asserts value is InitializationResult {
    if (!isRecord(value)) {
        throw new Error("Forge Studio authority handshake is incompatible");
    }
    const methods = value.methods;
    const capabilities = value.capabilities;
    if (
        value.service !== "world-forge.studio" ||
        value.service_version !== 5 ||
        value.protocol !== "rpg-world-forge.studio_protocol" ||
        value.protocol_version !== 5 ||
        !Array.isArray(methods) ||
        methods.length !== STUDIO_V5_METHODS.size ||
        ![...STUDIO_V5_METHODS].every((method) => methods.includes(method)) ||
        !isRecord(capabilities) ||
        !hasExactKeys(capabilities, [
            "asset_authority_reviews",
            "asset_previews",
            "asset_release_authority",
            "creation_asset_previews",
            "creation_evidence_projection",
            "creation_jobs",
            "creation_materialization_bundle",
            "creation_output_grants",
            "creation_preview_pre_release",
            "creation_runtime_bundle",
            "creation_runtime_compose",
            "game_package_extraction",
            "game_packaging",
            "materialization_execution",
            "runtime_headless_authority",
        ]) ||
        capabilities.creation_evidence_projection !== true ||
        capabilities.creation_asset_previews !== true ||
        capabilities.creation_jobs !== true ||
        capabilities.creation_materialization_bundle !== true ||
        capabilities.creation_output_grants !== true ||
        capabilities.creation_runtime_bundle !== true ||
        capabilities.creation_runtime_compose !== true ||
        capabilities.game_package_extraction !== true ||
        capabilities.game_packaging !== true ||
        capabilities.asset_previews !== false ||
        capabilities.materialization_execution !== true ||
        capabilities.asset_authority_reviews !== true ||
        capabilities.asset_release_authority !== true ||
        capabilities.runtime_headless_authority !== true ||
        capabilities.creation_preview_pre_release !== true
    ) {
        throw new Error("Forge Studio authority handshake is incompatible");
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

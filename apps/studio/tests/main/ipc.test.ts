import { describe, expect, it, vi } from "vitest";

import {
  registerStudioIpc,
  validateAssetpackArgument,
  validateAssetReceiptArgument,
  validateCancelJobArgument,
  validateChangesetsListParams,
  validateEventsListParams,
  validateHeadlessArgument,
  validateJobsListParams,
  validateInterruptTurnArgument,
  validateLoginArgument,
  validateReplayArgument,
  validateSourceReadArgument,
  validateStartTurnArgument,
  validateUserInputArgument,
  validateWorkspaceArgument,
} from "../../src/main/ipc";
import { IPC_CHANNELS } from "../../src/shared/studio-api";

describe("Studio named authoring and job IPC contracts", () => {
  it("accepts only exact workspace, source, and fixed-operation inputs", () => {
    expect(
      validateSourceReadArgument({
        workspaceId: "workspace_01",
        path: "source/lore/entry.md",
      }),
    ).toEqual({ workspaceId: "workspace_01", path: "source/lore/entry.md" });
    expect(
      validateAssetReceiptArgument({
        workspaceId: "workspace_01",
        input: { receipt: "receipts/item.json" },
      }),
    ).toEqual({ workspaceId: "workspace_01", input: { receipt: "receipts/item.json" } });
    expect(
      validateAssetpackArgument({
        workspaceId: "workspace_01",
        input: { assetpack: "build/assets.json", worldpack: "build/world.json" },
      }),
    ).toEqual({
      workspaceId: "workspace_01",
      input: { assetpack: "build/assets.json", worldpack: "build/world.json" },
    });
    expect(
      validateHeadlessArgument({
        workspaceId: "workspace_01",
        input: { worldpack: "build/world.json", ticks: 0 },
      }),
    ).toEqual({
      workspaceId: "workspace_01",
      input: { worldpack: "build/world.json", ticks: 0 },
    });
    expect(
      validateReplayArgument({
        workspaceId: "workspace_01",
        input: { worldpack: "build/world.json", replay: "replays/slot.json" },
      }),
    ).toEqual({
      workspaceId: "workspace_01",
      input: { worldpack: "build/world.json", replay: "replays/slot.json" },
    });
    expect(validateCancelJobArgument({ jobId: "job_01" })).toEqual({ jobId: "job_01" });
  });

  it.each([
    [validateSourceReadArgument, { workspaceId: "workspace_01", path: "../world.json" }],
    [validateSourceReadArgument, { workspaceId: "workspace_01", path: "source/../world.json" }],
    [validateSourceReadArgument, { workspaceId: "workspace_01", path: "source/CON.json" }],
    [
      validateAssetReceiptArgument,
      {
        workspaceId: "workspace_01",
        input: { receipt: "receipt.json", operation: "shell.execute" },
      },
    ],
    [
      validateAssetpackArgument,
      {
        workspaceId: "workspace_01",
        input: { assetpack: "pack.json", worldpack: "world.json", cwd: "/tmp" },
      },
    ],
    [
      validateHeadlessArgument,
      { workspaceId: "workspace_01", input: { worldpack: "world.json", ticks: true } },
    ],
    [
      validateHeadlessArgument,
      { workspaceId: "workspace_01", input: { worldpack: "world.json", ticks: -1 } },
    ],
    [
      validateHeadlessArgument,
      { workspaceId: "workspace_01", input: { worldpack: "world.json", ticks: 1_000_001 } },
    ],
    [
      validateReplayArgument,
      {
        workspaceId: "workspace_01",
        input: { worldpack: "world.json", replay: "slot.json", env: { PATH: "/tmp" } },
      },
    ],
    [validateCancelJobArgument, { jobId: "../job" }],
  ])("rejects malformed or capability-shaped authoring/job input %#", (validate, value) => {
    expect(() => validate(value)).toThrow();
  });
});

describe("Studio named authoring and job IPC routing", () => {
  it("owns request IDs and maps every capability to a fixed method and operation", async () => {
    const harness = createIpcHarness();
    const cases: Array<[string, unknown, string, Record<string, unknown>]> = [
      [
        IPC_CHANNELS.getWorkspaceOverview,
        { workspaceId: "workspace_01" },
        "workspace.overview",
        { workspace_id: "workspace_01" },
      ],
      [
        IPC_CHANNELS.listSourceDocuments,
        { workspaceId: "workspace_01" },
        "source.list",
        { workspace_id: "workspace_01" },
      ],
      [
        IPC_CHANNELS.readSourceDocument,
        { workspaceId: "workspace_01", path: "source/world.json" },
        "source.read",
        { workspace_id: "workspace_01", path: "source/world.json" },
      ],
      [
        IPC_CHANNELS.validateWorld,
        { workspaceId: "workspace_01" },
        "world.validate",
        { workspace_id: "workspace_01" },
      ],
      [
        IPC_CHANNELS.analyzeWorld,
        { workspaceId: "workspace_01" },
        "world.analyze",
        { workspace_id: "workspace_01" },
      ],
      [
        IPC_CHANNELS.validateAssetReceipt,
        { workspaceId: "workspace_01", input: { receipt: "receipts/item.json" } },
        "job.create",
        {
          workspace_id: "workspace_01",
          operation: "asset.receipt.validate",
          input: { receipt: "receipts/item.json" },
        },
      ],
      [
        IPC_CHANNELS.verifyAssetpack,
        {
          workspaceId: "workspace_01",
          input: { assetpack: "build/assets.json", worldpack: "build/world.json" },
        },
        "job.create",
        {
          workspace_id: "workspace_01",
          operation: "assetpack.verify",
          input: { assetpack: "build/assets.json", worldpack: "build/world.json" },
        },
      ],
      [
        IPC_CHANNELS.runHeadless,
        { workspaceId: "workspace_01", input: { worldpack: "build/world.json", ticks: 0 } },
        "job.create",
        {
          workspace_id: "workspace_01",
          operation: "runtime.headless",
          input: { worldpack: "build/world.json", ticks: 0 },
        },
      ],
      [
        IPC_CHANNELS.runReplay,
        {
          workspaceId: "workspace_01",
          input: { worldpack: "build/world.json", replay: "replays/slot.json" },
        },
        "job.create",
        {
          workspace_id: "workspace_01",
          operation: "runtime.replay",
          input: { worldpack: "build/world.json", replay: "replays/slot.json" },
        },
      ],
      [IPC_CHANNELS.cancelJob, { jobId: "job_01" }, "job.cancel", { job_id: "job_01" }],
    ];

    for (const [channel, argument] of cases) {
      const result = await harness.invoke(channel, argument);
      expect(result).toMatchObject({ ok: true });
    }
    const calls = harness.request.mock.calls;
    expect(calls).toHaveLength(cases.length);
    expect(calls.map((call) => [call[1], call[2]])).toEqual(
      cases.map(([, , method, params]) => [method, params]),
    );
    const requestIds = calls.map((call) => call[0]);
    expect(new Set(requestIds).size).toBe(requestIds.length);
    expect(requestIds.every((value) => /^[0-9a-f-]{36}$/u.test(value))).toBe(true);
  });

  it("accepts exact operation-specific v2 job.create replies for all four capabilities", async () => {
    const harness = createIpcHarness();
    for (const testCase of jobCapabilityCases()) {
      harness.request.mockImplementationOnce((requestId: string) =>
        Promise.resolve(
          createManagedJobResponse(requestId, testCase.operation, testCase.input),
        ),
      );
      expect(await harness.invoke(testCase.channel, testCase.argument)).toMatchObject({
        ok: true,
        value: {
          kind: "response",
          method: "job.create",
          result: { job: { format_version: 2, operation: testCase.operation } },
        },
      });
    }
  });

  it("rejects cross-operation job.create replies for all four capabilities", async () => {
    const harness = createIpcHarness();
    const cases = jobCapabilityCases();
    for (const [index, testCase] of cases.entries()) {
      const other = cases[(index + 1) % cases.length];
      harness.request.mockImplementationOnce((requestId: string) =>
        Promise.resolve(createManagedJobResponse(requestId, other.operation, other.input)),
      );
      expect(await harness.invoke(testCase.channel, testCase.argument)).toMatchObject({
        ok: false,
        error: { code: "service_unavailable" },
      });
    }
  });

  it("rejects same-operation replies whose inputs do not match the requested job", async () => {
    const harness = createIpcHarness();
    const mismatchedInputs = [
      { receipt: "receipts/other.json" },
      { assetpack: "build/other-assets.json", worldpack: "build/world.json" },
      { worldpack: "build/other-world.json", ticks: 0 },
      { worldpack: "build/world.json", replay: "replays/other.json" },
    ];
    for (const [index, testCase] of jobCapabilityCases().entries()) {
      harness.request.mockImplementationOnce((requestId: string) =>
        Promise.resolve(
          createManagedJobResponse(requestId, testCase.operation, mismatchedInputs[index]),
        ),
      );
      expect(await harness.invoke(testCase.channel, testCase.argument)).toMatchObject({
        ok: false,
        error: { code: "service_unavailable" },
      });
    }
  });

  it("rejects untrusted, extra-argument, mismatched, and malformed replies", async () => {
    const harness = createIpcHarness();
    expect(
      await harness.invoke(
        IPC_CHANNELS.runHeadless,
        { workspaceId: "workspace_01", input: { worldpack: "world.json", ticks: 0 } },
        { trusted: false },
      ),
    ).toMatchObject({ ok: false, error: { code: "invalid_request" } });
    expect(harness.request).not.toHaveBeenCalled();

    expect(
      await harness.invoke(
        IPC_CHANNELS.validateWorld,
        { workspaceId: "workspace_01" },
        { extraArgument: true },
      ),
    ).toMatchObject({ ok: false, error: { code: "invalid_request" } });
    expect(harness.request).not.toHaveBeenCalled();

    harness.request.mockImplementationOnce((requestId: string) =>
      Promise.resolve({
        protocol: "rpg-world-forge.studio_protocol",
        protocol_version: 1,
        kind: "response",
        request_id: requestId,
        method: "world.validate",
        result: {
          validation: {
            valid: true,
            profile: "release",
            world_id: "world_01",
            object_count: 0,
            diagnostics: [],
            diagnostics_truncated: false,
          },
        },
      }),
    );
    expect(
      await harness.invoke(IPC_CHANNELS.analyzeWorld, { workspaceId: "workspace_01" }),
    ).toMatchObject({ ok: false, error: { code: "service_unavailable" } });

    harness.request.mockImplementationOnce(() =>
      Promise.resolve({
        protocol: "rpg-world-forge.studio_protocol",
        protocol_version: 1,
        kind: "error",
        request_id: "wrong-request-id",
        error: { code: "not_found", message: "fixture", details: {} },
      }),
    );
    expect(
      await harness.invoke(IPC_CHANNELS.validateWorld, { workspaceId: "workspace_01" }),
    ).toMatchObject({ ok: false, error: { code: "service_unavailable" } });

    harness.request.mockImplementationOnce((requestId: string) =>
      Promise.resolve({
        protocol: "rpg-world-forge.studio_protocol",
        protocol_version: 1,
        kind: "response",
        request_id: requestId,
        method: "world.validate",
        result: {},
      }),
    );
    expect(
      await harness.invoke(IPC_CHANNELS.validateWorld, { workspaceId: "workspace_01" }),
    ).toMatchObject({ ok: false, error: { code: "service_unavailable" } });
  });
});

describe("Studio named read-only IPC filters", () => {
  it("accepts closed, bounded filters for each list operation", () => {
    expect(
      validateEventsListParams({
        workspace_id: "workspace_01",
        after_id: 0,
        limit: 1_000,
      }),
    ).toEqual({ workspace_id: "workspace_01", after_id: 0, limit: 1_000 });
    expect(
      validateChangesetsListParams({
        workspace_id: "workspace_01",
        status: "approved",
        limit: 100,
      }),
    ).toEqual({ workspace_id: "workspace_01", status: "approved", limit: 100 });
    expect(
      validateJobsListParams({
        workspace_id: "workspace_01",
        state: "awaiting_approval",
        limit: 1,
      }),
    ).toEqual({ workspace_id: "workspace_01", state: "awaiting_approval", limit: 1 });
  });

  it.each([
    [validateEventsListParams, { method: "workspace.register" }],
    [validateEventsListParams, { workspace_id: "../bad" }],
    [validateEventsListParams, { after_id: -1 }],
    [validateEventsListParams, { after_id: true }],
    [validateEventsListParams, { limit: 0 }],
    [validateChangesetsListParams, { status: "created" }],
    [validateChangesetsListParams, { limit: 1_001 }],
    [validateJobsListParams, { state: "approved" }],
    [validateJobsListParams, []],
    [validateJobsListParams, { state: "queued", command: "shell.exec" }],
  ])("rejects malformed, unknown, or mutation-shaped filters %#", (validate, value) => {
    expect(() => validate(value)).toThrow();
  });
});

describe("Codex named IPC contracts", () => {
  it("accepts only closed bounded values", () => {
    expect(validateWorkspaceArgument({ workspaceId: "workspace_01" })).toEqual({ workspaceId: "workspace_01" });
    expect(validateLoginArgument({ mode: "device-code" })).toEqual({ mode: "device-code" });
    expect(validateStartTurnArgument({ threadId: "thread-1", text: "hello" })).toEqual({ threadId: "thread-1", text: "hello" });
    expect(validateInterruptTurnArgument({ threadId: "thread-1", turnId: "turn-1" })).toEqual({ threadId: "thread-1", turnId: "turn-1" });
    expect(validateUserInputArgument({
      token: "00000000-0000-4000-8000-000000000000",
      answers: { choice: ["North"] },
    })).toEqual({
      token: "00000000-0000-4000-8000-000000000000",
      answers: { choice: ["North"] },
    });
  });

  it.each([
    [validateWorkspaceArgument, { workspaceId: "../bad" }],
    [validateLoginArgument, { mode: "api-key" }],
    [validateStartTurnArgument, { threadId: "thread-1", text: "ok", command: "shell" }],
    [validateStartTurnArgument, { threadId: "../bad", text: "ok" }],
    [validateInterruptTurnArgument, { threadId: "thread-1" }],
    [validateUserInputArgument, { token: "bad", answers: { choice: ["x"] } }],
  ])("rejects malformed or capability-shaped input %#", (validate, value) => {
    expect(() => validate(value)).toThrow();
  });
});

function createIpcHarness() {
  const handlers = new Map<string, (event: unknown, ...args: unknown[]) => unknown>();
  const ipcMain = {
    handle: vi.fn((channel: string, handler: (event: unknown, ...args: unknown[]) => unknown) => {
      handlers.set(channel, handler);
    }),
    removeHandler: vi.fn((channel: string) => handlers.delete(channel)),
  };
  const mainFrame = { url: "rwf-studio://app/index.html" };
  const webContents = {
    mainFrame,
    isDestroyed: () => false,
    send: vi.fn(),
  };
  const window = { webContents, isDestroyed: () => false };
  const request = vi.fn(
    (
      requestId: string,
      method: string,
      params: Record<string, unknown>,
      timeoutMs: number,
    ): Promise<unknown> => {
      void method;
      void params;
      void timeoutMs;
      return Promise.resolve({
        protocol: "rpg-world-forge.studio_protocol",
        protocol_version: 1,
        kind: "error",
        request_id: requestId,
        error: { code: "not_found", message: "fixture", details: {} },
      });
    },
  );
  const service = {
    status: { state: "ready", message: "ready", pid: 1 },
    subscribe: () => () => undefined,
    initialize: vi.fn(),
    request,
    getWorkspace: vi.fn(),
    stop: vi.fn(),
  };
  const codex = {
    status: { state: "unbound", message: "unbound", pid: null, workspaceId: null },
    subscribe: () => () => undefined,
  };
  registerStudioIpc(ipcMain as never, window as never, service as never, codex as never);

  return {
    request,
    async invoke(
      channel: string,
      argument: unknown,
      options: { trusted?: boolean; extraArgument?: boolean } = {},
    ): Promise<unknown> {
      const handler = handlers.get(channel);
      if (!handler) throw new Error(`Missing fixture handler for ${channel}`);
      const trusted = options.trusted ?? true;
      const event = trusted
        ? { sender: webContents, senderFrame: mainFrame }
        : { sender: {}, senderFrame: mainFrame };
      const args = options.extraArgument ? [argument, { forbidden: true }] : [argument];
      return await handler(event, ...args);
    },
  };
}

function jobCapabilityCases() {
  return [
    {
      channel: IPC_CHANNELS.validateAssetReceipt,
      argument: {
        workspaceId: "workspace_01",
        input: { receipt: "receipts/item.json" },
      },
      operation: "asset.receipt.validate",
      input: { receipt: "receipts/item.json" },
    },
    {
      channel: IPC_CHANNELS.verifyAssetpack,
      argument: {
        workspaceId: "workspace_01",
        input: { assetpack: "build/assets.json", worldpack: "build/world.json" },
      },
      operation: "assetpack.verify",
      input: { assetpack: "build/assets.json", worldpack: "build/world.json" },
    },
    {
      channel: IPC_CHANNELS.runHeadless,
      argument: {
        workspaceId: "workspace_01",
        input: { worldpack: "build/world.json", ticks: 0 },
      },
      operation: "runtime.headless",
      input: { worldpack: "build/world.json", ticks: 0 },
    },
    {
      channel: IPC_CHANNELS.runReplay,
      argument: {
        workspaceId: "workspace_01",
        input: { worldpack: "build/world.json", replay: "replays/slot.json" },
      },
      operation: "runtime.replay",
      input: { worldpack: "build/world.json", replay: "replays/slot.json" },
    },
  ] as const;
}

function createManagedJobResponse(
  requestId: string,
  operation: string,
  input: Readonly<Record<string, unknown>>,
) {
  return {
    protocol: "rpg-world-forge.studio_protocol",
    protocol_version: 1,
    kind: "response",
    request_id: requestId,
    method: "job.create",
    result: {
      job: {
        format: "rpg-world-forge.studio_job",
        format_version: 2,
        job_id: "job_01",
        workspace_id: "workspace_01",
        operation,
        state: "queued",
        input,
        result: null,
        error: null,
        created_at: "2026-07-23T00:00:00Z",
        updated_at: "2026-07-23T00:00:00Z",
      },
    },
  };
}

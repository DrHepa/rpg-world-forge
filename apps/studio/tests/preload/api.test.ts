import { describe, expect, expectTypeOf, it, vi } from "vitest";

import { createStudioApi, type PreloadTransport } from "../../src/preload/api";
import { IPC_CHANNELS } from "../../src/shared/studio-api";

describe("preload API", () => {
  it("exposes only the fixed Studio operations", async () => {
    const invoke = vi.fn().mockResolvedValue({ ok: true, value: { state: "ready" } });
    const transport: PreloadTransport = {
      invoke,
      on: vi.fn(),
      removeListener: vi.fn(),
    };
    const api = createStudioApi(transport);

    expect(Object.isFrozen(api)).toBe(true);
    expect(Object.keys(api).sort()).toEqual([
      "analyzeWorld",
      "answerCodexUserInput",
      "applyChangeset",
      "approveChangeset",
      "bindCodexWorkspace",
      "cancelJob",
      "closeAssetPreview",
      "forkCodexThread",
      "getChangeset",
      "getCodexStatus",
      "getServiceStatus",
      "getWorkspaceOverview",
      "initialize",
      "inspectAssetCatalogEntry",
      "interruptCodexTurn",
      "listAssetCatalog",
      "listChangesets",
      "listEvents",
      "listJobs",
      "listSourceDocuments",
      "listWorkspaces",
      "onCodexEvent",
      "onEvent",
      "openAssetPreview",
      "readAssetPreviewChunk",
      "readChangesetDiff",
      "readCodexAccount",
      "readSourceDocument",
      "rejectChangeset",
      "resumeCodexThread",
      "runHeadless",
      "runReplay",
      "stageSourceDocument",
      "startCodexLogin",
      "startCodexThread",
      "startCodexTurn",
      "steerCodexTurn",
      "validateAssetReceipt",
      "validateWorld",
      "verifyAssetpack",
    ]);
    expect(api).not.toHaveProperty("request");
    expect(api).not.toHaveProperty("cancelRequest");
    expect(api).not.toHaveProperty("ipcRenderer");
    expect(api).not.toHaveProperty("filesystem");
    expect(api).not.toHaveProperty("exec");
    await api.initialize();
    await api.getServiceStatus();
    await api.listWorkspaces();
    await api.listEvents({ workspace_id: "workspace_01", limit: 10 });
    await api.listChangesets({ status: "staged" });
    await api.listJobs({ state: "queued" });
    await api.getWorkspaceOverview("workspace_01");
    await api.listSourceDocuments("workspace_01");
    await api.readSourceDocument("workspace_01", "source/world.json");
    await api.listAssetCatalog("workspace_01");
    await api.listAssetCatalog("workspace_01", {
      offset: 64,
      manifestRevision: "c".repeat(64),
    });
    await api.inspectAssetCatalogEntry(
      "workspace_01",
      "c".repeat(64),
      `asset_${"d".repeat(64)}`,
    );
    await api.openAssetPreview(
      "workspace_01",
      "c".repeat(64),
      `asset_${"d".repeat(64)}`,
    );
    await api.readAssetPreviewChunk("E".repeat(43), 0);
    await api.closeAssetPreview("E".repeat(43));
    await api.stageSourceDocument(
      "workspace_01",
      "source/world.json",
      "a".repeat(64),
      "{}\n",
    );
    await api.getChangeset("changeset_01");
    await api.readChangesetDiff("changeset_01");
    await api.approveChangeset("changeset_01", "b".repeat(64));
    await api.rejectChangeset("legacy_01");
    await api.applyChangeset("changeset_01", "b".repeat(64));
    await api.validateWorld("workspace_01");
    await api.analyzeWorld("workspace_01");
    const receiptValidation = await api.validateAssetReceipt("workspace_01", {
      receipt: "receipts/item.json",
    });
    const assetpackVerification = await api.verifyAssetpack("workspace_01", {
      assetpack: "build/assetpack.json",
      worldpack: "build/worldpack.json",
    });
    const headlessRun = await api.runHeadless("workspace_01", {
      worldpack: "build/worldpack.json",
      ticks: 0,
    });
    const replayRun = await api.runReplay("workspace_01", {
      worldpack: "build/worldpack.json",
      replay: "replays/slot.json",
    });
    if (receiptValidation.ok && receiptValidation.value.kind === "response") {
      expectTypeOf(receiptValidation.value.result.job.operation).toEqualTypeOf<
        "asset.receipt.validate"
      >();
    }
    if (assetpackVerification.ok && assetpackVerification.value.kind === "response") {
      expectTypeOf(assetpackVerification.value.result.job.operation).toEqualTypeOf<
        "assetpack.verify"
      >();
    }
    if (headlessRun.ok && headlessRun.value.kind === "response") {
      expectTypeOf(headlessRun.value.result.job.operation).toEqualTypeOf<"runtime.headless">();
    }
    if (replayRun.ok && replayRun.value.kind === "response") {
      expectTypeOf(replayRun.value.result.job.operation).toEqualTypeOf<"runtime.replay">();
    }
    await api.cancelJob("job_01");
    await api.getCodexStatus();
    await api.bindCodexWorkspace("workspace_01");
    await api.readCodexAccount();
    await api.startCodexLogin("device-code");
    await api.startCodexThread();
    await api.resumeCodexThread("thread-1");
    await api.forkCodexThread("thread-1");
    await api.startCodexTurn("thread-1", "hello");
    await api.steerCodexTurn("thread-1", "turn-1", "more");
    await api.interruptCodexTurn("thread-1", "turn-1");
    await api.answerCodexUserInput("token", { question: ["answer"] });
    expect(invoke.mock.calls).toEqual([
      [IPC_CHANNELS.initialize],
      [IPC_CHANNELS.status],
      [IPC_CHANNELS.listWorkspaces],
      [IPC_CHANNELS.listEvents, { workspace_id: "workspace_01", limit: 10 }],
      [IPC_CHANNELS.listChangesets, { status: "staged" }],
      [IPC_CHANNELS.listJobs, { state: "queued" }],
      [IPC_CHANNELS.getWorkspaceOverview, { workspaceId: "workspace_01" }],
      [IPC_CHANNELS.listSourceDocuments, { workspaceId: "workspace_01" }],
      [IPC_CHANNELS.readSourceDocument, {
        workspaceId: "workspace_01",
        path: "source/world.json",
      }],
      [IPC_CHANNELS.listAssetCatalog, { workspaceId: "workspace_01" }],
      [
        IPC_CHANNELS.listAssetCatalog,
        {
          workspaceId: "workspace_01",
          offset: 64,
          expectedManifestRevision: "c".repeat(64),
        },
      ],
      [
        IPC_CHANNELS.inspectAssetCatalogEntry,
        {
          workspaceId: "workspace_01",
          manifestRevision: "c".repeat(64),
          entryId: `asset_${"d".repeat(64)}`,
        },
      ],
      [
        IPC_CHANNELS.openAssetPreview,
        {
          workspaceId: "workspace_01",
          manifestRevision: "c".repeat(64),
          entryId: `asset_${"d".repeat(64)}`,
        },
      ],
      [
        IPC_CHANNELS.readAssetPreviewChunk,
        { handle: "E".repeat(43), sequence: 0 },
      ],
      [IPC_CHANNELS.closeAssetPreview, { handle: "E".repeat(43) }],
      [IPC_CHANNELS.stageSourceDocument, {
        workspaceId: "workspace_01",
        path: "source/world.json",
        baseSha256: "a".repeat(64),
        content: "{}\n",
      }],
      [IPC_CHANNELS.getChangeset, { changesetId: "changeset_01" }],
      [IPC_CHANNELS.readChangesetDiff, { changesetId: "changeset_01" }],
      [IPC_CHANNELS.approveChangeset, {
        changesetId: "changeset_01",
        expectedReviewSha256: "b".repeat(64),
      }],
      [IPC_CHANNELS.rejectChangeset, { changesetId: "legacy_01" }],
      [IPC_CHANNELS.applyChangeset, {
        changesetId: "changeset_01",
        expectedReviewSha256: "b".repeat(64),
      }],
      [IPC_CHANNELS.validateWorld, { workspaceId: "workspace_01" }],
      [IPC_CHANNELS.analyzeWorld, { workspaceId: "workspace_01" }],
      [IPC_CHANNELS.validateAssetReceipt, {
        workspaceId: "workspace_01",
        input: { receipt: "receipts/item.json" },
      }],
      [IPC_CHANNELS.verifyAssetpack, {
        workspaceId: "workspace_01",
        input: { assetpack: "build/assetpack.json", worldpack: "build/worldpack.json" },
      }],
      [IPC_CHANNELS.runHeadless, {
        workspaceId: "workspace_01",
        input: { worldpack: "build/worldpack.json", ticks: 0 },
      }],
      [IPC_CHANNELS.runReplay, {
        workspaceId: "workspace_01",
        input: { worldpack: "build/worldpack.json", replay: "replays/slot.json" },
      }],
      [IPC_CHANNELS.cancelJob, { jobId: "job_01" }],
      [IPC_CHANNELS.codexStatus],
      [IPC_CHANNELS.codexBindWorkspace, { workspaceId: "workspace_01" }],
      [IPC_CHANNELS.codexReadAccount],
      [IPC_CHANNELS.codexStartLogin, { mode: "device-code" }],
      [IPC_CHANNELS.codexStartThread],
      [IPC_CHANNELS.codexResumeThread, { threadId: "thread-1" }],
      [IPC_CHANNELS.codexForkThread, { threadId: "thread-1" }],
      [IPC_CHANNELS.codexStartTurn, { threadId: "thread-1", text: "hello" }],
      [IPC_CHANNELS.codexSteerTurn, { threadId: "thread-1", turnId: "turn-1", text: "more" }],
      [IPC_CHANNELS.codexInterruptTurn, { threadId: "thread-1", turnId: "turn-1" }],
      [IPC_CHANNELS.codexAnswerUserInput, { token: "token", answers: { question: ["answer"] } }],
    ]);
  });

  it("uses a separate fixed Codex event channel", () => {
    let wrapped: ((event: unknown, payload: unknown) => void) | undefined;
    const on: PreloadTransport["on"] = (_channel, listener) => { wrapped = listener; };
    const removeListener: PreloadTransport["removeListener"] = vi.fn();
    const transport: PreloadTransport = {
      invoke: vi.fn(),
      on,
      removeListener,
    };
    const listener = vi.fn();
    const unsubscribe = createStudioApi(transport).onCodexEvent(listener);
    wrapped?.({}, { type: "codex-status", status: { state: "unbound" } });
    wrapped?.({}, { type: "arbitrary" });
    expect(listener).toHaveBeenCalledTimes(1);
    unsubscribe();
    expect(removeListener).toHaveBeenCalledWith(IPC_CHANNELS.codexEvent, wrapped);
  });

  it("subscribes and unsubscribes only on the fixed event channel", () => {
    let wrapped: ((event: unknown, payload: unknown) => void) | undefined;
    const on = vi.fn((
      _channel: string,
      listener: (event: unknown, payload: unknown) => void,
    ) => {
      wrapped = listener;
    });
    const removeListener = vi.fn((
      channel: string,
      listener: (event: unknown, payload: unknown) => void,
    ) => {
      void channel;
      void listener;
    });
    const transport: PreloadTransport = {
      invoke: vi.fn(),
      on,
      removeListener,
    };
    const listener = vi.fn();
    const unsubscribe = createStudioApi(transport).onEvent(listener);

    wrapped?.({}, { type: "service-stderr", text: "bounded" });
    wrapped?.({}, { type: "arbitrary-channel", value: true });
    expect(listener).toHaveBeenCalledTimes(1);
    unsubscribe();
    expect(removeListener).toHaveBeenCalledWith(IPC_CHANNELS.event, wrapped);
  });
});

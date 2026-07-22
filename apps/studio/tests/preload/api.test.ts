import { describe, expect, it, vi } from "vitest";

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

    expect(Object.keys(api).sort()).toEqual([
      "answerCodexUserInput",
      "bindCodexWorkspace",
      "forkCodexThread",
      "getCodexStatus",
      "getServiceStatus",
      "initialize",
      "interruptCodexTurn",
      "listChangesets",
      "listEvents",
      "listJobs",
      "listWorkspaces",
      "onCodexEvent",
      "onEvent",
      "readCodexAccount",
      "resumeCodexThread",
      "startCodexLogin",
      "startCodexThread",
      "startCodexTurn",
      "steerCodexTurn",
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

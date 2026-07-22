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
      "getServiceStatus",
      "initialize",
      "listChangesets",
      "listEvents",
      "listJobs",
      "listWorkspaces",
      "onEvent",
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
    expect(invoke.mock.calls).toEqual([
      [IPC_CHANNELS.initialize],
      [IPC_CHANNELS.status],
      [IPC_CHANNELS.listWorkspaces],
      [IPC_CHANNELS.listEvents, { workspace_id: "workspace_01", limit: 10 }],
      [IPC_CHANNELS.listChangesets, { status: "staged" }],
      [IPC_CHANNELS.listJobs, { state: "queued" }],
    ]);
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

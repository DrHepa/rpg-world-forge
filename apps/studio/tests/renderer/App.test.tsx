// @vitest-environment jsdom

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup } from "@testing-library/react";

import { App } from "../../src/renderer/App";
import type { ForgeStudioApi, StudioActivityEvent } from "../../src/shared/studio-api";

const initialization = {
  protocol: "rpg-world-forge.studio_protocol" as const,
  protocol_version: 1 as const,
  kind: "response" as const,
  request_id: "initial",
  result: { service: "rpg-world-forge.studio" },
};

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("Studio workbench", () => {
  it("shows real status, initializes, sends a request, and records activity", async () => {
    let activityListener: ((event: StudioActivityEvent) => void) | undefined;
    const listWorkspaces = vi.fn().mockResolvedValue({ ok: true, value: initialization });
    installApi({
      initialize: vi.fn().mockResolvedValue({ ok: true, value: initialization }),
      getServiceStatus: vi.fn().mockResolvedValue({
        ok: true,
        value: { state: "ready", message: "Forge Studio service is ready", pid: 123 },
      }),
      listWorkspaces,
      listEvents: vi.fn(),
      listChangesets: vi.fn(),
      listJobs: vi.fn(),
      onEvent: (listener) => {
        activityListener = listener;
        return vi.fn();
      },
    });

    render(<App />);

    expect(await screen.findByText("ready")).toBeInTheDocument();
    activityListener?.({
      type: "service-status",
      status: { state: "ready", message: "Handshake complete", pid: 123 },
    });
    expect((await screen.findAllByText(/Handshake complete/u)).length).toBeGreaterThanOrEqual(1);

    fireEvent.click(screen.getByRole("button", { name: "Run operation" }));
    await waitFor(() => expect(listWorkspaces).toHaveBeenCalledOnce());
    expect(await screen.findByText(/rpg-world-forge\.studio/u)).toBeInTheDocument();
  });

  it("reports initialization failures without claiming features exist", async () => {
    installApi({
      initialize: vi.fn().mockResolvedValue({
        ok: false,
        error: { code: "service_unavailable", message: "Runtime is not packaged yet" },
      }),
      getServiceStatus: vi.fn().mockResolvedValue({
        ok: true,
        value: { state: "unavailable", message: "Runtime is not packaged yet", pid: null },
      }),
      listWorkspaces: vi.fn(),
      listEvents: vi.fn(),
      listChangesets: vi.fn(),
      listJobs: vi.fn(),
      onEvent: () => vi.fn(),
    });

    render(<App />);

    expect((await screen.findAllByText("Runtime is not packaged yet")).length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText(/Visual lore, asset, and game tools remain future slices/u)).toBeInTheDocument();
  });

  it("binds Codex only through the named workspace operation", async () => {
    const bindCodexWorkspace = vi.fn().mockResolvedValue({
      ok: true,
      value: { state: "ready", message: "Codex is bound", pid: 456, workspaceId: "workspace_01" },
    });
    installApi({
      initialize: vi.fn().mockResolvedValue({ ok: true, value: initialization }),
      getServiceStatus: vi.fn().mockResolvedValue({
        ok: true,
        value: { state: "ready", message: "ready", pid: 123 },
      }),
      listWorkspaces: vi.fn(),
      listEvents: vi.fn(),
      listChangesets: vi.fn(),
      listJobs: vi.fn(),
      onEvent: () => vi.fn(),
      getCodexStatus: vi.fn().mockResolvedValue({
        ok: true,
        value: { state: "unbound", message: "Not bound", pid: null, workspaceId: null },
      }),
      bindCodexWorkspace,
      onCodexEvent: () => vi.fn(),
    });
    render(<App />);
    fireEvent.change(screen.getByLabelText("Registered workspace ID"), {
      target: { value: "workspace_01" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Bind Codex" }));
    await waitFor(() => expect(bindCodexWorkspace).toHaveBeenCalledWith("workspace_01"));
    expect(await screen.findByText("Codex is bound")).toBeInTheDocument();
  });
});

function installApi(api: Partial<ForgeStudioApi>): void {
  const unavailable = vi.fn().mockResolvedValue({
    ok: false,
    error: { code: "service_unavailable", message: "Codex unavailable in fixture" },
  });
  const complete: ForgeStudioApi = {
    getCodexStatus: unavailable,
    bindCodexWorkspace: unavailable,
    readCodexAccount: unavailable,
    startCodexLogin: unavailable,
    startCodexThread: unavailable,
    resumeCodexThread: unavailable,
    forkCodexThread: unavailable,
    startCodexTurn: unavailable,
    steerCodexTurn: unavailable,
    interruptCodexTurn: unavailable,
    answerCodexUserInput: unavailable,
    onCodexEvent: () => vi.fn(),
    ...api,
  } as ForgeStudioApi;
  Object.defineProperty(window, "forgeStudio", {
    configurable: true,
    value: complete,
  });
}

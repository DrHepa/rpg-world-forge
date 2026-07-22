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
    expect(screen.getByText(/Visual lore, asset, and game tools are not implemented yet/u)).toBeInTheDocument();
  });
});

function installApi(api: ForgeStudioApi): void {
  Object.defineProperty(window, "forgeStudio", {
    configurable: true,
    value: api,
  });
}

// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("../../src/renderer/CreationWorkspace", () => ({
  CreationWorkspace: ({
    onNavigationStateChange,
  }: {
    onNavigationStateChange: (state: {
      blocksNavigation: boolean;
      kind: "clean" | "output_grant" | "request_pending";
    }) => void;
  }) => (
    <main>
      <h1>Creation navigation fixture</h1>
      <button
        type="button"
        onClick={() =>
          onNavigationStateChange({ blocksNavigation: true, kind: "output_grant" })
        }
      >
        Register durable output
      </button>
      <button
        type="button"
        onClick={() =>
          onNavigationStateChange({ blocksNavigation: true, kind: "request_pending" })
        }
      >
        Begin fixed request
      </button>
    </main>
  ),
}));

import { App } from "../../src/renderer/App";
import type { ForgeStudioApi } from "../../src/shared/studio-api";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("App creation navigation authority", () => {
  it("labels every generic workspace by its actual project kind", async () => {
    const { api } = navigationApi(["game", "asset_library", "universe_library"]);
    Object.defineProperty(window, "forgeStudio", {
      configurable: true,
      value: api,
    });
    render(<App />);

    expect(
      await screen.findByRole("button", { name: /creation_game.*Game project/iu }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /creation_asset_library.*Asset library/iu }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", {
        name: /creation_universe_library.*Universe library/iu,
      }),
    ).toBeInTheDocument();
  });

  it("never offers discard navigation for durable output or unresolved native requests", async () => {
    const { api, getWorkspaceOverview } = navigationApi();
    Object.defineProperty(window, "forgeStudio", {
      configurable: true,
      value: api,
    });
    render(<App />);

    fireEvent.click(
      await screen.findByRole("button", {
        name: /creation_workspace.*Universe library/iu,
      }),
    );
    await screen.findByRole("heading", { name: "Creation navigation fixture" });

    const legacyTrigger = screen.getByRole("button", {
      name: /workspace_01.*Legacy RPG/iu,
    });
    fireEvent.click(screen.getByRole("button", { name: "Register durable output" }));
    fireEvent.click(legacyTrigger);
    expect(
      await screen.findByRole("dialog", {
        name: "Resolve the selected asset output before leaving",
      }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Discard draft" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Return to asset output" }));
    await waitFor(() => expect(legacyTrigger).toHaveFocus());

    fireEvent.click(screen.getByRole("button", { name: "Begin fixed request" }));
    fireEvent.click(legacyTrigger);
    expect(
      await screen.findByRole("dialog", {
        name: "Wait for the current creation request",
      }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Discard draft" })).not.toBeInTheDocument();
    expect(getWorkspaceOverview).not.toHaveBeenCalled();
  });
});

function navigationApi(
  projectKinds: readonly ("game" | "asset_library" | "universe_library")[] = [
    "universe_library",
  ],
): {
  api: ForgeStudioApi;
  getWorkspaceOverview: ReturnType<typeof vi.fn>;
} {
  const response = (version: number, method: string, result: Record<string, unknown>) => ({
    ok: true as const,
    value: {
      protocol: "rpg-world-forge.studio_protocol",
      protocol_version: version,
      kind: "response",
      request_id: "fixture-request",
      method,
      result,
    },
  });
  const getWorkspaceOverview = vi.fn();
  const api = {
    initialize: vi.fn().mockResolvedValue(response(1, "service.initialize", {})),
    getServiceStatus: vi.fn().mockResolvedValue({
      ok: true,
      value: { state: "ready", message: "ready", pid: 1 },
    }),
    getCodexStatus: vi.fn().mockResolvedValue({
      ok: true,
      value: { state: "ready", message: "ready", pid: 2 },
    }),
    onEvent: vi.fn().mockReturnValue(() => undefined),
    onCodexEvent: vi.fn().mockReturnValue(() => undefined),
    listWorkspaces: vi.fn().mockResolvedValue(
      response(1, "workspace.list", {
        workspaces: [{ workspace_id: "workspace_01" }],
      }),
    ),
    listCreationWorkspaces: vi.fn().mockResolvedValue(
      response(3, "creation_workspace.list", {
        workspaces: projectKinds.map((projectKind) =>
          ({
            format: "world-forge.studio_creation_workspace",
            format_version: 1,
            workspace_id:
              projectKinds.length === 1
                ? "creation_workspace"
                : `creation_${projectKind}`,
            project: {
              format: "world-forge.project",
              format_version: 1,
              id: `neutral_${projectKind}`,
              content_hash: "a".repeat(64),
            },
            project_kind: projectKind,
            source_revision: "b".repeat(64),
            workflow_status_hash: null,
            root_generation: 0,
            created_at: "2026-08-05T00:00:00Z",
            updated_at: "2026-08-05T00:00:00Z",
          }),
        ),
      }),
    ),
    getWorkspaceOverview,
  } as unknown as ForgeStudioApi;
  return { api, getWorkspaceOverview };
}

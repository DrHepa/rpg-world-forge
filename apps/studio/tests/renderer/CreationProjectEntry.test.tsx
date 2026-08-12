// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CreationProjectEntry } from "../../src/renderer/CreationProjectEntry";
import { CREATION_CONTENT_MODES } from "../../src/generated/creation-content-modes";
import type { ForgeStudioApi } from "../../src/shared/studio-api";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("CreationProjectEntry", () => {
  it("registers an existing project through the pathless fixed API and restores focus", async () => {
    const registerCreationProject = vi.fn().mockResolvedValue(
      workspaceResponse("creation_workspace.register"),
    );
    installApi({ registerCreationProject } as unknown as ForgeStudioApi);
    const onWorkspaceReady = vi.fn();
    render(<CreationProjectEntry onWorkspaceReady={onWorkspaceReady} />);

    const trigger = screen.getByRole("button", { name: "Register existing" });
    fireEvent.click(trigger);
    await waitFor(() => expect(registerCreationProject).toHaveBeenCalledWith());
    expect(onWorkspaceReady).toHaveBeenCalledWith(
      expect.objectContaining({ workspace_id: "creation_workspace" }),
    );
    await waitFor(() => expect(trigger).toHaveFocus());
    expect(document.body.textContent).not.toContain("/selected/");
  });

  it("creates a no-world no-narrative game through explicit accessible facets", async () => {
    const createCreationProject = vi.fn().mockResolvedValue(
      workspaceResponse("creation_workspace.create", "game"),
    );
    installApi({ createCreationProject } as unknown as ForgeStudioApi);
    const onWorkspaceReady = vi.fn();
    render(<CreationProjectEntry onWorkspaceReady={onWorkspaceReady} />);

    fireEvent.click(screen.getByRole("button", { name: "New creation project" }));
    expect(screen.getByRole("radio", { name: "Universe library" })).toBeChecked();
    fireEvent.click(screen.getByRole("radio", { name: "Game project" }));
    expect(screen.getByRole("group", { name: "Initial game profile" })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Project ID"), {
      target: { value: "neutral_game" },
    });
    fireEvent.change(screen.getByLabelText("Project title"), {
      target: { value: "Neutral game" },
    });
    fireEvent.change(screen.getByLabelText("Gameplay family"), { target: { value: "puzzle" } });
    fireEvent.change(screen.getByLabelText("Initial core verb"), { target: { value: "solve" } });
    fireEvent.change(screen.getByLabelText("Initial core loop"), {
      target: { value: "inspect and solve" },
    });
    fireEvent.change(screen.getByLabelText("World presence"), { target: { value: "none" } });
    fireEvent.change(screen.getByLabelText("Narrative requirement"), {
      target: { value: "none" },
    });
    fireEvent.change(screen.getByLabelText("Presentation mode"), { target: { value: "2d" } });
    fireEvent.change(screen.getByLabelText("Runtime support intent"), {
      target: { value: "authoring_only" },
    });
    expect(
      Array.from(screen.getByLabelText("Asset content mode").querySelectorAll("option")).map(
        (option) => option.value,
      ),
    ).toEqual(CREATION_CONTENT_MODES);
    fireEvent.change(screen.getByLabelText("Asset content mode"), {
      target: { value: "not_applicable" },
    });
    fireEvent.submit(screen.getByRole("form", { name: "New creation project" }));

    await waitFor(() =>
      expect(createCreationProject).toHaveBeenCalledWith({
        projectKind: "game",
        projectId: "neutral_game",
        title: "Neutral game",
        defaultLocale: "en",
        projectVersion: "0.1.0",
        gameplayFamily: "puzzle",
        initialCoreVerb: "solve",
        initialCoreLoop: "inspect and solve",
        worldPresence: "none",
        narrativeRequirement: "none",
        narrativeAuthorship: "none",
        narrativeTopology: "none",
        presentationMode: "2d",
        runtimeSupportIntent: "authoring_only",
        assetContentMode: "not_applicable",
      }),
    );
    expect(onWorkspaceReady).toHaveBeenCalledTimes(1);
    expect(await screen.findByText("Game project registered.")).toBeInTheDocument();
  });

  it("creates an asset library without rendering game-only facets", async () => {
    const createCreationProject = vi.fn().mockResolvedValue(
      workspaceResponse("creation_workspace.create", "asset_library"),
    );
    installApi({ createCreationProject } as unknown as ForgeStudioApi);
    render(<CreationProjectEntry onWorkspaceReady={vi.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: "New creation project" }));
    fireEvent.click(screen.getByRole("radio", { name: "Asset library" }));
    expect(screen.queryByRole("group", { name: "Initial game profile" })).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Asset content mode")).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Project ID"), {
      target: { value: "asset_library_demo" },
    });
    fireEvent.change(screen.getByLabelText("Project title"), {
      target: { value: "Asset library demo" },
    });
    fireEvent.submit(screen.getByRole("form", { name: "New creation project" }));

    await waitFor(() =>
      expect(createCreationProject).toHaveBeenCalledWith({
        projectKind: "asset_library",
        projectId: "asset_library_demo",
        title: "Asset library demo",
        defaultLocale: "en",
        projectVersion: "0.1.0",
      }),
    );
  });

  it("does not create a phantom record when native selection is canceled", async () => {
    const registerCreationProject = vi.fn().mockResolvedValue({
      ok: false,
      error: { code: "cancelled", message: "Creation project selection was cancelled" },
    });
    installApi({ registerCreationProject } as unknown as ForgeStudioApi);
    const onWorkspaceReady = vi.fn();
    render(<CreationProjectEntry onWorkspaceReady={onWorkspaceReady} />);
    const trigger = screen.getByRole("button", { name: "Register existing" });

    fireEvent.click(trigger);

    expect(await screen.findByText("Selection cancelled. No project was registered.")).toBeInTheDocument();
    expect(onWorkspaceReady).not.toHaveBeenCalled();
    expect(trigger).toHaveFocus();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("surfaces a pathless failure without creating a phantom rail entry", async () => {
    const createCreationProject = vi.fn().mockResolvedValue({
      ok: true,
      value: {
        protocol: "rpg-world-forge.studio_protocol",
        protocol_version: 3,
        kind: "error",
        request_id: "fixture-request",
        error: { code: "invalid_request", message: "Project ID is invalid", details: {} },
      },
    });
    installApi({ createCreationProject } as unknown as ForgeStudioApi);
    const onWorkspaceReady = vi.fn();
    render(<CreationProjectEntry onWorkspaceReady={onWorkspaceReady} />);
    fireEvent.click(screen.getByRole("button", { name: "New creation project" }));
    fireEvent.change(screen.getByLabelText("Project ID"), { target: { value: "bad" } });
    fireEvent.change(screen.getByLabelText("Project title"), { target: { value: "Bad" } });
    fireEvent.submit(screen.getByRole("form", { name: "New creation project" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Project ID is invalid");
    expect(onWorkspaceReady).not.toHaveBeenCalled();
  });
});

function installApi(api: ForgeStudioApi): void {
  Object.defineProperty(window, "forgeStudio", { configurable: true, value: api });
}

function workspaceResponse(
  method: "creation_workspace.register" | "creation_workspace.create",
  projectKind: "game" | "asset_library" | "universe_library" = "universe_library",
) {
  return {
    ok: true as const,
    value: {
      protocol: "rpg-world-forge.studio_protocol" as const,
      protocol_version: 3 as const,
      kind: "response" as const,
      request_id: "fixture-request",
      method,
      result: {
        workspace: {
          format: "world-forge.studio_creation_workspace" as const,
          format_version: 1 as const,
          workspace_id: "creation_workspace",
          project: {
            format: "world-forge.project" as const,
            format_version: 1 as const,
            id: "neutral_universe",
            content_hash: "a".repeat(64),
          },
          project_kind: projectKind,
          source_revision: "b".repeat(64),
          workflow_status_hash: null,
          root_generation: 0,
          created_at: "2026-08-01T00:00:00Z",
          updated_at: "2026-08-01T00:00:00Z",
        },
      },
    },
  };
}

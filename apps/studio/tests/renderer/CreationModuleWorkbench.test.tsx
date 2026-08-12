// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CreationModuleWorkbench } from "../../src/renderer/CreationModuleWorkbench";
import type { ForgeStudioApi } from "../../src/shared/studio-api";

const SOURCE = "1".repeat(64);
const WORKFLOW = "2".repeat(64);
const FILE = "3".repeat(64);
const CONTENT = "4".repeat(64);

describe("CreationModuleWorkbench", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });
  afterEach(cleanup);

  it("loads manifest-referenced typed modules and stages a reviewed aggregate edit", async () => {
    const fixture = installModuleApi();
    const navigation = vi.fn();
    render(<CreationModuleWorkbench
      workspace={workspace()}
      workflow={workflow()}
      onNavigationStateChange={navigation}
      onAuthorityRefresh={vi.fn().mockResolvedValue(undefined)}
      onApplied={vi.fn().mockResolvedValue(undefined)}
    />);

    expect(await screen.findByRole("heading", { name: "Typed modules" })).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: /core/i })).toBeInTheDocument();
    expect(screen.getByText(/No world modules are referenced/u)).toBeInTheDocument();
    expect(screen.getByText(/No narrative modules are referenced/u)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /core/i }));
    fireEvent.click(screen.getByRole("button", { name: "Edit module" }));
    const editor = screen.getByLabelText("Module JSON");
    fireEvent.change(editor, {
      target: { value: JSON.stringify({ ...logicModule(), title: "Updated logic" }, null, 2) },
    });
    fireEvent.click(screen.getByRole("button", { name: "Update draft" }));
    fireEvent.click(screen.getByRole("button", { name: "Stage module changes" }));

    await waitFor(() => expect(fixture.stageCreationModuleChange).toHaveBeenCalledTimes(1));
    expect(fixture.stageCreationModuleChange.mock.calls[0]?.[0]).toMatchObject({
      workspaceId: "creation_workspace",
      expectedRootGeneration: 4,
      expectedSourceRevision: SOURCE,
      expectedWorkflowStatusHash: WORKFLOW,
      operation: "replace",
      path: "source/logic/core.json",
      format: "world-forge.logic_module",
      expectedBaseFileSha256: FILE,
      proposedModule: { title: "Updated logic" },
    });
    expect(await screen.findByText("3 operations")).toBeInTheDocument();
    expect(navigation).toHaveBeenCalledWith({ blocksNavigation: true, kind: "staged" });
  });

  it("stages deletion rather than orphaning the selected module immediately", async () => {
    const fixture = installModuleApi();
    render(<CreationModuleWorkbench
      workspace={workspace()}
      workflow={workflow()}
      onNavigationStateChange={vi.fn()}
      onAuthorityRefresh={vi.fn().mockResolvedValue(undefined)}
      onApplied={vi.fn().mockResolvedValue(undefined)}
    />);

    fireEvent.click(await screen.findByRole("button", { name: /core/i }));
    fireEvent.click(screen.getByRole("button", { name: "Prepare deletion" }));
    fireEvent.click(screen.getByRole("button", { name: "Stage module deletion" }));
    await waitFor(() => expect(fixture.stageCreationModuleChange).toHaveBeenCalledWith(
      expect.objectContaining({
        operation: "delete",
        path: "source/logic/core.json",
        expectedBaseFileSha256: FILE,
      }),
    ));
    expect(screen.getByRole("button", { name: /core/i })).toBeInTheDocument();
  });
});

function installModuleApi() {
  const project = {
    format: "world-forge.project",
    format_version: 1,
    project_id: "neutral_universe",
    content_hash: CONTENT,
    profile: { format: "world-forge.creation_profile", format_version: 1, id: "profile", path: "profile.json", content_hash: CONTENT },
    source_manifest: { format: "world-forge.creation_source_manifest", format_version: 1, id: "neutral_universe", path: "source/manifest.json", content_hash: CONTENT },
  };
  const manifest = {
    format: "world-forge.creation_source_manifest",
    format_version: 1,
    project_id: "neutral_universe",
    content_hash: CONTENT,
    extensions: [],
    profile: project.profile,
    modules: {
      world_modules: [], activity_modules: [], narrative_modules: [], system_modules: [],
      logic_modules: [{ format: "world-forge.logic_module", format_version: 1, id: "core", path: "logic/core.json", content_hash: CONTENT }],
    },
  };
  const documents = [
    summary("project.json", "world-forge.project", "neutral_universe"),
    summary("source/manifest.json", "world-forge.creation_source_manifest", "neutral_universe"),
    summary("source/logic/core.json", "world-forge.logic_module", "core"),
  ];
  const listCreationDocuments = vi.fn().mockResolvedValue(v3("creation_document.list", { source_revision: SOURCE, documents }));
  const readCreationDocument = vi.fn((_workspaceId: string, _revision: string, path: string) => {
    const document = path === "project.json" ? project : path === "source/manifest.json" ? manifest : logicModule();
    const item = documents.find((candidate) => candidate.path === path)!;
    return Promise.resolve(v3("creation_document.read", { source_revision: SOURCE, document: { ...item, document } }));
  });
  const staged = changeset("staged");
  const stageCreationModuleChange = vi.fn().mockResolvedValue(v3("creation_changeset.create", { changeset: staged }));
  const getCreationChangeset = vi.fn().mockResolvedValue(v3("creation_changeset.get", { changeset: staged }));
  const diffCreationChangeset = vi.fn().mockResolvedValue(v3("creation_changeset.diff", {
    diff: {
      changeset_id: staged.changeset_id,
      workspace_id: staged.workspace_id,
      expected_source_revision: SOURCE,
      proposed_source_revision: "5".repeat(64),
      review_sha256: "6".repeat(64),
      operations: staged.operations.map((operation) => ({ ...operation, size_delta: 10 })),
    },
  }));
  window.forgeStudio = {
    listCreationDocuments,
    readCreationDocument,
    stageCreationModuleChange,
    getCreationChangeset,
    diffCreationChangeset,
  } as unknown as ForgeStudioApi;
  return { stageCreationModuleChange };
}

function workspace() {
  return {
    format: "world-forge.studio_creation_workspace" as const,
    format_version: 1 as const,
    workspace_id: "creation_workspace",
    project: { format: "world-forge.project" as const, format_version: 1 as const, id: "neutral_universe", content_hash: CONTENT },
    project_kind: "universe_library" as const,
    source_revision: SOURCE,
    workflow_status_hash: WORKFLOW,
    root_generation: 4,
    created_at: "2026-08-01T00:00:00Z",
    updated_at: "2026-08-01T00:00:00Z",
  };
}

function workflow() {
  return { state: "active" as const, source_revision: SOURCE, status_hash: WORKFLOW, current_phase: "p00_brief", revision: 1, status: {} };
}

function logicModule() {
  return { format: "world-forge.logic_module", format_version: 1, module_id: "core", project_id: "neutral_universe", title: "Core", content_hash: CONTENT };
}

function summary(path: string, format: string, id: string) {
  return { path, format, format_version: 1, id, content_hash: CONTENT, file_sha256: FILE };
}

function changeset(status: "staged" | "approved" | "applied") {
  const operation = (path: string) => ({ operation: "replace" as const, path, expected_base_file_sha256: FILE, expected_base_size: 100, proposed_file_sha256: "7".repeat(64), proposed_size: 110 });
  return {
    format: "world-forge.studio_creation_changeset" as const,
    format_version: 1 as const,
    changeset_id: "module_changeset",
    workspace_id: "creation_workspace",
    status,
    expected_root_generation: 4,
    expected_source_revision: SOURCE,
    proposed_source_revision: "5".repeat(64),
    expected_workflow_status_hash: WORKFLOW,
    review_sha256: "6".repeat(64),
    operations: [operation("source/logic/core.json"), operation("source/manifest.json"), operation("project.json")],
    created_at: "2026-08-01T00:00:00Z",
    updated_at: "2026-08-01T00:00:00Z",
    record_hash: "8".repeat(64),
  };
}

function v3(method: string, result: Record<string, unknown>) {
  return { ok: true as const, value: { protocol: "rpg-world-forge.studio_protocol" as const, protocol_version: 3 as const, kind: "response" as const, request_id: "request_01", method, result } };
}

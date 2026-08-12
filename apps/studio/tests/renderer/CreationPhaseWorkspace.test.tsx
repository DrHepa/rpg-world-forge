// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CreationPhaseWorkspace } from "../../src/renderer/CreationPhaseWorkspace";
import type { ForgeStudioApi } from "../../src/shared/studio-api";

const SOURCE = "1".repeat(64);
const STATUS = "2".repeat(64);
const REPORT_HASH = "3".repeat(64);

describe("CreationPhaseWorkspace", () => {
  afterEach(cleanup);

  it("loads pathless durable reports and completes only the exact validated draft", async () => {
    const fixture = installPhaseApi();
    const navigation = vi.fn();
    const refresh = vi.fn().mockResolvedValue(undefined);
    render(<CreationPhaseWorkspace
      workspace={workspace()}
      workflow={workflow()}
      onNavigationStateChange={navigation}
      onWorkflowRefresh={refresh}
    />);

    expect(await screen.findByRole("heading", { name: "Reviewed creation phases" })).toBeInTheDocument();
    expect(screen.getAllByRole("tab", { name: /^P\d{2}/u })).toHaveLength(15);
    expect(fixture.readCreationPhase).toHaveBeenCalledWith(expect.objectContaining({
      phaseId: "p00_brief",
      expectedWorkflowStatusHash: STATUS,
    }));
    fireEvent.click(screen.getByRole("tab", { name: /P00/u }));
    expect(await screen.findByText("Reviewed report evidence")).toBeInTheDocument();
    expect(screen.getByText(REPORT_HASH)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("tab", { name: /P01/u }));
    const reportEditor = screen.getByLabelText("Phase report JSON");
    const report = phaseReport("p01_genre_style");
    fireEvent.change(reportEditor, { target: { value: JSON.stringify(report, null, 2) } });
    fireEvent.change(screen.getByLabelText("Artifact registry JSON"), { target: { value: "[]" } });
    expect(screen.getByRole("button", { name: "Complete reviewed phase" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Validate phase report" }));
    await waitFor(() => expect(fixture.validateCreationPhase).toHaveBeenCalledTimes(1));
    expect(screen.getByRole("button", { name: "Complete reviewed phase" })).toBeEnabled();
    fireEvent.change(reportEditor, { target: { value: `${JSON.stringify({ ...report, status: "not_applicable" })}\n` } });
    expect(screen.getByRole("button", { name: "Complete reviewed phase" })).toBeDisabled();
    fireEvent.change(reportEditor, { target: { value: JSON.stringify(report, null, 2) } });
    expect(screen.getByRole("button", { name: "Complete reviewed phase" })).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: "Complete reviewed phase" }));
    await waitFor(() => expect(fixture.completeCreationPhase).toHaveBeenCalledTimes(1));
    expect(fixture.completeCreationPhase.mock.calls[0]?.[0]).toMatchObject({
      expectedRootGeneration: 4,
      expectedSourceRevision: SOURCE,
      expectedWorkflowStatusHash: STATUS,
      report: { phase: "p01_genre_style" },
      artifactRegistry: [],
    });
    expect(refresh).toHaveBeenCalledTimes(1);
    expect(navigation).toHaveBeenCalledWith({ blocksNavigation: true, kind: "draft" });
  });

  it("reopens reviewed suffixes with explicit approval and reconciles invalid workflow authority", async () => {
    const fixture = installPhaseApi();
    render(<CreationPhaseWorkspace
      workspace={workspace()}
      workflow={{ ...workflow(), state: "invalid" }}
      onNavigationStateChange={vi.fn()}
      onWorkflowRefresh={vi.fn().mockResolvedValue(undefined)}
    />);

    fireEvent.click(await screen.findByRole("tab", { name: /P00/u }));
    fireEvent.click(screen.getByRole("button", { name: "Reopen reviewed phase" }));
    fireEvent.change(screen.getByLabelText("Reopen reason"), { target: { value: "The brief changed" } });
    fireEvent.change(screen.getByLabelText("Approved by"), { target: { value: "lead_reviewer" } });
    const otherPhase = screen.getByRole("tab", { name: /P01/u });
    expect(otherPhase).toBeDisabled();
    fireEvent.click(otherPhase);
    expect(screen.getByRole("tab", { name: /P00/u })).toHaveAttribute("aria-selected", "true");
    const focusOther = vi.spyOn(otherPhase, "focus");
    const animationFrame = vi.spyOn(window, "requestAnimationFrame").mockImplementation((callback) => {
      callback(0);
      return 1;
    });
    fireEvent.keyDown(screen.getByRole("tab", { name: /P00/u }), { key: "ArrowDown" });
    expect(focusOther).not.toHaveBeenCalled();
    animationFrame.mockRestore();
    fireEvent.click(screen.getByRole("button", { name: "Confirm reopen and invalidate suffix" }));
    await waitFor(() => expect(fixture.reopenCreationPhase).toHaveBeenCalledWith(expect.objectContaining({
      phaseId: "p00_brief",
      reason: "The brief changed",
      approvedBy: "lead_reviewer",
      expectedWorkflowStatusHash: STATUS,
    })));

    fireEvent.click(screen.getByRole("button", { name: "Reconcile workflow" }));
    await waitFor(() => expect(fixture.reconcileCreationWorkflow).toHaveBeenCalledWith(expect.objectContaining({
      expectedWorkflowStatusHash: STATUS,
      artifactRegistry: [],
    })));
  });

  it("rejects validation evidence that does not equal the submitted phase report", async () => {
    const validateCreationPhase = vi.fn().mockResolvedValue(v3("creation_phase.validate", {
      workspace: workspace(),
      workflow: workflow(),
      report: { ...phaseReport("p01_genre_style"), status: "not_applicable" },
    }));
    installPhaseApi({ validateCreationPhase });
    render(<CreationPhaseWorkspace
      workspace={workspace()}
      workflow={workflow()}
      onNavigationStateChange={vi.fn()}
      onWorkflowRefresh={vi.fn().mockResolvedValue(undefined)}
    />);

    fireEvent.click(await screen.findByRole("tab", { name: /P01/u }));
    fireEvent.change(screen.getByLabelText("Phase report JSON"), {
      target: { value: JSON.stringify(phaseReport("p01_genre_style"), null, 2) },
    });
    fireEvent.change(screen.getByLabelText("Artifact registry JSON"), {
      target: { value: "[]" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Validate phase report" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "mismatched phase validation evidence",
    );
    expect(screen.getByRole("button", { name: "Complete reviewed phase" })).toBeDisabled();
  });

  it("retains phase inputs when completion authority evidence is incoherent", async () => {
    const completeCreationPhase = vi.fn().mockResolvedValue(v3("creation_phase.complete", {
      workspace: workspace(),
      workflow: { ...workflow(), status_hash: "9".repeat(64), revision: 2 },
    }));
    const refresh = vi.fn().mockResolvedValue(undefined);
    installPhaseApi({ completeCreationPhase });
    render(<CreationPhaseWorkspace
      workspace={workspace()}
      workflow={workflow()}
      onNavigationStateChange={vi.fn()}
      onWorkflowRefresh={refresh}
    />);

    fireEvent.click(await screen.findByRole("tab", { name: /P01/u }));
    const editor = screen.getByLabelText("Phase report JSON");
    fireEvent.change(editor, {
      target: { value: JSON.stringify(phaseReport("p01_genre_style"), null, 2) },
    });
    fireEvent.change(screen.getByLabelText("Artifact registry JSON"), {
      target: { value: "[]" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Validate phase report" }));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Complete reviewed phase" })).toBeEnabled(),
    );
    fireEvent.click(screen.getByRole("button", { name: "Complete reviewed phase" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "mismatched phase completion authority",
    );
    expect((editor as HTMLTextAreaElement).value).toContain('"phase": "p01_genre_style"');
    expect(refresh).not.toHaveBeenCalled();
  });

  it.each([
    ["root generation", 6, 2],
    ["workflow revision", 5, 3],
  ])("rejects completion evidence that skips the next %s", async (_label, rootGeneration, revision) => {
    const advancedWorkflow = { ...workflow(), status_hash: "9".repeat(64), revision };
    const completeCreationPhase = vi.fn().mockResolvedValue(v3("creation_phase.complete", {
      workspace: {
        ...workspace(),
        root_generation: rootGeneration,
        workflow_status_hash: advancedWorkflow.status_hash,
      },
      workflow: advancedWorkflow,
    }));
    const refresh = vi.fn().mockResolvedValue(undefined);
    installPhaseApi({ completeCreationPhase });
    render(<CreationPhaseWorkspace
      workspace={workspace()}
      workflow={workflow()}
      onNavigationStateChange={vi.fn()}
      onWorkflowRefresh={refresh}
    />);

    fireEvent.click(await screen.findByRole("tab", { name: /P01/u }));
    fireEvent.change(screen.getByLabelText("Phase report JSON"), {
      target: { value: JSON.stringify(phaseReport("p01_genre_style"), null, 2) },
    });
    fireEvent.change(screen.getByLabelText("Artifact registry JSON"), {
      target: { value: "[]" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Validate phase report" }));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Complete reviewed phase" })).toBeEnabled(),
    );
    fireEvent.click(screen.getByRole("button", { name: "Complete reviewed phase" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "mismatched phase completion authority",
    );
    expect(refresh).not.toHaveBeenCalled();
  });

  it("restores focus to the current report editor after reopening removes the trigger", async () => {
    const fixture = installPhaseApi();
    const renderState: { rerender: ReturnType<typeof render>["rerender"] | null } = {
      rerender: null,
    };
    const refresh = vi.fn(() => {
      if (!renderState.rerender) throw new Error("Phase fixture is not mounted");
      renderState.rerender(<CreationPhaseWorkspace
        workspace={{ ...workspace(), root_generation: 5, workflow_status_hash: "9".repeat(64) }}
        workflow={reopenedWorkflow()}
        onNavigationStateChange={vi.fn()}
        onWorkflowRefresh={refresh}
      />);
      return Promise.resolve();
    });
    renderState.rerender = render(<CreationPhaseWorkspace
      workspace={workspace()}
      workflow={workflow()}
      onNavigationStateChange={vi.fn()}
      onWorkflowRefresh={refresh}
    />).rerender;

    fireEvent.click(await screen.findByRole("tab", { name: /P00/u }));
    fireEvent.click(screen.getByRole("button", { name: "Reopen reviewed phase" }));
    fireEvent.change(screen.getByLabelText("Reopen reason"), {
      target: { value: "The brief changed" },
    });
    fireEvent.change(screen.getByLabelText("Approved by"), {
      target: { value: "lead_reviewer" },
    });
    fireEvent.click(screen.getByRole("button", {
      name: "Confirm reopen and invalidate suffix",
    }));

    await waitFor(() => expect(fixture.reopenCreationPhase).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.getByLabelText("Phase report JSON")).toHaveFocus());
    expect(screen.queryByRole("button", { name: "Reopen reviewed phase" })).not.toBeInTheDocument();
  });

  it("offers an explicit authority refresh after an immutable phase read fails", async () => {
    installPhaseApi({
      readCreationPhase: vi.fn().mockRejectedValue(new Error("Creation phase authority changed")),
    });
    const refresh = vi.fn().mockResolvedValue(undefined);
    render(<CreationPhaseWorkspace
      workspace={workspace()}
      workflow={workflow()}
      onNavigationStateChange={vi.fn()}
      onWorkflowRefresh={refresh}
    />);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Creation phase authority changed",
    );
    fireEvent.click(screen.getByRole("button", { name: "Refresh workspace authority" }));
    await waitFor(() => expect(refresh).toHaveBeenCalledTimes(1));
  });
});

function installPhaseApi(overrides: Partial<ForgeStudioApi> = {}) {
  const readCreationPhase = vi.fn().mockResolvedValue(v3("creation_phase.read", {
    workspace: workspace(),
    workflow: workflow(),
    reference: { phase: "p00_brief", status: "ready", content_hash: REPORT_HASH, invalidation_dependencies: [{}] },
    report: phaseReport("p00_brief"),
  }));
  const validateCreationPhase = vi.fn(() => Promise.resolve(v3("creation_phase.validate", {
    workspace: workspace(), workflow: workflow(), report: phaseReport("p01_genre_style"),
  })));
  const advancedWorkflow = { ...workflow(), status_hash: "9".repeat(64), revision: 2 };
  const advancedWorkspace = {
    ...workspace(),
    root_generation: 5,
    workflow_status_hash: advancedWorkflow.status_hash,
  };
  const completeCreationPhase = vi.fn().mockResolvedValue(v3("creation_phase.complete", { workspace: advancedWorkspace, workflow: advancedWorkflow }));
  const reopenCreationPhase = vi.fn().mockResolvedValue(v3("creation_phase.reopen", { workspace: advancedWorkspace, workflow: advancedWorkflow }));
  const reconcileCreationWorkflow = vi.fn().mockResolvedValue(v3("creation_workflow.reconcile", { workspace: advancedWorkspace, workflow: advancedWorkflow }));
  window.forgeStudio = {
    readCreationPhase,
    validateCreationPhase,
    completeCreationPhase,
    reopenCreationPhase,
    reconcileCreationWorkflow,
    ...overrides,
  } as unknown as ForgeStudioApi;
  return { readCreationPhase, validateCreationPhase, completeCreationPhase, reopenCreationPhase, reconcileCreationWorkflow };
}

function workspace() {
  return {
    format: "world-forge.studio_creation_workspace" as const,
    format_version: 1 as const,
    workspace_id: "creation_workspace",
    project: { format: "world-forge.project" as const, format_version: 1 as const, id: "neutral_universe", content_hash: "4".repeat(64) },
    project_kind: "universe_library" as const,
    source_revision: SOURCE,
    workflow_status_hash: STATUS,
    root_generation: 4,
    created_at: "2026-08-01T00:00:00Z",
    updated_at: "2026-08-01T00:00:00Z",
  };
}

function workflow() {
  return {
    state: "active" as const,
    source_revision: SOURCE,
    status_hash: STATUS,
    current_phase: "p01_genre_style",
    revision: 1,
    status: {
      format: "world-forge.creation_workflow_status",
      format_version: 1,
      current_phase: "p01_genre_style",
      completed_phases: ["p00_brief"],
      reports: [{ phase: "p00_brief", status: "ready", content_hash: REPORT_HASH, invalidation_dependencies: [{}] }],
      invalidated_reports: [],
    },
  };
}

function reopenedWorkflow() {
  return {
    ...workflow(),
    status_hash: "9".repeat(64),
    current_phase: "p00_brief",
    revision: 2,
    status: {
      ...workflow().status,
      current_phase: "p00_brief",
      completed_phases: [],
      reports: [],
    },
  };
}

function phaseReport(phase: string) {
  return { format: "world-forge.phase_report", format_version: 3, phase, status: "ready", content_hash: REPORT_HASH };
}

function v3(method: string, result: Record<string, unknown>) {
  return { ok: true as const, value: { protocol: "rpg-world-forge.studio_protocol" as const, protocol_version: 3 as const, kind: "response" as const, request_id: "request_01", method, result } };
}

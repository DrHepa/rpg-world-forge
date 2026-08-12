import { useEffect, useMemo, useRef, useState } from "react";

import type {
  StudioCreationWorkflowResult,
  StudioCreationWorkspace,
} from "../shared/studio-api";
import {
  creationArtifactRegistryPreview,
  creationPhaseReportPreview,
  creationPhaseValidationFingerprint,
  parseCreationArtifactRegistryJson,
  parseCreationPhaseReportJson,
  summarizeCreationPhaseStates,
  type CreationPhaseAuthorityFingerprint,
  type CreationPhaseId,
  type CreationPhaseSummary,
} from "./creation-phases";
import { expectCreationResult } from "./creation-service";
import type { CreationNavigationState } from "./creation-state";

interface CreationPhaseWorkspaceProps {
  workspace: StudioCreationWorkspace;
  workflow: StudioCreationWorkflowResult["workflow"];
  onNavigationStateChange: (state: CreationNavigationState) => void;
  onWorkflowRefresh: () => Promise<void>;
}

interface LoadedPhaseReport {
  reference: {
    phase: CreationPhaseId;
    status: "ready" | "not_applicable";
    contentHash: string;
  };
  report: Record<string, unknown>;
}

type PendingAction =
  | "load"
  | "refresh"
  | "validate"
  | "complete"
  | "reopen"
  | "reconcile"
  | null;
const EMPTY_REPORT = "{}\n";
const EMPTY_REGISTRY = "[]\n";

export function CreationPhaseWorkspace({
  workspace,
  workflow,
  onNavigationStateChange,
  onWorkflowRefresh,
}: CreationPhaseWorkspaceProps) {
  const phaseStates = useMemo(
    () => summarizeCreationPhaseStates(workflow.status),
    [workflow.status],
  );
  const initialPhase = phaseStates.find((phase) => phase.state === "current")?.id ?? "p00_brief";
  const [selectedPhase, setSelectedPhase] = useState<CreationPhaseId>(initialPhase);
  const [reports, setReports] = useState<Map<CreationPhaseId, LoadedPhaseReport>>(new Map());
  const [reportText, setReportText] = useState(EMPTY_REPORT);
  const [registryText, setRegistryText] = useState(EMPTY_REGISTRY);
  const [validatedFingerprint, setValidatedFingerprint] = useState<string | null>(null);
  const [validationMessage, setValidationMessage] = useState<string | null>(null);
  const [reopenVisible, setReopenVisible] = useState(false);
  const [reopenReason, setReopenReason] = useState("");
  const [approvedBy, setApprovedBy] = useState("");
  const [pending, setPending] = useState<PendingAction>("load");
  const [error, setError] = useState<string | null>(null);
  const [authorityRefreshAvailable, setAuthorityRefreshAvailable] = useState(false);
  const tokenRef = useRef(0);
  const reopenReasonRef = useRef<HTMLTextAreaElement>(null);
  const reopenTriggerRef = useRef<HTMLButtonElement>(null);
  const selected = phaseStates.find((phase) => phase.id === selectedPhase) ?? phaseStates[0];
  const loadedReport = reports.get(selectedPhase) ?? null;
  const authority = useMemo<CreationPhaseAuthorityFingerprint | null>(
    () => workflow.status_hash === null ? null : {
      expectedRootGeneration: workspace.root_generation,
      expectedSourceRevision: workspace.source_revision,
      expectedWorkflowStatusHash: workflow.status_hash,
    },
    [workflow.status_hash, workspace.root_generation, workspace.source_revision],
  );
  const currentFingerprint = authority
    ? safeFingerprint(authority, reportText, registryText, selectedPhase)
    : null;
  const completionEnabled = selected.state === "current" && pending === null &&
    validatedFingerprint !== null && currentFingerprint === validatedFingerprint;
  const draftDirty = reportText !== EMPTY_REPORT || registryText !== EMPTY_REGISTRY ||
    validatedFingerprint !== null || reopenVisible || reopenReason.length > 0 || approvedBy.length > 0;

  useEffect(() => {
    onNavigationStateChange({ blocksNavigation: draftDirty, kind: draftDirty ? "draft" : "clean" });
  }, [draftDirty, onNavigationStateChange]);

  useEffect(() => {
    const token = tokenRef.current + 1;
    tokenRef.current = token;
    let active = true;
    queueMicrotask(() => {
      if (!active) return;
      const completed = phaseStates.filter(
        (phase) => phase.state === "ready" || phase.state === "not_applicable",
      );
      if (!authority || completed.length === 0) {
        setReports(new Map());
        setPending(null);
        return;
      }
      setPending("load");
      setError(null);
      setAuthorityRefreshAvailable(false);
      void Promise.all(completed.map(async (phase) => {
        const result = await expectCreationResult(
          window.forgeStudio.readCreationPhase({
            workspaceId: workspace.workspace_id,
            ...authority,
            phaseId: phase.id,
          }),
          "creation_phase.read",
        );
        return validatePhaseReadResult(result, workspace, workflow, phase);
      })).then((loaded) => {
        if (tokenRef.current !== token) return;
        setReports(new Map(loaded.map((report) => [report.reference.phase, report])));
      }).catch((caught) => {
        if (tokenRef.current === token) {
          setError(describeError(caught));
          setAuthorityRefreshAvailable(true);
        }
      }).finally(() => {
        if (tokenRef.current === token) setPending(null);
      });
    });
    return () => {
      active = false;
      tokenRef.current += 1;
    };
  }, [authority, phaseStates, workspace, workflow]);

  useEffect(() => {
    if (reopenVisible) reopenReasonRef.current?.focus();
  }, [reopenVisible]);

  function selectPhase(phase: CreationPhaseSummary): boolean {
    if (draftDirty && phase.id !== selectedPhase) return false;
    setSelectedPhase(phase.id);
    setError(null);
    setAuthorityRefreshAvailable(false);
    setValidationMessage(null);
    return true;
  }

  function movePhase(
    event: React.KeyboardEvent<HTMLButtonElement>,
    phase: CreationPhaseSummary,
  ): void {
    const index = phaseStates.indexOf(phase);
    let next: CreationPhaseSummary | null = null;
    if (event.key === "Home") next = phaseStates[0];
    else if (event.key === "End") next = phaseStates.at(-1) ?? null;
    else if (event.key === "ArrowDown" || event.key === "ArrowRight") next = phaseStates[(index + 1) % phaseStates.length];
    else if (event.key === "ArrowUp" || event.key === "ArrowLeft") next = phaseStates[(index - 1 + phaseStates.length) % phaseStates.length];
    if (!next) return;
    event.preventDefault();
    if (!selectPhase(next)) return;
    window.requestAnimationFrame(() => document.querySelector<HTMLButtonElement>(`#creation-phase-${next.id}`)?.focus());
  }

  async function refreshAuthority(): Promise<void> {
    if (pending) return;
    setPending("refresh");
    setError(null);
    try {
      await onWorkflowRefresh();
      setAuthorityRefreshAvailable(false);
    } catch (caught) {
      setError(describeError(caught));
      setAuthorityRefreshAvailable(true);
    } finally {
      setPending(null);
    }
  }

  async function validateReport(): Promise<void> {
    if (!authority || selected.state !== "current" || pending) return;
    setPending("validate");
    setError(null);
    setAuthorityRefreshAvailable(false);
    setValidationMessage(null);
    try {
      const report = parseCreationPhaseReportJson(reportText, selected.id);
      const artifactRegistry = parseCreationArtifactRegistryJson(registryText);
      const result = await expectCreationResult(
        window.forgeStudio.validateCreationPhase({
          workspaceId: workspace.workspace_id,
          ...authority,
          report,
          artifactRegistry,
        }),
        "creation_phase.validate",
      );
      const validated = validatePhaseValidationResult(
        result,
        workspace,
        workflow,
        report,
        selected.id,
      );
      const normalizedReport = creationPhaseReportPreview(validated);
      const normalizedRegistry = creationArtifactRegistryPreview(artifactRegistry);
      setReportText(normalizedReport);
      setRegistryText(normalizedRegistry);
      setValidatedFingerprint(creationPhaseValidationFingerprint(authority, validated, artifactRegistry));
      setValidationMessage("The exact report and artifact registry are valid for this workflow authority.");
    } catch (caught) {
      setValidatedFingerprint(null);
      setError(describeError(caught));
      setAuthorityRefreshAvailable(isAuthorityFailure(caught));
    } finally {
      setPending(null);
    }
  }

  async function completeReport(): Promise<void> {
    if (!authority || !completionEnabled || pending) return;
    setPending("complete");
    setError(null);
    setAuthorityRefreshAvailable(false);
    try {
      const report = parseCreationPhaseReportJson(reportText, selected.id);
      const artifactRegistry = parseCreationArtifactRegistryJson(registryText);
      const fingerprint = creationPhaseValidationFingerprint(authority, report, artifactRegistry);
      if (fingerprint !== validatedFingerprint) throw new Error("Phase inputs changed after validation");
      const result = await expectCreationResult(
        window.forgeStudio.completeCreationPhase({
          workspaceId: workspace.workspace_id,
          ...authority,
          report,
          artifactRegistry,
        }),
        "creation_phase.complete",
      );
      validatePhaseAuthorityResult(result, workspace, workflow, "completion", true);
      await onWorkflowRefresh();
      setReportText(EMPTY_REPORT);
      setRegistryText(EMPTY_REGISTRY);
      setValidatedFingerprint(null);
      setValidationMessage(null);
      focusPhaseTarget(selected.id);
    } catch (caught) {
      setError(describeError(caught));
      setAuthorityRefreshAvailable(isAuthorityFailure(caught));
    } finally {
      setPending(null);
    }
  }

  async function reopenPhase(): Promise<void> {
    if (!authority || (selected.state !== "ready" && selected.state !== "not_applicable") || pending) return;
    setPending("reopen");
    setError(null);
    setAuthorityRefreshAvailable(false);
    try {
      const result = await expectCreationResult(
        window.forgeStudio.reopenCreationPhase({
          workspaceId: workspace.workspace_id,
          ...authority,
          phaseId: selected.id,
          reason: reopenReason,
          approvedBy,
        }),
        "creation_phase.reopen",
      );
      validatePhaseAuthorityResult(result, workspace, workflow, "reopen", true);
      await onWorkflowRefresh();
      setReopenVisible(false);
      setReopenReason("");
      setApprovedBy("");
      focusPhaseTarget(selected.id);
    } catch (caught) {
      setError(describeError(caught));
      setAuthorityRefreshAvailable(isAuthorityFailure(caught));
    } finally {
      setPending(null);
    }
  }

  async function reconcile(): Promise<void> {
    if (!authority || pending) return;
    setPending("reconcile");
    setError(null);
    setAuthorityRefreshAvailable(false);
    try {
      const artifactRegistry = parseCreationArtifactRegistryJson(registryText);
      const result = await expectCreationResult(
        window.forgeStudio.reconcileCreationWorkflow({
          workspaceId: workspace.workspace_id,
          ...authority,
          artifactRegistry,
        }),
        "creation_workflow.reconcile",
      );
      validatePhaseAuthorityResult(result, workspace, workflow, "reconciliation", true);
      await onWorkflowRefresh();
    } catch (caught) {
      setError(describeError(caught));
      setAuthorityRefreshAvailable(isAuthorityFailure(caught));
    } finally {
      setPending(null);
    }
  }

  return (
    <div className="creation-phase-workspace">
      <header className="creation-section-heading">
        <div>
          <p className="eyebrow">Profile-aware evidence</p>
          <h3>Reviewed creation phases</h3>
          <p>P00-P14 remain ordered, but reviewed <code>not_applicable</code> phases stay explicit rather than inventing irrelevant world or narrative content.</p>
        </div>
        {workflow.state === "invalid" && authority ? <button type="button" disabled={pending !== null} onClick={() => void reconcile()}>Reconcile workflow</button> : null}
      </header>
      {pending === "load" ? <p role="status">Loading immutable phase reports…</p> : null}
      {error ? (
        <div role="alert" className="inline-error">
          <p>{error}</p>
          {authorityRefreshAvailable ? (
            <button type="button" disabled={pending !== null} onClick={() => void refreshAuthority()}>
              Refresh workspace authority
            </button>
          ) : null}
        </div>
      ) : null}

      <div className="creation-phase-layout">
        <div className="creation-phase-rail" role="tablist" aria-orientation="vertical" aria-label="Creation phases">
          {phaseStates.map((phase) => (
            <button
              key={phase.id}
              id={`creation-phase-${phase.id}`}
              type="button"
              role="tab"
              aria-selected={selectedPhase === phase.id}
              aria-controls="creation-phase-panel"
              tabIndex={selectedPhase === phase.id ? 0 : -1}
              disabled={draftDirty && selectedPhase !== phase.id}
              onClick={() => { selectPhase(phase); }}
              onKeyDown={(event) => movePhase(event, phase)}
            >
              <span>{phase.short}</span>
              <strong>{phase.title}</strong>
              <small>{phaseStateLabel(phase.state)}</small>
            </button>
          ))}
        </div>

        <section id="creation-phase-panel" role="tabpanel" aria-labelledby={`creation-phase-${selected.id}`} className="creation-phase-panel">
          <header>
            <p className="eyebrow">{selected.short} · {phaseStateLabel(selected.state)}</p>
            <h4>{selected.title}</h4>
          </header>
          {selected.invalidationReason ? <p className="creation-phase-warning">Invalidated: {selected.invalidationReason}</p> : null}

          {loadedReport ? (
            <div className="creation-phase-evidence">
              <h5>Reviewed report evidence</h5>
              <dl>
                <div><dt>Status</dt><dd>{loadedReport.reference.status}</dd></div>
                <div><dt>Content hash</dt><dd><code>{loadedReport.reference.contentHash}</code></dd></div>
              </dl>
              <pre className="creation-json-preview">{creationPhaseReportPreview(loadedReport.report)}</pre>
              <button ref={reopenTriggerRef} type="button" disabled={pending !== null} onClick={() => setReopenVisible(true)}>Reopen reviewed phase</button>
            </div>
          ) : null}

          {reopenVisible ? (
            <div className="creation-reopen-form" role="group" aria-label="Reopen reviewed phase">
              <p>Reopening this phase invalidates its reviewed suffix. Existing reports remain immutable historical evidence.</p>
              <label>Reopen reason<textarea ref={reopenReasonRef} value={reopenReason} onChange={(event) => setReopenReason(event.target.value)} /></label>
              <label>Approved by<input value={approvedBy} onChange={(event) => setApprovedBy(event.target.value)} /></label>
              <div className="actions">
                <button type="button" disabled={pending !== null || reopenReason.trim().length === 0 || approvedBy.trim().length === 0} onClick={() => void reopenPhase()}>Confirm reopen and invalidate suffix</button>
                <button type="button" disabled={pending !== null} onClick={() => { setReopenVisible(false); setReopenReason(""); setApprovedBy(""); window.requestAnimationFrame(() => reopenTriggerRef.current?.focus()); }}>Cancel reopen</button>
              </div>
            </div>
          ) : null}

          {selected.state === "current" ? (
            <div className="creation-phase-editor">
              <label htmlFor="creation-phase-report-json">Phase report JSON</label>
              <textarea id="creation-phase-report-json" rows={20} value={reportText} onChange={(event) => { setReportText(event.target.value); setValidationMessage(null); }} />
              <label htmlFor="creation-artifact-registry-json">Artifact registry JSON</label>
              <textarea id="creation-artifact-registry-json" rows={8} value={registryText} onChange={(event) => { setRegistryText(event.target.value); setValidationMessage(null); }} />
              {validationMessage ? <p role="status" className="creation-validation-success">{validationMessage}</p> : null}
              <div className="actions">
                <button type="button" disabled={pending !== null || authority === null} onClick={() => void validateReport()}>Validate phase report</button>
                <button type="button" disabled={!completionEnabled} onClick={() => void completeReport()}>Complete reviewed phase</button>
              </div>
              <p>Completion is enabled only while report bytes, registry bytes, source revision, workflow hash, and root generation still match the validated fingerprint.</p>
            </div>
          ) : selected.state === "locked" ? <p>Complete the current reviewed phase before authoring this phase.</p> : null}
        </section>
      </div>
    </div>
  );
}

function safeFingerprint(
  authority: CreationPhaseAuthorityFingerprint,
  reportText: string,
  registryText: string,
  phaseId: CreationPhaseId,
): string | null {
  try {
    return creationPhaseValidationFingerprint(
      authority,
      parseCreationPhaseReportJson(reportText, phaseId),
      parseCreationArtifactRegistryJson(registryText),
    );
  } catch {
    return null;
  }
}

function validatePhaseReadResult(
  value: Record<string, unknown>,
  workspace: StudioCreationWorkspace,
  workflow: StudioCreationWorkflowResult["workflow"],
  phase: CreationPhaseSummary,
): LoadedPhaseReport {
  validatePhaseAuthorityResult(value, workspace, workflow, "read", false);
  const expectedStatus = phase.state === "not_applicable" ? "not_applicable" : "ready";
  if (!isRecord(value.reference) || Object.hasOwn(value.reference, "path") || value.reference.phase !== phase.id ||
    value.reference.status !== expectedStatus ||
    value.reference.content_hash !== phase.contentHash || !isRecord(value.report) ||
    value.report.phase !== phase.id || value.report.content_hash !== phase.contentHash) {
    throw new Error("Forge Studio returned mismatched immutable phase evidence");
  }
  return {
    reference: {
      phase: phase.id,
      status: expectedStatus,
      contentHash: String(value.reference.content_hash),
    },
    report: value.report,
  };
}

function validatePhaseValidationResult(
  value: Record<string, unknown>,
  workspace: StudioCreationWorkspace,
  workflow: StudioCreationWorkflowResult["workflow"],
  submitted: Record<string, unknown>,
  phase: CreationPhaseId,
): Record<string, unknown> {
  validatePhaseAuthorityResult(value, workspace, workflow, "validation", false);
  if (!isRecord(value.report)) {
    throw new Error("Forge Studio returned mismatched phase validation evidence");
  }
  const validated = parseCreationPhaseReportJson(
    creationPhaseReportPreview(value.report),
    phase,
  );
  if (creationPhaseReportPreview(validated) !== creationPhaseReportPreview(submitted)) {
    throw new Error("Forge Studio returned mismatched phase validation evidence");
  }
  return validated;
}

function validatePhaseAuthorityResult(
  value: Record<string, unknown>,
  workspace: StudioCreationWorkspace,
  workflow: StudioCreationWorkflowResult["workflow"],
  context: string,
  requireAdvance = false,
): void {
  const returnedWorkspace = value.workspace;
  const returnedWorkflow = value.workflow;
  if (
    !isRecord(returnedWorkspace) ||
    returnedWorkspace.workspace_id !== workspace.workspace_id ||
    returnedWorkspace.source_revision !== workspace.source_revision ||
    !sameProjectIdentity(returnedWorkspace.project, workspace.project) ||
    !isRecord(returnedWorkflow) ||
    returnedWorkflow.source_revision !== workspace.source_revision ||
    returnedWorkspace.workflow_status_hash !== returnedWorkflow.status_hash
  ) {
    throw new Error(`Forge Studio returned mismatched phase ${context} authority`);
  }
  if (requireAdvance) {
    if (
      !Number.isSafeInteger(returnedWorkspace.root_generation) ||
      returnedWorkspace.root_generation !== workspace.root_generation + 1 ||
      typeof returnedWorkflow.status_hash !== "string" ||
      !/^[0-9a-f]{64}$/u.test(returnedWorkflow.status_hash) ||
      returnedWorkflow.status_hash === workflow.status_hash ||
      !Number.isSafeInteger(workflow.revision) ||
      !Number.isSafeInteger(returnedWorkflow.revision) ||
      returnedWorkflow.revision !== Number(workflow.revision) + 1
    ) {
      throw new Error(`Forge Studio returned mismatched phase ${context} authority`);
    }
  } else if (
    returnedWorkspace.root_generation !== workspace.root_generation ||
    returnedWorkspace.workflow_status_hash !== workspace.workflow_status_hash ||
    returnedWorkflow.status_hash !== workflow.status_hash ||
    returnedWorkflow.current_phase !== workflow.current_phase ||
    returnedWorkflow.revision !== workflow.revision ||
    canonicalJson(returnedWorkflow.status) !== canonicalJson(workflow.status)
  ) {
    throw new Error(`Forge Studio returned mismatched phase ${context} authority`);
  }
}

function sameProjectIdentity(left: unknown, right: unknown): boolean {
  return isRecord(left) && isRecord(right) &&
    left.format === right.format && left.format_version === right.format_version &&
    left.id === right.id && left.content_hash === right.content_hash;
}

function canonicalJson(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (!isRecord(value)) {
    const encoded = JSON.stringify(value);
    if (encoded === undefined) throw new Error("Forge Studio returned non-JSON phase authority");
    return encoded;
  }
  return `{${Object.keys(value).sort().map((key) =>
    `${JSON.stringify(key)}:${canonicalJson(value[key])}`).join(",")}}`;
}

function focusPhaseTarget(phaseId: CreationPhaseId): void {
  window.requestAnimationFrame(() => {
    const reportEditor = document.querySelector<HTMLTextAreaElement>("#creation-phase-report-json");
    if (reportEditor) reportEditor.focus();
    else document.querySelector<HTMLButtonElement>(`#creation-phase-${phaseId}`)?.focus();
  });
}

function isAuthorityFailure(value: unknown): boolean {
  return value instanceof Error &&
    /authority|conflict|root generation|source revision|workflow/iu.test(value.message);
}

function phaseStateLabel(state: CreationPhaseSummary["state"]): string {
  if (state === "not_applicable") return "Reviewed not applicable";
  if (state === "ready") return "Reviewed ready";
  if (state === "current") return "Current";
  if (state === "invalidated") return "Invalidated";
  return "Locked";
}

function describeError(value: unknown): string {
  return value instanceof Error ? value.message : "Creation phase operation failed";
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

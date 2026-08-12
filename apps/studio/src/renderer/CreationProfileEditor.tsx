import { useEffect, useMemo, useRef, useState } from "react";

import type {
  StudioCreationChangeset,
  StudioCreationChangesetDiffResult,
  StudioCreationWorkflowResult,
  StudioCreationWorkspace,
} from "../shared/studio-api";
import { CreationServiceError, expectCreationResult } from "./creation-service";
import {
  CREATION_PROFILE_FACETS,
  creationProfilePreview,
  isCreationProfileDirty,
  parseCreationFacetJson,
  replaceCreationFacet,
  summarizeCreationFacet,
  type CreationNavigationKind,
  type CreationNavigationState,
  type CreationProfileDocument,
  type CreationProfileFacet,
} from "./creation-state";

interface CreationProfileEditorProps {
  workspace: StudioCreationWorkspace;
  workflow: StudioCreationWorkflowResult["workflow"];
  profilePath: string;
  profileFileSha256: string;
  baseProfile: CreationProfileDocument;
  draftProfile: CreationProfileDocument;
  onDraftChange: (profile: CreationProfileDocument) => void;
  onNavigationStateChange: (state: CreationNavigationState) => void;
  onAuthorityRefresh: () => Promise<void>;
  onApplied: () => Promise<void>;
}

type PendingAction = "stage" | "approve" | "apply" | "recover" | null;

export function CreationProfileEditor({
  workspace,
  workflow,
  profilePath,
  profileFileSha256,
  baseProfile,
  draftProfile,
  onDraftChange,
  onNavigationStateChange,
  onAuthorityRefresh,
  onApplied,
}: CreationProfileEditorProps) {
  const [editingFacet, setEditingFacet] = useState<CreationProfileFacet | null>(null);
  const [facetText, setFacetText] = useState("");
  const [facetError, setFacetError] = useState<string | null>(null);
  const [changeset, setChangeset] = useState<StudioCreationChangeset | null>(null);
  const [diff, setDiff] = useState<StudioCreationChangesetDiffResult["diff"] | null>(null);
  const [pending, setPending] = useState<PendingAction>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const reviewHeadingRef = useRef<HTMLHeadingElement>(null);
  const dirty = isCreationProfileDirty(baseProfile, draftProfile);
  const preview = useMemo(
    () => creationProfilePreview(draftProfile),
    [draftProfile],
  );
  const facetBaseline = editingFacet === null
    ? ""
    : `${JSON.stringify(draftProfile[editingFacet], null, 2)}\n`;
  const facetBufferDirty = editingFacet !== null && facetText !== facetBaseline;
  const navigationKind = changesetNavigationKind(changeset?.status) ??
    (facetBufferDirty ? "facet_buffer" : dirty ? "draft" : "clean");

  const changesetId = changeset?.changeset_id ?? null;
  useEffect(() => {
    if (changesetId) reviewHeadingRef.current?.focus();
  }, [changesetId]);

  useEffect(() => {
    reportNavigation(onNavigationStateChange, navigationKind);
  }, [navigationKind, onNavigationStateChange]);

  function beginFacetEdit(facet: CreationProfileFacet): void {
    if (changeset || pending) return;
    setEditingFacet(facet);
    setFacetText(`${JSON.stringify(draftProfile[facet], null, 2)}\n`);
    setFacetError(null);
  }

  function updateFacet(): void {
    if (!editingFacet) return;
    try {
      const value = parseCreationFacetJson(facetText, editingFacet);
      const nextProfile = replaceCreationFacet(draftProfile, editingFacet, value);
      onDraftChange(nextProfile);
      setFacetText(`${JSON.stringify(nextProfile[editingFacet], null, 2)}\n`);
      setFacetError(null);
    } catch (error) {
      setFacetError(describeError(error));
    }
  }

  async function stageProfile(): Promise<void> {
    if (!dirty || facetBufferDirty || pending) return;
    setPending("stage");
    setActionError(null);
    setChangeset(null);
    setDiff(null);
    try {
      const staged = await expectCreationResult(
        window.forgeStudio.stageCreationProfile({
          workspaceId: workspace.workspace_id,
          expectedRootGeneration: workspace.root_generation,
          expectedSourceRevision: workspace.source_revision,
          expectedWorkflowStatusHash: workflow.status_hash,
          path: profilePath,
          expectedBaseFileSha256: profileFileSha256,
          proposedProfile: draftProfile,
        }),
        "creation_changeset.create",
      );
      const stagedRecord = requireProfileChangeset(staged.changeset, {
        workspaceId: workspace.workspace_id,
        expectedRootGeneration: workspace.root_generation,
        expectedSourceRevision: workspace.source_revision,
        expectedWorkflowStatusHash: workflow.status_hash,
        profilePath,
        expectedBaseFileSha256: profileFileSha256,
      });
      if (stagedRecord.status !== "staged" && stagedRecord.status !== "recovery_required") {
        throw new Error("Forge Studio returned mismatched creation changeset evidence");
      }
      const [recordResult, diffResult] = await Promise.all([
        expectCreationResult(
          window.forgeStudio.getCreationChangeset(stagedRecord.changeset_id),
          "creation_changeset.get",
        ),
        expectCreationResult(
          window.forgeStudio.diffCreationChangeset(stagedRecord.changeset_id),
          "creation_changeset.diff",
        ),
      ]);
      const record = requireProfileChangeset(recordResult.changeset, {
        workspaceId: workspace.workspace_id,
        status: stagedRecord.status,
        immutable: stagedRecord,
        profilePath,
        expectedBaseFileSha256: profileFileSha256,
      });
      const reviewedDiff = requireProfileDiff(diffResult.diff, record);
      setChangeset(record);
      setDiff(reviewedDiff);
      setEditingFacet(null);
      reportNavigation(onNavigationStateChange, changesetNavigationKind(record.status) ?? "draft");
    } catch (error) {
      setActionError(describeError(error));
      if (error instanceof CreationServiceError && error.code === "conflict") {
        await onAuthorityRefresh();
      }
    } finally {
      setPending(null);
    }
  }

  async function approveProfile(): Promise<void> {
    if (!changeset || changeset.status !== "staged" || pending) return;
    setPending("approve");
    setActionError(null);
    try {
      const result = await expectCreationResult(
        window.forgeStudio.approveCreationChangeset(
          changeset.changeset_id,
          changeset.record_hash,
          changeset.review_sha256,
        ),
        "creation_changeset.approve",
      );
      const approved = requireProfileChangeset(result.changeset, {
        workspaceId: workspace.workspace_id,
        status: "approved",
        immutable: changeset,
        profilePath,
        expectedBaseFileSha256: profileFileSha256,
      });
      setChangeset(approved);
      reportNavigation(onNavigationStateChange, changesetNavigationKind(approved.status) ?? "draft");
    } catch (error) {
      setActionError(describeError(error));
      await refreshChangesetAfterFailure();
    } finally {
      setPending(null);
    }
  }

  async function applyProfile(): Promise<void> {
    if (!changeset || changeset.status !== "approved" || pending) return;
    setPending("apply");
    setActionError(null);
    try {
      const result = await expectCreationResult(
        window.forgeStudio.applyCreationChangeset(
          changeset.changeset_id,
          changeset.record_hash,
          changeset.review_sha256,
          workspace.root_generation,
        ),
        "creation_changeset.apply",
      );
      requireProfileChangeset(result.changeset, {
        workspaceId: workspace.workspace_id,
        status: "applied",
        immutable: changeset,
        profilePath,
        expectedBaseFileSha256: profileFileSha256,
        terminalAction: "apply",
      });
      await onApplied();
      setChangeset(null);
      setDiff(null);
      reportNavigation(onNavigationStateChange, "clean");
    } catch (error) {
      setActionError(describeError(error));
      await refreshChangesetAfterFailure();
      if (error instanceof CreationServiceError && error.code === "conflict") {
        await onAuthorityRefresh();
      }
    } finally {
      setPending(null);
    }
  }

  async function recoverProfile(mode: "resume" | "rollback"): Promise<void> {
    if (
      !changeset ||
      (changeset.status !== "applying" && changeset.status !== "recovery_required") ||
      pending
    ) return;
    setPending("recover");
    setActionError(null);
    try {
      const result = await expectCreationResult(
        window.forgeStudio.recoverCreationChangeset(
          changeset.changeset_id,
          mode,
          changeset.record_hash,
          changeset.review_sha256,
          workspace.root_generation,
        ),
        "creation_changeset.recover",
      );
      const recovered = requireProfileChangeset(result.changeset, {
        workspaceId: workspace.workspace_id,
        immutable: changeset,
        profilePath,
        expectedBaseFileSha256: profileFileSha256,
      });
      requireRecoveryTerminal(result.outcome, recovered.status, mode);
      await onApplied();
      setChangeset(null);
      setDiff(null);
      reportNavigation(onNavigationStateChange, "clean");
    } catch (error) {
      setActionError(describeError(error));
      await refreshChangesetAfterFailure();
    } finally {
      setPending(null);
    }
  }

  async function refreshChangesetAfterFailure(): Promise<void> {
    if (!changeset) return;
    try {
      const result = await expectCreationResult(
        window.forgeStudio.getCreationChangeset(changeset.changeset_id),
        "creation_changeset.get",
      );
      const refreshed = requireProfileChangeset(result.changeset, {
        workspaceId: workspace.workspace_id,
        immutable: changeset,
        profilePath,
        expectedBaseFileSha256: profileFileSha256,
      });
      setChangeset(refreshed);
      reportNavigation(
        onNavigationStateChange,
        changesetNavigationKind(refreshed.status) ?? "draft",
      );
    } catch {
      // Preserve the reviewed record already visible; the primary error remains authoritative.
    }
  }

  return (
    <div className="creation-profile-editor">
      <header className="creation-profile-heading">
        <div>
          <p className="eyebrow">Composable contract</p>
          <h3>Creation profile</h3>
          <p>
            Edit one bounded facet at a time. Python validates the complete profile and
            source graph before a changeset can be staged.
          </p>
        </div>
        <div className="draft-state">
          <strong>{dirty ? "Draft differs from the verified profile" : "Verified profile"}</strong>
          <small>No autosave. Staging creates reviewed evidence only.</small>
        </div>
      </header>

      <div className="creation-facet-grid">
        {CREATION_PROFILE_FACETS.map((facet) => (
          <section className="creation-facet-card" key={facet}>
            <header>
              <div>
                <p className="eyebrow">{facet.replaceAll("_", " ")}</p>
                <h3>{facetTitle(facet)}</h3>
              </div>
              <button
                type="button"
                className="secondary compact"
                disabled={changeset !== null || pending !== null}
                onClick={() => beginFacetEdit(facet)}
              >
                Edit {facetTitle(facet)} JSON
              </button>
            </header>
            <p>{summarizeCreationFacet(facet, draftProfile[facet])}</p>
            {editingFacet === facet ? (
              <div className="facet-json-editor">
                <label htmlFor={`creation-facet-${facet}`}>
                  {facetTitle(facet)} facet JSON
                </label>
                <textarea
                  id={`creation-facet-${facet}`}
                  value={facetText}
                  spellCheck={false}
                  disabled={changeset !== null || pending !== null}
                  onChange={(event) => {
                    const next = event.target.value;
                    setFacetText(next);
                    reportNavigation(
                      onNavigationStateChange,
                      next !== facetBaseline ? "facet_buffer" : dirty ? "draft" : "clean",
                    );
                  }}
                />
                <div className="actions">
                  <button
                    type="button"
                    disabled={changeset !== null || pending !== null}
                    onClick={updateFacet}
                  >
                    Update {facetTitle(facet)} draft
                  </button>
                  <button
                    type="button"
                    className="secondary"
                    disabled={changeset !== null || pending !== null}
                    onClick={() => {
                      setEditingFacet(null);
                      setFacetError(null);
                      reportNavigation(onNavigationStateChange, dirty ? "draft" : "clean");
                    }}
                  >
                    {facetBufferDirty ? "Discard typed facet changes" : "Close editor"}
                  </button>
                </div>
                {facetError ? <p role="alert" className="inline-error">{facetError}</p> : null}
              </div>
            ) : null}
          </section>
        ))}
      </div>

      <section className="creation-profile-preview" aria-labelledby="creation-preview-heading">
        <div className="section-heading">
          <div>
            <p className="eyebrow">Read-only</p>
            <h3 id="creation-preview-heading">Normalized whole profile</h3>
          </div>
          <span>{preview.length.toLocaleString("en-US")} chars</span>
        </div>
        <pre aria-label="Normalized creation profile preview">{preview}</pre>
      </section>

      <div className="creation-stage-row">
        <button
          type="button"
          disabled={!dirty || facetBufferDirty || pending !== null || changeset !== null}
          onClick={() => void stageProfile()}
        >
          {pending === "stage" ? "Staging profile…" : "Stage profile for review"}
        </button>
        <span role="status" aria-live="polite">
          {pending ? `${facetTitle(pending)} in progress` : "No repository write occurs before approval and apply."}
        </span>
      </div>
      {actionError ? <p role="alert" className="inline-error">{actionError}</p> : null}

      {changeset ? (
        <section className="creation-review" aria-labelledby="creation-review-heading">
          <p className="eyebrow">Exact reviewed changeset</p>
          <h3 id="creation-review-heading" ref={reviewHeadingRef} tabIndex={-1}>
            Profile review
          </h3>
          <dl className="creation-facts">
            <div><dt>Status</dt><dd>{changeset.status.replaceAll("_", " ")}</dd></div>
            <div><dt>Record hash</dt><dd><code>{changeset.record_hash}</code></dd></div>
            <div><dt>Review hash</dt><dd><code>{changeset.review_sha256}</code></dd></div>
            <div><dt>Proposed revision</dt><dd><code>{changeset.proposed_source_revision}</code></dd></div>
          </dl>
          {changeset.status === "applying" || changeset.status === "recovery_required" ? (
            <div className="creation-recovery" role="alert">
              <strong>
                {changeset.status === "applying" ? "Apply state unresolved" : "Recovery required"}
              </strong>
              <p>
                {changeset.status === "applying"
                  ? "The apply request did not return a terminal result. Resolve its bound journal before leaving."
                  : "The prior apply did not reach a provably terminal state."}
              </p>
              <div className="actions">
                <button
                  type="button"
                  disabled={pending !== null}
                  onClick={() => void recoverProfile("resume")}
                >
                  {changeset.status === "applying" ? "Resume unresolved apply" : "Resume recovery"}
                </button>
                <button
                  type="button"
                  className="danger"
                  disabled={pending !== null}
                  onClick={() => void recoverProfile("rollback")}
                >
                  {changeset.status === "applying" ? "Roll back unresolved apply" : "Roll back recovery"}
                </button>
              </div>
            </div>
          ) : null}
          {diff ? (
            <div className="creation-diff" aria-label="Bounded creation profile diff">
              <strong>{diff.operations.length} operation{diff.operations.length === 1 ? "" : "s"}</strong>
              <ul>
                {diff.operations.slice(0, 32).map((operation) => (
                  <li key={`${operation.operation}:${operation.path}`}>
                    <code>{operation.path}</code>
                    <span>{operation.operation} · {formatDelta(operation.size_delta)} bytes</span>
                  </li>
                ))}
              </ul>
              {diff.operations.length > 32 ? <p>Additional operations are omitted from this bounded view.</p> : null}
            </div>
          ) : null}
          <div className="actions">
            {changeset.status === "staged" ? (
              <button type="button" disabled={pending !== null} onClick={() => void approveProfile()}>
                Approve profile changeset
              </button>
            ) : null}
            {changeset.status === "approved" ? (
              <button type="button" disabled={pending !== null} onClick={() => void applyProfile()}>
                Apply approved profile
              </button>
            ) : null}
          </div>
        </section>
      ) : null}
    </div>
  );
}

interface ProfileChangesetExpectation {
  workspaceId: string;
  status?: StudioCreationChangeset["status"];
  expectedRootGeneration?: number;
  expectedSourceRevision?: string;
  expectedWorkflowStatusHash?: string | null;
  profilePath: string;
  expectedBaseFileSha256: string;
  immutable?: StudioCreationChangeset;
  terminalAction?: "apply";
}

function requireProfileChangeset(
  value: unknown,
  expectation: ProfileChangesetExpectation,
): StudioCreationChangeset {
  if (
    !isRecord(value) ||
    value.format !== "world-forge.studio_creation_changeset" ||
    value.format_version !== 1 ||
    value.workspace_id !== expectation.workspaceId ||
    typeof value.changeset_id !== "string" ||
    !isChangesetStatus(value.status) ||
    !Number.isSafeInteger(value.expected_root_generation) ||
    Number(value.expected_root_generation) < 0 ||
    !isSha256(value.expected_source_revision) ||
    !isSha256(value.proposed_source_revision) ||
    !(value.expected_workflow_status_hash === null ||
      isSha256(value.expected_workflow_status_hash)) ||
    !isSha256(value.review_sha256) ||
    !isSha256(value.record_hash) ||
    typeof value.created_at !== "string" ||
    typeof value.updated_at !== "string" ||
    !Array.isArray(value.operations) ||
    value.operations.length < 1 ||
    value.operations.length > 256 ||
    !value.operations.some((operation) =>
      isReplaceOperation(
        operation,
        expectation.profilePath,
        expectation.expectedBaseFileSha256,
      ),
    )
  ) {
    throw new Error("Forge Studio returned an invalid creation changeset");
  }
  const record = value as unknown as StudioCreationChangeset;
  if (expectation.terminalAction === "apply" && record.status !== "applied") {
    throw new Error("Forge Studio returned a non-terminal creation changeset");
  }
  if (expectation.status !== undefined && record.status !== expectation.status) {
    throw new Error("Forge Studio returned mismatched creation changeset evidence");
  }
  if (expectation.immutable) {
    if (!sameImmutableChangesetEvidence(expectation.immutable, record)) {
      throw new Error("Forge Studio returned mismatched creation changeset evidence");
    }
  } else if (
    record.expected_root_generation !== expectation.expectedRootGeneration ||
    record.expected_source_revision !== expectation.expectedSourceRevision ||
    record.expected_workflow_status_hash !== expectation.expectedWorkflowStatusHash
  ) {
    throw new Error("Forge Studio returned mismatched creation changeset evidence");
  }
  return record;
}

function requireProfileDiff(
  value: unknown,
  changeset: StudioCreationChangeset,
): StudioCreationChangesetDiffResult["diff"] {
  if (
    !isRecord(value) ||
    value.changeset_id !== changeset.changeset_id ||
    value.workspace_id !== changeset.workspace_id ||
    value.expected_source_revision !== changeset.expected_source_revision ||
    value.proposed_source_revision !== changeset.proposed_source_revision ||
    value.review_sha256 !== changeset.review_sha256 ||
    !Array.isArray(value.operations) ||
    value.operations.length !== changeset.operations.length ||
    !value.operations.every((operation, index) =>
      isDiffOperation(operation, changeset.operations[index]),
    )
  ) {
    throw new Error("Forge Studio returned an invalid creation changeset diff");
  }
  return value as unknown as StudioCreationChangesetDiffResult["diff"];
}

function sameImmutableChangesetEvidence(
  left: StudioCreationChangeset,
  right: StudioCreationChangeset,
): boolean {
  return (
    left.changeset_id === right.changeset_id &&
    left.workspace_id === right.workspace_id &&
    left.expected_root_generation === right.expected_root_generation &&
    left.expected_source_revision === right.expected_source_revision &&
    left.proposed_source_revision === right.proposed_source_revision &&
    left.expected_workflow_status_hash === right.expected_workflow_status_hash &&
    left.review_sha256 === right.review_sha256 &&
    left.operations.length === right.operations.length &&
    left.operations.every((operation, index) =>
      sameOperation(operation, right.operations[index]),
    )
  );
}

function sameOperation(
  left: StudioCreationChangeset["operations"][number] | undefined,
  right: StudioCreationChangeset["operations"][number] | undefined,
): boolean {
  return Boolean(
    left &&
    right &&
    left.operation === right.operation &&
    left.path === right.path &&
    left.expected_base_file_sha256 === right.expected_base_file_sha256 &&
    left.expected_base_size === right.expected_base_size &&
    left.proposed_file_sha256 === right.proposed_file_sha256 &&
    left.proposed_size === right.proposed_size,
  );
}

function isReplaceOperation(
  value: unknown,
  profilePath: string,
  expectedBaseFileSha256: string,
): boolean {
  return (
    isRecord(value) &&
    value.operation === "replace" &&
    value.path === profilePath &&
    value.expected_base_file_sha256 === expectedBaseFileSha256 &&
    Number.isSafeInteger(value.expected_base_size) &&
    Number(value.expected_base_size) >= 0 &&
    isSha256(value.proposed_file_sha256) &&
    Number.isSafeInteger(value.proposed_size) &&
    Number(value.proposed_size) >= 0
  );
}

function isDiffOperation(
  value: unknown,
  operation: StudioCreationChangeset["operations"][number] | undefined,
): boolean {
  if (!operation || !isRecord(value)) return false;
  return (
    value.operation === operation.operation &&
    value.path === operation.path &&
    value.expected_base_file_sha256 === operation.expected_base_file_sha256 &&
    value.expected_base_size === operation.expected_base_size &&
    value.proposed_file_sha256 === operation.proposed_file_sha256 &&
    value.proposed_size === operation.proposed_size &&
    typeof value.size_delta === "number" &&
    Number.isSafeInteger(value.size_delta) &&
    value.size_delta === Number(operation.proposed_size) - Number(operation.expected_base_size)
  );
}

function requireRecoveryTerminal(
  outcome: unknown,
  status: StudioCreationChangeset["status"],
  mode: "resume" | "rollback",
): void {
  if (
    (outcome !== "not_needed" && outcome !== "rolled_back" && outcome !== "committed") ||
    (outcome === "committed" && status !== "applied") ||
    (outcome === "rolled_back" && status !== "rejected") ||
    (mode === "resume" && outcome === "rolled_back") ||
    (mode === "rollback" && outcome === "committed") ||
    (outcome === "not_needed" && status !== "applied" && status !== "rejected")
  ) {
    throw new Error("Forge Studio returned a non-terminal creation recovery");
  }
}

function isChangesetStatus(value: unknown): value is StudioCreationChangeset["status"] {
  return (
    value === "staged" ||
    value === "approved" ||
    value === "applying" ||
    value === "applied" ||
    value === "rejected" ||
    value === "recovery_required"
  );
}

function isSha256(value: unknown): value is string {
  return typeof value === "string" && /^[0-9a-f]{64}$/u.test(value);
}

function changesetNavigationKind(
  status: StudioCreationChangeset["status"] | undefined,
): CreationNavigationKind | null {
  if (status === "staged") return "staged";
  if (status === "approved") return "approved";
  if (status === "applying" || status === "recovery_required") return "recovery_required";
  return null;
}

function reportNavigation(
  callback: (state: CreationNavigationState) => void,
  kind: CreationNavigationKind,
): void {
  callback({ blocksNavigation: kind !== "clean", kind });
}

function facetTitle(value: string): string {
  return value
    .replaceAll("_", " ")
    .replace(/\b\p{L}/gu, (character) => character.toUpperCase());
}

function formatDelta(value: number): string {
  return value > 0 ? `+${String(value)}` : String(value);
}

function describeError(error: unknown): string {
  return error instanceof Error ? error.message : "Creation profile operation failed";
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

import { useEffect, useMemo, useRef, useState } from "react";

import type {
  ForgeStudioApi,
  StudioCreationJob,
  StudioCreationOutputGrant,
  StudioCreationWorkspace,
} from "../shared/studio-api";
import {
  findIdenticalPendingMaterializationJob,
  gameMaterializeSubmission,
  gamePackageExtractSubmission,
  gamePackageSubmission,
  hasExactMaterializationOperationParams,
  loadCreationMaterializationPipelineCandidates,
  materializationBundleBuildSubmission,
  type ExtractionMaterializationCandidate,
  type LoadedMaterializationPipelineCandidates,
  type MaterializationBundleCandidate,
  type MaterializationOperation,
  type PackageMaterializationCandidate,
  type RuntimeBundleMaterializationCandidate,
  type StandaloneMaterializationCandidate,
} from "./creation-materialization-pipeline-state";
import {
  creationExecutionAuthorityKey,
  projectCreationJob,
  type CreationExecutionCensus,
  type CreationJobView,
} from "./creation-execution-state";
import { validateCreationOutputGrant } from "./creation-output-grant-state";
import {
  expectCreationEvidenceResult,
  isCreationServiceError,
} from "./creation-service";
import type { CreationNavigationState } from "./creation-state";

const STEP_IDS = ["bundle", "standalone", "package", "extract"] as const;
type StepId = (typeof STEP_IDS)[number];
type SourceCandidate =
  | RuntimeBundleMaterializationCandidate
  | MaterializationBundleCandidate
  | StandaloneMaterializationCandidate
  | PackageMaterializationCandidate;

const CLEAN_NAVIGATION: CreationNavigationState = {
  blocksNavigation: false,
  kind: "clean",
};
const BUFFERED_NAVIGATION: CreationNavigationState = {
  blocksNavigation: true,
  kind: "facet_buffer",
};
const PENDING_NAVIGATION: CreationNavigationState = {
  blocksNavigation: true,
  kind: "request_pending",
};
const RECOVERY_NAVIGATION: CreationNavigationState = {
  blocksNavigation: true,
  kind: "recovery_required",
};

const EMPTY_SELECTIONS: Record<StepId, string | null> = {
  bundle: null,
  standalone: null,
  package: null,
  extract: null,
};

const EMPTY_CANDIDATES: LoadedMaterializationPipelineCandidates = {
  runtimeBundleCandidates: [],
  materializationBundleCandidates: [],
  standaloneCandidates: [],
  packageCandidates: [],
  extractionCandidates: [],
  blockingReasonCodes: [],
  pendingJobs: [],
  boundGrantJobIds: new Map(),
};

interface StepDefinition {
  id: StepId;
  operation: MaterializationOperation;
  legend: string;
  candidateName: string;
  empty: string;
  selectorLabel: string;
  revokeLabel: string;
  submitLabel: string;
  submittingLabel: string;
  targetVersion: 3 | 4 | 5;
  targetKind: StudioCreationOutputGrant["kind"];
}

const STEP_DEFINITIONS: readonly StepDefinition[] = [
  {
    id: "bundle",
    operation: "game.materialization.bundle.build",
    legend: "1. Build materialization bundle",
    candidateName: "Runtime bundle candidate",
    empty: "No exact current runtime bundle candidate has a published source grant.",
    selectorLabel: "Select materialization bundle destination",
    revokeLabel: "Revoke selected materialization bundle destination",
    submitLabel: "Build selected materialization bundle",
    submittingLabel: "Submitting materialization bundle…",
    targetVersion: 3,
    targetKind: "game_materialization_bundle_directory",
  },
  {
    id: "standalone",
    operation: "game.materialize",
    legend: "2. Materialize standalone game",
    candidateName: "Materialization bundle candidate",
    empty: "No exact current materialization bundle candidate has a published source grant.",
    selectorLabel: "Select standalone game destination",
    revokeLabel: "Revoke selected standalone game destination",
    submitLabel: "Materialize selected standalone game",
    submittingLabel: "Submitting standalone materialization…",
    targetVersion: 4,
    targetKind: "standalone_game_directory",
  },
  {
    id: "package",
    operation: "game.package",
    legend: "3. Build game package",
    candidateName: "Standalone game candidate",
    empty: "No exact current standalone game candidate has a published source grant.",
    selectorLabel: "Select game package destination",
    revokeLabel: "Revoke selected game package destination",
    submitLabel: "Build selected game package",
    submittingLabel: "Submitting game package…",
    targetVersion: 5,
    targetKind: "game_package_file",
  },
  {
    id: "extract",
    operation: "game.package.extract",
    legend: "4. Extract game package",
    candidateName: "Game package candidate",
    empty: "No exact current game package candidate has a published source file grant.",
    selectorLabel: "Select game package extraction destination",
    revokeLabel: "Revoke selected game package extraction destination",
    submitLabel: "Extract selected game package",
    submittingLabel: "Submitting package extraction…",
    targetVersion: 4,
    targetKind: "standalone_game_directory",
  },
];

export interface CreationMaterializationPipelineProps {
  api: ForgeStudioApi;
  workspace: StudioCreationWorkspace;
  census: CreationExecutionCensus;
  grants: readonly StudioCreationOutputGrant[];
  executionBusy: boolean;
  observedJob: unknown;
  trackingError?: string | null;
  onNavigationStateChange: (state: CreationNavigationState) => void;
  onGrantChange: (grant: StudioCreationOutputGrant) => void;
  onGrantCensusRefresh?: () => void | Promise<void>;
  onSubmittedJob: (job: CreationJobView) => void | Promise<void>;
  onObservedJob: (job: StudioCreationJob) => void;
}

export function CreationMaterializationPipeline({
  api,
  workspace,
  census,
  grants,
  executionBusy,
  observedJob,
  trackingError = null,
  onNavigationStateChange,
  onGrantChange,
  onGrantCensusRefresh,
  onSubmittedJob,
  onObservedJob,
}: CreationMaterializationPipelineProps) {
  const authorityKey = creationExecutionAuthorityKey(census.authority);
  const [boundAuthorityKey, setBoundAuthorityKey] = useState(authorityKey);
  const [candidates, setCandidates] = useState(EMPTY_CANDIDATES);
  const [candidatePending, setCandidatePending] = useState(
    workspace.project_kind === "game",
  );
  const [candidateSelections, setCandidateSelections] = useState(EMPTY_SELECTIONS);
  const [targetSelections, setTargetSelections] = useState(EMPTY_SELECTIONS);
  const [pendingAction, setPendingAction] = useState<string | null>(null);
  const [status, setStatus] = useState(
    "Materialization pipeline is bound to the current execution authority.",
  );
  const [error, setError] = useState<string | null>(null);
  const requestToken = useRef(0);
  const submissionRef = useRef(false);
  const adoptedJobHashes = useRef(new Set<string>());
  const observedHashRef = useRef<string | null>(null);
  const alertRef = useRef<HTMLParagraphElement | null>(null);
  const statusRef = useRef<HTMLParagraphElement | null>(null);
  const callbacksRef = useRef({
    onNavigationStateChange,
    onGrantChange,
    onGrantCensusRefresh,
    onSubmittedJob,
    onObservedJob,
  });

  useEffect(() => {
    callbacksRef.current = {
      onNavigationStateChange,
      onGrantChange,
      onGrantCensusRefresh,
      onSubmittedJob,
      onObservedJob,
    };
  }, [
    onGrantCensusRefresh,
    onGrantChange,
    onNavigationStateChange,
    onObservedJob,
    onSubmittedJob,
  ]);

  const sourceCandidates = useMemo<Record<StepId, readonly SourceCandidate[]>>(
    () => ({
      bundle: candidates.runtimeBundleCandidates,
      standalone: candidates.materializationBundleCandidates,
      package: candidates.standaloneCandidates,
      extract: candidates.packageCandidates,
    }),
    [candidates],
  );

  const selectedCandidates = useMemo<Record<StepId, SourceCandidate | null>>(
    () => ({
      bundle:
        candidates.runtimeBundleCandidates.find(
          (candidate) => candidate.key === candidateSelections.bundle,
        ) ?? null,
      standalone:
        candidates.materializationBundleCandidates.find(
          (candidate) => candidate.key === candidateSelections.standalone,
        ) ?? null,
      package:
        candidates.standaloneCandidates.find(
          (candidate) => candidate.key === candidateSelections.package,
        ) ?? null,
      extract:
        candidates.packageCandidates.find(
          (candidate) => candidate.key === candidateSelections.extract,
        ) ?? null,
    }),
    [candidateSelections, candidates],
  );

  const targetGrants = useMemo<Record<StepId, StudioCreationOutputGrant[]>>(() => {
    const result = {} as Record<StepId, StudioCreationOutputGrant[]>;
    for (const step of STEP_DEFINITIONS) {
      result[step.id] = grants
        .filter(
          (grant) =>
            grant.workspace_id === census.authority.workspaceId &&
            grant.format_version === step.targetVersion &&
            grant.kind === step.targetKind &&
            grant.state !== "revoked",
        )
        .sort((left, right) => compareUtf8(left.grant_id, right.grant_id));
    }
    return result;
  }, [census.authority.workspaceId, grants]);

  const selectedTargets = useMemo<Record<StepId, StudioCreationOutputGrant | null>>(
    () => {
      const result = {} as Record<StepId, StudioCreationOutputGrant | null>;
      for (const step of STEP_DEFINITIONS) {
        result[step.id] =
          targetGrants[step.id].find(
            (grant) =>
              grant.grant_id === targetSelections[step.id] &&
              grant.state === "ready",
          ) ?? null;
      }
      return result;
    },
    [targetGrants, targetSelections],
  );

  const pendingDurable = candidates.pendingJobs.length > 0;
  const recoveryBlocked =
    candidates.blockingReasonCodes.length > 0 ||
    grants.some((grant) => grant.state === "recovery_required");
  const localSelection = [...STEP_IDS].some(
    (step) => candidateSelections[step] !== null || targetSelections[step] !== null,
  );
  const pipelineBlocked =
    boundAuthorityKey !== authorityKey ||
    candidatePending ||
    candidates.blockingReasonCodes.length > 0 ||
    grants.some(
      (grant) => grant.state === "reserved" || grant.state === "recovery_required",
    ) ||
    pendingDurable ||
    executionBusy ||
    pendingAction !== null;

  useEffect(() => {
    callbacksRef.current.onNavigationStateChange(
      pendingAction !== null || pendingDurable
        ? PENDING_NAVIGATION
        : recoveryBlocked
          ? RECOVERY_NAVIGATION
          : localSelection
            ? BUFFERED_NAVIGATION
            : CLEAN_NAVIGATION,
    );
  }, [localSelection, pendingAction, pendingDurable, recoveryBlocked]);

  useEffect(
    () => () => callbacksRef.current.onNavigationStateChange(CLEAN_NAVIGATION),
    [],
  );

  useEffect(() => {
    requestToken.current += 1;
    adoptedJobHashes.current.clear();
    observedHashRef.current = null;
    queueMicrotask(() => {
      setBoundAuthorityKey(authorityKey);
      setCandidateSelections(EMPTY_SELECTIONS);
      setTargetSelections(EMPTY_SELECTIONS);
      setPendingAction(null);
      setError(null);
      setStatus(
        "Materialization pipeline is bound to the current execution authority.",
      );
    });
  }, [authorityKey]);

  useEffect(() => {
    requestToken.current += 1;
    const token = requestToken.current;
    queueMicrotask(() => {
      if (requestToken.current !== token) return;
      setCandidatePending(workspace.project_kind === "game");
      setCandidates(EMPTY_CANDIDATES);
      setError(null);
      if (workspace.project_kind !== "game") return;
      void loadCreationMaterializationPipelineCandidates(api, census, grants)
        .then(async (loaded) => {
          if (requestToken.current !== token) return;
          setCandidates(loaded);
          setCandidateSelections((current) =>
            retainCandidateSelections(current, loaded),
          );
          setTargetSelections((current) =>
            retainReadyTargetSelections(current, grants),
          );
          if (loaded.pendingJobs.length > 1) {
            setError(
              "Ambiguous durable materialization jobs prevent exact reconstruction.",
            );
            setStatus("Materialization pipeline reconstruction failed closed.");
            return;
          }
          const pending = loaded.pendingJobs[0];
          if (pending && !adoptedJobHashes.current.has(pending.recordHash)) {
            adoptedJobHashes.current.add(pending.recordHash);
            const restored = restorePendingSelections(pending, loaded, grants);
            setCandidateSelections((current) => ({
              ...current,
              [restored.step]: restored.candidateKey,
            }));
            setTargetSelections((current) => ({
              ...current,
              [restored.step]: restored.targetGrantId,
            }));
            callbacksRef.current.onObservedJob(pending.record);
            await callbacksRef.current.onSubmittedJob(pending);
            if (requestToken.current !== token) return;
            setStatus(
              `${operationLabel(pending.operation)} job ${pending.job_id} was reconstructed as ${pending.state}.`,
            );
          } else {
            setStatus(
              loaded.blockingReasonCodes.length > 0
                ? "Materialization pipeline is blocked by durable recovery state."
                : "Current published materialization candidates and destination grants loaded.",
            );
          }
        })
        .catch((caught: unknown) => {
          if (requestToken.current !== token) return;
          setCandidates({
            ...EMPTY_CANDIDATES,
            blockingReasonCodes: ["materialization_candidate_evidence_invalid"],
          });
          setError(describeError(caught));
          setStatus("Materialization candidate evidence failed closed.");
        })
        .finally(() => {
          if (requestToken.current === token) setCandidatePending(false);
        });
    });
    return () => {
      requestToken.current += 1;
    };
  }, [api, authorityKey, census, grants, workspace.project_kind]);

  useEffect(() => {
    if (!error) return;
    queueMicrotask(() => alertRef.current?.focus());
  }, [error]);

  useEffect(() => {
    if (!trackingError) return;
    queueMicrotask(() => {
      setError(trackingError);
      setStatus("Materialization job tracking failed closed.");
    });
  }, [trackingError]);

  useEffect(() => {
    const projected = projectCreationJob(
      observedJob,
      census.authority.workspaceId,
      census.authority,
    );
    if (
      projected === null ||
      !isPipelineOperation(projected.operation) ||
      projected.recordHash === observedHashRef.current
    ) {
      return;
    }
    observedHashRef.current = projected.recordHash;
    if (projected.state === "queued" || projected.state === "running") {
      queueMicrotask(() =>
        setStatus(
          `${operationLabel(projected.operation)} job ${projected.job_id} is ${projected.state}.`,
        ),
      );
      return;
    }
    queueMicrotask(() => {
      if (projected.cleanupPending || projected.recoveryRequired) {
        setCandidates((current) => ({
          ...current,
          blockingReasonCodes: [
            ...new Set([
              ...current.blockingReasonCodes,
              projected.cleanupPending ? "cleanup_pending" : "recovery_required",
            ]),
          ].sort(compareUtf8),
        }));
      }
      setStatus(
        `${operationLabel(projected.operation)} job ${projected.job_id} completed as ${projected.state}. Candidate outputs remain release-blocked.`,
      );
      void callbacksRef.current.onGrantCensusRefresh?.();
      queueMicrotask(() => statusRef.current?.focus());
    });
  }, [census.authority, observedJob]);

  if (workspace.project_kind !== "game") {
    return (
      <section
        className="creation-card creation-materialization-pipeline"
        aria-labelledby="creation-materialization-pipeline-heading"
      >
        <h3 id="creation-materialization-pipeline-heading">Materialization pipeline</h3>
        <p>Game materialization is not applicable to this project kind.</p>
        <p role="status" aria-live="polite">
          No game materialization controls were loaded.
        </p>
      </section>
    );
  }

  async function selectTarget(step: StepDefinition): Promise<void> {
    setPendingAction(`select:${step.id}`);
    setError(null);
    setStatus(`Opening the native ${step.legend.toLocaleLowerCase("en-US")} destination selector.`);
    try {
      const reply = await selectorForStep(api, step)(census.authority.workspaceId);
      const result = await expectCreationEvidenceResult(
        Promise.resolve(reply),
        "creation_output_grant.create",
      );
      const selected = validateCreationOutputGrant(result.grant);
      if (
        selected.workspace_id !== census.authority.workspaceId ||
        selected.format_version !== step.targetVersion ||
        selected.kind !== step.targetKind ||
        selected.state !== "ready" ||
        selected.generation !== 0 ||
        selected.publication !== null
      ) {
        throw new Error("Forge Studio returned a mismatched destination authority");
      }
      callbacksRef.current.onGrantChange(selected);
      setTargetSelections((current) => ({
        ...current,
        [step.id]: selected.grant_id,
      }));
      await callbacksRef.current.onGrantCensusRefresh?.();
      setStatus(`${step.legend} destination authority selected.`);
      queueMicrotask(() => statusRef.current?.focus());
    } catch (caught) {
      if (isCreationServiceError(caught, "cancelled")) {
        setStatus("Destination selection was cancelled; no grant was created.");
      } else {
        setError(describeError(caught));
        setStatus(`${step.legend} destination selection failed closed.`);
      }
    } finally {
      setPendingAction(null);
    }
  }

  async function revokeTarget(step: StepDefinition): Promise<void> {
    const expected = selectedTargets[step.id];
    if (expected === null || expected.state !== "ready") return;
    setPendingAction(`revoke:${step.id}`);
    setError(null);
    setStatus(`Revoking ${step.legend.toLocaleLowerCase("en-US")} destination with generation CAS.`);
    try {
      const result = await expectCreationEvidenceResult(
        api.revokeCreationAssetpackOutput({
          grantId: expected.grant_id,
          expectedGeneration: expected.generation,
        }),
        "creation_output_grant.revoke",
      );
      const revoked = validateCreationOutputGrant(result.grant);
      if (
        revoked.workspace_id !== census.authority.workspaceId ||
        revoked.grant_id !== expected.grant_id ||
        revoked.format_version !== step.targetVersion ||
        revoked.kind !== step.targetKind ||
        revoked.state !== "revoked" ||
        revoked.generation !== expected.generation + 1 ||
        revoked.publication !== null
      ) {
        throw new Error("Forge Studio returned a mismatched revoked destination grant");
      }
      callbacksRef.current.onGrantChange(revoked);
      setTargetSelections((current) => {
        const next = { ...current };
        for (const stepId of STEP_IDS) {
          if (next[stepId] === revoked.grant_id) next[stepId] = null;
        }
        return next;
      });
      await callbacksRef.current.onGrantCensusRefresh?.();
      setStatus(`${step.legend} destination authority was revoked.`);
      queueMicrotask(() => statusRef.current?.focus());
    } catch (caught) {
      setError(describeError(caught));
      setStatus(`${step.legend} destination revocation failed closed.`);
    } finally {
      setPendingAction(null);
    }
  }

  async function submitStep(step: StepDefinition): Promise<void> {
    const candidate = selectedCandidates[step.id];
    const target = selectedTargets[step.id];
    if (
      submissionRef.current ||
      pipelineBlocked ||
      candidate === null ||
      target === null
    ) {
      return;
    }
    const submission = submissionForStep(step.id, census, candidate, target);
    submissionRef.current = true;
    setPendingAction(step.operation);
    setError(null);
    setStatus(`Checking exact ${operationLabel(step.operation).toLocaleLowerCase("en-US")} authority.`);
    let createdJobId: string | null = null;
    let duplicateJobId: string | null = null;
    try {
      const duplicate = await findIdenticalPendingMaterializationJob(
        api,
        census.authority,
        step.operation,
        submission,
      );
      if (duplicate) {
        duplicateJobId = duplicate.job_id;
        callbacksRef.current.onObservedJob(duplicate.record);
        await callbacksRef.current.onSubmittedJob(duplicate);
        setStatus(
          `${operationLabel(step.operation)} job ${duplicate.job_id} is already ${duplicate.state}; no duplicate was submitted.`,
        );
        queueMicrotask(() => statusRef.current?.focus());
        return;
      }
      const result = await expectCreationEvidenceResult(
        createForStep(api, step.id, submission),
        "creation_job.create",
      );
      const created = requireCreatedJob(
        result.job,
        census,
        step.operation,
        submission,
      );
      createdJobId = created.job_id;
      callbacksRef.current.onObservedJob(created.record);
      await callbacksRef.current.onSubmittedJob(created);
      setCandidateSelections((current) => ({ ...current, [step.id]: null }));
      setTargetSelections((current) => ({ ...current, [step.id]: null }));
      await callbacksRef.current.onGrantCensusRefresh?.();
      setStatus(`${operationLabel(step.operation)} job ${created.job_id} was ${created.state}.`);
      queueMicrotask(() => statusRef.current?.focus());
    } catch (caught) {
      if (duplicateJobId !== null) {
        setError(
          `Existing job ${duplicateJobId} was found, but local adoption failed closed: ${describeError(caught)}`,
        );
      } else if (createdJobId !== null) {
        setError(
          `Job ${createdJobId} was submitted, but local tracking failed closed: ${describeError(caught)}`,
        );
      } else {
        setError(describeError(caught));
      }
      setStatus(`${operationLabel(step.operation)} was not safely adopted.`);
    } finally {
      submissionRef.current = false;
      setPendingAction(null);
    }
  }

  return (
    <section
      className="creation-materialization-pipeline"
      aria-labelledby="creation-materialization-pipeline-heading"
      aria-busy={
        boundAuthorityKey !== authorityKey || candidatePending || pendingAction !== null
      }
    >
      <p className="eyebrow">Fixed build and publication boundary</p>
      <h3 id="creation-materialization-pipeline-heading">
        Game materialization, package, and extraction pipeline
      </h3>
      <p>
        Build outputs are immutable candidates. Candidate evidence does not change active
        readiness until reviewed evidence explicitly activates the exact lineage.
      </p>
      <p className="creation-materialization-release-warning">
        Build, materialization, package, and extraction success remains release-blocked until
        reviewed execution, native, and platform evidence is active. No native platform support is
        claimed here.
      </p>
      <p
        ref={statusRef}
        tabIndex={-1}
        role="status"
        aria-live="polite"
        aria-label="Materialization pipeline status"
      >
        {status}
      </p>
      {error ? (
        <p ref={alertRef} tabIndex={-1} role="alert" className="inline-error">
          {error}
        </p>
      ) : null}
      {candidates.blockingReasonCodes.length > 0 ? (
        <div className="creation-blockers" role="alert">
          <strong>Materialization downstream actions blocked</strong>
          <TokenList values={candidates.blockingReasonCodes} />
        </div>
      ) : null}
      {candidatePending ? (
        <p role="status" aria-live="polite">
          Inspecting current materialization lineage and durable grants…
        </p>
      ) : null}

      <div className="creation-materialization-stepper" aria-label="Materialization steps">
        {STEP_DEFINITIONS.map((step) => {
          const selectedCandidate = selectedCandidates[step.id];
          const selectedTarget = selectedTargets[step.id];
          return (
            <fieldset
              key={step.id}
              className="creation-card creation-materialization-step"
              disabled={pipelineBlocked}
            >
              <legend>{step.legend}</legend>
              <p>
                Source candidates and grants are derived from one succeeded, committed producer
                job under the current artifact authority.
              </p>
              {sourceCandidates[step.id].length === 0 ? (
                <p>{step.empty}</p>
              ) : (
                <div className="creation-radio-list">
                  {sourceCandidates[step.id].map((candidate) => (
                    <label key={candidate.key}>
                      <input
                        type="radio"
                        name={`materialization-${step.id}-candidate`}
                        checked={candidateSelections[step.id] === candidate.key}
                        aria-label={`${step.candidateName} — ${candidate.artifactId} — Published generation ${String(candidate.sourceGrantGeneration)}`}
                        onChange={() =>
                          setCandidateSelections((current) => ({
                            ...current,
                            [step.id]: candidate.key,
                          }))
                        }
                      />
                      <strong>{step.candidateName}</strong>
                      <span>Published source</span>
                      <span>generation {candidate.sourceGrantGeneration}</span>
                    </label>
                  ))}
                </div>
              )}

              <section
                className="creation-output-authority"
                aria-labelledby={`materialization-${step.id}-target-heading`}
              >
                <h4 id={`materialization-${step.id}-target-heading`}>
                  Destination authority
                </h4>
                <button type="button" onClick={() => void selectTarget(step)}>
                  {pendingAction === `select:${step.id}`
                    ? "Selecting destination…"
                    : step.selectorLabel}
                </button>
                {targetGrants[step.id].length === 0 ? (
                  <p>No matching destination grant is registered.</p>
                ) : (
                  <div className="creation-radio-list">
                    {targetGrants[step.id].map((grant) => (
                      <label key={grant.grant_id}>
                        <input
                          type="radio"
                          name={`materialization-${step.id}-target`}
                          checked={targetSelections[step.id] === grant.grant_id}
                          disabled={grant.state !== "ready"}
                          aria-label={`${grant.display_name} — ${titleToken(grant.state)} — generation ${String(grant.generation)}`}
                          onChange={() =>
                            setTargetSelections((current) => ({
                              ...current,
                              [step.id]: grant.grant_id,
                            }))
                          }
                        />
                        <strong>{grant.display_name}</strong>
                        <span>{grant.kind}</span>
                        <span>{titleToken(grant.state)}</span>
                        <span>generation {grant.generation}</span>
                      </label>
                    ))}
                  </div>
                )}
                {selectedTarget ? (
                  <button type="button" onClick={() => void revokeTarget(step)}>
                    {pendingAction === `revoke:${step.id}`
                      ? "Revoking destination…"
                      : step.revokeLabel}
                  </button>
                ) : null}
              </section>

              {step.id === "extract" ? (
                <ExtractionEvidence candidates={candidates.extractionCandidates} />
              ) : null}

              <button
                type="button"
                disabled={selectedCandidate === null || selectedTarget === null}
                onClick={() => void submitStep(step)}
              >
                {pendingAction === step.operation ? step.submittingLabel : step.submitLabel}
              </button>
            </fieldset>
          );
        })}
      </div>
    </section>
  );
}

function ExtractionEvidence({
  candidates,
}: {
  candidates: readonly ExtractionMaterializationCandidate[];
}) {
  if (candidates.length === 0) {
    return <p>No current extraction evidence candidate is present.</p>;
  }
  return (
    <section aria-label="Package extraction evidence">
      <h4>Current extraction evidence candidates</h4>
      {candidates.map((candidate) => (
        <div key={candidate.key} className="creation-materialization-extraction-evidence">
          <p>
            <strong>Extraction evidence candidate</strong> <code>{candidate.artifactId}</code>
          </p>
          <p>
            <strong>Preserved standalone identity</strong>{" "}
            <code>{candidate.preservedStandaloneContentHash}</code>
          </p>
          <p>
            <strong>Preserved standalone tree</strong>{" "}
            <code>{candidate.preservedStandaloneTreeHash}</code>
          </p>
        </div>
      ))}
    </section>
  );
}

function selectorForStep(api: ForgeStudioApi, step: StepDefinition) {
  if (step.id === "bundle") return api.selectCreationMaterializationBundleOutput.bind(api);
  if (step.id === "standalone") return api.selectCreationStandaloneGameOutput.bind(api);
  if (step.id === "package") return api.selectCreationGamePackageOutput.bind(api);
  return api.selectCreationGamePackageExtractionOutput.bind(api);
}

function submissionForStep(
  step: StepId,
  census: CreationExecutionCensus,
  candidate: SourceCandidate,
  target: StudioCreationOutputGrant,
) {
  if (step === "bundle") {
    return materializationBundleBuildSubmission(
      census,
      candidate as RuntimeBundleMaterializationCandidate,
      target,
    );
  }
  if (step === "standalone") {
    return gameMaterializeSubmission(
      census,
      candidate as MaterializationBundleCandidate,
      target,
    );
  }
  if (step === "package") {
    return gamePackageSubmission(
      census,
      candidate as StandaloneMaterializationCandidate,
      target,
    );
  }
  return gamePackageExtractSubmission(
    census,
    candidate as PackageMaterializationCandidate,
    target,
  );
}

function createForStep(
  api: ForgeStudioApi,
  step: StepId,
  submission: ReturnType<typeof submissionForStep>,
) {
  if (step === "bundle") {
    return api.buildCreationMaterializationBundle(
      submission as Parameters<ForgeStudioApi["buildCreationMaterializationBundle"]>[0],
    );
  }
  if (step === "standalone") {
    return api.materializeCreationGame(
      submission as Parameters<ForgeStudioApi["materializeCreationGame"]>[0],
    );
  }
  if (step === "package") {
    return api.packageCreationGame(
      submission as Parameters<ForgeStudioApi["packageCreationGame"]>[0],
    );
  }
  return api.extractCreationGamePackage(
    submission as Parameters<ForgeStudioApi["extractCreationGamePackage"]>[0],
  );
}

function restorePendingSelections(
  job: CreationJobView,
  candidates: LoadedMaterializationPipelineCandidates,
  grants: readonly StudioCreationOutputGrant[],
): { step: StepId; candidateKey: string; targetGrantId: string } {
  const step = stepForOperation(job.operation);
  if (step === null) throw new Error("Pending materialization operation is unsupported");
  const params = operationParameters(job);
  const sources: readonly SourceCandidate[] =
    step === "bundle"
      ? candidates.runtimeBundleCandidates
      : step === "standalone"
        ? candidates.materializationBundleCandidates
        : step === "package"
          ? candidates.standaloneCandidates
          : candidates.packageCandidates;
  const artifactParam =
    step === "bundle"
      ? "runtime_bundle_artifact_id"
      : step === "standalone"
        ? "materialization_bundle_artifact_id"
        : step === "package"
          ? "standalone_game_artifact_id"
          : "game_package_artifact_id";
  const expectedParamKeys = [
    artifactParam,
    "source_grant_generation",
    "source_grant_id",
    "target_grant_generation",
    "target_grant_id",
  ].sort(compareUtf8);
  const actualParamKeys = Object.keys(params).sort(compareUtf8);
  if (
    actualParamKeys.length !== expectedParamKeys.length ||
    actualParamKeys.some((key, index) => key !== expectedParamKeys[index])
  ) {
    throw new Error(
      "Pending materialization operation parameters are not closed and exact",
    );
  }
  const sourceMatches = sources.filter(
    (candidate) =>
      candidate.artifactId === params[artifactParam] &&
      candidate.sourceGrantId === params.source_grant_id &&
      candidate.sourceGrantGeneration === params.source_grant_generation,
  );
  const targetMatches = grants.filter((grant) => {
    const expectedGeneration =
      grant.state === "recovery_required" ? grant.generation - 1 : grant.generation;
    return (
      (grant.state === "reserved" || grant.state === "recovery_required") &&
      grant.grant_id === params.target_grant_id &&
      expectedGeneration === params.target_grant_generation &&
      candidates.boundGrantJobIds.get(grant.grant_id) === job.job_id
    );
  });
  if (sourceMatches.length !== 1 || targetMatches.length !== 1) {
    throw new Error("Pending materialization selection is ambiguous");
  }
  return {
    step,
    candidateKey: sourceMatches[0].key,
    targetGrantId: targetMatches[0].grant_id,
  };
}

function retainCandidateSelections(
  current: Record<StepId, string | null>,
  candidates: LoadedMaterializationPipelineCandidates,
): Record<StepId, string | null> {
  const available: Record<StepId, readonly SourceCandidate[]> = {
    bundle: candidates.runtimeBundleCandidates,
    standalone: candidates.materializationBundleCandidates,
    package: candidates.standaloneCandidates,
    extract: candidates.packageCandidates,
  };
  const next = { ...current };
  for (const step of STEP_IDS) {
    if (!available[step].some((candidate) => candidate.key === current[step])) {
      next[step] = null;
    }
  }
  return next;
}

function retainReadyTargetSelections(
  current: Record<StepId, string | null>,
  grants: readonly StudioCreationOutputGrant[],
): Record<StepId, string | null> {
  const next = { ...current };
  for (const step of STEP_IDS) {
    if (
      !grants.some(
        (grant) => grant.grant_id === current[step] && grant.state === "ready",
      )
    ) {
      next[step] = null;
    }
  }
  return next;
}

function requireCreatedJob(
  value: unknown,
  census: CreationExecutionCensus,
  operation: MaterializationOperation,
  submission: ReturnType<typeof submissionForStep>,
): CreationJobView {
  const job = projectCreationJob(
    value,
    census.authority.workspaceId,
    census.authority,
  );
  if (
    job === null ||
    !hasExactMaterializationOperationParams(job, operation, submission)
  ) {
    throw new Error(`Forge Studio returned a mismatched ${operation} job submission`);
  }
  return job;
}

function operationParameters(job: CreationJobView): Record<string, unknown> {
  const record = job.record as unknown as Record<string, unknown>;
  return isRecord(record.operation_params) ? record.operation_params : {};
}

function stepForOperation(operation: CreationJobView["operation"]): StepId | null {
  if (operation === "game.materialization.bundle.build") return "bundle";
  if (operation === "game.materialize") return "standalone";
  if (operation === "game.package") return "package";
  if (operation === "game.package.extract") return "extract";
  return null;
}

function isPipelineOperation(
  operation: CreationJobView["operation"],
): operation is MaterializationOperation {
  return stepForOperation(operation) !== null;
}

function operationLabel(operation: CreationJobView["operation"]): string {
  if (operation === "game.materialization.bundle.build") return "Materialization bundle build";
  if (operation === "game.materialize") return "Standalone materialization";
  if (operation === "game.package") return "Game package build";
  if (operation === "game.package.extract") return "Game package extraction";
  return operation;
}

function TokenList({ values }: { values: readonly string[] }) {
  return <ul>{values.map((value) => <li key={value}><code>{value}</code></li>)}</ul>;
}

function titleToken(value: string): string {
  return value
    .split("_")
    .map((token) => token.charAt(0).toLocaleUpperCase("en-US") + token.slice(1))
    .join(" ");
}

function compareUtf8(left: string, right: string): number {
  const leftBytes = new TextEncoder().encode(left);
  const rightBytes = new TextEncoder().encode(right);
  const limit = Math.min(leftBytes.length, rightBytes.length);
  for (let index = 0; index < limit; index += 1) {
    if (leftBytes[index] !== rightBytes[index]) return leftBytes[index] - rightBytes[index];
  }
  return leftBytes.length - rightBytes.length;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function describeError(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

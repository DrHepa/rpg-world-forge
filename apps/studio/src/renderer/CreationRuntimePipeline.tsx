import { useEffect, useMemo, useRef, useState } from "react";

import type {
  ForgeStudioApi,
  StudioCreationAuthorityCapabilities,
  StudioCreationJob,
  StudioCreationOutputGrant,
  StudioCreationWorkspace,
} from "../shared/studio-api";
import {
  findIdenticalPendingRuntimeJob,
  loadCreationRuntimePipelineCandidates,
  runtimeBundleBuildSubmission,
  runtimeComposeSubmission,
  type LoadedRuntimePipelineCandidates,
  type RuntimeBundleCandidate,
} from "./creation-runtime-pipeline-state";
import {
  creationExecutionAuthorityKey,
  projectCreationJob,
  type CreationExecutionCensus,
  type CreationJobView,
} from "./creation-execution-state";
import { validateCreationOutputGrant } from "./creation-output-grant-state";
import {
  expectCreationEvidenceResult,
  expectCreationAuthorityResult,
  isClosedCreationAuthorityCapabilities,
  isCreationServiceError,
} from "./creation-service";
import type { CreationNavigationState } from "./creation-state";

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
const EMPTY_CANDIDATES: LoadedRuntimePipelineCandidates = {
  composeCandidates: [],
  bundleCandidates: [],
  blockingReasonCodes: [],
  pendingJobs: [],
  boundGrantJobIds: new Map(),
};

export interface CreationRuntimePipelineProps {
  api: ForgeStudioApi;
  workspace: StudioCreationWorkspace;
  census: CreationExecutionCensus;
  authorityCapabilities?: StudioCreationAuthorityCapabilities | null;
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

export function CreationRuntimePipeline({
  api,
  workspace,
  census,
  authorityCapabilities = null,
  grants,
  executionBusy,
  observedJob,
  trackingError = null,
  onNavigationStateChange,
  onGrantChange,
  onGrantCensusRefresh,
  onSubmittedJob,
  onObservedJob,
}: CreationRuntimePipelineProps) {
  const authorityKey = creationExecutionAuthorityKey(census.authority);
  const [boundAuthorityKey, setBoundAuthorityKey] = useState(authorityKey);
  const [candidates, setCandidates] = useState<LoadedRuntimePipelineCandidates>(
    EMPTY_CANDIDATES,
  );
  const [candidatePending, setCandidatePending] = useState(
    workspace.project_kind === "game",
  );
  const [composeKey, setComposeKey] = useState<string | null>(null);
  const [bundleKey, setBundleKey] = useState<string | null>(null);
  const [targetGrantId, setTargetGrantId] = useState<string | null>(null);
  const [headlessRuntimeBundleArtifactId, setHeadlessRuntimeBundleArtifactId] = useState<string | null>(null);
  const [headlessTargetGrantId, setHeadlessTargetGrantId] = useState<string | null>(null);
  const [authorityGrants, setAuthorityGrants] = useState<readonly StudioCreationOutputGrant[]>([]);
  const [pendingAction, setPendingAction] = useState<string | null>(null);
  const [status, setStatus] = useState(
    "Runtime pipeline is bound to the current execution authority.",
  );
  const [error, setError] = useState<string | null>(null);
  const requestToken = useRef(0);
  const submissionRef = useRef(false);
  const alertRef = useRef<HTMLParagraphElement | null>(null);
  const statusRef = useRef<HTMLParagraphElement | null>(null);
  const adoptedJobHashes = useRef(new Set<string>());
  const observedHashRef = useRef<string | null>(null);
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

  const composeCandidate = useMemo(
    () => candidates.composeCandidates.find((item) => item.key === composeKey) ?? null,
    [candidates.composeCandidates, composeKey],
  );
  const bundleCandidate = useMemo(
    () => candidates.bundleCandidates.find((item) => item.key === bundleKey) ?? null,
    [bundleKey, candidates.bundleCandidates],
  );
  const runtimeGrants = useMemo(
    () =>
      grants
        .filter(
          (grant) =>
            grant.format_version === 2 &&
            grant.kind === "game_runtime_bundle_directory",
        )
        .sort((left, right) => compareUtf8(left.grant_id, right.grant_id)),
    [grants],
  );
  const headlessAuthorityAvailable =
    isClosedCreationAuthorityCapabilities(authorityCapabilities) &&
    typeof api.listCreationAuthorityOutputGrants === "function" &&
    authorityCapabilities.runtime_headless_authority &&
    typeof api.selectCreationHeadlessEvidenceOutput === "function" &&
    typeof api.verifyCreationHeadless === "function";
  const headlessCandidates = useMemo(
    () =>
      census.selectableArtifacts
        .filter((artifact) => artifact.subject.format === "world-forge.game_runtime_bundle")
        .sort((left, right) => compareUtf8(left.artifact_id, right.artifact_id)),
    [census.selectableArtifacts],
  );
  const headlessScripts = useMemo(
    () =>
      census.selectableArtifacts
        .filter((artifact) => artifact.subject.format === "world-forge.game_execution_script")
        .sort((left, right) => compareUtf8(left.artifact_id, right.artifact_id)),
    [census.selectableArtifacts],
  );
  const headlessGrants = useMemo(
    () =>
      (headlessAuthorityAvailable ? authorityGrants : grants)
        .filter((grant) => grant.kind === ("headless_evidence_directory" as StudioCreationOutputGrant["kind"]))
        .sort((left, right) => compareUtf8(left.grant_id, right.grant_id)),
    [authorityGrants, grants, headlessAuthorityAvailable],
  );
  const headlessTargetGrant =
    headlessGrants.find((grant) => grant.grant_id === headlessTargetGrantId && grant.state === "ready") ?? null;
  const headlessCandidate =
    headlessCandidates.find((artifact) => artifact.artifact_id === headlessRuntimeBundleArtifactId) ?? null;
  const targetGrant =
    runtimeGrants.find(
      (grant) => grant.grant_id === targetGrantId && grant.state === "ready",
    ) ?? null;
  const durableGrantBlocked = runtimeGrants.some(
    (grant) => grant.state === "reserved" || grant.state === "recovery_required",
  );
  const localSelection =
    composeKey !== null || bundleKey !== null || targetGrantId !== null;
  const hasRecoveryBlock =
    candidates.blockingReasonCodes.length > 0 || durableGrantBlocked;
  const downstreamBlocked =
    boundAuthorityKey !== authorityKey ||
    candidatePending ||
    candidates.blockingReasonCodes.length > 0 ||
    durableGrantBlocked ||
    executionBusy ||
    pendingAction !== null;

  useEffect(() => {
    callbacksRef.current.onNavigationStateChange(
      pendingAction !== null
        ? PENDING_NAVIGATION
        : hasRecoveryBlock
          ? RECOVERY_NAVIGATION
          : localSelection
          ? BUFFERED_NAVIGATION
          : CLEAN_NAVIGATION,
    );
  }, [hasRecoveryBlock, localSelection, pendingAction]);

  useEffect(
    () => () => {
      callbacksRef.current.onNavigationStateChange(CLEAN_NAVIGATION);
    },
    [],
  );

  useEffect(() => {
    requestToken.current += 1;
    adoptedJobHashes.current.clear();
    observedHashRef.current = null;
    queueMicrotask(() => {
      setBoundAuthorityKey(authorityKey);
      setComposeKey(null);
      setBundleKey(null);
      setTargetGrantId(null);
      setHeadlessRuntimeBundleArtifactId(null);
      setHeadlessTargetGrantId(null);
      setPendingAction(null);
      setError(null);
      setAuthorityGrants([]);
      setStatus("Runtime pipeline is bound to the current execution authority.");
    });
  }, [authorityKey]);

  useEffect(() => {
    if (!headlessAuthorityAvailable) {
      queueMicrotask(() => setAuthorityGrants([]));
      return;
    }
    let cancelled = false;
    void loadAuthorityHeadlessGrants(api, census)
      .then((loaded) => {
        if (!cancelled) setAuthorityGrants(loaded);
      })
      .catch((caught: unknown) => {
        if (!cancelled) {
          setAuthorityGrants([]);
          setError(describeError(caught));
          setStatus("Runtime headless grant authority listing failed closed.");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [api, census, headlessAuthorityAvailable]);

  useEffect(() => {
    requestToken.current += 1;
    const token = requestToken.current;
    queueMicrotask(() => {
      if (requestToken.current !== token) return;
      setCandidatePending(workspace.project_kind === "game");
      setCandidates(EMPTY_CANDIDATES);
      setError(null);
      if (workspace.project_kind !== "game") return;
      void loadCreationRuntimePipelineCandidates(api, census, grants)
        .then(async (loaded) => {
          if (requestToken.current !== token) return;
          setCandidates(loaded);
          setComposeKey((current) =>
            loaded.composeCandidates.some((item) => item.key === current) ? current : null,
          );
          setBundleKey((current) =>
            loaded.bundleCandidates.some((item) => item.key === current) ? current : null,
          );
          setTargetGrantId((current) => {
            const retained = runtimeGrants.find(
              (grant) => grant.grant_id === current && grant.state === "ready",
            );
            if (retained) return retained.grant_id;
            const ready = runtimeGrants.filter((grant) => grant.state === "ready");
            return ready.length === 1 ? ready[0].grant_id : null;
          });
          setHeadlessTargetGrantId((current) => {
            const retained = grants.find(
              (grant) =>
                grant.grant_id === current &&
                grant.kind === ("headless_evidence_directory" as StudioCreationOutputGrant["kind"]) &&
                grant.state === "ready",
            );
            if (retained) return retained.grant_id;
            const ready = grants.filter(
              (grant) =>
                grant.kind === ("headless_evidence_directory" as StudioCreationOutputGrant["kind"]) &&
                grant.state === "ready",
            );
            return ready.length === 1 ? ready[0].grant_id : null;
          });
          if (loaded.pendingJobs.length > 1) {
            setCandidates({
              ...loaded,
              composeCandidates: [],
              bundleCandidates: [],
              blockingReasonCodes: [...new Set([
                ...loaded.blockingReasonCodes,
                "ambiguous_pending_runtime_jobs",
              ])].sort(compareUtf8),
            });
            setError("Ambiguous durable runtime jobs prevent exact reconstruction.");
            setStatus("Runtime pipeline pending job reconstruction failed closed.");
            return;
          }
          const pending = loaded.pendingJobs[0];
          if (pending && !adoptedJobHashes.current.has(pending.recordHash)) {
            adoptedJobHashes.current.add(pending.recordHash);
            callbacksRef.current.onObservedJob(pending.record);
            await callbacksRef.current.onSubmittedJob(pending);
            if (requestToken.current !== token) return;
            restorePendingSelections(pending, loaded, runtimeGrants, {
              compose: setComposeKey,
              bundle: setBundleKey,
              grant: setTargetGrantId,
            });
            setStatus(
              `${operationLabel(pending.operation)} job ${pending.job_id} was reconstructed as ${pending.state}.`,
            );
          } else {
            setStatus(
              loaded.blockingReasonCodes.length > 0
                ? "Runtime pipeline is blocked by durable recovery state."
                : "Current sealed runtime candidates and output grants loaded.",
            );
          }
        })
        .catch((caught: unknown) => {
          if (requestToken.current !== token) return;
          setCandidates({
            ...EMPTY_CANDIDATES,
            blockingReasonCodes: ["runtime_candidate_evidence_invalid"],
          });
          setError(describeError(caught));
          setStatus("Runtime pipeline candidate evidence failed closed.");
        })
        .finally(() => {
          if (requestToken.current === token) setCandidatePending(false);
        });
    });
    return () => {
      requestToken.current += 1;
    };
  }, [api, authorityKey, census, grants, runtimeGrants, workspace.project_kind]);

  useEffect(() => {
    if (!error) return;
    queueMicrotask(() => alertRef.current?.focus());
  }, [error]);

  useEffect(() => {
    if (!trackingError) return;
    let current = true;
    queueMicrotask(() => {
      if (!current) return;
      setError(trackingError);
      setStatus("Runtime pipeline job tracking failed closed.");
    });
    return () => {
      current = false;
    };
  }, [trackingError]);

  useEffect(() => {
    const projected = projectCreationJob(
      observedJob,
      census.authority.workspaceId,
      census.authority,
    );
    if (
      projected === null ||
      observedHashRef.current === projected.recordHash ||
      (projected.operation !== "runtime.compose" &&
        projected.operation !== "runtime.bundle.build")
    ) {
      return;
    }
    observedHashRef.current = projected.recordHash;
    let current = true;
    queueMicrotask(() => {
      if (!current) return;
      if (projected.state === "queued" || projected.state === "running") {
        setStatus(
          `${operationLabel(projected.operation)} job ${projected.job_id} is ${projected.state}.`,
        );
        return;
      }
      if (projected.cleanupPending || projected.recoveryRequired) {
        setCandidates((loaded) => ({
          ...loaded,
          blockingReasonCodes: [...new Set([
            ...loaded.blockingReasonCodes,
            projected.cleanupPending ? "cleanup_pending" : "recovery_required",
          ])].sort(compareUtf8),
        }));
      }
      setStatus(
        `${operationLabel(projected.operation)} job ${projected.job_id} completed as ${projected.state}.`,
      );
      queueMicrotask(() => statusRef.current?.focus());
    });
    return () => {
      current = false;
    };
  }, [census.authority, observedJob]);

  if (workspace.project_kind !== "game") {
    return (
      <section className="creation-card creation-runtime-pipeline" aria-labelledby="runtime-pipeline-heading">
        <h3 id="runtime-pipeline-heading">Runtime pipeline</h3>
        <p>Executable runtime composition is not applicable to this project kind.</p>
        <p role="status" aria-live="polite">No runtime execution controls were loaded.</p>
      </section>
    );
  }

  async function submitCompose(): Promise<void> {
    if (submissionRef.current || downstreamBlocked || composeCandidate === null) return;
    const submission = runtimeComposeSubmission(census, composeCandidate);
    await submitRuntimeJob(
      "runtime.compose",
      submission,
      () => api.composeCreationRuntime(submission),
    );
  }

  async function submitBundle(): Promise<void> {
    if (
      submissionRef.current ||
      downstreamBlocked ||
      bundleCandidate === null ||
      targetGrant === null ||
      !bundleCandidate.bundleAllowed
    ) return;
    const submission = runtimeBundleBuildSubmission(census, bundleCandidate, targetGrant);
    await submitRuntimeJob(
      "runtime.bundle.build",
      submission,
      () => api.buildCreationRuntimeBundle(submission),
    );
  }

  async function submitRuntimeJob(
    operation: "runtime.compose" | "runtime.bundle.build",
    submission: Parameters<typeof findIdenticalPendingRuntimeJob>[3],
    create: () => ReturnType<ForgeStudioApi["composeCreationRuntime"]>,
  ): Promise<void> {
    let createdJobId: string | null = null;
    let duplicateJobId: string | null = null;
    submissionRef.current = true;
    setPendingAction(operation);
    setError(null);
    setStatus(`Checking exact ${operationLabel(operation).toLocaleLowerCase("en-US")} authority.`);
    try {
      const duplicate = await findIdenticalPendingRuntimeJob(
        api,
        census.authority,
        operation,
        submission,
      );
      if (duplicate) {
        duplicateJobId = duplicate.job_id;
        callbacksRef.current.onObservedJob(duplicate.record);
        await callbacksRef.current.onSubmittedJob(duplicate);
        setStatus(
          `${operationLabel(operation)} ${duplicate.job_id} is already ${duplicate.state}; no duplicate was submitted.`,
        );
        queueMicrotask(() => statusRef.current?.focus());
        return;
      }
      const result = await expectCreationEvidenceResult(
        create(),
        "creation_job.create",
      );
      const created = requireCreatedJob(result.job, census, operation);
      createdJobId = created.job_id;
      callbacksRef.current.onObservedJob(created.record);
      await callbacksRef.current.onSubmittedJob(created);
      if (operation === "runtime.compose") setComposeKey(null);
      else {
        setBundleKey(null);
        setTargetGrantId(null);
      }
      await callbacksRef.current.onGrantCensusRefresh?.();
      setStatus(`${operationLabel(operation)} job ${created.job_id} was ${created.state}.`);
      queueMicrotask(() => statusRef.current?.focus());
    } catch (caught) {
      if (duplicateJobId !== null) {
        setError(
          `Existing job ${duplicateJobId} was found, but local adoption failed closed: ${describeError(caught)}`,
        );
      } else if (createdJobId === null) {
        setError(describeError(caught));
      } else {
        setError(
          `Job ${createdJobId} was submitted, but local tracking failed closed: ${describeError(caught)}`,
        );
      }
      setStatus(`${operationLabel(operation)} was not safely adopted.`);
    } finally {
      submissionRef.current = false;
      setPendingAction(null);
    }
  }

  async function selectTarget(): Promise<void> {
    setPendingAction("select-target");
    setError(null);
    setStatus("Opening the native runtime bundle destination selector.");
    try {
      const result = await expectCreationEvidenceResult(
        api.selectCreationRuntimeBundleOutput(census.authority.workspaceId),
        "creation_output_grant.create",
      );
      const selected = validateCreationOutputGrant(result.grant);
      if (
        selected.workspace_id !== census.authority.workspaceId ||
        selected.format_version !== 2 ||
        selected.kind !== "game_runtime_bundle_directory" ||
        selected.state !== "ready" ||
        selected.generation !== 0 ||
        selected.publication !== null
      ) {
        throw new Error("Forge Studio returned an invalid runtime bundle destination grant");
      }
      callbacksRef.current.onGrantChange(selected);
      setTargetGrantId(selected.grant_id);
      await callbacksRef.current.onGrantCensusRefresh?.();
      setStatus("Runtime bundle destination authority selected.");
      queueMicrotask(() => statusRef.current?.focus());
    } catch (caught) {
      if (isCreationServiceError(caught, "cancelled")) {
        setStatus("Destination selection was cancelled; no grant was created.");
      } else {
        setError(describeError(caught));
        setStatus("Runtime bundle destination selection failed.");
      }
    } finally {
      setPendingAction(null);
    }
  }

  async function revokeTarget(): Promise<void> {
    if (targetGrant === null || targetGrant.state !== "ready") return;
    const expected = targetGrant;
    setPendingAction("revoke-target");
    setError(null);
    setStatus("Revoking ready runtime bundle destination with generation CAS.");
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
        revoked.format_version !== 2 ||
        revoked.kind !== "game_runtime_bundle_directory" ||
        revoked.state !== "revoked" ||
        revoked.generation !== expected.generation + 1 ||
        revoked.publication !== null
      ) {
        throw new Error("Forge Studio returned a mismatched revoked runtime bundle grant");
      }
      callbacksRef.current.onGrantChange(revoked);
      setTargetGrantId(null);
      await callbacksRef.current.onGrantCensusRefresh?.();
      setStatus("Runtime bundle destination authority was revoked.");
      queueMicrotask(() => statusRef.current?.focus());
    } catch (caught) {
      setError(describeError(caught));
      setStatus("Runtime bundle destination revocation failed closed.");
    } finally {
      setPendingAction(null);
    }
  }

  async function selectHeadlessTarget(): Promise<void> {
    if (!headlessAuthorityAvailable) return;
    setPendingAction("select-headless-target");
    setError(null);
    setStatus("Opening the native headless evidence destination selector.");
    try {
      const result = await expectCreationAuthorityResult(
        api.selectCreationHeadlessEvidenceOutput(census.authority.workspaceId),
        "creation_output_grant.create",
      );
      const grant = result.grant as StudioCreationOutputGrant;
      if (
        grant.workspace_id !== census.authority.workspaceId ||
        grant.kind !== ("headless_evidence_directory" as StudioCreationOutputGrant["kind"]) ||
        grant.state !== "ready"
      ) {
        throw new Error("Forge Studio returned an invalid headless evidence destination grant");
      }
      callbacksRef.current.onGrantChange(grant);
      setHeadlessTargetGrantId(grant.grant_id);
      await callbacksRef.current.onGrantCensusRefresh?.();
      setStatus("Headless evidence destination authority selected.");
    } catch (caught) {
      if (isCreationServiceError(caught, "cancelled")) {
        setStatus("Headless evidence destination selection was cancelled; no grant was created.");
      } else {
        setError(describeError(caught));
        setStatus("Headless evidence destination selection failed.");
      }
    } finally {
      setPendingAction(null);
    }
  }

  async function submitHeadlessVerification(): Promise<void> {
    if (
      !headlessAuthorityAvailable ||
      submissionRef.current ||
      downstreamBlocked ||
      headlessCandidate === null ||
      headlessScripts.length !== 1 ||
      headlessTargetGrant === null
    ) return;
    submissionRef.current = true;
    setPendingAction("runtime.headless.verify");
    setError(null);
    setStatus("Submitting headless-only verification authority.");
    try {
      const result = await expectCreationAuthorityResult(
        api.verifyCreationHeadless({
          workspaceId: census.authority.workspaceId,
          runtimeBundleArtifactId: headlessCandidate.artifact_id,
          sourceGrantId: runtimeGrants.find((grant) => grant.state === "published")?.grant_id ?? "",
          headlessScriptArtifactId: headlessScripts[0].artifact_id,
          targetGrantId: headlessTargetGrant.grant_id,
          platformId: "platform:linux_x86_64",
        }),
        "creation_job.create",
      );
      const created = requireCreatedJob(result.job, census, "runtime.headless.verify");
      callbacksRef.current.onObservedJob(created.record);
      await callbacksRef.current.onSubmittedJob(created);
      setStatus("Headless verified; native unavailable; release remains blocked.");
    } catch (caught) {
      setError(describeError(caught));
      setStatus("Headless verification authority was not safely adopted.");
    } finally {
      submissionRef.current = false;
      setPendingAction(null);
    }
  }

  return (
    <section
      className="creation-runtime-pipeline"
      aria-labelledby="runtime-pipeline-heading"
      aria-busy={boundAuthorityKey !== authorityKey || candidatePending || pendingAction !== null}
    >
      <p className="eyebrow">Fixed deterministic execution</p>
      <h3 id="runtime-pipeline-heading">Runtime composition and bundle pipeline</h3>
      <p>
        Every ID, adapter binding, source grant, and generation is derived from the exact current
        artifact census. Candidate evidence does not change active readiness until a reviewed phase
        references the immutable outputs.
      </p>
      <p>
        Pre-execution candidate only. This does not claim native verification or release readiness.
      </p>
      <p
        ref={statusRef}
        tabIndex={-1}
        role="status"
        aria-live="polite"
        aria-label="Runtime pipeline status"
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
          <strong>Runtime downstream actions blocked</strong>
          <TokenList values={candidates.blockingReasonCodes} />
        </div>
      ) : null}

      <div className="creation-runtime-pipeline-grid">
        {headlessAuthorityAvailable ? (
          <fieldset className="creation-card" disabled={downstreamBlocked} aria-describedby="runtime-headless-guidance">
            <legend>Verify headless authority</legend>
            <p id="runtime-headless-guidance">
              Headless evidence is independent from native support. A successful result is displayed only as headless verified, native unavailable, and release blocked.
            </p>
            {headlessCandidates.length === 0 ? (
              <p>No retained runtime bundle candidate is available for headless verification.</p>
            ) : (
              <div className="creation-radio-list">
                {headlessCandidates.map((candidate) => (
                  <label key={candidate.artifact_id}>
                    <input
                      type="radio"
                      name="runtime-headless-candidate"
                      checked={headlessRuntimeBundleArtifactId === candidate.artifact_id}
                      aria-label={`${candidate.subject.id} — headless candidate`}
                      onChange={() => setHeadlessRuntimeBundleArtifactId(candidate.artifact_id)}
                    />
                    <strong>{candidate.subject.id}</strong>
                    <span>Headless candidate</span>
                  </label>
                ))}
              </div>
            )}
            <button type="button" onClick={() => void selectHeadlessTarget()}>
              {pendingAction === "select-headless-target" ? "Selecting headless destination…" : "Select headless evidence destination"}
            </button>
            <button
              type="button"
              disabled={headlessCandidate === null || headlessScripts.length !== 1 || headlessTargetGrant === null}
              onClick={() => void submitHeadlessVerification()}
            >
              {pendingAction === "runtime.headless.verify" ? "Submitting headless verification…" : "Verify selected headless candidate"}
            </button>
          </fieldset>
        ) : null}

        <fieldset
          className="creation-card"
          disabled={downstreamBlocked}
          aria-describedby="runtime-compose-guidance"
        >
          <legend>Compose verified runtime</legend>
          <p id="runtime-compose-guidance">
            Only a current sealed asset pack from one succeeded, committed release job is offered.
          </p>
          {candidatePending ? <p role="status">Inspecting sealed runtime lineage…</p> : null}
          {!candidatePending && candidates.composeCandidates.length === 0 ? (
            <p>
              No current <code>world-forge.assetpack</code> candidate has exact sealed publication,
              gamepack, and asset inventory authority.
            </p>
          ) : (
            <div className="creation-radio-list">
              {candidates.composeCandidates.map((candidate) => (
                <label key={candidate.key}>
                  <input
                    type="radio"
                    name="runtime-compose-candidate"
                    checked={composeKey === candidate.key}
                    aria-label={`${artifactLabel(census, candidate.assetpackArtifactId)} — Published asset pack generation ${String(candidate.sourceGrantGeneration)}`}
                    onChange={() => setComposeKey(candidate.key)}
                  />
                  <strong>{artifactLabel(census, candidate.assetpackArtifactId)}</strong>
                  <span>Published</span>
                  <span>generation {candidate.sourceGrantGeneration}</span>
                </label>
              ))}
            </div>
          )}
          <button
            type="button"
            disabled={composeCandidate === null}
            onClick={() => void submitCompose()}
          >
            {pendingAction === "runtime.compose"
              ? "Submitting runtime composition…"
              : "Compose selected runtime"}
          </button>
        </fieldset>

        <fieldset className="creation-card" disabled={downstreamBlocked}>
          <legend>Build runtime bundle</legend>
          <p>
            The registry, snapshot, composition, and support report must be the exact four outputs
            of one succeeded, committed composition job.
          </p>
          {!candidatePending && candidates.bundleCandidates.length === 0 ? (
            <p>
              No exact current four-output runtime group is available. Missing format:
              <code>world-forge.runtime_support_report</code> or another required runtime format.
            </p>
          ) : (
            <div className="creation-radio-list">
              {candidates.bundleCandidates.map((candidate) => (
                <div key={candidate.key} className="creation-runtime-candidate">
                  <label>
                    <input
                      type="radio"
                      name="runtime-bundle-candidate"
                      checked={bundleKey === candidate.key}
                      aria-label={`${artifactLabel(census, candidate.runtimeCompositionArtifactId)} — ${titleToken(candidate.compatibilityStatus)}`}
                      onChange={() => setBundleKey(candidate.key)}
                    />
                    <strong>{artifactLabel(census, candidate.runtimeCompositionArtifactId)}</strong>
                    <span>{titleToken(candidate.compatibilityStatus)}</span>
                  </label>
                  <CandidateReasonSummary candidate={candidate} />
                </div>
              ))}
            </div>
          )}
          {bundleCandidate ? <SupportSummary candidate={bundleCandidate} /> : null}

          <section className="creation-output-authority" aria-labelledby="runtime-target-heading">
            <h4 id="runtime-target-heading">Runtime bundle destination authority</h4>
            <button type="button" onClick={() => void selectTarget()}>
              {pendingAction === "select-target"
                ? "Selecting destination…"
                : "Select runtime bundle destination"}
            </button>
            {runtimeGrants.length === 0 ? (
              <p>No runtime bundle destination grant is registered.</p>
            ) : (
              <div className="creation-radio-list">
                {runtimeGrants.map((grant) => (
                  <label key={grant.grant_id}>
                    <input
                      type="radio"
                      name="runtime-target-grant"
                      checked={targetGrantId === grant.grant_id}
                      disabled={grant.state !== "ready"}
                      aria-label={`${grant.display_name} — ${titleToken(grant.state)} — generation ${String(grant.generation)}`}
                      onChange={() => setTargetGrantId(grant.grant_id)}
                    />
                    <strong>{grant.display_name}</strong>
                    <span>{grant.kind}</span>
                    <span>{titleToken(grant.state)}</span>
                    <span>generation {grant.generation}</span>
                  </label>
                ))}
              </div>
            )}
            {targetGrant ? (
              <button type="button" onClick={() => void revokeTarget()}>
                {pendingAction === "revoke-target"
                  ? "Revoking destination…"
                  : "Revoke selected runtime bundle destination"}
              </button>
            ) : null}
          </section>
          <button
            type="button"
            disabled={
              bundleCandidate === null ||
              !bundleCandidate.bundleAllowed ||
              targetGrant === null
            }
            onClick={() => void submitBundle()}
          >
            {pendingAction === "runtime.bundle.build"
              ? "Submitting runtime bundle…"
              : "Build selected runtime bundle"}
          </button>
        </fieldset>
      </div>
    </section>
  );
}

function SupportSummary({ candidate }: { candidate: RuntimeBundleCandidate }) {
  return (
    <section className="creation-runtime-support" aria-label="Selected runtime support">
      <h4>Pre-execution support</h4>
      <dl className="creation-facts">
        <div><dt>Compatibility</dt><dd>{titleToken(candidate.compatibilityStatus)}</dd></div>
        <div><dt>Bundle handoff</dt><dd>{candidate.bundleAllowed ? "Allowed" : "Blocked"}</dd></div>
      </dl>
      <IdentifierGroup
        title="Pre-execution reason codes"
        values={candidate.supportReasonCodes}
        empty="No support reason codes are present."
      />
      <IdentifierGroup
        title="Optional capability gaps"
        values={candidate.optionalReasonCodes}
        empty="No optional capability gaps are present."
      />
      <IdentifierGroup
        title="Missing required capabilities"
        values={candidate.missingCapabilities}
        empty="No required capabilities are missing."
      />
      <IdentifierGroup
        title="Bundle blocker reason codes"
        values={candidate.blockingReasonCodes}
        empty="No required capability blocker prevents pre-execution bundle construction."
      />
    </section>
  );
}

function CandidateReasonSummary({ candidate }: { candidate: RuntimeBundleCandidate }) {
  const values = [...new Set([
    ...candidate.optionalReasonCodes,
    ...candidate.missingCapabilities,
    ...candidate.blockingReasonCodes,
  ])].sort(compareUtf8);
  if (values.length === 0) return <p>No required or optional capability gaps are present.</p>;
  return (
    <div className="creation-runtime-candidate-reasons">
      <strong>Capability reason codes and gaps</strong>
      <TokenList values={values} />
    </div>
  );
}

function IdentifierGroup({
  title,
  values,
  empty,
}: {
  title: string;
  values: readonly string[];
  empty: string;
}) {
  return (
    <div>
      <strong>{title}</strong>
      {values.length === 0 ? <p>{empty}</p> : <TokenList values={values} />}
    </div>
  );
}

function TokenList({ values }: { values: readonly string[] }) {
  return <ul>{values.map((value) => <li key={value}><code>{value}</code></li>)}</ul>;
}

function restorePendingSelections(
  job: CreationJobView,
  candidates: LoadedRuntimePipelineCandidates,
  grants: readonly StudioCreationOutputGrant[],
  setters: {
    compose: (value: string | null) => void;
    bundle: (value: string | null) => void;
    grant: (value: string | null) => void;
  },
): void {
  const record = job.record as unknown as Record<string, unknown>;
  const params = isRecord(record.operation_params) ? record.operation_params : {};
  if (job.operation === "runtime.compose") {
    const match = candidates.composeCandidates.filter(
      (candidate) =>
        candidate.gamepackArtifactId === params.gamepack_artifact_id &&
        candidate.assetInventoryArtifactId === params.asset_inventory_artifact_id &&
        candidate.assetpackArtifactId === params.assetpack_artifact_id &&
        candidate.sourceGrantId === params.target_grant_id &&
        candidate.sourceGrantGeneration === params.target_grant_generation,
    );
    if (match.length !== 1) throw new Error("Pending runtime composition selection is ambiguous");
    setters.compose(match[0].key);
    return;
  }
  const match = candidates.bundleCandidates.filter(
    (candidate) =>
      candidate.gamepackArtifactId === params.gamepack_artifact_id &&
      candidate.assetInventoryArtifactId === params.asset_inventory_artifact_id &&
      candidate.assetpackArtifactId === params.assetpack_artifact_id &&
      candidate.runtimeSnapshotArtifactId === params.runtime_snapshot_artifact_id &&
      candidate.runtimeAdapterRegistryArtifactId === params.runtime_adapter_registry_artifact_id &&
      candidate.runtimeCompositionArtifactId === params.runtime_composition_artifact_id &&
      candidate.runtimeSupportReportArtifactId === params.runtime_support_report_artifact_id &&
      candidate.sourceGrantId === params.source_grant_id &&
      candidate.sourceGrantGeneration === params.source_grant_generation,
  );
  const grant = grants.filter(
    (item) =>
      item.grant_id === params.target_grant_id &&
      item.format_version === 2 &&
      item.kind === "game_runtime_bundle_directory" &&
      (item.state === "reserved" || item.state === "recovery_required"),
  );
  if (match.length !== 1 || grant.length !== 1) {
    throw new Error("Pending runtime bundle selection is ambiguous");
  }
  setters.bundle(match[0].key);
  setters.grant(grant[0].grant_id);
}

function requireCreatedJob(
  value: unknown,
  census: CreationExecutionCensus,
  operation: "runtime.compose" | "runtime.bundle.build" | "runtime.headless.verify",
): CreationJobView {
  const job = projectCreationJob(value, census.authority.workspaceId, census.authority);
  if (job === null || job.operation !== operation) {
    throw new Error(`Forge Studio returned a mismatched ${operation} job submission`);
  }
  return job;
}

function artifactLabel(census: CreationExecutionCensus, artifactId: string): string {
  return census.selectableById.get(artifactId)?.subject.id ?? artifactId;
}

function operationLabel(operation: CreationJobView["operation"]): string {
  if (operation === "runtime.compose") return "Runtime composition";
  if (operation === "runtime.bundle.build") return "Runtime bundle build";
  return operation;
}

function titleToken(value: string): string {
  return value
    .split("_")
    .map((token) => token.charAt(0).toLocaleUpperCase("en-US") + token.slice(1))
    .join(" ");
}

async function loadAuthorityHeadlessGrants(
  api: ForgeStudioApi,
  census: CreationExecutionCensus,
): Promise<StudioCreationOutputGrant[]> {
  const retained: StudioCreationOutputGrant[] = [];
  const seen = new Set<string>();
  let cursor: string | null = null;
  let previous: string | null = null;
  for (let page = 0; page < 64; page += 1) {
    const result = await expectCreationAuthorityResult(
      api.listCreationAuthorityOutputGrants!({
        workspaceId: census.authority.workspaceId,
        expectedRootGeneration: census.authority.rootGeneration,
        expectedSourceRevision: census.authority.sourceRevision,
        expectedWorkflowStatusHash: census.authority.workflowStatusHash,
        expectedArtifactSnapshotHash: census.authority.artifactSnapshotHash,
        cursor,
        limit: 8,
      }),
      "creation_output_grant.list",
    );
    const grants = Array.isArray(result.grants) ? result.grants : null;
    if (grants === null || grants.length > 8) {
      throw new Error("Forge Studio authority grants failed closed");
    }
    for (const value of grants) {
      const grant = validateCreationOutputGrant(value);
      if (
        grant.workspace_id !== census.authority.workspaceId ||
        (previous !== null && compareUtf8(grant.grant_id, previous) <= 0) ||
        seen.has(grant.grant_id)
      ) {
        throw new Error("Forge Studio authority grants failed closed");
      }
      seen.add(grant.grant_id);
      previous = grant.grant_id;
      if (grant.format_version === 6) retained.push(grant);
    }
    const nextCursor = result.next_cursor;
    if (nextCursor === null) return retained;
    if (typeof nextCursor !== "string" || nextCursor === cursor || nextCursor !== previous) {
      throw new Error("Forge Studio authority grants failed closed");
    }
    cursor = nextCursor;
  }
  throw new Error("Forge Studio authority grants exceeded pagination limit");
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

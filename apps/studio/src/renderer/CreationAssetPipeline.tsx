import { useEffect, useMemo, useRef, useState } from "react";

import type {
  ForgeStudioApi,
  StudioCreationAuthorityCapabilities,
  StudioCreationArtifact,
  StudioCreationJob,
  StudioCreationOutputGrant,
  StudioCreationWorkspace,
} from "../shared/studio-api";
import {
  canonicalAssetOutputIds,
  canonicalAssetReleaseManifestId,
  canonicalizeArtifactDependencies,
  assertAssetOutputIdsAvailable,
  findIdenticalPendingAssetJob,
  loadAssetPipelineBlockingState,
  loadAssetPipelineCandidates,
  MAX_ASSET_ACCEPTANCE_RESULTS,
  normalizeAcceptanceResults,
  parseCreationAdmissionDocument,
  type AssetAcceptanceDraft,
  type AssetPipelineCandidates,
  type AssetProcessingGroup,
  type AssetReleaseGroup,
} from "./creation-asset-pipeline-state";
import { CreationAssetPreview } from "./CreationAssetPreview";
import {
  loadCreationPreviewCatalog,
  type CreationPreviewCatalog,
} from "./creation-preview-state";
import {
  creationExecutionAuthorityKey,
  projectCreationJob,
  type CreationExecutionCensus,
  type CreationJobView,
} from "./creation-execution-state";
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
const EMPTY_ACCEPTANCE: AssetAcceptanceDraft = {
  criterionSha256: "",
  status: "passed",
  evidenceHashes: "",
};
const UTC_TIMESTAMP =
  /^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z$/u;

export interface CreationAssetPipelineProps {
  api: ForgeStudioApi;
  workspace: StudioCreationWorkspace;
  census: CreationExecutionCensus;
  authorityCapabilities?: StudioCreationAuthorityCapabilities | null;
  executionBusy: boolean;
  observedJob: unknown;
  trackingError?: string | null;
  grants?: readonly StudioCreationOutputGrant[];
  grant: StudioCreationOutputGrant | null;
  onNavigationStateChange: (state: CreationNavigationState) => void;
  onGrantChange: (grant: StudioCreationOutputGrant | null) => void;
  onGrantSelectionChange?: (grantId: string | null) => void;
  onGrantCensusRefresh?: () => void | Promise<void>;
  onSubmittedJob: (job: CreationJobView) => void | Promise<void>;
  onObservedJob: (job: StudioCreationJob) => void;
}

export function CreationAssetPipeline({
  api,
  workspace,
  census,
  authorityCapabilities = null,
  executionBusy,
  observedJob,
  trackingError = null,
  grants,
  grant,
  onNavigationStateChange,
  onGrantChange,
  onGrantSelectionChange,
  onGrantCensusRefresh,
  onSubmittedJob,
  onObservedJob,
}: CreationAssetPipelineProps) {
  const authorityKey = creationExecutionAuthorityKey(census.authority);
  const [boundAuthorityKey, setBoundAuthorityKey] = useState(authorityKey);
  const [admissionJson, setAdmissionJson] = useState("");
  const [dependencyIds, setDependencyIds] = useState<Set<string>>(new Set());
  const [candidates, setCandidates] = useState<AssetPipelineCandidates>({
    processingGroups: [],
    releaseGroups: [],
    qaReviewGroups: [],
  });
  const [qaReviewKey, setQaReviewKey] = useState<string | null>(null);
  const [authorityReviewReceiptIds, setAuthorityReviewReceiptIds] = useState<string[]>([]);
  const [candidatePending, setCandidatePending] = useState(
    workspace.project_kind === "game",
  );
  const [blockingReasons, setBlockingReasons] = useState<string[]>([]);
  const [processingKey, setProcessingKey] = useState<string | null>(null);
  const [releaseKey, setReleaseKey] = useState<string | null>(null);
  const [outputSuffix, setOutputSuffix] = useState("");
  const [acceptanceRows, setAcceptanceRows] = useState<AssetAcceptanceDraft[]>([
    { ...EMPTY_ACCEPTANCE },
  ]);
  const [pendingAction, setPendingAction] = useState<string | null>(null);
  const [status, setStatus] = useState("Asset pipeline is bound to the current authority.");
  const [error, setError] = useState<string | null>(null);
  const [previewCatalog, setPreviewCatalog] = useState<CreationPreviewCatalog>({
    authorityKey,
    items: [],
  });
  const [previewCatalogKey, setPreviewCatalogKey] = useState("");
  const [previewPending, setPreviewPending] = useState(
    workspace.project_kind === "game",
  );
  const [previewError, setPreviewError] = useState<string | null>(null);
  const requestToken = useRef(0);
  const previewRequestToken = useRef(0);
  const submissionRef = useRef(false);
  const alertRef = useRef<HTMLParagraphElement | null>(null);
  const resultRef = useRef<HTMLParagraphElement | null>(null);
  const observedHashRef = useRef<string | null>(null);
  const observedJobRef = useRef(observedJob);

  const processingGroup = useMemo(
    () => candidates.processingGroups.find((group) => group.key === processingKey) ?? null,
    [candidates.processingGroups, processingKey],
  );
  const releaseGroup = useMemo(
    () => candidates.releaseGroups.find((group) => group.key === releaseKey) ?? null,
    [candidates.releaseGroups, releaseKey],
  );
  const qaReviewGroup = useMemo(
    () => candidates.qaReviewGroups.find((group) => group.key === qaReviewKey) ?? null,
    [candidates.qaReviewGroups, qaReviewKey],
  );
  const authorityReviewAvailable =
    isClosedCreationAuthorityCapabilities(authorityCapabilities) &&
    authorityCapabilities.asset_authority_reviews &&
    typeof api.reviewCreationAssetQa === "function";
  const authorityReleaseAvailable =
    isClosedCreationAuthorityCapabilities(authorityCapabilities) &&
    authorityCapabilities.asset_release_authority &&
    typeof api.authorizeCreationAssetRelease === "function";
  const outputGrants = useMemo(
    () => grants ?? (grant === null ? [] : [grant]),
    [grant, grants],
  );
  const previewDependencyKey = useMemo(
    () => creationPreviewDependencyKey(authorityKey, outputGrants),
    [authorityKey, outputGrants],
  );
  const previewCatalogIsCurrent =
    previewCatalogKey === previewDependencyKey && previewCatalog.authorityKey === authorityKey;
  const localDirty =
    admissionJson.length > 0 ||
    dependencyIds.size > 0 ||
    processingKey !== null ||
    releaseKey !== null ||
    outputSuffix.length > 0 ||
    acceptanceRows.length !== 1 ||
    acceptanceRows.some(
      (row) =>
        row.criterionSha256.length > 0 ||
        row.evidenceHashes.length > 0 ||
        row.status !== "passed",
    );
  const downstreamBlocked =
    boundAuthorityKey !== authorityKey ||
    candidatePending ||
    blockingReasons.length > 0 ||
    executionBusy ||
    pendingAction !== null ||
    outputGrants.some((item) => item.state === "recovery_required");

  useEffect(() => {
    onNavigationStateChange(
      pendingAction !== null
        ? PENDING_NAVIGATION
        : localDirty
          ? BUFFERED_NAVIGATION
          : CLEAN_NAVIGATION,
    );
  }, [localDirty, onNavigationStateChange, pendingAction]);

  useEffect(() => {
    observedJobRef.current = observedJob;
  }, [observedJob]);

  useEffect(() => {
    requestToken.current += 1;
    const token = requestToken.current;
    observedHashRef.current = null;
    queueMicrotask(() => {
      if (requestToken.current !== token) return;
      setAdmissionJson("");
      setDependencyIds(new Set());
      setCandidates({ processingGroups: [], releaseGroups: [], qaReviewGroups: [] });
      setCandidatePending(workspace.project_kind === "game");
      setBlockingReasons([]);
      setProcessingKey(null);
      setReleaseKey(null);
      setOutputSuffix("");
      setAcceptanceRows([{ ...EMPTY_ACCEPTANCE }]);
      setAuthorityReviewReceiptIds([]);
      setPendingAction(null);
      setError(null);
      setStatus("Asset pipeline is bound to the current authority.");
      setBoundAuthorityKey(authorityKey);
      if (workspace.project_kind !== "game") return;
      void Promise.all([
        loadAssetPipelineCandidates(api, census),
        loadAssetPipelineBlockingState(api, census.authority),
      ])
        .then(([nextCandidates, blockers]) => {
          if (requestToken.current !== token) return;
          setCandidates(nextCandidates);
          setBlockingReasons(blockers.reasonCodes);
          const currentJob = projectCreationJob(
            observedJobRef.current,
            census.authority.workspaceId,
            census.authority,
          );
          if (!isAssetPipelineJob(currentJob)) {
            setStatus(
              blockers.reasonCodes.length > 0
                ? "Asset pipeline downstream actions are blocked by durable recovery state."
                : "Current asset pipeline candidates loaded for the current authority.",
            );
          }
        })
        .catch((caught: unknown) => {
          if (requestToken.current !== token) return;
          setCandidates({ processingGroups: [], releaseGroups: [], qaReviewGroups: [] });
          setBlockingReasons(["candidate_evidence_invalid"]);
          setError(describeError(caught));
          setStatus("Asset pipeline candidate evidence failed closed.");
        })
        .finally(() => {
          if (requestToken.current === token) setCandidatePending(false);
        });
    });
    return () => {
      requestToken.current += 1;
    };
  }, [api, authorityKey, census, workspace.project_kind]);

  useEffect(() => {
    previewRequestToken.current += 1;
    const token = previewRequestToken.current;
    queueMicrotask(() => {
      if (previewRequestToken.current !== token) return;
      setPreviewCatalogKey(previewDependencyKey);
      setPreviewCatalog({ authorityKey, items: [] });
      setPreviewPending(workspace.project_kind === "game");
      setPreviewError(null);
      if (workspace.project_kind !== "game") return;
      void loadCreationPreviewCatalog(api, census, outputGrants)
        .then((catalog) => {
          if (previewRequestToken.current !== token) return;
          setPreviewCatalog(catalog);
        })
        .catch((caught: unknown) => {
          if (previewRequestToken.current !== token) return;
          setPreviewCatalog({ authorityKey, items: [] });
          setPreviewError(describeError(caught));
        })
        .finally(() => {
          if (previewRequestToken.current === token) setPreviewPending(false);
        });
    });
    return () => {
      previewRequestToken.current += 1;
    };
  }, [api, authorityKey, census, outputGrants, previewDependencyKey, workspace.project_kind]);

  useEffect(() => {
    if (!error) return;
    queueMicrotask(() => alertRef.current?.focus());
  }, [error]);

  useEffect(() => {
    if (!trackingError) return;
    const token = requestToken.current;
    queueMicrotask(() => {
      if (requestToken.current !== token) return;
      setError(trackingError);
      setStatus("Asset pipeline job tracking failed closed.");
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
      observedHashRef.current === projected.recordHash ||
      (projected.operation !== "artifact.admit" &&
        projected.operation !== "asset.process" &&
        projected.operation !== "asset.release.seal")
    ) {
      return;
    }
    observedHashRef.current = projected.recordHash;
    const token = requestToken.current;
    queueMicrotask(() => {
      if (requestToken.current !== token) return;
      if (projected.state === "queued" || projected.state === "running") {
        setStatus(`${operationLabel(projected.operation)} job ${projected.job_id} is ${projected.state}.`);
        return;
      }
      if (projected.cleanupPending || projected.recoveryRequired) {
        setBlockingReasons((current) =>
          [...new Set([...current, projected.cleanupPending ? "cleanup_pending" : "recovery_required"])].sort(),
        );
      }
      if (
        projected.operation === "asset.process" &&
        projected.state === "succeeded" &&
        projected.analysisStatus === "failed"
      ) {
        const reasonCodes = projected.record.result?.reason_codes ?? [];
        setStatus(
          `Asset processing execution succeeded, but controlled processing analysis failed${
            reasonCodes.length > 0 ? `: ${reasonCodes.join(", ")}` : "."
          }`,
        );
      } else {
        setStatus(`${operationLabel(projected.operation)} job ${projected.job_id} completed as ${projected.state}.`);
      }
      queueMicrotask(() => resultRef.current?.focus());
      if (projected.operation === "asset.release.seal" && grant !== null) {
        void refreshCreationAssetpackGrant(api, grant, census.authority.workspaceId)
          .then((next) => {
            if (requestToken.current === token) onGrantChange(next);
          })
          .catch((caught: unknown) => {
            if (requestToken.current === token) setError(describeError(caught));
          });
      }
    });
  }, [api, census.authority, grant, observedJob, onGrantChange]);

  if (workspace.project_kind !== "game") {
    return (
      <section className="creation-card creation-asset-pipeline" aria-labelledby="asset-pipeline-heading">
        <h3 id="asset-pipeline-heading">Asset production pipeline</h3>
        <p>Executable asset production is not applicable to this project kind.</p>
        <p role="status" aria-live="polite">No asset execution controls were loaded.</p>
      </section>
    );
  }

  function clearError(): void {
    setError(null);
  }

  function toggleDependency(artifactId: string): void {
    setDependencyIds((current) => {
      const next = new Set(current);
      if (next.has(artifactId)) next.delete(artifactId);
      else next.add(artifactId);
      return next;
    });
  }

  async function submitAdmission(): Promise<void> {
    if (submissionRef.current || executionBusy) return;
    let createdJobId: string | null = null;
    submissionRef.current = true;
    setPendingAction("admission");
    clearError();
    setStatus("Validating canonical admission input.");
    try {
      const document = parseCreationAdmissionDocument(admissionJson);
      const dependencyArtifactIds = canonicalizeArtifactDependencies([...dependencyIds], census);
      const result = await expectCreationEvidenceResult(
        api.admitCreationArtifact({
          ...authorityParams(census),
          document,
          dependencyArtifactIds,
        }),
        "creation_job.create",
      );
      const created = requireCreatedJob(result.job, census, "artifact.admit");
      createdJobId = created.job_id;
      setAdmissionJson("");
      setDependencyIds(new Set());
      setStatus(`Artifact admission job ${created.job_id} was ${created.state}.`);
      await onSubmittedJob(created);
      queueMicrotask(() => resultRef.current?.focus());
    } catch (caught) {
      if (createdJobId === null) {
        setError(describeError(caught));
        setStatus("Artifact admission was not submitted.");
      } else {
        setError(
          `Job ${createdJobId} was submitted, but local tracking failed closed: ${describeError(caught)}`,
        );
        setStatus(`Artifact admission job ${createdJobId} was submitted; tracking failed closed.`);
      }
    } finally {
      submissionRef.current = false;
      setPendingAction(null);
    }
  }

  async function submitProcessing(): Promise<void> {
    if (submissionRef.current || downstreamBlocked || processingGroup === null) return;
    let createdJobId: string | null = null;
    let duplicateJobId: string | null = null;
    submissionRef.current = true;
    setPendingAction("processing");
    clearError();
    setStatus("Checking exact asset processing authority and duplicate activity.");
    try {
      const currentGroup = requireProcessingGroup(candidates, processingGroup.key);
      const ids = canonicalAssetOutputIds(currentGroup.assetId, outputSuffix);
      assertAssetOutputIdsAvailable(ids, census);
      const acceptanceResults = normalizeAcceptanceResults(acceptanceRows);
      const rawParams = {
        license_artifact_ids: currentGroup.licenseArtifactIds,
        recipe_id: ids.recipeId,
        processing_receipt_id: ids.processingReceiptId,
        qa_report_id: ids.qaReportId,
        acceptance_results: acceptanceResults.map((row) => ({
          criterion_index: row.criterionIndex,
          criterion_sha256: row.criterionSha256,
          status: row.status,
          evidence_hashes: row.evidenceHashes,
        })),
      };
      const duplicate = await findIdenticalPendingAssetJob(
        api,
        census.authority,
        "asset.process",
        rawParams,
      );
      if (duplicate) {
        duplicateJobId = duplicate.job_id;
        onObservedJob(duplicate.record);
        await onSubmittedJob(duplicate);
        setStatus(`Asset processing ${duplicate.job_id} is already ${duplicate.state}; no duplicate was submitted.`);
        queueMicrotask(() => resultRef.current?.focus());
        return;
      }
      const result = await expectCreationEvidenceResult(
        api.processCreationAsset({
          ...authorityParams(census),
          licenseArtifactIds: currentGroup.licenseArtifactIds,
          ...ids,
          acceptanceResults,
        }),
        "creation_job.create",
      );
      const created = requireCreatedJob(result.job, census, "asset.process");
      createdJobId = created.job_id;
      setProcessingKey(null);
      setOutputSuffix("");
      setAcceptanceRows([{ ...EMPTY_ACCEPTANCE }]);
      setStatus(`Asset processing job ${created.job_id} was ${created.state}.`);
      await onSubmittedJob(created);
      queueMicrotask(() => resultRef.current?.focus());
    } catch (caught) {
      if (duplicateJobId !== null) {
        setError(
          `Existing job ${duplicateJobId} was found, but local adoption failed closed: ${describeError(caught)}`,
        );
        setStatus(`Existing asset processing job ${duplicateJobId} could not be adopted locally.`);
      } else if (createdJobId === null) {
        setError(describeError(caught));
        setStatus("Asset processing was not submitted.");
      } else {
        setError(
          `Job ${createdJobId} was submitted, but local tracking failed closed: ${describeError(caught)}`,
        );
        setStatus(`Asset processing job ${createdJobId} was submitted; tracking failed closed.`);
      }
    } finally {
      submissionRef.current = false;
      setPendingAction(null);
    }
  }

  async function selectOutput(): Promise<void> {
    setPendingAction("select-output");
    clearError();
    setStatus("Opening the native asset pack output selector.");
    try {
      const result = await expectCreationEvidenceResult(
        api.selectCreationAssetpackOutput(census.authority.workspaceId),
        "creation_output_grant.create",
      );
      const selected = validateCreatedAssetpackGrant(
        result.grant,
        census.authority.workspaceId,
      );
      onGrantChange(selected);
      onGrantSelectionChange?.(selected.grant_id);
      await onGrantCensusRefresh?.();
      setStatus("Asset pack output authority selected.");
      queueMicrotask(() => resultRef.current?.focus());
    } catch (caught) {
      if (isCreationServiceError(caught, "cancelled")) {
        setStatus("Output selection was cancelled; no grant was created.");
      } else {
        setError(describeError(caught));
        setStatus("Asset pack output selection failed.");
      }
    } finally {
      setPendingAction(null);
    }
  }

  async function revokeOutput(): Promise<void> {
    if (grant?.state !== "ready") return;
    const expected = grant;
    setPendingAction("revoke-output");
    clearError();
    setStatus("Revoking ready output authority with generation CAS.");
    try {
      const result = await expectCreationEvidenceResult(
        api.revokeCreationAssetpackOutput({
          grantId: expected.grant_id,
          expectedGeneration: expected.generation,
        }),
        "creation_output_grant.revoke",
      );
      const revoked = validateRevokedAssetpackGrant(
        result.grant,
        census.authority.workspaceId,
        expected,
      );
      onGrantChange(revoked);
      onGrantSelectionChange?.(null);
      await onGrantCensusRefresh?.();
      setStatus("Asset pack output authority was revoked.");
      queueMicrotask(() => resultRef.current?.focus());
    } catch (caught) {
      setError(describeError(caught));
      setStatus("Asset pack output revocation failed closed.");
    } finally {
      setPendingAction(null);
    }
  }

  async function submitRelease(): Promise<void> {
    if (
      submissionRef.current ||
      downstreamBlocked ||
      releaseGroup === null ||
      grant?.state !== "ready"
    ) return;
    let createdJobId: string | null = null;
    let duplicateJobId: string | null = null;
    let trackingComplete = false;
    submissionRef.current = true;
    setPendingAction("seal-release");
    clearError();
    setStatus("Revalidating exact QA inventory and output grant authority.");
    try {
      const currentGroup = requireReleaseGroup(candidates, releaseGroup.key);
      const qaArtifacts = currentGroup.qaReportArtifactIds.map((artifactId) => {
        const artifact = census.selectableById.get(artifactId);
        if (!artifact || artifact.subject.format !== "world-forge.asset_qa_report") {
          throw new Error("Asset release QA selection changed before submission");
        }
        return artifact;
      });
      const manifestId = canonicalAssetReleaseManifestId(workspace.project.id, qaArtifacts);
      const pendingParams = {
        qa_report_artifact_ids: currentGroup.qaReportArtifactIds,
        manifest_id: manifestId,
        target_grant_id: grant.grant_id,
        target_grant_generation: nextGrantGeneration(grant.generation),
      };
      const duplicate = await findIdenticalPendingAssetJob(
        api,
        census.authority,
        "asset.release.seal",
        pendingParams,
      );
      if (duplicate) {
        duplicateJobId = duplicate.job_id;
        onObservedJob(duplicate.record);
        await onSubmittedJob(duplicate);
        trackingComplete = true;
        await onGrantCensusRefresh?.();
        setStatus(`Asset release ${duplicate.job_id} is already ${duplicate.state}; no duplicate was submitted.`);
        queueMicrotask(() => resultRef.current?.focus());
        return;
      }
      const currentGrant = await refreshCreationAssetpackGrant(
        api,
        grant,
        census.authority.workspaceId,
      );
      onGrantChange(currentGrant);
      if (
        currentGrant.state !== "ready" ||
        currentGrant.generation !== grant.generation ||
        currentGrant.grant_id !== grant.grant_id
      ) {
        throw new Error("Asset pack output authority changed before release submission");
      }
      const result = await expectCreationEvidenceResult(
        api.sealCreationAssetRelease({
          ...authorityParams(census),
          qaReportArtifactIds: currentGroup.qaReportArtifactIds,
          manifestId,
          targetGrantId: currentGrant.grant_id,
          expectedTargetGrantGeneration: currentGrant.generation,
        }),
        "creation_job.create",
      );
      const created = requireCreatedJob(result.job, census, "asset.release.seal");
      createdJobId = created.job_id;
      await onSubmittedJob(created);
      trackingComplete = true;
      onGrantChange(
        await refreshCreationAssetpackGrant(
          api,
          currentGrant,
          census.authority.workspaceId,
        ),
      );
      await onGrantCensusRefresh?.();
      setReleaseKey(null);
      setStatus(`Asset release seal job ${created.job_id} was ${created.state}.`);
      queueMicrotask(() => resultRef.current?.focus());
    } catch (caught) {
      if (duplicateJobId !== null) {
        setError(
          trackingComplete
            ? `Existing job ${duplicateJobId} was adopted, but output reconciliation failed closed: ${describeError(caught)}`
            : `Existing job ${duplicateJobId} was found, but local adoption failed closed: ${describeError(caught)}`,
        );
        setStatus(
          trackingComplete
            ? `Existing asset release job ${duplicateJobId} is tracked; output reconciliation failed closed.`
            : `Existing asset release job ${duplicateJobId} could not be adopted locally.`,
        );
      } else if (createdJobId === null) {
        setError(describeError(caught));
        setStatus("Asset release seal was not submitted.");
      } else if (trackingComplete) {
        setError(
          `Job ${createdJobId} was submitted and tracked, but output reconciliation failed closed: ${describeError(caught)}`,
        );
        setStatus(
          `Asset release seal job ${createdJobId} is tracked; output reconciliation failed closed.`,
        );
      } else {
        setError(
          `Job ${createdJobId} was submitted, but local tracking failed closed: ${describeError(caught)}`,
        );
        setStatus(`Asset release seal job ${createdJobId} was submitted; tracking failed closed.`);
      }
    } finally {
      submissionRef.current = false;
      setPendingAction(null);
    }
  }

  async function submitAuthorityReview(): Promise<void> {
    if (
      submissionRef.current ||
      downstreamBlocked ||
      qaReviewGroup === null ||
      !authorityReviewAvailable
    ) return;
    submissionRef.current = true;
    setPendingAction("asset-qa-review");
    clearError();
    setStatus("Opening exact-byte main-owned QA review authority.");
    try {
      const result = await expectCreationAuthorityResult(
        api.reviewCreationAssetQa({
          workspaceId: census.authority.workspaceId,
          qaReportArtifactId: qaReviewGroup.qaReportArtifactId,
          outputRole: qaReviewGroup.outputRole,
        }),
        "creation_job.create",
      );
      const created = requireCreatedJob(result.job, census, "asset.qa.review");
      const outcome = created.authorityOutcome?.kind === "asset_qa_review" ? created.authorityOutcome : null;
      if (!outcome || outcome.status !== "approved" || outcome.reviewReceiptArtifactIds.length < 1) {
        throw new Error("Forge Studio returned a non-approved QA authority review result");
      }
      setAuthorityReviewReceiptIds(outcome.reviewReceiptArtifactIds);
      onObservedJob(created.record);
      await onSubmittedJob(created);
      setQaReviewKey(null);
      setStatus(`Asset QA authority job ${created.job_id} was ${created.state}.`);
      queueMicrotask(() => resultRef.current?.focus());
    } catch (caught) {
      setError(describeError(caught));
      setStatus("Asset QA authority review was not safely adopted.");
    } finally {
      submissionRef.current = false;
      setPendingAction(null);
    }
  }

  async function submitReleaseAuthorization(): Promise<void> {
    if (
      submissionRef.current ||
      downstreamBlocked ||
      !authorityReleaseAvailable ||
      grant?.state !== "ready" ||
      authorityReviewReceiptIds.length < 1
    ) return;
    submissionRef.current = true;
    setPendingAction("asset-release-authorize");
    clearError();
    setStatus("Submitting release authorization from retained QA review receipts.");
    try {
      const result = await expectCreationAuthorityResult(
        api.authorizeCreationAssetRelease({
          workspaceId: census.authority.workspaceId,
          reviewReceiptArtifactIds: authorityReviewReceiptIds,
          targetGrantId: grant.grant_id,
        }),
        "creation_job.create",
      );
      const created = requireCreatedJob(result.job, census, "asset.release.authorize");
      if (created.authorityOutcome?.kind !== "asset_release_authority") {
        throw new Error("Forge Studio returned a mismatched release authority job");
      }
      onObservedJob(created.record);
      await onSubmittedJob(created);
      setStatus(
        created.authorityOutcome.status === "authorized"
          ? `Asset release authorization job ${created.job_id} was authorized.`
          : `Asset release authorization job ${created.job_id} was blocked.`,
      );
      queueMicrotask(() => resultRef.current?.focus());
    } catch (caught) {
      setError(describeError(caught));
      setStatus("Asset release authorization was not safely adopted.");
    } finally {
      submissionRef.current = false;
      setPendingAction(null);
    }
  }

  function updateAcceptance(index: number, patch: Partial<AssetAcceptanceDraft>): void {
    setAcceptanceRows((current) =>
      current.map((row, rowIndex) => (rowIndex === index ? { ...row, ...patch } : row)),
    );
  }

  return (
    <section
      className="creation-asset-pipeline"
      aria-labelledby="asset-pipeline-heading"
      aria-busy={boundAuthorityKey !== authorityKey || candidatePending || pendingAction !== null}
    >
      <p className="eyebrow">Fixed provider-free operations</p>
      <h3 id="asset-pipeline-heading">Asset production pipeline</h3>
      <p>
        Every operation is bound to source <code>{census.authority.sourceRevision}</code> and artifact
        snapshot <code>{census.authority.artifactSnapshotHash}</code>. Candidate artifacts do not
        change active readiness until reviewed phase evidence references their exact identities.
      </p>
      <p
        ref={resultRef}
        tabIndex={-1}
        role="status"
        aria-live="polite"
        aria-label="Asset pipeline status"
      >
        {status}
      </p>
      {error ? (
        <p ref={alertRef} tabIndex={-1} role="alert" className="inline-error">
          {error}
        </p>
      ) : null}
      {blockingReasons.length > 0 ? (
        <div className="creation-blockers" role="alert">
          <strong>Downstream actions blocked</strong>
          <ul>{blockingReasons.map((reason) => <li key={reason}><code>{reason}</code></li>)}</ul>
        </div>
      ) : null}

      <div className="creation-asset-pipeline-grid">
        {authorityReviewAvailable ? (
          <fieldset className="creation-card" disabled={downstreamBlocked} aria-describedby="asset-authority-guidance">
            <legend>Review QA candidate authority</legend>
            <p id="asset-authority-guidance">
              The renderer selects only the current QA report identity and a fixed output role. Exact-byte approval remains in the main-owned modal.
            </p>
            <section className="creation-candidate-preview-lane" role="region" aria-label="QA candidate preview metadata">
              <h4>QA candidate preview metadata</h4><p>Preview contract v2</p>
              <p><code>qa_review_candidate</code> · Preview contract v2 · {qaReviewGroup?.qaReportArtifactId ?? "no candidate selected"}</p>
            {candidates.qaReviewGroups.length === 0 ? (
              <p>No QA candidate authority is pending review.</p>
            ) : (
              <div className="creation-radio-list">
                {candidates.qaReviewGroups.map((candidate) => (
                  <label key={candidate.key}>
                    <input
                      type="radio"
                      name="asset-qa-authority-candidate"
                      checked={qaReviewKey === candidate.key}
                      disabled={candidate.status === "blocked"}
                      aria-label={`${candidate.assetId} — ${titleToken(candidate.status)} QA authority`}
                      onChange={() => setQaReviewKey(candidate.key)}
                    />
                    <strong>{candidate.assetId}</strong>
                    <span>{titleToken(candidate.status)}</span>
                    <span>{candidate.blockerCount} blockers</span>
                    <code>{candidate.qaReportArtifactId}</code>
                  </label>
                ))}
              </div>
            )}
            <button
              type="button"
              disabled={qaReviewGroup === null || qaReviewGroup.status === "blocked"}
              onClick={() => void submitAuthorityReview()}
            >
              {pendingAction === "asset-qa-review" ? "Opening QA review…" : "Review selected QA candidate"}
            </button>
            </section>
            {authorityReleaseAvailable ? (
              <button
                type="button"
                disabled={authorityReviewReceiptIds.length < 1 || grant?.state !== "ready"}
                aria-describedby="asset-release-authority-reason"
                onClick={() => void submitReleaseAuthorization()}
              >
                {pendingAction === "asset-release-authorize"
                  ? "Authorizing release…"
                  : "Authorize reviewed asset release"}
              </button>
            ) : null}
            <p id="asset-release-authority-reason">
              Release authorization uses retained v10 review receipt artifact IDs and the selected ready output grant only.
            </p>
            {authorityReleaseAvailable ? (
              <section className="creation-output-grant" aria-labelledby="authority-output-grant-heading">
                <h4 id="authority-output-grant-heading">v11 asset pack target</h4>
                <button type="button" disabled={pendingAction !== null} onClick={() => void selectOutput()}>
                  {pendingAction === "select-output" ? "Opening output selector…" : "Select asset pack output"}
                </button>
                <p>{grant ? `${grant.display_name} — ${titleToken(grant.state)}` : "No asset pack output authority is registered."}</p>
              </section>
            ) : null}
          </fieldset>
        ) : null}

        <fieldset
          className="creation-card"
          disabled={
            boundAuthorityKey !== authorityKey ||
            candidatePending ||
            pendingAction !== null ||
            executionBusy
          }
        >
          <legend>Admit canonical artifact</legend>
          <label htmlFor="creation-artifact-admission-json">Canonical artifact JSON</label>
          <textarea
            id="creation-artifact-admission-json"
            rows={10}
            value={admissionJson}
            onChange={(event) => setAdmissionJson(event.target.value)}
          />
          <p>Strict JSON object only. Duplicate keys, non-finite numbers, overflow, and oversized input are rejected.</p>
          <fieldset className="creation-nested-fieldset">
            <legend>Verified current dependencies</legend>
            {census.selectableArtifacts.length === 0 ? (
              <p>No active or candidate dependencies are available.</p>
            ) : (
              <div className="creation-check-list">
                {[...census.selectableArtifacts]
                  .sort((left, right) => left.artifact_id.localeCompare(right.artifact_id, "en"))
                  .map((artifact) => (
                    <label key={artifact.artifact_id}>
                      <input
                        type="checkbox"
                        checked={dependencyIds.has(artifact.artifact_id)}
                        aria-label={`${artifact.subject.id} — ${lifecycleLabel(artifact.lifecycle)}`}
                        onChange={() => toggleDependency(artifact.artifact_id)}
                      />
                      <span>{artifact.subject.id}</span>
                      <span className={`creation-state-token state-${artifact.lifecycle}`}>
                        {lifecycleLabel(artifact.lifecycle)}
                      </span>
                    </label>
                  ))}
              </div>
            )}
          </fieldset>
          <button type="button" disabled={admissionJson.trim().length === 0} onClick={() => void submitAdmission()}>
            {pendingAction === "admission" ? "Submitting admission…" : "Admit artifact"}
          </button>
        </fieldset>

        <fieldset className="creation-card" disabled={downstreamBlocked}>
          <legend>Process licensed asset</legend>
          <p>Only exact active/candidate license closures with one gamepack, target, style, inventory, and production lineage are offered.</p>
          {candidatePending ? <p role="status">Inspecting licensed asset lineage…</p> : null}
          {!candidatePending && candidates.processingGroups.length === 0 ? (
            <p>No complete licensed asset candidates are available.</p>
          ) : (
            <div className="creation-radio-list">
              {candidates.processingGroups.map((group) => (
                <label key={group.key}>
                  <input
                    type="radio"
                    name="creation-processing-group"
                    checked={processingKey === group.key}
                    aria-label={`${group.assetId} — ${lifecycleLabel(group.lifecycle)} — ${String(group.licenseArtifactIds.length)} license${group.licenseArtifactIds.length === 1 ? "" : "s"}`}
                    onChange={() => setProcessingKey(group.key)}
                  />
                  <strong>{group.assetId}</strong>
                  <span className={`creation-state-token state-${group.lifecycle}`}>
                    {lifecycleLabel(group.lifecycle)}
                  </span>
                  <span>{group.licenseArtifactIds.length} license{group.licenseArtifactIds.length === 1 ? "" : "s"}</span>
                </label>
              ))}
            </div>
          )}
          <label htmlFor="creation-processing-suffix">Output ID suffix</label>
          <input
            id="creation-processing-suffix"
            value={outputSuffix}
            placeholder="studio"
            pattern="[a-z0-9][a-z0-9_-]{0,31}"
            onChange={(event) => setOutputSuffix(event.target.value)}
          />
          <fieldset className="creation-nested-fieldset">
            <legend>Structured acceptance results</legend>
            {acceptanceRows.map((row, index) => (
              <div key={index} className="creation-acceptance-row">
                <h4>Criterion {index + 1}</h4>
                <label htmlFor={`creation-criterion-${String(index)}`}>Criterion {index + 1} SHA-256</label>
                <input
                  id={`creation-criterion-${String(index)}`}
                  value={row.criterionSha256}
                  onChange={(event) => updateAcceptance(index, { criterionSha256: event.target.value })}
                />
                <label htmlFor={`creation-criterion-status-${String(index)}`}>Criterion {index + 1} result</label>
                <select
                  id={`creation-criterion-status-${String(index)}`}
                  value={row.status}
                  onChange={(event) =>
                    updateAcceptance(index, { status: event.target.value === "failed" ? "failed" : "passed" })
                  }
                >
                  <option value="passed">Passed</option>
                  <option value="failed">Failed</option>
                </select>
                <label htmlFor={`creation-criterion-evidence-${String(index)}`}>
                  Criterion {index + 1} evidence SHA-256 values
                </label>
                <textarea
                  id={`creation-criterion-evidence-${String(index)}`}
                  rows={2}
                  value={row.evidenceHashes}
                  onChange={(event) => updateAcceptance(index, { evidenceHashes: event.target.value })}
                />
                {acceptanceRows.length > 1 ? (
                  <button
                    type="button"
                    onClick={() => setAcceptanceRows((current) => current.filter((_, rowIndex) => rowIndex !== index))}
                  >
                    Remove criterion {index + 1}
                  </button>
                ) : null}
              </div>
            ))}
            <button
              type="button"
              disabled={acceptanceRows.length >= MAX_ASSET_ACCEPTANCE_RESULTS}
              onClick={() => setAcceptanceRows((current) => [...current, { ...EMPTY_ACCEPTANCE }])}
            >
              Add acceptance criterion
            </button>
          </fieldset>
          <button type="button" disabled={processingGroup === null} onClick={() => void submitProcessing()}>
            {pendingAction === "processing" ? "Submitting processing…" : "Process selected asset"}
          </button>
        </fieldset>

        {!authorityReleaseAvailable ? (
        <fieldset className="creation-card creation-evidence-wide">
          <legend>Seal asset release</legend>
          <p>Only complete inventories with one passed, blocker-free QA report per asset and unmixed gamepack/target/style authority are offered.</p>
          {candidates.releaseGroups.length === 0 ? (
            <p>No complete passed QA inventory is available.</p>
          ) : (
            <div className="creation-radio-list">
              {candidates.releaseGroups.map((group) => (
                <label key={group.key}>
                  <input
                    type="radio"
                    name="creation-release-group"
                    disabled={downstreamBlocked}
                    checked={releaseKey === group.key}
                    aria-label={`${String(group.qaReportArtifactIds.length)} passed QA — ${lifecycleLabel(group.lifecycle)} — full inventory`}
                    onChange={() => setReleaseKey(group.key)}
                  />
                  <strong>{group.qaReportArtifactIds.length} passed QA</strong>
                  <span className={`creation-state-token state-${group.lifecycle}`}>
                    {lifecycleLabel(group.lifecycle)}
                  </span>
                  <span>{group.inventoryAssetCount} inventory asset{group.inventoryAssetCount === 1 ? "" : "s"}</span>
                </label>
              ))}
            </div>
          )}
          <button
            type="button"
            disabled={pendingAction !== null}
            onClick={() => void selectOutput()}
          >
            {pendingAction === "select-output"
              ? "Opening output selector…"
              : outputGrants.length === 0
                ? "Select asset pack output"
                : "Select another asset pack output"}
          </button>
          {outputGrants.length > 0 ? (
            <section className="creation-output-grant" aria-labelledby="asset-output-authority-heading">
              <h4 id="asset-output-authority-heading">Asset pack output authorities</h4>
              <div className="creation-radio-list">
                {outputGrants.map((item) => (
                  <label key={item.grant_id}>
                    <input
                      type="radio"
                      name="creation-asset-output-grant"
                      checked={grant?.grant_id === item.grant_id}
                      disabled={item.state !== "ready" || pendingAction !== null}
                      onChange={() => onGrantSelectionChange?.(item.grant_id)}
                    />
                    <strong>{item.display_name}</strong>
                    <span className={`creation-state-token state-${item.state}`}>
                      {titleToken(item.state)}
                    </span>
                    <span>Generation {String(item.generation)}</span>
                  </label>
                ))}
              </div>
              {grant ? (
                <dl className="creation-facts">
                  <GrantFact label="Display name" value={grant.display_name} />
                  <GrantFact label="Kind" value={grant.kind} />
                  <GrantFact label="State" value={titleToken(grant.state)} />
                  <GrantFact label="Generation" value={grant.generation} />
                </dl>
              ) : (
                <p>Select one ready authority before sealing.</p>
              )}
              {grant?.state === "ready" ? (
                <button type="button" onClick={() => void revokeOutput()}>
                  {pendingAction === "revoke-output" ? "Revoking output…" : "Revoke selected output"}
                </button>
              ) : null}
            </section>
          ) : (
            <p>No asset pack output authority is registered.</p>
          )}
          <button
            type="button"
            disabled={downstreamBlocked || releaseGroup === null || grant?.state !== "ready"}
            onClick={() => void submitRelease()}
          >
            {pendingAction === "seal-release" ? "Submitting release seal…" : "Seal selected asset release"}
          </button>
        </fieldset>
        ) : null}
      </div>
      <CreationAssetPreview
        api={api}
        authorityKey={authorityKey}
        items={previewCatalogIsCurrent ? previewCatalog.items : []}
        catalogPending={
          previewCatalogIsCurrent ? previewPending : workspace.project_kind === "game"
        }
        catalogError={previewCatalogIsCurrent ? previewError : null}
      />
    </section>
  );
}

function creationPreviewDependencyKey(
  authorityKey: string,
  grants: readonly StudioCreationOutputGrant[],
): string {
  const grantIdentities = grants
    .map((item) =>
      JSON.stringify([
        item.format,
        item.format_version,
        item.grant_id,
        item.workspace_id,
        item.kind,
        item.state,
        item.generation,
        item.publication,
      ]),
    )
    .sort();
  return JSON.stringify([authorityKey, grantIdentities]);
}

function authorityParams(census: CreationExecutionCensus) {
  return {
    workspaceId: census.authority.workspaceId,
    expectedRootGeneration: census.authority.rootGeneration,
    expectedSourceRevision: census.authority.sourceRevision,
    expectedWorkflowStatusHash: census.authority.workflowStatusHash,
    expectedArtifactSnapshotHash: census.authority.artifactSnapshotHash,
  };
}

function requireCreatedJob(
  value: unknown,
  census: CreationExecutionCensus,
  operation: CreationJobView["operation"],
): CreationJobView {
  const job = projectCreationJob(value, census.authority.workspaceId, census.authority);
  if (job === null || job.operation !== operation) {
    throw new Error(`Forge Studio returned a mismatched ${operation} job submission`);
  }
  return job;
}

function requireProcessingGroup(
  candidates: AssetPipelineCandidates,
  key: string,
): AssetProcessingGroup {
  const group = candidates.processingGroups.find((candidate) => candidate.key === key);
  if (!group) throw new Error("Asset processing selection changed before submission");
  return group;
}

function requireReleaseGroup(candidates: AssetPipelineCandidates, key: string): AssetReleaseGroup {
  const group = candidates.releaseGroups.find((candidate) => candidate.key === key);
  if (!group) throw new Error("Asset release selection changed before submission");
  return group;
}

function nextGrantGeneration(generation: number): number {
  const next = generation + 1;
  if (!Number.isSafeInteger(next)) {
    throw new Error("Asset output grant generation cannot advance safely");
  }
  return next;
}

async function refreshCreationAssetpackGrant(
  api: ForgeStudioApi,
  grant: StudioCreationOutputGrant,
  workspaceId: string,
): Promise<StudioCreationOutputGrant> {
  const result = await expectCreationEvidenceResult(
    api.getCreationAssetpackOutput(grant.grant_id),
    "creation_output_grant.get",
  );
  const refreshed = validateAssetpackGrant(result.grant, workspaceId);
  if (refreshed.grant_id !== grant.grant_id) {
    throw new Error("Forge Studio returned mismatched asset output grant authority");
  }
  return refreshed;
}

function validateAssetpackGrant(value: unknown, workspaceId: string): StudioCreationOutputGrant {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, [
      "created_at",
      "display_name",
      "format",
      "format_version",
      "generation",
      "grant_id",
      "kind",
      "publication",
      "state",
      "updated_at",
      "workspace_id",
    ]) ||
    value.format !== "world-forge.studio_creation_output_grant" ||
    value.format_version !== 1 ||
    typeof value.grant_id !== "string" ||
    !isPortableEntityId(value.grant_id) ||
    value.workspace_id !== workspaceId ||
    value.kind !== "generic_assetpack_directory" ||
    typeof value.display_name !== "string" ||
    value.display_name.length < 1 ||
    value.display_name.length > 128 ||
    value.display_name.normalize("NFC") !== value.display_name ||
    [...value.display_name].some(
      (character) =>
        character === "/" ||
        character === "\\" ||
        character.codePointAt(0)! < 0x20 ||
        character.codePointAt(0) === 0x7f,
    ) ||
    !["ready", "reserved", "published", "recovery_required", "revoked"].includes(
      String(value.state),
    ) ||
    !Number.isSafeInteger(value.generation) ||
    Number(value.generation) < 0 ||
    !isUtcTimestamp(value.created_at) ||
    !isUtcTimestamp(value.updated_at) ||
    Date.parse(value.updated_at) < Date.parse(value.created_at) ||
    (value.state === "published"
      ? !isAssetpackPublication(value.publication)
      : value.publication !== null)
  ) {
    throw new Error("Forge Studio returned an invalid asset output grant");
  }
  return value as unknown as StudioCreationOutputGrant;
}

function validateCreatedAssetpackGrant(
  value: unknown,
  workspaceId: string,
): StudioCreationOutputGrant {
  const grant = validateAssetpackGrant(value, workspaceId);
  if (grant.state !== "ready" || grant.generation !== 0 || grant.publication !== null) {
    throw new Error("Forge Studio returned an invalid asset output grant create transition");
  }
  return grant;
}

function validateRevokedAssetpackGrant(
  value: unknown,
  workspaceId: string,
  expected: StudioCreationOutputGrant,
): StudioCreationOutputGrant {
  if (!Number.isSafeInteger(expected.generation + 1)) {
    throw new Error("Asset output grant generation cannot advance safely");
  }
  const grant = validateAssetpackGrant(value, workspaceId);
  if (
    grant.grant_id !== expected.grant_id ||
    grant.state !== "revoked" ||
    grant.generation !== expected.generation + 1
  ) {
    throw new Error("Forge Studio returned a mismatched revoked asset output grant generation");
  }
  return grant;
}

function isAssetpackPublication(value: unknown): boolean {
  return (
    isRecord(value) &&
    hasExactKeys(value, ["content_hash", "format", "format_version", "id", "inventory_hash"]) &&
    value.format === "world-forge.assetpack" &&
    value.format_version === 1 &&
    typeof value.id === "string" &&
    isPortableEntityId(value.id) &&
    typeof value.content_hash === "string" &&
    /^[0-9a-f]{64}$/u.test(value.content_hash) &&
    typeof value.inventory_hash === "string" &&
    /^[0-9a-f]{64}$/u.test(value.inventory_hash)
  );
}

function hasExactKeys(value: Record<string, unknown>, expected: readonly string[]): boolean {
  const actual = Object.keys(value).sort();
  return actual.length === expected.length && actual.every((key, index) => key === expected[index]);
}

function isPortableEntityId(value: string): boolean {
  return /^[a-z0-9][a-z0-9_-]{0,127}$/u.test(value);
}

function isUtcTimestamp(value: unknown): value is string {
  return (
    typeof value === "string" &&
    UTC_TIMESTAMP.test(value) &&
    Number.isFinite(Date.parse(value))
  );
}

function GrantFact({ label, value }: { label: string; value: string | number }) {
  return <div><dt>{label}</dt><dd>{value}</dd></div>;
}

function lifecycleLabel(value: StudioCreationArtifact["lifecycle"]): string {
  return value === "active" ? "Active" : value === "candidate" ? "Candidate" : "Unavailable";
}

function operationLabel(operation: CreationJobView["operation"]): string {
  if (operation === "artifact.admit") return "Artifact admission";
  if (operation === "asset.process") return "Asset processing";
  return "Asset release seal";
}

function isAssetPipelineJob(job: CreationJobView | null): job is CreationJobView {
  return (
    job !== null &&
    (job.operation === "artifact.admit" ||
      job.operation === "asset.process" ||
      job.operation === "asset.release.seal")
  );
}

function titleToken(value: string): string {
  const normalized = value.replaceAll("_", " ");
  return normalized.charAt(0).toUpperCase() + normalized.slice(1);
}

function describeError(error: unknown): string {
  return error instanceof Error ? error.message : "Asset pipeline operation failed";
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

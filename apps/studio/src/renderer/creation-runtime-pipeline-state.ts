import type {
  ForgeStudioApi,
  StudioCreationArtifact,
  StudioCreationArtifactInspectResult,
  StudioCreationOutputGrant,
  StudioCreationRuntimeBundleBuildParams,
  StudioCreationRuntimeComposeParams,
} from "../shared/studio-api";
import {
  listCreationJobPage,
  projectCreationJob,
  type CreationExecutionAuthority,
  type CreationExecutionCensus,
  type CreationJobView,
} from "./creation-execution-state";
import { expectCreationEvidenceResult } from "./creation-service";

const MAX_RUNTIME_LINEAGE = 128;
const MAX_JOB_PAGES = 64;
const COMPOSE_OUTPUT_FORMATS = [
  "world-forge.game_runtime_snapshot",
  "world-forge.runtime_adapter_registry",
  "world-forge.game_runtime_composition",
  "world-forge.runtime_support_report",
] as const;
const PRE_EXECUTION_REASON_CODES = new Set([
  "adapter_not_verified",
  "headless_evidence_missing",
  "native_evidence_missing",
  "packaging_evidence_missing",
  "save_replay_evidence_missing",
]);
const OPTIONAL_REASON_CODES = new Set(["optional_feature_unsupported"]);

export interface RuntimeComposeCandidate {
  key: string;
  assetpackArtifactId: string;
  gamepackArtifactId: string;
  assetInventoryArtifactId: string;
  sourceGrantId: string;
  sourceGrantGeneration: number;
  sealJobId: string;
}

export interface RuntimeBundleCandidate extends RuntimeComposeCandidate {
  producerJobId: string;
  runtimeSnapshotArtifactId: string;
  runtimeAdapterRegistryArtifactId: string;
  runtimeCompositionArtifactId: string;
  runtimeSupportReportArtifactId: string;
  compatibilityStatus: "partially_supported" | "unsupported";
  supportReasonCodes: string[];
  optionalReasonCodes: string[];
  missingCapabilities: string[];
  blockingReasonCodes: string[];
  bundleAllowed: boolean;
}

export interface RuntimePipelineCandidates {
  composeCandidates: RuntimeComposeCandidate[];
  bundleCandidates: RuntimeBundleCandidate[];
  blockingReasonCodes: string[];
}

export interface LoadedRuntimePipelineCandidates extends RuntimePipelineCandidates {
  pendingJobs: CreationJobView[];
  boundGrantJobIds: ReadonlyMap<string, string>;
}

interface ValidatedProjection {
  artifact: StudioCreationArtifact;
  dependencies: string[];
  facts: ReadonlyMap<string, string | number | boolean | null | string[]>;
  status: string | null;
}

export function deriveRuntimePipelineCandidates(
  census: CreationExecutionCensus,
  inspections: ReadonlyMap<string, StudioCreationArtifactInspectResult>,
  producerJobs: ReadonlyMap<string, CreationJobView>,
  grants: readonly StudioCreationOutputGrant[],
): RuntimePipelineCandidates {
  const projections = new Map<string, ValidatedProjection>();
  for (const [artifactId, inspection] of inspections) {
    projections.set(artifactId, validateInspection(census, artifactId, inspection));
  }

  const composeCandidates = census.candidateArtifacts
    .filter((artifact) => artifact.subject.format === "world-forge.assetpack")
    .map((assetpack) =>
      deriveComposeCandidate(assetpack, census, projections, producerJobs, grants),
    )
    .sort((left, right) => compareUtf8(left.key, right.key));

  const runtimeArtifacts = census.candidateArtifacts.filter((artifact) =>
    COMPOSE_OUTPUT_FORMATS.includes(
      artifact.subject.format as (typeof COMPOSE_OUTPUT_FORMATS)[number],
    ),
  );
  const byProducer = new Map<string, StudioCreationArtifact[]>();
  for (const artifact of runtimeArtifacts) {
    if (
      artifact.producer.kind !== "future_candidate" ||
      artifact.producer.phase_id !== null
    ) {
      throw runtimeError("runtime output has no exact future-candidate producer");
    }
    const producerId = artifact.producer.reference_id;
    const group = byProducer.get(producerId) ?? [];
    group.push(artifact);
    byProducer.set(producerId, group);
  }
  const bundleCandidates = [...byProducer]
    .map(([producerId, artifacts]) =>
      deriveBundleCandidate(
        producerId,
        artifacts,
        census,
        projections,
        producerJobs,
        composeCandidates,
      ),
    )
    .sort((left, right) => compareUtf8(left.key, right.key));

  return { composeCandidates, bundleCandidates, blockingReasonCodes: [] };
}

export async function loadCreationRuntimePipelineCandidates(
  api: ForgeStudioApi,
  census: CreationExecutionCensus,
  grants: readonly StudioCreationOutputGrant[],
): Promise<LoadedRuntimePipelineCandidates> {
  const candidateArtifacts = census.candidateArtifacts.filter(
    (artifact) =>
      artifact.subject.format === "world-forge.assetpack" ||
      COMPOSE_OUTPUT_FORMATS.includes(
        artifact.subject.format as (typeof COMPOSE_OUTPUT_FORMATS)[number],
      ),
  );
  const inspections = new Map<string, StudioCreationArtifactInspectResult>();
  const producerIds = new Set<string>();
  for (const artifact of candidateArtifacts) {
    if (
      artifact.producer.kind !== "future_candidate" ||
      artifact.producer.phase_id !== null
    ) {
      throw runtimeError(`candidate ${artifact.artifact_id} has no exact producer`);
    }
    producerIds.add(artifact.producer.reference_id);
    const result = await expectCreationEvidenceResult(
      api.inspectCreationArtifact({
        workspaceId: census.authority.workspaceId,
        expectedRootGeneration: census.authority.rootGeneration,
        expectedSourceRevision: census.authority.sourceRevision,
        expectedWorkflowStatusHash: census.authority.workflowStatusHash,
        expectedArtifactSnapshotHash: census.authority.artifactSnapshotHash,
        artifactId: artifact.artifact_id,
      }),
      "creation_artifact.inspect",
    );
    if (!isInspectionShape(result)) {
      throw runtimeError("artifact inspection reply shape is invalid");
    }
    inspections.set(
      artifact.artifact_id,
      result as unknown as StudioCreationArtifactInspectResult,
    );
  }
  const producerJobs = new Map<string, CreationJobView>();
  for (const producerId of [...producerIds].sort(compareUtf8)) {
    const result = await expectCreationEvidenceResult(
      api.getCreationJob(producerId),
      "creation_job.get",
    );
    const job = projectCreationJob(result.job, census.authority.workspaceId);
    if (job === null || job.job_id !== producerId) {
      throw runtimeError(`producer job ${producerId} is invalid or mismatched`);
    }
    producerJobs.set(producerId, job);
  }
  const derived = deriveRuntimePipelineCandidates(
    census,
    inspections,
    producerJobs,
    grants,
  );
  const durableJobs = await loadDurableRuntimeJobs(api, census);
  const pendingJobs = durableJobs.filter(
    (job) => job.state === "queued" || job.state === "running",
  );
  const blockingReasonCodes = new Set(derived.blockingReasonCodes);
  for (const job of durableJobs) {
    if (job.cleanupPending) blockingReasonCodes.add("cleanup_pending");
    if (job.recoveryRequired) blockingReasonCodes.add("recovery_required");
  }
  const boundGrantJobIds = bindRuntimeBundleGrants(grants, durableJobs, census);
  return {
    ...derived,
    blockingReasonCodes: [...blockingReasonCodes].sort(compareUtf8),
    pendingJobs,
    boundGrantJobIds,
  };
}

export function runtimeComposeSubmission(
  census: CreationExecutionCensus,
  candidate: RuntimeComposeCandidate,
): StudioCreationRuntimeComposeParams {
  return {
    ...authorityParams(census),
    gamepackArtifactId: candidate.gamepackArtifactId,
    assetInventoryArtifactId: candidate.assetInventoryArtifactId,
    assetpackArtifactId: candidate.assetpackArtifactId,
    targetGrantId: candidate.sourceGrantId,
    expectedTargetGrantGeneration: candidate.sourceGrantGeneration,
  };
}

export function runtimeBundleBuildSubmission(
  census: CreationExecutionCensus,
  candidate: RuntimeBundleCandidate,
  targetGrant: StudioCreationOutputGrant,
): StudioCreationRuntimeBundleBuildParams {
  if (
    !candidate.bundleAllowed ||
    targetGrant.workspace_id !== census.authority.workspaceId ||
    targetGrant.format_version !== 2 ||
    targetGrant.kind !== "game_runtime_bundle_directory" ||
    targetGrant.state !== "ready" ||
    targetGrant.publication !== null
  ) {
    throw runtimeError("runtime bundle target or capability authority is not ready");
  }
  return {
    ...authorityParams(census),
    gamepackArtifactId: candidate.gamepackArtifactId,
    assetInventoryArtifactId: candidate.assetInventoryArtifactId,
    assetpackArtifactId: candidate.assetpackArtifactId,
    runtimeSnapshotArtifactId: candidate.runtimeSnapshotArtifactId,
    runtimeAdapterRegistryArtifactId: candidate.runtimeAdapterRegistryArtifactId,
    runtimeCompositionArtifactId: candidate.runtimeCompositionArtifactId,
    runtimeSupportReportArtifactId: candidate.runtimeSupportReportArtifactId,
    sourceGrantId: candidate.sourceGrantId,
    expectedSourceGrantGeneration: candidate.sourceGrantGeneration,
    targetGrantId: targetGrant.grant_id,
    expectedTargetGrantGeneration: targetGrant.generation,
  };
}

export async function findIdenticalPendingRuntimeJob(
  api: ForgeStudioApi,
  authority: CreationExecutionAuthority,
  operation: "runtime.compose" | "runtime.bundle.build",
  submission:
    | StudioCreationRuntimeComposeParams
    | StudioCreationRuntimeBundleBuildParams,
): Promise<CreationJobView | null> {
  const expected = operationParamsFromSubmission(operation, submission);
  let match: CreationJobView | null = null;
  for (const state of ["queued", "running"] as const) {
    let afterSequence = 0;
    const cursors = new Set<number>();
    for (let pageIndex = 0; pageIndex < MAX_JOB_PAGES; pageIndex += 1) {
      const page = await listCreationJobPage(
        api,
        authority.workspaceId,
        state,
        afterSequence,
      );
      for (const listed of page.jobs) {
        const exact = projectCreationJob(
          listed.record,
          authority.workspaceId,
          authority,
        );
        if (
          exact?.operation === operation &&
          sameJson(operationParameters(exact), expected)
        ) {
          if (match !== null) {
            throw runtimeError("multiple identical runtime jobs are in flight");
          }
          match = exact;
        }
      }
      if (page.nextSequence === null) break;
      if (cursors.has(page.nextSequence)) {
        throw runtimeError("pending job cursor repeats");
      }
      cursors.add(page.nextSequence);
      afterSequence = page.nextSequence;
      if (pageIndex === MAX_JOB_PAGES - 1) {
        throw runtimeError("pending jobs exceed the bounded page limit");
      }
    }
  }
  return match;
}

function deriveComposeCandidate(
  assetpack: StudioCreationArtifact,
  census: CreationExecutionCensus,
  projections: ReadonlyMap<string, ValidatedProjection>,
  producerJobs: ReadonlyMap<string, CreationJobView>,
  grants: readonly StudioCreationOutputGrant[],
): RuntimeComposeCandidate {
  if (
    assetpack.producer.kind !== "future_candidate" ||
    assetpack.producer.phase_id !== null
  ) {
    throw runtimeError("sealed assetpack candidate has no exact future-candidate producer");
  }
  const projection = requireProjection(projections, assetpack.artifact_id);
  const gamepackArtifactId = exactDependencyFormat(
    projection.dependencies,
    census,
    "world-forge.gamepack",
    "sealed assetpack",
  );
  const assetInventoryArtifactId = exactDependencyFormat(
    projection.dependencies,
    census,
    "world-forge.asset_inventory",
    "sealed assetpack",
  );
  const exactDependencies = [gamepackArtifactId, assetInventoryArtifactId];
  for (const format of [
    "world-forge.asset_subject",
    "world-forge.asset_target",
    "world-forge.asset_style",
    "world-forge.asset_manifest",
  ]) {
    exactDependencies.push(
      exactDependencyFormat(projection.dependencies, census, format, "sealed assetpack"),
    );
  }
  requireExactDependencies(projection, exactDependencies, "sealed assetpack");
  const manifestArtifactId = exactDependencies.at(-1)!;

  const sealJobId = assetpack.producer.reference_id;
  const sealJob = producerJobs.get(sealJobId);
  if (
    !sealJob ||
    !matchesPublicAuthority(sealJob, census) ||
    sealJob.operation !== "asset.release.seal" ||
    sealJob.state !== "succeeded" ||
    sealJob.progress !== "committed" ||
    sealJob.cleanupPending ||
    sealJob.recoveryRequired ||
    sealJob.analysisStatus !== "passed" ||
    sealJob.record.result === null ||
    sealJob.record.result.output_artifact_ids.length !== 2 ||
    sealJob.record.result.output_artifact_ids[1] !== assetpack.artifact_id
  ) {
    throw runtimeError("sealed assetpack producer is incomplete or output membership changed");
  }
  const manifestArtifact = census.selectableById.get(
    sealJob.record.result.output_artifact_ids[0],
  );
  if (
    !manifestArtifact ||
    manifestArtifact.artifact_id !== manifestArtifactId ||
    manifestArtifact.subject.format !== "world-forge.asset_manifest" ||
    manifestArtifact.producer.kind !== "future_candidate" ||
    manifestArtifact.producer.phase_id !== null ||
    manifestArtifact.producer.reference_id !== sealJobId
  ) {
    throw runtimeError("sealed assetpack producer manifest membership changed");
  }
  const result = sealJob.record.result as unknown as Record<string, unknown>;
  const publication = result.publication;
  if (!isRecord(publication) || !isRecord(publication.assetpack)) {
    throw runtimeError("sealed assetpack publication is unavailable");
  }
  const grantId = publication.grant_id;
  const grantGeneration = publication.grant_generation;
  const grant = grants.find((item) => item.grant_id === grantId);
  if (
    typeof grantId !== "string" ||
    !Number.isSafeInteger(grantGeneration) ||
    publication.kind !== "generic_assetpack_directory" ||
    publication.state !== "published" ||
    !sameSubject(publication.assetpack, assetpack.subject) ||
    !grant ||
    grant.workspace_id !== census.authority.workspaceId ||
    grant.format_version !== 1 ||
    grant.kind !== "generic_assetpack_directory" ||
    grant.state !== "published" ||
    grant.generation !== grantGeneration ||
    !sameJson(grant.publication, publication.assetpack)
  ) {
    throw runtimeError("sealed assetpack source grant publication changed");
  }
  return {
    key: [assetpack.artifact_id, grantId, String(grantGeneration)].join("\u0000"),
    assetpackArtifactId: assetpack.artifact_id,
    gamepackArtifactId,
    assetInventoryArtifactId,
    sourceGrantId: grantId,
    sourceGrantGeneration: Number(grantGeneration),
    sealJobId,
  };
}

function deriveBundleCandidate(
  producerId: string,
  artifacts: readonly StudioCreationArtifact[],
  census: CreationExecutionCensus,
  projections: ReadonlyMap<string, ValidatedProjection>,
  producerJobs: ReadonlyMap<string, CreationJobView>,
  composeCandidates: readonly RuntimeComposeCandidate[],
): RuntimeBundleCandidate {
  const byFormat = new Map<string, StudioCreationArtifact>();
  for (const artifact of artifacts) {
    if (byFormat.has(artifact.subject.format)) {
      throw runtimeError("runtime compose four-output group repeats a format");
    }
    byFormat.set(artifact.subject.format, artifact);
  }
  if (
    artifacts.length !== COMPOSE_OUTPUT_FORMATS.length ||
    COMPOSE_OUTPUT_FORMATS.some((format) => !byFormat.has(format))
  ) {
    throw runtimeError("runtime compose four-output group is missing or mixed across producers");
  }
  const ordered = COMPOSE_OUTPUT_FORMATS.map((format) => byFormat.get(format)!);
  if (
    ordered.some(
      (artifact) =>
        artifact.producer.kind !== "future_candidate" ||
        artifact.producer.phase_id !== null ||
        artifact.producer.reference_id !== producerId,
    )
  ) {
    throw runtimeError("runtime compose outputs have mixed producer authority");
  }
  const job = producerJobs.get(producerId);
  if (
    !job ||
    !matchesPublicAuthority(job, census) ||
    job.operation !== "runtime.compose" ||
    job.state !== "succeeded" ||
    job.progress !== "committed" ||
    job.cleanupPending ||
    job.recoveryRequired ||
    job.record.result === null ||
    !sameStringArray(job.record.result.output_artifact_ids, ordered.map((item) => item.artifact_id))
  ) {
    throw runtimeError("runtime compose producer or exact output membership changed");
  }
  if (job.record.result.artifact_snapshot_hash !== census.authority.artifactSnapshotHash) {
    throw runtimeError("runtime compose producer artifact snapshot changed");
  }
  const params = operationParameters(job);
  const compose = composeCandidates.find(
    (candidate) => sameJson(params, {
      gamepack_artifact_id: candidate.gamepackArtifactId,
      asset_inventory_artifact_id: candidate.assetInventoryArtifactId,
      assetpack_artifact_id: candidate.assetpackArtifactId,
      target_grant_id: candidate.sourceGrantId,
      target_grant_generation: candidate.sourceGrantGeneration,
    }),
  );
  if (!compose) {
    throw runtimeError(
      "runtime compose producer parameters cross sealed assetpack authority",
    );
  }
  const expectedInputs = [
    compose.gamepackArtifactId,
    compose.assetInventoryArtifactId,
    compose.assetpackArtifactId,
  ];
  if (
    job.record.inputs.length !== expectedInputs.length ||
    job.record.inputs.some((input, index) => {
      const artifact = census.selectableById.get(expectedInputs[index]);
      return (
        !artifact ||
        input.artifact_id !== artifact.artifact_id ||
        !sameSubject(input.subject, artifact.subject)
      );
    })
  ) {
    throw runtimeError("runtime compose producer input lineage is incomplete or ambiguous");
  }

  const [runtimeSnapshot, runtimeRegistry, runtimeComposition, runtimeSupport] = ordered;
  requireExactDependencies(
    requireProjection(projections, runtimeSnapshot.artifact_id),
    [],
    "runtime snapshot",
  );
  requireExactDependencies(
    requireProjection(projections, runtimeRegistry.artifact_id),
    [runtimeSnapshot.artifact_id],
    "runtime registry",
  );
  requireExactDependencies(
    requireProjection(projections, runtimeComposition.artifact_id),
    [
      compose.gamepackArtifactId,
      compose.assetInventoryArtifactId,
      compose.assetpackArtifactId,
      runtimeRegistry.artifact_id,
      runtimeSnapshot.artifact_id,
    ],
    "runtime composition",
  );
  const supportProjection = requireProjection(projections, runtimeSupport.artifact_id);
  requireExactDependencies(
    supportProjection,
    [compose.gamepackArtifactId, runtimeComposition.artifact_id],
    "runtime support report",
  );
  const support = validatePreExecutionSupport(supportProjection);
  const resultReasonCodes = exactStringArray(
    job.record.result.reason_codes,
    "runtime compose result reason codes",
  );
  const optionalReasonCodes = resultReasonCodes.filter((code) => OPTIONAL_REASON_CODES.has(code));
  const producerBlocking = resultReasonCodes.filter((code) => !OPTIONAL_REASON_CODES.has(code));
  const blockingReasonCodes = [...new Set([
    ...producerBlocking,
    ...(support.compatibilityStatus === "unsupported"
      ? support.supportReasonCodes.filter(
          (code) => !PRE_EXECUTION_REASON_CODES.has(code),
        )
      : []),
    ...(support.missingCapabilities.length > 0 ? ["required_feature_unsupported"] : []),
  ])].sort(compareUtf8);
  if (job.analysisStatus !== "passed" && blockingReasonCodes.length === 0) {
    blockingReasonCodes.push(`runtime_compose_${job.analysisStatus ?? "invalid"}`);
  }
  return {
    ...compose,
    key: [compose.key, producerId, ...ordered.map((item) => item.artifact_id)].join("\u0000"),
    producerJobId: producerId,
    runtimeSnapshotArtifactId: runtimeSnapshot.artifact_id,
    runtimeAdapterRegistryArtifactId: runtimeRegistry.artifact_id,
    runtimeCompositionArtifactId: runtimeComposition.artifact_id,
    runtimeSupportReportArtifactId: runtimeSupport.artifact_id,
    compatibilityStatus: support.compatibilityStatus,
    supportReasonCodes: support.supportReasonCodes,
    optionalReasonCodes,
    missingCapabilities: support.missingCapabilities,
    blockingReasonCodes,
    bundleAllowed: blockingReasonCodes.length === 0 && job.analysisStatus === "passed",
  };
}

function validatePreExecutionSupport(projection: ValidatedProjection): {
  compatibilityStatus: "partially_supported" | "unsupported";
  supportReasonCodes: string[];
  missingCapabilities: string[];
} {
  const supported = booleanFact(projection, "supported", "runtime support report");
  const compatibility = stringFact(
    projection,
    "compatibility_status",
    "runtime support report",
  );
  if (supported || (compatibility !== "partially_supported" && compatibility !== "unsupported")) {
    throw runtimeError("runtime support report is not a pre-execution support state");
  }
  const supportReasonCodes = stringArrayFact(
    projection,
    "reason_codes",
    "runtime support report",
  );
  const reasonCount = integerFact(projection, "reason_code_count", "runtime support report");
  if (supportReasonCodes.length !== reasonCount) {
    throw runtimeError("runtime support report reason projection is truncated");
  }
  const missingCapabilities = stringArrayFact(
    projection,
    "missing_capabilities",
    "runtime support report",
  );
  const missingCount = integerFact(
    projection,
    "missing_capability_count",
    "runtime support report",
  );
  if (missingCapabilities.length !== missingCount) {
    throw runtimeError("runtime support report missing capability projection is truncated");
  }
  if (
    integerFact(projection, "evidence_count", "runtime support report") !== 0 ||
    stringFact(projection, "authoring", "runtime support report") !== "valid" ||
    stringFact(projection, "compilation", "runtime support report") !== "compiled" ||
    stringFact(projection, "assets", "runtime support report") !== "sealed" ||
    stringFact(projection, "adapter", "runtime support report") !== "declared" ||
    stringFact(projection, "packaging", "runtime support report") !== "unverified" ||
    stringFact(projection, "release", "runtime support report") !== "blocked"
  ) {
    throw runtimeError("runtime support report is not evidence-free and release-blocked");
  }
  const executionStatuses = stringArrayFact(
    projection,
    "execution_statuses",
    "runtime support report",
  );
  if (
    executionStatuses.length < 1 ||
    executionStatuses.some((item) => !item.endsWith(":untested"))
  ) {
    throw runtimeError("runtime support report execution evidence is not pre-execution");
  }
  const requiredPreExecution = [
    "adapter_not_verified",
    "headless_evidence_missing",
    "native_evidence_missing",
    "packaging_evidence_missing",
    "save_replay_evidence_missing",
  ];
  if (requiredPreExecution.some((code) => !supportReasonCodes.includes(code))) {
    throw runtimeError("runtime support report omits a precise pre-execution reason code");
  }
  return {
    compatibilityStatus: compatibility,
    supportReasonCodes,
    missingCapabilities,
  };
}

function validateInspection(
  census: CreationExecutionCensus,
  artifactId: string,
  result: StudioCreationArtifactInspectResult,
): ValidatedProjection {
  const expected = census.selectableById.get(artifactId);
  if (!expected || result.artifact.artifact_id !== artifactId) {
    throw runtimeError("inspection is outside the current active/candidate census");
  }
  if (
    result.artifact_snapshot_hash !== census.authority.artifactSnapshotHash ||
    !matchesAuthorityRecord(result.authority, census) ||
    !matchesAuthorityRecord(result.artifact.authority, census) ||
    result.artifact.record_hash !== expected.record_hash ||
    result.artifact.lifecycle !== expected.lifecycle ||
    !sameSubject(result.artifact.subject, expected.subject) ||
    result.artifact.references.dependency_count !== expected.references.dependency_count ||
    !sameJson(result.artifact.producer, expected.producer)
  ) {
    throw runtimeError("inspection authority, producer, or dependency count changed");
  }
  const lineage = result.projection.lineage;
  if (
    !Array.isArray(lineage) ||
    lineage.length !== expected.references.dependency_count ||
    lineage.length > MAX_RUNTIME_LINEAGE
  ) {
    throw runtimeError("inspection lineage is truncated or mismatches its dependency count");
  }
  const dependencies = new Set<string>();
  for (const edge of lineage) {
    const dependency = census.selectableById.get(edge.artifact_id);
    if (
      edge.relation !== "depends_on" ||
      !dependency ||
      edge.lifecycle !== dependency.lifecycle ||
      (dependency.lifecycle !== "active" && dependency.lifecycle !== "candidate")
    ) {
      throw runtimeError("inspection lineage contains stale or historical authority");
    }
    if (dependencies.has(edge.artifact_id)) {
      throw runtimeError("inspection lineage repeats a dependency");
    }
    dependencies.add(edge.artifact_id);
  }
  if (!Array.isArray(result.projection.facts) || result.projection.facts.length > 128) {
    throw runtimeError("inspection facts are invalid or truncated");
  }
  const facts = new Map<string, string | number | boolean | null | string[]>();
  for (const fact of result.projection.facts) {
    if (facts.has(fact.key)) throw runtimeError("inspection facts repeat a key");
    facts.set(fact.key, fact.value);
  }
  return {
    artifact: expected,
    dependencies: [...dependencies].sort(compareUtf8),
    facts,
    status: result.projection.status,
  };
}

function requireProjection(
  projections: ReadonlyMap<string, ValidatedProjection>,
  artifactId: string,
): ValidatedProjection {
  const projection = projections.get(artifactId);
  if (!projection) throw runtimeError(`inspection is missing for ${artifactId}`);
  return projection;
}

function exactDependencyFormat(
  dependencies: readonly string[],
  census: CreationExecutionCensus,
  format: string,
  context: string,
): string {
  const matches = dependencies.filter(
    (artifactId) => census.selectableById.get(artifactId)?.subject.format === format,
  );
  if (matches.length !== 1) {
    throw runtimeError(`${context} has missing or ambiguous ${format} lineage`);
  }
  return matches[0];
}

function requireExactDependencies(
  projection: ValidatedProjection,
  expected: readonly string[],
  context: string,
): void {
  const ordered = [...expected].sort(compareUtf8);
  if (!sameStringArray(projection.dependencies, ordered)) {
    throw runtimeError(`${context} exact lineage is missing, mixed, or ambiguous`);
  }
}

function matchesPublicAuthority(
  job: CreationJobView,
  census: CreationExecutionCensus,
): boolean {
  return (
    job.workspace_id === census.authority.workspaceId &&
    job.authority.workspaceId === census.authority.workspaceId &&
    job.authority.rootGeneration === census.authority.rootGeneration &&
    job.authority.sourceRevision === census.authority.sourceRevision &&
    job.authority.workflowStatusHash === census.authority.workflowStatusHash
  );
}

function matchesAuthorityRecord(
  value: unknown,
  census: CreationExecutionCensus,
): boolean {
  return (
    isRecord(value) &&
    value.workspace_id === census.authority.workspaceId &&
    value.root_generation === census.authority.rootGeneration &&
    value.source_revision === census.authority.sourceRevision &&
    value.workflow_status_hash === census.authority.workflowStatusHash
  );
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

function operationParamsFromSubmission(
  operation: "runtime.compose" | "runtime.bundle.build",
  submission:
    | StudioCreationRuntimeComposeParams
    | StudioCreationRuntimeBundleBuildParams,
): Record<string, unknown> {
  if (operation === "runtime.compose") {
    return {
      gamepack_artifact_id: submission.gamepackArtifactId,
      asset_inventory_artifact_id: submission.assetInventoryArtifactId,
      assetpack_artifact_id: submission.assetpackArtifactId,
      target_grant_id: submission.targetGrantId,
      target_grant_generation: submission.expectedTargetGrantGeneration,
    };
  }
  const bundle = submission as StudioCreationRuntimeBundleBuildParams;
  return {
    gamepack_artifact_id: bundle.gamepackArtifactId,
    asset_inventory_artifact_id: bundle.assetInventoryArtifactId,
    assetpack_artifact_id: bundle.assetpackArtifactId,
    runtime_snapshot_artifact_id: bundle.runtimeSnapshotArtifactId,
    runtime_adapter_registry_artifact_id: bundle.runtimeAdapterRegistryArtifactId,
    runtime_composition_artifact_id: bundle.runtimeCompositionArtifactId,
    runtime_support_report_artifact_id: bundle.runtimeSupportReportArtifactId,
    source_grant_id: bundle.sourceGrantId,
    source_grant_generation: bundle.expectedSourceGrantGeneration,
    target_grant_id: bundle.targetGrantId,
    target_grant_generation: nextGeneration(bundle.expectedTargetGrantGeneration),
  };
}

function nextGeneration(value: number): number {
  const next = value + 1;
  if (!Number.isSafeInteger(next)) throw runtimeError("grant generation cannot advance safely");
  return next;
}

function operationParameters(job: CreationJobView): Record<string, unknown> {
  const record = job.record as unknown as Record<string, unknown>;
  return isRecord(record.operation_params) ? record.operation_params : {};
}

async function loadDurableRuntimeJobs(
  api: ForgeStudioApi,
  census: CreationExecutionCensus,
): Promise<CreationJobView[]> {
  const retained: CreationJobView[] = [];
  const seen = new Set<string>();
  const cursors = new Set<number>();
  let afterSequence = 0;
  for (let pageIndex = 0; pageIndex < MAX_JOB_PAGES; pageIndex += 1) {
    const page = await listCreationJobPage(
      api,
      census.authority.workspaceId,
      null,
      afterSequence,
    );
    for (const job of page.jobs) {
      if (seen.has(job.job_id)) throw runtimeError("durable job census repeats an identity");
      seen.add(job.job_id);
      if (
        (job.operation === "runtime.compose" || job.operation === "runtime.bundle.build") &&
        matchesPublicAuthority(job, census) &&
        job.authority.artifactSnapshotHash === census.authority.artifactSnapshotHash &&
        (job.state === "queued" ||
          job.state === "running" ||
          job.state === "orphaned" ||
          job.cleanupPending ||
          job.recoveryRequired)
      ) {
        retained.push(job);
      }
    }
    if (page.nextSequence === null) return retained;
    if (cursors.has(page.nextSequence)) throw runtimeError("durable job cursor repeats");
    cursors.add(page.nextSequence);
    afterSequence = page.nextSequence;
  }
  throw runtimeError("durable job census exceeds the bounded page limit");
}

function bindRuntimeBundleGrants(
  grants: readonly StudioCreationOutputGrant[],
  jobs: readonly CreationJobView[],
  census: CreationExecutionCensus,
): ReadonlyMap<string, string> {
  const bindings = new Map<string, string>();
  const usedJobs = new Set<string>();
  for (const grant of grants) {
    if (
      grant.workspace_id !== census.authority.workspaceId ||
      grant.format_version !== 2 ||
      grant.kind !== "game_runtime_bundle_directory" ||
      (grant.state !== "reserved" && grant.state !== "recovery_required")
    ) {
      continue;
    }
    const expectedStates =
      grant.state === "reserved"
        ? new Set<CreationJobView["state"]>(["queued", "running"])
        : new Set<CreationJobView["state"]>(["orphaned"]);
    const expectedGeneration =
      grant.state === "reserved" ? grant.generation : grant.generation - 1;
    const matches = jobs.filter((job) => {
      const params = operationParameters(job);
      return (
        expectedGeneration >= 0 &&
        job.operation === "runtime.bundle.build" &&
        expectedStates.has(job.state) &&
        params.target_grant_id === grant.grant_id &&
        params.target_grant_generation === expectedGeneration
      );
    });
    if (matches.length !== 1 || usedJobs.has(matches[0]?.job_id ?? "")) {
      throw runtimeError(
        `${grant.state} runtime bundle grant ${grant.grant_id} has no unambiguous durable job binding`,
      );
    }
    usedJobs.add(matches[0].job_id);
    bindings.set(grant.grant_id, matches[0].job_id);
  }
  return bindings;
}

function isInspectionShape(value: Record<string, unknown>): boolean {
  return (
    typeof value.artifact_snapshot_hash === "string" &&
    isRecord(value.authority) &&
    isRecord(value.artifact) &&
    isRecord(value.projection) &&
    Array.isArray(value.projection.facts) &&
    Array.isArray(value.projection.lineage)
  );
}

function stringFact(projection: ValidatedProjection, key: string, context: string): string {
  const value = projection.facts.get(key);
  if (typeof value !== "string") throw runtimeError(`${context} ${key} fact is unavailable`);
  return value;
}

function booleanFact(projection: ValidatedProjection, key: string, context: string): boolean {
  const value = projection.facts.get(key);
  if (typeof value !== "boolean") throw runtimeError(`${context} ${key} fact is unavailable`);
  return value;
}

function integerFact(projection: ValidatedProjection, key: string, context: string): number {
  const value = projection.facts.get(key);
  if (!Number.isSafeInteger(value) || Number(value) < 0) {
    throw runtimeError(`${context} ${key} fact is unavailable`);
  }
  return Number(value);
}

function stringArrayFact(
  projection: ValidatedProjection,
  key: string,
  context: string,
): string[] {
  return exactStringArray(projection.facts.get(key), `${context} ${key}`);
}

function exactStringArray(value: unknown, context: string): string[] {
  if (
    !Array.isArray(value) ||
    value.length > MAX_RUNTIME_LINEAGE ||
    value.some((item) => typeof item !== "string")
  ) {
    throw runtimeError(`${context} is unavailable or exceeds its projection bound`);
  }
  const strings = value as string[];
  if (new Set(strings).size !== strings.length) {
    throw runtimeError(`${context} repeats a value`);
  }
  const ordered = [...strings].sort(compareUtf8);
  if (!sameStringArray(strings, ordered)) {
    throw runtimeError(`${context} is not canonically ordered`);
  }
  return ordered;
}

function sameSubject(left: unknown, right: unknown): boolean {
  return (
    isRecord(left) &&
    isRecord(right) &&
    left.format === right.format &&
    left.format_version === right.format_version &&
    left.id === right.id &&
    left.content_hash === right.content_hash
  );
}

function sameStringArray(left: readonly string[], right: readonly string[]): boolean {
  return left.length === right.length && left.every((value, index) => value === right[index]);
}

function sameJson(left: unknown, right: unknown): boolean {
  return JSON.stringify(sortJson(left)) === JSON.stringify(sortJson(right));
}

function sortJson(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(sortJson);
  if (!isRecord(value)) return value;
  const result: Record<string, unknown> = {};
  for (const key of Object.keys(value).sort(compareUtf8)) result[key] = sortJson(value[key]);
  return result;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
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

function runtimeError(reason: string): Error {
  return new Error(`Forge Studio runtime pipeline failed closed: ${reason}`);
}

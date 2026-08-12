import type {
  ForgeStudioApi,
  StudioCreationArtifact,
  StudioCreationArtifactInspectResult,
  StudioCreationGameMaterializeParams,
  StudioCreationGamePackageExtractParams,
  StudioCreationGamePackageParams,
  StudioCreationMaterializationBundleBuildParams,
  StudioCreationOutputGrant,
} from "../shared/studio-api";
import {
  listCreationJobPage,
  projectCreationJob,
  type CreationExecutionAuthority,
  type CreationExecutionCensus,
  type CreationJobView,
} from "./creation-execution-state";
import { expectCreationEvidenceResult } from "./creation-service";

const MAX_LINEAGE = 128;
const MAX_JOB_PAGES = 64;
const PIPELINE_OPERATIONS = [
  "game.materialization.bundle.build",
  "game.materialize",
  "game.package",
  "game.package.extract",
] as const;
const PIPELINE_FORMATS = new Set([
  "world-forge.game_runtime_bundle",
  "world-forge.game_materialization_bundle",
  "world-forge.standalone_game",
  "world-forge.game_package",
  "world-forge.game_package_extraction",
]);

export type MaterializationOperation = (typeof PIPELINE_OPERATIONS)[number];

interface MaterializationCandidateBase {
  key: string;
  artifactId: string;
  producerJobId: string;
  predecessorArtifactId: string | null;
  sourceGrantId: string;
  sourceGrantGeneration: number;
  gamepackArtifactId: string;
  gamepackContentHash: string;
  contentHash: string;
}

export interface RuntimeBundleMaterializationCandidate
  extends MaterializationCandidateBase {
  predecessorArtifactId: null;
  treeHash: string;
}

export interface MaterializationBundleCandidate
  extends MaterializationCandidateBase {
  predecessorArtifactId: string;
  treeHash: string;
}

export interface StandaloneMaterializationCandidate
  extends MaterializationCandidateBase {
  predecessorArtifactId: string;
  treeHash: string;
  preservedStandaloneArtifactId: string;
  preservedStandaloneSubjectId: string;
  preservedStandaloneContentHash: string;
  preservedStandaloneTreeHash: string;
}

export interface PackageMaterializationCandidate
  extends MaterializationCandidateBase {
  predecessorArtifactId: string;
  archiveSha256: string;
  sizeBytes: number;
  preservedStandaloneArtifactId: string;
  preservedStandaloneSubjectId: string;
  preservedStandaloneContentHash: string;
  preservedStandaloneTreeHash: string;
}

export interface ExtractionMaterializationCandidate {
  key: string;
  artifactId: string;
  producerJobId: string;
  predecessorArtifactId: string;
  publishedStandaloneGrantId: string;
  publishedStandaloneGrantGeneration: number;
  preservedStandaloneArtifactId: string;
  preservedStandaloneSubjectId: string;
  preservedStandaloneContentHash: string;
  preservedStandaloneTreeHash: string;
  gamepackArtifactId: string;
  gamepackContentHash: string;
  contentHash: string;
}

export interface MaterializationPipelineCandidates {
  runtimeBundleCandidates: RuntimeBundleMaterializationCandidate[];
  materializationBundleCandidates: MaterializationBundleCandidate[];
  standaloneCandidates: StandaloneMaterializationCandidate[];
  packageCandidates: PackageMaterializationCandidate[];
  extractionCandidates: ExtractionMaterializationCandidate[];
  blockingReasonCodes: string[];
}

export interface LoadedMaterializationPipelineCandidates
  extends MaterializationPipelineCandidates {
  pendingJobs: CreationJobView[];
  boundGrantJobIds: ReadonlyMap<string, string>;
}

interface ValidatedInspection {
  artifact: StudioCreationArtifact;
  dependencies: string[];
}

export function deriveMaterializationPipelineCandidates(
  census: CreationExecutionCensus,
  inspections: ReadonlyMap<string, StudioCreationArtifactInspectResult>,
  producerJobs: ReadonlyMap<string, CreationJobView>,
  grants: readonly StudioCreationOutputGrant[],
): MaterializationPipelineCandidates {
  const projections = new Map<string, ValidatedInspection>();
  for (const artifact of census.candidateArtifacts.filter((item) =>
    PIPELINE_FORMATS.has(item.subject.format),
  )) {
    const inspection = inspections.get(artifact.artifact_id);
    if (!inspection) {
      throw materializationError(`inspection is missing for ${artifact.artifact_id}`);
    }
    projections.set(
      artifact.artifact_id,
      validateInspection(census, artifact, inspection),
    );
  }

  const usedPublishedGrantIds = new Set<string>();
  const runtimeBundleCandidates = candidatesByFormat(
    census,
    "world-forge.game_runtime_bundle",
  )
    .map((artifact) =>
      deriveRuntimeBundleCandidate(
        artifact,
        census,
        projections,
        producerJobs,
        grants,
        usedPublishedGrantIds,
      ),
    )
    .sort(candidateOrder);

  const materializationBundleCandidates = candidatesByFormat(
    census,
    "world-forge.game_materialization_bundle",
  )
    .map((artifact) =>
      deriveMaterializationBundleCandidate(
        artifact,
        census,
        projections,
        producerJobs,
        grants,
        runtimeBundleCandidates,
        usedPublishedGrantIds,
      ),
    )
    .sort(candidateOrder);

  const standaloneCandidates = candidatesByFormat(
    census,
    "world-forge.standalone_game",
  )
    .map((artifact) =>
      deriveStandaloneCandidate(
        artifact,
        census,
        projections,
        producerJobs,
        grants,
        materializationBundleCandidates,
        usedPublishedGrantIds,
      ),
    )
    .sort(candidateOrder);

  const packageCandidates = candidatesByFormat(census, "world-forge.game_package")
    .map((artifact) =>
      derivePackageCandidate(
        artifact,
        census,
        projections,
        producerJobs,
        grants,
        standaloneCandidates,
        usedPublishedGrantIds,
      ),
    )
    .sort(candidateOrder);

  const extractionCandidates = candidatesByFormat(
    census,
    "world-forge.game_package_extraction",
  )
    .map((artifact) =>
      deriveExtractionCandidate(
        artifact,
        census,
        projections,
        producerJobs,
        grants,
        packageCandidates,
        usedPublishedGrantIds,
      ),
    )
    .sort(candidateOrder);

  for (const grant of grants) {
    if (
      grant.workspace_id === census.authority.workspaceId &&
      grant.state === "published" &&
      grant.format_version >= 2 &&
      grant.format_version <= 5 &&
      !usedPublishedGrantIds.has(grant.grant_id)
    ) {
      throw materializationError(
        `published grant ${grant.grant_id} has no exact current candidate producer`,
      );
    }
  }

  return {
    runtimeBundleCandidates,
    materializationBundleCandidates,
    standaloneCandidates,
    packageCandidates,
    extractionCandidates,
    blockingReasonCodes: [],
  };
}

export async function loadCreationMaterializationPipelineCandidates(
  api: ForgeStudioApi,
  census: CreationExecutionCensus,
  grants: readonly StudioCreationOutputGrant[],
): Promise<LoadedMaterializationPipelineCandidates> {
  const relevant = census.candidateArtifacts.filter((artifact) =>
    PIPELINE_FORMATS.has(artifact.subject.format),
  );
  const inspections = new Map<string, StudioCreationArtifactInspectResult>();
  const producerIds = new Set<string>();
  for (const artifact of relevant) {
    requireFutureCandidate(artifact);
    producerIds.add(artifact.producer.reference_id);
    const result = await expectCreationEvidenceResult(
      api.inspectCreationArtifact({
        ...authorityParams(census),
        artifactId: artifact.artifact_id,
      }),
      "creation_artifact.inspect",
    );
    if (!isInspectionShape(result)) {
      throw materializationError("artifact inspection reply shape is invalid");
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
    const producer = projectCreationJob(result.job, census.authority.workspaceId);
    if (producer === null || producer.job_id !== producerId) {
      throw materializationError(`producer job ${producerId} is invalid or mismatched`);
    }
    producerJobs.set(producerId, producer);
  }

  const derived = deriveMaterializationPipelineCandidates(
    census,
    inspections,
    producerJobs,
    grants,
  );
  const durableJobs = await loadDurableMaterializationJobs(api, census);
  const pendingJobs = durableJobs.filter(
    (job) => job.state === "queued" || job.state === "running",
  );
  const blockingReasonCodes = new Set<string>();
  for (const job of durableJobs) {
    if (job.cleanupPending) blockingReasonCodes.add("cleanup_pending");
    if (job.recoveryRequired || job.state === "orphaned") {
      blockingReasonCodes.add("recovery_required");
    }
  }
  if (pendingJobs.length > 1) {
    blockingReasonCodes.add("ambiguous_pending_materialization_jobs");
  }
  const boundGrantJobIds = bindDurableTargetGrants(grants, durableJobs, census);
  for (const job of pendingJobs) {
    const targetGrantId = operationParameters(job).target_grant_id;
    if (
      typeof targetGrantId !== "string" ||
      boundGrantJobIds.get(targetGrantId) !== job.job_id
    ) {
      throw materializationError(
        `pending job ${job.job_id} has no exact reserved target grant binding`,
      );
    }
  }
  return {
    ...derived,
    blockingReasonCodes: [...blockingReasonCodes].sort(compareUtf8),
    pendingJobs,
    boundGrantJobIds,
  };
}

export function materializationBundleBuildSubmission(
  census: CreationExecutionCensus,
  candidate: RuntimeBundleMaterializationCandidate,
  targetGrant: StudioCreationOutputGrant,
): StudioCreationMaterializationBundleBuildParams {
  requireReadyTarget(census, targetGrant, 3, "game_materialization_bundle_directory");
  return {
    ...authorityParams(census),
    runtimeBundleArtifactId: candidate.artifactId,
    sourceGrantId: candidate.sourceGrantId,
    expectedSourceGrantGeneration: candidate.sourceGrantGeneration,
    targetGrantId: targetGrant.grant_id,
    expectedTargetGrantGeneration: targetGrant.generation,
  };
}

export function gameMaterializeSubmission(
  census: CreationExecutionCensus,
  candidate: MaterializationBundleCandidate,
  targetGrant: StudioCreationOutputGrant,
): StudioCreationGameMaterializeParams {
  requireReadyTarget(census, targetGrant, 4, "standalone_game_directory");
  return {
    ...authorityParams(census),
    materializationBundleArtifactId: candidate.artifactId,
    sourceGrantId: candidate.sourceGrantId,
    expectedSourceGrantGeneration: candidate.sourceGrantGeneration,
    targetGrantId: targetGrant.grant_id,
    expectedTargetGrantGeneration: targetGrant.generation,
  };
}

export function gamePackageSubmission(
  census: CreationExecutionCensus,
  candidate: StandaloneMaterializationCandidate,
  targetGrant: StudioCreationOutputGrant,
): StudioCreationGamePackageParams {
  requireReadyTarget(census, targetGrant, 5, "game_package_file");
  return {
    ...authorityParams(census),
    standaloneGameArtifactId: candidate.artifactId,
    sourceGrantId: candidate.sourceGrantId,
    expectedSourceGrantGeneration: candidate.sourceGrantGeneration,
    targetGrantId: targetGrant.grant_id,
    expectedTargetGrantGeneration: targetGrant.generation,
  };
}

export function gamePackageExtractSubmission(
  census: CreationExecutionCensus,
  candidate: PackageMaterializationCandidate,
  targetGrant: StudioCreationOutputGrant,
): StudioCreationGamePackageExtractParams {
  requireReadyTarget(census, targetGrant, 4, "standalone_game_directory");
  return {
    ...authorityParams(census),
    gamePackageArtifactId: candidate.artifactId,
    sourceGrantId: candidate.sourceGrantId,
    expectedSourceGrantGeneration: candidate.sourceGrantGeneration,
    targetGrantId: targetGrant.grant_id,
    expectedTargetGrantGeneration: targetGrant.generation,
  };
}

export function hasExactMaterializationOperationParams(
  job: CreationJobView,
  operation: MaterializationOperation,
  submission:
    | StudioCreationMaterializationBundleBuildParams
    | StudioCreationGameMaterializeParams
    | StudioCreationGamePackageParams
    | StudioCreationGamePackageExtractParams,
): boolean {
  return (
    job.operation === operation &&
    sameJson(
      operationParameters(job),
      operationParamsFromSubmission(operation, submission),
    )
  );
}

export async function findIdenticalPendingMaterializationJob(
  api: ForgeStudioApi,
  authority: CreationExecutionAuthority,
  operation: MaterializationOperation,
  submission:
    | StudioCreationMaterializationBundleBuildParams
    | StudioCreationGameMaterializeParams
    | StudioCreationGamePackageParams
    | StudioCreationGamePackageExtractParams,
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
            throw materializationError(
              "multiple identical materialization jobs are in flight",
            );
          }
          match = exact;
        }
      }
      if (page.nextSequence === null) break;
      if (cursors.has(page.nextSequence)) {
        throw materializationError("pending job cursor repeats");
      }
      cursors.add(page.nextSequence);
      afterSequence = page.nextSequence;
      if (pageIndex === MAX_JOB_PAGES - 1) {
        throw materializationError("pending jobs exceed the bounded page limit");
      }
    }
  }
  return match;
}

function deriveRuntimeBundleCandidate(
  artifact: StudioCreationArtifact,
  census: CreationExecutionCensus,
  projections: ReadonlyMap<string, ValidatedInspection>,
  producerJobs: ReadonlyMap<string, CreationJobView>,
  grants: readonly StudioCreationOutputGrant[],
  usedPublishedGrantIds: Set<string>,
): RuntimeBundleMaterializationCandidate {
  const projection = requireProjection(projections, artifact.artifact_id);
  const dependencyFormats = [
    "world-forge.gamepack",
    "world-forge.game_runtime_snapshot",
    "world-forge.runtime_adapter_registry",
    "world-forge.game_runtime_composition",
    "world-forge.runtime_support_report",
    "world-forge.asset_manifest",
  ];
  const dependencies = requireExactDependencyFormats(
    projection,
    census,
    dependencyFormats,
    "runtime bundle",
  );
  const gamepack = dependencies.get("world-forge.gamepack")!;
  const job = requireProducerJob(
    artifact,
    census,
    producerJobs,
    "runtime.bundle.build",
  );
  const params = operationParameters(job);
  requireExactKeys(params, [
    "asset_inventory_artifact_id",
    "assetpack_artifact_id",
    "gamepack_artifact_id",
    "runtime_adapter_registry_artifact_id",
    "runtime_composition_artifact_id",
    "runtime_snapshot_artifact_id",
    "runtime_support_report_artifact_id",
    "source_grant_generation",
    "source_grant_id",
    "target_grant_generation",
    "target_grant_id",
  ], "runtime bundle producer parameters");
  const expectedInputIds = [
    params.gamepack_artifact_id,
    params.asset_inventory_artifact_id,
    params.assetpack_artifact_id,
    params.runtime_snapshot_artifact_id,
    params.runtime_adapter_registry_artifact_id,
    params.runtime_composition_artifact_id,
    params.runtime_support_report_artifact_id,
  ];
  if (
    params.gamepack_artifact_id !== gamepack.artifact_id ||
    params.runtime_snapshot_artifact_id !==
      dependencies.get("world-forge.game_runtime_snapshot")?.artifact_id ||
    params.runtime_adapter_registry_artifact_id !==
      dependencies.get("world-forge.runtime_adapter_registry")?.artifact_id ||
    params.runtime_composition_artifact_id !==
      dependencies.get("world-forge.game_runtime_composition")?.artifact_id ||
    params.runtime_support_report_artifact_id !==
      dependencies.get("world-forge.runtime_support_report")?.artifact_id
  ) {
    throw materializationError("runtime bundle producer parameters cross exact lineage");
  }
  requireExactJobInputs(job, expectedInputIds, census, "runtime bundle");
  requirePublishedAssetpackSource(params, census, grants);
  const published = requirePublishedOutput(
    artifact,
    job,
    grants,
    2,
    "game_runtime_bundle_directory",
    "runtime_bundle",
  );
  usedPublishedGrantIds.add(published.grant.grant_id);
  const treeHash = requireHashField(published.identity, "tree_hash", "runtime bundle");
  return {
    key: candidateKey(artifact, published.grant),
    artifactId: artifact.artifact_id,
    producerJobId: job.job_id,
    predecessorArtifactId: null,
    sourceGrantId: published.grant.grant_id,
    sourceGrantGeneration: published.grant.generation,
    gamepackArtifactId: gamepack.artifact_id,
    gamepackContentHash: gamepack.subject.content_hash,
    contentHash: artifact.subject.content_hash,
    treeHash,
  };
}

function requirePublishedAssetpackSource(
  params: Record<string, unknown>,
  census: CreationExecutionCensus,
  grants: readonly StudioCreationOutputGrant[],
): void {
  const assetpack =
    typeof params.assetpack_artifact_id === "string"
      ? census.selectableById.get(params.assetpack_artifact_id)
      : undefined;
  const inventory =
    typeof params.asset_inventory_artifact_id === "string"
      ? census.selectableById.get(params.asset_inventory_artifact_id)
      : undefined;
  const matches = grants.filter(
    (grant) => grant.grant_id === params.source_grant_id,
  );
  const grant = matches[0];
  if (
    assetpack?.subject.format !== "world-forge.assetpack" ||
    inventory?.subject.format !== "world-forge.asset_inventory" ||
    matches.length !== 1 ||
    grant.workspace_id !== census.authority.workspaceId ||
    grant.format_version !== 1 ||
    grant.kind !== "generic_assetpack_directory" ||
    grant.state !== "published" ||
    grant.generation !== params.source_grant_generation ||
    !isRecord(grant.publication) ||
    grant.publication.inventory_hash !== inventory.subject.content_hash ||
    !sameSubject(grant.publication, assetpack.subject)
  ) {
    throw materializationError(
      "runtime bundle assetpack source grant publication changed",
    );
  }
}

function deriveMaterializationBundleCandidate(
  artifact: StudioCreationArtifact,
  census: CreationExecutionCensus,
  projections: ReadonlyMap<string, ValidatedInspection>,
  producerJobs: ReadonlyMap<string, CreationJobView>,
  grants: readonly StudioCreationOutputGrant[],
  predecessors: readonly RuntimeBundleMaterializationCandidate[],
  usedPublishedGrantIds: Set<string>,
): MaterializationBundleCandidate {
  const predecessor = deriveLinearPredecessor(
    artifact,
    census,
    projections,
    producerJobs,
    "game.materialization.bundle.build",
    "runtime_bundle_artifact_id",
    predecessors,
    "materialization bundle",
  );
  const job = requireProducerJob(
    artifact,
    census,
    producerJobs,
    "game.materialization.bundle.build",
  );
  const published = requirePublishedOutput(
    artifact,
    job,
    grants,
    3,
    "game_materialization_bundle_directory",
    "materialization_bundle",
  );
  usedPublishedGrantIds.add(published.grant.grant_id);
  return {
    key: candidateKey(artifact, published.grant),
    artifactId: artifact.artifact_id,
    producerJobId: job.job_id,
    predecessorArtifactId: predecessor.artifactId,
    sourceGrantId: published.grant.grant_id,
    sourceGrantGeneration: published.grant.generation,
    gamepackArtifactId: predecessor.gamepackArtifactId,
    gamepackContentHash: predecessor.gamepackContentHash,
    contentHash: artifact.subject.content_hash,
    treeHash: requireHashField(
      published.identity,
      "tree_hash",
      "materialization bundle",
    ),
  };
}

function deriveStandaloneCandidate(
  artifact: StudioCreationArtifact,
  census: CreationExecutionCensus,
  projections: ReadonlyMap<string, ValidatedInspection>,
  producerJobs: ReadonlyMap<string, CreationJobView>,
  grants: readonly StudioCreationOutputGrant[],
  predecessors: readonly MaterializationBundleCandidate[],
  usedPublishedGrantIds: Set<string>,
): StandaloneMaterializationCandidate {
  const predecessor = deriveLinearPredecessor(
    artifact,
    census,
    projections,
    producerJobs,
    "game.materialize",
    "materialization_bundle_artifact_id",
    predecessors,
    "standalone game",
  );
  const job = requireProducerJob(artifact, census, producerJobs, "game.materialize");
  const published = requirePublishedOutput(
    artifact,
    job,
    grants,
    4,
    "standalone_game_directory",
    "standalone_game",
  );
  usedPublishedGrantIds.add(published.grant.grant_id);
  const treeHash = requireHashField(published.identity, "tree_hash", "standalone game");
  return {
    key: candidateKey(artifact, published.grant),
    artifactId: artifact.artifact_id,
    producerJobId: job.job_id,
    predecessorArtifactId: predecessor.artifactId,
    sourceGrantId: published.grant.grant_id,
    sourceGrantGeneration: published.grant.generation,
    gamepackArtifactId: predecessor.gamepackArtifactId,
    gamepackContentHash: predecessor.gamepackContentHash,
    contentHash: artifact.subject.content_hash,
    treeHash,
    preservedStandaloneArtifactId: artifact.artifact_id,
    preservedStandaloneSubjectId: artifact.subject.id,
    preservedStandaloneContentHash: artifact.subject.content_hash,
    preservedStandaloneTreeHash: treeHash,
  };
}

function derivePackageCandidate(
  artifact: StudioCreationArtifact,
  census: CreationExecutionCensus,
  projections: ReadonlyMap<string, ValidatedInspection>,
  producerJobs: ReadonlyMap<string, CreationJobView>,
  grants: readonly StudioCreationOutputGrant[],
  predecessors: readonly StandaloneMaterializationCandidate[],
  usedPublishedGrantIds: Set<string>,
): PackageMaterializationCandidate {
  const predecessor = deriveLinearPredecessor(
    artifact,
    census,
    projections,
    producerJobs,
    "game.package",
    "standalone_game_artifact_id",
    predecessors,
    "game package",
    true,
  );
  const job = requireProducerJob(artifact, census, producerJobs, "game.package");
  const published = requirePublishedOutput(
    artifact,
    job,
    grants,
    5,
    "game_package_file",
    "game_package",
  );
  usedPublishedGrantIds.add(published.grant.grant_id);
  const sizeBytes = published.identity.size_bytes;
  if (!Number.isSafeInteger(sizeBytes) || Number(sizeBytes) < 1) {
    throw materializationError("game package size evidence is invalid");
  }
  return {
    key: candidateKey(artifact, published.grant),
    artifactId: artifact.artifact_id,
    producerJobId: job.job_id,
    predecessorArtifactId: predecessor.artifactId,
    sourceGrantId: published.grant.grant_id,
    sourceGrantGeneration: published.grant.generation,
    gamepackArtifactId: predecessor.gamepackArtifactId,
    gamepackContentHash: predecessor.gamepackContentHash,
    contentHash: artifact.subject.content_hash,
    archiveSha256: requireHashField(
      published.identity,
      "archive_sha256",
      "game package",
    ),
    sizeBytes: Number(sizeBytes),
    preservedStandaloneArtifactId: predecessor.preservedStandaloneArtifactId,
    preservedStandaloneSubjectId: predecessor.preservedStandaloneSubjectId,
    preservedStandaloneContentHash: predecessor.preservedStandaloneContentHash,
    preservedStandaloneTreeHash: predecessor.preservedStandaloneTreeHash,
  };
}

function deriveExtractionCandidate(
  artifact: StudioCreationArtifact,
  census: CreationExecutionCensus,
  projections: ReadonlyMap<string, ValidatedInspection>,
  producerJobs: ReadonlyMap<string, CreationJobView>,
  grants: readonly StudioCreationOutputGrant[],
  predecessors: readonly PackageMaterializationCandidate[],
  usedPublishedGrantIds: Set<string>,
): ExtractionMaterializationCandidate {
  const predecessor = deriveLinearPredecessor(
    artifact,
    census,
    projections,
    producerJobs,
    "game.package.extract",
    "game_package_artifact_id",
    predecessors,
    "game package extraction",
  );
  const job = requireProducerJob(
    artifact,
    census,
    producerJobs,
    "game.package.extract",
  );
  const published = requirePublishedOutput(
    artifact,
    job,
    grants,
    4,
    "standalone_game_directory",
    "standalone_game",
    false,
  );
  const treeHash = requireHashField(
    published.identity,
    "tree_hash",
    "game package extraction",
  );
  if (
    published.identity.format !== "world-forge.standalone_game" ||
    published.identity.format_version !== 1 ||
    published.identity.id !== predecessor.preservedStandaloneSubjectId ||
    published.identity.content_hash !== predecessor.preservedStandaloneContentHash ||
    treeHash !== predecessor.preservedStandaloneTreeHash
  ) {
    throw materializationError(
      "game package extraction did not preserve the standalone identity",
    );
  }
  usedPublishedGrantIds.add(published.grant.grant_id);
  return {
    key: [
      artifact.artifact_id,
      published.grant.grant_id,
      String(published.grant.generation),
    ].join("\u0000"),
    artifactId: artifact.artifact_id,
    producerJobId: job.job_id,
    predecessorArtifactId: predecessor.artifactId,
    publishedStandaloneGrantId: published.grant.grant_id,
    publishedStandaloneGrantGeneration: published.grant.generation,
    preservedStandaloneArtifactId: predecessor.preservedStandaloneArtifactId,
    preservedStandaloneSubjectId: predecessor.preservedStandaloneSubjectId,
    preservedStandaloneContentHash: predecessor.preservedStandaloneContentHash,
    preservedStandaloneTreeHash: predecessor.preservedStandaloneTreeHash,
    gamepackArtifactId: predecessor.gamepackArtifactId,
    gamepackContentHash: predecessor.gamepackContentHash,
    contentHash: artifact.subject.content_hash,
  };
}

function deriveLinearPredecessor<T extends MaterializationCandidateBase>(
  artifact: StudioCreationArtifact,
  census: CreationExecutionCensus,
  projections: ReadonlyMap<string, ValidatedInspection>,
  producerJobs: ReadonlyMap<string, CreationJobView>,
  operation: MaterializationOperation,
  predecessorParam: string,
  predecessors: readonly T[],
  context: string,
  artifactHasNoDirectDependency = false,
): T {
  const projection = requireProjection(projections, artifact.artifact_id);
  const job = requireProducerJob(artifact, census, producerJobs, operation);
  const params = operationParameters(job);
  requireExactKeys(
    params,
    [
      predecessorParam,
      "source_grant_generation",
      "source_grant_id",
      "target_grant_generation",
      "target_grant_id",
    ],
    `${context} producer parameters`,
  );
  const matches = predecessors.filter(
    (candidate) =>
      candidate.artifactId === params[predecessorParam] &&
      candidate.sourceGrantId === params.source_grant_id &&
      candidate.sourceGrantGeneration === params.source_grant_generation,
  );
  if (matches.length !== 1) {
    throw materializationError(
      `${context} producer parameters do not select one exact predecessor publication`,
    );
  }
  const predecessor = matches[0];
  requireExactJobInputs(job, [predecessor.artifactId], census, context);
  if (artifactHasNoDirectDependency) {
    requireExactDependencies(projection, [], context);
  } else {
    requireExactDependencies(projection, [predecessor.artifactId], context);
  }
  return predecessor;
}

function requireProducerJob(
  artifact: StudioCreationArtifact,
  census: CreationExecutionCensus,
  producerJobs: ReadonlyMap<string, CreationJobView>,
  operation: CreationJobView["operation"],
): CreationJobView {
  requireFutureCandidate(artifact);
  const job = producerJobs.get(artifact.producer.reference_id);
  if (
    !job ||
    !matchesPublicAuthority(job, census) ||
    job.operation !== operation ||
    job.state !== "succeeded" ||
    job.progress !== "committed" ||
    job.cleanupPending ||
    job.recoveryRequired ||
    job.analysisStatus !== "passed" ||
    job.record.result === null ||
    job.record.result.cleanup_pending ||
    job.record.result.output_artifact_ids.length !== 1 ||
    job.record.result.output_artifact_ids[0] !== artifact.artifact_id
  ) {
    throw materializationError(
      `${operation} producer is not one succeeded, committed exact-output job`,
    );
  }
  return job;
}

function requirePublishedOutput(
  artifact: StudioCreationArtifact,
  job: CreationJobView,
  grants: readonly StudioCreationOutputGrant[],
  formatVersion: 2 | 3 | 4 | 5,
  kind: StudioCreationOutputGrant["kind"],
  identityKey: string,
  requireArtifactIdentity = true,
): { grant: StudioCreationOutputGrant; identity: Record<string, unknown> } {
  const result = job.record.result as unknown as Record<string, unknown>;
  const publication = result.publication;
  if (!isRecord(publication) || !isRecord(publication[identityKey])) {
    throw materializationError(`${job.operation} publication evidence is unavailable`);
  }
  const grantId = publication.grant_id;
  const generation = publication.grant_generation;
  const matches = grants.filter((grant) => grant.grant_id === grantId);
  if (
    matches.length !== 1 ||
    typeof grantId !== "string" ||
    !Number.isSafeInteger(generation) ||
    publication.kind !== kind ||
    publication.state !== "published"
  ) {
    throw materializationError(`${job.operation} publication authority is ambiguous`);
  }
  const grant = matches[0];
  const identity = publication[identityKey];
  const params = operationParameters(job);
  if (
    grant.workspace_id !== job.workspace_id ||
    grant.format_version !== formatVersion ||
    grant.kind !== kind ||
    grant.state !== "published" ||
    grant.generation !== generation ||
    params.target_grant_id !== grantId ||
    params.target_grant_generation !== generation ||
    !sameJson(grant.publication, identity) ||
    (requireArtifactIdentity && !sameSubject(identity, artifact.subject))
  ) {
    throw materializationError(`${job.operation} publication or hash identity changed`);
  }
  return { grant, identity };
}

function validateInspection(
  census: CreationExecutionCensus,
  expected: StudioCreationArtifact,
  result: StudioCreationArtifactInspectResult,
): ValidatedInspection {
  if (expected.lifecycle !== "candidate") {
    throw materializationError(
      `${expected.artifact_id} is not a current candidate lifecycle`,
    );
  }
  if (
    result.artifact_snapshot_hash !== census.authority.artifactSnapshotHash ||
    !matchesAuthorityRecord(result.authority, census) ||
    !matchesAuthorityRecord(result.artifact.authority, census) ||
    result.artifact.artifact_id !== expected.artifact_id ||
    result.artifact.record_hash !== expected.record_hash ||
    result.artifact.lifecycle !== "candidate" ||
    !sameSubject(result.artifact.subject, expected.subject) ||
    !sameJson(result.artifact.producer, expected.producer) ||
    result.artifact.references.dependency_count !== expected.references.dependency_count
  ) {
    throw materializationError(
      `${expected.artifact_id} inspection authority, lifecycle, or record changed`,
    );
  }
  const lineage = result.projection.lineage;
  if (
    !Array.isArray(lineage) ||
    lineage.length !== expected.references.dependency_count ||
    lineage.length > MAX_LINEAGE
  ) {
    throw materializationError(
      `${expected.artifact_id} dependency projection is truncated or mismatched`,
    );
  }
  const dependencies = new Set<string>();
  for (const edge of lineage) {
    const dependency = census.selectableById.get(edge.artifact_id);
    if (
      edge.relation !== "depends_on" ||
      !dependency ||
      edge.lifecycle !== dependency.lifecycle ||
      (dependency.lifecycle !== "active" && dependency.lifecycle !== "candidate") ||
      dependencies.has(edge.artifact_id)
    ) {
      throw materializationError(
        `${expected.artifact_id} dependency projection is stale or ambiguous`,
      );
    }
    dependencies.add(edge.artifact_id);
  }
  return {
    artifact: expected,
    dependencies: [...dependencies].sort(compareUtf8),
  };
}

function requireExactDependencyFormats(
  projection: ValidatedInspection,
  census: CreationExecutionCensus,
  expectedFormats: readonly string[],
  context: string,
): ReadonlyMap<string, StudioCreationArtifact> {
  if (projection.dependencies.length !== expectedFormats.length) {
    throw materializationError(`${context} exact dependency closure changed`);
  }
  const result = new Map<string, StudioCreationArtifact>();
  for (const format of expectedFormats) {
    const matches = projection.dependencies
      .map((artifactId) => census.selectableById.get(artifactId))
      .filter((artifact): artifact is StudioCreationArtifact =>
        artifact?.subject.format === format,
      );
    if (matches.length !== 1) {
      throw materializationError(`${context} has missing or ambiguous ${format} lineage`);
    }
    result.set(format, matches[0]);
  }
  return result;
}

function requireExactDependencies(
  projection: ValidatedInspection,
  expected: readonly string[],
  context: string,
): void {
  if (!sameStringArray(projection.dependencies, [...expected].sort(compareUtf8))) {
    throw materializationError(`${context} exact dependency lineage changed`);
  }
}

function requireExactJobInputs(
  job: CreationJobView,
  expectedIds: readonly unknown[],
  census: CreationExecutionCensus,
  context: string,
): void {
  if (
    expectedIds.some((value) => typeof value !== "string") ||
    new Set(expectedIds).size !== expectedIds.length ||
    job.record.inputs.length !== expectedIds.length ||
    job.record.inputs.some((input, index) => {
      const expectedId = expectedIds[index] as string;
      const artifact = census.selectableById.get(expectedId);
      return (
        !artifact ||
        input.artifact_id !== expectedId ||
        !sameSubject(input.subject, artifact.subject)
      );
    })
  ) {
    throw materializationError(`${context} producer inputs are incomplete or mixed`);
  }
}

async function loadDurableMaterializationJobs(
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
    for (const listed of page.jobs) {
      if (seen.has(listed.job_id)) {
        throw materializationError("durable job census repeats an identity");
      }
      seen.add(listed.job_id);
      const exact = projectCreationJob(
        listed.record,
        census.authority.workspaceId,
        census.authority,
      );
      if (
        exact !== null &&
        isMaterializationOperation(exact.operation) &&
        (exact.state === "queued" ||
          exact.state === "running" ||
          exact.state === "orphaned" ||
          exact.cleanupPending ||
          exact.recoveryRequired)
      ) {
        retained.push(exact);
      }
    }
    if (page.nextSequence === null) return retained;
    if (cursors.has(page.nextSequence)) {
      throw materializationError("durable job cursor repeats");
    }
    cursors.add(page.nextSequence);
    afterSequence = page.nextSequence;
  }
  throw materializationError("durable job census exceeds the bounded page limit");
}

function bindDurableTargetGrants(
  grants: readonly StudioCreationOutputGrant[],
  jobs: readonly CreationJobView[],
  census: CreationExecutionCensus,
): ReadonlyMap<string, string> {
  const bindings = new Map<string, string>();
  const usedJobs = new Set<string>();
  for (const grant of grants) {
    if (
      grant.workspace_id !== census.authority.workspaceId ||
      grant.format_version < 3 ||
      grant.format_version > 5 ||
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
        isMaterializationOperation(job.operation) &&
        expectedStates.has(job.state) &&
        params.target_grant_id === grant.grant_id &&
        params.target_grant_generation === expectedGeneration &&
        operationAcceptsGrant(job.operation, grant)
      );
    });
    if (matches.length !== 1 || usedJobs.has(matches[0]?.job_id ?? "")) {
      throw materializationError(
        `${grant.state} grant ${grant.grant_id} has no unambiguous durable job binding`,
      );
    }
    usedJobs.add(matches[0].job_id);
    bindings.set(grant.grant_id, matches[0].job_id);
  }
  return bindings;
}

function operationAcceptsGrant(
  operation: MaterializationOperation,
  grant: StudioCreationOutputGrant,
): boolean {
  if (operation === "game.materialization.bundle.build") {
    return grant.format_version === 3 && grant.kind === "game_materialization_bundle_directory";
  }
  if (operation === "game.materialize" || operation === "game.package.extract") {
    return grant.format_version === 4 && grant.kind === "standalone_game_directory";
  }
  return grant.format_version === 5 && grant.kind === "game_package_file";
}

function operationParamsFromSubmission(
  operation: MaterializationOperation,
  submission:
    | StudioCreationMaterializationBundleBuildParams
    | StudioCreationGameMaterializeParams
    | StudioCreationGamePackageParams
    | StudioCreationGamePackageExtractParams,
): Record<string, unknown> {
  const common = {
    source_grant_id: submission.sourceGrantId,
    source_grant_generation: submission.expectedSourceGrantGeneration,
    target_grant_id: submission.targetGrantId,
    target_grant_generation: nextGeneration(submission.expectedTargetGrantGeneration),
  };
  if (operation === "game.materialization.bundle.build") {
    return {
      runtime_bundle_artifact_id: (
        submission as StudioCreationMaterializationBundleBuildParams
      ).runtimeBundleArtifactId,
      ...common,
    };
  }
  if (operation === "game.materialize") {
    return {
      ...common,
      materialization_bundle_artifact_id: (
        submission as StudioCreationGameMaterializeParams
      ).materializationBundleArtifactId,
    };
  }
  if (operation === "game.package") {
    return {
      ...common,
      standalone_game_artifact_id: (
        submission as StudioCreationGamePackageParams
      ).standaloneGameArtifactId,
    };
  }
  return {
    ...common,
    game_package_artifact_id: (
      submission as StudioCreationGamePackageExtractParams
    ).gamePackageArtifactId,
  };
}

function requireReadyTarget(
  census: CreationExecutionCensus,
  grant: StudioCreationOutputGrant,
  formatVersion: 3 | 4 | 5,
  kind: StudioCreationOutputGrant["kind"],
): void {
  if (
    grant.workspace_id !== census.authority.workspaceId ||
    grant.format_version !== formatVersion ||
    grant.kind !== kind ||
    grant.state !== "ready" ||
    grant.publication !== null
  ) {
    throw materializationError("target grant authority is not ready");
  }
}

function candidatesByFormat(
  census: CreationExecutionCensus,
  format: string,
): StudioCreationArtifact[] {
  return census.candidateArtifacts
    .filter((artifact) => artifact.subject.format === format)
    .sort((left, right) => compareUtf8(left.artifact_id, right.artifact_id));
}

function requireProjection(
  projections: ReadonlyMap<string, ValidatedInspection>,
  artifactId: string,
): ValidatedInspection {
  const projection = projections.get(artifactId);
  if (!projection) throw materializationError(`inspection is missing for ${artifactId}`);
  return projection;
}

function requireFutureCandidate(artifact: StudioCreationArtifact): asserts artifact is StudioCreationArtifact & {
  producer: { kind: "future_candidate"; phase_id: null; reference_id: string };
} {
  if (
    artifact.lifecycle !== "candidate" ||
    artifact.producer.kind !== "future_candidate" ||
    artifact.producer.phase_id !== null
  ) {
    throw materializationError(
      `${artifact.artifact_id} is not an exact current future candidate`,
    );
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

function operationParameters(job: CreationJobView): Record<string, unknown> {
  const record = job.record as unknown as Record<string, unknown>;
  return isRecord(record.operation_params) ? record.operation_params : {};
}

function requireExactKeys(
  value: Record<string, unknown>,
  keys: readonly string[],
  context: string,
): void {
  const actual = Object.keys(value).sort(compareUtf8);
  const expected = [...keys].sort(compareUtf8);
  if (!sameStringArray(actual, expected)) {
    throw materializationError(`${context} are not closed and exact`);
  }
}

function requireHashField(
  value: Record<string, unknown>,
  field: string,
  context: string,
): string {
  const candidate = value[field];
  if (typeof candidate !== "string" || !/^[0-9a-f]{64}$/u.test(candidate)) {
    throw materializationError(`${context} ${field} hash is unavailable`);
  }
  return candidate;
}

function candidateKey(
  artifact: StudioCreationArtifact,
  grant: StudioCreationOutputGrant,
): string {
  return [artifact.artifact_id, grant.grant_id, String(grant.generation)].join("\u0000");
}

function isMaterializationOperation(
  operation: CreationJobView["operation"],
): operation is MaterializationOperation {
  return PIPELINE_OPERATIONS.includes(operation as MaterializationOperation);
}

function isInspectionShape(value: Record<string, unknown>): boolean {
  return (
    typeof value.artifact_snapshot_hash === "string" &&
    isRecord(value.authority) &&
    isRecord(value.artifact) &&
    isRecord(value.projection) &&
    Array.isArray(value.projection.lineage)
  );
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

function nextGeneration(value: number): number {
  const next = value + 1;
  if (!Number.isSafeInteger(next)) {
    throw materializationError("grant generation cannot advance safely");
  }
  return next;
}

function candidateOrder<T extends { key: string }>(left: T, right: T): number {
  return compareUtf8(left.key, right.key);
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

function materializationError(reason: string): Error {
  return new Error(`Forge Studio materialization pipeline failed closed: ${reason}`);
}

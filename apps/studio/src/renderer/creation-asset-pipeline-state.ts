import {
  MAX_STUDIO_ASSET_ACCEPTANCE_ITEMS,
  validateStudioCreationAssetAcceptanceResults,
  type ForgeStudioApi,
  type StudioCreationArtifact,
  type StudioCreationArtifactInspectResult,
  type StudioCreationAssetAcceptanceResult,
} from "../shared/studio-api";
export const MAX_ASSET_ACCEPTANCE_RESULTS = MAX_STUDIO_ASSET_ACCEPTANCE_ITEMS;
export const MAX_ASSET_ACCEPTANCE_EVIDENCE = MAX_STUDIO_ASSET_ACCEPTANCE_ITEMS;
import { expectCreationEvidenceResult } from "./creation-service";
import {
  listCreationJobPage,
  projectCreationJob,
  type CreationExecutionAuthority,
  type CreationExecutionCensus,
  type CreationJobView,
} from "./creation-execution-state";
import { parseCreationObjectJson } from "./creation-state";

export const MAX_CREATION_ADMISSION_BYTES = 768 * 1024;
export const MAX_ASSET_PIPELINE_CLOSURE = 128;
export const MAX_ASSET_RELEASE_LICENSES = 16;

const SHA256 = /^[0-9a-f]{64}$/u;
const ENTITY_ID = /^[a-z0-9][a-z0-9_-]{0,127}$/u;
const SUFFIX = /^[a-z0-9][a-z0-9_-]{0,31}$/u;

const PROCESS_REQUIRED_FORMATS = [
  "world-forge.gamepack",
  "world-forge.asset_subject",
  "world-forge.asset_target",
  "world-forge.asset_style",
  "world-forge.asset_inventory",
  "world-forge.asset_spec",
  "world-forge.asset_production_request",
  "world-forge.asset_production_receipt",
  "world-forge.asset_selection",
  "world-forge.asset_provenance_record",
] as const;

const RELEASE_REQUIRED_FORMATS = [
  ...PROCESS_REQUIRED_FORMATS,
  "world-forge.asset_processing_recipe",
  "world-forge.asset_processing_receipt",
] as const;

export interface AssetAcceptanceDraft {
  criterionSha256: string;
  status: "failed" | "passed";
  evidenceHashes: string;
}

export interface AssetProcessingGroup {
  key: string;
  assetId: string;
  licenseArtifactIds: string[];
  lifecycle: "active" | "candidate";
  anchorArtifactIds: string[];
}

export interface AssetReleaseGroup {
  key: string;
  inventoryAssetCount: number;
  qaReportArtifactIds: string[];
  lifecycle: "active" | "candidate";
  anchorArtifactIds: string[];
}

export interface AssetQaReviewGroup {
  key: string;
  qaReportArtifactId: string;
  assetId: string;
  status: "pending" | "blocked";
  blockerCount: number;
  outputRole: "primary";
}

export interface AssetPipelineCandidates {
  processingGroups: AssetProcessingGroup[];
  releaseGroups: AssetReleaseGroup[];
  qaReviewGroups: AssetQaReviewGroup[];
}

export interface AssetPipelineBlockingState {
  jobIds: string[];
  reasonCodes: string[];
}

interface ValidatedProjection {
  artifact: StudioCreationArtifact;
  dependencies: string[];
  facts: ReadonlyMap<string, string | number | boolean | null | string[]>;
  status: string | null;
}

export function parseCreationAdmissionDocument(source: string): Record<string, unknown> {
  return parseCreationObjectJson(source, "Artifact admission", MAX_CREATION_ADMISSION_BYTES);
}

export function canonicalizeArtifactDependencies(
  artifactIds: readonly string[],
  census: CreationExecutionCensus,
): string[] {
  if (artifactIds.length > MAX_ASSET_PIPELINE_CLOSURE) {
    throw new Error("Artifact admission dependency selection exceeds the bounded closure");
  }
  const unique = new Set<string>();
  for (const artifactId of artifactIds) {
    if (unique.has(artifactId)) throw new Error("Artifact admission dependency selection contains a duplicate");
    if (!census.selectableById.has(artifactId)) {
      throw new Error("Artifact admission dependency is not in the current active or candidate census");
    }
    unique.add(artifactId);
  }
  return [...unique].sort(compareUtf8);
}

export function canonicalAssetOutputIds(
  assetId: string,
  requestedSuffix: string,
): {
  recipeId: string;
  processingReceiptId: string;
  qaReportId: string;
} {
  requireEntityId(assetId, "asset ID");
  const suffix = requestedSuffix.trim() || "studio";
  if (!SUFFIX.test(suffix)) {
    throw new Error("Asset processing suffix must be a lowercase portable identifier of at most 32 characters");
  }
  const result = {
    recipeId: `${assetId}_${suffix}_recipe`,
    processingReceiptId: `${assetId}_${suffix}_processing_receipt`,
    qaReportId: `${assetId}_${suffix}_qa_report`,
  };
  for (const [field, value] of Object.entries(result)) requireEntityId(value, field);
  return result;
}

export function assertAssetOutputIdsAvailable(
  ids: ReturnType<typeof canonicalAssetOutputIds>,
  census: CreationExecutionCensus,
): void {
  const expected = [
    ["world-forge.asset_processing_recipe", ids.recipeId],
    ["world-forge.asset_processing_receipt", ids.processingReceiptId],
    ["world-forge.asset_qa_report", ids.qaReportId],
  ] as const;
  for (const [format, id] of expected) {
    if (
      census.selectableArtifacts.some(
        (artifact) => artifact.subject.format === format && artifact.subject.id === id,
      )
    ) {
      throw new Error(`Asset processing output ${format} ${id} already exists; choose a new suffix`);
    }
  }
}

export function canonicalAssetReleaseManifestId(
  projectId: string,
  qaArtifacts: readonly StudioCreationArtifact[],
): string {
  requireEntityId(projectId, "project ID");
  if (qaArtifacts.length < 1 || qaArtifacts.length > MAX_ASSET_PIPELINE_CLOSURE) {
    throw new Error("Asset release QA selection is empty or exceeds the bounded closure");
  }
  const ordered = [...qaArtifacts].sort((left, right) => compareUtf8(left.artifact_id, right.artifact_id));
  const first = ordered[0].subject.content_hash;
  const last = ordered.at(-1)!.subject.content_hash;
  if (!SHA256.test(first) || !SHA256.test(last)) throw new Error("Asset release QA hash is invalid");
  const prefixBudget = 128 - "_assetpack_000_".length - 16 - 1 - 16;
  const manifestId = `${projectId.slice(0, prefixBudget)}_assetpack_${String(ordered.length)}_${first.slice(0, 16)}_${last.slice(-16)}`;
  requireEntityId(manifestId, "asset release manifest ID");
  return manifestId;
}

export function normalizeAcceptanceResults(
  rows: readonly AssetAcceptanceDraft[],
): StudioCreationAssetAcceptanceResult[] {
  if (rows.length < 1 || rows.length > MAX_ASSET_ACCEPTANCE_RESULTS) {
    throw new Error(
      `Asset acceptance results must contain between 1 and ${String(MAX_ASSET_ACCEPTANCE_RESULTS)} positional rows`,
    );
  }
  const criteria = new Set<string>();
  const normalized = rows.map((row, criterionIndex) => {
    const criterionSha256 = row.criterionSha256.trim();
    if (!SHA256.test(criterionSha256)) {
      throw new Error(`Acceptance criterion ${String(criterionIndex + 1)} must use a lowercase canonical SHA-256 value`);
    }
    if (criteria.has(criterionSha256)) throw new Error("Asset acceptance criterion hashes repeat");
    criteria.add(criterionSha256);
    if (row.status !== "passed" && row.status !== "failed") {
      throw new Error(`Acceptance criterion ${String(criterionIndex + 1)} has an invalid status`);
    }
    const rawEvidence = row.evidenceHashes
      .split(/[\s,]+/u)
      .map((item) => item.trim())
      .filter(Boolean);
    if (rawEvidence.length < 1 || rawEvidence.length > MAX_ASSET_ACCEPTANCE_EVIDENCE) {
      throw new Error(`Acceptance criterion ${String(criterionIndex + 1)} requires between 1 and 64 evidence hashes`);
    }
    if (rawEvidence.some((digest) => !SHA256.test(digest))) {
      throw new Error(`Acceptance criterion ${String(criterionIndex + 1)} evidence must use lowercase canonical SHA-256 values`);
    }
    const evidenceHashes = [...new Set(rawEvidence)].sort(compareUtf8);
    if (evidenceHashes.length !== rawEvidence.length) {
      throw new Error("Asset acceptance evidence hashes repeat");
    }
    return {
      criterionIndex,
      criterionSha256,
      status: row.status,
      evidenceHashes,
    };
  });
  return validateStudioCreationAssetAcceptanceResults(normalized);
}

export function deriveAssetPipelineCandidates(
  census: CreationExecutionCensus,
  inspections: ReadonlyMap<string, StudioCreationArtifactInspectResult>,
): AssetPipelineCandidates {
  const projections = new Map<string, ValidatedProjection>();
  for (const [artifactId, result] of inspections) {
    projections.set(artifactId, validateInspection(census, artifactId, result));
  }

  const licenseStarts = census.selectableArtifacts.filter(
    (artifact) => artifact.subject.format === "world-forge.asset_license_record",
  );
  const processingByKey = new Map<
    string,
    AssetProcessingGroup & {
      outputCount: number;
      selectedBindings: string[];
      licenseByBinding: Map<string, string>;
    }
  >();
  for (const license of licenseStarts) {
    const closure = currentClosure(license.artifact_id, census, projections);
    assertExactStartingArtifact(
      closure,
      license.artifact_id,
      "world-forge.asset_license_record",
      "Asset processing",
    );
    const anchors = exactFormats(closure, PROCESS_REQUIRED_FORMATS, "Asset processing");
    const assetId = stringFact(projections.get(license.artifact_id)!, "asset_id", "Asset license");
    requireEntityId(assetId, "asset ID");
    const outputCount = positiveIntegerFact(
      projections.get(anchors.get("world-forge.asset_production_receipt")!)!,
      "output_count",
      "Asset production receipt",
    );
    const selectedBindings = exactSelectedOutputBindings(
      projections.get(anchors.get("world-forge.asset_selection")!)!,
      "Asset selection",
    );
    if (selectedBindings.length !== outputCount) {
      throw new Error("Asset processing selection bindings do not match production outputs");
    }
    const licenseBinding = exactLicenseBinding(
      projections.get(license.artifact_id)!,
      "Asset license",
    );
    if (!selectedBindings.includes(licenseBinding)) {
      throw new Error("Asset processing license binding crosses or exceeds the exact selection");
    }
    const anchorArtifactIds = PROCESS_REQUIRED_FORMATS.map((format) => anchors.get(format)!);
    const key = [assetId, ...anchorArtifactIds].join("\u0000");
    const existing = processingByKey.get(key);
    if (existing) {
      if (existing.outputCount !== outputCount) throw new Error("Asset processing lineage output count is ambiguous");
      if (!sameStringArray(existing.selectedBindings, selectedBindings)) {
        throw new Error("Asset processing selection binding authority is ambiguous");
      }
      if (existing.licenseByBinding.has(licenseBinding)) {
        throw new Error("Asset processing license binding is duplicated");
      }
      existing.licenseArtifactIds.push(license.artifact_id);
      existing.licenseByBinding.set(licenseBinding, license.artifact_id);
      if (license.lifecycle === "candidate") existing.lifecycle = "candidate";
    } else {
      processingByKey.set(key, {
        key,
        assetId,
        licenseArtifactIds: [license.artifact_id],
        lifecycle: currentLifecycle(license),
        anchorArtifactIds,
        outputCount,
        selectedBindings,
        licenseByBinding: new Map([[licenseBinding, license.artifact_id]]),
      });
    }
  }
  const processingGroups: AssetProcessingGroup[] = [];
  for (const group of processingByKey.values()) {
    group.licenseArtifactIds.sort(compareUtf8);
    if (
      group.licenseArtifactIds.length > group.outputCount ||
      group.licenseByBinding.size > group.selectedBindings.length
    ) {
      throw new Error("Asset processing license closure exceeds its production outputs");
    }
    if (
      group.licenseArtifactIds.length < group.outputCount ||
      group.licenseByBinding.size < group.selectedBindings.length
    ) {
      continue;
    }
    processingGroups.push({
      key: group.key,
      assetId: group.assetId,
      licenseArtifactIds: group.licenseArtifactIds,
      lifecycle: group.lifecycle,
      anchorArtifactIds: group.anchorArtifactIds,
    });
  }

  const qaStarts = census.selectableArtifacts.filter(
    (artifact) => artifact.subject.format === "world-forge.asset_qa_report",
  );
  const qaReviewGroups: AssetQaReviewGroup[] = [];
  const releaseByKey = new Map<
    string,
    AssetReleaseGroup & { assetIds: Set<string>; allPassed: boolean }
  >();
  for (const qa of qaStarts) {
    const closure = currentClosure(qa.artifact_id, census, projections);
    assertExactStartingArtifact(
      closure,
      qa.artifact_id,
      "world-forge.asset_qa_report",
      "Asset release",
    );
    const anchors = exactFormats(closure, RELEASE_REQUIRED_FORMATS, "Asset release");
    const qaProjection = projections.get(qa.artifact_id)!;
    const assetId = stringFact(qaProjection, "asset_id", "Asset QA report");
    requireEntityId(assetId, "asset ID");
    const licenseArtifacts = closure
      .filter((artifact) => artifact.subject.format === "world-forge.asset_license_record")
      .sort((left, right) => compareUtf8(left.artifact_id, right.artifact_id));
    if (licenseArtifacts.length < 1 || licenseArtifacts.length > MAX_ASSET_RELEASE_LICENSES) {
      throw new Error("Asset release license coverage is empty or exceeds its bound");
    }
    const recipeProjection = projections.get(anchors.get("world-forge.asset_processing_recipe")!)!;
    const recipeLicenseArtifactIds = recipeProjection.dependencies
      .filter(
        (artifactId) =>
          census.selectableById.get(artifactId)?.subject.format ===
          "world-forge.asset_license_record",
      )
      .sort(compareUtf8);
    const recipeAnchorArtifactIds = recipeProjection.dependencies
      .filter(
        (artifactId) =>
          census.selectableById.get(artifactId)?.subject.format !==
          "world-forge.asset_license_record",
      )
      .sort(compareUtf8);
    const expectedRecipeAnchorArtifactIds = PROCESS_REQUIRED_FORMATS.map(
      (format) => anchors.get(format)!,
    ).sort(compareUtf8);
    if (
      recipeLicenseArtifactIds.length !== licenseArtifacts.length ||
      recipeLicenseArtifactIds.some(
        (artifactId, index) => artifactId !== licenseArtifacts[index].artifact_id,
      ) ||
      recipeAnchorArtifactIds.length !== expectedRecipeAnchorArtifactIds.length ||
      recipeAnchorArtifactIds.some(
        (artifactId, index) => artifactId !== expectedRecipeAnchorArtifactIds[index],
      )
    ) {
      throw new Error("Asset release recipe license coverage is incomplete or ambiguous");
    }
    const productionOutputCount = positiveIntegerFact(
      projections.get(anchors.get("world-forge.asset_production_receipt")!)!,
      "output_count",
      "Asset production receipt",
    );
    const processingOutputCount = positiveIntegerFact(
      projections.get(anchors.get("world-forge.asset_processing_receipt")!)!,
      "output_count",
      "Asset processing receipt",
    );
    const selectedBindings = exactSelectedOutputBindings(
      projections.get(anchors.get("world-forge.asset_selection")!)!,
      "Asset selection",
    );
    const licenseBindings = licenseArtifacts
      .map((artifact) => exactLicenseBinding(projections.get(artifact.artifact_id)!, "Asset license"))
      .sort(compareUtf8);
    if (
      licenseArtifacts.length !== productionOutputCount ||
      licenseArtifacts.length !== processingOutputCount ||
      !sameStringArray(licenseBindings, selectedBindings)
    ) {
      throw new Error("Asset release license coverage does not match its exact selected outputs");
    }
    for (const licenseArtifact of licenseArtifacts) {
      const licenseProjection = projections.get(licenseArtifact.artifact_id)!;
      if (
        stringFact(licenseProjection, "asset_id", "Asset license") !== assetId ||
        booleanFact(licenseProjection, "commercial_use", "Asset license") !== true ||
        booleanFact(licenseProjection, "redistribution", "Asset license") !== true
      ) {
        throw new Error("Asset release license identity or permissions are not release-ready");
      }
    }
    const blockerCount = nonNegativeIntegerFact(qaProjection, "blocker_count", "Asset QA report");
    qaReviewGroups.push({
      key: qa.artifact_id,
      qaReportArtifactId: qa.artifact_id,
      assetId,
      status: blockerCount === 0 && qaProjection.status === "passed" ? "pending" : "blocked",
      blockerCount,
      outputRole: "primary",
    });
    const inventoryArtifactId = anchors.get("world-forge.asset_inventory")!;
    const inventoryAssetCount = positiveIntegerFact(
      projections.get(inventoryArtifactId)!,
      "asset_count",
      "Asset inventory",
    );
    const anchorArtifactIds = [
      anchors.get("world-forge.gamepack")!,
      anchors.get("world-forge.asset_subject")!,
      anchors.get("world-forge.asset_target")!,
      anchors.get("world-forge.asset_style")!,
      inventoryArtifactId,
    ];
    const key = anchorArtifactIds.join("\u0000");
    const passed = qaProjection.status === "passed" && blockerCount === 0;
    const existing = releaseByKey.get(key);
    if (existing) {
      existing.qaReportArtifactIds.push(qa.artifact_id);
      existing.allPassed &&= passed;
      if (existing.assetIds.has(assetId)) {
        throw new Error("Asset release inventory QA lineage is ambiguous for one asset");
      }
      existing.assetIds.add(assetId);
      if (qa.lifecycle === "candidate") existing.lifecycle = "candidate";
    } else {
      releaseByKey.set(key, {
        key,
        inventoryAssetCount,
        qaReportArtifactIds: [qa.artifact_id],
        lifecycle: currentLifecycle(qa),
        anchorArtifactIds,
        assetIds: new Set([assetId]),
        allPassed: passed,
      });
    }
  }
  const releaseGroups: AssetReleaseGroup[] = [];
  for (const group of releaseByKey.values()) {
    group.qaReportArtifactIds.sort(compareUtf8);
    if (
      group.qaReportArtifactIds.length > group.inventoryAssetCount ||
      group.assetIds.size > group.inventoryAssetCount
    ) {
      throw new Error("Asset release inventory QA lineage exceeds its inventory");
    }
    if (
      !group.allPassed ||
      group.qaReportArtifactIds.length < group.inventoryAssetCount ||
      group.assetIds.size < group.inventoryAssetCount
    ) {
      continue;
    }
    releaseGroups.push({
      key: group.key,
      inventoryAssetCount: group.inventoryAssetCount,
      qaReportArtifactIds: group.qaReportArtifactIds,
      lifecycle: group.lifecycle,
      anchorArtifactIds: group.anchorArtifactIds,
    });
  }

  const sealedQaSets = new Set<string>();
  for (const manifest of census.selectableArtifacts.filter(
    (artifact) => artifact.subject.format === "world-forge.asset_manifest",
  )) {
    const projection = projections.get(manifest.artifact_id);
    if (!projection || projection.status !== "release_ready") continue;
    const qaArtifactIds = projection.dependencies
      .filter(
        (artifactId) =>
          census.selectableById.get(artifactId)?.subject.format ===
          "world-forge.asset_qa_report",
      )
      .sort(compareUtf8);
    if (qaArtifactIds.length > 0) sealedQaSets.add(qaArtifactIds.join("\u0000"));
  }
  const unsealedReleaseGroups = releaseGroups.filter(
    (group) => !sealedQaSets.has(group.qaReportArtifactIds.join("\u0000")),
  );

  processingGroups.sort((left, right) => compareUtf8(left.key, right.key));
  unsealedReleaseGroups.sort((left, right) => compareUtf8(left.key, right.key));
  qaReviewGroups.sort((left, right) => compareUtf8(left.key, right.key));
  return { processingGroups, releaseGroups: unsealedReleaseGroups, qaReviewGroups };
}

export async function loadAssetPipelineCandidates(
  api: ForgeStudioApi,
  census: CreationExecutionCensus,
): Promise<AssetPipelineCandidates> {
  const pending = census.selectableArtifacts
    .filter((artifact) =>
      artifact.subject.format === "world-forge.asset_license_record" ||
      artifact.subject.format === "world-forge.asset_qa_report" ||
      artifact.subject.format === "world-forge.asset_manifest",
    )
    .map((artifact) => artifact.artifact_id)
    .sort(compareUtf8);
  const queued = new Set(pending);
  const inspections = new Map<string, StudioCreationArtifactInspectResult>();
  while (pending.length > 0) {
    if (inspections.size >= MAX_ASSET_PIPELINE_CLOSURE) {
      throw new Error("Asset pipeline inspection exceeds the bounded current closure");
    }
    const artifactId = pending.shift()!;
    queued.delete(artifactId);
    if (inspections.has(artifactId)) continue;
    const result = await expectCreationEvidenceResult(
      api.inspectCreationArtifact({
        workspaceId: census.authority.workspaceId,
        expectedRootGeneration: census.authority.rootGeneration,
        expectedSourceRevision: census.authority.sourceRevision,
        expectedWorkflowStatusHash: census.authority.workflowStatusHash,
        expectedArtifactSnapshotHash: census.authority.artifactSnapshotHash,
        artifactId,
      }),
      "creation_artifact.inspect",
    );
    if (!isInspectionShape(result)) {
      throw new Error("Forge Studio returned invalid asset pipeline inspection evidence");
    }
    const inspection = result as unknown as StudioCreationArtifactInspectResult;
    inspections.set(artifactId, inspection);
    for (const edge of inspection.projection.lineage) {
      if (
        edge.relation !== "depends_on" ||
        !census.selectableById.has(edge.artifact_id)
      ) {
        throw new Error("Asset pipeline inspection contains unavailable lineage evidence");
      }
      if (!inspections.has(edge.artifact_id) && !queued.has(edge.artifact_id)) {
        pending.push(edge.artifact_id);
        queued.add(edge.artifact_id);
      }
    }
    pending.sort(compareUtf8);
  }
  return deriveAssetPipelineCandidates(census, inspections);
}

export async function loadAssetPipelineBlockingState(
  api: ForgeStudioApi,
  authority: CreationExecutionAuthority,
): Promise<AssetPipelineBlockingState> {
  const jobIds: string[] = [];
  const reasonCodes = new Set<string>();
  let afterSequence = 0;
  const cursors = new Set<number>();
  for (let pageIndex = 0; pageIndex < 64; pageIndex += 1) {
    const page = await listCreationJobPage(api, authority.workspaceId, null, afterSequence);
    for (const listed of page.jobs) {
      const job = projectCreationJob(listed.record, authority.workspaceId);
      if (job === null || !sameRecoveryAuthority(job.authority, authority)) continue;
      if (job.cleanupPending) {
        jobIds.push(job.job_id);
        reasonCodes.add("cleanup_pending");
      }
      if (job.recoveryRequired) {
        jobIds.push(job.job_id);
        reasonCodes.add("recovery_required");
      }
    }
    if (page.nextSequence === null) {
      return {
        jobIds: [...new Set(jobIds)].sort(compareUtf8),
        reasonCodes: [...reasonCodes].sort(compareUtf8),
      };
    }
    if (cursors.has(page.nextSequence)) throw new Error("Asset pipeline job activity cursor repeats");
    cursors.add(page.nextSequence);
    afterSequence = page.nextSequence;
  }
  throw new Error("Asset pipeline job activity exceeds the bounded page limit");
}

export async function findIdenticalPendingAssetJob(
  api: ForgeStudioApi,
  authority: CreationExecutionAuthority,
  operation: "asset.process" | "asset.release.seal",
  operationParams: Record<string, unknown>,
): Promise<CreationJobView | null> {
  for (const state of ["queued", "running"] as const) {
    let afterSequence = 0;
    const cursors = new Set<number>();
    for (let pageIndex = 0; pageIndex < 64; pageIndex += 1) {
      const page = await listCreationJobPage(api, authority.workspaceId, state, afterSequence);
      for (const listed of page.jobs) {
        const exact = projectCreationJob(listed.record, authority.workspaceId, authority);
        if (
          exact?.operation === operation &&
          sameJson(operationParameters(exact), operationParams)
        ) {
          return exact;
        }
      }
      if (page.nextSequence === null) break;
      if (cursors.has(page.nextSequence)) throw new Error("Asset pipeline pending job cursor repeats");
      cursors.add(page.nextSequence);
      afterSequence = page.nextSequence;
      if (pageIndex === 63) throw new Error("Asset pipeline pending jobs exceed the bounded page limit");
    }
  }
  return null;
}

function validateInspection(
  census: CreationExecutionCensus,
  artifactId: string,
  result: StudioCreationArtifactInspectResult,
): ValidatedProjection {
  const expected = census.selectableById.get(artifactId);
  if (!expected || result.artifact.artifact_id !== artifactId) {
    throw new Error("Asset pipeline inspection is outside the current active/candidate census");
  }
  if (
    result.artifact_snapshot_hash !== census.authority.artifactSnapshotHash ||
    !sameAuthority(result.authority, census) ||
    !sameAuthority(result.artifact.authority, census) ||
    result.artifact.record_hash !== expected.record_hash ||
    result.artifact.lifecycle !== expected.lifecycle ||
    result.artifact.subject.format !== expected.subject.format ||
    result.artifact.subject.format_version !== expected.subject.format_version ||
    result.artifact.subject.id !== expected.subject.id ||
    result.artifact.subject.content_hash !== expected.subject.content_hash ||
    result.artifact.references.dependency_count !== expected.references.dependency_count
  ) {
    throw new Error("Asset pipeline lineage artifact evidence changed across authority or snapshot");
  }
  const lineage = result.projection.lineage;
  if (
    !Array.isArray(lineage) ||
    lineage.length !== expected.references.dependency_count ||
    lineage.length > MAX_ASSET_PIPELINE_CLOSURE
  ) {
    throw new Error("Asset pipeline lineage is truncated or does not match its dependency count");
  }
  const dependencies = new Set<string>();
  for (const edge of lineage) {
    const dependency = census.selectableById.get(edge.artifact_id);
    if (
      edge.relation !== "depends_on" ||
      !dependency ||
      edge.lifecycle !== dependency.lifecycle ||
      (edge.lifecycle !== "active" && edge.lifecycle !== "candidate")
    ) {
      throw new Error("Asset pipeline lineage contains stale or mismatched lifecycle evidence");
    }
    if (dependencies.has(edge.artifact_id)) throw new Error("Asset pipeline lineage repeats a dependency");
    dependencies.add(edge.artifact_id);
  }
  const facts = new Map<string, string | number | boolean | null | string[]>();
  if (!Array.isArray(result.projection.facts) || result.projection.facts.length > 128) {
    throw new Error("Asset pipeline projection facts are invalid or truncated");
  }
  for (const fact of result.projection.facts) {
    if (facts.has(fact.key)) throw new Error("Asset pipeline projection facts repeat a key");
    facts.set(fact.key, fact.value);
  }
  return {
    artifact: expected,
    dependencies: [...dependencies].sort(compareUtf8),
    facts,
    status: result.projection.status,
  };
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

function operationParameters(job: CreationJobView): Record<string, unknown> {
  const record = job.record as unknown as Record<string, unknown>;
  return isRecord(record.operation_params) ? record.operation_params : {};
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

function currentClosure(
  startArtifactId: string,
  census: CreationExecutionCensus,
  projections: ReadonlyMap<string, ValidatedProjection>,
): StudioCreationArtifact[] {
  const pending = [startArtifactId];
  const seen = new Set<string>();
  const result: StudioCreationArtifact[] = [];
  while (pending.length > 0) {
    const artifactId = pending.pop()!;
    if (seen.has(artifactId)) continue;
    if (seen.size >= MAX_ASSET_PIPELINE_CLOSURE) {
      throw new Error("Asset pipeline lineage exceeds the bounded closure");
    }
    seen.add(artifactId);
    const projection = projections.get(artifactId);
    const artifact = census.selectableById.get(artifactId);
    if (!projection || !artifact) throw new Error("Asset pipeline lineage is incomplete in the current snapshot");
    result.push(artifact);
    pending.push(...projection.dependencies);
  }
  return result;
}

function exactFormats(
  closure: readonly StudioCreationArtifact[],
  formats: readonly string[],
  context: string,
): Map<string, string> {
  const result = new Map<string, string>();
  for (const format of formats) {
    const matches = closure.filter((artifact) => artifact.subject.format === format);
    if (matches.length !== 1) {
      throw new Error(`${context} lineage has missing, ambiguous, or mixed ${format} authority`);
    }
    result.set(format, matches[0].artifact_id);
  }
  return result;
}

function assertExactStartingArtifact(
  closure: readonly StudioCreationArtifact[],
  startArtifactId: string,
  format: string,
  context: string,
): void {
  const matches = closure.filter((artifact) => artifact.subject.format === format);
  if (matches.length !== 1 || matches[0].artifact_id !== startArtifactId) {
    throw new Error(`${context} lineage has missing, ambiguous, or mixed ${format} starting evidence`);
  }
}

function stringFact(projection: ValidatedProjection, key: string, context: string): string {
  const value = projection.facts.get(key);
  if (typeof value !== "string") throw new Error(`${context} ${key} fact is unavailable`);
  return value;
}

function positiveIntegerFact(projection: ValidatedProjection, key: string, context: string): number {
  const value = nonNegativeIntegerFact(projection, key, context);
  if (value < 1) throw new Error(`${context} ${key} fact must be positive`);
  return value;
}

function nonNegativeIntegerFact(projection: ValidatedProjection, key: string, context: string): number {
  const value = projection.facts.get(key);
  if (!Number.isSafeInteger(value) || Number(value) < 0) {
    throw new Error(`${context} ${key} fact is unavailable`);
  }
  return Number(value);
}

function booleanFact(projection: ValidatedProjection, key: string, context: string): boolean {
  const value = projection.facts.get(key);
  if (typeof value !== "boolean") throw new Error(`${context} ${key} fact is unavailable`);
  return value;
}

function exactSelectedOutputBindings(
  projection: ValidatedProjection,
  context: string,
): string[] {
  const value = projection.facts.get("selected_output_bindings");
  if (
    !Array.isArray(value) ||
    value.length < 1 ||
    value.length > MAX_ASSET_RELEASE_LICENSES ||
    value.some((item) => typeof item !== "string")
  ) {
    throw new Error(`${context} selected output bindings are unavailable`);
  }
  const bindings = value.map((item) => validateOutputBinding(item, context));
  const canonical = [...new Set(bindings)].sort(compareUtf8);
  if (!sameStringArray(bindings, canonical)) {
    throw new Error(`${context} selected output bindings are duplicated or noncanonical`);
  }
  return canonical;
}

function exactLicenseBinding(projection: ValidatedProjection, context: string): string {
  const candidateArtifactId = stringFact(projection, "candidate_artifact_id", context);
  const role = stringFact(projection, "candidate_role", context);
  requireEntityId(candidateArtifactId, `${context} candidate artifact ID`);
  requireEntityId(role, `${context} candidate role`);
  return `${candidateArtifactId}:${role}`;
}

function validateOutputBinding(value: string, context: string): string {
  const separator = value.indexOf(":");
  if (separator < 1 || separator !== value.lastIndexOf(":")) {
    throw new Error(`${context} selected output binding is invalid`);
  }
  const candidateArtifactId = value.slice(0, separator);
  const role = value.slice(separator + 1);
  requireEntityId(candidateArtifactId, `${context} candidate artifact ID`);
  requireEntityId(role, `${context} candidate role`);
  return value;
}

function sameStringArray(left: readonly string[], right: readonly string[]): boolean {
  return left.length === right.length && left.every((value, index) => value === right[index]);
}

function sameAuthority(
  value: StudioCreationArtifact["authority"],
  census: CreationExecutionCensus,
): boolean {
  return (
    value.workspace_id === census.authority.workspaceId &&
    value.root_generation === census.authority.rootGeneration &&
    value.source_revision === census.authority.sourceRevision &&
    value.workflow_status_hash === census.authority.workflowStatusHash
  );
}

function sameRecoveryAuthority(
  left: CreationExecutionAuthority,
  right: CreationExecutionAuthority,
): boolean {
  return (
    left.workspaceId === right.workspaceId &&
    left.rootGeneration === right.rootGeneration &&
    left.sourceRevision === right.sourceRevision &&
    left.workflowStatusHash === right.workflowStatusHash
  );
}

function requireEntityId(value: string, context: string): void {
  if (!ENTITY_ID.test(value)) throw new Error(`${context} is not a portable entity identifier`);
}

function currentLifecycle(artifact: StudioCreationArtifact): "active" | "candidate" {
  if (artifact.lifecycle !== "active" && artifact.lifecycle !== "candidate") {
    throw new Error("Asset pipeline artifact is not current");
  }
  return artifact.lifecycle;
}

function compareUtf8(left: string, right: string): number {
  return left < right ? -1 : left > right ? 1 : 0;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

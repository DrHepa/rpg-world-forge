import type {
  ForgeStudioApi,
  StudioCreationArtifact,
  StudioCreationArtifactInspectResult,
  StudioCreationJob,
  StudioCreationOutputGrant,
} from "../shared/studio-api";
import {
  creationExecutionAuthorityKey,
  projectCreationJob,
  type CreationExecutionCensus,
} from "./creation-execution-state";
import { expectCreationEvidenceResult } from "./creation-service";

export const CREATION_PREVIEW_CHUNK_BYTES = 65_536;
export const CREATION_PREVIEW_MAX_BYTES = 64 * 1024 * 1024;
export const CREATION_PREVIEW_MAX_CHUNKS = 1024;

const MAX_PREVIEW_LINEAGE = 128;
const SHA256 = /^[0-9a-f]{64}$/u;
const HANDLE = /^[A-Za-z0-9_-]{43}$/u;
const ENTITY_ID = /^[a-z0-9][a-z0-9_-]{0,127}$/u;
const BASE64 = /^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/u;

export type CreationPreviewMediaType = "audio/wav" | "image/png";

export interface CreationPreviewSelectedOutput {
  candidateArtifactId: string;
  role: string;
  mediaType: string;
}

export interface CreationPreviewCandidate {
  key: string;
  workspaceId: string;
  rootGeneration: number;
  sourceRevision: string;
  workflowStatusHash: string | null;
  artifactSnapshotHash: string;
  assetpackArtifactId: string;
  assetpackId: string;
  assetpackContentHash: string;
  outputGrantId: string;
  outputGrantGeneration: number;
  sealJobId: string;
  assetId: string;
  mediaType: string;
  selectedOutput: CreationPreviewSelectedOutput;
  eligible: boolean;
  unsupportedReason: string | null;
}

export interface CreationPreviewCatalog {
  authorityKey: string;
  items: CreationPreviewCandidate[];
}

export interface CreationPreviewPngMetadata {
  kind: "png";
  width: number;
  height: number;
  mode: "grayscale8" | "rgb8" | "rgba8";
}

export interface CreationPreviewWavMetadata {
  kind: "wav_pcm16";
  channels: 1 | 2;
  sampleRate: number;
  frames: number;
  sampleWidth: 2;
}

export interface CreationPreviewDescriptor {
  handle: string;
  mediaType: CreationPreviewMediaType;
  byteLength: number;
  sha256: string;
  metadata: CreationPreviewPngMetadata | CreationPreviewWavMetadata;
}

export type CreationPreviewDecodeResult<T> =
  | { ok: true; value: T }
  | { ok: false; handle: string | null };

export interface CreationPreviewChunk {
  sequence: number;
  bytes: Uint8Array<ArrayBuffer>;
  cumulativeBytes: number;
  cumulativeSha256: string;
  eof: boolean;
}

export interface CreationPreviewStream {
  declaredBytes: number;
  nextSequence: number;
  cumulativeBytes: number;
  eof: boolean;
  previous: {
    sequence: number;
    canonicalReply: string;
  } | null;
}

export type CreationPreviewChunkTransition =
  | { kind: "next"; chunk: CreationPreviewChunk; stream: CreationPreviewStream }
  | { kind: "replay"; stream: CreationPreviewStream };

interface ValidatedProjection {
  artifact: StudioCreationArtifact;
  dependencies: string[];
  facts: ReadonlyMap<string, string | number | boolean | null | string[]>;
  status: string | null;
}

interface AssetProjectionGroup {
  assetId: string;
  selectedBindings: string[];
  selectionArtifactId: string;
  licenseBindings: Map<string, string>;
  qaArtifactId: string;
}

export async function loadCreationPreviewCatalog(
  api: ForgeStudioApi,
  census: CreationExecutionCensus,
  grants: readonly StudioCreationOutputGrant[],
): Promise<CreationPreviewCatalog> {
  const assetpacks = census.candidateArtifacts.filter(
    (artifact) => artifact.subject.format === "world-forge.assetpack",
  );
  const inspections = new Map<string, StudioCreationArtifactInspectResult>();
  const jobs = new Map<string, StudioCreationJob>();
  const pending = [...assetpacks.map((artifact) => artifact.artifact_id)].sort(compareUtf8);
  const queued = new Set(pending);
  while (pending.length > 0) {
    if (inspections.size >= MAX_PREVIEW_LINEAGE * Math.max(1, assetpacks.length)) {
      throw new Error("Creation preview lineage exceeds the bounded inspection limit");
    }
    const artifactId = pending.shift()!;
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
    const inspection = result as unknown as StudioCreationArtifactInspectResult;
    inspections.set(artifactId, inspection);
    const validated = validateInspection(census, artifactId, inspection);
    if (validated.artifact.subject.format === "world-forge.assetpack") {
      for (const dependency of validated.dependencies) {
        const artifact = census.selectableById.get(dependency);
        if (
          artifact &&
          isPreviewLineageFormat(artifact.subject.format) &&
          !queued.has(dependency)
        ) {
          queued.add(dependency);
          pending.push(dependency);
        }
      }
      pending.sort(compareUtf8);
      const jobId = validated.artifact.producer.reference_id;
      const jobResult = await expectCreationEvidenceResult(
        api.getCreationJob(jobId),
        "creation_job.get",
      );
      if (!asRecord(jobResult.job)) {
        throw new Error("Creation preview seal job response is invalid");
      }
      jobs.set(jobId, jobResult.job as StudioCreationJob);
    }
  }
  return deriveCreationPreviewCatalog(census, inspections, jobs, grants);
}

export function deriveCreationPreviewCatalog(
  census: CreationExecutionCensus,
  inspections: ReadonlyMap<string, StudioCreationArtifactInspectResult>,
  jobs: ReadonlyMap<string, StudioCreationJob>,
  grants: readonly StudioCreationOutputGrant[],
): CreationPreviewCatalog {
  const assetpacks = census.candidateArtifacts
    .filter((artifact) => artifact.subject.format === "world-forge.assetpack")
    .sort((left, right) => compareUtf8(left.artifact_id, right.artifact_id));
  const items: CreationPreviewCandidate[] = [];
  for (const assetpack of assetpacks) {
    const assetpackProjection = requireProjection(census, inspections, assetpack.artifact_id);
    const job = requireCommittedSealJob(census, assetpack, jobs.get(assetpack.producer.reference_id));
    const publication = requireSealPublication(job, assetpack);
    requirePublishedGrant(census, grants, publication);
    const direct = assetpackProjection.dependencies.map((artifactId) =>
      requireProjection(census, inspections, artifactId),
    );
    const groups = deriveAssetGroups(direct);
    const declaredAssetCount = nonNegativeIntegerFact(
      assetpackProjection,
      "asset_count",
      "Sealed assetpack",
    );
    if (declaredAssetCount < 1 || groups.size !== declaredAssetCount) {
      throw new Error("Creation preview sealed assetpack lineage is incomplete or ambiguous");
    }
    const operation = asRecord(job.operation_params);
    const qaIds = operation ? exactStringArray(operation.qa_report_artifact_ids) : null;
    const exactQaIds = [...groups.values()].map((group) => group.qaArtifactId).sort(compareUtf8);
    if (!qaIds || !sameStrings(qaIds, exactQaIds)) {
      throw new Error("Creation preview QA lineage does not match the committed seal job");
    }
    for (const group of [...groups.values()].sort((left, right) => compareUtf8(left.assetId, right.assetId))) {
      if (group.selectedBindings.length !== group.licenseBindings.size) {
        throw new Error("Creation preview selected output licensing is incomplete");
      }
      for (const binding of group.selectedBindings) {
        if (!group.licenseBindings.has(binding)) {
          throw new Error("Creation preview selected output is not licensed by the exact lineage");
        }
      }
      const selected = group.selectedBindings.map(parseOutputBinding);
      const unique = selected.length === 1 ? selected[0] : null;
      const mediaType = unique ? mediaTypeForRole(unique.role) : "multiple outputs";
      const eligible =
        unique !== null && (mediaType === "image/png" || mediaType === "audio/wav");
      const unsupportedReason = eligible
        ? null
        : unique === null
          ? "Assets with multiple selected outputs are not supported by the unique preview lease."
          : `${mediaType} preview is not supported; only PNG and WAV can be opened.`;
      const selectedOutput: CreationPreviewSelectedOutput = unique
        ? { ...unique, mediaType }
        : {
            candidateArtifactId: group.selectedBindings.join(","),
            role: "multiple",
            mediaType,
          };
      const candidate: CreationPreviewCandidate = {
        key: "",
        workspaceId: census.authority.workspaceId,
        rootGeneration: census.authority.rootGeneration,
        sourceRevision: census.authority.sourceRevision,
        workflowStatusHash: census.authority.workflowStatusHash,
        artifactSnapshotHash: census.authority.artifactSnapshotHash,
        assetpackArtifactId: assetpack.artifact_id,
        assetpackId: assetpack.subject.id,
        assetpackContentHash: assetpack.subject.content_hash,
        outputGrantId: publication.grantId,
        outputGrantGeneration: publication.grantGeneration,
        sealJobId: job.job_id,
        assetId: group.assetId,
        mediaType,
        selectedOutput,
        eligible,
        unsupportedReason,
      };
      candidate.key = creationPreviewCandidateKey(candidate);
      items.push(candidate);
    }
  }
  return {
    authorityKey: creationExecutionAuthorityKey(census.authority),
    items: items.sort((left, right) => compareUtf8(left.key, right.key)),
  };
}

export function creationPreviewCandidateKey(
  candidate: CreationPreviewCandidate | null,
): string {
  if (!candidate) return "idle";
  return JSON.stringify([
    candidate.workspaceId,
    candidate.rootGeneration,
    candidate.sourceRevision,
    candidate.workflowStatusHash,
    candidate.artifactSnapshotHash,
    candidate.assetpackArtifactId,
    candidate.assetpackContentHash,
    candidate.outputGrantId,
    candidate.outputGrantGeneration,
    candidate.sealJobId,
    candidate.assetId,
    candidate.selectedOutput.candidateArtifactId,
    candidate.selectedOutput.role,
    candidate.selectedOutput.mediaType,
  ]);
}

export function decodeCreationPreviewOpen(
  raw: unknown,
  candidate: CreationPreviewCandidate,
): CreationPreviewDecodeResult<CreationPreviewDescriptor> {
  const envelope = responseEnvelope(raw, "creation_preview.open");
  const result = envelope && exactRecord(envelope.result, ["preview"]);
  const preview = result && asRecord(result.preview);
  const handle = preview && typeof preview.handle === "string" && HANDLE.test(preview.handle)
    ? preview.handle
    : null;
  if (
    !preview ||
    !hasExactKeys(preview, [
      "asset_id",
      "assetpack_artifact_id",
      "byte_length",
      "chunk_bytes",
      "format",
      "format_version",
      "handle",
      "media_type",
      "metadata",
      "output_grant_generation",
      "output_grant_id",
      "sha256",
      "workspace_id",
    ]) ||
    preview.format !== "world-forge.studio_creation_preview" ||
    preview.format_version !== 1 ||
    !handle ||
    preview.workspace_id !== candidate.workspaceId ||
    preview.assetpack_artifact_id !== candidate.assetpackArtifactId ||
    preview.output_grant_id !== candidate.outputGrantId ||
    preview.output_grant_generation !== candidate.outputGrantGeneration ||
    preview.asset_id !== candidate.assetId ||
    preview.media_type !== candidate.selectedOutput.mediaType ||
    (preview.media_type !== "image/png" && preview.media_type !== "audio/wav") ||
    !safeInteger(preview.byte_length, 1, CREATION_PREVIEW_MAX_BYTES) ||
    typeof preview.sha256 !== "string" ||
    !SHA256.test(preview.sha256) ||
    preview.chunk_bytes !== CREATION_PREVIEW_CHUNK_BYTES
  ) {
    return { ok: false, handle };
  }
  const metadata = decodeMetadata(preview.media_type, preview.metadata);
  if (!metadata) return { ok: false, handle };
  return {
    ok: true,
    value: {
      handle,
      mediaType: preview.media_type,
      byteLength: Number(preview.byte_length),
      sha256: preview.sha256,
      metadata,
    },
  };
}

export function previewHandleFromOpenReply(raw: unknown): string | null {
  const client = asRecord(raw);
  const envelope = client?.ok === true ? asRecord(client.value) : null;
  const result = asRecord(envelope?.result);
  const preview = asRecord(result?.preview);
  return typeof preview?.handle === "string" && HANDLE.test(preview.handle)
    ? preview.handle
    : null;
}

export function initialCreationPreviewStream(declaredBytes: number): CreationPreviewStream {
  if (!safeInteger(declaredBytes, 1, CREATION_PREVIEW_MAX_BYTES)) {
    throw new Error("Creation preview declared byte length is invalid");
  }
  return {
    declaredBytes,
    nextSequence: 0,
    cumulativeBytes: 0,
    eof: false,
    previous: null,
  };
}

export function applyCreationPreviewChunk(
  raw: unknown,
  handle: string,
  stream: CreationPreviewStream,
  declaredSha256: string,
): CreationPreviewChunkTransition {
  if (!HANDLE.test(handle) || !SHA256.test(declaredSha256) || stream.eof) {
    throw new Error("Creation preview stream state is invalid");
  }
  const envelope = responseEnvelope(raw, "creation_preview.read");
  const result = envelope && exactRecord(envelope.result, [
    "byte_length",
    "cumulative_bytes",
    "cumulative_sha256",
    "data_base64",
    "eof",
    "handle",
    "sequence",
  ]);
  if (!result || result.handle !== handle || !Number.isSafeInteger(result.sequence)) {
    throw new Error("Creation preview chunk envelope is invalid");
  }
  const canonicalReply = canonicalChunkReply(result);
  const sequence = Number(result.sequence);
  if (sequence === stream.nextSequence - 1) {
    if (!stream.previous || stream.previous.sequence !== sequence || stream.previous.canonicalReply !== canonicalReply) {
      throw new Error("Creation preview immediate-previous replay changed");
    }
    return { kind: "replay", stream };
  }
  if (sequence !== stream.nextSequence) {
    throw new Error("Creation preview chunk sequence is missing or out of order");
  }
  if (sequence < 0 || sequence >= CREATION_PREVIEW_MAX_CHUNKS) {
    throw new Error("Creation preview chunk sequence exceeds the bounded range");
  }
  const bytes = decodeCanonicalBase64(result.data_base64);
  const remaining = stream.declaredBytes - stream.cumulativeBytes;
  const expectedLength = Math.min(CREATION_PREVIEW_CHUNK_BYTES, remaining);
  const cumulativeBytes = stream.cumulativeBytes + expectedLength;
  const expectedEof = cumulativeBytes === stream.declaredBytes;
  if (
    expectedLength < 1 ||
    result.byte_length !== expectedLength ||
    bytes.byteLength !== expectedLength ||
    result.cumulative_bytes !== cumulativeBytes ||
    typeof result.eof !== "boolean" ||
    result.eof !== expectedEof ||
    typeof result.cumulative_sha256 !== "string" ||
    !SHA256.test(result.cumulative_sha256) ||
    (expectedEof && result.cumulative_sha256 !== declaredSha256)
  ) {
    throw new Error("Creation preview chunk length, cumulative bytes, hash, or EOF is invalid");
  }
  const chunk: CreationPreviewChunk = {
    sequence,
    bytes,
    cumulativeBytes,
    cumulativeSha256: result.cumulative_sha256,
    eof: expectedEof,
  };
  return {
    kind: "next",
    chunk,
    stream: {
      declaredBytes: stream.declaredBytes,
      nextSequence: sequence + 1,
      cumulativeBytes,
      eof: expectedEof,
      previous: { sequence, canonicalReply },
    },
  };
}

export function decodeCreationPreviewClose(raw: unknown, handle: string): boolean {
  const envelope = responseEnvelope(raw, "creation_preview.close");
  const result = envelope && exactRecord(envelope.result, ["closed", "handle"]);
  return result?.handle === handle && result.closed === true;
}

function deriveAssetGroups(projections: readonly ValidatedProjection[]): Map<string, AssetProjectionGroup> {
  const groups = new Map<string, AssetProjectionGroup>();
  const selections = projections.filter(
    (projection) => projection.artifact.subject.format === "world-forge.asset_selection",
  );
  const licenses = projections.filter(
    (projection) => projection.artifact.subject.format === "world-forge.asset_license_record",
  );
  const qaReports = projections.filter(
    (projection) => projection.artifact.subject.format === "world-forge.asset_qa_report",
  );
  for (const selection of selections) {
    const assetId = stringFact(selection, "asset_id", "Asset selection");
    requireEntityId(assetId, "Asset selection asset ID");
    if (groups.has(assetId)) throw new Error("Creation preview asset selection is ambiguous");
    const bindings = stringArrayFact(selection, "selected_output_bindings", "Asset selection", 4);
    const canonical = [...new Set(bindings)].sort(compareUtf8);
    if (!sameStrings(bindings, canonical)) {
      throw new Error("Creation preview selected output bindings are duplicated or noncanonical");
    }
    bindings.forEach(parseOutputBinding);
    groups.set(assetId, {
      assetId,
      selectedBindings: bindings,
      selectionArtifactId: selection.artifact.artifact_id,
      licenseBindings: new Map(),
      qaArtifactId: "",
    });
  }
  for (const license of licenses) {
    const assetId = stringFact(license, "asset_id", "Asset license");
    const group = groups.get(assetId);
    if (!group || booleanFact(license, "redistribution", "Asset license") !== true) {
      throw new Error("Creation preview asset license does not authorize the exact selection");
    }
    const binding = `${stringFact(license, "candidate_artifact_id", "Asset license")}:${stringFact(license, "candidate_role", "Asset license")}`;
    parseOutputBinding(binding);
    if (group.licenseBindings.has(binding)) {
      throw new Error("Creation preview asset license binding is duplicated");
    }
    group.licenseBindings.set(binding, license.artifact.artifact_id);
  }
  for (const qa of qaReports) {
    const assetId = stringFact(qa, "asset_id", "Asset QA");
    const group = groups.get(assetId);
    if (
      !group ||
      group.qaArtifactId ||
      qa.status !== "passed" ||
      nonNegativeIntegerFact(qa, "blocker_count", "Asset QA") !== 0
    ) {
      throw new Error("Creation preview asset QA is missing, failed, blocked, or ambiguous");
    }
    group.qaArtifactId = qa.artifact.artifact_id;
  }
  if (
    groups.size < 1 ||
    [...groups.values()].some(
      (group) => group.licenseBindings.size < 1 || group.qaArtifactId.length === 0,
    )
  ) {
    throw new Error("Creation preview selection, license, and QA lineage is incomplete");
  }
  return groups;
}

function requireProjection(
  census: CreationExecutionCensus,
  inspections: ReadonlyMap<string, StudioCreationArtifactInspectResult>,
  artifactId: string,
): ValidatedProjection {
  const inspection = inspections.get(artifactId);
  if (!inspection) throw new Error("Creation preview lineage inspection is incomplete");
  return validateInspection(census, artifactId, inspection);
}

function validateInspection(
  census: CreationExecutionCensus,
  artifactId: string,
  inspection: StudioCreationArtifactInspectResult,
): ValidatedProjection {
  const expected = census.selectableById.get(artifactId);
  if (
    !expected ||
    inspection.artifact_snapshot_hash !== census.authority.artifactSnapshotHash ||
    !sameAuthority(inspection.authority, census) ||
    inspection.artifact.artifact_id !== artifactId ||
    inspection.artifact.record_hash !== expected.record_hash ||
    inspection.artifact.lifecycle !== expected.lifecycle ||
    inspection.artifact.subject.format !== expected.subject.format ||
    inspection.artifact.subject.id !== expected.subject.id ||
    inspection.artifact.subject.content_hash !== expected.subject.content_hash ||
    !sameAuthority(inspection.artifact.authority, census)
  ) {
    throw new Error("Creation preview artifact authority or snapshot changed");
  }
  if (
    !Array.isArray(inspection.projection.lineage) ||
    inspection.projection.lineage.length !== expected.references.dependency_count ||
    inspection.projection.lineage.length > MAX_PREVIEW_LINEAGE
  ) {
    throw new Error("Creation preview lineage projection is truncated or oversized");
  }
  const dependencies = new Set<string>();
  for (const edge of inspection.projection.lineage) {
    const dependency = census.selectableById.get(edge.artifact_id);
    if (
      edge.relation !== "depends_on" ||
      !dependency ||
      edge.lifecycle !== dependency.lifecycle ||
      (edge.lifecycle !== "active" && edge.lifecycle !== "candidate") ||
      dependencies.has(edge.artifact_id)
    ) {
      throw new Error("Creation preview lineage projection is stale, duplicated, or unavailable");
    }
    dependencies.add(edge.artifact_id);
  }
  if (!Array.isArray(inspection.projection.facts) || inspection.projection.facts.length > 128) {
    throw new Error("Creation preview projection facts are truncated or invalid");
  }
  const facts = new Map<string, string | number | boolean | null | string[]>();
  for (const fact of inspection.projection.facts) {
    if (facts.has(fact.key)) throw new Error("Creation preview projection facts repeat a key");
    facts.set(fact.key, fact.value);
  }
  return {
    artifact: expected,
    dependencies: [...dependencies].sort(compareUtf8),
    facts,
    status: inspection.projection.status,
  };
}

function requireCommittedSealJob(
  census: CreationExecutionCensus,
  assetpack: StudioCreationArtifact,
  record: StudioCreationJob | undefined,
): StudioCreationJob {
  const job = projectCreationJob(record, census.authority.workspaceId);
  const result = job?.record.result;
  if (
    !job ||
    job.operation !== "asset.release.seal" ||
    job.record.format_version !== 3 ||
    job.state !== "succeeded" ||
    job.progress !== "committed" ||
    job.cleanupPending ||
    job.analysisStatus !== "passed" ||
    job.authority.rootGeneration !== census.authority.rootGeneration ||
    job.authority.sourceRevision !== census.authority.sourceRevision ||
    job.authority.workflowStatusHash !== census.authority.workflowStatusHash ||
    !result ||
    result.artifact_snapshot_hash !== census.authority.artifactSnapshotHash ||
    !result.output_artifact_ids.includes(assetpack.artifact_id) ||
    assetpack.producer.kind !== "future_candidate" ||
    assetpack.producer.reference_id !== job.job_id
  ) {
    throw new Error("Creation preview assetpack was not produced by an exact succeeded committed seal job");
  }
  return job.record;
}

function requireSealPublication(
  job: StudioCreationJob,
  assetpack: StudioCreationArtifact,
): {
  grantId: string;
  grantGeneration: number;
  assetpackId: string;
  assetpackContentHash: string;
  inventoryHash: string;
} {
  const result = asRecord(job.result);
  const operation = asRecord(job.operation_params);
  const publication = asRecord(result?.publication);
  const identity = asRecord(publication?.assetpack);
  if (
    !operation ||
    !publication ||
    !identity ||
    publication.kind !== "generic_assetpack_directory" ||
    publication.state !== "published" ||
    typeof publication.grant_id !== "string" ||
    !ENTITY_ID.test(publication.grant_id) ||
    !safeInteger(publication.grant_generation, 0, Number.MAX_SAFE_INTEGER) ||
    operation.target_grant_id !== publication.grant_id ||
    operation.target_grant_generation !== publication.grant_generation ||
    identity.format !== "world-forge.assetpack" ||
    identity.format_version !== 1 ||
    identity.id !== assetpack.subject.id ||
    identity.content_hash !== assetpack.subject.content_hash ||
    typeof identity.inventory_hash !== "string" ||
    !SHA256.test(identity.inventory_hash)
  ) {
    throw new Error("Creation preview sealed publication identity or target grant is invalid");
  }
  return {
    grantId: publication.grant_id,
    grantGeneration: Number(publication.grant_generation),
    assetpackId: assetpack.subject.id,
    assetpackContentHash: assetpack.subject.content_hash,
    inventoryHash: identity.inventory_hash,
  };
}

function requirePublishedGrant(
  census: CreationExecutionCensus,
  grants: readonly StudioCreationOutputGrant[],
  publication: {
    grantId: string;
    grantGeneration: number;
    assetpackId: string;
    assetpackContentHash: string;
    inventoryHash: string;
  },
): void {
  const matches = grants.filter((grant) => grant.grant_id === publication.grantId);
  if (matches.length !== 1) throw new Error("Creation preview published output grant is unavailable or ambiguous");
  const grant = matches[0];
  const identity = asRecord(grant.publication);
  if (
    grant.format !== "world-forge.studio_creation_output_grant" ||
    grant.format_version !== 1 ||
    grant.workspace_id !== census.authority.workspaceId ||
    grant.kind !== "generic_assetpack_directory" ||
    grant.state !== "published" ||
    grant.generation !== publication.grantGeneration ||
    !identity ||
    identity.format !== "world-forge.assetpack" ||
    identity.format_version !== 1 ||
    identity.id !== publication.assetpackId ||
    identity.content_hash !== publication.assetpackContentHash ||
    identity.inventory_hash !== publication.inventoryHash
  ) {
    throw new Error("Creation preview output grant is not the exact published seal authority");
  }
}

function responseEnvelope(raw: unknown, method: string): Record<string, unknown> | null {
  const client = exactRecord(raw, ["ok", "value"]);
  if (!client || client.ok !== true) return null;
  const envelope = exactRecord(client.value, [
    "kind",
    "method",
    "protocol",
    "protocol_version",
    "request_id",
    "result",
  ]);
  if (
    !envelope ||
    envelope.protocol !== "rpg-world-forge.studio_protocol" ||
    envelope.protocol_version !== 4 ||
    envelope.kind !== "response" ||
    envelope.method !== method ||
    typeof envelope.request_id !== "string" ||
    !ENTITY_ID.test(envelope.request_id)
  ) {
    return null;
  }
  return envelope;
}

function decodeMetadata(
  mediaType: CreationPreviewMediaType,
  raw: unknown,
): CreationPreviewPngMetadata | CreationPreviewWavMetadata | null {
  const value = asRecord(raw);
  if (mediaType === "image/png") {
    if (
      !value ||
      !hasExactKeys(value, ["height", "kind", "mode", "width"]) ||
      value.kind !== "png" ||
      !safeInteger(value.width, 1, 16_384) ||
      !safeInteger(value.height, 1, 16_384) ||
      !["grayscale8", "rgb8", "rgba8"].includes(String(value.mode))
    ) return null;
    return {
      kind: "png",
      width: Number(value.width),
      height: Number(value.height),
      mode: value.mode as CreationPreviewPngMetadata["mode"],
    };
  }
  if (
    !value ||
    !hasExactKeys(value, ["channels", "frames", "kind", "sample_rate", "sample_width"]) ||
    value.kind !== "wav_pcm16" ||
    (value.channels !== 1 && value.channels !== 2) ||
    !safeInteger(value.sample_rate, 8_000, 192_000) ||
    !safeInteger(value.frames, 1, 192_000_000) ||
    value.sample_width !== 2
  ) return null;
  return {
    kind: "wav_pcm16",
    channels: value.channels,
    sampleRate: Number(value.sample_rate),
    frames: Number(value.frames),
    sampleWidth: 2,
  };
}

function decodeCanonicalBase64(value: unknown): Uint8Array<ArrayBuffer> {
  if (
    typeof value !== "string" ||
    value.length < 4 ||
    value.length > 87_384 ||
    value.length % 4 !== 0 ||
    !BASE64.test(value)
  ) {
    throw new Error("Creation preview chunk base64 is not canonical");
  }
  let binary: string;
  try {
    binary = globalThis.atob(value);
  } catch {
    throw new Error("Creation preview chunk base64 is invalid");
  }
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index);
  }
  let rebuilt = "";
  for (let offset = 0; offset < bytes.length; offset += 0x8000) {
    rebuilt += String.fromCharCode(...bytes.subarray(offset, offset + 0x8000));
  }
  if (globalThis.btoa(rebuilt) !== value) {
    throw new Error("Creation preview chunk base64 is noncanonical");
  }
  return bytes;
}

function canonicalChunkReply(result: Record<string, unknown>): string {
  return JSON.stringify([
    result.handle,
    result.sequence,
    result.data_base64,
    result.byte_length,
    result.cumulative_bytes,
    result.cumulative_sha256,
    result.eof,
  ]);
}

function isPreviewLineageFormat(format: string): boolean {
  return (
    format === "world-forge.asset_selection" ||
    format === "world-forge.asset_license_record" ||
    format === "world-forge.asset_qa_report"
  );
}

function mediaTypeForRole(role: string): string {
  if (role === "texture") return "image/png";
  if (role === "audio") return "audio/wav";
  if (["animation", "collision", "model", "skeleton"].includes(role)) return "model/gltf-binary";
  if (["fragment_shader", "vertex_shader"].includes(role)) return "text/x-glsl";
  if (["clipset", "localized_text"].includes(role)) return "application/json";
  if (role === "font") return "font/otf or font/ttf";
  return `role ${role}`;
}

function parseOutputBinding(value: string): { candidateArtifactId: string; role: string } {
  const separator = value.indexOf(":");
  if (separator < 1 || separator !== value.lastIndexOf(":")) {
    throw new Error("Creation preview selected output binding is invalid");
  }
  const candidateArtifactId = value.slice(0, separator);
  const role = value.slice(separator + 1);
  requireEntityId(candidateArtifactId, "Creation preview candidate artifact ID");
  requireEntityId(role, "Creation preview candidate role");
  return { candidateArtifactId, role };
}

function stringFact(projection: ValidatedProjection, key: string, context: string): string {
  const value = projection.facts.get(key);
  if (typeof value !== "string") throw new Error(`${context} ${key} fact is unavailable`);
  return value;
}

function stringArrayFact(
  projection: ValidatedProjection,
  key: string,
  context: string,
  maximum: number,
): string[] {
  const value = projection.facts.get(key);
  if (
    !Array.isArray(value) ||
    value.length < 1 ||
    value.length > maximum ||
    value.some((item) => typeof item !== "string")
  ) throw new Error(`${context} ${key} fact is unavailable`);
  return [...value];
}

function booleanFact(projection: ValidatedProjection, key: string, context: string): boolean {
  const value = projection.facts.get(key);
  if (typeof value !== "boolean") throw new Error(`${context} ${key} fact is unavailable`);
  return value;
}

function nonNegativeIntegerFact(
  projection: ValidatedProjection,
  key: string,
  context: string,
): number {
  const value = projection.facts.get(key);
  if (!Number.isSafeInteger(value) || Number(value) < 0) {
    throw new Error(`${context} ${key} fact is unavailable`);
  }
  return Number(value);
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

function exactStringArray(value: unknown): string[] | null {
  if (!Array.isArray(value) || value.some((item) => typeof item !== "string")) return null;
  const result: string[] = [];
  for (const item of value) {
    if (typeof item !== "string") return null;
    result.push(item);
  }
  return sameStrings(result, [...new Set(result)].sort(compareUtf8)) ? result : null;
}

function requireEntityId(value: string, context: string): void {
  if (!ENTITY_ID.test(value)) throw new Error(`${context} is not a portable entity identifier`);
}

function exactRecord(value: unknown, keys: readonly string[]): Record<string, unknown> | null {
  const record = asRecord(value);
  return record && hasExactKeys(record, keys) ? record : null;
}

function hasExactKeys(value: Record<string, unknown>, expected: readonly string[]): boolean {
  const actual = Object.keys(value).sort(compareUtf8);
  const ordered = [...expected].sort(compareUtf8);
  return sameStrings(actual, ordered);
}

function safeInteger(value: unknown, minimum: number, maximum: number): value is number {
  return (
    typeof value === "number" &&
    Number.isSafeInteger(value) &&
    value >= minimum &&
    value <= maximum
  );
}

function sameStrings(left: readonly string[], right: readonly string[]): boolean {
  return left.length === right.length && left.every((value, index) => value === right[index]);
}

function compareUtf8(left: string, right: string): number {
  return left < right ? -1 : left > right ? 1 : 0;
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

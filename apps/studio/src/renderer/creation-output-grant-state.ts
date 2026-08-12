import type {
  ForgeStudioApi,
  StudioCreationOutputGrant,
} from "../shared/studio-api";
import type { CreationExecutionCensus } from "./creation-execution-state";
import {
  listCreationJobPage,
  sameCreationExecutionAuthority,
  type CreationJobView,
} from "./creation-execution-state";
import {
  expectCreationAuthorityResult,
  expectCreationEvidenceResult,
  isClosedCreationAuthorityCapabilities,
} from "./creation-service";

const PAGE_SIZE = 8;
const MAX_PAGES = 64;
const JOB_STATES = ["queued", "running", "orphaned"] as const;
const ENTITY_ID = /^[a-z0-9][a-z0-9_-]{0,127}$/u;
const WORKSPACE_ID = /^[a-z][a-z0-9_-]{1,63}$/u;
const SHA256 = /^[0-9a-f]{64}$/u;
const UTC_TIMESTAMP =
  /^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z$/u;
const STATES = new Set([
  "ready",
  "reserved",
  "published",
  "recovery_required",
  "revoked",
]);
const VERSION_KIND = new Map<number, string>([
  [1, "generic_assetpack_directory"],
  [2, "game_runtime_bundle_directory"],
  [3, "game_materialization_bundle_directory"],
  [4, "standalone_game_directory"],
  [5, "game_package_file"],
  [6, "headless_evidence_directory"],
]);
const VERSION_PUBLICATION_FORMAT = new Map<number, string>([
  [1, "world-forge.assetpack"],
  [2, "world-forge.game_runtime_bundle"],
  [3, "world-forge.game_materialization_bundle"],
  [4, "world-forge.standalone_game"],
  [5, "world-forge.game_package"],
  [6, "world-forge.headless_evidence_set"],
]);

export async function loadCreationOutputGrantCensus(
  api: ForgeStudioApi,
  census: CreationExecutionCensus,
): Promise<StudioCreationOutputGrant[]> {
  return loadOutputGrantCensus(census, 5, (cursor) =>
    expectCreationEvidenceResult(
      api.listCreationOutputGrants(grantListParams(census, cursor)),
      "creation_output_grant.list",
    ),
  );
}

export async function loadCreationAuthorityOutputGrantCensus(
  api: ForgeStudioApi,
  census: CreationExecutionCensus,
  authorityCapabilities: unknown,
): Promise<StudioCreationOutputGrant[]> {
  if (
    !isClosedCreationAuthorityCapabilities(authorityCapabilities) ||
    typeof api.listCreationAuthorityOutputGrants !== "function"
  ) {
    throw censusError("authority output grants unavailable");
  }
  return loadOutputGrantCensus(census, 6, (cursor) =>
    expectCreationAuthorityResult(
      api.listCreationAuthorityOutputGrants!(grantListParams(census, cursor)),
      "creation_output_grant.list",
    ),
  );
}

function grantListParams(census: CreationExecutionCensus, cursor: string | null) {
  return {
    workspaceId: census.authority.workspaceId,
    expectedRootGeneration: census.authority.rootGeneration,
    expectedSourceRevision: census.authority.sourceRevision,
    expectedWorkflowStatusHash: census.authority.workflowStatusHash,
    expectedArtifactSnapshotHash: census.authority.artifactSnapshotHash,
    cursor,
    limit: PAGE_SIZE,
  };
}

async function loadOutputGrantCensus(
  census: CreationExecutionCensus,
  maxFormatVersion: 5 | 6,
  requestPage: (cursor: string | null) => Promise<Record<string, unknown>>,
): Promise<StudioCreationOutputGrant[]> {
  const retained: StudioCreationOutputGrant[] = [];
  const seen = new Set<string>();
  let cursor: string | null = null;
  let previous: string | null = null;
  for (let pageIndex = 0; pageIndex < MAX_PAGES; pageIndex += 1) {
    const page = validateGrantPage(
      await requestPage(cursor),
      census,
      previous,
      maxFormatVersion,
    );
    for (const grant of page.grants) {
      if (seen.has(grant.grant_id)) {
        throw censusError("duplicate grant identity across pages");
      }
      seen.add(grant.grant_id);
      previous = grant.grant_id;
      retained.push(grant);
    }
    if (page.nextCursor === null) return retained;
    if (seen.has(page.nextCursor) && page.nextCursor !== previous) {
      throw censusError("repeated pagination cursor");
    }
    if (page.nextCursor === cursor) {
      throw censusError("pagination cursor did not advance");
    }
    cursor = page.nextCursor;
  }
  throw censusError("pagination exceeded the bounded page limit");
}

export async function loadCreationAssetpackGrantBindings(
  api: ForgeStudioApi,
  census: CreationExecutionCensus,
  grants: readonly StudioCreationOutputGrant[],
): Promise<CreationJobView[]> {
  const jobs: CreationJobView[] = [];
  const seenJobs = new Set<string>();
  for (const state of JOB_STATES) {
    let afterSequence = 0;
    const cursors = new Set<number>();
    for (let pageIndex = 0; pageIndex < MAX_PAGES; pageIndex += 1) {
      const page = await listCreationJobPage(
        api,
        census.authority.workspaceId,
        state,
        afterSequence,
      );
      for (const job of page.jobs) {
        if (seenJobs.has(job.job_id)) {
          throw censusError("duplicate job identity across binding pages");
        }
        seenJobs.add(job.job_id);
        if (
          job.operation === "asset.release.seal" &&
          sameCreationExecutionAuthority(job.authority, census.authority)
        ) {
          jobs.push(job);
        }
      }
      if (page.nextSequence === null) break;
      if (cursors.has(page.nextSequence)) {
        throw censusError("cyclic job cursor while binding grants");
      }
      cursors.add(page.nextSequence);
      afterSequence = page.nextSequence;
      if (pageIndex === MAX_PAGES - 1) {
        throw censusError("grant binding jobs exceeded the bounded page limit");
      }
    }
  }

  const bound: CreationJobView[] = [];
  const usedJobs = new Set<string>();
  for (const grant of grants) {
    if (
      grant.format_version !== 1 ||
      grant.kind !== "generic_assetpack_directory"
    ) {
      continue;
    }
    const expectedState =
      grant.state === "reserved"
        ? new Set(["queued", "running"])
        : grant.state === "recovery_required"
          ? new Set(["orphaned"])
          : null;
    if (expectedState === null) continue;
    const expectedGeneration =
      grant.state === "recovery_required"
        ? grant.generation - 1
        : grant.generation;
    const matches = jobs.filter((job) => {
      const params = job.record.operation_params;
      return (
        expectedGeneration >= 0 &&
        expectedState.has(job.state) &&
        isRecord(params) &&
        params.target_grant_id === grant.grant_id &&
        params.target_grant_generation === expectedGeneration
      );
    });
    if (matches.length !== 1 || usedJobs.has(matches[0]?.job_id ?? "")) {
      throw censusError(
        `${grant.state} grant ${grant.grant_id} does not have one exact seal job binding`,
      );
    }
    usedJobs.add(matches[0].job_id);
    bound.push(matches[0]);
  }
  return bound;
}

export function validateCreationOutputGrant(
  value: unknown,
): StudioCreationOutputGrant {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, [
      "format",
      "format_version",
      "grant_id",
      "workspace_id",
      "kind",
      "display_name",
      "state",
      "generation",
      "publication",
      "created_at",
      "updated_at",
    ]) ||
    value.format !== "world-forge.studio_creation_output_grant" ||
    !Number.isSafeInteger(value.format_version) ||
    !VERSION_KIND.has(Number(value.format_version)) ||
    value.kind !== VERSION_KIND.get(Number(value.format_version)) ||
    !isEntityId(value.grant_id) ||
    typeof value.workspace_id !== "string" ||
    !WORKSPACE_ID.test(value.workspace_id) ||
    !isDisplayName(value.display_name) ||
    typeof value.state !== "string" ||
    !STATES.has(value.state) ||
    !isGeneration(value.generation) ||
    !isUtcTimestamp(value.created_at) ||
    !isUtcTimestamp(value.updated_at) ||
    Date.parse(value.updated_at) < Date.parse(value.created_at)
  ) {
    throw new Error("Forge Studio returned an invalid creation output grant");
  }
  if (value.state === "published") {
    validatePublication(value.publication, Number(value.format_version));
  } else if (value.publication !== null) {
    throw new Error("Forge Studio returned an invalid creation output grant");
  }
  return value as unknown as StudioCreationOutputGrant;
}

function validateGrantPage(
  result: Record<string, unknown>,
  census: CreationExecutionCensus,
  previous: string | null,
  maxFormatVersion: 5 | 6,
): { grants: StudioCreationOutputGrant[]; nextCursor: string | null } {
  if (
    !hasExactKeys(result, [
      "authority",
      "artifact_snapshot_hash",
      "grants",
      "next_cursor",
    ]) ||
    !isRecord(result.authority) ||
    !hasExactKeys(result.authority, [
      "workspace_id",
      "root_generation",
      "source_revision",
      "workflow_status_hash",
    ]) ||
    result.authority.workspace_id !== census.authority.workspaceId ||
    result.authority.root_generation !== census.authority.rootGeneration ||
    result.authority.source_revision !== census.authority.sourceRevision ||
    result.authority.workflow_status_hash !== census.authority.workflowStatusHash ||
    result.artifact_snapshot_hash !== census.authority.artifactSnapshotHash ||
    !Array.isArray(result.grants) ||
    result.grants.length > PAGE_SIZE ||
    (result.next_cursor !== null && !isEntityId(result.next_cursor))
  ) {
    throw censusError("reply authority or shape is invalid");
  }
  const grants: StudioCreationOutputGrant[] = [];
  let prior = previous;
  for (const value of result.grants) {
    const grant = validateCreationOutputGrant(value);
    if (Number(grant.format_version) > maxFormatVersion) {
      throw censusError("grant format version is outside this listing protocol");
    }
    if (grant.workspace_id !== census.authority.workspaceId) {
      throw censusError("grant belongs to another workspace");
    }
    if (prior !== null && grant.grant_id <= prior) {
      throw censusError("grants are not strictly ordered");
    }
    prior = grant.grant_id;
    grants.push(grant);
  }
  if (
    result.next_cursor !== null &&
    (grants.length === 0 || result.next_cursor !== grants.at(-1)?.grant_id)
  ) {
    throw censusError("next cursor does not match the page");
  }
  return {
    grants,
    nextCursor: result.next_cursor,
  };
}

function validatePublication(value: unknown, version: number): void {
  const common = ["format", "format_version", "id", "content_hash"];
  const keys =
    version === 1
      ? [...common, "inventory_hash"]
      : version === 5
        ? [...common, "archive_sha256", "size_bytes"]
        : version === 6
          ? [...common, "tree_hash"]
        : [...common, "tree_hash"];
  if (
    !isRecord(value) ||
    !hasExactKeys(value, keys) ||
    value.format !== VERSION_PUBLICATION_FORMAT.get(version) ||
    value.format_version !== 1 ||
    !isEntityId(value.id) ||
    !isSha256(value.content_hash)
  ) {
    throw new Error("Forge Studio returned an invalid creation output grant");
  }
  if (version === 1 && !isSha256(value.inventory_hash)) {
    throw new Error("Forge Studio returned an invalid creation output grant");
  }
  if (((version > 1 && version < 5) || version === 6) && !isSha256(value.tree_hash)) {
    throw new Error("Forge Studio returned an invalid creation output grant");
  }
  if (
    version === 5 &&
    (!isSha256(value.archive_sha256) ||
      !isGeneration(value.size_bytes) ||
      Number(value.size_bytes) < 1 ||
      Number(value.size_bytes) > 276_824_064)
  ) {
    throw new Error("Forge Studio returned an invalid creation output grant");
  }
}

function censusError(reason: string): Error {
  return new Error(`Forge Studio output grant census failed closed: ${reason}`);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function hasExactKeys(value: Record<string, unknown>, keys: readonly string[]): boolean {
  const actual = Object.keys(value).sort();
  const expected = [...keys].sort();
  return actual.length === expected.length && actual.every((key, index) => key === expected[index]);
}

function isEntityId(value: unknown): value is string {
  return typeof value === "string" && ENTITY_ID.test(value);
}

function isSha256(value: unknown): value is string {
  return typeof value === "string" && SHA256.test(value);
}

function isGeneration(value: unknown): value is number {
  return Number.isSafeInteger(value) && Number(value) >= 0;
}

function isDisplayName(value: unknown): value is string {
  return (
    typeof value === "string" &&
    value.length >= 1 &&
    value.length <= 128 &&
    value.normalize("NFC") === value &&
    [...value].every((character) => {
      const codePoint = character.codePointAt(0)!;
      return character !== "/" && character !== "\\" && codePoint >= 0x20 && codePoint !== 0x7f;
    })
  );
}

function isUtcTimestamp(value: unknown): value is string {
  return (
    typeof value === "string" &&
    UTC_TIMESTAMP.test(value) &&
    Number.isFinite(Date.parse(value))
  );
}

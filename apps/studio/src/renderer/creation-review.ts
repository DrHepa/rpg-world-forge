import type {
  StudioCreationChangeset,
  StudioCreationChangesetDiffResult,
} from "../shared/studio-api";
import type {
  CreationNavigationKind,
  CreationNavigationState,
} from "./creation-state";

export interface RequiredCreationOperation {
  operation: "create" | "replace" | "delete";
  path: string;
  expectedBaseFileSha256: string | null;
}

export interface CreationChangesetExpectation {
  workspaceId: string;
  requiredOperation: RequiredCreationOperation;
  status?: StudioCreationChangeset["status"];
  expectedRootGeneration?: number;
  expectedSourceRevision?: string;
  expectedWorkflowStatusHash?: string | null;
  immutable?: StudioCreationChangeset;
  terminal?: boolean;
}

export function requireCreationChangeset(
  value: unknown,
  expectation: CreationChangesetExpectation,
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
    !(value.expected_workflow_status_hash === null || isSha256(value.expected_workflow_status_hash)) ||
    !isSha256(value.review_sha256) ||
    !isSha256(value.record_hash) ||
    typeof value.created_at !== "string" ||
    typeof value.updated_at !== "string" ||
    !Array.isArray(value.operations) ||
    value.operations.length < 1 ||
    value.operations.length > 256 ||
    !value.operations.some((operation) =>
      matchesRequiredOperation(operation, expectation.requiredOperation),
    )
  ) {
    throw new Error("Forge Studio returned an invalid creation changeset");
  }
  const record = value as unknown as StudioCreationChangeset;
  if (expectation.terminal && record.status !== "applied") {
    throw new Error("Forge Studio returned a non-terminal creation changeset");
  }
  if (expectation.status !== undefined && record.status !== expectation.status) {
    throw new Error("Forge Studio returned mismatched creation changeset evidence");
  }
  if (expectation.immutable) {
    if (!sameImmutableCreationChangeset(expectation.immutable, record)) {
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

export function requireCreationChangesetDiff(
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
      matchesDiffOperation(operation, changeset.operations[index]),
    )
  ) {
    throw new Error("Forge Studio returned an invalid creation changeset diff");
  }
  return value as unknown as StudioCreationChangesetDiffResult["diff"];
}

export function requireCreationRecoveryTerminal(
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

export function creationChangesetNavigationKind(
  status: StudioCreationChangeset["status"] | undefined,
): CreationNavigationKind | null {
  if (status === "staged") return "staged";
  if (status === "approved") return "approved";
  if (status === "applying" || status === "recovery_required") return "recovery_required";
  return null;
}

export function reportCreationNavigation(
  callback: (state: CreationNavigationState) => void,
  kind: CreationNavigationKind,
): void {
  callback({ blocksNavigation: kind !== "clean", kind });
}

function matchesRequiredOperation(
  value: unknown,
  expected: RequiredCreationOperation,
): boolean {
  if (!isRecord(value) || value.operation !== expected.operation || value.path !== expected.path) {
    return false;
  }
  if (expected.operation === "create") {
    return value.expected_base_file_sha256 === null && value.expected_base_size === null &&
      isSha256(value.proposed_file_sha256) && isNonNegativeInteger(value.proposed_size);
  }
  if (expected.operation === "delete") {
    return value.expected_base_file_sha256 === expected.expectedBaseFileSha256 &&
      isNonNegativeInteger(value.expected_base_size) &&
      value.proposed_file_sha256 === null && value.proposed_size === null;
  }
  return value.expected_base_file_sha256 === expected.expectedBaseFileSha256 &&
    isNonNegativeInteger(value.expected_base_size) &&
    isSha256(value.proposed_file_sha256) && isNonNegativeInteger(value.proposed_size);
}

function matchesDiffOperation(
  value: unknown,
  operation: StudioCreationChangeset["operations"][number] | undefined,
): boolean {
  if (!operation || !isRecord(value)) return false;
  const expectedSize = operation.expected_base_size ?? 0;
  const proposedSize = operation.proposed_size ?? 0;
  return value.operation === operation.operation && value.path === operation.path &&
    value.expected_base_file_sha256 === operation.expected_base_file_sha256 &&
    value.expected_base_size === operation.expected_base_size &&
    value.proposed_file_sha256 === operation.proposed_file_sha256 &&
    value.proposed_size === operation.proposed_size &&
    Number.isSafeInteger(value.size_delta) && value.size_delta === proposedSize - expectedSize;
}

function sameImmutableCreationChangeset(
  left: StudioCreationChangeset,
  right: StudioCreationChangeset,
): boolean {
  return left.changeset_id === right.changeset_id &&
    left.workspace_id === right.workspace_id &&
    left.expected_root_generation === right.expected_root_generation &&
    left.expected_source_revision === right.expected_source_revision &&
    left.proposed_source_revision === right.proposed_source_revision &&
    left.expected_workflow_status_hash === right.expected_workflow_status_hash &&
    left.review_sha256 === right.review_sha256 &&
    left.operations.length === right.operations.length &&
    left.operations.every((operation, index) => sameOperation(operation, right.operations[index]));
}

function sameOperation(
  left: StudioCreationChangeset["operations"][number] | undefined,
  right: StudioCreationChangeset["operations"][number] | undefined,
): boolean {
  return Boolean(left && right && left.operation === right.operation && left.path === right.path &&
    left.expected_base_file_sha256 === right.expected_base_file_sha256 &&
    left.expected_base_size === right.expected_base_size &&
    left.proposed_file_sha256 === right.proposed_file_sha256 &&
    left.proposed_size === right.proposed_size);
}

function isChangesetStatus(value: unknown): value is StudioCreationChangeset["status"] {
  return value === "staged" || value === "approved" || value === "applying" ||
    value === "applied" || value === "rejected" || value === "recovery_required";
}

function isSha256(value: unknown): value is string {
  return typeof value === "string" && /^[0-9a-f]{64}$/u.test(value);
}

function isNonNegativeInteger(value: unknown): value is number {
  return Number.isSafeInteger(value) && Number(value) >= 0;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

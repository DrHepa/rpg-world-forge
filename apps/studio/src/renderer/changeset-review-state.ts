import type { StudioChangeset, StudioChangesetDiff } from "../shared/studio-api";
import { boundedMessage } from "./authoring-state";

export type ChangesetReviewAction = "approve" | "reject" | "apply";
export type ChangesetReviewRequest = "stage" | "open" | ChangesetReviewAction;

export interface StagedDraftSnapshot {
  workspaceId: string;
  generation: number;
  path: string;
  baseSha256: string;
  baseSize: number;
  content: string;
  proposedSha256: string;
}

export interface ChangesetReviewState {
  workspaceId: string | null;
  generation: number;
  selectedChangesetId: string | null;
  record: StudioChangeset | null;
  diff: StudioChangesetDiff | null;
  pending: ChangesetReviewRequest | null;
  requestId: number;
  error: string | null;
  notice: string | null;
}

export type ChangesetReviewStateAction =
  | { type: "workspace-changed"; workspaceId: string; generation: number }
  | {
      type: "stage-started";
      workspaceId: string;
      generation: number;
      requestId: number;
    }
  | {
      type: "open-started";
      workspaceId: string;
      generation: number;
      requestId: number;
      changesetId: string;
    }
  | {
      type: "open-succeeded";
      workspaceId: string;
      generation: number;
      requestId: number;
      changesetId: string;
      record: StudioChangeset;
      diff: StudioChangesetDiff;
    }
  | {
      type: "action-started";
      workspaceId: string;
      generation: number;
      requestId: number;
      changesetId: string;
      action: ChangesetReviewAction;
      expectedReviewSha256: string | null;
    }
  | {
      type: "action-succeeded";
      workspaceId: string;
      generation: number;
      requestId: number;
      changesetId: string;
      action: ChangesetReviewAction;
      previous: StudioChangeset;
      record: StudioChangeset;
    }
  | {
      type: "request-failed";
      workspaceId: string;
      generation: number;
      requestId: number;
      request: ChangesetReviewRequest;
      message: string;
    }
  | { type: "closed"; requestId: number };

export function createInitialChangesetReviewState(): ChangesetReviewState {
  return {
    workspaceId: null,
    generation: 0,
    selectedChangesetId: null,
    record: null,
    diff: null,
    pending: null,
    requestId: 0,
    error: null,
    notice: null,
  };
}

export function changesetReviewReducer(
  state: ChangesetReviewState,
  action: ChangesetReviewStateAction,
): ChangesetReviewState {
  switch (action.type) {
    case "workspace-changed":
      return {
        ...createInitialChangesetReviewState(),
        workspaceId: action.workspaceId,
        generation: action.generation,
        requestId: state.requestId,
      };
    case "stage-started":
      if (!matchesContext(state, action.workspaceId, action.generation)) return state;
      return {
        ...state,
        pending: "stage",
        requestId: action.requestId,
        error: null,
        notice: null,
      };
    case "open-started":
      if (!matchesContext(state, action.workspaceId, action.generation)) return state;
      return {
        ...state,
        selectedChangesetId: action.changesetId,
        record: null,
        diff: null,
        pending: "open",
        requestId: action.requestId,
        error: null,
        notice: null,
      };
    case "open-succeeded": {
      if (!matchesRequest(state, action, "open", action.changesetId)) return state;
      const error = reviewEvidenceError(action.record, action.diff, {
        workspaceId: action.workspaceId,
        changesetId: action.changesetId,
      });
      return error
        ? { ...state, pending: null, error }
        : {
            ...state,
            record: action.record,
            diff: action.diff,
            pending: null,
            error: null,
            notice: "Immutable review evidence loaded.",
          };
    }
    case "action-started":
      if (!matchesContext(state, action.workspaceId, action.generation)) return state;
      if (
        state.selectedChangesetId !== action.changesetId ||
        !state.record ||
        expectedReviewSha256(state.record) !== action.expectedReviewSha256
      ) {
        return state;
      }
      return {
        ...state,
        pending: action.action,
        requestId: action.requestId,
        error: null,
        notice: null,
      };
    case "action-succeeded": {
      if (!matchesRequest(state, action, action.action, action.changesetId)) return state;
      const error = actionCompletionError(action.previous, action.record, action.action);
      return error
        ? { ...state, pending: null, error }
        : {
            ...state,
            record: action.record,
            pending: null,
            error: null,
            notice: actionNotice(action.action),
          };
    }
    case "request-failed":
      if (!matchesRequest(state, action, action.request, state.selectedChangesetId)) {
        if (
          action.request !== "stage" ||
          !matchesContext(state, action.workspaceId, action.generation) ||
          state.requestId !== action.requestId ||
          state.pending !== "stage"
        ) {
          return state;
        }
      }
      return { ...state, pending: null, error: boundedMessage(action.message), notice: null };
    case "closed":
      return {
        ...state,
        selectedChangesetId: null,
        record: null,
        diff: null,
        pending: null,
        requestId: action.requestId,
        error: null,
        notice: null,
      };
  }
}

export function stagedChangesetError(
  record: StudioChangeset,
  snapshot: StagedDraftSnapshot,
): string | null {
  if (record.format_version !== 2) return "A newly staged draft did not return reviewable v2 evidence.";
  if (record.workspace_id !== snapshot.workspaceId || record.status !== "staged") {
    return "The staged changeset did not match the selected workspace and review state.";
  }
  if (record.operations.length !== 1) {
    return "The staged changeset did not contain the single requested source replacement.";
  }
  const operation = record.operations[0];
  if (
    operation.operation !== "replace" ||
    operation.path !== snapshot.path ||
    operation.base_sha256 !== snapshot.baseSha256 ||
    operation.base_size !== snapshot.baseSize ||
    operation.proposed_sha256 !== snapshot.proposedSha256 ||
    operation.size !== utf8ByteLength(snapshot.content)
  ) {
    return "The staged changeset identity did not match the selected draft snapshot.";
  }
  return null;
}

export function reviewEvidenceError(
  record: StudioChangeset,
  diff: StudioChangesetDiff,
  expected: { workspaceId: string; changesetId: string },
): string | null {
  if (
    record.workspace_id !== expected.workspaceId ||
    record.changeset_id !== expected.changesetId ||
    diff.changeset_id !== expected.changesetId
  ) {
    return "Changeset review evidence did not match the selected workspace and identity.";
  }
  if (record.format_version === 1) {
    return diff.changeset_format_version === 1 && !diff.available && diff.review_sha256 === null
      ? null
      : "Legacy changeset evidence returned an incompatible exact diff.";
  }
  if (
    diff.changeset_format_version !== 2 ||
    !diff.available ||
    diff.review_sha256 !== record.review_sha256 ||
    diff.operations.length !== record.operations.length
  ) {
    return "Changeset diff did not match the immutable review identity.";
  }
  for (const [index, operation] of record.operations.entries()) {
    const evidence = diff.operations[index];
    if (
      !evidence ||
      evidence.path !== operation.path ||
      evidence.operation !== operation.operation ||
      evidence.base_sha256 !== operation.base_sha256 ||
      evidence.base_size !== operation.base_size ||
      evidence.proposed_sha256 !== operation.proposed_sha256 ||
      evidence.size !== operation.size
    ) {
      return "Changeset diff operations did not match the reviewed source identities.";
    }
  }
  return null;
}

export function expectedReviewSha256(record: StudioChangeset): string | null {
  return record.format_version === 2 ? record.review_sha256 : null;
}

export function reviewActionUnavailableReason(
  record: StudioChangeset | null,
  diff: StudioChangesetDiff | null,
  action: ChangesetReviewAction,
): string | null {
  if (!record || !diff) return "Load immutable review evidence before taking action.";
  const evidenceError = reviewEvidenceError(record, diff, {
    workspaceId: record.workspace_id,
    changesetId: record.changeset_id,
  });
  if (evidenceError) return evidenceError;
  if (record.format_version === 1) {
    if (action !== "reject") return "Legacy v1 changesets cannot be freshly approved or applied.";
    return ["staged", "approved"].includes(record.status)
      ? null
      : `A ${record.status} changeset cannot be rejected.`;
  }
  if (action === "approve") {
    return record.status === "staged" ? null : "Only a staged changeset can be approved.";
  }
  if (action === "reject") {
    return ["staged", "approved"].includes(record.status)
      ? null
      : `A ${record.status} changeset cannot be rejected.`;
  }
  return record.status === "approved" ? null : "Only an approved changeset can be applied.";
}

export function actionCompletionError(
  previous: StudioChangeset,
  record: StudioChangeset,
  action: ChangesetReviewAction,
): string | null {
  if (action === "approve" && record.status !== "approved") {
    return "Changeset approval returned an unexpected status.";
  }
  if (action === "reject" && record.status !== "rejected") {
    return "Changeset rejection returned an unexpected status.";
  }
  if (action === "apply" && record.status !== "applied") {
    return "Changeset apply returned an unexpected status.";
  }
  if (
    previous.format_version !== record.format_version ||
    previous.changeset_id !== record.changeset_id ||
    previous.workspace_id !== record.workspace_id ||
    previous.created_at !== record.created_at ||
    expectedReviewSha256(previous) !== expectedReviewSha256(record) ||
    operationIdentity(previous) !== operationIdentity(record)
  ) {
    return "Changeset action returned a mismatched review identity.";
  }
  return null;
}

export async function sha256Utf8(value: string): Promise<string> {
  assertUnicodeScalarString(value);
  const subtle = globalThis.crypto?.subtle;
  if (!subtle) throw new Error("Secure SHA-256 is unavailable in this renderer.");
  const digest = await subtle.digest("SHA-256", new TextEncoder().encode(value));
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

function operationIdentity(record: StudioChangeset): string {
  return record.operations
    .map((operation) =>
      [
        operation.path,
        operation.operation,
        operation.base_sha256 ?? "-",
        "base_size" in operation ? operation.base_size : "legacy",
        operation.proposed_sha256 ?? "-",
        operation.size,
      ].join("\u0000"),
    )
    .join("\u0001");
}

function matchesContext(
  state: ChangesetReviewState,
  workspaceId: string,
  generation: number,
): boolean {
  return state.workspaceId === workspaceId && state.generation === generation;
}

function matchesRequest(
  state: ChangesetReviewState,
  action: { workspaceId: string; generation: number; requestId: number },
  request: ChangesetReviewRequest,
  changesetId: string | null,
): boolean {
  return (
    matchesContext(state, action.workspaceId, action.generation) &&
    state.requestId === action.requestId &&
    state.pending === request &&
    state.selectedChangesetId === changesetId
  );
}

function actionNotice(action: ChangesetReviewAction): string {
  if (action === "approve") return "Review approved. Source files remain unchanged.";
  if (action === "reject") return "Review rejected. Source files remain unchanged.";
  return "Approved changeset applied. Refreshing the verified workspace.";
}

function utf8ByteLength(value: string): number {
  return new TextEncoder().encode(value).byteLength;
}

function assertUnicodeScalarString(value: string): void {
  for (let index = 0; index < value.length; index += 1) {
    const code = value.charCodeAt(index);
    if (code >= 0xd800 && code <= 0xdbff) {
      const next = value.charCodeAt(index + 1);
      if (!(next >= 0xdc00 && next <= 0xdfff)) {
        throw new TypeError("Draft content contains an unpaired Unicode surrogate.");
      }
      index += 1;
    } else if (code >= 0xdc00 && code <= 0xdfff) {
      throw new TypeError("Draft content contains an unpaired Unicode surrogate.");
    }
  }
}

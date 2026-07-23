import { describe, expect, it } from "vitest";

import type { StudioChangeset, StudioChangesetDiff } from "../../src/shared/studio-api";
import {
  actionCompletionError,
  changesetReviewReducer,
  createInitialChangesetReviewState,
  reviewActionUnavailableReason,
  sha256Utf8,
  stagedChangesetError,
} from "../../src/renderer/changeset-review-state";

const BASE_SHA = "a".repeat(64);
const PROPOSED_SHA = "b0f3d575b215271a477d7ba5463f4ed4b9176db1a903d4678f3303bc70bdefa6";
const REVIEW_SHA = "c".repeat(64);

describe("changeset review state", () => {
  it("accepts only the exact staged draft identity and rejects same-size different content", async () => {
    const content = '{"id":"world_02"}';
    const snapshot = {
      workspaceId: "workspace_01",
      generation: 1,
      path: "source/world.json",
      baseSha256: BASE_SHA,
      baseSize: 12,
      content,
      proposedSha256: await sha256Utf8(content),
    };
    const exact = v2Record();
    const exactOperation = exact.operations[0];
    if (exactOperation.operation !== "replace") throw new Error("Expected replacement fixture");
    const sameSizeOtherContent = '{"id":"world_03"}';
    expect(new TextEncoder().encode(sameSizeOtherContent)).toHaveLength(
      new TextEncoder().encode(content).byteLength,
    );
    const sameSizeOtherSha = await sha256Utf8(sameSizeOtherContent);
    expect(stagedChangesetError(exact, snapshot)).toBeNull();
    expect(
      stagedChangesetError(
        { ...exact, operations: [{ ...exact.operations[0], path: "source/other.json" }] },
        snapshot,
      ),
    ).toMatch(/identity/u);
    expect(
      stagedChangesetError(
        {
          ...exact,
          operations: [
            { ...exactOperation, proposed_sha256: sameSizeOtherSha },
          ],
        },
        snapshot,
      ),
    ).toMatch(/identity/u);
    await expect(sha256Utf8("\ud800")).rejects.toThrow(/unpaired Unicode surrogate/u);
  });

  it("ignores stale workspace and superseded changeset replies", () => {
    let state = changesetReviewReducer(createInitialChangesetReviewState(), {
      type: "workspace-changed",
      workspaceId: "workspace_01",
      generation: 1,
    });
    state = changesetReviewReducer(state, {
      type: "open-started",
      workspaceId: "workspace_01",
      generation: 1,
      requestId: 1,
      changesetId: "changeset_01",
    });
    state = changesetReviewReducer(state, {
      type: "open-started",
      workspaceId: "workspace_01",
      generation: 1,
      requestId: 2,
      changesetId: "changeset_02",
    });
    const superseded = changesetReviewReducer(state, {
      type: "open-succeeded",
      workspaceId: "workspace_01",
      generation: 1,
      requestId: 1,
      changesetId: "changeset_01",
      record: v2Record(),
      diff: v2Diff(),
    });
    expect(superseded).toBe(state);
    expect(superseded.selectedChangesetId).toBe("changeset_02");
    expect(superseded.pending).toBe("open");

    const moved = changesetReviewReducer(state, {
      type: "workspace-changed",
      workspaceId: "workspace_02",
      generation: 2,
    });
    const staleWorkspace = changesetReviewReducer(moved, {
      type: "open-succeeded",
      workspaceId: "workspace_01",
      generation: 1,
      requestId: 2,
      changesetId: "changeset_02",
      record: v2Record({ changeset_id: "changeset_02" }),
      diff: v2Diff({ changeset_id: "changeset_02" }),
    });
    expect(staleWorkspace).toBe(moved);
  });

  it("requires matching evidence and action completion identities", () => {
    let state = loadedReview();
    state = changesetReviewReducer(state, {
      type: "action-started",
      workspaceId: "workspace_01",
      generation: 1,
      requestId: 2,
      changesetId: "changeset_01",
      action: "approve",
      expectedReviewSha256: REVIEW_SHA,
    });
    state = changesetReviewReducer(state, {
      type: "action-succeeded",
      workspaceId: "workspace_01",
      generation: 1,
      requestId: 2,
      changesetId: "changeset_01",
      action: "approve",
      previous: v2Record(),
      record: v2Record({ status: "approved" }),
    });
    expect(state.record?.status).toBe("approved");
    expect(state.notice).toMatch(/Source files remain unchanged/u);

    expect(
      actionCompletionError(
        v2Record(),
        v2Record({ status: "approved", review_sha256: "d".repeat(64) }),
        "approve",
      ),
    ).toMatch(/mismatched review identity/u);
  });

  it("keeps legacy v1 readable and rejectable but blocks fresh approve and apply", () => {
    const record = v1Record();
    const diff = v1Diff();
    expect(reviewActionUnavailableReason(record, diff, "reject")).toBeNull();
    expect(reviewActionUnavailableReason(record, diff, "approve")).toMatch(/Legacy v1/u);
    expect(reviewActionUnavailableReason(record, diff, "apply")).toMatch(/Legacy v1/u);
  });
});

function loadedReview() {
  let state = changesetReviewReducer(createInitialChangesetReviewState(), {
    type: "workspace-changed",
    workspaceId: "workspace_01",
    generation: 1,
  });
  state = changesetReviewReducer(state, {
    type: "open-started",
    workspaceId: "workspace_01",
    generation: 1,
    requestId: 1,
    changesetId: "changeset_01",
  });
  return changesetReviewReducer(state, {
    type: "open-succeeded",
    workspaceId: "workspace_01",
    generation: 1,
    requestId: 1,
    changesetId: "changeset_01",
    record: v2Record(),
    diff: v2Diff(),
  });
}

function v2Record(overrides: Partial<Extract<StudioChangeset, { format_version: 2 }>> = {}) {
  return {
    format: "rpg-world-forge.studio_changeset" as const,
    format_version: 2 as const,
    changeset_id: "changeset_01",
    workspace_id: "workspace_01",
    status: "staged" as const,
    operations: [
      {
        path: "source/world.json",
        operation: "replace" as const,
        base_sha256: BASE_SHA,
        base_size: 12,
        proposed_sha256: PROPOSED_SHA,
        size: 17,
      },
    ] as [Extract<StudioChangeset, { format_version: 2 }>["operations"][number]],
    review_sha256: REVIEW_SHA,
    created_at: "2026-07-23T00:00:00Z",
    updated_at: "2026-07-23T00:00:00Z",
    ...overrides,
  };
}

function v2Diff(
  overrides: Partial<Extract<StudioChangesetDiff, { changeset_format_version: 2 }>> = {},
) {
  return {
    changeset_id: "changeset_01",
    changeset_format_version: 2 as const,
    available: true as const,
    unavailable_reason: null,
    review_sha256: REVIEW_SHA,
    operations: [
      {
        path: "source/world.json",
        operation: "replace" as const,
        base_sha256: BASE_SHA,
        base_size: 12,
        proposed_sha256: PROPOSED_SHA,
        size: 17,
        text_hunks: [
          {
            base_start: 1,
            base_count: 1,
            proposed_start: 1,
            proposed_count: 1,
            lines: [{ kind: "add" as const, text: "new" }],
          },
        ],
        json_pointer_changes: [{ operation: "replace" as const, pointer: "/id", old_value: "world_01", value: "world_02" }],
      },
    ] as [Extract<StudioChangesetDiff, { changeset_format_version: 2 }>["operations"][number]],
    ...overrides,
  };
}

function v1Record(): Extract<StudioChangeset, { format_version: 1 }> {
  return {
    format: "rpg-world-forge.studio_changeset",
    format_version: 1,
    changeset_id: "legacy_01",
    workspace_id: "workspace_01",
    status: "staged",
    operations: [
      {
        path: "source/world.json",
        operation: "replace",
        base_sha256: BASE_SHA,
        proposed_sha256: PROPOSED_SHA,
        size: 17,
      },
    ],
    created_at: "2026-07-22T00:00:00Z",
    updated_at: "2026-07-22T00:00:00Z",
  };
}

function v1Diff(): Extract<StudioChangesetDiff, { changeset_format_version: 1 }> {
  return {
    changeset_id: "legacy_01",
    changeset_format_version: 1,
    available: false,
    unavailable_reason: "legacy_base_bytes_not_retained",
    review_sha256: null,
    operations: [],
  };
}

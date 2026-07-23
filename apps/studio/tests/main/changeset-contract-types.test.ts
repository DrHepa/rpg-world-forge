import { describe, expect, expectTypeOf, it } from "vitest";

import type {
  ChangesetApplyRequest,
  ChangesetApplyResponse,
  ChangesetApproveRequest,
  ChangesetApproveResponse,
  ChangesetCreateRequest,
  ChangesetCreateResponse,
  ChangesetDiffRequest,
  ChangesetDiffResponse,
  ChangesetGetRequest,
  ChangesetGetResponse,
  ChangesetListRequest,
  ChangesetListResponse,
  ChangesetRejectRequest,
  ChangesetRejectResponse,
  ForgeStudioReviewableFileChangesetV2,
} from "../../src/generated/studio-protocol";
import type {
  StudioRequestParams,
  StudioSuccessForMethod,
} from "../../src/main/ndjson-supervisor";

const common = {
  format: "rpg-world-forge.studio_changeset",
  changeset_id: "changeset_01",
  workspace_id: "workspace_01",
  status: "staged",
  created_at: "2026-07-23T00:00:00Z",
  updated_at: "2026-07-23T00:00:00Z",
} as const;

describe("generated changeset contracts", () => {
  it("keeps v1 and v2 closed while requiring the v2 review digest", () => {
    const v1: ForgeStudioReviewableFileChangesetV2 = {
      ...common,
      format_version: 1,
      operations: [
        {
          path: "source/lore/entry.md",
          operation: "replace",
          base_sha256: "a".repeat(64),
          proposed_sha256: "b".repeat(64),
          size: 4,
        },
      ],
    };
    const v2: ForgeStudioReviewableFileChangesetV2 = {
      ...common,
      format_version: 2,
      operations: [
        {
          path: "source/lore/entry.md",
          operation: "replace",
          base_sha256: "a".repeat(64),
          base_size: 4,
          proposed_sha256: "b".repeat(64),
          size: 4,
        },
      ],
      review_sha256: "c".repeat(64),
    };

    // @ts-expect-error v2 changesets require their canonical review digest.
    const missingReview: ForgeStudioReviewableFileChangesetV2 = {
      ...common,
      format_version: 2,
      operations: v2.operations,
    };
    const extraKey: ForgeStudioReviewableFileChangesetV2 = {
      ...v2,
      // @ts-expect-error persisted changesets are closed records.
      provider: "openai",
    };
    const extraLegacyKey: ForgeStudioReviewableFileChangesetV2 = {
      ...v1,
      // @ts-expect-error legacy changesets remain closed records.
      provider: "openai",
    };

    expect(v1.format_version).toBe(1);
    expect(v2.review_sha256).toHaveLength(64);
    expect(missingReview.format_version).toBe(2);
    expect(extraKey.format_version).toBe(2);
    expect(extraLegacyKey.format_version).toBe(1);
  });

  it("maps every exact changeset transport method without legacy fallbacks", () => {
    expectTypeOf<StudioRequestParams<"changeset.create">>().toEqualTypeOf<
      ChangesetCreateRequest["params"]
    >();
    expectTypeOf<StudioRequestParams<"changeset.get">>().toEqualTypeOf<
      ChangesetGetRequest["params"]
    >();
    expectTypeOf<StudioRequestParams<"changeset.list">>().toEqualTypeOf<
      ChangesetListRequest["params"]
    >();
    expectTypeOf<StudioRequestParams<"changeset.diff">>().toEqualTypeOf<
      ChangesetDiffRequest["params"]
    >();
    expectTypeOf<StudioRequestParams<"changeset.approve">>().toEqualTypeOf<
      ChangesetApproveRequest["params"]
    >();
    expectTypeOf<StudioRequestParams<"changeset.reject">>().toEqualTypeOf<
      ChangesetRejectRequest["params"]
    >();
    expectTypeOf<StudioRequestParams<"changeset.apply">>().toEqualTypeOf<
      ChangesetApplyRequest["params"]
    >();

    expectTypeOf<StudioSuccessForMethod<"changeset.create">>().toEqualTypeOf<
      ChangesetCreateResponse
    >();
    expectTypeOf<StudioSuccessForMethod<"changeset.get">>().toEqualTypeOf<
      ChangesetGetResponse
    >();
    expectTypeOf<StudioSuccessForMethod<"changeset.list">>().toEqualTypeOf<
      ChangesetListResponse
    >();
    expectTypeOf<StudioSuccessForMethod<"changeset.diff">>().toEqualTypeOf<
      ChangesetDiffResponse
    >();
    expectTypeOf<StudioSuccessForMethod<"changeset.approve">>().toEqualTypeOf<
      ChangesetApproveResponse
    >();
    expectTypeOf<StudioSuccessForMethod<"changeset.reject">>().toEqualTypeOf<
      ChangesetRejectResponse
    >();
    expectTypeOf<StudioSuccessForMethod<"changeset.apply">>().toEqualTypeOf<
      ChangesetApplyResponse
    >();
  });
});

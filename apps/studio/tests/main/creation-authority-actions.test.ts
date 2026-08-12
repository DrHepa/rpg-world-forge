import { createHash } from "node:crypto";

import { describe, expect, it, vi } from "vitest";

import {
  buildAuthorityReviewModalOptions,
  deriveAssetQaReviewJobCreateParams,
  deriveAssetReleaseAuthorizeJobCreateParams,
  deriveRuntimeHeadlessVerifyJobCreateParams,
  readVerifiedCreationPreviewBytes,
  validateAuthorityReviewReply,
  validateRendererCreationJobCreateBoundary,
} from "../../src/main/creation-authority-actions";

const authority = {
  workspaceId: "workspace_01",
  rootGeneration: 7,
  sourceRevision: "a".repeat(64),
  workflowStatusHash: "b".repeat(64),
  artifactSnapshotHash: "c".repeat(64),
};

describe("main-owned creation authority action helpers", () => {
  it("accepts only exact modal replies from the active nonce and decision count", () => {
    expect(
      validateAuthorityReviewReply(
        {
          nonce: "nonce_01",
          action: "approve",
          criterionDecisions: ["approved", "rejected"],
        },
        { expectedNonce: "nonce_01", expectedDecisionCount: 2 },
      ),
    ).toEqual({
      nonce: "nonce_01",
      action: "approve",
      criterionDecisions: ["approved", "rejected"],
    });

    for (const reply of [
      {
        nonce: "wrong",
        action: "approve",
        criterionDecisions: ["approved", "rejected"],
      },
      {
        nonce: "nonce_01",
        action: "approve",
        criterionDecisions: ["approved"],
      },
      {
        nonce: "nonce_01",
        action: "approve",
        criterionDecisions: ["approved", "rejected"],
        blockers: ["renderer_smuggled"],
      },
      {
        nonce: "nonce_01",
        action: "reject",
        criterionDecisions: ["passed", "failed"],
      },
    ]) {
      expect(() =>
        validateAuthorityReviewReply(reply, {
          expectedNonce: "nonce_01",
          expectedDecisionCount: 2,
        }),
      ).toThrow();
    }

    expect(
      validateAuthorityReviewReply(
        { nonce: "nonce_01", action: "cancel", criterionDecisions: [] },
        { expectedNonce: "nonce_01", expectedDecisionCount: 0 },
      ),
    ).toEqual({
      nonce: "nonce_01",
      action: "cancel",
      criterionDecisions: [],
    });
  });

  it("denies generic renderer-originated v10-v12 creation job passthrough", () => {
    for (const operation of [
      "asset.qa.review",
      "asset.release.authorize",
      "runtime.headless.verify",
    ]) {
      expect(() =>
        validateRendererCreationJobCreateBoundary({ operation }),
      ).toThrow(/main-owned authority/u);
    }
    expect(
      validateRendererCreationJobCreateBoundary({
        operation: "runtime.compose",
      }),
    ).toEqual({ operation: "runtime.compose" });
  });

  it("derives exact v10 snake_case review params from a main-owned gesture", () => {
    expect(
      deriveAssetQaReviewJobCreateParams({
        authority,
        qaReportArtifactId: "artifact_qa_01",
        outputRole: "texture",
        reviewReceiptId: "review_receipt_01",
        criterionHashes: ["1".repeat(64), "2".repeat(64)],
        gesture: {
          nonce: "nonce_01",
          action: "approve",
          criterionDecisions: ["approved", "rejected"],
        },
      }),
    ).toEqual({
      workspace_id: "workspace_01",
      operation: "asset.qa.review",
      expected_root_generation: 7,
      expected_source_revision: "a".repeat(64),
      expected_workflow_status_hash: "b".repeat(64),
      expected_artifact_snapshot_hash: "c".repeat(64),
      qa_report_artifact_id: "artifact_qa_01",
      output_role: "texture",
      review_receipt_id: "review_receipt_01",
      decisions: ["approved", "rejected"],
      blockers: ["criterion_rejected"],
    });
  });

  it("derives exact v11 release authority params from retained review artifacts and target grant", () => {
    expect(
      deriveAssetReleaseAuthorizeJobCreateParams({
        authority,
        reviewArtifacts: [
          {
            artifactId: "artifact_review_b",
            receiptId: "review_receipt_b",
            status: "approved",
          },
          {
            artifactId: "artifact_review_a",
            receiptId: "review_receipt_a",
            status: "rejected",
          },
        ],
        targetGrant: { grantId: "grant_assetpack_01", generation: 5 },
      }),
    ).toEqual({
      workspace_id: "workspace_01",
      operation: "asset.release.authorize",
      expected_root_generation: 7,
      expected_source_revision: "a".repeat(64),
      expected_workflow_status_hash: "b".repeat(64),
      expected_artifact_snapshot_hash: "c".repeat(64),
      review_receipt_artifact_ids: ["artifact_review_a", "artifact_review_b"],
      manifest_id:
        "release_manifest_6f8c50a199e810eb3403dbc4952e31c1884f86b452627948",
      assetpack_id:
        "assetpack_6f8c50a199e810eb3403dbc4952e31c1884f86b452627948",
      release_authority_id:
        "release_authority_6f8c50a199e810eb3403dbc4952e31c1884f86b452627948",
      blockers: ["review_rejected"],
      target_grant_id: "grant_assetpack_01",
      expected_target_grant_generation: 5,
    });
  });

  it("derives exact v12 headless params from inspected artifact lineage and current grants", () => {
    expect(
      deriveRuntimeHeadlessVerifyJobCreateParams({
        authority,
        sourceGrant: { grantId: "grant_runtime_source", generation: 3 },
        targetGrant: { grantId: "grant_headless_target", generation: 2 },
        platformId: "platform:linux_x86_64",
        artifacts: {
          gamepack: "artifact_gamepack",
          assetInventory: "artifact_inventory",
          assetpack: "artifact_assetpack",
          assetReleaseAuthority: "artifact_release_authority",
          runtimeSnapshot: "artifact_snapshot",
          runtimeAdapterRegistry: "artifact_registry",
          runtimeComposition: "artifact_composition",
          runtimeBundle: "artifact_runtime_bundle",
          headlessScript: "artifact_script",
        },
      }),
    ).toEqual({
      workspace_id: "workspace_01",
      operation: "runtime.headless.verify",
      expected_root_generation: 7,
      expected_source_revision: "a".repeat(64),
      expected_workflow_status_hash: "b".repeat(64),
      expected_artifact_snapshot_hash: "c".repeat(64),
      gamepack_artifact_id: "artifact_gamepack",
      asset_inventory_artifact_id: "artifact_inventory",
      assetpack_artifact_id: "artifact_assetpack",
      asset_release_authority_artifact_id: "artifact_release_authority",
      runtime_snapshot_artifact_id: "artifact_snapshot",
      runtime_adapter_registry_artifact_id: "artifact_registry",
      runtime_composition_artifact_id: "artifact_composition",
      runtime_bundle_artifact_id: "artifact_runtime_bundle",
      source_grant_id: "grant_runtime_source",
      expected_source_grant_generation: 3,
      platform_id: "platform:linux_x86_64",
      headless_script_artifact_id: "artifact_script",
      target_grant_id: "grant_headless_target",
      expected_target_grant_generation: 2,
    });
  });

  it("builds a sandboxed local authority modal with closed web preferences and CSP", () => {
    expect(
      buildAuthorityReviewModalOptions({
        parent: {},
        preloadPath: "/app/dist-electron/authority-modal/preload.cjs",
      }),
    ).toMatchObject({
      parent: {},
      modal: true,
      show: false,
      webPreferences: {
        sandbox: true,
        contextIsolation: true,
        nodeIntegration: false,
        webviewTag: false,
      },
    });
  });

  it("reads all preview chunks and verifies declared byte length and cumulative SHA-256", async () => {
    const first = Buffer.from("hello ");
    const second = Buffer.from("world");
    const all = Buffer.concat([first, second]);
    const sha256 = createHash("sha256").update(all).digest("hex");
    const read = vi
      .fn()
      .mockResolvedValueOnce({
        sequence: 0,
        dataBase64: first.toString("base64"),
        cumulativeBytes: first.length,
        cumulativeSha256: createHash("sha256").update(first).digest("hex"),
        eof: false,
      })
      .mockResolvedValueOnce({
        sequence: 1,
        dataBase64: second.toString("base64"),
        cumulativeBytes: all.length,
        cumulativeSha256: sha256,
        eof: true,
      });

    await expect(
      readVerifiedCreationPreviewBytes({
        handle: "H".repeat(43),
        byteLength: all.length,
        sha256,
        read,
      }),
    ).resolves.toEqual(new Uint8Array(all));
    expect(read).toHaveBeenCalledTimes(2);

    await expect(
      readVerifiedCreationPreviewBytes({
        handle: "H".repeat(43),
        byteLength: all.length + 1,
        sha256,
        read: vi.fn().mockResolvedValue({
          sequence: 0,
          dataBase64: all.toString("base64"),
          cumulativeBytes: all.length,
          cumulativeSha256: sha256,
          eof: true,
        }),
      }),
    ).rejects.toThrow(/byte length/u);
  });
});

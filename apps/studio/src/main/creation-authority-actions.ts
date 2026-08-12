import { createHash } from "node:crypto";

import type { JobCreateParams as StudioV5CreationJobCreateParams } from "../generated/studio-protocol-v5";

export const AUTHORITY_JOB_OPERATIONS = new Set([
    "asset.qa.review",
    "asset.release.authorize",
    "runtime.headless.verify",
] as const);

export type AuthorityJobOperation =
    (typeof AUTHORITY_JOB_OPERATIONS extends ReadonlySet<infer T> ? T : never);

export type AuthorityReviewAction = "approve" | "reject" | "cancel";
export type AuthorityCriterionDecision = "approved" | "rejected";

export interface AuthorityReviewReply {
    nonce: string;
    action: AuthorityReviewAction;
    criterionDecisions: AuthorityCriterionDecision[];
}

export interface AuthorityGestureExpectation {
    expectedNonce: string;
    expectedDecisionCount: number;
}

export interface CreationAuthoritySnapshot {
    workspaceId: string;
    rootGeneration: number;
    sourceRevision: string;
    workflowStatusHash: string | null;
    artifactSnapshotHash: string;
}

export interface DeriveAssetQaReviewParamsInput {
    authority: CreationAuthoritySnapshot;
    qaReportArtifactId: string;
    outputRole: string;
    reviewReceiptId: string;
    criterionHashes: readonly string[];
    gesture: AuthorityReviewReply;
}

export interface AuthorityReviewArtifactSelection {
    artifactId: string;
    receiptId: string;
    status: "approved" | "rejected";
}

export interface AuthorityGrantSelection {
    grantId: string;
    generation: number;
}

export interface DeriveAssetReleaseAuthorizeParamsInput {
    authority: CreationAuthoritySnapshot;
    reviewArtifacts: readonly AuthorityReviewArtifactSelection[];
    targetGrant: AuthorityGrantSelection;
}

export interface RuntimeHeadlessArtifactSelections {
    gamepack: string;
    assetInventory: string;
    assetpack: string;
    assetReleaseAuthority: string;
    runtimeSnapshot: string;
    runtimeAdapterRegistry: string;
    runtimeComposition: string;
    runtimeBundle: string;
    headlessScript: string;
}

export interface DeriveRuntimeHeadlessVerifyParamsInput {
    authority: CreationAuthoritySnapshot;
    artifacts: RuntimeHeadlessArtifactSelections;
    sourceGrant: AuthorityGrantSelection;
    targetGrant: AuthorityGrantSelection;
    platformId: "platform:linux_x86_64" | "platform:windows_x86_64";
}

export interface VerifiedPreviewReadChunk {
    sequence: number;
    dataBase64: string;
    cumulativeBytes: number;
    cumulativeSha256: string;
    eof: boolean;
}

export interface BuildAuthorityReviewModalOptionsInput {
    parent: unknown;
    preloadPath: string;
}

export interface ReadVerifiedPreviewBytesInput {
    handle: string;
    byteLength: number;
    sha256: string;
    read: (handle: string, sequence: number) => Promise<VerifiedPreviewReadChunk>;
}

const AUTHORITY_REVIEW_REPLY_KEYS = [
    "action",
    "criterionDecisions",
    "nonce",
] as const;
const SHA256_PATTERN = /^[0-9a-f]{64}$/u;
const MAX_PREVIEW_CHUNKS = 1024;

export function validateAuthorityReviewReply(
    value: unknown,
    expectation: AuthorityGestureExpectation,
): AuthorityReviewReply {
    const reply = requireClosedRecord(value, AUTHORITY_REVIEW_REPLY_KEYS);
    if (reply.nonce !== expectation.expectedNonce) {
        throw new TypeError("Authority review reply nonce is invalid");
    }
    if (
        reply.action !== "approve" &&
        reply.action !== "reject" &&
        reply.action !== "cancel"
    ) {
        throw new TypeError("Authority review reply action is invalid");
    }
    if (
        !Array.isArray(reply.criterionDecisions) ||
        reply.criterionDecisions.length !== expectation.expectedDecisionCount
    ) {
        throw new TypeError("Authority review reply decision count is invalid");
    }
    const rawCriterionDecisions = reply.criterionDecisions as readonly unknown[];
    const criterionDecisions: AuthorityCriterionDecision[] = [];
    for (const decision of rawCriterionDecisions) {
        if (decision !== "approved" && decision !== "rejected") {
            throw new TypeError(
                "Authority review criterion decision is invalid",
            );
        }
        criterionDecisions.push(decision);
    }
    return {
        nonce: String(reply.nonce),
        action: reply.action,
        criterionDecisions,
    };
}

export function validateRendererCreationJobCreateBoundary(
    value: unknown,
): unknown {
    if (!isRecord(value)) {
        throw new TypeError("Renderer creation job request is invalid");
    }
    if (
        typeof value.operation === "string" &&
        AUTHORITY_JOB_OPERATIONS.has(value.operation as AuthorityJobOperation)
    ) {
        throw new TypeError(
            "Renderer creation_job.create for main-owned authority operations is denied",
        );
    }
    return value;
}

export function deriveAssetQaReviewJobCreateParams({
    authority,
    qaReportArtifactId,
    outputRole,
    reviewReceiptId,
    criterionHashes,
    gesture,
}: DeriveAssetQaReviewParamsInput): StudioV5CreationJobCreateParams {
    if (gesture.action === "cancel") {
        throw new TypeError("Cancelled authority gestures do not create jobs");
    }
    if (gesture.criterionDecisions.length !== criterionHashes.length) {
        throw new TypeError("Authority review gesture decision count drifted");
    }
    if (gesture.criterionDecisions.length < 1) {
        throw new TypeError("Authority review requires at least one criterion");
    }
    const blockers =
        gesture.action === "reject" ||
        gesture.criterionDecisions.includes("rejected")
            ? ["criterion_rejected"]
            : [];
    const decisions = [...gesture.criterionDecisions] as [
        AuthorityCriterionDecision,
        ...AuthorityCriterionDecision[],
    ];
    const params: StudioV5CreationJobCreateParams = {
        workspace_id: authority.workspaceId,
        operation: "asset.qa.review",
        expected_root_generation: authority.rootGeneration,
        expected_source_revision: authority.sourceRevision,
        expected_workflow_status_hash: authority.workflowStatusHash,
        expected_artifact_snapshot_hash: authority.artifactSnapshotHash,
        qa_report_artifact_id: qaReportArtifactId,
        output_role: outputRole,
        review_receipt_id: reviewReceiptId,
        decisions,
        blockers,
    };
    return params;
}

export function deriveAssetReleaseAuthorizeJobCreateParams({
    authority,
    reviewArtifacts,
    targetGrant,
}: DeriveAssetReleaseAuthorizeParamsInput): StudioV5CreationJobCreateParams {
    if (reviewArtifacts.length < 1 || reviewArtifacts.length > 128) {
        throw new TypeError("Authority release review artifact count is invalid");
    }
    const canonicalReviews = [...reviewArtifacts].sort((left, right) =>
        left.receiptId.localeCompare(right.receiptId),
    );
    const reviewIds = canonicalReviews.map((review) => review.receiptId);
    if (new Set(reviewIds).size !== reviewIds.length) {
        throw new TypeError("Authority release review receipts are not unique");
    }
    const artifactIds = canonicalReviews.map((review) => review.artifactId);
    const digest = stableAuthorityDigest({
        authority,
        reviewArtifacts: canonicalReviews,
        targetGrant,
    });
    const params: StudioV5CreationJobCreateParams = {
        workspace_id: authority.workspaceId,
        operation: "asset.release.authorize",
        expected_root_generation: authority.rootGeneration,
        expected_source_revision: authority.sourceRevision,
        expected_workflow_status_hash: authority.workflowStatusHash,
        expected_artifact_snapshot_hash: authority.artifactSnapshotHash,
        review_receipt_artifact_ids: artifactIds as [string, ...string[]],
        manifest_id: `release_manifest_${digest}`,
        assetpack_id: `assetpack_${digest}`,
        release_authority_id: `release_authority_${digest}`,
        blockers: canonicalReviews.some((review) => review.status === "rejected")
            ? ["review_rejected"]
            : [],
        target_grant_id: targetGrant.grantId,
        expected_target_grant_generation: targetGrant.generation,
    };
    return params;
}

export function deriveRuntimeHeadlessVerifyJobCreateParams({
    authority,
    artifacts,
    sourceGrant,
    targetGrant,
    platformId,
}: DeriveRuntimeHeadlessVerifyParamsInput): StudioV5CreationJobCreateParams {
    if (sourceGrant.grantId === targetGrant.grantId) {
        throw new TypeError("Authority headless grants must be distinct");
    }
    const params: StudioV5CreationJobCreateParams = {
        workspace_id: authority.workspaceId,
        operation: "runtime.headless.verify",
        expected_root_generation: authority.rootGeneration,
        expected_source_revision: authority.sourceRevision,
        expected_workflow_status_hash: authority.workflowStatusHash,
        expected_artifact_snapshot_hash: authority.artifactSnapshotHash,
        gamepack_artifact_id: artifacts.gamepack,
        asset_inventory_artifact_id: artifacts.assetInventory,
        assetpack_artifact_id: artifacts.assetpack,
        asset_release_authority_artifact_id: artifacts.assetReleaseAuthority,
        runtime_snapshot_artifact_id: artifacts.runtimeSnapshot,
        runtime_adapter_registry_artifact_id: artifacts.runtimeAdapterRegistry,
        runtime_composition_artifact_id: artifacts.runtimeComposition,
        runtime_bundle_artifact_id: artifacts.runtimeBundle,
        source_grant_id: sourceGrant.grantId,
        expected_source_grant_generation: sourceGrant.generation,
        platform_id: platformId,
        headless_script_artifact_id: artifacts.headlessScript,
        target_grant_id: targetGrant.grantId,
        expected_target_grant_generation: targetGrant.generation,
    };
    return params;
}

export function buildAuthorityReviewModalOptions({
    parent,
    preloadPath,
}: BuildAuthorityReviewModalOptionsInput): Record<string, unknown> {
    return {
        parent,
        modal: true,
        width: 720,
        height: 620,
        minWidth: 520,
        minHeight: 420,
        show: false,
        title: "World Forge Authority Review",
        autoHideMenuBar: true,
        backgroundColor: "#11151c",
        webPreferences: {
            preload: preloadPath,
            sandbox: true,
            contextIsolation: true,
            nodeIntegration: false,
            webSecurity: true,
            allowRunningInsecureContent: false,
            webviewTag: false,
            spellcheck: false,
            safeDialogs: true,
        },
    };
}

export async function readVerifiedCreationPreviewBytes({
    handle,
    byteLength,
    sha256,
    read,
}: ReadVerifiedPreviewBytesInput): Promise<Uint8Array> {
    if (!Number.isSafeInteger(byteLength) || byteLength < 0) {
        throw new TypeError("Creation preview byte length is invalid");
    }
    if (!SHA256_PATTERN.test(sha256)) {
        throw new TypeError("Creation preview SHA-256 is invalid");
    }
    const digest = createHash("sha256");
    const chunks: Buffer[] = [];
    let cumulativeBytes = 0;
    for (let sequence = 0; sequence < MAX_PREVIEW_CHUNKS; sequence += 1) {
        const chunk = await read(handle, sequence);
        if (chunk.sequence !== sequence) {
            throw new Error("Creation preview sequence is not canonical");
        }
        const bytes = Buffer.from(chunk.dataBase64, "base64");
        if (bytes.toString("base64") !== chunk.dataBase64) {
            throw new Error("Creation preview chunk is not canonical base64");
        }
        cumulativeBytes += bytes.length;
        digest.update(bytes);
        const cumulativeSha256 = digest.copy().digest("hex");
        if (chunk.cumulativeBytes !== cumulativeBytes) {
            throw new Error("Creation preview cumulative byte length mismatch");
        }
        if (chunk.cumulativeSha256 !== cumulativeSha256) {
            throw new Error("Creation preview cumulative SHA-256 mismatch");
        }
        chunks.push(bytes);
        if (chunk.eof) {
            const all = Buffer.concat(chunks);
            if (all.length !== byteLength) {
                throw new Error("Creation preview byte length mismatch");
            }
            if (createHash("sha256").update(all).digest("hex") !== sha256) {
                throw new Error("Creation preview SHA-256 mismatch");
            }
            return new Uint8Array(all);
        }
    }
    throw new Error("Creation preview did not terminate within chunk limit");
}

function requireClosedRecord<const TKeys extends readonly string[]>(
    value: unknown,
    expectedKeys: TKeys,
): Record<TKeys[number], unknown> {
    if (!isRecord(value)) {
        throw new TypeError("Authority review reply is invalid");
    }
    const actual = Object.keys(value).sort();
    const expected = [...expectedKeys].sort();
    if (
        actual.length !== expected.length ||
        actual.some((key, index) => key !== expected[index])
    ) {
        throw new TypeError("Authority review reply fields are invalid");
    }
    return value;
}

function stableAuthorityDigest(value: unknown): string {
    return createHash("sha256")
        .update(JSON.stringify(value))
        .digest("hex")
        .slice(0, 48);
}

function isRecord(value: unknown): value is Record<string, unknown> {
    return typeof value === "object" && value !== null && !Array.isArray(value);
}

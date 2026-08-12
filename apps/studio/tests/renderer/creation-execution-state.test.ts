import { describe, expect, it, vi } from "vitest";

import {
    creationExecutionAuthorityKey,
    findPendingCompileJob,
    loadCreationExecutionCensus,
    projectCreationJob,
    recoveryActionsForCreationJob,
    type CreationAuthorityJobProjection,
    type CreationExecutionAuthority,
} from "../../src/renderer/creation-execution-state";
import type {
    ForgeStudioApi,
    StudioCreationWorkspace,
} from "../../src/shared/studio-api";

const SOURCE = "a".repeat(64);
const SNAPSHOT = "b".repeat(64);
const RECORD = "c".repeat(64);

describe("creation execution state", () => {
    it("loads active and candidate censuses separately and exposes only those lifecycles", async () => {
        const active = artifact("active_01", "active");
        const candidate = artifact("candidate_01", "candidate");
        const api = evidenceApi({ active: [active], candidate: [candidate] });

        const census = await loadCreationExecutionCensus(
            api,
            workspace(),
            null,
        );

        expect(census.authority).toEqual(authority());
        expect(census.activeArtifacts.map((item) => item.artifact_id)).toEqual([
            "active_01",
        ]);
        expect(
            census.candidateArtifacts.map((item) => item.artifact_id),
        ).toEqual(["candidate_01"]);
        expect(
            census.selectableArtifacts.map((item) => item.artifact_id),
        ).toEqual(["active_01", "candidate_01"]);
        expect(api.listCreationArtifacts.mock.calls[0]?.[0]).toEqual({
            workspaceId: "workspace_01",
            expectedRootGeneration: 3,
            expectedSourceRevision: SOURCE,
            expectedWorkflowStatusHash: null,
            expectedArtifactSnapshotHash: SNAPSHOT,
            lifecycle: "active",
            cursor: null,
            limit: 64,
        });
        expect(api.listCreationArtifacts.mock.calls[1]?.[0]).toEqual({
            workspaceId: "workspace_01",
            expectedRootGeneration: 3,
            expectedSourceRevision: SOURCE,
            expectedWorkflowStatusHash: null,
            expectedArtifactSnapshotHash: SNAPSHOT,
            lifecycle: "candidate",
            cursor: null,
            limit: 64,
        });
    });

    it("rejects incomplete counts and duplicate IDs across active and candidate censuses", async () => {
        const incomplete = evidenceApi(
            { active: [], candidate: [] },
            { active: 1 },
        );
        await expect(
            loadCreationExecutionCensus(incomplete, workspace(), null),
        ).rejects.toThrow("incomplete active artifact closure");

        const duplicate = artifact("shared_01", "active");
        const duplicateApi = evidenceApi({
            active: [duplicate],
            candidate: [{ ...duplicate, lifecycle: "candidate" }],
        });
        await expect(
            loadCreationExecutionCensus(duplicateApi, workspace(), null),
        ).rejects.toThrow(
            "duplicate creation artifact evidence across active and candidate",
        );
    });

    it("keys authority with the artifact snapshot and rejects malformed job authority", () => {
        expect(creationExecutionAuthorityKey(authority())).toContain(SNAPSHOT);
        expect(
            projectCreationJob(
                creationJob({
                    authority: {
                        ...jobAuthority(),
                        artifact_snapshot_hash: "d".repeat(64),
                    },
                }),
                "workspace_01",
                authority(),
            ),
        ).toBeNull();
        expect(
            projectCreationJob(creationJob(), "workspace_01", authority())
                ?.authority,
        ).toEqual(authority());
    });

    it("finds an identical pending compilation through bounded ascending pages", async () => {
        const first = creationJob({
            job_id: "older_asset",
            operation: "artifact.admit",
        });
        const matching = creationJob({
            job_id: "compile_current",
            state: "running",
        });
        const listCreationJobs = vi
            .fn()
            .mockResolvedValueOnce(
                v4("creation_job.list", { jobs: [first], next_sequence: 4 }),
            )
            .mockResolvedValueOnce(
                v4("creation_job.list", { jobs: [], next_sequence: null }),
            )
            .mockResolvedValueOnce(
                v4("creation_job.list", {
                    jobs: [matching],
                    next_sequence: null,
                }),
            );
        const api = { listCreationJobs } as unknown as ForgeStudioApi;

        const found = await findPendingCompileJob(api, authority());

        expect(found?.job_id).toBe("compile_current");
        expect(listCreationJobs).toHaveBeenNthCalledWith(1, {
            workspaceId: "workspace_01",
            state: "queued",
            afterSequence: 0,
            limit: 8,
        });
        expect(listCreationJobs).toHaveBeenNthCalledWith(2, {
            workspaceId: "workspace_01",
            state: "queued",
            afterSequence: 4,
            limit: 8,
        });
        expect(listCreationJobs).toHaveBeenNthCalledWith(3, {
            workspaceId: "workspace_01",
            state: "running",
            afterSequence: 0,
            limit: 8,
        });
    });

    it("derives only context-valid CAS recovery actions", () => {
        expect(
            recoveryActionsForCreationJob(
                creationJob({ state: "queued" }),
                authority(),
            ),
        ).toEqual(["cancel"]);
        expect(
            recoveryActionsForCreationJob(
                creationJob({ state: "orphaned", progress: "orphaned" }),
                authority(),
            ),
        ).toEqual(["resume", "rollback"]);
        expect(
            recoveryActionsForCreationJob(
                creationJob({
                    state: "succeeded",
                    progress: "cleanup_pending",
                    result: result({ cleanup_pending: true }),
                }),
                authority(),
            ),
        ).toEqual(["cleanup"]);
        expect(
            recoveryActionsForCreationJob(
                creationJob({
                    state: "failed",
                    progress: "failed",
                    result: null,
                    error: {
                        code: "worker_crashed",
                        message: "Stopped",
                        retryable: true,
                    },
                }),
                authority(),
            ),
        ).toEqual(["retry"]);
        expect(
            recoveryActionsForCreationJob(
                creationJob({
                    authority: { ...jobAuthority(), root_generation: 4 },
                }),
                authority(),
            ),
        ).toEqual(["cancel"]);
        expect(
            recoveryActionsForCreationJob(
                creationJob({
                    authority: { ...jobAuthority(), root_generation: 4 },
                    state: "orphaned",
                    progress: "orphaned",
                }),
                authority(),
            ),
        ).toEqual(["rollback"]);
        expect(
            recoveryActionsForCreationJob(
                creationJob({
                    authority: { ...jobAuthority(), root_generation: 4 },
                    state: "failed",
                    progress: "failed",
                    result: null,
                    error: {
                        code: "worker_crashed",
                        message: "Stopped",
                        retryable: true,
                    },
                }),
                authority(),
            ),
        ).toEqual([]);
    });

    it("accepts only exact v10-v12 authority operation/version pairs and closed outcomes", () => {
        const review = creationJob({
            format_version: 10,
            operation: "asset.qa.review",
            result: authorityResult({
                review_status: "approved",
                review_receipt: {
                    format: "world-forge.asset_qa_review_receipt",
                    format_version: 1,
                    review_receipt_id: "review_receipt_01",
                    content_hash: "d".repeat(64),
                },
            }),
        });
        const projectedReview = projectCreationJob(review, "workspace_01", authority()) as CreationAuthorityJobProjection;
        expect(projectedReview.authorityOutcome).toEqual({
            kind: "asset_qa_review",
            status: "approved",
            reviewReceiptArtifactIds: ["review_receipt_01"],
            blocked: false,
        });

        const release = creationJob({
            format_version: 11,
            operation: "asset.release.authorize",
            result: authorityResult({
                output_artifact_ids: ["artifact_manifest", "artifact_assetpack", "artifact_release_authority"],
                release_status: "authorized",
                publication: {
                    grant_id: "grant_assetpack",
                    grant_generation: 1,
                    kind: "generic_assetpack_directory",
                    state: "published",
                    assetpack: {
                        format: "world-forge.assetpack",
                        format_version: 1,
                        id: "assetpack_01",
                        content_hash: "e".repeat(64),
                        inventory_hash: "f".repeat(64),
                    },
                },
                asset_manifest: { manifest_id: "manifest_01", content_hash: "a".repeat(64) },
                assetpack: { assetpack_id: "assetpack_01", content_hash: "e".repeat(64) },
                asset_release_authority: {
                    format: "world-forge.asset_release_authority",
                    format_version: 1,
                    release_authority_id: "release_authority_01",
                    content_hash: "0".repeat(64),
                },
            }),
        });
        expect((projectCreationJob(release, "workspace_01", authority()) as CreationAuthorityJobProjection).authorityOutcome).toMatchObject({
            kind: "asset_release_authority",
            status: "authorized",
            blocked: false,
        });

        const headless = creationJob({
            format_version: 12,
            operation: "runtime.headless.verify",
            result: authorityResult({
                output_artifact_ids: ["artifact_support_authority", "artifact_runtime_evidence", "artifact_runtime_support"],
                runtime_support_authority: {
                    format: "world-forge.runtime_support_authority",
                    format_version: 1,
                    id: "support_authority_01",
                    content_hash: "1".repeat(64),
                },
                runtime_evidence: {
                    format: "world-forge.runtime_evidence",
                    format_version: 1,
                    id: "runtime_evidence_01",
                    content_hash: "2".repeat(64),
                },
                runtime_support_report: {
                    format: "world-forge.runtime_support_report",
                    format_version: 1,
                    id: "runtime_support_01",
                    content_hash: "3".repeat(64),
                },
                release_status: "blocked",
                native_status: "unavailable",
                supported: false,
                publication: {
                    grant_id: "grant_headless",
                    grant_generation: 1,
                    kind: "headless_evidence_directory",
                    state: "published",
                    headless_evidence_set: {
                        format: "world-forge.headless_evidence_set",
                        format_version: 1,
                        id: "headless_evidence_01",
                        content_hash: "4".repeat(64),
                        tree_hash: "5".repeat(64),
                    },
                },
            }),
        });
        expect((projectCreationJob(headless, "workspace_01", authority()) as CreationAuthorityJobProjection).authorityOutcome).toEqual({
            kind: "runtime_headless_authority",
            headlessVerified: true,
            nativeUnavailable: true,
            releaseBlocked: true,
            blocked: true,
        });

        expect(projectCreationJob({ ...review, format_version: 13 }, "workspace_01", authority())).toBeNull();
        expect(projectCreationJob({ ...review, operation: "asset.release.authorize" }, "workspace_01", authority())).toBeNull();
        expect(projectCreationJob({ ...headless, result: { ...(headless.result as unknown as Record<string, unknown>), native_status: "verified" } }, "workspace_01", authority())).toBeNull();
        expect(projectCreationJob({ ...review, result: { ...(review.result as unknown as Record<string, unknown>), renderer_smuggled: true } }, "workspace_01", authority())).toBeNull();
        expect(projectCreationJob({ ...release, result: { ...(release.result as unknown as Record<string, unknown>), publication: { ...((release.result as unknown as Record<string, unknown>).publication as Record<string, unknown>), state: "ready" } } }, "workspace_01", authority())).toBeNull();
        expect(projectCreationJob({ ...headless, result: { ...(headless.result as unknown as Record<string, unknown>), publication: { ...((headless.result as unknown as Record<string, unknown>).publication as Record<string, unknown>), candidate_bytes: true } } }, "workspace_01", authority())).toBeNull();
    });
});

function workspace(): StudioCreationWorkspace {
    return {
        format: "world-forge.studio_creation_workspace",
        format_version: 1,
        workspace_id: "workspace_01",
        project_kind: "game",
        root_generation: 3,
        project: {
            format: "world-forge.project",
            format_version: 1,
            id: "project_01",
            content_hash: "e".repeat(64),
        },
        source_revision: SOURCE,
        workflow_status_hash: null,
        created_at: "2026-08-04T00:00:00Z",
        updated_at: "2026-08-04T00:00:00Z",
    };
}

function authority(): CreationExecutionAuthority {
    return {
        workspaceId: "workspace_01",
        rootGeneration: 3,
        sourceRevision: SOURCE,
        workflowStatusHash: null,
        artifactSnapshotHash: SNAPSHOT,
    };
}

function evidenceApi(
    lifecycles: {
        active: ReturnType<typeof artifact>[];
        candidate: ReturnType<typeof artifact>[];
    },
    countOverride: Partial<Record<"active" | "candidate", number>> = {},
): ForgeStudioApi & { listCreationArtifacts: ReturnType<typeof vi.fn> } {
    const counts = {
        active: countOverride.active ?? lifecycles.active.length,
        invalidated: 2,
        historical: 1,
        candidate: countOverride.candidate ?? lifecycles.candidate.length,
        ignored: 0,
    };
    const inspectCreationEvidence = vi.fn().mockResolvedValue(
        v4("creation_evidence.inspect", {
            authority: publicAuthority(),
            artifact_snapshot_hash: SNAPSHOT,
            evidence: {
                format: "world-forge.studio_creation_evidence",
                format_version: 1,
                evidence_id: "evidence_01",
                authority: publicAuthority(),
                artifact_snapshot_hash: SNAPSHOT,
                artifact_counts: counts,
                dimensions: {
                    authoring: "valid",
                    compilation: "not_requested",
                    assets: "unplanned",
                    adapter: "absent",
                    execution: [],
                    packaging: "unverified",
                    release: "blocked",
                },
                blocker_reason_codes: ["compilation_not_requested"],
                mechanics: {
                    artifact_id: null,
                    total: 0,
                    status_counts: {
                        supported_current: 0,
                        game_extension_verified: 0,
                        authoring_only: 0,
                        blocked: 0,
                    },
                    required_features: [],
                    missing_features: [],
                },
                runtime: {
                    requested_adapter: null,
                    resolved_adapter: null,
                    required_features: [],
                    missing_features: [],
                    platforms: [],
                },
                assets: {
                    inventory_artifact_id: null,
                    assetpack_artifact_id: null,
                    inventory_assets: 0,
                    lineage_complete: 0,
                    lineage_partial: 0,
                    qa_passed: 0,
                    qa_failed: 0,
                    licensed: 0,
                },
                materialization: {
                    enabled: false,
                    state: "blocked",
                    prerequisites: [
                        {
                            code: "compile",
                            satisfied: false,
                            message: "Compile first.",
                        },
                    ],
                },
                readiness: {
                    format: "world-forge.creation_readiness",
                    format_version: 1,
                    id: "readiness_01",
                    content_hash: "f".repeat(64),
                },
                handoff: {
                    format: "world-forge.creation_handoff",
                    format_version: 1,
                    id: "handoff_01",
                    content_hash: "1".repeat(64),
                },
                content_hash: "2".repeat(64),
            },
        }),
    );
    const listCreationArtifacts = vi.fn().mockImplementation(({ lifecycle }) =>
        Promise.resolve(
            v4("creation_artifact.list", {
                authority: publicAuthority(),
                artifact_snapshot_hash: SNAPSHOT,
                artifacts:
                    lifecycle === "active"
                        ? lifecycles.active
                        : lifecycles.candidate,
                next_cursor: null,
                counts,
            }),
        ),
    );
    return {
        inspectCreationEvidence,
        listCreationArtifacts,
    } as unknown as ForgeStudioApi & {
        listCreationArtifacts: ReturnType<typeof vi.fn>;
    };
}

function artifact(artifactId: string, lifecycle: "active" | "candidate") {
    return {
        format: "world-forge.studio_creation_artifact" as const,
        format_version: 1 as const,
        artifact_id: artifactId,
        subject: {
            format: "world-forge.gamepack",
            format_version: 1 as const,
            id: `${artifactId}_subject`,
            content_hash: "3".repeat(64),
        },
        lifecycle,
        roles: ["compiled_logic"],
        producer: {
            kind:
                lifecycle === "active"
                    ? ("active_phase_report" as const)
                    : ("future_candidate" as const),
            phase_id: lifecycle === "active" ? "p10_canon_lock" : null,
            reference_id: lifecycle === "active" ? "phase_report_01" : "job_01",
        },
        references: { dependency_count: 0, dependent_count: 0 },
        authority: publicAuthority(),
        record_hash: RECORD,
    };
}

function creationJob(overrides: Record<string, unknown> = {}) {
    return {
        format: "world-forge.studio_creation_job",
        format_version: 1,
        job_id: "compile_01",
        workspace_id: "workspace_01",
        operation: "creation.compile",
        state: "queued",
        generation: 0,
        authority: jobAuthority(),
        inputs: [],
        progress: "queued",
        result: null,
        error: null,
        created_at: "2026-08-04T00:00:00Z",
        started_at: null,
        finished_at: null,
        updated_at: "2026-08-04T00:00:00Z",
        record_hash: RECORD,
        ...overrides,
    };
}

function result(overrides: Record<string, unknown> = {}) {
    return {
        output_artifact_ids: ["candidate_01"],
        artifact_snapshot_hash: "4".repeat(64),
        analysis_status: "passed",
        reason_codes: [],
        cleanup_pending: false,
        ...overrides,
    };
}

function authorityResult(overrides: Record<string, unknown> = {}) {
    return {
        output_artifact_ids: ["artifact_result"],
        artifact_snapshot_hash: SNAPSHOT,
        analysis_status: "passed",
        reason_codes: [],
        cleanup_pending: false,
        ...overrides,
    };
}

function jobAuthority() {
    return {
        root_generation: 3,
        source_revision: SOURCE,
        workflow_status_hash: null,
        artifact_snapshot_hash: SNAPSHOT,
    };
}

function publicAuthority() {
    return {
        workspace_id: "workspace_01",
        root_generation: 3,
        source_revision: SOURCE,
        workflow_status_hash: null,
    };
}

function v4(method: string, result: Record<string, unknown>) {
    return {
        ok: true as const,
        value: {
            protocol: "rpg-world-forge.studio_protocol" as const,
            protocol_version: 4 as const,
            kind: "response" as const,
            request_id: "request_01",
            method,
            result,
        },
    };
}

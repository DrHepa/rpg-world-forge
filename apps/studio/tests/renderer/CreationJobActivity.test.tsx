// @vitest-environment jsdom

import {
    cleanup,
    fireEvent,
    render,
    screen,
    waitFor,
    within,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CreationJobActivity } from "../../src/renderer/CreationJobActivity";
import type { CreationExecutionAuthority } from "../../src/renderer/creation-execution-state";
import type {
    ForgeStudioApi,
    StudioCreationJob,
} from "../../src/shared/studio-api";

const SOURCE = "a".repeat(64);
const SNAPSHOT = "b".repeat(64);
const RECORD = "c".repeat(64);

afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
});

describe("CreationJobActivity", () => {
    it("reports every job state plus independent analysis, cleanup and recovery evidence", async () => {
        const jobs = [
            job({ job_id: "queued_01", state: "queued" }),
            job({
                job_id: "running_01",
                state: "running",
                progress: "worker_started",
            }),
            job({
                job_id: "succeeded_01",
                state: "succeeded",
                progress: "cleanup_pending",
                result: result({
                    analysis_status: "inconclusive",
                    cleanup_pending: true,
                }),
            }),
            job({
                job_id: "failed_01",
                state: "failed",
                progress: "failed",
                error: {
                    code: "worker_crashed",
                    message: "Worker stopped",
                    retryable: true,
                },
            }),
            job({
                job_id: "canceled_01",
                state: "canceled",
                progress: "canceled",
            }),
            job({
                job_id: "orphaned_01",
                state: "orphaned",
                progress: "orphaned",
                error: {
                    code: "recovery_required",
                    message: "Review retained evidence",
                    retryable: true,
                },
            }),
        ];
        const api = activityApi(jobs);

        renderActivity(api);

        expect(
            await screen.findByRole("heading", {
                name: "Creation job activity",
            }),
        ).toBeInTheDocument();
        expect(screen.getByText("Jobs in creation order")).toBeInTheDocument();
        for (const state of [
            "Queued",
            "Running",
            "Succeeded",
            "Failed",
            "Canceled",
            "Orphaned",
        ]) {
            expect(screen.getAllByText(state).length).toBeGreaterThan(0);
        }
        const succeeded = screen.getByRole("article", {
            name: "Creation compile succeeded_01",
        });
        expect(within(succeeded).getByText("Inconclusive")).toBeInTheDocument();
        expect(
            within(succeeded).getAllByText("Cleanup pending").length,
        ).toBeGreaterThan(0);
        const orphaned = screen.getByRole("article", {
            name: "Creation compile orphaned_01",
        });
        expect(
            within(orphaned).getByText("Recovery required"),
        ).toBeInTheDocument();
        expect(screen.queryByText(/%/u)).not.toBeInTheDocument();
    });

    it("uses bounded forward pagination without describing the oldest page as recent", async () => {
        const listCreationJobs = vi
            .fn()
            .mockResolvedValueOnce(
                v4("creation_job.list", {
                    jobs: [job({ job_id: "first_01" })],
                    next_sequence: 8,
                }),
            )
            .mockResolvedValueOnce(
                v4("creation_job.list", {
                    jobs: [
                        job({
                            job_id: "ninth_01",
                            state: "failed",
                            progress: "failed",
                            error: {
                                code: "timeout",
                                message: "Timed out",
                                retryable: true,
                            },
                        }),
                    ],
                    next_sequence: null,
                }),
            );
        const api = activityApi([], { listCreationJobs });

        renderActivity(api);
        await screen.findByText("first_01");
        expect(screen.queryByText(/recent/iu)).not.toBeInTheDocument();
        fireEvent.click(screen.getByRole("button", { name: "Load next jobs" }));

        expect(await screen.findByText("ninth_01")).toBeInTheDocument();
        expect(listCreationJobs).toHaveBeenNthCalledWith(1, {
            workspaceId: "workspace_01",
            state: null,
            afterSequence: 0,
            limit: 8,
        });
        expect(listCreationJobs).toHaveBeenNthCalledWith(2, {
            workspaceId: "workspace_01",
            state: null,
            afterSequence: 8,
            limit: 8,
        });
    });

    it("executes only context-valid CAS cancellation and recovery mutations", async () => {
        const queued = job({ job_id: "queued_01" });
        const orphaned = job({
            job_id: "orphaned_01",
            state: "orphaned",
            progress: "orphaned",
            generation: 4,
            error: {
                code: "recovery_required",
                message: "Review",
                retryable: true,
            },
        });
        const cancelCreationJob = vi.fn().mockResolvedValue(
            v4("creation_job.cancel", {
                job: job({
                    job_id: "queued_01",
                    state: "canceled",
                    progress: "canceled",
                    generation: 1,
                }),
            }),
        );
        const recoverCreationJob = vi.fn().mockResolvedValue(
            v4("creation_job.recover", {
                job: job({
                    job_id: "orphaned_01",
                    state: "queued",
                    progress: "queued",
                    generation: 5,
                }),
            }),
        );
        const api = activityApi([queued, orphaned], {
            cancelCreationJob,
            recoverCreationJob,
        });
        const onObservedJobChange = vi.fn();

        renderActivity(api, { onObservedJobChange });
        await screen.findByText("queued_01");
        fireEvent.click(
            screen.getByRole("button", { name: "Cancel queued_01" }),
        );
        await waitFor(() =>
            expect(cancelCreationJob).toHaveBeenCalledWith({
                jobId: "queued_01",
                expectedGeneration: 0,
                expectedRecordHash: RECORD,
            }),
        );
        expect(onObservedJobChange).toHaveBeenCalledWith(
            expect.objectContaining({ job_id: "queued_01", state: "canceled" }),
        );
        fireEvent.click(
            screen.getByRole("button", { name: "Resume orphaned_01" }),
        );
        await waitFor(() =>
            expect(recoverCreationJob).toHaveBeenCalledWith({
                jobId: "orphaned_01",
                expectedGeneration: 4,
                expectedRecordHash: RECORD,
                mode: "resume",
            }),
        );
        expect(screen.getByRole("status")).toHaveTextContent(/queued/iu);
    });

    it("allows only stored-CAS-safe stale recovery actions and is neutral when execution is not applicable", async () => {
        const staleAuthority = {
            ...jobAuthority(),
            artifact_snapshot_hash: "d".repeat(64),
        };
        const api = activityApi([
            job({ job_id: "stale_queued", authority: staleAuthority }),
            job({
                job_id: "stale_orphaned",
                authority: staleAuthority,
                state: "orphaned",
                progress: "orphaned",
                error: {
                    code: "recovery_required",
                    message: "Review",
                    retryable: true,
                },
            }),
            job({
                job_id: "stale_cleanup",
                authority: staleAuthority,
                state: "succeeded",
                progress: "cleanup_pending",
                result: result({ cleanup_pending: true }),
            }),
            job({
                job_id: "stale_retry",
                authority: staleAuthority,
                state: "failed",
                progress: "failed",
                error: {
                    code: "worker_crashed",
                    message: "Stopped",
                    retryable: true,
                },
            }),
        ]);
        const { rerender } = renderActivity(api);

        const queued = await screen.findByRole("article", {
            name: "Creation compile stale_queued",
        });
        expect(within(queued).getByText("Stale authority")).toBeInTheDocument();
        expect(within(queued).getByRole("button", { name: "Cancel stale_queued" })).toBeEnabled();
        const orphaned = screen.getByRole("article", {
            name: "Creation compile stale_orphaned",
        });
        expect(within(orphaned).getByRole("button", { name: "Roll back stale_orphaned" })).toBeEnabled();
        expect(within(orphaned).queryByRole("button", { name: "Resume stale_orphaned" })).not.toBeInTheDocument();
        expect(
            within(
                screen.getByRole("article", { name: "Creation compile stale_cleanup" }),
            ).getByRole("button", { name: "Clean up stale_cleanup" }),
        ).toBeEnabled();
        expect(
            within(
                screen.getByRole("article", { name: "Creation compile stale_retry" }),
            ).queryByRole("button", { name: "Retry stale_retry" }),
        ).not.toBeInTheDocument();

        rerender(
            <CreationJobActivity
                api={api}
                workspaceId="workspace_01"
                authority={null}
                applicable={false}
                observedJob={null}
                focusJobId={null}
                refreshToken={0}
                onObservedJobChange={vi.fn()}
                onRetryCompile={vi.fn()}
            />,
        );
        expect(
            screen.getByText(
                "Game execution is not applicable to this project kind.",
            ),
        ).toBeInTheDocument();
    });

    it("focuses an observed result and announces activity changes through live status", async () => {
        const api = activityApi([]);
        const observed = job({
            job_id: "compile_focused",
            state: "succeeded",
            progress: "committed",
            result: result(),
        });

        renderActivity(api, {
            observedJob: observed,
            focusJobId: "compile_focused",
        });

        const card = await screen.findByRole("article", {
            name: "Creation compile compile_focused",
        });
        await waitFor(() => expect(card).toHaveFocus());
        expect(screen.getByRole("status")).toHaveAttribute(
            "aria-live",
            "polite",
        );
        expect(screen.getByText(/Candidate produced/iu)).toBeInTheDocument();
    });

    it("prefers a newer listed generation over a stale observed copy", async () => {
        const observed = job({ job_id: "compile_reconciled" });
        const listed = job({
            job_id: "compile_reconciled",
            generation: 1,
            state: "succeeded",
            progress: "committed",
            result: result(),
        });

        renderActivity(activityApi([listed]), { observedJob: observed });

        await screen.findByRole("article", {
            name: "Creation compile compile_reconciled",
        });
        await waitFor(() =>
            expect(
                within(
                    screen.getByRole("article", {
                        name: "Creation compile compile_reconciled",
                    }),
                ).getByText("Succeeded"),
            ).toBeInTheDocument(),
        );
        const card = screen.getByRole("article", {
            name: "Creation compile compile_reconciled",
        });
        expect(within(card).queryByRole("button")).not.toBeInTheDocument();
    });
});

function renderActivity(
    api: ForgeStudioApi,
    overrides: {
        observedJob?: ReturnType<typeof job> | null;
        focusJobId?: string | null;
        onObservedJobChange?: (job: StudioCreationJob) => void;
    } = {},
) {
    return render(
        <CreationJobActivity
            api={api}
            workspaceId="workspace_01"
            authority={authority()}
            applicable
            observedJob={overrides.observedJob ?? null}
            focusJobId={overrides.focusJobId ?? null}
            refreshToken={0}
            onObservedJobChange={overrides.onObservedJobChange ?? vi.fn()}
            onRetryCompile={vi.fn()}
        />,
    );
}

function activityApi(
    jobs: ReturnType<typeof job>[],
    overrides: Partial<ForgeStudioApi> = {},
): ForgeStudioApi {
    return {
        listCreationJobs: vi
            .fn()
            .mockResolvedValue(
                v4("creation_job.list", { jobs, next_sequence: null }),
            ),
        cancelCreationJob: vi.fn(),
        recoverCreationJob: vi.fn(),
        ...overrides,
    } as unknown as ForgeStudioApi;
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

function job(overrides: Record<string, unknown> = {}) {
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
        artifact_snapshot_hash: "e".repeat(64),
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

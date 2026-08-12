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

import { CreationWorkspace } from "../../src/renderer/CreationWorkspace";
import type {
    ForgeStudioApi,
    StudioCreationArtifactListParams,
    StudioCreationJobListParams,
} from "../../src/shared/studio-api";

const SOURCE_REVISION = "a".repeat(64);
const PROFILE_FILE_SHA = "b".repeat(64);
const PROJECT_HASH = "c".repeat(64);
const REVIEW_HASH = "d".repeat(64);
const RECORD_HASH = "e".repeat(64);
const PROPOSED_REVISION = "f".repeat(64);
const ARTIFACT_SNAPSHOT_HASH = "1".repeat(64);
const RESULT_SNAPSHOT_HASH = "7".repeat(64);
const GAMEPACK_ARTIFACT_ID = "artifact_gamepack";

afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
});

describe("CreationWorkspace", () => {
    it("opens a server-discriminated generic route and reports independent readiness dimensions", async () => {
        const { api, mocks } = creationApi();
        installApi(api);
        render(
            <CreationWorkspace
                workspaceId="creation_workspace"
                generation={1}
                onNavigationStateChange={vi.fn()}
            />,
        );

        expect(
            await screen.findByRole("heading", { name: "Neutral universe" }),
        ).toBeInTheDocument();
        expect(screen.getByText("Universe library")).toBeInTheDocument();
        expect(screen.getByText("Authoring validity")).toBeInTheDocument();
        expect(screen.getByText("Valid for authoring")).toBeInTheDocument();
        expect(
            screen.getByText("Implementation readiness"),
        ).toBeInTheDocument();
        expect(
            screen.getByText("Not implementation-ready"),
        ).toBeInTheDocument();
        expect(screen.getByText("Native execution")).toBeInTheDocument();
        expect(screen.getByText("N/A")).toBeInTheDocument();
        expect(screen.getByText("Release")).toBeInTheDocument();
        expect(screen.getByText("Blocked")).toBeInTheDocument();
        expect(screen.queryByText(/playable/iu)).not.toBeInTheDocument();
        expect(screen.getByRole("tab", { name: "Modules" })).toBeEnabled();
        expect(screen.getByRole("tab", { name: "Phases" })).toBeEnabled();
        expect(screen.getByRole("tab", { name: "Assets" })).toBeEnabled();
        expect(
            screen.getByRole("tab", { name: "Compatibility" }),
        ).toBeEnabled();
        expect(screen.getByRole("tab", { name: "Materialize" })).toBeEnabled();
        expect(mocks.inspectCreationEvidence).not.toHaveBeenCalled();
        expect(mocks.openCreationWorkspace).toHaveBeenCalledWith(
            "creation_workspace",
        );
        expect(mocks.listCreationDocuments).toHaveBeenCalledWith(
            "creation_workspace",
            SOURCE_REVISION,
        );
        expect(mocks.readCreationDocument).toHaveBeenCalledWith(
            "creation_workspace",
            SOURCE_REVISION,
            "profile.json",
        );
        expect(mocks.validateWorld).not.toHaveBeenCalled();
        expect(mocks.analyzeWorld).not.toHaveBeenCalled();
        expect(mocks.listSourceDocuments).not.toHaveBeenCalled();
        expect(
            screen.getByText(
                "Game execution is not applicable to this project kind.",
            ),
        ).toBeInTheDocument();
        expect(mocks.listCreationJobs).not.toHaveBeenCalled();
        expect(mocks.compileCreationProject).not.toHaveBeenCalled();
    });

    it("compiles a game with exact authority, polls only its job, refreshes candidates and preserves active readiness", async () => {
        const queued = creationJob();
        const succeeded = creationJob({
            state: "succeeded",
            progress: "committed",
            generation: 2,
            result: creationJobResult(RESULT_SNAPSHOT_HASH),
        });
        const inspectCreationEvidence = vi
            .fn()
            .mockResolvedValueOnce(evidenceResponse(ARTIFACT_SNAPSHOT_HASH, 0))
            .mockResolvedValueOnce(
                v4Error("conflict", "Creation artifact snapshot changed"),
            )
            .mockResolvedValueOnce(evidenceResponse(RESULT_SNAPSHOT_HASH, 1));
        const listCreationArtifacts = vi
            .fn()
            .mockImplementation((params: StudioCreationArtifactListParams) =>
                Promise.resolve(
                    artifactListResponse(
                        params.expectedArtifactSnapshotHash,
                        params.lifecycle,
                    ),
                ),
            );
        const listCreationJobs = vi.fn().mockResolvedValue(
            v4Response("creation_job.list", {
                jobs: [],
                next_sequence: null,
            }),
        );
        const compileCreationProject = vi
            .fn()
            .mockResolvedValue(
                v4Response("creation_job.create", { job: queued }),
            );
        const getCreationJob = vi
            .fn()
            .mockResolvedValue(
                v4Response("creation_job.get", { job: succeeded }),
            );
        const { api } = creationApi({
            openCreationWorkspace: gameOpen(),
            inspectCreationEvidence,
            listCreationArtifacts,
            listCreationJobs,
            compileCreationProject,
            getCreationJob,
        });
        installApi(api);
        render(
            <CreationWorkspace
                workspaceId="creation_workspace"
                generation={1}
                onNavigationStateChange={vi.fn()}
            />,
        );

        const compile = await screen.findByRole("button", {
            name: "Compile current project",
        });
        fireEvent.click(compile);

        await waitFor(() =>
            expect(compileCreationProject).toHaveBeenCalledWith({
                workspaceId: "creation_workspace",
                expectedRootGeneration: 4,
                expectedSourceRevision: SOURCE_REVISION,
                expectedWorkflowStatusHash: null,
                expectedArtifactSnapshotHash: ARTIFACT_SNAPSHOT_HASH,
            }),
        );
        await waitFor(
            () => expect(getCreationJob).toHaveBeenCalledWith("compile_01"),
            {
                timeout: 2_000,
            },
        );
        const card = await screen.findByRole("article", {
            name: "Creation compile compile_01",
        });
        await waitFor(() => expect(card).toHaveFocus());
        expect(
            within(card).getByText(/Candidate produced/iu),
        ).toBeInTheDocument();
        expect(
            screen.getByText("Not implementation-ready"),
        ).toBeInTheDocument();
        expect(inspectCreationEvidence).toHaveBeenNthCalledWith(2, {
            workspaceId: "creation_workspace",
            expectedRootGeneration: 4,
            expectedSourceRevision: SOURCE_REVISION,
            expectedWorkflowStatusHash: null,
            expectedArtifactSnapshotHash: RESULT_SNAPSHOT_HASH,
        });
        expect(inspectCreationEvidence).toHaveBeenNthCalledWith(3, {
            workspaceId: "creation_workspace",
            expectedRootGeneration: 4,
            expectedSourceRevision: SOURCE_REVISION,
            expectedWorkflowStatusHash: null,
            expectedArtifactSnapshotHash: null,
        });
    });

    it("routes fixed artifact admission through the shared job tracker and refreshes the exact census", async () => {
        const queued = creationJob({
            job_id: "admit_01",
            operation: "artifact.admit",
        });
        const succeeded = creationJob({
            job_id: "admit_01",
            operation: "artifact.admit",
            state: "succeeded",
            progress: "committed",
            generation: 1,
            result: creationJobResult(ARTIFACT_SNAPSHOT_HASH),
            record_hash: "6".repeat(64),
        });
        const admitCreationArtifact = vi.fn().mockResolvedValue(
            v4Response("creation_job.create", {
                job: queued,
            }),
        );
        const getCreationJob = vi.fn().mockResolvedValue(
            v4Response("creation_job.get", {
                job: succeeded,
            }),
        );
        const inspectCreationEvidence = vi
            .fn()
            .mockResolvedValue(
                evidenceResponse(ARTIFACT_SNAPSHOT_HASH, 0),
            );
        const { api } = creationApi({
            openCreationWorkspace: gameOpen(),
            admitCreationArtifact,
            getCreationJob,
            inspectCreationEvidence,
        });
        installApi(api);
        render(
            <CreationWorkspace
                workspaceId="creation_workspace"
                generation={1}
                onNavigationStateChange={vi.fn()}
            />,
        );

        await screen.findByRole("button", {
            name: "Compile current project",
        });
        fireEvent.click(screen.getByRole("tab", { name: "Assets" }));
        await screen.findByRole("heading", {
            name: "Asset production pipeline",
        });
        fireEvent.change(
            screen.getByLabelText("Canonical artifact JSON"),
            {
                target: {
                    value: '{"format":"world-forge.asset_license_record"}',
                },
            },
        );
        fireEvent.click(
            screen.getByRole("checkbox", {
                name: "neutral_puzzle — Active",
            }),
        );
        fireEvent.click(
            screen.getByRole("button", { name: "Admit artifact" }),
        );

        await waitFor(() =>
            expect(admitCreationArtifact).toHaveBeenCalledWith({
                workspaceId: "creation_workspace",
                expectedRootGeneration: 4,
                expectedSourceRevision: SOURCE_REVISION,
                expectedWorkflowStatusHash: null,
                expectedArtifactSnapshotHash: ARTIFACT_SNAPSHOT_HASH,
                document: {
                    format: "world-forge.asset_license_record",
                },
                dependencyArtifactIds: [GAMEPACK_ARTIFACT_ID],
            }),
        );
        await waitFor(
            () => expect(getCreationJob).toHaveBeenCalledWith("admit_01"),
            { timeout: 2_000 },
        );
        await waitFor(() =>
            expect(inspectCreationEvidence).toHaveBeenCalledTimes(2),
        );
        await waitFor(() =>
            expect(
                screen.getByLabelText("Asset pipeline status"),
            ).toHaveTextContent(
                "Artifact admission job admit_01 completed as succeeded.",
            ),
        );
    });

    it("reconstructs every durable asset output grant while keeping internal tab navigation local", async () => {
        const grants = [
            outputGrant("grant_ready_a", "ready"),
            outputGrant("grant_ready_b", "ready"),
            outputGrant("grant_reserved", "reserved"),
            outputGrant("grant_reserved_b", "reserved"),
            outputGrant("grant_recovery", "recovery_required"),
            outputGrant("grant_published", "published"),
            outputGrant("grant_revoked", "revoked"),
            outputGrant("grant_runtime", "ready", 2),
        ].sort((left, right) =>
            left.grant_id < right.grant_id
                ? -1
                : left.grant_id > right.grant_id
                  ? 1
                  : 0,
        );
        const listCreationOutputGrants = vi.fn().mockResolvedValue(
            v4Response("creation_output_grant.list", {
                authority: evidenceAuthority(),
                artifact_snapshot_hash: ARTIFACT_SNAPSHOT_HASH,
                grants,
                next_cursor: null,
            }),
        );
        const boundJobs = [
            assetSealJob("seal_reserved", "grant_reserved", 1, "queued"),
            assetSealJob("seal_reserved_b", "grant_reserved_b", 1, "queued"),
            assetSealJob(
                "seal_recovery",
                "grant_recovery",
                1,
                "orphaned",
            ),
        ];
        const listCreationJobs = vi
            .fn()
            .mockImplementation((params: StudioCreationJobListParams) =>
                Promise.resolve(
                    v4Response("creation_job.list", {
                        jobs: boundJobs.filter(
                            (job) => job.state === params.state,
                        ),
                        next_sequence: null,
                    }),
                ),
            );
        const getCreationJob = vi.fn().mockImplementation((jobId: string) =>
            Promise.resolve(
                v4Response("creation_job.get", {
                    job: boundJobs.find((job) => job.job_id === jobId),
                }),
            ),
        );
        const onNavigationStateChange = vi.fn();
        const { api } = creationApi({
            openCreationWorkspace: gameOpen(),
            listCreationOutputGrants,
            listCreationJobs,
            getCreationJob,
        });
        installApi(api);
        render(
            <CreationWorkspace
                workspaceId="creation_workspace"
                generation={1}
                onNavigationStateChange={onNavigationStateChange}
            />,
        );

        await waitFor(() =>
            expect(onNavigationStateChange).toHaveBeenLastCalledWith({
                blocksNavigation: true,
                kind: "output_grant",
            }),
        );
        expect(listCreationOutputGrants).toHaveBeenCalledWith({
            workspaceId: "creation_workspace",
            expectedRootGeneration: 4,
            expectedSourceRevision: SOURCE_REVISION,
            expectedWorkflowStatusHash: null,
            expectedArtifactSnapshotHash: ARTIFACT_SNAPSHOT_HASH,
            cursor: null,
            limit: 8,
        });

        fireEvent.click(screen.getByRole("tab", { name: "Assets" }));
        expect(
            await screen.findByRole("heading", {
                name: "Asset pack output authorities",
            }),
        ).toBeInTheDocument();
        expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
        for (const grant of grants.filter((item) => item.format_version === 1)) {
            expect(
                screen.getByRole("radio", {
                    name: new RegExp(`^${grant.display_name}(?!-)`, "u"),
                }),
            ).toBeInTheDocument();
        }
        expect(
            screen.queryByRole("radio", { name: /grant-runtime/u }),
        ).not.toBeInTheDocument();
        await waitFor(
            () => {
                expect(getCreationJob).toHaveBeenCalledWith("seal_reserved");
                expect(getCreationJob).toHaveBeenCalledWith("seal_reserved_b");
            },
            { timeout: 2_000 },
        );
        expect(
            screen.getByText("Select one ready authority before sealing."),
        ).toBeInTheDocument();

        fireEvent.change(screen.getByLabelText("Canonical artifact JSON"), {
            target: { value: '{"format":"world-forge.asset_style"}' },
        });
        fireEvent.click(screen.getByRole("tab", { name: "Overview" }));
        expect(
            await screen.findByRole("dialog", { name: "Leave Assets?" }),
        ).toBeInTheDocument();
        fireEvent.click(
            screen.getByRole("button", {
                name: "Discard local draft and switch",
            }),
        );
        expect(
            await screen.findByRole("button", {
                name: "Compile current project",
            }),
        ).toBeInTheDocument();
        await waitFor(() =>
            expect(onNavigationStateChange).toHaveBeenLastCalledWith({
                blocksNavigation: true,
                kind: "output_grant",
            }),
        );
    });

    it("keeps a non-asset output authority globally durable without exposing it as an assetpack", async () => {
        const runtimeGrant = outputGrant("grant_runtime", "ready", 2);
        const listCreationOutputGrants = vi.fn().mockResolvedValue(
            v4Response("creation_output_grant.list", {
                authority: evidenceAuthority(),
                artifact_snapshot_hash: ARTIFACT_SNAPSHOT_HASH,
                grants: [runtimeGrant],
                next_cursor: null,
            }),
        );
        const onNavigationStateChange = vi.fn();
        const { api } = creationApi({
            openCreationWorkspace: gameOpen(),
            listCreationOutputGrants,
        });
        installApi(api);
        render(
            <CreationWorkspace
                workspaceId="creation_workspace"
                generation={1}
                onNavigationStateChange={onNavigationStateChange}
            />,
        );

        await waitFor(() =>
            expect(onNavigationStateChange).toHaveBeenLastCalledWith({
                blocksNavigation: true,
                kind: "output_grant",
            }),
        );
        fireEvent.click(screen.getByRole("tab", { name: "Assets" }));
        expect(
            await screen.findByRole("button", {
                name: "Select asset pack output",
            }),
        ).toBeInTheDocument();
        expect(screen.queryByRole("radio")).not.toBeInTheDocument();
        expect(
            screen.getByText("No asset pack output authority is registered."),
        ).toBeInTheDocument();
    });

    it("suppresses an identical queued compile without polling a job it did not submit", async () => {
        const existing = creationJob({ job_id: "compile_existing" });
        const listCreationJobs = vi
            .fn()
            .mockImplementation((params: StudioCreationJobListParams) =>
                Promise.resolve(
                    v4Response("creation_job.list", {
                        jobs: params.state === "queued" ? [existing] : [],
                        next_sequence: null,
                    }),
                ),
            );
        const compileCreationProject = vi.fn();
        const getCreationJob = vi.fn();
        const { api } = creationApi({
            openCreationWorkspace: gameOpen(),
            listCreationJobs,
            compileCreationProject,
            getCreationJob,
        });
        installApi(api);
        render(
            <CreationWorkspace
                workspaceId="creation_workspace"
                generation={1}
                onNavigationStateChange={vi.fn()}
            />,
        );

        fireEvent.click(
            await screen.findByRole("button", {
                name: "Compile current project",
            }),
        );

        const existingCard = await screen.findByRole("article", {
            name: "Creation compile compile_existing",
        });
        await waitFor(() => expect(existingCard).toHaveFocus());
        expect(
            screen.getByRole("status", { name: "Compilation status" }),
        ).toHaveTextContent("already queued or running");
        expect(compileCreationProject).not.toHaveBeenCalled();
        expect(getCreationJob).not.toHaveBeenCalled();
    });

    it("adopts a resumed current-authority job into polling and refreshes durable grants", async () => {
        const orphaned = creationJob({
            job_id: "compile_orphaned",
            state: "orphaned",
            progress: "orphaned",
            generation: 4,
            error: {
                code: "recovery_required",
                message: "Review retained execution evidence",
                retryable: true,
            },
        });
        const requeued = creationJob({
            job_id: "compile_orphaned",
            state: "queued",
            progress: "queued",
            generation: 5,
            record_hash: "8".repeat(64),
        });
        const canceled = creationJob({
            job_id: "compile_orphaned",
            state: "canceled",
            progress: "canceled",
            generation: 6,
            record_hash: "9".repeat(64),
        });
        const listCreationJobs = vi.fn().mockResolvedValue(
            v4Response("creation_job.list", {
                jobs: [orphaned],
                next_sequence: null,
            }),
        );
        const recoverCreationJob = vi.fn().mockResolvedValue(
            v4Response("creation_job.recover", { job: requeued }),
        );
        const getCreationJob = vi.fn().mockResolvedValue(
            v4Response("creation_job.get", { job: canceled }),
        );
        const listCreationOutputGrants = vi.fn().mockResolvedValue(
            v4Response("creation_output_grant.list", {
                authority: evidenceAuthority(),
                artifact_snapshot_hash: ARTIFACT_SNAPSHOT_HASH,
                grants: [],
                next_cursor: null,
            }),
        );
        const { api } = creationApi({
            openCreationWorkspace: gameOpen(),
            listCreationJobs,
            recoverCreationJob,
            getCreationJob,
            listCreationOutputGrants,
        });
        installApi(api);
        render(
            <CreationWorkspace
                workspaceId="creation_workspace"
                generation={1}
                onNavigationStateChange={vi.fn()}
            />,
        );

        fireEvent.click(
            await screen.findByRole("button", {
                name: "Resume compile_orphaned",
            }),
        );
        await waitFor(() =>
            expect(recoverCreationJob).toHaveBeenCalledWith({
                jobId: "compile_orphaned",
                expectedGeneration: 4,
                expectedRecordHash: RECORD_HASH,
                mode: "resume",
            }),
        );
        await waitFor(
            () => expect(getCreationJob).toHaveBeenCalledWith("compile_orphaned"),
            { timeout: 2_000 },
        );
        await waitFor(() =>
            expect(listCreationOutputGrants.mock.calls.length).toBeGreaterThan(1),
        );
    });

    it("fails closed and focuses the contractual compilation error", async () => {
        const compileCreationProject = vi
            .fn()
            .mockResolvedValue(
                v4Error("conflict", "Creation execution authority changed"),
            );
        const { api } = creationApi({
            openCreationWorkspace: gameOpen(),
            compileCreationProject,
        });
        installApi(api);
        render(
            <CreationWorkspace
                workspaceId="creation_workspace"
                generation={1}
                onNavigationStateChange={vi.fn()}
            />,
        );

        fireEvent.click(
            await screen.findByRole("button", {
                name: "Compile current project",
            }),
        );

        const alert = await screen.findByRole("alert");
        expect(alert).toHaveTextContent("Creation execution authority changed");
        await waitFor(() => expect(alert).toHaveFocus());
        expect(
            screen.getByRole("status", { name: "Compilation status" }),
        ).toHaveTextContent("not submitted");
    });

    it("stops submitted-job polling after unmount and never applies the late result", async () => {
        let resolveJob!: (value: ReturnType<typeof v4Response>) => void;
        const pendingJob = new Promise<ReturnType<typeof v4Response>>(
            (resolve) => {
                resolveJob = resolve;
            },
        );
        const getCreationJob = vi.fn().mockReturnValue(pendingJob);
        const { api } = creationApi({
            openCreationWorkspace: gameOpen(),
            compileCreationProject: vi
                .fn()
                .mockResolvedValue(
                    v4Response("creation_job.create", { job: creationJob() }),
                ),
            getCreationJob,
        });
        installApi(api);
        const view = render(
            <CreationWorkspace
                workspaceId="creation_workspace"
                generation={1}
                onNavigationStateChange={vi.fn()}
            />,
        );
        fireEvent.click(
            await screen.findByRole("button", {
                name: "Compile current project",
            }),
        );
        await waitFor(() => expect(getCreationJob).toHaveBeenCalledTimes(1), {
            timeout: 2_000,
        });

        view.unmount();
        resolveJob(
            v4Response("creation_job.get", {
                job: creationJob({
                    state: "succeeded",
                    progress: "committed",
                    result: creationJobResult(RESULT_SNAPSHOT_HASH),
                }),
            }),
        );
        await Promise.resolve();

        expect(getCreationJob).toHaveBeenCalledTimes(1);
    });

    it("loads the integral active artifact closure lazily and inspects redacted evidence", async () => {
        const { api, mocks } = creationApi();
        installApi(api);
        render(
            <CreationWorkspace
                workspaceId="creation_workspace"
                generation={1}
                onNavigationStateChange={vi.fn()}
            />,
        );
        await screen.findByRole("heading", { name: "Neutral universe" });
        expect(mocks.inspectCreationEvidence).not.toHaveBeenCalled();
        expect(mocks.listCreationArtifacts).not.toHaveBeenCalled();

        fireEvent.click(screen.getByRole("tab", { name: "Assets" }));

        expect(
            await screen.findByRole("heading", { name: "Asset evidence" }),
        ).toBeInTheDocument();
        expect(screen.getByText("Sealed")).toBeInTheDocument();
        expect(screen.getByText("2 of 2")).toBeInTheDocument();
        expect(screen.getByText("Partial lineage")).toBeInTheDocument();
        expect(screen.getByText("compiled_logic")).toBeInTheDocument();
        expect(screen.getByText("active_phase_report")).toBeInTheDocument();
        expect(screen.getByText("p10_canon_lock")).toBeInTheDocument();
        expect(screen.getByText("report_p10")).toBeInTheDocument();
        expect(
            screen.getByText(
                /Verified sealed PNG and WAV previews use service-bound leases below/u,
            ),
        ).toBeInTheDocument();
        expect(mocks.inspectCreationEvidence).toHaveBeenCalledWith({
            workspaceId: "creation_workspace",
            expectedRootGeneration: 4,
            expectedSourceRevision: SOURCE_REVISION,
            expectedWorkflowStatusHash: null,
            expectedArtifactSnapshotHash: null,
        });
        expect(mocks.listCreationArtifacts).toHaveBeenCalledWith({
            workspaceId: "creation_workspace",
            expectedRootGeneration: 4,
            expectedSourceRevision: SOURCE_REVISION,
            expectedWorkflowStatusHash: null,
            expectedArtifactSnapshotHash: ARTIFACT_SNAPSHOT_HASH,
            lifecycle: "active",
            cursor: null,
            limit: 64,
        });

        fireEvent.click(
            screen.getByRole("button", { name: "Inspect neutral_puzzle" }),
        );
        expect(
            await screen.findByRole("heading", {
                name: "Neutral puzzle gamepack",
            }),
        ).toBeInTheDocument();
        expect(screen.getByText("asset_count")).toBeInTheDocument();
        expect(screen.getByText("depends_on")).toBeInTheDocument();
        expect(
            screen.getAllByText("artifact_asset_inventory").length,
        ).toBeGreaterThan(1);
        expect(screen.getByText("Invalidated")).toBeInTheDocument();
        expect(mocks.inspectCreationArtifact).toHaveBeenCalledWith({
            workspaceId: "creation_workspace",
            expectedRootGeneration: 4,
            expectedSourceRevision: SOURCE_REVISION,
            expectedWorkflowStatusHash: null,
            expectedArtifactSnapshotHash: ARTIFACT_SNAPSHOT_HASH,
            artifactId: GAMEPACK_ARTIFACT_ID,
        });
        expect(document.body.textContent).not.toContain("/private/");
    });

    it("fails closed when the active artifact census is incomplete", async () => {
        const listCreationArtifacts = vi.fn().mockResolvedValue(
            v4Response("creation_artifact.list", {
                authority: evidenceAuthority(),
                artifact_snapshot_hash: ARTIFACT_SNAPSHOT_HASH,
                artifacts: [],
                next_cursor: null,
                counts: creationEvidence().artifact_counts,
            }),
        );
        const { api } = creationApi({ listCreationArtifacts });
        installApi(api);
        render(
            <CreationWorkspace
                workspaceId="creation_workspace"
                generation={1}
                onNavigationStateChange={vi.fn()}
            />,
        );
        await screen.findByRole("heading", { name: "Neutral universe" });

        fireEvent.click(screen.getByRole("tab", { name: "Assets" }));

        expect(await screen.findByRole("alert")).toHaveTextContent(
            "incomplete active artifact closure",
        );
        expect(
            screen.queryByRole("heading", { name: "Asset evidence" }),
        ).not.toBeInTheDocument();
    });

    it("rejects an artifact inspection that diverges from the active census record", async () => {
        const listed = creationArtifact();
        const inspectCreationArtifact = vi.fn().mockResolvedValue(
            v4Response("creation_artifact.inspect", {
                authority: evidenceAuthority(),
                artifact_snapshot_hash: ARTIFACT_SNAPSHOT_HASH,
                artifact: {
                    ...listed,
                    subject: {
                        ...listed.subject,
                        content_hash: "4".repeat(64),
                    },
                },
                projection: {
                    projection_kind: "gamepack",
                    title: "Divergent gamepack",
                    status: "compiled",
                    facts: [],
                    lineage: [],
                },
            }),
        );
        const { api } = creationApi({ inspectCreationArtifact });
        installApi(api);
        render(
            <CreationWorkspace
                workspaceId="creation_workspace"
                generation={1}
                onNavigationStateChange={vi.fn()}
            />,
        );
        await screen.findByRole("heading", { name: "Neutral universe" });
        fireEvent.click(screen.getByRole("tab", { name: "Assets" }));
        await screen.findByRole("heading", { name: "Asset evidence" });

        fireEvent.click(
            screen.getByRole("button", { name: "Inspect neutral_puzzle" }),
        );

        expect(await screen.findByRole("alert")).toHaveTextContent(
            "mismatched artifact inspection evidence",
        );
        expect(
            screen.queryByRole("heading", { name: "Divergent gamepack" }),
        ).not.toBeInTheDocument();
    });

    it("cancels artifact inspection when leaving Assets and requires a fresh request", async () => {
        let resolveFirst!: (value: ReturnType<typeof v4Response>) => void;
        const first = new Promise<ReturnType<typeof v4Response>>((resolve) => {
            resolveFirst = resolve;
        });
        const inspectCreationArtifact = vi
            .fn()
            .mockReturnValueOnce(first)
            .mockResolvedValueOnce(
                artifactInspectionResponse("Fresh projection"),
            );
        const { api } = creationApi({ inspectCreationArtifact });
        installApi(api);
        render(
            <CreationWorkspace
                workspaceId="creation_workspace"
                generation={1}
                onNavigationStateChange={vi.fn()}
            />,
        );
        await screen.findByRole("heading", { name: "Neutral universe" });
        fireEvent.click(screen.getByRole("tab", { name: "Assets" }));
        await screen.findByRole("heading", { name: "Asset evidence" });
        fireEvent.click(
            screen.getByRole("button", { name: "Inspect neutral_puzzle" }),
        );
        expect(
            screen.getByText("Inspecting artifact evidence…"),
        ).toBeInTheDocument();

        fireEvent.click(screen.getByRole("tab", { name: "Compatibility" }));
        resolveFirst(artifactInspectionResponse("Stale projection"));
        await screen.findByRole("heading", {
            name: "Runtime compatibility evidence",
        });
        fireEvent.click(screen.getByRole("tab", { name: "Assets" }));

        expect(
            screen.queryByRole("heading", { name: "Stale projection" }),
        ).not.toBeInTheDocument();
        const inspect = screen.getByRole("button", {
            name: "Inspect neutral_puzzle",
        });
        expect(inspect).toBeEnabled();
        fireEvent.click(inspect);
        expect(
            await screen.findByRole("heading", { name: "Fresh projection" }),
        ).toBeInTheDocument();
        expect(inspectCreationArtifact).toHaveBeenCalledTimes(2);
    });

    it("reports compatibility independently and keeps active readiness immutable", async () => {
        const { api, mocks } = creationApi();
        installApi(api);
        render(
            <CreationWorkspace
                workspaceId="creation_workspace"
                generation={1}
                onNavigationStateChange={vi.fn()}
            />,
        );
        await screen.findByRole("heading", { name: "Neutral universe" });
        fireEvent.click(screen.getByRole("tab", { name: "Compatibility" }));

        expect(
            await screen.findByRole("heading", {
                name: "Runtime compatibility evidence",
            }),
        ).toBeInTheDocument();
        expect(screen.getByText("Compiled")).toBeInTheDocument();
        expect(screen.getByText("Declared")).toBeInTheDocument();
        expect(screen.getByText("Headless verified")).toBeInTheDocument();
        expect(
            screen.getByText("Mechanic ledger artifact"),
        ).toBeInTheDocument();
        expect(screen.getByText("artifact_ledger")).toBeInTheDocument();
        expect(
            screen.getAllByText("logic:finite_state").length,
        ).toBeGreaterThan(0);
        expect(screen.getAllByText("input:keyboard").length).toBeGreaterThan(0);
        expect(
            screen.getAllByText("runtime:native_evidence").length,
        ).toBeGreaterThan(0);
        expect(screen.getByText("headless_linux")).toBeInTheDocument();
        expect(
            within(
                screen.getByRole("tabpanel", { name: "Compatibility" }),
            ).getByText("adapter_not_verified"),
        ).toBeInTheDocument();
        expect(
            screen.getByText(/Authoring validity is not runtime support/u),
        ).toBeInTheDocument();

        fireEvent.click(screen.getByRole("tab", { name: "Materialize" }));
        expect(
            await screen.findByRole("heading", {
                name: "Materialization readiness",
            }),
        ).toBeInTheDocument();
        expect(screen.getByText("Active readiness inspection")).toBeInTheDocument();
        expect(
            screen.getByText("Adapter verification is still required."),
        ).toBeInTheDocument();
        expect(
            screen.queryByRole("button", { name: /materialize game/iu }),
        ).not.toBeInTheDocument();
        expect(mocks.inspectCreationEvidence).toHaveBeenCalledTimes(1);
    });

    it("mounts the fixed runtime pipeline only for a game compatibility workspace", async () => {
        const { api } = creationApi({
            openCreationWorkspace: gameOpen(),
        });
        installApi(api);
        render(
            <CreationWorkspace
                workspaceId="creation_workspace"
                generation={1}
                onNavigationStateChange={vi.fn()}
            />,
        );
        await screen.findByRole("heading", { name: "Neutral universe" });

        fireEvent.click(screen.getByRole("tab", { name: "Compatibility" }));

        expect(
            await screen.findByRole("heading", {
                name: "Runtime composition and bundle pipeline",
            }),
        ).toBeInTheDocument();
        expect(
            screen.getByRole("group", { name: "Compose verified runtime" }),
        ).toBeInTheDocument();
        expect(
            screen.getByRole("group", { name: "Build runtime bundle" }),
        ).toBeInTheDocument();
        expect(
            screen.queryByRole("button", { name: /materialize|package/iu }),
        ).not.toBeInTheDocument();
    });

    it("mounts the fixed four-step materialization pipeline only for a game workspace", async () => {
        const { api } = creationApi({
            openCreationWorkspace: gameOpen(),
        });
        installApi(api);
        render(
            <CreationWorkspace
                workspaceId="creation_workspace"
                generation={1}
                onNavigationStateChange={vi.fn()}
            />,
        );
        await screen.findByRole("heading", { name: "Neutral universe" });

        fireEvent.click(screen.getByRole("tab", { name: "Materialize" }));

        expect(
            await screen.findByRole("heading", {
                name: "Game materialization, package, and extraction pipeline",
            }),
        ).toBeInTheDocument();
        expect(
            screen.getByRole("group", {
                name: "1. Build materialization bundle",
            }),
        ).toBeInTheDocument();
        expect(
            screen.getByRole("group", {
                name: "2. Materialize standalone game",
            }),
        ).toBeInTheDocument();
        expect(
            screen.getByRole("group", { name: "3. Build game package" }),
        ).toBeInTheDocument();
        expect(
            screen.getByRole("group", { name: "4. Extract game package" }),
        ).toBeInTheDocument();
        expect(
            screen.getByText(
                /success remains release-blocked until reviewed execution/iu,
            ),
        ).toBeInTheDocument();
    });

    it("rejects workflow evidence from a newer same-source authority snapshot", async () => {
        const getCreationWorkflow = vi.fn().mockResolvedValue(
            v3Response("creation_workflow.get", {
                workflow: {
                    state: "active",
                    source_revision: SOURCE_REVISION,
                    status_hash: "9".repeat(64),
                    current_phase: "p02_world_laws",
                    revision: 2,
                    status: {},
                },
            }),
        );
        const { api } = creationApi({ getCreationWorkflow });
        installApi(api);
        render(
            <CreationWorkspace
                workspaceId="creation_workspace"
                generation={1}
                onNavigationStateChange={vi.fn()}
            />,
        );

        expect(await screen.findByRole("alert")).toHaveTextContent(
            "mismatched creation workflow authority",
        );
        expect(
            screen.queryByRole("heading", { name: "Neutral universe" }),
        ).not.toBeInTheDocument();
    });

    it("rejects readiness evidence from a newer same-source authority snapshot", async () => {
        const inspectCreationReadiness = vi.fn().mockResolvedValue(
            v3Response("creation_readiness.inspect", {
                readiness: {
                    state: "authoring_ready",
                    source_revision: SOURCE_REVISION,
                    workflow_status_hash: "9".repeat(64),
                    current_phase: "p02_world_laws",
                    release: "blocked",
                    blocker_reason_codes: [],
                    report: {},
                },
            }),
        );
        const { api } = creationApi({ inspectCreationReadiness });
        installApi(api);
        render(
            <CreationWorkspace
                workspaceId="creation_workspace"
                generation={1}
                onNavigationStateChange={vi.fn()}
            />,
        );

        expect(await screen.findByRole("alert")).toHaveTextContent(
            "mismatched creation readiness authority",
        );
        expect(
            screen.queryByRole("heading", { name: "Neutral universe" }),
        ).not.toBeInTheDocument();
    });

    it("uses roving creation tabs and preserves explicit no-world and no-narrative facets", async () => {
        const { api } = creationApi();
        installApi(api);
        render(
            <CreationWorkspace
                workspaceId="creation_workspace"
                generation={1}
                onNavigationStateChange={vi.fn()}
            />,
        );
        await screen.findByRole("heading", { name: "Neutral universe" });

        const overview = screen.getByRole("tab", { name: "Overview" });
        const profile = screen.getByRole("tab", { name: "Profile" });
        overview.focus();
        fireEvent.keyDown(overview, { key: "ArrowRight" });
        await waitFor(() => expect(profile).toHaveFocus());
        expect(profile).toHaveAttribute("aria-selected", "true");
        fireEvent.keyDown(profile, { key: "Home" });
        await waitFor(() => expect(overview).toHaveFocus());
        fireEvent.click(profile);

        expect(await screen.findByText("No world")).toBeInTheDocument();
        expect(screen.getByText("No narrative")).toBeInTheDocument();
        const preview = screen.getByLabelText(
            "Normalized creation profile preview",
        );
        expect(preview).toHaveTextContent('"presence": "none"');
        expect(preview).toHaveTextContent('"requirement": "none"');
        expect(
            screen.getByText(
                "No autosave. Staging creates reviewed evidence only.",
            ),
        ).toBeInTheDocument();
    });

    it("guards in-workspace tab switches and discards only local draft state explicitly", async () => {
        const { api } = creationApi();
        installApi(api);
        render(
            <CreationWorkspace
                workspaceId="creation_workspace"
                generation={1}
                onNavigationStateChange={vi.fn()}
            />,
        );
        await screen.findByRole("heading", { name: "Neutral universe" });
        fireEvent.click(screen.getByRole("tab", { name: "Profile" }));
        fireEvent.click(
            screen.getByRole("button", { name: "Edit Fiction JSON" }),
        );
        fireEvent.change(screen.getByLabelText("Fiction facet JSON"), {
            target: { value: '{"genres":["mystery"],"tones":[],"tags":[]}' },
        });
        fireEvent.click(screen.getByRole("tab", { name: "Modules" }));
        expect(
            screen.getByRole("dialog", { name: "Leave Profile?" }),
        ).toBeInTheDocument();
        await waitFor(() =>
            expect(
                screen.getByRole("button", {
                    name: "Discard local draft and switch",
                }),
            ).toHaveFocus(),
        );
        expect(screen.getByRole("tab", { name: "Profile" })).toHaveAttribute(
            "aria-selected",
            "true",
        );
        fireEvent.click(
            screen.getByRole("button", {
                name: "Discard local draft and switch",
            }),
        );
        expect(screen.getByRole("tab", { name: "Modules" })).toHaveAttribute(
            "aria-selected",
            "true",
        );
        await waitFor(() =>
            expect(screen.getByRole("tab", { name: "Modules" })).toHaveFocus(),
        );
        expect(
            await screen.findByRole("heading", { name: "Typed modules" }),
        ).toBeInTheDocument();
    });

    it("rejects strict JSON errors inline and stages only a valid facet draft", async () => {
        const onNavigationStateChange = vi.fn();
        const { api, mocks } = creationApi();
        installApi(api);
        render(
            <CreationWorkspace
                workspaceId="creation_workspace"
                generation={1}
                onNavigationStateChange={onNavigationStateChange}
            />,
        );
        await screen.findByRole("heading", { name: "Neutral universe" });
        fireEvent.click(screen.getByRole("tab", { name: "Profile" }));
        fireEvent.click(
            screen.getByRole("button", { name: "Edit Fiction JSON" }),
        );
        const editor = screen.getByLabelText("Fiction facet JSON");
        fireEvent.change(editor, {
            target: {
                value: '{"genres":[],"genres":["mystery"],"tones":[],"tags":[]}',
            },
        });
        fireEvent.click(
            screen.getByRole("button", { name: "Update Fiction draft" }),
        );
        expect(await screen.findByRole("alert")).toHaveTextContent(
            "duplicate object key",
        );
        expect(mocks.stageCreationProfile).not.toHaveBeenCalled();

        fireEvent.change(editor, {
            target: {
                value: '{"genres":["mystery"],"tones":["focused"],"tags":[]}',
            },
        });
        fireEvent.click(
            screen.getByRole("button", { name: "Update Fiction draft" }),
        );
        expect(
            await screen.findByText(/Draft differs from the verified profile/u),
        ).toBeInTheDocument();
        expect(onNavigationStateChange).toHaveBeenLastCalledWith({
            blocksNavigation: true,
            kind: "draft",
        });
        fireEvent.click(
            screen.getByRole("button", { name: "Stage profile for review" }),
        );

        await waitFor(() =>
            expect(mocks.stageCreationProfile).toHaveBeenCalledTimes(1),
        );
        const stageRequest: unknown =
            mocks.stageCreationProfile.mock.calls[0]?.[0];
        expect(stageRequest).toMatchObject({
            workspaceId: "creation_workspace",
            expectedRootGeneration: 4,
            expectedSourceRevision: SOURCE_REVISION,
            expectedWorkflowStatusHash: null,
            path: "profile.json",
            expectedBaseFileSha256: PROFILE_FILE_SHA,
        });
        expect(stageRequest).not.toHaveProperty("baseProfile");
        expect(
            (stageRequest as { proposedProfile: unknown }).proposedProfile,
        ).toMatchObject({
            fiction: {
                genres: ["mystery"],
                tones: ["focused"],
                tags: [],
            },
        });
        expect(mocks.getCreationChangeset).toHaveBeenCalledWith(
            "creation_changeset",
        );
        expect(mocks.diffCreationChangeset).toHaveBeenCalledWith(
            "creation_changeset",
        );
        expect(
            await screen.findByRole("heading", { name: "Profile review" }),
        ).toBeInTheDocument();
        expect(screen.getByText("profile.json")).toBeInTheDocument();
        expect(
            screen.getByRole("button", { name: "Edit Fiction JSON" }),
        ).toBeDisabled();
        fireEvent.click(screen.getByRole("tab", { name: "Modules" }));
        expect(
            screen.getByRole("dialog", { name: "Leave Profile?" }),
        ).toHaveTextContent("Resolve the staged state");
        expect(
            screen.queryByRole("button", {
                name: "Discard local draft and switch",
            }),
        ).not.toBeInTheDocument();
        fireEvent.click(
            screen.getByRole("button", { name: "Stay in Profile" }),
        );
        expect(screen.getByRole("tab", { name: "Profile" })).toHaveAttribute(
            "aria-selected",
            "true",
        );
    });

    it("protects typed facet text before it is committed to the profile draft", async () => {
        const onNavigationStateChange = vi.fn();
        const { api, mocks } = creationApi();
        installApi(api);
        render(
            <CreationWorkspace
                workspaceId="creation_workspace"
                generation={1}
                onNavigationStateChange={onNavigationStateChange}
            />,
        );
        await screen.findByRole("heading", { name: "Neutral universe" });
        fireEvent.click(screen.getByRole("tab", { name: "Profile" }));
        fireEvent.click(
            screen.getByRole("button", { name: "Edit Fiction JSON" }),
        );
        fireEvent.change(screen.getByLabelText("Fiction facet JSON"), {
            target: { value: '{"genres":["mystery"],"tones":[],"tags":[]}' },
        });

        expect(onNavigationStateChange).toHaveBeenLastCalledWith({
            blocksNavigation: true,
            kind: "facet_buffer",
        });
        expect(
            screen.getByRole("button", { name: "Stage profile for review" }),
        ).toBeDisabled();
        expect(mocks.stageCreationProfile).not.toHaveBeenCalled();
    });

    it("approves and applies with exact record/review/root CAS, then refreshes authority", async () => {
        const { api, mocks } = creationApi();
        installApi(api);
        render(
            <CreationWorkspace
                workspaceId="creation_workspace"
                generation={1}
                onNavigationStateChange={vi.fn()}
            />,
        );
        await screen.findByRole("heading", { name: "Neutral universe" });
        fireEvent.click(screen.getByRole("tab", { name: "Profile" }));
        fireEvent.click(
            screen.getByRole("button", { name: "Edit Experience JSON" }),
        );
        fireEvent.change(screen.getByLabelText("Experience facet JSON"), {
            target: {
                value: '{"player_promise":"A reviewed promise.","audiences":["players"],"experience_goals":["clarity"]}',
            },
        });
        fireEvent.click(
            screen.getByRole("button", { name: "Update Experience draft" }),
        );
        fireEvent.click(
            screen.getByRole("button", { name: "Stage profile for review" }),
        );
        await screen.findByRole("heading", { name: "Profile review" });

        fireEvent.click(
            screen.getByRole("button", { name: "Approve profile changeset" }),
        );
        await waitFor(() =>
            expect(mocks.approveCreationChangeset).toHaveBeenCalledWith(
                "creation_changeset",
                RECORD_HASH,
                REVIEW_HASH,
            ),
        );
        fireEvent.click(
            await screen.findByRole("button", {
                name: "Apply approved profile",
            }),
        );
        await waitFor(() =>
            expect(mocks.applyCreationChangeset).toHaveBeenCalledWith(
                "creation_changeset",
                "9".repeat(64),
                REVIEW_HASH,
                4,
            ),
        );
        await waitFor(() =>
            expect(mocks.openCreationWorkspace).toHaveBeenCalledTimes(2),
        );
    });

    it("refreshes a stale authority conflict without discarding the draft", async () => {
        const conflict = v3Error(
            "conflict",
            "Creation workspace source revision changed",
        );
        const { api, mocks } = creationApi({
            stageCreationProfile: vi.fn().mockResolvedValue(conflict),
        });
        installApi(api);
        render(
            <CreationWorkspace
                workspaceId="creation_workspace"
                generation={1}
                onNavigationStateChange={vi.fn()}
            />,
        );
        await screen.findByRole("heading", { name: "Neutral universe" });
        fireEvent.click(screen.getByRole("tab", { name: "Profile" }));
        fireEvent.click(
            screen.getByRole("button", { name: "Edit Fiction JSON" }),
        );
        const editor = screen.getByLabelText("Fiction facet JSON");
        const draft = '{"genres":["mystery"],"tones":["tense"],"tags":[]}';
        fireEvent.change(editor, { target: { value: draft } });
        fireEvent.click(
            screen.getByRole("button", { name: "Update Fiction draft" }),
        );
        fireEvent.click(
            screen.getByRole("button", { name: "Stage profile for review" }),
        );

        expect(await screen.findByRole("alert")).toHaveTextContent(
            "Creation workspace source revision changed",
        );
        await waitFor(() =>
            expect(mocks.openCreationWorkspace).toHaveBeenCalledTimes(2),
        );
        expect(screen.getByLabelText("Fiction facet JSON")).toHaveValue(
            `${JSON.stringify({ genres: ["mystery"], tones: ["tense"], tags: [] }, null, 2)}\n`,
        );
        expect(
            screen.getByText(/Draft differs from the verified profile/u),
        ).toBeInTheDocument();
    });

    it("surfaces recovery-required changesets and invokes bounded resume recovery", async () => {
        const recoveryRequired = {
            ...changeset("recovery_required"),
            record_hash: "7".repeat(64),
        };
        const { api, mocks } = creationApi({
            stageCreationProfile: vi.fn().mockResolvedValue(
                v3Response("creation_changeset.create", {
                    changeset: recoveryRequired,
                }),
            ),
            getCreationChangeset: vi.fn().mockResolvedValue(
                v3Response("creation_changeset.get", {
                    changeset: recoveryRequired,
                }),
            ),
        });
        installApi(api);
        render(
            <CreationWorkspace
                workspaceId="creation_workspace"
                generation={1}
                onNavigationStateChange={vi.fn()}
            />,
        );
        await screen.findByRole("heading", { name: "Neutral universe" });
        fireEvent.click(screen.getByRole("tab", { name: "Profile" }));
        fireEvent.click(
            screen.getByRole("button", { name: "Edit Fiction JSON" }),
        );
        fireEvent.change(screen.getByLabelText("Fiction facet JSON"), {
            target: { value: '{"genres":["mystery"],"tones":[],"tags":[]}' },
        });
        fireEvent.click(
            screen.getByRole("button", { name: "Update Fiction draft" }),
        );
        fireEvent.click(
            screen.getByRole("button", { name: "Stage profile for review" }),
        );

        expect(await screen.findByRole("alert")).toHaveTextContent(
            "Recovery required",
        );
        fireEvent.click(
            screen.getByRole("button", { name: "Resume recovery" }),
        );
        await waitFor(() =>
            expect(mocks.recoverCreationChangeset).toHaveBeenCalledWith(
                "creation_changeset",
                "resume",
                "7".repeat(64),
                REVIEW_HASH,
                4,
            ),
        );
    });

    it("fails closed when reviewed evidence does not match the staged changeset identity", async () => {
        const mismatched = {
            ...changeset("staged"),
            changeset_id: "other_changeset",
        };
        const { api } = creationApi({
            getCreationChangeset: vi.fn().mockResolvedValue(
                v3Response("creation_changeset.get", {
                    changeset: mismatched,
                }),
            ),
        });
        installApi(api);
        render(
            <CreationWorkspace
                workspaceId="creation_workspace"
                generation={1}
                onNavigationStateChange={vi.fn()}
            />,
        );
        await screen.findByRole("heading", { name: "Neutral universe" });
        fireEvent.click(screen.getByRole("tab", { name: "Profile" }));
        fireEvent.click(
            screen.getByRole("button", { name: "Edit Fiction JSON" }),
        );
        fireEvent.change(screen.getByLabelText("Fiction facet JSON"), {
            target: { value: '{"genres":["mystery"],"tones":[],"tags":[]}' },
        });
        fireEvent.click(
            screen.getByRole("button", { name: "Update Fiction draft" }),
        );
        fireEvent.click(
            screen.getByRole("button", { name: "Stage profile for review" }),
        );

        expect(await screen.findByRole("alert")).toHaveTextContent(
            "mismatched creation changeset evidence",
        );
        expect(
            screen.queryByRole("heading", { name: "Profile review" }),
        ).not.toBeInTheDocument();
    });

    it("does not clear the draft when apply returns a non-terminal changeset", async () => {
        const applyCreationChangeset = vi.fn().mockResolvedValue(
            v3Response("creation_changeset.apply", {
                changeset: {
                    ...changeset("approved"),
                    record_hash: "9".repeat(64),
                },
                workspace: workspace(),
                workflow: {
                    state: "active",
                    source_revision: SOURCE_REVISION,
                    status_hash: null,
                    current_phase: "p01_experience",
                    revision: 1,
                    status: {},
                },
            }),
        );
        const { api, mocks } = creationApi({ applyCreationChangeset });
        installApi(api);
        render(
            <CreationWorkspace
                workspaceId="creation_workspace"
                generation={1}
                onNavigationStateChange={vi.fn()}
            />,
        );
        await screen.findByRole("heading", { name: "Neutral universe" });
        fireEvent.click(screen.getByRole("tab", { name: "Profile" }));
        fireEvent.click(
            screen.getByRole("button", { name: "Edit Experience JSON" }),
        );
        fireEvent.change(screen.getByLabelText("Experience facet JSON"), {
            target: {
                value: '{"player_promise":"Reviewed.","audiences":["players"],"experience_goals":["clarity"]}',
            },
        });
        fireEvent.click(
            screen.getByRole("button", { name: "Update Experience draft" }),
        );
        fireEvent.click(
            screen.getByRole("button", { name: "Stage profile for review" }),
        );
        await screen.findByRole("heading", { name: "Profile review" });
        fireEvent.click(
            screen.getByRole("button", { name: "Approve profile changeset" }),
        );
        fireEvent.click(
            await screen.findByRole("button", {
                name: "Apply approved profile",
            }),
        );

        expect(await screen.findByRole("alert")).toHaveTextContent(
            "non-terminal creation changeset",
        );
        expect(mocks.openCreationWorkspace).toHaveBeenCalledTimes(1);
        expect(
            screen.getByText(/Draft differs from the verified profile/u),
        ).toBeInTheDocument();
    });

    it("offers bounded recovery when a timed-out apply remains applying", async () => {
        const applying = {
            ...changeset("applying"),
            record_hash: "6".repeat(64),
        };
        const getCreationChangeset = vi
            .fn()
            .mockResolvedValueOnce(
                v3Response("creation_changeset.get", {
                    changeset: changeset("staged"),
                }),
            )
            .mockResolvedValueOnce(
                v3Response("creation_changeset.get", { changeset: applying }),
            );
        const { api, mocks } = creationApi({
            applyCreationChangeset: vi
                .fn()
                .mockResolvedValue(
                    v3Error("conflict", "Apply result unavailable"),
                ),
            getCreationChangeset,
        });
        installApi(api);
        render(
            <CreationWorkspace
                workspaceId="creation_workspace"
                generation={1}
                onNavigationStateChange={vi.fn()}
            />,
        );
        await screen.findByRole("heading", { name: "Neutral universe" });
        fireEvent.click(screen.getByRole("tab", { name: "Profile" }));
        fireEvent.click(
            screen.getByRole("button", { name: "Edit Experience JSON" }),
        );
        fireEvent.change(screen.getByLabelText("Experience facet JSON"), {
            target: {
                value: '{"player_promise":"Reviewed.","audiences":["players"],"experience_goals":["clarity"]}',
            },
        });
        fireEvent.click(
            screen.getByRole("button", { name: "Update Experience draft" }),
        );
        fireEvent.click(
            screen.getByRole("button", { name: "Stage profile for review" }),
        );
        await screen.findByRole("heading", { name: "Profile review" });
        fireEvent.click(
            screen.getByRole("button", { name: "Approve profile changeset" }),
        );
        fireEvent.click(
            await screen.findByRole("button", {
                name: "Apply approved profile",
            }),
        );

        expect(
            await screen.findByText("Apply state unresolved"),
        ).toBeInTheDocument();
        fireEvent.click(
            screen.getByRole("button", { name: "Resume unresolved apply" }),
        );
        await waitFor(() =>
            expect(mocks.recoverCreationChangeset).toHaveBeenCalledWith(
                "creation_changeset",
                "resume",
                "6".repeat(64),
                REVIEW_HASH,
                4,
            ),
        );
    });
    it("marks generic production/runtime tabs inapplicable from profile facets and exact runtime target", async () => {
        const cases = [
            {
                assetMode: "authored",
                runtimeIntent: "authoring_only",
                runtimeTarget: null,
                expected: { assets: true, compatibility: true, materialize: true },
            },
            {
                assetMode: "not_applicable",
                runtimeIntent: "compatibility_assessment",
                runtimeTarget: "executable",
                expected: { assets: false, compatibility: true, materialize: true },
            },
            {
                assetMode: "authored",
                runtimeIntent: "compatibility_assessment",
                runtimeTarget: "executable",
                expected: { assets: true, compatibility: true, materialize: true },
            },
            {
                assetMode: "not_applicable",
                runtimeIntent: "authoring_only",
                runtimeTarget: null,
                expected: { assets: false, compatibility: true, materialize: true },
            },
        ] as const;

        for (const item of cases) {
            cleanup();
            const profile = creationProfileWithApplicability(
                item.assetMode,
                item.runtimeIntent,
                item.runtimeTarget,
            );
            const readCreationDocument = vi.fn().mockResolvedValue(
                v3Response("creation_document.read", {
                    source_revision: SOURCE_REVISION,
                    document: {
                        path: "profile.json",
                        format: "world-forge.creation_profile",
                        format_version: 1,
                        id: "neutral_profile",
                        content_hash: profile.content_hash,
                        file_sha256: PROFILE_FILE_SHA,
                        document: profile,
                    },
                }),
            );
            const { api, mocks } = creationApi({
                openCreationWorkspace: gameOpen(),
                readCreationDocument,
            });
            installApi(api);
            render(
                <CreationWorkspace
                    workspaceId="creation_workspace"
                    generation={1}
                    onNavigationStateChange={vi.fn()}
                />,
            );

            await screen.findByRole("heading", { name: "Neutral universe" });
            expect(screen.getByRole("tab", { name: /Assets/iu })).toHaveProperty(
                "disabled",
                !item.expected.assets,
            );
            expect(screen.getByRole("tab", { name: /Compatibility/iu })).toHaveProperty(
                "disabled",
                !item.expected.compatibility,
            );
            expect(screen.getByRole("tab", { name: /Materialize/iu })).toHaveProperty(
                "disabled",
                !item.expected.materialize,
            );
            if (!item.expected.assets) {
                expect(screen.getByText("Assets are not applicable to this creation profile.")).toBeInTheDocument();
            }
            if (!item.expected.compatibility || !item.expected.materialize) {
                expect(screen.getAllByText("No executable runtime target is present for this creation profile.").length).toBeGreaterThan(0);
            }
            if (!item.expected.compatibility) {
                expect(mocks.inspectCreationEvidence).not.toHaveBeenCalled();
            } else {
                await waitFor(() =>
                    expect(mocks.inspectCreationEvidence).toHaveBeenCalledWith({
                        workspaceId: "creation_workspace",
                        expectedRootGeneration: 4,
                        expectedSourceRevision: SOURCE_REVISION,
                        expectedWorkflowStatusHash: null,
                        expectedArtifactSnapshotHash: null,
                    }),
                );
            }
        }
    });

    it("disables runtime tabs only for exact P13 executable absence and no retained runtime artifacts", async () => {
        const absent = creationProfileWithApplicability(
            "authored",
            "authoring_only",
            null,
        );
        const mutations: Array<[string, (profile: ReturnType<typeof creationProfile>) => void]> = [
            ["requested_adapter", (profile) => { profile.runtime_target.requested_adapter = "adapter"; }],
            ["accepted_logic_formats", (profile) => { profile.runtime_target.accepted_logic_formats = [{ format: "world-forge.gamepack", versions: [1] }]; }],
            ["required_features", (profile) => { profile.runtime_target.required_features = ["logic:finite_state"]; }],
            ["optional_features", (profile) => { profile.runtime_target.optional_features = ["runtime:headless"] as never[]; }],
            ["platforms", (profile) => { profile.runtime_target.platforms = ["platform:linux_x86_64"]; }],
            ["renderer", (profile) => { profile.runtime_target.renderer = "raylib"; }],
            ["input_capabilities", (profile) => { profile.runtime_target.input_capabilities = ["input:keyboard"]; }],
            ["asset_formats", (profile) => { profile.runtime_target.asset_formats = ["asset:png"]; }],
            ["save_expected", (profile) => { profile.runtime_target.save_expected = true; }],
            ["replay_expected", (profile) => { profile.runtime_target.replay_expected = true; }],
            ["packaging_target", (profile) => { profile.runtime_target.packaging_target = "standalone desktop directory"; }],
        ];

        for (const [, mutate] of mutations) {
            cleanup();
            const profile = structuredClone(absent);
            mutate(profile);
            await renderRuntimeApplicabilityCase(profile, []);
            expect(screen.getByRole("tab", { name: /Compatibility/iu })).toBeEnabled();
            expect(screen.getByRole("tab", { name: /Materialize/iu })).toBeEnabled();
        }

        cleanup();
        await renderRuntimeApplicabilityCase(absent, []);
        await waitFor(() =>
            expect(screen.getByRole("tab", { name: /Compatibility/iu })).toBeDisabled(),
        );
        expect(screen.getByRole("tab", { name: /Materialize/iu })).toBeDisabled();

        cleanup();
        await renderRuntimeApplicabilityCase(absent, [creationArtifact()]);
        expect(screen.getByRole("tab", { name: /Compatibility/iu })).toBeEnabled();
        expect(screen.getByRole("tab", { name: /Materialize/iu })).toBeEnabled();

        cleanup();
        await renderRuntimeApplicabilityCase(absent, [
            creationArtifactWithSubjectFormat("world-forge.runtime_adapter_registry"),
        ]);
        expect(screen.getByRole("tab", { name: /Compatibility/iu })).toBeEnabled();
        expect(screen.getByRole("tab", { name: /Materialize/iu })).toBeEnabled();

        cleanup();
        await renderRuntimeApplicabilityCase(absent, [
            creationArtifactWithSubjectFormat("world-forge.unknown_runtime_evidence"),
        ]);
        expect(screen.getByRole("tab", { name: /Compatibility/iu })).toBeEnabled();
        expect(screen.getByRole("tab", { name: /Materialize/iu })).toBeEnabled();
    });

    it("moves from Assets to Overview when a refreshed profile makes Assets inapplicable", async () => {
        const authored = creationProfileWithApplicability(
            "authored",
            "compatibility_assessment",
            "executable",
        );
        const noAssets = creationProfileWithApplicability(
            "not_applicable",
            "compatibility_assessment",
            "executable",
        );
        const readCreationDocument = vi
            .fn()
            .mockResolvedValueOnce(profileReadResponse(authored))
            .mockResolvedValueOnce(profileReadResponse(noAssets));
        const { api } = creationApi({
            openCreationWorkspace: gameOpen(),
            readCreationDocument,
        });
        installApi(api);
        const view = render(
            <CreationWorkspace
                workspaceId="creation_workspace"
                generation={1}
                onNavigationStateChange={vi.fn()}
            />,
        );

        await screen.findByRole("heading", { name: "Neutral universe" });
        fireEvent.click(screen.getByRole("tab", { name: /Assets/iu }));
        await screen.findByRole("heading", { name: "Asset production pipeline" });

        view.rerender(
            <CreationWorkspace
                workspaceId="creation_workspace"
                generation={2}
                onNavigationStateChange={vi.fn()}
            />,
        );

        await waitFor(() =>
            expect(screen.getByRole("tab", { name: "Overview" })).toHaveAttribute(
                "aria-selected",
                "true",
            ),
        );
        expect(screen.getByRole("tab", { name: /Assets/iu })).toBeDisabled();
    });

});

function creationApi(overrides: Partial<ForgeStudioApi> = {}) {
    const profile = creationProfile();
    const openCreationWorkspace = vi.fn().mockResolvedValue(
        v3Response("creation_workspace.open", {
            workspace: workspace(),
            route: "generic",
            project_kind: "universe_library",
            source_revision: SOURCE_REVISION,
            workflow_status_hash: null,
            current_phase: "p01_experience",
        }),
    );
    const listCreationDocuments = vi.fn().mockResolvedValue(
        v3Response("creation_document.list", {
            source_revision: SOURCE_REVISION,
            documents: [
                {
                    path: "profile.json",
                    format: "world-forge.creation_profile",
                    format_version: 1,
                    id: "neutral_profile",
                    content_hash: profile.content_hash,
                    file_sha256: PROFILE_FILE_SHA,
                },
            ],
        }),
    );
    const readCreationDocument = vi.fn().mockResolvedValue(
        v3Response("creation_document.read", {
            source_revision: SOURCE_REVISION,
            document: {
                path: "profile.json",
                format: "world-forge.creation_profile",
                format_version: 1,
                id: "neutral_profile",
                content_hash: profile.content_hash,
                file_sha256: PROFILE_FILE_SHA,
                document: profile,
            },
        }),
    );
    const getCreationWorkflow = vi.fn().mockResolvedValue(
        v3Response("creation_workflow.get", {
            workflow: {
                state: "active",
                source_revision: SOURCE_REVISION,
                status_hash: null,
                current_phase: "p01_experience",
                revision: 1,
                status: {},
            },
        }),
    );
    const inspectCreationReadiness = vi.fn().mockResolvedValue(
        v3Response("creation_readiness.inspect", {
            readiness: {
                state: "authoring_ready",
                source_revision: SOURCE_REVISION,
                workflow_status_hash: null,
                current_phase: "p01_experience",
                release: "blocked",
                blocker_reason_codes: ["adapter_not_verified"],
                report: {
                    authoring: "valid",
                    compilation: "not_requested",
                    assets: "unplanned",
                    adapter: "absent",
                    execution: {},
                    packaging: "unverified",
                    release: "blocked",
                },
            },
        }),
    );
    const inspectCreationEvidence = vi.fn().mockResolvedValue(
        v4Response("creation_evidence.inspect", {
            authority: evidenceAuthority(),
            artifact_snapshot_hash: ARTIFACT_SNAPSHOT_HASH,
            evidence: creationEvidence(),
        }),
    );
    const listCreationArtifacts = vi.fn().mockImplementation(({ lifecycle }) =>
        Promise.resolve(
            v4Response("creation_artifact.list", {
                authority: evidenceAuthority(),
                artifact_snapshot_hash: ARTIFACT_SNAPSHOT_HASH,
                artifacts: lifecycle === "active" ? [creationArtifact()] : [],
                next_cursor: null,
                counts: creationEvidence().artifact_counts,
            }),
        ),
    );
    const inspectCreationArtifact = vi.fn().mockResolvedValue(
        v4Response("creation_artifact.inspect", {
            authority: evidenceAuthority(),
            artifact_snapshot_hash: ARTIFACT_SNAPSHOT_HASH,
            artifact: creationArtifact(),
            projection: {
                projection_kind: "gamepack",
                title: "Neutral puzzle gamepack",
                status: "compiled",
                facts: [{ key: "asset_count", value: 2 }],
                lineage: [
                    {
                        relation: "depends_on",
                        artifact_id: "artifact_asset_inventory",
                        lifecycle: "invalidated",
                    },
                ],
            },
        }),
    );
    const listCreationOutputGrants = vi.fn().mockResolvedValue(
        v4Response("creation_output_grant.list", {
            authority: evidenceAuthority(),
            artifact_snapshot_hash: ARTIFACT_SNAPSHOT_HASH,
            grants: [],
            next_cursor: null,
        }),
    );
    const stageCreationProfile = vi.fn().mockResolvedValue(
        v3Response("creation_changeset.create", {
            changeset: changeset("staged"),
        }),
    );
    const getCreationChangeset = vi.fn().mockResolvedValue(
        v3Response("creation_changeset.get", {
            changeset: changeset("staged"),
        }),
    );
    const diffCreationChangeset = vi.fn().mockResolvedValue(
        v3Response("creation_changeset.diff", {
            diff: {
                changeset_id: "creation_changeset",
                workspace_id: "creation_workspace",
                expected_source_revision: SOURCE_REVISION,
                proposed_source_revision: PROPOSED_REVISION,
                review_sha256: REVIEW_HASH,
                operations: [
                    {
                        operation: "replace",
                        path: "profile.json",
                        expected_base_file_sha256: PROFILE_FILE_SHA,
                        expected_base_size: 100,
                        proposed_file_sha256: "1".repeat(64),
                        proposed_size: 110,
                        size_delta: 10,
                    },
                ],
            },
        }),
    );
    const approveCreationChangeset = vi.fn().mockResolvedValue(
        v3Response("creation_changeset.approve", {
            changeset: {
                ...changeset("approved"),
                record_hash: "9".repeat(64),
            },
        }),
    );
    const applyCreationChangeset = vi.fn().mockResolvedValue(
        v3Response("creation_changeset.apply", {
            changeset: { ...changeset("applied"), record_hash: "8".repeat(64) },
            workspace: {
                ...workspace(),
                source_revision: PROPOSED_REVISION,
                root_generation: 5,
            },
            workflow: {
                state: "invalid",
                source_revision: PROPOSED_REVISION,
                status_hash: null,
                current_phase: null,
                revision: null,
                status: null,
            },
        }),
    );
    const recoverCreationChangeset = vi.fn().mockResolvedValue(
        v3Response("creation_changeset.recover", {
            changeset: { ...changeset("applied"), record_hash: "8".repeat(64) },
            workspace: workspace(),
            workflow: {
                state: "active",
                source_revision: SOURCE_REVISION,
                status_hash: null,
                current_phase: "p01_experience",
                revision: 1,
                status: {},
            },
            outcome: "committed",
        }),
    );
    const validateWorld = vi.fn();
    const analyzeWorld = vi.fn();
    const listSourceDocuments = vi.fn();
    const listCreationJobs = vi
        .fn()
        .mockResolvedValue(
            v4Response("creation_job.list", { jobs: [], next_sequence: null }),
        );
    const compileCreationProject = vi.fn();
    const getCreationJob = vi.fn();
    const cancelCreationJob = vi.fn();
    const recoverCreationJob = vi.fn();
    const api = {
        openCreationWorkspace,
        listCreationDocuments,
        readCreationDocument,
        getCreationWorkflow,
        inspectCreationReadiness,
        inspectCreationEvidence,
        listCreationArtifacts,
        inspectCreationArtifact,
        listCreationOutputGrants,
        listCreationJobs,
        compileCreationProject,
        getCreationJob,
        cancelCreationJob,
        recoverCreationJob,
        stageCreationProfile,
        getCreationChangeset,
        diffCreationChangeset,
        approveCreationChangeset,
        applyCreationChangeset,
        recoverCreationChangeset,
        validateWorld,
        analyzeWorld,
        listSourceDocuments,
        ...overrides,
    } as ForgeStudioApi;
    return {
        api,
        mocks: {
            openCreationWorkspace,
            listCreationDocuments,
            readCreationDocument,
            getCreationWorkflow,
            inspectCreationReadiness,
            inspectCreationEvidence,
            listCreationArtifacts,
            inspectCreationArtifact,
            listCreationOutputGrants,
            listCreationJobs,
            compileCreationProject,
            getCreationJob,
            cancelCreationJob,
            recoverCreationJob,
            stageCreationProfile,
            getCreationChangeset,
            diffCreationChangeset,
            approveCreationChangeset,
            applyCreationChangeset,
            recoverCreationChangeset,
            validateWorld,
            analyzeWorld,
            listSourceDocuments,
        },
    };
}

function artifactInspectionResponse(title: string) {
    return v4Response("creation_artifact.inspect", {
        authority: evidenceAuthority(),
        artifact_snapshot_hash: ARTIFACT_SNAPSHOT_HASH,
        artifact: creationArtifact(),
        projection: {
            projection_kind: "gamepack",
            title,
            status: "compiled",
            facts: [{ key: "asset_count", value: 2 }],
            lineage: [],
        },
    });
}

function outputGrant(
    grantId: string,
    state: "ready" | "reserved" | "published" | "recovery_required" | "revoked",
    version = 1,
) {
    const kind = {
        1: "generic_assetpack_directory",
        2: "game_runtime_bundle_directory",
        3: "game_materialization_bundle_directory",
        4: "standalone_game_directory",
        5: "game_package_file",
    }[version];
    return {
        format: "world-forge.studio_creation_output_grant",
        format_version: version,
        grant_id: grantId,
        workspace_id: "creation_workspace",
        kind,
        display_name: grantId.replaceAll("_", "-"),
        state,
        generation:
            state === "ready" ? 0 : state === "recovery_required" ? 2 : 1,
        publication:
            state === "published"
                ? {
                      format: "world-forge.assetpack",
                      format_version: 1,
                      id: "published_assetpack",
                      content_hash: "8".repeat(64),
                      inventory_hash: "9".repeat(64),
                  }
                : null,
        created_at: "2026-08-05T00:00:00Z",
        updated_at: "2026-08-05T00:00:01Z",
    };
}

function assetSealJob(
    jobId: string,
    grantId: string,
    grantGeneration: number,
    state: "queued" | "orphaned",
) {
    return {
        ...creationJob({
            format_version: 3,
            job_id: jobId,
            operation: "asset.release.seal",
            state,
            progress: state,
            error:
                state === "orphaned"
                    ? {
                          code: "recovery_required",
                          message: "Review retained seal evidence",
                          retryable: true,
                      }
                    : null,
            record_hash: `${state === "queued" ? "a" : "b"}`.repeat(64),
        }),
        operation_params: {
            qa_report_artifact_ids: ["artifact_qa"],
            manifest_id: `${grantId}_manifest`,
            target_grant_id: grantId,
            target_grant_generation: grantGeneration,
        },
    };
}

function installApi(api: ForgeStudioApi): void {
    Object.defineProperty(window, "forgeStudio", {
        configurable: true,
        value: api,
    });
}

function v3Response(method: string, result: Record<string, unknown>) {
    return {
        ok: true as const,
        value: {
            protocol: "rpg-world-forge.studio_protocol" as const,
            protocol_version: 3 as const,
            kind: "response" as const,
            request_id: "fixture-request",
            method,
            result,
        },
    };
}

function v4Response(method: string, result: Record<string, unknown>) {
    return {
        ok: true as const,
        value: {
            protocol: "rpg-world-forge.studio_protocol" as const,
            protocol_version: 4 as const,
            kind: "response" as const,
            request_id: "fixture-evidence-request",
            method,
            result,
        },
    };
}

function v4Error(code: "conflict", message: string) {
    return {
        ok: true as const,
        value: {
            protocol: "rpg-world-forge.studio_protocol" as const,
            protocol_version: 4 as const,
            kind: "error" as const,
            request_id: "fixture-evidence-request",
            error: { code, message, details: {} },
        },
    };
}

function gameOpen() {
    return vi.fn().mockResolvedValue(
        v3Response("creation_workspace.open", {
            workspace: { ...workspace(), project_kind: "game" },
            route: "generic",
            project_kind: "game",
            source_revision: SOURCE_REVISION,
            workflow_status_hash: null,
            current_phase: "p01_experience",
        }),
    );
}

function creationJob(overrides: Record<string, unknown> = {}) {
    return {
        format: "world-forge.studio_creation_job",
        format_version: 1,
        job_id: "compile_01",
        workspace_id: "creation_workspace",
        operation: "creation.compile",
        state: "queued",
        generation: 0,
        authority: {
            root_generation: 4,
            source_revision: SOURCE_REVISION,
            workflow_status_hash: null,
            artifact_snapshot_hash: ARTIFACT_SNAPSHOT_HASH,
        },
        inputs: [],
        progress: "queued",
        result: null,
        error: null,
        created_at: "2026-08-04T00:00:00Z",
        started_at: null,
        finished_at: null,
        updated_at: "2026-08-04T00:00:00Z",
        record_hash: RECORD_HASH,
        ...overrides,
    };
}

function creationJobResult(snapshotHash: string) {
    return {
        output_artifact_ids: ["artifact_candidate_gamepack"],
        artifact_snapshot_hash: snapshotHash,
        analysis_status: "passed",
        reason_codes: [],
        cleanup_pending: false,
    };
}

function evidenceResponse(snapshotHash: string, candidateCount: number) {
    const evidence = creationEvidence();
    return v4Response("creation_evidence.inspect", {
        authority: evidenceAuthority(),
        artifact_snapshot_hash: snapshotHash,
        evidence: {
            ...evidence,
            artifact_snapshot_hash: snapshotHash,
            artifact_counts: {
                ...evidence.artifact_counts,
                candidate: candidateCount,
            },
        },
    });
}

function artifactListResponse(
    snapshotHash: string | null,
    lifecycle: string | null,
) {
    if (snapshotHash === null)
        throw new Error("Artifact list requires an exact snapshot");
    const evidence = creationEvidence();
    const hasCandidate = snapshotHash === RESULT_SNAPSHOT_HASH;
    const counts = {
        ...evidence.artifact_counts,
        candidate: hasCandidate ? 1 : 0,
    };
    return v4Response("creation_artifact.list", {
        authority: evidenceAuthority(),
        artifact_snapshot_hash: snapshotHash,
        artifacts:
            lifecycle === "active"
                ? [creationArtifact()]
                : lifecycle === "candidate" && hasCandidate
                  ? [candidateArtifact()]
                  : [],
        next_cursor: null,
        counts,
    });
}

function candidateArtifact() {
    return {
        ...creationArtifact(),
        artifact_id: "artifact_candidate_gamepack",
        lifecycle: "candidate" as const,
        producer: {
            kind: "future_candidate" as const,
            phase_id: null,
            reference_id: "compile_01",
        },
        record_hash: "8".repeat(64),
    };
}

function evidenceAuthority() {
    return {
        workspace_id: "creation_workspace",
        root_generation: 4,
        source_revision: SOURCE_REVISION,
        workflow_status_hash: null,
    };
}

function creationArtifactWithSubjectFormat(format: string) {
    return {
        ...creationArtifact(),
        subject: {
            ...creationArtifact().subject,
            format,
        },
    };
}

function creationArtifact() {
    return {
        format: "world-forge.studio_creation_artifact" as const,
        format_version: 1 as const,
        artifact_id: GAMEPACK_ARTIFACT_ID,
        subject: {
            format: "world-forge.gamepack",
            format_version: 1 as const,
            id: "neutral_puzzle",
            content_hash: "2".repeat(64),
        },
        lifecycle: "active" as const,
        roles: ["compiled_logic"],
        producer: {
            kind: "active_phase_report" as const,
            phase_id: "p10_canon_lock",
            reference_id: "report_p10",
        },
        references: { dependency_count: 2, dependent_count: 4 },
        authority: evidenceAuthority(),
        record_hash: "3".repeat(64),
    };
}

function creationEvidence() {
    return {
        format: "world-forge.studio_creation_evidence" as const,
        format_version: 1 as const,
        evidence_id: "evidence_neutral_puzzle",
        authority: evidenceAuthority(),
        artifact_snapshot_hash: ARTIFACT_SNAPSHOT_HASH,
        artifact_counts: {
            active: 1,
            invalidated: 2,
            historical: 0,
            candidate: 0,
            ignored: 3,
        },
        dimensions: {
            authoring: "valid" as const,
            compilation: "compiled" as const,
            assets: "sealed" as const,
            adapter: "declared" as const,
            execution: [
                {
                    platform: "platform:linux_x86_64",
                    status: "headless_verified" as const,
                    evidence_ids: ["headless_linux"],
                },
            ],
            packaging: "unverified" as const,
            release: "blocked" as const,
        },
        blocker_reason_codes: ["adapter_not_verified"],
        mechanics: {
            artifact_id: "artifact_ledger",
            total: 2,
            status_counts: {
                supported_current: 2,
                game_extension_verified: 0,
                authoring_only: 0,
                blocked: 0,
            },
            required_features: ["logic:finite_state", "input:keyboard"],
            missing_features: ["runtime:native_evidence"],
        },
        runtime: {
            requested_adapter: "gamepack_raylib_2d_puzzle",
            resolved_adapter: "gamepack_raylib_2d_puzzle",
            required_features: ["logic:finite_state", "input:keyboard"],
            missing_features: ["runtime:native_evidence"],
            platforms: [
                {
                    platform: "platform:linux_x86_64",
                    status: "headless_verified" as const,
                    evidence_ids: ["headless_linux"],
                },
            ],
        },
        assets: {
            subject_artifact_id: "artifact_asset_subject",
            target_artifact_id: "artifact_asset_target",
            style_artifact_id: "artifact_asset_style",
            inventory_artifact_id: "artifact_asset_inventory",
            assetpack_artifact_id: "artifact_assetpack",
            inventory_assets: 2,
            lineage_complete: 2,
            lineage_partial: 0,
            qa_passed: 2,
            qa_failed: 0,
            licensed: 2,
        },
        materialization: {
            enabled: false as const,
            state: "blocked" as const,
            prerequisites: [
                {
                    code: "adapter_verified",
                    satisfied: false,
                    message: "Adapter verification is still required.",
                },
                {
                    code: "assets_sealed",
                    satisfied: true,
                    message: "Asset pack is sealed.",
                },
            ],
        },
        readiness: {
            format: "world-forge.creation_readiness" as const,
            format_version: 1 as const,
            id: "readiness_neutral_puzzle",
            content_hash: "4".repeat(64),
        },
        handoff: {
            format: "world-forge.creation_handoff" as const,
            format_version: 1 as const,
            id: "handoff_neutral_puzzle",
            content_hash: "5".repeat(64),
        },
        content_hash: "6".repeat(64),
    };
}

function v3Error(code: "conflict", message: string) {
    return {
        ok: true as const,
        value: {
            protocol: "rpg-world-forge.studio_protocol" as const,
            protocol_version: 3 as const,
            kind: "error" as const,
            request_id: "fixture-request",
            error: { code, message, details: {} },
        },
    };
}

function workspace() {
    return {
        format: "world-forge.studio_creation_workspace" as const,
        format_version: 1 as const,
        workspace_id: "creation_workspace",
        project: {
            format: "world-forge.project" as const,
            format_version: 1 as const,
            id: "neutral_universe",
            content_hash: PROJECT_HASH,
        },
        project_kind: "universe_library" as const,
        source_revision: SOURCE_REVISION,
        workflow_status_hash: null,
        root_generation: 4,
        created_at: "2026-08-01T00:00:00Z",
        updated_at: "2026-08-01T00:00:00Z",
    };
}

function changeset(
    status:
        "staged" | "approved" | "applying" | "applied" | "recovery_required",
) {
    return {
        format: "world-forge.studio_creation_changeset" as const,
        format_version: 1 as const,
        changeset_id: "creation_changeset",
        workspace_id: "creation_workspace",
        status,
        expected_root_generation: 4,
        expected_source_revision: SOURCE_REVISION,
        proposed_source_revision: PROPOSED_REVISION,
        expected_workflow_status_hash: null,
        review_sha256: REVIEW_HASH,
        operations: [
            {
                operation: "replace" as const,
                path: "profile.json",
                expected_base_file_sha256: PROFILE_FILE_SHA,
                expected_base_size: 100,
                proposed_file_sha256: "1".repeat(64),
                proposed_size: 110,
            },
        ],
        created_at: "2026-08-01T00:00:00Z",
        updated_at: "2026-08-01T00:00:00Z",
        record_hash: RECORD_HASH,
    };
}



async function renderRuntimeApplicabilityCase(
    profile: ReturnType<typeof creationProfile>,
    artifacts: ReturnType<typeof creationArtifact>[],
): Promise<void> {
    const readCreationDocument = vi.fn().mockResolvedValue(profileReadResponse(profile));
    const counts = {
        ...creationEvidence().artifact_counts,
        active: artifacts.length,
        candidate: 0,
    };
    const inspectCreationEvidence = vi.fn().mockResolvedValue(
        v4Response("creation_evidence.inspect", {
            authority: evidenceAuthority(),
            artifact_snapshot_hash: ARTIFACT_SNAPSHOT_HASH,
            evidence: {
                ...creationEvidence(),
                artifact_counts: counts,
            },
        }),
    );
    const listCreationArtifacts = vi.fn().mockImplementation(({ lifecycle }) =>
        Promise.resolve(
            v4Response("creation_artifact.list", {
                authority: evidenceAuthority(),
                artifact_snapshot_hash: ARTIFACT_SNAPSHOT_HASH,
                artifacts: lifecycle === "active" ? artifacts : [],
                next_cursor: null,
                counts,
            }),
        ),
    );
    const { api } = creationApi({
        openCreationWorkspace: gameOpen(),
        readCreationDocument,
        inspectCreationEvidence,
        listCreationArtifacts,
    });
    installApi(api);
    render(
        <CreationWorkspace
            workspaceId="creation_workspace"
            generation={1}
            onNavigationStateChange={vi.fn()}
        />,
    );
    await screen.findByRole("heading", { name: "Neutral universe" });
    await waitFor(() => expect(listCreationArtifacts).toHaveBeenCalled());
}

function profileReadResponse(profile: ReturnType<typeof creationProfile>) {
    return v3Response("creation_document.read", {
        source_revision: SOURCE_REVISION,
        document: {
            path: "profile.json",
            format: "world-forge.creation_profile",
            format_version: 1,
            id: "neutral_profile",
            content_hash: profile.content_hash,
            file_sha256: PROFILE_FILE_SHA,
            document: profile,
        },
    });
}

function creationProfileWithApplicability(
    assetMode: "authored" | "not_applicable",
    runtimeIntent: "authoring_only" | "compatibility_assessment",
    runtimeTarget: "executable" | null,
) {
    const profile = creationProfile();
    profile.production.content_modes.assets = assetMode;
    if (runtimeIntent === "authoring_only" || runtimeTarget === null) {
        profile.runtime_target = {
            requested_adapter: "",
            accepted_logic_formats: [],
            required_features: [],
            optional_features: [],
            presentation_mode: "2d",
            platforms: [],
            renderer: "",
            input_capabilities: [],
            asset_formats: [],
            save_expected: false,
            replay_expected: false,
            packaging_target: "",
        };
    }
    return profile;
}

function creationProfile() {
    return {
        content_hash: "2".repeat(64),
        experience: {
            player_promise: "Explore a neutral universe library.",
            audiences: ["creators"],
            experience_goals: ["coherence"],
        },
        extensions: [],
        fiction: { genres: [], tones: ["focused"], tags: [] },
        format: "world-forge.creation_profile" as const,
        format_version: 1 as const,
        gameplay: {
            primary_family: "puzzle",
            secondary_families: [],
            mechanic_tags: [],
            player_role: "solver",
            core_verbs: [{ id: "inspect", description: "Inspect state." }],
            core_loop: ["inspect"],
            rule_model: "deterministic",
            goal_model: "complete",
            challenge_model: "bounded",
            failure_recovery: "restart",
            progression: "finite",
            teleology: "finite",
            session_structure: "short",
            social_topology: "single_player",
            dependencies: { authored: [], systemic: [], procedural: [] },
        },
        narrative: {
            requirement: "none",
            authorship_mode: "none",
            topology: "none",
            delivery_channels: [],
            protagonist_model: "none",
            agency: "none",
            focalization: "none",
            canon_variability: "none",
            pacing: "none",
            endings: "none",
            information_model: "none",
        },
        presentation: {
            mode: "2d",
            camera: "fixed",
            perspective: "orthographic",
            visual_language: "neutral",
            ui_density: "low",
            audio_role: "feedback",
            input_assumptions: ["input:keyboard"],
            accessibility: {
                remapping: true,
                keyboard_only: true,
                captions: true,
                text_scaling: true,
                high_contrast: true,
                color_independence: true,
                reduced_motion: true,
                timing_alternatives: true,
                screen_reader_structure: true,
            },
            localization: {
                source_locale: "en",
                supported_locales: ["en"],
                externalized_text: true,
            },
        },
        production: {
            content_modes: {
                gameplay: "authored",
                world: "not_applicable",
                narrative: "not_applicable",
                assets: "authored",
            },
            seed_policy: "none",
            reproducibility: "content addressed",
            selection_policy: "reviewed",
            human_review: true,
            provenance_required: true,
            licensing_required: true,
            qa_required: true,
        },
        profile_id: "neutral_profile",
        project_id: "neutral_universe",
        runtime_target: {
            requested_adapter: "gamepack_raylib_2d_puzzle",
            accepted_logic_formats: [
                { format: "world-forge.gamepack", versions: [1] },
            ],
            required_features: ["logic:finite_state"],
            optional_features: [],
            presentation_mode: "2d",
            platforms: ["platform:linux_x86_64"],
            renderer: "raylib",
            input_capabilities: ["input:keyboard"],
            asset_formats: ["asset:png"],
            save_expected: true,
            replay_expected: true,
            packaging_target: "standalone desktop directory",
        },
        title: "Neutral universe",
        world: {
            presence: "none",
            spatial_topology: "none",
            scale: "none",
            time_model: "none",
            simulation_depth: "none",
            simulated_domains: [],
            persistence: "none",
            spatial_structure: "none",
        },
    };
}

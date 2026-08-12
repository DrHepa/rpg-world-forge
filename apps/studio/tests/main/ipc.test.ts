import { createHash } from "node:crypto";

import { describe, expect, it, vi } from "vitest";

import {
    registerStudioIpc,
    validateAssetCatalogInspectArgument,
    validateAssetCatalogListArgument,
    validateAssetPreviewCloseArgument,
    validateAssetPreviewOpenArgument,
    validateAssetPreviewReadArgument,
    validateAssetpackArgument,
    validateAssetReceiptArgument,
    validateCancelJobArgument,
    validateCreationChangesetActionArgument,
    validateCreationChangesetRecoveryArgument,
    validateCreationArtifactAdmissionArgument,
    validateCreationArtifactInspectArgument,
    validateCreationArtifactListArgument,
    validateCreationAssetProcessArgument,
    validateCreationAssetReleaseSealArgument,
    validateCreationMaterializationBundleBuildArgument,
    validateCreationGameMaterializeArgument,
    validateCreationGamePackageArgument,
    validateCreationGamePackageExtractArgument,
    validateCreationRuntimeBundleBuildArgument,
    validateCreationRuntimeComposeArgument,
    validateCreationCompileArgument,
    validateCreationDocumentArgument,
    validateCreationEvidenceInspectArgument,
    validateCreationPreviewCloseArgument,
    validateCreationPreviewOpenArgument,
    validateCreationPreviewReadArgument,
    validateCreationEventListArgument,
    validateCreationJobIdArgument,
    validateCreationJobListArgument,
    validateCreationJobMutationArgument,
    validateCreationJobRecoveryArgument,
    validateCreationAuthorityHeadlessArgument,
    validateCreationAuthorityJobActionArgument,
    validateCreationAuthorityReleaseArgument,
    validateCreationAuthorityReviewArgument,
    validateCreationOutputGrantGetArgument,
    validateCreationOutputGrantListArgument,
    validateCreationOutputGrantRevokeArgument,
    validateCreationOutputGrantSelectArgument,
    validateCreationModuleStageArgument,
    validateCreationPhaseReadArgument,
    validateCreationPhaseReportArgument,
    validateCreationPhaseReopenArgument,
    validateCreationProfileStageArgument,
    validateCreationProjectCreateArgument,
    validateChangesetActionArgument,
    validateChangesetIdArgument,
    validateChangesetsListParams,
    validateEventsListParams,
    validateExternalGrantCreateArgument,
    validateExternalJobIdArgument,
    validateExternalJobsListParams,
    validateExternalJobRecoveryArgument,
    validateExtractGamePackageArgument,
    validateHeadlessArgument,
    validateJobsListParams,
    validateInterruptTurnArgument,
    validateLoginArgument,
    validateMaterializeGameArgument,
    validatePackageGameArgument,
    validateReplayArgument,
    validateStageSourceDocumentArgument,
    validateSourceReadArgument,
    validateStartTurnArgument,
    validateUserInputArgument,
    validateWorkspaceArgument,
} from "../../src/main/ipc";
import { IPC_CHANNELS } from "../../src/shared/studio-api";

describe("Studio named authoring and job IPC contracts", () => {
    it("accepts only exact workspace, source, and fixed-operation inputs", () => {
        expect(
            validateSourceReadArgument({
                workspaceId: "workspace_01",
                path: "source/lore/entry.md",
            }),
        ).toEqual({
            workspaceId: "workspace_01",
            path: "source/lore/entry.md",
        });
        expect(
            validateAssetReceiptArgument({
                workspaceId: "workspace_01",
                input: { receipt: "receipts/item.json" },
            }),
        ).toEqual({
            workspaceId: "workspace_01",
            input: { receipt: "receipts/item.json" },
        });
        expect(
            validateAssetpackArgument({
                workspaceId: "workspace_01",
                input: {
                    assetpack: "build/assets.json",
                    worldpack: "build/world.json",
                },
            }),
        ).toEqual({
            workspaceId: "workspace_01",
            input: {
                assetpack: "build/assets.json",
                worldpack: "build/world.json",
            },
        });
        expect(
            validateHeadlessArgument({
                workspaceId: "workspace_01",
                input: { worldpack: "build/world.json", ticks: 0 },
            }),
        ).toEqual({
            workspaceId: "workspace_01",
            input: { worldpack: "build/world.json", ticks: 0 },
        });
        expect(
            validateReplayArgument({
                workspaceId: "workspace_01",
                input: {
                    worldpack: "build/world.json",
                    replay: "replays/slot.json",
                },
            }),
        ).toEqual({
            workspaceId: "workspace_01",
            input: {
                worldpack: "build/world.json",
                replay: "replays/slot.json",
            },
        });
        expect(validateCancelJobArgument({ jobId: "job_01" })).toEqual({
            jobId: "job_01",
        });
    });

    it.each([
        [
            validateSourceReadArgument,
            { workspaceId: "workspace_01", path: "../world.json" },
        ],
        [
            validateSourceReadArgument,
            { workspaceId: "workspace_01", path: "source/../world.json" },
        ],
        [
            validateSourceReadArgument,
            { workspaceId: "workspace_01", path: "source/CON.json" },
        ],
        [
            validateAssetReceiptArgument,
            {
                workspaceId: "workspace_01",
                input: { receipt: "receipt.json", operation: "shell.execute" },
            },
        ],
        [
            validateAssetpackArgument,
            {
                workspaceId: "workspace_01",
                input: {
                    assetpack: "pack.json",
                    worldpack: "world.json",
                    cwd: "/tmp",
                },
            },
        ],
        [
            validateHeadlessArgument,
            {
                workspaceId: "workspace_01",
                input: { worldpack: "world.json", ticks: true },
            },
        ],
        [
            validateHeadlessArgument,
            {
                workspaceId: "workspace_01",
                input: { worldpack: "world.json", ticks: -1 },
            },
        ],
        [
            validateHeadlessArgument,
            {
                workspaceId: "workspace_01",
                input: { worldpack: "world.json", ticks: 1_000_001 },
            },
        ],
        [
            validateReplayArgument,
            {
                workspaceId: "workspace_01",
                input: {
                    worldpack: "world.json",
                    replay: "slot.json",
                    env: { PATH: "/tmp" },
                },
            },
        ],
        [validateCancelJobArgument, { jobId: "../job" }],
    ])(
        "rejects malformed or capability-shaped authoring/job input %#",
        (validate, value) => {
            expect(() => validate(value)).toThrow();
        },
    );
});

describe("Studio fixed generic creation IPC contracts", () => {
    const hash = "a".repeat(64);
    const recordHash = "b".repeat(64);
    const reviewHash = "c".repeat(64);

    it("accepts only closed pathless project, document, and exact CAS inputs", () => {
        expect(
            validateCreationProjectCreateArgument({
                projectKind: "universe_library",
                projectId: "neutral_universe",
                title: "Neutral universe",
                defaultLocale: "en",
                projectVersion: "0.1.0",
            }),
        ).toMatchObject({
            projectKind: "universe_library",
            projectId: "neutral_universe",
        });
        expect(
            validateCreationProjectCreateArgument({
                projectKind: "game",
                projectId: "neutral_game",
                title: "Neutral game",
                defaultLocale: "en",
                projectVersion: "0.1.0",
                gameplayFamily: "narrative",
                initialCoreVerb: "choose",
                initialCoreLoop: "read, choose, and observe the consequence",
                worldPresence: "abstract",
                narrativeRequirement: "required",
                narrativeAuthorship: "authored",
                narrativeTopology: "branching",
                presentationMode: "text",
                runtimeSupportIntent: "compatibility_assessment",
            }),
        ).toEqual({
            projectKind: "game",
            projectId: "neutral_game",
            title: "Neutral game",
            defaultLocale: "en",
            projectVersion: "0.1.0",
            gameplayFamily: "narrative",
            initialCoreVerb: "choose",
            initialCoreLoop: "read, choose, and observe the consequence",
            worldPresence: "abstract",
            narrativeRequirement: "required",
            narrativeAuthorship: "authored",
            narrativeTopology: "branching",
            presentationMode: "text",
            runtimeSupportIntent: "compatibility_assessment",
        });
        expect(
            validateCreationDocumentArgument({
                workspaceId: "creation_workspace",
                expectedSourceRevision: hash,
                path: "profile.json",
            }),
        ).toEqual({
            workspaceId: "creation_workspace",
            expectedSourceRevision: hash,
            path: "profile.json",
        });
        expect(
            validateCreationChangesetActionArgument({
                changesetId: "creation_changeset",
                expectedRecordHash: recordHash,
                expectedReviewSha256: reviewHash,
            }),
        ).toEqual({
            changesetId: "creation_changeset",
            expectedRecordHash: recordHash,
            expectedReviewSha256: reviewHash,
        });
        expect(
            validateCreationChangesetRecoveryArgument({
                changesetId: "creation_changeset",
                mode: "resume",
                expectedRecordHash: recordHash,
                expectedReviewSha256: reviewHash,
                expectedRootGeneration: 3,
            }),
        ).toMatchObject({ mode: "resume", expectedRootGeneration: 3 });
        expect(
            validateCreationProfileStageArgument({
                workspaceId: "creation_workspace",
                expectedRootGeneration: 3,
                expectedSourceRevision: hash,
                expectedWorkflowStatusHash: null,
                path: "profile.json",
                expectedBaseFileSha256: recordHash,
                proposedProfile: minimalProfile("Updated"),
            }),
        ).toMatchObject({
            workspaceId: "creation_workspace",
            path: "profile.json",
        });

        for (const value of [
            {
                projectKind: "game",
                projectId: "neutral_game",
                title: "Neutral game",
                defaultLocale: "en",
                projectVersion: "0.1.0",
                gameplayFamily: "puzzle",
            },
            {
                projectKind: "universe_library",
                projectId: "neutral_universe",
                title: "Neutral universe",
                defaultLocale: "en",
                projectVersion: "0.1.0",
                path: "/tmp/forbidden",
            },
            {
                projectKind: "game",
                projectId: "neutral_game",
                title: "Neutral game",
                defaultLocale: "en",
                projectVersion: "0.1.0",
                gameplayFamily: "puzzle",
                initialCoreVerb: "solve-action",
                initialCoreLoop: "inspect and solve",
                worldPresence: "none",
                narrativeRequirement: "none",
                narrativeAuthorship: "none",
                narrativeTopology: "none",
                presentationMode: "2d",
                runtimeSupportIntent: "authoring_only",
            },
        ]) {
            expect(() =>
                validateCreationProjectCreateArgument(value),
            ).toThrow();
        }
        expect(() =>
            validateCreationDocumentArgument({
                workspaceId: "creation_workspace",
                expectedSourceRevision: hash,
                path: "../profile.json",
            }),
        ).toThrow();
        expect(() =>
            validateCreationChangesetRecoveryArgument({
                changesetId: "creation_changeset",
                mode: "force",
                expectedRecordHash: recordHash,
                expectedReviewSha256: reviewHash,
                expectedRootGeneration: 3,
            }),
        ).toThrow();
    });

    it("keeps native registration paths private and registers through protocol v3", async () => {
        const harness = createIpcHarness({
            projectSelection: vi.fn().mockResolvedValue({
                contentHash: hash,
                displayName: "Neutral universe",
            }),
        });
        harness.request
            .mockResolvedValueOnce(
                v3Response("creation_root_grant.create", {
                    grant: creationGrant("existing_root", hash),
                }),
            )
            .mockImplementationOnce((requestId: string, method: string) =>
                Promise.resolve(
                    v3Response(
                        method,
                        { workspace: creationWorkspace() },
                        requestId,
                    ),
                ),
            );

        const result = await harness.invokeNoArgs(
            IPC_CHANNELS.registerCreationProject,
        );

        expect(harness.showOpenDialog).toHaveBeenCalledWith(
            expect.anything(),
            expect.objectContaining({ properties: ["openDirectory"] }),
        );
        expect(
            harness.request.mock.calls.map((call) => [call[1], call[4]]),
        ).toEqual([
            ["creation_root_grant.create", 3],
            ["creation_workspace.register", 3],
        ]);
        expect(harness.request.mock.calls[0]?.[2]).toMatchObject({
            role: "existing_root",
            path: "/selected/source-directory",
            expected_project_hash: hash,
        });
        expect(harness.request.mock.calls[1]?.[2]).not.toHaveProperty("path");
        expect(JSON.stringify(result)).not.toContain("/selected/");
        expect(JSON.stringify(result)).not.toContain("grant_creation");
    });

    it("creates a generic game at an absent target and maps every facet exactly", async () => {
        const harness = createIpcHarness();
        harness.request
            .mockResolvedValueOnce(
                v3Response("creation_root_grant.create", {
                    grant: creationGrant("new_target", null),
                }),
            )
            .mockImplementationOnce((requestId: string, method: string) =>
                Promise.resolve(
                    v5Response(
                        requestId,
                        method,
                        { workspace: creationWorkspace() },
                    ),
                ),
            );

        const result = await harness.invoke(
            IPC_CHANNELS.createCreationProject,
            {
                projectKind: "game",
                projectId: "neutral_game",
                title: "Neutral game",
                defaultLocale: "en",
                projectVersion: "0.1.0",
                gameplayFamily: "narrative",
                initialCoreVerb: "choose",
                initialCoreLoop: "read, choose, and observe the consequence",
                worldPresence: "abstract",
                narrativeRequirement: "required",
                narrativeAuthorship: "authored",
                narrativeTopology: "branching",
                presentationMode: "text",
                runtimeSupportIntent: "compatibility_assessment",
                assetContentMode: "not_applicable",
            },
        );

        expect(harness.showSaveDialog).toHaveBeenCalledTimes(1);
        expect(
            harness.request.mock.calls.map((call) => [call[1], call[4]]),
        ).toEqual([
            ["creation_root_grant.create", 3],
            ["creation_workspace.create", 5],
        ]);
        expect(harness.request.mock.calls[0]?.[2]).toMatchObject({
            role: "new_target",
            path: "/selected/target-1",
            expected_project_hash: null,
        });
        const createParams = harness.request.mock.calls[1]?.[2];
        expect(createParams?.workspace_id).toMatch(/^creation_[a-f0-9]{32}$/u);
        expect({ ...createParams, workspace_id: "<generated>" }).toEqual({
            workspace_id: "<generated>",
            grant_id: "grant_creation",
            expected_grant_generation: 0,
            project_kind: "game",
            project_id: "neutral_game",
            title: "Neutral game",
            default_locale: "en",
            project_version: "0.1.0",
            gameplay_family: "narrative",
            initial_core_verb: "choose",
            initial_core_loop: "read, choose, and observe the consequence",
            world_presence: "abstract",
            narrative_requirement: "required",
            narrative_authorship: "authored",
            narrative_topology: "branching",
            presentation_mode: "text",
            runtime_support_intent: "compatibility_assessment",
            asset_content_mode: "not_applicable",
        });
        expect(JSON.stringify(result)).not.toContain("/selected/");
    });

    it("keeps omitted asset content mode on the legacy v3 create request", async () => {
        const harness = createIpcHarness();
        harness.request
            .mockResolvedValueOnce(
                v3Response("creation_root_grant.create", {
                    grant: creationGrant("new_target", null),
                }),
            )
            .mockImplementationOnce((requestId: string, method: string) =>
                Promise.resolve(
                    v3Response(
                        method,
                        { workspace: creationWorkspace() },
                        requestId,
                    ),
                ),
            );

        await harness.invoke(IPC_CHANNELS.createCreationProject, {
            projectKind: "game",
            projectId: "neutral_game",
            title: "Neutral game",
            defaultLocale: "en",
            projectVersion: "0.1.0",
            gameplayFamily: "puzzle",
            initialCoreVerb: "solve",
            initialCoreLoop: "inspect and solve",
            worldPresence: "none",
            narrativeRequirement: "none",
            narrativeAuthorship: "none",
            narrativeTopology: "none",
            presentationMode: "2d",
            runtimeSupportIntent: "authoring_only",
        });

        expect(harness.request.mock.calls.map((call) => [call[1], call[4]])).toEqual([
            ["creation_root_grant.create", 3],
            ["creation_workspace.create", 3],
        ]);
        expect(harness.request.mock.calls[1]?.[2]).not.toHaveProperty("asset_content_mode");
    });

    it("rejects game-only asset content mode on non-game project creation before transport", async () => {
        const harness = createIpcHarness();

        const result = await harness.invoke(IPC_CHANNELS.createCreationProject, {
            projectKind: "universe_library",
            projectId: "neutral_library",
            title: "Neutral library",
            defaultLocale: "en",
            projectVersion: "0.1.0",
            assetContentMode: "authored",
        });

        expect(result).toEqual({
            ok: false,
            error: {
                code: "invalid_request",
                message: "Studio library creation cannot include game facets",
            },
        });
        expect(harness.showSaveDialog).not.toHaveBeenCalled();
        expect(harness.request).not.toHaveBeenCalled();
    });

    it("rejects unknown asset content modes before Studio transport", async () => {
        const harness = createIpcHarness();

        const result = await harness.invoke(IPC_CHANNELS.createCreationProject, {
            projectKind: "game",
            projectId: "neutral_game",
            title: "Neutral game",
            defaultLocale: "en",
            projectVersion: "0.1.0",
            gameplayFamily: "puzzle",
            initialCoreVerb: "solve",
            initialCoreLoop: "inspect and solve",
            worldPresence: "none",
            narrativeRequirement: "none",
            narrativeAuthorship: "none",
            narrativeTopology: "none",
            presentationMode: "2d",
            runtimeSupportIntent: "authoring_only",
            assetContentMode: "unknown",
        });

        expect(result).toEqual({
            ok: false,
            error: {
                code: "invalid_request",
                message: "Studio game asset content mode is invalid",
            },
        });
        expect(harness.showSaveDialog).not.toHaveBeenCalled();
        expect(harness.request).not.toHaveBeenCalled();
    });

    it("maps generic reads and stages an authority-loaded profile graph atomically", async () => {
        const harness = createIpcHarness();
        const graph = creationGraphFixture();

        await harness.invokeNoArgs(IPC_CHANNELS.listCreationWorkspaces);
        await harness.invoke(IPC_CHANNELS.openCreationWorkspace, {
            workspaceId: "creation_workspace",
        });
        await harness.invoke(IPC_CHANNELS.listCreationDocuments, {
            workspaceId: "creation_workspace",
            expectedSourceRevision: hash,
        });
        await harness.invoke(IPC_CHANNELS.readCreationDocument, {
            workspaceId: "creation_workspace",
            expectedSourceRevision: hash,
            path: "profile.json",
        });
        await harness.invoke(IPC_CHANNELS.getCreationWorkflow, {
            workspaceId: "creation_workspace",
        });
        await harness.invoke(IPC_CHANNELS.inspectCreationReadiness, {
            workspaceId: "creation_workspace",
        });
        queueCreationGraphReads(harness.request, graph);
        await harness.invoke(IPC_CHANNELS.stageCreationProfile, {
            workspaceId: "creation_workspace",
            expectedRootGeneration: 4,
            expectedSourceRevision: hash,
            expectedWorkflowStatusHash: null,
            path: "profile.json",
            expectedBaseFileSha256: graph.files.profile.fileSha256,
            proposedProfile: { ...graph.profile, title: "Updated" },
        });

        expect(
            harness.request.mock.calls.map((call) => [call[1], call[4]]),
        ).toEqual([
            ["creation_workspace.list", 3],
            ["creation_workspace.open", 3],
            ["creation_document.list", 3],
            ["creation_document.read", 3],
            ["creation_workflow.get", 3],
            ["creation_readiness.inspect", 3],
            ["creation_document.list", 3],
            ["creation_document.read", 3],
            ["creation_document.read", 3],
            ["creation_document.read", 3],
            ["creation_changeset.create", 3],
        ]);
        const operations = harness.request.mock.calls.at(-1)?.[2]
            .operations as Record<string, unknown>[];
        expect(operations).toHaveLength(3);
        const [profileOperation, manifestOperation, projectOperation] =
            operations;
        expect(profileOperation).toMatchObject({
            operation: "replace",
            path: "profile.json",
            expected_base_file_sha256: graph.files.profile.fileSha256,
            expected_base_size: graph.files.profile.bytes.byteLength,
        });
        expect(profileOperation.document).toMatchObject({ title: "Updated" });
        expect(
            (profileOperation.document as Record<string, unknown>).content_hash,
        ).toMatch(/^[0-9a-f]{64}$/u);
        expect(profileOperation.proposed_file_sha256).toBe(
            createHash("sha256")
                .update(canonicalDocumentBytes(profileOperation.document))
                .digest("hex"),
        );
        expect(manifestOperation).toMatchObject({
            operation: "replace",
            path: "source/manifest.json",
        });
        const manifestDocument = requireRecord(manifestOperation.document);
        expect(requireRecord(manifestDocument.profile).content_hash).toBe(
            (profileOperation.document as Record<string, unknown>).content_hash,
        );
        expect(projectOperation).toMatchObject({
            operation: "replace",
            path: "project.json",
        });
        const projectDocument = requireRecord(projectOperation.document);
        expect(requireRecord(projectDocument.profile).content_hash).toBe(
            (profileOperation.document as Record<string, unknown>).content_hash,
        );
        expect(
            requireRecord(projectDocument.source_manifest).content_hash,
        ).toBe(
            (manifestOperation.document as Record<string, unknown>)
                .content_hash,
        );
    });

    it("stages a module, manifest, and project graph without renderer-owned snapshots", async () => {
        const harness = createIpcHarness();
        const graph = creationGraphFixture(true);
        queueCreationGraphReads(harness.request, graph, true);

        await harness.invoke(IPC_CHANNELS.stageCreationModuleChange, {
            workspaceId: "creation_workspace",
            expectedRootGeneration: 4,
            expectedSourceRevision: hash,
            expectedWorkflowStatusHash: hash,
            operation: "replace",
            path: "source/logic/core.json",
            format: "world-forge.logic_module",
            expectedBaseFileSha256: graph.files.module?.fileSha256,
            proposedModule: { ...graph.module, title: "Updated logic" },
        });

        expect(harness.request.mock.calls.map((call) => call[1])).toEqual([
            "creation_document.list",
            "creation_document.read",
            "creation_document.read",
            "creation_document.read",
            "creation_document.read",
            "creation_changeset.create",
        ]);
        const operations = harness.request.mock.calls.at(-1)?.[2]
            .operations as Record<string, unknown>[];
        expect(
            operations.map((operation) => [
                operation.operation,
                operation.path,
            ]),
        ).toEqual([
            ["replace", "source/logic/core.json"],
            ["replace", "source/manifest.json"],
            ["replace", "project.json"],
        ]);
        const manifestModules = requireRecord(
            requireRecord(operations[1].document).modules,
        );
        if (!Array.isArray(manifestModules.logic_modules)) {
            throw new Error("Expected logic_modules array");
        }
        expect(manifestModules.logic_modules[0]).toMatchObject({
            id: "core",
            path: "logic/core.json",
            content_hash: (operations[0].document as Record<string, unknown>)
                .content_hash,
        });
        expect(
            requireRecord(requireRecord(operations[2].document).source_manifest)
                .content_hash,
        ).toBe(
            (operations[1].document as Record<string, unknown>).content_hash,
        );
    });

    it("sorts added module references by canonical UTF-8 bytes rather than host collation", async () => {
        const harness = createIpcHarness();
        const graph = creationGraphFixture(true, "a_");
        queueCreationGraphReads(harness.request, graph, true);

        await harness.invoke(IPC_CHANNELS.stageCreationModuleChange, {
            workspaceId: "creation_workspace",
            expectedRootGeneration: 4,
            expectedSourceRevision: hash,
            expectedWorkflowStatusHash: hash,
            operation: "create",
            path: "source/logic/a-.json",
            format: "world-forge.logic_module",
            expectedBaseFileSha256: null,
            proposedModule: {
                format: "world-forge.logic_module",
                format_version: 1,
                module_id: "a-",
                project_id: "neutral_universe",
                title: "Canonical first",
                content_hash: "0".repeat(64),
            },
        });

        const operations = harness.request.mock.calls.at(-1)?.[2]
            .operations as Record<string, unknown>[];
        const modules = requireRecord(
            requireRecord(operations[1].document).modules,
        );
        if (!Array.isArray(modules.logic_modules))
            throw new Error("Expected logic modules");
        expect(
            modules.logic_modules.map((value) => requireRecord(value).id),
        ).toEqual(["a-", "a_"]);
    });

    it.each(["source/manifest.json", "source/MANIFEST.json"])(
        "rejects module creation on an existing graph path collision: %s",
        async (pathValue) => {
            const harness = createIpcHarness();
            const graph = creationGraphFixture();
            queueCreationGraphReads(harness.request, graph);

            expect(
                await harness.invoke(IPC_CHANNELS.stageCreationModuleChange, {
                    workspaceId: "creation_workspace",
                    expectedRootGeneration: 4,
                    expectedSourceRevision: hash,
                    expectedWorkflowStatusHash: hash,
                    operation: "create",
                    path: pathValue,
                    format: "world-forge.logic_module",
                    expectedBaseFileSha256: null,
                    proposedModule: {
                        format: "world-forge.logic_module",
                        format_version: 1,
                        module_id: "new_module",
                        project_id: "neutral_universe",
                        title: "Must not shadow the manifest",
                        content_hash: "0".repeat(64),
                    },
                }),
            ).toMatchObject({ ok: false, error: { code: "invalid_request" } });
            expect(
                harness.request.mock.calls.map((call) => call[1]),
            ).not.toContain("creation_changeset.create");
        },
    );

    it("maps workflow and phase actions to fixed pathless v3 methods", async () => {
        const harness = createIpcHarness();
        const authority = {
            workspaceId: "creation_workspace",
            expectedRootGeneration: 4,
            expectedSourceRevision: hash,
            expectedWorkflowStatusHash: hash,
        };
        const report = {
            format: "world-forge.phase_report",
            format_version: 3,
            phase: "p00_brief",
        };
        const artifactRegistry = [{ artifact_id: "brief", content_hash: hash }];

        await harness.invoke(IPC_CHANNELS.reconcileCreationWorkflow, {
            ...authority,
            artifactRegistry,
        });
        await harness.invoke(IPC_CHANNELS.readCreationPhase, {
            ...authority,
            phaseId: "p00_brief",
        });
        await harness.invoke(IPC_CHANNELS.validateCreationPhase, {
            ...authority,
            report,
            artifactRegistry,
        });
        await harness.invoke(IPC_CHANNELS.completeCreationPhase, {
            ...authority,
            report,
            artifactRegistry,
        });
        await harness.invoke(IPC_CHANNELS.reopenCreationPhase, {
            ...authority,
            phaseId: "p00_brief",
            reason: "Requirements changed",
            approvedBy: "reviewer_01",
        });

        expect(
            harness.request.mock.calls.map((call) => [
                call[1],
                call[2],
                call[4],
            ]),
        ).toEqual([
            [
                "creation_workflow.reconcile",
                {
                    workspace_id: "creation_workspace",
                    expected_root_generation: 4,
                    expected_source_revision: hash,
                    expected_workflow_status_hash: hash,
                    artifact_registry: artifactRegistry,
                },
                3,
            ],
            [
                "creation_phase.read",
                {
                    workspace_id: "creation_workspace",
                    expected_root_generation: 4,
                    expected_source_revision: hash,
                    expected_workflow_status_hash: hash,
                    phase_id: "p00_brief",
                },
                3,
            ],
            [
                "creation_phase.validate",
                expect.objectContaining({
                    report,
                    artifact_registry: artifactRegistry,
                }),
                3,
            ],
            [
                "creation_phase.complete",
                expect.objectContaining({
                    report,
                    artifact_registry: artifactRegistry,
                }),
                3,
            ],
            [
                "creation_phase.reopen",
                expect.objectContaining({
                    phase_id: "p00_brief",
                    reason: "Requirements changed",
                    approved_by: "reviewer_01",
                }),
                3,
            ],
        ]);
    });

    it("validates closed module and phase action inputs", () => {
        const authority = {
            workspaceId: "creation_workspace",
            expectedRootGeneration: 4,
            expectedSourceRevision: hash,
            expectedWorkflowStatusHash: hash,
        };
        expect(
            validateCreationModuleStageArgument({
                ...authority,
                operation: "delete",
                path: "source/logic/core.json",
                format: "world-forge.logic_module",
                expectedBaseFileSha256: hash,
            }),
        ).toMatchObject({ operation: "delete", expectedBaseFileSha256: hash });
        expect(
            validateCreationPhaseReadArgument({
                ...authority,
                phaseId: "p00_brief",
            }),
        ).toMatchObject({ phaseId: "p00_brief" });
        expect(
            validateCreationPhaseReportArgument({
                ...authority,
                report: {},
                artifactRegistry: [],
            }),
        ).toMatchObject({ report: {}, artifactRegistry: [] });
        expect(
            validateCreationPhaseReopenArgument({
                ...authority,
                phaseId: "p00_brief",
                reason: "Requirements changed",
                approvedBy: "reviewer_01",
            }),
        ).toMatchObject({ phaseId: "p00_brief", approvedBy: "reviewer_01" });
        expect(() =>
            validateCreationModuleStageArgument({
                ...authority,
                operation: "delete",
                path: "source/logic/core.json",
                format: "world-forge.logic_module",
                expectedBaseFileSha256: hash,
                proposedModule: {},
            }),
        ).toThrow();
        expect(() =>
            validateCreationPhaseReadArgument({
                ...authority,
                phaseId: "p00_brief",
                path: "/private/report.json",
            }),
        ).toThrow();
    });

    it("keeps canceled selection phantom-free and restores no private service state", async () => {
        const harness = createIpcHarness();
        harness.showOpenDialog.mockResolvedValueOnce({
            canceled: true,
            filePaths: [],
        });

        expect(
            await harness.invokeNoArgs(IPC_CHANNELS.registerCreationProject),
        ).toEqual({
            ok: false,
            error: {
                code: "cancelled",
                message: "Creation project selection was cancelled",
            },
        });
        expect(harness.request).not.toHaveBeenCalled();
    });

    it("maps changeset get, diff, approve, apply, and recover with every CAS value", async () => {
        const harness = createIpcHarness();
        await harness.invoke(IPC_CHANNELS.getCreationChangeset, {
            changesetId: "creation_changeset",
        });
        await harness.invoke(IPC_CHANNELS.diffCreationChangeset, {
            changesetId: "creation_changeset",
        });
        await harness.invoke(IPC_CHANNELS.approveCreationChangeset, {
            changesetId: "creation_changeset",
            expectedRecordHash: recordHash,
            expectedReviewSha256: reviewHash,
        });
        await harness.invoke(IPC_CHANNELS.applyCreationChangeset, {
            changesetId: "creation_changeset",
            expectedRecordHash: recordHash,
            expectedReviewSha256: reviewHash,
            expectedRootGeneration: 4,
        });
        await harness.invoke(IPC_CHANNELS.recoverCreationChangeset, {
            changesetId: "creation_changeset",
            mode: "rollback",
            expectedRecordHash: recordHash,
            expectedReviewSha256: reviewHash,
            expectedRootGeneration: 4,
        });

        expect(
            harness.request.mock.calls.map((call) => [
                call[1],
                call[2],
                call[4],
            ]),
        ).toEqual([
            [
                "creation_changeset.get",
                { changeset_id: "creation_changeset" },
                3,
            ],
            [
                "creation_changeset.diff",
                { changeset_id: "creation_changeset" },
                3,
            ],
            [
                "creation_changeset.approve",
                {
                    changeset_id: "creation_changeset",
                    expected_record_hash: recordHash,
                    expected_review_sha256: reviewHash,
                },
                3,
            ],
            [
                "creation_changeset.apply",
                {
                    changeset_id: "creation_changeset",
                    expected_record_hash: recordHash,
                    expected_review_sha256: reviewHash,
                    expected_root_generation: 4,
                },
                3,
            ],
            [
                "creation_changeset.recover",
                {
                    changeset_id: "creation_changeset",
                    mode: "rollback",
                    expected_record_hash: recordHash,
                    expected_review_sha256: reviewHash,
                    expected_root_generation: 4,
                },
                3,
            ],
        ]);
    });
});

describe("Studio read-only creation evidence IPC contracts", () => {
    const sourceHash = "a".repeat(64);
    const workflowHash = "b".repeat(64);
    const snapshotHash = "c".repeat(64);
    const authority = {
        workspaceId: "creation_workspace",
        expectedRootGeneration: 4,
        expectedSourceRevision: sourceHash,
        expectedWorkflowStatusHash: workflowHash,
        expectedArtifactSnapshotHash: snapshotHash,
    };

    it("accepts only closed pathless bounded evidence authority", () => {
        expect(
            validateCreationArtifactListArgument({
                ...authority,
                lifecycle: "active",
                cursor: null,
                limit: 32,
            }),
        ).toEqual({
            ...authority,
            lifecycle: "active",
            cursor: null,
            limit: 32,
        });
        expect(
            validateCreationArtifactInspectArgument({
                ...authority,
                artifactId: "artifact_01",
            }),
        ).toEqual({ ...authority, artifactId: "artifact_01" });
        expect(
            validateCreationEvidenceInspectArgument({
                ...authority,
                expectedArtifactSnapshotHash: null,
            }),
        ).toEqual({ ...authority, expectedArtifactSnapshotHash: null });

        for (const value of [
            { ...authority, lifecycle: "active", cursor: null, limit: 0 },
            { ...authority, lifecycle: "active", cursor: null, limit: 65 },
            { ...authority, lifecycle: "deleted", cursor: null, limit: 32 },
            { ...authority, lifecycle: null, cursor: "../artifact", limit: 32 },
            {
                ...authority,
                lifecycle: null,
                cursor: null,
                limit: 32,
                path: "/private",
            },
        ]) {
            expect(() => validateCreationArtifactListArgument(value)).toThrow();
        }
        expect(() =>
            validateCreationArtifactInspectArgument({
                ...authority,
                expectedArtifactSnapshotHash: null,
                artifactId: "artifact_01",
            }),
        ).toThrow();
        expect(() =>
            validateCreationEvidenceInspectArgument({
                ...authority,
                command: "materialize",
            }),
        ).toThrow();
    });

    it("maps fixed evidence operations to v4 with every CAS value", async () => {
        const harness = createIpcHarness();

        await harness.invoke(IPC_CHANNELS.listCreationArtifacts, {
            ...authority,
            lifecycle: "active",
            cursor: null,
            limit: 32,
        });
        await harness.invoke(IPC_CHANNELS.inspectCreationArtifact, {
            ...authority,
            artifactId: "artifact_01",
        });
        await harness.invoke(IPC_CHANNELS.inspectCreationEvidence, authority);

        expect(
            harness.request.mock.calls.map((call) => [
                call[1],
                call[2],
                call[4],
            ]),
        ).toEqual([
            [
                "creation_artifact.list",
                {
                    workspace_id: "creation_workspace",
                    expected_root_generation: 4,
                    expected_source_revision: sourceHash,
                    expected_workflow_status_hash: workflowHash,
                    expected_artifact_snapshot_hash: snapshotHash,
                    lifecycle: "active",
                    cursor: null,
                    limit: 32,
                },
                4,
            ],
            [
                "creation_artifact.inspect",
                {
                    workspace_id: "creation_workspace",
                    expected_root_generation: 4,
                    expected_source_revision: sourceHash,
                    expected_workflow_status_hash: workflowHash,
                    expected_artifact_snapshot_hash: snapshotHash,
                    artifact_id: "artifact_01",
                },
                4,
            ],
            [
                "creation_evidence.inspect",
                {
                    workspace_id: "creation_workspace",
                    expected_root_generation: 4,
                    expected_source_revision: sourceHash,
                    expected_workflow_status_hash: workflowHash,
                    expected_artifact_snapshot_hash: snapshotHash,
                },
                4,
            ],
        ]);
        expect(JSON.stringify(harness.request.mock.calls)).not.toContain(
            "path",
        );
    });

    it("removes every evidence handler during teardown", () => {
        const harness = createIpcHarness();
        harness.dispose();
        expect(harness.removeHandler).toHaveBeenCalledWith(
            IPC_CHANNELS.listCreationArtifacts,
        );
        expect(harness.removeHandler).toHaveBeenCalledWith(
            IPC_CHANNELS.inspectCreationArtifact,
        );
        expect(harness.removeHandler).toHaveBeenCalledWith(
            IPC_CHANNELS.inspectCreationEvidence,
        );
    });
});

describe("Studio main-owned authority IPC contracts", () => {
    it("accepts only stable renderer selections for v10-v12 authority initiation", () => {
        expect(
            validateCreationAuthorityReviewArgument({
                workspaceId: "creation_workspace",
                qaReportArtifactId: "artifact_qa_01",
                outputRole: "texture",
            }),
        ).toEqual({
            workspaceId: "creation_workspace",
            qaReportArtifactId: "artifact_qa_01",
            outputRole: "texture",
        });
        expect(
            validateCreationAuthorityReleaseArgument({
                workspaceId: "creation_workspace",
                reviewReceiptArtifactIds: ["artifact_review_01"],
                targetGrantId: "grant_assetpack_01",
            }),
        ).toEqual({
            workspaceId: "creation_workspace",
            reviewReceiptArtifactIds: ["artifact_review_01"],
            targetGrantId: "grant_assetpack_01",
        });
        expect(
            validateCreationAuthorityHeadlessArgument({
                workspaceId: "creation_workspace",
                runtimeBundleArtifactId: "artifact_runtime_bundle_01",
                sourceGrantId: "grant_runtime_bundle_01",
                headlessScriptArtifactId: "artifact_script_01",
                targetGrantId: "grant_headless_01",
                platformId: "platform:linux_x86_64",
            }),
        ).toEqual({
            workspaceId: "creation_workspace",
            runtimeBundleArtifactId: "artifact_runtime_bundle_01",
            sourceGrantId: "grant_runtime_bundle_01",
            headlessScriptArtifactId: "artifact_script_01",
            targetGrantId: "grant_headless_01",
            platformId: "platform:linux_x86_64",
        });
        expect(
            validateCreationAuthorityJobActionArgument({
                workspaceId: "creation_workspace",
                jobId: "job_review_01",
            }),
        ).toEqual({
            workspaceId: "creation_workspace",
            jobId: "job_review_01",
        });

        for (const forbidden of [
            { decisions: ["approved"] },
            { blockers: ["renderer_blocker"] },
            { expectedArtifactSnapshotHash: "a".repeat(64) },
            { expectedTargetGrantGeneration: 0 },
            { path: "/tmp/out" },
            { command: ["python"] },
            { scriptBytes: "e30=" },
            { releaseAuthorityId: "release_01" },
        ]) {
            expect(() =>
                validateCreationAuthorityReviewArgument({
                    workspaceId: "creation_workspace",
                    qaReportArtifactId: "artifact_qa_01",
                    outputRole: "texture",
                    ...forbidden,
                }),
            ).toThrow();
        }
    });

    it("owns v6 headless evidence output selection in main", async () => {
        const harness = createIpcHarness();

        await harness.invoke(IPC_CHANNELS.selectCreationHeadlessEvidenceOutput, {
            workspaceId: "creation_workspace",
        });

        expect(harness.showSaveDialog).toHaveBeenCalledTimes(1);
        expect(harness.request.mock.calls.map((call) => [call[1], call[2], call[4]])).toEqual([
            [
                "creation_output_grant.create",
                {
                    workspace_id: "creation_workspace",
                    kind: "headless_evidence_directory",
                    display_name: "target-1",
                    path: "/selected/target-1",
                },
                5,
            ],
        ]);
    });

    it("derives v10 review create body from inspected service state and modal decisions", async () => {
        const payload = Buffer.from("safe preview");
        const sha256 = createHash("sha256").update(payload).digest("hex");
        const authority = serviceAuthority();
        const authorityModal = {
            requestReview: vi
                .fn<
                    (
                        window: unknown,
                        modalPayload: {
                            nonce: string;
                            title: string;
                            preview: {
                                data: Uint8Array;
                                mediaType: "image/png" | "audio/wav" | "text/plain";
                                sha256: string;
                                byteLength: number;
                            };
                            criteria: readonly string[];
                        },
                    ) => Promise<{
                        nonce: string;
                        action: "approve";
                        criterionDecisions: ["approved"];
                    }>
                >()
                .mockImplementation((_window, modalPayload) =>
                    Promise.resolve({
                        nonce: modalPayload.nonce,
                        action: "approve",
                        criterionDecisions: ["approved"],
                    }),
                ),
        };
        const harness = createIpcHarness({ authorityModal });
        harness.request.mockImplementation(
            (requestId, method, params): Promise<unknown> => {
                if (method === "creation_artifact.inspect") {
                    return Promise.resolve(v5Response(requestId, method, {
                        authority,
                        artifact_snapshot_hash: authorityArtifactHash,
                        artifact: artifactRecord(
                            "artifact_qa_01",
                            "world-forge.asset_qa_report",
                            "qa_report_01",
                        ),
                        projection: {
                            projection_kind: "asset_qa_report",
                            title: "qa_report_01",
                            status: "passed",
                            facts: [
                                { key: "criterion_hashes", value: ["d".repeat(64)] },
                            ],
                            lineage: [],
                        },
                    }));
                }
                if (method === "creation_preview.open") {
                    return Promise.resolve(v5Response(requestId, method, {
                        preview: {
                            handle: "H".repeat(43),
                            byte_length: payload.length,
                            sha256,
                            media_type: "text/plain",
                        },
                    }));
                }
                if (method === "creation_preview.read") {
                    return Promise.resolve(v5Response(requestId, method, {
                        handle: "H".repeat(43),
                        sequence: Number(params.sequence),
                        data_base64: payload.toString("base64"),
                        cumulative_bytes: payload.length,
                        cumulative_sha256: sha256,
                        eof: true,
                    }));
                }
                if (method === "creation_preview.close") {
                    return Promise.resolve(v5Response(requestId, method, {
                        handle: "H".repeat(43),
                        closed: true,
                    }));
                }
                if (method === "creation_job.create") {
                    return Promise.resolve(v5Response(requestId, method, {
                        job: { job_id: "job_review_01" },
                    }));
                }
                throw new Error(`unexpected ${method}`);
            },
        );

        await harness.invoke(IPC_CHANNELS.reviewCreationAssetQa, {
            workspaceId: "creation_workspace",
            qaReportArtifactId: "artifact_qa_01",
            outputRole: "texture",
        });

        const createCall = harness.request.mock.calls.find(
            (call) => call[1] === "creation_job.create",
        );
        expect(createCall?.[4]).toBe(5);
        expect(createCall?.[2]).toMatchObject({
            workspace_id: "creation_workspace",
            operation: "asset.qa.review",
            expected_root_generation: 4,
            expected_source_revision: "a".repeat(64),
            expected_workflow_status_hash: "b".repeat(64),
            expected_artifact_snapshot_hash: authorityArtifactHash,
            qa_report_artifact_id: "artifact_qa_01",
            output_role: "texture",
            decisions: ["approved"],
            blockers: [],
        });
        expect(JSON.stringify(createCall?.[2])).not.toContain("renderer_blocker");
        expect(authorityModal.requestReview.mock.calls[0][1].preview).toMatchObject({
            mediaType: "text/plain",
            sha256,
            byteLength: payload.length,
        });
        expect(authorityModal.requestReview.mock.calls[0][1].preview.data).toEqual(
            new Uint8Array(payload),
        );
        expect(harness.request.mock.calls.map((call) => call[1])).toContain(
            "creation_preview.close",
        );
    });

    it("binds exact verified v10 preview bytes and rejects preview close failure before authority success", async () => {
        const payload = Buffer.from("verified image bytes");
        const sha256 = createHash("sha256").update(payload).digest("hex");
        const authority = serviceAuthority();
        let modalBytes: Uint8Array | null = null;
        const authorityModal = {
            requestReview: vi.fn(
                (_: unknown, modalPayload: AuthorityModalTestPayload) => {
                modalBytes = new Uint8Array(modalPayload.preview.data);
                modalPayload.preview.data[0] ^= 0xff;
                return Promise.resolve({
                    nonce: modalPayload.nonce,
                    action: "approve" as const,
                    criterionDecisions: ["approved" as const],
                });
            }),
        };
        const harness = createIpcHarness({ authorityModal });
        harness.request.mockImplementation((requestId, method, params) => {
            if (method === "creation_artifact.inspect") {
                return Promise.resolve(v5Response(requestId, method, {
                    authority,
                    artifact_snapshot_hash: authorityArtifactHash,
                    artifact: artifactRecord(
                        "artifact_qa_01",
                        "world-forge.asset_qa_report",
                        "qa_report_01",
                    ),
                    projection: exactProjection("asset_qa_report", "passed", [
                        { key: "criterion_hashes", value: ["d".repeat(64)] },
                    ]),
                }));
            }
            if (method === "creation_preview.open") {
                return Promise.resolve(v5Response(requestId, method, {
                    preview: {
                        handle: "H".repeat(43),
                        byte_length: payload.length,
                        sha256,
                        media_type: "image/png",
                    },
                }));
            }
            if (method === "creation_preview.read") {
                return Promise.resolve(v5Response(requestId, method, {
                    handle: "H".repeat(43),
                    sequence: Number(params.sequence),
                    data_base64: payload.toString("base64"),
                    cumulative_bytes: payload.length,
                    cumulative_sha256: sha256,
                    eof: true,
                }));
            }
            if (method === "creation_preview.close") {
                return Promise.resolve({
                    protocol: "rpg-world-forge.studio_protocol",
                    protocol_version: 5,
                    kind: "error",
                    request_id: requestId,
                    error: { code: "invalid_request", message: "close mismatch", details: {} },
                    method,
                });
            }
            if (method === "creation_job.create") {
                return Promise.resolve(v5Response(requestId, method, {
                    job: { job_id: "job_review_01" },
                }));
            }
            throw new Error(`unexpected ${method}`);
        });

        const result = await harness.invoke(IPC_CHANNELS.reviewCreationAssetQa, {
            workspaceId: "creation_workspace",
            qaReportArtifactId: "artifact_qa_01",
            outputRole: "texture",
        });

        expect(result).toEqual(expect.objectContaining({ ok: false }));
        expect(JSON.stringify(result)).toContain("preview close");
        expect(modalBytes).toBeNull();
        expect(authorityModal.requestReview).not.toHaveBeenCalled();
        expect(
            harness.request.mock.calls.some((call) => call[1] === "creation_job.create"),
        ).toBe(false);
        expect(
            harness.request.mock.calls.filter((call) => call[1] === "creation_preview.close"),
        ).toHaveLength(1);
    });

    it("rejects generic v10 inspected subjects and missing authoritative criteria", async () => {
        const harness = createIpcHarness();
        harness.request.mockResolvedValue(v5Response("request_01", "creation_artifact.inspect", {
            authority: serviceAuthority(),
            artifact_snapshot_hash: authorityArtifactHash,
            artifact: artifactRecord("artifact_qa_01", "world-forge.generic_asset", "qa_report_01"),
            projection: exactProjection("asset_qa_report", "passed", []),
        }));

        const result = await harness.invoke(IPC_CHANNELS.reviewCreationAssetQa, {
            workspaceId: "creation_workspace",
            qaReportArtifactId: "artifact_qa_01",
            outputRole: "texture",
        });

        expect(result).toEqual(expect.objectContaining({ ok: false }));
        expect(JSON.stringify(result)).toMatch(/asset_qa_report|criteria/u);
        expect(harness.request.mock.calls.map((call) => call[1])).not.toContain(
            "creation_preview.open",
        );
    });

    it("rejects unknown v11 review status before release authority creation", async () => {
        const harness = createIpcHarness();
        harness.request.mockImplementation((requestId, method) => {
            if (method === "creation_artifact.inspect") {
                return Promise.resolve(v5Response(requestId, method, {
                    authority: serviceAuthority(),
                    artifact_snapshot_hash: authorityArtifactHash,
                    artifact: artifactRecord(
                        "artifact_review_01",
                        "world-forge.asset_qa_review_receipt",
                        "review_receipt_01",
                    ),
                    projection: exactProjection("asset_qa_review_receipt", "maybe"),
                }));
            }
            throw new Error(`unexpected ${method}`);
        });

        const result = await harness.invoke(IPC_CHANNELS.authorizeCreationAssetRelease, {
            workspaceId: "creation_workspace",
            reviewReceiptArtifactIds: ["artifact_review_01"],
            targetGrantId: "grant_assetpack_01",
        });

        expect(result).toEqual(expect.objectContaining({ ok: false }));
        expect(JSON.stringify(result)).toContain("review status");
        expect(harness.request.mock.calls.map((call) => call[1])).not.toContain(
            "creation_job.create",
        );
    });

    it("derives exact v11 release body from approved and rejected review receipts", async () => {
        const harness = createIpcHarness();
        harness.request.mockImplementation((requestId, method, params) => {
            if (method === "creation_artifact.inspect") {
                const artifactId = String(params.artifact_id);
                const rejected = artifactId === "artifact_review_b";
                return Promise.resolve(v5Response(requestId, method, {
                    authority: serviceAuthority(),
                    artifact_snapshot_hash: authorityArtifactHash,
                    artifact: artifactRecord(
                        artifactId,
                        "world-forge.asset_qa_review_receipt",
                        rejected ? "review_receipt_b" : "review_receipt_a",
                    ),
                    projection: exactProjection(
                        "asset_qa_review_receipt",
                        rejected ? "rejected" : "approved",
                    ),
                }));
            }
            if (method === "creation_output_grant.get") {
                return Promise.resolve(v5Response(requestId, method, {
                    grant: {
                        grant_id: "grant_assetpack_01",
                        generation: 9,
                        kind: "generic_assetpack_directory",
                        state: "ready",
                        publication: null,
                    },
                }));
            }
            if (method === "creation_job.create") {
                return Promise.resolve(v5Response(requestId, method, {
                    job: { job_id: "job_release_01" },
                }));
            }
            throw new Error(`unexpected ${method}`);
        });

        await harness.invoke(IPC_CHANNELS.authorizeCreationAssetRelease, {
            workspaceId: "creation_workspace",
            reviewReceiptArtifactIds: ["artifact_review_a", "artifact_review_b"],
            targetGrantId: "grant_assetpack_01",
        });

        const createCall = harness.request.mock.calls.find(
            (call) => call[1] === "creation_job.create",
        );
        expect(createCall?.[2]).toMatchObject({
            operation: "asset.release.authorize",
            review_receipt_artifact_ids: ["artifact_review_a", "artifact_review_b"],
            blockers: ["review_rejected"],
            target_grant_id: "grant_assetpack_01",
            expected_target_grant_generation: 9,
        });
    });

    it("requires v12 headless source grant to be an exact published runtime bundle grant", async () => {
        const harness = createIpcHarness();
        harness.request.mockImplementation((requestId, method, params) => {
            if (method === "creation_artifact.inspect") {
                const artifactId = String(params.artifact_id);
                if (artifactId === "artifact_runtime_bundle") {
                    return Promise.resolve(v5Response(requestId, method, {
                        authority: serviceAuthority(),
                        artifact_snapshot_hash: authorityArtifactHash,
                        artifact: artifactRecord(artifactId, "world-forge.game_runtime_bundle", "runtime_bundle_01"),
                        projection: exactProjection("game_runtime_bundle", "succeeded", [], [
                            { artifact_id: "artifact_script_01" },
                            { artifact_id: "artifact_gamepack" },
                            { artifact_id: "artifact_inventory" },
                            { artifact_id: "artifact_assetpack" },
                            { artifact_id: "artifact_release_authority" },
                            { artifact_id: "artifact_snapshot" },
                            { artifact_id: "artifact_registry" },
                            { artifact_id: "artifact_composition" },
                        ]),
                    }));
                }
                const formats: Record<string, [string, string]> = {
                    artifact_script_01: ["world-forge.game_execution_script", "script_01"],
                    artifact_gamepack: ["world-forge.gamepack", "gamepack_01"],
                    artifact_inventory: ["world-forge.asset_inventory", "inventory_01"],
                    artifact_assetpack: ["world-forge.assetpack", "assetpack_01"],
                    artifact_release_authority: ["world-forge.asset_release_authority", "release_authority_01"],
                    artifact_snapshot: ["world-forge.game_runtime_snapshot", "snapshot_01"],
                    artifact_registry: ["world-forge.runtime_adapter_registry", "registry_01"],
                    artifact_composition: ["world-forge.game_runtime_composition", "composition_01"],
                };
                const [format, id] = formats[artifactId] ?? ["world-forge.gamepack", "unknown_01"];
                return Promise.resolve(v5Response(requestId, method, {
                    authority: serviceAuthority(),
                    artifact_snapshot_hash: authorityArtifactHash,
                    artifact: artifactRecord(artifactId, format, id),
                    projection: exactProjection(format.replace("world-forge.", ""), "succeeded"),
                }));
            }
            if (method === "creation_output_grant.get") {
                const grantId = String(params.grant_id);
                return Promise.resolve(v5Response(requestId, method, {
                    grant: {
                        grant_id: grantId,
                        generation: 1,
                        kind: grantId === "grant_runtime_source" ? "generic_assetpack_directory" : "headless_evidence_directory",
                        state: "published",
                        publication: null,
                    },
                }));
            }
            throw new Error(`unexpected ${method}`);
        });

        const result = await harness.invoke(IPC_CHANNELS.verifyCreationHeadless, {
            workspaceId: "creation_workspace",
            runtimeBundleArtifactId: "artifact_runtime_bundle",
            sourceGrantId: "grant_runtime_source",
            headlessScriptArtifactId: "artifact_script_01",
            targetGrantId: "grant_headless_target",
            platformId: "platform:linux_x86_64",
        });

        expect(result).toEqual(expect.objectContaining({ ok: false }));
        expect(JSON.stringify(result)).toMatch(/runtime bundle grant|publication/u);
        expect(harness.request.mock.calls.map((call) => call[1])).not.toContain("creation_job.create");
    });

    it("rejects swapped v12 headless scripts outside the selected runtime lineage", async () => {
        const harness = createIpcHarness();
        harness.request.mockImplementation((requestId, method, params) => {
            if (method === "creation_artifact.inspect") {
                const artifactId = String(params.artifact_id);
                if (artifactId === "artifact_runtime_bundle") {
                    return Promise.resolve(v5Response(requestId, method, {
                        authority: serviceAuthority(),
                        artifact_snapshot_hash: authorityArtifactHash,
                        artifact: artifactRecord(
                            artifactId,
                            "world-forge.game_runtime_bundle",
                            "runtime_bundle_01",
                        ),
                        projection: exactProjection("game_runtime_bundle", "succeeded", [], [
                            { artifact_id: "artifact_script_expected" },
                        ]),
                    }));
                }
                return Promise.resolve(v5Response(requestId, method, {
                    authority: serviceAuthority(),
                    artifact_snapshot_hash: authorityArtifactHash,
                    artifact: artifactRecord(
                        artifactId,
                        "world-forge.game_execution_script",
                        "script_01",
                    ),
                    projection: exactProjection("game_execution_script", "succeeded"),
                }));
            }
            if (method === "creation_output_grant.get") {
                const grantId = String(params.grant_id);
                return Promise.resolve(v5Response(requestId, method, {
                    grant:
                        grantId === "grant_runtime_source"
                            ? {
                                  ...runtimeSourceGrantFixture({}),
                                  publication: {
                                      ...runtimeSourceGrantFixture({}).publication,
                                      runtime_bundle: {
                                          format: "world-forge.game_runtime_bundle",
                                          format_version: 1,
                                          id: "runtime_bundle_01",
                                          content_hash: "f".repeat(64),
                                      },
                                  },
                              }
                            : headlessTargetGrantFixture(grantId, {}),
                }));
            }
            throw new Error(`unexpected ${method}`);
        });

        const result = await harness.invoke(IPC_CHANNELS.verifyCreationHeadless, {
            workspaceId: "creation_workspace",
            runtimeBundleArtifactId: "artifact_runtime_bundle",
            sourceGrantId: "grant_runtime_source",
            headlessScriptArtifactId: "artifact_script_swapped",
            targetGrantId: "grant_headless_target",
            platformId: "platform:linux_x86_64",
        });

        expect(result).toEqual(expect.objectContaining({ ok: false }));
        expect(JSON.stringify(result)).toContain("selected runtime lineage");
        expect(harness.request.mock.calls.map((call) => call[1])).not.toContain(
            "creation_job.create",
        );
    });

    it("derives an exact v12 headless request only after retained v11 release and bound grant proof", async () => {
        const harness = createIpcHarness();
        queueAuthorityHeadlessProof(harness.request);

        await harness.invoke(IPC_CHANNELS.verifyCreationHeadless, {
            workspaceId: "creation_workspace",
            runtimeBundleArtifactId: "artifact_runtime_bundle",
            sourceGrantId: "grant_runtime_source",
            headlessScriptArtifactId: "artifact_script_01",
            targetGrantId: "grant_headless_target",
            platformId: "platform:linux_x86_64",
        });

        const createCall = harness.request.mock.calls.find(
            (call) => call[1] === "creation_job.create",
        );
        expect(createCall?.[2]).toEqual({
            workspace_id: "creation_workspace",
            operation: "runtime.headless.verify",
            expected_root_generation: 4,
            expected_source_revision: "a".repeat(64),
            expected_workflow_status_hash: "b".repeat(64),
            expected_artifact_snapshot_hash: authorityArtifactHash,
            gamepack_artifact_id: "artifact_gamepack",
            asset_inventory_artifact_id: "artifact_inventory",
            assetpack_artifact_id: "artifact_assetpack",
            asset_release_authority_artifact_id: "artifact_release_authority",
            runtime_snapshot_artifact_id: "artifact_snapshot",
            runtime_adapter_registry_artifact_id: "artifact_registry",
            runtime_composition_artifact_id: "artifact_composition",
            runtime_bundle_artifact_id: "artifact_runtime_bundle",
            source_grant_id: "grant_runtime_source",
            expected_source_grant_generation: 7,
            platform_id: "platform:linux_x86_64",
            headless_script_artifact_id: "artifact_script_01",
            target_grant_id: "grant_headless_target",
            expected_target_grant_generation: 3,
        });
        expect(harness.request.mock.calls.map((call) => call[1])).toContain(
            "creation_job.get",
        );
    });

    it.each([
        ["missing v11", { omitReleaseJob: true }, /release authority/u],
        ["blocked v11", { releaseStatus: "blocked" }, /authorized/u],
        ["wrong operation", { releaseOperation: "asset.qa.review" }, /asset\.release\.authorize/u],
        ["wrong version", { releaseFormatVersion: 10 }, /v11|version/u],
        ["failed terminal state", { releaseState: "failed" }, /asset\.release\.authorize proof/u],
        ["result blockers", { releaseReasonCodes: ["blocked"] }, /asset\.release\.authorize proof/u],
        ["crossed assetpack", { assetpackSubjectId: "assetpack_crossed" }, /asset\.release\.authorize proof/u],
        ["crossed release authority", { releaseAuthoritySubjectId: "release_crossed" }, /asset\.release\.authorize proof/u],
        ["crossed runtime bundle grant", { sourceGrantBundleId: "runtime_crossed" }, /runtime bundle grant/u],
        ["duplicate lineage", { duplicateLineageFormat: "world-forge.assetpack" }, /ambiguous|duplicated/u],
        ["source grant for another bundle", { sourceGrantArtifactId: "artifact_other_runtime" }, /runtime bundle grant/u],
        ["wrong source grant kind", { sourceGrantKind: "generic_assetpack_directory" }, /runtime bundle grant/u],
        ["wrong source grant state", { sourceGrantState: "ready" }, /runtime bundle grant/u],
        ["wrong source grant version", { sourceGrantVersion: 6 }, /v2|version/u],
        ["wrong target grant kind", { targetGrantKind: "game_runtime_bundle_directory" }, /headless evidence/u],
        ["wrong target grant version", { targetGrantVersion: 5 }, /v6|version/u],
        ["source equals target", { targetGrantId: "grant_runtime_source" }, /distinct/u],
        ["stale script", { scriptSnapshotHash: "d".repeat(64) }, /snapshot|authority/u],
        ["workspace drift", { scriptWorkspaceId: "other_workspace" }, /crosses authority|workspace/u],
    ])(
        "rejects v12 authority proof drift: %s",
        async (_name, drift, expectedMessage) => {
            const harness = createIpcHarness();
            const typedDrift = drift as AuthorityHeadlessProofDrift;
            queueAuthorityHeadlessProof(harness.request, typedDrift);

            const result = await harness.invoke(IPC_CHANNELS.verifyCreationHeadless, {
                workspaceId: "creation_workspace",
                runtimeBundleArtifactId: "artifact_runtime_bundle",
                sourceGrantId: "grant_runtime_source",
                headlessScriptArtifactId: "artifact_script_01",
                targetGrantId: typedDrift.targetGrantId ?? "grant_headless_target",
                platformId: "platform:linux_x86_64",
            });

            expect(result).toEqual(expect.objectContaining({ ok: false }));
            expect(JSON.stringify(result)).toMatch(expectedMessage);
            expect(harness.request.mock.calls.map((call) => call[1])).not.toContain(
                "creation_job.create",
            );
        },
    );

    it("denies cancel and recover for non-authority creation jobs after re-read", async () => {
        const harness = createIpcHarness();
        harness.request.mockResolvedValue(
            v5Response("request_01", "creation_job.get", {
                job: {
                    workspace_id: "creation_workspace",
                    operation: "creation.compile",
                    state: "running",
                    generation: 1,
                    record_hash: "e".repeat(64),
                },
            }),
        );

        const cancelled = await harness.invoke(IPC_CHANNELS.requestCreationJobCancel, {
            workspaceId: "creation_workspace",
            jobId: "job_compile_01",
        });
        const recovered = await harness.invoke(IPC_CHANNELS.requestCreationJobRecovery, {
            workspaceId: "creation_workspace",
            jobId: "job_compile_01",
        });

        expect(JSON.stringify(cancelled)).toContain("limited to authority jobs");
        expect(JSON.stringify(recovered)).toContain("limited to authority jobs");
        expect(
            harness.request.mock.calls.some(
                (call) => call[1] === "creation_job.cancel",
            ),
        ).toBe(false);
        expect(
            harness.request.mock.calls.some(
                (call) => call[1] === "creation_job.recover",
            ),
        ).toBe(false);
    });
});

describe("Studio pathless creation preview IPC contracts", () => {
    const authority = {
        workspaceId: "creation_workspace",
        expectedRootGeneration: 4,
        expectedSourceRevision: "a".repeat(64),
        expectedWorkflowStatusHash: "b".repeat(64),
        expectedArtifactSnapshotHash: "c".repeat(64),
        assetpackArtifactId: "artifact_assetpack",
        outputGrantId: "grant_assetpack",
        expectedOutputGrantGeneration: 2,
        assetId: "board_ui",
    };

    it("maps exact fixed calls to the v4 base64 transport", async () => {
        const harness = createIpcHarness();
        const handle = "D".repeat(43);

        await harness.invoke(IPC_CHANNELS.openCreationPreview, authority);
        await harness.invoke(IPC_CHANNELS.readCreationPreviewChunk, {
            handle,
            sequence: 0,
        });
        await harness.invoke(IPC_CHANNELS.closeCreationPreview, { handle });

        expect(
            harness.request.mock.calls.map((call) => [
                call[1],
                call[2],
                call[4],
            ]),
        ).toEqual([
            [
                "creation_preview.open",
                {
                    workspace_id: "creation_workspace",
                    expected_root_generation: 4,
                    expected_source_revision: "a".repeat(64),
                    expected_workflow_status_hash: "b".repeat(64),
                    expected_artifact_snapshot_hash: "c".repeat(64),
                    assetpack_artifact_id: "artifact_assetpack",
                    output_grant_id: "grant_assetpack",
                    expected_output_grant_generation: 2,
                    asset_id: "board_ui",
                },
                4,
            ],
            ["creation_preview.read", { handle, sequence: 0 }, 4],
            ["creation_preview.close", { handle }, 4],
        ]);
        expect(JSON.stringify(harness.request.mock.calls)).not.toContain(
            "path",
        );
    });

    it("removes all creation preview handlers during teardown", () => {
        const harness = createIpcHarness();
        harness.dispose();
        expect(harness.removeHandler).toHaveBeenCalledWith(
            IPC_CHANNELS.openCreationPreview,
        );
        expect(harness.removeHandler).toHaveBeenCalledWith(
            IPC_CHANNELS.readCreationPreviewChunk,
        );
        expect(harness.removeHandler).toHaveBeenCalledWith(
            IPC_CHANNELS.closeCreationPreview,
        );
    });

    it("rejects renderer-controlled paths, offsets, and ranges", () => {
        const handle = "E".repeat(43);
        expect(validateCreationPreviewOpenArgument(authority)).toEqual(
            authority,
        );
        expect(
            validateCreationPreviewReadArgument({ handle, sequence: 0 }),
        ).toEqual({
            handle,
            sequence: 0,
        });
        expect(validateCreationPreviewCloseArgument({ handle })).toEqual({
            handle,
        });
        expect(() =>
            validateCreationPreviewOpenArgument({
                ...authority,
                path: "/tmp/a",
            }),
        ).toThrow();
        expect(() =>
            validateCreationPreviewReadArgument({
                handle,
                sequence: 0,
                offset: 1,
            }),
        ).toThrow();
        expect(() =>
            validateCreationPreviewCloseArgument({ handle, force: true }),
        ).toThrow();
    });
});

describe("Studio durable creation job IPC contracts", () => {
    const authority = {
        workspaceId: "creation_workspace",
        expectedRootGeneration: 4,
        expectedSourceRevision: "a".repeat(64),
        expectedWorkflowStatusHash: "b".repeat(64),
        expectedArtifactSnapshotHash: "c".repeat(64),
    };
    const document = {
        format: "world-forge.game_analysis",
        format_version: 1,
        analysis_id: "analysis_01",
        content_hash: "d".repeat(64),
    };
    const recordHash = "e".repeat(64);
    const assetProcess = {
        ...authority,
        jobId: "process_01",
        licenseArtifactIds: ["artifact_license_01"],
        recipeId: "board_ui_recipe",
        processingReceiptId: "board_ui_processing_receipt",
        qaReportId: "board_ui_qa",
        acceptanceResults: [
            {
                criterionIndex: 0,
                criterionSha256: "f".repeat(64),
                status: "passed" as const,
                evidenceHashes: ["1".repeat(64)],
            },
        ],
    };
    const assetSeal = {
        ...authority,
        jobId: "seal_01",
        qaReportArtifactIds: ["artifact_qa_01"],
        manifestId: "release_manifest_01",
        targetGrantId: "grant_output_01",
        expectedTargetGrantGeneration: 2,
    };
    const runtimeCompose = {
        ...authority,
        jobId: "compose_01",
        gamepackArtifactId: "artifact_gamepack_01",
        assetInventoryArtifactId: "artifact_inventory_01",
        assetpackArtifactId: "artifact_assetpack_01",
        targetGrantId: "grant_output_01",
        expectedTargetGrantGeneration: 2,
    };
    const runtimeBundleBuild = {
        ...authority,
        jobId: "bundle_01",
        gamepackArtifactId: "artifact_gamepack_01",
        assetInventoryArtifactId: "artifact_inventory_01",
        assetpackArtifactId: "artifact_assetpack_01",
        runtimeSnapshotArtifactId: "artifact_runtime_snapshot_01",
        runtimeAdapterRegistryArtifactId: "artifact_runtime_registry_01",
        runtimeCompositionArtifactId: "artifact_runtime_composition_01",
        runtimeSupportReportArtifactId: "artifact_runtime_support_01",
        sourceGrantId: "grant_assetpack_01",
        expectedSourceGrantGeneration: 3,
        targetGrantId: "grant_bundle_01",
        expectedTargetGrantGeneration: 1,
    };
    const materializationBundleBuild = {
        ...authority,
        jobId: "materialization_01",
        runtimeBundleArtifactId: "artifact_runtime_bundle_01",
        sourceGrantId: "grant_runtime_bundle_01",
        expectedSourceGrantGeneration: 2,
        targetGrantId: "grant_materialization_01",
        expectedTargetGrantGeneration: 1,
    };
    const gameMaterialize = {
        ...authority,
        jobId: "standalone_01",
        materializationBundleArtifactId: "artifact_materialization_bundle_01",
        sourceGrantId: "grant_materialization_01",
        expectedSourceGrantGeneration: 2,
        targetGrantId: "grant_standalone_01",
        expectedTargetGrantGeneration: 1,
    };
    const gamePackage = {
        ...authority,
        jobId: "package_01",
        standaloneGameArtifactId: "artifact_standalone_game_01",
        sourceGrantId: "grant_standalone_01",
        expectedSourceGrantGeneration: 2,
        targetGrantId: "grant_package_01",
        expectedTargetGrantGeneration: 1,
    };
    const gamePackageExtract = {
        ...authority,
        jobId: "extract_01",
        gamePackageArtifactId: "artifact_game_package_01",
        sourceGrantId: "grant_package_01",
        expectedSourceGrantGeneration: 2,
        targetGrantId: "grant_extracted_standalone_01",
        expectedTargetGrantGeneration: 1,
    };

    const boundedAcceptanceResults = (count: number, evidenceCount = 1) =>
        Array.from({ length: count }, (_, index) => ({
            criterionIndex: index,
            criterionSha256: (index + 1).toString(16).padStart(64, "0"),
            status: "passed" as const,
            evidenceHashes: Array.from({ length: evidenceCount }, (_unused, evidenceIndex) =>
                (evidenceIndex + 1).toString(16).padStart(64, "0"),
            ),
        }));

    it("accepts exactly 64 asset criteria and evidence hashes and rejects 65", () => {
        const maximumCriteria = boundedAcceptanceResults(64);
        expect(
            validateCreationAssetProcessArgument({
                ...assetProcess,
                acceptanceResults: maximumCriteria,
            }).acceptanceResults,
        ).toEqual(maximumCriteria);
        expect(() =>
            validateCreationAssetProcessArgument({
                ...assetProcess,
                acceptanceResults: boundedAcceptanceResults(65),
            }),
        ).toThrow(/acceptance results/u);

        const maximumEvidence = boundedAcceptanceResults(1, 64);
        expect(
            validateCreationAssetProcessArgument({
                ...assetProcess,
                acceptanceResults: maximumEvidence,
            }).acceptanceResults,
        ).toEqual(maximumEvidence);
        expect(() =>
            validateCreationAssetProcessArgument({
                ...assetProcess,
                acceptanceResults: boundedAcceptanceResults(1, 65),
            }),
        ).toThrow(/criterion evidence/u);
    });

    it("accepts only exact bounded compile, admission, job, and event inputs", () => {
        expect(
            validateCreationCompileArgument({
                ...authority,
                jobId: "compile_01",
            }),
        ).toEqual({
            ...authority,
            jobId: "compile_01",
        });
        expect(
            validateCreationArtifactAdmissionArgument({
                ...authority,
                document,
                dependencyArtifactIds: ["artifact_01", "artifact_02"],
            }),
        ).toEqual({
            ...authority,
            document,
            dependencyArtifactIds: ["artifact_01", "artifact_02"],
        });
        expect(validateCreationAssetProcessArgument(assetProcess)).toEqual(
            assetProcess,
        );
        expect(validateCreationAssetReleaseSealArgument(assetSeal)).toEqual(
            assetSeal,
        );
        expect(validateCreationRuntimeComposeArgument(runtimeCompose)).toEqual(
            runtimeCompose,
        );
        expect(
            validateCreationRuntimeBundleBuildArgument(runtimeBundleBuild),
        ).toEqual(runtimeBundleBuild);
        expect(
            validateCreationMaterializationBundleBuildArgument(
                materializationBundleBuild,
            ),
        ).toEqual(materializationBundleBuild);
        expect(
            validateCreationGameMaterializeArgument(gameMaterialize),
        ).toEqual(gameMaterialize);
        expect(validateCreationGamePackageArgument(gamePackage)).toEqual(
            gamePackage,
        );
        expect(
            validateCreationGamePackageExtractArgument(gamePackageExtract),
        ).toEqual(gamePackageExtract);
        expect(
            validateCreationOutputGrantSelectArgument({
                workspaceId: "creation_workspace",
            }),
        ).toEqual({ workspaceId: "creation_workspace" });
        expect(
            validateCreationOutputGrantGetArgument({
                grantId: "grant_output_01",
            }),
        ).toEqual({
            grantId: "grant_output_01",
        });
        expect(
            validateCreationOutputGrantListArgument({
                ...authority,
                cursor: "grant_output_01",
                limit: 8,
            }),
        ).toEqual({
            ...authority,
            cursor: "grant_output_01",
            limit: 8,
        });
        expect(() =>
            validateCreationOutputGrantListArgument({
                ...authority,
                cursor: null,
                limit: 9,
            }),
        ).toThrow(/page limit/iu);
        expect(() =>
            validateCreationOutputGrantListArgument({
                ...authority,
                cursor: null,
                limit: 8,
                path: "/private/output",
            }),
        ).toThrow();
        expect(
            validateCreationOutputGrantRevokeArgument({
                grantId: "grant_output_01",
                expectedGeneration: 2,
            }),
        ).toEqual({ grantId: "grant_output_01", expectedGeneration: 2 });
        expect(validateCreationJobIdArgument({ jobId: "compile_01" })).toEqual({
            jobId: "compile_01",
        });
        expect(
            validateCreationJobListArgument({
                workspaceId: "creation_workspace",
                state: "orphaned",
                afterSequence: 8,
                limit: 8,
            }),
        ).toEqual({
            workspaceId: "creation_workspace",
            state: "orphaned",
            afterSequence: 8,
            limit: 8,
        });
        expect(
            validateCreationJobMutationArgument({
                jobId: "compile_01",
                expectedGeneration: 2,
                expectedRecordHash: recordHash,
            }),
        ).toEqual({
            jobId: "compile_01",
            expectedGeneration: 2,
            expectedRecordHash: recordHash,
        });
        expect(
            validateCreationJobRecoveryArgument({
                jobId: "compile_01",
                mode: "cleanup",
                expectedGeneration: 2,
                expectedRecordHash: recordHash,
            }),
        ).toEqual({
            jobId: "compile_01",
            mode: "cleanup",
            expectedGeneration: 2,
            expectedRecordHash: recordHash,
        });
        expect(
            validateCreationEventListArgument({
                workspaceId: "creation_workspace",
                afterId: 0,
                limit: 256,
            }),
        ).toEqual({
            workspaceId: "creation_workspace",
            afterId: 0,
            limit: 256,
        });

        for (const value of [
            { ...authority, operation: "creation.compile" },
            { ...authority, path: "/private/project" },
            { ...authority, jobId: "UPPERCASE" },
        ]) {
            expect(() => validateCreationCompileArgument(value)).toThrow();
        }
        for (const value of [
            {
                ...authority,
                document: { ...document, score: Number.POSITIVE_INFINITY },
                dependencyArtifactIds: [],
            },
            {
                ...authority,
                document: { ...document, label: "\ud800" },
                dependencyArtifactIds: [],
            },
            {
                ...authority,
                document,
                dependencyArtifactIds: ["artifact_02", "artifact_01"],
            },
            {
                ...authority,
                document,
                dependencyArtifactIds: ["artifact_01", "artifact_01"],
            },
            {
                ...authority,
                document,
                dependencyArtifactIds: [],
                provider: "remote",
            },
            {
                ...authority,
                document: { ...document, payload: "x".repeat(1024 * 1024) },
                dependencyArtifactIds: [],
            },
        ]) {
            expect(() =>
                validateCreationArtifactAdmissionArgument(value),
            ).toThrow();
        }
        for (const value of [
            { ...assetProcess, operation: "asset.process" },
            {
                ...assetProcess,
                licenseArtifactIds: [
                    "artifact_license_01",
                    "artifact_license_01",
                ],
            },
            {
                ...assetProcess,
                acceptanceResults: [
                    { ...assetProcess.acceptanceResults[0], criterionIndex: 1 },
                ],
            },
            {
                ...assetProcess,
                acceptanceResults: [
                    {
                        ...assetProcess.acceptanceResults[0],
                        evidenceHashes: ["2".repeat(64), "1".repeat(64)],
                    },
                ],
            },
            { ...assetProcess, provider: "remote" },
        ]) {
            expect(() => validateCreationAssetProcessArgument(value)).toThrow();
        }
        for (const value of [
            { ...assetSeal, qaReportArtifactIds: [] },
            { ...assetSeal, qaReportArtifactIds: ["artifact_b", "artifact_a"] },
            { ...assetSeal, expectedTargetGrantGeneration: -1 },
            { ...assetSeal, path: "/renderer/private" },
            { ...assetSeal, operation: "asset.release.seal" },
            { ...assetSeal, kind: "generic_assetpack_directory" },
        ]) {
            expect(() =>
                validateCreationAssetReleaseSealArgument(value),
            ).toThrow();
        }
        for (const value of [
            { ...runtimeCompose, adapterId: "renderer_supplied" },
            { ...runtimeCompose, path: "/renderer/private" },
            {
                ...runtimeCompose,
                assetpackArtifactId: runtimeCompose.gamepackArtifactId,
            },
            { ...runtimeCompose, expectedTargetGrantGeneration: -1 },
        ]) {
            expect(() =>
                validateCreationRuntimeComposeArgument(value),
            ).toThrow();
        }
        for (const value of [
            { ...runtimeBundleBuild, path: "/renderer/private" },
            {
                ...runtimeBundleBuild,
                runtimeSupportReportArtifactId:
                    runtimeBundleBuild.gamepackArtifactId,
            },
            { ...runtimeBundleBuild, expectedSourceGrantGeneration: -1 },
            { ...runtimeBundleBuild, expectedTargetGrantGeneration: -1 },
            {
                ...runtimeBundleBuild,
                targetGrantId: runtimeBundleBuild.sourceGrantId,
            },
        ]) {
            expect(() =>
                validateCreationRuntimeBundleBuildArgument(value),
            ).toThrow();
        }
        for (const value of [
            { ...materializationBundleBuild, path: "/renderer/private" },
            {
                ...materializationBundleBuild,
                operation: "game.materialization.bundle.build",
            },
            {
                ...materializationBundleBuild,
                kind: "game_materialization_bundle_directory",
            },
            {
                ...materializationBundleBuild,
                expectedSourceGrantGeneration: -1,
            },
            {
                ...materializationBundleBuild,
                expectedTargetGrantGeneration: -1,
            },
            {
                ...materializationBundleBuild,
                targetGrantId: materializationBundleBuild.sourceGrantId,
            },
        ]) {
            expect(() =>
                validateCreationMaterializationBundleBuildArgument(value),
            ).toThrow();
        }
        for (const value of [
            { ...gameMaterialize, path: "/renderer/private" },
            { ...gameMaterialize, operation: "game.materialize" },
            { ...gameMaterialize, kind: "standalone_game_directory" },
            { ...gameMaterialize, adapterId: "renderer_selected" },
            { ...gameMaterialize, expectedSourceGrantGeneration: -1 },
            { ...gameMaterialize, expectedTargetGrantGeneration: -1 },
            {
                ...gameMaterialize,
                targetGrantId: gameMaterialize.sourceGrantId,
            },
        ]) {
            expect(() =>
                validateCreationGameMaterializeArgument(value),
            ).toThrow();
        }
        for (const value of [
            { ...gamePackage, path: "/renderer/private.wfgame" },
            { ...gamePackage, operation: "game.package" },
            { ...gamePackage, kind: "game_package_file" },
            { ...gamePackage, expectedSourceGrantGeneration: -1 },
            { ...gamePackage, expectedTargetGrantGeneration: -1 },
            { ...gamePackage, targetGrantId: gamePackage.sourceGrantId },
        ]) {
            expect(() => validateCreationGamePackageArgument(value)).toThrow();
        }
        for (const value of [
            { ...gamePackageExtract, path: "/renderer/private" },
            { ...gamePackageExtract, operation: "game.package.extract" },
            { ...gamePackageExtract, kind: "standalone_game_directory" },
            { ...gamePackageExtract, expectedSourceGrantGeneration: -1 },
            { ...gamePackageExtract, expectedTargetGrantGeneration: -1 },
            {
                ...gamePackageExtract,
                targetGrantId: gamePackageExtract.sourceGrantId,
            },
        ]) {
            expect(() =>
                validateCreationGamePackageExtractArgument(value),
            ).toThrow();
        }
        expect(() =>
            validateCreationJobListArgument({
                workspaceId: "creation_workspace",
                state: "paused",
                afterSequence: 0,
                limit: 8,
            }),
        ).toThrow();
        expect(() =>
            validateCreationJobMutationArgument({
                jobId: "compile_01",
                expectedGeneration: -1,
                expectedRecordHash: recordHash,
            }),
        ).toThrow();
        expect(() =>
            validateCreationJobRecoveryArgument({
                jobId: "compile_01",
                mode: "retry",
                expectedGeneration: 2,
                expectedRecordHash: recordHash,
            }),
        ).toThrow();
        expect(() =>
            validateCreationEventListArgument({
                workspaceId: "creation_workspace",
                afterId: 0,
                limit: 257,
            }),
        ).toThrow();
    });

    it("maps fixed renderer operations to closed v4 service methods", async () => {
        const harness = createIpcHarness();

        await harness.invoke(IPC_CHANNELS.compileCreationProject, {
            ...authority,
            jobId: "compile_01",
        });
        await harness.invoke(IPC_CHANNELS.admitCreationArtifact, {
            ...authority,
            document,
            dependencyArtifactIds: ["artifact_01"],
        });
        await harness.invoke(IPC_CHANNELS.processCreationAsset, assetProcess);
        await harness.invoke(IPC_CHANNELS.getCreationJob, {
            jobId: "compile_01",
        });
        await harness.invoke(IPC_CHANNELS.listCreationJobs, {
            workspaceId: "creation_workspace",
            state: null,
            afterSequence: 0,
            limit: 8,
        });
        await harness.invoke(IPC_CHANNELS.cancelCreationJob, {
            jobId: "compile_01",
            expectedGeneration: 0,
            expectedRecordHash: recordHash,
        });
        await harness.invoke(IPC_CHANNELS.recoverCreationJob, {
            jobId: "compile_01",
            mode: "resume",
            expectedGeneration: 1,
            expectedRecordHash: recordHash,
        });
        await harness.invoke(IPC_CHANNELS.listCreationEvents, {
            workspaceId: "creation_workspace",
            afterId: 0,
            limit: 64,
        });

        expect(
            harness.request.mock.calls.map((call) => [
                call[1],
                call[2],
                call[4],
            ]),
        ).toEqual([
            [
                "creation_job.create",
                {
                    job_id: "compile_01",
                    workspace_id: "creation_workspace",
                    operation: "creation.compile",
                    expected_root_generation: 4,
                    expected_source_revision: "a".repeat(64),
                    expected_workflow_status_hash: "b".repeat(64),
                    expected_artifact_snapshot_hash: "c".repeat(64),
                },
                4,
            ],
            [
                "creation_job.create",
                {
                    workspace_id: "creation_workspace",
                    operation: "artifact.admit",
                    expected_root_generation: 4,
                    expected_source_revision: "a".repeat(64),
                    expected_workflow_status_hash: "b".repeat(64),
                    expected_artifact_snapshot_hash: "c".repeat(64),
                    document,
                    dependency_artifact_ids: ["artifact_01"],
                },
                4,
            ],
            [
                "creation_job.create",
                {
                    job_id: "process_01",
                    workspace_id: "creation_workspace",
                    operation: "asset.process",
                    expected_root_generation: 4,
                    expected_source_revision: "a".repeat(64),
                    expected_workflow_status_hash: "b".repeat(64),
                    expected_artifact_snapshot_hash: "c".repeat(64),
                    license_artifact_ids: ["artifact_license_01"],
                    recipe_id: "board_ui_recipe",
                    processing_receipt_id: "board_ui_processing_receipt",
                    qa_report_id: "board_ui_qa",
                    acceptance_results: [
                        {
                            criterion_index: 0,
                            criterion_sha256: "f".repeat(64),
                            status: "passed",
                            evidence_hashes: ["1".repeat(64)],
                        },
                    ],
                },
                4,
            ],
            ["creation_job.get", { job_id: "compile_01" }, 4],
            [
                "creation_job.list",
                {
                    workspace_id: "creation_workspace",
                    state: null,
                    after_sequence: 0,
                    limit: 8,
                },
                4,
            ],
            [
                "creation_job.cancel",
                {
                    job_id: "compile_01",
                    expected_generation: 0,
                    expected_record_hash: recordHash,
                },
                4,
            ],
            [
                "creation_job.recover",
                {
                    job_id: "compile_01",
                    mode: "resume",
                    expected_generation: 1,
                    expected_record_hash: recordHash,
                },
                4,
            ],
            [
                "creation_event.list",
                {
                    workspace_id: "creation_workspace",
                    after_id: 0,
                    limit: 64,
                },
                4,
            ],
        ]);
    });

    it("owns output selection in main and maps only fixed release operations", async () => {
        const harness = createIpcHarness();

        await harness.invoke(IPC_CHANNELS.selectCreationAssetpackOutput, {
            workspaceId: "creation_workspace",
        });
        await harness.invoke(IPC_CHANNELS.getCreationAssetpackOutput, {
            grantId: "grant_output_01",
        });
        await harness.invoke(IPC_CHANNELS.listCreationOutputGrants, {
            ...authority,
            cursor: null,
            limit: 8,
        });
        await harness.invoke(IPC_CHANNELS.revokeCreationAssetpackOutput, {
            grantId: "grant_output_01",
            expectedGeneration: 2,
        });
        await harness.invoke(IPC_CHANNELS.sealCreationAssetRelease, assetSeal);
        await harness.invoke(
            IPC_CHANNELS.composeCreationRuntime,
            runtimeCompose,
        );
        await harness.invoke(IPC_CHANNELS.selectCreationRuntimeBundleOutput, {
            workspaceId: "creation_workspace",
        });
        await harness.invoke(
            IPC_CHANNELS.buildCreationRuntimeBundle,
            runtimeBundleBuild,
        );
        await harness.invoke(
            IPC_CHANNELS.selectCreationMaterializationBundleOutput,
            {
                workspaceId: "creation_workspace",
            },
        );
        await harness.invoke(
            IPC_CHANNELS.buildCreationMaterializationBundle,
            materializationBundleBuild,
        );
        await harness.invoke(IPC_CHANNELS.selectCreationStandaloneGameOutput, {
            workspaceId: "creation_workspace",
        });
        await harness.invoke(
            IPC_CHANNELS.materializeCreationGame,
            gameMaterialize,
        );
        await harness.invoke(IPC_CHANNELS.selectCreationGamePackageOutput, {
            workspaceId: "creation_workspace",
        });
        await harness.invoke(IPC_CHANNELS.packageCreationGame, gamePackage);
        await harness.invoke(
            IPC_CHANNELS.selectCreationGamePackageExtractionOutput,
            {
                workspaceId: "creation_workspace",
            },
        );
        await harness.invoke(
            IPC_CHANNELS.extractCreationGamePackage,
            gamePackageExtract,
        );

        expect(harness.showSaveDialog).toHaveBeenCalledTimes(6);
        expect(
            harness.request.mock.calls.map((call) => [
                call[1],
                call[2],
                call[4],
            ]),
        ).toEqual([
            [
                "creation_output_grant.create",
                {
                    workspace_id: "creation_workspace",
                    kind: "generic_assetpack_directory",
                    display_name: "target-1",
                    path: "/selected/target-1",
                },
                4,
            ],
            ["creation_output_grant.get", { grant_id: "grant_output_01" }, 4],
            [
                "creation_output_grant.list",
                {
                    workspace_id: "creation_workspace",
                    expected_root_generation: 4,
                    expected_source_revision: "a".repeat(64),
                    expected_workflow_status_hash: "b".repeat(64),
                    expected_artifact_snapshot_hash: "c".repeat(64),
                    cursor: null,
                    limit: 8,
                },
                4,
            ],
            [
                "creation_output_grant.revoke",
                { grant_id: "grant_output_01", expected_generation: 2 },
                4,
            ],
            [
                "creation_job.create",
                {
                    job_id: "seal_01",
                    workspace_id: "creation_workspace",
                    operation: "asset.release.seal",
                    expected_root_generation: 4,
                    expected_source_revision: "a".repeat(64),
                    expected_workflow_status_hash: "b".repeat(64),
                    expected_artifact_snapshot_hash: "c".repeat(64),
                    qa_report_artifact_ids: ["artifact_qa_01"],
                    manifest_id: "release_manifest_01",
                    target_grant_id: "grant_output_01",
                    expected_target_grant_generation: 2,
                },
                4,
            ],
            [
                "creation_job.create",
                {
                    job_id: "compose_01",
                    workspace_id: "creation_workspace",
                    operation: "runtime.compose",
                    expected_root_generation: 4,
                    expected_source_revision: "a".repeat(64),
                    expected_workflow_status_hash: "b".repeat(64),
                    expected_artifact_snapshot_hash: "c".repeat(64),
                    gamepack_artifact_id: "artifact_gamepack_01",
                    asset_inventory_artifact_id: "artifact_inventory_01",
                    assetpack_artifact_id: "artifact_assetpack_01",
                    target_grant_id: "grant_output_01",
                    expected_target_grant_generation: 2,
                },
                4,
            ],
            [
                "creation_output_grant.create",
                {
                    workspace_id: "creation_workspace",
                    kind: "game_runtime_bundle_directory",
                    display_name: "target-2",
                    path: "/selected/target-2",
                },
                4,
            ],
            [
                "creation_job.create",
                {
                    job_id: "bundle_01",
                    workspace_id: "creation_workspace",
                    operation: "runtime.bundle.build",
                    expected_root_generation: 4,
                    expected_source_revision: "a".repeat(64),
                    expected_workflow_status_hash: "b".repeat(64),
                    expected_artifact_snapshot_hash: "c".repeat(64),
                    gamepack_artifact_id: "artifact_gamepack_01",
                    asset_inventory_artifact_id: "artifact_inventory_01",
                    assetpack_artifact_id: "artifact_assetpack_01",
                    runtime_snapshot_artifact_id:
                        "artifact_runtime_snapshot_01",
                    runtime_adapter_registry_artifact_id:
                        "artifact_runtime_registry_01",
                    runtime_composition_artifact_id:
                        "artifact_runtime_composition_01",
                    runtime_support_report_artifact_id:
                        "artifact_runtime_support_01",
                    source_grant_id: "grant_assetpack_01",
                    expected_source_grant_generation: 3,
                    target_grant_id: "grant_bundle_01",
                    expected_target_grant_generation: 1,
                },
                4,
            ],
            [
                "creation_output_grant.create",
                {
                    workspace_id: "creation_workspace",
                    kind: "game_materialization_bundle_directory",
                    display_name: "target-3",
                    path: "/selected/target-3",
                },
                4,
            ],
            [
                "creation_job.create",
                {
                    job_id: "materialization_01",
                    workspace_id: "creation_workspace",
                    operation: "game.materialization.bundle.build",
                    expected_root_generation: 4,
                    expected_source_revision: "a".repeat(64),
                    expected_workflow_status_hash: "b".repeat(64),
                    expected_artifact_snapshot_hash: "c".repeat(64),
                    runtime_bundle_artifact_id: "artifact_runtime_bundle_01",
                    source_grant_id: "grant_runtime_bundle_01",
                    expected_source_grant_generation: 2,
                    target_grant_id: "grant_materialization_01",
                    expected_target_grant_generation: 1,
                },
                4,
            ],
            [
                "creation_output_grant.create",
                {
                    workspace_id: "creation_workspace",
                    kind: "standalone_game_directory",
                    display_name: "target-4",
                    path: "/selected/target-4",
                },
                4,
            ],
            [
                "creation_job.create",
                {
                    job_id: "standalone_01",
                    workspace_id: "creation_workspace",
                    operation: "game.materialize",
                    expected_root_generation: 4,
                    expected_source_revision: "a".repeat(64),
                    expected_workflow_status_hash: "b".repeat(64),
                    expected_artifact_snapshot_hash: "c".repeat(64),
                    materialization_bundle_artifact_id:
                        "artifact_materialization_bundle_01",
                    source_grant_id: "grant_materialization_01",
                    expected_source_grant_generation: 2,
                    target_grant_id: "grant_standalone_01",
                    expected_target_grant_generation: 1,
                },
                4,
            ],
            [
                "creation_output_grant.create",
                {
                    workspace_id: "creation_workspace",
                    kind: "game_package_file",
                    display_name: "target-5",
                    path: "/selected/target-5",
                },
                4,
            ],
            [
                "creation_job.create",
                {
                    job_id: "package_01",
                    workspace_id: "creation_workspace",
                    operation: "game.package",
                    expected_root_generation: 4,
                    expected_source_revision: "a".repeat(64),
                    expected_workflow_status_hash: "b".repeat(64),
                    expected_artifact_snapshot_hash: "c".repeat(64),
                    standalone_game_artifact_id: "artifact_standalone_game_01",
                    source_grant_id: "grant_standalone_01",
                    expected_source_grant_generation: 2,
                    target_grant_id: "grant_package_01",
                    expected_target_grant_generation: 1,
                },
                4,
            ],
            [
                "creation_output_grant.create",
                {
                    workspace_id: "creation_workspace",
                    kind: "standalone_game_directory",
                    display_name: "target-6",
                    path: "/selected/target-6",
                },
                4,
            ],
            [
                "creation_job.create",
                {
                    job_id: "extract_01",
                    workspace_id: "creation_workspace",
                    operation: "game.package.extract",
                    expected_root_generation: 4,
                    expected_source_revision: "a".repeat(64),
                    expected_workflow_status_hash: "b".repeat(64),
                    expected_artifact_snapshot_hash: "c".repeat(64),
                    game_package_artifact_id: "artifact_game_package_01",
                    source_grant_id: "grant_package_01",
                    expected_source_grant_generation: 2,
                    target_grant_id: "grant_extracted_standalone_01",
                    expected_target_grant_generation: 1,
                },
                4,
            ],
        ]);
    });

    it("adds a fixed v5 authority grant listing without changing legacy v4 listing", async () => {
        const harness = createIpcHarness();
        harness.request.mockImplementation(
            (requestId, method, params, _timeout, protocolVersion = 1) => {
                void params;
                if (method === "service.initialize") {
                    return Promise.resolve(
                        v5Response(requestId, method, {
                            service: "world-forge.studio",
                            service_version: 5,
                            protocol: "rpg-world-forge.studio_protocol",
                            protocol_version: 5,
                            capabilities: {
                                asset_authority_reviews: true,
                                asset_previews: true,
                                asset_release_authority: true,
                                creation_asset_previews: true,
                                creation_evidence_projection: true,
                                creation_jobs: true,
                                creation_materialization_bundle: true,
                                creation_output_grants: true,
                                creation_preview_pre_release: true,
                                creation_runtime_bundle: true,
                                creation_runtime_compose: true,
                                game_package_extraction: true,
                                game_packaging: true,
                                materialization_execution: true,
                                runtime_headless_authority: true,
                            },
                        }),
                    );
                }
                if (method === "creation_output_grant.list") {
                    return Promise.resolve({
                        protocol: "rpg-world-forge.studio_protocol",
                        protocol_version: protocolVersion,
                        kind: "response",
                        request_id: requestId,
                        method,
                        result: {
                            authority: serviceAuthority(),
                            artifact_snapshot_hash: authorityArtifactHash,
                            grants:
                                protocolVersion === 5
                                    ? [
                                          headlessTargetGrantFixture(
                                              "grant_headless_target",
                                              {},
                                          ),
                                      ]
                                    : [],
                            next_cursor: null,
                        },
                    });
                }
                throw new Error(`unexpected ${method}`);
            },
        );

        await harness.invoke(IPC_CHANNELS.listCreationOutputGrants, {
            ...authority,
            cursor: null,
            limit: 8,
        });
        await harness.invoke(IPC_CHANNELS.listCreationAuthorityOutputGrants, {
            ...authority,
            cursor: null,
            limit: 8,
        });

        expect(
            harness.request.mock.calls.map((call) => [call[1], call[4]]),
        ).toEqual([
            ["creation_output_grant.list", 4],
            ["service.initialize", 5],
            ["creation_output_grant.list", 5],
        ]);
    });

    it("removes every creation job handler during teardown", () => {
        const harness = createIpcHarness();
        harness.dispose();
        for (const channel of [
            IPC_CHANNELS.compileCreationProject,
            IPC_CHANNELS.admitCreationArtifact,
            IPC_CHANNELS.processCreationAsset,
            IPC_CHANNELS.selectCreationAssetpackOutput,
            IPC_CHANNELS.getCreationAssetpackOutput,
            IPC_CHANNELS.listCreationOutputGrants,
            IPC_CHANNELS.listCreationAuthorityOutputGrants,
            IPC_CHANNELS.revokeCreationAssetpackOutput,
            IPC_CHANNELS.sealCreationAssetRelease,
            IPC_CHANNELS.composeCreationRuntime,
            IPC_CHANNELS.selectCreationRuntimeBundleOutput,
            IPC_CHANNELS.buildCreationRuntimeBundle,
            IPC_CHANNELS.selectCreationMaterializationBundleOutput,
            IPC_CHANNELS.buildCreationMaterializationBundle,
            IPC_CHANNELS.selectCreationStandaloneGameOutput,
            IPC_CHANNELS.materializeCreationGame,
            IPC_CHANNELS.selectCreationGamePackageOutput,
            IPC_CHANNELS.packageCreationGame,
            IPC_CHANNELS.selectCreationGamePackageExtractionOutput,
            IPC_CHANNELS.extractCreationGamePackage,
            IPC_CHANNELS.getCreationJob,
            IPC_CHANNELS.listCreationJobs,
            IPC_CHANNELS.cancelCreationJob,
            IPC_CHANNELS.recoverCreationJob,
            IPC_CHANNELS.listCreationEvents,
        ]) {
            expect(harness.removeHandler).toHaveBeenCalledWith(channel);
        }
    });
});

describe("Studio named asset catalog IPC contracts", () => {
    const revision = "a".repeat(64);
    const entryId = `asset_${"b".repeat(64)}`;

    it("accepts only initial, revision-bound page, and exact inspection inputs", () => {
        expect(
            validateAssetCatalogListArgument({ workspaceId: "workspace_01" }),
        ).toEqual({
            workspaceId: "workspace_01",
        });
        expect(
            validateAssetCatalogListArgument({
                workspaceId: "workspace_01",
                offset: 0,
                expectedManifestRevision: revision,
            }),
        ).toEqual({
            workspaceId: "workspace_01",
            offset: 0,
            expectedManifestRevision: revision,
        });
        expect(
            validateAssetCatalogListArgument({
                workspaceId: "workspace_01",
                offset: 64,
                expectedManifestRevision: revision,
            }),
        ).toEqual({
            workspaceId: "workspace_01",
            offset: 64,
            expectedManifestRevision: revision,
        });
        expect(
            validateAssetCatalogInspectArgument({
                workspaceId: "workspace_01",
                manifestRevision: revision,
                entryId,
            }),
        ).toEqual({
            workspaceId: "workspace_01",
            manifestRevision: revision,
            entryId,
        });
    });

    it.each([
        { workspaceId: "workspace_01", offset: 64 },
        {
            workspaceId: "workspace_01",
            expectedManifestRevision: "a".repeat(64),
        },
        {
            workspaceId: "workspace_01",
            offset: -1,
            expectedManifestRevision: "a".repeat(64),
        },
        {
            workspaceId: "workspace_01",
            offset: 1.5,
            expectedManifestRevision: "a".repeat(64),
        },
        {
            workspaceId: "workspace_01",
            offset: Number.MAX_SAFE_INTEGER + 1,
            expectedManifestRevision: "a".repeat(64),
        },
        {
            workspaceId: "workspace_01",
            offset: 64,
            expectedManifestRevision: "A".repeat(64),
        },
        { workspaceId: "workspace_01", limit: 64 },
        { workspaceId: "workspace_01", cursor: "opaque" },
        { workspaceId: "workspace_01", path: "assets/manifest.json" },
        { workspaceId: "workspace_01", category: "manifest" },
        { workspaceId: "workspace_01", mediaType: "application/json" },
        { workspaceId: "workspace_01", method: "asset.catalog.list" },
    ])("rejects renderer-shaped or malformed list input %#", (value) => {
        expect(() => validateAssetCatalogListArgument(value)).toThrow();
    });

    it.each([
        {
            workspaceId: "workspace_01",
            manifestRevision: "A".repeat(64),
            entryId: `asset_${"b".repeat(64)}`,
        },
        {
            workspaceId: "workspace_01",
            manifestRevision: "a".repeat(64),
            entryId: "asset_bad",
        },
        {
            workspaceId: "workspace_01",
            manifestRevision: "a".repeat(64),
            entryId: `asset_${"b".repeat(64)}`,
            path: "assets/manifest.json",
        },
        {
            workspaceId: "workspace_01",
            manifestRevision: "a".repeat(64),
            entryId: `asset_${"b".repeat(64)}`,
            binary: true,
        },
    ])("rejects malformed or authority-shaped inspection input %#", (value) => {
        expect(() => validateAssetCatalogInspectArgument(value)).toThrow();
    });

    it("maps initial, revision-bound, and inspect calls with main-owned bounds", async () => {
        const harness = createIpcHarness();
        expect(
            await harness.invoke(IPC_CHANNELS.listAssetCatalog, {
                workspaceId: "workspace_01",
            }),
        ).toMatchObject({ ok: true });
        expect(
            await harness.invoke(IPC_CHANNELS.listAssetCatalog, {
                workspaceId: "workspace_01",
                offset: 64,
                expectedManifestRevision: revision,
            }),
        ).toMatchObject({ ok: true });
        expect(
            await harness.invoke(IPC_CHANNELS.inspectAssetCatalogEntry, {
                workspaceId: "workspace_01",
                manifestRevision: revision,
                entryId,
            }),
        ).toMatchObject({ ok: true });

        expect(
            harness.request.mock.calls.map((call) => [
                call[1],
                call[2],
                call[3],
            ]),
        ).toEqual([
            [
                "asset.catalog.list",
                { workspace_id: "workspace_01", offset: 0, limit: 64 },
                60_000,
            ],
            [
                "asset.catalog.list",
                {
                    workspace_id: "workspace_01",
                    offset: 64,
                    limit: 64,
                    expected_manifest_revision: revision,
                },
                60_000,
            ],
            [
                "asset.catalog.inspect",
                {
                    workspace_id: "workspace_01",
                    expected_manifest_revision: revision,
                    entry_id: entryId,
                },
                60_000,
            ],
        ]);
    });

    it("accepts exact correlated list and inspection replies", async () => {
        const harness = createIpcHarness();
        harness.request.mockImplementationOnce((requestId: string) =>
            Promise.resolve(
                createAssetCatalogListResponse(requestId, {
                    manifestRevision: "d".repeat(64),
                    offset: 0,
                    entries: createAssetEntries(1),
                    nextOffset: null,
                }),
            ),
        );
        expect(
            await harness.invoke(IPC_CHANNELS.listAssetCatalog, {
                workspaceId: "workspace_01",
            }),
        ).toMatchObject({
            ok: true,
            value: {
                method: "asset.catalog.list",
                result: {
                    manifest_revision: "d".repeat(64),
                    offset: 0,
                    next_offset: null,
                },
            },
        });

        harness.request.mockImplementationOnce((requestId: string) =>
            Promise.resolve(
                createAssetCatalogListResponse(requestId, {
                    manifestRevision: revision,
                    offset: 64,
                    entries: createAssetEntries(64),
                    nextOffset: 128,
                }),
            ),
        );
        expect(
            await harness.invoke(IPC_CHANNELS.listAssetCatalog, {
                workspaceId: "workspace_01",
                offset: 64,
                expectedManifestRevision: revision,
            }),
        ).toMatchObject({
            ok: true,
            value: {
                method: "asset.catalog.list",
                result: {
                    manifest_revision: revision,
                    offset: 64,
                    next_offset: 128,
                },
            },
        });

        harness.request.mockImplementationOnce((requestId: string) =>
            Promise.resolve(
                createAssetCatalogInspectResponse(requestId, revision, entryId),
            ),
        );
        expect(
            await harness.invoke(IPC_CHANNELS.inspectAssetCatalogEntry, {
                workspaceId: "workspace_01",
                manifestRevision: revision,
                entryId,
            }),
        ).toMatchObject({
            ok: true,
            value: {
                method: "asset.catalog.inspect",
                result: {
                    manifest_revision: revision,
                    entry: { entry_id: entryId },
                    inspection: { kind: "json" },
                },
            },
        });
    });

    it("rejects mismatched, duplicate, oversized, nonmonotonic, and forged list replies", async () => {
        const harness = createIpcHarness();
        const exactArgument = {
            workspaceId: "workspace_01",
            offset: 64,
            expectedManifestRevision: revision,
        };
        const base = createAssetCatalogListResponse("placeholder", {
            manifestRevision: revision,
            offset: 64,
            entries: createAssetEntries(1),
            nextOffset: null,
        });
        const cases = [
            (requestId: string) => ({
                ...base,
                request_id: `${requestId}-wrong`,
            }),
            (requestId: string) => ({
                ...base,
                request_id: requestId,
                method: "source.list",
            }),
            (requestId: string) => ({
                ...base,
                request_id: requestId,
                result: { ...base.result, manifest_revision: "c".repeat(64) },
            }),
            (requestId: string) => ({
                ...base,
                request_id: requestId,
                result: { ...base.result, offset: 0 },
            }),
            (requestId: string) => ({
                ...base,
                request_id: requestId,
                result: { ...base.result, limit: 63 },
            }),
            (requestId: string) =>
                createAssetCatalogListResponse(requestId, {
                    manifestRevision: revision,
                    offset: 64,
                    entries: [createAssetEntry(0), createAssetEntry(0)],
                    nextOffset: null,
                }),
            (requestId: string) =>
                createAssetCatalogListResponse(requestId, {
                    manifestRevision: revision,
                    offset: 64,
                    entries: createAssetEntries(65),
                    nextOffset: null,
                }),
            (requestId: string) =>
                createAssetCatalogListResponse(requestId, {
                    manifestRevision: revision,
                    offset: 64,
                    entries: createAssetEntries(1),
                    nextOffset: 64,
                }),
            (requestId: string) =>
                createAssetCatalogListResponse(requestId, {
                    manifestRevision: revision,
                    offset: 64,
                    entries: createAssetEntries(64),
                    nextOffset: 129,
                }),
            (requestId: string) => ({
                ...createAssetCatalogListResponse(requestId, {
                    manifestRevision: revision,
                    offset: 64,
                    entries: createAssetEntries(1),
                    nextOffset: null,
                }),
                result: {
                    ...base.result,
                    workspace_path: "/private/world",
                },
            }),
            (requestId: string) =>
                createAssetCatalogListResponse(requestId, {
                    manifestRevision: revision,
                    offset: 64,
                    entries: [
                        {
                            ...createAssetEntry(0),
                            path: "/private/world/asset.png",
                        },
                    ],
                    nextOffset: null,
                }),
            (requestId: string) =>
                createAssetCatalogListResponse(requestId, {
                    manifestRevision: revision,
                    offset: 64,
                    entries: [{ ...createAssetEntry(0), binary: "AA==" }],
                    nextOffset: null,
                }),
        ];

        for (const reply of cases) {
            harness.request.mockImplementationOnce((requestId: string) =>
                Promise.resolve(reply(requestId)),
            );
            expect(
                await harness.invoke(
                    IPC_CHANNELS.listAssetCatalog,
                    exactArgument,
                ),
            ).toMatchObject({
                ok: false,
                error: { code: "service_unavailable" },
            });
        }

        const largestPageOffset = Number.MAX_SAFE_INTEGER - 63;
        harness.request.mockImplementationOnce((requestId: string) =>
            Promise.resolve(
                createAssetCatalogListResponse(requestId, {
                    manifestRevision: revision,
                    offset: largestPageOffset,
                    entries: createAssetEntries(64),
                    nextOffset: Number.MAX_SAFE_INTEGER + 1,
                }),
            ),
        );
        expect(
            await harness.invoke(IPC_CHANNELS.listAssetCatalog, {
                workspaceId: "workspace_01",
                offset: largestPageOffset,
                expectedManifestRevision: revision,
            }),
        ).toMatchObject({
            ok: false,
            error: { code: "service_unavailable" },
        });
    });

    it("rejects inspection replies with wrong authority or extra binary data", async () => {
        const harness = createIpcHarness();
        const argument = {
            workspaceId: "workspace_01",
            manifestRevision: revision,
            entryId,
        };
        const cases = [
            (requestId: string) =>
                createAssetCatalogInspectResponse(
                    requestId,
                    "c".repeat(64),
                    entryId,
                ),
            (requestId: string) =>
                createAssetCatalogInspectResponse(
                    requestId,
                    revision,
                    `asset_${"c".repeat(64)}`,
                ),
            (requestId: string) => ({
                ...createAssetCatalogInspectResponse(
                    requestId,
                    revision,
                    entryId,
                ),
                result: {
                    ...createAssetCatalogInspectResponse(
                        requestId,
                        revision,
                        entryId,
                    ).result,
                    inspection: {
                        ...createAssetCatalogInspectResponse(
                            requestId,
                            revision,
                            entryId,
                        ).result.inspection,
                        binary: "AA==",
                    },
                },
            }),
        ];

        for (const reply of cases) {
            harness.request.mockImplementationOnce((requestId: string) =>
                Promise.resolve(reply(requestId)),
            );
            expect(
                await harness.invoke(
                    IPC_CHANNELS.inspectAssetCatalogEntry,
                    argument,
                ),
            ).toMatchObject({
                ok: false,
                error: { code: "service_unavailable" },
            });
        }
    });

    it("rejects untrusted and extra-argument catalog requests before the service", async () => {
        const harness = createIpcHarness();
        expect(
            await harness.invoke(
                IPC_CHANNELS.listAssetCatalog,
                { workspaceId: "workspace_01" },
                { trusted: false },
            ),
        ).toMatchObject({ ok: false, error: { code: "invalid_request" } });
        expect(
            await harness.invoke(
                IPC_CHANNELS.inspectAssetCatalogEntry,
                {
                    workspaceId: "workspace_01",
                    manifestRevision: revision,
                    entryId,
                },
                { extraArgument: true },
            ),
        ).toMatchObject({ ok: false, error: { code: "invalid_request" } });
        expect(harness.request).not.toHaveBeenCalled();
    });

    it("removes both catalog handlers during teardown", () => {
        const harness = createIpcHarness();
        harness.dispose();
        expect(harness.removeHandler).toHaveBeenCalledWith(
            IPC_CHANNELS.listAssetCatalog,
        );
        expect(harness.removeHandler).toHaveBeenCalledWith(
            IPC_CHANNELS.inspectAssetCatalogEntry,
        );
    });
});

describe("Studio named asset preview IPC contracts", () => {
    const revision = "a".repeat(64);
    const entryId = `asset_${"b".repeat(64)}`;
    const handle = "C".repeat(43);

    it("accepts only closed authority, handle, and bounded sequence inputs", () => {
        expect(
            validateAssetPreviewOpenArgument({
                workspaceId: "workspace_01",
                manifestRevision: revision,
                entryId,
            }),
        ).toEqual({
            workspaceId: "workspace_01",
            manifestRevision: revision,
            entryId,
        });
        expect(
            validateAssetPreviewReadArgument({ handle, sequence: 8191 }),
        ).toEqual({
            handle,
            sequence: 8191,
        });
        expect(validateAssetPreviewCloseArgument({ handle })).toEqual({
            handle,
        });

        for (const value of [
            {
                workspaceId: "workspace_01",
                manifestRevision: revision,
                entryId,
                path: "/private/preview.png",
            },
            { handle, sequence: 0, offset: 0 },
            { handle, sequence: 0, size: 65_536 },
            { handle, sequence: 0, encoding: "base64" },
            { handle, sequence: 0, data_base64: "YQ==" },
            { handle, sequence: -1 },
            { handle, sequence: 8192 },
            { handle, sequence: true },
            { handle: "bad" },
        ]) {
            expect(() =>
                "workspaceId" in value
                    ? validateAssetPreviewOpenArgument(value)
                    : "sequence" in value
                      ? validateAssetPreviewReadArgument(value)
                      : validateAssetPreviewCloseArgument(value),
            ).toThrow();
        }
    });

    it.each([
        ["image/png", Buffer.from([0x89, 0x50, 0x4e, 0x47])],
        ["audio/wav", Buffer.from("RIFF....WAVE", "ascii")],
    ] as const)(
        "round-trips %s only as a fresh Uint8Array",
        async (mediaType, payload) => {
            const harness = createIpcHarness();
            harness.request
                .mockImplementationOnce((requestId: string) =>
                    Promise.resolve(
                        createAssetPreviewOpenResponse(
                            requestId,
                            handle,
                            revision,
                            entryId,
                            mediaType,
                            payload,
                        ),
                    ),
                )
                .mockImplementationOnce((requestId: string) =>
                    Promise.resolve(
                        createAssetPreviewReadResponse(
                            requestId,
                            handle,
                            0,
                            payload,
                            payload,
                            true,
                        ),
                    ),
                )
                .mockImplementationOnce((requestId: string) =>
                    Promise.resolve(
                        createAssetPreviewCloseResponse(requestId, handle),
                    ),
                );

            expect(
                await harness.invoke(IPC_CHANNELS.openAssetPreview, {
                    workspaceId: "workspace_01",
                    manifestRevision: revision,
                    entryId,
                }),
            ).toMatchObject({
                ok: true,
                value: { result: { handle, media_type: mediaType } },
            });
            const read = await harness.invoke(
                IPC_CHANNELS.readAssetPreviewChunk,
                {
                    handle,
                    sequence: 0,
                },
            );
            expect(read).toMatchObject({
                ok: true,
                value: {
                    method: "asset.preview.read",
                    result: {
                        handle,
                        sequence: 0,
                        byte_length: payload.byteLength,
                        eof: true,
                    },
                },
            });
            const result = (
                read as { value: { result: Record<string, unknown> } }
            ).value.result;
            expect(result.bytes).toBeInstanceOf(Uint8Array);
            expect(Buffer.from(result.bytes as Uint8Array)).toEqual(payload);
            expect(result).not.toHaveProperty("data_base64");
            expect(result).not.toHaveProperty("path");
            expect(
                await harness.invoke(IPC_CHANNELS.closeAssetPreview, {
                    handle,
                }),
            ).toMatchObject({
                ok: true,
                value: { result: { handle, closed: true } },
            });

            expect(
                harness.request.mock.calls.map((call) => [call[1], call[2]]),
            ).toEqual([
                [
                    "asset.preview.open",
                    {
                        workspace_id: "workspace_01",
                        manifest_revision: revision,
                        entry_id: entryId,
                    },
                ],
                ["asset.preview.read", { handle, sequence: 0 }],
                ["asset.preview.close", { handle }],
            ]);
        },
    );

    it("enforces fixed chunks, sequence, cumulative identity, EOF, and replay copies", async () => {
        const harness = createIpcHarness();
        const first = Buffer.alloc(65_536, 0x61);
        const final = Buffer.from("tail");
        const whole = Buffer.concat([first, final]);
        harness.request
            .mockImplementationOnce((requestId: string) =>
                Promise.resolve(
                    createAssetPreviewOpenResponse(
                        requestId,
                        handle,
                        revision,
                        entryId,
                        "image/png",
                        whole,
                    ),
                ),
            )
            .mockImplementationOnce((requestId: string) =>
                Promise.resolve(
                    createAssetPreviewReadResponse(
                        requestId,
                        handle,
                        0,
                        first,
                        first,
                        false,
                    ),
                ),
            )
            .mockImplementationOnce((requestId: string) =>
                Promise.resolve(
                    createAssetPreviewReadResponse(
                        requestId,
                        handle,
                        1,
                        final,
                        whole,
                        true,
                    ),
                ),
            )
            .mockImplementationOnce((requestId: string) =>
                Promise.resolve(
                    createAssetPreviewReadResponse(
                        requestId,
                        handle,
                        1,
                        final,
                        whole,
                        true,
                    ),
                ),
            );

        await harness.invoke(IPC_CHANNELS.openAssetPreview, {
            workspaceId: "workspace_01",
            manifestRevision: revision,
            entryId,
        });
        const chunk0 = await harness.invoke(
            IPC_CHANNELS.readAssetPreviewChunk,
            {
                handle,
                sequence: 0,
            },
        );
        const chunk1 = await harness.invoke(
            IPC_CHANNELS.readAssetPreviewChunk,
            {
                handle,
                sequence: 1,
            },
        );
        const replay = await harness.invoke(
            IPC_CHANNELS.readAssetPreviewChunk,
            {
                handle,
                sequence: 1,
            },
        );
        expect(chunk0).toMatchObject({
            ok: true,
            value: {
                result: {
                    byte_length: 65_536,
                    cumulative_bytes: 65_536,
                    eof: false,
                },
            },
        });
        expect(chunk1).toMatchObject({
            ok: true,
            value: {
                result: {
                    byte_length: final.byteLength,
                    cumulative_bytes: whole.byteLength,
                    eof: true,
                },
            },
        });
        const firstBytes = (
            chunk1 as { value: { result: { bytes: Uint8Array } } }
        ).value.result.bytes;
        const replayBytes = (
            replay as { value: { result: { bytes: Uint8Array } } }
        ).value.result.bytes;
        expect(replayBytes).not.toBe(firstBytes);
        expect(replayBytes).toEqual(firstBytes);
    });

    it("rejects forged open/read/close correlations and malformed base64", async () => {
        const harness = createIpcHarness();
        const payload = Buffer.from("abc");
        harness.request.mockImplementationOnce((requestId: string) =>
            Promise.resolve(
                createAssetPreviewOpenResponse(
                    requestId,
                    handle,
                    revision,
                    entryId,
                    "image/png",
                    payload,
                ),
            ),
        );
        expect(
            await harness.invoke(IPC_CHANNELS.openAssetPreview, {
                workspaceId: "workspace_01",
                manifestRevision: revision,
                entryId,
            }),
        ).toMatchObject({ ok: true });

        const valid = createAssetPreviewReadResponse(
            "placeholder",
            handle,
            0,
            payload,
            payload,
            true,
        );
        const forged = [
            (requestId: string) => ({
                ...valid,
                request_id: `${requestId}-wrong`,
            }),
            (requestId: string) => ({
                ...valid,
                request_id: requestId,
                method: "source.read",
            }),
            (requestId: string) => ({
                ...valid,
                request_id: requestId,
                result: { ...valid.result, handle: "D".repeat(43) },
            }),
            (requestId: string) => ({
                ...valid,
                request_id: requestId,
                result: {
                    ...valid.result,
                    sequence: 1,
                    cumulative_bytes: 65_539,
                },
            }),
            (requestId: string) => ({
                ...valid,
                request_id: requestId,
                result: { ...valid.result, data_base64: "YR==" },
            }),
            (requestId: string) => ({
                ...valid,
                request_id: requestId,
                result: { ...valid.result, byte_length: 2 },
            }),
            (requestId: string) => ({
                ...valid,
                request_id: requestId,
                result: { ...valid.result, cumulative_bytes: 4 },
            }),
            (requestId: string) => ({
                ...valid,
                request_id: requestId,
                result: { ...valid.result, cumulative_sha256: "0".repeat(64) },
            }),
            (requestId: string) => ({
                ...valid,
                request_id: requestId,
                result: { ...valid.result, eof: false },
            }),
            (requestId: string) => ({
                ...valid,
                request_id: requestId,
                result: { ...valid.result, path: "/private/preview.png" },
            }),
        ];
        for (const reply of forged) {
            harness.request.mockImplementationOnce((requestId: string) =>
                Promise.resolve(reply(requestId)),
            );
            expect(
                await harness.invoke(IPC_CHANNELS.readAssetPreviewChunk, {
                    handle,
                    sequence: 0,
                }),
            ).toMatchObject({
                ok: false,
                error: { code: "service_unavailable" },
            });
        }

        harness.request.mockImplementationOnce((requestId: string) =>
            Promise.resolve(
                createAssetPreviewCloseResponse(requestId, "D".repeat(43)),
            ),
        );
        expect(
            await harness.invoke(IPC_CHANNELS.closeAssetPreview, { handle }),
        ).toMatchObject({ ok: false, error: { code: "service_unavailable" } });
    });

    it("rejects mismatched preview authority, forbidden media, and duplicate handles", async () => {
        const payload = Buffer.from("preview");
        const mutations: Array<
            (
                response: ReturnType<typeof createAssetPreviewOpenResponse>,
            ) => unknown
        > = [
            (response) => ({
                ...response,
                result: {
                    ...response.result,
                    manifest_revision: "d".repeat(64),
                },
            }),
            (response) => ({
                ...response,
                result: {
                    ...response.result,
                    entry_id: `asset_${"d".repeat(64)}`,
                },
            }),
            (response) => ({
                ...response,
                result: { ...response.result, media_type: "font/ttf" },
            }),
            (response) => ({
                ...response,
                result: { ...response.result, media_type: "model/gltf-binary" },
            }),
            (response) => ({
                ...response,
                result: { ...response.result, path: "/private/preview.png" },
            }),
        ];
        for (const mutate of mutations) {
            const harness = createIpcHarness();
            harness.request.mockImplementationOnce((requestId: string) =>
                Promise.resolve(
                    mutate(
                        createAssetPreviewOpenResponse(
                            requestId,
                            handle,
                            revision,
                            entryId,
                            "image/png",
                            payload,
                        ),
                    ),
                ),
            );
            expect(
                await harness.invoke(IPC_CHANNELS.openAssetPreview, {
                    workspaceId: "workspace_01",
                    manifestRevision: revision,
                    entryId,
                }),
            ).toMatchObject({
                ok: false,
                error: { code: "service_unavailable" },
            });
        }

        const duplicate = createIpcHarness();
        duplicate.request
            .mockImplementationOnce((requestId: string) =>
                Promise.resolve(
                    createAssetPreviewOpenResponse(
                        requestId,
                        handle,
                        revision,
                        entryId,
                        "image/png",
                        payload,
                    ),
                ),
            )
            .mockImplementationOnce((requestId: string) =>
                Promise.resolve(
                    createAssetPreviewOpenResponse(
                        requestId,
                        handle,
                        revision,
                        entryId,
                        "image/png",
                        payload,
                    ),
                ),
            );
        const argument = {
            workspaceId: "workspace_01",
            manifestRevision: revision,
            entryId,
        };
        expect(
            await duplicate.invoke(IPC_CHANNELS.openAssetPreview, argument),
        ).toMatchObject({
            ok: true,
        });
        expect(
            await duplicate.invoke(IPC_CHANNELS.openAssetPreview, argument),
        ).toMatchObject({
            ok: false,
            error: { code: "service_unavailable" },
        });
    });

    it("rejects untrusted, extra-argument, and unopened-handle calls before service", async () => {
        const harness = createIpcHarness();
        expect(
            await harness.invoke(
                IPC_CHANNELS.openAssetPreview,
                {
                    workspaceId: "workspace_01",
                    manifestRevision: revision,
                    entryId,
                },
                { trusted: false },
            ),
        ).toMatchObject({ ok: false, error: { code: "invalid_request" } });
        expect(
            await harness.invoke(
                IPC_CHANNELS.openAssetPreview,
                {
                    workspaceId: "workspace_01",
                    manifestRevision: revision,
                    entryId,
                },
                { extraArgument: true },
            ),
        ).toMatchObject({ ok: false, error: { code: "invalid_request" } });
        expect(
            await harness.invoke(IPC_CHANNELS.readAssetPreviewChunk, {
                handle,
                sequence: 0,
            }),
        ).toMatchObject({ ok: false, error: { code: "invalid_request" } });
        expect(harness.request).not.toHaveBeenCalled();
    });

    it("removes all preview handlers during teardown", () => {
        const harness = createIpcHarness();
        harness.dispose();
        expect(harness.removeHandler).toHaveBeenCalledWith(
            IPC_CHANNELS.openAssetPreview,
        );
        expect(harness.removeHandler).toHaveBeenCalledWith(
            IPC_CHANNELS.readAssetPreviewChunk,
        );
        expect(harness.removeHandler).toHaveBeenCalledWith(
            IPC_CHANNELS.closeAssetPreview,
        );
    });
});

describe("Studio named changeset IPC contracts", () => {
    it("accepts only closed portable stage, identity, and action inputs", () => {
        const baseSha256 = "a".repeat(64);
        expect(
            validateStageSourceDocumentArgument({
                workspaceId: "workspace_01",
                path: "source/lore/entry.md",
                baseSha256,
                content: "new\n",
            }),
        ).toEqual({
            workspaceId: "workspace_01",
            path: "source/lore/entry.md",
            baseSha256,
            content: "new\n",
        });
        expect(
            validateChangesetIdArgument({ changesetId: "changeset_01" }),
        ).toEqual({
            changesetId: "changeset_01",
        });
        expect(
            validateChangesetActionArgument({
                changesetId: "changeset_01",
                expectedReviewSha256: "b".repeat(64),
            }),
        ).toEqual({
            changesetId: "changeset_01",
            expectedReviewSha256: "b".repeat(64),
        });
        expect(
            validateChangesetActionArgument({ changesetId: "legacy_01" }),
        ).toEqual({
            changesetId: "legacy_01",
        });
    });

    it.each([
        {
            workspaceId: "workspace_01",
            path: "source/../entry.md",
            baseSha256: "a".repeat(64),
            content: "new\n",
        },
        {
            workspaceId: "workspace_01",
            path: "source/lore/entry.md",
            baseSha256: "A".repeat(64),
            content: "new\n",
        },
        {
            workspaceId: "workspace_01",
            path: "source/lore/entry.md",
            baseSha256: "a".repeat(64),
            content: "bad\ud800",
        },
        {
            workspaceId: "workspace_01",
            path: "source/lore/entry.md",
            baseSha256: "a".repeat(64),
            content: "x",
            operation: "delete",
        },
    ])("rejects malformed or capability-shaped stage input %#", (value) => {
        expect(() => validateStageSourceDocumentArgument(value)).toThrow();
    });

    it("enforces the exact UTF-8 byte ceiling for staged text", () => {
        expect(() =>
            validateStageSourceDocumentArgument({
                workspaceId: "workspace_01",
                path: "source/lore/entry.md",
                baseSha256: "a".repeat(64),
                content: "é".repeat(128 * 1024 + 1),
            }),
        ).toThrow();
    });

    it.each([
        [validateChangesetIdArgument, { changesetId: "../bad" }],
        [
            validateChangesetIdArgument,
            { changesetId: "changeset_01", operation: "apply" },
        ],
        [
            validateChangesetActionArgument,
            { changesetId: "changeset_01", expectedReviewSha256: null },
        ],
        [
            validateChangesetActionArgument,
            {
                changesetId: "changeset_01",
                expectedReviewSha256: "A".repeat(64),
            },
        ],
    ])("rejects malformed changeset identity input %#", (validate, value) => {
        expect(() => validate(value)).toThrow();
    });

    it("maps six review controls to fixed methods and one replace operation", async () => {
        const harness = createIpcHarness();
        const baseSha256 = "a".repeat(64);
        const calls = [
            [
                IPC_CHANNELS.stageSourceDocument,
                {
                    workspaceId: "workspace_01",
                    path: "source/lore/entry.md",
                    baseSha256,
                    content: "new\n",
                },
                "changeset.create",
                {
                    workspace_id: "workspace_01",
                    operations: [
                        {
                            path: "source/lore/entry.md",
                            operation: "replace",
                            expected_base_sha256: baseSha256,
                            content: "new\n",
                        },
                    ],
                },
            ],
            [
                IPC_CHANNELS.getChangeset,
                { changesetId: "changeset_01" },
                "changeset.get",
                { changeset_id: "changeset_01" },
            ],
            [
                IPC_CHANNELS.readChangesetDiff,
                { changesetId: "changeset_01" },
                "changeset.diff",
                { changeset_id: "changeset_01" },
            ],
            [
                IPC_CHANNELS.approveChangeset,
                {
                    changesetId: "changeset_01",
                    expectedReviewSha256: "b".repeat(64),
                },
                "changeset.approve",
                {
                    changeset_id: "changeset_01",
                    expected_review_sha256: "b".repeat(64),
                },
            ],
            [
                IPC_CHANNELS.rejectChangeset,
                { changesetId: "legacy_01" },
                "changeset.reject",
                { changeset_id: "legacy_01" },
            ],
            [
                IPC_CHANNELS.applyChangeset,
                {
                    changesetId: "changeset_01",
                    expectedReviewSha256: "b".repeat(64),
                },
                "changeset.apply",
                {
                    changeset_id: "changeset_01",
                    expected_review_sha256: "b".repeat(64),
                },
            ],
        ] as const;
        for (const [channel, argument] of calls) {
            expect(await harness.invoke(channel, argument)).toMatchObject({
                ok: true,
            });
        }
        expect(
            harness.request.mock.calls.map((call) => [call[1], call[2]]),
        ).toEqual(calls.map(([, , method, params]) => [method, params]));
    });

    it("rejects untrusted callers and extra arguments before changeset requests", async () => {
        const harness = createIpcHarness();
        expect(
            await harness.invoke(
                IPC_CHANNELS.stageSourceDocument,
                {
                    workspaceId: "workspace_01",
                    path: "source/lore/entry.md",
                    baseSha256: "a".repeat(64),
                    content: "new\n",
                },
                { trusted: false },
            ),
        ).toMatchObject({ ok: false, error: { code: "invalid_request" } });
        expect(
            await harness.invoke(
                IPC_CHANNELS.getChangeset,
                { changesetId: "changeset_01" },
                { extraArgument: true },
            ),
        ).toMatchObject({ ok: false, error: { code: "invalid_request" } });
        expect(harness.request).not.toHaveBeenCalled();
    });

    it("correlates one staged replacement through its full v2 review identity", async () => {
        const harness = createIpcHarness();
        const baseSha256 = "a".repeat(64);
        const content = "new\n";
        harness.request.mockImplementationOnce((requestId: string) =>
            Promise.resolve(
                createChangesetResponse(
                    requestId,
                    "changeset.create",
                    createV2Changeset({ baseSha256, content }),
                ),
            ),
        );
        expect(
            await harness.invoke(IPC_CHANNELS.stageSourceDocument, {
                workspaceId: "workspace_01",
                path: "source/lore/entry.md",
                baseSha256,
                content,
            }),
        ).toMatchObject({
            ok: true,
            value: { result: { changeset: { format_version: 2 } } },
        });

        for (const mutation of [
            { workspace_id: "workspace_02" },
            { status: "approved" },
            { format_version: 1, review_sha256: undefined },
            { review_sha256: "0".repeat(64) },
            {
                operations: [
                    {
                        ...createV2Changeset({ baseSha256, content })
                            .operations[0],
                        operation: "delete",
                    },
                ],
            },
        ]) {
            const record = {
                ...createV2Changeset({ baseSha256, content }),
                ...mutation,
            };
            if (mutation.review_sha256 === undefined)
                delete record.review_sha256;
            harness.request.mockImplementationOnce((requestId: string) =>
                Promise.resolve(
                    createChangesetResponse(
                        requestId,
                        "changeset.create",
                        record,
                    ),
                ),
            );
            expect(
                await harness.invoke(IPC_CHANNELS.stageSourceDocument, {
                    workspaceId: "workspace_01",
                    path: "source/lore/entry.md",
                    baseSha256,
                    content,
                }),
            ).toMatchObject({
                ok: false,
                error: { code: "service_unavailable" },
            });
        }
    });

    it("correlates get, diff, and exact action status for v1 and v2 replies", async () => {
        const harness = createIpcHarness();
        const v2 = createV2Changeset({
            baseSha256: "a".repeat(64),
            content: "new\n",
        });
        harness.request.mockImplementationOnce((requestId: string) =>
            Promise.resolve(
                createChangesetResponse(requestId, "changeset.get", v2),
            ),
        );
        expect(
            await harness.invoke(IPC_CHANNELS.getChangeset, {
                changesetId: "changeset_01",
            }),
        ).toMatchObject({ ok: true });

        harness.request.mockImplementationOnce((requestId: string) =>
            Promise.resolve(createDiffResponse(requestId, v2)),
        );
        expect(
            await harness.invoke(IPC_CHANNELS.readChangesetDiff, {
                changesetId: "changeset_01",
            }),
        ).toMatchObject({ ok: true });

        for (const [channel, method, status] of [
            [IPC_CHANNELS.approveChangeset, "changeset.approve", "approved"],
            [IPC_CHANNELS.rejectChangeset, "changeset.reject", "rejected"],
            [IPC_CHANNELS.applyChangeset, "changeset.apply", "applied"],
        ] as const) {
            harness.request.mockImplementationOnce((requestId: string) =>
                Promise.resolve(
                    createChangesetResponse(requestId, method, {
                        ...v2,
                        status,
                    }),
                ),
            );
            expect(
                await harness.invoke(channel, {
                    changesetId: "changeset_01",
                    expectedReviewSha256: v2.review_sha256,
                }),
            ).toMatchObject({ ok: true });
        }

        const legacy = createV1Changeset();
        harness.request.mockImplementationOnce((requestId: string) =>
            Promise.resolve(
                createChangesetResponse(requestId, "changeset.reject", {
                    ...legacy,
                    status: "rejected",
                }),
            ),
        );
        expect(
            await harness.invoke(IPC_CHANNELS.rejectChangeset, {
                changesetId: "legacy_01",
            }),
        ).toMatchObject({ ok: true });
    });

    it("rejects mismatched IDs, review hashes, statuses, methods, and diff operations", async () => {
        const harness = createIpcHarness();
        const v2 = createV2Changeset({
            baseSha256: "a".repeat(64),
            content: "new\n",
        });
        const cases = [
            {
                channel: IPC_CHANNELS.getChangeset,
                argument: { changesetId: "changeset_01" },
                reply: (requestId: string) =>
                    createChangesetResponse(requestId, "changeset.get", {
                        ...v2,
                        changeset_id: "changeset_02",
                    }),
            },
            {
                channel: IPC_CHANNELS.readChangesetDiff,
                argument: { changesetId: "changeset_01" },
                reply: (requestId: string) => ({
                    ...createDiffResponse(requestId, v2),
                    result: {
                        diff: {
                            ...createDiffResponse(requestId, v2).result.diff,
                            operations: [
                                {
                                    ...createDiffResponse(requestId, v2).result
                                        .diff.operations[0],
                                    operation: "execute",
                                },
                            ],
                        },
                    },
                }),
            },
            {
                channel: IPC_CHANNELS.approveChangeset,
                argument: {
                    changesetId: "changeset_01",
                    expectedReviewSha256: v2.review_sha256,
                },
                reply: (requestId: string) =>
                    createChangesetResponse(requestId, "changeset.approve", {
                        ...v2,
                        status: "rejected",
                    }),
            },
            {
                channel: IPC_CHANNELS.applyChangeset,
                argument: {
                    changesetId: "changeset_01",
                    expectedReviewSha256: "0".repeat(64),
                },
                reply: (requestId: string) =>
                    createChangesetResponse(requestId, "changeset.apply", {
                        ...v2,
                        status: "applied",
                    }),
            },
            {
                channel: IPC_CHANNELS.approveChangeset,
                argument: { changesetId: "changeset_01" },
                reply: (requestId: string) =>
                    createChangesetResponse(requestId, "changeset.approve", {
                        ...v2,
                        status: "approved",
                    }),
            },
            {
                channel: IPC_CHANNELS.rejectChangeset,
                argument: {
                    changesetId: "legacy_01",
                    expectedReviewSha256: v2.review_sha256,
                },
                reply: (requestId: string) =>
                    createChangesetResponse(requestId, "changeset.reject", {
                        ...createV1Changeset(),
                        status: "rejected",
                    }),
            },
            {
                channel: IPC_CHANNELS.rejectChangeset,
                argument: {
                    changesetId: "changeset_01",
                    expectedReviewSha256: v2.review_sha256,
                },
                reply: (requestId: string) =>
                    createChangesetResponse(requestId, "changeset.approve", {
                        ...v2,
                        status: "rejected",
                    }),
            },
        ];
        for (const testCase of cases) {
            harness.request.mockImplementationOnce((requestId: string) =>
                Promise.resolve(testCase.reply(requestId)),
            );
            expect(
                await harness.invoke(testCase.channel, testCase.argument),
            ).toMatchObject({
                ok: false,
                error: { code: "service_unavailable" },
            });
        }
    });
});

describe("Studio named authoring and job IPC routing", () => {
    it("owns request IDs and maps every capability to a fixed method and operation", async () => {
        const harness = createIpcHarness();
        const cases: Array<[string, unknown, string, Record<string, unknown>]> =
            [
                [
                    IPC_CHANNELS.getWorkspaceOverview,
                    { workspaceId: "workspace_01" },
                    "workspace.overview",
                    { workspace_id: "workspace_01" },
                ],
                [
                    IPC_CHANNELS.listSourceDocuments,
                    { workspaceId: "workspace_01" },
                    "source.list",
                    { workspace_id: "workspace_01" },
                ],
                [
                    IPC_CHANNELS.readSourceDocument,
                    { workspaceId: "workspace_01", path: "source/world.json" },
                    "source.read",
                    { workspace_id: "workspace_01", path: "source/world.json" },
                ],
                [
                    IPC_CHANNELS.validateWorld,
                    { workspaceId: "workspace_01" },
                    "world.validate",
                    { workspace_id: "workspace_01" },
                ],
                [
                    IPC_CHANNELS.analyzeWorld,
                    { workspaceId: "workspace_01" },
                    "world.analyze",
                    { workspace_id: "workspace_01" },
                ],
                [
                    IPC_CHANNELS.validateAssetReceipt,
                    {
                        workspaceId: "workspace_01",
                        input: { receipt: "receipts/item.json" },
                    },
                    "job.create",
                    {
                        workspace_id: "workspace_01",
                        operation: "asset.receipt.validate",
                        input: { receipt: "receipts/item.json" },
                    },
                ],
                [
                    IPC_CHANNELS.verifyAssetpack,
                    {
                        workspaceId: "workspace_01",
                        input: {
                            assetpack: "build/assets.json",
                            worldpack: "build/world.json",
                        },
                    },
                    "job.create",
                    {
                        workspace_id: "workspace_01",
                        operation: "assetpack.verify",
                        input: {
                            assetpack: "build/assets.json",
                            worldpack: "build/world.json",
                        },
                    },
                ],
                [
                    IPC_CHANNELS.runHeadless,
                    {
                        workspaceId: "workspace_01",
                        input: { worldpack: "build/world.json", ticks: 0 },
                    },
                    "job.create",
                    {
                        workspace_id: "workspace_01",
                        operation: "runtime.headless",
                        input: { worldpack: "build/world.json", ticks: 0 },
                    },
                ],
                [
                    IPC_CHANNELS.runReplay,
                    {
                        workspaceId: "workspace_01",
                        input: {
                            worldpack: "build/world.json",
                            replay: "replays/slot.json",
                        },
                    },
                    "job.create",
                    {
                        workspace_id: "workspace_01",
                        operation: "runtime.replay",
                        input: {
                            worldpack: "build/world.json",
                            replay: "replays/slot.json",
                        },
                    },
                ],
                [
                    IPC_CHANNELS.cancelJob,
                    { jobId: "job_01" },
                    "job.cancel",
                    { job_id: "job_01" },
                ],
            ];

        for (const [channel, argument] of cases) {
            const result = await harness.invoke(channel, argument);
            expect(result).toMatchObject({ ok: true });
        }
        const calls = harness.request.mock.calls;
        expect(calls).toHaveLength(cases.length);
        expect(calls.map((call) => [call[1], call[2]])).toEqual(
            cases.map(([, , method, params]) => [method, params]),
        );
        const requestIds = calls.map((call) => call[0]);
        expect(new Set(requestIds).size).toBe(requestIds.length);
        expect(
            requestIds.every((value) => /^[0-9a-f-]{36}$/u.test(value)),
        ).toBe(true);
    });

    it("accepts exact operation-specific v2 job.create replies for all four capabilities", async () => {
        const harness = createIpcHarness();
        for (const testCase of jobCapabilityCases()) {
            harness.request.mockImplementationOnce((requestId: string) =>
                Promise.resolve(
                    createManagedJobResponse(
                        requestId,
                        testCase.operation,
                        testCase.input,
                    ),
                ),
            );
            expect(
                await harness.invoke(testCase.channel, testCase.argument),
            ).toMatchObject({
                ok: true,
                value: {
                    kind: "response",
                    method: "job.create",
                    result: {
                        job: {
                            format_version: 2,
                            operation: testCase.operation,
                        },
                    },
                },
            });
        }
    });

    it("rejects cross-operation job.create replies for all four capabilities", async () => {
        const harness = createIpcHarness();
        const cases = jobCapabilityCases();
        for (const [index, testCase] of cases.entries()) {
            const other = cases[(index + 1) % cases.length];
            harness.request.mockImplementationOnce((requestId: string) =>
                Promise.resolve(
                    createManagedJobResponse(
                        requestId,
                        other.operation,
                        other.input,
                    ),
                ),
            );
            expect(
                await harness.invoke(testCase.channel, testCase.argument),
            ).toMatchObject({
                ok: false,
                error: { code: "service_unavailable" },
            });
        }
    });

    it("rejects cross-workspace same-operation job.create replies without exposing a job", async () => {
        const harness = createIpcHarness();
        for (const testCase of jobCapabilityCases()) {
            harness.request.mockImplementationOnce((requestId: string) =>
                Promise.resolve(
                    createManagedJobResponse(
                        requestId,
                        testCase.operation,
                        testCase.input,
                        "workspace_02",
                    ),
                ),
            );
            const result = await harness.invoke(
                testCase.channel,
                testCase.argument,
            );
            expect(result).toMatchObject({
                ok: false,
                error: { code: "service_unavailable" },
            });
            expect(result).not.toHaveProperty("value");
            expect(JSON.stringify(result)).not.toContain('"job"');
        }
    });

    it("rejects same-operation replies whose inputs do not match the requested job", async () => {
        const harness = createIpcHarness();
        const mismatchedInputs = [
            { receipt: "receipts/other.json" },
            {
                assetpack: "build/other-assets.json",
                worldpack: "build/world.json",
            },
            { worldpack: "build/other-world.json", ticks: 0 },
            { worldpack: "build/world.json", replay: "replays/other.json" },
        ];
        for (const [index, testCase] of jobCapabilityCases().entries()) {
            harness.request.mockImplementationOnce((requestId: string) =>
                Promise.resolve(
                    createManagedJobResponse(
                        requestId,
                        testCase.operation,
                        mismatchedInputs[index],
                    ),
                ),
            );
            expect(
                await harness.invoke(testCase.channel, testCase.argument),
            ).toMatchObject({
                ok: false,
                error: { code: "service_unavailable" },
            });
        }
    });

    it("rejects untrusted, extra-argument, mismatched, and malformed replies", async () => {
        const harness = createIpcHarness();
        expect(
            await harness.invoke(
                IPC_CHANNELS.runHeadless,
                {
                    workspaceId: "workspace_01",
                    input: { worldpack: "world.json", ticks: 0 },
                },
                { trusted: false },
            ),
        ).toMatchObject({ ok: false, error: { code: "invalid_request" } });
        expect(harness.request).not.toHaveBeenCalled();

        expect(
            await harness.invoke(
                IPC_CHANNELS.validateWorld,
                { workspaceId: "workspace_01" },
                { extraArgument: true },
            ),
        ).toMatchObject({ ok: false, error: { code: "invalid_request" } });
        expect(harness.request).not.toHaveBeenCalled();

        harness.request.mockImplementationOnce((requestId: string) =>
            Promise.resolve({
                protocol: "rpg-world-forge.studio_protocol",
                protocol_version: 1,
                kind: "response",
                request_id: requestId,
                method: "world.validate",
                result: {
                    validation: {
                        valid: true,
                        profile: "release",
                        world_id: "world_01",
                        object_count: 0,
                        diagnostics: [],
                        diagnostics_truncated: false,
                    },
                },
            }),
        );
        expect(
            await harness.invoke(IPC_CHANNELS.analyzeWorld, {
                workspaceId: "workspace_01",
            }),
        ).toMatchObject({ ok: false, error: { code: "service_unavailable" } });

        harness.request.mockImplementationOnce(() =>
            Promise.resolve({
                protocol: "rpg-world-forge.studio_protocol",
                protocol_version: 1,
                kind: "error",
                request_id: "wrong-request-id",
                error: { code: "not_found", message: "fixture", details: {} },
            }),
        );
        expect(
            await harness.invoke(IPC_CHANNELS.validateWorld, {
                workspaceId: "workspace_01",
            }),
        ).toMatchObject({ ok: false, error: { code: "service_unavailable" } });

        harness.request.mockImplementationOnce((requestId: string) =>
            Promise.resolve({
                protocol: "rpg-world-forge.studio_protocol",
                protocol_version: 1,
                kind: "response",
                request_id: requestId,
                method: "world.validate",
                result: {},
            }),
        );
        expect(
            await harness.invoke(IPC_CHANNELS.validateWorld, {
                workspaceId: "workspace_01",
            }),
        ).toMatchObject({ ok: false, error: { code: "service_unavailable" } });
    });
});

describe("Studio external artifact IPC authority", () => {
    const hash = "a".repeat(64);

    it("accepts only closed pathless grant and external-job arguments", () => {
        expect(
            validateExternalGrantCreateArgument({
                workspaceId: "workspace_01",
                operation: "game.materialize",
                role: "source",
                artifactKind: "game_materialization_bundle",
                expectedContentHash: hash,
            }),
        ).toMatchObject({
            workspaceId: "workspace_01",
            operation: "game.materialize",
            role: "source",
            artifactKind: "game_materialization_bundle",
        });
        expect(() =>
            validateExternalGrantCreateArgument({
                workspaceId: "workspace_01",
                operation: "game.materialize",
                role: "source",
                artifactKind: "game_materialization_bundle",
                expectedContentHash: hash,
                path: "/renderer/authority",
            }),
        ).toThrow();
        expect(() =>
            validateExternalGrantCreateArgument({
                workspaceId: "workspace_01",
                operation: "game.materialize",
                role: "source",
                artifactKind: "game_package",
                expectedContentHash: hash,
            }),
        ).toThrow();

        expect(
            validateMaterializeGameArgument({
                workspaceId: "workspace_01",
                sourceGrantId: "grant_source",
                targetGrantId: "grant_target",
                expectedMaterializationHash: hash,
            }),
        ).toMatchObject({ workspaceId: "workspace_01" });
        expect(
            validatePackageGameArgument({
                workspaceId: "workspace_01",
                sourceGrantId: "grant_source",
                targetGrantId: "grant_target",
                expectedGameHash: hash,
            }),
        ).toMatchObject({ workspaceId: "workspace_01" });
        expect(
            validateExtractGamePackageArgument({
                workspaceId: "workspace_01",
                sourceGrantId: "grant_source",
                targetGrantId: "grant_target",
                expectedPackageHash: hash,
            }),
        ).toMatchObject({ workspaceId: "workspace_01" });
        expect(validateExternalJobIdArgument({ jobId: "job_01" })).toEqual({
            jobId: "job_01",
        });
        expect(
            validateExternalJobsListParams({
                workspaceId: "workspace_01",
                state: "orphaned",
                limit: 10,
            }),
        ).toEqual({
            workspace_id: "workspace_01",
            state: "orphaned",
            limit: 10,
        });
        expect(
            validateExternalJobRecoveryArgument({
                jobId: "job_01",
                action: "rollback",
            }),
        ).toEqual({ jobId: "job_01", action: "rollback" });
    });

    it("owns all native path selection and sends only private v2 grant requests", async () => {
        const harness = createIpcHarness();
        const cases = [
            {
                operation: "game.materialize",
                role: "source",
                artifactKind: "game_materialization_bundle",
                expectedContentHash: hash,
            },
            {
                operation: "game.materialize",
                role: "target",
                artifactKind: "standalone_game",
                expectedContentHash: null,
            },
            {
                operation: "game.package",
                role: "source",
                artifactKind: "standalone_game",
                expectedContentHash: hash,
            },
            {
                operation: "game.package",
                role: "target",
                artifactKind: "game_package",
                expectedContentHash: null,
            },
            {
                operation: "game.package.extract",
                role: "source",
                artifactKind: "game_package",
                expectedContentHash: hash,
            },
            {
                operation: "game.package.extract",
                role: "target",
                artifactKind: "standalone_game",
                expectedContentHash: null,
            },
        ] as const;

        const results: unknown[] = [];
        for (const value of cases) {
            results.push(
                await harness.invoke(IPC_CHANNELS.createExternalGrant, {
                    workspaceId: "workspace_01",
                    ...value,
                }),
            );
        }

        expect(harness.showOpenDialog).toHaveBeenCalledTimes(3);
        expect(
            harness.showOpenDialog.mock.calls.map((call) => call[1].properties),
        ).toEqual([["openDirectory"], ["openDirectory"], ["openFile"]]);
        expect(harness.showSaveDialog).toHaveBeenCalledTimes(3);
        expect(harness.request).toHaveBeenCalledTimes(6);
        expect(
            harness.request.mock.calls.map((call) => [
                call[1],
                call[2].operation,
                call[2].role,
                call[2].artifact_kind,
                call[4],
            ]),
        ).toEqual(
            cases.map((value) => [
                "external_grant.create",
                value.operation,
                value.role,
                value.artifactKind,
                2,
            ]),
        );
        expect(
            harness.request.mock.calls.every((call) => "path" in call[2]),
        ).toBe(true);
        expect(
            harness.request.mock.calls.every(
                (call) => !("grant_id" in call[2]),
            ),
        ).toBe(true);
        expect(JSON.stringify(results)).not.toContain("/selected/");
    });

    it("keeps cancellation pathless and never contacts the service", async () => {
        const harness = createIpcHarness();
        harness.showOpenDialog.mockResolvedValueOnce({
            canceled: true,
            filePaths: [],
        });

        const result = await harness.invoke(IPC_CHANNELS.createExternalGrant, {
            workspaceId: "workspace_01",
            operation: "game.package.extract",
            role: "source",
            artifactKind: "game_package",
            expectedContentHash: hash,
        });

        expect(result).toEqual({
            ok: false,
            error: {
                code: "cancelled",
                message: "External artifact selection was cancelled",
            },
        });
        expect(harness.request).not.toHaveBeenCalled();
    });

    it("maps external job lifecycle methods only through protocol v2", async () => {
        const harness = createIpcHarness();
        await harness.invoke(IPC_CHANNELS.materializeGame, {
            workspaceId: "workspace_01",
            sourceGrantId: "grant_source",
            targetGrantId: "grant_target",
            expectedMaterializationHash: hash,
        });
        await harness.invoke(IPC_CHANNELS.packageGame, {
            workspaceId: "workspace_01",
            sourceGrantId: "grant_source",
            targetGrantId: "grant_target",
            expectedGameHash: hash,
        });
        await harness.invoke(IPC_CHANNELS.extractGamePackage, {
            workspaceId: "workspace_01",
            sourceGrantId: "grant_source",
            targetGrantId: "grant_target",
            expectedPackageHash: hash,
        });
        await harness.invoke(IPC_CHANNELS.getExternalGrant, {
            grantId: "grant_source",
        });
        await harness.invoke(IPC_CHANNELS.revokeExternalGrant, {
            grantId: "grant_source",
        });
        await harness.invoke(IPC_CHANNELS.getExternalJob, { jobId: "job_01" });
        await harness.invoke(IPC_CHANNELS.listExternalJobs, {
            workspaceId: "workspace_01",
            state: "queued",
            limit: 10,
        });
        await harness.invoke(IPC_CHANNELS.cancelExternalJob, {
            jobId: "job_01",
        });
        await harness.invoke(IPC_CHANNELS.recoverExternalJob, {
            jobId: "job_01",
            action: "resume",
        });

        expect(harness.request.mock.calls.every((call) => call[4] === 2)).toBe(
            true,
        );
        expect(harness.request.mock.calls.map((call) => call[1])).toEqual([
            "job.create",
            "job.create",
            "job.create",
            "external_grant.get",
            "external_grant.revoke",
            "job.get",
            "job.list",
            "job.cancel",
            "job.recover",
        ]);
        expect(
            harness.request.mock.calls.slice(0, 3).map((call) => call[2]),
        ).toEqual([
            {
                workspace_id: "workspace_01",
                operation: "game.materialize",
                input: {
                    source_grant_id: "grant_source",
                    target_grant_id: "grant_target",
                    expected_materialization_hash: hash,
                },
            },
            {
                workspace_id: "workspace_01",
                operation: "game.package",
                input: {
                    source_grant_id: "grant_source",
                    target_grant_id: "grant_target",
                    expected_game_hash: hash,
                },
            },
            {
                workspace_id: "workspace_01",
                operation: "game.package.extract",
                input: {
                    source_grant_id: "grant_source",
                    target_grant_id: "grant_target",
                    expected_package_hash: hash,
                },
            },
        ]);
    });
});

describe("Studio named read-only IPC filters", () => {
    it("accepts closed, bounded filters for each list operation", () => {
        expect(
            validateEventsListParams({
                workspace_id: "workspace_01",
                after_id: 0,
                limit: 1_000,
            }),
        ).toEqual({ workspace_id: "workspace_01", after_id: 0, limit: 1_000 });
        expect(
            validateChangesetsListParams({
                workspace_id: "workspace_01",
                status: "approved",
                limit: 100,
            }),
        ).toEqual({
            workspace_id: "workspace_01",
            status: "approved",
            limit: 100,
        });
        expect(
            validateJobsListParams({
                workspace_id: "workspace_01",
                state: "awaiting_approval",
                limit: 1,
            }),
        ).toEqual({
            workspace_id: "workspace_01",
            state: "awaiting_approval",
            limit: 1,
        });
    });

    it.each([
        [validateEventsListParams, { method: "workspace.register" }],
        [validateEventsListParams, { workspace_id: "../bad" }],
        [validateEventsListParams, { after_id: -1 }],
        [validateEventsListParams, { after_id: true }],
        [validateEventsListParams, { limit: 0 }],
        [validateChangesetsListParams, { status: "created" }],
        [validateChangesetsListParams, { limit: 1_001 }],
        [validateJobsListParams, { state: "approved" }],
        [validateJobsListParams, []],
        [validateJobsListParams, { state: "queued", command: "shell.exec" }],
    ])(
        "rejects malformed, unknown, or mutation-shaped filters %#",
        (validate, value) => {
            expect(() => validate(value)).toThrow();
        },
    );
});

describe("Codex named IPC contracts", () => {
    it("accepts only closed bounded values", () => {
        expect(
            validateWorkspaceArgument({ workspaceId: "workspace_01" }),
        ).toEqual({ workspaceId: "workspace_01" });
        expect(validateLoginArgument({ mode: "device-code" })).toEqual({
            mode: "device-code",
        });
        expect(
            validateStartTurnArgument({ threadId: "thread-1", text: "hello" }),
        ).toEqual({ threadId: "thread-1", text: "hello" });
        expect(
            validateInterruptTurnArgument({
                threadId: "thread-1",
                turnId: "turn-1",
            }),
        ).toEqual({ threadId: "thread-1", turnId: "turn-1" });
        expect(
            validateUserInputArgument({
                token: "00000000-0000-4000-8000-000000000000",
                answers: { choice: ["North"] },
            }),
        ).toEqual({
            token: "00000000-0000-4000-8000-000000000000",
            answers: { choice: ["North"] },
        });
    });

    it.each([
        [validateWorkspaceArgument, { workspaceId: "../bad" }],
        [validateLoginArgument, { mode: "api-key" }],
        [
            validateStartTurnArgument,
            { threadId: "thread-1", text: "ok", command: "shell" },
        ],
        [validateStartTurnArgument, { threadId: "../bad", text: "ok" }],
        [validateInterruptTurnArgument, { threadId: "thread-1" }],
        [
            validateUserInputArgument,
            { token: "bad", answers: { choice: ["x"] } },
        ],
    ])("rejects malformed or capability-shaped input %#", (validate, value) => {
        expect(() => validate(value)).toThrow();
    });
});

function createAssetPreviewOpenResponse(
    requestId: string,
    handle: string,
    manifestRevision: string,
    entryId: string,
    mediaType: "image/png" | "audio/wav",
    payload: Uint8Array,
) {
    return {
        protocol: "rpg-world-forge.studio_protocol",
        protocol_version: 1,
        kind: "response",
        request_id: requestId,
        method: "asset.preview.open",
        result: {
            handle,
            manifest_revision: manifestRevision,
            entry_id: entryId,
            media_type: mediaType,
            byte_length: payload.byteLength,
            sha256: createHash("sha256").update(payload).digest("hex"),
            chunk_bytes: 65_536,
        },
    };
}

function createAssetPreviewReadResponse(
    requestId: string,
    handle: string,
    sequence: number,
    payload: Uint8Array,
    cumulativePayload: Uint8Array,
    eof: boolean,
) {
    return {
        protocol: "rpg-world-forge.studio_protocol",
        protocol_version: 1,
        kind: "response",
        request_id: requestId,
        method: "asset.preview.read",
        result: {
            handle,
            sequence,
            data_base64: Buffer.from(payload).toString("base64"),
            byte_length: payload.byteLength,
            cumulative_bytes: cumulativePayload.byteLength,
            cumulative_sha256: createHash("sha256")
                .update(cumulativePayload)
                .digest("hex"),
            eof,
        },
    };
}

function createAssetPreviewCloseResponse(requestId: string, handle: string) {
    return {
        protocol: "rpg-world-forge.studio_protocol",
        protocol_version: 1,
        kind: "response",
        request_id: requestId,
        method: "asset.preview.close",
        result: { handle, closed: true },
    };
}

type ProjectSelectionFixture = (rootPath: string) => Promise<{
    contentHash: string;
    displayName: string;
}>;

interface AuthorityModalTestPayload {
    nonce: string;
    title: string;
    preview: {
        artifactId: string;
        subject: {
            format: string;
            formatVersion: number;
            id: string;
            contentHash: string;
        };
        mediaType: "image/png" | "audio/wav" | "text/plain";
        data: Uint8Array;
        sha256: string;
        byteLength: number;
    };
    criteria: readonly string[];
}

function createIpcHarness({
    projectSelection = vi.fn<ProjectSelectionFixture>().mockResolvedValue({
        contentHash: "a".repeat(64),
        displayName: "Selected creation project",
    }),
    authorityModal,
}: {
    projectSelection?: ProjectSelectionFixture;
    authorityModal?: {
        requestReview: (
            window: unknown,
            payload: AuthorityModalTestPayload,
        ) => Promise<{
            nonce: string;
            action: "approve" | "reject" | "cancel";
            criterionDecisions: ("approved" | "rejected")[];
        }>;
    };
} = {}) {
    const handlers = new Map<
        string,
        (event: unknown, ...args: unknown[]) => unknown
    >();
    const ipcMain = {
        handle: vi.fn(
            (
                channel: string,
                handler: (event: unknown, ...args: unknown[]) => unknown,
            ) => {
                handlers.set(channel, handler);
            },
        ),
        removeHandler: vi.fn((channel: string) => handlers.delete(channel)),
    };
    const mainFrame = { url: "rwf-studio://app/index.html" };
    const webContents = {
        mainFrame,
        isDestroyed: () => false,
        send: vi.fn(),
    };
    const window = { webContents, isDestroyed: () => false };
    type RequestFixture = (
        requestId: string,
        method: string,
        params: Record<string, unknown>,
        timeoutMs: number,
        protocolVersion?: number,
    ) => Promise<unknown>;
    const request = vi.fn<RequestFixture>(
        (
            requestId: string,
            method: string,
            params: Record<string, unknown>,
            timeoutMs: number,
            protocolVersion = 1,
        ): Promise<unknown> => {
            void method;
            void params;
            void timeoutMs;
            return Promise.resolve({
                protocol: "rpg-world-forge.studio_protocol",
                protocol_version: protocolVersion,
                kind: "error",
                request_id: requestId,
                error: { code: "not_found", message: "fixture", details: {} },
            });
        },
    );
    type OpenDialogFixture = (
        window: unknown,
        options: { properties?: string[] },
    ) => Promise<{ canceled: boolean; filePaths: string[] }>;
    const showOpenDialog = vi.fn<OpenDialogFixture>(
        (
            _window: unknown,
            options: { properties?: string[] },
        ): Promise<{ canceled: boolean; filePaths: string[] }> =>
            Promise.resolve({
                canceled: false,
                filePaths: [
                    options.properties?.includes("openFile")
                        ? "/selected/source.wfgame"
                        : "/selected/source-directory",
                ],
            }),
    );
    let saved = 0;
    type SaveDialogFixture = () => Promise<{
        canceled: boolean;
        filePath: string;
    }>;
    const showSaveDialog = vi.fn<SaveDialogFixture>(
        (): Promise<{ canceled: boolean; filePath: string }> => {
            saved += 1;
            return Promise.resolve({
                canceled: false,
                filePath: `/selected/target-${String(saved)}`,
            });
        },
    );
    const service = {
        status: { state: "ready", message: "ready", pid: 1 },
        subscribe: () => () => undefined,
        initialize: vi.fn(),
        request,
        getWorkspace: vi.fn(),
        stop: vi.fn(),
    };
    const codex = {
        status: {
            state: "unbound",
            message: "unbound",
            pid: null,
            workspaceId: null,
        },
        subscribe: () => () => undefined,
    };
    const dispose = registerStudioIpc(
        ipcMain as never,
        window as never,
        service as never,
        codex as never,
        { showOpenDialog, showSaveDialog },
        { readProjectIdentity: projectSelection },
        authorityModal === undefined ? {} : { authorityModal },
    );

    return {
        dispose,
        removeHandler: ipcMain.removeHandler,
        request,
        showOpenDialog,
        showSaveDialog,
        projectSelection,
        async invokeNoArgs(
            channel: string,
            options: { trusted?: boolean } = {},
        ): Promise<unknown> {
            const handler = handlers.get(channel);
            if (!handler)
                throw new Error(`Missing fixture handler for ${channel}`);
            const trusted = options.trusted ?? true;
            const event = trusted
                ? { sender: webContents, senderFrame: mainFrame }
                : { sender: {}, senderFrame: mainFrame };
            return await handler(event);
        },
        async invoke(
            channel: string,
            argument: unknown,
            options: { trusted?: boolean; extraArgument?: boolean } = {},
        ): Promise<unknown> {
            const handler = handlers.get(channel);
            if (!handler)
                throw new Error(`Missing fixture handler for ${channel}`);
            const trusted = options.trusted ?? true;
            const event = trusted
                ? { sender: webContents, senderFrame: mainFrame }
                : { sender: {}, senderFrame: mainFrame };
            const args = options.extraArgument
                ? [argument, { forbidden: true }]
                : [argument];
            return await handler(event, ...args);
        },
    };
}

function v3Response(
    method: string,
    result: Record<string, unknown>,
    requestId = "fixture-request",
) {
    return {
        protocol: "rpg-world-forge.studio_protocol",
        protocol_version: 3,
        kind: "response",
        request_id: requestId,
        method,
        result,
    };
}

function v5Response(
    requestId: string,
    method: string,
    result: Record<string, unknown>,
) {
    return {
        protocol: "rpg-world-forge.studio_protocol",
        protocol_version: 5,
        kind: "response",
        request_id: requestId,
        method,
        result,
    };
}

function creationGrant(
    role: "existing_root" | "new_target",
    projectHash: string | null,
) {
    return {
        format: "world-forge.studio_creation_root_grant",
        format_version: 1,
        grant_id: "grant_creation",
        role,
        display_name: "Neutral universe",
        state: role === "existing_root" ? "ready" : "reserved",
        expected_target_state:
            role === "existing_root" ? "existing_project" : "absent",
        expected_project:
            role === "existing_root"
                ? {
                      format: "world-forge.project",
                      format_version: 1,
                      id: "neutral_universe",
                      content_hash: projectHash,
                  }
                : null,
        generation: 0,
        created_at: "2026-08-01T00:00:00Z",
        updated_at: "2026-08-01T00:00:00Z",
    };
}

function creationWorkspace() {
    return {
        format: "world-forge.studio_creation_workspace",
        format_version: 1,
        workspace_id: "creation_workspace",
        project: {
            format: "world-forge.project",
            format_version: 1,
            id: "neutral_universe",
            content_hash: "d".repeat(64),
        },
        project_kind: "universe_library",
        source_revision: "e".repeat(64),
        workflow_status_hash: null,
        root_generation: 0,
        created_at: "2026-08-01T00:00:00Z",
        updated_at: "2026-08-01T00:00:00Z",
    };
}

const authorityArtifactHash = "c".repeat(64);

function serviceAuthority() {
    return {
        workspace_id: "creation_workspace",
        root_generation: 4,
        source_revision: "a".repeat(64),
        workflow_status_hash: "b".repeat(64),
    };
}

function artifactRecord(artifactId: string, format: string, id: string) {
    return {
        format: "world-forge.studio_creation_artifact",
        format_version: 1,
        artifact_id: artifactId,
        subject: {
            format,
            format_version: 1,
            id,
            content_hash: "f".repeat(64),
        },
        lifecycle: "candidate",
        roles: ["registered_artifact"],
        producer: {
            kind: "future_candidate",
            phase_id: null,
            reference_id: "job_fixture",
        },
        references: { dependency_count: 0, dependent_count: 0 },
        authority: serviceAuthority(),
        record_hash: "e".repeat(64),
    };
}

type AuthorityHeadlessProofDrift = Partial<{
    omitReleaseJob: boolean;
    releaseStatus: "authorized" | "blocked";
    releaseOperation: string;
    releaseFormatVersion: number;
    releaseState: string;
    releaseReasonCodes: string[];
    assetpackSubjectId: string;
    releaseAuthoritySubjectId: string;
    sourceGrantBundleId: string;
    sourceGrantArtifactId: string;
    duplicateLineageFormat: string;
    sourceGrantKind: string;
    sourceGrantState: string;
    sourceGrantVersion: number;
    targetGrantId: string;
    targetGrantKind: string;
    targetGrantVersion: number;
    scriptSnapshotHash: string;
    scriptWorkspaceId: string;
}>;

function queueAuthorityHeadlessProof(
    request: ReturnType<typeof createIpcHarness>["request"],
    drift: AuthorityHeadlessProofDrift = {},
) {
    const artifactSubjects: Record<string, [string, string, string]> = {
        artifact_script_01: ["world-forge.game_execution_script", "script_01", "1".repeat(64)],
        artifact_gamepack: ["world-forge.gamepack", "gamepack_01", "2".repeat(64)],
        artifact_inventory: ["world-forge.asset_inventory", "inventory_01", "3".repeat(64)],
        artifact_manifest: ["world-forge.asset_manifest", "manifest_01", "4".repeat(64)],
        artifact_assetpack: [
            "world-forge.assetpack",
            drift.assetpackSubjectId ?? "assetpack_01",
            "5".repeat(64),
        ],
        artifact_release_authority: [
            "world-forge.asset_release_authority",
            drift.releaseAuthoritySubjectId ?? "release_authority_01",
            "6".repeat(64),
        ],
        artifact_snapshot: ["world-forge.game_runtime_snapshot", "snapshot_01", "7".repeat(64)],
        artifact_registry: ["world-forge.runtime_adapter_registry", "registry_01", "8".repeat(64)],
        artifact_composition: ["world-forge.game_runtime_composition", "composition_01", "9".repeat(64)],
        artifact_runtime_bundle: ["world-forge.game_runtime_bundle", "runtime_bundle_01", "a".repeat(64)],
    };
    request.mockImplementation((requestId, method, params) => {
        if (method === "creation_artifact.inspect") {
            const artifactId = String(params.artifact_id);
            const lineage = [
                { artifact_id: "artifact_script_01" },
                { artifact_id: "artifact_gamepack" },
                { artifact_id: "artifact_inventory" },
                { artifact_id: "artifact_manifest" },
                { artifact_id: "artifact_assetpack" },
                { artifact_id: "artifact_release_authority" },
                { artifact_id: "artifact_snapshot" },
                { artifact_id: "artifact_registry" },
                { artifact_id: "artifact_composition" },
            ];
            if (drift.duplicateLineageFormat === "world-forge.assetpack") {
                artifactSubjects.artifact_duplicate_assetpack = [
                    "world-forge.assetpack",
                    "assetpack_duplicate",
                    "b".repeat(64),
                ];
                lineage.push({ artifact_id: "artifact_duplicate_assetpack" });
            }
            const [format, id, contentHash] = artifactSubjects[artifactId] ?? [
                "world-forge.gamepack",
                "unknown_01",
                "0".repeat(64),
            ];
            const artifact = artifactRecord(artifactId, format, id);
            artifact.subject.content_hash = contentHash;
            artifact.producer.reference_id =
                artifactId === "artifact_manifest" ||
                artifactId === "artifact_assetpack" ||
                artifactId === "artifact_release_authority"
                    ? "job_release_01"
                    : "job_runtime_01";
            const authority = {
                ...serviceAuthority(),
                workspace_id:
                    artifactId === "artifact_script_01" && drift.scriptWorkspaceId
                        ? drift.scriptWorkspaceId
                        : serviceAuthority().workspace_id,
            };
            return Promise.resolve(v5Response(requestId, method, {
                authority,
                artifact_snapshot_hash:
                    artifactId === "artifact_script_01" && drift.scriptSnapshotHash
                        ? drift.scriptSnapshotHash
                        : authorityArtifactHash,
                artifact,
                projection: exactProjection(
                    format.replace("world-forge.", ""),
                    "succeeded",
                    [],
                    artifactId === "artifact_runtime_bundle" ? lineage : [],
                ),
            }));
        }
        if (method === "creation_job.get") {
            if (drift.omitReleaseJob) {
                return Promise.resolve({
                    protocol: "rpg-world-forge.studio_protocol",
                    protocol_version: 5,
                    kind: "error",
                    request_id: requestId,
                    error: { code: "not_found", message: "missing", details: {} },
                });
            }
            return Promise.resolve(v5Response(requestId, method, {
                job: v11ReleaseJobFixture(drift),
            }));
        }
        if (method === "creation_output_grant.get") {
            const grantId = String(params.grant_id);
            if (grantId === "grant_runtime_source") {
                return Promise.resolve(v5Response(requestId, method, {
                    grant: runtimeSourceGrantFixture(drift),
                }));
            }
            return Promise.resolve(v5Response(requestId, method, {
                grant: headlessTargetGrantFixture(grantId, drift),
            }));
        }
        if (method === "creation_job.create") {
            return Promise.resolve(v5Response(requestId, method, {
                job: { job_id: "job_headless_01" },
            }));
        }
        throw new Error(`unexpected ${method}`);
    });
}

function v11ReleaseJobFixture(drift: AuthorityHeadlessProofDrift) {
    const authorized = drift.releaseStatus !== "blocked";
    return {
        format: "world-forge.studio_creation_job",
        format_version: drift.releaseFormatVersion ?? 11,
        job_id: "job_release_01",
        workspace_id: "creation_workspace",
        operation: drift.releaseOperation ?? "asset.release.authorize",
        operation_params: {
            review_receipt_artifact_ids: ["artifact_review_01"],
            manifest_id: "manifest_01",
            assetpack_id: "assetpack_01",
            release_authority_id: "release_authority_01",
            blockers: [],
            target_grant_id: "grant_assetpack_01",
            expected_target_grant_generation: 6,
        },
        state: drift.releaseState ?? "succeeded",
        generation: 6,
        authority: {
            root_generation: 4,
            source_revision: "a".repeat(64),
            workflow_status_hash: "b".repeat(64),
            artifact_snapshot_hash: authorityArtifactHash,
        },
        result: {
            output_artifact_ids: [
                "artifact_manifest",
                "artifact_assetpack",
                "artifact_release_authority",
            ],
            artifact_snapshot_hash: authorityArtifactHash,
            analysis_status: authorized ? "passed" : "failed",
            reason_codes: drift.releaseReasonCodes ?? [],
            cleanup_pending: false,
            asset_manifest: { manifest_id: "manifest_01", content_hash: "4".repeat(64) },
            assetpack: { assetpack_id: "assetpack_01", content_hash: "5".repeat(64) },
            asset_release_authority: {
                format: "world-forge.asset_release_authority",
                format_version: 1,
                release_authority_id: "release_authority_01",
                content_hash: "6".repeat(64),
            },
            release_status: drift.releaseStatus ?? "authorized",
            publication: authorized ? { grant_id: "grant_assetpack_01" } : null,
        },
        error: null,
    };
}

function runtimeSourceGrantFixture(drift: AuthorityHeadlessProofDrift) {
    return {
        format: "world-forge.studio_creation_output_grant",
        format_version: drift.sourceGrantVersion ?? 2,
        grant_id: "grant_runtime_source",
        workspace_id: "creation_workspace",
        generation: 7,
        kind: drift.sourceGrantKind ?? "game_runtime_bundle_directory",
        state: drift.sourceGrantState ?? "published",
        publication: {
            kind: "game_runtime_bundle_directory",
            state: "published",
            artifact_id: drift.sourceGrantArtifactId ?? "artifact_runtime_bundle",
            runtime_bundle: {
                format: "world-forge.game_runtime_bundle",
                format_version: 1,
                id: drift.sourceGrantBundleId ?? "runtime_bundle_01",
                content_hash: "a".repeat(64),
            },
        },
    };
}

function headlessTargetGrantFixture(
    grantId: string,
    drift: AuthorityHeadlessProofDrift,
) {
    return {
        format: "world-forge.studio_creation_output_grant",
        format_version: drift.targetGrantVersion ?? 6,
        grant_id: grantId,
        workspace_id: "creation_workspace",
        generation: 3,
        kind: drift.targetGrantKind ?? "headless_evidence_directory",
        state: "ready",
        publication: null,
    };
}

function exactProjection(
    projectionKind: string,
    status: string,
    facts: unknown[] = [],
    lineage: { artifact_id: string }[] = [],
) {
    return {
        projection_kind: projectionKind,
        title: projectionKind,
        status,
        facts,
        lineage,
    };
}

function creationGraphFixture(includeModule = false, moduleId = "core") {
    const profile = sealCreationDocument({
        format: "world-forge.creation_profile",
        format_version: 1,
        profile_id: "neutral_profile",
        project_id: "neutral_universe",
        title: "Original",
    });
    const module = includeModule
        ? sealCreationDocument({
              format: "world-forge.logic_module",
              format_version: 1,
              module_id: moduleId,
              project_id: "neutral_universe",
              title: "Core logic",
          })
        : null;
    const manifest = sealCreationDocument({
        format: "world-forge.creation_source_manifest",
        format_version: 1,
        project_id: "neutral_universe",
        profile: {
            format: "world-forge.creation_profile",
            format_version: 1,
            id: "neutral_profile",
            path: "profile.json",
            content_hash: profile.content_hash,
        },
        modules: {
            world_modules: [],
            activity_modules: [],
            narrative_modules: [],
            system_modules: [],
            logic_modules: module
                ? [
                      {
                          format: "world-forge.logic_module",
                          format_version: 1,
                          id: moduleId,
                          path: `logic/${moduleId}.json`,
                          content_hash: module.content_hash,
                      },
                  ]
                : [],
        },
    });
    const project = sealCreationDocument({
        format: "world-forge.project",
        format_version: 1,
        project_id: "neutral_universe",
        title: "Neutral universe",
        profile: {
            format: "world-forge.creation_profile",
            format_version: 1,
            id: "neutral_profile",
            path: "profile.json",
            content_hash: profile.content_hash,
        },
        source_manifest: {
            format: "world-forge.creation_source_manifest",
            format_version: 1,
            id: "neutral_universe",
            path: "source/manifest.json",
            content_hash: manifest.content_hash,
        },
    });
    return {
        project,
        manifest,
        profile,
        module,
        files: {
            project: creationFile("project.json", project),
            manifest: creationFile("source/manifest.json", manifest),
            profile: creationFile("profile.json", profile),
            module: module
                ? creationFile(`source/logic/${moduleId}.json`, module)
                : null,
        },
    };
}

function queueCreationGraphReads(
    request: {
        mockImplementationOnce: (
            implementation: (requestId: string) => Promise<unknown>,
        ) => unknown;
    },
    graph: ReturnType<typeof creationGraphFixture>,
    includeModule = false,
): void {
    const files = [
        graph.files.project,
        graph.files.manifest,
        graph.files.profile,
        ...(includeModule && graph.files.module ? [graph.files.module] : []),
    ];
    request.mockImplementationOnce((requestId) =>
        Promise.resolve(
            v3Response(
                "creation_document.list",
                {
                    source_revision: "a".repeat(64),
                    documents: files.map((file) => ({
                        path: file.path,
                        format: file.format,
                        format_version: file.format_version,
                        id: file.id,
                        content_hash: file.content_hash,
                        file_sha256: file.file_sha256,
                    })),
                },
                requestId,
            ),
        ),
    );
    for (const file of files) {
        request.mockImplementationOnce((requestId) =>
            Promise.resolve(
                v3Response(
                    "creation_document.read",
                    {
                        source_revision: "a".repeat(64),
                        document: {
                            path: file.path,
                            file_sha256: file.fileSha256,
                            format: file.document.format,
                            format_version: file.document.format_version,
                            id: file.id,
                            content_hash: file.document.content_hash,
                            document: file.document,
                        },
                    },
                    requestId,
                ),
            ),
        );
    }
}

function creationFile(path: string, document: Record<string, unknown>) {
    const bytes = canonicalDocumentBytes(document);
    const id =
        document.format === "world-forge.creation_profile"
            ? document.profile_id
            : document.format === "world-forge.project" ||
                document.format === "world-forge.creation_source_manifest"
              ? document.project_id
              : document.module_id;
    return {
        path,
        id,
        fileSha256: createHash("sha256").update(bytes).digest("hex"),
        file_sha256: createHash("sha256").update(bytes).digest("hex"),
        size: bytes.byteLength,
        format: document.format,
        format_version: document.format_version,
        content_hash: document.content_hash,
        document,
        bytes,
    };
}

function sealCreationDocument(
    value: Record<string, unknown>,
): Record<string, unknown> {
    const payload = { ...value };
    delete payload.content_hash;
    const normalized = JSON.stringify(sortJsonForTest(payload));
    return {
        ...payload,
        content_hash: createHash("sha256")
            .update(normalized, "utf8")
            .digest("hex"),
    };
}

function sortJsonForTest(value: unknown): unknown {
    if (Array.isArray(value)) return value.map(sortJsonForTest);
    if (typeof value !== "object" || value === null) return value;
    return Object.fromEntries(
        Object.entries(value as Record<string, unknown>)
            .sort(([left], [right]) => left.localeCompare(right))
            .map(([key, item]) => [key, sortJsonForTest(item)]),
    );
}

function minimalProfile(title: string): Record<string, unknown> {
    return {
        content_hash: "0".repeat(64),
        format: "world-forge.creation_profile",
        format_version: 1,
        title,
    };
}

function canonicalDocumentBytes(value: unknown): Buffer {
    function sort(candidate: unknown): unknown {
        if (Array.isArray(candidate)) return candidate.map(sort);
        if (typeof candidate !== "object" || candidate === null)
            return candidate;
        return Object.fromEntries(
            Object.entries(candidate as Record<string, unknown>)
                .sort(([left], [right]) => left.localeCompare(right))
                .map(([key, item]) => [key, sort(item)]),
        );
    }
    return Buffer.from(`${JSON.stringify(sort(value), null, 2)}\n`, "utf8");
}

function createAssetEntry(index: number) {
    const suffix = index.toString(16).padStart(64, "0");
    return {
        entry_id: `asset_${suffix}`,
        asset_id: `asset-${String(index)}`,
        category: "manifest",
        role: null,
        path: `assets/catalog-${String(index)}.json`,
        sha256: suffix,
        media_type: "application/json",
        selected: false,
        inspectable: true,
    };
}

function createAssetEntries(count: number) {
    return Array.from({ length: count }, (_, index) => createAssetEntry(index));
}

function createAssetCatalogListResponse(
    requestId: string,
    {
        manifestRevision,
        offset,
        entries,
        nextOffset,
    }: {
        manifestRevision: string;
        offset: number;
        entries: readonly Record<string, unknown>[];
        nextOffset: number | null;
    },
) {
    return {
        protocol: "rpg-world-forge.studio_protocol",
        protocol_version: 1,
        kind: "response",
        request_id: requestId,
        method: "asset.catalog.list",
        result: {
            manifest_revision: manifestRevision,
            offset,
            limit: 64,
            entries,
            next_offset: nextOffset,
        },
    };
}

function createAssetCatalogInspectResponse(
    requestId: string,
    manifestRevision: string,
    entryId: string,
) {
    return {
        protocol: "rpg-world-forge.studio_protocol",
        protocol_version: 1,
        kind: "response",
        request_id: requestId,
        method: "asset.catalog.inspect",
        result: {
            manifest_revision: manifestRevision,
            entry: { ...createAssetEntry(0), entry_id: entryId },
            inspection: {
                kind: "json",
                encoding: "utf-8",
                content: "{}",
                value: {},
            },
        },
    };
}

function jobCapabilityCases() {
    return [
        {
            channel: IPC_CHANNELS.validateAssetReceipt,
            argument: {
                workspaceId: "workspace_01",
                input: { receipt: "receipts/item.json" },
            },
            operation: "asset.receipt.validate",
            input: { receipt: "receipts/item.json" },
        },
        {
            channel: IPC_CHANNELS.verifyAssetpack,
            argument: {
                workspaceId: "workspace_01",
                input: {
                    assetpack: "build/assets.json",
                    worldpack: "build/world.json",
                },
            },
            operation: "assetpack.verify",
            input: {
                assetpack: "build/assets.json",
                worldpack: "build/world.json",
            },
        },
        {
            channel: IPC_CHANNELS.runHeadless,
            argument: {
                workspaceId: "workspace_01",
                input: { worldpack: "build/world.json", ticks: 0 },
            },
            operation: "runtime.headless",
            input: { worldpack: "build/world.json", ticks: 0 },
        },
        {
            channel: IPC_CHANNELS.runReplay,
            argument: {
                workspaceId: "workspace_01",
                input: {
                    worldpack: "build/world.json",
                    replay: "replays/slot.json",
                },
            },
            operation: "runtime.replay",
            input: {
                worldpack: "build/world.json",
                replay: "replays/slot.json",
            },
        },
    ] as const;
}

function createManagedJobResponse(
    requestId: string,
    operation: string,
    input: Readonly<Record<string, unknown>>,
    workspaceId = "workspace_01",
) {
    return {
        protocol: "rpg-world-forge.studio_protocol",
        protocol_version: 1,
        kind: "response",
        request_id: requestId,
        method: "job.create",
        result: {
            job: {
                format: "rpg-world-forge.studio_job",
                format_version: 2,
                job_id: "job_01",
                workspace_id: workspaceId,
                operation,
                state: "queued",
                input,
                result: null,
                error: null,
                created_at: "2026-07-23T00:00:00Z",
                updated_at: "2026-07-23T00:00:00Z",
            },
        },
    };
}

function createV2Changeset({
    baseSha256,
    content,
}: {
    baseSha256: string;
    content: string;
}) {
    const operation = {
        path: "source/lore/entry.md",
        operation: "replace" as const,
        base_sha256: baseSha256,
        base_size: 4,
        proposed_sha256: createHash("sha256")
            .update(content, "utf8")
            .digest("hex"),
        size: Buffer.byteLength(content, "utf8"),
    };
    return {
        format: "rpg-world-forge.studio_changeset" as const,
        format_version: 2 as const,
        changeset_id: "changeset_01",
        workspace_id: "workspace_01",
        status: "staged" as "staged" | "approved" | "rejected" | "applied",
        operations: [operation],
        review_sha256: reviewSha256([operation]),
        created_at: "2026-07-23T00:00:00Z",
        updated_at: "2026-07-23T00:00:00Z",
    };
}

function createV1Changeset() {
    return {
        format: "rpg-world-forge.studio_changeset" as const,
        format_version: 1 as const,
        changeset_id: "legacy_01",
        workspace_id: "workspace_01",
        status: "staged" as "staged" | "approved" | "rejected" | "applied",
        operations: [
            {
                path: "source/lore/legacy.md",
                operation: "replace" as const,
                base_sha256: "a".repeat(64),
                proposed_sha256: "b".repeat(64),
                size: 4,
            },
        ],
        created_at: "2026-07-22T00:00:00Z",
        updated_at: "2026-07-22T00:00:00Z",
    };
}

function requireRecord(value: unknown): Record<string, unknown> {
    if (typeof value !== "object" || value === null || Array.isArray(value)) {
        throw new Error("Expected object record");
    }
    return value as Record<string, unknown>;
}

function reviewSha256(operations: readonly Record<string, unknown>[]): string {
    const projected = operations.map((operation) => ({
        base_sha256: operation.base_sha256,
        base_size: operation.base_size,
        operation: operation.operation,
        path: operation.path,
        proposed_sha256: operation.proposed_sha256,
        size: operation.size,
    }));
    return createHash("sha256")
        .update(
            JSON.stringify({
                format: "rpg-world-forge.studio_changeset_review",
                format_version: 1,
                operations: projected,
            }),
            "utf8",
        )
        .digest("hex");
}

function createChangesetResponse(
    requestId: string,
    method: string,
    changeset: Record<string, unknown>,
) {
    return {
        protocol: "rpg-world-forge.studio_protocol",
        protocol_version: 1,
        kind: "response",
        request_id: requestId,
        method,
        result: { changeset },
    };
}

function createDiffResponse(
    requestId: string,
    changeset: ReturnType<typeof createV2Changeset>,
) {
    const operation = changeset.operations[0];
    return {
        protocol: "rpg-world-forge.studio_protocol",
        protocol_version: 1,
        kind: "response",
        request_id: requestId,
        method: "changeset.diff",
        result: {
            diff: {
                changeset_id: changeset.changeset_id,
                changeset_format_version: 2,
                available: true,
                unavailable_reason: null,
                review_sha256: changeset.review_sha256,
                operations: [
                    {
                        ...operation,
                        text_hunks: [
                            {
                                base_start: 1,
                                base_count: 1,
                                proposed_start: 1,
                                proposed_count: 1,
                                lines: [
                                    { kind: "remove", text: "old\n" },
                                    { kind: "add", text: "new\n" },
                                ],
                            },
                        ],
                        json_pointer_changes: null,
                    },
                ],
            },
        },
    };
}

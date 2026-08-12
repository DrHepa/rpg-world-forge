import { describe, expect, expectTypeOf, it, vi } from "vitest";

import { createStudioApi, type PreloadTransport } from "../../src/preload/api";
import { IPC_CHANNELS } from "../../src/shared/studio-api";

describe("preload API", () => {
    it("exposes only the fixed Studio operations", async () => {
        const invoke = vi
            .fn()
            .mockResolvedValue({ ok: true, value: { state: "ready" } });
        const transport: PreloadTransport = {
            invoke,
            on: vi.fn(),
            removeListener: vi.fn(),
        };
        const api = createStudioApi(transport);

        expect(Object.isFrozen(api)).toBe(true);
        expect(Object.keys(api).sort()).toEqual([
            "admitCreationArtifact",
            "analyzeWorld",
            "answerCodexUserInput",
            "applyChangeset",
            "applyCreationChangeset",
            "approveChangeset",
            "approveCreationChangeset",
            "authorizeCreationAssetRelease",
            "bindCodexWorkspace",
            "buildCreationMaterializationBundle",
            "buildCreationRuntimeBundle",
            "cancelCreationJob",
            "cancelExternalJob",
            "cancelJob",
            "closeAssetPreview",
            "closeCreationPreview",
            "compileCreationProject",
            "completeCreationPhase",
            "composeCreationRuntime",
            "createCreationProject",
            "createExternalGrant",
            "diffCreationChangeset",
            "extractCreationGamePackage",
            "extractGamePackage",
            "forkCodexThread",
            "getChangeset",
            "getCodexStatus",
            "getCreationAssetpackOutput",
            "getCreationAuthorityCapabilities",
            "getCreationChangeset",
            "getCreationJob",
            "getCreationWorkflow",
            "getExternalGrant",
            "getExternalJob",
            "getServiceStatus",
            "getWorkspaceOverview",
            "initialize",
            "inspectAssetCatalogEntry",
            "inspectCreationArtifact",
            "inspectCreationEvidence",
            "inspectCreationReadiness",
            "interruptCodexTurn",
            "listAssetCatalog",
            "listChangesets",
            "listCreationArtifacts",
            "listCreationAuthorityOutputGrants",
            "listCreationDocuments",
            "listCreationEvents",
            "listCreationJobs",
            "listCreationOutputGrants",
            "listCreationWorkspaces",
            "listEvents",
            "listExternalJobs",
            "listJobs",
            "listSourceDocuments",
            "listWorkspaces",
            "materializeCreationGame",
            "materializeGame",
            "onCodexEvent",
            "onEvent",
            "openAssetPreview",
            "openCreationPreview",
            "openCreationWorkspace",
            "packageCreationGame",
            "packageGame",
            "processCreationAsset",
            "readAssetPreviewChunk",
            "readChangesetDiff",
            "readCodexAccount",
            "readCreationDocument",
            "readCreationPhase",
            "readCreationPreviewChunk",
            "readSourceDocument",
            "reconcileCreationWorkflow",
            "recoverCreationChangeset",
            "recoverCreationJob",
            "recoverExternalJob",
            "registerCreationProject",
            "rejectChangeset",
            "reopenCreationPhase",
            "requestCreationJobCancel",
            "requestCreationJobRecovery",
            "resumeCodexThread",
            "reviewCreationAssetQa",
            "revokeCreationAssetpackOutput",
            "revokeExternalGrant",
            "runHeadless",
            "runReplay",
            "sealCreationAssetRelease",
            "selectCreationAssetpackOutput",
            "selectCreationGamePackageExtractionOutput",
            "selectCreationGamePackageOutput",
            "selectCreationHeadlessEvidenceOutput",
            "selectCreationMaterializationBundleOutput",
            "selectCreationRuntimeBundleOutput",
            "selectCreationStandaloneGameOutput",
            "stageCreationModuleChange",
            "stageCreationProfile",
            "stageSourceDocument",
            "startCodexLogin",
            "startCodexThread",
            "startCodexTurn",
            "steerCodexTurn",
            "validateAssetReceipt",
            "validateCreationPhase",
            "validateWorld",
            "verifyAssetpack",
            "verifyCreationHeadless",
        ]);
        expect(api).not.toHaveProperty("request");
        expect(api).not.toHaveProperty("cancelRequest");
        expect(api).not.toHaveProperty("ipcRenderer");
        expect(api).not.toHaveProperty("filesystem");
        expect(api).not.toHaveProperty("exec");
        await api.initialize();
        await api.getCreationAuthorityCapabilities!();
        await api.getServiceStatus();
        await api.listWorkspaces();
        await api.listEvents({ workspace_id: "workspace_01", limit: 10 });
        await api.listChangesets({ status: "staged" });
        await api.listJobs({ state: "queued" });
        await api.getWorkspaceOverview("workspace_01");
        await api.listSourceDocuments("workspace_01");
        await api.readSourceDocument("workspace_01", "source/world.json");
        await api.listAssetCatalog("workspace_01");
        await api.listAssetCatalog("workspace_01", {
            offset: 64,
            manifestRevision: "c".repeat(64),
        });
        await api.inspectAssetCatalogEntry(
            "workspace_01",
            "c".repeat(64),
            `asset_${"d".repeat(64)}`,
        );
        await api.openAssetPreview(
            "workspace_01",
            "c".repeat(64),
            `asset_${"d".repeat(64)}`,
        );
        await api.readAssetPreviewChunk("E".repeat(43), 0);
        await api.closeAssetPreview("E".repeat(43));
        await api.stageSourceDocument(
            "workspace_01",
            "source/world.json",
            "a".repeat(64),
            "{}\n",
        );
        await api.getChangeset("changeset_01");
        await api.readChangesetDiff("changeset_01");
        await api.approveChangeset("changeset_01", "b".repeat(64));
        await api.rejectChangeset("legacy_01");
        await api.applyChangeset("changeset_01", "b".repeat(64));
        await api.validateWorld("workspace_01");
        await api.analyzeWorld("workspace_01");
        const receiptValidation = await api.validateAssetReceipt(
            "workspace_01",
            {
                receipt: "receipts/item.json",
            },
        );
        const assetpackVerification = await api.verifyAssetpack(
            "workspace_01",
            {
                assetpack: "build/assetpack.json",
                worldpack: "build/worldpack.json",
            },
        );
        const headlessRun = await api.runHeadless("workspace_01", {
            worldpack: "build/worldpack.json",
            ticks: 0,
        });
        const replayRun = await api.runReplay("workspace_01", {
            worldpack: "build/worldpack.json",
            replay: "replays/slot.json",
        });
        if (
            receiptValidation.ok &&
            receiptValidation.value.kind === "response"
        ) {
            expectTypeOf(
                receiptValidation.value.result.job.operation,
            ).toEqualTypeOf<"asset.receipt.validate">();
        }
        if (
            assetpackVerification.ok &&
            assetpackVerification.value.kind === "response"
        ) {
            expectTypeOf(
                assetpackVerification.value.result.job.operation,
            ).toEqualTypeOf<"assetpack.verify">();
        }
        if (headlessRun.ok && headlessRun.value.kind === "response") {
            expectTypeOf(
                headlessRun.value.result.job.operation,
            ).toEqualTypeOf<"runtime.headless">();
        }
        if (replayRun.ok && replayRun.value.kind === "response") {
            expectTypeOf(
                replayRun.value.result.job.operation,
            ).toEqualTypeOf<"runtime.replay">();
        }
        await api.cancelJob("job_01");
        await api.getCodexStatus();
        await api.bindCodexWorkspace("workspace_01");
        await api.readCodexAccount();
        await api.startCodexLogin("device-code");
        await api.startCodexThread();
        await api.resumeCodexThread("thread-1");
        await api.forkCodexThread("thread-1");
        await api.startCodexTurn("thread-1", "hello");
        await api.steerCodexTurn("thread-1", "turn-1", "more");
        await api.interruptCodexTurn("thread-1", "turn-1");
        await api.answerCodexUserInput("token", { question: ["answer"] });
        expect(invoke.mock.calls).toEqual([
            [IPC_CHANNELS.initialize],
            [IPC_CHANNELS.getCreationAuthorityCapabilities],
            [IPC_CHANNELS.status],
            [IPC_CHANNELS.listWorkspaces],
            [
                IPC_CHANNELS.listEvents,
                { workspace_id: "workspace_01", limit: 10 },
            ],
            [IPC_CHANNELS.listChangesets, { status: "staged" }],
            [IPC_CHANNELS.listJobs, { state: "queued" }],
            [
                IPC_CHANNELS.getWorkspaceOverview,
                { workspaceId: "workspace_01" },
            ],
            [IPC_CHANNELS.listSourceDocuments, { workspaceId: "workspace_01" }],
            [
                IPC_CHANNELS.readSourceDocument,
                {
                    workspaceId: "workspace_01",
                    path: "source/world.json",
                },
            ],
            [IPC_CHANNELS.listAssetCatalog, { workspaceId: "workspace_01" }],
            [
                IPC_CHANNELS.listAssetCatalog,
                {
                    workspaceId: "workspace_01",
                    offset: 64,
                    expectedManifestRevision: "c".repeat(64),
                },
            ],
            [
                IPC_CHANNELS.inspectAssetCatalogEntry,
                {
                    workspaceId: "workspace_01",
                    manifestRevision: "c".repeat(64),
                    entryId: `asset_${"d".repeat(64)}`,
                },
            ],
            [
                IPC_CHANNELS.openAssetPreview,
                {
                    workspaceId: "workspace_01",
                    manifestRevision: "c".repeat(64),
                    entryId: `asset_${"d".repeat(64)}`,
                },
            ],
            [
                IPC_CHANNELS.readAssetPreviewChunk,
                { handle: "E".repeat(43), sequence: 0 },
            ],
            [IPC_CHANNELS.closeAssetPreview, { handle: "E".repeat(43) }],
            [
                IPC_CHANNELS.stageSourceDocument,
                {
                    workspaceId: "workspace_01",
                    path: "source/world.json",
                    baseSha256: "a".repeat(64),
                    content: "{}\n",
                },
            ],
            [IPC_CHANNELS.getChangeset, { changesetId: "changeset_01" }],
            [IPC_CHANNELS.readChangesetDiff, { changesetId: "changeset_01" }],
            [
                IPC_CHANNELS.approveChangeset,
                {
                    changesetId: "changeset_01",
                    expectedReviewSha256: "b".repeat(64),
                },
            ],
            [IPC_CHANNELS.rejectChangeset, { changesetId: "legacy_01" }],
            [
                IPC_CHANNELS.applyChangeset,
                {
                    changesetId: "changeset_01",
                    expectedReviewSha256: "b".repeat(64),
                },
            ],
            [IPC_CHANNELS.validateWorld, { workspaceId: "workspace_01" }],
            [IPC_CHANNELS.analyzeWorld, { workspaceId: "workspace_01" }],
            [
                IPC_CHANNELS.validateAssetReceipt,
                {
                    workspaceId: "workspace_01",
                    input: { receipt: "receipts/item.json" },
                },
            ],
            [
                IPC_CHANNELS.verifyAssetpack,
                {
                    workspaceId: "workspace_01",
                    input: {
                        assetpack: "build/assetpack.json",
                        worldpack: "build/worldpack.json",
                    },
                },
            ],
            [
                IPC_CHANNELS.runHeadless,
                {
                    workspaceId: "workspace_01",
                    input: { worldpack: "build/worldpack.json", ticks: 0 },
                },
            ],
            [
                IPC_CHANNELS.runReplay,
                {
                    workspaceId: "workspace_01",
                    input: {
                        worldpack: "build/worldpack.json",
                        replay: "replays/slot.json",
                    },
                },
            ],
            [IPC_CHANNELS.cancelJob, { jobId: "job_01" }],
            [IPC_CHANNELS.codexStatus],
            [IPC_CHANNELS.codexBindWorkspace, { workspaceId: "workspace_01" }],
            [IPC_CHANNELS.codexReadAccount],
            [IPC_CHANNELS.codexStartLogin, { mode: "device-code" }],
            [IPC_CHANNELS.codexStartThread],
            [IPC_CHANNELS.codexResumeThread, { threadId: "thread-1" }],
            [IPC_CHANNELS.codexForkThread, { threadId: "thread-1" }],
            [
                IPC_CHANNELS.codexStartTurn,
                { threadId: "thread-1", text: "hello" },
            ],
            [
                IPC_CHANNELS.codexSteerTurn,
                { threadId: "thread-1", turnId: "turn-1", text: "more" },
            ],
            [
                IPC_CHANNELS.codexInterruptTurn,
                { threadId: "thread-1", turnId: "turn-1" },
            ],
            [
                IPC_CHANNELS.codexAnswerUserInput,
                { token: "token", answers: { question: ["answer"] } },
            ],
        ]);
    });

    it("exposes pathless fixed generic-creation operations with exact CAS inputs", async () => {
        const invoke = vi.fn().mockResolvedValue({
            ok: false,
            error: { code: "not_found", message: "fixture" },
        });
        const api = createStudioApi({
            invoke,
            on: vi.fn(),
            removeListener: vi.fn(),
        });
        const hash = "a".repeat(64);
        const recordHash = "b".repeat(64);
        const reviewHash = "c".repeat(64);
        const profile = {
            format: "world-forge.creation_profile",
            format_version: 1,
            content_hash: hash,
        };
        const module = {
            format: "world-forge.logic_module",
            format_version: 1,
            module_id: "neutral_logic",
            project_id: "neutral_universe",
            content_hash: hash,
        };
        const authority = {
            workspaceId: "creation_workspace",
            expectedRootGeneration: 2,
            expectedSourceRevision: hash,
            expectedWorkflowStatusHash: reviewHash,
        };

        await api.listCreationWorkspaces();
        await api.registerCreationProject();
        await api.createCreationProject({
            projectKind: "universe_library",
            projectId: "neutral_universe",
            title: "Neutral universe",
            defaultLocale: "en",
            projectVersion: "0.1.0",
        });
        await api.openCreationWorkspace("creation_workspace");
        await api.listCreationDocuments("creation_workspace", hash);
        await api.readCreationDocument(
            "creation_workspace",
            hash,
            "profile.json",
        );
        await api.getCreationWorkflow("creation_workspace");
        await api.inspectCreationReadiness("creation_workspace");
        await api.stageCreationProfile({
            ...authority,
            path: "profile.json",
            expectedBaseFileSha256: recordHash,
            proposedProfile: { ...profile, title: "Updated" },
        });
        await api.stageCreationModuleChange({
            ...authority,
            operation: "replace",
            path: "source/logic/neutral.json",
            format: "world-forge.logic_module",
            expectedBaseFileSha256: recordHash,
            proposedModule: { ...module, title: "Updated logic" },
        });
        await api.getCreationChangeset("creation_changeset");
        await api.diffCreationChangeset("creation_changeset");
        await api.approveCreationChangeset(
            "creation_changeset",
            recordHash,
            reviewHash,
        );
        await api.applyCreationChangeset(
            "creation_changeset",
            recordHash,
            reviewHash,
            2,
        );
        await api.recoverCreationChangeset(
            "creation_changeset",
            "rollback",
            recordHash,
            reviewHash,
            2,
        );
        await api.reconcileCreationWorkflow({
            ...authority,
            artifactRegistry: [],
        });
        await api.readCreationPhase({ ...authority, phaseId: "p00_brief" });
        await api.validateCreationPhase({
            ...authority,
            report: { format: "world-forge.phase_report", format_version: 3 },
            artifactRegistry: [],
        });
        await api.completeCreationPhase({
            ...authority,
            report: { format: "world-forge.phase_report", format_version: 3 },
            artifactRegistry: [],
        });
        await api.reopenCreationPhase({
            ...authority,
            phaseId: "p00_brief",
            reason: "Requirements changed",
            approvedBy: "lead_reviewer",
        });

        expect(invoke.mock.calls).toEqual([
            [IPC_CHANNELS.listCreationWorkspaces],
            [IPC_CHANNELS.registerCreationProject],
            [
                IPC_CHANNELS.createCreationProject,
                {
                    projectKind: "universe_library",
                    projectId: "neutral_universe",
                    title: "Neutral universe",
                    defaultLocale: "en",
                    projectVersion: "0.1.0",
                },
            ],
            [
                IPC_CHANNELS.openCreationWorkspace,
                { workspaceId: "creation_workspace" },
            ],
            [
                IPC_CHANNELS.listCreationDocuments,
                {
                    workspaceId: "creation_workspace",
                    expectedSourceRevision: hash,
                },
            ],
            [
                IPC_CHANNELS.readCreationDocument,
                {
                    workspaceId: "creation_workspace",
                    expectedSourceRevision: hash,
                    path: "profile.json",
                },
            ],
            [
                IPC_CHANNELS.getCreationWorkflow,
                { workspaceId: "creation_workspace" },
            ],
            [
                IPC_CHANNELS.inspectCreationReadiness,
                { workspaceId: "creation_workspace" },
            ],
            [
                IPC_CHANNELS.stageCreationProfile,
                {
                    ...authority,
                    path: "profile.json",
                    expectedBaseFileSha256: recordHash,
                    proposedProfile: { ...profile, title: "Updated" },
                },
            ],
            [
                IPC_CHANNELS.stageCreationModuleChange,
                {
                    ...authority,
                    operation: "replace",
                    path: "source/logic/neutral.json",
                    format: "world-forge.logic_module",
                    expectedBaseFileSha256: recordHash,
                    proposedModule: { ...module, title: "Updated logic" },
                },
            ],
            [
                IPC_CHANNELS.getCreationChangeset,
                { changesetId: "creation_changeset" },
            ],
            [
                IPC_CHANNELS.diffCreationChangeset,
                { changesetId: "creation_changeset" },
            ],
            [
                IPC_CHANNELS.approveCreationChangeset,
                {
                    changesetId: "creation_changeset",
                    expectedRecordHash: recordHash,
                    expectedReviewSha256: reviewHash,
                },
            ],
            [
                IPC_CHANNELS.applyCreationChangeset,
                {
                    changesetId: "creation_changeset",
                    expectedRecordHash: recordHash,
                    expectedReviewSha256: reviewHash,
                    expectedRootGeneration: 2,
                },
            ],
            [
                IPC_CHANNELS.recoverCreationChangeset,
                {
                    changesetId: "creation_changeset",
                    mode: "rollback",
                    expectedRecordHash: recordHash,
                    expectedReviewSha256: reviewHash,
                    expectedRootGeneration: 2,
                },
            ],
            [
                IPC_CHANNELS.reconcileCreationWorkflow,
                {
                    ...authority,
                    artifactRegistry: [],
                },
            ],
            [
                IPC_CHANNELS.readCreationPhase,
                { ...authority, phaseId: "p00_brief" },
            ],
            [
                IPC_CHANNELS.validateCreationPhase,
                {
                    ...authority,
                    report: {
                        format: "world-forge.phase_report",
                        format_version: 3,
                    },
                    artifactRegistry: [],
                },
            ],
            [
                IPC_CHANNELS.completeCreationPhase,
                {
                    ...authority,
                    report: {
                        format: "world-forge.phase_report",
                        format_version: 3,
                    },
                    artifactRegistry: [],
                },
            ],
            [
                IPC_CHANNELS.reopenCreationPhase,
                {
                    ...authority,
                    phaseId: "p00_brief",
                    reason: "Requirements changed",
                    approvedBy: "lead_reviewer",
                },
            ],
        ]);
        expect(JSON.stringify(invoke.mock.calls)).not.toContain("/home/");
        expect(api).not.toHaveProperty("requestV3");
        expect(api).not.toHaveProperty("createRootGrant");
    });

    it("exposes only fixed pathless creation evidence operations", async () => {
        const invoke = vi.fn().mockResolvedValue({
            ok: false,
            error: { code: "not_found", message: "fixture" },
        });
        const api = createStudioApi({
            invoke,
            on: vi.fn(),
            removeListener: vi.fn(),
        });
        const sourceHash = "a".repeat(64);
        const workflowHash = "b".repeat(64);
        const snapshotHash = "c".repeat(64);
        const authority = {
            workspaceId: "creation_workspace",
            expectedRootGeneration: 2,
            expectedSourceRevision: sourceHash,
            expectedWorkflowStatusHash: workflowHash,
            expectedArtifactSnapshotHash: snapshotHash,
        };

        await api.listCreationArtifacts({
            ...authority,
            lifecycle: "active",
            cursor: null,
            limit: 32,
        });
        await api.inspectCreationArtifact({
            ...authority,
            artifactId: "artifact_01",
        });
        await api.inspectCreationEvidence(authority);

        expect(invoke.mock.calls).toEqual([
            [
                IPC_CHANNELS.listCreationArtifacts,
                {
                    ...authority,
                    lifecycle: "active",
                    cursor: null,
                    limit: 32,
                },
            ],
            [
                IPC_CHANNELS.inspectCreationArtifact,
                {
                    ...authority,
                    artifactId: "artifact_01",
                },
            ],
            [IPC_CHANNELS.inspectCreationEvidence, authority],
        ]);
        expect(JSON.stringify(invoke.mock.calls)).not.toContain("path");
        expect(api).not.toHaveProperty("requestV4");
        expect(api).toHaveProperty("materializeCreationGame");
        expect(api).not.toHaveProperty("previewCreationAsset");
    });

    it("rejects asset criteria and evidence beyond 64 before crossing IPC", async () => {
        const invoke = vi.fn().mockResolvedValue({
            ok: false,
            error: { code: "not_found", message: "fixture" },
        });
        const api = createStudioApi({
            invoke,
            on: vi.fn(),
            removeListener: vi.fn(),
        });
        const authority = {
            workspaceId: "creation_workspace",
            expectedRootGeneration: 2,
            expectedSourceRevision: "a".repeat(64),
            expectedWorkflowStatusHash: "b".repeat(64),
            expectedArtifactSnapshotHash: "c".repeat(64),
        };
        const acceptanceResults = (count: number, evidenceCount = 1) =>
            Array.from({ length: count }, (_, index) => ({
                criterionIndex: index,
                criterionSha256: (index + 1)
                    .toString(16)
                    .padStart(64, "0"),
                status: "passed" as const,
                evidenceHashes: Array.from(
                    { length: evidenceCount },
                    (_unused, evidenceIndex) =>
                        (evidenceIndex + 1)
                            .toString(16)
                            .padStart(64, "0"),
                ),
            }));
        const process = (results: ReturnType<typeof acceptanceResults>) =>
            api.processCreationAsset({
                ...authority,
                jobId: "process_01",
                licenseArtifactIds: ["artifact_license_01"],
                recipeId: "board_ui_recipe",
                processingReceiptId: "board_ui_processing_receipt",
                qaReportId: "board_ui_qa",
                acceptanceResults: results,
            });

        await process(acceptanceResults(64));
        await process(acceptanceResults(1, 64));
        await expect(process(acceptanceResults(65))).rejects.toThrow(
            /acceptance results/u,
        );
        await expect(process(acceptanceResults(1, 65))).rejects.toThrow(
            /criterion evidence/u,
        );
        expect(invoke).toHaveBeenCalledTimes(2);
    });

    it("exposes fixed pathless creation job operations without a generic operation escape hatch", async () => {
        const invoke = vi.fn().mockResolvedValue({
            ok: false,
            error: { code: "not_found", message: "fixture" },
        });
        const api = createStudioApi({
            invoke,
            on: vi.fn(),
            removeListener: vi.fn(),
        });
        const authority = {
            workspaceId: "creation_workspace",
            expectedRootGeneration: 2,
            expectedSourceRevision: "a".repeat(64),
            expectedWorkflowStatusHash: "b".repeat(64),
            expectedArtifactSnapshotHash: "c".repeat(64),
        };
        const expectedRecordHash = "d".repeat(64);

        await api.compileCreationProject({ ...authority, jobId: "compile_01" });
        await api.admitCreationArtifact({
            ...authority,
            document: {
                format: "world-forge.game_analysis",
                format_version: 1,
                content_hash: "e".repeat(64),
            },
            dependencyArtifactIds: ["artifact_01"],
        });
        await api.processCreationAsset({
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
                    status: "passed",
                    evidenceHashes: ["1".repeat(64)],
                },
            ],
        });
        await api.selectCreationAssetpackOutput("creation_workspace");
        await api.getCreationAssetpackOutput("grant_output_01");
        await api.listCreationOutputGrants({
            ...authority,
            cursor: null,
            limit: 8,
        });
        await api.listCreationAuthorityOutputGrants?.({
            ...authority,
            cursor: null,
            limit: 8,
        });
        await api.revokeCreationAssetpackOutput({
            grantId: "grant_output_01",
            expectedGeneration: 2,
        });
        await api.sealCreationAssetRelease({
            ...authority,
            jobId: "seal_01",
            qaReportArtifactIds: ["artifact_qa_01"],
            manifestId: "release_manifest_01",
            targetGrantId: "grant_output_01",
            expectedTargetGrantGeneration: 2,
        });
        await api.composeCreationRuntime({
            ...authority,
            jobId: "compose_01",
            gamepackArtifactId: "artifact_gamepack_01",
            assetInventoryArtifactId: "artifact_inventory_01",
            assetpackArtifactId: "artifact_assetpack_01",
            targetGrantId: "grant_output_01",
            expectedTargetGrantGeneration: 2,
        });
        await api.selectCreationRuntimeBundleOutput("creation_workspace");
        await api.buildCreationRuntimeBundle({
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
        });
        await api.selectCreationMaterializationBundleOutput(
            "creation_workspace",
        );
        await api.buildCreationMaterializationBundle({
            ...authority,
            jobId: "materialization_01",
            runtimeBundleArtifactId: "artifact_runtime_bundle_01",
            sourceGrantId: "grant_runtime_bundle_01",
            expectedSourceGrantGeneration: 2,
            targetGrantId: "grant_materialization_01",
            expectedTargetGrantGeneration: 1,
        });
        await api.selectCreationStandaloneGameOutput("creation_workspace");
        await api.materializeCreationGame({
            ...authority,
            jobId: "standalone_01",
            materializationBundleArtifactId:
                "artifact_materialization_bundle_01",
            sourceGrantId: "grant_materialization_01",
            expectedSourceGrantGeneration: 2,
            targetGrantId: "grant_standalone_01",
            expectedTargetGrantGeneration: 1,
        });
        await api.selectCreationGamePackageOutput("creation_workspace");
        await api.packageCreationGame({
            ...authority,
            jobId: "package_01",
            standaloneGameArtifactId: "artifact_standalone_game_01",
            sourceGrantId: "grant_standalone_01",
            expectedSourceGrantGeneration: 2,
            targetGrantId: "grant_package_01",
            expectedTargetGrantGeneration: 1,
        });
        await api.selectCreationGamePackageExtractionOutput(
            "creation_workspace",
        );
        await api.extractCreationGamePackage({
            ...authority,
            jobId: "extract_01",
            gamePackageArtifactId: "artifact_game_package_01",
            sourceGrantId: "grant_package_01",
            expectedSourceGrantGeneration: 2,
            targetGrantId: "grant_extracted_standalone_01",
            expectedTargetGrantGeneration: 1,
        });
        await api.reviewCreationAssetQa({
            workspaceId: "creation_workspace",
            qaReportArtifactId: "artifact_qa_01",
            outputRole: "texture",
        });
        await api.authorizeCreationAssetRelease({
            workspaceId: "creation_workspace",
            reviewReceiptArtifactIds: ["artifact_review_01"],
            targetGrantId: "grant_assetpack_01",
        });
        await api.selectCreationHeadlessEvidenceOutput("creation_workspace");
        await api.verifyCreationHeadless({
            workspaceId: "creation_workspace",
            runtimeBundleArtifactId: "artifact_runtime_bundle_01",
            sourceGrantId: "grant_runtime_bundle_01",
            headlessScriptArtifactId: "artifact_script_01",
            targetGrantId: "grant_headless_01",
            platformId: "platform:linux_x86_64",
        });
        await api.requestCreationJobCancel({
            workspaceId: "creation_workspace",
            jobId: "job_review_01",
        });
        await api.requestCreationJobRecovery({
            workspaceId: "creation_workspace",
            jobId: "job_review_01",
        });
        await api.getCreationJob("compile_01");
        await api.listCreationJobs({
            workspaceId: "creation_workspace",
            state: null,
            afterSequence: 0,
            limit: 25,
        });
        await api.cancelCreationJob({
            jobId: "compile_01",
            expectedGeneration: 0,
            expectedRecordHash,
        });
        await api.recoverCreationJob({
            jobId: "compile_01",
            mode: "resume",
            expectedGeneration: 1,
            expectedRecordHash,
        });
        await api.listCreationEvents({
            workspaceId: "creation_workspace",
            afterId: 0,
            limit: 64,
        });

        expect(invoke.mock.calls).toEqual([
            [
                IPC_CHANNELS.compileCreationProject,
                { ...authority, jobId: "compile_01" },
            ],
            [
                IPC_CHANNELS.admitCreationArtifact,
                {
                    ...authority,
                    document: {
                        format: "world-forge.game_analysis",
                        format_version: 1,
                        content_hash: "e".repeat(64),
                    },
                    dependencyArtifactIds: ["artifact_01"],
                },
            ],
            [
                IPC_CHANNELS.processCreationAsset,
                {
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
                            status: "passed",
                            evidenceHashes: ["1".repeat(64)],
                        },
                    ],
                },
            ],
            [
                IPC_CHANNELS.selectCreationAssetpackOutput,
                { workspaceId: "creation_workspace" },
            ],
            [
                IPC_CHANNELS.getCreationAssetpackOutput,
                { grantId: "grant_output_01" },
            ],
            [
                IPC_CHANNELS.listCreationOutputGrants,
                { ...authority, cursor: null, limit: 8 },
            ],
            [
                IPC_CHANNELS.listCreationAuthorityOutputGrants,
                { ...authority, cursor: null, limit: 8 },
            ],
            [
                IPC_CHANNELS.revokeCreationAssetpackOutput,
                {
                    grantId: "grant_output_01",
                    expectedGeneration: 2,
                },
            ],
            [
                IPC_CHANNELS.sealCreationAssetRelease,
                {
                    ...authority,
                    jobId: "seal_01",
                    qaReportArtifactIds: ["artifact_qa_01"],
                    manifestId: "release_manifest_01",
                    targetGrantId: "grant_output_01",
                    expectedTargetGrantGeneration: 2,
                },
            ],
            [
                IPC_CHANNELS.composeCreationRuntime,
                {
                    ...authority,
                    jobId: "compose_01",
                    gamepackArtifactId: "artifact_gamepack_01",
                    assetInventoryArtifactId: "artifact_inventory_01",
                    assetpackArtifactId: "artifact_assetpack_01",
                    targetGrantId: "grant_output_01",
                    expectedTargetGrantGeneration: 2,
                },
            ],
            [
                IPC_CHANNELS.selectCreationRuntimeBundleOutput,
                {
                    workspaceId: "creation_workspace",
                },
            ],
            [
                IPC_CHANNELS.buildCreationRuntimeBundle,
                {
                    ...authority,
                    jobId: "bundle_01",
                    gamepackArtifactId: "artifact_gamepack_01",
                    assetInventoryArtifactId: "artifact_inventory_01",
                    assetpackArtifactId: "artifact_assetpack_01",
                    runtimeSnapshotArtifactId: "artifact_runtime_snapshot_01",
                    runtimeAdapterRegistryArtifactId:
                        "artifact_runtime_registry_01",
                    runtimeCompositionArtifactId:
                        "artifact_runtime_composition_01",
                    runtimeSupportReportArtifactId:
                        "artifact_runtime_support_01",
                    sourceGrantId: "grant_assetpack_01",
                    expectedSourceGrantGeneration: 3,
                    targetGrantId: "grant_bundle_01",
                    expectedTargetGrantGeneration: 1,
                },
            ],
            [
                IPC_CHANNELS.selectCreationMaterializationBundleOutput,
                {
                    workspaceId: "creation_workspace",
                },
            ],
            [
                IPC_CHANNELS.buildCreationMaterializationBundle,
                {
                    ...authority,
                    jobId: "materialization_01",
                    runtimeBundleArtifactId: "artifact_runtime_bundle_01",
                    sourceGrantId: "grant_runtime_bundle_01",
                    expectedSourceGrantGeneration: 2,
                    targetGrantId: "grant_materialization_01",
                    expectedTargetGrantGeneration: 1,
                },
            ],
            [
                IPC_CHANNELS.selectCreationStandaloneGameOutput,
                {
                    workspaceId: "creation_workspace",
                },
            ],
            [
                IPC_CHANNELS.materializeCreationGame,
                {
                    ...authority,
                    jobId: "standalone_01",
                    materializationBundleArtifactId:
                        "artifact_materialization_bundle_01",
                    sourceGrantId: "grant_materialization_01",
                    expectedSourceGrantGeneration: 2,
                    targetGrantId: "grant_standalone_01",
                    expectedTargetGrantGeneration: 1,
                },
            ],
            [
                IPC_CHANNELS.selectCreationGamePackageOutput,
                {
                    workspaceId: "creation_workspace",
                },
            ],
            [
                IPC_CHANNELS.packageCreationGame,
                {
                    ...authority,
                    jobId: "package_01",
                    standaloneGameArtifactId: "artifact_standalone_game_01",
                    sourceGrantId: "grant_standalone_01",
                    expectedSourceGrantGeneration: 2,
                    targetGrantId: "grant_package_01",
                    expectedTargetGrantGeneration: 1,
                },
            ],
            [
                IPC_CHANNELS.selectCreationGamePackageExtractionOutput,
                {
                    workspaceId: "creation_workspace",
                },
            ],
            [
                IPC_CHANNELS.extractCreationGamePackage,
                {
                    ...authority,
                    jobId: "extract_01",
                    gamePackageArtifactId: "artifact_game_package_01",
                    sourceGrantId: "grant_package_01",
                    expectedSourceGrantGeneration: 2,
                    targetGrantId: "grant_extracted_standalone_01",
                    expectedTargetGrantGeneration: 1,
                },
            ],
            [
                IPC_CHANNELS.reviewCreationAssetQa,
                {
                    workspaceId: "creation_workspace",
                    qaReportArtifactId: "artifact_qa_01",
                    outputRole: "texture",
                },
            ],
            [
                IPC_CHANNELS.authorizeCreationAssetRelease,
                {
                    workspaceId: "creation_workspace",
                    reviewReceiptArtifactIds: ["artifact_review_01"],
                    targetGrantId: "grant_assetpack_01",
                },
            ],
            [
                IPC_CHANNELS.selectCreationHeadlessEvidenceOutput,
                { workspaceId: "creation_workspace" },
            ],
            [
                IPC_CHANNELS.verifyCreationHeadless,
                {
                    workspaceId: "creation_workspace",
                    runtimeBundleArtifactId: "artifact_runtime_bundle_01",
                    sourceGrantId: "grant_runtime_bundle_01",
                    headlessScriptArtifactId: "artifact_script_01",
                    targetGrantId: "grant_headless_01",
                    platformId: "platform:linux_x86_64",
                },
            ],
            [
                IPC_CHANNELS.requestCreationJobCancel,
                {
                    workspaceId: "creation_workspace",
                    jobId: "job_review_01",
                },
            ],
            [
                IPC_CHANNELS.requestCreationJobRecovery,
                {
                    workspaceId: "creation_workspace",
                    jobId: "job_review_01",
                },
            ],
            [IPC_CHANNELS.getCreationJob, { jobId: "compile_01" }],
            [
                IPC_CHANNELS.listCreationJobs,
                {
                    workspaceId: "creation_workspace",
                    state: null,
                    afterSequence: 0,
                    limit: 25,
                },
            ],
            [
                IPC_CHANNELS.cancelCreationJob,
                {
                    jobId: "compile_01",
                    expectedGeneration: 0,
                    expectedRecordHash,
                },
            ],
            [
                IPC_CHANNELS.recoverCreationJob,
                {
                    jobId: "compile_01",
                    mode: "resume",
                    expectedGeneration: 1,
                    expectedRecordHash,
                },
            ],
            [
                IPC_CHANNELS.listCreationEvents,
                {
                    workspaceId: "creation_workspace",
                    afterId: 0,
                    limit: 64,
                },
            ],
        ]);
        expect(JSON.stringify(invoke.mock.calls)).not.toContain('"operation"');
        expect(JSON.stringify(invoke.mock.calls)).not.toContain('"path"');
        expect(JSON.stringify(invoke.mock.calls)).not.toContain('"kind"');
        expect(api).not.toHaveProperty("requestCreationJob");
    });

    it("exposes only pathless external grant and fixed external-job invocations", async () => {
        const invoke = vi.fn().mockResolvedValue({
            ok: false,
            error: { code: "not_found", message: "fixture" },
        });
        const api = createStudioApi({
            invoke,
            on: vi.fn(),
            removeListener: vi.fn(),
        });
        const hash = "a".repeat(64);

        await api.createExternalGrant({
            workspaceId: "workspace_01",
            operation: "game.materialize",
            role: "source",
            artifactKind: "game_materialization_bundle",
            expectedContentHash: hash,
        });
        await api.getExternalGrant("grant_source");
        await api.revokeExternalGrant("grant_source");
        await api.materializeGame({
            workspaceId: "workspace_01",
            sourceGrantId: "grant_source",
            targetGrantId: "grant_target",
            expectedMaterializationHash: hash,
        });
        await api.packageGame({
            workspaceId: "workspace_01",
            sourceGrantId: "grant_source",
            targetGrantId: "grant_target",
            expectedGameHash: hash,
        });
        await api.extractGamePackage({
            workspaceId: "workspace_01",
            sourceGrantId: "grant_source",
            targetGrantId: "grant_target",
            expectedPackageHash: hash,
        });
        await api.getExternalJob("job_01");
        await api.listExternalJobs({
            workspaceId: "workspace_01",
            state: "orphaned",
            limit: 10,
        });
        await api.cancelExternalJob("job_01");
        await api.recoverExternalJob("job_01", "rollback");

        expect(invoke.mock.calls).toEqual([
            [
                IPC_CHANNELS.createExternalGrant,
                {
                    workspaceId: "workspace_01",
                    operation: "game.materialize",
                    role: "source",
                    artifactKind: "game_materialization_bundle",
                    expectedContentHash: hash,
                },
            ],
            [IPC_CHANNELS.getExternalGrant, { grantId: "grant_source" }],
            [IPC_CHANNELS.revokeExternalGrant, { grantId: "grant_source" }],
            [
                IPC_CHANNELS.materializeGame,
                {
                    workspaceId: "workspace_01",
                    sourceGrantId: "grant_source",
                    targetGrantId: "grant_target",
                    expectedMaterializationHash: hash,
                },
            ],
            [
                IPC_CHANNELS.packageGame,
                {
                    workspaceId: "workspace_01",
                    sourceGrantId: "grant_source",
                    targetGrantId: "grant_target",
                    expectedGameHash: hash,
                },
            ],
            [
                IPC_CHANNELS.extractGamePackage,
                {
                    workspaceId: "workspace_01",
                    sourceGrantId: "grant_source",
                    targetGrantId: "grant_target",
                    expectedPackageHash: hash,
                },
            ],
            [IPC_CHANNELS.getExternalJob, { jobId: "job_01" }],
            [
                IPC_CHANNELS.listExternalJobs,
                {
                    workspaceId: "workspace_01",
                    state: "orphaned",
                    limit: 10,
                },
            ],
            [IPC_CHANNELS.cancelExternalJob, { jobId: "job_01" }],
            [
                IPC_CHANNELS.recoverExternalJob,
                { jobId: "job_01", action: "rollback" },
            ],
        ]);
        expect(JSON.stringify(invoke.mock.calls)).not.toContain('"path"');
        expect(api).not.toHaveProperty("selectPath");
        expect(api).not.toHaveProperty("dialog");
    });

    it("uses a separate fixed Codex event channel", () => {
        let wrapped: ((event: unknown, payload: unknown) => void) | undefined;
        const on: PreloadTransport["on"] = (_channel, listener) => {
            wrapped = listener;
        };
        const removeListener: PreloadTransport["removeListener"] = vi.fn();
        const transport: PreloadTransport = {
            invoke: vi.fn(),
            on,
            removeListener,
        };
        const listener = vi.fn();
        const unsubscribe = createStudioApi(transport).onCodexEvent(listener);
        wrapped?.({}, { type: "codex-status", status: { state: "unbound" } });
        wrapped?.({}, { type: "arbitrary" });
        expect(listener).toHaveBeenCalledTimes(1);
        unsubscribe();
        expect(removeListener).toHaveBeenCalledWith(
            IPC_CHANNELS.codexEvent,
            wrapped,
        );
    });

    it("subscribes and unsubscribes only on the fixed event channel", () => {
        let wrapped: ((event: unknown, payload: unknown) => void) | undefined;
        const on = vi.fn(
            (
                _channel: string,
                listener: (event: unknown, payload: unknown) => void,
            ) => {
                wrapped = listener;
            },
        );
        const removeListener = vi.fn(
            (
                channel: string,
                listener: (event: unknown, payload: unknown) => void,
            ) => {
                void channel;
                void listener;
            },
        );
        const transport: PreloadTransport = {
            invoke: vi.fn(),
            on,
            removeListener,
        };
        const listener = vi.fn();
        const unsubscribe = createStudioApi(transport).onEvent(listener);

        wrapped?.({}, { type: "service-stderr", text: "bounded" });
        wrapped?.({}, { type: "arbitrary-channel", value: true });
        expect(listener).toHaveBeenCalledTimes(1);
        unsubscribe();
        expect(removeListener).toHaveBeenCalledWith(
            IPC_CHANNELS.event,
            wrapped,
        );
    });
});

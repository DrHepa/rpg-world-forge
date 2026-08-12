import { describe, expect, it } from "vitest";

import { validateStudioEnvelope } from "../../src/main/protocol-validator";
import type {
    AssetCatalogInspectRequest,
    AssetCatalogInspectResponse,
    AssetCatalogListRequest,
    AssetCatalogListResponse,
    AssetPreviewOpenRequest,
    AssetPreviewReadResponse,
} from "../../src/generated/studio-protocol";
import type { Request as StudioV3Request } from "../../src/generated/studio-protocol-v3";
import type {
    Request as StudioV4Request,
    Response as StudioV4Response,
} from "../../src/generated/studio-protocol-v4";
import type {
    StudioJobCancelResponse,
    StudioJobCreateRequest,
    StudioJobCreateResponse,
    StudioSourceReadRequest,
    StudioSourceReadResponse,
} from "../../src/shared/studio-api";

const protocol = {
    protocol: "rpg-world-forge.studio_protocol",
    protocol_version: 1,
} as const;

describe("Studio protocol authoring discrimination", () => {
    it("accepts only closed pathless creation evidence and job v4 envelopes", () => {
        const request: StudioV4Request = {
            protocol: "rpg-world-forge.studio_protocol",
            protocol_version: 4,
            kind: "request",
            request_id: "artifact-list-1",
            method: "creation_artifact.list",
            params: {
                workspace_id: "workspace_01",
                expected_root_generation: 3,
                expected_source_revision: "a".repeat(64),
                expected_workflow_status_hash: "b".repeat(64),
                expected_artifact_snapshot_hash: null,
                lifecycle: "active",
                cursor: null,
                limit: 32,
            },
        };
        const initialize = {
            protocol: "rpg-world-forge.studio_protocol",
            protocol_version: 4,
            kind: "response",
            request_id: "evidence-initialize-1",
            method: "service.initialize",
            result: {
                service: "world-forge.studio",
                service_version: 4,
                protocol: "rpg-world-forge.studio_protocol",
                protocol_version: 4,
                methods: [
                    "service.initialize",
                    "creation_artifact.list",
                    "creation_artifact.inspect",
                    "creation_evidence.inspect",
                    "creation_output_grant.create",
                    "creation_output_grant.get",
                    "creation_output_grant.list",
                    "creation_output_grant.revoke",
                    "creation_preview.open",
                    "creation_preview.read",
                    "creation_preview.close",
                    "creation_job.create",
                    "creation_job.get",
                    "creation_job.list",
                    "creation_job.cancel",
                    "creation_job.recover",
                    "creation_event.list",
                ],
                capabilities: {
                    creation_evidence_projection: true,
                    creation_jobs: true,
                    creation_output_grants: true,
                    creation_runtime_compose: true,
                    creation_runtime_bundle: true,
                    creation_materialization_bundle: true,
                    creation_asset_previews: true,
                    game_packaging: true,
                    game_package_extraction: true,
                    asset_previews: false,
                    materialization_execution: true,
                },
            },
        } satisfies StudioV4Response;
        const compileRequest: StudioV4Request = {
            protocol: "rpg-world-forge.studio_protocol",
            protocol_version: 4,
            kind: "request",
            request_id: "compile-1",
            method: "creation_job.create",
            params: {
                workspace_id: "workspace_01",
                operation: "creation.compile",
                expected_root_generation: 3,
                expected_source_revision: "a".repeat(64),
                expected_workflow_status_hash: "b".repeat(64),
                expected_artifact_snapshot_hash: "c".repeat(64),
            },
        };
        const admissionRequest: StudioV4Request = {
            ...compileRequest,
            request_id: "admit-1",
            params: {
                ...compileRequest.params,
                operation: "artifact.admit",
                document: {
                    format: "world-forge.game_analysis",
                    format_version: 1,
                    content_hash: "d".repeat(64),
                },
                dependency_artifact_ids: ["artifact_01"],
            },
        };
        const assetProcessRequest: StudioV4Request = {
            ...compileRequest,
            request_id: "asset-process-1",
            params: {
                ...compileRequest.params,
                operation: "asset.process",
                license_artifact_ids: ["artifact_license_01"],
                recipe_id: "board_ui_recipe",
                processing_receipt_id: "board_ui_processing_receipt",
                qa_report_id: "board_ui_qa",
                acceptance_results: [
                    {
                        criterion_index: 0,
                        criterion_sha256: "e".repeat(64),
                        status: "passed",
                        evidence_hashes: ["f".repeat(64)],
                    },
                ],
            },
        };

        expect(validateStudioEnvelope(request)).toBe(true);
        expect(validateStudioEnvelope(initialize)).toBe(true);
        expect(validateStudioEnvelope(compileRequest)).toBe(true);
        expect(validateStudioEnvelope(admissionRequest)).toBe(true);
        expect(validateStudioEnvelope(assetProcessRequest)).toBe(true);
        const acceptanceResults = (count: number, evidenceCount = 1) =>
            Array.from({ length: count }, (_, index) => ({
                criterion_index: index,
                criterion_sha256: (index + 1)
                    .toString(16)
                    .padStart(64, "0"),
                status: "passed" as const,
                evidence_hashes: Array.from(
                    { length: evidenceCount },
                    (_unused, evidenceIndex) =>
                        (evidenceIndex + 1)
                            .toString(16)
                            .padStart(64, "0"),
                ),
            }));
        expect(
            validateStudioEnvelope({
                ...assetProcessRequest,
                params: {
                    ...assetProcessRequest.params,
                    acceptance_results: acceptanceResults(64),
                },
            }),
        ).toBe(true);
        expect(
            validateStudioEnvelope({
                ...assetProcessRequest,
                params: {
                    ...assetProcessRequest.params,
                    acceptance_results: acceptanceResults(65),
                },
            }),
        ).toBe(false);
        expect(
            validateStudioEnvelope({
                ...assetProcessRequest,
                params: {
                    ...assetProcessRequest.params,
                    acceptance_results: acceptanceResults(1, 64),
                },
            }),
        ).toBe(true);
        expect(
            validateStudioEnvelope({
                ...assetProcessRequest,
                params: {
                    ...assetProcessRequest.params,
                    acceptance_results: acceptanceResults(1, 65),
                },
            }),
        ).toBe(false);
        expect(
            validateStudioEnvelope({
                ...assetProcessRequest,
                params: { ...assetProcessRequest.params, provider: "remote" },
            }),
        ).toBe(false);
        expect(
            validateStudioEnvelope({
                ...request,
                params: {
                    ...request.params,
                    path: "/private/project/.worldforge",
                },
            }),
        ).toBe(false);
        expect(
            validateStudioEnvelope({ ...request, protocol_version: 3 }),
        ).toBe(false);
        expect(
            validateStudioEnvelope({
                ...compileRequest,
                params: { ...compileRequest.params, path: "/private/project" },
            }),
        ).toBe(false);
        const initializeResult = initialize.result;
        expect(
            validateStudioEnvelope({
                ...initialize,
                result: {
                    ...initializeResult,
                    capabilities: {
                        ...initializeResult.capabilities,
                        materialization_execution: false,
                    },
                },
            }),
        ).toBe(false);
    });

    it("keeps generic creation v3 closed and isolated from legacy protocols", () => {
        const request: StudioV3Request = {
            protocol: "rpg-world-forge.studio_protocol",
            protocol_version: 3,
            kind: "request",
            request_id: "creation-read-1",
            method: "creation_document.read",
            params: {
                workspace_id: "workspace_01",
                expected_source_revision: "a".repeat(64),
                path: "profile.json",
            },
        };
        expect(validateStudioEnvelope(request)).toBe(true);
        expect(
            validateStudioEnvelope({
                ...request,
                params: { ...request.params, native_path: "/private/project" },
            }),
        ).toBe(false);
        expect(
            validateStudioEnvelope({ ...request, protocol_version: 2 }),
        ).toBe(false);
    });

    it("enforces kind-aware creation facets in the public v3 schema", () => {
        const gameRequest = {
            protocol: "rpg-world-forge.studio_protocol",
            protocol_version: 3,
            kind: "request",
            request_id: "create-game-1",
            method: "creation_workspace.create",
            params: {
                workspace_id: "workspace_01",
                grant_id: "grant_01",
                expected_grant_generation: 0,
                project_kind: "game",
                project_id: "neutral_game",
                title: "Neutral game",
                default_locale: "en",
                project_version: "0.1.0",
                gameplay_family: "puzzle",
                initial_core_verb: "solve",
                initial_core_loop: "inspect and solve",
                world_presence: "none",
                narrative_requirement: "none",
                narrative_authorship: "none",
                narrative_topology: "none",
                presentation_mode: "2d",
                runtime_support_intent: "authoring_only",
            },
        } as const;

        expect(validateStudioEnvelope(gameRequest)).toBe(true);
        const missingFacetParams: Record<string, unknown> = {
            ...gameRequest.params,
        };
        delete missingFacetParams.presentation_mode;
        expect(
            validateStudioEnvelope({
                ...gameRequest,
                params: missingFacetParams,
            }),
        ).toBe(false);
        expect(
            validateStudioEnvelope({
                ...gameRequest,
                params: {
                    ...gameRequest.params,
                    initial_core_verb: "solve-action",
                },
            }),
        ).toBe(false);
        expect(
            validateStudioEnvelope({
                ...gameRequest,
                params: {
                    ...gameRequest.params,
                    initial_core_loop: "   ",
                },
            }),
        ).toBe(false);
        expect(
            validateStudioEnvelope({
                ...gameRequest,
                params: {
                    ...gameRequest.params,
                    narrative_authorship: "authored",
                },
            }),
        ).toBe(false);
        expect(
            validateStudioEnvelope({
                ...gameRequest,
                params: {
                    ...gameRequest.params,
                    project_kind: "asset_library",
                },
            }),
        ).toBe(false);
    });

    it("discriminates closed v3 changeset and inline phase requests and results", () => {
        const authority = {
            workspace_id: "workspace_01",
            expected_root_generation: 0,
            expected_source_revision: "a".repeat(64),
            expected_workflow_status_hash: "b".repeat(64),
        } as const;
        const phaseRequest = {
            protocol: "rpg-world-forge.studio_protocol",
            protocol_version: 3,
            kind: "request",
            request_id: "phase-complete",
            method: "creation_phase.complete",
            params: { ...authority, report: {}, artifact_registry: [] },
        } as const;
        expect(validateStudioEnvelope(phaseRequest)).toBe(true);
        expect(
            validateStudioEnvelope({
                ...phaseRequest,
                params: {
                    ...phaseRequest.params,
                    expected_workflow_status_hash: null,
                },
            }),
        ).toBe(false);
        expect(
            validateStudioEnvelope({
                ...phaseRequest,
                method: "creation_phase.reopen",
                params: {
                    ...authority,
                    expected_workflow_status_hash: null,
                    phase_id: "p00_brief",
                    reason: "Requirements changed",
                    approved_by: "lead_reviewer",
                },
            }),
        ).toBe(false);
        expect(
            validateStudioEnvelope({
                ...phaseRequest,
                params: {
                    ...phaseRequest.params,
                    report_path: "/private/report.json",
                },
            }),
        ).toBe(false);
        expect(
            validateStudioEnvelope({
                ...phaseRequest,
                method: "creation_changeset.approve",
                params: {
                    changeset_id: "changeset_01",
                    expected_record_hash: "c".repeat(64),
                },
            }),
        ).toBe(false);

        const changeset = {
            format: "world-forge.studio_creation_changeset",
            format_version: 1,
            changeset_id: "changeset_01",
            workspace_id: "workspace_01",
            status: "staged",
            expected_root_generation: 0,
            expected_source_revision: "a".repeat(64),
            proposed_source_revision: "c".repeat(64),
            expected_workflow_status_hash: "b".repeat(64),
            review_sha256: "d".repeat(64),
            operations: [
                {
                    operation: "replace",
                    path: "project.json",
                    expected_base_file_sha256: "e".repeat(64),
                    expected_base_size: 10,
                    proposed_file_sha256: "f".repeat(64),
                    proposed_size: 11,
                },
            ],
            created_at: "2026-08-01T00:00:00Z",
            updated_at: "2026-08-01T00:00:00Z",
            record_hash: "0".repeat(64),
        } as const;
        const getResponse = {
            protocol: "rpg-world-forge.studio_protocol",
            protocol_version: 3,
            kind: "response",
            request_id: "changeset-get",
            method: "creation_changeset.get",
            result: { changeset },
        } as const;
        expect(validateStudioEnvelope(getResponse)).toBe(true);
        expect(
            validateStudioEnvelope({
                ...getResponse,
                method: "creation_changeset.list",
            }),
        ).toBe(false);
        expect(
            validateStudioEnvelope({
                ...getResponse,
                result: {
                    changeset: {
                        ...changeset,
                        native_path: "/private/project",
                    },
                },
            }),
        ).toBe(false);
    });

    it("closes preview authority and enforces canonical bounded chunks", () => {
        const open: AssetPreviewOpenRequest = {
            ...protocol,
            kind: "request",
            request_id: "preview-open",
            method: "asset.preview.open",
            params: {
                workspace_id: "workspace_01",
                manifest_revision: "a".repeat(64),
                entry_id: `asset_${"b".repeat(64)}`,
            },
        };
        const read: AssetPreviewReadResponse = {
            ...protocol,
            kind: "response",
            request_id: "preview-read",
            method: "asset.preview.read",
            result: {
                handle: "C".repeat(43),
                sequence: 0,
                data_base64: Buffer.from("abc").toString("base64"),
                byte_length: 3,
                cumulative_bytes: 3,
                cumulative_sha256: "d".repeat(64),
                eof: true,
            },
        };

        expect(validateStudioEnvelope(open)).toBe(true);
        expect(validateStudioEnvelope(read)).toBe(true);
        expect(
            validateStudioEnvelope({
                ...open,
                params: { ...open.params, path: "/private/preview.png" },
            }),
        ).toBe(false);
        for (const result of [
            { ...read.result, data_base64: "YR==" },
            { ...read.result, data_base64: "%%==" },
            { ...read.result, data_base64: "" },
            { ...read.result, byte_length: 2 },
            { ...read.result, cumulative_bytes: 4 },
            { ...read.result, sequence: 1 },
            { ...read.result, eof: false },
            { ...read.result, payload: "YWJj" },
        ]) {
            expect(validateStudioEnvelope({ ...read, result })).toBe(false);
        }
    });

    it("accepts bounded creation-preview chunks without exposing native paths", () => {
        const response = {
            ...protocol,
            protocol_version: 4,
            kind: "response",
            request_id: "creation-preview-read-1",
            method: "creation_preview.read",
            result: {
                handle: "B".repeat(43),
                sequence: 0,
                data_base64: "cHJldmlldw==",
                byte_length: 7,
                cumulative_bytes: 7,
                cumulative_sha256: "a".repeat(64),
                eof: true,
            },
        } as const;
        expect(validateStudioEnvelope(response)).toBe(true);
        expect(
            validateStudioEnvelope({
                ...response,
                result: {
                    ...response.result,
                    path: "/private/assets/board.png",
                },
            }),
        ).toBe(false);
        expect(
            validateStudioEnvelope({
                ...response,
                result: { ...response.result, data_base64: "cHJldmlldw" },
            }),
        ).toBe(false);
        expect(
            validateStudioEnvelope({
                ...response,
                result: { ...response.result, byte_length: 6 },
            }),
        ).toBe(false);
    });

    it("closes revision-bound asset catalog requests", () => {
        const firstPage: AssetCatalogListRequest = {
            ...protocol,
            kind: "request",
            request_id: "assets-1",
            method: "asset.catalog.list",
            params: { workspace_id: "workspace_01", limit: 64 },
        };
        const laterPage: AssetCatalogListRequest = {
            ...firstPage,
            request_id: "assets-2",
            params: {
                workspace_id: "workspace_01",
                offset: 64,
                limit: 64,
                expected_manifest_revision: "a".repeat(64),
            },
        };
        const inspect: AssetCatalogInspectRequest = {
            ...protocol,
            kind: "request",
            request_id: "asset-inspect-1",
            method: "asset.catalog.inspect",
            params: {
                workspace_id: "workspace_01",
                entry_id: `asset_${"b".repeat(64)}`,
                expected_manifest_revision: "a".repeat(64),
            },
        };

        expect(validateStudioEnvelope(firstPage)).toBe(true);
        expect(validateStudioEnvelope(laterPage)).toBe(true);
        expect(validateStudioEnvelope(inspect)).toBe(true);
        expect(
            validateStudioEnvelope({
                ...laterPage,
                params: { workspace_id: "workspace_01", offset: 64, limit: 64 },
            }),
        ).toBe(false);
        expect(
            validateStudioEnvelope({
                ...firstPage,
                params: {
                    ...firstPage.params,
                    path: "assets/renderpack/manifest.json",
                },
            }),
        ).toBe(false);
        expect(
            validateStudioEnvelope({
                ...inspect,
                params: {
                    ...inspect.params,
                    entry_id: "assets/renderpack/manifest.json",
                },
            }),
        ).toBe(false);
    });

    it("accepts only closed metadata-only asset catalog responses", () => {
        const entry = {
            entry_id: `asset_${"b".repeat(64)}`,
            asset_id: "neutral_sheet",
            category: "runtime_output",
            role: "texture",
            path: "assets/renderpack/processed/neutral_sheet/neutral_sheet.png",
            sha256: "c".repeat(64),
            media_type: "image/png",
            selected: false,
            inspectable: true,
        } as const;
        const list: AssetCatalogListResponse = {
            ...protocol,
            kind: "response",
            request_id: "assets-1",
            method: "asset.catalog.list",
            result: {
                manifest_revision: "a".repeat(64),
                offset: 0,
                limit: 64,
                entries: [entry],
                next_offset: null,
            },
        };
        const inspect: AssetCatalogInspectResponse = {
            ...protocol,
            kind: "response",
            request_id: "asset-inspect-1",
            method: "asset.catalog.inspect",
            result: {
                manifest_revision: "a".repeat(64),
                entry,
                inspection: {
                    kind: "png",
                    width: 32,
                    height: 16,
                    bit_depth: 8,
                    color_type: 6,
                    interlaced: false,
                },
            },
        };

        expect(validateStudioEnvelope(list)).toBe(true);
        expect(validateStudioEnvelope(inspect)).toBe(true);
        expect(
            validateStudioEnvelope({
                ...list,
                result: { ...list.result, total: 1 },
            }),
        ).toBe(false);
        expect(
            validateStudioEnvelope({
                ...list,
                result: {
                    ...list.result,
                    entries: [{ ...entry, path: "/absolute/private.png" }],
                },
            }),
        ).toBe(false);
        expect(
            validateStudioEnvelope({
                ...inspect,
                result: {
                    ...inspect.result,
                    inspection: {
                        ...inspect.result.inspection,
                        bytes: "forbidden",
                    },
                },
            }),
        ).toBe(false);
    });

    it("enforces portable asset paths and UTF-8 byte limits", () => {
        const entry = {
            entry_id: `asset_${"b".repeat(64)}`,
            asset_id: "neutral_sheet",
            category: "runtime_output",
            role: "texture",
            path: "assets/renderpack/processed/neutral_sheet/neutral_sheet.png",
            sha256: "c".repeat(64),
            media_type: "image/png",
            selected: false,
            inspectable: true,
        } as const;
        const list = {
            ...protocol,
            kind: "response",
            request_id: "asset-path",
            method: "asset.catalog.list",
            result: {
                manifest_revision: "a".repeat(64),
                offset: 0,
                limit: 64,
                entries: [entry],
                next_offset: null,
            },
        } as const;

        for (const path of [
            "assets/../private.png",
            Array.from(
                { length: 33 },
                (_, index) => `part-${String(index)}`,
            ).join("/"),
            "assets/cafe\u0301.png",
        ]) {
            expect(
                validateStudioEnvelope({
                    ...list,
                    result: { ...list.result, entries: [{ ...entry, path }] },
                }),
            ).toBe(false);
        }

        const oversizedValue = { text: "é".repeat(200_000) };
        for (const inspection of [
            {
                kind: "json",
                encoding: "utf-8",
                content: JSON.stringify(oversizedValue),
                value: oversizedValue,
            },
            {
                kind: "glsl",
                encoding: "utf-8",
                content: "é".repeat(200_000),
            },
        ]) {
            expect(
                validateStudioEnvelope({
                    ...protocol,
                    kind: "response",
                    request_id: `asset-${inspection.kind}`,
                    method: "asset.catalog.inspect",
                    result: {
                        manifest_revision: "a".repeat(64),
                        entry,
                        inspection,
                    },
                }),
            ).toBe(false);
        }
    });

    it("requires the exact source.read request params", () => {
        const valid: StudioSourceReadRequest = {
            ...protocol,
            kind: "request",
            request_id: "read-1",
            method: "source.read",
            params: { workspace_id: "workspace_01", path: "source/world.json" },
        };

        expect(validateStudioEnvelope(valid)).toBe(true);
        expect(validateStudioEnvelope({ ...valid, params: {} })).toBe(false);
        expect(
            validateStudioEnvelope({
                ...valid,
                params: {
                    workspace_id: "workspace_01",
                    path: "source/../project.json",
                },
            }),
        ).toBe(false);
    });

    it("accepts only the closed read-only job.create operations", () => {
        const valid: StudioJobCreateRequest = {
            ...protocol,
            kind: "request",
            request_id: "job-1",
            method: "job.create",
            params: {
                workspace_id: "workspace_01",
                operation: "runtime.headless",
                input: { worldpack: "build/worldpack.json", ticks: 0 },
            },
        };

        expect(validateStudioEnvelope(valid)).toBe(true);
        expect(
            validateStudioEnvelope({
                ...valid,
                params: {
                    workspace_id: "workspace_01",
                    operation: "runtime.headless",
                    input: { worldpack: "build/worldpack.json", ticks: -1 },
                },
            }),
        ).toBe(false);
        expect(
            validateStudioEnvelope({
                ...valid,
                params: {
                    workspace_id: "workspace_01",
                    operation: "shell.execute",
                    input: { command: "echo unsafe" },
                },
            }),
        ).toBe(false);
    });

    it("creates managed v2 jobs while retaining legacy v1 cancel responses", () => {
        const managedJob = {
            format: "rpg-world-forge.studio_job",
            format_version: 2,
            job_id: "job_01",
            workspace_id: "workspace_01",
            operation: "runtime.headless",
            state: "queued",
            input: { worldpack: "build/world.json", ticks: 0 },
            result: null,
            error: null,
            created_at: "2026-07-22T12:00:00Z",
            updated_at: "2026-07-22T12:00:00Z",
        } as const;
        const createResponse: StudioJobCreateResponse = {
            ...protocol,
            kind: "response",
            request_id: "job-1",
            method: "job.create",
            result: { job: managedJob },
        };
        expect(validateStudioEnvelope(createResponse)).toBe(true);

        const legacyJob = {
            ...managedJob,
            format_version: 1,
            operation: "runtime.headless",
            input: { legacy_command: "headless --old-contract" },
        } as const;
        expect(
            validateStudioEnvelope({
                ...createResponse,
                result: { job: legacyJob },
            }),
        ).toBe(false);
        const cancelResponse: StudioJobCancelResponse = {
            ...protocol,
            kind: "response",
            request_id: "cancel-1",
            method: "job.cancel",
            result: { job: legacyJob },
        };
        expect(validateStudioEnvelope(cancelResponse)).toBe(true);
    });

    it("requires source.read responses to name the method and exact result", () => {
        const valid: StudioSourceReadResponse = {
            ...protocol,
            kind: "response",
            request_id: "read-1",
            method: "source.read",
            result: {
                document: {
                    path: "source/world.json",
                    kind: "world",
                    size: 3,
                    sha256: "0".repeat(64),
                    encoding: "utf-8",
                    content: "{}\n",
                    json: {},
                },
            },
        };

        expect(validateStudioEnvelope(valid)).toBe(true);
        const missingMethod: Record<string, unknown> = { ...valid };
        delete missingMethod.method;
        expect(validateStudioEnvelope(missingMethod)).toBe(false);
        expect(
            validateStudioEnvelope({ ...valid, method: "source.list" }),
        ).toBe(false);
        expect(
            validateStudioEnvelope({ ...valid, result: { documents: [] } }),
        ).toBe(false);
    });

    it("closes every changeset request and response including immutable diffs", () => {
        const operation = {
            path: "source/lore/entry.md",
            operation: "replace",
            base_sha256: "a".repeat(64),
            base_size: 4,
            proposed_sha256: "b".repeat(64),
            size: 4,
        } as const;
        const changeset = {
            format: "rpg-world-forge.studio_changeset",
            format_version: 2,
            changeset_id: "changeset_01",
            workspace_id: "workspace_01",
            status: "staged",
            operations: [operation],
            review_sha256: "c".repeat(64),
            created_at: "2026-07-23T00:00:00Z",
            updated_at: "2026-07-23T00:00:00Z",
        } as const;
        const create = {
            ...protocol,
            kind: "request",
            request_id: "stage-1",
            method: "changeset.create",
            params: {
                workspace_id: "workspace_01",
                operations: [
                    {
                        path: "source/lore/entry.md",
                        operation: "replace",
                        expected_base_sha256: "a".repeat(64),
                        content: "new\n",
                    },
                ],
            },
        } as const;
        expect(validateStudioEnvelope(create)).toBe(true);
        expect(
            validateStudioEnvelope({
                ...create,
                params: { ...create.params, cwd: "/tmp" },
            }),
        ).toBe(false);
        expect(
            validateStudioEnvelope({
                ...create,
                params: {
                    ...create.params,
                    operations: [
                        {
                            ...create.params.operations[0],
                            operation: "execute",
                        },
                    ],
                },
            }),
        ).toBe(false);

        const ids = ["changeset.get", "changeset.diff"] as const;
        for (const method of ids) {
            expect(
                validateStudioEnvelope({
                    ...protocol,
                    kind: "request",
                    request_id: method,
                    method,
                    params: { changeset_id: "changeset_01" },
                }),
            ).toBe(true);
        }
        expect(
            validateStudioEnvelope({
                ...protocol,
                kind: "request",
                request_id: "list-1",
                method: "changeset.list",
                params: {
                    workspace_id: "workspace_01",
                    status: "applying",
                    limit: 1,
                },
            }),
        ).toBe(true);
        for (const method of [
            "changeset.approve",
            "changeset.reject",
            "changeset.apply",
        ] as const) {
            expect(
                validateStudioEnvelope({
                    ...protocol,
                    kind: "request",
                    request_id: method,
                    method,
                    params: {
                        changeset_id: "changeset_01",
                        expected_review_sha256: "c".repeat(64),
                    },
                }),
            ).toBe(true);
        }

        const getResponse = {
            ...protocol,
            kind: "response",
            request_id: "get-1",
            method: "changeset.get",
            result: { changeset },
        } as const;
        expect(validateStudioEnvelope(getResponse)).toBe(true);
        const withoutReview: Record<string, unknown> = { ...changeset };
        delete withoutReview.review_sha256;
        expect(
            validateStudioEnvelope({
                ...getResponse,
                result: { changeset: withoutReview },
            }),
        ).toBe(false);
        expect(
            validateStudioEnvelope({
                ...getResponse,
                result: { changeset: { ...changeset, provider: "openai" } },
            }),
        ).toBe(false);
        const legacyChangeset = {
            ...withoutReview,
            format_version: 1,
            operations: [
                {
                    path: operation.path,
                    operation: operation.operation,
                    base_sha256: operation.base_sha256,
                    proposed_sha256: operation.proposed_sha256,
                    size: operation.size,
                },
            ],
        } as const;
        expect(
            validateStudioEnvelope({
                ...getResponse,
                result: { changeset: legacyChangeset },
            }),
        ).toBe(true);
        expect(
            validateStudioEnvelope({
                ...getResponse,
                result: {
                    changeset: { ...legacyChangeset, provider: "openai" },
                },
            }),
        ).toBe(false);
        expect(
            validateStudioEnvelope({
                ...getResponse,
                result: { changeset: { ...changeset, format_version: 3 } },
            }),
        ).toBe(false);

        const diffResponse = {
            ...protocol,
            kind: "response",
            request_id: "diff-1",
            method: "changeset.diff",
            result: {
                diff: {
                    changeset_id: "changeset_01",
                    changeset_format_version: 2,
                    available: true,
                    unavailable_reason: null,
                    review_sha256: "c".repeat(64),
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
        } as const;
        expect(validateStudioEnvelope(diffResponse)).toBe(true);
        expect(
            validateStudioEnvelope({
                ...diffResponse,
                result: {
                    diff: {
                        ...diffResponse.result.diff,
                        changeset_format_version: 1,
                    },
                },
            }),
        ).toBe(false);
        expect(
            validateStudioEnvelope({
                ...diffResponse,
                result: {
                    diff: {
                        ...diffResponse.result.diff,
                        operations: [
                            {
                                ...diffResponse.result.diff.operations[0],
                                operation: "execute",
                            },
                        ],
                    },
                },
            }),
        ).toBe(false);
    });

    it("validates the closed external artifact protocol v2 independently from v1", () => {
        const v2 = {
            protocol: "rpg-world-forge.studio_protocol",
            protocol_version: 2,
        } as const;
        const createGrant = {
            ...v2,
            kind: "request",
            request_id: "grant-create",
            method: "external_grant.create",
            params: {
                grant_id: "grant_source",
                workspace_id: "workspace_01",
                operation: "game.materialize",
                role: "source",
                artifact_kind: "game_materialization_bundle",
                display_name: "Materialization source",
                path: "/private/materialization",
                expected_content_hash: "a".repeat(64),
            },
        } as const;
        expect(validateStudioEnvelope(createGrant)).toBe(true);
        expect(
            validateStudioEnvelope({ ...createGrant, protocol_version: 1 }),
        ).toBe(false);
        expect(
            validateStudioEnvelope({
                ...createGrant,
                method: "world.validate",
                params: { workspace_id: "workspace_01" },
            }),
        ).toBe(false);

        const grant = {
            format: "rpg-world-forge.studio_external_grant",
            format_version: 1,
            grant_id: "grant_source",
            workspace_id: "workspace_01",
            operation: "game.materialize",
            role: "source",
            artifact_kind: "game_materialization_bundle",
            display_name: "Materialization source",
            state: "ready",
            expected_content_hash: "a".repeat(64),
            created_at: "2026-07-30T12:00:00Z",
            updated_at: "2026-07-30T12:00:00Z",
        } as const;
        const grantResponse = {
            ...v2,
            kind: "response",
            request_id: "grant-create",
            method: "external_grant.create",
            result: { grant },
        } as const;
        expect(validateStudioEnvelope(grantResponse)).toBe(true);
        expect(
            validateStudioEnvelope({
                ...grantResponse,
                result: {
                    grant: { ...grant, path: "/private/materialization" },
                },
            }),
        ).toBe(false);

        const job = {
            format: "rpg-world-forge.studio_job",
            format_version: 3,
            job_id: "job_01",
            workspace_id: "workspace_01",
            operation: "game.materialize",
            state: "queued",
            input: {
                source_grant_id: "grant_source",
                target_grant_id: "grant_target",
                expected_materialization_hash: "a".repeat(64),
            },
            result: null,
            error: null,
            created_at: "2026-07-30T12:00:00Z",
            updated_at: "2026-07-30T12:00:00Z",
        } as const;
        const jobResponse = {
            ...v2,
            kind: "response",
            request_id: "job-create",
            method: "job.create",
            result: { job },
        } as const;
        expect(validateStudioEnvelope(jobResponse)).toBe(true);
        expect(
            validateStudioEnvelope({
                ...jobResponse,
                result: { job: { ...job, state: "awaiting_user" } },
            }),
        ).toBe(false);
    });
});

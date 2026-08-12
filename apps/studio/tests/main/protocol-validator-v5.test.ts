import { describe, expect, it } from "vitest";

import { validateStudioEnvelope } from "../../src/main/protocol-validator";
import type {
    InitializeResult,
    JobCreateParams,
    Request,
    Response,
} from "../../src/generated/studio-protocol-v5";
import { STUDIO_V5_METHODS } from "../../src/shared/studio-api";

const authority = {
  workspace_id: "workspace_01",
  expected_root_generation: 3,
  expected_source_revision: "a".repeat(64),
  expected_workflow_status_hash: "b".repeat(64),
  expected_artifact_snapshot_hash: "c".repeat(64),
} as const;

const headlessLineageArtifactFields = [
  "gamepack_artifact_id",
  "asset_inventory_artifact_id",
  "assetpack_artifact_id",
  "asset_release_authority_artifact_id",
  "runtime_snapshot_artifact_id",
  "runtime_adapter_registry_artifact_id",
  "runtime_composition_artifact_id",
  "runtime_bundle_artifact_id",
  "headless_script_artifact_id",
] as const;

const requests: Request[] = [
  {
    protocol: "rpg-world-forge.studio_protocol",
    protocol_version: 5,
    kind: "request",
    request_id: "review_01",
    method: "creation_job.create",
    params: {
      ...authority,
      operation: "asset.qa.review",
      qa_report_artifact_id: "artifact_qa_01",
      output_role: "texture",
      review_receipt_id: "review_receipt_01",
      decisions: ["approved", "rejected"],
      blockers: ["criterion_rejected"],
    },
  },
  {
    protocol: "rpg-world-forge.studio_protocol",
    protocol_version: 5,
    kind: "request",
    request_id: "release_01",
    method: "creation_job.create",
    params: {
      ...authority,
      operation: "asset.release.authorize",
      review_receipt_artifact_ids: ["artifact_review_01"],
      manifest_id: "manifest_01",
      assetpack_id: "assetpack_01",
      release_authority_id: "release_authority_01",
      blockers: [],
      target_grant_id: "grant_assetpack_01",
      expected_target_grant_generation: 3,
    },
  },
  {
    protocol: "rpg-world-forge.studio_protocol",
    protocol_version: 5,
    kind: "request",
    request_id: "headless_01",
    method: "creation_job.create",
    params: {
      ...authority,
      operation: "runtime.headless.verify",
      gamepack_artifact_id: "artifact_gamepack_01",
      asset_inventory_artifact_id: "artifact_inventory_01",
      assetpack_artifact_id: "artifact_assetpack_01",
      asset_release_authority_artifact_id: "artifact_release_authority_01",
      runtime_snapshot_artifact_id: "artifact_runtime_snapshot_01",
      runtime_adapter_registry_artifact_id: "artifact_runtime_registry_01",
      runtime_composition_artifact_id: "artifact_runtime_composition_01",
      runtime_bundle_artifact_id: "artifact_runtime_bundle_01",
      headless_script_artifact_id: "artifact_headless_script_01",
      source_grant_id: "grant_runtime_bundle_01",
      expected_source_grant_generation: 2,
      target_grant_id: "grant_headless_evidence_01",
      expected_target_grant_generation: 0,
      platform_id: "platform:linux_x86_64",
    },
  },
];

const qaPreviewRequest = {
  protocol: "rpg-world-forge.studio_protocol",
  protocol_version: 5,
  kind: "request",
  request_id: "preview_qa_01",
  method: "creation_preview.open",
  params: {
    source_kind: "qa_review_candidate",
    workspace_id: "workspace_01",
    expected_root_generation: 3,
    expected_source_revision: "a".repeat(64),
    expected_workflow_status_hash: "b".repeat(64),
    expected_artifact_snapshot_hash: "c".repeat(64),
    qa_report_artifact_id: "artifact_qa_01",
    asset_id: "board_ui",
    output_role: "texture",
  },
} as const;

describe("Studio protocol v5 authority shell", () => {

  it("accepts additive v5 workspace create asset content mode without widening v3", () => {
    const request = {
      protocol: "rpg-world-forge.studio_protocol",
      protocol_version: 5,
      kind: "request",
      request_id: "workspace_create_asset_mode",
      method: "creation_workspace.create",
      params: {
        workspace_id: "workspace_game",
        grant_id: "grant_game",
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
        asset_content_mode: "not_applicable",
      },
    } as const;

    expect(validateStudioEnvelope(request)).toBe(true);
    expect(validateStudioEnvelope({ ...request, protocol_version: 3 })).toBe(false);
    expect(validateStudioEnvelope({ ...request, protocol_version: 4 })).toBe(false);
    expect(
      validateStudioEnvelope({
        ...request,
        params: { ...request.params, asset_content_mode: "unknown" },
      }),
    ).toBe(false);
    expect(
      validateStudioEnvelope({
        ...request,
        params: {
          workspace_id: "workspace_library",
          grant_id: "grant_library",
          expected_grant_generation: 0,
          project_kind: "universe_library",
          project_id: "neutral_library",
          title: "Neutral library",
          default_locale: "en",
          project_version: "0.1.0",
          asset_content_mode: "authored",
        },
      }),
    ).toBe(false);
  });

  it("accepts omitted v5 workspace create asset mode for authored-default compatibility", () => {
    expect(
      validateStudioEnvelope({
        protocol: "rpg-world-forge.studio_protocol",
        protocol_version: 5,
        kind: "request",
        request_id: "workspace_create_default_asset_mode",
        method: "creation_workspace.create",
        params: {
          workspace_id: "workspace_game",
          grant_id: "grant_game",
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
      }),
    ).toBe(true);
  });

  it("accepts only the pathless pre-release QA preview selector", () => {
    expect(validateStudioEnvelope(qaPreviewRequest)).toBe(true);
    expect(
      validateStudioEnvelope({ ...qaPreviewRequest, protocol_version: 4 }),
    ).toBe(false);

    for (const forbidden of [
      "path",
      "runtime_path",
      "sha256",
      "content_hash",
      "status",
      "evidence",
      "decision",
      "command",
      "provider",
      "env",
      "output_grant_id",
      "expected_output_grant_generation",
    ]) {
      expect(
        validateStudioEnvelope({
          ...qaPreviewRequest,
          params: {
            ...qaPreviewRequest.params,
            [forbidden]: "renderer-controlled",
          },
        }),
      ).toBe(false);
    }
  });

  it("accepts exact ID-only authority requests and rejects renderer-controlled execution data", () => {
    for (const request of requests) {
      expect(validateStudioEnvelope(request)).toBe(true);
    }

    for (const forbidden of [
      "status",
      "content_hash",
      "evidence_hash",
      "path",
      "runtime_path",
      "command",
      "provider",
      "env",
      "script",
      "script_bytes",
      "headless_evidence_set_id",
    ]) {
      expect(
        validateStudioEnvelope({
          ...requests[0],
          params: { ...requests[0].params, [forbidden]: "renderer-controlled" },
        }),
      ).toBe(false);
    }

    const headlessRequest = requests[2] as Request & {
      params: Extract<
        JobCreateParams,
        { operation: "runtime.headless.verify" }
      >;
    };
    expect(
      validateStudioEnvelope({
        ...headlessRequest,
        params: {
          ...headlessRequest.params,
          platform_id: "platform:windows_x86_64",
        },
      }),
    ).toBe(true);
    expect(
      validateStudioEnvelope({
        ...headlessRequest,
        params: { ...headlessRequest.params, platform_id: "linux-x86_64" },
      }),
    ).toBe(false);

    for (let left = 0; left < headlessLineageArtifactFields.length; left += 1) {
      for (
        let right = left + 1;
        right < headlessLineageArtifactFields.length;
        right += 1
      ) {
        const leftField = headlessLineageArtifactFields[left];
        const rightField = headlessLineageArtifactFields[right];
        expect(
          validateStudioEnvelope({
            ...headlessRequest,
            params: {
              ...headlessRequest.params,
              [rightField]: headlessRequest.params[leftField],
            },
          }),
          `${leftField} must differ from ${rightField}`,
        ).toBe(false);
      }
    }
    expect(
      validateStudioEnvelope({
        ...headlessRequest,
        params: {
          ...headlessRequest.params,
          target_grant_id: headlessRequest.params.source_grant_id,
        },
      }),
    ).toBe(false);
  });

  it("keeps exactly 18 methods and requires the activated authority capabilities", () => {
    expect(STUDIO_V5_METHODS.size).toBe(18);
    const response = {
      protocol: "rpg-world-forge.studio_protocol",
      protocol_version: 5,
      kind: "response",
      request_id: "initialize_v5",
      method: "service.initialize",
      result: {
        service: "world-forge.studio",
        service_version: 5,
        protocol: "rpg-world-forge.studio_protocol",
        protocol_version: 5,
        methods: [...STUDIO_V5_METHODS] as InitializeResult["methods"],
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
          asset_authority_reviews: true,
          asset_release_authority: true,
          runtime_headless_authority: true,
          creation_preview_pre_release: true,
        },
      },
    } satisfies Response;
    expect(validateStudioEnvelope(response)).toBe(true);
    expect(
      validateStudioEnvelope({
        ...response,
        result: {
          ...response.result,
          capabilities: {
            ...response.result.capabilities,
            runtime_headless_authority: false,
          },
        },
      }),
    ).toBe(false);
  });

  it("rejects v10-v12 operation and format-version mismatches", () => {
    const queuedJob = {
      format: "world-forge.studio_creation_job",
      format_version: 10,
      job_id: "job_review_01",
      workspace_id: "workspace_01",
      operation: "asset.qa.review",
      operation_params: {
        qa_report_artifact_id: "artifact_qa_01",
        output_role: "texture",
        review_receipt_id: "review_receipt_01",
        decisions: ["approved"],
        blockers: [],
      },
      state: "queued",
      generation: 0,
      authority: {
        root_generation: 3,
        source_revision: "a".repeat(64),
        workflow_status_hash: "b".repeat(64),
        artifact_snapshot_hash: "c".repeat(64),
      },
      inputs: [],
      progress: "queued",
      result: null,
      error: null,
      created_at: "2026-08-08T00:00:00Z",
      started_at: null,
      finished_at: null,
      updated_at: "2026-08-08T00:00:00Z",
      record_hash: "d".repeat(64),
    } as const;
    const jobResponse = {
      protocol: "rpg-world-forge.studio_protocol",
      protocol_version: 5,
      kind: "response",
      request_id: "job_get_01",
      method: "creation_job.get",
      result: { job: queuedJob },
    } as const;

    expect(validateStudioEnvelope(jobResponse)).toBe(true);
    expect(
      validateStudioEnvelope({
        ...jobResponse,
        result: { job: { ...queuedJob, format_version: 11 } },
      }),
    ).toBe(false);
    expect(
      validateStudioEnvelope({ ...jobResponse, protocol_version: 4 }),
    ).toBe(false);
  });

  it("accepts only fail-closed v12 runtime authority results", () => {
    const job = {
      format: "world-forge.studio_creation_job",
      format_version: 12,
      job_id: "job_headless_01",
      workspace_id: "workspace_01",
      operation: "runtime.headless.verify",
      operation_params: {
        gamepack_artifact_id: "artifact_gamepack_01",
        asset_inventory_artifact_id: "artifact_inventory_01",
        assetpack_artifact_id: "artifact_assetpack_01",
        asset_release_authority_artifact_id: "artifact_release_authority_01",
        runtime_snapshot_artifact_id: "artifact_runtime_snapshot_01",
        runtime_adapter_registry_artifact_id: "artifact_runtime_registry_01",
        runtime_composition_artifact_id: "artifact_runtime_composition_01",
        runtime_bundle_artifact_id: "artifact_runtime_bundle_01",
        headless_script_artifact_id: "artifact_headless_script_01",
        source_grant_id: "grant_runtime_bundle_01",
        expected_source_grant_generation: 2,
        target_grant_id: "grant_headless_evidence_01",
        expected_target_grant_generation: 0,
        platform_id: "platform:linux_x86_64",
      },
      state: "succeeded",
      generation: 1,
      authority: {
        root_generation: 3,
        source_revision: "a".repeat(64),
        workflow_status_hash: "b".repeat(64),
        artifact_snapshot_hash: "c".repeat(64),
      },
      inputs: [],
      progress: "committed",
      result: {
        output_artifact_ids: [
          "artifact_runtime_authority_01",
          "artifact_runtime_evidence_01",
          "artifact_runtime_support_01",
        ],
        artifact_snapshot_hash: "a".repeat(64),
        analysis_status: "passed",
        reason_codes: [],
        cleanup_pending: false,
        runtime_support_authority: {
          format: "world-forge.runtime_support_authority",
          format_version: 1,
          id: "runtime_authority_01",
          content_hash: "a".repeat(64),
        },
        runtime_evidence: {
          format: "world-forge.runtime_evidence",
          format_version: 1,
          id: "runtime_evidence_01",
          content_hash: "b".repeat(64),
        },
        runtime_support_report: {
          format: "world-forge.runtime_support_report",
          format_version: 1,
          id: "runtime_support_01",
          content_hash: "c".repeat(64),
        },
        release_status: "blocked",
        native_status: "unavailable",
        supported: false,
        publication: {
          grant_id: "grant_headless_evidence_01",
          grant_generation: 1,
          kind: "headless_evidence_directory",
          state: "published",
          headless_evidence_set: {
            format: "world-forge.headless_evidence_set",
            format_version: 1,
            id: "headless_evidence_set_01",
            content_hash: "b".repeat(64),
            tree_hash: "c".repeat(64),
          },
        },
      },
      error: null,
      created_at: "2026-08-08T00:00:00Z",
      started_at: "2026-08-08T00:00:01Z",
      finished_at: "2026-08-08T00:00:02Z",
      updated_at: "2026-08-08T00:00:02Z",
      record_hash: "d".repeat(64),
    } as const;
    const response = {
      protocol: "rpg-world-forge.studio_protocol",
      protocol_version: 5,
      kind: "response",
      request_id: "job_headless_get_01",
      method: "creation_job.get",
      result: { job },
    } as const;
    expect(validateStudioEnvelope(response)).toBe(true);
    const duplicateLineageJob = {
      ...job,
      operation_params: {
        ...job.operation_params,
        headless_script_artifact_id:
          job.operation_params.runtime_bundle_artifact_id,
      },
    } as const;
    expect(
      validateStudioEnvelope({
        ...response,
        result: { job: duplicateLineageJob },
      }),
    ).toBe(false);
    expect(
      validateStudioEnvelope({
        ...response,
        result: {
          job: {
            ...job,
            operation_params: {
              ...job.operation_params,
              target_grant_id: job.operation_params.source_grant_id,
            },
          },
        },
      }),
    ).toBe(false);
    expect(
      validateStudioEnvelope({
        ...response,
        request_id: "job_headless_list_01",
        method: "creation_job.list",
        result: { jobs: [duplicateLineageJob], next_sequence: null },
      }),
    ).toBe(false);
    expect(
      validateStudioEnvelope({
        ...response,
        result: {
          job: { ...job, result: { ...job.result, supported: true } },
        },
      }),
    ).toBe(false);
    expect(
      validateStudioEnvelope({
        ...response,
        result: {
          job: {
            ...job,
            result: {
              ...job.result,
              headless_evidence_set: {
                headless_evidence_set_id: "renderer_chosen",
                content_hash: "b".repeat(64),
              },
            },
          },
        },
      }),
    ).toBe(false);
  });

  it("admits v6 headless evidence grants only through protocol v5", () => {
    const grant = {
      format: "world-forge.studio_creation_output_grant",
      format_version: 6,
      grant_id: "grant_headless_evidence_01",
      workspace_id: "workspace_01",
      kind: "headless_evidence_directory",
      display_name: "headless-evidence",
      state: "published",
      generation: 1,
      publication: {
        format: "world-forge.headless_evidence_set",
        format_version: 1,
        id: "headless_evidence_set_01",
        content_hash: "b".repeat(64),
        tree_hash: "c".repeat(64),
      },
      created_at: "2026-08-09T00:00:00Z",
      updated_at: "2026-08-09T00:00:01Z",
    } as const;
    const response = {
      protocol: "rpg-world-forge.studio_protocol",
      protocol_version: 5,
      kind: "response",
      request_id: "grant_v6",
      method: "creation_output_grant.get",
      result: { grant },
    } as const;
    expect(validateStudioEnvelope(response)).toBe(true);
    expect(
      validateStudioEnvelope({ ...response, protocol_version: 4 }),
    ).toBe(false);
  });
});

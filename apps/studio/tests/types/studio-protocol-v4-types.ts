import type {
  NdjsonSupervisor,
  StudioV4RequestParams,
  StudioV4SuccessForMethod,
} from "../../src/main/ndjson-supervisor";
import type {
  StudioCreationAdmissionParams,
  StudioCreationAssetReleaseSealParams,
  StudioCreationAssetProcessParams,
  StudioCreationCompileParams,
  StudioCreationOutputGrantMutationParams,
  StudioCreationRuntimeComposeParams,
} from "../../src/shared/studio-api";
import type {
  WorldForgeStudioCreationJobV9,
  WorldForgeStudioCreationOutputGrantV5,
} from "../../src/generated/studio-protocol-v4";

const compile: StudioV4RequestParams<"creation_job.create"> = {
  workspace_id: "workspace_01",
  operation: "creation.compile",
  expected_root_generation: 0,
  expected_source_revision: "a".repeat(64),
  expected_workflow_status_hash: null,
  expected_artifact_snapshot_hash: "b".repeat(64),
};
void compile;

const admission: StudioV4RequestParams<"creation_job.create"> = {
  ...compile,
  operation: "artifact.admit",
  document: { format: "world-forge.game_analysis", format_version: 1 },
  dependency_artifact_ids: [],
};
void admission;

const assetProcess: StudioV4RequestParams<"creation_job.create"> = {
  ...compile,
  operation: "asset.process",
  license_artifact_ids: ["artifact_license_01"],
  recipe_id: "board_ui_recipe",
  processing_receipt_id: "board_ui_processing_receipt",
  qa_report_id: "board_ui_qa",
  acceptance_results: [
    {
      criterion_index: 0,
      criterion_sha256: "c".repeat(64),
      status: "passed",
      evidence_hashes: ["d".repeat(64)],
    },
  ],
};
void assetProcess;

const assetSeal: StudioV4RequestParams<"creation_job.create"> = {
  ...compile,
  operation: "asset.release.seal",
  qa_report_artifact_ids: ["artifact_qa_01"],
  manifest_id: "release_manifest_01",
  target_grant_id: "grant_output_01",
  expected_target_grant_generation: 2,
};
void assetSeal;

const runtimeCompose: StudioV4RequestParams<"creation_job.create"> = {
  ...compile,
  operation: "runtime.compose",
  gamepack_artifact_id: "artifact_gamepack_01",
  asset_inventory_artifact_id: "artifact_inventory_01",
  assetpack_artifact_id: "artifact_assetpack_01",
  target_grant_id: "grant_output_01",
  expected_target_grant_generation: 2,
};
void runtimeCompose;

const outputGrantCreate: StudioV4RequestParams<"creation_output_grant.create"> = {
  workspace_id: "workspace_01",
  kind: "generic_assetpack_directory",
  display_name: "release",
  path: "/private/release",
};
void outputGrantCreate;

const rendererCompile: StudioCreationCompileParams = {
  workspaceId: "workspace_01",
  expectedRootGeneration: 0,
  expectedSourceRevision: "a".repeat(64),
  expectedWorkflowStatusHash: null,
  expectedArtifactSnapshotHash: "b".repeat(64),
};
void rendererCompile;

const rendererAdmission: StudioCreationAdmissionParams = {
  ...rendererCompile,
  document: { format: "world-forge.game_analysis", format_version: 1 },
  dependencyArtifactIds: [],
};
void rendererAdmission;

const rendererAssetProcess: StudioCreationAssetProcessParams = {
  ...rendererCompile,
  licenseArtifactIds: ["artifact_license_01"],
  recipeId: "board_ui_recipe",
  processingReceiptId: "board_ui_processing_receipt",
  qaReportId: "board_ui_qa",
  acceptanceResults: [
    {
      criterionIndex: 0,
      criterionSha256: "c".repeat(64),
      status: "passed",
      evidenceHashes: ["d".repeat(64)],
    },
  ],
};
void rendererAssetProcess;

const rendererAssetSeal: StudioCreationAssetReleaseSealParams = {
  ...rendererCompile,
  qaReportArtifactIds: ["artifact_qa_01"],
  manifestId: "release_manifest_01",
  targetGrantId: "grant_output_01",
  expectedTargetGrantGeneration: 2,
};
void rendererAssetSeal;

const rendererRuntimeCompose: StudioCreationRuntimeComposeParams = {
  ...rendererCompile,
  gamepackArtifactId: "artifact_gamepack_01",
  assetInventoryArtifactId: "artifact_inventory_01",
  assetpackArtifactId: "artifact_assetpack_01",
  targetGrantId: "grant_output_01",
  expectedTargetGrantGeneration: 2,
};
void rendererRuntimeCompose;

const rendererGrantMutation: StudioCreationOutputGrantMutationParams = {
  grantId: "grant_output_01",
  expectedGeneration: 2,
};
void rendererGrantMutation;

const rendererPathLeak: StudioCreationAssetReleaseSealParams = {
  ...rendererAssetSeal,
  // @ts-expect-error Renderer seal params never expose an output path.
  path: "/private/release",
};
void rendererPathLeak;

const rendererOperationLeak: StudioCreationCompileParams = {
  ...rendererCompile,
  // @ts-expect-error Renderer compile params never accept a generic operation selector.
  operation: "artifact.admit",
};
void rendererOperationLeak;

declare const supervisor: NdjsonSupervisor;
void supervisor.request("compile_01", "creation_job.create", compile, 1_000, 4);
void supervisor.request(
  "events_01",
  "creation_event.list",
  { workspace_id: "workspace_01", after_id: 0, limit: 64 },
  1_000,
  4,
);
// @ts-expect-error The v4 transport does not accept renderer-style camelCase authority.
const leakedWireAuthority: StudioV4RequestParams<"creation_job.create"> = rendererCompile;
void leakedWireAuthority;

declare const jobReply: StudioV4SuccessForMethod<"creation_job.get">;
void jobReply.result.job.record_hash;
// @ts-expect-error Job replies cannot be consumed as artifact census replies.
void jobReply.result.artifacts;

declare const eventReply: StudioV4SuccessForMethod<"creation_event.list">;
void eventReply.result.events;
// @ts-expect-error Event replies cannot be consumed as job replies.
void eventReply.result.job;

declare const closedGeneratedJob: WorldForgeStudioCreationJobV9;
// @ts-expect-error Closed generated v4 jobs cannot expose private keys.
void closedGeneratedJob.native_path;

declare const publicOutputGrant: WorldForgeStudioCreationOutputGrantV5;
void publicOutputGrant.display_name;
// @ts-expect-error Public output grants cannot expose their private native path.
void publicOutputGrant.path;
// @ts-expect-error Protocol v4 remains closed to output-grant v6.
const unavailableV6: WorldForgeStudioCreationOutputGrantV5["format_version"] = 6;
void unavailableV6;

type LegacyCreationJob = Extract<
  WorldForgeStudioCreationJobV9,
  { format_version: 1 }
>;
// @ts-expect-error Legacy v1 jobs cannot expose v2-only operation parameters.
const legacyOperationParams: NonNullable<LegacyCreationJob["operation_params"]> = {
  license_artifact_ids: ["artifact_license_01"],
  recipe_id: "board_ui_recipe",
  processing_receipt_id: "board_ui_processing_receipt",
  qa_report_id: "board_ui_qa",
  acceptance_results: [
    {
      criterion_index: 0,
      criterion_sha256: "c".repeat(64),
      status: "passed",
      evidence_hashes: ["d".repeat(64)],
    },
  ],
};
void legacyOperationParams;

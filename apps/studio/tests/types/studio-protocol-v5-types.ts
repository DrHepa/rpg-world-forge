import type {
  JobCreateParams,
  WorldForgeStudioCreationJobV12,
  WorldForgeStudioCreationOutputGrantV6,
} from "../../src/generated/studio-protocol-v5";
import type {
  StudioCreationOutputGrantV6,
  StudioV5Method,
  StudioV5ReplyEnvelope,
} from "../../src/shared/studio-api";
import type { NdjsonSupervisor } from "../../src/main/ndjson-supervisor";

const review: JobCreateParams = {
  workspace_id: "workspace_01",
  operation: "asset.qa.review",
  expected_root_generation: 3,
  expected_source_revision: "a".repeat(64),
  expected_workflow_status_hash: "b".repeat(64),
  expected_artifact_snapshot_hash: "c".repeat(64),
  qa_report_artifact_id: "artifact_qa_01",
  output_role: "texture",
  review_receipt_id: "review_receipt_01",
  decisions: ["approved"],
  blockers: [],
};
void review;

const leakedReview: JobCreateParams = {
  ...review,
  // @ts-expect-error Public v5 authority requests never accept caller-computed status.
  status: "passed",
};
void leakedReview;

const leakedPath: JobCreateParams = {
  ...review,
  // @ts-expect-error Public v5 authority requests never expose native paths.
  runtime_path: "/private/runtime",
};
void leakedPath;

const headless: JobCreateParams = {
  workspace_id: "workspace_01",
  operation: "runtime.headless.verify",
  expected_root_generation: 3,
  expected_source_revision: "a".repeat(64),
  expected_workflow_status_hash: "b".repeat(64),
  expected_artifact_snapshot_hash: "c".repeat(64),
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
};
void headless;

const callerNamedEvidence: JobCreateParams = {
  ...headless,
  // @ts-expect-error The evidence-set identity is derived by the retained worker.
  headless_evidence_set_id: "headless_evidence_set_01",
};
void callerNamedEvidence;

declare const grantV6: WorldForgeStudioCreationOutputGrantV6;
const sharedGrantV6: StudioCreationOutputGrantV6 = grantV6;
void sharedGrantV6;

declare const job: WorldForgeStudioCreationJobV12;
if (job.format_version === 10) {
  void job.operation_params.review_receipt_id;
  // @ts-expect-error v10 review jobs cannot expose v12 runtime artifacts.
  void job.operation_params.runtime_bundle_artifact_id;
}

declare const method: StudioV5Method;
void method;
declare const reply: StudioV5ReplyEnvelope;
void reply;

declare const supervisor: NdjsonSupervisor;
void supervisor.request("active_v5", "service.initialize", {}, 1_000, 5);

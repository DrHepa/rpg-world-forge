import type {
  EmptyParams as StudioV3GeneratedEmptyParams,
  Request as StudioV3GeneratedRequest,
  WorkspaceCreateParams as StudioV3GeneratedWorkspaceCreateParams,
} from "../../src/generated/studio-protocol-v3";
import type {
  NdjsonSupervisor,
  StudioV3RequestParams,
  StudioV3SuccessForMethod,
} from "../../src/main/ndjson-supervisor";
import type { StudioCreationProjectCreateParams } from "../../src/shared/studio-api";

const documentRead: StudioV3RequestParams<"creation_document.read"> = {
  workspace_id: "workspace_01",
  expected_source_revision: "a".repeat(64),
  path: "project.json",
};
void documentRead;

const workflowRead: StudioV3RequestParams<"creation_workflow.get"> = {
  workspace_id: "workspace_01",
};
void workflowRead;

const recovery: StudioV3RequestParams<"creation_workspace.recover"> = {
  workspace_id: "workspace_01",
  expected_root_generation: 0,
};
void recovery;

const phaseComplete: StudioV3RequestParams<"creation_phase.complete"> = {
  workspace_id: "workspace_01",
  expected_root_generation: 0,
  expected_source_revision: "a".repeat(64),
  expected_workflow_status_hash: "b".repeat(64),
  report: {},
  artifact_registry: [],
};
void phaseComplete;

const nullPhaseCompleteHash: StudioV3RequestParams<"creation_phase.complete"> = {
  ...phaseComplete,
  // @ts-expect-error Phase completion requires a concrete current workflow CAS hash.
  expected_workflow_status_hash: null,
};
void nullPhaseCompleteHash;

const nullPhaseReopenHash: StudioV3RequestParams<"creation_phase.reopen"> = {
  workspace_id: "workspace_01",
  expected_root_generation: 0,
  expected_source_revision: "a".repeat(64),
  // @ts-expect-error Phase reopen requires a concrete current workflow CAS hash.
  expected_workflow_status_hash: null,
  phase_id: "p00_brief",
  reason: "Requirements changed",
  approved_by: "lead_reviewer",
};
void nullPhaseReopenHash;

const leakedPhasePath: StudioV3RequestParams<"creation_phase.complete"> = {
  ...phaseComplete,
  // @ts-expect-error Inline phase completion never accepts a renderer-controlled path.
  report_path: "/private/report.json",
};
void leakedPhasePath;

// @ts-expect-error Changeset approval requires both reviewed hashes.
const incompleteApproval: StudioV3RequestParams<"creation_changeset.approve"> = {
  changeset_id: "changeset_01",
  expected_record_hash: "a".repeat(64),
};
void incompleteApproval;

// @ts-expect-error Existing-root grants require a concrete project hash.
const existingRootWithoutHash: StudioV3RequestParams<"creation_root_grant.create"> = {
  role: "existing_root",
  display_name: "Existing root",
  path: "/tmp/existing-root",
  expected_project_hash: null,
};
void existingRootWithoutHash;

// @ts-expect-error New-target grants require a null project hash.
const newTargetWithHash: StudioV3RequestParams<"creation_root_grant.create"> = {
  role: "new_target",
  display_name: "New target",
  path: "/tmp/new-target",
  expected_project_hash: "a".repeat(64),
};
void newTargetWithHash;

const generatedRequestWithLeak: StudioV3GeneratedRequest = {
  protocol: "rpg-world-forge.studio_protocol",
  protocol_version: 3,
  kind: "request",
  request_id: "request_01",
  method: "creation_workspace.list",
  params: {},
  // @ts-expect-error Generated v3 envelopes must not expose a broad index signature.
  native_path: "/private/project",
};
void generatedRequestWithLeak;

const generatedEmptyParamsLeak: StudioV3GeneratedEmptyParams = {
  // @ts-expect-error Closed empty v3 params cannot contain extension keys.
  extension: true,
};
void generatedEmptyParamsLeak;

const generatedGameCreate: StudioV3GeneratedWorkspaceCreateParams = {
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
};
void generatedGameCreate;

// @ts-expect-error Generated game creation requires every explicit initial facet.
const generatedGameMissingPresentation: StudioV3GeneratedWorkspaceCreateParams = {
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
  runtime_support_intent: "authoring_only",
};
void generatedGameMissingPresentation;

const generatedGameWithLeak: StudioV3GeneratedWorkspaceCreateParams = {
  ...generatedGameCreate,
  // @ts-expect-error Generated game creation params remain closed.
  provider: "remote",
};
void generatedGameWithLeak;

const impossibleSharedNarrativeGame: StudioCreationProjectCreateParams = {
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
  // @ts-expect-error No-narrative game inputs cannot carry narrative authorship.
  narrativeAuthorship: "authored",
  narrativeTopology: "none",
  presentationMode: "2d",
  runtimeSupportIntent: "authoring_only",
};
void impossibleSharedNarrativeGame;

declare const supervisor: NdjsonSupervisor;
// @ts-expect-error The public v3 call boundary rejects non-empty initialize params.
void supervisor.request("request_02", "service.initialize", { extension: true }, 1_000, 3);
void supervisor.request(
  "request_03",
  "creation_document.read",
  // @ts-expect-error The public v3 call boundary requires the document path.
  { workspace_id: "workspace_01", expected_source_revision: "a".repeat(64) },
  1_000,
  3,
);
void supervisor.request(
  "request_04",
  "creation_root_grant.create",
  // @ts-expect-error The public v3 call boundary preserves the grant role/hash relation.
  {
    role: "existing_root",
    display_name: "Existing root",
    path: "/tmp/existing-root",
    expected_project_hash: null,
  },
  1_000,
  3,
);

// @ts-expect-error creation_document.read requires its portable document path.
const missingPath: StudioV3RequestParams<"creation_document.read"> = {
  workspace_id: "workspace_01",
  expected_source_revision: "a".repeat(64),
};
void missingPath;

const leakedNativePath: StudioV3RequestParams<"creation_document.read"> = {
  workspace_id: "workspace_01",
  expected_source_revision: "a".repeat(64),
  path: "project.json",
  // @ts-expect-error Native paths are not part of the closed v3 document request.
  native_path: "/private/project.json",
};
void leakedNativePath;

declare const openReply: StudioV3SuccessForMethod<"creation_workspace.open">;
void openReply.result.workspace;
// @ts-expect-error Workspace-open replies cannot be consumed as readiness replies.
void openReply.result.readiness;

declare const readinessReply: StudioV3SuccessForMethod<"creation_readiness.inspect">;
void readinessReply.result.readiness;
// @ts-expect-error Readiness replies do not contain an opened workspace result.
void readinessReply.result.route;

declare const phaseReply: StudioV3SuccessForMethod<"creation_phase.complete">;
void phaseReply.result.workflow.status_hash;
// @ts-expect-error Phase completion does not return a changeset record.
void phaseReply.result.changeset;

declare const changesetReply: StudioV3SuccessForMethod<"creation_changeset.get">;
void changesetReply.result.changeset.record_hash;
// @ts-expect-error Changeset get does not return a workflow transition.
void changesetReply.result.workflow;

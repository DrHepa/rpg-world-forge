/* AUTO-GENERATED from schemas/studio-protocol-v3.schema.json. Do not edit by hand. */
/* eslint-disable @typescript-eslint/no-empty-object-type */

export type WorldForgeStudioGenericCreationProtocolV3 = Request | Response | ErrorEnvelope;
export type Request = Base & {
  protocol: "rpg-world-forge.studio_protocol";
  protocol_version: 3;
  kind: "request";
  request_id: EntityId;
  method: Method;
  params: unknown;
} & (
    | {
        method: "service.initialize" | "creation_workspace.list";
        params: EmptyParams;
      }
    | {
        method: "creation_root_grant.create";
        params: GrantCreateParams;
      }
    | {
        method: "creation_root_grant.get";
        params: GrantIdParams;
      }
    | {
        method: "creation_root_grant.revoke";
        params: GrantMutationParams;
      }
    | {
        method: "creation_workspace.create";
        params: WorkspaceCreateParams;
      }
    | {
        method: "creation_workspace.recover";
        params: WorkspaceRecoverParams;
      }
    | {
        method: "creation_workspace.register";
        params: WorkspaceRegisterParams;
      }
    | {
        method:
          | "creation_workspace.get"
          | "creation_workspace.open"
          | "creation_workflow.get"
          | "creation_readiness.inspect";
        params: WorkspaceIdParams;
      }
    | {
        method: "creation_document.list";
        params: RevisionParams;
      }
    | {
        method: "creation_document.read";
        params: DocumentReadParams;
      }
    | {
        method: "creation_changeset.create";
        params: ChangesetCreateParams;
      }
    | {
        method: "creation_changeset.get" | "creation_changeset.diff";
        params: ChangesetIdParams;
      }
    | {
        method: "creation_changeset.list";
        params: ChangesetListParams;
      }
    | {
        method: "creation_changeset.approve" | "creation_changeset.reject";
        params: ChangesetActionParams;
      }
    | {
        method: "creation_changeset.apply";
        params: ChangesetApplyParams;
      }
    | {
        method: "creation_changeset.recover";
        params: ChangesetRecoverParams;
      }
    | {
        method: "creation_workflow.reconcile";
        params: WorkflowReconcileParams;
      }
    | {
        method: "creation_phase.read";
        params: PhaseReadParams;
      }
    | {
        method: "creation_phase.validate";
        params: PhaseReportParams;
      }
    | {
        method: "creation_phase.complete";
        params: PhaseCompleteParams;
      }
    | {
        method: "creation_phase.reopen";
        params: PhaseReopenParams;
      }
  );
export type EntityId = string;
export type Method =
  | "service.initialize"
  | "creation_root_grant.create"
  | "creation_root_grant.get"
  | "creation_root_grant.revoke"
  | "creation_workspace.create"
  | "creation_workspace.recover"
  | "creation_workspace.register"
  | "creation_workspace.get"
  | "creation_workspace.list"
  | "creation_workspace.open"
  | "creation_document.list"
  | "creation_document.read"
  | "creation_changeset.create"
  | "creation_changeset.get"
  | "creation_changeset.list"
  | "creation_changeset.diff"
  | "creation_changeset.approve"
  | "creation_changeset.reject"
  | "creation_changeset.apply"
  | "creation_changeset.recover"
  | "creation_workflow.get"
  | "creation_workflow.reconcile"
  | "creation_phase.read"
  | "creation_phase.validate"
  | "creation_phase.complete"
  | "creation_phase.reopen"
  | "creation_readiness.inspect";
export type GrantCreateParams = {
  grant_id?: EntityId;
  role: "existing_root" | "new_target";
  display_name: string;
  path: string;
  expected_project_hash: Sha256 | null;
} & GrantCreateParams1;
export type Sha256 = string;
export type GrantCreateParams1 =
  | {
      role: "existing_root";
      expected_project_hash: Sha256;
    }
  | {
      role: "new_target";
      expected_project_hash: null;
    };
export type WorkspaceCreateParams = WorkspaceCreateLibraryParams | WorkspaceCreateGameParams;
export type WorkspaceId = string;
export type WorkspaceCreateGameParams =
  WorkspaceCreateGameWithoutNarrativeParams | WorkspaceCreateGameWithNarrativeParams;
export type CreationScaffoldIdentifier = string;
export type ChangesetCreateParams = CreationAuthorityProperties & {
  changeset_id?: EntityId;
  workspace_id: WorkspaceId;
  expected_root_generation: number;
  expected_source_revision: Sha256;
  expected_workflow_status_hash: NullableSha256;
  /**
   * @minItems 1
   * @maxItems 256
   */
  operations: [ChangesetInputOperation, ...ChangesetInputOperation[]];
};
export type NullableSha256 = Sha256 | null;
export type ChangesetInputOperation =
  | {
      operation: "create";
      path: string;
      expected_base_file_sha256: null;
      expected_base_size: null;
      proposed_file_sha256: Sha256;
      proposed_size: number;
      document: {};
    }
  | {
      operation: "replace";
      path: string;
      expected_base_file_sha256: Sha256;
      expected_base_size: number;
      proposed_file_sha256: Sha256;
      proposed_size: number;
      document: {};
    }
  | {
      operation: "delete";
      path: string;
      expected_base_file_sha256: Sha256;
      expected_base_size: number;
      proposed_file_sha256: null;
      proposed_size: null;
    };
export type WorkflowReconcileParams = CreationAuthorityProperties & {
  workspace_id: WorkspaceId;
  expected_root_generation: number;
  expected_source_revision: Sha256;
  expected_workflow_status_hash: NullableSha256;
  artifact_registry: ArtifactRegistry;
};
/**
 * @maxItems 1024
 */
export type ArtifactRegistry = {}[];
export type PhaseReadParams = CreationAuthorityProperties & {
  workspace_id: WorkspaceId;
  phase_id: string;
  expected_root_generation: number;
  expected_source_revision: Sha256;
  expected_workflow_status_hash: Sha256;
};
export type PhaseReportParams = CreationAuthorityProperties & {
  workspace_id: WorkspaceId;
  expected_root_generation: number;
  expected_source_revision: Sha256;
  expected_workflow_status_hash: NullableSha256;
  report: {};
  artifact_registry: ArtifactRegistry;
};
export type PhaseCompleteParams = CreationAuthorityProperties & {
  workspace_id: WorkspaceId;
  expected_root_generation: number;
  expected_source_revision: Sha256;
  expected_workflow_status_hash: Sha256;
  report: {};
  artifact_registry: ArtifactRegistry;
};
export type PhaseReopenParams = CreationAuthorityProperties & {
  workspace_id: WorkspaceId;
  expected_root_generation: number;
  expected_source_revision: Sha256;
  expected_workflow_status_hash: Sha256;
  phase_id: string;
  reason: string;
  approved_by: EntityId;
};
export type Response = Base & {
  protocol: "rpg-world-forge.studio_protocol";
  protocol_version: 3;
  kind: "response";
  request_id: EntityId;
  method: Method;
  result: unknown;
} & (
    | {
        method: "service.initialize";
        result: InitializeResult;
      }
    | {
        method:
          "creation_root_grant.create" | "creation_root_grant.get" | "creation_root_grant.revoke";
        result: GrantResult;
      }
    | {
        method:
          "creation_workspace.create" | "creation_workspace.register" | "creation_workspace.get";
        result: WorkspaceResult;
      }
    | {
        method: "creation_workspace.recover";
        result: WorkspaceRecoverResult;
      }
    | {
        method: "creation_workspace.list";
        result: WorkspaceListResult;
      }
    | {
        method: "creation_workspace.open";
        result: WorkspaceOpenResult;
      }
    | {
        method: "creation_document.list";
        result: DocumentListResult;
      }
    | {
        method: "creation_document.read";
        result: DocumentReadResult;
      }
    | {
        method: "creation_workflow.get";
        result: WorkflowResult;
      }
    | {
        method:
          | "creation_changeset.create"
          | "creation_changeset.get"
          | "creation_changeset.approve"
          | "creation_changeset.reject";
        result: ChangesetResult;
      }
    | {
        method: "creation_changeset.list";
        result: ChangesetListResult;
      }
    | {
        method: "creation_changeset.diff";
        result: ChangesetDiffResult;
      }
    | {
        method: "creation_changeset.apply";
        result: ChangesetApplyResult;
      }
    | {
        method: "creation_changeset.recover";
        result: ChangesetRecoverResult;
      }
    | {
        method: "creation_workflow.reconcile" | "creation_phase.complete" | "creation_phase.reopen";
        result: WorkspaceWorkflowResult;
      }
    | {
        method: "creation_phase.read";
        result: PhaseReadResult;
      }
    | {
        method: "creation_phase.validate";
        result: PhaseValidateResult;
      }
    | {
        method: "creation_readiness.inspect";
        result: ReadinessResult;
      }
  );
export type WorldForgeStudioCreationRootGrantV1 = {
  format: "world-forge.studio_creation_root_grant";
  format_version: 1;
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "entityId".
   */
  grant_id: string;
  role: "existing_root" | "new_target";
  display_name: string;
  state: "ready" | "reserved" | "recovery_required" | "consumed" | "revoked";
  expected_target_state: "existing_project" | "absent";
  expected_project: ProjectIdentity | null;
  generation: number;
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "timestamp".
   */
  created_at: string;
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "timestamp".
   */
  updated_at: string;
} & (
  | {
      role: "existing_root";
      expected_target_state: "existing_project";
      expected_project: ProjectIdentity;
    }
  | {
      role: "new_target";
      expected_target_state: "absent";
      expected_project: null;
    }
);
/**
 * This interface was referenced by `WorldForgeStudioCreationChangesetV1`'s JSON-Schema
 * via the `definition` "operation".
 */
export type Operation = {
  operation: "create" | "replace" | "delete";
  path: string;
  /**
   * This interface was referenced by `WorldForgeStudioCreationChangesetV1`'s JSON-Schema
   * via the `definition` "nullableSha256".
   */
  expected_base_file_sha256: string | null;
  /**
   * This interface was referenced by `WorldForgeStudioCreationChangesetV1`'s JSON-Schema
   * via the `definition` "nullableSize".
   */
  expected_base_size: number | null;
  /**
   * This interface was referenced by `WorldForgeStudioCreationChangesetV1`'s JSON-Schema
   * via the `definition` "nullableSha256".
   */
  proposed_file_sha256: string | null;
  /**
   * This interface was referenced by `WorldForgeStudioCreationChangesetV1`'s JSON-Schema
   * via the `definition` "nullableSize".
   */
  proposed_size: number | null;
} & (
  | {
      operation?: "create";
      expected_base_file_sha256?: null;
      expected_base_size?: null;
      /**
       * This interface was referenced by `WorldForgeStudioCreationChangesetV1`'s JSON-Schema
       * via the `definition` "sha256".
       */
      proposed_file_sha256?: string;
      proposed_size?: number;
    }
  | {
      operation?: "replace";
      /**
       * This interface was referenced by `WorldForgeStudioCreationChangesetV1`'s JSON-Schema
       * via the `definition` "sha256".
       */
      expected_base_file_sha256?: string;
      expected_base_size?: number;
      /**
       * This interface was referenced by `WorldForgeStudioCreationChangesetV1`'s JSON-Schema
       * via the `definition` "sha256".
       */
      proposed_file_sha256?: string;
      proposed_size?: number;
    }
  | {
      operation?: "delete";
      /**
       * This interface was referenced by `WorldForgeStudioCreationChangesetV1`'s JSON-Schema
       * via the `definition` "sha256".
       */
      expected_base_file_sha256?: string;
      expected_base_size?: number;
      proposed_file_sha256?: null;
      proposed_size?: null;
    }
);

export interface Base {
  protocol: "rpg-world-forge.studio_protocol";
  protocol_version: 3;
  kind: "request" | "response" | "error";
  request_id: EntityId;
}
export type EmptyParams = Record<string, never>;
export interface GrantIdParams {
  grant_id: EntityId;
}
export interface GrantMutationParams {
  grant_id: EntityId;
  expected_generation: number;
}
export interface WorkspaceCreateLibraryParams {
  workspace_id?: WorkspaceId;
  grant_id: EntityId;
  expected_grant_generation: number;
  project_kind: "asset_library" | "universe_library";
  project_id: EntityId;
  title: string;
  default_locale: string;
  project_version: string;
}
export interface WorkspaceCreateGameWithoutNarrativeParams {
  workspace_id?: WorkspaceId;
  grant_id: EntityId;
  expected_grant_generation: number;
  project_kind: "game";
  project_id: EntityId;
  title: string;
  default_locale: string;
  project_version: string;
  gameplay_family:
    | "action"
    | "adventure"
    | "educational"
    | "narrative"
    | "puzzle"
    | "rhythm"
    | "role_playing"
    | "sandbox"
    | "simulation"
    | "sports"
    | "strategy";
  initial_core_verb: CreationScaffoldIdentifier;
  initial_core_loop: string;
  world_presence: "none" | "abstract" | "symbolic" | "diegetic";
  narrative_requirement: "none";
  narrative_authorship: "none";
  narrative_topology: "none";
  presentation_mode: "text" | "2d" | "2_5d" | "3d" | "mixed" | "vr" | "ar";
  runtime_support_intent: "authoring_only" | "compatibility_assessment";
}
export interface WorkspaceCreateGameWithNarrativeParams {
  workspace_id?: WorkspaceId;
  grant_id: EntityId;
  expected_grant_generation: number;
  project_kind: "game";
  project_id: EntityId;
  title: string;
  default_locale: string;
  project_version: string;
  gameplay_family:
    | "action"
    | "adventure"
    | "educational"
    | "narrative"
    | "puzzle"
    | "rhythm"
    | "role_playing"
    | "sandbox"
    | "simulation"
    | "sports"
    | "strategy";
  initial_core_verb: CreationScaffoldIdentifier;
  initial_core_loop: string;
  world_presence: "none" | "abstract" | "symbolic" | "diegetic";
  narrative_requirement: "optional" | "required";
  narrative_authorship:
    "authored" | "emergent" | "procedural" | "player_authored" | "social" | "hybrid";
  narrative_topology:
    | "linear"
    | "foldback"
    | "branching"
    | "branch_and_bottleneck"
    | "hub_and_spoke"
    | "modular"
    | "storylet"
    | "loop_reset"
    | "episodic"
    | "seasonal"
    | "open_ended";
  presentation_mode: "text" | "2d" | "2_5d" | "3d" | "mixed" | "vr" | "ar";
  runtime_support_intent: "authoring_only" | "compatibility_assessment";
}
export interface WorkspaceRecoverParams {
  workspace_id: WorkspaceId;
  expected_root_generation: number;
}
export interface WorkspaceRegisterParams {
  workspace_id?: WorkspaceId;
  grant_id: EntityId;
  expected_grant_generation: number;
  expected_project_hash: Sha256;
}
export interface WorkspaceIdParams {
  workspace_id: WorkspaceId;
}
export interface RevisionParams {
  workspace_id: WorkspaceId;
  expected_source_revision: Sha256;
}
export interface DocumentReadParams {
  workspace_id: WorkspaceId;
  expected_source_revision: Sha256;
  path: string;
}
export interface CreationAuthorityProperties {
  workspace_id: WorkspaceId;
  expected_root_generation: number;
  expected_source_revision: Sha256;
  expected_workflow_status_hash: NullableSha256;
}
export interface ChangesetIdParams {
  changeset_id: EntityId;
}
export interface ChangesetListParams {
  workspace_id?: WorkspaceId;
  status?: "staged" | "approved" | "applying" | "applied" | "rejected" | "recovery_required";
  limit?: number;
}
export interface ChangesetActionParams {
  changeset_id: EntityId;
  expected_record_hash: Sha256;
  expected_review_sha256: Sha256;
}
export interface ChangesetApplyParams {
  changeset_id: EntityId;
  expected_record_hash: Sha256;
  expected_review_sha256: Sha256;
  expected_root_generation: number;
}
export interface ChangesetRecoverParams {
  changeset_id: EntityId;
  mode: "resume" | "rollback";
  expected_record_hash: Sha256;
  expected_review_sha256: Sha256;
  expected_root_generation: number;
}
export interface InitializeResult {
  service: "world-forge.studio";
  service_version: 3;
  protocol: "rpg-world-forge.studio_protocol";
  protocol_version: 3;
  /**
   * @minItems 27
   * @maxItems 27
   */
  methods: [
    Method,
    Method,
    Method,
    Method,
    Method,
    Method,
    Method,
    Method,
    Method,
    Method,
    Method,
    Method,
    Method,
    Method,
    Method,
    Method,
    Method,
    Method,
    Method,
    Method,
    Method,
    Method,
    Method,
    Method,
    Method,
    Method,
    Method,
  ];
  capabilities: {
    generic_creation: true;
    safe_project_creation: true;
    read_only_documents: true;
    profile_editing: true;
    generic_jobs: false;
    reviewed_changesets: true;
    workflow_mutations: true;
    inline_phase_reports: true;
  };
}
export interface GrantResult {
  grant: WorldForgeStudioCreationRootGrantV1;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "projectIdentity".
 */
export interface ProjectIdentity {
  format: "world-forge.project";
  format_version: 1;
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "entityId".
   */
  id: string;
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "sha256".
   */
  content_hash: string;
}
export interface WorkspaceResult {
  workspace: WorldForgeStudioCreationWorkspaceV1;
}
export interface WorldForgeStudioCreationWorkspaceV1 {
  format: "world-forge.studio_creation_workspace";
  format_version: 1;
  /**
   * This interface was referenced by `WorldForgeStudioCreationWorkspaceV1`'s JSON-Schema
   * via the `definition` "workspaceId".
   */
  workspace_id: string;
  project: ProjectIdentity1;
  project_kind: "game" | "universe_library" | "asset_library";
  /**
   * This interface was referenced by `WorldForgeStudioCreationWorkspaceV1`'s JSON-Schema
   * via the `definition` "sha256".
   */
  source_revision: string;
  workflow_status_hash: string | null;
  root_generation: number;
  /**
   * This interface was referenced by `WorldForgeStudioCreationWorkspaceV1`'s JSON-Schema
   * via the `definition` "timestamp".
   */
  created_at: string;
  /**
   * This interface was referenced by `WorldForgeStudioCreationWorkspaceV1`'s JSON-Schema
   * via the `definition` "timestamp".
   */
  updated_at: string;
}
/**
 * This interface was referenced by `WorldForgeStudioCreationWorkspaceV1`'s JSON-Schema
 * via the `definition` "projectIdentity".
 */
export interface ProjectIdentity1 {
  format: "world-forge.project";
  format_version: 1;
  /**
   * This interface was referenced by `WorldForgeStudioCreationWorkspaceV1`'s JSON-Schema
   * via the `definition` "entityId".
   */
  id: string;
  /**
   * This interface was referenced by `WorldForgeStudioCreationWorkspaceV1`'s JSON-Schema
   * via the `definition` "sha256".
   */
  content_hash: string;
}
export interface WorkspaceRecoverResult {
  workspace: WorldForgeStudioCreationWorkspaceV1;
  state: "complete" | "cleanup_pending";
}
export interface WorkspaceListResult {
  /**
   * @maxItems 1000
   */
  workspaces: WorldForgeStudioCreationWorkspaceV1[];
}
export interface WorkspaceOpenResult {
  workspace: WorldForgeStudioCreationWorkspaceV1;
  route: "generic";
  project_kind: "game" | "asset_library" | "universe_library";
  source_revision: Sha256;
  workflow_status_hash: Sha256 | null;
  current_phase: string | null;
}
export interface DocumentListResult {
  /**
   * @maxItems 1024
   */
  documents: DocumentSummary[];
  source_revision: Sha256;
}
export interface DocumentSummary {
  path: string;
  format: string;
  format_version: number;
  id: EntityId;
  content_hash: Sha256;
  file_sha256: Sha256;
}
export interface DocumentReadResult {
  source_revision: Sha256;
  document: {
    path: string;
    format: string;
    format_version: number;
    id: EntityId;
    content_hash: Sha256;
    file_sha256: Sha256;
    document: {};
  };
}
export interface WorkflowResult {
  workflow: Workflow;
}
export interface Workflow {
  state: "missing" | "not_started" | "active" | "complete" | "invalid";
  source_revision: Sha256;
  status_hash: Sha256 | null;
  current_phase: string | null;
  revision: number | null;
  status: {} | null;
}
export interface ChangesetResult {
  changeset: WorldForgeStudioCreationChangesetV1;
}
export interface WorldForgeStudioCreationChangesetV1 {
  format: "world-forge.studio_creation_changeset";
  format_version: 1;
  /**
   * This interface was referenced by `WorldForgeStudioCreationChangesetV1`'s JSON-Schema
   * via the `definition` "entityId".
   */
  changeset_id: string;
  /**
   * This interface was referenced by `WorldForgeStudioCreationChangesetV1`'s JSON-Schema
   * via the `definition` "workspaceId".
   */
  workspace_id: string;
  status: "staged" | "approved" | "applying" | "applied" | "rejected" | "recovery_required";
  expected_root_generation: number;
  /**
   * This interface was referenced by `WorldForgeStudioCreationChangesetV1`'s JSON-Schema
   * via the `definition` "sha256".
   */
  expected_source_revision: string;
  /**
   * This interface was referenced by `WorldForgeStudioCreationChangesetV1`'s JSON-Schema
   * via the `definition` "sha256".
   */
  proposed_source_revision: string;
  expected_workflow_status_hash: string | null;
  /**
   * This interface was referenced by `WorldForgeStudioCreationChangesetV1`'s JSON-Schema
   * via the `definition` "sha256".
   */
  review_sha256: string;
  /**
   * @minItems 1
   * @maxItems 256
   */
  operations: [Operation, ...Operation[]];
  /**
   * This interface was referenced by `WorldForgeStudioCreationChangesetV1`'s JSON-Schema
   * via the `definition` "timestamp".
   */
  created_at: string;
  /**
   * This interface was referenced by `WorldForgeStudioCreationChangesetV1`'s JSON-Schema
   * via the `definition` "timestamp".
   */
  updated_at: string;
  /**
   * This interface was referenced by `WorldForgeStudioCreationChangesetV1`'s JSON-Schema
   * via the `definition` "sha256".
   */
  record_hash: string;
}
export interface ChangesetListResult {
  /**
   * @maxItems 1000
   */
  changesets: WorldForgeStudioCreationChangesetV1[];
}
export interface ChangesetDiffResult {
  diff: {
    changeset_id: EntityId;
    workspace_id: WorkspaceId;
    expected_source_revision: Sha256;
    proposed_source_revision: Sha256;
    review_sha256: Sha256;
    /**
     * @minItems 1
     * @maxItems 256
     */
    operations: [ChangesetDiffOperation, ...ChangesetDiffOperation[]];
  };
}
export interface ChangesetDiffOperation {
  operation: "create" | "replace" | "delete";
  path: string;
  expected_base_file_sha256: NullableSha256;
  expected_base_size: number | null;
  proposed_file_sha256: NullableSha256;
  proposed_size: number | null;
  size_delta: number;
}
export interface ChangesetApplyResult {
  changeset: WorldForgeStudioCreationChangesetV1;
  workspace: WorldForgeStudioCreationWorkspaceV1;
  workflow: Workflow;
}
export interface ChangesetRecoverResult {
  changeset: WorldForgeStudioCreationChangesetV1;
  workspace: WorldForgeStudioCreationWorkspaceV1;
  workflow: Workflow;
  outcome: "not_needed" | "rolled_back" | "committed";
}
export interface WorkspaceWorkflowResult {
  workspace: WorldForgeStudioCreationWorkspaceV1;
  workflow: Workflow;
}
export interface PhaseReadResult {
  workspace: WorldForgeStudioCreationWorkspaceV1;
  workflow: Workflow;
  reference: {
    phase: string;
    status: "ready" | "not_applicable";
    content_hash: Sha256;
    /**
     * @minItems 1
     * @maxItems 1024
     */
    invalidation_dependencies: [{}, ...{}[]];
  };
  report: {};
}
export interface PhaseValidateResult {
  workspace: WorldForgeStudioCreationWorkspaceV1;
  workflow: Workflow;
  report: {};
}
export interface ReadinessResult {
  readiness: {
    state:
      | "missing"
      | "not_started"
      | "invalid"
      | "authoring_ready"
      | "implementation_ready"
      | "blocked";
    source_revision: Sha256;
    workflow_status_hash: Sha256 | null;
    current_phase: string | null;
    release: "blocked" | "ready";
    /**
     * @maxItems 128
     */
    blocker_reason_codes: string[];
    report: {} | null;
  };
}
export interface ErrorEnvelope {
  protocol: "rpg-world-forge.studio_protocol";
  protocol_version: 3;
  kind: "error";
  request_id: EntityId | null;
  error: {
    code:
      | "invalid_request"
      | "not_found"
      | "conflict"
      | "invalid_state"
      | "internal_error"
      | "recovery_ambiguous"
      | "recovery_failed";
    message: string;
    details: {};
  };
}

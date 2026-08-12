/* AUTO-GENERATED from schemas/studio-protocol-v5.schema.json. Do not edit by hand. */
/* eslint-disable @typescript-eslint/no-empty-object-type */

export type WorldForgeStudioAuthorityProtocolV5 = Request | Response | ErrorEnvelope;
export type Request = {
  protocol: "rpg-world-forge.studio_protocol";
  protocol_version: 5;
  kind: "request";
  request_id: EntityId;
  method: Method;
  params: unknown;
} & Request1;
export type EntityId = string;
export type Method =
  | "service.initialize"
  | "creation_workspace.create"
  | "creation_artifact.inspect"
  | "creation_artifact.list"
  | "creation_event.list"
  | "creation_evidence.inspect"
  | "creation_job.cancel"
  | "creation_job.create"
  | "creation_job.get"
  | "creation_job.list"
  | "creation_job.recover"
  | "creation_output_grant.create"
  | "creation_output_grant.get"
  | "creation_output_grant.list"
  | "creation_output_grant.revoke"
  | "creation_preview.close"
  | "creation_preview.open"
  | "creation_preview.read";
export type Request1 =
  | {
      method: "service.initialize";
      params: EmptyParams;
    }
  | {
      method: "creation_workspace.create";
      params: WorkspaceCreateParams;
    }
  | {
      method: "creation_output_grant.create";
      params: OutputGrantCreateParams;
    }
  | {
      method: "creation_output_grant.get";
      params: OutputGrantGetParams;
    }
  | {
      method: "creation_output_grant.list";
      params: OutputGrantListParams;
    }
  | {
      method: "creation_output_grant.revoke";
      params: OutputGrantRevokeParams;
    }
  | {
      method: "creation_artifact.list";
      params: ArtifactListParams;
    }
  | {
      method: "creation_artifact.inspect";
      params: ArtifactInspectParams;
    }
  | {
      method: "creation_evidence.inspect";
      params: EvidenceInspectParams;
    }
  | {
      method: "creation_job.create";
      params: JobCreateParams;
    }
  | {
      method: "creation_job.get";
      params: JobGetParams;
    }
  | {
      method: "creation_job.list";
      params: JobListParams;
    }
  | {
      method: "creation_job.cancel";
      params: JobCancelParams;
    }
  | {
      method: "creation_job.recover";
      params: JobRecoverParams;
    }
  | {
      method: "creation_event.list";
      params: EventListParams;
    }
  | {
      method: "creation_preview.open";
      params: CreationPreviewOpenParams;
    }
  | {
      method: "creation_preview.read";
      params: CreationPreviewReadParams;
    }
  | {
      method: "creation_preview.close";
      params: CreationPreviewCloseParams;
    };
export type WorkspaceCreateParams =
  | WorkspaceCreateLibraryParams
  | WorkspaceCreateGameWithoutNarrativeParams
  | WorkspaceCreateGameWithNarrativeParams;
export type WorkspaceId = string;
export type CreationScaffoldIdentifier = string;
export type Sha256 = string;
export type NullableSha256 = Sha256 | null;
export type ArtifactListParams = AuthorityProperties & {
  workspace_id: WorkspaceId;
  expected_root_generation: number;
  expected_source_revision: Sha256;
  expected_workflow_status_hash: NullableSha256;
  expected_artifact_snapshot_hash: NullableSha256;
  lifecycle: ("active" | "invalidated" | "historical" | "candidate") | null;
  cursor: EntityId | null;
  limit: number;
};
export type EvidenceInspectParams = AuthorityProperties;
export type JobCreateParams =
  | {
      job_id?: EntityId;
      workspace_id: WorkspaceId;
      operation: "creation.compile";
      expected_root_generation: number;
      expected_source_revision: Sha256;
      expected_workflow_status_hash: NullableSha256;
      expected_artifact_snapshot_hash: Sha256;
    }
  | {
      job_id?: EntityId;
      workspace_id: WorkspaceId;
      operation: "artifact.admit";
      expected_root_generation: number;
      expected_source_revision: Sha256;
      expected_workflow_status_hash: NullableSha256;
      expected_artifact_snapshot_hash: Sha256;
      document: {};
      /**
       * @maxItems 128
       */
      dependency_artifact_ids: EntityId[];
    }
  | {
      job_id?: EntityId;
      workspace_id: WorkspaceId;
      operation: "asset.process";
      expected_root_generation: number;
      expected_source_revision: Sha256;
      expected_workflow_status_hash: NullableSha256;
      expected_artifact_snapshot_hash: Sha256;
      /**
       * @minItems 1
       * @maxItems 4
       */
      license_artifact_ids:
        | [EntityId]
        | [EntityId, EntityId]
        | [EntityId, EntityId, EntityId]
        | [EntityId, EntityId, EntityId, EntityId];
      recipe_id: EntityId;
      processing_receipt_id: EntityId;
      qa_report_id: EntityId;
      /**
       * @minItems 1
       * @maxItems 64
       */
      acceptance_results: [AssetProcessAcceptanceResult, ...AssetProcessAcceptanceResult[]];
    }
  | {
      job_id?: EntityId;
      workspace_id: WorkspaceId;
      operation: "asset.release.seal";
      expected_root_generation: number;
      expected_source_revision: Sha256;
      expected_workflow_status_hash: NullableSha256;
      expected_artifact_snapshot_hash: Sha256;
      /**
       * @minItems 1
       * @maxItems 128
       */
      qa_report_artifact_ids: [EntityId, ...EntityId[]];
      manifest_id: EntityId;
      target_grant_id: EntityId;
      expected_target_grant_generation: number;
    }
  | {
      job_id?: EntityId;
      workspace_id: WorkspaceId;
      operation: "runtime.compose";
      expected_root_generation: number;
      expected_source_revision: Sha256;
      expected_workflow_status_hash: NullableSha256;
      expected_artifact_snapshot_hash: Sha256;
      gamepack_artifact_id: EntityId;
      asset_inventory_artifact_id: EntityId;
      assetpack_artifact_id: EntityId;
      target_grant_id: EntityId;
      expected_target_grant_generation: number;
    }
  | {
      job_id?: EntityId;
      workspace_id: WorkspaceId;
      operation: "runtime.bundle.build";
      expected_root_generation: number;
      expected_source_revision: Sha256;
      expected_workflow_status_hash: NullableSha256;
      expected_artifact_snapshot_hash: Sha256;
      gamepack_artifact_id: EntityId;
      asset_inventory_artifact_id: EntityId;
      assetpack_artifact_id: EntityId;
      target_grant_id: EntityId;
      expected_target_grant_generation: number;
      runtime_snapshot_artifact_id: EntityId;
      runtime_adapter_registry_artifact_id: EntityId;
      runtime_composition_artifact_id: EntityId;
      runtime_support_report_artifact_id: EntityId;
      source_grant_id: EntityId;
      expected_source_grant_generation: number;
    }
  | {
      job_id?: EntityId;
      workspace_id: WorkspaceId;
      operation: "game.materialization.bundle.build";
      expected_root_generation: number;
      expected_source_revision: Sha256;
      expected_workflow_status_hash: NullableSha256;
      expected_artifact_snapshot_hash: Sha256;
      runtime_bundle_artifact_id: EntityId;
      source_grant_id: EntityId;
      expected_source_grant_generation: number;
      target_grant_id: EntityId;
      expected_target_grant_generation: number;
    }
  | {
      job_id?: EntityId;
      workspace_id: WorkspaceId;
      operation: "game.materialize";
      expected_root_generation: number;
      expected_source_revision: Sha256;
      expected_workflow_status_hash: NullableSha256;
      expected_artifact_snapshot_hash: Sha256;
      source_grant_id: EntityId;
      expected_source_grant_generation: number;
      target_grant_id: EntityId;
      expected_target_grant_generation: number;
      materialization_bundle_artifact_id: EntityId;
    }
  | {
      job_id?: EntityId;
      workspace_id: WorkspaceId;
      operation: "game.package";
      expected_root_generation: number;
      expected_source_revision: Sha256;
      expected_workflow_status_hash: NullableSha256;
      expected_artifact_snapshot_hash: Sha256;
      source_grant_id: EntityId;
      expected_source_grant_generation: number;
      target_grant_id: EntityId;
      expected_target_grant_generation: number;
      standalone_game_artifact_id: EntityId;
    }
  | {
      job_id?: EntityId;
      workspace_id: WorkspaceId;
      operation: "game.package.extract";
      expected_root_generation: number;
      expected_source_revision: Sha256;
      expected_workflow_status_hash: NullableSha256;
      expected_artifact_snapshot_hash: Sha256;
      source_grant_id: EntityId;
      expected_source_grant_generation: number;
      target_grant_id: EntityId;
      expected_target_grant_generation: number;
      game_package_artifact_id: EntityId;
    }
  | {
      job_id?: EntityId;
      workspace_id: WorkspaceId;
      expected_root_generation: number;
      expected_source_revision: Sha256;
      expected_workflow_status_hash: NullableSha256;
      expected_artifact_snapshot_hash: Sha256;
      operation: "asset.qa.review";
      qa_report_artifact_id: EntityId;
      output_role: EntityId;
      review_receipt_id: EntityId;
      /**
       * @minItems 1
       * @maxItems 64
       */
      decisions: ["approved" | "rejected", ...("approved" | "rejected")[]];
      /**
       * @maxItems 64
       */
      blockers: PublicToken[];
    }
  | {
      job_id?: EntityId;
      workspace_id: WorkspaceId;
      expected_root_generation: number;
      expected_source_revision: Sha256;
      expected_workflow_status_hash: NullableSha256;
      expected_artifact_snapshot_hash: Sha256;
      operation: "asset.release.authorize";
      /**
       * @minItems 1
       * @maxItems 128
       */
      review_receipt_artifact_ids: [EntityId, ...EntityId[]];
      manifest_id: EntityId;
      assetpack_id: EntityId;
      release_authority_id: EntityId;
      /**
       * @maxItems 64
       */
      blockers: PublicToken[];
      target_grant_id: EntityId;
      expected_target_grant_generation: number;
    }
  | {
      job_id?: EntityId;
      workspace_id: WorkspaceId;
      expected_root_generation: number;
      expected_source_revision: Sha256;
      expected_workflow_status_hash: NullableSha256;
      expected_artifact_snapshot_hash: Sha256;
      operation: "runtime.headless.verify";
      gamepack_artifact_id: EntityId;
      asset_inventory_artifact_id: EntityId;
      assetpack_artifact_id: EntityId;
      asset_release_authority_artifact_id: EntityId;
      runtime_snapshot_artifact_id: EntityId;
      runtime_adapter_registry_artifact_id: EntityId;
      runtime_composition_artifact_id: EntityId;
      runtime_bundle_artifact_id: EntityId;
      source_grant_id: EntityId;
      expected_source_grant_generation: number;
      platform_id: RuntimeHeadlessPlatformId;
      headless_script_artifact_id: EntityId;
      target_grant_id: EntityId;
      expected_target_grant_generation: number;
    };
export type PublicToken = string;
export type RuntimeHeadlessPlatformId = "platform:linux_x86_64" | "platform:windows_x86_64";
export type CreationPreviewOpenParams =
  CreationPreviewPublishedOpenParams | CreationPreviewQaReviewCandidateOpenParams;
export type Response = {
  protocol: "rpg-world-forge.studio_protocol";
  protocol_version: 5;
  kind: "response";
  request_id: EntityId;
  method: Method;
  result: unknown;
} & Response1;
export type Response1 =
  | {
      method: "service.initialize";
      result: InitializeResult;
    }
  | {
      method: "creation_workspace.create";
      result: WorkspaceResult;
    }
  | {
      method:
        | "creation_output_grant.create"
        | "creation_output_grant.get"
        | "creation_output_grant.revoke";
      result: OutputGrantResult;
    }
  | {
      method: "creation_output_grant.list";
      result: OutputGrantListResult;
    }
  | {
      method: "creation_artifact.list";
      result: ArtifactListResult;
    }
  | {
      method: "creation_artifact.inspect";
      result: ArtifactInspectResult;
    }
  | {
      method: "creation_evidence.inspect";
      result: EvidenceInspectResult;
    }
  | {
      method:
        "creation_job.create" | "creation_job.get" | "creation_job.cancel" | "creation_job.recover";
      result: JobResult;
    }
  | {
      method: "creation_job.list";
      result: JobListResult;
    }
  | {
      method: "creation_event.list";
      result: EventListResult;
    }
  | {
      method: "creation_preview.open";
      result: CreationPreviewOpenResult;
    }
  | {
      method: "creation_preview.read";
      result: CreationPreviewReadResult;
    }
  | {
      method: "creation_preview.close";
      result: CreationPreviewCloseResult;
    };
export type WorldForgeStudioCreationOutputGrantV6 = {
  format: "world-forge.studio_creation_output_grant";
  format_version: 1 | 2 | 3 | 4 | 5 | 6;
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "entityId".
   */
  grant_id: string;
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "workspaceId".
   */
  workspace_id: string;
  kind:
    | "generic_assetpack_directory"
    | "game_runtime_bundle_directory"
    | "game_materialization_bundle_directory"
    | "standalone_game_directory"
    | "game_package_file"
    | "headless_evidence_directory";
  display_name: string;
  state: "ready" | "reserved" | "published" | "recovery_required" | "revoked";
  generation: number;
  publication:
    | Publication
    | RuntimePublication
    | MaterializationPublication
    | StandalonePublication
    | GamePackagePublication
    | HeadlessEvidencePublication
    | null;
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
};
/**
 * @maxItems 32
 *
 * This interface was referenced by `WorldForgeStudioCreationEvidenceProjectionV1`'s JSON-Schema
 * via the `definition` "executionRows".
 */
export type ExecutionRows = ExecutionRow[];
/**
 * @maxItems 4096
 *
 * This interface was referenced by `WorldForgeStudioCreationEvidenceProjectionV1`'s JSON-Schema
 * via the `definition` "featureIds".
 */
export type FeatureIds = string[];
export type WorldForgeStudioCreationJobV12 = {
  format: "world-forge.studio_creation_job";
  format_version: 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 11 | 12;
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "entityId".
   */
  job_id: string;
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "workspaceId".
   */
  workspace_id: string;
  operation:
    | "artifact.admit"
    | "asset.process"
    | "asset.release.seal"
    | "creation.compile"
    | "runtime.compose"
    | "runtime.bundle.build"
    | "game.materialization.bundle.build"
    | "game.materialize"
    | "game.package"
    | "game.package.extract"
    | "asset.qa.review"
    | "asset.release.authorize"
    | "runtime.headless.verify";
  operation_params?:
    | AssetProcessOperationParams
    | AssetSealOperationParams
    | RuntimeComposeOperationParams
    | RuntimeBundleOperationParams
    | MaterializationBundleOperationParams
    | GameMaterializeOperationParams
    | GamePackageOperationParams
    | GamePackageExtractOperationParams
    | AssetQaReviewOperationParams
    | AssetReleaseAuthorizeOperationParams
    | RuntimeHeadlessVerifyOperationParams;
  state: "queued" | "running" | "succeeded" | "failed" | "canceled" | "orphaned";
  generation: number;
  authority: Authority2;
  /**
   * @maxItems 128
   */
  inputs: InputReference[];
  progress:
    | "queued"
    | "reserved"
    | "worker_started"
    | "output_published"
    | "registry_committing"
    | "committed"
    | "cleanup_pending"
    | "failed"
    | "canceled"
    | "orphaned";
  result:
    | ResultV1V2
    | ResultV3
    | null
    | ResultV5
    | ResultV6
    | ResultV7
    | ResultV8
    | ResultV9
    | ResultV10
    | ResultV11
    | ResultV12;
  error: Error | null;
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "timestamp".
   */
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "timestamp".
   */
  updated_at: string;
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "sha256".
   */
  record_hash: string;
} & (
    | {
        format_version: 1;
        operation: "artifact.admit" | "creation.compile";
        operation_params?: never;
        result?: ResultV1V2 | null;
      }
    | {
        format_version: 2;
        operation: "asset.process";
        operation_params: AssetProcessOperationParams;
        result?: ResultV1V2 | null;
      }
    | {
        format_version: 3;
        operation: "asset.release.seal";
        operation_params: AssetSealOperationParams;
        result?: ResultV3 | null;
      }
    | {
        format_version: 4;
        operation: "runtime.compose";
        operation_params: RuntimeComposeOperationParams;
        result?: ResultV1V2 | null;
      }
    | {
        format_version: 5;
        operation: "runtime.bundle.build";
        operation_params: RuntimeBundleOperationParams;
        result?: ResultV5 | null;
      }
    | {
        format_version: 6;
        operation: "game.materialization.bundle.build";
        operation_params: MaterializationBundleOperationParams;
        result?: ResultV6 | null;
      }
    | {
        format_version: 7;
        operation: "game.materialize";
        operation_params: GameMaterializeOperationParams;
        result?: ResultV7 | null;
      }
    | {
        format_version: 8;
        operation: "game.package";
        operation_params: GamePackageOperationParams;
        result?: ResultV8 | null;
      }
    | {
        format_version: 9;
        operation: "game.package.extract";
        operation_params: GamePackageExtractOperationParams;
        result?: ResultV9 | null;
      }
    | {
        format_version: 10;
        operation: "asset.qa.review";
        operation_params: AssetQaReviewOperationParams;
        result?: ResultV10 | null;
      }
    | {
        format_version: 11;
        operation: "asset.release.authorize";
        operation_params: AssetReleaseAuthorizeOperationParams;
        result?: ResultV11 | null;
      }
    | {
        format_version: 12;
        operation: "runtime.headless.verify";
        operation_params: RuntimeHeadlessVerifyOperationParams;
        result?: ResultV12 | null;
      }
  );
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "resultV11".
 */
export type ResultV11 = {
  [k: string]: unknown;
} & {
  /**
   * @minItems 3
   * @maxItems 3
   */
  output_artifact_ids: [string, string, string];
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "sha256".
   */
  artifact_snapshot_hash: string;
  analysis_status: "passed" | "failed" | "inconclusive" | "unsupported" | "not_applicable";
  /**
   * @maxItems 128
   */
  reason_codes: string[];
  cleanup_pending: boolean;
  asset_manifest: {
    /**
     * This interface was referenced by `undefined`'s JSON-Schema
     * via the `definition` "entityId".
     */
    manifest_id: string;
    /**
     * This interface was referenced by `undefined`'s JSON-Schema
     * via the `definition` "sha256".
     */
    content_hash: string;
  };
  assetpack: {
    /**
     * This interface was referenced by `undefined`'s JSON-Schema
     * via the `definition` "entityId".
     */
    assetpack_id: string;
    /**
     * This interface was referenced by `undefined`'s JSON-Schema
     * via the `definition` "sha256".
     */
    content_hash: string;
  };
  asset_release_authority: {
    format: "world-forge.asset_release_authority";
    format_version: 1;
    /**
     * This interface was referenced by `undefined`'s JSON-Schema
     * via the `definition` "entityId".
     */
    release_authority_id: string;
    /**
     * This interface was referenced by `undefined`'s JSON-Schema
     * via the `definition` "sha256".
     */
    content_hash: string;
  };
  release_status: "authorized" | "blocked";
  publication: Publication1 | null;
};
export type Timestamp = string;
export type WorldForgeStudioCreationPreviewV1 = {
  format: "world-forge.studio_creation_preview";
  format_version: 1;
  handle: string;
  workspace_id: string;
  assetpack_artifact_id: string;
  output_grant_id: string;
  output_grant_generation: number;
  asset_id: string;
  media_type: "audio/wav" | "image/png";
  byte_length: number;
  sha256: string;
  chunk_bytes: 65536;
  metadata: PngMetadata | WavMetadata;
} & (
  | {
      media_type: "image/png";
      metadata: PngMetadata;
    }
  | {
      media_type: "audio/wav";
      metadata: WavMetadata;
    }
);
export type WorldForgeStudioQAReviewCandidatePreviewV2 = {
  format: "world-forge.studio_creation_preview";
  format_version: 2;
  handle: string;
  workspace_id: string;
  source: {
    kind: "qa_review_candidate";
    qa_report_artifact_id: string;
    asset_id: string;
    output_role: string;
  };
  media_type: "audio/wav" | "image/png";
  byte_length: number;
  sha256: string;
  chunk_bytes: 65536;
  metadata: PngMetadata | WavMetadata;
} & (
  | {
      media_type: "image/png";
      metadata: PngMetadata;
    }
  | {
      media_type: "audio/wav";
      metadata: WavMetadata;
    }
);

export type EmptyParams = Record<string, never>;
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
  presentation_mode: "text" | "2d" | "2_5d" | "3d" | "mixed" | "vr" | "ar";
  runtime_support_intent: "authoring_only" | "compatibility_assessment";
  asset_content_mode?:
    | "authored"
    | "modular"
    | "deterministic_procedural"
    | "generated_at_authoring_time"
    | "player_generated"
    | "hybrid"
    | "not_applicable";
  narrative_requirement: "none";
  narrative_authorship: "none";
  narrative_topology: "none";
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
  presentation_mode: "text" | "2d" | "2_5d" | "3d" | "mixed" | "vr" | "ar";
  runtime_support_intent: "authoring_only" | "compatibility_assessment";
  asset_content_mode?:
    | "authored"
    | "modular"
    | "deterministic_procedural"
    | "generated_at_authoring_time"
    | "player_generated"
    | "hybrid"
    | "not_applicable";
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
}
export interface OutputGrantCreateParams {
  grant_id?: EntityId;
  workspace_id: WorkspaceId;
  kind:
    | "generic_assetpack_directory"
    | "game_runtime_bundle_directory"
    | "game_materialization_bundle_directory"
    | "standalone_game_directory"
    | "game_package_file"
    | "headless_evidence_directory";
  display_name: string;
  path: string;
}
export interface OutputGrantGetParams {
  grant_id: EntityId;
}
export interface OutputGrantListParams {
  workspace_id: WorkspaceId;
  expected_root_generation: number;
  expected_source_revision: Sha256;
  expected_workflow_status_hash: NullableSha256;
  expected_artifact_snapshot_hash: Sha256;
  cursor: EntityId | null;
  limit: number;
}
export interface OutputGrantRevokeParams {
  grant_id: EntityId;
  expected_generation: number;
}
export interface AuthorityProperties {
  workspace_id: WorkspaceId;
  expected_root_generation: number;
  expected_source_revision: Sha256;
  expected_workflow_status_hash: NullableSha256;
  expected_artifact_snapshot_hash: NullableSha256;
}
export interface ArtifactInspectParams {
  workspace_id: WorkspaceId;
  expected_root_generation: number;
  expected_source_revision: Sha256;
  expected_workflow_status_hash: NullableSha256;
  expected_artifact_snapshot_hash: Sha256;
  artifact_id: EntityId;
}
export interface AssetProcessAcceptanceResult {
  criterion_index: number;
  criterion_sha256: Sha256;
  status: "failed" | "passed";
  /**
   * @minItems 1
   * @maxItems 64
   */
  evidence_hashes: [Sha256, ...Sha256[]];
}
export interface JobGetParams {
  job_id: EntityId;
}
export interface JobListParams {
  workspace_id: WorkspaceId;
  state: ("queued" | "running" | "succeeded" | "failed" | "canceled" | "orphaned") | null;
  after_sequence: number;
  limit: number;
}
export interface JobCancelParams {
  job_id: EntityId;
  expected_generation: number;
  expected_record_hash: Sha256;
}
export interface JobRecoverParams {
  job_id: EntityId;
  mode: "resume" | "rollback" | "cleanup";
  expected_generation: number;
  expected_record_hash: Sha256;
}
export interface EventListParams {
  workspace_id: WorkspaceId;
  after_id: number;
  limit: number;
}
export interface CreationPreviewPublishedOpenParams {
  workspace_id: WorkspaceId;
  expected_root_generation: number;
  expected_source_revision: Sha256;
  expected_workflow_status_hash: NullableSha256;
  expected_artifact_snapshot_hash: Sha256;
  assetpack_artifact_id: EntityId;
  output_grant_id: EntityId;
  expected_output_grant_generation: number;
  asset_id: EntityId;
}
export interface CreationPreviewQaReviewCandidateOpenParams {
  source_kind: "qa_review_candidate";
  workspace_id: WorkspaceId;
  expected_root_generation: number;
  expected_source_revision: Sha256;
  expected_workflow_status_hash: NullableSha256;
  expected_artifact_snapshot_hash: Sha256;
  qa_report_artifact_id: EntityId;
  asset_id: EntityId;
  output_role: EntityId;
}
export interface CreationPreviewReadParams {
  handle: string;
  sequence: number;
}
export interface CreationPreviewCloseParams {
  handle: string;
}
export interface InitializeResult {
  service: "world-forge.studio";
  service_version: 5;
  protocol: "rpg-world-forge.studio_protocol";
  protocol_version: 5;
  /**
   * @minItems 18
   * @maxItems 18
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
  ];
  capabilities: {
    creation_evidence_projection: true;
    creation_jobs: true;
    asset_previews: false;
    materialization_execution: true;
    creation_output_grants: true;
    creation_runtime_compose: true;
    creation_runtime_bundle: true;
    creation_materialization_bundle: true;
    game_packaging: true;
    game_package_extraction: true;
    creation_asset_previews: true;
    asset_authority_reviews: true;
    asset_release_authority: true;
    runtime_headless_authority: true;
    creation_preview_pre_release: true;
  };
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
  project: ProjectIdentity;
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
export interface ProjectIdentity {
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
export interface OutputGrantResult {
  grant: WorldForgeStudioCreationOutputGrantV6;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "publication".
 */
export interface Publication {
  format: "world-forge.assetpack";
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
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "sha256".
   */
  inventory_hash: string;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "runtimePublication".
 */
export interface RuntimePublication {
  format: "world-forge.game_runtime_bundle";
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
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "sha256".
   */
  tree_hash: string;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "materializationPublication".
 */
export interface MaterializationPublication {
  format: "world-forge.game_materialization_bundle";
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
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "sha256".
   */
  tree_hash: string;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "standalonePublication".
 */
export interface StandalonePublication {
  format: "world-forge.standalone_game";
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
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "sha256".
   */
  tree_hash: string;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "gamePackagePublication".
 */
export interface GamePackagePublication {
  format: "world-forge.game_package";
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
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "sha256".
   */
  archive_sha256: string;
  size_bytes: number;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "headlessEvidencePublication".
 */
export interface HeadlessEvidencePublication {
  format: "world-forge.headless_evidence_set";
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
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "sha256".
   */
  tree_hash: string;
}
export interface OutputGrantListResult {
  authority: PublicAuthority;
  artifact_snapshot_hash: Sha256;
  /**
   * @maxItems 8
   */
  grants:
    | []
    | [WorldForgeStudioCreationOutputGrantV6]
    | [WorldForgeStudioCreationOutputGrantV6, WorldForgeStudioCreationOutputGrantV6]
    | [
        WorldForgeStudioCreationOutputGrantV6,
        WorldForgeStudioCreationOutputGrantV6,
        WorldForgeStudioCreationOutputGrantV6,
      ]
    | [
        WorldForgeStudioCreationOutputGrantV6,
        WorldForgeStudioCreationOutputGrantV6,
        WorldForgeStudioCreationOutputGrantV6,
        WorldForgeStudioCreationOutputGrantV6,
      ]
    | [
        WorldForgeStudioCreationOutputGrantV6,
        WorldForgeStudioCreationOutputGrantV6,
        WorldForgeStudioCreationOutputGrantV6,
        WorldForgeStudioCreationOutputGrantV6,
        WorldForgeStudioCreationOutputGrantV6,
      ]
    | [
        WorldForgeStudioCreationOutputGrantV6,
        WorldForgeStudioCreationOutputGrantV6,
        WorldForgeStudioCreationOutputGrantV6,
        WorldForgeStudioCreationOutputGrantV6,
        WorldForgeStudioCreationOutputGrantV6,
        WorldForgeStudioCreationOutputGrantV6,
      ]
    | [
        WorldForgeStudioCreationOutputGrantV6,
        WorldForgeStudioCreationOutputGrantV6,
        WorldForgeStudioCreationOutputGrantV6,
        WorldForgeStudioCreationOutputGrantV6,
        WorldForgeStudioCreationOutputGrantV6,
        WorldForgeStudioCreationOutputGrantV6,
        WorldForgeStudioCreationOutputGrantV6,
      ]
    | [
        WorldForgeStudioCreationOutputGrantV6,
        WorldForgeStudioCreationOutputGrantV6,
        WorldForgeStudioCreationOutputGrantV6,
        WorldForgeStudioCreationOutputGrantV6,
        WorldForgeStudioCreationOutputGrantV6,
        WorldForgeStudioCreationOutputGrantV6,
        WorldForgeStudioCreationOutputGrantV6,
        WorldForgeStudioCreationOutputGrantV6,
      ];
  next_cursor: EntityId | null;
}
export interface PublicAuthority {
  workspace_id: WorkspaceId;
  root_generation: number;
  source_revision: Sha256;
  workflow_status_hash: NullableSha256;
}
export interface ArtifactListResult {
  authority: PublicAuthority;
  artifact_snapshot_hash: Sha256;
  /**
   * @maxItems 64
   */
  artifacts: WorldForgeStudioCreationArtifactEvidenceV1[];
  next_cursor: EntityId | null;
  counts: ArtifactCounts;
}
export interface WorldForgeStudioCreationArtifactEvidenceV1 {
  format: "world-forge.studio_creation_artifact";
  format_version: 1;
  /**
   * This interface was referenced by `WorldForgeStudioCreationArtifactEvidenceV1`'s JSON-Schema
   * via the `definition` "entityId".
   */
  artifact_id: string;
  subject: ArtifactIdentity;
  lifecycle: "active" | "invalidated" | "historical" | "candidate";
  /**
   * @minItems 1
   * @maxItems 64
   */
  roles: [string, ...string[]];
  producer: {
    kind:
      "source_snapshot" | "active_phase_report" | "invalidated_phase_report" | "future_candidate";
    phase_id: string | null;
    /**
     * This interface was referenced by `WorldForgeStudioCreationArtifactEvidenceV1`'s JSON-Schema
     * via the `definition` "operationId".
     */
    reference_id: string;
  };
  references: {
    dependency_count: number;
    dependent_count: number;
  };
  authority: Authority;
  /**
   * This interface was referenced by `WorldForgeStudioCreationArtifactEvidenceV1`'s JSON-Schema
   * via the `definition` "sha256".
   */
  record_hash: string;
}
/**
 * This interface was referenced by `WorldForgeStudioCreationArtifactEvidenceV1`'s JSON-Schema
 * via the `definition` "artifactIdentity".
 */
export interface ArtifactIdentity {
  /**
   * This interface was referenced by `WorldForgeStudioCreationArtifactEvidenceV1`'s JSON-Schema
   * via the `definition` "operationId".
   */
  format: string;
  format_version: 1;
  /**
   * This interface was referenced by `WorldForgeStudioCreationArtifactEvidenceV1`'s JSON-Schema
   * via the `definition` "entityId".
   */
  id: string;
  /**
   * This interface was referenced by `WorldForgeStudioCreationArtifactEvidenceV1`'s JSON-Schema
   * via the `definition` "sha256".
   */
  content_hash: string;
}
/**
 * This interface was referenced by `WorldForgeStudioCreationArtifactEvidenceV1`'s JSON-Schema
 * via the `definition` "authority".
 */
export interface Authority {
  /**
   * This interface was referenced by `WorldForgeStudioCreationArtifactEvidenceV1`'s JSON-Schema
   * via the `definition` "workspaceId".
   */
  workspace_id: string;
  root_generation: number;
  /**
   * This interface was referenced by `WorldForgeStudioCreationArtifactEvidenceV1`'s JSON-Schema
   * via the `definition` "sha256".
   */
  source_revision: string;
  workflow_status_hash: string | null;
}
/**
 * This interface was referenced by `WorldForgeStudioCreationEvidenceProjectionV1`'s JSON-Schema
 * via the `definition` "artifactCounts".
 */
export interface ArtifactCounts {
  active: number;
  invalidated: number;
  historical: number;
  candidate: number;
  ignored: number;
}
export interface ArtifactInspectResult {
  authority: PublicAuthority;
  artifact_snapshot_hash: Sha256;
  artifact: WorldForgeStudioCreationArtifactEvidenceV1;
  projection: Projection;
}
export interface Projection {
  projection_kind: string;
  title: string;
  status: string | null;
  /**
   * @maxItems 128
   */
  facts: ProjectionFact[];
  /**
   * @maxItems 128
   */
  lineage: {
    relation: "depends_on";
    artifact_id: EntityId;
    lifecycle: "active" | "invalidated" | "historical" | "candidate";
  }[];
}
export interface ProjectionFact {
  key: string;
  value: string | number | boolean | null | string[];
}
export interface EvidenceInspectResult {
  authority: PublicAuthority;
  artifact_snapshot_hash: Sha256;
  evidence: WorldForgeStudioCreationEvidenceProjectionV1;
}
export interface WorldForgeStudioCreationEvidenceProjectionV1 {
  format: "world-forge.studio_creation_evidence";
  format_version: 1;
  /**
   * This interface was referenced by `WorldForgeStudioCreationEvidenceProjectionV1`'s JSON-Schema
   * via the `definition` "entityId".
   */
  evidence_id: string;
  authority: Authority1;
  /**
   * This interface was referenced by `WorldForgeStudioCreationEvidenceProjectionV1`'s JSON-Schema
   * via the `definition` "sha256".
   */
  artifact_snapshot_hash: string;
  artifact_counts: ArtifactCounts;
  dimensions: Dimensions;
  /**
   * @maxItems 128
   */
  blocker_reason_codes: string[];
  mechanics: Mechanics;
  runtime: Runtime;
  assets: Assets;
  materialization: Materialization;
  readiness: ReadinessIdentity;
  handoff: HandoffIdentity;
  /**
   * This interface was referenced by `WorldForgeStudioCreationEvidenceProjectionV1`'s JSON-Schema
   * via the `definition` "sha256".
   */
  content_hash: string;
}
/**
 * This interface was referenced by `WorldForgeStudioCreationEvidenceProjectionV1`'s JSON-Schema
 * via the `definition` "authority".
 */
export interface Authority1 {
  /**
   * This interface was referenced by `WorldForgeStudioCreationEvidenceProjectionV1`'s JSON-Schema
   * via the `definition` "workspaceId".
   */
  workspace_id: string;
  root_generation: number;
  /**
   * This interface was referenced by `WorldForgeStudioCreationEvidenceProjectionV1`'s JSON-Schema
   * via the `definition` "sha256".
   */
  source_revision: string;
  workflow_status_hash: string | null;
}
/**
 * This interface was referenced by `WorldForgeStudioCreationEvidenceProjectionV1`'s JSON-Schema
 * via the `definition` "dimensions".
 */
export interface Dimensions {
  authoring: "valid" | "invalid";
  compilation: "not_requested" | "compiled" | "unsupported" | "failed";
  assets: "unplanned" | "planned" | "produced" | "processed" | "sealed" | "failed";
  adapter: "absent" | "declared" | "verified";
  execution: ExecutionRows;
  packaging: "unverified" | "verified" | "failed";
  release: "blocked" | "ready";
}
/**
 * This interface was referenced by `WorldForgeStudioCreationEvidenceProjectionV1`'s JSON-Schema
 * via the `definition` "executionRow".
 */
export interface ExecutionRow {
  /**
   * This interface was referenced by `WorldForgeStudioCreationEvidenceProjectionV1`'s JSON-Schema
   * via the `definition` "publicToken".
   */
  platform: string;
  status: "untested" | "headless_verified" | "native_verified" | "failed";
  /**
   * @maxItems 64
   */
  evidence_ids: string[];
}
/**
 * This interface was referenced by `WorldForgeStudioCreationEvidenceProjectionV1`'s JSON-Schema
 * via the `definition` "mechanics".
 */
export interface Mechanics {
  artifact_id: string | null;
  total: number;
  status_counts: {
    supported_current: number;
    game_extension_verified: number;
    authoring_only: number;
    blocked: number;
  };
  required_features: FeatureIds;
  missing_features: FeatureIds;
}
/**
 * This interface was referenced by `WorldForgeStudioCreationEvidenceProjectionV1`'s JSON-Schema
 * via the `definition` "runtime".
 */
export interface Runtime {
  requested_adapter: string | null;
  resolved_adapter: string | null;
  required_features: FeatureIds;
  missing_features: FeatureIds;
  platforms: ExecutionRows;
}
/**
 * This interface was referenced by `WorldForgeStudioCreationEvidenceProjectionV1`'s JSON-Schema
 * via the `definition` "assets".
 */
export interface Assets {
  /**
   * This interface was referenced by `WorldForgeStudioCreationEvidenceProjectionV1`'s JSON-Schema
   * via the `definition` "nullableArtifactId".
   */
  subject_artifact_id: string | null;
  /**
   * This interface was referenced by `WorldForgeStudioCreationEvidenceProjectionV1`'s JSON-Schema
   * via the `definition` "nullableArtifactId".
   */
  target_artifact_id: string | null;
  /**
   * This interface was referenced by `WorldForgeStudioCreationEvidenceProjectionV1`'s JSON-Schema
   * via the `definition` "nullableArtifactId".
   */
  style_artifact_id: string | null;
  /**
   * This interface was referenced by `WorldForgeStudioCreationEvidenceProjectionV1`'s JSON-Schema
   * via the `definition` "nullableArtifactId".
   */
  inventory_artifact_id: string | null;
  /**
   * This interface was referenced by `WorldForgeStudioCreationEvidenceProjectionV1`'s JSON-Schema
   * via the `definition` "nullableArtifactId".
   */
  assetpack_artifact_id: string | null;
  inventory_assets: number;
  lineage_complete: number;
  lineage_partial: number;
  qa_passed: number;
  qa_failed: number;
  licensed: number;
}
/**
 * This interface was referenced by `WorldForgeStudioCreationEvidenceProjectionV1`'s JSON-Schema
 * via the `definition` "materialization".
 */
export interface Materialization {
  enabled: false;
  state: "blocked";
  /**
   * @minItems 1
   * @maxItems 64
   */
  prerequisites: [
    {
      /**
       * This interface was referenced by `WorldForgeStudioCreationEvidenceProjectionV1`'s JSON-Schema
       * via the `definition` "operationId".
       */
      code: string;
      satisfied: boolean;
      message: string;
    },
    ...{
      /**
       * This interface was referenced by `WorldForgeStudioCreationEvidenceProjectionV1`'s JSON-Schema
       * via the `definition` "operationId".
       */
      code: string;
      satisfied: boolean;
      message: string;
    }[],
  ];
}
/**
 * This interface was referenced by `WorldForgeStudioCreationEvidenceProjectionV1`'s JSON-Schema
 * via the `definition` "readinessIdentity".
 */
export interface ReadinessIdentity {
  format: "world-forge.creation_readiness";
  format_version: 1;
  /**
   * This interface was referenced by `WorldForgeStudioCreationEvidenceProjectionV1`'s JSON-Schema
   * via the `definition` "entityId".
   */
  id: string;
  /**
   * This interface was referenced by `WorldForgeStudioCreationEvidenceProjectionV1`'s JSON-Schema
   * via the `definition` "sha256".
   */
  content_hash: string;
}
/**
 * This interface was referenced by `WorldForgeStudioCreationEvidenceProjectionV1`'s JSON-Schema
 * via the `definition` "handoffIdentity".
 */
export interface HandoffIdentity {
  format: "world-forge.creation_handoff";
  format_version: 1;
  /**
   * This interface was referenced by `WorldForgeStudioCreationEvidenceProjectionV1`'s JSON-Schema
   * via the `definition` "entityId".
   */
  id: string;
  /**
   * This interface was referenced by `WorldForgeStudioCreationEvidenceProjectionV1`'s JSON-Schema
   * via the `definition` "sha256".
   */
  content_hash: string;
}
export interface JobResult {
  job: WorldForgeStudioCreationJobV12;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "assetProcessOperationParams".
 */
export interface AssetProcessOperationParams {
  /**
   * @minItems 1
   * @maxItems 4
   */
  license_artifact_ids:
    [string] | [string, string] | [string, string, string] | [string, string, string, string];
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "entityId".
   */
  recipe_id: string;
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "entityId".
   */
  processing_receipt_id: string;
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "entityId".
   */
  qa_report_id: string;
  /**
   * @minItems 1
   * @maxItems 64
   */
  acceptance_results: [AcceptanceResult, ...AcceptanceResult[]];
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "acceptanceResult".
 */
export interface AcceptanceResult {
  criterion_index: number;
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "sha256".
   */
  criterion_sha256: string;
  status: "failed" | "passed";
  /**
   * @minItems 1
   * @maxItems 64
   */
  evidence_hashes: [string, ...string[]];
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "assetSealOperationParams".
 */
export interface AssetSealOperationParams {
  /**
   * @minItems 1
   * @maxItems 128
   */
  qa_report_artifact_ids: [string, ...string[]];
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "entityId".
   */
  manifest_id: string;
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "entityId".
   */
  target_grant_id: string;
  target_grant_generation: number;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "runtimeComposeOperationParams".
 */
export interface RuntimeComposeOperationParams {
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "entityId".
   */
  gamepack_artifact_id: string;
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "entityId".
   */
  asset_inventory_artifact_id: string;
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "entityId".
   */
  assetpack_artifact_id: string;
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "entityId".
   */
  target_grant_id: string;
  target_grant_generation: number;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "runtimeBundleOperationParams".
 */
export interface RuntimeBundleOperationParams {
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "entityId".
   */
  gamepack_artifact_id: string;
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "entityId".
   */
  asset_inventory_artifact_id: string;
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "entityId".
   */
  assetpack_artifact_id: string;
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "entityId".
   */
  runtime_snapshot_artifact_id: string;
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "entityId".
   */
  runtime_adapter_registry_artifact_id: string;
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "entityId".
   */
  runtime_composition_artifact_id: string;
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "entityId".
   */
  runtime_support_report_artifact_id: string;
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "entityId".
   */
  source_grant_id: string;
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "entityId".
   */
  target_grant_id: string;
  source_grant_generation: number;
  target_grant_generation: number;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "materializationBundleOperationParams".
 */
export interface MaterializationBundleOperationParams {
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "entityId".
   */
  runtime_bundle_artifact_id: string;
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "entityId".
   */
  source_grant_id: string;
  source_grant_generation: number;
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "entityId".
   */
  target_grant_id: string;
  target_grant_generation: number;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "gameMaterializeOperationParams".
 */
export interface GameMaterializeOperationParams {
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "entityId".
   */
  source_grant_id: string;
  source_grant_generation: number;
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "entityId".
   */
  target_grant_id: string;
  target_grant_generation: number;
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "entityId".
   */
  materialization_bundle_artifact_id: string;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "gamePackageOperationParams".
 */
export interface GamePackageOperationParams {
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "entityId".
   */
  source_grant_id: string;
  source_grant_generation: number;
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "entityId".
   */
  target_grant_id: string;
  target_grant_generation: number;
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "entityId".
   */
  standalone_game_artifact_id: string;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "gamePackageExtractOperationParams".
 */
export interface GamePackageExtractOperationParams {
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "entityId".
   */
  source_grant_id: string;
  source_grant_generation: number;
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "entityId".
   */
  target_grant_id: string;
  target_grant_generation: number;
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "entityId".
   */
  game_package_artifact_id: string;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "assetQaReviewOperationParams".
 */
export interface AssetQaReviewOperationParams {
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "entityId".
   */
  qa_report_artifact_id: string;
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "entityId".
   */
  output_role: string;
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "entityId".
   */
  review_receipt_id: string;
  /**
   * @minItems 1
   * @maxItems 64
   */
  decisions: ["approved" | "rejected", ...("approved" | "rejected")[]];
  /**
   * @maxItems 64
   */
  blockers: string[];
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "assetReleaseAuthorizeOperationParams".
 */
export interface AssetReleaseAuthorizeOperationParams {
  /**
   * @minItems 1
   * @maxItems 128
   */
  review_receipt_artifact_ids: [string, ...string[]];
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "entityId".
   */
  manifest_id: string;
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "entityId".
   */
  assetpack_id: string;
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "entityId".
   */
  release_authority_id: string;
  /**
   * @maxItems 64
   */
  blockers: string[];
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "entityId".
   */
  target_grant_id: string;
  target_grant_generation: number;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "runtimeHeadlessVerifyOperationParams".
 */
export interface RuntimeHeadlessVerifyOperationParams {
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "entityId".
   */
  gamepack_artifact_id: string;
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "entityId".
   */
  asset_inventory_artifact_id: string;
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "entityId".
   */
  assetpack_artifact_id: string;
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "entityId".
   */
  asset_release_authority_artifact_id: string;
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "entityId".
   */
  runtime_snapshot_artifact_id: string;
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "entityId".
   */
  runtime_adapter_registry_artifact_id: string;
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "entityId".
   */
  runtime_composition_artifact_id: string;
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "entityId".
   */
  runtime_bundle_artifact_id: string;
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "entityId".
   */
  source_grant_id: string;
  expected_source_grant_generation: number;
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "runtimeHeadlessPlatformId".
   */
  platform_id: "platform:linux_x86_64" | "platform:windows_x86_64";
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "entityId".
   */
  headless_script_artifact_id: string;
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "entityId".
   */
  target_grant_id: string;
  expected_target_grant_generation: number;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "authority".
 */
export interface Authority2 {
  root_generation: number;
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "sha256".
   */
  source_revision: string;
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "nullableSha256".
   */
  workflow_status_hash: string | null;
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "sha256".
   */
  artifact_snapshot_hash: string;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "inputReference".
 */
export interface InputReference {
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "entityId".
   */
  artifact_id: string;
  subject: ArtifactIdentity1;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "artifactIdentity".
 */
export interface ArtifactIdentity1 {
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "operationId".
   */
  format: string;
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
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "resultV1V2".
 */
export interface ResultV1V2 {
  /**
   * @minItems 1
   * @maxItems 16
   */
  output_artifact_ids:
    | [string]
    | [string, string]
    | [string, string, string]
    | [string, string, string, string]
    | [string, string, string, string, string]
    | [string, string, string, string, string, string]
    | [string, string, string, string, string, string, string]
    | [string, string, string, string, string, string, string, string]
    | [string, string, string, string, string, string, string, string, string]
    | [string, string, string, string, string, string, string, string, string, string]
    | [string, string, string, string, string, string, string, string, string, string, string]
    | [
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
      ]
    | [
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
      ]
    | [
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
      ]
    | [
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
      ]
    | [
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
      ];
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "sha256".
   */
  artifact_snapshot_hash: string;
  analysis_status: "passed" | "failed" | "inconclusive" | "unsupported" | "not_applicable";
  /**
   * @maxItems 128
   */
  reason_codes: string[];
  cleanup_pending: boolean;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "resultV3".
 */
export interface ResultV3 {
  /**
   * @minItems 1
   * @maxItems 16
   */
  output_artifact_ids:
    | [string]
    | [string, string]
    | [string, string, string]
    | [string, string, string, string]
    | [string, string, string, string, string]
    | [string, string, string, string, string, string]
    | [string, string, string, string, string, string, string]
    | [string, string, string, string, string, string, string, string]
    | [string, string, string, string, string, string, string, string, string]
    | [string, string, string, string, string, string, string, string, string, string]
    | [string, string, string, string, string, string, string, string, string, string, string]
    | [
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
      ]
    | [
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
      ]
    | [
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
      ]
    | [
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
      ]
    | [
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
      ];
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "sha256".
   */
  artifact_snapshot_hash: string;
  analysis_status: "passed" | "failed" | "inconclusive" | "unsupported" | "not_applicable";
  /**
   * @maxItems 128
   */
  reason_codes: string[];
  cleanup_pending: boolean;
  publication: Publication1;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "publication".
 */
export interface Publication1 {
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "entityId".
   */
  grant_id: string;
  grant_generation: number;
  kind: "generic_assetpack_directory";
  state: "published";
  assetpack: AssetpackIdentity;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "assetpackIdentity".
 */
export interface AssetpackIdentity {
  format: "world-forge.assetpack";
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
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "sha256".
   */
  inventory_hash: string;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "resultV5".
 */
export interface ResultV5 {
  /**
   * @minItems 1
   * @maxItems 16
   */
  output_artifact_ids:
    | [string]
    | [string, string]
    | [string, string, string]
    | [string, string, string, string]
    | [string, string, string, string, string]
    | [string, string, string, string, string, string]
    | [string, string, string, string, string, string, string]
    | [string, string, string, string, string, string, string, string]
    | [string, string, string, string, string, string, string, string, string]
    | [string, string, string, string, string, string, string, string, string, string]
    | [string, string, string, string, string, string, string, string, string, string, string]
    | [
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
      ]
    | [
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
      ]
    | [
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
      ]
    | [
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
      ]
    | [
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
      ];
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "sha256".
   */
  artifact_snapshot_hash: string;
  analysis_status: "passed" | "failed" | "inconclusive" | "unsupported" | "not_applicable";
  /**
   * @maxItems 128
   */
  reason_codes: string[];
  cleanup_pending: boolean;
  publication: RuntimeBundlePublication;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "runtimeBundlePublication".
 */
export interface RuntimeBundlePublication {
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "entityId".
   */
  grant_id: string;
  grant_generation: number;
  kind: "game_runtime_bundle_directory";
  state: "published";
  runtime_bundle: RuntimeBundleIdentity;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "runtimeBundleIdentity".
 */
export interface RuntimeBundleIdentity {
  format: "world-forge.game_runtime_bundle";
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
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "sha256".
   */
  tree_hash: string;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "resultV6".
 */
export interface ResultV6 {
  /**
   * @minItems 1
   * @maxItems 16
   */
  output_artifact_ids:
    | [string]
    | [string, string]
    | [string, string, string]
    | [string, string, string, string]
    | [string, string, string, string, string]
    | [string, string, string, string, string, string]
    | [string, string, string, string, string, string, string]
    | [string, string, string, string, string, string, string, string]
    | [string, string, string, string, string, string, string, string, string]
    | [string, string, string, string, string, string, string, string, string, string]
    | [string, string, string, string, string, string, string, string, string, string, string]
    | [
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
      ]
    | [
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
      ]
    | [
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
      ]
    | [
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
      ]
    | [
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
      ];
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "sha256".
   */
  artifact_snapshot_hash: string;
  analysis_status: "passed" | "failed" | "inconclusive" | "unsupported" | "not_applicable";
  /**
   * @maxItems 128
   */
  reason_codes: string[];
  cleanup_pending: boolean;
  publication: MaterializationBundlePublication;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "materializationBundlePublication".
 */
export interface MaterializationBundlePublication {
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "entityId".
   */
  grant_id: string;
  grant_generation: number;
  kind: "game_materialization_bundle_directory";
  state: "published";
  materialization_bundle: MaterializationBundleIdentity;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "materializationBundleIdentity".
 */
export interface MaterializationBundleIdentity {
  format: "world-forge.game_materialization_bundle";
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
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "sha256".
   */
  tree_hash: string;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "resultV7".
 */
export interface ResultV7 {
  /**
   * @minItems 1
   * @maxItems 16
   */
  output_artifact_ids:
    | [string]
    | [string, string]
    | [string, string, string]
    | [string, string, string, string]
    | [string, string, string, string, string]
    | [string, string, string, string, string, string]
    | [string, string, string, string, string, string, string]
    | [string, string, string, string, string, string, string, string]
    | [string, string, string, string, string, string, string, string, string]
    | [string, string, string, string, string, string, string, string, string, string]
    | [string, string, string, string, string, string, string, string, string, string, string]
    | [
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
      ]
    | [
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
      ]
    | [
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
      ]
    | [
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
      ]
    | [
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
      ];
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "sha256".
   */
  artifact_snapshot_hash: string;
  analysis_status: "passed" | "failed" | "inconclusive" | "unsupported" | "not_applicable";
  /**
   * @maxItems 128
   */
  reason_codes: string[];
  cleanup_pending: boolean;
  publication: StandaloneGamePublication;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "standaloneGamePublication".
 */
export interface StandaloneGamePublication {
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "entityId".
   */
  grant_id: string;
  grant_generation: number;
  kind: "standalone_game_directory";
  state: "published";
  standalone_game: StandaloneGameIdentity;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "standaloneGameIdentity".
 */
export interface StandaloneGameIdentity {
  format: "world-forge.standalone_game";
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
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "sha256".
   */
  tree_hash: string;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "resultV8".
 */
export interface ResultV8 {
  /**
   * @minItems 1
   * @maxItems 16
   */
  output_artifact_ids:
    | [string]
    | [string, string]
    | [string, string, string]
    | [string, string, string, string]
    | [string, string, string, string, string]
    | [string, string, string, string, string, string]
    | [string, string, string, string, string, string, string]
    | [string, string, string, string, string, string, string, string]
    | [string, string, string, string, string, string, string, string, string]
    | [string, string, string, string, string, string, string, string, string, string]
    | [string, string, string, string, string, string, string, string, string, string, string]
    | [
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
      ]
    | [
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
      ]
    | [
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
      ]
    | [
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
      ]
    | [
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
      ];
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "sha256".
   */
  artifact_snapshot_hash: string;
  analysis_status: "passed" | "failed" | "inconclusive" | "unsupported" | "not_applicable";
  /**
   * @maxItems 128
   */
  reason_codes: string[];
  cleanup_pending: boolean;
  publication: GamePackagePublication1;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "gamePackagePublication".
 */
export interface GamePackagePublication1 {
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "entityId".
   */
  grant_id: string;
  grant_generation: number;
  kind: "game_package_file";
  state: "published";
  game_package: GamePackageIdentity;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "gamePackageIdentity".
 */
export interface GamePackageIdentity {
  format: "world-forge.game_package";
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
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "sha256".
   */
  archive_sha256: string;
  size_bytes: number;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "resultV9".
 */
export interface ResultV9 {
  /**
   * @minItems 1
   * @maxItems 16
   */
  output_artifact_ids:
    | [string]
    | [string, string]
    | [string, string, string]
    | [string, string, string, string]
    | [string, string, string, string, string]
    | [string, string, string, string, string, string]
    | [string, string, string, string, string, string, string]
    | [string, string, string, string, string, string, string, string]
    | [string, string, string, string, string, string, string, string, string]
    | [string, string, string, string, string, string, string, string, string, string]
    | [string, string, string, string, string, string, string, string, string, string, string]
    | [
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
      ]
    | [
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
      ]
    | [
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
      ]
    | [
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
      ]
    | [
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
      ];
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "sha256".
   */
  artifact_snapshot_hash: string;
  analysis_status: "passed" | "failed" | "inconclusive" | "unsupported" | "not_applicable";
  /**
   * @maxItems 128
   */
  reason_codes: string[];
  cleanup_pending: boolean;
  publication: StandaloneGamePublication;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "resultV10".
 */
export interface ResultV10 {
  /**
   * @minItems 1
   * @maxItems 16
   */
  output_artifact_ids:
    | [string]
    | [string, string]
    | [string, string, string]
    | [string, string, string, string]
    | [string, string, string, string, string]
    | [string, string, string, string, string, string]
    | [string, string, string, string, string, string, string]
    | [string, string, string, string, string, string, string, string]
    | [string, string, string, string, string, string, string, string, string]
    | [string, string, string, string, string, string, string, string, string, string]
    | [string, string, string, string, string, string, string, string, string, string, string]
    | [
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
      ]
    | [
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
      ]
    | [
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
      ]
    | [
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
      ]
    | [
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
        string,
      ];
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "sha256".
   */
  artifact_snapshot_hash: string;
  analysis_status: "passed" | "failed" | "inconclusive" | "unsupported" | "not_applicable";
  /**
   * @maxItems 128
   */
  reason_codes: string[];
  cleanup_pending: boolean;
  review_receipt: {
    format: "world-forge.asset_qa_review_receipt";
    format_version: 1;
    /**
     * This interface was referenced by `undefined`'s JSON-Schema
     * via the `definition` "entityId".
     */
    review_receipt_id: string;
    /**
     * This interface was referenced by `undefined`'s JSON-Schema
     * via the `definition` "sha256".
     */
    content_hash: string;
  };
  review_status: "approved" | "rejected";
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "resultV12".
 */
export interface ResultV12 {
  /**
   * @minItems 3
   * @maxItems 3
   */
  output_artifact_ids: [string, string, string];
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "sha256".
   */
  artifact_snapshot_hash: string;
  analysis_status: "passed" | "failed" | "inconclusive" | "unsupported" | "not_applicable";
  /**
   * @maxItems 128
   */
  reason_codes: string[];
  cleanup_pending: boolean;
  runtime_support_authority: {
    format: "world-forge.runtime_support_authority";
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
  };
  runtime_evidence: {
    format: "world-forge.runtime_evidence";
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
  };
  runtime_support_report: {
    format: "world-forge.runtime_support_report";
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
  };
  release_status: "blocked";
  native_status: "unavailable";
  supported: false;
  publication: HeadlessPublication;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "headlessPublication".
 */
export interface HeadlessPublication {
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "entityId".
   */
  grant_id: string;
  grant_generation: number;
  kind: "headless_evidence_directory";
  state: "published";
  headless_evidence_set: {
    format: "world-forge.headless_evidence_set";
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
    /**
     * This interface was referenced by `undefined`'s JSON-Schema
     * via the `definition` "sha256".
     */
    tree_hash: string;
  };
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "error".
 */
export interface Error {
  code:
    | "authority_changed"
    | "canceled"
    | "input_changed"
    | "internal_error"
    | "invalid_artifact"
    | "invalid_project"
    | "recovery_ambiguous"
    | "recovery_required"
    | "service_restart"
    | "timeout"
    | "worker_crashed"
    | "worker_protocol";
  message: string;
  retryable: boolean;
  recovery_evidence?: RecoveryEvidence;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "recoveryEvidence".
 */
export interface RecoveryEvidence {
  stage?: RetainedEvidenceItem;
  journal?: RetainedEvidenceItem;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "retainedEvidenceItem".
 */
export interface RetainedEvidenceItem {
  locator: string;
  identity: [number, number] | null;
  retention: "active";
}
export interface JobListResult {
  /**
   * @maxItems 8
   */
  jobs:
    | []
    | [WorldForgeStudioCreationJobV12]
    | [WorldForgeStudioCreationJobV12, WorldForgeStudioCreationJobV12]
    | [
        WorldForgeStudioCreationJobV12,
        WorldForgeStudioCreationJobV12,
        WorldForgeStudioCreationJobV12,
      ]
    | [
        WorldForgeStudioCreationJobV12,
        WorldForgeStudioCreationJobV12,
        WorldForgeStudioCreationJobV12,
        WorldForgeStudioCreationJobV12,
      ]
    | [
        WorldForgeStudioCreationJobV12,
        WorldForgeStudioCreationJobV12,
        WorldForgeStudioCreationJobV12,
        WorldForgeStudioCreationJobV12,
        WorldForgeStudioCreationJobV12,
      ]
    | [
        WorldForgeStudioCreationJobV12,
        WorldForgeStudioCreationJobV12,
        WorldForgeStudioCreationJobV12,
        WorldForgeStudioCreationJobV12,
        WorldForgeStudioCreationJobV12,
        WorldForgeStudioCreationJobV12,
      ]
    | [
        WorldForgeStudioCreationJobV12,
        WorldForgeStudioCreationJobV12,
        WorldForgeStudioCreationJobV12,
        WorldForgeStudioCreationJobV12,
        WorldForgeStudioCreationJobV12,
        WorldForgeStudioCreationJobV12,
        WorldForgeStudioCreationJobV12,
      ]
    | [
        WorldForgeStudioCreationJobV12,
        WorldForgeStudioCreationJobV12,
        WorldForgeStudioCreationJobV12,
        WorldForgeStudioCreationJobV12,
        WorldForgeStudioCreationJobV12,
        WorldForgeStudioCreationJobV12,
        WorldForgeStudioCreationJobV12,
        WorldForgeStudioCreationJobV12,
      ];
  next_sequence: number | null;
}
export interface EventListResult {
  /**
   * @maxItems 256
   */
  events: CreationEvent[];
}
export interface CreationEvent {
  event_id: number;
  workspace_id: WorkspaceId;
  topic: string;
  entity_type: string;
  entity_id: EntityId;
  payload: {};
  created_at: Timestamp;
}
export interface CreationPreviewOpenResult {
  preview: WorldForgeStudioCreationPreviewV1 | WorldForgeStudioQAReviewCandidatePreviewV2;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "pngMetadata".
 */
export interface PngMetadata {
  kind: "png";
  width: number;
  height: number;
  mode: "grayscale8" | "rgb8" | "rgba8";
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "wavMetadata".
 */
export interface WavMetadata {
  kind: "wav_pcm16";
  channels: 1 | 2;
  sample_rate: number;
  frames: number;
  sample_width: 2;
}
export interface CreationPreviewReadResult {
  handle: string;
  sequence: number;
  data_base64: string;
  byte_length: number;
  cumulative_bytes: number;
  cumulative_sha256: Sha256;
  eof: boolean;
}
export interface CreationPreviewCloseResult {
  handle: string;
  closed: true;
}
export interface ErrorEnvelope {
  protocol: "rpg-world-forge.studio_protocol";
  protocol_version: 5;
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

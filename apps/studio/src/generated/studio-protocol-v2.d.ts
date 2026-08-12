/* AUTO-GENERATED from schemas/studio-protocol-v2.schema.json. Do not edit by hand. */
/* eslint-disable @typescript-eslint/no-empty-object-type */

export type ForgeStudioExternalArtifactApplicationProtocolV2 = {
  protocol: "rpg-world-forge.studio_protocol";
  protocol_version: 2;
  kind: "request" | "response" | "error";
  request_id: EntityId | null;
  [k: string]: unknown;
} & (Request | Response | ErrorEnvelope);
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "entityId".
 */
export type EntityId = string;
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "workspaceId".
 */
export type WorkspaceId = string;
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "sha256".
 */
export type Sha256 = string;
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "method".
 */
export type Method =
  | "service.initialize"
  | "external_grant.create"
  | "external_grant.get"
  | "external_grant.revoke"
  | "job.create"
  | "job.get"
  | "job.list"
  | "job.cancel"
  | "job.recover";
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "grantCreateParams".
 */
export type GrantCreateParams = {
  grant_id?: EntityId;
  workspace_id: WorkspaceId;
  operation: "game.materialize" | "game.package" | "game.package.extract";
  role: "source" | "target";
  artifact_kind: "game_materialization_bundle" | "standalone_game" | "game_package";
  display_name: string;
  path: string;
  expected_content_hash: Sha256 | null;
} & GrantCreateParams1;
export type GrantCreateParams1 =
  | {
      operation: "game.materialize";
      role: "source";
      artifact_kind: "game_materialization_bundle";
      [k: string]: unknown;
    }
  | {
      operation: "game.materialize";
      role: "target";
      artifact_kind: "standalone_game";
      [k: string]: unknown;
    }
  | {
      operation: "game.package";
      role: "source";
      artifact_kind: "standalone_game";
      [k: string]: unknown;
    }
  | {
      operation: "game.package";
      role: "target";
      artifact_kind: "game_package";
      [k: string]: unknown;
    }
  | {
      operation: "game.package.extract";
      role: "source";
      artifact_kind: "game_package";
      [k: string]: unknown;
    }
  | {
      operation: "game.package.extract";
      role: "target";
      artifact_kind: "standalone_game";
      [k: string]: unknown;
    };
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "jobCreateParams".
 */
export type JobCreateParams = {
  job_id?: EntityId;
  workspace_id: WorkspaceId;
  operation: "game.materialize" | "game.package" | "game.package.extract";
  input: MaterializeInput | PackageInput | ExtractInput;
} & JobCreateParams1;
export type JobCreateParams1 =
  | {
      operation: "game.materialize";
      input: MaterializeInput;
      [k: string]: unknown;
    }
  | {
      operation: "game.package";
      input: PackageInput;
      [k: string]: unknown;
    }
  | {
      operation: "game.package.extract";
      input: ExtractInput;
      [k: string]: unknown;
    };
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "request".
 */
export type Request = Base & {
  protocol: "rpg-world-forge.studio_protocol";
  protocol_version: 2;
  kind: "request";
  request_id: EntityId;
  method: Method;
  params: {
    [k: string]: unknown;
  };
} & (
    | {
        method: "service.initialize";
        params: {};
        [k: string]: unknown;
      }
    | {
        method: "external_grant.create";
        params: GrantCreateParams;
        [k: string]: unknown;
      }
    | {
        method: "external_grant.get" | "external_grant.revoke";
        params: GrantIdParams;
        [k: string]: unknown;
      }
    | {
        method: "job.create";
        params: JobCreateParams;
        [k: string]: unknown;
      }
    | {
        method: "job.get" | "job.cancel";
        params: JobIdParams;
        [k: string]: unknown;
      }
    | {
        method: "job.list";
        params: JobListParams;
        [k: string]: unknown;
      }
    | {
        method: "job.recover";
        params: JobRecoverParams;
        [k: string]: unknown;
      }
  );
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "response".
 */
export type Response = Base & {
  protocol: "rpg-world-forge.studio_protocol";
  protocol_version: 2;
  kind: "response";
  request_id: EntityId;
  method: Method;
  result: {
    [k: string]: unknown;
  };
} & (
    | {
        method: "service.initialize";
        result: {
          [k: string]: unknown;
        };
        [k: string]: unknown;
      }
    | {
        method: "external_grant.create" | "external_grant.get" | "external_grant.revoke";
        result: {
          grant: ForgeStudioExternalArtifactGrantV1;
        };
        [k: string]: unknown;
      }
    | {
        method: "job.create" | "job.get" | "job.cancel" | "job.recover";
        result: {
          job: ForgeStudioExternalArtifactJobV3;
        };
        [k: string]: unknown;
      }
    | {
        method: "job.list";
        result: {
          /**
           * @maxItems 1000
           */
          jobs: ForgeStudioExternalArtifactJobV3[];
        };
        [k: string]: unknown;
      }
  );
export type ForgeStudioExternalArtifactGrantV1 = {
  [k: string]: unknown;
} & {
  format: "rpg-world-forge.studio_external_grant";
  format_version: 1;
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
  operation: "game.materialize" | "game.package" | "game.package.extract";
  role: "source" | "target";
  artifact_kind: "game_materialization_bundle" | "standalone_game" | "game_package";
  display_name: string;
  state: "ready" | "reserved" | "recovery_required" | "consumed" | "revoked";
  expected_content_hash: string | null;
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
        operation: "game.materialize";
        role: "source";
        artifact_kind: "game_materialization_bundle";
        [k: string]: unknown;
      }
    | {
        operation: "game.materialize";
        role: "target";
        artifact_kind: "standalone_game";
        [k: string]: unknown;
      }
    | {
        operation: "game.package";
        role: "source";
        artifact_kind: "standalone_game";
        [k: string]: unknown;
      }
    | {
        operation: "game.package";
        role: "target";
        artifact_kind: "game_package";
        [k: string]: unknown;
      }
    | {
        operation: "game.package.extract";
        role: "source";
        artifact_kind: "game_package";
        [k: string]: unknown;
      }
    | {
        operation: "game.package.extract";
        role: "target";
        artifact_kind: "standalone_game";
        [k: string]: unknown;
      }
  );
export type ForgeStudioExternalArtifactJobV3 = (
  MaterializeOperation | PackageOperation | ExtractOperation
) & {
  [k: string]: unknown;
} & {
  format: "rpg-world-forge.studio_job";
  format_version: 3;
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
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "externalOperation".
   */
  operation: "game.materialize" | "game.package" | "game.package.extract";
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "jobState".
   */
  state: "queued" | "running" | "succeeded" | "failed" | "canceled" | "orphaned";
  input: MaterializeInput | PackageInput | ExtractInput;
  result: MaterializeResult | PackageResult | ExtractResult | null;
  error: JobError | null;
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
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "base".
 */
export interface Base {
  protocol: "rpg-world-forge.studio_protocol";
  protocol_version: 2;
  kind: "request" | "response" | "error";
  request_id: EntityId;
  [k: string]: unknown;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "grantIdParams".
 */
export interface GrantIdParams {
  grant_id: EntityId;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "materializeInput".
 */
export interface MaterializeInput {
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
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "sha256".
   */
  expected_materialization_hash: string;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "packageInput".
 */
export interface PackageInput {
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
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "sha256".
   */
  expected_game_hash: string;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "extractInput".
 */
export interface ExtractInput {
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
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "sha256".
   */
  expected_package_hash: string;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "jobIdParams".
 */
export interface JobIdParams {
  job_id: EntityId;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "jobListParams".
 */
export interface JobListParams {
  workspace_id?: WorkspaceId;
  state?: "queued" | "running" | "succeeded" | "failed" | "canceled" | "orphaned";
  limit?: number;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "jobRecoverParams".
 */
export interface JobRecoverParams {
  job_id: EntityId;
  action: "resume" | "rollback";
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "materializeOperation".
 */
export interface MaterializeOperation {
  operation: "game.materialize";
  input: MaterializeInput;
  result: MaterializeResult | null;
  [k: string]: unknown;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "materializeResult".
 */
export interface MaterializeResult {
  operation: "game.materialize";
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "entityId".
   */
  game_id: string;
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "sha256".
   */
  standalone_hash: string;
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "sha256".
   */
  payload_lock_hash: string;
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "sha256".
   */
  runtime_bundle_hash: string;
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "entityId".
   */
  target_grant_id: string;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "packageOperation".
 */
export interface PackageOperation {
  operation: "game.package";
  input: PackageInput;
  result: PackageResult | null;
  [k: string]: unknown;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "packageResult".
 */
export interface PackageResult {
  operation: "game.package";
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "entityId".
   */
  package_id: string;
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
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "entityId".
   */
  game_id: string;
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "sha256".
   */
  game_hash: string;
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "entityId".
   */
  target_grant_id: string;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "extractOperation".
 */
export interface ExtractOperation {
  operation: "game.package.extract";
  input: ExtractInput;
  result: ExtractResult | null;
  [k: string]: unknown;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "extractResult".
 */
export interface ExtractResult {
  operation: "game.package.extract";
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "entityId".
   */
  package_id: string;
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "sha256".
   */
  package_hash: string;
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "sha256".
   */
  archive_sha256: string;
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "entityId".
   */
  game_id: string;
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "sha256".
   */
  game_hash: string;
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "sha256".
   */
  payload_lock_hash: string;
  /**
   * This interface was referenced by `undefined`'s JSON-Schema
   * via the `definition` "entityId".
   */
  target_grant_id: string;
}
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "jobError".
 */
export interface JobError {
  code:
    | "execution_failed"
    | "invalid_workspace"
    | "timeout"
    | "worker_crashed"
    | "worker_protocol"
    | "recovery_ambiguous"
    | "recovery_failed"
    | "recovery_required"
    | "source_changed"
    | "target_changed";
  message: string;
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
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "errorEnvelope".
 */
export interface ErrorEnvelope {
  protocol: "rpg-world-forge.studio_protocol";
  protocol_version: 2;
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
    details: {
      [k: string]: unknown;
    };
  };
}

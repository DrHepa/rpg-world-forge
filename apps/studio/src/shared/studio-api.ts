import type {
  Error as StudioErrorEnvelope,
  Event as StudioEventEnvelope,
  Method as StudioMethod,
  Response as StudioResponseEnvelope,
} from "../generated/studio-protocol";

export type {
  StudioErrorEnvelope,
  StudioEventEnvelope,
  StudioMethod,
  StudioResponseEnvelope,
};

export type StudioReplyEnvelope = StudioResponseEnvelope | StudioErrorEnvelope;

export type ForgeServiceState =
  | "stopped"
  | "starting"
  | "ready"
  | "unavailable"
  | "crashed";

export interface ForgeServiceStatus {
  state: ForgeServiceState;
  message: string;
  pid: number | null;
}

export type StudioReadMethod =
  | "workspace.list"
  | "events.list"
  | "changeset.list"
  | "job.list";

export interface EventsListParams {
  workspace_id?: string;
  after_id?: number;
  limit?: number;
}

export interface ChangesetsListParams {
  workspace_id?: string;
  status?: "staged" | "approved" | "rejected" | "applied";
  limit?: number;
}

export interface JobsListParams {
  workspace_id?: string;
  state?:
    | "queued"
    | "running"
    | "awaiting_approval"
    | "awaiting_user"
    | "paused"
    | "succeeded"
    | "failed"
    | "canceled"
    | "orphaned";
  limit?: number;
}

export type StudioActivityEvent =
  | {
      type: "service-status";
      status: ForgeServiceStatus;
    }
  | {
      type: "studio-event";
      envelope: StudioEventEnvelope;
    }
  | {
      type: "service-stderr";
      text: string;
    };

export interface StudioClientError {
  code: "invalid_request" | "service_unavailable" | "timeout" | "cancelled" | "internal_error";
  message: string;
}

export type StudioClientResult<T> =
  | { ok: true; value: T }
  | { ok: false; error: StudioClientError };

export interface ForgeStudioApi {
  initialize(): Promise<StudioClientResult<StudioReplyEnvelope>>;
  getServiceStatus(): Promise<StudioClientResult<ForgeServiceStatus>>;
  listWorkspaces(): Promise<StudioClientResult<StudioReplyEnvelope>>;
  listEvents(params?: EventsListParams): Promise<StudioClientResult<StudioReplyEnvelope>>;
  listChangesets(params?: ChangesetsListParams): Promise<StudioClientResult<StudioReplyEnvelope>>;
  listJobs(params?: JobsListParams): Promise<StudioClientResult<StudioReplyEnvelope>>;
  onEvent(listener: (event: StudioActivityEvent) => void): () => void;
}

export const STUDIO_METHODS: ReadonlySet<StudioMethod> = new Set([
  "service.initialize",
  "workspace.register",
  "workspace.list",
  "workspace.get",
  "events.list",
  "changeset.create",
  "changeset.get",
  "changeset.list",
  "changeset.approve",
  "changeset.reject",
  "changeset.apply",
  "job.create",
  "job.get",
  "job.list",
  "job.transition",
  "job.cancel",
]);

export const STUDIO_READ_METHODS: ReadonlySet<StudioReadMethod> = new Set([
  "workspace.list",
  "events.list",
  "changeset.list",
  "job.list",
]);

export const IPC_CHANNELS = Object.freeze({
  initialize: "studio:initialize",
  status: "studio:get-service-status",
  listWorkspaces: "studio:list-workspaces",
  listEvents: "studio:list-events",
  listChangesets: "studio:list-changesets",
  listJobs: "studio:list-jobs",
  event: "studio:event",
});

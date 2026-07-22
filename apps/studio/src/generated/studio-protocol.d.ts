/* AUTO-GENERATED from schemas/studio-protocol.schema.json. Do not edit by hand. */

export type ForgeStudioNDJSONApplicationProtocolV1 = {
  protocol: "rpg-world-forge.studio_protocol";
  protocol_version: 1;
  kind: "request" | "response" | "error" | "event";
  request_id: string | null;
  [k: string]: unknown;
} & (Request | Response | Error | Event);
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "method".
 */
export type Method =
  | "service.initialize"
  | "workspace.register"
  | "workspace.list"
  | "workspace.get"
  | "events.list"
  | "changeset.create"
  | "changeset.get"
  | "changeset.list"
  | "changeset.approve"
  | "changeset.reject"
  | "changeset.apply"
  | "job.create"
  | "job.get"
  | "job.list"
  | "job.transition"
  | "job.cancel";
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "request".
 */
export type Request = Base & {
  protocol: "rpg-world-forge.studio_protocol";
  protocol_version: 1;
  kind: "request";
  request_id: string;
  method: Method;
  params: {
    [k: string]: unknown;
  };
};
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "response".
 */
export type Response = Base & {
  protocol: "rpg-world-forge.studio_protocol";
  protocol_version: 1;
  kind: "response";
  request_id: string;
  result: {
    [k: string]: unknown;
  };
};
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "errorCode".
 */
export type ErrorCode =
  "invalid_request" | "not_found" | "conflict" | "invalid_state" | "internal_error";
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "error".
 */
export type Error = Base & {
  protocol: "rpg-world-forge.studio_protocol";
  protocol_version: 1;
  kind: "error";
  request_id: string | null;
  error: {
    code: ErrorCode;
    message: string;
    details: {
      [k: string]: unknown;
    };
  };
};
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "event".
 */
export type Event = Base & {
  protocol: "rpg-world-forge.studio_protocol";
  protocol_version: 1;
  kind: "event";
  request_id: null;
  event: {
    [k: string]: unknown;
  };
};

/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "base".
 */
export interface Base {
  protocol: "rpg-world-forge.studio_protocol";
  protocol_version: 1;
  kind: "request" | "response" | "error" | "event";
  request_id: string | null;
  [k: string]: unknown;
}

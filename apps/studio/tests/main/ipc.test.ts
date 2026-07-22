import { describe, expect, it } from "vitest";

import {
  validateChangesetsListParams,
  validateEventsListParams,
  validateJobsListParams,
} from "../../src/main/ipc";

describe("Studio named read-only IPC filters", () => {
  it("accepts closed, bounded filters for each list operation", () => {
    expect(
      validateEventsListParams({
        workspace_id: "workspace_01",
        after_id: 0,
        limit: 1_000,
      }),
    ).toEqual({ workspace_id: "workspace_01", after_id: 0, limit: 1_000 });
    expect(
      validateChangesetsListParams({
        workspace_id: "workspace_01",
        status: "approved",
        limit: 100,
      }),
    ).toEqual({ workspace_id: "workspace_01", status: "approved", limit: 100 });
    expect(
      validateJobsListParams({
        workspace_id: "workspace_01",
        state: "awaiting_approval",
        limit: 1,
      }),
    ).toEqual({ workspace_id: "workspace_01", state: "awaiting_approval", limit: 1 });
  });

  it.each([
    [validateEventsListParams, { method: "workspace.register" }],
    [validateEventsListParams, { workspace_id: "../bad" }],
    [validateEventsListParams, { after_id: -1 }],
    [validateEventsListParams, { after_id: true }],
    [validateEventsListParams, { limit: 0 }],
    [validateChangesetsListParams, { status: "created" }],
    [validateChangesetsListParams, { limit: 1_001 }],
    [validateJobsListParams, { state: "approved" }],
    [validateJobsListParams, []],
    [validateJobsListParams, { state: "queued", command: "shell.exec" }],
  ])("rejects malformed, unknown, or mutation-shaped filters %#", (validate, value) => {
    expect(() => validate(value)).toThrow();
  });
});

import { describe, expect, it } from "vitest";

import {
  validateChangesetsListParams,
  validateEventsListParams,
  validateJobsListParams,
  validateInterruptTurnArgument,
  validateLoginArgument,
  validateStartTurnArgument,
  validateUserInputArgument,
  validateWorkspaceArgument,
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

describe("Codex named IPC contracts", () => {
  it("accepts only closed bounded values", () => {
    expect(validateWorkspaceArgument({ workspaceId: "workspace_01" })).toEqual({ workspaceId: "workspace_01" });
    expect(validateLoginArgument({ mode: "device-code" })).toEqual({ mode: "device-code" });
    expect(validateStartTurnArgument({ threadId: "thread-1", text: "hello" })).toEqual({ threadId: "thread-1", text: "hello" });
    expect(validateInterruptTurnArgument({ threadId: "thread-1", turnId: "turn-1" })).toEqual({ threadId: "thread-1", turnId: "turn-1" });
    expect(validateUserInputArgument({
      token: "00000000-0000-4000-8000-000000000000",
      answers: { choice: ["North"] },
    })).toEqual({
      token: "00000000-0000-4000-8000-000000000000",
      answers: { choice: ["North"] },
    });
  });

  it.each([
    [validateWorkspaceArgument, { workspaceId: "../bad" }],
    [validateLoginArgument, { mode: "api-key" }],
    [validateStartTurnArgument, { threadId: "thread-1", text: "ok", command: "shell" }],
    [validateStartTurnArgument, { threadId: "../bad", text: "ok" }],
    [validateInterruptTurnArgument, { threadId: "thread-1" }],
    [validateUserInputArgument, { token: "bad", answers: { choice: ["x"] } }],
  ])("rejects malformed or capability-shaped input %#", (validate, value) => {
    expect(() => validate(value)).toThrow();
  });
});

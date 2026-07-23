import { describe, expect, it } from "vitest";

import {
  authoringReducer,
  createInitialAuthoringState,
  RequestLimiter,
  selectedDraft,
  sourceVersionKey,
} from "../../src/renderer/authoring-state";

const SHA = "a".repeat(64);

describe("authoring reducer", () => {
  it("ignores stale workspace generations", () => {
    const first = authoringReducer(createInitialAuthoringState(), {
      type: "workspace-selected",
      workspaceId: "workspace_01",
      generation: 1,
    });
    const second = authoringReducer(first, {
      type: "workspace-selected",
      workspaceId: "workspace_02",
      generation: 2,
    });
    const stale = authoringReducer(second, {
      type: "workspace-loaded",
      workspaceId: "workspace_01",
      generation: 1,
      overview: overview("workspace_01"),
      documents: [],
      validation: validation(),
      analysis: null,
    });
    expect(stale).toBe(second);
    expect(stale.workspaceId).toBe("workspace_02");
  });

  it("caches verified reads by workspace, path, and listed SHA", () => {
    let state = authoringReducer(createInitialAuthoringState(), {
      type: "workspace-selected",
      workspaceId: "workspace_01",
      generation: 1,
    });
    state = authoringReducer(state, {
      type: "workspace-loaded",
      workspaceId: "workspace_01",
      generation: 1,
      overview: overview("workspace_01"),
      documents: [{ path: "source/world.json", kind: "world", size: 12, sha256: SHA }],
      validation: validation(),
      analysis: null,
    });
    state = authoringReducer(state, { type: "source-selected", path: "source/world.json" });
    state = authoringReducer(state, {
      type: "source-loaded",
      workspaceId: "workspace_01",
      generation: 1,
      path: "source/world.json",
      expectedSha256: SHA,
      document: {
        path: "source/world.json",
        kind: "world",
        size: 12,
        sha256: SHA,
        encoding: "utf-8",
        content: '{"id":"world_01"}',
        json: { id: "world_01" },
      },
    });
    expect(state.cache[sourceVersionKey("workspace_01", "source/world.json", SHA)]).toMatchObject({
      content: '{"id":"world_01"}',
    });
    expect(selectedDraft(state)).toMatchObject({ dirty: false, jsonSyntaxError: null });
  });

  it("rejects list/read SHA disagreement without caching", () => {
    let state = authoringReducer(createInitialAuthoringState(), {
      type: "workspace-selected",
      workspaceId: "workspace_01",
      generation: 1,
    });
    state = authoringReducer(state, {
      type: "workspace-loaded",
      workspaceId: "workspace_01",
      generation: 1,
      overview: overview("workspace_01"),
      documents: [{ path: "source/world.json", kind: "world", size: 12, sha256: SHA }],
      validation: validation(),
      analysis: null,
    });
    state = authoringReducer(state, { type: "source-selected", path: "source/world.json" });
    state = authoringReducer(state, {
      type: "source-loaded",
      workspaceId: "workspace_01",
      generation: 1,
      path: "source/world.json",
      expectedSha256: SHA,
      document: {
        path: "source/world.json",
        kind: "world",
        size: 12,
        sha256: "b".repeat(64),
        encoding: "utf-8",
        content: "{}",
        json: {},
      },
    });
    expect(state.cache).toEqual({});
    expect(state.sourceError).toMatch(/Source changed after listing/u);
  });

  it("ignores a stale source response without clearing the current request", () => {
    let state = loadedWorkspaceState();
    state = authoringReducer(state, { type: "source-selected", path: "source/world.json" });
    state = authoringReducer(state, {
      type: "source-loading",
      workspaceId: "workspace_01",
      generation: 1,
      path: "source/world.json",
    });
    state = authoringReducer(state, { type: "source-selected", path: "source/map.json" });
    state = authoringReducer(state, {
      type: "source-loading",
      workspaceId: "workspace_01",
      generation: 1,
      path: "source/map.json",
    });
    const stale = authoringReducer(state, {
      type: "source-loaded",
      workspaceId: "workspace_01",
      generation: 1,
      path: "source/world.json",
      expectedSha256: SHA,
      document: sourceDocument("source/world.json", SHA),
    });
    expect(stale).toBe(state);
    expect(stale.selectedPath).toBe("source/map.json");
    expect(stale.sourcePending).toBe(true);
  });

  it("rejects a same-SHA response for a different source path", () => {
    let state = loadedWorkspaceState();
    state = authoringReducer(state, { type: "source-selected", path: "source/world.json" });
    state = authoringReducer(state, {
      type: "source-loading",
      workspaceId: "workspace_01",
      generation: 1,
      path: "source/world.json",
    });
    state = authoringReducer(state, {
      type: "source-loaded",
      workspaceId: "workspace_01",
      generation: 1,
      path: "source/world.json",
      expectedSha256: SHA,
      document: sourceDocument("source/map.json", SHA),
    });
    expect(state.cache).toEqual({});
    expect(state.sourcePending).toBe(false);
    expect(state.sourceError).toMatch(/requested document/u);
  });

  it("tracks invalid JSON as an unstaged dirty draft and can discard it", () => {
    let state = loadedSourceState();
    state = authoringReducer(state, { type: "draft-changed", text: '{"broken":' });
    expect(selectedDraft(state)).toMatchObject({ dirty: true });
    expect(selectedDraft(state)?.jsonSyntaxError).toBeTruthy();
    state = authoringReducer(state, { type: "draft-discarded" });
    expect(selectedDraft(state)).toMatchObject({ dirty: false, text: '{"id":"world_01"}' });
  });

  it("limits named requests to four concurrent operations", async () => {
    const limiter = new RequestLimiter(4);
    let active = 0;
    let peak = 0;
    await Promise.all(
      Array.from({ length: 12 }, (_, index) =>
        limiter.run(async () => {
          active += 1;
          peak = Math.max(peak, active);
          await new Promise((resolve) => setTimeout(resolve, index % 2));
          active -= 1;
        }),
      ),
    );
    expect(peak).toBe(4);
  });
});

function loadedSourceState() {
  let state = loadedWorkspaceState();
  state = authoringReducer(state, { type: "source-selected", path: "source/world.json" });
  return authoringReducer(state, {
    type: "source-loaded",
    workspaceId: "workspace_01",
    generation: 1,
    path: "source/world.json",
    expectedSha256: SHA,
    document: sourceDocument("source/world.json", SHA),
  });
}

function loadedWorkspaceState() {
  let state = authoringReducer(createInitialAuthoringState(), {
    type: "workspace-selected",
    workspaceId: "workspace_01",
    generation: 1,
  });
  state = authoringReducer(state, {
    type: "workspace-loaded",
    workspaceId: "workspace_01",
    generation: 1,
    overview: overview("workspace_01"),
    documents: [
      { path: "source/map.json", kind: "map", size: 12, sha256: SHA },
      { path: "source/world.json", kind: "world", size: 12, sha256: SHA },
    ],
    validation: validation(),
    analysis: null,
  });
  return state;
}

function sourceDocument(path: string, sha256: string) {
  return {
    path,
    kind: path.includes("map") ? "map" : "world",
    size: 12,
    sha256,
    encoding: "utf-8" as const,
    content: '{"id":"world_01"}',
    json: { id: "world_01" },
  };
}

function overview(workspaceId: string) {
  return {
    workspace_id: workspaceId,
    project: { world_id: "world_01", title: "World", world_version: "1.0.0" },
    status: { current_phase: "foundation", revision: 1, canon_locked: false, worldpack_hash: null },
    repositories: { game_registered: false, bundle_registered: false },
    capabilities: {
      providers: false as const,
      source_inspection: true as const,
      world_validation: true as const,
      narrative_analysis: true as const,
      staged_changesets: true as const,
    },
  };
}

function validation() {
  return {
    valid: true,
    profile: "release" as const,
    world_id: "world_01",
    object_count: 1,
    diagnostics: [],
    diagnostics_truncated: false,
  };
}

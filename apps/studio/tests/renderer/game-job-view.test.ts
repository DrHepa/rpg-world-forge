import { describe, expect, it } from "vitest";

import {
  isPortableGamePath,
  projectCanceledGameJob,
  projectCreatedGameJob,
  projectGameJobs,
} from "../../src/renderer/game-job-view";

const HASH = "a".repeat(64);

describe("Game job projection", () => {
  it("keeps only exact v2 current-workspace Game operations", () => {
    const valid = gameJob();
    const jobs = projectGameJobs(
      [
        { ...valid, format_version: 1 },
        { ...valid, job_id: "wrong-workspace", workspace_id: "workspace_02" },
        { ...valid, job_id: "receipt", operation: "asset.receipt.validate" },
        { ...valid, job_id: "bad-input", input: { worldpack: "/home/private/world.json", ticks: 2 } },
        valid,
      ],
      [],
      "workspace_01",
    );

    expect(jobs).toHaveLength(1);
    expect(jobs[0]).toMatchObject({
      jobId: "job_01",
      operation: "runtime.headless",
      operationLabel: "Headless simulation",
      state: "queued",
      canCancel: true,
      inputFacts: [
        { label: "Worldpack", value: "build/worldpack.json", kind: "path" },
        { label: "Ticks", value: "2", kind: "number" },
      ],
    });
  });

  it("projects operation-specific successful facts without raw records", () => {
    const jobs = projectGameJobs(
      [
        gameJob({
          job_id: "assetpack-job",
          operation: "assetpack.verify",
          state: "succeeded",
          input: {
            assetpack: "build/assets/assetpack.json",
            worldpack: "build/worldpack.json",
          },
          result: {
            operation: "assetpack.verify",
            valid: true,
            world_id: "world_01",
            world_content_hash: HASH,
            target_id: "pyray",
            target_hash: "b".repeat(64),
            content_hash: "c".repeat(64),
            asset_count: 7,
            file_count: 8,
            binding_count: 3,
          },
        }),
        gameJob({
          job_id: "replay-job",
          operation: "runtime.replay",
          state: "succeeded",
          input: {
            worldpack: "build/worldpack.json",
            replay: "replays/accepted.json",
          },
          result: {
            operation: "runtime.replay",
            world_id: "world_01",
            world_content_hash: HASH,
            action_count: 5,
            state_tick: 11,
            absolute_minute: 720,
            state_digest: "d".repeat(64),
          },
        }),
      ],
      [],
      "workspace_01",
    );

    expect(jobs[0]?.resultFacts).toContainEqual({
      label: "Assets",
      value: "7",
      kind: "number",
    });
    expect(jobs[0]?.resultFacts).toContainEqual({
      label: "Bindings",
      value: "3",
      kind: "number",
    });
    expect(jobs[1]?.resultFacts).toContainEqual({
      label: "Replay actions",
      value: "5",
      kind: "number",
    });
    expect(JSON.stringify(jobs)).not.toContain("absolute_root");
  });

  it("uses controlled error copy instead of raw messages or details", () => {
    const jobs = projectGameJobs(
      [
        gameJob({
          state: "failed",
          error: {
            code: "invalid_workspace",
            message: "SECRET /home/private/world.json",
          },
        }),
      ],
      [],
      "workspace_01",
    );

    expect(jobs[0]?.error).toEqual({
      code: "invalid_workspace",
      message: "A selected workspace input was unavailable or changed.",
    });
    expect(JSON.stringify(jobs)).not.toMatch(/SECRET|\/home\/private/u);
  });

  it("shows observed running progress only with reliable current-workspace event identity", () => {
    const job = gameJob({ state: "running" });
    const validEvent = progressEvent({ event_id: 9, progress: 50 });

    expect(projectGameJobs([job], [validEvent], "workspace_01")[0]?.progress).toBe(50);
    expect(
      projectGameJobs(
        [job],
        [{ ...validEvent, workspace_id: "workspace_02" }],
        "workspace_01",
      )[0]?.progress,
    ).toBeNull();
    expect(
      projectGameJobs(
        [job],
        [{ ...validEvent, event_id: undefined }],
        "workspace_01",
      )[0]?.progress,
    ).toBeNull();
    expect(
      projectGameJobs(
        [job],
        [
          validEvent,
          progressEvent({ event_id: 9, progress: 20 }),
        ],
        "workspace_01",
      )[0]?.progress,
    ).toBeNull();
    expect(
      projectGameJobs(
        [{ ...job, state: "succeeded", result: headlessResult() }],
        [validEvent],
        "workspace_01",
      )[0]?.progress,
    ).toBeNull();
  });

  it("accepts a created job only for the exact workspace, operation, and input", () => {
    const job = gameJob();
    const request = {
      operation: "runtime.headless" as const,
      input: { worldpack: "build/worldpack.json", ticks: 2 },
    };

    expect(projectCreatedGameJob(job, "workspace_01", request)?.jobId).toBe("job_01");
    expect(
      projectCreatedGameJob(job, "workspace_01", {
        ...request,
        input: { ...request.input, ticks: 3 },
      }),
    ).toBeNull();
    expect(projectCreatedGameJob(job, "workspace_02", request)).toBeNull();
    expect(
      projectCreatedGameJob({ ...job, state: "running" }, "workspace_01", request),
    ).toBeNull();
  });

  it("rejects extra top-level keys and invalid state/result/error pairs", () => {
    const records = [
      { ...gameJob(), extra: "not closed" },
      gameJob({
        job_id: "queued-error",
        error: { code: "timeout", message: "late" },
      }),
      gameJob({
        job_id: "failed-without-error",
        state: "failed",
      }),
      gameJob({
        job_id: "succeeded-with-error",
        state: "succeeded",
        result: headlessResult(),
        error: { code: "timeout", message: "late" },
      }),
      gameJob({
        job_id: "canceled-with-result",
        state: "canceled",
        result: headlessResult(),
      }),
      gameJob({
        job_id: "valid-canceled",
        state: "canceled",
      }),
    ];

    expect(projectGameJobs(records, [], "workspace_01").map((job) => job.jobId)).toEqual([
      "valid-canceled",
    ]);
  });

  it("correlates cancel records to the exact workspace, job, and operation", () => {
    const canceled = gameJob({ state: "canceled" });
    expect(
      projectCanceledGameJob(
        canceled,
        "workspace_01",
        "job_01",
        "runtime.headless",
      )?.state,
    ).toBe("canceled");
    expect(
      projectCanceledGameJob(canceled, "workspace_02", "job_01", "runtime.headless"),
    ).toBeNull();
    expect(
      projectCanceledGameJob(canceled, "workspace_01", "other-job", "runtime.headless"),
    ).toBeNull();
    expect(
      projectCanceledGameJob(canceled, "workspace_01", "job_01", "runtime.replay"),
    ).toBeNull();
  });
});

describe("Game path inputs", () => {
  it("accepts portable workspace-relative paths and rejects roots or traversal", () => {
    expect(isPortableGamePath("build/worldpack.json")).toBe(true);
    expect(isPortableGamePath("replays/accepted.json")).toBe(true);
    expect(isPortableGamePath("")).toBe(false);
    expect(isPortableGamePath("/home/private/worldpack.json")).toBe(false);
    expect(isPortableGamePath("../worldpack.json")).toBe(false);
    expect(isPortableGamePath("build\\worldpack.json")).toBe(false);
    expect(isPortableGamePath("build/CON.json")).toBe(false);
    expect(isPortableGamePath("build/worldpack.json ")).toBe(false);
  });
});

function gameJob(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    format: "rpg-world-forge.studio_job",
    format_version: 2,
    job_id: "job_01",
    workspace_id: "workspace_01",
    operation: "runtime.headless",
    state: "queued",
    input: { worldpack: "build/worldpack.json", ticks: 2 },
    result: null,
    error: null,
    created_at: "2026-07-23T10:00:00Z",
    updated_at: "2026-07-23T10:00:00Z",
    ...overrides,
  };
}

function headlessResult(): Record<string, unknown> {
  return {
    operation: "runtime.headless",
    world_id: "world_01",
    world_content_hash: HASH,
    ticks: 2,
    state_tick: 2,
    absolute_minute: 2,
    state_digest: "b".repeat(64),
  };
}

function progressEvent({
  event_id,
  progress,
}: {
  event_id: number;
  progress: number;
}): Record<string, unknown> {
  return {
    event_id,
    workspace_id: "workspace_01",
    topic: "job.progress",
    entity_type: "job",
    entity_id: "job_01",
    payload: { progress, stage: "executing" },
    created_at: "2026-07-23T10:00:01Z",
  };
}

// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { GameCockpit } from "../../src/renderer/GameCockpit";
import type { GameJobRequest, GameJobView } from "../../src/renderer/game-job-view";

const HASH = "a".repeat(64);

afterEach(cleanup);

describe("GameCockpit", () => {
  it("submits the three exact named-operation inputs with zero ticks", () => {
    const onSubmit = vi.fn<(request: GameJobRequest) => void>();
    renderCockpit({ onSubmit });

    fireEvent.change(screen.getByLabelText("Assetpack path"), {
      target: { value: "build/assets/assetpack.json" },
    });
    fireEvent.change(
      screen.getByLabelText("Worldpack path for assetpack verification"),
      { target: { value: "build/worldpack.json" } },
    );
    fireEvent.click(screen.getByRole("button", { name: "Verify assetpack" }));

    fireEvent.change(screen.getByLabelText("Worldpack path for headless simulation"), {
      target: { value: "build/worldpack.json" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Run headless simulation" }));

    fireEvent.change(screen.getByLabelText("Worldpack path for replay verification"), {
      target: { value: "build/worldpack.json" },
    });
    fireEvent.change(screen.getByLabelText("Existing replay path"), {
      target: { value: "replays/accepted.json" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Verify existing replay" }));

    expect(onSubmit.mock.calls.map(([request]) => request)).toEqual([
      {
        operation: "assetpack.verify",
        input: {
          assetpack: "build/assets/assetpack.json",
          worldpack: "build/worldpack.json",
        },
      },
      {
        operation: "runtime.headless",
        input: { worldpack: "build/worldpack.json", ticks: 0 },
      },
      {
        operation: "runtime.replay",
        input: {
          worldpack: "build/worldpack.json",
          replay: "replays/accepted.json",
        },
      },
    ]);
  });

  it("enforces integer tick bounds and portable path inputs", () => {
    const onSubmit = vi.fn<(request: GameJobRequest) => void>();
    renderCockpit({ onSubmit });
    const worldpack = screen.getByLabelText("Worldpack path for headless simulation");
    const ticks = screen.getByLabelText("Headless ticks");
    fireEvent.change(worldpack, { target: { value: "build/worldpack.json" } });

    for (const invalid of ["-1", "1.5", "1000001", "01"]) {
      fireEvent.change(ticks, { target: { value: invalid } });
      expect(screen.getByRole("button", { name: "Run headless simulation" })).toBeDisabled();
    }

    fireEvent.change(ticks, { target: { value: "1000000" } });
    fireEvent.change(worldpack, { target: { value: "/home/private/worldpack.json" } });
    fireEvent.click(screen.getByRole("button", { name: "Run headless simulation" }));
    expect(screen.getByRole("alert")).toHaveTextContent(/portable workspace-relative/u);
    expect(screen.getByRole("alert")).not.toHaveTextContent("/home/private/worldpack.json");
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("renders structured results, controlled errors, progress, and exact cancel names", () => {
    const onCancel = vi.fn<(job: GameJobView) => void>();
    const records = [
      gameJob({
        state: "running",
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
          action_count: 4,
          state_tick: 9,
          absolute_minute: 700,
          state_digest: "b".repeat(64),
        },
      }),
      gameJob({
        job_id: "failed-job",
        state: "failed",
        error: {
          code: "invalid_workspace",
          message: "SECRET C:\\private\\worldpack.json",
        },
      }),
    ];
    renderCockpit({
      records,
      events: [progressEvent(20)],
      onCancel,
    });

    expect(screen.getByText("Observed progress 20%")).toBeInTheDocument();
    expect(screen.getByText("Replay actions").nextSibling).toHaveTextContent("4");
    expect(screen.getByText("invalid_workspace")).toBeInTheDocument();
    expect(screen.queryByText(/SECRET|C:\\private/u)).not.toBeInTheDocument();
    const cancel = screen.getByRole("button", {
      name: "Cancel Headless simulation job job_01",
    });
    fireEvent.click(cancel);
    expect(onCancel).toHaveBeenCalledWith(
      expect.objectContaining({ jobId: "job_01", state: "running" }),
    );
    expect(screen.queryByRole("button", { name: /replay-job/u })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /failed-job/u })).not.toBeInTheDocument();
  });

  it("uses indeterminate progress without an associated event and honest replay wording", () => {
    renderCockpit({ records: [gameJob({ state: "running" })] });

    expect(
      screen.getByText("Running; no associated progress percentage has been observed."),
    ).toHaveAttribute("role", "status");
    expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
    expect(screen.getByText(/does not record a replay/u)).toBeInTheDocument();
    expect(screen.getByText(/does not use generated-game replay slots/u)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /record/u })).not.toBeInTheDocument();
    expect(screen.getByText(/bounded and is not a chronological job history/u)).toBeInTheDocument();
  });

  it("announces pending, inserted job-count, and error status without raw detail", () => {
    renderCockpit({
      pending: {
        "assetpack.verify": false,
        "runtime.headless": true,
        "runtime.replay": false,
      },
      errors: {
        "assetpack.verify": null,
        "runtime.headless": "The fixed offline Game job could not be queued.",
        "runtime.replay": null,
      },
      records: [gameJob()],
    });

    expect(screen.getByText("Queuing headless simulation.")).toHaveAttribute(
      "role",
      "status",
    );
    expect(screen.getByText("1 valid Game jobs")).toHaveAttribute("role", "status");
    expect(
      screen.getByText("The fixed offline Game job could not be queued."),
    ).toHaveAttribute("role", "alert");
  });

  it("resets workspace-relative form values when the workspace generation changes", () => {
    const view = renderCockpit();
    const input = screen.getByLabelText("Assetpack path");
    fireEvent.change(input, { target: { value: "build/assets/assetpack.json" } });
    expect(input).toHaveValue("build/assets/assetpack.json");

    view.rerender(
      cockpit({
        workspaceId: "workspace_02",
      }, "workspace_02-generation-2"),
    );
    expect(screen.getByLabelText("Assetpack path")).toHaveValue("");
  });
});

function renderCockpit(
  overrides: Partial<React.ComponentProps<typeof GameCockpit>> = {},
) {
  return render(cockpit(overrides));
}

function cockpit(
  overrides: Partial<React.ComponentProps<typeof GameCockpit>> = {},
  key = "workspace_01-generation-1",
) {
  return (
    <GameCockpit
      key={key}
      workspaceId="workspace_01"
      repositories={{ gameRegistered: true, bundleRegistered: false }}
      records={[]}
      events={[]}
      pending={{
        "assetpack.verify": false,
        "runtime.headless": false,
        "runtime.replay": false,
      }}
      errors={{
        "assetpack.verify": null,
        "runtime.headless": null,
        "runtime.replay": null,
      }}
      cancelingJobIds={new Set()}
      onSubmit={() => undefined}
      onCancel={() => undefined}
      {...overrides}
    />
  );
}

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

function progressEvent(progress: number): Record<string, unknown> {
  return {
    event_id: 5,
    workspace_id: "workspace_01",
    topic: "job.progress",
    entity_type: "job",
    entity_id: "job_01",
    payload: { progress, stage: "validated" },
    created_at: "2026-07-23T10:00:01Z",
  };
}

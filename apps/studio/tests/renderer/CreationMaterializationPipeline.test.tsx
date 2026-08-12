// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";

import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  CreationMaterializationPipeline,
  type CreationMaterializationPipelineProps,
} from "../../src/renderer/CreationMaterializationPipeline";
import * as pipelineState from "../../src/renderer/creation-materialization-pipeline-state";
import type {
  CreationExecutionCensus,
  CreationJobView,
} from "../../src/renderer/creation-execution-state";
import {
  projectCreationJob,
} from "../../src/renderer/creation-execution-state";
import type {
  ForgeStudioApi,
  StudioCreationJob,
  StudioCreationOutputGrant,
  StudioCreationWorkspace,
} from "../../src/shared/studio-api";

const SOURCE = "a".repeat(64);
const SNAPSHOT = "b".repeat(64);

describe("CreationMaterializationPipeline", () => {
  afterEach(cleanup);

  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(
      pipelineState,
      "loadCreationMaterializationPipelineCandidates",
    ).mockResolvedValue(loadedCandidates());
  });

  it("renders an accessible four-step build pipeline without overclaiming release or native support", async () => {
    renderPipeline();

    expect(
      await screen.findByRole("group", { name: "1. Build materialization bundle" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("group", { name: "2. Materialize standalone game" })).toBeInTheDocument();
    expect(screen.getByRole("group", { name: "3. Build game package" })).toBeInTheDocument();
    expect(screen.getByRole("group", { name: "4. Extract game package" })).toBeInTheDocument();
    expect(screen.getByText(/candidate evidence does not change active readiness/iu)).toBeInTheDocument();
    expect(screen.getByText(/release-blocked until reviewed execution, native, and platform evidence is active/iu)).toBeInTheDocument();
    expect(screen.getByText("standalone_hash").closest("p")).toHaveTextContent(
      "Preserved standalone identity standalone_hash",
    );
    expect(screen.queryByText(/Windows verified|native verified/iu)).not.toBeInTheDocument();
  });

  it("uses only the fixed pathless selector, treats cancel as neutral, and revokes ready grants with CAS", async () => {
    const target = readyGrant(
      "grant_materialization_target",
      3,
      "game_materialization_bundle_directory",
      "Materialization output",
    );
    const selectCreationMaterializationBundleOutput = vi
      .fn()
      .mockResolvedValueOnce(clientError("cancelled", "dialog cancelled"))
      .mockResolvedValueOnce(v4("creation_output_grant.create", { grant: target }));
    const revokeCreationAssetpackOutput = vi.fn().mockResolvedValue(
      v4("creation_output_grant.revoke", {
        grant: {
          ...target,
          state: "revoked",
          generation: 1,
          updated_at: "2026-08-05T00:00:01Z",
        },
      }),
    );
    renderPipeline({
      api: api({
        selectCreationMaterializationBundleOutput,
        revokeCreationAssetpackOutput,
      }),
    });

    const step = await screen.findByRole("group", { name: "1. Build materialization bundle" });
    fireEvent.click(
      within(step).getByRole("button", { name: "Select materialization bundle destination" }),
    );
    expect(await screen.findByText(/selection was cancelled; no grant was created/iu)).toBeInTheDocument();
    fireEvent.click(
      within(step).getByRole("button", { name: "Select materialization bundle destination" }),
    );
    expect(await within(step).findByText("Materialization output")).toBeInTheDocument();
    const selectedTarget = within(step)
      .getByRole("radio", { name: /Materialization output — Ready/iu })
      .closest("label")!;
    expect(within(selectedTarget).getByText("game_materialization_bundle_directory")).toBeInTheDocument();
    expect(within(selectedTarget).getByText("Ready")).toBeInTheDocument();
    expect(within(selectedTarget).getByText("generation 0")).toBeInTheDocument();
    expect(within(step).queryByText("grant_materialization_target")).not.toBeInTheDocument();
    fireEvent.click(
      within(step).getByRole("button", {
        name: "Revoke selected materialization bundle destination",
      }),
    );
    await waitFor(() =>
      expect(revokeCreationAssetpackOutput).toHaveBeenCalledWith({
        grantId: "grant_materialization_target",
        expectedGeneration: 0,
      }),
    );
  });

  it("submits exact current candidate, source grant, target grant, and authority for all four steps", async () => {
    const targets = [
      readyGrant("grant_mat_target", 3, "game_materialization_bundle_directory", "Materialization target"),
      readyGrant("grant_game_target", 4, "standalone_game_directory", "Standalone target"),
      readyGrant("grant_package_target", 5, "game_package_file", "Package target"),
      readyGrant("grant_extract_target", 4, "standalone_game_directory", "Extraction target"),
    ];
    const methods = {
      buildCreationMaterializationBundle: vi.fn().mockResolvedValue(
        createdJob("game.materialization.bundle.build", 6, "job_materialization_submit"),
      ),
      materializeCreationGame: vi.fn().mockResolvedValue(
        createdJob("game.materialize", 7, "job_materialize_submit"),
      ),
      packageCreationGame: vi.fn().mockResolvedValue(
        createdJob("game.package", 8, "job_package_submit"),
      ),
      extractCreationGamePackage: vi.fn().mockResolvedValue(
        createdJob("game.package.extract", 9, "job_extract_submit"),
      ),
    };
    renderPipeline({ grants: [...publishedGrants(), ...targets], api: api(methods) });

    const steps = [
      await screen.findByRole("group", { name: "1. Build materialization bundle" }),
      screen.getByRole("group", { name: "2. Materialize standalone game" }),
      screen.getByRole("group", { name: "3. Build game package" }),
      screen.getByRole("group", { name: "4. Extract game package" }),
    ];
    const candidateLabels = [
      /Runtime bundle candidate/iu,
      /Materialization bundle candidate/iu,
      /Standalone game candidate/iu,
      /Game package candidate/iu,
    ];
    const targetLabels = [
      /Materialization target — Ready/iu,
      /Standalone target — Ready/iu,
      /Package target — Ready/iu,
      /Extraction target — Ready/iu,
    ];
    const submitLabels = [
      "Build selected materialization bundle",
      "Materialize selected standalone game",
      "Build selected game package",
      "Extract selected game package",
    ];
    for (let index = 0; index < steps.length; index += 1) {
      fireEvent.click(within(steps[index]).getByRole("radio", { name: candidateLabels[index] }));
      fireEvent.click(within(steps[index]).getByRole("radio", { name: targetLabels[index] }));
      fireEvent.click(within(steps[index]).getByRole("button", { name: submitLabels[index] }));
      await waitFor(() => expect(Object.values(methods)[index]).toHaveBeenCalledOnce());
    }

    expect(methods.buildCreationMaterializationBundle).toHaveBeenCalledWith({
      ...authorityParams(),
      runtimeBundleArtifactId: "artifact_runtime_bundle",
      sourceGrantId: "grant_runtime",
      expectedSourceGrantGeneration: 2,
      targetGrantId: "grant_mat_target",
      expectedTargetGrantGeneration: 0,
    });
    expect(methods.materializeCreationGame).toHaveBeenCalledWith({
      ...authorityParams(),
      materializationBundleArtifactId: "artifact_materialization",
      sourceGrantId: "grant_materialization",
      expectedSourceGrantGeneration: 2,
      targetGrantId: "grant_game_target",
      expectedTargetGrantGeneration: 0,
    });
    expect(methods.packageCreationGame).toHaveBeenCalledWith({
      ...authorityParams(),
      standaloneGameArtifactId: "artifact_standalone",
      sourceGrantId: "grant_standalone",
      expectedSourceGrantGeneration: 2,
      targetGrantId: "grant_package_target",
      expectedTargetGrantGeneration: 0,
    });
    expect(methods.extractCreationGamePackage).toHaveBeenCalledWith({
      ...authorityParams(),
      gamePackageArtifactId: "artifact_package",
      sourceGrantId: "grant_package",
      expectedSourceGrantGeneration: 2,
      targetGrantId: "grant_extract_target",
      expectedTargetGrantGeneration: 0,
    });
  });

  it("fails closed when a created job does not echo the exact submitted operation parameters", async () => {
    const target = readyGrant(
      "grant_mat_target",
      3,
      "game_materialization_bundle_directory",
      "Materialization target",
    );
    const onSubmittedJob = vi.fn().mockResolvedValue(undefined);
    renderPipeline({
      grants: [...publishedGrants(), target],
      api: api({
        buildCreationMaterializationBundle: vi.fn().mockResolvedValue(
          v4("creation_job.create", {
            job: rawJob(
              "game.materialization.bundle.build",
              6,
              "job_mismatched_params",
              {
                ...operationParamsFor("game.materialization.bundle.build"),
                source_grant_generation: 99,
              },
            ),
          }),
        ),
      }),
      onSubmittedJob,
    });

    const step = await screen.findByRole("group", {
      name: "1. Build materialization bundle",
    });
    fireEvent.click(within(step).getByRole("radio", { name: /Runtime bundle candidate/iu }));
    fireEvent.click(
      within(step).getByRole("radio", { name: /Materialization target — Ready/iu }),
    );
    fireEvent.click(
      within(step).getByRole("button", {
        name: "Build selected materialization bundle",
      }),
    );

    expect(await screen.findByRole("alert")).toHaveTextContent(
      /mismatched game\.materialization\.bundle\.build job submission/iu,
    );
    expect(onSubmittedJob).not.toHaveBeenCalled();
  });

  it("reconstructs one queued durable job and its reserved target without choosing an ambiguous first match", async () => {
    const reserved = readyGrant(
      "grant_materialization_target",
      3,
      "game_materialization_bundle_directory",
      "Reserved materialization",
      "reserved",
      1,
    );
    const pending = jobView(
      rawJob("game.materialization.bundle.build", 6, "job_pending", {
        runtime_bundle_artifact_id: "artifact_runtime_bundle",
        source_grant_id: "grant_runtime",
        source_grant_generation: 2,
        target_grant_id: "grant_materialization_target",
        target_grant_generation: 1,
      }),
    );
    vi.mocked(pipelineState.loadCreationMaterializationPipelineCandidates).mockResolvedValue({
      ...loadedCandidates(),
      pendingJobs: [pending],
      boundGrantJobIds: new Map([[reserved.grant_id, pending.job_id]]),
    });
    const onSubmittedJob = vi.fn().mockResolvedValue(undefined);
    renderPipeline({
      grants: [...publishedGrants(), reserved],
      onSubmittedJob,
    });

    await waitFor(() => expect(onSubmittedJob).toHaveBeenCalledWith(pending));
    const step = screen.getByRole("group", { name: "1. Build materialization bundle" });
    expect(within(step).getByRole("radio", { name: /Runtime bundle candidate/iu })).toBeChecked();
    expect(within(step).getByRole("radio", { name: /Reserved materialization — Reserved/iu })).toBeChecked();

    vi.mocked(pipelineState.loadCreationMaterializationPipelineCandidates).mockResolvedValue({
      ...loadedCandidates(),
      pendingJobs: [pending, { ...pending, job_id: "job_other" }],
      blockingReasonCodes: ["ambiguous_pending_materialization_jobs"],
      boundGrantJobIds: new Map([[reserved.grant_id, pending.job_id]]),
    });
  });

  it("rejects restart adoption when pending operation parameters contain an extra field", async () => {
    const reserved = readyGrant(
      "grant_materialization_target",
      3,
      "game_materialization_bundle_directory",
      "Reserved materialization",
      "reserved",
      1,
    );
    const pending = jobView(
      rawJob("game.materialization.bundle.build", 6, "job_pending_extra", {
        runtime_bundle_artifact_id: "artifact_runtime_bundle",
        source_grant_id: "grant_runtime",
        source_grant_generation: 2,
        target_grant_id: "grant_materialization_target",
        target_grant_generation: 1,
        unexpected_parameter: true,
      }),
    );
    vi.mocked(pipelineState.loadCreationMaterializationPipelineCandidates).mockResolvedValue({
      ...loadedCandidates(),
      pendingJobs: [pending],
      boundGrantJobIds: new Map([[reserved.grant_id, pending.job_id]]),
    });
    const onSubmittedJob = vi.fn().mockResolvedValue(undefined);
    renderPipeline({
      grants: [...publishedGrants(), reserved],
      onSubmittedJob,
    });

    const exactParameterError = await screen.findByText(
      /parameters are not closed and exact/iu,
    );
    expect(exactParameterError.closest('[role="alert"]')).not.toBeNull();
    expect(onSubmittedJob).not.toHaveBeenCalled();
  });

  it("refreshes grants on terminal observation and clears authority-bound selections", async () => {
    const onGrantCensusRefresh = vi.fn().mockResolvedValue(undefined);
    const target = readyGrant(
      "grant_materialization_target",
      3,
      "game_materialization_bundle_directory",
      "Materialization target",
    );
    const view = renderPipeline({
      grants: [...publishedGrants(), target],
      onGrantCensusRefresh,
    });
    const step = await screen.findByRole("group", { name: "1. Build materialization bundle" });
    fireEvent.click(within(step).getByRole("radio", { name: /Runtime bundle candidate/iu }));
    fireEvent.click(within(step).getByRole("radio", { name: /Materialization target — Ready/iu }));
    expect(within(step).getByRole("button", { name: "Build selected materialization bundle" })).toBeEnabled();

    view.rerender(
      <PipelineHarness
        api={api()}
        workspaceValue={workspace()}
        censusValue={census()}
        initialGrants={[...publishedGrants(), target]}
        observedJob={terminalJob("game.materialization.bundle.build", "job_terminal")}
        onGrantCensusRefresh={onGrantCensusRefresh}
        onSubmittedJob={vi.fn().mockResolvedValue(undefined)}
      />,
    );
    await waitFor(() => expect(onGrantCensusRefresh).toHaveBeenCalled());
    view.rerender(
      <PipelineHarness
        api={api()}
        workspaceValue={workspace()}
        censusValue={{
          ...census(),
          authority: { ...census().authority, artifactSnapshotHash: "f".repeat(64) },
        }}
        initialGrants={[...publishedGrants(), target]}
        observedJob={null}
        onGrantCensusRefresh={onGrantCensusRefresh}
        onSubmittedJob={vi.fn().mockResolvedValue(undefined)}
      />,
    );
    expect(
      within(screen.getByRole("group", { name: "1. Build materialization bundle" }))
        .getByRole("button", { name: "Build selected materialization bundle" }),
    ).toBeDisabled();
  });

  it("reports materialization as not applicable for non-game projects", () => {
    renderPipeline({ workspaceValue: workspace("asset_library") });
    expect(screen.getByText(/not applicable to this project kind/iu)).toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });
});

function PipelineHarness({
  api,
  workspaceValue,
  censusValue,
  initialGrants,
  observedJob,
  onGrantCensusRefresh,
  onSubmittedJob,
}: {
  api: ForgeStudioApi;
  workspaceValue: StudioCreationWorkspace;
  censusValue: CreationExecutionCensus;
  initialGrants: StudioCreationOutputGrant[];
  observedJob: unknown;
  onGrantCensusRefresh: () => void | Promise<void>;
  onSubmittedJob: CreationMaterializationPipelineProps["onSubmittedJob"];
}) {
  const [grants, setGrants] = useState(initialGrants);
  return (
    <CreationMaterializationPipeline
      api={api}
      workspace={workspaceValue}
      census={censusValue}
      grants={grants}
      executionBusy={false}
      observedJob={observedJob}
      trackingError={null}
      onNavigationStateChange={vi.fn()}
      onGrantChange={(grant) =>
        setGrants((current) => [
          ...current.filter((item) => item.grant_id !== grant.grant_id),
          grant,
        ])
      }
      onGrantCensusRefresh={onGrantCensusRefresh}
      onSubmittedJob={onSubmittedJob}
      onObservedJob={vi.fn()}
    />
  );
}

function renderPipeline({
  api: apiValue = api(),
  workspaceValue = workspace(),
  grants = publishedGrants(),
  observedJob = null,
  onGrantCensusRefresh = vi.fn().mockResolvedValue(undefined),
  onSubmittedJob = vi.fn().mockResolvedValue(undefined),
}: {
  api?: ForgeStudioApi;
  workspaceValue?: StudioCreationWorkspace;
  grants?: StudioCreationOutputGrant[];
  observedJob?: unknown;
  onGrantCensusRefresh?: () => void | Promise<void>;
  onSubmittedJob?: CreationMaterializationPipelineProps["onSubmittedJob"];
} = {}) {
  return render(
    <PipelineHarness
      api={apiValue}
      workspaceValue={workspaceValue}
      censusValue={census()}
      initialGrants={grants}
      observedJob={observedJob}
      onGrantCensusRefresh={onGrantCensusRefresh}
      onSubmittedJob={onSubmittedJob}
    />,
  );
}

function loadedCandidates(): pipelineState.LoadedMaterializationPipelineCandidates {
  const common = {
    gamepackArtifactId: "artifact_gamepack",
    gamepackContentHash: "gamepack_hash",
  };
  return {
    runtimeBundleCandidates: [
      {
        key: "runtime_key",
        artifactId: "artifact_runtime_bundle",
        producerJobId: "job_runtime",
        predecessorArtifactId: null,
        sourceGrantId: "grant_runtime",
        sourceGrantGeneration: 2,
        contentHash: "runtime_hash",
        treeHash: "runtime_tree",
        ...common,
      },
    ],
    materializationBundleCandidates: [
      {
        key: "materialization_key",
        artifactId: "artifact_materialization",
        producerJobId: "job_materialization",
        predecessorArtifactId: "artifact_runtime_bundle",
        sourceGrantId: "grant_materialization",
        sourceGrantGeneration: 2,
        contentHash: "materialization_hash",
        treeHash: "materialization_tree",
        ...common,
      },
    ],
    standaloneCandidates: [
      {
        key: "standalone_key",
        artifactId: "artifact_standalone",
        producerJobId: "job_standalone",
        predecessorArtifactId: "artifact_materialization",
        sourceGrantId: "grant_standalone",
        sourceGrantGeneration: 2,
        contentHash: "standalone_hash",
        treeHash: "standalone_tree",
        preservedStandaloneArtifactId: "artifact_standalone",
        preservedStandaloneSubjectId: "standalone_subject",
        preservedStandaloneContentHash: "standalone_hash",
        preservedStandaloneTreeHash: "standalone_tree",
        ...common,
      },
    ],
    packageCandidates: [
      {
        key: "package_key",
        artifactId: "artifact_package",
        producerJobId: "job_package",
        predecessorArtifactId: "artifact_standalone",
        sourceGrantId: "grant_package",
        sourceGrantGeneration: 2,
        contentHash: "package_hash",
        archiveSha256: "archive_hash",
        sizeBytes: 4096,
        preservedStandaloneArtifactId: "artifact_standalone",
        preservedStandaloneSubjectId: "standalone_subject",
        preservedStandaloneContentHash: "standalone_hash",
        preservedStandaloneTreeHash: "standalone_tree",
        ...common,
      },
    ],
    extractionCandidates: [
      {
        key: "extract_key",
        artifactId: "artifact_extraction",
        producerJobId: "job_extract",
        predecessorArtifactId: "artifact_package",
        publishedStandaloneGrantId: "grant_extracted",
        publishedStandaloneGrantGeneration: 2,
        preservedStandaloneArtifactId: "artifact_standalone",
        preservedStandaloneSubjectId: "standalone_subject",
        preservedStandaloneContentHash: "standalone_hash",
        preservedStandaloneTreeHash: "standalone_tree",
        contentHash: "extraction_hash",
        ...common,
      },
    ],
    blockingReasonCodes: [],
    pendingJobs: [],
    boundGrantJobIds: new Map(),
  };
}

function publishedGrants(): StudioCreationOutputGrant[] {
  return [
    publishedGrant("grant_runtime", 2, "game_runtime_bundle_directory", "Runtime bundle"),
    publishedGrant(
      "grant_materialization",
      3,
      "game_materialization_bundle_directory",
      "Materialization bundle",
    ),
    publishedGrant("grant_standalone", 4, "standalone_game_directory", "Standalone game"),
    publishedGrant("grant_package", 5, "game_package_file", "Game package"),
    publishedGrant("grant_extracted", 4, "standalone_game_directory", "Extracted game"),
  ];
}

function publishedGrant(
  grantId: string,
  version: 2 | 3 | 4 | 5,
  kind: StudioCreationOutputGrant["kind"],
  displayName: string,
): StudioCreationOutputGrant {
  const format = new Map<number, string>([
    [2, "world-forge.game_runtime_bundle"],
    [3, "world-forge.game_materialization_bundle"],
    [4, "world-forge.standalone_game"],
    [5, "world-forge.game_package"],
  ]).get(version)!;
  return {
    format: "world-forge.studio_creation_output_grant",
    format_version: version,
    grant_id: grantId,
    workspace_id: "creation_workspace",
    kind,
    display_name: displayName,
    state: "published",
    generation: 2,
    publication: {
      format,
      format_version: 1,
      id: `${grantId}_identity`,
      content_hash: "c".repeat(64),
      ...(version === 5
        ? { archive_sha256: "d".repeat(64), size_bytes: 4096 }
        : { tree_hash: "d".repeat(64) }),
    } as StudioCreationOutputGrant["publication"],
    created_at: "2026-08-05T00:00:00Z",
    updated_at: "2026-08-05T00:00:00Z",
  };
}

function readyGrant(
  grantId: string,
  version: 3 | 4 | 5,
  kind: StudioCreationOutputGrant["kind"],
  displayName: string,
  state: StudioCreationOutputGrant["state"] = "ready",
  generation = 0,
): StudioCreationOutputGrant {
  return {
    format: "world-forge.studio_creation_output_grant",
    format_version: version,
    grant_id: grantId,
    workspace_id: "creation_workspace",
    kind,
    display_name: displayName,
    state,
    generation,
    publication: null,
    created_at: "2026-08-05T00:00:00Z",
    updated_at: "2026-08-05T00:00:00Z",
  };
}

function api(overrides: Partial<ForgeStudioApi> = {}): ForgeStudioApi {
  return {
    listCreationJobs: vi.fn().mockResolvedValue(
      v4("creation_job.list", { jobs: [], next_sequence: null }),
    ),
    selectCreationMaterializationBundleOutput: vi.fn(),
    selectCreationStandaloneGameOutput: vi.fn(),
    selectCreationGamePackageOutput: vi.fn(),
    selectCreationGamePackageExtractionOutput: vi.fn(),
    revokeCreationAssetpackOutput: vi.fn(),
    buildCreationMaterializationBundle: vi.fn(),
    materializeCreationGame: vi.fn(),
    packageCreationGame: vi.fn(),
    extractCreationGamePackage: vi.fn(),
    ...overrides,
  } as unknown as ForgeStudioApi;
}

function createdJob(operation: string, version: number, jobId: string) {
  return v4("creation_job.create", {
    job: rawJob(operation, version, jobId, operationParamsFor(operation)),
  });
}

function operationParamsFor(operation: string): Record<string, unknown> {
  const targets: Record<string, Record<string, unknown>> = {
    "game.materialization.bundle.build": {
      runtime_bundle_artifact_id: "artifact_runtime_bundle",
      source_grant_id: "grant_runtime",
      source_grant_generation: 2,
      target_grant_id: "grant_mat_target",
      target_grant_generation: 1,
    },
    "game.materialize": {
      materialization_bundle_artifact_id: "artifact_materialization",
      source_grant_id: "grant_materialization",
      source_grant_generation: 2,
      target_grant_id: "grant_game_target",
      target_grant_generation: 1,
    },
    "game.package": {
      standalone_game_artifact_id: "artifact_standalone",
      source_grant_id: "grant_standalone",
      source_grant_generation: 2,
      target_grant_id: "grant_package_target",
      target_grant_generation: 1,
    },
    "game.package.extract": {
      game_package_artifact_id: "artifact_package",
      source_grant_id: "grant_package",
      source_grant_generation: 2,
      target_grant_id: "grant_extract_target",
      target_grant_generation: 1,
    },
  };
  return targets[operation];
}

function rawJob(
  operation: string,
  version: number,
  jobId: string,
  operationParams: Record<string, unknown>,
) {
  return {
    format: "world-forge.studio_creation_job",
    format_version: version,
    job_id: jobId,
    workspace_id: "creation_workspace",
    operation,
    operation_params: operationParams,
    state: "queued",
    generation: 0,
    authority: {
      root_generation: 4,
      source_revision: SOURCE,
      workflow_status_hash: null,
      artifact_snapshot_hash: SNAPSHOT,
    },
    inputs: [],
    progress: "queued",
    result: null,
    error: null,
    created_at: "2026-08-05T00:00:00Z",
    started_at: null,
    finished_at: null,
    updated_at: "2026-08-05T00:00:00Z",
    record_hash: "e".repeat(64),
  };
}

function jobView(value: Record<string, unknown>): CreationJobView {
  const job = projectCreationJob(value, "creation_workspace");
  if (job === null) throw new Error("invalid component job fixture");
  return job;
}

function terminalJob(operation: string, jobId: string): StudioCreationJob {
  return {
    ...rawJob(operation, 6, jobId, operationParamsFor(operation)),
    state: "succeeded",
    progress: "committed",
    result: {
      output_artifact_ids: ["artifact_output"],
      artifact_snapshot_hash: SNAPSHOT,
      analysis_status: "passed",
      reason_codes: [],
      cleanup_pending: false,
      publication: {
        grant_id: "grant_materialization_target",
        grant_generation: 1,
        kind: "game_materialization_bundle_directory",
        state: "published",
        materialization_bundle: {
          format: "world-forge.game_materialization_bundle",
          format_version: 1,
          id: "materialization_output",
          content_hash: "f".repeat(64),
          tree_hash: "1".repeat(64),
        },
      },
    },
  } as unknown as StudioCreationJob;
}

function workspace(
  projectKind: StudioCreationWorkspace["project_kind"] = "game",
): StudioCreationWorkspace {
  return {
    format: "world-forge.studio_creation_workspace",
    format_version: 1,
    workspace_id: "creation_workspace",
    project_kind: projectKind,
    root_generation: 4,
    project: {
      format: "world-forge.project",
      format_version: 1,
      id: "project_01",
      content_hash: "f".repeat(64),
    },
    source_revision: SOURCE,
    workflow_status_hash: null,
    created_at: "2026-08-05T00:00:00Z",
    updated_at: "2026-08-05T00:00:00Z",
  };
}

function census(): CreationExecutionCensus {
  return {
    authority: {
      workspaceId: "creation_workspace",
      rootGeneration: 4,
      sourceRevision: SOURCE,
      workflowStatusHash: null,
      artifactSnapshotHash: SNAPSHOT,
    },
    evidence: {} as CreationExecutionCensus["evidence"],
    activeArtifacts: [],
    candidateArtifacts: [],
    selectableArtifacts: [],
    selectableById: new Map(),
  };
}

function authorityParams() {
  return {
    workspaceId: "creation_workspace",
    expectedRootGeneration: 4,
    expectedSourceRevision: SOURCE,
    expectedWorkflowStatusHash: null,
    expectedArtifactSnapshotHash: SNAPSHOT,
  };
}

function v4<T extends object>(method: string, result: T) {
  return {
    ok: true as const,
    value: {
      protocol: "rpg-world-forge.studio_protocol" as const,
      protocol_version: 4 as const,
      kind: "response" as const,
      request_id: "request_01",
      method,
      result,
    },
  };
}

function clientError(code: string, message: string) {
  return { ok: false as const, error: { code, message } };
}

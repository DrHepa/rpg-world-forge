// @vitest-environment jsdom

import { useState } from "react";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  CreationRuntimePipeline,
  type CreationRuntimePipelineProps,
} from "../../src/renderer/CreationRuntimePipeline";
import type { CreationExecutionCensus } from "../../src/renderer/creation-execution-state";
import type {
  ForgeStudioApi,
  StudioCreationArtifact,
  StudioCreationArtifactInspectResult,
  StudioCreationOutputGrant,
  StudioCreationWorkspace,
} from "../../src/shared/studio-api";

const SOURCE = "a".repeat(64);
const SNAPSHOT = "b".repeat(64);
const RECORD = "c".repeat(64);
const SEALED_SNAPSHOT = "e".repeat(64);

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("CreationRuntimePipeline", () => {
  it("renders neutral not-applicable copy for a non-game project", () => {
    const graph = runtimeGraph(false);
    renderPipeline({
      graph,
      workspace: { ...workspace(), project_kind: "universe_library" },
    });

    expect(screen.getByRole("heading", { name: "Runtime pipeline" })).toBeInTheDocument();
    expect(screen.getByText(/not applicable to this project kind/iu)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /compose|bundle/iu })).not.toBeInTheDocument();
  });

  it("submits runtime.compose with every artifact and published grant derived from sealed evidence", async () => {
    const graph = runtimeGraph(false);
    const composeCreationRuntime = vi.fn().mockResolvedValue(
      v4("creation_job.create", {
        job: rawJob("runtime.compose", {
          format_version: 4,
          job_id: "job_submitted_compose",
          operation_params: {
            gamepack_artifact_id: "artifact_gamepack",
            asset_inventory_artifact_id: "artifact_inventory",
            assetpack_artifact_id: "artifact_assetpack",
            target_grant_id: "grant_assetpack",
            target_grant_generation: 2,
          },
        }),
      }),
    );
    const onSubmittedJob = submittedJobSpy();
    renderPipeline({
      graph,
      api: graphApi(graph, { composeCreationRuntime }),
      onSubmittedJob,
    });

    const compose = screen.getByRole("group", { name: "Compose verified runtime" });
    const option = await within(compose).findByRole("radio", {
      name: /assetpack.*Published.*generation 2/iu,
    });
    fireEvent.click(option);
    fireEvent.click(within(compose).getByRole("button", { name: "Compose selected runtime" }));

    await waitFor(() =>
      expect(composeCreationRuntime).toHaveBeenCalledWith({
        ...authorityParams(),
        gamepackArtifactId: "artifact_gamepack",
        assetInventoryArtifactId: "artifact_inventory",
        assetpackArtifactId: "artifact_assetpack",
        targetGrantId: "grant_assetpack",
        expectedTargetGrantGeneration: 2,
      }),
    );
    expect(onSubmittedJob).toHaveBeenCalledWith(
      expect.objectContaining({ operation: "runtime.compose", job_id: "job_submitted_compose" }),
    );
    expect(screen.getByText(/candidate evidence does not change active readiness/iu)).toBeInTheDocument();
    expect(screen.getByText(/does not claim native verification or release readiness/iu)).toBeInTheDocument();
  });

  it("suppresses an identical queued composition and adopts the shared job controller", async () => {
    const graph = runtimeGraph(false);
    const pending = rawJob("runtime.compose", {
      format_version: 4,
      job_id: "job_existing_compose",
      state: "running",
      progress: "worker_started",
      operation_params: {
        gamepack_artifact_id: "artifact_gamepack",
        asset_inventory_artifact_id: "artifact_inventory",
        assetpack_artifact_id: "artifact_assetpack",
        target_grant_id: "grant_assetpack",
        target_grant_generation: 2,
      },
    });
    const composeCreationRuntime = vi.fn();
    const onObservedJob = observedJobSpy();
    const onSubmittedJob = submittedJobSpy();
    const api = graphApi(graph, {
      composeCreationRuntime,
      listCreationJobs: vi.fn().mockImplementation((params: { state: string | null }) =>
        Promise.resolve(v4("creation_job.list", {
          jobs: params.state === "running" ? [pending] : [],
          next_sequence: null,
        })),
      ),
    });
    renderPipeline({ graph, api, onObservedJob, onSubmittedJob });

    const compose = screen.getByRole("group", { name: "Compose verified runtime" });
    fireEvent.click(await within(compose).findByRole("radio"));
    fireEvent.click(within(compose).getByRole("button", { name: "Compose selected runtime" }));

    expect(await screen.findByText(/already running; no duplicate was submitted/iu)).toBeInTheDocument();
    expect(composeCreationRuntime).not.toHaveBeenCalled();
    expect(onObservedJob).toHaveBeenCalledWith(
      expect.objectContaining({ job_id: "job_existing_compose" }),
    );
    expect(onSubmittedJob).toHaveBeenCalledWith(
      expect.objectContaining({ job_id: "job_existing_compose" }),
    );
  });

  it("blocks controls when durable runtime job reconstruction is ambiguous", async () => {
    const graph = runtimeGraph(false);
    const pending = ["job_pending_a", "job_pending_b"].map((jobId) =>
      rawJob("runtime.compose", {
        format_version: 4,
        job_id: jobId,
        state: "running",
        progress: "worker_started",
        operation_params: {
          gamepack_artifact_id: "artifact_gamepack",
          asset_inventory_artifact_id: "artifact_inventory",
          assetpack_artifact_id: "artifact_assetpack",
          target_grant_id: "grant_assetpack",
          target_grant_generation: 2,
        },
      }),
    );
    const api = graphApi(graph, {
      listCreationJobs: vi.fn().mockImplementation((params: { state: string | null }) =>
        Promise.resolve(v4("creation_job.list", {
          jobs: params.state === null ? pending : [],
          next_sequence: null,
        })),
      ),
    });
    renderPipeline({ graph, api });

    const compose = screen.getByRole("group", { name: "Compose verified runtime" });
    expect(await screen.findByText(/ambiguous durable runtime jobs/iu)).toBeInTheDocument();
    expect(compose).toBeDisabled();
    expect(screen.getByText("ambiguous_pending_runtime_jobs")).toBeInTheDocument();
  });

  it("selects a pathless runtime destination and revokes ready grants with CAS", async () => {
    const graph = runtimeGraph(true);
    const target = runtimeGrant();
    const selectCreationRuntimeBundleOutput = vi
      .fn()
      .mockResolvedValueOnce(clientError("cancelled", "dialog cancelled"))
      .mockResolvedValueOnce(v4("creation_output_grant.create", { grant: target }));
    const revokeCreationAssetpackOutput = vi.fn().mockResolvedValue(
      v4("creation_output_grant.revoke", {
        grant: { ...target, state: "revoked", generation: 1, updated_at: "2026-08-05T00:00:01Z" },
      }),
    );
    const api = graphApi(graph, {
      selectCreationRuntimeBundleOutput,
      revokeCreationAssetpackOutput,
    });
    renderPipeline({ graph, api });

    const bundle = screen.getByRole("group", { name: "Build runtime bundle" });
    const group = await within(bundle).findByRole("radio", { name: /partially supported/iu });
    fireEvent.click(group);
    fireEvent.click(within(bundle).getByRole("button", { name: "Select runtime bundle destination" }));
    expect(await screen.findByText(/selection was cancelled; no grant was created/iu)).toBeInTheDocument();
    fireEvent.click(within(bundle).getByRole("button", { name: "Select runtime bundle destination" }));

    expect(await within(bundle).findByText("Puzzle runtime bundle")).toBeInTheDocument();
    expect(within(bundle).getByText("game_runtime_bundle_directory")).toBeInTheDocument();
    expect(within(bundle).queryByText(/\//u)).not.toBeInTheDocument();
    fireEvent.click(
      within(bundle).getByRole("button", { name: "Revoke selected runtime bundle destination" }),
    );
    await waitFor(() =>
      expect(revokeCreationAssetpackOutput).toHaveBeenCalledWith({
        grantId: "grant_runtime_target",
        expectedGeneration: 0,
      }),
    );
  });

  it("submits exact runtime bundle authority from one four-output composition", async () => {
    const graph = runtimeGraph(true);
    graph.grants.push(runtimeGrant());
    const buildCreationRuntimeBundle = vi.fn().mockResolvedValue(
      v4("creation_job.create", {
        job: rawJob("runtime.bundle.build", {
          format_version: 5,
          job_id: "job_build_runtime",
          operation_params: {
            gamepack_artifact_id: "artifact_gamepack",
            asset_inventory_artifact_id: "artifact_inventory",
            assetpack_artifact_id: "artifact_assetpack",
            runtime_snapshot_artifact_id: "artifact_runtime_snapshot",
            runtime_adapter_registry_artifact_id: "artifact_runtime_registry",
            runtime_composition_artifact_id: "artifact_runtime_composition",
            runtime_support_report_artifact_id: "artifact_runtime_support",
            source_grant_id: "grant_assetpack",
            source_grant_generation: 2,
            target_grant_id: "grant_runtime_target",
            target_grant_generation: 1,
          },
        }),
      }),
    );
    const onSubmittedJob = submittedJobSpy();
    renderPipeline({
      graph,
      api: graphApi(graph, { buildCreationRuntimeBundle }),
      onSubmittedJob,
    });

    const bundle = screen.getByRole("group", { name: "Build runtime bundle" });
    fireEvent.click(await within(bundle).findByRole("radio", { name: /partially supported/iu }));
    fireEvent.click(within(bundle).getByRole("button", { name: "Build selected runtime bundle" }));

    await waitFor(() =>
      expect(buildCreationRuntimeBundle).toHaveBeenCalledWith({
        ...authorityParams(),
        gamepackArtifactId: "artifact_gamepack",
        assetInventoryArtifactId: "artifact_inventory",
        assetpackArtifactId: "artifact_assetpack",
        runtimeSnapshotArtifactId: "artifact_runtime_snapshot",
        runtimeAdapterRegistryArtifactId: "artifact_runtime_registry",
        runtimeCompositionArtifactId: "artifact_runtime_composition",
        runtimeSupportReportArtifactId: "artifact_runtime_support",
        sourceGrantId: "grant_assetpack",
        expectedSourceGrantGeneration: 2,
        targetGrantId: "grant_runtime_target",
        expectedTargetGrantGeneration: 0,
      }),
    );
    expect(onSubmittedJob).toHaveBeenCalledWith(
      expect.objectContaining({ job_id: "job_build_runtime" }),
    );
  });

  it("submits v12 headless authority with stable platform and fixed independent display", async () => {
    const graph = runtimeGraph(true);
    graph.census.candidateArtifacts.push(
      artifact("artifact_headless_script", "world-forge.game_execution_script", "candidate", [], "job_bundle"),
    );
    graph.census.selectableArtifacts.push(graph.census.candidateArtifacts.at(-1)!);
    (graph.census as unknown as { selectableById: Map<string, StudioCreationArtifact> }).selectableById = new Map(
      graph.census.selectableArtifacts.map((item) => [item.artifact_id, item]),
    );
    graph.grants.push(publishedRuntimeGrant(), headlessGrant());
    const verifyCreationHeadless = vi.fn().mockResolvedValue(
      v5("creation_job.create", {
        job: rawJob("runtime.headless.verify", {
          format_version: 12,
          job_id: "job_headless",
          state: "succeeded",
          progress: "committed",
          result: {
            output_artifact_ids: ["artifact_support_authority", "artifact_runtime_evidence", "artifact_runtime_support_report"],
            artifact_snapshot_hash: SNAPSHOT,
            analysis_status: "passed",
            reason_codes: [],
            cleanup_pending: false,
            runtime_support_authority: { format: "world-forge.runtime_support_authority", format_version: 1, id: "support_authority_01", content_hash: "1".repeat(64) },
            runtime_evidence: { format: "world-forge.runtime_evidence", format_version: 1, id: "runtime_evidence_01", content_hash: "2".repeat(64) },
            runtime_support_report: { format: "world-forge.runtime_support_report", format_version: 1, id: "runtime_support_01", content_hash: "3".repeat(64) },
            release_status: "blocked",
            native_status: "unavailable",
            supported: false,
            publication: {
              grant_id: "grant_headless_target",
              grant_generation: 0,
              kind: "headless_evidence_directory",
              state: "published",
              headless_evidence_set: { format: "world-forge.headless_evidence_set", format_version: 1, id: "headless_evidence_01", content_hash: "4".repeat(64), tree_hash: "5".repeat(64) },
            },
          },
        }),
      }),
    );
    renderPipeline({
      graph,
      api: graphApi(graph, {
        selectCreationHeadlessEvidenceOutput: vi.fn(),
        verifyCreationHeadless,
      }),
      authorityCapabilities: {
        protocolVersion: 5,
        asset_authority_reviews: true,
        asset_release_authority: true,
        runtime_headless_authority: true,
        creation_preview_pre_release: true,
      },
    });

    const headless = await screen.findByRole("group", { name: "Verify headless authority" });
    fireEvent.click(within(headless).getByRole("radio", { name: /headless candidate/iu }));
    fireEvent.click(within(headless).getByRole("button", { name: "Verify selected headless candidate" }));

    await waitFor(() => expect(verifyCreationHeadless).toHaveBeenCalledWith({
      workspaceId: "creation_workspace",
      runtimeBundleArtifactId: "artifact_runtime_bundle",
      sourceGrantId: "grant_runtime_source",
      headlessScriptArtifactId: "artifact_headless_script",
      targetGrantId: "grant_headless_target",
      platformId: "platform:linux_x86_64",
    }));
    expect(await screen.findByText("Headless verified; native unavailable; release remains blocked.")).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/native verified|release-ready|supported current/iu);
  });

  it("shows precise required and optional reason codes but blocks unsupported bundles", async () => {
    const graph = runtimeGraph(true, true);
    renderPipeline({ graph });

    const bundle = screen.getByRole("group", { name: "Build runtime bundle" });
    await within(bundle).findByText("required_feature_unsupported");
    expect(within(bundle).getByText("optional_feature_unsupported")).toBeInTheDocument();
    expect(within(bundle).getByText("audio:sfx")).toBeInTheDocument();
    expect(within(bundle).getByRole("button", { name: "Build selected runtime bundle" })).toBeDisabled();
  });
});

function PipelineHarness({
  graph,
  api,
  workspaceValue,
  onSubmittedJob,
  onObservedJob,
  authorityCapabilities = null,
}: {
  graph: ReturnType<typeof runtimeGraph>;
  api: ForgeStudioApi;
  workspaceValue: StudioCreationWorkspace;
  onSubmittedJob: CreationRuntimePipelineProps["onSubmittedJob"];
  onObservedJob: CreationRuntimePipelineProps["onObservedJob"];
  authorityCapabilities?: CreationRuntimePipelineProps["authorityCapabilities"];
}) {
  const [grants, setGrants] = useState(graph.grants);
  return (
    <CreationRuntimePipeline
      api={api}
      workspace={workspaceValue}
      census={graph.census}
      authorityCapabilities={authorityCapabilities}
      grants={grants}
      executionBusy={false}
      observedJob={null}
      trackingError={null}
      onNavigationStateChange={vi.fn()}
      onGrantChange={(next) => {
        setGrants((current) => [
          ...current.filter((grant) => grant.grant_id !== next.grant_id),
          next,
        ]);
      }}
      onGrantCensusRefresh={vi.fn()}
      onSubmittedJob={onSubmittedJob}
      onObservedJob={onObservedJob}
    />
  );
}

function renderPipeline({
  graph = runtimeGraph(true),
  api = graphApi(graph),
  workspace: workspaceValue = workspace(),
  onSubmittedJob = submittedJobSpy(),
  onObservedJob = observedJobSpy(),
  authorityCapabilities = null,
}: {
  graph?: ReturnType<typeof runtimeGraph>;
  api?: ForgeStudioApi;
  workspace?: StudioCreationWorkspace;
  onSubmittedJob?: CreationRuntimePipelineProps["onSubmittedJob"];
  onObservedJob?: CreationRuntimePipelineProps["onObservedJob"];
  authorityCapabilities?: CreationRuntimePipelineProps["authorityCapabilities"];
} = {}) {
  return render(
    <PipelineHarness
      graph={graph}
      api={api}
      workspaceValue={workspaceValue}
      onSubmittedJob={onSubmittedJob}
      onObservedJob={onObservedJob}
      authorityCapabilities={authorityCapabilities}
    />,
  );
}

function runtimeGraph(includeRuntime: boolean, unsupported = false) {
  const gamepack = artifact("artifact_gamepack", "world-forge.gamepack", "active", []);
  const inventory = artifact("artifact_inventory", "world-forge.asset_inventory", "active", []);
  const subject = artifact("artifact_subject", "world-forge.asset_subject", "candidate", []);
  const target = artifact("artifact_target", "world-forge.asset_target", "candidate", []);
  const style = artifact("artifact_style", "world-forge.asset_style", "candidate", []);
  const manifest = artifact("artifact_manifest", "world-forge.asset_manifest", "candidate", [], "job_seal");
  const assetpack = artifact(
    "artifact_assetpack",
    "world-forge.assetpack",
    "candidate",
    ["artifact_gamepack", "artifact_inventory", "artifact_subject", "artifact_target", "artifact_style", "artifact_manifest"],
    "job_seal",
  );
  const candidates = [subject, target, style, manifest, assetpack];
  if (includeRuntime) {
    candidates.push(
      artifact("artifact_runtime_snapshot", "world-forge.game_runtime_snapshot", "candidate", [], "job_compose"),
      artifact("artifact_runtime_registry", "world-forge.runtime_adapter_registry", "candidate", ["artifact_runtime_snapshot"], "job_compose"),
      artifact("artifact_runtime_bundle", "world-forge.game_runtime_bundle", "candidate", ["artifact_runtime_composition"], "job_bundle"),
      artifact(
        "artifact_runtime_composition",
        "world-forge.game_runtime_composition",
        "candidate",
        ["artifact_gamepack", "artifact_inventory", "artifact_assetpack", "artifact_runtime_registry", "artifact_runtime_snapshot"],
        "job_compose",
      ),
      artifact(
        "artifact_runtime_support",
        "world-forge.runtime_support_report",
        "candidate",
        ["artifact_gamepack", "artifact_runtime_composition"],
        "job_compose",
      ),
    );
  }
  const census = censusWith([gamepack, inventory], candidates);
  const inspections = new Map<string, StudioCreationArtifactInspectResult>();
  for (const candidate of candidates) inspections.set(candidate.artifact_id, inspection(candidate, census));
  if (includeRuntime) {
    const reasonCodes = [
      "adapter_not_verified",
      "headless_evidence_missing",
      "native_evidence_missing",
      "packaging_evidence_missing",
      ...(unsupported ? ["required_feature_unsupported"] : []),
      "save_replay_evidence_missing",
    ].sort();
    const support = inspections.get("artifact_runtime_support")!;
    inspections.set("artifact_runtime_support", {
      ...support,
      projection: {
        ...support.projection,
        facts: [
          { key: "supported", value: false },
          { key: "compatibility_status", value: unsupported ? "unsupported" : "partially_supported" },
          { key: "reason_code_count", value: reasonCodes.length },
          { key: "reason_codes", value: reasonCodes },
          { key: "missing_capability_count", value: unsupported ? 1 : 0 },
          { key: "missing_capabilities", value: unsupported ? ["audio:sfx"] : [] },
          { key: "evidence_count", value: 0 },
          { key: "authoring", value: "valid" },
          { key: "compilation", value: "compiled" },
          { key: "assets", value: "sealed" },
          { key: "adapter", value: "declared" },
          { key: "packaging", value: "unverified" },
          { key: "release", value: "blocked" },
          { key: "execution_statuses", value: ["platform:linux_x86_64:untested"] },
        ],
      },
    });
  }
  const assetpackGrant = assetGrant(assetpack);
  const grants = [assetpackGrant];
  const jobs = new Map<string, Record<string, unknown>>([
    [
      "job_seal",
      rawJob("asset.release.seal", {
        format_version: 3,
        job_id: "job_seal",
        state: "succeeded",
        progress: "committed",
        operation_params: {
          qa_report_artifact_ids: ["artifact_qa"],
          manifest_id: manifest.subject.id,
          target_grant_id: "grant_assetpack",
          target_grant_generation: 2,
        },
        result: {
          output_artifact_ids: ["artifact_manifest", "artifact_assetpack"],
          artifact_snapshot_hash: SEALED_SNAPSHOT,
          analysis_status: "passed",
          reason_codes: [],
          cleanup_pending: false,
          publication: {
            grant_id: "grant_assetpack",
            grant_generation: 2,
            kind: "generic_assetpack_directory",
            state: "published",
            assetpack: assetpackGrant.publication,
          },
        },
      }),
    ],
  ]);
  if (includeRuntime) {
    jobs.set(
      "job_compose",
      rawJob("runtime.compose", {
        format_version: 4,
        job_id: "job_compose",
        state: "succeeded",
        progress: "committed",
        operation_params: {
          gamepack_artifact_id: "artifact_gamepack",
          asset_inventory_artifact_id: "artifact_inventory",
          assetpack_artifact_id: "artifact_assetpack",
          target_grant_id: "grant_assetpack",
          target_grant_generation: 2,
        },
        inputs: [gamepack, inventory, assetpack].map((item) => ({ artifact_id: item.artifact_id, subject: item.subject })),
        result: {
          output_artifact_ids: [
            "artifact_runtime_snapshot",
            "artifact_runtime_registry",
            "artifact_runtime_composition",
            "artifact_runtime_support",
          ],
          artifact_snapshot_hash: SNAPSHOT,
          analysis_status: unsupported ? "unsupported" : "passed",
          reason_codes: unsupported
            ? ["optional_feature_unsupported", "required_feature_unsupported"]
            : ["optional_feature_unsupported"],
          cleanup_pending: false,
        },
      }),
    );
  }
  return { census, inspections, jobs, grants };
}

function graphApi(
  graph: ReturnType<typeof runtimeGraph>,
  overrides: Partial<ForgeStudioApi> = {},
): ForgeStudioApi {
  return {
    inspectCreationArtifact: vi.fn().mockImplementation(({ artifactId }: { artifactId: string }) =>
      Promise.resolve(v4("creation_artifact.inspect", graph.inspections.get(artifactId)!)),
    ),
    getCreationJob: vi.fn().mockImplementation((jobId: string) =>
      Promise.resolve(v4("creation_job.get", { job: graph.jobs.get(jobId)! })),
    ),
    listCreationJobs: vi.fn().mockResolvedValue(
      v4("creation_job.list", { jobs: [], next_sequence: null }),
    ),
    listCreationAuthorityOutputGrants: vi.fn().mockResolvedValue(
      v5("creation_output_grant.list", {
        authority: publicAuthority(),
        artifact_snapshot_hash: SNAPSHOT,
        grants: [...graph.grants].sort((left, right) =>
          left.grant_id < right.grant_id ? -1 : left.grant_id > right.grant_id ? 1 : 0,
        ),
        next_cursor: null,
      }),
    ),
    composeCreationRuntime: vi.fn(),
    buildCreationRuntimeBundle: vi.fn(),
    selectCreationRuntimeBundleOutput: vi.fn(),
    revokeCreationAssetpackOutput: vi.fn(),
    ...overrides,
  } as unknown as ForgeStudioApi;
}

function artifact(
  artifactId: string,
  format: string,
  lifecycle: "active" | "candidate",
  dependencies: readonly string[],
  producerJobId?: string,
): StudioCreationArtifact {
  return {
    format: "world-forge.studio_creation_artifact",
    format_version: 1,
    artifact_id: artifactId,
    subject: { format, format_version: 1, id: `${format.split(".").at(-1)}_${artifactId}`, content_hash: hashFor(artifactId) },
    lifecycle,
    roles: ["runtime_test"],
    producer: producerJobId
      ? { kind: "future_candidate", phase_id: null, reference_id: producerJobId }
      : { kind: "active_phase_report", phase_id: "p10_canon_lock", reference_id: "report_01" },
    references: { dependency_count: dependencies.length, dependent_count: 0 },
    authority: publicAuthority(),
    record_hash: hashFor(`${artifactId}_record`),
  };
}

function inspection(item: StudioCreationArtifact, census: CreationExecutionCensus): StudioCreationArtifactInspectResult {
  return {
    authority: publicAuthority(),
    artifact_snapshot_hash: SNAPSHOT,
    artifact: item,
    projection: {
      projection_kind: "runtime_test",
      title: item.subject.id,
      status: null,
      facts: [],
      lineage: dependencyIds(item.artifact_id).map((artifactId) => ({
        relation: "depends_on",
        artifact_id: artifactId,
        lifecycle: census.selectableById.get(artifactId)!.lifecycle,
      })),
    },
  };
}

function dependencyIds(artifactId: string): string[] {
  const dependencyRows: Record<string, string[]> = {
    artifact_assetpack: ["artifact_gamepack", "artifact_inventory", "artifact_subject", "artifact_target", "artifact_style", "artifact_manifest"],
    artifact_runtime_snapshot: [],
    artifact_runtime_registry: ["artifact_runtime_snapshot"],
    artifact_runtime_composition: ["artifact_gamepack", "artifact_inventory", "artifact_assetpack", "artifact_runtime_registry", "artifact_runtime_snapshot"],
    artifact_runtime_support: ["artifact_gamepack", "artifact_runtime_composition"],
  };
  return dependencyRows[artifactId] ?? [];
}

function censusWith(activeArtifacts: StudioCreationArtifact[], candidateArtifacts: StudioCreationArtifact[]): CreationExecutionCensus {
  const selectableArtifacts = [...activeArtifacts, ...candidateArtifacts];
  return {
    authority: { workspaceId: "creation_workspace", rootGeneration: 4, sourceRevision: SOURCE, workflowStatusHash: null, artifactSnapshotHash: SNAPSHOT },
    evidence: {} as CreationExecutionCensus["evidence"],
    activeArtifacts,
    candidateArtifacts,
    selectableArtifacts,
    selectableById: new Map(selectableArtifacts.map((item) => [item.artifact_id, item])),
  };
}

function assetGrant(assetpack: StudioCreationArtifact): StudioCreationOutputGrant {
  return {
    format: "world-forge.studio_creation_output_grant",
    format_version: 1,
    grant_id: "grant_assetpack",
    workspace_id: "creation_workspace",
    kind: "generic_assetpack_directory",
    display_name: "Puzzle assets",
    state: "published",
    generation: 2,
    publication: { format: "world-forge.assetpack", format_version: 1, id: assetpack.subject.id, content_hash: assetpack.subject.content_hash, inventory_hash: "d".repeat(64) },
    created_at: "2026-08-05T00:00:00Z",
    updated_at: "2026-08-05T00:00:00Z",
  };
}

function publishedRuntimeGrant(): StudioCreationOutputGrant {
  return {
    format: "world-forge.studio_creation_output_grant",
    format_version: 2,
    grant_id: "grant_runtime_source",
    workspace_id: "creation_workspace",
    kind: "game_runtime_bundle_directory",
    display_name: "Published runtime bundle",
    state: "published",
    generation: 1,
    publication: {
      format: "world-forge.game_runtime_bundle",
      format_version: 1,
      id: "game_runtime_bundle_artifact_runtime_bundle",
      content_hash: hashFor("artifact_runtime_bundle"),
      tree_hash: "6".repeat(64),
    } as unknown as StudioCreationOutputGrant["publication"],
    created_at: "2026-08-05T00:00:00Z",
    updated_at: "2026-08-05T00:00:00Z",
  };
}

function headlessGrant(): StudioCreationOutputGrant {
  return ({
    format: "world-forge.studio_creation_output_grant",
    format_version: 6,
    grant_id: "grant_headless_target",
    workspace_id: "creation_workspace",
    kind: "headless_evidence_directory",
    display_name: "Headless evidence",
    state: "ready",
    generation: 0,
    publication: null,
    created_at: "2026-08-05T00:00:00Z",
    updated_at: "2026-08-05T00:00:00Z",
  } as unknown as StudioCreationOutputGrant);
}

function runtimeGrant(): StudioCreationOutputGrant {
  return {
    format: "world-forge.studio_creation_output_grant",
    format_version: 2,
    grant_id: "grant_runtime_target",
    workspace_id: "creation_workspace",
    kind: "game_runtime_bundle_directory",
    display_name: "Puzzle runtime bundle",
    state: "ready",
    generation: 0,
    publication: null,
    created_at: "2026-08-05T00:00:00Z",
    updated_at: "2026-08-05T00:00:00Z",
  };
}

function rawJob(operation: string, overrides: Record<string, unknown> = {}) {
  return {
    format: "world-forge.studio_creation_job",
    format_version: 1,
    job_id: "job_default",
    workspace_id: "creation_workspace",
    operation,
    operation_params: {},
    state: "queued",
    generation: 0,
    authority: { root_generation: 4, source_revision: SOURCE, workflow_status_hash: null, artifact_snapshot_hash: SNAPSHOT },
    inputs: [],
    progress: "queued",
    result: null,
    error: null,
    created_at: "2026-08-05T00:00:00Z",
    started_at: null,
    finished_at: null,
    updated_at: "2026-08-05T00:00:00Z",
    record_hash: RECORD,
    ...overrides,
  };
}

function workspace(): StudioCreationWorkspace {
  return {
    format: "world-forge.studio_creation_workspace",
    format_version: 1,
    workspace_id: "creation_workspace",
    project_kind: "game",
    root_generation: 4,
    project: { format: "world-forge.project", format_version: 1, id: "puzzle_project", content_hash: "e".repeat(64) },
    source_revision: SOURCE,
    workflow_status_hash: null,
    created_at: "2026-08-05T00:00:00Z",
    updated_at: "2026-08-05T00:00:00Z",
  };
}

function authorityParams() {
  return { workspaceId: "creation_workspace", expectedRootGeneration: 4, expectedSourceRevision: SOURCE, expectedWorkflowStatusHash: null, expectedArtifactSnapshotHash: SNAPSHOT };
}

function publicAuthority() {
  return { workspace_id: "creation_workspace", root_generation: 4, source_revision: SOURCE, workflow_status_hash: null };
}

function hashFor(value: string): string {
  let digit = 0;
  for (const character of value) digit = (digit + character.codePointAt(0)!) % 16;
  return digit.toString(16).repeat(64);
}

function submittedJobSpy() {
  return vi
    .fn<CreationRuntimePipelineProps["onSubmittedJob"]>()
    .mockImplementation(() => Promise.resolve());
}

function observedJobSpy() {
  return vi.fn<CreationRuntimePipelineProps["onObservedJob"]>();
}

function v4<T extends object>(method: string, result: T) {
  return { ok: true as const, value: { protocol: "rpg-world-forge.studio_protocol" as const, protocol_version: 4 as const, kind: "response" as const, request_id: "request_01", method, result } };
}

function clientError(code: string, message: string) {
  return { ok: false as const, error: { code, message } };
}

function v5<T extends object>(method: string, result: T) {
  return { ok: true as const, value: { protocol: "rpg-world-forge.studio_protocol" as const, protocol_version: 5 as const, kind: "response" as const, request_id: "request_01", method, result } };
}

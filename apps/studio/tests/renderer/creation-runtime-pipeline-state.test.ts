import { describe, expect, it, vi } from "vitest";

import {
  deriveRuntimePipelineCandidates,
  findIdenticalPendingRuntimeJob,
  loadCreationRuntimePipelineCandidates,
  runtimeBundleBuildSubmission,
  runtimeComposeSubmission,
} from "../../src/renderer/creation-runtime-pipeline-state";
import {
  projectCreationJob,
  type CreationExecutionCensus,
  type CreationJobView,
} from "../../src/renderer/creation-execution-state";
import type {
  ForgeStudioApi,
  StudioCreationArtifact,
  StudioCreationArtifactInspectResult,
  StudioCreationOutputGrant,
} from "../../src/shared/studio-api";

const SOURCE = "a".repeat(64);
const SNAPSHOT = "b".repeat(64);
const RECORD = "c".repeat(64);
const SEALED_SNAPSHOT = "e".repeat(64);

describe("creation runtime pipeline state", () => {
  it("derives one sealed compose candidate and one exact four-output bundle group", () => {
    const graph = runtimeGraph();
    const result = deriveRuntimePipelineCandidates(
      graph.census,
      graph.inspections,
      graph.jobs,
      graph.grants,
    );

    expect(result.composeCandidates).toEqual([
      expect.objectContaining({
        assetpackArtifactId: "artifact_assetpack",
        gamepackArtifactId: "artifact_gamepack",
        assetInventoryArtifactId: "artifact_inventory",
        sourceGrantId: "grant_assetpack",
        sourceGrantGeneration: 2,
        sealJobId: "job_seal",
      }),
    ]);
    expect(result.bundleCandidates).toEqual([
      expect.objectContaining({
        producerJobId: "job_compose",
        runtimeSnapshotArtifactId: "artifact_runtime_snapshot",
        runtimeAdapterRegistryArtifactId: "artifact_runtime_registry",
        runtimeCompositionArtifactId: "artifact_runtime_composition",
        runtimeSupportReportArtifactId: "artifact_runtime_support",
        compatibilityStatus: "partially_supported",
        optionalReasonCodes: ["optional_feature_unsupported"],
        supportReasonCodes: [
          "adapter_not_verified",
          "headless_evidence_missing",
          "native_evidence_missing",
          "packaging_evidence_missing",
          "save_replay_evidence_missing",
        ],
        missingCapabilities: [],
        bundleAllowed: true,
      }),
    ]);
    expect(result.blockingReasonCodes).toEqual([]);
  });

  it("fails closed on truncated assetpack lineage and mixed compose producers", () => {
    const truncated = runtimeGraph();
    const assetpack = truncated.census.selectableById.get("artifact_assetpack")!;
    replaceArtifact(truncated.census, {
      ...assetpack,
      references: { ...assetpack.references, dependency_count: 7 },
    });
    expect(() =>
      deriveRuntimePipelineCandidates(
        truncated.census,
        truncated.inspections,
        truncated.jobs,
        truncated.grants,
      ),
    ).toThrow(/truncated|dependency count/iu);

    const mixed = runtimeGraph();
    const support = mixed.census.selectableById.get("artifact_runtime_support")!;
    replaceArtifact(mixed.census, {
      ...support,
      producer: { ...support.producer, reference_id: "job_other_compose" },
    });
    expect(() =>
      deriveRuntimePipelineCandidates(
        mixed.census,
        mixed.inspections,
        mixed.jobs,
        mixed.grants,
      ),
    ).toThrow(/mixed|producer|four-output/iu);
  });

  it("accepts the historical seal snapshot but rejects a stale latest compose snapshot", () => {
    const graph = runtimeGraph();
    expect(graph.jobs.get("job_seal")!.record.result!.artifact_snapshot_hash).toBe(
      SEALED_SNAPSHOT,
    );
    expect(() =>
      deriveRuntimePipelineCandidates(
        graph.census,
        graph.inspections,
        graph.jobs,
        graph.grants,
      ),
    ).not.toThrow();

    const producer = graph.jobs.get("job_compose")!;
    graph.jobs.set(
      "job_compose",
      view(
        rawJob(producer.operation, {
          ...producer.record,
          result: {
            ...producer.record.result!,
            artifact_snapshot_hash: "f".repeat(64),
          },
        }),
      ),
    );
    expect(() =>
      deriveRuntimePipelineCandidates(
        graph.census,
        graph.inspections,
        graph.jobs,
        graph.grants,
      ),
    ).toThrow(/snapshot/iu);
  });

  it("rejects extra sealed lineage and a manifest outside the seal future-candidate set", () => {
    const extraLineage = runtimeGraph();
    const extra = artifact("artifact_extra", "world-forge.game_analysis", "active", []);
    extraLineage.census.activeArtifacts.push(extra);
    extraLineage.census.selectableArtifacts.push(extra);
    (extraLineage.census.selectableById as Map<string, StudioCreationArtifact>).set(
      extra.artifact_id,
      extra,
    );
    const assetpack = extraLineage.census.selectableById.get("artifact_assetpack")!;
    const expanded = {
      ...assetpack,
      references: {
        ...assetpack.references,
        dependency_count: assetpack.references.dependency_count + 1,
      },
      record_hash: hashFor("artifact_assetpack_extra_record"),
    };
    replaceArtifact(extraLineage.census, expanded);
    const assetpackInspection = extraLineage.inspections.get("artifact_assetpack")!;
    extraLineage.inspections.set("artifact_assetpack", {
      ...assetpackInspection,
      artifact: expanded,
      projection: {
        ...assetpackInspection.projection,
        lineage: [
          ...assetpackInspection.projection.lineage,
          { relation: "depends_on", artifact_id: extra.artifact_id, lifecycle: "active" },
        ],
      },
    });
    expect(() =>
      deriveRuntimePipelineCandidates(
        extraLineage.census,
        extraLineage.inspections,
        extraLineage.jobs,
        extraLineage.grants,
      ),
    ).toThrow(/lineage|dependency/iu);

    const staleManifest = runtimeGraph();
    const manifest = staleManifest.census.selectableById.get("artifact_manifest")!;
    const staleManifestArtifact = {
      ...manifest,
      producer: {
        kind: "active_phase_report" as const,
        phase_id: "p10_canon_lock",
        reference_id: "job_seal",
      },
    };
    replaceArtifact(staleManifest.census, staleManifestArtifact);
    const manifestInspection = staleManifest.inspections.get("artifact_manifest")!;
    staleManifest.inspections.set("artifact_manifest", {
      ...manifestInspection,
      artifact: staleManifestArtifact,
    });
    expect(() =>
      deriveRuntimePipelineCandidates(
        staleManifest.census,
        staleManifest.inspections,
        staleManifest.jobs,
        staleManifest.grants,
      ),
    ).toThrow(/manifest|producer/iu);
  });

  it("rejects broadened runtime compose producer parameters", () => {
    const graph = runtimeGraph();
    const producer = graph.jobs.get("job_compose")!;
    graph.jobs.set(
      "job_compose",
      view(
        rawJob(producer.operation, {
          ...producer.record,
          operation_params: {
            ...producer.record.operation_params,
            unexpected_runtime_mode: "unsafe",
          },
        }),
      ),
    );

    expect(() =>
      deriveRuntimePipelineCandidates(
        graph.census,
        graph.inspections,
        graph.jobs,
        graph.grants,
      ),
    ).toThrow(/parameter|authority/iu);
  });

  it("blocks unsupported required capabilities while retaining exact reason codes", () => {
    const graph = runtimeGraph();
    const support = graph.inspections.get("artifact_runtime_support")!;
    graph.inspections.set("artifact_runtime_support", {
      ...support,
      projection: {
        ...support.projection,
        facts: support.projection.facts.map((fact) => {
          if (fact.key === "compatibility_status") return { ...fact, value: "unsupported" };
          if (fact.key === "missing_capabilities") return { ...fact, value: ["audio:sfx"] };
          if (fact.key === "missing_capability_count") return { ...fact, value: 1 };
          if (fact.key === "reason_codes") {
            return {
              ...fact,
              value: [
                "adapter_not_verified",
                "headless_evidence_missing",
                "native_evidence_missing",
                "packaging_evidence_missing",
                "required_feature_unsupported",
                "save_replay_evidence_missing",
              ],
            };
          }
          if (fact.key === "reason_code_count") return { ...fact, value: 6 };
          return fact;
        }),
      },
    });

    const result = deriveRuntimePipelineCandidates(
      graph.census,
      graph.inspections,
      graph.jobs,
      graph.grants,
    );
    expect(result.bundleCandidates[0]).toMatchObject({
      bundleAllowed: false,
      missingCapabilities: ["audio:sfx"],
      blockingReasonCodes: ["required_feature_unsupported"],
    });
  });

  it("builds exact pathless compose and bundle submissions from derived authority", () => {
    const graph = runtimeGraph();
    const candidates = deriveRuntimePipelineCandidates(
      graph.census,
      graph.inspections,
      graph.jobs,
      graph.grants,
    );
    expect(runtimeComposeSubmission(graph.census, candidates.composeCandidates[0])).toEqual({
      workspaceId: "creation_workspace",
      expectedRootGeneration: 4,
      expectedSourceRevision: SOURCE,
      expectedWorkflowStatusHash: null,
      expectedArtifactSnapshotHash: SNAPSHOT,
      gamepackArtifactId: "artifact_gamepack",
      assetInventoryArtifactId: "artifact_inventory",
      assetpackArtifactId: "artifact_assetpack",
      targetGrantId: "grant_assetpack",
      expectedTargetGrantGeneration: 2,
    });
    expect(
      runtimeBundleBuildSubmission(
        graph.census,
        candidates.bundleCandidates[0],
        graph.grants.find((grant) => grant.grant_id === "grant_runtime_target")!,
      ),
    ).toEqual({
      workspaceId: "creation_workspace",
      expectedRootGeneration: 4,
      expectedSourceRevision: SOURCE,
      expectedWorkflowStatusHash: null,
      expectedArtifactSnapshotHash: SNAPSHOT,
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
    });
  });

  it("suppresses only byte-equivalent in-flight runtime parameters", async () => {
    const graph = runtimeGraph();
    const compose = runtimeComposeSubmission(
      graph.census,
      deriveRuntimePipelineCandidates(
        graph.census,
        graph.inspections,
        graph.jobs,
        graph.grants,
      ).composeCandidates[0],
    );
    const pending = rawJob("runtime.compose", {
      format_version: 4,
      job_id: "job_pending_compose",
      state: "running",
      progress: "worker_started",
      operation_params: {
        gamepack_artifact_id: compose.gamepackArtifactId,
        asset_inventory_artifact_id: compose.assetInventoryArtifactId,
        assetpack_artifact_id: compose.assetpackArtifactId,
        target_grant_id: compose.targetGrantId,
        target_grant_generation: compose.expectedTargetGrantGeneration,
      },
    });
    const listCreationJobs = vi.fn().mockImplementation((params: { state: string | null }) =>
      Promise.resolve(v4("creation_job.list", {
        jobs: params.state === "running" ? [pending] : [],
        next_sequence: null,
      })),
    );
    const api = { listCreationJobs } as unknown as ForgeStudioApi;
    await expect(
      findIdenticalPendingRuntimeJob(api, graph.census.authority, "runtime.compose", compose),
    ).resolves.toMatchObject({ job_id: "job_pending_compose" });
    await expect(
      findIdenticalPendingRuntimeJob(api, graph.census.authority, "runtime.compose", {
        ...compose,
        assetpackArtifactId: "artifact_other",
      }),
    ).resolves.toBeNull();
  });

  it("matches a queued bundle against its exclusively reserved next grant generation", async () => {
    const graph = runtimeGraph();
    const candidates = deriveRuntimePipelineCandidates(
      graph.census,
      graph.inspections,
      graph.jobs,
      graph.grants,
    );
    const submission = runtimeBundleBuildSubmission(
      graph.census,
      candidates.bundleCandidates[0],
      graph.grants.find((grant) => grant.grant_id === "grant_runtime_target")!,
    );
    const queued = rawJob("runtime.bundle.build", {
      format_version: 5,
      job_id: "job_pending_bundle",
      state: "queued",
      progress: "queued",
      operation_params: {
        gamepack_artifact_id: submission.gamepackArtifactId,
        asset_inventory_artifact_id: submission.assetInventoryArtifactId,
        assetpack_artifact_id: submission.assetpackArtifactId,
        runtime_snapshot_artifact_id: submission.runtimeSnapshotArtifactId,
        runtime_adapter_registry_artifact_id: submission.runtimeAdapterRegistryArtifactId,
        runtime_composition_artifact_id: submission.runtimeCompositionArtifactId,
        runtime_support_report_artifact_id: submission.runtimeSupportReportArtifactId,
        source_grant_id: submission.sourceGrantId,
        source_grant_generation: submission.expectedSourceGrantGeneration,
        target_grant_id: submission.targetGrantId,
        target_grant_generation: 1,
      },
    });
    const api = {
      listCreationJobs: vi.fn().mockImplementation((params: { state: string | null }) =>
        Promise.resolve(v4("creation_job.list", {
          jobs: params.state === "queued" ? [queued] : [],
          next_sequence: null,
        })),
      ),
    } as unknown as ForgeStudioApi;

    await expect(
      findIdenticalPendingRuntimeJob(
        api,
        graph.census.authority,
        "runtime.bundle.build",
        submission,
      ),
    ).resolves.toMatchObject({ job_id: "job_pending_bundle" });
    await expect(
      findIdenticalPendingRuntimeJob(
        api,
        graph.census.authority,
        "runtime.bundle.build",
        { ...submission, targetGrantId: "grant_other" },
      ),
    ).resolves.toBeNull();
  });

  it("fails closed when more than one identical runtime job is in flight", async () => {
    const graph = runtimeGraph();
    const submission = runtimeComposeSubmission(
      graph.census,
      deriveRuntimePipelineCandidates(
        graph.census,
        graph.inspections,
        graph.jobs,
        graph.grants,
      ).composeCandidates[0],
    );
    const params = {
      gamepack_artifact_id: submission.gamepackArtifactId,
      asset_inventory_artifact_id: submission.assetInventoryArtifactId,
      assetpack_artifact_id: submission.assetpackArtifactId,
      target_grant_id: submission.targetGrantId,
      target_grant_generation: submission.expectedTargetGrantGeneration,
    };
    const api = {
      listCreationJobs: vi.fn().mockImplementation((request: { state: string | null }) =>
        Promise.resolve(v4("creation_job.list", {
          jobs: request.state === "running"
            ? [
                rawJob("runtime.compose", {
                  format_version: 4,
                  job_id: "job_duplicate_a",
                  state: "running",
                  progress: "worker_started",
                  operation_params: params,
                }),
                rawJob("runtime.compose", {
                  format_version: 4,
                  job_id: "job_duplicate_b",
                  state: "running",
                  progress: "worker_started",
                  operation_params: params,
                }),
              ]
            : [],
          next_sequence: null,
        })),
      ),
    } as unknown as ForgeStudioApi;

    await expect(
      findIdenticalPendingRuntimeJob(
        api,
        graph.census.authority,
        "runtime.compose",
        submission,
      ),
    ).rejects.toThrow(/ambiguous|multiple/iu);
  });

  it("reconstructs one reserved runtime target and its exact queued bundle job after restart", async () => {
    const graph = runtimeGraph();
    const readyIndex = graph.grants.findIndex((grant) => grant.grant_id === "grant_runtime_target");
    graph.grants[readyIndex] = grant("grant_runtime_target", 2, "reserved", 1, null);
    const queued = rawJob("runtime.bundle.build", {
      format_version: 5,
      job_id: "job_pending_bundle",
      state: "queued",
      progress: "queued",
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
    });
    const api = {
      inspectCreationArtifact: vi.fn().mockImplementation(({ artifactId }: { artifactId: string }) =>
        Promise.resolve(v4("creation_artifact.inspect", graph.inspections.get(artifactId)!)),
      ),
      getCreationJob: vi.fn().mockImplementation((jobId: string) =>
        Promise.resolve(v4("creation_job.get", { job: graph.jobs.get(jobId)!.record })),
      ),
      listCreationJobs: vi.fn().mockResolvedValue(
        v4("creation_job.list", { jobs: [queued], next_sequence: null }),
      ),
    } as unknown as ForgeStudioApi;

    const loaded = await loadCreationRuntimePipelineCandidates(
      api,
      graph.census,
      graph.grants,
    );
    expect(loaded.composeCandidates).toHaveLength(1);
    expect(loaded.bundleCandidates).toHaveLength(1);
    expect(loaded.pendingJobs).toEqual([
      expect.objectContaining({ job_id: "job_pending_bundle", operation: "runtime.bundle.build" }),
    ]);
    expect(loaded.boundGrantJobIds).toEqual(
      new Map([["grant_runtime_target", "job_pending_bundle"]]),
    );
    expect(loaded.blockingReasonCodes).toEqual([]);
  });
});

function runtimeGraph() {
  const gamepack = artifact("artifact_gamepack", "world-forge.gamepack", "active", []);
  const inventory = artifact("artifact_inventory", "world-forge.asset_inventory", "active", []);
  const subject = artifact("artifact_subject", "world-forge.asset_subject", "candidate", []);
  const target = artifact("artifact_target", "world-forge.asset_target", "candidate", []);
  const style = artifact("artifact_style", "world-forge.asset_style", "candidate", []);
  const manifest = artifact(
    "artifact_manifest",
    "world-forge.asset_manifest",
    "candidate",
    [],
    "job_seal",
  );
  const assetpackDependencies = [
    gamepack.artifact_id,
    inventory.artifact_id,
    subject.artifact_id,
    target.artifact_id,
    style.artifact_id,
    manifest.artifact_id,
  ];
  const assetpack = artifact(
    "artifact_assetpack",
    "world-forge.assetpack",
    "candidate",
    assetpackDependencies,
    "job_seal",
  );
  const runtimeSnapshot = artifact(
    "artifact_runtime_snapshot",
    "world-forge.game_runtime_snapshot",
    "candidate",
    [],
    "job_compose",
  );
  const runtimeRegistry = artifact(
    "artifact_runtime_registry",
    "world-forge.runtime_adapter_registry",
    "candidate",
    [runtimeSnapshot.artifact_id],
    "job_compose",
  );
  const runtimeComposition = artifact(
    "artifact_runtime_composition",
    "world-forge.game_runtime_composition",
    "candidate",
    [
      gamepack.artifact_id,
      inventory.artifact_id,
      assetpack.artifact_id,
      runtimeRegistry.artifact_id,
      runtimeSnapshot.artifact_id,
    ],
    "job_compose",
  );
  const runtimeSupport = artifact(
    "artifact_runtime_support",
    "world-forge.runtime_support_report",
    "candidate",
    [gamepack.artifact_id, runtimeComposition.artifact_id],
    "job_compose",
  );
  const census = censusWith(
    [gamepack, inventory],
    [
      subject,
      target,
      style,
      manifest,
      assetpack,
      runtimeSnapshot,
      runtimeRegistry,
      runtimeComposition,
      runtimeSupport,
    ],
  );
  const inspections = new Map<string, StudioCreationArtifactInspectResult>();
  for (const candidate of census.candidateArtifacts) {
    inspections.set(candidate.artifact_id, inspection(candidate, census));
  }
  inspections.set("artifact_runtime_support", {
    ...inspections.get("artifact_runtime_support")!,
    projection: {
      ...inspections.get("artifact_runtime_support")!.projection,
      status: null,
      facts: [
        { key: "supported", value: false },
        { key: "compatibility_status", value: "partially_supported" },
        { key: "reason_code_count", value: 5 },
        {
          key: "reason_codes",
          value: [
            "adapter_not_verified",
            "headless_evidence_missing",
            "native_evidence_missing",
            "packaging_evidence_missing",
            "save_replay_evidence_missing",
          ],
        },
        { key: "missing_capability_count", value: 0 },
        { key: "missing_capabilities", value: [] },
        { key: "evidence_count", value: 0 },
        { key: "authoring", value: "valid" },
        { key: "compilation", value: "compiled" },
        { key: "assets", value: "sealed" },
        { key: "adapter", value: "declared" },
        { key: "packaging", value: "unverified" },
        { key: "release", value: "blocked" },
        {
          key: "execution_statuses",
          value: ["platform:linux_x86_64:untested", "platform:windows_x86_64:untested"],
        },
      ],
    },
  });
  const grants = [
    grant("grant_assetpack", 1, "published", 2, {
      format: "world-forge.assetpack",
      format_version: 1,
      id: assetpack.subject.id,
      content_hash: assetpack.subject.content_hash,
      inventory_hash: "d".repeat(64),
    }),
    grant("grant_runtime_target", 2, "ready", 0, null),
  ];
  const jobs = new Map<string, CreationJobView>([
    [
      "job_seal",
      view(
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
            output_artifact_ids: [manifest.artifact_id, assetpack.artifact_id],
            artifact_snapshot_hash: SEALED_SNAPSHOT,
            analysis_status: "passed",
            reason_codes: [],
            cleanup_pending: false,
            publication: {
              grant_id: "grant_assetpack",
              grant_generation: 2,
              kind: "generic_assetpack_directory",
              state: "published",
              assetpack: grants[0].publication,
            },
          },
        }),
      ),
    ],
    [
      "job_compose",
      view(
        rawJob("runtime.compose", {
          format_version: 4,
          job_id: "job_compose",
          state: "succeeded",
          progress: "committed",
          operation_params: {
            gamepack_artifact_id: gamepack.artifact_id,
            asset_inventory_artifact_id: inventory.artifact_id,
            assetpack_artifact_id: assetpack.artifact_id,
            target_grant_id: "grant_assetpack",
            target_grant_generation: 2,
          },
          inputs: [gamepack, inventory, assetpack].map((item) => ({
            artifact_id: item.artifact_id,
            subject: item.subject,
          })),
          result: {
            output_artifact_ids: [
              runtimeSnapshot.artifact_id,
              runtimeRegistry.artifact_id,
              runtimeComposition.artifact_id,
              runtimeSupport.artifact_id,
            ],
            artifact_snapshot_hash: SNAPSHOT,
            analysis_status: "passed",
            reason_codes: ["optional_feature_unsupported"],
            cleanup_pending: false,
          },
        }),
      ),
    ],
  ]);
  return { census, inspections, jobs, grants };
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
    subject: {
      format,
      format_version: 1,
      id: `${format.split(".").at(-1)}_${artifactId.replace("artifact_", "")}`,
      content_hash: hashFor(artifactId),
    },
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

function inspection(
  item: StudioCreationArtifact,
  census: CreationExecutionCensus,
): StudioCreationArtifactInspectResult {
  const dependencies = dependencyIds(item.artifact_id);
  return {
    authority: publicAuthority(),
    artifact_snapshot_hash: SNAPSHOT,
    artifact: item,
    projection: {
      projection_kind: "runtime_test",
      title: item.subject.id,
      status: null,
      facts: [],
      lineage: dependencies.map((artifactId) => ({
        relation: "depends_on",
        artifact_id: artifactId,
        lifecycle: census.selectableById.get(artifactId)!.lifecycle,
      })),
    },
  };
}

function dependencyIds(artifactId: string): string[] {
  const rows: Record<string, string[]> = {
    artifact_assetpack: [
      "artifact_gamepack",
      "artifact_inventory",
      "artifact_subject",
      "artifact_target",
      "artifact_style",
      "artifact_manifest",
    ],
    artifact_runtime_snapshot: [],
    artifact_runtime_registry: ["artifact_runtime_snapshot"],
    artifact_runtime_composition: [
      "artifact_gamepack",
      "artifact_inventory",
      "artifact_assetpack",
      "artifact_runtime_registry",
      "artifact_runtime_snapshot",
    ],
    artifact_runtime_support: ["artifact_gamepack", "artifact_runtime_composition"],
  };
  return rows[artifactId] ?? [];
}

function censusWith(
  activeArtifacts: StudioCreationArtifact[],
  candidateArtifacts: StudioCreationArtifact[],
): CreationExecutionCensus {
  const selectableArtifacts = [...activeArtifacts, ...candidateArtifacts];
  return {
    authority: {
      workspaceId: "creation_workspace",
      rootGeneration: 4,
      sourceRevision: SOURCE,
      workflowStatusHash: null,
      artifactSnapshotHash: SNAPSHOT,
    },
    evidence: {} as CreationExecutionCensus["evidence"],
    activeArtifacts,
    candidateArtifacts,
    selectableArtifacts,
    selectableById: new Map(selectableArtifacts.map((item) => [item.artifact_id, item])),
  };
}

function replaceArtifact(census: CreationExecutionCensus, next: StudioCreationArtifact): void {
  for (const collection of [census.activeArtifacts, census.candidateArtifacts, census.selectableArtifacts]) {
    const index = collection.findIndex((item) => item.artifact_id === next.artifact_id);
    if (index >= 0) collection[index] = next;
  }
  (census.selectableById as Map<string, StudioCreationArtifact>).set(next.artifact_id, next);
}

function grant(
  grantId: string,
  formatVersion: 1 | 2,
  state: "ready" | "reserved" | "published",
  generation: number,
  publication: StudioCreationOutputGrant["publication"],
): StudioCreationOutputGrant {
  return {
    format: "world-forge.studio_creation_output_grant",
    format_version: formatVersion,
    grant_id: grantId,
    workspace_id: "creation_workspace",
    kind: formatVersion === 1 ? "generic_assetpack_directory" : "game_runtime_bundle_directory",
    display_name: formatVersion === 1 ? "Puzzle assets" : "Puzzle runtime bundle",
    state,
    generation,
    publication,
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
    record_hash: RECORD,
    ...overrides,
  };
}

function view(value: ReturnType<typeof rawJob>): CreationJobView {
  const projected = projectCreationJob(value, "creation_workspace");
  if (!projected) throw new Error("test job did not project");
  return projected;
}

function publicAuthority() {
  return {
    workspace_id: "creation_workspace",
    root_generation: 4,
    source_revision: SOURCE,
    workflow_status_hash: null,
  };
}

function hashFor(value: string): string {
  let digit = 0;
  for (const character of value) digit = (digit + character.codePointAt(0)!) % 16;
  return digit.toString(16).repeat(64);
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

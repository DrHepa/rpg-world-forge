import { describe, expect, it, vi } from "vitest";

import {
  deriveMaterializationPipelineCandidates,
  findIdenticalPendingMaterializationJob,
  gameMaterializeSubmission,
  gamePackageExtractSubmission,
  gamePackageSubmission,
  loadCreationMaterializationPipelineCandidates,
  materializationBundleBuildSubmission,
} from "../../src/renderer/creation-materialization-pipeline-state";
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

describe("creation materialization pipeline state", () => {
  it("derives one exact current producer-and-publication chain through extraction", () => {
    const graph = materializationGraph();
    const result = deriveMaterializationPipelineCandidates(
      graph.census,
      graph.inspections,
      graph.jobs,
      graph.grants,
    );

    expect(result.runtimeBundleCandidates).toEqual([
      expect.objectContaining({
        artifactId: "artifact_runtime_bundle",
        sourceGrantId: "grant_runtime",
        sourceGrantGeneration: 2,
        producerJobId: "job_runtime_bundle",
        gamepackArtifactId: "artifact_gamepack",
        gamepackContentHash: hashFor("artifact_gamepack"),
      }),
    ]);
    expect(result.materializationBundleCandidates).toEqual([
      expect.objectContaining({
        artifactId: "artifact_materialization",
        predecessorArtifactId: "artifact_runtime_bundle",
        sourceGrantId: "grant_materialization",
        sourceGrantGeneration: 2,
      }),
    ]);
    expect(result.standaloneCandidates).toEqual([
      expect.objectContaining({
        artifactId: "artifact_standalone",
        predecessorArtifactId: "artifact_materialization",
        sourceGrantId: "grant_standalone",
      }),
    ]);
    expect(result.packageCandidates).toEqual([
      expect.objectContaining({
        artifactId: "artifact_package",
        predecessorArtifactId: "artifact_standalone",
        sourceGrantId: "grant_package",
        archiveSha256: hashFor("package_archive"),
      }),
    ]);
    expect(result.extractionCandidates).toEqual([
      expect.objectContaining({
        artifactId: "artifact_extraction",
        predecessorArtifactId: "artifact_package",
        publishedStandaloneGrantId: "grant_extracted",
        preservedStandaloneArtifactId: "artifact_standalone",
        preservedStandaloneSubjectId: "standalone_game_artifact_standalone",
        preservedStandaloneContentHash: hashFor("artifact_standalone"),
        preservedStandaloneTreeHash: hashFor("standalone_tree"),
      }),
    ]);
    expect(result.blockingReasonCodes).toEqual([]);
  });

  it("builds fixed pathless submissions with exact current authority and generations", () => {
    const graph = materializationGraph();
    const candidates = deriveMaterializationPipelineCandidates(
      graph.census,
      graph.inspections,
      graph.jobs,
      graph.grants,
    );
    const readyMaterialization = readyGrant(
      "grant_materialization_target",
      3,
      "game_materialization_bundle_directory",
    );
    const readyStandalone = readyGrant(
      "grant_standalone_target",
      4,
      "standalone_game_directory",
    );
    const readyPackage = readyGrant("grant_package_target", 5, "game_package_file");
    const readyExtraction = readyGrant(
      "grant_extraction_target",
      4,
      "standalone_game_directory",
    );

    expect(
      materializationBundleBuildSubmission(
        graph.census,
        candidates.runtimeBundleCandidates[0],
        readyMaterialization,
      ),
    ).toEqual({
      ...authorityParams(),
      runtimeBundleArtifactId: "artifact_runtime_bundle",
      sourceGrantId: "grant_runtime",
      expectedSourceGrantGeneration: 2,
      targetGrantId: "grant_materialization_target",
      expectedTargetGrantGeneration: 0,
    });
    expect(
      gameMaterializeSubmission(
        graph.census,
        candidates.materializationBundleCandidates[0],
        readyStandalone,
      ),
    ).toEqual({
      ...authorityParams(),
      materializationBundleArtifactId: "artifact_materialization",
      sourceGrantId: "grant_materialization",
      expectedSourceGrantGeneration: 2,
      targetGrantId: "grant_standalone_target",
      expectedTargetGrantGeneration: 0,
    });
    expect(
      gamePackageSubmission(
        graph.census,
        candidates.standaloneCandidates[0],
        readyPackage,
      ),
    ).toEqual({
      ...authorityParams(),
      standaloneGameArtifactId: "artifact_standalone",
      sourceGrantId: "grant_standalone",
      expectedSourceGrantGeneration: 2,
      targetGrantId: "grant_package_target",
      expectedTargetGrantGeneration: 0,
    });
    expect(
      gamePackageExtractSubmission(
        graph.census,
        candidates.packageCandidates[0],
        readyExtraction,
      ),
    ).toEqual({
      ...authorityParams(),
      gamePackageArtifactId: "artifact_package",
      sourceGrantId: "grant_package",
      expectedSourceGrantGeneration: 2,
      targetGrantId: "grant_extraction_target",
      expectedTargetGrantGeneration: 0,
    });
  });

  it("rejects mixed producer authority, stale lifecycle, truncated lineage, and hash drift", () => {
    const mixed = materializationGraph();
    mixed.jobs.set(
      "job_materialization",
      view(
        rawJob("game.materialization.bundle.build", {
          ...mixed.jobs.get("job_materialization")!.record,
          operation_params: {
            ...operationParams(mixed.jobs.get("job_materialization")!),
            runtime_bundle_artifact_id: "artifact_other_runtime",
          },
        }),
      ),
    );
    expect(() => derive(mixed)).toThrow(/predecessor|parameter|lineage/iu);

    const stale = materializationGraph();
    replaceArtifact(stale.census, {
      ...stale.census.selectableById.get("artifact_package")!,
      lifecycle: "active",
    });
    expect(() => derive(stale)).toThrow(/current candidate|lifecycle/iu);

    const truncated = materializationGraph();
    const inspection = truncated.inspections.get("artifact_materialization")!;
    truncated.inspections.set("artifact_materialization", {
      ...inspection,
      projection: { ...inspection.projection, lineage: [] },
    });
    expect(() => derive(truncated)).toThrow(/truncated|dependency/iu);

    const hashDrift = materializationGraph();
    const grantIndex = hashDrift.grants.findIndex((grant) => grant.grant_id === "grant_package");
    hashDrift.grants[grantIndex] = {
      ...hashDrift.grants[grantIndex],
      publication: {
        ...(hashDrift.grants[grantIndex].publication as unknown as Record<string, unknown>),
        archive_sha256: hashFor("tampered_archive"),
      } as StudioCreationOutputGrant["publication"],
    };
    expect(() => derive(hashDrift)).toThrow(/publication|hash/iu);

    const targetGrantDrift = materializationGraph();
    const targetGrantJob = targetGrantDrift.jobs.get("job_materialization")!;
    targetGrantDrift.jobs.set(
      targetGrantJob.job_id,
      view(
        rawJob(targetGrantJob.operation, {
          ...targetGrantJob.record,
          operation_params: {
            ...operationParams(targetGrantJob),
            target_grant_id: "grant_other_materialization",
          },
        }),
      ),
    );
    expect(() => derive(targetGrantDrift)).toThrow(/publication|grant|target/iu);

    const targetGenerationDrift = materializationGraph();
    const targetGenerationJob = targetGenerationDrift.jobs.get("job_materialization")!;
    targetGenerationDrift.jobs.set(
      targetGenerationJob.job_id,
      view(
        rawJob(targetGenerationJob.operation, {
          ...targetGenerationJob.record,
          operation_params: {
            ...operationParams(targetGenerationJob),
            target_grant_generation: 1,
          },
        }),
      ),
    );
    expect(() => derive(targetGenerationDrift)).toThrow(/publication|generation|target/iu);
  });

  it("binds the runtime bundle source grant to the exact published assetpack", () => {
    const missing = materializationGraph();
    missing.grants.splice(
      missing.grants.findIndex((grant) => grant.grant_id === "grant_assetpack"),
      1,
    );
    expect(() => derive(missing)).toThrow(/assetpack|source grant|publication/iu);

    const generationDrift = materializationGraph();
    const generationJob = generationDrift.jobs.get("job_runtime_bundle")!;
    generationDrift.jobs.set(
      generationJob.job_id,
      view(
        rawJob(generationJob.operation, {
          ...generationJob.record,
          operation_params: {
            ...operationParams(generationJob),
            source_grant_generation: 1,
          },
        }),
      ),
    );
    expect(() => derive(generationDrift)).toThrow(/assetpack|generation|source grant/iu);

    const identityDrift = materializationGraph();
    const assetpackGrantIndex = identityDrift.grants.findIndex(
      (grant) => grant.grant_id === "grant_assetpack",
    );
    identityDrift.grants[assetpackGrantIndex] = {
      ...identityDrift.grants[assetpackGrantIndex],
      publication: {
        ...(identityDrift.grants[assetpackGrantIndex]
          .publication as unknown as Record<string, unknown>),
        content_hash: hashFor("different_assetpack"),
      } as StudioCreationOutputGrant["publication"],
    };
    expect(() => derive(identityDrift)).toThrow(/assetpack|identity|publication/iu);

    const inventoryHashDrift = materializationGraph();
    const inventoryGrantIndex = inventoryHashDrift.grants.findIndex(
      (grant) => grant.grant_id === "grant_assetpack",
    );
    inventoryHashDrift.grants[inventoryGrantIndex] = {
      ...inventoryHashDrift.grants[inventoryGrantIndex],
      publication: {
        ...(inventoryHashDrift.grants[inventoryGrantIndex]
          .publication as unknown as Record<string, unknown>),
        inventory_hash: hashFor("different_inventory"),
      } as StudioCreationOutputGrant["publication"],
    };
    expect(() => derive(inventoryHashDrift)).toThrow(
      /assetpack|inventory|publication/iu,
    );
  });

  it("rejects ambiguous publications and extraction that changes standalone identity", () => {
    const ambiguous = materializationGraph();
    ambiguous.grants.push({
      ...ambiguous.grants.find((grant) => grant.grant_id === "grant_runtime")!,
      grant_id: "grant_runtime_duplicate",
    });
    const runtimeJob = ambiguous.jobs.get("job_runtime_bundle")!;
    ambiguous.jobs.set(
      runtimeJob.job_id,
      view(
        rawJob(runtimeJob.operation, {
          ...runtimeJob.record,
          result: {
            ...runtimeJob.record.result!,
            publication: {
              ...(runtimeJob.record.result as unknown as Record<string, unknown>).publication as object,
              grant_id: "grant_runtime_duplicate",
            },
          },
        }),
      ),
    );
    expect(() => derive(ambiguous)).toThrow(/publication|producer/iu);

    const changedIdentity = materializationGraph();
    const extractionJob = changedIdentity.jobs.get("job_extract")!;
    const changedStandaloneIdentity = {
      ...((((extractionJob.record.result as unknown as Record<string, unknown>).publication as Record<string, unknown>)
        .standalone_game as object)),
      content_hash: hashFor("different_standalone"),
    };
    changedIdentity.jobs.set(
      extractionJob.job_id,
      view(
        rawJob(extractionJob.operation, {
          ...extractionJob.record,
          result: {
            ...extractionJob.record.result!,
            publication: {
              ...((extractionJob.record.result as unknown as Record<string, unknown>).publication as object),
              standalone_game: changedStandaloneIdentity,
            },
          },
        }),
      ),
    );
    const extractedGrantIndex = changedIdentity.grants.findIndex(
      (grant) => grant.grant_id === "grant_extracted",
    );
    changedIdentity.grants[extractedGrantIndex] = {
      ...changedIdentity.grants[extractedGrantIndex],
      publication: changedStandaloneIdentity as StudioCreationOutputGrant["publication"],
    };
    expect(() => derive(changedIdentity)).toThrow(/standalone identity|preserv/iu);

    const changedId = materializationGraph();
    const changedIdJob = changedId.jobs.get("job_extract")!;
    const changedStandaloneId = {
      ...((((changedIdJob.record.result as unknown as Record<string, unknown>)
        .publication as Record<string, unknown>).standalone_game as object)),
      id: "standalone_changed_id",
    };
    changedId.jobs.set(
      changedIdJob.job_id,
      view(
        rawJob(changedIdJob.operation, {
          ...changedIdJob.record,
          result: {
            ...changedIdJob.record.result!,
            publication: {
              ...((changedIdJob.record.result as unknown as Record<string, unknown>)
                .publication as object),
              standalone_game: changedStandaloneId,
            },
          },
        }),
      ),
    );
    const changedIdGrantIndex = changedId.grants.findIndex(
      (grant) => grant.grant_id === "grant_extracted",
    );
    changedId.grants[changedIdGrantIndex] = {
      ...changedId.grants[changedIdGrantIndex],
      publication: changedStandaloneId as StudioCreationOutputGrant["publication"],
    };
    expect(() => derive(changedId)).toThrow(/standalone identity|preserv/iu);
  });

  it("matches only byte-equivalent in-flight parameters at the reserved next generation", async () => {
    const graph = materializationGraph();
    const candidates = derive(graph);
    const target = readyGrant(
      "grant_materialization_target",
      3,
      "game_materialization_bundle_directory",
    );
    const submission = materializationBundleBuildSubmission(
      graph.census,
      candidates.runtimeBundleCandidates[0],
      target,
    );
    const pending = rawJob("game.materialization.bundle.build", {
      format_version: 6,
      job_id: "job_pending_materialization",
      state: "queued",
      progress: "queued",
      operation_params: {
        runtime_bundle_artifact_id: submission.runtimeBundleArtifactId,
        source_grant_id: submission.sourceGrantId,
        source_grant_generation: submission.expectedSourceGrantGeneration,
        target_grant_id: submission.targetGrantId,
        target_grant_generation: 1,
      },
    });
    const api = {
      listCreationJobs: vi.fn().mockImplementation(({ state }: { state: string | null }) =>
        Promise.resolve(
          v4("creation_job.list", {
            jobs: state === "queued" ? [pending] : [],
            next_sequence: null,
          }),
        ),
      ),
    } as unknown as ForgeStudioApi;

    await expect(
      findIdenticalPendingMaterializationJob(
        api,
        graph.census.authority,
        "game.materialization.bundle.build",
        submission,
      ),
    ).resolves.toMatchObject({ job_id: "job_pending_materialization" });
    await expect(
      findIdenticalPendingMaterializationJob(
        api,
        graph.census.authority,
        "game.materialization.bundle.build",
        { ...submission, sourceGrantId: "grant_other" },
      ),
    ).resolves.toBeNull();
  });

  it("reconstructs one reserved target and blocks ambiguous durable bindings", async () => {
    const graph = materializationGraph();
    graph.grants.push(
      readyGrant(
        "grant_materialization_target",
        3,
        "game_materialization_bundle_directory",
        "reserved",
        1,
      ),
    );
    const queued = rawJob("game.materialization.bundle.build", {
      format_version: 6,
      job_id: "job_pending_materialization",
      state: "queued",
      progress: "queued",
      operation_params: {
        runtime_bundle_artifact_id: "artifact_runtime_bundle",
        source_grant_id: "grant_runtime",
        source_grant_generation: 2,
        target_grant_id: "grant_materialization_target",
        target_grant_generation: 1,
      },
    });
    const api = graphApi(graph, [queued]);
    const loaded = await loadCreationMaterializationPipelineCandidates(
      api,
      graph.census,
      graph.grants,
    );
    expect(loaded.pendingJobs).toEqual([
      expect.objectContaining({ job_id: "job_pending_materialization" }),
    ]);
    expect(loaded.boundGrantJobIds).toEqual(
      new Map([["grant_materialization_target", "job_pending_materialization"]]),
    );

    const duplicateApi = graphApi(graph, [
      queued,
      { ...queued, job_id: "job_pending_duplicate", record_hash: hashFor("duplicate") },
    ]);
    await expect(
      loadCreationMaterializationPipelineCandidates(
        duplicateApi,
        graph.census,
        graph.grants,
      ),
    ).rejects.toThrow(/ambiguous|binding/iu);
  });

  it("retains cleanup and orphan recovery blockers with an exact recovery grant binding", async () => {
    const graph = materializationGraph();
    graph.grants.push(
      readyGrant(
        "grant_materialization_recovery",
        3,
        "game_materialization_bundle_directory",
        "recovery_required",
        2,
      ),
    );
    const orphaned = rawJob("game.materialization.bundle.build", {
      format_version: 6,
      job_id: "job_orphaned_materialization",
      state: "orphaned",
      progress: "orphaned",
      operation_params: {
        runtime_bundle_artifact_id: "artifact_runtime_bundle",
        source_grant_id: "grant_runtime",
        source_grant_generation: 2,
        target_grant_id: "grant_materialization_recovery",
        target_grant_generation: 1,
      },
    });
    const cleanup = rawJob("game.package", {
      format_version: 8,
      job_id: "job_package_cleanup",
      state: "succeeded",
      progress: "cleanup_pending",
      result: {
        output_artifact_ids: ["artifact_package"],
        artifact_snapshot_hash: SNAPSHOT,
        analysis_status: "passed",
        reason_codes: [],
        cleanup_pending: true,
      },
    });

    const loaded = await loadCreationMaterializationPipelineCandidates(
      graphApi(graph, [orphaned, cleanup]),
      graph.census,
      graph.grants,
    );

    expect(loaded.pendingJobs).toEqual([]);
    expect(loaded.blockingReasonCodes).toEqual([
      "cleanup_pending",
      "recovery_required",
    ]);
    expect(loaded.boundGrantJobIds).toEqual(
      new Map([
        ["grant_materialization_recovery", "job_orphaned_materialization"],
      ]),
    );
  });
});

function derive(graph: ReturnType<typeof materializationGraph>) {
  return deriveMaterializationPipelineCandidates(
    graph.census,
    graph.inspections,
    graph.jobs,
    graph.grants,
  );
}

function materializationGraph() {
  const gamepack = artifact("artifact_gamepack", "world-forge.gamepack", [], undefined, "active");
  const inventory = artifact("artifact_inventory", "world-forge.asset_inventory", [], undefined, "active");
  const assetpack = artifact("artifact_assetpack", "world-forge.assetpack", [], undefined);
  const snapshot = artifact("artifact_runtime_snapshot", "world-forge.game_runtime_snapshot", [], undefined);
  const registry = artifact("artifact_runtime_registry", "world-forge.runtime_adapter_registry", [], undefined);
  const composition = artifact("artifact_runtime_composition", "world-forge.game_runtime_composition", [], undefined);
  const support = artifact("artifact_runtime_support", "world-forge.runtime_support_report", [], undefined);
  const manifest = artifact("artifact_asset_manifest", "world-forge.asset_manifest", [], undefined);
  const runtime = artifact(
    "artifact_runtime_bundle",
    "world-forge.game_runtime_bundle",
    [gamepack, snapshot, registry, composition, support, manifest],
    "job_runtime_bundle",
  );
  const materialization = artifact(
    "artifact_materialization",
    "world-forge.game_materialization_bundle",
    [runtime],
    "job_materialization",
  );
  const standalone = artifact(
    "artifact_standalone",
    "world-forge.standalone_game",
    [materialization],
    "job_materialize",
  );
  const gamePackage = artifact(
    "artifact_package",
    "world-forge.game_package",
    [],
    "job_package",
  );
  const extraction = artifact(
    "artifact_extraction",
    "world-forge.game_package_extraction",
    [gamePackage],
    "job_extract",
  );
  const activeArtifacts = [gamepack, inventory];
  const candidateArtifacts = [
    assetpack,
    snapshot,
    registry,
    composition,
    support,
    manifest,
    runtime,
    materialization,
    standalone,
    gamePackage,
    extraction,
  ];
  const selectableArtifacts = [...activeArtifacts, ...candidateArtifacts];
  const census: CreationExecutionCensus = {
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
  const inspections = new Map<string, StudioCreationArtifactInspectResult>();
  for (const candidate of candidateArtifacts) {
    inspections.set(candidate.artifact_id, inspection(candidate, census));
  }
  const grants: StudioCreationOutputGrant[] = [
    publishedGrant("grant_runtime", 2, "game_runtime_bundle_directory", runtime, {
      tree_hash: hashFor("runtime_tree"),
    }),
    publishedGrant(
      "grant_materialization",
      3,
      "game_materialization_bundle_directory",
      materialization,
      { tree_hash: hashFor("materialization_tree") },
    ),
    publishedGrant("grant_standalone", 4, "standalone_game_directory", standalone, {
      tree_hash: hashFor("standalone_tree"),
    }),
    publishedGrant("grant_package", 5, "game_package_file", gamePackage, {
      archive_sha256: hashFor("package_archive"),
      size_bytes: 4096,
    }),
    {
      ...publishedGrant(
        "grant_extracted",
        4,
        "standalone_game_directory",
        standalone,
        { tree_hash: hashFor("standalone_tree") },
      ),
      generation: 2,
    },
    publishedGrant(
      "grant_assetpack",
      1,
      "generic_assetpack_directory",
      assetpack,
      { inventory_hash: inventory.subject.content_hash },
    ),
  ];
  const jobs = new Map<string, CreationJobView>([
    [
      "job_runtime_bundle",
      view(
        succeededJob(
          "runtime.bundle.build",
          5,
          "job_runtime_bundle",
          [gamepack, inventory, assetpack, snapshot, registry, composition, support],
          {
            gamepack_artifact_id: gamepack.artifact_id,
            asset_inventory_artifact_id: inventory.artifact_id,
            assetpack_artifact_id: assetpack.artifact_id,
            runtime_snapshot_artifact_id: snapshot.artifact_id,
            runtime_adapter_registry_artifact_id: registry.artifact_id,
            runtime_composition_artifact_id: composition.artifact_id,
            runtime_support_report_artifact_id: support.artifact_id,
            source_grant_id: "grant_assetpack",
            source_grant_generation: 2,
            target_grant_id: "grant_runtime",
            target_grant_generation: 2,
          },
          runtime,
          publication("grant_runtime", 2, "game_runtime_bundle_directory", "runtime_bundle", grants[0].publication!),
        ),
      ),
    ],
    [
      "job_materialization",
      view(
        succeededJob(
          "game.materialization.bundle.build",
          6,
          "job_materialization",
          [runtime],
          {
            runtime_bundle_artifact_id: runtime.artifact_id,
            source_grant_id: "grant_runtime",
            source_grant_generation: 2,
            target_grant_id: "grant_materialization",
            target_grant_generation: 2,
          },
          materialization,
          publication("grant_materialization", 2, "game_materialization_bundle_directory", "materialization_bundle", grants[1].publication!),
        ),
      ),
    ],
    [
      "job_materialize",
      view(
        succeededJob(
          "game.materialize",
          7,
          "job_materialize",
          [materialization],
          {
            materialization_bundle_artifact_id: materialization.artifact_id,
            source_grant_id: "grant_materialization",
            source_grant_generation: 2,
            target_grant_id: "grant_standalone",
            target_grant_generation: 2,
          },
          standalone,
          publication("grant_standalone", 2, "standalone_game_directory", "standalone_game", grants[2].publication!),
        ),
      ),
    ],
    [
      "job_package",
      view(
        succeededJob(
          "game.package",
          8,
          "job_package",
          [standalone],
          {
            standalone_game_artifact_id: standalone.artifact_id,
            source_grant_id: "grant_standalone",
            source_grant_generation: 2,
            target_grant_id: "grant_package",
            target_grant_generation: 2,
          },
          gamePackage,
          publication("grant_package", 2, "game_package_file", "game_package", grants[3].publication!),
        ),
      ),
    ],
    [
      "job_extract",
      view(
        succeededJob(
          "game.package.extract",
          9,
          "job_extract",
          [gamePackage],
          {
            game_package_artifact_id: gamePackage.artifact_id,
            source_grant_id: "grant_package",
            source_grant_generation: 2,
            target_grant_id: "grant_extracted",
            target_grant_generation: 2,
          },
          extraction,
          publication("grant_extracted", 2, "standalone_game_directory", "standalone_game", grants[4].publication!),
        ),
      ),
    ],
  ]);
  return { census, inspections, jobs, grants };
}

function artifact(
  artifactId: string,
  format: string,
  dependencies: readonly StudioCreationArtifact[],
  producerJobId?: string,
  lifecycle: "active" | "candidate" = "candidate",
): StudioCreationArtifact {
  return {
    format: "world-forge.studio_creation_artifact",
    format_version: 1,
    artifact_id: artifactId,
    subject: {
      format,
      format_version: 1,
      id: `${format.split(".").at(-1)}_${artifactId}`,
      content_hash: hashFor(artifactId),
    },
    lifecycle,
    roles: ["materialization_test"],
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
  const dependencies: Record<string, string[]> = {
    artifact_runtime_bundle: [
      "artifact_gamepack",
      "artifact_runtime_snapshot",
      "artifact_runtime_registry",
      "artifact_runtime_composition",
      "artifact_runtime_support",
      "artifact_asset_manifest",
    ],
    artifact_materialization: ["artifact_runtime_bundle"],
    artifact_standalone: ["artifact_materialization"],
    artifact_package: [],
    artifact_extraction: ["artifact_package"],
  };
  return {
    authority: publicAuthority(),
    artifact_snapshot_hash: SNAPSHOT,
    artifact: item,
    projection: {
      projection_kind: "materialization_test",
      title: item.subject.id,
      status: null,
      facts: [],
      lineage: (dependencies[item.artifact_id] ?? []).map((artifactId) => ({
        relation: "depends_on",
        artifact_id: artifactId,
        lifecycle: census.selectableById.get(artifactId)!.lifecycle,
      })),
    },
  };
}

function succeededJob(
  operation: string,
  version: number,
  jobId: string,
  inputs: readonly StudioCreationArtifact[],
  params: Record<string, unknown>,
  output: StudioCreationArtifact,
  resultPublication: Record<string, unknown>,
) {
  return rawJob(operation, {
    format_version: version,
    job_id: jobId,
    state: "succeeded",
    progress: "committed",
    operation_params: params,
    inputs: inputs.map((item) => ({ artifact_id: item.artifact_id, subject: item.subject })),
    result: {
      output_artifact_ids: [output.artifact_id],
      artifact_snapshot_hash: SNAPSHOT,
      analysis_status: "passed",
      reason_codes: [],
      cleanup_pending: false,
      publication: resultPublication,
    },
  });
}

function rawJob(operation: string, overrides: Record<string, unknown> = {}) {
  const jobId = typeof overrides.job_id === "string" ? overrides.job_id : "job_default";
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
    record_hash: hashFor(jobId),
    ...overrides,
  };
}

function view(value: Record<string, unknown>): CreationJobView {
  const projected = projectCreationJob(value, "creation_workspace");
  if (projected === null) throw new Error("invalid materialization job fixture");
  return projected;
}

function publishedGrant(
  grantId: string,
  formatVersion: 1 | 2 | 3 | 4 | 5,
  kind: StudioCreationOutputGrant["kind"],
  artifactValue: StudioCreationArtifact,
  extra: Record<string, unknown>,
): StudioCreationOutputGrant {
  return {
    format: "world-forge.studio_creation_output_grant",
    format_version: formatVersion,
    grant_id: grantId,
    workspace_id: "creation_workspace",
    kind,
    display_name: `${kind} output`,
    state: "published",
    generation: 2,
    publication: {
      ...artifactValue.subject,
      ...extra,
    } as StudioCreationOutputGrant["publication"],
    created_at: "2026-08-05T00:00:00Z",
    updated_at: "2026-08-05T00:00:00Z",
  };
}

function readyGrant(
  grantId: string,
  formatVersion: 3 | 4 | 5,
  kind: StudioCreationOutputGrant["kind"],
  state: StudioCreationOutputGrant["state"] = "ready",
  generation = 0,
): StudioCreationOutputGrant {
  return {
    format: "world-forge.studio_creation_output_grant",
    format_version: formatVersion,
    grant_id: grantId,
    workspace_id: "creation_workspace",
    kind,
    display_name: `${kind} destination`,
    state,
    generation,
    publication: null,
    created_at: "2026-08-05T00:00:00Z",
    updated_at: "2026-08-05T00:00:00Z",
  };
}

function publication(
  grantId: string,
  generation: number,
  kind: StudioCreationOutputGrant["kind"],
  identityKey: string,
  identity: NonNullable<StudioCreationOutputGrant["publication"]>,
): Record<string, unknown> {
  return {
    grant_id: grantId,
    grant_generation: generation,
    kind,
    state: "published",
    [identityKey]: identity,
  };
}

function graphApi(
  graph: ReturnType<typeof materializationGraph>,
  durableJobs: readonly Record<string, unknown>[],
): ForgeStudioApi {
  return {
    inspectCreationArtifact: vi.fn().mockImplementation(({ artifactId }: { artifactId: string }) =>
      Promise.resolve(v4("creation_artifact.inspect", graph.inspections.get(artifactId)!)),
    ),
    getCreationJob: vi.fn().mockImplementation((jobId: string) =>
      Promise.resolve(v4("creation_job.get", { job: graph.jobs.get(jobId)!.record })),
    ),
    listCreationJobs: vi.fn().mockImplementation(({ state }: { state: string | null }) =>
      Promise.resolve(
        v4("creation_job.list", {
          jobs: state === null ? durableJobs : [],
          next_sequence: null,
        }),
      ),
    ),
  } as unknown as ForgeStudioApi;
}

function replaceArtifact(census: CreationExecutionCensus, replacement: StudioCreationArtifact) {
  const replace = (items: StudioCreationArtifact[]) => {
    const index = items.findIndex((item) => item.artifact_id === replacement.artifact_id);
    if (index >= 0) items[index] = replacement;
  };
  replace(census.activeArtifacts);
  replace(census.candidateArtifacts);
  replace(census.selectableArtifacts);
  (census.selectableById as Map<string, StudioCreationArtifact>).set(
    replacement.artifact_id,
    replacement,
  );
}

function operationParams(job: CreationJobView): Record<string, unknown> {
  return job.record.operation_params as unknown as Record<string, unknown>;
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

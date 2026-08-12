import { describe, expect, it, vi } from "vitest";

import {
  assertAssetOutputIdsAvailable,
  canonicalAssetOutputIds,
  canonicalAssetReleaseManifestId,
  canonicalizeArtifactDependencies,
  deriveAssetPipelineCandidates,
  loadAssetPipelineBlockingState,
  normalizeAcceptanceResults,
  parseCreationAdmissionDocument,
} from "../../src/renderer/creation-asset-pipeline-state";
import type {
  ForgeStudioApi,
  StudioCreationArtifact,
  StudioCreationArtifactInspectResult,
} from "../../src/shared/studio-api";
import type { CreationExecutionCensus } from "../../src/renderer/creation-execution-state";

const SOURCE = "a".repeat(64);
const SNAPSHOT = "b".repeat(64);

describe("creation asset pipeline state", () => {
  it("strictly parses bounded admission objects and rejects duplicate keys, overflow, and non-object roots", () => {
    expect(parseCreationAdmissionDocument('{"format":"world-forge.asset_style","format_version":1}')).toEqual({
      format: "world-forge.asset_style",
      format_version: 1,
    });
    expect(() => parseCreationAdmissionDocument('{"id":"first","id":"second"}')).toThrow(
      /duplicate object key/u,
    );
    expect(() => parseCreationAdmissionDocument('{"weight":1e400}')).toThrow(/finite/u);
    expect(() => parseCreationAdmissionDocument("[]")).toThrow(/object root/u);
    expect(() => parseCreationAdmissionDocument(`{"payload":"${"x".repeat(768 * 1024)}"}`)).toThrow(
      /786432-byte limit/u,
    );
  });

  it("canonicalizes only unique dependencies from the current active/candidate census", () => {
    const active = artifact("artifact_active", "world-forge.gamepack", "active");
    const candidate = artifact("artifact_candidate", "world-forge.asset_style", "candidate");
    const census = censusWith([active], [candidate]);

    expect(
      canonicalizeArtifactDependencies(
        [candidate.artifact_id, active.artifact_id],
        census,
      ),
    ).toEqual([active.artifact_id, candidate.artifact_id]);
    expect(() =>
      canonicalizeArtifactDependencies([active.artifact_id, active.artifact_id], census),
    ).toThrow(/duplicate/u);
    expect(() => canonicalizeArtifactDependencies(["artifact_historical"], census)).toThrow(
      /current active or candidate/u,
    );
  });

  it("derives canonical recipe, receipt, QA, and manifest identifiers without free-typed artifact IDs", () => {
    expect(canonicalAssetOutputIds("board_ui", "reviewed")).toEqual({
      recipeId: "board_ui_reviewed_recipe",
      processingReceiptId: "board_ui_reviewed_processing_receipt",
      qaReportId: "board_ui_reviewed_qa_report",
    });
    expect(canonicalAssetOutputIds("board_ui", "")).toEqual({
      recipeId: "board_ui_studio_recipe",
      processingReceiptId: "board_ui_studio_processing_receipt",
      qaReportId: "board_ui_studio_qa_report",
    });
    expect(() => canonicalAssetOutputIds("board_ui", "Unsafe Suffix")).toThrow(/suffix/u);
    const ids = canonicalAssetOutputIds("board_ui", "reviewed");
    expect(() =>
      assertAssetOutputIdsAvailable(
        ids,
        censusWith(
          [
            artifact("artifact_recipe_collision", "world-forge.asset_processing_recipe", "active", {
              id: ids.recipeId,
            }),
          ],
          [],
        ),
      ),
    ).toThrow(/already exists.*new suffix/iu);

    const qa = artifact("artifact_qa", "world-forge.asset_qa_report", "candidate", {
      id: "board_ui_qa",
      contentHash: "c".repeat(64),
    });
    expect(canonicalAssetReleaseManifestId("puzzle_project", [qa])).toBe(
      "puzzle_project_assetpack_1_cccccccccccccccc_cccccccccccccccc",
    );
  });

  it("normalizes positional acceptance rows and rejects repeated or malformed hashes", () => {
    const first = "1".repeat(64);
    const second = "2".repeat(64);
    const evidenceA = "a".repeat(64);
    const evidenceB = "b".repeat(64);
    expect(
      normalizeAcceptanceResults([
        {
          criterionSha256: first,
          status: "passed",
          evidenceHashes: `${evidenceB}\n${evidenceA}`,
        },
        {
          criterionSha256: second,
          status: "failed",
          evidenceHashes: evidenceA,
        },
      ]),
    ).toEqual([
      {
        criterionIndex: 0,
        criterionSha256: first,
        status: "passed",
        evidenceHashes: [evidenceA, evidenceB],
      },
      {
        criterionIndex: 1,
        criterionSha256: second,
        status: "failed",
        evidenceHashes: [evidenceA],
      },
    ]);
    expect(() =>
      normalizeAcceptanceResults([
        { criterionSha256: first, status: "passed", evidenceHashes: evidenceA },
        { criterionSha256: first, status: "passed", evidenceHashes: evidenceB },
      ]),
    ).toThrow(/criterion hashes repeat/u);
    expect(() =>
      normalizeAcceptanceResults([
        { criterionSha256: first, status: "passed", evidenceHashes: `${evidenceA} ${evidenceA}` },
      ]),
    ).toThrow(/evidence hashes repeat/u);
  });

  it("accepts exactly 64 asset criteria and evidence hashes and rejects 65", () => {
    const rows = (count: number, evidenceCount = 1) =>
      Array.from({ length: count }, (_, index) => ({
        criterionSha256: (index + 1).toString(16).padStart(64, "0"),
        status: "passed" as const,
        evidenceHashes: Array.from({ length: evidenceCount }, (_unused, evidenceIndex) =>
          (evidenceIndex + 1).toString(16).padStart(64, "0"),
        ).join("\n"),
      }));

    expect(normalizeAcceptanceResults(rows(64))).toHaveLength(64);
    expect(() => normalizeAcceptanceResults(rows(65))).toThrow(/between 1 and 64/u);
    expect(normalizeAcceptanceResults(rows(1, 64))[0].evidenceHashes).toHaveLength(64);
    expect(() => normalizeAcceptanceResults(rows(1, 65))).toThrow(/between 1 and 64/u);
  });

  it("builds exact process and full-inventory release groups from current same-lineage evidence", () => {
    const graph = assetGraph();
    const result = deriveAssetPipelineCandidates(graph.census, graph.inspections);

    expect(result.processingGroups).toHaveLength(1);
    expect(result.processingGroups[0]).toMatchObject({
      assetId: "board_ui",
      licenseArtifactIds: ["artifact_license"],
      lifecycle: "candidate",
    });
    expect(result.releaseGroups).toHaveLength(1);
    expect(result.releaseGroups[0]).toMatchObject({
      inventoryAssetCount: 1,
      qaReportArtifactIds: ["artifact_qa"],
    });
  });

  it("requires process and release closures to contain their exact starting artifact kinds", () => {
    const graph = assetGraph();
    const recipe = graph.inspections.get("artifact_recipe")!;
    graph.inspections.set("artifact_recipe", {
      ...recipe,
      projection: {
        ...recipe.projection,
        lineage: [
          {
            relation: "depends_on",
            artifact_id: "artifact_provenance",
            lifecycle: "candidate",
          },
        ],
      },
    });

    expect(() => deriveAssetPipelineCandidates(graph.census, graph.inspections)).toThrow(
      /license|lineage/u,
    );
  });

  it("accepts complete multi-output license coverage for one release asset", () => {
    const graph = assetGraph();
    const secondLicense = artifact(
      "artifact_license_secondary",
      "world-forge.asset_license_record",
      "candidate",
      { dependencyCount: 1 },
    );
    graph.census.candidateArtifacts.push(secondLicense);
    graph.census.selectableArtifacts.push(secondLicense);
    (graph.census.selectableById as Map<string, StudioCreationArtifact>).set(
      secondLicense.artifact_id,
      secondLicense,
    );
    graph.inspections.set(
      secondLicense.artifact_id,
      inspection(
        secondLicense,
        ["artifact_provenance"],
        {
          asset_id: "board_ui",
          candidate_artifact_id: "board_ui_candidate_secondary",
          candidate_role: "overlay",
          commercial_use: true,
          redistribution: true,
        },
        null,
        graph.census,
      ),
    );

    const recipe = graph.census.selectableById.get("artifact_recipe")!;
    const recipeDependencies = graph.inspections
      .get("artifact_recipe")!
      .projection.lineage.map((edge) => edge.artifact_id);
    const expandedRecipe = {
      ...recipe,
      references: { ...recipe.references, dependency_count: recipeDependencies.length + 1 },
    };
    replaceArtifact(graph.census, expandedRecipe);
    graph.inspections.set(
      expandedRecipe.artifact_id,
      inspection(
        expandedRecipe,
        [...recipeDependencies, secondLicense.artifact_id],
        {},
        null,
        graph.census,
      ),
    );
    for (const artifactId of ["artifact_receipt", "artifact_processing_receipt"]) {
      const receipt = graph.inspections.get(artifactId)!;
      graph.inspections.set(artifactId, {
        ...receipt,
        projection: {
          ...receipt.projection,
          facts: receipt.projection.facts.map((fact) =>
            fact.key === "output_count" ? { ...fact, value: 2 } : fact,
          ),
        },
      });
    }
    const selection = graph.inspections.get("artifact_selection")!;
    graph.inspections.set("artifact_selection", {
      ...selection,
      projection: {
        ...selection.projection,
        facts: selection.projection.facts.map((fact) =>
          fact.key === "selected_output_bindings"
            ? {
                ...fact,
                value: ["board_ui_candidate:texture", "board_ui_candidate_secondary:overlay"],
              }
            : fact,
        ),
      },
    });

    const result = deriveAssetPipelineCandidates(graph.census, graph.inspections);
    expect(result.processingGroups[0].licenseArtifactIds).toEqual([
      "artifact_license",
      "artifact_license_secondary",
    ]);
    expect(result.releaseGroups).toHaveLength(1);
  });

  it("rejects duplicate and crossed license bindings instead of trusting counts", () => {
    const crossed = assetGraph();
    const crossedLicense = crossed.inspections.get("artifact_license")!;
    crossed.inspections.set("artifact_license", {
      ...crossedLicense,
      projection: {
        ...crossedLicense.projection,
        facts: crossedLicense.projection.facts.map((fact) =>
          fact.key === "candidate_role" ? { ...fact, value: "font" } : fact,
        ),
      },
    });
    expect(() => deriveAssetPipelineCandidates(crossed.census, crossed.inspections)).toThrow(
      /binding|coverage|role|license/u,
    );

    const duplicate = assetGraph();
    addSecondaryLicense(duplicate, {
      candidateArtifactId: "board_ui_candidate",
      role: "texture",
      selectedBindings: ["board_ui_candidate:texture", "board_ui_candidate_secondary:overlay"],
    });
    expect(() => deriveAssetPipelineCandidates(duplicate.census, duplicate.inspections)).toThrow(
      /duplicate|coverage|license/u,
    );
  });

  it("rejects extra license bindings and omits incomplete exact selection coverage", () => {
    const extra = assetGraph();
    addSecondaryLicense(extra, {
      candidateArtifactId: "unexpected_candidate",
      role: "overlay",
      selectedBindings: ["board_ui_candidate:texture"],
      outputCount: 1,
    });
    expect(() => deriveAssetPipelineCandidates(extra.census, extra.inspections)).toThrow(
      /extra|coverage|license/u,
    );

    const missing = assetGraph();
    setProjectionFact(missing, "artifact_receipt", "output_count", 2);
    setProjectionFact(missing, "artifact_processing_receipt", "output_count", 2);
    setProjectionFact(missing, "artifact_selection", "selected_output_bindings", [
      "board_ui_candidate:texture",
      "board_ui_candidate_secondary:overlay",
    ]);
    removeArtifacts(missing, ["artifact_recipe", "artifact_processing_receipt", "artifact_qa"]);
    expect(deriveAssetPipelineCandidates(missing.census, missing.inspections).processingGroups).toEqual([]);
  });

  it("rejects release candidates whose license permissions are not release-ready", () => {
    const graph = assetGraph();
    const license = graph.inspections.get("artifact_license")!;
    graph.inspections.set("artifact_license", {
      ...license,
      projection: {
        ...license.projection,
        facts: license.projection.facts.map((fact) =>
          fact.key === "redistribution" ? { ...fact, value: false } : fact,
        ),
      },
    });

    expect(() => deriveAssetPipelineCandidates(graph.census, graph.inspections)).toThrow(
      /license|permission|release/u,
    );
  });

  it("keeps processing available while omitting a failed QA release candidate", () => {
    const graph = assetGraph();
    const qa = graph.inspections.get("artifact_qa")!;
    graph.inspections.set("artifact_qa", {
      ...qa,
      projection: {
        ...qa.projection,
        status: "failed",
        facts: qa.projection.facts.map((fact) =>
          fact.key === "blocker_count" ? { ...fact, value: 1 } : fact,
        ),
      },
    });

    const result = deriveAssetPipelineCandidates(graph.census, graph.inspections);
    expect(result.processingGroups).toHaveLength(1);
    expect(result.releaseGroups).toEqual([]);
  });

  it("omits only release-ready manifests with the exact QA dependency set", () => {
    const sealed = assetGraph();
    addManifest(sealed, "release_ready");
    expect(deriveAssetPipelineCandidates(sealed.census, sealed.inspections).releaseGroups).toEqual(
      [],
    );

    const intermediate = assetGraph();
    addManifest(intermediate, "processed");
    expect(
      deriveAssetPipelineCandidates(intermediate.census, intermediate.inspections).releaseGroups,
    ).toHaveLength(1);
  });

  it("omits an incomplete processing group until every output license is present", () => {
    const graph = assetGraph();
    removeArtifacts(graph, ["artifact_recipe", "artifact_processing_receipt", "artifact_qa"]);
    const receipt = graph.inspections.get("artifact_receipt")!;
    graph.inspections.set("artifact_receipt", {
      ...receipt,
      projection: {
        ...receipt.projection,
        facts: receipt.projection.facts.map((fact) =>
          fact.key === "output_count" ? { ...fact, value: 2 } : fact,
        ),
      },
    });
    setProjectionFact(graph, "artifact_selection", "selected_output_bindings", [
      "board_ui_candidate:texture",
      "board_ui_candidate_secondary:overlay",
    ]);

    expect(deriveAssetPipelineCandidates(graph.census, graph.inspections)).toEqual({
      processingGroups: [],
      qaReviewGroups: [],
      releaseGroups: [],
    });
  });

  it("retains cleanup blockers from an old input snapshot after the result snapshot becomes current", async () => {
    const oldSnapshot = "e".repeat(64);
    const listCreationJobs = vi.fn().mockResolvedValue(
      v4("creation_job.list", {
        jobs: [
          creationJob({
            authority: {
              root_generation: 2,
              source_revision: SOURCE,
              workflow_status_hash: null,
              artifact_snapshot_hash: oldSnapshot,
            },
            state: "succeeded",
            progress: "cleanup_pending",
            result: {
              output_artifact_ids: ["artifact_qa"],
              artifact_snapshot_hash: SNAPSHOT,
              analysis_status: "passed",
              reason_codes: [],
              cleanup_pending: true,
            },
          }),
        ],
        next_sequence: null,
      }),
    );
    const api = { listCreationJobs } as unknown as ForgeStudioApi;

    await expect(loadAssetPipelineBlockingState(api, assetGraph().census.authority)).resolves.toEqual({
      jobIds: ["job_asset"],
      reasonCodes: ["cleanup_pending"],
    });
  });

  it("fails closed on truncated, stale, ambiguous, or mixed asset lineage", () => {
    const truncated = assetGraph();
    truncated.inspections.set("artifact_license", {
      ...truncated.inspections.get("artifact_license")!,
      projection: {
        ...truncated.inspections.get("artifact_license")!.projection,
        lineage: [],
      },
    });
    expect(() => deriveAssetPipelineCandidates(truncated.census, truncated.inspections)).toThrow(
      /truncated|dependency count/u,
    );

    const stale = assetGraph();
    stale.inspections.set("artifact_license", {
      ...stale.inspections.get("artifact_license")!,
      projection: {
        ...stale.inspections.get("artifact_license")!.projection,
        lineage: [
          {
            relation: "depends_on",
            artifact_id: "artifact_provenance",
            lifecycle: "historical",
          },
        ],
      },
    });
    expect(() => deriveAssetPipelineCandidates(stale.census, stale.inspections)).toThrow(
      /stale|lifecycle/u,
    );

    const ambiguous = assetGraph();
    const secondQa = artifact(
      "artifact_qa_second",
      "world-forge.asset_qa_report",
      "candidate",
      { id: "board_ui_qa_second", contentHash: "d".repeat(64), dependencyCount: 1 },
    );
    ambiguous.census.candidateArtifacts.push(secondQa);
    ambiguous.census.selectableArtifacts.push(secondQa);
    (ambiguous.census.selectableById as Map<string, StudioCreationArtifact>).set(
      secondQa.artifact_id,
      secondQa,
    );
    ambiguous.inspections.set(
      secondQa.artifact_id,
      inspection(secondQa, ["artifact_processing_receipt"], {
        asset_id: "board_ui",
        blocker_count: 0,
      }, "passed", ambiguous.census),
    );
    expect(() => deriveAssetPipelineCandidates(ambiguous.census, ambiguous.inspections)).toThrow(
      /ambiguous|inventory/u,
    );

    const mixed = assetGraph();
    const qa = mixed.inspections.get("artifact_qa")!;
    const otherTarget = artifact(
      "artifact_target_other",
      "world-forge.asset_target",
      "candidate",
      { id: "other_target", contentHash: "e".repeat(64) },
    );
    mixed.census.candidateArtifacts.push(otherTarget);
    mixed.census.selectableArtifacts.push(otherTarget);
    (mixed.census.selectableById as Map<string, StudioCreationArtifact>).set(
      otherTarget.artifact_id,
      otherTarget,
    );
    mixed.inspections.set(
      otherTarget.artifact_id,
      inspection(otherTarget, [], {}, null, mixed.census),
    );
    mixed.inspections.set("artifact_processing_receipt", {
      ...mixed.inspections.get("artifact_processing_receipt")!,
      projection: {
        ...mixed.inspections.get("artifact_processing_receipt")!.projection,
        lineage: [
          ...mixed.inspections.get("artifact_processing_receipt")!.projection.lineage,
          {
            relation: "depends_on",
            artifact_id: otherTarget.artifact_id,
            lifecycle: otherTarget.lifecycle,
          },
        ],
      },
      artifact: {
        ...mixed.inspections.get("artifact_processing_receipt")!.artifact,
        references: {
          ...mixed.inspections.get("artifact_processing_receipt")!.artifact.references,
          dependency_count:
            mixed.inspections.get("artifact_processing_receipt")!.artifact.references
              .dependency_count + 1,
        },
      },
    });
    expect(() => deriveAssetPipelineCandidates(mixed.census, mixed.inspections)).toThrow(
      /mixed|target|lineage/u,
    );
    expect(qa.projection.status).toBe("passed");
  });
});

function assetGraph(): {
  census: CreationExecutionCensus;
  inspections: Map<string, StudioCreationArtifactInspectResult>;
} {
  const definitions = [
    ["artifact_gamepack", "world-forge.gamepack", []],
    ["artifact_subject", "world-forge.asset_subject", ["artifact_gamepack"]],
    ["artifact_target", "world-forge.asset_target", ["artifact_gamepack"]],
    ["artifact_style", "world-forge.asset_style", ["artifact_target"]],
    ["artifact_inventory", "world-forge.asset_inventory", ["artifact_subject", "artifact_target", "artifact_style"]],
    ["artifact_spec", "world-forge.asset_spec", ["artifact_inventory"]],
    ["artifact_request", "world-forge.asset_production_request", ["artifact_spec"]],
    ["artifact_receipt", "world-forge.asset_production_receipt", ["artifact_request"]],
    ["artifact_selection", "world-forge.asset_selection", ["artifact_receipt"]],
    ["artifact_provenance", "world-forge.asset_provenance_record", ["artifact_selection"]],
    ["artifact_license", "world-forge.asset_license_record", ["artifact_provenance"]],
    [
      "artifact_recipe",
      "world-forge.asset_processing_recipe",
      [
        "artifact_gamepack",
        "artifact_subject",
        "artifact_target",
        "artifact_style",
        "artifact_inventory",
        "artifact_spec",
        "artifact_request",
        "artifact_receipt",
        "artifact_selection",
        "artifact_provenance",
        "artifact_license",
      ],
    ],
    ["artifact_processing_receipt", "world-forge.asset_processing_receipt", ["artifact_recipe"]],
    ["artifact_qa", "world-forge.asset_qa_report", ["artifact_processing_receipt"]],
  ] as const;
  const artifacts = definitions.map(([id, format, dependencies]) =>
    artifact(id, format, "candidate", {
      id: id.replace("artifact_", ""),
      dependencyCount: dependencies.length,
    }),
  );
  const census = censusWith([], artifacts);
  const inspections = new Map<string, StudioCreationArtifactInspectResult>();
  for (const [id, , dependencies] of definitions) {
    const item = census.selectableById.get(id)!;
    const facts: Record<string, string | number | boolean | string[]> = {};
    let status: string | null = null;
    if (id === "artifact_inventory") facts.asset_count = 1;
    if (id === "artifact_receipt" || id === "artifact_processing_receipt") {
      facts.output_count = 1;
    }
    if (id === "artifact_selection") {
      facts.selected_output_bindings = ["board_ui_candidate:texture"];
    }
    if (id === "artifact_license" || id === "artifact_qa") facts.asset_id = "board_ui";
    if (id === "artifact_license") {
      facts.candidate_artifact_id = "board_ui_candidate";
      facts.candidate_role = "texture";
      facts.commercial_use = true;
      facts.redistribution = true;
    }
    if (id === "artifact_qa") {
      facts.blocker_count = 0;
      status = "passed";
    }
    inspections.set(id, inspection(item, [...dependencies], facts, status, census));
  }
  return { census, inspections };
}

function replaceArtifact(
  census: CreationExecutionCensus,
  replacement: StudioCreationArtifact,
): void {
  for (const collection of [census.activeArtifacts, census.candidateArtifacts, census.selectableArtifacts]) {
    const index = collection.findIndex((item) => item.artifact_id === replacement.artifact_id);
    if (index >= 0) collection[index] = replacement;
  }
  (census.selectableById as Map<string, StudioCreationArtifact>).set(
    replacement.artifact_id,
    replacement,
  );
}

function addSecondaryLicense(
  graph: {
    census: CreationExecutionCensus;
    inspections: Map<string, StudioCreationArtifactInspectResult>;
  },
  options: {
    candidateArtifactId: string;
    role: string;
    selectedBindings: string[];
    outputCount?: number;
  },
): void {
  const secondLicense = artifact(
    "artifact_license_secondary",
    "world-forge.asset_license_record",
    "candidate",
    { dependencyCount: 1 },
  );
  graph.census.candidateArtifacts.push(secondLicense);
  graph.census.selectableArtifacts.push(secondLicense);
  (graph.census.selectableById as Map<string, StudioCreationArtifact>).set(
    secondLicense.artifact_id,
    secondLicense,
  );
  graph.inspections.set(
    secondLicense.artifact_id,
    inspection(
      secondLicense,
      ["artifact_provenance"],
      {
        asset_id: "board_ui",
        candidate_artifact_id: options.candidateArtifactId,
        candidate_role: options.role,
        commercial_use: true,
        redistribution: true,
      },
      null,
      graph.census,
    ),
  );
  const recipe = graph.census.selectableById.get("artifact_recipe")!;
  const recipeDependencies = graph.inspections
    .get("artifact_recipe")!
    .projection.lineage.map((edge) => edge.artifact_id);
  const expandedRecipe = {
    ...recipe,
    references: { ...recipe.references, dependency_count: recipeDependencies.length + 1 },
  };
  replaceArtifact(graph.census, expandedRecipe);
  graph.inspections.set(
    expandedRecipe.artifact_id,
    inspection(
      expandedRecipe,
      [...recipeDependencies, secondLicense.artifact_id],
      {},
      null,
      graph.census,
    ),
  );
  const outputCount = options.outputCount ?? 2;
  setProjectionFact(graph, "artifact_receipt", "output_count", outputCount);
  setProjectionFact(graph, "artifact_processing_receipt", "output_count", outputCount);
  setProjectionFact(
    graph,
    "artifact_selection",
    "selected_output_bindings",
    options.selectedBindings,
  );
}

function addManifest(
  graph: {
    census: CreationExecutionCensus;
    inspections: Map<string, StudioCreationArtifactInspectResult>;
  },
  status: string,
): void {
  const manifest = artifact(
    "artifact_manifest",
    "world-forge.asset_manifest",
    "candidate",
    { dependencyCount: 1 },
  );
  graph.census.candidateArtifacts.push(manifest);
  graph.census.selectableArtifacts.push(manifest);
  (graph.census.selectableById as Map<string, StudioCreationArtifact>).set(
    manifest.artifact_id,
    manifest,
  );
  graph.inspections.set(
    manifest.artifact_id,
    inspection(manifest, ["artifact_qa"], {}, status, graph.census),
  );
}

function setProjectionFact(
  graph: {
    inspections: Map<string, StudioCreationArtifactInspectResult>;
  },
  artifactId: string,
  key: string,
  value: string | number | boolean | string[],
): void {
  const current = graph.inspections.get(artifactId)!;
  const found = current.projection.facts.some((fact) => fact.key === key);
  graph.inspections.set(artifactId, {
    ...current,
    projection: {
      ...current.projection,
      facts: found
        ? current.projection.facts.map((fact) =>
            fact.key === key ? { ...fact, value } : fact,
          )
        : [...current.projection.facts, { key, value }],
    },
  });
}

function removeArtifacts(
  graph: {
    census: CreationExecutionCensus;
    inspections: Map<string, StudioCreationArtifactInspectResult>;
  },
  artifactIds: readonly string[],
): void {
  const removed = new Set(artifactIds);
  for (const collection of [
    graph.census.activeArtifacts,
    graph.census.candidateArtifacts,
    graph.census.selectableArtifacts,
  ]) {
    for (let index = collection.length - 1; index >= 0; index -= 1) {
      if (removed.has(collection[index].artifact_id)) collection.splice(index, 1);
    }
  }
  for (const artifactId of artifactIds) {
    (graph.census.selectableById as Map<string, StudioCreationArtifact>).delete(artifactId);
    graph.inspections.delete(artifactId);
  }
}

function censusWith(
  activeArtifacts: StudioCreationArtifact[],
  candidateArtifacts: StudioCreationArtifact[],
): CreationExecutionCensus {
  const selectableArtifacts = [...activeArtifacts, ...candidateArtifacts];
  return {
    authority: {
      workspaceId: "workspace_01",
      rootGeneration: 2,
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

function artifact(
  artifactId: string,
  format: string,
  lifecycle: "active" | "candidate",
  overrides: { id?: string; contentHash?: string; dependencyCount?: number } = {},
): StudioCreationArtifact {
  return {
    format: "world-forge.studio_creation_artifact",
    format_version: 1,
    artifact_id: artifactId,
    subject: {
      format,
      format_version: 1,
      id: overrides.id ?? artifactId.replace("artifact_", ""),
      content_hash: overrides.contentHash ?? "c".repeat(64),
    },
    lifecycle,
    roles: ["asset_lineage"],
    producer: {
      kind: "future_candidate",
      phase_id: null,
      reference_id: "job_asset",
    },
    references: {
      dependency_count: overrides.dependencyCount ?? 0,
      dependent_count: 0,
    },
    authority: {
      workspace_id: "workspace_01",
      root_generation: 2,
      source_revision: SOURCE,
      workflow_status_hash: null,
    },
    record_hash: "d".repeat(64),
  };
}

function inspection(
  item: StudioCreationArtifact,
  dependencies: string[],
  facts: Record<string, string | number | boolean | string[]>,
  status: string | null,
  census: CreationExecutionCensus,
): StudioCreationArtifactInspectResult {
  return {
    authority: item.authority,
    artifact_snapshot_hash: census.authority.artifactSnapshotHash,
    artifact: item,
    projection: {
      projection_kind: item.roles[0],
      title: item.subject.id,
      status,
      facts: Object.entries(facts).map(([key, value]) => ({ key, value })),
      lineage: dependencies.map((artifactId) => ({
        relation: "depends_on",
        artifact_id: artifactId,
        lifecycle: census.selectableById.get(artifactId)!.lifecycle,
      })),
    },
  };
}

function creationJob(overrides: Record<string, unknown> = {}) {
  return {
    format: "world-forge.studio_creation_job",
    format_version: 2,
    job_id: "job_asset",
    workspace_id: "workspace_01",
    operation: "asset.process",
    state: "queued",
    generation: 1,
    authority: {
      root_generation: 2,
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
    record_hash: "f".repeat(64),
    ...overrides,
  };
}

function v4(method: string, result: Record<string, unknown>) {
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

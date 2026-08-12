import { describe, expect, it } from "vitest";

import {
  CREATION_PREVIEW_CHUNK_BYTES,
  applyCreationPreviewChunk,
  creationPreviewCandidateKey,
  decodeCreationPreviewClose,
  decodeCreationPreviewOpen,
  deriveCreationPreviewCatalog,
  initialCreationPreviewStream,
  type CreationPreviewCandidate,
} from "../../src/renderer/creation-preview-state";
import type {
  StudioCreationArtifact,
  StudioCreationArtifactInspectResult,
  StudioCreationJob,
  StudioCreationOutputGrant,
} from "../../src/shared/studio-api";
import type { CreationExecutionCensus } from "../../src/renderer/creation-execution-state";

const SOURCE = "a".repeat(64);
const SNAPSHOT = "b".repeat(64);
const ASSETPACK_HASH = "c".repeat(64);
const INVENTORY_HASH = "d".repeat(64);
const FILE_HASH = "e".repeat(64);
const HANDLE = "H".repeat(43);

describe("Studio v4 creation preview state", () => {
  it("derives only exact sealed PNG/WAV asset IDs and lists unsupported selected outputs", () => {
    const graph = previewGraph([
      { assetId: "board_ui", candidateId: "board_png", role: "texture" },
      { assetId: "choice_audio", candidateId: "choice_wav", role: "audio" },
      { assetId: "board_model", candidateId: "board_glb", role: "model" },
    ]);

    const catalog = deriveCreationPreviewCatalog(
      graph.census,
      graph.inspections,
      new Map([[graph.job.job_id, graph.job]]),
      [graph.grant],
    );

    expect(catalog.items.map((item) => [item.assetId, item.mediaType, item.eligible])).toEqual([
      ["board_model", "model/gltf-binary", false],
      ["board_ui", "image/png", true],
      ["choice_audio", "audio/wav", true],
    ]);
    expect(catalog.items[0].unsupportedReason).toMatch(/not supported/iu);
    expect(catalog.items[1]).toMatchObject({
      assetpackArtifactId: "artifact_assetpack",
      assetpackContentHash: ASSETPACK_HASH,
      outputGrantId: "grant_assetpack",
      outputGrantGeneration: 2,
      selectedOutput: {
        candidateArtifactId: "board_png",
        role: "texture",
      },
    });
    expect(creationPreviewCandidateKey(catalog.items[1])).toContain(SNAPSHOT);
    expect(creationPreviewCandidateKey(catalog.items[1])).toContain("board_png");
  });

  it("fails closed when the sealed lineage projection is truncated or not tied to the committed seal", () => {
    const graph = previewGraph([
      { assetId: "board_ui", candidateId: "board_png", role: "texture" },
    ]);
    const assetpack = graph.inspections.get("artifact_assetpack")!;
    graph.inspections.set("artifact_assetpack", {
      ...assetpack,
      projection: {
        ...assetpack.projection,
        lineage: assetpack.projection.lineage.slice(1),
      },
    });
    expect(() =>
      deriveCreationPreviewCatalog(
        graph.census,
        graph.inspections,
        new Map([[graph.job.job_id, graph.job]]),
        [graph.grant],
      ),
    ).toThrow(/truncated|lineage/iu);

    const clean = previewGraph([
      { assetId: "board_ui", candidateId: "board_png", role: "texture" },
    ]);
    const uncommitted = {
      ...clean.job,
      progress: "cleanup_pending",
    } as StudioCreationJob;
    expect(() =>
      deriveCreationPreviewCatalog(
        clean.census,
        clean.inspections,
        new Map([[uncommitted.job_id, uncommitted]]),
        [clean.grant],
      ),
    ).toThrow(/committed/iu);
  });

  it("fails closed when the published grant names different assetpack bytes", () => {
    const graph = previewGraph([
      { assetId: "board_ui", candidateId: "board_png", role: "texture" },
    ]);
    const mismatchedGrant = {
      ...graph.grant,
      publication: {
        ...graph.grant.publication,
        id: "different_assetpack",
        content_hash: "0".repeat(64),
      },
    } as StudioCreationOutputGrant;

    expect(() =>
      deriveCreationPreviewCatalog(
        graph.census,
        graph.inspections,
        new Map([[graph.job.job_id, graph.job]]),
        [mismatchedGrant],
      ),
    ).toThrow(/exact published seal authority/iu);
  });

  it("fails closed when the seal target grant differs from its publication", () => {
    const graph = previewGraph([
      { assetId: "board_ui", candidateId: "board_png", role: "texture" },
    ]);
    const mismatchedTarget = {
      ...graph.job,
      operation_params: {
        ...graph.job.operation_params,
        target_grant_id: "different_grant",
        target_grant_generation: 7,
      },
    } as StudioCreationJob;

    expect(() =>
      deriveCreationPreviewCatalog(
        graph.census,
        graph.inspections,
        new Map([[mismatchedTarget.job_id, mismatchedTarget]]),
        [graph.grant],
      ),
    ).toThrow(/target grant|publication identity/iu);
  });

  it("validates exact PNG and WAV open envelopes against the selected authority", () => {
    const png = candidate("image/png", "texture");
    const opened = decodeCreationPreviewOpen(
      openReply(png, {
        kind: "png",
        width: 16,
        height: 9,
        mode: "rgba8",
      }),
      png,
    );
    expect(opened.ok).toBe(true);
    if (!opened.ok) throw new Error("expected a valid PNG preview envelope");
    expect(opened.value).toEqual({
      handle: HANDLE,
      mediaType: "image/png",
      byteLength: 4,
      sha256: FILE_HASH,
      metadata: { kind: "png", width: 16, height: 9, mode: "rgba8" },
    });

    const wav = candidate("audio/wav", "audio");
    expect(
      decodeCreationPreviewOpen(
        openReply(wav, {
          kind: "wav_pcm16",
          channels: 2,
          sample_rate: 48_000,
          frames: 96_000,
          sample_width: 2,
        }),
        wav,
      ),
    ).toMatchObject({ ok: true, value: { mediaType: "audio/wav" } });

    const mismatched = openReply(png, {
      kind: "png",
      width: 16,
      height: 9,
      mode: "rgba8",
    });
    if (mismatched.ok) {
      (mismatched.value as unknown as { result: { preview: { workspace_id: string } } }).result.preview.workspace_id =
        "other_workspace";
    }
    expect(decodeCreationPreviewOpen(mismatched, png)).toEqual({
      ok: false,
      handle: HANDLE,
    });
  });

  it("accepts canonical sequential chunks and only an exact immediate-previous replay", () => {
    const firstBytes = new Uint8Array(CREATION_PREVIEW_CHUNK_BYTES).fill(7);
    const finalBytes = new Uint8Array([1, 2, 3]);
    let stream = initialCreationPreviewStream(CREATION_PREVIEW_CHUNK_BYTES + finalBytes.length);

    const firstReply = readReply(0, firstBytes, CREATION_PREVIEW_CHUNK_BYTES, false, "1".repeat(64));
    const first = applyCreationPreviewChunk(firstReply, HANDLE, stream, FILE_HASH);
    expect(first.kind).toBe("next");
    if (first.kind !== "next") throw new Error("expected next chunk");
    stream = first.stream;
    expect(stream.cumulativeBytes).toBe(CREATION_PREVIEW_CHUNK_BYTES);

    const replay = applyCreationPreviewChunk(firstReply, HANDLE, stream, FILE_HASH);
    expect(replay).toEqual({ kind: "replay", stream });
    expect(() =>
      applyCreationPreviewChunk(
        readReply(0, firstBytes, CREATION_PREVIEW_CHUNK_BYTES, false, "2".repeat(64)),
        HANDLE,
        stream,
        FILE_HASH,
      ),
    ).toThrow(/replay/iu);

    const final = applyCreationPreviewChunk(
      readReply(
        1,
        finalBytes,
        CREATION_PREVIEW_CHUNK_BYTES + finalBytes.length,
        true,
        FILE_HASH,
      ),
      HANDLE,
      stream,
      FILE_HASH,
    );
    expect(final.kind).toBe("next");
    if (final.kind !== "next") throw new Error("expected final chunk");
    expect(final.chunk.bytes).toEqual(finalBytes);
    expect(final.stream).toMatchObject({
      nextSequence: 2,
      cumulativeBytes: CREATION_PREVIEW_CHUNK_BYTES + 3,
      eof: true,
    });
  });

  it("rejects noncanonical base64, missing sequences, wrong lengths, and false EOF claims", () => {
    const stream = initialCreationPreviewStream(4);
    expect(() =>
      applyCreationPreviewChunk(readReply(1, new Uint8Array([1, 2, 3, 4]), 4, true, FILE_HASH), HANDLE, stream, FILE_HASH),
    ).toThrow(/sequence/iu);

    const invalidBase64 = readReply(0, new Uint8Array([1, 2, 3, 4]), 4, true, FILE_HASH);
    if (invalidBase64.ok) {
      (invalidBase64.value as unknown as { result: { data_base64: string } }).result.data_base64 = "AQIDBA";
    }
    expect(() => applyCreationPreviewChunk(invalidBase64, HANDLE, stream, FILE_HASH)).toThrow(/base64/iu);

    expect(() =>
      applyCreationPreviewChunk(readReply(0, new Uint8Array([1, 2, 3]), 3, true, FILE_HASH), HANDLE, stream, FILE_HASH),
    ).toThrow(/length|bytes/iu);
    expect(() =>
      applyCreationPreviewChunk(readReply(0, new Uint8Array([1, 2, 3, 4]), 4, false, FILE_HASH), HANDLE, stream, FILE_HASH),
    ).toThrow(/EOF/iu);
  });

  it("requires one exact v4 close envelope", () => {
    expect(decodeCreationPreviewClose(closeReply(HANDLE), HANDLE)).toBe(true);
    const wrong = closeReply(HANDLE);
    if (wrong.ok) {
      (wrong.value as unknown as { result: { closed: boolean } }).result.closed = false;
    }
    expect(decodeCreationPreviewClose(wrong, HANDLE)).toBe(false);
  });
});

interface AssetDefinition {
  assetId: string;
  candidateId: string;
  role: string;
}

function previewGraph(definitions: readonly AssetDefinition[]) {
  const lineageArtifacts: StudioCreationArtifact[] = [];
  const inspections = new Map<string, StudioCreationArtifactInspectResult>();
  const directDependencies: string[] = [];
  for (const [index, definition] of definitions.entries()) {
    const suffix = String(index);
    const selectionId = `artifact_selection_${suffix}`;
    const licenseId = `artifact_license_${suffix}`;
    const qaId = `artifact_qa_${suffix}`;
    for (const item of [
      artifact(selectionId, "world-forge.asset_selection", []),
      artifact(licenseId, "world-forge.asset_license_record", []),
      artifact(qaId, "world-forge.asset_qa_report", []),
    ]) {
      lineageArtifacts.push(item);
      directDependencies.push(item.artifact_id);
    }
    inspections.set(
      selectionId,
      inspection(lineageArtifacts.at(-3)!, [], {
        asset_id: definition.assetId,
        selected_output_bindings: [`${definition.candidateId}:${definition.role}`],
      }),
    );
    inspections.set(
      licenseId,
      inspection(lineageArtifacts.at(-2)!, [], {
        asset_id: definition.assetId,
        candidate_artifact_id: definition.candidateId,
        candidate_role: definition.role,
        redistribution: true,
      }),
    );
    inspections.set(
      qaId,
      inspection(
        lineageArtifacts.at(-1)!,
        [],
        { asset_id: definition.assetId, blocker_count: 0 },
        "passed",
      ),
    );
  }
  const assetpack = artifact(
    "artifact_assetpack",
    "world-forge.assetpack",
    directDependencies,
    {
      producer: { kind: "future_candidate", phase_id: null, reference_id: "job_seal" },
      subjectId: "puzzle_assetpack",
      contentHash: ASSETPACK_HASH,
    },
  );
  const artifacts = [...lineageArtifacts, assetpack];
  const census = censusWith(artifacts);
  inspections.set(
    assetpack.artifact_id,
    inspection(assetpack, directDependencies, { asset_count: definitions.length }),
  );
  const qaIds = definitions.map((_, index) => `artifact_qa_${String(index)}`);
  const publication = {
    grant_id: "grant_assetpack",
    grant_generation: 2,
    kind: "generic_assetpack_directory" as const,
    state: "published" as const,
    assetpack: {
      format: "world-forge.assetpack" as const,
      format_version: 1 as const,
      id: "puzzle_assetpack",
      content_hash: ASSETPACK_HASH,
      inventory_hash: INVENTORY_HASH,
    },
  };
  const job = {
    format: "world-forge.studio_creation_job",
    format_version: 3,
    job_id: "job_seal",
    workspace_id: "creation_workspace",
    operation: "asset.release.seal",
    operation_params: {
      qa_report_artifact_ids: qaIds,
      manifest_id: "puzzle_manifest",
      target_grant_id: "grant_assetpack",
      target_grant_generation: 2,
    },
    state: "succeeded",
    generation: 3,
    authority: {
      root_generation: 4,
      source_revision: SOURCE,
      workflow_status_hash: null,
      artifact_snapshot_hash: "f".repeat(64),
    },
    inputs: [],
    progress: "committed",
    result: {
      output_artifact_ids: ["artifact_asset_manifest", "artifact_assetpack"],
      artifact_snapshot_hash: SNAPSHOT,
      analysis_status: "passed",
      reason_codes: [],
      cleanup_pending: false,
      publication,
    },
    error: null,
    created_at: "2026-08-05T00:00:00Z",
    started_at: "2026-08-05T00:00:01Z",
    finished_at: "2026-08-05T00:00:02Z",
    updated_at: "2026-08-05T00:00:03Z",
    record_hash: "9".repeat(64),
  } as unknown as StudioCreationJob;
  const grant: StudioCreationOutputGrant = {
    format: "world-forge.studio_creation_output_grant",
    format_version: 1,
    grant_id: "grant_assetpack",
    workspace_id: "creation_workspace",
    kind: "generic_assetpack_directory",
    display_name: "Puzzle assets",
    state: "published",
    generation: 2,
    publication: publication.assetpack,
    created_at: "2026-08-05T00:00:00Z",
    updated_at: "2026-08-05T00:00:03Z",
  };
  return { census, inspections, job, grant };
}

function candidate(
  mediaType: "image/png" | "audio/wav",
  role: "texture" | "audio",
): CreationPreviewCandidate {
  return {
    key: "candidate-key",
    workspaceId: "creation_workspace",
    rootGeneration: 4,
    sourceRevision: SOURCE,
    workflowStatusHash: null,
    artifactSnapshotHash: SNAPSHOT,
    assetpackArtifactId: "artifact_assetpack",
    assetpackId: "puzzle_assetpack",
    assetpackContentHash: ASSETPACK_HASH,
    outputGrantId: "grant_assetpack",
    outputGrantGeneration: 2,
    sealJobId: "job_seal",
    assetId: mediaType === "image/png" ? "board_ui" : "choice_audio",
    mediaType,
    selectedOutput: {
      candidateArtifactId: mediaType === "image/png" ? "board_png" : "choice_wav",
      role,
      mediaType,
    },
    eligible: true,
    unsupportedReason: null,
  };
}

function censusWith(artifacts: StudioCreationArtifact[]): CreationExecutionCensus {
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
    candidateArtifacts: artifacts,
    selectableArtifacts: artifacts,
    selectableById: new Map(artifacts.map((item) => [item.artifact_id, item])),
  };
}

function artifact(
  artifactId: string,
  format: string,
  dependencies: readonly string[],
  overrides: {
    producer?: StudioCreationArtifact["producer"];
    subjectId?: string;
    contentHash?: string;
  } = {},
): StudioCreationArtifact {
  return {
    format: "world-forge.studio_creation_artifact",
    format_version: 1,
    artifact_id: artifactId,
    subject: {
      format,
      format_version: 1,
      id: overrides.subjectId ?? artifactId.replace("artifact_", ""),
      content_hash: overrides.contentHash ?? "8".repeat(64),
    },
    lifecycle: "candidate",
    roles: ["asset_lineage"],
    producer: overrides.producer ?? {
      kind: "future_candidate",
      phase_id: null,
      reference_id: "job_process",
    },
    references: { dependency_count: dependencies.length, dependent_count: 0 },
    authority: {
      workspace_id: "creation_workspace",
      root_generation: 4,
      source_revision: SOURCE,
      workflow_status_hash: null,
    },
    record_hash: "7".repeat(64),
  };
}

function inspection(
  artifactValue: StudioCreationArtifact,
  dependencies: readonly string[],
  facts: Record<string, string | number | boolean | string[]>,
  status: string | null = null,
): StudioCreationArtifactInspectResult {
  return {
    authority: artifactValue.authority,
    artifact_snapshot_hash: SNAPSHOT,
    artifact: artifactValue,
    projection: {
      projection_kind: "asset_lineage",
      title: artifactValue.subject.id,
      status,
      facts: Object.entries(facts).map(([key, value]) => ({ key, value })),
      lineage: dependencies.map((artifactId) => ({
        relation: "depends_on",
        artifact_id: artifactId,
        lifecycle: "candidate",
      })),
    },
  };
}

function openReply(
  selected: CreationPreviewCandidate,
  metadata: Record<string, unknown>,
) {
  return v4("creation_preview.open", {
    preview: {
      format: "world-forge.studio_creation_preview",
      format_version: 1,
      handle: HANDLE,
      workspace_id: selected.workspaceId,
      assetpack_artifact_id: selected.assetpackArtifactId,
      output_grant_id: selected.outputGrantId,
      output_grant_generation: selected.outputGrantGeneration,
      asset_id: selected.assetId,
      media_type: selected.selectedOutput.mediaType,
      byte_length: 4,
      sha256: FILE_HASH,
      chunk_bytes: CREATION_PREVIEW_CHUNK_BYTES,
      metadata,
    },
  });
}

function readReply(
  sequence: number,
  bytes: Uint8Array,
  cumulativeBytes: number,
  eof: boolean,
  cumulativeSha256: string,
) {
  return v4("creation_preview.read", {
    handle: HANDLE,
    sequence,
    data_base64: Buffer.from(bytes).toString("base64"),
    byte_length: bytes.byteLength,
    cumulative_bytes: cumulativeBytes,
    cumulative_sha256: cumulativeSha256,
    eof,
  });
}

function closeReply(handle: string) {
  return v4("creation_preview.close", { handle, closed: true });
}

function v4(method: string, result: Record<string, unknown>) {
  return {
    ok: true as const,
    value: {
      protocol: "rpg-world-forge.studio_protocol" as const,
      protocol_version: 4 as const,
      kind: "response" as const,
      request_id: "preview_request",
      method,
      result,
    },
  };
}

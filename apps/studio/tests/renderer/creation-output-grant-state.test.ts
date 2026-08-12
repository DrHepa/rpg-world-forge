import { readFileSync } from "node:fs";

import { describe, expect, it, vi } from "vitest";

import {
  loadCreationAssetpackGrantBindings,
  loadCreationAuthorityOutputGrantCensus,
  loadCreationOutputGrantCensus,
  validateCreationOutputGrant,
} from "../../src/renderer/creation-output-grant-state";
import type {
  ForgeStudioApi,
  StudioCreationOutputGrant,
} from "../../src/shared/studio-api";
import type { CreationExecutionCensus } from "../../src/renderer/creation-execution-state";

const authority = {
  workspaceId: "workspace_puzzle",
  rootGeneration: 2,
  sourceRevision: "a".repeat(64),
  workflowStatusHash: "b".repeat(64),
  artifactSnapshotHash: "c".repeat(64),
};

describe("creation output grant census", () => {
  it("pages every valid kind and retains every generic assetpack authority", async () => {
    const listCreationOutputGrants = vi
      .fn()
      .mockResolvedValueOnce(
        v4({
          grants: [
            grant("grant_a", "ready"),
            grant("grant_b", "reserved"),
            grant("grant_runtime", "ready", 2),
          ],
          next_cursor: "grant_runtime",
        }),
      )
      .mockResolvedValueOnce(
        v4({
          grants: [
            grant("grant_y", "recovery_required"),
            grant("grant_z", "published"),
          ],
          next_cursor: null,
        }),
      );
    const api = {
      listCreationOutputGrants,
    } as unknown as ForgeStudioApi;

    const loaded = await loadCreationOutputGrantCensus(api, census());

    expect(loaded.map((item) => [item.grant_id, item.state])).toEqual([
      ["grant_a", "ready"],
      ["grant_b", "reserved"],
      ["grant_runtime", "ready"],
      ["grant_y", "recovery_required"],
      ["grant_z", "published"],
    ]);
    expect(listCreationOutputGrants).toHaveBeenNthCalledWith(1, {
      workspaceId: authority.workspaceId,
      expectedRootGeneration: authority.rootGeneration,
      expectedSourceRevision: authority.sourceRevision,
      expectedWorkflowStatusHash: authority.workflowStatusHash,
      expectedArtifactSnapshotHash: authority.artifactSnapshotHash,
      cursor: null,
      limit: 8,
    });
    expect(listCreationOutputGrants).toHaveBeenNthCalledWith(
      2,
      expect.objectContaining({ cursor: "grant_runtime", limit: 8 }),
    );
  });

  it("keeps legacy v4 listing below v6 and uses fixed v5 authority listing for headless grants", async () => {
    const legacyListCreationOutputGrants = vi.fn().mockResolvedValue(
      v4({
        grants: [grant("grant_bundle", "published", 2)],
        next_cursor: null,
      }),
    );
    const legacyApi = {
      listCreationOutputGrants: legacyListCreationOutputGrants,
    } as unknown as ForgeStudioApi;

    await expect(loadCreationOutputGrantCensus(legacyApi, census())).resolves.toEqual([
      grant("grant_bundle", "published", 2),
    ]);

    const listCreationAuthorityOutputGrants = vi
      .fn()
      .mockResolvedValueOnce(
        v5({
          grants: [
            grant("grant_bundle", "published", 2),
            headlessGrant("grant_headless_a", "ready"),
          ],
          next_cursor: "grant_headless_a",
        }),
      )
      .mockResolvedValueOnce(
        v5({
          grants: [headlessGrant("grant_headless_z", "published")],
          next_cursor: null,
        }),
      );
    const authorityApi = {
      listCreationAuthorityOutputGrants,
    } as unknown as ForgeStudioApi;

    const loaded = await loadCreationAuthorityOutputGrantCensus(
      authorityApi,
      census(),
      authorityCapabilities(),
    );

    expect(loaded.map((item) => [item.grant_id, item.format_version, item.kind])).toEqual([
      ["grant_bundle", 2, "game_runtime_bundle_directory"],
      ["grant_headless_a", 6, "headless_evidence_directory"],
      ["grant_headless_z", 6, "headless_evidence_directory"],
    ]);
    expect(listCreationAuthorityOutputGrants).toHaveBeenNthCalledWith(2, {
      workspaceId: authority.workspaceId,
      expectedRootGeneration: authority.rootGeneration,
      expectedSourceRevision: authority.sourceRevision,
      expectedWorkflowStatusHash: authority.workflowStatusHash,
      expectedArtifactSnapshotHash: authority.artifactSnapshotHash,
      cursor: "grant_headless_a",
      limit: 8,
    });
  });

  it("fails authority v5 grant listing closed without exact capabilities, function, or valid page", async () => {
    const api = {
      listCreationAuthorityOutputGrants: vi.fn().mockResolvedValue(
        v5({
          grants: [headlessGrant("grant_headless", "ready")],
          next_cursor: null,
        }),
      ),
    } as unknown as ForgeStudioApi;

    await expect(
      loadCreationAuthorityOutputGrantCensus(api, census(), null),
    ).rejects.toThrow(/authority output grants unavailable/iu);
    await expect(
      loadCreationAuthorityOutputGrantCensus({} as ForgeStudioApi, census(), authorityCapabilities()),
    ).rejects.toThrow(/authority output grants unavailable/iu);
    await expect(
      loadCreationAuthorityOutputGrantCensus(
        {
          listCreationAuthorityOutputGrants: vi.fn().mockResolvedValue(
            v5({
              grants: [{ ...headlessGrant("grant_headless", "ready"), format_version: 7 }],
              next_cursor: null,
            }),
          ),
        } as unknown as ForgeStudioApi,
        census(),
        authorityCapabilities(),
      ),
    ).rejects.toThrow(/invalid creation output grant|output grant census/iu);
  });

  it("fails closed on malformed grants, ordering, cursor, and authority", async () => {
    expect(() =>
      validateCreationOutputGrant({ ...grant("grant_a", "ready"), path: "/private" }),
    ).toThrow(/invalid creation output grant/iu);
    expect(() =>
      validateCreationOutputGrant({
        ...grant("grant_a", "ready"),
        format_version: 2,
      }),
    ).toThrow(/invalid creation output grant/iu);
    expect(() =>
      validateCreationOutputGrant({
        ...grant("grant_a", "ready"),
        created_at: "2026-99-99T99:99:99Z",
        updated_at: "2026-99-99T99:99:99Z",
      }),
    ).toThrow(/invalid creation output grant/iu);

    for (const result of [
      { grants: [grant("grant_b", "ready"), grant("grant_a", "ready")], next_cursor: null },
      { grants: [grant("grant_a", "ready")], next_cursor: "grant_b" },
      {
        grants: [grant("grant_a", "ready")],
        next_cursor: null,
        authority: { ...publicAuthority(), source_revision: "0".repeat(64) },
      },
    ]) {
      const api = {
        listCreationOutputGrants: vi.fn().mockResolvedValue(v4(result)),
      } as unknown as ForgeStudioApi;
      await expect(loadCreationOutputGrantCensus(api, census())).rejects.toThrow(
        /output grant census/iu,
      );
    }
  });

  it("uses the service binary order and validates published facts for every grant kind", async () => {
    const binaryOrdered = [
      grant("grant-0", "ready"),
      grant("grant0", "ready"),
      grant("grant_0", "ready"),
    ];
    const api = {
      listCreationOutputGrants: vi.fn().mockResolvedValue(
        v4({ grants: binaryOrdered, next_cursor: null }),
      ),
    } as unknown as ForgeStudioApi;
    await expect(loadCreationOutputGrantCensus(api, census())).resolves.toHaveLength(3);

    for (let version = 1; version <= 5; version += 1) {
      const published = grant(`published_${String(version)}`, "published", version);
      expect(validateCreationOutputGrant(published)).toEqual(published);
    }
    expect(validateCreationOutputGrant(headlessGrant())).toEqual(headlessGrant());
    expect(() =>
      validateCreationOutputGrant({
        ...headlessGrant(),
        format_version: 7,
      }),
    ).toThrow(/invalid creation output grant/iu);
    expect(() =>
      validateCreationOutputGrant({
        ...headlessGrant(),
        kind: "game_runtime_bundle_directory",
      }),
    ).toThrow(/invalid creation output grant/iu);
  });

  it("matches generated protocol types to the Python output-grant version matrix", () => {
    const generatedGrantSchema = JSON.parse(
      readFileSync(
        new URL("../../../../schemas/studio-creation-output-grant-v6.schema.json", import.meta.url),
        "utf8",
      ),
    ) as {
      allOf: {
        if?: { properties?: Record<string, { const?: unknown }> };
        then?: { properties?: Record<string, { const?: unknown }> };
      }[];
    };
    const pythonContracts = readFileSync(
      new URL("../../../../src/worldforge/studio/contracts.py", import.meta.url),
      "utf8",
    );
    const schemaMatrix = new Map(
      generatedGrantSchema.allOf
        .map((item) => ({
          version: item.if?.properties?.format_version?.const,
          kind: item.then?.properties?.kind?.const,
        }))
        .filter((item): item is { version: unknown; kind: unknown } =>
          item.version !== undefined && item.kind !== undefined,
        )
        .map((item) => [Number(item.version), String(item.kind)]),
    );

    expect(pythonContracts).toContain('2: "game_runtime_bundle_directory"');
    expect(pythonContracts).toContain('6: "headless_evidence_directory"');
    expect(schemaMatrix.get(2)).toBe("game_runtime_bundle_directory");
    expect(schemaMatrix.get(6)).toBe("headless_evidence_directory");
    expect(Array.from(schemaMatrix)).not.toContainEqual([
      6,
      "game_runtime_bundle_directory",
    ]);
  });

  it("binds every live assetpack grant across reservation and recovery generations", async () => {
    const reserved = { ...grant("grant_reserved", "reserved"), generation: 1 };
    const recovery = { ...grant("grant_recovery", "recovery_required"), generation: 2 };
    const runtime = { ...grant("grant_runtime", "reserved", 2), generation: 1 };
    const jobs = [
      sealJob("seal_reserved", reserved.grant_id, 1, "queued"),
      sealJob("seal_recovery", recovery.grant_id, 1, "orphaned"),
      sealJob("seal_runtime", runtime.grant_id, 1, "queued"),
    ];
    const api = jobListApi(jobs);

    const bound = await loadCreationAssetpackGrantBindings(
      api,
      census(),
      [reserved, recovery, runtime],
    );

    expect(bound.map((job) => job.job_id)).toEqual([
      "seal_reserved",
      "seal_recovery",
    ]);
  });

  it("fails closed when an assetpack grant has no transition-exact seal binding", async () => {
    const recovery = { ...grant("grant_recovery", "recovery_required"), generation: 2 };
    const api = jobListApi([
      sealJob("seal_wrong_generation", recovery.grant_id, 2, "orphaned"),
    ]);

    await expect(
      loadCreationAssetpackGrantBindings(api, census(), [recovery]),
    ).rejects.toThrow(/one exact seal job binding/iu);
  });
});

function census(): CreationExecutionCensus {
  return { authority } as CreationExecutionCensus;
}

function publicAuthority() {
  return {
    workspace_id: authority.workspaceId,
    root_generation: authority.rootGeneration,
    source_revision: authority.sourceRevision,
    workflow_status_hash: authority.workflowStatusHash,
  };
}

function v4(
  result: Record<string, unknown>,
): { ok: true; value: Record<string, unknown> } {
  return {
    ok: true,
    value: {
      protocol: "rpg-world-forge.studio_protocol",
      protocol_version: 4,
      kind: "response",
      request_id: "request_output_grants",
      method: "creation_output_grant.list",
      result: {
        authority: publicAuthority(),
        artifact_snapshot_hash: authority.artifactSnapshotHash,
        ...result,
      },
    },
  };
}

function v5(
  result: Record<string, unknown>,
): { ok: true; value: Record<string, unknown> } {
  return {
    ok: true,
    value: {
      protocol: "rpg-world-forge.studio_protocol",
      protocol_version: 5,
      kind: "response",
      request_id: "request_output_grants",
      method: "creation_output_grant.list",
      result: {
        authority: publicAuthority(),
        artifact_snapshot_hash: authority.artifactSnapshotHash,
        ...result,
      },
    },
  };
}

function authorityCapabilities() {
  return {
    protocolVersion: 5,
    asset_authority_reviews: true,
    asset_release_authority: true,
    runtime_headless_authority: true,
    creation_preview_pre_release: true,
  } as const;
}

function jobListApi(jobs: readonly Record<string, unknown>[]): ForgeStudioApi {
  return {
    listCreationJobs: vi.fn().mockImplementation(({ state }) =>
      Promise.resolve({
        ok: true,
        value: {
          protocol: "rpg-world-forge.studio_protocol",
          protocol_version: 4,
          kind: "response",
          request_id: `request_jobs_${String(state)}`,
          method: "creation_job.list",
          result: {
            jobs: jobs.filter((job) => job.state === state),
            next_sequence: null,
          },
        },
      }),
    ),
  } as unknown as ForgeStudioApi;
}

function sealJob(
  jobId: string,
  grantId: string,
  grantGeneration: number,
  state: "queued" | "orphaned",
): Record<string, unknown> {
  return {
    format: "world-forge.studio_creation_job",
    format_version: 3,
    job_id: jobId,
    workspace_id: authority.workspaceId,
    operation: "asset.release.seal",
    operation_params: {
      qa_report_artifact_ids: ["artifact_qa"],
      manifest_id: `${grantId}_manifest`,
      target_grant_id: grantId,
      target_grant_generation: grantGeneration,
    },
    state,
    generation: 1,
    authority: {
      root_generation: authority.rootGeneration,
      source_revision: authority.sourceRevision,
      workflow_status_hash: authority.workflowStatusHash,
      artifact_snapshot_hash: authority.artifactSnapshotHash,
    },
    inputs: [],
    progress: state,
    result: null,
    error:
      state === "orphaned"
        ? {
            code: "recovery_required",
            message: "Review retained seal evidence",
            retryable: true,
          }
        : null,
    created_at: "2026-08-05T00:00:00Z",
    started_at: null,
    finished_at: null,
    updated_at: "2026-08-05T00:00:01Z",
    record_hash: "f".repeat(64),
  };
}

function headlessGrant(
  grantId = "grant_headless",
  state: "ready" | "published" = "published",
): StudioCreationOutputGrant {
  return {
    format: "world-forge.studio_creation_output_grant",
    format_version: 6,
    grant_id: grantId,
    workspace_id: authority.workspaceId,
    kind: "headless_evidence_directory",
    display_name: "Headless evidence",
    state,
    generation: state === "published" ? 1 : 0,
    publication:
      state === "published"
        ? {
            format: "world-forge.headless_evidence_set",
            format_version: 1,
            id: "headless_evidence_01",
            content_hash: "1".repeat(64),
            tree_hash: "2".repeat(64),
          }
        : null,
    created_at: "2026-08-05T00:00:00Z",
    updated_at: "2026-08-05T00:00:00Z",
  };
}

function grant(
  grantId: string,
  state: StudioCreationOutputGrant["state"],
  version = 1,
): StudioCreationOutputGrant {
  const kind = {
    1: "generic_assetpack_directory",
    2: "game_runtime_bundle_directory",
    3: "game_materialization_bundle_directory",
    4: "standalone_game_directory",
    5: "game_package_file",
  }[version] as StudioCreationOutputGrant["kind"];
  return {
    format: "world-forge.studio_creation_output_grant",
    format_version: version as StudioCreationOutputGrant["format_version"],
    grant_id: grantId,
    workspace_id: authority.workspaceId,
    kind,
    display_name: grantId,
    state,
    generation: state === "ready" ? 0 : 1,
    publication:
      state === "published"
        ? publishedFacts(version)
        : null,
    created_at: "2026-08-05T00:00:00Z",
    updated_at: "2026-08-05T00:00:01Z",
  } as StudioCreationOutputGrant;
}

function publishedFacts(version: number): Record<string, unknown> {
  const common = {
    format: {
      1: "world-forge.assetpack",
      2: "world-forge.game_runtime_bundle",
      3: "world-forge.game_materialization_bundle",
      4: "world-forge.standalone_game",
      5: "world-forge.game_package",
    }[version],
    format_version: 1,
    id: `published_${String(version)}`,
    content_hash: "d".repeat(64),
  };
  if (version === 1) return { ...common, inventory_hash: "e".repeat(64) };
  if (version === 5) {
    return { ...common, archive_sha256: "e".repeat(64), size_bytes: 128 };
  }
  return { ...common, tree_hash: "e".repeat(64) };
}

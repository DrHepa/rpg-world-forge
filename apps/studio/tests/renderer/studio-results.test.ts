import { describe, expect, it } from "vitest";

import {
  boundedFindings,
  decodeLegacyList,
  jobRows,
  workspaceIds,
} from "../../src/renderer/studio-results";

describe("Studio renderer result projection", () => {
  it("decodes only the expected legacy list method and bounds workspace IDs", () => {
    const decoded = decodeLegacyList(
      {
        ok: true,
        value: {
          protocol: "rpg-world-forge.studio_protocol",
          protocol_version: 1,
          kind: "response",
          request_id: "request-1",
          method: "workspace.list",
          result: { workspaces: [{ workspace_id: "workspace_01", world_root: "/private/root" }] },
        },
      },
      "workspace.list",
      "workspaces",
      20,
    );
    expect(decoded.error).toBeNull();
    expect(workspaceIds(decoded.records)).toEqual(["workspace_01"]);
    expect(
      decodeLegacyList(
        {
          ok: true,
          value: {
            protocol: "rpg-world-forge.studio_protocol",
            protocol_version: 1,
            kind: "response",
            request_id: "request-2",
            method: "job.list",
            result: { jobs: [] },
          },
        },
        "workspace.list",
        "workspaces",
        20,
      ).error,
    ).toMatch(/invalid workspace\.list response/u);
  });

  it("bounds validation and narrative findings", () => {
    const validation = {
      valid: false,
      profile: "release" as const,
      world_id: "world_01",
      object_count: 1,
      diagnostics: Array.from({ length: 40 }, (_, index) => ({
        severity: "error" as const,
        code: "validation_error" as const,
        path: `/objects/${String(index)}`,
        message: "Invalid object",
      })),
      diagnostics_truncated: false,
    };
    const analysis = {
      format: "rpg-world-forge.narrative_analysis" as const,
      format_version: 1 as const,
      world_id: "world_01",
      summary: {},
      findings: Array.from({ length: 40 }, (_, index) => ({
        severity: "warning" as const,
        code: "narrative_gap",
        path: `/lore/${String(index)}`,
        message: "Missing consequence",
      })),
    };
    const result = boundedFindings(validation, analysis, 64);
    expect(result.findings).toHaveLength(64);
    expect(result.truncated).toBe(true);
  });

  it("projects bounded job state and latest progress without raw payloads", () => {
    const rows = jobRows(
      [
        {
          job_id: "job_01",
          operation: "runtime.headless",
          state: "running",
          updated_at: "2026-07-23T00:00:00Z",
        },
      ],
      [
        {
          topic: "job.progress",
          entity_id: "job_01",
          payload: { progress: 25, rich_html: "<script>bad()</script>" },
        },
        {
          topic: "job.progress",
          entity_id: "job_01",
          payload: { progress: 60 },
        },
      ],
    );
    expect(rows).toEqual([
      {
        id: "job_01",
        title: "runtime.headless",
        meta: "2026-07-23T00:00:00Z",
        detail: "job_01",
        state: "running",
        progress: 60,
      },
    ]);
  });
});

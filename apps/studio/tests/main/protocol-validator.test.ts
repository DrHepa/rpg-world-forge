import { describe, expect, it } from "vitest";

import { validateStudioEnvelope } from "../../src/main/protocol-validator";
import type {
  AssetCatalogInspectRequest,
  AssetCatalogInspectResponse,
  AssetCatalogListRequest,
  AssetCatalogListResponse,
  AssetPreviewOpenRequest,
  AssetPreviewReadResponse,
} from "../../src/generated/studio-protocol";
import type {
  StudioJobCancelResponse,
  StudioJobCreateRequest,
  StudioJobCreateResponse,
  StudioSourceReadRequest,
  StudioSourceReadResponse,
} from "../../src/shared/studio-api";

const protocol = {
  protocol: "rpg-world-forge.studio_protocol",
  protocol_version: 1,
} as const;

describe("Studio protocol authoring discrimination", () => {
  it("closes preview authority and enforces canonical bounded chunks", () => {
    const open: AssetPreviewOpenRequest = {
      ...protocol,
      kind: "request",
      request_id: "preview-open",
      method: "asset.preview.open",
      params: {
        workspace_id: "workspace_01",
        manifest_revision: "a".repeat(64),
        entry_id: `asset_${"b".repeat(64)}`,
      },
    };
    const read: AssetPreviewReadResponse = {
      ...protocol,
      kind: "response",
      request_id: "preview-read",
      method: "asset.preview.read",
      result: {
        handle: "C".repeat(43),
        sequence: 0,
        data_base64: Buffer.from("abc").toString("base64"),
        byte_length: 3,
        cumulative_bytes: 3,
        cumulative_sha256: "d".repeat(64),
        eof: true,
      },
    };

    expect(validateStudioEnvelope(open)).toBe(true);
    expect(validateStudioEnvelope(read)).toBe(true);
    expect(
      validateStudioEnvelope({
        ...open,
        params: { ...open.params, path: "/private/preview.png" },
      }),
    ).toBe(false);
    for (const result of [
      { ...read.result, data_base64: "YR==" },
      { ...read.result, data_base64: "%%==" },
      { ...read.result, data_base64: "" },
      { ...read.result, byte_length: 2 },
      { ...read.result, cumulative_bytes: 4 },
      { ...read.result, sequence: 1 },
      { ...read.result, eof: false },
      { ...read.result, payload: "YWJj" },
    ]) {
      expect(validateStudioEnvelope({ ...read, result })).toBe(false);
    }
  });

  it("closes revision-bound asset catalog requests", () => {
    const firstPage: AssetCatalogListRequest = {
      ...protocol,
      kind: "request",
      request_id: "assets-1",
      method: "asset.catalog.list",
      params: { workspace_id: "workspace_01", limit: 64 },
    };
    const laterPage: AssetCatalogListRequest = {
      ...firstPage,
      request_id: "assets-2",
      params: {
        workspace_id: "workspace_01",
        offset: 64,
        limit: 64,
        expected_manifest_revision: "a".repeat(64),
      },
    };
    const inspect: AssetCatalogInspectRequest = {
      ...protocol,
      kind: "request",
      request_id: "asset-inspect-1",
      method: "asset.catalog.inspect",
      params: {
        workspace_id: "workspace_01",
        entry_id: `asset_${"b".repeat(64)}`,
        expected_manifest_revision: "a".repeat(64),
      },
    };

    expect(validateStudioEnvelope(firstPage)).toBe(true);
    expect(validateStudioEnvelope(laterPage)).toBe(true);
    expect(validateStudioEnvelope(inspect)).toBe(true);
    expect(
      validateStudioEnvelope({
        ...laterPage,
        params: { workspace_id: "workspace_01", offset: 64, limit: 64 },
      }),
    ).toBe(false);
    expect(
      validateStudioEnvelope({
        ...firstPage,
        params: { ...firstPage.params, path: "assets/renderpack/manifest.json" },
      }),
    ).toBe(false);
    expect(
      validateStudioEnvelope({
        ...inspect,
        params: { ...inspect.params, entry_id: "assets/renderpack/manifest.json" },
      }),
    ).toBe(false);
  });

  it("accepts only closed metadata-only asset catalog responses", () => {
    const entry = {
      entry_id: `asset_${"b".repeat(64)}`,
      asset_id: "neutral_sheet",
      category: "runtime_output",
      role: "texture",
      path: "assets/renderpack/processed/neutral_sheet/neutral_sheet.png",
      sha256: "c".repeat(64),
      media_type: "image/png",
      selected: false,
      inspectable: true,
    } as const;
    const list: AssetCatalogListResponse = {
      ...protocol,
      kind: "response",
      request_id: "assets-1",
      method: "asset.catalog.list",
      result: {
        manifest_revision: "a".repeat(64),
        offset: 0,
        limit: 64,
        entries: [entry],
        next_offset: null,
      },
    };
    const inspect: AssetCatalogInspectResponse = {
      ...protocol,
      kind: "response",
      request_id: "asset-inspect-1",
      method: "asset.catalog.inspect",
      result: {
        manifest_revision: "a".repeat(64),
        entry,
        inspection: {
          kind: "png",
          width: 32,
          height: 16,
          bit_depth: 8,
          color_type: 6,
          interlaced: false,
        },
      },
    };

    expect(validateStudioEnvelope(list)).toBe(true);
    expect(validateStudioEnvelope(inspect)).toBe(true);
    expect(
      validateStudioEnvelope({ ...list, result: { ...list.result, total: 1 } }),
    ).toBe(false);
    expect(
      validateStudioEnvelope({
        ...list,
        result: {
          ...list.result,
          entries: [{ ...entry, path: "/absolute/private.png" }],
        },
      }),
    ).toBe(false);
    expect(
      validateStudioEnvelope({
        ...inspect,
        result: {
          ...inspect.result,
          inspection: { ...inspect.result.inspection, bytes: "forbidden" },
        },
      }),
    ).toBe(false);
  });

  it("enforces portable asset paths and UTF-8 byte limits", () => {
    const entry = {
      entry_id: `asset_${"b".repeat(64)}`,
      asset_id: "neutral_sheet",
      category: "runtime_output",
      role: "texture",
      path: "assets/renderpack/processed/neutral_sheet/neutral_sheet.png",
      sha256: "c".repeat(64),
      media_type: "image/png",
      selected: false,
      inspectable: true,
    } as const;
    const list = {
      ...protocol,
      kind: "response",
      request_id: "asset-path",
      method: "asset.catalog.list",
      result: {
        manifest_revision: "a".repeat(64),
        offset: 0,
        limit: 64,
        entries: [entry],
        next_offset: null,
      },
    } as const;

    for (const path of [
      "assets/../private.png",
      Array.from({ length: 33 }, (_, index) => `part-${String(index)}`).join("/"),
      "assets/cafe\u0301.png",
    ]) {
      expect(
        validateStudioEnvelope({
          ...list,
          result: { ...list.result, entries: [{ ...entry, path }] },
        }),
      ).toBe(false);
    }

    const oversizedValue = { text: "é".repeat(200_000) };
    for (const inspection of [
      {
        kind: "json",
        encoding: "utf-8",
        content: JSON.stringify(oversizedValue),
        value: oversizedValue,
      },
      {
        kind: "glsl",
        encoding: "utf-8",
        content: "é".repeat(200_000),
      },
    ]) {
      expect(
        validateStudioEnvelope({
          ...protocol,
          kind: "response",
          request_id: `asset-${inspection.kind}`,
          method: "asset.catalog.inspect",
          result: {
            manifest_revision: "a".repeat(64),
            entry,
            inspection,
          },
        }),
      ).toBe(false);
    }
  });

  it("requires the exact source.read request params", () => {
    const valid: StudioSourceReadRequest = {
      ...protocol,
      kind: "request",
      request_id: "read-1",
      method: "source.read",
      params: { workspace_id: "workspace_01", path: "source/world.json" },
    };

    expect(validateStudioEnvelope(valid)).toBe(true);
    expect(validateStudioEnvelope({ ...valid, params: {} })).toBe(false);
    expect(
      validateStudioEnvelope({
        ...valid,
        params: { workspace_id: "workspace_01", path: "source/../project.json" },
      }),
    ).toBe(false);
  });

  it("accepts only the closed read-only job.create operations", () => {
    const valid: StudioJobCreateRequest = {
      ...protocol,
      kind: "request",
      request_id: "job-1",
      method: "job.create",
      params: {
        workspace_id: "workspace_01",
        operation: "runtime.headless",
        input: { worldpack: "build/worldpack.json", ticks: 0 },
      },
    };

    expect(validateStudioEnvelope(valid)).toBe(true);
    expect(
      validateStudioEnvelope({
        ...valid,
        params: {
          workspace_id: "workspace_01",
          operation: "runtime.headless",
          input: { worldpack: "build/worldpack.json", ticks: -1 },
        },
      }),
    ).toBe(false);
    expect(
      validateStudioEnvelope({
        ...valid,
        params: {
          workspace_id: "workspace_01",
          operation: "shell.execute",
          input: { command: "echo unsafe" },
        },
      }),
    ).toBe(false);
  });

  it("creates managed v2 jobs while retaining legacy v1 cancel responses", () => {
    const managedJob = {
      format: "rpg-world-forge.studio_job",
      format_version: 2,
      job_id: "job_01",
      workspace_id: "workspace_01",
      operation: "runtime.headless",
      state: "queued",
      input: { worldpack: "build/world.json", ticks: 0 },
      result: null,
      error: null,
      created_at: "2026-07-22T12:00:00Z",
      updated_at: "2026-07-22T12:00:00Z",
    } as const;
    const createResponse: StudioJobCreateResponse = {
      ...protocol,
      kind: "response",
      request_id: "job-1",
      method: "job.create",
      result: { job: managedJob },
    };
    expect(validateStudioEnvelope(createResponse)).toBe(true);

    const legacyJob = {
      ...managedJob,
      format_version: 1,
      operation: "runtime.headless",
      input: { legacy_command: "headless --old-contract" },
    } as const;
    expect(
      validateStudioEnvelope({
        ...createResponse,
        result: { job: legacyJob },
      }),
    ).toBe(false);
    const cancelResponse: StudioJobCancelResponse = {
      ...protocol,
      kind: "response",
      request_id: "cancel-1",
      method: "job.cancel",
      result: { job: legacyJob },
    };
    expect(validateStudioEnvelope(cancelResponse)).toBe(true);
  });

  it("requires source.read responses to name the method and exact result", () => {
    const valid: StudioSourceReadResponse = {
      ...protocol,
      kind: "response",
      request_id: "read-1",
      method: "source.read",
      result: {
        document: {
          path: "source/world.json",
          kind: "world",
          size: 3,
          sha256: "0".repeat(64),
          encoding: "utf-8",
          content: "{}\n",
          json: {},
        },
      },
    };

    expect(validateStudioEnvelope(valid)).toBe(true);
    const missingMethod: Record<string, unknown> = { ...valid };
    delete missingMethod.method;
    expect(validateStudioEnvelope(missingMethod)).toBe(false);
    expect(validateStudioEnvelope({ ...valid, method: "source.list" })).toBe(false);
    expect(validateStudioEnvelope({ ...valid, result: { documents: [] } })).toBe(false);
  });

  it("closes every changeset request and response including immutable diffs", () => {
    const operation = {
      path: "source/lore/entry.md",
      operation: "replace",
      base_sha256: "a".repeat(64),
      base_size: 4,
      proposed_sha256: "b".repeat(64),
      size: 4,
    } as const;
    const changeset = {
      format: "rpg-world-forge.studio_changeset",
      format_version: 2,
      changeset_id: "changeset_01",
      workspace_id: "workspace_01",
      status: "staged",
      operations: [operation],
      review_sha256: "c".repeat(64),
      created_at: "2026-07-23T00:00:00Z",
      updated_at: "2026-07-23T00:00:00Z",
    } as const;
    const create = {
      ...protocol,
      kind: "request",
      request_id: "stage-1",
      method: "changeset.create",
      params: {
        workspace_id: "workspace_01",
        operations: [
          {
            path: "source/lore/entry.md",
            operation: "replace",
            expected_base_sha256: "a".repeat(64),
            content: "new\n",
          },
        ],
      },
    } as const;
    expect(validateStudioEnvelope(create)).toBe(true);
    expect(validateStudioEnvelope({ ...create, params: { ...create.params, cwd: "/tmp" } })).toBe(
      false,
    );
    expect(
      validateStudioEnvelope({
        ...create,
        params: {
          ...create.params,
          operations: [{ ...create.params.operations[0], operation: "execute" }],
        },
      }),
    ).toBe(false);

    const ids = ["changeset.get", "changeset.diff"] as const;
    for (const method of ids) {
      expect(
        validateStudioEnvelope({
          ...protocol,
          kind: "request",
          request_id: method,
          method,
          params: { changeset_id: "changeset_01" },
        }),
      ).toBe(true);
    }
    expect(
      validateStudioEnvelope({
        ...protocol,
        kind: "request",
        request_id: "list-1",
        method: "changeset.list",
        params: { workspace_id: "workspace_01", status: "applying", limit: 1 },
      }),
    ).toBe(true);
    for (const method of [
      "changeset.approve",
      "changeset.reject",
      "changeset.apply",
    ] as const) {
      expect(
        validateStudioEnvelope({
          ...protocol,
          kind: "request",
          request_id: method,
          method,
          params: {
            changeset_id: "changeset_01",
            expected_review_sha256: "c".repeat(64),
          },
        }),
      ).toBe(true);
    }

    const getResponse = {
      ...protocol,
      kind: "response",
      request_id: "get-1",
      method: "changeset.get",
      result: { changeset },
    } as const;
    expect(validateStudioEnvelope(getResponse)).toBe(true);
    const withoutReview: Record<string, unknown> = { ...changeset };
    delete withoutReview.review_sha256;
    expect(
      validateStudioEnvelope({ ...getResponse, result: { changeset: withoutReview } }),
    ).toBe(false);
    expect(
      validateStudioEnvelope({
        ...getResponse,
        result: { changeset: { ...changeset, provider: "openai" } },
      }),
    ).toBe(false);
    const legacyChangeset = {
      ...withoutReview,
      format_version: 1,
      operations: [
        {
          path: operation.path,
          operation: operation.operation,
          base_sha256: operation.base_sha256,
          proposed_sha256: operation.proposed_sha256,
          size: operation.size,
        },
      ],
    } as const;
    expect(
      validateStudioEnvelope({ ...getResponse, result: { changeset: legacyChangeset } }),
    ).toBe(true);
    expect(
      validateStudioEnvelope({
        ...getResponse,
        result: { changeset: { ...legacyChangeset, provider: "openai" } },
      }),
    ).toBe(false);
    expect(
      validateStudioEnvelope({
        ...getResponse,
        result: { changeset: { ...changeset, format_version: 3 } },
      }),
    ).toBe(false);

    const diffResponse = {
      ...protocol,
      kind: "response",
      request_id: "diff-1",
      method: "changeset.diff",
      result: {
        diff: {
          changeset_id: "changeset_01",
          changeset_format_version: 2,
          available: true,
          unavailable_reason: null,
          review_sha256: "c".repeat(64),
          operations: [
            {
              ...operation,
              text_hunks: [
                {
                  base_start: 1,
                  base_count: 1,
                  proposed_start: 1,
                  proposed_count: 1,
                  lines: [
                    { kind: "remove", text: "old\n" },
                    { kind: "add", text: "new\n" },
                  ],
                },
              ],
              json_pointer_changes: null,
            },
          ],
        },
      },
    } as const;
    expect(validateStudioEnvelope(diffResponse)).toBe(true);
    expect(
      validateStudioEnvelope({
        ...diffResponse,
        result: {
          diff: { ...diffResponse.result.diff, changeset_format_version: 1 },
        },
      }),
    ).toBe(false);
    expect(
      validateStudioEnvelope({
        ...diffResponse,
        result: {
          diff: {
            ...diffResponse.result.diff,
            operations: [{ ...diffResponse.result.diff.operations[0], operation: "execute" }],
          },
        },
      }),
    ).toBe(false);
  });
});

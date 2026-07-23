import { describe, expect, it } from "vitest";

import { validateStudioEnvelope } from "../../src/main/protocol-validator";
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

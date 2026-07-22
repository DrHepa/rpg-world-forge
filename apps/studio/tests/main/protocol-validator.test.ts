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
});

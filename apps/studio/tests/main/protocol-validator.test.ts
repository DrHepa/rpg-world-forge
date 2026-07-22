import { describe, expect, it } from "vitest";

import { validateStudioEnvelope } from "../../src/main/protocol-validator";
import type {
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

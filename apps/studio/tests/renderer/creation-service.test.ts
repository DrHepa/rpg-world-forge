import { describe, expect, it } from "vitest";

import { expectCreationEvidenceResult } from "../../src/renderer/creation-service";
import type { CreationServiceError } from "../../src/renderer/creation-service";
import type {
  StudioClientResult,
  StudioV4ReplyEnvelope,
} from "../../src/shared/studio-api";

const response = {
  protocol: "rpg-world-forge.studio_protocol",
  protocol_version: 4,
  kind: "response",
  request_id: "request_01",
  method: "creation_output_grant.list",
  result: { grants: [] },
};

describe("creation service v4 envelope validation", () => {
  it("accepts only the exact closed response envelope", async () => {
    await expect(
      expectCreationEvidenceResult(reply(response), "creation_output_grant.list"),
    ).resolves.toEqual({ grants: [] });

    for (const invalid of [
      { ...response, protocol: "world-forge.studio_protocol" },
      { ...response, protocol_version: 3 },
      { ...response, kind: "request" },
      { ...response, request_id: "Invalid Request" },
      { ...response, leaked: "/private/output" },
    ]) {
      await expect(
        expectCreationEvidenceResult(reply(invalid), "creation_output_grant.list"),
      ).rejects.toThrow(/invalid creation evidence response/iu);
    }
  });

  it("accepts only an exact closed error envelope before surfacing its code", async () => {
    const error = {
      protocol: "rpg-world-forge.studio_protocol",
      protocol_version: 4,
      kind: "error",
      request_id: "request_01",
      error: {
        code: "conflict",
        message: "Authority changed",
        details: {},
      },
    };
    await expect(
      expectCreationEvidenceResult(reply(error), "creation_output_grant.list"),
    ).rejects.toEqual(
      expect.objectContaining<Partial<CreationServiceError>>({ code: "conflict" }),
    );
    for (const invalid of [
      { ...error, protocol_version: 3 },
      { ...error, request_id: "Invalid Request" },
      { ...error, error: { code: "conflict", message: "Authority changed" } },
      { ...error, error: { ...error.error, code: "unknown" } },
      { ...error, error: { ...error.error, leaked: "/private/output" } },
    ]) {
      await expect(
        expectCreationEvidenceResult(reply(invalid), "creation_output_grant.list"),
      ).rejects.toThrow(/invalid creation evidence response/iu);
    }
  });
});

function reply(value: unknown): Promise<StudioClientResult<StudioV4ReplyEnvelope>> {
  return Promise.resolve({ ok: true, value } as StudioClientResult<StudioV4ReplyEnvelope>);
}

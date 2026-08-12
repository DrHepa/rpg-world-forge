import { describe, expect, it } from "vitest";

import {
  MAX_STUDIO_ASSET_ACCEPTANCE_ITEMS,
  validateStudioCreationAssetAcceptanceResults,
} from "../../src/shared/studio-api";

const acceptanceResults = (count: number, evidenceCount = 1) =>
  Array.from({ length: count }, (_, index) => ({
    criterionIndex: index,
    criterionSha256: (index + 1).toString(16).padStart(64, "0"),
    status: "passed" as const,
    evidenceHashes: Array.from({ length: evidenceCount }, (_unused, evidenceIndex) =>
      (evidenceIndex + 1).toString(16).padStart(64, "0"),
    ),
  }));

describe("shared Studio asset acceptance contract", () => {
  it("accepts exactly 64 criteria and evidence hashes and rejects 65", () => {
    expect(MAX_STUDIO_ASSET_ACCEPTANCE_ITEMS).toBe(64);
    expect(validateStudioCreationAssetAcceptanceResults(acceptanceResults(64))).toHaveLength(64);
    expect(() => validateStudioCreationAssetAcceptanceResults(acceptanceResults(65))).toThrow(
      /acceptance results/u,
    );
    expect(
      validateStudioCreationAssetAcceptanceResults(acceptanceResults(1, 64))[0].evidenceHashes,
    ).toHaveLength(64);
    expect(() => validateStudioCreationAssetAcceptanceResults(acceptanceResults(1, 65))).toThrow(
      /criterion evidence/u,
    );
  });

  it("rejects noncanonical indices, hashes, evidence order, and extra fields", () => {
    expect(() =>
      validateStudioCreationAssetAcceptanceResults([
        { ...acceptanceResults(1)[0], criterionIndex: 1 },
      ]),
    ).toThrow(/criterion index/u);
    expect(() =>
      validateStudioCreationAssetAcceptanceResults([
        { ...acceptanceResults(1)[0], criterionSha256: "not-a-sha" },
      ]),
    ).toThrow(/criterion hash/u);
    expect(() =>
      validateStudioCreationAssetAcceptanceResults([
        { ...acceptanceResults(1, 2)[0], evidenceHashes: ["2".repeat(64), "1".repeat(64)] },
      ]),
    ).toThrow(/criterion evidence/u);
    expect(() =>
      validateStudioCreationAssetAcceptanceResults([
        { ...acceptanceResults(1)[0], provider: "renderer-controlled" },
      ]),
    ).toThrow(/fields/u);

    expect(() =>
      validateStudioCreationAssetAcceptanceResults(new Array(1)),
    ).toThrow(/acceptance result/u);
    const sparseEvidence = acceptanceResults(1);
    sparseEvidence[0].evidenceHashes = new Array<string>(1);
    expect(() =>
      validateStudioCreationAssetAcceptanceResults(sparseEvidence),
    ).toThrow(/criterion evidence/u);
  });
});

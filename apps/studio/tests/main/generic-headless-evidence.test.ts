import { readFile } from "node:fs/promises";
import path from "node:path";

import { describe, expect, it } from "vitest";

import {
  canonicalGenericHeadlessContentHash,
  canonicalGenericHeadlessId,
} from "../../scripts/generic-headless-validation.mjs";
import {
  GENERIC_HEADLESS_INSPECTOR_RUNTIME,
  buildGenericHeadlessPythonInvocation,
  hasVerifiedGenericHeadlessPythonResult,
  inspectGenericHeadlessContract,
  validateGenericHeadlessContract,
} from "../../src/main/generic-headless-evidence";

const repositoryRoot = path.resolve(import.meta.dirname, "../../../..");
const fixturePath =
  "examples/multigenre-contracts/abstract-puzzle/runtime/headless/" +
  "execution-script.json";

async function fixture(): Promise<Record<string, unknown>> {
  return JSON.parse(
    await readFile(path.join(repositoryRoot, fixturePath), "utf8"),
  ) as Record<string, unknown>;
}

describe("generic headless contract inspection", () => {
  it("inspects immutable scripts without claiming gameplay execution", async () => {
    const document = await fixture();
    const validated = validateGenericHeadlessContract(document);
    expect(validated).not.toBeNull();
    expect(Object.isFrozen(validated)).toBe(true);
    expect(inspectGenericHeadlessContract(validated)).toEqual({
      content_hash: document.content_hash,
      format: "world-forge.game_execution_script",
      id: document.script_id,
      semantic_verification: "required_python",
      status: "structurally_valid",
    });
    expect(GENERIC_HEADLESS_INSPECTOR_RUNTIME).toEqual({
      contract_formats: [
        "world-forge.game_execution_script",
        "world-forge.headless_evidence_set",
        "world-forge.headless_execution_receipt",
      ],
      format: "world-forge.studio_internal_headless_inspector",
      format_version: 1,
      interprets_gameplay: false,
      semantic_boundary: "packaged_python_required",
    });
  });

  it("rejects a self-resealed noncanonical scenario order", async () => {
    const document = structuredClone(await fixture());
    const scenarios = document.scenarios;
    if (!Array.isArray(scenarios)) {
      throw new Error("fixture scenarios are missing");
    }
    scenarios.reverse();
    document.script_id = canonicalGenericHeadlessId(document);
    document.content_hash =
      canonicalGenericHeadlessContentHash(document);
    expect(validateGenericHeadlessContract(document)).toBeNull();
  });

  it("routes semantic execution and evidence verification through packaged Python", () => {
    expect(
      buildGenericHeadlessPythonInvocation({
        bundleRoot: "/tmp/world-forge/bundle",
        mode: "execute",
        outputRoot: "/tmp/world-forge/evidence",
        pythonExecutable: "/tmp/world-forge/python",
        source: "/tmp/world-forge/script.json",
      }),
    ).toEqual({
      args: [
        "-I",
        "-B",
        "-m",
        "worldforge",
        "verify-game-headless",
        "/tmp/world-forge/bundle",
        "/tmp/world-forge/script.json",
        "--output",
        "/tmp/world-forge/evidence",
      ],
      executable: "/tmp/world-forge/python",
    });
    expect(
      buildGenericHeadlessPythonInvocation({
        bundleRoot: "/tmp/world-forge/bundle",
        mode: "evidence",
        pythonExecutable: "/tmp/world-forge/python",
        source: "/tmp/world-forge/evidence",
      }),
    ).toEqual({
      args: [
        "-I",
        "-B",
        "-m",
        "worldforge",
        "verify-game-headless-evidence",
        "/tmp/world-forge/evidence",
        "--bundle",
        "/tmp/world-forge/bundle",
      ],
      executable: "/tmp/world-forge/python",
    });
  });

  it("accepts the exact successful Python CLI result contract", () => {
    const result = {
      content_hash: "a".repeat(64),
      evidence_set_id: `headless_evidence_set_${"b".repeat(40)}`,
      execution_status: "headless_verified",
      integrity: "valid",
      path: path.resolve(repositoryRoot, "headless-evidence"),
      release: "blocked",
      supported: false,
    };
    expect(hasVerifiedGenericHeadlessPythonResult(result)).toBe(true);
    for (const tampered of [
      { ...result, content_hash: "0".repeat(63) },
      { ...result, evidence_set_id: "headless_evidence_set_wrong" },
      { ...result, execution_status: "native_verified" },
      { ...result, integrity: "invalid" },
      { ...result, path: "relative/evidence" },
      { ...result, release: "ready" },
      { ...result, supported: true },
      { ...result, extra: true },
    ]) {
      expect(hasVerifiedGenericHeadlessPythonResult(tampered)).toBe(false);
    }
  });
});

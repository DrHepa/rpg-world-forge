import { createHash, createHmac } from "node:crypto";

import { describe, expect, it, vi } from "vitest";

import inventoryFixture from "../../../../examples/multigenre-contracts/abstract-puzzle/assets/inventory.json";
import licenseFixture from "../../../../examples/multigenre-contracts/abstract-puzzle/assets/production/board_ui/license.json";
import manifestFixture from "../../../../examples/multigenre-contracts/abstract-puzzle/assets/manifest.json";
import processingReceiptFixture from "../../../../examples/multigenre-contracts/abstract-puzzle/assets/production/board_ui/processing-receipt.json";
import processingRecipeFixture from "../../../../examples/multigenre-contracts/abstract-puzzle/assets/production/board_ui/recipe.json";
import productionReceiptFixture from "../../../../examples/multigenre-contracts/abstract-puzzle/assets/production/board_ui/receipt.json";
import productionRequestFixture from "../../../../examples/multigenre-contracts/abstract-puzzle/assets/production/board_ui/request.json";
import provenanceFixture from "../../../../examples/multigenre-contracts/abstract-puzzle/assets/production/board_ui/provenance.json";
import qaReportFixture from "../../../../examples/multigenre-contracts/abstract-puzzle/assets/production/board_ui/qa-report.json";
import selectionFixture from "../../../../examples/multigenre-contracts/abstract-puzzle/assets/production/board_ui/selection.json";
import narrativeReceiptFixture from "../../../../examples/multigenre-contracts/branching-narrative/assets/production/narrative_ui_font/receipt.json";
import specificationFixture from "../../../../examples/multigenre-contracts/branching-narrative/assets/specs/narrative_ui_font.json";
import styleFixture from "../../../../examples/multigenre-contracts/abstract-puzzle/assets/style.json";
import subjectFixture from "../../../../examples/multigenre-contracts/abstract-puzzle/assets/subject.json";
import targetFixture from "../../../../examples/multigenre-contracts/abstract-puzzle/assets/target.json";
import { canonicalGenericAssetContentHash } from "../../scripts/generic-asset-validation.mjs";
import { validateGenericAssetContract } from "../../src/main/generic-asset-contracts";

const JWT_TEST_KEY = "world-forge-d2a-fixture-key";
const BASE64URL_ALPHABET =
  "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";

function reseal<T extends { content_hash: string }>(value: T): T {
  const digest = canonicalGenericAssetContentHash(value);
  if (digest === null) {
    throw new Error("Test contract is not canonical JSON");
  }
  value.content_hash = digest;
  return value;
}

function expectDeepFrozenJson(value: unknown): void {
  const pending: unknown[] = [value];
  while (pending.length > 0) {
    const current = pending.pop();
    if (current === null || typeof current !== "object") {
      continue;
    }
    expect(Object.isFrozen(current)).toBe(true);
    for (const child of Object.values(current)) {
      pending.push(child);
    }
  }
}

function validHs256Jwt(headerPadding: number, payloadPadding = 8): string {
  const header = Buffer.from(
    JSON.stringify({
      alg: "HS256",
      pad: "x".repeat(headerPadding),
      typ: "JWT",
    }),
    "utf8",
  ).toString("base64url");
  const payload = Buffer.from(
    JSON.stringify({
      pad: "x".repeat(payloadPadding),
      sub: "fixture",
    }),
    "utf8",
  ).toString("base64url");
  const signingInput = `${header}.${payload}`;
  const signature = createHmac("sha256", JWT_TEST_KEY)
    .update(signingInput, "ascii")
    .digest("base64url");
  return `${signingInput}.${signature}`;
}

function validNonCanonicalHs256Jwt(): {
  canonical: string;
  noncanonical: string;
} {
  const headerBytes = Buffer.from(
    JSON.stringify({ alg: "HS256", x: "a" }),
    "utf8",
  );
  const canonicalHeader = headerBytes.toString("base64url");
  let noncanonicalHeader: string | null = null;
  for (const suffix of BASE64URL_ALPHABET) {
    const candidate = `${canonicalHeader.slice(0, -1)}${suffix}`;
    if (
      candidate !== canonicalHeader &&
      Buffer.from(candidate, "base64url").equals(headerBytes)
    ) {
      noncanonicalHeader = candidate;
      break;
    }
  }
  if (noncanonicalHeader === null) {
    throw new Error("Unable to construct a noncanonical base64url JWT header");
  }
  const payload = Buffer.from(
    JSON.stringify({ sub: "fixture" }),
    "utf8",
  ).toString("base64url");
  const sign = (header: string): string => {
    const signingInput = `${header}.${payload}`;
    const signature = createHmac("sha256", JWT_TEST_KEY)
      .update(signingInput, "ascii")
      .digest("base64url");
    return `${signingInput}.${signature}`;
  };
  return {
    canonical: sign(canonicalHeader),
    noncanonical: sign(noncanonicalHeader),
  };
}

function observePrototypeRead<T>(
  prototype: object,
  key: PropertyKey,
  replacement: unknown,
  operation: () => T,
): { calls: number; result: T } {
  const previous = Object.getOwnPropertyDescriptor(prototype, key);
  let calls = 0;
  let result!: T;
  const pollutedGetter = () => {
    calls += 1;
    return replacement;
  };
  Object.defineProperty(prototype, key, {
    configurable: true,
    get: pollutedGetter,
  });
  try {
    result = operation();
    const restored = Object.getOwnPropertyDescriptor(prototype, key);
    if (restored?.get !== pollutedGetter) {
      throw new Error(`Validation leaked prototype mutation for ${String(key)}`);
    }
  } finally {
    if (previous === undefined) {
      Reflect.deleteProperty(prototype, key);
    } else {
      Object.defineProperty(prototype, key, previous);
    }
  }
  return { calls, result };
}

function observeSharedPrototypeMutations<T>(
  operation: () => T,
): {
  events: string[];
  preserved: boolean;
  reentrantCalls: number;
  result: T;
} {
  const originalObjectDefineProperty = Object.defineProperty;
  const originalObjectDefineProperties = Object.defineProperties;
  const originalObjectFreeze = Object.freeze;
  const originalObjectPreventExtensions = Object.preventExtensions;
  const originalObjectSeal = Object.seal;
  const originalObjectSetPrototypeOf = Object.setPrototypeOf;
  const originalReflectDefineProperty = Reflect.defineProperty;
  const originalReflectDeleteProperty = Reflect.deleteProperty;
  const originalReflectPreventExtensions = Reflect.preventExtensions;
  const originalReflectSetPrototypeOf = Reflect.setPrototypeOf;
  const pollutionKey = Symbol("world-forge-reentrant-prototype-observer");
  const events: string[] = [];
  let reentrantCalls = 0;
  let observingReentry = false;

  const observe = (kind: string, target: object): void => {
    if (target !== Object.prototype && target !== Array.prototype) {
      return;
    }
    events.push(kind);
    if (!observingReentry) {
      observingReentry = true;
      reentrantCalls += 1;
      const nested = structuredClone(targetFixture) as Record<string, unknown>;
      delete nested.format_version;
      expect(validateGenericAssetContract(nested)).toBeNull();
      observingReentry = false;
    }
  };

  originalObjectDefineProperty(Object.prototype, pollutionKey, {
    configurable: true,
    enumerable: false,
    value: "ambient",
    writable: true,
  });
  Object.defineProperty = ((target: object, key: PropertyKey, descriptor: PropertyDescriptor) => {
    observe(`Object.defineProperty:${String(key)}`, target);
    return originalObjectDefineProperty(target, key, descriptor);
  }) as typeof Object.defineProperty;
  Object.defineProperties = ((target: object, descriptors: PropertyDescriptorMap) => {
    observe("Object.defineProperties", target);
    return originalObjectDefineProperties(target, descriptors);
  }) as typeof Object.defineProperties;
  Object.freeze = ((target: object) => {
    observe("Object.freeze", target);
    return originalObjectFreeze(target);
  });
  Object.preventExtensions = ((target: object) => {
    observe("Object.preventExtensions", target);
    return originalObjectPreventExtensions(target);
  }) as typeof Object.preventExtensions;
  Object.seal = ((target: object) => {
    observe("Object.seal", target);
    return originalObjectSeal(target);
  }) as typeof Object.seal;
  Object.setPrototypeOf = ((target: object, prototype: object | null) => {
    observe("Object.setPrototypeOf", target);
    originalObjectSetPrototypeOf(target, prototype);
    return target;
  }) as typeof Object.setPrototypeOf;
  Reflect.defineProperty = ((target: object, key: PropertyKey, descriptor: PropertyDescriptor) => {
    observe(`Reflect.defineProperty:${String(key)}`, target);
    return originalReflectDefineProperty(target, key, descriptor);
  }) as typeof Reflect.defineProperty;
  Reflect.deleteProperty = ((target: object, key: PropertyKey) => {
    observe(`Reflect.deleteProperty:${String(key)}`, target);
    return originalReflectDeleteProperty(target, key);
  });
  Reflect.preventExtensions = ((target: object) => {
    observe("Reflect.preventExtensions", target);
    return originalReflectPreventExtensions(target);
  });
  Reflect.setPrototypeOf = ((target: object, prototype: object | null) => {
    observe("Reflect.setPrototypeOf", target);
    return originalReflectSetPrototypeOf(target, prototype);
  });

  try {
    const result = operation();
    const descriptor = Object.getOwnPropertyDescriptor(
      Object.prototype,
      pollutionKey,
    );
    return {
      events,
      preserved:
        descriptor !== undefined &&
        "value" in descriptor &&
        descriptor.value === "ambient",
      reentrantCalls,
      result,
    };
  } finally {
    Object.defineProperty = originalObjectDefineProperty;
    Object.defineProperties = originalObjectDefineProperties;
    Object.freeze = originalObjectFreeze;
    Object.preventExtensions = originalObjectPreventExtensions;
    Object.seal = originalObjectSeal;
    Object.setPrototypeOf = originalObjectSetPrototypeOf;
    Reflect.defineProperty = originalReflectDefineProperty;
    Reflect.deleteProperty = originalReflectDeleteProperty;
    Reflect.preventExtensions = originalReflectPreventExtensions;
    Reflect.setPrototypeOf = originalReflectSetPrototypeOf;
    originalReflectDeleteProperty(Object.prototype, pollutionKey);
  }
}

describe("generic asset contract boundary", () => {
  it("admits only strict-AJV validated, detached, frozen generic contracts", () => {
    for (const fixture of [
      subjectFixture,
      targetFixture,
      styleFixture,
      inventoryFixture,
      specificationFixture,
      productionRequestFixture,
      productionReceiptFixture,
      selectionFixture,
      provenanceFixture,
      licenseFixture,
      processingRecipeFixture,
      processingReceiptFixture,
      qaReportFixture,
      manifestFixture,
    ]) {
      const validated = validateGenericAssetContract(fixture);
      expect(validated).not.toBeNull();
      expect(validated).not.toBe(fixture);
      expect(Object.isFrozen(validated)).toBe(true);
      expectDeepFrozenJson(validated);
      expect(canonicalGenericAssetContentHash(validated)).toBe(
        fixture.content_hash,
      );
    }

    const unexpected = {
      ...targetFixture,
      unexpected_field: true,
    };
    expect(validateGenericAssetContract(unexpected)).toBeNull();

    const inherited = Object.create(subjectFixture) as Record<string, unknown>;
    inherited.format = subjectFixture.format;
    expect(validateGenericAssetContract(inherited)).toBeNull();
  });

  it.each([
    [1, false],
    [2, true],
    [49, true],
    [56, true],
    [64, true],
    [65, false],
    [100, false],
  ])("enforces the exact generic identifier domain at length %i", (length, accepted) => {
    const candidate = structuredClone(targetFixture);
    candidate.target_id = `a${"b".repeat(Math.max(0, length - 1))}`;
    expect(validateGenericAssetContract(candidate) !== null).toBe(accepted);
  });

  it("enforces canonical glyph ranges and portable runtime paths", () => {
    for (const ranges of [
      ["U+-"],
      ["U+007F-0020"],
      ["U+0020-007E", "U+007E-00FF"],
      ["U+00000-0007E"],
      ["U+10FFFF-110000"],
    ]) {
      const candidate = structuredClone(specificationFixture);
      candidate.outputs[0].expectations.glyph_ranges = ranges;
      expect(validateGenericAssetContract(candidate)).toBeNull();
    }

    for (const ranges of [
      ["U+0000-0000"],
      ["U+0020-007E", "U+10000-10FFFF"],
      ["U+10FFFF-10FFFF"],
    ]) {
      const candidate = structuredClone(specificationFixture);
      candidate.outputs[0].expectations.glyph_ranges = ranges;
      expect(validateGenericAssetContract(candidate)).not.toBeNull();
    }

    for (const runtimePath of [
      "assets/fonts./font.ttf",
      "assets/CON/font.ttf",
      "assets/fonts /font.ttf",
    ]) {
      const candidate = structuredClone(specificationFixture);
      candidate.outputs[0].runtime_path = runtimePath;
      expect(validateGenericAssetContract(candidate)).toBeNull();
    }
  });

  it("enforces the exact short runtime string bounds", () => {
    for (const field of ["camera", "density"] as const) {
      const candidate = structuredClone(styleFixture);
      if (field === "camera") {
        candidate.visual.camera = "a".repeat(256);
      } else {
        candidate.visual.ui.density = "a".repeat(256);
      }
      expect(validateGenericAssetContract(candidate)).not.toBeNull();
      if (field === "camera") {
        candidate.visual.camera += "a";
      } else {
        candidate.visual.ui.density += "a";
      }
      expect(validateGenericAssetContract(candidate)).toBeNull();
    }

    const jsonExpectation = {
      ...structuredClone(specificationFixture),
      outputs: [
        {
          role: "localized_text",
          media_type: "application/json",
          runtime_path: "assets/text/localized.json",
          expectations: {
            kind: "schema_json",
            schema_id: "a".repeat(256),
            schema_version: 1,
            max_records: 1,
            max_bytes: 1024,
          },
        },
      ],
    };
    expect(validateGenericAssetContract(jsonExpectation)).not.toBeNull();
    jsonExpectation.outputs[0].expectations.schema_id += "a";
    expect(validateGenericAssetContract(jsonExpectation)).toBeNull();
  });

  it("enforces the exact 64-item specification criteria and QA evidence bounds", () => {
    const specification = structuredClone(specificationFixture) as unknown as {
      acceptance_criteria: string[];
      content_hash: string;
    };
    specification.acceptance_criteria = Array.from(
      { length: 64 },
      (_, index) => `Criterion ${String(index).padStart(3, "0")} remains exact.`,
    );
    expect(validateGenericAssetContract(reseal(specification))).not.toBeNull();
    specification.acceptance_criteria.push("Criterion 064 remains exact.");
    expect(validateGenericAssetContract(reseal(specification))).toBeNull();

    const qaReport = structuredClone(qaReportFixture) as unknown as {
      acceptance_criteria: Array<{
        criterion_index: number;
        criterion_sha256: string;
        evidence_hashes: string[];
        status: "failed" | "passed";
      }>;
      content_hash: string;
    };
    qaReport.acceptance_criteria = Array.from({ length: 64 }, (_, index) => ({
      criterion_index: index,
      criterion_sha256: createHash("sha256")
        .update(`criterion:${String(index)}`, "utf8")
        .digest("hex"),
      evidence_hashes: [(index + 1).toString(16).padStart(64, "0")],
      status: "passed" as const,
    }));
    expect(validateGenericAssetContract(reseal(qaReport))).not.toBeNull();
    qaReport.acceptance_criteria.push({
      criterion_index: 64,
      criterion_sha256: createHash("sha256")
        .update("criterion:64", "utf8")
        .digest("hex"),
      evidence_hashes: [(65).toString(16).padStart(64, "0")],
      status: "passed",
    });
    expect(validateGenericAssetContract(reseal(qaReport))).toBeNull();

    const qaEvidence = structuredClone(qaReportFixture) as unknown as {
      acceptance_criteria: Array<{ evidence_hashes: string[] }>;
      content_hash: string;
    };
    qaEvidence.acceptance_criteria[0].evidence_hashes = Array.from(
      { length: 64 },
      (_, index) => (index + 1).toString(16).padStart(64, "0"),
    );
    expect(validateGenericAssetContract(reseal(qaEvidence))).not.toBeNull();
    qaEvidence.acceptance_criteria[0].evidence_hashes.push(
      (65).toString(16).padStart(64, "0"),
    );
    expect(validateGenericAssetContract(reseal(qaEvidence))).toBeNull();
  });

  it("closes production toolchains, receipt status, and authoring locators", () => {
    const toolchains = [
      {
        production_class: "human",
        reproducibility: {
          mode: "reviewed_nondeterministic",
          seed_policy: "forbidden",
        },
        toolchain_requirements: {
          production_class: "human",
          creator_id: "fixture_artist",
          operation_id: "generate_png",
          work_attestation_hash: "1".repeat(64),
        },
      },
      {
        production_class: "procedural_offline",
        reproducibility: {
          mode: "deterministic",
          seed_policy: "fixed",
        },
        toolchain_requirements: productionRequestFixture.toolchain_requirements,
      },
      {
        production_class: "external_authoring",
        reproducibility: {
          mode: "reviewed_nondeterministic",
          seed_policy: "forbidden",
        },
        toolchain_requirements: {
          production_class: "external_authoring",
          tool_id: "fixture_editor",
          tool_version: "1.0.0",
          operation_id: "generate_png",
        },
      },
      {
        production_class: "generative_authoring",
        reproducibility: {
          mode: "deterministic",
          seed_policy: "fixed",
        },
        toolchain_requirements: {
          production_class: "generative_authoring",
          provider_id: "fixture_provider",
          tool_id: "fixture_generator",
          tool_version: "1.0.0",
          operation_id: "generate_png",
          model_id: "fixture_model",
          model_version: "1.0.0",
          weights_id: "fixture_weights",
          weights_version: "1.0.0",
          dataset_ids: ["fixture_dataset"],
          seed_policy: "fixed",
          seed: 7,
          instruction_artifact_hash: "2".repeat(64),
        },
      },
    ];
    for (const toolchain of toolchains) {
      const candidate = {
        ...structuredClone(productionRequestFixture),
        ...toolchain,
      };
      reseal(candidate);
      expect(validateGenericAssetContract(candidate)).not.toBeNull();
    }

    const crossedToolchain = structuredClone(productionRequestFixture);
    Object.assign(crossedToolchain.toolchain_requirements, {
      model_id: "invented_model",
    });
    reseal(crossedToolchain);
    expect(validateGenericAssetContract(crossedToolchain)).toBeNull();

    const traversal = structuredClone(productionRequestFixture) as Record<
      string,
      unknown
    >;
    traversal.input_artifacts = [
      {
        artifact_id: "unsafe_input",
        role: "reference",
        locator: "../secret.png",
        size_bytes: 1,
        sha256: "1".repeat(64),
      },
    ];
    reseal(traversal as typeof productionRequestFixture);
    expect(validateGenericAssetContract(traversal)).toBeNull();

    const failedWithOutputs = structuredClone(productionReceiptFixture) as Record<
      string,
      unknown
    >;
    failedWithOutputs.status = "failed";
    failedWithOutputs.failure_reasons = ["candidate_generation_failed"];
    reseal(failedWithOutputs as typeof productionReceiptFixture);
    expect(validateGenericAssetContract(failedWithOutputs)).toBeNull();

    const promptField = {
      ...selectionFixture,
      prompt: "Never accepted at this boundary.",
    };
    expect(validateGenericAssetContract(promptField)).toBeNull();
  });

  it("binds every D2 document to its exact canonical content hash", () => {
    for (const [fixture, idField] of [
      [productionRequestFixture, "request_id"],
      [productionReceiptFixture, "receipt_id"],
      [selectionFixture, "selection_id"],
      [provenanceFixture, "provenance_id"],
      [licenseFixture, "license_record_id"],
      [processingRecipeFixture, "recipe_id"],
      [processingReceiptFixture, "processing_receipt_id"],
      [qaReportFixture, "qa_report_id"],
      [manifestFixture, "manifest_id"],
    ] as const) {
      const candidate = structuredClone(fixture) as Record<string, unknown> & {
        content_hash: string;
      };
      candidate[idField] = `${String(candidate[idField])}_changed`;
      expect(validateGenericAssetContract(candidate)).toBeNull();
      reseal(candidate);
      expect(validateGenericAssetContract(candidate)).not.toBeNull();
    }
  });

  it("rejects contradictory processing, QA, and manifest states", () => {
    const completedWithFailure = structuredClone(processingReceiptFixture);
    (
      completedWithFailure as unknown as { failure_reasons: string[] }
    ).failure_reasons = ["processing_failed"];
    reseal(completedWithFailure);
    expect(validateGenericAssetContract(completedWithFailure)).toBeNull();

    const crossedCheck = structuredClone(qaReportFixture);
    crossedCheck.outputs[0].checks[4].status = "not_applicable";
    reseal(crossedCheck);
    expect(validateGenericAssetContract(crossedCheck)).toBeNull();

    const passedWithFailedCriterion = structuredClone(qaReportFixture);
    passedWithFailedCriterion.acceptance_criteria[0].status = "failed";
    (
      passedWithFailedCriterion as unknown as { blockers: string[] }
    ).blockers = ["acceptance_criterion_0_failed"];
    reseal(passedWithFailedCriterion);
    expect(validateGenericAssetContract(passedWithFailedCriterion)).toBeNull();

    const crossedManifestState = structuredClone(manifestFixture);
    crossedManifestState.assets[0].state = "processed";
    reseal(crossedManifestState);
    expect(validateGenericAssetContract(crossedManifestState)).toBeNull();
  });

  it("enforces complete D2b recipe, recovery, QA, and manifest semantics", () => {
    const boundRecipe = structuredClone(processingRecipeFixture) as unknown as {
      content_hash: string;
      licenses: Array<{
        candidate_artifact_id: string;
        role: string;
        license_record: Record<string, unknown>;
      }>;
      steps: Array<{
        candidate_artifact_id: string;
        role: string;
        source_locator: string;
        runtime_path: string;
        license_record: Record<string, unknown>;
      }>;
    };
    for (const collection of [boundRecipe.licenses, boundRecipe.steps]) {
      for (const item of collection) {
        item.license_record.candidate_artifact_id = item.candidate_artifact_id;
        item.license_record.role = item.role;
      }
    }
    reseal(boundRecipe);
    expect(validateGenericAssetContract(boundRecipe)).not.toBeNull();

    const crossedLicense = structuredClone(boundRecipe);
    crossedLicense.steps[0].license_record.candidate_artifact_id =
      "crossed_candidate";
    crossedLicense.licenses[0].license_record.candidate_artifact_id =
      "crossed_candidate";
    reseal(crossedLicense);
    expect(validateGenericAssetContract(crossedLicense)).toBeNull();

    const pathCollision = structuredClone(boundRecipe);
    pathCollision.steps[0].runtime_path =
      pathCollision.steps[0].source_locator;
    reseal(pathCollision);
    expect(validateGenericAssetContract(pathCollision)).toBeNull();

    const failedReceipt = structuredClone(processingReceiptFixture) as unknown as {
      content_hash: string;
      failure_reasons: string[];
      outputs: unknown[];
      recipe: Record<string, unknown>;
      recovery: null | {
        failure_code: string;
        recipe: Record<string, unknown>;
        retained_artifacts: unknown[];
        content_hash: string;
      };
      status: string;
    };
    failedReceipt.status = "failed";
    failedReceipt.outputs = [];
    failedReceipt.failure_reasons = ["processor_interrupted"];
    failedReceipt.recovery = {
      failure_code: "processor_interrupted",
      recipe: structuredClone(failedReceipt.recipe),
      retained_artifacts: [],
      content_hash: "",
    };
    reseal(failedReceipt.recovery);
    reseal(failedReceipt);
    expect(validateGenericAssetContract(failedReceipt)).not.toBeNull();

    failedReceipt.recovery.failure_code = "different_failure";
    reseal(failedReceipt.recovery);
    reseal(failedReceipt);
    expect(validateGenericAssetContract(failedReceipt)).toBeNull();

    const failedQa = structuredClone(qaReportFixture) as unknown as {
      blockers: string[];
      content_hash: string;
      outputs: Array<{
        role: string;
        metadata: unknown;
        checks: Array<{ check_id: string; status: string }>;
      }>;
      status: string;
    };
    failedQa.status = "failed";
    failedQa.outputs[0].metadata = null;
    for (const check of failedQa.outputs[0].checks) {
      if (["hash", "media", "png"].includes(check.check_id)) {
        check.status = "failed";
      }
    }
    failedQa.blockers = [
      "output_texture_hash_failed",
      "output_texture_media_failed",
      "output_texture_png_failed",
    ];
    reseal(failedQa);
    expect(validateGenericAssetContract(failedQa)).not.toBeNull();

    failedQa.blockers = ["output_texture_hash_failed"];
    reseal(failedQa);
    expect(validateGenericAssetContract(failedQa)).toBeNull();

    const crossedManifestPaths = structuredClone(manifestFixture) as unknown as {
      assets: Array<{
        asset: { asset_id: string; content_hash: string };
        outputs: Array<{ runtime_path: string; locator: string }>;
      }>;
      content_hash: string;
    };
    const duplicateAsset = structuredClone(crossedManifestPaths.assets[0]);
    duplicateAsset.asset.asset_id = "second_manifest_asset";
    duplicateAsset.asset.content_hash = "f".repeat(64);
    crossedManifestPaths.assets.push(duplicateAsset);
    crossedManifestPaths.assets.sort((left, right) =>
      Buffer.compare(
        Buffer.from(left.asset.asset_id, "utf8"),
        Buffer.from(right.asset.asset_id, "utf8"),
      ),
    );
    reseal(crossedManifestPaths);
    expect(validateGenericAssetContract(crossedManifestPaths)).toBeNull();
  });

  it("enforces production request operation and reproducibility coherence", () => {
    const mismatchedOperation = structuredClone(productionRequestFixture);
    mismatchedOperation.toolchain_requirements.operation_id = "different_operation";
    reseal(mismatchedOperation);
    expect(validateGenericAssetContract(mismatchedOperation)).toBeNull();

    const humanWithSeed = {
      ...structuredClone(productionRequestFixture),
      production_class: "human",
      reproducibility: {
        mode: "reviewed_nondeterministic",
        seed_policy: "fixed",
      },
      toolchain_requirements: {
        production_class: "human",
        creator_id: "fixture_artist",
        operation_id: "generate_png",
        work_attestation_hash: "1".repeat(64),
      },
    };
    reseal(humanWithSeed);
    expect(validateGenericAssetContract(humanWithSeed)).toBeNull();

    const proceduralWithoutFixedSeed = structuredClone(productionRequestFixture);
    (
      proceduralWithoutFixedSeed.toolchain_requirements as {
        seed: number | null;
      }
    ).seed = null;
    reseal(proceduralWithoutFixedSeed);
    expect(validateGenericAssetContract(proceduralWithoutFixedSeed)).toBeNull();

    const proceduralWithoutRecordedSeed = structuredClone(productionRequestFixture);
    proceduralWithoutRecordedSeed.reproducibility.seed_policy = "recorded";
    (
      proceduralWithoutRecordedSeed.toolchain_requirements as {
        seed: number | null;
      }
    ).seed = null;
    reseal(proceduralWithoutRecordedSeed);
    expect(validateGenericAssetContract(proceduralWithoutRecordedSeed)).toBeNull();

    const proceduralWithRecordedSeed = structuredClone(productionRequestFixture);
    proceduralWithRecordedSeed.reproducibility.seed_policy = "recorded";
    reseal(proceduralWithRecordedSeed);
    expect(validateGenericAssetContract(proceduralWithRecordedSeed)).not.toBeNull();

    const generativeWithCrossedSeedPolicy = {
      ...structuredClone(productionRequestFixture),
      production_class: "generative_authoring",
      reproducibility: {
        mode: "deterministic",
        seed_policy: "fixed",
      },
      toolchain_requirements: {
        production_class: "generative_authoring",
        provider_id: "fixture_provider",
        tool_id: "fixture_generator",
        tool_version: "1.0.0",
        operation_id: "generate_png",
        model_id: "fixture_model",
        model_version: "1.0.0",
        weights_id: "fixture_weights",
        weights_version: "1.0.0",
        dataset_ids: ["fixture_dataset"],
        seed_policy: "recorded",
        seed: 7,
        instruction_artifact_hash: "2".repeat(64),
      },
    };
    reseal(generativeWithCrossedSeedPolicy);
    expect(validateGenericAssetContract(generativeWithCrossedSeedPolicy)).toBeNull();

    const excessiveOperationVersion = structuredClone(productionRequestFixture);
    excessiveOperationVersion.operation.version = 65_536;
    reseal(excessiveOperationVersion);
    expect(validateGenericAssetContract(excessiveOperationVersion)).toBeNull();

    const incompleteOutput = structuredClone(productionRequestFixture) as Record<
      string,
      unknown
    > & { content_hash: string };
    incompleteOutput.expected_outputs = [
      {
        role: "texture",
        media_type: "image/png",
        runtime_path: "assets/ui/board.png",
      },
    ];
    reseal(incompleteOutput);
    expect(validateGenericAssetContract(incompleteOutput)).toBeNull();
  });

  it("enforces canonical collection and portable path-tree policies", () => {
    const noncanonicalDatasets = {
      ...structuredClone(productionRequestFixture),
      production_class: "generative_authoring",
      reproducibility: {
        mode: "deterministic",
        seed_policy: "fixed",
      },
      toolchain_requirements: {
        production_class: "generative_authoring",
        provider_id: "fixture_provider",
        tool_id: "fixture_generator",
        tool_version: "1.0.0",
        operation_id: "generate_png",
        model_id: "fixture_model",
        model_version: "1.0.0",
        weights_id: "fixture_weights",
        weights_version: "1.0.0",
        dataset_ids: ["dataset_z", "dataset_a"],
        seed_policy: "fixed",
        seed: 7,
        instruction_artifact_hash: "2".repeat(64),
      },
    };
    reseal(noncanonicalDatasets);
    expect(validateGenericAssetContract(noncanonicalDatasets)).toBeNull();

    for (const field of ["sanitized_log_hashes", "rights_evidence_hashes"] as const) {
      const receipt = structuredClone(productionReceiptFixture);
      if (field === "sanitized_log_hashes") {
        receipt.execution_evidence.sanitized_log_hashes = [
          "b".repeat(64),
          "a".repeat(64),
        ];
      } else {
        receipt.rights_attestation.evidence_hashes = [
          "b".repeat(64),
          "a".repeat(64),
        ];
      }
      reseal(receipt);
      expect(validateGenericAssetContract(receipt)).toBeNull();
    }

    const selection = structuredClone(selectionFixture);
    selection.review.evidence_hashes = ["b".repeat(64), "a".repeat(64)];
    reseal(selection);
    expect(validateGenericAssetContract(selection)).toBeNull();

    const duplicateSelectedHash = structuredClone(selectionFixture);
    duplicateSelectedHash.selected_outputs.push({
      ...duplicateSelectedHash.selected_outputs[0],
      candidate_artifact_id: "second_candidate",
      role: "z_texture",
    });
    reseal(duplicateSelectedHash);
    expect(validateGenericAssetContract(duplicateSelectedHash)).toBeNull();

    for (const locators of [
      ["inputs/Straße", "inputs/STRASSE"],
      ["inputs/Σ", "inputs/ς"],
      ["inputs/file", "inputs/file/child"],
    ]) {
      const request = structuredClone(productionRequestFixture) as Omit<
        typeof productionRequestFixture,
        "input_artifacts"
      > & {
        input_artifacts: Array<{
          artifact_id: string;
          role: string;
          locator: string;
          size_bytes: number;
          sha256: string;
        }>;
      };
      request.input_artifacts = locators.map((locator, index) => ({
        artifact_id: `input_${String(index)}`,
        role: "reference",
        locator,
        size_bytes: 1,
        sha256: String(index + 1).repeat(64),
      }));
      reseal(request);
      expect(validateGenericAssetContract(request)).toBeNull();
    }
  });

  it("requires one exact receipt-lineage closure for every selected or rejected root", () => {
    const rejectedReceipt = {
      format: "world-forge.asset_production_receipt" as const,
      format_version: 1 as const,
      id: "rejected_receipt",
      content_hash: "b".repeat(64),
    };
    const complete = structuredClone(selectionFixture);
    (
      complete.rejected_candidates as unknown as Array<{
        candidate_artifact_id: string;
        receipt: typeof rejectedReceipt;
        reason_code: string;
      }>
    ).push({
      candidate_artifact_id: "rejected_candidate",
      receipt: rejectedReceipt,
      reason_code: "not_selected",
    });
    complete.receipt_lineage.closures.push({
      root: { ...rejectedReceipt },
      parents: [],
    });
    reseal(complete);
    expect(validateGenericAssetContract(complete)).not.toBeNull();

    const missing = structuredClone(complete);
    missing.receipt_lineage.closures.pop();
    reseal(missing);
    expect(validateGenericAssetContract(missing)).toBeNull();

    const extra = structuredClone(selectionFixture);
    extra.receipt_lineage.closures.push({
      root: rejectedReceipt,
      parents: [],
    });
    reseal(extra);
    expect(validateGenericAssetContract(extra)).toBeNull();

    const swapped = structuredClone(complete);
    swapped.receipt_lineage.closures[1].root = {
      ...rejectedReceipt,
      content_hash: "c".repeat(64),
    };
    reseal(swapped);
    expect(validateGenericAssetContract(swapped)).toBeNull();
  });

  it("enforces exact font metadata and runtime-safe text parity", () => {
    const wrongContainer = structuredClone(narrativeReceiptFixture);
    wrongContainer.outputs[0].metadata.container = "otf";
    reseal(wrongContainer);
    expect(validateGenericAssetContract(wrongContainer)).toBeNull();

    for (const mutate of [
      (receipt: typeof narrativeReceiptFixture) => {
        receipt.outputs[0].metadata.glyph_ranges = ["U+007E-0020"];
      },
      (receipt: typeof narrativeReceiptFixture) => {
        receipt.outputs[0].metadata.glyph_count += 1;
      },
    ]) {
      const receipt = structuredClone(narrativeReceiptFixture);
      mutate(receipt);
      reseal(receipt);
      expect(validateGenericAssetContract(receipt)).toBeNull();
    }

    for (const unsafe of [
      "C:\\Build\\artifacts\\authoring.bin",
      "\\\\server\\share\\authoring.bin",
      "@scope/package@1.0.0",
      "Cafe\u0301 public domain.",
      "\ud800",
    ]) {
      const selection = structuredClone(selectionFixture);
      selection.review.rationale = unsafe;
      reseal(selection);
      expect(validateGenericAssetContract(selection)).toBeNull();
    }
  });

  it("structurally discriminates candidate role, media, and shader stage", () => {
    const crossedCandidates = [
      (() => {
        const document = structuredClone(productionReceiptFixture);
        document.outputs[0].role = "audio";
        return document;
      })(),
      (() => {
        const document = structuredClone(selectionFixture);
        document.selected_outputs[0].role = "audio";
        return document;
      })(),
      (() => {
        const document = structuredClone(provenanceFixture);
        document.candidates[0].role = "audio";
        return document;
      })(),
      (() => {
        const document = structuredClone(licenseFixture);
        document.candidate.role = "audio";
        return document;
      })(),
    ];
    for (const document of crossedCandidates) {
      reseal(document);
      expect(validateGenericAssetContract(document)).toBeNull();
    }

    const crossedShaderStage = structuredClone(productionReceiptFixture);
    (crossedShaderStage.outputs as unknown as Array<Record<string, unknown>>)[0] = {
      ...crossedShaderStage.outputs[0],
      role: "vertex_shader",
      media_type: "text/x-glsl",
      metadata: {
        kind: "glsl",
        stage: "fragment",
        line_count: 2,
      },
    };
    reseal(crossedShaderStage);
    expect(validateGenericAssetContract(crossedShaderStage)).toBeNull();
  });

  it("binds runtime notices and component licenses to reviewed evidence", () => {
    const staleNotice = structuredClone(licenseFixture);
    staleNotice.runtime_notice.text = "Fixture asset terms remain available.";
    reseal(staleNotice);
    expect(validateGenericAssetContract(staleNotice)).toBeNull();

    staleNotice.runtime_notice.sha256 = createHash("sha256")
      .update(staleNotice.runtime_notice.text, "utf8")
      .digest("hex");
    reseal(staleNotice);
    expect(validateGenericAssetContract(staleNotice)).not.toBeNull();

    for (const authoringNotice of [
      "Dataset details are unavailable.",
      "Datasets and models are unavailable.",
      "MCPs are unavailable.",
    ]) {
      const datasetNotice = structuredClone(licenseFixture);
      datasetNotice.runtime_notice.text = authoringNotice;
      datasetNotice.runtime_notice.sha256 = createHash("sha256")
        .update(datasetNotice.runtime_notice.text, "utf8")
        .digest("hex");
      reseal(datasetNotice);
      expect(validateGenericAssetContract(datasetNotice)).toBeNull();
    }

    for (const credentialNotice of [
      "eyJhbGciOiJIUzI1NiJ9.payload.signature",
      "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJmaXh0dXJlIiwiaWF0IjoxNzAwMDAwMDB9.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c",
      "Bearer abcdefghijklmnop",
      "  bearer ABCDEFGHIJKLMNOP==",
      "Bearer eyJhbGciOiJIUzI1NiJ9.payload.signature",
      "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJmaXh0dXJlIiwiaWF0IjoxNzAwMDAwMDB9.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c",
      "sk-abcdefghijklmnop1234567890",
      "AKIAABCDEFGHIJKLMNOP",
      "Authorization: Bearer abcdefghijklmnop",
      "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJmaXh0dXJlIiwiaWF0IjoxNzAwMDAwMDB9.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c",
      "Authorization: Basic Zml4dHVyZTpwYXNzd29yZA==",
      "-----BEGIN PRIVATE KEY-----",
      "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghij",
    ]) {
      const unsafeNotice = structuredClone(licenseFixture);
      unsafeNotice.runtime_notice.text = credentialNotice;
      unsafeNotice.runtime_notice.sha256 = createHash("sha256")
        .update(credentialNotice, "utf8")
        .digest("hex");
      reseal(unsafeNotice);
      expect(validateGenericAssetContract(unsafeNotice)).toBeNull();
    }

    for (const narrativeNotice of [
      "The bearer crossed the valley.",
      "The bearer approached the eastern gate.",
      "THE BEARER APPROACHED THE EASTERN GATE.",
      "The Bearer, approached the eastern gate.",
      "The bearer—approached the eastern gate.",
      "The bearer approached the eastern gate!",
      "A skillful scout keeps the key to the eastern gate.",
      "The privateer keeps watch.",
      "LicenseRef.WorldForge.Fixture",
      "chapter.one.final",
      "Chapter.one.final remains the narrative identifier.",
    ]) {
      const safeNotice = structuredClone(licenseFixture);
      safeNotice.runtime_notice.text = narrativeNotice;
      safeNotice.runtime_notice.sha256 = createHash("sha256")
        .update(narrativeNotice, "utf8")
        .digest("hex");
      reseal(safeNotice);
      expect(validateGenericAssetContract(safeNotice)).not.toBeNull();
    }

    for (const [codePoints, utf8Bytes, accepted] of [
      [1000, 4000, true],
      [1001, 4004, true],
      [1024, 4096, true],
      [1025, 4100, false],
    ] as const) {
      const astralNotice = "😀".repeat(codePoints);
      expect(Array.from(astralNotice)).toHaveLength(codePoints);
      expect(Buffer.byteLength(astralNotice, "utf8")).toBe(utf8Bytes);
      const astralLicense = structuredClone(licenseFixture);
      astralLicense.runtime_notice.text = astralNotice;
      astralLicense.runtime_notice.sha256 = createHash("sha256")
        .update(astralNotice, "utf8")
        .digest("hex");
      reseal(astralLicense);
      expect(validateGenericAssetContract(astralLicense) !== null).toBe(accepted);
    }

    const unapprovedComponent = structuredClone(licenseFixture);
    unapprovedComponent.component_licenses[0].identifier =
      "LicenseRef-Unreviewed-Custom-Terms";
    reseal(unapprovedComponent);
    expect(validateGenericAssetContract(unapprovedComponent)).toBeNull();

    const crossedSpdxBasis = structuredClone(licenseFixture);
    crossedSpdxBasis.license_basis.identifier =
      "LicenseRef-WorldForge-Fixture-Public-Domain";
    reseal(crossedSpdxBasis);
    expect(validateGenericAssetContract(crossedSpdxBasis)).toBeNull();

    const fixedWithoutYear = structuredClone(licenseFixture);
    const fixedCopyright = fixedWithoutYear.copyright as {
      year: number | null;
      year_policy: string;
    };
    fixedCopyright.year_policy = "fixed";
    fixedCopyright.year = null;
    reseal(fixedWithoutYear);
    expect(validateGenericAssetContract(fixedWithoutYear)).toBeNull();

    const notApplicableWithYear = structuredClone(licenseFixture);
    const notApplicableCopyright = notApplicableWithYear.copyright as {
      year: number | null;
      year_policy: string;
    };
    notApplicableCopyright.year_policy = "not_applicable";
    notApplicableCopyright.year = 2026;
    reseal(notApplicableWithYear);
    expect(validateGenericAssetContract(notApplicableWithYear)).toBeNull();

    const maximalComponents = structuredClone(licenseFixture);
    maximalComponents.component_licenses = Array.from({ length: 70 }, (_, index) => ({
      scope: "dataset",
      component_id: `dataset_${String(index).padStart(2, "0")}`,
      identifier: "CC0-1.0",
      evidence_hash: createHash("sha256")
        .update(`dataset:${String(index)}`, "utf8")
        .digest("hex"),
    }));
    reseal(maximalComponents);
    expect(validateGenericAssetContract(maximalComponents)).not.toBeNull();
    maximalComponents.component_licenses.push({
      scope: "dataset",
      component_id: "dataset_70",
      identifier: "CC0-1.0",
      evidence_hash: "f".repeat(64),
    });
    reseal(maximalComponents);
    expect(validateGenericAssetContract(maximalComponents)).toBeNull();
  });

  it("fails closed for valid JWTs whose encoded headers reach or exceed the decode bound", () => {
    for (const [headerPadding, expectedHeaderLength] of [
      [348, 512],
      [349, 514],
      [600, 848],
    ] as const) {
      const token = validHs256Jwt(headerPadding);
      const [header, payload, signature] = token.split(".");
      expect(header).toHaveLength(expectedHeaderLength);
      expect(
        createHmac("sha256", JWT_TEST_KEY)
          .update(`${header}.${payload}`, "ascii")
          .digest("base64url"),
      ).toBe(signature);
      if (expectedHeaderLength === 848) {
        expect(token).toHaveLength(939);
      }
      const candidate = structuredClone(licenseFixture);
      candidate.runtime_notice.text = token;
      candidate.runtime_notice.sha256 = createHash("sha256")
        .update(token, "utf8")
        .digest("hex");
      reseal(candidate);
      expect(validateGenericAssetContract(candidate)).toBeNull();
    }
  });

  it("rejects signed JWTs with canonical or noncanonical base64url headers", () => {
    const tokens = validNonCanonicalHs256Jwt();
    const [canonicalHeader] = tokens.canonical.split(".");
    const [noncanonicalHeader] = tokens.noncanonical.split(".");
    expect(noncanonicalHeader).not.toBe(canonicalHeader);
    expect(Buffer.from(noncanonicalHeader, "base64url")).toEqual(
      Buffer.from(canonicalHeader, "base64url"),
    );
    expect(
      Buffer.from(noncanonicalHeader, "base64url").toString("base64url"),
    ).toBe(canonicalHeader);

    for (const token of [tokens.canonical, tokens.noncanonical]) {
      const [header, payload, signature] = token.split(".");
      expect(
        createHmac("sha256", JWT_TEST_KEY)
          .update(`${header}.${payload}`, "ascii")
          .digest("base64url"),
      ).toBe(signature);
      const candidate = structuredClone(licenseFixture);
      candidate.runtime_notice.text = token;
      candidate.runtime_notice.sha256 = createHash("sha256")
        .update(token, "utf8")
        .digest("hex");
      reseal(candidate);
      expect(validateGenericAssetContract(candidate)).toBeNull();
    }
  });

  it("rejects a million-character notice before normalization, clone, or AJV", () => {
    const oversized = structuredClone(licenseFixture);
    oversized.runtime_notice.text = "x".repeat(1_000_000);

    const originalCodePointAt = oversized.runtime_notice.text.codePointAt.bind(
      oversized.runtime_notice.text,
    );
    let preflightReads = 0;
    let observeReads = true;
    const codePointAt = vi
      .spyOn(String.prototype, "codePointAt")
      .mockImplementation(function (this: string, position: number) {
        if (observeReads) {
          preflightReads += 1;
          if (preflightReads > 1025) {
            throw new Error("runtime notice preflight exceeded 1025 code-point reads");
          }
        }
        return originalCodePointAt(position);
      });
    const normalize = vi.spyOn(String.prototype, "normalize");
    const clone = vi.spyOn(globalThis, "structuredClone");

    const observed = (() => {
      try {
        const validated = validateGenericAssetContract(oversized);
        observeReads = false;
        return {
          validated,
          normalizeCalls: normalize.mock.calls.length,
          cloneCalls: clone.mock.calls.length,
        };
      } finally {
        codePointAt.mockRestore();
        normalize.mockRestore();
        clone.mockRestore();
      }
    })();

    expect(observed.validated).toBeNull();
    expect(preflightReads).toBe(1025);
    expect(observed.normalizeCalls).toBe(0);
    expect(observed.cloneCalls).toBe(0);
  });

  it("never invokes hostile accessors or proxy traps while snapshotting contracts", () => {
    const accessorMutation = structuredClone(licenseFixture);
    const originalNotice = accessorMutation.runtime_notice.text;
    let getterCalls = 0;
    Object.defineProperty(accessorMutation, "license_record_id", {
      configurable: true,
      enumerable: true,
      get() {
        getterCalls += 1;
        accessorMutation.runtime_notice.text = "x".repeat(1_000_000);
        return licenseFixture.license_record_id;
      },
    });
    const clone = vi.spyOn(globalThis, "structuredClone");
    const cloneCalls = (() => {
      try {
        expect(validateGenericAssetContract(accessorMutation)).toBeNull();
        return clone.mock.calls.length;
      } finally {
        clone.mockRestore();
      }
    })();
    expect(getterCalls).toBe(0);
    expect(cloneCalls).toBe(0);
    expect(accessorMutation.runtime_notice.text).toBe(originalNotice);

    const nestedAccessor = structuredClone(licenseFixture);
    let nestedGetterCalls = 0;
    Object.defineProperty(nestedAccessor.permissions, "commercial_use", {
      configurable: true,
      enumerable: true,
      get() {
        nestedGetterCalls += 1;
        return true;
      },
    });
    expect(validateGenericAssetContract(nestedAccessor)).toBeNull();
    expect(nestedGetterCalls).toBe(0);

    const rootTarget = structuredClone(licenseFixture);
    let rootProxyTraps = 0;
    const rootProxy = new Proxy(rootTarget, {
      getOwnPropertyDescriptor(target, property) {
        rootProxyTraps += 1;
        return Reflect.getOwnPropertyDescriptor(target, property);
      },
      ownKeys(target) {
        rootProxyTraps += 1;
        return Reflect.ownKeys(target);
      },
    });
    expect(validateGenericAssetContract(rootProxy)).toBeNull();
    expect(rootProxyTraps).toBe(0);

    const revokedRoot = Proxy.revocable(structuredClone(licenseFixture), {});
    revokedRoot.revoke();
    expect(validateGenericAssetContract(revokedRoot.proxy)).toBeNull();

    const nestedProxy = structuredClone(licenseFixture);
    let nestedProxyTraps = 0;
    nestedProxy.permissions = new Proxy(nestedProxy.permissions, {
      getOwnPropertyDescriptor(target, property) {
        nestedProxyTraps += 1;
        return Reflect.getOwnPropertyDescriptor(target, property);
      },
      ownKeys(target) {
        nestedProxyTraps += 1;
        return Reflect.ownKeys(target);
      },
    });
    expect(validateGenericAssetContract(nestedProxy)).toBeNull();
    expect(nestedProxyTraps).toBe(0);
  });

  it("rejects ambiguous or excessive object graphs and owns valid snapshots", () => {
    const prototypePollution = structuredClone(licenseFixture) as Record<
      string,
      unknown
    >;
    Object.defineProperty(prototypePollution, "__proto__", {
      configurable: true,
      enumerable: true,
      value: { polluted: true },
      writable: true,
    });
    expect(validateGenericAssetContract(prototypePollution)).toBeNull();

    const cycle = structuredClone(licenseFixture) as Record<string, unknown>;
    cycle.cycle = cycle;
    expect(validateGenericAssetContract(cycle)).toBeNull();

    const alias = structuredClone(licenseFixture) as Record<string, unknown>;
    const shared = { value: "shared" };
    alias.alias_one = shared;
    alias.alias_two = shared;
    expect(validateGenericAssetContract(alias)).toBeNull();

    const sparse = structuredClone(licenseFixture);
    const sparseLicenses = [] as unknown as typeof sparse.component_licenses;
    sparseLicenses.length = 1;
    sparse.component_licenses = sparseLicenses;
    expect(validateGenericAssetContract(sparse)).toBeNull();

    const excessive = structuredClone(licenseFixture) as Record<string, unknown>;
    excessive.excessive_graph = Array.from({ length: 100_001 }, () => null);
    const clone = vi.spyOn(globalThis, "structuredClone");
    const cloneCalls = (() => {
      try {
        expect(validateGenericAssetContract(excessive)).toBeNull();
        return clone.mock.calls.length;
      } finally {
        clone.mockRestore();
      }
    })();
    expect(cloneCalls).toBe(0);

    const source = structuredClone(licenseFixture);
    const validated = validateGenericAssetContract(source);
    if (
      validated === null ||
      validated.format !== "world-forge.asset_license_record"
    ) {
      throw new Error("valid license fixture was not validated as a license");
    }
    const snapshottedNotice = validated.runtime_notice.text;
    source.runtime_notice.text = "Mutated after validation.";
    expect(validated.runtime_notice.text).toBe(snapshottedNotice);
    expect(validated.runtime_notice.text).toBe(licenseFixture.runtime_notice.text);
  });

  it("isolates root and nested validation from Object.prototype getters", () => {
    const missingRoot = structuredClone(targetFixture) as Record<string, unknown>;
    delete missingRoot.target_id;
    const rootObservation = observePrototypeRead(
      Object.prototype,
      "target_id",
      undefined,
      () => validateGenericAssetContract(missingRoot),
    );
    expect(rootObservation.result).toBeNull();
    expect(rootObservation.calls).toBe(0);

    const missingNested = structuredClone(productionRequestFixture);
    delete (missingNested.operation as unknown as Record<string, unknown>).operation_id;
    const nestedObservation = observePrototypeRead(
      Object.prototype,
      "operation_id",
      undefined,
      () => validateGenericAssetContract(missingNested),
    );
    expect(nestedObservation.result).toBeNull();
    expect(nestedObservation.calls).toBe(0);

    const procedural = structuredClone(productionRequestFixture);
    const proceduralObservation = observePrototypeRead(
      Object.prototype,
      "model_id",
      undefined,
      () => validateGenericAssetContract(procedural),
    );
    expect(proceduralObservation.result).not.toBeNull();
    expect(proceduralObservation.calls).toBe(0);
    if (
      proceduralObservation.result === null ||
      proceduralObservation.result.format !==
        "world-forge.asset_production_request"
    ) {
      throw new Error("Procedural request did not retain its validated format");
    }
    expect(Reflect.getPrototypeOf(proceduralObservation.result)).toBeNull();
    expect(
      Reflect.getPrototypeOf(proceduralObservation.result.operation),
    ).toBeNull();
    expect(
      Reflect.getPrototypeOf(proceduralObservation.result.expected_outputs),
    ).toBeNull();

    const validFormats = [
      subjectFixture,
      targetFixture,
      styleFixture,
      inventoryFixture,
      specificationFixture,
      productionRequestFixture,
      productionReceiptFixture,
      selectionFixture,
      provenanceFixture,
      licenseFixture,
      processingRecipeFixture,
      processingReceiptFixture,
      qaReportFixture,
      manifestFixture,
    ].map((fixture) => structuredClone(fixture));
    const invalidFormats = validFormats.map((fixture) => {
      const invalid = structuredClone(fixture) as Record<string, unknown>;
      delete invalid.format_version;
      return invalid;
    });
    const formatObservation = observePrototypeRead(
      Object.prototype,
      "format_version",
      undefined,
      () => {
        const valid = new Array<
          ReturnType<typeof validateGenericAssetContract>
        >(validFormats.length);
        const invalid = new Array<
          ReturnType<typeof validateGenericAssetContract>
        >(invalidFormats.length);
        for (let index = 0; index < validFormats.length; index += 1) {
          valid[index] = validateGenericAssetContract(validFormats[index]);
          invalid[index] = validateGenericAssetContract(invalidFormats[index]);
        }
        return { invalid, valid };
      },
    );
    expect(formatObservation.calls).toBe(0);
    for (let index = 0; index < validFormats.length; index += 1) {
      expect(formatObservation.result.valid[index]).not.toBeNull();
      expect(formatObservation.result.invalid[index]).toBeNull();
    }
  });

  it("severs every private-realm prototype and constructor across validation calls", () => {
    const fixtures = [
      subjectFixture,
      targetFixture,
      styleFixture,
      inventoryFixture,
      specificationFixture,
      productionRequestFixture,
      productionReceiptFixture,
      selectionFixture,
      provenanceFixture,
      licenseFixture,
      processingRecipeFixture,
      processingReceiptFixture,
      qaReportFixture,
      manifestFixture,
    ];
    const validated = fixtures.map((fixture) =>
      validateGenericAssetContract(structuredClone(fixture)),
    );
    expect(validated).toHaveLength(14);
    for (const contract of validated) {
      expect(contract).not.toBeNull();
    }

    const pending = validated.filter(
      (contract): contract is NonNullable<typeof contract> => contract !== null,
    ) as object[];
    const visited = new Set<object>();
    const reachablePrototypes = new Set<object>();
    const reachableConstructors = new Set<object>();
    while (pending.length > 0) {
      const current = pending.pop();
      if (current === undefined || visited.has(current)) {
        continue;
      }
      visited.add(current);
      const prototype = Reflect.getPrototypeOf(current);
      if (prototype !== null) {
        reachablePrototypes.add(prototype);
        const constructor = Object.getOwnPropertyDescriptor(
          prototype,
          "constructor",
        );
        if (
          constructor !== undefined &&
          "value" in constructor
        ) {
          const constructorValue: unknown = constructor.value;
          if (typeof constructorValue === "function") {
            reachableConstructors.add(constructorValue);
          }
        }
      }
      for (const key of Reflect.ownKeys(current)) {
        const descriptor = Object.getOwnPropertyDescriptor(current, key);
        const child: unknown =
          descriptor !== undefined && "value" in descriptor
            ? descriptor.value
            : undefined;
        if (
          child !== null &&
          typeof child === "object"
        ) {
          pending.push(child);
        }
      }
    }

    const mutationMarker = Symbol("world-forge-realm-leak-regression");
    const restorations: Array<() => void> = [];
    try {
      for (const prototype of reachablePrototypes) {
        const previous = Object.getOwnPropertyDescriptor(
          prototype,
          mutationMarker,
        );
        if (
          Reflect.defineProperty(prototype, mutationMarker, {
            configurable: true,
            value: true,
            writable: true,
          })
        ) {
          restorations.push(() => {
            if (previous === undefined) {
              Reflect.deleteProperty(prototype, mutationMarker);
            } else {
              Reflect.defineProperty(prototype, mutationMarker, previous);
            }
          });
        }
      }
      for (const constructor of reachableConstructors) {
        const marker = Object.getOwnPropertyDescriptor(
          constructor,
          mutationMarker,
        );
        if (
          Reflect.defineProperty(constructor, mutationMarker, {
            configurable: true,
            value: true,
            writable: true,
          })
        ) {
          restorations.push(() => {
            if (marker === undefined) {
              Reflect.deleteProperty(constructor, mutationMarker);
            } else {
              Reflect.defineProperty(constructor, mutationMarker, marker);
            }
          });
        }
        const isArray = Object.getOwnPropertyDescriptor(constructor, "isArray");
        if (
          isArray !== undefined &&
          "value" in isArray &&
          typeof isArray.value === "function" &&
          isArray.configurable === true
        ) {
          Reflect.defineProperty(constructor, "isArray", {
            ...isArray,
            value: () => false,
          });
          restorations.push(() => {
            Reflect.defineProperty(constructor, "isArray", isArray);
          });
        }
      }

      for (const fixture of fixtures) {
        const valid = structuredClone(fixture);
        const invalid = structuredClone(fixture) as Record<string, unknown>;
        delete invalid.format_version;
        expect(validateGenericAssetContract(valid)).not.toBeNull();
        expect(validateGenericAssetContract(invalid)).toBeNull();
      }
      expect(reachablePrototypes.size).toBe(0);
      expect(reachableConstructors.size).toBe(0);
    } finally {
      for (let index = restorations.length - 1; index >= 0; index -= 1) {
        restorations[index]();
      }
    }
  });

  it("never resolves Array.prototype accessors or method replacements for any D2 format", () => {
    const candidates = [
      subjectFixture,
      targetFixture,
      styleFixture,
      inventoryFixture,
      specificationFixture,
      productionRequestFixture,
      productionReceiptFixture,
      selectionFixture,
      provenanceFixture,
      licenseFixture,
      processingRecipeFixture,
      processingReceiptFixture,
      qaReportFixture,
      manifestFixture,
    ].map((fixture) => structuredClone(fixture));
    const invalidCandidates = candidates.map((candidate) => {
      const invalid = structuredClone(candidate) as Record<string, unknown>;
      delete invalid.format_version;
      return invalid;
    });
    const methodKeys: PropertyKey[] = [
      "constructor",
      "every",
      "join",
      "map",
      "pop",
      "push",
      "reduce",
      "slice",
      "some",
      "sort",
      Symbol.iterator,
    ];
    for (let methodIndex = 0; methodIndex < methodKeys.length; methodIndex += 1) {
      const methodKey = methodKeys[methodIndex];
      const descriptor = Object.getOwnPropertyDescriptor(
        Array.prototype,
        methodKey,
      );
      if (descriptor === undefined || !("value" in descriptor)) {
        throw new Error(`Missing Array.prototype intrinsic ${String(methodKey)}`);
      }
      const intrinsic = descriptor.value as (
        this: unknown,
        ...parameters: unknown[]
      ) => unknown;
      const observation = observePrototypeRead(
        Array.prototype,
        methodKey,
        intrinsic,
        () => {
          const valid = new Array<
            ReturnType<typeof validateGenericAssetContract>
          >(candidates.length);
          const invalid = new Array<
            ReturnType<typeof validateGenericAssetContract>
          >(invalidCandidates.length);
          for (
            let candidateIndex = 0;
            candidateIndex < candidates.length;
            candidateIndex += 1
          ) {
            valid[candidateIndex] = validateGenericAssetContract(
              candidates[candidateIndex],
            );
            invalid[candidateIndex] = validateGenericAssetContract(
              invalidCandidates[candidateIndex],
            );
          }
          return { invalid, valid };
        },
      );
      expect(observation.calls, String(methodKey)).toBe(0);
      for (
        let candidateIndex = 0;
        candidateIndex < observation.result.valid.length;
        candidateIndex += 1
      ) {
        expect(
          observation.result.valid[candidateIndex],
          String(methodKey),
        ).not.toBeNull();
        expect(
          observation.result.invalid[candidateIndex],
          String(methodKey),
        ).toBeNull();
      }
    }

    const numericObservation = observePrototypeRead(
      Array.prototype,
      "0",
      undefined,
      () => validateGenericAssetContract(structuredClone(productionRequestFixture)),
    );
    expect(numericObservation.calls).toBe(0);
    expect(numericObservation.result).not.toBeNull();

    const mapDescriptor = Object.getOwnPropertyDescriptor(Array.prototype, "map");
    if (mapDescriptor === undefined || !("value" in mapDescriptor)) {
      throw new Error("Missing Array.prototype.map intrinsic");
    }
    const originalMap = mapDescriptor.value as (
      this: unknown,
      ...parameters: unknown[]
    ) => unknown;
    let replacementCalls = 0;
    const replacement = function (
      this: unknown,
      ...parameters: unknown[]
    ): unknown {
      replacementCalls += 1;
      return Reflect.apply(originalMap, this, parameters);
    };
    let replacementResult: ReturnType<typeof validateGenericAssetContract>;
    Object.defineProperty(Array.prototype, "map", {
      configurable: true,
      value: replacement,
      writable: true,
    });
    try {
      replacementResult = validateGenericAssetContract(
        structuredClone(productionRequestFixture),
      );
      const invalid = structuredClone(productionRequestFixture) as Record<
        string,
        unknown
      >;
      delete invalid.format_version;
      if (validateGenericAssetContract(invalid) !== null) {
        throw new Error("Invalid request passed under method replacement");
      }
      if (
        Object.getOwnPropertyDescriptor(Array.prototype, "map")?.value !==
        replacement
      ) {
        throw new Error("Validation leaked the Array.prototype.map replacement");
      }
    } finally {
      Object.defineProperty(Array.prototype, "map", mapDescriptor);
    }
    expect(replacementResult).not.toBeNull();
    expect(replacementCalls).toBe(0);
  });

  it("never mutates shared prototypes during valid, invalid, or reentrant validation", () => {
    const valid = structuredClone(productionRequestFixture);
    const invalid = structuredClone(productionRequestFixture) as Record<
      string,
      unknown
    >;
    delete invalid.format_version;
    const observation = observeSharedPrototypeMutations(() => ({
      invalid: validateGenericAssetContract(invalid),
      valid: validateGenericAssetContract(valid),
    }));

    expect(observation.result.valid).not.toBeNull();
    expect(observation.result.invalid).toBeNull();
    expect(observation.events).toEqual([]);
    expect(observation.preserved).toBe(true);
    expect(observation.reentrantCalls).toBe(0);
  });

  it("validates all formats under unread nonconfigurable ambient descriptors", () => {
    const objectKey = Symbol("world-forge-nonconfigurable-object-ambient");
    const arrayKey = Symbol("world-forge-nonconfigurable-array-ambient");
    let getterCalls = 0;
    Object.defineProperty(Object.prototype, objectKey, {
      configurable: false,
      enumerable: false,
      get() {
        getterCalls += 1;
        return "ambient";
      },
    });
    Object.defineProperty(Array.prototype, arrayKey, {
      configurable: false,
      enumerable: false,
      get() {
        getterCalls += 1;
        return "ambient";
      },
    });

    const fixtures = [
      subjectFixture,
      targetFixture,
      styleFixture,
      inventoryFixture,
      specificationFixture,
      productionRequestFixture,
      productionReceiptFixture,
      selectionFixture,
      provenanceFixture,
      licenseFixture,
      processingRecipeFixture,
      processingReceiptFixture,
      qaReportFixture,
      manifestFixture,
    ];
    for (const fixture of fixtures) {
      const valid = structuredClone(fixture);
      const invalid = structuredClone(fixture) as Record<string, unknown>;
      delete invalid.format_version;
      expect(validateGenericAssetContract(valid)).not.toBeNull();
      expect(validateGenericAssetContract(invalid)).toBeNull();
    }
    expect(getterCalls).toBe(0);
  });
});

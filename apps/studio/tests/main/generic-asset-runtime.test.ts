import { createHash } from "node:crypto";
import { createRequire } from "node:module";
import {
  copyFile,
  mkdir,
  mkdtemp,
  readFile,
  realpath,
  rm,
  symlink,
} from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { afterAll, beforeAll, describe, expect, it } from "vitest";

import {
  createCanonicalAssetpackSmokeRoot,
  GENERIC_ASSET_RUNTIME_ENTRY,
  verifyGenericAssetRuntimeArtifact,
  verifyGenericAssetRuntimeSnapshot,
} from "../../scripts/verify-generic-asset-runtime.mjs";

const require = createRequire(import.meta.url);
const asar = require("@electron/asar") as {
  createPackage(source: string, destination: string): Promise<void>;
};
const studioRoot = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../..",
);
const builtRuntime = path.join(
  studioRoot,
  ...GENERIC_ASSET_RUNTIME_ENTRY.split("/"),
);
const expectedFormats = [
  "world-forge.asset_inventory",
  "world-forge.asset_license_record",
  "world-forge.asset_manifest",
  "world-forge.asset_processing_receipt",
  "world-forge.asset_processing_recipe",
  "world-forge.asset_production_receipt",
  "world-forge.asset_production_request",
  "world-forge.asset_provenance_record",
  "world-forge.asset_qa_report",
  "world-forge.asset_selection",
  "world-forge.asset_spec",
  "world-forge.asset_style",
  "world-forge.asset_subject",
  "world-forge.asset_target",
];

let temporaryRoot: string;

beforeAll(async () => {
  temporaryRoot = await mkdtemp(
    path.join(os.tmpdir(), "world-forge-generic-asset-runtime-test-"),
  );
});

afterAll(async () => {
  await rm(temporaryRoot, { force: true, recursive: true });
});

describe("packaged generic asset validator runtime", () => {
  it("canonicalizes an aliased smoke root before production verification", async () => {
    const canonicalParent = path.join(temporaryRoot, "canonical-smoke-parent");
    const aliasParent = path.join(temporaryRoot, "aliased-smoke-parent");
    await mkdir(canonicalParent);
    await symlink(
      canonicalParent,
      aliasParent,
      process.platform === "win32" ? "junction" : "dir",
    );

    const root = await createCanonicalAssetpackSmokeRoot(aliasParent);

    expect(root).toBe(await realpath(root));
    expect(path.dirname(root)).toBe(await realpath(canonicalParent));
  });

  it("executes all fourteen D2 contract validators from the built internal CJS entry", async () => {
    const report = await verifyGenericAssetRuntimeArtifact({
      artifactPath: builtRuntime,
      artifactKind: "module",
    });
    expect(report).toEqual({
      accepted_formats: expectedFormats,
      artifact_kind: "module",
      format: "world-forge.studio_generic_asset_runtime_smoke",
      format_version: 2,
      invalid_documents_rejected: 23,
      sealed_pack_formats: ["world-forge.assetpack"],
      sealed_packs_verified: 2,
      sealed_tamper_rejections: 2,
      status: "verified",
      valid_documents_accepted: 14,
    });
  });

  it("pins and executes the same internal CJS entry from an ASAR inventory", async () => {
    const packageDocument = JSON.parse(
      await readFile(path.join(studioRoot, "package.json"), "utf8"),
    ) as { build: { files: string[] } };
    expect(packageDocument.build.files).toContain(GENERIC_ASSET_RUNTIME_ENTRY);

    const source = path.join(temporaryRoot, "asar-source");
    const archive = path.join(temporaryRoot, "app.asar");
    const packagedRuntime = path.join(
      source,
      ...GENERIC_ASSET_RUNTIME_ENTRY.split("/"),
    );
    await mkdir(path.dirname(packagedRuntime), { recursive: true });
    await copyFile(builtRuntime, packagedRuntime);
    await asar.createPackage(source, archive);

    const report = await verifyGenericAssetRuntimeArtifact({
      artifactPath: archive,
      artifactKind: "asar",
    });
    expect(report).toEqual({
      accepted_formats: expectedFormats,
      artifact_kind: "asar",
      format: "world-forge.studio_generic_asset_runtime_smoke",
      format_version: 2,
      invalid_documents_rejected: 23,
      sealed_pack_formats: ["world-forge.assetpack"],
      sealed_packs_verified: 2,
      sealed_tamper_rejections: 2,
      status: "verified",
      valid_documents_accepted: 14,
    });

    const retainedBytes = await readFile(archive);
    const retainedSha256 = createHash("sha256")
      .update(retainedBytes)
      .digest("hex");
    const retainedReport = await verifyGenericAssetRuntimeSnapshot({
      artifactBytes: retainedBytes,
      expectedSha256: retainedSha256,
      expectedSize: retainedBytes.length,
    });
    expect(retainedReport).toMatchObject({
      artifact_kind: "asar",
      artifact_sha256: retainedSha256,
      artifact_size_bytes: retainedBytes.length,
      status: "verified",
    });

    const substitutedBytes = Buffer.from(retainedBytes);
    substitutedBytes[substitutedBytes.length - 1] ^= 1;
    await expect(
      verifyGenericAssetRuntimeSnapshot({
        artifactBytes: substitutedBytes,
        expectedSha256: retainedSha256,
        expectedSize: retainedBytes.length,
      }),
    ).rejects.toThrowError(
      "generic_asset_runtime_smoke:snapshot_identity_mismatch",
    );
  });
});

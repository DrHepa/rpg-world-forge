import { createHash } from "node:crypto";
import {
  cp,
  mkdir,
  mkdtemp,
  readFile,
  realpath,
  rename,
  rm,
  symlink,
  writeFile,
} from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { afterAll, beforeAll, describe, expect, it } from "vitest";

import {
  canonicalGenericAssetpackId,
  inspectGenericAssetpackMedia,
} from "../../scripts/generic-assetpack-validation.mjs";
import {
  canonicalGenericAssetContentHash,
} from "../../scripts/generic-asset-validation.mjs";
import {
  noFollowOpenFlagForPlatform,
  validateGenericAssetpack,
  verifyGenericAssetpackDirectory,
} from "../../src/main/generic-assetpack";
import type {
  WorldForgeAssetSpecificationV1,
  WorldForgeDeterministicAssetProcessingReceiptV1,
  WorldForgeGenericAssetReleaseManifestV1,
  WorldForgeRuntimeSafeAssetLicenseRecordV1,
} from "../../src/generated/world-forge-contracts";

const studioRoot = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../..",
);
const repositoryRoot = path.resolve(studioRoot, "../..");
let temporaryRoot: string;

async function readFixture<Document>(relative: string): Promise<Document> {
  const parsed: unknown = JSON.parse(
    await readFile(
      path.join(repositoryRoot, ...relative.split("/")),
      "utf8",
    ),
  );
  return parsed as Document;
}

function identity<IdField extends string>(
  document: {
    content_hash: string;
    format: string;
    format_version: number;
  } & Record<IdField, string>,
  idField: IdField,
) {
  return {
    content_hash: document.content_hash,
    format: document.format,
    format_version: document.format_version,
    id: document[idField],
  };
}

async function buildPuzzlePack() {
  const fixtureRoot = "examples/multigenre-contracts/abstract-puzzle";
  const manifest = await readFixture<WorldForgeGenericAssetReleaseManifestV1>(
    `${fixtureRoot}/assets/manifest.json`,
  );
  const specification = await readFixture<WorldForgeAssetSpecificationV1>(
    `${fixtureRoot}/assets/specs/board_ui.json`,
  );
  const processingReceipt =
    await readFixture<WorldForgeDeterministicAssetProcessingReceiptV1>(
    `${fixtureRoot}/assets/production/board_ui/processing-receipt.json`,
  );
  const license = await readFixture<WorldForgeRuntimeSafeAssetLicenseRecordV1>(
    `${fixtureRoot}/assets/production/board_ui/license.json`,
  );
  const payload = await readFile(
    path.join(
      repositoryRoot,
      ...`${fixtureRoot}/assets/production/board_ui/processed/texture/board.png`.split(
        "/",
      ),
    ),
  );
  const manifestAsset = manifest.assets[0];
  const output = manifestAsset.outputs[0];
  const processedOutput = processingReceipt.outputs[0];
  if (processedOutput === undefined) {
    throw new Error("generic assetpack fixture has no processed output");
  }
  const noticeBytes = Buffer.from(license.runtime_notice.text, "utf8");
  const noticePath = `notices/${license.runtime_notice.sha256}.txt`;
  const files = [
    {
      path: output.runtime_path,
      sha256: createHash("sha256").update(payload).digest("hex"),
      size_bytes: payload.length,
    },
    {
      path: noticePath,
      sha256: license.runtime_notice.sha256,
      size_bytes: noticeBytes.length,
    },
  ].sort((left, right) =>
    Buffer.compare(Buffer.from(left.path, "utf8"), Buffer.from(right.path, "utf8")),
  );
  const inventory = {
    file_count: files.length,
    files,
    total_bytes: files.reduce((total, file) => total + file.size_bytes, 0),
    content_hash: "",
  };
  const inventoryHash = canonicalGenericAssetContentHash(inventory);
  if (inventoryHash === null) {
    throw new Error("failed to hash generic assetpack inventory");
  }
  inventory.content_hash = inventoryHash;
  const document = {
    asset_inventory: manifest.inventory,
    asset_subject: manifest.asset_subject,
    assets: [
      {
        asset: manifestAsset.asset,
        licenses: manifestAsset.licenses,
        outputs: [
          {
            constraints: {
              ...specification.outputs[0].expectations,
              max_bytes: output.size_bytes,
            },
            license_record: identity(license, "license_record_id"),
            media_type: output.media_type,
            metadata: processedOutput.metadata,
            role: output.role,
            runtime_notice: {
              path: noticePath,
              sha256: license.runtime_notice.sha256,
              size_bytes: noticeBytes.length,
            },
            runtime_path: output.runtime_path,
            sha256: output.sha256,
            size_bytes: output.size_bytes,
          },
        ],
        processing_receipt: manifestAsset.processing_receipt!,
        processing_recipe: manifestAsset.processing_recipe!,
        provenance: manifestAsset.provenance,
        qa_report: manifestAsset.qa_report!,
        receipt: manifestAsset.receipt,
        request: manifestAsset.request,
        selection: manifestAsset.selection,
        specification: manifestAsset.specification,
      },
    ],
    format: "world-forge.assetpack",
    format_version: 1,
    gamepack: manifest.gamepack,
    inventory,
    release_ready_manifest: identity(manifest, "manifest_id"),
    state: "sealed",
    style: manifest.style,
    target: manifest.target,
    assetpack_id: "",
    content_hash: "",
  };
  const assetpackId = canonicalGenericAssetpackId(document);
  if (assetpackId === null) {
    throw new Error("failed to identify generic assetpack fixture");
  }
  document.assetpack_id = assetpackId;
  const contentHash = canonicalGenericAssetContentHash(document);
  if (contentHash === null) {
    throw new Error("failed to seal generic assetpack fixture");
  }
  document.content_hash = contentHash;
  return {
    document,
    files: new Map<string, Buffer>([
      [output.runtime_path, payload],
      [noticePath, noticeBytes],
    ]),
  };
}

function canonicalPretty(value: unknown): Buffer {
  function ordered(candidate: unknown): unknown {
    if (Array.isArray(candidate)) {
      return candidate.map(ordered);
    }
    if (candidate !== null && typeof candidate === "object") {
      const record = candidate as Record<string, unknown>;
      return Object.fromEntries(
        Object.keys(record)
          .sort()
          .map((key) => [key, ordered(record[key])]),
      );
    }
    return candidate;
  }
  return Buffer.from(`${JSON.stringify(ordered(value), null, 2)}\n`, "utf8");
}

function reseal(document: Awaited<ReturnType<typeof buildPuzzlePack>>["document"]) {
  const assetpackId = canonicalGenericAssetpackId(document);
  if (assetpackId === null) {
    throw new Error("failed to reseal generic assetpack fixture");
  }
  document.assetpack_id = assetpackId;
  const contentHash = canonicalGenericAssetContentHash(document);
  if (contentHash === null) {
    throw new Error("failed to rehash generic assetpack fixture");
  }
  document.content_hash = contentHash;
}

async function writePack(
  root: string,
  document: unknown,
  files: Map<string, Buffer>,
) {
  await mkdir(root, { recursive: false });
  await writeFile(path.join(root, "assetpack.json"), canonicalPretty(document), {
    flag: "wx",
  });
  for (const [relative, payload] of files) {
    const target = path.join(root, ...relative.split("/"));
    await mkdir(path.dirname(target), { recursive: true });
    await writeFile(target, payload, { flag: "wx" });
  }
}

type GlbParityOperation = {
  op: "set";
  path: (number | string)[];
  value: unknown;
};

function mutateGlb(
  source: Buffer,
  operations: GlbParityOperation[],
): { document: Record<string, unknown>; payload: Buffer } {
  if (
    source.length < 20 ||
    source.toString("ascii", 0, 4) !== "glTF" ||
    source.readUInt32LE(16) !== 0x4e4f534a
  ) {
    throw new Error("GLB parity fixture is invalid");
  }
  const jsonLength = source.readUInt32LE(12);
  if (20 + jsonLength > source.length) {
    throw new Error("GLB parity fixture has invalid JSON bounds");
  }
  const document = JSON.parse(
    source
      .subarray(20, 20 + jsonLength)
      .toString("utf8")
      .trimEnd(),
  ) as Record<string, unknown>;
  for (const operation of operations) {
    if (operation.op !== "set" || operation.path.length === 0) {
      throw new Error("unsupported GLB parity operation");
    }
    let target: unknown = document;
    for (const segment of operation.path.slice(0, -1)) {
      target = (target as Record<number | string, unknown>)[segment];
    }
    const finalSegment = operation.path.at(-1);
    if (finalSegment === undefined) {
      throw new Error("GLB parity operation path is empty");
    }
    (target as Record<number | string, unknown>)[finalSegment] =
      structuredClone(operation.value);
  }
  const encoded = Buffer.from(JSON.stringify(document), "utf8");
  const padding = (4 - (encoded.length % 4)) % 4;
  const padded = Buffer.concat([encoded, Buffer.alloc(padding, 0x20)]);
  const tail = source.subarray(20 + jsonLength);
  const payload = Buffer.alloc(20 + padded.length + tail.length);
  payload.write("glTF", 0, "ascii");
  payload.writeUInt32LE(2, 4);
  payload.writeUInt32LE(payload.length, 8);
  payload.writeUInt32LE(padded.length, 12);
  payload.writeUInt32LE(0x4e4f534a, 16);
  padded.copy(payload, 20);
  tail.copy(payload, 20 + padded.length);
  return { document, payload };
}

beforeAll(async () => {
  temporaryRoot = await mkdtemp(
    path.join(os.tmpdir(), "world-forge-studio-d3-test-"),
  );
});

afterAll(async () => {
  await rm(temporaryRoot, { force: true, recursive: true });
});

describe("sealed generic assetpack validation", () => {
  it("uses no raw O_NOFOLLOW flag on Windows while keeping it elsewhere", () => {
    expect(noFollowOpenFlagForPlatform("win32", 0x20000)).toBe(0);
    expect(noFollowOpenFlagForPlatform("linux", 0x20000)).toBe(0x20000);
  });

  it("keeps structural validation separate from frozen integral sealed evidence", async () => {
    const fixture = await buildPuzzlePack();
    const structural = validateGenericAssetpack(fixture.document);
    expect(structural).not.toBeNull();
    expect(Object.isFrozen(structural)).toBe(true);
    expect(structural).not.toHaveProperty("status");

    const widenedByteBound = structuredClone(fixture.document);
    widenedByteBound.assets[0].outputs[0].constraints.max_bytes += 1;
    const widenedAssetpackId =
      canonicalGenericAssetpackId(widenedByteBound);
    if (widenedAssetpackId === null) {
      throw new Error("failed to reseal widened byte constraint");
    }
    widenedByteBound.assetpack_id = widenedAssetpackId;
    const widenedContentHash =
      canonicalGenericAssetContentHash(widenedByteBound);
    if (widenedContentHash === null) {
      throw new Error("failed to hash widened byte constraint");
    }
    widenedByteBound.content_hash = widenedContentHash;
    expect(validateGenericAssetpack(widenedByteBound)).toBeNull();

    const substitutedLicenseHash = structuredClone(fixture.document);
    substitutedLicenseHash.assets[0].outputs[0].license_record.content_hash =
      "f".repeat(64);
    reseal(substitutedLicenseHash);
    expect(validateGenericAssetpack(substitutedLicenseHash)).toBeNull();

    const oversizedNotice = structuredClone(fixture.document);
    const notice = oversizedNotice.assets[0].outputs[0].runtime_notice;
    const noticeFile = oversizedNotice.inventory.files.find(
      (entry) => entry.path === notice.path,
    );
    if (noticeFile === undefined) {
      throw new Error("generic assetpack fixture has no notice inventory entry");
    }
    oversizedNotice.inventory.total_bytes += 4097 - noticeFile.size_bytes;
    noticeFile.size_bytes = 4097;
    notice.size_bytes = 4097;
    const inventoryHash = canonicalGenericAssetContentHash(
      oversizedNotice.inventory,
    );
    if (inventoryHash === null) {
      throw new Error("failed to rehash oversized notice inventory");
    }
    oversizedNotice.inventory.content_hash = inventoryHash;
    reseal(oversizedNotice);
    expect(validateGenericAssetpack(oversizedNotice)).toBeNull();

    const root = path.join(temporaryRoot, "valid-pack");
    await writePack(root, fixture.document, fixture.files);
    const verified = await verifyGenericAssetpackDirectory(root);
    expect(verified).not.toBeNull();
    expect(verified).toMatchObject({
      assetpack_id: fixture.document.assetpack_id,
      content_hash: fixture.document.content_hash,
      file_count: 2,
      root,
      status: "sealed",
    });
    expect(Object.isFrozen(verified)).toBe(true);
  });

  it("rejects an aliased root while accepting the same canonical assetpack", async () => {
    const fixture = await buildPuzzlePack();
    const canonicalRoot = path.join(temporaryRoot, "canonical-root-pack");
    const aliasRoot = path.join(temporaryRoot, "aliased-root-pack");
    await writePack(canonicalRoot, fixture.document, fixture.files);
    await symlink(
      canonicalRoot,
      aliasRoot,
      process.platform === "win32" ? "junction" : "dir",
    );

    await expect(verifyGenericAssetpackDirectory(aliasRoot)).resolves.toBeNull();
    const canonical = await realpath(aliasRoot);
    await expect(verifyGenericAssetpackDirectory(canonical)).resolves.toMatchObject({
      root: canonical,
      status: "sealed",
    });
  });

  it("binds processed media to constraints and rejects a resealed bad PNG CRC", async () => {
    const fixture = await buildPuzzlePack();
    const output = fixture.document.assets[0].outputs[0];
    const payload = fixture.files.get(output.runtime_path);
    if (payload === undefined) {
      throw new Error("fixture output payload is missing");
    }
    expect(inspectGenericAssetpackMedia(payload, output)).not.toBeNull();

    const badCrc = Buffer.from(payload);
    badCrc[badCrc.length - 1] ^= 1;
    const resealedOutput = {
      ...output,
      sha256: createHash("sha256").update(badCrc).digest("hex"),
    };
    expect(inspectGenericAssetpackMedia(badCrc, resealedOutput)).toBeNull();

    const wrongConstraint = structuredClone(fixture.document);
    const mutatedOutput = wrongConstraint.assets[0].outputs[0];
    if (mutatedOutput.media_type !== "image/png") {
      throw new Error("fixture output is not PNG");
    }
    if (mutatedOutput.constraints.kind !== "png") {
      throw new Error("fixture constraints are not PNG");
    }
    mutatedOutput.constraints.width -= 1;
    const assetpackId = canonicalGenericAssetpackId(wrongConstraint);
    if (assetpackId === null) {
      throw new Error("failed to reseal constraint mutation");
    }
    wrongConstraint.assetpack_id = assetpackId;
    const contentHash = canonicalGenericAssetContentHash(wrongConstraint);
    if (contentHash === null) {
      throw new Error("failed to hash constraint mutation");
    }
    wrongConstraint.content_hash = contentHash;
    const root = path.join(temporaryRoot, "wrong-constraint-pack");
    await writePack(root, wrongConstraint, fixture.files);
    await expect(verifyGenericAssetpackDirectory(root)).resolves.toBeNull();
  });

  it("derives bounded GLB metrics from the sealed bytes and requires an exact byte bound", async () => {
    const payload = await readFile(
      path.join(
        repositoryRoot,
        "examples/m5-neutral/assetpack/processed/neutral_actor_3d/neutral_actor_3d.glb",
      ),
    );
    const output = {
      constraints: {
        kind: "glb",
        max_animations: 1,
        max_bytes: payload.length,
        max_joints: 0,
        max_materials: 1,
        max_meshes: 1,
        max_nodes: 1,
        max_primitives: 1,
        max_triangles: 1,
      },
      media_type: "model/gltf-binary",
      metadata: {
        kind: "glb",
        max_texture_dimension: 0,
        metrics: {
          animations: 1,
          joints: 0,
          materials: 1,
          meshes: 1,
          nodes: 1,
          primitives: 1,
          triangles: 1,
        },
      },
      role: "model",
      runtime_path: "assets/models/neutral_actor_3d.glb",
      sha256: createHash("sha256").update(payload).digest("hex"),
      size_bytes: payload.length,
    };

    expect(inspectGenericAssetpackMedia(payload, output)).toEqual(
      output.metadata,
    );
    expect(
      inspectGenericAssetpackMedia(payload, {
        ...output,
        constraints: {
          ...output.constraints,
          max_bytes: payload.length + 1,
        },
      }),
    ).toBeNull();
    expect(
      inspectGenericAssetpackMedia(payload, {
        ...output,
        metadata: {
          ...output.metadata,
          metrics: {
            ...output.metadata.metrics,
            triangles: 0,
          },
        },
      }),
    ).toBeNull();

    const truncatedView = Buffer.from(payload);
    const declaredViewLength = Buffer.from('"byteLength":36', "ascii");
    const shortenedViewLength = Buffer.from('"byteLength":35', "ascii");
    const viewLengthOffset = truncatedView.indexOf(declaredViewLength);
    expect(viewLengthOffset).toBeGreaterThanOrEqual(0);
    shortenedViewLength.copy(truncatedView, viewLengthOffset);
    expect(
      inspectGenericAssetpackMedia(truncatedView, {
        ...output,
        sha256: createHash("sha256").update(truncatedView).digest("hex"),
      }),
    ).toBeNull();

    const danglingAnimation = Buffer.from(payload);
    const channelSampler = Buffer.from('"sampler":0', "ascii");
    const danglingSampler = Buffer.from('"sampler":9', "ascii");
    const samplerOffset = danglingAnimation.indexOf(channelSampler);
    expect(samplerOffset).toBeGreaterThanOrEqual(0);
    danglingSampler.copy(danglingAnimation, samplerOffset);
    expect(
      inspectGenericAssetpackMedia(danglingAnimation, {
        ...output,
        sha256: createHash("sha256").update(danglingAnimation).digest("hex"),
      }),
    ).toBeNull();

    const covertBinaryGap = Buffer.from(payload);
    const jsonChunkLength = covertBinaryGap.readUInt32LE(12);
    const binaryOffset = 12 + 8 + jsonChunkLength + 8;
    covertBinaryGap[binaryOffset + 42] = 1;
    expect(
      inspectGenericAssetpackMedia(covertBinaryGap, {
        ...output,
        sha256: createHash("sha256").update(covertBinaryGap).digest("hex"),
      }),
    ).toBeNull();
  });

  it("matches the authoritative Python GLB subset on the shared parity corpus", async () => {
    const corpus = await readFixture<{
      cases: {
        expected_valid: boolean;
        id: string;
        operations: GlbParityOperation[];
      }[];
      source: string;
    }>("tests/fixtures/generic-assetpack/glb-parity-corpus.json");
    const baseline = await readFile(
      path.join(repositoryRoot, ...corpus.source.split("/")),
    );
    for (const testCase of corpus.cases) {
      const { document, payload } = mutateGlb(
        baseline,
        testCase.operations,
      );
      const nodes = Array.isArray(document.nodes) ? document.nodes.length : 0;
      const output = {
        constraints: {
          kind: "glb",
          max_animations: 32,
          max_bytes: payload.length,
          max_joints: 512,
          max_materials: 512,
          max_meshes: 512,
          max_nodes: 4096,
          max_primitives: 4096,
          max_triangles: 1_000_000,
        },
        media_type: "model/gltf-binary",
        metadata: {
          kind: "glb",
          max_texture_dimension: 0,
          metrics: {
            animations: 1,
            joints: 0,
            materials: 1,
            meshes: 1,
            nodes,
            primitives: 1,
            triangles: 1,
          },
        },
        role: "model",
        runtime_path: "assets/models/glb-parity.glb",
        sha256: createHash("sha256").update(payload).digest("hex"),
        size_bytes: payload.length,
      };
      const inspected = inspectGenericAssetpackMedia(payload, output);
      if (testCase.expected_valid) {
        expect(inspected, testCase.id).not.toBeNull();
      } else {
        expect(inspected, testCase.id).toBeNull();
      }
    }
  });

  it("rejects same-size tampering, extra files, and linked payloads", async () => {
    const fixture = await buildPuzzlePack();

    const tamperedRoot = path.join(temporaryRoot, "tampered-pack");
    await writePack(tamperedRoot, fixture.document, fixture.files);
    const payloadPath = path.join(tamperedRoot, "assets/ui/board.png");
    const payload = await readFile(payloadPath);
    await writeFile(payloadPath, Buffer.alloc(payload.length));
    await expect(verifyGenericAssetpackDirectory(tamperedRoot)).resolves.toBeNull();

    const extraRoot = path.join(temporaryRoot, "extra-pack");
    await writePack(extraRoot, fixture.document, fixture.files);
    await writeFile(path.join(extraRoot, "extra.txt"), "extra");
    await expect(verifyGenericAssetpackDirectory(extraRoot)).resolves.toBeNull();

    if (process.platform !== "win32") {
      const linkedRoot = path.join(temporaryRoot, "linked-pack");
      await writePack(linkedRoot, fixture.document, fixture.files);
      const linkedPayload = path.join(linkedRoot, "assets/ui/board.png");
      await rm(linkedPayload);
      await symlink(
        path.join(extraRoot, "assets/ui/board.png"),
        linkedPayload,
      );
      await expect(verifyGenericAssetpackDirectory(linkedRoot)).resolves.toBeNull();
    }
  });

  it("rejects byte-identical manifest, payload, and nested-directory swaps", async () => {
    const fixture = await buildPuzzlePack();
    const sourceRoot = path.join(temporaryRoot, "swap-source");
    await writePack(sourceRoot, fixture.document, fixture.files);

    const manifestRoot = path.join(temporaryRoot, "manifest-swap");
    await cp(sourceRoot, manifestRoot, { recursive: true });
    await expect(
      verifyGenericAssetpackDirectory(manifestRoot, {
        verificationHook: async (event) => {
          if (event !== "after_manifest_read") {
            return;
          }
          const manifest = path.join(manifestRoot, "assetpack.json");
          const replacement = `${manifest}.replacement`;
          await writeFile(replacement, await readFile(manifest), { flag: "wx" });
          await rename(replacement, manifest);
        },
      }),
    ).resolves.toBeNull();

    const payloadRoot = path.join(temporaryRoot, "payload-swap");
    await cp(sourceRoot, payloadRoot, { recursive: true });
    let payloadSwapped = false;
    await expect(
      verifyGenericAssetpackDirectory(payloadRoot, {
        verificationHook: async (event, relative) => {
          if (
            event !== "after_file_read" ||
            relative === undefined ||
            !relative.startsWith("assets/") ||
            payloadSwapped
          ) {
            return;
          }
          const payload = path.join(payloadRoot, ...relative.split("/"));
          const replacement = `${payload}.replacement`;
          await writeFile(replacement, await readFile(payload), { flag: "wx" });
          await rename(replacement, payload);
          payloadSwapped = true;
        },
      }),
    ).resolves.toBeNull();

    const directoryRoot = path.join(temporaryRoot, "directory-swap");
    await cp(sourceRoot, directoryRoot, { recursive: true });
    await expect(
      verifyGenericAssetpackDirectory(directoryRoot, {
        verificationHook: async (event) => {
          if (event !== "after_tree_snapshot") {
            return;
          }
          const assets = path.join(directoryRoot, "assets");
          const retained = path.join(directoryRoot, "assets-retained");
          await rename(assets, retained);
          await cp(retained, assets, { recursive: true });
          await rm(retained, { recursive: true });
        },
      }),
    ).resolves.toBeNull();
  });
});

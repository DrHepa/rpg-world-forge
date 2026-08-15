import {
  mkdir,
  mkdtemp,
  readFile,
  realpath,
  rm,
  writeFile,
} from "node:fs/promises";
import { createHash } from "node:crypto";
import { createRequire } from "node:module";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

import {
  canonicalGenericAssetpackId,
} from "./generic-assetpack-validation.mjs";
import { canonicalGenericAssetContentHash } from "./generic-asset-validation.mjs";

const require = createRequire(import.meta.url);
const asar = require("@electron/asar");
const SCRIPT_ROOT = path.dirname(fileURLToPath(import.meta.url));
const REPOSITORY_ROOT = path.resolve(SCRIPT_ROOT, "../../..");
const EXPECTED_RUNTIME_FORMAT =
  "world-forge.studio_internal_generic_asset_validator";
const EXPECTED_CONTRACT_FORMATS = Object.freeze([
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
]);
const EXPECTED_SEALED_PACK_FORMATS = Object.freeze([
  "world-forge.assetpack",
]);
const FIXTURE_PATHS = Object.freeze([
  "examples/multigenre-contracts/abstract-puzzle/assets/inventory.json",
  "examples/multigenre-contracts/abstract-puzzle/assets/production/board_ui/license.json",
  "examples/multigenre-contracts/abstract-puzzle/assets/manifest.json",
  "examples/multigenre-contracts/abstract-puzzle/assets/production/board_ui/processing-receipt.json",
  "examples/multigenre-contracts/abstract-puzzle/assets/production/board_ui/recipe.json",
  "examples/multigenre-contracts/abstract-puzzle/assets/production/board_ui/receipt.json",
  "examples/multigenre-contracts/abstract-puzzle/assets/production/board_ui/request.json",
  "examples/multigenre-contracts/abstract-puzzle/assets/production/board_ui/provenance.json",
  "examples/multigenre-contracts/abstract-puzzle/assets/production/board_ui/qa-report.json",
  "examples/multigenre-contracts/abstract-puzzle/assets/production/board_ui/selection.json",
  "examples/multigenre-contracts/branching-narrative/assets/specs/narrative_ui_font.json",
  "examples/multigenre-contracts/abstract-puzzle/assets/style.json",
  "examples/multigenre-contracts/abstract-puzzle/assets/subject.json",
  "examples/multigenre-contracts/abstract-puzzle/assets/target.json",
]);

export const GENERIC_ASSET_RUNTIME_ENTRY =
  "dist-electron/main/generic-asset-runtime.cjs";

/**
 * @param {string} [parent]
 * @returns {Promise<string>}
 */
export async function createCanonicalAssetpackSmokeRoot(
  parent = os.tmpdir(),
) {
  const root = await mkdtemp(
    path.join(parent, "world-forge-generic-assetpack-runtime-"),
  );
  return realpath(root);
}

function fail(code) {
  throw new Error(`generic_asset_runtime_smoke:${code}`);
}

function loadRuntimeModule(modulePath) {
  let resolved;
  try {
    resolved = require.resolve(modulePath);
  } catch {
    fail("module_missing");
  }
  try {
    return require(resolved);
  } catch {
    fail("module_load_failed");
  }
}

async function loadRuntimeFromAsarBytes(archiveBytes) {
  const temporaryRoot = await mkdtemp(
    path.join(os.tmpdir(), "world-forge-generic-asset-runtime-"),
  );
  const archivePath = path.join(temporaryRoot, "app.asar");
  const modulePath = path.join(temporaryRoot, "generic-asset-runtime.cjs");
  let payload;
  try {
    await writeFile(archivePath, archiveBytes, { flag: "wx", mode: 0o600 });
    payload = Buffer.from(
      asar.extractFile(
        archivePath,
        GENERIC_ASSET_RUNTIME_ENTRY.split("/").join(path.sep),
        false,
      ),
    );
  } catch {
    asar.uncache(archivePath);
    await rm(temporaryRoot, { force: true, recursive: true });
    fail("asar_entry_missing");
  }
  try {
    await writeFile(modulePath, payload, { flag: "wx", mode: 0o600 });
    return {
      close: async () => {
        try {
          delete require.cache[require.resolve(modulePath)];
        } catch {
          // The smoke is already complete when the temporary module is absent.
        }
        asar.uncache(archivePath);
        await rm(temporaryRoot, { force: true, recursive: true });
      },
      runtime: loadRuntimeModule(modulePath),
    };
  } catch (error) {
    asar.uncache(archivePath);
    await rm(temporaryRoot, { force: true, recursive: true });
    throw error;
  }
}

function validateRuntimeSurface(runtime) {
  const descriptor = runtime?.GENERIC_ASSET_VALIDATOR_RUNTIME;
  if (
    descriptor === null ||
    typeof descriptor !== "object" ||
    descriptor.format !== EXPECTED_RUNTIME_FORMAT ||
    descriptor.format_version !== 2 ||
    !Array.isArray(descriptor.contract_formats) ||
    JSON.stringify(descriptor.contract_formats) !==
      JSON.stringify(EXPECTED_CONTRACT_FORMATS) ||
    !Array.isArray(descriptor.sealed_pack_formats) ||
    JSON.stringify(descriptor.sealed_pack_formats) !==
      JSON.stringify(EXPECTED_SEALED_PACK_FORMATS) ||
    typeof runtime?.validateGenericAssetContract !== "function" ||
    typeof runtime?.validateGenericAssetpack !== "function" ||
    typeof runtime?.verifyGenericAssetpackDirectory !== "function"
  ) {
    fail("runtime_surface_invalid");
  }
  return {
    validateContract: runtime.validateGenericAssetContract,
    validateAssetpack: runtime.validateGenericAssetpack,
    verifyAssetpack: runtime.verifyGenericAssetpackDirectory,
  };
}

async function loadFixtures() {
  const fixtures = [];
  for (let index = 0; index < FIXTURE_PATHS.length; index += 1) {
    const relative = FIXTURE_PATHS[index];
    let document;
    try {
      document = JSON.parse(
        await readFile(
          path.join(REPOSITORY_ROOT, ...relative.split("/")),
          "utf8",
        ),
      );
    } catch {
      fail("fixture_invalid");
    }
    fixtures.push(document);
  }
  return fixtures;
}

function cloneJson(value) {
  return JSON.parse(JSON.stringify(value));
}

function reseal(value) {
  const digest = canonicalGenericAssetContentHash(value);
  if (digest === null) {
    fail("invalid_mutation_not_canonical");
  }
  value.content_hash = digest;
  return value;
}

function identity(document, idField) {
  return {
    content_hash: document.content_hash,
    format: document.format,
    format_version: document.format_version,
    id: document[idField],
  };
}

function canonicalPretty(value) {
  const ordered = (candidate) => {
    if (Array.isArray(candidate)) {
      return candidate.map(ordered);
    }
    if (candidate !== null && typeof candidate === "object") {
      return Object.fromEntries(
        Object.keys(candidate)
          .sort()
          .map((key) => [key, ordered(candidate[key])]),
      );
    }
    return candidate;
  };
  return Buffer.from(`${JSON.stringify(ordered(value), null, 2)}\n`, "utf8");
}

async function readFixtureDocument(relative) {
  try {
    return JSON.parse(
      await readFile(
        path.join(REPOSITORY_ROOT, ...relative.split("/")),
        "utf8",
      ),
    );
  } catch {
    fail("assetpack_fixture_invalid");
  }
}

export async function buildAssetpackFixture(fixtureName) {
  const fixtureRoot = `examples/multigenre-contracts/${fixtureName}`;
  const manifest = await readFixtureDocument(
    `${fixtureRoot}/assets/manifest.json`,
  );
  if (!Array.isArray(manifest.assets) || manifest.assets.length !== 1) {
    fail("assetpack_fixture_shape_invalid");
  }
  const manifestAsset = manifest.assets[0];
  const assetId = manifestAsset.asset?.asset_id;
  if (
    typeof assetId !== "string" ||
    !Array.isArray(manifestAsset.outputs) ||
    manifestAsset.outputs.length !== 1
  ) {
    fail("assetpack_fixture_shape_invalid");
  }
  const specification = await readFixtureDocument(
    `${fixtureRoot}/assets/specs/${assetId}.json`,
  );
  const productionRoot =
    `${fixtureRoot}/assets/production/${assetId}`;
  const processingReceipt = await readFixtureDocument(
    `${productionRoot}/processing-receipt.json`,
  );
  const license = await readFixtureDocument(
    `${productionRoot}/license.json`,
  );
  const output = manifestAsset.outputs[0];
  const payload = await readFile(
    path.join(REPOSITORY_ROOT, ...`${fixtureRoot}/${output.locator}`.split("/")),
  );
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
    Buffer.compare(
      Buffer.from(left.path, "utf8"),
      Buffer.from(right.path, "utf8"),
    ),
  );
  const inventory = {
    file_count: files.length,
    files,
    total_bytes: files.reduce(
      (total, entry) => total + entry.size_bytes,
      0,
    ),
  };
  inventory.content_hash = canonicalGenericAssetContentHash(inventory);
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
            metadata: processingReceipt.outputs[0].metadata,
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
        processing_receipt: manifestAsset.processing_receipt,
        processing_recipe: manifestAsset.processing_recipe,
        provenance: manifestAsset.provenance,
        qa_report: manifestAsset.qa_report,
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
  };
  document.assetpack_id = canonicalGenericAssetpackId(document);
  document.content_hash = canonicalGenericAssetContentHash(document);
  return {
    document,
    files: new Map([
      [output.runtime_path, payload],
      [noticePath, noticeBytes],
    ]),
    runtimePath: output.runtime_path,
  };
}

export async function writeAssetpack(root, fixture) {
  await mkdir(root, { recursive: false });
  await writeFile(
    path.join(root, "assetpack.json"),
    canonicalPretty(fixture.document),
    { flag: "wx" },
  );
  for (const [relative, payload] of fixture.files) {
    const target = path.join(root, ...relative.split("/"));
    await mkdir(path.dirname(target), { recursive: true });
    await writeFile(target, payload, { flag: "wx" });
  }
}

async function smokeAssetpacks(runtimeSurface) {
  const temporaryRoot = await createCanonicalAssetpackSmokeRoot();
  let verified = 0;
  let tamperRejected = 0;
  try {
    for (const fixtureName of [
      "abstract-puzzle",
      "branching-narrative",
    ]) {
      const fixture = await buildAssetpackFixture(fixtureName);
      const structural = runtimeSurface.validateAssetpack(fixture.document);
      if (
        structural === null ||
        !Object.isFrozen(structural) ||
        Object.hasOwn(structural, "status")
      ) {
        fail("valid_assetpack_document_rejected");
      }
      const root = path.join(temporaryRoot, fixtureName);
      await writeAssetpack(root, fixture);
      const evidence = await runtimeSurface.verifyAssetpack(root);
      if (
        evidence === null ||
        evidence.status !== "sealed" ||
        evidence.assetpack_id !== fixture.document.assetpack_id ||
        !Object.isFrozen(evidence)
      ) {
        fail("valid_assetpack_directory_rejected");
      }
      verified += 1;
      const payloadPath = path.join(
        root,
        ...fixture.runtimePath.split("/"),
      );
      const payload = fixture.files.get(fixture.runtimePath);
      await writeFile(payloadPath, Buffer.alloc(payload.length));
      if ((await runtimeSurface.verifyAssetpack(root)) !== null) {
        fail("tampered_assetpack_accepted");
      }
      tamperRejected += 1;
    }
  } finally {
    await rm(temporaryRoot, { force: true, recursive: true });
  }
  return {
    sealed_packs_verified: verified,
    sealed_tamper_rejections: tamperRejected,
  };
}

function d2bInvalidMutations(fixture) {
  const mutations = [];
  if (fixture.format === "world-forge.asset_processing_recipe") {
    const crossedLicense = cloneJson(fixture);
    crossedLicense.steps[0].license_record.candidate_artifact_id =
      "crossed_candidate";
    crossedLicense.licenses[0].license_record.candidate_artifact_id =
      "crossed_candidate";
    mutations.push(reseal(crossedLicense));

    const pathCollision = cloneJson(fixture);
    pathCollision.steps[0].runtime_path =
      pathCollision.steps[0].source_locator;
    mutations.push(reseal(pathCollision));
  } else if (
    fixture.format === "world-forge.asset_processing_receipt"
  ) {
    const duplicateRole = cloneJson(fixture);
    const duplicate = cloneJson(duplicateRole.outputs[0]);
    duplicate.step_id = "step_duplicate_runtime_smoke";
    duplicate.candidate_artifact_id = "duplicate_runtime_smoke_candidate";
    duplicate.runtime_path = "assets/ui/duplicate-board.png";
    duplicate.locator =
      "assets/production/board_ui/processed/texture/duplicate-board.png";
    duplicateRole.outputs.push(duplicate);
    mutations.push(reseal(duplicateRole));

    const contradictory = cloneJson(fixture);
    contradictory.failure_reasons = ["processor_interrupted"];
    mutations.push(reseal(contradictory));
  } else if (fixture.format === "world-forge.asset_qa_report") {
    const blockerMismatch = cloneJson(fixture);
    blockerMismatch.status = "failed";
    blockerMismatch.blockers = ["fabricated_blocker"];
    mutations.push(reseal(blockerMismatch));

    const criterionMismatch = cloneJson(fixture);
    criterionMismatch.acceptance_criteria[0].criterion_index = 1;
    mutations.push(reseal(criterionMismatch));

    const metadataMismatch = cloneJson(fixture);
    metadataMismatch.outputs[0].metadata = null;
    mutations.push(reseal(metadataMismatch));
  } else if (fixture.format === "world-forge.asset_manifest") {
    const stateMismatch = cloneJson(fixture);
    stateMismatch.assets[0].state = "processed";
    mutations.push(reseal(stateMismatch));

    const pathCollision = cloneJson(fixture);
    const duplicateAsset = cloneJson(pathCollision.assets[0]);
    duplicateAsset.asset.asset_id = "second_manifest_asset";
    duplicateAsset.asset.content_hash = "f".repeat(64);
    pathCollision.assets.push(duplicateAsset);
    pathCollision.assets.sort((left, right) =>
      Buffer.compare(
        Buffer.from(left.asset.asset_id, "utf8"),
        Buffer.from(right.asset.asset_id, "utf8"),
      ),
    );
    mutations.push(reseal(pathCollision));
  }
  return mutations;
}

async function smokeRuntime(runtime, artifactKind) {
  const runtimeSurface = validateRuntimeSurface(runtime);
  const validate = runtimeSurface.validateContract;
  const fixtures = await loadFixtures();
  const acceptedFormats = [];
  let invalidDocumentsRejected = 0;
  for (let index = 0; index < fixtures.length; index += 1) {
    const fixture = fixtures[index];
    const validated = validate(fixture);
    if (
      validated === null ||
      validated.format !== fixture.format ||
      !Object.isFrozen(validated)
    ) {
      fail("valid_document_rejected");
    }
    acceptedFormats.push(validated.format);

    const invalid = JSON.parse(JSON.stringify(fixture));
    delete invalid.format_version;
    if (validate(invalid) !== null) {
      fail("invalid_document_accepted");
    }
    invalidDocumentsRejected += 1;
    const semanticInvalids = d2bInvalidMutations(fixture);
    for (
      let mutationIndex = 0;
      mutationIndex < semanticInvalids.length;
      mutationIndex += 1
    ) {
      if (validate(semanticInvalids[mutationIndex]) !== null) {
        fail("semantic_invalid_document_accepted");
      }
      invalidDocumentsRejected += 1;
    }
  }
  acceptedFormats.sort();
  if (
    JSON.stringify(acceptedFormats) !==
    JSON.stringify(EXPECTED_CONTRACT_FORMATS)
  ) {
    fail("contract_matrix_incomplete");
  }
  const assetpacks = await smokeAssetpacks(runtimeSurface);
  return {
    accepted_formats: acceptedFormats,
    artifact_kind: artifactKind,
    format: "world-forge.studio_generic_asset_runtime_smoke",
    format_version: 2,
    invalid_documents_rejected: invalidDocumentsRejected,
    sealed_pack_formats: [...EXPECTED_SEALED_PACK_FORMATS],
    sealed_packs_verified: assetpacks.sealed_packs_verified,
    sealed_tamper_rejections: assetpacks.sealed_tamper_rejections,
    status: "verified",
    valid_documents_accepted: fixtures.length,
  };
}

export async function verifyGenericAssetRuntimeArtifact({
  artifactKind,
  artifactPath,
}) {
  if (
    !["asar", "module"].includes(artifactKind) ||
    typeof artifactPath !== "string" ||
    !path.isAbsolute(artifactPath) ||
    path.normalize(artifactPath) !== artifactPath
  ) {
    fail("invalid_arguments");
  }
  if (artifactKind === "module") {
    return smokeRuntime(loadRuntimeModule(artifactPath), artifactKind);
  }
  let archiveBytes;
  try {
    archiveBytes = await readFile(artifactPath);
  } catch {
    fail("asar_entry_missing");
  }
  const loaded = await loadRuntimeFromAsarBytes(archiveBytes);
  try {
    return await smokeRuntime(loaded.runtime, artifactKind);
  } finally {
    await loaded.close();
  }
}

export async function verifyGenericAssetRuntimeSnapshot({
  artifactBytes,
  expectedSha256,
  expectedSize,
}) {
  if (
    !Buffer.isBuffer(artifactBytes) ||
    !Number.isSafeInteger(expectedSize) ||
    expectedSize < 1 ||
    artifactBytes.length !== expectedSize ||
    typeof expectedSha256 !== "string" ||
    !/^[0-9a-f]{64}$/u.test(expectedSha256) ||
    createHash("sha256").update(artifactBytes).digest("hex") !== expectedSha256
  ) {
    fail("snapshot_identity_mismatch");
  }
  const retainedBytes = Buffer.from(artifactBytes);
  const loaded = await loadRuntimeFromAsarBytes(retainedBytes);
  try {
    const report = await smokeRuntime(loaded.runtime, "asar");
    if (
      retainedBytes.length !== expectedSize ||
      createHash("sha256").update(retainedBytes).digest("hex") !==
        expectedSha256
    ) {
      fail("snapshot_identity_mismatch");
    }
    return Object.freeze({
      ...report,
      artifact_sha256: expectedSha256,
      artifact_size_bytes: expectedSize,
    });
  } finally {
    await loaded.close();
  }
}

function parseArguments(argv) {
  if (
    argv.length !== 2 ||
    !["--asar", "--module"].includes(argv[0])
  ) {
    fail("invalid_arguments");
  }
  return {
    artifactKind: argv[0] === "--asar" ? "asar" : "module",
    artifactPath: path.resolve(argv[1]),
  };
}

if (
  process.argv[1] &&
  path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)
) {
  try {
    const report = await verifyGenericAssetRuntimeArtifact(
      parseArguments(process.argv.slice(2)),
    );
    process.stdout.write(`${JSON.stringify(report)}\n`);
  } catch (error) {
    process.stderr.write(
      `${error instanceof Error ? error.message : "generic_asset_runtime_smoke:failed"}\n`,
    );
    process.exitCode = 1;
  }
}

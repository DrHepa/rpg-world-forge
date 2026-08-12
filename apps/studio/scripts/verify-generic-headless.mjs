import { createHash } from "node:crypto";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { createRequire } from "node:module";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

import {
  canonicalGenericHeadlessContentHash,
  canonicalGenericHeadlessId,
} from "./generic-headless-validation.mjs";

const scriptRoot = path.dirname(fileURLToPath(import.meta.url));
const repositoryRoot = path.resolve(scriptRoot, "../../..");
const require = createRequire(import.meta.url);
const asar = require("@electron/asar");

export const GENERIC_HEADLESS_ENTRY =
  "dist-electron/main/generic-headless-evidence.cjs";

const FIXTURES = Object.freeze([
  "examples/multigenre-contracts/abstract-puzzle/runtime/headless/execution-script.json",
  "examples/multigenre-contracts/branching-narrative/runtime/headless/execution-script.json",
]);

function fail(code) {
  throw new Error(`generic_headless_smoke:${code}`);
}

function digest(payload) {
  return createHash("sha256").update(payload).digest("hex");
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
    path.join(os.tmpdir(), "world-forge-generic-headless-"),
  );
  const archivePath = path.join(temporaryRoot, "app.asar");
  const modulePath = path.join(
    temporaryRoot,
    "generic-headless-evidence.cjs",
  );
  try {
    await writeFile(archivePath, archiveBytes, {
      flag: "wx",
      mode: 0o600,
    });
    const payload = Buffer.from(
      asar.extractFile(
        archivePath,
        GENERIC_HEADLESS_ENTRY.split("/").join(path.sep),
        false,
      ),
    );
    await writeFile(modulePath, payload, {
      flag: "wx",
      mode: 0o600,
    });
    return {
      close: async () => {
        try {
          delete require.cache[require.resolve(modulePath)];
        } catch {
          // The retained smoke is already complete if the module disappeared.
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

async function readFixture(relative) {
  return JSON.parse(
    await readFile(
      path.join(repositoryRoot, ...relative.split("/")),
      "utf8",
    ),
  );
}

function reseal(document) {
  document.script_id = canonicalGenericHeadlessId(document);
  document.content_hash =
    canonicalGenericHeadlessContentHash(document);
}

async function smokeRuntime(runtime, artifactKind) {
  const descriptor = runtime.GENERIC_HEADLESS_INSPECTOR_RUNTIME;
  if (
    descriptor?.format !==
      "world-forge.studio_internal_headless_inspector" ||
    descriptor?.format_version !== 1 ||
    descriptor?.interprets_gameplay !== false ||
    descriptor?.semantic_boundary !== "packaged_python_required" ||
    JSON.stringify(descriptor?.contract_formats) !==
      JSON.stringify([
        "world-forge.game_execution_script",
        "world-forge.headless_evidence_set",
        "world-forge.headless_execution_receipt",
      ]) ||
    typeof runtime.validateGenericHeadlessContract !== "function" ||
    typeof runtime.inspectGenericHeadlessContract !== "function" ||
    typeof runtime.buildGenericHeadlessPythonInvocation !== "function" ||
    typeof runtime.hasVerifiedGenericHeadlessPythonResult !==
      "function"
  ) {
    fail("surface_invalid");
  }

  let documentsVerified = 0;
  let tamperRejections = 0;
  for (const relative of FIXTURES) {
    const document = await readFixture(relative);
    const validated =
      runtime.validateGenericHeadlessContract(document);
    const inspection =
      runtime.inspectGenericHeadlessContract(validated);
    if (
      validated === null ||
      inspection?.status !== "structurally_valid" ||
      inspection?.semantic_verification !== "required_python"
    ) {
      fail("fixture_rejected");
    }
    documentsVerified += 1;

    const hashTamper = structuredClone(document);
    hashTamper.content_hash = "0".repeat(64);
    if (
      runtime.validateGenericHeadlessContract(hashTamper) !== null
    ) {
      fail("tamper_accepted");
    }
    tamperRejections += 1;

    const orderTamper = structuredClone(document);
    orderTamper.scenarios.reverse();
    reseal(orderTamper);
    if (
      runtime.validateGenericHeadlessContract(orderTamper) !== null
    ) {
      fail("self_resealed_order_accepted");
    }
    tamperRejections += 1;
  }

  const authorityResult = {
    content_hash: "a".repeat(64),
    evidence_set_id: `headless_evidence_set_${"b".repeat(40)}`,
    execution_status: "headless_verified",
    integrity: "valid",
    path: path.resolve(repositoryRoot, "headless-evidence"),
    release: "blocked",
    supported: false,
  };
  if (
    !runtime.hasVerifiedGenericHeadlessPythonResult(authorityResult)
  ) {
    fail("python_authority_result_rejected");
  }
  for (const tampered of [
    { ...authorityResult, content_hash: "0".repeat(63) },
    {
      ...authorityResult,
      evidence_set_id: "headless_evidence_set_wrong",
    },
    { ...authorityResult, execution_status: "native_verified" },
    { ...authorityResult, integrity: "invalid" },
    { ...authorityResult, path: "relative/evidence" },
    { ...authorityResult, release: "ready" },
    { ...authorityResult, supported: true },
    { ...authorityResult, extra: true },
  ]) {
    if (runtime.hasVerifiedGenericHeadlessPythonResult(tampered)) {
      fail("python_authority_tamper_accepted");
    }
    tamperRejections += 1;
  }

  return Object.freeze({
    artifact_kind: artifactKind,
    contract_formats: descriptor.contract_formats,
    documents_verified: documentsVerified,
    format: "world-forge.studio_generic_headless_smoke",
    format_version: 1,
    release: "blocked",
    semantic_boundary: descriptor.semantic_boundary,
    status: "verified",
    supported: false,
    tamper_rejections: tamperRejections,
  });
}

export async function verifyGenericHeadlessArtifact({
  artifactKind,
  artifactPath,
}) {
  if (
    !["asar", "module"].includes(artifactKind) ||
    typeof artifactPath !== "string" ||
    !path.isAbsolute(artifactPath)
  ) {
    fail("artifact_invalid");
  }
  if (artifactKind === "module") {
    return smokeRuntime(
      loadRuntimeModule(artifactPath),
      artifactKind,
    );
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

export async function verifyGenericHeadlessSnapshot({
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
    digest(artifactBytes) !== expectedSha256
  ) {
    fail("snapshot_identity_mismatch");
  }
  const retainedBytes = Buffer.from(artifactBytes);
  const loaded = await loadRuntimeFromAsarBytes(retainedBytes);
  try {
    const report = await smokeRuntime(loaded.runtime, "asar");
    if (
      retainedBytes.length !== expectedSize ||
      digest(retainedBytes) !== expectedSha256
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

async function run(argv = process.argv.slice(2)) {
  if (
    argv.length !== 2 ||
    !["--asar", "--module"].includes(argv[0])
  ) {
    fail("arguments_invalid");
  }
  const report = await verifyGenericHeadlessArtifact({
    artifactKind: argv[0] === "--asar" ? "asar" : "module",
    artifactPath: path.resolve(argv[1]),
  });
  process.stdout.write(`${JSON.stringify(report)}\n`);
}

if (
  process.argv[1] &&
  path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)
) {
  try {
    await run();
  } catch (error) {
    const message =
      error instanceof Error
        ? error.message
        : "generic_headless_smoke:unknown";
    process.stderr.write(
      `Studio generic headless verification failed: ${message}\n`,
    );
    process.exitCode = 1;
  }
}

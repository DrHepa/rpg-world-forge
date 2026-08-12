import { createHash } from "node:crypto";
import {
  mkdtemp,
  readFile,
  rm,
  writeFile,
} from "node:fs/promises";
import { createRequire } from "node:module";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

import {
  GENERIC_RUNTIME_EXECUTION_POLICY,
} from "./generic-runtime-policy.mjs";

const require = createRequire(import.meta.url);
const asar = require("@electron/asar");
const SCRIPT_ROOT = path.dirname(fileURLToPath(import.meta.url));
const REPOSITORY_ROOT = path.resolve(SCRIPT_ROOT, "../../..");
const CORPUS_PATH = path.join(
  REPOSITORY_ROOT,
  "tests/fixtures/generic-runtime/parity-corpus.json",
);
const EXPECTED_FORMATS = Object.freeze([
  "world-forge.game_runtime_composition",
  "world-forge.game_runtime_snapshot",
  "world-forge.runtime_adapter",
  "world-forge.runtime_adapter_registry",
  "world-forge.runtime_evidence",
  "world-forge.runtime_support_report",
]);

export const GENERIC_RUNTIME_CONTRACTS_ENTRY =
  "dist-electron/main/generic-runtime-contracts.cjs";

function fail(code, cause) {
  const error = new Error(`generic_runtime_contract_smoke:${code}`);
  if (cause !== undefined) {
    Object.defineProperty(error, "cause", {
      configurable: false,
      enumerable: false,
      value: cause,
      writable: false,
    });
  }
  throw error;
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
  } catch (error) {
    fail("module_load_failed", error);
  }
}

function exactAsarEntryBytes(archiveBytes, archivePath, relative) {
  let rawHeader;
  try {
    rawHeader = asar.getRawHeader(archivePath);
  } catch (error) {
    fail("asar_entry_missing", error);
  }
  if (
    rawHeader === null ||
    typeof rawHeader !== "object" ||
    !Number.isSafeInteger(rawHeader.headerSize) ||
    rawHeader.headerSize < 1 ||
    rawHeader.header === null ||
    typeof rawHeader.header !== "object"
  ) {
    fail("asar_entry_missing");
  }
  let entry = rawHeader.header;
  for (const component of relative.split("/")) {
    if (
      entry === null ||
      typeof entry !== "object" ||
      entry.files === null ||
      typeof entry.files !== "object" ||
      !Object.hasOwn(entry.files, component)
    ) {
      fail("asar_entry_missing");
    }
    entry = entry.files[component];
  }
  const integrity = entry?.integrity;
  if (
    entry === null ||
    typeof entry !== "object" ||
    Object.hasOwn(entry, "files") ||
    Object.hasOwn(entry, "link") ||
    entry.unpacked === true ||
    !Number.isSafeInteger(entry.size) ||
    entry.size < 1 ||
    typeof entry.offset !== "string" ||
    !/^(?:0|[1-9][0-9]*)$/u.test(entry.offset) ||
    integrity === null ||
    typeof integrity !== "object" ||
    integrity.algorithm !== "SHA256" ||
    typeof integrity.hash !== "string" ||
    !/^[0-9a-f]{64}$/u.test(integrity.hash)
  ) {
    fail("asar_entry_missing");
  }
  const relativeOffset = Number(entry.offset);
  const start = 8 + rawHeader.headerSize + relativeOffset;
  const end = start + entry.size;
  if (
    !Number.isSafeInteger(relativeOffset) ||
    !Number.isSafeInteger(start) ||
    !Number.isSafeInteger(end) ||
    start < 0 ||
    end > archiveBytes.length
  ) {
    fail("asar_entry_missing");
  }
  const payload = Buffer.from(archiveBytes.subarray(start, end));
  if (
    payload.length !== entry.size ||
    createHash("sha256").update(payload).digest("hex") !== integrity.hash
  ) {
    fail("asar_entry_missing");
  }
  return payload;
}

async function loadRuntimeFromAsarBytes(archiveBytes) {
  const temporaryRoot = await mkdtemp(
    path.join(os.tmpdir(), "world-forge-generic-runtime-contracts-"),
  );
  const archivePath = path.join(temporaryRoot, "app.asar");
  const modulePath = path.join(temporaryRoot, "generic-runtime-contracts.cjs");
  try {
    await writeFile(archivePath, archiveBytes, { flag: "wx", mode: 0o600 });
    const visibleArchive = await readFile(archivePath);
    if (!visibleArchive.equals(archiveBytes)) {
      fail("asar_entry_missing");
    }
    const payload = exactAsarEntryBytes(
      visibleArchive,
      archivePath,
      GENERIC_RUNTIME_CONTRACTS_ENTRY,
    );
    await writeFile(modulePath, payload, { flag: "wx", mode: 0o600 });
    const visibleModule = await readFile(modulePath);
    if (!visibleModule.equals(payload)) {
      fail("asar_entry_missing");
    }
    return {
      close: async () => {
        try {
          delete require.cache[require.resolve(modulePath)];
        } catch {
          // The smoke is complete when its temporary module is already absent.
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
  const descriptor = runtime?.GENERIC_RUNTIME_VALIDATOR_RUNTIME;
  if (
    descriptor === null ||
    typeof descriptor !== "object" ||
    descriptor.format !==
      "world-forge.studio_internal_generic_runtime_validator" ||
    descriptor.format_version !== 1 ||
    JSON.stringify(descriptor.contract_formats) !==
      JSON.stringify(EXPECTED_FORMATS) ||
    JSON.stringify(descriptor.execution_semantics) !==
      JSON.stringify(GENERIC_RUNTIME_EXECUTION_POLICY) ||
    typeof runtime?.validateGenericRuntimeContract !== "function" ||
    typeof runtime?.inspectGenericRuntimeSupport !== "function"
  ) {
    fail("runtime_surface_invalid");
  }
  return runtime.validateGenericRuntimeContract;
}

async function loadCorpus() {
  let corpus;
  try {
    corpus = JSON.parse(await readFile(CORPUS_PATH, "utf8"));
  } catch {
    fail("corpus_invalid");
  }
  if (
    corpus === null ||
    typeof corpus !== "object" ||
    corpus.format !== "world-forge.runtime_parity_corpus" ||
    corpus.format_version !== 1 ||
    JSON.stringify(corpus.execution_semantics) !==
      JSON.stringify(GENERIC_RUNTIME_EXECUTION_POLICY) ||
    !Array.isArray(corpus.valid) ||
    corpus.valid.length < 7 ||
    !Array.isArray(corpus.invalid) ||
    corpus.invalid.length < 21
  ) {
    fail("corpus_invalid");
  }
  const caseIds = [...corpus.valid, ...corpus.invalid].map(
    (testCase) => testCase?.case_id,
  );
  if (
    caseIds.some((caseId) => typeof caseId !== "string") ||
    new Set(caseIds).size !== caseIds.length
  ) {
    fail("corpus_invalid");
  }
  return corpus;
}

function sameJson(left, right) {
  const ordered = (value) => {
    if (Array.isArray(value)) {
      const result = [];
      for (let index = 0; index < value.length; index += 1) {
        result.push(ordered(value[index]));
      }
      return result;
    }
    if (value !== null && typeof value === "object") {
      return Object.fromEntries(
        Object.keys(value)
          .sort()
          .map((key) => [key, ordered(value[key])]),
      );
    }
    return value;
  };
  return JSON.stringify(ordered(left)) === JSON.stringify(ordered(right));
}

async function smokeRuntime(runtime, artifactKind) {
  const validate = validateRuntimeSurface(runtime);
  const corpus = await loadCorpus();
  let validDocumentsAccepted = 0;
  for (let index = 0; index < corpus.valid.length; index += 1) {
    const document = corpus.valid[index]?.document;
    let checked;
    try {
      checked = validate(document);
    } catch {
      fail("valid_document_rejected");
    }
    if (!sameJson(checked, document)) {
      fail("valid_document_changed");
    }
    validDocumentsAccepted += 1;
  }
  let invalidDocumentsRejected = 0;
  for (let index = 0; index < corpus.invalid.length; index += 1) {
    const document = corpus.invalid[index]?.document;
    let rejected = false;
    try {
      validate(document);
    } catch {
      rejected = true;
    }
    if (!rejected) {
      fail("invalid_document_accepted");
    }
    invalidDocumentsRejected += 1;
  }
  return Object.freeze({
    artifact_kind: artifactKind,
    execution_semantics: GENERIC_RUNTIME_EXECUTION_POLICY,
    format: "world-forge.studio_generic_runtime_contract_smoke",
    format_version: 1,
    invalid_documents_rejected: invalidDocumentsRejected,
    status: "verified",
    valid_documents_accepted: validDocumentsAccepted,
  });
}

export async function verifyGenericRuntimeArtifact({
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

export async function verifyGenericRuntimeSnapshot({
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
    createHash("sha256").update(artifactBytes).digest("hex") !==
      expectedSha256
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
    const report = await verifyGenericRuntimeArtifact(
      parseArguments(process.argv.slice(2)),
    );
    process.stdout.write(`${JSON.stringify(report)}\n`);
  } catch (error) {
    process.stderr.write(
      `${error instanceof Error ? error.message : "generic_runtime_contract_smoke:failed"}\n`,
    );
    process.exitCode = 1;
  }
}

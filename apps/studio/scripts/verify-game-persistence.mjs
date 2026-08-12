import { createHash } from "node:crypto";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { createRequire } from "node:module";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

import {
  canonicalGamePersistenceContentHash,
  canonicalGamePersistenceId,
} from "./game-persistence-validation.mjs";

const scriptRoot = path.dirname(fileURLToPath(import.meta.url));
const repositoryRoot = path.resolve(scriptRoot, "../../..");
const require = createRequire(import.meta.url);
const asar = require("@electron/asar");

export const GAME_PERSISTENCE_ENTRY =
  "dist-electron/main/generic-game-persistence.cjs";

const FIXTURES = Object.freeze([
  "examples/multigenre-contracts/abstract-puzzle/runtime/persistence/saves/initial.json",
  "examples/multigenre-contracts/abstract-puzzle/runtime/persistence/saves/solved.json",
  "examples/multigenre-contracts/abstract-puzzle/runtime/persistence/replays/solve.json",
  "examples/multigenre-contracts/abstract-puzzle/runtime/persistence/replays/zero-step.json",
  "examples/multigenre-contracts/branching-narrative/runtime/persistence/saves/left.json",
  "examples/multigenre-contracts/branching-narrative/runtime/persistence/saves/right.json",
  "examples/multigenre-contracts/branching-narrative/runtime/persistence/replays/left.json",
  "examples/multigenre-contracts/branching-narrative/runtime/persistence/replays/right.json",
  "examples/multigenre-contracts/abstract-puzzle/runtime/persistence/generations/saves/initial.json",
  "examples/multigenre-contracts/abstract-puzzle/runtime/persistence/generations/saves/solved.json",
  "examples/multigenre-contracts/abstract-puzzle/runtime/persistence/generations/replays/solve.json",
  "examples/multigenre-contracts/abstract-puzzle/runtime/persistence/generations/replays/zero-step.json",
  "examples/multigenre-contracts/branching-narrative/runtime/persistence/generations/saves/left.json",
  "examples/multigenre-contracts/branching-narrative/runtime/persistence/generations/saves/right.json",
  "examples/multigenre-contracts/branching-narrative/runtime/persistence/generations/replays/left.json",
  "examples/multigenre-contracts/branching-narrative/runtime/persistence/generations/replays/right.json",
]);

function fail(code) {
  throw new Error(`game_persistence_smoke:${code}`);
}

function digest(payload) {
  return createHash("sha256").update(payload).digest("hex");
}

function unsafeCanonicalHash(value, { omitContentHash = false } = {}) {
  const copy = (candidate, root = false) => {
    if (Array.isArray(candidate)) {
      return candidate.map((item) => copy(item));
    }
    if (candidate !== null && typeof candidate === "object") {
      return Object.fromEntries(
        Object.keys(candidate)
          .filter(
            (key) =>
              !(root && omitContentHash && key === "content_hash"),
          )
          .sort((left, right) =>
            Buffer.compare(
              Buffer.from(left, "utf8"),
              Buffer.from(right, "utf8"),
            ),
          )
          .map((key) => [key, copy(candidate[key])]),
      );
    }
    return candidate;
  };
  return digest(
    Buffer.from(JSON.stringify(copy(value, true)), "utf8"),
  );
}

function unsafeResealSave(document) {
  const seed = Object.fromEntries(
    Object.entries(document).filter(
      ([key]) => key !== "save_id" && key !== "content_hash",
    ),
  );
  document.save_id =
    `game_save_${unsafeCanonicalHash(seed).slice(0, 48)}`;
  document.content_hash = unsafeCanonicalHash(document, {
    omitContentHash: true,
  });
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
    path.join(os.tmpdir(), "world-forge-game-persistence-"),
  );
  const archivePath = path.join(temporaryRoot, "app.asar");
  const modulePath = path.join(
    temporaryRoot,
    "generic-game-persistence.cjs",
  );
  try {
    await writeFile(archivePath, archiveBytes, {
      flag: "wx",
      mode: 0o600,
    });
    const payload = Buffer.from(
      asar.extractFile(
        archivePath,
        GAME_PERSISTENCE_ENTRY.split("/").join(path.sep),
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
  const idField =
    document.format === "world-forge.game_save"
      ? "save_id"
      : "replay_id";
  document[idField] = canonicalGamePersistenceId(document);
  document.content_hash = canonicalGamePersistenceContentHash(document);
}

async function smokeRuntime(runtime, artifactKind) {
  const descriptor =
    runtime.GENERIC_GAME_PERSISTENCE_INSPECTOR_RUNTIME;
  if (
    descriptor?.format !==
      "world-forge.studio_internal_game_persistence_inspector" ||
    descriptor?.format_version !== 1 ||
    descriptor?.interprets_gameplay !== false ||
    descriptor?.semantic_boundary !== "packaged_python_required" ||
    JSON.stringify(descriptor?.contract_formats) !==
      JSON.stringify([
        "world-forge.game_replay",
        "world-forge.game_save",
        "world-forge.persistence_generation",
      ]) ||
    typeof runtime.validateGenericGamePersistence !== "function" ||
    typeof runtime.inspectGenericGamePersistence !== "function" ||
    typeof runtime.buildGenericGamePersistencePythonInvocation !==
      "function"
  ) {
    fail("surface_invalid");
  }

  let verified = 0;
  let tamperRejected = 0;
  for (const relative of FIXTURES) {
    const document = await readFixture(relative);
    const validated =
      runtime.validateGenericGamePersistence(document);
    const inspection =
      runtime.inspectGenericGamePersistence(validated);
    if (
      validated === null ||
      inspection?.status !== "structurally_valid" ||
      inspection?.semantic_verification !== "required_python"
    ) {
      fail("fixture_rejected");
    }
    verified += 1;

    const tampered = structuredClone(document);
    tampered.content_hash = "0".repeat(64);
    if (
      runtime.validateGenericGamePersistence(tampered) !== null
    ) {
      fail("tamper_accepted");
    }
    tamperRejected += 1;
  }

  const incoherentSave = await readFixture(FIXTURES[1]);
  incoherentSave.state.saved_hash = "f".repeat(64);
  reseal(incoherentSave);
  if (
    runtime.validateGenericGamePersistence(incoherentSave) !== null
  ) {
    fail("self_resealed_save_accepted");
  }
  tamperRejected += 1;

  const surrogateSave = await readFixture(FIXTURES[1]);
  surrogateSave.state.saved.board[0] = "\ud800";
  surrogateSave.state.saved_hash =
    unsafeCanonicalHash(surrogateSave.state.saved);
  unsafeResealSave(surrogateSave);
  if (
    runtime.validateGenericGamePersistence(surrogateSave) !== null
  ) {
    fail("self_resealed_surrogate_save_accepted");
  }
  tamperRejected += 1;

  const surrogateKeySave = await readFixture(FIXTURES[1]);
  surrogateKeySave.state.saved["\ud800"] = 0;
  surrogateKeySave.state.saved_hash =
    unsafeCanonicalHash(surrogateKeySave.state.saved);
  unsafeResealSave(surrogateKeySave);
  if (
    runtime.validateGenericGamePersistence(surrogateKeySave) !== null
  ) {
    fail("self_resealed_surrogate_key_save_accepted");
  }
  tamperRejected += 1;

  const incoherentReplay = await readFixture(FIXTURES[2]);
  incoherentReplay.steps[0].index = 7;
  incoherentReplay.trace_hash =
    canonicalGamePersistenceContentHash({
      steps: incoherentReplay.steps,
    });
  reseal(incoherentReplay);
  if (
    runtime.validateGenericGamePersistence(incoherentReplay) !== null
  ) {
    fail("self_resealed_replay_accepted");
  }
  tamperRejected += 1;

  const incoherentGeneration = await readFixture(FIXTURES[8]);
  incoherentGeneration.payload_hash = "f".repeat(64);
  incoherentGeneration.content_hash =
    canonicalGamePersistenceContentHash(incoherentGeneration);
  if (
    runtime.validateGenericGamePersistence(incoherentGeneration) !==
    null
  ) {
    fail("self_resealed_generation_accepted");
  }
  tamperRejected += 1;

  return Object.freeze({
    artifact_kind: artifactKind,
    contract_formats: descriptor.contract_formats,
    documents_verified: verified,
    format: "world-forge.studio_game_persistence_smoke",
    format_version: 1,
    release: "blocked",
    semantic_boundary: descriptor.semantic_boundary,
    status: "verified",
    supported: false,
    tamper_rejections: tamperRejected,
  });
}

export async function verifyGamePersistenceArtifact({
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

export async function verifyGamePersistenceSnapshot({
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
  const report = await verifyGamePersistenceArtifact({
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
        : "game_persistence_smoke:unknown";
    process.stderr.write(`Studio game persistence verification failed: ${message}\n`);
    process.exitCode = 1;
  }
}

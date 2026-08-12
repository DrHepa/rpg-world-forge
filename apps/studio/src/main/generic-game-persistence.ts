import { spawn } from "node:child_process";
import path from "node:path";

import Ajv2020 from "ajv/dist/2020.js";

import gameReplaySchema from "../../../../schemas/game-replay.schema.json";
import gameSaveSchema from "../../../../schemas/game-save.schema.json";
import persistenceGenerationSchema from "../../../../schemas/persistence-generation.schema.json";
import {
  canonicalGamePersistenceByteLength,
  canonicalGamePersistenceContentHash,
  hasCoherentGamePersistence,
  hasCoherentPersistenceGeneration,
} from "../../scripts/game-persistence-validation.mjs";
import {
  decodeStrictJsonObject,
  snapshotStrictJsonObject,
} from "../../scripts/strict-json.mjs";

declare const validatedGenericGamePersistenceBrand: unique symbol;
export type ValidatedGenericGamePersistence = Readonly<
  Record<string, unknown>
> & {
  readonly [validatedGenericGamePersistenceBrand]: true;
};

export const GENERIC_GAME_PERSISTENCE_INSPECTOR_RUNTIME = Object.freeze({
  contract_formats: Object.freeze([
    "world-forge.game_replay",
    "world-forge.game_save",
    "world-forge.persistence_generation",
  ]),
  format: "world-forge.studio_internal_game_persistence_inspector",
  format_version: 1,
  interprets_gameplay: false,
  semantic_boundary: "packaged_python_required",
});

const ajv = new Ajv2020({
  allErrors: true,
  ownProperties: true,
  strict: true,
});
ajv.addKeyword({
  keyword: "x-world-forge-canonical-content-hash",
  schemaType: "boolean",
  type: "object",
  validate: (required: boolean, value: unknown) =>
    required !== true ||
    canonicalGamePersistenceContentHash(value) ===
      Reflect.get(value as object, "content_hash"),
});
ajv.addKeyword({
  keyword: "x-world-forge-game-persistence-coherent",
  schemaType: "string",
  type: "object",
  validate: (kind: string, value: unknown) =>
    (kind === "game_save" || kind === "game_replay") &&
    hasCoherentGamePersistence(value),
});
ajv.addKeyword({
  keyword: "x-world-forge-persistence-generation-coherent",
  schemaType: "boolean",
  type: "object",
  validate: (required: boolean, value: unknown) =>
    required !== true || hasCoherentPersistenceGeneration(value),
});
const validateSave = ajv.compile(gameSaveSchema);
const validateReplay = ajv.compile(gameReplaySchema);
const validateGeneration = ajv.compile(persistenceGenerationSchema);
const MAX_GAME_SAVE_BYTES = 256 * 1024;
const MAX_GAME_REPLAY_BYTES = 4 * 1024 * 1024;
const MAX_PERSISTENCE_GENERATION_BYTES =
  MAX_GAME_REPLAY_BYTES + 64 * 1024;

function deepFreezeStrictJson(
  root: Record<string, unknown>,
): Readonly<Record<string, unknown>> {
  const pending: object[] = [root];
  const visited = new Set<object>();
  while (pending.length > 0) {
    const current = pending.pop();
    if (current === undefined || visited.has(current)) {
      continue;
    }
    visited.add(current);
    for (const value of Object.values(
      current as Record<string, unknown>,
    )) {
      if (value !== null && typeof value === "object") {
        pending.push(value);
      }
    }
    Object.freeze(current);
  }
  return root;
}

export function validateGenericGamePersistence(
  value: unknown,
): ValidatedGenericGamePersistence | null {
  let owned: Record<string, unknown>;
  try {
    owned = snapshotStrictJsonObject(value, {
      context: "generic game persistence",
      maxDepth: 64,
      maxNodes: 100_000,
    });
  } catch {
    return null;
  }
  const format = Reflect.get(owned, "format");
  const maximumBytes =
    format === "world-forge.game_save"
      ? MAX_GAME_SAVE_BYTES
      : format === "world-forge.game_replay"
        ? MAX_GAME_REPLAY_BYTES
        : format === "world-forge.persistence_generation"
          ? MAX_PERSISTENCE_GENERATION_BYTES
          : null;
  const canonicalBytes = canonicalGamePersistenceByteLength(owned);
  if (
    maximumBytes === null ||
    canonicalBytes === null ||
    canonicalBytes > maximumBytes
  ) {
    return null;
  }
  const valid =
    format === "world-forge.game_save"
      ? validateSave(owned)
      : format === "world-forge.game_replay"
        ? validateReplay(owned)
        : format === "world-forge.persistence_generation"
          ? validateGeneration(owned)
        : false;
  return valid
    ? (deepFreezeStrictJson(owned) as ValidatedGenericGamePersistence)
    : null;
}

export function inspectGenericGamePersistence(
  value: ValidatedGenericGamePersistence | null,
) {
  if (value === null) {
    return null;
  }
  const format = Reflect.get(value, "format");
  const generation =
    format === "world-forge.persistence_generation";
  return Object.freeze({
    content_hash: Reflect.get(value, "content_hash"),
    format,
    id: Reflect.get(
      value,
      generation
        ? "content_hash"
        : format === "world-forge.game_save"
          ? "save_id"
          : "replay_id",
    ),
    semantic_verification: "required_python" as const,
    status: "structurally_valid" as const,
  });
}

type PythonInvocationOptions = Readonly<{
  bundleRoot: string;
  kind: "generation" | "save" | "replay";
  pythonExecutable: string;
  source: string;
}>;

export function buildGenericGamePersistencePythonInvocation(
  options: PythonInvocationOptions,
) {
  for (const [field, value] of Object.entries({
    bundleRoot: options.bundleRoot,
    pythonExecutable: options.pythonExecutable,
    source: options.source,
  })) {
    if (
      typeof value !== "string" ||
      value.length === 0 ||
      value.normalize("NFC") !== value ||
      !path.isAbsolute(value)
    ) {
      throw new Error(`game_persistence_python_invocation:${field}`);
    }
  }
  if (
    options.kind !== "generation" &&
    options.kind !== "save" &&
    options.kind !== "replay"
  ) {
    throw new Error("game_persistence_python_invocation:kind");
  }
  return Object.freeze({
    args: Object.freeze([
      "-I",
      "-B",
      "-m",
      "worldforge",
      options.kind === "save"
        ? "verify-game-save"
        : options.kind === "replay"
          ? "verify-game-replay"
          : "verify-persistence-generation",
      options.source,
      "--bundle",
      options.bundleRoot,
    ]),
    executable: options.pythonExecutable,
  });
}

export async function verifyGenericGamePersistenceWithPython(
  options: PythonInvocationOptions,
): Promise<Readonly<Record<string, unknown>>> {
  const invocation =
    buildGenericGamePersistencePythonInvocation(options);
  return new Promise((resolve, reject) => {
    const child = spawn(invocation.executable, invocation.args, {
      env: {
        PYTHONDONTWRITEBYTECODE: "1",
        PYTHONIOENCODING: "utf-8",
        PYTHONNOUSERSITE: "1",
        PYTHONUTF8: "1",
      },
      stdio: ["ignore", "pipe", "pipe"],
      windowsHide: true,
    });
    const stdout: Buffer[] = [];
    const stderr: Buffer[] = [];
    let bytes = 0;
    const limit = 1024 * 1024;
    const collect = (target: Buffer[]) => (chunk: Buffer) => {
      bytes += chunk.length;
      if (bytes > limit) {
        child.kill();
        reject(new Error("game_persistence_python_output_limit"));
        return;
      }
      target.push(Buffer.from(chunk));
    };
    child.stdout.on("data", collect(stdout));
    child.stderr.on("data", collect(stderr));
    child.once("error", reject);
    child.once("close", (code) => {
      if (code !== 0) {
        reject(
          new Error(
            `game_persistence_python_failed:${code}:` +
              Buffer.concat(stderr).toString("utf8").slice(0, 4096),
          ),
        );
        return;
      }
      const document = decodeStrictJsonObject(Buffer.concat(stdout), {
        context: "packaged Python persistence verification",
        maxBytes: limit,
        maxDepth: 16,
      });
      if (
        document === null ||
        Reflect.get(document, "status") !== "verified"
      ) {
        reject(new Error("game_persistence_python_result_invalid"));
        return;
      }
      resolve(Object.freeze(document));
    });
  });
}

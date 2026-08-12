import { spawn } from "node:child_process";
import path from "node:path";

import Ajv2020 from "ajv/dist/2020.js";

import gameExecutionScriptSchema from "../../../../schemas/game-execution-script.schema.json";
import headlessEvidenceSetSchema from "../../../../schemas/headless-evidence-set.schema.json";
import headlessExecutionReceiptSchema from "../../../../schemas/headless-execution-receipt.schema.json";
import {
  canonicalGenericHeadlessContentHash,
  hasCoherentGenericHeadlessContract,
} from "../../scripts/generic-headless-validation.mjs";
import { GENERIC_HEADLESS_AUTHORITY_RESULT_POLICY } from "../../scripts/generic-headless-authority-result.mjs";
import {
  decodeStrictJsonObject,
  snapshotStrictJsonObject,
} from "../../scripts/strict-json.mjs";

declare const validatedGenericHeadlessContractBrand: unique symbol;
export type ValidatedGenericHeadlessContract = Readonly<
  Record<string, unknown>
> & {
  readonly [validatedGenericHeadlessContractBrand]: true;
};

export const GENERIC_HEADLESS_INSPECTOR_RUNTIME = Object.freeze({
  contract_formats: Object.freeze([
    "world-forge.game_execution_script",
    "world-forge.headless_evidence_set",
    "world-forge.headless_execution_receipt",
  ]),
  format: "world-forge.studio_internal_headless_inspector",
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
    canonicalGenericHeadlessContentHash(value) ===
      Reflect.get(value as object, "content_hash"),
});
ajv.addKeyword({
  keyword: "x-world-forge-generic-headless-coherent",
  schemaType: "string",
  type: "object",
  validate: (kind: string, value: unknown) =>
    hasCoherentGenericHeadlessContract(value, kind),
});
const validateScript = ajv.compile(gameExecutionScriptSchema);
const validateReceipt = ajv.compile(headlessExecutionReceiptSchema);
const validateEvidenceSet = ajv.compile(headlessEvidenceSetSchema);
const MAX_HEADLESS_CONTRACT_BYTES = 4 * 1024 * 1024;

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

export function validateGenericHeadlessContract(
  value: unknown,
): ValidatedGenericHeadlessContract | null {
  let owned: Record<string, unknown>;
  try {
    owned = snapshotStrictJsonObject(value, {
      context: "generic headless contract",
      maxDepth: 64,
      maxNodes: 100_000,
    });
  } catch {
    return null;
  }
  if (
    Buffer.byteLength(JSON.stringify(owned), "utf8") >
    MAX_HEADLESS_CONTRACT_BYTES
  ) {
    return null;
  }
  const format = Reflect.get(owned, "format");
  const valid =
    format === "world-forge.game_execution_script"
      ? validateScript(owned)
      : format === "world-forge.headless_execution_receipt"
        ? validateReceipt(owned)
        : format === "world-forge.headless_evidence_set"
          ? validateEvidenceSet(owned)
          : false;
  return valid
    ? (deepFreezeStrictJson(owned) as ValidatedGenericHeadlessContract)
    : null;
}

export function inspectGenericHeadlessContract(
  value: ValidatedGenericHeadlessContract | null,
) {
  if (value === null) {
    return null;
  }
  const format = Reflect.get(value, "format");
  const idField =
    format === "world-forge.game_execution_script"
      ? "script_id"
      : format === "world-forge.headless_execution_receipt"
        ? "receipt_id"
        : "evidence_set_id";
  return Object.freeze({
    content_hash: Reflect.get(value, "content_hash"),
    format,
    id: Reflect.get(value, idField),
    semantic_verification: "required_python" as const,
    status: "structurally_valid" as const,
  });
}

type PythonInvocationOptions = Readonly<{
  bundleRoot: string;
  mode: "execute" | "evidence";
  outputRoot?: string;
  pythonExecutable: string;
  source: string;
}>;

function requireAbsolute(field: string, value: unknown): string {
  if (
    typeof value !== "string" ||
    value.length === 0 ||
    value.normalize("NFC") !== value ||
    !path.isAbsolute(value)
  ) {
    throw new Error(`generic_headless_python_invocation:${field}`);
  }
  return value;
}

export function buildGenericHeadlessPythonInvocation(
  options: PythonInvocationOptions,
) {
  const bundleRoot = requireAbsolute(
    "bundleRoot",
    options.bundleRoot,
  );
  const pythonExecutable = requireAbsolute(
    "pythonExecutable",
    options.pythonExecutable,
  );
  const source = requireAbsolute("source", options.source);
  if (options.mode === "execute") {
    const outputRoot = requireAbsolute(
      "outputRoot",
      options.outputRoot,
    );
    return Object.freeze({
      args: Object.freeze([
        "-I",
        "-B",
        "-m",
        "worldforge",
        "verify-game-headless",
        bundleRoot,
        source,
        "--output",
        outputRoot,
      ]),
      executable: pythonExecutable,
    });
  }
  if (options.mode !== "evidence" || options.outputRoot !== undefined) {
    throw new Error("generic_headless_python_invocation:mode");
  }
  return Object.freeze({
    args: Object.freeze([
      "-I",
      "-B",
      "-m",
      "worldforge",
      "verify-game-headless-evidence",
      source,
      "--bundle",
      bundleRoot,
    ]),
    executable: pythonExecutable,
  });
}

export function hasVerifiedGenericHeadlessPythonResult(
  value: unknown,
): boolean {
  try {
    const document = snapshotStrictJsonObject(value, {
      context: "packaged Python headless result",
      maxDepth: 4,
      maxNodes: 16,
    });
    const expectedKeys =
      GENERIC_HEADLESS_AUTHORITY_RESULT_POLICY.fields;
    const keys = Object.keys(document).sort();
    const contentHash = Reflect.get(document, "content_hash");
    const evidenceSetId = Reflect.get(document, "evidence_set_id");
    const resultPath = Reflect.get(document, "path");
    return (
      keys.length === expectedKeys.length &&
      keys.every((key, index) => key === expectedKeys[index]) &&
      typeof contentHash === "string" &&
      /^[0-9a-f]{64}$/.test(contentHash) &&
      typeof evidenceSetId === "string" &&
      /^headless_evidence_set_[0-9a-f]{40}$/.test(evidenceSetId) &&
      typeof resultPath === "string" &&
      path.isAbsolute(resultPath) &&
      resultPath.normalize("NFC") === resultPath &&
      Reflect.get(document, "integrity") ===
        GENERIC_HEADLESS_AUTHORITY_RESULT_POLICY.integrity &&
      Reflect.get(document, "execution_status") ===
        GENERIC_HEADLESS_AUTHORITY_RESULT_POLICY.execution_status &&
      Reflect.get(document, "release") ===
        GENERIC_HEADLESS_AUTHORITY_RESULT_POLICY.release &&
      Reflect.get(document, "supported") ===
        GENERIC_HEADLESS_AUTHORITY_RESULT_POLICY.supported
    );
  } catch {
    return false;
  }
}

export async function verifyGenericHeadlessWithPython(
  options: PythonInvocationOptions,
): Promise<Readonly<Record<string, unknown>>> {
  const invocation = buildGenericHeadlessPythonInvocation(options);
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
        reject(new Error("generic_headless_python_output_limit"));
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
            `generic_headless_python_failed:${code}:` +
              Buffer.concat(stderr).toString("utf8").slice(0, 4096),
          ),
        );
        return;
      }
      let document: Record<string, unknown>;
      try {
        document = decodeStrictJsonObject(Buffer.concat(stdout), {
          context: "packaged Python headless verification",
          maxBytes: limit,
          maxDepth: 32,
        });
      } catch (error) {
        reject(
          error instanceof Error
            ? error
            : new Error("generic_headless_python_result_invalid"),
        );
        return;
      }
      if (!hasVerifiedGenericHeadlessPythonResult(document)) {
        reject(new Error("generic_headless_python_result_invalid"));
        return;
      }
      resolve(Object.freeze(document));
    });
  });
}

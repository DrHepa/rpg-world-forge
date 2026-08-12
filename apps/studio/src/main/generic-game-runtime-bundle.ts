import { createHash } from "node:crypto";
import {
  constants,
  lstat,
  open,
  readdir,
  realpath,
} from "node:fs/promises";
import path from "node:path";

import Ajv2020 from "ajv/dist/2020.js";

import creationProfileSchema from "../../../../schemas/creation-profile.schema.json";
import gameRuntimeBundleSchema from "../../../../schemas/game-runtime-bundle.schema.json";
import gamepackSchema from "../../../../schemas/gamepack.schema.json";
import logicModuleSchema from "../../../../schemas/logic-module.schema.json";
import {
  canonicalGenericAssetContentHash,
  hasCanonicalGenericAssetContentHash,
  isCanonicalGenericAssetObjectArray,
} from "../../scripts/generic-asset-validation.mjs";
import {
  hasCoherentGameRuntimeBundle,
} from "../../scripts/game-runtime-bundle-validation.mjs";
import {
  GENERIC_RUNTIME_TRUSTED_SNAPSHOT,
} from "../../scripts/generic-runtime-trusted-files.mjs";
import {
  decodeStrictJsonObject,
  snapshotStrictJsonObject,
} from "../../scripts/strict-json.mjs";
import {
  validateGenericAssetpack,
  verifyGenericAssetpackDirectory,
} from "./generic-assetpack";
import {
  inspectGenericRuntimeSupport,
  validateGenericRuntimeContract,
} from "./generic-runtime-contracts";

declare const validatedGenericGameRuntimeBundleBrand: unique symbol;
export type ValidatedGenericGameRuntimeBundle = Readonly<
  Record<string, unknown>
> & {
  readonly [validatedGenericGameRuntimeBundleBrand]: true;
};

export type VerifiedGenericGameRuntimeBundleEvidence = Readonly<{
  bundle_id: string;
  content_hash: string;
  integrity: "valid";
  release: "blocked";
  state: "pre_execution";
  supported: false;
}>;

export const GENERIC_GAME_RUNTIME_BUNDLE_VALIDATOR_RUNTIME = Object.freeze({
  contract_formats: Object.freeze([
    "world-forge.game_runtime_bundle",
  ]),
  format: "world-forge.studio_internal_game_runtime_bundle_validator",
  format_version: 1,
  verification_scope: "transfer_integrity_only",
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
    required !== true || hasCanonicalGenericAssetContentHash(value),
});
ajv.addKeyword({
  keyword: "x-world-forge-canonical-object-array",
  schemaType: "object",
  type: "array",
  validate: (policy: unknown, value: unknown) =>
    isCanonicalGenericAssetObjectArray(value, policy),
});
ajv.addKeyword({
  keyword: "x-world-forge-game-runtime-bundle-coherent",
  schemaType: "boolean",
  type: "object",
  validate: (required: boolean, value: unknown) =>
    required !== true || hasCoherentGameRuntimeBundle(value),
});
const validateSchema = ajv.compile(gameRuntimeBundleSchema);
const gamepackAjv = new Ajv2020({
  allErrors: true,
  ownProperties: true,
  strict: true,
});
gamepackAjv.addKeyword({
  keyword: "x-world-forge-final-compiler-owned",
  schemaType: "boolean",
  type: "array",
  validate: (required: boolean, value: unknown) => {
    if (!required || !Array.isArray(value) || value.length === 0) {
      return !required;
    }
    const final: unknown = value[value.length - 1];
    return (
      final !== null &&
      typeof final === "object" &&
      !Array.isArray(final) &&
      Reflect.get(final, "compiler_owned") === true
    );
  },
});
gamepackAjv.addSchema([creationProfileSchema, logicModuleSchema]);
const validateGamepackSchema = gamepackAjv.compile(gamepackSchema);

const BUNDLE_MANIFEST = "game-runtime-bundle.json";
const MAX_BUNDLE_FILES = 257;
const MAX_BUNDLE_DIRECTORIES = 256;
const MAX_BUNDLE_DEPTH = 32;
const MAX_BUNDLE_FILE_BYTES = 4 * 1024 * 1024;
const MAX_BUNDLE_BYTES = 32 * 1024 * 1024;
const LICENSE_SHA256 =
  "2e55c53ff294650e049d844f2544fec947c3516440aeffca4b2334cf94b13eeb";

type FileRecord = Readonly<{
  path: string;
  sha256: string;
  size_bytes: number;
}>;

type FileState = Readonly<{
  ctimeNs: bigint;
  dev: bigint;
  ino: bigint;
  mode: bigint;
  mtimeNs: bigint;
  nlink: bigint;
  size: bigint;
}>;

type TreeSnapshot = Readonly<{
  directories: ReadonlyMap<string, FileState>;
  files: ReadonlyMap<string, FileState>;
}>;

type BundleDocument = Readonly<{
  assetpack: Readonly<{
    inventory_hash: string;
    manifest: Readonly<Record<string, unknown>>;
    root_hash: string;
  }>;
  bindings: readonly Readonly<Record<string, unknown>>[];
  bundle_id: string;
  content_hash: string;
  contracts: Readonly<Record<string, Readonly<Record<string, unknown>>>>;
  files: readonly FileRecord[];
  legal: Readonly<{
    asset_notices: readonly FileRecord[];
  }>;
  runtime_snapshot_tree: Readonly<{
    file_count: number;
    total_bytes: number;
    tree_hash: string;
  }>;
}>;

const TRUSTED_RUNTIME_FILES = (() => {
  const files = new Map<string, Buffer>();
  const records: FileRecord[] = [];
  for (const entry of GENERIC_RUNTIME_TRUSTED_SNAPSHOT.files) {
    if (
      typeof entry.path !== "string" ||
      typeof entry.base64 !== "string" ||
      typeof entry.sha256 !== "string" ||
      !Number.isSafeInteger(entry.size_bytes) ||
      files.has(entry.path)
    ) {
      throw new Error("generated trusted runtime map is invalid");
    }
    const payload = Buffer.from(entry.base64, "base64");
    if (
      payload.toString("base64") !== entry.base64 ||
      payload.length !== entry.size_bytes ||
      createHash("sha256").update(payload).digest("hex") !== entry.sha256
    ) {
      throw new Error("generated trusted runtime bytes are invalid");
    }
    files.set(entry.path, payload);
    records.push({
      path: entry.path,
      sha256: entry.sha256,
      size_bytes: entry.size_bytes,
    });
  }
  records.sort((left, right) =>
    Buffer.compare(
      Buffer.from(left.path, "utf8"),
      Buffer.from(right.path, "utf8"),
    ),
  );
  if (
    canonicalGenericAssetContentHash({ files: records }) !==
    GENERIC_RUNTIME_TRUSTED_SNAPSHOT.tree_hash
  ) {
    throw new Error("generated trusted runtime tree hash is invalid");
  }
  return Object.freeze({
    files,
    records: Object.freeze(records),
    treeHash: GENERIC_RUNTIME_TRUSTED_SNAPSHOT.tree_hash,
  });
})();

function stateOf(stat: {
  ctimeNs: bigint;
  dev: bigint;
  ino: bigint;
  mode: bigint;
  mtimeNs: bigint;
  nlink: bigint;
  size: bigint;
}): FileState {
  return {
    ctimeNs: stat.ctimeNs,
    dev: stat.dev,
    ino: stat.ino,
    mode: stat.mode,
    mtimeNs: stat.mtimeNs,
    nlink: stat.nlink,
    size: stat.size,
  };
}

function sameState(left: FileState, right: FileState): boolean {
  return (
    left.ctimeNs === right.ctimeNs &&
    left.dev === right.dev &&
    left.ino === right.ino &&
    left.mode === right.mode &&
    left.mtimeNs === right.mtimeNs &&
    left.nlink === right.nlink &&
    left.size === right.size
  );
}

function isWithin(root: string, candidate: string): boolean {
  const relative = path.relative(root, candidate);
  return (
    candidate === root ||
    (relative !== ".." &&
      !relative.startsWith(`..${path.sep}`) &&
      !path.isAbsolute(relative))
  );
}

async function snapshotTree(root: string): Promise<TreeSnapshot | null> {
  const directories = new Map<string, FileState>();
  const files = new Map<string, FileState>();
  const pending = [""];
  while (pending.length > 0) {
    const relativeDirectory = pending.pop();
    if (relativeDirectory === undefined) {
      return null;
    }
    const absoluteDirectory =
      relativeDirectory === ""
        ? root
        : path.join(root, ...relativeDirectory.split("/"));
    const directory = await lstat(absoluteDirectory, { bigint: true });
    if (
      !directory.isDirectory() ||
      directory.isSymbolicLink() ||
      (await realpath(absoluteDirectory)) !== absoluteDirectory
    ) {
      return null;
    }
    directories.set(relativeDirectory, stateOf(directory));
    const entries = await readdir(absoluteDirectory, {
      encoding: "utf8",
      withFileTypes: true,
    });
    entries.sort((left, right) =>
      Buffer.compare(
        Buffer.from(left.name, "utf8"),
        Buffer.from(right.name, "utf8"),
      ),
    );
    const folded = new Set<string>();
    for (const entry of entries) {
      const foldedName = entry.name.toLowerCase();
      if (
        entry.name !== entry.name.normalize("NFC") ||
        entry.name === "." ||
        entry.name === ".." ||
        entry.name === "__pycache__" ||
        entry.name.endsWith(".pyc") ||
        entry.name.endsWith(".pyo") ||
        folded.has(foldedName)
      ) {
        return null;
      }
      folded.add(foldedName);
      const relative =
        relativeDirectory === ""
          ? entry.name
          : `${relativeDirectory}/${entry.name}`;
      if (relative.split("/").length > MAX_BUNDLE_DEPTH) {
        return null;
      }
      const absolute = path.join(absoluteDirectory, entry.name);
      const info = await lstat(absolute, { bigint: true });
      if (info.isSymbolicLink() || (await realpath(absolute)) !== absolute) {
        return null;
      }
      if (info.isDirectory()) {
        directories.set(relative, stateOf(info));
        pending.push(relative);
      } else if (
        info.isFile() &&
        info.nlink === 1n &&
        info.size <= BigInt(MAX_BUNDLE_FILE_BYTES)
      ) {
        files.set(relative, stateOf(info));
      } else {
        return null;
      }
      if (
        files.size > MAX_BUNDLE_FILES ||
        directories.size - 1 > MAX_BUNDLE_DIRECTORIES
      ) {
        return null;
      }
    }
  }
  return { directories, files };
}

async function treeUnchanged(
  root: string,
  expected: TreeSnapshot,
): Promise<boolean> {
  const current = await snapshotTree(root);
  if (
    current === null ||
    current.directories.size !== expected.directories.size ||
    current.files.size !== expected.files.size
  ) {
    return false;
  }
  for (const [relative, state] of expected.directories) {
    const actual = current.directories.get(relative);
    if (actual === undefined || !sameState(state, actual)) {
      return false;
    }
  }
  for (const [relative, state] of expected.files) {
    const actual = current.files.get(relative);
    if (actual === undefined || !sameState(state, actual)) {
      return false;
    }
  }
  return true;
}

async function pinnedFile(
  filename: string,
  root: string,
  expected: FileState,
): Promise<Buffer | null> {
  if (!isWithin(root, filename)) {
    return null;
  }
  const before = await lstat(filename, { bigint: true });
  if (
    !before.isFile() ||
    before.isSymbolicLink() ||
    before.nlink !== 1n ||
    !sameState(expected, stateOf(before)) ||
    (await realpath(filename)) !== filename
  ) {
    return null;
  }
  let handle;
  try {
    handle = await open(
      filename,
      constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0),
    );
  } catch {
    return null;
  }
  try {
    const opened = await handle.stat({ bigint: true });
    if (
      !opened.isFile() ||
      opened.nlink !== 1n ||
      !sameState(expected, stateOf(opened))
    ) {
      return null;
    }
    const bytes = await handle.readFile();
    const after = await handle.stat({ bigint: true });
    const named = await lstat(filename, { bigint: true });
    if (
      bytes.length !== Number(expected.size) ||
      !sameState(expected, stateOf(after)) ||
      !sameState(expected, stateOf(named)) ||
      (await realpath(filename)) !== filename
    ) {
      return null;
    }
    return bytes;
  } finally {
    await handle.close();
  }
}

function canonicalPrettyJson(value: unknown): Buffer {
  const ordered = (candidate: unknown): unknown => {
    if (Array.isArray(candidate)) {
      const result = new Array<unknown>(candidate.length);
      for (let index = 0; index < candidate.length; index += 1) {
        result[index] = ordered(candidate[index]);
      }
      return result;
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
  };
  return Buffer.from(`${JSON.stringify(ordered(value), null, 2)}\n`, "utf8");
}

function expectedDirectories(paths: ReadonlySet<string>): Set<string> {
  const result = new Set([""]);
  for (const relative of paths) {
    const parts = relative.split("/");
    parts.pop();
    let current = "";
    for (const part of parts) {
      current = current === "" ? part : `${current}/${part}`;
      result.add(current);
    }
  }
  return result;
}

function setEquals(
  left: ReadonlySet<string>,
  right: ReadonlySet<string>,
): boolean {
  return (
    left.size === right.size &&
    [...left].every((candidate) => right.has(candidate))
  );
}

function sameJson(left: unknown, right: unknown): boolean {
  return canonicalPrettyJson(left).equals(canonicalPrettyJson(right));
}

function recordValue(
  value: unknown,
): Readonly<Record<string, unknown>> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Readonly<Record<string, unknown>>)
    : null;
}

function recordArray(
  value: unknown,
): readonly Readonly<Record<string, unknown>>[] | null {
  if (!Array.isArray(value)) {
    return null;
  }
  const records: Readonly<Record<string, unknown>>[] = [];
  for (let index = 0; index < value.length; index += 1) {
    const record = recordValue(value[index] as unknown);
    if (record === null) {
      return null;
    }
    records.push(record);
  }
  return records;
}

function identityMatches(
  identity: Readonly<Record<string, unknown>>,
  document: Readonly<Record<string, unknown>>,
  id: unknown,
): boolean {
  return (
    identity.format === document.format &&
    identity.format_version === document.format_version &&
    identity.id === id &&
    identity.content_hash === document.content_hash
  );
}

function lineageIdentity(
  document: Readonly<Record<string, unknown>>,
  id: unknown,
): Readonly<Record<string, unknown>> {
  return {
    content_hash: document.content_hash,
    format: document.format,
    format_version: document.format_version,
    id,
  };
}

function deriveBindings(
  gamepack: Readonly<Record<string, unknown>>,
  adapter: Readonly<Record<string, unknown>>,
  assetpack: Readonly<Record<string, unknown>>,
): readonly Readonly<Record<string, unknown>>[] | null {
  const requirements = recordArray(gamepack.asset_requirements);
  const rules = recordArray(adapter.asset_bindings);
  const assets = recordArray(assetpack.assets);
  if (
    requirements === null ||
    rules === null ||
    assets === null
  ) {
    return null;
  }
  const required = new Set(
    requirements
      .filter((item) => item.required === true)
      .map((item) => item.binding_id),
  );
  if (
    rules.length !== required.size ||
    !rules.every((rule) => required.has(rule.binding_id))
  ) {
    return null;
  }
  const result: Readonly<Record<string, unknown>>[] = [];
  const consumed = new Set<string>();
  for (
    const rule of [...rules].sort((left, right) =>
        Buffer.compare(
        Buffer.from(String(left.binding_id), "utf8"),
        Buffer.from(String(right.binding_id), "utf8"),
      ),
    )
  ) {
    const assetId = rule.asset_id;
    const asset = assets.find(
      (candidate) =>
        recordValue(candidate.asset)?.asset_id === assetId,
    );
    const outputs = recordArray(asset?.outputs);
    if (outputs === null) {
      return null;
    }
    const matches = outputs.filter(
      (output) =>
        output.role === rule.role &&
        output.media_type === rule.media_type &&
        output.runtime_path === rule.runtime_path,
    );
    if (matches.length !== 1) {
      return null;
    }
    const output = matches[0];
    if (output === undefined) {
      return null;
    }
    const key = [
      String(assetId),
      String(output.role),
      String(output.media_type),
      String(output.runtime_path),
    ].join("\u0000");
    consumed.add(key);
    result.push({
      binding_id: rule.binding_id,
      asset_id: assetId,
      role: output.role,
      media_type: output.media_type,
      runtime_path: output.runtime_path,
      bundle_path: `assetpack/${String(output.runtime_path)}`,
      sha256: output.sha256,
      size_bytes: output.size_bytes,
    });
  }
  let outputCount = 0;
  for (const asset of assets) {
    const outputs = recordArray(asset.outputs);
    const assetIdentity = recordValue(asset.asset);
    if (outputs === null || assetIdentity === null) {
      return null;
    }
    for (const output of outputs) {
      outputCount += 1;
      const key = [
        String(assetIdentity.asset_id),
        String(output.role),
        String(output.media_type),
        String(output.runtime_path),
      ].join("\u0000");
      if (!consumed.has(key)) {
        return null;
      }
    }
  }
  return outputCount === consumed.size ? result : null;
}

function deepFreeze<T extends object>(root: T): T {
  const pending: object[] = [root];
  while (pending.length > 0) {
    const current = pending.pop();
    if (current === undefined) {
      continue;
    }
    for (const key of Reflect.ownKeys(current)) {
      const descriptor = Object.getOwnPropertyDescriptor(current, key);
      const child: unknown =
        descriptor !== undefined && "value" in descriptor
          ? descriptor.value
          : undefined;
      if (child !== null && typeof child === "object") {
        pending.push(child);
      }
    }
    Object.freeze(current);
  }
  return root;
}

export function validateGenericGameRuntimeBundle(
  value: unknown,
): ValidatedGenericGameRuntimeBundle | null {
  let candidate: Record<string, unknown>;
  try {
    candidate = snapshotStrictJsonObject(value, {
      context: "generic game runtime bundle",
      maxKeys: 200_000,
      maxNodes: 200_000,
      maxStringCodeUnits: 32 * 1024 * 1024,
    });
  } catch {
    return null;
  }
  let isolated: Record<string, unknown>;
  try {
    isolated = JSON.parse(
      JSON.stringify(candidate),
    ) as Record<string, unknown>;
  } catch {
    return null;
  }
  try {
    if (validateSchema(isolated) !== true) {
      return null;
    }
  } catch {
    return null;
  }
  return deepFreeze(candidate) as ValidatedGenericGameRuntimeBundle;
}

export async function verifyGenericGameRuntimeBundleDirectory(
  root: string,
  options: Readonly<{
    verificationHook?: (
      event:
        | "after_assetpack_verification"
        | "after_contract_verification"
        | "after_file_read"
        | "after_manifest_read"
        | "after_manifest_verification"
        | "after_tree_snapshot",
      relative?: string,
    ) => Promise<void> | void;
  }> = {},
): Promise<VerifiedGenericGameRuntimeBundleEvidence | null> {
  try {
    if (
      typeof root !== "string" ||
      !path.isAbsolute(root) ||
      path.normalize(root) !== root ||
      path.resolve(root) !== root ||
      (await realpath(root)) !== root
    ) {
      return null;
    }
    const tree = await snapshotTree(root);
    if (
      tree === null ||
      !tree.files.has(BUNDLE_MANIFEST) ||
      [...tree.files.values()].reduce(
        (total, state) => total + Number(state.size),
        0,
      ) >
        MAX_BUNDLE_BYTES + MAX_BUNDLE_FILE_BYTES
    ) {
      return null;
    }
    await options.verificationHook?.("after_tree_snapshot");
    if (!(await treeUnchanged(root, tree))) {
      return null;
    }
    const retained = new Map<string, Buffer>();
    for (
      const relative of [...tree.files.keys()].sort((left, right) =>
        Buffer.compare(
          Buffer.from(left, "utf8"),
          Buffer.from(right, "utf8"),
        ),
      )
    ) {
      const state = tree.files.get(relative);
      if (state === undefined) {
        return null;
      }
      const payload = await pinnedFile(
        path.join(root, ...relative.split("/")),
        root,
        state,
      );
      if (payload === null) {
        return null;
      }
      retained.set(relative, payload);
      await options.verificationHook?.(
        relative === BUNDLE_MANIFEST
          ? "after_manifest_read"
          : "after_file_read",
        relative,
      );
      if (!(await treeUnchanged(root, tree))) {
        return null;
      }
    }
    const manifestBytes = retained.get(BUNDLE_MANIFEST);
    if (manifestBytes === undefined) {
      return null;
    }
    const decodedManifest = decodeStrictJsonObject(manifestBytes, {
      context: "game runtime bundle manifest",
      maxBytes: MAX_BUNDLE_FILE_BYTES,
      maxDepth: 128,
    });
    const validatedManifest =
      validateGenericGameRuntimeBundle(decodedManifest);
    if (
      validatedManifest === null ||
      !manifestBytes.equals(canonicalPrettyJson(validatedManifest))
    ) {
      return null;
    }
    const manifest = JSON.parse(
      JSON.stringify(validatedManifest),
    ) as BundleDocument;
    await options.verificationHook?.("after_manifest_verification");
    const expectedFiles = new Set([
      BUNDLE_MANIFEST,
      ...manifest.files.map((entry) => entry.path),
    ]);
    if (
      !setEquals(expectedFiles, new Set(tree.files.keys())) ||
      !setEquals(
        expectedDirectories(expectedFiles),
        new Set(tree.directories.keys()),
      )
    ) {
      return null;
    }
    const inventory = new Map(
      manifest.files.map((entry) => [entry.path, entry]),
    );
    for (const [relative, payload] of retained) {
      if (relative === BUNDLE_MANIFEST) {
        continue;
      }
      const record = inventory.get(relative);
      if (
        record === undefined ||
        payload.length !== record.size_bytes ||
        createHash("sha256").update(payload).digest("hex") !== record.sha256
      ) {
        return null;
      }
    }

    const decodeDocument = (
      relative: string,
    ): Readonly<Record<string, unknown>> | null => {
      const payload = retained.get(relative);
      if (payload === undefined) {
        return null;
      }
      const document = decodeStrictJsonObject(payload, {
        context: `game runtime bundle ${relative}`,
        maxBytes: MAX_BUNDLE_FILE_BYTES,
        maxDepth: 128,
      });
      return payload.equals(canonicalPrettyJson(document))
        ? (JSON.parse(
            JSON.stringify(document),
          ) as Readonly<Record<string, unknown>>)
        : null;
    };
    const gamepack = decodeDocument("contracts/gamepack.json");
    if (
      gamepack === null ||
      validateGamepackSchema(structuredClone(gamepack)) !== true ||
      canonicalGenericAssetContentHash(gamepack) !== gamepack.content_hash
    ) {
      return null;
    }
    const runtimeDocument = (
      relative: string,
    ): Readonly<Record<string, unknown>> | null => {
      const document = decodeDocument(relative);
      if (document === null) {
        return null;
      }
      try {
        return JSON.parse(
          JSON.stringify(validateGenericRuntimeContract(document)),
        ) as Readonly<Record<string, unknown>>;
      } catch {
        return null;
      }
    };
    const snapshot = runtimeDocument("contracts/runtime-snapshot.json");
    const registry = runtimeDocument(
      "contracts/runtime-adapter-registry.json",
    );
    const composition = runtimeDocument(
      "contracts/runtime-composition.json",
    );
    const support = runtimeDocument("status/runtime-support-report.json");
    const adapterPath = manifest.contracts.runtime_adapter.path;
    if (
      snapshot === null ||
      registry === null ||
      composition === null ||
      support === null ||
      typeof adapterPath !== "string"
    ) {
      return null;
    }
    const descriptor = runtimeDocument(adapterPath);
    if (descriptor === null) {
      return null;
    }
    await options.verificationHook?.("after_contract_verification");

    const assetpackPath = path.join(root, "assetpack");
    const assetpackEvidence =
      await verifyGenericAssetpackDirectory(assetpackPath);
    const assetpackBytes = retained.get("assetpack/assetpack.json");
    const assetpackDocument =
      assetpackBytes === undefined
        ? null
        : validateGenericAssetpack(
            decodeStrictJsonObject(assetpackBytes, {
              context: "nested game runtime bundle assetpack",
              maxBytes: MAX_BUNDLE_FILE_BYTES,
              maxDepth: 128,
            }),
          );
    if (
      assetpackEvidence === null ||
      assetpackDocument === null ||
      !assetpackBytes?.equals(canonicalPrettyJson(assetpackDocument)) ||
      assetpackEvidence.assetpack_id !== assetpackDocument.assetpack_id ||
      assetpackEvidence.content_hash !== assetpackDocument.content_hash
    ) {
      return null;
    }
    const assetpack = JSON.parse(
      JSON.stringify(assetpackDocument),
    ) as Readonly<Record<string, unknown>>;
    await options.verificationHook?.("after_assetpack_verification");
    const game = gamepack.game;
    if (
      game === null ||
      typeof game !== "object" ||
      Array.isArray(game) ||
      !identityMatches(
        manifest.contracts.gamepack,
        gamepack,
        Reflect.get(game, "id"),
      ) ||
      !identityMatches(
        manifest.contracts.runtime_snapshot,
        snapshot,
        snapshot.snapshot_id,
      ) ||
      !identityMatches(
        manifest.contracts.runtime_adapter_registry,
        registry,
        registry.registry_id,
      ) ||
      !identityMatches(
        manifest.contracts.runtime_composition,
        composition,
        composition.composition_id,
      ) ||
      !identityMatches(
        manifest.contracts.runtime_support_report,
        support,
        support.report_id,
      ) ||
      !identityMatches(
        manifest.contracts.runtime_adapter,
        descriptor,
        descriptor.adapter_id,
      ) ||
      manifest.contracts.runtime_adapter.adapter_version !==
        descriptor.adapter_version ||
      !identityMatches(
        manifest.assetpack.manifest,
        assetpack,
        assetpack.assetpack_id,
      )
    ) {
      return null;
    }

    const bundledRuntimeFiles = new Map<string, Buffer>();
    for (const [relative, payload] of retained) {
      if (relative.startsWith("runtime/snapshot-tree/")) {
        bundledRuntimeFiles.set(
          relative.slice("runtime/snapshot-tree/".length),
          payload,
        );
      }
    }
    if (
      bundledRuntimeFiles.size !== TRUSTED_RUNTIME_FILES.files.size ||
      [...TRUSTED_RUNTIME_FILES.files].some(
        ([relative, trusted]) =>
          !bundledRuntimeFiles.get(relative)?.equals(trusted),
      )
    ) {
      return null;
    }
    const runtimeRecords = TRUSTED_RUNTIME_FILES.records;
    if (
      !sameJson(snapshot.files, runtimeRecords) ||
      snapshot.tree_hash !== TRUSTED_RUNTIME_FILES.treeHash ||
      manifest.runtime_snapshot_tree.tree_hash !== snapshot.tree_hash ||
      manifest.runtime_snapshot_tree.file_count !== runtimeRecords.length ||
      manifest.runtime_snapshot_tree.total_bytes !==
        runtimeRecords.reduce(
          (total, entry) => total + entry.size_bytes,
          0,
        )
    ) {
      return null;
    }
    const adapters = registry.adapters;
    if (!Array.isArray(adapters)) {
      return null;
    }
    const selected = adapters.filter(
      (candidate) =>
        Reflect.get(candidate, "adapter_id") === descriptor.adapter_id &&
        Reflect.get(candidate, "adapter_version") ===
          descriptor.adapter_version &&
        Reflect.get(candidate, "content_hash") === descriptor.content_hash,
    );
    if (selected.length !== 1 || !sameJson(selected[0], descriptor)) {
      return null;
    }

    const assetpackFiles = new Map<string, Buffer>();
    for (const [relative, payload] of retained) {
      if (relative.startsWith("assetpack/")) {
        assetpackFiles.set(
          relative.slice("assetpack/".length),
          payload,
        );
      }
    }
    const assetpackRecords = [...assetpackFiles.entries()]
      .map(([relative, payload]) => ({
        path: relative,
        sha256: createHash("sha256").update(payload).digest("hex"),
        size_bytes: payload.length,
      }))
      .sort((left, right) =>
        Buffer.compare(
          Buffer.from(left.path, "utf8"),
          Buffer.from(right.path, "utf8"),
        ),
      );
    const assetpackInventory = recordValue(assetpack.inventory);
    const assetpackInventoryFiles = recordArray(
      assetpackInventory?.files,
    );
    if (assetpackInventory === null || assetpackInventoryFiles === null) {
      return null;
    }
    const expectedPayloadFiles = new Set([
      "contracts/gamepack.json",
      "contracts/runtime-adapter-registry.json",
      "contracts/runtime-composition.json",
      "contracts/runtime-snapshot.json",
      "licenses/world-forge-mit.txt",
      "status/runtime-support-report.json",
      "assetpack/assetpack.json",
      ...assetpackInventoryFiles.map(
        (entry) => `assetpack/${String(entry.path)}`,
      ),
      ...TRUSTED_RUNTIME_FILES.records.map(
        (entry) => `runtime/snapshot-tree/${entry.path}`,
      ),
    ]);
    if (
      !setEquals(
        expectedPayloadFiles,
        new Set(manifest.files.map((entry) => entry.path)),
      )
    ) {
      return null;
    }
    if (
      manifest.assetpack.root_hash !==
        canonicalGenericAssetContentHash({ files: assetpackRecords }) ||
      manifest.assetpack.inventory_hash !==
        assetpackInventory.content_hash
    ) {
      return null;
    }
    const derivedBindings = deriveBindings(gamepack, descriptor, assetpack);
    const compositionBindings = derivedBindings?.map((binding) =>
      Object.fromEntries(
        Object.entries(binding).filter(([key]) => key !== "bundle_path"),
      ),
    );
    if (
      derivedBindings === null ||
      !sameJson(manifest.bindings, derivedBindings) ||
      !sameJson(composition.bindings, compositionBindings)
    ) {
      return null;
    }
    const compositionAssetpack = recordValue(composition.assetpack);
    const compositionAssetInventory = recordValue(
      composition.asset_inventory,
    );
    const compositionGamepack = recordValue(composition.gamepack);
    const compositionSnapshot = recordValue(composition.runtime_snapshot);
    const compositionAdapter = recordValue(composition.adapter);
    const compositionRegistry = recordValue(composition.registry);
    const gameIdentity = recordValue(gamepack.game);
    const assetpackAssetInventory = recordValue(
      assetpack.asset_inventory,
    );
    const assetpackGamepack = recordValue(assetpack.gamepack);
    if (
      compositionAssetpack === null ||
      compositionAssetInventory === null ||
      compositionGamepack === null ||
      compositionSnapshot === null ||
      compositionAdapter === null ||
      compositionRegistry === null ||
      gameIdentity === null ||
      assetpackAssetInventory === null ||
      assetpackGamepack === null ||
      !sameJson(
        compositionAssetpack,
        {
          ...lineageIdentity(
            assetpack,
            assetpack.assetpack_id,
          ),
          inventory_hash: manifest.assetpack.inventory_hash,
          root_hash: manifest.assetpack.root_hash,
        },
      ) ||
      !sameJson(
        assetpackGamepack,
        lineageIdentity(gamepack, gameIdentity.id),
      ) ||
      !sameJson(compositionAssetInventory, assetpackAssetInventory) ||
      !sameJson(
        compositionGamepack,
        lineageIdentity(gamepack, gameIdentity.id),
      ) ||
      !sameJson(
        compositionSnapshot,
        lineageIdentity(snapshot, snapshot.snapshot_id),
      ) ||
      !sameJson(
        compositionAdapter,
        lineageIdentity(descriptor, descriptor.adapter_id),
      ) ||
      !sameJson(
        compositionRegistry,
        lineageIdentity(registry, registry.registry_id),
      )
    ) {
      return null;
    }

    const supportInspection = inspectGenericRuntimeSupport(support);
    const supportDimensions = support.dimensions;
    const supportDimensionsRecord =
      supportDimensions !== null &&
      typeof supportDimensions === "object" &&
      !Array.isArray(supportDimensions)
        ? (supportDimensions as Readonly<Record<string, unknown>>)
        : null;
    const execution =
      supportDimensionsRecord !== null
        ? Reflect.get(supportDimensionsRecord, "execution")
        : null;
    const executionRecords = recordArray(execution);
    const supportGamepack = recordValue(support.gamepack);
    const supportComposition = recordValue(support.composition);
    const supportAdapter = recordValue(support.adapter);
    if (
      supportInspection.adapter !== "declared" ||
      supportInspection.release !== "blocked" ||
      supportInspection.supported ||
      !Array.isArray(support.evidence) ||
      support.evidence.length !== 0 ||
      executionRecords === null ||
      executionRecords.some((entry) => {
        const evidenceIds: unknown = entry.evidence_ids;
        return (
          entry.status !== "untested" ||
          !Array.isArray(evidenceIds) ||
          evidenceIds.length !== 0
        );
      }) ||
      supportDimensionsRecord === null ||
      Reflect.get(supportDimensionsRecord, "packaging") !== "unverified" ||
      supportGamepack === null ||
      supportComposition === null ||
      supportAdapter === null ||
      gameIdentity === null ||
      !sameJson(
        supportGamepack,
        lineageIdentity(gamepack, gameIdentity.id),
      ) ||
      !sameJson(
        supportComposition,
        lineageIdentity(composition, composition.composition_id),
      ) ||
      !sameJson(
        supportAdapter,
        lineageIdentity(descriptor, descriptor.adapter_id),
      )
    ) {
      return null;
    }

    const assetRecords = recordArray(assetpack.assets);
    if (assetRecords === null) {
      return null;
    }
    const assetNoticesByPath = new Map<
      string,
      Readonly<Record<string, unknown>>
    >();
    for (const asset of assetRecords) {
      const outputs = recordArray(asset.outputs);
      if (outputs === null) {
        return null;
      }
      for (const output of outputs) {
        const notice = recordValue(output.runtime_notice);
        if (notice === null) {
          return null;
        }
        const relative = `assetpack/${String(notice.path)}`;
        const record = {
          path: relative,
          sha256: notice.sha256,
          size_bytes: notice.size_bytes,
        };
        const existing = assetNoticesByPath.get(relative);
        if (existing !== undefined && !sameJson(existing, record)) {
          return null;
        }
        assetNoticesByPath.set(relative, record);
      }
    }
    const assetNotices = [...assetNoticesByPath.values()];
    assetNotices.sort((left, right) =>
        Buffer.compare(
          Buffer.from(String(left.path), "utf8"),
          Buffer.from(String(right.path), "utf8"),
        ),
      );
    const license = retained.get("licenses/world-forge-mit.txt");
    if (
      !sameJson(manifest.legal.asset_notices, assetNotices) ||
      license === undefined ||
      license.length !== 1063 ||
      createHash("sha256").update(license).digest("hex") !==
        LICENSE_SHA256 ||
      !(await treeUnchanged(root, tree))
    ) {
      return null;
    }
    return Object.freeze({
      bundle_id: manifest.bundle_id,
      content_hash: manifest.content_hash,
      integrity: "valid" as const,
      release: "blocked" as const,
      state: "pre_execution" as const,
      supported: false as const,
    });
  } catch {
    return null;
  }
}

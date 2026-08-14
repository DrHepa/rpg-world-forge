import { createHash } from "node:crypto";
import { constants } from "node:fs";
import {
  lstat,
  open,
  readdir,
  realpath,
} from "node:fs/promises";
import path from "node:path";
import { Script, createContext } from "node:vm";

import Ajv2020 from "ajv/dist/2020.js";
import {
  _,
  Name,
  strConcat,
} from "ajv/dist/compile/codegen/index.js";
import type { KeywordCxt } from "ajv/dist/compile/validate/index.js";
import standaloneCode from "ajv/dist/standalone/index.js";

import genericAssetpackSchema from "../../../../schemas/generic-assetpack.schema.json";
import {
  hasCoherentGenericAssetpack,
  inspectGenericAssetpackMedia,
} from "../../scripts/generic-assetpack-validation.mjs";
import {
  areCanonicalGenericAssetGlyphRanges,
  hasCanonicalGenericAssetContentHash,
  hasMatchingGenericAssetGlyphCount,
  hasPortableGenericAssetPathTree,
  isCanonicalGenericAssetObjectArray,
  isPortableGenericAssetRuntimePath,
  isSafeGenericAssetRuntimeText,
} from "../../scripts/generic-asset-validation.mjs";
import {
  decodeStrictJsonObject,
  snapshotStrictJsonObject,
} from "../../scripts/strict-json.mjs";
import type { WorldForgeSealedGenericAssetpackV1 } from "../generated/world-forge-contracts";
import { noFollowOpenFlagForPlatform } from "./no-follow-open-flag";
export { noFollowOpenFlagForPlatform } from "./no-follow-open-flag";

const MAX_ASSETPACK_MANIFEST_BYTES = 16 * 1024 * 1024;
const MAX_ASSETPACK_FILE_BYTES = 256 * 1024 * 1024;
const MAX_ASSETPACK_FILES = 8193;
const MAX_ASSETPACK_DIRECTORIES = 8193;
const MAX_ASSETPACK_TREE_NODES =
  MAX_ASSETPACK_FILES + MAX_ASSETPACK_DIRECTORIES;
const MAX_ASSETPACK_TREE_DEPTH = 32;
const MAX_ASSETPACK_NOTICE_BYTES = 4096;
const MAX_ASSETPACK_NOTICE_CHARACTERS = 4096;
const ASSETPACK_MANIFEST = "assetpack.json";
const utf8Decoder = new TextDecoder("utf-8", { fatal: true });

declare const validatedGenericAssetpackBrand: unique symbol;
export type ValidatedGenericAssetpack =
  Readonly<WorldForgeSealedGenericAssetpackV1> & {
    readonly [validatedGenericAssetpackBrand]: true;
  };

export type VerifiedGenericAssetpackEvidence = Readonly<{
  assetpack_id: string;
  content_hash: string;
  file_count: number;
  root: string;
  status: "sealed";
}>;

type SemanticHandler = (schema: unknown, value: unknown) => boolean;
type IsolatedSemanticHandler = (
  schema: unknown,
  privateValue: unknown,
  instancePath: unknown,
) => boolean;
type SemanticSchemaType = "boolean" | "object" | "string";
type SemanticValueType = "array" | "object" | "string";

const freezeObject = Object.freeze;
const getOwnPropertyDescriptor = Object.getOwnPropertyDescriptor;
const ownKeys = Reflect.ownKeys;
const setPrototypeOf = Reflect.setPrototypeOf;
const ajvInstancePath = new Name("instancePath");
const semanticHandlers = Object.create(null) as Record<
  string,
  SemanticHandler
>;
let semanticHandlerIndex = 0;
const ajv = new Ajv2020({
  allErrors: true,
  code: { source: true },
  ownProperties: true,
  strict: true,
});

function addSemanticKeyword(
  keyword: string,
  schemaType: SemanticSchemaType,
  type: SemanticValueType,
  handler: SemanticHandler,
): void {
  const handlerIndex = semanticHandlerIndex;
  semanticHandlerIndex += 1;
  semanticHandlers[`handler${String(handlerIndex)}`] = handler;
  ajv.addKeyword({
    code(context: KeywordCxt) {
      const scopedHandler = context.gen.scopeValue("func", {
        code: _`globalThis.__worldForgeAssetpackSemantics.handler${handlerIndex}`,
        ref: handler,
      });
      context.pass(
        _`${scopedHandler}(${context.schemaCode}, ${context.data}, ${strConcat(ajvInstancePath, context.it.errorPath)})`,
      );
    },
    keyword,
    schemaType,
    type,
  });
}

addSemanticKeyword(
  "x-world-forge-canonical-content-hash",
  "boolean",
  "object",
  (required, value) =>
    required !== true || hasCanonicalGenericAssetContentHash(value),
);
addSemanticKeyword(
  "x-world-forge-canonical-glyph-ranges",
  "boolean",
  "array",
  (required, value) =>
    required !== true || areCanonicalGenericAssetGlyphRanges(value),
);
addSemanticKeyword(
  "x-world-forge-canonical-object-array",
  "object",
  "array",
  (policy, value) => isCanonicalGenericAssetObjectArray(value, policy),
);
addSemanticKeyword(
  "x-world-forge-generic-assetpack-coherent",
  "boolean",
  "object",
  (required, value) =>
    required !== true || hasCoherentGenericAssetpack(value),
);
addSemanticKeyword(
  "x-world-forge-glyph-count-match",
  "boolean",
  "object",
  (required, value) =>
    required !== true || hasMatchingGenericAssetGlyphCount(value),
);
addSemanticKeyword(
  "x-world-forge-portable-path-tree",
  "string",
  "array",
  (field, value) => hasPortableGenericAssetPathTree(value, field),
);
addSemanticKeyword(
  "x-world-forge-portable-runtime-path",
  "boolean",
  "string",
  (required, value) =>
    required !== true || isPortableGenericAssetRuntimePath(value),
);
addSemanticKeyword(
  "x-world-forge-safe-runtime-text",
  "boolean",
  "string",
  (required, value) =>
    required !== true || isSafeGenericAssetRuntimeText(value),
);

ajv.addSchema(genericAssetpackSchema);
const validatorSource = standaloneCode(ajv, {
  validateAssetpack: genericAssetpackSchema.$id,
});

type IsolatedValidator = (value: Record<string, unknown>) => boolean;

function createIsolatedValidator(
  source: string,
  handlers: Record<string, IsolatedSemanticHandler>,
): IsolatedValidator {
  const sandbox = Object.create(null) as {
    __validateGenericAssetpack?: IsolatedValidator;
    __worldForgeAssetpackSemantics: Record<string, IsolatedSemanticHandler>;
  };
  sandbox.__worldForgeAssetpackSemantics = handlers;
  const context = createContext(sandbox, {
    codeGeneration: {
      strings: false,
      wasm: false,
    },
    name: "world-forge-generic-assetpack-validation",
  });
  new Script(ISOLATED_VALIDATOR_BOOTSTRAP, {
    filename: "world-forge-generic-assetpack-bootstrap.cjs",
  }).runInContext(context);
  new Script(source, {
    filename: "world-forge-generic-assetpack-validator.cjs",
  }).runInContext(context);
  new Script(ISOLATED_VALIDATOR_WRAPPER, {
    filename: "world-forge-generic-assetpack-wrapper.cjs",
  }).runInContext(context);
  const validator = sandbox.__validateGenericAssetpack;
  if (typeof validator !== "function") {
    throw new Error("Failed to initialize isolated generic assetpack validator");
  }
  return validator;
}

const ISOLATED_VALIDATOR_BOOTSTRAP = String.raw`
"use strict";
const __equalJson = function equalJson(left, right) {
  if (left === right) {
    return true;
  }
  if (
    left === null ||
    right === null ||
    typeof left !== "object" ||
    typeof right !== "object" ||
    left.constructor !== right.constructor
  ) {
    return false;
  }
  if (Array.isArray(left)) {
    if (!Array.isArray(right) || left.length !== right.length) {
      return false;
    }
    for (let index = left.length - 1; index >= 0; index -= 1) {
      if (!equalJson(left[index], right[index])) {
        return false;
      }
    }
    return true;
  }
  const leftKeys = Object.keys(left);
  const rightKeys = Object.keys(right);
  if (leftKeys.length !== rightKeys.length) {
    return false;
  }
  for (let index = leftKeys.length - 1; index >= 0; index -= 1) {
    const key = leftKeys[index];
    if (
      !Object.prototype.hasOwnProperty.call(right, key) ||
      !equalJson(left[key], right[key])
    ) {
      return false;
    }
  }
  return true;
};
const __ucs2Length = (value) => {
  let length = 0;
  let position = 0;
  while (position < value.length) {
    length += 1;
    const high = value.charCodeAt(position);
    position += 1;
    if (high >= 0xd800 && high <= 0xdbff && position < value.length) {
      const low = value.charCodeAt(position);
      if ((low & 0xfc00) === 0xdc00) {
        position += 1;
      }
    }
  }
  return length;
};
const __runtimeModules = Object.freeze({
  "ajv/dist/runtime/equal": Object.freeze({ default: __equalJson }),
  "ajv/dist/runtime/ucs2length": Object.freeze({ default: __ucs2Length }),
});
Object.defineProperty(globalThis, "exports", {
  configurable: false,
  enumerable: false,
  value: Object.create(null),
  writable: false,
});
Object.defineProperty(globalThis, "require", {
  configurable: false,
  enumerable: false,
  value: (identifier) => {
    if (!Object.prototype.hasOwnProperty.call(__runtimeModules, identifier)) {
      throw new Error("Unsupported isolated validator runtime module");
    }
    return __runtimeModules[identifier];
  },
  writable: false,
});
const __privateObjectPrototype = Object.prototype;
const __privateArrayPrototype = Array.prototype;
Object.setPrototypeOf(__privateArrayPrototype, null);
Object.freeze(__privateObjectPrototype);
Object.freeze(__privateArrayPrototype);
Object.freeze(Function.prototype);
Object.freeze(RegExp.prototype);
Object.freeze(String.prototype);
`;

const ISOLATED_VALIDATOR_WRAPPER = String.raw`
"use strict";
const __parseJson = JSON.parse;
const __stringifyJson = JSON.stringify;
Object.defineProperty(globalThis, "__validateGenericAssetpack", {
  configurable: false,
  enumerable: false,
  value: (snapshot) => {
    const candidate = __parseJson(__stringifyJson(snapshot));
    return exports.validateAssetpack(candidate) === true;
  },
  writable: false,
});
`;

type HostSnapshotLookup =
  | { readonly found: false }
  | { readonly found: true; readonly value: unknown };

const MISSING_HOST_SNAPSHOT_VALUE: HostSnapshotLookup = Object.freeze({
  found: false,
});
let activeHostSnapshot: Record<string, unknown> | undefined;

function decodeJsonPointerSegment(
  pointer: string,
  start: number,
  end: number,
): string | null {
  let decoded = "";
  for (let index = start; index < end; index += 1) {
    const character = pointer[index];
    if (character !== "~") {
      decoded += character;
      continue;
    }
    index += 1;
    if (index >= end) {
      return null;
    }
    const escaped = pointer[index];
    if (escaped === "0") {
      decoded += "~";
    } else if (escaped === "1") {
      decoded += "/";
    } else {
      return null;
    }
  }
  return decoded;
}

function hostSnapshotValueAt(
  root: Record<string, unknown>,
  instancePath: unknown,
): HostSnapshotLookup {
  if (typeof instancePath !== "string") {
    return MISSING_HOST_SNAPSHOT_VALUE;
  }
  if (instancePath.length === 0) {
    return { found: true, value: root };
  }
  if (instancePath[0] !== "/") {
    return MISSING_HOST_SNAPSHOT_VALUE;
  }
  let current: unknown = root;
  let segmentStart = 1;
  for (let index = 1; index <= instancePath.length; index += 1) {
    if (index !== instancePath.length && instancePath[index] !== "/") {
      continue;
    }
    const key = decodeJsonPointerSegment(instancePath, segmentStart, index);
    if (key === null || current === null || typeof current !== "object") {
      return MISSING_HOST_SNAPSHOT_VALUE;
    }
    const descriptor = getOwnPropertyDescriptor(current, key);
    if (descriptor === undefined || !("value" in descriptor)) {
      return MISSING_HOST_SNAPSHOT_VALUE;
    }
    current = descriptor.value;
    segmentStart = index + 1;
  }
  return { found: true, value: current };
}

function bridgeSemanticHandlers(
  handlers: Record<string, SemanticHandler>,
): Record<string, IsolatedSemanticHandler> {
  const bridged = Object.create(null) as Record<
    string,
    IsolatedSemanticHandler
  >;
  for (const key of ownKeys(handlers)) {
    if (typeof key !== "string") {
      throw new Error("Semantic handler names must be strings");
    }
    const handler = handlers[key];
    bridged[key] = (schema, _privateValue, instancePath) => {
      const root = activeHostSnapshot;
      if (root === undefined) {
        return false;
      }
      const lookup = hostSnapshotValueAt(root, instancePath);
      return lookup.found && handler(schema, lookup.value);
    };
  }
  return bridged;
}

function freezeHostSnapshot<T extends Record<string, unknown>>(root: T): T {
  const pending: object[] = [root];
  setPrototypeOf(pending, null);
  while (pending.length > 0) {
    const pendingIndex = pending.length - 1;
    const current = pending[pendingIndex];
    pending.length = pendingIndex;
    for (const key of ownKeys(current)) {
      const descriptor = getOwnPropertyDescriptor(current, key);
      const child: unknown =
        descriptor !== undefined && "value" in descriptor
          ? descriptor.value
          : undefined;
      if (child !== null && typeof child === "object") {
        pending[pending.length] = child;
      }
    }
    freezeObject(current);
  }
  return root;
}

const isolatedValidator = createIsolatedValidator(
  validatorSource,
  bridgeSemanticHandlers(semanticHandlers),
);

export function validateGenericAssetpack(
  value: unknown,
): ValidatedGenericAssetpack | null {
  if (value === null || typeof value !== "object") {
    return null;
  }
  let candidate: Record<string, unknown>;
  try {
    candidate = snapshotStrictJsonObject(value, {
      context: "generic assetpack",
      maxNodes: 200_000,
      maxKeys: 200_000,
      maxStringCodeUnits: MAX_ASSETPACK_MANIFEST_BYTES,
    });
  } catch {
    return null;
  }
  const previousHostSnapshot = activeHostSnapshot;
  activeHostSnapshot = candidate;
  try {
    if (!isolatedValidator(candidate)) {
      return null;
    }
  } catch {
    return null;
  } finally {
    activeHostSnapshot = previousHostSnapshot;
  }
  return freezeHostSnapshot(candidate) as ValidatedGenericAssetpack;
}

type FileState = Readonly<{
  ctimeNs: bigint;
  dev: bigint;
  ino: bigint;
  mode: bigint;
  mtimeNs: bigint;
  nlink: bigint;
  size: bigint;
}>;

type PinnedFile = Readonly<{
  bytes: Buffer;
  state: FileState;
}>;

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

function sameState(
  left: FileState,
  right: FileState,
): boolean {
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

async function pinnedFile(
  filename: string,
  root: string,
  maxBytes: number,
): Promise<PinnedFile | null> {
  const before = await lstat(filename, { bigint: true });
  if (
    !before.isFile() ||
    before.isSymbolicLink() ||
    before.nlink !== 1n ||
    before.size < 0n ||
    before.size > BigInt(maxBytes) ||
    (await realpath(filename)) !== filename ||
    !isWithin(root, filename)
  ) {
    return null;
  }
  const initial = stateOf(before);
  let handle;
  try {
    handle = await open(
      filename,
      constants.O_RDONLY |
        noFollowOpenFlagForPlatform(process.platform, constants.O_NOFOLLOW),
    );
  } catch {
    return null;
  }
  try {
    const opened = await handle.stat({ bigint: true });
    if (
      !opened.isFile() ||
      opened.nlink !== 1n ||
      !sameState(initial, stateOf(opened))
    ) {
      return null;
    }
    const bytes = await handle.readFile();
    const after = await handle.stat({ bigint: true });
    if (
      BigInt(bytes.length) !== opened.size ||
      !sameState(stateOf(opened), stateOf(after))
    ) {
      return null;
    }
    const final = await lstat(filename, { bigint: true });
    if (
      final.isSymbolicLink() ||
      !sameState(initial, stateOf(final)) ||
      (await realpath(filename)) !== filename
    ) {
      return null;
    }
    return { bytes, state: initial };
  } finally {
    await handle.close();
  }
}

function isWithin(root: string, candidate: string): boolean {
  return (
    candidate === root ||
    (!path.relative(root, candidate).startsWith(`..${path.sep}`) &&
      path.relative(root, candidate) !== ".." &&
      !path.isAbsolute(path.relative(root, candidate)))
  );
}

function canonicalPrettyJson(value: unknown): Buffer {
  function ordered(candidate: unknown): unknown {
    if (Array.isArray(candidate)) {
      const result = new Array(candidate.length);
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
  }
  return Buffer.from(`${JSON.stringify(ordered(value), null, 2)}\n`, "utf8");
}

type TreeSnapshot = Readonly<{
  directories: ReadonlyMap<string, FileState>;
  files: ReadonlyMap<string, FileState>;
}>;

async function snapshotTree(root: string): Promise<TreeSnapshot | null> {
  const directories = new Map<string, FileState>();
  const files = new Map<string, FileState>();
  const pending = [""];
  while (pending.length > 0) {
    const relativeDirectory = pending.pop();
    if (relativeDirectory === undefined) {
      break;
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
    for (const entry of entries) {
      if (
        entry.name !== entry.name.normalize("NFC") ||
        entry.name === "." ||
        entry.name === ".."
      ) {
        return null;
      }
      const relative =
        relativeDirectory === ""
          ? entry.name
          : `${relativeDirectory}/${entry.name}`;
      const absolute = path.join(absoluteDirectory, entry.name);
      const info = await lstat(absolute, { bigint: true });
      if (info.isSymbolicLink()) {
        return null;
      }
      const depth = relative.split("/").length;
      if (depth > MAX_ASSETPACK_TREE_DEPTH) {
        return null;
      }
      if (info.isDirectory()) {
        if (directories.size >= MAX_ASSETPACK_DIRECTORIES) {
          return null;
        }
        directories.set(relative, stateOf(info));
        pending.push(relative);
      } else if (info.isFile() && info.nlink === 1n) {
        if (files.size >= MAX_ASSETPACK_FILES) {
          return null;
        }
        files.set(relative, stateOf(info));
      } else {
        return null;
      }
      if (
        files.size + directories.size > MAX_ASSETPACK_TREE_NODES ||
        (await realpath(absolute)) !== absolute
      ) {
        return null;
      }
    }
  }
  return { directories, files };
}

function expectedDirectories(paths: ReadonlySet<string>): Set<string> {
  const directories = new Set([""]);
  for (const relative of paths) {
    const parts = relative.split("/");
    parts.pop();
    let current = "";
    for (const part of parts) {
      current = current === "" ? part : `${current}/${part}`;
      directories.add(current);
    }
  }
  return directories;
}

async function treeUnchanged(
  root: string,
  snapshot: TreeSnapshot,
): Promise<boolean> {
  const current = await snapshotTree(root);
  if (
    current === null ||
    current.directories.size !== snapshot.directories.size ||
    current.files.size !== snapshot.files.size
  ) {
    return false;
  }
  for (const [relative, state] of snapshot.directories) {
    const candidate = current.directories.get(relative);
    if (candidate === undefined || !sameState(state, candidate)) {
      return false;
    }
  }
  for (const [relative, state] of snapshot.files) {
    const candidate = current.files.get(relative);
    if (candidate === undefined || !sameState(state, candidate)) {
      return false;
    }
  }
  return true;
}

async function snapshotPathUnchanged(
  root: string,
  snapshot: TreeSnapshot,
  relative: string,
): Promise<boolean> {
  const rootState = snapshot.directories.get("");
  if (rootState === undefined) {
    return false;
  }
  const expected = snapshot.files.get(relative);
  if (expected === undefined) {
    return false;
  }
  const parts = relative.split("/");
  const directoryParts: string[] = [];
  const paths: Array<readonly [string, FileState]> = [["", rootState]];
  for (const part of parts.slice(0, -1)) {
    directoryParts.push(part);
    const directory = directoryParts.join("/");
    const state = snapshot.directories.get(directory);
    if (state === undefined) {
      return false;
    }
    paths.push([directory, state]);
  }
  for (const [directory, state] of paths) {
    const absolute =
      directory === "" ? root : path.join(root, ...directory.split("/"));
    const current = await lstat(absolute, { bigint: true });
    if (
      !current.isDirectory() ||
      current.isSymbolicLink() ||
      !sameState(state, stateOf(current)) ||
      (await realpath(absolute)) !== absolute
    ) {
      return false;
    }
  }
  const filename = path.join(root, ...parts);
  const current = await lstat(filename, { bigint: true });
  return (
    current.isFile() &&
    !current.isSymbolicLink() &&
    current.nlink === 1n &&
    sameState(expected, stateOf(current)) &&
    (await realpath(filename)) === filename
  );
}

function setEquals(
  left: ReadonlySet<string>,
  right: ReadonlySet<string>,
): boolean {
  if (left.size !== right.size) {
    return false;
  }
  for (const value of left) {
    if (!right.has(value)) {
      return false;
    }
  }
  return true;
}

export async function verifyGenericAssetpackDirectory(
  root: string,
  options: Readonly<{
    verificationHook?: (
      event: "after_file_read" | "after_manifest_read" | "after_tree_snapshot",
      relative?: string,
    ) => Promise<void> | void;
  }> = {},
): Promise<VerifiedGenericAssetpackEvidence | null> {
  try {
    if (
      typeof root !== "string" ||
      !path.isAbsolute(root) ||
      path.normalize(root) !== root
    ) {
      return null;
    }
    const absoluteRoot = path.resolve(root);
    if (absoluteRoot !== root || (await realpath(root)) !== root) {
      return null;
    }
    const tree = await snapshotTree(root);
    if (tree === null || !tree.files.has(ASSETPACK_MANIFEST)) {
      return null;
    }
    await options.verificationHook?.("after_tree_snapshot");
    if (!(await treeUnchanged(root, tree))) {
      return null;
    }
    const manifestFile = await pinnedFile(
      path.join(root, ASSETPACK_MANIFEST),
      root,
      MAX_ASSETPACK_MANIFEST_BYTES,
    );
    if (manifestFile === null) {
      return null;
    }
    await options.verificationHook?.(
      "after_manifest_read",
      ASSETPACK_MANIFEST,
    );
    if (
      !(await snapshotPathUnchanged(
        root,
        tree,
        ASSETPACK_MANIFEST,
      ))
    ) {
      return null;
    }
    const decoded = decodeStrictJsonObject(manifestFile.bytes, {
      context: "sealed generic assetpack manifest",
      maxBytes: MAX_ASSETPACK_MANIFEST_BYTES,
    });
    const document = validateGenericAssetpack(decoded);
    if (
      document === null ||
      !manifestFile.bytes.equals(canonicalPrettyJson(document))
    ) {
      return null;
    }
    const expectedFiles = new Set<string>([ASSETPACK_MANIFEST]);
    const outputs = new Map<string, unknown>();
    const notices = new Set<string>();
    for (
      let assetIndex = 0;
      assetIndex < document.assets.length;
      assetIndex += 1
    ) {
      const asset = document.assets[assetIndex];
      for (
        let outputIndex = 0;
        outputIndex < asset.outputs.length;
        outputIndex += 1
      ) {
        const output = asset.outputs[outputIndex];
        expectedFiles.add(output.runtime_path);
        expectedFiles.add(output.runtime_notice.path);
        outputs.set(output.runtime_path, output);
        notices.add(output.runtime_notice.path);
      }
    }
    if (
      !setEquals(expectedFiles, new Set(tree.files.keys())) ||
      !setEquals(expectedDirectories(expectedFiles), new Set(tree.directories.keys()))
    ) {
      return null;
    }
    for (
      let fileIndex = 0;
      fileIndex < document.inventory.files.length;
      fileIndex += 1
    ) {
      const entry = document.inventory.files[fileIndex];
      const filename = path.join(root, ...entry.path.split("/"));
      if (!isWithin(root, filename)) {
        return null;
      }
      const retained = await pinnedFile(
        filename,
        root,
        MAX_ASSETPACK_FILE_BYTES,
      );
      if (
        retained === null ||
        retained.bytes.length !== entry.size_bytes ||
        createHash("sha256").update(retained.bytes).digest("hex") !==
          entry.sha256
      ) {
        return null;
      }
      await options.verificationHook?.("after_file_read", entry.path);
      if (!(await snapshotPathUnchanged(root, tree, entry.path))) {
        return null;
      }
      const output = outputs.get(entry.path);
      if (
        output !== undefined &&
        inspectGenericAssetpackMedia(retained.bytes, output) === null
      ) {
        return null;
      }
      if (notices.has(entry.path)) {
        try {
          const notice = utf8Decoder.decode(retained.bytes);
          if (
            retained.bytes.length > MAX_ASSETPACK_NOTICE_BYTES ||
            Array.from(notice).length > MAX_ASSETPACK_NOTICE_CHARACTERS
          ) {
            return null;
          }
        } catch {
          return null;
        }
      }
      if (!(await snapshotPathUnchanged(root, tree, entry.path))) {
        return null;
      }
    }
    if (!(await treeUnchanged(root, tree))) {
      return null;
    }
    return freezeObject({
      assetpack_id: document.assetpack_id,
      content_hash: document.content_hash,
      file_count: document.inventory.file_count,
      root,
      status: "sealed" as const,
    });
  } catch {
    return null;
  }
}

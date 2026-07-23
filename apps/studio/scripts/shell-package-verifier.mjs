import { createHash } from "node:crypto";
import { spawn } from "node:child_process";
import {
  constants,
  lstatSync,
  readFileSync,
} from "node:fs";
import {
  lstat,
  mkdtemp,
  open,
  readdir,
} from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { createRequire } from "node:module";
import { createInterface } from "node:readline";
import { fileURLToPath } from "node:url";

import {
  FuseState,
  FuseV1Options,
  getCurrentFuseWire,
} from "@electron/fuses";
import Ajv2020 from "ajv/dist/2020.js";

const require = createRequire(import.meta.url);
const asar = require("@electron/asar");

const SCRIPT_ROOT = path.dirname(fileURLToPath(import.meta.url));
export const STUDIO_ROOT = path.resolve(SCRIPT_ROOT, "..");
export const SHELL_MANIFEST_PATH = "resources/shell-package-manifest.json";
export const SHELL_MANIFEST_SCHEMA_ID =
  "https://rpg-world-forge.local/schemas/studio-shell-package-manifest.schema.json";
export const SHELL_MANIFEST_FORMAT =
  "rpg-world-forge.studio_shell_package_manifest";

export const OPEN_BLOCKER_CODES = Object.freeze([
  "codex_ripgrep_static_dependency_notice_sbom_incomplete",
  "linux_bwrap_lgpl_corresponding_source_relink_build_materials_incomplete",
  "linux_bwrap_musl_provenance_incomplete",
  "pbs_zlib_ng_license_incomplete",
  "linux_berkeley_db_dbm_route_unresolved",
  "windows_vc_runtime_redistribution_authority_unresolved",
  "github_attestation_trust_root_rfc3161_verification_pending",
]);

export const EXPECTED_FUSES = Object.freeze({
  enable_cookie_encryption: true,
  enable_embedded_asar_integrity_validation: true,
  enable_node_cli_inspect_arguments: false,
  enable_node_options_environment_variable: false,
  grant_file_protocol_extra_privileges: false,
  only_load_app_from_asar: true,
  run_as_node: false,
});

const MAX_NODES = 20_000;
const MAX_DEPTH = 32;
const MAX_FILE_BYTES = 1_073_741_824;
const MAX_TOTAL_BYTES = 3_221_225_472;
const READ_CHUNK_BYTES = 1024 * 1024;
const PROTOCOL_ROOT =
  "resources/protocol/codex-app-server-0.144.6";
const SOURCE_PROTOCOL_ROOT =
  "protocol/codex-app-server-0.144.6";
const APP_ASAR_PATH = "resources/app.asar";
const RUNTIME_MANIFEST_PATH = "resources/runtime-manifest.json";
const RUNTIME_SOURCES_PATH = "resources/packaging/runtime-sources.json";

const SCHEMA_RESOURCE_PATHS = Object.freeze([
  "resources/packaging/runtime-package-manifest.schema.json",
  "resources/packaging/runtime-sources.schema.json",
  "resources/packaging/shell-package-manifest.schema.json",
]);

const SOURCE_COPY_MAP = Object.freeze([
  ["resources/runtime-manifest.json", RUNTIME_MANIFEST_PATH],
  ["packaging/runtime-sources.json", RUNTIME_SOURCES_PATH],
  [
    "packaging/runtime-package-manifest.schema.json",
    "resources/packaging/runtime-package-manifest.schema.json",
  ],
  [
    "packaging/runtime-sources.schema.json",
    "resources/packaging/runtime-sources.schema.json",
  ],
  [
    "packaging/shell-package-manifest.schema.json",
    "resources/packaging/shell-package-manifest.schema.json",
  ],
]);

const LOCALES = Object.freeze([
  "af",
  "am",
  "ar",
  "bg",
  "bn",
  "ca",
  "cs",
  "da",
  "de",
  "el",
  "en-GB",
  "en-US",
  "es",
  "es-419",
  "et",
  "fa",
  "fi",
  "fil",
  "fr",
  "gu",
  "he",
  "hi",
  "hr",
  "hu",
  "id",
  "it",
  "ja",
  "kn",
  "ko",
  "lt",
  "lv",
  "ml",
  "mr",
  "ms",
  "nb",
  "nl",
  "pl",
  "pt-BR",
  "pt-PT",
  "ro",
  "ru",
  "sk",
  "sl",
  "sr",
  "sv",
  "sw",
  "ta",
  "te",
  "th",
  "tr",
  "uk",
  "ur",
  "vi",
  "zh-CN",
  "zh-TW",
]);

const TARGETS = Object.freeze({
  "linux-x64": {
    executable: "rpg-world-forge-studio",
    rootFiles: [
      "LICENSE.electron.txt",
      "LICENSES.chromium.html",
      "chrome-sandbox",
      "chrome_100_percent.pak",
      "chrome_200_percent.pak",
      "chrome_crashpad_handler",
      "icudtl.dat",
      "libEGL.so",
      "libGLESv2.so",
      "libffmpeg.so",
      "libvk_swiftshader.so",
      "libvulkan.so.1",
      "resources.pak",
      "rpg-world-forge-studio",
      "snapshot_blob.bin",
      "v8_context_snapshot.bin",
      "vk_swiftshader_icd.json",
    ],
  },
  "win32-x64": {
    executable: "RPG World Forge Studio.exe",
    rootFiles: [
      "LICENSE.electron.txt",
      "LICENSES.chromium.html",
      "RPG World Forge Studio.exe",
      "chrome_100_percent.pak",
      "chrome_200_percent.pak",
      "d3dcompiler_47.dll",
      "ffmpeg.dll",
      "icudtl.dat",
      "libEGL.dll",
      "libGLESv2.dll",
      "resources.pak",
      "snapshot_blob.bin",
      "v8_context_snapshot.bin",
      "vk_swiftshader.dll",
      "vk_swiftshader_icd.json",
      "vulkan-1.dll",
    ],
  },
});

const textDecoder = new TextDecoder("utf-8", { fatal: true });

export class ShellPackageError extends Error {
  constructor(code, message = code) {
    super(message);
    this.name = "ShellPackageError";
    this.code = code;
  }
}

function fail(code, message = code) {
  throw new ShellPackageError(code, message);
}

function requireLinuxSecurePrimitive() {
  if (process.platform !== "linux" || !constants.O_NOFOLLOW) {
    fail(
      "secure_primitive_unavailable",
      "descriptor-relative no-follow traversal is unavailable on this host",
    );
  }
  try {
    const info = lstatSync("/proc/self/fd");
    if (!info.isDirectory()) {
      fail("secure_primitive_unavailable");
    }
  } catch {
    fail("secure_primitive_unavailable");
  }
}

function validateAbsoluteRoot(root) {
  if (
    typeof root !== "string" ||
    !path.isAbsolute(root) ||
    root.includes("\0") ||
    path.normalize(root) !== root ||
    path.parse(root).root === root
  ) {
    fail("invalid_package_root");
  }
  return root;
}

function compareUtf8(left, right) {
  return Buffer.compare(Buffer.from(left, "utf8"), Buffer.from(right, "utf8"));
}

function validatePortableSegment(segment) {
  if (
    segment.length < 1 ||
    Buffer.byteLength(segment, "utf8") > 255 ||
    segment !== segment.normalize("NFC") ||
    !/^[\u0020-\u007e]+$/u.test(segment) ||
    /[<>:"/\\|?*]/u.test(segment) ||
    /[ .]$/u.test(segment) ||
    segment === "." ||
    segment === ".." ||
    /^(?:aux|con|nul|prn|com[1-9]|lpt[1-9])(?:\..*)?$/iu.test(segment)
  ) {
    fail("nonportable_package_path");
  }
}

function validatePortablePath(relative) {
  if (
    typeof relative !== "string" ||
    relative.length < 1 ||
    Buffer.byteLength(relative, "utf8") > 1024 ||
    relative.startsWith("/") ||
    relative.includes("\\")
  ) {
    fail("nonportable_package_path");
  }
  for (const segment of relative.split("/")) {
    validatePortableSegment(segment);
  }
  return relative;
}

function sameIdentity(left, right) {
  return (
    left.dev === right.dev &&
    left.ino === right.ino &&
    left.mode === right.mode &&
    left.size === right.size &&
    left.nlink === right.nlink
  );
}

function procPath(handle, child = "") {
  const base = `/proc/self/fd/${String(handle.fd)}`;
  return child ? `${base}/${child}` : base;
}

async function digestHandle(handle, size) {
  const hash = createHash("sha256");
  const buffer = Buffer.allocUnsafe(Math.min(READ_CHUNK_BYTES, Math.max(size, 1)));
  let offset = 0;
  while (offset < size) {
    const length = Math.min(buffer.length, size - offset);
    const { bytesRead } = await handle.read(buffer, 0, length, offset);
    if (bytesRead !== length) {
      fail("package_file_changed");
    }
    hash.update(buffer.subarray(0, bytesRead));
    offset += bytesRead;
  }
  return hash.digest("hex");
}

async function readRecord(record, maxBytes = MAX_FILE_BYTES) {
  if (record.size > maxBytes) {
    fail("package_file_too_large");
  }
  const payload = Buffer.alloc(record.size);
  let offset = 0;
  while (offset < payload.length) {
    const { bytesRead } = await record.handle.read(
      payload,
      offset,
      payload.length - offset,
      offset,
    );
    if (bytesRead < 1) {
      fail("package_file_changed");
    }
    offset += bytesRead;
  }
  return payload;
}

async function openPinnedTree(rootPath) {
  requireLinuxSecurePrimitive();
  const absoluteRoot = validateAbsoluteRoot(rootPath);
  const handles = [];
  const files = new Map();
  const directories = new Map();
  const aliases = new Map();
  let totalBytes = 0;
  let nodes = 0;

  async function retain(handle) {
    handles.push(handle);
    return handle;
  }

  let rootHandle;
  try {
    rootHandle = await retain(
      await open(
        absoluteRoot,
        constants.O_RDONLY | constants.O_DIRECTORY | constants.O_NOFOLLOW,
      ),
    );
  } catch {
    fail("invalid_package_root");
  }
  const rootStat = await rootHandle.stat({ bigint: true });
  const rootPathStat = await lstat(absoluteRoot, { bigint: true });
  if (
    !rootStat.isDirectory() ||
    !rootPathStat.isDirectory() ||
    !sameIdentity(rootStat, rootPathStat)
  ) {
    fail("package_root_changed");
  }
  const rootRecord = {
    childNames: [],
    handle: rootHandle,
    name: "",
    parent: null,
    relative: "",
    stat: rootStat,
  };
  directories.set("", rootRecord);

  async function visit(directory, depth) {
    if (depth > MAX_DEPTH) {
      fail("package_tree_too_deep");
    }
    let entries;
    try {
      entries = await readdir(procPath(directory.handle), {
        encoding: "buffer",
        withFileTypes: true,
      });
    } catch {
      fail("package_directory_changed");
    }
    entries.sort((left, right) => Buffer.compare(left.name, right.name));
    directory.childNames = entries.map((entry) => Buffer.from(entry.name));
    for (const entry of entries) {
      nodes += 1;
      if (nodes > MAX_NODES) {
        fail("package_tree_too_large");
      }
      let name;
      try {
        name = textDecoder.decode(entry.name);
      } catch {
        fail("nonportable_package_path");
      }
      if (!Buffer.from(name, "utf8").equals(entry.name)) {
        fail("nonportable_package_path");
      }
      validatePortableSegment(name);
      const relative = directory.relative
        ? `${directory.relative}/${name}`
        : name;
      validatePortablePath(relative);
      const alias = relative.toLowerCase();
      const previous = aliases.get(alias);
      if (previous !== undefined && previous !== relative) {
        fail("package_path_alias");
      }
      aliases.set(alias, relative);

      const anchored = procPath(directory.handle, name);
      let before;
      try {
        before = await lstat(anchored, { bigint: true });
      } catch {
        fail("package_entry_changed");
      }
      if (!before.isDirectory() && !before.isFile()) {
        fail("package_non_regular_entry");
      }
      const flags = before.isDirectory()
        ? constants.O_RDONLY | constants.O_DIRECTORY | constants.O_NOFOLLOW
        : constants.O_RDONLY |
          constants.O_NONBLOCK |
          constants.O_NOFOLLOW;
      let handle;
      try {
        handle = await retain(await open(anchored, flags));
      } catch {
        fail("package_entry_changed");
      }
      const stat = await handle.stat({ bigint: true });
      if (!sameIdentity(before, stat)) {
        fail("package_entry_changed");
      }
      const record = {
        handle,
        name,
        parent: directory,
        relative,
        stat,
      };
      if (stat.isDirectory()) {
        directories.set(relative, { ...record, childNames: [] });
        await visit(directories.get(relative), depth + 1);
        continue;
      }
      if (!stat.isFile() || stat.nlink !== 1n) {
        fail("package_non_regular_entry");
      }
      if (stat.size > BigInt(MAX_FILE_BYTES)) {
        fail("package_file_too_large");
      }
      const size = Number(stat.size);
      totalBytes += size;
      if (totalBytes > MAX_TOTAL_BYTES) {
        fail("package_tree_too_large");
      }
      files.set(relative, {
        ...record,
        sha256: await digestHandle(handle, size),
        size,
      });
    }
  }

  try {
    await visit(rootRecord, 0);
  } catch (error) {
    for (const handle of handles.reverse()) {
      try {
        await handle.close();
      } catch {
        // Preserve the original fail-closed traversal error.
      }
    }
    throw error;
  }

  async function createExclusiveFile(parentRelative, name, payload) {
    validatePortableSegment(name);
    const parent = directories.get(parentRelative);
    if (!parent) {
      fail("package_resource_directory_missing");
    }
    const relative = parentRelative ? `${parentRelative}/${name}` : name;
    validatePortablePath(relative);
    if (files.has(relative) || directories.has(relative)) {
      fail("shell_manifest_already_exists");
    }
    const alias = relative.toLowerCase();
    if (aliases.has(alias)) {
      fail("package_path_alias");
    }
    let handle;
    try {
      handle = await retain(
        await open(
          procPath(parent.handle, name),
          constants.O_CREAT |
            constants.O_EXCL |
            constants.O_RDWR |
            constants.O_NOFOLLOW,
          0o644,
        ),
      );
      await handle.writeFile(payload);
      await handle.sync();
    } catch {
      fail("shell_manifest_publish_failed");
    }
    const stat = await handle.stat({ bigint: true });
    if (!stat.isFile() || stat.nlink !== 1n || stat.size !== BigInt(payload.length)) {
      fail("shell_manifest_publish_failed");
    }
    const record = {
      handle,
      name,
      parent,
      relative,
      sha256: await digestHandle(handle, payload.length),
      size: payload.length,
      stat,
    };
    files.set(relative, record);
    aliases.set(alias, relative);
    parent.childNames.push(Buffer.from(name, "utf8"));
    parent.childNames.sort(Buffer.compare);
    nodes += 1;
    totalBytes += payload.length;
    return record;
  }

  async function finalize() {
    const rootFinal = await lstat(absoluteRoot, { bigint: true });
    if (!sameIdentity(rootStat, rootFinal) || !rootFinal.isDirectory()) {
      fail("package_root_replaced");
    }
    const records = [
      ...directories.values(),
      ...files.values(),
    ].filter((record) => record.relative);
    for (const record of records) {
      let current;
      try {
        current = await lstat(procPath(record.parent.handle, record.name), {
          bigint: true,
        });
      } catch {
        fail("package_entry_replaced");
      }
      if (!sameIdentity(record.stat, current)) {
        fail("package_entry_replaced");
      }
    }
    for (const record of files.values()) {
      let retained;
      try {
        retained = await record.handle.stat({ bigint: true });
      } catch {
        fail("package_entry_replaced");
      }
      if (
        !retained.isFile() ||
        retained.nlink !== 1n ||
        retained.size !== BigInt(record.size) ||
        !sameIdentity(record.stat, retained) ||
        (await digestHandle(record.handle, record.size)) !== record.sha256
      ) {
        fail("package_entry_replaced");
      }
    }
    for (const directory of directories.values()) {
      const current = await readdir(procPath(directory.handle), {
        encoding: "buffer",
        withFileTypes: true,
      });
      const names = current
        .map((entry) => Buffer.from(entry.name))
        .sort(Buffer.compare);
      if (
        names.length !== directory.childNames.length ||
        names.some((name, index) => !name.equals(directory.childNames[index]))
      ) {
        fail("package_directory_replaced");
      }
    }
  }

  async function close() {
    for (const handle of handles.reverse()) {
      try {
        await handle.close();
      } catch {
        // The verification result is already fail-closed; closing is best effort.
      }
    }
  }

  return {
    close,
    createExclusiveFile,
    directories,
    files,
    finalize,
    rootPath: absoluteRoot,
  };
}

function identity(record, packagedPath = record.relative) {
  return {
    path: packagedPath,
    sha256: record.sha256,
    size: record.size,
  };
}

function canonicalJson(value) {
  if (
    value === null ||
    typeof value === "boolean" ||
    typeof value === "number" ||
    typeof value === "string"
  ) {
    return JSON.stringify(value);
  }
  if (Array.isArray(value)) {
    return `[${value.map((item) => canonicalJson(item)).join(",")}]`;
  }
  const record = value;
  return `{${Object.keys(record)
    .sort()
    .map((key) => `${JSON.stringify(key)}:${canonicalJson(record[key])}`)
    .join(",")}}`;
}

function canonicalJsonBytes(value) {
  return Buffer.from(`${canonicalJson(value)}\n`, "utf8");
}

function canonicalInventory(records) {
  return [...records]
    .sort((left, right) => compareUtf8(left.relative, right.relative))
    .map((record) => identity(record));
}

function parseJson(payload, code) {
  let text;
  try {
    text = textDecoder.decode(payload);
  } catch {
    fail(code);
  }
  try {
    return JSON.parse(text);
  } catch {
    fail(code);
  }
}

function exactObjectKeys(value, keys) {
  return (
    value !== null &&
    typeof value === "object" &&
    !Array.isArray(value) &&
    JSON.stringify(Object.keys(value).sort()) ===
      JSON.stringify([...keys].sort())
  );
}

function validateRuntimeSources(payload) {
  const document = parseJson(payload, "runtime_sources_invalid");
  const redistribution = document?.redistribution;
  if (
    document?.format !== "rpg-world-forge.studio_runtime_sources" ||
    document?.format_version !== 1 ||
    !exactObjectKeys(redistribution, [
      "open_blocker_codes",
      "release_ready",
      "status",
    ]) ||
    redistribution.status !== "blocked" ||
    redistribution.release_ready !== false ||
    JSON.stringify(redistribution.open_blocker_codes) !==
      JSON.stringify(OPEN_BLOCKER_CODES)
  ) {
    fail("runtime_sources_invalid");
  }
}

function validateRuntimeManifest(payload) {
  const document = parseJson(payload, "runtime_manifest_invalid");
  if (
    document?.format !== "rpg-world-forge.studio_runtime_manifest" ||
    document?.version !== 3 ||
    document?.codex?.version !== "0.144.6" ||
    document?.codex_protocol?.version !== "0.144.6" ||
    document?.codex_protocol?.manifest !==
      "protocol/codex-app-server-0.144.6/manifest.json" ||
    document?.package_manifest?.format_version !== 1 ||
    document?.package_manifest?.path !== "runtime-package-manifest.json"
  ) {
    fail("runtime_manifest_invalid");
  }
}

function treeIdentity(records) {
  const sorted = [...records].sort((left, right) =>
    compareUtf8(left.relative, right.relative),
  );
  const hash = createHash("sha256");
  let bytes = 0;
  for (const record of sorted) {
    const relative = record.relative;
    bytes += record.size;
    hash.update(relative, "utf8");
    hash.update("\0");
    hash.update(String(record.size), "ascii");
    hash.update("\0");
    hash.update(record.sha256, "ascii");
    hash.update("\0");
  }
  return {
    bytes,
    files: sorted.length,
    inventory_sha256: hash.digest("hex"),
  };
}

async function sourceControls(sourceRoot) {
  const trees = [];
  let closed = false;
  const close = async () => {
    if (closed) {
      return;
    }
    closed = true;
    for (const tree of trees.reverse()) {
      await tree.close();
    }
  };
  try {
    const sourceTrees = new Map();
    for (const relative of [
      "packaging",
      "resources",
      SOURCE_PROTOCOL_ROOT,
      "dist-electron",
      "dist-renderer",
    ]) {
      const tree = await openPinnedTree(
        path.join(sourceRoot, ...relative.split("/")),
      );
      trees.push(tree);
      sourceTrees.set(relative, tree);
    }

    const controls = new Map();
    for (const [sourcePath, packagedPath] of SOURCE_COPY_MAP) {
      const separator = sourcePath.indexOf("/");
      const rootName = sourcePath.slice(0, separator);
      const relative = sourcePath.slice(separator + 1);
      const record = sourceTrees.get(rootName)?.files.get(relative);
      if (!record || record.size > 8 * 1024 * 1024) {
        fail("source_resource_invalid");
      }
      const payload = await readRecord(record, 8 * 1024 * 1024);
      controls.set(packagedPath, {
        payload,
        sha256: record.sha256,
        size: record.size,
      });
    }
    validateRuntimeSources(controls.get(RUNTIME_SOURCES_PATH).payload);
    validateRuntimeManifest(controls.get(RUNTIME_MANIFEST_PATH).payload);

    const sourceProtocol = sourceTrees.get(SOURCE_PROTOCOL_ROOT);
    const records = [...sourceProtocol.files.values()];
    const summary = treeIdentity(records);
    const asarRecords = [];
    for (const root of ["dist-electron", "dist-renderer"]) {
      for (const record of sourceTrees.get(root).files.values()) {
        asarRecords.push({
          ...record,
          relative: `${root}/${record.relative}`,
        });
      }
    }
    return {
      asarRecords,
      close,
      controls,
      finalize: async () => {
        try {
          for (const tree of trees) {
            await tree.finalize();
          }
        } catch (error) {
          if (error instanceof ShellPackageError) {
            fail("source_resource_changed");
          }
          throw error;
        }
      },
      protocol: { records, summary },
    };
  } catch (error) {
    await close();
    throw error;
  }
}

function protocolRelative(packagedPath) {
  return packagedPath.slice(PROTOCOL_ROOT.length + 1);
}

function validateProtocolProvenance(record, protocolRecords) {
  const document = parseJson(
    readFileSync(procPath(record.handle)),
    "codex_protocol_manifest_invalid",
  );
  if (
    document?.format !==
      "rpg-world-forge.codex_app_server_protocol_provenance" ||
    document?.format_version !== 1 ||
    document?.codex_cli_version !== "0.144.6" ||
    document?.experimental !== false ||
    document?.mcp_protocol_version !== "2025-11-25"
  ) {
    fail("codex_protocol_manifest_invalid");
  }
  for (const [key, directory] of [
    ["json_schema", "json-schema/"],
    ["typescript", "typescript/"],
  ]) {
    const selected = protocolRecords
      .filter((candidate) => protocolRelative(candidate.relative).startsWith(directory))
      .map((candidate) => ({
        ...candidate,
        relative: protocolRelative(candidate.relative).slice(directory.length),
      }));
    const expected = document?.artifacts?.[key];
    const hash = createHash("sha256");
    let bytes = 0;
    for (const item of selected.sort((left, right) =>
      compareUtf8(left.relative, right.relative),
    )) {
      const payload = readFileSync(procPath(item.handle));
      bytes += payload.length;
      hash.update(item.relative, "utf8");
      hash.update("\0");
      hash.update(String(payload.length), "ascii");
      hash.update("\0");
      hash.update(payload);
    }
    if (
      expected?.bytes !== bytes ||
      expected?.files !== selected.length ||
      expected?.sha256 !== hash.digest("hex")
    ) {
      fail("codex_protocol_manifest_invalid");
    }
  }
}

async function compareCommittedResources(tree, sourceRoot) {
  const source = await sourceControls(sourceRoot);
  try {
    for (const [packagedPath, expected] of source.controls) {
      const actual = tree.files.get(packagedPath);
      if (
        !actual ||
        actual.size !== expected.size ||
        actual.sha256 !== expected.sha256 ||
        !(await readRecord(actual, expected.size)).equals(expected.payload)
      ) {
        fail("packaged_resource_mismatch");
      }
    }

    const packagedProtocol = [...tree.files.values()].filter((record) =>
      record.relative.startsWith(`${PROTOCOL_ROOT}/`),
    );
    const sourceByPath = new Map(
      source.protocol.records.map((record) => [record.relative, record]),
    );
    if (packagedProtocol.length !== sourceByPath.size) {
      fail("codex_protocol_tree_mismatch");
    }
    for (const record of packagedProtocol) {
      const expected = sourceByPath.get(protocolRelative(record.relative));
      if (
        !expected ||
        expected.size !== record.size ||
        expected.sha256 !== record.sha256
      ) {
        fail("codex_protocol_tree_mismatch");
      }
    }
    const summary = treeIdentity(
      packagedProtocol.map((record) => ({
        ...record,
        relative: protocolRelative(record.relative),
      })),
    );
    if (JSON.stringify(summary) !== JSON.stringify(source.protocol.summary)) {
      fail("codex_protocol_tree_mismatch");
    }
    const protocolManifest = tree.files.get(`${PROTOCOL_ROOT}/manifest.json`);
    if (!protocolManifest) {
      fail("codex_protocol_manifest_missing");
    }
    validateProtocolProvenance(protocolManifest, packagedProtocol);
    return { ...source, packagedProtocol, protocolManifest, summary };
  } catch (error) {
    await source.close();
    throw error;
  }
}

function asarEntryIdentity(items) {
  return createHash("sha256")
    .update(canonicalJsonBytes(items))
    .digest("hex");
}

function expectedAsarDirectories(filePaths) {
  const directories = new Set();
  for (const filePath of filePaths) {
    const components = filePath.split("/");
    for (let index = 1; index < components.length; index += 1) {
      directories.add(components.slice(0, index).join("/"));
    }
  }
  return [...directories].sort(compareUtf8);
}

function inspectAsar(record, sourceRecords) {
  const archivePath = record.snapshotPath ?? procPath(record.handle);
  let listed;
  try {
    listed = asar.listPackage(archivePath);
  } catch {
    fail("app_asar_invalid");
  }
  const aliases = new Map();
  const files = [];
  const directories = [];
  try {
    for (const raw of listed) {
      const relative = raw.startsWith("/") ? raw.slice(1) : raw;
      validatePortablePath(relative);
      const alias = relative.toLowerCase();
      const previous = aliases.get(alias);
      if (previous !== undefined && previous !== relative) {
        fail("app_asar_path_alias");
      }
      aliases.set(alias, relative);
      const stat = asar.statFile(archivePath, relative, false);
      if (stat.link !== undefined || stat.unpacked === true) {
        fail("app_asar_non_regular_entry");
      }
      if (typeof stat.size === "number") {
        files.push({ path: relative, size: stat.size });
      } else if (!stat.files || typeof stat.files !== "object") {
        fail("app_asar_non_regular_entry");
      } else {
        directories.push(relative);
      }
    }
    files.sort((left, right) => compareUtf8(left.path, right.path));
    directories.sort(compareUtf8);
    const sourceByPath = new Map(
      sourceRecords.map((source) => [source.relative, source]),
    );
    for (const required of [
      "dist-electron/main/index.cjs",
      "dist-electron/preload/index.cjs",
      "dist-renderer/index.html",
    ]) {
      if (!sourceByPath.has(required)) {
        fail("app_asar_entrypoint_missing");
      }
    }
    const expectedFiles = [...sourceByPath.keys(), "package.json"].sort(
      compareUtf8,
    );
    if (
      JSON.stringify(files.map((entry) => entry.path)) !==
        JSON.stringify(expectedFiles) ||
      JSON.stringify(directories) !==
        JSON.stringify(expectedAsarDirectories(expectedFiles))
    ) {
      fail("app_asar_inventory_mismatch");
    }
    for (const [relative, source] of sourceByPath) {
      const payload = Buffer.from(
        asar.extractFile(archivePath, relative, false),
      );
      if (
        payload.length !== source.size ||
        createHash("sha256").update(payload).digest("hex") !== source.sha256
      ) {
        fail("app_asar_source_mismatch");
      }
    }
    const packageBytes = Buffer.from(
      asar.extractFile(archivePath, "package.json", false),
    );
    const packageDocument = parseJson(packageBytes, "app_asar_package_invalid");
    if (
      packageDocument?.name !== "@rpg-world-forge/studio" ||
      packageDocument?.version !== "0.1.0" ||
      packageDocument?.private !== true ||
      packageDocument?.type !== "module" ||
      packageDocument?.main !== "dist-electron/main/index.cjs" ||
      !sameJson(packageDocument?.dependencies, {
        ajv: "8.20.0",
        react: "19.2.8",
        "react-dom": "19.2.8",
      }) ||
      Object.hasOwn(packageDocument, "scripts") ||
      Object.hasOwn(packageDocument, "devDependencies") ||
      Object.hasOwn(packageDocument, "optionalDependencies") ||
      Object.hasOwn(packageDocument, "peerDependencies") ||
      Object.hasOwn(packageDocument, "bundledDependencies") ||
      Object.hasOwn(packageDocument, "build")
    ) {
      fail("app_asar_package_invalid");
    }
    return {
      entries: files.length,
      entries_sha256: asarEntryIdentity(files),
      main: "dist-electron/main/index.cjs",
      path: APP_ASAR_PATH,
      preload: "dist-electron/preload/index.cjs",
      renderer: "dist-renderer/index.html",
      sha256: record.sha256,
      size: record.size,
    };
  } finally {
    asar.uncache(archivePath);
  }
}

async function readStaticFuses(executableRecord) {
  let wire;
  try {
    wire = await getCurrentFuseWire(
      executableRecord.snapshotPath ?? procPath(executableRecord.handle),
    );
  } catch {
    fail("electron_fuse_read_failed");
  }
  const actual = {
    enable_cookie_encryption:
      wire[FuseV1Options.EnableCookieEncryption] === FuseState.ENABLE,
    enable_embedded_asar_integrity_validation:
      wire[FuseV1Options.EnableEmbeddedAsarIntegrityValidation] ===
      FuseState.ENABLE,
    enable_node_cli_inspect_arguments:
      wire[FuseV1Options.EnableNodeCliInspectArguments] === FuseState.ENABLE,
    enable_node_options_environment_variable:
      wire[FuseV1Options.EnableNodeOptionsEnvironmentVariable] ===
      FuseState.ENABLE,
    grant_file_protocol_extra_privileges:
      wire[FuseV1Options.GrantFileProtocolExtraPrivileges] === FuseState.ENABLE,
    only_load_app_from_asar:
      wire[FuseV1Options.OnlyLoadAppFromAsar] === FuseState.ENABLE,
    run_as_node: wire[FuseV1Options.RunAsNode] === FuseState.ENABLE,
  };
  if (JSON.stringify(actual) !== JSON.stringify(EXPECTED_FUSES)) {
    fail("electron_fuses_not_hardened");
  }
  return actual;
}

function expectedTarget(targetId) {
  const target = TARGETS[targetId];
  if (!target) {
    fail("unsupported_shell_target");
  }
  return target;
}

function validateClosedOuterLayout(tree, targetId) {
  const target = expectedTarget(targetId);
  const expectedRoot = [...target.rootFiles].sort(compareUtf8);
  const actualRoot = [...tree.files.keys()]
    .filter((relative) => !relative.includes("/"))
    .sort(compareUtf8);
  if (JSON.stringify(actualRoot) !== JSON.stringify(expectedRoot)) {
    fail("electron_root_layout_mismatch");
  }
  const expectedLocales = LOCALES.map((locale) => `locales/${locale}.pak`).sort(
    compareUtf8,
  );
  const actualLocales = [...tree.files.keys()]
    .filter((relative) => relative.startsWith("locales/"))
    .sort(compareUtf8);
  if (JSON.stringify(actualLocales) !== JSON.stringify(expectedLocales)) {
    fail("electron_locale_layout_mismatch");
  }
  for (const relative of tree.files.keys()) {
    if (
      relative.includes("/") &&
      !relative.startsWith("locales/") &&
      !relative.startsWith("resources/")
    ) {
      fail("electron_layout_extra");
    }
    if (
      relative.startsWith("resources/runtime/") ||
      relative === "resources/runtime-package-manifest.json"
    ) {
      fail("shell_package_contains_runtime");
    }
  }
  const allowedResource = (relative) =>
    relative === APP_ASAR_PATH ||
    relative === RUNTIME_MANIFEST_PATH ||
    relative === RUNTIME_SOURCES_PATH ||
    relative === SHELL_MANIFEST_PATH ||
    SCHEMA_RESOURCE_PATHS.includes(relative) ||
    relative.startsWith(`${PROTOCOL_ROOT}/`);
  for (const relative of tree.files.keys()) {
    if (relative.startsWith("resources/") && !allowedResource(relative)) {
      fail("shell_resource_extra");
    }
  }
  return target;
}

function validateDirectoryClosure(tree) {
  const expected = new Set([""]);
  for (const relative of tree.files.keys()) {
    const components = relative.split("/");
    components.pop();
    let current = "";
    for (const component of components) {
      current = current ? `${current}/${component}` : component;
      expected.add(current);
    }
  }
  const actual = [...tree.directories.keys()].sort(compareUtf8);
  const closed = [...expected].sort(compareUtf8);
  if (JSON.stringify(actual) !== JSON.stringify(closed)) {
    fail("package_directory_extra");
  }
}

function validateManifestSchema(document, schemaBytes) {
  const schema = parseJson(schemaBytes, "shell_manifest_schema_invalid");
  const ajv = new Ajv2020({ allErrors: true, strict: true });
  const validate = ajv.compile(schema);
  if (!validate(document)) {
    fail("shell_manifest_schema_invalid");
  }
}

function sameJson(left, right) {
  return canonicalJson(left) === canonicalJson(right);
}

function decodeBackendControl(report, relative, required = true) {
  const encoded = report.controls?.[relative];
  if (encoded === undefined && !required) {
    return undefined;
  }
  if (
    typeof encoded !== "string" ||
    encoded.length > 16 * 1024 * 1024 ||
    !/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/u.test(
      encoded,
    )
  ) {
    fail("windows_backend_invalid");
  }
  const payload = Buffer.from(encoded, "base64");
  if (payload.toString("base64") !== encoded) {
    fail("windows_backend_invalid");
  }
  return payload;
}

function windowsTreeEvidence(report, targetId, snapshotRoot) {
  if (
    !exactObjectKeys(report, [
      "asar_source",
      "controls",
      "directories",
      "files",
      "format",
      "format_version",
      "protocol",
      "snapshots",
      "status",
      "target_id",
    ]) ||
    report.format !== "rpg-world-forge.studio_shell_snapshot" ||
    report.format_version !== 1 ||
    report.status !== "ready" ||
    report.target_id !== targetId ||
    !Array.isArray(report.files) ||
    !Array.isArray(report.directories) ||
    !Array.isArray(report.asar_source) ||
    report.files.length > MAX_NODES ||
    report.directories.length > MAX_NODES ||
    report.asar_source.length > MAX_NODES
  ) {
    fail("windows_backend_invalid");
  }
  const files = new Map();
  for (const raw of report.files) {
    if (
      !exactObjectKeys(raw, ["path", "sha256", "size"]) ||
      typeof raw.path !== "string" ||
      typeof raw.sha256 !== "string" ||
      !/^[0-9a-f]{64}$/u.test(raw.sha256) ||
      !Number.isSafeInteger(raw.size) ||
      raw.size < 0 ||
      raw.size > MAX_FILE_BYTES
    ) {
      fail("windows_backend_invalid");
    }
    validatePortablePath(raw.path);
    if (files.has(raw.path)) {
      fail("windows_backend_invalid");
    }
    files.set(raw.path, {
      relative: raw.path,
      sha256: raw.sha256,
      size: raw.size,
    });
  }
  const directories = new Map();
  for (const relative of report.directories) {
    if (typeof relative !== "string" || directories.has(relative)) {
      fail("windows_backend_invalid");
    }
    if (relative) {
      validatePortablePath(relative);
    }
    directories.set(relative, { relative });
  }
  if (!directories.has("")) {
    fail("windows_backend_invalid");
  }
  const asarRecords = [];
  const asarPaths = new Set();
  for (const raw of report.asar_source) {
    if (
      !exactObjectKeys(raw, ["path", "sha256", "size"]) ||
      typeof raw.path !== "string" ||
      (!raw.path.startsWith("dist-electron/") &&
        !raw.path.startsWith("dist-renderer/")) ||
      typeof raw.sha256 !== "string" ||
      !/^[0-9a-f]{64}$/u.test(raw.sha256) ||
      !Number.isSafeInteger(raw.size) ||
      raw.size < 0 ||
      raw.size > MAX_FILE_BYTES
    ) {
      fail("windows_backend_invalid");
    }
    validatePortablePath(raw.path);
    if (asarPaths.has(raw.path)) {
      fail("windows_backend_invalid");
    }
    asarPaths.add(raw.path);
    asarRecords.push({
      relative: raw.path,
      sha256: raw.sha256,
      size: raw.size,
    });
  }
  const target = expectedTarget(targetId);
  const snapshots = report.snapshots;
  if (
    !exactObjectKeys(snapshots, [APP_ASAR_PATH, target.executable])
  ) {
    fail("windows_backend_invalid");
  }
  for (const relative of [APP_ASAR_PATH, target.executable]) {
    const snapshotPath = snapshots[relative];
    if (
      typeof snapshotPath !== "string" ||
      !path.isAbsolute(snapshotPath) ||
      path.normalize(snapshotPath) !== snapshotPath ||
      path.dirname(snapshotPath) !== snapshotRoot
    ) {
      fail("windows_backend_invalid");
    }
    const record = files.get(relative);
    if (!record) {
      fail("windows_backend_invalid");
    }
    record.snapshotPath = snapshotPath;
  }
  const controls = [
    RUNTIME_MANIFEST_PATH,
    RUNTIME_SOURCES_PATH,
    ...SCHEMA_RESOURCE_PATHS,
    `${PROTOCOL_ROOT}/manifest.json`,
  ];
  for (const relative of controls) {
    const payload = decodeBackendControl(report, relative);
    const record = files.get(relative);
    if (
      !record ||
      payload.length !== record.size ||
      createHash("sha256").update(payload).digest("hex") !== record.sha256
    ) {
      fail("windows_backend_invalid");
    }
    record.payload = payload;
  }
  const shellPayload = decodeBackendControl(
    report,
    SHELL_MANIFEST_PATH,
    false,
  );
  if (shellPayload !== undefined) {
    const record = files.get(SHELL_MANIFEST_PATH);
    if (
      !record ||
      shellPayload.length !== record.size ||
      createHash("sha256").update(shellPayload).digest("hex") !== record.sha256
    ) {
      fail("windows_backend_invalid");
    }
    record.payload = shellPayload;
  } else if (files.has(SHELL_MANIFEST_PATH)) {
    fail("windows_backend_invalid");
  }
  validateRuntimeSources(files.get(RUNTIME_SOURCES_PATH).payload);
  validateRuntimeManifest(files.get(RUNTIME_MANIFEST_PATH).payload);
  const protocol = report.protocol;
  if (
    !exactObjectKeys(protocol, [
      "bytes",
      "files",
      "inventory_sha256",
    ]) ||
    !Number.isSafeInteger(protocol.bytes) ||
    protocol.bytes < 1 ||
    !Number.isSafeInteger(protocol.files) ||
    protocol.files < 3 ||
    typeof protocol.inventory_sha256 !== "string" ||
    !/^[0-9a-f]{64}$/u.test(protocol.inventory_sha256)
  ) {
    fail("windows_backend_invalid");
  }
  const protocolManifest = files.get(`${PROTOCOL_ROOT}/manifest.json`);
  const protocolDocument = parseJson(
    protocolManifest.payload,
    "codex_protocol_manifest_invalid",
  );
  if (
    protocolDocument?.format !==
      "rpg-world-forge.codex_app_server_protocol_provenance" ||
    protocolDocument?.format_version !== 1 ||
    protocolDocument?.codex_cli_version !== "0.144.6" ||
    protocolDocument?.experimental !== false ||
    protocolDocument?.mcp_protocol_version !== "2025-11-25"
  ) {
    fail("codex_protocol_manifest_invalid");
  }
  return {
    committed: {
      asarRecords,
      protocolManifest,
      summary: protocol,
    },
    schemaBytes: files.get(
      "resources/packaging/shell-package-manifest.schema.json",
    ).payload,
    tree: { directories, files },
  };
}

function windowsPythonExecutable(explicit) {
  const executable =
    explicit ??
    process.env.RWF_STUDIO_BUILD_PYTHON ??
    process.env.PYTHON ??
    (process.env.pythonLocation
      ? path.join(process.env.pythonLocation, "python.exe")
      : undefined);
  if (
    typeof executable !== "string" ||
    !path.isAbsolute(executable) ||
    path.normalize(executable) !== executable
  ) {
    fail("windows_python_backend_unavailable");
  }
  return executable;
}

async function withWindowsBackend(
  {
    outputPath,
    pythonExecutable,
    sourceRoot,
    targetId,
  },
  callback,
) {
  const executable = windowsPythonExecutable(pythonExecutable);
  const snapshotRoot = await mkdtemp(
    path.join(os.tmpdir(), "rwf-shell-snapshot-"),
  );
  const repoRoot = path.resolve(sourceRoot, "../..");
  const backend = path.join(
    sourceRoot,
    "scripts/shell_package_snapshot.py",
  );
  const environment = Object.fromEntries(
    Object.entries({
      PYTHONDONTWRITEBYTECODE: "1",
      PYTHONIOENCODING: "utf-8",
      PYTHONNOUSERSITE: "1",
      PYTHONPATH: [path.join(repoRoot, "src"), repoRoot].join(path.delimiter),
      PYTHONUTF8: "1",
      SYSTEMROOT: process.env.SYSTEMROOT,
      TEMP: process.env.TEMP,
      TMP: process.env.TMP,
      WINDIR: process.env.WINDIR,
    }).filter(([, value]) => typeof value === "string"),
  );
  const child = spawn(
    executable,
    [
      backend,
      "serve",
      "--path",
      outputPath,
      "--target",
      targetId,
      "--source-root",
      sourceRoot,
      "--snapshot-dir",
      snapshotRoot,
    ],
    {
      cwd: sourceRoot,
      env: environment,
      shell: false,
      stdio: ["pipe", "pipe", "pipe"],
      windowsHide: true,
    },
  );
  child.stderr.resume();
  const exited = new Promise((resolve, reject) => {
    child.once("error", () => reject(new ShellPackageError("windows_python_backend_unavailable")));
    child.once("close", (status) => resolve(status));
  });
  const lines = createInterface({
    crlfDelay: Infinity,
    input: child.stdout,
  });
  const iterator = lines[Symbol.asyncIterator]();
  let callbackResult;
  let completed = false;
  try {
    const first = await iterator.next();
    if (
      first.done ||
      typeof first.value !== "string" ||
      Buffer.byteLength(first.value, "utf8") > 16 * 1024 * 1024
    ) {
      fail("windows_backend_invalid");
    }
    const report = parseJson(
      Buffer.from(first.value, "utf8"),
      "windows_backend_invalid",
    );
    const evidence = windowsTreeEvidence(report, targetId, snapshotRoot);
    const outcome = await callback({ evidence, report, snapshotRoot });
    const { result, ...command } = outcome;
    child.stdin.end(canonicalJsonBytes(command));
    const final = await iterator.next();
    if (
      final.done ||
      !sameJson(
        parseJson(Buffer.from(final.value, "utf8"), "windows_backend_invalid"),
        { status: "finalized" },
      )
    ) {
      fail("windows_backend_invalid");
    }
    const status = await exited;
    if (status !== 0) {
      fail("windows_backend_failed");
    }
    completed = true;
    callbackResult = result;
  } finally {
    lines.close();
    if (!completed) {
      child.stdin.end();
      await exited.catch(() => undefined);
    }
  }
  return callbackResult;
}

async function createManifestDocument(
  tree,
  targetId,
  fuseReader,
  committedEvidence,
) {
  const target = validateClosedOuterLayout(tree, targetId);
  validateDirectoryClosure(tree);
  if (!committedEvidence) {
    fail("source_evidence_missing");
  }
  const committed = committedEvidence;
  const appAsarRecord = tree.files.get(APP_ASAR_PATH);
  const executableRecord = tree.files.get(target.executable);
  if (!appAsarRecord || !executableRecord) {
    fail("shell_entrypoint_missing");
  }
  const fuses = await fuseReader(executableRecord);
  if (!sameJson(fuses, EXPECTED_FUSES)) {
    fail("electron_fuses_not_hardened");
  }
  const inventory = canonicalInventory(
    [...tree.files.values()].filter(
      (record) => record.relative !== SHELL_MANIFEST_PATH,
    ),
  );
  return {
    app_asar: inspectAsar(appAsarRecord, committed.asarRecords),
    bundled_runtimes: {
      codex: false,
      python: false,
    },
    electron: {
      executable: target.executable,
      fuses,
    },
    format: SHELL_MANIFEST_FORMAT,
    format_version: 1,
    inventory,
    open_blocker_codes: [...OPEN_BLOCKER_CODES],
    package_kind: "shell_only",
    redistribution_status: "blocked",
    release_ready: false,
    resources: {
      codex_protocol: {
        ...committed.summary,
        manifest: identity(committed.protocolManifest),
        root: PROTOCOL_ROOT,
      },
      runtime_manifest: identity(tree.files.get(RUNTIME_MANIFEST_PATH)),
      runtime_sources: identity(tree.files.get(RUNTIME_SOURCES_PATH)),
      schemas: SCHEMA_RESOURCE_PATHS.map((relative) =>
        identity(tree.files.get(relative)),
      ),
    },
    schema_id: SHELL_MANIFEST_SCHEMA_ID,
    target_id: targetId,
  };
}

async function validateManifestDocument(
  document,
  tree,
  targetId,
  fuseReader,
  committedEvidence,
  schemaBytes,
) {
  const expected = await createManifestDocument(
    tree,
    targetId,
    fuseReader,
    committedEvidence,
  );
  const trustedSchema = schemaBytes ?? committedEvidence?.controls?.get(
    "resources/packaging/shell-package-manifest.schema.json",
  )?.payload;
  if (!Buffer.isBuffer(trustedSchema)) {
    fail("source_resource_invalid");
  }
  validateManifestSchema(document, trustedSchema);
  if (!sameJson(document, expected)) {
    fail("shell_manifest_evidence_mismatch");
  }
}

export async function writeShellPackageManifest({
  outputPath,
  pythonExecutable,
  targetId,
  sourceRoot = STUDIO_ROOT,
  fuseReader = readStaticFuses,
} = {}) {
  if (process.platform === "win32") {
    await withWindowsBackend(
      {
        outputPath,
        pythonExecutable,
        sourceRoot,
        targetId,
      },
      async ({ evidence }) => {
        if (evidence.tree.files.has(SHELL_MANIFEST_PATH)) {
          fail("shell_manifest_already_exists");
        }
        const document = await createManifestDocument(
          evidence.tree,
          targetId,
          fuseReader,
          evidence.committed,
        );
        validateManifestSchema(document, evidence.schemaBytes);
        const payload = canonicalJsonBytes(document);
        return {
          action: "publish",
          payload: payload.toString("base64"),
          result: null,
        };
      },
    );
    return verifyPackagedShell({
      outputPath,
      pythonExecutable,
      sourceRoot,
      targetId,
      fuseReader,
    });
  }
  const tree = await openPinnedTree(outputPath);
  let committed;
  try {
    if (tree.files.has(SHELL_MANIFEST_PATH)) {
      fail("shell_manifest_already_exists");
    }
    committed = await compareCommittedResources(tree, sourceRoot);
    const document = await createManifestDocument(
      tree,
      targetId,
      fuseReader,
      committed,
    );
    const schemaBytes = committed.controls.get(
      "resources/packaging/shell-package-manifest.schema.json",
    ).payload;
    validateManifestSchema(document, schemaBytes);
    const payload = canonicalJsonBytes(document);
    await tree.createExclusiveFile(
      "resources",
      "shell-package-manifest.json",
      payload,
    );
    validateDirectoryClosure(tree);
    await committed.finalize();
    await tree.finalize();
  } finally {
    await committed?.close();
    await tree.close();
  }
  return verifyPackagedShell({
    outputPath,
    sourceRoot,
    targetId,
    fuseReader,
  });
}

export async function verifyPackagedShell({
  beforeFinalBinding,
  outputPath,
  pythonExecutable,
  sourceRoot = STUDIO_ROOT,
  targetId,
  fuseReader = readStaticFuses,
} = {}) {
  if (process.platform === "win32") {
    return withWindowsBackend(
      {
        outputPath,
        pythonExecutable,
        sourceRoot,
        targetId,
      },
      async ({ evidence }) => {
        const manifestRecord = evidence.tree.files.get(SHELL_MANIFEST_PATH);
        if (
          !manifestRecord ||
          !manifestRecord.payload ||
          manifestRecord.size > 8 * 1024 * 1024
        ) {
          fail("shell_manifest_missing");
        }
        const document = parseJson(
          manifestRecord.payload,
          "shell_manifest_invalid_json",
        );
        if (
          !manifestRecord.payload.equals(canonicalJsonBytes(document))
        ) {
          fail("shell_manifest_not_canonical");
        }
        await validateManifestDocument(
          document,
          evidence.tree,
          targetId,
          fuseReader,
          evidence.committed,
          evidence.schemaBytes,
        );
        if (beforeFinalBinding) {
          await beforeFinalBinding();
        }
        return {
          action: "finalize",
          result: Object.freeze({
            package_kind: "shell_only",
            redistribution_status: "blocked",
            release_ready: false,
            target_id: targetId,
            verified_files: document.inventory.length + 1,
          }),
        };
      },
    );
  }
  const tree = await openPinnedTree(outputPath);
  let committed;
  try {
    const manifestRecord = tree.files.get(SHELL_MANIFEST_PATH);
    if (!manifestRecord || manifestRecord.size > 8 * 1024 * 1024) {
      fail("shell_manifest_missing");
    }
    const payload = await readRecord(manifestRecord, 8 * 1024 * 1024);
    const document = parseJson(payload, "shell_manifest_invalid_json");
    if (!payload.equals(canonicalJsonBytes(document))) {
      fail("shell_manifest_not_canonical");
    }
    committed = await compareCommittedResources(tree, sourceRoot);
    await validateManifestDocument(
      document,
      tree,
      targetId,
      fuseReader,
      committed,
    );
    if (beforeFinalBinding) {
      await beforeFinalBinding();
    }
    await committed.finalize();
    await tree.finalize();
    return Object.freeze({
      package_kind: "shell_only",
      redistribution_status: "blocked",
      release_ready: false,
      target_id: targetId,
      verified_files: document.inventory.length + 1,
    });
  } finally {
    await committed?.close();
    await tree.close();
  }
}

export function targetFromAfterPackContext(context) {
  if (context?.arch !== 1) {
    fail("unsupported_shell_target");
  }
  if (context?.electronPlatformName === "linux") {
    return "linux-x64";
  }
  if (context?.electronPlatformName === "win32") {
    return "win32-x64";
  }
  fail("unsupported_shell_target");
}

export function targetFixtureLayout(targetId) {
  const target = expectedTarget(targetId);
  return Object.freeze({
    executable: target.executable,
    locales: LOCALES.map((locale) => `locales/${locale}.pak`),
    rootFiles: [...target.rootFiles],
  });
}

export function staticFuseFixture() {
  return { ...EXPECTED_FUSES };
}

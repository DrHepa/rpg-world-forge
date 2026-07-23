import { createHash } from "node:crypto";
import { constants, type Stats } from "node:fs";
import { lstat, open, realpath } from "node:fs/promises";
import path from "node:path";
import { isDeepStrictEqual } from "node:util";

import type { FixedSpawnSpec } from "./ndjson-supervisor";

const MANIFEST_FORMAT = "rpg-world-forge.studio_runtime_manifest";
const MANIFEST_FILE = "runtime-manifest.json";
const MANIFEST_MAX_BYTES = 16 * 1024;
export const PACKAGE_MANIFEST_MAX_BYTES = 32 * 1024 * 1024;
export const PACKAGE_MANIFEST_MAX_JSON_DEPTH = 4;
export const PACKAGE_MANIFEST_MAX_JSON_NODES = 100_000;
export const PACKAGE_MANIFEST_MAX_INVENTORY = 16_384;
const PACKAGE_MANIFEST_MAX_BLOCKERS = 64;
const PACKAGE_MANIFEST_FORMAT = "rpg-world-forge.studio_runtime_package_manifest";
const PACKAGE_MANIFEST_SCHEMA =
  "https://rpg-world-forge.local/schemas/studio-runtime-package-manifest.schema.json";
const NORMALIZATION_FORMAT = "rpg-world-forge.studio_runtime_archive_normalization";
const NORMALIZATION_PACKAGE_PATH =
  "runtime/python/linux-x64/runtime-archive-normalization.json";
const NORMALIZATION_SIZE = 1_031_213;
const NORMALIZATION_SHA256 =
  "3c4fea7af2d435c036d412a56d7b762131e780560b339cbffe80e7637416db0e";
const LINUX_PBS_ARCHIVE_SHA256 =
  "5854aa6ec71cad00334d5065633c210b2e7feb40956767a59a91791cadcf0b79";
const LINUX_PBS_ARCHIVE_FILENAME =
  "cpython-3.12.13+20260718-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz";
const LINUX_PBS_ARCHIVE_SIZE = 34_199_823;
const NORMALIZATION_OUTPUT_FILES = 4_522;
const NORMALIZATION_REGULAR_FILES = 3_474;
const NORMALIZATION_SYMLINKS = 1_048;
const NORMALIZATION_OUTPUT_BYTES = 193_644_409;
const RUNTIME_SOURCES_FORMAT = "rpg-world-forge.studio_runtime_sources";
const RUNTIME_SOURCES_FILE = "runtime-sources.json";
const RUNTIME_SOURCES_SIZE = 13_717;
const RUNTIME_SOURCES_SHA256 =
  "99419da1ccc87cb8ea6c279e7e8e6bbc1d6b4d08eb6a67ae6ac7bf66d1182414";
const RUNTIME_SOURCES_SCHEMA =
  "https://rpg-world-forge.local/schemas/studio-runtime-sources.schema.json";
const VERIFIED_BLOCKERS = [
  "codex_ripgrep_static_dependency_notice_sbom_incomplete",
  "linux_bwrap_lgpl_corresponding_source_relink_build_materials_incomplete",
  "linux_bwrap_musl_provenance_incomplete",
  "pbs_zlib_ng_license_incomplete",
  "linux_berkeley_db_dbm_route_unresolved",
  "windows_vc_runtime_redistribution_authority_unresolved",
  "github_attestation_trust_root_rfc3161_verification_pending",
] as const;
const MAX_ARCHIVE_BYTES = 512 * 1024 * 1024;
const MAX_ARCHIVE_MEMBER_BYTES = 768 * 1024 * 1024;
const MAX_OUTPUT_BYTES = 6 * 1024 * 1024 * 1024;
const MAX_OUTPUT_FILES = PACKAGE_MANIFEST_MAX_INVENTORY;
const MAX_OUTPUT_DIRECTORIES = MAX_OUTPUT_FILES;
const MAX_OUTPUT_NODES = MAX_OUTPUT_FILES * 2 + 1;
const MAX_PATH_BYTES = 1024;
const MAX_PATH_DEPTH = 64;
const ZIP_MIN_EPOCH = 315_532_800;
const ZIP_MAX_EPOCH = 4_354_819_199;
const SERVICE_MODULE = "worldforge.studio";
export const FORGE_MCP_MODULE = "worldforge.studio.mcp_server";
export const CODEX_VERSION = "0.144.6";
const CODEX_TYPESCRIPT_SHA256 =
  "125dc17b4ef299a13428dd348c26fbc2c5436cbade0da19c2087217da90931f6";
const CODEX_JSON_SCHEMA_SHA256 =
  "fe9e9099c388569380a5595e75015321be54bf2215885c5e0a0696f6c717b81d";

type RuntimePlatformKey = "linux_x64" | "win32_x64";
type RuntimeTarget = "linux-x64" | "win32-x64";
type RuntimeComponent = "codex" | "control" | "forge" | "python";
const WINDOWS_FORBIDDEN = new Set(['<', '>', ':', '"', '|', '?', '*']);
const WINDOWS_RESERVED = new Set([
  "aux",
  "con",
  "nul",
  "prn",
  ...Array.from({ length: 9 }, (_, index) => `com${index + 1}`),
  ...Array.from({ length: 9 }, (_, index) => `lpt${index + 1}`),
]);

interface RuntimeManifest {
  format: typeof MANIFEST_FORMAT;
  version: 3;
  python: {
    service_module: typeof SERVICE_MODULE;
    mcp_module: typeof FORGE_MCP_MODULE;
    linux_x64: string;
    win32_x64: string;
  };
  codex: {
    version: typeof CODEX_VERSION;
    linux_x64: string;
    win32_x64: string;
  };
  codex_protocol: {
    version: typeof CODEX_VERSION;
    manifest: string;
  };
  package_manifest: {
    format_version: 1;
    path: string;
  };
}

interface RuntimePackageInventoryEntry {
  component: RuntimeComponent;
  mode: 420 | 493;
  path: string;
  sha256: string;
  size: number;
}

interface RuntimePackageManifest {
  assembly_kind: "synthetic_test_fixture" | "verified_development_runtime";
  format: typeof PACKAGE_MANIFEST_FORMAT;
  format_version: 1;
  inventory: RuntimePackageInventoryEntry[];
  launch: {
    codex: string;
    mcp_module: typeof FORGE_MCP_MODULE;
    python: string;
    service_module: typeof SERVICE_MODULE;
  };
  open_blocker_codes: string[];
  redistribution_status: "blocked";
  release_ready: false;
  schema_id: typeof PACKAGE_MANIFEST_SCHEMA;
  source_date_epoch: number;
  sources: {
    codex: RuntimeSourceIdentity;
    forge: { inventory_sha256: string; version: "0.7.0" };
    python: PythonRuntimeSourceIdentity;
    runtime_sources: RuntimeSourcesIdentity | null;
    runtime_sources_sha256: string;
  };
  target_id: RuntimeTarget;
}

interface RuntimeSourceIdentity {
  archive: {
    entrypoint: string;
    filename: string;
    payload_root: string;
    sha256: string;
    size: number;
  };
  version: string;
}

interface RuntimeNormalizationIdentity {
  archive_sha256: typeof LINUX_PBS_ARCHIVE_SHA256;
  format: typeof NORMALIZATION_FORMAT;
  format_version: 1;
  path: typeof NORMALIZATION_PACKAGE_PATH;
  sha256: typeof NORMALIZATION_SHA256;
  size: typeof NORMALIZATION_SIZE;
}

interface RuntimeSourcesIdentity {
  format: typeof RUNTIME_SOURCES_FORMAT;
  format_version: 1;
  path: typeof RUNTIME_SOURCES_FILE;
  sha256: typeof RUNTIME_SOURCES_SHA256;
  size: typeof RUNTIME_SOURCES_SIZE;
}

interface PythonRuntimeSourceIdentity extends RuntimeSourceIdentity {
  normalization: RuntimeNormalizationIdentity | null;
}

interface NormalizationFile {
  link: string | null;
  mode: 420 | 493;
  sha256: string;
  size: number;
  source: string;
  source_kind: "regular" | "symlink";
  target: string;
}

interface CasefoldDirectoryPair {
  first: string;
  second: string;
}

interface CasefoldFilePair {
  first_mode: 420 | 493;
  first_sha256: string;
  first_size: number;
  first_source: string;
  first_target: string;
  second_mode: 420 | 493;
  second_sha256: string;
  second_size: number;
  second_source: string;
  second_target: string;
}

interface RuntimeArchiveNormalization {
  casefold_directories: CasefoldDirectoryPair[];
  casefold_files: CasefoldFilePair[];
  files: NormalizationFile[];
}

interface LoadedRuntimeManifest {
  bytes: Buffer;
  manifest: RuntimeManifest;
}

interface PackagedRuntimeContract {
  manifest: RuntimeManifest;
  packageManifest: RuntimePackageManifest;
}

interface PinnedResourceFile {
  bytes: Buffer;
  filename: string;
  mode: number;
}

export interface ForgeLaunchOptions {
  packaged: boolean;
  resourcesPath: string;
  dataDir: string;
  environment?: NodeJS.ProcessEnv;
  platform?: NodeJS.Platform;
  architecture?: string;
}

export interface CodexRuntime {
  codexExecutable: string;
  pythonExecutable: string;
  version: typeof CODEX_VERSION;
}

export async function resolveForgeServiceLaunch(options: ForgeLaunchOptions): Promise<FixedSpawnSpec> {
  assertAbsoluteSafePath(options.dataDir, "Studio data directory");
  const environment = options.environment ?? process.env;
  if (!options.packaged) {
    const configured = environment.RWF_STUDIO_DEV_PYTHON;
    if (!configured) {
      throw new Error(
        "Development service is not configured. Set RWF_STUDIO_DEV_PYTHON to an absolute Python 3.11/3.12 interpreter path.",
      );
    }
    assertAbsoluteSafePath(configured, "Development Python interpreter");
    await assertDevelopmentInterpreter(configured);
    return fixedPythonSpec(configured, options.dataDir, environment);
  }

  assertAbsoluteSafePath(options.resourcesPath, "Packaged resources directory");
  const platform = options.platform ?? process.platform;
  const architecture = options.architecture ?? process.arch;
  const key = runtimePlatformKey(platform, architecture);
  const contract = await loadPackagedRuntimeContract(options.resourcesPath, key);
  const entry = inventoryEntry(
    contract.packageManifest,
    contract.manifest.python[key],
    "python",
  );
  const executable = await resolvePackagedExecutable(
    options.resourcesPath,
    contract.manifest.python[key],
    platform,
    entry,
  );
  return fixedPythonSpec(executable, options.dataDir, environment);
}

export async function resolveCodexRuntime(
  options: ForgeLaunchOptions,
): Promise<CodexRuntime> {
  const environment = options.environment ?? process.env;
  if (!options.packaged) {
    const codex = environment.RWF_STUDIO_DEV_CODEX;
    const python = environment.RWF_STUDIO_DEV_PYTHON;
    if (!codex || !python) {
      throw new Error(
        "Development Codex bridge requires absolute RWF_STUDIO_DEV_CODEX and RWF_STUDIO_DEV_PYTHON paths.",
      );
    }
    assertAbsoluteSafePath(codex, "Development Codex executable");
    assertAbsoluteSafePath(python, "Development Python interpreter");
    return {
      codexExecutable: await assertDevelopmentExecutable(codex, "Codex executable"),
      pythonExecutable: await assertDevelopmentExecutable(python, "Python interpreter"),
      version: CODEX_VERSION,
    };
  }

  assertAbsoluteSafePath(options.resourcesPath, "Packaged resources directory");
  const platform = options.platform ?? process.platform;
  const architecture = options.architecture ?? process.arch;
  const key = runtimePlatformKey(platform, architecture);
  const contract = await loadPackagedRuntimeContract(options.resourcesPath, key);
  await verifyPackagedProtocol(
    options.resourcesPath,
    contract.manifest.codex_protocol.manifest,
    inventoryEntry(
      contract.packageManifest,
      contract.manifest.codex_protocol.manifest,
      "forge",
    ),
  );
  return {
    codexExecutable: await resolvePackagedExecutable(
      options.resourcesPath,
      contract.manifest.codex[key],
      platform,
      inventoryEntry(
        contract.packageManifest,
        contract.manifest.codex[key],
        "codex",
      ),
    ),
    pythonExecutable: await resolvePackagedExecutable(
      options.resourcesPath,
      contract.manifest.python[key],
      platform,
      inventoryEntry(
        contract.packageManifest,
        contract.manifest.python[key],
        "python",
      ),
    ),
    version: CODEX_VERSION,
  };
}

async function loadRuntimeManifest(resourcesPath: string): Promise<LoadedRuntimeManifest> {
  const resource = await readPinnedResourceFile(
    resourcesPath,
    MANIFEST_FILE,
    MANIFEST_MAX_BYTES,
    "Studio runtime manifest",
  );
  const expected = canonicalRuntimeManifest();
  if (!resource.bytes.equals(canonicalJsonBytes(expected))) {
    throw new Error("Packaged Studio runtime manifest bytes are not canonical");
  }
  let parsed: unknown;
  try {
    parsed = parseStrictUtf8Json(resource.bytes);
  } catch (error) {
    throw new Error("Packaged Studio runtime manifest is not valid UTF-8 JSON", { cause: error });
  }
  if (!isDeepStrictEqual(parsed, expected)) {
    throw new Error("Packaged Studio runtime manifest is not the canonical launch contract");
  }
  return { bytes: resource.bytes, manifest: expected };
}

function canonicalRuntimeManifest(): RuntimeManifest {
  return {
    codex: {
      linux_x64: "runtime/codex/linux-x64/bin/codex",
      version: CODEX_VERSION,
      win32_x64: "runtime/codex/win32-x64/bin/codex.exe",
    },
    codex_protocol: {
      manifest: "protocol/codex-app-server-0.144.6/manifest.json",
      version: CODEX_VERSION,
    },
    format: MANIFEST_FORMAT,
    package_manifest: {
      format_version: 1,
      path: "runtime-package-manifest.json",
    },
    python: {
      linux_x64: "runtime/python/linux-x64/bin/python3",
      mcp_module: FORGE_MCP_MODULE,
      service_module: SERVICE_MODULE,
      win32_x64: "runtime/python/win32-x64/python.exe",
    },
    version: 3,
  };
}

async function loadPackagedRuntimeContract(
  resourcesPath: string,
  key: RuntimePlatformKey,
): Promise<PackagedRuntimeContract> {
  const loaded = await loadRuntimeManifest(resourcesPath);
  const packageResource = await readPinnedResourceFile(
    resourcesPath,
    loaded.manifest.package_manifest.path,
    PACKAGE_MANIFEST_MAX_BYTES,
    "runtime package manifest",
  );
  let parsed: unknown;
  try {
    parsed = parseStrictUtf8Json(packageResource.bytes, {
      maxBytes: PACKAGE_MANIFEST_MAX_BYTES,
      maxDepth: PACKAGE_MANIFEST_MAX_JSON_DEPTH,
      maxNodes: PACKAGE_MANIFEST_MAX_JSON_NODES,
    });
  } catch (error) {
    throw new Error("Packaged runtime package manifest is not valid UTF-8 JSON", {
      cause: error,
    });
  }
  const normalizationResource = manifestUsesLinuxPbs(parsed)
    ? await readPinnedResourceFile(
        resourcesPath,
        NORMALIZATION_PACKAGE_PATH,
        NORMALIZATION_SIZE,
        "Linux Python archive normalization receipt",
      )
    : undefined;
  const runtimeSourcesResource =
    isRecord(parsed) && parsed.assembly_kind === "verified_development_runtime"
      ? await readPinnedResourceFile(
          resourcesPath,
          RUNTIME_SOURCES_FILE,
          RUNTIME_SOURCES_SIZE,
          "Studio runtime source provenance",
        )
      : undefined;
  const packageManifest = validateRuntimePackageManifestSemantic(
    parsed,
    normalizationResource?.bytes,
    runtimeSourcesResource?.bytes,
  );
  if (!packageResource.bytes.equals(canonicalJsonBytes(packageManifest))) {
    throw new Error("Packaged runtime package manifest is not canonical JSON");
  }
  const expectedTarget: RuntimeTarget =
    key === "linux_x64" ? "linux-x64" : "win32-x64";
  if (packageManifest.target_id !== expectedTarget) {
    throw new Error("Packaged runtime target does not match the host launch selection");
  }
  const control = inventoryEntry(
    packageManifest,
    MANIFEST_FILE,
    "control",
  );
  if (
    control.mode !== 420 ||
    control.size !== loaded.bytes.length ||
    control.sha256 !== sha256(loaded.bytes)
  ) {
    throw new Error("Packaged Studio runtime manifest does not match its package inventory");
  }
  return { manifest: loaded.manifest, packageManifest };
}

export function validateRuntimePackageManifestSemantic(
  value: unknown,
  normalizationBytes?: Uint8Array,
  runtimeSourcesBytes?: Uint8Array,
): RuntimePackageManifest {
  const root = requireExactRecord(
    value,
    [
      "assembly_kind",
      "format",
      "format_version",
      "inventory",
      "launch",
      "open_blocker_codes",
      "redistribution_status",
      "release_ready",
      "schema_id",
      "source_date_epoch",
      "sources",
      "target_id",
    ],
    "$",
  );
  if (
    (root.assembly_kind !== "synthetic_test_fixture" &&
      root.assembly_kind !== "verified_development_runtime") ||
    root.format !== PACKAGE_MANIFEST_FORMAT ||
    root.format_version !== 1 ||
    root.redistribution_status !== "blocked" ||
    root.release_ready !== false ||
    root.schema_id !== PACKAGE_MANIFEST_SCHEMA ||
    (root.target_id !== "linux-x64" && root.target_id !== "win32-x64") ||
    !isBoundedInteger(root.source_date_epoch, ZIP_MIN_EPOCH, ZIP_MAX_EPOCH)
  ) {
    semanticError("$");
  }
  const blockerCodes = root.open_blocker_codes;
  if (!isBlockerCodes(blockerCodes)) {
    semanticError("$.open_blocker_codes");
  }

  const launch = requireExactRecord(
    root.launch,
    ["codex", "mcp_module", "python", "service_module"],
    "$.launch",
  );
  const expectedLaunch: RuntimePackageManifest["launch"] =
    root.target_id === "linux-x64"
      ? {
          codex: "runtime/codex/linux-x64/bin/codex",
          mcp_module: FORGE_MCP_MODULE,
          python: "runtime/python/linux-x64/bin/python3",
          service_module: SERVICE_MODULE,
        }
      : {
          codex: "runtime/codex/win32-x64/bin/codex.exe",
          mcp_module: FORGE_MCP_MODULE,
          python: "runtime/python/win32-x64/python.exe",
          service_module: SERVICE_MODULE,
        };
  if (!isDeepStrictEqual(launch, expectedLaunch)) {
    semanticError("$.launch");
  }

  const sources = requireExactRecord(
    root.sources,
    ["codex", "forge", "python", "runtime_sources", "runtime_sources_sha256"],
    "$.sources",
  );
  if (!isSha256(sources.runtime_sources_sha256)) {
    semanticError("$.sources.runtime_sources_sha256");
  }
  const sourceEntries = new Map<"codex" | "python", RuntimeSourceIdentity>();
  const archiveFilenameAliases = new Set<string>();
  let pythonNormalizationValue: unknown;
  for (const [component, version] of [
    ["codex", CODEX_VERSION],
    ["python", "3.12.13"],
  ] as const) {
    const source = requireExactRecord(
      sources[component],
      component === "python"
        ? ["archive", "normalization", "version"]
        : ["archive", "version"],
      `$.sources.${component}`,
    );
    const archive = requireExactRecord(
      source.archive,
      ["entrypoint", "filename", "payload_root", "sha256", "size"],
      `$.sources.${component}.archive`,
    );
    if (
      source.version !== version ||
      !isPortableResourcePath(archive.filename) ||
      archive.filename.includes("/") ||
      !isBoundedInteger(archive.size, 1, MAX_ARCHIVE_BYTES) ||
      !isSha256(archive.sha256) ||
      !isPortableResourcePath(archive.payload_root) ||
      !isPortableResourcePath(archive.entrypoint) ||
      archive.entrypoint === archive.payload_root ||
      !archive.entrypoint.startsWith(`${archive.payload_root}/`)
    ) {
      semanticError(`$.sources.${component}`);
    }
    const filenameAlias = portablePathAlias(archive.filename);
    if (archiveFilenameAliases.has(filenameAlias)) {
      semanticError("$.sources");
    }
    archiveFilenameAliases.add(filenameAlias);
    sourceEntries.set(component, {
      archive: {
        entrypoint: archive.entrypoint,
        filename: archive.filename,
        payload_root: archive.payload_root,
        sha256: archive.sha256,
        size: archive.size,
      },
      version,
    });
    if (component === "python") {
      pythonNormalizationValue = source.normalization;
    }
  }
  const pythonSource = sourceEntries.get("python");
  if (!pythonSource) {
    semanticError("$.sources.python");
  }
  let runtimeSourcesIdentity: RuntimeSourcesIdentity | null = null;
  if (root.assembly_kind === "verified_development_runtime") {
    runtimeSourcesIdentity = validateRuntimeSourcesIdentity(
      sources.runtime_sources,
    );
    if (
      sources.runtime_sources_sha256 !== RUNTIME_SOURCES_SHA256 ||
      runtimeSourcesBytes === undefined
    ) {
      semanticError("$.sources.runtime_sources");
    }
    const provenance = validateRuntimeSourcesProvenance(
      Buffer.from(runtimeSourcesBytes),
      root.target_id,
    );
    if (
      !isDeepStrictEqual(sourceEntries.get("codex")?.archive, provenance.codex) ||
      !isDeepStrictEqual(pythonSource.archive, provenance.python) ||
      !isDeepStrictEqual(blockerCodes, [...VERIFIED_BLOCKERS])
    ) {
      semanticError("$.sources");
    }
  } else if (sources.runtime_sources !== null) {
    semanticError("$.sources.runtime_sources");
  }
  let normalization: RuntimeArchiveNormalization | null = null;
  let normalizationIdentity: RuntimeNormalizationIdentity | null = null;
  if (root.target_id === "win32-x64") {
    if (pythonNormalizationValue !== null) {
      semanticError("$.sources.python.normalization");
    }
  } else if (pythonSource.archive.sha256 === LINUX_PBS_ARCHIVE_SHA256) {
    if (
      !isDeepStrictEqual(pythonSource.archive, canonicalLinuxPbsArchiveIdentity())
    ) {
      semanticError("$.sources.python.archive");
    }
    normalizationIdentity = validateNormalizationIdentity(pythonNormalizationValue);
    if (normalizationBytes === undefined) {
      semanticError("$.sources.python.normalization");
    }
    normalization = validateArchiveNormalization(
      Buffer.from(normalizationBytes),
      normalizationIdentity,
    );
  } else if (pythonNormalizationValue !== null) {
    semanticError("$.sources.python.normalization");
  }
  const forge = requireExactRecord(
    sources.forge,
    ["inventory_sha256", "version"],
    "$.sources.forge",
  );
  if (forge.version !== "0.7.0" || !isSha256(forge.inventory_sha256)) {
    semanticError("$.sources.forge");
  }

  if (
    !Array.isArray(root.inventory) ||
    root.inventory.length === 0 ||
    root.inventory.length > MAX_OUTPUT_FILES
  ) {
    semanticError("$.inventory");
  }
  const inventory: RuntimePackageInventoryEntry[] = [];
  const allowedFileAliases = normalization
    ? normalizationCasefoldFiles(normalization, root.target_id)
    : new Map<string, Set<string>>();
  const aliases = new Map<string, Set<string>>();
  let totalBytes = 0;
  for (const [index, valueEntry] of root.inventory.entries()) {
    const entry = requireExactRecord(
      valueEntry,
      ["component", "mode", "path", "sha256", "size"],
      `$.inventory[${index}]`,
    );
    if (
      (entry.component !== "codex" &&
        entry.component !== "control" &&
        entry.component !== "forge" &&
        entry.component !== "python") ||
      (entry.mode !== 420 && entry.mode !== 493) ||
      !isPortableResourcePath(entry.path) ||
      entry.path === "runtime-package-manifest.json" ||
      !isSha256(entry.sha256) ||
      !isBoundedInteger(entry.size, 0, MAX_ARCHIVE_MEMBER_BYTES)
    ) {
      semanticError(`$.inventory[${index}]`);
    }
    const alias = portablePathAlias(entry.path);
    const existing = aliases.get(alias) ?? new Set<string>();
    const combined = new Set(existing);
    combined.add(entry.path);
    const allowed = allowedFileAliases.get(alias);
    if (
      existing.has(entry.path) ||
      (existing.size > 0 &&
        (allowed === undefined || !isSubset(combined, allowed)))
    ) {
      semanticError("$.inventory");
    }
    combined.add(entry.path);
    aliases.set(alias, combined);
    totalBytes += entry.size;
    if (totalBytes > MAX_OUTPUT_BYTES) {
      semanticError("$.inventory");
    }
    inventory.push({
      component: entry.component,
      mode: entry.mode,
      path: entry.path,
      sha256: entry.sha256,
      size: entry.size,
    });
  }
  const sortedPaths = inventory
    .map((entry) => entry.path)
    .toSorted(compareUtf8);
  if (
    inventory.some((entry, index) => entry.path !== sortedPaths[index])
  ) {
    semanticError("$.inventory");
  }
  requireExactAliasGroups(
    new Map([...aliases].filter(([, values]) => values.size > 1)),
    allowedFileAliases,
    "$.inventory",
  );
  validateInventoryDirectoryAliases(
    inventory,
    normalization,
    root.target_id,
  );

  const target = root.target_id;
  const codexPrefix = `runtime/codex/${target}/`;
  const pythonPrefix = `runtime/python/${target}/`;
  const allowedControl = new Set([MANIFEST_FILE]);
  if (runtimeSourcesIdentity !== null) {
    allowedControl.add(RUNTIME_SOURCES_FILE);
  }
  if (normalization !== null) {
    allowedControl.add(NORMALIZATION_PACKAGE_PATH);
    const expectedPython = normalization.files.map(
      (item): RuntimePackageInventoryEntry => ({
        component: "python",
        mode: item.mode,
        path: `${pythonPrefix}${item.source}`,
        sha256: item.sha256,
        size: item.size,
      }),
    );
    const actualPython = inventory.filter((entry) => entry.component === "python");
    if (!isDeepStrictEqual(actualPython, expectedPython)) {
      semanticError("$.inventory");
    }
  }
  for (const entry of inventory) {
    if (
      (entry.component === "codex" && !entry.path.startsWith(codexPrefix)) ||
      (entry.component === "python" && !entry.path.startsWith(pythonPrefix)) ||
      (entry.component === "forge" &&
        !entry.path.startsWith(pythonPrefix) &&
        !entry.path.startsWith("protocol/codex-app-server-0.144.6/")) ||
      (entry.component === "control" && !allowedControl.has(entry.path))
    ) {
      semanticError("$.inventory");
    }
  }
  const typedLaunch = expectedLaunch;
  const required = [
    [typedLaunch.codex, "codex", 493],
    [typedLaunch.python, "python", 493],
    [MANIFEST_FILE, "control", 420],
    ...(normalization === null
      ? []
      : [[NORMALIZATION_PACKAGE_PATH, "control", 420] as const]),
    ...(runtimeSourcesIdentity === null
      ? []
      : [[RUNTIME_SOURCES_FILE, "control", 420] as const]),
  ] as const;
  for (const [requiredPath, component, mode] of required) {
    const entry = inventory.find((candidate) => candidate.path === requiredPath);
    if (!entry || entry.component !== component || entry.mode !== mode) {
      semanticError("$.inventory");
    }
  }
  if (normalization !== null) {
    const receiptEntry = inventory.find(
      (candidate) => candidate.path === NORMALIZATION_PACKAGE_PATH,
    );
    if (
      receiptEntry?.size !== NORMALIZATION_SIZE ||
      receiptEntry.sha256 !== NORMALIZATION_SHA256
    ) {
      semanticError("$.inventory");
    }
  }
  if (runtimeSourcesIdentity !== null) {
    const provenanceEntry = inventory.find(
      (candidate) => candidate.path === RUNTIME_SOURCES_FILE,
    );
    if (
      provenanceEntry?.size !== RUNTIME_SOURCES_SIZE ||
      provenanceEntry.sha256 !== RUNTIME_SOURCES_SHA256
    ) {
      semanticError("$.inventory");
    }
  }
  for (const component of ["codex", "python"] as const) {
    const source = sourceEntries.get(component);
    if (!source) {
      semanticError(`$.sources.${component}`);
    }
    const relativeEntrypoint = source.archive.entrypoint.slice(
      source.archive.payload_root.length + 1,
    );
    const launchPath =
      component === "codex" ? typedLaunch.codex : typedLaunch.python;
    const outputPrefix = component === "codex" ? codexPrefix : pythonPrefix;
    if (launchPath !== `${outputPrefix}${relativeEntrypoint}`) {
      semanticError("$.launch");
    }
  }
  const forgeInventory = inventory.filter((entry) => entry.component === "forge");
  if (forge.inventory_sha256 !== sha256(canonicalJsonBytes(forgeInventory))) {
    semanticError("$.sources.forge.inventory_sha256");
  }

  return {
    assembly_kind: root.assembly_kind,
    format: PACKAGE_MANIFEST_FORMAT,
    format_version: 1,
    inventory,
    launch: typedLaunch,
    open_blocker_codes: [...blockerCodes],
    redistribution_status: "blocked",
    release_ready: false,
    schema_id: PACKAGE_MANIFEST_SCHEMA,
    source_date_epoch: root.source_date_epoch,
    sources: {
      codex: sourceEntries.get("codex")!,
      forge: {
        inventory_sha256: forge.inventory_sha256,
        version: "0.7.0",
      },
      python: {
        archive: pythonSource.archive,
        normalization: normalizationIdentity,
        version: pythonSource.version,
      },
      runtime_sources: runtimeSourcesIdentity,
      runtime_sources_sha256: sources.runtime_sources_sha256,
    },
    target_id: target,
  };
}

function canonicalLinuxPbsArchiveIdentity(): RuntimeSourceIdentity["archive"] {
  return {
    entrypoint: "python/bin/python3",
    filename: LINUX_PBS_ARCHIVE_FILENAME,
    payload_root: "python",
    sha256: LINUX_PBS_ARCHIVE_SHA256,
    size: LINUX_PBS_ARCHIVE_SIZE,
  };
}

function canonicalNormalizationIdentity(): RuntimeNormalizationIdentity {
  return {
    archive_sha256: LINUX_PBS_ARCHIVE_SHA256,
    format: NORMALIZATION_FORMAT,
    format_version: 1,
    path: NORMALIZATION_PACKAGE_PATH,
    sha256: NORMALIZATION_SHA256,
    size: NORMALIZATION_SIZE,
  };
}

function canonicalRuntimeSourcesIdentity(): RuntimeSourcesIdentity {
  return {
    format: RUNTIME_SOURCES_FORMAT,
    format_version: 1,
    path: RUNTIME_SOURCES_FILE,
    sha256: RUNTIME_SOURCES_SHA256,
    size: RUNTIME_SOURCES_SIZE,
  };
}

function validateRuntimeSourcesIdentity(value: unknown): RuntimeSourcesIdentity {
  const identity = requireExactRecord(
    value,
    ["format", "format_version", "path", "sha256", "size"],
    "$.sources.runtime_sources",
  );
  const expected = canonicalRuntimeSourcesIdentity();
  if (!isDeepStrictEqual(identity, expected)) {
    semanticError("$.sources.runtime_sources");
  }
  return expected;
}

function validateRuntimeSourcesProvenance(
  bytes: Buffer,
  target: RuntimeTarget,
): {
  codex: RuntimeSourceIdentity["archive"];
  python: RuntimeSourceIdentity["archive"];
} {
  if (
    bytes.length !== RUNTIME_SOURCES_SIZE ||
    sha256(bytes) !== RUNTIME_SOURCES_SHA256
  ) {
    semanticError("$.sources.runtime_sources");
  }
  let value: unknown;
  try {
    value = parseStrictUtf8Json(bytes);
  } catch {
    semanticError("$.sources.runtime_sources");
  }
  const root = requireExactRecord(
    value,
    [
      "blockers",
      "codex",
      "format",
      "format_version",
      "python",
      "redistribution",
      "schema_id",
      "supported_targets",
    ],
    "$.sources.runtime_sources",
  );
  const codex = looseRecord(root.codex, "$.sources.runtime_sources.codex");
  const python = looseRecord(root.python, "$.sources.runtime_sources.python");
  const redistribution = looseRecord(
    root.redistribution,
    "$.sources.runtime_sources.redistribution",
  );
  const codexTarget = sourceTarget(codex.targets, target);
  const pythonTarget = sourceTarget(python.targets, target);
  const codexArchive = looseRecord(
    codexTarget.archive,
    "$.sources.runtime_sources.codex.targets.archive",
  );
  const pythonArchive = looseRecord(
    pythonTarget.runtime_archive,
    "$.sources.runtime_sources.python.targets.runtime_archive",
  );
  const expected = verifiedSourceProfile(target);
  if (
    root.format !== RUNTIME_SOURCES_FORMAT ||
    root.format_version !== 1 ||
    root.schema_id !== RUNTIME_SOURCES_SCHEMA ||
    !isDeepStrictEqual(root.supported_targets, ["linux-x64", "win32-x64"]) ||
    codex.package !== "@openai/codex" ||
    codex.version !== CODEX_VERSION ||
    codex.commit !== "5d1fbf26c43abc65a203928b2e31561cb039e06d" ||
    codexTarget.sri !== expected.codexSri ||
    codexTarget.payload_root !== expected.codex.payload_root ||
    codexTarget.entrypoint !== expected.codex.entrypoint ||
    !isDeepStrictEqual(codexArchive, expected.codexArchive) ||
    python.distribution !== "python-build-standalone" ||
    python.python_version !== "3.12.13" ||
    python.release !== "20260718" ||
    python.commit !== "0e4d9c24b72d28573e622518f09b16aef4a33be8" ||
    pythonTarget.payload_root !== expected.python.payload_root ||
    pythonTarget.entrypoint !== expected.python.entrypoint ||
    !isDeepStrictEqual(pythonArchive, expected.pythonArchive) ||
    !isDeepStrictEqual(
      redistribution.open_blocker_codes,
      [...VERIFIED_BLOCKERS],
    )
  ) {
    semanticError("$.sources.runtime_sources");
  }
  return { codex: expected.codex, python: expected.python };
}

function looseRecord(value: unknown, field: string): Record<string, unknown> {
  if (!isRecord(value)) {
    semanticError(field);
  }
  return value;
}

function sourceTarget(value: unknown, target: RuntimeTarget): Record<string, unknown> {
  if (!Array.isArray(value)) {
    semanticError("$.sources.runtime_sources");
  }
  const candidates = value as unknown[];
  const selected = candidates.find(
    (candidate) => isRecord(candidate) && candidate.target_id === target,
  );
  if (!isRecord(selected)) {
    semanticError("$.sources.runtime_sources");
  }
  return selected;
}

function verifiedSourceProfile(target: RuntimeTarget): {
  codex: RuntimeSourceIdentity["archive"];
  codexArchive: Record<string, unknown>;
  codexSri: string;
  python: RuntimeSourceIdentity["archive"];
  pythonArchive: Record<string, unknown>;
} {
  if (target === "linux-x64") {
    return {
      codex: {
        entrypoint: "package/vendor/x86_64-unknown-linux-musl/bin/codex",
        filename: "codex-0.144.6-linux-x64.tgz",
        payload_root: "package/vendor/x86_64-unknown-linux-musl",
        sha256: "b6752eb2e8c10e6fcc96ac5c1c8ad8342cdb9a74504fb84686addf081a7d2868",
        size: 131_212_687,
      },
      codexArchive: {
        filename: "codex-0.144.6-linux-x64.tgz",
        sha256: "b6752eb2e8c10e6fcc96ac5c1c8ad8342cdb9a74504fb84686addf081a7d2868",
        size: 131_212_687,
        url: "https://registry.npmjs.org/@openai/codex/-/codex-0.144.6-linux-x64.tgz",
      },
      codexSri:
        "sha512-4E7EnzCg0OnBxCyYnwJ+qnZwWHYe0YScr5ucKWbngE9u4+0XrpWELqq2Kn9jl5GZK8MDjU7PrJwFIwusHOHjuw==",
      python: canonicalLinuxPbsArchiveIdentity(),
      pythonArchive: {
        filename: LINUX_PBS_ARCHIVE_FILENAME,
        sha256: LINUX_PBS_ARCHIVE_SHA256,
        size: LINUX_PBS_ARCHIVE_SIZE,
        url: "https://github.com/astral-sh/python-build-standalone/releases/download/20260718/cpython-3.12.13%2B20260718-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz",
      },
    };
  }
  return {
    codex: {
      entrypoint: "package/vendor/x86_64-pc-windows-msvc/bin/codex.exe",
      filename: "codex-0.144.6-win32-x64.tgz",
      payload_root: "package/vendor/x86_64-pc-windows-msvc",
      sha256: "e04afbe9841be306455d075ad414993a946c94a399e55d7f9ec223f734cd4101",
      size: 145_169_047,
    },
    codexArchive: {
      filename: "codex-0.144.6-win32-x64.tgz",
      sha256: "e04afbe9841be306455d075ad414993a946c94a399e55d7f9ec223f734cd4101",
      size: 145_169_047,
      url: "https://registry.npmjs.org/@openai/codex/-/codex-0.144.6-win32-x64.tgz",
    },
    codexSri:
      "sha512-dN39VnjEthKz5io1RNWwZDtErdSn07nW3pGUgvlA6DMxgm/nuGaIAZO/sG/Hgxq/x5j9HteAENfrFgVkpZ0lFg==",
    python: {
      entrypoint: "python/python.exe",
      filename:
        "cpython-3.12.13+20260718-x86_64-pc-windows-msvc-install_only_stripped.tar.gz",
      payload_root: "python",
      sha256: "0d422a1439ec308e03f47df551bc30f5994727c456e414b026d202bcda9b7c1c",
      size: 21_932_298,
    },
    pythonArchive: {
      filename:
        "cpython-3.12.13+20260718-x86_64-pc-windows-msvc-install_only_stripped.tar.gz",
      sha256: "0d422a1439ec308e03f47df551bc30f5994727c456e414b026d202bcda9b7c1c",
      size: 21_932_298,
      url: "https://github.com/astral-sh/python-build-standalone/releases/download/20260718/cpython-3.12.13%2B20260718-x86_64-pc-windows-msvc-install_only_stripped.tar.gz",
    },
  };
}

function validateNormalizationIdentity(value: unknown): RuntimeNormalizationIdentity {
  const identity = requireExactRecord(
    value,
    ["archive_sha256", "format", "format_version", "path", "sha256", "size"],
    "$.sources.python.normalization",
  );
  const expected = canonicalNormalizationIdentity();
  if (!isDeepStrictEqual(identity, expected)) {
    semanticError("$.sources.python.normalization");
  }
  return expected;
}

function validateArchiveNormalization(
  bytes: Buffer,
  identity: RuntimeNormalizationIdentity,
): RuntimeArchiveNormalization {
  if (
    bytes.length !== identity.size ||
    sha256(bytes) !== identity.sha256
  ) {
    semanticError("$.sources.python.normalization");
  }
  let value: unknown;
  try {
    value = parseStrictUtf8Json(bytes);
  } catch {
    semanticError("$.sources.python.normalization");
  }
  if (!bytes.equals(canonicalJsonBytes(value))) {
    semanticError("$.sources.python.normalization");
  }
  const root = requireExactRecord(
    value,
    [
      "archive_sha256",
      "casefold_directories",
      "casefold_files",
      "component",
      "files",
      "format",
      "format_version",
      "max_symlink_depth",
      "output_bytes",
      "output_file_count",
      "payload_root",
      "policy",
      "regular_file_count",
      "relative_symlink_count",
      "source_file_count",
      "target_id",
    ],
    "$.sources.python.normalization",
  );
  if (
    root.archive_sha256 !== LINUX_PBS_ARCHIVE_SHA256 ||
    root.component !== "python" ||
    root.format !== NORMALIZATION_FORMAT ||
    root.format_version !== 1 ||
    root.max_symlink_depth !== 1 ||
    root.output_bytes !== NORMALIZATION_OUTPUT_BYTES ||
    root.output_file_count !== NORMALIZATION_OUTPUT_FILES ||
    root.payload_root !== "python" ||
    root.policy !== "materialize_relative_symlinks_preserve_case_sensitive_paths_v1" ||
    root.regular_file_count !== NORMALIZATION_REGULAR_FILES ||
    root.relative_symlink_count !== NORMALIZATION_SYMLINKS ||
    root.source_file_count !== NORMALIZATION_OUTPUT_FILES ||
    root.target_id !== "linux-x64" ||
    !Array.isArray(root.files) ||
    !Array.isArray(root.casefold_directories) ||
    !Array.isArray(root.casefold_files)
  ) {
    semanticError("$.sources.python.normalization");
  }

  const files: NormalizationFile[] = [];
  const bySource = new Map<string, NormalizationFile>();
  let regularFiles = 0;
  let symlinks = 0;
  let outputBytes = 0;
  for (const [index, candidate] of root.files.entries()) {
    const item = requireExactRecord(
      candidate,
      ["link", "mode", "sha256", "size", "source", "source_kind", "target"],
      `$.sources.python.normalization.files[${index}]`,
    );
    if (
      !isPortableResourcePath(item.source) ||
      !isPortableResourcePath(item.target) ||
      (item.mode !== 420 && item.mode !== 493) ||
      !isSha256(item.sha256) ||
      !isBoundedInteger(item.size, 0, MAX_ARCHIVE_MEMBER_BYTES) ||
      (item.source_kind !== "regular" && item.source_kind !== "symlink") ||
      bySource.has(item.source)
    ) {
      semanticError("$.sources.python.normalization.files");
    }
    if (
      (item.source_kind === "regular" &&
        (item.link !== null || item.target !== item.source)) ||
      (item.source_kind === "symlink" &&
        (typeof item.link !== "string" || item.link.length === 0))
    ) {
      semanticError("$.sources.python.normalization.files");
    }
    const parsed: NormalizationFile = {
      link: item.link as string | null,
      mode: item.mode,
      sha256: item.sha256,
      size: item.size,
      source: item.source,
      source_kind: item.source_kind,
      target: item.target,
    };
    files.push(parsed);
    bySource.set(parsed.source, parsed);
    outputBytes += parsed.size;
    if (parsed.source_kind === "regular") {
      regularFiles += 1;
    } else {
      symlinks += 1;
    }
  }
  if (
    files.length !== NORMALIZATION_OUTPUT_FILES ||
    files.some(
      (item, index) =>
        index > 0 && compareUtf8(files[index - 1].source, item.source) >= 0,
    ) ||
    regularFiles !== NORMALIZATION_REGULAR_FILES ||
    symlinks !== NORMALIZATION_SYMLINKS ||
    outputBytes !== NORMALIZATION_OUTPUT_BYTES
  ) {
    semanticError("$.sources.python.normalization.files");
  }

  let maxDepth = 0;
  for (const item of files) {
    if (item.source_kind !== "symlink") {
      continue;
    }
    let current = item.source;
    const visited = new Set<string>();
    let depth = 0;
    while (true) {
      const currentItem = bySource.get(current);
      if (!currentItem) {
        semanticError("$.sources.python.normalization.files");
      }
      if (currentItem.source_kind === "regular") {
        if (
          current !== item.target ||
          currentItem.mode !== item.mode ||
          currentItem.size !== item.size ||
          currentItem.sha256 !== item.sha256
        ) {
          semanticError("$.sources.python.normalization.files");
        }
        break;
      }
      if (visited.has(current) || depth >= MAX_PATH_DEPTH) {
        semanticError("$.sources.python.normalization.files");
      }
      visited.add(current);
      current = relativeSymlinkTarget(current, currentItem.link);
      depth += 1;
    }
    maxDepth = Math.max(maxDepth, depth);
  }
  if (maxDepth !== 1) {
    semanticError("$.sources.python.normalization.files");
  }

  const casefoldDirectories = validateCasefoldDirectories(
    root.casefold_directories,
    files,
  );
  const casefoldFiles = validateCasefoldFiles(root.casefold_files, bySource);
  return {
    casefold_directories: casefoldDirectories,
    casefold_files: casefoldFiles,
    files,
  };
}

function validateCasefoldDirectories(
  value: unknown[],
  files: NormalizationFile[],
): CasefoldDirectoryPair[] {
  if (value.length !== 8) {
    semanticError("$.sources.python.normalization.casefold_directories");
  }
  const pairs = value.map((candidate, index): CasefoldDirectoryPair => {
    const pair = requireExactRecord(
      candidate,
      ["first", "second"],
      `$.sources.python.normalization.casefold_directories[${index}]`,
    );
    if (
      !isPortableResourcePath(pair.first) ||
      !isPortableResourcePath(pair.second) ||
      pair.first === pair.second ||
      portablePathAlias(pair.first) !== portablePathAlias(pair.second) ||
      compareUtf8(pair.first, pair.second) >= 0
    ) {
      semanticError("$.sources.python.normalization.casefold_directories");
    }
    return { first: pair.first, second: pair.second };
  });
  if (
    pairs.some(
      (pair, index) =>
        index > 0 && compareUtf8(pairs[index - 1].first, pair.first) >= 0,
    )
  ) {
    semanticError("$.sources.python.normalization.casefold_directories");
  }
  const observed = observedCasefoldDirectories(files.map((item) => item.source));
  if (!isDeepStrictEqual(pairs, observed)) {
    semanticError("$.sources.python.normalization.casefold_directories");
  }
  return pairs;
}

function observedCasefoldDirectories(paths: string[]): CasefoldDirectoryPair[] {
  const prefixes = new Map<string, Set<string>>();
  for (const filename of paths) {
    const parts = filename.split("/").slice(0, -1);
    for (let count = 1; count <= parts.length; count += 1) {
      const prefix = parts.slice(0, count).join("/");
      const alias = portablePathAlias(prefix);
      const values = prefixes.get(alias) ?? new Set<string>();
      values.add(prefix);
      prefixes.set(alias, values);
    }
  }
  const result: CasefoldDirectoryPair[] = [];
  for (const values of prefixes.values()) {
    if (values.size === 1) {
      continue;
    }
    if (values.size !== 2) {
      semanticError("$.sources.python.normalization.casefold_directories");
    }
    const [first, second] = [...values].toSorted(compareUtf8);
    result.push({ first, second });
  }
  return result.toSorted((left, right) => compareUtf8(left.first, right.first));
}

function validateCasefoldFiles(
  value: unknown[],
  bySource: Map<string, NormalizationFile>,
): CasefoldFilePair[] {
  if (value.length !== 25) {
    semanticError("$.sources.python.normalization.casefold_files");
  }
  const pairs = value.map((candidate, index): CasefoldFilePair => {
    const pair = requireExactRecord(
      candidate,
      [
        "first_mode",
        "first_sha256",
        "first_size",
        "first_source",
        "first_target",
        "second_mode",
        "second_sha256",
        "second_size",
        "second_source",
        "second_target",
      ],
      `$.sources.python.normalization.casefold_files[${index}]`,
    );
    const first = bySource.get(
      typeof pair.first_source === "string" ? pair.first_source : "",
    );
    const second = bySource.get(
      typeof pair.second_source === "string" ? pair.second_source : "",
    );
    if (
      !first ||
      !second ||
      pair.first_source === pair.second_source ||
      compareUtf8(pair.first_source as string, pair.second_source as string) >= 0 ||
      portablePathAlias(pair.first_source as string) !==
        portablePathAlias(pair.second_source as string) ||
      pair.first_target !== first.target ||
      pair.second_target !== second.target ||
      pair.first_mode !== first.mode ||
      pair.second_mode !== second.mode ||
      pair.first_size !== first.size ||
      pair.second_size !== second.size ||
      pair.first_sha256 !== first.sha256 ||
      pair.second_sha256 !== second.sha256
    ) {
      semanticError("$.sources.python.normalization.casefold_files");
    }
    return {
      first_mode: first.mode,
      first_sha256: first.sha256,
      first_size: first.size,
      first_source: first.source,
      first_target: first.target,
      second_mode: second.mode,
      second_sha256: second.sha256,
      second_size: second.size,
      second_source: second.source,
      second_target: second.target,
    };
  });
  if (
    pairs.some(
      (pair, index) =>
        index > 0 &&
        compareUtf8(pairs[index - 1].first_source, pair.first_source) >= 0,
    )
  ) {
    semanticError("$.sources.python.normalization.casefold_files");
  }
  const observed = new Map<string, Set<string>>();
  for (const source of bySource.keys()) {
    const alias = portablePathAlias(source);
    const values = observed.get(alias) ?? new Set<string>();
    values.add(source);
    observed.set(alias, values);
  }
  const expected = new Map(
    pairs.map((pair) => [
      portablePathAlias(pair.first_source),
      new Set([pair.first_source, pair.second_source]),
    ]),
  );
  const collisions = new Map(
    [...observed].filter(([, values]) => values.size > 1),
  );
  requireExactAliasGroups(
    collisions,
    expected,
    "$.sources.python.normalization.casefold_files",
  );
  return pairs;
}

function relativeSymlinkTarget(source: string, link: string | null): string {
  if (
    typeof link !== "string" ||
    link.length === 0 ||
    link !== link.normalize("NFC") ||
    Buffer.byteLength(link, "utf8") > MAX_PATH_BYTES ||
    link.includes("\\") ||
    link.startsWith("/") ||
    /^[A-Za-z]:/u.test(link) ||
    containsControl(link) ||
    link.split("/").some((component) => component.length === 0) ||
    link.split("/").length > MAX_PATH_DEPTH
  ) {
    semanticError("$.sources.python.normalization.files");
  }
  const target = path.posix.normalize(path.posix.join(path.posix.dirname(source), link));
  if (
    target === "." ||
    target === ".." ||
    target.startsWith("../") ||
    !isPortableResourcePath(target)
  ) {
    semanticError("$.sources.python.normalization.files");
  }
  return target;
}

function normalizationCasefoldFiles(
  normalization: RuntimeArchiveNormalization,
  target: RuntimeTarget,
): Map<string, Set<string>> {
  const prefix = `runtime/python/${target}/`;
  return new Map(
    normalization.casefold_files.map((pair) => {
      const first = `${prefix}${pair.first_source}`;
      const second = `${prefix}${pair.second_source}`;
      return [portablePathAlias(first), new Set([first, second])];
    }),
  );
}

function normalizationCasefoldDirectories(
  normalization: RuntimeArchiveNormalization,
  target: RuntimeTarget,
): Map<string, Set<string>> {
  const prefix = `runtime/python/${target}/`;
  return new Map(
    normalization.casefold_directories.map((pair) => {
      const first = `${prefix}${pair.first}`;
      const second = `${prefix}${pair.second}`;
      return [portablePathAlias(first), new Set([first, second])];
    }),
  );
}

function validateInventoryDirectoryAliases(
  inventory: RuntimePackageInventoryEntry[],
  normalization: RuntimeArchiveNormalization | null,
  target: RuntimeTarget,
): void {
  const aliases = new Map<string, Set<string>>();
  const directories = new Set<string>();
  const fileAliases = new Set(
    inventory.map((entry) => portablePathAlias(entry.path)),
  );
  if (inventory.length + 1 > MAX_OUTPUT_NODES) {
    semanticError("$.inventory");
  }
  for (const entry of inventory) {
    const parts = entry.path.split("/").slice(0, -1);
    for (let count = 1; count <= parts.length; count += 1) {
      const directory = parts.slice(0, count).join("/");
      if (!directories.has(directory)) {
        if (
          directories.size >= MAX_OUTPUT_DIRECTORIES ||
          inventory.length + directories.size + 2 > MAX_OUTPUT_NODES
        ) {
          semanticError("$.inventory");
        }
        directories.add(directory);
      }
      const alias = portablePathAlias(directory);
      const values = aliases.get(alias) ?? new Set<string>();
      values.add(directory);
      aliases.set(alias, values);
    }
  }
  if ([...aliases.keys()].some((alias) => fileAliases.has(alias))) {
    semanticError("$.inventory");
  }
  const collisions = new Map(
    [...aliases].filter(([, values]) => values.size > 1),
  );
  const allowed =
    normalization === null
      ? new Map<string, Set<string>>()
      : normalizationCasefoldDirectories(normalization, target);
  requireExactAliasGroups(collisions, allowed, "$.inventory");
}

function requireExactAliasGroups(
  observed: Map<string, Set<string>>,
  expected: Map<string, Set<string>>,
  field: string,
): void {
  if (observed.size !== expected.size) {
    semanticError(field);
  }
  for (const [alias, expectedValues] of expected) {
    const actual = observed.get(alias);
    if (
      actual === undefined ||
      actual.size !== expectedValues.size ||
      !isSubset(actual, expectedValues)
    ) {
      semanticError(field);
    }
  }
}

function isSubset(left: Set<string>, right: Set<string>): boolean {
  return [...left].every((value) => right.has(value));
}

function manifestUsesLinuxPbs(value: unknown): boolean {
  if (!isRecord(value) || value.target_id !== "linux-x64") {
    return false;
  }
  const sources = value.sources;
  if (!isRecord(sources) || !isRecord(sources.python)) {
    return false;
  }
  const archive = sources.python.archive;
  return isRecord(archive) && archive.sha256 === LINUX_PBS_ARCHIVE_SHA256;
}

function inventoryEntry(
  manifest: RuntimePackageManifest,
  relativePath: string,
  component: RuntimeComponent,
): RuntimePackageInventoryEntry {
  const entry = manifest.inventory.find((candidate) => candidate.path === relativePath);
  if (!entry || entry.component !== component) {
    throw new Error("Packaged Studio launch path is not authorized by its package inventory");
  }
  return entry;
}

async function resolvePackagedExecutable(
  resourcesPath: string,
  relativePath: string,
  platform: NodeJS.Platform,
  entry: RuntimePackageInventoryEntry,
): Promise<string> {
  const resource = await readPinnedResourceFile(
    resourcesPath,
    relativePath,
    MAX_ARCHIVE_MEMBER_BYTES,
    "Studio executable",
  );
  if (
    resource.bytes.length !== entry.size ||
    sha256(resource.bytes) !== entry.sha256 ||
    (platform !== "win32" && (resource.mode & 0o777) !== entry.mode)
  ) {
    throw new Error("Packaged Studio executable does not match its package inventory");
  }
  return resource.filename;
}

async function assertDevelopmentInterpreter(executable: string): Promise<void> {
  await assertDevelopmentExecutable(executable, "Python interpreter");
}

async function assertDevelopmentExecutable(executable: string, label: string): Promise<string> {
  const stat = await lstat(executable);
  if (!stat.isFile() && !stat.isSymbolicLink()) {
    throw new Error(`Configured development ${label} is not a file`);
  }
  const target = await realpath(executable);
  const targetStat = await lstat(target);
  if (!targetStat.isFile()) {
    throw new Error(`Configured development ${label} does not resolve to a regular file`);
  }
  return target;
}

async function verifyPackagedProtocol(
  resourcesPath: string,
  relativePath: string,
  entry: RuntimePackageInventoryEntry,
): Promise<void> {
  const resource = await readPinnedResourceFile(
    resourcesPath,
    relativePath,
    MANIFEST_MAX_BYTES,
    "Codex protocol manifest",
  );
  if (
    resource.bytes.length !== entry.size ||
    sha256(resource.bytes) !== entry.sha256
  ) {
    throw new Error("Packaged Codex protocol manifest does not match its package inventory");
  }
  let value: unknown;
  try {
    value = parseStrictUtf8Json(resource.bytes);
  } catch (error) {
    throw new Error("Packaged Codex protocol manifest is not valid UTF-8 JSON", { cause: error });
  }
  if (
    !isRecord(value) ||
    !hasExactKeys(value, [
      "artifacts",
      "codex_cli_version",
      "commands",
      "experimental",
      "format",
      "format_version",
      "mcp_protocol_version",
    ]) ||
    value.format !== "rpg-world-forge.codex_app_server_protocol_provenance" ||
    value.format_version !== 1 ||
    value.codex_cli_version !== CODEX_VERSION ||
    value.experimental !== false ||
    value.mcp_protocol_version !== "2025-11-25" ||
    !isRecord(value.artifacts) ||
    !hasExactKeys(value.artifacts, ["json_schema", "typescript"]) ||
    !isProtocolArtifact(value.artifacts.typescript, 598, 322_075, CODEX_TYPESCRIPT_SHA256) ||
    !isProtocolArtifact(value.artifacts.json_schema, 267, 2_719_809, CODEX_JSON_SCHEMA_SHA256)
  ) {
    throw new Error("Packaged Codex protocol provenance is incompatible");
  }
}

function isProtocolArtifact(
  value: unknown,
  files: number,
  bytes: number,
  sha256: string,
): boolean {
  return (
    isRecord(value) &&
    hasExactKeys(value, ["bytes", "files", "sha256"]) &&
    value.files === files &&
    value.bytes === bytes &&
    value.sha256 === sha256
  );
}

async function readPinnedResourceFile(
  resourcesPath: string,
  relativePath: string,
  maxBytes: number,
  label: string,
): Promise<PinnedResourceFile> {
  if (!isPortableResourcePath(relativePath)) {
    throw new Error(`Packaged ${label} path is not portable`);
  }
  const root = await realpath(resourcesPath);
  const filename = path.resolve(root, ...relativePath.split("/"));
  if (!isWithin(root, filename)) {
    throw new Error(`Packaged ${label} escapes the resources directory`);
  }
  const before = await lstat(filename);
  if (
    !before.isFile() ||
    before.isSymbolicLink() ||
    before.nlink !== 1 ||
    before.size > maxBytes
  ) {
    throw new Error(`Packaged ${label} must be a bounded standalone regular file`);
  }
  const canonical = await realpath(filename);
  if (canonical !== filename || !isWithin(root, canonical)) {
    throw new Error(`Packaged ${label} identity is not anchored in resources`);
  }

  const flags = constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0);
  let handle;
  try {
    handle = await open(filename, flags);
  } catch (error) {
    throw new Error(`Packaged ${label} could not be opened without following links`, {
      cause: error,
    });
  }
  let bytes: Buffer;
  try {
    const opened = await handle.stat();
    if (
      !opened.isFile() ||
      opened.nlink !== 1 ||
      opened.size > maxBytes ||
      !sameFileIdentity(before, opened)
    ) {
      throw new Error(`Packaged ${label} identity changed before its pinned read`);
    }
    bytes = await handle.readFile();
    const after = await handle.stat();
    if (
      bytes.length !== opened.size ||
      !sameFileIdentity(opened, after)
    ) {
      throw new Error(`Packaged ${label} identity changed during its pinned read`);
    }
  } finally {
    await handle.close();
  }
  const final = await lstat(filename);
  if (
    final.isSymbolicLink() ||
    !sameFileIdentity(before, final) ||
    (await realpath(filename)) !== filename
  ) {
    throw new Error(`Packaged ${label} identity changed after its pinned read`);
  }
  return { bytes, filename, mode: before.mode };
}

function sameFileIdentity(left: Stats, right: Stats): boolean {
  return (
    left.dev === right.dev &&
    left.ino === right.ino &&
    left.mode === right.mode &&
    left.nlink === right.nlink &&
    left.size === right.size &&
    left.mtimeMs === right.mtimeMs &&
    left.ctimeMs === right.ctimeMs
  );
}

function canonicalJsonBytes(value: unknown): Buffer {
  return Buffer.from(`${canonicalJson(value)}\n`, "utf8");
}

function canonicalJson(value: unknown): string {
  if (
    value === null ||
    typeof value === "boolean" ||
    typeof value === "string"
  ) {
    return JSON.stringify(value);
  }
  if (typeof value === "number" && Number.isSafeInteger(value)) {
    return JSON.stringify(value);
  }
  if (Array.isArray(value)) {
    return `[${value.map((item) => canonicalJson(item)).join(",")}]`;
  }
  if (isRecord(value)) {
    return `{${Object.keys(value)
      .toSorted()
      .map((key) => `${JSON.stringify(key)}:${canonicalJson(value[key])}`)
      .join(",")}}`;
  }
  throw new Error("Runtime package manifest contains a non-canonical JSON value");
}

function requireExactRecord(
  value: unknown,
  keys: readonly string[],
  field: string,
): Record<string, unknown> {
  if (!isRecord(value) || !hasExactKeys(value, keys)) {
    semanticError(field);
  }
  return value;
}

function semanticError(field: string): never {
  throw new Error(`Packaged runtime package manifest semantic contract failed at ${field}`);
}

function isBoundedInteger(value: unknown, minimum: number, maximum: number): value is number {
  return Number.isSafeInteger(value) && (value as number) >= minimum && (value as number) <= maximum;
}

function isSha256(value: unknown): value is string {
  return typeof value === "string" && /^[0-9a-f]{64}$/u.test(value);
}

function isBlockerCodes(value: unknown): value is string[] {
  return (
    Array.isArray(value) &&
    value.length > 0 &&
    value.length <= PACKAGE_MANIFEST_MAX_BLOCKERS &&
    value.every(
      (code): code is string =>
        typeof code === "string" &&
        /^[a-z][a-z0-9_]{2,127}$/u.test(code),
    ) &&
    new Set(value).size === value.length
  );
}

function sha256(value: Uint8Array): string {
  return createHash("sha256").update(value).digest("hex");
}

function compareUtf8(left: string, right: string): number {
  return Buffer.compare(Buffer.from(left, "utf8"), Buffer.from(right, "utf8"));
}

function portablePathAlias(value: string): string {
  return value
    .split("/")
    .map((component) => component.toLowerCase())
    .join("/");
}

export interface StrictJsonLimits {
  maxBytes: number;
  maxDepth: number;
  maxNodes: number;
}

const DEFAULT_STRICT_JSON_LIMITS: StrictJsonLimits = {
  maxBytes: 2 * 1024 * 1024,
  maxDepth: 128,
  maxNodes: 100_000,
};
const MAX_JSON_NUMBER_TOKEN_LENGTH = 128;

export function parseStrictUtf8Json(
  bytes: Buffer,
  limits: StrictJsonLimits = DEFAULT_STRICT_JSON_LIMITS,
): unknown {
  if (
    !Number.isSafeInteger(limits.maxBytes) ||
    !Number.isSafeInteger(limits.maxDepth) ||
    !Number.isSafeInteger(limits.maxNodes) ||
    limits.maxBytes < 2 ||
    limits.maxDepth < 0 ||
    limits.maxNodes < 1 ||
    bytes.length > limits.maxBytes
  ) {
    throw new SyntaxError("JSON input exceeds its contract limits");
  }
  const text = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
  return new StrictJsonParser(text, limits).parse();
}

class StrictJsonParser {
  // Parity with Python: every JSON value is one node, keys are not nodes,
  // and the root value is depth zero.
  readonly #text: string;
  readonly #limits: StrictJsonLimits;
  #index = 0;
  #nodes = 0;

  constructor(text: string, limits: StrictJsonLimits) {
    this.#text = text;
    this.#limits = limits;
  }

  parse(): unknown {
    this.#skipWhitespace();
    const value = this.#parseValue(0);
    this.#skipWhitespace();
    if (this.#index !== this.#text.length) {
      throw new SyntaxError("Unexpected data after JSON value");
    }
    return value;
  }

  #parseValue(depth: number): unknown {
    this.#nodes += 1;
    if (depth > this.#limits.maxDepth || this.#nodes > this.#limits.maxNodes) {
      throw new SyntaxError("JSON tree exceeds its contract limits");
    }
    const character = this.#text[this.#index];
    if (character === "{") {
      return this.#parseObject(depth);
    }
    if (character === "[") {
      return this.#parseArray(depth);
    }
    if (character === '"') {
      return this.#parseString();
    }
    if (character === "t") {
      return this.#parseLiteral("true", true);
    }
    if (character === "f") {
      return this.#parseLiteral("false", false);
    }
    if (character === "n") {
      return this.#parseLiteral("null", null);
    }
    return this.#parseNumber();
  }

  #parseObject(depth: number): Record<string, unknown> {
    this.#index += 1;
    this.#skipWhitespace();
    const result: Record<string, unknown> = {};
    const keys = new Set<string>();
    if (this.#text[this.#index] === "}") {
      this.#index += 1;
      return result;
    }
    while (true) {
      if (this.#text[this.#index] !== '"') {
        throw new SyntaxError("JSON object key must be a string");
      }
      const key = this.#parseString();
      if (keys.has(key)) {
        throw new SyntaxError("Duplicate JSON object key");
      }
      keys.add(key);
      this.#skipWhitespace();
      if (this.#text[this.#index] !== ":") {
        throw new SyntaxError("JSON object key must be followed by a colon");
      }
      this.#index += 1;
      this.#skipWhitespace();
      Object.defineProperty(result, key, {
        configurable: true,
        enumerable: true,
        value: this.#parseValue(depth + 1),
        writable: true,
      });
      this.#skipWhitespace();
      const separator = this.#text[this.#index];
      if (separator === "}") {
        this.#index += 1;
        return result;
      }
      if (separator !== ",") {
        throw new SyntaxError("JSON object members must be comma separated");
      }
      this.#index += 1;
      this.#skipWhitespace();
    }
  }

  #parseArray(depth: number): unknown[] {
    this.#index += 1;
    this.#skipWhitespace();
    const result: unknown[] = [];
    if (this.#text[this.#index] === "]") {
      this.#index += 1;
      return result;
    }
    while (true) {
      result.push(this.#parseValue(depth + 1));
      this.#skipWhitespace();
      const separator = this.#text[this.#index];
      if (separator === "]") {
        this.#index += 1;
        return result;
      }
      if (separator !== ",") {
        throw new SyntaxError("JSON array items must be comma separated");
      }
      this.#index += 1;
      this.#skipWhitespace();
    }
  }

  #parseString(): string {
    const start = this.#index;
    this.#index += 1;
    while (this.#index < this.#text.length) {
      const character = this.#text[this.#index];
      if (character === '"') {
        this.#index += 1;
        return JSON.parse(this.#text.slice(start, this.#index)) as string;
      }
      if (character === "\\") {
        this.#index += 1;
        const escaped = this.#text[this.#index];
        if (escaped === "u") {
          const codePoint = this.#text.slice(this.#index + 1, this.#index + 5);
          if (!/^[0-9a-fA-F]{4}$/u.test(codePoint)) {
            throw new SyntaxError("Invalid JSON Unicode escape");
          }
          this.#index += 5;
          continue;
        }
        if (
          escaped === undefined ||
          !new Set(['"', "\\", "/", "b", "f", "n", "r", "t"]).has(escaped)
        ) {
          throw new SyntaxError("Invalid JSON escape");
        }
        this.#index += 1;
        continue;
      }
      if (character.codePointAt(0)! < 0x20) {
        throw new SyntaxError("Unescaped control character in JSON string");
      }
      this.#index += 1;
    }
    throw new SyntaxError("Unterminated JSON string");
  }

  #parseLiteral<T>(literal: string, value: T): T {
    if (this.#text.slice(this.#index, this.#index + literal.length) !== literal) {
      throw new SyntaxError("Invalid JSON literal");
    }
    this.#index += literal.length;
    return value;
  }

  #parseNumber(): number {
    const match =
      /^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?/u.exec(
        this.#text.slice(this.#index),
      );
    if (!match) {
      throw new SyntaxError("Invalid JSON value");
    }
    if (match[0].length > MAX_JSON_NUMBER_TOKEN_LENGTH) {
      throw new SyntaxError("JSON number exceeds its token limit");
    }
    this.#index += match[0].length;
    const value = Number(match[0]);
    if (!Number.isFinite(value)) {
      throw new SyntaxError("Non-finite JSON number");
    }
    return value;
  }

  #skipWhitespace(): void {
    while (
      this.#text[this.#index] === " " ||
      this.#text[this.#index] === "\t" ||
      this.#text[this.#index] === "\r" ||
      this.#text[this.#index] === "\n"
    ) {
      this.#index += 1;
    }
  }
}

function runtimePlatformKey(
  platform: NodeJS.Platform,
  architecture: string,
): RuntimePlatformKey {
  if (
    (platform !== "linux" && platform !== "win32") ||
    architecture !== "x64"
  ) {
    throw new Error(`Packaged Forge runtime is not defined for ${platform}-${architecture}`);
  }
  return `${platform}_${architecture}`;
}

function fixedPythonSpec(
  executable: string,
  dataDir: string,
  environment: NodeJS.ProcessEnv,
): FixedSpawnSpec {
  return {
    executable,
    args: Object.freeze(["-I", "-m", SERVICE_MODULE, "--data-dir", dataDir]),
    cwd: path.dirname(executable),
    env: Object.freeze(sanitizedChildEnvironment(environment)),
  };
}

function sanitizedChildEnvironment(environment: NodeJS.ProcessEnv): Record<string, string> {
  const allowed = [
    "APPDATA",
    "HOME",
    "LANG",
    "LC_ALL",
    "LOCALAPPDATA",
    "SYSTEMROOT",
    "TEMP",
    "TMP",
    "USERPROFILE",
    "WINDIR",
  ];
  const result: Record<string, string> = {
    PYTHONDONTWRITEBYTECODE: "1",
    PYTHONUTF8: "1",
    PYTHONUNBUFFERED: "1",
  };
  for (const name of allowed) {
    const value = environment[name];
    if (value && !containsControl(value)) {
      result[name] = value;
    }
  }
  return result;
}

function isPortableResourcePath(value: unknown): value is string {
  if (
    typeof value !== "string" ||
    value.length === 0 ||
    value !== value.normalize("NFC") ||
    value.length > MAX_PATH_BYTES ||
    [...value].some((character) => {
      const code = character.codePointAt(0);
      return code === undefined || code < 0x20 || code > 0x7e;
    }) ||
    value.includes("\\") ||
    value.startsWith("/") ||
    containsControl(value)
  ) {
    return false;
  }
  const components = value.split("/");
  return (
    components.length <= MAX_PATH_DEPTH &&
    components.every((component) => {
      const lowered = component.toLowerCase();
      return (
        component.length > 0 &&
        component.length <= 255 &&
        component !== "." &&
        component !== ".." &&
        !component.endsWith(" ") &&
        !component.endsWith(".") &&
        !WINDOWS_RESERVED.has(lowered.split(".", 1)[0] ?? "") &&
        ![...component].some((character) => WINDOWS_FORBIDDEN.has(character))
      );
    })
  );
}

function hasExactKeys(value: Record<string, unknown>, keys: readonly string[]): boolean {
  const actual = Object.keys(value).sort();
  const expected = [...keys].sort();
  return actual.length === expected.length && actual.every((key, index) => key === expected[index]);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function assertAbsoluteSafePath(value: string, label: string): void {
  if (!path.isAbsolute(value) || containsControl(value)) {
    throw new Error(`${label} must be an absolute path without control characters`);
  }
}

function containsControl(value: string): boolean {
  return [...value].some((character) => {
    const code = character.codePointAt(0);
    return code !== undefined && (code <= 0x1f || code === 0x7f);
  });
}

function isWithin(root: string, candidate: string): boolean {
  const relative = path.relative(root, candidate);
  return relative !== "" && !relative.startsWith(`..${path.sep}`) && relative !== ".." && !path.isAbsolute(relative);
}

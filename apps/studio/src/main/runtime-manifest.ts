import { lstat, readFile, realpath } from "node:fs/promises";
import path from "node:path";

import type { FixedSpawnSpec } from "./ndjson-supervisor";

const MANIFEST_FORMAT = "rpg-world-forge.studio_runtime_manifest";
const MANIFEST_FILE = "runtime-manifest.json";
const MANIFEST_MAX_BYTES = 16 * 1024;
const SERVICE_MODULE = "worldforge.studio";
export const FORGE_MCP_MODULE = "worldforge.studio.mcp_server";
export const CODEX_VERSION = "0.144.6";
const CODEX_TYPESCRIPT_SHA256 =
  "125dc17b4ef299a13428dd348c26fbc2c5436cbade0da19c2087217da90931f6";
const CODEX_JSON_SCHEMA_SHA256 =
  "fe9e9099c388569380a5595e75015321be54bf2215885c5e0a0696f6c717b81d";

type RuntimePlatformKey =
  | "linux_x64"
  | "linux_arm64"
  | "win32_x64"
  | "win32_arm64";

interface RuntimeManifest {
  format: typeof MANIFEST_FORMAT;
  version: 2;
  python: {
    service_module: typeof SERVICE_MODULE;
    mcp_module: typeof FORGE_MCP_MODULE;
    linux_x64: string;
    linux_arm64: string;
    win32_x64: string;
    win32_arm64: string;
  };
  codex: {
    version: typeof CODEX_VERSION;
    linux_x64: string;
    linux_arm64: string;
    win32_x64: string;
    win32_arm64: string;
  };
  codex_protocol: {
    version: typeof CODEX_VERSION;
    manifest: string;
  };
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
  const manifest = await loadRuntimeManifest(options.resourcesPath);
  const executable = await resolvePackagedExecutable(
    options.resourcesPath,
    manifest.python[key],
    platform,
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
  const manifest = await loadRuntimeManifest(options.resourcesPath);
  await verifyPackagedProtocol(options.resourcesPath, manifest.codex_protocol.manifest);
  return {
    codexExecutable: await resolvePackagedExecutable(
      options.resourcesPath,
      manifest.codex[key],
      platform,
    ),
    pythonExecutable: await resolvePackagedExecutable(
      options.resourcesPath,
      manifest.python[key],
      platform,
    ),
    version: CODEX_VERSION,
  };
}

async function loadRuntimeManifest(resourcesPath: string): Promise<RuntimeManifest> {
  const manifestPath = path.join(resourcesPath, MANIFEST_FILE);
  const stat = await lstat(manifestPath);
  if (!stat.isFile() || stat.isSymbolicLink() || stat.size > MANIFEST_MAX_BYTES) {
    throw new Error("Packaged Studio runtime manifest must be a small standalone regular file");
  }
  let parsed: unknown;
  try {
    parsed = parseStrictUtf8Json(await readFile(manifestPath));
  } catch (error) {
    throw new Error("Packaged Studio runtime manifest is not valid UTF-8 JSON", { cause: error });
  }
  if (
    !isRecord(parsed) ||
    !hasExactKeys(parsed, ["codex", "codex_protocol", "format", "python", "version"])
  ) {
    throw new Error("Packaged Studio runtime manifest has unknown or missing fields");
  }
  if (
    parsed.format !== MANIFEST_FORMAT ||
    parsed.version !== 2 ||
    !isRecord(parsed.python) ||
    !isRecord(parsed.codex) ||
    !isRecord(parsed.codex_protocol)
  ) {
    throw new Error("Packaged Studio runtime manifest format or version is unsupported");
  }
  const platformKeys: RuntimePlatformKey[] = [
    "linux_x64",
    "linux_arm64",
    "win32_x64",
    "win32_arm64",
  ];
  if (
    !hasExactKeys(parsed.python, ["service_module", "mcp_module", ...platformKeys]) ||
    !hasExactKeys(parsed.codex, ["version", ...platformKeys]) ||
    !hasExactKeys(parsed.codex_protocol, ["version", "manifest"])
  ) {
    throw new Error("Packaged Studio runtime manifest sections are not closed");
  }
  if (
    parsed.python.service_module !== SERVICE_MODULE ||
    parsed.python.mcp_module !== FORGE_MCP_MODULE ||
    parsed.codex.version !== CODEX_VERSION ||
    parsed.codex_protocol.version !== CODEX_VERSION
  ) {
    throw new Error("Packaged Studio runtime identities are unsupported");
  }
  for (const key of platformKeys) {
    if (!isPortableResourcePath(parsed.python[key]) || !isPortableResourcePath(parsed.codex[key])) {
      throw new Error(`Packaged Studio ${key} runtime path is not portable`);
    }
  }
  if (!isPortableResourcePath(parsed.codex_protocol.manifest)) {
    throw new Error("Packaged Codex protocol manifest path is not portable");
  }
  return parsed as unknown as RuntimeManifest;
}

async function resolvePackagedExecutable(
  resourcesPath: string,
  relativePath: string,
  platform: NodeJS.Platform,
): Promise<string> {
  const root = await realpath(resourcesPath);
  const candidate = path.resolve(root, ...relativePath.split("/"));
  if (!isWithin(root, candidate)) {
    throw new Error("Packaged Studio executable escapes the resources directory");
  }
  const stat = await lstat(candidate);
  if (!stat.isFile() || stat.isSymbolicLink()) {
    throw new Error("Packaged Studio executable must be a standalone regular file");
  }
  const canonical = await realpath(candidate);
  if (!isWithin(root, canonical) || canonical !== candidate) {
    throw new Error("Packaged Studio executable identity is not anchored in resources");
  }
  if (platform !== "win32" && (stat.mode & 0o111) === 0) {
    throw new Error("Packaged Studio runtime is not executable");
  }
  return candidate;
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

async function verifyPackagedProtocol(resourcesPath: string, relativePath: string): Promise<void> {
  const root = await realpath(resourcesPath);
  const filename = path.resolve(root, ...relativePath.split("/"));
  if (!isWithin(root, filename)) {
    throw new Error("Packaged Codex protocol manifest escapes resources");
  }
  const info = await lstat(filename);
  if (!info.isFile() || info.isSymbolicLink() || info.size > MANIFEST_MAX_BYTES) {
    throw new Error("Packaged Codex protocol manifest is not a small regular file");
  }
  const canonical = await realpath(filename);
  if (canonical !== filename || !isWithin(root, canonical)) {
    throw new Error("Packaged Codex protocol manifest identity is not anchored in resources");
  }
  let value: unknown;
  try {
    value = parseStrictUtf8Json(await readFile(filename));
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

function parseStrictUtf8Json(bytes: Buffer): unknown {
  return JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes));
}

function runtimePlatformKey(
  platform: NodeJS.Platform,
  architecture: string,
): RuntimePlatformKey {
  if (
    (platform !== "linux" && platform !== "win32") ||
    (architecture !== "x64" && architecture !== "arm64")
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
    value.includes("\\") ||
    value.startsWith("/") ||
    containsControl(value)
  ) {
    return false;
  }
  const components = value.split("/");
  return components.every(
    (component) => component.length > 0 && component !== "." && component !== "..",
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

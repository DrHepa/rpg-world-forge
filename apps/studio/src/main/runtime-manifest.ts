import { lstat, readFile, realpath } from "node:fs/promises";
import path from "node:path";

import type { FixedSpawnSpec } from "./ndjson-supervisor";

const MANIFEST_FORMAT = "rpg-world-forge.studio_runtime_manifest";
const MANIFEST_FILE = "runtime-manifest.json";
const MANIFEST_MAX_BYTES = 16 * 1024;
const SERVICE_MODULE = "worldforge.studio";

interface RuntimeManifest {
  format: typeof MANIFEST_FORMAT;
  version: 1;
  python: {
    module: typeof SERVICE_MODULE;
    linux_x64: string;
    win32_x64: string;
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
  if (architecture !== "x64" || (platform !== "linux" && platform !== "win32")) {
    throw new Error(`Packaged Forge runtime is not defined for ${platform}-${architecture}`);
  }
  const manifest = await loadRuntimeManifest(options.resourcesPath);
  const relativeExecutable =
    platform === "linux" ? manifest.python.linux_x64 : manifest.python.win32_x64;
  const executable = await resolvePackagedExecutable(options.resourcesPath, relativeExecutable);
  return fixedPythonSpec(executable, options.dataDir, environment);
}

async function loadRuntimeManifest(resourcesPath: string): Promise<RuntimeManifest> {
  const manifestPath = path.join(resourcesPath, MANIFEST_FILE);
  const stat = await lstat(manifestPath);
  if (!stat.isFile() || stat.isSymbolicLink() || stat.size > MANIFEST_MAX_BYTES) {
    throw new Error("Packaged Studio runtime manifest must be a small standalone regular file");
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(await readFile(manifestPath, "utf8"));
  } catch (error) {
    throw new Error("Packaged Studio runtime manifest is not valid UTF-8 JSON", { cause: error });
  }
  if (!isRecord(parsed) || !hasExactKeys(parsed, ["format", "version", "python"])) {
    throw new Error("Packaged Studio runtime manifest has unknown or missing fields");
  }
  if (parsed.format !== MANIFEST_FORMAT || parsed.version !== 1 || !isRecord(parsed.python)) {
    throw new Error("Packaged Studio runtime manifest format or version is unsupported");
  }
  if (!hasExactKeys(parsed.python, ["module", "linux_x64", "win32_x64"])) {
    throw new Error("Packaged Studio Python runtime manifest is not closed");
  }
  if (parsed.python.module !== SERVICE_MODULE) {
    throw new Error("Packaged Studio service module is unsupported");
  }
  for (const key of ["linux_x64", "win32_x64"] as const) {
    if (!isPortableResourcePath(parsed.python[key])) {
      throw new Error(`Packaged Studio ${key} executable path is not portable`);
    }
  }
  return parsed as unknown as RuntimeManifest;
}

async function resolvePackagedExecutable(resourcesPath: string, relativePath: string): Promise<string> {
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
  if (process.platform !== "win32" && (stat.mode & 0o111) === 0) {
    throw new Error("Packaged Studio Python runtime is not executable");
  }
  return candidate;
}

async function assertDevelopmentInterpreter(executable: string): Promise<void> {
  const stat = await lstat(executable);
  if (!stat.isFile() && !stat.isSymbolicLink()) {
    throw new Error("Configured development Python interpreter is not a file");
  }
  const target = await realpath(executable);
  const targetStat = await lstat(target);
  if (!targetStat.isFile()) {
    throw new Error("Configured development Python interpreter does not resolve to a regular file");
  }
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

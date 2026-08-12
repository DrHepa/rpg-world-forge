import path from "node:path";

const PREFIX = "WORLD_FORGE_STUDIO_";
const LEGACY_PREFIX = "RWF_STUDIO_";
const SUPPORTED_SUFFIXES = Object.freeze([
  "BUILD_PYTHON",
  "DEV_CODEX",
  "DEV_PYTHON",
  "PACKAGE_OUTPUT",
  "TEST_PYTHON",
]);
const SUPPORTED = new Set(SUPPORTED_SUFFIXES);

function environmentValue(environment, name) {
  const value = environment[name];
  if (value === undefined || (typeof value === "string" && value.trim() === "")) {
    return undefined;
  }
  if (typeof value !== "string") {
    throw new TypeError(`Studio environment variable must be text: ${name}`);
  }
  return value;
}

function normalizedPathValue(value, { cwd, pathFlavor }) {
  return pathFlavor.resolve(cwd, value);
}

function comparisonKey(value, pathFlavor) {
  return pathFlavor.sep === "\\" ? value.toLowerCase() : value;
}

export function resolveStudioEnvironmentValue(
  environment,
  suffix,
  {
    cwd = process.cwd(),
    pathFlavor = path,
  } = {},
) {
  if (!SUPPORTED.has(suffix)) {
    throw new TypeError(`unsupported Studio environment variable: ${suffix}`);
  }
  const canonicalName = `${PREFIX}${suffix}`;
  const legacyName = `${LEGACY_PREFIX}${suffix}`;
  const canonical = environmentValue(environment, canonicalName);
  const legacy = environmentValue(environment, legacyName);
  const canonicalPath =
    canonical === undefined
      ? undefined
      : normalizedPathValue(canonical, { cwd, pathFlavor });
  const legacyPath =
    legacy === undefined
      ? undefined
      : normalizedPathValue(legacy, { cwd, pathFlavor });
  if (
    canonicalPath !== undefined &&
    legacyPath !== undefined &&
    comparisonKey(canonicalPath, pathFlavor) !==
      comparisonKey(legacyPath, pathFlavor)
  ) {
    throw new Error(
      `conflicting Studio environment variables: ${canonicalName} and ${legacyName}`,
    );
  }
  return canonicalPath ?? legacyPath;
}

export function resolveStudioEnvironment(environment, options = {}) {
  const resolved = {};
  for (const suffix of SUPPORTED_SUFFIXES) {
    resolved[suffix] = resolveStudioEnvironmentValue(
      environment,
      suffix,
      options,
    );
  }
  return Object.freeze(resolved);
}

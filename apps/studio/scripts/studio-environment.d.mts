import type path from "node:path";

export type StudioEnvironmentSuffix =
  | "BUILD_PYTHON"
  | "DEV_CODEX"
  | "DEV_PYTHON"
  | "PACKAGE_OUTPUT"
  | "TEST_PYTHON";

export type ResolvedStudioEnvironment = Readonly<
  Record<StudioEnvironmentSuffix, string | undefined>
>;

export interface StudioEnvironmentResolutionOptions {
  cwd?: string;
  pathFlavor?: path.PlatformPath;
}

export function resolveStudioEnvironmentValue(
  environment: NodeJS.ProcessEnv,
  suffix: StudioEnvironmentSuffix,
  options?: StudioEnvironmentResolutionOptions,
): string | undefined;

export function resolveStudioEnvironment(
  environment: NodeJS.ProcessEnv,
  options?: StudioEnvironmentResolutionOptions,
): ResolvedStudioEnvironment;

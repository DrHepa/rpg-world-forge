export function noFollowOpenFlagForPlatform(
  platform: NodeJS.Platform,
  noFollowFlag: number | undefined,
): number {
  return platform === "win32" ? 0 : (noFollowFlag ?? 0);
}

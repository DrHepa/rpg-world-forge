export const GAME_PACKAGE_ENTRY: string;

export interface GamePackageSmokeReport {
  readonly artifact_kind: "asar" | "module";
  readonly artifact_sha256?: string;
  readonly artifact_size_bytes?: number;
  readonly contract_format: "world-forge.game_package";
  readonly format: "world-forge.studio_game_package_smoke";
  readonly format_version: 1;
  readonly manifests_verified: number;
  readonly release: "blocked";
  readonly semantic_boundary: "packaged_python_required";
  readonly status: "verified";
  readonly supported: false;
  readonly tamper_rejections: number;
}

export function verifyGamePackageArtifact(options: {
  readonly artifactKind: "asar" | "module";
  readonly artifactPath: string;
}): Promise<GamePackageSmokeReport>;

export function verifyGamePackageSnapshot(options: {
  readonly artifactBytes: Buffer;
  readonly expectedSha256: string;
  readonly expectedSize: number;
}): Promise<GamePackageSmokeReport>;

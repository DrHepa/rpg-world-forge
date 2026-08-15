export const GENERIC_ASSET_RUNTIME_ENTRY: string;

export function createCanonicalAssetpackSmokeRoot(
  parent?: string,
): Promise<string>;

export function buildAssetpackFixture(fixtureName: string): Promise<{
  document: Record<string, unknown>;
  files: Map<string, Buffer>;
  runtimePath: string;
}>;

export function writeAssetpack(
  root: string,
  fixture: {
    document: Record<string, unknown>;
    files: ReadonlyMap<string, Buffer>;
  },
): Promise<void>;

export interface GenericAssetRuntimeSmokeReport {
  accepted_formats: string[];
  artifact_kind: "asar" | "module";
  format: "world-forge.studio_generic_asset_runtime_smoke";
  format_version: 2;
  invalid_documents_rejected: number;
  sealed_pack_formats: ["world-forge.assetpack"];
  sealed_packs_verified: number;
  sealed_tamper_rejections: number;
  status: "verified";
  valid_documents_accepted: number;
}

export interface RetainedGenericAssetRuntimeSmokeReport
  extends GenericAssetRuntimeSmokeReport {
  artifact_sha256: string;
  artifact_size_bytes: number;
  artifact_kind: "asar";
}

export function verifyGenericAssetRuntimeArtifact(options: {
  artifactKind: "asar" | "module";
  artifactPath: string;
}): Promise<GenericAssetRuntimeSmokeReport>;

export function verifyGenericAssetRuntimeSnapshot(options: {
  artifactBytes: Buffer;
  expectedSha256: string;
  expectedSize: number;
}): Promise<RetainedGenericAssetRuntimeSmokeReport>;

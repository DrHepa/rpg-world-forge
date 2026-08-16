export function buildGameRuntimeBundleFixture(
  fixtureName: "abstract-puzzle" | "branching-narrative",
): Promise<{
  document: Record<string, unknown>;
  files: Map<string, Buffer>;
}>;

export function writeGameRuntimeBundleFixture(
  root: string,
  fixture: {
    document: Record<string, unknown>;
    files: ReadonlyMap<string, Buffer>;
  },
): Promise<void>;

export function applySelfResealedGameRuntimeBundleMutation(
  root: string,
  mutation:
    | "extra_authoring_file"
    | "runtime_source"
    | "assetpack_gamepack_id"
    | "composition_assetpack_id"
    | "composition_asset_inventory_id",
): Promise<void>;

export function createCanonicalGameRuntimeBundleSmokeRoot(
  parent?: string,
): Promise<string>;

export function verifyGameRuntimeBundleArtifact(input: {
  artifactKind: "asar" | "module";
  artifactPath: string;
}): Promise<Readonly<Record<string, unknown>>>;

export function verifyGameRuntimeBundleSnapshot(input: {
  artifactBytes: Buffer;
  expectedSha256: string;
  expectedSize: number;
}): Promise<Readonly<Record<string, unknown>>>;
export const GAME_RUNTIME_BUNDLE_ENTRY: string;

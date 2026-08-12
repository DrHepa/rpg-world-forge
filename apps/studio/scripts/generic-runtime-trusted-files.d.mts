export const GENERIC_RUNTIME_TRUSTED_SNAPSHOT: Readonly<{
  content_hash: string;
  files: readonly Readonly<{
    base64: string;
    path: string;
    sha256: string;
    size_bytes: number;
  }>[];
  snapshot_id: string;
  tree_hash: string;
}>;

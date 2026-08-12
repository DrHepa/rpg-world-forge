export const GENERIC_MATERIALIZATION_CONTRACTS_ENTRY: string;

export function verifyGenericMaterializationArtifact(options: {
  artifactKind: "asar" | "module";
  artifactPath: string;
}): Promise<Readonly<{
  artifact_kind: "asar" | "module";
  invalid_documents_rejected: number;
  status: "verified";
  valid_documents_accepted: number;
}>>;

export function verifyGenericMaterializationSnapshot(options: {
  artifactBytes: Buffer;
  expectedSha256: string;
  expectedSize: number;
}): Promise<Readonly<{
  artifact_kind: "asar";
  artifact_sha256: string;
  artifact_size_bytes: number;
  invalid_documents_rejected: number;
  status: "verified";
  valid_documents_accepted: number;
}>>;

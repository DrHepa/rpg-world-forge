export const GENERIC_RUNTIME_CONTRACTS_ENTRY: string;

export interface GenericRuntimeContractSmoke {
  readonly artifact_kind: "asar" | "module";
  readonly execution_semantics: Readonly<{
    readonly content_hash: string;
    readonly version: 1;
  }>;
  readonly format: "world-forge.studio_generic_runtime_contract_smoke";
  readonly format_version: 1;
  readonly invalid_documents_rejected: number;
  readonly status: "verified";
  readonly valid_documents_accepted: number;
}

export function verifyGenericRuntimeArtifact(options: {
  readonly artifactKind: "asar" | "module";
  readonly artifactPath: string;
}): Promise<GenericRuntimeContractSmoke>;

export function verifyGenericRuntimeSnapshot(options: {
  readonly artifactBytes: Buffer;
  readonly expectedSha256: string;
  readonly expectedSize: number;
}): Promise<
  GenericRuntimeContractSmoke & {
    readonly artifact_sha256: string;
    readonly artifact_size_bytes: number;
  }
>;

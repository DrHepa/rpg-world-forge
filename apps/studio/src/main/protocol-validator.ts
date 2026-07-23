import Ajv2020, { type ErrorObject, type ValidateFunction } from "ajv/dist/2020.js";

import changesetSchema from "../../../../schemas/studio-changeset.schema.json";
import jobSchema from "../../../../schemas/studio-job.schema.json";
import protocolSchema from "../../../../schemas/studio-protocol.schema.json";
import type {
  Event as StudioEventEnvelope,
  Request as StudioRequestEnvelope,
  Response as StudioResponseEnvelope,
  Error as StudioErrorEnvelope,
} from "../generated/studio-protocol";

export type StudioEnvelope =
  | StudioRequestEnvelope
  | StudioResponseEnvelope
  | StudioErrorEnvelope
  | StudioEventEnvelope;

const WINDOWS_RESERVED_NAMES = new Set([
  "aux",
  "con",
  "nul",
  "prn",
  ...Array.from({ length: 9 }, (_, index) => `com${String(index + 1)}`),
  ...Array.from({ length: 9 }, (_, index) => `lpt${String(index + 1)}`),
]);

const ajv = new Ajv2020({ allErrors: true, allowUnionTypes: true, strict: true });
ajv.addKeyword({ keyword: "x-worldforge-path-policy", schemaType: "object" });
ajv.addKeyword({
  keyword: "x-worldforge-max-utf8-bytes",
  type: "string",
  schemaType: "number",
  validate: (limit: number, value: string) => Buffer.byteLength(value, "utf8") <= limit,
});
ajv.addFormat("rpg-world-forge-portable-source-path", {
  type: "string",
  validate: isPortableSourcePath,
});
ajv.addFormat("rpg-world-forge-portable-relative-path", {
  type: "string",
  validate: isPortableRelativePath,
});
ajv.addFormat("rpg-world-forge-portable-asset-catalog-path", {
  type: "string",
  validate: isPortableAssetCatalogPath,
});
ajv.addSchema(changesetSchema);
ajv.addSchema(jobSchema);
const validate: ValidateFunction<StudioEnvelope> = ajv.compile(protocolSchema);
const ASSET_PREVIEW_CHUNK_BYTES = 64 * 1024;
const MAX_ASSET_PREVIEW_BASE64_LENGTH = 87_384;
const CANONICAL_BASE64_PATTERN =
  /^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/u;

export function isPortableSourcePath(value: string): boolean {
  const parts = value.split("/");
  if (parts.length < 2 || parts.length > 8 || parts[0] !== "source") {
    return false;
  }
  return parts.every((part) => isPortablePathComponent(part));
}

export function isPortableRelativePath(value: string): boolean {
  const parts = value.split("/");
  return parts.length >= 1 && parts.length <= 16 && parts.every(isPortablePathComponent);
}

export function isPortableAssetCatalogPath(value: string): boolean {
  const parts = value.split("/");
  return parts.length >= 1 && parts.length <= 32 && parts.every(isPortablePathComponent);
}

function isPortablePathComponent(value: string): boolean {
  if (
    value.length === 0 ||
    value === "." ||
    value === ".." ||
    value.normalize("NFC") !== value ||
    Buffer.byteLength(value, "utf8") > 255 ||
    value.endsWith(" ") ||
    value.endsWith(".") ||
    containsInvalidUnicode(value)
  ) {
    return false;
  }
  for (const character of value) {
    if (character.charCodeAt(0) < 32 || '<>:"/\\|?*'.includes(character)) {
      return false;
    }
  }
  return !WINDOWS_RESERVED_NAMES.has(value.split(".", 1)[0].toLowerCase());
}

function containsInvalidUnicode(value: string): boolean {
  for (let index = 0; index < value.length; index += 1) {
    const code = value.charCodeAt(index);
    if (code >= 0xd800 && code <= 0xdbff) {
      const following = value.charCodeAt(index + 1);
      if (index + 1 >= value.length || following < 0xdc00 || following > 0xdfff) {
        return true;
      }
      index += 1;
    } else if (code >= 0xdc00 && code <= 0xdfff) {
      return true;
    }
  }
  return false;
}

export function validateStudioEnvelope(value: unknown): value is StudioEnvelope {
  if (!validate(value)) {
    return false;
  }
  if (value.kind !== "response" || value.method !== "asset.preview.read") {
    return true;
  }
  const decoded = decodeCanonicalAssetPreviewBase64(value.result.data_base64);
  if (
    decoded === null ||
    decoded.byteLength !== value.result.byte_length ||
    value.result.cumulative_bytes !==
      value.result.sequence * ASSET_PREVIEW_CHUNK_BYTES + value.result.byte_length ||
    (!value.result.eof && value.result.byte_length !== ASSET_PREVIEW_CHUNK_BYTES)
  ) {
    return false;
  }
  return true;
}

export function decodeCanonicalAssetPreviewBase64(value: unknown): Uint8Array | null {
  if (
    typeof value !== "string" ||
    value.length < 4 ||
    value.length > MAX_ASSET_PREVIEW_BASE64_LENGTH ||
    value.length % 4 !== 0 ||
    !CANONICAL_BASE64_PATTERN.test(value)
  ) {
    return null;
  }
  const decoded = Buffer.from(value, "base64");
  if (decoded.toString("base64") !== value) {
    return null;
  }
  return new Uint8Array(decoded);
}

export function describeProtocolErrors(): string {
  const errors: ErrorObject[] | null | undefined = validate.errors;
  if (!errors || errors.length === 0) {
    return "unknown protocol validation error";
  }
  return errors
    .slice(0, 4)
    .map((error) => `${error.instancePath || "/"} ${error.message ?? "is invalid"}`)
    .join("; ");
}

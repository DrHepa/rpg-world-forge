import Ajv2020, { type ErrorObject, type ValidateFunction } from "ajv/dist/2020.js";

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
ajv.addFormat("rpg-world-forge-portable-source-path", {
  type: "string",
  validate: isPortableSourcePath,
});
ajv.addFormat("rpg-world-forge-portable-relative-path", {
  type: "string",
  validate: isPortableRelativePath,
});
ajv.addSchema(jobSchema);
const validate: ValidateFunction<StudioEnvelope> = ajv.compile(protocolSchema);

function isPortableSourcePath(value: string): boolean {
  const parts = value.split("/");
  if (parts.length < 2 || parts.length > 8 || parts[0] !== "source") {
    return false;
  }
  return parts.every((part) => isPortablePathComponent(part));
}

function isPortableRelativePath(value: string): boolean {
  const parts = value.split("/");
  return parts.length >= 1 && parts.length <= 16 && parts.every(isPortablePathComponent);
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
      if (following < 0xdc00 || following > 0xdfff) {
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
  return validate(value);
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

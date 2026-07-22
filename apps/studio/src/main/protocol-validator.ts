import Ajv2020, { type ErrorObject, type ValidateFunction } from "ajv/dist/2020.js";

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

const ajv = new Ajv2020({ allErrors: true, allowUnionTypes: true, strict: true });
const validate: ValidateFunction<StudioEnvelope> = ajv.compile(protocolSchema);

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

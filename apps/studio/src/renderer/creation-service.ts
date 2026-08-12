import type {
  StudioClientResult,
  StudioCreationAuthorityCapabilities,
  StudioV3ReplyEnvelope,
  StudioV4ReplyEnvelope,
  StudioV5ReplyEnvelope,
} from "../shared/studio-api";

export class CreationServiceError extends Error {
  public constructor(
    public readonly code: string,
    message: string,
  ) {
    super(message);
  }
}

export function isCreationServiceError(
  error: unknown,
  code: string,
): error is CreationServiceError {
  return error instanceof CreationServiceError && error.code === code;
}

export async function expectCreationResult(
  promise: Promise<StudioClientResult<StudioV3ReplyEnvelope>>,
  method: string,
): Promise<Record<string, unknown>> {
  const reply = await promise;
  if (!reply.ok) throw new CreationServiceError(reply.error.code, reply.error.message);
  if (reply.value.kind === "error") {
    throw new CreationServiceError(reply.value.error.code, reply.value.error.message);
  }
  if (reply.value.method !== method || !isRecord(reply.value.result)) {
    throw new Error("Forge Studio returned an invalid generic creation response");
  }
  return reply.value.result;
}

export async function expectCreationEvidenceResult(
  promise: Promise<StudioClientResult<StudioV4ReplyEnvelope>>,
  method: string,
): Promise<Record<string, unknown>> {
  const reply = await promise;
  if (!reply.ok) throw new CreationServiceError(reply.error.code, reply.error.message);
  const envelope = validateCreationEvidenceEnvelope(reply.value, method);
  if (envelope.kind === "error") {
    throw new CreationServiceError(envelope.code, envelope.message);
  }
  return envelope.result;
}

export async function expectCreationAuthorityResult(
  promise: Promise<StudioClientResult<StudioV5ReplyEnvelope>>,
  method: string,
): Promise<Record<string, unknown>> {
  const reply = await promise;
  if (!reply.ok) throw new CreationServiceError(reply.error.code, reply.error.message);
  const envelope = validateCreationAuthorityEnvelope(reply.value, method);
  if (envelope.kind === "error") {
    throw new CreationServiceError(envelope.code, envelope.message);
  }
  return envelope.result;
}

export function isClosedCreationAuthorityCapabilities(
  value: unknown,
): value is StudioCreationAuthorityCapabilities {
  return (
    isRecord(value) &&
    hasExactKeys(value, [
      "asset_authority_reviews",
      "asset_release_authority",
      "creation_preview_pre_release",
      "protocolVersion",
      "runtime_headless_authority",
    ]) &&
    value.protocolVersion === 5 &&
    value.asset_authority_reviews === true &&
    value.asset_release_authority === true &&
    value.runtime_headless_authority === true &&
    value.creation_preview_pre_release === true
  );
}

function validateCreationAuthorityEnvelope(
  value: unknown,
  method: string,
):
  | { kind: "response"; result: Record<string, unknown> }
  | { kind: "error"; code: string; message: string } {
  if (
    !isRecord(value) ||
    value.protocol !== "rpg-world-forge.studio_protocol" ||
    value.protocol_version !== 5 ||
    (value.kind !== "response" && value.kind !== "error")
  ) {
    throw new Error("Forge Studio returned an invalid creation authority response");
  }
  if (value.kind === "response") {
    if (
      !hasExactKeys(value, [
        "protocol",
        "protocol_version",
        "kind",
        "request_id",
        "method",
        "result",
      ]) ||
      !isEntityId(value.request_id) ||
      value.method !== method ||
      !isRecord(value.result)
    ) {
      throw new Error("Forge Studio returned an invalid creation authority response");
    }
    return { kind: "response", result: value.result };
  }
  if (
    !hasExactKeys(value, [
      "protocol",
      "protocol_version",
      "kind",
      "request_id",
      "error",
    ]) ||
    (value.request_id !== null && !isEntityId(value.request_id)) ||
    !isRecord(value.error) ||
    !hasExactKeys(value.error, ["code", "message", "details"]) ||
    typeof value.error.code !== "string" ||
    !CREATION_ERROR_CODES.has(value.error.code) ||
    typeof value.error.message !== "string" ||
    !isRecord(value.error.details)
  ) {
    throw new Error("Forge Studio returned an invalid creation authority response");
  }
  return {
    kind: "error",
    code: value.error.code,
    message: value.error.message,
  };
}

function validateCreationEvidenceEnvelope(
  value: unknown,
  method: string,
):
  | { kind: "response"; result: Record<string, unknown> }
  | { kind: "error"; code: string; message: string } {
  if (
    !isRecord(value) ||
    value.protocol !== "rpg-world-forge.studio_protocol" ||
    value.protocol_version !== 4 ||
    (value.kind !== "response" && value.kind !== "error")
  ) {
    throw new Error("Forge Studio returned an invalid creation evidence response");
  }
  if (value.kind === "response") {
    if (
      !hasExactKeys(value, [
        "protocol",
        "protocol_version",
        "kind",
        "request_id",
        "method",
        "result",
      ]) ||
      !isEntityId(value.request_id) ||
      value.method !== method ||
      !isRecord(value.result)
    ) {
      throw new Error("Forge Studio returned an invalid creation evidence response");
    }
    return { kind: "response", result: value.result };
  }
  if (
    !hasExactKeys(value, [
      "protocol",
      "protocol_version",
      "kind",
      "request_id",
      "error",
    ]) ||
    (value.request_id !== null && !isEntityId(value.request_id)) ||
    !isRecord(value.error) ||
    !hasExactKeys(value.error, ["code", "message", "details"]) ||
    typeof value.error.code !== "string" ||
    !CREATION_ERROR_CODES.has(value.error.code) ||
    typeof value.error.message !== "string" ||
    value.error.message.length < 1 ||
    value.error.message.length > 4096 ||
    !isRecord(value.error.details)
  ) {
    throw new Error("Forge Studio returned an invalid creation evidence response");
  }
  return {
    kind: "error",
    code: value.error.code,
    message: value.error.message,
  };
}

const CREATION_ERROR_CODES = new Set([
  "invalid_request",
  "not_found",
  "conflict",
  "invalid_state",
  "internal_error",
  "recovery_ambiguous",
  "recovery_failed",
]);

const ENTITY_ID = /^[a-z0-9][a-z0-9_-]{0,127}$/u;

function isEntityId(value: unknown): value is string {
  return typeof value === "string" && ENTITY_ID.test(value);
}

function hasExactKeys(value: Record<string, unknown>, keys: readonly string[]): boolean {
  const actual = Object.keys(value).sort();
  const expected = [...keys].sort();
  return actual.length === expected.length && actual.every((key, index) => key === expected[index]);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

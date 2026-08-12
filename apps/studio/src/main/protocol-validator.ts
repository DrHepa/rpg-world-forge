import Ajv2020, {
    type ErrorObject,
    type ValidateFunction,
} from "ajv/dist/2020.js";

import changesetSchema from "../../../../schemas/studio-changeset.schema.json";
import externalGrantSchema from "../../../../schemas/studio-external-grant.schema.json";
import creationRootGrantSchema from "../../../../schemas/studio-creation-root-grant.schema.json";
import creationArtifactSchema from "../../../../schemas/studio-creation-artifact.schema.json";
import creationChangesetSchema from "../../../../schemas/studio-creation-changeset.schema.json";
import creationEvidenceSchema from "../../../../schemas/studio-creation-evidence.schema.json";
import creationJobSchema from "../../../../schemas/studio-creation-job.schema.json";
import creationJobV12Schema from "../../../../schemas/studio-creation-job-v12.schema.json";
import creationOutputGrantSchema from "../../../../schemas/studio-creation-output-grant.schema.json";
import creationOutputGrantV6Schema from "../../../../schemas/studio-creation-output-grant-v6.schema.json";
import creationPreviewSchema from "../../../../schemas/studio-creation-preview.schema.json";
import creationPreviewV2Schema from "../../../../schemas/studio-creation-preview-v2.schema.json";
import creationWorkspaceSchema from "../../../../schemas/studio-creation-workspace.schema.json";
import jobSchema from "../../../../schemas/studio-job.schema.json";
import externalJobSchema from "../../../../schemas/studio-job-v3.schema.json";
import protocolSchema from "../../../../schemas/studio-protocol.schema.json";
import protocolV2Schema from "../../../../schemas/studio-protocol-v2.schema.json";
import protocolV3Schema from "../../../../schemas/studio-protocol-v3.schema.json";
import protocolV4Schema from "../../../../schemas/studio-protocol-v4.schema.json";
import protocolV5Schema from "../../../../schemas/studio-protocol-v5.schema.json";
import type {
    Error as StudioErrorEnvelope,
    Event as StudioEventEnvelope,
    Request as StudioRequestEnvelope,
    Response as StudioResponseEnvelope,
} from "../generated/studio-protocol";
import type {
    ErrorEnvelope as StudioV2ErrorEnvelope,
    Request as StudioV2RequestEnvelope,
    Response as StudioV2ResponseEnvelope,
} from "../generated/studio-protocol-v2";
import type {
    ErrorEnvelope as StudioV3ErrorEnvelope,
    Request as StudioV3RequestEnvelope,
    Response as StudioV3ResponseEnvelope,
} from "../generated/studio-protocol-v3";
import type {
    ErrorEnvelope as StudioV4ErrorEnvelope,
    Request as StudioV4RequestEnvelope,
    Response as StudioV4ResponseEnvelope,
} from "../generated/studio-protocol-v4";
import type {
    ErrorEnvelope as StudioV5ErrorEnvelope,
    Request as StudioV5RequestEnvelope,
    Response as StudioV5ResponseEnvelope,
} from "../generated/studio-protocol-v5";
import { hasDistinctStudioV12HeadlessAuthorityIdentities } from "../shared/studio-api";

export type StudioEnvelope =
    | StudioRequestEnvelope
    | StudioResponseEnvelope
    | StudioErrorEnvelope
    | StudioEventEnvelope
    | StudioV2RequestEnvelope
    | StudioV2ResponseEnvelope
    | StudioV2ErrorEnvelope
    | StudioV3RequestEnvelope
    | StudioV3ResponseEnvelope
    | StudioV3ErrorEnvelope
    | StudioV4RequestEnvelope
    | StudioV4ResponseEnvelope
    | StudioV4ErrorEnvelope
    | StudioV5RequestEnvelope
    | StudioV5ResponseEnvelope
    | StudioV5ErrorEnvelope;

const WINDOWS_RESERVED_NAMES = new Set([
    "aux",
    "con",
    "nul",
    "prn",
    ...Array.from({ length: 9 }, (_, index) => `com${String(index + 1)}`),
    ...Array.from({ length: 9 }, (_, index) => `lpt${String(index + 1)}`),
]);

const ajv = new Ajv2020({
    allErrors: true,
    allowUnionTypes: true,
    strict: true,
});
ajv.addKeyword({ keyword: "x-worldforge-path-policy", schemaType: "object" });
ajv.addKeyword({
    keyword: "x-world-forge-portable-path",
    type: "string",
    schemaType: "boolean",
    validate: (enabled: boolean, value: string) =>
        !enabled || isPortableRelativePath(value),
});
ajv.addKeyword({
    keyword: "x-worldforge-max-utf8-bytes",
    type: "string",
    schemaType: "number",
    validate: (limit: number, value: string) =>
        Buffer.byteLength(value, "utf8") <= limit,
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
ajv.addSchema(externalGrantSchema);
ajv.addSchema(jobSchema);
ajv.addSchema(externalJobSchema);
ajv.addSchema(creationRootGrantSchema);
ajv.addSchema(creationArtifactSchema);
ajv.addSchema(creationChangesetSchema);
ajv.addSchema(creationEvidenceSchema);
ajv.addSchema(creationJobSchema);
ajv.addSchema(creationJobV12Schema);
ajv.addSchema(creationOutputGrantSchema);
ajv.addSchema(creationOutputGrantV6Schema);
ajv.addSchema(creationPreviewSchema);
ajv.addSchema(creationPreviewV2Schema);
ajv.addSchema(creationWorkspaceSchema);
const validateV1: ValidateFunction<StudioEnvelope> =
    ajv.compile(protocolSchema);
const validateV2: ValidateFunction<StudioEnvelope> =
    ajv.compile(protocolV2Schema);
const validateV3: ValidateFunction<StudioEnvelope> =
    ajv.compile(protocolV3Schema);
const validateV4: ValidateFunction<StudioEnvelope> =
    ajv.compile(protocolV4Schema);
const validateV5: ValidateFunction<StudioEnvelope> =
    ajv.compile(protocolV5Schema);
let lastErrors: ErrorObject[] | null | undefined;
let lastSemanticError: string | null = null;
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
    return (
        parts.length >= 1 &&
        parts.length <= 16 &&
        parts.every(isPortablePathComponent)
    );
}

export function isPortableAssetCatalogPath(value: string): boolean {
    const parts = value.split("/");
    return (
        parts.length >= 1 &&
        parts.length <= 32 &&
        parts.every(isPortablePathComponent)
    );
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
            if (
                index + 1 >= value.length ||
                following < 0xdc00 ||
                following > 0xdfff
            ) {
                return true;
            }
            index += 1;
        } else if (code >= 0xdc00 && code <= 0xdfff) {
            return true;
        }
    }
    return false;
}

export function validateStudioEnvelope(
    value: unknown,
): value is StudioEnvelope {
    lastSemanticError = null;
    const validator =
        isRecord(value) && value.protocol_version === 5
            ? validateV5
            : isRecord(value) && value.protocol_version === 4
              ? validateV4
              : isRecord(value) && value.protocol_version === 3
                ? validateV3
                : isRecord(value) && value.protocol_version === 2
                  ? validateV2
                  : validateV1;
    if (!validator(value)) {
        lastErrors = validator.errors;
        return false;
    }
    lastErrors = null;
    const semanticError = validateStudioV5HeadlessAuthoritySemantics(value);
    if (semanticError !== null) {
        lastSemanticError = semanticError;
        return false;
    }
    if (
        value.kind !== "response" ||
        (value.method !== "asset.preview.read" &&
            value.method !== "creation_preview.read")
    ) {
        return true;
    }
    const decoded = decodeCanonicalAssetPreviewBase64(value.result.data_base64);
    if (
        decoded === null ||
        decoded.byteLength !== value.result.byte_length ||
        value.result.cumulative_bytes !==
            value.result.sequence * ASSET_PREVIEW_CHUNK_BYTES +
                value.result.byte_length ||
        (!value.result.eof &&
            value.result.byte_length !== ASSET_PREVIEW_CHUNK_BYTES)
    ) {
        return false;
    }
    return true;
}

export function decodeCanonicalAssetPreviewBase64(
    value: unknown,
): Uint8Array | null {
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
    if (lastSemanticError !== null) {
        return lastSemanticError;
    }
    const errors = lastErrors;
    if (!errors || errors.length === 0) {
        return "unknown protocol validation error";
    }
    return errors
        .slice(0, 4)
        .map(
            (error) =>
                `${error.instancePath || "/"} ${error.message ?? "is invalid"}`,
        )
        .join("; ");
}

function isRecord(value: unknown): value is Record<string, unknown> {
    return typeof value === "object" && value !== null && !Array.isArray(value);
}

function validateStudioV5HeadlessAuthoritySemantics(
    value: unknown,
): string | null {
    if (!isRecord(value) || value.protocol_version !== 5) {
        return null;
    }
    if (
        value.kind === "request" &&
        value.method === "creation_job.create" &&
        isRecord(value.params) &&
        value.params.operation === "runtime.headless.verify"
    ) {
        return hasDistinctStudioV12HeadlessAuthorityIdentities(value.params)
            ? null
            : "v12 headless retained authority identities are not distinct";
    }
    if (value.kind !== "response" || !isRecord(value.result)) {
        return null;
    }
    if (value.method === "creation_job.list") {
        const jobs = value.result.jobs;
        if (!Array.isArray(jobs)) {
            return null;
        }
        return jobs.some(hasInvalidStudioV12HeadlessAuthority)
            ? "v12 headless retained authority identities are not distinct"
            : null;
    }
    if (
        value.method === "creation_job.create" ||
        value.method === "creation_job.get" ||
        value.method === "creation_job.cancel" ||
        value.method === "creation_job.recover"
    ) {
        return hasInvalidStudioV12HeadlessAuthority(value.result.job)
            ? "v12 headless retained authority identities are not distinct"
            : null;
    }
    return null;
}

function hasInvalidStudioV12HeadlessAuthority(value: unknown): boolean {
    return (
        isRecord(value) &&
        value.format_version === 12 &&
        value.operation === "runtime.headless.verify" &&
        !hasDistinctStudioV12HeadlessAuthorityIdentities(value.operation_params)
    );
}

import { createHash } from "node:crypto";
import { mkdir, readFile, readdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

import Ajv2020 from "ajv/dist/2020.js";
import { compile } from "json-schema-to-typescript";
import ts from "typescript";

import {
    GENERIC_ASSET_GLYPH_RANGE_PATTERN,
    GENERIC_ASSET_ID_PATTERN,
    GENERIC_ASSET_RUNTIME_STRING_PATTERN,
    areCanonicalGenericAssetGlyphRanges,
    hasCanonicalGenericAssetContentHash,
    hasCoherentGenericAssetD2bContract,
    hasCoherentGenericAssetProductionRequest,
    hasDistinctGenericAssetContentHashes,
    hasExactGenericAssetReceiptLineageRoots,
    hasMatchingGenericAssetGlyphCount,
    hasMatchingGenericAssetTextSha256,
    hasPortableGenericAssetPathTree,
    isCanonicalGenericAssetObjectArray,
    isCanonicalGenericAssetStringArray,
    isPortableGenericAssetRuntimePath,
    isRuntimeSafeGenericAssetNotice,
    isSafeGenericAssetRuntimeText,
} from "./generic-asset-validation.mjs";
import { hasCoherentGenericAssetpack } from "./generic-assetpack-validation.mjs";
import {
    hasCoherentGamePersistence,
    hasCoherentPersistenceGeneration,
} from "./game-persistence-validation.mjs";
import { hasCoherentGamePackage } from "./game-package-validation.mjs";
import { hasCoherentGameRuntimeBundle } from "./game-runtime-bundle-validation.mjs";
import { hasCoherentGenericHeadlessContract } from "./generic-headless-validation.mjs";
import { hasCoherentGenericRuntimeContract } from "./generic-runtime-validation.mjs";
import {
    hasAuditedRuntimePlatformLock,
    hasCoherentGameMaterializationBundle,
    hasCoherentRuntimeImplementation,
    hasCoherentStandaloneGame,
    hasCoherentStandaloneGameLock,
    hasCoherentStandalonePlatform,
} from "./materialization-contract-validation.mjs";
import { toPortableFixtureKey } from "./generator-paths.mjs";
import { decodeStrictJsonObject } from "./strict-json.mjs";

const appRoot = path.resolve(
    path.dirname(fileURLToPath(import.meta.url)),
    "..",
);
const schemaPath = path.resolve(
    appRoot,
    "../../schemas/studio-protocol.schema.json",
);
const outputPath = path.resolve(appRoot, "src/generated/studio-protocol.d.ts");
const protocolV2SchemaPath = path.resolve(
    appRoot,
    "../../schemas/studio-protocol-v2.schema.json",
);
const protocolV2OutputPath = path.resolve(
    appRoot,
    "src/generated/studio-protocol-v2.d.ts",
);
const protocolV3SchemaPath = path.resolve(
    appRoot,
    "../../schemas/studio-protocol-v3.schema.json",
);
const protocolV3OutputPath = path.resolve(
    appRoot,
    "src/generated/studio-protocol-v3.d.ts",
);
const protocolV4SchemaPath = path.resolve(
    appRoot,
    "../../schemas/studio-protocol-v4.schema.json",
);
const protocolV4OutputPath = path.resolve(
    appRoot,
    "src/generated/studio-protocol-v4.d.ts",
);
const protocolV5SchemaPath = path.resolve(
    appRoot,
    "../../schemas/studio-protocol-v5.schema.json",
);
const protocolV5OutputPath = path.resolve(
    appRoot,
    "src/generated/studio-protocol-v5.d.ts",
);
const contractsSchemaRoot = path.resolve(appRoot, "../../schemas");
const contractsOutputPath = path.resolve(
    appRoot,
    "src/generated/world-forge-contracts.d.ts",
);
const contractsConformancePath = path.resolve(
    appRoot,
    "src/generated/world-forge-contracts.conformance.ts",
);
const creationContentModesOutputPath = path.resolve(
    appRoot,
    "src/generated/creation-content-modes.ts",
);
const contractsFixtureRoot = path.resolve(
    appRoot,
    "../../examples/multigenre-contracts",
);
const logicRuntimeStringCorpusPath = path.resolve(
    appRoot,
    "../../tests/fixtures/logic-runtime-string-corpus.json",
);
const checkOnly = process.argv.includes("--check");

async function readStrictJsonObject(file) {
    return decodeStrictJsonObject(await readFile(file), { context: file });
}

const logicRuntimeStringCorpus = await readStrictJsonObject(
    logicRuntimeStringCorpusPath,
);
const schema = await readStrictJsonObject(schemaPath);
const protocolV2Schema = await readStrictJsonObject(protocolV2SchemaPath);
const protocolV3Schema = await readStrictJsonObject(protocolV3SchemaPath);
const protocolV4Schema = await readStrictJsonObject(protocolV4SchemaPath);
const protocolV5Schema = await readStrictJsonObject(protocolV5SchemaPath);
const creationProfileSchema = await readStrictJsonObject(
    path.resolve(contractsSchemaRoot, "creation-profile.schema.json"),
);
await verifyFixtureHashes();
await verifyContractSchemas();
const style = {
    bracketSpacing: true,
    printWidth: 100,
    semi: true,
    singleQuote: false,
    tabWidth: 2,
    trailingComma: "all",
    useTabs: false,
};

const creationContentModes = creationProfileSchema?.$defs?.productionMode?.enum;
if (
    !Array.isArray(creationContentModes) ||
    creationContentModes.length === 0 ||
    !creationContentModes.every((mode) => typeof mode === "string") ||
    !creationContentModes.includes("authored")
) {
    throw new Error("Creation profile productionMode enum is not a usable generated vocabulary");
}
const generatedCreationContentModes = `/* AUTO-GENERATED from schemas/creation-profile.schema.json. Do not edit by hand. */
export const CREATION_CONTENT_MODES = ${JSON.stringify(creationContentModes, null, 2)} as const;
export type CreationContentMode = (typeof CREATION_CONTENT_MODES)[number];
export const DEFAULT_CREATION_CONTENT_MODE: CreationContentMode = "authored";
const CREATION_CONTENT_MODE_SET: ReadonlySet<string> = new Set(CREATION_CONTENT_MODES);
export function isCreationContentMode(value: unknown): value is CreationContentMode {
  return typeof value === "string" && CREATION_CONTENT_MODE_SET.has(value);
}
`;

const generated = await compile(schema, "StudioProtocolEnvelope", {
    bannerComment:
        "/* AUTO-GENERATED from schemas/studio-protocol.schema.json. Do not edit by hand. */",
    cwd: path.dirname(schemaPath),
    style,
    unreachableDefinitions: true,
});
const generatedProtocolV2 = await compile(
    protocolV2Schema,
    "StudioProtocolV2Envelope",
    {
        bannerComment:
            "/* AUTO-GENERATED from schemas/studio-protocol-v2.schema.json. Do not edit by hand. */\n" +
            "/* eslint-disable @typescript-eslint/no-empty-object-type */",
        cwd: path.dirname(protocolV2SchemaPath),
        style,
        unreachableDefinitions: true,
    },
);
const generatedProtocolV3Raw = await compile(
    protocolV3Schema,
    "StudioProtocolV3Envelope",
    {
        bannerComment:
            "/* AUTO-GENERATED from schemas/studio-protocol-v3.schema.json. Do not edit by hand. */\n" +
            "/* eslint-disable @typescript-eslint/no-empty-object-type */",
        additionalProperties: false,
        cwd: path.dirname(protocolV3SchemaPath),
        style,
        unreachableDefinitions: true,
    },
);
const emptyParamsDeclaration = "export interface EmptyParams {}";
if (!generatedProtocolV3Raw.includes(emptyParamsDeclaration)) {
    throw new Error(
        "Generated Studio protocol v3 empty params declaration changed",
    );
}
const generatedProtocolV3 = generatedProtocolV3Raw.replace(
    emptyParamsDeclaration,
    "export type EmptyParams = Record<string, never>;",
);
const generatedProtocolV4Raw = await compile(
    protocolV4Schema,
    "StudioProtocolV4Envelope",
    {
        bannerComment:
            "/* AUTO-GENERATED from schemas/studio-protocol-v4.schema.json. Do not edit by hand. */\n" +
            "/* eslint-disable @typescript-eslint/no-empty-object-type */",
        additionalProperties: false,
        cwd: path.dirname(protocolV4SchemaPath),
        style,
        unreachableDefinitions: true,
    },
);
if (!generatedProtocolV4Raw.includes(emptyParamsDeclaration)) {
    throw new Error(
        "Generated Studio protocol v4 empty params declaration changed",
    );
}
const openCreationJobDeclaration =
    "export type WorldForgeStudioCreationJobV9 = {\n  [k: string]: unknown;\n} & {";
if (!generatedProtocolV4Raw.includes(openCreationJobDeclaration)) {
    throw new Error(
        "Generated Studio protocol v4 creation-job declaration changed",
    );
}
const openCreationOutputGrantDeclaration =
    "export type WorldForgeStudioCreationOutputGrantV5 = {\n  [k: string]: unknown;\n} & {";
if (!generatedProtocolV4Raw.includes(openCreationOutputGrantDeclaration)) {
    throw new Error(
        "Generated Studio protocol v4 creation-output-grant declaration changed",
    );
}
const generatedProtocolV4 = generatedProtocolV4Raw
    .replace(
        emptyParamsDeclaration,
        "export type EmptyParams = Record<string, never>;",
    )
    .replace(
        openCreationJobDeclaration,
        "export type WorldForgeStudioCreationJobV9 = {",
    )
    .replace(
        openCreationOutputGrantDeclaration,
        "export type WorldForgeStudioCreationOutputGrantV5 = {",
    );
const generatedProtocolV5Raw = await compile(
    protocolV5Schema,
    "StudioProtocolV5Envelope",
    {
        bannerComment:
            "/* AUTO-GENERATED from schemas/studio-protocol-v5.schema.json. Do not edit by hand. */\n" +
            "/* eslint-disable @typescript-eslint/no-empty-object-type */",
        additionalProperties: false,
        cwd: path.dirname(protocolV5SchemaPath),
        style,
        unreachableDefinitions: true,
    },
);
if (!generatedProtocolV5Raw.includes(emptyParamsDeclaration)) {
    throw new Error(
        "Generated Studio protocol v5 empty params declaration changed",
    );
}
const openCreationJobV12Declaration =
    "export type WorldForgeStudioCreationJobV12 = {\n  [k: string]: unknown;\n} & {";
if (!generatedProtocolV5Raw.includes(openCreationJobV12Declaration)) {
    throw new Error(
        "Generated Studio protocol v5 creation-job declaration changed",
    );
}
const openCreationOutputGrantV6Declaration =
    "export type WorldForgeStudioCreationOutputGrantV6 = {\n  [k: string]: unknown;\n} & {";
if (!generatedProtocolV5Raw.includes(openCreationOutputGrantV6Declaration)) {
    throw new Error(
        "Generated Studio protocol v5 creation-output-grant declaration changed",
    );
}
const generatedProtocolV5 = generatedProtocolV5Raw
    .replace(
        emptyParamsDeclaration,
        "export type EmptyParams = Record<string, never>;",
    )
    .replace(
        openCreationJobV12Declaration,
        "export type WorldForgeStudioCreationJobV12 = {",
    )
    .replace(
        openCreationOutputGrantV6Declaration,
        "export type WorldForgeStudioCreationOutputGrantV6 = {",
    );
const contractDefinitions = {
    LegacyIdentityAllowlist: {
        $ref: "legacy-identity-allowlist.schema.json",
    },
    WorldProjectMigrationBackup: {
        $ref: "world-project-migration-backup.schema.json",
    },
    WorldProjectMigrationEvidence: {
        $ref: "world-project-migration-evidence.schema.json",
    },
    WorldProjectMigrationJournal: {
        $ref: "world-project-migration-journal.schema.json",
    },
    CreationProject: { $ref: "creation-project.schema.json" },
    CreationProfile: { $ref: "creation-profile.schema.json" },
    CreationSourceManifest: { $ref: "creation-source-manifest.schema.json" },
    CreationWorkflowStatus: {
        $ref: "creation-workflow-status.schema.json",
    },
    CreationReadiness: { $ref: "creation-readiness.schema.json" },
    CreationHandoff: { $ref: "creation-handoff.schema.json" },
    StudioCreationRootGrant: {
        $ref: "studio-creation-root-grant.schema.json",
    },
    StudioCreationWorkspace: {
        $ref: "studio-creation-workspace.schema.json",
    },
    StudioCreationChangeset: {
        $ref: "studio-creation-changeset.schema.json",
    },
    StudioCreationArtifact: {
        $ref: "studio-creation-artifact.schema.json",
    },
    StudioCreationEvidence: {
        $ref: "studio-creation-evidence.schema.json",
    },
    StudioCreationOutputGrant: {
        $ref: "studio-creation-output-grant.schema.json",
    },
    StudioCreationOutputGrantV6: {
        $ref: "studio-creation-output-grant-v6.schema.json",
    },
    StudioCreationPreview: {
        $ref: "studio-creation-preview.schema.json",
    },
    StudioCreationPreviewV2: {
        $ref: "studio-creation-preview-v2.schema.json",
    },
    StudioCreationJob: {
        $ref: "studio-creation-job.schema.json",
    },
    StudioCreationWorker: {
        $ref: "studio-creation-worker.schema.json",
    },
    WorldModule: { $ref: "world-module.schema.json" },
    ActivityModule: { $ref: "activity-module.schema.json" },
    NarrativeModule: { $ref: "narrative-module.schema.json" },
    SystemModule: { $ref: "system-module.schema.json" },
    LogicModule: { $ref: "logic-module.schema.json" },
    PhaseReportV2: { $ref: "phase-report-v2.schema.json" },
    PhaseReportV3: { $ref: "phase-report-v3.schema.json" },
    Lorepack: { $ref: "lorepack.schema.json" },
    Gamepack: { $ref: "gamepack.schema.json" },
    GameAnalysis: { $ref: "game-analysis.schema.json" },
    MechanicCapabilityLedger: {
        $ref: "mechanic-capability-ledger.schema.json",
    },
    AssetSubject: { $ref: "generic-asset-subject.schema.json" },
    AssetTarget: { $ref: "generic-asset-target.schema.json" },
    AssetStyle: { $ref: "generic-asset-style.schema.json" },
    AssetInventory: { $ref: "generic-asset-inventory.schema.json" },
    AssetSpecification: { $ref: "generic-asset-spec.schema.json" },
    AssetProductionRequest: {
        $ref: "generic-asset-production-request.schema.json",
    },
    AssetProductionReceipt: {
        $ref: "generic-asset-production-receipt.schema.json",
    },
    AssetSelection: { $ref: "generic-asset-selection.schema.json" },
    AssetProvenanceRecord: {
        $ref: "generic-asset-provenance-record.schema.json",
    },
    AssetLicenseRecord: {
        $ref: "generic-asset-license-record.schema.json",
    },
    AssetProcessingRecipe: {
        $ref: "generic-asset-processing-recipe.schema.json",
    },
    AssetProcessingReceipt: {
        $ref: "generic-asset-processing-receipt.schema.json",
    },
    AssetQaReport: {
        $ref: "generic-asset-qa-report.schema.json",
    },
    AssetQaReviewReceipt: {
        $ref: "generic-asset-qa-review-receipt.schema.json",
    },
    AssetManifest: {
        $ref: "generic-asset-manifest.schema.json",
    },
    AssetReleaseAuthority: {
        $ref: "generic-asset-release-authority.schema.json",
    },
    SealedGenericAssetpack: {
        $ref: "generic-assetpack.schema.json",
    },
    RuntimeAdapter: {
        $ref: "generic-runtime-adapter.schema.json",
    },
    RuntimeAdapterRegistry: {
        $ref: "generic-runtime-adapter-registry.schema.json",
    },
    GameRuntimeSnapshot: {
        $ref: "game-runtime-snapshot.schema.json",
    },
    GameRuntimeComposition: {
        $ref: "game-runtime-composition.schema.json",
    },
    RuntimeEvidence: {
        $ref: "generic-runtime-evidence.schema.json",
    },
    RuntimeSupportReport: {
        $ref: "generic-runtime-support-report.schema.json",
    },
    RuntimeSupportAuthority: {
        $ref: "runtime-support-authority.schema.json",
    },
    HostedNativeReleaseAttestationReceipt: {
        $ref: "hosted-native-release-attestation-receipt.schema.json",
    },
    HostedNativeReleaseAuthority: {
        $ref: "hosted-native-release-authority.schema.json",
    },
    GameRuntimeBundle: {
        $ref: "game-runtime-bundle.schema.json",
    },
    RuntimeImplementation: {
        $ref: "runtime-implementation.schema.json",
    },
    RuntimePlatformLock: {
        $ref: "runtime-platform-lock.schema.json",
    },
    GameMaterializationBundle: {
        $ref: "game-materialization-bundle.schema.json",
    },
    GamePackage: {
        $ref: "game-package.schema.json",
    },
    GamePackageExtraction: {
        $ref: "game-package-extraction.schema.json",
    },
    StandaloneGame: {
        $ref: "standalone-game.schema.json",
    },
    StandaloneGameLock: {
        $ref: "standalone-game-lock.schema.json",
    },
    StandalonePlatform: {
        $ref: "standalone-platform.schema.json",
    },
    GameSave: {
        $ref: "game-save.schema.json",
    },
    GameReplay: {
        $ref: "game-replay.schema.json",
    },
    PersistenceGeneration: {
        $ref: "persistence-generation.schema.json",
    },
    GameExecutionScript: {
        $ref: "game-execution-script.schema.json",
    },
    HeadlessExecutionReceipt: {
        $ref: "headless-execution-receipt.schema.json",
    },
    HeadlessEvidenceSet: {
        $ref: "headless-evidence-set.schema.json",
    },
};
const contractsSchema = {
    $id: "https://world-forge.local/schemas/world-forge-contracts.generated.json",
    $schema: "https://json-schema.org/draft/2020-12/schema",
    $defs: contractDefinitions,
    oneOf: Object.keys(contractDefinitions).map((name) => ({
        $ref: `#/$defs/${name}`,
    })),
    title: "World Forge creation contract",
};
const generatedContractsRaw = await compile(
    contractsSchema,
    "WorldForgeContract",
    {
        bannerComment:
            "/* AUTO-GENERATED from additive world-forge.* schemas. Do not edit by hand. */\n" +
            "/* eslint-disable @typescript-eslint/no-empty-object-type */",
        cwd: contractsSchemaRoot,
        style,
        unreachableDefinitions: true,
    },
);
const generatedContractsBase = generatedContractsRaw
    .replace(/^\s*\[k: string\]: unknown;\n/gmu, "")
    .replace("export type Gameplay =", "type GameplayGenerated =")
    .replace("export type Narrative =", "type NarrativeGenerated =")
    .replace("export type World =", "type WorldGenerated =")
    .replace(
        "export type WorldForgeTypedWorldModuleV1 =",
        "type WorldForgeTypedWorldModuleV1Generated =",
    )
    .replace("export type Unit =", "type UnitGenerated =")
    .replace(
        "export interface WorldForgeDeclarativeLogicModuleV1 {",
        "interface WorldForgeDeclarativeLogicModuleV1Generated {",
    )
    .replace(
        "export type WorldForgePhaseReportV2 =",
        "type WorldForgePhaseReportV2Generated =",
    )
    .replace(
        "export type WorldForgeLorepackV1 =",
        "type WorldForgeLorepackV1Generated =",
    )
    .replace(
        "export type LoreNarrativeUnit =",
        "type LoreNarrativeUnitGenerated =",
    )
    .replace(
        "export interface WorldForgeDeterministicGamepackV1 {",
        "interface WorldForgeDeterministicGamepackV1Generated {",
    )
    .replace(
        "export type WorldForgeDeterministicGamepackV1 =",
        "type WorldForgeDeterministicGamepackV1Generated =",
    )
    .replace(
        "export interface WorldForgeDeterministicGameAnalysisV1 {",
        "interface WorldForgeDeterministicGameAnalysisV1Generated {",
    )
    .replace(
        "export type WorldForgeDeterministicGameAnalysisV1 =",
        "type WorldForgeDeterministicGameAnalysisV1Generated =",
    )
    .replace(
        "export type AnalysisRequirements =",
        "type AnalysisRequirementsGenerated =",
    )
    .replace("export type Analyzer =", "type AnalyzerGenerated =")
    .replace("export type Check =", "type CheckGenerated =")
    .replace(
        "export interface WorldForgeMechanicCapabilityLedgerV1 {",
        "interface WorldForgeMechanicCapabilityLedgerV1Generated {",
    )
    .replace(
        "export type WorldForgeMechanicCapabilityLedgerV1 =",
        "type WorldForgeMechanicCapabilityLedgerV1Generated =",
    )
    .replace(
        "export interface WorldForgeAssetSubjectV1 {",
        "interface WorldForgeAssetSubjectV1Generated {",
    )
    .replace(
        "export interface WorldForgeReviewedAssetTargetV1 {",
        "interface WorldForgeReviewedAssetTargetV1Generated {",
    )
    .replace(
        "export interface WorldForgeReviewedAssetStyleV1 {",
        "interface WorldForgeReviewedAssetStyleV1Generated {",
    )
    .replace(
        "export interface WorldForgeDeterministicAssetInventoryV1 {",
        "interface WorldForgeDeterministicAssetInventoryV1Generated {",
    )
    .replace(
        "export interface WorldForgeAssetSpecificationV1 {",
        "interface WorldForgeAssetSpecificationV1Generated {",
    )
    .replace(
        "export type WorldForgeAssetProductionRequestV1 =",
        "type WorldForgeAssetProductionRequestV1Generated =",
    )
    .replace(
        "export type WorldForgeAssetProductionReceiptV1 =",
        "type WorldForgeAssetProductionReceiptV1Generated =",
    )
    .replace(
        "export type WorldForgeSelectedAssetProvenanceRecordV1 =",
        "type WorldForgeSelectedAssetProvenanceRecordV1Generated =",
    )
    .replace(
        "export type WorldForgeRuntimeSafeAssetLicenseRecordV1 =",
        "type WorldForgeRuntimeSafeAssetLicenseRecordV1Generated =",
    );
function boundedAnalysisCheckTuple(targetType) {
    return Array.from({ length: 32 }, (_, index) => {
        const prefix = Array.from({ length: index }, () => "Check").join(", ");
        const suffix = Array.from({ length: 31 - index }, () => "Check?").join(
            ", ",
        );
        return `  | [${[prefix, targetType, suffix].filter(Boolean).join(", ")}]`;
    }).join("\n");
}
const analysisChecks = `[Check${", Check?".repeat(31)}]`;
const analysisChecksAllPassed = `[GameAnalysisPassedCheck${", GameAnalysisPassedCheck?".repeat(31)}]`;
const analysisChecksAllNotApplicable = `[GameAnalysisNotApplicableCheck${", GameAnalysisNotApplicableCheck?".repeat(31)}]`;
const analysisChecksWithFailed = boundedAnalysisCheckTuple(
    "GameAnalysisFailedCheck",
);
const analysisChecksWithInconclusive = boundedAnalysisCheckTuple(
    "GameAnalysisInconclusiveCheck",
);
const generatedContracts = `${generatedContractsBase}

type GenericAssetRuntimeForbiddenFieldName =
  | "absolute_path"
  | "authoring_path"
  | "callback"
  | "command"
  | "credential"
  | "credentials"
  | "endpoint"
  | "executable"
  | "executable_script"
  | "expression"
  | "import"
  | "javascript"
  | "manual_assets"
  | "model"
  | "model_id"
  | "mutable_path"
  | "native_code"
  | "project_path"
  | "prompt"
  | "provider"
  | "provider_credentials"
  | "provider_details"
  | "provider_id"
  | "python"
  | "runtime_ai"
  | "script"
  | "source_path"
  | "token"
  | "tool"
  | "url";
type GenericAssetEnvelopeForbiddenFieldName =
  | GenericAssetRuntimeForbiddenFieldName
  | "receipt";
type GenericAssetRuntimeForbiddenFields = {
  [Field in GenericAssetRuntimeForbiddenFieldName]?: never;
};
type GenericAssetEnvelopeForbiddenFields = {
  [Field in GenericAssetEnvelopeForbiddenFieldName]?: never;
};
type GenericAssetKnownFieldGuard<Value> =
  Value extends readonly unknown[]
    ? { [Index in keyof Value]: GenericAssetKnownFieldGuard<Value[Index]> }
    : Value extends object
      ? {
          [Key in keyof Value]: GenericAssetKnownFieldGuard<Value[Key]>;
        } & GenericAssetEnvelopeForbiddenFields
      : Value;
type GenericAssetRuntimeEnvelopeGuard<Value> =
  Value extends readonly unknown[]
    ? { [Index in keyof Value]: GenericAssetKnownFieldGuard<Value[Index]> }
    : Value extends object
      ? {
          [Key in keyof Value]: GenericAssetKnownFieldGuard<Value[Key]>;
        } & GenericAssetRuntimeForbiddenFields
      : Value;
/**
 * Raw structural shapes generated from JSON Schema. TypeScript cannot enforce
 * arbitrary additionalProperties:false keys on pre-bound values. Studio call
 * boundaries must use validateGenericAssetContract before treating a value as
 * a ValidatedGenericAssetContract.
 */
export type WorldForgeAssetSubjectV1 =
  GenericAssetKnownFieldGuard<WorldForgeAssetSubjectV1Generated>;
export type WorldForgeReviewedAssetTargetV1 =
  GenericAssetKnownFieldGuard<WorldForgeReviewedAssetTargetV1Generated>;
type AssetStyleGeneratedAudio =
  WorldForgeReviewedAssetStyleV1Generated["audio"];
type AssetStyleNotApplicableAudio =
  GenericAssetKnownFieldGuard<
    Extract<AssetStyleGeneratedAudio, { status: "not_applicable" }>
  > & {
    role_direction?: never;
    mix_direction?: never;
    music_direction?: never;
    sfx_direction?: never;
    voice_direction?: never;
    caption_direction?: never;
    runtime_formats?: never;
  };
type AssetStyleDefinedAudio =
  GenericAssetKnownFieldGuard<
    Extract<AssetStyleGeneratedAudio, { status: "defined" }>
  > & {
    rationale?: never;
  };
export type WorldForgeReviewedAssetStyleV1 =
  Omit<
    GenericAssetKnownFieldGuard<WorldForgeReviewedAssetStyleV1Generated>,
    "audio"
  > & {
    audio: AssetStyleNotApplicableAudio | AssetStyleDefinedAudio;
  };
export type WorldForgeDeterministicAssetInventoryV1 =
  GenericAssetKnownFieldGuard<WorldForgeDeterministicAssetInventoryV1Generated>;
export type WorldForgeAssetSpecificationV1 =
  GenericAssetKnownFieldGuard<WorldForgeAssetSpecificationV1Generated>;
type AssetProductionClass =
  | "human"
  | "procedural_offline"
  | "external_authoring"
  | "generative_authoring";
type CorrelatedProductionToolchain<
  Toolchain,
  ProductionClass extends AssetProductionClass,
> = Extract<Toolchain, { production_class: ProductionClass }>;
type AssetProductionRequestBase = Omit<
  WorldForgeAssetProductionRequestV1Generated,
  "production_class" | "reproducibility" | "toolchain_requirements"
>;
type AssetProductionReproducibility =
  WorldForgeAssetProductionRequestV1Generated["reproducibility"];
type AssetProductionReproducibilityWithSeedPolicy<
  SeedPolicy extends AssetProductionReproducibility["seed_policy"],
> = Omit<AssetProductionReproducibility, "seed_policy"> & {
  seed_policy: SeedPolicy;
};
type HumanOrExternalAssetProductionRequestVariant<
  ProductionClass extends "human" | "external_authoring",
> = {
  production_class: ProductionClass;
  reproducibility: AssetProductionReproducibilityWithSeedPolicy<"forbidden">;
  toolchain_requirements: CorrelatedProductionToolchain<
    WorldForgeAssetProductionRequestV1Generated["toolchain_requirements"],
    ProductionClass
  >;
};
type ProceduralAssetProductionToolchain = CorrelatedProductionToolchain<
  WorldForgeAssetProductionRequestV1Generated["toolchain_requirements"],
  "procedural_offline"
>;
type ProceduralAssetProductionRequestVariant =
  | {
      production_class: "procedural_offline";
      reproducibility: AssetProductionReproducibilityWithSeedPolicy<"fixed">;
      toolchain_requirements: Omit<ProceduralAssetProductionToolchain, "seed"> & {
        seed: number;
      };
    }
  | {
      production_class: "procedural_offline";
      reproducibility: AssetProductionReproducibilityWithSeedPolicy<"forbidden">;
      toolchain_requirements: Omit<ProceduralAssetProductionToolchain, "seed"> & {
        seed: null;
      };
    }
  | {
      production_class: "procedural_offline";
      reproducibility: AssetProductionReproducibilityWithSeedPolicy<"recorded">;
      toolchain_requirements: Omit<ProceduralAssetProductionToolchain, "seed"> & {
        seed: number;
      };
    };
type GenerativeAssetProductionToolchain = CorrelatedProductionToolchain<
  WorldForgeAssetProductionRequestV1Generated["toolchain_requirements"],
  "generative_authoring"
>;
type GenerativeAssetProductionRequestVariant =
  | {
      production_class: "generative_authoring";
      reproducibility: AssetProductionReproducibilityWithSeedPolicy<"fixed">;
      toolchain_requirements: Omit<GenerativeAssetProductionToolchain, "seed_policy"> & {
        seed_policy: "fixed";
      };
    }
  | {
      production_class: "generative_authoring";
      reproducibility: AssetProductionReproducibilityWithSeedPolicy<"recorded">;
      toolchain_requirements: Omit<GenerativeAssetProductionToolchain, "seed_policy"> & {
        seed_policy: "recorded";
      };
    };
type AssetProductionRequestVariant =
  | HumanOrExternalAssetProductionRequestVariant<"human">
  | HumanOrExternalAssetProductionRequestVariant<"external_authoring">
  | ProceduralAssetProductionRequestVariant
  | GenerativeAssetProductionRequestVariant;
export type WorldForgeAssetProductionRequestV1 =
  AssetProductionRequestBase & AssetProductionRequestVariant;
type AssetProductionReceiptBase = Omit<
  WorldForgeAssetProductionReceiptV1Generated,
  "production_class" | "executed_toolchain" | "status" | "outputs" | "failure_reasons"
>;
type AssetProductionReceiptVariant<ProductionClass extends AssetProductionClass> = {
  production_class: ProductionClass;
  executed_toolchain: CorrelatedProductionToolchain<
    WorldForgeAssetProductionReceiptV1Generated["executed_toolchain"],
    ProductionClass
  >;
};
type AssetProductionReceiptOutput =
  WorldForgeAssetProductionReceiptV1Generated["outputs"][number];
type AssetProductionReceiptFailureReason =
  WorldForgeAssetProductionReceiptV1Generated["failure_reasons"][number];
type AssetProductionReceiptStatusVariant =
  | {
      status: "completed";
      outputs: [
        AssetProductionReceiptOutput,
        AssetProductionReceiptOutput?,
        AssetProductionReceiptOutput?,
        AssetProductionReceiptOutput?,
      ];
      failure_reasons: [];
    }
  | {
      status: "failed";
      outputs: [];
      failure_reasons: [
        AssetProductionReceiptFailureReason,
        ...AssetProductionReceiptFailureReason[],
      ];
    };
export type WorldForgeAssetProductionReceiptV1 =
  AssetProductionReceiptBase &
  {
    [ProductionClass in AssetProductionClass]: AssetProductionReceiptVariant<ProductionClass>;
  }[AssetProductionClass] &
  AssetProductionReceiptStatusVariant;
type AssetProvenanceBase = Omit<
  WorldForgeSelectedAssetProvenanceRecordV1Generated,
  "production_class" | "toolchain"
>;
type AssetProvenanceVariant<ProductionClass extends AssetProductionClass> = {
  production_class: ProductionClass;
  toolchain: CorrelatedProductionToolchain<
    WorldForgeSelectedAssetProvenanceRecordV1Generated["toolchain"],
    ProductionClass
  >;
};
export type WorldForgeSelectedAssetProvenanceRecordV1 =
  AssetProvenanceBase &
  {
    [ProductionClass in AssetProductionClass]: AssetProvenanceVariant<ProductionClass>;
  }[AssetProductionClass];
type AssetLicenseRecordBase = Omit<
  GenericAssetRuntimeEnvelopeGuard<WorldForgeRuntimeSafeAssetLicenseRecordV1Generated>,
  "copyright" | "license_basis"
>;
type AssetLicenseCopyrightBase = Omit<
  GenericAssetKnownFieldGuard<
    WorldForgeRuntimeSafeAssetLicenseRecordV1Generated["copyright"]
  >,
  "year" | "year_policy"
>;
type AssetLicenseCopyright =
  | (AssetLicenseCopyrightBase & {
      year_policy: "fixed";
      year: number;
    })
  | (AssetLicenseCopyrightBase & {
      year_policy: "not_applicable";
      year: null;
    });
type AssetLicenseBasisBase = Omit<
  GenericAssetKnownFieldGuard<
    WorldForgeRuntimeSafeAssetLicenseRecordV1Generated["license_basis"]
  >,
  "identifier" | "kind"
>;
type AssetLicenseBasis =
  | (AssetLicenseBasisBase & {
      kind: "custom";
      identifier: "LicenseRef-WorldForge-Fixture-Public-Domain";
    })
  | (AssetLicenseBasisBase & {
      kind: "spdx";
      identifier: string;
    });
export type WorldForgeRuntimeSafeAssetLicenseRecordV1 =
  AssetLicenseRecordBase & {
    copyright: AssetLicenseCopyright;
    license_basis: AssetLicenseBasis;
  };

type GamepackForbiddenRuntimeFieldName =
  | "runtime_ai"
  | "script"
  | "expression"
  | "callback"
  | "command"
  | "import"
  | "module"
  | "native_code"
  | "executable"
  | "executable_script"
  | "prompt"
  | "provider"
  | "provider_id"
  | "provider_credentials"
  | "provider_details"
  | "model"
  | "model_id"
  | "tool"
  | "credential"
  | "credentials"
  | "token"
  | "endpoint"
  | "authoring_path"
  | "mutable_path"
  | "project_path"
  | "source_path"
  | "path"
  | "absolute_path"
  | "python"
  | "javascript";
type GamepackForbiddenRuntimeFields = {
  [Field in GamepackForbiddenRuntimeFieldName]?: never;
};
type GamepackDeepClosed<Value> =
  Value extends readonly unknown[]
    ? { [Index in keyof Value]: GamepackDeepClosed<Value[Index]> }
    : Value extends object
      ? {
          [Key in keyof Value]: Key extends GamepackForbiddenRuntimeFieldName
            ? never
            : GamepackDeepClosed<Value[Key]>;
        } & GamepackForbiddenRuntimeFields
      : Value;
type GamepackSourceOwned = GamepackForbiddenRuntimeFields & {
  compiler_owned?: never;
};
type GamepackSourceIdentity = GamepackSourceOwned & {
  format:
    | "world-forge.project"
    | "world-forge.creation_profile"
    | "world-forge.creation_source_manifest"
    | "world-forge.world_module"
    | "world-forge.activity_module"
    | "world-forge.narrative_module"
    | "world-forge.system_module"
    | "world-forge.logic_module"
    | "world-forge.gamepack";
  format_version: 1;
  id: string;
  content_hash: string;
};
type GamepackOperand =
  | (GamepackSourceOwned & {
      kind: "literal";
      value_type: "boolean";
      value: boolean;
      state_id?: never;
      action_id?: never;
      parameter_id?: never;
    })
  | (GamepackSourceOwned & {
      kind: "literal";
      value_type: "integer";
      value: number;
      state_id?: never;
      action_id?: never;
      parameter_id?: never;
    })
  | (GamepackSourceOwned & {
      kind: "literal";
      value_type: "string";
      value: string;
      state_id?: never;
      action_id?: never;
      parameter_id?: never;
    })
  | (GamepackSourceOwned & {
      kind: "literal";
      value_type: "string_array";
      value: string[];
      state_id?: never;
      action_id?: never;
      parameter_id?: never;
    })
  | (GamepackSourceOwned & {
      kind: "state";
      state_id: string;
      value_type?: never;
      value?: never;
      action_id?: never;
      parameter_id?: never;
    })
  | (GamepackSourceOwned & {
      kind: "parameter";
      action_id: string;
      parameter_id: string;
      state_id?: never;
      value_type?: never;
      value?: never;
    });
type GamepackConditionCommon = GamepackSourceOwned & {
  id: string;
  action_id: string | null;
};
type GamepackCondition =
  | (GamepackConditionCommon & {
      operator: "constant";
      value: boolean;
      comparison?: never;
      left?: never;
      right?: never;
      condition_ids?: never;
      condition_id?: never;
      array_state_id?: never;
      index?: never;
      distance?: never;
    })
  | (GamepackConditionCommon & {
      operator: "compare";
      comparison:
        | "equal"
        | "not_equal"
        | "less_than"
        | "less_or_equal"
        | "greater_than"
        | "greater_or_equal";
      left: GamepackOperand;
      right: GamepackOperand;
      value?: never;
      condition_ids?: never;
      condition_id?: never;
      array_state_id?: never;
      index?: never;
      distance?: never;
    })
  | (GamepackConditionCommon & {
      operator: "all" | "any";
      condition_ids: [string, ...string[]];
      value?: never;
      comparison?: never;
      left?: never;
      right?: never;
      condition_id?: never;
      array_state_id?: never;
      index?: never;
      distance?: never;
    })
  | (GamepackConditionCommon & {
      operator: "not";
      condition_id: string;
      value?: never;
      comparison?: never;
      left?: never;
      right?: never;
      condition_ids?: never;
      array_state_id?: never;
      index?: never;
      distance?: never;
    })
  | (GamepackConditionCommon & {
      action_id: string;
      operator: "index_valid";
      array_state_id: string;
      index: GamepackOperand;
      value?: never;
      comparison?: never;
      left?: never;
      right?: never;
      condition_ids?: never;
      condition_id?: never;
      distance?: never;
    })
  | (GamepackConditionCommon & {
      action_id: string;
      operator: "integer_distance";
      left: GamepackOperand;
      right: GamepackOperand;
      distance: number;
      value?: never;
      comparison?: never;
      condition_ids?: never;
      condition_id?: never;
      array_state_id?: never;
      index?: never;
    });
type GamepackEffectCommon = GamepackSourceOwned & {
  id: string;
  action_id: string;
  invalid_transition_policy: "reject_transition";
};
type GamepackEffect =
  | (GamepackEffectCommon & {
      operation: "set";
      state_id: string;
      value: GamepackOperand;
      array_state_id?: never;
      first_index?: never;
      second_index?: never;
      amount?: never;
    })
  | (GamepackEffectCommon & {
      operation: "swap_array_items";
      array_state_id: string;
      first_index: GamepackOperand;
      second_index: GamepackOperand;
      state_id?: never;
      value?: never;
      amount?: never;
    })
  | (GamepackEffectCommon & {
      operation: "append_unique";
      array_state_id: string;
      value: GamepackOperand;
      state_id?: never;
      first_index?: never;
      second_index?: never;
      amount?: never;
    })
  | (GamepackEffectCommon & {
      operation: "increment";
      state_id: string;
      amount: GamepackOperand;
      array_state_id?: never;
      value?: never;
      first_index?: never;
      second_index?: never;
    })
  | (GamepackEffectCommon & {
      operation: "reset";
      state_id: string;
      array_state_id?: never;
      value?: never;
      first_index?: never;
      second_index?: never;
      amount?: never;
    });
type GamepackSourceStateCommon = GamepackSourceOwned & {
  id: string;
  mutability: "mutable" | "constant";
  persistence: "saved" | "transient";
};
type GamepackSourceState =
  | (GamepackSourceStateCommon & {
      type: "boolean";
      initial: boolean;
      minimum?: never;
      maximum?: never;
      allowed_values?: never;
      min_items?: never;
      max_items?: never;
    })
  | (GamepackSourceStateCommon & {
      type: "integer";
      initial: number;
      minimum: number;
      maximum: number;
      allowed_values?: never;
      min_items?: never;
      max_items?: never;
    })
  | (GamepackSourceStateCommon & {
      type: "string";
      initial: string;
      allowed_values: [string, ...string[]];
      minimum?: never;
      maximum?: never;
      min_items?: never;
      max_items?: never;
    })
  | (GamepackSourceStateCommon & {
      type: "string_array";
      initial: string[];
      allowed_values: [string, ...string[]];
      min_items: number;
      max_items: number;
      minimum?: never;
      maximum?: never;
    });
type GamepackParameter =
  | (GamepackSourceOwned & {
      id: string;
      type: "boolean";
      minimum?: never;
      maximum?: never;
      allowed_values?: never;
      min_items?: never;
      max_items?: never;
    })
  | (GamepackSourceOwned & {
      id: string;
      type: "integer";
      minimum: number;
      maximum: number;
      allowed_values?: never;
      min_items?: never;
      max_items?: never;
    })
  | (GamepackSourceOwned & {
      id: string;
      type: "string";
      allowed_values: [string, ...string[]];
      minimum?: never;
      maximum?: never;
      min_items?: never;
      max_items?: never;
    })
  | (GamepackSourceOwned & {
      id: string;
      type: "string_array";
      allowed_values: [string, ...string[]];
      min_items: number;
      max_items: number;
      minimum?: never;
      maximum?: never;
    });
type GamepackSourceBinding =
  | (GamepackSourceOwned & {
      kind: "activity" | "system";
      source_id: string;
      option_id?: never;
    })
  | (GamepackSourceOwned & {
      kind: "narrative_option";
      source_id: string;
      option_id: string;
    });
type GamepackAction = GamepackSourceOwned & {
  id: string;
  core_verb_id: string;
  parameters: GamepackParameter[];
  source_bindings: [GamepackSourceBinding, ...GamepackSourceBinding[]];
  rule_ids: [string, ...string[]];
  presentation_hook_ids: string[];
  required_feature_ids: [string, ...string[]];
};
type GamepackCoreVerb = GamepackSourceOwned & {
  id: string;
  description: string;
};
type GamepackRule = GamepackSourceOwned & {
  id: string;
  action_id: string;
  order: number;
  condition_ids: string[];
  effect_ids: [string, ...string[]];
  event_ids: string[];
};
type GamepackGoal = GamepackSourceOwned & {
  id: string;
  condition_ids: [string, ...string[]];
  success_ending_id: string;
};
type GamepackFailure = GamepackSourceOwned & {
  id: string;
  condition_ids: [string, ...string[]];
  recovery_action_ids: [string, ...string[]];
};
type GamepackEnding = GamepackSourceOwned & {
  id: string;
  kind: "success" | "failure" | "neutral";
  condition_ids: [string, ...string[]];
  event_ids: string[];
  presentation_hook_ids: [string, ...string[]];
};
type GamepackEvent = GamepackSourceOwned & {
  id: string;
};
type GamepackPresentationHook = GamepackSourceOwned & {
  id: string;
  kind: "board" | "text" | "feedback" | "ending";
  asset_binding_ids: [string, ...string[]];
};
type GamepackMechanic = GamepackSourceOwned & {
  id: string;
  core_verb_id: string;
  action_id: string;
  authoritative_state_ids: [string, ...string[]];
  condition_ids: string[];
  rule_ids: [string, ...string[]];
  effect_ids: [string, ...string[]];
  event_ids: string[];
  presentation_hook_ids: [string, ...string[]];
  asset_binding_ids: [string, ...string[]];
  required_feature_ids: [string, ...string[]];
};
type GamepackInternalCursor = GamepackForbiddenRuntimeFields & {
  compiler_owned: true;
  id: "wf_internal_narrative_cursor";
  type: "string";
  initial: string;
  allowed_values: [string, ...string[]];
  mutability: "mutable";
  persistence: "saved";
};
type GamepackNarrativeTransitionPrecondition =
  GamepackForbiddenRuntimeFields & {
    compiler_owned: true;
    id: string;
    operator: "cursor_equals";
    cursor_state_id: "wf_internal_narrative_cursor";
    value: string;
    action_id?: never;
    comparison?: never;
    left?: never;
    right?: never;
    condition_ids?: never;
    condition_id?: never;
    array_state_id?: never;
    index?: never;
    distance?: never;
  };
type GamepackNarrativeTransitionEffect = GamepackForbiddenRuntimeFields & {
  compiler_owned: true;
  id: string;
  operation: "set_cursor";
  cursor_state_id: "wf_internal_narrative_cursor";
  value: string;
  invalid_transition_policy: "reject_transition";
  action_id?: never;
  state_id?: never;
  array_state_id?: never;
  first_index?: never;
  second_index?: never;
  amount?: never;
};
type GamepackNarrativeTransition = GamepackForbiddenRuntimeFields & {
  compiler_owned: true;
  id: string;
  action_id: string;
  source_unit_id: string;
  option_id: string;
  target_unit_id: string;
  precondition: GamepackNarrativeTransitionPrecondition;
  effect: GamepackNarrativeTransitionEffect;
  atomic_source_condition_ids: string[];
  atomic_source_effect_ids: [string, ...string[]];
};
type GamepackLogicCommon = GamepackForbiddenRuntimeFields & {
  source: GamepackSourceIdentity;
  title: string;
  initial_state: Record<string, boolean | number | string | string[]>;
  core_verbs: [GamepackCoreVerb, ...GamepackCoreVerb[]];
  actions: [GamepackAction, ...GamepackAction[]];
  conditions: [GamepackCondition, ...GamepackCondition[]];
  effects: [GamepackEffect, ...GamepackEffect[]];
  rules: [GamepackRule, ...GamepackRule[]];
  goals: [GamepackGoal, ...GamepackGoal[]];
  failures: GamepackFailure[];
  endings: [GamepackEnding, ...GamepackEnding[]];
  events: [GamepackEvent, ...GamepackEvent[]];
  presentation_hooks: [
    GamepackPresentationHook,
    ...GamepackPresentationHook[],
  ];
  mechanics: [GamepackMechanic, ...GamepackMechanic[]];
};
type GamepackLogic =
  | (GamepackLogicCommon & {
      narrative_cursor: null;
      narrative_transitions: [];
      state_schema: [GamepackSourceState, ...GamepackSourceState[]];
    })
  | (GamepackLogicCommon & {
      narrative_cursor: GamepackInternalCursor;
      narrative_transitions: GamepackNarrativeTransition[];
      state_schema: [
        GamepackSourceState,
        ...GamepackSourceState[],
        GamepackInternalCursor,
      ];
    });
type GamepackWorldRecord =
  | (GamepackSourceOwned & {
      id: string;
      statement: string;
      status: "canon" | "provisional";
      sequence?: never;
      summary?: never;
      name?: never;
      topology?: never;
      group_type?: never;
      role?: never;
      access?: never;
    })
  | (GamepackSourceOwned & {
      id: string;
      sequence: number;
      summary: string;
      statement?: never;
      status?: never;
      name?: never;
      topology?: never;
      group_type?: never;
      role?: never;
      access?: never;
    })
  | (GamepackSourceOwned & {
      id: string;
      name: string;
      topology: "abstract" | "symbolic" | "diegetic";
      statement?: never;
      status?: never;
      sequence?: never;
      summary?: never;
      group_type?: never;
      role?: never;
      access?: never;
    })
  | (GamepackSourceOwned & {
      id: string;
      name: string;
      group_type: string;
      statement?: never;
      status?: never;
      sequence?: never;
      summary?: never;
      topology?: never;
      role?: never;
      access?: never;
    })
  | (GamepackSourceOwned & {
      id: string;
      name: string;
      role: string;
      statement?: never;
      status?: never;
      sequence?: never;
      summary?: never;
      topology?: never;
      group_type?: never;
      access?: never;
    })
  | (GamepackSourceOwned & {
      id: string;
      statement: string;
      access: "public" | "restricted" | "secret";
      status?: never;
      sequence?: never;
      summary?: never;
      name?: never;
      topology?: never;
      group_type?: never;
      role?: never;
    });
type GamepackWorldProjectionBase = GamepackSourceOwned & {
  source: GamepackSourceIdentity;
  title: string;
};
type GamepackWorldProjection =
  | (GamepackWorldProjectionBase & {
      module_type: "canon";
      records: [
        Extract<GamepackWorldRecord, { status: "canon" | "provisional" }>,
        ...Extract<
          GamepackWorldRecord,
          { status: "canon" | "provisional" }
        >[],
      ];
    })
  | (GamepackWorldProjectionBase & {
      module_type: "chronology";
      records: [
        Extract<GamepackWorldRecord, { sequence: number }>,
        ...Extract<GamepackWorldRecord, { sequence: number }>[],
      ];
    })
  | (GamepackWorldProjectionBase & {
      module_type: "space";
      records: [
        Extract<GamepackWorldRecord, { topology: "abstract" | "symbolic" | "diegetic" }>,
        ...Extract<
          GamepackWorldRecord,
          { topology: "abstract" | "symbolic" | "diegetic" }
        >[],
      ];
    })
  | (GamepackWorldProjectionBase & {
      module_type: "group";
      records: [
        Extract<GamepackWorldRecord, { group_type: string }>,
        ...Extract<GamepackWorldRecord, { group_type: string }>[],
      ];
    })
  | (GamepackWorldProjectionBase & {
      module_type: "character";
      records: [
        Extract<GamepackWorldRecord, { role: string }>,
        ...Extract<GamepackWorldRecord, { role: string }>[],
      ];
    })
  | (GamepackWorldProjectionBase & {
      module_type: "knowledge";
      records: [
        Extract<GamepackWorldRecord, { access: "public" | "restricted" | "secret" }>,
        ...Extract<
          GamepackWorldRecord,
          { access: "public" | "restricted" | "secret" }
        >[],
      ];
    });
type GamepackActivity = GamepackSourceOwned & {
  id: string;
  activity_type:
    | "level"
    | "mission"
    | "quest"
    | "scenario"
    | "match"
    | "race"
    | "puzzle"
    | "encounter"
    | "contract"
    | "expedition"
    | "run"
    | "tutorial"
    | "challenge";
  title: string;
  participant_ids: string[];
  spatial_context_ids: string[];
  start_condition_ids: string[];
  end_condition_ids: string[];
  success_condition_ids: string[];
  failure_condition_ids: string[];
  effect_ids: string[];
  event_ids: string[];
  presentation_hook_ids: string[];
  asset_binding_ids: string[];
};
type GamepackActivityProjection = GamepackSourceOwned & {
  source: GamepackSourceIdentity;
  title: string;
  activities: [GamepackActivity, ...GamepackActivity[]];
};
type GamepackNarrativeChoiceOption = GamepackSourceOwned & {
  id: string;
  label: string;
  next_unit_id: string;
  condition_ids: string[];
  effect_ids: string[];
};
type GamepackNarrativeUnitCommon = GamepackSourceOwned & {
  id: string;
  title: string;
  prerequisite_ids: string[];
  effect_ids: string[];
  next_unit_ids: string[];
  asset_binding_ids: string[];
};
type GamepackNarrativeUnit =
  | (GamepackNarrativeUnitCommon & {
      unit_type:
        | "arc"
        | "beat"
        | "scene"
        | "dialogue"
        | "storylet"
        | "clue"
        | "reveal"
        | "memory"
        | "episode";
      options?: never;
      ending_kind?: never;
    })
  | (GamepackNarrativeUnitCommon & {
      unit_type: "choice";
      options: [
        GamepackNarrativeChoiceOption,
        GamepackNarrativeChoiceOption,
        ...GamepackNarrativeChoiceOption[],
      ];
      ending_kind?: never;
    })
  | (Omit<GamepackNarrativeUnitCommon, "next_unit_ids"> & {
      unit_type: "ending";
      next_unit_ids: [];
      ending_kind: "success" | "failure" | "neutral";
      options?: never;
    });
type GamepackNarrativeProjection = GamepackSourceOwned & {
  source: GamepackSourceIdentity;
  title: string;
  entry_unit_ids: [string, ...string[]];
  units: [GamepackNarrativeUnit, ...GamepackNarrativeUnit[]];
};
type GamepackSystem = GamepackSourceOwned & {
  id: string;
  system_type:
    | "rule"
    | "event"
    | "consequence"
    | "schedule"
    | "economy"
    | "production_process"
    | "simulation_scenario"
    | "world_modifier"
    | "season";
  title: string;
  precondition_ids: string[];
  effect_ids: string[];
  event_ids: string[];
  asset_binding_ids: string[];
};
type GamepackSystemProjection = GamepackSourceOwned & {
  source: GamepackSourceIdentity;
  title: string;
  systems: [GamepackSystem, ...GamepackSystem[]];
};
type GamepackModulesCommon = GamepackForbiddenRuntimeFields & {
  world: GamepackWorldProjection[];
  activities: GamepackActivityProjection[];
  systems: GamepackSystemProjection[];
};
type GamepackNarrativeFreeModules = GamepackModulesCommon & {
  narrative: [];
};
type GamepackNarrativeModules = GamepackModulesCommon & {
  narrative: [GamepackNarrativeProjection, ...GamepackNarrativeProjection[]];
};
type GamepackAuthoredNarrativeUnit = Exclude<
  GamepackNarrativeProjection["units"][number],
  { unit_type: "choice" }
>;
type GamepackAuthoredNarrativeProjection = Omit<
  GamepackNarrativeProjection,
  "units"
> & {
  units: [GamepackAuthoredNarrativeUnit, ...GamepackAuthoredNarrativeUnit[]];
};
type GamepackAuthoredNarrativeModules = GamepackModulesCommon & {
  narrative: [
    GamepackAuthoredNarrativeProjection,
    ...GamepackAuthoredNarrativeProjection[],
  ];
};
type GamepackGeneratedBase = Omit<
  WorldForgeDeterministicGamepackV1Generated,
  "logic" | "modules"
>;
type GamepackGeneratedClosed =
  | (GamepackGeneratedBase & {
      logic: Extract<GamepackLogic, { narrative_cursor: null }>;
      modules: GamepackNarrativeFreeModules;
    })
  | (GamepackGeneratedBase & {
      logic: Extract<GamepackLogic, { narrative_cursor: null }>;
      modules: GamepackAuthoredNarrativeModules;
    })
  | (GamepackGeneratedBase & {
      logic: Extract<
        GamepackLogic,
        { narrative_cursor: { compiler_owned: true } }
      >;
      modules: GamepackNarrativeModules;
    });
export type WorldForgeDeterministicGamepackV1 =
  GamepackDeepClosed<GamepackGeneratedClosed>;

type GameAnalysisRequirementCommon = Omit<
  AnalysisRequirementsGenerated,
  "profile" | "analyzer_id" | "analyzer_version" | "reason_code"
> & {
  analyzer_version: 1;
};
export type AnalysisRequirements =
  | (GameAnalysisRequirementCommon & {
      profile: "abstract_puzzle";
      analyzer_id: "worldforge.abstract_puzzle_exhaustive";
      reason_code: null;
    })
  | (GameAnalysisRequirementCommon & {
      profile: "branching_narrative";
      analyzer_id: "worldforge.branching_narrative_exhaustive";
      reason_code: null;
    })
  | (GameAnalysisRequirementCommon & {
      profile: "unsupported";
      analyzer_id: "worldforge.unsupported_profile";
      reason_code: "analysis_profile_unsupported";
    });
export type Analyzer =
  | {
      profile: "abstract_puzzle";
      id: "worldforge.abstract_puzzle_exhaustive";
      version: 1;
    }
  | {
      profile: "branching_narrative";
      id: "worldforge.branching_narrative_exhaustive";
      version: 1;
    }
  | {
      profile: "unsupported";
      id: "worldforge.unsupported_profile";
      version: 1;
    };
type GameAnalysisCheckCommon = Omit<CheckGenerated, "status" | "reason_codes">;
export type Check =
  | (GameAnalysisCheckCommon & {
      status: "passed";
      reason_codes: [];
    })
  | (GameAnalysisCheckCommon & {
      status: "failed";
      reason_codes: [string, ...string[]];
    })
  | (GameAnalysisCheckCommon & {
      status: "inconclusive";
      reason_codes: [string, ...string[]];
    })
  | (GameAnalysisCheckCommon & {
      status: "not_applicable";
      reason_codes: string[];
    });
type GameAnalysisPassedCheck = Extract<Check, { status: "passed" }>;
type GameAnalysisFailedCheck = Extract<Check, { status: "failed" }>;
type GameAnalysisInconclusiveCheck = Extract<Check, { status: "inconclusive" }>;
type GameAnalysisNotApplicableCheck = Extract<
  Check,
  { status: "not_applicable" }
>;
type GameAnalysisChecks = ${analysisChecks};
type GameAnalysisChecksAllPassed = ${analysisChecksAllPassed};
type GameAnalysisChecksAllNotApplicable = ${analysisChecksAllNotApplicable};
type GameAnalysisChecksWithFailed =
${analysisChecksWithFailed};
type GameAnalysisChecksWithInconclusive =
${analysisChecksWithInconclusive};
type GameAnalysisMetrics =
  WorldForgeDeterministicGameAnalysisV1Generated["metrics"];
type GameAnalysisGeneratedClosed = Omit<
  WorldForgeDeterministicGameAnalysisV1Generated,
  | "analyzer"
  | "requirement"
  | "status"
  | "reason_codes"
  | "checks"
  | "findings"
  | "metrics"
  | "assumptions"
  | "false_positive_risks"
  | "false_negative_risks"
  | "out_of_scope_claims"
> & {
  assumptions: [
    "The validated gamepack is the complete authoritative logic input.",
    "Action parameter domains are finite and exactly declared by the gamepack.",
    "Array order is authoritative and state equality uses compact canonical JSON.",
  ];
  false_positive_risks: [
    "A modeled state may be reachable but unusable in an unmodeled presentation adapter.",
  ];
  false_negative_risks: [
    "Any configured bound reached before frontier closure makes the result inconclusive.",
  ];
  out_of_scope_claims: [
    "asset_readability",
    "native_adapter_execution",
    "platform_performance",
    "save_replay_serialization",
    "timing_and_input_ux",
  ];
};
type GameAnalysisSupportedProfile =
  | {
      analyzer: Extract<Analyzer, { profile: "abstract_puzzle" }>;
      requirement: Extract<
        AnalysisRequirements,
        { profile: "abstract_puzzle" }
      >;
    }
  | {
      analyzer: Extract<Analyzer, { profile: "branching_narrative" }>;
      requirement: Extract<
        AnalysisRequirements,
        { profile: "branching_narrative" }
      >;
    };
type GameAnalysisSupportedEvidence =
  | {
      status: "passed";
      reason_codes: [];
      checks: GameAnalysisChecksAllPassed;
      findings: [];
      metrics: GameAnalysisMetrics & { frontier_closed: true };
    }
  | {
      status: "failed";
      reason_codes: [string, ...string[]];
      checks: GameAnalysisChecks;
      findings: [Finding, ...Finding[]];
      metrics: GameAnalysisMetrics;
    }
  | {
      status: "failed";
      reason_codes: [string, ...string[]];
      checks: GameAnalysisChecksWithFailed;
      findings: Finding[];
      metrics: GameAnalysisMetrics;
    }
  | {
      status: "inconclusive";
      reason_codes: [string, ...string[]];
      checks: GameAnalysisChecksWithInconclusive;
      findings: Finding[];
      metrics: GameAnalysisMetrics;
    };
type GameAnalysisUnsupportedEvidence = {
  analyzer: Extract<Analyzer, { profile: "unsupported" }>;
  requirement: Extract<AnalysisRequirements, { profile: "unsupported" }>;
  status: "unsupported";
  reason_codes: [string, ...string[]];
  checks: GameAnalysisChecksAllNotApplicable;
  findings: [];
  metrics: GameAnalysisMetrics & { frontier_closed: false };
};
export type WorldForgeDeterministicGameAnalysisV1 =
  GameAnalysisGeneratedClosed &
    (
      | (GameAnalysisSupportedProfile & GameAnalysisSupportedEvidence)
      | GameAnalysisUnsupportedEvidence
    );

type LedgerClaimFields =
  | "extension"
  | "missing_feature_ids"
  | "native_evidence"
  | "reason_code"
  | "status"
  | "test_evidence";
type LedgerEvidence = EvidenceArray[number];
type LedgerExtension = Exclude<Feature["extension"], null>;
type LedgerClaim =
  | {
      extension: null;
      missing_feature_ids: [];
      native_evidence: [];
      reason_code: "adapter_not_evaluated";
      status: "authoring_only";
      test_evidence: [];
    }
  | {
      extension: null;
      missing_feature_ids: [string, ...string[]];
      native_evidence: [];
      reason_code: "missing_required_capability";
      status: "blocked";
      test_evidence: [];
    }
  | {
      extension: null;
      missing_feature_ids: [];
      native_evidence: [LedgerEvidence, ...LedgerEvidence[]];
      reason_code: "adapter_verified";
      status: "supported_current";
      test_evidence: [LedgerEvidence, ...LedgerEvidence[]];
    }
  | {
      extension: LedgerExtension;
      missing_feature_ids: [];
      native_evidence: [LedgerEvidence, ...LedgerEvidence[]];
      reason_code: "game_extension_verified";
      status: "game_extension_verified";
      test_evidence: [LedgerEvidence, ...LedgerEvidence[]];
    };
type ClosedLedgerFeature = Omit<Feature, LedgerClaimFields> & LedgerClaim;
type ClosedLedgerMechanic = Omit<Mechanic1, LedgerClaimFields> & LedgerClaim;
type CapabilityLedgerGeneratedClosed = Omit<
  WorldForgeMechanicCapabilityLedgerV1Generated,
  "features" | "mechanics"
> & {
  features: [ClosedLedgerFeature, ...ClosedLedgerFeature[]];
  mechanics: [ClosedLedgerMechanic, ...ClosedLedgerMechanic[]];
};
export type WorldForgeMechanicCapabilityLedgerV1 =
  GamepackDeepClosed<CapabilityLedgerGeneratedClosed>;

type ClosedGameplayDependencies = {
  authored: TokenArray;
  procedural: TokenArray;
  systemic: TokenArray;
};
type ClosedGameplayCommon = {
  challenge_model: string;
  core_loop: StringArray;
  core_verbs: CoreVerb[];
  dependencies: ClosedGameplayDependencies;
  failure_recovery: string;
  goal_model: string;
  mechanic_tags: TokenArray;
  player_role: string;
  progression: string;
  rule_model: string;
  secondary_families: (
    | "none"
    | "action"
    | "adventure"
    | "educational"
    | "narrative"
    | "puzzle"
    | "rhythm"
    | "role_playing"
    | "sandbox"
    | "simulation"
    | "sports"
    | "strategy"
  )[];
  session_structure: string;
  social_topology:
    | "none"
    | "single_player"
    | "local_cooperative"
    | "local_competitive"
    | "online_cooperative"
    | "online_competitive"
    | "massively_multiplayer";
  teleology: "none" | "finite" | "infinite" | "open_ended";
};
export type Gameplay =
  | (ClosedGameplayCommon & {
      primary_family: "none";
      challenge_model: "none";
      core_loop: [];
      core_verbs: [];
      dependencies: { authored: []; procedural: []; systemic: [] };
      failure_recovery: "none";
      goal_model: "none";
      mechanic_tags: [];
      player_role: "none";
      progression: "none";
      rule_model: "none";
      secondary_families: [];
      session_structure: "none";
      social_topology: "none";
      teleology: "none";
    })
  | (ClosedGameplayCommon & {
      primary_family:
        | "action"
        | "adventure"
        | "educational"
        | "narrative"
        | "puzzle"
        | "rhythm"
        | "role_playing"
        | "sandbox"
        | "simulation"
        | "sports"
        | "strategy";
      core_loop: [string, ...string[]];
      core_verbs: [CoreVerb, ...CoreVerb[]];
    });

type ClosedWorldCommon = {
  persistence: string;
  scale: string;
  simulated_domains: TokenArray;
  simulation_depth: string;
  spatial_structure: string;
  spatial_topology: string;
  time_model: string;
};
export type World =
  | (ClosedWorldCommon & {
      presence: "none";
      persistence: "none";
      scale: "none";
      simulated_domains: [];
      simulation_depth: "none";
      spatial_structure: "none";
      spatial_topology: "none";
      time_model: "none";
    })
  | (ClosedWorldCommon & { presence: "abstract" | "symbolic" | "diegetic" });

type ClosedNarrativeCommon = {
  agency: string;
  authorship_mode:
    | "none"
    | "authored"
    | "emergent"
    | "procedural"
    | "player_authored"
    | "social"
    | "hybrid";
  canon_variability: string;
  delivery_channels: TokenArray;
  endings: string;
  focalization: string;
  information_model: string;
  pacing: string;
  protagonist_model: string;
  topology:
    | "none"
    | "linear"
    | "foldback"
    | "branching"
    | "branch_and_bottleneck"
    | "hub_and_spoke"
    | "modular"
    | "storylet"
    | "loop_reset"
    | "episodic"
    | "seasonal"
    | "open_ended";
};
export type Narrative =
  | (ClosedNarrativeCommon & {
      requirement: "none";
      agency: "none";
      authorship_mode: "none";
      canon_variability: "none";
      delivery_channels: [];
      endings: "none";
      focalization: "none";
      information_model: "none";
      pacing: "none";
      protagonist_model: "none";
      topology: "none";
    })
  | (ClosedNarrativeCommon & { requirement: "optional" | "required" });

type ClosedWorldModuleBase = {
  content_hash: string;
  extensions: Extension3[];
  format: "world-forge.world_module";
  format_version: 1;
  module_id: string;
  project_id: string;
  title: string;
};
export type WorldForgeTypedWorldModuleV1 =
  | (ClosedWorldModuleBase & {
      module_type: "canon";
      facts: [Fact, ...Fact[]];
      events?: never;
      spaces?: never;
      groups?: never;
      characters?: never;
      knowledge_items?: never;
    })
  | (ClosedWorldModuleBase & {
      module_type: "chronology";
      events: [Event, ...Event[]];
      facts?: never;
      spaces?: never;
      groups?: never;
      characters?: never;
      knowledge_items?: never;
    })
  | (ClosedWorldModuleBase & {
      module_type: "space";
      spaces: [Space, ...Space[]];
      facts?: never;
      events?: never;
      groups?: never;
      characters?: never;
      knowledge_items?: never;
    })
  | (ClosedWorldModuleBase & {
      module_type: "group";
      groups: [Group, ...Group[]];
      facts?: never;
      events?: never;
      spaces?: never;
      characters?: never;
      knowledge_items?: never;
    })
  | (ClosedWorldModuleBase & {
      module_type: "character";
      characters: [Character, ...Character[]];
      facts?: never;
      events?: never;
      spaces?: never;
      groups?: never;
      knowledge_items?: never;
    })
  | (ClosedWorldModuleBase & {
      module_type: "knowledge";
      knowledge_items: [Knowledge, ...Knowledge[]];
      facts?: never;
      events?: never;
      spaces?: never;
      groups?: never;
      characters?: never;
    });

type ClosedNarrativeUnitBase = {
  compiler_owned?: never;
  asset_binding_ids: IdArray1;
  effect_ids: IdArray1;
  id: string;
  next_unit_ids: IdArray1;
  prerequisite_ids: IdArray1;
  title: string;
};
export type Unit =
  | (ClosedNarrativeUnitBase & {
      unit_type:
        | "arc"
        | "beat"
        | "scene"
        | "dialogue"
        | "storylet"
        | "clue"
        | "reveal"
        | "memory"
        | "episode";
      options?: never;
      ending_kind?: never;
    })
  | (ClosedNarrativeUnitBase & {
      unit_type: "choice";
      options: [ChoiceOption, ChoiceOption, ...ChoiceOption[]];
      ending_kind?: never;
    })
  | (ClosedNarrativeUnitBase & {
      unit_type: "ending";
      ending_kind: "success" | "failure" | "neutral";
      options?: never;
    });

type LogicForbiddenRuntimeFields = {
  compiler_owned?: never;
  runtime_ai?: never;
  script?: never;
  expression?: never;
  callback?: never;
  command?: never;
  import?: never;
  module?: never;
  provider?: never;
  provider_id?: never;
  provider_credentials?: never;
  provider_details?: never;
  model?: never;
  model_id?: never;
  tool?: never;
  prompt?: never;
  credential?: never;
  credentials?: never;
  token?: never;
  endpoint?: never;
  path?: never;
  source_path?: never;
  absolute_path?: never;
  mutable_path?: never;
  project_path?: never;
  authoring_path?: never;
  python?: never;
  javascript?: never;
  native_code?: never;
  executable?: never;
  executable_script?: never;
};
type LogicClosed<Value> = Value & LogicForbiddenRuntimeFields;
declare const logicRuntimeStringBrand: unique symbol;
export type LogicRuntimeString = string & {
  readonly [logicRuntimeStringBrand]: "validated-logic-runtime-string";
};

type LogicLiteralOperandCommon = LogicForbiddenRuntimeFields & {
  kind: "literal";
  state_id?: never;
  action_id?: never;
  parameter_id?: never;
};
export type LogicOperand =
  | (LogicLiteralOperandCommon & {
      value_type: "boolean";
      value: boolean;
    })
  | (LogicLiteralOperandCommon & {
      value_type: "integer";
      value: number;
    })
  | (LogicLiteralOperandCommon & {
      value_type: "string";
      value: LogicRuntimeString;
    })
  | (LogicLiteralOperandCommon & {
      value_type: "string_array";
      value: LogicRuntimeString[];
    })
  | (LogicForbiddenRuntimeFields & {
      kind: "state";
      state_id: string;
      value_type?: never;
      value?: never;
      action_id?: never;
      parameter_id?: never;
    })
  | (LogicForbiddenRuntimeFields & {
      kind: "parameter";
      action_id: string;
      parameter_id: string;
      state_id?: never;
      value_type?: never;
      value?: never;
    });

type LogicConditionCommon = LogicForbiddenRuntimeFields & {
  id: string;
  action_id: string | null;
};
export type LogicCondition =
  | (LogicConditionCommon & {
      operator: "constant";
      value: boolean;
      comparison?: never;
      left?: never;
      right?: never;
      condition_ids?: never;
      condition_id?: never;
      array_state_id?: never;
      index?: never;
      distance?: never;
    })
  | (LogicConditionCommon & {
      operator: "compare";
      comparison:
        | "equal"
        | "not_equal"
        | "less_than"
        | "less_or_equal"
        | "greater_than"
        | "greater_or_equal";
      left: LogicOperand;
      right: LogicOperand;
      value?: never;
      condition_ids?: never;
      condition_id?: never;
      array_state_id?: never;
      index?: never;
      distance?: never;
    })
  | (LogicConditionCommon & {
      operator: "all" | "any";
      condition_ids: [string, ...string[]];
      value?: never;
      comparison?: never;
      left?: never;
      right?: never;
      condition_id?: never;
      array_state_id?: never;
      index?: never;
      distance?: never;
    })
  | (LogicConditionCommon & {
      operator: "not";
      condition_id: string;
      value?: never;
      comparison?: never;
      left?: never;
      right?: never;
      condition_ids?: never;
      array_state_id?: never;
      index?: never;
      distance?: never;
    })
  | (LogicConditionCommon & {
      action_id: string;
      operator: "index_valid";
      array_state_id: string;
      index: LogicOperand;
      value?: never;
      comparison?: never;
      left?: never;
      right?: never;
      condition_ids?: never;
      condition_id?: never;
      distance?: never;
    })
  | (LogicConditionCommon & {
      action_id: string;
      operator: "integer_distance";
      left: LogicOperand;
      right: LogicOperand;
      distance: number;
      value?: never;
      comparison?: never;
      condition_ids?: never;
      condition_id?: never;
      array_state_id?: never;
      index?: never;
    });

type LogicEffectCommon = LogicForbiddenRuntimeFields & {
  id: string;
  action_id: string;
  invalid_transition_policy: "reject_transition";
};
export type LogicEffect =
  | (LogicEffectCommon & {
      operation: "set";
      state_id: string;
      value: LogicOperand;
      array_state_id?: never;
      first_index?: never;
      second_index?: never;
      amount?: never;
    })
  | (LogicEffectCommon & {
      operation: "swap_array_items";
      array_state_id: string;
      first_index: LogicOperand;
      second_index: LogicOperand;
      state_id?: never;
      value?: never;
      amount?: never;
    })
  | (LogicEffectCommon & {
      operation: "append_unique";
      array_state_id: string;
      value: LogicOperand;
      state_id?: never;
      first_index?: never;
      second_index?: never;
      amount?: never;
    })
  | (LogicEffectCommon & {
      operation: "increment";
      state_id: string;
      amount: LogicOperand;
      array_state_id?: never;
      value?: never;
      first_index?: never;
      second_index?: never;
    })
  | (LogicEffectCommon & {
      operation: "reset";
      state_id: string;
      array_state_id?: never;
      value?: never;
      first_index?: never;
      second_index?: never;
      amount?: never;
    });

type LogicStateCommon = LogicForbiddenRuntimeFields & {
  id: string;
  mutability: "mutable" | "constant";
  persistence: "saved" | "transient";
};
export type LogicStateVariable =
  | (LogicStateCommon & {
      type: "boolean";
      initial: boolean;
      minimum?: never;
      maximum?: never;
      allowed_values?: never;
      min_items?: never;
      max_items?: never;
    })
  | (LogicStateCommon & {
      type: "integer";
      initial: number;
      minimum: number;
      maximum: number;
      allowed_values?: never;
      min_items?: never;
      max_items?: never;
    })
  | (LogicStateCommon & {
      type: "string";
      initial: LogicRuntimeString;
      allowed_values: [LogicRuntimeString, ...LogicRuntimeString[]];
      minimum?: never;
      maximum?: never;
      min_items?: never;
      max_items?: never;
    })
  | (LogicStateCommon & {
      type: "string_array";
      initial: LogicRuntimeString[];
      allowed_values: [LogicRuntimeString, ...LogicRuntimeString[]];
      min_items: number;
      max_items: number;
      minimum?: never;
      maximum?: never;
    });

export type LogicParameter =
  | (LogicForbiddenRuntimeFields & {
      id: string;
      type: "boolean";
      minimum?: never;
      maximum?: never;
      allowed_values?: never;
      min_items?: never;
      max_items?: never;
    })
  | (LogicForbiddenRuntimeFields & {
      id: string;
      type: "integer";
      minimum: number;
      maximum: number;
      allowed_values?: never;
      min_items?: never;
      max_items?: never;
    })
  | (LogicForbiddenRuntimeFields & {
      id: string;
      type: "string";
      allowed_values: [LogicRuntimeString, ...LogicRuntimeString[]];
      minimum?: never;
      maximum?: never;
      min_items?: never;
      max_items?: never;
    })
  | (LogicForbiddenRuntimeFields & {
      id: string;
      type: "string_array";
      allowed_values: [LogicRuntimeString, ...LogicRuntimeString[]];
      min_items: number;
      max_items: number;
      minimum?: never;
      maximum?: never;
    });

export type LogicSourceBinding =
  | (LogicForbiddenRuntimeFields & {
      kind: "activity" | "system";
      source_id: string;
      option_id?: never;
    })
  | (LogicForbiddenRuntimeFields & {
      kind: "narrative_option";
      source_id: string;
      option_id: string;
    });
export type LogicAction = LogicForbiddenRuntimeFields & {
  id: string;
  core_verb_id: string;
  parameters: LogicParameter[];
  source_bindings: [LogicSourceBinding, ...LogicSourceBinding[]];
  rule_ids: [string, ...string[]];
  presentation_hook_ids: string[];
  required_feature_ids: [string, ...string[]];
};
export type WorldForgeDeclarativeLogicModuleV1 = LogicForbiddenRuntimeFields &
  Omit<
    WorldForgeDeclarativeLogicModuleV1Generated,
    | "actions"
    | "conditions"
    | "effects"
    | "state_variables"
    | "rules"
    | "goals"
    | "failures"
    | "endings"
    | "events"
    | "presentation_hooks"
    | "mechanics"
    | "extensions"
    | "title"
  > & {
    title: LogicRuntimeString;
    actions: [LogicAction, ...LogicAction[]];
    conditions: [LogicCondition, ...LogicCondition[]];
    effects: [LogicEffect, ...LogicEffect[]];
    state_variables: [LogicStateVariable, ...LogicStateVariable[]];
    rules: [LogicClosed<Rule>, ...LogicClosed<Rule>[]];
    goals: [LogicClosed<Goal>, ...LogicClosed<Goal>[]];
    failures: LogicClosed<Failure>[];
    endings: [LogicClosed<Ending>, ...LogicClosed<Ending>[]];
    events: [LogicClosed<Event1>, ...LogicClosed<Event1>[]];
    presentation_hooks: [
      LogicClosed<PresentationHook>,
      ...LogicClosed<PresentationHook>[],
    ];
    mechanics: [LogicClosed<Mechanic>, ...LogicClosed<Mechanic>[]];
    extensions: LogicClosed<Extension7>[];
  };

type PhaseReportV2Common = Omit<
  WorldForgePhaseReportV2Generated,
  "phase" | "status" | "rationale" | "evidence" | "output_evidence"
>;
type PhaseIdentity<Format extends string> = {
  format: Format;
  format_version: 1;
  id: string;
  content_hash: string;
};
type PhaseEvidence<Format extends string> = {
  evidence_id: string;
  claim: string;
  subject: PhaseIdentity<Format>;
};
type CreationIdentityFormat =
  | "world-forge.project"
  | "world-forge.creation_profile"
  | "world-forge.creation_source_manifest"
  | "world-forge.world_module"
  | "world-forge.activity_module"
  | "world-forge.narrative_module"
  | "world-forge.system_module"
  | "world-forge.logic_module";
type PhaseEvidenceScope =
  | {
      phase: "p02_world_laws" | "p07_systems" | "p09_narrative_content";
      evidence: [
        PhaseEvidence<CreationIdentityFormat>,
        ...PhaseEvidence<CreationIdentityFormat>[],
      ];
    }
  | {
      phase: Exclude<
        | "p00_brief"
        | "p01_genre_style"
        | "p02_world_laws"
        | "p03_geography"
        | "p04_timeline"
        | "p05_societies"
        | "p06_characters"
        | "p07_systems"
        | "p08_world_arcs"
        | "p09_narrative_content"
        | "p10_canon_lock",
        "p02_world_laws" | "p07_systems" | "p09_narrative_content"
      >;
      evidence: [
        PhaseEvidence<Exclude<CreationIdentityFormat, "world-forge.logic_module">>,
        ...PhaseEvidence<
          Exclude<CreationIdentityFormat, "world-forge.logic_module">
        >[],
      ];
    };
type PhaseOutputEvidence<
  Phase extends string,
  Role extends string,
  Subject,
> = {
  format: "world-forge.phase_output_evidence";
  format_version: 1;
  id: string;
  phase: Phase;
  role: Role;
  subject: Subject;
  reviewer: { id: string; role: string };
  content_hash: string;
};
export type PhaseOutputEvidenceV1 =
  | PhaseOutputEvidence<
      "p00_brief",
      "brief_review",
      PhaseIdentity<"world-forge.project">
    >
  | PhaseOutputEvidence<
      "p01_genre_style",
      "experience_classification",
      PhaseIdentity<"world-forge.creation_profile">
    >
  | PhaseOutputEvidence<
      "p02_world_laws",
      "interaction_ontology",
      PhaseIdentity<
        | "world-forge.creation_profile"
        | "world-forge.activity_module"
        | "world-forge.system_module"
        | "world-forge.logic_module"
      >
    >
  | PhaseOutputEvidence<
      "p03_geography",
      "world_topology",
      PhaseIdentity<"world-forge.creation_profile" | "world-forge.world_module">
    >
  | PhaseOutputEvidence<
      "p04_timeline",
      "chronology",
      PhaseIdentity<"world-forge.world_module" | "world-forge.system_module">
    >
  | PhaseOutputEvidence<
      "p05_societies",
      "group_structures",
      PhaseIdentity<"world-forge.world_module">
    >
  | PhaseOutputEvidence<
      "p06_characters",
      "actors",
      PhaseIdentity<
        | "world-forge.world_module"
        | "world-forge.activity_module"
        | "world-forge.narrative_module"
      >
    >
  | PhaseOutputEvidence<
      "p07_systems",
      "systems_design",
      PhaseIdentity<
        | "world-forge.creation_profile"
        | "world-forge.activity_module"
        | "world-forge.system_module"
        | "world-forge.logic_module"
      >
    >
  | PhaseOutputEvidence<
      "p08_world_arcs",
      "narrative_architecture",
      PhaseIdentity<"world-forge.creation_profile" | "world-forge.narrative_module">
    >
  | PhaseOutputEvidence<
      "p09_narrative_content",
      "typed_content",
      PhaseIdentity<
        | "world-forge.creation_source_manifest"
        | "world-forge.world_module"
        | "world-forge.activity_module"
        | "world-forge.narrative_module"
        | "world-forge.system_module"
        | "world-forge.logic_module"
      >
    >
  | PhaseOutputEvidence<
      "p10_canon_lock",
      "content_lock",
      PhaseIdentity<"world-forge.creation_source_manifest">
    >;
type ReadyPhaseReportV2 =
  | {
      phase: "p00_brief";
      status: "ready";
      rationale: { code: "phase_ready"; message: string };
      output_evidence: Extract<PhaseOutputEvidenceV1, { phase: "p00_brief" }>;
    }
  | {
      phase: "p01_genre_style";
      status: "ready";
      rationale: { code: "phase_ready"; message: string };
      output_evidence: Extract<PhaseOutputEvidenceV1, { phase: "p01_genre_style" }>;
    }
  | {
      phase: "p02_world_laws";
      status: "ready";
      rationale: { code: "phase_ready"; message: string };
      output_evidence: Extract<PhaseOutputEvidenceV1, { phase: "p02_world_laws" }>;
    }
  | {
      phase: "p03_geography";
      status: "ready";
      rationale: { code: "phase_ready"; message: string };
      output_evidence: Extract<PhaseOutputEvidenceV1, { phase: "p03_geography" }>;
    }
  | {
      phase: "p04_timeline";
      status: "ready";
      rationale: { code: "phase_ready"; message: string };
      output_evidence: Extract<PhaseOutputEvidenceV1, { phase: "p04_timeline" }>;
    }
  | {
      phase: "p05_societies";
      status: "ready";
      rationale: { code: "phase_ready"; message: string };
      output_evidence: Extract<PhaseOutputEvidenceV1, { phase: "p05_societies" }>;
    }
  | {
      phase: "p06_characters";
      status: "ready";
      rationale: { code: "phase_ready"; message: string };
      output_evidence: Extract<PhaseOutputEvidenceV1, { phase: "p06_characters" }>;
    }
  | {
      phase: "p07_systems";
      status: "ready";
      rationale: { code: "phase_ready"; message: string };
      output_evidence: Extract<PhaseOutputEvidenceV1, { phase: "p07_systems" }>;
    }
  | {
      phase: "p08_world_arcs";
      status: "ready";
      rationale: { code: "phase_ready"; message: string };
      output_evidence: Extract<PhaseOutputEvidenceV1, { phase: "p08_world_arcs" }>;
    }
  | {
      phase: "p09_narrative_content";
      status: "ready";
      rationale: { code: "phase_ready"; message: string };
      output_evidence: Extract<
        PhaseOutputEvidenceV1,
        { phase: "p09_narrative_content" }
      >;
    }
  | {
      phase: "p10_canon_lock";
      status: "ready";
      rationale: { code: "phase_ready"; message: string };
      output_evidence: Extract<PhaseOutputEvidenceV1, { phase: "p10_canon_lock" }>;
    };
type NotApplicablePhaseReportV2 =
  | {
      phase: "p03_geography";
      status: "not_applicable";
      rationale: { code: "world_absent"; message: string };
      output_evidence: null;
    }
  | {
      phase: "p04_timeline";
      status: "not_applicable";
      rationale: { code: "chronology_absent"; message: string };
      output_evidence: null;
    }
  | {
      phase: "p05_societies";
      status: "not_applicable";
      rationale: { code: "group_structures_absent"; message: string };
      output_evidence: null;
    }
  | {
      phase: "p06_characters";
      status: "not_applicable";
      rationale: { code: "actors_absent"; message: string };
      output_evidence: null;
    };
export type WorldForgePhaseReportV2 = PhaseReportV2Common &
  (ReadyPhaseReportV2 | NotApplicablePhaseReportV2) &
  PhaseEvidenceScope;

type LoreForbiddenNestedFields = {
  world_modules?: never;
  narrative_modules?: never;
  activity_modules?: never;
  system_modules?: never;
  actions?: never;
  rules?: never;
  effects?: never;
  goals?: never;
  condition_ids?: never;
  effect_ids?: never;
  prerequisite_ids?: never;
  asset_binding_ids?: never;
  system_ids?: never;
  action_ids?: never;
  rule_ids?: never;
  runtime_requirements?: never;
  sources?: never;
  prompt?: never;
  provider?: never;
  provider_details?: never;
  provider_credentials?: never;
  model?: never;
  tool?: never;
  credentials?: never;
  source_path?: never;
  absolute_path?: never;
  mutable_path?: never;
  project_path?: never;
  authoring_path?: never;
  extensions?: never;
  script?: never;
  executable?: never;
};
type LorepackSourceIdentity = LoreForbiddenNestedFields & {
  format:
    | "world-forge.project"
    | "world-forge.creation_profile"
    | "world-forge.creation_source_manifest"
    | "world-forge.world_module"
    | "world-forge.narrative_module";
  format_version: 1;
  id: string;
  content_hash: string;
};
type LorepackDependencyIdentity = LoreForbiddenNestedFields & {
  format: "world-forge.lorepack";
  format_version: 1;
  id: string;
  content_hash: string;
};
export type LorepackProvenance =
  | (LoreForbiddenNestedFields & {
      provenance_id: string;
      kind: "source_contract";
      subject: LorepackSourceIdentity;
    })
  | (LoreForbiddenNestedFields & {
      provenance_id: string;
      kind: "dependency_lorepack";
      subject: LorepackDependencyIdentity;
    });
type LoreWorldProjectionCommon = LoreForbiddenNestedFields & {
  format: "world-forge.lore_world_projection";
  format_version: 1;
  projection_id: string;
  source: LoreForbiddenNestedFields & {
    format: "world-forge.world_module";
    format_version: 1;
    id: string;
    content_hash: string;
  };
  title: string;
  content_hash: string;
};
export type LoreWorldFactRecord = LoreForbiddenNestedFields & {
  id: string;
  statement: string;
  status: "canon" | "provisional";
};
export type LoreWorldEventRecord = LoreForbiddenNestedFields & {
  id: string;
  sequence: number;
  summary: string;
};
export type LoreWorldSpaceRecord = LoreForbiddenNestedFields & {
  id: string;
  name: string;
  topology: "abstract" | "symbolic" | "diegetic";
};
export type LoreWorldGroupRecord = LoreForbiddenNestedFields & {
  id: string;
  name: string;
  group_type: string;
};
export type LoreWorldCharacterRecord = LoreForbiddenNestedFields & {
  id: string;
  name: string;
  role: string;
};
export type LoreWorldKnowledgeRecord = LoreForbiddenNestedFields & {
  id: string;
  statement: string;
  access: "public" | "restricted" | "secret";
};
export type LoreWorldProjection =
  | (LoreWorldProjectionCommon & {
      module_type: "canon";
      records: [LoreWorldFactRecord, ...LoreWorldFactRecord[]];
    })
  | (LoreWorldProjectionCommon & {
      module_type: "chronology";
      records: [LoreWorldEventRecord, ...LoreWorldEventRecord[]];
    })
  | (LoreWorldProjectionCommon & {
      module_type: "space";
      records: [LoreWorldSpaceRecord, ...LoreWorldSpaceRecord[]];
    })
  | (LoreWorldProjectionCommon & {
      module_type: "group";
      records: [LoreWorldGroupRecord, ...LoreWorldGroupRecord[]];
    })
  | (LoreWorldProjectionCommon & {
      module_type: "character";
      records: [LoreWorldCharacterRecord, ...LoreWorldCharacterRecord[]];
    })
  | (LoreWorldProjectionCommon & {
      module_type: "knowledge";
      records: [LoreWorldKnowledgeRecord, ...LoreWorldKnowledgeRecord[]];
    });
export type LoreChoiceOption = LoreForbiddenNestedFields & {
  id: string;
  label: string;
  next_unit_id: string;
};
type LoreNarrativeUnitCommon = LoreForbiddenNestedFields & {
  id: string;
  title: string;
  next_unit_ids: string[];
};
export type LoreNarrativeUnit =
  | (LoreNarrativeUnitCommon & {
      unit_type:
        | "arc"
        | "beat"
        | "scene"
        | "dialogue"
        | "storylet"
        | "clue"
        | "reveal"
        | "memory"
        | "episode";
      options?: never;
      ending_kind?: never;
    })
  | (LoreNarrativeUnitCommon & {
      unit_type: "choice";
      options: [LoreChoiceOption, LoreChoiceOption, ...LoreChoiceOption[]];
      ending_kind?: never;
    })
  | (LoreNarrativeUnitCommon & {
      unit_type: "ending";
      next_unit_ids: [];
      ending_kind: "success" | "failure" | "neutral";
      options?: never;
    });
export type LoreNarrativeProjection = LoreForbiddenNestedFields & {
  format: "world-forge.lore_narrative_projection";
  format_version: 1;
  projection_id: string;
  source: LoreForbiddenNestedFields & {
    format: "world-forge.narrative_module";
    format_version: 1;
    id: string;
    content_hash: string;
  };
  title: string;
  entry_unit_ids: [string, ...string[]];
  units: [LoreNarrativeUnit, ...LoreNarrativeUnit[]];
  content_hash: string;
};
export type LorepackLocalizationReference = LoreForbiddenNestedFields & {
  key: string;
  locale: string;
  module_id: string;
  subject_kind: "module" | "world_record" | "narrative_unit" | "choice_option";
  subject_id: string;
  parent_id: string;
  field: "title" | "statement" | "summary" | "name" | "role" | "group_type" | "label";
};
export type LorepackLocalization = LoreForbiddenNestedFields & {
  source_locale: string;
  supported_locales: [string, ...string[]];
  references: [LorepackLocalizationReference, ...LorepackLocalizationReference[]];
};
type LorepackV1Common = Omit<
  WorldForgeLorepackV1Generated,
  "world_projections" | "narrative_projections" | "localization" | "provenance"
> & {
  localization: LorepackLocalization;
  provenance: [LorepackProvenance, ...LorepackProvenance[]];
  world_modules?: never;
  narrative_modules?: never;
  activity_modules?: never;
  system_modules?: never;
  actions?: never;
  rules?: never;
  effects?: never;
  goals?: never;
  runtime_requirements?: never;
  script?: never;
  prompt?: never;
  provider?: never;
  provider_credentials?: never;
  credentials?: never;
  model?: never;
  tool?: never;
  source_path?: never;
  absolute_path?: never;
  mutable_path?: never;
  project_path?: never;
  authoring_path?: never;
  provider_details?: never;
  executable?: never;
  condition_ids?: never;
  effect_ids?: never;
  prerequisite_ids?: never;
  asset_binding_ids?: never;
  system_ids?: never;
  action_ids?: never;
  rule_ids?: never;
};
export type WorldForgeLorepackV1 = LorepackV1Common &
  (
    | {
        world_projections: [
          LoreWorldProjection,
          ...LoreWorldProjection[],
        ];
        narrative_projections: LoreNarrativeProjection[];
      }
    | {
        world_projections: LoreWorldProjection[];
        narrative_projections: [
          LoreNarrativeProjection,
          ...LoreNarrativeProjection[],
        ];
      }
  );
`;
verifyGeneratedContractDeclarations(generatedContracts);

const assetLicenseConformanceFixture = await readStrictJsonObject(
    path.resolve(
        contractsFixtureRoot,
        "abstract-puzzle/assets/production/board_ui/license.json",
    ),
);
const assetLicenseConformanceLiteral = JSON.stringify(
    assetLicenseConformanceFixture,
    null,
    2,
);
const generatedContractsConformance = `/* AUTO-GENERATED negative type probes for world-forge.* contracts. */
import type {
  Check,
  LogicCondition,
  LogicEffect,
  LogicOperand,
  LogicRuntimeString,
  LoreNarrativeUnit,
  LorepackProvenance,
  LoreWorldFactRecord,
  WorldForgeDeclarativeLogicModuleV1,
  WorldForgeDeterministicGameAnalysisV1,
  WorldForgeDeterministicGamepackV1,
  WorldForgeLorepackV1,
  WorldForgeMechanicCapabilityLedgerV1,
  WorldForgePhaseReportV2,
  WorldForgeAssetSubjectV1,
  WorldForgeReviewedAssetTargetV1,
  WorldForgeReviewedAssetStyleV1,
  WorldForgeDeterministicAssetInventoryV1,
  WorldForgeAssetSpecificationV1,
  WorldForgeAssetProductionRequestV1,
  WorldForgeAssetProductionReceiptV1,
  WorldForgeSelectedAssetProvenanceRecordV1,
  WorldForgeRuntimeSafeAssetLicenseRecordV1,
  Unit,
  WorldForgeCreationSourceManifestV1,
  WorldForgeTypedWorldModuleV1,
} from "./world-forge-contracts";
import type { ValidatedGenericAssetContract } from "../main/generic-asset-contracts";

type AssertTrue<Value extends true> = Value;
type AssertFalse<Value extends false> = Value;
type IsNever<Value> = [Value] extends [never] ? true : false;
export type RuntimeSafeAssetLicenseRecordIsInhabitable = AssertFalse<
  IsNever<WorldForgeRuntimeSafeAssetLicenseRecordV1>
>;
type ForbiddenLoreNestedField =
  | "world_modules"
  | "narrative_modules"
  | "activity_modules"
  | "system_modules"
  | "actions"
  | "rules"
  | "effects"
  | "goals"
  | "provider_credentials";
type RejectsForbiddenLoreField<
  Subject,
  Field extends PropertyKey,
> = Field extends keyof Subject
  ? Exclude<Subject[Field], undefined> extends never
    ? true
    : false
  : false;
export type LoreNarrativeUnitForbiddenFieldsAreClosed = AssertTrue<
  RejectsForbiddenLoreField<LoreNarrativeUnit, ForbiddenLoreNestedField>
>;
export type LoreWorldFactForbiddenFieldsAreClosed = AssertTrue<
  RejectsForbiddenLoreField<LoreWorldFactRecord, ForbiddenLoreNestedField>
>;
export type LoreProvenanceForbiddenFieldsAreClosed = AssertTrue<
  RejectsForbiddenLoreField<LorepackProvenance, ForbiddenLoreNestedField>
>;
type RejectsLogicProviderCredentials<Subject> =
  "provider_credentials" extends keyof Subject
    ? Exclude<Subject["provider_credentials"], undefined> extends never
      ? true
      : false
    : false;
export type LogicNestedProviderCredentialsAreClosed = {
  actions: AssertTrue<
    RejectsLogicProviderCredentials<
      WorldForgeDeclarativeLogicModuleV1["actions"][number]
    >
  >;
  conditions: AssertTrue<
    RejectsLogicProviderCredentials<
      WorldForgeDeclarativeLogicModuleV1["conditions"][number]
    >
  >;
  effects: AssertTrue<
    RejectsLogicProviderCredentials<
      WorldForgeDeclarativeLogicModuleV1["effects"][number]
    >
  >;
  state_variables: AssertTrue<
    RejectsLogicProviderCredentials<
      WorldForgeDeclarativeLogicModuleV1["state_variables"][number]
    >
  >;
  rules: AssertTrue<
    RejectsLogicProviderCredentials<
      WorldForgeDeclarativeLogicModuleV1["rules"][number]
    >
  >;
  goals: AssertTrue<
    RejectsLogicProviderCredentials<
      WorldForgeDeclarativeLogicModuleV1["goals"][number]
    >
  >;
  failures: AssertTrue<
    RejectsLogicProviderCredentials<
      WorldForgeDeclarativeLogicModuleV1["failures"][number]
    >
  >;
  endings: AssertTrue<
    RejectsLogicProviderCredentials<
      WorldForgeDeclarativeLogicModuleV1["endings"][number]
    >
  >;
  events: AssertTrue<
    RejectsLogicProviderCredentials<
      WorldForgeDeclarativeLogicModuleV1["events"][number]
    >
  >;
  presentation_hooks: AssertTrue<
    RejectsLogicProviderCredentials<
      WorldForgeDeclarativeLogicModuleV1["presentation_hooks"][number]
    >
  >;
  mechanics: AssertTrue<
    RejectsLogicProviderCredentials<
      WorldForgeDeclarativeLogicModuleV1["mechanics"][number]
    >
  >;
  extensions: AssertTrue<
    RejectsLogicProviderCredentials<
      WorldForgeDeclarativeLogicModuleV1["extensions"][number]
    >
  >;
};

type ActivityReference =
  WorldForgeCreationSourceManifestV1["modules"]["activity_modules"][number];

const completeReference: ActivityReference = {
  format: "world-forge.activity_module",
  format_version: 1,
  id: "activity_module",
  path: "activities/module.json",
  content_hash: "0000000000000000000000000000000000000000000000000000000000000000",
};
void completeReference;

// @ts-expect-error missing required reference fields
const incompleteReference: ActivityReference = {
  format: "world-forge.activity_module",
};
void incompleteReference;

const invalidDiscriminatorPayload: WorldForgeTypedWorldModuleV1 = {
  format: "world-forge.world_module",
  format_version: 1,
  module_id: "canon_module",
  project_id: "example_project",
  module_type: "canon",
  title: "Canon",
  facts: [
    {
      id: "fact_one",
      statement: "A fact.",
      status: "canon",
      sources: [],
    },
  ],
  // @ts-expect-error direct discriminator payload must remain closed
  events: [
    {
      id: "event_one",
      sequence: 1,
      summary: "Must not coexist with a canon payload.",
    },
  ],
  extensions: [],
  content_hash: "0000000000000000000000000000000000000000000000000000000000000000",
};
void invalidDiscriminatorPayload;

type CanonWorldModule = Extract<
  WorldForgeTypedWorldModuleV1,
  { module_type: "canon" }
>;
const preboundMixedDiscriminatorPayload = {
  format: "world-forge.world_module" as const,
  format_version: 1 as const,
  module_id: "canon_module",
  project_id: "example_project",
  module_type: "canon" as const,
  title: "Canon",
  facts: [
    {
      id: "fact_one",
      statement: "A fact.",
      status: "canon",
      sources: [],
    },
  ] as CanonWorldModule["facts"],
  events: [
    {
      id: "event_one",
      sequence: 1,
      summary: "Must not coexist with a canon payload.",
    },
  ],
  extensions: [],
  content_hash: "0000000000000000000000000000000000000000000000000000000000000000",
};
// @ts-expect-error pre-bound discriminator payload must remain closed
const invalidPreboundDiscriminatorPayload: WorldForgeTypedWorldModuleV1 =
  preboundMixedDiscriminatorPayload;
void invalidPreboundDiscriminatorPayload;

const invalidNarrativeUnitLiteral: Unit = {
  asset_binding_ids: [],
  effect_ids: [],
  id: "mixed_scene",
  next_unit_ids: [],
  prerequisite_ids: [],
  title: "Mixed scene",
  unit_type: "scene",
  // @ts-expect-error direct narrative-unit payload must remain closed
  ending_kind: "neutral",
};
void invalidNarrativeUnitLiteral;

const preboundMixedNarrativeUnit = {
  asset_binding_ids: [],
  effect_ids: [],
  id: "mixed_scene",
  next_unit_ids: [],
  prerequisite_ids: [],
  title: "Mixed scene",
  unit_type: "scene" as const,
  ending_kind: "neutral" as const,
};
// @ts-expect-error pre-bound narrative-unit payload must remain closed
const invalidPreboundNarrativeUnit: Unit = preboundMixedNarrativeUnit;
void invalidPreboundNarrativeUnit;

const preboundMixedLogicOperand = {
  action_id: "swap_tiles",
  kind: "parameter" as const,
  parameter_id: "first_index",
  state_id: "board",
};
// @ts-expect-error pre-bound logic operands cannot mix parameter and state fields
const invalidLogicOperand: LogicOperand = preboundMixedLogicOperand;
void invalidLogicOperand;

const preboundMixedLogicCondition = {
  action_id: null,
  id: "board_ready",
  left: { kind: "state" as const, state_id: "board" },
  operator: "constant" as const,
  value: true,
};
// @ts-expect-error pre-bound logic conditions cannot mix operator payloads
const invalidLogicCondition: LogicCondition = preboundMixedLogicCondition;
void invalidLogicCondition;

const preboundMixedLogicEffect = {
  action_id: "restart_board",
  id: "reset_board",
  operation: "reset" as const,
  state_id: "board",
  value: {
    kind: "literal" as const,
    value: true,
    value_type: "boolean" as const,
  },
};
// @ts-expect-error pre-bound logic effects cannot mix operation payloads
const invalidLogicEffect: LogicEffect = preboundMixedLogicEffect;
void invalidLogicEffect;

declare const completeLogicModule: WorldForgeDeclarativeLogicModuleV1;
declare const validatedLogicRuntimeString: LogicRuntimeString;
const logicWithValidatedRuntimeString = {
  ...completeLogicModule,
  title: validatedLogicRuntimeString,
};
const validRuntimeStringLogic: WorldForgeDeclarativeLogicModuleV1 =
  logicWithValidatedRuntimeString;
void validRuntimeStringLogic;

const preboundLogicWithUncheckedRuntimeString = {
  ...completeLogicModule,
  title: ${JSON.stringify(logicRuntimeStringCorpus.rejected[0])},
};
// @ts-expect-error logic runtime strings require strict schema validation first
const invalidUncheckedRuntimeStringLogic: WorldForgeDeclarativeLogicModuleV1 =
  preboundLogicWithUncheckedRuntimeString;
void invalidUncheckedRuntimeStringLogic;

const preboundLogicWithRuntimeAi = {
  ...completeLogicModule,
  runtime_ai: true,
};
// @ts-expect-error source logic cannot pre-bind runtime AI
const invalidRuntimeAiLogic: WorldForgeDeclarativeLogicModuleV1 =
  preboundLogicWithRuntimeAi;
void invalidRuntimeAiLogic;

declare const completePhaseReport: WorldForgePhaseReportV2;
const preboundNonWaivablePhase = {
  ...completePhaseReport,
  phase: "p08_world_arcs" as const,
  status: "not_applicable" as const,
  rationale: {
    code: "world_absent" as const,
    message: "Invalid waiver.",
  },
};
// @ts-expect-error non-waivable phases cannot use not_applicable
const invalidPhaseReport: WorldForgePhaseReportV2 = preboundNonWaivablePhase;
void invalidPhaseReport;

declare const completeP00PhaseReport: Extract<
  WorldForgePhaseReportV2,
  { phase: "p00_brief"; status: "ready" }
>;
const preboundP00WithProfileSubject = {
  ...completeP00PhaseReport,
  output_evidence: {
    ...completeP00PhaseReport.output_evidence,
    subject: completeP00PhaseReport.profile,
  },
};
// @ts-expect-error P00 output evidence requires a project subject
const invalidP00Subject: WorldForgePhaseReportV2 =
  preboundP00WithProfileSubject;
void invalidP00Subject;

const preboundP00WithWrongEvidencePhase = {
  ...completeP00PhaseReport,
  output_evidence: {
    ...completeP00PhaseReport.output_evidence,
    phase: "p08_world_arcs" as const,
  },
};
// @ts-expect-error report and output-evidence phases are inseparable
const invalidP00EvidencePhase: WorldForgePhaseReportV2 =
  preboundP00WithWrongEvidencePhase;
void invalidP00EvidencePhase;

const preboundP00WithWrongRole = {
  ...completeP00PhaseReport,
  output_evidence: {
    ...completeP00PhaseReport.output_evidence,
    role: "narrative_architecture" as const,
  },
};
// @ts-expect-error P00 output evidence requires the brief_review role
const invalidP00Role: WorldForgePhaseReportV2 = preboundP00WithWrongRole;
void invalidP00Role;

const preboundP00WithLogicEvidence = {
  ...completeP00PhaseReport,
  evidence: [
    {
      evidence_id: "logic_subject",
      claim: "Invalid phase scope.",
      subject: {
        format: "world-forge.logic_module" as const,
        format_version: 1 as const,
        id: "logic_module",
        content_hash:
          "0000000000000000000000000000000000000000000000000000000000000000",
      },
    },
  ],
};
// @ts-expect-error supplemental logic evidence is restricted to P02, P07, and P09
const invalidP00LogicEvidence: WorldForgePhaseReportV2 =
  preboundP00WithLogicEvidence;
void invalidP00LogicEvidence;

const preboundFuturePhase = {
  ...completeP00PhaseReport,
  phase: "p11_art_audio" as const,
};
// @ts-expect-error phase-report v2 deliberately ends at P10
const invalidFuturePhase: WorldForgePhaseReportV2 = preboundFuturePhase;
void invalidFuturePhase;

declare const completeLorepack: WorldForgeLorepackV1;
const preboundExecutableLorepack = {
  ...completeLorepack,
  script: "not allowed",
};
// @ts-expect-error lorepacks cannot contain executable scripts
const invalidLorepack: WorldForgeLorepackV1 = preboundExecutableLorepack;
void invalidLorepack;

const preboundCredentialLorepack = {
  ...completeLorepack,
  credentials: { token: "not allowed" },
};
// @ts-expect-error lorepacks cannot contain pre-bound provider credentials
const invalidCredentialLorepack: WorldForgeLorepackV1 =
  preboundCredentialLorepack;
void invalidCredentialLorepack;

declare const completeLoreNarrativeUnit: LoreNarrativeUnit;
const preboundNarrativeUnitWithRuntimeHooks = {
  ...completeLoreNarrativeUnit,
  condition_ids: ["condition"],
  effect_ids: ["effect"],
};
// @ts-expect-error lore narrative projections cannot retain runtime hooks
const invalidLoreNarrativeUnit: LoreNarrativeUnit =
  preboundNarrativeUnitWithRuntimeHooks;
void invalidLoreNarrativeUnit;

declare const completeLoreWorldFact: LoreWorldFactRecord;
const preboundLoreFactWithSources = {
  ...completeLoreWorldFact,
  sources: ["mutable_authoring_source"],
};
// @ts-expect-error lore world facts cannot retain authoring sources
const invalidLoreWorldFact: LoreWorldFactRecord = preboundLoreFactWithSources;
void invalidLoreWorldFact;

declare const sourceContractSubject: Extract<
  LorepackProvenance,
  { kind: "source_contract" }
>["subject"];
const preboundDependencyWithSource = {
  provenance_id: "invalid_dependency",
  kind: "dependency_lorepack" as const,
  subject: sourceContractSubject,
};
// @ts-expect-error dependency provenance requires a lorepack subject
const invalidDependencyProvenance: LorepackProvenance =
  preboundDependencyWithSource;
void invalidDependencyProvenance;

declare const lorepackSubject: Extract<
  LorepackProvenance,
  { kind: "dependency_lorepack" }
>["subject"];
const preboundSourceWithLorepack = {
  provenance_id: "invalid_source",
  kind: "source_contract" as const,
  subject: lorepackSubject,
};
// @ts-expect-error source provenance cannot name a lorepack subject
const invalidSourceProvenance: LorepackProvenance =
  preboundSourceWithLorepack;
void invalidSourceProvenance;

declare const completeGamepack: WorldForgeDeterministicGamepackV1;
const preboundGamepackWithRuntimeAi = {
  ...completeGamepack,
  runtime_ai: true,
};
// @ts-expect-error gamepacks cannot contain runtime AI declarations
const invalidRuntimeAiGamepack: WorldForgeDeterministicGamepackV1 =
  preboundGamepackWithRuntimeAi;
void invalidRuntimeAiGamepack;

declare const completeGamepackAction: WorldForgeDeterministicGamepackV1["logic"]["actions"][number];
const preboundNestedGamepackProviderCredentials = {
  ...completeGamepack,
  logic: {
    ...completeGamepack.logic,
    actions: [
      {
        ...completeGamepackAction,
        provider_credentials: { token: "not allowed" },
      },
    ],
  },
};
// @ts-expect-error nested gamepack records cannot contain provider credentials
const invalidNestedGamepackProviderCredentials: WorldForgeDeterministicGamepackV1 =
  preboundNestedGamepackProviderCredentials;
void invalidNestedGamepackProviderCredentials;

declare const completeGameAnalysis: WorldForgeDeterministicGameAnalysisV1;
const exactAnalysisAssumptions: WorldForgeDeterministicGameAnalysisV1["assumptions"] = [
  "The validated gamepack is the complete authoritative logic input.",
  "Action parameter domains are finite and exactly declared by the gamepack.",
  "Array order is authoritative and state equality uses compact canonical JSON.",
];
void exactAnalysisAssumptions;
const exactAnalysisOutOfScope: WorldForgeDeterministicGameAnalysisV1["out_of_scope_claims"] =
  [
    "asset_readability",
    "native_adapter_execution",
    "platform_performance",
    "save_replay_serialization",
    "timing_and_input_ux",
  ];
void exactAnalysisOutOfScope;
// @ts-expect-error game analysis disclosures cannot be empty
const invalidEmptyAnalysisAssumptions: WorldForgeDeterministicGameAnalysisV1["assumptions"] =
  [];
void invalidEmptyAnalysisAssumptions;
const preboundAnalysisWithUnknownAnalyzer = {
  ...completeGameAnalysis,
  analyzer: {
    ...completeGameAnalysis.analyzer,
    id: "worldforge.dynamic_plugin" as const,
  },
};
// @ts-expect-error game analysis requires one frozen built-in analyzer identity
const invalidUnknownAnalyzerAnalysis: WorldForgeDeterministicGameAnalysisV1 =
  preboundAnalysisWithUnknownAnalyzer;
void invalidUnknownAnalyzerAnalysis;

const preboundAnalysisWithChangedLimit = {
  ...completeGameAnalysis,
  requirement: {
    ...completeGameAnalysis.requirement,
    limits: {
      ...completeGameAnalysis.requirement.limits,
      depth: 511 as const,
    },
  },
};
// @ts-expect-error game analysis requirements pin exact deterministic bounds
const invalidChangedLimitAnalysis: WorldForgeDeterministicGameAnalysisV1 =
  preboundAnalysisWithChangedLimit;
void invalidChangedLimitAnalysis;

const preboundAnalysisWithMismatchedAnalyzer = {
  ...completeGameAnalysis,
  analyzer: {
    profile: "abstract_puzzle" as const,
    id: "worldforge.branching_narrative_exhaustive" as const,
    version: 1 as const,
  },
};
// @ts-expect-error analyzer ID is correlated with its structural profile
const invalidMismatchedAnalyzerAnalysis: WorldForgeDeterministicGameAnalysisV1 =
  preboundAnalysisWithMismatchedAnalyzer;
void invalidMismatchedAnalyzerAnalysis;

const preboundAnalysisWithMismatchedRequirement = {
  ...completeGameAnalysis,
  requirement: {
    ...completeGameAnalysis.requirement,
    profile: "unsupported" as const,
    analyzer_id: "worldforge.abstract_puzzle_exhaustive" as const,
    reason_code: null,
  },
};
// @ts-expect-error requirement analyzer and reason are correlated with its profile
const invalidMismatchedAnalysisRequirement: WorldForgeDeterministicGameAnalysisV1 =
  preboundAnalysisWithMismatchedRequirement;
void invalidMismatchedAnalysisRequirement;

declare const completePuzzleAnalysis: Extract<
  WorldForgeDeterministicGameAnalysisV1,
  { analyzer: { profile: "abstract_puzzle" } }
>;
const preboundPuzzleAnalysisWithBranchingAnalyzer = {
  ...completePuzzleAnalysis,
  analyzer: {
    profile: "branching_narrative" as const,
    id: "worldforge.branching_narrative_exhaustive" as const,
    version: 1 as const,
  },
};
// @ts-expect-error report analyzer must match its exact requirement profile
const invalidCrossProfileAnalysis: WorldForgeDeterministicGameAnalysisV1 =
  preboundPuzzleAnalysisWithBranchingAnalyzer;
void invalidCrossProfileAnalysis;

const preboundPuzzleAnalysisWithUnsupportedStatus = {
  ...completePuzzleAnalysis,
  status: "unsupported" as const,
};
// @ts-expect-error supported analysis profiles cannot claim unsupported status
const invalidSupportedAnalysisStatus: WorldForgeDeterministicGameAnalysisV1 =
  preboundPuzzleAnalysisWithUnsupportedStatus;
void invalidSupportedAnalysisStatus;

declare const completePassedPuzzleAnalysis: Extract<
  WorldForgeDeterministicGameAnalysisV1,
  { analyzer: { profile: "abstract_puzzle" }; status: "passed" }
>;
const failedAnalysisCheck: Extract<Check, { status: "failed" }> = {
  id: "synthetic_failure",
  status: "failed",
  reason_codes: ["synthetic_failure"],
};
const passedAnalysisCheck: Extract<Check, { status: "passed" }> = {
  id: "synthetic_pass",
  status: "passed",
  reason_codes: [],
};
const preboundPassedAnalysisWithFailedCheck = {
  ...completePassedPuzzleAnalysis,
  checks: [failedAnalysisCheck] as [typeof failedAnalysisCheck],
};
// @ts-expect-error passed analysis cannot retain a failed check
const invalidPassedAnalysisWithFailedCheck: WorldForgeDeterministicGameAnalysisV1 =
  preboundPassedAnalysisWithFailedCheck;
void invalidPassedAnalysisWithFailedCheck;

const preboundFailedCheckWithoutReason = {
  id: "synthetic_failure",
  status: "failed" as const,
  reason_codes: [] as [],
};
// @ts-expect-error failed checks require at least one reason code
const invalidFailedCheckWithoutReason: Check = preboundFailedCheckWithoutReason;
void invalidFailedCheckWithoutReason;

const preboundInconclusiveCheckWithoutReason = {
  id: "synthetic_inconclusive",
  status: "inconclusive" as const,
  reason_codes: [] as [],
};
// @ts-expect-error inconclusive checks require at least one reason code
const invalidInconclusiveCheckWithoutReason: Check =
  preboundInconclusiveCheckWithoutReason;
void invalidInconclusiveCheckWithoutReason;

const preboundFailedAnalysisWithoutEvidence = {
  ...completePassedPuzzleAnalysis,
  status: "failed" as const,
  reason_codes: ["synthetic_failure"] as [string],
  checks: [passedAnalysisCheck] as [typeof passedAnalysisCheck],
  findings: [] as [],
};
// @ts-expect-error failed analysis requires a failed check or a finding
const invalidFailedAnalysisWithoutEvidence: WorldForgeDeterministicGameAnalysisV1 =
  preboundFailedAnalysisWithoutEvidence;
void invalidFailedAnalysisWithoutEvidence;

type ConformanceGamepackState =
  WorldForgeDeterministicGamepackV1["logic"]["state_schema"][number];
declare const completeSourceState: Exclude<
  ConformanceGamepackState,
  { compiler_owned: true }
>;
declare const completeInternalCursor: Extract<
  ConformanceGamepackState,
  { compiler_owned: true }
>;
type ConformanceNarrativeGamepackLogic = Extract<
  WorldForgeDeterministicGamepackV1["logic"],
  { narrative_cursor: { compiler_owned: true } }
>;
type ConformancePuzzleGamepackLogic = Extract<
  WorldForgeDeterministicGamepackV1["logic"],
  { narrative_cursor: null }
>;
declare const completeNarrativeGamepackLogic: ConformanceNarrativeGamepackLogic;
declare const completePuzzleGamepackLogic: ConformancePuzzleGamepackLogic;
const duplicateCursorTuple: [
  typeof completeSourceState,
  typeof completeInternalCursor,
  typeof completeInternalCursor,
] = [
  completeSourceState,
  completeInternalCursor,
  completeInternalCursor,
];
const preboundNarrativeLogicWithDuplicateCursor = {
  ...completeNarrativeGamepackLogic,
  state_schema: duplicateCursorTuple,
};
// @ts-expect-error narrative logic requires exactly one canonical final cursor
const invalidNarrativeLogicWithDuplicateCursor: ConformanceNarrativeGamepackLogic =
  preboundNarrativeLogicWithDuplicateCursor;
void invalidNarrativeLogicWithDuplicateCursor;

const puzzleCursorTuple: [
  typeof completeSourceState,
  typeof completeInternalCursor,
] = [completeSourceState, completeInternalCursor];
const preboundNarrativeFreeLogicWithCursor = {
  ...completePuzzleGamepackLogic,
  state_schema: puzzleCursorTuple,
};
// @ts-expect-error narrative-free logic cannot retain compiler-owned cursor state
const invalidNarrativeFreeLogicWithCursor: ConformancePuzzleGamepackLogic =
  preboundNarrativeFreeLogicWithCursor;
void invalidNarrativeFreeLogicWithCursor;

type ConformanceNarrativeGamepack = Extract<
  WorldForgeDeterministicGamepackV1,
  { logic: { narrative_cursor: { compiler_owned: true } } }
>;
type ConformanceAuthoredNarrativeGamepack = Extract<
  WorldForgeDeterministicGamepackV1,
  {
    logic: { narrative_cursor: null };
    modules: {
      narrative: [
        ConformanceNarrativeGamepack["modules"]["narrative"][number],
        ...ConformanceNarrativeGamepack["modules"]["narrative"][number][],
      ];
    };
  }
>;
declare const completeNarrativeGamepack: ConformanceNarrativeGamepack;
declare const completeAuthoredNarrativeGamepack: ConformanceAuthoredNarrativeGamepack;
type ConformanceNarrativeChoiceUnit = Extract<
  ConformanceNarrativeGamepack["modules"]["narrative"][number]["units"][number],
  { unit_type: "choice" }
>;
declare const completeNarrativeChoiceUnit: ConformanceNarrativeChoiceUnit;
const preboundAuthoredNarrativeGamepackWithChoice = {
  ...completeAuthoredNarrativeGamepack,
  modules: {
    ...completeAuthoredNarrativeGamepack.modules,
    narrative: [
      {
        ...completeAuthoredNarrativeGamepack.modules.narrative[0],
        units: [completeNarrativeChoiceUnit] as [ConformanceNarrativeChoiceUnit],
      },
    ] as const,
  },
};
// @ts-expect-error authored narrative projection cannot contain choice units
const invalidAuthoredNarrativeGamepackWithChoice: WorldForgeDeterministicGamepackV1 =
  preboundAuthoredNarrativeGamepackWithChoice;
void invalidAuthoredNarrativeGamepackWithChoice;
const preboundAuthoredNarrativeGamepackWithTransitions = {
  ...completeAuthoredNarrativeGamepack,
  logic: {
    ...completeAuthoredNarrativeGamepack.logic,
    narrative_transitions: completeNarrativeGamepack.logic.narrative_transitions,
  },
};
// @ts-expect-error authored narrative projection cannot retain executable transitions
const invalidAuthoredNarrativeGamepackWithTransitions: WorldForgeDeterministicGamepackV1 =
  preboundAuthoredNarrativeGamepackWithTransitions;
void invalidAuthoredNarrativeGamepackWithTransitions;

const preboundNarrativeGamepackWithoutNarrativeModules = {
  ...completeNarrativeGamepack,
  modules: {
    ...completeNarrativeGamepack.modules,
    narrative: [] as [],
  },
};
// @ts-expect-error narrative logic requires at least one narrative module
const invalidNarrativeGamepackWithoutNarrativeModules: WorldForgeDeterministicGamepackV1 =
  preboundNarrativeGamepackWithoutNarrativeModules;
void invalidNarrativeGamepackWithoutNarrativeModules;

type ConformanceGamepackEffect =
  WorldForgeDeterministicGamepackV1["logic"]["effects"][number];
declare const completeResetEffect: Extract<
  ConformanceGamepackEffect,
  { operation: "reset" }
>;
const preboundResetEffectWithValue = {
  ...completeResetEffect,
  value: {
    kind: "state" as const,
    state_id: "board",
  },
};
// @ts-expect-error reset effects cannot retain a value payload
const invalidResetEffectWithValue: ConformanceGamepackEffect =
  preboundResetEffectWithValue;
void invalidResetEffectWithValue;

type ConformanceGamepackCondition =
  WorldForgeDeterministicGamepackV1["logic"]["conditions"][number];
declare const completeConstantCondition: Extract<
  ConformanceGamepackCondition,
  { operator: "constant" }
>;
const preboundConstantConditionWithLeft = {
  ...completeConstantCondition,
  left: {
    kind: "state" as const,
    state_id: "board",
  },
};
// @ts-expect-error constant conditions cannot retain a left operand
const invalidConstantConditionWithLeft: ConformanceGamepackCondition =
  preboundConstantConditionWithLeft;
void invalidConstantConditionWithLeft;

type ConformanceGamepackNarrativeUnit =
  WorldForgeDeterministicGamepackV1["modules"]["narrative"][number]["units"][number];
declare const completeStandardNarrativeUnit: Extract<
  ConformanceGamepackNarrativeUnit,
  { options?: never; ending_kind?: never }
>;
const preboundSceneWithEndingKind = {
  ...completeStandardNarrativeUnit,
  unit_type: "scene" as const,
  ending_kind: "neutral" as const,
};
// @ts-expect-error standard narrative units cannot retain ending_kind
const invalidSceneWithEndingKind: ConformanceGamepackNarrativeUnit =
  preboundSceneWithEndingKind;
void invalidSceneWithEndingKind;

declare const completeCapabilityLedger: WorldForgeMechanicCapabilityLedgerV1;
const preboundCapabilityLedgerWithProvider = {
  ...completeCapabilityLedger,
  provider: "not allowed",
};
// @ts-expect-error capability ledgers cannot contain provider metadata
const invalidProviderCapabilityLedger: WorldForgeMechanicCapabilityLedgerV1 =
  preboundCapabilityLedgerWithProvider;
void invalidProviderCapabilityLedger;

const preboundInconsistentAdapter = {
  adapter_id: "adapter_present",
  adapter_version: null,
  status: "absent" as const,
};
// @ts-expect-error absent adapters cannot retain an adapter identity
const invalidCapabilityLedgerAdapter: WorldForgeMechanicCapabilityLedgerV1["adapter"] =
  preboundInconsistentAdapter;
void invalidCapabilityLedgerAdapter;

declare const completeLedgerFeature: WorldForgeMechanicCapabilityLedgerV1["features"][number];
const preboundSupportedFeatureWithoutEvidence = {
  ...completeLedgerFeature,
  extension: null,
  missing_feature_ids: [],
  native_evidence: [],
  reason_code: "adapter_verified" as const,
  status: "supported_current" as const,
  test_evidence: [],
};
// @ts-expect-error supported capability branches require test and native evidence
const invalidSupportedFeatureWithoutEvidence: WorldForgeMechanicCapabilityLedgerV1["features"][number] =
  preboundSupportedFeatureWithoutEvidence;
void invalidSupportedFeatureWithoutEvidence;

declare const completeAssetSubject: WorldForgeAssetSubjectV1;
const preboundAssetSubjectWithProvider = {
  ...completeAssetSubject,
  provider: "not allowed",
};
// @ts-expect-error generic asset subjects are closed to provider metadata
const invalidProviderAssetSubject: WorldForgeAssetSubjectV1 =
  preboundAssetSubjectWithProvider;
void invalidProviderAssetSubject;

const preboundAssetSubjectWithUnexpectedField = {
  ...completeAssetSubject,
  unexpected_field: true,
};
// @ts-expect-error raw pre-bound values cannot enter the branded validated domain
const invalidUnvalidatedAssetContract: ValidatedGenericAssetContract =
  preboundAssetSubjectWithUnexpectedField;
void invalidUnvalidatedAssetContract;

declare const completeHumanProductionRequest: Extract<
  WorldForgeAssetProductionRequestV1,
  { production_class: "human" }
>;
declare const completeGenerativeProductionRequest: Extract<
  WorldForgeAssetProductionRequestV1,
  { production_class: "generative_authoring" }
>;
const preboundCrossedProductionRequest = {
  ...completeHumanProductionRequest,
  toolchain_requirements:
    completeGenerativeProductionRequest.toolchain_requirements,
};
// @ts-expect-error request production class and toolchain are inseparable
const invalidCrossedProductionRequest: WorldForgeAssetProductionRequestV1 =
  preboundCrossedProductionRequest;
void invalidCrossedProductionRequest;

const preboundHumanRequestWithFixedSeed = {
  ...completeHumanProductionRequest,
  reproducibility: {
    ...completeHumanProductionRequest.reproducibility,
    seed_policy: "fixed" as const,
  },
};
// @ts-expect-error human production forbids seed policies
const invalidHumanRequestWithFixedSeed: WorldForgeAssetProductionRequestV1 =
  preboundHumanRequestWithFixedSeed;
void invalidHumanRequestWithFixedSeed;

const preboundGenerativeRequestWithCrossedSeedPolicy = {
  ...completeGenerativeProductionRequest,
  toolchain_requirements: {
    ...completeGenerativeProductionRequest.toolchain_requirements,
    seed_policy:
      completeGenerativeProductionRequest.reproducibility.seed_policy === "fixed"
        ? ("recorded" as const)
        : ("fixed" as const),
  },
};
// @ts-expect-error generative reproducibility and toolchain seed policies are inseparable
const invalidGenerativeRequestWithCrossedSeedPolicy: WorldForgeAssetProductionRequestV1 =
  preboundGenerativeRequestWithCrossedSeedPolicy;
void invalidGenerativeRequestWithCrossedSeedPolicy;

declare const completeFixedProceduralProductionRequest: Extract<
  WorldForgeAssetProductionRequestV1,
  { production_class: "procedural_offline"; reproducibility: { seed_policy: "fixed" } }
>;
const preboundFixedProceduralRequestWithoutSeed = {
  ...completeFixedProceduralProductionRequest,
  toolchain_requirements: {
    ...completeFixedProceduralProductionRequest.toolchain_requirements,
    seed: null,
  },
};
// @ts-expect-error fixed procedural requests require an integer seed
const invalidFixedProceduralRequestWithoutSeed: WorldForgeAssetProductionRequestV1 =
  preboundFixedProceduralRequestWithoutSeed;
void invalidFixedProceduralRequestWithoutSeed;

declare const completeRecordedProceduralProductionRequest: Extract<
  WorldForgeAssetProductionRequestV1,
  { production_class: "procedural_offline"; reproducibility: { seed_policy: "recorded" } }
>;
const preboundRecordedProceduralRequestWithoutSeed = {
  ...completeRecordedProceduralProductionRequest,
  toolchain_requirements: {
    ...completeRecordedProceduralProductionRequest.toolchain_requirements,
    seed: null,
  },
};
// @ts-expect-error recorded procedural requests require an integer seed
const invalidRecordedProceduralRequestWithoutSeed: WorldForgeAssetProductionRequestV1 =
  preboundRecordedProceduralRequestWithoutSeed;
void invalidRecordedProceduralRequestWithoutSeed;

declare const completeHumanProductionReceipt: Extract<
  WorldForgeAssetProductionReceiptV1,
  { production_class: "human" }
>;
declare const completeGenerativeProductionReceipt: Extract<
  WorldForgeAssetProductionReceiptV1,
  { production_class: "generative_authoring" }
>;
const preboundCrossedProductionReceipt = {
  ...completeHumanProductionReceipt,
  executed_toolchain:
    completeGenerativeProductionReceipt.executed_toolchain,
};
// @ts-expect-error receipt production class and toolchain are inseparable
const invalidCrossedProductionReceipt: WorldForgeAssetProductionReceiptV1 =
  preboundCrossedProductionReceipt;
void invalidCrossedProductionReceipt;

declare const completeCompletedHumanProductionReceipt: Extract<
  WorldForgeAssetProductionReceiptV1,
  { production_class: "human"; status: "completed" }
>;
const preboundFailedReceiptWithOutputs = {
  ...completeCompletedHumanProductionReceipt,
  status: "failed" as const,
  failure_reasons: ["candidate_generation_failed"],
};
// @ts-expect-error failed receipts cannot retain candidate outputs
const invalidFailedReceiptWithOutputs: WorldForgeAssetProductionReceiptV1 =
  preboundFailedReceiptWithOutputs;
void invalidFailedReceiptWithOutputs;

const preboundCompletedReceiptWithFailures = {
  ...completeCompletedHumanProductionReceipt,
  failure_reasons: ["candidate_generation_failed"],
};
// @ts-expect-error completed receipts require an empty failure array
const invalidCompletedReceiptWithFailures: WorldForgeAssetProductionReceiptV1 =
  preboundCompletedReceiptWithFailures;
void invalidCompletedReceiptWithFailures;

declare const completeHumanAssetProvenance: Extract<
  WorldForgeSelectedAssetProvenanceRecordV1,
  { production_class: "human" }
>;
declare const completeGenerativeAssetProvenance: Extract<
  WorldForgeSelectedAssetProvenanceRecordV1,
  { production_class: "generative_authoring" }
>;
const preboundCrossedAssetProvenance = {
  ...completeHumanAssetProvenance,
  toolchain: completeGenerativeAssetProvenance.toolchain,
};
// @ts-expect-error provenance production class and toolchain are inseparable
const invalidCrossedAssetProvenance: WorldForgeSelectedAssetProvenanceRecordV1 =
  preboundCrossedAssetProvenance;
void invalidCrossedAssetProvenance;

const completeAssetLicenseRecord = ${assetLicenseConformanceLiteral} as const satisfies
  WorldForgeRuntimeSafeAssetLicenseRecordV1;
void completeAssetLicenseRecord;

const preboundAssetLicenseWithPrompt = {
  ...completeAssetLicenseRecord,
  prompt: "not allowed",
};
// @ts-expect-error runtime-safe license records cannot retain authoring prompts
const invalidPromptAssetLicense: WorldForgeRuntimeSafeAssetLicenseRecordV1 =
  preboundAssetLicenseWithPrompt;
void invalidPromptAssetLicense;

const preboundAssetLicenseCandidateWithCredentials = {
  ...completeAssetLicenseRecord,
  candidate: {
    ...completeAssetLicenseRecord.candidate,
    provider_credentials: { token: "not allowed" },
  },
};
// @ts-expect-error runtime candidate identities cannot retain provider credentials
const invalidCredentialAssetLicense: WorldForgeRuntimeSafeAssetLicenseRecordV1 =
  preboundAssetLicenseCandidateWithCredentials;
void invalidCredentialAssetLicense;

const preboundFixedAssetLicenseWithoutYear = {
  ...completeAssetLicenseRecord,
  copyright: {
    ...completeAssetLicenseRecord.copyright,
    year_policy: "fixed" as const,
    year: null,
  },
};
// @ts-expect-error a fixed copyright policy requires an integer year
const invalidFixedAssetLicenseWithoutYear: WorldForgeRuntimeSafeAssetLicenseRecordV1 =
  preboundFixedAssetLicenseWithoutYear;
void invalidFixedAssetLicenseWithoutYear;

const preboundNotApplicableAssetLicenseWithYear = {
  ...completeAssetLicenseRecord,
  copyright: {
    ...completeAssetLicenseRecord.copyright,
    year_policy: "not_applicable" as const,
    year: 2026,
  },
};
// @ts-expect-error a not-applicable copyright policy requires a null year
const invalidNotApplicableAssetLicenseWithYear: WorldForgeRuntimeSafeAssetLicenseRecordV1 =
  preboundNotApplicableAssetLicenseWithYear;
void invalidNotApplicableAssetLicenseWithYear;

const preboundUnapprovedCustomAssetLicense = {
  ...completeAssetLicenseRecord,
  license_basis: {
    kind: "custom" as const,
    identifier: "LicenseRef-Unreviewed-Custom-Terms" as const,
  },
};
// @ts-expect-error custom license identifiers are closed to the reviewed allowlist
const invalidUnapprovedCustomAssetLicense: WorldForgeRuntimeSafeAssetLicenseRecordV1 =
  preboundUnapprovedCustomAssetLicense;
void invalidUnapprovedCustomAssetLicense;

declare const completeAssetBinding: WorldForgeReviewedAssetTargetV1["bindings"][number];
const preboundAssetBindingWithPrompt = {
  ...completeAssetBinding,
  prompt: "not allowed",
};
// @ts-expect-error reviewed target bindings cannot retain prompts
const invalidPromptAssetBinding: WorldForgeReviewedAssetTargetV1["bindings"][number] =
  preboundAssetBindingWithPrompt;
void invalidPromptAssetBinding;

declare const completeAnimationAssetBinding: Extract<
  WorldForgeReviewedAssetTargetV1["bindings"][number],
  { kind: "animation_3d" }
>;
const preboundAnimationBindingWith2dRepresentation = {
  ...completeAnimationAssetBinding,
  representation: "2d" as const,
};
// @ts-expect-error GLB animation bindings require the 3d representation
const invalidAnimationBindingRepresentation: WorldForgeReviewedAssetTargetV1["bindings"][number] =
  preboundAnimationBindingWith2dRepresentation;
void invalidAnimationBindingRepresentation;

declare const completeNotApplicableAudio: Extract<
  WorldForgeReviewedAssetStyleV1["audio"],
  { status: "not_applicable" }
>;
const preboundNotApplicableAudioWithMusic = {
  ...completeNotApplicableAudio,
  music_direction: "invented",
};
// @ts-expect-error not-applicable audio cannot retain fake direction fields
const invalidNotApplicableAudio: WorldForgeReviewedAssetStyleV1["audio"] =
  preboundNotApplicableAudioWithMusic;
void invalidNotApplicableAudio;

declare const completeInventory: WorldForgeDeterministicAssetInventoryV1;
const preboundInventoryWithManualAssets = {
  ...completeInventory,
  manual_assets: [],
};
// @ts-expect-error inventories expose only deterministic derived assets
const invalidManualInventory: WorldForgeDeterministicAssetInventoryV1 =
  preboundInventoryWithManualAssets;
void invalidManualInventory;

declare const completeAssetSpecification: WorldForgeAssetSpecificationV1;
const preboundSpecificationWithSourcePath = {
  ...completeAssetSpecification,
  source_path: "not allowed",
};
// @ts-expect-error runtime asset specifications cannot retain source paths
const invalidSourcePathSpecification: WorldForgeAssetSpecificationV1 =
  preboundSpecificationWithSourcePath;
void invalidSourcePathSpecification;

const preboundSpecificationWithIncompleteSubject = {
  ...completeAssetSpecification,
  asset_subject: {
    format: "world-forge.asset_subject" as const,
  },
};
// @ts-expect-error asset subject identities require version, id, and content hash
const invalidIncompleteAssetSubject: WorldForgeAssetSpecificationV1 =
  preboundSpecificationWithIncompleteSubject;
void invalidIncompleteAssetSubject;

declare const completeTtfSpecificationOutput: Extract<
  WorldForgeAssetSpecificationV1["outputs"][number],
  { media_type: "font/ttf" }
>;
const preboundTtfOutputWithOtfContainer = {
  ...completeTtfSpecificationOutput,
  expectations: {
    ...completeTtfSpecificationOutput.expectations,
    container: "otf" as const,
  },
};
// @ts-expect-error TTF outputs cannot declare an OTF expectation container
const invalidTtfExpectationContainer: WorldForgeAssetSpecificationV1["outputs"][number] =
  preboundTtfOutputWithOtfContainer;
void invalidTtfExpectationContainer;

declare const completeFragmentSpecificationOutput: Extract<
  WorldForgeAssetSpecificationV1["outputs"][number],
  { role: "fragment_shader" }
>;
const preboundFragmentOutputWithVertexStage = {
  ...completeFragmentSpecificationOutput,
  expectations: {
    ...completeFragmentSpecificationOutput.expectations,
    stage: "vertex" as const,
  },
};
// @ts-expect-error fragment outputs cannot declare a vertex shader expectation
const invalidFragmentExpectationStage: WorldForgeAssetSpecificationV1["outputs"][number] =
  preboundFragmentOutputWithVertexStage;
void invalidFragmentExpectationStage;
`;

function canonicalJson(value, omitContentHash = false) {
    if (
        value === null ||
        typeof value === "boolean" ||
        typeof value === "string"
    ) {
        return JSON.stringify(value);
    }
    if (typeof value === "number") {
        if (!Number.isSafeInteger(value)) {
            throw new Error(
                "World Forge fixture integers must be JavaScript-safe",
            );
        }
        return JSON.stringify(value);
    }
    if (Array.isArray(value)) {
        return `[${value.map((item) => canonicalJson(item)).join(",")}]`;
    }
    if (typeof value === "object") {
        const entries = Object.entries(value)
            .filter(([key]) => !omitContentHash || key !== "content_hash")
            .sort(([left], [right]) =>
                Buffer.compare(Buffer.from(left), Buffer.from(right)),
            );
        return `{${entries
            .map(
                ([key, item]) =>
                    `${JSON.stringify(key)}:${canonicalJson(item)}`,
            )
            .join(",")}}`;
    }
    throw new Error("World Forge fixtures must contain only JSON values");
}

function reseal(document) {
    document.content_hash = createHash("sha256")
        .update(canonicalJson(document, true), "utf8")
        .digest("hex");
    return document;
}

async function fixtureJsonFiles(root) {
    const result = [];
    for (const entry of await readdir(root, { withFileTypes: true })) {
        const candidate = path.join(root, entry.name);
        if (entry.isDirectory()) {
            result.push(...(await fixtureJsonFiles(candidate)));
        } else if (entry.isFile() && entry.name.endsWith(".json")) {
            result.push(candidate);
        }
    }
    return result.sort((left, right) =>
        Buffer.compare(Buffer.from(left), Buffer.from(right)),
    );
}

async function verifyFixtureHashes() {
    for (const fixture of await fixtureJsonFiles(contractsFixtureRoot)) {
        const document = await readStrictJsonObject(fixture);
        if (typeof document.content_hash !== "string") {
            throw new Error(
                `World Forge fixture lacks content_hash: ${fixture}`,
            );
        }
        const digest = createHash("sha256")
            .update(canonicalJson(document, true), "utf8")
            .digest("hex");
        if (digest !== document.content_hash) {
            throw new Error(
                `World Forge fixture hash differs in Node: ${fixture}`,
            );
        }
    }
}

function verifyGeneratedContractDeclarations(contents) {
    const virtualPath = path.resolve(
        appRoot,
        "src/generated/world-forge-contracts.virtual.d.ts",
    );
    const compilerOptions = {
        module: ts.ModuleKind.ESNext,
        noEmit: true,
        skipLibCheck: false,
        strict: true,
        target: ts.ScriptTarget.ESNext,
        types: [],
    };
    const host = ts.createCompilerHost(compilerOptions);
    const defaultGetSourceFile = host.getSourceFile.bind(host);
    host.fileExists = (file) =>
        path.resolve(file) === virtualPath || ts.sys.fileExists(file);
    host.readFile = (file) =>
        path.resolve(file) === virtualPath ? contents : ts.sys.readFile(file);
    host.getSourceFile = (
        file,
        languageVersion,
        onError,
        shouldCreateNewSourceFile,
    ) =>
        path.resolve(file) === virtualPath
            ? ts.createSourceFile(
                  file,
                  contents,
                  languageVersion,
                  true,
                  ts.ScriptKind.TS,
              )
            : defaultGetSourceFile(
                  file,
                  languageVersion,
                  onError,
                  shouldCreateNewSourceFile,
              );
    const program = ts.createProgram([virtualPath], compilerOptions, host);
    const diagnostics = ts.getPreEmitDiagnostics(program);
    if (diagnostics.length === 0) {
        return;
    }
    const details = diagnostics
        .map((diagnostic) => {
            const message = ts.flattenDiagnosticMessageText(
                diagnostic.messageText,
                "\n",
            );
            if (
                diagnostic.file === undefined ||
                diagnostic.start === undefined
            ) {
                return `TS${diagnostic.code}: ${message}`;
            }
            const position = diagnostic.file.getLineAndCharacterOfPosition(
                diagnostic.start,
            );
            return `${path.basename(diagnostic.file.fileName)}:${position.line + 1}:${
                position.character + 1
            } TS${diagnostic.code}: ${message}`;
        })
        .join("\n");
    throw new Error(
        `Generated World Forge declarations are invalid:\n${details}`,
    );
}

async function verifyContractSchemas() {
    const schemaNames = [
        "creation-project",
        "creation-profile",
        "creation-source-manifest",
        "creation-workflow-status",
        "creation-readiness",
        "creation-handoff",
        "studio-creation-artifact",
        "studio-creation-evidence",
        "studio-creation-output-grant",
        "studio-creation-output-grant-v6",
        "studio-creation-job",
        "studio-creation-worker",
        "world-module",
        "activity-module",
        "narrative-module",
        "system-module",
        "logic-module",
        "phase-report-v2",
        "phase-report-v3",
        "lorepack",
        "gamepack",
        "game-analysis",
        "mechanic-capability-ledger",
        "generic-asset-subject",
        "generic-asset-target",
        "generic-asset-style",
        "generic-asset-inventory",
        "generic-asset-spec",
        "generic-asset-production-request",
        "generic-asset-production-receipt",
        "generic-asset-selection",
        "generic-asset-provenance-record",
        "generic-asset-license-record",
        "generic-asset-processing-recipe",
        "generic-asset-processing-receipt",
        "generic-asset-qa-report",
        "generic-asset-qa-review-receipt",
        "generic-asset-manifest",
        "generic-asset-release-authority",
        "generic-assetpack",
        "generic-runtime-adapter",
        "generic-runtime-adapter-registry",
        "game-runtime-snapshot",
        "game-runtime-composition",
        "generic-runtime-evidence",
        "generic-runtime-support-report",
        "runtime-support-authority",
        "hosted-native-release-authority",
        "game-runtime-bundle",
        "runtime-implementation",
        "runtime-platform-lock",
        "game-materialization-bundle",
        "game-package",
        "game-package-extraction",
        "standalone-game",
        "standalone-game-lock",
        "standalone-platform",
        "game-save",
        "game-replay",
        "persistence-generation",
        "game-execution-script",
        "headless-execution-receipt",
        "headless-evidence-set",
    ];
    const ajv = new Ajv2020({
        allErrors: true,
        ownProperties: true,
        strict: true,
    });
    ajv.addKeyword({
        keyword: "x-world-forge-final-compiler-owned",
        schemaType: "boolean",
        type: "array",
        validate: (required, data) =>
            !required ||
            (data.length > 0 &&
                data[data.length - 1] !== null &&
                typeof data[data.length - 1] === "object" &&
                data[data.length - 1].compiler_owned === true),
    });
    ajv.addKeyword({
        keyword: "x-world-forge-canonical-glyph-ranges",
        schemaType: "boolean",
        type: "array",
        validate: (required, data) =>
            !required || areCanonicalGenericAssetGlyphRanges(data),
    });
    ajv.addKeyword({
        keyword: "x-world-forge-portable-runtime-path",
        schemaType: "boolean",
        type: "string",
        validate: (required, data) =>
            !required || isPortableGenericAssetRuntimePath(data),
    });
    ajv.addKeyword({
        keyword: "x-world-forge-distinct-content-hashes",
        schemaType: "boolean",
        type: "object",
        validate: (required, data) =>
            !required || hasDistinctGenericAssetContentHashes(data),
    });
    ajv.addKeyword({
        keyword: "x-world-forge-canonical-content-hash",
        schemaType: "boolean",
        type: "object",
        validate: (required, data) =>
            !required || hasCanonicalGenericAssetContentHash(data),
    });
    ajv.addKeyword({
        keyword: "x-world-forge-production-request-coherent",
        schemaType: "boolean",
        type: "object",
        validate: (required, data) =>
            !required || hasCoherentGenericAssetProductionRequest(data),
    });
    ajv.addKeyword({
        keyword: "x-world-forge-d2b-coherent",
        schemaType: "string",
        type: "object",
        validate: (kind, data) =>
            hasCoherentGenericAssetD2bContract(data, kind),
    });
    ajv.addKeyword({
        keyword: "x-world-forge-runtime-safe-notice",
        schemaType: "boolean",
        type: "string",
        validate: (required, data) =>
            !required || isRuntimeSafeGenericAssetNotice(data),
    });
    ajv.addKeyword({
        keyword: "x-world-forge-safe-runtime-text",
        schemaType: "boolean",
        type: "string",
        validate: (required, data) =>
            !required || isSafeGenericAssetRuntimeText(data),
    });
    ajv.addKeyword({
        keyword: "x-world-forge-sha256-text-match",
        schemaType: "boolean",
        type: "object",
        validate: (required, data) =>
            !required || hasMatchingGenericAssetTextSha256(data),
    });
    ajv.addKeyword({
        keyword: "x-world-forge-glyph-count-match",
        schemaType: "boolean",
        type: "object",
        validate: (required, data) =>
            !required || hasMatchingGenericAssetGlyphCount(data),
    });
    ajv.addKeyword({
        keyword: "x-world-forge-canonical-object-array",
        schemaType: "object",
        type: "array",
        validate: (policy, data) =>
            isCanonicalGenericAssetObjectArray(data, policy),
    });
    ajv.addKeyword({
        keyword: "x-world-forge-receipt-lineage-roots",
        schemaType: "boolean",
        type: "object",
        validate: (required, data) =>
            !required || hasExactGenericAssetReceiptLineageRoots(data),
    });
    ajv.addKeyword({
        keyword: "x-world-forge-canonical-string-array",
        schemaType: "boolean",
        type: "array",
        validate: (required, data) =>
            !required || isCanonicalGenericAssetStringArray(data),
    });
    ajv.addKeyword({
        keyword: "x-world-forge-portable-path-tree",
        schemaType: "string",
        type: "array",
        validate: (field, data) => hasPortableGenericAssetPathTree(data, field),
    });
    ajv.addKeyword({
        keyword: "x-world-forge-generic-assetpack-coherent",
        schemaType: "boolean",
        type: "object",
        validate: (required, data) =>
            !required || hasCoherentGenericAssetpack(data),
    });
    ajv.addKeyword({
        keyword: "x-world-forge-generic-runtime-coherent",
        schemaType: "string",
        type: "object",
        validate: (kind, data) => hasCoherentGenericRuntimeContract(data, kind),
    });
    ajv.addKeyword({
        keyword: "x-world-forge-game-runtime-bundle-coherent",
        schemaType: "boolean",
        type: "object",
        validate: (required, data) =>
            !required || hasCoherentGameRuntimeBundle(data),
    });
    ajv.addKeyword({
        keyword: "x-world-forge-runtime-implementation-coherent",
        schemaType: "boolean",
        type: "object",
        validate: (required, data) =>
            !required || hasCoherentRuntimeImplementation(data),
    });
    ajv.addKeyword({
        keyword: "x-world-forge-runtime-platform-lock-audited",
        schemaType: "boolean",
        type: "object",
        validate: (required, data) =>
            !required || hasAuditedRuntimePlatformLock(data),
    });
    ajv.addKeyword({
        keyword: "x-world-forge-game-materialization-bundle-coherent",
        schemaType: "boolean",
        type: "object",
        validate: (required, data) =>
            !required || hasCoherentGameMaterializationBundle(data),
    });
    ajv.addKeyword({
        keyword: "x-world-forge-game-package-coherent",
        schemaType: "boolean",
        type: "object",
        validate: (required, data) => !required || hasCoherentGamePackage(data),
    });
    ajv.addKeyword({
        keyword: "x-world-forge-game-package-extraction-coherent",
        schemaType: "boolean",
        type: "object",
        validate: (required, data) =>
            !required ||
            data.extracted_tree_hash === data.payload_lock?.tree_hash,
    });
    ajv.addKeyword({
        keyword: "x-world-forge-standalone-game-coherent",
        schemaType: "boolean",
        type: "object",
        validate: (required, data) =>
            !required || hasCoherentStandaloneGame(data),
    });
    ajv.addKeyword({
        keyword: "x-world-forge-standalone-game-lock-coherent",
        schemaType: "boolean",
        type: "object",
        validate: (required, data) =>
            !required || hasCoherentStandaloneGameLock(data),
    });
    ajv.addKeyword({
        keyword: "x-world-forge-standalone-platform-coherent",
        schemaType: "boolean",
        type: "object",
        validate: (required, data) =>
            !required || hasCoherentStandalonePlatform(data),
    });
    ajv.addKeyword({
        keyword: "x-world-forge-game-persistence-coherent",
        schemaType: "string",
        type: "object",
        validate: (kind, data) =>
            (kind === "game_save" || kind === "game_replay") &&
            hasCoherentGamePersistence(data),
    });
    ajv.addKeyword({
        keyword: "x-world-forge-persistence-generation-coherent",
        schemaType: "boolean",
        type: "object",
        validate: (required, data) =>
            !required || hasCoherentPersistenceGeneration(data),
    });
    ajv.addKeyword({
        keyword: "x-world-forge-generic-headless-coherent",
        schemaType: "string",
        type: "object",
        validate: (kind, data) =>
            hasCoherentGenericHeadlessContract(data, kind),
    });
    const schemaByFormat = new Map();
    const schemaByFormatVersion = new Map();
    for (const name of schemaNames) {
        const contractSchema = await readStrictJsonObject(
            path.join(contractsSchemaRoot, `${name}.schema.json`),
        );
        if (name.startsWith("generic-asset-")) {
            const identifier = contractSchema.$defs?.id;
            if (
                identifier?.type !== "string" ||
                identifier.minLength !== 2 ||
                identifier.maxLength !== 64 ||
                identifier.pattern !== GENERIC_ASSET_ID_PATTERN
            ) {
                throw new Error(
                    `${name} does not use the canonical generic asset ID domain`,
                );
            }
        }
        if (
            name === "logic-module" &&
            contractSchema.$defs?.runtimeString?.pattern !==
                GENERIC_ASSET_RUNTIME_STRING_PATTERN
        ) {
            throw new Error(
                "generic asset runtime-text grammar drifted from logic contracts",
            );
        }
        if (
            [
                "generic-asset-production-request",
                "generic-asset-production-receipt",
                "generic-asset-selection",
                "generic-asset-provenance-record",
                "generic-asset-license-record",
                "generic-asset-processing-recipe",
                "generic-asset-processing-receipt",
                "generic-asset-qa-report",
                "generic-asset-qa-review-receipt",
                "generic-asset-manifest",
                "generic-asset-release-authority",
                "generic-assetpack",
            ].includes(name) &&
            contractSchema["x-world-forge-canonical-content-hash"] !== true
        ) {
            throw new Error(`${name} does not bind its canonical content hash`);
        }
        if (
            name === "generic-assetpack" &&
            contractSchema["x-world-forge-generic-assetpack-coherent"] !== true
        ) {
            throw new Error("generic assetpack coherence is not enforced");
        }
        if (
            [
                "generic-runtime-adapter",
                "generic-runtime-adapter-registry",
                "game-runtime-snapshot",
                "game-runtime-composition",
                "generic-runtime-evidence",
                "generic-runtime-support-report",
                "runtime-support-authority",
            ].includes(name) &&
            (contractSchema["x-world-forge-canonical-content-hash"] !== true ||
                typeof contractSchema[
                    "x-world-forge-generic-runtime-coherent"
                ] !== "string")
        ) {
            throw new Error(
                `${name} does not enforce generic runtime coherence`,
            );
        }
        if (
            name === "game-runtime-bundle" &&
            (contractSchema["x-world-forge-canonical-content-hash"] !== true ||
                contractSchema["x-world-forge-game-runtime-bundle-coherent"] !==
                    true)
        ) {
            throw new Error("game runtime bundle coherence is not enforced");
        }
        const materializationKeyword = {
            "runtime-implementation":
                "x-world-forge-runtime-implementation-coherent",
            "runtime-platform-lock":
                "x-world-forge-runtime-platform-lock-audited",
            "game-materialization-bundle":
                "x-world-forge-game-materialization-bundle-coherent",
            "standalone-game": "x-world-forge-standalone-game-coherent",
            "standalone-game-lock":
                "x-world-forge-standalone-game-lock-coherent",
            "standalone-platform": "x-world-forge-standalone-platform-coherent",
        }[name];
        if (
            materializationKeyword !== undefined &&
            (contractSchema["x-world-forge-canonical-content-hash"] !== true ||
                contractSchema[materializationKeyword] !== true)
        ) {
            throw new Error(
                `${name} does not enforce executable materialization coherence`,
            );
        }
        if (
            name === "game-package" &&
            (contractSchema["x-world-forge-canonical-content-hash"] !== true ||
                contractSchema["x-world-forge-game-package-coherent"] !== true)
        ) {
            throw new Error("game package coherence is not enforced");
        }
        if (
            name === "game-package-extraction" &&
            (contractSchema["x-world-forge-canonical-content-hash"] !== true ||
                contractSchema[
                    "x-world-forge-game-package-extraction-coherent"
                ] !== true)
        ) {
            throw new Error(
                "game package extraction coherence is not enforced",
            );
        }
        if (
            ["game-save", "game-replay"].includes(name) &&
            (contractSchema["x-world-forge-canonical-content-hash"] !== true ||
                contractSchema["x-world-forge-game-persistence-coherent"] !==
                    name.replace("-", "_"))
        ) {
            throw new Error(
                `${name} does not enforce game persistence coherence`,
            );
        }
        if (
            name === "persistence-generation" &&
            (contractSchema["x-world-forge-canonical-content-hash"] !== true ||
                contractSchema[
                    "x-world-forge-persistence-generation-coherent"
                ] !== true)
        ) {
            throw new Error("persistence generation coherence is not enforced");
        }
        const headlessKind = {
            "game-execution-script": "game_execution_script",
            "headless-execution-receipt": "headless_execution_receipt",
            "headless-evidence-set": "headless_evidence_set",
        }[name];
        if (
            headlessKind !== undefined &&
            (contractSchema["x-world-forge-canonical-content-hash"] !== true ||
                contractSchema["x-world-forge-generic-headless-coherent"] !==
                    headlessKind)
        ) {
            throw new Error(
                `${name} does not enforce generic headless coherence`,
            );
        }
        if (
            name === "generic-asset-production-request" &&
            contractSchema["x-world-forge-production-request-coherent"] !== true
        ) {
            throw new Error(
                "generic asset production request coherence is not enforced",
            );
        }
        const d2bKind = {
            "generic-asset-processing-recipe": "recipe",
            "generic-asset-processing-receipt": "receipt",
            "generic-asset-qa-report": "qa",
            "generic-asset-manifest": "manifest",
        }[name];
        if (
            d2bKind !== undefined &&
            contractSchema["x-world-forge-d2b-coherent"] !== d2bKind
        ) {
            throw new Error(`${name} does not enforce exact D2b coherence`);
        }
        if (name === "generic-asset-selection") {
            const rationale =
                contractSchema.properties?.review?.properties?.rationale;
            if (
                rationale?.pattern !== GENERIC_ASSET_RUNTIME_STRING_PATTERN ||
                rationale["x-world-forge-safe-runtime-text"] !== true ||
                contractSchema["x-world-forge-receipt-lineage-roots"] !== true
            ) {
                throw new Error(
                    "generic asset selection rationale or receipt-lineage roots drifted",
                );
            }
        }
        if (name === "generic-asset-license-record") {
            const holder =
                contractSchema.properties?.copyright?.properties?.holder;
            const notice = contractSchema.properties?.runtime_notice;
            if (
                holder?.pattern !== GENERIC_ASSET_RUNTIME_STRING_PATTERN ||
                holder["x-world-forge-safe-runtime-text"] !== true ||
                notice?.properties?.text?.pattern !==
                    GENERIC_ASSET_RUNTIME_STRING_PATTERN ||
                notice?.properties?.text?.[
                    "x-world-forge-safe-runtime-text"
                ] !== true ||
                notice?.properties?.text?.[
                    "x-world-forge-runtime-safe-notice"
                ] !== true ||
                notice?.["x-world-forge-sha256-text-match"] !== true
            ) {
                throw new Error(
                    "generic asset license runtime-text/hash grammar drifted",
                );
            }
        }
        if (name === "generic-asset-spec") {
            const glyphRanges =
                contractSchema.$defs?.fontExpectation?.properties?.glyph_ranges;
            if (
                glyphRanges?.items?.pattern !==
                    GENERIC_ASSET_GLYPH_RANGE_PATTERN ||
                glyphRanges["x-world-forge-canonical-glyph-ranges"] !== true ||
                contractSchema.$defs?.path?.[
                    "x-world-forge-portable-runtime-path"
                ] !== true
            ) {
                throw new Error(
                    "generic-asset-spec does not use the canonical glyph/path rules",
                );
            }
        }
        if (name === "generic-asset-style" || name === "generic-asset-spec") {
            const shortRuntimeString = contractSchema.$defs?.runtimeShortString;
            if (
                shortRuntimeString?.allOf?.[0]?.$ref !==
                    "#/$defs/runtimeString" ||
                shortRuntimeString?.allOf?.[1]?.maxLength !== 256 ||
                shortRuntimeString?.allOf?.[1]?.type !== "string"
            ) {
                throw new Error(
                    `${name} does not use the canonical short runtime string limit`,
                );
            }
        }
        if (
            name === "generic-asset-style" &&
            (contractSchema.$defs?.visual?.properties?.camera?.$ref !==
                "#/$defs/runtimeShortString" ||
                contractSchema.$defs?.visual?.properties?.ui?.properties
                    ?.density?.$ref !== "#/$defs/runtimeShortString")
        ) {
            throw new Error(
                "generic-asset-style camera/density bounds drifted from Python",
            );
        }
        if (
            name === "generic-asset-spec" &&
            contractSchema.$defs?.jsonExpectation?.properties?.schema_id
                ?.$ref !== "#/$defs/runtimeShortString"
        ) {
            throw new Error(
                "generic-asset-spec schema_id bound drifted from Python",
            );
        }
        ajv.addSchema(contractSchema);
        const formatProperties =
            contractSchema.properties ??
            contractSchema.$defs?.common?.properties;
        const formatName = formatProperties?.format?.const;
        const formatVersion =
            formatProperties?.format_version?.const ??
            (name === "phase-report-v2" ? 2 : undefined);
        if (typeof formatName !== "string") {
            throw new Error(
                `${name} does not expose a canonical format discriminator`,
            );
        }
        if (!schemaByFormat.has(formatName)) {
            schemaByFormat.set(formatName, contractSchema.$id);
        }
        if (formatVersion !== undefined) {
            schemaByFormatVersion.set(
                `${formatName}@${formatVersion}`,
                contractSchema.$id,
            );
        }
    }
    const documents = new Map();
    for (const fixture of await fixtureJsonFiles(contractsFixtureRoot)) {
        const document = await readStrictJsonObject(fixture);
        const schemaId =
            schemaByFormatVersion.get(
                `${document.format}@${document.format_version}`,
            ) ?? schemaByFormat.get(document.format);
        if (!schemaId || !ajv.validate(schemaId, document)) {
            throw new Error(
                `World Forge fixture fails strict AJV: ${fixture}: ${ajv.errorsText(ajv.errors)}`,
            );
        }
        const fixtureKey = toPortableFixtureKey(
            path.relative(contractsFixtureRoot, fixture),
            path.sep,
        );
        documents.set(fixtureKey, document);
    }

    const invalidWorld = structuredClone(
        documents.get("universe-library/source/world/canon.json"),
    );
    invalidWorld.events = [
        { id: "event_one", sequence: 1, summary: "Wrong payload." },
    ];
    if (ajv.validate(schemaByFormat.get(invalidWorld.format), invalidWorld)) {
        throw new Error("World module discriminator accepted a mixed payload");
    }

    const invalidNarrative = structuredClone(
        documents.get("branching-narrative/source/narrative/branching.json"),
    );
    invalidNarrative.units[0].ending_kind = "neutral";
    if (
        ajv.validate(
            schemaByFormat.get(invalidNarrative.format),
            invalidNarrative,
        )
    ) {
        throw new Error(
            "Narrative unit discriminator accepted a mixed payload",
        );
    }

    const missingNarrativeEntry = structuredClone(
        documents.get("branching-narrative/source/narrative/branching.json"),
    );
    delete missingNarrativeEntry.entry_unit_ids;
    if (
        ajv.validate(
            schemaByFormat.get(missingNarrativeEntry.format),
            missingNarrativeEntry,
        )
    ) {
        throw new Error("Narrative module accepted missing entry_unit_ids");
    }

    const nonTerminalEnding = structuredClone(
        documents.get("branching-narrative/source/narrative/branching.json"),
    );
    nonTerminalEnding.units[1].next_unit_ids = ["ending_right"];
    if (
        ajv.validate(
            schemaByFormat.get(nonTerminalEnding.format),
            nonTerminalEnding,
        )
    ) {
        throw new Error(
            "Narrative module accepted an ending with an outgoing transition",
        );
    }

    const runtimeAiLogic = structuredClone(
        documents.get("abstract-puzzle/source/logic/puzzle.json"),
    );
    runtimeAiLogic.runtime_ai = true;
    if (
        ajv.validate(schemaByFormat.get(runtimeAiLogic.format), runtimeAiLogic)
    ) {
        throw new Error("Logic module accepted pre-bound runtime AI");
    }

    if (
        !Array.isArray(logicRuntimeStringCorpus.accepted) ||
        !Array.isArray(logicRuntimeStringCorpus.rejected)
    ) {
        throw new Error(
            "Logic runtime-string corpus must contain accepted/rejected arrays",
        );
    }
    for (const candidate of logicRuntimeStringCorpus.accepted) {
        const acceptedRuntimeString = structuredClone(
            documents.get("abstract-puzzle/source/logic/puzzle.json"),
        );
        acceptedRuntimeString.title = candidate;
        if (
            !ajv.validate(
                schemaByFormat.get(acceptedRuntimeString.format),
                acceptedRuntimeString,
            )
        ) {
            throw new Error(
                `Logic runtime-string grammar rejected accepted corpus value: ${JSON.stringify(candidate)}`,
            );
        }
    }
    for (const candidate of logicRuntimeStringCorpus.rejected) {
        const unsafeTitle = structuredClone(
            documents.get("abstract-puzzle/source/logic/puzzle.json"),
        );
        unsafeTitle.title = candidate;
        if (ajv.validate(schemaByFormat.get(unsafeTitle.format), unsafeTitle)) {
            throw new Error(
                `Logic title accepted unsafe runtime-string corpus value: ${JSON.stringify(candidate)}`,
            );
        }

        const unsafeAllowedValue = structuredClone(
            documents.get("branching-narrative/source/logic/branching.json"),
        );
        unsafeAllowedValue.state_variables[0].allowed_values[0] = candidate;
        if (
            ajv.validate(
                schemaByFormat.get(unsafeAllowedValue.format),
                unsafeAllowedValue,
            )
        ) {
            throw new Error(
                `Logic state domain accepted unsafe runtime-string corpus value: ${JSON.stringify(candidate)}`,
            );
        }

        const unsafeLiteral = structuredClone(
            documents.get("branching-narrative/source/logic/branching.json"),
        );
        unsafeLiteral.effects[0].value.value = candidate;
        if (
            ajv.validate(
                schemaByFormat.get(unsafeLiteral.format),
                unsafeLiteral,
            )
        ) {
            throw new Error(
                `Logic literal accepted unsafe runtime-string corpus value: ${JSON.stringify(candidate)}`,
            );
        }

        const unsafeArrayLiteral = structuredClone(
            documents.get("abstract-puzzle/source/logic/puzzle.json"),
        );
        unsafeArrayLiteral.effects[0] = {
            action_id: "restart_board",
            id: "reset_board",
            invalid_transition_policy: "reject_transition",
            operation: "set",
            state_id: "board",
            value: {
                kind: "literal",
                value: [candidate],
                value_type: "string_array",
            },
        };
        if (
            ajv.validate(
                schemaByFormat.get(unsafeArrayLiteral.format),
                unsafeArrayLiteral,
            )
        ) {
            throw new Error(
                `Logic array literal accepted unsafe runtime-string corpus value: ${JSON.stringify(candidate)}`,
            );
        }
    }

    const mixedLogicOperand = structuredClone(
        documents.get("abstract-puzzle/source/logic/puzzle.json"),
    );
    mixedLogicOperand.conditions[0].left.state_id = "board";
    if (
        ajv.validate(
            schemaByFormat.get(mixedLogicOperand.format),
            mixedLogicOperand,
        )
    ) {
        throw new Error(
            "Logic operand accepted mixed parameter and state fields",
        );
    }

    const mixedLogicCondition = structuredClone(
        documents.get("abstract-puzzle/source/logic/puzzle.json"),
    );
    mixedLogicCondition.conditions[0].value = true;
    if (
        ajv.validate(
            schemaByFormat.get(mixedLogicCondition.format),
            mixedLogicCondition,
        )
    ) {
        throw new Error("Logic condition accepted mixed operator payloads");
    }

    const mixedLogicEffect = structuredClone(
        documents.get("abstract-puzzle/source/logic/puzzle.json"),
    );
    mixedLogicEffect.effects[1].value = {
        kind: "literal",
        value: true,
        value_type: "boolean",
    };
    if (
        ajv.validate(
            schemaByFormat.get(mixedLogicEffect.format),
            mixedLogicEffect,
        )
    ) {
        throw new Error("Logic effect accepted mixed operation payloads");
    }

    const missingTransitionPolicy = structuredClone(
        documents.get("abstract-puzzle/source/logic/puzzle.json"),
    );
    delete missingTransitionPolicy.effects[0].invalid_transition_policy;
    if (
        ajv.validate(
            schemaByFormat.get(missingTransitionPolicy.format),
            missingTransitionPolicy,
        )
    ) {
        throw new Error("Logic effect accepted a missing transition policy");
    }

    for (const collection of [
        "actions",
        "conditions",
        "effects",
        "state_variables",
        "rules",
        "goals",
        "failures",
        "endings",
        "events",
        "presentation_hooks",
        "mechanics",
        "extensions",
    ]) {
        const nestedCredentials = structuredClone(
            documents.get("abstract-puzzle/source/logic/puzzle.json"),
        );
        if (
            collection === "extensions" &&
            nestedCredentials.extensions.length === 0
        ) {
            nestedCredentials.extensions.push({
                id: "example.optional",
                version: 1,
                required: false,
                content_hash:
                    "0000000000000000000000000000000000000000000000000000000000000000",
            });
        }
        const target = nestedCredentials[collection][0];
        target.provider_credentials = { token: "forbidden" };
        if (
            ajv.validate(
                schemaByFormat.get(nestedCredentials.format),
                nestedCredentials,
            )
        ) {
            throw new Error(
                `Logic ${collection} accepted nested provider credentials`,
            );
        }
    }

    const oversizedLogic = structuredClone(
        documents.get("abstract-puzzle/source/logic/puzzle.json"),
    );
    oversizedLogic.actions = Array.from({ length: 129 }, () =>
        structuredClone(oversizedLogic.actions[0]),
    );
    if (
        ajv.validate(schemaByFormat.get(oversizedLogic.format), oversizedLogic)
    ) {
        throw new Error("Logic module accepted an oversized action collection");
    }

    const invalidP00LogicEvidence = structuredClone(
        documents.get("branching-narrative/phase-reports/p00_brief.json"),
    );
    invalidP00LogicEvidence.evidence.push({
        evidence_id: "logic_subject",
        claim: "Invalid phase scope.",
        subject: {
            format: "world-forge.logic_module",
            format_version: 1,
            id: "branching_logic",
            content_hash: documents.get(
                "branching-narrative/source/logic/branching.json",
            ).content_hash,
        },
    });
    if (
        ajv.validate(
            schemaByFormat.get(invalidP00LogicEvidence.format),
            invalidP00LogicEvidence,
        )
    ) {
        throw new Error("P00 accepted supplemental logic-module evidence");
    }

    const incompleteReference = structuredClone(
        documents.get("abstract-puzzle/source/manifest.json"),
    );
    delete incompleteReference.modules.activity_modules[0].id;
    if (
        ajv.validate(
            schemaByFormat.get(incompleteReference.format),
            incompleteReference,
        )
    ) {
        throw new Error(
            "Source manifest accepted an incomplete module reference",
        );
    }

    const invalidNoneProfile = structuredClone(
        documents.get("abstract-puzzle/profile.json"),
    );
    invalidNoneProfile.production.content_modes.world = "authored";
    if (
        ajv.validate(
            schemaByFormat.get(invalidNoneProfile.format),
            invalidNoneProfile,
        )
    ) {
        throw new Error("Creation profile accepted production for world:none");
    }

    const invalidNoneDependency = structuredClone(
        documents.get("abstract-puzzle/profile.json"),
    );
    invalidNoneDependency.gameplay.dependencies.authored.push("world:invented");
    if (
        ajv.validate(
            schemaByFormat.get(invalidNoneDependency.format),
            invalidNoneDependency,
        )
    ) {
        throw new Error(
            "Creation profile accepted a world dependency for world:none",
        );
    }

    const invalidNoneFeature = structuredClone(
        documents.get("abstract-puzzle/profile.json"),
    );
    invalidNoneFeature.runtime_target.required_features.push(
        "narrative:invented",
    );
    if (
        ajv.validate(
            schemaByFormat.get(invalidNoneFeature.format),
            invalidNoneFeature,
        )
    ) {
        throw new Error(
            "Creation profile accepted a narrative feature for narrative:none",
        );
    }

    const mismatchedPresentation = structuredClone(
        documents.get("abstract-puzzle/profile.json"),
    );
    mismatchedPresentation.runtime_target.presentation_mode = "text";
    if (
        ajv.validate(
            schemaByFormat.get(mismatchedPresentation.format),
            mismatchedPresentation,
        )
    ) {
        throw new Error(
            "Creation profile accepted mismatched presentation modes",
        );
    }

    const invalidPhase = structuredClone(
        documents.get("branching-narrative/phase-reports/p08_world_arcs.json"),
    );
    invalidPhase.status = "not_applicable";
    invalidPhase.rationale.code = "world_absent";
    invalidPhase.output_evidence = null;
    reseal(invalidPhase);
    if (ajv.validate(schemaByFormat.get(invalidPhase.format), invalidPhase)) {
        throw new Error("Phase report v2 accepted a non-waivable phase");
    }

    const readyWithoutOutput = structuredClone(
        documents.get("branching-narrative/phase-reports/p00_brief.json"),
    );
    readyWithoutOutput.output_evidence = null;
    reseal(readyWithoutOutput);
    if (
        ajv.validate(
            schemaByFormat.get(readyWithoutOutput.format),
            readyWithoutOutput,
        )
    ) {
        throw new Error(
            "Phase report v2 accepted ready without output evidence",
        );
    }

    const mismatchedOutputRole = structuredClone(
        documents.get("branching-narrative/phase-reports/p00_brief.json"),
    );
    mismatchedOutputRole.output_evidence.role = "narrative_architecture";
    reseal(mismatchedOutputRole.output_evidence);
    reseal(mismatchedOutputRole);
    if (
        ajv.validate(
            schemaByFormat.get(mismatchedOutputRole.format),
            mismatchedOutputRole,
        )
    ) {
        throw new Error(
            "Phase output evidence accepted a mismatched phase role",
        );
    }

    const mismatchedOutputSubject = structuredClone(
        documents.get("branching-narrative/phase-reports/p00_brief.json"),
    );
    mismatchedOutputSubject.output_evidence.subject = structuredClone(
        mismatchedOutputSubject.profile,
    );
    reseal(mismatchedOutputSubject.output_evidence);
    reseal(mismatchedOutputSubject);
    if (
        ajv.validate(
            schemaByFormat.get(mismatchedOutputSubject.format),
            mismatchedOutputSubject,
        )
    ) {
        throw new Error("P00 phase output evidence accepted a profile subject");
    }

    const mismatchedOutputPhase = structuredClone(
        documents.get("branching-narrative/phase-reports/p00_brief.json"),
    );
    mismatchedOutputPhase.output_evidence.phase = "p08_world_arcs";
    reseal(mismatchedOutputPhase.output_evidence);
    reseal(mismatchedOutputPhase);
    if (
        ajv.validate(
            schemaByFormat.get(mismatchedOutputPhase.format),
            mismatchedOutputPhase,
        )
    ) {
        throw new Error(
            "Phase report accepted output evidence from another phase",
        );
    }

    const unsupportedFuturePhase = structuredClone(
        documents.get("branching-narrative/phase-reports/p00_brief.json"),
    );
    unsupportedFuturePhase.phase = "p11_art_audio";
    unsupportedFuturePhase.output_evidence.phase = "p11_art_audio";
    unsupportedFuturePhase.output_evidence.role = "asset_inventory";
    reseal(unsupportedFuturePhase.output_evidence);
    reseal(unsupportedFuturePhase);
    if (
        ajv.validate(
            schemaByFormat.get(unsupportedFuturePhase.format),
            unsupportedFuturePhase,
        )
    ) {
        throw new Error("Phase report v2 accepted unsupported P11 readiness");
    }

    const invalidLorepack = structuredClone(
        documents.get(
            "branching-narrative/artifacts/branching-narrative.lorepack.json",
        ),
    );
    invalidLorepack.script = "not allowed";
    if (
        ajv.validate(
            schemaByFormat.get(invalidLorepack.format),
            invalidLorepack,
        )
    ) {
        throw new Error("Lorepack accepted executable script content");
    }

    const credentialLorepack = structuredClone(
        documents.get(
            "branching-narrative/artifacts/branching-narrative.lorepack.json",
        ),
    );
    credentialLorepack.credentials = { token: "not allowed" };
    if (
        ajv.validate(
            schemaByFormat.get(credentialLorepack.format),
            credentialLorepack,
        )
    ) {
        throw new Error("Lorepack accepted pre-bound credentials");
    }

    const runtimeHookLorepack = structuredClone(
        documents.get(
            "branching-narrative/artifacts/branching-narrative.lorepack.json",
        ),
    );
    runtimeHookLorepack.narrative_projections[0].units[0].effect_ids = [
        "runtime_effect",
    ];
    reseal(runtimeHookLorepack.narrative_projections[0]);
    reseal(runtimeHookLorepack);
    if (
        ajv.validate(
            schemaByFormat.get(runtimeHookLorepack.format),
            runtimeHookLorepack,
        )
    ) {
        throw new Error("Lorepack accepted a nested narrative runtime hook");
    }

    const canonModule = structuredClone(
        documents.get("universe-library/source/world/canon.json"),
    );
    const factWithSources = reseal({
        format: "world-forge.lore_world_projection",
        format_version: 1,
        projection_id: canonModule.module_id,
        source: {
            format: canonModule.format,
            format_version: canonModule.format_version,
            id: canonModule.module_id,
            content_hash: canonModule.content_hash,
        },
        module_type: "canon",
        title: canonModule.title,
        records: [
            {
                id: canonModule.facts[0].id,
                statement: canonModule.facts[0].statement,
                status: canonModule.facts[0].status,
                sources: ["mutable_authoring_source"],
            },
        ],
    });
    if (
        ajv.validate(
            `${schemaByFormat.get("world-forge.lorepack")}#/$defs/worldProjection`,
            factWithSources,
        )
    ) {
        throw new Error("Lore world projection accepted raw fact sources");
    }

    const dependencyKindWithSource = structuredClone(
        documents.get(
            "branching-narrative/artifacts/branching-narrative.lorepack.json",
        ),
    );
    dependencyKindWithSource.provenance[0].kind = "dependency_lorepack";
    if (
        ajv.validate(
            schemaByFormat.get(dependencyKindWithSource.format),
            dependencyKindWithSource,
        )
    ) {
        throw new Error(
            "Lorepack accepted dependency provenance with a source subject",
        );
    }

    const sourceKindWithLorepack = structuredClone(
        documents.get(
            "branching-narrative/artifacts/branching-narrative.lorepack.json",
        ),
    );
    sourceKindWithLorepack.provenance[0] = {
        provenance_id: "invalid_source",
        kind: "source_contract",
        subject: {
            format: "world-forge.lorepack",
            format_version: 1,
            id: sourceKindWithLorepack.lorepack_id,
            content_hash: sourceKindWithLorepack.content_hash,
        },
    };
    if (
        ajv.validate(
            schemaByFormat.get(sourceKindWithLorepack.format),
            sourceKindWithLorepack,
        )
    ) {
        throw new Error(
            "Lorepack accepted source provenance with a lorepack subject",
        );
    }

    const runtimeAiGamepack = structuredClone(
        documents.get(
            "abstract-puzzle/artifacts/abstract-puzzle.gamepack.json",
        ),
    );
    runtimeAiGamepack.runtime_ai = true;
    if (
        ajv.validate(
            schemaByFormat.get(runtimeAiGamepack.format),
            runtimeAiGamepack,
        )
    ) {
        throw new Error("Gamepack accepted runtime AI");
    }

    const rawAuthoringGamepack = structuredClone(
        documents.get(
            "abstract-puzzle/artifacts/abstract-puzzle.gamepack.json",
        ),
    );
    rawAuthoringGamepack.modules.activities[0].activities[0].provenance =
        "raw authoring prose";
    if (
        ajv.validate(
            schemaByFormat.get(rawAuthoringGamepack.format),
            rawAuthoringGamepack,
        )
    ) {
        throw new Error("Gamepack accepted raw activity provenance");
    }

    const nestedProviderGamepack = structuredClone(
        documents.get(
            "abstract-puzzle/artifacts/abstract-puzzle.gamepack.json",
        ),
    );
    nestedProviderGamepack.logic.actions[0].provider_credentials = {
        token: "forbidden",
    };
    if (
        ajv.validate(
            schemaByFormat.get(nestedProviderGamepack.format),
            nestedProviderGamepack,
        )
    ) {
        throw new Error("Gamepack accepted nested provider credentials");
    }

    const nullStateGamepack = structuredClone(
        documents.get(
            "abstract-puzzle/artifacts/abstract-puzzle.gamepack.json",
        ),
    );
    nullStateGamepack.logic.state_schema = [null];
    if (
        ajv.validate(
            schemaByFormat.get(nullStateGamepack.format),
            nullStateGamepack,
        )
    ) {
        throw new Error("Gamepack accepted a null state-schema entry");
    }

    const internalBackdoorGamepack = structuredClone(
        documents.get(
            "abstract-puzzle/artifacts/abstract-puzzle.gamepack.json",
        ),
    );
    internalBackdoorGamepack.logic.state_schema.push({
        ...structuredClone(internalBackdoorGamepack.logic.state_schema[0]),
        id: "wf_internal_backdoor",
    });
    internalBackdoorGamepack.logic.initial_state.wf_internal_backdoor = false;
    if (
        ajv.validate(
            schemaByFormat.get(internalBackdoorGamepack.format),
            internalBackdoorGamepack,
        )
    ) {
        throw new Error("Gamepack accepted arbitrary compiler-owned state");
    }

    const sourceOwnedState = structuredClone(
        documents.get("abstract-puzzle/source/logic/puzzle.json"),
    );
    sourceOwnedState.state_variables[0].compiler_owned = false;
    if (
        ajv.validate(
            schemaByFormat.get(sourceOwnedState.format),
            sourceOwnedState,
        )
    ) {
        throw new Error("Source logic accepted a compiler-owned state marker");
    }

    const duplicateCursorGamepack = structuredClone(
        documents.get(
            "branching-narrative/artifacts/branching-narrative.gamepack.json",
        ),
    );
    duplicateCursorGamepack.logic.state_schema.push(
        structuredClone(duplicateCursorGamepack.logic.narrative_cursor),
    );
    if (
        ajv.validate(
            schemaByFormat.get(duplicateCursorGamepack.format),
            duplicateCursorGamepack,
        )
    ) {
        throw new Error("Gamepack accepted duplicate compiler-owned cursors");
    }

    const nonFinalCursorGamepack = structuredClone(
        documents.get(
            "branching-narrative/artifacts/branching-narrative.gamepack.json",
        ),
    );
    nonFinalCursorGamepack.logic.state_schema.reverse();
    if (
        ajv.validate(
            schemaByFormat.get(nonFinalCursorGamepack.format),
            nonFinalCursorGamepack,
        )
    ) {
        throw new Error("Gamepack accepted a non-final compiler-owned cursor");
    }

    const puzzleCursorGamepack = structuredClone(
        documents.get(
            "abstract-puzzle/artifacts/abstract-puzzle.gamepack.json",
        ),
    );
    puzzleCursorGamepack.logic.state_schema.push({
        compiler_owned: true,
        id: "wf_internal_narrative_cursor",
        type: "string",
        initial: "board",
        allowed_values: ["board"],
        mutability: "mutable",
        persistence: "saved",
    });
    if (
        ajv.validate(
            schemaByFormat.get(puzzleCursorGamepack.format),
            puzzleCursorGamepack,
        )
    ) {
        throw new Error(
            "Narrative-free Gamepack accepted a compiler-owned cursor",
        );
    }

    const authoredChoiceProjectionGamepack = structuredClone(
        documents.get(
            "branching-narrative/artifacts/branching-narrative.gamepack.json",
        ),
    );
    authoredChoiceProjectionGamepack.logic.narrative_cursor = null;
    authoredChoiceProjectionGamepack.logic.narrative_transitions = [];
    authoredChoiceProjectionGamepack.logic.state_schema =
        authoredChoiceProjectionGamepack.logic.state_schema.filter(
            (state) => state.compiler_owned !== true,
        );
    delete authoredChoiceProjectionGamepack.logic.initial_state
        .wf_internal_narrative_cursor;
    if (
        ajv.validate(
            schemaByFormat.get(authoredChoiceProjectionGamepack.format),
            authoredChoiceProjectionGamepack,
        )
    ) {
        throw new Error(
            "Gamepack accepted authored choice projection without executable logic",
        );
    }
    for (const unitType of ["scene", "storylet"]) {
        const authoredNarrativeProjectionGamepack = structuredClone(
            authoredChoiceProjectionGamepack,
        );
        const choiceUnit =
            authoredNarrativeProjectionGamepack.modules.narrative[0].units.find(
                (unit) => unit.unit_type === "choice",
            );
        choiceUnit.unit_type = unitType;
        delete choiceUnit.options;
        if (
            !ajv.validate(
                schemaByFormat.get(authoredNarrativeProjectionGamepack.format),
                authoredNarrativeProjectionGamepack,
            )
        ) {
            throw new Error(
                `Gamepack rejected authored ${unitType} projection without executable logic`,
            );
        }
    }

    const narrativeLogicWithoutModulesGamepack = structuredClone(
        documents.get(
            "branching-narrative/artifacts/branching-narrative.gamepack.json",
        ),
    );
    narrativeLogicWithoutModulesGamepack.modules.narrative = [];
    if (
        ajv.validate(
            schemaByFormat.get(narrativeLogicWithoutModulesGamepack.format),
            narrativeLogicWithoutModulesGamepack,
        )
    ) {
        throw new Error(
            "Gamepack accepted narrative logic without narrative modules",
        );
    }

    const mixedWorldProjectionGamepack = structuredClone(
        documents.get(
            "abstract-puzzle/artifacts/abstract-puzzle.gamepack.json",
        ),
    );
    mixedWorldProjectionGamepack.modules.world = [
        {
            source: {
                format: "world-forge.world_module",
                format_version: 1,
                id: "mixed_world_projection",
                content_hash:
                    "1111111111111111111111111111111111111111111111111111111111111111",
            },
            module_type: "canon",
            title: "Mixed world projection",
            records: [
                {
                    id: "wrong_record",
                    sequence: 1,
                    summary: "Chronology record in a canon projection.",
                },
            ],
        },
    ];
    if (
        ajv.validate(
            schemaByFormat.get(mixedWorldProjectionGamepack.format),
            mixedWorldProjectionGamepack,
        )
    ) {
        throw new Error("Gamepack accepted a mixed world projection record");
    }

    const puzzleAnalysis = documents.get(
        "abstract-puzzle/artifacts/abstract-puzzle.game-analysis.json",
    );
    const mismatchedAnalyzerReport = structuredClone(puzzleAnalysis);
    mismatchedAnalyzerReport.analyzer = {
        id: "worldforge.branching_narrative_exhaustive",
        version: 1,
        profile: "branching_narrative",
    };
    if (
        ajv.validate(
            schemaByFormat.get(mismatchedAnalyzerReport.format),
            mismatchedAnalyzerReport,
        )
    ) {
        throw new Error(
            "Game analysis accepted an analyzer from another requirement",
        );
    }

    const unsupportedPuzzleReport = structuredClone(puzzleAnalysis);
    unsupportedPuzzleReport.status = "unsupported";
    unsupportedPuzzleReport.reason_codes = ["analysis_profile_unsupported"];
    if (
        ajv.validate(
            schemaByFormat.get(unsupportedPuzzleReport.format),
            unsupportedPuzzleReport,
        )
    ) {
        throw new Error(
            "Game analysis accepted unsupported status for a puzzle profile",
        );
    }

    const passedAnalysisWithFailedCheck = structuredClone(puzzleAnalysis);
    passedAnalysisWithFailedCheck.checks[0].status = "failed";
    passedAnalysisWithFailedCheck.checks[0].reason_codes = [
        "synthetic_failure",
    ];
    if (
        ajv.validate(
            schemaByFormat.get(passedAnalysisWithFailedCheck.format),
            passedAnalysisWithFailedCheck,
        )
    ) {
        throw new Error("Passed game analysis accepted a failed check");
    }

    const failedCheckWithoutReason = structuredClone(puzzleAnalysis);
    failedCheckWithoutReason.status = "failed";
    failedCheckWithoutReason.reason_codes = ["synthetic_failure"];
    failedCheckWithoutReason.checks[0].status = "failed";
    failedCheckWithoutReason.checks[0].reason_codes = [];
    if (
        ajv.validate(
            schemaByFormat.get(failedCheckWithoutReason.format),
            failedCheckWithoutReason,
        )
    ) {
        throw new Error(
            "Failed game-analysis check accepted an empty reason list",
        );
    }

    const inconclusiveCheckWithoutReason = structuredClone(puzzleAnalysis);
    inconclusiveCheckWithoutReason.status = "inconclusive";
    inconclusiveCheckWithoutReason.reason_codes = ["synthetic_bound"];
    inconclusiveCheckWithoutReason.checks[0].status = "inconclusive";
    inconclusiveCheckWithoutReason.checks[0].reason_codes = [];
    if (
        ajv.validate(
            schemaByFormat.get(inconclusiveCheckWithoutReason.format),
            inconclusiveCheckWithoutReason,
        )
    ) {
        throw new Error(
            "Inconclusive game-analysis check accepted an empty reason list",
        );
    }

    const failedAnalysisWithoutEvidence = structuredClone(puzzleAnalysis);
    failedAnalysisWithoutEvidence.status = "failed";
    failedAnalysisWithoutEvidence.reason_codes = ["synthetic_failure"];
    if (
        ajv.validate(
            schemaByFormat.get(failedAnalysisWithoutEvidence.format),
            failedAnalysisWithoutEvidence,
        )
    ) {
        throw new Error("Failed game analysis accepted no failure evidence");
    }

    const oversizedPreflightMetric = structuredClone(puzzleAnalysis);
    oversizedPreflightMetric.metrics.largest_state_bytes = 65588;
    oversizedPreflightMetric.metrics.frontier_closed = false;
    oversizedPreflightMetric.status = "inconclusive";
    oversizedPreflightMetric.reason_codes = ["state_bytes_bound_reached"];
    oversizedPreflightMetric.checks[0].status = "inconclusive";
    oversizedPreflightMetric.checks[0].reason_codes = [
        "state_bytes_bound_reached",
    ];
    oversizedPreflightMetric.summary.passed -= 1;
    oversizedPreflightMetric.summary.inconclusive += 1;
    if (
        !ajv.validate(
            schemaByFormat.get(oversizedPreflightMetric.format),
            oversizedPreflightMetric,
        )
    ) {
        throw new Error(
            `Game analysis rejected an observed oversized preflight metric: ${ajv.errorsText(
                ajv.errors,
            )}`,
        );
    }

    const dishonestLedger = structuredClone(
        documents.get(
            "abstract-puzzle/artifacts/abstract-puzzle.authoring-ledger.json",
        ),
    );
    dishonestLedger.mechanics[0].status = "supported_current";
    reseal(dishonestLedger);
    if (
        ajv.validate(
            schemaByFormat.get(dishonestLedger.format),
            dishonestLedger,
        )
    ) {
        throw new Error(
            "Capability ledger accepted support without native evidence",
        );
    }

    const mismatchedBlockedLedger = structuredClone(
        documents.get(
            "abstract-puzzle/artifacts/abstract-puzzle.authoring-ledger.json",
        ),
    );
    mismatchedBlockedLedger.features[0].status = "blocked";
    mismatchedBlockedLedger.features[0].reason_code = "adapter_not_evaluated";
    reseal(mismatchedBlockedLedger);
    if (
        ajv.validate(
            schemaByFormat.get(mismatchedBlockedLedger.format),
            mismatchedBlockedLedger,
        )
    ) {
        throw new Error(
            "Capability ledger accepted a mismatched blocked branch",
        );
    }

    const inconsistentAdapterLedger = structuredClone(
        documents.get(
            "abstract-puzzle/artifacts/abstract-puzzle.authoring-ledger.json",
        ),
    );
    inconsistentAdapterLedger.adapter.status = "absent";
    reseal(inconsistentAdapterLedger);
    if (
        ajv.validate(
            schemaByFormat.get(inconsistentAdapterLedger.format),
            inconsistentAdapterLedger,
        )
    ) {
        throw new Error(
            "Capability ledger accepted an inconsistent adapter identity",
        );
    }

    const puzzleAssetSubject = structuredClone(
        documents.get("abstract-puzzle/assets/subject.json"),
    );
    puzzleAssetSubject.subject = {
        kind: "legacy_worldpack",
        format: "isoworld.worldpack",
        format_version: 6,
        id: "legacy_world",
        content_hash: "0".repeat(64),
    };
    reseal(puzzleAssetSubject);
    if (
        ajv.validate(
            schemaByFormat.get(puzzleAssetSubject.format),
            puzzleAssetSubject,
        )
    ) {
        throw new Error(
            "Asset subject accepted an unsupported legacy worldpack version",
        );
    }

    const puzzleAssetTarget = documents.get(
        "abstract-puzzle/assets/target.json",
    );
    for (const [length, accepted] of [
        [1, false],
        [2, true],
        [49, true],
        [56, true],
        [64, true],
        [65, false],
        [100, false],
    ]) {
        const boundaryTarget = structuredClone(puzzleAssetTarget);
        boundaryTarget.target_id = `a${"b".repeat(Math.max(0, length - 1))}`;
        if (
            ajv.validate(
                schemaByFormat.get(boundaryTarget.format),
                boundaryTarget,
            ) !== accepted
        ) {
            throw new Error(
                `Asset target ID length ${length} disagrees with the canonical domain`,
            );
        }
    }
    const targetWithUnexpectedField = {
        ...structuredClone(puzzleAssetTarget),
        unexpected_field: true,
    };
    if (
        ajv.validate(
            schemaByFormat.get(targetWithUnexpectedField.format),
            targetWithUnexpectedField,
        )
    ) {
        throw new Error(
            "Asset target accepted an arbitrary additional property",
        );
    }

    const impossibleAssetTarget = structuredClone(puzzleAssetTarget);
    impossibleAssetTarget.bindings[0].kind = "model_3d";
    impossibleAssetTarget.bindings[0].representation = "3d";
    reseal(impossibleAssetTarget);
    if (
        ajv.validate(
            schemaByFormat.get(impossibleAssetTarget.format),
            impossibleAssetTarget,
        )
    ) {
        throw new Error("Asset target accepted an impossible physical matrix");
    }

    const fakeNotApplicableAudio = structuredClone(
        documents.get("abstract-puzzle/assets/style.json"),
    );
    fakeNotApplicableAudio.audio.music_direction = "Invented music.";
    reseal(fakeNotApplicableAudio);
    if (
        ajv.validate(
            schemaByFormat.get(fakeNotApplicableAudio.format),
            fakeNotApplicableAudio,
        )
    ) {
        throw new Error(
            "Asset style accepted fake not-applicable audio fields",
        );
    }

    const manualAssetInventory = structuredClone(
        documents.get("abstract-puzzle/assets/inventory.json"),
    );
    manualAssetInventory.manual_assets = [];
    reseal(manualAssetInventory);
    if (
        ajv.validate(
            schemaByFormat.get(manualAssetInventory.format),
            manualAssetInventory,
        )
    ) {
        throw new Error("Asset inventory accepted a manual asset collection");
    }

    const mismatchedAssetSpecification = structuredClone(
        documents.get("abstract-puzzle/assets/specs/board_ui.json"),
    );
    mismatchedAssetSpecification.outputs[0].media_type = "font/ttf";
    reseal(mismatchedAssetSpecification);
    if (
        ajv.validate(
            schemaByFormat.get(mismatchedAssetSpecification.format),
            mismatchedAssetSpecification,
        )
    ) {
        throw new Error(
            "Asset specification accepted mismatched media expectations",
        );
    }

    const fontSpecification = documents.get(
        "branching-narrative/assets/specs/narrative_ui_font.json",
    );
    for (const glyphRanges of [
        ["U+-"],
        ["U+007F-0020"],
        ["U+0020-007E", "U+007E-00FF"],
        ["U+00000-0007E"],
        ["U+10FFFF-110000"],
    ]) {
        const invalidGlyphs = structuredClone(fontSpecification);
        invalidGlyphs.outputs[0].expectations.glyph_ranges = glyphRanges;
        if (
            ajv.validate(
                schemaByFormat.get(invalidGlyphs.format),
                invalidGlyphs,
            )
        ) {
            throw new Error(
                `Asset specification accepted invalid glyph ranges ${glyphRanges}`,
            );
        }
    }
    for (const glyphRanges of [
        ["U+0000-0000"],
        ["U+0020-007E", "U+10000-10FFFF"],
        ["U+10FFFF-10FFFF"],
    ]) {
        const validGlyphs = structuredClone(fontSpecification);
        validGlyphs.outputs[0].expectations.glyph_ranges = glyphRanges;
        if (
            !ajv.validate(schemaByFormat.get(validGlyphs.format), validGlyphs)
        ) {
            throw new Error(
                `Asset specification rejected valid glyph ranges ${glyphRanges}: ${ajv.errorsText(
                    ajv.errors,
                )}`,
            );
        }
    }
}

async function synchronize(output, contents, staleMessage) {
    let current = "";
    try {
        current = await readFile(output, "utf8");
    } catch (error) {
        if (error?.code !== "ENOENT") {
            throw error;
        }
    }

    if (checkOnly) {
        if (current !== contents) {
            console.error(staleMessage);
            process.exitCode = 1;
        }
    } else if (current !== contents) {
        await mkdir(path.dirname(output), { recursive: true });
        await writeFile(output, contents, "utf8");
    }
}

await synchronize(
    outputPath,
    generated,
    "Generated Studio protocol types are stale. Run npm run generate:types.",
);
await synchronize(
    protocolV2OutputPath,
    generatedProtocolV2,
    "Generated Studio protocol v2 types are stale. Run npm run generate:types.",
);
await synchronize(
    protocolV3OutputPath,
    generatedProtocolV3,
    "Generated Studio protocol v3 types are stale. Run npm run generate:types.",
);
await synchronize(
    protocolV4OutputPath,
    generatedProtocolV4,
    "Generated Studio protocol v4 types are stale. Run npm run generate:types.",
);
await synchronize(
    protocolV5OutputPath,
    generatedProtocolV5,
    "Generated Studio protocol v5 types are stale. Run npm run generate:types.",
);
await synchronize(
    creationContentModesOutputPath,
    generatedCreationContentModes,
    "Generated creation content mode constants are stale. Run npm run generate:types.",
);
await synchronize(
    contractsOutputPath,
    generatedContracts,
    "Generated World Forge contract types are stale. Run npm run generate:types.",
);
await synchronize(
    contractsConformancePath,
    generatedContractsConformance,
    "Generated World Forge contract type probes are stale. Run npm run generate:types.",
);

/* AUTO-GENERATED negative type probes for world-forge.* contracts. */
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
  title: "/tmp/authoring/project.json",
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

const completeAssetLicenseRecord = {
  "asset": {
    "asset_id": "board_ui",
    "content_hash": "c60401da521cff7fa3d6e08ac3c309f1a71d4b6b73b5e9c09d1985e90768ffe9"
  },
  "asset_subject": {
    "content_hash": "09742eff54989a4cba753af413b320dbfabad30f874be38c6cd646f952f6b4c7",
    "format": "world-forge.asset_subject",
    "format_version": 1,
    "id": "asset_subject_eaa36e78dc8661deddec17555aa8c2bb8d15e22e70d14656"
  },
  "candidate": {
    "candidate_artifact_id": "board_ui_candidate",
    "media_type": "image/png",
    "role": "texture",
    "sha256": "69801bb77d5a0ddd63b59700fb567ad003bceb23e303488167d2da14ecd56d8b"
  },
  "component_licenses": [
    {
      "component_id": "world_forge_fixture_generator",
      "evidence_hash": "9999999999999999999999999999999999999999999999999999999999999999",
      "identifier": "MIT",
      "scope": "generator_tool"
    }
  ],
  "content_hash": "2bc857cca2a376da2e55273bc188e8620d5e34a9711d724b8574ff22e4eddafe",
  "copyright": {
    "holder": "World Forge contributors",
    "year": null,
    "year_policy": "not_applicable"
  },
  "evidence_hashes": [
    "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  ],
  "format": "world-forge.asset_license_record",
  "format_version": 1,
  "gamepack": {
    "content_hash": "0510d69d0f78d3e80810aa26dd4b76752416809f7733e731274ac8d7f35dac09",
    "format": "world-forge.gamepack",
    "format_version": 1,
    "id": "abstract_puzzle"
  },
  "inventory": {
    "content_hash": "ef6496fd27a91fe4aa19c57390b09accd0a748eff1c6bdbc280b2dfc0302335c",
    "format": "world-forge.asset_inventory",
    "format_version": 1,
    "id": "asset_inventory_db1e30fe4a098bf505b52989066137a1f34f4123b5ece765"
  },
  "license_basis": {
    "identifier": "CC0-1.0",
    "kind": "spdx"
  },
  "license_record_id": "board_ui_license",
  "obligations": {
    "attribution_required": false,
    "notice_required": true,
    "source_offer_required": false
  },
  "permissions": {
    "commercial_use": true,
    "modification": true,
    "redistribution": true
  },
  "provenance": {
    "content_hash": "4b48f9cc1341d5da110f3d4045db416d93685ea0752fa798512847ffc02cec5d",
    "format": "world-forge.asset_provenance_record",
    "format_version": 1,
    "id": "board_ui_provenance"
  },
  "receipt": {
    "content_hash": "e485f5d380e8fda4d15a5aeb2f198b3bb55b7133704e0991a528cce1e2aa450f",
    "format": "world-forge.asset_production_receipt",
    "format_version": 1,
    "id": "board_ui_receipt"
  },
  "request": {
    "content_hash": "00ae4bce2f6e68aefad25d05e1320f2ab4403849bf8bc15c2724426f0d5547d2",
    "format": "world-forge.asset_production_request",
    "format_version": 1,
    "id": "board_ui_production"
  },
  "runtime_notice": {
    "sha256": "e136d1dd589d4b24e66adbf15287a9412d4845395b5845f1a07b1642594fdd4f",
    "text": "Puzzle board fixture is dedicated to the public domain under CC0-1.0."
  },
  "selection": {
    "content_hash": "a3751275ae5261293f93be875bc20be6f8f70c45cf0cb395f036431f09aea0d8",
    "format": "world-forge.asset_selection",
    "format_version": 1,
    "id": "board_ui_selection"
  },
  "specification": {
    "content_hash": "046c7d15b317c290f2c3487a8e50853bc7fc961e1d815273acc086a06af1473a",
    "format": "world-forge.asset_spec",
    "format_version": 1,
    "id": "asset_spec_327fa7c7303d4f06a0f86589cc971d3d4c3ed8d53ff5f8b2"
  },
  "style": {
    "content_hash": "8c6bc7d3654b7679921de0b9db2524abefbd5f83ca0f7f6ec45f306272fba563",
    "format": "world-forge.asset_style",
    "format_version": 1,
    "id": "asset_style_1ed627dcb9cc101212f8c5b69dd218524a29610aaa8188cc"
  },
  "target": {
    "content_hash": "609fa138f16bc4bb402624a7fa3082b5b377e112f7f98633f2d9c0f3971a9d33",
    "format": "world-forge.asset_target",
    "format_version": 1,
    "id": "asset_target_226e1b8d904d61fc8c631fef1a188b32300712752f903f02"
  }
} as const satisfies
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

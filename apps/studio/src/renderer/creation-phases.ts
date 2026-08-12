import {
  parseCreationJson,
  parseCreationObjectJson,
  snapshotCreationJson,
} from "./creation-state";

export const CREATION_PHASE_CATALOG = [
  { id: "p00_brief", short: "P00", title: "Brief, audience, constraints, and non-goals" },
  { id: "p01_genre_style", short: "P01", title: "Experience classification and player promise" },
  { id: "p02_world_laws", short: "P02", title: "Interaction grammar, ontology, rules, and goals" },
  { id: "p03_geography", short: "P03", title: "World presence, topology, and environments" },
  { id: "p04_timeline", short: "P04", title: "History, progression chronology, and time" },
  { id: "p05_societies", short: "P05", title: "Societies, teams, factions, and institutions" },
  { id: "p06_characters", short: "P06", title: "Player representation, actors, and personal arcs" },
  { id: "p07_systems", short: "P07", title: "Core loops, systems, progression, and interaction matrix" },
  { id: "p08_world_arcs", short: "P08", title: "Narrative architecture or explicit no-narrative design" },
  { id: "p09_narrative_content", short: "P09", title: "Typed content architecture" },
  { id: "p10_canon_lock", short: "P10", title: "Playability, continuity, solvability, and content lock" },
  { id: "p11_art_audio", short: "P11", title: "Presentation, visual, and audio direction" },
  { id: "p12_asset_specs", short: "P12", title: "Asset inventory, specification, production policy, and QA" },
  { id: "p13_asset_production", short: "P13", title: "Runtime compatibility and implementation support" },
  { id: "p14_handoff", short: "P14", title: "Reviewed implementation handoff" },
] as const;

export type CreationPhaseId = (typeof CREATION_PHASE_CATALOG)[number]["id"];
export type CreationPhaseState = "ready" | "not_applicable" | "current" | "invalidated" | "locked";

export interface CreationPhaseSummary {
  id: CreationPhaseId;
  short: string;
  title: string;
  state: CreationPhaseState;
  contentHash: string | null;
  invalidationReason: string | null;
}

export interface CreationPhaseAuthorityFingerprint {
  expectedRootGeneration: number;
  expectedSourceRevision: string;
  expectedWorkflowStatusHash: string;
}

export function parseCreationPhaseReportJson(
  source: string,
  expectedPhase: CreationPhaseId,
): Record<string, unknown> {
  const report = parseCreationObjectJson(source, "phase report");
  if (
    report.format !== "world-forge.phase_report" ||
    report.format_version !== 3 ||
    report.phase !== expectedPhase ||
    (report.status !== "ready" && report.status !== "not_applicable")
  ) {
    throw new Error("Phase report format, phase, or reviewed status is invalid");
  }
  return report;
}

export function parseCreationArtifactRegistryJson(source: string): Record<string, unknown>[] {
  const value = parseCreationJson(source, "artifact registry");
  if (!Array.isArray(value) || value.length > 1024 || !value.every(isRecord)) {
    throw new Error("Artifact registry JSON must be an array of at most 1024 objects");
  }
  return snapshotCreationJson(value) as Record<string, unknown>[];
}

export function summarizeCreationPhaseStates(value: unknown): CreationPhaseSummary[] {
  const status = isRecord(value) ? value : {};
  const completed = new Set(
    Array.isArray(status.completed_phases)
      ? status.completed_phases.filter(isPhaseId)
      : [],
  );
  const current = isPhaseId(status.current_phase) ? status.current_phase : null;
  const references = Array.isArray(status.reports) ? status.reports.filter(isRecord) : [];
  const invalidated = Array.isArray(status.invalidated_reports)
    ? status.invalidated_reports.filter(isRecord)
    : [];
  return CREATION_PHASE_CATALOG.map((phase) => {
    const reference = references.find((candidate) => candidate.phase === phase.id);
    const invalidation = [...invalidated].reverse().find((candidate) => candidate.phase === phase.id);
    let state: CreationPhaseState = "locked";
    if (completed.has(phase.id) && reference?.status === "ready") state = "ready";
    else if (completed.has(phase.id) && reference?.status === "not_applicable") state = "not_applicable";
    else if (phase.id === current) state = "current";
    else if (invalidation) state = "invalidated";
    return {
      ...phase,
      state,
      contentHash: typeof reference?.content_hash === "string" ? reference.content_hash : null,
      invalidationReason: typeof invalidation?.reason === "string" ? invalidation.reason : null,
    };
  });
}

export function creationPhaseValidationFingerprint(
  authority: CreationPhaseAuthorityFingerprint,
  report: Record<string, unknown>,
  artifactRegistry: Record<string, unknown>[],
): string {
  return JSON.stringify(sortJson({
    authority,
    report: snapshotCreationJson(report),
    artifact_registry: snapshotCreationJson(artifactRegistry),
  }));
}

export function creationPhaseReportPreview(value: unknown): string {
  const snapshot = snapshotCreationJson(value);
  if (!isRecord(snapshot)) throw new Error("Phase report must have an object root");
  return `${JSON.stringify(sortJson(snapshot), null, 2)}\n`;
}

export function creationArtifactRegistryPreview(value: unknown): string {
  const snapshot = snapshotCreationJson(value);
  if (!Array.isArray(snapshot)) throw new Error("Artifact registry must be an array");
  return `${JSON.stringify(sortJson(snapshot), null, 2)}\n`;
}

function isPhaseId(value: unknown): value is CreationPhaseId {
  return typeof value === "string" && CREATION_PHASE_CATALOG.some((phase) => phase.id === value);
}

function sortJson(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(sortJson);
  if (!isRecord(value)) return value;
  const result: Record<string, unknown> = {};
  for (const key of Object.keys(value).sort()) result[key] = sortJson(value[key]);
  return result;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

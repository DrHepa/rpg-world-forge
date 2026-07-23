import type {
  StudioClientResult,
  StudioReplyEnvelope,
  StudioWorldAnalyzeResult,
  StudioWorldValidateResult,
} from "../shared/studio-api";
import { boundedMessage } from "./authoring-state";

export type LegacyListMethod = "workspace.list" | "events.list" | "changeset.list" | "job.list";

export interface DecodedList {
  records: Record<string, unknown>[];
  error: string | null;
}

export interface FindingView {
  id: string;
  severity: "error" | "warning" | "info";
  code: string;
  path: string;
  message: string;
}

export interface DockRow {
  id: string;
  title: string;
  meta: string;
  detail: string;
  state: string | null;
  progress: number | null;
}

export function decodeLegacyList(
  result: StudioClientResult<StudioReplyEnvelope>,
  method: LegacyListMethod,
  field: "workspaces" | "events" | "changesets" | "jobs",
  limit: number,
): DecodedList {
  if (!result.ok) return { records: [], error: boundedMessage(result.error.message) };
  const envelope = result.value;
  if (envelope.kind === "error") {
    return { records: [], error: boundedMessage(envelope.error.message) };
  }
  if (envelope.kind !== "response" || envelope.method !== method || !isRecord(envelope.result)) {
    return { records: [], error: `Forge Studio returned an invalid ${method} response.` };
  }
  const raw = envelope.result[field];
  if (!Array.isArray(raw) || !raw.every(isRecord)) {
    return { records: [], error: `Forge Studio returned an invalid ${field} list.` };
  }
  return { records: raw.slice(0, limit), error: null };
}

export function workspaceIds(records: readonly Record<string, unknown>[]): string[] {
  return records
    .map((record) => stringField(record, "workspace_id", 64))
    .filter((value): value is string => value !== null)
    .slice(0, 100);
}

export function boundedFindings(
  validation: StudioWorldValidateResult["validation"] | null,
  analysis: StudioWorldAnalyzeResult["analysis"] | null,
  limit = 64,
): { findings: FindingView[]; truncated: boolean } {
  const all: FindingView[] = [];
  for (const [index, diagnostic] of (validation?.diagnostics ?? []).entries()) {
    all.push({
      id: `validation-${String(index)}`,
      severity: "error",
      code: boundedMessage(diagnostic.code, 80),
      path: boundedMessage(diagnostic.path, 160),
      message: boundedMessage(diagnostic.message, 320),
    });
  }
  for (const [index, finding] of (analysis?.findings ?? []).entries()) {
    all.push({
      id: `analysis-${String(index)}`,
      severity: finding.severity,
      code: boundedMessage(finding.code, 80),
      path: boundedMessage(finding.path, 160),
      message: boundedMessage(finding.message, 320),
    });
  }
  return {
    findings: all.slice(0, limit),
    truncated:
      all.length > limit ||
      Boolean(validation?.diagnostics_truncated) ||
      (analysis?.findings.length ?? 0) > limit,
  };
}

export function eventRows(records: readonly Record<string, unknown>[], limit = 40): DockRow[] {
  return records.slice(-limit).reverse().map((record, index) => ({
    id: stringField(record, "event_id", 80) ?? `event-${String(index)}`,
    title: stringField(record, "topic", 100) ?? "Studio activity",
    meta: stringField(record, "created_at", 80) ?? "",
    detail: [stringField(record, "entity_type", 60), stringField(record, "entity_id", 128)]
      .filter(Boolean)
      .join(" · "),
    state: null,
    progress: eventProgress(record),
  }));
}

export function changesetRows(records: readonly Record<string, unknown>[], limit = 40): DockRow[] {
  return records.slice(0, limit).map((record, index) => ({
    id: stringField(record, "changeset_id", 128) ?? `changeset-${String(index)}`,
    title: stringField(record, "changeset_id", 128) ?? "Changeset",
    meta: stringField(record, "updated_at", 80) ?? stringField(record, "created_at", 80) ?? "",
    detail: `${String(arrayField(record, "operations").length)} source operations`,
    state: stringField(record, "status", 32),
    progress: null,
  }));
}

export function jobRows(
  records: readonly Record<string, unknown>[],
  events: readonly Record<string, unknown>[],
  limit = 40,
): DockRow[] {
  const progress = jobProgressById(events);
  return records.slice(0, limit).map((record, index) => {
    const jobId = stringField(record, "job_id", 128) ?? `job-${String(index)}`;
    return {
      id: jobId,
      title: stringField(record, "operation", 96) ?? "Legacy job",
      meta: stringField(record, "updated_at", 80) ?? stringField(record, "created_at", 80) ?? "",
      detail: jobId,
      state: stringField(record, "state", 32),
      progress: progress.get(jobId) ?? null,
    };
  });
}

function jobProgressById(events: readonly Record<string, unknown>[]): Map<string, number> {
  const result = new Map<string, number>();
  for (const event of events) {
    if (event.topic !== "job.progress") continue;
    const jobId = stringField(event, "entity_id", 128);
    const payload = event.payload;
    if (!jobId || !isRecord(payload)) continue;
    const progress = payload.progress;
    if (typeof progress === "number" && Number.isSafeInteger(progress) && progress >= 0 && progress <= 100) {
      result.set(jobId, progress);
    }
  }
  return result;
}

function eventProgress(record: Record<string, unknown>): number | null {
  if (record.topic !== "job.progress" || !isRecord(record.payload)) return null;
  const value = record.payload.progress;
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0 && value <= 100
    ? value
    : null;
}

function stringField(
  record: Record<string, unknown>,
  field: string,
  limit: number,
): string | null {
  const value = record[field];
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  return typeof value === "string" ? boundedMessage(value, limit) : null;
}

function arrayField(record: Record<string, unknown>, field: string): unknown[] {
  const value = record[field];
  return Array.isArray(value) ? value.slice(0, 1_024) : [];
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

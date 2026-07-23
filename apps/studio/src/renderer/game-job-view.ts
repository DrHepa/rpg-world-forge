import type {
  StudioAssetpackVerifyInput,
  StudioRuntimeHeadlessInput,
  StudioRuntimeReplayInput,
} from "../shared/studio-api";

export type GameOperation = "assetpack.verify" | "runtime.headless" | "runtime.replay";
export type GameJobState =
  | "queued"
  | "running"
  | "awaiting_approval"
  | "awaiting_user"
  | "paused"
  | "succeeded"
  | "failed"
  | "canceled"
  | "orphaned";

export type GameJobInput =
  | ({ operation: "assetpack.verify" } & StudioAssetpackVerifyInput)
  | ({ operation: "runtime.headless" } & StudioRuntimeHeadlessInput)
  | ({ operation: "runtime.replay" } & StudioRuntimeReplayInput);

export interface GameJobFact {
  label: string;
  value: string;
  kind: "text" | "number" | "hash" | "path";
}

export interface GameJobErrorView {
  code: string;
  message: string;
}

export interface GameJobView {
  jobId: string;
  operation: GameOperation;
  operationLabel: string;
  state: GameJobState;
  stateLabel: string;
  createdAt: string;
  updatedAt: string;
  input: GameJobInput;
  inputFacts: GameJobFact[];
  resultFacts: GameJobFact[] | null;
  error: GameJobErrorView | null;
  progress: number | null;
  canCancel: boolean;
}

export type GameJobRequest =
  | { operation: "assetpack.verify"; input: StudioAssetpackVerifyInput }
  | { operation: "runtime.headless"; input: StudioRuntimeHeadlessInput }
  | { operation: "runtime.replay"; input: StudioRuntimeReplayInput };

const MAX_GAME_JOBS = 40;
const GAME_JOB_KEYS = [
  "created_at",
  "error",
  "format",
  "format_version",
  "input",
  "job_id",
  "operation",
  "result",
  "state",
  "updated_at",
  "workspace_id",
] as const;
const JOB_ID_PATTERN = /^[a-z0-9][a-z0-9_-]{0,127}$/u;
const TIMESTAMP_PATTERN =
  /^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z$/u;
const SHA256_PATTERN = /^[0-9a-f]{64}$/u;
const WINDOWS_DEVICE_NAMES = new Set([
  "aux",
  "com1",
  "com2",
  "com3",
  "com4",
  "com5",
  "com6",
  "com7",
  "com8",
  "com9",
  "con",
  "lpt1",
  "lpt2",
  "lpt3",
  "lpt4",
  "lpt5",
  "lpt6",
  "lpt7",
  "lpt8",
  "lpt9",
  "nul",
  "prn",
]);
const OPERATION_LABELS: Record<GameOperation, string> = {
  "assetpack.verify": "Assetpack verification",
  "runtime.headless": "Headless simulation",
  "runtime.replay": "Replay verification",
};
const STATE_LABELS: Record<GameJobState, string> = {
  queued: "Queued",
  running: "Running",
  awaiting_approval: "Awaiting approval",
  awaiting_user: "Awaiting user",
  paused: "Paused",
  succeeded: "Succeeded",
  failed: "Failed",
  canceled: "Canceled",
  orphaned: "Orphaned",
};
const ERROR_MESSAGES = {
  execution_failed: "The fixed offline worker could not complete the operation.",
  invalid_workspace: "A selected workspace input was unavailable or changed.",
  timeout: "The fixed offline worker reached its execution time limit.",
  worker_crashed: "The fixed offline worker stopped unexpectedly.",
  worker_protocol: "The fixed offline worker returned an invalid response.",
} as const;

export function projectGameJobs(
  records: readonly Record<string, unknown>[],
  events: readonly Record<string, unknown>[],
  workspaceId: string | null,
  limit = MAX_GAME_JOBS,
): GameJobView[] {
  if (!workspaceId || !Number.isSafeInteger(limit) || limit < 1) return [];
  const decoded: Omit<GameJobView, "progress">[] = [];
  const seen = new Set<string>();
  for (const record of records) {
    const job = decodeGameJob(record, workspaceId);
    if (!job || seen.has(job.jobId)) continue;
    decoded.push(job);
    seen.add(job.jobId);
    if (decoded.length >= Math.min(limit, MAX_GAME_JOBS)) break;
  }
  const progress = associatedProgress(events, workspaceId, seen);
  return decoded.map((job) => ({
    ...job,
    progress: job.state === "running" ? (progress.get(job.jobId) ?? null) : null,
  }));
}

export function projectCreatedGameJob(
  record: unknown,
  workspaceId: string,
  request: GameJobRequest,
): GameJobView | null {
  if (!isRecord(record)) return null;
  const projected = projectGameJobs([record], [], workspaceId, 1)[0] ?? null;
  if (
    !projected ||
    projected.state !== "queued" ||
    !gameJobMatchesRequest(projected, request)
  ) {
    return null;
  }
  return projected;
}

export function projectCanceledGameJob(
  record: unknown,
  workspaceId: string,
  jobId: string,
  operation: GameOperation,
): GameJobView | null {
  if (!isRecord(record)) return null;
  const projected = projectGameJobs([record], [], workspaceId, 1)[0] ?? null;
  return projected?.jobId === jobId && projected.operation === operation
    ? projected
    : null;
}

export function gameJobMatchesRequest(
  job: GameJobView,
  request: GameJobRequest,
): boolean {
  if (job.operation !== request.operation) return false;
  if (request.operation === "assetpack.verify") {
    return (
      job.input.operation === request.operation &&
      job.input.assetpack === request.input.assetpack &&
      job.input.worldpack === request.input.worldpack
    );
  }
  if (request.operation === "runtime.headless") {
    return (
      job.input.operation === request.operation &&
      job.input.worldpack === request.input.worldpack &&
      job.input.ticks === request.input.ticks
    );
  }
  return (
    job.input.operation === request.operation &&
    job.input.worldpack === request.input.worldpack &&
    job.input.replay === request.input.replay
  );
}

export function isPortableGamePath(value: string): boolean {
  if (
    value.length < 1 ||
    value.length > 4_096 ||
    value !== value.normalize("NFC") ||
    value.startsWith("/") ||
    value.includes("\\")
  ) {
    return false;
  }
  const components = value.split("/");
  if (components.length > 16) return false;
  const encoder = new TextEncoder();
  return components.every((component) => {
    if (
      component.length < 1 ||
      component === "." ||
      component === ".." ||
      component.endsWith(".") ||
      component.endsWith(" ") ||
      hasControlCharacters(component) ||
      /[<>:"|?*]/u.test(component) ||
      encoder.encode(component).byteLength > 255
    ) {
      return false;
    }
    return !WINDOWS_DEVICE_NAMES.has(component.split(".", 1)[0]?.toLocaleLowerCase("en-US"));
  });
}

function decodeGameJob(
  record: Record<string, unknown>,
  workspaceId: string,
): Omit<GameJobView, "progress"> | null {
  if (
    !hasExactKeys(record, GAME_JOB_KEYS) ||
    record.format !== "rpg-world-forge.studio_job" ||
    record.format_version !== 2 ||
    record.workspace_id !== workspaceId ||
    !isJobId(record.job_id) ||
    !isGameOperation(record.operation) ||
    !isGameJobState(record.state) ||
    !isTimestamp(record.created_at) ||
    !isTimestamp(record.updated_at)
  ) {
    return null;
  }
  const input = decodeInput(record.operation, record.input);
  if (!input) return null;
  let resultFacts: GameJobFact[] | null = null;
  let error: GameJobErrorView | null = null;
  if (record.state === "succeeded") {
    if (record.result === null || record.error !== null) return null;
    resultFacts = decodeResult(record.operation, record.result);
    if (resultFacts === null) return null;
  } else if (record.state === "failed") {
    if (record.result !== null || record.error === null) return null;
    error = decodeError(record.error);
    if (error === null) return null;
  } else if (record.result !== null || record.error !== null) {
    return null;
  }
  return {
    jobId: record.job_id,
    operation: record.operation,
    operationLabel: OPERATION_LABELS[record.operation],
    state: record.state,
    stateLabel: STATE_LABELS[record.state],
    createdAt: record.created_at,
    updatedAt: record.updated_at,
    input,
    inputFacts: inputFacts(input),
    resultFacts,
    error,
    canCancel: record.state === "queued" || record.state === "running",
  };
}

function decodeInput(operation: GameOperation, value: unknown): GameJobInput | null {
  if (!isRecord(value)) return null;
  if (operation === "assetpack.verify") {
    if (
      !hasExactKeys(value, ["assetpack", "worldpack"]) ||
      !isPortablePathField(value.assetpack) ||
      !isPortablePathField(value.worldpack)
    ) {
      return null;
    }
    return { operation, assetpack: value.assetpack, worldpack: value.worldpack };
  }
  if (operation === "runtime.headless") {
    if (
      !hasExactKeys(value, ["ticks", "worldpack"]) ||
      !isPortablePathField(value.worldpack) ||
      !isBoundedInteger(value.ticks, 1_000_000)
    ) {
      return null;
    }
    return { operation, worldpack: value.worldpack, ticks: value.ticks };
  }
  if (
    !hasExactKeys(value, ["replay", "worldpack"]) ||
    !isPortablePathField(value.worldpack) ||
    !isPortablePathField(value.replay)
  ) {
    return null;
  }
  return { operation, worldpack: value.worldpack, replay: value.replay };
}

function decodeResult(
  operation: GameOperation,
  value: unknown,
): GameJobFact[] | null {
  if (value === null) return null;
  if (!isRecord(value)) return null;
  if (operation === "assetpack.verify") {
    const keys = [
      "asset_count",
      "binding_count",
      "content_hash",
      "file_count",
      "operation",
      "target_hash",
      "target_id",
      "valid",
      "world_content_hash",
      "world_id",
    ];
    if (
      !hasExactKeys(value, keys) ||
      value.operation !== operation ||
      value.valid !== true ||
      !isSafeIdentifier(value.world_id) ||
      !isSafeIdentifier(value.target_id) ||
      !isSha256(value.world_content_hash) ||
      !isSha256(value.target_hash) ||
      !isSha256(value.content_hash) ||
      !isBoundedInteger(value.asset_count) ||
      !isBoundedInteger(value.file_count) ||
      !isBoundedInteger(value.binding_count)
    ) {
      return null;
    }
    return [
      fact("Verification", "Verified", "text"),
      fact("World ID", value.world_id, "text"),
      fact("World content hash", value.world_content_hash, "hash"),
      fact("Target ID", value.target_id, "text"),
      fact("Target hash", value.target_hash, "hash"),
      fact("Assetpack content hash", value.content_hash, "hash"),
      fact("Assets", String(value.asset_count), "number"),
      fact("Files", String(value.file_count), "number"),
      fact("Bindings", String(value.binding_count), "number"),
    ];
  }
  if (operation === "runtime.headless") {
    const keys = [
      "absolute_minute",
      "operation",
      "state_digest",
      "state_tick",
      "ticks",
      "world_content_hash",
      "world_id",
    ];
    if (
      !hasExactKeys(value, keys) ||
      value.operation !== operation ||
      !isSafeIdentifier(value.world_id) ||
      !isSha256(value.world_content_hash) ||
      !isBoundedInteger(value.ticks, 1_000_000) ||
      !isBoundedInteger(value.state_tick) ||
      !isBoundedInteger(value.absolute_minute) ||
      !isSha256(value.state_digest)
    ) {
      return null;
    }
    return [
      fact("World ID", value.world_id, "text"),
      fact("Requested ticks", String(value.ticks), "number"),
      fact("State tick", String(value.state_tick), "number"),
      fact("Absolute minute", String(value.absolute_minute), "number"),
      fact("World content hash", value.world_content_hash, "hash"),
      fact("State digest", value.state_digest, "hash"),
    ];
  }
  const keys = [
    "absolute_minute",
    "action_count",
    "operation",
    "state_digest",
    "state_tick",
    "world_content_hash",
    "world_id",
  ];
  if (
    !hasExactKeys(value, keys) ||
    value.operation !== operation ||
    !isSafeIdentifier(value.world_id) ||
    !isSha256(value.world_content_hash) ||
    !isBoundedInteger(value.action_count, 1_000_000) ||
    !isBoundedInteger(value.state_tick) ||
    !isBoundedInteger(value.absolute_minute) ||
    !isSha256(value.state_digest)
  ) {
    return null;
  }
  return [
    fact("World ID", value.world_id, "text"),
    fact("Replay actions", String(value.action_count), "number"),
    fact("State tick", String(value.state_tick), "number"),
    fact("Absolute minute", String(value.absolute_minute), "number"),
    fact("World content hash", value.world_content_hash, "hash"),
    fact("State digest", value.state_digest, "hash"),
  ];
}

function inputFacts(input: GameJobInput): GameJobFact[] {
  if (input.operation === "assetpack.verify") {
    return [
      fact("Assetpack", input.assetpack, "path"),
      fact("Worldpack", input.worldpack, "path"),
    ];
  }
  if (input.operation === "runtime.headless") {
    return [
      fact("Worldpack", input.worldpack, "path"),
      fact("Ticks", String(input.ticks), "number"),
    ];
  }
  return [
    fact("Worldpack", input.worldpack, "path"),
    fact("Replay", input.replay, "path"),
  ];
}

function decodeError(value: unknown): GameJobErrorView | null {
  if (value === null) return null;
  if (
    !isRecord(value) ||
    !hasExactKeys(value, ["code", "message"]) ||
    typeof value.code !== "string" ||
    typeof value.message !== "string" ||
    value.message.length < 1 ||
    !Object.hasOwn(ERROR_MESSAGES, value.code)
  ) {
    return null;
  }
  const code = value.code as keyof typeof ERROR_MESSAGES;
  return { code, message: ERROR_MESSAGES[code] };
}

function associatedProgress(
  events: readonly Record<string, unknown>[],
  workspaceId: string,
  jobIds: ReadonlySet<string>,
): Map<string, number> {
  const latest = new Map<string, { eventId: number; progress: number }>();
  const unreliable = new Set<string>();
  for (const event of events) {
    if (
      event.topic !== "job.progress" ||
      event.workspace_id !== workspaceId ||
      event.entity_type !== "job" ||
      !isJobId(event.entity_id) ||
      !jobIds.has(event.entity_id)
    ) {
      continue;
    }
    const jobId = event.entity_id;
    if (
      !Number.isSafeInteger(event.event_id) ||
      (event.event_id as number) < 1 ||
      !isRecord(event.payload) ||
      !hasExactKeys(event.payload, ["progress", "stage"]) ||
      !isBoundedInteger(event.payload.progress, 100) ||
      typeof event.payload.stage !== "string" ||
      event.payload.stage.length < 1 ||
      event.payload.stage.length > 64
    ) {
      unreliable.add(jobId);
      continue;
    }
    const candidate = {
      eventId: event.event_id as number,
      progress: event.payload.progress,
    };
    const prior = latest.get(jobId);
    if (prior?.eventId === candidate.eventId && prior.progress !== candidate.progress) {
      unreliable.add(jobId);
      continue;
    }
    if (!prior || candidate.eventId > prior.eventId) latest.set(jobId, candidate);
  }
  const result = new Map<string, number>();
  for (const [jobId, observation] of latest) {
    if (!unreliable.has(jobId)) result.set(jobId, observation.progress);
  }
  return result;
}

function fact(label: string, value: string, kind: GameJobFact["kind"]): GameJobFact {
  return { label, value, kind };
}

function isPortablePathField(value: unknown): value is string {
  return typeof value === "string" && isPortableGamePath(value);
}

function isJobId(value: unknown): value is string {
  return typeof value === "string" && JOB_ID_PATTERN.test(value);
}

function isTimestamp(value: unknown): value is string {
  return (
    typeof value === "string" &&
    TIMESTAMP_PATTERN.test(value) &&
    Number.isFinite(Date.parse(value))
  );
}

function isSha256(value: unknown): value is string {
  return typeof value === "string" && SHA256_PATTERN.test(value);
}

function isSafeIdentifier(value: unknown): value is string {
  return (
    typeof value === "string" &&
    value.length >= 1 &&
    value.length <= 128 &&
    value === value.normalize("NFC") &&
    !hasControlCharacters(value) &&
    !/[/\\]/u.test(value)
  );
}

function isBoundedInteger(value: unknown, maximum = Number.MAX_SAFE_INTEGER): value is number {
  return (
    typeof value === "number" &&
    Number.isSafeInteger(value) &&
    value >= 0 &&
    value <= maximum
  );
}

function isGameOperation(value: unknown): value is GameOperation {
  return (
    value === "assetpack.verify" ||
    value === "runtime.headless" ||
    value === "runtime.replay"
  );
}

function isGameJobState(value: unknown): value is GameJobState {
  return (
    value === "queued" ||
    value === "running" ||
    value === "awaiting_approval" ||
    value === "awaiting_user" ||
    value === "paused" ||
    value === "succeeded" ||
    value === "failed" ||
    value === "canceled" ||
    value === "orphaned"
  );
}

function hasExactKeys(record: Record<string, unknown>, expected: readonly string[]): boolean {
  const actual = Object.keys(record).sort();
  return (
    actual.length === expected.length &&
    expected
      .toSorted()
      .every((key, index) => actual[index] === key)
  );
}

function hasControlCharacters(value: string): boolean {
  return [...value].some((character) => (character.codePointAt(0) ?? 0) < 32);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

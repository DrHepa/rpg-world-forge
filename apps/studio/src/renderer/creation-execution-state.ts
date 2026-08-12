import type {
    ForgeStudioApi,
    StudioCreationArtifact,
    StudioCreationEvidence,
    StudioCreationJob,
    StudioCreationJobV12,
    StudioCreationJobState,
    StudioCreationWorkspace,
} from "../shared/studio-api";
import { expectCreationEvidenceResult } from "./creation-service";

const MAX_ARTIFACT_PAGES = 64;
const MAX_JOB_PAGES = 64;
const ARTIFACT_PAGE_SIZE = 64;
export const CREATION_JOB_PAGE_SIZE = 8;

const JOB_STATES = new Set<StudioCreationJobState>([
    "queued",
    "running",
    "succeeded",
    "failed",
    "canceled",
    "orphaned",
]);
const JOB_OPERATIONS = new Set([
    "artifact.admit",
    "asset.process",
    "asset.release.seal",
    "creation.compile",
    "runtime.compose",
    "runtime.bundle.build",
    "game.materialization.bundle.build",
    "game.materialize",
    "game.package",
    "game.package.extract",
    "asset.qa.review",
    "asset.release.authorize",
    "runtime.headless.verify",
]);
const JOB_PROGRESS = new Set([
    "queued",
    "reserved",
    "worker_started",
    "output_published",
    "registry_committing",
    "committed",
    "cleanup_pending",
    "failed",
    "canceled",
    "orphaned",
]);
const ANALYSIS_STATUSES = new Set([
    "passed",
    "failed",
    "inconclusive",
    "unsupported",
    "not_applicable",
]);

const RESULT_BASE_KEYS = [
    "analysis_status",
    "artifact_snapshot_hash",
    "cleanup_pending",
    "output_artifact_ids",
    "reason_codes",
] as const;
const RESULT_V10_KEYS = [
    ...RESULT_BASE_KEYS,
    "review_receipt",
    "review_status",
] as const;
const RESULT_V11_KEYS = [
    ...RESULT_BASE_KEYS,
    "asset_manifest",
    "asset_release_authority",
    "assetpack",
    "publication",
    "release_status",
] as const;
const RESULT_V12_KEYS = [
    ...RESULT_BASE_KEYS,
    "native_status",
    "publication",
    "release_status",
    "runtime_evidence",
    "runtime_support_authority",
    "runtime_support_report",
    "supported",
] as const;

export interface CreationExecutionAuthority {
    workspaceId: string;
    rootGeneration: number;
    sourceRevision: string;
    workflowStatusHash: string | null;
    artifactSnapshotHash: string;
}

export interface CreationExecutionCensus {
    authority: CreationExecutionAuthority;
    evidence: StudioCreationEvidence;
    activeArtifacts: StudioCreationArtifact[];
    candidateArtifacts: StudioCreationArtifact[];
    selectableArtifacts: StudioCreationArtifact[];
    selectableById: ReadonlyMap<string, StudioCreationArtifact>;
}

export interface CreationJobView {
    job_id: string;
    workspace_id: string;
    operation: StudioCreationJobV12["operation"];
    state: StudioCreationJobState;
    generation: number;
    authority: CreationExecutionAuthority;
    progress: StudioCreationJob["progress"];
    analysisStatus: string | null;
    cleanupPending: boolean;
    recoveryRequired: boolean;
    error: StudioCreationJob["error"];
    createdAt: string;
    updatedAt: string;
    recordHash: string;
    record: StudioCreationJobV12;
    authorityOutcome: CreationAuthorityOutcome | null;
}

export type CreationAuthorityOutcome =
    | {
          kind: "asset_qa_review";
          status: "approved" | "rejected" | "blocked";
          reviewReceiptArtifactIds: string[];
          blocked: boolean;
      }
    | {
          kind: "asset_release_authority";
          status: "authorized" | "blocked";
          blocked: boolean;
      }
    | {
          kind: "runtime_headless_authority";
          headlessVerified: boolean;
          nativeUnavailable: boolean;
          releaseBlocked: boolean;
          blocked: boolean;
      };

export type CreationAuthorityJobProjection = CreationJobView & {
    authorityOutcome: CreationAuthorityOutcome;
};

export type CreationJobAction =
    "cancel" | "retry" | "resume" | "rollback" | "cleanup";
export interface CreationJobPage {
    jobs: CreationJobView[];
    nextSequence: number | null;
}

export function creationExecutionAuthorityKey(
    authority: CreationExecutionAuthority,
): string {
    return [
        authority.workspaceId,
        String(authority.rootGeneration),
        authority.sourceRevision,
        authority.workflowStatusHash ?? "",
        authority.artifactSnapshotHash,
    ].join("\u0000");
}

export function sameCreationExecutionAuthority(
    left: CreationExecutionAuthority,
    right: CreationExecutionAuthority,
): boolean {
    return (
        creationExecutionAuthorityKey(left) ===
        creationExecutionAuthorityKey(right)
    );
}

export async function loadCreationExecutionCensus(
    api: ForgeStudioApi,
    workspace: StudioCreationWorkspace,
    expectedArtifactSnapshotHash: string | null,
): Promise<CreationExecutionCensus> {
    const baseAuthority = evidenceAuthority(workspace);
    const evidenceResult = await expectCreationEvidenceResult(
        api.inspectCreationEvidence({
            ...baseAuthority,
            expectedArtifactSnapshotHash,
        }),
        "creation_evidence.inspect",
    );
    const evidence = validateEvidence(evidenceResult, workspace);
    const authority: CreationExecutionAuthority = {
        workspaceId: workspace.workspace_id,
        rootGeneration: workspace.root_generation,
        sourceRevision: workspace.source_revision,
        workflowStatusHash: workspace.workflow_status_hash,
        artifactSnapshotHash: evidence.artifact_snapshot_hash,
    };
    const seen = new Set<string>();
    const activeArtifacts = await loadArtifactLifecycle(
        api,
        workspace,
        authority,
        evidence,
        "active",
        seen,
    );
    const candidateArtifacts = await loadArtifactLifecycle(
        api,
        workspace,
        authority,
        evidence,
        "candidate",
        seen,
    );
    const selectableArtifacts = [...activeArtifacts, ...candidateArtifacts];
    return {
        authority,
        evidence,
        activeArtifacts,
        candidateArtifacts,
        selectableArtifacts,
        selectableById: new Map(
            selectableArtifacts.map((artifact) => [
                artifact.artifact_id,
                artifact,
            ]),
        ),
    };
}

export async function loadCreationExecutionCensusAfterJob(
    api: ForgeStudioApi,
    workspace: StudioCreationWorkspace,
    resultSnapshotHash: string | null,
): Promise<CreationExecutionCensus> {
    if (resultSnapshotHash === null)
        return await loadCreationExecutionCensus(api, workspace, null);
    try {
        return await loadCreationExecutionCensus(
            api,
            workspace,
            resultSnapshotHash,
        );
    } catch (error) {
        if (!isSnapshotConflict(error)) throw error;
        return await loadCreationExecutionCensus(api, workspace, null);
    }
}

export function projectCreationJob(
    value: unknown,
    workspaceId: string,
    expectedAuthority?: CreationExecutionAuthority,
): CreationJobView | null {
    if (
        !isRecord(value) ||
        value.format !== "world-forge.studio_creation_job" ||
        !Number.isSafeInteger(value.format_version) ||
        !operationMatchesVersion(Number(value.format_version), value.operation) ||
        typeof value.job_id !== "string" ||
        value.workspace_id !== workspaceId ||
        typeof value.operation !== "string" ||
        !JOB_OPERATIONS.has(value.operation) ||
        typeof value.state !== "string" ||
        !JOB_STATES.has(value.state as StudioCreationJobState) ||
        !Number.isSafeInteger(value.generation) ||
        Number(value.generation) < 0 ||
        typeof value.progress !== "string" ||
        !JOB_PROGRESS.has(value.progress) ||
        !Array.isArray(value.inputs) ||
        typeof value.created_at !== "string" ||
        typeof value.updated_at !== "string" ||
        typeof value.record_hash !== "string" ||
        !isRecord(value.authority)
    )
        return null;
    const authority = normalizeJobAuthority(value.authority, workspaceId);
    if (
        authority === null ||
        (expectedAuthority &&
            !sameCreationExecutionAuthority(authority, expectedAuthority))
    ) {
        return null;
    }
    const record = value as unknown as StudioCreationJobV12;
    const result = value.result;
    let analysisStatus: string | null = null;
    let cleanupPending = value.progress === "cleanup_pending";
    if (result !== null) {
        if (
            !isRecord(result) ||
            typeof result.analysis_status !== "string" ||
            !ANALYSIS_STATUSES.has(result.analysis_status) ||
            typeof result.cleanup_pending !== "boolean" ||
            typeof result.artifact_snapshot_hash !== "string" ||
            !Array.isArray(result.reason_codes) ||
            !Array.isArray(result.output_artifact_ids)
        )
            return null;
        analysisStatus = result.analysis_status;
        cleanupPending = cleanupPending || result.cleanup_pending;
    }
    const authorityOutcome = projectAuthorityOutcome(
        Number(value.format_version),
        record.operation,
        result,
    );
    if (
        (record.operation === "asset.qa.review" ||
            record.operation === "asset.release.authorize" ||
            record.operation === "runtime.headless.verify") &&
        result !== null &&
        authorityOutcome === null
    ) {
        return null;
    }
    if (value.error !== null && !isJobError(value.error)) return null;
    const error = record.error;
    return {
        job_id: value.job_id,
        workspace_id: workspaceId,
        operation: record.operation,
        state: value.state as StudioCreationJobState,
        generation: Number(value.generation),
        authority,
        progress: record.progress,
        analysisStatus,
        cleanupPending,
        recoveryRequired:
            value.state === "orphaned" || error?.code === "recovery_required",
        error,
        createdAt: value.created_at,
        updatedAt: value.updated_at,
        recordHash: value.record_hash,
        record,
        authorityOutcome,
    };
}

function operationMatchesVersion(version: number, operation: unknown): boolean {
    switch (version) {
        case 1:
            return operation === "artifact.admit" || operation === "creation.compile";
        case 2:
            return operation === "asset.process";
        case 3:
            return operation === "asset.release.seal";
        case 4:
            return operation === "runtime.compose";
        case 5:
            return operation === "runtime.bundle.build";
        case 6:
            return operation === "game.materialization.bundle.build";
        case 7:
            return operation === "game.materialize";
        case 8:
            return operation === "game.package";
        case 9:
            return operation === "game.package.extract";
        case 10:
            return operation === "asset.qa.review";
        case 11:
            return operation === "asset.release.authorize";
        case 12:
            return operation === "runtime.headless.verify";
        default:
            return false;
    }
}

function projectAuthorityOutcome(
    version: number,
    operation: StudioCreationJobV12["operation"],
    result: unknown,
): CreationAuthorityOutcome | null {
    if (result === null) return null;
    if (!isRecord(result)) return null;
    if (version === 10 && operation === "asset.qa.review") {
        if (!hasExactKeys(result, RESULT_V10_KEYS)) return null;
        const receipt = isRecord(result.review_receipt) &&
            hasExactKeys(result.review_receipt, ["content_hash", "format", "format_version", "review_receipt_id"])
            ? result.review_receipt : null;
        const reviewStatus = result.review_status;
        if (
            (reviewStatus !== "approved" && reviewStatus !== "rejected") ||
            !receipt ||
            receipt.format !== "world-forge.asset_qa_review_receipt" ||
            receipt.format_version !== 1 ||
            typeof receipt.review_receipt_id !== "string" ||
            typeof receipt.content_hash !== "string"
        ) {
            return null;
        }
        const blocked = result.analysis_status !== "passed" || result.cleanup_pending === true;
        return {
            kind: "asset_qa_review",
            status: blocked ? "blocked" : reviewStatus,
            reviewReceiptArtifactIds: [receipt.review_receipt_id],
            blocked,
        };
    }
    if (version === 11 && operation === "asset.release.authorize") {
        if (!hasExactKeys(result, RESULT_V11_KEYS)) return null;
        if (
            !Array.isArray(result.output_artifact_ids) ||
            result.output_artifact_ids.length !== 3 ||
            new Set(result.output_artifact_ids).size !== 3 ||
            (result.release_status !== "authorized" &&
                result.release_status !== "blocked") ||
            !Array.isArray(result.reason_codes) ||
            (result.release_status === "authorized" &&
                (result.analysis_status !== "passed" ||
                    result.reason_codes.length !== 0 ||
                    !isRecord(result.publication))) ||
            (result.release_status === "blocked" &&
                (result.analysis_status !== "failed" ||
                    result.reason_codes.length < 1 ||
                    result.publication !== null)) ||
            !isRecord(result.asset_manifest) ||
            !hasExactKeys(result.asset_manifest, ["content_hash", "manifest_id"]) ||
            typeof result.asset_manifest.manifest_id !== "string" ||
            typeof result.asset_manifest.content_hash !== "string" ||
            !isRecord(result.assetpack) ||
            !hasExactKeys(result.assetpack, ["assetpack_id", "content_hash"]) ||
            typeof result.assetpack.assetpack_id !== "string" ||
            typeof result.assetpack.content_hash !== "string" ||
            !isRecord(result.asset_release_authority) ||
            !hasExactKeys(result.asset_release_authority, ["content_hash", "format", "format_version", "release_authority_id"]) ||
            result.asset_release_authority.format !== "world-forge.asset_release_authority" ||
            result.asset_release_authority.format_version !== 1 ||
            typeof result.asset_release_authority.release_authority_id !== "string" ||
            typeof result.asset_release_authority.content_hash !== "string" ||
            (result.publication !== null && !isAssetpackPublication(result.publication))
        ) {
            return null;
        }
        return {
            kind: "asset_release_authority",
            status: result.release_status,
            blocked: result.release_status === "blocked" || result.cleanup_pending === true,
        };
    }
    if (version === 12 && operation === "runtime.headless.verify") {
        if (!hasExactKeys(result, RESULT_V12_KEYS)) return null;
        if (
            !Array.isArray(result.output_artifact_ids) ||
            result.output_artifact_ids.length !== 3 ||
            new Set(result.output_artifact_ids).size !== 3 ||
            result.analysis_status !== "passed" ||
            result.cleanup_pending !== false ||
            result.release_status !== "blocked" ||
            result.native_status !== "unavailable" ||
            result.supported !== false ||
            !isHeadlessPublication(result.publication) ||
            !isAuthorityIdentity(result.runtime_support_authority, "world-forge.runtime_support_authority") ||
            !isAuthorityIdentity(result.runtime_evidence, "world-forge.runtime_evidence") ||
            !isAuthorityIdentity(result.runtime_support_report, "world-forge.runtime_support_report")
        ) {
            return null;
        }
        return {
            kind: "runtime_headless_authority",
            headlessVerified: true,
            nativeUnavailable: true,
            releaseBlocked: true,
            blocked: true,
        };
    }
    return null;
}

function isAssetpackPublication(value: unknown): boolean {
    if (!isRecord(value) || !hasExactKeys(value, ["assetpack", "grant_generation", "grant_id", "kind", "state"])) return false;
    const assetpack = value.assetpack;
    return (
        value.kind === "generic_assetpack_directory" &&
        value.state === "published" &&
        typeof value.grant_id === "string" &&
        Number.isSafeInteger(value.grant_generation) &&
        isRecord(assetpack) &&
        hasExactKeys(assetpack, ["content_hash", "format", "format_version", "id", "inventory_hash"]) &&
        assetpack.format === "world-forge.assetpack" &&
        assetpack.format_version === 1 &&
        typeof assetpack.id === "string" &&
        typeof assetpack.content_hash === "string" &&
        typeof assetpack.inventory_hash === "string"
    );
}

function isHeadlessPublication(value: unknown): boolean {
    if (!isRecord(value) || !hasExactKeys(value, ["grant_generation", "grant_id", "headless_evidence_set", "kind", "state"])) return false;
    const evidence = value.headless_evidence_set;
    return (
        value.kind === "headless_evidence_directory" &&
        value.state === "published" &&
        typeof value.grant_id === "string" &&
        Number.isSafeInteger(value.grant_generation) &&
        isRecord(evidence) &&
        hasExactKeys(evidence, ["content_hash", "format", "format_version", "id", "tree_hash"]) &&
        evidence.format === "world-forge.headless_evidence_set" &&
        evidence.format_version === 1 &&
        typeof evidence.id === "string" &&
        typeof evidence.content_hash === "string" &&
        typeof evidence.tree_hash === "string"
    );
}

function isAuthorityIdentity(value: unknown, format: string): boolean {
    return (
        isRecord(value) &&
        hasExactKeys(value, ["content_hash", "format", "format_version", "id"]) &&
        value.format === format &&
        value.format_version === 1 &&
        typeof value.id === "string" &&
        typeof value.content_hash === "string"
    );
}

function hasExactKeys(value: Record<string, unknown>, keys: readonly string[]): boolean {
    const actual = Object.keys(value).sort();
    const expected = [...keys].sort();
    return actual.length === expected.length && actual.every((key, index) => key === expected[index]);
}

export function recoveryActionsForCreationJob(
    value: unknown,
    authority: CreationExecutionAuthority,
): CreationJobAction[] {
    const job = projectCreationJob(value, authority.workspaceId);
    if (job === null) return [];
    const hasCurrentAuthority = sameCreationExecutionAuthority(
        job.authority,
        authority,
    );
    if (job.state === "queued" || job.state === "running") return ["cancel"];
    if (job.state === "orphaned")
        return hasCurrentAuthority ? ["resume", "rollback"] : ["rollback"];
    if (job.state === "succeeded" && job.cleanupPending) return ["cleanup"];
    if (
        hasCurrentAuthority &&
        job.operation === "creation.compile" &&
        (job.state === "canceled" ||
            (job.state === "failed" && job.error?.retryable === true))
    )
        return ["retry"];
    return [];
}

export async function listCreationJobPage(
    api: ForgeStudioApi,
    workspaceId: string,
    state: StudioCreationJobState | null,
    afterSequence: number,
): Promise<CreationJobPage> {
    const result = await expectCreationEvidenceResult(
        api.listCreationJobs({
            workspaceId,
            state,
            afterSequence,
            limit: CREATION_JOB_PAGE_SIZE,
        }),
        "creation_job.list",
    );
    if (
        !Array.isArray(result.jobs) ||
        result.jobs.length > CREATION_JOB_PAGE_SIZE ||
        (result.next_sequence !== null &&
            (!Number.isSafeInteger(result.next_sequence) ||
                Number(result.next_sequence) <= afterSequence))
    )
        throw new Error("Forge Studio returned an invalid creation job page");
    const seen = new Set<string>();
    const jobs = result.jobs.map((record) => {
        const job = projectCreationJob(record, workspaceId);
        if (
            job === null ||
            seen.has(job.job_id) ||
            (state !== null && job.state !== state)
        ) {
            throw new Error(
                "Forge Studio returned invalid creation job activity",
            );
        }
        seen.add(job.job_id);
        return job;
    });
    return {
        jobs,
        nextSequence:
            result.next_sequence === null ? null : Number(result.next_sequence),
    };
}

export async function findPendingCompileJob(
    api: ForgeStudioApi,
    authority: CreationExecutionAuthority,
): Promise<CreationJobView | null> {
    for (const state of ["queued", "running"] as const) {
        let afterSequence = 0;
        const cursors = new Set<number>();
        for (let pageIndex = 0; pageIndex < MAX_JOB_PAGES; pageIndex += 1) {
            const page = await listCreationJobPage(
                api,
                authority.workspaceId,
                state,
                afterSequence,
            );
            for (const listed of page.jobs) {
                const exact = projectCreationJob(
                    listed.record,
                    authority.workspaceId,
                    authority,
                );
                if (exact?.operation === "creation.compile") return exact;
            }
            if (page.nextSequence === null) break;
            if (cursors.has(page.nextSequence))
                throw new Error(
                    "Forge Studio returned a cyclic creation job cursor",
                );
            cursors.add(page.nextSequence);
            afterSequence = page.nextSequence;
            if (pageIndex === MAX_JOB_PAGES - 1) {
                throw new Error(
                    "Forge Studio pending creation jobs exceed the bounded page limit",
                );
            }
        }
    }
    return null;
}

export function creationCompileParams(authority: CreationExecutionAuthority) {
    return {
        workspaceId: authority.workspaceId,
        expectedRootGeneration: authority.rootGeneration,
        expectedSourceRevision: authority.sourceRevision,
        expectedWorkflowStatusHash: authority.workflowStatusHash,
        expectedArtifactSnapshotHash: authority.artifactSnapshotHash,
    };
}

export function creationJobResultSnapshot(job: CreationJobView): string | null {
    return job.record.result === null
        ? null
        : job.record.result.artifact_snapshot_hash;
}

async function loadArtifactLifecycle(
    api: ForgeStudioApi,
    workspace: StudioCreationWorkspace,
    authority: CreationExecutionAuthority,
    evidence: StudioCreationEvidence,
    lifecycle: "active" | "candidate",
    seenAcrossLifecycles: Set<string>,
): Promise<StudioCreationArtifact[]> {
    const artifacts: StudioCreationArtifact[] = [];
    const cursors = new Set<string>();
    let cursor: string | null = null;
    for (let pageIndex = 0; pageIndex < MAX_ARTIFACT_PAGES; pageIndex += 1) {
        const result = await expectCreationEvidenceResult(
            api.listCreationArtifacts({
                ...evidenceAuthority(workspace),
                expectedArtifactSnapshotHash: authority.artifactSnapshotHash,
                lifecycle,
                cursor,
                limit: ARTIFACT_PAGE_SIZE,
            }),
            "creation_artifact.list",
        );
        if (
            !matchesPublicAuthority(result.authority, workspace) ||
            result.artifact_snapshot_hash !== authority.artifactSnapshotHash ||
            !Array.isArray(result.artifacts) ||
            result.artifacts.length > ARTIFACT_PAGE_SIZE ||
            !countsEqual(result.counts, evidence.artifact_counts) ||
            (result.next_cursor !== null &&
                typeof result.next_cursor !== "string")
        )
            throw new Error(
                `Forge Studio returned mismatched ${lifecycle} artifact authority`,
            );
        for (const value of result.artifacts) {
            const artifact = validateArtifact(value, workspace, lifecycle);
            if (seenAcrossLifecycles.has(artifact.artifact_id)) {
                throw new Error(
                    "Forge Studio returned duplicate creation artifact evidence across active and candidate",
                );
            }
            seenAcrossLifecycles.add(artifact.artifact_id);
            artifacts.push(artifact);
        }
        if (result.next_cursor === null) {
            if (artifacts.length !== evidence.artifact_counts[lifecycle]) {
                throw new Error(
                    `Forge Studio returned an incomplete ${lifecycle} artifact closure`,
                );
            }
            return artifacts;
        }
        if (result.next_cursor === cursor || cursors.has(result.next_cursor)) {
            throw new Error(
                `Forge Studio returned a cyclic ${lifecycle} artifact cursor`,
            );
        }
        cursors.add(result.next_cursor);
        cursor = result.next_cursor;
    }
    throw new Error(
        `Forge Studio ${lifecycle} artifact closure exceeds the bounded page limit`,
    );
}

function validateEvidence(
    result: Record<string, unknown>,
    workspace: StudioCreationWorkspace,
): StudioCreationEvidence {
    if (
        !matchesPublicAuthority(result.authority, workspace) ||
        typeof result.artifact_snapshot_hash !== "string" ||
        !isRecord(result.evidence) ||
        result.evidence.format !== "world-forge.studio_creation_evidence" ||
        result.evidence.format_version !== 1 ||
        result.evidence.artifact_snapshot_hash !==
            result.artifact_snapshot_hash ||
        !matchesPublicAuthority(result.evidence.authority, workspace) ||
        !validArtifactCounts(result.evidence.artifact_counts)
    )
        throw new Error(
            "Forge Studio returned mismatched creation evidence authority",
        );
    return result.evidence as unknown as StudioCreationEvidence;
}

function validateArtifact(
    value: unknown,
    workspace: StudioCreationWorkspace,
    lifecycle: "active" | "candidate",
): StudioCreationArtifact {
    if (
        !isRecord(value) ||
        value.format !== "world-forge.studio_creation_artifact" ||
        value.format_version !== 1 ||
        typeof value.artifact_id !== "string" ||
        value.lifecycle !== lifecycle ||
        !Array.isArray(value.roles) ||
        value.roles.length < 1 ||
        !isRecord(value.subject) ||
        !isRecord(value.producer) ||
        !isRecord(value.references) ||
        !matchesPublicAuthority(value.authority, workspace) ||
        typeof value.record_hash !== "string"
    )
        throw new Error(
            `Forge Studio returned invalid ${lifecycle} creation artifact evidence`,
        );
    return value as unknown as StudioCreationArtifact;
}

function normalizeJobAuthority(
    value: Record<string, unknown>,
    workspaceId: string,
): CreationExecutionAuthority | null {
    if (
        !Number.isSafeInteger(value.root_generation) ||
        Number(value.root_generation) < 0 ||
        typeof value.source_revision !== "string" ||
        (value.workflow_status_hash !== null &&
            typeof value.workflow_status_hash !== "string") ||
        typeof value.artifact_snapshot_hash !== "string"
    )
        return null;
    return {
        workspaceId,
        rootGeneration: Number(value.root_generation),
        sourceRevision: value.source_revision,
        workflowStatusHash: value.workflow_status_hash,
        artifactSnapshotHash: value.artifact_snapshot_hash,
    };
}

function isJobError(
    value: unknown,
): value is NonNullable<StudioCreationJob["error"]> {
    return (
        isRecord(value) &&
        typeof value.code === "string" &&
        typeof value.message === "string" &&
        typeof value.retryable === "boolean"
    );
}

function evidenceAuthority(workspace: StudioCreationWorkspace) {
    return {
        workspaceId: workspace.workspace_id,
        expectedRootGeneration: workspace.root_generation,
        expectedSourceRevision: workspace.source_revision,
        expectedWorkflowStatusHash: workspace.workflow_status_hash,
    };
}

function matchesPublicAuthority(
    value: unknown,
    workspace: StudioCreationWorkspace,
): boolean {
    return (
        isRecord(value) &&
        value.workspace_id === workspace.workspace_id &&
        value.root_generation === workspace.root_generation &&
        value.source_revision === workspace.source_revision &&
        value.workflow_status_hash === workspace.workflow_status_hash
    );
}

function validArtifactCounts(
    value: unknown,
): value is StudioCreationEvidence["artifact_counts"] {
    return (
        isRecord(value) &&
        ["active", "invalidated", "historical", "candidate", "ignored"].every(
            (key) =>
                Number.isSafeInteger(value[key]) && Number(value[key]) >= 0,
        )
    );
}

function countsEqual(
    left: unknown,
    right: StudioCreationEvidence["artifact_counts"],
): boolean {
    return (
        validArtifactCounts(left) &&
        left.active === right.active &&
        left.invalidated === right.invalidated &&
        left.historical === right.historical &&
        left.candidate === right.candidate &&
        left.ignored === right.ignored
    );
}

function isSnapshotConflict(error: unknown): boolean {
    return (
        error instanceof Error &&
        "code" in error &&
        (error as Error & { code?: unknown }).code === "conflict"
    );
}

function isRecord(value: unknown): value is Record<string, unknown> {
    return typeof value === "object" && value !== null && !Array.isArray(value);
}

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type { ForgeStudioApi, StudioCreationJob } from "../shared/studio-api";
import { expectCreationEvidenceResult } from "./creation-service";
import {
    creationExecutionAuthorityKey,
    listCreationJobPage,
    projectCreationJob,
    recoveryActionsForCreationJob,
    sameCreationExecutionAuthority,
    type CreationExecutionAuthority,
    type CreationJobAction,
    type CreationJobView,
} from "./creation-execution-state";

export interface CreationJobActivityProps {
    api: ForgeStudioApi;
    workspaceId: string;
    authority: CreationExecutionAuthority | null;
    applicable: boolean;
    observedJob: unknown;
    focusJobId: string | null;
    refreshToken: number;
    onObservedJobChange: (job: StudioCreationJob) => void;
    onRetryCompile: (job: CreationJobView) => void | Promise<void>;
}

const STATE_LABELS: Record<CreationJobView["state"], string> = {
    queued: "Queued",
    running: "Running",
    succeeded: "Succeeded",
    failed: "Failed",
    canceled: "Canceled",
    orphaned: "Orphaned",
};
const OPERATION_LABELS: Record<CreationJobView["operation"], string> = {
    "artifact.admit": "Artifact admission",
    "asset.process": "Asset processing",
    "asset.qa.review": "Asset QA authority review",
    "asset.release.authorize": "Asset release authorization",
    "asset.release.seal": "Asset release seal",
    "creation.compile": "Creation compile",
    "runtime.compose": "Runtime composition",
    "runtime.bundle.build": "Runtime bundle build",
    "runtime.headless.verify": "Headless runtime verification",
    "game.materialization.bundle.build": "Materialization bundle build",
    "game.materialize": "Game materialization",
    "game.package": "Game package",
    "game.package.extract": "Game package extraction",
};

export function CreationJobActivity({
    api,
    workspaceId,
    authority,
    applicable,
    observedJob,
    focusJobId,
    refreshToken,
    onObservedJobChange,
    onRetryCompile,
}: CreationJobActivityProps) {
    const [jobs, setJobs] = useState<CreationJobView[]>([]);
    const [nextSequence, setNextSequence] = useState<number | null>(null);
    const [pending, setPending] = useState(false);
    const [mutationJobId, setMutationJobId] = useState<string | null>(null);
    const [status, setStatus] = useState("Job activity is not loaded.");
    const [error, setError] = useState<string | null>(null);
    const requestToken = useRef(0);
    const alertRef = useRef<HTMLParagraphElement | null>(null);
    const authorityKey = authority
        ? creationExecutionAuthorityKey(authority)
        : null;
    const projectedObserved = useMemo(
        () => projectCreationJob(observedJob, workspaceId),
        [observedJob, workspaceId],
    );

    const loadFirstPage = useCallback(async (): Promise<void> => {
        if (!applicable || authority === null) return;
        const token = requestToken.current + 1;
        requestToken.current = token;
        setPending(true);
        setError(null);
        setStatus("Loading creation jobs in creation order.");
        try {
            const page = await listCreationJobPage(api, workspaceId, null, 0);
            if (requestToken.current !== token) return;
            setJobs(page.jobs);
            setNextSequence(page.nextSequence);
            setStatus(
                page.jobs.length === 0
                    ? "No creation jobs recorded."
                    : "Creation job activity loaded.",
            );
        } catch (caught) {
            if (requestToken.current !== token) return;
            setJobs([]);
            setNextSequence(null);
            setError(describeError(caught));
            setStatus("Creation job activity failed to load.");
        } finally {
            if (requestToken.current === token) setPending(false);
        }
    }, [api, applicable, authority, workspaceId]);

    useEffect(() => {
        requestToken.current += 1;
        if (!applicable || authorityKey === null) return;
        queueMicrotask(() => void loadFirstPage());
        return () => {
            requestToken.current += 1;
        };
    }, [applicable, authorityKey, loadFirstPage, refreshToken]);

    useEffect(() => {
        if (!error) return;
        queueMicrotask(() => alertRef.current?.focus());
    }, [error]);

    useEffect(() => {
        if (!focusJobId) return;
        document.getElementById(jobCardId(focusJobId))?.focus();
    }, [focusJobId, jobs, projectedObserved]);

    async function loadNextPage(): Promise<void> {
        if (pending || nextSequence === null || authority === null) return;
        const cursor = nextSequence;
        const token = requestToken.current + 1;
        requestToken.current = token;
        setPending(true);
        setError(null);
        setStatus("Loading the next jobs in creation order.");
        try {
            const page = await listCreationJobPage(
                api,
                workspaceId,
                null,
                cursor,
            );
            if (requestToken.current !== token) return;
            const merged = mergeJobPages(jobs, page.jobs);
            setJobs(merged);
            setNextSequence(page.nextSequence);
            setStatus("Next creation job page loaded.");
        } catch (caught) {
            if (requestToken.current !== token) return;
            setError(describeError(caught));
            setStatus("The next creation job page failed to load.");
        } finally {
            if (requestToken.current === token) setPending(false);
        }
    }

    async function runAction(
        job: CreationJobView,
        action: CreationJobAction,
    ): Promise<void> {
        if (
            authority === null ||
            !recoveryActionsForCreationJob(job.record, authority).includes(
                action,
            )
        ) {
            setError(
                "Creation job authority changed before the requested action",
            );
            setStatus(
                "Creation job action was rejected because its authority is stale.",
            );
            return;
        }
        setMutationJobId(job.job_id);
        setError(null);
        setStatus(`${actionStatus(action)} ${job.job_id}.`);
        try {
            if (action === "retry") {
                await onRetryCompile(job);
                setStatus(`Retry requested for ${job.job_id}.`);
                return;
            }
            const promise =
                action === "cancel"
                    ? api.cancelCreationJob({
                          jobId: job.job_id,
                          expectedGeneration: job.generation,
                          expectedRecordHash: job.recordHash,
                      })
                    : api.recoverCreationJob({
                          jobId: job.job_id,
                          expectedGeneration: job.generation,
                          expectedRecordHash: job.recordHash,
                          mode: action,
                      });
            const method =
                action === "cancel"
                    ? "creation_job.cancel"
                    : "creation_job.recover";
            const result = await expectCreationEvidenceResult(promise, method);
            const updated = projectCreationJob(
                result.job,
                workspaceId,
                job.authority,
            );
            if (
                updated === null ||
                updated.job_id !== job.job_id ||
                updated.operation !== job.operation
            ) {
                throw new Error(
                    "Forge Studio returned a mismatched creation job mutation",
                );
            }
            setJobs((current) => replaceJob(current, updated));
            onObservedJobChange(updated.record);
            setStatus(
                `${updated.job_id} is now ${STATE_LABELS[updated.state].toLocaleLowerCase("en-US")}.`,
            );
        } catch (caught) {
            setError(describeError(caught));
            setStatus(`Creation job action failed for ${job.job_id}.`);
        } finally {
            setMutationJobId(null);
        }
    }

    if (!applicable) {
        return (
            <section
                className="creation-card creation-job-activity"
                aria-labelledby="creation-job-activity-heading"
            >
                <h3 id="creation-job-activity-heading">
                    Creation job activity
                </h3>
                <p>Game execution is not applicable to this project kind.</p>
                <p role="status" aria-live="polite">
                    Execution controls were not loaded.
                </p>
            </section>
        );
    }

    const observedIsListed =
        projectedObserved !== null &&
        jobs.some((job) => job.job_id === projectedObserved.job_id);
    return (
        <section
            className="creation-card creation-job-activity"
            aria-labelledby="creation-job-activity-heading"
            aria-busy={pending || mutationJobId !== null}
        >
            <p className="eyebrow">Bounded durable execution</p>
            <h3 id="creation-job-activity-heading">Creation job activity</h3>
            <p>
                <strong>Jobs in creation order</strong>
            </p>
            <p>
                Pages are oldest-first and contain no more than eight records.
            </p>
            <p role="status" aria-live="polite">
                {status}
            </p>
            {error ? (
                <p
                    ref={alertRef}
                    tabIndex={-1}
                    role="alert"
                    className="inline-error"
                >
                    {error}
                </p>
            ) : null}

            {projectedObserved && !observedIsListed ? (
                <div className="creation-job-observed">
                    <h4>Current submitted job</h4>
                    <CreationJobCard
                        job={projectedObserved}
                        authority={authority}
                        pending={mutationJobId === projectedObserved.job_id}
                        onAction={runAction}
                    />
                </div>
            ) : null}

            <div
                className="creation-job-page"
                aria-label="Jobs in creation order"
            >
                {jobs.length === 0 && !pending ? (
                    <p>No creation jobs are recorded for this workspace.</p>
                ) : null}
                {jobs.map((listed) => {
                    const job =
                        projectedObserved?.job_id === listed.job_id &&
                        projectedObserved.generation > listed.generation
                            ? projectedObserved
                            : listed;
                    return (
                        <CreationJobCard
                            key={job.job_id}
                            job={job}
                            authority={authority}
                            pending={mutationJobId === job.job_id}
                            onAction={runAction}
                        />
                    );
                })}
            </div>
            {nextSequence !== null ? (
                <button
                    type="button"
                    disabled={pending}
                    onClick={() => void loadNextPage()}
                >
                    {pending ? "Loading next jobs…" : "Load next jobs"}
                </button>
            ) : null}
        </section>
    );
}

function CreationJobCard({
    job,
    authority,
    pending,
    onAction,
}: {
    job: CreationJobView;
    authority: CreationExecutionAuthority | null;
    pending: boolean;
    onAction: (
        job: CreationJobView,
        action: CreationJobAction,
    ) => Promise<void>;
}) {
    const exactAuthority =
        authority !== null &&
        sameCreationExecutionAuthority(job.authority, authority);
    const actions =
        authority === null
            ? []
            : recoveryActionsForCreationJob(job.record, authority);
    return (
        <article
            id={jobCardId(job.job_id)}
            className="creation-job-card"
            aria-label={`${OPERATION_LABELS[job.operation]} ${job.job_id}`}
            tabIndex={-1}
        >
            <header>
                <div>
                    <h4>{OPERATION_LABELS[job.operation]}</h4>
                    <code>{job.job_id}</code>
                </div>
                <strong>{STATE_LABELS[job.state]}</strong>
            </header>
            <dl className="creation-job-facts">
                <div>
                    <dt>Progress</dt>
                    <dd>{labelToken(job.progress)}</dd>
                </div>
                <div>
                    <dt>Analysis</dt>
                    <dd>
                        {job.analysisStatus
                            ? labelToken(job.analysisStatus)
                            : "Not available"}
                    </dd>
                </div>
                <div>
                    <dt>Cleanup</dt>
                    <dd>
                        {job.cleanupPending
                            ? "Cleanup pending"
                            : "No cleanup pending"}
                    </dd>
                </div>
                <div>
                    <dt>Recovery</dt>
                    <dd>
                        {job.recoveryRequired
                            ? "Recovery required"
                            : "Not required"}
                    </dd>
                </div>
                <div>
                    <dt>Authority</dt>
                    <dd>{exactAuthority ? "Current" : "Stale authority"}</dd>
                </div>
            </dl>
            {job.error ? (
                <p className="inline-error">{job.error.message}</p>
            ) : null}
            {job.state === "succeeded" ? (
                <p className="creation-candidate-notice">
                    Candidate produced; readiness and active evidence remain
                    unchanged until a reviewed phase report references its exact
                    identity.
                </p>
            ) : null}
            {actions.length > 0 ? (
                <div className="actions">
                    {actions.map((action) => (
                        <button
                            key={action}
                            type="button"
                            disabled={pending}
                            aria-label={`${actionLabel(action)} ${job.job_id}`}
                            onClick={() => void onAction(job, action)}
                        >
                            {pending ? "Working…" : actionLabel(action)}
                        </button>
                    ))}
                </div>
            ) : null}
        </article>
    );
}

function replaceJob(
    jobs: CreationJobView[],
    updated: CreationJobView,
): CreationJobView[] {
    return jobs.map((job) => (job.job_id === updated.job_id ? updated : job));
}

function mergeJobPages(
    current: CreationJobView[],
    next: CreationJobView[],
): CreationJobView[] {
    const seen = new Set(current.map((job) => job.job_id));
    for (const job of next) {
        if (seen.has(job.job_id))
            throw new Error(
                "Forge Studio returned duplicate creation jobs across pages",
            );
        seen.add(job.job_id);
    }
    return [...current, ...next];
}

function actionLabel(action: CreationJobAction): string {
    if (action === "rollback") return "Roll back";
    if (action === "cleanup") return "Clean up";
    return action[0].toUpperCase() + action.slice(1);
}

function actionStatus(action: CreationJobAction): string {
    if (action === "cancel") return "Canceling";
    if (action === "resume") return "Resuming";
    if (action === "rollback") return "Rolling back";
    if (action === "cleanup") return "Cleaning up";
    return "Retrying";
}

function labelToken(value: string): string {
    const text = value.replaceAll("_", " ");
    return text[0].toUpperCase() + text.slice(1);
}

function jobCardId(jobId: string): string {
    return `creation-job-card-${jobId}`;
}

function describeError(error: unknown): string {
    return error instanceof Error
        ? error.message
        : "Creation job activity failed";
}

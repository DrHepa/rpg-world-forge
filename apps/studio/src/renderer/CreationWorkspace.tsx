import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type {
    ForgeStudioApi,
    StudioCreationAuthorityCapabilities,
    StudioCreationArtifact,
    StudioCreationArtifactInspectResult,
    StudioCreationDocumentListResult,
    StudioCreationEvidence,
    StudioCreationOutputGrant,
    StudioCreationReadinessResult,
    StudioCreationWorkflowResult,
    StudioCreationWorkspace,
    StudioCreationWorkspaceOpenResult,
} from "../shared/studio-api";
import { CreationJobActivity } from "./CreationJobActivity";
import { CreationAssetPipeline } from "./CreationAssetPipeline";
import { CreationMaterializationPipeline } from "./CreationMaterializationPipeline";
import { CreationRuntimePipeline } from "./CreationRuntimePipeline";
import {
    loadCreationAssetpackGrantBindings,
    loadCreationOutputGrantCensus,
} from "./creation-output-grant-state";
import {
    CreationEvidencePanels,
    type CreationEvidencePanelMode,
} from "./CreationEvidencePanels";
import { CreationModuleWorkbench } from "./CreationModuleWorkbench";
import { CreationPhaseWorkspace } from "./CreationPhaseWorkspace";
import { CreationProfileEditor } from "./CreationProfileEditor";
import {
    expectCreationEvidenceResult,
    expectCreationResult,
} from "./creation-service";
import {
    creationCompileParams,
    creationExecutionAuthorityKey,
    creationJobResultSnapshot,
    findPendingCompileJob,
    loadCreationExecutionCensus,
    loadCreationExecutionCensusAfterJob,
    projectCreationJob,
    sameCreationExecutionAuthority,
    type CreationExecutionCensus,
    type CreationJobView,
} from "./creation-execution-state";
import {
    isCreationProfileDirty,
    type CreationNavigationState,
    type CreationProfileDocument,
    validateCreationProfileDocument,
} from "./creation-state";

const CREATION_TABS = [
    "overview",
    "profile",
    "modules",
    "phases",
    "assets",
    "compatibility",
    "materialize",
] as const;
type CreationTab = (typeof CREATION_TABS)[number];

const CREATION_TAB_LABELS: Record<CreationTab, string> = {
    overview: "Overview",
    profile: "Profile",
    modules: "Modules",
    phases: "Phases",
    assets: "Assets",
    compatibility: "Compatibility",
    materialize: "Materialize",
};

const EVIDENCE_TABS = new Set<CreationTab>([
    "assets",
    "compatibility",
    "materialize",
]);
const CREATION_PROJECT_KINDS = new Set([
    "game",
    "universe_library",
    "asset_library",
]);

interface LoadedCreationEvidence {
    authorityKey: string;
    artifactSnapshotHash: string;
    evidence: StudioCreationEvidence;
    artifacts: StudioCreationArtifact[];
    census: CreationExecutionCensus;
}

const CLEAN_NAVIGATION: CreationNavigationState = {
    blocksNavigation: false,
    kind: "clean",
};

interface LoadedCreationWorkspace {
    workspace: StudioCreationWorkspace;
    open: StudioCreationWorkspaceOpenResult;
    workflow: StudioCreationWorkflowResult["workflow"];
    readiness: StudioCreationReadinessResult["readiness"];
    profilePath: string;
    profileFileSha256: string;
    profile: CreationProfileDocument;
}

interface CreationTabApplicability {
    applicable: boolean;
    reason: string | null;
}

export interface CreationWorkspaceProps {
    workspaceId: string;
    generation: number;
    authorityCapabilities?: StudioCreationAuthorityCapabilities | null;
    onNavigationStateChange: (state: CreationNavigationState) => void;
}

export function CreationWorkspace({
    workspaceId,
    generation,
    authorityCapabilities = null,
    onNavigationStateChange,
}: CreationWorkspaceProps) {
    const [activeTab, setActiveTab] = useState<CreationTab>("overview");
    const [loaded, setLoaded] = useState<LoadedCreationWorkspace | null>(null);
    const [draft, setDraft] = useState<CreationProfileDocument | null>(null);
    const [pending, setPending] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [navigationState, setNavigationState] =
        useState<CreationNavigationState>(CLEAN_NAVIGATION);
    const [pendingTab, setPendingTab] = useState<CreationTab | null>(null);
    const [editorGeneration, setEditorGeneration] = useState(0);
    const [creationEvidence, setCreationEvidence] =
        useState<LoadedCreationEvidence | null>(null);
    const [evidencePending, setEvidencePending] = useState(false);
    const [evidenceError, setEvidenceError] = useState<string | null>(null);
    const [artifactInspection, setArtifactInspection] =
        useState<StudioCreationArtifactInspectResult | null>(null);
    const [artifactInspectionPending, setArtifactInspectionPending] =
        useState(false);
    const [artifactInspectionError, setArtifactInspectionError] = useState<
        string | null
    >(null);
    const [compilePending, setCompilePending] = useState(false);
    const [compileStatus, setCompileStatus] = useState(
        "Compilation has not been requested.",
    );
    const [compileError, setCompileError] = useState<string | null>(null);
    const [observedJob, setObservedJob] = useState<unknown>(null);
    const [submittedJobId, setSubmittedJobId] = useState<string | null>(null);
    const [submittedJobOperation, setSubmittedJobOperation] = useState<
        CreationJobView["operation"] | null
    >(null);
    const [assetTrackingError, setAssetTrackingError] = useState<string | null>(null);
    const [creationOutputGrants, setCreationOutputGrants] = useState<
        StudioCreationOutputGrant[]
    >([]);
    const [assetGrantBoundJobs, setAssetGrantBoundJobs] = useState<
        CreationJobView[]
    >([]);
    const [selectedAssetOutputGrantId, setSelectedAssetOutputGrantId] =
        useState<string | null>(null);
    const [assetGrantCensusPhase, setAssetGrantCensusPhase] = useState<
        "idle" | "loading" | "ready" | "failed"
    >("idle");
    const [assetGrantCensusError, setAssetGrantCensusError] = useState<
        string | null
    >(null);
    const [focusJobId, setFocusJobId] = useState<string | null>(null);
    const [jobRefreshToken, setJobRefreshToken] = useState(0);
    const requestToken = useRef(0);
    const evidenceRequestToken = useRef(0);
    const evidenceLoadKeyRef = useRef<string | null>(null);
    const artifactInspectionToken = useRef(0);
    const assetGrantRequestToken = useRef(0);
    const activeTabRef = useRef<CreationTab>("overview");
    const creationEvidenceRef = useRef<LoadedCreationEvidence | null>(null);
    const draftRef = useRef<CreationProfileDocument | null>(null);
    const loadedRef = useRef<LoadedCreationWorkspace | null>(null);
    const tabGuardRef = useRef<HTMLDivElement | null>(null);
    const compileAlertRef = useRef<HTMLParagraphElement | null>(null);
    const compileSubmissionRef = useRef(false);
    const navigationCallbackRef = useRef(onNavigationStateChange);

    useEffect(() => {
        navigationCallbackRef.current = onNavigationStateChange;
    }, [onNavigationStateChange]);

    useEffect(() => {
        if (!compileError) return;
        queueMicrotask(() => compileAlertRef.current?.focus());
    }, [compileError]);

    const reportNavigation = useCallback(
        (state: CreationNavigationState): void => {
            setNavigationState(state);
        },
        [],
    );

    const assetOutputGrants = useMemo(
        () =>
            creationOutputGrants.filter(
                (grant) =>
                    grant.format_version === 1 &&
                    grant.kind === "generic_assetpack_directory",
            ),
        [creationOutputGrants],
    );
    const hasLiveCreationOutputGrant = creationOutputGrants.some((grant) =>
        ["ready", "reserved", "recovery_required"].includes(grant.state),
    );
    const hasPendingBoundAssetJob = assetGrantBoundJobs.some(
        (job) => job.state === "queued" || job.state === "running",
    );
    const selectedAssetOutputGrant =
        assetOutputGrants.find(
            (grant) =>
                grant.grant_id === selectedAssetOutputGrantId &&
                grant.state === "ready",
        ) ?? null;
    const effectiveNavigationState = useMemo<CreationNavigationState>(
        () =>
            navigationState.kind === "request_pending" ||
            navigationState.kind === "staged" ||
            navigationState.kind === "approved" ||
            navigationState.kind === "recovery_required"
                ? navigationState
                : assetGrantCensusPhase === "failed"
                  ? { blocksNavigation: true, kind: "recovery_required" }
                  : assetGrantCensusPhase === "loading" ||
                      hasLiveCreationOutputGrant
                    ? { blocksNavigation: true, kind: "output_grant" }
                    : navigationState,
        [assetGrantCensusPhase, hasLiveCreationOutputGrant, navigationState],
    );

    useEffect(() => {
        navigationCallbackRef.current(effectiveNavigationState);
    }, [effectiveNavigationState]);

    const clearArtifactInspection = useCallback((): void => {
        artifactInspectionToken.current += 1;
        setArtifactInspection(null);
        setArtifactInspectionPending(false);
        setArtifactInspectionError(null);
    }, []);

    const load = useCallback(
        async (preserveDraft: boolean): Promise<boolean> => {
            const token = requestToken.current + 1;
            requestToken.current = token;
            evidenceRequestToken.current += 1;
            clearArtifactInspection();
            evidenceLoadKeyRef.current = null;
            creationEvidenceRef.current = null;
            setCreationEvidence(null);
            setEvidencePending(false);
            setEvidenceError(null);
            setCompilePending(false);
            setCompileError(null);
            setCompileStatus("Compilation has not been requested.");
            setObservedJob(null);
            setSubmittedJobId(null);
            setSubmittedJobOperation(null);
            setAssetTrackingError(null);
            assetGrantRequestToken.current += 1;
            setCreationOutputGrants([]);
            setAssetGrantBoundJobs([]);
            setSelectedAssetOutputGrantId(null);
            setAssetGrantCensusPhase("idle");
            setAssetGrantCensusError(null);
            setFocusJobId(null);
            if (!preserveDraft) {
                loadedRef.current = null;
                draftRef.current = null;
                setLoaded(null);
                setDraft(null);
                activeTabRef.current = "overview";
                setActiveTab("overview");
                setPendingTab(null);
                setEditorGeneration((current) => current + 1);
                reportNavigation(CLEAN_NAVIGATION);
            }
            setPending(true);
            setError(null);
            try {
                const openResult = await expectCreationResult(
                    window.forgeStudio.openCreationWorkspace(workspaceId),
                    "creation_workspace.open",
                );
                const open = validateOpenResult(openResult, workspaceId);
                const sourceRevision = open.source_revision;
                const [documentsResult, workflowResult, readinessResult] =
                    await Promise.all([
                        expectCreationResult(
                            window.forgeStudio.listCreationDocuments(
                                workspaceId,
                                sourceRevision,
                            ),
                            "creation_document.list",
                        ),
                        expectCreationResult(
                            window.forgeStudio.getCreationWorkflow(workspaceId),
                            "creation_workflow.get",
                        ),
                        expectCreationResult(
                            window.forgeStudio.inspectCreationReadiness(
                                workspaceId,
                            ),
                            "creation_readiness.inspect",
                        ),
                    ]);
                const documents = validateDocumentList(
                    documentsResult,
                    sourceRevision,
                );
                const profileSummary = documents.documents.filter(
                    (document) =>
                        document.format === "world-forge.creation_profile" &&
                        document.format_version === 1,
                );
                if (profileSummary.length !== 1) {
                    throw new Error(
                        "Generic creation workspace must expose exactly one profile document",
                    );
                }
                const summary = profileSummary[0];
                const readResult = await expectCreationResult(
                    window.forgeStudio.readCreationDocument(
                        workspaceId,
                        sourceRevision,
                        summary.path,
                    ),
                    "creation_document.read",
                );
                const profile = validateReadProfile(
                    readResult,
                    sourceRevision,
                    summary.path,
                    summary.file_sha256,
                );
                const workflow = validateWorkflowResult(workflowResult, open);
                const readiness = validateReadinessResult(
                    readinessResult,
                    open,
                );
                if (requestToken.current !== token) return false;
                const next: LoadedCreationWorkspace = {
                    workspace: open.workspace,
                    open,
                    workflow,
                    readiness,
                    profilePath: summary.path,
                    profileFileSha256: summary.file_sha256,
                    profile,
                };
                loadedRef.current = next;
                setLoaded(next);
                if (!preserveDraft || draftRef.current === null) {
                    draftRef.current = profile;
                    setDraft(profile);
                }
                return true;
            } catch (caught) {
                if (requestToken.current !== token) return false;
                setError(describeError(caught));
                return false;
            } finally {
                if (requestToken.current === token) setPending(false);
            }
        },
        [clearArtifactInspection, reportNavigation, workspaceId],
    );

    const refreshAuthority = useCallback(async (): Promise<void> => {
        if (!(await load(true))) {
            throw new Error("Creation workspace authority refresh failed");
        }
    }, [load]);

    const refreshAuthorityBestEffort = useCallback(async (): Promise<void> => {
        await load(true);
    }, [load]);

    const refreshAfterApply = useCallback(async (): Promise<void> => {
        draftRef.current = null;
        if (!(await load(false))) {
            throw new Error("Creation workspace refresh after apply failed");
        }
    }, [load]);

    useEffect(() => {
        let active = true;
        requestToken.current += 1;
        queueMicrotask(() => {
            if (active) void load(false);
        });
        return () => {
            active = false;
            requestToken.current += 1;
            evidenceRequestToken.current += 1;
            artifactInspectionToken.current += 1;
            assetGrantRequestToken.current += 1;
            creationEvidenceRef.current = null;
        };
    }, [generation, load, workspaceId]);

    const evidenceAuthorityKey = loaded
        ? creationAuthorityKey(loaded.workspace)
        : null;
    const tabApplicability = useMemo(
        () => deriveCreationTabApplicability(loaded, creationEvidence),
        [creationEvidence, loaded],
    );
    const executionApplicable = loaded?.workspace.project_kind === "game";
    const evidenceRequired =
        (activeTab === "overview" && executionApplicable) ||
        (EVIDENCE_TABS.has(activeTab) && tabApplicability[activeTab].applicable);

    useEffect(() => {
        if (!loaded) return;
        const current = activeTabRef.current;
        if (!tabApplicability[current].applicable) {
            if (current === "assets") {
                artifactInspectionToken.current += 1;
                setArtifactInspection(null);
                setArtifactInspectionPending(false);
                setArtifactInspectionError(null);
            }
            activeTabRef.current = "overview";
            setActiveTab("overview");
        }
    }, [loaded, tabApplicability]);

    useEffect(() => {
        if (
            !loaded ||
            !evidenceAuthorityKey ||
            !evidenceRequired ||
            creationEvidence?.authorityKey === evidenceAuthorityKey ||
            evidenceLoadKeyRef.current === evidenceAuthorityKey
        ) {
            return;
        }
        const token = evidenceRequestToken.current + 1;
        evidenceRequestToken.current = token;
        evidenceLoadKeyRef.current = evidenceAuthorityKey;
        setEvidencePending(true);
        setEvidenceError(null);
        void loadCreationEvidenceClosure(
            window.forgeStudio,
            loaded.workspace,
            evidenceAuthorityKey,
        )
            .then((next) => {
                const live = loadedRef.current?.workspace;
                if (
                    evidenceRequestToken.current === token &&
                    live !== undefined &&
                    creationAuthorityKey(live) === evidenceAuthorityKey &&
                    (EVIDENCE_TABS.has(activeTabRef.current) ||
                        (activeTabRef.current === "overview" &&
                            live.project_kind === "game"))
                ) {
                    creationEvidenceRef.current = next;
                    setCreationEvidence(next);
                }
            })
            .catch((caught: unknown) => {
                if (evidenceRequestToken.current === token) {
                    evidenceLoadKeyRef.current = null;
                    setEvidenceError(describeError(caught));
                }
            })
            .finally(() => {
                if (evidenceRequestToken.current === token)
                    setEvidencePending(false);
            });
    }, [
        activeTab,
        creationEvidence,
        evidenceAuthorityKey,
        evidenceRequired,
        loaded,
    ]);

    const reconcileAssetOutputGrants = useCallback(async (): Promise<void> => {
        const expected = creationEvidenceRef.current;
        if (expected === null) return;
        const token = assetGrantRequestToken.current + 1;
        assetGrantRequestToken.current = token;
        setAssetGrantCensusPhase("loading");
        setAssetGrantCensusError(null);
        try {
            const grants = await loadCreationOutputGrantCensus(
                window.forgeStudio,
                expected.census,
            );
            let live = creationEvidenceRef.current;
            if (
                assetGrantRequestToken.current !== token ||
                live === null ||
                live.authorityKey !== expected.authorityKey ||
                live.artifactSnapshotHash !== expected.artifactSnapshotHash
            ) {
                return;
            }
            setCreationOutputGrants(grants);
            const assetGrants = grants.filter(
                (grant) =>
                    grant.format_version === 1 &&
                    grant.kind === "generic_assetpack_directory",
            );
            setSelectedAssetOutputGrantId((current) => {
                const retained = assetGrants.find(
                    (grant) =>
                        grant.grant_id === current && grant.state === "ready",
                );
                if (retained) return retained.grant_id;
                const ready = assetGrants.filter((grant) => grant.state === "ready");
                return ready.length === 1 ? ready[0].grant_id : null;
            });
            const boundJobs = await loadCreationAssetpackGrantBindings(
                window.forgeStudio,
                expected.census,
                grants,
            );
            live = creationEvidenceRef.current;
            if (
                assetGrantRequestToken.current !== token ||
                live === null ||
                live.authorityKey !== expected.authorityKey ||
                live.artifactSnapshotHash !== expected.artifactSnapshotHash
            ) {
                return;
            }
            setAssetGrantBoundJobs(boundJobs);
            const boundJob = boundJobs.length === 1 ? boundJobs[0] : null;
            if (boundJob !== null) {
                setObservedJob(boundJob.record);
                setFocusJobId(boundJob.job_id);
                setJobRefreshToken((current) => current + 1);
            }
            setAssetGrantCensusPhase("ready");
        } catch (caught) {
            if (assetGrantRequestToken.current !== token) return;
            setAssetGrantCensusPhase("failed");
            setAssetGrantCensusError(describeError(caught));
        }
    }, []);

    useEffect(() => {
        if (creationEvidence === null) return;
        void reconcileAssetOutputGrants();
    }, [creationEvidence, reconcileAssetOutputGrants]);

    const updateAssetOutputGrant = useCallback(
        (grant: StudioCreationOutputGrant): void => {
            const expectedWorkspace = loadedRef.current?.workspace.workspace_id;
            if (expectedWorkspace === undefined || grant.workspace_id !== expectedWorkspace) {
                setAssetGrantCensusPhase("failed");
                setAssetGrantCensusError(
                    "Asset output grant belongs to another creation workspace",
                );
                return;
            }
            setCreationOutputGrants((current) =>
                [...current.filter((item) => item.grant_id !== grant.grant_id), grant].sort(
                    (left, right) =>
                        left.grant_id < right.grant_id
                            ? -1
                            : left.grant_id > right.grant_id
                              ? 1
                              : 0,
                ),
            );
            setSelectedAssetOutputGrantId((current) =>
                grant.state === "ready"
                    ? grant.grant_id
                    : current === grant.grant_id
                      ? null
                      : current,
            );
        },
        [],
    );

    const refreshExecutionAfterJob = useCallback(
        async (
            job: CreationJobView,
            submittedAuthorityKey: string,
        ): Promise<void> => {
            const currentWorkspace = loadedRef.current?.workspace;
            const currentEvidence = creationEvidenceRef.current;
            if (
                currentWorkspace === undefined ||
                currentEvidence === null ||
                creationExecutionAuthorityKey(
                    currentEvidence.census.authority,
                ) !== submittedAuthorityKey
            ) {
                return;
            }
            const token = evidenceRequestToken.current + 1;
            evidenceRequestToken.current = token;
            setEvidencePending(true);
            setEvidenceError(null);
            try {
                const census = await loadCreationExecutionCensusAfterJob(
                    window.forgeStudio,
                    currentWorkspace,
                    creationJobResultSnapshot(job),
                );
                const liveWorkspace = loadedRef.current?.workspace;
                if (
                    evidenceRequestToken.current !== token ||
                    liveWorkspace === undefined ||
                    creationAuthorityKey(liveWorkspace) !==
                        creationAuthorityKey(currentWorkspace)
                ) {
                    return;
                }
                const next = loadedEvidenceFromCensus(
                    census,
                    creationAuthorityKey(liveWorkspace),
                );
                creationEvidenceRef.current = next;
                evidenceLoadKeyRef.current = next.authorityKey;
                setCreationEvidence(next);
                setJobRefreshToken((current) => current + 1);
            } catch (caught) {
                if (evidenceRequestToken.current === token) {
                    setEvidenceError(describeError(caught));
                }
            } finally {
                if (evidenceRequestToken.current === token)
                    setEvidencePending(false);
            }
        },
        [],
    );

    useEffect(() => {
        if (creationEvidence === null) return;
        const pollingJobs = assetGrantBoundJobs.filter(
            (job) =>
                job.job_id !== submittedJobId &&
                (job.state === "queued" || job.state === "running"),
        );
        if (pollingJobs.length === 0) return;
        const authority = creationEvidence.census.authority;
        const authorityKey = creationExecutionAuthorityKey(authority);
        let canceled = false;
        const timer = window.setTimeout(() => {
            void (async () => {
                try {
                    const updatedJobs = await Promise.all(
                        pollingJobs.map(async (job) => {
                            const result = await expectCreationEvidenceResult(
                                window.forgeStudio.getCreationJob(job.job_id),
                                "creation_job.get",
                            );
                            const updated = projectCreationJob(
                                result.job,
                                authority.workspaceId,
                                job.authority,
                            );
                            if (
                                updated === null ||
                                updated.job_id !== job.job_id ||
                                updated.operation !== job.operation
                            ) {
                                throw new Error(
                                    "Forge Studio returned a mismatched bound asset release job",
                                );
                            }
                            return updated;
                        }),
                    );
                    if (canceled) return;
                    const liveEvidence = creationEvidenceRef.current;
                    if (
                        liveEvidence === null ||
                        creationExecutionAuthorityKey(
                            liveEvidence.census.authority,
                        ) !== authorityKey
                    ) {
                        return;
                    }
                    const byId = new Map(
                        updatedJobs.map((job) => [job.job_id, job]),
                    );
                    setAssetGrantBoundJobs((current) =>
                        current.map((job) => byId.get(job.job_id) ?? job),
                    );
                    setJobRefreshToken((current) => current + 1);
                    const terminal = updatedJobs.find(
                        (job) =>
                            job.state !== "queued" && job.state !== "running",
                    );
                    if (terminal !== undefined) {
                        await reconcileAssetOutputGrants();
                        if (!canceled) {
                            await refreshExecutionAfterJob(terminal, authorityKey);
                        }
                    }
                } catch (caught) {
                    if (canceled) return;
                    setAssetGrantCensusPhase("failed");
                    setAssetGrantCensusError(describeError(caught));
                }
            })();
        }, 200);
        return () => {
            canceled = true;
            window.clearTimeout(timer);
        };
    }, [
        assetGrantBoundJobs,
        creationEvidence,
        reconcileAssetOutputGrants,
        refreshExecutionAfterJob,
        submittedJobId,
    ]);

    useEffect(() => {
        if (!submittedJobId || !submittedJobOperation || !creationEvidence)
            return;
        const authority = creationEvidence.census.authority;
        const authorityKey = creationExecutionAuthorityKey(authority);
        let canceled = false;
        const timer = window.setTimeout(() => {
            void (async () => {
                try {
                    const result = await expectCreationEvidenceResult(
                        window.forgeStudio.getCreationJob(submittedJobId),
                        "creation_job.get",
                    );
                    if (canceled) return;
                    const liveEvidence = creationEvidenceRef.current;
                    if (
                        liveEvidence === null ||
                        creationExecutionAuthorityKey(
                            liveEvidence.census.authority,
                        ) !== authorityKey
                    ) {
                        return;
                    }
                    const updated = projectCreationJob(
                        result.job,
                        authority.workspaceId,
                        authority,
                    );
                    if (
                        updated === null ||
                        updated.job_id !== submittedJobId ||
                        updated.operation !== submittedJobOperation
                    ) {
                        throw new Error(
                            "Forge Studio returned a mismatched submitted creation job",
                        );
                    }
                    setObservedJob(updated.record);
                    setFocusJobId(updated.job_id);
                    if (updated.operation === "creation.compile") {
                        setCompileStatus(
                            `Compilation job ${updated.job_id} is ${updated.state}.`,
                        );
                    } else {
                        setAssetTrackingError(null);
                    }
                    if (
                        updated.state !== "queued" &&
                        updated.state !== "running"
                    ) {
                        setSubmittedJobId(null);
                        setSubmittedJobOperation(null);
                        await refreshExecutionAfterJob(updated, authorityKey);
                    }
                } catch (caught) {
                    if (canceled) return;
                    setSubmittedJobId(null);
                    setSubmittedJobOperation(null);
                    if (submittedJobOperation === "creation.compile") {
                        setCompileError(describeError(caught));
                        setCompileStatus("Compilation polling failed closed.");
                        queueMicrotask(() => compileAlertRef.current?.focus());
                    } else {
                        setAssetTrackingError(describeError(caught));
                    }
                }
            })();
        }, 200);
        return () => {
            canceled = true;
            window.clearTimeout(timer);
        };
    }, [
        creationEvidence,
        observedJob,
        refreshExecutionAfterJob,
        submittedJobId,
        submittedJobOperation,
    ]);

    useEffect(() => {
        if (!pendingTab) return;
        const frame = window.requestAnimationFrame(() => {
            tabGuardButtons(tabGuardRef.current)[0]?.focus();
        });
        function handleKeyDown(event: KeyboardEvent): void {
            if (event.key === "Escape") {
                event.preventDefault();
                setPendingTab(null);
                focusCreationTab(activeTab);
                return;
            }
            if (event.key !== "Tab") return;
            const buttons = tabGuardButtons(tabGuardRef.current);
            const first = buttons[0];
            const last = buttons.at(-1);
            if (!first || !last) return;
            if (event.shiftKey && document.activeElement === first) {
                event.preventDefault();
                last.focus();
            } else if (!event.shiftKey && document.activeElement === last) {
                event.preventDefault();
                first.focus();
            }
        }
        document.addEventListener("keydown", handleKeyDown);
        return () => {
            window.cancelAnimationFrame(frame);
            document.removeEventListener("keydown", handleKeyDown);
        };
    }, [activeTab, pendingTab]);

    function updateDraft(next: CreationProfileDocument): void {
        draftRef.current = next;
        setDraft(next);
        const authority = loadedRef.current;
        if (authority) {
            const nextDirty = isCreationProfileDirty(authority.profile, next);
            reportNavigation({
                blocksNavigation: nextDirty,
                kind: nextDirty ? "draft" : "clean",
            });
        }
    }

    function requestTab(next: CreationTab, focusAfterSelection = false): void {
        if (!tabApplicability[next].applicable) return;
        if (next === activeTab) return;
        if (navigationState.blocksNavigation) {
            setPendingTab(next);
            return;
        }
        if (activeTabRef.current === "assets" && next !== "assets") {
            clearArtifactInspection();
        }
        reportNavigation(CLEAN_NAVIGATION);
        activeTabRef.current = next;
        setActiveTab(next);
        if (focusAfterSelection) focusCreationTab(next);
    }

    function discardLocalDraftAndSwitch(): void {
        if (!pendingTab || !isDiscardableNavigation(navigationState)) return;
        const next = pendingTab;
        if (loaded) {
            draftRef.current = loaded.profile;
            setDraft(loaded.profile);
        }
        setEditorGeneration((current) => current + 1);
        setPendingTab(null);
        reportNavigation(CLEAN_NAVIGATION);
        if (activeTabRef.current === "assets" && next !== "assets") {
            clearArtifactInspection();
        }
        activeTabRef.current = next;
        setActiveTab(next);
        focusCreationTab(next);
    }

    function stayInCurrentTab(): void {
        setPendingTab(null);
        focusCreationTab(activeTab);
    }

    async function inspectArtifact(artifactId: string): Promise<void> {
        const currentEvidence = creationEvidenceRef.current;
        const currentWorkspace = loadedRef.current?.workspace;
        if (
            !currentEvidence ||
            !currentWorkspace ||
            activeTabRef.current !== "assets"
        )
            return;
        const expectedArtifact = currentEvidence.artifacts.find(
            (artifact) => artifact.artifact_id === artifactId,
        );
        if (!expectedArtifact) {
            setArtifactInspectionError(
                "Artifact is not in the active creation evidence census",
            );
            return;
        }
        const token = artifactInspectionToken.current + 1;
        artifactInspectionToken.current = token;
        setArtifactInspection(null);
        setArtifactInspectionPending(true);
        setArtifactInspectionError(null);
        try {
            const result = await expectCreationEvidenceResult(
                window.forgeStudio.inspectCreationArtifact({
                    ...creationEvidenceAuthority(currentWorkspace),
                    expectedArtifactSnapshotHash:
                        currentEvidence.artifactSnapshotHash,
                    artifactId,
                }),
                "creation_artifact.inspect",
            );
            const inspection = validateArtifactInspectionResult(
                result,
                currentWorkspace,
                currentEvidence.artifactSnapshotHash,
                expectedArtifact,
            );
            const liveWorkspace = loadedRef.current?.workspace;
            const liveEvidence = creationEvidenceRef.current;
            if (
                artifactInspectionToken.current === token &&
                activeTabRef.current === "assets" &&
                liveWorkspace !== undefined &&
                liveEvidence !== null &&
                creationAuthorityKey(liveWorkspace) ===
                    currentEvidence.authorityKey &&
                liveEvidence.authorityKey === currentEvidence.authorityKey &&
                liveEvidence.artifactSnapshotHash ===
                    currentEvidence.artifactSnapshotHash
            ) {
                setArtifactInspection(inspection);
            }
        } catch (caught) {
            if (artifactInspectionToken.current === token) {
                setArtifactInspectionError(describeError(caught));
            }
        } finally {
            if (artifactInspectionToken.current === token)
                setArtifactInspectionPending(false);
        }
    }

    async function compileCurrentProject(): Promise<void> {
        const currentWorkspace = loadedRef.current?.workspace;
        const currentEvidence = creationEvidenceRef.current;
        if (
            compileSubmissionRef.current ||
            currentWorkspace?.project_kind !== "game" ||
            currentEvidence === null ||
            navigationState.blocksNavigation
        ) {
            return;
        }
        const authority = currentEvidence.census.authority;
        const authorityKey = creationExecutionAuthorityKey(authority);
        compileSubmissionRef.current = true;
        setCompilePending(true);
        setCompileError(null);
        setCompileStatus(
            "Checking for an identical queued or running compilation.",
        );
        try {
            const duplicate = await findPendingCompileJob(
                window.forgeStudio,
                authority,
            );
            const liveEvidence = creationEvidenceRef.current;
            if (
                liveEvidence === null ||
                creationExecutionAuthorityKey(liveEvidence.census.authority) !==
                    authorityKey
            ) {
                throw new Error(
                    "Creation execution authority changed before compilation submission",
                );
            }
            if (duplicate) {
                setObservedJob(duplicate.record);
                setSubmittedJobId(null);
                setSubmittedJobOperation(null);
                setFocusJobId(duplicate.job_id);
                setCompileStatus(
                    `Compilation ${duplicate.job_id} is already queued or running; no duplicate was submitted.`,
                );
                return;
            }
            const result = await expectCreationEvidenceResult(
                window.forgeStudio.compileCreationProject(
                    creationCompileParams(authority),
                ),
                "creation_job.create",
            );
            const created = projectCreationJob(
                result.job,
                authority.workspaceId,
                authority,
            );
            if (created === null || created.operation !== "creation.compile") {
                throw new Error(
                    "Forge Studio returned a mismatched compilation submission",
                );
            }
            setObservedJob(created.record);
            setFocusJobId(created.job_id);
            setJobRefreshToken((current) => current + 1);
            if (created.state === "queued" || created.state === "running") {
                setSubmittedJobId(created.job_id);
                setSubmittedJobOperation(created.operation);
                setCompileStatus(
                    `Compilation job ${created.job_id} was ${created.state}.`,
                );
            } else {
                setSubmittedJobId(null);
                setSubmittedJobOperation(null);
                setCompileStatus(
                    `Compilation job ${created.job_id} completed as ${created.state}.`,
                );
                await refreshExecutionAfterJob(created, authorityKey);
            }
        } catch (caught) {
            setSubmittedJobId(null);
            setSubmittedJobOperation(null);
            setCompileError(describeError(caught));
            setCompileStatus("Compilation was not submitted.");
            queueMicrotask(() => compileAlertRef.current?.focus());
        } finally {
            compileSubmissionRef.current = false;
            setCompilePending(false);
        }
    }

    async function trackSubmittedJob(job: CreationJobView): Promise<void> {
        const currentEvidence = creationEvidenceRef.current;
        if (
            currentEvidence === null ||
            creationExecutionAuthorityKey(job.authority) !==
                creationExecutionAuthorityKey(currentEvidence.census.authority)
        ) {
            throw new Error(
                "Creation execution authority changed before job tracking",
            );
        }
        const authorityKey = creationExecutionAuthorityKey(job.authority);
        setAssetTrackingError(null);
        setObservedJob(job.record);
        setFocusJobId(job.job_id);
        setJobRefreshToken((current) => current + 1);
        if (job.state === "queued" || job.state === "running") {
            setSubmittedJobId(job.job_id);
            setSubmittedJobOperation(job.operation);
            return;
        }
        setSubmittedJobId(null);
        setSubmittedJobOperation(null);
        await refreshExecutionAfterJob(job, authorityKey);
    }

    function observeAssetJob(job: unknown): void {
        setObservedJob(job);
        const authority = creationEvidenceRef.current?.census.authority;
        if (authority === undefined) return;
        const projected = projectCreationJob(
            job,
            authority.workspaceId,
            authority,
        );
        if (projected === null) return;
        setFocusJobId(projected.job_id);
        setJobRefreshToken((current) => current + 1);
    }

    function observeMutatedJob(job: unknown): void {
        setObservedJob(job);
        const authority = creationEvidenceRef.current?.census.authority;
        if (authority === undefined) return;
        const projected = projectCreationJob(
            job,
            authority.workspaceId,
        );
        if (projected === null) return;
        setAssetGrantBoundJobs((current) =>
            current.map((item) =>
                item.job_id === projected.job_id ? projected : item,
            ),
        );
        setFocusJobId(projected.job_id);
        setJobRefreshToken((current) => current + 1);
        void reconcileAssetOutputGrants();
        const hasCurrentAuthority = sameCreationExecutionAuthority(
            projected.authority,
            authority,
        );
        if (
            hasCurrentAuthority &&
            (projected.state === "queued" || projected.state === "running")
        ) {
            setSubmittedJobId(projected.job_id);
            setSubmittedJobOperation(projected.operation);
            return;
        }
        if (
            projected.job_id === submittedJobId &&
            projected.operation === submittedJobOperation &&
            projected.state !== "queued" &&
            projected.state !== "running"
        ) {
            setSubmittedJobId(null);
            setSubmittedJobOperation(null);
        }
        if (hasCurrentAuthority) {
            void refreshExecutionAfterJob(
                projected,
                creationExecutionAuthorityKey(authority),
            );
        }
    }

    function moveTab(
        event: React.KeyboardEvent<HTMLButtonElement>,
        current: CreationTab,
    ): void {
        const index = CREATION_TABS.indexOf(current);
        let next: CreationTab | null = null;
        if (event.key === "Home") next = CREATION_TABS[0];
        else if (event.key === "End") next = CREATION_TABS.at(-1) ?? null;
        else if (event.key === "ArrowRight") {
            next = CREATION_TABS[(index + 1) % CREATION_TABS.length];
        } else if (event.key === "ArrowLeft") {
            next =
                CREATION_TABS[
                    (index - 1 + CREATION_TABS.length) % CREATION_TABS.length
                ];
        }
        if (!next) return;
        event.preventDefault();
        requestTab(next, true);
    }

    return (
        <main
            id="creation-workbench"
            className="world-area creation-workspace"
            aria-labelledby="creation-workspace-heading"
            tabIndex={-1}
        >
            <header className="project-header creation-header">
                <div>
                    <p className="breadcrumb">
                        Creation /{" "}
                        {loaded
                            ? projectKindLabel(loaded.workspace.project_kind)
                            : "Project"}
                    </p>
                    <h2 id="creation-workspace-heading">
                        {loaded?.profile.title ?? workspaceId}
                    </h2>
                    <p>
                        {loaded
                            ? `${loaded.workspace.project.id} · source ${loaded.workspace.source_revision.slice(0, 12)}`
                            : "Opening a server-validated generic creation workspace."}
                    </p>
                </div>
                <dl
                    className="project-status"
                    aria-label="Creation workspace identity"
                >
                    <div>
                        <dt>Project kind</dt>
                        <dd>
                            {loaded
                                ? projectKindLabel(
                                      loaded.workspace.project_kind,
                                  )
                                : "—"}
                        </dd>
                    </div>
                    <div>
                        <dt>Current phase</dt>
                        <dd>
                            {loaded?.workflow.current_phase ?? "Not started"}
                        </dd>
                    </div>
                    <div>
                        <dt>Root generation</dt>
                        <dd>
                            {loaded
                                ? String(loaded.workspace.root_generation)
                                : "—"}
                        </dd>
                    </div>
                </dl>
            </header>

            {pending ? (
                <p
                    className="creation-loading"
                    role="status"
                    aria-live="polite"
                >
                    Loading verified creation contracts…
                </p>
            ) : null}
            {error ? (
                <p role="alert" className="inline-error">
                    {error}
                </p>
            ) : null}

            <div
                className="creation-tabbar"
                role="tablist"
                aria-label="Creation workspace sections"
            >
                {CREATION_TABS.map((tab) => (
                    <button
                        key={tab}
                        id={`creation-tab-${tab}`}
                        type="button"
                        role="tab"
                        aria-selected={activeTab === tab}
                        aria-controls={`creation-panel-${tab}`}
                        tabIndex={activeTab === tab ? 0 : -1}
                        disabled={!tabApplicability[tab].applicable}
                        aria-describedby={
                            tabApplicability[tab].reason
                                ? `creation-tab-${tab}-reason`
                                : undefined
                        }
                        onClick={() => requestTab(tab)}
                        onKeyDown={(event) => moveTab(event, tab)}
                    >
                        {CREATION_TAB_LABELS[tab]}
                        {!tabApplicability[tab].applicable ? (
                            <span className="tab-badge">Not applicable</span>
                        ) : null}
                    </button>
                ))}
            </div>
            <div className="creation-tab-reasons" aria-live="polite">
                {CREATION_TABS.map((tab) =>
                    tabApplicability[tab].reason ? (
                        <p key={tab} id={`creation-tab-${tab}-reason`}>
                            {tabApplicability[tab].reason}
                        </p>
                    ) : null,
                )}
            </div>

            <section
                id="creation-panel-overview"
                role="tabpanel"
                aria-labelledby="creation-tab-overview"
                hidden={activeTab !== "overview"}
                className="creation-panel"
            >
                {loaded ? (
                    <>
                        <CreationOverview loaded={loaded} />
                        {loaded.workspace.project_kind === "game" ? (
                            evidencePending && !creationEvidence ? (
                                <p
                                    className="creation-loading"
                                    role="status"
                                    aria-live="polite"
                                >
                                    Loading exact execution authority…
                                </p>
                            ) : evidenceError && !creationEvidence ? (
                                <p role="alert" className="inline-error">
                                    {evidenceError}
                                </p>
                            ) : creationEvidence ? (
                                <>
                                    <fieldset
                                        className="creation-card creation-compile-control"
                                        aria-busy={
                                            compilePending ||
                                            submittedJobId !== null
                                        }
                                    >
                                        <legend>
                                            Deterministic compilation
                                        </legend>
                                        <p>
                                            Compile the exact current source and
                                            reviewed workflow against artifact
                                            snapshot{" "}
                                            <code>
                                                {
                                                    creationEvidence.artifactSnapshotHash
                                                }
                                            </code>
                                            .
                                        </p>
                                        <p>
                                            Success creates candidate artifacts
                                            only. Readiness and active evidence
                                            remain unchanged until a reviewed
                                            phase report references each exact
                                            identity.
                                        </p>
                                        <button
                                            type="button"
                                            disabled={
                                                compilePending ||
                                                submittedJobId !== null ||
                                                navigationState.blocksNavigation
                                            }
                                            onClick={() =>
                                                void compileCurrentProject()
                                            }
                                        >
                                            {compilePending
                                                ? "Checking compilation…"
                                                : "Compile current project"}
                                        </button>
                                        <p
                                            role="status"
                                            aria-live="polite"
                                            aria-label="Compilation status"
                                        >
                                            {compileStatus}
                                        </p>
                                        {compileError ? (
                                            <p
                                                ref={compileAlertRef}
                                                tabIndex={-1}
                                                role="alert"
                                                className="inline-error"
                                            >
                                                {compileError}
                                            </p>
                                        ) : null}
                                    </fieldset>
                                    <CreationJobActivity
                                        api={window.forgeStudio}
                                        workspaceId={
                                            loaded.workspace.workspace_id
                                        }
                                        authority={
                                            creationEvidence.census.authority
                                        }
                                        applicable
                                        observedJob={observedJob}
                                        focusJobId={focusJobId}
                                        refreshToken={jobRefreshToken}
                                        onObservedJobChange={observeMutatedJob}
                                        onRetryCompile={() =>
                                            compileCurrentProject()
                                        }
                                    />
                                </>
                            ) : null
                        ) : (
                            <CreationJobActivity
                                api={window.forgeStudio}
                                workspaceId={loaded.workspace.workspace_id}
                                authority={null}
                                applicable={false}
                                observedJob={null}
                                focusJobId={null}
                                refreshToken={0}
                                onObservedJobChange={() => undefined}
                                onRetryCompile={() => undefined}
                            />
                        )}
                    </>
                ) : null}
            </section>
            <section
                id="creation-panel-profile"
                role="tabpanel"
                aria-labelledby="creation-tab-profile"
                hidden={activeTab !== "profile"}
                className="creation-panel"
            >
                {activeTab === "profile" && loaded && draft ? (
                    <CreationProfileEditor
                        key={`${workspaceId}\u0000${String(generation)}\u0000${String(editorGeneration)}`}
                        workspace={loaded.workspace}
                        workflow={loaded.workflow}
                        profilePath={loaded.profilePath}
                        profileFileSha256={loaded.profileFileSha256}
                        baseProfile={loaded.profile}
                        draftProfile={draft}
                        onDraftChange={updateDraft}
                        onNavigationStateChange={reportNavigation}
                        onAuthorityRefresh={refreshAuthorityBestEffort}
                        onApplied={refreshAfterApply}
                    />
                ) : null}
            </section>
            <section
                id="creation-panel-modules"
                role="tabpanel"
                aria-labelledby="creation-tab-modules"
                hidden={activeTab !== "modules"}
                className="creation-panel"
            >
                {activeTab === "modules" && loaded ? (
                    <CreationModuleWorkbench
                        key={`${workspaceId}\u0000${String(generation)}\u0000${String(editorGeneration)}`}
                        workspace={loaded.workspace}
                        workflow={loaded.workflow}
                        onNavigationStateChange={reportNavigation}
                        onAuthorityRefresh={refreshAuthorityBestEffort}
                        onApplied={refreshAfterApply}
                    />
                ) : null}
            </section>
            <section
                id="creation-panel-phases"
                role="tabpanel"
                aria-labelledby="creation-tab-phases"
                hidden={activeTab !== "phases"}
                className="creation-panel"
            >
                {activeTab === "phases" && loaded ? (
                    <CreationPhaseWorkspace
                        key={`${workspaceId}\u0000${String(generation)}\u0000${String(editorGeneration)}`}
                        workspace={loaded.workspace}
                        workflow={loaded.workflow}
                        onNavigationStateChange={reportNavigation}
                        onWorkflowRefresh={refreshAuthority}
                    />
                ) : null}
            </section>
            {(["assets", "compatibility", "materialize"] as const).map(
                (tab) => (
                    <section
                        key={tab}
                        id={`creation-panel-${tab}`}
                        role="tabpanel"
                        aria-labelledby={`creation-tab-${tab}`}
                        hidden={activeTab !== tab}
                        className="creation-panel"
                    >
                        {activeTab === tab ? (
                            evidencePending ? (
                                <p
                                    className="creation-loading"
                                    role="status"
                                    aria-live="polite"
                                >
                                    Loading active creation evidence…
                                </p>
                            ) : evidenceError ? (
                                <p role="alert" className="inline-error">
                                    {evidenceError}
                                </p>
                            ) : creationEvidence ? (
                                <CreationEvidencePanels
                                    mode={
                                        tab satisfies CreationEvidencePanelMode
                                    }
                                    evidence={creationEvidence.evidence}
                                    artifacts={creationEvidence.artifacts}
                                    inspection={artifactInspection}
                                    inspectionPending={
                                        artifactInspectionPending
                                    }
                                    inspectionError={artifactInspectionError}
                                    onInspect={(artifactId) =>
                                        void inspectArtifact(artifactId)
                                    }
                                    assetPipeline={
                                        tab === "assets" && loaded ? (
                                            <CreationAssetPipeline
                                                key={creationExecutionAuthorityKey(
                                                    creationEvidence.census
                                                        .authority,
                                                )}
                                                api={window.forgeStudio}
                                                workspace={loaded.workspace}
                                                census={
                                                    creationEvidence.census
                                                }
                                                authorityCapabilities={
                                                    authorityCapabilities
                                                }
                                                executionBusy={
                                                    compilePending ||
                                                    submittedJobId !== null ||
                                                    hasPendingBoundAssetJob ||
                                                    assetGrantCensusPhase !==
                                                        "ready"
                                                }
                                                observedJob={observedJob}
                                                trackingError={
                                                    assetGrantCensusError ??
                                                    assetTrackingError
                                                }
                                                grants={assetOutputGrants}
                                                grant={selectedAssetOutputGrant}
                                                onNavigationStateChange={
                                                    reportNavigation
                                                }
                                                onGrantChange={
                                                    (grant) => {
                                                        if (grant) {
                                                            updateAssetOutputGrant(
                                                                grant,
                                                            );
                                                        } else {
                                                            setSelectedAssetOutputGrantId(
                                                                null,
                                                            );
                                                        }
                                                    }
                                                }
                                                onGrantSelectionChange={
                                                    (grantId) => {
                                                        const selected =
                                                            assetOutputGrants.find(
                                                                (grant) =>
                                                                    grant.grant_id ===
                                                                        grantId &&
                                                                    grant.state ===
                                                                        "ready",
                                                            );
                                                        setSelectedAssetOutputGrantId(
                                                            selected?.grant_id ??
                                                                null,
                                                        );
                                                    }
                                                }
                                                onGrantCensusRefresh={
                                                    reconcileAssetOutputGrants
                                                }
                                                onSubmittedJob={
                                                    trackSubmittedJob
                                                }
                                                onObservedJob={observeAssetJob}
                                            />
                                        ) : null
                                    }
                                    runtimePipeline={
                                        tab === "compatibility" && loaded ? (
                                            <CreationRuntimePipeline
                                                key={creationExecutionAuthorityKey(
                                                    creationEvidence.census
                                                        .authority,
                                                )}
                                                api={window.forgeStudio}
                                                workspace={loaded.workspace}
                                                census={creationEvidence.census}
                                                authorityCapabilities={
                                                    authorityCapabilities
                                                }
                                                grants={creationOutputGrants}
                                                executionBusy={
                                                    compilePending ||
                                                    submittedJobId !== null ||
                                                    hasPendingBoundAssetJob ||
                                                    assetGrantCensusPhase !==
                                                        "ready"
                                                }
                                                observedJob={observedJob}
                                                trackingError={
                                                    assetGrantCensusError ??
                                                    assetTrackingError
                                                }
                                                onNavigationStateChange={
                                                    reportNavigation
                                                }
                                                onGrantChange={
                                                    updateAssetOutputGrant
                                                }
                                                onGrantCensusRefresh={
                                                    reconcileAssetOutputGrants
                                                }
                                                onSubmittedJob={
                                                    trackSubmittedJob
                                                }
                                                onObservedJob={observeAssetJob}
                                            />
                                        ) : null
                                    }
                                    materializationPipeline={
                                        tab === "materialize" && loaded ? (
                                            <CreationMaterializationPipeline
                                                key={creationExecutionAuthorityKey(
                                                    creationEvidence.census
                                                        .authority,
                                                )}
                                                api={window.forgeStudio}
                                                workspace={loaded.workspace}
                                                census={creationEvidence.census}
                                                grants={creationOutputGrants}
                                                executionBusy={
                                                    compilePending ||
                                                    submittedJobId !== null ||
                                                    hasPendingBoundAssetJob ||
                                                    assetGrantCensusPhase !==
                                                        "ready"
                                                }
                                                observedJob={observedJob}
                                                trackingError={
                                                    assetGrantCensusError ??
                                                    assetTrackingError
                                                }
                                                onNavigationStateChange={
                                                    reportNavigation
                                                }
                                                onGrantChange={
                                                    updateAssetOutputGrant
                                                }
                                                onGrantCensusRefresh={
                                                    reconcileAssetOutputGrants
                                                }
                                                onSubmittedJob={
                                                    trackSubmittedJob
                                                }
                                                onObservedJob={observeAssetJob}
                                            />
                                        ) : null
                                    }
                                />
                            ) : null
                        ) : null}
                    </section>
                ),
            )}

            {pendingTab ? (
                <div
                    className="modal-backdrop creation-tab-guard"
                    role="dialog"
                    aria-modal="true"
                    aria-labelledby="creation-tab-guard-heading"
                >
                    <div
                        ref={tabGuardRef}
                        className="confirmation-dialog creation-tab-guard-card"
                    >
                        <p className="eyebrow">Protected authoring state</p>
                        <h3 id="creation-tab-guard-heading">
                            Leave {CREATION_TAB_LABELS[activeTab]}?
                        </h3>
                        {isDiscardableNavigation(navigationState) ? (
                            <p>
                                This section has local edits that are not staged
                                evidence. Discard them before switching to{" "}
                                {CREATION_TAB_LABELS[pendingTab]}.
                            </p>
                        ) : (
                            <p>
                                Resolve the{" "}
                                {navigationState.kind.replaceAll("_", " ")}{" "}
                                state in this section before switching tabs.
                                Reviewed or recovering evidence is never
                                discarded here.
                            </p>
                        )}
                        <div className="actions">
                            {isDiscardableNavigation(navigationState) ? (
                                <button
                                    type="button"
                                    className="danger"
                                    onClick={discardLocalDraftAndSwitch}
                                >
                                    Discard local draft and switch
                                </button>
                            ) : null}
                            <button type="button" onClick={stayInCurrentTab}>
                                Stay in {CREATION_TAB_LABELS[activeTab]}
                            </button>
                        </div>
                    </div>
                </div>
            ) : null}
        </main>
    );
}

function isDiscardableNavigation(state: CreationNavigationState): boolean {
    return state.kind === "draft" || state.kind === "facet_buffer";
}


function deriveCreationTabApplicability(
    loaded: LoadedCreationWorkspace | null,
    evidence: LoadedCreationEvidence | null,
): Record<CreationTab, CreationTabApplicability> {
    const applicable = { applicable: true, reason: null };
    const result: Record<CreationTab, CreationTabApplicability> = {
        overview: applicable,
        profile: applicable,
        modules: applicable,
        phases: applicable,
        assets: applicable,
        compatibility: applicable,
        materialize: applicable,
    };
    if (!loaded || loaded.workspace.project_kind !== "game") return result;
    const assetMode = loaded.profile.production.content_modes.assets;
    const runtimeIsExactlyAbsent = isRuntimeTargetExactlyAbsent(loaded.profile.runtime_target);
    const runtimeArtifactsKnown = evidence !== null;
    const hasRetainedRuntimeArtifacts =
        evidence !== null && hasRuntimeArtifactEvidence(evidence.artifacts);
    if (assetMode === "not_applicable") {
        result.assets = {
            applicable: false,
            reason: "Assets are not applicable to this creation profile.",
        };
    }
    if (runtimeIsExactlyAbsent && runtimeArtifactsKnown && !hasRetainedRuntimeArtifacts) {
        const reason =
            "No executable runtime target is present for this creation profile.";
        result.compatibility = { applicable: false, reason };
        result.materialize = { applicable: false, reason };
    }
    return result;
}

function isRuntimeTargetExactlyAbsent(
    runtimeTarget: CreationProfileDocument["runtime_target"],
): boolean {
    return (
        !nonEmptyString(runtimeTarget.requested_adapter) &&
        runtimeTarget.accepted_logic_formats.length === 0 &&
        runtimeTarget.required_features.length === 0 &&
        runtimeTarget.optional_features.length === 0 &&
        runtimeTarget.platforms.length === 0 &&
        !nonEmptyString(runtimeTarget.renderer) &&
        runtimeTarget.input_capabilities.length === 0 &&
        runtimeTarget.asset_formats.length === 0 &&
        runtimeTarget.save_expected === false &&
        runtimeTarget.replay_expected === false &&
        !nonEmptyString(runtimeTarget.packaging_target)
    );
}

function hasRuntimeArtifactEvidence(artifacts: StudioCreationArtifact[]): boolean {
    const runtimeFormats = new Set([
        "world-forge.gamepack",
        "world-forge.runtime_adapter_registry",
        "world-forge.game_runtime_snapshot",
        "world-forge.game_runtime_composition",
        "world-forge.game_runtime_bundle",
        "world-forge.runtime_support_report",
        "world-forge.runtime_support_authority",
        "world-forge.runtime_implementation",
        "world-forge.game_materialization_bundle",
        "world-forge.standalone_game",
    ]);
    return artifacts.some((artifact) =>
        runtimeFormats.has(artifact.subject.format) ||
        !artifact.subject.format.startsWith("world-forge.asset"),
    );
}

function nonEmptyString(value: unknown): boolean {
    return typeof value === "string" && value.length > 0;
}

function focusCreationTab(tab: CreationTab): void {
    window.requestAnimationFrame(() =>
        document
            .querySelector<HTMLButtonElement>(`#creation-tab-${tab}`)
            ?.focus(),
    );
}

function tabGuardButtons(root: HTMLDivElement | null): HTMLButtonElement[] {
    if (!root) return [];
    return [
        ...root.querySelectorAll<HTMLButtonElement>("button:not(:disabled)"),
    ];
}

function CreationOverview({ loaded }: { loaded: LoadedCreationWorkspace }) {
    const readiness = loaded.readiness;
    const authoring =
        readiness.state === "authoring_ready" ||
        readiness.state === "implementation_ready"
            ? "Valid for authoring"
            : readiness.state === "invalid"
              ? "Invalid"
              : "Not yet validated";
    const implementation =
        readiness.state === "implementation_ready"
            ? "Implementation-ready"
            : "Not implementation-ready";
    return (
        <div className="creation-overview-grid">
            <section
                className="creation-card"
                aria-labelledby="creation-identity-heading"
            >
                <p className="eyebrow">Immutable identity</p>
                <h3 id="creation-identity-heading">Project and source</h3>
                <dl className="creation-facts">
                    <div>
                        <dt>Project ID</dt>
                        <dd>{loaded.workspace.project.id}</dd>
                    </div>
                    <div>
                        <dt>Project hash</dt>
                        <dd>
                            <code>{loaded.workspace.project.content_hash}</code>
                        </dd>
                    </div>
                    <div>
                        <dt>Source revision</dt>
                        <dd>
                            <code>{loaded.workspace.source_revision}</code>
                        </dd>
                    </div>
                    <div>
                        <dt>Workflow hash</dt>
                        <dd>
                            <code>
                                {loaded.workspace.workflow_status_hash ??
                                    "Not created"}
                            </code>
                        </dd>
                    </div>
                </dl>
            </section>
            <section
                className="creation-card"
                aria-labelledby="creation-readiness-heading"
            >
                <p className="eyebrow">Independent evidence</p>
                <h3 id="creation-readiness-heading">Readiness dimensions</h3>
                <dl className="creation-readiness">
                    <div>
                        <dt>Authoring validity</dt>
                        <dd>{authoring}</dd>
                    </div>
                    <div>
                        <dt>Implementation readiness</dt>
                        <dd>{implementation}</dd>
                    </div>
                    <div>
                        <dt>Native execution</dt>
                        <dd>
                            {loaded.workspace.project_kind === "game"
                                ? "Not verified"
                                : "N/A"}
                        </dd>
                    </div>
                    <div>
                        <dt>Release</dt>
                        <dd>
                            {readiness.release === "ready"
                                ? "Ready"
                                : "Blocked"}
                        </dd>
                    </div>
                </dl>
                {readiness.blocker_reason_codes.length > 0 ? (
                    <div className="creation-blockers">
                        <strong>Current blockers</strong>
                        <ul>
                            {readiness.blocker_reason_codes
                                .slice(0, 16)
                                .map((code) => (
                                    <li key={code}>
                                        <code>{code}</code>
                                    </li>
                                ))}
                        </ul>
                    </div>
                ) : null}
            </section>
        </div>
    );
}

async function loadCreationEvidenceClosure(
    api: ForgeStudioApi,
    workspace: StudioCreationWorkspace,
    authorityKey: string,
): Promise<LoadedCreationEvidence> {
    const census = await loadCreationExecutionCensus(api, workspace, null);
    return loadedEvidenceFromCensus(census, authorityKey);
}

function loadedEvidenceFromCensus(
    census: CreationExecutionCensus,
    authorityKey: string,
): LoadedCreationEvidence {
    return {
        authorityKey,
        artifactSnapshotHash: census.authority.artifactSnapshotHash,
        evidence: census.evidence,
        artifacts: census.activeArtifacts,
        census,
    };
}

function validateArtifactInspectionResult(
    result: Record<string, unknown>,
    workspace: StudioCreationWorkspace,
    artifactSnapshotHash: string,
    expectedArtifact: StudioCreationArtifact,
): StudioCreationArtifactInspectResult {
    if (
        !matchesEvidenceAuthority(result.authority, workspace) ||
        result.artifact_snapshot_hash !== artifactSnapshotHash ||
        !isRecord(result.artifact) ||
        !matchesCreationArtifactRecord(
            result.artifact,
            expectedArtifact,
            workspace,
        ) ||
        !isRecord(result.projection) ||
        !Array.isArray(result.projection.facts) ||
        !Array.isArray(result.projection.lineage)
    ) {
        throw new Error(
            "Forge Studio returned mismatched artifact inspection evidence",
        );
    }
    return result as unknown as StudioCreationArtifactInspectResult;
}

function matchesCreationArtifactRecord(
    value: Record<string, unknown>,
    expected: StudioCreationArtifact,
    workspace: StudioCreationWorkspace,
): boolean {
    if (
        value.format !== expected.format ||
        value.format_version !== expected.format_version ||
        value.artifact_id !== expected.artifact_id ||
        value.lifecycle !== "active" ||
        value.lifecycle !== expected.lifecycle ||
        value.record_hash !== expected.record_hash ||
        !isRecord(value.subject) ||
        !isRecord(value.producer) ||
        !isRecord(value.references) ||
        !Array.isArray(value.roles) ||
        value.roles.length !== expected.roles.length ||
        !value.roles.every((role, index) => role === expected.roles[index]) ||
        !matchesEvidenceAuthority(value.authority, workspace)
    ) {
        return false;
    }
    return (
        value.subject.format === expected.subject.format &&
        value.subject.format_version === expected.subject.format_version &&
        value.subject.id === expected.subject.id &&
        value.subject.content_hash === expected.subject.content_hash &&
        value.producer.kind === expected.producer.kind &&
        value.producer.phase_id === expected.producer.phase_id &&
        value.producer.reference_id === expected.producer.reference_id &&
        value.references.dependency_count ===
            expected.references.dependency_count &&
        value.references.dependent_count === expected.references.dependent_count
    );
}

function creationEvidenceAuthority(workspace: StudioCreationWorkspace) {
    return {
        workspaceId: workspace.workspace_id,
        expectedRootGeneration: workspace.root_generation,
        expectedSourceRevision: workspace.source_revision,
        expectedWorkflowStatusHash: workspace.workflow_status_hash,
    };
}

function creationAuthorityKey(workspace: StudioCreationWorkspace): string {
    return [
        workspace.workspace_id,
        String(workspace.root_generation),
        workspace.source_revision,
        workspace.workflow_status_hash ?? "",
    ].join("\u0000");
}

function matchesEvidenceAuthority(
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

function projectKindLabel(
    projectKind: StudioCreationWorkspace["project_kind"],
): string {
    if (projectKind === "game") return "Game";
    if (projectKind === "asset_library") return "Asset library";
    return "Universe library";
}

function validateOpenResult(
    result: Record<string, unknown>,
    workspaceId: string,
): StudioCreationWorkspaceOpenResult {
    const workspace = result.workspace;
    if (
        !isWorkspace(workspace) ||
        workspace.workspace_id !== workspaceId ||
        result.route !== "generic" ||
        result.project_kind !== workspace.project_kind ||
        result.source_revision !== workspace.source_revision ||
        result.workflow_status_hash !== workspace.workflow_status_hash ||
        (result.current_phase !== null &&
            typeof result.current_phase !== "string")
    ) {
        throw new Error(
            "Forge Studio returned a mismatched generic workspace route",
        );
    }
    return result as unknown as StudioCreationWorkspaceOpenResult;
}

function validateDocumentList(
    result: Record<string, unknown>,
    sourceRevision: string,
): StudioCreationDocumentListResult {
    if (
        result.source_revision !== sourceRevision ||
        !Array.isArray(result.documents)
    ) {
        throw new Error(
            "Forge Studio returned an invalid creation document list",
        );
    }
    for (const document of result.documents) {
        if (
            !isRecord(document) ||
            typeof document.path !== "string" ||
            typeof document.format !== "string" ||
            typeof document.format_version !== "number" ||
            typeof document.file_sha256 !== "string"
        ) {
            throw new Error(
                "Forge Studio returned an invalid creation document summary",
            );
        }
    }
    return result as unknown as StudioCreationDocumentListResult;
}

function validateReadProfile(
    result: Record<string, unknown>,
    sourceRevision: string,
    expectedPath: string,
    expectedFileSha256: string,
): CreationProfileDocument {
    const document = result.document;
    if (
        result.source_revision !== sourceRevision ||
        !isRecord(document) ||
        document.path !== expectedPath ||
        document.file_sha256 !== expectedFileSha256 ||
        document.format !== "world-forge.creation_profile" ||
        document.format_version !== 1
    ) {
        throw new Error(
            "Forge Studio returned a mismatched creation profile document",
        );
    }
    return validateCreationProfileDocument(document.document);
}

function validateWorkflowResult(
    result: Record<string, unknown>,
    open: StudioCreationWorkspaceOpenResult,
): StudioCreationWorkflowResult["workflow"] {
    if (
        !isRecord(result.workflow) ||
        result.workflow.source_revision !== open.source_revision ||
        result.workflow.status_hash !== open.workflow_status_hash ||
        result.workflow.current_phase !== open.current_phase
    ) {
        throw new Error(
            "Forge Studio returned mismatched creation workflow authority",
        );
    }
    return result.workflow as unknown as StudioCreationWorkflowResult["workflow"];
}

function validateReadinessResult(
    result: Record<string, unknown>,
    open: StudioCreationWorkspaceOpenResult,
): StudioCreationReadinessResult["readiness"] {
    if (
        !isRecord(result.readiness) ||
        result.readiness.source_revision !== open.source_revision ||
        result.readiness.workflow_status_hash !== open.workflow_status_hash ||
        result.readiness.current_phase !== open.current_phase ||
        !Array.isArray(result.readiness.blocker_reason_codes)
    ) {
        throw new Error(
            "Forge Studio returned mismatched creation readiness authority",
        );
    }
    return result.readiness as StudioCreationReadinessResult["readiness"];
}

function isWorkspace(value: unknown): value is StudioCreationWorkspace {
    return (
        isRecord(value) &&
        value.format === "world-forge.studio_creation_workspace" &&
        value.format_version === 1 &&
        typeof value.project_kind === "string" &&
        CREATION_PROJECT_KINDS.has(value.project_kind) &&
        typeof value.workspace_id === "string" &&
        typeof value.source_revision === "string" &&
        isRecord(value.project)
    );
}

function isRecord(value: unknown): value is Record<string, unknown> {
    return typeof value === "object" && value !== null && !Array.isArray(value);
}

function describeError(error: unknown): string {
    return error instanceof Error
        ? error.message
        : "Generic creation workspace failed";
}

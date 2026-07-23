import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";

import type {
  CodexBridgeStatus,
  CodexUserInputQuestion,
  ForgeServiceStatus,
  StudioChangeset,
  StudioChangesetApplyResponse,
  StudioChangesetApproveResponse,
  StudioChangesetCreateResponse,
  StudioChangesetDiffResponse,
  StudioChangesetGetResponse,
  StudioChangesetRejectResponse,
  StudioClientResult,
  StudioErrorEnvelope,
  StudioJobCreateReply,
  StudioSourceListResult,
  StudioSourceReadResult,
  StudioWorkspaceOverviewResult,
  StudioWorldAnalyzeResult,
  StudioWorldValidateResult,
} from "../shared/studio-api";
import { AssetsCockpit } from "./AssetsCockpit";
import { ChangesetReviewPanel } from "./ChangesetReviewPanel";
import { GameCockpit } from "./GameCockpit";
import { NeutralMapCanvas } from "./NeutralMapCanvas";
import {
  beginAssetCatalogInspection,
  beginAssetCatalogList,
  bindAssetCatalogWorkspace,
  createInitialAssetCatalogState,
  receiveAssetCatalogInspection,
  receiveAssetCatalogList,
  selectAssetCatalogCategory,
  type AssetCatalogListMode,
  type AssetCatalogState,
} from "./asset-catalog-state";
import {
  authoringReducer,
  boundedMessage,
  createInitialAuthoringState,
  RequestLimiter,
  selectedCachedDocument,
  selectedDraft,
  selectedSourceSummary,
  sourceVersionKey,
  type SourceSummary,
} from "./authoring-state";
import {
  actionCompletionError,
  changesetReviewReducer,
  createInitialChangesetReviewState,
  expectedReviewSha256,
  reviewActionUnavailableReason,
  reviewEvidenceError,
  sha256Utf8,
  stagedChangesetError,
  type ChangesetReviewAction,
  type StagedDraftSnapshot,
} from "./changeset-review-state";
import {
  projectCanceledGameJob,
  projectCreatedGameJob,
  type GameJobRequest,
  type GameJobView,
  type GameOperation,
} from "./game-job-view";
import {
  boundedFindings,
  changesetRows,
  decodeLegacyList,
  eventRows,
  jobRows,
  workspaceIds,
  type DockRow,
} from "./studio-results";

const INITIAL_STATUS: ForgeServiceStatus = {
  state: "starting",
  message: "Connecting to the local Forge service",
  pid: null,
};

const INITIAL_CODEX_STATUS: CodexBridgeStatus = {
  state: "unbound",
  message: "Codex is not bound to a Forge workspace",
  pid: null,
  workspaceId: null,
};

const DOCK_POLL_INTERVAL_MS = 15_000;
const MAX_VISIBLE_ERRORS = 6;
const COMPACT_DISCIPLINE_TABS_QUERY = "(max-width: 860px)";

type DockTab = "activity" | "changesets" | "jobs";
type WorkbenchTab = "world" | "assets" | "game";
type GameOperationState<T> = Record<GameOperation, T>;
type PendingNavigation =
  | { kind: "workspace"; workspaceId: string }
  | { kind: "source"; document: SourceSummary };

function useMediaQuery(query: string, fallback: boolean): boolean {
  const [matches, setMatches] = useState(() => {
    if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
      return fallback;
    }
    return window.matchMedia(query).matches;
  });

  useEffect(() => {
    if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
      return;
    }
    const mediaQuery = window.matchMedia(query);
    const updateMatch = (): void => setMatches(mediaQuery.matches);
    mediaQuery.addEventListener("change", updateMatch);
    updateMatch();
    return () => mediaQuery.removeEventListener("change", updateMatch);
  }, [query]);

  return matches;
}

export function App() {
  const [status, setStatus] = useState<ForgeServiceStatus>(INITIAL_STATUS);
  const [workspaces, setWorkspaces] = useState<string[]>([]);
  const [registryPending, setRegistryPending] = useState(true);
  const [authoring, dispatch] = useReducer(authoringReducer, undefined, createInitialAuthoringState);
  const [review, reviewDispatch] = useReducer(
    changesetReviewReducer,
    undefined,
    createInitialChangesetReviewState,
  );
  const [activeWorkbench, setActiveWorkbench] = useState<WorkbenchTab>("world");
  const compactDisciplineTabs = useMediaQuery(COMPACT_DISCIPLINE_TABS_QUERY, false);
  const [assetCatalog, setAssetCatalog] = useState(createInitialAssetCatalogState);
  const assetCatalogRef = useRef(assetCatalog);
  const assetCatalogLazyContextRef = useRef<string | null>(null);
  const generationRef = useRef(0);
  const reviewRequestRef = useRef(0);
  const reviewMutationRef = useRef<number | null>(null);
  const limiterRef = useRef(new RequestLimiter(4));
  const [errors, setErrors] = useState<string[]>([]);
  const [serviceActivityCount, setServiceActivityCount] = useState(0);
  const [dockTab, setDockTab] = useState<DockTab>("activity");
  const [dockEvents, setDockEvents] = useState<Record<string, unknown>[]>([]);
  const [dockChangesets, setDockChangesets] = useState<Record<string, unknown>[]>([]);
  const [dockJobs, setDockJobs] = useState<Record<string, unknown>[]>([]);
  const [dockPending, setDockPending] = useState(false);
  const [dockRefresh, setDockRefresh] = useState(0);
  const [gameImmediateJobs, setGameImmediateJobs] = useState<Record<string, unknown>[]>([]);
  const [gamePending, setGamePending] = useState<GameOperationState<boolean>>(
    createGamePendingState,
  );
  const [gameErrors, setGameErrors] = useState<GameOperationState<string | null>>(
    createGameErrorState,
  );
  const [cancelingGameJobIds, setCancelingGameJobIds] = useState<ReadonlySet<string>>(
    () => new Set(),
  );
  const gameRequestTokensRef = useRef<GameOperationState<number>>(
    createGameRequestTokenState(),
  );
  const gameCancelTokenRef = useRef(0);
  const gameCancelTokensRef = useRef(new Map<string, number>());
  const dockCancelTokenRef = useRef(0);
  const dockCancelTokensRef = useRef(new Map<string, number>());
  const [pendingNavigation, setPendingNavigation] = useState<PendingNavigation | null>(null);
  const navigationTriggerRef = useRef<HTMLButtonElement | null>(null);
  const stayButtonRef = useRef<HTMLButtonElement>(null);
  const reviewTriggerRef = useRef<HTMLButtonElement | null>(null);
  const reviewCloseButtonRef = useRef<HTMLButtonElement>(null);
  const reviewPendingStatusRef = useRef<HTMLParagraphElement>(null);
  const reviewActionTriggerRef = useRef<HTMLButtonElement | null>(null);
  const reviewActionCancelRef = useRef<HTMLButtonElement>(null);
  const [pendingReviewAction, setPendingReviewAction] = useState<ChangesetReviewAction | null>(null);
  const [assistantOpen, setAssistantOpen] = useState(false);
  const [codexStatus, setCodexStatus] = useState<CodexBridgeStatus>(INITIAL_CODEX_STATUS);
  const [codexPending, setCodexPending] = useState(false);
  const [threadId, setThreadId] = useState<string | null>(null);
  const [turnId, setTurnId] = useState<string | null>(null);
  const [turnText, setTurnText] = useState("");
  const [codexUpdateCount, setCodexUpdateCount] = useState(0);
  const [userInput, setUserInput] = useState<{
    token: string;
    questions: CodexUserInputQuestion[];
  } | null>(null);
  const [userAnswers, setUserAnswers] = useState<Record<string, string>>({});

  function recordError(message: string): void {
    setErrors((current) => [...current.slice(-(MAX_VISIBLE_ERRORS - 1)), boundedMessage(message)]);
  }

  const commitAssetCatalog = useCallback(
    (update: (current: AssetCatalogState) => AssetCatalogState): AssetCatalogState => {
      const next = update(assetCatalogRef.current);
      assetCatalogRef.current = next;
      setAssetCatalog(next);
      return next;
    },
    [],
  );

  const requestAssetCatalogList = useCallback(
    (mode: AssetCatalogListMode): boolean => {
      const transition = beginAssetCatalogList(assetCatalogRef.current, mode);
      if (!transition) return false;
      commitAssetCatalog(() => transition.state);
      const { intent } = transition;
      const page = intent.page;
      const request = page
        ? () =>
            window.forgeStudio.listAssetCatalog(intent.workspaceId, {
              offset: page.offset,
              manifestRevision: page.manifestRevision,
            })
        : () => window.forgeStudio.listAssetCatalog(intent.workspaceId);
      void limiterRef.current
        .run(request)
        .then((reply) => {
          commitAssetCatalog((current) =>
            receiveAssetCatalogList(current, intent, reply),
          );
        })
        .catch(() => {
          commitAssetCatalog((current) =>
            receiveAssetCatalogList(current, intent, catalogClientFailure()),
          );
        });
      return true;
    },
    [commitAssetCatalog],
  );

  const requestAssetCatalogInspection = useCallback(
    (entryId: string): boolean => {
      const transition = beginAssetCatalogInspection(
        assetCatalogRef.current,
        entryId,
      );
      if (!transition) return false;
      commitAssetCatalog(() => transition.state);
      const { intent } = transition;
      void limiterRef.current
        .run(() =>
          window.forgeStudio.inspectAssetCatalogEntry(
            intent.workspaceId,
            intent.manifestRevision,
            intent.entryId,
          ),
        )
        .then((reply) => {
          commitAssetCatalog((current) =>
            receiveAssetCatalogInspection(current, intent, reply),
          );
        })
        .catch(() => {
          commitAssetCatalog((current) =>
            receiveAssetCatalogInspection(current, intent, catalogClientFailure()),
          );
        });
      return true;
    },
    [commitAssetCatalog],
  );

  async function loadWorkspaceRegistry(): Promise<void> {
    setRegistryPending(true);
    const result = await limiterRef.current.run(() => window.forgeStudio.listWorkspaces());
    const decoded = decodeLegacyList(result, "workspace.list", "workspaces", 100);
    setRegistryPending(false);
    if (decoded.error) {
      recordError(decoded.error);
      return;
    }
    setWorkspaces(workspaceIds(decoded.records));
  }

  useEffect(() => {
    const unsubscribe = window.forgeStudio.onEvent((activity) => {
      if (activity.type === "service-status") {
        setStatus(activity.status);
      } else {
        setServiceActivityCount((count) => Math.min(9_999, count + 1));
      }
    });
    const unsubscribeCodex = window.forgeStudio.onCodexEvent((activity) => {
      if (activity.type === "codex-status") {
        setCodexStatus(activity.status);
        return;
      }
      setCodexUpdateCount((count) => Math.min(9_999, count + 1));
      if (activity.type === "codex-user-input") {
        setUserInput({ token: activity.token, questions: activity.questions.slice(0, 3) });
        setUserAnswers({});
      }
    });
    void window.forgeStudio.getServiceStatus().then((result) => {
      if (result.ok) setStatus(result.value);
      else recordError(result.error.message);
    });
    void window.forgeStudio.initialize().then((result) => {
      if (!result.ok) recordError(result.error.message);
      else if (result.value.kind === "error") recordError(result.value.error.message);
      setRegistryPending(true);
      void limiterRef.current.run(() => window.forgeStudio.listWorkspaces()).then((listed) => {
        const decoded = decodeLegacyList(listed, "workspace.list", "workspaces", 100);
        setRegistryPending(false);
        if (decoded.error) recordError(decoded.error);
        else setWorkspaces(workspaceIds(decoded.records));
      });
    });
    void window.forgeStudio.getCodexStatus().then((result) => {
      if (result.ok) setCodexStatus(result.value);
      else recordError(result.error.message);
    });
    return () => {
      unsubscribe();
      unsubscribeCodex();
    };
  }, []);

  useEffect(() => {
    if (pendingNavigation) stayButtonRef.current?.focus();
  }, [pendingNavigation]);

  useEffect(() => {
    if (review.selectedChangesetId) reviewCloseButtonRef.current?.focus();
  }, [review.selectedChangesetId]);

  useEffect(() => {
    if (pendingReviewAction) reviewActionCancelRef.current?.focus();
  }, [pendingReviewAction]);

  useEffect(() => {
    if (
      review.pending === "approve" ||
      review.pending === "reject" ||
      review.pending === "apply"
    ) {
      reviewPendingStatusRef.current?.focus();
    }
  }, [review.pending]);

  useEffect(() => {
    const workspaceId = authoring.workspaceId;
    if (activeWorkbench !== "assets" || !workspaceId) return;
    const context = `${workspaceId}\u0000${String(authoring.generation)}`;
    if (assetCatalogLazyContextRef.current === context) return;
    assetCatalogLazyContextRef.current = context;
    requestAssetCatalogList("initial");
  }, [
    activeWorkbench,
    authoring.generation,
    authoring.workspaceId,
    requestAssetCatalogList,
  ]);

  useEffect(() => {
    const workspaceId = authoring.workspaceId;
    if (typeof workspaceId !== "string") return undefined;
    const generation = authoring.generation;
    let active = true;
    let inFlight = false;
    async function poll(selectedWorkspaceId: string): Promise<void> {
      if (inFlight || generationRef.current !== generation) return;
      inFlight = true;
      setDockPending(true);
      try {
        const [eventsResult, changesetsResult, jobsResult] = await Promise.all([
          limiterRef.current.run(() =>
            window.forgeStudio.listEvents({ workspace_id: selectedWorkspaceId, limit: 100 }),
          ),
          limiterRef.current.run(() =>
            window.forgeStudio.listChangesets({ workspace_id: selectedWorkspaceId, limit: 40 }),
          ),
          limiterRef.current.run(() =>
            window.forgeStudio.listJobs({ workspace_id: selectedWorkspaceId, limit: 40 }),
          ),
        ]);
        if (!active || generationRef.current !== generation) return;
        const events = decodeLegacyList(eventsResult, "events.list", "events", 100);
        const changesets = decodeLegacyList(
          changesetsResult,
          "changeset.list",
          "changesets",
          40,
        );
        const jobs = decodeLegacyList(jobsResult, "job.list", "jobs", 40);
        for (const error of [events.error, changesets.error, jobs.error]) {
          if (error) recordError(error);
        }
        setDockEvents(events.records);
        setDockChangesets(changesets.records);
        setDockJobs(jobs.records);
      } finally {
        inFlight = false;
        if (active) setDockPending(false);
      }
    }
    void poll(workspaceId);
    const timer = window.setInterval(() => void poll(workspaceId), DOCK_POLL_INTERVAL_MS);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [authoring.generation, authoring.workspaceId, dockRefresh]);

  function moveWorkbenchTab(
    event: React.KeyboardEvent<HTMLButtonElement>,
    current: WorkbenchTab,
  ): void {
    const enabledTabs: WorkbenchTab[] = ["world", "assets", "game"];
    const currentIndex = enabledTabs.indexOf(current);
    let next: WorkbenchTab | null = null;
    if (event.key === "Home") next = enabledTabs[0] ?? null;
    else if (event.key === "End") next = enabledTabs.at(-1) ?? null;
    else if (
      (compactDisciplineTabs && event.key === "ArrowRight") ||
      (!compactDisciplineTabs && event.key === "ArrowDown")
    ) {
      next = enabledTabs[(currentIndex + 1) % enabledTabs.length] ?? null;
    } else if (
      (compactDisciplineTabs && event.key === "ArrowLeft") ||
      (!compactDisciplineTabs && event.key === "ArrowUp")
    ) {
      next =
        enabledTabs[(currentIndex - 1 + enabledTabs.length) % enabledTabs.length] ??
        null;
    }
    if (!next) return;
    event.preventDefault();
    setActiveWorkbench(next);
    window.requestAnimationFrame(() =>
      document.querySelector<HTMLButtonElement>(`#discipline-${next}`)?.focus(),
    );
  }

  function requestWorkspaceSelection(
    workspaceId: string,
    trigger: HTMLButtonElement,
  ): void {
    if (workspaceId === authoring.workspaceId) return;
    if (selectedDraft(authoring)?.dirty) {
      navigationTriggerRef.current = trigger;
      setPendingNavigation({ kind: "workspace", workspaceId });
      return;
    }
    void loadWorkspace(workspaceId);
  }

  async function loadWorkspace(workspaceId: string): Promise<void> {
    const generation = generationRef.current + 1;
    generationRef.current = generation;
    invalidateGameRequests();
    assetCatalogLazyContextRef.current = null;
    commitAssetCatalog((current) =>
      bindAssetCatalogWorkspace(current, workspaceId, generation),
    );
    reviewMutationRef.current = null;
    dispatch({ type: "workspace-selected", workspaceId, generation });
    reviewDispatch({ type: "workspace-changed", workspaceId, generation });
    setPendingReviewAction(null);
    setDockEvents([]);
    setDockChangesets([]);
    setDockJobs([]);
    setGameImmediateJobs([]);
    try {
      const [overview, sources, validation, analysis] = await Promise.all([
        limiterRef.current
          .run(() => window.forgeStudio.getWorkspaceOverview(workspaceId))
          .then((result) =>
            responseResult<"workspace.overview", StudioWorkspaceOverviewResult>(
              result,
              "workspace.overview",
            ),
          ),
        limiterRef.current
          .run(() => window.forgeStudio.listSourceDocuments(workspaceId))
          .then((result) =>
            responseResult<"source.list", StudioSourceListResult>(result, "source.list"),
          ),
        limiterRef.current
          .run(() => window.forgeStudio.validateWorld(workspaceId))
          .then((result) =>
            responseResult<"world.validate", StudioWorldValidateResult>(
              result,
              "world.validate",
            ),
          ),
        limiterRef.current
          .run(() => window.forgeStudio.analyzeWorld(workspaceId))
          .then((result) =>
            responseResult<"world.analyze", StudioWorldAnalyzeResult>(result, "world.analyze"),
          ),
      ]);
      if (generationRef.current !== generation) return;
      dispatch({
        type: "workspace-loaded",
        workspaceId,
        generation,
        overview: overview.overview,
        documents: sources.documents,
        validation: validation.validation,
        analysis: analysis.analysis,
      });
      const first = preferredSource(sources.documents);
      if (first) selectSourceNow(workspaceId, generation, first);
    } catch (error) {
      if (generationRef.current !== generation) return;
      const message = describeError(error);
      dispatch({ type: "workspace-failed", workspaceId, generation, message });
      recordError(message);
    }
  }

  function requestSourceSelection(document: SourceSummary, trigger: HTMLButtonElement): void {
    if (document.path === authoring.selectedPath) return;
    if (selectedDraft(authoring)?.dirty) {
      navigationTriggerRef.current = trigger;
      setPendingNavigation({ kind: "source", document });
      return;
    }
    if (authoring.workspaceId) {
      selectSourceNow(authoring.workspaceId, authoring.generation, document);
    }
  }

  function selectSourceNow(workspaceId: string, generation: number, document: SourceSummary): void {
    dispatch({ type: "source-selected", path: document.path });
    const key = sourceVersionKey(workspaceId, document.path, document.sha256);
    if (authoring.cache[key]) return;
    dispatch({ type: "source-loading", workspaceId, generation, path: document.path });
    void limiterRef.current
      .run(() => window.forgeStudio.readSourceDocument(workspaceId, document.path))
      .then((result) => responseResult<"source.read", StudioSourceReadResult>(result, "source.read"))
      .then((source) => {
        dispatch({
          type: "source-loaded",
          workspaceId,
          generation,
          path: document.path,
          expectedSha256: document.sha256,
          document: source.document,
        });
      })
      .catch((error: unknown) => {
        const message = describeError(error);
        dispatch({
          type: "source-failed",
          workspaceId,
          generation,
          path: document.path,
          message,
        });
        if (generationRef.current === generation) recordError(message);
      });
  }

  async function stageSourceDraft(trigger: HTMLButtonElement): Promise<void> {
    if (
      !draft ||
      !cached ||
      !draft.dirty ||
      draft.jsonSyntaxError !== null ||
      !authoring.workspaceId ||
      review.pending !== null
    ) {
      return;
    }
    const snapshotInput = {
      workspaceId: authoring.workspaceId,
      generation: authoring.generation,
      path: draft.path,
      baseSha256: draft.baseSha256,
      baseSize: cached.size,
      content: draft.text,
    };
    const requestId = reviewRequestRef.current + 1;
    if (reviewMutationRef.current !== null) return;
    reviewRequestRef.current = requestId;
    reviewMutationRef.current = requestId;
    reviewTriggerRef.current = trigger;
    reviewDispatch({
      type: "stage-started",
      workspaceId: snapshotInput.workspaceId,
      generation: snapshotInput.generation,
      requestId,
    });
    try {
      const proposedSha256 = await sha256Utf8(snapshotInput.content);
      if (
        generationRef.current !== snapshotInput.generation ||
        reviewRequestRef.current !== requestId
      ) {
        return;
      }
      const snapshot: StagedDraftSnapshot = { ...snapshotInput, proposedSha256 };
      const staged = await limiterRef.current
        .run(() =>
          window.forgeStudio.stageSourceDocument(
            snapshot.workspaceId,
            snapshot.path,
            snapshot.baseSha256,
            snapshot.content,
          ),
        )
        .then((result) =>
          responseResult<"changeset.create", StudioChangesetCreateResponse["result"]>(
            result,
            "changeset.create",
          ),
        );
      if (
        generationRef.current !== snapshotInput.generation ||
        reviewRequestRef.current !== requestId
      ) {
        return;
      }
      const mismatch = stagedChangesetError(staged.changeset, snapshot);
      if (mismatch) throw new Error(mismatch);
      setDockRefresh((value) => value + 1);
      void openChangesetNow(
        snapshot.workspaceId,
        snapshot.generation,
        staged.changeset.changeset_id,
      );
    } catch (error) {
      if (
        generationRef.current !== snapshotInput.generation ||
        reviewRequestRef.current !== requestId
      ) {
        return;
      }
      const message = describeError(error);
      reviewDispatch({
        type: "request-failed",
        workspaceId: snapshotInput.workspaceId,
        generation: snapshotInput.generation,
        requestId,
        request: "stage",
        message,
      });
      recordError(message);
    } finally {
      if (reviewMutationRef.current === requestId) reviewMutationRef.current = null;
    }
  }

  function requestChangesetReview(changesetId: string, trigger: HTMLButtonElement): void {
    if (!authoring.workspaceId || pendingNavigation) return;
    reviewTriggerRef.current = trigger;
    void openChangesetNow(authoring.workspaceId, authoring.generation, changesetId);
  }

  async function openChangesetNow(
    workspaceId: string,
    generation: number,
    changesetId: string,
  ): Promise<void> {
    const requestId = reviewRequestRef.current + 1;
    reviewRequestRef.current = requestId;
    reviewDispatch({
      type: "open-started",
      workspaceId,
      generation,
      requestId,
      changesetId,
    });
    try {
      const [recordResult, diffResult] = await Promise.all([
        limiterRef.current
          .run(() => window.forgeStudio.getChangeset(changesetId))
          .then((result) =>
            responseResult<"changeset.get", StudioChangesetGetResponse["result"]>(
              result,
              "changeset.get",
            ),
          ),
        limiterRef.current
          .run(() => window.forgeStudio.readChangesetDiff(changesetId))
          .then((result) =>
            responseResult<"changeset.diff", StudioChangesetDiffResponse["result"]>(
              result,
              "changeset.diff",
            ),
          ),
      ]);
      if (generationRef.current !== generation || reviewRequestRef.current !== requestId) return;
      const mismatch = reviewEvidenceError(recordResult.changeset, diffResult.diff, {
        workspaceId,
        changesetId,
      });
      if (mismatch) throw new Error(mismatch);
      reviewDispatch({
        type: "open-succeeded",
        workspaceId,
        generation,
        requestId,
        changesetId,
        record: recordResult.changeset,
        diff: diffResult.diff,
      });
    } catch (error) {
      if (generationRef.current !== generation || reviewRequestRef.current !== requestId) return;
      const message = describeError(error);
      reviewDispatch({
        type: "request-failed",
        workspaceId,
        generation,
        requestId,
        request: "open",
        message,
      });
      recordError(message);
    }
  }

  function closeChangesetReview(): void {
    reviewRequestRef.current += 1;
    reviewDispatch({ type: "closed", requestId: reviewRequestRef.current });
    setPendingReviewAction(null);
    window.requestAnimationFrame(() => reviewTriggerRef.current?.focus());
  }

  function requestReviewAction(
    action: ChangesetReviewAction,
    trigger: HTMLButtonElement,
  ): void {
    const unavailable = reviewActionUnavailableReason(review.record, review.diff, action);
    if (unavailable) {
      recordError(unavailable);
      return;
    }
    reviewActionTriggerRef.current = trigger;
    setPendingReviewAction(action);
  }

  function cancelReviewAction(): void {
    setPendingReviewAction(null);
    window.requestAnimationFrame(() => reviewActionTriggerRef.current?.focus());
  }

  async function confirmReviewAction(): Promise<void> {
    const action = pendingReviewAction;
    const previous = review.record;
    const diff = review.diff;
    const workspaceId = authoring.workspaceId;
    const generation = authoring.generation;
    if (!action || !previous || !diff || !workspaceId) return;
    const unavailable = reviewActionUnavailableReason(previous, diff, action);
    if (unavailable) {
      setPendingReviewAction(null);
      recordError(unavailable);
      return;
    }
    const expectedReview = expectedReviewSha256(previous);
    const requestId = reviewRequestRef.current + 1;
    if (reviewMutationRef.current !== null) return;
    reviewRequestRef.current = requestId;
    reviewMutationRef.current = requestId;
    setPendingReviewAction(null);
    reviewDispatch({
      type: "action-started",
      workspaceId,
      generation,
      requestId,
      changesetId: previous.changeset_id,
      action,
      expectedReviewSha256: expectedReview,
    });
    try {
      const next = await runChangesetAction(action, previous.changeset_id, expectedReview);
      if (generationRef.current !== generation || reviewRequestRef.current !== requestId) return;
      const mismatch = actionCompletionError(previous, next, action);
      if (mismatch) throw new Error(mismatch);
      reviewDispatch({
        type: "action-succeeded",
        workspaceId,
        generation,
        requestId,
        changesetId: previous.changeset_id,
        action,
        previous,
        record: next,
      });
      setDockRefresh((value) => value + 1);
      if (action === "apply") {
        await loadWorkspace(workspaceId);
        window.requestAnimationFrame(() => document.querySelector<HTMLElement>("#world-workbench")?.focus());
      } else {
        window.requestAnimationFrame(() => reviewCloseButtonRef.current?.focus());
      }
    } catch (error) {
      if (generationRef.current !== generation || reviewRequestRef.current !== requestId) return;
      const message = describeError(error);
      reviewDispatch({
        type: "request-failed",
        workspaceId,
        generation,
        requestId,
        request: action,
        message,
      });
      recordError(message);
      window.requestAnimationFrame(() => reviewCloseButtonRef.current?.focus());
    } finally {
      if (reviewMutationRef.current === requestId) reviewMutationRef.current = null;
    }
  }

  async function runChangesetAction(
    action: ChangesetReviewAction,
    changesetId: string,
    expectedReview: string | null,
  ): Promise<StudioChangeset> {
    if (action === "approve") {
      const result = await limiterRef.current.run(() =>
        window.forgeStudio.approveChangeset(changesetId, expectedReview ?? undefined),
      );
      return responseResult<"changeset.approve", StudioChangesetApproveResponse["result"]>(
        result,
        "changeset.approve",
      ).changeset;
    }
    if (action === "reject") {
      const result = await limiterRef.current.run(() =>
        window.forgeStudio.rejectChangeset(changesetId, expectedReview ?? undefined),
      );
      return responseResult<"changeset.reject", StudioChangesetRejectResponse["result"]>(
        result,
        "changeset.reject",
      ).changeset;
    }
    const result = await limiterRef.current.run(() =>
      window.forgeStudio.applyChangeset(changesetId, expectedReview ?? undefined),
    );
    return responseResult<"changeset.apply", StudioChangesetApplyResponse["result"]>(
      result,
      "changeset.apply",
    ).changeset;
  }

  function stayOnDraft(): void {
    setPendingNavigation(null);
    window.requestAnimationFrame(() => navigationTriggerRef.current?.focus());
  }

  function discardAndNavigate(): void {
    const navigation = pendingNavigation;
    dispatch({ type: "draft-discarded" });
    setPendingNavigation(null);
    window.requestAnimationFrame(() => navigationTriggerRef.current?.focus());
    if (!navigation) return;
    if (navigation.kind === "workspace") {
      void loadWorkspace(navigation.workspaceId);
    } else if (authoring.workspaceId) {
      selectSourceNow(authoring.workspaceId, authoring.generation, navigation.document);
    }
  }

  function invalidateGameRequests(): void {
    for (const operation of gameOperations()) {
      gameRequestTokensRef.current[operation] += 1;
    }
    gameCancelTokenRef.current += 1;
    gameCancelTokensRef.current.clear();
    dockCancelTokenRef.current += 1;
    dockCancelTokensRef.current.clear();
    setGamePending(createGamePendingState());
    setGameErrors(createGameErrorState());
    setCancelingGameJobIds(new Set());
  }

  async function submitGameJob(request: GameJobRequest): Promise<void> {
    const workspaceId = authoring.workspaceId;
    const generation = authoring.generation;
    const operation = request.operation;
    if (!workspaceId || gamePending[operation]) return;
    const token = gameRequestTokensRef.current[operation] + 1;
    gameRequestTokensRef.current[operation] = token;
    setGamePending((current) => ({ ...current, [operation]: true }));
    setGameErrors((current) => ({ ...current, [operation]: null }));
    try {
      const reply = await limiterRef.current.run(() =>
        executeGameJobRequest(workspaceId, request),
      );
      if (!isCurrentGameRequest(operation, workspaceId, generation, token)) return;
      if (!reply.ok || reply.value.kind === "error") {
        setGameErrors((current) => ({
          ...current,
          [operation]: "The fixed offline Game job could not be queued.",
        }));
        return;
      }
      if (
        reply.value.kind !== "response" ||
        reply.value.method !== "job.create"
      ) {
        setGameErrors((current) => ({
          ...current,
          [operation]: "Forge Studio returned an invalid Game job response.",
        }));
        return;
      }
      const candidate = reply.value.result.job;
      const view = projectCreatedGameJob(candidate, workspaceId, request);
      if (!view) {
        setGameErrors((current) => ({
          ...current,
          [operation]: "Forge Studio returned an invalid Game job response.",
        }));
        return;
      }
      const immediate = candidate as Record<string, unknown>;
      setGameImmediateJobs((current) => [
        immediate,
        ...current.filter((record) => record.job_id !== view.jobId),
      ].slice(0, 12));
      setDockRefresh((value) => value + 1);
    } catch {
      if (!isCurrentGameRequest(operation, workspaceId, generation, token)) return;
      setGameErrors((current) => ({
        ...current,
        [operation]: "The fixed offline Game job could not be queued.",
      }));
    } finally {
      if (isCurrentGameRequest(operation, workspaceId, generation, token)) {
        setGamePending((current) => ({ ...current, [operation]: false }));
      }
    }
  }

  function isCurrentGameRequest(
    operation: GameOperation,
    workspaceId: string,
    generation: number,
    token: number,
  ): boolean {
    return (
      authoring.workspaceId === workspaceId &&
      generationRef.current === generation &&
      gameRequestTokensRef.current[operation] === token
    );
  }

  async function cancelGameJob(job: GameJobView): Promise<void> {
    const workspaceId = authoring.workspaceId;
    const generation = authoring.generation;
    if (!workspaceId || !job.canCancel || cancelingGameJobIds.has(job.jobId)) return;
    const token = gameCancelTokenRef.current + 1;
    gameCancelTokenRef.current = token;
    gameCancelTokensRef.current.set(job.jobId, token);
    setCancelingGameJobIds((current) => new Set(current).add(job.jobId));
    setGameErrors((current) => ({ ...current, [job.operation]: null }));
    try {
      const result = await window.forgeStudio.cancelJob(job.jobId);
      if (!isCurrentGameCancellation(job.jobId, workspaceId, generation, token)) return;
      if (!result.ok || result.value.kind === "error") {
        setGameErrors((current) => ({
          ...current,
          [job.operation]: "The current Game job could not be canceled.",
        }));
        return;
      }
      if (
        result.value.kind !== "response" ||
        result.value.method !== "job.cancel"
      ) {
        setGameErrors((current) => ({
          ...current,
          [job.operation]: "Forge Studio returned an invalid job cancellation response.",
        }));
        return;
      }
      const candidate = result.value.result.job;
      const updated = projectCanceledGameJob(
        candidate,
        workspaceId,
        job.jobId,
        job.operation,
      );
      if (!updated) {
        setGameErrors((current) => ({
          ...current,
          [job.operation]: "Forge Studio returned an invalid job cancellation response.",
        }));
        return;
      }
      const immediate = candidate as Record<string, unknown>;
      setGameImmediateJobs((current) => [
        immediate,
        ...current.filter((record) => record.job_id !== updated.jobId),
      ].slice(0, 12));
      setDockRefresh((value) => value + 1);
    } catch {
      if (!isCurrentGameCancellation(job.jobId, workspaceId, generation, token)) return;
      setGameErrors((current) => ({
        ...current,
        [job.operation]: "The current Game job could not be canceled.",
      }));
    } finally {
      if (isCurrentGameCancellation(job.jobId, workspaceId, generation, token)) {
        gameCancelTokensRef.current.delete(job.jobId);
        setCancelingGameJobIds((current) => {
          const next = new Set(current);
          next.delete(job.jobId);
          return next;
        });
      }
    }
  }

  function isCurrentGameCancellation(
    jobId: string,
    workspaceId: string,
    generation: number,
    token: number,
  ): boolean {
    return (
      authoring.workspaceId === workspaceId &&
      generationRef.current === generation &&
      gameCancelTokensRef.current.get(jobId) === token
    );
  }

  async function cancelJob(jobId: string): Promise<void> {
    const selected = dockJobs.find((record) => record.job_id === jobId);
    const workspaceId = authoring.workspaceId;
    const generation = authoring.generation;
    if (
      !selected ||
      !workspaceId ||
      selected.workspace_id !== workspaceId ||
      typeof selected.operation !== "string" ||
      (selected.format_version !== 1 && selected.format_version !== 2) ||
      (selected.state !== "queued" && selected.state !== "running")
    ) {
      return;
    }
    const operation = selected.operation;
    const token = dockCancelTokenRef.current + 1;
    dockCancelTokenRef.current = token;
    dockCancelTokensRef.current.set(jobId, token);
    try {
      const result = await window.forgeStudio.cancelJob(jobId);
      if (!isCurrentDockCancellation(jobId, workspaceId, generation, token)) return;
      if (
        !result.ok ||
        result.value.kind === "error" ||
        result.value.kind !== "response" ||
        result.value.method !== "job.cancel" ||
        !cancelReplyMatches(result.value.result.job, {
          jobId,
          workspaceId,
          operation,
          formatVersion: selected.format_version,
        })
      ) {
        recordError("The current job could not be canceled.");
        return;
      }
      setDockRefresh((value) => value + 1);
    } catch {
      if (isCurrentDockCancellation(jobId, workspaceId, generation, token)) {
        recordError("The current job could not be canceled.");
      }
    } finally {
      if (isCurrentDockCancellation(jobId, workspaceId, generation, token)) {
        dockCancelTokensRef.current.delete(jobId);
      }
    }
  }

  function isCurrentDockCancellation(
    jobId: string,
    workspaceId: string,
    generation: number,
    token: number,
  ): boolean {
    return (
      authoring.workspaceId === workspaceId &&
      generationRef.current === generation &&
      dockCancelTokensRef.current.get(jobId) === token
    );
  }

  async function bindCodex(event: React.FormEvent): Promise<void> {
    event.preventDefault();
    if (!authoring.workspaceId) return;
    setCodexPending(true);
    const result = await window.forgeStudio.bindCodexWorkspace(authoring.workspaceId);
    setCodexPending(false);
    if (result.ok) setCodexStatus(result.value);
    else recordError(result.error.message);
  }

  async function startThread(): Promise<void> {
    setCodexPending(true);
    const result = await window.forgeStudio.startCodexThread();
    setCodexPending(false);
    if (result.ok) {
      setThreadId(result.value.threadId);
      setTurnId(null);
    } else recordError(result.error.message);
  }

  async function startTurn(event: React.FormEvent): Promise<void> {
    event.preventDefault();
    if (!threadId || turnText.trim().length === 0) return;
    setCodexPending(true);
    const result = await window.forgeStudio.startCodexTurn(threadId, turnText);
    setCodexPending(false);
    if (result.ok) {
      setTurnText("");
      setTurnId(result.value.turnId);
    } else recordError(result.error.message);
  }

  async function interruptTurn(): Promise<void> {
    if (!threadId || !turnId) return;
    setCodexPending(true);
    const result = await window.forgeStudio.interruptCodexTurn(threadId, turnId);
    setCodexPending(false);
    if (result.ok) setTurnId(null);
    else recordError(result.error.message);
  }

  async function answerUserInput(event: React.FormEvent): Promise<void> {
    event.preventDefault();
    if (!userInput) return;
    const answers: Record<string, string[]> = {};
    for (const question of userInput.questions) {
      const answer = userAnswers[question.id]?.trim();
      if (!answer) return;
      answers[question.id] = [answer];
    }
    setCodexPending(true);
    const result = await window.forgeStudio.answerCodexUserInput(userInput.token, answers);
    setCodexPending(false);
    if (result.ok) setUserInput(null);
    else recordError(result.error.message);
  }

  const draft = selectedDraft(authoring);
  const cached = selectedCachedDocument(authoring);
  const selectedSummary = selectedSourceSummary(authoring);
  const findings = useMemo(
    () => boundedFindings(authoring.validation, authoring.analysis, 64),
    [authoring.analysis, authoring.validation],
  );
  const groupedSources = useMemo(() => groupSources(authoring.documents), [authoring.documents]);
  const activeDockRows = useMemo(() => {
    if (dockTab === "activity") return eventRows(dockEvents);
    if (dockTab === "changesets") return changesetRows(dockChangesets);
    return jobRows(dockJobs, dockEvents);
  }, [dockChangesets, dockEvents, dockJobs, dockTab]);
  const gameJobRecords = useMemo(
    () => mergeGameJobRecords(dockJobs, gameImmediateJobs),
    [dockJobs, gameImmediateJobs],
  );
  const activeWorkbenchTarget = `#${activeWorkbench}-workbench`;
  const activeWorkbenchLabel = titleCase(activeWorkbench);
  const isMapDraft = Boolean(
    draft && draft.jsonSyntaxError === null && selectedSummary?.path.includes("/maps/"),
  );

  return (
    <div className="studio-shell">
      <div
        className="studio-content"
        aria-hidden={review.selectedChangesetId ? true : undefined}
        inert={review.selectedChangesetId !== null}
      >
        <a
          className="skip-link"
          href={activeWorkbenchTarget}
        >
        Skip to {activeWorkbenchLabel} workbench
        </a>
        <header className="app-header">
        <div className="brand-lockup">
          <span className="forge-mark" aria-hidden="true">◆</span>
          <div>
            <p className="eyebrow">Local authoring control plane</p>
            <h1>RPG World Forge Studio</h1>
          </div>
        </div>
        <div className="header-actions">
          <ServiceBadge status={status} />
          <button
            type="button"
            className="secondary"
            aria-expanded={assistantOpen}
            aria-controls="assistant-drawer"
            onClick={() => setAssistantOpen((open) => !open)}
          >
            Assistant
          </button>
        </div>
        </header>

      {errors.length > 0 ? (
        <section className="error-banner" role="alert" aria-label="Studio errors">
          <strong>Studio needs attention</strong>
          <ul>
            {errors.map((error, index) => <li key={`${error}-${String(index)}`}>{error}</li>)}
          </ul>
          <button type="button" className="secondary compact" onClick={() => setErrors([])}>
            Dismiss
          </button>
        </section>
      ) : null}

      <div className="studio-body">
        <aside className="project-rail" aria-label="Projects and disciplines">
          <div className="rail-heading">
            <span>Projects</span>
            <button
              type="button"
              className="icon-button"
              aria-label="Refresh registered workspaces"
              disabled={registryPending}
              onClick={() => void loadWorkspaceRegistry()}
            >
              ↻
            </button>
          </div>
          <nav aria-label="Registered workspaces">
            {registryPending ? <p role="status">Loading workspaces…</p> : null}
            {workspaces.length === 0 && !registryPending ? (
              <p className="empty-state">No registered workspaces.</p>
            ) : (
              <ul className="workspace-list">
                {workspaces.map((workspaceId) => (
                  <li key={workspaceId}>
                    <button
                      type="button"
                      className={workspaceId === authoring.workspaceId ? "active" : ""}
                      aria-current={workspaceId === authoring.workspaceId ? "page" : undefined}
                      onClick={(event) => requestWorkspaceSelection(workspaceId, event.currentTarget)}
                    >
                      <span className="workspace-avatar" aria-hidden="true">
                        {workspaceId.slice(0, 2).toUpperCase()}
                      </span>
                      <span>{workspaceId}</span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </nav>
          <div
            className="discipline-nav"
            role="tablist"
            aria-label="Forge disciplines"
            aria-orientation={compactDisciplineTabs ? "horizontal" : "vertical"}
          >
            <button
              id="discipline-world"
              type="button"
              role="tab"
              aria-selected={activeWorkbench === "world"}
              aria-controls="world-workbench"
              tabIndex={activeWorkbench === "world" ? 0 : -1}
              onClick={() => setActiveWorkbench("world")}
              onKeyDown={(event) => moveWorkbenchTab(event, "world")}
            >
              World
            </button>
            <button
              id="discipline-assets"
              type="button"
              role="tab"
              aria-selected={activeWorkbench === "assets"}
              aria-controls="assets-workbench"
              tabIndex={activeWorkbench === "assets" ? 0 : -1}
              onClick={() => setActiveWorkbench("assets")}
              onKeyDown={(event) => moveWorkbenchTab(event, "assets")}
            >
              Assets
            </button>
            <button
              id="discipline-game"
              type="button"
              role="tab"
              aria-selected={activeWorkbench === "game"}
              aria-controls="game-workbench"
              tabIndex={activeWorkbench === "game" ? 0 : -1}
              onClick={() => setActiveWorkbench("game")}
              onKeyDown={(event) => moveWorkbenchTab(event, "game")}
            >
              Game
            </button>
          </div>
        </aside>

        <main
          id="world-workbench"
          className="world-area"
          role="tabpanel"
          aria-labelledby="discipline-world"
          tabIndex={-1}
          hidden={activeWorkbench !== "world"}
        >
          <ProjectHeader authoring={authoring} />
          <div className="workbench-layout">
            <nav className="source-browser" aria-labelledby="source-browser-heading">
              <div className="section-heading">
                <div>
                  <p className="eyebrow">Manifest authorized</p>
                  <h2 id="source-browser-heading">World sources</h2>
                </div>
                <span>{authoring.documents.length}</span>
              </div>
              {authoring.loadingWorkspace ? <p role="status">Loading world context…</p> : null}
              {authoring.workspaceError ? <p role="alert">{authoring.workspaceError}</p> : null}
              {groupedSources.map(([group, documents]) => (
                <section className="source-group" key={group} aria-labelledby={`group-${slug(group)}`}>
                  <h3 id={`group-${slug(group)}`}>{group}</h3>
                  <ul>
                    {documents.map((document) => {
                      const key = sourceVersionKey(
                        authoring.workspaceId ?? "",
                        document.path,
                        document.sha256,
                      );
                      return (
                        <li key={document.path}>
                          <button
                            type="button"
                            className={document.path === authoring.selectedPath ? "active" : ""}
                            aria-current={document.path === authoring.selectedPath ? "true" : undefined}
                            onClick={(event) => requestSourceSelection(document, event.currentTarget)}
                          >
                            <span>{fileName(document.path)}</span>
                            {authoring.drafts[key]?.dirty ? <em>Draft</em> : <small>{document.kind}</small>}
                          </button>
                        </li>
                      );
                    })}
                  </ul>
                </section>
              ))}
            </nav>

            <section className="authoring-workbench" aria-labelledby="authoring-heading">
              <div className="editor-heading">
                <div>
                  <p className="eyebrow">World / Lore</p>
                  <h2 id="authoring-heading">{selectedSummary?.path ?? "Select a source document"}</h2>
                </div>
                {draft ? (
                  <div className="draft-state">
                    <strong>Draft — not staged</strong>
                    <small>
                      {draft.dirty ? "Modified" : "Matches source"} · Base {draft.baseSha256.slice(0, 12)}
                    </small>
                  </div>
                ) : null}
              </div>
              {authoring.sourcePending ? <p role="status">Reading verified source…</p> : null}
              {authoring.sourceError ? <p className="inline-error" role="alert">{authoring.sourceError}</p> : null}
              {draft && cached ? (
                <div className="editor-preview-grid">
                  <div className="editor-pane">
                    <label htmlFor="source-draft">In-memory source draft</label>
                    <textarea
                      id="source-draft"
                      className="source-editor"
                      spellCheck={false}
                      value={draft.text}
                      onChange={(event) =>
                        dispatch({ type: "draft-changed", text: event.target.value })
                      }
                    />
                    <div className="editor-status" aria-live="polite">
                      <span>{draft.text.length.toLocaleString("en-US")} characters</span>
                      <span>No autosave · no repository writes</span>
                    </div>
                    <div className="stage-review-row">
                      <button
                        type="button"
                        disabled={
                          !draft.dirty ||
                          draft.jsonSyntaxError !== null ||
                          review.pending !== null
                        }
                        onClick={(event) => void stageSourceDraft(event.currentTarget)}
                      >
                        {review.pending === "stage" ? "Staging exact snapshot…" : "Stage for review"}
                      </button>
                      <small>
                        Sends this exact path, base SHA-256, and in-memory content to a review
                        changeset. It does not write the source file.
                      </small>
                    </div>
                    {draft.jsonSyntaxError ? (
                      <p className="syntax-error" role="alert">
                        JSON syntax: {draft.jsonSyntaxError}
                      </p>
                    ) : selectedSummary?.path.endsWith(".json") ? (
                      <p className="syntax-valid" role="status">JSON syntax is valid.</p>
                    ) : null}
                  </div>
                  <div className="preview-pane">
                    {isMapDraft ? (
                      <NeutralMapCanvas text={draft.text} />
                    ) : (
                      <section className="preview-placeholder" aria-label="Draft preview">
                        <span aria-hidden="true">◇</span>
                        <h3>Structured preview</h3>
                        <p>
                          Neutral 2.5D preview activates for exact map JSON drafts. Other lore
                          remains safely visible as text.
                        </p>
                        <small>Draft preview — non-authoritative</small>
                      </section>
                    )}
                  </div>
                </div>
              ) : (
                <div className="workbench-empty">
                  <span aria-hidden="true">◇</span>
                  <p>Select a registered workspace and source document to begin.</p>
                </div>
              )}
            </section>

            <FindingsInspector authoring={authoring} findings={findings} />
          </div>
        </main>

        <main
          id="assets-workbench"
          className="world-area assets-area"
          role="tabpanel"
          aria-labelledby="discipline-assets"
          tabIndex={-1}
          hidden={activeWorkbench !== "assets"}
        >
          <AssetsCockpit
            state={assetCatalog}
            active={activeWorkbench === "assets"}
            onList={requestAssetCatalogList}
            onInspect={requestAssetCatalogInspection}
            onCategory={(category) =>
              commitAssetCatalog((current) =>
                selectAssetCatalogCategory(current, category),
              )
            }
          />
        </main>

        <main
          id="game-workbench"
          className="world-area game-area"
          role="tabpanel"
          aria-labelledby="discipline-game"
          tabIndex={-1}
          hidden={activeWorkbench !== "game"}
        >
          <GameCockpit
            key={`${authoring.workspaceId ?? "none"}\u0000${String(authoring.generation)}`}
            workspaceId={authoring.workspaceId}
            repositories={{
              gameRegistered: Boolean(authoring.overview?.repositories.game_registered),
              bundleRegistered: Boolean(authoring.overview?.repositories.bundle_registered),
            }}
            records={gameJobRecords}
            events={dockEvents}
            pending={gamePending}
            errors={gameErrors}
            cancelingJobIds={cancelingGameJobIds}
            onSubmit={(request) => void submitGameJob(request)}
            onCancel={(job) => void cancelGameJob(job)}
          />
        </main>
      </div>

      <BottomDock
        tab={dockTab}
        pending={dockPending}
        rows={activeDockRows}
        serviceActivityCount={serviceActivityCount}
        onTab={setDockTab}
        onCancel={(jobId) => void cancelJob(jobId)}
        onOpenChangeset={requestChangesetReview}
      />

        <AssistantDrawer
        open={assistantOpen}
        workspaceId={authoring.workspaceId}
        status={codexStatus}
        pending={codexPending}
        threadId={threadId}
        turnId={turnId}
        turnText={turnText}
        updateCount={codexUpdateCount}
        userInput={userInput}
        answers={userAnswers}
        onClose={() => setAssistantOpen(false)}
        onBind={(event) => void bindCodex(event)}
        onNewThread={() => void startThread()}
        onTurnText={setTurnText}
        onSendTurn={(event) => void startTurn(event)}
        onInterrupt={() => void interruptTurn()}
        onAnswer={(id, answer) => setUserAnswers((current) => ({ ...current, [id]: answer }))}
        onSubmitAnswers={(event) => void answerUserInput(event)}
        />

      {pendingNavigation ? (
        <div className="modal-backdrop">
          <section
            className="confirmation-dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby="discard-heading"
            aria-describedby="discard-description"
          >
            <p className="eyebrow">Unstaged work</p>
            <h2 id="discard-heading">Discard this in-memory draft?</h2>
            <p id="discard-description">
              The current draft has not been staged or written to the repository.
            </p>
            <div className="actions">
              <button ref={stayButtonRef} type="button" onClick={stayOnDraft}>Stay here</button>
              <button type="button" className="danger" onClick={discardAndNavigate}>
                Discard draft
              </button>
            </div>
          </section>
        </div>
      ) : null}

      <p className="sr-only" role="status" aria-live="polite">
        {authoring.loadingWorkspace
          ? "Loading selected workspace"
          : authoring.workspaceId
            ? `Workspace ${authoring.workspaceId} ready`
            : "Choose a workspace"}
      </p>
      </div>

      {review.selectedChangesetId ? (
        <ChangesetReviewPanel
          state={review}
          closeButtonRef={reviewCloseButtonRef}
          pendingStatusRef={reviewPendingStatusRef}
          obscured={pendingReviewAction !== null}
          onClose={closeChangesetReview}
          onRequestAction={requestReviewAction}
        />
      ) : null}

      {pendingReviewAction ? (
        <ReviewActionConfirmation
          action={pendingReviewAction}
          legacy={review.record?.format_version === 1}
          dirtyDraft={Boolean(draft?.dirty)}
          cancelButtonRef={reviewActionCancelRef}
          onCancel={cancelReviewAction}
          onConfirm={() => void confirmReviewAction()}
        />
      ) : null}
    </div>
  );
}

function ProjectHeader({ authoring }: { authoring: ReturnType<typeof createInitialAuthoringState> }) {
  const overview = authoring.overview;
  const validation = authoring.validation;
  return (
    <header className="project-header">
      <div>
        <p className="breadcrumb">World / Authoring cockpit</p>
        <h2>{overview?.project.title ?? authoring.workspaceId ?? "No world selected"}</h2>
        <p>{overview ? `${overview.project.world_id} · revision ${String(overview.status.revision)}` : "Choose a registered workspace from the project rail."}</p>
      </div>
      <dl className="project-status" aria-label="Project status">
        <div>
          <dt>Phase</dt>
          <dd>{overview?.status.current_phase ?? "Not reported"}</dd>
        </div>
        <div>
          <dt>Validation</dt>
          <dd className={validation?.valid ? "valid" : validation ? "invalid" : "neutral"}>
            {validation ? (validation.valid ? "Valid" : "Needs attention") : "Not run"}
          </dd>
        </div>
        <div>
          <dt>Canon</dt>
          <dd>{overview?.status.canon_locked ? "Locked" : "Open"}</dd>
        </div>
      </dl>
    </header>
  );
}

function FindingsInspector({
  authoring,
  findings,
}: {
  authoring: ReturnType<typeof createInitialAuthoringState>;
  findings: ReturnType<typeof boundedFindings>;
}) {
  return (
    <aside className="findings-inspector" aria-labelledby="findings-heading">
      <div className="section-heading">
        <div>
          <p className="eyebrow">Release evidence</p>
          <h2 id="findings-heading">Findings</h2>
        </div>
        <span>{findings.findings.length}</span>
      </div>
      {authoring.validation ? (
        <p className="inspector-summary">
          {authoring.validation.valid ? "Release validation passed" : "Release validation found issues"}
          {` · ${String(authoring.validation.object_count)} objects`}
        </p>
      ) : (
        <p className="empty-state">Validation and narrative findings appear after selecting a world.</p>
      )}
      <ol className="finding-list">
        {findings.findings.map((finding) => (
          <li key={finding.id} className={`finding-${finding.severity}`}>
            <div><strong>{finding.code}</strong><span>{finding.severity}</span></div>
            <p>{finding.message}</p>
            <small>{finding.path}</small>
          </li>
        ))}
      </ol>
      {findings.truncated ? <p className="bounded-note">More findings exist; this view is bounded.</p> : null}
    </aside>
  );
}

function BottomDock({
  tab,
  pending,
  rows,
  serviceActivityCount,
  onTab,
  onCancel,
  onOpenChangeset,
}: {
  tab: DockTab;
  pending: boolean;
  rows: DockRow[];
  serviceActivityCount: number;
  onTab: (tab: DockTab) => void;
  onCancel: (jobId: string) => void;
  onOpenChangeset: (changesetId: string, trigger: HTMLButtonElement) => void;
}) {
  return (
    <section className="bottom-dock" aria-labelledby="dock-heading">
      <div className="dock-tabs" role="tablist" aria-label="Workspace records">
        <h2 id="dock-heading" className="sr-only">Workspace records</h2>
        {(["activity", "changesets", "jobs"] as const).map((candidate) => (
          <button
            key={candidate}
            type="button"
            role="tab"
            aria-selected={tab === candidate}
            onClick={() => onTab(candidate)}
          >
            {titleCase(candidate)}
          </button>
        ))}
        <span role="status" aria-live="polite">
          {pending ? "Refreshing…" : `${String(rows.length)} rows`}
          {serviceActivityCount > 0 ? ` · ${String(serviceActivityCount)} live updates` : ""}
        </span>
      </div>
      <div className="dock-content" role="tabpanel">
        {rows.length === 0 ? (
          <p className="empty-state">No {tab} records for this workspace.</p>
        ) : (
          <ol className="dock-list">
            {rows.map((row) => (
              <li key={row.id}>
                <div>
                  <strong>{row.title}</strong>
                  {row.state ? <span className={`state state-${slug(row.state)}`}>{row.state}</span> : null}
                </div>
                <p>{row.detail}</p>
                <small>{row.meta}</small>
                {row.progress !== null ? (
                  <label className="progress-label">
                    Progress {row.progress}%
                    <progress max="100" value={row.progress}>{row.progress}%</progress>
                  </label>
                ) : null}
                {tab === "jobs" && row.state && ["queued", "running"].includes(row.state) ? (
                  <button
                    type="button"
                    className="secondary compact"
                    aria-label={`Cancel ${row.title} job ${row.id}`}
                    onClick={() => onCancel(row.id)}
                  >
                    Cancel job
                  </button>
                ) : null}
                {tab === "changesets" ? (
                  <button
                    type="button"
                    className="secondary compact"
                    onClick={(event) => onOpenChangeset(row.id, event.currentTarget)}
                  >
                    Open review
                  </button>
                ) : null}
              </li>
            ))}
          </ol>
        )}
      </div>
    </section>
  );
}

function ReviewActionConfirmation({
  action,
  legacy,
  dirtyDraft,
  cancelButtonRef,
  onCancel,
  onConfirm,
}: {
  action: ChangesetReviewAction;
  legacy: boolean;
  dirtyDraft: boolean;
  cancelButtonRef: React.RefObject<HTMLButtonElement | null>;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const heading =
    action === "approve"
      ? "Approve this reviewed changeset?"
      : action === "reject"
        ? "Reject this changeset?"
        : "Apply this approved changeset?";
  const description =
    action === "approve"
      ? "Approval records this human review only. It does not apply or write source files."
      : action === "reject"
        ? legacy
          ? "This legacy review has no immutable exact diff. Rejection closes it without changing source files."
          : "Rejection closes this reviewed proposal without changing source files."
        : `This separate action writes the exact approved v2 proposal after the service rechecks its review identity and base hashes.${
            dirtyDraft
              ? " After success, verified sources refresh; a draft based on a changed source will no longer be active."
              : ""
          }`;
  const confirmLabel =
    action === "approve"
      ? "Approve only"
      : action === "reject"
        ? "Confirm rejection"
        : "Confirm apply";
  return (
    <div className="modal-backdrop">
      <section
        className="confirmation-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="review-action-heading"
        aria-describedby="review-action-description"
        onKeyDown={(event) => containConfirmationFocus(event, onCancel)}
      >
        <p className="eyebrow">Explicit human decision</p>
        <h2 id="review-action-heading">{heading}</h2>
        <p id="review-action-description">{description}</p>
        <div className="actions">
          <button ref={cancelButtonRef} type="button" onClick={onCancel}>Return to review</button>
          <button
            type="button"
            className={action === "approve" ? undefined : "danger"}
            onClick={onConfirm}
          >
            {confirmLabel}
          </button>
        </div>
      </section>
    </div>
  );
}

function containConfirmationFocus(
  event: React.KeyboardEvent<HTMLElement>,
  cancel: () => void,
): void {
  if (event.key === "Escape") {
    event.preventDefault();
    cancel();
    return;
  }
  if (event.key !== "Tab") return;
  const focusable = Array.from(
    event.currentTarget.querySelectorAll<HTMLButtonElement>("button:not(:disabled)"),
  );
  const first = focusable[0];
  const last = focusable.at(-1);
  if (!first || !last) return;
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
}

function AssistantDrawer({
  open,
  workspaceId,
  status,
  pending,
  threadId,
  turnId,
  turnText,
  updateCount,
  userInput,
  answers,
  onClose,
  onBind,
  onNewThread,
  onTurnText,
  onSendTurn,
  onInterrupt,
  onAnswer,
  onSubmitAnswers,
}: {
  open: boolean;
  workspaceId: string | null;
  status: CodexBridgeStatus;
  pending: boolean;
  threadId: string | null;
  turnId: string | null;
  turnText: string;
  updateCount: number;
  userInput: { token: string; questions: CodexUserInputQuestion[] } | null;
  answers: Record<string, string>;
  onClose: () => void;
  onBind: (event: React.FormEvent) => void;
  onNewThread: () => void;
  onTurnText: (text: string) => void;
  onSendTurn: (event: React.FormEvent) => void;
  onInterrupt: () => void;
  onAnswer: (id: string, answer: string) => void;
  onSubmitAnswers: (event: React.FormEvent) => void;
}) {
  return (
    <aside
      id="assistant-drawer"
      className="assistant-drawer"
      hidden={!open}
      aria-labelledby="assistant-heading"
    >
      <div className="assistant-heading">
        <div><p className="eyebrow">Read-only authoring partner</p><h2 id="assistant-heading">Assistant</h2></div>
        <button type="button" className="icon-button" aria-label="Close Assistant" onClick={onClose}>×</button>
      </div>
      <p className="assistant-status" role="status">{status.message}</p>
      <form onSubmit={onBind}>
        <label>
          Registered workspace ID
          <input aria-label="Registered workspace ID" value={workspaceId ?? ""} readOnly />
        </label>
        <div className="actions">
          <button type="submit" disabled={pending || !workspaceId}>Bind Codex</button>
          <button type="button" className="secondary" disabled={pending || status.state !== "ready"} onClick={onNewThread}>New thread</button>
        </div>
      </form>
      <form onSubmit={onSendTurn}>
        <label>
          Turn message
          <textarea aria-label="Turn message" value={turnText} onChange={(event) => onTurnText(event.target.value)} disabled={!threadId} />
        </label>
        <div className="actions">
          <button type="submit" disabled={pending || !threadId || turnText.trim().length === 0}>Send turn</button>
          <button type="button" className="secondary" disabled={pending || !threadId || !turnId} onClick={onInterrupt}>Interrupt turn</button>
        </div>
      </form>
      <small>{threadId ? `Thread ${threadId}` : "No active thread"} · {String(updateCount)} summarized updates</small>
      {userInput ? (
        <form className="user-input-form" onSubmit={onSubmitAnswers}>
          <h3>Assistant needs input</h3>
          {userInput.questions.map((question) => (
            <label key={question.id}>
              {boundedMessage(question.question, 240)}
              {question.options ? (
                <select value={answers[question.id] ?? ""} onChange={(event) => onAnswer(question.id, event.target.value)}>
                  <option value="">Choose an answer</option>
                  {question.options.slice(0, 3).map((option) => <option key={option.label} value={option.label}>{boundedMessage(option.label, 80)}</option>)}
                </select>
              ) : (
                <input type={question.isSecret ? "password" : "text"} value={answers[question.id] ?? ""} onChange={(event) => onAnswer(question.id, event.target.value)} />
              )}
            </label>
          ))}
          <button type="submit" disabled={pending}>Submit answers</button>
        </form>
      ) : null}
    </aside>
  );
}

function ServiceBadge({ status }: { status: ForgeServiceStatus }) {
  return (
    <div className={`service-badge status-${status.state}`} role="status" aria-live="polite">
      <span className="status-dot" aria-hidden="true" />
      <span><strong>{status.state}</strong><small>{status.message}</small></span>
    </div>
  );
}

type NamedResponse<M extends string, R> = {
  kind: "response";
  method: M;
  result: R;
};

function responseResult<M extends string, R>(
  result: StudioClientResult<NamedResponse<M, R> | StudioErrorEnvelope>,
  method: M,
): R {
  if (!result.ok) throw new Error(boundedMessage(result.error.message));
  if (result.value.kind === "error") throw new Error(boundedMessage(result.value.error.message));
  if (result.value.method !== method) throw new Error(`Forge Studio returned an invalid ${method} response.`);
  return result.value.result;
}

function executeGameJobRequest(
  workspaceId: string,
  request: GameJobRequest,
): Promise<StudioClientResult<StudioJobCreateReply>> {
  if (request.operation === "assetpack.verify") {
    return window.forgeStudio.verifyAssetpack(workspaceId, request.input);
  }
  if (request.operation === "runtime.headless") {
    return window.forgeStudio.runHeadless(workspaceId, request.input);
  }
  return window.forgeStudio.runReplay(workspaceId, request.input);
}

function gameOperations(): readonly GameOperation[] {
  return ["assetpack.verify", "runtime.headless", "runtime.replay"];
}

function createGamePendingState(): GameOperationState<boolean> {
  return {
    "assetpack.verify": false,
    "runtime.headless": false,
    "runtime.replay": false,
  };
}

function createGameErrorState(): GameOperationState<string | null> {
  return {
    "assetpack.verify": null,
    "runtime.headless": null,
    "runtime.replay": null,
  };
}

function createGameRequestTokenState(): GameOperationState<number> {
  return {
    "assetpack.verify": 0,
    "runtime.headless": 0,
    "runtime.replay": 0,
  };
}

function cancelReplyMatches(
  value: unknown,
  expected: {
    jobId: string;
    workspaceId: string;
    operation: string;
    formatVersion: 1 | 2;
  },
): boolean {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return false;
  const record = value as Record<string, unknown>;
  const keys = [
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
  ];
  if (
    Object.keys(record).toSorted().join("\u0000") !== keys.join("\u0000") ||
    record.format !== "rpg-world-forge.studio_job" ||
    record.format_version !== expected.formatVersion ||
    record.job_id !== expected.jobId ||
    record.workspace_id !== expected.workspaceId ||
    record.operation !== expected.operation
  ) {
    return false;
  }
  return (
    record.state === "queued" ||
    record.state === "running" ||
    record.state === "awaiting_approval" ||
    record.state === "awaiting_user" ||
    record.state === "paused" ||
    record.state === "succeeded" ||
    record.state === "failed" ||
    record.state === "canceled" ||
    record.state === "orphaned"
  );
}

function mergeGameJobRecords(
  listed: readonly Record<string, unknown>[],
  immediate: readonly Record<string, unknown>[],
): Record<string, unknown>[] {
  const merged = [...listed];
  const positions = new Map<string, number>();
  for (const [index, record] of merged.entries()) {
    if (typeof record.job_id === "string" && !positions.has(record.job_id)) {
      positions.set(record.job_id, index);
    }
  }
  const additions: Record<string, unknown>[] = [];
  for (const record of immediate) {
    if (typeof record.job_id !== "string") {
      additions.push(record);
      continue;
    }
    const index = positions.get(record.job_id);
    if (index === undefined) {
      additions.push(record);
      positions.set(record.job_id, -(additions.length));
      continue;
    }
    const listedRecord = merged[index];
    if (
      listedRecord &&
      typeof record.updated_at === "string" &&
      (typeof listedRecord.updated_at !== "string" ||
        record.updated_at >= listedRecord.updated_at)
    ) {
      merged[index] = record;
    }
  }
  return [...additions, ...merged];
}

function preferredSource(documents: readonly SourceSummary[]): SourceSummary | null {
  return (
    documents.find((document) => document.path === "source/world.json") ??
    documents.find((document) => document.path === "source/manifest.json") ??
    documents[0] ??
    null
  );
}

function groupSources(documents: readonly SourceSummary[]): Array<[string, SourceSummary[]]> {
  const groups = new Map<string, SourceSummary[]>();
  for (const document of documents) {
    const parts = document.path.split("/");
    const group = parts.length <= 2 ? "World core" : titleCase(parts[1].replaceAll("_", " "));
    const current = groups.get(group) ?? [];
    current.push(document);
    groups.set(group, current);
  }
  return [...groups.entries()].sort(([left], [right]) => left.localeCompare(right, "en"));
}

function fileName(path: string): string {
  return path.split("/").at(-1) ?? path;
}

function titleCase(value: string): string {
  return value.length === 0 ? value : `${value[0].toUpperCase()}${value.slice(1)}`;
}

function slug(value: string): string {
  return value.toLowerCase().replace(/[^a-z0-9]+/gu, "-").replace(/^-|-$/gu, "");
}

function describeError(error: unknown): string {
  return error instanceof Error ? boundedMessage(error.message) : "Unknown Studio error";
}

function catalogClientFailure() {
  return {
    ok: false as const,
    error: {
      code: "internal_error" as const,
      message: "The local asset catalog request failed.",
    },
  };
}

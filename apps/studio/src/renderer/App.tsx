import { useEffect, useMemo, useReducer, useRef, useState } from "react";

import type {
  CodexBridgeStatus,
  CodexUserInputQuestion,
  ForgeServiceStatus,
  StudioClientResult,
  StudioErrorEnvelope,
  StudioSourceListResult,
  StudioSourceReadResult,
  StudioWorkspaceOverviewResult,
  StudioWorldAnalyzeResult,
  StudioWorldValidateResult,
} from "../shared/studio-api";
import { NeutralMapCanvas } from "./NeutralMapCanvas";
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

type DockTab = "activity" | "changesets" | "jobs";
type PendingNavigation =
  | { kind: "workspace"; workspaceId: string }
  | { kind: "source"; document: SourceSummary };

export function App() {
  const [status, setStatus] = useState<ForgeServiceStatus>(INITIAL_STATUS);
  const [workspaces, setWorkspaces] = useState<string[]>([]);
  const [registryPending, setRegistryPending] = useState(true);
  const [authoring, dispatch] = useReducer(authoringReducer, undefined, createInitialAuthoringState);
  const generationRef = useRef(0);
  const limiterRef = useRef(new RequestLimiter(4));
  const [errors, setErrors] = useState<string[]>([]);
  const [serviceActivityCount, setServiceActivityCount] = useState(0);
  const [dockTab, setDockTab] = useState<DockTab>("activity");
  const [dockEvents, setDockEvents] = useState<Record<string, unknown>[]>([]);
  const [dockChangesets, setDockChangesets] = useState<Record<string, unknown>[]>([]);
  const [dockJobs, setDockJobs] = useState<Record<string, unknown>[]>([]);
  const [dockPending, setDockPending] = useState(false);
  const [dockRefresh, setDockRefresh] = useState(0);
  const [pendingNavigation, setPendingNavigation] = useState<PendingNavigation | null>(null);
  const navigationTriggerRef = useRef<HTMLButtonElement | null>(null);
  const stayButtonRef = useRef<HTMLButtonElement>(null);
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
    dispatch({ type: "workspace-selected", workspaceId, generation });
    setDockEvents([]);
    setDockChangesets([]);
    setDockJobs([]);
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

  async function cancelJob(jobId: string): Promise<void> {
    const result = await window.forgeStudio.cancelJob(jobId);
    if (!result.ok) recordError(result.error.message);
    else if (result.value.kind === "error") recordError(result.value.error.message);
    setDockRefresh((value) => value + 1);
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
  const isMapDraft = Boolean(
    draft && draft.jsonSyntaxError === null && selectedSummary?.path.includes("/maps/"),
  );

  return (
    <div className="studio-shell">
      <a className="skip-link" href="#world-workbench">
        Skip to World workbench
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
          <nav className="discipline-nav" aria-label="Forge disciplines">
            <button type="button" className="active" aria-current="page">World</button>
            <button type="button" disabled aria-describedby="assets-coming-next">Assets</button>
            <small id="assets-coming-next">Coming next</small>
            <button type="button" disabled aria-describedby="game-coming-next">Game</button>
            <small id="game-coming-next">Coming next</small>
          </nav>
        </aside>

        <main id="world-workbench" className="world-area" tabIndex={-1}>
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
      </div>

      <BottomDock
        tab={dockTab}
        pending={dockPending}
        rows={activeDockRows}
        serviceActivityCount={serviceActivityCount}
        onTab={setDockTab}
        onCancel={(jobId) => void cancelJob(jobId)}
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
}: {
  tab: DockTab;
  pending: boolean;
  rows: DockRow[];
  serviceActivityCount: number;
  onTab: (tab: DockTab) => void;
  onCancel: (jobId: string) => void;
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
                {tab === "jobs" && row.state && ["queued", "running", "paused", "awaiting_user", "awaiting_approval"].includes(row.state) ? (
                  <button type="button" className="secondary compact" onClick={() => onCancel(row.id)}>
                    Cancel job
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

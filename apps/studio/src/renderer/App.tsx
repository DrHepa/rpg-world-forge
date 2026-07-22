import { useEffect, useMemo, useState } from "react";

import type {
  CodexActivityEvent,
  CodexBridgeStatus,
  ForgeServiceStatus,
  StudioActivityEvent,
  StudioClientResult,
  StudioReadMethod,
  StudioReplyEnvelope,
} from "../shared/studio-api";

type ReadOperation = "service.initialize" | StudioReadMethod;

const READ_OPERATIONS: readonly ReadOperation[] = [
  "service.initialize",
  "workspace.list",
  "events.list",
  "changeset.list",
  "job.list",
];

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

export function App() {
  const [status, setStatus] = useState<ForgeServiceStatus>(INITIAL_STATUS);
  const [activities, setActivities] = useState<StudioActivityEvent[]>([]);
  const [errors, setErrors] = useState<string[]>([]);
  const [method, setMethod] = useState<ReadOperation>("workspace.list");
  const [reply, setReply] = useState<StudioReplyEnvelope | null>(null);
  const [pending, setPending] = useState(false);
  const [codexStatus, setCodexStatus] = useState<CodexBridgeStatus>(INITIAL_CODEX_STATUS);
  const [codexEvents, setCodexEvents] = useState<CodexActivityEvent[]>([]);
  const [workspaceId, setWorkspaceId] = useState("");
  const [threadId, setThreadId] = useState<string | null>(null);
  const [turnText, setTurnText] = useState("");
  const [codexPending, setCodexPending] = useState(false);

  useEffect(() => {
    const unsubscribe = window.forgeStudio.onEvent((activity) => {
      setActivities((current) => [...current.slice(-99), activity]);
      if (activity.type === "service-status") {
        setStatus(activity.status);
      }
    });
    const unsubscribeCodex = window.forgeStudio.onCodexEvent((activity) => {
      setCodexEvents((current) => [...current.slice(-99), activity]);
      if (activity.type === "codex-status") setCodexStatus(activity.status);
    });
    void window.forgeStudio.getServiceStatus().then((result) => {
      if (result.ok) {
        setStatus(result.value);
      } else {
        recordError(result.error.message);
      }
    });
    void window.forgeStudio.initialize().then((result) => {
      if (result.ok) {
        setReply(result.value);
      } else {
        recordError(result.error.message);
      }
    });
    void window.forgeStudio.getCodexStatus().then((result) => {
      if (result.ok) setCodexStatus(result.value);
      else recordError(result.error.message);
    });
    return () => { unsubscribe(); unsubscribeCodex(); };
  }, []);

  const formattedReply = useMemo(
    () => (reply ? JSON.stringify(reply, null, 2) : "No service response yet."),
    [reply],
  );

  function recordError(message: string): void {
    setErrors((current) => [...current.slice(-19), message]);
  }

  async function submitRequest(event: React.FormEvent): Promise<void> {
    event.preventDefault();
    setPending(true);
    let result: StudioClientResult<StudioReplyEnvelope>;
    switch (method) {
      case "service.initialize":
        result = await window.forgeStudio.initialize();
        break;
      case "workspace.list":
        result = await window.forgeStudio.listWorkspaces();
        break;
      case "events.list":
        result = await window.forgeStudio.listEvents({ limit: 100 });
        break;
      case "changeset.list":
        result = await window.forgeStudio.listChangesets({ limit: 100 });
        break;
      case "job.list":
        result = await window.forgeStudio.listJobs({ limit: 100 });
        break;
    }
    setPending(false);
    if (result.ok) {
      setReply(result.value);
    } else {
      recordError(result.error.message);
    }
  }

  async function bindCodex(event: React.FormEvent): Promise<void> {
    event.preventDefault();
    setCodexPending(true);
    const result = await window.forgeStudio.bindCodexWorkspace(workspaceId);
    setCodexPending(false);
    if (result.ok) setCodexStatus(result.value);
    else recordError(result.error.message);
  }

  async function startThread(): Promise<void> {
    setCodexPending(true);
    const result = await window.forgeStudio.startCodexThread();
    setCodexPending(false);
    if (result.ok) setThreadId(result.value.threadId);
    else recordError(result.error.message);
  }

  async function startTurn(event: React.FormEvent): Promise<void> {
    event.preventDefault();
    if (!threadId) return;
    setCodexPending(true);
    const result = await window.forgeStudio.startCodexTurn(threadId, turnText);
    setCodexPending(false);
    if (result.ok) setTurnText("");
    else recordError(result.error.message);
  }

  return (
    <main className="workbench">
      <header className="titlebar">
        <div>
          <p className="eyebrow">Local authoring control plane</p>
          <h1>RPG World Forge Studio</h1>
        </div>
        <ServiceBadge status={status} />
      </header>

      <section className="notice" aria-label="Implementation status">
        <strong>Secure Codex bridge</strong>
        <span>
          Codex can be bound to one registered workspace and may only stage reviewable Forge
          changesets. Visual lore, asset, and game tools remain future slices.
        </span>
      </section>

      <div className="workspace-grid">
        <section className="panel projects" aria-labelledby="projects-heading">
          <div className="panel-heading">
            <h2 id="projects-heading">Projects</h2>
            <span>Workspace registry</span>
          </div>
          <p className="empty-state">
            Use <code>workspace.list</code> to inspect workspaces already registered by the Forge
            service.
          </p>
        </section>

        <section className="panel codex-panel" aria-labelledby="codex-heading">
          <div className="panel-heading">
            <h2 id="codex-heading">Codex workspace</h2>
            <span>{codexStatus.state}</span>
          </div>
          <p role="status">{codexStatus.message}</p>
          <form onSubmit={(event) => void bindCodex(event)}>
            <label>
              Registered workspace ID
              <input
                aria-label="Registered workspace ID"
                value={workspaceId}
                onChange={(event) => setWorkspaceId(event.target.value)}
                autoComplete="off"
              />
            </label>
            <div className="actions">
              <button type="submit" disabled={codexPending || workspaceId.length === 0}>
                Bind Codex
              </button>
              <button
                type="button"
                disabled={codexPending || codexStatus.state !== "ready"}
                onClick={() => void startThread()}
              >
                New thread
              </button>
            </div>
          </form>
          <form onSubmit={(event) => void startTurn(event)}>
            <label>
              Turn message
              <textarea
                aria-label="Turn message"
                value={turnText}
                onChange={(event) => setTurnText(event.target.value)}
                disabled={!threadId}
              />
            </label>
            <div className="actions">
              <button type="submit" disabled={codexPending || !threadId || turnText.length === 0}>
                Send turn
              </button>
            </div>
          </form>
          <small>{threadId ? `Thread ${threadId}` : "No active thread"}</small>
        </section>

        <section className="panel request-panel" aria-labelledby="request-heading">
          <div className="panel-heading">
            <h2 id="request-heading">Service inspection</h2>
            <span>Named read-only operations</span>
          </div>
          <form onSubmit={(event) => void submitRequest(event)}>
            <label>
              Method
              <select
                value={method}
                onChange={(event) => setMethod(event.target.value as ReadOperation)}
              >
                {READ_OPERATIONS.map((candidate) => (
                  <option key={candidate} value={candidate}>
                    {candidate}
                  </option>
                ))}
              </select>
            </label>
            <div className="actions">
              <button type="submit" disabled={pending}>
                {pending ? "Waiting…" : "Run operation"}
              </button>
            </div>
          </form>
        </section>

        <section className="panel response-panel" aria-labelledby="response-heading">
          <div className="panel-heading">
            <h2 id="response-heading">Response</h2>
            <span>Correlated NDJSON</span>
          </div>
          <pre>{formattedReply}</pre>
        </section>
      </div>

      <div className="bottom-grid">
        <section className="panel" aria-labelledby="activity-heading">
          <div className="panel-heading">
            <h2 id="activity-heading">Activity</h2>
            <span>{activities.length} events</span>
          </div>
          <ol className="event-list">
            {activities.length === 0 ? (
              <li className="empty-state">No service activity received.</li>
            ) : (
              activities.map((activity, index) => (
                <li key={`${activity.type}-${String(index)}`}>{describeActivity(activity)}</li>
              ))
            )}
          </ol>
          <p>{codexEvents.length} Codex events</p>
        </section>

        <section className="panel errors" aria-labelledby="errors-heading">
          <div className="panel-heading">
            <h2 id="errors-heading">Errors</h2>
            <span>{errors.length}</span>
          </div>
          <ol className="event-list" aria-live="polite">
            {errors.length === 0 ? (
              <li className="empty-state">No errors.</li>
            ) : (
              errors.map((error, index) => <li key={`${error}-${String(index)}`}>{error}</li>)
            )}
          </ol>
        </section>
      </div>
    </main>
  );
}

function ServiceBadge({ status }: { status: ForgeServiceStatus }) {
  return (
    <div className={`service-badge status-${status.state}`} role="status" aria-live="polite">
      <span className="status-dot" aria-hidden="true" />
      <span>
        <strong>{status.state}</strong>
        <small>{status.message}</small>
      </span>
    </div>
  );
}

function describeActivity(activity: StudioActivityEvent): string {
  if (activity.type === "service-status") {
    return `Service ${activity.status.state}: ${activity.status.message}`;
  }
  if (activity.type === "service-stderr") {
    return `Service stderr: ${activity.text}`;
  }
  const eventType = activity.envelope.event.type;
  return typeof eventType === "string" ? `Studio event: ${eventType}` : "Studio event received";
}

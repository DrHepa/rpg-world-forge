import { useEffect, useMemo, useState } from "react";

import type {
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

export function App() {
  const [status, setStatus] = useState<ForgeServiceStatus>(INITIAL_STATUS);
  const [activities, setActivities] = useState<StudioActivityEvent[]>([]);
  const [errors, setErrors] = useState<string[]>([]);
  const [method, setMethod] = useState<ReadOperation>("workspace.list");
  const [reply, setReply] = useState<StudioReplyEnvelope | null>(null);
  const [pending, setPending] = useState(false);

  useEffect(() => {
    const unsubscribe = window.forgeStudio.onEvent((activity) => {
      setActivities((current) => [...current.slice(-99), activity]);
      if (activity.type === "service-status") {
        setStatus(activity.status);
      }
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
    return unsubscribe;
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
        <strong>Foundation shell</strong>
        <span>
          This build exposes the real provider-free Forge service. Visual lore, asset, and game
          tools are not implemented yet.
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

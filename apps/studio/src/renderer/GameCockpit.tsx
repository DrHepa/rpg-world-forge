import { useMemo, useState } from "react";

import type {
  StudioAssetpackVerifyInput,
  StudioRuntimeHeadlessInput,
  StudioRuntimeReplayInput,
} from "../shared/studio-api";
import {
  isPortableGamePath,
  projectGameJobs,
  type GameJobRequest,
  type GameJobView,
  type GameOperation,
} from "./game-job-view";

type GamePendingState = Record<GameOperation, boolean>;
type GameErrorState = Record<GameOperation, string | null>;

export function GameCockpit({
  workspaceId,
  repositories,
  records,
  events,
  pending,
  errors,
  cancelingJobIds,
  onSubmit,
  onCancel,
}: {
  workspaceId: string | null;
  repositories: {
    gameRegistered: boolean;
    bundleRegistered: boolean;
  };
  records: readonly Record<string, unknown>[];
  events: readonly Record<string, unknown>[];
  pending: GamePendingState;
  errors: GameErrorState;
  cancelingJobIds: ReadonlySet<string>;
  onSubmit: (request: GameJobRequest) => void;
  onCancel: (job: GameJobView) => void;
}) {
  const [assetpack, setAssetpack] = useState("");
  const [assetpackWorldpack, setAssetpackWorldpack] = useState("");
  const [headlessWorldpack, setHeadlessWorldpack] = useState("");
  const [ticks, setTicks] = useState("0");
  const [replayWorldpack, setReplayWorldpack] = useState("");
  const [replay, setReplay] = useState("");
  const [formErrors, setFormErrors] = useState<GameErrorState>(emptyErrors);
  const jobs = useMemo(
    () => projectGameJobs(records, events, workspaceId),
    [events, records, workspaceId],
  );

  function submitAssetpack(event: React.FormEvent): void {
    event.preventDefault();
    const input: StudioAssetpackVerifyInput = {
      assetpack,
      worldpack: assetpackWorldpack,
    };
    if (!isPortableGamePath(input.assetpack) || !isPortableGamePath(input.worldpack)) {
      setFormError(
        "assetpack.verify",
        "Enter portable workspace-relative assetpack and worldpack paths.",
      );
      return;
    }
    setFormError("assetpack.verify", null);
    onSubmit({ operation: "assetpack.verify", input });
  }

  function submitHeadless(event: React.FormEvent): void {
    event.preventDefault();
    const parsedTicks = parseTicks(ticks);
    const input: StudioRuntimeHeadlessInput = {
      worldpack: headlessWorldpack,
      ticks: parsedTicks ?? -1,
    };
    if (!isPortableGamePath(input.worldpack) || parsedTicks === null) {
      setFormError(
        "runtime.headless",
        "Enter a portable workspace-relative worldpack path and an integer from 0 to 1,000,000.",
      );
      return;
    }
    setFormError("runtime.headless", null);
    onSubmit({ operation: "runtime.headless", input: { ...input, ticks: parsedTicks } });
  }

  function submitReplay(event: React.FormEvent): void {
    event.preventDefault();
    const input: StudioRuntimeReplayInput = {
      worldpack: replayWorldpack,
      replay,
    };
    if (!isPortableGamePath(input.worldpack) || !isPortableGamePath(input.replay)) {
      setFormError(
        "runtime.replay",
        "Enter portable workspace-relative worldpack and existing replay paths.",
      );
      return;
    }
    setFormError("runtime.replay", null);
    onSubmit({ operation: "runtime.replay", input });
  }

  function setFormError(operation: GameOperation, message: string | null): void {
    setFormErrors((current) => ({ ...current, [operation]: message }));
  }

  if (!workspaceId) {
    return (
      <section className="game-cockpit" aria-labelledby="game-cockpit-heading">
        <header className="game-header">
          <div>
            <p className="breadcrumb">Game / Deterministic verification cockpit</p>
            <h2 id="game-cockpit-heading">Reference runtime checks</h2>
          </div>
        </header>
        <div className="game-empty">
          <h3>No workspace selected</h3>
          <p>Choose a registered workspace to run fixed offline verification jobs.</p>
        </div>
      </section>
    );
  }

  return (
    <section className="game-cockpit" aria-labelledby="game-cockpit-heading">
      <header className="game-header">
        <div>
          <p className="breadcrumb">Game / Deterministic verification cockpit</p>
          <h2 id="game-cockpit-heading">Reference runtime checks</h2>
          <p>
            Fixed local jobs verify an engine-neutral handoff, simulate the reference
            runtime without graphics, or verify an existing replay. This is not a playable
            game preview.
          </p>
        </div>
        <dl className="game-registration" aria-label="Registered external artifacts">
          <div>
            <dt>Game repository</dt>
            <dd>{repositories.gameRegistered ? "Registered" : "Not registered"}</dd>
          </div>
          <div>
            <dt>Bundle repository</dt>
            <dd>{repositories.bundleRegistered ? "Registered" : "Not registered"}</dd>
          </div>
        </dl>
      </header>

      <div className="game-operation-grid">
        <form aria-labelledby="assetpack-job-heading" onSubmit={submitAssetpack}>
          <div>
            <p className="eyebrow">Metadata and handoff only</p>
            <h3 id="assetpack-job-heading">Verify assetpack</h3>
          </div>
          <p>
            Validates the existing assetpack against its worldpack. It does not render or
            modify either artifact.
          </p>
          <label>
            Assetpack path
            <input
              autoComplete="off"
              spellCheck={false}
              value={assetpack}
              onChange={(event) => setAssetpack(event.target.value)}
              placeholder="build/assets/assetpack.json"
            />
          </label>
          <label>
            Worldpack path for assetpack verification
            <input
              autoComplete="off"
              spellCheck={false}
              value={assetpackWorldpack}
              onChange={(event) => setAssetpackWorldpack(event.target.value)}
              placeholder="build/worldpack.json"
            />
          </label>
          <FormMessage
            validation={formErrors["assetpack.verify"]}
            submission={errors["assetpack.verify"]}
          />
          {pending["assetpack.verify"] ? (
            <p className="game-form-status" role="status" aria-live="polite">
              Queuing assetpack verification.
            </p>
          ) : null}
          <button
            type="submit"
            disabled={pending["assetpack.verify"] || !assetpack || !assetpackWorldpack}
          >
            {pending["assetpack.verify"] ? "Queuing verification…" : "Verify assetpack"}
          </button>
        </form>

        <form aria-labelledby="headless-job-heading" onSubmit={submitHeadless}>
          <div>
            <p className="eyebrow">Reference runtime · no graphics</p>
            <h3 id="headless-job-heading">Run headless simulation</h3>
          </div>
          <p>
            Advances the reference worldpack runtime by an exact tick count without opening
            a window or invoking a game renderer.
          </p>
          <label>
            Worldpack path for headless simulation
            <input
              autoComplete="off"
              spellCheck={false}
              value={headlessWorldpack}
              onChange={(event) => setHeadlessWorldpack(event.target.value)}
              placeholder="build/worldpack.json"
            />
          </label>
          <label>
            Headless ticks
            <input
              type="number"
              min="0"
              max="1000000"
              step="1"
              inputMode="numeric"
              value={ticks}
              onChange={(event) => setTicks(event.target.value)}
            />
          </label>
          <FormMessage
            validation={formErrors["runtime.headless"]}
            submission={errors["runtime.headless"]}
          />
          {pending["runtime.headless"] ? (
            <p className="game-form-status" role="status" aria-live="polite">
              Queuing headless simulation.
            </p>
          ) : null}
          <button
            type="submit"
            disabled={
              pending["runtime.headless"] ||
              !headlessWorldpack ||
              parseTicks(ticks) === null
            }
          >
            {pending["runtime.headless"] ? "Queuing simulation…" : "Run headless simulation"}
          </button>
        </form>

        <form aria-labelledby="replay-job-heading" onSubmit={submitReplay}>
          <div>
            <p className="eyebrow">Existing action log only</p>
            <h3 id="replay-job-heading">Verify replay</h3>
          </div>
          <p>
            Verifies an existing replay action log against the reference runtime. It does
            not record a replay and does not use generated-game replay slots.
          </p>
          <label>
            Worldpack path for replay verification
            <input
              autoComplete="off"
              spellCheck={false}
              value={replayWorldpack}
              onChange={(event) => setReplayWorldpack(event.target.value)}
              placeholder="build/worldpack.json"
            />
          </label>
          <label>
            Existing replay path
            <input
              autoComplete="off"
              spellCheck={false}
              value={replay}
              onChange={(event) => setReplay(event.target.value)}
              placeholder="replays/accepted.json"
            />
          </label>
          <FormMessage
            validation={formErrors["runtime.replay"]}
            submission={errors["runtime.replay"]}
          />
          {pending["runtime.replay"] ? (
            <p className="game-form-status" role="status" aria-live="polite">
              Queuing replay verification.
            </p>
          ) : null}
          <button
            type="submit"
            disabled={pending["runtime.replay"] || !replayWorldpack || !replay}
          >
            {pending["runtime.replay"] ? "Queuing replay…" : "Verify existing replay"}
          </button>
        </form>
      </div>

      <section className="game-results" aria-labelledby="game-results-heading">
        <div className="section-heading">
          <div>
            <p className="eyebrow">Current workspace only</p>
            <h2 id="game-results-heading">Deterministic job results</h2>
          </div>
          <span role="status" aria-live="polite">
            {String(jobs.length)} valid Game jobs
          </span>
        </div>
        <p className="bounded-note">
          This view is bounded and is not a chronological job history.
        </p>
        {jobs.length === 0 ? (
          <p className="empty-state">
            No valid Game verification jobs are present in the bounded workspace view.
          </p>
        ) : (
          <ol className="game-job-list">
            {jobs.map((job) => (
              <li key={job.jobId}>
                <article aria-label={`${job.operationLabel} job ${job.jobId}`}>
                  <header>
                    <div>
                      <h3>{job.operationLabel}</h3>
                      <code>{job.jobId}</code>
                    </div>
                    <span className={`state state-${job.state}`}>{job.stateLabel}</span>
                  </header>
                  <dl className="game-job-timestamps">
                    <div>
                      <dt>Created</dt>
                      <dd>{job.createdAt}</dd>
                    </div>
                    <div>
                      <dt>Updated</dt>
                      <dd>{job.updatedAt}</dd>
                    </div>
                  </dl>
                  <JobFacts heading="Inputs" facts={job.inputFacts} />
                  {job.resultFacts ? (
                    <JobFacts heading="Verified result" facts={job.resultFacts} />
                  ) : null}
                  {job.error ? (
                    <div className="game-job-error" role="alert">
                      <strong>{job.error.code}</strong>
                      <p>{job.error.message}</p>
                    </div>
                  ) : null}
                  {job.state === "running" && job.progress !== null ? (
                    <label className="game-job-progress">
                      {`Observed progress ${String(job.progress)}%`}
                      <progress max="100" value={job.progress}>
                        {`${String(job.progress)}%`}
                      </progress>
                    </label>
                  ) : job.state === "running" ? (
                    <p className="game-job-running-status" role="status">
                      Running; no associated progress percentage has been observed.
                    </p>
                  ) : null}
                  {job.canCancel ? (
                    <button
                      type="button"
                      className="secondary compact"
                      disabled={cancelingJobIds.has(job.jobId)}
                      aria-label={`Cancel ${job.operationLabel} job ${job.jobId}`}
                      onClick={() => onCancel(job)}
                    >
                      {cancelingJobIds.has(job.jobId) ? "Canceling…" : "Cancel job"}
                    </button>
                  ) : null}
                </article>
              </li>
            ))}
          </ol>
        )}
      </section>
    </section>
  );
}

function JobFacts({
  heading,
  facts,
}: {
  heading: string;
  facts: GameJobView["inputFacts"];
}) {
  return (
    <section className="game-job-facts" aria-label={heading}>
      <h4>{heading}</h4>
      <dl>
        {facts.map((fact) => (
          <div key={fact.label}>
            <dt>{fact.label}</dt>
            <dd>
              {fact.kind === "hash" || fact.kind === "path" ? (
                <code>{fact.value}</code>
              ) : (
                fact.value
              )}
            </dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

function FormMessage({
  validation,
  submission,
}: {
  validation: string | null;
  submission: string | null;
}) {
  const message = validation ?? submission;
  return message ? <p className="game-form-error" role="alert">{message}</p> : null;
}

function emptyErrors(): GameErrorState {
  return {
    "assetpack.verify": null,
    "runtime.headless": null,
    "runtime.replay": null,
  };
}

function parseTicks(value: string): number | null {
  if (!/^(?:0|[1-9][0-9]{0,6})$/u.test(value)) return null;
  const parsed = Number(value);
  return Number.isSafeInteger(parsed) && parsed <= 1_000_000 ? parsed : null;
}

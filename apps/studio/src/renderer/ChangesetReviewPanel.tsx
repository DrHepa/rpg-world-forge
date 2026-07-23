import type { KeyboardEvent, RefObject } from "react";

import type { StudioChangeset, StudioChangesetDiff } from "../shared/studio-api";
import { boundedMessage } from "./authoring-state";
import {
  reviewActionUnavailableReason,
  type ChangesetReviewAction,
  type ChangesetReviewState,
} from "./changeset-review-state";

const MAX_OPERATIONS = 8;
const MAX_HUNKS_PER_OPERATION = 6;
const MAX_LINES_PER_HUNK = 50;
const MAX_JSON_CHANGES_PER_OPERATION = 64;

interface ChangesetReviewPanelProps {
  state: ChangesetReviewState;
  closeButtonRef: RefObject<HTMLButtonElement | null>;
  pendingStatusRef: RefObject<HTMLParagraphElement | null>;
  obscured: boolean;
  onClose: () => void;
  onRequestAction: (action: ChangesetReviewAction, trigger: HTMLButtonElement) => void;
}

export function ChangesetReviewPanel({
  state,
  closeButtonRef,
  pendingStatusRef,
  obscured,
  onClose,
  onRequestAction,
}: ChangesetReviewPanelProps) {
  const record = state.record;
  const diff = state.diff;
  const actionPending =
    state.pending === "approve" || state.pending === "reject" || state.pending === "apply";
  return (
    <div className="review-backdrop">
      <section
        className="changeset-review-panel"
        role="dialog"
        aria-modal={obscured ? undefined : true}
        aria-hidden={obscured ? true : undefined}
        inert={obscured}
        aria-labelledby="changeset-review-heading"
        aria-describedby="changeset-review-summary"
        aria-busy={state.pending !== null}
        onKeyDown={(event) => containDialogFocus(event, actionPending ? undefined : onClose)}
      >
        <header className="changeset-review-heading">
          <div>
            <p className="eyebrow">Human source review</p>
            <h2 id="changeset-review-heading">Changeset review</h2>
          </div>
          <button
            ref={closeButtonRef}
            type="button"
            className="icon-button"
            aria-label="Close changeset review"
            disabled={actionPending}
            onClick={onClose}
          >
            ×
          </button>
        </header>

        <p id="changeset-review-summary" className="review-summary">
          Review immutable source evidence before recording any decision. Approval never applies
          files automatically.
        </p>
        {state.pending === "open" ? <p role="status">Loading immutable review evidence…</p> : null}
        {actionPending ? (
          <p
            ref={pendingStatusRef}
            className="review-pending-status"
            role="status"
            tabIndex={0}
          >
            {pendingActionMessage(state.pending)}
          </p>
        ) : null}
        {state.error ? <p className="inline-error" role="alert">{state.error}</p> : null}
        {state.notice ? <p className="review-notice" role="status">{state.notice}</p> : null}

        {record && diff ? (
          <>
            <ReviewIdentity record={record} diff={diff} />
            {diff.available ? (
              <ExactDiff diff={diff} />
            ) : (
              <section className="legacy-review-warning" aria-labelledby="legacy-review-heading">
                <h3 id="legacy-review-heading">Exact diff unavailable</h3>
                <p>
                  This legacy v1 changeset did not retain immutable base bytes. Fresh approval and
                  apply are disabled. Rejection remains a separate confirmed human decision.
                </p>
              </section>
            )}
            <ReviewActions
              record={record}
              diff={diff}
              pending={state.pending}
              onRequestAction={onRequestAction}
            />
          </>
        ) : state.pending !== "open" ? (
          <p className="empty-state">No verified changeset evidence is available.</p>
        ) : null}
      </section>
    </div>
  );
}

function ReviewIdentity({ record, diff }: { record: StudioChangeset; diff: StudioChangesetDiff }) {
  return (
    <section className="review-identity" aria-labelledby="review-identity-heading">
      <h3 id="review-identity-heading">Review identity</h3>
      <dl>
        <div><dt>Changeset</dt><dd><code>{record.changeset_id}</code></dd></div>
        <div><dt>Workspace</dt><dd><code>{record.workspace_id}</code></dd></div>
        <div><dt>Status</dt><dd><span className={`state state-${record.status}`}>{record.status}</span></dd></div>
        <div><dt>Format</dt><dd>v{record.format_version}</dd></div>
        <div>
          <dt>Review SHA-256</dt>
          <dd><code>{diff.review_sha256 ?? "Unavailable for legacy v1"}</code></dd>
        </div>
      </dl>
    </section>
  );
}

function ExactDiff({ diff }: { diff: Extract<StudioChangesetDiff, { available: true }> }) {
  const operations = diff.operations.slice(0, MAX_OPERATIONS);
  return (
    <section className="exact-diff" aria-labelledby="exact-diff-heading">
      <div className="review-section-heading">
        <h3 id="exact-diff-heading">Exact source diff</h3>
        <span>{diff.operations.length} operations</span>
      </div>
      <ol className="review-operation-list">
        {operations.map((operation, index) => (
          <li key={`${operation.path}-${String(index)}`}>
            <article className="review-operation" aria-labelledby={`review-operation-${String(index)}`}>
              <header>
                <div>
                  <span className="operation-kind">{operation.operation}</span>
                  <h4 id={`review-operation-${String(index)}`}>{operation.path}</h4>
                </div>
                <span>{operation.base_size.toLocaleString("en-US")} → {operation.size.toLocaleString("en-US")} bytes</span>
              </header>
              <dl className="operation-anchors">
                <div><dt>Base SHA-256</dt><dd><code>{operation.base_sha256 ?? "New file"}</code></dd></div>
                <div><dt>Proposed SHA-256</dt><dd><code>{operation.proposed_sha256 ?? "Deleted"}</code></dd></div>
              </dl>
              <TextHunks operation={operation} />
              <JsonPointerChanges operation={operation} />
            </article>
          </li>
        ))}
      </ol>
      {diff.operations.length > operations.length ? (
        <p className="bounded-note">
          Showing the first {operations.length} operations of this bounded review.
        </p>
      ) : null}
    </section>
  );
}

function TextHunks({
  operation,
}: {
  operation: Extract<StudioChangesetDiff, { available: true }>['operations'][number];
}) {
  const hunks = operation.text_hunks.slice(0, MAX_HUNKS_PER_OPERATION);
  if (hunks.length === 0) return <p className="empty-state">No textual lines in this operation.</p>;
  return (
    <section className="text-diff" aria-label={`Text changes for ${operation.path}`}>
      {hunks.map((hunk, index) => {
        const lines = hunk.lines.slice(0, MAX_LINES_PER_HUNK);
        return (
          <div className="diff-hunk" key={`${String(hunk.base_start)}-${String(hunk.proposed_start)}-${String(index)}`}>
            <h5>
              @@ -{hunk.base_start},{hunk.base_count} +{hunk.proposed_start},{hunk.proposed_count} @@
            </h5>
            <ol>
              {lines.map((line, lineIndex) => (
                <li className={`diff-line diff-${line.kind}`} key={lineIndex}>
                  <span className="diff-prefix" aria-label={line.kind}>{diffPrefix(line.kind)}</span>
                  <code>{boundedCodeText(line.text, 400)}</code>
                </li>
              ))}
            </ol>
            {hunk.lines.length > lines.length ? (
              <p className="bounded-note">Additional lines are omitted from this bounded hunk.</p>
            ) : null}
          </div>
        );
      })}
      {operation.text_hunks.length > hunks.length ? (
        <p className="bounded-note">Additional text hunks are omitted from this bounded view.</p>
      ) : null}
    </section>
  );
}

function JsonPointerChanges({
  operation,
}: {
  operation: Extract<StudioChangesetDiff, { available: true }>['operations'][number];
}) {
  if (operation.json_pointer_changes === null) return null;
  const changes = operation.json_pointer_changes.slice(0, MAX_JSON_CHANGES_PER_OPERATION);
  return (
    <section className="semantic-diff" aria-label={`JSON semantic changes for ${operation.path}`}>
      <h5>JSON Pointer changes</h5>
      {changes.length === 0 ? (
        <p className="empty-state">No semantic JSON changes.</p>
      ) : (
        <ol>
          {changes.map((change, index) => (
            <li key={`${change.pointer}-${String(index)}`}>
              <div>
                <strong>{change.operation}</strong>
                <code>{boundedCodeText(change.pointer || "/", 320)}</code>
              </div>
              {change.operation === "remove" || change.operation === "replace" ? (
                <p><span>Previous</span><code>{semanticValue(change.old_value)}</code></p>
              ) : null}
              {change.operation === "add" || change.operation === "replace" ? (
                <p><span>Proposed</span><code>{semanticValue(change.value)}</code></p>
              ) : null}
            </li>
          ))}
        </ol>
      )}
      {operation.json_pointer_changes.length > changes.length ? (
        <p className="bounded-note">Additional JSON Pointer changes are omitted.</p>
      ) : null}
    </section>
  );
}

function ReviewActions({
  record,
  diff,
  pending,
  onRequestAction,
}: {
  record: StudioChangeset;
  diff: StudioChangesetDiff;
  pending: ChangesetReviewState["pending"];
  onRequestAction: (action: ChangesetReviewAction, trigger: HTMLButtonElement) => void;
}) {
  const actions: ChangesetReviewAction[] = [];
  if (record.format_version === 2 && record.status === "staged") actions.push("approve");
  if (["staged", "approved"].includes(record.status)) actions.push("reject");
  if (record.format_version === 2 && record.status === "approved") actions.push("apply");
  return (
    <footer className="review-actions">
      <p>
        {record.status === "approved"
          ? "Approved for review only. Applying still requires a separate confirmation."
          : "No review action writes source files unless an approved v2 changeset is separately applied."}
      </p>
      <div className="actions">
        {actions.map((action) => (
          <button
            key={action}
            type="button"
            className={action === "reject" || action === "apply" ? "danger" : undefined}
            disabled={pending !== null || reviewActionUnavailableReason(record, diff, action) !== null}
            onClick={(event) => onRequestAction(action, event.currentTarget)}
          >
            {actionLabel(action)}
          </button>
        ))}
      </div>
    </footer>
  );
}

function actionLabel(action: ChangesetReviewAction): string {
  if (action === "approve") return "Approve review";
  if (action === "reject") return "Reject changeset";
  return "Apply approved changeset";
}

function pendingActionMessage(action: ChangesetReviewState["pending"]): string {
  if (action === "approve") return "Approval request pending. Source files remain unchanged.";
  if (action === "reject") return "Rejection request pending. Source files remain unchanged.";
  return "Apply request pending. Waiting for the verified source write to complete.";
}

function diffPrefix(kind: "context" | "remove" | "add"): string {
  if (kind === "add") return "+";
  if (kind === "remove") return "−";
  return " ";
}

function boundedCodeText(value: string, limit: number): string {
  const safe = Array.from(value, (character) => {
    const code = character.codePointAt(0) ?? 0;
    return (code < 32 && character !== "\t") || (code >= 127 && code <= 159)
      ? "�"
      : character;
  });
  return safe.length <= limit ? safe.join("") : `${safe.slice(0, limit - 1).join("")}…`;
}

function semanticValue(value: unknown): string {
  try {
    const encoded = JSON.stringify(value);
    return boundedCodeText(encoded ?? "null", 480);
  } catch {
    return boundedMessage("Unrenderable semantic value", 80);
  }
}

function containDialogFocus(
  event: KeyboardEvent<HTMLElement>,
  close: (() => void) | undefined,
): void {
  if (event.key === "Escape" && close) {
    event.preventDefault();
    close();
    return;
  }
  if (event.key !== "Tab") return;
  const focusable = Array.from(
    event.currentTarget.querySelectorAll<HTMLElement>(
      'button:not(:disabled), [href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"])',
    ),
  );
  if (focusable.length === 0) return;
  const first = focusable[0];
  const last = focusable.at(-1)!;
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
}

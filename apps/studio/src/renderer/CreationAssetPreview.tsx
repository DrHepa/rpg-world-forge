import { useMemo, useState } from "react";

import type { ForgeStudioApi } from "../shared/studio-api";
import type { CreationPreviewCandidate } from "./creation-preview-state";
import { useCreationPreview } from "./useCreationPreview";

export function CreationAssetPreview({
  api,
  authorityKey,
  items,
  catalogPending = false,
  catalogError = null,
}: {
  api: ForgeStudioApi;
  authorityKey: string;
  items: readonly CreationPreviewCandidate[];
  catalogPending?: boolean;
  catalogError?: string | null;
}) {
  const eligible = useMemo(() => items.filter((item) => item.eligible), [items]);
  const unsupported = useMemo(() => items.filter((item) => !item.eligible), [items]);
  const [selection, setSelection] = useState(() => ({
    authorityKey,
    itemKey: eligible[0]?.key ?? "",
  }));
  const selected =
    (selection.authorityKey === authorityKey
      ? eligible.find((item) => item.key === selection.itemKey)
      : null) ??
    eligible[0] ??
    null;
  const preview = useCreationPreview({ api, candidate: selected });

  const busy = catalogPending || preview.busy;
  const descriptor = preview.descriptor;

  return (
    <section
      className="creation-card creation-evidence-wide creation-asset-preview"
      role="region"
      aria-label="Sealed asset previews"
      aria-busy={busy}
    >
      <p className="eyebrow">Verified pathless media lease</p>
      <h4>Sealed asset previews</h4>
      <p>
        Only asset IDs derived from the exact selection, redistribution license, passed QA,
        committed seal job, and published asset-pack authority can be opened. The portable
        runtime path stays bound and revalidated inside the service; it is never selected by
        the renderer.
      </p>

      {catalogPending ? (
        <p role="status" aria-live="polite">Inspecting sealed preview lineage…</p>
      ) : null}
      {catalogError ? <p role="alert" className="inline-error">{catalogError}</p> : null}

      {!catalogPending && !catalogError && eligible.length > 0 ? (
        <div className="creation-preview-controls">
          <label htmlFor="creation-sealed-preview-selection">Verified sealed asset</label>
          <select
            id="creation-sealed-preview-selection"
            value={selected?.key ?? ""}
            disabled={preview.busy}
            onChange={(event) =>
              setSelection({ authorityKey, itemKey: event.target.value })
            }
          >
            {eligible.map((item) => (
              <option key={item.key} value={item.key}>
                {item.assetId} — {formatLabel(item.mediaType)}
              </option>
            ))}
          </select>
          {selected ? (
            <dl className="creation-facts" aria-label={`${selected.assetId} preview authority`}>
              <Fact label="Asset ID" value={selected.assetId} />
              <Fact label="Selected role" value={selected.selectedOutput.role} />
              <Fact label="Media type" value={selected.mediaType} />
              <Fact label="Asset pack" value={selected.assetpackId} />
              <Fact label="Grant generation" value={selected.outputGrantGeneration} />
              <Fact label="Runtime path" value="Service-bound and pathless" />
            </dl>
          ) : null}

          {preview.lifecycle === "idle" ? (
            <button type="button" disabled={!preview.canOpen} onClick={preview.open}>
              Open verified preview
            </button>
          ) : null}
          {preview.canClose ? (
            <button type="button" className="secondary" onClick={preview.close}>
              Close verified preview
            </button>
          ) : null}
          {preview.canRetry ? (
            <button type="button" className="secondary" onClick={preview.retry}>
              Retry verified preview
            </button>
          ) : null}
        </div>
      ) : null}

      {!catalogPending && !catalogError && eligible.length === 0 ? (
        <p>No current sealed PNG or WAV asset is eligible for preview.</p>
      ) : null}

      {unsupported.length > 0 ? (
        <section aria-labelledby="creation-preview-unsupported-heading">
          <h5 id="creation-preview-unsupported-heading">Unsupported sealed assets</h5>
          <ul className="creation-preview-unsupported-list">
            {unsupported.map((item) => (
              <li key={item.key}>
                <strong>{item.assetId}</strong>
                <span>{item.unsupportedReason ?? "This selected output cannot be previewed."}</span>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {preview.busy ? (
        <div className="creation-preview-progress">
          <p role="status" aria-live="polite">
            {preview.lifecycle === "opening"
              ? "Opening verified preview…"
              : preview.lifecycle === "reading"
                ? `Reading verified preview… ${String(preview.loadedBytes)} of ${String(preview.declaredBytes)} bytes`
                : "Closing verified preview lease…"}
          </p>
          {preview.declaredBytes > 0 ? (
            <progress
              aria-label="Verified preview byte progress"
              max={preview.declaredBytes}
              value={preview.loadedBytes}
            />
          ) : null}
        </div>
      ) : null}

      {preview.error ? <p role="alert" className="inline-error">{preview.error}</p> : null}

      {preview.lifecycle === "ready" && descriptor && preview.objectUrl ? (
        <div className="creation-preview-media">
          <p role="status" aria-live="polite">Verified preview ready.</p>
          {descriptor.metadata.kind === "png" ? (
            <img
              src={preview.objectUrl}
              width={descriptor.metadata.width}
              height={descriptor.metadata.height}
              alt={`Verified PNG preview for ${selected?.assetId ?? "sealed asset"}, ${String(descriptor.metadata.width)} by ${String(descriptor.metadata.height)} pixels`}
            />
          ) : (
            <audio
              src={preview.objectUrl}
              controls
              preload="metadata"
              aria-label={`Verified WAV preview for ${selected?.assetId ?? "sealed asset"}`}
            />
          )}
        </div>
      ) : null}
    </section>
  );
}

function Fact({ label, value }: { label: string; value: string | number }) {
  return <div><dt>{label}</dt><dd>{value}</dd></div>;
}

function formatLabel(mediaType: string): string {
  if (mediaType === "image/png") return "PNG";
  if (mediaType === "audio/wav") return "WAV";
  return mediaType;
}

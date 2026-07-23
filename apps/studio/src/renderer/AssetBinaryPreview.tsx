import type { StudioAssetCatalogEntry } from "../shared/studio-api";
import type {
  AssetBinaryPreviewContext,
  AssetBinaryPreviewInspection,
} from "./asset-binary-preview-state";
import { useAssetBinaryPreview } from "./useAssetBinaryPreview";

export function AssetBinaryPreview({
  context,
  entry,
  inspection,
}: {
  context: AssetBinaryPreviewContext | undefined;
  entry: StudioAssetCatalogEntry;
  inspection: AssetBinaryPreviewInspection;
}) {
  const {
    lifecycle,
    loadedBytes,
    declaredBytes,
    error,
    retryVisible,
    eligible,
    retry,
    bindMediaElement,
  } = useAssetBinaryPreview({ context, entry, inspection });
  const busy =
    lifecycle === "opening" ||
    lifecycle === "reading" ||
    lifecycle === "closing";
  const label = entry.asset_id ?? "selected asset";

  return (
    <section
      className="asset-binary-preview"
      aria-label={`Verified ${inspection.kind.toUpperCase()} preview`}
      aria-busy={busy}
    >
      {!eligible || lifecycle === "idle" ? (
        <p className="asset-preview-unavailable">
          {inspection.kind === "png"
            ? "Image preview is unavailable for this asset category or media type."
            : "Audio playback is unavailable for this asset category or media type."}
        </p>
      ) : null}

      {busy ? (
        <p className="asset-preview-status" role="status" aria-live="polite">
          {lifecycle === "opening"
            ? "Opening verified preview…"
            : lifecycle === "reading"
              ? `Reading verified preview… ${loadedBytes.toLocaleString("en-US")} of ${declaredBytes.toLocaleString("en-US")} bytes`
              : "Closing verified preview lease…"}
        </p>
      ) : null}

      {lifecycle === "error" && error ? (
        <p className="asset-preview-error" role="alert">
          {error}
        </p>
      ) : null}

      {lifecycle === "ready" ? (
        <>
          <p className="asset-preview-status" role="status" aria-live="polite">
            Verified preview ready.
          </p>
          {inspection.kind === "png" ? (
            <img
              ref={bindMediaElement}
              className="asset-png-preview"
              width={inspection.width}
              height={inspection.height}
              alt={`Verified PNG preview for ${label}, ${String(inspection.width)} by ${String(inspection.height)} pixels`}
            />
          ) : (
            <audio
              ref={bindMediaElement}
              className="asset-wav-preview"
              controls
              preload="metadata"
              aria-label={`Verified WAV preview for ${label}`}
            />
          )}
        </>
      ) : null}

      {retryVisible ? (
        <button
          type="button"
          className="secondary asset-preview-retry"
          disabled={busy}
          onClick={retry}
        >
          Retry preview
        </button>
      ) : null}
    </section>
  );
}

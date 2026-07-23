import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type {
  StudioAssetCatalogEntry,
  StudioAssetInspection,
} from "../shared/studio-api";
import {
  ASSET_PREVIEW_MAX_BYTES,
  ASSET_PREVIEW_MAX_CHUNKS,
  assetBinaryPreviewIdentity,
  assetBinaryPreviewIdentityKey,
  decodeAssetPreviewChunk,
  decodeAssetPreviewClose,
  decodeAssetPreviewOpen,
  previewHandleFromOpenReply,
  type AssetBinaryPreviewContext,
  type AssetBinaryPreviewIdentity,
  type AssetBinaryPreviewLifecycle,
} from "./asset-binary-preview-state";

const INVALID_PREVIEW_MESSAGE =
  "The verified preview stream did not match the selected asset.";
const PREVIEW_LIMIT_MESSAGE =
  "This verified asset exceeds the 64 MiB renderer preview limit.";
const PREVIEW_REQUEST_MESSAGE =
  "The local verified preview request could not be completed.";
const PREVIEW_CLOSE_MESSAGE =
  "The verified preview lease could not be closed safely.";
const PREVIEW_URL_MESSAGE =
  "This renderer could not publish a safe local preview URL.";

interface AssetBinaryPreviewView {
  identityKey: string;
  lifecycle: AssetBinaryPreviewLifecycle;
  loadedBytes: number;
  declaredBytes: number;
  error: string | null;
  retryVisible: boolean;
}

interface PreviewResources {
  token: number;
  identityKey: string;
  cancelled: boolean;
  handle: string | null;
  chunks: Uint8Array<ArrayBuffer>[];
  objectUrl: string | null;
  closePromise: Promise<boolean> | null;
  activityPromise: Promise<void> | null;
  seenViews: WeakSet<object>;
  seenBuffers: WeakSet<object>;
}

export interface UseAssetBinaryPreviewResult extends AssetBinaryPreviewView {
  eligible: boolean;
  retry: () => void;
  bindMediaElement: (element: HTMLImageElement | HTMLAudioElement | null) => void;
}

class PreviewFailure extends Error {}

export function useAssetBinaryPreview({
  context,
  entry,
  inspection,
}: {
  context: AssetBinaryPreviewContext | undefined;
  entry: StudioAssetCatalogEntry | null;
  inspection: StudioAssetInspection | null;
}): UseAssetBinaryPreviewResult {
  const identity = useMemo(
    () => assetBinaryPreviewIdentity(context, entry, inspection),
    [context, entry, inspection],
  );
  const identityKey = assetBinaryPreviewIdentityKey(identity);
  const [retryToken, setRetryToken] = useState(0);
  const [view, setView] = useState<AssetBinaryPreviewView>({
    identityKey: "idle",
    lifecycle: "idle",
    loadedBytes: 0,
    declaredBytes: 0,
    error: null,
    retryVisible: false,
  });
  const mountedRef = useRef(true);
  const nextTokenRef = useRef(0);
  const currentTokenRef = useRef<number | null>(null);
  const resourcesRef = useRef(new Map<number, PreviewResources>());
  const closeBarrierRef = useRef<Promise<boolean> | null>(null);
  const closeFailedRef = useRef(false);
  const mediaElementRef = useRef<HTMLImageElement | HTMLAudioElement | null>(null);

  const bindMediaElement = useCallback(
    (element: HTMLImageElement | HTMLAudioElement | null): void => {
      const previous = mediaElementRef.current;
      if (previous && previous !== element && previous.hasAttribute("src")) {
        resetMediaElement(previous);
      }
      mediaElementRef.current = element;
      if (!element || currentTokenRef.current === null) return;
      const resources = resourcesRef.current.get(currentTokenRef.current);
      if (
        resources?.objectUrl &&
        resources.identityKey === identityKey &&
        identityKey !== "idle"
      ) {
        element.setAttribute("src", resources.objectUrl);
      }
    },
    [identityKey],
  );

  useEffect(
    () => {
      mountedRef.current = true;
      return () => {
        mountedRef.current = false;
      };
    },
    [],
  );

  useEffect(() => {
    const token = nextTokenRef.current + 1;
    nextTokenRef.current = token;
    currentTokenRef.current = token;
    const resources: PreviewResources = {
      token,
      identityKey,
      cancelled: false,
      handle: null,
      chunks: [],
      objectUrl: null,
      closePromise: null,
      activityPromise: null,
      seenViews: new WeakSet(),
      seenBuffers: new WeakSet(),
    };
    resourcesRef.current.set(token, resources);
    const priorClose = closeBarrierRef.current;

    if (identity) {
      const activity = runPreview(resources, identity, priorClose);
      resources.activityPromise = activity;
      void activity;
    } else {
      void Promise.resolve().then(() => {
        updateCurrent(resources, {
          lifecycle: "idle",
          loadedBytes: 0,
          declaredBytes: 0,
          error: null,
        });
      });
    }

    return () => {
      resources.cancelled = true;
      if (currentTokenRef.current === token) currentTokenRef.current = null;
      queueDisposal(resources);
    };

    async function runPreview(
      owned: PreviewResources,
      currentIdentity: AssetBinaryPreviewIdentity,
      closeBeforeOpen: Promise<boolean> | null,
    ): Promise<void> {
      try {
        const priorClosed = (await closeBeforeOpen) ?? true;
        if (owned.cancelled) return;
        if (!priorClosed || closeFailedRef.current) {
          throw new PreviewFailure(PREVIEW_CLOSE_MESSAGE);
        }
        updateCurrent(owned, {
          lifecycle: "opening",
          loadedBytes: 0,
          declaredBytes: 0,
          error: null,
        });
        const rawOpen = await window.forgeStudio.openAssetPreview(
          currentIdentity.workspaceId,
          currentIdentity.manifestRevision,
          currentIdentity.entryId,
        );
        const open = decodeAssetPreviewOpen(rawOpen, currentIdentity);
        if (!open.ok) {
          const salvageHandle = open.handle ?? previewHandleFromOpenReply(rawOpen);
          if (salvageHandle) {
            owned.handle = salvageHandle;
            if (!(await closeLease(owned))) {
              throw new PreviewFailure(PREVIEW_CLOSE_MESSAGE);
            }
          }
          throw new PreviewFailure(INVALID_PREVIEW_MESSAGE);
        }
        owned.handle = open.value.handle;
        if (owned.cancelled) {
          await closeLease(owned);
          return;
        }
        if (
          open.value.byteLength > ASSET_PREVIEW_MAX_BYTES ||
          Math.ceil(open.value.byteLength / open.value.chunkBytes) >
            ASSET_PREVIEW_MAX_CHUNKS
        ) {
          if (!(await closeLease(owned))) {
            throw new PreviewFailure(PREVIEW_CLOSE_MESSAGE);
          }
          throw new PreviewFailure(PREVIEW_LIMIT_MESSAGE);
        }

        updateCurrent(owned, {
          lifecycle: "reading",
          loadedBytes: 0,
          declaredBytes: open.value.byteLength,
          error: null,
        });
        let sequence = 0;
        let cumulativeBytes = 0;
        while (cumulativeBytes < open.value.byteLength) {
          if (owned.cancelled) return;
          if (sequence >= ASSET_PREVIEW_MAX_CHUNKS || !owned.handle) {
            throw new PreviewFailure(INVALID_PREVIEW_MESSAGE);
          }
          const rawChunk = await window.forgeStudio.readAssetPreviewChunk(
            owned.handle,
            sequence,
          );
          if (owned.cancelled) return;
          const chunk = decodeAssetPreviewChunk(rawChunk, {
            handle: owned.handle,
            sequence,
            cumulativeBytes,
            declaredBytes: open.value.byteLength,
            declaredSha256: open.value.sha256,
            seenViews: owned.seenViews,
            seenBuffers: owned.seenBuffers,
          });
          if (!chunk.ok) throw new PreviewFailure(INVALID_PREVIEW_MESSAGE);
          owned.chunks.push(chunk.value.bytes);
          cumulativeBytes = chunk.value.cumulativeBytes;
          updateCurrent(owned, {
            lifecycle: "reading",
            loadedBytes: cumulativeBytes,
            declaredBytes: open.value.byteLength,
            error: null,
          });
          sequence += 1;
          if (chunk.value.eof) break;
        }
        if (
          cumulativeBytes !== open.value.byteLength ||
          sequence < 1 ||
          sequence > ASSET_PREVIEW_MAX_CHUNKS
        ) {
          throw new PreviewFailure(INVALID_PREVIEW_MESSAGE);
        }

        updateCurrent(owned, {
          lifecycle: "closing",
          loadedBytes: cumulativeBytes,
          declaredBytes: open.value.byteLength,
          error: null,
        });
        const closed = await closeLease(owned);
        if (owned.cancelled) return;
        if (!closed) throw new PreviewFailure(PREVIEW_CLOSE_MESSAGE);
        if (
          typeof URL.createObjectURL !== "function" ||
          typeof URL.revokeObjectURL !== "function"
        ) {
          throw new PreviewFailure(PREVIEW_URL_MESSAGE);
        }
        const blob = new Blob(owned.chunks, { type: currentIdentity.mediaType });
        owned.chunks = [];
        const objectUrl = URL.createObjectURL(blob);
        if (typeof objectUrl !== "string" || !objectUrl.startsWith("blob:")) {
          if (typeof objectUrl === "string") URL.revokeObjectURL(objectUrl);
          throw new PreviewFailure(PREVIEW_URL_MESSAGE);
        }
        if (owned.cancelled) {
          URL.revokeObjectURL(objectUrl);
          return;
        }
        owned.objectUrl = objectUrl;
        const mediaElement = mediaElementRef.current;
        const expectedTag = currentIdentity.kind === "png" ? "IMG" : "AUDIO";
        if (mediaElement?.tagName === expectedTag) {
          mediaElement.setAttribute("src", objectUrl);
        }
        updateCurrent(owned, {
          lifecycle: "ready",
          loadedBytes: cumulativeBytes,
          declaredBytes: open.value.byteLength,
          error: null,
        });
      } catch (error) {
        let message =
          error instanceof PreviewFailure ? error.message : PREVIEW_REQUEST_MESSAGE;
        if (!(await disposeResources(owned))) message = PREVIEW_CLOSE_MESSAGE;
        if (!owned.cancelled) {
          updateCurrent(owned, {
            lifecycle: "error",
            loadedBytes: 0,
            declaredBytes: 0,
            error: message,
            retryVisible: true,
          });
        }
      }
    }

    function updateCurrent(
      owned: PreviewResources,
      update: Partial<AssetBinaryPreviewView>,
    ): void {
      if (
        !mountedRef.current ||
        owned.cancelled ||
        currentTokenRef.current !== owned.token
      ) {
        return;
      }
      setView((current) => ({
        ...current,
        ...update,
        identityKey: owned.identityKey,
      }));
    }

    function queueDisposal(owned: PreviewResources): void {
      const previousBarrier = closeBarrierRef.current;
      const disposal = disposeAfterActivity(owned);
      const barrier = Promise.all([
        previousBarrier ?? Promise.resolve(true),
        disposal,
      ]).then((results) => results.every(Boolean));
      closeBarrierRef.current = barrier;
      void barrier.finally(() => {
        if (closeBarrierRef.current === barrier) closeBarrierRef.current = null;
      });
    }

    async function disposeAfterActivity(owned: PreviewResources): Promise<boolean> {
      const initiallyClosed = await disposeResources(owned);
      await owned.activityPromise;
      const finallyClosed = await disposeResources(owned);
      resourcesRef.current.delete(owned.token);
      return initiallyClosed && finallyClosed;
    }

    async function disposeResources(owned: PreviewResources): Promise<boolean> {
      resetAndRevoke(owned);
      owned.chunks = [];
      return closeLease(owned);
    }

    async function closeLease(owned: PreviewResources): Promise<boolean> {
      if (owned.closePromise) return owned.closePromise;
      const handle = owned.handle;
      if (!handle) return true;
      const close = Promise.resolve()
        .then(() => window.forgeStudio.closeAssetPreview(handle))
        .then((reply) => decodeAssetPreviewClose(reply, handle))
        .catch(() => false)
        .then((closed) => {
          if (!closed) closeFailedRef.current = true;
          return closed;
        })
        .finally(() => {
          if (owned.handle === handle) owned.handle = null;
        });
      owned.closePromise = close;
      return close;
    }

    function resetAndRevoke(owned: PreviewResources): void {
      const objectUrl = owned.objectUrl;
      if (!objectUrl) return;
      if (mediaElementRef.current) resetMediaElement(mediaElementRef.current);
      owned.objectUrl = null;
      if (typeof URL.revokeObjectURL === "function") {
        try {
          URL.revokeObjectURL(objectUrl);
        } catch {
          // The URL is no longer retained even if the platform reports disposal failure.
        }
      }
    }
  }, [identity, identityKey, retryToken]);

  const retry = useCallback((): void => {
    setRetryToken((current) => current + 1);
  }, []);
  const presentedView: AssetBinaryPreviewView =
    view.identityKey === identityKey
      ? view
      : {
          identityKey,
          lifecycle: identity ? "opening" : "idle",
          loadedBytes: 0,
          declaredBytes: 0,
          error: null,
          retryVisible: false,
        };
  return {
    ...presentedView,
    eligible: identity !== null,
    retry,
    bindMediaElement,
  };
}

function resetMediaElement(element: HTMLImageElement | HTMLAudioElement): void {
  if (element instanceof HTMLAudioElement) {
    try {
      element.pause();
    } catch {
      // Best-effort pause precedes source removal and URL revocation.
    }
  }
  element.removeAttribute("src");
  if (element instanceof HTMLAudioElement) {
    try {
      element.load();
    } catch {
      // Source removal remains authoritative when media loading is unavailable.
    }
  }
}

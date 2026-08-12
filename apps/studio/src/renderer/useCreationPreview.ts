import { useEffect, useMemo, useSyncExternalStore } from "react";

import type { ForgeStudioApi } from "../shared/studio-api";
import {
  applyCreationPreviewChunk,
  creationPreviewCandidateKey,
  decodeCreationPreviewClose,
  decodeCreationPreviewOpen,
  initialCreationPreviewStream,
  previewHandleFromOpenReply,
  type CreationPreviewCandidate,
  type CreationPreviewDescriptor,
} from "./creation-preview-state";

const INVALID_PREVIEW = "The verified creation preview stream did not match the selected sealed asset.";
const REQUEST_FAILED = "The verified creation preview request could not be completed.";
const CLOSE_FAILED = "The verified creation preview lease could not be closed safely.";
const HASH_FAILED = "The renderer SHA-256 did not match the verified creation preview.";
const HANDLE_REUSED = "The creation preview service reused an opaque lease handle.";
const BLOB_FAILED = "The renderer could not publish a safe local preview URL.";

export type CreationPreviewLifecycle =
  | "idle"
  | "opening"
  | "reading"
  | "closing"
  | "ready"
  | "error";

export interface UseCreationPreviewResult {
  lifecycle: CreationPreviewLifecycle;
  busy: boolean;
  loadedBytes: number;
  declaredBytes: number;
  descriptor: CreationPreviewDescriptor | null;
  objectUrl: string | null;
  error: string | null;
  canOpen: boolean;
  canClose: boolean;
  canRetry: boolean;
  open: () => void;
  retry: () => void;
  close: () => void;
}

interface PreviewView {
  identityKey: string;
  lifecycle: CreationPreviewLifecycle;
  loadedBytes: number;
  declaredBytes: number;
  descriptor: CreationPreviewDescriptor | null;
  objectUrl: string | null;
  error: string | null;
}

interface PreviewResources {
  token: number;
  identityKey: string;
  cancelled: boolean;
  handle: string | null;
  closePromise: Promise<boolean> | null;
  chunks: Uint8Array<ArrayBuffer>[];
  objectUrl: string | null;
  activity: Promise<void> | null;
  disposal: Promise<boolean> | null;
}

class PreviewFailure extends Error {}

export function useCreationPreview({
  api,
  candidate,
}: {
  api: ForgeStudioApi;
  candidate: CreationPreviewCandidate | null;
}): UseCreationPreviewResult {
  const controller = useMemo(() => new CreationPreviewController(api), [api]);
  const identityKey = creationPreviewCandidateKey(candidate);
  const view = useSyncExternalStore(
    controller.subscribe,
    controller.getSnapshot,
    controller.getSnapshot,
  );

  useEffect(() => {
    controller.bind(candidate);
    return () => controller.bind(null);
  }, [candidate, controller, identityKey]);

  useEffect(() => () => controller.dispose(), [controller]);

  const isBound = view.identityKey === identityKey;
  const visibleView = isBound ? view : idleView(identityKey);
  const busy =
    visibleView.lifecycle === "opening" ||
    visibleView.lifecycle === "reading" ||
    visibleView.lifecycle === "closing";
  return {
    lifecycle: visibleView.lifecycle,
    busy,
    loadedBytes: visibleView.loadedBytes,
    declaredBytes: visibleView.declaredBytes,
    descriptor: visibleView.descriptor,
    objectUrl: visibleView.objectUrl,
    error: visibleView.error,
    canOpen: isBound && controller.canOpen(),
    canClose: isBound && (busy || visibleView.lifecycle === "ready"),
    canRetry: isBound && controller.canRetry(),
    open: controller.open,
    retry: controller.retry,
    close: controller.close,
  };
}

export class CreationPreviewController {
  readonly #api: ForgeStudioApi;
  readonly #listeners = new Set<() => void>();
  readonly #seenHandles = new Set<string>();
  #candidate: CreationPreviewCandidate | null = null;
  #view = idleView("idle");
  #nextToken = 0;
  #current: PreviewResources | null = null;
  #closeBarrier: Promise<boolean> | null = null;
  #closeFailed = false;
  #disposed = false;

  constructor(api: ForgeStudioApi) {
    this.#api = api;
  }

  readonly subscribe = (listener: () => void): (() => void) => {
    this.#listeners.add(listener);
    return () => this.#listeners.delete(listener);
  };

  readonly getSnapshot = (): PreviewView => this.#view;

  bind(candidate: CreationPreviewCandidate | null): void {
    const nextKey = creationPreviewCandidateKey(candidate);
    if (nextKey === creationPreviewCandidateKey(this.#candidate)) return;
    this.#candidate = candidate;
    const previous = this.#current;
    this.#current = null;
    this.#emit(idleView(nextKey));
    if (!previous) return;
    previous.cancelled = true;
    void this.#queueDisposal(previous).then((closed) => {
      if (!closed && creationPreviewCandidateKey(this.#candidate) === nextKey) {
        this.#emit(errorView(nextKey, CLOSE_FAILED));
      }
    });
  }

  canOpen(): boolean {
    return (
      this.#candidate?.eligible === true &&
      this.#view.lifecycle === "idle" &&
      this.#current === null &&
      !this.#closeFailed &&
      !this.#disposed
    );
  }

  canRetry(): boolean {
    return (
      this.#candidate?.eligible === true &&
      this.#view.lifecycle === "error" &&
      !this.#closeFailed &&
      !this.#disposed
    );
  }

  readonly open = (): void => {
    const selected = this.#candidate;
    if (!this.canOpen() || !selected) return;
    const resources: PreviewResources = {
      token: this.#nextToken + 1,
      identityKey: creationPreviewCandidateKey(selected),
      cancelled: false,
      handle: null,
      closePromise: null,
      chunks: [],
      objectUrl: null,
      activity: null,
      disposal: null,
    };
    this.#nextToken = resources.token;
    this.#current = resources;
    this.#updateCurrent(resources, {
      lifecycle: "opening",
      loadedBytes: 0,
      declaredBytes: 0,
      descriptor: null,
      objectUrl: null,
      error: null,
    });
    const barrier = this.#closeBarrier;
    resources.activity = this.#runPreview(resources, selected, barrier);
    void resources.activity;
  };

  readonly close = (): void => {
    const resources = this.#current;
    if (!resources) {
      this.#emit(idleView(creationPreviewCandidateKey(this.#candidate)));
      return;
    }
    this.#current = null;
    resources.cancelled = true;
    this.#emit({ ...this.#view, lifecycle: "closing", objectUrl: null, error: null });
    void this.#queueDisposal(resources).then((closed) => {
      const key = creationPreviewCandidateKey(this.#candidate);
      this.#emit(closed ? idleView(key) : errorView(key, CLOSE_FAILED));
    });
  };

  readonly retry = (): void => {
    if (!this.canRetry()) return;
    const resources = this.#current;
    if (!resources) {
      if (this.#view.lifecycle === "error") {
        this.#emit(idleView(creationPreviewCandidateKey(this.#candidate)));
      }
      this.open();
      return;
    }
    this.#current = null;
    resources.cancelled = true;
    void this.#queueDisposal(resources).then((closed) => {
      if (closed) this.open();
      else this.#emit(errorView(creationPreviewCandidateKey(this.#candidate), CLOSE_FAILED));
    });
  };

  dispose(): void {
    if (this.#disposed) return;
    this.#disposed = true;
    this.#candidate = null;
    const resources = this.#current;
    this.#current = null;
    this.#listeners.clear();
    if (resources) {
      resources.cancelled = true;
      void this.#queueDisposal(resources);
    }
  }

  async #runPreview(
    resources: PreviewResources,
    selected: CreationPreviewCandidate,
    barrier: Promise<boolean> | null,
  ): Promise<void> {
    try {
      if (((await barrier) ?? true) !== true || this.#closeFailed) {
        throw new PreviewFailure(CLOSE_FAILED);
      }
      if (resources.cancelled) return;
      const rawOpen = await this.#api.openCreationPreview({
        workspaceId: selected.workspaceId,
        expectedRootGeneration: selected.rootGeneration,
        expectedSourceRevision: selected.sourceRevision,
        expectedWorkflowStatusHash: selected.workflowStatusHash,
        expectedArtifactSnapshotHash: selected.artifactSnapshotHash,
        assetpackArtifactId: selected.assetpackArtifactId,
        outputGrantId: selected.outputGrantId,
        expectedOutputGrantGeneration: selected.outputGrantGeneration,
        assetId: selected.assetId,
      });
      const opened = decodeCreationPreviewOpen(rawOpen, selected);
      if (!opened.ok) {
        const salvage = opened.handle ?? previewHandleFromOpenReply(rawOpen);
        if (salvage) {
          resources.handle = salvage;
          if (!(await this.#closeLease(resources))) throw new PreviewFailure(CLOSE_FAILED);
        }
        throw new PreviewFailure(INVALID_PREVIEW);
      }
      resources.handle = opened.value.handle;
      if (this.#seenHandles.has(opened.value.handle)) {
        if (!(await this.#closeLease(resources))) throw new PreviewFailure(CLOSE_FAILED);
        throw new PreviewFailure(HANDLE_REUSED);
      }
      this.#seenHandles.add(opened.value.handle);
      if (resources.cancelled) return;
      this.#updateCurrent(resources, {
        lifecycle: "reading",
        loadedBytes: 0,
        declaredBytes: opened.value.byteLength,
        descriptor: opened.value,
        error: null,
      });
      let stream = initialCreationPreviewStream(opened.value.byteLength);
      while (!stream.eof) {
        if (resources.cancelled) return;
        const requestedSequence = stream.nextSequence;
        const rawChunk = await this.#api.readCreationPreviewChunk(
          opened.value.handle,
          requestedSequence,
        );
        if (resources.cancelled) return;
        const transition = applyCreationPreviewChunk(
          rawChunk,
          opened.value.handle,
          stream,
          opened.value.sha256,
        );
        if (transition.kind !== "next" || transition.chunk.sequence !== requestedSequence) {
          throw new PreviewFailure(INVALID_PREVIEW);
        }
        resources.chunks.push(transition.chunk.bytes);
        stream = transition.stream;
        this.#updateCurrent(resources, {
          lifecycle: "reading",
          loadedBytes: stream.cumulativeBytes,
          declaredBytes: stream.declaredBytes,
          descriptor: opened.value,
          error: null,
        });
      }
      const bytes = concatenateChunks(resources.chunks, opened.value.byteLength);
      const digest = await sha256(bytes);
      if (digest !== opened.value.sha256) throw new PreviewFailure(HASH_FAILED);
      if (resources.cancelled) return;
      this.#updateCurrent(resources, {
        lifecycle: "closing",
        loadedBytes: opened.value.byteLength,
        declaredBytes: opened.value.byteLength,
        descriptor: opened.value,
        error: null,
      });
      if (!(await this.#closeLease(resources))) throw new PreviewFailure(CLOSE_FAILED);
      if (resources.cancelled) return;
      if (
        typeof URL.createObjectURL !== "function" ||
        typeof URL.revokeObjectURL !== "function"
      ) throw new PreviewFailure(BLOB_FAILED);
      const blob = new Blob([bytes], { type: opened.value.mediaType });
      const objectUrl = URL.createObjectURL(blob);
      if (typeof objectUrl !== "string" || !objectUrl.startsWith("blob:")) {
        if (typeof objectUrl === "string") URL.revokeObjectURL(objectUrl);
        throw new PreviewFailure(BLOB_FAILED);
      }
      if (resources.cancelled || this.#current !== resources) {
        URL.revokeObjectURL(objectUrl);
        return;
      }
      resources.chunks = [];
      resources.objectUrl = objectUrl;
      this.#updateCurrent(resources, {
        lifecycle: "ready",
        loadedBytes: opened.value.byteLength,
        declaredBytes: opened.value.byteLength,
        descriptor: opened.value,
        objectUrl,
        error: null,
      });
    } catch (caught) {
      let message = caught instanceof PreviewFailure ? caught.message : REQUEST_FAILED;
      const closed = await this.#disposeResources(resources);
      if (!closed) message = CLOSE_FAILED;
      if (!resources.cancelled && this.#current === resources) {
        this.#current = null;
        this.#emit(errorView(resources.identityKey, message));
      }
    } finally {
      if (resources.cancelled) await this.#closeLease(resources);
    }
  }

  #updateCurrent(resources: PreviewResources, update: Partial<PreviewView>): void {
    if (resources.cancelled || this.#current !== resources) return;
    this.#emit({ ...this.#view, ...update, identityKey: resources.identityKey });
  }

  #queueDisposal(resources: PreviewResources): Promise<boolean> {
    if (resources.disposal) return resources.disposal;
    resources.cancelled = true;
    this.#revokeObjectUrl(resources);
    resources.chunks = [];
    const previous = this.#closeBarrier;
    const disposal = Promise.all([
      previous ?? Promise.resolve(true),
      (async () => {
        await resources.activity;
        return await this.#disposeResources(resources);
      })(),
    ]).then((values) => values.every(Boolean));
    resources.disposal = disposal;
    this.#closeBarrier = disposal;
    void disposal.finally(() => {
      if (this.#closeBarrier === disposal) this.#closeBarrier = null;
    });
    return disposal;
  }

  async #disposeResources(resources: PreviewResources): Promise<boolean> {
    this.#revokeObjectUrl(resources);
    resources.chunks = [];
    return await this.#closeLease(resources);
  }

  async #closeLease(resources: PreviewResources): Promise<boolean> {
    if (resources.closePromise) return await resources.closePromise;
    const handle = resources.handle;
    if (!handle) return true;
    const closePromise = (async () => {
      try {
        const raw = await this.#api.closeCreationPreview(handle);
        const closed = decodeCreationPreviewClose(raw, handle);
        if (closed) resources.handle = null;
        else this.#closeFailed = true;
        return closed;
      } catch {
        this.#closeFailed = true;
        return false;
      }
    })();
    resources.closePromise = closePromise;
    return await closePromise;
  }

  #revokeObjectUrl(resources: PreviewResources): void {
    const url = resources.objectUrl;
    if (!url) return;
    resources.objectUrl = null;
    URL.revokeObjectURL(url);
  }

  #emit(view: PreviewView): void {
    if (this.#disposed) return;
    this.#view = view;
    this.#listeners.forEach((listener) => listener());
  }
}

function idleView(identityKey: string): PreviewView {
  return {
    identityKey,
    lifecycle: "idle",
    loadedBytes: 0,
    declaredBytes: 0,
    descriptor: null,
    objectUrl: null,
    error: null,
  };
}

function errorView(identityKey: string, error: string): PreviewView {
  return { ...idleView(identityKey), lifecycle: "error", error };
}

function concatenateChunks(
  chunks: readonly Uint8Array<ArrayBuffer>[],
  expectedLength: number,
): Uint8Array<ArrayBuffer> {
  const bytes = new Uint8Array(expectedLength);
  let offset = 0;
  for (const chunk of chunks) {
    if (offset + chunk.byteLength > expectedLength) {
      throw new PreviewFailure(INVALID_PREVIEW);
    }
    bytes.set(chunk, offset);
    offset += chunk.byteLength;
  }
  if (offset !== expectedLength) throw new PreviewFailure(INVALID_PREVIEW);
  return bytes;
}

async function sha256(bytes: Uint8Array<ArrayBuffer>): Promise<string> {
  if (!globalThis.crypto?.subtle) throw new PreviewFailure(HASH_FAILED);
  const digest = await globalThis.crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(digest)]
    .map((value) => value.toString(16).padStart(2, "0"))
    .join("");
}
